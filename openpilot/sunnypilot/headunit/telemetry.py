"""
Turns openpilot/sunnypilot messages into the JSON snapshot the head unit consumes.

Units are SI (m, m/s, m/s^2, deg). Non-finite floats become null so the output is always
valid JSON. Geometry follows openpilot frames:
  - modelV2 lane lines, road edges and path: device frame, x forward, y RIGHT positive
  - radarState leads: yRel is LEFT positive (radard negates the model's y)
"""
import math

from openpilot.sunnypilot.headunit.config import HeadUnitConfig

SERVICES = [
  "carState", "carStateSP", "selfdriveState", "selfdriveStateSP", "radarState",
  "longitudinalPlan", "longitudinalPlanSP", "driverMonitoringState", "deviceState",
  "modelV2", "liveMapDataSP",
]

# copied from selfdrive/controls/lib/longitudinal_mpc_lib/long_mpc.py so this process doesn't
# import the acados solver; test_telemetry cross-checks them against the real module
COMFORT_BRAKE = 2.5
STOP_DISTANCE = 6.0
T_FOLLOW = {"aggressive": 1.25, "standard": 1.45, "relaxed": 1.75}

GEOMETRY_STEP = 2  # 33 model points -> 17 sent
SCHEMA_VERSION = 1


def desired_follow_distance(v_ego: float, v_lead: float, t_follow: float) -> float:
  return (v_ego ** 2 - v_lead ** 2) / (2 * COMFORT_BRAKE) + t_follow * v_ego + STOP_DISTANCE


def enum_int(value) -> int:
  return int(getattr(value, "raw", value))


def _f(value, nd: int = 3):
  value = float(value)
  return round(value, nd) if math.isfinite(value) else None


def _fl(values, nd: int = 2, step: int = 1) -> list:
  return [_f(v, nd) for v in list(values)[::step]]


class TelemetryCollector:
  def __init__(self, config: HeadUnitConfig):
    self.config = config
    self.seq = 0

  def build(self, sm, now: float, is_metric: bool, video_status: dict) -> dict:
    self.seq += 1
    return {
      "v": SCHEMA_VERSION,
      "seq": self.seq,
      "t": round(now, 3),
      "started": bool(sm["deviceState"].started),
      "isMetric": is_metric,
      "alive": {s: bool(sm.alive[s]) for s in SERVICES},
      "engage": self._engage(sm),
      "car": self._car(sm),
      "steer": self._steer(sm),
      "lead": self._lead(sm),
      "plan": self._plan(sm),
      "dm": self._dm(sm),
      "device": self._device(sm),
      "model": self._model(sm),
      "map": self._map(sm),
      "video": video_status,
    }

  @staticmethod
  def guard_inputs(sm) -> tuple[int, bool, float, float]:
    md = sm["modelV2"]
    return enum_int(sm["deviceState"].thermalStatus), bool(sm.alive["modelV2"]), md.modelExecutionTime, md.frameDropPerc

  @staticmethod
  def _engage(sm) -> dict:
    ss = sm["selfdriveState"]
    mads = sm["selfdriveStateSP"].mads
    return {
      "state": str(ss.state),
      "enabled": ss.enabled,
      "active": ss.active,
      "engageable": ss.engageable,
      "alertText1": ss.alertText1,
      "alertText2": ss.alertText2,
      "alertStatus": str(ss.alertStatus),
      "alertSize": str(ss.alertSize),
      "personality": str(ss.personality),
      "mads": {
        "state": str(mads.state),
        "enabled": mads.enabled,
        "active": mads.active,
        "available": mads.available,
      },
    }

  @staticmethod
  def _car(sm) -> dict:
    cs = sm["carState"]
    cs_sp = sm["carStateSP"]
    cruise = cs.cruiseState
    return {
      "vEgo": _f(cs.vEgo),
      "aEgo": _f(cs.aEgo),
      "vEgoCluster": _f(cs.vEgoCluster),
      "vCruise": _f(cs.vCruise, 2),
      "vCruiseCluster": _f(cs.vCruiseCluster, 2),
      "cruise": {
        "enabled": cruise.enabled,
        "available": cruise.available,
        "speed": _f(cruise.speed, 2),
        "speedCluster": _f(cruise.speedCluster, 2),
        "standstill": cruise.standstill,
        "nonAdaptive": cruise.nonAdaptive,
      },
      "gasPressed": cs.gasPressed,
      "brakePressed": cs.brakePressed,
      "brakeHoldActive": cs.brakeHoldActive,
      "standstill": cs.standstill,
      "gear": str(cs.gearShifter),
      "leftBlindspot": cs.leftBlindspot,
      "rightBlindspot": cs.rightBlindspot,
      "doorOpen": cs.doorOpen,
      "seatbeltUnlatched": cs.seatbeltUnlatched,
      "stockAeb": cs.stockAeb,
      "stockFcw": cs.stockFcw,
      "accFaulted": cs.accFaulted,
      "distanceBars": cs_sp.distanceBars,
      "carSpeedLimit": _f(cs_sp.speedLimit, 2),
    }

  @staticmethod
  def _steer(sm) -> dict:
    cs = sm["carState"]
    return {
      "angleDeg": _f(cs.steeringAngleDeg, 2),
      "rateDeg": _f(cs.steeringRateDeg, 2),
      "torque": _f(cs.steeringTorque, 1),
      "torqueEps": _f(cs.steeringTorqueEps, 1),
      "pressed": cs.steeringPressed,
      "leftBlinker": cs.leftBlinker,
      "rightBlinker": cs.rightBlinker,
      "faultTemporary": cs.steerFaultTemporary,
      "faultPermanent": cs.steerFaultPermanent,
      "lowSpeedAlert": cs.lowSpeedAlert,
    }

  @staticmethod
  def _lead_data(lead) -> dict:
    return {
      "present": lead.present,
      "dRel": _f(lead.dRel, 2),
      "yRel": _f(lead.yRel, 2),
      "vRel": _f(lead.vRel, 2),
      "vLead": _f(lead.vLead, 2),
      "aLeadK": _f(lead.aLeadK, 2),
      "modelProb": _f(lead.modelProb, 2),
      "radar": lead.radar,
    }

  def _lead(self, sm) -> dict:
    rs = sm["radarState"]
    v_ego = float(sm["carState"].vEgo)
    lead = rs.leadOne
    t_follow = T_FOLLOW.get(str(sm["selfdriveState"].personality), T_FOLLOW["standard"])

    leads_v3 = sm["modelV2"].leadsV3
    x_std = None
    if len(leads_v3) and len(leads_v3[0].xStd):
      x_std = _f(leads_v3[0].xStd[0], 2)

    time_gap = desired = gap_error = None
    if lead.present:
      desired_m = desired_follow_distance(v_ego, float(lead.vLead), t_follow)
      desired = _f(desired_m, 2)
      gap_error = _f(float(lead.dRel) - desired_m, 2)
      if v_ego > 1.0:
        time_gap = _f(float(lead.dRel) / v_ego, 2)

    return {
      "one": self._lead_data(lead),
      "two": self._lead_data(rs.leadTwo),
      "modelDistStd": x_std,
      "tFollow": t_follow,
      "timeGapS": time_gap,
      "desiredDistM": desired,
      "gapErrorM": gap_error,
    }

  @staticmethod
  def _plan(sm) -> dict:
    lp = sm["longitudinalPlan"]
    sp = sm["longitudinalPlanSP"]
    scc = sp.smartCruiseControl
    resolver = sp.speedLimit.resolver
    assist = sp.speedLimit.assist
    return {
      "aTarget": _f(lp.aTarget),
      "speeds": _fl(lp.speeds),
      "hasLead": lp.hasLead,
      "fcw": lp.fcw,
      "source": str(lp.longitudinalPlanSource),
      "shouldStop": lp.shouldStop,
      "allowThrottle": lp.allowThrottle,
      "allowBrake": lp.allowBrake,
      "sp": {
        "source": str(sp.longitudinalPlanSource),
        "vTarget": _f(sp.vTarget, 2),
        "aTarget": _f(sp.aTarget),
        "sccVision": {"state": str(scc.vision.state), "active": scc.vision.active, "vTarget": _f(scc.vision.vTarget, 2)},
        "sccMap": {"state": str(scc.map.state), "active": scc.map.active, "vTarget": _f(scc.map.vTarget, 2)},
        "speedLimit": {
          "limit": _f(resolver.speedLimit, 2),
          "final": _f(resolver.speedLimitFinal, 2),
          "offset": _f(resolver.speedLimitOffset, 2),
          "valid": resolver.speedLimitValid,
          "source": str(resolver.source),
          "distM": _f(resolver.distToSpeedLimit, 1),
        },
        "assist": {"state": str(assist.state), "active": assist.active, "vTarget": _f(assist.vTarget, 2)},
        "greenLightAlert": sp.e2eAlerts.greenLightAlert,
        "leadDepartAlert": sp.e2eAlerts.leadDepartAlert,
      },
    }

  @staticmethod
  def _dm(sm) -> dict:
    dm = sm["driverMonitoringState"]
    vision = dm.visionPolicyState
    policy = str(dm.activePolicy)
    awareness = vision.awarenessPercent if policy == "vision" else dm.wheeltouchPolicyState.awarenessPercent
    types = vision.distractedTypes
    return {
      "alertLevel": str(dm.alertLevel),
      "policy": policy,
      "awarenessPercent": awareness,
      "distracted": vision.isDistracted,
      "distractedTypes": [name for name in ("pose", "eye", "phone", "sleep") if getattr(types, name)],
      "faceDetected": vision.faceDetected,
      "lockout": dm.lockout,
    }

  @staticmethod
  def _device(sm) -> dict:
    ds = sm["deviceState"]
    return {
      "thermalStatus": str(ds.thermalStatus),
      "maxTempC": _f(ds.maxTempC, 1),
      "cpuTempC": _fl(ds.cpuTempC, 1),
      "gpuTempC": _fl(ds.gpuTempC, 1),
      "cpuUsagePercent": list(ds.cpuUsagePercent),
      "memoryUsagePercent": ds.memoryUsagePercent,
      "gpuUsagePercent": ds.gpuUsagePercent,
      "fanSpeedPercent": ds.fanSpeedPercentDesired,
      "networkType": str(ds.networkType),
    }

  @staticmethod
  def _xy(xyzt) -> dict:
    return {"x": _fl(xyzt.x, 2, GEOMETRY_STEP), "y": _fl(xyzt.y, 2, GEOMETRY_STEP)}

  def _model(self, sm) -> dict:
    md = sm["modelV2"]
    probs, stds, edge_stds = list(md.laneLineProbs), list(md.laneLineStds), list(md.roadEdgeStds)
    lanes = []
    for i, line in enumerate(md.laneLines):
      lanes.append({**self._xy(line), "prob": _f(probs[i], 2) if i < len(probs) else None,
                    "std": _f(stds[i], 2) if i < len(stds) else None})
    edges = []
    for i, edge in enumerate(md.roadEdges):
      edges.append({**self._xy(edge), "std": _f(edge_stds[i], 2) if i < len(edge_stds) else None})
    return {
      "execTimeS": _f(md.modelExecutionTime, 4),
      "frameDropPerc": _f(md.frameDropPerc, 2),
      "confidence": str(md.confidence),
      "big": md.big,
      "laneChangeState": str(md.meta.laneChangeState),
      "laneLines": lanes,
      "roadEdges": edges,
      "path": self._xy(md.position),
    }

  @staticmethod
  def _map(sm) -> dict:
    lm = sm["liveMapDataSP"]
    return {
      "roadName": lm.roadName,
      "speedLimit": _f(lm.speedLimit, 2),
      "speedLimitValid": lm.speedLimitValid,
      "speedLimitAhead": _f(lm.speedLimitAhead, 2),
      "speedLimitAheadValid": lm.speedLimitAheadValid,
      "speedLimitAheadDistM": _f(lm.speedLimitAheadDistance, 1),
    }
