"""ROS 2 end-to-end probe: text on a topic -> synthesised audio on a topic.

A minimal speaker node (subscribe /aura/speaker/say, publish /aura/speaker/audio)
and a probe that sends every prompt and times say -> audio received over DDS.
Run inside a sourced ROS 2 environment with sherpa-onnx installed:

    python ros2_probe.py --model kokoro --threads 4 --out results_ros2
"""

import argparse
import json
import threading
import time
from pathlib import Path

import numpy as np
import rclpy
import sherpa_onnx
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from std_msgs.msg import Float32MultiArray, String

from synth import VOICE, generate, model_config


class Speaker(Node):
    def __init__(self, name, threads):
        super().__init__("aura_speaker_probe")
        self.name = name
        self.tts = sherpa_onnx.OfflineTts(sherpa_onnx.OfflineTtsConfig(model=model_config(name, threads)))
        generate(self.tts, name, "Warming up the speaker module.", VOICE[name])
        self.pub = self.create_publisher(Float32MultiArray, "/aura/speaker/audio", 10)
        self.create_subscription(String, "/aura/speaker/say", self.on_say, 10)

    def on_say(self, msg):
        audio = generate(self.tts, self.name, msg.data, VOICE[self.name])
        out = Float32MultiArray()
        out.data = np.asarray(audio.samples, dtype=np.float32).tolist()
        self.pub.publish(out)


class Probe(Node):
    def __init__(self):
        super().__init__("aura_speaker_probe_client")
        self.pub = self.create_publisher(String, "/aura/speaker/say", 10)
        self.got = threading.Event()
        self.samples = 0
        self.create_subscription(Float32MultiArray, "/aura/speaker/audio", self.on_audio, 10)

    def on_audio(self, msg):
        self.samples = len(msg.data)
        self.got.set()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, choices=sorted(VOICE))
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--out", default=str(Path(__file__).with_name("results_ros2")))
    args = ap.parse_args()
    prompts = [p for p in Path(__file__).with_name("prompts.txt").read_text().splitlines() if p.strip()]

    rclpy.init()
    speaker, probe = Speaker(args.model, args.threads), Probe()
    ex = MultiThreadedExecutor(num_threads=4)
    ex.add_node(speaker)
    ex.add_node(probe)
    threading.Thread(target=ex.spin, daemon=True).start()
    while probe.pub.get_subscription_count() == 0:
        time.sleep(0.05)

    rows = []
    for i, text in enumerate(prompts):
        probe.got.clear()
        t = time.perf_counter()
        probe.pub.publish(String(data=text))
        ok = probe.got.wait(timeout=30)
        rows.append({"model": args.model, "idx": i, "ok": ok, "e2e_ms": (time.perf_counter() - t) * 1000,
                     "samples": probe.samples})
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{args.model}_t{args.threads}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    e2e = np.array([r["e2e_ms"] for r in rows])
    print(json.dumps({"model": args.model, "ok": sum(r["ok"] for r in rows), "n": len(rows),
                      "p50_ms": float(np.median(e2e)), "p95_ms": float(np.percentile(e2e, 95))}))
    ex.shutdown()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
