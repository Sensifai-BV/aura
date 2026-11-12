"""ROS2 microphone capture with inference isolated from the executor."""

import collections
import queue
import signal
import threading
import time
import wave
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.executors import ExternalShutdownException
from aura_interfaces.msg import ModuleStatus
from std_msgs.msg import Bool, String

from .asr import StreamingTranscriber
from .audio import SAMPLE_RATE, EchoGate, UtteranceSegmenter
from .echo import EchoFilter, RobotSpeech, partial_safety
from .metrics import stats
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
        self.final_times = collections.deque()
        self.final_latency_ms = collections.deque(maxlen=20)
        self.partial_count = 0
        self.echo = None
        self.echo_filter = None
        self.robot_speech = None
        self.muted_samples = 0
        self.utterance_index = 0
        self.metrics = None
        try:
            defaults = SttConfig.defaults()
            for name, default in defaults.items():
                self.declare_parameter(name, default, ParameterDescriptor(
                    read_only=True, description="Startup setting; restart STT to change it"))
            self.config = SttConfig.from_parameters({
                name: self.get_parameter(name).value for name in defaults})
            config = self.config
            self.final_publisher = self.create_publisher(String, config.transcript_topic, 10)
            self.partial_publisher = self.create_publisher(String, config.partial_topic, 10)
            self.status_publisher = self.create_publisher(ModuleStatus, config.status_topic, 10)
            if config.debug:
                from .metrics import MetricsLog
                self.metrics = MetricsLog(config.metrics_dir, "stt", [
                    "text", "words", "speech_s", "final_latency_ms", "first_partial_ms", "partials", "robot_spoke_s",
                    "forwarded", "echo_removed", "early_safety", "audio_file", "device"])
                self.metrics.summary = self._summary_rows
                self.create_timer(5.0, self.metrics.tick)  # summary survives a kill
                self.get_logger().info(f"debug: metrics to {self.metrics.path}")
            if config.speaking_topic:
                self.echo = EchoGate(config.echo_tail_s)
                self.create_subscription(Bool, config.speaking_topic, self._on_speaking, 10)
                if config.echo_mode == "filter":
                    self.robot_speech = RobotSpeech()
                    self.echo_filter = EchoFilter(self.echo, self.robot_speech, config.echo_similarity)
                    self.create_subscription(String, config.robot_text_topic, self._on_robot_text, 10)
            if config.record_audio:
                self.audio_dir = Path(config.metrics_dir).expanduser() / "audio"
                self.audio_dir.mkdir(parents=True, exist_ok=True)
                self.get_logger().info(f"record_audio: utterances to {self.audio_dir}")
            self.segmenter = UtteranceSegmenter(
                self._start_session, threshold_db=config.speech_threshold_db,
                silence_s=config.silence_s, pre_roll_s=config.pre_roll_s,
                max_utterance_s=config.max_utterance_s, tail_s=config.tail_s, record=config.record_audio)
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
                channel = (f"ch{config.input_channel}" if config.input_channel >= 0 else "average")
                source_name = (f"dev#{device.index} {device.name}; "
                               f"{device.sample_rate} Hz/{device.channels}ch, {channel} -> 16000 Hz mono")
            self.create_timer(0.02, self._publish)
            self.create_timer(1.0, self._status)
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

    def _on_speaking(self, msg):
        self.echo.speaking(bool(msg.data))
        if self.config.debug_audio:
            state = ("speaking, microphone muted" if self.config.echo_mode == "mute" else "speaking, echo filter on") \
                if msg.data else "quiet"
            self.get_logger().info(f"echo gate: robot {state} (t={time.monotonic():.2f})")

    def _on_robot_text(self, msg):
        self.robot_speech.said(msg.data, time.monotonic())

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
            self.audio.put_nowait((block, time.monotonic()))
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
                    item = self.audio.get(timeout=0.1)
                except queue.Empty:
                    continue
                if item is None:
                    self.segmenter.finish()
                    return
                block, captured_at = item
                if self.echo_filter is None and self.echo is not None and self.echo.muted(captured_at):
                    self.muted_samples += len(block)
                    block = block * 0
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
                buffer, partials, early = self.current_buffer, [], []

                def on_partial(text):
                    partials.append(time.monotonic())
                    self._emit("partial", text)
                    if self.config.partial_safety and not early:
                        # Send a stop now: the utterance may end only when the robot stops talking or
                        # walking. Robot words are removed first when the echo filter is on.
                        safety = (self.echo_filter.early_safety(text, buffer.opened_at, time.monotonic())
                                  if self.echo_filter is not None else partial_safety(text))
                        if safety:
                            early.append(safety)
                            self.final_times.append(time.time())
                            self._emit("final", safety)

                text = self.transcriber.transcribe(buffer, on_partial)
                closed_at = buffer.closed_at
                self.current_buffer = None
                forwarded, removed = text, 0
                if text and self.echo_filter is not None:
                    forwarded, removed = self.echo_filter.clean(
                        text, buffer.opened_at, closed_at if closed_at is not None else time.monotonic())
                if early:
                    forwarded = ""  # its safety words were already sent from a partial
                audio_file = self._record(buffer) if self.config.record_audio else ""
                if self.metrics is not None:
                    self._utterance_metrics(buffer, text, partials, forwarded, removed, early, audio_file)
                if forwarded:
                    if closed_at is not None:
                        self.final_latency_ms.append((time.monotonic() - closed_at) * 1000)
                    self.final_times.append(time.time())
                    self._emit("final", forwarded)
        except Exception as exc:
            self._fail(exc)

    def _record(self, buffer):
        """Save what the recogniser heard (pre-roll, speech, endpoint tail) as 16 kHz mono PCM16."""
        if not buffer.recording:
            return ""
        self.utterance_index += 1
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%f")[:-3]
        path = self.audio_dir / f"stt_{stamp}Z_{self.utterance_index:04d}.wav"
        try:
            pcm = (np.clip(np.concatenate(buffer.recording), -1, 1) * 32767).astype("<i2")
            with wave.open(str(path), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(SAMPLE_RATE)
                w.writeframes(pcm.tobytes())
        except Exception as exc:  # a debug aid must not stop recognition
            self.get_logger().warning(f"record_audio: {exc}")
            return ""
        finally:
            buffer.recording = None
        return path.name

    def _utterance_metrics(self, buffer, text, partials, forwarded="", removed=0, early=(), audio_file=""):
        now = time.monotonic()
        end = buffer.closed_at
        muted = 0.0
        if self.echo is not None and end is not None:  # robot speech inside this utterance (muted by the gate)
            muted = sum(0.1 for i in range(int((end - buffer.opened_at) * 10))
                        if self.echo.muted(buffer.opened_at + i * 0.1))
        self.metrics.row(text=text or "", words=len((text or "").split()),
                         speech_s=(end - buffer.opened_at) if end else None,
                         final_latency_ms=(now - end) * 1000 if end else None,
                         first_partial_ms=(partials[0] - buffer.opened_at) * 1000 if partials else None,
                         partials=len(partials), robot_spoke_s=muted,
                         forwarded=early[0] if early else (forwarded or ""), echo_removed=removed,
                         early_safety=int(bool(early)), audio_file=audio_file, device=str(self.transcriber.device))

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
            self.partial_count += kind != "final"
            publisher.publish(String(data=text))
            if kind == "final":
                self.get_logger().info(text)

    def _status(self):
        now = time.time()
        while self.final_times and now - self.final_times[0] > 60:
            self.final_times.popleft()
        status = ModuleStatus(name="aura_stt: recognizer", hardware_id=str(self.transcriber.device))
        if self.failure is not None:
            status.level, status.message = ModuleStatus.ERROR, str(self.failure)
        else:
            status.level, status.message = ModuleStatus.OK, "listening"
        latency = (sum(self.final_latency_ms) / len(self.final_latency_ms)) if self.final_latency_ms else 0.0
        values = dict(rate=round(len(self.final_times), 2), rate_unit="/min", latency_ms=round(latency, 1),
                      latency_setting_ms=self.config.latency_ms, captured_s=round(self.captured_samples / SAMPLE_RATE, 1),
                      peak=round(self.capture_peak, 3), device=str(self.transcriber.device),
                      echo_gate=self.config.echo_mode if self.echo is not None else "off",
                      muted_s=round(self.muted_samples / SAMPLE_RATE, 1))
        status.keys, status.values = list(values), [str(v) for v in values.values()]
        status.header.stamp = self.get_clock().now().to_msg()
        self.status_publisher.publish(status)

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
        if self.metrics is not None:
            self.get_logger().info(f"debug: metrics summary in {self.metrics.close()}")


    def _summary_rows(self):
        m = self.metrics
        overlap = [r for r in m.rows if r["robot_spoke_s"]]
        rows = [("utterances", len(m.rows), "", "speech segments decoded"),
                ("empty decodes", sum(1 for r in m.rows if not r["text"]), "", "noise that opened an utterance"),
                ("words", sum(r["words"] for r in m.rows), "", ""),
                ("echo mode", self.config.echo_mode if self.echo is not None else "off", "", ""),
                ("utterances overlapping robot speech", len(overlap), "",
                 "audio muted inside the utterance" if self.config.echo_mode == "mute" else "robot words filtered")]
        if self.echo_filter is not None:
            rows += [("dropped as robot echo", sum(1 for r in overlap if r["text"] and not r["forwarded"]), "",
                      "transcript was only the robot's own words"),
                     ("forwarded during robot speech", sum(1 for r in overlap if r["forwarded"]), "",
                      "operator words left after removing the robot's"),
                     ("robot words removed", sum(r["echo_removed"] for r in m.rows), "", "")]
        else:
            rows.append(("microphone muted (echo gate)", round(self.muted_samples / SAMPLE_RATE, 1), "s", ""))
        rows.append(("early safety finals", sum(r["early_safety"] for r in m.rows), "",
                     "stop/pause sent from a partial transcript"))
        rows.append(("latency setting", self.config.latency_ms, "ms", ""))
        rows += stats("final latency", m.column("final_latency_ms", lambda r: r["text"]),
                      note="end of speech -> final transcript")
        rows += stats("first partial", m.column("first_partial_ms"), note="speech onset -> first partial")
        rows += stats("speech duration", m.column("speech_s", lambda r: r["text"]), unit="s", digits=2)
        return rows

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
