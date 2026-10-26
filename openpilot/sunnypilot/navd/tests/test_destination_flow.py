"""
Copyright (c) 2021-, James Vecellio, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import platform
import time
from concurrent.futures import Future

from openpilot.common.params import Params
from openpilot.common.test import Mocker
from openpilot.sunnypilot.navd import navigationd as navigationd_module
from openpilot.sunnypilot.navd.helpers import Coordinate
from openpilot.sunnypilot.navd.navigationd import Navigationd
from openpilot.sunnypilot.navd.tests.helpers import NavdTestCase

DESTINATION = "740 E Ventura Blvd"
RESOLVED = "740 E Ventura Blvd, Camarillo, California 93010"
ROUTE = {
  'steps': [{'maneuver': 'depart', 'instruction': '', 'distance': 100.0, 'duration': 10.0, 'modifier': 'straight',
             'location': {'latitude': 34.233, 'longitude': -119.175}, 'bannerInstructions': []}],
  'totalDistance': 100.0, 'totalDuration': 10.0,
  'geometry': [{'latitude': 34.233, 'longitude': -119.175}, {'latitude': 34.234, 'longitude': -119.175}],
  'maxspeed': [],
}


# recents are recorded when a route is accepted and the route preference dies with the trip;
# both behaviors live in navigationd so every destination source shares them
class TestDestinationFlow(NavdTestCase):
  is_darwin = platform.system() == "Darwin"

  def setUp(self):
    super().setUp()
    mocker = Mocker(self.addCleanup)
    if self.is_darwin:
      mocker.patch('openpilot.cereal.messaging.SubMaster')
      mocker.patch('openpilot.cereal.messaging.PubMaster')

    self.params = Params()
    self.params.put("MapboxRoute", DESTINATION, block=True)

    self.now = 1000.0
    mocker.patch.object(navigationd_module, 'monotonic', lambda: self.now)

    self.nav = Navigationd()
    self.nav.last_position = Coordinate(latitude=34.23305, longitude=-119.17557)

    self.route_ready = True

    def fake_set_destination(destination, *args, **kwargs):
      destination.update({'resolved_name': RESOLVED})
      return destination, ROUTE if self.route_ready else None

    mocker.patch.object(self.nav.mapbox, 'set_destination', side_effect=fake_set_destination)

    def inline_submit(fn, *args, **kwargs):
      future = Future()
      future.set_result(fn(*args, **kwargs))
      return future

    mocker.patch.object(self.nav.executor, 'submit', side_effect=inline_submit)

  def run_for(self, seconds: float) -> None:
    for _ in range(int(seconds * 3)):
      self.now += 1 / 3
      self.nav._update_params()

  def test_acceptance_records_a_recent_with_the_resolved_name(self):
    self.run_for(1.0)
    assert self.nav.route is not None
    recents = self.params.get("MapboxRecents")
    assert recents == [{"name": RESOLVED, "dest": DESTINATION}]

  def test_acceptance_keeps_a_label_chosen_by_the_page_or_phone(self):
    self.nav.destination_store.record_recent("Library", DESTINATION)
    self.run_for(1.0)
    recents = self.params.get("MapboxRecents")
    assert recents == [{"name": "Library", "dest": DESTINATION}]

  def test_reacceptance_does_not_duplicate_the_recent(self):
    self.run_for(1.0)
    # re-acceptance of the same destination, as after a navd restart mid-trip
    self.nav.destination = None
    self.run_for(6.0)
    recents = self.params.get("MapboxRecents")
    assert len(recents) == 1

  def test_failed_request_records_nothing(self):
    self.route_ready = False
    self.run_for(1.0)
    assert self.params.get("MapboxRecents") is None

  def test_arrival_clears_the_route_preference(self):
    self.params.put("MapboxRoutePreference", {"dest": DESTINATION, "summary": "CA-1"}, block=True)
    self.run_for(1.0)
    assert self.nav.route is not None
    self.nav.arrival_counter = 30
    self.run_for(1.0)
    # navd clears the destination with a non-blocking put, so give the writer a moment;
    # Params reads the cleared (empty string) param back as None
    for _ in range(20):
      if not self.params.get("MapboxRoute"):
        break
      time.sleep(0.1)
    assert not self.params.get("MapboxRoute")
    assert self.params.get("MapboxRoutePreference") is None

  def test_external_clear_clears_the_route_preference(self):
    self.params.put("MapboxRoutePreference", {"dest": DESTINATION, "summary": "CA-1"}, block=True)
    self.run_for(1.0)
    assert self.nav.route is not None
    self.params.put("MapboxRoute", "", block=True)
    self.run_for(11.0)  # two 5s polls must both read empty before the route drops
    assert self.nav.route is None
    assert self.params.get("MapboxRoutePreference") is None
