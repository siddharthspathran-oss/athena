"""A to-do list: things to get done, with an optional day, ticked off when done.

Reminders fire at a moment ("remind me at 5 to call mom"); a task is just
something that needs doing ("finish the DSD lab record", "submit MATH
assignment by Friday"). The Command Center's Tasks page shows today's and
the remaining ones side by side with reminders and the rest of today's
classes. Kept in data/tasks.json.
"""
from __future__ import annotations

import datetime as dt
import re
import secrets

from ..config import DATA
from ..registry import tool
from ..store import load_json, save_json

TAG = ["schedule"]
FILE = DATA / "tasks.json"

_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _due(when: str) -> str | None:
    """"today", "tomorrow", "friday", "2026-10-02" -> ISO date, or None."""
    w = (when or "").strip().lower()
    if not w or w in ("none", "someday", "any time", "anytime", "later"):
        return None
    today = dt.date.today()
    if w in ("today", "tonight"):
        return today.isoformat()
    if w == "tomorrow":
        return (today + dt.timedelta(days=1)).isoformat()
    w = re.sub(r"^(?:by|on|this|next)\s+", "", w)
    if w in _DAYS:
        ahead = (_DAYS.index(w) - today.weekday()) % 7 or 7
        return (today + dt.timedelta(days=ahead)).isoformat()
    try:
        return dt.date.fromisoformat(w).isoformat()
    except ValueError:
        return None


def load() -> list[dict]:
    return load_json(FILE, [])


def save(tasks: list[dict]) -> None:
    save_json(FILE, tasks)


@tool(tags=TAG)
def add_task(text: str, due: str = "") -> str:
    """Add something to the to-do list.

    text: what needs doing
    due: "today", "tomorrow", a weekday, a date, or empty for no date
    """
    text = re.sub(r"\s+", " ", text or "").strip().rstrip(".")
    if not text:
        return "ERROR: what is the task?"
    tasks = load()
    item = {"id": secrets.token_hex(3), "text": text, "due": _due(due),
            "done": False, "created": dt.datetime.now().isoformat(timespec="seconds")}
    tasks.append(item)
    save(tasks)
    when = f" for {due.strip()}" if item["due"] else ""
    return f"Added to your tasks{when}: {text}."


@tool(tags=TAG)
def complete_task(task: str) -> str:
    """Tick a task off the to-do list.

    task: its id, or words from it
    """
    tasks = load()
    key = task.strip().lower()
    for t in tasks:
        if not t["done"] and (t["id"] == key or key in t["text"].lower()):
            t["done"] = True
            t["finished"] = dt.datetime.now().isoformat(timespec="seconds")
            save(tasks)
            return f"Done: {t['text']}."
    return f"ERROR: no open task matching '{task}'."


@tool(tags=TAG)
def list_tasks() -> str:
    """List the to-do list: what is due today (or overdue), and what remains."""
    today = dt.date.today().isoformat()
    open_ = [t for t in load() if not t["done"]]
    if not open_:
        return "Your to-do list is empty."
    now = [t for t in open_ if t["due"] and t["due"] <= today]
    rest = [t for t in open_ if t not in now]
    out = []
    if now:
        out.append("Today:")
        out += [f"  - {t['text']}" + (" (overdue)" if t["due"] < today else "") for t in now]
    if rest:
        out.append("Remaining:")
        out += [f"  - {t['text']}" + (f" (due {t['due']})" if t["due"] else "") for t in rest]
    return "\n".join(out)
