"""Decode recorded phrases with the STT model and parse them with the intent model; print a scorecard.

    python score_offline.py runs/rec [--latency-ms 560]      (in the ROS 2 VM, ML venv)
Decision follows aura_intent's rules with confirm_mode=never: none -> IGNORE, unknown or p < 0.52 -> REJECT.
"""
import argparse
import json
import re
import wave
from pathlib import Path

import numpy as np
from aura_intent.config import IntentConfig, resolve_model_path
from aura_intent.model import IntentModel
from aura_stt.asr import StreamingTranscriber
from aura_stt.audio import SampleBuffer


def norm(t):
    t = re.sub(r"[^a-z0-9 ]", "", t.lower())
    t = re.sub(r"^(hey )?(artaban|robot|unit one) ?", "", t)
    return " ".join(t.split())


ap = argparse.ArgumentParser()
ap.add_argument("dir")
ap.add_argument("--latency-ms", type=int, default=560)
ap.add_argument("--highpass", type=float, default=0, help="Butterworth high-pass cutoff in Hz before decoding")
ap.add_argument("--normalize", action="store_true", help="scale each recording to a 0.5 peak before decoding")
a = ap.parse_args()
meta = json.load(open(Path(a.dir) / "phrases.json"))
stt = StreamingTranscriber("model", "cpu", a.latency_ms, 4)
intent = IntentModel(resolve_model_path(IntentConfig.model_path), 4)
intent.warmup()
ok = stt_ok = 0
print(f"{'#':>2}  {'said':36} {'STT heard':36} {'intent':14} {'p':>5} {'decision':8}  result")
for m in meta:
    with wave.open(str(Path(a.dir) / f"{m['n']:02d}.wav")) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768
    if a.highpass:
        from scipy.signal import butter, sosfilt
        x = sosfilt(butter(4, a.highpass, "highpass", fs=16000, output="sos"), x).astype(np.float32)
    if a.normalize:
        x = x / (np.abs(x).max() + 1e-6) * 0.5
    buf = SampleBuffer(len(x) + 128000)
    buf.write(x)
    buf.write(np.zeros(48000, np.float32))
    buf.close()
    heard = stt.transcribe(buf, lambda t: None)
    if heard.strip():
        p = intent.parse(heard)
        i_name, conf = p.intent, p.confidence
        dec = "IGNORE" if i_name == "none" else "REJECT" if (i_name == "unknown" or conf < 0.52) else "ACCEPT"
        i_name = "unknown" if i_name == "none" else i_name  # ignored side talk is published as intent unknown
    else:
        i_name, conf, dec = "-", 0.0, "-"
    s_ok = norm(heard) == norm(m["phrase"])
    i_ok = i_name == m["expect_intent"] and dec == m["expect_decision"]
    stt_ok += s_ok
    ok += i_ok
    print(f"{m['n']:>2}  {m['phrase'][:36]:36} {heard[:36] or '(nothing)':36} {i_name:14} {conf:5.2f} {dec:8}  "
          f"{'PASS' if i_ok else 'FAIL'}{'' if s_ok else '  (STT differs)'}", flush=True)
n = len(meta)
print(f"\nintent+decision correct: {ok}/{n}   STT exact (ignoring wake word/case/punctuation): {stt_ok}/{n}")
