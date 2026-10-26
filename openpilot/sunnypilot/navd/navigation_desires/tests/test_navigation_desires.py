"""
Copyright (c) 2021-, James Vecellio, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import types

from openpilot.cereal import log
from openpilot.common.params import Params
from openpilot.common.test import Mocker

from openpilot.selfdrive.controls.lib.desire_helper import DesireHelper
from openpilot.sunnypilot.navd.navigation_desires.navigation_desires import NavigationDesires
from openpilot.sunnypilot.navd.tests.helpers import NavdTestCase


def make_car(vEgo=0, leftBlinker=False, rightBlinker=False, leftBlindspot=False, rightBlindspot=False, steeringPressed=False, steeringTorque=0):
  return types.SimpleNamespace(
    vEgo=vEgo, leftBlinker=leftBlinker, rightBlinker=rightBlinker,
    leftBlindspot=leftBlindspot, rightBlindspot=rightBlindspot,
    steeringPressed=steeringPressed, steeringTorque=steeringTorque
  )

NAVIGATION_PARAMS: list[tuple] = [
  ('slightLeft', make_car(steeringPressed=True, steeringTorque=1), log.Desire.keepLeft),
  ('slightRight', make_car(steeringPressed=True, steeringTorque=-1), log.Desire.keepRight),
  ('slightLeft', make_car(vEgo=9, leftBlindspot=True), log.Desire.none),
  ('slightRight', make_car(vEgo=9, rightBlindspot=True), log.Desire.none),
  ('left', make_car(vEgo=5, leftBlinker=True, rightBlinker=False, leftBlindspot=False), log.Desire.turnLeft),
  ('left', make_car(vEgo=5, leftBlinker=False, rightBlinker=True), log.Desire.none),
  ('right', make_car(vEgo=6, rightBlinker=True, leftBlindspot=False), log.Desire.turnRight),
  ('right', make_car(vEgo=6, rightBlinker=True, rightBlindspot=True), log.Desire.none),
  ('left', make_car(vEgo=9, leftBlinker=True), log.Desire.none),
]

INTEGRATION_PARAMS: list[tuple] = [(carstate, upcoming, log.Desire.none, expected) for upcoming, carstate, expected in NAVIGATION_PARAMS] + [
  (make_car(vEgo=6, leftBlinker=True, steeringPressed=True, steeringTorque=1), 'slightLeft', log.Desire.turnLeft, log.Desire.keepLeft),
  (make_car(vEgo=5, rightBlinker=True, steeringPressed=True, steeringTorque=-1), 'slightRight', log.Desire.turnRight, log.Desire.keepRight),
  (make_car(vEgo=9, leftBlinker=True), 'slightLeft', log.Desire.laneChangeLeft, log.Desire.laneChangeLeft),
  (make_car(vEgo=9, rightBlinker=True), 'slightRight', log.Desire.laneChangeRight, log.Desire.laneChangeRight),
  (make_car(vEgo=9), 'none', log.Desire.none, log.Desire.none),
]

def make_nav_msg(valid=False, upcoming='none'):
  return types.SimpleNamespace(valid=valid, upcomingTurn=upcoming)

def params_setter(allowed: bool):
  params = Params()
  params.put("NavDesiresAllowed", allowed, block=True)


class TestNavigationDesires(NavdTestCase):
  def setUp(self):
    super().setUp()
    self.mocker = Mocker(self.addCleanup)
    mock_sm = self.mocker.patch('openpilot.cereal.messaging.SubMaster')
    self.sm = self.mocker.MagicMock()
    mock_sm.return_value = self.sm
    self.sm.__getitem__.return_value = make_nav_msg(valid=False)
    params_setter(True)

  def test_navigation_desires_update(self):
    for upcoming, carstate, expected in NAVIGATION_PARAMS:
      with self.subTest(upcoming=upcoming, carstate=carstate, expected=expected):
        nav_desires = NavigationDesires()
        self.sm.__getitem__.return_value = make_nav_msg(valid=True, upcoming=upcoming)
        nav_desires.update(carstate, True)
        self.assertEqual(nav_desires.desire, expected)

  def test_invalid_or_inactive(self):
    for msg_valid, lateral_active in [(False, True), (True, False)]:
      with self.subTest(msg_valid=msg_valid, lateral_active=lateral_active):
        nav_desires = NavigationDesires()
        self.sm.__getitem__.return_value = make_nav_msg(valid=msg_valid, upcoming='slightLeft')
        nav_desires.update(make_car(), lateral_active)
        self.assertEqual(nav_desires.desire, log.Desire.none)

  def test_update(self):
    self.sm.__getitem__.return_value = make_nav_msg(valid=True, upcoming='left')
    nav_desires = NavigationDesires()

    self.assertEqual(nav_desires.update(make_car(leftBlinker=True, steeringPressed=True, steeringTorque=1), True), log.Desire.turnLeft)

    params_setter(False)
    nav_desires.param_counter = 59
    nav_desires.update(make_car(leftBlinker=True), True)
    self.assertEqual(nav_desires.desire, log.Desire.none)

  def test_desire_helper(self):
    for carstate, upcoming, current_desire, expected in INTEGRATION_PARAMS:
      with self.subTest(carstate=carstate, upcoming=upcoming, current_desire=current_desire, expected=expected):
        self.sm.__getitem__.return_value = make_nav_msg(valid=True, upcoming=upcoming)
        dh = DesireHelper()
        dh.desire = current_desire

        if current_desire in (log.Desire.laneChangeLeft, log.Desire.laneChangeRight):
          dh.lane_change_state = log.LaneChangeState.laneChangeStarting
          dh.lane_change_direction = log.LaneChangeDirection.left if current_desire == log.Desire.laneChangeLeft else log.LaneChangeDirection.right

        dh.update(carstate, True, 1.0)
        self.assertEqual(dh.desire, expected)
