import os
import unittest
from unittest.mock import patch

from aura_stt.config import LATENCY_LOOKAHEAD, SttConfig


class ConfigTests(unittest.TestCase):
    def test_all_supported_latencies(self):
        for latency, lookahead in ((80, 0), (160, 1), (560, 6), (1120, 13)):
            config = SttConfig.from_mapping({"model_dir": "/local/model", "latency_ms": latency})
            self.assertEqual(LATENCY_LOOKAHEAD[config.latency_ms], lookahead)
            self.assertGreater(config.tail_s, latency / 1000)

    def test_invalid_values_fail_at_startup(self):
        for values in ({"latency_ms": 200}, {"latency_ms": 560.0},
                       {"silence_s": 0}, {"pre_roll_s": -1},
                       {"max_utterance_s": 0.5}, {"device": "gpu"},
                       {"speech_threshold_db": float("nan")},
                       {"input_device": 1}, {"num_threads": 0}, {"audio_file": 1}, {"debug_audio": "true"},
                       {"input_sample_rate": -1}, {"capture_channels": -1},
                       {"capture_block_ms": 0}, {"input_latency": -1}, {"prefer_usb": "true"},
                       {"partial_topic": "/aura_transcript"}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                SttConfig.from_mapping({"model_dir": "/local/model", **values})
        with self.assertRaises(ValueError):
            SttConfig.from_mapping({"model_dir": ""})

    def test_device_selection_and_environment_default(self):
        with patch.dict(os.environ, {"AURA_STT_MODEL_DIR": "/local/model"}):
            self.assertEqual(SttConfig.defaults()["model_dir"], "/local/model")
        for requested, expected in (("", None), ("1", 1), ("USB microphone", "USB microphone")):
            self.assertEqual(SttConfig(input_device=requested).microphone_device, expected)


if __name__ == "__main__":
    unittest.main()
