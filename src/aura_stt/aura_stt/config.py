"""Shared, validated startup configuration for the STT module."""

import math
import os
from dataclasses import asdict, dataclass

LATENCY_LOOKAHEAD = {80: 0, 160: 1, 560: 6, 1120: 13}


@dataclass(frozen=True)
class SttConfig:
    model_dir: str = "model"
    device: str = "cpu"
    input_device: str = ""
    input_sample_rate: int = 0
    capture_channels: int = 0
    capture_block_ms: int = 100
    input_latency: float = 0.0
    prefer_usb: bool = True
    include_internal: bool = False
    audio_file: str = ""
    latency_ms: int = 560
    num_threads: int = 4
    debug_audio: bool = False
    transcript_topic: str = "/aura_transcript"
    partial_topic: str = "/aura_partial_transcript"
    speech_threshold_db: float = -40.0
    silence_s: float = 0.8
    pre_roll_s: float = 0.3
    max_utterance_s: float = 20.0

    @classmethod
    def defaults(cls):
        return asdict(cls(model_dir=os.environ.get("AURA_STT_MODEL_DIR", "model")))

    @classmethod
    def from_mapping(cls, values):
        config = cls(**values)
        config.validate()
        return config

    @property
    def microphone_device(self):
        return int(self.input_device) if self.input_device.isdigit() else self.input_device or None

    @property
    def tail_s(self):
        return max(1.2, self.latency_ms / 1000 + 0.2)

    def validate(self):
        for name in ("model_dir", "device", "input_device", "audio_file", "transcript_topic", "partial_topic"):
            if not isinstance(getattr(self, name), str):
                raise ValueError(f"{name} must be a string (quote microphone indices)")
        if not self.model_dir.strip():
            raise ValueError("Set model_dir or AURA_STT_MODEL_DIR to your local fine-tuned model")
        if type(self.latency_ms) is not int or self.latency_ms not in LATENCY_LOOKAHEAD:
            raise ValueError("latency_ms must be 80, 160, 560 or 1120")
        if type(self.num_threads) is not int or self.num_threads < 1:
            raise ValueError("num_threads must be a positive integer")
        if type(self.debug_audio) is not bool:
            raise ValueError("debug_audio must be true or false")
        for name in ("prefer_usb", "include_internal"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be true or false")
        if type(self.input_sample_rate) is not int or self.input_sample_rate < 0:
            raise ValueError("input_sample_rate must be 0 (auto) or a positive integer")
        if type(self.capture_channels) is not int or self.capture_channels < 0:
            raise ValueError("capture_channels must be 0 (auto) or a positive channel count")
        if type(self.capture_block_ms) is not int or not 10 <= self.capture_block_ms <= 200:
            raise ValueError("capture_block_ms must be between 10 and 200")
        if isinstance(self.input_latency, bool) or not isinstance(self.input_latency, (float, int)) or not math.isfinite(self.input_latency) or self.input_latency < 0:
            raise ValueError("input_latency must be 0 (device high latency) or positive seconds")
        if self.device not in ("auto", "cpu", "mps", "cuda") and not (
            self.device.startswith("cuda:") and self.device[5:].isdigit()
        ):
            raise ValueError("device must be auto, cpu, mps, cuda or cuda:<index>")
        if not self.transcript_topic.strip() or not self.partial_topic.strip():
            raise ValueError("Transcript topic names must not be empty")
        if self.transcript_topic == self.partial_topic:
            raise ValueError("Partial and final topics must be different")
        for name in ("speech_threshold_db", "silence_s", "pre_roll_s", "max_utterance_s"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
                raise ValueError(f"{name} must be a finite number")
        if not -100 <= self.speech_threshold_db <= 0:
            raise ValueError("speech_threshold_db must be between -100 and 0")
        if self.silence_s < 0.02 or self.pre_roll_s < 0:
            raise ValueError("silence_s must be >= 0.02 and pre_roll_s >= 0")
        if self.max_utterance_s <= self.silence_s:
            raise ValueError("max_utterance_s must exceed silence_s")
