"""
Copyright (c) 2021-, James Vecellio, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import openpilot.cereal.messaging as messaging
from opendbc.car.structs import car
from openpilot.cereal import log
from openpilot.common.constants import CV
from openpilot.common.params import Params
from openpilot.sunnypilot.navd.constants import NAV_LANE_CHANGE_OFF


class NavigationDesires:
  def __init__(self):
    self.sm = messaging.SubMaster(['navigationd'])
    self.desire = log.Desire.none
    self._turn_speed_limit = 20 * CV.MPH_TO_MS
    self._params = Params()
    self.param_counter = -1
    self.nav_allowed: bool = False
    self.lane_assist: bool = False

  def update_params(self):
    self.param_counter += 1
    if self.param_counter % 60 == 0:  # every 3 seconds at 20hz
      self.nav_allowed = self._params.get("NavDesiresAllowed", return_default=True)
      self.lane_assist = self._params.get("NavLaneChangeTimer", return_default=True) != NAV_LANE_CHANGE_OFF

  # second half of the double gate: navigationd only publishes a direction while the lane
  # change timer is set, and the param re-check keeps a stale message from confirming after
  # it is switched off.
  # autoConfirm is navigationd's judgment that the adjacent lane runs our way
  def lane_change_hint(self) -> str:
    nav_msg = self.sm['navigationd']
    if self.lane_assist and nav_msg.valid and nav_msg.laneChangeAutoConfirm:
      return str(nav_msg.laneChangeDirection)
    return 'none'

  def update(self, CS: car.CarState, lateral_active: bool) -> log.Desire:
    self.update_params()
    self.sm.update(0)
    nav_msg = self.sm['navigationd']
    self.desire = log.Desire.none
    if self.nav_allowed and nav_msg.valid and lateral_active:
      upcoming = nav_msg.upcomingTurn
      if upcoming == 'slightLeft' and not CS.rightBlinker and not CS.leftBlindspot and CS.steeringPressed and CS.steeringTorque > 0:
        self.desire = log.Desire.keepLeft
      elif upcoming == 'slightRight' and not CS.leftBlinker and not CS.rightBlindspot and CS.steeringPressed and CS.steeringTorque < 0:
        self.desire = log.Desire.keepRight
      # the driver's matching blinker is required: without it the car would start the turn on its own
      elif upcoming == 'left' and CS.leftBlinker and not CS.rightBlinker and not CS.leftBlindspot and CS.vEgo < self._turn_speed_limit:
        self.desire = log.Desire.turnLeft
      elif upcoming == 'right' and CS.rightBlinker and not CS.leftBlinker and not CS.rightBlindspot and CS.vEgo < self._turn_speed_limit:
        self.desire = log.Desire.turnRight
    return self.desire
