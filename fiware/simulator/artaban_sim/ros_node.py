"""ROS 2 publisher for the Artaban / AURA topics (stand-in for the robot and the AURA modules).

Publishes exactly the topics the real nodes publish or will publish (see fiware/docs/DATA_MODEL.md);
Orion-LD's native DDS bridge maps each topic onto an NGSI-LD attribute (fiware/orion/config-dds.json).
Nothing here talks to FIWARE directly.

Test conditions are requested on /aura_sim/scenario (std_msgs/String), e.g.
    ros2 topic pub --once /aura_sim/scenario std_msgs/msg/String "{data: gas_leak}"
"""

from __future__ import annotations

import math
import os
import random
import time
from collections import deque
from datetime import datetime, timezone

import rclpy
from aura_interfaces.msg import ArtabanStatus, EnvironmentReading, GestureIntent, IntentEvent, ModuleStatus
from diagnostic_msgs.msg import DiagnosticStatus, KeyValue
from nav_msgs.msg import Odometry
from rclpy.node import Node
from sensor_msgs.msg import BatteryState, Imu
from std_msgs.msg import Float32, String

from .hri import OPERATORS, IntentGenerator, condition_for, recognition_mode
from .robot import JOINTS, Robot

MODULES = ("stt", "intent", "gesture", "speaker")
DECISION = {"ACCEPT": IntentEvent.DECISION_ACCEPT, "CONFIRM": IntentEvent.DECISION_CONFIRM,
            "REJECT": IntentEvent.DECISION_REJECT, "IGNORE": IntentEvent.DECISION_IGNORE}
GESTURE_NAME = {"fist_raise": "fist", "arm_wave": "palm", "point": "point", "thumb_up": "thumb_up", "raise_wave": "palm"}
SPEAKER_LINES = {
    "CONFIRM": "Did you say {intent}? Thumb up to confirm.",
    "REJECT": "Sorry, please repeat.",
    "ACCEPT": "{intent}, executing.",
    "STOP": "Stopping now.",
}
SCENARIO_SECONDS = {
    "noise_spike": 90, "mask_on": 180, "gas_leak": 90, "motor_overheat": 120,
    "comms_degraded": 90, "low_battery": 1, "estop": 20,
}


def quaternion(roll: float, pitch: float, yaw: float):
    cr, sr = math.cos(roll / 2), math.sin(roll / 2)
    cp, sp = math.cos(pitch / 2), math.sin(pitch / 2)
    cy, sy = math.cos(yaw / 2), math.sin(yaw / 2)
    return (sr * cp * cy - cr * sp * sy, cr * sp * cy + sr * cp * sy, cr * cp * sy - sr * sp * cy, cr * cp * cy + sr * sp * sy)


def noisy(text: str, noise_db: float) -> str:
    """Transcript as STT produces it, with a noise marker when the floor is high."""
    if noise_db < 80 or len(text) < 4:
        return text
    i = random.randrange(1, len(text) - 2)
    return f"[noise] {text[:i]} ... {text[i + 1:]}"


class ArtabanPublisher(Node):
    def __init__(self, rate_hz: float = 1.0, mission_minutes: float = 12.0):
        super().__init__("artaban_telemetry")
        self.robot = Robot(dwell_s=max(10.0, (mission_minutes * 60 - 260) / 7))
        self.intents = IntentGenerator()
        self.operator = random.choice(OPERATORS)
        self.mission_id = ""
        self.next_event_in = 5.0
        self.module_events = {m: deque() for m in MODULES}

        self.platform_only = os.environ.get("SIM_TOPICS", "all") == "platform"  # real AURA modules publish the rest
        pub = self.create_publisher
        self.pub_battery = pub(BatteryState, "/battery_state", 10)
        self.pub_odom = pub(Odometry, "/odom", 10)
        self.pub_imu = pub(Imu, "/imu/data", 10)
        self.pub_status = pub(ArtabanStatus, "/artaban/status", 10)
        self.pub_env = pub(EnvironmentReading, "/artaban/environment", 10)
        if not self.platform_only:
            self.create_aura_publishers(pub)
        self.create_subscription(String, "/aura_sim/scenario", self.on_scenario, 10)

        self.dt = 1.0 / rate_hz
        self.tick = 0
        self.new_mission()
        self.create_timer(self.dt, self.step)

    def create_aura_publishers(self, pub) -> None:
        self.pub_transcript = pub(String, "/aura_transcript", 10)
        self.pub_partial = pub(String, "/aura_partial_transcript", 10)
        self.pub_gesture = pub(GestureIntent, "/aura/gesture/intent", 10)
        self.pub_intent = pub(IntentEvent, "/aura/intent/event", 10)
        self.pub_say = pub(String, "/aura/speaker/say", 10)
        self.pub_module = {m: pub(ModuleStatus, f"/aura/{m}/status", 10) for m in MODULES}

    # -- mission -------------------------------------------------------------------
    def new_mission(self) -> None:
        self.operator = random.choice(OPERATORS)
        # SIM_MISSION_ID pins the session id when another ROS 2 host cannot receive /artaban/status
        self.mission_id = os.environ.get("SIM_MISSION_ID") or datetime.now(timezone.utc).strftime("M%Y%m%d-%H%M")
        self.intents.counter = 0
        self.robot.start_mission()
        self.get_logger().info(f"mission {self.mission_id} started ({self.operator.id})")

    def masked(self) -> bool:
        return self.operator.masked or self.robot.active("mask_on")

    def on_scenario(self, msg: String) -> None:
        name = msg.data.strip()
        if name == "none":
            self.robot.scenarios.clear()
        elif name in SCENARIO_SECONDS:
            self.robot.scenario(name, SCENARIO_SECONDS[name])
            self.get_logger().info(f"test condition {name}")

    def step(self) -> None:
        r = self.robot
        if r.step(self.dt) == "mission_complete":
            self.get_logger().info(f"mission {self.mission_id} complete, charging")
        if r.state == "charging" and r.battery >= 85:
            self.new_mission()
        stamp = self.get_clock().now().to_msg()
        self.publish_platform(stamp)
        if not self.platform_only:
            self.maybe_event(stamp)
        self.tick += 1
        if self.tick % max(1, int(2 / self.dt)) == 0:
            if not self.platform_only:
                self.publish_module_status()

    # -- Artaban platform -----------------------------------------------------------
    def publish_platform(self, stamp) -> None:
        r = self.robot
        b = BatteryState()
        b.header.stamp, b.header.frame_id = stamp, "base_link"
        b.voltage, b.current = float(r.voltage), float(r.current)
        b.temperature, b.percentage = float(r.battery_temp), float(r.battery / 100.0)
        b.power_supply_status = BatteryState.POWER_SUPPLY_STATUS_CHARGING if r.charging else BatteryState.POWER_SUPPLY_STATUS_DISCHARGING
        b.power_supply_technology = BatteryState.POWER_SUPPLY_TECHNOLOGY_LION
        b.present = True
        self.pub_battery.publish(b)

        o = Odometry()
        o.header.stamp, o.header.frame_id, o.child_frame_id = stamp, "site", "base_link"
        o.pose.pose.position.x, o.pose.pose.position.y = float(r.x), float(r.y)
        qx, qy, qz, qw = quaternion(0.0, 0.0, r.yaw)
        o.pose.pose.orientation.x, o.pose.pose.orientation.y, o.pose.pose.orientation.z, o.pose.pose.orientation.w = qx, qy, qz, qw
        o.twist.twist.linear.x = float(r.speed)
        self.pub_odom.publish(o)

        imu = Imu()
        imu.header.stamp, imu.header.frame_id = stamp, "imu_link"
        qx, qy, qz, qw = quaternion(math.radians(r.roll), math.radians(r.pitch), r.yaw)
        imu.orientation.x, imu.orientation.y, imu.orientation.z, imu.orientation.w = qx, qy, qz, qw
        imu.linear_acceleration.z = 9.81
        self.pub_imu.publish(imu)

        _, _, waypoint = r.target
        s = ArtabanStatus()
        s.header.stamp = stamp
        s.mission_id, s.mission_state, s.mission_progress = self.mission_id, r.state, float(r.progress)
        s.current_waypoint, s.operation_mode, s.gait = waypoint, r.mode, r.gait
        s.safety_state, s.e_stop, s.odometry_m = r.safety_state, r.state == "safe-stop", float(r.odometry)
        s.joint_names = list(JOINTS)
        s.joint_temperatures = [round(float(r.joint_temp[j]), 1) for j in JOINTS]
        s.max_joint_temperature = max(s.joint_temperatures)
        s.cpu_load, s.gpu_load, s.memory_usage = float(r.cpu), float(r.gpu), float(r.mem)
        s.soc_temperature, s.wifi_rssi = float(r.soc_temp), float(r.rssi)
        s.link_latency_ms, s.packet_loss = float(r.link_latency), float(r.packet_loss)
        self.pub_status.publish(s)

        e = EnvironmentReading()
        e.header.stamp = stamp
        e.h2s_ppm, e.ch4_lel, e.co_ppm, e.o2_vol = float(r.h2s), float(r.ch4), float(r.co), float(r.o2)
        e.temperature, e.relative_humidity, e.thermal_max = float(r.air_temp), float(r.humidity), float(r.thermal_max)
        e.la_eq, e.la_max, e.noise_source = float(r.noise_db), float(r.noise_peak), r.noise_source
        self.pub_env.publish(e)

    # -- AURA modules -------------------------------------------------------------------
    def mark(self, module: str) -> None:
        self.module_events[module].append(time.time())

    def maybe_event(self, stamp) -> None:
        r = self.robot
        if r.state in ("charging", "idle"):
            return
        self.next_event_in -= self.dt
        if self.next_event_in > 0:
            return
        self.next_event_in = random.expovariate(1 / 6.0) + 1.5
        ev = self.intents.event(self.mission_id, self.operator, r.state, r.noise_db, self.masked(), r.gpu, r.link_latency)

        # Platform reaction to accepted safety commands.
        if ev["decision"] == "ACCEPT" and ev["outcome"] in ("correct", "false_activation"):
            if ev["intent"] in ("stop", "pause"):
                r.pause(random.uniform(4, 9))
            elif ev["intent"] == "resume":
                r.resume()

        # microphone → stt → gesture → intent → speaker, as the real pipeline publishes them
        if ev["modality"] != "gesture":
            text = noisy(ev.get("utterance") or "", ev["noiseDb"])
            words = text.split()
            if len(words) > 1:
                self.pub_partial.publish(String(data=" ".join(words[:-1])))
            self.pub_transcript.publish(String(data=text))
            self.mark("stt")
        if ev.get("gesture"):
            g = GestureIntent()
            g.header.stamp = stamp
            g.gesture = GESTURE_NAME.get(ev["gesture"], ev["gesture"])
            g.intent, g.raw_label = ev["intent"], g.gesture
            g.state = GestureIntent.STATE_ACCEPTED if ev["decision"] == "ACCEPT" else GestureIntent.STATE_CANDIDATE
            g.confidence = float(min(0.99, ev["confidence"] + random.uniform(-0.05, 0.08)))
            g.frame_confidence = float(min(0.99, g.confidence + random.uniform(-0.04, 0.04)))
            g.threshold, g.votes, g.window = 0.7, random.randint(4, 7), 7
            g.inference_ms, g.latency_ms, g.model_id = float(random.uniform(14, 26)), float(ev["latencyMs"] * 0.4), "yolov10n-gestures"
            self.pub_gesture.publish(g)
            self.mark("gesture")

        i = IntentEvent()
        i.header.stamp = stamp
        i.event_id, i.mission_id, i.intent = ev["eventId"], ev["missionId"], ev["intent"]
        i.utterance, i.gesture, i.modality = ev.get("utterance") or "", ev.get("gesture") or "", ev["modality"]
        i.decision = DECISION[ev["decision"]]
        i.confidence, i.accept_threshold = float(ev["confidence"]), float(ev["threshold"])
        i.reject_threshold = float(max(0.0, ev["threshold"] - 0.2))
        i.latency_ms, i.noise_db = float(ev["latencyMs"]), float(ev["noiseDb"])
        i.acoustic_condition, i.operator_id = ev["condition"], ev["operatorId"]
        i.engaged, i.safety_critical, i.correct, i.outcome = ev["engaged"], ev["safetyCritical"], ev["correct"], ev["outcome"]
        i.time_to_safe_stop_ms = float(ev.get("timeToSafeStopMs", 0.0))
        i.recovery_time_s = float(ev.get("recoveryTimeS", 0.0))
        self.pub_intent.publish(i)
        self.mark("intent")

        if ev["decision"] != "IGNORE":
            key = "STOP" if ev["decision"] == "ACCEPT" and ev["intent"] == "stop" else ev["decision"]
            self.pub_say.publish(String(data=SPEAKER_LINES[key].format(intent=ev["intent"].replace("_", " "))))
            self.mark("speaker")

    def publish_module_status(self) -> None:
        r, now = self.robot, time.time()
        per_min = {}
        for m, q in self.module_events.items():
            while q and now - q[0] > 300:
                q.popleft()
            per_min[m] = len(q) / 5.0
        noise, comms_bad = r.noise_db, r.active("comms_degraded")
        cond = condition_for(noise, self.masked())
        health = {
            "stt": noise >= 85 or r.gpu > 85, "intent": False,
            "gesture": comms_bad, "speaker": noise >= 88 and r.state != "safe-stop",
        }
        rate = {
            "stt": (6.25 if noise < 85 else 4.1, "Hz"), "intent": (per_min["intent"], "/min"),
            "gesture": (7.5 if comms_bad else 15.0, "Hz"), "speaker": (per_min["speaker"], "/min"),
        }
        latency = {
            "stt": round(160 + max(0, noise - 65) * 2.5 + random.uniform(0, 25)),
            "intent": round(40 + r.gpu * 0.4 + random.uniform(0, 10)),
            "gesture": round(70 + (120 if comms_bad else 0) + random.uniform(0, 15)), "speaker": 95 + random.randint(0, 20),
        }
        stamp_now = self.get_clock().now().to_msg()
        for m in MODULES:
            st = ModuleStatus()
            st.level = ModuleStatus.WARN if health[m] else ModuleStatus.OK
            st.name, st.hardware_id = f"aura/{m}", "artaban-01"
            st.message = "degraded" if health[m] else "ok"
            values = {"rate": f"{rate[m][0]:.2f}", "rate_unit": rate[m][1], "latency_ms": str(latency[m])}
            if m == "intent":
                values.update({"accept_threshold": f"{self.operator.threshold:.2f}",
                               "reject_threshold": f"{self.operator.threshold - 0.2:.2f}",
                               "recognition_mode": recognition_mode(cond, noise), "acoustic_condition": cond,
                               "engagement": self.intents.engagement, "operator_id": self.operator.id})
            st.keys, st.values = list(values), [str(v) for v in values.values()]
            st.header.stamp = stamp_now
            self.pub_module[m].publish(st)


def main() -> None:
    import os

    rclpy.init()
    node = ArtabanPublisher(float(os.environ.get("SIM_RATE_HZ", "1")), float(os.environ.get("SIM_MISSION_MINUTES", "12")))
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
