from typing import Any

import numpy as np

from openpilot.cereal import log
from openpilot.common.test import OpenpilotTestCase
from openpilot.selfdrive.controls.lib.drive_helpers import lat_smooth_seconds, smooth_value
from openpilot.sunnypilot.modeld_v2.modeld import ModelState


class MockStruct:
  def __init__(self, **kwargs):
    for k, v in kwargs.items():
      setattr(self, k, v)


class TestLatSmooth(OpenpilotTestCase):
  def test_schedule_fades_out_by_highway_speed(self):
    for v_ego, tau in ((0.0, 0.4), (5.0, 0.4), (8.0, 0.3), (12.0, 0.15), (15.0, 0.1), (20.0, 0.1), (30.0, 0.1)):
      assert abs(lat_smooth_seconds(v_ego, 0.1) - tau) < 1e-9, v_ego
    assert lat_smooth_seconds(12.0, 0.0) == 0.15
    assert lat_smooth_seconds(30.0, 0.0) == 0.0

  def test_action_smooths_the_request_with_the_scheduled_constant(self):
    state: Any = MockStruct(LONG_SMOOTH_SECONDS=0.3, LAT_SMOOTH_SECONDS=0.1, MIN_LAT_CONTROL_SPEED=0.3, generation=12)
    prev = log.ModelDataV2.Action(desiredCurvature=0.0, desiredAcceleration=0.0)
    for v_ego, tau in ((3.0, 0.4), (25.0, 0.1)):
      raw = np.array([[0.02 * v_ego ** 2, 0.0]], dtype=np.float32)
      action = ModelState.get_action_from_model(state, {'action': raw}, prev, 0.0, 0.0, v_ego)
      assert abs(action.desiredCurvature - smooth_value(0.02, 0.0, tau)) < 1e-6, v_ego
