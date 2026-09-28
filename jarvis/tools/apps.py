"""Read and press the controls of any open app - buttons, boxes, menus, tabs.

The same UI Automation route WhatsApp uses (jarvis/uia.py), made general:
app_read lists what is on screen in an app's window, numbered; app_click
presses one; app_type fills a text box. Numbers come from the last
app_read, the way the browser tools work, so the model never has to guess
at coordinates.

Native apps expose themselves unevenly. Office, Settings, File Explorer,
Notepad and most Store apps name their controls well; some games and
custom-drawn apps expose nothing, and then app_read says so rather than
pretending.

Same two limits as the browser tools: nothing is typed into a password
field, and a final button - Send, Delete, Pay, Uninstall, Format - is not
pressed until the user says "submit it".
"""
from __future__ import annotations

import ctypes
import re

from .. import uia, winapi
from ..registry import tool

TAG = ["system"]

# UI Automation control types worth listing: the ones a person clicks or types in.
_KINDS = {
    50000: "button", 50002: "checkbox", 50003: "dropdown", 50004: "textbox",
    50005: "link", 50007: "list item", 50011: "menu item", 50013: "radio",
    50019: "tab", 50024: "tree item", 50029: "item", 50031: "split button",
    50010: "menu", 50016: "toggle",
    # Text is not clickable, but it is how an app shows its answer - the
    # calculator's result is a Text element named "Display is 15".
    50020: "text",
    # Editors - Notepad, Word, a mail body - are Documents, not Edits.
    50030: "text area",
}
MAX_LISTED = 120

_FINAL = re.compile(
    r"\b(send|submit|pay|buy|purchase|place\s+order|delete|remove|uninstall|format|"
    r"erase|reset|discard|confirm|transfer|sign\s*out|log\s*out|empty\s+recycle|"
    r"permanently|shut\s*down|restart)\b", re.I)

_state: dict = {"hwnd": 0, "title": "", "items": [], "pending": None}


def _hwnd_for(app: str) -> tuple[int, str]:
    """The window an app name means. "" / "this" / "current" = the window in
    front, skipping Athena's own HUD."""
    wins = winapi.list_windows()
    key = (app or "").strip().lower()
    if key in ("", "this", "current", "it", "active", "this app", "current app", "that"):
        fg = ctypes.windll.user32.GetForegroundWindow()
        for hwnd, title in wins:
            if hwnd == fg and title != "Athena":
                return hwnd, title
        for hwnd, title in wins:                     # next one down
            if title and title != "Athena" and not title.startswith("Athena wake"):
                return hwnd, title
        return 0, ""
    from .system import _find_window_for

    title = _find_window_for(app)
    if title:
        for hwnd, t in wins:
            if t == title:
                return hwnd, t
    # By program name: "notepad" finds "Untitled - Notepad", "word" finds the doc.
    import psutil
    for hwnd, t, pid in winapi.windows_with_pids():
        try:
            exe = psutil.Process(pid).name().lower().removesuffix(".exe")
        except Exception:
            continue
        if key.replace(" ", "") in exe or exe in key.replace(" ", ""):
            return hwnd, t
    return 0, ""


def _collect(hwnd: int) -> list[dict]:
    """Every named, on-screen, clickable or typeable control in the window."""
    uia.wake(hwnd, settle=0.6)
    client, uiac = uia._client()
    root = client.ElementFromHandle(hwnd)
    conds = [client.CreatePropertyCondition(uiac.UIA_ControlTypePropertyId, k) for k in _KINDS]
    cond = conds[0]
    for c in conds[1:]:
        cond = client.CreateOrCondition(cond, c)
    req = client.CreateCacheRequest()
    for prop in (uiac.UIA_NamePropertyId, uiac.UIA_BoundingRectanglePropertyId,
                 uiac.UIA_IsOffscreenPropertyId, uiac.UIA_ControlTypePropertyId,
                 uiac.UIA_IsPasswordPropertyId, uiac.UIA_IsEnabledPropertyId):
        req.AddProperty(prop)
    found = root.FindAllBuildCache(uiac.TreeScope_Descendants, cond, req)
    items, seen = [], set()
    for i in range(found.Length):
        try:
            raw = found.GetElement(i)
            el = uia.Element(raw, uiac, cached=True)
            name, kind = el.name, _KINDS.get(el.control_type, "control")
            if not el.on_screen or not raw.CachedIsEnabled:
                continue
            if not name and kind not in ("textbox", "text area"):
                continue
            key = (name, kind, el.rect[:2])
            if key in seen:
                continue
            seen.add(key)
            items.append({"el": el, "raw": raw, "kind": kind, "name": name[:70],
                          "password": bool(raw.CachedIsPassword)})
        except Exception:
            continue
        if len(items) >= MAX_LISTED:
            break
    # Reading order: top to bottom, then left to right.
    items.sort(key=lambda it: (it["el"].rect[1] // 12, it["el"].rect[0]))
    return items


def _find(target: str) -> dict:
    items = _state["items"]
    t = str(target).strip()
    if t.isdigit() and 1 <= int(t) <= len(items):
        return items[int(t) - 1]
    low = t.lower()
    for it in items:
        if it["name"].lower() == low:
            return it
    for it in items:
        if low and low in it["name"].lower():
            return it
    raise LookupError(f"nothing called '{target}' in {_state['title'] or 'that window'} - "
                      f"read it again (app_read) and use the number")


@tool(tags=TAG)
def app_read(app: str = "") -> str:
    """List the buttons, boxes, menus and tabs visible in an open app's window,
    numbered, so they can be clicked or typed into. Call before app_click/app_type.

    app: the app, e.g. "notepad", "settings", "word"; empty for the window in front
    """
    # No app named: keep working in the one already open, if it still is.
    # Falling back to "whatever is in front" re-read Claude's own window in
    # the middle of a calculator task, because focus had moved on.
    if not app.strip() and _state["hwnd"] and ctypes.windll.user32.IsWindow(_state["hwnd"]):
        hwnd, title = _state["hwnd"], winapi._title(_state["hwnd"]) or _state["title"]
    else:
        hwnd, title = _hwnd_for(app)
    if not hwnd:
        return f"ERROR: no open window for '{app}'. Open it first (open_app)."
    try:
        items = _collect(hwnd)
    except Exception as e:
        return f"ERROR: could not read {title}: {e}"
    _state.update(hwnd=hwnd, title=title, items=items, pending=None)
    if not items:
        return (f"{title} doesn't expose its controls to Windows, so I can't press "
                f"things in it. I can still type into whatever has focus (type_text).")
    rows = []
    for n, it in enumerate(items, 1):
        extra = " (you type this one)" if it["password"] else ""
        rows.append(f"[{n}] {it['kind']} '{it['name'] or '(unnamed)'}'{extra}")
    return f"Window: {title}\n" + "\n".join(rows)


@tool(tags=TAG)
def app_click(target: str) -> str:
    """Click a button, menu item, tab, checkbox or list item in the app last read.

    A final button - Send, Delete, Pay, Uninstall and the like - is not pressed
    straight away: the user is told and must say "submit it".

    target: the number from app_read, or the control's name. Several, in order, separated by commas ("One, Two, Plus")
    """
    if not _state["hwnd"]:
        return "ERROR: read an app first (app_read)."
    if "," in str(target):
        # A run of presses in one step: typing a number on the calculator
        # cost one planner round trip per digit and ran it out of steps.
        import time
        done = []
        for part in [p.strip() for p in str(target).split(",") if p.strip()]:
            r = app_click(part)
            if not r.startswith("Clicked"):
                return (f"Pressed {', '.join(done) or 'nothing'}; then: {r}")
            done.append(part)
            time.sleep(0.15)
        return f"Clicked {', '.join(done)} in {_state['title']}."
    try:
        it = _find(target)
    except LookupError as e:
        return f"ERROR: {e}"
    if _FINAL.search(it["name"]):
        _state["pending"] = it
        return (f"Ready to press '{it['name']}' in {_state['title']}. "
                f"Say 'submit it' to go ahead, or tell me what to change.")
    if not it["el"].press(_state["hwnd"]):
        return f"ERROR: couldn't press '{it['name']}' - is the window minimised?"
    return f"Clicked '{it['name']}' in {_state['title']}."


@tool(tags=TAG)
def app_type(target: str, text: str) -> str:
    """Type text into a text box in the app last read.

    target: the text box's number from app_read, or its name
    text: what to type
    """
    if not _state["hwnd"]:
        return "ERROR: read an app first (app_read)."
    try:
        it = _find(target)
    except LookupError as e:
        return f"ERROR: {e}"
    if it["password"] or re.search(r"pass(word|code)|\bpin\b|otp|cvv", it["name"], re.I):
        return (f"I don't type passwords, PINs or codes. '{it['name'] or 'That box'}' "
                f"is waiting for you in {_state['title']}.")
    # A text area is someone's document. Setting its value replaces all of
    # it: the first test of this tool typed "Hello from Athena" into the
    # Notepad window already open on voice.log and wiped the tab (25 Sep
    # 2026; restored from disk, never saved). So documents are appended to -
    # click in, jump to the end, type - and only single-line boxes are set.
    if it["kind"] == "text area":
        if not it["el"].press(_state["hwnd"]):
            return f"ERROR: couldn't reach the text in {_state['title']}."
        winapi.press_keys(0x11, 0x23)                  # Ctrl+End
        _type_slowly(text)
        return f"Added the text at the end of {_state['title']}."
    # The Value pattern sets the text directly; not every box offers it, so
    # fall back to clicking in and typing.
    try:
        client, uiac = uia._client()
        pat = it["raw"].GetCurrentPattern(uiac.UIA_ValuePatternId)
        if pat:
            pat.QueryInterface(uiac.IUIAutomationValuePattern).SetValue(text)
            return f"Typed into '{it['name'] or 'the box'}' in {_state['title']}."
    except Exception:
        pass
    if not it["el"].press(_state["hwnd"]):
        return f"ERROR: couldn't reach '{it['name']}'."
    _type_slowly(text)
    return f"Typed into '{it['name'] or 'the box'}' in {_state['title']}."


def _type_slowly(text: str) -> None:
    """Put text in by pasting it, then give the user's clipboard back.

    Simulated keystrokes were mangled by Windows 11 Notepad: "added by
    Athena" arrived as "added sss", and in slower bursts as "mmmmmore
    iiiiiiiis" (25 Sep 2026). A paste is one message the app handles as a
    whole, so it arrives exactly as sent.
    """
    import time

    import win32clipboard as cb
    import win32con

    saved = None
    try:
        cb.OpenClipboard()
        try:
            saved = cb.GetClipboardData(win32con.CF_UNICODETEXT)
        except Exception:
            saved = None
        cb.EmptyClipboard()
        cb.SetClipboardData(win32con.CF_UNICODETEXT, text)
    finally:
        cb.CloseClipboard()
    time.sleep(0.15)
    winapi.press_keys(0x11, ord("V"))                  # Ctrl+V
    time.sleep(0.4)                                    # let the app take it first
    if saved is not None:
        try:
            cb.OpenClipboard()
            cb.EmptyClipboard()
            cb.SetClipboardData(win32con.CF_UNICODETEXT, saved)
        finally:
            cb.CloseClipboard()


def has_pending() -> bool:
    return _state["pending"] is not None


def submit_pending() -> str:
    """Press the held-back final button. Only from the user's "submit it"."""
    it, _state["pending"] = _state["pending"], None
    if it is None:
        return "There's nothing waiting."
    if not it["el"].press(_state["hwnd"]):
        return f"Couldn't press '{it['name']}' - the window may have changed."
    return f"Pressed '{it['name']}' in {_state['title']}."


def cancel_pending() -> str:
    _state["pending"] = None
    return "OK, not pressing it."
