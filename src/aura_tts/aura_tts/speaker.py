"""Speech queue of the speaker module, free of ROS.

Dialogue is turn-based, so the newest request wins: a new utterance cuts the
one playing and drops any not yet started (a "Stop, executing." must never wait
behind a long report). Text is split into sentences; the next sentence is
synthesised while the current one plays, so audio starts after the first
sentence. Cached phrases skip synthesis entirely.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from .normalize import normalize, sentences


@dataclass
class Utterance:
    text: str
    received: float  # time.monotonic() when the request arrived
    first_audio_ms: float = 0.0  # request -> playback start
    synth_ms: float = 0.0        # total synthesis time, all sentences
    cached: bool = False
    interrupted: bool = False
    error: str = ""
    audio_s: float = 0.0         # audio played (all sentences, until an interruption)


class Speaker:
    def __init__(self, engine, player, cache=None, normalize_text=True, max_chars=400,
                 on_speaking=None, on_done=None):
        self.engine, self.player, self.cache = engine, player, cache
        self.normalize_text, self.max_chars = normalize_text, max_chars
        self.on_speaking = on_speaking or (lambda speaking: None)
        self.on_done = on_done or (lambda utterance: None)
        self._cv = threading.Condition()
        self._next = None
        self._generation = 0
        self._closed = False
        self._synth = ThreadPoolExecutor(1, thread_name_prefix="aura-tts-synth")
        self.thread = threading.Thread(target=self._run, name="aura-tts", daemon=True)
        self.thread.start()

    def say(self, text):
        """Queue text, interrupting whatever is playing. Returns False for empty text."""
        text = " ".join(text.split())[:self.max_chars]
        if not text:
            return False
        with self._cv:
            self._next = Utterance(text, time.monotonic())
            self._generation += 1
            self._cv.notify_all()
        self.player.stop()
        return True

    def close(self):
        with self._cv:
            self._closed = True
            self._generation += 1
            self._cv.notify_all()
        self.player.stop()
        self.thread.join(timeout=5)
        self._synth.shutdown(wait=False, cancel_futures=True)

    def _superseded(self, generation):
        return self._closed or self._generation != generation

    def _synthesize(self, text):
        t = time.perf_counter()
        audio = self.engine.synthesize(normalize(text) if self.normalize_text else text)
        return audio, (time.perf_counter() - t) * 1000

    def _run(self):
        while True:
            with self._cv:
                while self._next is None and not self._closed:
                    self._cv.wait()
                if self._closed:
                    return
                u, self._next = self._next, None
                generation = self._generation
            try:
                self._speak(u, generation)
            except Exception as exc:  # noqa: BLE001 - one bad utterance must not kill the speaker
                u.error = f"{type(exc).__name__}: {exc}"
            finally:
                self.on_speaking(False)
                self.on_done(u)

    def _speak(self, u, generation):
        clip = self.cache.get(u.text) if self.cache else None
        if clip is not None:
            u.cached = True
            parts = [clip]
        else:
            parts = sentences(u.text) or [u.text]
        sr = self.engine.sample_rate
        pending = None if u.cached else self._synth.submit(self._synthesize, parts[0])
        for i in range(len(parts)):
            if u.cached:
                audio = parts[0]
            else:
                audio, ms = pending.result()
                u.synth_ms += ms
                pending = self._synth.submit(self._synthesize, parts[i + 1]) if i + 1 < len(parts) else None
            if self._superseded(generation):
                u.interrupted = True
                return
            if i == 0:
                u.first_audio_ms = (time.monotonic() - u.received) * 1000
                self.on_speaking(True)
            u.audio_s += len(audio) / sr
            if not self.player.play(audio, sr, lambda: self._superseded(generation)):
                u.interrupted = True
                return
