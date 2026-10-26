"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from openpilot.sunnypilot.navd.helpers import Coordinate, project_onto_geometry
from openpilot.sunnypilot.navd.navigation_helpers.route import Route, RouteProgress, Step, upcoming_turn
from openpilot.sunnypilot.navd.tests.helpers import NavdTestCase

LAT, LON = 32.7767, -96.797


class TestProjectOntoGeometry(NavdTestCase):
  def setup_method(self):
    # one ~700m segment, the vertex spacing Mapbox uses on straight interstate
    self.a, self.b = Coordinate(LAT, LON), Coordinate(LAT + 0.0063, LON)
    self.cumulative = [0.0, self.a.distance_to(self.b)]

  def test_car_between_sparse_vertices_is_on_route(self):
    # the nearest vertex is ~350m away here; the crosstrack distance must not see it
    distance, idx, along = project_onto_geometry([self.a, self.b], self.cumulative, Coordinate(LAT + 0.00315, LON))
    assert distance < 1.0
    assert idx == 0
    assert abs(along - self.cumulative[1] / 2) < 1.0

  def test_crosstrack_offset_is_measured(self):
    east_30m = 30.0 / (111320.0 * 0.84)  # ~30m of longitude at this latitude
    distance, _, along = project_onto_geometry([self.a, self.b], self.cumulative, Coordinate(LAT + 0.00315, LON + east_30m))
    assert 25.0 < distance < 35.0
    assert abs(along - self.cumulative[1] / 2) < 1.0

  def test_position_before_the_start_clamps(self):
    pos = Coordinate(LAT - 0.001, LON)
    distance, idx, along = project_onto_geometry([self.a, self.b], self.cumulative, pos)
    assert abs(distance - self.a.distance_to(pos)) < 1.0
    assert idx == 0
    assert along == 0.0


HERE = Coordinate(LAT, LON)


def _step(maneuver_type: str, modifier: str, location: Coordinate = HERE) -> Step:
  return Step(maneuver_type, modifier, '', 100.0, 10.0, location, 0.0, (0, 'kmh'), [])


def _progress(maneuver_type: str, modifier: str, location: Coordinate = HERE) -> RouteProgress:
  # next turn at the current position by default, so the distance gate passes
  step = _step(maneuver_type, modifier, location)
  return RouteProgress(0.0, 0, 0, step, step, 0.0, 0.0, 0.0, [])


class TestUpcomingTurn(NavdTestCase):
  def test_plain_turn_publishes_modifier(self):
    assert upcoming_turn(_progress('turn', 'right'), HERE, 0.0) == 'right'

  def test_uturn_survives(self):
    assert upcoming_turn(_progress('turn', 'uturn'), HERE, 0.0) == 'uturn'

  def test_roundabout_overrides_modifier(self):
    # the modifier is only the exit heading, so it must not read as an ordinary turn
    for maneuver_type in ('roundabout', 'rotary', 'roundabout turn', 'exit roundabout', 'exit rotary'):
      assert upcoming_turn(_progress(maneuver_type, 'slightRight'), HERE, 0.0) == 'roundabout'

  def test_far_turn_stays_hidden(self):
    assert upcoming_turn(_progress('turn', 'right', Coordinate(LAT + 0.1, LON)), HERE, 0.0) == 'none'


class TestRouteRemaining(NavdTestCase):
  def setup_method(self):
    # straight north-south route: 11 points, two 60-second driving steps plus the arrive step
    geometry = [Coordinate(LAT + 0.001 * i, LON) for i in range(11)]
    cumulative = [0.0]
    for i in range(1, len(geometry)):
      cumulative.append(cumulative[-1] + geometry[i - 1].distance_to(geometry[i]))
    self.total = cumulative[-1]
    self.halfway = cumulative[5]

    def step(idx, distance, duration, maneuver):
      return Step(maneuver, 'none', '', distance, duration, geometry[idx], cumulative[idx], (0, 'kmh'), [])

    self.route = Route(
      route_id=1,
      geometry=geometry,
      cumulative_distances=cumulative,
      bearings=[],
      steps=[step(0, self.halfway, 60.0, 'depart'),
             step(5, self.total - self.halfway, 60.0, 'turn'),
             step(10, 0.0, 0.0, 'arrive')],
      total_distance=self.total,
      total_duration=120.0,
    )

  def test_at_start(self):
    progress = self.route.progress(Coordinate(LAT, LON))
    assert abs(progress.distance_remaining - self.total) < 1.0
    assert abs(progress.time_remaining - 120.0) < 1.0

  def test_halfway(self):
    progress = self.route.progress(Coordinate(LAT + 0.005, LON))
    assert abs(progress.distance_remaining - (self.total - self.halfway)) < 1.0
    assert abs(progress.time_remaining - 60.0) < 1.0

  def test_at_destination(self):
    progress = self.route.progress(Coordinate(LAT + 0.010, LON))
    assert progress.distance_remaining < 1.0
    assert progress.time_remaining < 1.0
