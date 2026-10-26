"""
HTTP endpoints for the head unit bridge (stdlib only, one thread per connection).

  GET  /                      test page (static files under web/)
  GET  /status                bridge + video status
  GET  /snapshot              one telemetry snapshot (JSON)
  GET  /telemetry?hz=N        Server-Sent Events stream of snapshots (1..telemetry_max_hz)
  GET  /video?camera=road|wide|driver&format=framed|raw
                              H.264 stream; connecting starts video, the last viewer leaving stops it
  POST /video/stop            stop video now

No login. Requests from public (globally routable) addresses are refused unless
allow_public_clients is set.
"""
import ipaddress
import json
import mimetypes
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from openpilot.common.swaglog import cloudlog

from openpilot.sunnypilot.headunit.config import HeadUnitConfig
from openpilot.sunnypilot.headunit.hub import TelemetryHub
from openpilot.sunnypilot.headunit.video import VideoController, VideoUnavailable

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
SSE_KEEPALIVE_S = 2.0
CONTENT_TYPES = {".js": "text/javascript", ".css": "text/css", ".html": "text/html", ".svg": "image/svg+xml"}


def client_allowed(address: str, allow_public: bool) -> bool:
  if allow_public:
    return True
  try:
    ip = ipaddress.ip_address(address)
  except ValueError:
    return False
  if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
    ip = ip.ipv4_mapped
  return not ip.is_global


def list_static_files(root: str = WEB_DIR) -> dict[str, str]:
  """Map of URL path -> file path for everything under web/. Only these are ever served."""
  files = {}
  for dirpath, _, names in os.walk(root):
    for name in names:
      full = os.path.join(dirpath, name)
      rel = os.path.relpath(full, root).replace(os.sep, "/")
      files[f"/{rel}"] = full
  if "/index.html" in files:
    files["/"] = files["/index.html"]
  return files


class HeadUnitServer(ThreadingHTTPServer):
  daemon_threads = True
  allow_reuse_address = True

  def __init__(self, config: HeadUnitConfig, hub: TelemetryHub, video: VideoController, status_fn):
    self.config = config
    self.hub = hub
    self.video = video
    self.status_fn = status_fn
    self.static_files = list_static_files()
    super().__init__((config.bind, config.port), HeadUnitRequestHandler)


class HeadUnitRequestHandler(BaseHTTPRequestHandler):
  # HTTP/1.0: every response ends by closing the connection, which is what the streams need
  protocol_version = "HTTP/1.0"
  server: HeadUnitServer

  def do_GET(self) -> None:
    self._dispatch("GET")

  def do_POST(self) -> None:
    self._dispatch("POST")

  def log_message(self, format: str, *args: object) -> None:  # noqa: A002  # stdlib override
    pass

  def _dispatch(self, method: str) -> None:
    if not client_allowed(self.client_address[0], self.server.config.allow_public_clients):
      self._json({"error": "forbidden"}, 403)
      return

    url = urlparse(self.path)
    query = parse_qs(url.query)
    try:
      if method == "GET" and url.path == "/status":
        self._json(self.server.status_fn())
      elif method == "GET" and url.path == "/snapshot":
        self._snapshot()
      elif method == "GET" and url.path == "/telemetry":
        self._telemetry(query)
      elif method == "GET" and url.path == "/video":
        self._video(query)
      elif method == "POST" and url.path == "/video/stop":
        self.server.video.request_stop("user")
        self._json({"ok": True})
      elif method == "GET" and url.path in self.server.static_files:
        self._static(self.server.static_files[url.path])
      else:
        self._json({"error": "not found"}, 404)
    except (BrokenPipeError, ConnectionResetError, TimeoutError):
      pass
    except Exception:
      cloudlog.exception(f"headunitd: error handling {method} {url.path}")

  def _headers(self, status: int, content_type: str, length: int | None = None) -> None:
    self.send_response(status)
    self.send_header("Content-Type", content_type)
    self.send_header("Cache-Control", "no-store")
    self.send_header("Access-Control-Allow-Origin", "*")
    if length is not None:
      self.send_header("Content-Length", str(length))
    self.end_headers()

  def _json(self, obj, status: int = 200) -> None:
    body = json.dumps(obj).encode()
    self._headers(status, "application/json", len(body))
    self.wfile.write(body)

  def _static(self, path: str) -> None:
    ext = os.path.splitext(path)[1]
    content_type = CONTENT_TYPES.get(ext) or mimetypes.guess_type(path)[0] or "application/octet-stream"
    with open(path, "rb") as f:
      body = f.read()
    self._headers(200, content_type, len(body))
    self.wfile.write(body)

  def _snapshot(self) -> None:
    payload = self.server.hub.fresh_snapshot(timeout=2.0)
    if payload is None:
      self._json({"error": "no data"}, 503)
      return
    self._headers(200, "application/json", len(payload))
    self.wfile.write(payload)

  def _telemetry(self, query: dict) -> None:
    max_hz = self.server.config.telemetry_max_hz
    try:
      hz = min(max(float(query.get("hz", [max_hz])[0]), 0.5), max_hz)
    except ValueError:
      hz = max_hz
    interval = 1.0 / hz

    hub = self.server.hub
    self._headers(200, "text/event-stream")
    hub.subscribe()
    try:
      seq, next_send = 0, 0.0
      while True:
        seq, payload = hub.wait_next(seq, timeout=SSE_KEEPALIVE_S)
        if payload is None:
          self.wfile.write(b": keepalive\n\n")
          self.wfile.flush()
          continue
        now = time.monotonic()
        if now < next_send:
          continue
        next_send = now + interval * 0.9  # small slack so 20 Hz requests don't alias down to 10 Hz
        self.wfile.write(b"data: " + payload + b"\n\n")
        self.wfile.flush()
    finally:
      hub.unsubscribe()

  def _video(self, query: dict) -> None:
    camera = query.get("camera", [self.server.config.video_default_camera])[0]
    raw = query.get("format", ["framed"])[0] == "raw"
    video = self.server.video
    try:
      viewer = video.open_viewer(camera, time.monotonic())
    except VideoUnavailable as e:
      self._json({"error": "video unavailable", "reason": e.reason}, 409)
      return

    try:
      self._headers(200, "video/h264" if raw else "application/octet-stream")
      self.wfile.flush()
      while True:
        frame = viewer.get(timeout=1.0)
        if frame is None:
          if viewer.closed_reason is not None:
            break
          continue
        self.wfile.write(frame.payload if raw else frame.framed())
        self.wfile.flush()
        viewer.frames_sent += 1
    finally:
      video.close_viewer(viewer, time.monotonic())
