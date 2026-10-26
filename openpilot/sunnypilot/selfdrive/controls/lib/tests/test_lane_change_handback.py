"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from opendbc.car.structs import car
from openpilot.common.test import OpenpilotTestCase
from openpilot.selfdrive.controls.lib.desire_helper import DesireHelper, LaneChangeState

ABOVE_LANE_CHANGE_SPEED = 12.


class TestLaneChangeHandback(OpenpilotTestCase):
  def setup_method(self):
    self.DH = DesireHelper()

  def step(self, lateral_active, blinker=True, fault=False, hand=False, frames=2):
    CS = car.CarState.new_message(vEgo=ABOVE_LANE_CHANGE_SPEED, leftBlinker=blinker, steerFaultTemporary=fault,
                                  steeringPressed=hand, steeringTorque=100. if hand else 0.)
    for _ in range(frames):
      self.DH.update(CS, lateral_active, 0.0)
    return self.DH.lane_change_state

  def test_a_blinker_press_arms_and_a_fault_clears(self):
    assert self.step(lateral_active=True) == LaneChangeState.preLaneChange
    assert self.step(lateral_active=False, fault=True) == LaneChangeState.off

  def test_a_handback_with_the_hand_steering_is_not_a_blinker_press(self):
    self.step(lateral_active=True)
    self.step(lateral_active=False, fault=True)
    assert self.step(lateral_active=True, hand=True) == LaneChangeState.off

  def test_a_fresh_blinker_after_the_handback_arms(self):
    self.step(lateral_active=True)
    self.step(lateral_active=False, fault=True)
    self.step(lateral_active=True, hand=True)
    self.step(lateral_active=True, blinker=False)
    assert self.step(lateral_active=True) == LaneChangeState.preLaneChange

  def test_engaging_with_the_blinker_already_on_still_arms(self):
    self.step(lateral_active=False)
    assert self.step(lateral_active=True) == LaneChangeState.preLaneChange
