"""
Copyright (c) 2021-, James Vecellio, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import os
import unittest

from openpilot.common.constants import CV
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.common.test import OpenpilotTestCase
from openpilot.sunnypilot.navd.helpers import Coordinate
from openpilot.sunnypilot.navd.navigation_helpers.mapbox_integration import MapboxIntegration
from openpilot.sunnypilot.navd.navigation_helpers.route import Route, arrived, upcoming_turn


@unittest.skipUnless(os.environ.get('MAPBOX_TOKEN_CI'), "requires a Mapbox token, set MAPBOX_TOKEN_CI to run")
class TestMapbox(OpenpilotTestCase):
  # one live route per class, not per test: the base's prefix is per test, so the class
  # opens its own so the test destination never lands in the real param space
  @classmethod
  def setUpClass(cls):
    super().setUpClass()
    cls.prefix = OpenpilotPrefix()
    cls.prefix.__enter__()
    cls.mapbox = MapboxIntegration()
    cls.mapbox.params.put('MapboxToken', os.environ['MAPBOX_TOKEN_CI'], block=True)

    cls.here = Coordinate(34.23305, -119.17557)
    cls.mapbox.params.put('MapboxRoute', '740 E Ventura Blvd. Camarillo, CA', block=True)
    cls.destination, route_dict = cls.mapbox.set_destination({"place_name": cls.mapbox.params.get('MapboxRoute')}, cls.here)
    route = Route.from_mapbox(route_dict)
    # set_destination hands back a stored route, not just a geocoded address
    assert route is not None
    cls.route = route
    cls.progress = route.progress(cls.here)

  @classmethod
  def tearDownClass(cls):
    cls.prefix.__exit__(None, None, None)
    super().tearDownClass()

  def test_set_destination(self):
    settings = self.mapbox.params.get('MapboxSettings')
    assert settings is not None
    assert settings['navData']['current'] == {'latitude': self.destination["latitude"], 'longitude': self.destination["longitude"]}

  def test_get_route(self):
    assert len(self.route.steps) > 0
    assert len(self.route.geometry) > 0
    assert self.route.total_distance > 0
    assert self.route.total_duration > 0
    assert all(step.modifier for step in self.route.steps)

  def test_upcoming_turn_detection(self):
    assert upcoming_turn(self.progress, self.here, v_ego=40.0) == 'none'

    turn = self.route.steps[1].location
    just_before = Coordinate(turn.latitude - 0.000175, turn.longitude)
    assert upcoming_turn(self.progress, just_before, v_ego=0.0) == self.progress.next_turn.modifier == 'right'

  def test_route_progress_tracking(self):
    assert self.progress.distance_from_route >= 0
    assert self.progress.next_turn is not None
    assert len(self.progress.all_maneuvers) > 0

  def test_speed_limit_handling(self):
    speed_limit_metric = self.progress.current_step.maxspeed[0]
    assert isinstance(speed_limit_metric, int)
    assert isinstance(round(speed_limit_metric * CV.KPH_TO_MPH), int)

  def test_timezone_lookup(self):
    tzid = self.mapbox.get_timezone(self.here, os.environ['MAPBOX_TOKEN_CI'])
    assert tzid == 'America/Los_Angeles'
    assert self.mapbox.params.get('NavDestinationTimezone') == 'America/Los_Angeles'

  def test_arrival_detection(self):
    assert not arrived(self.progress, 2.0)

  def test_search_places(self):
    results = self.mapbox.search_places('Camarillo Public Library', self.here)
    assert results, "search should return at least one candidate"
    assert all('name' in r and 'latitude' in r and 'longitude' in r for r in results)
    assert len(results) <= 5

  def test_preview_routes(self):
    routes = self.mapbox.preview_routes(self.here, Coordinate(self.destination['latitude'], self.destination['longitude']))
    assert routes, "preview should return at least one route"
    for route in routes:
      assert route['distance'] > 0
      assert route['duration'] > 0
      assert route['durationTypical'] > 0
      # the summary identifies a chosen alternate later, so it must not come back empty
      assert isinstance(route['summary'], str) and route['summary']
      # the overview shape starts near here and ends near the destination, as [lat, lon]
      assert len(route['points']) >= 2
      assert abs(route['points'][0][0] - self.here.latitude) < 0.01
      assert abs(route['points'][-1][1] - self.destination['longitude']) < 0.01

  def test_bearing_misalign(self):
    progress = self.route.progress(self.route.steps[1].location)
    route_bearing = self.route.bearings[progress.closest_idx]
    assert self.route.bearing_misaligned(progress, route_bearing + 135.0, 5.0)
    assert not self.route.bearing_misaligned(progress, route_bearing, 5.0)
