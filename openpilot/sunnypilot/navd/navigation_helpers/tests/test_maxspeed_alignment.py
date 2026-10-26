"""
Copyright (c) 2021-, James Vecellio, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from openpilot.common.test import OpenpilotTestCase
from openpilot.sunnypilot.navd.navigation_helpers.mapbox_integration import MapboxIntegration
from openpilot.sunnypilot.navd.helpers import Coordinate
from openpilot.sunnypilot.navd.navigation_helpers.route import Route

# five vertices, four segments; the second segment has no posted limit
COORDS = [[-88.2, 40.1], [-88.19, 40.1], [-88.18, 40.1], [-88.17, 40.1], [-88.16, 40.1]]
ANNOTATION = [{'speed': 30, 'unit': 'mph'}, {'unknown': True}, {'speed': 55, 'unit': 'mph'}, {'speed': 25, 'unit': 'mph'}]


def _step(idx, maneuver):
  return {'maneuver': {'type': maneuver, 'instruction': '', 'location': COORDS[idx], 'modifier': 'straight'},
          'distance': 800.0, 'duration': 30.0, 'bannerInstructions': []}


class FakeResponse:
  status_code = 200

  def json(self):
    return {'code': 'Ok', 'routes': [{
      'distance': 3200.0, 'duration': 120.0,
      'geometry': {'coordinates': COORDS},
      'legs': [{'summary': 'W Main St', 'annotation': {'maxspeed': ANNOTATION},
                'steps': [_step(0, 'depart'), _step(2, 'turn'), _step(4, 'arrive')]}],
    }]}


# a fixture by name: the test base resolves a test method's parameters against this module
def route(mocker):
  mocker.patch('openpilot.sunnypilot.navd.navigation_helpers.mapbox_integration.requests.get', return_value=FakeResponse())
  return MapboxIntegration.generate_route(Coordinate(40.1, -88.2), Coordinate(40.1, -88.16), 'pk.test')


class TestMaxspeedAlignment(OpenpilotTestCase):
  def test_unknown_segments_keep_their_slot(self, route):
    assert route['maxspeed'] == [{'speed': 30, 'unit': 'mph'}, None, {'speed': 55, 'unit': 'mph'}, {'speed': 25, 'unit': 'mph'}]

  def test_steps_take_the_limit_of_their_own_segment(self, route):
    steps = Route.from_mapbox(route).steps
    # dropping the unknown slot would hand the turn at vertex 2 the 25 mph of the segment after it
    assert [step.maxspeed for step in steps] == [(30, 'mph'), (55, 'mph'), (25, 'mph')]
