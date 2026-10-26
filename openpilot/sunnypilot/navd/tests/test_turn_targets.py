"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import math

from openpilot.common.test import OpenpilotTestCase
from openpilot.sunnypilot.navd.helpers import Coordinate
from openpilot.sunnypilot.navd.navigation_helpers.route import Route, Step
from openpilot.sunnypilot.navd.navigation_helpers.turn_targets import car_scale, turn_angle, turn_targets

ORIGIN = Coordinate(32.7767, -96.797)
M_PER_DEG_LAT = 111_320.0


def offset(origin: Coordinate, north_m: float, east_m: float) -> Coordinate:
  return Coordinate(origin.latitude + north_m / M_PER_DEG_LAT,
                    origin.longitude + east_m / (M_PER_DEG_LAT * math.cos(math.radians(origin.latitude))))


def leg(start: Coordinate, heading_deg: float, length_m: float, spacing_m: float = 50.0) -> list[Coordinate]:
  """Points every spacing_m along a heading, the start excluded, the end included."""
  count = int(length_m / spacing_m)
  return [offset(start, math.cos(math.radians(heading_deg)) * spacing_m * i, math.sin(math.radians(heading_deg)) * spacing_m * i)
          for i in range(1, count + 1)]


def corner_route(exit_heading_deg: float, corner_maneuver: str = 'turn') -> Route:
  """200 m east, one maneuver at the corner, 200 m out along exit_heading_deg."""
  first = [ORIGIN, *leg(ORIGIN, 90.0, 200.0)]
  geometry = [*first, *leg(first[-1], exit_heading_deg, 200.0)]
  cumulative = [0.0]
  for i in range(1, len(geometry)):
    cumulative.append(cumulative[-1] + geometry[i - 1].distance_to(geometry[i]))
  corner = len(first) - 1

  def step(idx: int, maneuver: str, distance: float) -> Step:
    return Step(maneuver, 'none', '', distance, 10.0, geometry[idx], cumulative[idx], (0, 'kmh'), [])

  return Route(route_id=1, geometry=geometry, cumulative_distances=cumulative, bearings=[],
               steps=[step(0, 'depart', cumulative[corner]), step(corner, corner_maneuver, cumulative[-1] - cumulative[corner]),
                      step(len(geometry) - 1, 'arrive', 0.0)],
               total_distance=cumulative[-1], total_duration=30.0)


class TestTurnTargets(OpenpilotTestCase):
  def test_target_by_angle(self):
    for exit_heading, expected in [(0.0, 6.5), (270.0, 3.0), (320.0, 4.5), (120.0, None)]:
      with self.subTest(exit_heading=exit_heading):
        targets = turn_targets(corner_route(exit_heading))
        if expected is None:
          assert targets == []
        else:
          assert len(targets) == 1
          assert targets[0].velocity == expected
          self.assertAlmostEqual(targets[0].along, 200.0, delta=1.0)
          assert (targets[0].latitude, targets[0].longitude) == (corner_route(exit_heading).steps[1].location.latitude,
                                                                 corner_route(exit_heading).steps[1].location.longitude)

  def test_roundabouts_and_forks_carry_no_target(self):
    for maneuver in ['roundabout', 'exit rotary', 'fork']:
      with self.subTest(maneuver=maneuver):
        assert turn_targets(corner_route(0.0, maneuver)) == []

  def test_car_scale_applies(self):
    self.assertAlmostEqual(turn_targets(corner_route(0.0), car_scale('volkswagen'))[0].velocity, 6.5 * 1.25)
    assert car_scale('subaru') == 1.0

  def test_duplicate_vertex_at_the_corner_keeps_the_angle(self):
    route = corner_route(0.0)
    corner = 4
    geometry = [*route.geometry[:corner + 1], route.geometry[corner], *route.geometry[corner + 1:]]
    self.assertAlmostEqual(turn_angle(geometry, corner), 90.0, delta=0.5)
    self.assertAlmostEqual(turn_angle(geometry, corner + 1), 90.0, delta=0.5)

  def test_route_ends_have_no_angle(self):
    geometry = corner_route(0.0).geometry
    assert turn_angle(geometry, 0) == 0.0
    assert turn_angle(geometry, len(geometry) - 1) == 0.0
