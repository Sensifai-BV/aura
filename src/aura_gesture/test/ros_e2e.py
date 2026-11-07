"""End-to-end ROS2 validation and A/B test of the installed gesture module.

Run in a sourced workspace with the ML Python environment. Ground truth comes
from the guided camera stream (``tools/mac_camera_stream.py --guided``), whose
prompts the camera node republishes on ``/aura/camera/annotation``.

live    camera + one gesture node per model on the *same* frames (gesture_ab
        launch), while ``ros2 bag record`` keeps images and annotations.
replay  for each model, start only the gesture node and play the bag at 1x,
        so every arm sees identical input at the real frame rate.

    python test/ros_e2e.py live --source http://host.orb.internal:18090/stream.mjpg \
        --models model/gesture/YOLOv10n_gestures.onnx model/gesture/YOLOv10x_gestures.onnx \
        --bag runs/live1 --report runs/live1.json
    python test/ros_e2e.py replay --bag runs/live1 --models ... --report runs/replay1.json
"""

import argparse
import json
import os
import signal
import subprocess
import time
from pathlib import Path

import numpy as np
import rclpy
from std_msgs.msg import String

from aura_interfaces.msg import GestureIntent

GT = {"like": "thumb_up", "dislike": "thumb_down", "fist": "fist", "palm": "palm", "point": "point",
      "no_gesture": "none"}
RAW = {"like": "thumb_up", "dislike": "thumb_down", "fist": "fist", "palm": "palm", "stop": "palm",
       "point": "point", "one": "point"}


def stamp_s(header):
    return header.stamp.sec + header.stamp.nanosec * 1e-9


def start(command, log_path):
    log = open(log_path, "w")
    return subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True), log


def stop(proc, timeout=20):
    if proc.poll() is None:
        os.killpg(proc.pid, signal.SIGINT)
        try:
            return proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            return -9
    return proc.returncode


def assert_no_publishers(node, topics):
    """A stray gesture node from an earlier run would silently mix into the results."""
    time.sleep(1.0)  # let discovery settle
    rclpy.spin_once(node, timeout_sec=0.2)
    busy = {t: node.count_publishers(t) for t in topics if node.count_publishers(t)}
    assert not busy, f"other publishers already active on {busy}; stop stray nodes first"


def wait_for(node, predicate, timeout, procs=()):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rclpy.spin_once(node, timeout_sec=0.05)
        for p, log in procs:
            if p.poll() is not None:
                raise RuntimeError(f"process exited early ({p.returncode}); see {log.name}")
        if predicate():
            return True
    return False


class Collector:
    def __init__(self, node, arms):
        self.annotations = []
        self.hyp = {arm: [] for arm in arms}
        self.intents = {arm: [] for arm in arms}
        self.node = node
        self.subs = [node.create_subscription(String, "/aura/camera/annotation", self._ann, 1000)]
        for arm, prefix in arms.items():
            self.subs.append(node.create_subscription(
                GestureIntent, f"{prefix}/hypothesis", lambda m, a=arm: self.hyp[a].append(self._row(m)), 1000))
            self.subs.append(node.create_subscription(
                GestureIntent, f"{prefix}/intent", lambda m, a=arm: self.intents[a].append(self._row(m)), 100))

    def close(self):
        for sub in self.subs:
            self.node.destroy_subscription(sub)
        self.subs = []

    def _ann(self, msg):
        self.annotations.append(json.loads(msg.data))

    @staticmethod
    def _row(m):
        return dict(stamp=stamp_s(m.header), gesture=m.gesture, intent=m.intent, raw=m.raw_label, state=m.state,
                    conf=m.confidence, frame_conf=m.frame_confidence, votes=m.votes, inference_ms=m.inference_ms,
                    latency_ms=m.latency_ms, model=m.model_id, recv=time.time())


def segments(annotations):
    """Contiguous (label, round) spans covering ready+hold of each prompt."""
    segs = []
    for a in sorted(annotations, key=lambda a: a["stamp"]):
        key = (a["label"], a["round"], a["phase"] == "lead_in")
        if segs and segs[-1]["key"] == key:
            segs[-1]["end"] = a["stamp"]
            if a["phase"] == "hold" and segs[-1]["hold_start"] is None:
                segs[-1]["hold_start"] = a["stamp"]
        else:
            segs.append(dict(key=key, label=a["label"], round=a["round"], distance=a.get("distance", ""),
                             start=a["stamp"], end=a["stamp"],
                             hold_start=a["stamp"] if a["phase"] == "hold" else None))
    return segs


def score_arm(annotations, hyp, intents, grace_s=0.5, live=True):
    gt_by_stamp = {round(a["stamp"], 6): a for a in annotations}
    segs = segments(annotations)
    # frame level, hold phases only
    rows = []
    for h in hyp:
        a = gt_by_stamp.get(round(h["stamp"], 6))
        if not a or a["phase"] != "hold":
            continue
        pred = RAW.get(h["raw"], "none")
        rows.append((GT[a["label"]], pred, h["frame_conf"] if pred != "none" else 0.0, a.get("distance", "")))
    per_class = {}
    for g in sorted(set(r[0] for r in rows)):
        rs = [r for r in rows if r[0] == g]
        ok = [r for r in rs if r[1] == g]
        per_class[g] = dict(frames=len(rs), frame_accuracy=len(ok) / len(rs),
                            mean_confidence_correct=float(np.mean([r[2] for r in ok])) if ok else 0.0)
    # segment level
    seg_rows, false_acts, neg_seconds = [], [], 0.0
    for s in segs:
        hits = [i for i in intents if s["start"] <= i["stamp"] <= s["end"] + grace_s]
        gt = GT[s["label"]]
        if gt == "none":
            neg_seconds += s["end"] - s["start"]
            false_acts += hits
            continue
        right = [i for i in hits if i["gesture"] == gt]
        wrong = [i for i in hits if i["gesture"] != gt]
        seg_rows.append(dict(label=gt, round=s["round"], distance=s["distance"], success=bool(right), wrong=len(wrong),
                             time_to_accept_s=(right[0]["stamp"] - s["start"]) if right else None,
                             from_hold_s=(right[0]["stamp"] - s["hold_start"]) if right and s["hold_start"] else None,
                             confidence=right[0]["conf"] if right else None))
    per_distance = {}
    for d in sorted(set(r[3] for r in rows) | set(r["distance"] for r in seg_rows)):
        fr = [r for r in rows if r[3] == d]
        sr = [r for r in seg_rows if r["distance"] == d]
        negs = [s for s in segs if s["distance"] == d and GT[s["label"]] == "none" and s["key"][2] is False]
        fa = [i for s in negs for i in intents if s["start"] <= i["stamp"] <= s["end"] + grace_s]
        per_distance[d or "unspecified"] = dict(
            frame_accuracy=float(np.mean([r[0] == r[1] for r in fr])) if fr else None,
            mean_confidence_correct=float(np.mean([r[2] for r in fr if r[0] == r[1] and r[0] != "none"] or [0])),
            command_success=float(np.mean([r["success"] for r in sr])) if sr else None,
            per_gesture_success={g: float(np.mean([r["success"] for r in sr if r["label"] == g]))
                                 for g in sorted(set(r["label"] for r in sr))},
            false_activations=len(fa))
    lat = [h["latency_ms"] for h in hyp] if live else []  # replayed stamps are old
    inf = [h["inference_ms"] for h in hyp]
    tta = [r["time_to_accept_s"] for r in seg_rows if r["time_to_accept_s"] is not None]
    stop_rows = [r for r in seg_rows if r["label"] == "fist"]
    stamps = sorted(h["stamp"] for h in hyp)
    return dict(
        model=hyp[0]["model"] if hyp else "",
        processed_frames=len(hyp),
        processed_fps=(len(stamps) - 1) / (stamps[-1] - stamps[0]) if len(stamps) > 1 else 0.0,
        frame_accuracy=float(np.mean([r[0] == r[1] for r in rows])) if rows else 0.0,
        per_class=per_class,
        per_distance=per_distance,
        command_segments=len(seg_rows),
        command_success=float(np.mean([r["success"] for r in seg_rows])) if seg_rows else 0.0,
        per_gesture_success={g: float(np.mean([r["success"] for r in seg_rows if r["label"] == g]))
                             for g in sorted(set(r["label"] for r in seg_rows))},
        wrong_intents=int(sum(r["wrong"] for r in seg_rows)),
        false_activations=len(false_acts),
        false_activations_per_min=len(false_acts) / max(neg_seconds / 60, 1e-6),
        negative_seconds=neg_seconds,
        median_time_to_accept_s=float(np.median(tta)) if tta else None,
        stop_success=float(np.mean([r["success"] for r in stop_rows])) if stop_rows else None,
        median_stop_time_s=float(np.median([r["time_to_accept_s"] for r in stop_rows if r["success"]]))
        if any(r["success"] for r in stop_rows) else None,
        latency_ms_p50=float(np.median(lat)) if lat else None,
        latency_ms_p95=float(np.percentile(lat, 95)) if lat else None,
        inference_ms_p50=float(np.median(inf)) if inf else None,
        segments=seg_rows,
        intents=[dict(stamp=i["stamp"], gesture=i["gesture"], intent=i["intent"], conf=i["conf"]) for i in intents],
    )


def run_live(args, node, out):
    arms = {"a": "/aura/gesture_a", "b": "/aura/gesture_b"} if len(args.models) == 2 else {"a": "/aura/gesture"}
    assert_no_publishers(node, [f"{p}/hypothesis" for p in arms.values()] + ["/aura/camera/image_raw/compressed"])
    col = Collector(node, arms)
    if len(args.models) == 2:
        launch = ["ros2", "launch", "aura_gesture", "gesture_ab.launch.py", f"source:={args.source}",
                  f"model_a:={args.models[0]}", f"model_b:={args.models[1]}"]
    else:
        launch = ["ros2", "launch", "aura_gesture", "gesture.launch.py", f"source:={args.source}",
                  f"model_path:={args.models[0]}"]
    procs = [start(launch, out.with_suffix(".launch.log"))]
    if args.bag:
        procs.append(start(["ros2", "bag", "record", "-o", args.bag, "--topics", "/aura/camera/image_raw/compressed",
                            "/aura/camera/annotation"], out.with_suffix(".bag.log")))
    try:
        assert wait_for(node, lambda: all(col.hyp[a] for a in arms), args.first_frame_timeout, procs), (
            f"no gesture hypotheses within {args.first_frame_timeout}s - is the camera source streaming?")
        # guided stream ends -> annotations stop; finish once quiet for 3 s
        last = [time.monotonic(), 0]

        def finished():
            if len(col.annotations) != last[1]:
                last[:] = [time.monotonic(), len(col.annotations)]
            return col.annotations and time.monotonic() - last[0] > 3

        assert wait_for(node, finished, args.timeout, procs), "guided stream did not finish in time"
    finally:
        codes = [stop(p) for p, _ in reversed(procs)]
        for _, log in procs:
            log.close()
    return col, arms, codes


def run_replay(args, node, out):
    results = {}
    codes = []
    annotations = None
    for i, model in enumerate(args.models):
        arm = chr(ord("a") + i)
        assert_no_publishers(node, ["/aura/gesture/hypothesis", "/aura/camera/image_raw/compressed"])
        col = Collector(node, {arm: "/aura/gesture"})
        g, glog = start(["ros2", "launch", "aura_gesture", "gesture.launch.py", "camera:=false",
                         f"model_path:={model}", "max_frame_age_s:=0"], out.with_suffix(f".{arm}.log"))
        try:
            assert wait_for(node, lambda: "Gesture ready" in Path(glog.name).read_text(), 120, [(g, glog)]), \
                "gesture node did not start"
            # keep spinning during playback, or DDS history (depth) silently drops results
            play, plog = start(["ros2", "bag", "play", "-i", args.bag], out.with_suffix(f".{arm}.play.log"))
            try:
                wait_for(node, lambda: play.poll() is not None, args.timeout)
            finally:
                plog.close()
            assert play.returncode == 0, Path(plog.name).read_text()[-500:]
            wait_for(node, lambda: False, 3)  # drain in-flight results
        finally:
            col.close()  # later arms must not leak into this arm's results
            codes.append(stop(g))
            glog.close()
        annotations = annotations or col.annotations
        results[arm] = (col.hyp[arm], col.intents[arm])
    return annotations, results, codes


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["live", "replay"])
    ap.add_argument("--models", nargs="+", required=True)
    ap.add_argument("--source", default="http://host.orb.internal:18090/stream.mjpg")
    ap.add_argument("--bag", default="")
    ap.add_argument("--report", required=True)
    ap.add_argument("--timeout", type=float, default=600)
    ap.add_argument("--first-frame-timeout", type=float, default=90)
    args = ap.parse_args()
    if len(args.models) > 2 and args.mode == "live":
        ap.error("live mode compares at most two models")
    out = Path(args.report)
    out.parent.mkdir(parents=True, exist_ok=True)
    rclpy.init()
    node = rclpy.create_node("aura_gesture_validation")
    try:
        if args.mode == "live":
            col, arms, codes = run_live(args, node, out)
            annotations = col.annotations
            arms_data = {a: (col.hyp[a], col.intents[a]) for a in arms}
        else:
            annotations, arms_data, codes = run_replay(args, node, out)
        assert annotations, "no ground-truth annotations received (use the guided stream)"
        report = dict(mode=args.mode, source=args.source, bag=args.bag, shutdown_exit_codes=codes,
                      annotation_frames=len(annotations),
                      arms={a: score_arm(annotations, h, i, live=args.mode == "live")
                            for a, (h, i) in arms_data.items()})
        for a, (h, i) in arms_data.items():
            Path(out.with_suffix(f".{a}.hyp.jsonl")).write_text("\n".join(json.dumps(r) for r in h))
        Path(out.with_suffix(".annotations.jsonl")).write_text("\n".join(json.dumps(r) for r in annotations))
        out.write_text(json.dumps(report, indent=2) + "\n")
        for a, r in report["arms"].items():
            brief = {k: v for k, v in r.items() if k not in ("segments", "intents", "per_class", "per_distance")}
            brief["per_distance"] = {d: {k: v[k] for k in ("frame_accuracy", "command_success", "false_activations")}
                                     for d, v in r["per_distance"].items()}
            print(a, json.dumps(brief, indent=1))
        assert all(c == 0 for c in codes), f"unclean shutdown {codes}"
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
