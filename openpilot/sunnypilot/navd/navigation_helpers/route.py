"""
Copyright (c) 2021-, James Vecellio, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from __future__ import annotations

from dataclasses import dataclass

from numpy import interp

from openpilot.sunnypilot.navd.helpers import ROUNDABOUT_TYPES, Coordinate, bearing_between_two_points, project_onto_geometry, string_to_direction
from openpilot.sunnypilot.navd.navigation_helpers.route_line import cumulative_distances as cumulative_distances_along, route_id, step_along

UPCOMING_TURN_SPEED_BP = [0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 35.0, 40.0]  # m/s
UPCOMING_TURN_DIST = [20.0, 25.0, 30.0, 45.0, 60.0, 75.0, 90.0, 105.0, 120.0]  # m
MISALIGN_MIN_SPEED = 5.0  # m/s
MISALIGN_DEG = 110.0
ARRIVAL_MAX_SPEED = 1.0  # m/s
# trips parked short of the arrive step ended 2 to 68 m from the end; the nearest stops at lights
# beside a destination sit 200 m out (arrival scan, roadmap phase 34)
ARRIVAL_RADIUS = 75.0  # m along the route
MAX_MANEUVERS = 3

NO_LIMIT = (0, 'kmh')


@dataclass(frozen=True)
class Step:
  maneuver: str
  modifier: str
  instruction: str
  distance: float
  duration: float
  location: Coordinate
  cumulative_distance: float
  maxspeed: tuple[float, str]
  banner_instructions: list


@dataclass(frozen=True)
class Maneuver:
  distance: float
  type: str
  modifier: str
  instruction: str


@dataclass(frozen=True)
class RouteProgress:
  distance_from_route: float
  closest_idx: int  # segment index: the car is between geometry[i] and geometry[i+1]
  current_step_idx: int
  current_step: Step
  next_turn: Step | None
  distance_to_end_of_step: float
  distance_remaining: float
  time_remaining: float
  all_maneuvers: list[Maneuver]


@dataclass(frozen=True)
class Route:
  route_id: int
  geometry: list[Coordinate]
  cumulative_distances: list[float]
  bearings: list[float]
  steps: list[Step]
  total_distance: float
  total_duration: float

  @classmethod
  def from_mapbox(cls, route: dict | None) -> Route | None:
    """From generate_route's dict, the same bytes destinationd's /api/route serves and hashes."""
    if not route or not route['geometry'] or not route['steps']:
      return None

    geometry = [Coordinate(coord['latitude'], coord['longitude']) for coord in route['geometry']]
    cumulative_distances = cumulative_distances_along(geometry)
    maxspeed = [(item['speed'], item['unit']) if item else NO_LIMIT for item in route['maxspeed']]

    steps = []
    for step in route['steps']:
      location = Coordinate(step['location']['latitude'], step['location']['longitude'])
      closest_idx = min(range(len(geometry)), key=lambda i: location.distance_to(geometry[i]))
      steps.append(Step(
        maneuver=step['maneuver'],
        modifier=string_to_direction(step['modifier']),
        instruction=step['instruction'],
        distance=step['distance'],
        duration=step['duration'],
        location=location,
        cumulative_distance=step_along(geometry, cumulative_distances, location),
        maxspeed=maxspeed[min(closest_idx, len(maxspeed) - 1)] if maxspeed else NO_LIMIT,
        banner_instructions=step['bannerInstructions'],
      ))

    return cls(
      route_id=route_id(route),
      geometry=geometry,
      cumulative_distances=cumulative_distances,
      # bearings[i] spans geometry[i] to geometry[i+2], which covers the segment the car is on
      bearings=[bearing_between_two_points(geometry[i], geometry[i + 2]) for i in range(len(geometry) - 2)],
      steps=steps,
      total_distance=route['totalDistance'],
      total_duration=route['totalDuration'],
    )

  def progress(self, position: Coordinate) -> RouteProgress:
    distance_from_route, closest_idx, along = project_onto_geometry(self.geometry, self.cumulative_distances, position)

    current_step_idx = max((idx for idx, step in enumerate(self.steps) if step.cumulative_distance <= along), default=0)
    current_step = self.steps[current_step_idx]
    next_turn = self.steps[current_step_idx + 1] if current_step_idx + 1 < len(self.steps) else None
    distance_to_end_of_step = max(0.0, current_step.distance - (along - current_step.cumulative_distance))

    # durations reflect traffic at request time, so the estimate drifts between reroutes
    step_fraction = min(1.0, distance_to_end_of_step / current_step.distance) if current_step.distance > 0 else 0.0
    time_remaining = current_step.duration * step_fraction + sum(step.duration for step in self.steps[current_step_idx + 1:])

    all_maneuvers = [
      Maneuver(distance_to_end_of_step if idx == current_step_idx else step.cumulative_distance - along,
               step.maneuver, step.modifier, step.instruction)
      for idx, step in enumerate(self.steps[current_step_idx:current_step_idx + MAX_MANEUVERS], start=current_step_idx)
    ]

    return RouteProgress(
      distance_from_route=distance_from_route,
      closest_idx=closest_idx,
      current_step_idx=current_step_idx,
      current_step=current_step,
      next_turn=next_turn,
      distance_to_end_of_step=distance_to_end_of_step,
      distance_remaining=max(0.0, self.total_distance - along),
      time_remaining=time_remaining,
      all_maneuvers=all_maneuvers,
    )

  def bearing_misaligned(self, progress: RouteProgress, bearing: float | None, v_ego: float) -> bool:
    if bearing is None or v_ego < MISALIGN_MIN_SPEED or progress.closest_idx >= len(self.bearings):
      return False
    difference = abs((bearing + 360) % 360 - self.bearings[progress.closest_idx])
    return min(difference, 360 - difference) > MISALIGN_DEG


def upcoming_turn(progress: RouteProgress, position: Coordinate, v_ego: float) -> str:
  """The next maneuver's modifier once it is within a speed-scaled window, else 'none'."""
  if progress.next_turn is None:
    return 'none'
  if position.distance_to(progress.next_turn.location) > float(interp(v_ego, UPCOMING_TURN_SPEED_BP, UPCOMING_TURN_DIST)):
    return 'none'
  # a roundabout's modifier only describes the exit heading, and publishing it would let
  # steering desires treat the roundabout like an ordinary turn
  if any(t in progress.next_turn.maneuver for t in ROUNDABOUT_TYPES):
    return 'roundabout'
  return progress.next_turn.modifier


def arrived(progress: RouteProgress, v_ego: float) -> bool:
  if v_ego >= ARRIVAL_MAX_SPEED:
    return False
  current = progress.all_maneuvers[0]
  on_arrive_step = current.type == 'arrive' or current.instruction.startswith('Your destination')
  return on_arrive_step or progress.distance_remaining < ARRIVAL_RADIUS
