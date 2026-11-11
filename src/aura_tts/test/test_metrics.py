import csv
import filecmp
from pathlib import Path

from aura_tts.metrics import MetricsLog, percentile, stats


def test_rows_and_summary_files(tmp_path):
    m = MetricsLog(tmp_path / "metrics", "tts", ["text", "first_audio_ms"])
    m.row(text="Stop, executing.", first_audio_ms=0.512345)
    m.row(text="Did you say next step?", first_audio_ms=640.0)
    with open(m.path) as f:  # rows are on disk before close (a killed node keeps its data)
        rows = list(csv.DictReader(f))
    assert [r["text"] for r in rows] == ["Stop, executing.", "Did you say next step?"]
    assert rows[0]["first_audio_ms"] == "0.5123" and rows[0]["time"].endswith("+00:00")
    assert m.column("first_audio_ms", lambda r: r["first_audio_ms"] > 1) == [640.0]
    path = m.close(stats("first audio", m.column("first_audio_ms")))
    with open(path) as f:
        summary = {r["metric"]: r for r in csv.DictReader(f)}
    assert summary["first audio count"]["value"] == "2"
    assert summary["first audio p50"]["value"] == "320.3" and summary["first audio p50"]["unit"] == "ms"


def test_percentile_and_empty_stats():
    assert percentile([10, 20, 30, 40], 50) == 25
    assert percentile([5], 95) == 5 and percentile([], 50) is None
    assert stats("latency", []) == [("latency count", 0, "", "")]


def test_every_module_ships_the_same_helper():
    src = Path(__file__).resolve().parents[2]
    copies = [src / f"aura_{m}" / f"aura_{m}" / "metrics.py" for m in ("stt", "intent", "gesture", "tts")]
    if not all(p.exists() for p in copies):  # installed copy of this package only
        return
    assert all(filecmp.cmp(copies[0], p, shallow=False) for p in copies[1:])
