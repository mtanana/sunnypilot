import time

import pytest

import openpilot.cereal.messaging as messaging
from opendbc.car.structs import car

from openpilot.sunnypilot.headunit import process_hooks
from openpilot.sunnypilot.headunit.config import HeadUnitConfig
from openpilot.sunnypilot.headunit.guards import THERMAL_OVERHEATED, LoadGuard
from openpilot.sunnypilot.headunit.video import (
  FRAME_HEADER, V4L2_BUF_FLAG_KEYFRAME, EncodedFrame, VideoController, VideoForwarder, VideoUnavailable, Viewer,
)


def frame(keyframe: bool, n: int = 0) -> EncodedFrame:
  return EncodedFrame("road", keyframe, n, bytes([n % 256]))


class FakeParams:
  def __init__(self, live_streaming: bool = False):
    self.live_streaming = live_streaming

  def get_bool(self, key: str) -> bool:
    assert key == "IsLiveStreaming"
    return self.live_streaming


@pytest.fixture
def flag(tmp_path, monkeypatch):
  path = str(tmp_path / "headunit_video")
  monkeypatch.setattr(process_hooks, "VIDEO_FLAG_PATH", path)
  return path


@pytest.fixture
def controller(flag):
  cfg = HeadUnitConfig()
  return VideoController(cfg, LoadGuard(cfg), flag_path=flag)


class TestViewer:
  def test_waits_for_keyframe(self):
    v = Viewer("road", 0.0)
    v.offer(frame(False, 1))
    assert v.get(0) is None
    v.offer(frame(True, 2))
    v.offer(frame(False, 3))
    assert v.get(0).pts_us == 2
    assert v.get(0).pts_us == 3

  def test_overflow_drops_to_next_keyframe(self):
    v = Viewer("road", 0.0)
    v.offer(frame(True, 0))
    for i in range(1, Viewer.QUEUE_FRAMES + 5):
      v.offer(frame(False, i))
    v.offer(frame(False, 100))
    assert v.get(0) is None  # queue was cleared, deltas refused
    v.offer(frame(True, 200))
    assert v.get(0).pts_us == 200

  def test_close_wakes_reader(self):
    v = Viewer("road", 0.0)
    v.close("idle")
    assert v.get(0) is None
    assert v.closed_reason == "idle"

  def test_framed_header(self):
    f = EncodedFrame("road", True, 123456, b"abc")
    length, flags, pts = FRAME_HEADER.unpack(f.framed()[:FRAME_HEADER.size])
    assert (length, flags, pts) == (3, 1, 123456)
    assert f.framed()[FRAME_HEADER.size:] == b"abc"


class TestVideoController:
  def test_refuses_offroad_and_unknown_camera(self, controller):
    controller.tick(0.0, started=False, live_streaming=False)
    with pytest.raises(VideoUnavailable) as e:
      controller.open_viewer("road", 0.0)
    assert e.value.reason == "offroad"
    controller.tick(0.0, started=True, live_streaming=False)
    with pytest.raises(VideoUnavailable) as e:
      controller.open_viewer("rear", 0.0)
    assert e.value.reason == "unknown_camera"

  def test_session_lifecycle_and_flag(self, controller, flag):
    controller.tick(0.0, True, False)
    viewer = controller.open_viewer("road", 0.0)
    assert not controller.active
    controller.tick(0.1, True, False)
    assert controller.active
    assert controller.cameras() == {"road"}
    assert process_hooks.video_requested(now=0.2)
    assert not process_hooks.video_requested(now=0.1 + process_hooks.VIDEO_FLAG_TTL_S + 0.1)

    controller.close_viewer(viewer, 1.0)
    controller.tick(1.0 + controller.config.video_idle_stop_s - 0.5, True, False)
    assert controller.active
    controller.tick(1.0 + controller.config.video_idle_stop_s + 0.5, True, False)
    assert not controller.active
    assert controller.status(10.0)["lastStopReason"] == "idle"
    assert not process_hooks.video_requested(now=10.0)

  def test_flag_refreshed_while_active(self, controller):
    controller.tick(0.0, True, False)
    viewer = controller.open_viewer("road", 0.0)
    viewer.frames_sent = 1
    for i in range(1, 30):
      controller.tick(i * 0.5, True, False)
    assert process_hooks.video_requested(now=14.6)

  @pytest.mark.parametrize("started,live,reason", [(False, False, "offroad"), (True, True, "comma_connect")])
  def test_stops_for_conditions(self, controller, started, live, reason):
    controller.tick(0.0, True, False)
    viewer = controller.open_viewer("road", 0.0)
    controller.tick(0.1, True, False)
    controller.tick(0.2, started, live)
    assert not controller.active
    assert viewer.closed_reason == reason

  def test_session_limit(self, controller):
    controller.tick(0.0, True, False)
    viewer = controller.open_viewer("road", 0.0)
    viewer.frames_sent = 1
    controller.tick(0.0, True, False)
    controller.tick(controller.config.video_max_session_s + 1, True, False)
    assert viewer.closed_reason == "session_limit"

  def test_user_stop(self, controller):
    controller.tick(0.0, True, False)
    viewer = controller.open_viewer("road", 0.0)
    controller.tick(0.1, True, False)
    controller.request_stop()
    controller.tick(0.2, True, False)
    assert viewer.closed_reason == "user"

  def test_guard_stop_sets_cooldown(self, controller):
    controller.tick(0.0, True, False)
    controller.open_viewer("road", 0.0)
    controller.tick(0.1, True, False)
    controller.guard.add_sample(0.2, THERMAL_OVERHEATED, True, 0.01, 0.0)
    controller.tick(0.2, True, False)
    assert controller.status(0.2)["lastStopReason"] == "thermal"
    with pytest.raises(VideoUnavailable) as e:
      controller.open_viewer("road", 1.0)
    assert e.value.reason == "cooldown"
    controller.open_viewer("road", 0.2 + controller.config.video_cooldown_s + 1)

  def test_viewer_without_frames_is_dropped(self, controller):
    controller.tick(0.0, True, False)
    viewer = controller.open_viewer("road", 0.0)
    controller.tick(0.1, True, False)
    controller.tick(controller.config.video_first_frame_timeout_s + 1, True, False)
    assert viewer.closed_reason == "no_frames"


class TestForwarder:
  def encode_msg(self, encode_id: int, keyframe: bool):
    msg = messaging.new_message("livestreamNarrowRoadEncodeData")
    ed = msg.livestreamNarrowRoadEncodeData
    ed.idx.encodeId = encode_id
    ed.idx.flags = V4L2_BUF_FLAG_KEYFRAME if keyframe else 0
    ed.idx.timestampEof = 5_000_000 + encode_id
    ed.header = b"SPS" if keyframe else b""
    ed.data = b"D"
    return msg

  def test_dispatch_and_gap_resync(self, controller):
    controller.tick(0.0, True, False)
    viewer = controller.open_viewer("road", 0.0)
    controller.tick(0.1, True, False)
    forwarder = VideoForwarder(controller)

    forwarder.dispatch(self.encode_msg(0, True))
    forwarder.dispatch(self.encode_msg(1, False))
    first = viewer.get(0)
    assert first.keyframe and first.payload == b"SPSD" and first.pts_us == 5000
    assert viewer.get(0).payload == b"D"

    forwarder.dispatch(self.encode_msg(5, False))  # gap: wait for keyframe
    assert viewer.get(0) is None
    forwarder.dispatch(self.encode_msg(6, True))
    assert viewer.get(0).keyframe


class TestProcessHook:
  def test_headunit_video(self, flag):
    CP = car.CarParams.new_message()
    with open(flag, "w") as f:
      f.write("0")
    assert not process_hooks.headunit_video(False, FakeParams(), CP)

    with open(flag, "w") as f:
      f.write(f"{time.monotonic():.3f}")
    assert process_hooks.headunit_video(True, FakeParams(), CP)
    assert not process_hooks.headunit_video(True, FakeParams(live_streaming=True), CP)

    CP.notCar = True
    assert not process_hooks.headunit_video(True, FakeParams(), CP)

  def test_cmdline_sets_bitrate(self):
    cmd = process_hooks.headunit_encoderd_cmdline()
    assert cmd[0] == "env" and cmd[1].startswith("STREAM_BITRATE=") and cmd[-2:] == ["./encoderd", "--stream"]
