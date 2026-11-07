"""Does aura_tts.normalize fix the tag / number misreadings found in the A/B?

Synthesises tag- and number-heavy prompts with Supertonic 3, raw and normalised,
and checks whether each listener heard the tag and value tokens that matter
(WER against the raw text would penalise "84 degrees Celsius" for "84 °C").

    python normalize_check.py > normalize_check.txt
"""

import sys
from pathlib import Path

import numpy as np
import soxr

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src" / "aura_tts"))
from asr_eval import SR, StreamingTranscriber, WhisperModel, add_noise, nemotron, normalise, whisper  # noqa: E402
from aura_tts.engine import SherpaTts  # noqa: E402
from aura_tts.normalize import normalize, sentences  # noqa: E402

# prompt -> key tokens, in Whisper-normalised form with spaces removed
PROMPTS = {
    "Please confirm: inspect valve V-104 next to the north compressor.": ["v104", "valve"],
    "Thermal reading on motor M-12 is 84 °C.": ["m12", "84degreescelsius"],
    "Leak suspected near flange F-3. Taking a closer image.": ["leak", "f3"],
    "The vibration on pump P-201 is higher than normal.": ["p201"],
    "Route updated. Avoiding the wet floor near tank T-7.": ["t7"],
    "Pressure on PT-12B is 4.5 bar.": ["pt12b", "4.5bar"],
    "Methane at 20 %LEL near compressor K-3.": ["20%", "lowerexplosivelimit", "k3"],
    "Mission paused by operator at 14:30.": ["1430"],
    "Battery at 15%. Returning to dock D-2.": ["15%", "d2"],
    "Hazard zone ahead. Please keep a distance of 3 m.": ["3m"],
    "Should I go to separator V-2201?": ["v2201"],
    "Temperature on heat exchanger E-105 is -4 °C.": ["e105", "-4degreescelsius"],
}


def speak(tts, text):
    # as the node does: one synthesis per sentence, concatenated
    return np.concatenate([tts.synthesize(s) for s in sentences(text)])


def main():
    tts = SherpaTts(REPO / "src/aura_tts/model/supertonic-3-int8", num_threads=2)
    wmodel = WhisperModel("large-v3-turbo", device="cpu", compute_type="int8", cpu_threads=8)
    stt = StreamingTranscriber(str(REPO / "model"), device="cpu", latency_ms=560, num_threads=8)
    stt.warmup()
    scores = {}
    for mode in ("raw", "normalised"):
        for cond in ("clean", "snr5"):
            hits = {"stt": 0, "whisper": 0}
            total = 0
            for i, (text, keys) in enumerate(PROMPTS.items()):
                spoken = normalize(text) if mode == "normalised" else text
                audio = soxr.resample(speak(tts, spoken), tts.sample_rate, SR).astype(np.float32)
                if cond == "snr5":
                    audio = add_noise(audio, 5.0, 100 + i)
                heard = {"stt": nemotron(stt, audio), "whisper": whisper(wmodel, audio)[0]}
                total += len(keys)
                for name, hyp in heard.items():
                    compact = normalise(hyp).replace(" ", "")
                    found = [k for k in keys if k in compact]
                    hits[name] += len(found)
                    if cond == "clean":
                        missed = [k for k in keys if k not in found]
                        print(f"[{mode}/{name}] {hyp}" + (f"   MISSED {missed}" if missed else ""))
            scores[mode, cond] = (hits["stt"] / total, hits["whisper"] / total, total)
    print("\nKey tokens heard (tag and value), Supertonic 3, 2 threads")
    print("mode        cond    AURA STT   Whisper   keys")
    for (mode, cond), (n, w, total) in scores.items():
        print(f"{mode:<11} {cond:<6} {100 * n:8.0f}%  {100 * w:7.0f}%   {total}")


if __name__ == "__main__":
    main()
