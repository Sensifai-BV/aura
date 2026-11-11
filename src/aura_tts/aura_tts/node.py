"""ROS 2 speaker: text on /aura/speaker/say -> speech on the robot's loudspeaker.

aura_intent publishes its confirmation questions and acknowledgements as
std_msgs/String. Synthesis and playback run on their own threads (speaker.py);
the executor only queues requests and publishes state from a timer, so a long
utterance never blocks ROS callbacks. /aura/speaker/speaking (std_msgs/Bool)
lets the STT ignore the robot's own voice.
"""

import queue
import signal
import time

import rclpy
from aura_interfaces.msg import ModuleStatus
from rcl_interfaces.msg import ParameterDescriptor
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile
from std_msgs.msg import Bool, String

from .config import TtsConfig, resolve_model_dir
from .engine import PhraseCache, SherpaTts
from .normalize import normalize
from .metrics import stats
from .player import make_player
from .speaker import Speaker

WINDOW_S = 60.0


class TtsNode(Node):
    def __init__(self):
        super().__init__("aura_tts")
        defaults = TtsConfig.defaults()
        for name, default in defaults.items():
            self.declare_parameter(name, default, ParameterDescriptor(
                read_only=True, description="Startup setting; restart the speaker node to change it"))
        self.config = config = TtsConfig.from_mapping(
            {name: self.get_parameter(name).value for name in defaults})
        model_dir = resolve_model_dir(config.model_dir)
        self.get_logger().info(f"Loading TTS model {model_dir}")
        t = time.perf_counter()
        self.engine = SherpaTts(model_dir, config.num_threads, config.voice, config.speed, config.num_steps,
                                config.provider)
        self.engine.warmup()
        load_s = time.perf_counter() - t
        self.model_id = config.model_id or self.engine.name
        prepare = normalize if config.normalize_text else (lambda text: text)
        self.cache = PhraseCache(self.engine, config.cache_phrases, prepare)
        self.events = queue.SimpleQueue()
        self.recent = []
        self.failure = None
        self.speaking = False
        self.speaker = Speaker(self.engine, make_player(config.output_device, config.gain), self.cache,
                               config.normalize_text, config.max_chars,
                               on_speaking=lambda s: self.events.put(("speaking", s)),
                               on_done=lambda u: self.events.put(("done", u)))
        latched = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.speaking_pub = self.create_publisher(Bool, config.speaking_topic, latched)
        self.status_pub = self.create_publisher(ModuleStatus, config.status_topic, 10)
        self.speaking_pub.publish(Bool(data=False))
        self.metrics = None
        if config.debug:
            from .metrics import MetricsLog
            self.metrics = MetricsLog(config.metrics_dir, "tts", [
                "text", "chars", "cached", "first_audio_ms", "synth_ms", "audio_s", "interrupted", "error", "model_id"])
            self.get_logger().info(f"debug: metrics to {self.metrics.path}")
        self.create_subscription(String, config.say_topic, self._on_say, 10)
        self.create_timer(0.01, self._publish)
        if config.diagnostics_period_s > 0:
            self.create_timer(config.diagnostics_period_s, self._diagnostics)
        self.get_logger().info(
            f"Speaker ready: {self.engine.family} model {self.model_id}, voice {config.voice}, "
            f"{config.num_threads} threads, loaded in {load_s:.1f}s, {len(self.cache.audio)} cached phrases "
            f"in {self.cache.build_s:.1f}s; listening on {config.say_topic}")

    def _on_say(self, msg):
        if self.speaker.say(msg.data):
            self.get_logger().debug(f"say: {msg.data!r}")

    def _publish(self):
        while True:
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                return
            if kind == "speaking":
                if value != self.speaking:
                    self.speaking = value
                    self.speaking_pub.publish(Bool(data=value))
                continue
            u = value
            self.recent.append((time.time(), u))
            if self.metrics is not None:
                self.metrics.row(text=u.text, chars=len(u.text), cached=int(u.cached), first_audio_ms=u.first_audio_ms,
                                 synth_ms=u.synth_ms, audio_s=u.audio_s, interrupted=int(u.interrupted),
                                 error=u.error, model_id=self.model_id)
            if u.error:
                self.failure = u.error
                self.get_logger().error(f"Speech failed for {u.text!r}: {u.error}")
            else:
                self.failure = None
                self.get_logger().info(
                    f"said {u.text!r}: first audio {u.first_audio_ms:.0f}ms, synth {u.synth_ms:.0f}ms"
                    f"{', cached' if u.cached else ''}{', interrupted' if u.interrupted else ''}")

    def _diagnostics(self):
        now = time.time()
        self.recent = [r for r in self.recent if now - r[0] <= WINDOW_S]
        status = ModuleStatus(name="aura_tts: speaker", hardware_id=self.model_id)
        if self.failure is not None:
            status.level, status.message = ModuleStatus.ERROR, self.failure
        else:
            status.level, status.message = ModuleStatus.OK, "speaking" if self.speaking else "ready"
        spoken = [u for _, u in self.recent if not u.error and u.first_audio_ms > 0]
        synthesized = [u for u in spoken if not u.cached]
        values = dict(rate=round(len(self.recent) * 60.0 / WINDOW_S, 2), rate_unit="/min",
                      first_audio_ms=round(sum(u.first_audio_ms for u in spoken) / len(spoken), 1) if spoken else 0.0,
                      synth_ms=round(sum(u.synth_ms for u in synthesized) / len(synthesized), 1) if synthesized else 0.0,
                      interrupted=sum(u.interrupted for _, u in self.recent),
                      model_family=self.engine.family, voice=self.config.voice, speaking=self.speaking)
        status.keys, status.values = list(values), [str(v) for v in values.values()]
        status.header.stamp = self.get_clock().now().to_msg()
        self.status_pub.publish(status)

    def close(self):
        self.speaker.close()
        self.speaker.player.close()
        if self.metrics is not None:
            m = self.metrics
            played = lambda r: not r["error"] and r["first_audio_ms"] > 0
            rows = [("prompts", len(m.rows), "", ""),
                    ("cached prompts", sum(r["cached"] for r in m.rows), "", "no synthesis delay"),
                    ("interrupted by a newer prompt", sum(r["interrupted"] for r in m.rows), "", ""),
                    ("errors", sum(1 for r in m.rows if r["error"]), "", ""),
                    ("audio played", round(sum(r["audio_s"] for r in m.rows), 1), "s", "")]
            rows += stats("first audio", m.column("first_audio_ms", played), note="prompt received -> playback start")
            rows += stats("first audio (synthesised)", m.column("first_audio_ms", lambda r: played(r) and not r["cached"]))
            rows += stats("first audio (cached)", m.column("first_audio_ms", lambda r: played(r) and r["cached"]))
            rows += stats("synthesis", m.column("synth_ms", lambda r: not r["cached"]), note="all sentences of a prompt")
            path = m.close(rows)
            self.get_logger().info(f"debug: metrics summary in {path}")


def main(args=None):
    rclpy.init(args=args)
    node = None
    exit_code = 0
    try:
        node = TtsNode()
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    except Exception as exc:
        rclpy.logging.get_logger("aura_tts").error(str(exc))
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
