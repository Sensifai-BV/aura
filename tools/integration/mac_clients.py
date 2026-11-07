"""Host-side camera + microphone streamers for ws_bridge (run on the Mac).

    python mac_clients.py --url ws://ros2.orb.local:8765 [--no-mic] [--no-camera] [--preview]

Grant Camera / Microphone access to the terminal app the first time.
"""
import argparse
import threading
import time

import cv2
import numpy as np
from websockets.sync.client import connect


def camera(url, index, fps, preview):
    cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        raise SystemExit("cannot open camera (grant Camera permission to this app)")
    with connect(f"{url}/camera", max_size=None) as ws:
        nxt, n = time.monotonic(), 0
        while True:
            ok, frame = cap.read()
            if not ok:
                continue
            frame = cv2.resize(frame, (640, int(frame.shape[0] * 640 / frame.shape[1])))
            ws.send(cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes())
            n += 1
            if preview:
                cv2.imshow("aura camera", frame)
                cv2.waitKey(1)
            nxt += 1 / fps
            time.sleep(max(0, nxt - time.monotonic()))


speaking_until = [0.0]


def speaker(url):
    """Play the robot's voice (relayed from aura_tts) and mute the mic while it speaks."""
    import collections
    import json

    import sounddevice as sd
    while True:
        try:
            with connect(f"{url}/speaker_out", max_size=None) as ws:
                buf, stream, lock = collections.deque(), None, threading.Lock()

                def cb(out, frames, t, status):
                    with lock:
                        need = frames * 2
                        data = b""
                        while buf and len(data) < need:
                            data += buf.popleft()
                        if len(data) > need:
                            buf.appendleft(data[need:])
                            data = data[:need]
                    pcm = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768
                    out[:len(pcm), 0] = pcm
                    out[len(pcm):, 0] = 0
                    if len(pcm):
                        speaking_until[0] = time.monotonic() + 0.7  # room echo tail

                print("speaker: ready", flush=True)
                for msg in ws:
                    if isinstance(msg, str):
                        m = json.loads(msg)
                        if m.get("cancel"):
                            with lock:
                                buf.clear()
                        elif "rate" in m:
                            if stream is not None:
                                stream.stop(); stream.close()
                            stream = sd.OutputStream(samplerate=m["rate"], channels=1, dtype="float32",
                                                     blocksize=m["rate"] // 50, callback=cb)
                            stream.start()
                    else:
                        with lock:
                            buf.append(msg)
                        speaking_until[0] = time.monotonic() + 0.7
        except Exception as exc:
            print(f"speaker: {exc}; retrying", flush=True)
            time.sleep(1)


def mic(url):
    import sounddevice as sd
    info = sd.query_devices(kind="input")
    rate = int(info["default_samplerate"])
    with connect(f"{url}/mic", max_size=None) as ws:
        ws.send('{"rate": %d}' % rate)
        def cb(indata, frames, t, status):
            block = np.zeros(frames) if time.monotonic() < speaking_until[0] else indata[:, 0]  # no self-hearing
            ws.send((np.clip(block, -1, 1) * 32767).astype("<i2").tobytes())
        with sd.InputStream(samplerate=rate, channels=1, dtype="float32", blocksize=rate // 10, callback=cb):
            print(f"mic: {info['name']} @ {rate} Hz", flush=True)
            while True:
                time.sleep(1)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="ws://ros2.orb.local:8765")
    ap.add_argument("--camera-index", type=int, default=0)
    ap.add_argument("--fps", type=float, default=15)
    ap.add_argument("--no-speaker", action="store_true")
    ap.add_argument("--no-mic", action="store_true")
    ap.add_argument("--no-camera", action="store_true")
    ap.add_argument("--preview", action="store_true")
    a = ap.parse_args()
    if not a.no_speaker:
        threading.Thread(target=speaker, args=(a.url,), daemon=True).start()
    if not a.no_mic:
        threading.Thread(target=mic, args=(a.url,), daemon=True).start()
    if not a.no_camera:
        camera(a.url, a.camera_index, a.fps, a.preview)
    while True:
        time.sleep(1)
