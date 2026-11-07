"""Camera source for aura_gesture: V4L2/AVFoundation device or MJPEG URL.

Publishes ``sensor_msgs/CompressedImage`` (JPEG). MJPEG frames are forwarded
without re-encoding. When the source sends ``X-Aura-Label`` part headers (the
guided capture tool does), the prompt is published on the annotation topic as
JSON so end-to-end tests can score against ground truth.
"""

import json
import signal
import threading
import time

import cv2
import rclpy
from builtin_interfaces.msg import Time
from rcl_interfaces.msg import ParameterDescriptor
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from sensor_msgs.msg import CompressedImage
from std_msgs.msg import String

from .config import CameraConfig
from .mjpeg import MjpegStream


def to_time(seconds):
    sec = int(seconds)
    return Time(sec=sec, nanosec=int((seconds - sec) * 1e9))


class CameraNode(Node):
    def __init__(self):
        super().__init__("aura_camera")
        defaults = CameraConfig.defaults()
        for name, default in defaults.items():
            self.declare_parameter(name, default, ParameterDescriptor(
                read_only=True, description="Startup setting; restart the camera to change it"))
        self.config = CameraConfig.from_mapping({name: self.get_parameter(name).value for name in defaults})
        self.image_pub = self.create_publisher(CompressedImage, self.config.image_topic, 5)
        self.annotation_pub = self.create_publisher(String, self.config.annotation_topic, 50)
        self.stop_event = threading.Event()
        self.frames = 0
        self.failure = None
        self.thread = threading.Thread(target=self._run, name="aura-camera", daemon=True)
        self.thread.start()
        self.create_timer(5.0, self._report)
        self.last_report = (time.monotonic(), 0)
        self.get_logger().info(f"Camera source {self.config.source} -> {self.config.image_topic}")

    def _publish(self, jpeg, stamp, headers=None):
        msg = CompressedImage()
        msg.header.stamp = to_time(stamp)
        msg.header.frame_id = self.config.frame_id
        msg.format = "jpeg"
        msg.data = jpeg
        self.image_pub.publish(msg)
        self.frames += 1
        if headers and "x-aura-label" in headers:
            tags = {k[len("x-aura-"):]: v for k, v in headers.items() if k.startswith("x-aura-")}
            self.annotation_pub.publish(String(data=json.dumps(dict(tags, stamp=stamp))))

    def _run_mjpeg(self):
        stream = MjpegStream(self.config.source).open()
        try:
            while not self.stop_event.is_set():
                jpeg, headers = stream.read()
                stamp = float(headers["x-aura-stamp"]) if "x-aura-stamp" in headers else time.time()
                self._publish(jpeg, stamp, headers)
        finally:
            stream.close()

    def _run_device(self):
        src = int(self.config.source) if self.config.source.isdigit() else self.config.source
        cap = cv2.VideoCapture(src)
        if not cap.isOpened():
            raise RuntimeError(f"cannot open camera {self.config.source}")
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.height)
        cap.set(cv2.CAP_PROP_FPS, self.config.fps)
        quality = [cv2.IMWRITE_JPEG_QUALITY, self.config.jpeg_quality]
        try:
            while not self.stop_event.is_set():
                ok, frame = cap.read()
                stamp = time.time()
                if not ok:
                    raise ConnectionError("camera returned no frame")
                ok, buf = cv2.imencode(".jpg", frame, quality)
                if ok:
                    self._publish(buf.tobytes(), stamp)
        finally:
            cap.release()

    def _run(self):
        run = self._run_mjpeg if self.config.source.startswith(("http://", "https://")) else self._run_device
        while not self.stop_event.is_set():
            try:
                run()
            except Exception as exc:  # noqa: BLE001 - keep retrying the source
                if self.stop_event.is_set():
                    break
                self.get_logger().warning(f"Camera source error: {exc}; retrying in {self.config.reconnect_s}s")
                self.stop_event.wait(self.config.reconnect_s)

    def _report(self):
        now = time.monotonic()
        t0, n0 = self.last_report
        self.last_report = (now, self.frames)
        fps = (self.frames - n0) / max(now - t0, 1e-6)
        if fps == 0:
            self.get_logger().warning("No camera frames in the last period")
        else:
            self.get_logger().debug(f"Camera {fps:.1f} fps")

    def close(self):
        self.stop_event.set()
        self.thread.join(timeout=5)


def main(args=None):
    rclpy.init(args=args)
    node = None
    exit_code = 0
    try:
        node = CameraNode()
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    except Exception as exc:
        rclpy.logging.get_logger("aura_camera").error(str(exc))
        exit_code = 1
    finally:
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
