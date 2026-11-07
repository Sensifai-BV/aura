"""Debug and data-collection CLI for aura_gesture.

    ros2 run aura_gesture debug monitor                     # live terminal view
    ros2 run aura_gesture debug record -o runs/t1 --label like --duration 20 --images
    ros2 run aura_gesture debug gallery runs/t1             # rebuild runs/t1/gallery.html
    ros2 run aura_gesture debug summarize runs/t1/hypotheses.jsonl [--plot runs/t1/conf.png]
    ros2 run aura_gesture debug sweep runs/t1/hypotheses.jsonl --thresholds 0.6 0.7 0.8 0.9 --votes 3 5
    ros2 run aura_gesture debug snapshot -o runs/snaps --count 5

``record`` keeps every per-frame hypothesis (raw label, frame confidence,
smoothed confidence, votes, state, latency), every accepted intent, the node
diagnostics and, when the camera provides them, ground-truth annotations.
``sweep`` replays recorded per-frame scores through the same TemporalGate the
node uses, so acceptance parameters can be tuned without re-recording.
Use ``--prefix /aura/gesture_a`` for the A/B launch arms.
"""

import argparse
import json
import sys
import time
from collections import Counter, defaultdict, deque
from pathlib import Path

import numpy as np

from .decision import ACCEPTED, TemporalGate
from .labels import ACTIONABLE, HAGRID_TO_CANONICAL, NONE

STATES = {0: "idle", 1: "candidate", 2: "ACCEPTED"}
GT = {"like": "thumb_up", "dislike": "thumb_down", "fist": "fist", "palm": "palm", "point": "point",
      "no_gesture": NONE}


def stamp_s(header):
    return header.stamp.sec + header.stamp.nanosec * 1e-9


def hyp_row(m):
    return dict(stamp=stamp_s(m.header), gesture=m.gesture, intent=m.intent, raw=m.raw_label,
                frame_gesture=HAGRID_TO_CANONICAL.get(m.raw_label, NONE), state=m.state,
                conf=round(m.confidence, 4), frame_conf=round(m.frame_confidence, 4), threshold=m.threshold,
                votes=m.votes, window=m.window, inference_ms=round(m.inference_ms, 2),
                latency_ms=round(m.latency_ms, 2), model=m.model_id,
                bbox=[round(m.bbox.center.position.x, 1), round(m.bbox.center.position.y, 1),
                      round(m.bbox.size_x, 1), round(m.bbox.size_y, 1)])


class Session:
    """Subscriptions shared by monitor/record."""

    def __init__(self, node, prefix, on_hyp, on_intent, on_diag=None, on_annotation=None):
        from diagnostic_msgs.msg import DiagnosticArray
        from std_msgs.msg import String

        from aura_interfaces.msg import GestureIntent

        node.create_subscription(GestureIntent, f"{prefix}/hypothesis", on_hyp, 200)
        node.create_subscription(GestureIntent, f"{prefix}/intent", on_intent, 50)
        if on_diag:
            node.create_subscription(DiagnosticArray, "/diagnostics", on_diag, 20)
        if on_annotation:
            node.create_subscription(String, "/aura/camera/annotation", on_annotation, 200)


def spin_until(node, deadline):
    import rclpy
    from rclpy.executors import ExternalShutdownException

    try:
        while rclpy.ok() and (deadline is None or time.monotonic() < deadline):
            rclpy.spin_once(node, timeout_sec=0.1)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass


def annotate(img, dets, lines=(), highlight=False):
    """Draw detection boxes (vision_msgs Detection2D) and text lines on a BGR image."""
    import cv2

    for d in dets:
        h = d.results[0].hypothesis if d.results else None
        cx, cy, w, hh = d.bbox.center.position.x, d.bbox.center.position.y, d.bbox.size_x, d.bbox.size_y
        p1, p2 = (int(cx - w / 2), int(cy - hh / 2)), (int(cx + w / 2), int(cy + hh / 2))
        color = (160, 160, 160) if h and h.class_id == "no_gesture" else (0, 200, 0)
        cv2.rectangle(img, p1, p2, color, 2)
        if h:
            cv2.putText(img, f"{h.class_id} {h.score:.2f}", (p1[0], max(20, p1[1] - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
    for i, t in enumerate(lines):
        y = 32 + 32 * i
        cv2.putText(img, t, (12, y), cv2.FONT_HERSHEY_SIMPLEX, 0.85, (0, 0, 0), 5)
        cv2.putText(img, t, (12, y), cv2.FONT_HERSHEY_SIMPLEX, 0.85, (0, 0, 255) if highlight else (255, 255, 255), 2)
    return img


# ----------------------------------------------------------------------------- monitor
def cmd_monitor(args):
    import rclpy
    from rclpy.executors import ExternalShutdownException

    rclpy.init()
    node = rclpy.create_node("aura_gesture_debug_monitor")
    recent = deque(maxlen=90)
    intents = deque(maxlen=6)
    diag = {}
    seen = defaultdict(list)

    def on_hyp(m):
        r = hyp_row(m)
        recent.append((time.monotonic(), r))
        if r["frame_gesture"] != NONE:
            seen[r["frame_gesture"]].append(r["frame_conf"])

    def on_intent(m):
        intents.appendleft(f"{time.strftime('%H:%M:%S')} {m.intent:<8} ({m.gesture}) conf {m.confidence:.2f} "
                           f"votes {m.votes}/{m.window} {m.latency_ms:.0f}ms")

    def on_diag(msg):
        for s in msg.status:
            if s.name.startswith("aura_gesture"):
                diag.update({kv.key: kv.value for kv in s.values}, level=s.level, message=s.message)

    Session(node, args.prefix, on_hyp, on_intent, on_diag)
    last_draw = 0.0
    try:
        while rclpy.ok():
            rclpy.spin_once(node, timeout_sec=0.05)
            now = time.monotonic()
            if now - last_draw < 0.2:
                continue
            last_draw = now
            out = ["\x1b[2J\x1b[H", f"aura_gesture monitor  [{args.prefix}]   Ctrl-C to quit\n"]
            if not recent:
                out.append("waiting for hypotheses...")
            else:
                r = recent[-1][1]
                fps = len([t for t, _ in recent if now - t < 1.0])
                bar = "#" * int(r["frame_conf"] * 30)
                out += [f"model     {r['model']}",
                        f"frame     {r['raw'] or '-':<14} -> {r['frame_gesture']:<10} {r['frame_conf']:.2f} |{bar:<30}|",
                        f"decision  {r['gesture']:<10} intent {r['intent']:<8} state {STATES.get(r['state'], r['state']):<9} "
                        f"conf {r['conf']:.2f}  votes {r['votes']}/{r['window']} (thr {r['threshold']:.2f})",
                        f"timing    {fps} fps   inference {r['inference_ms']:.1f} ms   capture->publish {r['latency_ms']:.1f} ms"]
                if diag:
                    out.append("node      " + "  ".join(f"{k}={diag[k]}" for k in
                                                        ("received", "processed", "dropped", "stale", "accepted",
                                                         "latency_p95_ms", "provider") if k in diag))
                out.append("\nconfidence seen this session (frame level)")
                for g in ACTIONABLE:
                    v = seen.get(g)
                    if v:
                        out.append(f"  {g:<11} n={len(v):5d}  mean {np.mean(v):.2f}  p5 {np.percentile(v, 5):.2f}  "
                                   f"p50 {np.median(v):.2f}  p95 {np.percentile(v, 95):.2f}")
            out.append("\nlast intents")
            out += [f"  {i}" for i in intents] or ["  (none yet)"]
            sys.stdout.write("\n".join(out) + "\n")
            sys.stdout.flush()
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


# ----------------------------------------------------------------------------- record
def cmd_record(args):
    import rclpy

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    files = {k: open(out / f"{k}.jsonl", "a") for k in ("hypotheses", "intents", "diagnostics", "annotations")}
    counts = Counter()
    meta = dict(prefix=args.prefix, label=args.label, note=args.note, started=time.time())

    def write(kind, row):
        if args.label:
            row["label"] = args.label
        files[kind].write(json.dumps(row) + "\n")
        counts[kind] += 1

    def on_diag(msg):
        for s in msg.status:
            if s.name.startswith("aura_gesture"):
                write("diagnostics", dict(stamp=time.time(), level=int.from_bytes(s.level, "little") if isinstance(s.level, bytes) else int(s.level),
                                          message=s.message, **{kv.key: kv.value for kv in s.values}))

    rclpy.init()
    node = rclpy.create_node("aura_gesture_debug_record")
    truth_by_stamp = {}

    def on_annotation(m):
        a = json.loads(m.data)
        truth_by_stamp[round(a["stamp"], 6)] = a
        write("annotations", a)

    saver = ImageSaver(node, out, args, truth_by_stamp, counts) if args.images else None

    def on_hyp(m):
        row = hyp_row(m)
        write("hypotheses", row)
        if saver:
            saver.hypothesis(m.header.stamp, row)

    Session(node, args.prefix, on_hyp, lambda m: write("intents", hyp_row(m)), on_diag, on_annotation)
    print(f"recording {args.prefix} -> {out} (label={args.label or '-'}); Ctrl-C to stop", flush=True)
    try:
        spin_until(node, time.monotonic() + args.duration if args.duration else None)
    finally:
        for f in files.values():
            f.close()
        meta.update(stopped=time.time(), counts=dict(counts))
        (out / "session.json").write_text(json.dumps(meta, indent=2) + "\n")
        node.destroy_node()
        rclpy.try_shutdown()
    if saver:
        saver.close()
        build_gallery(out)
        print(f"images -> {out / 'images'}; review {out / 'gallery.html'}")
    print(json.dumps(dict(counts)))
    if counts["hypotheses"]:
        print_summary(load(out / "hypotheses.jsonl"), load(out / "intents.jsonl"))


def fmt_latency(ms):
    # bag replays keep the original capture stamps, so latency is meaningless there
    return "replay" if ms > 60000 else f"{ms:.0f} ms"


class ImageSaver:
    """Pairs camera frames, detections and hypotheses by capture stamp and saves
    the frames worth checking: every Nth, accepted intents, gesture changes and
    disagreements with the ground truth (``--label`` or guided annotations)."""

    def __init__(self, node, out, args, truth_by_stamp, counts):
        from rclpy.qos import qos_profile_sensor_data
        from sensor_msgs.msg import CompressedImage
        from vision_msgs.msg import Detection2DArray

        self.dir = out / "images"
        self.dir.mkdir(exist_ok=True)
        self.index = open(out / "images.jsonl", "a")
        self.args, self.truth_by_stamp, self.counts = args, truth_by_stamp, counts
        self.images, self.dets, self.pending = {}, {}, []
        self.frame = 0
        self.last_gesture = None
        node.create_subscription(CompressedImage, args.image_topic, self._image, qos_profile_sensor_data)
        node.create_subscription(Detection2DArray, f"{args.prefix}/detections", self._dets, 50)

    @staticmethod
    def key(stamp):
        return (stamp.sec, stamp.nanosec)

    def _trim(self, d, n=120):
        while len(d) > n:
            d.pop(next(iter(d)))

    def _image(self, m):
        self.images[self.key(m.header.stamp)] = m.data
        self._trim(self.images)
        self._flush()

    def _dets(self, arr):
        self.dets[self.key(arr.header.stamp)] = arr.detections
        self._trim(self.dets)
        self._flush()

    def hypothesis(self, stamp, row):
        self.frame += 1
        a = self.truth_by_stamp.get(round(row["stamp"], 6))
        phase = (a or {}).get("phase", "")
        if row.get("label"):
            truth, phase = GT.get(row["label"], row["label"]), "hold"
        else:
            truth = GT.get(a["label"], NONE) if a and phase != "lead_in" else None
        reasons = []
        if row["state"] == ACCEPTED:
            reasons.append("accepted")
        # during the "ready" countdown the operator is still forming the gesture
        if truth is not None and phase == "hold" and row["frame_gesture"] != truth:
            reasons.append("mismatch")
        if row["frame_gesture"] != self.last_gesture:
            reasons.append("change")
        if self.args.image_every and self.frame % self.args.image_every == 0:
            reasons.append("sample")
        self.last_gesture = row["frame_gesture"]
        if reasons and self.counts["images"] < self.args.max_images:
            self.pending.append((self.key(stamp), row, f"{truth} ({phase})" if truth and phase else truth,
                                 reasons, time.monotonic()))
            self._flush()

    def _flush(self):
        import cv2

        keep = []
        for key, row, truth, reasons, t in self.pending:
            if key not in self.images or key not in self.dets:
                if time.monotonic() - t < 2.0:
                    keep.append((key, row, truth, reasons, t))
                continue
            raw = cv2.imdecode(np.frombuffer(self.images[key], np.uint8), cv2.IMREAD_COLOR)
            if raw is None:
                continue
            name = f"{key[0]}_{key[1]:09d}"
            lines = [f"frame: {row['raw'] or '-'} -> {row['frame_gesture']} {row['frame_conf']:.2f}",
                     f"decision: {row['gesture']}/{row['intent']} {STATES.get(row['state'], row['state'])} "
                     f"conf {row['conf']:.2f} votes {row['votes']}/{row['window']}",
                     f"truth: {truth or '?'}   {' '.join(reasons)}   {fmt_latency(row['latency_ms'])}"]
            img = annotate(raw.copy(), self.dets[key], lines, highlight="mismatch" in reasons)
            cv2.imwrite(str(self.dir / f"{name}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 85])
            if self.args.save_raw:
                (self.dir / f"{name}_raw.jpg").write_bytes(bytes(self.images[key]))
            self.index.write(json.dumps(dict(row, file=f"images/{name}.jpg", truth=truth, reasons=reasons)) + "\n")
            self.counts["images"] += 1
        self.pending = keep

    def close(self):
        self._flush()
        self.index.close()


def build_gallery(folder):
    """Static HTML contact sheet of saved debug frames, grouped by truth/prediction."""
    import html

    folder = Path(folder)
    rows = load(folder / "images.jsonl")
    if not rows:
        return None
    groups = defaultdict(list)
    for r in rows:
        groups[r.get("truth") or r["frame_gesture"]].append(r)
    mism = sum("mismatch" in r["reasons"] for r in rows)
    parts = [f"<!doctype html><meta charset=utf-8><title>aura_gesture debug</title><style>"
             "body{font:14px system-ui;margin:16px;background:#fafaf8;color:#111}"
             ".g{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:10px}"
             ".c{background:#fff;border:2px solid #ddd;border-radius:6px;padding:4px}"
             ".c.bad{border-color:#d33}.c.acc{border-color:#2a78d6}img{width:100%;display:block}"
             "small{color:#555}</style>",
             f"<h1>aura_gesture debug frames</h1><p>{len(rows)} frames, {mism} disagree with ground truth "
             "(red), accepted intents in blue.</p>"]
    for g in sorted(groups):
        rs = groups[g]
        bad = sum("mismatch" in r["reasons"] for r in rs)
        parts.append(f"<h2>{html.escape(g)} - {len(rs)} frames, {bad} mismatches</h2><div class=g>")
        for r in sorted(rs, key=lambda r: ("mismatch" not in r["reasons"], r["stamp"])):
            cls = "bad" if "mismatch" in r["reasons"] else ("acc" if "accepted" in r["reasons"] else "")
            parts.append(f"<div class='c {cls}'><a href='{r['file']}'><img loading=lazy src='{r['file']}'></a>"
                         f"<small>{html.escape(r['raw'] or '-')} {r['frame_conf']:.2f} &rarr; "
                         f"{html.escape(r['gesture'])}/{html.escape(r['intent'])} conf {r['conf']:.2f} "
                         f"[{' '.join(r['reasons'])}]</small></div>")
        parts.append("</div>")
    path = folder / "gallery.html"
    path.write_text("\n".join(parts))
    return path


def cmd_gallery(args):
    path = build_gallery(args.folder)
    print(path or "no images.jsonl in folder")


# ----------------------------------------------------------------------------- summarize
def load(path):
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def truth_of(row, annotations=None):
    if row.get("label"):
        return GT.get(row["label"], row["label"])
    if annotations is not None:
        a = annotations.get(round(row["stamp"], 6))
        if a and a.get("phase") == "hold":
            return GT.get(a["label"], NONE)
    return None


def summary(rows, intents, annotations=None):
    out = dict(frames=len(rows), models=sorted({r.get("model", "") for r in rows}))
    by = defaultdict(list)
    for r in rows:
        by[r["frame_gesture"]].append(r["frame_conf"])
    out["frame_confidence"] = {
        g: dict(n=len(v), mean=round(float(np.mean(v)), 3), p5=round(float(np.percentile(v, 5)), 3),
                p50=round(float(np.median(v)), 3), p95=round(float(np.percentile(v, 95)), 3))
        for g, v in sorted(by.items()) if g != NONE}
    truth = [(truth_of(r, annotations), r) for r in rows]
    truth = [(t, r) for t, r in truth if t]
    if truth:
        per = defaultdict(lambda: [0, 0])
        for t, r in truth:
            per[t][0] += r["frame_gesture"] == t
            per[t][1] += 1
        out["frame_accuracy"] = {t: round(a / n, 3) for t, (a, n) in sorted(per.items())}
    out["intents"] = dict(Counter(i["intent"] for i in intents))
    for key in ("latency_ms", "inference_ms"):
        v = [r[key] for r in rows if r.get(key) is not None]
        if key == "latency_ms" and v and np.median(v) > 60000:
            out[key] = "n/a (bag replay keeps original capture stamps)"
        elif v:
            out[key] = dict(p50=round(float(np.median(v)), 1), p95=round(float(np.percentile(v, 95)), 1))
    stamps = sorted(r["stamp"] for r in rows)
    if len(stamps) > 1:
        out["fps"] = round((len(stamps) - 1) / (stamps[-1] - stamps[0]), 2)
        gaps = np.diff(stamps)
        out["max_gap_s"] = round(float(gaps.max()), 3)
    return out


def print_summary(rows, intents, annotations=None):
    print(json.dumps(summary(rows, intents, annotations), indent=2))


def annotations_for(path):
    ann = load(Path(path).with_name("annotations.jsonl"))
    return {round(a["stamp"], 6): a for a in ann} if ann else None


def cmd_summarize(args):
    rows = load(args.hypotheses)
    intents = load(Path(args.hypotheses).with_name("intents.jsonl"))
    ann = annotations_for(args.hypotheses)
    print_summary(rows, intents, ann)
    if args.plot:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, axes = plt.subplots(1, 2, figsize=(12, 4))
        bins = np.linspace(0, 1, 26)
        for g in ACTIONABLE:
            v = [r["frame_conf"] for r in rows if r["frame_gesture"] == g]
            if v:
                axes[0].hist(v, bins, alpha=0.6, label=f"{g} (n={len(v)})")
        axes[0].set(title="Frame confidence by detected gesture", xlabel="confidence", ylabel="frames")
        axes[0].legend(fontsize=8)
        t0 = rows[0]["stamp"] if rows else 0
        for g in ACTIONABLE:
            pts = [(r["stamp"] - t0, r["frame_conf"]) for r in rows if r["frame_gesture"] == g]
            if pts:
                axes[1].scatter(*zip(*pts), s=4, label=g)
        for i in intents:
            axes[1].axvline(i["stamp"] - t0, color="k", lw=0.6, alpha=0.5)
        axes[1].set(title="Confidence over time (lines = accepted intents)", xlabel="s", ylim=(0, 1.02))
        fig.tight_layout()
        fig.savefig(args.plot, dpi=120)
        print(f"plot -> {args.plot}")


# ----------------------------------------------------------------------------- sweep
def simulate(rows, threshold, window, votes, cooldown_s, rearm_frames, stop_threshold=None, stop_votes=None):
    gate = TemporalGate(window, min(votes, window), threshold,
                        {"fist": stop_threshold} if stop_threshold is not None else None,
                        {"fist": min(stop_votes, window)} if stop_votes else None, cooldown_s, rearm_frames)
    accepted = []
    for r in rows:
        d = gate.update(r["frame_gesture"], r["frame_conf"], r["stamp"])
        if d.state == ACCEPTED:
            accepted.append(dict(stamp=r["stamp"], gesture=d.gesture))
    return accepted


def cmd_sweep(args):
    rows = sorted(load(args.hypotheses), key=lambda r: r["stamp"])
    ann = annotations_for(args.hypotheses)
    # ground-truth segments (contiguous label runs) if annotations or --label exist
    segs = []
    if ann:
        for a in sorted(ann.values(), key=lambda a: a["stamp"]):
            key = (a["label"], a.get("round"))
            if segs and segs[-1]["key"] == key:
                segs[-1]["end"] = a["stamp"]
            else:
                segs.append(dict(key=key, truth=GT.get(a["label"], NONE), start=a["stamp"], end=a["stamp"]))
    results = []
    for thr in args.thresholds:
        for w in args.windows:
            for k in args.votes:
                if k > w:
                    continue
                acc = simulate(rows, thr, w, k, args.cooldown, args.rearm)
                res = dict(threshold=thr, window=w, votes=k, intents=len(acc))
                if segs:
                    cmd = [s for s in segs if s["truth"] != NONE]
                    hit = lambda s, g: [a for a in acc if s["start"] <= a["stamp"] <= s["end"] + 0.5 and a["gesture"] == g]  # noqa: E731
                    ok = [s for s in cmd if hit(s, s["truth"])]
                    res["command_success"] = round(len(ok) / max(len(cmd), 1), 3)
                    res["wrong"] = sum(len([a for a in acc if s["start"] <= a["stamp"] <= s["end"] and a["gesture"] != s["truth"]])
                                       for s in cmd)
                    res["false_activations"] = sum(len([a for a in acc if s["start"] <= a["stamp"] <= s["end"]])
                                                   for s in segs if s["truth"] == NONE)
                    tta = [hit(s, s["truth"])[0]["stamp"] - s["start"] for s in ok]
                    res["median_time_to_accept_s"] = round(float(np.median(tta)), 2) if tta else None
                results.append(res)
    keys = list(results[0]) if results else []
    print("  ".join(f"{k:>12}" for k in keys))
    for r in results:
        print("  ".join(f"{str(r[k]):>12}" for k in keys))
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2) + "\n")


# ----------------------------------------------------------------------------- snapshot
def cmd_snapshot(args):
    import cv2
    import rclpy
    from rclpy.executors import ExternalShutdownException
    from rclpy.qos import qos_profile_sensor_data
    from sensor_msgs.msg import CompressedImage
    from vision_msgs.msg import Detection2DArray

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    rclpy.init()
    node = rclpy.create_node("aura_gesture_debug_snapshot")
    images, saved = {}, [0]

    def on_image(m):
        images[(m.header.stamp.sec, m.header.stamp.nanosec)] = m
        while len(images) > 60:
            images.pop(next(iter(images)))

    def on_dets(arr):
        key = (arr.header.stamp.sec, arr.header.stamp.nanosec)
        if key not in images or saved[0] >= args.count:
            return
        if args.only_detections and not arr.detections:
            return
        img = annotate(cv2.imdecode(np.frombuffer(images[key].data, np.uint8), cv2.IMREAD_COLOR), arr.detections)
        path = out / f"snap_{key[0]}_{key[1]:09d}.jpg"
        cv2.imwrite(str(path), img)
        saved[0] += 1
        print(path, flush=True)

    node.create_subscription(CompressedImage, args.image_topic, on_image, qos_profile_sensor_data)
    node.create_subscription(Detection2DArray, f"{args.prefix}/detections", on_dets, 20)
    deadline = time.monotonic() + args.timeout
    try:
        while rclpy.ok() and saved[0] < args.count and time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=0.1)
            time.sleep(args.interval if saved[0] else 0)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
    print(f"saved {saved[0]} snapshots to {out}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ros2 run aura_gesture debug", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("monitor", help="live terminal view of hypotheses, intents and node health")
    p.add_argument("--prefix", default="/aura/gesture")
    p.set_defaults(func=cmd_monitor)

    p = sub.add_parser("record", help="collect per-frame confidence, intents and diagnostics to JSONL")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--prefix", default="/aura/gesture")
    p.add_argument("--label", default="", help="ground truth for the whole recording, e.g. like/dislike/no_gesture")
    p.add_argument("--note", default="", help="free text stored in session.json (distance, PPE, lighting...)")
    p.add_argument("--duration", type=float, default=0, help="seconds; 0 = until Ctrl-C")
    p.add_argument("--images", action="store_true",
                   help="also save annotated frames (accepted, changes, mismatches, every Nth) + gallery.html")
    p.add_argument("--image-every", type=int, default=15, help="sample every Nth frame (0 = only events)")
    p.add_argument("--max-images", type=int, default=2000)
    p.add_argument("--save-raw", action="store_true", help="keep the unannotated JPEG next to each frame")
    p.add_argument("--image-topic", default="/aura/camera/image_raw/compressed")
    p.set_defaults(func=cmd_record)

    p = sub.add_parser("gallery", help="(re)build gallery.html from a record --images folder")
    p.add_argument("folder")
    p.set_defaults(func=cmd_gallery)

    p = sub.add_parser("summarize", help="confidence/accuracy/latency summary of a recording")
    p.add_argument("hypotheses")
    p.add_argument("--plot", default="")
    p.set_defaults(func=cmd_summarize)

    p = sub.add_parser("sweep", help="re-simulate the acceptance gate over parameter grids")
    p.add_argument("hypotheses")
    p.add_argument("--thresholds", nargs="+", type=float, default=[0.6, 0.7, 0.8, 0.9])
    p.add_argument("--windows", nargs="+", type=int, default=[8])
    p.add_argument("--votes", nargs="+", type=int, default=[3, 5])
    p.add_argument("--cooldown", type=float, default=1.0)
    p.add_argument("--rearm", type=int, default=3)
    p.add_argument("--json", default="")
    p.set_defaults(func=cmd_sweep)

    p = sub.add_parser("snapshot", help="save annotated frames with detections")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--prefix", default="/aura/gesture")
    p.add_argument("--image-topic", default="/aura/camera/image_raw/compressed")
    p.add_argument("--count", type=int, default=5)
    p.add_argument("--interval", type=float, default=0.5)
    p.add_argument("--timeout", type=float, default=30)
    p.add_argument("--only-detections", action="store_true")
    p.set_defaults(func=cmd_snapshot)

    args = ap.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
