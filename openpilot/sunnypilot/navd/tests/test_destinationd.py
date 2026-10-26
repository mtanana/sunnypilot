"""
Copyright (c) 2021-, James Vecellio, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import json
import threading
import time
from types import SimpleNamespace

import requests

import openpilot.cereal.messaging as messaging
from openpilot.common.params import Params
from openpilot.common.test import Mocker
from openpilot.sunnypilot.navd.destinationd import make_server
from openpilot.sunnypilot.navd.tests.helpers import NavdTestCase

TOKEN = "pk.secret-sentinel-token"
PLACES = [{"name": "Camarillo Public Library", "longitude": -119.03, "latitude": 34.22}]
ROUTES = [
  {"summary": "US-101 North", "distance": 12000.0, "duration": 600.0, "durationTypical": 540.0},
  {"summary": "CA-1", "distance": 15000.0, "duration": 900.0, "durationTypical": 900.0},
]


class TestDestinationd(NavdTestCase):
  def setUp(self):
    super().setUp()
    mocker = Mocker(self.addCleanup)
    self.params = Params()
    self.params.put("MapboxToken", TOKEN, block=True)
    self.params.put("AllowNavigation", True, block=True)
    self.params.put("LastGPSPositionLLK", json.dumps({"latitude": 34.23, "longitude": -119.17}), block=True)

    # parked by default; individual tests flip this to exercise the gate
    self.vehicle = SimpleNamespace(offroad=True, can_set=True)
    self.server = make_server(host="127.0.0.1", port=0, params=self.params, vehicle=self.vehicle)
    self.search_results: list | None = PLACES
    self.route_results: list | None = ROUTES
    mocker.patch.object(self.server.api.mapbox, "search_places", side_effect=lambda *a, **kw: self.search_results)
    mocker.patch.object(self.server.api.mapbox, "preview_routes", side_effect=lambda *a, **kw: self.route_results)

    thread = threading.Thread(target=self.server.serve_forever, daemon=True)
    thread.start()
    self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

  def tearDown(self):
    self.server.shutdown()
    self.server.server_close()
    super().tearDown()

  def get(self, path: str) -> requests.Response:
    return requests.get(self.base + path, timeout=5)

  def post(self, path: str, payload: dict) -> requests.Response:
    return requests.post(self.base + path, json=payload, timeout=5)

  def test_serves_the_page(self):
    res = self.get("/")
    assert res.status_code == 200
    assert "sunnypilot navigation" in res.text

  def test_unknown_path_and_wrong_method(self):
    assert self.get("/etc/passwd").status_code == 404
    assert self.get("/api/navigate").status_code == 405

  def test_status_shape(self):
    body = self.get("/api/status").json()
    assert body["destination"] == ""
    assert body["navEnabled"] is True
    assert body["tokenSet"] is True
    assert body["canSet"] is True
    assert body["device"] == self.server.api.device
    assert body["favorites"] == [] and body["recents"] == []

  def test_state_inactive_when_navigationd_is_silent(self):
    res = self.get("/api/state")
    assert res.status_code == 200
    assert res.json() == {"active": False}

  def _publish_navigationd(self, stop_event: threading.Event) -> None:
    pm = messaging.PubMaster(['navigationd'])
    while not stop_event.is_set():
      msg = messaging.new_message('navigationd')
      msg.valid = True
      nav = msg.navigationd
      nav.valid = True
      nav.routeState = 'onRoute'
      nav.distanceRemaining = 3862.0
      nav.timeRemaining = 480.0
      nav.currentSpeedLimit = 72
      nav.bannerInstructions = 'Turn right onto Fair Oaks Ave'
      nav.audioCueKind = 'turn'
      nav.audioCueStage = 'approach'
      nav.audioCueDirection = 'right'
      nav.hasPosition = True
      nav.positionLatitude = 34.226
      nav.positionLongitude = -119.032
      nav.positionBearingDeg = 271.5
      nav.routeId = 3123456789
      maneuvers = nav.init('allManeuvers', 2)
      maneuvers[0].distance = 240.0
      maneuvers[0].type = 'turn'
      maneuvers[0].modifier = 'right'
      maneuvers[0].instruction = 'Turn right onto Fair Oaks Ave'
      maneuvers[1].distance = 1130.0
      maneuvers[1].type = 'turn'
      maneuvers[1].modifier = 'left'
      maneuvers[1].instruction = 'Turn left onto Colorado Blvd'
      lanes = nav.init('lanes', 1)
      lanes[0].directions = ['straight', 'right']
      lanes[0].active = True
      lanes[0].activeDirection = 'right'
      pm.send('navigationd', msg)
      time.sleep(0.05)

  def test_state_serializes_a_live_navigationd_message(self):
    stop = threading.Event()
    thread = threading.Thread(target=self._publish_navigationd, args=(stop,), daemon=True)
    thread.start()
    try:
      time.sleep(0.1)  # let the publisher bind before the one-shot subscriber connects
      result = self.get("/api/state").json()
    finally:
      stop.set()
      thread.join(timeout=2)

    assert result["active"] is True and result["valid"] is True
    assert result["routeState"] == "onRoute"
    assert result["routeFailures"] == 0
    assert result["distanceRemaining"] == 3862.0
    assert result["timeRemaining"] == 480.0
    assert result["currentSpeedLimit"] == 72
    assert result["audioCueStage"] == "approach"
    assert [m["modifier"] for m in result["maneuvers"]] == ["right", "left"]
    assert result["maneuvers"][0] == {"distance": 240.0, "type": "turn", "modifier": "right",
                                      "instruction": "Turn right onto Fair Oaks Ave"}
    assert result["lanes"] == [{"directions": ["straight", "right"], "active": True,
                                "activeDirection": "right"}]
    assert result["routeId"] == 3123456789
    assert result["position"] == {"latitude": 34.226, "longitude": -119.032, "bearingDeg": 271.5}

  def test_route_empty_without_a_route(self):
    self.params.remove("MapboxSettings")
    assert self.get("/api/route").json() == {"routeId": 0, "points": [], "totalDistance": 0.0, "steps": []}

  def test_route_serves_the_stored_polyline(self):
    geometry = [{"latitude": 34.2 + i * 0.001, "longitude": -119.0 + (i % 2) * 0.001} for i in range(5)]
    self.params.put("MapboxSettings", {"navData": {"current": geometry[0], "route": {"geometry": geometry}}}, block=True)
    body = self.get("/api/route").json()
    assert body["routeId"] != 0
    assert body["points"][0] == [geometry[0]["latitude"], geometry[0]["longitude"]]
    assert body["points"][-1] == [geometry[-1]["latitude"], geometry[-1]["longitude"]]

  def test_search(self):
    body = self.get("/api/search?q=library").json()
    assert body["results"] == PLACES
    assert self.get("/api/search?q=").status_code == 400
    self.search_results = None
    assert self.get("/api/search?q=library").status_code == 502

  def test_routes(self):
    body = self.get("/api/routes?lon=-119.03&lat=34.22").json()
    assert body["routes"] == ROUTES
    assert self.get("/api/routes?lon=oops&lat=34").status_code == 400
    self.route_results = None
    assert self.get("/api/routes?lon=-119.03&lat=34.22").status_code == 502

  def test_routes_without_a_position(self):
    self.params.remove("LastGPSPositionLLK")
    assert self.get("/api/routes?lon=-119.03&lat=34.22").status_code == 409

  def test_navigate_writes_route_preference_and_recent(self):
    res = self.post("/api/navigate", {"dest": "-119.03,34.22", "name": "Library", "summary": "CA-1"})
    assert res.status_code == 200
    assert res.json()["destination"] == "-119.03,34.22"
    assert self.params.get("MapboxRoute") == "-119.03,34.22"
    assert self.params.get("MapboxRoutePreference") == {"dest": "-119.03,34.22", "summary": "CA-1"}
    recents = self.params.get("MapboxRecents")
    assert recents and recents[0] == {"name": "Library", "dest": "-119.03,34.22"}

  def test_navigate_requires_dest(self):
    assert self.post("/api/navigate", {"name": "nowhere"}).status_code == 400

  def test_navigate_refused_while_navigation_off(self):
    # the server stays up so the phone can turn navigation back on; a destination navd would never read is refused
    self.params.put("AllowNavigation", False, block=True)
    res = self.post("/api/navigate", {"dest": "-119.03,34.22"})
    assert res.status_code == 409 and res.json()["error"] == "navigation is disabled on the device"
    assert self.params.get("MapboxRoute") is None
    self.params.put("AllowNavigation", True, block=True)

  def test_navigate_allowed_while_moving(self):
    self.vehicle.can_set = False
    assert self.post("/api/navigate", {"dest": "-119.03,34.22"}).status_code == 200
    assert self.params.get("MapboxRoute") == "-119.03,34.22"

  def test_cancel_allowed_while_moving(self):
    self.post("/api/navigate", {"dest": "-119.03,34.22", "summary": "CA-1"})
    self.vehicle.can_set = False
    res = self.post("/api/cancel", {})
    assert res.status_code == 200
    assert res.json()["destination"] == ""
    # Params reads an empty string param back as None
    assert not self.params.get("MapboxRoute")
    assert self.params.get("MapboxRoutePreference") is None

  def test_favorites_round_trip(self):
    res = self.post("/api/favorites", {"action": "set", "kind": "home", "dest": "123 Home St"})
    assert [f["kind"] for f in res.json()["favorites"]] == ["home"]
    res = self.post("/api/favorites", {"action": "set", "name": "Gym", "dest": "-119.1,34.2"})
    assert len(res.json()["favorites"]) == 2
    res = self.post("/api/favorites", {"action": "set", "name": "Cafe", "dest": "-119.2,34.3"})
    assert [f["name"] for f in res.json()["favorites"]] == ["Home", "Gym", "Cafe"]
    res = self.post("/api/favorites", {"action": "reorder", "names": ["Cafe", "Gym"]})
    assert [f["name"] for f in res.json()["favorites"]] == ["Home", "Cafe", "Gym"]
    res = self.post("/api/favorites", {"action": "remove", "name": "Gym"})
    res = self.post("/api/favorites", {"action": "remove", "name": "Cafe"})
    res = self.post("/api/favorites", {"action": "remove", "kind": "home"})
    assert res.json()["favorites"] == []
    assert self.post("/api/favorites", {"action": "set"}).status_code == 400
    assert self.post("/api/favorites", {"action": "reorder"}).status_code == 400

  def test_route_bound_favorite_round_trip(self):
    res = self.post("/api/favorites", {"action": "set", "kind": "work", "dest": "-119.1,34.2", "summary": "US-101 North"})
    assert res.json()["favorites"] == [{"kind": "work", "name": "Work", "dest": "-119.1,34.2", "summary": "US-101 North"}]
    # re-saving with no route picked returns the favorite to fastest-route behavior
    res = self.post("/api/favorites", {"action": "set", "kind": "work", "dest": "-119.1,34.2"})
    assert res.json()["favorites"] == [{"kind": "work", "name": "Work", "dest": "-119.1,34.2"}]

  def test_settings_round_trip(self):
    body = self.get("/api/settings").json()
    assert body == {"device": self.server.api.device, "version": self.server.api.version, "hasLongitudinalControl": None, "allowNavigation": True, "navHudMode": 3,  # noqa: E501
                    "navAudio": False, "navDesiresAllowed": False, "navTurnSlowdown": False, "laneGuidance": False, "navLaneChangeTimer": 0,
                    "recompute": False, "quietGlyph": False, "speedLimitSign": False, "metric": False, "tokenSet": True}
    res = self.post("/api/settings", {"allowNavigation": False, "navHudMode": 1, "navAudio": True, "navDesiresAllowed": True,
                                      "navTurnSlowdown": True, "laneGuidance": True, "navLaneChangeTimer": 3, "recompute": True,
                                      "quietGlyph": True, "speedLimitSign": True})
    assert res.status_code == 200
    body = res.json()
    assert body["allowNavigation"] is False and body["navHudMode"] == 1 and body["navAudio"] is True
    assert body["laneGuidance"] is True and body["navLaneChangeTimer"] == 3
    assert body["navDesiresAllowed"] is True and body["recompute"] is True and body["quietGlyph"] is True
    assert body["speedLimitSign"] is True and body["navTurnSlowdown"] is True
    assert not self.params.get_bool("AllowNavigation")
    assert self.params.get("NavHudMode") == 1
    assert self.params.get_bool("NavigationAudio")
    assert self.params.get_bool("NavDesiresAllowed")
    assert self.params.get_bool("NavTurnSlowdown")
    assert self.params.get_bool("NavLaneGuidance")
    assert self.params.get("NavLaneChangeTimer") == 3
    assert self.params.get_bool("MapboxRecompute")
    assert self.params.get_bool("NavMiciQuietGlyph")
    assert self.params.get_bool("NavSpeedLimitSign")
    # posted settings persist in the shared param space; put the defaults back for the tests behind us
    self.post("/api/settings", {"allowNavigation": True, "navHudMode": 3, "navAudio": False, "navDesiresAllowed": False,
                                "navTurnSlowdown": False, "laneGuidance": False, "navLaneChangeTimer": 0, "recompute": False,
                                "quietGlyph": False, "speedLimitSign": False})

  def test_settings_report_longitudinal_control(self):
    from opendbc.car.structs import car
    self.params.put("CarParamsPersistent", car.CarParams.new_message(openpilotLongitudinalControl=True).to_bytes(), block=True)
    assert self.get("/api/settings").json()["hasLongitudinalControl"] is True
    self.params.put("CarParamsPersistent", car.CarParams.new_message(alphaLongitudinalAvailable=True).to_bytes(), block=True)
    assert self.get("/api/settings").json()["hasLongitudinalControl"] is False
    self.params.remove("CarParamsPersistent")

  def test_settings_reject_bad_values(self):
    assert self.post("/api/settings", {"navHudMode": 7}).status_code == 400
    # bool is an int in Python; True must not slip through as mode 1
    assert self.post("/api/settings", {"navHudMode": True}).status_code == 400
    assert self.post("/api/settings", {"navLaneChangeTimer": 6}).status_code == 400

  def test_settings_refused_while_moving(self):
    # a passenger may cancel or reroute mid-drive, not reconfigure
    self.vehicle.can_set = False
    assert self.post("/api/settings", {"recompute": True}).status_code == 409
    assert not self.params.get_bool("MapboxRecompute")

  def test_token_is_write_only(self):
    res = self.post("/api/settings", {"token": "pk.replacement-token"})
    assert res.json()["tokenSet"] is True
    assert "pk.replacement-token" not in res.text
    assert self.params.get("MapboxToken") == "pk.replacement-token"
    # an empty submit is a no-op, not a wipe
    self.post("/api/settings", {"token": "  "})
    assert self.params.get("MapboxToken") == "pk.replacement-token"

  def test_token_never_reaches_the_browser(self):
    # the whole point of proxying Mapbox through the device: sweep every endpoint, including
    # error paths, and require the token to be absent from every response body
    self.post("/api/favorites", {"action": "set", "kind": "home", "dest": "123 Home St"})
    self.post("/api/navigate", {"dest": "-119.03,34.22", "summary": "CA-1"})
    responses = [
      self.get("/"),
      self.get("/api/status"),
      self.get("/api/state"),
      self.get("/api/route"),
      self.get("/api/search?q=library"),
      self.get("/api/routes?lon=-119.03&lat=34.22"),
      self.post("/api/navigate", {"dest": "-119.03,34.22"}),
      self.post("/api/cancel", {}),
      self.post("/api/favorites", {"action": "set", "name": "Gym", "dest": "x"}),
      self.get("/nope"),
      self.post("/api/favorites", {}),
      self.get("/api/settings"),
      self.post("/api/settings", {"navHudMode": 2}),
      # even posting the token itself must come back as set or not set, never echoed
      self.post("/api/settings", {"token": TOKEN}),
    ]
    for res in responses:
      assert TOKEN not in res.text, f"token leaked in {res.url}"
