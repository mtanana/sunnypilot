"""
Copyright (c) 2021-, James Vecellio, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

Live guidance snapshot for the head unit mirror: one navigationd message (3 Hz
publisher) serialized to a plain dict for destinationd's /api/state, LAN or tailnet
only; guidance and position never ride comma's relay. active false means navigationd
is not publishing, which covers navigation disabled and the process not running; a
client shows the same nothing either way. distanceFromRoute stays home: raw meters
are never drawn, and what is not served cannot be.
"""
import openpilot.cereal.messaging as messaging

NAVSTATE_TIMEOUT_MS = 1000


def nav_state_snapshot(timeout_ms: int = NAVSTATE_TIMEOUT_MS) -> dict:
  sock = messaging.sub_sock('navigationd', timeout=timeout_ms)
  try:
    msg = messaging.recv_one(sock)
  finally:
    del sock
  if msg is None:
    return {"active": False}
  nav = msg.navigationd
  return {
    "active": True,
    "valid": bool(nav.valid),
    "routeState": str(nav.routeState),
    "routeFailures": int(nav.routeFailures),
    "distanceRemaining": float(nav.distanceRemaining),
    "timeRemaining": float(nav.timeRemaining),
    "currentSpeedLimit": int(nav.currentSpeedLimit),
    "bannerInstructions": str(nav.bannerInstructions),
    "audioCueKind": str(nav.audioCueKind),
    "audioCueStage": str(nav.audioCueStage),
    "audioCueDirection": str(nav.audioCueDirection),
    "routeId": int(nav.routeId),
    "position": ({"latitude": float(nav.positionLatitude), "longitude": float(nav.positionLongitude),
                  "bearingDeg": float(nav.positionBearingDeg)} if nav.hasPosition else None),
    "maneuvers": [
      {"distance": float(m.distance), "type": str(m.type), "modifier": str(m.modifier),
       "instruction": str(m.instruction)} for m in nav.allManeuvers
    ],
    "lanes": [
      {"directions": [str(d) for d in lane.directions], "active": bool(lane.active),
       "activeDirection": str(lane.activeDirection)} for lane in nav.lanes
    ],
  }
