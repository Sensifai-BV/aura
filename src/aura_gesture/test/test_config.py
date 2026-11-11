import pytest

from aura_gesture.config import CameraConfig, GestureConfig


def test_defaults_are_valid():
    cfg = GestureConfig.from_mapping(GestureConfig.defaults())
    assert cfg.intent_map["thumb_up"] == "confirm"
    assert cfg.threshold_map["fist"] == 0.8 and cfg.votes_map["fist"] == 5
    assert cfg.provider_list == []
    CameraConfig.from_mapping(CameraConfig.defaults())


@pytest.mark.parametrize("change", [
    {"min_votes": 9},
    {"gestures": ["thumb_up", "wave"], "intents": ["confirm", "x"], "thresholds": [0.8, 0.8], "votes": [5, 5]},
    {"thresholds": [0.8, 0.8, 0.1, 0.8, 0.8]},  # below min_detection_score
    {"votes": [5, 5, 3, 5]},
    {"intent_topic": "/aura/gesture/hypothesis"},
    {"accept_threshold": 1.5},
])
def test_rejects_bad_values(change):
    with pytest.raises(ValueError):
        GestureConfig.from_mapping({**GestureConfig.defaults(), **change})


def test_camera_rejects_bad_quality():
    with pytest.raises(ValueError):
        CameraConfig.from_mapping({**CameraConfig.defaults(), "jpeg_quality": 0})


def test_providers_string():
    cfg = GestureConfig.from_mapping({**GestureConfig.defaults(), "providers": "TensorrtExecutionProvider, CPUExecutionProvider"})
    assert cfg.provider_list == ["TensorrtExecutionProvider", "CPUExecutionProvider"]
