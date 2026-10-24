"""Loops: a job Athena repeats on a schedule until its goal is met - then it
retires itself (idea from Symvibe, 20 Oct 2026).

"Every 30 minutes, check whether the orb site works on phones, until it does."
Each run is an ordinary mission (missions.py) in the loop's one folder, so a
run sees what the earlier ones left. The worker ends its reply with GOAL MET
or NOT YET: <what's missing>; GOAL MET retires the loop, and so does reaching
max_runs (it "gives up" and says why). Runs are quiet - only the outcome is
said aloud and pushed to the phone. Claude runs count against the missions
spending caps like any mission.

Runs in the voice process (start()), next to the mission watcher.
"""
from __future__ import annotations

import re
import threading
import time
import uuid
from pathlib import Path

from .config import DATA, cfg
from .registry import tool
from .store import load_json, save_json

FILE = DATA / "loops.json"
MIN_EVERY = 10                       # minutes - a loop can't hammer the workers
_lock = threading.Lock()

LOOP_RULES = ("\n\nThis is run {n} of at most {max} of a repeating job. Stop condition: {goal}. Check the REAL current "
              "state now (open the files, run the page, test it) - don't trust notes from earlier runs. If something "
              "small is wrong and the job asks you to fix it, fix it. End your reply with ONE last line, exactly "
              "\"GOAL MET\" if the stop condition is met now, or \"NOT YET: <what is still missing>\" if it isn't.")


def load() -> list[dict]:
    return load_json(FILE, []) if FILE.exists() else []


def _save(loops: list[dict]) -> None:
    save_json(FILE, loops)


def _update(lid: str, **kw) -> dict | None:
    with _lock:
        ls = load()
        for lp in ls:
            if lp["id"] == lid:
                lp.update(kw)
                _save(ls)
                return lp
    return None


def verdict(result: str) -> tuple[bool | None, str]:
    """The worker's last verdict line -> (met, note). (None, ...) when it gave none."""
    for line in reversed([ln.strip(" *_`#>-") for ln in (result or "").splitlines() if ln.strip()]):
        if re.match(r"goal\s+met\b", line, re.I):
            return True, "Goal met."
        m = re.match(r"not\s+yet\b\s*[:\-]?\s*(.*)", line, re.I)
        if m:
            return False, (m.group(1).strip() or "Not there yet.")[:240]
    return None, ((result or "").strip().splitlines() or ["No verdict."])[-1][:240]


def add(task: str, until: str, every_minutes: float = 30, worker: str = "claude", max_runs: int = 0) -> dict:
    from . import missions
    task, until = task.strip(" .,"), until.strip(" .,")
    if not task or not until:
        raise RuntimeError("a loop needs a job and a stop condition")
    worker = worker if worker in ("claude", "gemini") else "claude"
    lid = uuid.uuid4().hex[:8]
    folder = missions.ROOT / f"loop-{missions._slug(missions._title(task))}-{lid[:4]}"
    folder.mkdir(parents=True, exist_ok=True)
    lp = {"id": lid, "task": task, "until": until, "worker": worker, "folder": str(folder),
          "every": max(MIN_EVERY, float(every_minutes)), "max_runs": int(max_runs or cfg.get("loops.max_runs", 8)),
          "runs": 0, "status": "active", "next": time.time(), "mission": "", "history": [],
          "created": time.time(), "note": "First run starting."}
    with _lock:
        ls = load()
        ls.append(lp)
        _save(ls)
    return lp


def stop(lid: str, why: str = "Stopped by you.") -> dict | None:
    from . import missions
    lp = _update(lid, status="stopped", note=why, ended=time.time())
    if lp and lp.get("mission"):
        missions.stop(lp["mission"])
    return lp


def _find(which: str) -> dict | None:
    ls = [lp for lp in load() if lp["status"] == "active"]
    if which:
        words = set(re.findall(r"[a-z0-9]{3,}", which.lower()))
        ls = [lp for lp in ls if words & set(re.findall(r"[a-z0-9]{3,}", (lp["task"] + " " + lp["until"]).lower()))] or ls
    return max(ls, key=lambda lp: lp["created"]) if ls else None


def _every_text(minutes: float) -> str:
    m = int(minutes)
    return f"every {m // 60} hour{'s' if m >= 120 else ''}" if m % 60 == 0 else f"every {m} minutes"


def tick(now: float | None = None) -> list[tuple[str, dict]]:
    """Advance every active loop one step; returns (event, loop) for the outcomes worth telling."""
    from . import missions
    now = now or time.time()
    events = []
    by_id = {m["id"]: m for m in missions.load()}
    for lp in [lp for lp in load() if lp["status"] == "active"]:
        if lp.get("mission"):
            m = by_id.get(lp["mission"])
            if m and m["status"] in ("running", "starting"):
                continue
            met, note = verdict(m.get("result", "")) if m and m["status"] == "done" else \
                (None, (m or {}).get("result") or "The run didn't finish.")
            runs = lp["runs"] + 1
            hist = (lp.get("history") or []) + [{"ts": now, "mission": lp["mission"], "met": met, "note": note[:200]}]
            if met:
                lp = _update(lp["id"], runs=runs, history=hist[-20:], mission="", status="met", note=note, ended=now)
                events.append(("met", lp))
            elif runs >= lp["max_runs"]:
                lp = _update(lp["id"], runs=runs, history=hist[-20:], mission="", status="gave_up", note=note, ended=now)
                events.append(("gave_up", lp))
            else:
                _update(lp["id"], runs=runs, history=hist[-20:], mission="", note=note, next=now + lp["every"] * 60)
            continue
        if now < float(lp.get("next") or 0):
            continue
        prompt = lp["task"] + LOOP_RULES.format(n=lp["runs"] + 1, max=lp["max_runs"], goal=lp["until"])
        try:
            m = missions.start(prompt, lp["worker"], folder=lp["folder"], loop=lp["id"])
        except RuntimeError as e:
            if "already on a mission" in str(e):           # the worker is busy: try again in a few minutes
                _update(lp["id"], next=now + 180, note=f"Waiting: {e}.")
            else:                                          # spending cap, missing CLI: pause and say why
                lp = _update(lp["id"], status="paused", note=f"Paused: {e}.", ended=now)
                events.append(("paused", lp))
            continue
        _update(lp["id"], mission=m["id"], last=now, note=f"Run {lp['runs'] + 1} in progress.")
    return events


def outcome_line(event: str, lp: dict) -> str:
    runs = f"{lp['runs']} run{'s' if lp['runs'] != 1 else ''}"
    if event == "met":
        return f"Loop finished - the goal is met after {runs}: {lp['until']}."
    if event == "gave_up":
        return f"Loop gave up after {runs} without meeting its goal ({lp['until']}). Last check: {lp['note']}"
    return f"Loop paused: {lp['task'][:80]}. {lp['note']}"


def start(announce) -> None:
    """In the voice process: run the loops, and tell the outcome aloud and on the phone."""
    from . import attention

    def loop() -> None:
        while True:
            time.sleep(20)
            try:
                for event, lp in tick():
                    attention.tell(announce, outcome_line(event, lp), title="Loop " + {"met": "done", "gave_up": "gave up"}
                                   .get(event, "paused"), tags="repeat" if event == "met" else "warning")
            except Exception as e:
                print(f"[loops: {e}]")

    threading.Thread(target=loop, daemon=True, name="athena-loops").start()


@tool(tags=["loops"], destructive=True, timeout=20)
def loop_start(task: str, until: str, every_minutes: float = 30, worker: str = "claude") -> str:
    """Repeat a job on a schedule until a goal is met, then stop by itself:
    "every 30 minutes check whether the orb site works on phones until it
    does". Each run is a background mission (Claude by default) and uses the
    user's limits, so only when asked.

    task: what each run does
    until: the stop condition, in the user's words
    every_minutes: how often (10 or more)
    worker: claude or gemini
    """
    try:
        lp = add(task, until, every_minutes, worker)
    except RuntimeError as e:
        return f"Can't: {e}."
    return (f"Loop on - {_every_text(lp['every'])}, up to {lp['max_runs']} runs, until {lp['until']}. "
            f"The first run starts now; I'll tell you when the goal is met.")


@tool(tags=["loops"])
def loop_stop(which: str = "") -> str:
    """Stop a loop. which: part of its job, or empty for the newest."""
    lp = _find(which)
    if not lp:
        return "There's no loop running."
    stop(lp["id"])
    return f"Stopped the loop: {lp['task'][:80]}."


@tool(tags=["loops"])
def loop_status() -> str:
    """How the loops are doing: runs so far, the last check, which have met their goal."""
    ls = sorted(load(), key=lambda lp: -lp["created"])[:5]
    if not ls:
        return "No loops yet."
    word = {"active": "running", "met": "goal met", "gave_up": "gave up", "paused": "paused", "stopped": "stopped"}
    return " ".join(f"{lp['task'][:60]}: {word.get(lp['status'], lp['status'])} after {lp['runs']} of "
                    f"{lp['max_runs']} runs - {lp['note']}" for lp in ls)


if __name__ == "__main__":
    import tempfile
    from unittest import mock

    assert verdict("All good.\n\n**GOAL MET**") == (True, "Goal met.")
    assert verdict("Checked.\nNOT YET: the stat cards overflow at 360px") == (False, "the stat cards overflow at 360px")
    assert verdict("I checked it.")[0] is None
    d = Path(tempfile.mkdtemp())
    store = {"missions": []}

    def fake_start(prompt, worker, folder="", parent="", loop=""):
        m = {"id": f"m{len(store['missions'])}", "status": "running", "result": "", "loop": loop}
        store["missions"].append(m)
        return m

    with mock.patch(__name__ + ".FILE", d / "loops.json"), \
            mock.patch("jarvis.missions.ROOT", d), mock.patch("jarvis.missions.start", fake_start), \
            mock.patch("jarvis.missions.load", lambda: store["missions"]):
        lp = add("check the orb page on phones", "it fits at 360px", every_minutes=1, max_runs=3)
        assert lp["every"] == MIN_EVERY and lp["max_runs"] == 3
        assert tick(now=time.time()) == [] and load()[0]["mission"] == "m0"            # run 1 started
        store["missions"][0].update(status="done", result="Looked.\nNOT YET: cards overflow")
        assert tick() == [] and load()[0]["runs"] == 1 and load()[0]["note"] == "cards overflow"
        assert tick() == [] and load()[0]["mission"] == ""                              # waits for its time
        assert tick(now=time.time() + MIN_EVERY * 60 + 1) == [] and load()[0]["mission"] == "m1"
        store["missions"][1].update(status="done", result="Fixed it.\nGOAL MET")
        ev = tick()
        assert ev and ev[0][0] == "met" and load()[0]["status"] == "met" and load()[0]["runs"] == 2, ev
        assert "goal is met after 2 runs" in outcome_line(*ev[0])
    print("loops: checks pass")
