import os
import sys
import time

import pytest

from openpilot.sunnypilot.headunit.config import HeadUnitConfig
from openpilot.sunnypilot.headunit.log_jobs import ExportRefused, LogExports, list_routes

# Stand-in for log_export: prints progress like the real one and writes the -o file.
FAKE_EXPORTER = """
import sys, time
args = sys.argv[1:]
out = args[args.index("-o") + 1]
dirs = args[:args.index("-o")]
for i, d in enumerate(dirs):
  print(f"[{i + 1}/{len(dirs)}] {d}", file=sys.stderr, flush=True)
  time.sleep(float(__import__("os").environ.get("FAKE_EXPORT_DELAY", "0")))
if "fail" in out:
  raise SystemExit("boom: bad log")
open(out, "wb").write(b"shlog")
"""

ROUTE = "0000002a--1a2b3c4d5e"


def make_route(root, route=ROUTE, segments=(0, 1, 2), qlog_only=()):
  for n in segments:
    d = root / f"{route}--{n}"
    d.mkdir(parents=True)
    (d / ("qlog.zst" if n in qlog_only else "rlog.zst")).write_bytes(b"x" * 10)


def exports(tmp_path, **cfg) -> LogExports:
  config = HeadUnitConfig(export_dir=str(tmp_path / "out"), **cfg)
  return LogExports(config, root=str(tmp_path / "realdata"), command=[sys.executable, "-c", FAKE_EXPORTER])


def wait(job, timeout=10.0):
  deadline = time.monotonic() + timeout
  while not job.finished and time.monotonic() < deadline:
    time.sleep(0.02)
  assert job.finished
  return job


class TestLogJobs:
  def test_list_routes(self, tmp_path):
    root = tmp_path / "realdata"
    make_route(root, segments=(0, 1, 2), qlog_only=(2,))
    (root / "not-a-segment").mkdir()
    (root / f"{ROUTE}--9").mkdir()  # no logs
    routes = list_routes(str(root))
    assert len(routes) == 1
    r = routes[0]
    assert r["route"] == ROUTE and r["segments"] == [0, 1, 2] and r["qlogOnly"] == [2]
    assert r["bytes"] == 30

  def test_export_runs_and_caches(self, tmp_path):
    make_route(tmp_path / "realdata")
    ex = exports(tmp_path)
    job = wait(ex.start(ROUTE, 1, 2, "long"))
    assert job.state == "done", job.error
    assert job.done == job.total == 2
    assert job.file == f"{ROUTE}--1-2.long.shlog"
    assert ex.file_path(job.file) is not None
    assert [e["name"] for e in ex.exports()] == [job.file]

    again = ex.start(ROUTE, 1, 2, "long")
    assert again.cached and again.state == "done"

  def test_failure_reports_last_stderr_line(self, tmp_path):
    make_route(tmp_path / "realdata", route="fail")
    job = wait(exports(tmp_path).start("fail", None, None, "all"))
    assert job.state == "error"
    assert "boom" in job.error
    assert not os.path.exists(job.output + ".part")

  def test_refusals(self, tmp_path):
    make_route(tmp_path / "realdata")
    ex = exports(tmp_path, export_max_segments=2)
    for args, reason in (((ROUTE, None, None, "nope"), "bad_preset"), (("../etc", None, None, "long"), "bad_route"),
                         (("missing", None, None, "long"), "no_route"), ((ROUTE, 7, 9, "long"), "no_segments"),
                         ((ROUTE, None, None, "long"), "too_many_segments")):
      with pytest.raises(ExportRefused) as e:
        ex.start(*args)
      assert e.value.reason == reason
    ex.tick(True)
    with pytest.raises(ExportRefused) as e:
      ex.start(ROUTE, 0, 0, "long")
    assert e.value.reason == "onroad"

  def test_busy_and_onroad_cancel(self, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_EXPORT_DELAY", "5")
    make_route(tmp_path / "realdata")
    ex = exports(tmp_path)
    job = ex.start(ROUTE, 0, 1, "long")
    assert ex.start(ROUTE, 0, 1, "long") is job  # same request joins the running job
    with pytest.raises(ExportRefused) as e:
      ex.start(ROUTE, 2, 2, "long")
    assert e.value.reason == "busy"
    ex.tick(True)
    assert wait(job).state == "cancelled"
    job.proc.wait(timeout=5)
    assert ex.exports() == []

  def test_file_path_rejects_traversal(self, tmp_path):
    ex = exports(tmp_path)
    for name in ("../headunit.json", "x.json", "", "a/b.shlog"):
      assert ex.file_path(name) is None
