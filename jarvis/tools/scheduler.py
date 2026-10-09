"""Reminders and recurring tasks that survive restarts.

Jobs live in data/jobs.json. A background scheduler thread runs inside
whichever interface you launch; anything due while Athena was closed
fires once on the next start.
"""
from __future__ import annotations

import os
import re
import threading
import time
import uuid
from datetime import datetime, timedelta

from ..config import DATA
from ..store import load_json, save_json
from ..registry import tool

TAG = ["schedule"]

JOBS_FILE = DATA / "jobs.json"
_notify_hook = None  # set by the UI so reminders can surface properly


def set_notifier(fn) -> None:
    """UIs call this to receive reminder text when a job fires."""
    global _notify_hook
    _notify_hook = fn


def _load() -> list[dict]:
    """Pending jobs, or [] if none. Raises StoreError on corruption."""
    return load_json(JOBS_FILE, [])


def _save(jobs: list[dict]) -> None:
    save_json(JOBS_FILE, jobs)


_WORD_NUMBERS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "couple": 2, "few": 3, "half": 0.5, "quarter": 0.25,
}

_UNITS = {
    "second": 1, "seconds": 1, "sec": 1, "secs": 1,
    "minute": 60, "minutes": 60, "min": 60, "mins": 60,
    "hour": 3600, "hours": 3600, "hr": 3600, "hrs": 3600,
    "day": 86400, "days": 86400,
    "week": 604800, "weeks": 604800,
}


_UNIT_WORDS = "|".join(sorted(_UNITS, key=len, reverse=True))

# "2158" spoken as a time. Whisper writes clock times without the colon, so
# the digits arrive glued together. Not applied when a unit follows, or
# "in 1530 seconds" would become half past three.
_BARE_CLOCK = re.compile(r"\b(\d{1,2})(\d{2})\b(?!\s*(?:" + _UNIT_WORDS + r")\b)", re.I)


def _despeech(s: str) -> str:
    """Undo the transcription habits that hide a time from the parsers."""
    return _BARE_CLOCK.sub(r"\1:\2", s)


def _normalise(s: str) -> str:
    """Make spoken time phrasing look like something parseable.

    Speech gives "13.46 pm", "half an hour", "6 30 pm" - none of which the
    plain formats accept, and all of which people actually say.
    """
    s = s.strip().lower()
    s = re.sub(r"[,]", " ", s)
    s = re.sub(r"\bo'?clock\b", "", s)
    # "p.m." and the full stop speech puts at the end of every sentence both
    # stop the meridiem matching, which loses the afternoon half of the day.
    s = re.sub(r"\b([ap])\.?\s*m\.?\b", r"\1m", s)
    s = s.strip(" .,!?")
    s = re.sub(r"\bplease\b|\bfrom now\b|\bfor (me|today)\b", " ", s)
    s = _despeech(s)
    # "13.46" / "13 46" -> "13:46"
    s = re.sub(r"(?<=\d)[.\s](?=\d{2}\b)", ":", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _relative(s: str, now: datetime) -> datetime | None:
    """'in 10 minutes', 'in half an hour', 'in a couple of hours'."""
    m = re.match(r"^(?:in|after)\s+(.*)$", s)
    if not m:
        return None
    rest = m.group(1).replace(" of ", " ").split()
    if not rest:
        return None

    # "a couple of hours" - the article is not the quantity when a real one
    # follows it, and reading it as 1 silently halves the wait.
    if (len(rest) > 1 and rest[0] in ("a", "an")
            and (rest[1] in _WORD_NUMBERS
                 or re.fullmatch(r"\d+(?:\.\d+)?", rest[1]))):
        rest = rest[1:]

    qty, idx = None, 0
    # numbers may be digits or words, and "half an hour" needs both read
    for i, tok in enumerate(rest):
        if re.fullmatch(r"\d+(?:\.\d+)?", tok):
            qty = float(tok)
            idx = i + 1
            break
        if tok in _WORD_NUMBERS:
            qty = _WORD_NUMBERS[tok]
            idx = i + 1
            # "half an hour" - skip the article that follows
            if idx < len(rest) and rest[idx] in ("a", "an"):
                idx += 1
            break
    if qty is None:
        return None

    unit = next((t for t in rest[idx:] if t in _UNITS), None)
    if unit is None:
        unit = "minutes"
    return now + timedelta(seconds=qty * _UNITS[unit])


def _absolute(s: str, now: datetime) -> datetime | None:
    """'at 6pm', '18:30', 'tomorrow 9am', 'tonight at 8'."""
    day_offset = 0
    if "tomorrow" in s:
        day_offset = 1
        s = s.replace("tomorrow", " ")
    if "tonight" in s:
        s = s.replace("tonight", " night ")
    # "at 5 in the evening" became 5 AM: the part of the day was dropped
    # (28 Sep 2026). It decides am/pm, and with no clock it sets one.
    part = re.search(r"\b(?:in\s+the\s+|this\s+|at\s+|early\s+|late\s+)?(morning|afternoon|evening|night)\b", s)
    if part:
        pod = part.group(1)
        s = s[:part.start()] + " " + s[part.end():]
        if not re.search(r"\d", s):
            s += {"morning": " 9 am", "afternoon": " 3 pm", "evening": " 6 pm", "night": " 9 pm"}[pod]
        elif not re.search(r"\b(?:am|pm)\b", s):
            s += " am" if pod == "morning" else " pm"
    s = re.sub(r"^(at|on|by)\s+", "", s.strip()).strip()
    s = re.sub(r"\s+", " ", s)

    # Speech produces contradictions like "13:46 pm". The 24-hour number is
    # the reliable part, so drop the meridiem rather than failing outright.
    m = re.match(r"^(\d{1,2})(:\d{2})?\s*(am|pm)$", s)
    if m and int(m.group(1)) > 12:
        s = m.group(1) + (m.group(2) or "")

    for fmt in ("%Y-%m-%d %H:%M", "%H:%M", "%I:%M %p", "%I:%M%p",
                "%I %p", "%I%p", "%H"):
        try:
            t = datetime.strptime(s, fmt)
        except ValueError:
            continue
        if fmt == "%Y-%m-%d %H:%M":
            return t
        target = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
        target += timedelta(days=day_offset)
        if day_offset == 0 and target <= now:
            target += timedelta(days=1)   # that time has already passed today
        return target
    return None


def _parse_when(when: str) -> datetime | None:
    """Turn a spoken or typed time into a datetime, or None if unintelligible."""
    s = _normalise(when)
    if not s:
        return None
    now = datetime.now()
    return _relative(s, now) or _absolute(s, now)


# ---------- recognising a reminder request without the model ----------
#
# A 7B model picks add_reminder out of fifty-odd tools only sometimes. The
# rest of the time it invents `set_alarm`, or announces that no tool exists
# for reminders. Reminder phrasing is regular enough to parse outright, so it
# does not get left to chance: parse_request runs first and only hands the
# turn to the model when it genuinely cannot tell what time was meant.

_NUM_WORDS = "|".join(sorted(_WORD_NUMBERS, key=len, reverse=True))

_TRIGGER = re.compile(r"\b(remind|reminders?|alarms?|wake me)\b", re.I)

# Everything people say before getting to the point.
_LEAD = re.compile(
    r"^\W*(?:(?:athena|jarvis)\b\W*)?"
    r"(?:please\s+)?"
    r"(?:(?:can|could|would|will)\s+you\s+)?"
    r"(?:please\s+)?"
    r"(?:set|make|create|add|put|send|give|start|schedule|book)?\s*"
    r"(?:up\s+)?(?:a|an|the)?\s*(?:new\s+)?"
    r"(?:reminders?|remind(?:\s+me)?|alarms?|wake\s+me(?:\s+up)?)\s*",
    re.I,
)

# "in 20 minutes", "for fifteen minutes", "after half an hour"
_REL_PHRASE = re.compile(
    r"\b(?:in|after|within|for)\s+"
    # The article is optional in front of the quantity ("a couple of hours")
    # and can also be the quantity itself ("an hour").
    r"((?:(?:a|an)\s+)?(?:\d+(?:\.\d+)?|" + _NUM_WORDS + r")"
    r"(?:\s+(?:a|an|of))*\s*(?:" + _UNIT_WORDS + r"))\b",
    re.I,
)

# "at 6pm", "for 21:58 pm", "by 9 o'clock"
_CLOCK_PHRASE = re.compile(
    r"\b(?:at|for|by|around|about)\s+"
    r"(\d{1,2}(?:[:.\s]\d{2})?\s*(?:a\.?m\.?|p\.?m\.?|o'?clock)?)",
    re.I,
)

# A time standing on its own, with a meridiem to prove it is one.
_LOOSE_CLOCK = re.compile(
    r"\b(\d{1,2}(?:[:.]\d{2})?\s*(?:a\.?m\.?|p\.?m\.?|o'?clock))", re.I
)

# "remind me to call papa at 9pm" -> "call papa"
_TASK = re.compile(
    r"\bto\s+(.+?)(?=\s+(?:at|in|on|by|after|around|about|within|"
    r"tomorrow|tonight|this\s+(?:morning|afternoon|evening))\b|[.!?]*\s*$)",
    re.I,
)

_DAY_WORD = re.compile(r"\b(tomorrow(?:\s+(?:morning|afternoon|evening|night))?|tonight|"
                       r"this\s+(?:morning|afternoon|evening))\b", re.I)
# "at 5 in the evening": the part of the day after the clock decides am/pm.
_PART_AFTER = re.compile(r"^\s*(in\s+the\s+(?:morning|afternoon|evening|night)|at\s+night)\b", re.I)


def parse_request(utterance: str) -> tuple[str, str] | None:
    """Read a spoken reminder request as (text, when), or None if it isn't one.

    Returns None whenever the time cannot be pinned down, so a genuine
    question like "what reminders do I have" still reaches the model.
    """
    if not utterance or not _TRIGGER.search(utterance):
        return None

    u = _despeech(utterance.strip())
    rest = _LEAD.sub("", u, count=1).strip() or u

    day = _DAY_WORD.search(u)
    prefix = (day.group(1) + " ") if day else ""

    m = _REL_PHRASE.search(rest)
    if m:
        when = "in " + m.group(1)
    else:
        m = _CLOCK_PHRASE.search(rest) or _LOOSE_CLOCK.search(rest)
        if m:
            after = _PART_AFTER.match(rest[m.end():])
            when = prefix + "at " + m.group(1) + ((" " + after.group(1)) if after else "")
        elif day and re.search(r"morning|afternoon|evening|night", day.group(1), re.I):
            when = day.group(1)          # "remind me tomorrow morning to ..."
        else:
            return None

    if _parse_when(when) is None:
        return None

    task = _TASK.search(u)
    text = task.group(1).strip(" .,!?") if task else ""
    return (text or "Reminder"), when


@tool(tags=TAG)
def add_reminder(text: str, when: str, repeat: str = "once") -> str:
    """Set a reminder or a recurring task.

    text: what to remind you about
    when: when to fire, for example 'in 20 minutes', 'at 18:30', 'tomorrow 9am'
    repeat: one of once, daily, weekly, hourly
    """
    fire_at = _parse_when(when)
    if fire_at is None:
        return (f"ERROR: could not understand the time {when}. "
                "Try 'in 20 minutes', 'at 18:30' or 'tomorrow 9am'.")
    if repeat not in ("once", "daily", "weekly", "hourly"):
        return f"ERROR: repeat must be once, daily, weekly or hourly (got {repeat})."

    jobs = _load()
    jobs.append({
        "id": uuid.uuid4().hex[:8],
        "text": text,
        "fire_at": fire_at.isoformat(timespec="seconds"),
        "repeat": repeat,
        "created": datetime.now().isoformat(timespec="seconds"),
    })
    _save(jobs)
    when_str = fire_at.strftime("%a %d %b at %I:%M %p").replace(" 0", " ")
    suffix = "" if repeat == "once" else f", repeating {repeat}"
    return f"Reminder set for {when_str}{suffix}: {text}"


@tool(tags=TAG)
def list_reminders() -> str:
    """List all pending reminders and recurring tasks."""
    jobs = sorted(_load(), key=lambda j: j["fire_at"])
    if not jobs:
        return "No reminders set."
    lines = []
    for j in jobs:
        t = datetime.fromisoformat(j["fire_at"]).strftime("%a %d %b %I:%M %p")
        rep = "" if j["repeat"] == "once" else f" [{j['repeat']}]"
        lines.append(f"{j['id']}  {t}{rep}  {j['text']}")
    return "\n".join(lines)


@tool(tags=TAG, destructive=True)
def cancel_reminder(reminder_id: str) -> str:
    """Cancel a reminder by its id or by what it says, or pass 'all' to clear
    every reminder.

    reminder_id: the 8-character id from list_reminders, words from the reminder ("the dentist one"), or 'all'
    """
    jobs = _load()
    key = reminder_id.strip()
    if key.lower() == "all":
        _save([])
        return f"Cancelled all {len(jobs)} reminder(s)."
    hit = [j for j in jobs if j["id"] == key]
    if not hit:
        # The model passes the reminder's words at least as often as its id:
        # "cancel the test athena reminder" came as "test athena", got "No
        # reminder with id", and was reported as cancelled (28 Sep 2026).
        words = [w for w in re.findall(r"[a-z0-9]+", key.lower())
                 if w not in ("the", "a", "my", "reminder", "reminders", "one", "for", "to", "about")]
        if words:
            hit = [j for j in jobs if all(w in str(j.get("text", "")).lower() for w in words)]
    if not hit:
        return f"NOT CANCELLED: no reminder matches '{key}'. Nothing was changed."
    if len(hit) > 1:
        return ("NOT CANCELLED: several reminders match - which one? "
                + "; ".join(f"{j['id']} {j.get('text', '')}" for j in hit))
    _save([j for j in jobs if j["id"] != hit[0]["id"]])
    return f"Cancelled the reminder: {hit[0].get('text', hit[0]['id'])}."


# ---------- background runner ----------

_STEP = {"hourly": timedelta(hours=1), "daily": timedelta(days=1), "weekly": timedelta(weeks=1)}

# Voice, web and hotkey all run at once, and each used to start its own
# scheduler over the same jobs.json. Whichever ticked first fired the job and
# deleted it - but only voice can actually speak, so most reminders were
# consumed in silence and looked like they had never been saved. One process
# owns the queue at a time, and the one that can be heard wins.
_LOCK_FILE = DATA / "scheduler.lock"
_UI_PRIORITY = {"voice": 3, "gui": 2, "hotkey": 1, "cli": 1, "web": 0}
_STALE_AFTER = 90.0   # a holder that stopped beating this long ago is gone
_YIELD_SECONDS = 90.0  # long enough for voice to load Whisper and take over


def _claim(ui: str) -> bool:
    """True if this process currently owns the reminder queue."""
    me = {"ui": ui, "pid": os.getpid(), "beat": time.time()}
    try:
        held = load_json(_LOCK_FILE, {})
    except Exception:
        held = {}

    if held.get("pid") == me["pid"]:
        save_json(_LOCK_FILE, me)      # still ours - refresh the heartbeat
        return True

    alive = (time.time() - float(held.get("beat") or 0)) < _STALE_AFTER
    outrank = _UI_PRIORITY.get(ui, 0) > _UI_PRIORITY.get(held.get("ui") or "", -1)
    if held and alive and not outrank:
        return False

    # Take it, then read back: if two processes claimed at once only one
    # pid survives, and the loser must not also fire.
    save_json(_LOCK_FILE, me)
    time.sleep(0.4)
    try:
        return load_json(_LOCK_FILE, {}).get("pid") == me["pid"]
    except Exception:
        return False


def _tick(hold_before: datetime | None = None) -> list[str]:
    """Fire anything due. Returns the texts that fired.

    hold_before: leave jobs that fell due before this moment untouched. Used
        to keep the startup backlog intact until the interface that can speak
        has finished loading.
    """
    jobs = _load()
    if not jobs:
        return []
    now = datetime.now()
    fired: list[str] = []
    keep: list[dict] = []

    for j in jobs:
        try:
            due = datetime.fromisoformat(j["fire_at"])
        except Exception:
            continue
        if due > now or (hold_before is not None and due < hold_before):
            keep.append(j)
            continue

        fired.append(j["text"])
        step = _STEP.get(j["repeat"])
        if step:
            # roll forward past any occurrences missed while Athena was closed
            while due <= now:
                due += step
            j["fire_at"] = due.isoformat(timespec="seconds")
            keep.append(j)

    if fired:
        _save(keep)
    return fired


def _phone_reminder(text: str) -> None:
    """Ring the phone as well: a reminder spoken to an empty room is the
    commonest way one gets missed. The key carries the minute it fired, so a
    daily reminder rings every day but one firing never rings twice."""
    from .. import alerts

    said = "Reminder." if text.strip().lower() == "reminder" else f"Reminder: {text}."
    alerts.submit("reminders", f"reminder:{datetime.now():%Y-%m-%dT%H:%M}:{text}", said)


def start_scheduler(ui: str = "cli", interval_seconds: int = 20) -> threading.Thread:
    """Start the background thread that fires reminders.

    ui: which interface this is, so the queue goes to the one the user can
        actually hear when several are running at once.
    """

    started = time.time()
    started_at = datetime.now()
    # Voice is last to finish loading, so a quicker interface would otherwise
    # swallow the whole backlog before there is anything to speak through.
    yields = _UI_PRIORITY.get(ui, 0) < max(_UI_PRIORITY.values())

    def loop() -> None:
        while True:
            try:
                # A process that does not own the queue keeps looping so it
                # can take over if the owner dies, but never consumes a job.
                if _claim(ui):
                    hold = (started_at if yields
                            and time.time() - started < _YIELD_SECONDS else None)
                    for text in _tick(hold):
                        if _notify_hook:
                            _notify_hook(text)
                        else:
                            print(f"\n[REMINDER] {text}\n")
                        _phone_reminder(text)
            except Exception:
                pass
            time.sleep(interval_seconds)

    t = threading.Thread(target=loop, daemon=True, name="jarvis-scheduler")
    t.start()
    return t
