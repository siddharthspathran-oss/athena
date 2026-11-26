"""Study autopilot: plans for assignments, primers for tomorrow's classes, and
exam revision plans - all as tasks, reminders and pages, all on a schedule.

    7 pm  : every assignment in Tasks without a plan gets one - the work split
            into sessions before the due date, each a reminder (study.plan_at)
    8:30pm: a short primer page per course on tomorrow's timetable, from the
            course name and, if present, data/syllabus/<course>.md
    asked : "make me a revision plan for my exams on the 20th"

It prepares and reminds; it does not do or submit the work.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import threading
import time

from .config import DATA, cfg
from .registry import tool
from .store import load_json, save_json

PLANS = DATA / "study_plans.json"
SYLLABUS = DATA / "syllabus"
_WORK = re.compile(r"\b(assignment|lab|record|report|project|quiz|exam|test|viva|submission|homework|worksheet|ppt|presentation)\b", re.I)


def _remind(text: str, when: dt.datetime) -> None:
    """A one-off reminder at an exact time (the spoken-time parser has no dates)."""
    from .tools import scheduler
    scheduler.add_at(text, when)


def _ask(prompt: str) -> str:
    from .inbox import _ask as ask
    return ask(prompt)


def plan_assignments() -> str:
    from .tools import todo
    done = load_json(PLANS, {})
    today = dt.date.today()
    made = []
    for t in todo.load():
        if t["done"] or not t.get("due") or t["id"] in done or not _WORK.search(t["text"]):
            continue
        due = dt.date.fromisoformat(t["due"][:10])
        days = (due - today).days
        if days < 1 or days > 21:
            continue
        raw = _ask(f"A B.Tech ECE student has this to do: '{t['text']}', due in {days} days. Estimate the total "
                   "work in minutes, and split it into 1 to 4 work sessions of 30 to 120 minutes. Reply with JSON "
                   'only: {"total": 120, "sessions": [60, 60], "first_step": "..."}')
        try:
            p = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
            sessions = [int(s) for s in p["sessions"]][:4] or [60]
        except Exception:
            p, sessions = {"first_step": ""}, [60]
        # Spread the sessions over the days before the due date, finishing a day early.
        span = max(1, days - 1)
        at = cfg.get("study.plan_at", "18:30")
        for i, mins in enumerate(sessions):
            day = today + dt.timedelta(days=min(span, 1 + i * max(1, span // len(sessions))))
            if day >= due:
                day = due - dt.timedelta(days=1) if due - dt.timedelta(days=1) > today else today
            _remind(f"Work on {t['text']} - session {i + 1} of {len(sessions)}, about {mins} minutes",
                    dt.datetime.combine(day, dt.time.fromisoformat(at)))
        done[t["id"]] = {"t": time.time(), "sessions": sessions}
        made.append(f"{t['text']}: {len(sessions)} session(s)" + (f", start with {p['first_step']}" if p.get("first_step") else ""))
    save_json(PLANS, done)
    if made:
        from . import rules
        rules.log("planned " + "; ".join(made), "study")
    return ("Planned: " + "; ".join(made) + ".") if made else "No new assignments to plan."


def _tomorrows_courses() -> list[str]:
    day = dt.date.today() + dt.timedelta(days=1)
    names: list[str] = []
    try:
        from .tools import brief
        names += [e["what"] for e in brief._events(day) if not e["all_day"]]
    except Exception:
        pass
    try:
        from .tools import teams
        names += [m["title"] for m in (teams.read_day(day) or []) if not m["canceled"]]
    except Exception:
        pass
    seen, out = set(), []
    for n in names:
        key = re.sub(r"\s*\(.*?\)|\s*lab\b|\s*tutorial\b", "", n, flags=re.I).strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(n)
    return out


def primers() -> str:
    from . import render
    courses = _tomorrows_courses()
    if not courses:
        return "No classes tomorrow."
    pages = []
    for c in courses[:5]:
        code = re.search(r"\(([A-Z]{3,5}\d{3,5}\w*)", c)
        syl = ""
        for name in ([code.group(1)] if code else []) + [re.sub(r"\W+", "_", c.lower())[:40]]:
            f = SYLLABUS / f"{name}.md"
            if f.exists():
                syl = f.read_text(encoding="utf-8")[:4000]
                break
        text = _ask(f"Write a one-page primer for a B.Tech ECE student before tomorrow's class: '{c}'. "
                    + (f"The syllabus is:\n{syl}\n" if syl else "")
                    + "Give: the core ideas in 5-7 bullet points with the key formulas in LaTeX (\\( \\) inline, "
                      "\\[ \\] displayed), one worked example, and 3 questions to check understanding. Markdown.")
        if text.strip():
            path = render.write_page(f"Primer: {c}", text, open_it=False)
            pages.append((c, path.name))
    if pages:
        from . import rules
        rules.log("primers ready: " + ", ".join(c for c, _ in pages), "study")
    return (f"Primers for tomorrow are ready: " + ", ".join(c for c, _ in pages) + "."
            + "".join(f" [{p}]" for _, p in pages[:1])) if pages else "Couldn't write the primers."


@tool(tags=["study"], timeout=300)
def plan_my_assignments() -> str:
    """Split each upcoming assignment in Tasks into work sessions before its due
    date, each with a reminder."""
    return plan_assignments()


@tool(tags=["study"], timeout=600)
def prepare_for_tomorrow() -> str:
    """Write a short primer page for each class on tomorrow's timetable."""
    return primers()


def exam_plan(exam_date: str, subjects: str) -> str:
    """Make a day-by-day revision timetable up to an exam, as reminders.

    exam_date: the date of the first exam, e.g. 2026-10-20 or "20 October"
    subjects: the subjects, comma separated
    """
    try:
        d = dt.date.fromisoformat(exam_date)
    except ValueError:
        m = re.search(r"(\d{1,2})", exam_date)
        if not m:
            return "Which date is the exam?"
        d = dt.date.today().replace(day=int(m.group(1)))
        if d <= dt.date.today():
            d = (d.replace(day=1) + dt.timedelta(days=32)).replace(day=int(m.group(1)))
    subs = [s.strip() for s in re.split(r",|\band\b", subjects) if s.strip()]
    days = (d - dt.date.today()).days
    if days < 1 or not subs:
        return "I need a future exam date and at least one subject."
    raw = _ask(f"Make a revision timetable for a B.Tech student: exam on {d}, {days} days from today, subjects: "
               f"{', '.join(subs)}. Each day gets one or two subjects and a concrete focus; leave the last day for "
               'full revision. JSON only: [{"day": 1, "focus": "..."}]')
    try:
        plan = json.loads(re.search(r"\[.*\]", raw, re.S).group(0))
    except Exception:
        plan = [{"day": i + 1, "focus": subs[i % len(subs)]} for i in range(days)]
    at = cfg.get("study.revise_at", "17:00")
    for p in plan[:days]:
        day = dt.date.today() + dt.timedelta(days=int(p["day"]) - 1)
        if day < d:
            _remind(f"Revision: {p['focus']}", dt.datetime.combine(day, dt.time.fromisoformat(at)))
    return f"Revision plan set: {len(plan[:days])} days up to {d.strftime('%d %b')}, a reminder each day at {at}. Day 1: {plan[0]['focus']}."


def start() -> None:
    def loop():
        ran: dict[str, str] = {}
        while True:
            now = dt.datetime.now()
            today = now.date().isoformat()
            try:
                from . import rules
                if not rules.paused():
                    if now.strftime("%H:%M") >= cfg.get("study.plan_check_at", "19:00") and ran.get("plan") != today:
                        ran["plan"] = today
                        plan_assignments()
                    if now.strftime("%H:%M") >= cfg.get("study.primers_at", "20:30") and ran.get("primer") != today \
                            and now.hour < 23:
                        ran["primer"] = today
                        said = primers()
                        if rules._speak and said.startswith("Primers"):
                            rules._speak(said.split(" [")[0])
            except Exception:
                pass
            time.sleep(60)
    threading.Thread(target=loop, daemon=True, name="athena-study").start()
