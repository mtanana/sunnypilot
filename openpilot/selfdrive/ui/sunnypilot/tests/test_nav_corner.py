"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from openpilot.cereal import custom

from openpilot.selfdrive.ui.sunnypilot.mici.onroad.hud_renderer import HudRendererSP
from openpilot.selfdrive.ui.sunnypilot.mici.onroad.nav_corner import (
  FAILURE_ALPHA, FULL_ALPHA, QUIET_ALPHA, SEARCH_ALPHA, corner_content,
)
from openpilot.selfdrive.ui.sunnypilot.onroad.transient_nav import ChipMode, TransientNavState
from openpilot.common.test import OpenpilotTestCase


def _msg(maneuvers=(), lane_count: int = 0, route_state: str = 'onRoute', failures: int = 0):
  msg = custom.Navigationd.new_message()
  msg.routeState = route_state
  msg.routeFailures = failures
  msg.init('allManeuvers', len(maneuvers))
  for slot, (maneuver_type, modifier, distance) in zip(msg.allManeuvers, maneuvers, strict=True):
    slot.type = maneuver_type
    slot.modifier = modifier
    slot.distance = distance
  lanes = msg.init('lanes', lane_count)
  for lane in lanes:
    lane.active = True
    lane.activeDirection = 'right'
  return msg


TWO_STEPS = (('depart', 'none', 0.0), ('turn', 'right', 400.0))


class TestStatusStates(OpenpilotTestCase):
  def test_searching_shows_the_faint_flag_in_any_state(self):
    for state in TransientNavState:
      content = corner_content(state, ChipMode.SEARCHING, _msg(), True, False)
      assert content is not None and content.kind == 'searching'
      assert content.alpha == SEARCH_ALPHA

  def test_failure_shows_the_flag_brighter(self):
    content = corner_content(TransientNavState.QUIET, ChipMode.FAILURE, _msg(), False, False)
    assert content is not None and content.kind == 'failure'
    assert content.alpha == FAILURE_ALPHA

  def test_searching_flag_carries_its_stage(self):
    # pole only until the GPS fix, pole plus banner once the wait is on Mapbox
    lowered = corner_content(TransientNavState.QUIET, ChipMode.SEARCHING, _msg(), False, False, raised=False)
    assert lowered is not None and not lowered.raised
    raised = corner_content(TransientNavState.QUIET, ChipMode.SEARCHING, _msg(), False, False, raised=True)
    assert raised is not None and raised.raised

  def test_failure_flag_always_flies_full(self):
    content = corner_content(TransientNavState.QUIET, ChipMode.FAILURE, _msg(), False, False, raised=False)
    assert content is not None and content.raised


class TestRouteStateTreatments(OpenpilotTestCase):
  def test_off_route_dims_to_a_bare_glyph_even_with_quiet_glyph_off(self):
    # PINNED wears the same treatment instead of collapsing; APPROACH never sees offRoute
    # because the machine force-collapses it first
    for state in (TransientNavState.QUIET, TransientNavState.PINNED):
      content = corner_content(state, ChipMode.LIVE, _msg(TWO_STEPS, 2, route_state='offRoute'), True, False)
      assert content is not None and content.kind == 'maneuver'
      assert content.alpha == QUIET_ALPHA
      assert content.distance is None and content.lanes == ()

  def test_rerouting_shows_the_searching_flag(self):
    content = corner_content(TransientNavState.PINNED, ChipMode.LIVE, _msg(TWO_STEPS, route_state='rerouting'), False, False)
    assert content is not None and content.kind == 'searching'

  def test_rerouting_failures_turn_the_flag_red(self):
    content = corner_content(TransientNavState.QUIET, ChipMode.LIVE,
                             _msg(TWO_STEPS, route_state='rerouting', failures=1), False, False)
    assert content is not None and content.kind == 'failure'

  def test_hidden_mode_is_an_empty_corner(self):
    assert corner_content(TransientNavState.APPROACH, ChipMode.HIDDEN, _msg(TWO_STEPS), True, True) is None


class TestQuietState(OpenpilotTestCase):
  def test_default_quiet_is_an_empty_corner(self):
    assert corner_content(TransientNavState.QUIET, ChipMode.LIVE, _msg(TWO_STEPS), True, False) is None

  def test_param_turns_on_the_faint_glyph(self):
    content = corner_content(TransientNavState.QUIET, ChipMode.LIVE, _msg(TWO_STEPS), True, True)
    assert content is not None and content.kind == 'maneuver'
    assert (content.maneuver_type, content.modifier) == ('turn', 'right')
    assert content.alpha == QUIET_ALPHA

  def test_faint_glyph_is_a_glyph_only(self):
    # no text beyond distance is the mici rule, and the quiet hint has not even that
    content = corner_content(TransientNavState.QUIET, ChipMode.LIVE, _msg(TWO_STEPS, lane_count=3), True, True)
    assert content.distance is None
    assert content.lanes == ()

  def test_off_state_is_empty_even_with_the_param(self):
    assert corner_content(TransientNavState.OFF, ChipMode.LIVE, _msg(TWO_STEPS), True, True) is None


class TestExpandedStates(OpenpilotTestCase):
  def test_approach_carries_distance_and_lanes(self):
    content = corner_content(TransientNavState.APPROACH, ChipMode.LIVE, _msg(TWO_STEPS, lane_count=3), True, False)
    assert content is not None
    assert content.alpha == FULL_ALPHA
    assert content.distance == 400.0
    assert len(content.lanes) == 3

  def test_pinned_matches_approach(self):
    approach = corner_content(TransientNavState.APPROACH, ChipMode.LIVE, _msg(TWO_STEPS), True, False)
    pinned = corner_content(TransientNavState.PINNED, ChipMode.LIVE, _msg(TWO_STEPS), True, False)
    assert approach == pinned

  def test_lane_guidance_off_drops_the_lane_row(self):
    content = corner_content(TransientNavState.APPROACH, ChipMode.LIVE, _msg(TWO_STEPS, lane_count=3), False, False)
    assert content.lanes == ()

  def test_a_lone_arrive_step_still_shows(self):
    content = corner_content(TransientNavState.APPROACH, ChipMode.LIVE, _msg((('arrive', 'none', 120.0),)), True, False)
    assert content is not None
    assert content.maneuver_type == 'arrive'

  def test_no_upcoming_maneuver_is_an_empty_corner(self):
    assert corner_content(TransientNavState.APPROACH, ChipMode.LIVE, _msg(), True, False) is None
    # a lone non-arrive step is the step being driven, with nothing ahead to show
    assert corner_content(TransientNavState.APPROACH, ChipMode.LIVE, _msg((('depart', 'none', 0.0),)), True, False) is None


class TestCornerCanDraw(OpenpilotTestCase):
  def test_alert_suppresses_the_corner(self):
    assert not HudRendererSP.corner_can_draw(False, False, 0.0)

  def test_set_speed_circle_owns_the_slot_while_up(self):
    assert not HudRendererSP.corner_can_draw(True, True, 1.0)

  def test_stale_circle_alpha_does_not_hold_the_slot(self):
    # cruise unset freezes the circle's filter; the corner must not stay suppressed by it
    assert HudRendererSP.corner_can_draw(True, False, 1.0)

  def test_clear_corner_draws(self):
    assert HudRendererSP.corner_can_draw(True, True, 0.0)


class TestAssistPulse(OpenpilotTestCase):
  """The glyph of a maneuver the car is acting on breathes: a slowdown, or a lane change it stands ready to take."""

  def test_quiet_corner_raises_the_glyph_and_pulses(self):
    content = corner_content(TransientNavState.QUIET, ChipMode.LIVE, _msg(TWO_STEPS), True, False, assist=True)
    assert content is not None
    assert (content.kind, content.alpha, content.modifier, content.pulse) == ('maneuver', QUIET_ALPHA, 'right', True)
    assert content.distance is None

  def test_approach_keeps_its_card_and_pulses(self):
    content = corner_content(TransientNavState.APPROACH, ChipMode.LIVE, _msg(TWO_STEPS, lane_count=2), True, False, assist=True)
    assert content is not None
    assert (content.alpha, content.distance, len(content.lanes), content.pulse) == (FULL_ALPHA, 400.0, 2, True)

  def test_off_route_never_pulses(self):
    content = corner_content(TransientNavState.QUIET, ChipMode.LIVE, _msg(TWO_STEPS, route_state='offRoute'), True, False, assist=True)
    assert content is not None
    assert not content.pulse

  def test_without_assist_nothing_changes(self):
    assert corner_content(TransientNavState.QUIET, ChipMode.LIVE, _msg(TWO_STEPS), True, False) is None
    content = corner_content(TransientNavState.QUIET, ChipMode.LIVE, _msg(TWO_STEPS), True, True)
    assert content is not None
    assert not content.pulse
