"""Shared, validated startup configuration for the gesture module."""

import math
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .labels import ACTIONABLE, DEFAULT_INTENTS


def resolve_model_path(path, package="aura_gesture"):
    """Absolute paths as given; relative ones against the working directory if
    present there, otherwise against the installed package share (the bundled
    model lives at share/aura_gesture/model/gesture/)."""
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
class CameraConfig:
    # Device index ("0"), V4L2 path ("/dev/video0") or MJPEG URL ("http://host:18090/stream.mjpg")
    source: str = "0"
    width: int = 1280
    height: int = 720
    fps: float = 30.0
    jpeg_quality: int = 85
    frame_id: str = "aura_camera"
    image_topic: str = "/aura/camera/image_raw/compressed"
    annotation_topic: str = "/aura/camera/annotation"
    reconnect_s: float = 1.0

    @classmethod
    def defaults(cls):
        return asdict(cls(source=os.environ.get("AURA_CAMERA_SOURCE", "0")))

    @classmethod
    def from_mapping(cls, values):
        config = cls(**values)
        config.validate()
        return config

    def validate(self):
        for name in ("source", "frame_id", "image_topic", "annotation_topic"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"{name} must be a non-empty string (quote device indices)")
        for name in ("width", "height", "jpeg_quality"):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if not 1 <= self.jpeg_quality <= 100:
            raise ValueError("jpeg_quality must be between 1 and 100")
        for name in ("fps", "reconnect_s"):
            if not _finite(getattr(self, name)) or getattr(self, name) <= 0:
                raise ValueError(f"{name} must be a positive number")


@dataclass(frozen=True)
class GestureConfig:
    model_path: str = "model/gesture/YOLOv10n_gestures.onnx"
    model_id: str = ""  # reported in every message for A/B attribution; defaults to the file stem
    num_threads: int = 4
    providers: str = ""  # comma-separated ORT providers; empty: TensorRT > CUDA > CPU when available
    image_topic: str = "/aura/camera/image_raw/compressed"
    hypothesis_topic: str = "/aura/gesture/hypothesis"
    intent_topic: str = "/aura/gesture/intent"
    detections_topic: str = "/aura/gesture/detections"
    min_detection_score: float = 0.25
    window: int = 8
    min_votes: int = 5
    accept_threshold: float = 0.8
    # Per-gesture overrides, as parallel lists (ROS parameters have no maps).
    gestures: list = field(default_factory=lambda: list(ACTIONABLE))
    intents: list = field(default_factory=lambda: [DEFAULT_INTENTS[g] for g in ACTIONABLE])
    thresholds: list = field(default_factory=lambda: [0.8, 0.8, 0.7, 0.8, 0.8])
    votes: list = field(default_factory=lambda: [5, 5, 3, 5, 5])
    cooldown_s: float = 1.0
    rearm_frames: int = 3
    max_frame_age_s: float = 0.5
    diagnostics_period_s: float = 1.0
    status_topic: str = "/aura/gesture/status"
    # Annotated JPEG (boxes, labels, confidence, decision) for rqt/Foxglove; costs an encode per frame
    publish_debug_image: bool = False
    debug_image_topic: str = "/aura/gesture/debug_image/compressed"

    @classmethod
    def defaults(cls):
        return asdict(cls(model_path=os.environ.get("AURA_GESTURE_MODEL", cls.model_path)))

    @classmethod
    def from_mapping(cls, values):
        values = dict(values)
        for name in ("gestures", "intents", "thresholds", "votes"):
            values[name] = list(values.get(name) or [])
        config = cls(**values)
        config.validate()
        return config

    @property
    def provider_list(self):
        return [p.strip() for p in self.providers.split(",") if p.strip()]

    @property
    def intent_map(self):
        return dict(zip(self.gestures, self.intents))

    @property
    def threshold_map(self):
        return dict(zip(self.gestures, self.thresholds))

    @property
    def votes_map(self):
        return dict(zip(self.gestures, self.votes))

    def validate(self):
        if not isinstance(self.providers, str) or not isinstance(self.model_id, str):
            raise ValueError("providers and model_id must be strings")
        if type(self.publish_debug_image) is not bool:
            raise ValueError("publish_debug_image must be true or false")
        for name in ("model_path", "image_topic", "hypothesis_topic", "intent_topic", "detections_topic",
                     "debug_image_topic"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"{name} must be a non-empty string")
        topics = (self.hypothesis_topic, self.intent_topic, self.detections_topic, self.debug_image_topic)
        if len(set(topics)) != len(topics):
            raise ValueError("hypothesis, intent, detections and debug image topics must differ")
        if type(self.num_threads) is not int or self.num_threads < 1:
            raise ValueError("num_threads must be a positive integer")
        if type(self.window) is not int or self.window < 1:
            raise ValueError("window must be a positive integer")
        if type(self.min_votes) is not int or not 1 <= self.min_votes <= self.window:
            raise ValueError("min_votes must be between 1 and window")
        if type(self.rearm_frames) is not int or self.rearm_frames < 1:
            raise ValueError("rearm_frames must be a positive integer")
        for name in ("min_detection_score", "accept_threshold"):
            if not _finite(getattr(self, name)) or not 0 <= getattr(self, name) <= 1:
                raise ValueError(f"{name} must be between 0 and 1")
        for name in ("cooldown_s", "max_frame_age_s", "diagnostics_period_s"):
            if not _finite(getattr(self, name)) or getattr(self, name) < 0:
                raise ValueError(f"{name} must be a non-negative number")
        n = len(self.gestures)
        if not (len(self.intents) == len(self.thresholds) == len(self.votes) == n):
            raise ValueError("gestures, intents, thresholds and votes must have the same length")
        unknown = set(self.gestures) - set(ACTIONABLE)
        if unknown:
            raise ValueError(f"unknown gestures {sorted(unknown)}; expected a subset of {ACTIONABLE}")
        if any(not _finite(t) or not 0 <= t <= 1 for t in self.thresholds):
            raise ValueError("thresholds must be between 0 and 1")
        if any(type(v) is not int or not 1 <= v <= self.window for v in self.votes):
            raise ValueError("votes must be integers between 1 and window")
        if any(t < self.min_detection_score for t in [self.accept_threshold, *self.thresholds]):
            raise ValueError("acceptance thresholds must not be below min_detection_score")
