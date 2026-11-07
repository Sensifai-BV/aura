"""Minimal multipart MJPEG client that keeps JPEG bytes and per-part headers.

Frames are passed through without decoding, and ``X-Aura-*`` part headers
(ground-truth prompts from the guided capture tool) survive the transport.
"""

import urllib.request


class MjpegStream:
    def __init__(self, url, timeout=5.0):
        self.url = url
        self.timeout = timeout
        self.response = None
        self.boundary = None

    def open(self):
        self.response = urllib.request.urlopen(self.url, timeout=self.timeout)
        ctype = self.response.headers.get("Content-Type", "")
        if "boundary=" not in ctype:
            raise ValueError(f"not a multipart MJPEG stream: {ctype!r}")
        self.boundary = ctype.split("boundary=", 1)[1].strip().strip('"').encode()
        return self

    def close(self):
        if self.response is not None:
            self.response.close()
            self.response = None

    def _line(self):
        line = self.response.readline()
        if not line:
            raise ConnectionError("MJPEG stream closed")
        return line.rstrip(b"\r\n")

    def read(self):
        """Return (jpeg_bytes, headers) for the next part."""
        while True:
            line = self._line()
            if line.lstrip(b"-") == self.boundary.lstrip(b"-"):
                break
        headers = {}
        while True:
            line = self._line()
            if not line:
                break
            key, _, value = line.decode("latin-1").partition(":")
            headers[key.strip().lower()] = value.strip()
        length = int(headers.get("content-length", "0"))
        if length <= 0:
            raise ValueError("MJPEG part without Content-Length")
        data = self.response.read(length)
        if len(data) != length:
            raise ConnectionError("truncated MJPEG frame")
        return data, headers
