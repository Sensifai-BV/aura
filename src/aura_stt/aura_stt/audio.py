"""Bounded, thread-safe audio storage and energy-based utterance endpoints."""

import threading
import time
from collections import deque

import numpy as np

SAMPLE_RATE = 16000


class SampleBuffer:
    """Overlapping reads use absolute sample positions, including left padding."""

    def __init__(self, capacity):
        self.capacity = capacity
        self._cv = threading.Condition()
        self._data = np.empty(0, dtype=np.float32)
        self._start = 0
        self._closed = False
        self.closed_at = None  # monotonic time of the endpoint, for final-result latency

    def write(self, block):
        with self._cv:
            if self._closed:
                return
            if len(self._data) + len(block) > self.capacity:
                raise BufferError("ASR audio backlog exceeded capacity")
            self._data = np.concatenate((self._data, block))
            self._cv.notify_all()

    def read(self, start, count):
        lead = min(count, max(0, -start))
        start += lead
        count -= lead
        with self._cv:
            if start < self._start:
                raise ValueError("Audio read precedes retained samples")
            while not self._closed and self._start + len(self._data) < start + count:
                self._cv.wait()
            if self._start + len(self._data) < start + count:
                return None
            offset = start - self._start
            chunk = self._data[offset:offset + count].copy()
            self._data = self._data[offset:]
            self._start = start
        return np.concatenate((np.zeros(lead, dtype=np.float32), chunk)) if lead else chunk

    def close(self):
        with self._cv:
            if self.closed_at is None:
                self.closed_at = time.monotonic()
            self._closed = True
            self._cv.notify_all()


class EchoGate:
    """Mute the microphone while the robot's own speaker plays.

    aura_tts publishes ``speaking`` true/false. Audio captured from the start of
    playback until ``tail_s`` after its end is replaced by silence before it
    reaches the segmenter, so the robot's own words never become a transcript and
    an operator answer right after a question starts a fresh utterance. Times are
    ``time.monotonic()`` of capture; the segmenter thread may lag behind, so
    the recent speaking intervals are kept rather than only the current state.
    """

    def __init__(self, tail_s=0.4, keep_s=30.0):
        self.tail_s = tail_s
        self.keep_s = keep_s
        self._lock = threading.Lock()
        self._intervals = deque()  # [start, end or None]

    def speaking(self, active, now=None):
        now = time.monotonic() if now is None else now
        with self._lock:
            current = self._intervals[-1] if self._intervals else None
            if active and (current is None or current[1] is not None):
                self._intervals.append([now, None])
            elif not active and current is not None and current[1] is None:
                current[1] = now
            while self._intervals and self._intervals[0][1] is not None \
                    and now - self._intervals[0][1] > self.keep_s:
                self._intervals.popleft()

    def muted(self, t):
        with self._lock:
            return any(start <= t and (end is None or t <= end + self.tail_s)
                       for start, end in self._intervals)


class UtteranceSegmenter:
    """Open a live buffer on speech, close on silence or a duration limit.

    This is an RMS gate, not a neural VAD. Pre-roll preserves word onsets;
    trailing silence lets the streaming encoder flush its right context.
    """

    def __init__(self, on_start, threshold_db=-40.0, silence_s=0.8,
                 pre_roll_s=0.3, max_utterance_s=20.0, tail_s=1.2):
        if not np.isfinite(threshold_db) or not -100 <= threshold_db <= 0:
            raise ValueError("threshold_db must be between -100 and 0")
        if not all(np.isfinite(v) for v in
                   (silence_s, pre_roll_s, max_utterance_s, tail_s)):
            raise ValueError("Endpoint durations must be finite")
        if silence_s <= 0 or pre_roll_s < 0 or max_utterance_s <= silence_s or tail_s < 0:
            raise ValueError("Invalid endpoint durations")
        self.on_start = on_start
        self.threshold = 10 ** (threshold_db / 20)
        self.silence_limit = int(silence_s * SAMPLE_RATE)
        self.pre_roll_limit = int(pre_roll_s * SAMPLE_RATE)
        self.max_samples = int(max_utterance_s * SAMPLE_RATE)
        self.tail_samples = int(tail_s * SAMPLE_RATE)
        self.capacity = self.max_samples + self.pre_roll_limit + self.tail_samples + SAMPLE_RATE
        self.history = deque()
        self.history_samples = 0
        self.active = None
        self.elapsed = 0
        self.silence = 0

    def push(self, block):
        speech = float(np.sqrt(np.mean(np.square(block)))) >= self.threshold
        if self.active is None:
            if not speech:
                self.history.append(block)
                self.history_samples += len(block)
                while self.history_samples > self.pre_roll_limit:
                    oldest = self.history.popleft()
                    excess = self.history_samples - self.pre_roll_limit
                    removed = min(excess, len(oldest))
                    if removed < len(oldest):
                        self.history.appendleft(oldest[removed:])
                    self.history_samples -= removed
                return
            self.active = SampleBuffer(self.capacity)
            for previous in self.history:
                self.active.write(previous)
            self.active.write(block)
            self.history.clear()
            self.history_samples = 0
            self.elapsed = len(block)
            self.silence = 0
            self.on_start(self.active)
        else:
            self.active.write(block)
            self.elapsed += len(block)
            self.silence = 0 if speech else self.silence + len(block)
        if self.silence >= self.silence_limit or self.elapsed >= self.max_samples:
            self.finish()

    def finish(self):
        if self.active is not None:
            try:
                self.active.write(np.zeros(self.tail_samples, dtype=np.float32))
            finally:
                self.active.close()
                self.active = None
