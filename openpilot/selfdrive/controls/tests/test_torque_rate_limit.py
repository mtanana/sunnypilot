from openpilot.common.test import OpenpilotTestCase

from openpilot.cereal import log
from opendbc.car.structs import car
from opendbc.car.car_helpers import interfaces
from opendbc.car.subaru.values import CAR as SUBARU
from opendbc.car.toyota.values import CAR as TOYOTA
from opendbc.car.vehicle_model import VehicleModel
from openpilot.common.realtime import DT_CTRL
from openpilot.selfdrive.car.helpers import convert_to_capnp
from openpilot.selfdrive.controls.lib.latcontrol_torque import LatControlTorque, KP as KP_V1, KP_INTERP, KP_INTERP_SUBARU_LOW
from openpilot.sunnypilot.selfdrive.controls.lib.latcontrol_torque_v0 import LatControlTorque as LatControlTorqueV0, KP as KP_V0
from openpilot.selfdrive.locationd.helpers import Pose
from openpilot.common.mock.generators import generate_deviceMotion
from openpilot.sunnypilot.selfdrive.car import interfaces as sunnypilot_interfaces

# the last gain each controller's table ends on
KP_LAST: dict[type, float] = {LatControlTorque: KP_V1, LatControlTorqueV0: KP_V0}


class TestTorqueRateLimit(OpenpilotTestCase):
  """The Subaru EPS slews at about 1300 counts/s (STEER_MAX 2047); the torque controller caps its
  command to that and freezes the integrator while capped, so a large error cannot be commanded
  into slew saturation. Other brands are unaffected. Both torque controllers carry it, since
  controlsd_ext picks V0 by the TorqueControlTune param."""

  CONTROLLERS = (LatControlTorque, LatControlTorqueV0)

  def _controller(self, car_name, cls=LatControlTorque):
    CarInterface = interfaces[car_name]
    CP = CarInterface.get_non_essential_params(car_name)
    CP_SP = CarInterface.get_non_essential_params_sp(CP, car_name)
    CI = CarInterface(CP, CP_SP)
    sunnypilot_interfaces.setup_interfaces(CI)
    # Subaru defaults to a non-torque tune off-device; force the torque union so the controller builds
    if CP.lateralTuning.which() != 'torque':
      CP.lateralTuning.init('torque')
      CP.lateralTuning.torque.latAccelFactor = 2.5
      CP.lateralTuning.torque.friction = 0.1
    lac = cls(CP.as_reader(), convert_to_capnp(CP_SP).as_reader(), CI, DT_CTRL)
    return lac, VehicleModel(CP)

  def _step(self, lac, VM, desired_curv):
    CS = car.CarState.new_message()
    CS.vEgo = 15.6
    CS.steeringPressed = False
    pose = Pose.from_device_motion(generate_deviceMotion().deviceMotion)
    _out, _, _log = lac.update(True, CS, VM, log.VehicleParameters.new_message(), False, desired_curv, pose, False, 0.15)
    return _out

  def test_subaru_caps_the_rate_and_freezes_the_integrator(self):
    for cls in self.CONTROLLERS:
      lac, VM = self._controller(SUBARU.SUBARU_FORESTER, cls)
      cap = 1300.0 / 2047.0 * DT_CTRL
      prev, engaged = 0.0, False
      for _ in range(40):
        out = self._step(lac, VM, 0.05)  # a step the loop cannot satisfy in one frame
        assert abs(out - prev) <= cap + 1e-9, (cls.__module__, out, prev, cap)
        prev = out
        engaged = engaged or lac.torque_rate_limited
      assert engaged, f"{cls.__module__}: the rate limit never engaged on a hard step"
      assert abs(lac.pid.i) <= abs(out) + 1e-6, f"{cls.__module__}: the integrator wound up past the capped command"

  def test_subaru_resumes_from_zero_after_an_inactive_frame(self):
    # a guard cut hands lateral back with the wheel far from its request; the cap ramps it in
    for cls in self.CONTROLLERS:
      lac, VM = self._controller(SUBARU.SUBARU_FORESTER, cls)
      for _ in range(40):
        self._step(lac, VM, 0.05)
      CS = car.CarState.new_message()
      CS.vEgo = 15.6
      lac.update(False, CS, VM, log.VehicleParameters.new_message(), False, 0.05, Pose.from_device_motion(generate_deviceMotion().deviceMotion), False, 0.15)
      out = self._step(lac, VM, 0.05)
      assert abs(out) <= 1300.0 / 2047.0 * DT_CTRL + 1e-9, (cls.__module__, out)

  def test_subaru_low_speed_gains_in_both_controllers(self):
    for cls in self.CONTROLLERS:
      lac, _ = self._controller(SUBARU.SUBARU_FORESTER, cls)
      speeds, gains = lac.pid._k_p
      assert list(gains) == KP_INTERP_SUBARU_LOW + [KP_LAST[cls]], cls.__module__
      assert speeds[-1] == 30

  def test_other_brands_are_not_capped(self):
    for cls in self.CONTROLLERS:
      lac, VM = self._controller(TOYOTA.TOYOTA_RAV4, cls)
      assert lac.torque_rate_limit == float("inf")
      assert list(lac.pid._k_p[1]) == KP_INTERP[:-1] + [KP_LAST[cls]]
      for _ in range(10):
        self._step(lac, VM, 0.05)
      assert not lac.torque_rate_limited
