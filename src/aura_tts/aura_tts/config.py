"""Validated startup configuration for the speaker (TTS) module."""

import math
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Fixed prompts of aura_intent/dialog.py, synthesised at start-up so the
# safety acknowledgements play without synthesis delay.
DEFAULT_PHRASES = (
    "Stop, executing.", "Pause, executing.", "Cancel, executing.", "Resume, executing.",
    "Sorry, I did not understand. Please repeat.", "There is nothing to confirm.",
    "Sorry, I cannot do that.", "No answer, command dropped.",
)


def resolve_model_dir(path, package="aura_tts"):
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


def _finite(value):
    return not isinstance(value, bool) and isinstance(value, (float, int)) and math.isfinite(value)


@dataclass(frozen=True)
class TtsConfig:
    # sherpa-onnx model directory; Supertonic 3, Kokoro or Piper/VITS is detected from its files
    model_dir: str = "model/supertonic-3-int8"  # bundled in share/aura_tts
    model_id: str = ""        # reported in diagnostics for A/B attribution; defaults to the directory name
    num_threads: int = 2      # Supertonic is faster on 2 than on 4 threads (A/B, section 5.1)
    provider: str = "cpu"     # ONNX Runtime provider: cpu, cuda, coreml
    voice: int = 0            # speaker id (Supertonic 0..9, Kokoro 0..53)
    speed: float = 1.0
    num_steps: int = 5        # Supertonic flow-matching steps; fewer is faster, rougher
    say_topic: str = "/aura/speaker/say"
    speaking_topic: str = "/aura/speaker/speaking"
    status_topic: str = "/aura/speaker/status"
    # "" = system default output, a device index or name substring, or "none" for no audio device
    output_device: str = ""
    gain: float = 1.0
    normalize_text: bool = True
    cache_phrases: list = field(default_factory=lambda: list(DEFAULT_PHRASES))
    max_chars: int = 400
    diagnostics_period_s: float = 1.0
    debug: bool = False                 # write one CSV row per prompt and a summary (metrics.py)
    metrics_dir: str = "aura_metrics"   # relative to the working directory

    @classmethod
    def defaults(cls):
        values = asdict(cls(model_dir=os.environ.get("AURA_TTS_MODEL", cls.model_dir)))
        values["cache_phrases"] = list(values["cache_phrases"])
        return values

    @classmethod
    def from_mapping(cls, values):
        values = dict(values)
        values["cache_phrases"] = [p for p in (values.get("cache_phrases") or []) if p]
        config = cls(**values)
        config.validate()
        return config

    def validate(self):
        for name in ("model_dir", "say_topic", "speaking_topic", "status_topic", "provider"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"{name} must be a non-empty string")
        for name in ("model_id", "output_device"):
            if not isinstance(getattr(self, name), str):
                raise ValueError(f"{name} must be a string")
        if len({self.say_topic, self.speaking_topic, self.status_topic}) != 3:
            raise ValueError("say, speaking and status topics must differ")
        for name in ("num_threads", "num_steps", "max_chars"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f"{name} must be a positive integer")
        if type(self.voice) is not int or self.voice < 0:
            raise ValueError("voice must be a non-negative integer")
        if not _finite(self.speed) or not 0.5 <= self.speed <= 2.0:
            raise ValueError("speed must be between 0.5 and 2.0")
        if not _finite(self.gain) or not 0 < self.gain <= 4.0:
            raise ValueError("gain must be in (0, 4]")
        if not _finite(self.diagnostics_period_s) or self.diagnostics_period_s < 0:
            raise ValueError("diagnostics_period_s must be zero (off) or positive")
        if type(self.debug) is not bool or not isinstance(self.metrics_dir, str) or not self.metrics_dir.strip():
            raise ValueError("debug must be true or false and metrics_dir a non-empty path")
        if type(self.normalize_text) is not bool:
            raise ValueError("normalize_text must be true or false")
        if not all(isinstance(p, str) for p in self.cache_phrases):
            raise ValueError("cache_phrases must be strings")
