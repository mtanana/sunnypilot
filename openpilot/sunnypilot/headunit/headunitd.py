#!/usr/bin/env python3
"""
headunitd: streams openpilot/sunnypilot state (and, on request, H.264 video) to a head unit
or browser on the local network. See README.md in this directory for the protocol.

Idle cost: one Python process waking twice a second to read conflated messages. JSON is only
built while a client is listening, and the video encoder only runs during a video session.
"""
import os
import threading
import time

import openpilot.cereal.messaging as messaging
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog

from openpilot.sunnypilot.headunit import mdns
from openpilot.sunnypilot.headunit.config import HeadUnitConfig
from openpilot.sunnypilot.headunit.guards import LoadGuard
from openpilot.sunnypilot.headunit.hub import TelemetryHub
from openpilot.sunnypilot.headunit.server import HeadUnitServer
from openpilot.sunnypilot.headunit.telemetry import SCHEMA_VERSION, SERVICES, TelemetryCollector
from openpilot.sunnypilot.headunit.video import VideoController, VideoForwarder

IDLE_INTERVAL_S = 0.5
PARAMS_INTERVAL_S = 1.0
BIND_RETRY_S = 5.0


class HeadUnitDaemon:
  def __init__(self, config: HeadUnitConfig):
    self.config = config
    self.params = Params()
    self.sm = messaging.SubMaster(SERVICES)
    self.hub = TelemetryHub()
    self.collector = TelemetryCollector(config)
    self.guard = LoadGuard(config)
    self.video = VideoController(config, self.guard)
    self.forwarder = VideoForwarder(self.video)
    self.server: HeadUnitServer | None = None

    self._started = False
    self._is_metric = False
    self._live_streaming = False
    self._last_params_read = float("-inf")

  def status(self) -> dict:
    return {
      "v": SCHEMA_VERSION,
      "started": self._started,
      "telemetryClients": self.hub.subscribers,
      "video": self.video.status(time.monotonic()),
    }

  def run(self) -> None:
    self.server = self._bind()
    threading.Thread(target=self.server.serve_forever, name="headunit-http", daemon=True).start()
    self.forwarder.start()
    mdns.start(self.config.port)
    cloudlog.info(f"headunitd: listening on {self.config.bind}:{self.config.port}")
    try:
      while True:
        start = time.monotonic()
        try:
          self.step(start)
        except Exception:
          cloudlog.exception("headunitd: step failed")
        busy = self.hub.wanted or self.video.active
        interval = 1.0 / self.config.telemetry_max_hz if busy else IDLE_INTERVAL_S
        self.video.wake.wait(max(0.0, interval - (time.monotonic() - start)))
        self.video.wake.clear()
    finally:
      mdns.stop()
      self.forwarder.stop()
      self.video.shutdown()
      self.server.shutdown()
      self.server.server_close()

  def step(self, now: float) -> None:
    self.sm.update(0)
    self._read_params(now)
    self._started = bool(self.sm["deviceState"].started)

    self.guard.add_sample(now, *self.collector.guard_inputs(self.sm))
    self.video.tick(now, self._started, self._live_streaming)

    if self.hub.wanted:
      self.hub.publish(self.collector.build(self.sm, now, self._is_metric, self.video.status(now)))

  def _read_params(self, now: float) -> None:
    if now - self._last_params_read < PARAMS_INTERVAL_S:
      return
    self._last_params_read = now
    self._live_streaming = self.params.get_bool("IsLiveStreaming")
    self._is_metric = self.params.get_bool("IsMetric")

  def _bind(self) -> HeadUnitServer:
    while True:
      try:
        return HeadUnitServer(self.config, self.hub, self.video, self.status)
      except OSError:
        cloudlog.exception(f"headunitd: can't bind {self.config.bind}:{self.config.port}, retrying")
        time.sleep(BIND_RETRY_S)


def main():
  # this is a convenience feature; never compete with openpilot for CPU
  os.nice(10)
  HeadUnitDaemon(HeadUnitConfig.load()).run()


if __name__ == "__main__":
  main()
