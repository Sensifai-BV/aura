"""AURA acoustic intent skill behaviour: confidence-aware multimodal command loop."""

from __future__ import annotations

import random
from dataclasses import dataclass

SAFETY_CRITICAL = {"stop", "pause", "cancel"}

UTTERANCES = {
    "stop": ["stop robot", "stop", "stop now"],
    "pause": ["pause operation", "hold on", "pause"],
    "resume": ["resume operation", "continue", "resume"],
    "confirm": ["confirm action", "yes confirm", "affirmative"],
    "cancel": ["cancel that", "cancel", "abort step"],
    "next_step": ["next step", "go to next point", "next"],
    "target_select": ["inspect that valve", "select target", "check that gauge"],
    "come_follow": ["come here", "follow me"],
}
GESTURES = {
    "stop": "fist_raise", "pause": "fist_raise", "come_follow": "arm_wave",
    "target_select": "point", "confirm": "thumb_up", "cancel": "raise_wave",
}
CONDITION_CONF = {"clean": (0.93, 0.05), "noise": (0.79, 0.10), "mask": (0.76, 0.10), "noise+mask": (0.62, 0.13)}


@dataclass(frozen=True)
class Operator:
    id: str
    alias: str
    experience: str
    language: str
    threshold: float
    verbosity: str
    ppe: str
    skill: float  # additive confidence bias (clear, practiced phrasing)

    @property
    def masked(self) -> bool:
        return "full-face" in self.ppe


OPERATORS = [
    Operator("operator-01", "Operator A", "expert", "en", 0.65, "low", "ear defenders", 0.03),
    Operator("operator-02", "Operator B", "novice", "en", 0.78, "high", "full-face respirator", -0.03),
    Operator("operator-03", "Operator C", "intermediate", "sk", 0.72, "normal", "half mask + ear defenders", 0.0),
    Operator("operator-04", "Operator D", "intermediate", "de", 0.72, "normal", "full-face respirator", -0.01),
]


def condition_for(noise_db: float, masked: bool) -> str:
    noisy = noise_db >= 75
    return "noise+mask" if noisy and masked else "noise" if noisy else "mask" if masked else "clean"


def recognition_mode(condition: str, noise_db: float) -> str:
    if noise_db >= 85:
        return "gesture-fallback"
    if condition == "noise+mask":
        return "fused"
    if condition in ("noise", "mask"):
        return "acoustic+thermal"
    return "acoustic"


class IntentGenerator:
    def __init__(self) -> None:
        self.counter = 0
        self.engagement = "ENGAGED"

    def choose_intent(self, robot_state: str) -> str:
        if robot_state == "inspecting":
            pool = ["target_select"] * 4 + ["confirm"] * 3 + ["next_step"] * 4 + ["pause"] + ["cancel"]
        elif robot_state == "paused":
            pool = ["resume"] * 6 + ["cancel", "confirm"]
        elif robot_state in ("navigating", "returning"):
            pool = ["pause"] * 2 + ["stop"] + ["come_follow"] * 2 + ["next_step"] * 2 + ["confirm"]
        else:
            pool = ["next_step", "confirm", "come_follow"]
        return random.choice(pool)

    def event(self, mission_id: str, op: Operator, robot_state: str, noise_db: float,
              masked: bool, gpu_load: float, link_latency: float) -> dict:
        self.counter += 1
        cond = condition_for(noise_db, masked)
        mode = recognition_mode(cond, noise_db)

        # Engagement: operator occasionally talks to colleagues / radio chatter.
        engaged = random.random() > (0.12 if noise_db > 75 else 0.07)
        self.engagement = "ENGAGED" if engaged else random.choice(["ATTENDING", "DISENGAGED"])

        intent = self.choose_intent(robot_state) if engaged else random.choice(list(UTTERANCES))
        use_gesture = mode == "gesture-fallback" and intent in GESTURES and random.random() < 0.6
        fused = mode in ("fused", "acoustic+thermal") and intent in GESTURES and random.random() < 0.35
        modality = "gesture" if use_gesture else "fused" if fused else "voice"

        mu, sd = CONDITION_CONF[cond]
        mu += op.skill + (0.08 if modality == "fused" else 0.0)
        if modality == "gesture":
            mu, sd = 0.86, 0.07
        conf = max(0.05, min(0.995, random.gauss(mu, sd)))
        # Slight over-confidence in noise: a calibration gap the monitor should expose.
        p_correct = conf ** (1.6 if cond.startswith("noise") else 1.15)
        correct = random.random() < p_correct

        threshold = op.threshold
        accept_at = threshold - (0.15 if intent in SAFETY_CRITICAL else 0.0)
        if not engaged:
            # Engagement gating suppresses most unaddressed speech; misses become false activations.
            decision = "ACCEPT" if (conf > 0.85 and random.random() < 0.35) else "IGNORE"
        elif conf >= accept_at:
            decision = "ACCEPT"
        elif conf >= accept_at - 0.2:
            decision = "CONFIRM"
        else:
            decision = "REJECT"

        if decision == "IGNORE":
            outcome = "rejected"
        elif not engaged:
            outcome = "false_activation"
        elif decision == "REJECT":
            outcome = "rejected"
        elif correct:
            outcome = "correct"
        else:
            # Wrong hypothesis: confirmation catches it (recovered) or operator must cancel.
            outcome = "recovered" if decision == "CONFIRM" or random.random() < 0.5 else "misrecognized"

        base = 245 if modality == "voice" else 330 if modality == "gesture" else 290
        latency = base + max(0.0, noise_db - 65) * 3.2 + gpu_load * 0.9 + random.lognormvariate(3.2, 0.6)
        latency += 0.25 * link_latency

        ev = {
            "eventId": f"{mission_id}-{self.counter:04d}",
            "missionId": mission_id,
            "intent": intent if correct else random.choice([i for i in UTTERANCES if i != intent] + ["unknown"]),
            "utterance": random.choice(UTTERANCES[intent]) if modality != "gesture" else None,
            "decision": decision,
            "outcome": outcome,
            "correct": correct,
            "confidence": round(conf, 3),
            "threshold": round(accept_at, 3),
            "latencyMs": round(latency),
            "modality": modality,
            "gesture": GESTURES.get(intent) if modality != "voice" else None,
            "noiseDb": round(noise_db, 1),
            "condition": cond,
            "operatorId": op.id,
            "engaged": engaged,
            "safetyCritical": intent in SAFETY_CRITICAL,
        }
        if decision == "ACCEPT" and intent in ("stop", "pause") and correct and engaged:
            ev["timeToSafeStopMs"] = round(latency + random.uniform(280, 520))
        if outcome == "recovered":
            ev["recoveryTimeS"] = round(random.uniform(2.5, 9.0), 1)
        return ev
