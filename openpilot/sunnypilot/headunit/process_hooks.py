"""
Hooks the process manager imports from process_config.py. Keep this module light: it is
imported by the manager and headunit_video() runs on every manager loop (~2 Hz).

Video is requested through a flag file rather than a param, because new params must be
registered in params_keys.h. headunitd writes its time.monotonic() into the file about once a
second while a video session is live; the encoder runs only while that value is fresh, so a
crashed headunitd can never leave the encoder running for more than VIDEO_FLAG_TTL_S.
"""
import os
import tempfile
import time

from openpilot.sunnypilot.headunit.config import HeadUnitConfig

VIDEO_FLAG_TTL_S = 5.0
VIDEO_FLAG_PATH = os.path.join("/dev/shm" if os.path.isdir("/dev/shm") else tempfile.gettempdir(), "headunit_video")

_config = HeadUnitConfig.load()
HEADUNIT_ENABLED = _config.enabled


def video_requested(now: float | None = None, path: str | None = None) -> bool:
  try:
    with open(path or VIDEO_FLAG_PATH) as f:
      stamp = float(f.read().strip())
  except (OSError, ValueError):
    return False
  now = time.monotonic() if now is None else now
  return 0.0 <= now - stamp < VIDEO_FLAG_TTL_S


def headunit_video(started, params, CP) -> bool:
  if not (HEADUNIT_ENABLED and started) or CP.notCar:
    return False
  # comma connect's own stream_encoderd publishes the same topics; it always wins
  return video_requested() and not params.get_bool("IsLiveStreaming")


def headunit_encoderd_cmdline() -> list[str]:
  # encoderd reads STREAM_BITRATE for its livestream encoders (loggerd.h StreamEncoderSettings)
  return ["env", f"STREAM_BITRATE={_config.video_bitrate}", "./encoderd", "--stream"]
