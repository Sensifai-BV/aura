"""ROS2 intent node: speech transcripts and accepted gestures -> operator commands.

Transcripts are parsed by the fine-tuned LFM2.5 model on a worker thread so the
executor keeps serving gestures (a fist must stop the robot even while a
sentence is being parsed). Every decision is published as an IntentEvent; the
speaker gets the confirmation question or the "executing" feedback.
"""

import queue
import signal
import threading
import time

import rclpy
from aura_interfaces.msg import ModuleStatus
from rcl_interfaces.msg import ParameterDescriptor
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from std_msgs.msg import Bool, String

from aura_interfaces.msg import ArtabanStatus, EnvironmentReading, GestureIntent, IntentEvent

from .config import IntentConfig, resolve_model_path
from .dialog import DECISION_NAMES, IGNORE, REJECT, Dialog
from .metrics import stats

WINDOW_S = 60.0


class IntentNode(Node):
    def __init__(self):
        super().__init__("aura_intent")
        self.stop_event = threading.Event()
        self.transcripts = queue.Queue(maxsize=8)
        self.results = queue.Queue(maxsize=64)
        self.lock = threading.Lock()
        self.failure = None
        self.thread = None
        self.counter = 0
        self.mission_id = ""
        self.noise_db = 0.0
        self.recent = []  # (time, latency_ms, engaged) of the last WINDOW_S
        defaults = IntentConfig.defaults()
        for name, default in defaults.items():
            self.declare_parameter(name, default, ParameterDescriptor(
                read_only=True, description="Startup setting; restart the intent node to change it"))
        self.config = config = IntentConfig.from_mapping(
            {name: self.get_parameter(name).value for name in defaults})
        self.mission_id = config.mission_id
        self.session_prefix = config.session_prefix or time.strftime("S%d%H%M%S", time.gmtime())
        model_path = resolve_model_path(config.model_path)
        self.model_id = config.model_id or model_path.stem
        self.get_logger().info(f"Loading intent model {model_path}")
        from .model import IntentModel

        self.model = IntentModel(model_path, config.num_threads, n_gpu_layers=config.n_gpu_layers)
        self.model.warmup()
        self.dialog = Dialog(config.accept_threshold, config.reject_threshold, config.confirm_mode,
                             config.confirm_timeout_s)
        self.event_pub = self.create_publisher(IntentEvent, config.event_topic, 50)
        self.speaker_pub = self.create_publisher(String, config.speaker_topic, 10)
        self.diagnostics_pub = self.create_publisher(ModuleStatus, config.diagnostics_topic, 10)
        self.create_subscription(String, config.transcript_topic, self._on_transcript, 10)
        self.create_subscription(GestureIntent, config.gesture_topic, self._on_gesture, 50)
        if config.speaking_topic:
            self.create_subscription(Bool, config.speaking_topic, self._on_speaking, 10)
        self.create_subscription(ArtabanStatus, config.status_topic, self._on_status, 10)
        self.create_subscription(EnvironmentReading, config.environment_topic, self._on_environment, 10)
        self.create_timer(0.02, self._publish)
        if config.diagnostics_period_s > 0:
            self.create_timer(config.diagnostics_period_s, self._diagnostics)
        self.metrics = None
        if config.debug:
            from .metrics import MetricsLog
            self.metrics = MetricsLog(config.metrics_dir, "intent", [
                "event_id", "mission_id", "decision", "intent", "target", "modality", "confidence", "latency_ms",
                "parse_ms", "answer_s", "utterance", "gesture", "outcome", "correct", "say", "safety_critical", "engaged",
                "noise_db", "acoustic_condition", "accept_threshold", "reject_threshold", "model_id"])
            self.metrics.summary = self._summary
            self.create_timer(5.0, self.metrics.tick)  # summary survives a kill
            self.get_logger().info(f"debug: metrics to {self.metrics.path}")
        self.thread = threading.Thread(target=self._parse, name="aura-intent", daemon=True)
        self.thread.start()
        self.get_logger().info(
            f"Intent ready: model {self.model_id}; confirm {config.confirm_mode}; thresholds "
            f"accept {config.accept_threshold} reject {config.reject_threshold}; events on {config.event_topic}")

    # -- inputs ------------------------------------------------------------------------------------
    def _on_transcript(self, msg):
        text = msg.data.strip()[: self.config.max_transcript_chars]
        if not text:
            return
        try:
            self.transcripts.put_nowait((text, time.time()))
        except queue.Full:
            self.get_logger().warning(f"Intent parser busy, transcript dropped: {text!r}")

    def _on_gesture(self, msg):
        if msg.state != GestureIntent.STATE_ACCEPTED:
            return
        now = time.time()
        with self.lock:
            expired = self.dialog.expire(now)
            decision = self.dialog.on_gesture(msg.gesture, msg.intent, float(msg.confidence), now)
        stamp = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
        if expired is not None:
            self._publish_decision(expired, 0.0)
        if decision is not None:
            self._publish_decision(decision, (now - stamp) * 1000 if stamp > 0 else 0.0)

    def _on_speaking(self, msg):
        with self.lock:
            self.dialog.on_speaking(bool(msg.data), time.time())

    def _on_status(self, msg):
        if not self.config.mission_id and msg.mission_id:
            self.mission_id = msg.mission_id

    def _on_environment(self, msg):
        self.noise_db = float(msg.la_eq)

    # -- worker ------------------------------------------------------------------------------------
    def _parse(self):
        try:
            while not self.stop_event.is_set():
                try:
                    text, received = self.transcripts.get(timeout=0.1)
                except queue.Empty:
                    continue
                parse = self.model.parse(text)
                now = time.time()
                with self.lock:
                    expired = self.dialog.expire(now)
                    decision = self.dialog.on_voice(text, parse.intent, parse.target, parse.confidence, now)
                latency = (now - received) * 1000
                for item in ((expired, 0.0, None), (decision, latency, parse.inference_ms)):
                    if item[0] is not None:
                        self._emit(item)
                self.get_logger().debug(f"{text!r} -> {parse.intent} {parse.target!r} "
                                        f"p={parse.confidence:.2f} {parse.inference_ms:.0f}ms")
        except Exception as exc:
            self.failure = exc
            self.stop_event.set()

    def _emit(self, item):
        while not self.stop_event.is_set():
            try:
                self.results.put(item, timeout=0.1)
                return
            except queue.Full:
                continue

    # -- outputs -----------------------------------------------------------------------------------
    def _publish(self):
        if self.failure is not None:
            raise RuntimeError(f"Intent parsing stopped: {self.failure}") from self.failure
        with self.lock:
            expired = self.dialog.expire(time.time())
        if expired is not None:
            self._publish_decision(expired, 0.0)
        for _ in range(64):
            try:
                decision, latency, parse_ms = self.results.get_nowait()
            except queue.Empty:
                break
            self._publish_decision(decision, latency, parse_ms)

    @property
    def acoustic_condition(self):
        return "noise" if self.noise_db > self.config.noise_threshold_db else "clean"

    def _publish_decision(self, d, latency_ms, parse_ms=None):
        cfg = self.config
        self.counter += 1
        msg = IntentEvent()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = "aura_intent"
        msg.event_id = f"{self.session_prefix}-{self.counter:04d}"
        msg.mission_id = self.mission_id
        msg.intent = d.intent if d.engaged else "unknown"
        msg.utterance = d.utterance
        msg.gesture = d.gesture
        msg.modality = d.modality
        msg.decision = d.decision
        msg.confidence = float(d.confidence)
        msg.accept_threshold = float(cfg.accept_threshold)
        msg.reject_threshold = float(cfg.reject_threshold)
        msg.latency_ms = float(latency_ms)
        msg.noise_db = self.noise_db
        msg.acoustic_condition = self.acoustic_condition
        msg.operator_id = cfg.operator_id
        msg.engaged = d.engaged
        msg.safety_critical = d.safety_critical
        msg.correct = d.correct
        msg.outcome = d.outcome
        self.event_pub.publish(msg)
        if d.say:
            self.speaker_pub.publish(String(data=d.say))
        self.recent.append((time.time(), latency_ms, d.engaged))
        if self.metrics is not None:
            self.metrics.row(event_id=msg.event_id, mission_id=msg.mission_id, decision=DECISION_NAMES[d.decision],
                             intent=msg.intent, target=d.target, modality=d.modality, confidence=d.confidence,
                             latency_ms=latency_ms, parse_ms=parse_ms, answer_s=d.answer_s, utterance=d.utterance,
                             gesture=d.gesture, outcome=d.outcome, correct=int(d.correct), say=d.say,
                             safety_critical=int(d.safety_critical), engaged=int(d.engaged), noise_db=msg.noise_db,
                             acoustic_condition=msg.acoustic_condition, accept_threshold=cfg.accept_threshold,
                             reject_threshold=cfg.reject_threshold, model_id=self.model_id)
        if d.decision != IGNORE:
            target = f" [{d.target}]" if d.target else ""
            text = (f"{msg.event_id} {DECISION_NAMES[d.decision]} {d.intent}{target} ({d.modality}) "
                    f"conf {d.confidence:.2f} latency {latency_ms:.0f}ms")
            # rclpy fixes the severity per call site, so each level needs its own call.
            if d.decision == REJECT:
                self.get_logger().warning(text)
            else:
                self.get_logger().info(text)

    def _diagnostics(self):
        now = time.time()
        self.recent = [r for r in self.recent if now - r[0] <= WINDOW_S]
        status = ModuleStatus(name="aura_intent: decision", hardware_id=self.model_id)
        if self.failure is not None:
            status.level, status.message = ModuleStatus.ERROR, str(self.failure)
        else:
            status.level, status.message = ModuleStatus.OK, "ready"
        latencies = [r[1] for r in self.recent if r[1] > 0]
        engaged = any(r[2] for r in self.recent)
        values = dict(rate=round(len(self.recent) * 60.0 / WINDOW_S, 2), rate_unit="/min",
                      latency_ms=round(sum(latencies) / len(latencies), 1) if latencies else 0.0,
                      accept_threshold=self.config.accept_threshold, reject_threshold=self.config.reject_threshold,
                      recognition_mode=f"llm+gesture/{self.config.confirm_mode}",
                      acoustic_condition=self.acoustic_condition,
                      engagement="engaged" if engaged else "idle", operator_id=self.config.operator_id,
                      pending=self.dialog.pending.intent if self.dialog.pending else "")
        status.keys, status.values = list(values), [str(v) for v in values.values()]
        status.header.stamp = self.get_clock().now().to_msg()
        self.diagnostics_pub.publish(status)

    def close(self):
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=5)
            if self.thread.is_alive():
                self.get_logger().warning("Parser worker did not finish within shutdown timeout")
        if self.metrics is not None:
            self.get_logger().info(f"debug: metrics summary in {self.metrics.close()}")

    def _summary(self):
        m = self.metrics
        cmds = [r for r in m.rows if r["decision"] != "IGNORE"]
        count = lambda pred: sum(1 for r in m.rows if pred(r))
        asked = count(lambda r: r["decision"] == "CONFIRM")
        yes = count(lambda r: r["outcome"] == "correct")
        no = count(lambda r: r["outcome"] == "misrecognized")
        timeouts = count(lambda r: r["say"] == "No answer, command dropped.")
        rows = [("decisions", len(m.rows), "", ""), ("commands (not IGNORE)", len(cmds), "", ""),
                ("ignored (not addressed to the robot)", len(m.rows) - len(cmds), "", "engagement gate")]
        rows += [(name, count(lambda r, n=name: r["decision"] == n), "", "") for name in ("ACCEPT", "CONFIRM", "REJECT")]
        rows += [("questions asked", asked, "", "confirm_mode " + self.config.confirm_mode),
                 ("answered yes / thumb up", yes, "", "outcome correct"),
                 ("answered no / thumb down", no, "", "outcome misrecognized"),
                 ("not answered in time", timeouts, "", f"confirm_timeout_s {self.config.confirm_timeout_s}"),
                 ("answer rate", round(100 * (yes + no) / asked, 1) if asked else None, "%", ""),
                 ("rejected below reject_threshold", count(lambda r: r["decision"] == "REJECT" and r["parse_ms"] is not None
                                                          and r["confidence"] < r["reject_threshold"]), "", ""),
                 ("safety commands executed", count(lambda r: r["decision"] == "ACCEPT" and r["safety_critical"]), "",
                  "stop, pause, cancel")]
        for modality in ("voice", "gesture", "fused"):
            rows.append((f"{modality} decisions", count(lambda r, mo=modality: r["modality"] == mo
                                                       and r["decision"] != "IGNORE"), "", ""))
        rows += stats("confidence (ACCEPT)", m.column("confidence", lambda r: r["decision"] == "ACCEPT"), unit="",
                      digits=3)
        rows += stats("decision latency", m.column("latency_ms", lambda r: r["latency_ms"]),
                      note="transcript or gesture received -> decision")
        rows += stats("model inference", m.column("parse_ms"), note="LLM parse of one transcript")
        rows += stats("answer time", m.column("answer_s"), unit="s", digits=2, note="end of question -> answer")
        return rows


def main(args=None):
    rclpy.init(args=args)
    node = None
    exit_code = 0
    try:
        node = IntentNode()
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    except Exception as exc:
        rclpy.logging.get_logger("aura_intent").error(str(exc))
        exit_code = 1
    finally:
        # A terminal SIGINT reaches both ros2 launch and its child; cleanup must finish.
        previous_handler = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            if node is not None:
                node.close()
                node.destroy_node()
            if rclpy.ok():
                rclpy.shutdown()
        finally:
            signal.signal(signal.SIGINT, previous_handler)
    return exit_code
