"""ROS 2 messages as Orion-LD's DDS bridge stores them → the flat values the KPI / alert logic uses.

Each mapped topic is one NGSI-LD attribute whose value is the whole ROS message (fiware/orion/config-dds.json):
Robot.battery (sensor_msgs/BatteryState), Robot.odometry (nav_msgs/Odometry), Robot.status
(aura_interfaces/ArtabanStatus), AirQualityObserved.reading (aura_interfaces/EnvironmentReading),
HRIEvent:intent.intentEvent (aura_interfaces/IntentEvent).
"""

from __future__ import annotations

DECISIONS = {0: "ACCEPT", 1: "CONFIRM", 2: "REJECT", 3: "IGNORE"}
CHARGING = 1  # sensor_msgs/BatteryState.POWER_SUPPLY_STATUS_CHARGING


def _msg(v) -> dict:
    return v if isinstance(v, dict) else {}


def robot(entity: dict) -> dict:
    """Simplified Robot entity → flat robot values (batteryLevel %, jointTemperatures, …)."""
    b, s, o = _msg(entity.get("battery")), _msg(entity.get("status")), _msg(entity.get("odometry"))
    out: dict = {"id": entity.get("id"), "_observedAt": entity.get("_observedAt")}
    if b:
        out.update({
            "batteryLevel": round(100.0 * float(b.get("percentage", 0.0)), 2),
            "batteryVoltage": b.get("voltage"), "batteryCurrent": b.get("current"),
            "charging": b.get("power_supply_status") == CHARGING,
        })
    if s:
        names, temps = s.get("joint_names") or [], s.get("joint_temperatures") or []
        out.update({
            "missionId": s.get("mission_id") or None, "missionState": s.get("mission_state"),
            "operationMode": s.get("operation_mode"), "safetyState": s.get("safety_state"),
            "eStop": bool(s.get("e_stop")), "odometry": s.get("odometry_m"),
            "jointTemperatures": dict(zip(names, temps)), "maxJointTemperature": s.get("max_joint_temperature"),
            "linkLatency": s.get("link_latency_ms"), "packetLoss": s.get("packet_loss"),
        })
    if o:
        out["speed"] = _msg(_msg(o.get("twist")).get("twist")).get("linear", {}).get("x")
    return out


def environment(entity: dict) -> dict:
    """Simplified AirQualityObserved entity (reading = EnvironmentReading) → flat environment values."""
    r = _msg(entity.get("reading"))
    return {
        "id": entity.get("id"), "_observedAt": entity.get("_observedAt"),
        "h2s": r.get("h2s_ppm"), "ch4": r.get("ch4_lel"), "co": r.get("co_ppm"), "o2": r.get("o2_vol"),
        "thermalMax": r.get("thermal_max"), "LAeq": r.get("la_eq"), "LAmax": r.get("la_max"),
        "noiseSource": r.get("noise_source"),
    }


# Fields the robot adapter may fill in later by publishing the same IntentEvent again (same
# event_id and header.stamp): it knows whether a stop was unintended and how long the robot took to
# come to rest.
FEEDBACK = ("outcome", "correct", "timeToSafeStopMs", "recoveryTimeS")
# event_id restarts with aura_intent: without a header stamp, only events this close in time match.
FEEDBACK_WINDOW_S = 600.0
STAMP_TOLERANCE_S = 0.001


def event_key(ev: dict) -> tuple:
    return ev.get("missionId"), ev.get("eventId")


def header_stamp(msg: dict) -> float | None:
    """header.stamp of a ROS message as Orion-LD stores it (sec/nanosec), in seconds; None when unset."""
    stamp = _msg(_msg(msg.get("header")).get("stamp"))
    try:
        t = float(stamp.get("sec", stamp.get("secs", 0)) or 0) + \
            float(stamp.get("nanosec", stamp.get("nsecs", 0)) or 0) / 1e9
    except (TypeError, ValueError):
        return None
    return t if t > 0 else None


def same_event(a: dict, b: dict, t: str = "_rx") -> bool:
    """True when ``b`` is ``a`` published again (feedback or a repeated notification).

    The decision's header stamp tells an event apart from a new one that reuses its event_id after an
    aura_intent restart; the adapter keeps the original stamp when it re-publishes. Without a stamp on
    both sides, only events within FEEDBACK_WINDOW_S of each other match.
    """
    if event_key(a) != event_key(b):
        return False
    sa, sb = a.get("stamp"), b.get("stamp")
    if sa is not None and sb is not None:
        return abs(float(sa) - float(sb)) <= STAMP_TOLERANCE_S
    return abs(float(a.get(t, 0)) - float(b.get(t, 0))) <= FEEDBACK_WINDOW_S


def apply_feedback(target: dict, update: dict) -> bool:
    """Copy feedback fields that ``update`` sets onto ``target``; True when something changed."""
    changed = False
    for k in FEEDBACK:
        v = update.get(k)
        if v in (None, "") or (k == "correct" and not v and not update.get("outcome")):
            continue
        if target.get(k) != v:
            target[k] = v
            changed = True
    return changed


def merge_feedback(events: list[dict], t: str = "_rx") -> list[dict]:
    """Collapse re-published events (oldest first) into the first one that is the same event."""
    seen: dict[tuple, list[dict]] = {}
    out = []
    for ev in events:
        candidates = seen.setdefault(event_key(ev), [])
        prev = next((p for p in reversed(candidates) if same_event(p, ev, t)), None)
        if prev is not None:
            apply_feedback(prev, ev)
            continue
        candidates.append(ev)
        out.append(ev)
    return out


def intent_event(msg: dict) -> dict | None:
    """aura_interfaces/IntentEvent → the event dict used by kpi.compute (camelCase, decision as a word)."""
    msg = _msg(msg)
    if not msg.get("event_id"):
        return None
    ev = {
        "eventId": msg["event_id"], "missionId": msg.get("mission_id"), "intent": msg.get("intent"),
        "utterance": msg.get("utterance"), "gesture": msg.get("gesture") or None, "modality": msg.get("modality"),
        "decision": DECISIONS.get(msg.get("decision"), "IGNORE"), "outcome": msg.get("outcome"),
        "correct": bool(msg.get("correct")), "confidence": float(msg.get("confidence", 0.0)),
        "threshold": float(msg.get("accept_threshold", 0.0)), "latencyMs": float(msg.get("latency_ms", 0.0)),
        "noiseDb": float(msg.get("noise_db", 0.0)), "condition": msg.get("acoustic_condition"),
        "operatorId": msg.get("operator_id"), "engaged": bool(msg.get("engaged")),
        "safetyCritical": bool(msg.get("safety_critical")),
    }
    stamp = header_stamp(msg)
    if stamp is not None:
        ev["stamp"] = stamp
    if msg.get("time_to_safe_stop_ms"):
        ev["timeToSafeStopMs"] = float(msg["time_to_safe_stop_ms"])
    if msg.get("recovery_time_s"):
        ev["recoveryTimeS"] = float(msg["recovery_time_s"])
    return ev
