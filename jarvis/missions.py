"""Missions: one command, a big job done in the background by Claude Code.

    "Athena, have Claude build me a one-page website for a cafe called Brew Lab"

Athena hands the job to Claude Code (headless, `claude -p`, stream-json) in a
folder of its own under athena-jobs/, shows its progress live, and reports
when it's done - spoken if you're at the PC, a push to the phone if not.
"make the header darker" continues the same job (--resume).

Safety: the worker gets only the tools in missions.tools (file tools, web
search/fetch, and running python/node - no installs), in dontAsk mode, so
anything else is refused rather than asked about; it works in its own folder,
and is told never to send, post, push or buy anything. Each run uses the
user's Claude plan, so the model defaults to Sonnet and connectors, skills
and plugins' MCP servers are not loaded (~4-6x less than a default run).
"""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from .config import DATA, cfg
from .registry import tool
from .store import load_json, save_json

FILE = DATA / "missions.json"
ROOT = Path(str(cfg.get("missions.folder", r"C:\claude code\athena-jobs")))
_lock = threading.Lock()
_procs: dict[str, subprocess.Popen] = {}
TOLD = DATA / "missions_told.json"   # finished missions already announced (voice process only)

WORKER_RULES = (
    "You are Athena's worker: you do one job for the user, in the current folder, without asking questions. "
    "Work only inside the current folder. Never send, post, publish, push, email, message or buy anything, "
    "and never contact anyone. Prefer simple, dependency-free solutions (plain HTML/CSS/JS for websites, "
    "with an index.html). When done, reply with two or three plain sentences: what you made and the main "
    "file to open. If something can't be done, say so plainly.")


def load() -> list[dict]:
    return load_json(FILE, [])


def _save(ms: list[dict]) -> None:
    save_json(FILE, ms)


def _update(mid: str, **fields) -> dict | None:
    with _lock:
        ms = load()
        m = next((x for x in ms if x["id"] == mid), None)
        if m is not None:
            for k, v in fields.items():
                if k == "progress":
                    m.setdefault("progress", []).append(v)
                    m["progress"] = m["progress"][-60:]
                else:
                    m[k] = v
            _save(ms)
        return m


WORKERS = {"claude": "Claude", "gemini": "Gemini", "argon": "Gemini Argon", "maps": "Google Maps"}


# ---------- Gemini Argon, through the Gemini desktop app ----------
#
# On Google AI Pro, Argon exists only in the Gemini app - not in agy, not in
# the API. So this worker drives the app's window: new chat, Argon picked,
# Task mode, the job pasted in, then it waits for "Stop response" to go and
# "Copy" to appear (both by name, via UI Automation), and copies the answer
# out with the app's own Copy button. It needs the PC unlocked and takes the
# foreground for a few seconds while it types.

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
        pick = find("Open mode picker")
        if pick and "Argon" not in pick.name and _invoke(pick):
            time.sleep(1)
            item = next((e for e in uia.elements(hwnd, 50011) if "4 Argon" in e.name), None)   # MENUITEM
            limit = re.search(r"Limit resets (.+)$", item.name if item else "")
            if limit:               # Google AI Pro's Argon quota is used up (16 Oct: after a 15-min Task)
                _collapse(pick)
                raise RuntimeError(f"your Argon limit on Google AI Pro is used up - it resets {limit.group(1)}")
            if item and _invoke(item):
                time.sleep(1)
            _collapse(pick)
        pick = find("Open mode picker")
        if not pick or "Argon" not in pick.name:
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


def _exe(worker: str) -> str | None:
    if worker == "gemini":                      # Google's Antigravity CLI (agy), on the user's Google AI plan
        found = shutil.which("agy") or next(iter(sorted((Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Packages")
                                                         .glob("Google.AntigravityCLI*/agy.exe"))), None)
        return str(found) if found else None
    return shutil.which("claude") or next((str(p) for p in (Path.home() / ".local" / "bin" / "claude.exe",
                                                             Path.home() / ".local" / "bin" / "claude") if p.exists()), None)


def _cmd(m: dict, exe: str, prompt: str, resume: bool) -> list[str]:
    if m.get("worker") == "gemini":
        # agy -p has no system-prompt flag, so the rules lead the first prompt.
        # File edits in the working folder are allowed; anything else needing
        # approval (shell commands) is refused in print mode; --sandbox limits the terminal.
        rules = WORKER_RULES + (" Never use the terminal (run_command is blocked and ends your turn): use list_dir, "
                                "view_file and write_to_file for files, and search_web / read_url_content to research.")
        cmd = [exe, "-p", prompt if resume and m.get("session") else f"{rules}\n\nThe job: {prompt}",
               "--output-format", "stream-json", "--disable-slash-commands", "--sandbox",
               "--print-timeout", f"{int(cfg.get('missions.max_minutes', 30))}m"]
        if cfg.get("missions.gemini_model"):
            cmd += ["--model", str(cfg.get("missions.gemini_model"))]
        return cmd + (["--conversation", m["session"]] if resume and m.get("session") else [])
    cmd = [exe, "-p", prompt, "--output-format", "stream-json", "--verbose",
           "--permission-mode", "dontAsk", "--model", str(cfg.get("missions.model", "sonnet")),
           "--strict-mcp-config", "--disable-slash-commands",
           "--tools", ",".join(cfg.get("missions.tools_available", ["Read", "Write", "Edit", "Glob", "Grep",
                                                                     "WebSearch", "WebFetch", "Bash"])),
           "--allowedTools", " ".join(cfg.get("missions.tools", ["Read", "Write", "Edit", "Glob", "Grep",
                                                                 "WebSearch", "WebFetch",
                                                                 "Bash(python *)", "Bash(node *)"])),
           "--append-system-prompt", WORKER_RULES]
    return cmd + (["--resume", m["session"]] if resume else ["--session-id", m["session"]])


def _parse(worker: str, e: dict) -> tuple[list[str], dict | None]:
    """One stream-json event -> (progress lines, the final result or None).
    The result is the same shape for both: ok, result, cost, turns, session."""
    if worker == "gemini":
        kind = e.get("event") or e.get("type") or ""       # agy: {"event": "step_update", "step_update": {...}}
        body = e.get(kind) if isinstance(e.get(kind), dict) else e
        if kind == "step_update" and body.get("step_type") == "tool" and body.get("state") == "ACTIVE":
            info = body.get("tool_info") or {}
            return [_describe(str(body.get("tool_name") or info.get("name") or ""), info.get("parameters") or {})], None
        if kind == "result":
            # A refused action (e.g. writing outside its folder) ends the turn with an empty response.
            denied = ", ".join(sorted({str(d.get("action")) for d in body.get("denied_actions") or []}))
            text = str(body.get("response") or body.get("error") or "").strip() or (
                f"Gemini stopped: it tried something it isn't allowed to ({denied})." if denied else "")
            # A final answer counts as done even with status ERROR: Google's connection sometimes drops on a
            # call after the answer is written (IPv6 timeouts to daily-cloudcode-pa.googleapis.com, 16 Oct).
            return [], {"ok": bool(str(body.get("response") or "").strip()),
                        "result": text, "cost": 0.0, "turns": int(body.get("num_turns") or 0),
                        "session": body.get("conversation_id")}
        return [], None
    if e.get("type") == "assistant":
        return [_describe(b.get("name", ""), b.get("input") or {})
                for b in (e.get("message") or {}).get("content") or [] if b.get("type") == "tool_use"], None
    if e.get("type") == "result":
        return [], {"ok": not e.get("is_error") and e.get("subtype") == "success",
                    "result": re.sub(r"\n+Some connectors.*", "", str(e.get("result") or ""), flags=re.S),
                    "cost": float(e.get("total_cost_usd") or 0), "turns": int(e.get("num_turns") or 0), "session": None}
    return [], None


def _describe(name: str, inp: dict) -> str:
    """A tool call as a line for the HUD: 'writing index.html'."""
    low = {k.lower(): v for k, v in inp.items()} if isinstance(inp, dict) else {}
    path = Path(str(next((low[k] for k in ("file_path", "path", "targetfile", "absolutepath", "file") if low.get(k)), ""))).name
    known = {"Write": f"writing {path}", "Edit": f"editing {path}", "Read": f"reading {path}",
             "Glob": "looking through the files", "Grep": "searching the files",
             "WebSearch": f"searching the web: {str(low.get('query', ''))[:60]}",
             "WebFetch": f"reading {str(low.get('url', ''))[:60]}",
             "Bash": "running " + re.sub(r'^cd\s+("[^"]*"|\S+)\s*&&\s*', "", str(low.get("command", "")))[:50]}
    if name in known:
        return known[name]
    n = name.lower()                            # Gemini's tool names (write_file, run_command...)
    if "search" in n and ("web" in n or "google" in n or low.get("query")):
        return f"searching the web: {str(low.get('query', ''))[:60]}"
    for words, verb in ((("write", "create"), "writing"), (("edit", "replace"), "editing"),
                        (("read", "view"), "reading")):
        if any(w in n for w in words):
            return f"{verb} {path}".strip()
    if any(w in n for w in ("command", "shell", "terminal")):
        return f"running {str(low.get('commandline') or low.get('command') or 'a command')[:50]}"
    if "url" in n or "fetch" in n or "browse" in n:
        return f"reading {str(low.get('url', ''))[:60]}"
    return name.replace("_", " ")


def _preview(folder: Path) -> str:
    """The file to show: index.html, else the newest .html, else the newest
    .md (research and Argon's answers), so the phone can read the result."""
    if (folder / "index.html").exists():
        return "index.html"
    for pattern in ("*.html", "*.pdf", "*.md"):
        files = sorted(folder.rglob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
        if files:
            return str(files[0].relative_to(folder)).replace("\\", "/")
    return ""


def _run(mid: str, prompt: str, resume: bool, retried: bool = False, review: bool = False) -> None:
    m = next(x for x in load() if x["id"] == mid)
    worker = m.get("worker", "claude")
    t0 = time.time()
    if worker == "argon":
        return _run_argon(mid, prompt, resume)
    if worker == "maps":
        return _run_maps(mid, prompt)
    exe = _exe(worker)
    if not exe:
        _update(mid, status="failed", result=f"{WORKERS[worker]}'s agent isn't installed on this PC.", ended=time.time())
        return
    folder = Path(m["folder"])
    # stdin closed: agy -p hangs on an idle open stdin pipe (antigravity-cli issue #76)
    proc = subprocess.Popen(_cmd(m, exe, prompt, resume), cwd=str(folder), stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace",
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    _procs[mid] = proc
    _update(mid, status="running", pid=proc.pid)
    limit = time.time() + float(cfg.get("missions.max_minutes", 30)) * 60
    final, other, did = None, [], 0
    (DATA / "mission_logs").mkdir(exist_ok=True)
    log = open(DATA / "mission_logs" / f"{mid}.jsonl", "a", encoding="utf-8")   # the raw stream, for "why did it fail"
    try:
        for line in proc.stdout:
            log.write(line)
            if time.time() > limit:
                proc.kill()
                _update(mid, progress={"t": time.time(), "text": "stopped: took longer than the time limit"})
                break
            try:
                e = json.loads(line)
            except ValueError:
                if line.strip():
                    other = (other + [line.strip()])[-5:]      # not JSON: an error message, e.g. "sign in"
                continue
            notes, res = _parse(worker, e)
            for n in notes:
                _note(mid, n)
                did += 1
            final = res or final
        proc.wait(timeout=30)
    except Exception as ex:
        _update(mid, progress={"t": time.time(), "text": f"error: {str(ex)[:120]}"})
    finally:
        _procs.pop(mid, None)
        log.close()
    m = next(x for x in load() if x["id"] == mid)
    if m.get("status") == "stopped":
        return
    ok = bool(final and final["ok"])
    if not ok and not did and not retried:      # failed before doing anything (a dropped connection): once more
        _note(mid, "that attempt failed before starting - trying again")
        if m.get("worker", "claude") == "claude" and not resume:
            _update(mid, session=str(uuid.uuid4()))   # the failed run may have claimed the old session id
        return _run(mid, prompt, resume, retried=True, review=review)
    result = str((final or {}).get("result") or "").strip() or " ".join(other)[:300]
    cost = round(float(m.get("cost", 0) or 0) + float((final or {}).get("cost") or 0), 3)
    turns = int(m.get("turns", 0) or 0) + int((final or {}).get("turns") or 0)
    session = (final or {}).get("session") or m.get("session")
    page = folder / "index.html"
    if (ok and not review and cfg.get("missions.review", True) and page.exists()
            and page.stat().st_mtime >= t0 - 5):
        # Built or changed a page: look at it in a real browser, and let the
        # worker fix what it sees before the user does. Workers said "I haven't
        # opened it in a browser", and the orb site's sections came up blank
        # until scrolled (16 Oct 2026).
        _update(mid, cost=cost, turns=turns, session=session, result=result)
        _note(mid, "checking the page in a real browser")
        try:
            shots, errors = _snap(folder)
        except Exception as e:
            shots, errors = [], []
            _note(mid, f"couldn't open it in a browser ({str(e)[:80]})")
        if shots:
            return _run(mid, _review_prompt(shots, errors), True, review=True)
    if review:                                  # the result the user hears is the build's, plus the check
        first = m.get("result") or ""
        if ok:                                  # it has been opened in a browser now
            first = re.sub(r"[^.\n]*\b(?:haven'?t|have not|didn'?t|did not)\s+(?:opened|tested|checked|viewed)\b[^.\n]*"
                           r"(?:browser|untested)[^.\n]*\.", "", first, flags=re.I)
            first = re.sub(r"[ \t]{2,}", " ", first).strip()
        result = (f"{first} Checked in a browser: {result}" if ok and result else
                  f"{first} (I couldn't double-check it in a browser.)").strip()
        ok = True                               # a failed check doesn't undo a finished build
    m = _update(mid, status="done" if ok else "failed", ended=time.time(), cost=cost, session=session,
                result=result or ("It didn't finish." if not ok else "Done."), preview=_preview(folder), turns=turns)
    try:
        from . import rules
        rules.log(f"mission {'done' if ok else 'failed'}: {m['title']}", "missions")
    except Exception:
        pass


def _run_maps(mid: str, query: str) -> None:
    """A chain step that reads Google Maps (mapsearch.py) into maps.md - the
    real local places, and which have no website of their own."""
    import os
    from . import mapsearch
    folder = Path(_get(mid)["folder"])
    _update(mid, status="running", pid=os.getpid())
    _note(mid, f"searching Google Maps: {query[:80]}")
    try:
        rows = mapsearch.search(query, int(cfg.get("maps.chain_places", 12)))
    except Exception as e:
        _update(mid, status="failed", ended=time.time(), result=f"Couldn't read Google Maps: {str(e)[:120]}.")
        return
    if not rows:
        _update(mid, status="failed", ended=time.time(), result=f"Google Maps found nothing for '{query}'.")
        return
    (folder / "maps.md").write_text(mapsearch.to_markdown(query, rows), encoding="utf-8")
    without = [r["name"] for r in rows if not r["website"]]
    _update(mid, status="done", ended=time.time(), turns=1, preview=_preview(folder),
            result=(f"Saved {len(rows)} places from Google Maps to maps.md (ratings, addresses, phones, websites). "
                    + (f"No website of their own: {', '.join(without[:8])}." if without else "All have websites.")))


def _snap(folder: Path) -> tuple[list[str], list[str]]:
    """index.html in a real browser (Playwright's Chromium): screenshots top to
    bottom at desktop and phone width, after scrolling through so fade-in
    sections have shown, plus any JavaScript errors. Saved in .review/ (never
    published)."""
    from playwright.sync_api import sync_playwright
    out = folder / ".review"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir()
    shots, errors = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)     # the installed Chrome, like upes.py
        try:
            for name, w, h in (("desktop", 1280, 800), ("phone", 390, 844)):
                pg = browser.new_page(viewport={"width": w, "height": h})
                pg.on("console", lambda msg: errors.append(msg.text[:200]) if msg.type == "error" else None)
                pg.on("pageerror", lambda e: errors.append(str(e)[:200]))
                pg.goto((folder / "index.html").as_uri(), wait_until="load", timeout=30000)
                pg.wait_for_timeout(1500)
                total = int(pg.evaluate("document.documentElement.scrollHeight"))
                for i, y in enumerate(range(0, max(total, 1), h)):
                    if i >= int(cfg.get("missions.review_shots", 5)):
                        break
                    pg.evaluate(f"window.scrollTo(0, {y})")
                    pg.wait_for_timeout(900)                 # reveal animations, lazy content
                    f = out / f"{name}-{i + 1}.png"
                    pg.screenshot(path=str(f))
                    shots.append(f".review/{f.name}")
                pg.close()
        finally:
            browser.close()
    return shots, sorted(set(errors))[:10]


def _review_prompt(shots: list[str], errors: list[str]) -> str:
    return ("Check your work before the user sees it. index.html was opened in a real browser and screenshotted "
            f"top to bottom, desktop (1280 px) and phone (390 px) width: {', '.join(shots)}. "
            f"JavaScript errors: {'; '.join(errors) if errors else 'none'}. Open and look at every screenshot. "
            "Fix anything broken, blank, unreadable, overlapping, cut off or clearly ugly, and any JavaScript error - "
            "but don't redesign what already works, and if it all looks right change nothing. Don't touch .review/. "
            "Reply with one short sentence: what you checked and what, if anything, you fixed.")


def _note(mid: str, text: str) -> None:
    _update(mid, progress={"t": time.time(), "text": text})
    try:
        from . import live
        live.note(f"mission: {text}")
    except Exception:
        pass


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "mission"


def _title(task: str) -> str:
    title = re.sub(r"^(?:please\s+)?(?:build|make|create|write|design|find|research)\s+(?:me\s+)?", "", task.strip(), flags=re.I)
    title = re.split(r"\s*[:;,.(—]\s*|\s+-\s+|\s+and\s+(?:then\s+)?(?:build|make|create|write|draft|design|code)\b",
                     title, maxsplit=1, flags=re.I)[0]                # the first clause / the first action
    title = " ".join(title.split()[:9])
    return title[:1].upper() + title[1:] if title else "Mission"


def start(task: str, worker: str = "claude", folder: str = "", parent: str = "") -> dict:
    """One worker on one job. folder and parent: a step of a chain, in the chain's folder."""
    worker = worker if worker in WORKERS else "claude"
    running = [m for m in load() if m["status"] in ("running", "starting") and m.get("worker", "claude") == worker]
    if len(running) >= int(cfg.get("missions.max_running", 1)):         # per worker: Claude and Gemini can run side by side
        raise RuntimeError(f"{WORKERS[worker]} is already on a mission ({running[0]['title']}) - wait for it or stop it")
    mid = uuid.uuid4().hex[:10]
    title = _title(task)
    path = Path(folder) if folder else ROOT / f"{dt.date.today().isoformat()}-{_slug(title)}-{mid[:4]}"
    path.mkdir(parents=True, exist_ok=True)
    m = {"id": mid, "title": title, "task": task, "folder": str(path), "worker": worker, "parent": parent,
         "session": str(uuid.uuid4()) if worker == "claude" else "",      # Gemini's id arrives with its result
         "status": "starting", "started": time.time(), "ended": None, "progress": [], "result": "",
         "cost": 0.0, "turns": 0, "preview": "", "followups": []}
    with _lock:
        ms = load()
        ms.append(m)
        _save(ms)
    threading.Thread(target=_run, args=(mid, task, False), daemon=True, name=f"mission-{mid}").start()
    return m


# ---------- chained missions ----------
#
# "Athena, find a cafe near me with no website and build them one": the
# planner model splits the goal into steps and picks a worker for each
# (Gemini researches on the Google plan, Claude builds), then each step runs
# as an ordinary mission in the chain's one folder, told what the earlier
# steps did and which files they left. Only the chain is announced.

PLAN_PROMPT = """You plan background jobs for Athena, a personal assistant. Split the user's goal into 1 to 4 steps, done one after another by AI workers in ONE shared folder. Each worker sees the files earlier steps saved there and a summary of what they did.

Workers:
- "gemini": research - web searches, reading websites, comparing options, finding facts, prices, places, writing reports and documents. Free on the user's Google plan: use it for research.
- "claude": building - websites (plain HTML/CSS/JS with index.html), programs, scripts, data processing, editing and improving files. Use it for anything that is built or coded.
- "argon": ONLY if the user explicitly asks for Argon.
- "maps": Athena reads Google Maps herself - real local places with ratings, addresses, phone numbers and whether each has its own website. Use it FIRST, and only, when the goal is about actual places or businesses (cafes near somewhere, shops with no website) - never for products, prices or general research. Its task is ONLY the Maps search text with the area, e.g. "cafes near Rajpur Road Dehradun" - no instructions. It saves maps.md for later steps.

Rules:
- Use as few steps as the goal needs; a simple goal is ONE step.
- Each task must be concrete and self-contained: say what to do, which earlier files to read, and which file(s) to save (e.g. research.md, index.html).
- Workers cannot send, email, message, post, publish, upload, buy or contact anyone. If the goal asks for that, end with a step that writes email_draft.md: a "To:" line (the address if one was found), a "Subject:" line, then a short, personal, honest message - for the user to review and send themselves. Athena can publish a finished site separately when the user says so.

Reply with JSON only: {"steps": [{"worker": "gemini", "task": "..."}]}"""


def plan(goal: str) -> list[dict]:
    """The goal as steps [{"worker", "task"}], from the planner model; a simple
    research-then-build split if the model can't be reached."""
    try:
        from . import planner
        reply, _ = planner._chat([{"role": "system", "content": PLAN_PROMPT},
                                  {"role": "user", "content": f"{goal}\n\n(The user lives in "
                                                              f"{cfg.get('assistant.city', 'Dehradun')}.)"}])
        data = json.loads(re.search(r"\{[\s\S]*\}", reply).group(0))
        steps = [{"worker": str(s.get("worker", "claude")).lower(), "task": str(s.get("task", "")).strip()}
                 for s in data.get("steps", []) if str(s.get("task", "")).strip()][:4]
        if steps:
            for s in steps:
                if s["worker"] not in WORKERS or (s["worker"] == "argon" and not re.search(r"\bargon\b", goal, re.I)):
                    s["worker"] = "gemini" if re.search(r"\b(research|find|compare|search|look up)\b", s["task"], re.I) else "claude"
            for s in steps:
                if s["worker"] == "maps":
                    s["task"] = _maps_query(s["task"])
            return steps
    except Exception as e:
        print(f"[mission plan: {str(e)[:120]} - using the simple plan]")
    research = re.search(r"\b(find|research|compare|look\s+up|search|best|which)\b", goal, re.I)
    build = re.search(r"\b(build|make|create|write|code|design|website|site|app|script|page)\b", goal, re.I)
    if research and build:
        return [{"worker": "gemini", "task": f"Research what this needs and save your findings to research.md: {goal}"},
                {"worker": "claude", "task": f"Using research.md in this folder, do the rest of this: {goal}"}]
    return [{"worker": "gemini" if research and not build else "claude", "task": goal}]


def _maps_query(task: str) -> str:
    """'Search Google Maps for cafes near Dehradun and list those without...'
    -> 'cafes near Dehradun': Maps wants the search, not instructions."""
    q = re.sub(r"^\s*(?:search|look\s+up|find|get|list)\s+(?:on\s+)?(?:google\s+)?(?:maps\s+)?(?:for\s+)?", "", task, flags=re.I)
    q = re.split(r"[.;\n]|\s+(?:and|then|to|that|which|who|with(?:out)?)\s+(?:list|save|have|has|don'?t|do\s+not|no|find|note|"
                 r"check|pick|write)\b|\s+(?:without|with\s+no|lacking)\b", q, maxsplit=1, flags=re.I)[0]
    return q.strip(" ,:\"'")[:80] or task[:80]


def _step_prompt(c: dict, i: int) -> str:
    steps = c["steps"]
    done = "\n".join(f"- Step {n + 1} ({WORKERS[s['worker']]}): {s['task']}\n  Result: {(s.get('result') or '')[:600]}"
                     for n, s in enumerate(steps[:i]))
    return (f"This is step {i + 1} of {len(steps)} of a bigger job. The whole goal: {c['task']}\n\n"
            + (f"Earlier steps (their files are in this folder - read them):\n{done}\n\n" if done else "")
            + f"Your step: {steps[i]['task']}")


def _get(mid: str) -> dict | None:
    return next((x for x in load() if x["id"] == mid), None)


def _set_step(cid: str, i: int, **fields) -> None:
    with _lock:
        ms = load()
        c = next((x for x in ms if x["id"] == cid), None)
        if c is not None and i < len(c.get("steps") or []):
            c["steps"][i].update(fields)
            _save(ms)


def _run_chain(cid: str, start_at: int = 0) -> None:
    import os
    c = _update(cid, status="running", pid=os.getpid())      # the runner lives in this process
    if not c.get("steps"):
        _note(cid, "planning the steps")
        steps = [{**s, "status": "waiting"} for s in plan(c["task"])]
        c = _update(cid, steps=steps)
        _note(cid, "plan: " + " -> ".join(f"{WORKERS[s['worker']]}" for s in steps))
    n = len(c["steps"])
    for i in range(start_at, n):
        step = _get(cid)["steps"][i]
        child, waited = None, time.time()
        while child is None:                                  # a worker busy with another mission: wait for it
            if _get(cid)["status"] == "stopped":
                return
            try:
                child = start(step["task"] if step["worker"] == "maps" else _step_prompt(_get(cid), i),
                              step["worker"], folder=c["folder"], parent=cid)
            except RuntimeError as e:
                if time.time() - waited > float(cfg.get("missions.max_minutes", 30)) * 60:
                    _update(cid, status="failed", ended=time.time(), result=f"Step {i + 1} never got started: {e}.")
                    return
                if time.time() - waited < 11:
                    _note(cid, f"waiting: {e}")
                time.sleep(10)
        _set_step(cid, i, status="running", mission=child["id"])
        _note(cid, f"step {i + 1}/{n} - {WORKERS[step['worker']]}: {step['task'][:90]}")
        last_t = 0.0
        while True:
            time.sleep(3)
            ch, c = _get(child["id"]), _get(cid)
            for p in ch.get("progress") or []:                 # the step's live lines, shown on the chain
                if p["t"] > last_t:
                    last_t = p["t"]
                    _update(cid, progress={"t": p["t"], "text": f"{WORKERS[step['worker']]}: {p['text']}"})
            if c["status"] == "stopped":
                stop(ch["id"])
                _set_step(cid, i, status="stopped")
                return
            if ch["status"] not in ("running", "starting"):
                break
        _set_step(cid, i, status=ch["status"], result=ch.get("result", ""))
        kids = [x for x in load() if x.get("parent") == cid]
        _update(cid, cost=round(sum(float(x.get("cost") or 0) for x in kids), 3),
                turns=sum(int(x.get("turns") or 0) for x in kids), preview=_preview(Path(c["folder"])))
        if ch["status"] != "done":
            _update(cid, status="failed", ended=time.time(),
                    result=f"Step {i + 1} of {n} ({WORKERS[step['worker']]}) got stuck: {ch.get('result', '')}"[:600])
            return
    c = _get(cid)
    _update(cid, status="done", ended=time.time(), preview=_preview(Path(c["folder"])),
            result=(c["steps"][-1].get("result") or "Done.") + (f" ({len(c['steps'])} steps)" if len(c["steps"]) > 1 else ""))


def start_chain(goal: str) -> dict:
    """A planned, multi-step mission: one sentence, several workers, one report."""
    cid = uuid.uuid4().hex[:10]
    title = _title(goal)
    folder = ROOT / f"{dt.date.today().isoformat()}-{_slug(title)}-{cid[:4]}"
    folder.mkdir(parents=True, exist_ok=True)
    c = {"id": cid, "title": title, "task": goal, "folder": str(folder), "worker": "chain", "steps": [],
         "status": "starting", "started": time.time(), "ended": None, "progress": [], "result": "",
         "cost": 0.0, "turns": 0, "preview": "", "followups": []}
    with _lock:
        ms = load()
        ms.append(c)
        _save(ms)
    threading.Thread(target=_run_chain, args=(cid,), daemon=True, name=f"chain-{cid}").start()
    return c


def follow_up(mid: str, text: str, worker: str = "") -> dict:
    m = next((x for x in load() if x["id"] == mid), None)
    if m is None:
        raise RuntimeError("no such mission")
    if m["status"] in ("running", "starting"):
        raise RuntimeError("it's still working - wait for it to finish first")
    if m.get("worker") == "chain":      # one more step, by the worker named or the last one
        w = worker if worker in WORKERS else (m["steps"][-1]["worker"] if m.get("steps") else "claude")
        _update(mid, status="starting", ended=None, followups=m.get("followups", []) + [text],
                steps=m.get("steps", []) + [{"worker": w, "task": text, "status": "waiting"}])
        threading.Thread(target=_run_chain, args=(mid, len(m.get("steps", []))), daemon=True, name=f"chain-{mid}").start()
        return m
    _update(mid, status="starting", ended=None, followups=m.get("followups", []) + [text])
    threading.Thread(target=_run, args=(mid, text, True), daemon=True, name=f"mission-{mid}").start()
    return m


def stop(mid: str) -> bool:
    m = next((x for x in load() if x["id"] == mid), None)
    if not m or m["status"] not in ("running", "starting"):
        return False
    _update(mid, status="stopped", ended=time.time())
    if m.get("worker") == "chain":      # its runner sees "stopped" and stops the step that's running
        for kid in [x for x in load() if x.get("parent") == mid and x["status"] in ("running", "starting")]:
            stop(kid["id"])
        return True
    if m.get("worker") in ("argon", "maps"):   # their pid is Athena's own: never killed
        return True
    proc = _procs.get(mid)
    pid = proc.pid if proc else m.get("pid")    # no proc here: the other process (voice / web) started it
    try:
        import psutil
        p = psutil.Process(int(pid))
        for c in p.children(recursive=True):    # shells and scripts Claude started
            c.kill()
        p.kill()
    except Exception:
        if proc:
            proc.kill()
    return True


def _latest(which: str = "", worker: str = "") -> dict | None:
    """The newest mission (chains count as any worker they used); chain steps never on their own."""
    ms = [m for m in load() if not m.get("parent") and (
        not worker or m.get("worker", "claude") == worker
        or (m.get("worker") == "chain" and worker in {s["worker"] for s in m.get("steps") or []}))]
    if which:
        words = set(re.findall(r"[a-z]{3,}", which.lower()))
        hit = [m for m in ms if words & set(re.findall(r"[a-z]{3,}", (m["title"] + " " + m["task"]).lower()))]
        ms = hit or ms
    return max(ms, key=lambda m: m["started"]) if ms else None


def _finished_line(m: dict) -> str:
    first = re.sub(r"[`*_#]", "", (m.get("result") or "").strip().split("\n")[0])[:220]
    if m.get("worker") == "chain":
        return (f"Your mission is done: {m['title']}. {first}" if m["status"] == "done"
                else f"Your mission got stuck: {m['title']}. {first}").strip()
    who = WORKERS.get(m.get("worker", "claude"), "Claude")
    return (f"{who} finished your mission: {m['title']}. {first}" if m["status"] == "done"
            else f"{who}'s mission got stuck: {m['title']}. {first}").strip()


def start_watch(announce) -> None:
    """In the voice process: say when a mission ends - whichever process ran
    it (the HUD starts them too) - or push it to the phone if you're away."""
    from . import presence

    app = {}

    def pass_on(m: dict) -> None:
        line = _finished_line(m)
        from .alerts import in_quiet_hours
        quiet = in_quiet_hours(dt.datetime.now(), *(cfg.get("alerts.quiet_hours") or ["23:00", "07:00"]))
        if not presence.away() and not quiet:       # at night: the phone only (missions spoke at 1 AM, 16 Oct)
            announce(line)
        if presence.away() or cfg.get("missions.push_to_phone", True):    # long jobs: the phone hears too
            try:
                from . import phone
                if "url" not in app:            # the phone app's address; the token stays on the phone
                    from .ui.web import _tailscale_https
                    app["url"] = _tailscale_https()
                phone.push(line, title=f"{WORKERS.get(m.get('worker', 'claude'), 'Your')} mission "
                           + ("done" if m["status"] == "done" else "stuck"), priority="default",
                           tags="rocket" if m["status"] == "done" else "warning",
                           click=f"{app['url']}/#missions" if app["url"] else "")
            except Exception as e:
                print(f"[missions push: {e}]")

    def loop() -> None:
        key = lambda m: m["id"] + str(m.get("ended"))
        told = set(load_json(TOLD, []) if TOLD.exists() else
                   [key(m) for m in load() if m["status"] in ("done", "failed")])   # first run: nothing old
        while True:
            time.sleep(5)
            try:
                new = [m for m in load() if m["status"] in ("done", "failed") and key(m) not in told
                       and not m.get("parent")]                  # a chain's steps: only the chain is announced
                for m in new:
                    told.add(key(m))
                    pass_on(m)
                if new:
                    save_json(TOLD, sorted(told))
            except Exception as e:
                print(f"[missions watch: {e}]")

    # A mission still marked running from before a restart has lost its worker.
    with _lock:
        ms = load()
        for m in ms:
            if m["status"] in ("running", "starting"):
                try:
                    import psutil
                    alive = bool(m.get("pid")) and psutil.pid_exists(int(m["pid"]))
                except Exception:
                    alive = False
                if not alive:
                    m.update(status="failed", ended=time.time(), result="Athena restarted while it was running.")
        _save(ms)
    threading.Thread(target=loop, daemon=True, name="missions-watch").start()


# ---------- publishing and outreach (always the user's yes, never on their own) ----------
#
# A built site goes live on GitHub Pages: one public repo (missions.publish.repo,
# cloned to missions.publish.dir) with each mission in a folder of its own,
# pushed with the git credentials this PC already has. Only web files go - the
# research notes, drafts and logs stay on the PC.

_WEB = {".html", ".htm", ".css", ".js", ".json", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico",
        ".woff", ".woff2", ".ttf", ".txt", ".xml", ".webmanifest"}


def _site_slug(m: dict) -> str:
    return _slug(m["title"])[:40] + "-" + m["id"][:4]


def publish_target(m: dict) -> tuple[Path | None, str]:
    folder = Path(str(cfg.get("missions.publish.dir", r"C:\claude code\athena-sites")))
    base = str(cfg.get("missions.publish.url", "")).rstrip("/")
    if not (folder / ".git").exists() or not base:
        return None, ""
    return folder, f"{base}/{_site_slug(m)}/"


def _site_index(repo: Path) -> None:
    """The repo's front page: a list of the sites published so far."""
    import html
    rows = []
    for d in sorted((p for p in repo.iterdir() if p.is_dir() and (p / "index.html").exists()),
                    key=lambda p: p.stat().st_mtime, reverse=True):
        title = re.search(r"<title>(.*?)</title>", (d / "index.html").read_text(encoding="utf-8", errors="replace"),
                          re.I | re.S)
        name = html.escape((title.group(1).strip() if title else d.name)[:90])
        rows.append(f'<li><a href="{html.escape(d.name)}/">{name}</a></li>')
    (repo / "index.html").write_text(
        "<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Athena sites</title><style>body{font:16px/1.6 system-ui,sans-serif;max-width:640px;margin:48px auto;"
        "padding:0 16px;color:#1d1b26;background:#faf9fd}a{color:#5b3fd6}h1{font-size:24px}</style>"
        "<h1>Sites built with Athena</h1><ul>" + "".join(rows or ["<li>Nothing published yet.</li>"]) + "</ul>",
        encoding="utf-8")


def publish(mid: str) -> str:
    """Copy a mission's web files into the Pages repo and push. Returns the URL."""
    m = _get(mid)
    if m is None:
        raise RuntimeError("no such mission")
    repo, url = publish_target(m)
    if repo is None:
        raise RuntimeError("publishing isn't set up yet (missions.publish in config.yaml)")
    src = Path(m["folder"])
    if not (src / "index.html").exists():
        raise RuntimeError("there's no index.html to publish")
    dest = repo / _site_slug(m)
    if dest.exists():
        shutil.rmtree(dest)
    for f in src.rglob("*"):
        rel = f.relative_to(src).parts
        if (f.is_file() and f.suffix.lower() in _WEB and "mission_logs" not in rel
                and not any(part.startswith(".") for part in rel)):      # .review/ screenshots stay home
            (dest / f.relative_to(src)).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest / f.relative_to(src))
    _site_index(repo)
    git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, timeout=120,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    git("add", "-A")
    out = git("commit", "-m", f"Publish {m['title']}")
    if out.returncode and "nothing to commit" not in out.stdout + out.stderr:
        raise RuntimeError(f"git commit failed: {(out.stderr or out.stdout)[:160]}")
    out = git("push")
    if out.returncode:
        raise RuntimeError(f"git push failed: {(out.stderr or out.stdout)[:160]}")
    _update(mid, published=url)
    _note(mid, f"published at {url}")
    return url


def draft_file(m: dict) -> Path | None:
    """The outreach draft a chain wrote (email_draft.md and the like), if any."""
    files = sorted(Path(m["folder"]).glob("*draft*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def open_draft(mid: str) -> str:
    """Open the mission's email draft in the mail app, filled in. Sending is the user's own press."""
    import os
    import urllib.parse
    m = _get(mid)
    f = draft_file(m) if m else None
    if f is None:
        raise RuntimeError("this mission has no email draft")
    text = f.read_text(encoding="utf-8")
    to = re.search(r"^\**to:?\**:?\s*(\S+@\S+)", text, re.I | re.M)
    subject = re.search(r"^\**subject:?\**:?\s*(.+)$", text, re.I | re.M)
    body = re.sub(r"^\**(?:to|subject):?\**:?.*$\n?", "", text, flags=re.I | re.M).strip()
    body = re.sub(r"^#+\s*", "", body, flags=re.M)
    query = urllib.parse.urlencode({k: v for k, v in (("subject", subject.group(1).strip() if subject else ""),
                                                       ("body", body[:1800])) if v}, quote_via=urllib.parse.quote)
    os.startfile(f"mailto:{to.group(1).strip('<>') if to else ''}?{query}")
    return f"Opened the draft in your mail app{' to ' + to.group(1) if to else ''} - read it, then send it yourself."


# ---------- tools ----------

@tool(tags=["missions"], timeout=30)
def mission_start(task: str, worker: str = "claude") -> str:
    """Start a mission: a big job (build a website, write a program or script,
    make a document or a set of files, research something in depth) done in
    the background by an AI agent, in its own folder. Athena says when it's
    done. Use for "have Claude build...", "ask Gemini to make...", "start a
    mission to...". Not for quick questions.

    task: the whole job, in the user's words, with every detail they gave
    worker: "claude" (Claude Code), "gemini" (Google's agent, Gemini Flash) or "argon" (Gemini 4 Argon in the Gemini app) - whichever the user named; claude if they named neither
    """
    try:
        m = start(task, str(worker).lower().strip())
    except RuntimeError as e:
        return f"Can't start it: {e}."
    return (f"On it - {WORKERS[m['worker']]} is working on \"{m['title']}\" in the background. I'll tell you "
            "when it's ready; you can watch it on the Missions page.")


@tool(tags=["missions"], timeout=30)
def mission_chain(goal: str) -> str:
    """Start a big multi-step mission when the user names no particular AI:
    Athena plans the steps and picks a worker for each (Gemini researches,
    Claude builds), runs them one after another in one folder, and reports
    once at the end. Use for "start a mission to...", and for goals that need
    research AND building, e.g. "find a cafe near me with no website and
    build them one". If the user names Claude, Gemini or Argon, use
    mission_start instead.

    goal: the whole goal, in the user's words, with every detail they gave
    """
    c = start_chain(goal)
    return (f"On it - planning \"{c['title']}\" and handing each step to the right worker. "
            "I'll tell you when it's all done; you can watch it on the Missions page.")


@tool(tags=["missions"], destructive=True, timeout=120)
def mission_publish(which: str = "") -> str:
    """Put a mission's website on the public internet (GitHub Pages) and give
    the link. Public: anyone with the link can see it - for a site about a real
    business, only with the owner's OK. Use for "publish it", "put the site online".

    which: part of the mission's name, or empty for the latest
    """
    m = _latest(which)
    if not m:
        return "There's no mission to publish."
    try:
        return f"\"{m['title']}\" is live at {publish(m['id'])} - it can take a minute to appear."
    except RuntimeError as e:
        return f"Couldn't publish it: {e}."


@tool(tags=["missions"])
def mission_draft(which: str = "") -> str:
    """Open a mission's outreach email draft in the mail app, filled in, for the
    user to read and send themselves. Never sends anything.

    which: part of the mission's name, or empty for the latest
    """
    m = _latest(which)
    if not m:
        return "There's no mission with a draft."
    try:
        return open_draft(m["id"])
    except RuntimeError as e:
        return f"Can't: {e}."


@tool(tags=["missions"])
def mission_status(which: str = "") -> str:
    """How a mission is going: running or done, what it's doing now, the result.

    which: part of its name, or empty for the latest
    """
    m = _latest(which)
    if not m:
        return 'No missions yet. Say something like "have Claude build me a one-page website for my orb".'
    mins = int(((m.get("ended") or time.time()) - m["started"]) / 60)
    now = (m.get("progress") or [{}])[-1].get("text", "")
    if m["status"] in ("running", "starting"):
        return f"\"{m['title']}\" is running ({mins} min so far) - right now: {now or 'getting started'}."
    return f"\"{m['title']}\" is {m['status']} after {mins} min. {m.get('result', '')[:400]}"


@tool(tags=["missions"])
def mission_stop(which: str = "") -> str:
    """Stop a running mission.

    which: part of its name, or empty for the running one
    """
    run = [m for m in load() if m["status"] in ("running", "starting") and not m.get("parent")]
    m = _latest(which) if which else (run[0] if run else None)
    if m and stop(m["id"]):
        return f"Stopped \"{m['title']}\"."
    return "No mission is running."


@tool(tags=["missions"], timeout=30)
def mission_follow_up(instruction: str, which: str = "", worker: str = "") -> str:
    """Change or continue a finished mission: "make the header darker", "add a
    contact page". Claude carries on in the same folder with what it built.

    instruction: what to change or add
    which: part of the mission's name, or empty for the latest
    worker: "claude", "gemini" or "argon" if the user named one, else empty
    """
    m = _latest(which, str(worker).lower().strip() if str(worker).lower().strip() in WORKERS else "")
    if not m:
        return "There's no mission to continue."
    try:
        follow_up(m["id"], instruction, str(worker).lower().strip())
    except RuntimeError as e:
        return f"Can't yet: {e}."
    return f"On it - {WORKERS.get(m.get('worker', 'claude'), 'Claude')} is updating \"{m['title']}\". I'll tell you when it's done."


if __name__ == "__main__":
    assert _describe("Write", {"file_path": r"C:\x\index.html"}) == "writing index.html"
    assert _describe("WebSearch", {"query": "cafe menus"}).startswith("searching the web")
    assert _slug("A Cafe: Brew Lab!") == "a-cafe-brew-lab"
    assert _describe("write_to_file", {"TargetFile": "C:/x/index.html"}) == "writing index.html"
    assert _describe("search_web", {"query": "esp32 mics"}) == "searching the web: esp32 mics"
    assert _parse("gemini", {"event": "result", "result": {"status": "SUCCESS", "response": "Made it.", "conversation_id": "c1",
                                                           "num_turns": 3}})[1] == {"ok": True, "result": "Made it.", "cost": 0.0,
                                                                                     "turns": 3, "session": "c1"}
    assert not _parse("gemini", {"event": "result", "result": {"status": "SUCCESS", "response": "",
                                                               "denied_actions": [{"action": "write_file"}]}})[1]["ok"]
    assert _parse("gemini", {"event": "result", "result": {"status": "ERROR", "response": "Wrote mics.md.",
                                                           "error": "API error (attempt 1): request failed"}})[1]["ok"]
    assert not _parse("gemini", {"event": "result", "result": {"status": "ERROR", "response": "",
                                                               "error": "API error (attempt 1): request failed"}})[1]["ok"]
    assert _parse("gemini", {"event": "step_update", "step_update": {"step_type": "tool", "state": "ACTIVE", "tool_name": "write_to_file",
                                                                     "tool_info": {"parameters": {"TargetFile": "a/b.md"}}}})[0] == ["writing b.md"]
    assert _parse("claude", {"type": "result", "subtype": "success", "result": "ok", "total_cost_usd": 0.1, "num_turns": 2})[1]["cost"] == 0.1
    assert _describe("Bash", {"command": 'cd "C:/x y" && python app.py'}) == "running python app.py"
    assert _finished_line({"title": "Site", "status": "done", "result": "Open `index.html`."}) == "Claude finished your mission: Site. Open index.html."
    assert _finished_line({"title": "Cafe site", "status": "done", "worker": "chain", "result": "Built it."}) == \
        "Your mission is done: Cafe site. Built it."
    p = _step_prompt({"task": "find a cafe and build a site", "steps": [
        {"worker": "gemini", "task": "find one; save research.md", "result": "Found Brew Lab."},
        {"worker": "claude", "task": "build index.html from research.md"}]}, 1)
    assert "step 2 of 2" in p and "Found Brew Lab." in p and "Your step: build index.html" in p
    assert _title("Find a cafe near me with no website and build them one") == "A cafe near me with no website"
    assert _maps_query("Search for cafes near Dehradun and list those that have no website. Save maps.md") == "cafes near Dehradun"
    assert _maps_query("cafes near Rajpur Road Dehradun") == "cafes near Rajpur Road Dehradun"
    assert _maps_query("Search Google Maps for gyms in Dehradun without a website") == "gyms in Dehradun"
    print("missions: checks pass")
