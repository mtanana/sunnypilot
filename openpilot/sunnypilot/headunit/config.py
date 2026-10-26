"""
Head unit bridge configuration.

Defaults live here; an optional JSON file (default /data/headunit.json, override with the
HEADUNIT_CONFIG env var) can override any field. Unknown keys are ignored so an old config
file never breaks startup.
"""
import json
import os
from dataclasses import dataclass, fields, replace

DEFAULT_CONFIG_PATH = "/data/headunit.json"

CAMERAS = {
  "road": "livestreamNarrowRoadEncodeData",
  "wide": "livestreamWideRoadEncodeData",
  "driver": "livestreamCabinEncodeData",
}


@dataclass(frozen=True)
class HeadUnitConfig:
  enabled: bool = True
  bind: str = "0.0.0.0"
  port: int = 8090
  # only LAN / link-local / CGNAT (tailscale) clients unless this is set
  allow_public_clients: bool = False

  telemetry_max_hz: int = 20

  video_bitrate: int = 2_000_000          # per camera; applied when the manager starts
  video_default_camera: str = "road"
  video_max_session_s: float = 900.0
  video_idle_stop_s: float = 5.0          # stop this long after the last viewer leaves
  video_first_frame_timeout_s: float = 15.0
  video_cooldown_s: float = 60.0          # refuse restarts this long after a load-guard stop

  guard_window_s: float = 3.0
  guard_baseline_s: float = 10.0
  guard_model_exec_s: float = 0.035
  guard_baseline_headroom: float = 1.25
  guard_frame_drop_perc: float = 0.5      # selfdrived soft-disables above 1.0

  @classmethod
  def load(cls, path: str | None = None) -> "HeadUnitConfig":
    path = path or os.environ.get("HEADUNIT_CONFIG", DEFAULT_CONFIG_PATH)
    cfg = cls()
    try:
      with open(path) as f:
        raw = json.load(f)
    except (OSError, ValueError):
      return cfg
    if not isinstance(raw, dict):
      return cfg

    overrides = {}
    for field in fields(cls):
      if field.name not in raw:
        continue
      default = getattr(cfg, field.name)
      try:
        overrides[field.name] = type(default)(raw[field.name])
      except (TypeError, ValueError):
        continue
    cfg = replace(cfg, **overrides)
    if cfg.video_default_camera not in CAMERAS:
      cfg = replace(cfg, video_default_camera="road")
    return cfg
