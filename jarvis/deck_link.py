"""Athena Deck (deck/, built 20 Oct 2026): a board of real Claude Code, Gemini
and PowerShell consoles. Athena drives it by voice through its local API
(127.0.0.1:8430, the Deck's own token in deck/data/token.txt):

    "open the deck"                               deck_open
    "open two Claude consoles on the orb site"   deck_spawn
    "what are my consoles doing"                  deck_status
    "tell console 2 to run the tests"             deck_send
    "what does the Gemini console say"            deck_read
    "show me console 1"                           deck_focus

Consoles that need you show up in attention.py (said aloud, pushed, on the
HUD's Needs-you panel); their Claude spend in usage.py.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from .config import DATA
from .registry import tool
from .store import load_json, save_json

ROOT = Path(__file__).resolve().parents[1]
DECK = ROOT / "deck"
PORT = 8430
# Consoles Athena started on a task, for the HUD's timer chip and "Claude
# finished ..." (Hedwig's "Claude Code · Building your website · 11s", 23 Oct
# 2026): {console id: {"title", "who", "started", "done"}}.
JOBS = DATA / "deck_jobs.json"
ACTIVE = ("starting", "working", "needs")
SETTLE = 20          # seconds: a console resting before this is still starting, not done
# A new build gets a folder of its own (next to the missions'), not whatever
# project the board happens to have open.
_NEW_BUILD = re.compile(r"^\W*(?:please\s+)?(?:build|make|create|design|develop|code|program|write|generate)\b", re.I)
WORDS = {"one": 1, "first": 1, "two": 2, "second": 2, "three": 3, "third": 3, "four": 4, "fourth": 4, "five": 5,
         "fifth": 5, "six": 6, "sixth": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "a": 1, "an": 1}
KIND_WORDS = {"codex": "codex", "chatgpt": "codex", "gpt": "codex", "claude": "claude", "cloud": "claude", "clawed": "claude", "gemini": "gemini", "jemini": "gemini",
              "antigravity": "gemini", "powershell": "shell", "shell": "shell", "terminal": "shell"}
LABEL = {"claude": "Claude", "codex": "Codex", "gemini": "Antigravity", "shell": "PowerShell", "custom": "custom"}


def _token() -> str:
    return (DECK / "data" / "token.txt").read_text(encoding="utf-8").strip()


def api(method: str, path: str, body: dict | None = None, timeout: float = 10) -> dict:
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", method=method,
                                 data=json.dumps(body).encode("utf-8") if body is not None else None,
                                 headers={"X-Token": _token(), "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _ping(timeout: float = 2) -> dict:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/ping", timeout=timeout) as r:
            d = json.load(r)
            return d if d.get("app") == "athena-deck" else {}
    except Exception:
        return {}


def running(timeout: float = 2) -> bool:
    return bool(_ping(timeout))


def launch() -> bool:
    """Open the Deck window (one only - if it's open, it comes forward). True once a window is
    connected: deck/__main__ starts the server, then the window."""
    if _ping().get("windows"):
        api("POST", "/api/focus", {})
        return True
    exe = Path(sys.executable).with_name("pythonw.exe")
    subprocess.Popen([str(exe if exe.exists() else sys.executable), "-m", "deck"], cwd=str(ROOT),
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0))
    for _ in range(60):
        time.sleep(0.5)
        if _ping().get("windows"):
            return True
    return running()


def consoles(st: dict) -> list[dict]:
    """Every console, numbered the way you'd say it: 1, 2, 3 in the order they were opened."""
    out = []
    for i, n in enumerate(sorted((n for n in st["nodes"] if n["type"] == "console"), key=lambda n: n.get("created", 0)), 1):
        s = st.get("status", {}).get(n["id"], {})
        out.append({**n, "n": i, "state": s.get("state", "stopped"), "detail": s.get("detail", "")})
    return out


def find(st: dict, which: str) -> dict | None:
    """'2', 'console two', 'the gemini one', 'orb builder', 'the one that needs me' -> a console."""
    cs = consoles(st)
    if not cs:
        return None
    w = (which or "").lower().strip()
    if not w:
        return max(cs, key=lambda c: c.get("created", 0))
    if re.search(r"needs?\s+(?:me|you)|waiting|stuck", w):
        hit = [c for c in cs if c["state"] == "needs"]
        if hit:
            return hit[0]
    m = re.search(r"\b(\d+|" + "|".join(k for k in WORDS if len(k) > 2) + r")\b", w)
    kind = next((v for k, v in KIND_WORDS.items() if re.search(rf"\b{k}\b", w)), "")
    if m:
        num = int(m.group(1)) if m.group(1).isdigit() else WORDS[m.group(1)]
        pool = [c for c in cs if c["kind"] == kind] if kind else cs           # "claude 2" = the second Claude
        if 0 < num <= len(pool):
            return pool[num - 1]
    words = set(re.findall(r"[a-z0-9]{3,}", w)) - {"console", "the", "one", "agent", "terminal"} - set(KIND_WORDS)
    titled = [c for c in cs if words and words <= set(re.findall(r"[a-z0-9]{3,}", c["title"].lower()))]
    if titled:
        return titled[0]
    if kind:
        same = [c for c in cs if c["kind"] == kind]
        if same:
            return max(same, key=lambda c: c.get("created", 0))
    return None


def _folder(hint: str, st: dict | None = None) -> str:
    """A project folder from how you'd say it: a path, a workspace's name, or a folder under C:\\claude code."""
    hint = (hint or "").strip(" .")
    if not hint:
        return ""
    if Path(hint).is_dir():
        return str(Path(hint))
    words = set(re.findall(r"[a-z0-9]{3,}", hint.lower())) - {"the", "my", "site", "website", "project", "folder", "repo"}
    for w in (st or {}).get("workspaces", []):
        if words and words & set(re.findall(r"[a-z0-9]{3,}", (w["name"] + " " + w["folder"]).lower())):
            return w["folder"]
    base = Path(r"C:\claude code")
    pool = [p for d in (base, base / "athena-sites", base / "athena-jobs") if d.is_dir() for p in d.iterdir() if p.is_dir()]
    hits = [p for p in pool if words and words <= set(re.findall(r"[a-z0-9]{3,}", p.name.lower()))] or \
           [p for p in pool if words and words & set(re.findall(r"[a-z0-9]{3,}", p.name.lower()))]
    return str(max(hits, key=lambda p: p.stat().st_mtime)) if hits else ""


def _workspace(folder: str, st: dict) -> str:
    """The workspace for a folder - made if there isn't one."""
    for w in st["workspaces"]:
        if Path(w["folder"]).resolve() == Path(folder).resolve():
            return w["id"]
    return api("POST", "/api/workspaces", {"folder": folder, "name": Path(folder).name})["id"]


def summary(st: dict) -> str:
    cs = consoles(st)
    if not cs:
        return "The Deck is open, with no consoles yet."
    word = {"needs": "needs you", "working": "working", "resting": "resting", "starting": "starting", "exited": "stopped"}
    parts = [f"{c['n']}, {c['title']}, {word.get(c['state'], c['state'])}" + (f" - {c['detail'][:90]}" if c["state"] == "needs" and c["detail"] else "")
             for c in cs]
    needs = sum(1 for c in cs if c["state"] == "needs")
    return (f"{len(cs)} console{'s' if len(cs) > 1 else ''}{f', {needs} need you' if needs else ''}: " + "; ".join(parts) + ".")


def brief() -> dict:
    """For the HUD and attention: the consoles, or running=False - never slow when the Deck is closed."""
    if not running(0.4):
        return {"running": False}
    try:
        st = api("GET", "/api/state", timeout=2)
    except Exception:
        return {"running": False}
    return {"running": True, "consoles": [{k: c.get(k) for k in ("id", "n", "title", "kind", "state", "detail", "ws")}
                                          | {"since": st["status"].get(c["id"], {}).get("since", 0)} for c in consoles(st)],
            "costs": st.get("costs", {}), "workspaces": [{"id": w["id"], "name": w["name"]} for w in st["workspaces"]]}


# ---------- jobs: consoles Athena started on a task ----------

def _new_project(task: str) -> str:
    from . import missions
    import uuid
    path = missions.ROOT / f"{time.strftime('%Y-%m-%d')}-{missions._slug(missions._title(task))}-{uuid.uuid4().hex[:4]}"
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def _track(made: list[dict], task: str, who: str) -> None:
    from . import missions
    jobs = {k: v for k, v in _jobs_file().items() if time.time() - v["started"] < 86400}     # a day is plenty
    for c in made:
        jobs[c["id"]] = {"title": missions._title(task), "who": who, "started": time.time(), "done": 0}
    save_json(JOBS, jobs)


def _jobs_file() -> dict:
    return load_json(JOBS, {}) if JOBS.exists() else {}


def jobs() -> list[dict]:
    """Every job not yet told as done, with its console's state now ("gone" if
    the console was closed). Free when there are none: the Deck isn't asked."""
    recs = {k: v for k, v in _jobs_file().items() if not v.get("done")}
    if not recs:
        return []
    b = brief()
    states = {c["id"]: c["state"] for c in b.get("consoles", [])} if b.get("running") else {}
    return [{**v, "id": k, "state": states.get(k, "gone")} for k, v in recs.items()]


def job_active(j: dict) -> bool:
    """Still on it - or resting only because it hasn't started yet."""
    return j["state"] in ACTIVE or (j["state"] == "resting" and time.time() - j["started"] < SETTLE)


def mark_done(console: str) -> None:
    recs = _jobs_file()
    if console in recs:
        recs[console]["done"] = time.time()
        save_json(JOBS, recs)


def last_build() -> float:
    """When Athena last started a console on a task (0 if never, or over a day ago)."""
    return max((v["started"] for v in _jobs_file().values() if time.time() - v["started"] < 86400), default=0.0)


# ---------- tools ----------

@tool(tags=["deck"], timeout=40)
def deck_open(folder: str = "") -> str:
    """Open Athena Deck - the board of Claude, Gemini and PowerShell consoles -
    optionally on a project folder. Use for "open the deck", "open my board".

    folder: a project to open it on, e.g. "orb site" (optional)
    """
    if not launch():
        return "The Deck didn't start."
    if folder:
        st = api("GET", "/api/state")
        path = _folder(folder, st)
        if not path:
            return f"The Deck is open, but I couldn't find a folder for {folder}."
        api("PATCH", f"/api/workspaces/{_workspace(path, st)}", {"activate": True})
        api("POST", "/api/focus", {})
        return f"The Deck is open on {Path(path).name}."
    return "The Deck is open."


@tool(tags=["deck"], timeout=60)
def deck_spawn(kind: str = "claude", count: int = 1, folder: str = "", task: str = "") -> str:
    """Open consoles on the Deck: Claude Code, Gemini or PowerShell, one or a
    team, on a project folder, optionally starting on a task. Use for "open two
    Claude consoles on the orb site", "spin up a Gemini agent to research X".

    kind: claude, codex (OpenAI Codex, ChatGPT), gemini (Antigravity) or shell
    count: how many (1-6)
    folder: the project, e.g. "orb site" (empty = the board's current workspace)
    task: a first instruction each agent starts on (optional - uses your limits)
    """
    kind = KIND_WORDS.get(kind.lower().strip(), kind.lower().strip())
    if kind not in ("claude", "codex", "gemini", "shell"):
        kind = "claude"
    count = max(1, min(6, int(count or 1)))
    if not launch():
        return "The Deck didn't start."
    st = api("GET", "/api/state")
    path = _folder(folder, st) if folder else ""
    if folder and not path:
        return f"I couldn't find a folder for {folder} - say its name or path."
    if not path and task and kind != "shell" and _NEW_BUILD.match(task):
        path = _new_project(task)
    wid = _workspace(path, st) if path else st["active"]
    cwd = path or next(w["folder"] for w in st["workspaces"] if w["id"] == wid)
    api("PATCH", f"/api/workspaces/{wid}", {"activate": True})
    made = api("POST", "/api/composer", {"ws": wid, "cwd": cwd, "prompt": task if kind != "shell" else "",
                                         "slots": [{"kind": kind}] * count})["made"]
    if made:
        api("POST", "/api/focus", {"id": made[0]["id"]})
        if task and kind != "shell":
            _track(made, task, LABEL[kind])
    what = f"{len(made)} {LABEL[kind]} console{'s' if len(made) > 1 else ''}"
    return f"Opened {what} on {Path(cwd).name}" + (f", starting on: {task}" if task and kind != "shell" else "") + "."


@tool(tags=["deck"], timeout=40)
def deck_browse(site: str, folder: str = "") -> str:
    """Open a browser tile on the Deck - any site (Google, YouTube, GitHub, a
    localhost preview) or a search. Use for "open google on the deck", "open
    youtube in the deck".

    site: an address or what to search for
    folder: which project's workspace (optional - the current one)
    """
    if not launch():
        return "The Deck didn't start."
    st = api("GET", "/api/state")
    wid = st["active"]
    if folder:
        path = _folder(folder, st)
        if path:
            wid = _workspace(path, st)
            api("PATCH", f"/api/workspaces/{wid}", {"activate": True})
    url = to_url(site)
    title = re.sub(r"^www\.", "", url.split("/")[2]) if "//" in url else site
    n = api("POST", "/api/nodes", {"type": "web", "ws": wid, "url": url, "title": title, "w": 900, "h": 620})
    api("POST", "/api/focus", {"id": n["id"]})
    return f"Opened {title} on the Deck."


def to_url(v: str) -> str:
    """Like the board's own address bar: an address, a localhost port, or a Google search."""
    import urllib.parse
    v = (v or "").strip()
    named = {"google": "https://www.google.com", "youtube": "https://www.youtube.com", "github": "https://github.com",
             "gmail": "https://mail.google.com", "chatgpt": "https://chatgpt.com", "claude": "https://claude.ai"}
    if v.lower() in named:
        return named[v.lower()]
    if re.match(r"https?://", v, re.I):
        return v
    if re.match(r"(localhost|127\.0\.0\.1)(:\d+)?(/.*)?$", v, re.I):
        return "http://" + v
    if re.match(r"[\w-]+(\.[\w-]+)+(:\d+)?(/\S*)?$", v):
        return "https://" + v
    return "https://www.google.com/search?q=" + urllib.parse.quote(v)


@tool(tags=["deck"])
def deck_status() -> str:
    """What every console on the Deck is doing - working, resting, or needs you,
    and why. Use for "what are my consoles doing", "deck status"."""
    if not running():
        return "The Deck isn't open."
    return summary(api("GET", "/api/state"))


@tool(tags=["deck"], timeout=20)
def deck_send(text: str, to: str = "") -> str:
    """Type a message into one of the Deck's consoles and press Enter - an
    instruction for its agent, or an answer to its question. Use for "tell
    console 2 to run the tests", "tell the Gemini console yes".

    text: what to type
    to: which console - a number, its name, its kind ("the Gemini one") or "the one that needs me"
    """
    if not running():
        return "The Deck isn't open."
    st = api("GET", "/api/state")
    c = find(st, to)
    if not c:
        return f"There's no console called {to}." if to else "There are no consoles on the Deck."
    try:
        api("POST", f"/api/consoles/{c['id']}/send", {"text": text, "submit": True})
    except Exception:
        return f"{c['title']} isn't running - restart it on the Deck first."
    return f"Sent to {c['title']}."


@tool(tags=["deck"])
def deck_read(to: str = "") -> str:
    """The last lines on one of the Deck's consoles - what its agent said or is
    asking. Use for "what does console 2 say", "what is the Gemini one asking".

    to: which console - a number, its name or its kind
    """
    if not running():
        return "The Deck isn't open."
    st = api("GET", "/api/state")
    c = find(st, to)
    if not c:
        return "There's no such console."
    text = api("GET", f"/api/consoles/{c['id']}/screen")["text"]       # the real screen, from the window
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip() and not re.fullmatch(r"[\s─━│╭╮╰╯>❯·*-]+", ln.strip())]
    return f"{c['title']} ({c['state']}) ends with:\n" + "\n".join(lines[-14:])


@tool(tags=["deck"], timeout=20)
def deck_handoff(frm: str, to: str = "claude", task: str = "") -> str:
    """Hand one Deck console's work to another agent: the outgoing agent writes a
    brief, the next one reads it and carries on. Use for "hand console 1 off to
    Codex", "pass the orb builder's work to a new Claude and have it write the tests".

    frm: the console handing over - a number, its name or its kind
    to: a new agent (claude, codex, antigravity) or an open console (number or name)
    task: what the next agent should do (optional - otherwise it carries on)
    """
    if not running():
        return "The Deck isn't open."
    st = api("GET", "/api/state")
    src = find(st, frm)
    if not src:
        return f"There's no console called {frm}."
    kind = KIND_WORDS.get(to.lower().strip().removeprefix("a new ").removeprefix("new ").strip(), "")
    body = {"from": src["id"], "task": task}
    if kind in ("claude", "codex", "gemini"):
        body["kind"] = kind
    else:
        dst = find(st, to)
        if not dst or dst["id"] == src["id"]:
            return f"Hand it to whom? There's no other console called {to}."
        body["to"] = dst["id"]
    try:
        api("POST", "/api/handoff", body)
    except Exception as e:
        return f"The hand-off didn't start ({e})."
    target = LABEL.get(kind, kind) if kind in ("claude", "codex", "gemini") else find(st, to)["title"]
    return f"{src['title']} is writing its brief; then {('a new ' + target) if body.get('kind') else target} takes over."


@tool(tags=["deck"])
def deck_loop(to: str, text: str, every: int = 0, idle: int = 0, match: str = "", goal: str = "", max_runs: int = 20) -> str:
    """Put a loop on a Deck console: the same message again and again - every N
    minutes, whenever it's been idle N seconds, or whenever its output matches a
    word - until a goal is met or max_runs. Never interrupts work in progress.
    Use for "every 10 minutes tell console 2 to run the tests", "loop the Codex
    console: fix the lint errors until there are none".

    to: which console
    text: the message to send each time
    every: minutes between runs (the default when nothing else is given: 10)
    idle: seconds idle before each run (instead of every)
    match: a word or pattern in its output that sets it off (instead of every)
    goal: when met, the loop retires itself (optional)
    max_runs: stop after this many runs
    """
    if not running():
        return "The Deck isn't open."
    c = find(api("GET", "/api/state"), to)
    if not c:
        return f"There's no console called {to}."
    spec = {"text": text, "goal": goal, "max": max_runs or 20}
    spec.update({"mode": "output", "match": match} if match else {"mode": "idle", "idle": idle} if idle else {"mode": "every", "every": every or 10})
    try:
        api("POST", f"/api/consoles/{c['id']}/loops", spec)
    except Exception as e:
        return f"That loop didn't take ({e})."
    when = f"whenever it prints {match}" if match else f"after {idle} seconds idle" if idle else f"every {every or 10} minutes"
    return f"Looping {c['title']} {when}" + (f" until {goal}." if goal else f", up to {spec['max']} times.")


@tool(tags=["deck"])
def deck_loop_stop(to: str = "") -> str:
    """Pause every loop on a Deck console. Use for "stop the loop on console 2"."""
    if not running():
        return "The Deck isn't open."
    st = api("GET", "/api/state")
    c = find(st, to)
    node = next((n for n in st["nodes"] if c and n["id"] == c["id"]), None)
    live = [lp for lp in (node or {}).get("loops") or [] if lp.get("on")]
    if not live:
        return f"{c['title'] if c else 'That console'} has no loop running."
    for lp in live:
        api("PATCH", f"/api/consoles/{c['id']}/loops/{lp['id']}", {"on": False})
    return f"Paused {len(live)} loop{'s' if len(live) > 1 else ''} on {c['title']}."


def _pick_flow(flows: list[dict], name: str) -> dict | None:
    """'weekly summary', 'the feedback one', a workspace's name -> a flow; the only one if there's one."""
    if len(flows) == 1 and not name.strip():
        return flows[0]
    words = set(re.findall(r"[a-z0-9]{3,}", name.lower())) - {"the", "flow", "one", "my"}
    if not words:
        return None
    score = lambda f: max(len(words & set(re.findall(r"[a-z0-9]{3,}", t.lower())))           # noqa: E731
                          for t in [f["name"], f["workspace"], *f["titles"]])
    best = max(flows, key=score)
    return best if score(best) else None


@tool(tags=["deck"], timeout=150)
def deck_flow_run(name: str = "") -> str:
    """Run a flow on the Deck - boxes wired together, Langflow-style, with Agent
    steps - and say what it produced. Use for "run the weekly summary flow",
    "run my feedback flow".

    name: which flow - the box it ends in, any box in it, or its workspace (optional when there's only one)
    """
    if not launch():
        return "The Deck didn't start."
    flows = api("GET", "/api/flows")["flows"]
    if not flows:
        return "There are no flows on the Deck yet - wire two boxes together first."
    f = _pick_flow(flows, name)
    if not f:
        return "Which flow? " + ", ".join(x["name"] for x in flows[:6]) + "."
    started, ids = time.time(), set(f.get("ids") or f["ends"])     # a Deck from before 23 Oct lists no ids
    try:
        api("POST", "/api/flows/run", {"node": f["ends"][0], "whole": True})
    except Exception as e:
        return f"{f['name']} didn't start ({e})."
    while time.time() - started < 120:             # the tool waits for the answer, like a Langflow API call
        time.sleep(3)
        if not set(api("GET", "/api/flows")["busy"]) & ids:
            break
    else:
        return f"{f['name']} is still running on the Deck - the result will land in {f['name']}."
    nodes = {n["id"]: n for n in api("GET", "/api/state")["nodes"]}
    bad = next((n for i in ids if (n := nodes.get(i)) and (n.get("run") or {}).get("state") == "failed"
                and (n["run"].get("at") or 0) >= started), None)
    if bad:
        return f"{f['name']} stopped at {bad['title']}: {bad['run'].get('error', 'it failed')[:200]}"
    end = nodes.get(f["ends"][0], {})
    result = (end.get("text") if end.get("type") in ("note", "text") else end.get("out")) or ""
    return f"{f['name']}: {result.strip()[:700]}" if result.strip() else f"{f['name']} ran - it's on the Deck."


@tool(tags=["deck"])
def deck_focus(to: str = "") -> str:
    """Bring the Deck forward and fly the board to a console. Use for "show me
    console 2", "go to the Claude console"."""
    if not launch():
        return "The Deck didn't start."
    c = find(api("GET", "/api/state"), to)
    api("POST", "/api/focus", {"id": c["id"]} if c else {})
    return f"Showing {c['title']}." if c else "The Deck is in front."


if __name__ == "__main__":
    st = {"nodes": [{"id": "a", "type": "console", "kind": "claude", "title": "Claude Code 1", "created": 1},
                    {"id": "b", "type": "console", "kind": "gemini", "title": "Orb researcher", "created": 2},
                    {"id": "c", "type": "console", "kind": "claude", "title": "Claude Code 2", "created": 3},
                    {"id": "d", "type": "note", "title": "Plan", "created": 4}],
          "status": {"b": {"state": "needs", "detail": "Allow npm install? (y/n)"}, "a": {"state": "working"}},
          "workspaces": [{"id": "w", "name": "athena-sites", "folder": r"C:\claude code\athena-sites"}]}
    assert [c["n"] for c in consoles(st)] == [1, 2, 3]
    assert find(st, "2")["id"] == "b" and find(st, "console two")["id"] == "b"
    assert find(st, "claude 2")["id"] == "c" and find(st, "the gemini one")["id"] == "b"
    assert find(st, "the one that needs me")["id"] == "b" and find(st, "orb researcher")["id"] == "b"
    assert find(st, "")["id"] == "c"
    assert KIND_WORDS["chatgpt"] == "codex"
    assert "1 need you" in summary(st) and "Allow npm" in summary(st)
    assert _folder("athena sites", st) == r"C:\claude code\athena-sites"
    assert Path(_folder("orb landing")).name == "orb-landing"
    assert to_url("google") == "https://www.google.com" and to_url("youtube.com") == "https://youtube.com"
    assert to_url("localhost:5173") == "http://localhost:5173" and to_url("best esp32 boards").endswith("q=best%20esp32%20boards")
    # jobs: tracked, active while working, resting at once = still starting, done once told
    import tempfile
    from unittest import mock
    with mock.patch(__name__ + ".JOBS", Path(tempfile.mkdtemp()) / "jobs.json"):
        _track([{"id": "a"}], "build my friend's peptide shop website", "Claude")
        assert last_build() > 0
        with mock.patch(__name__ + ".brief", lambda: {"running": True, "consoles": [{"id": "a", "state": "working"}]}):
            (j,) = jobs()
            assert j["state"] == "working" and job_active(j) and "peptide shop website" in j["title"].lower()
        with mock.patch(__name__ + ".brief", lambda: {"running": True, "consoles": [{"id": "a", "state": "resting"}]}):
            assert job_active(jobs()[0])                         # resting in its first seconds: not done yet
            assert not job_active({**jobs()[0], "started": time.time() - SETTLE - 1})
        mark_done("a")
        assert jobs() == [] and last_build() > 0                  # told once; still the last build
    assert _NEW_BUILD.match("build a snake game") and not _NEW_BUILD.match("fix the failing tests")
    fl = [{"name": "Weekly summary", "workspace": "Feedback", "titles": ["Surveys", "Summarise", "Weekly summary"]},
          {"name": "Release notes", "workspace": "Orb site", "titles": ["Git log", "Write notes", "Release notes"]}]
    assert _pick_flow(fl, "weekly summary")["name"] == "Weekly summary" and _pick_flow(fl, "the orb site one")["name"] == "Release notes"
    assert _pick_flow(fl, "") is None and _pick_flow(fl[:1], "")["name"] == "Weekly summary" and _pick_flow(fl, "banana") is None
    print("deck_link: checks pass")
