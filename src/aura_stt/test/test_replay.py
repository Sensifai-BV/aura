import tempfile
import threading
import unittest
import wave
from pathlib import Path

import numpy as np

from aura_stt.replay import WaveReplay


class ReplayTests(unittest.TestCase):
    def test_pcm_conversion_and_end_of_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "speech.wav"
            expected = np.arange(640, dtype="<i2")
            with wave.open(str(path), "wb") as source:
                source.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                source.writeframes(expected.tobytes())
            blocks, errors = [], []
            finished = threading.Event()
            replay = WaveReplay(path, lambda audio, *args: blocks.append(audio),
                                finished.set, errors.append)
            try:
                replay.start()
                self.assertTrue(finished.wait(2))
            finally:
                replay.close()
            self.assertFalse(errors)
            np.testing.assert_array_equal(np.concatenate(blocks)[:, 0], expected.astype(np.float32) / 32768)

    def test_incompatible_sample_rate_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "speech.wav"
            with wave.open(str(path), "wb") as source:
                source.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
                source.writeframes(bytes(640))
            with self.assertRaises(ValueError):
                WaveReplay(path, None, None, None)


if __name__ == "__main__":
    unittest.main()
