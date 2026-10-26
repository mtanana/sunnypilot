"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from openpilot.sunnypilot.navd.helpers import Coordinate
from openpilot.sunnypilot.navd.navigation_helpers.route import ARRIVAL_RADIUS, Maneuver, RouteProgress, Step, arrived

STEP = Step('turn', 'right', '', 400.0, 40.0, Coordinate(32.7767, -96.797), 0.0, (0, 'kmh'), [])


def _progress(maneuver_type: str, distance_remaining: float) -> RouteProgress:
  maneuver = Maneuver(distance_remaining, maneuver_type, 'none', '')
  return RouteProgress(0.0, 0, 0, STEP, None, distance_remaining, distance_remaining, 30.0, [maneuver])


def test_stopped_on_the_arrive_step():
  assert arrived(_progress('arrive', 300.0), 0.0)


def test_stopped_short_of_the_arrive_step_inside_the_radius():
  assert arrived(_progress('turn', ARRIVAL_RADIUS - 1.0), 0.0)


def test_stopped_outside_the_radius_keeps_the_route():
  assert not arrived(_progress('turn', ARRIVAL_RADIUS + 1.0), 0.0)


def test_moving_inside_the_radius_keeps_the_route():
  assert not arrived(_progress('turn', 10.0), 5.0)
  assert not arrived(_progress('arrive', 1.0), 5.0)
