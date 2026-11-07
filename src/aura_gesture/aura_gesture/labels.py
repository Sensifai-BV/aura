"""Model labels, the canonical AURA gesture set and its default intent mapping."""

# Class order of the HaGRIDv2 gesture detectors (hukenovs/hagrid constants.py).
HAGRID_LABELS = (
    "grabbing", "grip", "holy", "point", "call", "three3", "timeout", "xsign", "hand_heart", "hand_heart2",
    "little_finger", "middle_finger", "take_picture", "dislike", "fist", "four", "like", "mute", "ok", "one",
    "palm", "peace", "peace_inverted", "rock", "stop", "stop_inverted", "three", "three2", "two_up",
    "two_up_inverted", "three_gun", "thumb_index", "thumb_index2", "no_gesture",
)

# Operator gestures from the AURA proposal; wave/follow are dynamic and their
# static key pose is the open palm.
ACTIONABLE = ("thumb_up", "thumb_down", "fist", "palm", "point")
NONE = "none"

HAGRID_TO_CANONICAL = {
    "like": "thumb_up",
    "dislike": "thumb_down",
    "fist": "fist",
    "palm": "palm",
    "stop": "palm",
    "point": "point",
    "one": "point",
}

DEFAULT_INTENTS = {
    "thumb_up": "confirm",
    "thumb_down": "reject",
    "fist": "stop",
    "palm": "cancel",
    "point": "select",
}


def canonical(label: str) -> str:
    """Map a model label to the canonical set; everything else is 'none'."""
    return HAGRID_TO_CANONICAL.get(label, NONE)
