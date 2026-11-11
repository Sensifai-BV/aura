import pytest

from aura_intent.dialog import ACCEPT, CONFIRM, IGNORE, REJECT, Dialog
import json

from aura_intent.schema import LABELS, NONE, answer


def test_voice_command_asks_then_voice_yes_executes():
    d = Dialog()
    ask = d.on_voice("come follow", "come_follow", "", 0.9, now=0.0)
    assert ask.decision == CONFIRM and "come follow" in ask.say and d.pending is not None
    done = d.on_voice("yes", "confirm", "", 0.95, now=2.0)
    assert done.decision == ACCEPT and done.intent == "come_follow" and done.outcome == "correct"
    assert done.modality == "voice" and done.correct and d.pending is None


def test_thumb_up_confirms_voice_command_as_fused():
    d = Dialog()
    d.on_voice("go to pump two", "target_select", "pump two", 0.8, now=0.0)
    done = d.on_gesture("thumb_up", "confirm", 0.9, now=1.0)
    assert done.decision == ACCEPT and done.intent == "target_select" and done.target == "pump two"
    assert done.modality == "fused" and done.gesture == "thumb_up"


@pytest.mark.parametrize("gesture,intent", [("thumb_down", "reject"), ("palm", "cancel")])
def test_gesture_no_cancels_pending(gesture, intent):
    d = Dialog()
    d.on_voice("next", "next_step", "", 0.8, now=0.0)
    out = d.on_gesture(gesture, intent, 0.9, now=1.0)
    assert out.intent == "cancel" and out.outcome == "misrecognized" and d.pending is None


def test_voice_no_cancels_pending():
    d = Dialog()
    d.on_voice("resume", "resume", "", 0.8, now=0.0)
    out = d.on_voice("no", "cancel", "", 0.9, now=1.0)
    assert out.decision == ACCEPT and out.intent == "cancel" and out.outcome == "misrecognized"


@pytest.mark.parametrize("intent", ["stop", "pause"])
def test_safety_critical_never_waits(intent):
    d = Dialog(confirm_mode="always")
    d.on_voice("next", "next_step", "", 0.8, now=0.0)
    out = d.on_voice(intent, intent, "", 0.6, now=0.5)
    assert out.decision == ACCEPT and out.safety_critical and d.pending is None


def test_cancel_without_pending_executes():
    out = Dialog().on_voice("abort", "cancel", "", 0.9, now=0.0)
    assert out.decision == ACCEPT and out.intent == "cancel" and out.outcome == ""


def test_fist_stops_and_clears_pending():
    d = Dialog()
    d.on_voice("go to tank b", "target_select", "tank b", 0.8, now=0.0)
    out = d.on_gesture("fist", "stop", 0.9, now=0.2)
    assert out.decision == ACCEPT and out.intent == "stop" and d.pending is None


def test_thresholds_and_engagement():
    d = Dialog(accept_threshold=0.72, reject_threshold=0.52, confirm_mode="uncertain")
    assert d.on_voice("stop for lunch", "none", "", 0.99, now=0).decision == IGNORE
    assert d.on_voice("mumble", "resume", "", 0.3, now=0).decision == REJECT
    assert d.on_voice("resume", "resume", "", 0.6, now=0).decision == CONFIRM
    d.pending = None
    assert d.on_voice("resume", "resume", "", 0.9, now=0).decision == ACCEPT
    assert d.on_voice("dance", "unknown", "", 0.9, now=0).decision == REJECT
    assert d.on_voice("yes", "confirm", "", 0.9, now=0).decision == REJECT  # nothing pending


def test_never_mode_executes_directly():
    d = Dialog(confirm_mode="never")
    assert d.on_voice("next", "next_step", "", 0.6, now=0).decision == ACCEPT


def test_pending_expires():
    d = Dialog(confirm_timeout_s=5.0)
    d.on_voice("next", "next_step", "", 0.8, now=0.0)
    assert d.expire(4.0) is None
    out = d.expire(6.0)
    assert out.decision == REJECT and out.intent == "next_step" and d.pending is None
    assert d.on_gesture("thumb_up", "confirm", 0.9, now=6.5).decision == REJECT


def test_answer_window_starts_when_question_has_been_spoken():
    d = Dialog(confirm_timeout_s=8.0)
    d.on_voice("next", "next_step", "", 0.8, now=0.0)
    d.on_speaking(True, now=0.3)
    assert d.expire(9.0) is None  # the question is still playing
    d.on_speaking(False, now=4.5)
    assert d.expire(12.0) is None  # 7.5 s after the question
    out = d.on_voice("yes", "confirm", "", 0.99, now=12.0)
    assert out.decision == ACCEPT and out.correct


def test_answer_window_expires_after_spoken_question():
    d = Dialog(confirm_timeout_s=8.0)
    d.on_speaking(False, now=-1.0)  # end of an earlier sentence, no question pending
    d.on_voice("next", "next_step", "", 0.8, now=0.0)
    d.on_speaking(False, now=0.1)  # a stale "false" before the question plays is ignored
    assert d.pending.answer_from is None
    d.on_speaking(True, now=0.2)
    d.on_speaking(False, now=4.0)
    assert d.expire(11.9) is None
    assert d.expire(12.1).decision == REJECT


def test_question_that_never_finishes_playing_is_dropped():
    d = Dialog(confirm_timeout_s=8.0)
    d.on_voice("next", "next_step", "", 0.8, now=0.0)
    d.on_speaking(True, now=0.2)
    assert d.expire(29.0) is None
    assert d.expire(31.0).decision == REJECT


def test_point_selects_pointed_target_with_confirmation():
    d = Dialog()
    out = d.on_gesture("point", "select", 0.9, now=0.0)
    assert out.decision == CONFIRM and out.intent == "target_select" and out.target == "pointed target"


def test_unknown_gesture_intent_is_ignored():
    assert Dialog().on_gesture("none", "none", 0.0, now=0.0) is None


def test_invalid_settings():
    with pytest.raises(ValueError):
        Dialog(confirm_mode="sometimes")
    with pytest.raises(ValueError):
        Dialog(accept_threshold=0.4, reject_threshold=0.5)


def test_answer_format():
    assert json.loads(answer("target_select", "pump 3")) == {"intent": "target_select", "target": "pump 3"}
    assert NONE in LABELS and len(set(LABELS)) == len(LABELS)
