"""ROS2 gesture recognition with inference isolated from the executor.

Only the newest frame is kept: if inference falls behind, stale frames are
dropped (and counted) rather than queued, so decisions never lag the operator.
"""

import queue
import signal
import threading
import time

import cv2
import numpy as np
import rclpy
from aura_interfaces.msg import ModuleStatus
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
from rcl_interfaces.msg import ParameterDescriptor
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import CompressedImage
from vision_msgs.msg import Detection2D, Detection2DArray, ObjectHypothesisWithPose

from aura_interfaces.msg import GestureIntent

from .config import GestureConfig, resolve_model_path
from .metrics import stats
from .decision import ACCEPTED, TemporalGate
from .detector import OnnxGestureDetector, top_command
from .labels import NONE


def stamp_seconds(stamp):
    return stamp.sec + stamp.nanosec * 1e-9


class GestureNode(Node):
    def __init__(self):
        super().__init__("aura_gesture")
        self.stop_event = threading.Event()
        self.frames = queue.Queue(maxsize=1)
        self.results = queue.Queue(maxsize=64)
        self.failure = None
        self.thread = None
        self.stats = dict(received=0, processed=0, dropped=0, stale=0, accepted=0)
        self.latency = []
        self.inference = []
        defaults = GestureConfig.defaults()
        for name, default in defaults.items():
            self.declare_parameter(name, default, ParameterDescriptor(
                read_only=True, description="Startup setting; restart the gesture node to change it"))
        self.config = config = GestureConfig.from_mapping(
            {name: self.get_parameter(name).value for name in defaults})
        model_path = resolve_model_path(config.model_path)
        self.get_logger().info(f"Loading gesture model {model_path}")
        self.detector = OnnxGestureDetector(model_path, config.num_threads,
                                            providers=config.provider_list or None)
        self.detector.warmup()
        self.model_id = config.model_id or self.detector.name
        self.gate = TemporalGate(config.window, config.min_votes, config.accept_threshold,
                                 config.threshold_map, config.votes_map, config.cooldown_s, config.rearm_frames)
        self.hypothesis_pub = self.create_publisher(GestureIntent, config.hypothesis_topic, 50)
        self.intent_pub = self.create_publisher(GestureIntent, config.intent_topic, 50)
        self.detections_pub = self.create_publisher(Detection2DArray, config.detections_topic, 10)
        self.diagnostics_pub = self.create_publisher(DiagnosticArray, "/diagnostics", 10)
        self.status_pub = self.create_publisher(ModuleStatus, config.status_topic, 10)
        self.debug_pub = (self.create_publisher(CompressedImage, config.debug_image_topic, 5)
                          if config.publish_debug_image else None)
        self.metrics = None
        self.started = time.monotonic()
        self.all_inference, self.all_latency = [], []
        if config.debug:
            from .metrics import MetricsLog
            self.metrics = MetricsLog(config.metrics_dir, "gesture", [
                "gesture", "intent", "confidence", "frame_confidence", "threshold", "votes", "window", "latency_ms",
                "inference_ms", "raw_label", "model_id", "provider"])
            self.get_logger().info(f"debug: metrics to {self.metrics.path}")
        self.create_subscription(CompressedImage, config.image_topic, self._on_image, qos_profile_sensor_data)
        self.create_timer(0.01, self._publish)
        if config.diagnostics_period_s > 0:
            self.create_timer(config.diagnostics_period_s, self._diagnostics)
        self.thread = threading.Thread(target=self._infer, name="aura-gesture", daemon=True)
        self.thread.start()
        self.get_logger().info(
            f"Gesture ready: model {self.model_id} on {self.detector.provider}; "
            f"intents on {config.intent_topic}; window {config.window}, votes {config.votes_map}, "
            f"thresholds {config.threshold_map}")

    def _on_image(self, msg):
        self.stats["received"] += 1
        try:
            self.frames.put_nowait(msg)
        except queue.Full:
            try:
                self.frames.get_nowait()
                self.stats["dropped"] += 1
            except queue.Empty:
                pass
            self.frames.put_nowait(msg)

    def _infer(self):
        cfg = self.config
        try:
            while not self.stop_event.is_set():
                try:
                    msg = self.frames.get(timeout=0.1)
                except queue.Empty:
                    continue
                captured = stamp_seconds(msg.header.stamp)
                if cfg.max_frame_age_s and time.time() - captured > cfg.max_frame_age_s:
                    self.stats["stale"] += 1
                    continue
                frame = cv2.imdecode(np.frombuffer(msg.data, np.uint8), cv2.IMREAD_COLOR)
                if frame is None:
                    self.get_logger().warning("Undecodable image dropped")
                    continue
                dets, ms = self.detector.detect(frame, cfg.min_detection_score)
                best = top_command(dets)
                gesture = best.gesture if best else NONE
                decision = self.gate.update(gesture, best.score if best else 0.0, captured)
                debug = self._debug_image(frame, dets, decision, ms) if self.debug_pub else None
                self._emit((msg.header, dets, best, decision, ms, debug))
        except Exception as exc:
            self.failure = exc
            self.stop_event.set()

    def _debug_image(self, frame, dets, decision, ms):
        """Overlay detections and the gate decision; runs on the inference thread."""
        img = frame.copy()
        for d in dets:
            color = (0, 200, 0) if d.gesture != NONE else (160, 160, 160)
            p1, p2 = tuple(map(int, d.box[:2])), tuple(map(int, d.box[2:]))
            cv2.rectangle(img, p1, p2, color, 2)
            cv2.putText(img, f"{d.label} {d.score:.2f}", (p1[0], max(20, p1[1] - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
        state = ("idle", "candidate", "ACCEPTED")[decision.state]
        lines = [f"{self.model_id}  {ms:.0f} ms",
                 f"{decision.gesture} -> {self.config.intent_map.get(decision.gesture, NONE)}  {state}",
                 f"conf {decision.confidence:.2f}  votes {decision.votes}/{decision.window}"]
        for i, t in enumerate(lines):
            y = 30 + 30 * i
            cv2.putText(img, t, (12, y), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 4)
            cv2.putText(img, t, (12, y), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                        (0, 0, 255) if decision.state == ACCEPTED else (255, 255, 255), 2)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return buf.tobytes() if ok else None

    def _emit(self, item):
        while not self.stop_event.is_set():
            try:
                self.results.put(item, timeout=0.1)
                return
            except queue.Full:
                continue

    def _intent_msg(self, header, best, decision, ms):
        msg = GestureIntent()
        msg.header = header
        msg.gesture = decision.gesture
        msg.intent = self.config.intent_map.get(decision.gesture, NONE)
        msg.raw_label = best.label if best else ""
        msg.state = decision.state
        msg.confidence = float(decision.confidence)
        msg.frame_confidence = float(decision.frame_confidence)
        msg.threshold = float(self.gate.threshold_for(decision.gesture)) if decision.gesture != NONE else 0.0
        msg.votes = decision.votes
        msg.window = decision.window
        if best:
            x1, y1, x2, y2 = best.box
            msg.bbox.center.position.x = (x1 + x2) / 2
            msg.bbox.center.position.y = (y1 + y2) / 2
            msg.bbox.size_x = x2 - x1
            msg.bbox.size_y = y2 - y1
        msg.inference_ms = float(ms)
        msg.latency_ms = float((time.time() - stamp_seconds(header.stamp)) * 1000)
        msg.model_id = self.model_id
        return msg

    def _publish(self):
        if self.failure is not None:
            raise RuntimeError(f"Gesture recognition stopped: {self.failure}") from self.failure
        for _ in range(64):
            try:
                header, dets, best, decision, ms, debug = self.results.get_nowait()
            except queue.Empty:
                break
            self.stats["processed"] += 1
            arr = Detection2DArray(header=header)
            for d in dets:
                det = Detection2D(header=header)
                hyp = ObjectHypothesisWithPose()
                hyp.hypothesis.class_id = d.label
                hyp.hypothesis.score = d.score
                det.results.append(hyp)
                det.bbox.center.position.x = (d.box[0] + d.box[2]) / 2
                det.bbox.center.position.y = (d.box[1] + d.box[3]) / 2
                det.bbox.size_x = d.box[2] - d.box[0]
                det.bbox.size_y = d.box[3] - d.box[1]
                arr.detections.append(det)
            self.detections_pub.publish(arr)
            if debug is not None:
                self.debug_pub.publish(CompressedImage(header=header, format="jpeg", data=debug))
            msg = self._intent_msg(header, best, decision, ms)
            self.hypothesis_pub.publish(msg)
            self.inference.append(ms)
            self.latency.append(msg.latency_ms)
            if self.metrics is not None:
                self.all_inference.append(ms)
                self.all_latency.append(msg.latency_ms)
            if decision.state == ACCEPTED:
                self.stats["accepted"] += 1
                self.intent_pub.publish(msg)
                if self.metrics is not None:
                    self.metrics.row(gesture=msg.gesture, intent=msg.intent, confidence=msg.confidence,
                                     frame_confidence=msg.frame_confidence, threshold=msg.threshold, votes=msg.votes,
                                     window=msg.window, latency_ms=msg.latency_ms, inference_ms=msg.inference_ms,
                                     raw_label=msg.raw_label, model_id=self.model_id, provider=self.detector.provider)
                self.get_logger().info(
                    f"Intent {msg.intent} ({msg.gesture}) conf {msg.confidence:.2f} "
                    f"votes {msg.votes}/{msg.window} latency {msg.latency_ms:.0f}ms")

    def _diagnostics(self):
        inf, lat = self.inference, self.latency
        self.inference, self.latency = [], []
        status = DiagnosticStatus(name="aura_gesture: recognizer", hardware_id=self.model_id)
        processed = len(inf)
        fps = processed / max(self.config.diagnostics_period_s, 1e-6)
        if self.failure is not None:
            status.level, status.message = DiagnosticStatus.ERROR, str(self.failure)
        elif processed == 0:
            status.level, status.message = DiagnosticStatus.WARN, "no frames processed"
        else:
            status.level, status.message = DiagnosticStatus.OK, f"{fps:.1f} fps"
        values = dict(self.stats, fps=round(fps, 2), provider=self.detector.provider)
        if inf:
            values.update(inference_p50_ms=round(float(np.median(inf)), 2),
                          latency_p50_ms=round(float(np.median(lat)), 2),
                          latency_p95_ms=round(float(np.percentile(lat, 95)), 2))
        status.values = [KeyValue(key=k, value=str(v)) for k, v in values.items()]
        metrics = dict(rate=f"{fps:.2f}", rate_unit="Hz", latency_ms=f"{float(np.median(lat)) if lat else 0.0:.1f}")
        metrics.update({kv.key: kv.value for kv in status.values})
        level = status.level[0] if isinstance(status.level, (bytes, bytearray)) else int(status.level)
        module = ModuleStatus(level=level, message=status.message, name="aura_gesture: recognizer",
                              hardware_id=self.model_id, keys=list(metrics), values=[str(v) for v in metrics.values()])
        module.header.stamp = self.get_clock().now().to_msg()
        self.status_pub.publish(module)
        arr = DiagnosticArray(status=[status])
        arr.header.stamp = self.get_clock().now().to_msg()
        self.diagnostics_pub.publish(arr)

    def close(self):
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=5)
            if self.thread.is_alive():
                self.get_logger().warning("Inference worker did not finish within shutdown timeout")
        if self.metrics is not None:
            m, s = self.metrics, self.stats
            run_s = time.monotonic() - self.started
            rows = [("run time", round(run_s, 1), "s", ""),
                    ("frames received", s["received"], "", ""),
                    ("frames processed", s["processed"], "", ""),
                    ("frames processed per second", round(s["processed"] / run_s, 2) if run_s else None, "fps", ""),
                    ("frames dropped (busy)", s["dropped"], "", "newest frame wins"),
                    ("frames dropped (stale)", s["stale"], "", f"older than max_frame_age_s {self.config.max_frame_age_s}"),
                    ("provider", self.detector.provider, "", self.model_id),
                    ("accepted gestures", len(m.rows), "", "")]
            for g in self.config.gestures:
                conf = m.column("confidence", lambda r, g=g: r["gesture"] == g)
                rows.append((f"accepted {g}", len(conf), "",
                             f"mean confidence {sum(conf) / len(conf):.2f}" if conf else ""))
            rows += stats("inference", self.all_inference, note="per frame")
            rows += stats("frame latency", self.all_latency, note="capture -> result, per frame")
            rows += stats("accepted gesture latency", m.column("latency_ms"), note="capture of the deciding frame -> intent")
            self.get_logger().info(f"debug: metrics summary in {m.close(rows)}")


def main(args=None):
    rclpy.init(args=args)
    node = None
    exit_code = 0
    try:
        node = GestureNode()
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    except Exception as exc:
        rclpy.logging.get_logger("aura_gesture").error(str(exc))
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
