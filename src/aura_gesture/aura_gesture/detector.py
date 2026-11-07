"""ONNX Runtime gesture detector for NMS-free YOLOv10 exports (1 x N x 6 output)."""

import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .labels import ACTIONABLE, HAGRID_LABELS, NONE, canonical


@dataclass(frozen=True)
class Detection:
    label: str  # raw model class
    gesture: str  # canonical gesture or 'none'
    score: float
    box: tuple  # x1, y1, x2, y2 in source pixels


def letterbox(bgr, size, pad=114):
    """Resize the long side to ``size`` and centre-pad, as Ultralytics does."""
    h, w = bgr.shape[:2]
    scale = size / max(h, w)
    nh, nw = round(h * scale), round(w * scale)
    out = np.full((size, size, 3), pad, np.uint8)
    top, left = (size - nh) // 2, (size - nw) // 2
    out[top:top + nh, left:left + nw] = cv2.resize(bgr, (nw, nh), interpolation=cv2.INTER_LINEAR)
    return out, scale, left, top


class OnnxGestureDetector:
    def __init__(self, model_path, num_threads=4, input_size=640, providers=None, labels=HAGRID_LABELS):
        import onnxruntime as ort

        path = Path(model_path)
        if not path.is_file():
            raise FileNotFoundError(f"Gesture model not found: {path}")
        options = ort.SessionOptions()
        options.intra_op_num_threads = num_threads
        options.inter_op_num_threads = 1
        available = ort.get_available_providers()
        wanted = providers or ["TensorrtExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider"]
        self.session = ort.InferenceSession(str(path), options, providers=[p for p in wanted if p in available])
        self.input_name = self.session.get_inputs()[0].name
        shape = self.session.get_inputs()[0].shape
        self.input_size = shape[-1] if isinstance(shape[-1], int) else input_size
        self.labels = labels
        self.provider = self.session.get_providers()[0]
        self.name = path.stem

    def warmup(self, runs=3):
        frame = np.zeros((480, 640, 3), np.uint8)
        for _ in range(runs):
            self.detect(frame)

    def detect(self, bgr, min_score=0.05):
        """Return (detections sorted by score, inference milliseconds)."""
        img, scale, left, top = letterbox(bgr, self.input_size)
        x = np.ascontiguousarray(img[:, :, ::-1].transpose(2, 0, 1)[None], dtype=np.float32) / 255.0
        start = time.perf_counter()
        out = self.session.run(None, {self.input_name: x})[0][0]
        ms = (time.perf_counter() - start) * 1000
        h, w = bgr.shape[:2]
        dets = []
        for x1, y1, x2, y2, score, cls in out:
            if score < min_score:
                continue
            label = self.labels[int(cls)] if 0 <= int(cls) < len(self.labels) else str(int(cls))
            box = (
                float(np.clip((x1 - left) / scale, 0, w)), float(np.clip((y1 - top) / scale, 0, h)),
                float(np.clip((x2 - left) / scale, 0, w)), float(np.clip((y2 - top) / scale, 0, h)),
            )
            dets.append(Detection(label, canonical(label), float(score), box))
        dets.sort(key=lambda d: d.score, reverse=True)
        return dets, ms


def top_command(dets):
    """Best actionable hand; a resting second hand must not mask a command."""
    for d in dets:
        if d.gesture in ACTIONABLE:
            return d
    return Detection(dets[0].label, NONE, dets[0].score, dets[0].box) if dets else None
