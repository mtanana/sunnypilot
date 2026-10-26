"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from openpilot.cereal import custom, log

from openpilot.selfdrive.ui.sunnypilot.onroad import nav_indicator as nav_indicator_module
from openpilot.selfdrive.ui.sunnypilot.onroad.nav_indicator import ARROW_ANGLES, assist_ready, format_distance
from openpilot.selfdrive.ui.sunnypilot.onroad.route_summary import (
  MIN_SCALE, PADDING_V, ROW_HEIGHT, format_remaining_time, plan_layout,
)
from openpilot.sunnypilot.navd.helpers import string_to_direction
from openpilot.common.test import Mocker, OpenpilotTestCase


class TestFormatDistance(OpenpilotTestCase):
  def test_metric(self):
    assert format_distance(432, True) == "430 m"
    assert format_distance(996, True) == "1000 m"
    assert format_distance(1230, True) == "1.2 km"
    assert format_distance(15400, True) == "15 km"

  def test_imperial(self):
    assert format_distance(100, False) == "350 ft"
    assert format_distance(304, False) == "1000 ft"
    assert format_distance(305, False) == "0.2 mi"
    assert format_distance(500, False) == "0.3 mi"
    assert format_distance(17000, False) == "11 mi"

  def test_never_negative(self):
    assert format_distance(-5, True) == "0 m"
    assert format_distance(-5, False) == "0 ft"


class TestAssistReady(OpenpilotTestCase):
  """The lane change delay stands ready: the breath lasts until the car moves over."""

  def setup_method(self):
    self.sm: dict = {}
    Mocker(self.addCleanup).patch.object(nav_indicator_module.ui_state, 'sm', self.sm)

  def _set(self, direction='right', confirm=True, lane_change=log.LaneChangeState.off):
    nav = custom.Navigationd.new_message()
    nav.laneChangeDirection = direction
    nav.laneChangeAutoConfirm = confirm
    model = log.ModelDataV2.new_message()
    model.meta.laneChangeState = lane_change
    self.sm['navigationd'] = nav
    self.sm['modelV2'] = model

  def test_ready_while_the_hint_can_confirm(self):
    self._set()
    assert assist_ready()
    self._set(lane_change=log.LaneChangeState.preLaneChange)
    assert assist_ready()

  def test_a_hint_that_cannot_confirm_is_only_advisory(self):
    self._set(confirm=False)
    assert not assist_ready()
    self._set(direction='none', confirm=False)
    assert not assist_ready()

  def test_stops_once_the_car_moves_over(self):
    self._set(lane_change=log.LaneChangeState.laneChangeStarting)
    assert not assist_ready()
    self._set(lane_change=log.LaneChangeState.laneChangeFinishing)
    assert not assist_ready()


class TestFormatRemainingTime(OpenpilotTestCase):
  def test_minutes(self):
    assert format_remaining_time(0) == "0 min"
    assert format_remaining_time(59) == "1 min"
    assert format_remaining_time(25 * 60) == "25 min"

  def test_hours(self):
    assert format_remaining_time(3600) == "1 hr 0 min"
    assert format_remaining_time(4980) == "1 hr 23 min"
    assert format_remaining_time(7260) == "2 hr 1 min"


class TestSummaryLayout(OpenpilotTestCase):
  FULL_HEIGHT = ROW_HEIGHT * 3 + 2 * PADDING_V

  def test_top_anchored_under_the_stack(self):
    # the card tucks under whatever is above it rather than floating up from the bottom
    plan = plan_layout(500.0, 2000.0, 3)
    assert plan is not None
    kept, scale, y = plan
    assert (kept, scale, y) == (3, 1.0, 500.0)

  def test_scales_down_before_shedding(self):
    plan = plan_layout(0.0, self.FULL_HEIGHT * 0.8, 3)
    assert plan is not None
    kept, scale, _ = plan
    assert kept == 3
    assert MIN_SCALE <= scale < 1.0

  def test_sheds_a_row_when_scaling_is_not_enough(self):
    plan = plan_layout(0.0, self.FULL_HEIGHT * MIN_SCALE * 0.9, 3)
    assert plan is not None
    kept, scale, _ = plan
    assert kept == 2
    assert scale >= MIN_SCALE

  def test_hides_when_the_rail_has_no_room(self):
    assert plan_layout(0.0, 40.0, 3) is None
    assert plan_layout(980.0, 1000.0, 2) is None


class TestArrowVocabulary(OpenpilotTestCase):
  def test_covers_every_normalized_modifier(self):
    # navigationd publishes string_to_direction's output verbatim, so every value it can
    # produce must either map to an angle or have a dedicated glyph ('uturn')
    raw_modifiers = ['left', 'right', 'straight', 'slight left', 'slight right',
                     'sharp left', 'sharp right', '']
    for raw in raw_modifiers:
      assert string_to_direction(raw) in ARROW_ANGLES

  def test_uturn_is_not_flattened(self):
    assert string_to_direction('uturn') == 'uturn'
