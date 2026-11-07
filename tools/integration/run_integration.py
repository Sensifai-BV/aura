"""Full-stack AURA integration test, run inside the ROS 2 VM.

Starts the real nodes (aura_stt, aura_intent, aura_tts, aura_camera, aura_gesture) as separate
processes, plus ws_bridge.py. The test plays the operator and the robot platform from outside:

  operator speech -> synthesized with Supertonic (a different voice from the robot's) + optional
                     plant noise -> websocket /mic -> bridge -> aura_stt
  operator camera -> JPEG frames -> websocket /camera -> bridge -> MJPEG -> aura_camera -> gesture
  robot platform  -> /artaban/status, /artaban/environment published by this script

and records every ROS 2 output (transcripts, intent events, speaker text, speaking state, gesture
topics, diagnostics) to judge each scenario. Nothing in the nodes is mocked.

    source /opt/ros/lyrical/setup.bash; source ~/aura_venv/bin/activate
    source ~/aura_ws/install/setup.bash
    python tools/integration/run_integration.py --out runs/it1 [--gesture-images DIR] [--only s2,s3]

--gesture-images expects DIR/<thumb_up|thumb_down|fist|palm|point>/*.jpg (HaGRID style crops).
"""

import argparse
import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

import cv2
import numpy as np
import rclpy
import soxr
from aura_interfaces.msg import ModuleStatus
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile
from sensor_msgs.msg import CompressedImage
from std_msgs.msg import Bool, String
from websockets.sync.client import connect

from aura_interfaces.msg import ArtabanStatus, EnvironmentReading, GestureIntent, IntentEvent

RATE, BLOCK = 16000, 320
DECISIONS = {0: "ACCEPT", 1: "CONFIRM", 2: "REJECT", 3: "IGNORE"}
ROOT = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------- process control
class Procs:
    def __init__(self, out):
        self.out, self.items = Path(out), {}

    def start(self, name, cmd, env=None):
        log = open(self.out / f"{name}.log", "w")
        self.items[name] = (subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                                             env={**os.environ, **(env or {})}), log)

    def alive(self, name):
        return self.items[name][0].poll() is None

    def wait_log(self, name, pattern, timeout):
        end, path = time.monotonic() + timeout, self.out / f"{name}.log"
        while time.monotonic() < end:
            if not self.alive(name):
                raise RuntimeError(f"{name} exited early:\n{path.read_text()[-1500:]}")
            if re.search(pattern, path.read_text(errors="replace")):
                return
            time.sleep(0.5)
        raise TimeoutError(f"{name}: '{pattern}' not seen in {timeout}s:\n{path.read_text()[-1500:]}")

    def stop_all(self):
        codes = {}
        for name, (proc, log) in reversed(list(self.items.items())):
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGINT)
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
            codes[name] = proc.returncode
            log.close()
        return codes


# --------------------------------------------------------------------------- ROS observer
class Observer(Node):
    def __init__(self):
        super().__init__("aura_integration_test")
        self.lock = threading.Lock()
        self.log = []  # (monotonic, topic, dict)
        self.frames = 0
        self.diag = {}
        latched = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
        sub = self.create_subscription
        sub(String, "/aura_transcript", lambda m: self.add("transcript", text=m.data), 20)
        sub(String, "/aura_partial_transcript", lambda m: self.add("partial", text=m.data), 100)
        sub(String, "/aura/speaker/say", lambda m: self.add("say", text=m.data), 20)
        sub(Bool, "/aura/speaker/speaking", lambda m: self.add("speaking", value=m.data), latched)
        sub(IntentEvent, "/aura/intent/event", self.on_event, 20)
        sub(GestureIntent, "/aura/gesture/intent", lambda m: self.add(
            "gesture_intent", gesture=m.gesture, intent=m.intent, confidence=m.confidence, votes=m.votes,
            latency_ms=m.latency_ms), 20)
        sub(GestureIntent, "/aura/gesture/hypothesis", lambda m: self.add(
            "hypothesis", gesture=m.gesture, raw=m.raw_label, state=m.state, conf=m.frame_confidence,
            inference_ms=m.inference_ms, latency_ms=m.latency_ms), 100)
        sub(CompressedImage, "/aura/camera/image_raw/compressed", self.on_frame, 10)
        for topic in ("/aura/intent/status", "/aura/speaker/status"):
            sub(ModuleStatus, topic, lambda m, t=topic: self.on_diag(t, m), 10)
        self.status_pub = self.create_publisher(ArtabanStatus, "/artaban/status", 10)
        self.env_pub = self.create_publisher(EnvironmentReading, "/artaban/environment", 10)

    def add(self, kind, **fields):
        with self.lock:
            self.log.append((time.monotonic(), kind, fields))

    def on_event(self, m):
        self.add("event", event_id=m.event_id, mission_id=m.mission_id, intent=m.intent, utterance=m.utterance,
                 gesture=m.gesture, modality=m.modality, decision=DECISIONS[m.decision], confidence=m.confidence,
                 latency_ms=m.latency_ms, noise_db=m.noise_db, condition=m.acoustic_condition,
                 engaged=m.engaged, safety_critical=m.safety_critical, correct=m.correct, outcome=m.outcome,
                 operator_id=m.operator_id)

    def on_frame(self, m):
        self.frames += 1

    def on_diag(self, topic, m):
        self.diag[topic] = dict(zip(m.keys, m.values)) | {"level": m.level, "message": m.message}

    def mark(self):
        with self.lock:
            return len(self.log)

    def since(self, mark, kind=None):
        with self.lock:
            return [(t, f) for t, k, f in self.log[mark:] if kind is None or k == kind]

    def wait(self, pred, kind, mark, timeout):
        """Poll for the first record of `kind` after `mark` satisfying pred."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            for t, f in self.since(mark, kind):
                if pred(f):
                    return t, f
            time.sleep(0.05)
        return None, None

    def platform(self, mission="M-IT-001", noise=62.0):
        s = ArtabanStatus()
        s.header.stamp = self.get_clock().now().to_msg()
        s.mission_id, s.mission_state, s.operation_mode = mission, "navigating", "supervised"
        s.safety_state = "nominal"
        self.status_pub.publish(s)
        e = EnvironmentReading()
        e.header.stamp = s.header.stamp
        e.la_eq = e.la_max = float(noise)
        e.noise_source = "ambient"
        self.env_pub.publish(e)


# --------------------------------------------------------------------------- operator simulators
class Operator:
    """Voice and camera as seen by the robot: always-on streams over websockets."""

    def __init__(self, url, tts):
        self.url, self.tts = url, tts
        self.audio = queue.Queue()
        self.frames = None  # list of JPEG bytes currently shown to the camera
        self.stop = threading.Event()
        self.sent_audio_s = 0.0
        self.sent_frames = 0
        self.ambient_db = -66.0
        self.rng = np.random.default_rng(7)
        for target in (self._mic, self._cam):
            threading.Thread(target=target, daemon=True).start()

    # ---- microphone
    def _mic(self):
        with connect(f"{self.url}/mic", max_size=None) as ws:
            deadline = time.monotonic()
            while not self.stop.is_set():
                try:
                    block = self.audio.get_nowait()
                except queue.Empty:
                    block = self.rng.standard_normal(BLOCK).astype(np.float32) * 10 ** (self.ambient_db / 20)
                ws.send(np.clip(block * 32767, -32768, 32767).astype("<i2").tobytes())
                self.sent_audio_s += BLOCK / RATE
                deadline += BLOCK / RATE
                time.sleep(max(0, deadline - time.monotonic()))

    def synth(self, text, voice=3):
        self.tts.voice = voice
        audio = np.asarray(self.tts.synthesize(text), dtype=np.float32)
        audio = soxr.resample(audio, self.tts.sample_rate, RATE)
        return audio / max(1e-6, float(np.abs(audio).max())) * 0.5  # about -6 dBFS peak

    def speak(self, text, noise_snr_db=None, tail_s=1.6, voice=3):
        """Queue speech; returns monotonic time at which the speech ends."""
        audio = self.synth(text, voice)
        if noise_snr_db is not None:
            audio = audio + self.plant_noise(len(audio), audio, noise_snr_db)
        pad = np.zeros(int(0.2 * RATE), dtype=np.float32)
        pcm = np.concatenate((pad, audio, np.zeros(int(tail_s * RATE), dtype=np.float32)))
        if noise_snr_db is not None:  # noise continues through the silence after the phrase
            pcm[len(pad) + len(audio):] += self.plant_noise(len(pcm) - len(pad) - len(audio), audio, noise_snr_db)
        backlog = self.audio.qsize() * BLOCK / RATE
        pcm = np.pad(pcm, (0, -len(pcm) % BLOCK))
        for i in range(0, len(pcm), BLOCK):
            self.audio.put(pcm[i:i + BLOCK])
        return time.monotonic() + backlog + (len(pad) + len(audio)) / RATE

    def plant_noise(self, n, speech, snr_db):
        """Low-pass shaped broadband noise plus a 120 Hz machinery hum at the requested SNR."""
        white = self.rng.standard_normal(n + 400).astype(np.float32)
        noise = np.convolve(white, np.ones(8) / 8, mode="valid")[:n]
        noise += 0.6 * np.sin(2 * np.pi * 120 * np.arange(n) / RATE).astype(np.float32)
        rms = lambda x: float(np.sqrt(np.mean(np.square(x))) + 1e-9)
        return noise * (rms(speech) / rms(noise)) * 10 ** (-snr_db / 20)

    def wait_idle(self):
        while not self.audio.empty():
            time.sleep(0.05)

    # ---- camera
    def show(self, frames):
        self.frames = frames

    def _cam(self):
        with connect(f"{self.url}/camera", max_size=None) as ws:
            i, deadline = 0, time.monotonic()
            while not self.stop.is_set():
                frames = self.frames
                if frames:
                    ws.send(frames[i % len(frames)])
                    self.sent_frames += 1
                    i += 1
                deadline += 1 / 15
                time.sleep(max(0, deadline - time.monotonic()))

    @staticmethod
    def jpeg(img, quality=85):
        return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])[1].tobytes()

    def empty_scene(self, n=30, w=640, h=480):
        """Plain room with moving sensor noise, no hands."""
        rng = np.random.default_rng(3)
        base = np.full((h, w, 3), 90, np.uint8)
        cv2.rectangle(base, (60, 300), (580, 480), (60, 70, 80), -1)
        return [self.jpeg(np.clip(base + rng.normal(0, 4, base.shape), 0, 255).astype(np.uint8)) for _ in range(n)]



def prime_orion(obs, peer, timeout=120):
    """Let Orion-LD create its DDS readers for the module status topics while the machine is idle.

    Orion resolves a topic's type from the first publisher it discovers; under the CPU load of the model
    start-up that lookup silently fails and is not retried, so the status entities stay "uninitialized".
    Publishes an honest "starting" status until Orion has stored one for every module, then stops.
    """
    import urllib.request
    mods = ("stt", "intent", "gesture", "speaker")
    pubs = {m: obs.create_publisher(ModuleStatus, f"/aura/{m}/status", 10) for m in mods}
    pending, end = set(mods), time.time() + timeout
    while pending and time.time() < end:
        for m, pub in pubs.items():
            msg = ModuleStatus(level=ModuleStatus.WARN, name=f"aura_{m}", message="starting",
                               keys=["rate", "rate_unit"], values=["0", "/min"])
            msg.header.stamp = obs.get_clock().now().to_msg()
            pub.publish(msg)
        for m in list(pending):
            try:
                url = f"http://{peer}:1026/ngsi-ld/v1/entities/urn:ngsi-ld:HRIModuleStatus:{m}?options=keyValues"
                if isinstance(json.load(urllib.request.urlopen(url, timeout=2)).get("status"), dict):
                    pending.discard(m)
            except Exception:
                pass
        time.sleep(1)
    for pub in pubs.values():
        obs.destroy_publisher(pub)
    return not pending

# --------------------------------------------------------------------------- scenarios
class Ctx:
    def __init__(self, obs, op, args):
        self.obs, self.op, self.args = obs, op, args
        self.checks = []
        self.notes = {}

    def check(self, name, ok, detail=""):
        self.checks.append({"check": name, "ok": bool(ok), "detail": str(detail)})
        return bool(ok)

    def utter(self, text, timeout=25, noise=None, kind="event", expect=None):
        """Speak, return (mark, speech_end_t, first record of `kind` after speech)."""
        mark = self.obs.mark()
        end = self.op.speak(text, noise_snr_db=noise)
        pred = expect or (lambda f: True)
        t, rec = self.obs.wait(pred, kind, mark, timeout + max(0, end - time.monotonic()))
        return mark, end, t, rec

    def settle(self, seconds=1.0):
        time.sleep(seconds)


def is_(**want):
    return lambda f: all(f.get(k) == v for k, v in want.items())


def s01_stt_transcription(c):
    mark, end, t, rec = c.utter("inspect valve seven", kind="transcript")
    text = (rec or {}).get("text", "")
    c.check("final transcript published", rec is not None, text)
    c.check("transcript contains valve + seven", re.search(r"valve", text, re.I) and re.search(r"seven|7", text, re.I), text)
    partials = [f for _, f in c.obs.since(mark, "partial")]
    c.check("streaming partials precede the final", len(partials) >= 1, f"{len(partials)} partials")
    if t:
        c.notes["end_of_speech_to_final_s"] = round(t - end, 2)
    c.utter_cleanup()


def s02_stop_is_immediate(c):
    mark, end, t, rec = c.utter("Stop, there is a worker ahead", expect=is_(intent="stop"))
    ok = c.check("stop event published", rec is not None, rec)
    if ok:
        c.check("stop executes without confirmation", rec["decision"] == "ACCEPT" and rec["safety_critical"], rec["decision"])
        c.check("modality voice, engaged", rec["modality"] == "voice" and rec["engaged"], rec["modality"])
        c.check("mission id taken from /artaban/status", rec["mission_id"] == "M-IT-001", rec["mission_id"])
        c.check("operator id set", bool(rec["operator_id"]), rec["operator_id"])
        c.notes["end_of_speech_to_event_s"] = round(t - end, 2)
    t2, say = c.obs.wait(is_(text="Stop, executing."), "say", mark, 3)
    c.check("speaker prompt 'Stop, executing.'", say is not None)
    t3, sp = (None, None)
    if t2:
        t3, sp = c.obs.wait(lambda f: f["value"] is True, "speaking", c.obs.mark() - 1 if False else mark, 3)
        while sp is not None and t3 < t2:  # an edge from an earlier phrase does not count
            nxt = [(t, f) for t, f in c.obs.since(mark, "speaking") if f["value"] is True and t >= t2]
            t3, sp = nxt[0] if nxt else (None, None)
            break
    c.check("TTS starts speaking (/aura/speaker/speaking true)", sp is not None)
    if t2 and t3:
        c.notes["say_to_speaking_ms"] = round((t3 - t2) * 1000)
    c.utter_cleanup()


def s03_confirm_by_voice(c):
    mark, end, t, rec = c.utter("robot, follow me", expect=lambda f: f["intent"] == "come_follow")
    c.check("command recognised", rec is not None, rec)
    if rec:
        c.check("held for confirmation (CONFIRM)", rec["decision"] == "CONFIRM", rec["decision"])
    _, say = c.obs.wait(lambda f: "Did you say come follow" in f["text"], "say", mark, 3)
    c.check("speaker asks 'Did you say come follow?'", say is not None, say)
    c.utter_cleanup()
    mark, end, t, rec = c.utter("yes, do it", expect=is_(decision="ACCEPT"))
    c.check("'yes' answers the pending question", rec is not None and rec["intent"] == "come_follow", rec)
    if rec:
        c.check("outcome correct", rec["outcome"] == "correct" and rec["correct"], rec["outcome"])
        c.notes["confirm_answer_latency_ms"] = round(rec["latency_ms"])
    _, say = c.obs.wait(lambda f: "executing" in f["text"].lower(), "say", mark, 3)
    c.check("speaker acknowledges execution", say is not None, say)
    c.utter_cleanup()


def s04_deny_by_voice(c):
    mark, end, t, rec = c.utter("inspect valve seven", expect=lambda f: f["decision"] in ("CONFIRM", "REJECT", "ACCEPT"))
    c.check("target_select held for confirmation", rec is not None and rec["decision"] == "CONFIRM", rec)
    c.utter_cleanup()
    mark, end, t, rec = c.utter("no, cancel that", expect=lambda f: f["intent"] == "cancel")
    c.check("'no' cancels the pending command", rec is not None, rec)
    if rec:
        c.check("outcome misrecognized (operator_cancelled in monitor)", rec["outcome"] == "misrecognized", rec["outcome"])
    c.utter_cleanup()


def s05_side_talk_ignored(c):
    mark, end, t, rec = c.utter("Mike, follow the red line to the compressor", expect=lambda f: True)
    c.check("event published", rec is not None, rec)
    if rec:
        c.check("IGNORE, not engaged", rec["decision"] == "IGNORE" and not rec["engaged"], rec["decision"])
    c.settle(2)
    c.check("robot stays silent", not c.obs.since(mark, "say"), c.obs.since(mark, "say"))
    c.utter_cleanup()


def s06_unsupported_command(c):
    mark, end, t, rec = c.utter("robot, open the valve", expect=lambda f: True)
    c.check("event published", rec is not None, rec)
    if rec:
        c.check("unsupported command is never executed", rec["decision"] in ("REJECT", "IGNORE"), rec["decision"])
        c.notes["path"] = f"{rec['decision']} intent={rec['intent']} conf={rec['confidence']:.2f} heard={rec['utterance']!r}"
        if rec["decision"] == "REJECT":
            _, say = c.obs.wait(is_(text="Sorry, I cannot do that."), "say", mark, 3)
            c.check("operator told 'cannot do that'", say is not None)
    c.utter_cleanup()


def s07_answer_without_question(c):
    mark, end, t, rec = c.utter("yes", expect=lambda f: True)
    c.check("'yes' with nothing pending is rejected", rec is not None and rec["decision"] == "REJECT", rec)
    _, say = c.obs.wait(is_(text="There is nothing to confirm."), "say", mark, 3)
    c.check("speaker says nothing to confirm", say is not None)
    c.utter_cleanup()


def s08_confirm_timeout(c):
    mark, end, t, rec = c.utter("next step", expect=lambda f: f["decision"] == "CONFIRM")
    c.check("question asked", rec is not None, rec)
    t0 = time.monotonic()
    _, say = c.obs.wait(is_(text="No answer, command dropped."), "say", mark, 14)
    c.check("unanswered question expires (~8 s)", say is not None)
    _, ev = c.obs.wait(is_(decision="REJECT", outcome="rejected"), "event", mark, 2)
    c.check("timeout published as REJECT/rejected", ev is not None, ev)
    c.utter_cleanup()


def s09_stop_interrupts_pending(c):
    mark, end, t, rec = c.utter("resume", expect=lambda f: f["decision"] == "CONFIRM")
    c.check("resume held for confirmation", rec is not None, rec)
    c.utter_cleanup()
    mark, end, t, rec = c.utter("stop", expect=is_(intent="stop"))
    c.check("stop accepted while a question is pending", rec is not None and rec["decision"] == "ACCEPT", rec)
    c.utter_cleanup()
    mark, end, t, rec = c.utter("yes", expect=lambda f: True)
    c.check("stop cleared the pending command ('yes' now rejected)", rec is not None and rec["decision"] == "REJECT", rec)
    c.utter_cleanup()


def s10_noisy_plant(c):
    results = {}
    for snr in (15, 5):
        mark, end, t, rec = c.utter("pause", noise=snr, expect=lambda f: True, timeout=20)
        results[snr] = {"intent": rec and rec["intent"], "decision": rec and rec["decision"],
                        "condition": rec and rec["condition"], "conf": rec and round(rec["confidence"], 2)}
        tr = [f["text"] for _, f in c.obs.since(mark, "transcript")]
        results[snr]["transcript"] = tr
        c.utter_cleanup()
    c.notes["noise_results"] = results
    c.check("pause at 15 dB SNR recognised and executed",
            results[15]["intent"] == "pause" and results[15]["decision"] == "ACCEPT", results[15])
    # At 5 dB the safe behaviours are 'executes' or 'asks again'; an unrelated command must never execute.
    ok = results[5]["intent"] in ("pause", "unknown", "none", None) or results[5]["decision"] != "ACCEPT"
    c.check("pause at 5 dB SNR never executes a wrong command", ok, results[5])


def s11_platform_context(c):
    c.obs.platform(mission="M-IT-002", noise=88.0)
    c.settle(1.0)
    mark, end, t, rec = c.utter("pause", expect=is_(intent="pause"))
    c.check("pause accepted", rec is not None, rec)
    if rec:
        c.check("mission id follows /artaban/status", rec["mission_id"] == "M-IT-002", rec["mission_id"])
        c.check("noise level from /artaban/environment", rec["noise_db"] >= 85, rec["noise_db"])
        c.check("acoustic condition flags noise", "noise" in rec["condition"], rec["condition"])
    c.obs.platform(mission="M-IT-001", noise=62.0)
    c.utter_cleanup()


def s12_camera_pipeline(c):
    obs = c.obs
    before_frames, m = obs.frames, obs.mark()
    c.op.show(c.op.empty_scene())
    time.sleep(10)
    n = obs.frames - before_frames
    c.check("frames flow ws -> bridge -> MJPEG -> /aura/camera/image_raw/compressed", n >= 80, f"{n} frames in 10 s")
    hyp = [f for _, f in obs.since(m, "hypothesis")]
    c.check("gesture node processes frames (hypothesis stream)", len(hyp) >= 60, f"{len(hyp)}")
    c.check("no hands -> no accepted gesture", not obs.since(m, "gesture_intent"), obs.since(m, "gesture_intent"))
    if hyp:
        inf = sorted(f["inference_ms"] for f in hyp)
        lat = sorted(f["latency_ms"] for f in hyp)
        c.notes["gesture_inference_ms_p50"] = round(inf[len(inf) // 2], 1)
        c.notes["gesture_latency_ms_p95"] = round(lat[int(len(lat) * .95) - 1], 1)
    c.notes["camera_diag"] = obs.diag.get("/aura/gesture/status")


def load_gesture_images(root):
    out = {}
    for cls in ("thumb_up", "thumb_down", "fist", "palm", "point"):
        files = sorted((Path(root) / cls).glob("*.jp*g"))[:8]
        if files:
            out[cls] = [f.read_bytes() for f in files]
    return out


def s13_gestures(c):
    if not c.args.gesture_images:
        c.skip = "no --gesture-images given (needs labelled hand photos)"
        return
    imgs = load_gesture_images(c.args.gesture_images)
    expected = {"thumb_up": "confirm", "thumb_down": "reject", "fist": "stop", "palm": "cancel", "point": "select"}
    for cls, frames in imgs.items():
        c.op.show(c.op.empty_scene(10))
        time.sleep(2.0)
        m = c.obs.mark()
        c.op.show(frames)
        t, rec = c.obs.wait(lambda f: True, "gesture_intent", m, 8)
        c.check(f"{cls} -> {expected[cls]}", rec is not None and rec["intent"] == expected[cls], rec)
        if rec:
            c.notes[f"{cls}_accept_ms"] = round((t - c.obs.since(m)[0][0]) * 1000)
        c.op.show(c.op.empty_scene(10))
        time.sleep(1.5)


def s14_fusion_voice_then_gesture(c):
    if not c.args.gesture_images:
        c.skip = "no --gesture-images given"
        return
    imgs = load_gesture_images(c.args.gesture_images)
    if "thumb_up" not in imgs:
        c.skip = "no thumb_up images"
        return
    mark, end, t, rec = c.utter("robot, follow me", expect=lambda f: f["intent"] == "come_follow")
    c.check("voice command held", rec is not None and rec["decision"] == "CONFIRM", rec)
    c.op.show(imgs["thumb_up"])
    t, ev = c.obs.wait(is_(decision="ACCEPT"), "event", mark, 10)
    c.check("thumb-up confirms the voice command", ev is not None and ev["intent"] == "come_follow", ev)
    if ev:
        c.check("modality fused", ev["modality"] == "fused", ev["modality"])
    c.op.show(c.op.empty_scene(10))
    c.settle(2)


def s15_diagnostics(c):
    intent = c.obs.diag.get("/aura/intent/status")
    tts = c.obs.diag.get("/aura/speaker/status")
    c.check("intent diagnostics published", intent is not None, intent)
    c.check("tts diagnostics published", tts is not None, tts)
    c.notes["intent_status"], c.notes["tts_status"] = intent, tts



def s16_command_suite(c):
    """The 22 fine-tuned commands (guided_commands.SET) spoken by the synthetic operator. Needs confirm_mode=never."""
    sys.path.insert(0, str(Path(__file__).parent))
    from guided_commands import SET
    table = []
    for phrase, intent, decision in SET:
        mark, end, t, rec = c.utter(phrase, timeout=20)
        heard = " / ".join(f["text"] for _, f in c.obs.since(mark, "transcript")) or "(nothing)"
        got = (rec or {}).get("intent", "-"), (rec or {}).get("decision", "-")
        ok = got == (intent, decision)
        c.check(f"{phrase!r} -> {intent}/{decision}", ok, f"heard {heard!r}; got {got[0]}/{got[1]}")
        table.append({"said": phrase, "heard": heard, "intent": got[0], "decision": got[1], "ok": ok,
                      "latency_s": round(t - end, 2) if t else None})
        c.utter_cleanup()
    c.notes["suite"] = table
    c.notes["suite_score"] = f"{sum(r['ok'] for r in table)}/{len(table)}"

SCENARIOS = [s01_stt_transcription, s02_stop_is_immediate, s03_confirm_by_voice, s04_deny_by_voice,
             s05_side_talk_ignored, s06_unsupported_command, s07_answer_without_question, s08_confirm_timeout,
             s09_stop_interrupts_pending, s10_noisy_plant, s11_platform_context, s12_camera_pipeline,
             s13_gestures, s14_fusion_voice_then_gesture, s15_diagnostics, s16_command_suite]


def utter_cleanup(self):
    """Let the previous phrase finish and the pipeline go quiet before the next one."""
    self.op.wait_idle()
    time.sleep(1.2)


Ctx.utter_cleanup = utter_cleanup


# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="runs/integration")
    ap.add_argument("--model-dir", default=str(ROOT / "model"))
    ap.add_argument("--tts-dir", default=str(ROOT / "src/aura_tts/model/supertonic-3-int8"))
    ap.add_argument("--latency-ms", type=int, default=560)
    ap.add_argument("--gesture-images")
    ap.add_argument("--only", help="comma list of scenario numbers, e.g. 2,3")
    ap.add_argument("--confirm-timeout", type=float, default=0, help="seconds an operator has to answer (0 = node default)")
    ap.add_argument("--no-platform", action="store_true", help="do not publish /artaban/*; the robot platform does")
    ap.add_argument("--speech-threshold-db", type=float, default=0, help="STT energy gate in dBFS (0 = node default -40)")
    ap.add_argument("--edge", action="store_true", help="publish real edge-computer metrics on /artaban/status (when no robot platform does)")
    ap.add_argument("--mission-id", default="", help="mission id for aura_intent when /artaban/status is not received")
    ap.add_argument("--confirm-mode", default="", help="aura_intent confirm_mode: always, uncertain or never")
    ap.add_argument("--record-mic", default="", help="save the audio STT receives (raw PCM16 16 kHz) to this path")
    ap.add_argument("--live", action="store_true", help="host streams real camera/mic via mac_clients.py; print events")
    ap.add_argument("--keep-running", action="store_true", help="leave nodes + bridge up for live host devices")
    args = ap.parse_args()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    fifo = "/tmp/aura_mic.wav"
    procs = Procs(out)
    report = {"scenarios": [], "started": time.strftime("%F %T")}
    rclpy.init()
    obs = Observer()
    spin = threading.Thread(target=rclpy.spin, args=(obs,), daemon=True)
    spin.start()
    op = None
    try:
        peer0 = os.environ.get("ROS_STATIC_PEERS", "").split(";")[0]
        if peer0:
            ok = prime_orion(obs, peer0)
            print("orion status readers primed" if ok else "WARNING: Orion did not create all status readers", flush=True)
        procs.start("bridge", [sys.executable, str(Path(__file__).parent / "ws_bridge.py"), "--fifo", fifo]
                    + (["--record", args.record_mic] if args.record_mic else []))
        procs.wait_log("bridge", "ws bridge on", 20)
        procs.start("tts", ["ros2", "run", "aura_tts", "tts", "--ros-args", "-p", "output_device:=" + (
            "ws://127.0.0.1:8765/speaker" if args.live else "none")])
        iparams = ([f"confirm_mode:={args.confirm_mode}"] if args.confirm_mode else []) + (
            [f"confirm_timeout_s:={args.confirm_timeout}"] if args.confirm_timeout else []) + (
            [f"mission_id:={args.mission_id}"] if args.mission_id else [])
        procs.start("intent", ["ros2", "run", "aura_intent", "intent"]
                    + (["--ros-args"] + [x for p in iparams for x in ("-p", p)] if iparams else []))
        procs.start("stt", ["ros2", "run", "aura_stt", "stt", "--ros-args", "-p", f"model_dir:={args.model_dir}",
                            "-p", f"audio_file:={fifo}", "-p", "device:=cpu", "-p", f"latency_ms:={args.latency_ms}"]
                   + (["-p", f"speech_threshold_db:={args.speech_threshold_db}"] if args.speech_threshold_db else []))
        procs.start("camera", ["ros2", "run", "aura_gesture", "camera", "--ros-args",
                               "-p", "source:=http://127.0.0.1:18090/stream.mjpg"])
        procs.start("gesture", ["ros2", "run", "aura_gesture", "gesture"])
        peer = os.environ.get("ROS_STATIC_PEERS", "").split(";")[0]
        if args.live and args.edge and peer:  # real edge-computer metrics for /artaban/status, probing the broker
            procs.start("edge", [sys.executable, str(Path(__file__).parent / "edge_telemetry.py"), "--probe", f"{peer}:1026"])
        # the operator needs frames before the camera node can connect to the MJPEG stream
        if not args.live:
            from aura_tts.engine import SherpaTts
            tts = SherpaTts(args.tts_dir, num_threads=2)
            op = Operator("ws://127.0.0.1:8765", tts)
            op.show(op.empty_scene())
        for name, pat in (("tts", r"Speaker ready"), ("intent", r"Intent ready"),
                          ("stt", r"Audio ready"), ("camera", r"Camera source"), ("gesture", r"Gesture ready")):
            try:
                procs.wait_log(name, pat, 240 if name in ("stt", "intent") else 60)
            except TimeoutError as exc:
                report.setdefault("startup_warnings", []).append(str(exc)[:300])
        time.sleep(3)
        if not args.no_platform:
            for _ in range(6):
                obs.platform()
                time.sleep(0.4)
        if args.live:
            print("LIVE: stream from the host now (mac_clients.py)", flush=True)
            seen = 0
            while True:
                time.sleep(0.2)
                with obs.lock:
                    new, seen = obs.log[seen:], len(obs.log)
                for t, k, f in new:
                    if k in ("hypothesis", "partial"):
                        if k == "hypothesis" and f["state"] == 0 and f["gesture"] == "none":
                            continue
                    print(f"{time.time():.2f} {t:10.2f} {k:15s} {json.dumps(f, default=str)[:600]}", flush=True)
        only = {int(x) for x in args.only.split(",")} if args.only else None
        for i, fn in enumerate(SCENARIOS, 1):
            if only and i not in only:
                continue
            ctx = Ctx(obs, op, args)
            ctx.skip = None
            t0 = time.monotonic()
            error = None
            try:
                fn(ctx)
            except Exception:
                error = traceback.format_exc()
            ok = ctx.skip is None and error is None and all(ch["ok"] for ch in ctx.checks)
            status = "SKIP" if ctx.skip else ("PASS" if ok else "FAIL")
            print(f"[{status}] s{i:02d} {fn.__name__} ({time.monotonic() - t0:.0f}s)", flush=True)
            for ch in ctx.checks:
                print(f"    {'ok ' if ch['ok'] else 'BAD'} {ch['check']}" + ("" if ch["ok"] else f"  -> {ch['detail'][:200]}"), flush=True)
            if error:
                print(error, flush=True)
            report["scenarios"].append({"n": i, "name": fn.__name__, "status": status, "skip": ctx.skip, "error": error,
                                        "checks": ctx.checks, "notes": ctx.notes})
        report["frames_sent"], report["audio_s_sent"] = op.sent_frames, round(op.sent_audio_s, 1)
        with obs.lock:
            report["timeline"] = [(round(t, 3), k, f) for t, k, f in obs.log if k not in ("partial", "hypothesis")]
        if args.keep_running:
            print("nodes left running; Ctrl+C to stop", flush=True)
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    except Exception:
        report["fatal"] = traceback.format_exc()
        print(report["fatal"], flush=True)
    finally:
        if op:
            op.stop.set()
        report["exit_codes"] = procs.stop_all()
        (out / "report.json").write_text(json.dumps(report, indent=1, default=str))
        rclpy.try_shutdown()
    counts = {s: sum(1 for x in report["scenarios"] if x["status"] == s) for s in ("PASS", "FAIL", "SKIP")}
    print(f"\nsummary {counts}; exit codes {report.get('exit_codes')}; report {out / 'report.json'}")
    sys.exit(1 if counts["FAIL"] or "fatal" in report else 0)


if __name__ == "__main__":
    main()
