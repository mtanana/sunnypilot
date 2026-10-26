"""
Copyright (c) 2021-, James Vecellio, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from openpilot.cereal import custom, messaging
from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL

from openpilot.sunnypilot.navd.constants import BannerMode, NAV_BANNER, NAV_CV


class EventBuilder:
  def __init__(self):
    self._counter: int = -1
    self._mode: int = BannerMode.OFF
    self._params = Params()
    self._banner_key: tuple | None = None
    self._banner_distance: float = 0.0
    self._banner_crossed: int = 0
    self._banner_frames: int = 0

  @staticmethod
  def _build_banner_message(metric: bool, nav_msg):
    m = nav_msg.allManeuvers[1] if len(nav_msg.allManeuvers) > 1 else nav_msg.allManeuvers[0]
    banner = m.instruction

    if metric:
      dist = f'{m.distance / NAV_CV.METERS_PER_KM:.1f} km,'
      if m.distance < NAV_CV.SHORT_DISTANCE_METERS:
        dist = f'{int(m.distance)}m,'
    else:
      dist = f'{m.distance / NAV_CV.METERS_PER_MILE:.1f} mi,'
      if m.distance < NAV_CV.QUARTER_MILE:
        dist = f'{round((m.distance * NAV_CV.FEET_PER_METER) / 50) * 50}ft,'

    if m.type == 'depart':
      base_msg = banner
    elif m.type == 'arrive' or 'Your destination' in banner:
      # far from the destination the raw text reads as "you have arrived", so keep the distance on it
      base_msg = banner if m.distance < NAV_CV.SHORT_DISTANCE_METERS else f'In {dist} {banner}'
    elif banner.startswith(('Continue', 'Drive', 'Head')):
      base_msg = f'For {dist} {banner}'
    elif 'Turn' in banner or 'Take' in banner or 'Make' in banner or banner.startswith(('Bear', 'Keep', 'Merge', 'Exit')):
      base_msg = f'In {dist} {banner}'
    else:
      base_msg = f'For {dist} Continue on {banner}'

    return base_msg

  @staticmethod
  def _get_turning_message(upcoming_turn):
    turn_messages = {
      'left': 'Turning Left, Make sure to nudge the wheel',
      'right': 'Turning Right, Make sure to nudge the wheel',
      'slightLeft': 'Keeping Left',
      'slightRight': 'Keeping Right',
      'sharpLeft': 'Sharp Left Turn',
      'sharpRight': 'Sharp Right Turn',
      'straight': 'Continuing Straight',
      'uturn': 'U-Turn Ahead',
      'roundabout': 'Roundabout Ahead',
    }
    return turn_messages.get(upcoming_turn, f"Upcoming {upcoming_turn.replace('_', ' ').title()}")

  @staticmethod
  def build_navigation_events(sm: messaging.SubMaster, metric=True) -> list:
    nav_msg = sm['navigationd']
    # a route can be valid before any maneuvers have been computed, so both are required to build a banner
    if not nav_msg.valid or not len(nav_msg.allManeuvers):
      return []

    banner_message = EventBuilder._build_banner_message(metric, nav_msg)

    if nav_msg.upcomingTurn != 'none':
      banner_message = EventBuilder._get_turning_message(nav_msg.upcomingTurn)

    return [{
      'name': custom.OnroadEventSP.EventName.navigationBanner,
      'message': banner_message,
    }]

  # each maneuver walks down THRESHOLDS_M, firing once per threshold crossed however many it
  # passes in one step, so the screen stays free between prompts
  def _banner_due(self, nav_msg) -> bool:
    if not len(nav_msg.allManeuvers):
      return False

    m = nav_msg.allManeuvers[1] if len(nav_msg.allManeuvers) > 1 else nav_msg.allManeuvers[0]
    distance = m.distance
    key = (m.instruction, m.type, m.modifier)

    # distance jumping up means the previous maneuver was passed, even if the text repeats
    if key != self._banner_key or distance > self._banner_distance + NAV_BANNER.NEW_MANEUVER_JUMP_M:
      self._banner_key = key
      self._banner_crossed = 0
      self._banner_frames = 0
    self._banner_distance = distance

    crossed = sum(1 for t in NAV_BANNER.THRESHOLDS_M if distance <= t)
    if crossed > self._banner_crossed:
      self._banner_crossed = crossed
      self._banner_frames = int(NAV_BANNER.SHOW_SECONDS / DT_MDL)

    if self._banner_frames > 0:
      self._banner_frames -= 1
      return True
    return False

  def update(self, sm: messaging.SubMaster) -> list:
    self._counter += 1
    if self._counter % int(3.0 / DT_MDL) == 0:
      self._mode = int(self._params.get("NavBannerMode", return_default=True))

    if self._mode == BannerMode.OFF:
      return []

    if self._mode == BannerMode.INCREMENTAL and not self._banner_due(sm['navigationd']):
      return []

    return self.build_navigation_events(sm)
