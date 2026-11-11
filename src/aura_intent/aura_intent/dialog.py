"""Decision and confirmation dialogue of the intent module, free of ROS.

Voice: the parsed command is gated by confidence. Safety-critical commands
(stop, pause, cancel) execute at once; every other command is held and the
speaker asks the operator to confirm (voice "yes" / "no" or thumb up / down),
unless confirm_mode lets a confident command through.
Gesture: aura_gesture already applied its temporal acceptance, so its intents
answer a pending question or act directly (fist = stop, palm = cancel,
point = select the pointed target).

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
            return self._answer(intent == "confirm", "voice", confidence, utterance=utterance)
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
            return self._answer(intent == "confirm", "gesture", confidence, gesture=gesture)
        if intent == "confirm" or gesture_intent == "reject":
            return Decision(intent, REJECT, confidence, "gesture", gesture=gesture, outcome="rejected",
                            say="There is nothing to confirm.")
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
            return Decision(intent, ACCEPT, say=f"{spoken(intent).capitalize()}, executing.", **base)
        if self.confirm_mode == "always" or (
                self.confirm_mode == "uncertain" and confidence < self.accept_threshold):
            self.pending = Pending(intent, target, confidence, modality, utterance, gesture, now)
            return Decision(intent, CONFIRM, say=f"Did you say {spoken(intent, target)}? "
                                                 "Say yes or show thumb up.", **base)
        return Decision(intent, ACCEPT, say=f"{spoken(intent, target).capitalize()}, executing.", **base)

    def _answer(self, yes, modality, confidence, utterance="", gesture=""):
        p, self.pending = self.pending, None
        if yes:
            return Decision(p.intent, ACCEPT, max(p.confidence, confidence),
                            "fused" if modality != p.modality else modality, target=p.target,
                            utterance=p.utterance or utterance, gesture=gesture or p.gesture,
                            outcome="correct", correct=True,
                            say=f"{spoken(p.intent, p.target).capitalize()}, executing.")
        return Decision("cancel", ACCEPT, confidence, modality, utterance=utterance, gesture=gesture,
                        target=p.target, outcome="misrecognized", say=f"Cancelled {spoken(p.intent, p.target)}.")
