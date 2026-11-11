"""Keep the microphone open while the robot talks and drop only the robot's own words.

The speaker plays what aura_intent publishes on ``/aura/speaker/say``. When an utterance overlaps
robot speech, the words of the sentences played in that time are removed from the transcript (each
robot word removes at most one transcript word, with a tolerance for recognition errors). What is
left is the operator: "Did you say come follow stop" becomes "stop". The robot's own
"Stop, executing." heard back is removed completely, so it cannot trigger another stop.

A single leftover word is forwarded only when it is a command word (stop, yes, ...): a misheard
robot word ("fellow" for "follow") must not reach the intent parser and make the robot answer.
"""

import re
import threading
from collections import deque
from difflib import SequenceMatcher

# A word left over after removing the robot's words is forwarded on its own only if it is one of these.
KEYWORDS = frozenset({
    "stop", "halt", "freeze", "pause", "wait", "hold", "resume", "continue", "cancel", "abort",
    "yes", "yeah", "yep", "no", "nope", "confirm", "artaban",
})
# Safety words: during robot speech these are sent as soon as a partial transcript shows them,
# instead of waiting for the end of the utterance (which ends only when the robot stops talking).
SAFETY_WORDS = frozenset({"stop", "halt", "freeze", "pause"})

_WORD = re.compile(r"[a-z0-9']+")


def words(text):
    return _WORD.findall(text.lower())


def _similar(a, b, threshold):
    return a == b or (len(a) > 2 and len(b) > 2 and SequenceMatcher(None, a, b).ratio() >= threshold)


def strip_echo(transcript_words, robot_words, similarity=0.75):
    """Transcript words that no robot word explains. Exact matches are used before fuzzy ones."""
    unused = list(robot_words)
    left = []
    pending = []
    for w in transcript_words:
        if w in unused:
            unused.remove(w)
        else:
            pending.append(w)
    for w in pending:
        match = next((r for r in unused if _similar(w, r, similarity)), None)
        if match is None:
            left.append(w)
        else:
            unused.remove(match)
    # keep the operator's word order
    out, budget = [], {w: left.count(w) for w in left}
    for w in transcript_words:
        if budget.get(w, 0):
            out.append(w)
            budget[w] -= 1
    return out


def worth_forwarding(residual):
    return len(residual) >= 2 or (len(residual) == 1 and residual[0] in KEYWORDS)


class RobotSpeech:
    """Sentences sent to the speaker, with their receive time (``time.monotonic()``)."""

    def __init__(self, keep_s=60.0, lead_s=2.0):
        self.keep_s = keep_s
        self.lead_s = lead_s  # a sentence arrives up to this long before its playback starts (synthesis)
        self._lock = threading.Lock()
        self._said = deque()

    def said(self, text, now):
        with self._lock:
            self._said.append((now, words(text)))
            while self._said and now - self._said[0][0] > self.keep_s:
                self._said.popleft()

    def words_for(self, intervals):
        """Words of the sentences that can have played in the given (start, end) speaking intervals."""
        out = []
        with self._lock:
            for t, w in self._said:
                if any(start - self.lead_s <= t <= end for start, end in intervals):
                    out.extend(w)
        return out


class EchoFilter:
    """Combines the speaker state (EchoGate intervals) with the sentences played."""

    def __init__(self, gate, speech, similarity=0.75):
        self.gate = gate
        self.speech = speech
        self.similarity = similarity

    def robot_words(self, t0, t1):
        intervals = self.gate.intervals(t0, t1)
        return self.speech.words_for(intervals) if intervals else None

    def clean(self, text, t0, t1):
        """(text to forward or "", number of words removed). Unchanged when no robot speech overlaps."""
        robot = self.robot_words(t0, t1)
        if robot is None:
            return text, 0
        heard = words(text)
        residual = strip_echo(heard, robot, self.similarity)
        removed = len(heard) - len(residual)
        if not removed:
            return text, 0
        return (" ".join(residual) if worth_forwarding(residual) else ""), removed

    def early_safety(self, partial, t0, t1):
        """The cleaned partial when it already holds a safety word the robot did not say, else ""."""
        robot = self.robot_words(t0, t1)
        if robot is None:
            return ""
        residual = strip_echo(words(partial), robot, self.similarity)
        return " ".join(residual) if SAFETY_WORDS.intersection(residual) else ""
