"""Confidence-aware temporal acceptance for per-frame gesture predictions.

A gesture is *accepted* only when at least ``min_votes`` of the last ``window``
frames show it at or above its threshold. Each gesture can have its own
threshold and vote count, so the safety-critical stop (fist) can react faster
than confirm/reject. After an acceptance the same gesture is disarmed until it
has been absent for ``rearm_frames`` frames, and no gesture is accepted within
``cooldown_s`` of the previous one, so one held pose yields one intent.
"""

from collections import deque
from dataclasses import dataclass

from .labels import ACTIONABLE, NONE

IDLE, CANDIDATE, ACCEPTED = 0, 1, 2


@dataclass(frozen=True)
class Decision:
    gesture: str
    state: int
    confidence: float  # mean score of ``gesture`` over the window (absent frames count 0)
    frame_confidence: float
    votes: int
    window: int


class TemporalGate:
    def __init__(self, window=8, min_votes=5, threshold=0.8, thresholds=None, votes=None,
                 cooldown_s=1.0, rearm_frames=3):
        if not 1 <= min_votes <= window:
            raise ValueError("min_votes must be between 1 and window")
        self.window = window
        self.min_votes = min_votes
        self.threshold = threshold
        self.thresholds = dict(thresholds or {})
        self.votes = dict(votes or {})
        for g, v in self.votes.items():
            if not 1 <= v <= window:
                raise ValueError(f"votes for {g} must be between 1 and window")
        self.cooldown_s = cooldown_s
        self.rearm_frames = rearm_frames
        self.history = deque(maxlen=window)  # (frame index, gesture, score)
        self.frame = 0
        self.disarmed = {}  # gesture -> consecutive frames absent since acceptance
        # gesture -> first frame index whose votes count again after re-arming,
        # so votes left in the window from the held pose cannot re-fire it
        self.counts_from = {}
        self.last_accept = None

    def threshold_for(self, gesture):
        return self.thresholds.get(gesture, self.threshold)

    def votes_for(self, gesture):
        return self.votes.get(gesture, self.min_votes)

    def reset(self):
        self.history.clear()
        self.disarmed.clear()
        self.counts_from.clear()
        self.last_accept = None

    def update(self, gesture, score, now):
        """Feed one frame (canonical gesture or 'none', its score) and decide."""
        gesture = gesture if gesture in ACTIONABLE else NONE
        self.frame += 1
        self.history.append((self.frame, gesture, float(score)))
        for g in list(self.disarmed):
            self.disarmed[g] = 0 if gesture == g else self.disarmed[g] + 1
            if self.disarmed[g] >= self.rearm_frames:
                del self.disarmed[g]
                self.counts_from[g] = self.frame

        best = None
        for g in ACTIONABLE:
            thr = self.threshold_for(g)
            since = self.counts_from.get(g, 0)
            votes = sum(1 for i, h, s in self.history if h == g and s >= thr and i >= since)
            conf = sum(s for i, h, s in self.history if h == g and i >= since) / self.window
            if votes and (best is None or (votes, conf) > (best[1], best[2])):
                best = (g, votes, conf)
        if best is None:
            return Decision(NONE, IDLE, 0.0, float(score) if gesture == NONE else 0.0, 0, self.window)

        g, votes, conf = best
        frame_conf = float(score) if gesture == g else 0.0
        ready = votes >= self.votes_for(g)
        cooled = self.last_accept is None or now - self.last_accept >= self.cooldown_s
        if ready and cooled and g not in self.disarmed:
            self.disarmed[g] = 0
            self.last_accept = now
            return Decision(g, ACCEPTED, conf, frame_conf, votes, self.window)
        return Decision(g, CANDIDATE, conf, frame_conf, votes, self.window)
