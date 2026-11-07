"""Record each guided phrase straight from the host microphone (no live pipeline), for offline scoring.

    python record_guided.py --out runs/rec        # then run score_offline.py in the ROS 2 VM
Per phrase: a tick sound, then 4.5 s of recording (16 kHz mono WAV) named NN.wav, plus phrases.json.
"""
import argparse
import json
import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd
import soxr

sys.path.insert(0, str(Path(__file__).parent))
from guided_commands import SET  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--out", default="runs/rec")
ap.add_argument("--seconds", type=float, default=4.5)
a = ap.parse_args()
out = Path(a.out)
out.mkdir(parents=True, exist_ok=True)
sr = int(sd.query_devices(kind="input")["default_samplerate"])
print(f"\n{len(SET)} phrases, {a.seconds:.1f} s each. Say the phrase right after the tick sound. Starting in 4 s...",
      flush=True)
time.sleep(4)
meta = []
for i, (phrase, intent, decision) in enumerate(SET, 1):
    print(f"\n[{i}/{len(SET)}]  SAY:  \033[1m{phrase}\033[0m", flush=True)
    subprocess.run(["afplay", "/System/Library/Sounds/Tink.aiff"])
    x = sd.rec(int(sr * a.seconds), samplerate=sr, channels=1, dtype="float32")
    sd.wait()
    y = soxr.resample(x[:, 0], sr, 16000)
    with wave.open(str(out / f"{i:02d}.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes((np.clip(y, -1, 1) * 32767).astype("<i2").tobytes())
    meta.append({"n": i, "phrase": phrase, "expect_intent": intent, "expect_decision": decision})
    json.dump(meta, open(out / "phrases.json", "w"), indent=1)
    print(f"      peak {20 * np.log10(abs(y).max() + 1e-9):.0f} dBFS", flush=True)
    time.sleep(1.0)
print("\nDone. Thank you.", flush=True)
