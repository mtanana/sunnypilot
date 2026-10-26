from openpilot.sunnypilot.headunit.config import HeadUnitConfig
from openpilot.sunnypilot.headunit.guards import THERMAL_OVERHEATED, LoadGuard

DT = 0.05


def feed(guard: LoadGuard, start: float, duration: float, exec_s: float, drop: float = 0.0, thermal: int = 0) -> float:
  t = start
  while t < start + duration:
    guard.add_sample(t, thermal, True, exec_s, drop)
    t += DT
  return t


class TestLoadGuard:
  def setup_method(self):
    self.cfg = HeadUnitConfig()
    self.guard = LoadGuard(self.cfg)

  def test_no_verdict_outside_session(self):
    feed(self.guard, 0.0, 5.0, exec_s=1.0, thermal=THERMAL_OVERHEATED)
    assert self.guard.check(5.0) is None

  def test_thermal_trips_immediately(self):
    self.guard.start_session(0.0)
    self.guard.add_sample(0.1, THERMAL_OVERHEATED, True, 0.01, 0.0)
    assert self.guard.check(0.1) == "thermal"

  def test_waits_for_full_window(self):
    self.guard.start_session(0.0)
    t = feed(self.guard, 0.0, self.cfg.guard_window_s / 2, exec_s=0.1)
    assert self.guard.check(t) is None
    t = feed(self.guard, t, self.cfg.guard_window_s, exec_s=0.1)
    assert self.guard.check(t) == "model_exec_time"

  def test_absolute_limit_with_fast_baseline(self):
    t = feed(self.guard, 0.0, self.cfg.guard_baseline_s, exec_s=0.020)
    self.guard.start_session(t)
    assert self.guard.exec_limit == self.cfg.guard_model_exec_s
    t = feed(self.guard, t, self.cfg.guard_window_s + 0.5, exec_s=0.030)
    assert self.guard.check(t) is None
    t = feed(self.guard, t, self.cfg.guard_window_s + 0.5, exec_s=0.040)
    assert self.guard.check(t) == "model_exec_time"

  def test_headroom_over_slow_baseline(self):
    t = feed(self.guard, 0.0, self.cfg.guard_baseline_s, exec_s=0.040)
    self.guard.start_session(t)
    assert abs(self.guard.exec_limit - 0.040 * self.cfg.guard_baseline_headroom) < 1e-6
    t = feed(self.guard, t, self.cfg.guard_window_s + 0.5, exec_s=0.045)
    assert self.guard.check(t) is None
    t = feed(self.guard, t, self.cfg.guard_window_s + 0.5, exec_s=0.060)
    assert self.guard.check(t) == "model_exec_time"

  def test_frame_drops_trip_immediately_on_absolute_line(self):
    t = feed(self.guard, 0.0, self.cfg.guard_baseline_s, exec_s=0.02, drop=0.0)
    self.guard.start_session(t)
    self.guard.add_sample(t + DT, 0, True, 0.02, self.cfg.guard_frame_drop_perc * 0.9)
    assert self.guard.check(t + DT) is None
    self.guard.add_sample(t + 2 * DT, 0, True, 0.02, self.cfg.guard_frame_drop_perc + 0.1)
    assert self.guard.check(t + 2 * DT) == "frame_drops"

  def test_drop_line_stays_below_selfdrived_soft_disable(self):
    # selfdrived raises modeldLagging (soft disable) above 1% frame drops
    assert self.cfg.guard_frame_drop_perc < 1.0

  def test_model_not_alive_is_not_judged(self):
    self.guard.start_session(0.0)
    for i in range(200):
      self.guard.add_sample(i * DT, 0, False, 1.0, 100.0)
    assert self.guard.check(200 * DT) is None
