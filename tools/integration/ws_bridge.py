"""WebSocket sensor bridge: lets a host (or a test) act as the robot's camera and microphone.

Runs in the ROS 2 VM next to the AURA nodes and presents the device interfaces they already
support, so the nodes under test run unmodified:

  ws://HOST:8765/camera   binary JPEG frames  -> MJPEG http://127.0.0.1:18090/stream.mjpg
                          (aura_camera `source:=` URL)
  ws://HOST:8765/mic      binary PCM16LE mono -> real-time WAV stream on a FIFO
                          (aura_stt `audio_file:=` path). Optional first text message
                          {"rate": 48000} when the client does not send 16 kHz.

The mic FIFO is clocked at real time: when no client audio is available the bridge writes
a very quiet noise floor, like an idle microphone, so the STT replay thread never stalls.
"""

import argparse
import asyncio
import os
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import soxr
import websockets
from websockets.asyncio.server import serve

RATE = 16000
BLOCK = 320  # 20 ms


class HighPass:
    """Stateful 4th-order Butterworth high-pass on the 16 kHz stream: fan, desk and handling rumble below the
    speech band would otherwise trip STT's energy gate (each false trigger costs about 2 s of decoding).
    cutoff_hz = 0 disables it."""

    def __init__(self, cutoff_hz):
        self.enabled = cutoff_hz > 0
        if self.enabled:
            from scipy.signal import butter, sosfilt
            self.sosfilt = sosfilt
            self.sos = butter(4, cutoff_hz, "highpass", fs=RATE, output="sos")
            self.zi = np.zeros((self.sos.shape[0], 2))

    def process(self, pcm):
        if not self.enabled:
            return pcm
        out, self.zi = self.sosfilt(self.sos, pcm.astype(np.float64), zi=self.zi)
        return np.clip(out, -32768, 32767).astype(np.int16)


class Camera:
    def __init__(self):
        self.cond = threading.Condition()
        self.frame = None
        self.seq = 0
        self.received = 0

    def push(self, jpeg):
        with self.cond:
            self.frame, self.seq = jpeg, self.seq + 1
            self.received += 1
            self.cond.notify_all()


def make_handler(camera, fps):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.end_headers()
            period, last_seq, last_sent = 1.0 / fps, -1, 0.0
            try:
                while True:
                    with camera.cond:
                        camera.cond.wait_for(lambda: camera.frame is not None and camera.seq != last_seq,
                                             timeout=period)
                        frame, last_seq = camera.frame, camera.seq
                    if frame is None:
                        continue
                    # A silent client repeats the last frame so the stream stays alive.
                    gap = last_sent + period - time.monotonic()
                    if gap > 0:
                        time.sleep(gap)
                    last_sent = time.monotonic()
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n"
                                     b"X-Aura-Stamp: %.6f\r\n\r\n" % (len(frame), time.time()) + frame + b"\r\n")
            except (BrokenPipeError, ConnectionResetError):
                pass

    return Handler


class Mic:
    """Real-time paced PCM source feeding a FIFO shaped like a 16 kHz mono PCM16 WAV."""

    def __init__(self, fifo, record="", highpass_hz=0.0):
        self.fifo = fifo
        self.highpass = HighPass(highpass_hz)
        self.record = open(record, "ab", buffering=0) if record else None  # raw PCM16 of what STT receives
        self.dropped_s = 0.0
        self.lock = threading.Lock()
        self.pending = np.empty(0, dtype=np.int16)
        self.received_s = 0.0
        self.underruns = 0
        if os.path.exists(fifo):
            os.remove(fifo)
        os.mkfifo(fifo)
        threading.Thread(target=self._writer, name="mic-fifo", daemon=True).start()

    def push(self, samples):
        samples = self.highpass.process(samples)
        with self.lock:
            # like a real microphone ring buffer: keep only the newest second, never build up latency
            merged = np.concatenate((self.pending, samples))
            self.dropped_s += max(0, len(merged) - RATE) / RATE  # audio lost to the 1 s ring (writer starved)
            self.pending = merged[-RATE:]
            self.received_s += len(samples) / RATE

    def _writer(self):
        rng = np.random.default_rng(1)
        while True:
            try:
                out = open(self.fifo, "wb", buffering=0)  # blocks until STT opens it
            except OSError:
                time.sleep(0.5)
                continue
            try:
                data_len = 0x7FFFFFF0
                out.write(b"RIFF" + struct.pack("<I", 36 + data_len) + b"WAVEfmt " +
                          struct.pack("<IHHIIHH", 16, 1, 1, RATE, RATE * 2, 2, 16) +
                          b"data" + struct.pack("<I", data_len))
                with self.lock:
                    self.pending = np.empty(0, dtype=np.int16)  # nothing recorded before the reader attached
                deadline = time.monotonic()
                while True:
                    with self.lock:
                        if len(self.pending) >= BLOCK:
                            block, self.pending = self.pending[:BLOCK], self.pending[BLOCK:]
                        else:
                            block = None
                    if block is None:
                        self.underruns += 1
                        block = (rng.standard_normal(BLOCK) * 3).astype(np.int16)  # about -80 dBFS
                    out.write(block.astype("<i2").tobytes())
                    if self.record:
                        self.record.write(block.astype("<i2").tobytes())
                    deadline += BLOCK / RATE
                    time.sleep(max(0, deadline - time.monotonic()))
            except (BrokenPipeError, OSError):
                continue  # STT restarted; wait for the next reader


async def main_async(args):
    camera, mic = Camera(), Mic(args.fifo, args.record, args.highpass)
    listeners = set()  # hosts that own a loudspeaker
    http = ThreadingHTTPServer(("127.0.0.1", args.mjpeg_port), make_handler(camera, args.fps))
    threading.Thread(target=http.serve_forever, daemon=True).start()

    async def handler(ws):
        path = ws.request.path
        if path == "/camera":
            async for message in ws:
                if isinstance(message, bytes):
                    camera.push(message)
        elif path == "/mic":
            resampler = None
            async for message in ws:
                if isinstance(message, str):
                    rate = int(__import__("json").loads(message).get("rate", RATE))
                    resampler = soxr.ResampleStream(rate, RATE, 1, dtype="float32") if rate != RATE else None
                    continue
                pcm = np.frombuffer(message, dtype="<i2")
                if resampler is not None:
                    f = resampler.resample_chunk(pcm.astype(np.float32) / 32768)
                    pcm = np.clip(f * 32768, -32768, 32767).astype(np.int16)
                mic.push(pcm)
        elif path == "/speaker":  # aura_tts output_device -> relayed to every /speaker_out listener
            async for message in ws:
                websockets.broadcast(listeners, message)
        elif path == "/speaker_out":
            listeners.add(ws)
            try:
                await ws.wait_closed()
            finally:
                listeners.discard(ws)
        elif path == "/stats":
            await ws.send(__import__("json").dumps({
                "camera_frames": camera.received, "mic_seconds": round(mic.received_s, 2),
                "mic_buffered_s": round(len(mic.pending) / RATE, 2), "mic_dropped_s": round(mic.dropped_s, 2)}))
        else:
            await ws.close(code=1008, reason="unknown path")

    async with serve(handler, args.host, args.port, max_size=8 * 2**20):
        print(f"ws bridge on ws://{args.host}:{args.port}/(camera|mic|speaker|speaker_out|stats); "
              f"mjpeg http://127.0.0.1:{args.mjpeg_port}/stream.mjpg; mic fifo {args.fifo}", flush=True)
        await asyncio.Future()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--mjpeg-port", type=int, default=18090)
    p.add_argument("--fifo", default="/tmp/aura_mic.wav")
    p.add_argument("--record", default="", help="append the PCM16 16 kHz audio STT receives to this file")
    p.add_argument("--highpass", type=float, default=100.0, help="high-pass cutoff in Hz for the mic stream (0 = off)")
    p.add_argument("--fps", type=float, default=15.0)
    asyncio.run(main_async(p.parse_args()))
