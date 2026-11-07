"""Threshold alert rules with debounce + hysteresis, persisted as NGSI-LD Alert entities."""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from typing import Callable

from .ngsi import Orion, now_iso, prop, rel, simplify

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Rule:
    id: str
    category: str
    severity: str
    source: str                         # which flattened entity the rule reads: robot | air | session
    trigger: Callable[[dict], bool]
    clear: Callable[[dict], bool]
    value: Callable[[dict], float | str]
    threshold: str
    describe: Callable[[dict], str]
    hold_s: float = 5.0


def _n(d: dict, k: str) -> float:
    v = d.get(k)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0


RULES: list[Rule] = [
    Rule("rejectionRateSpike", "hri", "high", "session",
         lambda s: _n(s, "windowRejectionRate") > 10, lambda s: _n(s, "windowRejectionRate") < 7,
         lambda s: _n(s, "windowRejectionRate"), "> 10 % (5 min window)",
         lambda s: f"Rejection rate reached {_n(s, 'windowRejectionRate'):.1f}% in the last 5 min. "
                   "Likely acoustic degradation (noise/PPE); gesture or fused fallback recommended.", 0),
    Rule("confirmationLoopsElevated", "hri", "medium", "session",
         lambda s: _n(s, "commandCount") >= 10 and _n(s, "confirmationLoopRate") > 2.0,
         lambda s: _n(s, "confirmationLoopRate") < 1.7,
         lambda s: _n(s, "confirmationLoopRate"), "> 2.0 per 10 commands",
         lambda s: f"Confirmation loops averaging {_n(s, 'confirmationLoopRate'):.1f} per 10 commands; "
                   "operator is repeatedly asked to confirm intents.", 0),
    Rule("latencyAbnormal", "hri", "medium", "session",
         lambda s: _n(s, "commandCount") >= 10 and _n(s, "p95Latency") > 600, lambda s: _n(s, "p95Latency") < 540,
         lambda s: _n(s, "p95Latency"), "p95 > 600 ms",
         lambda s: f"End-to-end command latency p95 is {_n(s, 'p95Latency'):.0f} ms.", 0),
    Rule("falseActivationHigh", "hri", "high", "session",
         lambda s: _n(s, "commandCount") >= 20 and _n(s, "falseActivationRate") > 5, lambda s: _n(s, "falseActivationRate") < 4,
         lambda s: _n(s, "falseActivationRate"), "> 5 %",
         lambda s: f"False activation rate {_n(s, 'falseActivationRate'):.1f}% — engagement gating letting unaddressed speech through.", 0),
    Rule("confidenceDrift", "hri", "low", "session",
         lambda s: _n(s, "commandCount") >= 15 and _n(s, "meanConfidence") < 0.70, lambda s: _n(s, "meanConfidence") > 0.73,
         lambda s: _n(s, "meanConfidence"), "< 0.70",
         lambda s: f"Mean acoustic confidence dropped to {_n(s, 'meanConfidence'):.2f}; possible model/site drift.", 0),
    Rule("batteryCritical", "robot", "critical", "robot",
         lambda r: 0 < _n(r, "batteryLevel") < 12, lambda r: _n(r, "batteryLevel") > 15,
         lambda r: _n(r, "batteryLevel"), "< 12 %",
         lambda r: f"Battery critical at {_n(r, 'batteryLevel'):.0f}%. Return to dock immediately."),
    Rule("batteryLow", "robot", "medium", "robot",
         lambda r: 12 <= _n(r, "batteryLevel") < 25 and not r.get("charging"), lambda r: _n(r, "batteryLevel") > 30 or bool(r.get("charging")),
         lambda r: _n(r, "batteryLevel"), "< 25 %",
         lambda r: f"Battery low at {_n(r, 'batteryLevel'):.0f}%; plan return to dock."),
    Rule("jointOverTemp", "robot", "high", "robot",
         lambda r: _n(r, "maxJointTemperature") > 70, lambda r: _n(r, "maxJointTemperature") < 65,
         lambda r: _n(r, "maxJointTemperature"), "> 70 °C",
         lambda r: "Joint over-temperature: " + ", ".join(
             f"{j} {t:.0f} °C" for j, t in sorted((r.get("jointTemperatures") or {}).items(), key=lambda x: -x[1])[:2])),
    Rule("eStopEngaged", "robot", "critical", "robot",
         lambda r: r.get("eStop") is True, lambda r: r.get("eStop") is False,
         lambda r: "engaged", "eStop == true",
         lambda r: "Emergency/protective stop engaged on Artaban. Operator acknowledgement required.", 0),
    Rule("commsDegraded", "robot", "medium", "robot",
         lambda r: _n(r, "packetLoss") > 5 or _n(r, "linkLatency") > 250,
         lambda r: _n(r, "packetLoss") < 3 and _n(r, "linkLatency") < 180,
         lambda r: _n(r, "linkLatency"), "loss > 5 % or latency > 250 ms",
         lambda r: f"Comms degraded: {_n(r, 'packetLoss'):.1f}% loss, {_n(r, 'linkLatency'):.0f} ms link latency."),
    Rule("gasDetected", "environment", "critical", "air",
         lambda a: _n(a, "h2s") > 10 or _n(a, "ch4") > 10, lambda a: _n(a, "h2s") < 5 and _n(a, "ch4") < 5,
         lambda a: _n(a, "h2s"), "H2S > 10 ppm or CH4 > 10 %LEL",
         lambda a: f"Gas detected: H2S {_n(a, 'h2s'):.1f} ppm, CH4 {_n(a, 'ch4'):.1f} %LEL. Evacuate zone, keep robot clear.", 2),
    Rule("noiseHazard", "environment", "low", "air",
         lambda n: _n(n, "LAeq") > 85, lambda n: _n(n, "LAeq") < 82,
         lambda n: _n(n, "LAeq"), "LAeq > 85 dB",
         lambda n: f"Noise at {_n(n, 'LAeq'):.0f} dB(A) ({n.get('noiseSource', 'unknown')}); voice reliability reduced, hearing protection required.", 8),
]

SOURCE_ID = {
    "robot": "urn:ngsi-ld:Robot:artaban-01",
    "air": "urn:ngsi-ld:AirQualityObserved:artaban-01",
}


class AlertEngine:
    def __init__(self, orion: Orion, on_change: Callable[[dict], None]):
        self.orion = orion
        self.on_change = on_change
        self.active: dict[str, dict] = {}          # rule id -> simplified alert
        self.pending: dict[str, float] = {}        # rule id -> first time condition seen
        self.mission_id: str | None = None
        self.raised_in_mission = 0

    async def load(self) -> None:
        for e in await self.orion.query("Alert", limit=1000):
            a = simplify(e)
            if a.get("status") in ("active", "acknowledged"):
                self.active[a.get("subCategory")] = a

    async def evaluate(self, source: str, data: dict, mission_id: str | None) -> None:
        now = time.time()
        for rule in RULES:
            if rule.source != source:
                continue
            current = self.active.get(rule.id)
            if current is None:
                if rule.trigger(data):
                    first = self.pending.setdefault(rule.id, now)
                    if now - first >= rule.hold_s:
                        await self._raise(rule, data, mission_id, source)
                else:
                    self.pending.pop(rule.id, None)
            elif rule.clear(data):
                await self._resolve(rule.id, auto=True)

    async def _raise(self, rule: Rule, data: dict, mission_id: str | None, source: str) -> None:
        self.pending.pop(rule.id, None)
        ts = now_iso()
        aid = f"urn:ngsi-ld:Alert:{rule.id}-{uuid.uuid4().hex[:8]}"
        src = SOURCE_ID.get(source, f"urn:ngsi-ld:HRISession:{mission_id}")
        value = rule.value(data)
        entity = {
            "id": aid, "type": "Alert",
            "category": prop(rule.category, ts), "subCategory": prop(rule.id, ts),
            "severity": prop(rule.severity, ts), "description": prop(rule.describe(data), ts),
            "dateIssued": prop(ts, ts), "status": prop("active", ts),
            "value": prop(round(value, 2) if isinstance(value, float) else value, ts),
            "threshold": prop(rule.threshold, ts), "missionId": prop(mission_id or "-", ts),
            "alertSource": rel(src),
        }
        await self.orion.upsert([entity])
        alert = simplify(entity)
        self.active[rule.id] = alert
        self.raised_in_mission += 1
        log.info("ALERT %s %s", rule.severity, rule.id)
        self.on_change(alert)

    async def _resolve(self, rule_id: str, auto: bool) -> dict | None:
        alert = self.active.pop(rule_id, None)
        if not alert:
            return None
        ts = now_iso()
        await self.orion.patch(alert["id"], {"status": prop("resolved", ts), "validTo": prop(ts, ts)})
        alert = {**alert, "status": "resolved", "validTo": ts}
        self.on_change(alert)
        return alert

    def sync(self, alert: dict) -> None:
        """Follow a status change made directly in Orion-LD (e.g. from the WireCloud alerts widget)."""
        for rule_id, current in list(self.active.items()):
            if current["id"] == alert.get("id") and alert.get("status") != current.get("status"):
                if alert.get("status") == "resolved":
                    self.active.pop(rule_id, None)
                else:
                    current["status"] = alert.get("status")

    async def set_status(self, alert_id: str, status: str) -> dict | None:
        for rule_id, alert in list(self.active.items()):
            if alert["id"] == alert_id:
                if status == "resolved":
                    return await self._resolve(rule_id, auto=False)
                ts = now_iso()
                await self.orion.patch(alert_id, {"status": prop(status, ts)})
                alert["status"] = status
                self.on_change(alert)
                return alert
        # Not tracked (already resolved) — patch anyway so the UI stays consistent.
        ts = now_iso()
        attrs = {"status": prop(status, ts)}
        if status == "resolved":
            attrs["validTo"] = prop(ts, ts)
        await self.orion.patch(alert_id, attrs)
        e = await self.orion.get(alert_id)
        return simplify(e) if e else None
