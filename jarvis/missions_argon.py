"""Gemini Argon through the Gemini desktop app: find and wake the app, type the task,
wait for the answer, and export what it made - driven by UI Automation Invoke only
(a coordinate click once hit Share and made a public link, 16 Oct 2026).

Split out of missions.py (31 Oct 2026); missions.py imports all of it back.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path

from .config import cfg
from .missions_store import load, _update, _preview, _note


def _gemini_app() -> int | None:
    import psutil

    from . import winapi
    for hwnd, _t, pid in winapi.windows_with_pids():
        try:
            if psutil.Process(pid).name().lower() == "gemini.exe":
                return hwnd
        except Exception:
            continue
    return None


def _wake_app(hwnd: int) -> None:
    """Electron builds its accessibility tree per render widget, on request."""
    import ctypes
    from ctypes import wintypes

    from . import uia
    if not hwnd:            # EnumChildWindows(None) walks every window on the desktop
        return
    kids = []
    cb = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)(lambda h, _: kids.append(h) or True)
    ctypes.windll.user32.EnumChildWindows(hwnd, cb, 0)
    for h in [hwnd] + kids:
        uia.wake(h, 0.3)


def _clip(text: str | None = None) -> str:
    """Read the clipboard, or set it when text is given."""
    if text is not None:
        subprocess.run("clip", input=text.encode("utf-16-le"), shell=True, check=False,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return text
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-Clipboard -Raw"],
                         capture_output=True, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return out.stdout.decode("utf-8", "replace").rstrip("\r\n")


def _invoke(el) -> bool:
    """Press a control through UI Automation's Invoke - aimed at the named
    control itself, never at screen coordinates. A coordinate click on a moved
    or minimised window landed on Share instead of Copy (16 Oct 2026)."""
    from . import uia
    _, uiac = uia._client()
    # Buttons Invoke, the mode picker Expands, its menu items only take the default action.
    for pid, iface, call in ((uiac.UIA_InvokePatternId, uiac.IUIAutomationInvokePattern, "Invoke"),
                             (uiac.UIA_ExpandCollapsePatternId, uiac.IUIAutomationExpandCollapsePattern, "Expand"),
                             (uiac.UIA_LegacyIAccessiblePatternId, uiac.IUIAutomationLegacyIAccessiblePattern, "DoDefaultAction")):
        try:
            pat = el._raw.GetCurrentPattern(pid)
            if pat:
                getattr(pat.QueryInterface(iface), call)()
                return True
        except Exception:
            continue
    return False


def _collapse(el) -> None:
    from . import uia
    _, uiac = uia._client()
    try:
        pat = el._raw.GetCurrentPattern(uiac.UIA_ExpandCollapsePatternId).QueryInterface(uiac.IUIAutomationExpandCollapsePattern)
        if pat.CurrentExpandCollapseState == 1:      # expanded
            pat.Collapse()
    except Exception:
        pass


def _focus_box(hwnd: int, box) -> bool:
    """Keyboard focus in the prompt box, checked - keys go wherever focus is."""
    import ctypes

    from . import uia, winapi
    uia_c, _ = uia._client()
    winapi.focus_hwnd(hwnd)
    time.sleep(0.4)
    try:
        box._raw.SetFocus()
        time.sleep(0.4)
        return (ctypes.windll.user32.GetForegroundWindow() == hwnd
                and uia_c.GetFocusedElement().CurrentName == box.name)
    except Exception:
        return False


def _run_argon(mid: str, prompt: str, resume: bool) -> None:
    import os

    from . import uia, winapi
    m = next(x for x in load() if x["id"] == mid)
    folder = Path(m["folder"])
    _update(mid, status="running", pid=os.getpid())      # alive while this process is
    hwnd = _gemini_app()
    if not hwnd:
        os.startfile("shell:AppsFolder\\com.google.gemini.windows")
        for _ in range(30):
            time.sleep(1)
            if (hwnd := _gemini_app()):
                break
    if not hwnd:
        _update(mid, status="failed", ended=time.time(), result="The Gemini app didn't open.")
        return
    time.sleep(2)
    _wake_app(hwnd)

    def find(prefix: str, exact: bool = False):
        for e in uia.elements(hwnd):
            if e.control_type in (uia.BUTTON, 50005, 50011) and (e.name == prefix if exact else e.name.startswith(prefix)):
                return e
        return None

    if resume:      # a fresh chat that carries the last answer: the app's own chat may have moved on
        last = (folder / "answer.md").read_text(encoding="utf-8") if (folder / "answer.md").exists() else ""
        prompt = f"Here is the work so far:\n\n{last}\n\nNow: {prompt}"
    saved = _clip()
    try:
        new = find("New chat", exact=True)
        if new and _invoke(new):
            time.sleep(2)
        def argon_on():
            p = find("Open mode picker")
            return p, bool(p and "Argon" in p.name)

        pick, on = argon_on()
        for _attempt in range(2):     # the label catches up a moment after the pick - one check failed it (18 Oct 2026)
            if on:
                break
            if pick and _invoke(pick):
                time.sleep(1)
                item = next((e for e in uia.elements(hwnd, 50011) if "4 Argon" in e.name), None)   # MENUITEM
                limit = re.search(r"Limit resets (.+)$", item.name if item else "")
                if limit:               # Google AI Pro's Argon quota is used up (16 Oct: after a 15-min Task)
                    _collapse(pick)
                    raise RuntimeError(f"your Argon limit on Google AI Pro is used up - it resets {limit.group(1)}")
                if item and _invoke(item):
                    time.sleep(1)
                _collapse(pick)
            for _ in range(10):
                time.sleep(0.5)
                pick, on = argon_on()
                if on:
                    break
        if not on:
            raise RuntimeError("couldn't pick Argon in the Gemini app")
        mode = find("Task", exact=True)
        if mode and _invoke(mode):
            time.sleep(1.5)
        box = next((e for e in uia.elements(hwnd, uia.EDIT) if e.name == "Enter a prompt for Gemini"), None)
        if box is None or not _focus_box(hwnd, box):
            raise RuntimeError("couldn't get the Gemini app's prompt box in focus (is the PC locked, or another window on top?)")
        _clip(prompt)                                   # pasted, not typed: keeps newlines without sending early
        time.sleep(0.4)
        winapi.press_keys(0x11, ord("V"))
        time.sleep(0.8)
        winapi.press_vk(0x0D)
        _note(mid, "gave the task to Gemini Argon in the Gemini app")
    except Exception as ex:
        _clip(saved)
        _update(mid, status="failed", ended=time.time(), result=f"Couldn't hand it to the Gemini app: {ex}.")
        return
    _clip(saved)

    limit = time.time() + float(cfg.get("missions.max_minutes", 30)) * 60
    told = set()          # Argon's own step lines ("Verified page 1 layout..."), passed on as progress
    while time.time() < limit:
        time.sleep(5)
        m = next(x for x in load() if x["id"] == mid)
        import ctypes
        if ctypes.windll.user32.IsIconic(hwnd):     # minimised, the app stops drawing - the answer never shows (18 Oct 2026)
            ctypes.windll.user32.ShowWindow(hwnd, 4)    # SW_SHOWNOACTIVATE: back on screen without stealing focus
        names = {e.name for e in uia.elements(hwnd, uia.BUTTON)}
        if m["status"] == "stopped":
            stop_btn = find("Stop response", exact=True)
            if stop_btn:
                _invoke(stop_btn)
            return
        if "Stop response" not in names and "Copy" in names:
            break
        for e in uia.elements(hwnd, 50020):                     # TEXT
            line = e.name.strip()
            if len(line) > 25 and line not in told and not line.startswith(("You said", "Gemini is AI")) \
                    and line[:60] not in prompt:
                told.add(line)
                _note(mid, line[:110])
    else:
        _update(mid, status="failed", ended=time.time(), result="Argon didn't finish in time - it's still in the Gemini app.")
        return
    saved, marker = _clip(), f"athena-{mid}"
    try:
        _clip(marker)
        copy = [e for e in uia.elements(hwnd, uia.BUTTON) if e.name == "Copy"]
        answer = _clip() if copy and _invoke(copy[-1]) and not time.sleep(1.2) else marker
    finally:
        _clip(saved)
    if not answer.strip() or answer == marker:
        _update(mid, status="failed", ended=time.time(), result="Argon finished, but I couldn't copy its answer - it's in the Gemini app.")
        return
    (folder / "answer.md").write_text(answer + "\n", encoding="utf-8")
    first = next((ln.strip(" *#-[]") for ln in answer.splitlines() if ln.strip(" *#-[]")), "")
    pdf = ""
    try:
        pdf = _export_canvas(hwnd, folder)
    except Exception as e:
        _note(mid, f"couldn't export Argon's canvas ({str(e)[:80]})")
    _update(mid, status="done", ended=time.time(), preview=_preview(folder),
            turns=int(m.get("turns", 0) or 0) + 1,
            result=f"Saved Argon's answer to answer.md{' and its document to ' + pdf if pdf else ''}. "
                   f"It starts: {first[:200]}")


def _export_canvas(hwnd: int, folder: Path) -> str:
    """Argon's Canvas document (a typeset PDF) out of the app and into the
    mission folder: Export -> PDF -> Save in the app's own dialog, then the new
    PDF is moved out of Downloads. All by UI Automation, no screen clicks.
    "" when this answer has no Canvas."""
    from . import uia
    exp = next((e for e in uia.elements(hwnd, uia.BUTTON) if e.name == "Export"), None)
    if exp is None:
        return ""
    downloads = Path.home() / "Downloads"
    before = {p.name for p in downloads.glob("*.pdf")}
    _invoke(exp)
    time.sleep(1.2)
    _wake_app(hwnd)
    item = next((e for e in uia.elements(hwnd, 50011) if e.name == "PDF"), None)    # MENUITEM
    if item is None or not _invoke(item):
        _collapse(exp)
        return ""
    for _ in range(20):                       # the app asks where to save: take what it offers
        time.sleep(1)
        dlg = _save_dialog()
        if dlg:
            save = next((e for e in uia.elements(dlg, uia.BUTTON) if e.name in ("Save", "&Save")), None)
            if save:
                _invoke(save)
            break
    for _ in range(30):
        time.sleep(1)
        new = [p for p in downloads.glob("*.pdf") if p.name not in before and p.stat().st_size > 1000]
        if new:
            dest = folder / "argon.pdf"
            shutil.move(str(new[0]), dest)
            return dest.name
    return ""


def _save_dialog() -> int | None:
    """The Gemini app's Save As dialog (a standard #32770 window), if it is open."""
    import ctypes

    import psutil

    from . import winapi
    for h, _t, pid in winapi.windows_with_pids():
        try:
            if psutil.Process(pid).name().lower() != "gemini.exe":
                continue
        except Exception:
            continue
        buf = ctypes.create_unicode_buffer(64)
        ctypes.windll.user32.GetClassNameW(h, buf, 64)
        if buf.value == "#32770":
            return h
    return None
