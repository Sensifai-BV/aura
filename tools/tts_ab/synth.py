"""Synthesise every prompt with one TTS model and record latency and memory.

Run one model per process so peak RSS and load time are clean:
    python synth.py --model piper --threads 4 --out results
"""

import argparse
import json
import os
import resource
import sys
import time
from pathlib import Path

import numpy as np
import sherpa_onnx
import soundfile as sf

ROOT = Path(__file__).resolve().parents[2] / "model" / "tts"

PIPER = ROOT / "vits-piper-en_US-lessac-medium"
KOKORO = ROOT / "kokoro-int8-multi-lang-v1_0"
SUPERTONIC = ROOT / "sherpa-onnx-supertonic-3-tts-int8-2026-05-11"

# Speaker id per model: Kokoro 3 = af_heart (its best-graded English voice);
# Piper lessac has one speaker; Supertonic uses the bundled default style.
VOICE = {"piper": 0, "kokoro": 3, "supertonic": 0}


def model_config(name, threads):
    if name == "piper":
        vits = sherpa_onnx.OfflineTtsVitsModelConfig(
            model=str(PIPER / "en_US-lessac-medium.onnx"),
            tokens=str(PIPER / "tokens.txt"),
            data_dir=str(PIPER / "espeak-ng-data"))
        return sherpa_onnx.OfflineTtsModelConfig(vits=vits, num_threads=threads)
    if name == "kokoro":
        kokoro = sherpa_onnx.OfflineTtsKokoroModelConfig(
            model=str(KOKORO / "model.int8.onnx"),
            voices=str(KOKORO / "voices.bin"),
            tokens=str(KOKORO / "tokens.txt"),
            lexicon=str(KOKORO / "lexicon-us-en.txt"),
            data_dir=str(KOKORO / "espeak-ng-data"),
            dict_dir=str(KOKORO / "dict"),
            lang="en-us")
        return sherpa_onnx.OfflineTtsModelConfig(kokoro=kokoro, num_threads=threads)
    if name == "supertonic":
        st = sherpa_onnx.OfflineTtsSupertonicModelConfig(
            duration_predictor=str(SUPERTONIC / "duration_predictor.int8.onnx"),
            text_encoder=str(SUPERTONIC / "text_encoder.int8.onnx"),
            vector_estimator=str(SUPERTONIC / "vector_estimator.int8.onnx"),
            vocoder=str(SUPERTONIC / "vocoder.int8.onnx"),
            tts_json=str(SUPERTONIC / "tts.json"),
            unicode_indexer=str(SUPERTONIC / "unicode_indexer.bin"),
            voice_style=str(SUPERTONIC / "voice.bin"))
        return sherpa_onnx.OfflineTtsModelConfig(supertonic=st, num_threads=threads)
    raise ValueError(name)


def generate(tts, name, text, sid):
    if name == "supertonic":
        cfg = sherpa_onnx.GenerationConfig()
        cfg.sid = sid
        cfg.speed = 1.0
        cfg.num_steps = 5
        cfg.extra = {"lang": "en"}
        return tts.generate(text, cfg)
    return tts.generate(text, sid=sid, speed=1.0)


def rss_mb():
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / 1e6 if sys.platform == "darwin" else peak / 1e3


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, choices=sorted(VOICE))
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--prompts", default=str(Path(__file__).with_name("prompts.txt")))
    ap.add_argument("--out", default=str(Path(__file__).with_name("results")))
    args = ap.parse_args()

    prompts = [p.strip() for p in Path(args.prompts).read_text().splitlines() if p.strip()]
    out = Path(args.out) / f"{args.model}_t{args.threads}"
    (out / "wav").mkdir(parents=True, exist_ok=True)

    base_rss = rss_mb()
    t0 = time.perf_counter()
    tts = sherpa_onnx.OfflineTts(sherpa_onnx.OfflineTtsConfig(model=model_config(args.model, args.threads)))
    load_s = time.perf_counter() - t0
    sid = VOICE[args.model]
    for _ in range(2):
        generate(tts, args.model, "Warming up the speaker module.", sid)

    rows = []
    for i, text in enumerate(prompts):
        for run in range(args.runs):
            t = time.perf_counter()
            audio = generate(tts, args.model, text, sid)
            ms = (time.perf_counter() - t) * 1000
            samples = np.asarray(audio.samples, dtype=np.float32)
            dur = len(samples) / audio.sample_rate
            rows.append({"model": args.model, "threads": args.threads, "idx": i, "run": run,
                         "text": text, "chars": len(text), "synth_ms": ms, "audio_s": dur,
                         "rtf": ms / 1000 / dur if dur else float("nan"), "sr": audio.sample_rate})
            if run == 0:
                sf.write(out / "wav" / f"{i:02d}.wav", samples, audio.sample_rate)

    size_mb = sum(f.stat().st_size for f in
                  (PIPER if args.model == "piper" else KOKORO if args.model == "kokoro" else SUPERTONIC)
                  .glob("*.onnx")) / 1e6
    summary = {"model": args.model, "threads": args.threads, "load_s": load_s,
               "peak_rss_mb": rss_mb(), "base_rss_mb": base_rss, "onnx_mb": size_mb,
               "sample_rate": rows[0]["sr"], "num_speakers": tts.num_speakers, "pid": os.getpid()}
    (out / "synth.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
