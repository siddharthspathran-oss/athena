"""Athena Deck: `pythonw -m deck`.

Two processes: the server (`-m deck --serve`: the consoles, the API) and the
window (pywebview). Apart, because WebView2 starting up in the server's process
stalled the server for ~15 s (20 Oct 2026). Opening the Deck starts the server
if it isn't running, then a window - or, if a window is already open, just
brings it forward. Closing the window stops the server and its consoles; the
board is saved, and next time each console comes back and resumes.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from .consoles import DATA
from .server import PORT, DeckServer, token


def _ping() -> dict:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/ping", timeout=2) as r:
            d = json.load(r)
            return d if d.get("app") == "athena-deck" else {}
    except Exception:
        return {}


def _post(path: str) -> None:
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=b"{}", method="POST",
                                 headers={"X-Token": token(), "Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=5).read()


class Api:
    """What the page can ask the window for (pywebview js_api)."""
    def __init__(self):
        self.window = None

    def pick_folder(self) -> str:
        import webview
        got = self.window.create_file_dialog(webview.FOLDER_DIALOG) if self.window else None
        return got[0] if got else ""


def serve() -> None:
    DeckServer().run()


def window() -> None:
    """The board's window: the Electron shell (deck/shell - browser tiles are real browsers there),
    or pywebview if Electron isn't installed (tiles then only show sites that allow embedding)."""
    shell = Path(__file__).resolve().parent / "shell"
    electron = shell / "node_modules" / "electron" / "dist" / "electron.exe"
    if electron.exists():
        subprocess.run([str(electron), str(shell)])     # returns when the window closes; the shell stops the server
        return
    import webview
    api = Api()
    win = webview.create_window("Athena Deck", f"http://127.0.0.1:{PORT}/?t={token()}", js_api=api,
                                width=1600, height=980, min_size=(900, 600), background_color="#07070b")
    api.window = win
    webview.start(gui="edgechromium", private_mode=False, storage_path=str(DATA / "webview"))
    try:
        _post("/api/quit")                       # window closed: the consoles stop, the board is saved
    except Exception:
        pass


def main() -> None:
    if "--serve" in sys.argv:
        return serve()
    if not _ping():
        exe = Path(sys.executable).with_name("pythonw.exe")
        subprocess.Popen([str(exe if exe.exists() else sys.executable), "-m", "deck", "--serve"],
                         cwd=str(Path(__file__).resolve().parents[1]),
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for _ in range(60):
            time.sleep(0.25)
            if _ping():
                break
    if _ping().get("windows"):                   # one window is enough: bring it forward
        _post("/api/focus")
        return
    window()


if __name__ == "__main__":
    main()
