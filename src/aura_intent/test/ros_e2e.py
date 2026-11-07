"""End-to-end check of a running aura_intent node over ROS 2.

Plays the two operator flows against the live node and checks the published
IntentEvents and speaker prompts:
  1. voice command -> CONFIRM question -> voice "yes" -> ACCEPT
  2. voice command -> CONFIRM question -> thumb_up gesture -> ACCEPT (fused)
plus stop (immediate), side talk (IGNORE) and a gesture "no" (cancel).

    ros2 launch aura_intent intent.launch.py &
    python3 src/aura_intent/test/ros_e2e.py
"""

import sys
import time

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from aura_interfaces.msg import GestureIntent, IntentEvent

DECISIONS = {0: "ACCEPT", 1: "CONFIRM", 2: "REJECT", 3: "IGNORE"}


class Driver(Node):
    def __init__(self):
        super().__init__("aura_intent_e2e")
        self.events, self.said = [], []
        self.transcript = self.create_publisher(String, "/aura_transcript", 10)
        self.gesture = self.create_publisher(GestureIntent, "/aura/gesture/intent", 10)
        self.create_subscription(IntentEvent, "/aura/intent/event", self.events.append, 50)
        self.create_subscription(String, "/aura/speaker/say", lambda m: self.said.append(m.data), 50)

    def spin_for(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            rclpy.spin_once(self, timeout_sec=0.05)

    def wait_event(self, timeout=10.0):
        n = len(self.events)
        end = time.time() + timeout
        while time.time() < end and len(self.events) == n:
            rclpy.spin_once(self, timeout_sec=0.05)
        if len(self.events) == n:
            raise TimeoutError("no IntentEvent")
        self.spin_for(0.2)  # let the matching speaker message arrive
        e = self.events[-1]
        print(f"  {e.event_id} {DECISIONS[e.decision]:<7} {e.intent:<13} {e.modality:<7} conf {e.confidence:.2f} "
              f"latency {e.latency_ms:4.0f}ms outcome={e.outcome or '-':<13} say={self.said[-1] if self.said else ''!r}")
        return e

    def say(self, text):
        print(f"> operator says {text!r}")
        self.transcript.publish(String(data=text))
        return self.wait_event()

    def show(self, gesture, intent):
        print(f"> operator shows {gesture}")
        msg = GestureIntent(gesture=gesture, intent=intent, state=GestureIntent.STATE_ACCEPTED, confidence=0.9)
        msg.header.stamp = self.get_clock().now().to_msg()
        self.gesture.publish(msg)
        return self.wait_event()


def main():
    rclpy.init()
    d = Driver()
    failures = []

    def check(cond, what):
        if not cond:
            failures.append(what)
            print(f"  FAIL: {what}")

    end = time.time() + 30
    while d.count_subscribers("/aura_transcript") == 0 and time.time() < end:
        d.spin_for(0.2)
    check(d.count_subscribers("/aura_transcript") > 0, "intent node is subscribed to /aura_transcript")
    d.spin_for(1.0)

    e = d.say("Robot, follow me please.")
    check(e.decision == 1 and e.intent == "come_follow", "voice command is held for confirmation")
    check(d.said and d.said[-1].startswith("Did you say"), "speaker asks the operator")
    e = d.say("Yes, do it.")
    check(e.decision == 0 and e.intent == "come_follow" and e.outcome == "correct", "voice yes executes")

    e = d.say("Inspect the compressor bay.")
    check(e.decision == 1 and e.intent == "target_select", "target command is held")
    e = d.show("thumb_up", "confirm")
    check(e.decision == 0 and e.intent == "target_select" and e.modality == "fused", "thumb up executes (fused)")

    e = d.say("Next waypoint.")
    check(e.decision == 1 and e.intent == "next_step", "next step is held")
    e = d.show("thumb_down", "reject")
    check(e.intent == "cancel" and e.outcome == "misrecognized", "thumb down cancels")

    e = d.say("Stop! Stop!")
    check(e.decision == 0 and e.intent == "stop" and e.safety_critical, "stop executes immediately")

    e = d.say("Mike, can you check the gauge on the way out?")
    check(e.decision == 3 and not e.engaged, "side talk is ignored")

    e = d.show("fist", "stop")
    check(e.decision == 0 and e.intent == "stop", "fist stops")

    d.destroy_node()
    rclpy.shutdown()
    print("PASS" if not failures else f"FAILED: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
