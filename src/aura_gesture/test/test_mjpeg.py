import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from aura_gesture.mjpeg import MjpegStream

FRAMES = [b"\xff\xd8first\xff\xd9", b"\xff\xd8second-frame\xff\xd9"]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=auraframe")
        self.end_headers()
        for i, jpeg in enumerate(FRAMES):
            self.wfile.write(b"--auraframe\r\nContent-Type: image/jpeg\r\n"
                             + f"Content-Length: {len(jpeg)}\r\nX-Aura-Label: like\r\nX-Aura-Seq: {i}\r\n\r\n".encode()
                             + jpeg + b"\r\n")


@pytest.fixture()
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/stream.mjpg"
    srv.shutdown()


def test_reads_frames_and_headers(server):
    stream = MjpegStream(server).open()
    try:
        for i, expected in enumerate(FRAMES):
            jpeg, headers = stream.read()
            assert jpeg == expected
            assert headers["x-aura-label"] == "like" and headers["x-aura-seq"] == str(i)
        with pytest.raises(ConnectionError):
            stream.read()
    finally:
        stream.close()
