"""Microsoft Teams: today's classes from the Teams calendar, and joining them.

Read from the Teams app itself (new Teams, MSTeams), not the Graph API - a
school tenant blocks outside apps, but the calendar Teams shows is plain UI
Automation. Each meeting is one button whose name carries everything:

    "Oct 5 2026 | 13:00 - 13:55 | Marketing Fundamentals (SOB20B002_3) | Subba Prabha, ..."
    "Canceled: Oct 8 2026 | 13:00 - 13:55 | Introduction to Digital Business ..."

Every morning (teams.sync_at) and at start-up, today's meetings become tasks
and reminders, and are kept in data/teams.json for the Command Centre. At the
start time Athena joins - mic and camera off - when you are at the PC
(teams.auto_join: at_pc), always, or never (off); away from the PC she pushes
it to your phone instead.
"""
from __future__ import annotations

import ctypes
import datetime as dt
import re
import subprocess
import threading
import time

from ..config import DATA, cfg
from ..registry import tool
from ..store import load_json, save_json

TAG = ["schedule"]
STATE = DATA / "teams.json"
_TILE = re.compile(r"^(?P<cancel>Canceled:\s*)?(?P<date>\w{3} \d{1,2} \d{4}) \| (?P<start>\d{1,2}:\d\d) - "
                   r"(?P<end>\d{1,2}:\d\d) \| (?P<title>.+?) \| (?P<who>[^,|]+)")
_lock = threading.Lock()


def _hwnd(launch: bool = True) -> int | None:
    from .. import winapi
    find = lambda: next((h for h, t, _p in winapi.windows_with_pids() if t.endswith("Microsoft Teams")), None)
    h = find()
    if h or not launch:
        return h
    subprocess.Popen(["explorer.exe", r"shell:AppsFolder\MSTeams_8wekyb3d8bbwe!MSTeams"])
    for _ in range(30):
        time.sleep(1)
        h = find()
        if h:
            time.sleep(3)           # the window exists before its content does
            return h
    return None


def _tiles(h) -> list[dict]:
    from .. import uia
    out = []
    for e in uia.elements(h, uia.BUTTON):
        m = _TILE.match(e.name or "") if e.on_screen else None
        if m:
            d = dt.datetime.strptime(m["date"], "%b %d %Y").date()
            out.append({"date": d.isoformat(), "start": m["start"].zfill(5), "end": m["end"].zfill(5),
                        "title": m["title"].strip(), "who": m["who"].strip(),
                        "canceled": bool(m["cancel"]), "tile": e.name})
    return out


def _calendar(h) -> bool:
    from .. import uia, winapi
    winapi.focus_hwnd(h)
    uia.wake(h, settle=0.6)
    cal = uia.find_button(h, "Calendar (Ctrl+5)")
    if cal is None or not cal.press(h):
        return False
    for _ in range(12):
        time.sleep(1)
        uia.wake(h, settle=0.3)
        if any(e.name.startswith("Go to today") for e in uia.elements(h, uia.BUTTON) if e.name):
            return True
    return False


def _show_week_of(h, day: dt.date) -> list[dict]:
    """The tiles of the work week containing day (today's week, or the next)."""
    from .. import uia
    today = next((e for e in uia.elements(h, uia.BUTTON) if e.on_screen and (e.name or "").startswith("Go to today")), None)
    if today is not None:
        today.press(h)
        time.sleep(2)
    for _ in range(3):
        uia.wake(h, settle=0.6)
        tiles = _tiles(h)
        dates = {t["date"] for t in tiles}
        if day.isoformat() in dates or not tiles or min(dates) > day.isoformat():
            return tiles
        nxt = next((e for e in uia.elements(h, uia.BUTTON) if e.on_screen and (e.name or "").startswith("Go to next week")), None)
        if nxt is None:
            return tiles
        nxt.press(h)
        time.sleep(2)
    return _tiles(h)


def read_day(day: dt.date) -> list[dict] | None:
    """Meetings on day, from the Teams app; None if Teams could not be read."""
    from .. import winapi
    with _lock:
        before = ctypes.windll.user32.GetForegroundWindow()
        h = _hwnd()
        if h is None or not _calendar(h):
            return None
        tiles = _show_week_of(h, day)
        if before:
            winapi.focus_hwnd(before)       # give the screen back
    return sorted((t for t in tiles if t["date"] == day.isoformat()), key=lambda t: t["start"])


def sync(note=None) -> str:
    """Today's meetings -> data/teams.json, tasks and 5-minute reminders."""
    from . import todo
    today = dt.date.today()
    meets = read_day(today)
    if meets is None:
        return "I couldn't read the Teams calendar - is Teams signed in?"
    meets = _vs_timetable(meets)
    state = load_json(STATE, {})
    same = state.get("day") == today.isoformat()
    save_json(STATE, {"day": today.isoformat(), "read": time.time(), "meetings": meets,
                      "joined": state.get("joined", []) if same else [], "warned": state.get("warned", []) if same else []})
    live = [m for m in meets if not m["canceled"]]
    tasks = todo.load()
    have = {t["text"] for t in tasks}
    for m in live:
        text = f"{m['start']} {_spoken(m)} (Teams)"
        if text not in have:
            todo.add_task(text, today.isoformat())
    if note:
        note(f"teams: {len(live)} meeting(s) today" + (f", {len(meets) - len(live)} cancelled" if len(meets) > len(live) else ""))
    return _today_text(meets)


def _today_text(meets: list[dict]) -> str:
    live = [m for m in meets if not m["canceled"]]
    gone = len(meets) - len(live)
    if not meets:
        return "No Teams meetings today."
    return (("Today on Teams: " + "; ".join(f"{m['start']} {_spoken(m)}" for m in live) + "."
             if live else "") + (f" {gone} cancelled." if gone else "")).strip()


def _idle_seconds() -> float:
    class LII(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
    lii = LII(cbSize=ctypes.sizeof(LII))
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii))
    return (ctypes.windll.kernel32.GetTickCount() - lii.dwTime) / 1000.0


def join(m: dict) -> str:
    """Open the meeting from the calendar, press Join, then Join now - mic and camera off."""
    from .. import uia, winapi
    with _lock:
        h = _hwnd()
        if h is None or not _calendar(h):
            return "I couldn't open Teams."
        _show_week_of(h, dt.date.fromisoformat(m["date"]))
        tile = next((e for e in uia.elements(h, uia.BUTTON) if e.on_screen and (e.name or "").startswith(
            m["tile"][:60])), None)
        if tile is None or not tile.press(h):
            return f"I couldn't find {_spoken(m)} in the Teams calendar."
        time.sleep(2.5)
        uia.wake(h, settle=0.5)
        btn = uia.find_button(h, "Join")
        if btn is None or not btn.press(h):
            winapi.press_vk(0x1B)
            return f"{_spoken(m)} has no Join button yet."
        # The pre-join screen: turn camera and mic off if they are on, then Join now.
        for _ in range(30):
            time.sleep(1)
            mh = next((x for x, t, _p in winapi.windows_with_pids() if "Microsoft Teams" in t and x != h), h)
            uia.wake(mh, settle=0.3)
            names = [(e, (e.name or "").lower()) for e in uia.elements(mh) if e.on_screen and e.name]
            now = next((e for e, n in names if n == "join now"), None)
            if now is None:
                continue
            for e, n in names:
                if n.startswith(("turn camera off", "camera on", "video on")) or n.startswith(("mute mic", "microphone on", "mute microphone")):
                    e.press(mh)
            now.press(mh)
            # Once in: the controls name their own state ("Mute mic", "Turn camera off").
            time.sleep(6)
            uia.wake(mh, settle=0.5)
            for e in uia.elements(mh, uia.BUTTON):
                n = (e.name or "").lower()
                if e.on_screen and (n.startswith("mute mic") or n.startswith("turn camera off")):
                    e.press(mh)
            return f"Joined {_spoken(m)} with your mic and camera off."
        return f"Opened {_spoken(m)} but the Join now button never appeared."


def _meetings_today() -> list[dict]:
    s = load_json(STATE, {})
    return s.get("meetings", []) if s.get("day") == dt.date.today().isoformat() else []


def upcoming(weeks: int = 3) -> list[dict] | None:
    """Every meeting from this work week on, `weeks` weeks ahead; None if Teams could not be read."""
    from .. import uia, winapi
    with _lock:
        before = ctypes.windll.user32.GetForegroundWindow()
        h = _hwnd()
        if h is None or not _calendar(h):
            return None
        today = next((e for e in uia.elements(h, uia.BUTTON)
                      if e.on_screen and (e.name or "").startswith("Go to today")), None)
        if today is not None:
            today.press(h)
            time.sleep(2)
        seen: dict[str, dict] = {}
        for w in range(weeks):
            uia.wake(h, settle=0.6)
            for t in _tiles(h):
                seen[t["tile"]] = t
            nxt = next((e for e in uia.elements(h, uia.BUTTON)
                        if e.on_screen and (e.name or "").startswith("Go to next week")), None)
            if nxt is None or w == weeks - 1:
                break
            nxt.press(h)
            time.sleep(2)
        if today is not None:
            today.press(h)                  # leave the calendar on this week
        if before:
            winapi.focus_hwnd(before)
    return sorted(seen.values(), key=lambda t: (t["date"], t["start"]))


def _vs_timetable(meets: list[dict]) -> list[dict]:
    """A Teams meeting counts only if the UPES timetable has that class.
    Recurring Teams meetings run on through exams: Marketing Fundamentals
    sat in Teams every afternoon of the 5-10 Oct 2026 mid-sems, when the
    timetable had no classes at all. Days the timetable does not cover are
    taken as Teams says."""
    from .. import upes
    for m in meets:
        day = dt.date.fromisoformat(m["date"])
        if m["canceled"] or not upes.covers(day):
            continue
        t = dt.datetime.strptime(m["start"], "%H:%M")
        if not any(abs((dt.datetime.strptime(c["start"], "%H:%M") - t).total_seconds()) <= 1800
                   for c in upes.classes(day, cached_only=True) if not c["cancelled"]):
            m["canceled"], m["why"] = True, "not on the UPES timetable"
    return meets


def _spoken(m: dict) -> str:
    """'Marketing Fundamentals (SOB20B002_3)' -> 'Marketing Fundamentals': the code is not for reading out."""
    return re.sub(r"\s*\([A-Z0-9_]{5,}[^)]*\)\s*$", "", m["title"]) or m["title"]


def _when(m: dict) -> str:
    d = dt.date.fromisoformat(m["date"])
    gap = (d - dt.date.today()).days
    day = "today" if gap == 0 else "tomorrow" if gap == 1 else d.strftime("%A %d %B").replace(" 0", " ")
    t = dt.datetime.strptime(m["start"], "%H:%M").strftime("%I:%M %p").lstrip("0").replace(":00", "")
    return f"{day} at {t}"


@tool(tags=TAG, timeout=180)
def teams_meetings(day: str = "today") -> str:
    """List classes and meetings from the Microsoft Teams calendar.

    day: today, tomorrow, or next (the next one coming up, however far ahead)
    """
    if day.strip().lower().startswith("next"):
        # From the UPES timetable: it covers the whole semester and knows
        # about exams; the Teams calendar does not.
        from .. import upes
        ahead = [c for c in upes.upcoming() if "teams" in c["room"].lower()]
        if ahead:
            c = ahead[0]
            first = dt.date.fromisoformat(c["date"])
            gap = ("" if (first - dt.date.today()).days <= 3 else
                   " There are no classes on your timetable before then.")
            return (f"Your next Teams class is {c['course']}, "
                    f"{_when({'date': c['date'], 'start': c['start']})}.{gap}")
        meets = upcoming()
        if meets is None:
            return "I couldn't read the Teams calendar - is Teams signed in?"
        now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        ahead = [m for m in _vs_timetable(meets) if not m["canceled"] and f"{m['date']} {m['end']}" > now]
        if not ahead:
            return "Nothing on your Teams calendar for the next three weeks."
        m = ahead[0]
        return f"Your next Teams meeting is {_spoken(m)}, {_when(m)}."
    if day.strip().lower().startswith("tom"):
        meets = read_day(dt.date.today() + dt.timedelta(days=1))
        if meets is None:
            return "I couldn't read the Teams calendar - is Teams signed in?"
        live = [m for m in _vs_timetable(meets) if not m["canceled"]]
        return ("Tomorrow on Teams: " + "; ".join(f"{m['start']} {_spoken(m)}" for m in live) + "."
                if live else "Nothing on Teams tomorrow.")
    # Read this morning (07:30) or within three hours: answer from that rather
    # than driving the Teams app again - 12 s every time (3 Oct 2026).
    state = load_json(STATE, {})
    if state.get("day") == dt.date.today().isoformat() and time.time() - state.get("read", 0) < 3 * 3600:
        return _today_text(state.get("meetings", []))
    return sync()


@tool(tags=TAG, timeout=120)
def join_teams_meeting(which: str = "next") -> str:
    """Join a Teams class or meeting now, with mic and camera off.

    which: "next" for the current or next one, or part of its name
    """
    meets = [m for m in (_meetings_today() or read_day(dt.date.today()) or []) if not m["canceled"]]
    if not meets:
        return "There's nothing on Teams today."
    now = dt.datetime.now().strftime("%H:%M")
    w = which.strip().lower()
    pick = (next((m for m in meets if w in m["title"].lower()), None) if w not in ("next", "", "now", "current")
            else next((m for m in meets if m["end"] > now), None))
    if pick is None:
        return "That one isn't on today's Teams calendar." if w not in ("next", "") else "No more Teams meetings today."
    state = load_json(STATE, {})                    # so the start-time auto-join doesn't open it again
    state.setdefault("joined", []).append(pick["date"] + pick["start"] + pick["title"])
    save_json(STATE, state)
    return join(pick)


def start(note=print) -> None:
    """In the voice process: sync each morning, and join at start time."""
    if not cfg.get("teams.enabled", True):
        return

    def loop():
        from .. import alerts, live
        last_sync = ""
        while True:
            try:
                today = dt.date.today().isoformat()
                at = cfg.get("teams.sync_at", "07:30")
                if last_sync != today and (dt.datetime.now().strftime("%H:%M") >= at or not _meetings_today()):
                    note(sync(note=lambda s: live.note(s)))
                    last_sync = today
                mode = str(cfg.get("teams.auto_join", "at_pc"))
                state = load_json(STATE, {})
                for m in _meetings_today():
                    key = m["date"] + m["start"] + m["title"]
                    start_t = dt.datetime.combine(dt.date.today(), dt.time.fromisoformat(m["start"]))
                    # A heads-up a few minutes before, with an offer to join now.
                    lead = int(cfg.get("teams.remind_minutes", 5))
                    soon = start_t - dt.timedelta(minutes=lead + 1) <= dt.datetime.now() < start_t - dt.timedelta(minutes=1)
                    if soon and not m["canceled"] and key not in state.get("warned", []):
                        state.setdefault("warned", []).append(key)
                        save_json(STATE, state)
                        from .. import offer, presence
                        if presence.away():
                            alerts.submit("teams", key + "-soon", f"{_spoken(m)} on Teams in {lead} minutes.")
                        else:
                            offer.set_choices([offer.Choice(m["title"], "join_teams_meeting", {"which": m["title"]})])
                            presence.interject(f"{_spoken(m)} starts on Teams in {lead} minutes. Shall I join you in now?")
                    if m["canceled"] or key in state.get("joined", []) or not (
                            start_t - dt.timedelta(minutes=1) <= dt.datetime.now() <= start_t + dt.timedelta(minutes=10)):
                        continue
                    state.setdefault("joined", []).append(key)
                    save_json(STATE, state)
                    if mode == "off":
                        continue
                    if mode == "always" or _idle_seconds() < 300:
                        said = join(m)
                        live.note(f"teams: {said}")
                        note(said)
                    else:
                        alerts.submit("teams", key, f"{_spoken(m)} is starting on Teams - join from your phone.")
            except Exception as e:
                note(f"[teams] {e}")
            time.sleep(30)
    threading.Thread(target=loop, daemon=True, name="athena-teams").start()
