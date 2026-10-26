"""
Copyright (c) 2021-, James Vecellio, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

The destination contract, transport-agnostic: one implementation the LAN page
(destinationd) and the athena RPC methods (navd/athena_methods.py) both call, so the
two fronts cannot drift. Methods return plain dicts and raise ApiError for every
refusal; each transport maps ApiError to its own error shape (HTTP status, JSON-RPC
error). The gate is a callable so each transport supplies its own vehicle check with
identical semantics: settings writes only offroad or at a standstill; destinations and
cancel any time.
"""
from collections.abc import Callable

from opendbc.car.structs import car
import openpilot.cereal.messaging as messaging
from openpilot.common.git import get_commit, get_short_branch
from openpilot.common.hardware import HARDWARE
from openpilot.common.params import Params
from openpilot.sunnypilot.navd.helpers import Coordinate, coordinate_from_param
from openpilot.sunnypilot.navd.navigation_helpers.destination_store import DestinationStore
from openpilot.sunnypilot.navd.navigation_helpers.mapbox_integration import MapboxIntegration

STANDSTILL_SPEED = 0.5  # m/s


class ApiError(Exception):
  def __init__(self, message: str, status: int = 400):
    super().__init__(message)
    self.status = status


class DestinationAPI:
  def __init__(self, params: Params | None = None, store: DestinationStore | None = None,
               mapbox: MapboxIntegration | None = None, can_set: Callable[[], bool] = lambda: True,
               offroad: Callable[[], bool] = lambda: True, source: str = "destinationd",
               device: str | None = None, version: str | None = None):
    self.params = params or Params()
    self.store = store or DestinationStore(self.params)
    self.mapbox = mapbox or MapboxIntegration()
    self.can_set = can_set
    self.offroad = offroad
    self.source = source
    self.device = device or HARDWARE.get_device_type()
    self.version = version or " ".join(filter(None, [get_short_branch(), get_commit()[:8]]))

  def status(self) -> dict:
    return {
      "destination": self.store.active_destination(),
      "navEnabled": self.params.get_bool("AllowNavigation"),
      "tokenSet": bool(self.mapbox.get_public_token()),
      "offroad": self.offroad(),
      "canSet": self.can_set(),
      "device": self.device,
      "favorites": self.store.favorites(),
      "recents": self.store.recents(),
    }

  def search(self, text: str) -> dict:
    text = str(text).strip()
    if not text:
      raise ApiError("empty query")
    results = self.mapbox.search_places(text, coordinate_from_param("LastGPSPositionLLK", self.params))
    if results is None:
      raise ApiError("search failed, check the connection and Mapbox token", status=502)
    return {"results": results}

  def routes(self, end_lon, end_lat) -> dict:
    try:
      end_lon, end_lat = float(end_lon), float(end_lat)
    except (TypeError, ValueError):
      raise ApiError("lon and lat are required") from None
    position = coordinate_from_param("LastGPSPositionLLK", self.params)
    if position is None:
      # no start point to preview from; the destination can still be set, navd routes once there is a fix
      raise ApiError("no known device position", status=409)
    routes = self.mapbox.preview_routes(position, Coordinate(end_lat, end_lon))
    if routes is None:
      raise ApiError("route preview failed", status=502)
    return {"routes": routes}

  def navigate(self, dest, name="", summary="") -> dict:
    # settable while driving: nav desires need the driver's blinker and torque, so a route swap never touches control
    if not str(dest or "").strip():
      raise ApiError("dest is required")
    if not self.params.get_bool("AllowNavigation"):
      raise ApiError("navigation is disabled on the device", status=409)
    self.store.set_destination(str(dest), name=str(name or ""), route_summary=str(summary or ""))
    return self.status()

  def cancel(self) -> dict:
    self.store.clear_destination(source=self.source)
    return self.status()

  def favorites_action(self, action, name="", dest="", kind=None, summary="", names=None) -> dict:
    if action == "set" and str(dest or "").strip():
      self.store.set_favorite(str(name or ""), str(dest), kind=kind, summary=str(summary or ""))
    elif action == "remove":
      self.store.remove_favorite(str(name or ""), kind=kind)
    elif action == "reorder" and isinstance(names, list):
      self.store.reorder_favorites(names)
    else:
      raise ApiError("action must be set (with dest), remove, or reorder (with names)")
    return {"favorites": self.store.favorites()}

  # the UI's own reading of longitudinal control, from the CarParams that outlive the drive;
  # None before a car has ever been seen, so a client leaves the row alone
  def _has_longitudinal_control(self) -> bool | None:
    cp_bytes = self.params.get("CarParamsPersistent")
    if cp_bytes is None:
      return None
    CP = messaging.log_from_bytes(cp_bytes, car.CarParams)
    if CP.alphaLongitudinalAvailable:
      return self.params.get_bool("AlphaLongitudinalEnabled")
    return bool(CP.openpilotLongitudinalControl)

  def settings_view(self) -> dict:
    p = self.params
    return {
      # a client grays the rows one device lacks: the arrival pill is the 3X's, the quiet glyph the four's,
      # Slow for Route Turns a car's with openpilot longitudinal
      "device": self.device,
      # the branch and commit the device runs, for bug reports
      "version": self.version,
      "hasLongitudinalControl": self._has_longitudinal_control(),
      "allowNavigation": p.get_bool("AllowNavigation"),
      "navHudMode": p.get("NavHudMode", return_default=True),
      "navAudio": p.get_bool("NavigationAudio"),
      "navDesiresAllowed": p.get_bool("NavDesiresAllowed"),
      "navTurnSlowdown": p.get_bool("NavTurnSlowdown"),
      "laneGuidance": p.get_bool("NavLaneGuidance"),
      "navLaneChangeTimer": p.get("NavLaneChangeTimer", return_default=True),
      "recompute": p.get_bool("MapboxRecompute"),
      "quietGlyph": p.get_bool("NavMiciQuietGlyph"),
      "speedLimitSign": p.get_bool("NavSpeedLimitSign"),
      # stock openpilot's units toggle; every distance a client shows reads by it
      "metric": p.get_bool("IsMetric"),
      # write-only: set or not set is all a client learns of the token
      "tokenSet": bool(self.mapbox.get_public_token()),
    }

  @staticmethod
  def _valid_index(value, upper: int) -> bool:
    # bool is an int, and True must not slip through as mode 1
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= upper

  def apply_settings(self, body: dict) -> dict:
    if not self.can_set():
      raise ApiError("settings can only be changed while parked", status=409)

    p = self.params
    if "allowNavigation" in body:
      p.put_bool("AllowNavigation", bool(body["allowNavigation"]), block=True)
    if "navHudMode" in body:
      if not self._valid_index(body["navHudMode"], 3):
        raise ApiError("navHudMode must be an integer 0 to 3")
      p.put("NavHudMode", body["navHudMode"], block=True)
    if "navAudio" in body:
      p.put_bool("NavigationAudio", bool(body["navAudio"]), block=True)
    if "navDesiresAllowed" in body:
      p.put_bool("NavDesiresAllowed", bool(body["navDesiresAllowed"]), block=True)
    if "navTurnSlowdown" in body:
      p.put_bool("NavTurnSlowdown", bool(body["navTurnSlowdown"]), block=True)
    if "laneGuidance" in body:
      p.put_bool("NavLaneGuidance", bool(body["laneGuidance"]), block=True)
    if "navLaneChangeTimer" in body:
      if not self._valid_index(body["navLaneChangeTimer"], 5):
        raise ApiError("navLaneChangeTimer must be an integer 0 to 5")
      p.put("NavLaneChangeTimer", body["navLaneChangeTimer"], block=True)
    if "recompute" in body:
      p.put_bool("MapboxRecompute", bool(body["recompute"]), block=True)
    if "quietGlyph" in body:
      p.put_bool("NavMiciQuietGlyph", bool(body["quietGlyph"]), block=True)
    if "speedLimitSign" in body:
      p.put_bool("NavSpeedLimitSign", bool(body["speedLimitSign"]), block=True)
    if "token" in body:
      # an empty submit is a no-op, not a wipe
      token = str(body["token"]).strip()
      if token:
        p.put("MapboxToken", token, block=True)
    return self.settings_view()
