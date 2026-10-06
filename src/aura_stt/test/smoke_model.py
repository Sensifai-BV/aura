"""Exercise the actual local weights without ROS2 or a microphone.

Run with PYTHONPATH=src/aura_stt python src/aura_stt/test/smoke_model.py
    --model-dir /path/to/model --wav /path/to/speech.wav
"""

import argparse
import wave

import numpy as np

from aura_stt.asr import StreamingTranscriber
from aura_stt.audio import SAMPLE_RATE, SampleBuffer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--wav", required=True)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--latency-ms", type=int, default=560)
    args = parser.parse_args()
    with wave.open(args.wav, "rb") as source:
        if (source.getframerate(), source.getnchannels(), source.getsampwidth()) != (16000, 1, 2):
            raise ValueError("Smoke test requires 16 kHz mono PCM16 WAV")
        audio = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2").astype(np.float32) / 32768
    engine = StreamingTranscriber(args.model_dir, args.device, args.latency_ms)
    buffer = SampleBuffer(len(audio) + SAMPLE_RATE * 2)
    buffer.write(audio)
    buffer.write(np.zeros(SAMPLE_RATE * 2, dtype=np.float32))
    buffer.close()
    partials = []
    text = engine.transcribe(buffer, partials.append)
    assert text and partials, "No transcript was decoded from the speech fixture"
    assert partials[-1] == text
    print(f"device={engine.device}; latency={args.latency_ms}ms; partials={len(partials)}")
    print(f"final={text}")


if __name__ == "__main__":
    main()
