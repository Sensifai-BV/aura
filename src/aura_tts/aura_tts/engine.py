"""sherpa-onnx text-to-speech with a cache of pre-synthesised phrases.

The model family is detected from the files in model_dir, so the A/B
candidates (Supertonic 3, Kokoro, Piper/VITS) all load through one class.
"""

import time
from pathlib import Path

import numpy as np


def detect_family(directory):
    d = Path(directory)
    if (d / "tts.json").is_file() and (d / "voice.bin").is_file():
        return "supertonic"
    if (d / "voices.bin").is_file():
        return "kokoro"
    if any(d.glob("*.onnx")) and (d / "tokens.txt").is_file():
        return "vits"
    raise FileNotFoundError(f"{d} is not a sherpa-onnx Supertonic, Kokoro or VITS/Piper model directory")


def _one(directory, pattern):
    matches = sorted(Path(directory).glob(pattern))
    if not matches:
        raise FileNotFoundError(f"{directory} has no {pattern}")
    return str(matches[0])


def model_config(directory, num_threads, provider="cpu"):
    import sherpa_onnx

    d = Path(directory)
    family = detect_family(d)
    if family == "supertonic":
        model = sherpa_onnx.OfflineTtsModelConfig(supertonic=sherpa_onnx.OfflineTtsSupertonicModelConfig(
            duration_predictor=_one(d, "duration_predictor*.onnx"), text_encoder=_one(d, "text_encoder*.onnx"),
            vector_estimator=_one(d, "vector_estimator*.onnx"), vocoder=_one(d, "vocoder*.onnx"),
            tts_json=str(d / "tts.json"), unicode_indexer=str(d / "unicode_indexer.bin"),
            voice_style=str(d / "voice.bin")))
    elif family == "kokoro":
        lexicon = d / "lexicon-us-en.txt"
        model = sherpa_onnx.OfflineTtsModelConfig(kokoro=sherpa_onnx.OfflineTtsKokoroModelConfig(
            model=_one(d, "model*.onnx"), voices=str(d / "voices.bin"), tokens=str(d / "tokens.txt"),
            lexicon=str(lexicon) if lexicon.is_file() else "",
            data_dir=str(d / "espeak-ng-data") if (d / "espeak-ng-data").is_dir() else "",
            dict_dir=str(d / "dict") if (d / "dict").is_dir() else "", lang="en-us"))
    else:
        model = sherpa_onnx.OfflineTtsModelConfig(vits=sherpa_onnx.OfflineTtsVitsModelConfig(
            model=_one(d, "*.onnx"), tokens=str(d / "tokens.txt"),
            data_dir=str(d / "espeak-ng-data") if (d / "espeak-ng-data").is_dir() else ""))
    model.num_threads = num_threads
    model.provider = provider
    return family, sherpa_onnx.OfflineTtsConfig(model=model)


class SherpaTts:
    def __init__(self, model_dir, num_threads=2, voice=0, speed=1.0, num_steps=5, provider="cpu"):
        import sherpa_onnx

        self.family, config = model_config(model_dir, num_threads, provider)
        self.tts = sherpa_onnx.OfflineTts(config)
        if not 0 <= voice < self.tts.num_speakers:
            raise ValueError(f"voice must be 0..{self.tts.num_speakers - 1} for this model")
        self.sample_rate = self.tts.sample_rate
        self.name = Path(model_dir).name
        self._sherpa = sherpa_onnx
        self.voice, self.speed, self.num_steps = voice, speed, num_steps

    def synthesize(self, text):
        """Text -> float32 mono samples at self.sample_rate."""
        if self.family == "supertonic":
            cfg = self._sherpa.GenerationConfig()
            cfg.sid, cfg.speed, cfg.num_steps = self.voice, self.speed, self.num_steps
            cfg.extra = {"lang": "en"}
            audio = self.tts.generate(text, cfg)
        else:
            audio = self.tts.generate(text, sid=self.voice, speed=self.speed)
        samples = np.asarray(audio.samples, dtype=np.float32)
        if samples.size == 0:
            raise RuntimeError(f"{self.family} produced no audio for {text!r}")
        return samples

    def warmup(self):
        self.synthesize("Speaker ready.")


class PhraseCache:
    """Pre-synthesised audio for fixed phrases (safety acknowledgements, prompts)."""

    def __init__(self, engine, phrases, prepare=lambda text: text):
        self.audio = {}
        t = time.perf_counter()
        for phrase in phrases:
            if phrase.strip():
                self.audio[self.key(phrase)] = engine.synthesize(prepare(phrase))
        self.build_s = time.perf_counter() - t

    @staticmethod
    def key(text):
        return " ".join(text.lower().split())

    def get(self, text):
        return self.audio.get(self.key(text))
