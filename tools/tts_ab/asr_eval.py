"""Score synthesised speech for intelligibility: WER and ASR confidence.

Two listeners transcribe every WAV from synth.py:
  * Whisper large-v3-turbo (independent reference; gives word probabilities)
  * the AURA streaming STT (Nemotron RNNT, the robot's own ear, src/aura_stt)
Each clip is scored clean and with pink noise at 5 dB SNR (plant noise proxy).

    python asr_eval.py results/piper_t4 results/kokoro_t4 results/supertonic_t4
"""

import argparse
import json
import re
import sys
from pathlib import Path

import jiwer
import numpy as np
import soundfile as sf
import soxr
from faster_whisper import WhisperModel
from transformers.models.whisper.english_normalizer import EnglishTextNormalizer

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src" / "aura_stt"))
from aura_stt.asr import StreamingTranscriber  # noqa: E402
from aura_stt.audio import SampleBuffer  # noqa: E402

SR = 16000
SNR_DB = 5.0
_norm = EnglishTextNormalizer({})


def normalise(text):
    text = _norm(text)
    # "v 104", "v104" and "v-104" are the same tag.
    return re.sub(r"\b([a-z])\s+(?=\d)", r"\1", text).strip()


def pink_noise(n, rng):
    spectrum = np.fft.rfft(rng.standard_normal(n))
    f = np.arange(len(spectrum))
    f[0] = 1
    noise = np.fft.irfft(spectrum / np.sqrt(f), n)
    return noise / np.std(noise)


def add_noise(clean, snr_db, seed):
    noise = pink_noise(len(clean), np.random.default_rng(seed))
    gain = np.sqrt(np.mean(clean ** 2) / 10 ** (snr_db / 10))
    mixed = clean + gain * noise
    return (mixed / max(1.0, np.max(np.abs(mixed)))).astype(np.float32)


def nemotron(stt, audio):
    pad = np.zeros(SR // 2, dtype=np.float32)
    buf = SampleBuffer(capacity=SR * 60)
    buf.write(np.concatenate((pad, audio, pad, pad)))
    buf.close()
    return stt.transcribe(buf, lambda _: None)


def whisper(model, audio):
    segments, _ = model.transcribe(audio, language="en", beam_size=5, word_timestamps=True,
                                   condition_on_previous_text=False, vad_filter=False)
    words = [w for s in segments for w in (s.words or [])]
    text = "".join(w.word for w in words).strip()
    probs = [w.probability for w in words]
    return text, (float(np.mean(probs)) if probs else 0.0), (float(np.min(probs)) if probs else 0.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="+")
    ap.add_argument("--stt-model", default=str(REPO / "model"))
    args = ap.parse_args()

    wmodel = WhisperModel("large-v3-turbo", device="cpu", compute_type="int8", cpu_threads=8)
    stt = StreamingTranscriber(args.stt_model, device="cpu", latency_ms=560, num_threads=8)
    stt.warmup()

    for d in map(Path, args.dirs):
        synth = [json.loads(l) for l in (d / "synth.jsonl").read_text().splitlines()]
        refs = {r["idx"]: r["text"] for r in synth}
        rows = []
        for idx, ref in sorted(refs.items()):
            audio, sr = sf.read(d / "wav" / f"{idx:02d}.wav", dtype="float32")
            audio = soxr.resample(audio, sr, SR).astype(np.float32)
            for cond, clip in (("clean", audio), (f"snr{int(SNR_DB)}", add_noise(audio, SNR_DB, idx))):
                w_text, w_conf, w_min = whisper(wmodel, clip)
                n_text = nemotron(stt, clip)
                r = normalise(ref)
                rows.append({"idx": idx, "cond": cond, "ref": ref,
                             "whisper": w_text, "whisper_conf": w_conf, "whisper_min_conf": w_min,
                             "whisper_wer": jiwer.wer(r, normalise(w_text) or "<empty>"),
                             "nemotron": n_text,
                             "nemotron_wer": jiwer.wer(r, normalise(n_text) or "<empty>"),
                             "ref_words": len(r.split())})
                print(d.name, idx, cond, f"w={rows[-1]['whisper_wer']:.2f}",
                      f"n={rows[-1]['nemotron_wer']:.2f}", f"c={w_conf:.2f}", flush=True)
        (d / "asr.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))


if __name__ == "__main__":
    main()
