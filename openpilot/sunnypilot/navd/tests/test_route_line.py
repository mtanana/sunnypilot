"""
Copyright (c) 2021-, James Vecellio, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from openpilot.common.params import Params
from openpilot.common.test import OpenpilotTestCase
from openpilot.sunnypilot.navd.helpers import Coordinate, project_onto_geometry
from openpilot.sunnypilot.navd.navigation_helpers.route import Route
from openpilot.sunnypilot.navd.navigation_helpers.route_line import DECIMATION_TOLERANCE_M, overview_points, route_id, route_line_snapshot

M_PER_DEG_LAT = 111319.5


def _point(lat: float, lon: float) -> dict:
  return {'latitude': lat, 'longitude': lon}


def _route(geometry: list[dict]) -> dict:
  # only geometry matters to the route line; the rest mirrors generate_route's shape
  step = {'maneuver': 'depart', 'instruction': '', 'distance': 0.0, 'duration': 0.0, 'modifier': 'straight',
          'location': geometry[0], 'bannerInstructions': []}
  return {'geometry': geometry, 'steps': [step], 'totalDistance': 0.0, 'totalDuration': 0.0, 'maxspeed': []}


def _put_route(params: Params, geometry: list[dict]) -> None:
  # block: the default put is fire-and-forget and the very next line reads the param back
  params.put('MapboxSettings', {'navData': {'current': geometry[0], 'route': _route(geometry)}}, block=True)


class TestOverviewPoints(OpenpilotTestCase):
  def test_geojson_becomes_decimated_lat_lon(self):
    # a 1 m kink between two points on a straight north-south line drops out at the 10 m tolerance
    kink = 1.0 / M_PER_DEG_LAT
    coordinates = [[-119.0, 34.2], [-119.0 + kink, 34.25], [-119.0, 34.3]]
    assert overview_points(coordinates) == [[34.2, -119.0], [34.3, -119.0]]
    assert overview_points([[-119.0, 34.2], [-119.1, 34.3]]) == [[34.2, -119.0], [34.3, -119.1]]
    assert overview_points([]) == []


class TestRouteId(OpenpilotTestCase):
  def test_no_route_is_zero(self):
    assert route_id(None) == 0
    assert route_id({'geometry': []}) == 0

  def test_same_content_same_id_and_a_moved_point_moves_it(self):
    geometry = [_point(34.2, -119.0), _point(34.3, -119.1)]
    assert route_id(_route(geometry)) == route_id(_route(list(geometry)))
    moved = [geometry[0], _point(34.3, -119.2)]
    assert route_id(_route(moved)) != route_id(_route(geometry))
    assert route_id(_route(geometry)) != 0


class TestRouteLineSnapshot(OpenpilotTestCase):
  def test_no_route(self):
    assert route_line_snapshot(Params()) == {"routeId": 0, "points": [], "totalDistance": 0.0, "steps": []}

  def test_steps_sit_where_navigationd_places_them(self):
    # a client lists the turns ahead as the steps past (totalDistance - distanceRemaining),
    # which only works if each step's along matches the cumulative distance navigationd
    # counts the state's maneuver distances from
    params = Params()
    geometry = [_point(34.2 + i * 0.001, -119.0) for i in range(11)]
    route = _route(geometry)
    route['steps'].append({'maneuver': 'turn', 'instruction': 'Turn right onto Elm St', 'distance': 500.0, 'duration': 40.0,
                           'modifier': 'right', 'location': geometry[5], 'bannerInstructions': []})
    route['totalDistance'] = 1113.0
    params.put('MapboxSettings', {'navData': {'current': geometry[0], 'route': route}}, block=True)

    snap = route_line_snapshot(params)
    loaded = Route.from_mapbox(route)
    assert [s["along"] for s in snap["steps"]] == [step.cumulative_distance for step in loaded.steps]
    assert snap["steps"][1] == {"along": loaded.steps[1].cumulative_distance, "type": "turn", "modifier": "right",
                                "instruction": "Turn right onto Elm St"}
    assert snap["totalDistance"] == 1113.0
    progress = loaded.progress(Coordinate(34.2025, -119.0))
    along = snap["totalDistance"] - progress.distance_remaining
    ahead = [s for s in snap["steps"] if s["along"] > along]
    assert len(ahead) == 1
    self.assertAlmostEqual(ahead[0]["along"] - along, progress.all_maneuvers[1].distance, delta=1e-3)

  def test_straight_line_collapses_and_a_corner_survives(self):
    params = Params()
    # north up the meridian with vertices every ~111m, then a hard right turn east
    corner = _point(34.21, -119.0)
    north = [_point(34.2 + i * 0.001, -119.0) for i in range(10)] + [corner]
    east = [_point(34.21, -119.0 + i * 0.001) for i in range(1, 11)]
    geometry = north + east
    _put_route(params, geometry)

    snap = route_line_snapshot(params)
    assert snap["points"][0] == [geometry[0]['latitude'], geometry[0]['longitude']]
    assert snap["points"][-1] == [geometry[-1]['latitude'], geometry[-1]['longitude']]
    assert [corner['latitude'], corner['longitude']] in snap["points"]
    assert len(snap["points"]) < 6  # the collinear vertices are gone

  def test_dropped_vertices_stay_within_tolerance_of_the_kept_line(self):
    params = Params()
    # a wiggly diagonal: 3m of lateral jitter should vanish, 30m kinks must survive
    geometry = []
    for i in range(60):
      jitter_m = 3.0 if i % 2 else -3.0
      if i % 15 == 7:
        jitter_m = 30.0
      geometry.append(_point(34.2 + i * 0.0005, -119.0 + jitter_m / M_PER_DEG_LAT))
    _put_route(params, geometry)

    snap = route_line_snapshot(params)
    assert 2 < len(snap["points"]) < len(geometry)

    kept = [Coordinate(lat, lon) for lat, lon in snap["points"]]
    cumulative = [0.0]
    cumulative.extend(cumulative[-1] + kept[i - 1].distance_to(kept[i]) for i in range(1, len(kept)))
    for point in geometry:
      crosstrack, _, _ = project_onto_geometry(kept, cumulative, Coordinate(point['latitude'], point['longitude']))
      assert crosstrack <= DECIMATION_TOLERANCE_M + 0.5

  def test_id_matches_the_route_navigationd_loads(self):
    # the invariant the poll relies on: the id navigationd publishes for its loaded route
    # names the same polyline /api/route serves
    params = Params()
    _put_route(params, [_point(34.2, -119.0), _point(34.25, -119.05), _point(34.3, -119.0)])
    loaded = Route.from_mapbox(params.get('MapboxSettings')['navData']['route'])
    assert loaded.route_id == route_line_snapshot(params)["routeId"] != 0
