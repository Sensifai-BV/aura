"""Local Nemotron streaming inference, adapted from nemotron_stt/asr.py."""

from pathlib import Path

import numpy as np
import torch
from transformers import AutoModelForRNNT, AutoProcessor

from . import adapter
from .config import LATENCY_LOOKAHEAD


def pick_device(requested):
    if requested != "auto":
        return torch.device(requested)
    if torch.cuda.is_available():
        return torch.device("cuda")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


class TextSink:
    """The generate streamer protocol, emitting cumulative partial text."""

    def __init__(self, tokenizer, on_partial):
        self.tokenizer = tokenizer
        self.on_partial = on_partial
        self.tokens = []
        self.text = ""

    def put(self, value):
        self.tokens.extend(value.detach().cpu().reshape(-1).tolist())
        text = self.tokenizer.decode(self.tokens, skip_special_tokens=True).strip()
        if text and text != self.text:
            self.text = text
            self.on_partial(text)

    def end(self):
        pass


class StreamingTranscriber:
    def __init__(self, model_dir, device="auto", latency_ms=560, num_threads=4):
        if latency_ms not in LATENCY_LOOKAHEAD:
            raise ValueError("latency_ms must be 80, 160, 560 or 1120")
        directory = Path(model_dir).expanduser().resolve()
        base = adapter.base_of(directory)
        required = ("config.json", "processor_config.json", "tokenizer.json",
                    "tokenizer_config.json", "generation_config.json", "model.safetensors")
        missing = [name for name in required if not (base / name).is_file()]
        if missing:
            raise FileNotFoundError(f"Local Transformers model {base} is missing {missing}")
        self.device = pick_device(device)
        torch.set_num_threads(num_threads)
        self.processor = AutoProcessor.from_pretrained(str(base), local_files_only=True)
        self.model = AutoModelForRNNT.from_pretrained(str(base), local_files_only=True)
        if base != directory:
            adapter.attach(self.model, directory)
        self.model.to(self.device).eval()
        self.processor.set_num_lookahead_tokens(LATENCY_LOOKAHEAD[latency_ms])
        if self.processor.feature_extractor.sampling_rate != 16000:
            raise ValueError("The microphone pipeline requires a 16 kHz model")

    def warmup(self):
        inputs = self.processor(np.zeros(16000, dtype=np.float32),
                                sampling_rate=16000, return_tensors="pt")
        with torch.inference_mode():
            self.model.generate(**inputs.to(self.device, dtype=self.model.dtype))

    def transcribe(self, buffer, on_partial):
        p = self.processor
        fe = p.feature_extractor
        first = buffer.read(0, p.num_samples_first_audio_chunk)
        if first is None:
            return ""
        inputs = p(first, sampling_rate=16000, is_streaming=True,
                   is_first_audio_chunk=True, return_tensors="pt").to(
                       self.device, dtype=self.model.dtype)

        def chunks():
            yield inputs.input_features[:, :p.num_mel_frames_first_audio_chunk, :]
            mel_frame = p.num_mel_frames_first_audio_chunk
            while True:
                start = mel_frame * fe.hop_length - fe.n_fft // 2
                block = buffer.read(start, p.num_samples_per_audio_chunk)
                if block is None:
                    return
                yield p(block, sampling_rate=16000, is_streaming=True,
                        is_first_audio_chunk=False, return_tensors="pt").input_features
                mel_frame += p.num_mel_frames_per_audio_chunk

        sink = TextSink(p.tokenizer, on_partial)
        with torch.inference_mode():
            self.model.generate(**{**inputs, "input_features": chunks(), "streamer": sink})
        return sink.text
