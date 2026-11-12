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
    # Which captured channel STT uses: 0 = the first (on the ReSpeaker the processed, beamformed channel
    # with AGC), n = channel n, -1 = the average of all captured channels (before 12 Nov). Averaging the
    # ReSpeaker's ch0 with a raw microphone lost the operator's voice in step noise.
    input_channel: int = 0
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
    status_topic: str = "/aura/stt/status"
    # aura_tts playback state (true while the robot talks). Empty disables all echo handling
    # (STT then hears the robot itself and can loop on its own "Stop, executing.").
    speaking_topic: str = "/aura/speaker/speaking"
    # filter: the microphone stays open; words of the sentences the robot played (robot_text_topic)
    #         are removed from transcripts that overlap its speech, so "stop" is heard while it talks.
    # mute:   the microphone is muted while the robot talks and echo_tail_s after (round-2 behaviour).
    echo_mode: str = "filter"
    robot_text_topic: str = "/aura/speaker/say"
    echo_tail_s: float = 0.4
    echo_similarity: float = 0.75  # how close a misrecognised robot word must be to count as echo
    record_audio: bool = False  # save every utterance as a 16 kHz WAV in <metrics_dir>/audio
    debug: bool = False                 # write one CSV row per utterance and a summary (metrics.py)
    metrics_dir: str = "aura_metrics"   # relative to the working directory
    speech_threshold_db: float = -40.0
    silence_s: float = 0.8
    pre_roll_s: float = 0.3
    # An utterance closes after silence_s below speech_threshold_db, or at this length. Steady noise
    # above the threshold (a walking robot) keeps it open until this limit (20 s before 12 Nov).
    max_utterance_s: float = 6.0
    # Send stop / pause from a partial transcript as soon as it shows them (echo.addressed_safety),
    # instead of at the end of the utterance.
    partial_safety: bool = True

    @classmethod
    def defaults(cls):
        return asdict(cls(model_dir=os.environ.get("AURA_STT_MODEL_DIR", "model")))

    @classmethod
    def from_parameters(cls, values):
        """ROS parameters, with AURA_STT_MODEL_DIR (when set) taking precedence over model_dir."""
        env = os.environ.get("AURA_STT_MODEL_DIR", "").strip()
        return cls.from_mapping({**values, "model_dir": env} if env else values)

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
        for name in ("model_dir", "device", "input_device", "audio_file", "transcript_topic", "partial_topic",
                     "speaking_topic", "robot_text_topic", "echo_mode"):
            if not isinstance(getattr(self, name), str):
                raise ValueError(f"{name} must be a string (quote microphone indices)")
        if not self.model_dir.strip():
            raise ValueError("Set model_dir or AURA_STT_MODEL_DIR to your local fine-tuned model")
        if type(self.latency_ms) is not int or self.latency_ms not in LATENCY_LOOKAHEAD:
            raise ValueError("latency_ms must be 80, 160, 560 or 1120")
        if type(self.num_threads) is not int or self.num_threads < 1:
            raise ValueError("num_threads must be a positive integer")
        if type(self.debug) is not bool or not isinstance(self.metrics_dir, str) or not self.metrics_dir.strip():
            raise ValueError("debug must be true or false and metrics_dir a non-empty path")
        if type(self.debug_audio) is not bool or type(self.record_audio) is not bool:
            raise ValueError("debug_audio and record_audio must be true or false")
        if self.echo_mode not in ("filter", "mute"):
            raise ValueError("echo_mode must be filter or mute")
        if self.echo_mode == "filter" and self.speaking_topic and not self.robot_text_topic:
            raise ValueError("echo_mode filter needs robot_text_topic (the speaker's say topic)")
        for name in ("prefer_usb", "include_internal"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be true or false")
        if type(self.input_sample_rate) is not int or self.input_sample_rate < 0:
            raise ValueError("input_sample_rate must be 0 (auto) or a positive integer")
        if type(self.capture_channels) is not int or self.capture_channels < 0:
            raise ValueError("capture_channels must be 0 (auto) or a positive channel count")
        if type(self.input_channel) is not int or self.input_channel < -1:
            raise ValueError("input_channel must be -1 (average) or a channel index >= 0")
        if self.capture_channels and self.input_channel >= self.capture_channels:
            raise ValueError("input_channel must be below capture_channels")
        if type(self.partial_safety) is not bool:
            raise ValueError("partial_safety must be true or false")
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
        for name in ("speech_threshold_db", "silence_s", "pre_roll_s", "max_utterance_s", "echo_tail_s",
                     "echo_similarity"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
                raise ValueError(f"{name} must be a finite number")
        if not -100 <= self.speech_threshold_db <= 0:
            raise ValueError("speech_threshold_db must be between -100 and 0")
        if self.silence_s < 0.02 or self.pre_roll_s < 0:
            raise ValueError("silence_s must be >= 0.02 and pre_roll_s >= 0")
        if not 0 <= self.echo_tail_s <= 5:
            raise ValueError("echo_tail_s must be between 0 and 5 seconds")
        if not 0.5 <= self.echo_similarity <= 1:
            raise ValueError("echo_similarity must be between 0.5 and 1")
        if self.max_utterance_s <= self.silence_s:
            raise ValueError("max_utterance_s must exceed silence_s")
