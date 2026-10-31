import pytest

import openpilot.cereal.messaging as messaging

from openpilot.sunnypilot.headunit.log_export import LogExporter, read_shlog

SUBARU_DBC = "subaru_global_2017_generated"


def event(service: str, t: float, size: int | None = None):
  msg = messaging.new_message(service, size) if size is not None else messaging.new_message(service)
  msg.logMonoTime = int(1e9 + t * 1e9)
  return msg


def export(tmp_path, events, **kwargs):
  exporter = LogExporter(**kwargs)
  for msg in events:
    exporter.add(msg.as_reader())
  path = str(tmp_path / "out.shlog")
  exporter.write(path)
  return read_shlog(path)


class TestLogExport:
  def test_numbers_bools_and_valid(self, tmp_path):
    events = []
    for i, v in enumerate((1.0, 2.0, 3.0)):
      msg = event("carState", i * 0.01)
      msg.carState.vEgo = v
      msg.carState.gasPressed = i == 1
      events.append(msg)
    header, data = export(tmp_path, events, can="none")

    t, v = data["carState.vEgo"]
    assert list(v) == [1.0, 2.0, 3.0]
    assert t[0] == pytest.approx(0.0) and t[2] == pytest.approx(0.02, abs=1e-6)
    assert list(data["carState.gasPressed"][1]) == [0.0, 1.0, 0.0]
    assert "carState._valid" in data
    kinds = {c["name"]: c["kind"] for c in header["channels"]}
    assert kinds["carState.vEgo"] == "num" and kinds["carState.gasPressed"] == "bool"
    assert header["duration"] == pytest.approx(0.02, abs=1e-6)

  def test_runs_keep_only_endpoints(self, tmp_path):
    events = []
    for i, v in enumerate((0.0, 5.0, 5.0, 5.0, 5.0, 7.0)):
      msg = event("carState", i * 0.1)
      msg.carState.aEgo = v
      events.append(msg)
    _, data = export(tmp_path, events, can="none")
    t, v = data["carState.aEgo"]
    assert list(v) == [0.0, 5.0, 5.0, 7.0]
    assert [round(x, 3) for x in t] == [0.0, 0.1, 0.4, 0.5]

  def test_trailing_run_is_closed(self, tmp_path):
    events = []
    for i in range(4):
      msg = event("carState", i * 0.1)
      msg.carState.vEgo = 3.0
      events.append(msg)
    _, data = export(tmp_path, events, can="none")
    t, v = data["carState.vEgo"]
    assert list(v) == [3.0, 3.0]
    assert t[-1] == pytest.approx(0.3, abs=1e-6)

  def test_enums_have_labels(self, tmp_path):
    a = event("longitudinalPlan", 0.0)
    a.longitudinalPlan.longitudinalPlanSource = "cruise"
    b = event("longitudinalPlan", 0.05)
    b.longitudinalPlan.longitudinalPlanSource = "e2e"
    header, data = export(tmp_path, [a, b], can="none")
    ch = next(c for c in header["channels"] if c["name"] == "longitudinalPlan.longitudinalPlanSource")
    assert ch["kind"] == "enum"
    labels = ch["labels"]
    assert [labels[int(x)] for x in data[ch["name"]][1]] == ["cruise", "e2e"]

  def test_nested_struct_and_short_lists(self, tmp_path):
    msg = event("carControl", 0.0)
    msg.carControl.actuators.accel = -1.25
    msg.carControl.orientationNED = [0.0, 0.05, 1.0]
    _, data = export(tmp_path, [msg], can="none")
    assert data["carControl.actuators.accel"][1][0] == pytest.approx(-1.25)
    assert data["carControl.orientationNED[1]"][1][0] == pytest.approx(0.05)

  def test_long_lists_are_skipped(self, tmp_path):
    msg = event("longitudinalPlan", 0.0)
    msg.longitudinalPlan.accels = [0.1] * 33
    _, data = export(tmp_path, [msg], can="none", max_list=8)
    assert not any(name.startswith("longitudinalPlan.accels") for name in data)

  def test_text_changes_and_event_names(self, tmp_path):
    a = event("selfdriveState", 0.0)
    a.selfdriveState.alertText1 = "Pedal pressed"
    b = event("selfdriveState", 0.1)
    b.selfdriveState.alertText1 = "Pedal pressed"
    c = event("onroadEvents", 0.2, 2)
    c.onroadEvents[0].name = "pedalPressed"
    c.onroadEvents[1].name = "gasPressedOverride"
    header, _ = export(tmp_path, [a, b, c], can="none")
    texts = {x["name"]: x for x in header["texts"]}
    assert texts["selfdriveState.alertText1"]["v"] == ["Pedal pressed"]
    assert texts["onroadEvents"]["v"] == ["gasPressedOverride, pedalPressed"]

  def test_services_filter_and_include(self, tmp_path):
    a = event("carState", 0.0)
    a.carState.vEgo = 1.0
    b = event("deviceState", 0.0)
    b.deviceState.maxTempC = 50.0
    _, data = export(tmp_path, [a, b], can="none", services=frozenset({"carState"}), include=[r"vEgo$"])
    assert set(data) == {"carState.vEgo"}

  def test_bookmarks_become_marks(self, tmp_path):
    header, _ = export(tmp_path, [event("carState", 0.0), event("bookmarkButton", 1.5)], can="none")
    assert header["marks"] == [{"t": pytest.approx(1.5), "label": "bookmarkButton"}]

  def test_sendcan_decoded_with_dbc(self, tmp_path):
    from opendbc.can import CANPacker
    packer = CANPacker(SUBARU_DBC)
    addr, dat, bus = packer.make_can_msg("ES_Distance", 0, {"Cruise_Throttle": 2100, "Cruise_Brake_Active": 1})
    msg = event("sendcan", 0.0, 1)
    msg.sendcan[0].address = addr
    msg.sendcan[0].dat = bytes(dat)
    msg.sendcan[0].src = bus
    echo = event("can", 0.01, 1)
    echo.can[0].address = addr
    echo.can[0].dat = bytes(dat)
    echo.can[0].src = 128 + bus
    header, data = export(tmp_path, [msg, echo], can="ES_Distance", dbc=SUBARU_DBC)
    assert data["sendcan0.ES_Distance.Cruise_Throttle"][1][0] == 2100
    assert data["sendcan0.ES_Distance.Cruise_Brake_Active"][1][0] == 1
    assert not any(name.startswith("can") for name in data)  # TX echo ignored
    assert not any(name.endswith((".COUNTER", ".CHECKSUM")) for name in data)
    assert header["dbc"] == SUBARU_DBC
