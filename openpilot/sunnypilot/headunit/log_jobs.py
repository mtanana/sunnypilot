"""
Route listing and on-demand .shlog exports for sunnyhead's Logs view.

The export itself runs log_export.py in a child process (lowest CPU priority) so a big route can't
grow headunitd's memory or stall its HTTP threads. One export at a time; finished files are kept
in config.export_dir (newest config.export_keep_files) and reused while their source logs are
unchanged. Exports are refused onroad, and a running one is cancelled when the car starts, unless
config.export_allow_onroad is set.
"""
import collections
import os
import re
import subprocess
import sys
import threading
import time
import uuid

from openpilot.common.basedir import BASEDIR
from openpilot.common.swaglog import cloudlog

from openpilot.sunnypilot.headunit.config import HeadUnitConfig
from openpilot.sunnypilot.headunit.log_export import LOG_NAMES, PRESETS, REALDATA, SEGMENT_RE

ROUTE_RE = re.compile(r"^[\w-]+$")
EXPORT_NAME_RE = re.compile(r"^[\w.-]+\.shlog$")
PROGRESS_RE = re.compile(r"^\[(\d+)/(\d+)\]")
STDERR_TAIL = 20
MAX_JOBS = 20  # finished jobs remembered for /logs/job


class ExportRefused(Exception):
  """Request can't be served now. `reason` is a stable code the app maps to a message."""

  def __init__(self, reason: str, status: int = 409, detail: str = ""):
    super().__init__(reason)
    self.reason = reason
    self.status = status
    self.detail = detail


def _log_file(seg_dir: str, names: tuple[str, ...]) -> str | None:
  for name in names:
    p = os.path.join(seg_dir, name)
    if os.path.isfile(p):
      return p
  return None


def scan_segments(root: str = REALDATA) -> dict[str, dict[int, str]]:
  """route -> {segment number -> log file}, preferring rlogs."""
  routes: dict[str, dict[int, str]] = {}
  try:
    names = os.listdir(root)
  except OSError:
    return routes
  for name in names:
    m = SEGMENT_RE.match(name)
    if m is None or not ROUTE_RE.match(m.group(1)):
      continue
    fn = _log_file(os.path.join(root, name), LOG_NAMES)
    if fn is not None:
      routes.setdefault(m.group(1), {})[int(m.group(2))] = fn
  return routes


def list_routes(root: str = REALDATA) -> list[dict]:
  out = []
  for route, segs in scan_segments(root).items():
    files = list(segs.values())
    try:
      mtimes = [os.path.getmtime(f) for f in files]
      size = sum(os.path.getsize(f) for f in files)
    except OSError:  # deleter removed a segment mid-scan
      continue
    out.append({
      "route": route,
      "segments": sorted(segs),
      "qlogOnly": sorted(n for n, f in segs.items() if os.path.basename(f).startswith("qlog")),
      "bytes": size,
      # file times: the device clock can be wrong before GPS/NTP sync, so treat as approximate
      "startWallS": min(mtimes) - 60.0,
      "endWallS": max(mtimes),
    })
  out.sort(key=lambda r: r["endWallS"], reverse=True)
  return out


class ExportJob:
  def __init__(self, route: str, first: int, last: int, preset: str, files: list[str], output: str):
    self.id = uuid.uuid4().hex[:12]
    self.route = route
    self.first = first
    self.last = last
    self.preset = preset
    self.files = files
    self.output = output
    self.state = "running"  # running | done | error | cancelled
    self.done = 0
    self.total = len(files)
    self.error: str | None = None
    self.cached = False
    self.started_at = time.monotonic()
    self.finished_at: float | None = None
    self.proc: subprocess.Popen | None = None
    self.stderr_tail: collections.deque[str] = collections.deque(maxlen=STDERR_TAIL)

  @property
  def file(self) -> str:
    return os.path.basename(self.output)

  @property
  def finished(self) -> bool:
    return self.state != "running"

  def to_dict(self) -> dict:
    end = self.finished_at if self.finished_at is not None else time.monotonic()
    size = None
    if self.state == "done":
      try:
        size = os.path.getsize(self.output)
      except OSError:
        pass
    return {
      "id": self.id,
      "state": self.state,
      "route": self.route,
      "start": self.first,
      "end": self.last,
      "preset": self.preset,
      "done": self.done,
      "total": self.total,
      "file": self.file if self.state == "done" else None,
      "bytes": size,
      "cached": self.cached,
      "error": self.error,
      "elapsedS": round(end - self.started_at, 1),
    }


class LogExports:
  def __init__(self, config: HeadUnitConfig, root: str = REALDATA, command: list[str] | None = None):
    self.config = config
    self.root = root
    self.out_dir = config.export_dir
    # overridable for tests; real exports run the module with the daemon's interpreter
    self.command = command or ["nice", "-n", "19", sys.executable, "-m", "openpilot.sunnypilot.headunit.log_export"]
    self._lock = threading.Lock()
    self._jobs: dict[str, ExportJob] = {}
    self._current: ExportJob | None = None
    self._onroad = False

  # ---- daemon hooks

  def tick(self, started: bool) -> None:
    self._onroad = started
    if started and not self.config.export_allow_onroad:
      job = self._current
      if job is not None and not job.finished:
        self._kill(job, "cancelled", "the car started")

  def shutdown(self) -> None:
    job = self._current
    if job is not None and not job.finished:
      self._kill(job, "cancelled", "headunitd stopped")

  # ---- queries

  @property
  def allowed(self) -> bool:
    return self.config.export_allow_onroad or not self._onroad

  def overview(self) -> dict:
    job = self._current
    return {
      "onroad": self._onroad,
      "exportsAllowed": self.allowed,
      "maxSegments": self.config.export_max_segments,
      "presets": sorted(PRESETS),
      "routes": list_routes(self.root),
      "job": job.to_dict() if job is not None else None,
    }

  def exports(self) -> list[dict]:
    out = []
    try:
      names = os.listdir(self.out_dir)
    except OSError:
      return out
    for name in names:
      if not EXPORT_NAME_RE.match(name):
        continue
      p = os.path.join(self.out_dir, name)
      try:
        st = os.stat(p)
      except OSError:
        continue
      out.append({"name": name, "bytes": st.st_size, "wallS": st.st_mtime})
    out.sort(key=lambda e: e["wallS"], reverse=True)
    return out

  def job(self, job_id: str) -> ExportJob | None:
    return self._jobs.get(job_id)

  def file_path(self, name: str) -> str | None:
    if not EXPORT_NAME_RE.match(name):
      return None
    p = os.path.join(self.out_dir, name)
    return p if os.path.isfile(p) else None

  # ---- actions

  def start(self, route: str, first: int | None, last: int | None, preset: str) -> ExportJob:
    if preset not in PRESETS:
      raise ExportRefused("bad_preset", 400)
    if not ROUTE_RE.match(route):
      raise ExportRefused("bad_route", 400)
    if not self.allowed:
      raise ExportRefused("onroad")

    segs = scan_segments(self.root).get(route)
    if not segs:
      raise ExportRefused("no_route", 404)
    first = min(segs) if first is None else first
    last = max(segs) if last is None else last
    if first > last:
      first, last = last, first
    picked = [n for n in sorted(segs) if first <= n <= last]
    if not picked:
      raise ExportRefused("no_segments", 404)
    if len(picked) > self.config.export_max_segments:
      raise ExportRefused("too_many_segments", 400, f"{len(picked)} > {self.config.export_max_segments}")
    first, last = picked[0], picked[-1]
    files = [segs[n] for n in picked]
    output = os.path.join(self.out_dir, f"{route}--{first}-{last}.{preset}.shlog")

    with self._lock:
      cur = self._current
      if cur is not None and not cur.finished:
        if cur.output == output:
          return cur
        raise ExportRefused("busy")

      job = ExportJob(route, first, last, preset, files, output)
      self._jobs = {k: v for k, v in list(self._jobs.items())[-(MAX_JOBS - 1):]}
      self._jobs[job.id] = job
      self._current = job
      if self._is_fresh(output, files):
        job.state, job.cached, job.done = "done", True, job.total
        job.finished_at = time.monotonic()
        return job

      cmd = [*self.command, *(os.path.dirname(f) for f in files), "-o", output + ".part", "--preset", preset]
      try:
        os.makedirs(self.out_dir, exist_ok=True)
        job.proc = subprocess.Popen(cmd, cwd=BASEDIR, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.PIPE, text=True, errors="replace")
      except OSError as e:
        job.state, job.error, job.finished_at = "error", f"could not start exporter: {e}", time.monotonic()
        return job
    threading.Thread(target=self._watch, args=(job,), name="headunit-export", daemon=True).start()
    cloudlog.info(f"headunitd: exporting {route} segments {first}-{last} ({preset})")
    return job

  def cancel(self, job_id: str) -> ExportJob | None:
    job = self._jobs.get(job_id)
    if job is not None and not job.finished:
      self._kill(job, "cancelled", None)
    return job

  # ---- internals

  @staticmethod
  def _is_fresh(output: str, files: list[str]) -> bool:
    try:
      out_mtime = os.path.getmtime(output)
      return all(os.path.getmtime(f) <= out_mtime for f in files)
    except OSError:
      return False

  def _kill(self, job: ExportJob, state: str, error: str | None) -> None:
    with self._lock:
      if job.finished:
        return
      job.state, job.error, job.finished_at = state, error, time.monotonic()
    if job.proc is not None and job.proc.poll() is None:
      job.proc.terminate()

  def _watch(self, job: ExportJob) -> None:
    proc = job.proc
    assert proc is not None and proc.stderr is not None
    for line in proc.stderr:
      line = line.rstrip()
      if not line:
        continue
      job.stderr_tail.append(line)
      m = PROGRESS_RE.match(line)
      if m:
        job.done = int(m.group(1)) - 1  # "[i/n]" is printed when segment i starts
    rc = proc.wait()
    part = job.output + ".part"
    with self._lock:
      if not job.finished:
        if rc == 0 and os.path.isfile(part):
          try:
            os.replace(part, job.output)
            job.state, job.done = "done", job.total
          except OSError as e:
            job.state, job.error = "error", f"could not save export: {e}"
        else:
          job.state = "error"
          job.error = job.stderr_tail[-1] if job.stderr_tail else f"exporter exited with {rc}"
        job.finished_at = time.monotonic()
    if os.path.exists(part):
      try:
        os.remove(part)
      except OSError:
        pass
    if job.state == "done":
      cloudlog.info(f"headunitd: export {job.file} done in {job.to_dict()['elapsedS']} s")
      self._prune()
    elif job.state == "error":
      cloudlog.warning(f"headunitd: export {job.route} failed: {list(job.stderr_tail)}")

  def _prune(self) -> None:
    for entry in self.exports()[self.config.export_keep_files:]:
      try:
        os.remove(os.path.join(self.out_dir, entry["name"]))
      except OSError:
        pass
