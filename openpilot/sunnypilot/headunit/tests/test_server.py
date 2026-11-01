import http.client
import json
import threading

from openpilot.sunnypilot.headunit.config import HeadUnitConfig
from openpilot.sunnypilot.headunit.server import HeadUnitServer, client_allowed, list_static_files


class TestConfig:
  def test_missing_file_gives_defaults(self, tmp_path):
    assert HeadUnitConfig.load(str(tmp_path / "missing.json")) == HeadUnitConfig()

  def test_overrides_and_bad_values(self, tmp_path):
    path = tmp_path / "headunit.json"
    path.write_text(json.dumps({"port": 9000, "video_bitrate": 1e6, "video_max_session_s": "oops",
                                "video_default_camera": "rear", "unknown": 1}))
    cfg = HeadUnitConfig.load(str(path))
    assert cfg.port == 9000
    assert cfg.video_bitrate == 1_000_000 and isinstance(cfg.video_bitrate, int)
    assert cfg.video_max_session_s == HeadUnitConfig().video_max_session_s
    assert cfg.video_default_camera == "road"

  def test_invalid_json(self, tmp_path):
    path = tmp_path / "headunit.json"
    path.write_text("{not json")
    assert HeadUnitConfig.load(str(path)) == HeadUnitConfig()


class TestServerHelpers:
  def test_client_allowed(self):
    for ip in ("127.0.0.1", "192.168.43.10", "10.0.0.5", "172.16.1.1", "169.254.3.3", "100.101.102.103", "::1", "::ffff:192.168.1.2"):
      assert client_allowed(ip, False), ip
    for ip in ("8.8.8.8", "2001:4860:4860::8888", "::ffff:8.8.8.8", "garbage"):
      assert not client_allowed(ip, False), ip
    assert client_allowed("8.8.8.8", True)

  def test_static_files(self):
    files = list_static_files()
    for path in ("/", "/index.html", "/css/app.css", "/js/main.js", "/js/VideoPlayer.js"):
      assert path in files
    assert not any(".." in p for p in files)


class TestCors:
  def test_preflight(self):
    server = HeadUnitServer(HeadUnitConfig(bind="127.0.0.1", port=0), None, None, None, lambda: {"ok": True})
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
      conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
      conn.request("OPTIONS", "/telemetry?hz=20", headers={
        "Origin": "http://localhost:1234",
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "cache-control",
        "Access-Control-Request-Private-Network": "true",
      })
      resp = conn.getresponse()
      assert resp.status == 204
      assert resp.getheader("Access-Control-Allow-Origin") == "*"
      assert resp.getheader("Access-Control-Allow-Headers") == "cache-control"
      assert resp.getheader("Access-Control-Allow-Private-Network") == "true"
      assert "POST" in resp.getheader("Access-Control-Allow-Methods")
      conn.close()

      conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
      conn.request("GET", "/status")
      resp = conn.getresponse()
      assert resp.status == 200 and resp.getheader("Access-Control-Allow-Origin") == "*"
      conn.close()
    finally:
      server.shutdown()
      server.server_close()
