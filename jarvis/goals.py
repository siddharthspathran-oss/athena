"""Long-running goals: "prepare me for the Signals end-sem by Friday".

Athena turns a goal into a day-by-day plan from the project's own files (the
course plan's units, the old papers), then keeps at it on her own: today's
tasks in the morning brief, a nudge in the evening if they're not done, missed
tasks moved into the days that are left, and a wrap-up when the deadline comes.
Quiz tasks tick themselves off when you finish "quiz me on ...".

One file, data/goals.json. The plan is written once by the study model; after
that every change (ticking off, re-planning) is plain code, so it can't drift.
"""
from __future__ import annotations

import datetime as dt
import difflib
import json
import re
import threading
import time

from .config import DATA, cfg
from .registry import tool
from .store import load_json, save_json

FILE = DATA / "goals.json"
_lock = threading.Lock()
KINDS = ("study", "practice", "quiz", "review")


def load() -> list[dict]:
    return load_json(FILE, [])


def _save(goals: list[dict]) -> None:
    save_json(FILE, goals)


def _today() -> dt.date:
    return dt.date.today()


# ---------- dates: "friday", "20 october", "in 5 days", "next week", "2026-10-20" ----------

_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def parse_date(text: str, today: dt.date | None = None) -> dt.date | None:
    today = today or _today()
    t = re.sub(r"\b(by|on|before|until|till|the|this|coming)\b", " ", text.lower()).strip()
    if m := re.search(r"\d{4}-\d{2}-\d{2}", t):
        try:
            return dt.date.fromisoformat(m.group(0))
        except ValueError:
            return None
    if "tomorrow" in t:
        return today + dt.timedelta(days=1)
    if m := re.search(r"in\s+(\d+|a|one|two|three)\s+(day|week)s?", t):
        n = {"a": 1, "one": 1, "two": 2, "three": 3}.get(m.group(1)) or int(m.group(1))
        return today + dt.timedelta(days=n * (7 if m.group(2) == "week" else 1))
    if "next week" in t:
        return today + dt.timedelta(days=7)
    for i, name in enumerate(_DAYS):
        if re.search(rf"\b{name[:3]}\w*\b", t):
            ahead = (i - today.weekday()) % 7 or 7
            if "next" in t and ahead < 7:
                ahead += 7
            return today + dt.timedelta(days=ahead)
    m = (re.search(r"(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?([a-z]{3})", t)
         or re.search(r"([a-z]{3})[a-z]*\s+(\d{1,2})", t))
    if m:
        a, b = m.groups()
        day, mon = (a, b) if a.isdigit() else (b, a)
        if mon[:3] in _MONTHS:
            d = dt.date(today.year, _MONTHS.index(mon[:3]) + 1, int(day))
            return d if d >= today else d.replace(year=today.year + 1)
    return None


# ---------- making the plan ----------

_GENERIC = {"and", "the", "for", "end", "sem", "mid", "exam", "exams", "prepare", "semester", "test", "quiz",
            "goal", "plan", "study", "revision", "course", "subject", "my", "lab", "theory"}


def named_project(text: str) -> dict | None:
    """The project the words name: "analog" -> ANALOG ELECTRONICS II,
    "signals" / "s&s" -> SIGNALS AND SYSTEMS. Typos are forgiven ("anolog").
    None when no project is named."""
    from . import chats
    words = set(re.findall(r"[a-z]{3,}", text.lower())) - _GENERIC
    best, score = None, 0.0
    for p in chats.projects():
        name = set(re.findall(r"[a-z]{3,}", p["name"].lower())) - _GENERIC
        sc = len(words & name) + 0.8 * sum(1 for w in words - name
                                           if len(w) >= 5 and difflib.get_close_matches(w, name, n=1, cutoff=0.8))
        if sc > score:
            best, score = p, sc
    return best if score >= 0.8 else None


def _project(text: str) -> dict | None:
    """The goal's project: the one it names, else the project chat it was set in."""
    from . import chats
    return named_project(text) or chats.ACTIVE


PLAN_PROMPT = """You plan a student's preparation as a day-by-day schedule.

Goal: {goal}
Today is day 1 ({today}); the deadline is {deadline} - {days} days, so plan days 1 to {days}.
About {minutes} minutes of work a day. The last day is for review and an old paper only.
{notes}
Course files (use their real units, topics and old papers; never invent topics):
{ctx}

Rules:
- Cover every unit/topic that the goal needs, in the course's order, before the review day.
- 1 to 3 tasks a day. Kinds: "study" (learn a topic), "practice" (solve problems or an old
  paper), "quiz" (a short self-test on topics already studied), "review" (revise).
- Put a quiz after each unit or two. Tasks are concrete: "Laplace transform: ROC and
  properties (Unit II)", not "study Unit II".
- "topic" is 2-6 words, usable as "quiz me on <topic>".

Reply with JSON only:
{{"title": "short goal title", "tasks": [{{"day": 1, "kind": "study", "task": "...", "topic": "...", "minutes": 45}}]}}"""


def make_plan(goal: str, deadline: dt.date, project: dict | None) -> dict:
    """The plan from the study model, checked: days in range, kinds known, at least one task a day."""
    from . import chats
    from .tools import study
    today = _today()
    days = (deadline - today).days + 1
    ctx = chats.relevant(project["id"], goal + " syllabus units topics course plan old paper", budget=10000) if project else ""
    notes = f"The student's notes: {project['notes']}" if project and project.get("notes") else ""
    prompt = PLAN_PROMPT.format(goal=goal, today=today.isoformat(), deadline=deadline.isoformat(), days=days,
                                minutes=int(cfg.get("goals.daily_minutes", 120)), notes=notes,
                                ctx=ctx or "(no course files - use the standard syllabus for this subject)")
    raw = study._ask(study._model(), [{"role": "user", "content": prompt}], max_wait=8)
    m = re.search(r"\{.*\}", raw, re.S)
    data = json.loads(m.group(0)) if m else {}
    tasks = []
    for i, t in enumerate(data.get("tasks") or []):
        try:
            day = min(max(int(t.get("day", 1)), 1), days)
        except (TypeError, ValueError):
            continue
        text = str(t.get("task", "")).strip().rstrip(".")
        if not text:
            continue
        kind = str(t.get("kind", "study")).lower()
        tasks.append({"id": f"t{i + 1}", "date": (today + dt.timedelta(days=day - 1)).isoformat(),
                      "kind": kind if kind in KINDS else "study", "task": text[:200],
                      "topic": re.sub(r"\s*\b(?:quiz|self[- ]?test|test)\b\s*$", "", str(t.get("topic", "") or text), flags=re.I)[:60] or text[:60],
                      "minutes": int(t.get("minutes") or 45),
                      "done": False})
    if not tasks:
        raise ValueError("the plan came back empty")
    tasks.sort(key=lambda t: t["date"])
    return {"title": str(data.get("title") or goal)[:80], "tasks": tasks}


# ---------- keeping at it: plain code ----------

def replan(g: dict, today: dt.date | None = None) -> int:
    """Missed tasks (before today, not done) spread over today..deadline-1,
    onto the lightest days first. Returns how many moved."""
    today = today or _today()
    deadline = dt.date.fromisoformat(g["deadline"])
    missed = [t for t in g["tasks"] if not t["done"] and dt.date.fromisoformat(t["date"]) < today]
    if not missed:
        return 0
    last = max(today, deadline - dt.timedelta(days=1)) if deadline > today else today
    span = [today + dt.timedelta(days=i) for i in range((last - today).days + 1)]
    load = {d: sum(t["minutes"] for t in g["tasks"] if not t["done"] and t["date"] == d.isoformat()) for d in span}
    for t in missed:
        d = min(span, key=lambda d: (load[d], d))
        t["date"], t["moved"] = d.isoformat(), t.get("moved", 0) + 1
        load[d] += t["minutes"]
    g["tasks"].sort(key=lambda t: t["date"])
    return len(missed)


def _active(name: str = "") -> list[dict]:
    """Active goals a command is about: the project it names; else, in a project
    chat, that project's goals; else words of the goal's title; else all."""
    from . import chats
    goals = [g for g in load() if g["status"] == "active"]
    proj = named_project(name) if name else None
    if proj:
        return [g for g in goals if g.get("project") == proj["id"]]
    if not name and chats.ACTIVE:
        mine = [g for g in goals if g.get("project") == chats.ACTIVE["id"]]
        if mine:
            return mine
    if name:
        words = set(re.findall(r"[a-z]{3,}", name.lower())) - _GENERIC
        hit = [g for g in goals if words & set(re.findall(r"[a-z]{3,}", (g["title"] + " " + g["text"]).lower()))]
        return hit or goals
    return goals


def for_project(pid: str) -> dict | None:
    return next((g for g in load() if g["status"] == "active" and g.get("project") == pid), None)


def next_task(g: dict) -> dict | None:
    """Today's first open task, else the earliest open one (missed first)."""
    today = _today().isoformat()
    open_ = [t for t in g["tasks"] if not t["done"]]
    return next((t for t in open_ if t["date"] == today), None) or (min(open_, key=lambda t: t["date"]) if open_ else None)


def context(pid: str) -> str:
    """The project's goal, for its chats: progress, today's tasks, what's next."""
    g = for_project(pid)
    if not g:
        return ""
    lines = [f"The student's goal in this project: {progress(g)}."]
    tt = today_tasks(g)
    if tt:
        lines.append("Today's tasks: " + "; ".join(("[done] " if t["done"] else "[ ] ") + t["task"] for t in tt) + ".")
    n = next_task(g)
    if n:
        lines.append(f"Next task: {n['task']} ({n['kind']}). If they ask what to do next, it is this - "
                     f"they can say \"start my next task\".")
    return "\n".join(lines)


def task_prompt(t: dict) -> str:
    """What doing a task means, asked in the project chat."""
    if t["kind"] == "quiz":
        return f"quiz me on {t['topic']}"
    if t["kind"] == "practice":
        return (f"Give me practice problems with fully worked, step-by-step solutions on: {t['task']}. "
                "Base them on my old papers and course files.")
    if t["kind"] == "review":
        return f"Give me revision notes for: {t['task']} - the key formulas, results and likely exam questions."
    return f"Teach me: {t['task']}. Use my course files."


# A task started from the Goals page: ticked off when its answer arrives.
PENDING: dict = {}


def start_task(gid: str, tid: str) -> dict | None:
    for g in load():
        if g["id"] == gid:
            t = next((x for x in g["tasks"] if x["id"] == tid), None)
            if t:
                PENDING.clear()
                PENDING.update(goal=gid, task=tid, prompt=task_prompt(t))
                return {"project": g.get("project", ""), "prompt": PENDING["prompt"], "kind": t["kind"]}
    return None


def complete_pending(said: str) -> str:
    """After a project answer: if it was a started task's own prompt, tick it off."""
    if not PENDING or " ".join(said.split()).lower() != " ".join(PENDING["prompt"].split()).lower():
        return ""
    gid, tid = PENDING["goal"], PENDING["task"]
    PENDING.clear()
    with _lock:
        goals = load()
        for g in goals:
            t = next((x for x in g["tasks"] if g["id"] == gid and x["id"] == tid), None)
            if t:
                t["done"], t["done_at"] = True, time.time()
                _save(goals)
                return f"Ticked off: {t['task']}."
    return ""


def progress(g: dict) -> str:
    done = sum(t["done"] for t in g["tasks"])
    left = (dt.date.fromisoformat(g["deadline"]) - _today()).days
    when = "today" if left == 0 else "tomorrow" if left == 1 else f"in {left} days" if left > 1 else "passed"
    return f"{g['title']}: {done} of {len(g['tasks'])} tasks done ({round(100 * done / max(1, len(g['tasks'])))}%), deadline {when}"


def today_tasks(g: dict, day: dt.date | None = None) -> list[dict]:
    return [t for t in g["tasks"] if t["date"] == (day or _today()).isoformat()]


def _hint(t: dict) -> str:
    return f' - say "quiz me on {t["topic"]}"' if t["kind"] == "quiz" else ""


def brief_lines() -> list[str]:
    """For the morning brief: each active goal's progress and today's tasks."""
    out = []
    for g in _active():
        out.append(progress(g) + ".")
        out += [f"  {'✓' if t['done'] else '•'} {t['task']}{_hint(t)}" for t in today_tasks(g)]
    return out


# ---------- tools ----------

@tool(tags=["study"], timeout=180)
def set_goal(goal: str, deadline: str, project: str = "") -> str:
    """Set a long-running goal, e.g. preparing for an exam by a date: Athena makes a
    day-by-day plan from the project's course files and keeps you on it.

    goal: what to achieve, e.g. "prepare for the Signals and Systems end-sem"
    deadline: when, e.g. "friday", "20 october", "in 10 days"
    project: the course/project name, if the goal does not say it
    """
    d = parse_date(deadline)
    if d is None:
        return f"When is the deadline? I couldn't read \"{deadline}\" as a date."
    if d < _today():
        return "That date has already passed."
    proj = _project(project or goal)
    have = for_project(proj["id"]) if proj else None
    if have:                                   # one goal per project: a second would split the days
        return (f"You already have a goal for {proj['name'].title()}: {progress(have)}. "
                f"Say \"drop my {proj['name'].split()[0].lower()} goal\" first if you want a new plan.")
    try:
        plan = make_plan(goal, d, proj)
    except Exception as e:
        return f"ERROR: couldn't make the plan ({str(e)[:120]})."
    g = {"id": f"g{int(time.time())}", "text": goal, "title": plan["title"], "deadline": d.isoformat(),
         "project": proj["id"] if proj else "", "created": time.time(), "status": "active",
         "tasks": plan["tasks"], "said": {}}
    with _lock:
        goals = load()
        goals.append(g)
        _save(goals)
    days = (d - _today()).days + 1
    first = today_tasks(g)
    src = f" for your {proj['name'].title()} project, from its files" if proj else " (no project matched, so from the usual syllabus)"
    return (f"Goal set: {g['title']}. {len(g['tasks'])} tasks over {days} days{src}. "
            + ("Today: " + "; ".join(t["task"] + _hint(t) for t in first) + "." if first else "")
            + " I'll keep you on it - see the Goals page.")


@tool(tags=["study"])
def goal_status(goal: str = "") -> str:
    """How a goal is going: progress, today's tasks, anything missed.

    goal: which goal (part of its name), or empty for all
    """
    goals = _active(goal)
    if not goals:
        return 'No goals yet. Say something like "prepare me for my Signals end-sem by Friday".'
    out = []
    for g in goals:
        out.append(progress(g) + ".")
        tt = today_tasks(g)
        out += [f"{'Done' if t['done'] else 'Today'}: {t['task']}{_hint(t)}" for t in tt] or ["Nothing planned for today."]
        missed = [t for t in g["tasks"] if not t["done"] and t["date"] < _today().isoformat()]
        if missed:
            out.append(f"{len(missed)} missed - say \"replan my goal\" and I'll fit them into the days left.")
    return "\n".join(out)


def _match(g: dict, text: str) -> dict | None:
    pool = [t for t in g["tasks"] if not t["done"]]
    if not pool:
        return None
    if not text.strip():
        today = today_tasks(g)
        return next((t for t in today if not t["done"]), None)
    low = text.lower()
    def score(t):
        words = set(re.findall(r"[a-z0-9]{3,}", (t["task"] + " " + t["topic"]).lower()))
        q = set(re.findall(r"[a-z0-9]{3,}", low))
        return len(words & q) + difflib.SequenceMatcher(None, low, t["topic"].lower()).ratio()
    best = max(pool, key=lambda t: (score(t), t["date"] <= _today().isoformat()))
    return best if score(best) >= 1 else None


@tool(tags=["study"])
def goal_done(task: str = "", goal: str = "") -> str:
    """Tick off a task of a goal, e.g. "I finished the Laplace revision".

    task: what was finished (part of the task), or empty for today's next task
    goal: which goal, if there are several
    """
    with _lock:
        goals = load()
        scope = {x["id"] for x in _active(goal)}
        for g in goals:
            if g["status"] != "active" or g["id"] not in scope:
                continue
            t = _match(g, task)
            if t:
                t["done"], t["done_at"] = True, time.time()
                _save(goals)
                left = [x for x in today_tasks(g) if not x["done"]]
                return (f"Ticked off: {t['task']}. " + (f"{len(left)} left today." if left else "That's today done!")
                        + f" {progress(g)}.")
    return "I couldn't find that task in your goals - say \"what's my goal plan\" to see them."


@tool(tags=["study"])
def replan_goal(goal: str = "") -> str:
    """Move a goal's missed tasks into the days that are left."""
    with _lock:
        goals = load()
        scope = {x["id"] for x in _active(goal)}
        moved = sum(replan(g) for g in goals if g["status"] == "active" and g["id"] in scope)
        _save(goals)
    return f"Moved {moved} missed task{'s' if moved != 1 else ''} into the days left." if moved else "Nothing missed - you're on track."


@tool(tags=["study"])
def drop_goal(goal: str) -> str:
    """Stop working on a goal (it stays on the Goals page as dropped).

    goal: which goal (part of its name)
    """
    with _lock:
        goals = load()
        scope = [x["id"] for x in _active(goal)]
        hit = [g for g in goals if g["status"] == "active" and g["id"] in scope][:1]
        for g in hit:
            g["status"] = "dropped"
        _save(goals)
    return f"Dropped: {hit[0]['title']}." if hit else "No such goal."


@tool(tags=["study"], timeout=240)
def goal_start(goal: str = "") -> str:
    """Start the next task of a goal now: a lesson, worked practice problems,
    revision notes or a quiz, made from that project's files.

    goal: which goal or subject (e.g. "analog"), or empty for the one in this project chat
    """
    from . import chats
    goals = _active(goal)
    if not goals:
        return 'No goal for that. Say something like "prepare me for my analog end-sem by Friday".'
    g = goals[0]
    t = next_task(g)
    if t is None:
        return f"Every task of {g['title']} is done!"
    proj = chats.project(g.get("project", ""))
    prev = chats.ACTIVE
    chats.ACTIVE = proj or prev                  # the quiz and the lesson read this project's files
    try:
        if t["kind"] == "quiz":
            from .quiz import quiz_me
            return f"Next for {g['title']}: {t['task']}. " + quiz_me(t["topic"])
        if proj:
            from .tools import study
            answer = study.project_answer(task_prompt(t), [], proj)
        else:
            from .tools.study import explain
            answer = explain(task_prompt(t))
    finally:
        chats.ACTIVE = prev
    if answer.startswith("ERROR"):
        return answer
    with _lock:
        gs = load()
        for x in gs:
            for y in x["tasks"]:
                if x["id"] == g["id"] and y["id"] == t["id"]:
                    y["done"], y["done_at"] = True, time.time()
        _save(gs)
    return f"{answer}\n\nTicked off: {t['task']}."


def quiz_finished(topic: str, right: float, asked: int) -> str:
    """quiz.py calls this: a quiz task on that topic ticks itself off."""
    with _lock:
        goals = load()
        for g in goals:
            if g["status"] != "active":
                continue
            pool = [t for t in g["tasks"] if t["kind"] == "quiz" and not t["done"]]
            t = max(pool, key=lambda t: difflib.SequenceMatcher(None, topic.lower(), t["topic"].lower()).ratio(), default=None)
            if t and difflib.SequenceMatcher(None, topic.lower(), t["topic"].lower()).ratio() > 0.5:
                t["done"], t["done_at"], t["score"] = True, time.time(), f"{right:g}/{asked}"
                _save(goals)
                if PENDING.get("task") == t["id"]:
                    PENDING.clear()
                return f" Ticked off your goal's quiz: {t['task']}."
    return ""


# ---------- on her own ----------

def tick(speak, now: dt.datetime | None = None) -> None:
    """Once a minute in the voice process: re-plan missed tasks each morning,
    nudge in the evening if today's tasks aren't done, wrap up past deadlines."""
    from . import presence, rules
    now = now or dt.datetime.now()
    today = now.date().isoformat()
    hhmm = now.strftime("%H:%M")
    if rules.paused():
        return
    with _lock:
        goals = load()
        changed = False
        for g in goals:
            if g["status"] != "active":
                continue
            said = g.setdefault("said", {})
            if now.date() > dt.date.fromisoformat(g["deadline"]):
                g["status"] = "finished"
                changed = True
                done = sum(t["done"] for t in g["tasks"])
                speak(f"Your goal {g['title']} has reached its deadline: you did {done} of {len(g['tasks'])} tasks.")
                rules.log(f"goal finished: {g['title']} ({done}/{len(g['tasks'])})", "goals")
                continue
            if said.get("replan") != today and hhmm >= "05:00":
                said["replan"] = today
                changed = True
                n = replan(g, now.date())
                if n:
                    rules.log(f"goal: moved {n} missed task(s) of {g['title']} into the days left", "goals")
            open_ = [t for t in today_tasks(g, now.date()) if not t["done"]]
            if (open_ and said.get("nudge") != today and hhmm >= str(cfg.get("goals.nudge_at", "19:00"))
                    and not presence.away()):
                said["nudge"] = today
                changed = True
                speak(f"For your goal {g['title']}, still on today's plan: " + "; ".join(t["task"] for t in open_[:3]) + ".")
        if changed:
            _save(goals)


def start(speak) -> None:
    if not cfg.get("goals.enabled", True):
        return

    def loop():
        while True:
            time.sleep(60)
            try:
                tick(speak)
            except Exception as e:
                print(f"[goals] {e}")
    threading.Thread(target=loop, daemon=True, name="athena-goals").start()


if __name__ == "__main__":
    # The plain-code parts, checked without a model.
    t0 = dt.date(2026, 10, 14)                     # a Wednesday
    assert parse_date("friday", t0) == dt.date(2026, 10, 16)
    assert parse_date("next friday", t0) == dt.date(2026, 10, 23)
    assert parse_date("by 20 october", t0) == dt.date(2026, 10, 20)
    assert parse_date("oct 22", t0) == dt.date(2026, 10, 22)
    assert parse_date("in 5 days", t0) == dt.date(2026, 10, 19)
    assert parse_date("2026-11-02", t0) == dt.date(2026, 11, 2)
    assert parse_date("whenever", t0) is None
    g = {"deadline": "2026-10-20", "tasks": [
        {"date": "2026-10-12", "done": False, "minutes": 60, "task": "a"},
        {"date": "2026-10-13", "done": True, "minutes": 60, "task": "b"},
        {"date": "2026-10-13", "done": False, "minutes": 30, "task": "c"},
        {"date": "2026-10-14", "done": False, "minutes": 90, "task": "d"}]}
    assert replan(g, t0) == 2
    moved = [t for t in g["tasks"] if t["task"] in "ac"]
    assert all("2026-10-14" < t["date"] < "2026-10-20" or t["date"] == "2026-10-14" for t in moved)
    assert all(t["date"] >= "2026-10-14" for t in g["tasks"] if not t["done"])
    assert {t["date"] for t in moved} != {"2026-10-14"}          # spread, not piled onto today's heavy day
    print("goals: all checks pass")
