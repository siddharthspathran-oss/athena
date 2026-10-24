"""The Stage's window (24 Oct 2026, after Hedwig): a small panel in the
bottom-right corner that comes up when you talk to Athena and goes after.

Frameless and exactly the panel's size, always on top, and it never takes
the focus from whatever you're typing in (focus=False: WS_EX_NOACTIVATE).
Shown and hidden with plain Win32 calls for the same reason: pywebview's
show() activates the window. Not transparent: pywebview's WinForms window
only makes the page clear, the form behind it stays light grey, and that
showed as a white frame round the panel (24 Oct 2026). Windows 11 rounds the
corners and draws the border and shadow instead (DWM).

The page (ui/stage.html) draws everything from /live; this process only
decides when the window is up: while Athena is listening, thinking or
speaking, a few seconds after, and longer while a card is up or the pointer
is over it. Its ✕ puts it away until you next talk to her.
"""
from __future__ import annotations

import ctypes
import threading
import time

from ..config import DATA, cfg

W, H_MAX, H_MIN = 420, 740, 160          # window = the panel, in CSS pixels; the page asks for its height (fit)
MARGIN = 14                              # from the screen's edges
BG = "#0e0b16"                           # the panel's colour (stage.css): nothing lighter ever shows
BORDER = (74, 58, 112)                   # a quiet violet edge, drawn by Windows
ACTIVE = ("listening", "thinking", "speaking")
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE = 0x1, 0x2, 0x4, 0x10
u32 = ctypes.windll.user32


class _Rect(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def _work_area() -> _Rect:
    r = _Rect()
    u32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0)                # SPI_GETWORKAREA: the screen minus the taskbar
    return r


def _overlay(hwnd: int) -> None:
    """Never takes the focus, and not in the taskbar or Alt+Tab: an overlay, not an app.
    Set here because WinForms rebuilds the window (shadow on) and drops pywebview's flag."""
    ex = u32.GetWindowLongW(hwnd, -20)                                    # GWL_EXSTYLE
    u32.SetWindowLongW(hwnd, -20, (ex | 0x08000000 | 0x80) & ~0x40000)   # +NOACTIVATE +TOOLWINDOW -APPWINDOW


def _round(hwnd: int) -> None:
    """Windows 11 draws the rounded corners and a thin violet border (DWM)."""
    dwm = ctypes.windll.dwmapi
    corner = ctypes.c_int(2)                                              # DWMWCP_ROUND
    dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(corner), 4)          # DWMWA_WINDOW_CORNER_PREFERENCE
    r, g, b = BORDER
    colour = ctypes.c_uint(r | g << 8 | b << 16)
    dwm.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(colour), 4)          # DWMWA_BORDER_COLOR


def wanted(now: float, active: bool, card: dict, last_active: float, hover: bool, snoozed: bool) -> bool:
    """Up while she's busy, QUIET seconds after, LINGER after a card, and while the pointer is on it."""
    if snoozed:
        return False
    quiet = float(cfg.get("stage.quiet_seconds", 5))
    linger = float(cfg.get("stage.linger_seconds", 25))
    recent_card = bool(card) and now - max(float(card.get("t") or 0), last_active) < linger
    return active or hover or recent_card or (last_active > 0 and now - last_active < quiet)


def run() -> None:
    import webview

    from .. import live
    from .web import get_token

    port = int(cfg.get("web.port", 8420))
    wa = _work_area()
    st = {"hover": False, "snoozed": False, "seen_idle": False, "shown": False, "h": 320, "dpr": 1.0, "hwnd": 0}

    def place(h_css: float) -> None:
        """Bottom-right, the given height: the panel grows upwards."""
        hwnd, k = st["hwnd"], st["dpr"]
        if not hwnd:
            return
        w, h = int(W * k), int(max(H_MIN, min(H_MAX, h_css)) * k)
        u32.SetWindowPos(hwnd, 0, wa.right - w - MARGIN, wa.bottom - h - MARGIN, w, h, SWP_NOZORDER | SWP_NOACTIVATE)

    class Api:
        def hover(self, on):
            st["hover"] = bool(on)

        def fit(self, h, dpr=1):
            st["h"], st["dpr"] = float(h), float(dpr or 1)
            place(st["h"])

        def dismiss(self):
            st["snoozed"], st["seen_idle"], st["hover"] = True, False, False

    win = webview.create_window("Athena Stage", f"http://127.0.0.1:{port}/stage?t={get_token()}", js_api=Api(),
                                width=W, height=st["h"], x=wa.right - W - MARGIN, y=wa.bottom - st["h"] - MARGIN,
                                frameless=True, easy_drag=False, on_top=True, focus=False, shadow=True,
                                resizable=False, hidden=True, background_color=BG)

    def watch() -> None:
        last_active = 0.0
        while not st["hwnd"]:
            st["hwnd"] = u32.FindWindowW(None, "Athena Stage")
            time.sleep(0.2)
        u32.ShowWindow(st["hwnd"], SW_HIDE)
        _overlay(st["hwnd"])
        _round(st["hwnd"])
        while True:
            time.sleep(0.1)
            v = live.read_voice()
            now = time.time()
            active = bool(v.get("running")) and v.get("state") in ACTIVE
            if active:
                last_active = now
            if st["snoozed"]:                                             # ✕: away until she's next spoken to
                st["seen_idle"] = st["seen_idle"] or not active
                if st["seen_idle"] and active:
                    st["snoozed"] = False
            want = wanted(now, active, v.get("card") or {}, last_active, st["hover"], st["snoozed"])
            if want and not st["shown"]:
                place(st["h"])
                u32.ShowWindow(st["hwnd"], SW_SHOWNOACTIVATE)
                u32.SetWindowPos(st["hwnd"], -1, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)   # topmost, no focus
                st["shown"] = True
                win.evaluate_js("window.stageEnter && stageEnter()")
            elif not want and st["shown"]:
                win.evaluate_js("window.stageLeave && stageLeave()")
                time.sleep(0.32)                                          # the page's exit animation
                u32.ShowWindow(st["hwnd"], SW_HIDE)
                st["shown"] = False

    webview.start(lambda: threading.Thread(target=watch, daemon=True, name="stage-watch").start(),
                  gui="edgechromium", private_mode=False, storage_path=str(DATA / "stage-webview"))


if __name__ == "__main__":
    t = time.time()
    assert wanted(t, True, {}, t, False, False)                                  # she's busy
    assert wanted(t, False, {}, t - 2, False, False)                             # just finished
    assert not wanted(t, False, {}, t - 60, False, False)                        # long done, nothing to show
    assert wanted(t, False, {"t": t - 10}, t - 10, False, False)                 # a card, lingering
    assert not wanted(t, False, {"t": t - 120}, t - 120, False, False)           # an old card
    assert wanted(t, False, {}, t - 60, True, False)                             # the pointer is on it
    assert not wanted(t, True, {}, t, True, True)                                # put away with ✕
    print("stage_window: checks pass")
