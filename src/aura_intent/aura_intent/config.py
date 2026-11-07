"""Validated startup configuration for the intent module."""

import math
from dataclasses import asdict, dataclass
from pathlib import Path


def resolve_model_path(path, package="aura_intent"):
    """Absolute paths as given; relative ones against the working directory if
    present there, otherwise against the installed package share."""
    p = Path(path).expanduser()
    if p.is_absolute() or p.exists():
        return p
    try:
        from ament_index_python.packages import get_package_share_directory

        shared = Path(get_package_share_directory(package)) / p
    except Exception:  # noqa: BLE001 - not in a ROS environment (unit tests)
        shared = Path(__file__).resolve().parents[1] / p
    return shared if shared.exists() else p


@dataclass(frozen=True)
class IntentConfig:
    model_path: str = "model/aura-intent-lfm2.5-1.2b-q8_0.gguf"
    model_id: str = ""  # reported in diagnostics; defaults to the file stem
    num_threads: int = 4
    n_gpu_layers: int = 0  # 0 = CPU; -1 offloads all layers (Metal / CUDA builds of llama.cpp)
    transcript_topic: str = "/aura_transcript"
    gesture_topic: str = "/aura/gesture/intent"
    status_topic: str = "/artaban/status"
    environment_topic: str = "/artaban/environment"
    event_topic: str = "/aura/intent/event"
    speaker_topic: str = "/aura/speaker/say"
    diagnostics_topic: str = "/aura/intent/status"
    accept_threshold: float = 0.72
    reject_threshold: float = 0.52
    confirm_mode: str = "always"  # always | uncertain | never; stop/pause/cancel never wait
    confirm_timeout_s: float = 8.0
    max_transcript_chars: int = 300
    operator_id: str = "operator-01"
    mission_id: str = ""  # empty: taken from ArtabanStatus.mission_id
    session_prefix: str = "S001"  # event_id = <prefix>-<counter>
    noise_threshold_db: float = 80.0  # la_eq above this -> acoustic_condition "noise"
    diagnostics_period_s: float = 1.0

    @classmethod
    def defaults(cls):
        return asdict(cls())

    @classmethod
    def from_mapping(cls, values):
        config = cls(**values)
        config.validate()
        return config

    def validate(self):
        for name in ("model_path", "transcript_topic", "gesture_topic", "event_topic", "speaker_topic",
                     "diagnostics_topic", "session_prefix"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"{name} must be a non-empty string")
        if type(self.num_threads) is not int or self.num_threads <= 0:
            raise ValueError("num_threads must be a positive integer")
        for name in ("accept_threshold", "reject_threshold"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"{name} must be between 0 and 1")
        if self.reject_threshold > self.accept_threshold:
            raise ValueError("reject_threshold must not exceed accept_threshold")
        if self.confirm_mode not in ("always", "uncertain", "never"):
            raise ValueError("confirm_mode must be always, uncertain or never")
        if self.confirm_timeout_s <= 0 or self.max_transcript_chars <= 0:
            raise ValueError("confirm_timeout_s and max_transcript_chars must be positive")
