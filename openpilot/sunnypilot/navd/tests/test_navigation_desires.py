"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from types import SimpleNamespace
from typing import cast

import openpilot.cereal.messaging as messaging

from openpilot.cereal import log
from openpilot.sunnypilot.navd.navigation_desires.navigation_desires import NavigationDesires
from openpilot.sunnypilot.navd.tests.helpers import NavdTestCase


class _StubSM:
  def __init__(self, msg):
    self.msg = msg

  def update(self, _):
    pass

  def __getitem__(self, _):
    return self.msg


def _carstate(left_blinker=False, right_blinker=False, v_ego=5.0, left_blindspot=False, right_blindspot=False,
              steering_pressed=False, steering_torque=0.0):
  return SimpleNamespace(leftBlinker=left_blinker, rightBlinker=right_blinker, vEgo=v_ego,
                         leftBlindspot=left_blindspot, rightBlindspot=right_blindspot,
                         steeringPressed=steering_pressed, steeringTorque=steering_torque)


def _desires(upcoming: str) -> NavigationDesires:
  desires = NavigationDesires()
  desires.sm = cast(messaging.SubMaster, _StubSM(SimpleNamespace(valid=True, upcomingTurn=upcoming)))
  desires.nav_allowed = True
  desires.param_counter = 0  # keep clear of the periodic param re-read
  return desires


class TestTurnDesires(NavdTestCase):
  def test_no_blinker_means_no_turn(self):
    # the car must never start a turn it was not asked for
    assert _desires('left').update(_carstate(), True) == log.Desire.none
    assert _desires('right').update(_carstate(), True) == log.Desire.none

  def test_the_matching_blinker_starts_the_turn(self):
    assert _desires('left').update(_carstate(left_blinker=True), True) == log.Desire.turnLeft
    assert _desires('right').update(_carstate(right_blinker=True), True) == log.Desire.turnRight

  def test_the_opposite_blinker_does_not(self):
    assert _desires('left').update(_carstate(right_blinker=True), True) == log.Desire.none

  def test_hazards_do_not_count_as_a_signal(self):
    assert _desires('left').update(_carstate(left_blinker=True, right_blinker=True), True) == log.Desire.none

  def test_a_blindspot_blocks_the_turn(self):
    assert _desires('left').update(_carstate(left_blinker=True, left_blindspot=True), True) == log.Desire.none

  def test_speed_limit_blocks_the_turn(self):
    assert _desires('left').update(_carstate(left_blinker=True, v_ego=20.0), True) == log.Desire.none

  def test_slight_turns_still_need_driver_torque(self):
    assert _desires('slightLeft').update(_carstate(), True) == log.Desire.none
    assert _desires('slightLeft').update(_carstate(steering_pressed=True, steering_torque=1.0), True) == log.Desire.keepLeft

  def test_lateral_inactive_publishes_nothing(self):
    assert _desires('left').update(_carstate(left_blinker=True), False) == log.Desire.none
