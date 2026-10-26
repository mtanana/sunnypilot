"""
Copyright (c) 2021-, James Vecellio, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from openpilot.common.test import OpenpilotTestCase
from openpilot.sunnypilot.navd.helpers import Coordinate, bearing_between_two_points
from openpilot.sunnypilot.navd.navigation_helpers.route import Route

ORIGIN = Coordinate(40.1, -88.2)


class TestBearing(OpenpilotTestCase):
  def test_compass_bearings_at_mid_latitude(self):
    # the longitude leg of a diagonal shrinks by cos(lat), so NE is not 45 here
    for dlat, dlon, expected in [(0.01, 0.0, 0.0), (0.0, 0.01, 90.0), (-0.01, 0.0, 180.0), (0.0, -0.01, 270.0), (0.01, 0.01, 37.4)]:
      with self.subTest(dlat=dlat, dlon=dlon):
        bearing = bearing_between_two_points(ORIGIN, Coordinate(ORIGIN.latitude + dlat, ORIGIN.longitude + dlon))
        self.assertAlmostEqual(bearing, expected, delta=0.2)

  def test_driving_along_an_eastbound_route_is_aligned(self):
    geometry = [Coordinate(ORIGIN.latitude, ORIGIN.longitude + 0.001 * i) for i in range(4)]
    route = Route.from_mapbox({
      'steps': [{'maneuver': 'depart', 'instruction': '', 'distance': 300.0, 'duration': 30.0, 'modifier': 'straight',
                 'location': geometry[0].as_dict(), 'bannerInstructions': []}],
      'totalDistance': 300.0, 'totalDuration': 30.0, 'geometry': [c.as_dict() for c in geometry], 'maxspeed': [],
    })
    progress = route.progress(ORIGIN)
    assert not route.bearing_misaligned(progress, 90.0, 20.0)
    assert route.bearing_misaligned(progress, 270.0, 20.0)
