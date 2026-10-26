"""
Copyright (c) 2021-, James Vecellio, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import json
import platform
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from math import degrees
from numpy import interp
from time import monotonic

import openpilot.cereal.messaging as messaging
from opendbc.car.structs import car
from openpilot.cereal import custom
from openpilot.common.constants import CV
from openpilot.common.params import Params
from openpilot.common.realtime import Ratekeeper
from openpilot.common.swaglog import cloudlog

from openpilot.sunnypilot.navd.constants import NAV_LANE_CHANGE_OFF, NAV_RETRY
from openpilot.sunnypilot.navd.helpers import Coordinate, lane_change_auto_confirm, lane_change_hint, parse_banner_instructions
from openpilot.sunnypilot.navd.nav_audio import NavAudioCues
from openpilot.sunnypilot.navd.navigation_helpers.destination_store import DestinationStore
from openpilot.sunnypilot.navd.navigation_helpers.mapbox_integration import MapboxIntegration
from openpilot.sunnypilot.navd.navigation_helpers.route import Route, RouteProgress, arrived, upcoming_turn
from openpilot.sunnypilot.navd.navigation_helpers.turn_targets import TurnTarget, car_scale, turn_targets

PARAM_POLL_FRAMES = 15  # 5 s at 3 Hz
REROUTE_TICKS = 10  # lost this many 3 Hz cycles before a recompute is requested
ARRIVAL_TICKS = 30  # arrived this many cycles before the trip concludes
TURN_CLEAR_DIST = 10.0  # m past a maneuver point before its speed target is dropped; the apex is the point itself

# hints flap left/right within a second, and a flap that reaches the assist gate flips
# which blinker can start a lane change
HINT_STABLE_CYCLES = 3

# a single blip against the distance or bearing thresholds must not dim the display
OFF_ROUTE_DEBOUNCE_TICKS = 3

OFF_ROUTE_SPEED_BP = [0.0, 5.0, 10.0, 20.0, 40.0]  # m/s
OFF_ROUTE_DIST = [100.0, 125.0, 150.0, 200.0, 250.0]  # m


@dataclass
class Guidance:
  """One tick's guidance output, everything the message carries beyond the route itself."""
  upcoming_turn: str = 'none'
  current_speed_limit: int = 0  # kph
  arrived: bool = False
  distance_from_route: float = 0.0
  distance_remaining: float = 0.0
  time_remaining: float = 0.0
  lanes: list = field(default_factory=list)
  lane_change_direction: str = 'none'
  lane_change_auto_confirm: bool = False


class RouteRetry:
  """Exponential backoff for route requests that failed; a new destination starts fresh."""

  def __init__(self):
    self.attempted_destination: str | None = None
    self.failed_attempts: int = 0
    self.next_attempt_time: float = 0.0

  def reset(self) -> None:
    self.failed_attempts = 0
    self.next_attempt_time = 0.0

  def schedule(self, destination: str) -> None:
    delay = min(NAV_RETRY.BASE_SECONDS * (2 ** self.failed_attempts), NAV_RETRY.MAX_SECONDS)
    self.failed_attempts += 1
    self.next_attempt_time = monotonic() + delay
    cloudlog.warning("navd: no route for destination %r, retrying in %.0fs (attempt %d)", destination, delay, self.failed_attempts)

  def may_request(self) -> bool:
    return monotonic() >= self.next_attempt_time


class HintDebounce:
  """A lane-change hint publishes only after holding for HINT_STABLE_CYCLES; 'none' is immediate."""

  def __init__(self):
    self.reset()

  def reset(self) -> None:
    self.candidate = 'none'
    self.stable_count = 0
    self.published = 'none'

  def update(self, hint: str) -> str:
    if hint == self.candidate:
      self.stable_count += 1
    else:
      self.candidate = hint
      self.stable_count = 1

    if hint == 'none' or self.stable_count >= HINT_STABLE_CYCLES:
      self.published = hint
    elif self.published != hint:
      # between directions the safe output is no hint, not the stale one
      self.published = 'none'
    return self.published


class Navigationd:
  def __init__(self):
    self.params = Params()
    self.mem_params = Params("/dev/shm/params") if platform.system() != "Darwin" else self.params
    self.mapbox = MapboxIntegration()
    self.destination_store = DestinationStore(self.params)
    self.nav_audio = NavAudioCues()
    self.retry = RouteRetry()
    self.hint = HintDebounce()

    self.sm = messaging.SubMaster(['carState', 'liveLocationKalman'])
    self.pm = messaging.PubMaster(['navigationd'])
    self.rk = Ratekeeper(3)

    self.route: Route | None = None
    self.destination: str | None = None  # the destination the loaded route serves
    self.new_destination: str = ''  # the MapboxRoute param as last polled

    self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='navd_route')
    self.route_request: Future | None = None

    self.allow_navigation: bool = False
    self.lane_guidance = False  # lanes on the turn card
    self.lane_assist = False  # lane change hints for the controls side
    self.recompute_allowed: bool = False
    self.reroute_counter: int = 0
    self.arrival_counter: int = 0
    self.empty_destination_reads: int = 0
    self.observed_destination: str | None = None
    self.final_step: bool = False
    self.rerouting: bool = False
    self.off_route: bool = False

    self.frame: int = -1
    self.last_position: Coordinate | None = None
    self.last_bearing: float | None = None
    self.valid: bool = False

    self.turn_speed_scale: float | None = None  # unknown until CarParams lands
    self.turn_targets: list[TurnTarget] = []
    self.turn_targets_key: tuple | None = None
    self.turn_targets_written: str = ''

  # MapboxSettings outlives the in-memory route, so a route left there can be reloaded later
  def _drop_route(self) -> None:
    self.params.remove("MapboxSettings")
    # a chosen alternate must not leak into a later trip to the same place
    self.params.remove("MapboxRoutePreference")
    self.route = None
    self.destination = None
    # MapboxRoute is only re-read every 15 frames; a stale value would re-request the concluded trip
    self.new_destination = ''
    self.arrival_counter = 0
    self.reroute_counter = 0
    self.off_route = False
    self.empty_destination_reads = 0
    self.final_step = False
    self.hint.reset()

  def _poll_params(self) -> None:
    self.allow_navigation = self.params.get('AllowNavigation', return_default=True)
    self.lane_guidance = self.params.get('NavLaneGuidance', return_default=True)
    self.lane_assist = self.params.get('NavLaneChangeTimer', return_default=True) != NAV_LANE_CHANGE_OFF
    self.new_destination = self.params.get('MapboxRoute')
    self.recompute_allowed = self.params.get('MapboxRecompute', return_default=True)
    if self.turn_speed_scale is None and (car_params := self.params.get("CarParams")):
      self.turn_speed_scale = car_scale(messaging.log_from_bytes(car_params, car.CarParams).brand)

    # a single empty read can be a glitch; log every change so a clear is attributable
    if self.new_destination != self.observed_destination:
      cloudlog.warning("navd: destination param changed %r -> %r", self.observed_destination, self.new_destination)
      self.observed_destination = self.new_destination

    # Params returns None for unset and empty alike; the route only drops once the clear
    # persists across polls, so a read glitch costs one poll of latency
    if not self.new_destination and self.route is not None:
      self.empty_destination_reads += 1
      if self.empty_destination_reads >= 2:
        cloudlog.warning("navd: destination stayed empty across polls, dropping the route")
        self._drop_route()
    else:
      self.empty_destination_reads = 0

  def _accept_route(self, destination: dict, route: Route) -> None:
    # recents are recorded at acceptance so athena, CLI and settings destinations all
    # land; reroutes re-accept the destination they hold and are skipped
    if self.destination != self.new_destination:
      name = destination.get('resolved_name') or destination.get('name') or self.new_destination
      # a friendly label already recorded by the page or athena wins over this backfill
      self.destination_store.record_recent(name, self.new_destination, keep_existing_name=True)
    self.destination = self.new_destination
    self.route = route
    self.arrival_counter = 0
    self.reroute_counter = 0
    self.retry.reset()

  def _update_params(self):
    if self.last_position is None:
      return
    self.frame += 1
    if self.frame % PARAM_POLL_FRAMES == 0:
      self._poll_params()

    # a different destination must not inherit the previous one's backoff
    if self.new_destination != self.retry.attempted_destination:
      self.retry.reset()

    # a destination is accepted only once a route came back, so a failure stays pending
    pending = bool(self.new_destination) and self.new_destination != self.destination
    self.rerouting = bool(self.recompute_allowed and not self.final_step and self.reroute_counter >= REROUTE_TICKS and self.route)

    # requests run off the loop: geocoding + directions + timezone can block 15s on a dead LTE link
    if (pending or self.rerouting) and self.retry.may_request() and self.route_request is None:
      self.retry.attempted_destination = self.new_destination
      self.route_request = self.executor.submit(self.mapbox.set_destination, {'place_name': self.new_destination},
                                                self.last_position, self.last_bearing)

    if self.route_request is not None and self.route_request.done():
      request, self.route_request = self.route_request, None
      try:
        destination, route_dict = request.result()
      except Exception:
        cloudlog.exception("navd: route request raised")
        destination, route_dict = {}, None

      # a result for a destination since changed or cleared must not land
      if self.retry.attempted_destination == self.new_destination:
        route = Route.from_mapbox(route_dict)
        if route is not None:
          self._accept_route(destination, route)
        else:
          # an existing route is left alone: only the request for a new one failed
          self.retry.schedule(self.new_destination)

    # clearing the param is what lets the same address start a fresh route later
    if self.arrival_counter >= ARRIVAL_TICKS:
      self.params.put("MapboxRoute", "")
      self._drop_route()

    self.valid = self.route is not None

  def _update_navigation(self) -> tuple[str, RouteProgress | None, Guidance]:
    if not (self.allow_navigation and self.route and self.last_position is not None):
      self.off_route = False
      self.hint.reset()
      return '', None, Guidance()

    progress = self.route.progress(self.last_position)
    v_ego = float(max(self.sm['carState'].vEgo, 0.0))
    speed_limit, speed_unit = progress.current_step.maxspeed
    guidance = Guidance(
      upcoming_turn=upcoming_turn(progress, self.last_position, v_ego),
      # Mapbox annotates maxspeed in the road's posted unit; the message field is kph
      current_speed_limit=round(speed_limit * CV.MPH_TO_KPH) if speed_unit == 'mph' else int(speed_limit),
      arrived=arrived(progress, v_ego),
      distance_from_route=progress.distance_from_route,
      distance_remaining=progress.distance_remaining,
      time_remaining=progress.time_remaining,
    )

    banner = ''
    parsed = parse_banner_instructions(progress.current_step.banner_instructions, progress.distance_to_end_of_step)
    if parsed:
      banner = parsed['maneuverPrimaryText']
      # earlier banners describe the maneuver from too far out for lane advice to apply
      if self.lane_guidance and parsed.get('showFull'):
        guidance.lanes = parsed.get('lanes') or []

    large_distance = progress.distance_from_route > float(interp(v_ego, OFF_ROUTE_SPEED_BP, OFF_ROUTE_DIST))
    misaligned = self.route.bearing_misaligned(progress, self.last_bearing, v_ego)

    if self.lane_assist:
      # off the route or waiting on a failed reroute, the stale route must not prompt lane changes
      route_trusted = not large_distance and not misaligned and self.retry.failed_attempts == 0
      guidance.lane_change_direction = self.hint.update(lane_change_hint(progress, v_ego) if route_trusted else 'none')
      guidance.lane_change_auto_confirm = guidance.lane_change_direction != 'none' and lane_change_auto_confirm(progress)

    self._update_route_standing(large_distance, misaligned, guidance.arrived)

    # recomputing inside the final step loops at the destination; a dedicated latch, since
    # the param-backed flags flap back on at the next 5s re-read
    self.final_step = progress.current_step_idx == len(self.route.steps) - 1
    return banner, progress, guidance

  def _turn_targets_ahead(self, progress: RouteProgress) -> list[TurnTarget]:
    assert self.route is not None
    key = (self.route.route_id, self.turn_speed_scale)
    if key != self.turn_targets_key:
      self.turn_targets = turn_targets(self.route, self.turn_speed_scale or 1.0)
      self.turn_targets_key = key
    along = progress.current_step.cumulative_distance + progress.current_step.distance - progress.distance_to_end_of_step
    return [target for target in self.turn_targets if target.along > along - TURN_CLEAR_DIST]

  # the longitudinal planner's route-turn controller takes the nearest listed point as the car's
  # position, so a passed turn must leave the list; off the route the targets are the wrong road's
  def _publish_turn_targets(self, progress: RouteProgress | None) -> None:
    ahead = [] if progress is None or self.off_route or self.rerouting else self._turn_targets_ahead(progress)
    encoded = json.dumps([target.as_dict() for target in ahead]) if ahead else ''
    if encoded == self.turn_targets_written and self.frame % PARAM_POLL_FRAMES != 0:
      return
    if encoded:
      self.mem_params.put("NavTargetVelocities", encoded, block=True)
    else:
      self.mem_params.remove("NavTargetVelocities")
    self.turn_targets_written = encoded

  # being lost never cancels the route: it only asks for a recompute, and with no network the
  # route is held so guidance returns on its own. The counter doubles as routeState's
  # off-route debounce, so it counts with recompute off too; the arrival hold stays onRoute
  # because the last meters to the flag routinely leave the mapped line
  def _update_route_standing(self, large_distance: bool, misaligned: bool, arrived: bool) -> None:
    if arrived:
      self.arrival_counter += 1
      self.recompute_allowed = False
    elif large_distance or misaligned:
      self.reroute_counter += 1
    else:
      self.arrival_counter = 0
      self.reroute_counter = 0

    self.off_route = self.reroute_counter >= OFF_ROUTE_DEBOUNCE_TICKS and not arrived

  def _build_navigation_message(self, banner: str, progress: RouteProgress | None, guidance: Guidance, valid: bool):
    msg = messaging.new_message('navigationd')
    msg.valid = valid
    nav = msg.navigationd
    nav.upcomingTurn = guidance.upcoming_turn
    nav.currentSpeedLimit = guidance.current_speed_limit
    nav.bannerInstructions = banner
    nav.distanceFromRoute = guidance.distance_from_route
    nav.distanceRemaining = guidance.distance_remaining
    nav.timeRemaining = guidance.time_remaining
    nav.laneChangeDirection = guidance.lane_change_direction
    nav.laneChangeAutoConfirm = guidance.lane_change_auto_confirm
    nav.valid = self.valid
    nav.routeFailures = min(self.retry.failed_attempts, 0xffff)
    # rerouting outranks offRoute; self.rerouting already excludes the final step
    if self.rerouting:
      nav.routeState = 'rerouting'
    elif self.off_route:
      nav.routeState = 'offRoute'
    else:
      nav.routeState = 'onRoute'
    nav.hasPosition = self.last_position is not None
    if self.last_position is not None:
      nav.positionLatitude = self.last_position.latitude
      nav.positionLongitude = self.last_position.longitude
      nav.positionBearingDeg = (self.last_bearing + 360) % 360 if self.last_bearing is not None else 0.0
    nav.routeId = self.route.route_id if self.route else 0
    nav.audioCueKind = self.nav_audio.kind
    nav.audioCueStage = self.nav_audio.stage
    nav.audioCueId = self.nav_audio.cue_id
    nav.audioCueDirection = self.nav_audio.direction
    nav.audioCueCount = self.nav_audio.count
    nav.allManeuvers = [
      custom.Navigationd.Maneuver.new_message(distance=m.distance, type=m.type, modifier=m.modifier, instruction=m.instruction)
      for m in (progress.all_maneuvers if progress else [])
    ]
    nav.lanes = [
      custom.Navigationd.LaneGuidance.new_message(directions=lane['directions'], active=lane['active'],
                                                  activeDirection=lane.get('activeDirection', ''))
      for lane in guidance.lanes
    ]
    return msg

  def run(self):
    cloudlog.warning('navigationd init')

    while True:
      self.sm.update(0)
      location = self.sm['liveLocationKalman']
      localizer_valid = location.positionGeodetic.valid if location else False

      if localizer_valid:
        self.last_bearing = degrees(location.calibratedOrientationNED.value[2])
        self.last_position = Coordinate(location.positionGeodetic.value[0], location.positionGeodetic.value[1])

      self._update_params()
      banner, progress, guidance = self._update_navigation()
      self._publish_turn_targets(progress)
      self.nav_audio.update(self.route, progress, guidance, float(max(self.sm['carState'].vEgo, 0.0)), self.rerouting)

      msg = self._build_navigation_message(banner, progress, guidance, valid=localizer_valid)

      self.pm.send('navigationd', msg)
      self.rk.keep_time()


def main():
  Navigationd().run()


if __name__ == "__main__":
  main()
