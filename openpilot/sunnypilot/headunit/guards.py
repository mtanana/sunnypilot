"""
Load guard for the optional video stream.

  - Frame drops: trips as soon as modelV2.frameDropPerc exceeds guard_frame_drop_perc. This is
    an absolute line, not relative to a baseline, because selfdrived soft-disables openpilot
    ("Driving Model Lagging") once frameDropPerc goes above 1%. The default leaves margin below that.
  - Model run time: averaged over guard_window_s, compared against the run time just before
    video started so a model that is normally slow on this device doesn't lock video out:
    trips above max(guard_model_exec_s, baseline * guard_baseline_headroom).
  - Thermal: trips immediately at overheated or worse.
"""
import math
from collections import deque

from openpilot.cereal import log

from openpilot.sunnypilot.headunit.config import HeadUnitConfig

THERMAL_OVERHEATED = log.DeviceState.ThermalStatus.overheated


class LoadGuard:
  def __init__(self, config: HeadUnitConfig):
    self.config = config
    self._samples: deque[tuple[float, float]] = deque()  # (t, exec_s)
    self._thermal = 0
    self._drop_perc = 0.0
    self._session_start: float | None = None
    self.baseline_exec = 0.0

  def add_sample(self, now: float, thermal_status: int, model_alive: bool, exec_s: float, drop_perc: float) -> None:
    self._thermal = int(thermal_status)
    if model_alive and math.isfinite(drop_perc):
      self._drop_perc = float(drop_perc)
    elif not model_alive:
      self._drop_perc = 0.0
    if model_alive and math.isfinite(exec_s):
      self._samples.append((now, float(exec_s)))
    keep = self.config.guard_window_s if self._session_start is not None else self.config.guard_baseline_s
    while self._samples and now - self._samples[0][0] > keep:
      self._samples.popleft()

  def start_session(self, now: float) -> None:
    self.baseline_exec = self._avg_exec()
    self._samples.clear()
    self._session_start = now

  def end_session(self) -> None:
    self._session_start = None

  @property
  def exec_limit(self) -> float:
    return max(self.config.guard_model_exec_s, self.baseline_exec * self.config.guard_baseline_headroom)

  def check(self, now: float) -> str | None:
    """Reason video must stop, or None. Only meaningful between start_session and end_session."""
    if self._session_start is None:
      return None
    if self._thermal >= THERMAL_OVERHEATED:
      return "thermal"
    if self._drop_perc > self.config.guard_frame_drop_perc:
      return "frame_drops"
    # judge the run-time average only once a full window of video-on samples exists
    if self._samples and now - self._session_start >= self.config.guard_window_s and self._avg_exec() > self.exec_limit:
      return "model_exec_time"
    return None

  def _avg_exec(self) -> float:
    if not self._samples:
      return 0.0
    return sum(s[1] for s in self._samples) / len(self._samples)

  def status(self) -> dict:
    return {
      "avgModelExecS": round(self._avg_exec(), 4),
      "execLimitS": round(self.exec_limit, 4),
      "frameDropPerc": round(self._drop_perc, 2),
      "dropLimitPerc": self.config.guard_frame_drop_perc,
    }
