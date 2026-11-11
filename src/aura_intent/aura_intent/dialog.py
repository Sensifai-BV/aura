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

Prompts are short ("Did you say come follow?") and hold no answer word, so the
echo filter of aura_stt can remove the question without removing the operator's
"yes" or "stop" said over it.

Outcomes follow fiware/docs/DATA_MODEL.md: an executed confirmation is operator
ground truth ("correct"); a "no" to the question marks the held command
"misrecognized" (counted as operator_cancelled by aura-monitor).
"""

from dataclasses import dataclass

from .schema import ANSWERS, NONE, SAFETY_CRITICAL

ACCEPT, CONFIRM, REJECT, IGNORE = 0, 1, 2, 3  # IntentEvent.DECISION_*
DECISION_NAMES = ("ACCEPT", "CONFIRM", "REJECT", "IGNORE")

# aura_gesture intent -> AURA intent
GESTURE_INTENTS = {"confirm": "confirm", "reject": "cancel", "stop": "stop", "cancel": "cancel",
                   "select": "target_select"}

SPOKEN = {"next_step": "next step", "target_select": "go to", "come_follow": "come follow"}


def spoken(intent, target=""):
    return f"{SPOKEN.get(intent, intent.replace('_', ' '))} {target}".strip()


def question(intent, target=""):
    return f"Did you say {spoken(intent, target)}?"


def executing(intent, target=""):
    return f"{spoken(intent, target).capitalize()}, executing."


# Fixed sentences, synthesised by aura_tts at start-up (keep its cache_phrases in step: test_dialog checks).
FIXED_PHRASES = tuple(
    [executing(i) for i in ("stop", "pause", "cancel", "resume", "next_step", "come_follow")]
    + [executing("target_select", "pointed target")]
    + [question(i) for i in ("resume", "next_step", "come_follow")] + [question("target_select", "pointed target")]
    + [f"Cancelled {spoken(i)}." for i in ("resume", "next_step", "come_follow")]
    + ["Cancelled go to pointed target.", "Sorry, I did not understand. Please repeat.",
       "There is nothing to confirm.", "Sorry, I cannot do that.", "No answer, command dropped."])


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
        if self.confirm_mode == "always" or (
                self.confirm_mode == "uncertain" and confidence < self.accept_threshold):
            self.pending = Pending(intent, target, confidence, modality, utterance, gesture, now)
            return Decision(intent, CONFIRM, say=question(intent, target), **base)
        return Decision(intent, ACCEPT, say=executing(intent, target), **base)

    def _answer(self, yes, modality, confidence, utterance="", gesture="", now=None):
        p, self.pending = self.pending, None
        answer_s = None if now is None else now - p.window_start
        if yes:
            return Decision(p.intent, ACCEPT, max(p.confidence, confidence),
                            "fused" if modality != p.modality else modality, target=p.target,
                            utterance=p.utterance or utterance, gesture=gesture or p.gesture,
                            outcome="correct", correct=True, answer_s=answer_s,
                            say=executing(p.intent, p.target))
        return Decision("cancel", ACCEPT, confidence, modality, utterance=utterance, gesture=gesture,
                        target=p.target, outcome="misrecognized", answer_s=answer_s, say=f"Cancelled {spoken(p.intent, p.target)}.")
