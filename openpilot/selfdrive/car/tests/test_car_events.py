from opendbc.car import DT_CTRL
from opendbc.car.structs import car
from opendbc.car.subaru.values import CAR
from openpilot.cereal import log
from openpilot.selfdrive.car.car_events import CarEvents, SUBARU_CUT_FADE_FRAMES, SUBARU_CUT_ANGLE_DEG, SUBARU_CUT_HOLD_TORQUE
from openpilot.common.test import OpenpilotTestCase

EventName = log.OnroadEvent.EventName
STEER_EVENTS = (EventName.steerTempUnavailable, EventName.steerTempUnavailableSilent)
UNPRESSED_FRAMES = int(1.5 / DT_CTRL)
EMPTY_WHEEL_TORQUE = 20.
LIGHT_HAND_TORQUE = SUBARU_CUT_HOLD_TORQUE + 1.


def car_state(**fields):
  return car.CarState.new_message(**{'vEgo': 5., 'steeringAngleDeg': SUBARU_CUT_ANGLE_DEG + 10., 'steeringTorque': EMPTY_WHEEL_TORQUE, **fields})


class TestSubaruHighAngleCut(OpenpilotTestCase):
  def setup_method(self):
    self.car_events = CarEvents(car.CarParams.new_message(brand='subaru', carFingerprint=CAR.SUBARU_FORESTER))
    self.CS_prev = car_state()

  def _drive(self, CS, frames):
    seen = set()
    for _ in range(frames):
      names = self.car_events.update(CS, self.CS_prev, car.CarControl.new_message()).names
      seen |= {n for n in names if n in STEER_EVENTS}
      self.CS_prev = CS
    return seen

  def test_empty_wheel_chimes_after_the_fade(self):
    self._drive(car_state(), UNPRESSED_FRAMES)
    assert self._drive(car_state(steerFaultTemporary=True), SUBARU_CUT_FADE_FRAMES) == set()
    assert self._drive(car_state(steerFaultTemporary=True), 1) == {EventName.steerTempUnavailableSilent}

  def test_light_hand_on_the_wheel_stays_quiet(self):
    self._drive(car_state(), UNPRESSED_FRAMES)
    assert self._drive(car_state(steerFaultTemporary=True, steeringTorque=LIGHT_HAND_TORQUE), 5 * SUBARU_CUT_FADE_FRAMES) == set()

  def test_hand_released_mid_cut_gets_the_stock_grace(self):
    self._drive(car_state(steerFaultTemporary=True, steeringTorque=LIGHT_HAND_TORQUE), 5 * SUBARU_CUT_FADE_FRAMES)
    assert self._drive(car_state(steerFaultTemporary=True), UNPRESSED_FRAMES - 1) == set()
    assert self._drive(car_state(steerFaultTemporary=True), 1) == {EventName.steerTempUnavailableSilent}

  def test_standstill_stays_quiet(self):
    self._drive(car_state(), UNPRESSED_FRAMES)
    assert self._drive(car_state(steerFaultTemporary=True, standstill=True), 5 * SUBARU_CUT_FADE_FRAMES) == set()

  def test_fault_outside_the_envelope_keeps_the_stock_alert(self):
    self._drive(car_state(vEgo=20., steeringAngleDeg=10.), UNPRESSED_FRAMES)
    assert self._drive(car_state(vEgo=20., steeringAngleDeg=10., steerFaultTemporary=True), 1) == {EventName.steerTempUnavailable}
