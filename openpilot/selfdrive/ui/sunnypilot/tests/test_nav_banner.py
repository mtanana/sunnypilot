"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from openpilot.cereal import custom

from openpilot.selfdrive.ui.sunnypilot.onroad.hud_renderer import HudRendererSP, PILL_BOTTOM
from openpilot.selfdrive.ui.sunnypilot.onroad.nav_banner import (
  DEFAULT_ICON, ICON_FILES, banner_content, icon_name, wrap_two_lines,
)
from openpilot.selfdrive.ui.sunnypilot.onroad.transient_nav import ChipMode, TransientNavState
from openpilot.sunnypilot.navd.helpers import string_to_direction
from openpilot.common.test import OpenpilotTestCase


def _msg(maneuvers=(), banner: str = '', lane_count: int = 0, route_state: str = 'onRoute', failures: int = 0):
  msg = custom.Navigationd.new_message()
  msg.bannerInstructions = banner
  msg.routeState = route_state
  msg.routeFailures = failures
  msg.init('allManeuvers', len(maneuvers))
  for slot, (maneuver_type, modifier, distance, instruction) in zip(msg.allManeuvers, maneuvers, strict=True):
    slot.type = maneuver_type
    slot.modifier = modifier
    slot.distance = distance
    slot.instruction = instruction
  lanes = msg.init('lanes', lane_count)
  for lane in lanes:
    lane.active = True
    lane.activeDirection = 'right'
  return msg


THREE_STEPS = (('depart', 'none', 0.0, 'Head north'),
               ('turn', 'right', 400.0, 'Turn right onto North Neil Street'),
               ('fork', 'slightLeft', 900.0, 'Keep left toward I-74 West'))


class TestIconSet(OpenpilotTestCase):
  def test_import_is_complete(self):
    assert len(ICON_FILES) == 87
    assert 'direction_notification_sharp_right' in ICON_FILES

  def test_default_exists(self):
    assert DEFAULT_ICON in ICON_FILES


class TestIconMapping(OpenpilotTestCase):
  def test_exact_pair(self):
    assert icon_name('turn', 'right') == 'direction_turn_right'
    assert icon_name('fork', 'slightLeft') == 'direction_fork_slight_left'

  def test_spaces_and_camel_case_become_snake_case(self):
    assert icon_name('off ramp', 'slightRight') == 'direction_off_ramp_slight_right'
    assert icon_name('on ramp', 'sharpLeft') == 'direction_on_ramp_sharp_left'

  def test_bare_type_when_no_modifier(self):
    assert icon_name('arrive', 'none') == 'direction_arrive'
    assert icon_name('roundabout', '') == 'direction_roundabout'

  def test_compound_roundabout_aliases(self):
    assert icon_name('roundabout turn', 'left') == 'direction_roundabout_left'
    assert icon_name('exit roundabout', 'slightLeft') == 'direction_roundabout_slight_left'
    assert icon_name('exit rotary', 'right') == 'direction_rotary_right'

  def test_bare_type_beats_the_turn_family(self):
    # invalid has no sharp variants, and the right fallback is its own generic icon
    assert icon_name('invalid', 'sharpLeft') == 'direction_invalid'

  def test_turn_family_covers_typeless_gaps(self):
    # end of road has no straight variant and no bare icon
    assert icon_name('end of road', 'straight') == 'direction_turn_straight'

  def test_bare_modifier_carries_uturn(self):
    assert icon_name('turn', 'uturn') == 'direction_uturn'
    assert icon_name('continue', 'uturn') == 'direction_continue_uturn'

  def test_unknown_everything_falls_to_default(self):
    assert icon_name('teleport', 'warp') == DEFAULT_ICON

  def test_published_vocabulary_always_resolves(self):
    types = ['turn', 'new name', 'depart', 'arrive', 'merge', 'on ramp', 'off ramp', 'fork',
             'end of road', 'continue', 'roundabout', 'rotary', 'roundabout turn',
             'notification', 'exit roundabout', 'exit rotary']
    raw_modifiers = ['left', 'right', 'straight', 'slight left', 'slight right',
                     'sharp left', 'sharp right', 'uturn', '']
    for maneuver_type in types:
      for raw in raw_modifiers:
        assert icon_name(maneuver_type, string_to_direction(raw)) in ICON_FILES


def _measure(text: str) -> float:
  return len(text) * 10


class TestWrapTwoLines(OpenpilotTestCase):
  def test_short_text_stays_on_one_line(self):
    assert wrap_two_lines('North Neil Street', 200, _measure) == ['North Neil Street']

  def test_wraps_at_word_boundaries(self):
    lines = wrap_two_lines('North Prospect Avenue South', 150, _measure)
    assert lines == ['North Prospect', 'Avenue South']

  def test_overflow_is_elided_with_ellipsis(self):
    lines = wrap_two_lines('Martin Luther King Junior Memorial Highway Extension', 150, _measure)
    assert len(lines) == 2
    assert lines[1].endswith('…')
    assert all(_measure(line) <= 150 for line in lines)

  def test_single_overlong_word_is_kept_and_elided(self):
    lines = wrap_two_lines('Pneumonoultramicroscopic', 100, _measure)
    assert len(lines) == 1
    assert lines[0].endswith('…')
    assert _measure(lines[0]) <= 100

  def test_empty_text(self):
    assert wrap_two_lines('', 100, _measure) == []


class TestBannerContent(OpenpilotTestCase):
  def test_down_while_quiet_or_off(self):
    msg = _msg(THREE_STEPS)
    for state in (TransientNavState.OFF, TransientNavState.QUIET):
      assert banner_content(state, ChipMode.LIVE, msg, True) is None

  def test_down_without_a_live_route(self):
    msg = _msg(THREE_STEPS)
    for mode in (ChipMode.HIDDEN, ChipMode.SEARCHING, ChipMode.FAILURE):
      assert banner_content(TransientNavState.APPROACH, mode, msg, True) is None

  def test_shows_the_upcoming_maneuver_and_the_one_after(self):
    for state in (TransientNavState.APPROACH, TransientNavState.PINNED):
      content = banner_content(state, ChipMode.LIVE, _msg(THREE_STEPS), False)
      assert content is not None
      assert (content.maneuver_type, content.modifier) == ('turn', 'right')
      assert (content.then_type, content.then_modifier) == ('fork', 'slightLeft')

  def test_no_then_chip_on_the_last_maneuver(self):
    content = banner_content(TransientNavState.APPROACH, ChipMode.LIVE, _msg(THREE_STEPS[:2]), False)
    assert content is not None
    assert content.then_type is None

  def test_route_state_rides_along_for_the_pinned_treatments(self):
    content = banner_content(TransientNavState.PINNED, ChipMode.LIVE, _msg(THREE_STEPS, route_state='offRoute'), False)
    assert content is not None
    assert content.route_state == 'offRoute' and not content.failing

  def test_rerouting_failures_are_flagged(self):
    content = banner_content(TransientNavState.PINNED, ChipMode.LIVE,
                             _msg(THREE_STEPS, route_state='rerouting', failures=1), False)
    assert content is not None
    assert content.route_state == 'rerouting' and content.failing

  def test_lone_arrive_still_shows(self):
    content = banner_content(TransientNavState.APPROACH, ChipMode.LIVE,
                             _msg((('arrive', 'none', 60.0, 'You have arrived'),)), False)
    assert content is not None
    assert content.maneuver_type == 'arrive'
    assert content.then_type is None

  def test_down_with_nothing_upcoming(self):
    assert banner_content(TransientNavState.PINNED, ChipMode.LIVE, _msg(), False) is None
    assert banner_content(TransientNavState.PINNED, ChipMode.LIVE,
                          _msg((('depart', 'none', 120.0, 'Head north'),)), False) is None

  def test_street_prefers_the_parsed_banner_text(self):
    content = banner_content(TransientNavState.APPROACH, ChipMode.LIVE,
                             _msg(THREE_STEPS, banner='N Neil St'), False)
    assert content is not None
    assert content.street == 'N Neil St'

  def test_street_falls_back_to_the_instruction(self):
    content = banner_content(TransientNavState.APPROACH, ChipMode.LIVE, _msg(THREE_STEPS), False)
    assert content is not None
    assert content.street == 'Turn right onto North Neil Street'

  def test_lanes_are_gated_on_the_setting(self):
    msg = _msg(THREE_STEPS, lane_count=2)
    off = banner_content(TransientNavState.APPROACH, ChipMode.LIVE, msg, False)
    on = banner_content(TransientNavState.APPROACH, ChipMode.LIVE, msg, True)
    assert off is not None and len(off.lanes) == 0
    assert on is not None and len(on.lanes) == 2


class TestReflowGating(OpenpilotTestCase):
  def test_consolidates_only_while_the_banner_is_up(self):
    assert HudRendererSP.speed_displays_consolidated(True, False)
    assert not HudRendererSP.speed_displays_consolidated(False, False)

  def test_hidden_current_speed_leaves_the_box_alone(self):
    assert not HudRendererSP.speed_displays_consolidated(True, True)

  def test_summary_tucks_under_the_chip_normally(self):
    assert HudRendererSP.nav_summary_anchor(False, False, True, chip_bottom=333.0) == 333.0

  def test_summary_tucks_under_the_pill_while_expanded(self):
    assert HudRendererSP.nav_summary_anchor(True, False, True, chip_bottom=333.0) == PILL_BOTTOM

  def test_summary_tucks_under_the_box_when_the_reflow_keeps_it(self):
    # hidden current speed or no cruise: the standard box stays, ending at 45 + 204
    assert HudRendererSP.nav_summary_anchor(True, True, True, chip_bottom=333.0) == 249.0
    assert HudRendererSP.nav_summary_anchor(True, False, False, chip_bottom=333.0) == 249.0


class TestAssistPulse(OpenpilotTestCase):
  def test_banner_breathes_while_assisting_on_route(self):
    content = banner_content(TransientNavState.APPROACH, ChipMode.LIVE, _msg(THREE_STEPS), False, assist=True)
    assert content is not None and content.pulse

  def test_off_route_never_pulses(self):
    content = banner_content(TransientNavState.APPROACH, ChipMode.LIVE, _msg(THREE_STEPS, route_state='offRoute'), False, assist=True)
    assert content is not None and not content.pulse

  def test_pulse_breathes_over_a_third(self):
    from openpilot.selfdrive.ui.sunnypilot.onroad.nav_indicator import PULSE_DEPTH, PULSE_PERIOD, assist_pulse
    assert assist_pulse(0.0) == 1.0
    assert abs(assist_pulse(PULSE_PERIOD / 2) - (1.0 - PULSE_DEPTH)) < 1e-9
    assert abs(assist_pulse(PULSE_PERIOD) - 1.0) < 1e-9
