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


def stamped(sec, nanosec=0, **kw):
    return msg(header={"stamp": {"sec": sec, "nanosec": nanosec}, "frame_id": "aura_intent"}, **kw)


def test_header_stamp_is_carried():
    assert ros.intent_event(stamped(1794402919, 293200000))["stamp"] == 1794402919.2932
    assert "stamp" not in ros.intent_event(msg())


def test_republished_stop_counted_once_even_after_the_window():
    first = {**ros.intent_event(stamped(1000)), "_rx": 100.0}
    marked = {**ros.intent_event(stamped(1000, outcome="false_activation")), "_rx": 140.0}
    rest = {**ros.intent_event(stamped(1000, time_to_safe_stop_ms=640.0)), "_rx": 100.0 + ros.FEEDBACK_WINDOW_S + 300}
    events = ros.merge_feedback([first, marked, rest])
    assert len(events) == 1
    assert events[0]["outcome"] == "false_activation" and events[0]["timeToSafeStopMs"] == 640.0


def test_restart_reusing_event_id_with_new_stamp_is_two_events():
    first = {**ros.intent_event(stamped(1000)), "_rx": 100.0}
    reused = {**ros.intent_event(stamped(2000, intent="pause")), "_rx": 160.0}  # well inside the window
    feedback = {**ros.intent_event(stamped(1000, outcome="false_activation")), "_rx": 170.0}
    events = ros.merge_feedback([first, reused, feedback])
    assert [e["intent"] for e in events] == ["stop", "pause"]
    assert events[0]["outcome"] == "false_activation" and events[1]["outcome"] == ""


def test_later_republish_without_outcome_keeps_false_activation():
    first = {**ros.intent_event(stamped(1000)), "_rx": 100.0}
    marked = {**ros.intent_event(stamped(1000, outcome="false_activation")), "_rx": 110.0}
    rest = {**ros.intent_event(stamped(1000, time_to_safe_stop_ms=700.0)), "_rx": 120.0}
    (event,) = ros.merge_feedback([first, marked, rest])
    assert event["outcome"] == "false_activation" and event["timeToSafeStopMs"] == 700.0
    assert compute([event], now=121.0)["falseActivationRate"] == 100.0


def test_same_event_without_stamp_falls_back_to_window():
    a = {**ros.intent_event(msg()), "_rx": 100.0}
    assert ros.same_event(a, {**ros.intent_event(stamped(1000)), "_rx": 200.0})
    assert not ros.same_event(a, {**ros.intent_event(msg()), "_rx": 100.0 + ros.FEEDBACK_WINDOW_S + 1})


def test_merged_events_sql_is_shared_and_keys_on_stamp():
    from aura_monitor.intent_sql import merged_intent_events
    sql = merged_intent_events()
    assert "header,stamp,sec" in sql and "interval '10 minutes'" in sql and "id LIKE '%/intentEvent'" in sql
