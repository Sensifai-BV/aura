"""End-to-end check of a running aura_tts node over ROS 2.

Sends the intent module's prompts on /aura/speaker/say and checks the speaking
state and diagnostics: say -> speaking latency for synthesised and cached
phrases, and that a new request interrupts a long one.

    ros2 launch aura_tts tts.launch.py output_device:=none &
    python3 src/aura_tts/test/ros_e2e.py
"""

import statistics
import sys
import time

import rclpy
from aura_interfaces.msg import ModuleStatus
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile
from std_msgs.msg import Bool, String

PROMPTS = [
    "Did you say go to pump station three? Say yes or show thumb up.",
    "Did you say next step? Say yes or show thumb up.",
    "Please confirm: inspect valve V-104 next to the north compressor.",
    "Thermal reading on motor M-12 is 84 °C.",
    "Come follow, executing.",
    "Cancelled go to the separator tank.",
]
CACHED = ["Stop, executing.", "Pause, executing.", "Sorry, I did not understand. Please repeat."]


class Driver(Node):
    def __init__(self):
        super().__init__("aura_tts_e2e")
        self.edges, self.status = [], None
        self.say_pub = self.create_publisher(String, "/aura/speaker/say", 10)
        latched = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.create_subscription(Bool, "/aura/speaker/speaking", self._on_speaking, latched)
        self.create_subscription(ModuleStatus, "/aura/speaker/status", self._on_status, 10)

    def _on_speaking(self, msg):
        self.edges.append((time.monotonic(), msg.data))

    def _on_status(self, msg):
        self.status = dict(zip(msg.keys, msg.values)) | {"level": msg.level, "message": msg.message}

    def spin_until(self, predicate, timeout):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            rclpy.spin_once(self, timeout_sec=0.01)
            if predicate():
                return True
        return False

    def say(self, text):
        n = len(self.edges)
        sent = time.monotonic()
        self.say_pub.publish(String(data=text))
        if not self.spin_until(lambda: any(on for _, on in self.edges[n:]), 10):
            raise TimeoutError(f"never started speaking: {text!r}")
        started = next(t for t, on in self.edges[n:] if on)
        return (started - sent) * 1000

    def wait_quiet(self, timeout=20):
        if not self.spin_until(lambda: self.edges and not self.edges[-1][1], timeout):
            raise TimeoutError("still speaking")


def main():
    rclpy.init()
    d = Driver()
    failures = 0
    if not d.spin_until(lambda: d.say_pub.get_subscription_count() > 0 and d.edges, 30):
        print("FAIL no aura_tts node on /aura/speaker/say")
        return 1
    print("Synthesised prompts (say -> speaking):")
    synth = []
    for text in PROMPTS:
        ms = d.say(text)
        synth.append(ms)
        d.wait_quiet()
        print(f"  {ms:6.0f} ms  {text}")
    print("Cached phrases:")
    cached = []
    for text in CACHED:
        ms = d.say(text)
        cached.append(ms)
        d.wait_quiet()
        print(f"  {ms:6.0f} ms  {text}")

    print("Interrupt: long report, then 'Stop, executing.' after 0.5 s")
    d.say("Inspection point six of eleven completed. The pressure gauge shows four point five bar. "
          "All twelve checkpoints are clear. Heading to the flare stack.")
    d.spin_until(lambda: False, 0.5)
    stop_ms = d.say("Stop, executing.")
    d.wait_quiet()
    print(f"  stop started {stop_ms:.0f} ms after it was sent")
    if stop_ms > 150:
        print("FAIL stop acknowledgement waited for the report")
        failures += 1

    d.spin_until(lambda: d.status is not None and int(d.status.get("interrupted", 0)) >= 1, 3)
    print(f"Diagnostics: {d.status}")
    if not d.status or d.status["level"] != ModuleStatus.OK or int(d.status["interrupted"]) < 1:
        print("FAIL diagnostics missing, not OK or interruption not counted")
        failures += 1
    print(f"say -> speaking p50: synthesised {statistics.median(synth):.0f} ms, "
          f"cached {statistics.median(cached):.0f} ms")
    if statistics.median(cached) > 100:
        print("FAIL cached phrases are not immediate")
        failures += 1
    print("PASS" if not failures else f"{failures} FAILED")
    d.destroy_node()
    rclpy.shutdown()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
