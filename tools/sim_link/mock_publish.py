"""Mock Artaban + AURA data for the Simulator plane (DDS domain 1 on aura-dash.sensifai.com).

Runs inside the aura-ddsrouter image (ROS 2 Jazzy + aura_interfaces), next to a DDS Router client (see run.sh).
Every topic Orion-LD maps is published, the same contract the robot and the modules use (fiware/docs/DATA_MODEL.md):
telemetry at 1 Hz, and every 15 s one operator exchange from a fixed cycle:

  stop (voice)       -> ACCEPT, then the adapter's feedback: at rest after 700 ms
  next step + yes    -> CONFIRM, then ACCEPT correct (voice)
  follow me + thumb  -> CONFIRM, then ACCEPT correct (fused)
  fist while talking -> ACCEPT stop (gesture), then the adapter's feedback: false activation
  side talk          -> IGNORE
  mumble             -> REJECT

    python3 mock_publish.py [--seconds 600] [--mission M-SIM-...]
"""

import argparse
import math
import time

import rclpy
from aura_interfaces.msg import ArtabanStatus, EnvironmentReading, GestureIntent, IntentEvent, ModuleStatus
from nav_msgs.msg import Odometry
from sensor_msgs.msg import BatteryState, Imu
from std_msgs.msg import String

JOINTS = [f"{leg}_{j}" for leg in ("FL", "FR", "RL", "RR") for j in ("hip", "thigh", "calf")]
ACCEPT, CONFIRM, REJECT, IGNORE = 0, 1, 2, 3


class Mock:
    def __init__(self, mission):
        rclpy.init()
        self.n = n = rclpy.create_node("aura_sim_link_mock")
        self.mission, self.counter = mission, 0
        self.pub = {
            "battery": n.create_publisher(BatteryState, "/battery_state", 10),
            "odom": n.create_publisher(Odometry, "/odom", 10),
            "imu": n.create_publisher(Imu, "/imu/data", 10),
            "status": n.create_publisher(ArtabanStatus, "/artaban/status", 10),
            "env": n.create_publisher(EnvironmentReading, "/artaban/environment", 10),
            "partial": n.create_publisher(String, "/aura_partial_transcript", 10),
            "final": n.create_publisher(String, "/aura_transcript", 10),
            "gesture": n.create_publisher(GestureIntent, "/aura/gesture/intent", 10),
            "intent": n.create_publisher(IntentEvent, "/aura/intent/event", 10),
            "say": n.create_publisher(String, "/aura/speaker/say", 10),
        }
        self.mods = {m: n.create_publisher(ModuleStatus, f"/aura/{m}/status", 10)
                     for m in ("stt", "intent", "gesture", "speaker")}

    def stamp(self):
        return self.n.get_clock().now().to_msg()

    # -- telemetry ---------------------------------------------------------------------------------
    def telemetry(self, k):
        now = self.stamp()
        b = BatteryState()
        b.header.stamp, b.percentage, b.voltage, b.current = now, max(0.2, 0.92 - 0.0005 * k), 48.6, -6.0
        b.temperature, b.present = 31.0, True
        self.pub["battery"].publish(b)
        o = Odometry()
        o.header.stamp, o.header.frame_id = now, "odom"
        o.pose.pose.position.x, o.pose.pose.position.y = 0.35 * k, 2.0 * math.sin(k / 12)
        o.pose.pose.orientation.w, o.twist.twist.linear.x = 1.0, 0.35
        self.pub["odom"].publish(o)
        i = Imu()
        i.header.stamp, i.header.frame_id = now, "imu_link"
        i.orientation.w, i.linear_acceleration.z = 1.0, 9.81
        self.pub["imu"].publish(i)
        s = ArtabanStatus()
        s.header.stamp, s.mission_id, s.mission_state = now, self.mission, "inspecting"
        s.mission_progress, s.current_waypoint = min(100.0, k / 6), f"WP-{1 + k // 120:02d}"
        s.operation_mode, s.gait, s.safety_state, s.e_stop = "supervised", "walk", "nominal", False
        s.odometry_m, s.joint_names = 0.35 * k, JOINTS
        s.joint_temperatures = [38.0 + (j % 3) + 0.01 * k for j in range(12)]
        s.max_joint_temperature = max(s.joint_temperatures)
        s.cpu_load, s.gpu_load, s.memory_usage, s.soc_temperature = 38.0, 41.0, 52.0, 63.0
        s.wifi_rssi, s.link_latency_ms, s.packet_loss = -57.0, 45.0, 0.1
        self.pub["status"].publish(s)
        e = EnvironmentReading()
        e.header.stamp, e.h2s_ppm, e.ch4_lel, e.co_ppm, e.o2_vol = now, 1.2, 1.5, 2.5, 20.9
        e.temperature, e.relative_humidity, e.thermal_max = 24.0, 47.0, 52.0
        e.la_eq, e.la_max, e.noise_source = 66.0, 72.0, "compressor"
        self.pub["env"].publish(e)
        for name, p in self.mods.items():
            m = ModuleStatus()
            m.header.stamp, m.level, m.name, m.message, m.hardware_id = now, ModuleStatus.OK, name, "ready", "sim-link"
            m.keys, m.values = ["rate", "rate_unit", "latency_ms"], ["15.0" if name == "gesture" else "1.0",
                                                                     "Hz" if name == "gesture" else "/min", "120"]
            self.mods[name].publish(m)

    # -- interaction -------------------------------------------------------------------------------
    def event(self, intent, decision, utterance="", gesture="", modality="voice", confidence=0.95, outcome="",
              correct=False, event_id=None):
        if event_id is None:
            self.counter += 1
            event_id = f"SIM-{self.counter:04d}"
        m = IntentEvent()
        m.header.stamp, m.event_id, m.mission_id = self.stamp(), event_id, self.mission
        m.intent, m.utterance, m.gesture, m.modality, m.decision = intent, utterance, gesture, modality, decision
        m.confidence, m.accept_threshold, m.reject_threshold = confidence, 0.72, 0.52
        m.latency_ms, m.noise_db, m.acoustic_condition = 80.0 + 40 * (self.counter % 3), 66.0, "clean"
        m.operator_id, m.engaged = "operator-01", decision != IGNORE
        m.safety_critical = intent in ("stop", "pause", "cancel")
        m.correct, m.outcome = correct, outcome
        self.pub["intent"].publish(m)
        return m

    def say(self, text):
        self.pub["say"].publish(String(data=text))

    def heard(self, text):
        self.pub["partial"].publish(String(data=text[: max(3, len(text) // 2)]))
        time.sleep(0.3)
        self.pub["final"].publish(String(data=text))

    def gesture(self, name, intent, confidence):
        g = GestureIntent()
        g.header.stamp, g.gesture, g.intent, g.state = self.stamp(), name, intent, GestureIntent.STATE_ACCEPTED
        g.raw_label, g.confidence, g.frame_confidence, g.votes, g.window = name, confidence, 0.9, 6, 8
        g.latency_ms, g.inference_ms, g.model_id = 165.0, 8.0, "YOLOv10n_gestures"
        self.pub["gesture"].publish(g)

    def exchange(self, step):
        kind = step % 6
        if kind == 0:
            self.heard("Artaban, stop")
            ev = self.event("stop", ACCEPT, "Artaban, stop", confidence=0.99)
            self.say("Stop, executing.")
            time.sleep(2)
            ev.time_to_safe_stop_ms = 700.0  # adapter feedback: robot at rest
            self.pub["intent"].publish(ev)
        elif kind == 1:
            self.heard("next waypoint")
            ev = self.event("next_step", CONFIRM, "next waypoint", confidence=0.97)
            self.say("Did you say next step?")
            time.sleep(5)
            self.heard("yes")
            self.event("next_step", ACCEPT, "next waypoint", confidence=0.97, outcome="correct", correct=True)
            self.say("Next step, executing.")
        elif kind == 2:
            self.heard("follow me")
            self.event("come_follow", CONFIRM, "follow me", confidence=0.96)
            self.say("Did you say come follow?")
            time.sleep(5)
            self.gesture("thumb_up", "confirm", 0.62)
            self.event("come_follow", ACCEPT, "follow me", gesture="thumb_up", modality="fused", confidence=0.96,
                       outcome="correct", correct=True)
            self.say("Come follow, executing.")
        elif kind == 3:
            self.gesture("fist", "stop", 0.49)
            ev = self.event("stop", ACCEPT, gesture="fist", modality="gesture", confidence=0.49)
            self.say("Stop, executing.")
            time.sleep(3)
            ev.outcome, ev.time_to_safe_stop_ms = "false_activation", 650.0  # adapter feedback: nobody meant it
            self.pub["intent"].publish(ev)
        elif kind == 4:
            self.heard("Mike, follow the red line to the compressor")
            self.event("unknown", IGNORE, "Mike, follow the red line to the compressor", confidence=0.99)
        else:
            self.heard("all the mission")
            self.event("pause", REJECT, "all the mission", confidence=0.43, outcome="rejected")
            self.say("Sorry, I did not understand. Please repeat.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=600)
    ap.add_argument("--mission", default=time.strftime("M-SIM-%Y%m%d-%H%M", time.gmtime()))
    ap.add_argument("--every", type=float, default=15, help="seconds between operator exchanges")
    args = ap.parse_args()
    mock = Mock(args.mission)
    print(f"mission {args.mission}: telemetry 1 Hz, an exchange every {args.every:.0f} s, for {args.seconds:.0f} s",
          flush=True)
    t0, k, step, next_exchange = time.time(), 0, 0, time.time() + 5
    while rclpy.ok() and time.time() - t0 < args.seconds:
        mock.telemetry(k)
        if time.time() >= next_exchange:
            mock.exchange(step)
            print(f"t={time.time() - t0:5.0f}s exchange {step % 6} (events so far {mock.counter})", flush=True)
            step += 1
            next_exchange += args.every
        k += 1
        time.sleep(max(0.0, t0 + k - time.time()))
    print(f"done: {k} telemetry ticks, {mock.counter} intent events", flush=True)


if __name__ == "__main__":
    main()
