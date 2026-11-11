"""Interruptible audio output: playback checks `cancelled()` every block, so a new
utterance cuts the current one within one block (about 20 ms)."""

import threading
import time

import numpy as np


def resample(samples, rate_in, rate_out):
    """Band-limited (FFT) resampling of a whole clip; clips are a few seconds, so this is cheap."""
    samples = np.asarray(samples, np.float32)
    if rate_in == rate_out or len(samples) == 0:
        return samples
    n_out = max(1, round(len(samples) * rate_out / rate_in))
    spectrum = np.fft.rfft(samples)
    bins = n_out // 2 + 1
    if len(spectrum) >= bins:
        spectrum = spectrum[:bins]
    else:
        spectrum = np.concatenate((spectrum, np.zeros(bins - len(spectrum), spectrum.dtype)))
    return (np.fft.irfft(spectrum, n_out) * (n_out / len(samples))).astype(np.float32)


class NullPlayer:
    """No sound device (headless VM, tests): blocks for the clip duration, like playback would."""

    def __init__(self):
        self._wake = threading.Event()

    def play(self, samples, sample_rate, cancelled=lambda: False):
        end = time.monotonic() + len(samples) / sample_rate
        while (left := end - time.monotonic()) > 0:
            if cancelled():
                return False
            self._wake.wait(min(left, 0.02))
            self._wake.clear()
        return not cancelled()

    def stop(self):
        self._wake.set()

    def close(self):
        self.stop()


class SoundDevicePlayer:
    def __init__(self, device="", gain=1.0, blocksize=512):
        import sounddevice

        self.sd = sounddevice
        self.device = int(device) if device.isdigit() else (device or None)
        self.gain = gain
        self.blocksize = blocksize
        self.sd.check_output_settings(device=self.device, channels=1, dtype="float32")
        self._rates = {}  # model rate -> rate the device plays

    def output_rate(self, sample_rate):
        """The model's rate when the device accepts it, otherwise the device's default rate."""
        if sample_rate not in self._rates:
            try:
                self.sd.check_output_settings(device=self.device, channels=1, dtype="float32",
                                              samplerate=sample_rate)
                self._rates[sample_rate] = sample_rate
            except Exception:  # noqa: BLE001 - PortAudio reports unsupported rates with its own errors
                info = self.sd.query_devices(self.device, "output")
                self._rates[sample_rate] = int(info["default_samplerate"]) or 48000
        return self._rates[sample_rate]

    def play(self, samples, sample_rate, cancelled=lambda: False):
        """Blocks until the clip ends; returns False when cancelled() turned true."""
        rate = self.output_rate(sample_rate)
        data = np.clip(resample(samples, sample_rate, rate) * self.gain, -1.0, 1.0).astype(np.float32)
        with self.sd.OutputStream(samplerate=rate, channels=1, dtype="float32",
                                  device=self.device, blocksize=self.blocksize) as stream:
            for start in range(0, len(data), self.blocksize):
                if cancelled():
                    stream.abort()
                    return False
                stream.write(data[start:start + self.blocksize].reshape(-1, 1))
        return True

    def stop(self):
        pass  # cancellation is polled per block

    def close(self):
        pass


class WebSocketPlayer:
    """Streams PCM16 to a remote loudspeaker (ws://host:port/speaker), paced at real time.

    For hosts with no audio device of their own (an OrbStack VM): a bridge relays the stream to
    the machine that has the speaker. Text frames {"rate": N} start a clip, {"cancel": true}
    cuts it; binary frames are 20 ms of PCM16LE mono.
    """

    def __init__(self, url, gain=1.0, blocksize=320):
        from websockets.sync.client import connect

        self._connect = connect
        self.url, self.gain, self.blocksize = url, gain, blocksize
        self.ws = None
        self._wake = threading.Event()

    def _socket(self):
        if self.ws is None:
            self.ws = self._connect(self.url, max_size=None, open_timeout=5)
        return self.ws

    def play(self, samples, sample_rate, cancelled=lambda: False):
        import json

        data = (np.clip(samples * self.gain, -1.0, 1.0) * 32767).astype("<i2")
        block = max(1, int(sample_rate * 0.02))
        try:
            ws = self._socket()
            ws.send(json.dumps({"rate": int(sample_rate)}))
            start = time.monotonic()
            for i in range(0, len(data), block):
                if cancelled():
                    ws.send(json.dumps({"cancel": True}))
                    return False
                ws.send(data[i:i + block].tobytes())
                # stay about 100 ms ahead of real time: cancel latency stays small
                ahead = start + i / sample_rate - 0.1 - time.monotonic()
                if ahead > 0:
                    self._wake.wait(ahead)
                    self._wake.clear()
            remaining = start + len(data) / sample_rate - time.monotonic()
            while remaining > 0:
                if cancelled():
                    ws.send(json.dumps({"cancel": True}))
                    return False
                self._wake.wait(min(remaining, 0.02))
                self._wake.clear()
                remaining = start + len(data) / sample_rate - time.monotonic()
            return True
        except Exception:
            self.ws = None  # reconnect on the next clip
            raise

    def stop(self):
        self._wake.set()

    def close(self):
        self.stop()
        if self.ws is not None:
            self.ws.close()


def make_player(device, gain):
    """device "none" -> NullPlayer; ws://... -> WebSocketPlayer; else a real output (fails loudly if absent)."""
    if device.strip().lower() == "none":
        return NullPlayer()
    if device.strip().startswith("ws://"):
        return WebSocketPlayer(device.strip(), gain)
    return SoundDevicePlayer(device.strip(), gain)
