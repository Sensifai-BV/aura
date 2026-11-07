"""HRI KPIs over a mission's intent events (definitions in docs/DATA_MODEL.md)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from statistics import mean

WINDOW_S = 300
WINDOW_MIN_COMMANDS = 8


def _pct(n: int, d: int) -> float:
    return round(100.0 * n / d, 2) if d else 0.0


def p95(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return float(s[min(len(s) - 1, int(round(0.95 * (len(s) - 1))))])


def calibration_bins(events: list[dict], bins: int = 10) -> list[dict]:
    out = []
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        sel = [e for e in events if lo <= e["confidence"] < hi or (b == bins - 1 and e["confidence"] == 1.0)]
        if sel:
            out.append({
                "bin": round((lo + hi) / 2, 2),
                "confidence": round(mean(e["confidence"] for e in sel), 3),
                "accuracy": round(mean(1.0 if e.get("correct") else 0.0 for e in sel), 3),
                "count": len(sel),
            })
    return out


def ece(events: list[dict]) -> float:
    n = len(events)
    if not n:
        return 0.0
    return round(sum(b["count"] / n * abs(b["accuracy"] - b["confidence"]) for b in calibration_bins(events)), 4)


def compute(events: list[dict], now: float | None = None) -> dict:
    now = now or time.time()
    cmds = [e for e in events if e.get("decision") != "IGNORE"]
    n = len(cmds)
    outcomes = [e.get("outcome") for e in cmds]
    window = [e for e in cmds if now - e.get("_rx", now) <= WINDOW_S]
    lat = [e["latencyMs"] for e in cmds if "latencyMs" in e]
    tss = [e["timeToSafeStopMs"] for e in cmds if "timeToSafeStopMs" in e]
    rec = [e["recoveryTimeS"] for e in cmds if "recoveryTimeS" in e]
    interventions = sum(
        1 for e in cmds
        if (e.get("decision") == "ACCEPT" and e.get("intent") in ("stop", "pause", "cancel")) or e.get("outcome") == "misrecognized"
    )
    return {
        "commandCount": n,
        "commandSuccessRate": _pct(sum(o in ("correct", "recovered") for o in outcomes), n),
        "falseActivationRate": _pct(outcomes.count("false_activation"), n),
        "rejectionRate": _pct(sum(e["decision"] == "REJECT" for e in cmds), n),
        "windowRejectionRate": _pct(sum(e["decision"] == "REJECT" for e in window), len(window))
        if len(window) >= WINDOW_MIN_COMMANDS else 0.0,
        "confirmationLoopRate": round(10.0 * sum(e["decision"] == "CONFIRM" for e in cmds) / n, 2) if n else 0.0,
        "meanLatency": round(mean(lat)) if lat else 0,
        "p95Latency": round(p95(lat)) if lat else 0,
        "meanConfidence": round(mean(e["confidence"] for e in cmds), 3) if n else 0.0,
        "timeToSafeStop": round(mean(tss)) if tss else 0,
        "recoveryTime": round(mean(rec), 1) if rec else 0.0,
        "operatorInterventions": interventions,
        "calibrationError": ece(cmds),
    }


@dataclass
class Mission:
    mission_id: str
    started_at: str
    operator: str | None = None
    events: list[dict] = field(default_factory=list)
    conditions: dict = field(default_factory=dict)
    start_battery: float | None = None
    min_battery: float | None = None
    distance: float = 0.0
    alerts: int = 0

    def add(self, ev: dict) -> None:
        ev.setdefault("_rx", time.time())
        self.events.append(ev)
        cond = ev.get("condition")
        if cond:
            self.conditions[cond] = self.conditions.get(cond, 0) + 1
        self.operator = ev.get("operatorId") or self.operator

    @property
    def dominant_condition(self) -> str:
        return max(self.conditions, key=self.conditions.get) if self.conditions else "clean"

    def observe_robot(self, robot: dict) -> None:
        b = robot.get("batteryLevel")
        if isinstance(b, (int, float)):
            self.start_battery = b if self.start_battery is None else self.start_battery
            self.min_battery = b if self.min_battery is None else min(self.min_battery, b)
        odo = robot.get("odometry")
        if isinstance(odo, (int, float)):
            self.distance = max(self.distance, odo)

    def kpis(self) -> dict:
        return compute(self.events)


def verdict(k: dict) -> str:
    if k["commandSuccessRate"] >= 88 and k["falseActivationRate"] < 3 and k["p95Latency"] < 600:
        return "pass"
    if k["commandSuccessRate"] >= 80 and k["falseActivationRate"] < 5:
        return "watch"
    return "fail"
