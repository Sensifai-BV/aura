"""synth.py for Kitten TTS 2 (PyTorch speech LM, kittenml package).

Same outputs as synth.py (results/<name>/synth.jsonl, summary.json, wav/), so
asr_eval.py and report.py score it alongside the sherpa-onnx models. Runs in its
own venv (.venv-kitten) because kittenml pins its own torch / transformers.

    .venv-kitten/bin/python synth_kitten.py --decoder student_w4 --threads 4
"""

import argparse
import json
import resource
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from kittenml import KittenTTS

REPO = "KittenML/kitten-tts-2"


def rss_mb():
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / 1e6 if sys.platform == "darwin" else peak / 1e3


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--decoder", default="student_w4", help="default (459 MB) or student_w4 (39 MB)")
    ap.add_argument("--weights", default="packed", help="packed (ternary, 954 MB) or emb4 (469 MB)")
    ap.add_argument("--backend", default=None, help="None = PyTorch; mlx = Apple GPU")
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--voice", default="Bruno", help="repository default voice")
    ap.add_argument("--prompts", default=str(Path(__file__).with_name("prompts.txt")))
    ap.add_argument("--out", default=str(Path(__file__).with_name("results")))
    ap.add_argument("--name", default=None)
    args = ap.parse_args()

    torch.set_num_threads(args.threads)
    prompts = [p.strip() for p in Path(args.prompts).read_text().splitlines() if p.strip()]
    name = args.name or f"kitten2-{args.weights}-{args.decoder}{'-' + args.backend if args.backend else ''}"
    out = Path(args.out) / f"{name}_t{args.threads}"
    (out / "wav").mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()
    m = KittenTTS(REPO, device=args.device, decoder=args.decoder, weights=args.weights, backend=args.backend)
    load_s = time.perf_counter() - t0
    gen = dict(voice=args.voice, advanced={"seed": 7})
    for _ in range(2):
        m.generate("Warming up the speaker module.", **gen)

    rows = []
    for i, text in enumerate(prompts):
        for run in range(args.runs):
            t = time.perf_counter()
            first = None
            chunks = []
            for chunk in m.generate_stream(text, **gen):
                if first is None:
                    first = (time.perf_counter() - t) * 1000
                chunks.append(np.asarray(chunk, dtype=np.float32))
            ms = (time.perf_counter() - t) * 1000
            samples = np.concatenate(chunks) if chunks else np.zeros(0, np.float32)
            dur = len(samples) / m.sample_rate
            rows.append({"model": name, "threads": args.threads, "idx": i, "run": run, "text": text,
                         "chars": len(text), "synth_ms": ms, "first_chunk_ms": first, "audio_s": dur,
                         "rtf": ms / 1000 / dur if dur else float("nan"), "sr": m.sample_rate})
            print(f"{i:02d} {ms:7.0f} ms  audio {dur:4.1f}s  rtf {rows[-1]['rtf']:.2f}  {text}", flush=True)
            if run == 0:
                sf.write(out / "wav" / f"{i:02d}.wav", samples, m.sample_rate)

    summary = {"model": name, "threads": args.threads, "load_s": load_s, "peak_rss_mb": rss_mb(),
               "base_rss_mb": 0.0, "onnx_mb": 0.0, "sample_rate": m.sample_rate, "num_speakers": len(m.available_voices),
               "device": args.device, "backend": args.backend or "torch", "decoder": args.decoder,
               "weights": args.weights}
    (out / "synth.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
