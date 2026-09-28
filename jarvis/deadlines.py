"""Deadline watch: assignment and exam dates from your email and calendar,
added to Tasks by themselves.

Every few hours (and on "check my deadlines"):
  - Gmail: mail from the last week that talks about assignments, submissions,
    quizzes or exams. The local model reads subject + preview and keeps only
    real deadlines for you, with the date resolved ("by Friday" -> a date).
  - Google Calendar: events in the next two weeks named like a deadline.
Each message is read once (data/deadlines_seen.json), and a task that is
already on the list is not added twice.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import threading
import time
from difflib import SequenceMatcher

from .config import DATA, cfg
from .registry import tool
from .store import load_json, save_json

SEEN = DATA / "deadlines_seen.json"
_WORDS = re.compile(r"\b(assignment|deadline|due|submission|submit|quiz|exam|test|viva|project|lab record|last date)\b", re.I)
_lock = threading.Lock()


def _mail() -> list[dict]:
    from .tools.brief import _credentials
    creds = _credentials()
    if not creds:
        return []
    from googleapiclient.discovery import build
    svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
    q = ('newer_than:7d -category:promotions -category:social '
         '(assignment OR deadline OR due OR submit OR submission OR quiz OR exam OR viva OR "last date")')
    try:
        ids = svc.users().messages().list(userId="me", maxResults=25, q=q).execute().get("messages", [])
    except Exception:
        return []
    out = []
    for m in ids:
        try:
            msg = svc.users().messages().get(userId="me", id=m["id"], format="metadata",
                                             metadataHeaders=["Subject", "From", "Date"]).execute()
        except Exception:
            continue
        h = {x["name"]: x["value"] for x in msg.get("payload", {}).get("headers", [])}
        out.append({"id": m["id"], "subject": h.get("Subject", ""), "from": h.get("From", "").split("<")[0].strip(' "'),
                    "date": h.get("Date", ""), "preview": msg.get("snippet", "")[:300]})
    return out


def _extract(items: list[dict]) -> list[dict]:
    """The local model picks the real deadlines out of the mail."""
    if not items:
        return []
    from .llm import LLM
    today = dt.date.today()
    listing = "\n".join(f"[{i}] From: {m['from']} | Sent: {m['date'][:16]} | Subject: {m['subject']} | Preview: {m['preview']}"
                        for i, m in enumerate(items))
    msg = LLM().chat([
        {"role": "system", "content": (
            "You pick out deadlines for a university student from their email. Today is "
            f"{today:%A %Y-%m-%d}. For each message that gives the student something to hand in, "
            "sit or finish by a date (assignment, submission, quiz, exam, viva, project), output one "
            "JSON object: {\"n\": <message number>, \"task\": <short to-do, e.g. \"Submit DSD assignment 3\">, "
            "\"due\": <YYYY-MM-DD or \"\" if no date is given>}. Skip newsletters, ads, results, "
            "general announcements and anything already past. Reply with a JSON array only, [] if none.")},
        {"role": "user", "content": listing},
    ], temperature=0.0, max_tokens=600)
    text = (msg.content or "").strip()
    m = re.search(r"\[.*\]", text, re.S)
    try:
        found = json.loads(m.group(0)) if m else []
    except ValueError:
        return []
    out = []
    for f in found:
        try:
            n = int(f.get("n"))
            task = str(f.get("task", "")).strip()
        except (TypeError, ValueError):
            continue
        if not task or not (0 <= n < len(items)):
            continue
        due = str(f.get("due", "")).strip()
        try:
            if due and dt.date.fromisoformat(due) < today:
                continue                    # already past
        except ValueError:
            due = ""
        out.append({"task": task, "due": due, "src": f"email: {items[n]['subject'][:60]}"})
    return out


def _calendar() -> list[dict]:
    from .tools.brief import _google_events
    out, today = [], dt.date.today()
    for d in range(0, 14):
        day = today + dt.timedelta(days=d)
        try:
            events = _google_events(day)
        except Exception:
            return out
        for e in events:
            title = str(e.get("title") or e.get("summary") or e.get("what") or "")
            if _WORDS.search(title):
                out.append({"task": title.strip(), "due": day.isoformat(), "src": "calendar",
                            "key": f"cal:{day}:{title.lower()}"})
    return out


def _add(found: list[dict]) -> list[str]:
    from .tools import todo
    tasks = todo.load()
    added = []
    for f in found:
        low = f["task"].lower()
        dup = any(not t["done"] and (low in t["text"].lower() or t["text"].lower() in low
                                     or SequenceMatcher(None, low, t["text"].lower()).ratio() > 0.75)
                  for t in tasks)
        if dup:
            continue
        item = {"id": __import__("secrets").token_hex(3), "text": f["task"], "due": f["due"] or None,
                "done": False, "created": dt.datetime.now().isoformat(timespec="seconds"), "source": f["src"]}
        tasks.append(item)
        added.append(f["task"] + (f" (due {f['due']})" if f["due"] else ""))
    if added:
        todo.save(tasks)
    return added


def scan() -> list[str]:
    """One pass. Returns the tasks added."""
    with _lock:
        seen = load_json(SEEN, {"mail": [], "cal": []})
        mail = [m for m in _mail() if m["id"] not in seen["mail"]]
        found = _extract(mail)
        cal = [c for c in _calendar() if c["key"] not in seen["cal"]]
        added = _add(found + cal)
        seen["mail"] = (seen["mail"] + [m["id"] for m in mail])[-500:]
        seen["cal"] = (seen["cal"] + [c["key"] for c in cal])[-500:]
        save_json(SEEN, seen)
    if added:
        try:
            from . import live
            for a in added:
                live.note(f"deadline added to Tasks: {a[:70]}")
        except Exception:
            pass
    return added


@tool(tags=["schedule"], timeout=180)
def check_deadlines() -> str:
    """Look through recent email and the calendar for assignment, quiz and exam
    deadlines and add any new ones to the task list. Use for "check my
    deadlines", "any new assignments?"."""
    added = scan()
    if not added:
        return "No new deadlines in your email or calendar."
    return "Added to your tasks: " + "; ".join(added) + "."


def start(every_hours: float | None = None) -> None:
    hours = float(every_hours or cfg.get("deadlines.every_hours", 3))

    def loop():
        time.sleep(90)                  # let everything else start first
        while True:
            try:
                from . import gaming
                if not gaming.on():      # the scan runs the chat model
                    scan()
                    from .agent import warm
                    warm(voice=True)     # its prompt replaced the command one in the cache
            except Exception as e:
                print(f"[deadlines] {e}")
            time.sleep(hours * 3600)
    threading.Thread(target=loop, daemon=True, name="athena-deadlines").start()
