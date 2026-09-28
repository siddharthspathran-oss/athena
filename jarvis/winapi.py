"""Direct Win32 calls for window control, keys and screenshots.

This exists because pyautogui and pygetwindow ship as source-only packages
that build incorrectly on current setuptools - they install their __init__
without the Windows backend modules. Everything needed here is a handful of
user32 calls, so ctypes does the job with no build step and nothing to break.
"""
from __future__ import annotations

import ctypes
import time as time_module
from ctypes import wintypes
from pathlib import Path

user32 = ctypes.WinDLL("user32", use_last_error=True)

# --- window management ---

SW_RESTORE = 9
SW_MINIMIZE = 6
SW_MAXIMIZE = 3

_EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]


def _title(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def list_windows() -> list[tuple[int, str]]:
    """Every visible window with a title, as (hwnd, title)."""
    found: list[tuple[int, str]] = []

    def cb(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd):
            t = _title(hwnd)
            if t.strip():
                found.append((hwnd, t))
        return True

    user32.EnumWindows(_EnumWindowsProc(cb), 0)
    return found


def windows_with_pids() -> list[tuple[int, str, int]]:
    """Visible titled windows as (hwnd, title, owning pid)."""
    out: list[tuple[int, str, int]] = []
    for hwnd, title in list_windows():
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        out.append((hwnd, title, int(pid.value)))
    return out


WM_CLOSE = 0x0010


def close_window(hwnd: int) -> None:
    """Ask a window to close, the same as clicking its X.

    Preferred over killing the process: the app gets to save state and shut
    down cleanly instead of being terminated mid-write.
    """
    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)


def focus_window(needle: str) -> str | None:
    """Bring the first window whose title contains `needle` to the front.

    Returns the matched title, or None if nothing matched.
    """
    needle = needle.lower()
    for hwnd, title in list_windows():
        if needle in title.lower():
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, SW_RESTORE)
            # Windows blocks foreground steals unless the calling thread is
            # attached to the target's input queue.
            fg = user32.GetForegroundWindow()
            tid_target = user32.GetWindowThreadProcessId(hwnd, None)
            tid_fg = user32.GetWindowThreadProcessId(fg, None)
            user32.AttachThreadInput(tid_fg, tid_target, True)
            user32.SetForegroundWindow(hwnd)
            user32.AttachThreadInput(tid_fg, tid_target, False)
            return title
    return None


def window_rect(hwnd: int) -> tuple[int, int, int, int]:
    """Screen rectangle of a window, as (left, top, right, bottom)."""
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return int(r.left), int(r.top), int(r.right), int(r.bottom)


def focus_hwnd(hwnd: int) -> bool:
    """Restore and foreground one specific window.

    focus_window() takes a title substring, which picks whichever window
    matches first - "WhatsApp - Opera" ahead of WhatsApp itself. When the
    caller already knows the handle, matching by name can only get it wrong.
    A minimised window reports coordinates like (-31616, -31950), so anything
    measured before restoring it is meaningless.
    """
    if not hwnd:
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)

    # SetForegroundWindow alone is refused unless the caller owns the current
    # foreground, and it fails silently. The old version returned True
    # regardless, so clicks meant for another app landed in whatever window
    # was actually in front. Try harder, then report what really happened.
    fg = user32.GetForegroundWindow()
    if fg != hwnd:
        tid_target = user32.GetWindowThreadProcessId(hwnd, None)
        tid_fg = user32.GetWindowThreadProcessId(fg, None)
        user32.AttachThreadInput(tid_fg, tid_target, True)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.AttachThreadInput(tid_fg, tid_target, False)

    if user32.GetForegroundWindow() != hwnd:
        # SwitchToThisWindow is what Alt-Tab uses and is not subject to the
        # same lock. Undocumented, present since XP.
        try:
            user32.SwitchToThisWindow(hwnd, True)
        except Exception:
            pass

    if user32.GetForegroundWindow() != hwnd:
        # Windows lifts the foreground lock for a process that has just
        # received input. A background service has had none, which is why
        # this worked from a terminal and failed from the voice process -
        # so give it one, then ask again.
        user32.keybd_event(0x12, 0, 0, 0)          # ALT down
        user32.keybd_event(0x12, 0, KEYEVENTF_KEYUP, 0)
        time_module.sleep(0.05)
        user32.SetForegroundWindow(hwnd)

    for _ in range(20):
        if user32.GetForegroundWindow() == hwnd:
            return True
        time_module.sleep(0.05)
    return False


def window_state(needle: str, state: str) -> str | None:
    """Minimise or maximise a window by partial title. Returns the title."""
    flag = {"minimize": SW_MINIMIZE, "maximize": SW_MAXIMIZE, "restore": SW_RESTORE}.get(state)
    if flag is None:
        return None
    needle = needle.lower()
    for hwnd, title in list_windows():
        if needle in title.lower():
            user32.ShowWindow(hwnd, flag)
            return title
    return None


# --- keyboard ---

# Virtual key codes for the media and volume keys
VK = {
    "playpause": 0xB3,
    "next": 0xB0,
    "previous": 0xB1,
    "stop": 0xB2,
    "mute": 0xAD,
    "volumedown": 0xAE,
    "volumeup": 0xAF,
}

KEYEVENTF_KEYUP = 0x0002


def press_vk(vk: int) -> None:
    """Send a single virtual-key press and release."""
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


def media_key(name: str) -> bool:
    vk = VK.get(name)
    if vk is None:
        return False
    press_vk(vk)
    return True


def type_text(text: str) -> bool:
    """Type a string into the focused window, unicode included.

    The struct this passes to SendInput has to be the real INPUT - 40 bytes,
    sized by its largest member, the mouse event. A union holding only the
    keyboard member measures 32, and SendInput rejects any cbSize that is not
    exactly right: it returns 0 and types nothing, without an error. This
    typed nothing at all until the union was widened.
    """
    KEYEVENTF_UNICODE = 0x0004

    events = []
    for ch in text:
        for flags in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
            e = _MINPUT(type=1)
            e.ki = _KEYBDINPUT_M(0, ord(ch), flags, 0, None)
            events.append(e)
    if not events:
        return True
    arr = (_MINPUT * len(events))(*events)
    sent = user32.SendInput(len(events), arr, ctypes.sizeof(_MINPUT))
    return sent == len(events)


# --- mouse ---

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004


def click(x: int, y: int) -> None:
    """Move the pointer and left-click at a screen coordinate."""
    user32.SetCursorPos(int(x), int(y))
    time_module.sleep(0.15)
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _KEYBDINPUT_M(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _MU(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT_M)]


class _MINPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _MU)]


MOUSEEVENTF_WHEEL = 0x0800


def scroll(x: int, y: int, notches: int) -> bool:
    """Turn the wheel at a screen position. Positive scrolls up."""
    vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    vw = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    vh = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    if vw <= 1 or vh <= 1:
        return False
    ax = int((int(x) - vx) * 65535 / (vw - 1))
    ay = int((int(y) - vy) * 65535 / (vh - 1))
    base = MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK

    move = _MINPUT(type=0)
    move.mi = _MOUSEINPUT(ax, ay, 0, base | MOUSEEVENTF_MOVE, 0, None)
    wheel = _MINPUT(type=0)
    wheel.mi = _MOUSEINPUT(ax, ay, int(notches) * 120,
                           base | MOUSEEVENTF_WHEEL, 0, None)
    arr = (_MINPUT * 2)(move, wheel)
    return user32.SendInput(2, arr, ctypes.sizeof(_MINPUT)) == 2


def click_input(x: int, y: int) -> bool:
    """Left-click a screen coordinate through SendInput.

    Apps built on a web view take this where the legacy mouse_event path is
    less reliable. The caller must have the target window in the foreground
    first - a click goes to whatever is actually in front, not to whatever
    window the coordinates happen to belong to.
    """
    vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    vw = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    vh = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    if vw <= 1 or vh <= 1:
        return False
    ax = int((int(x) - vx) * 65535 / (vw - 1))
    ay = int((int(y) - vy) * 65535 / (vh - 1))
    base = MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK

    # Move first, then click as a separate batch. Controls that only render
    # on hover - Spotify's per-row play button is one - never see the press
    # when the whole sequence arrives at once: the button does not exist yet
    # at the moment the click lands, so it goes to the row behind it.
    move = _MINPUT(type=0)
    move.mi = _MOUSEINPUT(ax, ay, 0, base | MOUSEEVENTF_MOVE, 0, None)
    if user32.SendInput(1, ctypes.byref(move), ctypes.sizeof(_MINPUT)) != 1:
        return False
    time_module.sleep(0.25)

    events = []
    for flags in (base | MOUSEEVENTF_LEFTDOWN, base | MOUSEEVENTF_LEFTUP):
        e = _MINPUT(type=0)
        e.mi = _MOUSEINPUT(ax, ay, 0, flags, 0, None)
        events.append(e)
    arr = (_MINPUT * len(events))(*events)
    return user32.SendInput(len(events), arr, ctypes.sizeof(_MINPUT)) == len(events)


def press_keys(*vks: int) -> None:
    """Press a chord, e.g. press_keys(VK_CTRL, ord('N'))."""
    for vk in vks:
        user32.keybd_event(vk, 0, 0, 0)
    for vk in reversed(vks):
        user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


VK_CONTROL = 0x11
VK_RETURN = 0x0D
VK_ESCAPE = 0x1B


# --- screen ---

def screenshot(path: Path, region: tuple[int, int, int, int] | None = None) -> Path:
    """Capture the screen (or an x, y, width, height box) to a PNG."""
    from PIL import ImageGrab

    if region:
        x, y, w, h = region
        img = ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True)
    else:
        img = ImageGrab.grab(all_screens=True)
    img.save(path)
    return path


def screen_size() -> tuple[int, int]:
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
