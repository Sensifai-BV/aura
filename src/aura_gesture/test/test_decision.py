import pytest

from aura_gesture.decision import ACCEPTED, CANDIDATE, IDLE, TemporalGate


def feed(gate, seq, t0=0.0, dt=0.05):
    return [gate.update(g, s, t0 + i * dt) for i, (g, s) in enumerate(seq)]


def test_accepts_once_after_enough_votes():
    gate = TemporalGate(window=8, min_votes=5, threshold=0.8)
    out = feed(gate, [("thumb_up", 0.9)] * 10)
    states = [d.state for d in out]
    assert states[:4] == [CANDIDATE] * 4
    assert states[4] == ACCEPTED
    assert states.count(ACCEPTED) == 1  # holding the pose does not repeat the intent
    assert out[4].gesture == "thumb_up" and out[4].votes == 5


def test_low_confidence_never_accepts():
    gate = TemporalGate(window=8, min_votes=5, threshold=0.8)
    out = feed(gate, [("thumb_down", 0.79)] * 20)
    assert all(d.state != ACCEPTED for d in out)


def test_per_gesture_threshold_and_votes_make_stop_faster():
    gate = TemporalGate(window=8, min_votes=5, threshold=0.8, thresholds={"fist": 0.7}, votes={"fist": 3})
    out = feed(gate, [("fist", 0.72)] * 4)
    assert [d.state for d in out] == [CANDIDATE, CANDIDATE, ACCEPTED, CANDIDATE]


def test_rearm_requires_absence_and_cooldown_blocks_others():
    gate = TemporalGate(window=4, min_votes=2, threshold=0.5, cooldown_s=1.0, rearm_frames=3)
    out = feed(gate, [("palm", 0.9)] * 3 + [("none", 0.0)] * 4 + [("palm", 0.9)] * 3, dt=0.5)
    assert [d.state for d in out].count(ACCEPTED) == 2
    gate = TemporalGate(window=4, min_votes=2, threshold=0.5, cooldown_s=10.0)
    out = feed(gate, [("palm", 0.9)] * 2 + [("point", 0.9)] * 4, dt=0.1)
    assert [d.gesture for d in out if d.state == ACCEPTED] == ["palm"]


def test_held_pose_does_not_refire_after_brief_switch():
    # live regression: thumb up held, 3 transition frames re-armed it, and the
    # thumb-up votes still in the window accepted it a second time
    gate = TemporalGate(window=8, min_votes=5, threshold=0.8, cooldown_s=1.0, rearm_frames=3)
    seq = [("thumb_up", 0.94)] * 60 + [("point", 0.3)] * 3 + [("thumb_down", 0.9)] * 10
    out = feed(gate, seq, dt=0.033)
    assert [d.gesture for d in out if d.state == ACCEPTED] == ["thumb_up", "thumb_down"]


def test_flicker_is_not_enough():
    gate = TemporalGate(window=8, min_votes=5, threshold=0.8)
    seq = [("thumb_up", 0.95), ("none", 0.0)] * 8
    assert all(d.state != ACCEPTED for d in feed(gate, seq))


def test_idle_and_validation():
    gate = TemporalGate()
    assert gate.update("other", 0.9, 0.0).state == IDLE
    with pytest.raises(ValueError):
        TemporalGate(window=4, min_votes=5)
    with pytest.raises(ValueError):
        TemporalGate(window=4, min_votes=2, votes={"fist": 9})
