"""
Copyright (c) 2021-, James Vecellio, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

The route line for the head unit surface: a content-derived route id, a decimated
polyline and the steps at their distance along the route, all computed from the
MapboxSettings param navigationd routes on, so the id the poll reports, the shape served
here and the turns a client lists cannot disagree. Served by destinationd only; like
position, the route shape rides the LAN or the tailnet, never comma's relay.
"""
import math
import zlib

from openpilot.common.params import Params
from openpilot.sunnypilot.navd.helpers import Coordinate

# meters of crosstrack a dropped vertex may cost; a lane width cuts an interstate polyline tenfold
DECIMATION_TOLERANCE_M = 10.0

METERS_PER_DEGREE = 111319.5  # mean earth radius * pi / 180


def route_id(route: dict | None) -> int:
  """0 with no route, else a hash of the geometry: restarts and separate readers of the
  same param agree, and any reroute moves it."""
  geometry = (route or {}).get('geometry') or []
  if not geometry:
    return 0
  # 6 decimals is ~0.1m, below GPS noise, so formatting can never split one route into two ids
  digest = ";".join(f"{c['latitude']:.6f},{c['longitude']:.6f}" for c in geometry)
  # 0 is reserved for no route; the one crc in four billion that lands there moves off it
  return zlib.crc32(digest.encode()) or 1


def _chord_distance(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
  # distance to the chord through the endpoints, not the clamped segment: decimation
  # measures how far the shape would deviate if the vertex were dropped
  dx, dy = b[0] - a[0], b[1] - a[1]
  chord2 = dx * dx + dy * dy
  if chord2 == 0.0:
    return math.hypot(p[0] - a[0], p[1] - a[1])
  return abs(dx * (a[1] - p[1]) - (a[0] - p[0]) * dy) / math.sqrt(chord2)


def _decimate(points: list[list[float]], tolerance_m: float) -> list[bool]:
  """Douglas-Peucker keep-mask over [lat, lon] points, tolerance in meters.

  Iterative on purpose: a coast-to-coast polyline recursed per vertex would flirt with
  the interpreter's recursion limit.
  """
  # equirectangular projection about the first vertex; over one route the flat-earth
  # error is far below the tolerance, and longitude must be scaled by cos(lat) or
  # east-west deviation outweighs north-south
  k = math.cos(math.radians(points[0][0]))
  projected = [(lon * k, lat) for lat, lon in points]
  tolerance_deg = tolerance_m / METERS_PER_DEGREE

  keep = [False] * len(points)
  keep[0] = keep[-1] = True
  spans = [(0, len(points) - 1)]
  while spans:
    first, last = spans.pop()
    d_max, idx_max = tolerance_deg, 0
    for i in range(first + 1, last):
      d = _chord_distance(projected[i], projected[first], projected[last])
      if d > d_max:
        d_max, idx_max = d, i
    if idx_max:
      keep[idx_max] = True
      spans.append((first, idx_max))
      spans.append((idx_max, last))
  return keep


def overview_points(coordinates: list, tolerance_m: float = DECIMATION_TOLERANCE_M) -> list[list[float]]:
  """A GeoJSON [lon, lat] line as decimated [lat, lon] points, the route line's own shape."""
  points = [[float(c[1]), float(c[0])] for c in coordinates]
  if len(points) > 2:
    keep = _decimate(points, tolerance_m)
    points = [p for p, kept in zip(points, keep, strict=True) if kept]
  return points


def cumulative_distances(geometry: list[Coordinate]) -> list[float]:
  """Meters along the route at each vertex."""
  cumulative = [0.0]
  cumulative.extend(cumulative[-1] + geometry[i - 1].distance_to(geometry[i]) for i in range(1, len(geometry)))
  return cumulative


def step_along(geometry: list[Coordinate], cumulative: list[float], location: Coordinate) -> float:
  """A step's distance along the route: that of the vertex nearest its maneuver point."""
  return cumulative[min(range(len(geometry)), key=lambda i: location.distance_to(geometry[i]))]


def route_line_snapshot(params: Params, tolerance_m: float = DECIMATION_TOLERANCE_M) -> dict:
  value = params.get('MapboxSettings')
  route = value['navData']['route'] if value else None
  rid = route_id(route)
  if rid == 0 or route is None:
    return {"routeId": 0, "points": [], "totalDistance": 0.0, "steps": []}
  points = [[float(c['latitude']), float(c['longitude'])] for c in route['geometry']]
  if len(points) > 2:
    keep = _decimate(points, tolerance_m)
    points = [p for p, kept in zip(points, keep, strict=True) if kept]
  # each step where navigationd places it, so a client subtracting the route's distance
  # remaining lists exactly the turns still ahead, at the distances the state reports
  geometry = [Coordinate(c['latitude'], c['longitude']) for c in route['geometry']]
  cumulative = cumulative_distances(geometry)
  steps = [
    {"along": step_along(geometry, cumulative, Coordinate(s['location']['latitude'], s['location']['longitude'])),
     "type": s['maneuver'], "modifier": s['modifier'], "instruction": s['instruction']}
    for s in route.get('steps') or []
  ]
  return {"routeId": rid, "points": points, "totalDistance": float(route.get('totalDistance') or 0.0), "steps": steps}
