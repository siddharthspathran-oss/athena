"""Phone voice: talk into the phone, hear Athena answer on the phone.

The phone app records you (MediaRecorder) and plays the reply; the hearing
and speaking happen on the PC, in the voice process, which already has
Whisper and Kokoro warm on the GPU. CPU copies in the web process were far
too slow (16 Oct 2026: Kokoro took 16.7 s for 6 s of speech, Whisper 3.6 s
per sentence) and the GPU has no room for second copies.

So the voice process serves two calls on 127.0.0.1 only, behind the same
token as the HUD, and the web server forwards the phone's requests to them:

    POST /stt   audio bytes (webm/opus, mp4, wav...)  -> {"text": ...}
    POST /tts   {"text": ...}                         -> audio/wav

Audio never leaves the PC - the same privacy as the desk mic.
"""
from __future__ import annotations

import io
import json
import secrets
import threading
import urllib.error
import urllib.request
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

from .config import DATA, cfg

PORT = int(cfg.get("phone_voice.port", 8421))
TOKEN_FILE = DATA / "web_token.txt"          # the HUD's token (ui/web.py)
MAX_AUDIO = 8 * 1024 * 1024                   # ~ minutes of opus; a phrase is ~50 KB


def _token() -> str:
    return TOKEN_FILE.read_text(encoding="utf-8").strip() if TOKEN_FILE.exists() else ""


def wav_bytes(audio: np.ndarray, rate: int) -> bytes:
    pcm = (np.clip(np.asarray(audio, dtype=np.float32), -1, 1) * 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(rate))
        w.writeframes(pcm)
    return buf.getvalue()


def serve(hear, say) -> None:
    """In the voice process. hear(audio bytes) -> text; say(text) -> (audio, rate)."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_a) -> None:
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            tok = _token()
            if not tok or not secrets.compare_digest(self.headers.get("X-Token", ""), tok):
                self._send(401, b"Bad or missing token.", "text/plain")
                return
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_AUDIO:
                self._send(413, b"Too long.", "text/plain")
                return
            body = self.rfile.read(n)
            try:
                if self.path == "/stt":
                    self._send(200, json.dumps({"text": hear(body)}).encode(), "application/json")
                elif self.path == "/tts":
                    audio, rate = say(str(json.loads(body or b"{}").get("text", ""))[:2000])
                    self._send(200, wav_bytes(audio, rate), "audio/wav")
                else:
                    self._send(404, b"", "text/plain")
            except Exception as e:
                self._send(500, str(e)[:200].encode(), "text/plain")

    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError as e:
        print(f"[phone voice not started: {e}]")
        return
    threading.Thread(target=srv.serve_forever, daemon=True, name="phone-voice").start()
    print(f"[phone voice ready on 127.0.0.1:{PORT}]")


def call(path: str, body: bytes, ctype: str = "application/octet-stream") -> bytes:
    """From the web server: forward a phone request to the voice process."""
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=body, method="POST",
                                 headers={"X-Token": _token(), "Content-Type": ctype})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(e.read().decode("utf-8", "replace")[:200] or f"HTTP {e.code}") from None
    except (urllib.error.URLError, ConnectionError, TimeoutError):
        raise RuntimeError("Athena's voice isn't running on the PC") from None


if __name__ == "__main__":
    import wave as _w
    b = wav_bytes(np.zeros(2400, dtype=np.float32), 24000)
    with _w.open(io.BytesIO(b)) as w:
        assert (w.getframerate(), w.getnframes(), w.getsampwidth()) == (24000, 2400, 2)
    print("phonevoice: checks pass")
