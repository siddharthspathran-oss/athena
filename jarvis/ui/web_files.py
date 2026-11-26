"""What the web server sends and takes besides chat: shared files (with resume),
uploads from the phone, icons, answer and mission pages, documents to read, and
a look through the phone's or the PC's camera.

A mixin: web.Handler inherits FileRoutes, so self._send / self._json and the
request itself are the handler's. Split out of web.py (31 Oct 2026).
"""
from __future__ import annotations

import json
import re
import urllib.parse
import secrets
import time
from pathlib import Path

from .. import render, vision
from ..tools.knowledge import _extract as extract_text
from ..tools.study import answer_page
from ..config import DATA
from .web_status import _log
from .web_base import _lock, get_token


class FileRoutes:
    """Handler methods for files, pages and the camera (see the module docstring)."""

    def _range_start(self, size: int):
        """Answers a Range request's status line and range headers; returns
        (start, end) to send, or None after a 416. Ranges: a big video resumes
        after a dropped connection and a player can seek - and phone/WebView
        players won't play a video at all from a server that ignores them."""
        start, end = 0, size - 1
        rng = re.match(r"bytes=(\d*)-(\d*)$", self.headers.get("Range", "") or "")
        if rng and (rng.group(1) or rng.group(2)):
            if rng.group(1):
                start = int(rng.group(1))
                end = min(int(rng.group(2)), size - 1) if rng.group(2) else size - 1
            else:
                start = max(0, size - int(rng.group(2)))
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return None
        self.send_response(206 if rng and (start, end) != (0, size - 1) else 200)
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        if (start, end) != (0, size - 1):
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        return start, end

    def _serve_share(self, share_id: str) -> None:
        """A file the PC shared with the phone (jarvis/share.py).

        Like /answers, a link the phone's browser follows, so the token comes
        in the query string. Both it and the share id must be right, and the
        file is checked again now - still there, still allowed.
        """
        import urllib.parse

        from .. import share

        query = urllib.parse.parse_qs(self.path.partition("?")[2])
        if not secrets.compare_digest((query.get("t") or [""])[0], get_token()):
            self._send(401, b"Bad or missing token.", "text/plain; charset=utf-8")
            return
        target = share.resolve(urllib.parse.unquote(share_id))
        if target is None:
            self._send(404, b"That file is no longer shared - ask Athena to send it again.",
                       "text/plain; charset=utf-8")
            return
        import mimetypes

        size = target.stat().st_size
        quoted = urllib.parse.quote(target.name)
        ascii_name = target.name.encode("ascii", "replace").decode().replace('"', "_")
        # The real type, not application/octet-stream: with nosniff the phone
        # saved a video as unknown data and showed "no preview" (2 Oct 2026).
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        span = self._range_start(size)
        if span is None:
            return
        start, end = span
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Disposition",
                         f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quoted}")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        left = end - start + 1
        with open(target, "rb") as f:
            f.seek(start)
            while left > 0:
                block = f.read(min(1 << 20, left))
                left -= len(block)
                if not block:
                    break
                try:
                    self.wfile.write(block)
                except (BrokenPipeError, ConnectionResetError):
                    return                      # the phone gave up; nothing to clean up
        _log(f"phone downloaded {target.name}")

    def _receive_upload(self) -> None:
        """A file sent from the phone, streamed straight to disk (share.receive)."""
        import urllib.parse

        from .. import share

        if not self._authorised():
            self._json(401, {"error": "Bad or missing token."})
            return
        query = urllib.parse.parse_qs(self.path.partition("?")[2])
        name = (query.get("name") or ["file"])[0]
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0
        try:
            saved = share.receive(self.rfile, length, name)
        except share.UploadError as e:
            self._json(e.status, {"error": str(e)})
            return
        except OSError as e:
            self._json(500, {"error": f"Could not save it on the PC: {e}"})
            return
        _log(f"received from phone: {saved.name} ({share.human_size(saved.stat().st_size)})")
        self._json(200, {"saved": saved.name, "folder": str(saved.parent),
                         "size": share.human_size(saved.stat().st_size)})

    def _serve_icon(self, path: str) -> None:
        name = Path(path[len("/icons/"):]).name
        icon = Path(__file__).parent / "icons" / name
        if icon.suffix != ".png" or not icon.is_file():
            self._json(404, {"error": "not found"})
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(icon.stat().st_size))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        self.wfile.write(icon.read_bytes())

    def _look(self) -> None:
        """A photo plus a question about it - from the phone, or this PC's webcam.

        The phone's own camera does the capturing - `capture` on a file input
        opens it directly - so the picture arrives here as base64 and only
        needs handing to the vision model.

        Desktop Chrome ignores `capture` and shows a file dialog instead, so
        the HUD on the PC sends `{"webcam": true}` with no image and the frame
        is taken here with ffmpeg, the same path the voice `look` tool uses.
        Capturing server-side rather than with getUserMedia means no camera
        permission prompt in the `--app` window, and it keeps working if that
        Chrome profile ever has the permission denied.
        """
        if not self._authorised():
            self._json(401, {"error": "Bad or missing token."})
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            if length > 12 * 1024 * 1024:
                self._json(413, {"error": "That image is too large (12 MB limit)."})
                return
            payload = json.loads(self.rfile.read(length) or b"{}")
        except Exception as e:
            self._json(400, {"error": f"Could not read the upload: {e}"})
            return

        raw = (payload.get("image") or "").split(",")[-1]
        question = (payload.get("text") or "").strip()
        if not raw and payload.get("webcam"):
            self._look_webcam(question)
            return
        if not raw:
            self._json(400, {"error": "No image was sent."})
            return

        import base64

        vision.CAPTURES.mkdir(parents=True, exist_ok=True)
        name = f"phone_{time.strftime('%Y%m%d_%H%M%S')}.jpg"
        shot = vision.CAPTURES / name
        try:
            shot.write_bytes(base64.b64decode(raw))
        except Exception as e:
            self._json(400, {"error": f"That image did not decode: {e}"})
            return

        with _lock:
            answer = vision.describe(shot, question)
        self._json(200, {"answer": answer, "steps": [
            {"tool": "look", "result": f"{name} ({shot.stat().st_size // 1024} KB)"}]})

    def _look_webcam(self, question: str) -> None:
        """Take a frame from the PC's webcam and ask the vision model about it."""
        import base64

        # Under the lock as well: ffmpeg holds the camera, and two overlapping
        # dshow opens of the same device fail with "Could not run graph".
        with _lock:
            shot = vision.capture()
            if isinstance(shot, str):
                self._json(200, {"answer": shot, "steps": []})
                return
            answer = vision.describe(shot, question)
        # The frame goes back too, so the transcript shows what the camera
        # actually saw - the same thumbnail an attached photo gets.
        image = "data:image/jpeg;base64," + base64.b64encode(shot.read_bytes()).decode()
        self._json(200, {"answer": answer, "image": image, "steps": [
            {"tool": "look", "result": f"{shot.name} ({shot.stat().st_size // 1024} KB)"}]})

    def _read_document(self) -> None:
        """A PDF, Word file or text file sent from the phone.

        The phone is where you actually have the worksheet, and until now it
        could only send pictures - `accept="image/*"` meant the file picker
        offered the photo gallery and nothing else. This takes the document,
        pulls the text out of it and gives it the same treatment the desktop
        does: worked through question by question and rendered to a page.
        """
        if not self._authorised():
            self._json(401, {"error": "Bad or missing token."})
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            if length > 25 * 1024 * 1024:
                self._json(413, {"error": "That file is too large (25 MB limit)."})
                return
            payload = json.loads(self.rfile.read(length) or b"{}")
        except Exception as e:
            self._json(400, {"error": f"Could not read the upload: {e}"})
            return

        raw = (payload.get("data") or "").split(",")[-1]
        question = (payload.get("text") or "").strip()
        # Only the file name, never a path: the phone supplies this, and
        # "../../something" must not escape the uploads folder.
        name = Path(payload.get("name") or "attachment").name
        if not raw:
            self._json(400, {"error": "No file was sent."})
            return

        import base64

        uploads = DATA / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        saved = uploads / f"{time.strftime('%Y%m%d_%H%M%S')}_{name}"
        try:
            saved.write_bytes(base64.b64decode(raw))
        except Exception as e:
            self._json(400, {"error": f"That file did not decode: {e}"})
            return

        body = extract_text(saved).strip()
        if not body:
            self._json(200, {"answer": f"I could not read any text out of {name}. "
                                       f"If it is a scan, send it as a photo instead "
                                       f"and I will look at it.", "steps": []})
            return

        # Same ceiling the desktop uses: roughly what fits alongside an answer.
        limit = 20000
        clipped = body[:limit] + ("\n[...truncated]" if len(body) > limit else "")
        with _lock:
            answer = answer_page(question, clipped, name)
        self._json(200, {"answer": answer, "steps": [
            {"tool": "read", "result": f"{name} ({len(body)} characters)"}]})

    def _serve_answer(self, path: str) -> None:
        """Serve a rendered answer page to the phone.

        The token has to travel in the query string here: this is a link the
        phone browser follows, and a navigation cannot carry the X-Token
        header the rest of the interface uses.
        """
        import urllib.parse

        query = urllib.parse.parse_qs(self.path.partition("?")[2])
        supplied = (query.get("t") or [""])[0]
        if not secrets.compare_digest(supplied, get_token()):
            self._send(401, b"Bad or missing token.", "text/plain; charset=utf-8")
            return

        # Only ever a file inside the answers folder: taking the bare name
        # stops "../../" from reaching anything else on the disk.
        name = Path(urllib.parse.unquote(path[len("/answers/"):])).name
        target = (render.ANSWERS / name).resolve()
        if target.parent != render.ANSWERS.resolve() or not target.is_file():
            self._send(404, b"No such answer.", "text/plain; charset=utf-8")
            return
        self._send(200, target.read_bytes(), "text/html; charset=utf-8")

    def _serve_mission(self, path: str) -> None:
        """A file a mission made: /mission/<id>/<token>/<path in its folder>.

        The token sits in the path, not the query, so a built site's relative
        links (style.css, app.js) carry it too. Pages are served sandboxed - an
        origin of their own - so code Claude wrote can't read the HUD's token
        or call Athena's API.
        """
        import mimetypes
        import urllib.parse

        from .. import missions

        parts = urllib.parse.unquote(path).split("/", 4)          # "", "mission", id, token, rest
        if len(parts) < 4 or not secrets.compare_digest(parts[3], get_token()):
            self._send(401, b"Bad or missing token.", "text/plain; charset=utf-8")
            return
        m = next((x for x in missions.load() if x["id"] == parts[2]), None)
        if m is None:
            self._send(404, b"No such mission.", "text/plain; charset=utf-8")
            return
        root = Path(m["folder"]).resolve()
        target = (root / (parts[4] if len(parts) > 4 and parts[4] else (m.get("preview") or "index.html"))).resolve()
        if root not in target.parents or not target.is_file():   # never outside the mission's folder
            self._send(404, b"No such file.", "text/plain; charset=utf-8")
            return
        body = target.read_bytes()
        span = self._range_start(len(body))
        if span is None:
            return
        body = body[span[0]:span[1] + 1]
        # .md and friends as plain text: readable on the phone instead of a download
        ctype = ("text/plain; charset=utf-8" if target.suffix.lower() in (".md", ".txt", ".csv")
                 else mimetypes.guess_type(target.name)[0] or "text/plain; charset=utf-8")
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "sandbox allow-scripts allow-forms allow-popups allow-modals")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass
