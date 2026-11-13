"""Decision and confirmation dialogue of the intent module, free of ROS.

Voice: the parsed command is gated by confidence. Safety-critical commands
(stop, pause, cancel) execute at once; every other command is held and the
speaker asks the operator to confirm (voice "yes" / "no" or thumb up / down),
unless confirm_mode lets a confident command through.
Gesture: aura_gesture already applied its temporal acceptance, so its intents
answer a pending question or act directly (fist = stop, palm = cancel,
point = select the pointed target). A thumb up / down with no question pending
is ignored without speech: in the field these were unintended, and every
robot sentence is time in which the operator has to talk over the robot.

Prompts are short ("Did you say follow me?") and hold no answer word, so the
echo filter of aura_stt can remove the question without removing the operator's
"yes" or "stop" said over it. They name a command in the operator's words
("next waypoint", "follow me", "go there"), not the intent label: in the field
operators answered "no" to a correct "Did you say come follow?".

A resume while the robot reports a protective stop is refused with "Holding. The
operator must release.": the motion gate never lifts a stop on a voice resume.
The sentence holds no safety word: on 13 Nov the robot's own "Stopped" was heard as
"stop" and stopped the robot.
A voice "cancel" with nothing pending and a confidence below the accept threshold
needs the wake word; otherwise it is treated as side talk.

Outcomes follow fiware/docs/DATA_MODEL.md: an executed confirmation is operator
ground truth ("correct"); a "no" to the question marks the held command
"misrecognized" (counted as operator_cancelled by aura-monitor).
"""

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from .schema import ANSWERS, NONE, SAFETY_CRITICAL

ACCEPT, CONFIRM, REJECT, IGNORE = 0, 1, 2, 3  # IntentEvent.DECISION_*
DECISION_NAMES = ("ACCEPT", "CONFIRM", "REJECT", "IGNORE")

# aura_gesture intent -> AURA intent
GESTURE_INTENTS = {"confirm": "confirm", "reject": "cancel", "stop": "stop", "cancel": "cancel",
                   "select": "target_select"}

# How the robot names a command when it asks or confirms: the operator's words, not the intent label.
SPOKEN = {"next_step": "next waypoint", "target_select": "go to", "come_follow": "follow me"}
POINTED_TARGET = "pointed target"
RELEASE_NEEDED = "Holding. The operator must release."

WAKE_WORD = "artaban"
_WORD = re.compile(r"[a-z]+")


def has_wake_word(text):
    """True when a word of the transcript sounds like "Artaban" (Artavan, Arteman, Artaman, ...)."""
    return any(w == WAKE_WORD or (len(w) >= 6 and w.startswith("art")
                                   and SequenceMatcher(None, w, WAKE_WORD).ratio() >= 0.7)
               for w in _WORD.findall(text.lower()))


def spoken(intent, target=""):
    if intent == "target_select" and target == POINTED_TARGET:
        return "go there"
    return f"{SPOKEN.get(intent, intent.replace('_', ' '))} {target}".strip()


def question(intent, target=""):
    return f"Did you say {spoken(intent, target)}?"


def executing(intent, target=""):
    return f"{spoken(intent, target).capitalize()}, executing."


# Fixed sentences, synthesised by aura_tts at start-up (keep its cache_phrases in step: test_dialog checks).
FIXED_PHRASES = tuple(
    [executing(i) for i in ("stop", "pause", "cancel", "resume", "next_step", "come_follow")]
    + [executing("target_select", POINTED_TARGET)]
    + [question(i) for i in ("resume", "next_step", "come_follow")] + [question("target_select", POINTED_TARGET)]
    + [f"Cancelled {spoken(i)}." for i in ("resume", "next_step", "come_follow")]
    + [f"Cancelled {spoken('target_select', POINTED_TARGET)}.", "Sorry, I did not understand. Please repeat.",
       "There is nothing to confirm.", "Sorry, I cannot do that.", "No answer, command dropped.", RELEASE_NEEDED])


@dataclass
class Decision:
    intent: str
    decision: int
    confidence: float
    modality: str  # voice, gesture or fused
    say: str = ""
    target: str = ""
    utterance: str = ""
    gesture: str = ""
    engaged: bool = True
    outcome: str = ""
    correct: bool = False
    answer_s: float | None = None  # answers: end of the spoken question (or the question, if unknown) -> answer

    @property
    def safety_critical(self):
        return self.intent in SAFETY_CRITICAL


# A question whose playback never reports its end is dropped after this long anyway.
MAX_QUESTION_S = 30.0


@dataclass
class Pending:
    intent: str
    target: str
    confidence: float
    modality: str
    utterance: str
    gesture: str
    asked_at: float
    playing: bool = False  # the speaker is playing the question now
    answer_from: float | None = None  # end of the question's playback

    @property
    def window_start(self):
        return self.asked_at if self.answer_from is None else self.answer_from


class Dialog:
    def __init__(self, accept_threshold=0.72, reject_threshold=0.52, confirm_mode="always", confirm_timeout_s=12.0):
        if confirm_mode not in ("always", "uncertain", "never"):
            raise ValueError("confirm_mode must be always, uncertain or never")
        if not 0.0 <= reject_threshold <= accept_threshold <= 1.0:
            raise ValueError("need 0 <= reject_threshold <= accept_threshold <= 1")
        self.accept_threshold = accept_threshold
        self.reject_threshold = reject_threshold
        self.confirm_mode = confirm_mode
        self.confirm_timeout_s = confirm_timeout_s
        self.pending = None
        self.protective_stop = False  # /artaban/status safety_state == protective-stop (motion gate closed)

    def on_robot_status(self, safety_state):
        self.protective_stop = safety_state == "protective-stop"

    def _release_needed(self, intent, base):
        """A resume cannot lift a protective stop; say so instead of "Resume, executing."."""
        return Decision(intent, REJECT, outcome="rejected", say=RELEASE_NEEDED, **base)

    def on_voice(self, utterance, intent, target, confidence, now):
        base = dict(confidence=confidence, modality="voice", utterance=utterance, target=target)
        if intent == NONE:
            return Decision(NONE, IGNORE, engaged=False, **base)
        if confidence < self.reject_threshold:
            return Decision(intent, REJECT, outcome="rejected", say="Sorry, I did not understand. Please repeat.",
                            **base)
        if intent in ANSWERS and self.pending is not None:
            return self._answer(intent == "confirm", "voice", confidence, utterance=utterance, now=now)
        if intent == "confirm":
            return Decision(intent, REJECT, outcome="rejected", say="There is nothing to confirm.", **base)
        if intent == "cancel" and confidence < self.accept_threshold and not has_wake_word(utterance):
            # a doubtful cancel without the wake word: side talk ("... cancel that meeting ...")
            return Decision(NONE, IGNORE, engaged=False, **base)
        if intent == "unknown":
            return Decision(intent, REJECT, outcome="rejected", say="Sorry, I cannot do that.", **base)
        return self._command(intent, target, confidence, "voice", now, utterance=utterance)

    def on_gesture(self, gesture, gesture_intent, confidence, now):
        intent = GESTURE_INTENTS.get(gesture_intent)
        if intent is None:
            return None
        if intent in ANSWERS and self.pending is not None:
            return self._answer(intent == "confirm", "gesture", confidence, gesture=gesture, now=now)
        if intent == "confirm" or gesture_intent == "reject":
            return Decision(intent, IGNORE, confidence, "gesture", gesture=gesture, engaged=False)
        target = "pointed target" if intent == "target_select" else ""
        return self._command(intent, target, confidence, "gesture", now, gesture=gesture)

    def on_speaking(self, active, now):
        """Speaker playback state. The answer window opens when the question has finished
        playing, so the time to answer does not shrink with the length of the question."""
        p = self.pending
        if p is None:
            return
        if active:
            p.playing = True
        elif p.playing:
            p.playing = False
            p.answer_from = now

    def expire(self, now):
        """Drop an unanswered question; call periodically and before every input."""
        p = self.pending
        if p is None:
            return None
        if p.playing:
            if now - p.asked_at <= MAX_QUESTION_S:
                return None
        elif now - p.window_start <= self.confirm_timeout_s:
            return None
        p, self.pending = self.pending, None
        return Decision(p.intent, REJECT, p.confidence, p.modality, target=p.target, utterance=p.utterance,
                        gesture=p.gesture, outcome="rejected", say="No answer, command dropped.")

    def _command(self, intent, target, confidence, modality, now, utterance="", gesture=""):
        base = dict(confidence=confidence, modality=modality, utterance=utterance, gesture=gesture, target=target)
        if intent in SAFETY_CRITICAL:
            self.pending = None
            return Decision(intent, ACCEPT, say=executing(intent), **base)
        if intent == "resume" and self.protective_stop:
            return self._release_needed(intent, base)
        if self.confirm_mode == "always" or (
                self.confirm_mode == "uncertain" and confidence < self.accept_threshold):
            self.pending = Pending(intent, target, confidence, modality, utterance, gesture, now)
            return Decision(intent, CONFIRM, say=question(intent, target), **base)
        return Decision(intent, ACCEPT, say=executing(intent, target), **base)

    def _answer(self, yes, modality, confidence, utterance="", gesture="", now=None):
        p, self.pending = self.pending, None
        answer_s = None if now is None else now - p.window_start
        if yes and p.intent == "resume" and self.protective_stop:
            return self._release_needed(p.intent, dict(confidence=max(p.confidence, confidence), modality=(
                "fused" if modality != p.modality else modality), utterance=p.utterance or utterance,
                gesture=gesture or p.gesture, target=p.target))
        if yes:
            return Decision(p.intent, ACCEPT, max(p.confidence, confidence),
                            "fused" if modality != p.modality else modality, target=p.target,
                            utterance=p.utterance or utterance, gesture=gesture or p.gesture,
                            outcome="correct", correct=True, answer_s=answer_s,
                            say=executing(p.intent, p.target))
        return Decision("cancel", ACCEPT, confidence, modality, utterance=utterance, gesture=gesture,
                        target=p.target, outcome="misrecognized", answer_s=answer_s, say=f"Cancelled {spoken(p.intent, p.target)}.")
