"""Chained missions: one goal split by the planner into steps (Gemini research, then a
Claude build...) run one after another in one folder, with one report at the end.

Split out of missions.py (31 Oct 2026); missions.py imports all of it back. The
runner (missions.start / stop) is imported inside the functions that use it, since
missions.py imports this module.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import threading
import time
import uuid
from pathlib import Path

from .config import cfg
from .missions_store import ROOT, WORKERS, _title, _lock, load, _save, _update, _preview, _note, _slug, _get


PLAN_PROMPT = """You plan background jobs for Athena, a personal assistant. Split the user's goal into 1 to 4 steps, done one after another by AI workers in ONE shared folder. Each worker sees the files earlier steps saved there and a summary of what they did.

Workers:
- "gemini": research - web searches, reading websites, comparing options, finding facts, prices, places, writing reports and documents. Free on the user's Google plan: use it for research.
- "claude": building - websites (plain HTML/CSS/JS with index.html), programs, scripts, data processing, editing and improving files. Use it for anything that is built or coded.
- "argon": ONLY if the user explicitly asks for Argon.
- "video": Athena films an animation page into video.mp4 herself. For ANY video, animation, motion graphic, reel or intro: one "claude" step builds the animation as index.html (it is given the exact video rules automatically - describe the content, look, length and shape), then a last "video" step whose task is only the length and shape, e.g. "15 seconds, 1920x1080". Never ask a worker to write a program or script that renders a video.
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
            if (re.search(r"\b(video|animation|motion\s*graphics?|reel|intro|mp4)\b", goal, re.I)
                    and not any(s["worker"] == "video" for s in steps)):
                secs = re.search(r"(\d+)\s*(?:s\b|sec|second)", goal, re.I)
                steps = [s for s in steps if s["worker"] != "claude"][:2] + \
                        [{"worker": "claude", "task": f"Build the animation for: {goal}"},
                         {"worker": "video", "task": f"{secs.group(1) if secs else 15} seconds"}]
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
    if steps[i]["worker"] in ("claude", "gemini", "argon") and any(s["worker"] == "video" for s in steps[i + 1:]):
        from .videorender import VIDEO_RULES
        steps = [dict(s) for s in steps]
        steps[i]["task"] = steps[i]["task"] + "\n\n" + VIDEO_RULES
    done = "\n".join(f"- Step {n + 1} ({WORKERS[s['worker']]}): {s['task']}\n  Result: {(s.get('result') or '')[:600]}"
                     for n, s in enumerate(steps[:i]))
    return (f"This is step {i + 1} of {len(steps)} of a bigger job. The whole goal: {c['task']}\n\n"
            + (f"Earlier steps (their files are in this folder - read them):\n{done}\n\n" if done else "")
            + f"Your step: {steps[i]['task']}")


def _set_step(cid: str, i: int, **fields) -> None:
    with _lock:
        ms = load()
        c = next((x for x in ms if x["id"] == cid), None)
        if c is not None and i < len(c.get("steps") or []):
            c["steps"][i].update(fields)
            _save(ms)


def _run_chain(cid: str, start_at: int = 0) -> None:
    from .missions import start, stop            # the runner; missions.py imports this module
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
                child = start(step["task"] if step["worker"] in ("maps", "video") else _step_prompt(_get(cid), i),
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
