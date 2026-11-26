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
from .missions_store import (  # noqa: F401 - split out 31 Oct 2026; still importable from here
    ROOT,
    WORKERS,
    _title,
    FILE,
    _lock,
    load,
    _save,
    _update,
    _preview,
    _note,
    _slug,
    _get,
)
from .missions_argon import (  # noqa: F401 - split out 31 Oct 2026; still importable from here
    _gemini_app,
    _wake_app,
    _clip,
    _invoke,
    _collapse,
    _focus_box,
    _run_argon,
    _export_canvas,
    _save_dialog,
)
from .missions_publish import (  # noqa: F401 - split out 31 Oct 2026; still importable from here
    _WEB,
    _site_slug,
    publish_target,
    _site_index,
    publish,
    draft_file,
    open_draft,
)
from .missions_chain import (  # noqa: F401 - split out 31 Oct 2026; still importable from here
    PLAN_PROMPT,
    plan,
    _maps_query,
    _step_prompt,
    _set_step,
    _run_chain,
    start_chain,
)

_procs: dict[str, subprocess.Popen] = {}
TOLD = DATA / "missions_told.json"   # finished missions already announced (voice process only)

WORKER_RULES = (
    "You are Athena's worker: you do one job for the user, in the current folder, without asking questions. "
    "Work only inside the current folder. Never send, post, publish, push, email, message or buy anything, "
    "and never contact anyone. Prefer simple, dependency-free solutions (plain HTML/CSS/JS for websites, "
    "with an index.html). When done, reply with two or three plain sentences: what you made and the main "
    "file to open. If something can't be done, say so plainly.")


# ---------- Gemini Argon, through the Gemini desktop app ----------
#
# On Google AI Pro, Argon exists only in the Gemini app - not in agy, not in
# the API. So this worker drives the app's window: new chat, Argon picked,
# Task mode, the job pasted in, then it waits for "Stop response" to go and
# "Copy" to appear (both by name, via UI Automation), and copies the answer
# out with the app's own Copy button. It needs the PC unlocked and takes the
# foreground for a few seconds while it types.


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
           "--append-system-prompt", WORKER_RULES,
           "--max-budget-usd", f"{float(cfg.get('missions.max_cost_usd', 1.5)):.2f}"]    # Claude Code stops itself there
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
        capped = "budget" in str(e.get("subtype", ""))
        return [], {"ok": not e.get("is_error") and e.get("subtype") == "success",
                    "result": (f"Stopped at the ${float(cfg.get('missions.max_cost_usd', 1.5)):.2f} limit for one mission "
                               "(missions.max_cost_usd). Say what to finish, or raise the limit." if capped else
                               re.sub(r"\n+Some connectors.*", "", str(e.get("result") or ""), flags=re.S)),
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


def _run(mid: str, prompt: str, resume: bool, retried: bool = False, review: bool = False) -> None:
    m = next(x for x in load() if x["id"] == mid)
    worker = m.get("worker", "claude")
    t0 = time.time()
    if worker == "argon":
        return _run_argon(mid, prompt, resume)
    if worker == "maps":
        return _run_maps(mid, prompt)
    if worker == "video":
        return _run_video(mid, prompt)
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
    changed = page.exists() and any(            # the page or anything it loads: a fix to app.js counts (17 Oct 2026)
        f.stat().st_mtime >= t0 - 5 for f in folder.rglob("*")
        if f.suffix.lower() in (".html", ".js", ".css") and ".review" not in f.parts)
    if ok and not review and cfg.get("missions.review", True) and changed:
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


def _run_video(mid: str, ask: str) -> None:
    """A chain step that films the animation page an earlier step built into
    video.mp4 (videorender.py) - the user asked for a video, so they get one."""
    import os
    from . import videorender
    folder = Path(_get(mid)["folder"])
    _update(mid, status="running", pid=os.getpid())
    secs = re.search(r"(\d+(?:\.\d+)?)\s*(?:s\b|sec|second)", ask or "", re.I)
    _note(mid, "filming the animation into an MP4")
    try:
        info = videorender.render(folder, seconds=float(secs.group(1)) if secs else None,
                                  progress=lambda line: _note(mid, line))
    except Exception as e:
        _update(mid, status="failed", ended=time.time(), result=f"Couldn't film it: {str(e)[:150]}.")
        return
    _update(mid, status="done", ended=time.time(), turns=1, preview=_preview(folder),
            result=(f"Filmed {info['file']}: {info['seconds']:g} seconds, {info['width']}x{info['height']} at "
                    f"{info['fps']} fps" + ("" if info["exact"] else " (recorded live - the page had no renderFrame)") + "."))


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


def month_spend() -> float:
    """What missions have cost on the Claude plan this calendar month (Gemini, Argon and Maps are free)."""
    first = dt.datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
    # a chain's own cost only adds up its steps - counting both doubled it and tripped the cap early (20 Oct 2026)
    return round(sum(float(m.get("cost") or 0) for m in load()
                     if m.get("started", 0) >= first and m.get("worker") != "chain"), 2)


def _check_budget(worker: str) -> None:
    cap = float(cfg.get("missions.monthly_cap_usd", 15))
    if worker == "claude" and month_spend() >= cap:
        raise RuntimeError(f"this month's mission budget is used up (${month_spend():.2f} of ${cap:.0f}) - "
                           "Gemini still works, or raise missions.monthly_cap_usd")


def start(task: str, worker: str = "claude", folder: str = "", parent: str = "", loop: str = "") -> dict:
    """One worker on one job. folder and parent: a step of a chain, in the chain's folder.
    loop: a run of a loop (loops.py) - quiet; only the loop's outcome is told."""
    worker = worker if worker in WORKERS else "claude"
    _check_budget(worker)
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
         "cost": 0.0, "turns": 0, "preview": "", "followups": [], "loop": loop}
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


def follow_up(mid: str, text: str, worker: str = "") -> dict:
    m = next((x for x in load() if x["id"] == mid), None)
    if m is None:
        raise RuntimeError("no such mission")
    if m["status"] in ("running", "starting"):
        raise RuntimeError("it's still working - wait for it to finish first")
    _check_budget(m.get("worker", "claude") if m.get("worker") != "chain" else worker or "claude")
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
    if m.get("worker") in ("argon", "maps", "video"):   # their pid is Athena's own: never killed
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


_SITE_FILLER = {"the", "my", "our", "website", "site", "page", "landing", "web", "home", "homepage", "webpage", "new", "one",
                "video", "clip", "animation", "reel", "movie", "that", "made"}


def find_site(which: str) -> tuple[str, str] | None:
    """(title, link or file) of the user's own site that matches every word of
    `which` - a finished mission (its published link, else its page) or a
    folder in athena-sites (a video there wins over its page) - the newest wins."""
    words = set(re.findall(r"[a-z0-9]{3,}", which.lower())) - _SITE_FILLER
    if not words:
        return None
    hits = []                                  # (when, title, where)
    for m in load():
        text = set(re.findall(r"[a-z0-9]{3,}", (m["title"] + " " + m["task"]).lower()))
        if m.get("parent") or m["status"] != "done" or not words <= text:
            continue
        page = Path(m["folder"]) / (m.get("preview") or "index.html")
        where = m.get("published") or (str(page) if page.suffix.lower() in (".html", ".htm") and page.exists() else "")
        if where:
            hits.append((m.get("ended") or m["started"], m["title"], where))
    sites = Path(r"C:\claude code\athena-sites")
    for d in (p for p in sites.iterdir() if p.is_dir() and not p.name.startswith(".")) if sites.is_dir() else []:
        if not words <= set(re.findall(r"[a-z0-9]{3,}", d.name.lower())):
            continue
        made = sorted(d.glob("*.mp4"), key=lambda p: p.stat().st_mtime) or [p for p in (d / "index.html",) if p.exists()]
        if made:
            hits.append((made[-1].stat().st_mtime, d.name.replace("-", " "), str(made[-1])))
    if not hits:
        return None
    _, title, where = max(hits)
    return title, where


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
                       and not m.get("parent") and not m.get("loop")]   # a chain's steps, a loop's runs: only the whole is told
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
def site_open(which: str) -> str:
    """Open one of the user's own websites or videos - one a mission built or
    published, or a page or video in athena-sites. Use for "open my orb
    website", "show me the landing page", "play my LOQ video".

    which: the site's name, e.g. "orb" or "desk orb"
    """
    import os
    hit = find_site(which)
    if not hit:
        return f"I don't have a site of yours called {which}."
    title, where = hit
    if not where.startswith("http") and Path(where).suffix.lower() not in (
            ".html", ".htm", ".mp4", ".webm", ".mov", ".pdf", ".png", ".jpg", ".jpeg", ".gif"):
        return f"That one is a {Path(where).suffix} file - open it from its folder: {Path(where).parent}"
    os.startfile(where)
    return f"Opened {title}" + (" - your live site." if where.startswith("http") else " - it's only on this PC, not published.")


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
    assert "--max-budget-usd" in _cmd({"worker": "claude", "session": "s"}, "claude", "x", False)
    got = _parse("claude", {"type": "result", "subtype": "error_max_budget_usd", "is_error": True, "result": ""})[1]
    assert not got["ok"] and "limit for one mission" in got["result"]
    assert _maps_query("Search Google Maps for gyms in Dehradun without a website") == "gyms in Dehradun"
    print("missions: checks pass")
