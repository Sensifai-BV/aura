"""Detector tests: letterbox geometry always; model parity when weights exist."""

import os
from pathlib import Path

import numpy as np
import pytest

from aura_gesture.detector import Detection, letterbox, top_command

MODEL = Path(os.environ.get("AURA_GESTURE_MODEL", Path(__file__).resolve().parents[1] / "model" / "gesture" / "YOLOv10n_gestures.onnx"))


def test_letterbox_keeps_aspect_and_centres():
    img = np.zeros((720, 1280, 3), np.uint8)
    out, scale, left, top = letterbox(img, 640)
    assert out.shape == (640, 640, 3)
    assert scale == pytest.approx(0.5)
    assert (left, top) == (0, 140)


def test_top_command_prefers_actionable_hand():
    rest = Detection("no_gesture", "none", 0.95, (0, 0, 1, 1))
    cmd = Detection("like", "thumb_up", 0.85, (0, 0, 1, 1))
    assert top_command([rest, cmd]) is cmd
    assert top_command([rest]).gesture == "none"
    assert top_command([]) is None


def test_bundled_model_resolves_from_any_directory(tmp_path, monkeypatch):
    from aura_gesture.config import resolve_model_path

    monkeypatch.chdir(tmp_path)
    assert resolve_model_path("model/gesture/YOLOv10n_gestures.onnx").is_file()


@pytest.mark.skipif(not MODEL.is_file(), reason="gesture model not present")
def test_model_runs_and_is_quiet_on_blank_frames():
    from aura_gesture.detector import OnnxGestureDetector

    det = OnnxGestureDetector(MODEL, num_threads=2)
    dets, ms = det.detect(np.full((480, 640, 3), 127, np.uint8), min_score=0.25)
    assert dets == [] and ms > 0
