"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import json
import math
import platform
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from openpilot.cereal import custom
from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL
from openpilot.selfdrive.car.cruise import V_CRUISE_UNSET
from openpilot.sunnypilot.selfdrive.controls.lib.smart_cruise_control import NAV_TURN_MIN_V
from openpilot.sunnypilot.selfdrive.controls.lib.smart_cruise_control.map_controller import R, SmartCruiseControlMap
from openpilot.common.test import OpenpilotTestCase

MapState = VisionState = custom.LongitudinalPlanSP.SmartCruiseControl.MapState


class TestSmartCruiseControlMap(OpenpilotTestCase):

  def setup_method(self):
    self.mapd_patch = patch("openpilot.sunnypilot.selfdrive.controls.lib.smart_cruise_control.map_controller.MAPD_AVAILABLE", True)
    self.mapd_patch.start()
    self.params = Params()
    self.mem_params = Params("/dev/shm/params") if platform.system() != "Darwin" else self.params
    self.reset_params()
    self.scc_m = SmartCruiseControlMap()
    self.sm = MagicMock()
    self.sm.__getitem__.return_value = SimpleNamespace(latitude=0.0, longitude=0.0)

  def teardown_method(self):
    self.mapd_patch.stop()

  def reset_params(self):
    self.params.put_bool("SmartCruiseControlMap", True, block=True)

    self.params.put("MapTargetVelocities", "{}", block=True)

  def test_initial_state(self):
    assert self.scc_m.state == VisionState.disabled
    assert not self.scc_m.is_active
    assert self.scc_m.output_v_target == V_CRUISE_UNSET
    assert self.scc_m.output_a_target == 0.

  def test_system_disabled(self):
    self.params.put_bool("SmartCruiseControlMap", False, block=True)
    self.scc_m.enabled = self.params.get_bool("SmartCruiseControlMap")

    for _ in range(int(10. / DT_MDL)):
      self.scc_m.update(self.sm, True, False, 0., 0., 0.)
    assert self.scc_m.state == VisionState.disabled
    assert not self.scc_m.is_active

  def test_mapd_unavailable(self):
    self.mapd_patch.stop()
    self.scc_m = SmartCruiseControlMap()
    for _ in range(int(10. / DT_MDL)):
      self.scc_m.update(self.sm, True, False, 0., 0., 0.)
    assert self.scc_m.state == VisionState.disabled
    self.mapd_patch.start()

  def test_disabled(self):
    for _ in range(int(10. / DT_MDL)):
      self.scc_m.update(self.sm, False, False, 0., 0., 0.)
    assert self.scc_m.state == VisionState.disabled

  def test_transition_disabled_to_enabled(self):
    for _ in range(int(10. / DT_MDL)):
      self.scc_m.update(self.sm, True, False, 0., 0., 0.)
    assert self.scc_m.state == VisionState.enabled

  def test_moderate_curve(self):
    # Regression: `... / 2 * a` parsed as `(.../2)*a` instead of `.../(2*a)`,
    # making max_d ~11x too small so the moderate-curve branch never tripped.
    # v_ego=25, a_ego=0, tv=24: fixed max_d≈45m vs buggy ≈4m at a 40m waypoint.
    waypoint_lon_deg = (40.0 / R) * (180.0 / math.pi)
    self.mem_params.put("MapTargetVelocities",
                        json.dumps([{"latitude": 0.0, "longitude": waypoint_lon_deg, "velocity": 24.0}]), block=True)

    self.scc_m.update(self.sm, True, False, 25.0, 0.0, 30.0)

    self.assertAlmostEqual(self.scc_m.v_target, 24.0, delta=24.0 * 1e-6)

  # TODO-SP: mock data from modelV2 to test other states


class TestNavTurnInstance(OpenpilotTestCase):
  """The route-turn controller is the map controller on its own toggle, param and floor."""

  def setup_method(self):
    self.params = Params()
    self.mem_params = Params("/dev/shm/params") if platform.system() != "Darwin" else self.params
    self.params.put_bool("NavTurnSlowdown", True, block=True)
    self.params.put_bool("SmartCruiseControlMap", False, block=True)
    self.nav_turn = SmartCruiseControlMap("NavTurnSlowdown", "NavTargetVelocities", NAV_TURN_MIN_V, needs_mapd=False)
    self.sm = MagicMock()
    self.sm.__getitem__.return_value = SimpleNamespace(latitude=0.0, longitude=0.0)

  def test_floor_is_its_own(self):
    waypoint_lon_deg = (20.0 / R) * (180.0 / math.pi)
    self.mem_params.put("NavTargetVelocities",
                        json.dumps([{"latitude": 0.0, "longitude": waypoint_lon_deg, "velocity": 2.0}]), block=True)

    for _ in range(2):
      self.nav_turn.update(self.sm, True, False, 10.0, 0.0, 15.0)

    assert self.nav_turn.state == MapState.turning
    assert self.nav_turn.output_v_target == NAV_TURN_MIN_V

  def test_cleared_param_releases(self):
    self.test_floor_is_its_own()
    self.mem_params.remove("NavTargetVelocities")
    self.nav_turn.update(self.sm, True, False, 10.0, 0.0, 15.0)
    assert self.nav_turn.state == MapState.enabled
    assert self.nav_turn.output_v_target == V_CRUISE_UNSET


class TestNavTurnFloorFollowsMinSteerSpeed(OpenpilotTestCase):
  def test_floor(self):
    from opendbc.car import structs
    from openpilot.sunnypilot.selfdrive.controls.lib.smart_cruise_control.smart_cruise_control import SmartCruiseControl
    CP = structs.CarParams()
    assert SmartCruiseControl(CP).nav_turn.min_v == NAV_TURN_MIN_V
    CP.minSteerSpeed = 7.0
    assert SmartCruiseControl(CP).nav_turn.min_v == 7.0
