"""The HUD window and the orb: whether the HUD is up, showing it, and the orb's state and level.

Split out of voice.py (31 Oct 2026). run() in voice.py sets voice_hud._ORB.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from .. import live


# The on-screen orb, when enabled. A module-level handle keeps the state calls
# to one line at each point rather than threading it through every function.
_ORB = None


_hud_seen = {"t": 0.0, "open": False}


def _hud_open() -> bool:
    """True while the HUD app window is open and not minimised.

    The orb exists to show that Athena is listening when nothing else on
    screen does. With the HUD open it only repeats what the reactor ring is
    already showing, and pops up over the very window you are looking at.

    Matched by its exact title: the app window is called "Athena", while the
    same page in an ordinary Chrome tab is "Athena - Google Chrome", so a
    browser tab does not count. Checked at most once a second.
    """
    now = time.time()
    if now - _hud_seen["t"] < 1.0:
        return _hud_seen["open"]
    found = False
    try:
        import win32gui

        def look(hwnd, _):
            nonlocal found
            if (not found and win32gui.IsWindowVisible(hwnd)
                    and not win32gui.IsIconic(hwnd)
                    and win32gui.GetWindowText(hwnd) == "Athena"
                    and win32gui.GetClassName(hwnd).startswith("Chrome_WidgetWin")):
                found = True

        win32gui.EnumWindows(look, None)
    except Exception:
        pass
    _hud_seen.update(t=now, open=found)
    return found


def _hud_hwnd() -> int:
    """The HUD app window's handle, minimised or not; 0 if it is closed."""
    hit = 0
    try:
        import win32gui

        def look(hwnd, _):
            nonlocal hit
            if (not hit and win32gui.IsWindowVisible(hwnd)
                    and win32gui.GetWindowText(hwnd) == "Athena"
                    and win32gui.GetClassName(hwnd).startswith("Chrome_WidgetWin")):
                hit = hwnd

        win32gui.EnumWindows(look, None)
    except Exception:
        pass
    return hit


def _show_hud() -> str:
    """Bring the HUD up: restore it if it is only minimised, else open it."""
    hwnd = _hud_hwnd()
    if hwnd:
        try:
            import win32con
            import win32gui

            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass
        return "restored"
    import subprocess
    import sys

    pyw = Path(sys.executable).with_name("pythonw.exe")
    subprocess.Popen([str(pyw if pyw.exists() else sys.executable), "run.py", "gui"],
                     cwd=str(Path(__file__).resolve().parents[2]),
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return "opened"


def _orb_state(state: str) -> None:
    live.state(state)               # the HUD, whether or not the orb is on
    if _ORB is not None:
        # Stay off screen while the HUD is open; it shows the same thing.
        _ORB.set_state("idle" if _hud_open() else state)


def _orb_level(rms: float) -> None:
    live.level(rms)
    if _ORB is not None:
        _ORB.set_level(rms)
