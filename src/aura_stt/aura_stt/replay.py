"""Replay recorded microphone audio at real time for repeatable ROS2 debugging."""

import threading
import time
import wave
from pathlib import Path

import numpy as np

from .audio import SAMPLE_RATE


class WaveReplay:
    def __init__(self, path, callback, finished_callback, on_error, blocksize=320):
        self.source = wave.open(str(Path(path).expanduser()), "rb")
        if (self.source.getframerate(), self.source.getnchannels(), self.source.getsampwidth()) != (SAMPLE_RATE, 1, 2):
            self.source.close()
            raise ValueError("audio_file must be 16 kHz mono PCM16 WAV")
        self.callback = callback
        self.finished_callback = finished_callback
        self.on_error = on_error
        self.blocksize = blocksize
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, name="aura-replay", daemon=True)

    def start(self):
        self.thread.start()

    def _run(self):
        deadline = time.monotonic()
        try:
            while not self.stop_event.is_set():
                raw = self.source.readframes(self.blocksize)
                if not raw:
                    self.finished_callback()
                    return
                audio = np.frombuffer(raw, dtype="<i2").astype(np.float32).reshape(-1, 1) / 32768
                self.callback(audio, len(audio), None, False)
                deadline += len(audio) / SAMPLE_RATE
                self.stop_event.wait(max(0, deadline - time.monotonic()))
        except Exception as exc:
            self.on_error(exc)

    def stop(self):
        self.stop_event.set()
        if self.thread.ident is not None:
            self.thread.join(timeout=2)

    def close(self):
        self.stop()
        self.source.close()
