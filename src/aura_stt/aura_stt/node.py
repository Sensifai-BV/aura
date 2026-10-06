"""ROS2 microphone capture with inference isolated from the executor."""

import queue
import signal
import threading
import time

import rclpy
from rclpy.node import Node
from rclpy.executors import ExternalShutdownException
from std_msgs.msg import String

from .asr import StreamingTranscriber
from .audio import SAMPLE_RATE, UtteranceSegmenter
from .config import SttConfig
from .replay import WaveReplay
from .microphone import MicrophoneCapture
from rcl_interfaces.msg import ParameterDescriptor


class SttNode(Node):
    def __init__(self):
        super().__init__("aura_stt")
        self.stop_event = threading.Event()
        self.audio = queue.Queue(maxsize=100)  # at most two seconds of capture
        self.sessions = queue.Queue(maxsize=4)
        self.results = queue.Queue(maxsize=256)
        self.failure = None
        self.stream = None
        self.threads = []
        self.current_buffer = None
        self.segmenter = None
        self.captured_samples = 0
        self.capture_peak = 0.0
        self.silence_warned = False
        self.last_audio_diagnostic = time.monotonic()
        self.last_xruns = 0
        self.last_dropped_blocks = 0
        try:
            defaults = SttConfig.defaults()
            for name, default in defaults.items():
                self.declare_parameter(name, default, ParameterDescriptor(
                    read_only=True, description="Startup setting; restart STT to change it"))
            self.config = SttConfig.from_mapping({
                name: self.get_parameter(name).value for name in defaults})
            config = self.config
            self.final_publisher = self.create_publisher(String, config.transcript_topic, 10)
            self.partial_publisher = self.create_publisher(String, config.partial_topic, 10)
            self.segmenter = UtteranceSegmenter(
                self._start_session, threshold_db=config.speech_threshold_db,
                silence_s=config.silence_s, pre_roll_s=config.pre_roll_s,
                max_utterance_s=config.max_utterance_s, tail_s=config.tail_s)
            self.get_logger().info("Loading local Nemotron model and warming up")
            self.transcriber = StreamingTranscriber(
                config.model_dir, config.device, config.latency_ms, config.num_threads)
            self.transcriber.warmup()
            if config.audio_file:
                self.stream = WaveReplay(config.audio_file, self._capture,
                                         self._capture_finished, self._fail)
                source_name = f"recording {config.audio_file} (real-time replay)"
            else:
                self.stream = MicrophoneCapture(config, self._ingest_audio,
                                                self._capture_finished, self._fail)
                device = self.stream.device
                source_name = (f"dev#{device.index} {device.name}; "
                               f"{device.sample_rate} Hz/{device.channels}ch -> 16000 Hz mono")
            self.create_timer(0.02, self._publish)
            for target, name in ((self._segment_audio, "aura-audio"), (self._recognize, "aura-asr")):
                thread = threading.Thread(target=target, name=name, daemon=True)
                self.threads.append(thread)
                thread.start()
            self.stream.start()
            self.last_audio_diagnostic = time.monotonic()
            self.get_logger().info(
                f"Audio ready: {source_name}; "
                f"ASR on {self.transcriber.device}; latency {config.latency_ms}ms; "
                f"final text on {config.transcript_topic}")
        except Exception:
            self.close()
            super().destroy_node()
            raise

    def _fail(self, exc):
        if self.failure is None:
            self.failure = exc
        self.stop_event.set()
        if self.current_buffer is not None:
            self.current_buffer.close()

    def _capture(self, indata, frames, time_info, status):
        if self.stop_event.is_set():
            return
        if status:
            self._fail(RuntimeError(f"Microphone capture failed: {status}"))
            return
        self._ingest_audio(indata[:, 0].copy())

    def _ingest_audio(self, block):
        if self.stop_event.is_set():
            return
        try:
            self.captured_samples += len(block)
            self.capture_peak = max(self.capture_peak, float(abs(block).max()))
            self.audio.put_nowait(block)
        except queue.Full:
            self._fail(BufferError("Microphone queue full; inference cannot keep up"))

    def _capture_finished(self):
        if not self.stop_event.is_set():
            if self.config.audio_file:
                try:
                    self.audio.put_nowait(None)
                except queue.Full:
                    self._fail(BufferError("Audio queue full at end of recording"))
            else:
                self._fail(RuntimeError("Microphone stream stopped unexpectedly"))

    def _start_session(self, buffer):
        try:
            self.sessions.put_nowait(buffer)
        except queue.Full:
            buffer.close()
            raise BufferError("Utterance queue full; inference cannot keep up")

    def _segment_audio(self):
        try:
            while not self.stop_event.is_set():
                try:
                    block = self.audio.get(timeout=0.1)
                except queue.Empty:
                    continue
                if block is None:
                    self.segmenter.finish()
                    return
                self.segmenter.push(block)
        except Exception as exc:
            self._fail(exc)
        finally:
            # Cancellation unblocks inference even when an utterance is incomplete.
            if self.segmenter.active is not None:
                self.segmenter.active.close()

    def _emit(self, kind, text):
        while not self.stop_event.is_set():
            try:
                self.results.put((kind, text), timeout=0.1)
                return
            except queue.Full:
                continue

    def _recognize(self):
        try:
            while not self.stop_event.is_set():
                try:
                    self.current_buffer = self.sessions.get(timeout=0.1)
                except queue.Empty:
                    continue
                if self.stop_event.is_set():
                    self.current_buffer.close()
                    break
                text = self.transcriber.transcribe(
                    self.current_buffer, lambda text: self._emit("partial", text))
                self.current_buffer = None
                if text:
                    self._emit("final", text)
        except Exception as exc:
            self._fail(exc)

    def _publish(self):
        if self.failure is not None:
            raise RuntimeError(f"STT stopped: {self.failure}") from self.failure
        now = time.monotonic()
        if now - self.last_audio_diagnostic >= 5:
            self.last_audio_diagnostic = now
            if self.config.debug_audio:
                self.get_logger().info(
                    f"Audio captured: {self.captured_samples / SAMPLE_RATE:.2f}s; "
                    f"peak amplitude: {self.capture_peak:.6f}")
            if isinstance(self.stream, MicrophoneCapture):
                capture = self.stream
                if self.config.debug_audio:
                    self.get_logger().info(
                        f"Native capture: {capture.raw_samples / capture.device.sample_rate:.2f}s; "
                        f"peak: {capture.raw_peak:.6f}; xruns: {capture.xruns}; "
                        f"dropped blocks: {capture.dropped_blocks}")
                if capture.xruns != self.last_xruns or capture.dropped_blocks != self.last_dropped_blocks:
                    self.get_logger().warning(
                        f"Microphone discontinuities: xruns={capture.xruns}, "
                        f"dropped capture blocks={capture.dropped_blocks}. "
                        "Increase input_latency or reduce CPU load if this repeats.")
                    self.last_xruns = capture.xruns
                    self.last_dropped_blocks = capture.dropped_blocks
            if self.captured_samples >= 5 * SAMPLE_RATE and self.capture_peak == 0 and not self.silence_warned:
                self.silence_warned = True
                self.get_logger().warning(
                    "Capture is returning digital silence. Check the input device, "
                    "microphone permission and VM audio passthrough.")
        for _ in range(64):
            try:
                kind, text = self.results.get_nowait()
            except queue.Empty:
                break
            publisher = self.final_publisher if kind == "final" else self.partial_publisher
            publisher.publish(String(data=text))
            if kind == "final":
                self.get_logger().info(text)

    def close(self):
        self.stop_event.set()
        if self.stream is not None:
            try:
                self.stream.stop()
            finally:
                self.stream.close()
        if self.current_buffer is not None:
            self.current_buffer.close()
        for thread in self.threads:
            thread.join(timeout=5)
        for thread in self.threads:
            if thread.is_alive():
                self.get_logger().warning(f"Worker {thread.name} did not finish within shutdown timeout")


def main(args=None):
    rclpy.init(args=args)
    node = None
    exit_code = 0
    try:
        node = SttNode()
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    except Exception as exc:
        rclpy.logging.get_logger("aura_stt").error(str(exc))
        exit_code = 1
    finally:
        # A terminal SIGINT reaches both ros2 launch and its child. Launch can
        # forward another SIGINT while workers are joining; cleanup must finish.
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
