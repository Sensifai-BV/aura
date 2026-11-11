import sys
import types

import numpy as np

from aura_tts.player import SoundDevicePlayer, resample


def test_resample_keeps_duration_and_pitch():
    t = np.arange(44100) / 44100
    tone = np.sin(2 * np.pi * 440 * t).astype(np.float32)
    out = resample(tone, 44100, 48000)
    assert out.dtype == np.float32 and len(out) == 48000
    peak_hz = np.argmax(np.abs(np.fft.rfft(out))) * 48000 / len(out)
    assert abs(peak_hz - 440) < 2
    assert resample(tone, 44100, 44100) is not None and len(resample(tone, 44100, 44100)) == 44100
    assert len(resample(np.zeros(0, np.float32), 44100, 48000)) == 0


class FakeStream:
    def __init__(self, samplerate, **kwargs):
        self.samplerate, self.written = samplerate, 0
        FakeSd.streams.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def write(self, block):
        self.written += len(block)

    def abort(self):
        pass


class FakeSd(types.ModuleType):
    """A card that plays 48 kHz only, like many USB and HDMI outputs."""
    streams = []

    def check_output_settings(self, samplerate=None, **kwargs):
        if samplerate not in (None, 48000):
            raise RuntimeError("Invalid sample rate")

    def query_devices(self, device, kind):
        return {"default_samplerate": 48000.0}

    OutputStream = FakeStream


def test_unsupported_model_rate_is_resampled_to_the_card(monkeypatch):
    monkeypatch.setitem(sys.modules, "sounddevice", FakeSd("sounddevice"))
    player = SoundDevicePlayer()
    assert player.play(np.zeros(44100, np.float32), 44100)
    stream = FakeSd.streams[-1]
    assert stream.samplerate == 48000 and stream.written == 48000
    assert player.output_rate(48000) == 48000
