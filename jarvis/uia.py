"""Reading and pressing controls inside other applications.

Windows apps built on a web view - WhatsApp is one - look empty to UI
Automation at first: a couple of thousand anonymous panes and not a single
button. The tree is not missing, it is lazy. Chromium only builds its
accessibility tree once a client asks for it, and the ask is a WM_GETOBJECT
message. After that the same window exposes 27,000 elements and 4,000 named
buttons.

That one message is the difference between "Athena cannot press that" and
"Athena can press that".
"""
from __future__ import annotations

import ctypes
import time

_WM_GETOBJECT = 0x003D
_OBJID_CLIENT = 0xFFFFFFFC
_SMTO_ABORTIFHUNG = 0x0002

_uia = None
_UIAC = None


def _client():
    """The UI Automation client, created once."""
    global _uia, _UIAC
    if _uia is None:
        import comtypes.client

        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as UIAC

        _UIAC = UIAC
        _uia = comtypes.client.CreateObject(
            "{ff48dba4-60ef-4201-aa87-54103eef594e}",
            interface=UIAC.IUIAutomation)
    return _uia, _UIAC


def wake(hwnd: int, settle: float = 1.5) -> None:
    """Ask the window to build its accessibility tree, then let it finish."""
    ctypes.windll.user32.SendMessageTimeoutW(
        hwnd, _WM_GETOBJECT, 0, _OBJID_CLIENT, _SMTO_ABORTIFHUNG, 2000,
        ctypes.byref(ctypes.c_void_p()))
    time.sleep(settle)


class Element:
    """One control: what it is called, where it is, and how to press it.

    Every Current* property is a separate call into the other process. Over
    a few thousand controls that alone costs seconds, so these are read from
    the cache when the search filled one, and only fall back to a live read
    when it did not.
    """

    def __init__(self, raw, uiac, cached: bool = False):
        self._raw = raw
        self._uiac = uiac
        self._cached = cached

    def _get(self, cached_attr: str, live_attr: str, default):
        if self._cached:
            try:
                return getattr(self._raw, cached_attr)
            except Exception:
                pass
        try:
            return getattr(self._raw, live_attr)
        except Exception:
            return default

    @property
    def name(self) -> str:
        return (self._get("CachedName", "CurrentName", "") or "").strip()

    @property
    def rect(self) -> tuple[int, int, int, int]:
        r = self._get("CachedBoundingRectangle", "CurrentBoundingRectangle", None)
        if r is None:
            return 0, 0, 0, 0
        return int(r.left), int(r.top), int(r.right), int(r.bottom)

    @property
    def on_screen(self) -> bool:
        return not bool(self._get("CachedIsOffscreen", "CurrentIsOffscreen", True))

    @property
    def value(self) -> str | None:
        """The text in a box, read live from this one element - no tree walk.
        None if the control has no value to read."""
        try:
            pat = self._raw.GetCurrentPattern(self._uiac.UIA_ValuePatternId)
            return pat.QueryInterface(self._uiac.IUIAutomationValuePattern).CurrentValue or ""
        except Exception:
            return None

    @property
    def control_type(self) -> int:
        return int(self._get("CachedControlType", "CurrentControlType", 0) or 0)

    def press(self, hwnd: int) -> bool:
        """Click this control, for real.

        The accessibility Invoke pattern reports success on these web-view
        buttons and does nothing, so the press is a genuine click at the
        control's own coordinates - which UI Automation gives us, so it is
        aimed at a named button rather than a guessed position.

        The window must be foregrounded first: a click lands on whatever is
        actually in front, not on the window the coordinates belong to. That
        one detail is what made this look impossible.
        """
        from . import winapi

        if not winapi.focus_hwnd(hwnd):
            return False
        left, top, right, bottom = self.rect
        if left < 0 or top < 0:      # minimised windows report -31616, -31950
            return False
        return winapi.click_input((left + right) // 2, (top + bottom) // 2)


BUTTON = 50000
EDIT = 50004
DATA_ITEM = 50029   # WhatsApp's chat and search-result rows


def elements(hwnd: int, control_type: int | None = None,
             name: str | None = None) -> list[Element]:
    """Controls in the window, after waking the tree.

    WhatsApp exposes about 27,000 elements, and walking all of them takes
    seconds - doing it repeatedly is what made a single call take 38. Given a
    control type or a name, the filtering happens inside UI Automation
    instead, which is enormously cheaper.
    """
    uia, uiac = _client()
    try:
        root = uia.ElementFromHandle(hwnd)
    except Exception:
        # A window that is still opening, or just closed, raises COMError
        # ("An event was unable to invoke any of the subscribers") - it
        # crashed play_my_playlist on 15 Oct 2026. No window, no controls.
        return []

    conds = []
    if control_type is not None:
        conds.append(uia.CreatePropertyCondition(
            uiac.UIA_ControlTypePropertyId, control_type))
    if name is not None:
        conds.append(uia.CreatePropertyCondition(uiac.UIA_NamePropertyId, name))

    if not conds:
        cond = uia.CreateTrueCondition()
    elif len(conds) == 1:
        cond = conds[0]
    else:
        cond = uia.CreateAndCondition(conds[0], conds[1])

    # One call that walks the tree and brings the properties back with it.
    # Fetching them afterwards, one at a time, cost more than the walk did.
    cached = False
    found = None
    try:
        req = uia.CreateCacheRequest()
        for prop in (uiac.UIA_NamePropertyId,
                     uiac.UIA_BoundingRectanglePropertyId,
                     uiac.UIA_IsOffscreenPropertyId,
                     uiac.UIA_ControlTypePropertyId):
            req.AddProperty(prop)
        found = root.FindAllBuildCache(uiac.TreeScope_Descendants, cond, req)
        cached = True
    except Exception:
        try:
            found = root.FindAll(uiac.TreeScope_Descendants, cond)
        except Exception:
            return []

    out = []
    for i in range(found.Length):
        try:
            out.append(Element(found.GetElement(i), uiac, cached))
        except Exception:
            continue
    return out


def find_button(hwnd_or_items, name: str) -> Element | None:
    """An on-screen button with exactly this name.

    Takes either a window handle - which queries UI Automation directly and
    is much faster - or an already-gathered list of elements.
    """
    want = name.strip().lower()
    if isinstance(hwnd_or_items, int):
        items = elements(hwnd_or_items, BUTTON, name)
    else:
        items = hwnd_or_items
    for el in items:
        if el.control_type == BUTTON and el.on_screen and el.name.lower() == want:
            return el
    return None
