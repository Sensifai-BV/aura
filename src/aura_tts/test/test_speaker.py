import threading
import time

import numpy as np
import pytest

from aura_tts.config import TtsConfig
from aura_tts.engine import PhraseCache
from aura_tts.player import NullPlayer
from aura_tts.speaker import Speaker

SR = 1000


class FakeEngine:
    """0.1 s of audio per word, synthesised in `delay` seconds."""

    sample_rate = SR

    def __init__(self, delay=0.05):
        self.delay = delay
        self.calls = []

    def synthesize(self, text):
        self.calls.append(text)
        if "explode" in text:
            raise RuntimeError("model failure")
        time.sleep(self.delay)
        return np.zeros(SR // 10 * len(text.split()), dtype=np.float32)


class Recorder:
    def __init__(self):
        self.done, self.speaking = [], []
        self.finished = threading.Event()

    def on_done(self, u):
        self.done.append(u)
        self.finished.set()

    def wait(self, n, timeout=5.0):
        end = time.monotonic() + timeout
        while len(self.done) < n and time.monotonic() < end:
            time.sleep(0.01)
        assert len(self.done) >= n, f"only {len(self.done)} of {n} utterances finished"


def make(engine=None, cache=None, **kw):
    engine = engine or FakeEngine()
    rec = Recorder()
    speaker = Speaker(engine, NullPlayer(), cache, on_speaking=rec.speaking.append, on_done=rec.on_done, **kw)
    return speaker, engine, rec


def test_speaks_normalised_text_sentence_by_sentence():
    speaker, engine, rec = make()
    speaker.say("Valve V-104 is open. Pressure 4.5 bar.")
    rec.wait(1)
    speaker.close()
    u = rec.done[0]
    assert engine.calls == ["Valve vee one zero four is open.", "Pressure four point five bar."]
    assert not u.interrupted and not u.cached and not u.error
    assert 40 <= u.first_audio_ms < 500 and u.synth_ms > 0
    assert rec.speaking == [True, False]


def test_first_audio_after_first_sentence_only():
    speaker, engine, rec = make(FakeEngine(delay=0.2))
    speaker.say("One. Two. Three.")
    rec.wait(1)
    speaker.close()
    # audio starts after one synthesis, not three
    assert rec.done[0].first_audio_ms < 350


def test_newest_request_interrupts_current():
    speaker, engine, rec = make()
    speaker.say("This is a long status report with many words in it.")
    time.sleep(0.15)  # playing (1.1 s of audio)
    speaker.say("Stop, executing.")
    rec.wait(2)
    speaker.close()
    first, second = rec.done
    assert first.interrupted and first.text.startswith("This is a long")
    assert not second.interrupted and second.text == "Stop, executing."


def test_cached_phrase_skips_synthesis():
    engine = FakeEngine()
    cache = PhraseCache(engine, ["Stop, executing."])
    engine.calls.clear()
    speaker, _, rec = make(engine, cache)
    speaker.say("  stop,   EXECUTING. ")
    rec.wait(1)
    speaker.close()
    assert engine.calls == [] and rec.done[0].cached
    assert rec.done[0].first_audio_ms < 30


def test_failure_is_reported_and_speaker_survives():
    speaker, engine, rec = make()
    speaker.say("Please explode.")
    rec.wait(1)
    speaker.say("Still here.")
    rec.wait(2)
    speaker.close()
    assert "model failure" in rec.done[0].error
    assert not rec.done[1].error


def test_empty_and_long_text():
    speaker, engine, rec = make(max_chars=20)
    assert not speaker.say("   ")
    speaker.say("word " * 50)
    rec.wait(1)
    speaker.close()
    assert len(rec.done[0].text) <= 20


def test_normalisation_can_be_disabled():
    speaker, engine, rec = make(normalize_text=False)
    speaker.say("Valve V-104.")
    rec.wait(1)
    speaker.close()
    assert engine.calls == ["Valve V-104."]


def test_default_config_is_valid():
    cfg = TtsConfig.from_mapping(TtsConfig.defaults())
    assert cfg.num_threads == 2 and "Stop, executing." in cfg.cache_phrases


@pytest.mark.parametrize("change", [
    {"num_threads": 0}, {"speed": 3.0}, {"gain": 0}, {"voice": -1},
    {"speaking_topic": "/aura/speaker/say"}, {"model_dir": ""}, {"normalize_text": "yes"},
    {"debug": "true"}, {"metrics_dir": ""},
])
def test_config_rejects_bad_values(change):
    with pytest.raises(ValueError):
        TtsConfig.from_mapping({**TtsConfig.defaults(), **change})
