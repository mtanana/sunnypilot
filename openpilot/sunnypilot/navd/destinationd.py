"""
Copyright (c) 2021-, James Vecellio, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

destinationd: the destination page, served from the device for phones on the same
LAN or Wifi Tethering (http://<device-ip>:5050, tethering default http://192.168.43.1:5050).
The page is deliberately LAN-only; away from the car the same contract rides comma's
athena connection (navd/athena_methods.py), where auth is the comma account JWT.

Search, route choice, favorites, and recents talk to Mapbox from the device with the
public token; the token itself is never sent to the browser. The daemon always runs:
it is where a phone turns navigation on, and a passenger cancels guidance mid-drive.
Setting a destination is refused while navigation is off, and settings only change
offroad or at a standstill. The handlers themselves live in
navigation_helpers/destination_api.py, shared with the athena transport.
"""
import argparse
import json
import threading
from typing import cast
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import openpilot.cereal.messaging as messaging
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog
from openpilot.sunnypilot.navd.navigation_helpers.destination_api import ApiError, DestinationAPI, STANDSTILL_SPEED
from openpilot.sunnypilot.navd.navigation_helpers.nav_state import nav_state_snapshot
from openpilot.sunnypilot.navd.navigation_helpers.route_line import route_line_snapshot

DEFAULT_PORT = 5050
PAGE_PATH = Path(__file__).parent / "assets" / "destination_page.html"


@lru_cache(maxsize=1)
def _page_bytes() -> bytes:
  return PAGE_PATH.read_bytes()


class VehicleMonitor(threading.Thread):
  """Caches whether setting a destination is allowed right now.

  Handler threads must not share a SubMaster, so one thread owns it and the handlers read
  plain attributes. Onroad with no fresh carState the answer is conservative: deny.
  """

  def __init__(self, params: Params):
    super().__init__(daemon=True, name="destinationd_vehicle")
    self.params = params
    self.sm = messaging.SubMaster(['carState'])
    self.offroad = True
    self.moving = False

  @property
  def can_set(self) -> bool:
    return self.offroad or not self.moving

  def run(self):
    while True:
      self.sm.update(1000)
      self.offroad = self.params.get_bool("IsOffroad")
      if self.offroad:
        self.moving = False
      elif self.sm.alive['carState']:
        self.moving = self.sm['carState'].vEgo > STANDSTILL_SPEED
      else:
        self.moving = True


def _json_response(payload: dict, status: int = 200) -> tuple[int, bytes, str]:
  return status, json.dumps(payload).encode(), "application/json"


class DestinationHandler(BaseHTTPRequestHandler):
  protocol_version = "HTTP/1.1"

  # path -> allowed methods; everything else is a 404 so the server never doubles as a file host
  _routes = {
    "/": ("GET", "HEAD"),
    "/api/status": ("GET",),
    "/api/state": ("GET",),
    "/api/route": ("GET",),
    "/api/search": ("GET",),
    "/api/routes": ("GET",),
    "/api/navigate": ("POST",),
    "/api/cancel": ("POST",),
    "/api/favorites": ("POST",),
    "/api/settings": ("GET", "POST"),
  }

  def _send(self, status: int, body: bytes, content_type: str) -> None:
    self.send_response(status)
    self.send_header("Content-Type", content_type)
    self.send_header("Content-Length", str(len(body)))
    self.end_headers()
    if self.command != "HEAD":
      self.wfile.write(body)

  def _read_json(self) -> dict | None:
    length = int(self.headers.get("Content-Length", 0))
    try:
      payload = json.loads(self.rfile.read(length)) if length else {}
    except ValueError:
      return None
    return payload if isinstance(payload, dict) else None

  def _handle_navigate(self, api: DestinationAPI) -> dict:
    body = self._read_json() or {}
    return api.navigate(body.get("dest"), name=body.get("name", ""), summary=body.get("summary", ""))

  def _handle_favorites(self, api: DestinationAPI) -> dict:
    body = self._read_json() or {}
    return api.favorites_action(body.get("action"), name=body.get("name", ""), dest=body.get("dest", ""),
                                kind=body.get("kind"), summary=body.get("summary", ""), names=body.get("names"))

  def _handle_settings_post(self, api: DestinationAPI) -> dict:
    body = self._read_json()
    if body is None:
      raise ApiError("invalid JSON")
    return api.apply_settings(body)

  def _dispatch_request(self) -> None:
    server = cast(DestinationHTTPServer, self.server)
    api = server.api
    parsed = urlparse(self.path)
    allowed = self._routes.get(parsed.path)
    query = parse_qs(parsed.query)

    try:
      if allowed is None:
        result = _json_response({"error": "not found"}, status=404)
      elif self.command not in allowed:
        result = _json_response({"error": "method not allowed"}, status=405)
      elif parsed.path == "/":
        result = (200, _page_bytes(), "text/html; charset=utf-8")
      elif parsed.path == "/api/status":
        result = _json_response(api.status())
      elif parsed.path == "/api/state":
        # live guidance for a polling head unit client; read-only, so no gate
        result = _json_response(nav_state_snapshot())
      elif parsed.path == "/api/route":
        # the decimated route shape, fetched once per routeId change in the poll
        result = _json_response(route_line_snapshot(server.params))
      elif parsed.path == "/api/search":
        result = _json_response(api.search(query.get("q", [""])[0]))
      elif parsed.path == "/api/routes":
        result = _json_response(api.routes(query.get("lon", [None])[0], query.get("lat", [None])[0]))
      elif parsed.path == "/api/navigate":
        result = _json_response(self._handle_navigate(api))
      elif parsed.path == "/api/cancel":
        result = _json_response(api.cancel())
      elif parsed.path == "/api/settings":
        result = _json_response(api.settings_view() if self.command == "GET" else self._handle_settings_post(api))
      else:  # /api/favorites
        result = _json_response(self._handle_favorites(api))
    except ApiError as e:
      result = _json_response({"error": str(e)}, status=e.status)
    except Exception as e:
      cloudlog.exception("destinationd: unhandled error handling %s", self.path)
      result = _json_response({"error": "exception", "message": f"{type(e).__name__}: {e}"}, status=500)

    self._send(*result)

  def do_GET(self) -> None:
    self._dispatch_request()

  def do_HEAD(self) -> None:
    self._dispatch_request()

  def do_POST(self) -> None:
    self._dispatch_request()

  def log_message(self, format: str, *args: object) -> None:  # noqa: A002  # stdlib override
    # silence per-request access logging; errors are logged explicitly in _dispatch_request
    pass


class DestinationHTTPServer(ThreadingHTTPServer):
  daemon_threads = True
  allow_reuse_address = True
  params: Params
  api: DestinationAPI
  vehicle: VehicleMonitor


def make_server(host: str = "0.0.0.0", port: int = DEFAULT_PORT, params: Params | None = None,
                vehicle=None) -> DestinationHTTPServer:
  server = DestinationHTTPServer((host, port), DestinationHandler)
  server.params = params or Params()
  server.vehicle = vehicle if vehicle is not None else VehicleMonitor(server.params)
  server.api = DestinationAPI(params=server.params, can_set=lambda: server.vehicle.can_set,
                              offroad=lambda: server.vehicle.offroad)
  return server


def main():
  parser = argparse.ArgumentParser(description="sunnypilot destination page server")
  parser.add_argument("--host", type=str, default="0.0.0.0", help="Host to listen on")
  parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port to listen on")
  args = parser.parse_args()

  server = make_server(args.host, args.port)
  server.vehicle.start()
  cloudlog.warning("destinationd: serving on %s:%d", args.host, args.port)
  server.serve_forever()


if __name__ == "__main__":
  main()
