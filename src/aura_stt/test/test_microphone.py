import unittest

import numpy as np
import soxr

from aura_stt.config import SttConfig
from aura_stt.microphone import AudioConverter, MicrophoneCapture, select_microphone


class FakeAudio:
    def __init__(self):
        self.devices = [
            {"name": "Built-in microphone", "max_input_channels": 1, "default_samplerate": 48000},
            {"name": "USB microphone", "max_input_channels": 2, "default_samplerate": 48000},
            {"name": "default", "max_input_channels": 128, "default_samplerate": 48000},
        ]

    def query_devices(self):
        return self.devices

    def check_input_settings(self, device, channels, dtype, samplerate):
        if samplerate != 48000 or channels > self.devices[device]["max_input_channels"]:
            raise ValueError("Unsupported hardware format")

    def InputStream(self, **kwargs):
        self.stream_settings = kwargs
        return type("Stream", (), {"stop": lambda self: None, "close": lambda self: None})()


class MicrophoneTests(unittest.TestCase):
    def test_auto_selects_physical_usb_at_native_stereo_rate(self):
        device = select_microphone(SttConfig(), FakeAudio())
        self.assertEqual((device.index, device.sample_rate, device.channels), (1, 48000, 2))

    def test_explicit_device_can_select_virtual_input(self):
        device = select_microphone(SttConfig(input_device="2", capture_channels=1), FakeAudio())
        self.assertEqual((device.index, device.channels), (2, 1))
        audio = FakeAudio()
        audio.devices.append({"name": "sysdefault", "max_input_channels": 2,
                              "default_samplerate": 48000})
        self.assertEqual(select_microphone(SttConfig(input_device="default"), audio).index, 2)
        with self.assertRaises(ValueError):
            select_microphone(SttConfig(input_device="not a microphone"), FakeAudio())

    def test_internal_fallback_and_requested_rate_validation(self):
        audio = FakeAudio()
        audio.devices = audio.devices[:1]
        self.assertEqual(select_microphone(SttConfig(), audio).index, 0)
        with self.assertRaises(RuntimeError):
            select_microphone(SttConfig(input_sample_rate=16000), audio)

    def test_any_input_name_remains_eligible(self):
        for name in ("USB microphone", "Vendor USB audio", "Bluetooth headset",
                     "Onboard ADC", "HDMI capture", "Loopback", "Monitor input",
                     "pulse", "default"):
            with self.subTest(name=name):
                audio = FakeAudio()
                audio.devices = [{"name": name, "max_input_channels": 1,
                                  "default_samplerate": 48000}]
                self.assertEqual(select_microphone(SttConfig(), audio).name, name)

    def test_auto_probes_mono_and_multichannel_hardware(self):
        for advertised, supported in ((2, 1), (4, 4)):
            with self.subTest(channels=supported):
                audio = FakeAudio()
                audio.devices = [{"name": "Audio interface", "max_input_channels": advertised,
                                  "default_samplerate": 48000}]

                def check_input_settings(device, channels, dtype, samplerate):
                    if channels != supported or samplerate != 48000:
                        raise ValueError("Unsupported hardware format")

                audio.check_input_settings = check_input_settings
                self.assertEqual(select_microphone(SttConfig(), audio).channels, supported)
                self.assertEqual(select_microphone(SttConfig(capture_channels=supported), audio).channels, supported)

    def test_unusable_external_input_falls_back_to_internal_input(self):
        audio = FakeAudio()
        original = audio.check_input_settings

        def check_input_settings(device, **settings):
            if device == 1:
                raise ValueError("Unavailable device")
            original(device=device, **settings)

        audio.check_input_settings = check_input_settings
        self.assertEqual(select_microphone(SttConfig(), audio).index, 0)

    def test_unusual_native_sample_rate_is_supported(self):
        audio = FakeAudio()
        audio.devices = [{"name": "Audio interface", "max_input_channels": 1,
                          "default_samplerate": 96000}]

        def check_input_settings(device, channels, dtype, samplerate):
            if samplerate != 96000:
                raise ValueError("Unsupported sample rate")

        audio.check_input_settings = check_input_settings
        self.assertEqual(select_microphone(SttConfig(), audio).sample_rate, 96000)

    def test_streaming_resampling_preserves_boundaries_and_channel_signal(self):
        time = np.arange(48000, dtype=np.float32) / 48000
        signal = (0.5 * np.sin(2 * np.pi * 440 * time)).astype(np.float32)
        stereo = np.column_stack((np.zeros_like(signal), signal))
        expected = soxr.resample(stereo.mean(axis=1), 48000, 16000, quality="HQ")
        for chunk_size in (4800, 137):
            converter = AudioConverter(48000)
            blocks = []
            for start in range(0, len(stereo), chunk_size):
                end = min(start + chunk_size, len(stereo))
                blocks.extend(converter.process(stereo[start:end], last=end == len(stereo)))
            result = np.concatenate(blocks)
            self.assertEqual(len(result), 16000)
            self.assertTrue(all(len(block) == 320 for block in blocks))
            np.testing.assert_allclose(result, expected, atol=1e-6)
            self.assertGreater(float(abs(result).max()), 0.2)

    def test_capture_queue_is_bounded_and_counts_discontinuities(self):
        audio = FakeAudio()
        errors = []
        capture = MicrophoneCapture(SttConfig(), lambda block: None,
                                    lambda: None, errors.append, audio_api=audio)
        try:
            for index in range(25):
                capture._capture(np.full((4800, 2), index, dtype=np.float32), 4800, None, index == 0)
            self.assertEqual(capture.queue.qsize(), 20)
            self.assertEqual(capture.dropped_blocks, 5)
            self.assertEqual(capture.xruns, 1)
            self.assertEqual(float(capture.queue.get_nowait()[0, 0]), 5)
            self.assertFalse(errors)
            self.assertEqual(audio.stream_settings["samplerate"], 48000)
            self.assertEqual(audio.stream_settings["channels"], 2)
            self.assertEqual(audio.stream_settings["latency"], "high")
        finally:
            capture.close()


if __name__ == "__main__":
    unittest.main()
