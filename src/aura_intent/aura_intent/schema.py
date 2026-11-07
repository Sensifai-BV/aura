"""Intent vocabulary, the model prompt and its output contract.

Shared by the ROS node and the fine-tuning scripts so the model is trained on
exactly the prompt it sees at runtime.
"""

import json
from dataclasses import dataclass

# Operator commands (IntentEvent.intent). "none" = speech not addressed to the robot.
INTENTS = ("stop", "pause", "resume", "confirm", "cancel", "next_step", "target_select", "come_follow", "unknown")
NONE = "none"
LABELS = INTENTS + (NONE,)

# Executed at once, never held for confirmation (time-to-safe-stop).
SAFETY_CRITICAL = frozenset({"stop", "pause", "cancel"})
# Answers to a pending confirmation, not commands of their own.
ANSWERS = frozenset({"confirm", "cancel"})

SYSTEM_PROMPT = (
    "You are the intent parser of AURA, a quadruped inspection robot (Artaban) in an industrial plant. "
    "Classify the operator transcript. It comes from speech recognition and may contain errors.\n"
    "Intents: stop (halt now), pause (hold position), resume (continue the paused mission), "
    "confirm (yes to a question), cancel (no / abort the last command), next_step (go to the next inspection point), "
    "target_select (go to or inspect a named place or object), come_follow (come to or follow the operator), "
    "unknown (addressed to the robot but not one of these), none (not addressed to the robot).\n"
    'Answer only JSON: {"intent": "<intent>", "target": "<place or object for target_select, else empty>"}'
)


@dataclass(frozen=True)
class Parse:
    intent: str
    target: str
    confidence: float  # probability of the intent label, 0..1
    inference_ms: float = 0.0

    @property
    def addressed(self):
        return self.intent != NONE


def messages(transcript):
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": transcript.strip()}]


def answer(intent, target=""):
    """Assistant turn the model is trained to produce."""
    return json.dumps({"intent": intent, "target": target}, ensure_ascii=False)
