"""Live microphone level meter on the host: shows dBFS and how much of the energy is in the voice band.

    python level_meter.py [seconds]
Speech at normal distance should reach about -25 to -15 dBFS with most energy in 150 Hz to 4 kHz.
"""
import sys
import time

import numpy as np
import sounddevice as sd

SR = 48000
seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0
print(f"Level meter for {seconds:.0f} s on '{sd.query_devices(kind='input')['name']}' - speak toward the laptop", flush=True)


def callback(x, frames, t, status):
    v = x[:, 0]
    db = 20 * np.log10(np.sqrt(np.mean(v ** 2)) + 1e-9)
    spectrum = np.abs(np.fft.rfft(v * np.hanning(len(v)))) ** 2
    f = np.fft.rfftfreq(len(v), 1 / SR)
    voice = 100 * spectrum[(f >= 150) & (f < 4000)].sum() / (spectrum.sum() + 1e-12)
    print(f"{db:6.1f} dBFS  voice-band {voice:3.0f}%  " + "#" * max(0, int((db + 70) / 1.5)), flush=True)


with sd.InputStream(samplerate=SR, channels=1, blocksize=SR // 5, callback=callback):
    time.sleep(seconds)
