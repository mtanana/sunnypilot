"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import openpilot.cereal.messaging as messaging
from opendbc.car import structs
from openpilot.sunnypilot.selfdrive.controls.lib.smart_cruise_control.vision_controller import SmartCruiseControlVision
from openpilot.sunnypilot.selfdrive.controls.lib.smart_cruise_control import NAV_TURN_MIN_V
from openpilot.sunnypilot.selfdrive.controls.lib.smart_cruise_control.map_controller import SmartCruiseControlMap


class SmartCruiseControl:
  def __init__(self, CP: structs.CarParams):
    self.vision = SmartCruiseControlVision()
    self.map = SmartCruiseControlMap()
    # the route's turns, one target per maneuver, written by navigationd; never below the speed
    # the car can still steer at, or the slowdown would hand the corner back to the driver
    self.nav_turn = SmartCruiseControlMap("NavTurnSlowdown", "NavTargetVelocities", max(NAV_TURN_MIN_V, CP.minSteerSpeed),
                                          needs_mapd=False)

  def update(self, sm: messaging.SubMaster, long_enabled: bool, long_override: bool, v_ego: float, a_ego: float, v_cruise: float) -> None:
    self.map.update(sm, long_enabled, long_override, v_ego, a_ego, v_cruise)
    self.nav_turn.update(sm, long_enabled, long_override, v_ego, a_ego, v_cruise)
    self.vision.update(sm, long_enabled, long_override, v_ego, a_ego, v_cruise)
