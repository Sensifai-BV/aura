from aura_monitor import ros
from aura_monitor.kpi import compute


def msg(event_id="S001-0006", mission="M1", intent="stop", decision=0, outcome="", **kw):
    return {"event_id": event_id, "mission_id": mission, "intent": intent, "decision": decision,
            "outcome": outcome, "confidence": 0.49, "latency_ms": 70.0, **kw}


def test_republished_event_adds_feedback_once():
    first = {**ros.intent_event(msg()), "_rx": 100.0}
    again = {**ros.intent_event(msg(outcome="false_activation", time_to_safe_stop_ms=640.0)), "_rx": 104.0}
    other = {**ros.intent_event(msg(event_id="S001-0007", intent="next_step", decision=1)), "_rx": 105.0}
    events = ros.merge_feedback([first, again, other])
    assert len(events) == 2
    assert events[0]["outcome"] == "false_activation" and events[0]["timeToSafeStopMs"] == 640.0
    k = compute(events, now=106.0)
    assert k["commandCount"] == 2 and k["falseActivationRate"] == 50.0 and k["timeToSafeStop"] == 640


def test_reused_event_id_after_restart_is_a_new_event():
    first = {**ros.intent_event(msg()), "_rx": 100.0}
    later = {**ros.intent_event(msg(intent="pause")), "_rx": 100.0 + ros.FEEDBACK_WINDOW_S + 1}
    assert len(ros.merge_feedback([first, later])) == 2


def test_empty_fields_do_not_erase_feedback():
    target = ros.intent_event(msg(outcome="false_activation", time_to_safe_stop_ms=500.0))
    assert not ros.apply_feedback(target, ros.intent_event(msg()))
    assert target["outcome"] == "false_activation" and target["timeToSafeStopMs"] == 500.0
