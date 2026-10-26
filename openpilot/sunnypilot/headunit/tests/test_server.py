import json

from openpilot.sunnypilot.headunit.config import HeadUnitConfig
from openpilot.sunnypilot.headunit.server import client_allowed, list_static_files


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
