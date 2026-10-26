"""
Copyright (c) 2021-, James Vecellio, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

The destination contract over athena: the RPC methods are exercised through the real
JSON-RPC layer (rpc.handle), so the wire shape a phone app sees is what is asserted,
including error responses and the token redline. The state poll and search are
destinationd HTTP endpoints, covered in test_destinationd.py.
"""
import json
import threading
import time

import openpilot.cereal.messaging as messaging
from openpilot.common.params import Params
from openpilot.common.test import Mocker
from openpilot.system.athena.rpc import Dispatcher, handle
from openpilot.sunnypilot.navd import athena_methods
from openpilot.sunnypilot.navd.tests.helpers import NavdTestCase

TOKEN = "pk.secret-sentinel-token"


def rpc_call(method: str, params: dict | None = None) -> dict:
  dispatcher = Dispatcher()
  athena_methods.register(dispatcher)
  raw = {"jsonrpc": "2.0", "method": method, "id": 0}
  if params is not None:
    raw["params"] = params
  return json.loads(handle(json.dumps(raw), dispatcher))


class TestAthenaNavMethods(NavdTestCase):
  def setUp(self):
    super().setUp()
    mocker = Mocker(self.addCleanup)
    self.params = Params()
    self.params.put("MapboxToken", TOKEN, block=True)
    self.params.put("AllowNavigation", True, block=True)
    self.params.put_bool("IsOffroad", True, block=True)
    self.can_set = mocker.patch.object(athena_methods, "_can_set_now", return_value=True)

  def test_athenad_registers_the_destination_contract(self):
    # athenad registers at import
    from openpilot.system.athena.athenad import dispatcher
    for name in ("getNavStatus", "listDestinations", "setDestination", "cancelRoute"):
      assert name in dispatcher, f"{name} missing from the athena dispatcher"
    assert "setNavDestination" in dispatcher, "the upstream method must stay untouched"

  def test_status_shape_matches_the_page(self):
    result = rpc_call("getNavStatus")["result"]
    assert set(result) == {"destination", "navEnabled", "tokenSet", "offroad", "canSet", "device", "favorites", "recents"}
    assert result["destination"] == "" and result["navEnabled"] is True and result["tokenSet"] is True

  def test_set_destination_writes_route_preference_and_recent(self):
    result = rpc_call("setDestination", {"dest": "-119.03,34.22", "name": "Library", "summary": "CA-1"})["result"]
    assert result["destination"] == "-119.03,34.22"
    assert self.params.get("MapboxRoute") == "-119.03,34.22"
    assert self.params.get("MapboxRoutePreference") == {"dest": "-119.03,34.22", "summary": "CA-1"}
    recents = self.params.get("MapboxRecents")
    assert recents and recents[0] == {"name": "Library", "dest": "-119.03,34.22"}

  def test_set_destination_requires_dest(self):
    response = rpc_call("setDestination", {"name": "nowhere"})
    assert response["error"]["message"] == "dest is required"
    assert self.params.get("MapboxRoute") is None

  def test_set_destination_allowed_while_moving(self):
    self.can_set.return_value = False
    result = rpc_call("setDestination", {"dest": "-119.03,34.22"})["result"]
    assert result["destination"] == "-119.03,34.22"
    assert self.params.get("MapboxRoute") == "-119.03,34.22"

  def test_set_destination_refused_when_navigation_disabled(self):
    # the shared handler's refusal, so the relay cannot set a destination navd would never read
    self.params.put("AllowNavigation", False, block=True)
    response = rpc_call("setDestination", {"dest": "-119.03,34.22"})
    assert response["error"]["message"] == "navigation is disabled on the device"
    assert self.params.get("MapboxRoute") is None

  def test_cancel_allowed_while_moving(self):
    rpc_call("setDestination", {"dest": "-119.03,34.22", "summary": "CA-1"})
    self.can_set.return_value = False
    result = rpc_call("cancelRoute")["result"]
    assert result["destination"] == ""
    assert not self.params.get("MapboxRoute")
    assert self.params.get("MapboxRoutePreference") is None

  def test_list_destinations(self):
    rpc_call("setDestination", {"dest": "-119.03,34.22", "name": "Library"})
    self.params.put("MapboxFavorites", {"home": "123 Home St"}, block=True)
    result = rpc_call("listDestinations")["result"]
    assert result["favorites"] == [{"kind": "home", "name": "Home", "dest": "123 Home St"}]
    assert result["recents"] == [{"name": "Library", "dest": "-119.03,34.22"}]

  def test_token_never_crosses_the_tunnel(self):
    # same redline as the page: sweep every method, success and error paths, and require
    # the Mapbox token to be absent from every JSON-RPC response
    self.params.put("MapboxFavorites", {"home": "123 Home St"}, block=True)
    responses = [
      rpc_call("getNavStatus"),
      rpc_call("listDestinations"),
      rpc_call("setDestination", {"dest": "-119.03,34.22"}),
      rpc_call("setDestination", {}),
      rpc_call("cancelRoute"),
    ]
    self.can_set.return_value = False
    responses.append(rpc_call("setDestination", {"dest": "-119.03,34.22"}))
    for response in responses:
      assert TOKEN not in json.dumps(response), f"token leaked in {response}"


class TestCanSetNow(NavdTestCase):
  """The one-shot gate on real sockets: offroad allows, standstill allows, moving and
  silence both deny, matching the page's VehicleMonitor semantics."""

  def _publish_carstate(self, v_ego: float, stop_event: threading.Event) -> None:
    pm = messaging.PubMaster(['carState'])
    while not stop_event.is_set():
      msg = messaging.new_message('carState')
      msg.valid = True
      msg.carState.vEgo = v_ego
      pm.send('carState', msg)
      time.sleep(0.05)

  def _can_set_with_publisher(self, v_ego: float) -> bool:
    stop = threading.Event()
    thread = threading.Thread(target=self._publish_carstate, args=(v_ego, stop), daemon=True)
    thread.start()
    try:
      time.sleep(0.1)  # let the publisher bind before the one-shot subscriber connects
      return athena_methods._can_set_now()
    finally:
      stop.set()
      thread.join(timeout=2)

  def test_offroad_allows_without_carstate(self):
    Params().put_bool("IsOffroad", True, block=True)
    assert athena_methods._can_set_now() is True

  def test_standstill_allows(self):
    Params().put_bool("IsOffroad", False, block=True)
    assert self._can_set_with_publisher(0.0) is True

  def test_moving_denies(self):
    Params().put_bool("IsOffroad", False, block=True)
    assert self._can_set_with_publisher(5.0) is False

  def test_silence_denies(self):
    # onroad with no carState at all is the stale-data case: conservative deny
    Params().put_bool("IsOffroad", False, block=True)
    assert athena_methods._can_set_now() is False
