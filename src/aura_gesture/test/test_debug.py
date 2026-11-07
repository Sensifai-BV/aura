import json

from aura_gesture import debug


def rows(seq, t0=100.0, dt=0.033):
    return [dict(stamp=t0 + i * dt, frame_gesture=g, frame_conf=c, raw=g, model="m", latency_ms=30.0,
                 inference_ms=20.0) for i, (g, c) in enumerate(seq)]


def test_simulate_matches_gate_parameters():
    r = rows([("thumb_up", 0.85)] * 20 + [("none", 0.0)] * 5 + [("thumb_down", 0.65)] * 20)
    assert [a["gesture"] for a in debug.simulate(r, 0.8, 8, 5, 0.5, 3)] == ["thumb_up"]
    assert [a["gesture"] for a in debug.simulate(r, 0.6, 8, 5, 0.5, 3)] == ["thumb_up", "thumb_down"]


def test_summary_reports_confidence_accuracy_and_timing():
    r = rows([("thumb_up", 0.9)] * 10 + [("fist", 0.7)] * 10)
    for x in r:
        x["label"] = "like"
    s = debug.summary(r, [dict(intent="confirm")])
    assert s["frame_confidence"]["thumb_up"]["n"] == 10
    assert s["frame_accuracy"] == {"thumb_up": 0.5}
    assert s["intents"] == {"confirm": 1}
    assert 29 < s["fps"] < 31


def test_sweep_cli_scores_against_annotations(tmp_path, capsys):
    r = rows([("none", 0.0)] * 30 + [("thumb_up", 0.9)] * 30)
    ann = [dict(stamp=x["stamp"], label="no_gesture" if i < 30 else "like", phase="hold", round="0")
           for i, x in enumerate(r)]
    (tmp_path / "hypotheses.jsonl").write_text("\n".join(json.dumps(x) for x in r))
    (tmp_path / "annotations.jsonl").write_text("\n".join(json.dumps(a) for a in ann))
    out = tmp_path / "sweep.json"
    debug.main(["sweep", str(tmp_path / "hypotheses.jsonl"), "--thresholds", "0.8", "0.95", "--json", str(out)])
    res = json.loads(out.read_text())
    assert res[0]["command_success"] == 1.0 and res[0]["false_activations"] == 0
    assert res[-1]["command_success"] == 0.0
