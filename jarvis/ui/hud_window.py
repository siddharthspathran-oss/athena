"""The desktop window: the HUD, opened as an app rather than a browser tab.

The HUD is one page served by the web interface, so the phone and the PC show
the same thing and a change to it lands on both. On the desktop it opens in a
Chromium "app" window - its own taskbar entry and icon, no address bar or
tabs - which is also the only way to get the design's glow, blur and
animation: Tk cannot draw any of it.

It needs the web interface running, and starts it if it is not.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from ..config import DATA, cfg

_BROWSERS = [
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Microsoft/Edge/Application/msedge.exe",
]


def _browser() -> str | None:
    for p in _BROWSERS:
        if p.exists():
            return str(p)
    return shutil.which("chrome") or shutil.which("msedge")


def _native(url: str) -> None:
    """The HUD as a real Windows app (14 Oct 2026): pywebview on Edge WebView2,
    which ships with Windows 11 - its own window, taskbar entry and icon, no
    Chrome. Full screen at start like before; F11 toggles it."""
    import ctypes

    import webview

    # One window: a second launch brings the first one forward instead.
    ctypes.windll.kernel32.CreateMutexW(None, False, "Athena.HUD.single")
    if ctypes.windll.kernel32.GetLastError() == 183:          # ERROR_ALREADY_EXISTS
        h = ctypes.windll.user32.FindWindowW(None, "Athena")
        if h:
            ctypes.windll.user32.ShowWindow(h, 9)             # SW_RESTORE
            ctypes.windll.user32.SetForegroundWindow(h)
        return
    # Its own taskbar group and icon, not Python's.
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Athena.HUD")

    class Api:
        def toggle_fullscreen(self):
            win.toggle_fullscreen()

    full = bool(cfg.get("hud.fullscreen", True))
    win = webview.create_window("Athena", url, js_api=Api(), width=1440, height=920, min_size=(900, 600),
                                background_color="#07060b", fullscreen=full, maximized=not full)
    ico = Path(__file__).with_name("icons") / "jarvis.ico"

    def shown():
        try:                                                  # the window's own icon (WinForms)
            from System.Drawing import Icon
            win.native.Icon = Icon(str(ico))
        except Exception:
            pass
        win.evaluate_js("document.addEventListener('keydown',e=>{if(e.key==='F11'){e.preventDefault();"
                        "window.pywebview&&pywebview.api.toggle_fullscreen()}})")

    win.events.shown += shown
    webview.settings["ALLOW_DOWNLOADS"] = True                # chat export
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    # Not private: the page keeps its settings (open projects, orb choice) between launches.
    webview.start(gui="edgechromium", private_mode=False, storage_path=str(DATA / "hud-webview"))


def _server_up(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2):
            return True
    except Exception:
        return False


def run() -> None:
    from .web import get_token

    port = int(cfg.get("web.port", 8420))
    if not _server_up(port):
        # The HUD is served by the web interface; bring it up if it is down.
        root = Path(__file__).resolve().parents[2]
        subprocess.Popen([sys.executable, "run.py", "web"], cwd=str(root),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for _ in range(40):
            if _server_up(port):
                break
            time.sleep(0.5)

    # Opens on the Command Centre (the phone keeps the voice stage as home).
    url = f"http://127.0.0.1:{port}/?t={get_token()}&home=dash&orb={cfg.get('hud.orb', 'core')}"   # core | face
    if cfg.get("hud.native", True):
        try:
            _native(url)
            return
        except ImportError:
            pass                    # no pywebview: the Chrome app window below
    exe = _browser()
    if not exe:
        # No Chromium: fall back to the old window rather than nothing.
        from . import gui
        gui.run()
        return

    # A profile of its own, so the window keeps its size and position between
    # launches and never opens as a tab inside your everyday browser session.
    profile = DATA / "hud-profile"
    subprocess.Popen([exe, f"--app={url}", f"--user-data-dir={profile}",
                      "--window-size=1440,920", "--start-fullscreen", "--no-first-run",   # F11 leaves full screen
                      "--no-default-browser-check", "--disable-features=Translate"])
