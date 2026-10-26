"""
Optional H.264 video for the head unit.

VideoController owns the session state machine and the flag file that makes the process
manager run headunit_encoderd (see process_hooks.py). It is ticked from headunitd's main
loop; HTTP handler threads only add/remove viewers and ask for stops.

VideoForwarder subscribes to the livestream*EncodeData topics for cameras that have viewers
and hands each encoded frame (SPS/PPS header + data, Annex B) to those viewers' queues.
"""
import os
import queue
import struct
import threading
from dataclasses import dataclass

import openpilot.cereal.messaging as messaging
from openpilot.common.swaglog import cloudlog

from openpilot.sunnypilot.headunit.config import CAMERAS, HeadUnitConfig
from openpilot.sunnypilot.headunit.guards import LoadGuard
from openpilot.sunnypilot.headunit.process_hooks import VIDEO_FLAG_PATH

V4L2_BUF_FLAG_KEYFRAME = 0x8

# framed format: 4-byte big-endian payload length, 1 flag byte (bit0 = keyframe),
# 8-byte big-endian pts in microseconds (camera end-of-frame time), then the Annex B payload
FRAME_HEADER = struct.Struct(">IBQ")

GUARD_REASONS = {"thermal", "model_exec_time", "frame_drops"}
SERVICE_TO_CAMERA = {service: camera for camera, service in CAMERAS.items()}


@dataclass(frozen=True)
class EncodedFrame:
  camera: str
  keyframe: bool
  pts_us: int
  payload: bytes

  def framed(self) -> bytes:
    return FRAME_HEADER.pack(len(self.payload), int(self.keyframe), self.pts_us) + self.payload


class VideoUnavailable(Exception):
  def __init__(self, reason: str):
    super().__init__(reason)
    self.reason = reason


class Viewer:
  QUEUE_FRAMES = 30  # 1.5 s at 20 fps; a slower client is dropped back to the next keyframe

  def __init__(self, camera: str, now: float):
    self.camera = camera
    self.opened = now
    self.frames_sent = 0
    self.closed_reason: str | None = None
    self._queue: queue.Queue[EncodedFrame | None] = queue.Queue(maxsize=self.QUEUE_FRAMES)
    self._need_keyframe = True

  def resync(self) -> None:
    self._need_keyframe = True

  def offer(self, frame: EncodedFrame) -> None:
    """Called from the forwarder thread only."""
    if self.closed_reason is not None:
      return
    if self._need_keyframe:
      if not frame.keyframe:
        return
      self._need_keyframe = False
    try:
      self._queue.put_nowait(frame)
    except queue.Full:
      self._clear()
      if frame.keyframe:
        self._queue.put_nowait(frame)
      else:
        self._need_keyframe = True

  def get(self, timeout: float) -> EncodedFrame | None:
    try:
      return self._queue.get(timeout=timeout)
    except queue.Empty:
      return None

  def close(self, reason: str) -> None:
    self.closed_reason = reason
    self._clear()
    try:
      self._queue.put_nowait(None)  # wake a handler blocked in get()
    except queue.Full:
      pass

  def _clear(self) -> None:
    try:
      while True:
        self._queue.get_nowait()
    except queue.Empty:
      pass


class VideoController:
  FLAG_REFRESH_S = 1.0

  def __init__(self, config: HeadUnitConfig, guard: LoadGuard, flag_path: str = VIDEO_FLAG_PATH):
    self.config = config
    self.guard = guard
    self.flag_path = flag_path
    self.wake = threading.Event()     # wakes headunitd's main loop
    self.changed = threading.Event()  # wakes the forwarder when cameras/session change

    self._lock = threading.Lock()
    self._viewers: set[Viewer] = set()
    self._active = False
    self._session_start = 0.0
    self._last_viewer_left: float | None = None
    self._last_stop_reason: str | None = None
    self._stop_requested: str | None = None
    self._cooldown_until = 0.0
    self._started = False
    self._live_streaming = False
    self._last_flag_write = 0.0
    self._guard_status: dict = guard.status()
    self._remove_flag()

  @property
  def active(self) -> bool:
    with self._lock:
      return self._active

  # *** called from HTTP handler threads ***

  def open_viewer(self, camera: str, now: float) -> Viewer:
    if camera not in CAMERAS:
      raise VideoUnavailable("unknown_camera")
    with self._lock:
      reason = self._refusal(now)
      if reason is not None:
        raise VideoUnavailable(reason)
      viewer = Viewer(camera, now)
      self._viewers.add(viewer)
      self._last_viewer_left = None
    self.changed.set()
    self.wake.set()
    return viewer

  def close_viewer(self, viewer: Viewer, now: float) -> None:
    with self._lock:
      if viewer in self._viewers:
        self._viewers.discard(viewer)
        if not self._viewers:
          self._last_viewer_left = now
    self.changed.set()

  def request_stop(self, reason: str = "user") -> None:
    with self._lock:
      self._stop_requested = reason
    self.wake.set()

  # *** called from the forwarder thread ***

  def cameras(self) -> set[str]:
    with self._lock:
      return {v.camera for v in self._viewers} if self._active else set()

  def viewers_for(self, camera: str) -> list[Viewer]:
    with self._lock:
      return [v for v in self._viewers if v.camera == camera]

  # *** called from headunitd's main loop ***

  def tick(self, now: float, started: bool, live_streaming: bool) -> None:
    with self._lock:
      self._started, self._live_streaming = started, live_streaming

      for viewer in list(self._viewers):
        if viewer.frames_sent == 0 and now - viewer.opened > self.config.video_first_frame_timeout_s:
          self._drop_viewer(viewer, "no_frames", now)

      stop_requested, self._stop_requested = self._stop_requested, None
      if not self._active:
        if self._viewers:
          reason = stop_requested or self._refusal(now)
          if reason is None:
            self._start(now)
          else:
            for viewer in list(self._viewers):
              self._drop_viewer(viewer, reason, now)
      else:
        reason = stop_requested or self._stop_reason(now)
        if reason is not None:
          self._stop(now, reason)
        elif now - self._last_flag_write >= self.FLAG_REFRESH_S:
          self._write_flag(now)

      self._guard_status = self.guard.status()

  def shutdown(self) -> None:
    with self._lock:
      for viewer in self._viewers:
        viewer.close("shutdown")
      self._viewers.clear()
      self._active = False
    self._remove_flag()
    self.changed.set()

  def status(self, now: float) -> dict:
    with self._lock:
      return {
        "active": self._active,
        "viewers": len(self._viewers),
        "cameras": sorted({v.camera for v in self._viewers}),
        "sessionS": round(now - self._session_start, 1) if self._active else None,
        "maxSessionS": self.config.video_max_session_s,
        "lastStopReason": self._last_stop_reason,
        "cooldownS": round(max(0.0, self._cooldown_until - now), 1),
        "bitrate": self.config.video_bitrate,
        "guard": self._guard_status,
      }

  # *** internals, lock held ***

  def _refusal(self, now: float) -> str | None:
    if not self._started:
      return "offroad"
    if self._live_streaming:
      return "comma_connect"
    if now < self._cooldown_until:
      return "cooldown"
    return None

  def _stop_reason(self, now: float) -> str | None:
    if not self._started:
      return "offroad"
    if self._live_streaming:
      return "comma_connect"
    guard_reason = self.guard.check(now)
    if guard_reason is not None:
      return guard_reason
    if now - self._session_start > self.config.video_max_session_s:
      return "session_limit"
    if not self._viewers and self._last_viewer_left is not None and now - self._last_viewer_left > self.config.video_idle_stop_s:
      return "idle"
    return None

  def _start(self, now: float) -> None:
    self._active = True
    self._session_start = now
    self._last_stop_reason = None
    self.guard.start_session(now)
    self._write_flag(now)
    self.changed.set()

  def _stop(self, now: float, reason: str) -> None:
    self._active = False
    self._remove_flag()
    self.guard.end_session()
    for viewer in self._viewers:
      viewer.close(reason)
    self._viewers.clear()
    self._last_viewer_left = None
    self._last_stop_reason = reason
    if reason in GUARD_REASONS:
      self._cooldown_until = now + self.config.video_cooldown_s
    self.changed.set()

  def _drop_viewer(self, viewer: Viewer, reason: str, now: float) -> None:
    viewer.close(reason)
    self._viewers.discard(viewer)
    if not self._viewers:
      self._last_viewer_left = now
    self.changed.set()

  def _write_flag(self, now: float) -> None:
    tmp = f"{self.flag_path}.tmp"
    try:
      with open(tmp, "w") as f:
        f.write(f"{now:.3f}")
      os.replace(tmp, self.flag_path)
      self._last_flag_write = now
    except OSError:
      pass

  def _remove_flag(self) -> None:
    try:
      os.unlink(self.flag_path)
    except FileNotFoundError:
      pass


class VideoForwarder(threading.Thread):
  POLL_MS = 100

  def __init__(self, controller: VideoController):
    super().__init__(name="headunit-video", daemon=True)
    self.controller = controller
    self._exit = threading.Event()
    self._last_encode_id: dict[str, int] = {}

  def stop(self) -> None:
    self._exit.set()
    self.controller.changed.set()

  def run(self) -> None:
    socks: dict[str, messaging.SubSocket] = {}
    poller = None
    while not self._exit.is_set():
      cameras = self.controller.cameras()
      if set(socks) != cameras:
        socks = {c: socks[c] if c in socks else messaging.sub_sock(CAMERAS[c], conflate=False) for c in cameras}
        poller = messaging.Poller()
        for sock in socks.values():
          poller.registerSocket(sock)
        self._last_encode_id.clear()

      if not socks:
        self.controller.changed.wait(0.5)
        self.controller.changed.clear()
        continue

      for sock in poller.poll(self.POLL_MS):
        for dat in messaging.drain_sock_raw(sock):
          try:
            self.dispatch(messaging.log_from_bytes(dat))
          except Exception:
            cloudlog.exception("headunitd: bad encode packet")

  def dispatch(self, msg) -> None:
    service = msg.which()
    camera = SERVICE_TO_CAMERA.get(service)
    if camera is None:
      return
    encode_data = getattr(msg, service)
    idx = encode_data.idx

    prev = self._last_encode_id.get(camera)
    self._last_encode_id[camera] = idx.encodeId
    gap = prev is not None and idx.encodeId != prev + 1

    frame = EncodedFrame(
      camera=camera,
      keyframe=bool(idx.flags & V4L2_BUF_FLAG_KEYFRAME),
      pts_us=idx.timestampEof // 1000,
      payload=bytes(encode_data.header) + bytes(encode_data.data),
    )
    for viewer in self.controller.viewers_for(camera):
      if gap:
        viewer.resync()
      viewer.offer(frame)
