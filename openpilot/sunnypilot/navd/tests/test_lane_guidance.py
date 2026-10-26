"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from types import SimpleNamespace
from typing import cast

import openpilot.cereal.messaging as messaging

from openpilot.cereal import log
from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL
from openpilot.selfdrive.controls.lib.desire_helper import DesireHelper
from openpilot.sunnypilot.navd.constants import NAV_LANE_CHANGE_OFF
from openpilot.sunnypilot.navd.helpers import Coordinate, compose_banner_text, lane_change_auto_confirm, lane_change_hint, parse_banner_instructions
from openpilot.sunnypilot.navd.navigation_desires.navigation_desires import NavigationDesires
from openpilot.sunnypilot.navd.navigation_helpers.route import Maneuver, Route, RouteProgress, Step
from openpilot.sunnypilot.navd.navigationd import HINT_STABLE_CYCLES, Guidance, HintDebounce, Navigationd
from openpilot.sunnypilot.navd.tests.helpers import NavdTestCase


def _banner(distance: float, lanes: list | None = None) -> dict:
  banner = {'distanceAlongGeometry': distance, 'primary': {'text': 'Turn right', 'type': 'turn', 'modifier': 'right'}}
  if lanes is not None:
    banner['sub'] = {'components': [{'type': 'lane', **lane} for lane in lanes] + [{'type': 'text', 'text': 'noise'}]}
  return banner


class TestLaneParsing(NavdTestCase):
  def test_lanes_normalized(self):
    banners = [_banner(400, lanes=[
      {'active': False, 'directions': ['straight']},
      {'active': True, 'directions': ['straight', 'slight right'], 'active_direction': 'slight right'},
      {'active': False, 'directions': ['uturn']},
    ])]
    parsed = parse_banner_instructions(banners, distance_to_maneuver=100)
    assert parsed is not None
    assert parsed['showFull']
    assert parsed['lanes'] == [
      {'active': False, 'directions': ['straight']},
      {'active': True, 'directions': ['straight', 'slightRight'], 'activeDirection': 'slightRight'},
      {'active': False, 'directions': ['uturn']},
    ]

  def test_banner_without_sub_parses_to_primary_only(self):
    parsed = parse_banner_instructions([_banner(400)], distance_to_maneuver=100)
    assert parsed == {'showFull': True, 'maneuverPrimaryText': 'Turn right', 'maneuverType': 'turn', 'maneuverModifier': 'right'}

  def test_far_banner_is_not_full(self):
    parsed = parse_banner_instructions([_banner(400)], distance_to_maneuver=800)
    assert parsed is not None
    assert not parsed['showFull']


class TestLanePublishing(NavdTestCase):
  def test_lanes_reach_the_message(self):
    nav = Navigationd()
    guidance = Guidance(lanes=[{'active': True, 'directions': ['left', 'straight'], 'activeDirection': 'left'},
                               {'active': False, 'directions': ['right']}])
    msg = nav._build_navigation_message('', None, guidance, True)
    lanes = msg.navigationd.lanes
    assert len(lanes) == 2
    assert list(lanes[0].directions) == ['left', 'straight']
    assert lanes[0].active
    assert lanes[0].activeDirection == 'left'
    assert not lanes[1].active
    assert lanes[1].activeDirection == ''

  def test_no_lanes_publishes_empty(self):
    nav = Navigationd()
    msg = nav._build_navigation_message('', None, Guidance(), True)
    assert len(msg.navigationd.lanes) == 0
    assert msg.navigationd.laneChangeDirection == 'none'


DEPART = Step('depart', 'none', '', 40.0, 5.0, Coordinate(32.7767, -96.797), 0.0, (0, 'kmh'), [])


def _maneuvers_progress(maneuvers: list[Maneuver], distance_from_route: float = 0.0) -> RouteProgress:
  return RouteProgress(distance_from_route, 0, 0, DEPART, None, 10.0, 1000.0, 60.0, maneuvers)


def _hint_progress(maneuver_type: str, modifier: str, distance: float) -> RouteProgress:
  return _maneuvers_progress([Maneuver(40.0, 'depart', 'none', ''), Maneuver(distance, maneuver_type, modifier, '')])


class TestLaneChangeHint(NavdTestCase):
  def test_sides(self):
    assert lane_change_hint(_hint_progress('off ramp', 'slightRight', 200.0), 30.0) == 'right'
    assert lane_change_hint(_hint_progress('turn', 'left', 200.0), 30.0) == 'left'
    assert lane_change_hint(_hint_progress('turn', 'uturn', 200.0), 30.0) == 'left'

  def test_window_scales_with_speed(self):
    progress = _hint_progress('off ramp', 'slightRight', 700.0)
    assert lane_change_hint(progress, 35.0) == 'right'
    assert lane_change_hint(progress, 10.0) == 'none'

  def test_roundabouts_and_straight_are_excluded(self):
    assert lane_change_hint(_hint_progress('roundabout', 'slightRight', 100.0), 30.0) == 'none'
    assert lane_change_hint(_hint_progress('continue', 'straight', 100.0), 30.0) == 'none'

  def test_lone_maneuver_has_no_hint(self):
    assert lane_change_hint(_maneuvers_progress([Maneuver(50.0, 'arrive', 'none', '')]), 30.0) == 'none'


class TestAutoConfirm(NavdTestCase):
  def test_slip_road_topology_confirms(self):
    assert lane_change_auto_confirm(_hint_progress('off ramp', 'slightRight', 200.0))
    assert lane_change_auto_confirm(_hint_progress('merge', 'slightLeft', 200.0))

  def test_turns_never_confirm(self):
    # near a turn the blinker means the turn: an auto lane change would aim at a lane
    # that may not exist, so only the wheel nudge starts one, however many lanes the map shows
    for maneuver_type in ('turn', 'fork', 'continue', 'end of road'):
      assert not lane_change_auto_confirm(_hint_progress(maneuver_type, 'left', 200.0))

  def test_uturn_never_confirms(self):
    assert not lane_change_auto_confirm(_hint_progress('turn', 'uturn', 200.0))

  def test_lone_maneuver_does_not_confirm(self):
    assert not lane_change_auto_confirm(_maneuvers_progress([Maneuver(50.0, 'arrive', 'none', '')]))

  def test_flag_reaches_the_message(self):
    nav = Navigationd()
    msg = nav._build_navigation_message('', None, Guidance(lane_change_direction='left', lane_change_auto_confirm=True), True)
    assert msg.navigationd.laneChangeAutoConfirm
    msg = nav._build_navigation_message('', None, Guidance(lane_change_direction='left'), True)
    assert not msg.navigationd.laneChangeAutoConfirm


# road names captured from real drives, where they appeared on the turn card with no action attached
class TestBannerWording(NavdTestCase):
  def test_road_names_get_their_action_back(self):
    assert compose_banner_text('South 5th Street / I 55 Business', 'turn', 'right') == 'Turn right onto South 5th Street / I 55 Business'
    assert compose_banner_text('Exit 179 I-57', 'off ramp', 'slight right') == 'Take Exit 179 I-57'
    assert compose_banner_text('I 72 East / US 51 North', 'merge', 'slight left') == 'Merge left onto I 72 East / US 51 North'
    assert compose_banner_text('North Neil Street', 'turn', 'slight left') == 'Bear left onto North Neil Street'

  def test_action_only_texts_pass_through(self):
    assert compose_banner_text('Turn right', 'turn', 'right') == 'Turn right'
    assert compose_banner_text('Bear left', 'turn', 'slight left') == 'Bear left'
    assert compose_banner_text('Your destination is on the right', 'arrive', 'right') == 'Your destination is on the right'

  def test_forks_and_unnumbered_exits(self):
    assert compose_banner_text('I-74 West', 'fork', 'left') == 'Keep left toward I-74 West'
    assert compose_banner_text('J. David Jones Parkway / IL 29', 'off ramp', 'right') == 'Take the right exit onto J. David Jones Parkway / IL 29'

  def test_uturns_and_roundabouts(self):
    assert compose_banner_text('Main Street', 'turn', 'uturn') == 'Make a U-turn onto Main Street'
    assert compose_banner_text('Main Street', 'roundabout', 'slight right') == 'At the roundabout, exit onto Main Street'

  def test_parse_composes_the_primary_text(self):
    banners = [{'distanceAlongGeometry': 400, 'primary': {'text': 'South 4th Street', 'type': 'turn', 'modifier': 'left'}}]
    parsed = parse_banner_instructions(banners, distance_to_maneuver=100)
    assert parsed is not None
    assert parsed['maneuverPrimaryText'] == 'Turn left onto South 4th Street'


class TestHintStability(NavdTestCase):
  def test_a_flapping_direction_never_publishes(self):
    hint = HintDebounce()
    published = [hint.update(h) for h in ['left', 'right'] * 5]
    assert set(published) == {'none'}

  def test_a_steady_direction_publishes_after_the_dwell(self):
    hint = HintDebounce()
    published = [hint.update('left') for _ in range(HINT_STABLE_CYCLES + 1)]
    assert published[:HINT_STABLE_CYCLES - 1] == ['none'] * (HINT_STABLE_CYCLES - 1)
    assert published[HINT_STABLE_CYCLES - 1:] == ['left', 'left']

  def test_a_cleared_hint_publishes_immediately(self):
    hint = HintDebounce()
    for _ in range(HINT_STABLE_CYCLES):
      hint.update('left')
    assert hint.update('none') == 'none'


def _trusted_progress(distance_from_route: float) -> RouteProgress:
  return _maneuvers_progress([Maneuver(40.0, 'depart', 'none', ''), Maneuver(100.0, 'off ramp', 'slightRight', '')],
                             distance_from_route)


class TestHintTrust(NavdTestCase):
  def _nav(self, distance_from_route: float) -> Navigationd:
    nav = Navigationd()
    nav.allow_navigation = True
    nav.lane_assist = True
    nav.route = cast(Route, SimpleNamespace(steps=[{}, {}], progress=lambda position: _trusted_progress(distance_from_route),
                                            bearing_misaligned=lambda *args: False))
    nav.last_position = Coordinate(32.7767, -96.797)
    return nav

  def _run(self, nav: Navigationd) -> Guidance:
    for _ in range(HINT_STABLE_CYCLES + 1):
      _, _, guidance = nav._update_navigation()
    return guidance

  def test_hints_publish_on_a_trusted_route(self):
    guidance = self._run(self._nav(5.0))
    assert guidance.lane_change_direction == 'right'
    assert guidance.lane_change_auto_confirm

  def test_hints_are_suppressed_off_route(self):
    guidance = self._run(self._nav(500.0))
    assert guidance.lane_change_direction == 'none'
    assert not guidance.lane_change_auto_confirm

  def test_hints_are_suppressed_while_reroutes_fail(self):
    nav = self._nav(5.0)
    nav.retry.failed_attempts = 1
    guidance = self._run(nav)
    assert guidance.lane_change_direction == 'none'
    assert not guidance.lane_change_auto_confirm


class TestAssistGate(NavdTestCase):
  def test_hint_requires_a_lane_change_timer_and_valid_message(self):
    desires = NavigationDesires()
    msg = desires.sm['navigationd']

    Params().put("NavLaneChangeTimer", NAV_LANE_CHANGE_OFF, block=True)
    desires.param_counter = -1
    desires.update_params()
    assert not desires.lane_assist
    assert desires.lane_change_hint() == 'none'

    Params().put("NavLaneChangeTimer", 1, block=True)
    desires.param_counter = -1
    desires.update_params()
    assert desires.lane_assist
    # message is not valid yet, so still gated
    assert not msg.valid
    assert desires.lane_change_hint() == 'none'

  def test_hint_requires_the_auto_confirm_flag(self):
    desires = NavigationDesires()
    desires.lane_assist = True
    desires.sm = cast(messaging.SubMaster, {'navigationd': SimpleNamespace(valid=True, laneChangeDirection='left', laneChangeAutoConfirm=False)})
    assert desires.lane_change_hint() == 'none'
    desires.sm = cast(messaging.SubMaster, {'navigationd': SimpleNamespace(valid=True, laneChangeDirection='left', laneChangeAutoConfirm=True)})
    assert desires.lane_change_hint() == 'left'


class TestNavConfirmation(NavdTestCase):
  """desire_helper: the blinker alone starts a hinted lane change once the nav timer has run."""

  def _helper(self, mocker, timer: int) -> DesireHelper:
    Params().put("NavLaneChangeTimer", timer, block=True)
    dh = DesireHelper()
    mocker.patch.object(dh.navigation_desires, 'lane_change_hint', return_value='right')
    mocker.patch.object(dh.navigation_desires, 'update', return_value=log.Desire.none)
    mocker.patch.object(dh.adjacent_lane_detector, 'available', return_value=True)
    return dh

  def _signal_for(self, dh: DesireHelper, seconds: float):
    car = SimpleNamespace(vEgo=30.0, leftBlinker=False, rightBlinker=True, leftBlindspot=False, rightBlindspot=False,
                          steeringPressed=False, steeringTorque=0, brakePressed=False)
    for _ in range(int(seconds / DT_MDL) + 2):
      dh.update(car, True, 1.0)
    return dh.lane_change_state

  def test_the_hint_starts_the_lane_change_after_the_timer(self, mocker):
    dh = self._helper(mocker, 2)  # 0.5 s
    assert self._signal_for(dh, 0.2) == log.LaneChangeState.preLaneChange
    assert self._signal_for(dh, 0.5) == log.LaneChangeState.laneChangeStarting

  def test_off_leaves_the_nudge_flow_in_place(self, mocker):
    dh = self._helper(mocker, NAV_LANE_CHANGE_OFF)
    assert self._signal_for(dh, 5.0) == log.LaneChangeState.preLaneChange
