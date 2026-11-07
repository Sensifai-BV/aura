import time

from aura_monitor.kpi import compute, ece, verdict


def ev(decision="ACCEPT", outcome="correct", correct=True, confidence=0.9, latency=300, **kw):
    return {"decision": decision, "outcome": outcome, "correct": correct, "confidence": confidence,
            "latencyMs": latency, "intent": kw.pop("intent", "next_step"), "_rx": kw.pop("_rx", time.time()), **kw}


def test_rates_exclude_ignored_events():
    events = [ev(), ev(outcome="recovered", decision="CONFIRM"), ev(decision="REJECT", outcome="rejected"),
              ev(outcome="false_activation"), ev(decision="IGNORE", outcome="rejected")]
    k = compute(events)
    assert k["commandCount"] == 4
    assert k["commandSuccessRate"] == 50.0
    assert k["rejectionRate"] == 25.0
    assert k["falseActivationRate"] == 25.0
    assert k["confirmationLoopRate"] == 2.5


def test_window_rejection_needs_enough_recent_commands():
    old = time.time() - 900
    events = [ev(decision="REJECT", outcome="rejected", _rx=old) for _ in range(20)] + [ev() for _ in range(3)]
    assert compute(events)["windowRejectionRate"] == 0.0
    events += [ev(decision="REJECT", outcome="rejected") for _ in range(5)]
    assert compute(events)["windowRejectionRate"] == 62.5


def test_safety_metrics_and_interventions():
    events = [ev(intent="stop", timeToSafeStopMs=700), ev(intent="pause", timeToSafeStopMs=900),
              ev(outcome="misrecognized", correct=False), ev(outcome="recovered", recoveryTimeS=4.0)]
    k = compute(events)
    assert k["timeToSafeStop"] == 800
    assert k["recoveryTime"] == 4.0
    assert k["operatorInterventions"] == 3


def test_calibration_error_is_zero_when_confidence_matches_accuracy():
    perfect = [ev(confidence=0.95, correct=True)] * 19 + [ev(confidence=0.95, correct=False)]
    assert ece(perfect) < 0.01
    overconfident = [ev(confidence=0.95, correct=False)] * 10
    assert ece(overconfident) > 0.9


def test_verdict_thresholds():
    assert verdict({"commandSuccessRate": 92, "falseActivationRate": 1, "p95Latency": 450}) == "pass"
    assert verdict({"commandSuccessRate": 84, "falseActivationRate": 4, "p95Latency": 700}) == "watch"
    assert verdict({"commandSuccessRate": 70, "falseActivationRate": 1, "p95Latency": 400}) == "fail"
