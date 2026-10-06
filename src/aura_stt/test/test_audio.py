import threading
import unittest

import numpy as np

from aura_stt.audio import SampleBuffer, UtteranceSegmenter


class AudioTests(unittest.TestCase):
    def test_negative_start_and_overlap(self):
        buffer = SampleBuffer(1000)
        buffer.write(np.arange(100, dtype=np.float32))
        np.testing.assert_array_equal(buffer.read(-3, 8), [0, 0, 0, 0, 1, 2, 3, 4])
        np.testing.assert_array_equal(buffer.read(2, 6), np.arange(2, 8))
        buffer.close()
        self.assertIsNone(buffer.read(98, 4))

    def test_close_unblocks_reader(self):
        buffer = SampleBuffer(100)
        result = []
        reader = threading.Thread(target=lambda: result.append(buffer.read(0, 10)))
        reader.start()
        buffer.close()
        reader.join(timeout=1)
        self.assertFalse(reader.is_alive())
        self.assertEqual(result, [None])

    def test_backlog_is_bounded(self):
        buffer = SampleBuffer(10)
        buffer.write(np.ones(10, dtype=np.float32))
        with self.assertRaises(BufferError):
            buffer.write(np.ones(1, dtype=np.float32))

    def test_pause_endpoints_pre_roll_and_tail(self):
        sessions = []
        segmenter = UtteranceSegmenter(sessions.append, silence_s=0.04,
                                      pre_roll_s=0.04, tail_s=0.02)
        silence = np.zeros(320, dtype=np.float32)
        speech = np.full(320, 0.1, dtype=np.float32)
        for _ in range(20):
            segmenter.push(silence)
        self.assertFalse(sessions)
        segmenter.push(speech)
        self.assertEqual(len(sessions), 1)
        self.assertIsNotNone(segmenter.active)
        segmenter.push(silence)
        segmenter.push(silence)
        self.assertIsNone(segmenter.active)
        samples = sessions[0].read(0, 1920)
        np.testing.assert_array_equal(samples[:640], np.zeros(640))
        np.testing.assert_array_equal(samples[640:960], speech)
        self.assertIsNone(sessions[0].read(1920, 1))
        segmenter.push(speech)
        self.assertEqual(len(sessions), 2)
        segmenter.finish()

    def test_duration_limit_splits_continuous_speech(self):
        sessions = []
        segmenter = UtteranceSegmenter(sessions.append, silence_s=0.02,
                                      pre_roll_s=0, max_utterance_s=0.06, tail_s=0)
        for _ in range(6):
            segmenter.push(np.full(320, 0.1, dtype=np.float32))
        self.assertEqual(len(sessions), 2)
        self.assertIsNone(segmenter.active)
        for buffer in sessions:
            np.testing.assert_array_equal(buffer.read(0, 960), np.full(960, 0.1, dtype=np.float32))


if __name__ == "__main__":
    unittest.main()
