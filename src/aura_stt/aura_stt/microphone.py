"""Device-independent input discovery, capture and worker resampling.

One available audio input is selected for STT. Its capture channels are downmixed
and converted continuously to 16 kHz; PortAudio callbacks never run DSP or ASR.
"""

from dataclasses import dataclass
import math
import queue
import threading

import numpy as np
import sounddevice as sd
import soxr

from .audio import SAMPLE_RATE


@dataclass(frozen=True)
class MicrophoneDevice:
    index: int
    name: str
    sample_rate: int
    channels: int


def looks_usb(name):
    return any(value in name.lower() for value in ("usb", "uac", "card"))


def input_priority(item, config):
    """Prefer hardware/USB inputs without excluding any input or brand."""
    index, info = item
    name = info["name"].lower()
    system_alias = name.strip() in ("default", "sysdefault", "pulse", "pulseaudio", "pipewire")
    internal = any(value in name for value in ("built-in", "builtin", "internal"))
    return (system_alias, internal and not config.include_internal,
            config.prefer_usb and not looks_usb(name), index)


def select_microphone(config, audio_api=sd):
    inputs = [(index, info) for index, info in enumerate(audio_api.query_devices())
              if info.get("max_input_channels", 0) > 0]
    if config.input_device:
        requested = config.microphone_device
        if isinstance(requested, int):
            candidates = [(index, info) for index, info in inputs if index == requested]
        else:
            exact = [(index, info) for index, info in inputs
                     if requested.lower() == info["name"].lower()]
            candidates = exact or [(index, info) for index, info in inputs
                                   if requested.lower() in info["name"].lower()]
        if len(candidates) != 1:
            raise ValueError(f"input_device {config.input_device!r} matched {len(candidates)} inputs; use a device index")
    else:
        candidates = sorted(inputs, key=lambda item: input_priority(item, config))
    failures = []
    for index, info in candidates:
        native_channels = int(info["max_input_channels"])
        channel_options = ([config.capture_channels] if config.capture_channels else
                           list(dict.fromkeys((min(2, native_channels), 1, native_channels))))
        if config.capture_channels > native_channels:
            failures.append(f"{info['name']} does not support {config.capture_channels} channels")
            continue
        rates = ([config.input_sample_rate] if config.input_sample_rate else
                 [48000, 44100, 32000, 16000, int(info["default_samplerate"])])
        for channels in channel_options:
            for rate in dict.fromkeys(rates):
                try:
                    audio_api.check_input_settings(device=index, channels=channels,
                                                   dtype="float32", samplerate=rate)
                except Exception as exc:
                    failures.append(f"{info['name']} at {rate} Hz/{channels}ch: {exc}")
                    continue
                return MicrophoneDevice(index, info["name"], rate, channels)
    raise RuntimeError("No supported microphone format was found among the available input devices. "
                       + "; ".join(failures))


class AudioConverter:
    """Keep one resampling filter across callbacks and emit 20 ms mono blocks."""

    def __init__(self, sample_rate):
        self.resampler = (soxr.ResampleStream(sample_rate, SAMPLE_RATE, 1,
                                            dtype="float32", quality="HQ")
                          if sample_rate != SAMPLE_RATE else None)
        self.pending = np.empty(0, dtype=np.float32)

    def process(self, audio, last=False):
        audio = np.asarray(audio, dtype=np.float32)
        mono = audio.mean(axis=1) if audio.ndim == 2 else audio
        mono = np.ascontiguousarray(mono, dtype=np.float32)
        converted = self.resampler.resample_chunk(mono, last=last) if self.resampler else mono
        self.pending = np.concatenate((self.pending, converted))
        blocks = []
        while len(self.pending) >= 320:
            blocks.append(self.pending[:320].copy())
            self.pending = self.pending[320:]
        if last and len(self.pending):
            blocks.append(self.pending.copy())
            self.pending = np.empty(0, dtype=np.float32)
        return blocks


class MicrophoneCapture:
    def __init__(self, config, on_audio, on_finished, on_error, audio_api=sd):
        self.device = select_microphone(config, audio_api)
        self.converter = AudioConverter(self.device.sample_rate)
        self.on_audio = on_audio
        self.on_error = on_error
        self.stop_event = threading.Event()
        self.queue = queue.Queue(maxsize=math.ceil(2000 / config.capture_block_ms))
        self.xruns = 0
        self.dropped_blocks = 0
        self.raw_samples = 0
        self.raw_peak = 0.0
        self.worker = threading.Thread(target=self._run, name="aura-microphone", daemon=True)
        self.stream = audio_api.InputStream(
            device=self.device.index, samplerate=self.device.sample_rate,
            channels=self.device.channels, dtype="float32",
            blocksize=round(self.device.sample_rate * config.capture_block_ms / 1000),
            latency=config.input_latency or "high", callback=self._capture,
            finished_callback=on_finished)

    def _capture(self, indata, frames, time_info, status):
        if self.stop_event.is_set():
            return
        if status:
            self.xruns += 1
        block = indata.copy()
        try:
            self.queue.put_nowait(block)
        except queue.Full:
            # Bound capture latency by preserving the newest audio.
            try:
                self.queue.get_nowait()
            except queue.Empty:
                pass
            self.dropped_blocks += 1
            try:
                self.queue.put_nowait(block)
            except queue.Full:
                self.dropped_blocks += 1

    def _run(self):
        try:
            while not self.stop_event.is_set():
                try:
                    audio = self.queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                self.raw_samples += len(audio)
                self.raw_peak = max(self.raw_peak, float(abs(audio).max()))
                for block in self.converter.process(audio):
                    self.on_audio(block)
        except Exception as exc:
            self.on_error(exc)

    def start(self):
        self.worker.start()
        self.stream.start()

    def stop(self):
        self.stop_event.set()
        try:
            self.stream.stop()
        finally:
            if self.worker.ident is not None:
                self.worker.join(timeout=2)

    def close(self):
        try:
            self.stop()
        finally:
            self.stream.close()
