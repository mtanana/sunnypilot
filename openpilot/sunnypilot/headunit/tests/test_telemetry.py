import json
import math

import pytest

import openpilot.cereal.messaging as messaging
from openpilot.sunnypilot.tests.fake_submaster import FakeSubMaster

from openpilot.sunnypilot.headunit.config import HeadUnitConfig
from openpilot.sunnypilot.headunit.hub import TelemetryHub
from openpilot.sunnypilot.headunit.telemetry import (
  COMFORT_BRAKE, GEOMETRY_STEP, SERVICES, STOP_DISTANCE, T_FOLLOW, TelemetryCollector, desired_follow_distance, enum_int,
)


def make_sm() -> FakeSubMaster:
  return FakeSubMaster({s: messaging.new_message(s) for s in SERVICES})


def build(sm) -> dict:
  return TelemetryCollector(HeadUnitConfig()).build(sm, now=1.0, is_metric=False, video_status={"active": False})


class TestTelemetry:
  def test_default_snapshot_is_strict_json(self):
    hub = TelemetryHub()
    hub.publish(build(make_sm()))
    snapshot = json.loads(hub.wait_next(0, timeout=0)[1])
    for key in ("engage", "car", "steer", "lead", "plan", "dm", "device", "model", "map", "video", "alive"):
      assert key in snapshot

  def test_non_finite_becomes_null(self):
    sm = make_sm()
    sm["carState"].vEgo = math.nan
    sm["carState"].aEgo = math.inf
    snapshot = build(sm)
    assert snapshot["car"]["vEgo"] is None
    assert snapshot["car"]["aEgo"] is None
    TelemetryHub().publish(snapshot)  # allow_nan=False would raise

  def test_enums_are_names(self):
    sm = make_sm()
    sm["carState"].gearShifter = "drive"
    sm["selfdriveState"].personality = "relaxed"
    sm["selfdriveStateSP"].mads.state = "paused"
    snapshot = build(sm)
    assert snapshot["car"]["gear"] == "drive"
    assert snapshot["engage"]["personality"] == "relaxed"
    assert snapshot["engage"]["mads"]["state"] == "paused"

  def test_lead_derived_values(self):
    sm = make_sm()
    sm["carState"].vEgo = 20.0
    sm["selfdriveState"].personality = "standard"
    lead = sm["radarState"].leadOne
    lead.present = True
    lead.dRel = 40.0
    lead.vLead = 18.0

    out = build(sm)["lead"]
    expected = desired_follow_distance(20.0, 18.0, T_FOLLOW["standard"])
    assert out["timeGapS"] == pytest.approx(2.0)
    assert out["tFollow"] == T_FOLLOW["standard"]
    assert out["desiredDistM"] == pytest.approx(expected, abs=0.01)
    assert out["gapErrorM"] == pytest.approx(40.0 - expected, abs=0.01)

  def test_no_lead_has_no_derived_values(self):
    sm = make_sm()
    sm["carState"].vEgo = 20.0
    out = build(sm)["lead"]
    assert out["one"]["present"] is False
    assert out["timeGapS"] is None and out["desiredDistM"] is None and out["gapErrorM"] is None

  def test_no_time_gap_when_stopped(self):
    sm = make_sm()
    sm["radarState"].leadOne.present = True
    sm["radarState"].leadOne.dRel = 5.0
    out = build(sm)["lead"]
    assert out["timeGapS"] is None
    assert out["desiredDistM"] == pytest.approx(STOP_DISTANCE)

  def test_geometry_is_downsampled(self):
    sm = make_sm()
    md = sm["modelV2"]
    for xyzt in md.init("laneLines", 4):
      xyzt.x = [float(i) for i in range(33)]
      xyzt.y = [0.5] * 33
    md.laneLineProbs = [0.9, 0.8, 0.7, 0.6]
    md.position.x = [float(i) for i in range(33)]
    md.position.y = [0.0] * 33

    model = build(sm)["model"]
    assert len(model["laneLines"]) == 4
    assert len(model["laneLines"][0]["x"]) == len(range(0, 33, GEOMETRY_STEP))
    assert model["laneLines"][0]["x"][1] == float(GEOMETRY_STEP)
    assert model["laneLines"][3]["prob"] == pytest.approx(0.6)
    assert model["laneLines"][0]["std"] is None  # stds missing -> null, not an IndexError
    assert len(model["path"]["x"]) == len(model["laneLines"][0]["x"])

  def test_dm_awareness_follows_policy(self):
    sm = make_sm()
    dm = sm["driverMonitoringState"]
    dm.visionPolicyState.awarenessPercent = 80
    dm.wheeltouchPolicyState.awarenessPercent = 30
    dm.visionPolicyState.distractedTypes.phone = True

    dm.activePolicy = "vision"
    assert build(sm)["dm"]["awarenessPercent"] == 80
    assert build(sm)["dm"]["distractedTypes"] == ["phone"]
    dm.activePolicy = "wheeltouch"
    assert build(sm)["dm"]["awarenessPercent"] == 30

  def test_guard_inputs(self):
    sm = make_sm()
    sm["deviceState"].thermalStatus = "overheated"
    sm["modelV2"].modelExecutionTime = 0.03
    thermal, alive, exec_s, _ = TelemetryCollector.guard_inputs(sm)
    assert thermal == 2 == enum_int(sm["deviceState"].thermalStatus)
    assert alive is True
    assert exec_s == pytest.approx(0.03)

  def test_constants_match_long_mpc(self):
    long_mpc = pytest.importorskip("openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc")
    from openpilot.cereal import log
    assert COMFORT_BRAKE == long_mpc.COMFORT_BRAKE
    assert STOP_DISTANCE == long_mpc.STOP_DISTANCE
    for name, t_follow in T_FOLLOW.items():
      assert t_follow == long_mpc.get_T_FOLLOW(getattr(log.LongitudinalPersonality, name))
    expected = long_mpc.get_safe_obstacle_distance(25.0, 1.45) - long_mpc.get_stopped_equivalence_factor(22.0)
    assert desired_follow_distance(25.0, 22.0, 1.45) == pytest.approx(expected)
