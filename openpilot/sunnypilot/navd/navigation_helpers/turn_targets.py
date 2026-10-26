"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from __future__ import annotations

from dataclasses import dataclass

from openpilot.sunnypilot.navd.helpers import ROUNDABOUT_TYPES, Coordinate, bearing_between_two_points
from openpilot.sunnypilot.navd.navigation_helpers.route import Route

# apex speeds drivers hold by turn angle, from 3,271 logged turns (nav-turn-slowdown-data.md);
# under the first bound the corner speed is the road's, which the vision and OSM controllers own
TURN_ANGLE_DEG = (55.0, 120.0, 160.0)
TURN_SPEED = (6.5, 4.5, 3.0)  # m/s
# the same corners a quarter faster in the logged Tiguan
TURN_SPEED_SCALE = {'volkswagen': 1.25}
MIN_VERTEX_SPACING = 2.0  # m


@dataclass(frozen=True)
class TurnTarget:
  along: float  # m from the route start to the maneuver point
  latitude: float
  longitude: float
  velocity: float  # m/s

  def as_dict(self) -> dict:
    return {'latitude': self.latitude, 'longitude': self.longitude, 'velocity': self.velocity}


def turn_angle(geometry: list[Coordinate], idx: int) -> float:
  """Degrees between the bearing into geometry[idx] and the bearing out of it; 0 at either end."""
  vertex = geometry[idx]
  before = next((g for g in reversed(geometry[:idx]) if g.distance_to(vertex) >= MIN_VERTEX_SPACING), None)
  after = next((g for g in geometry[idx + 1:] if g.distance_to(vertex) >= MIN_VERTEX_SPACING), None)
  if before is None or after is None:
    return 0.0
  difference = abs(bearing_between_two_points(before, vertex) - bearing_between_two_points(vertex, after))
  return min(difference, 360.0 - difference)


def turn_speed(angle: float) -> float | None:
  speed = None
  for bound, bound_speed in zip(TURN_ANGLE_DEG, TURN_SPEED, strict=True):
    if angle >= bound:
      speed = bound_speed
  return speed


def car_scale(brand: str) -> float:
  return TURN_SPEED_SCALE.get(brand, 1.0)


def turn_targets(route: Route, scale: float = 1.0) -> list[TurnTarget]:
  """One target per route step whose geometry bends enough to be a turn, in route order."""
  targets = []
  for step in route.steps:
    # a roundabout's bend is its entry, not the exit the car takes, and a fork is a lane choice
    if any(t in step.maneuver for t in ROUNDABOUT_TYPES) or step.maneuver == 'fork':
      continue
    idx = min(range(len(route.geometry)), key=lambda i: step.location.distance_to(route.geometry[i]))
    speed = turn_speed(turn_angle(route.geometry, idx))
    if speed is not None:
      targets.append(TurnTarget(step.cumulative_distance, step.location.latitude, step.location.longitude, speed * scale))
  return targets
