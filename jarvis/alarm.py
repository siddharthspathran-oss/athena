"""Alarms: a reminder or a deadline rings - a real alarm sound, looping, with
Athena saying what it is every few seconds - until you stop it.

Stop it by voice ("Athena, stop", "alarm off", "I'm up"), with the STOP button
the HUD shows while it rings, or snooze it ("snooze", "snooze for 5 minutes").
After alarm.max_minutes it stops by itself and the reminder goes to your phone
- an alarm must not ring all day in an empty room.

Runs in the voice process; any process can stop it (data/alarm.json).
"""
from __future__ import annotations

import datetime as dt
import threading
import time
import winsound

from .config import DATA, cfg
from .registry import tool
from .store import load_json, save_json

STATE = DATA / "alarm.json"
_lock = threading.Lock()
_thread: threading.Thread | None = None


def ringing() -> dict | None:
    s = load_json(STATE, {})
    return s if s.get("text") and not s.get("stop") else None


def stop() -> str:
    s = load_json(STATE, {})
    if not s.get("text") or s.get("stop"):
        return "Nothing is ringing."
    s["stop"] = True
    save_json(STATE, s)
    return f"Alarm off ({s['text'][:60]})."


def snooze(minutes: int = 10) -> str:
    s = ringing()
    if not s:
        return "Nothing is ringing."
    stop()
    from .studyauto import _remind
    _remind(s["text"], dt.datetime.now() + dt.timedelta(minutes=minutes))
    return f"Snoozed for {minutes} minutes."


def ring(text: str, speak, busy=lambda: False, says: int = 0) -> None:
    """Start ringing (or add this to the alarm already ringing).
    says: stop after saying it this many times (0 = until stopped or alarm.max_minutes)."""
    global _thread
    if not cfg.get("alarm.enabled", True):
        speak(f"Reminder: {text}")
        return
    with _lock:
        s = ringing()
        if s and _thread and _thread.is_alive():
            if text not in s["text"]:
                s["text"] += f"; {text}"
                save_json(STATE, s)
            return
        save_json(STATE, {"text": text, "since": time.time(), "stop": False, "says": int(says)})
        _thread = threading.Thread(target=_loop, args=(speak, busy), daemon=True, name="athena-alarm")
        _thread.start()


def _wait(seconds: float) -> bool:
    """Sleep in small steps; True if the alarm was stopped meanwhile."""
    end = time.time() + seconds
    while time.time() < end:
        if not ringing():
            return True
        time.sleep(0.25)
    return not ringing()


def _loop(speak, busy) -> None:
    sound = str(cfg.get("alarm.sound", r"C:\Windows\Media\Alarm03.wav"))
    started = time.time()
    limit = float(cfg.get("alarm.max_minutes", 10)) * 60
    try:
        from . import rules
        rules.log(f"alarm: {ringing()['text'][:80]}", "alarm")
    except Exception:
        pass
    said = 0
    while ringing() and time.time() - started < limit:
        try:
            winsound.PlaySound(sound, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_LOOP)
        except Exception:
            pass
        stopped = _wait(float(cfg.get("alarm.ring_seconds", 12)))
        winsound.PlaySound(None, 0)
        if stopped:
            break
        s = ringing()
        if s and s.get("says") and said >= s["says"]:
            break                                    # said enough: the phone gets it (below)
        if s:
            said += 1
            speak(f"Reminder: {s['text']}. Say Athena stop to turn it off.")
            time.sleep(1)
            for _ in range(60):                      # let the sentence finish
                if not busy() or not ringing():
                    break
                time.sleep(0.25)
    winsound.PlaySound(None, 0)
    s = load_json(STATE, {})
    if s.get("text") and not s.get("stop"):          # rang out with nobody there
        try:
            from . import alerts
            alerts.submit("reminders", f"alarm-missed:{int(s['since'])}", f"Missed alarm: {s['text']}")
        except Exception:
            pass
    save_json(STATE, {})


def start(speak, busy) -> None:
    """Task deadlines ring at alarm.tasks_at on the day they are due (once a day)."""
    day_file = DATA / "alarm_tasks_day.json"     # on disk: a restart rang it twice (3 Oct 2026)

    def loop():
        done = load_json(day_file, {}).get("day", "")
        while True:
            now = dt.datetime.now()
            if now.strftime("%H:%M") >= cfg.get("alarm.tasks_at", "09:00") and done != now.date().isoformat() \
                    and now.hour < 21:
                done = now.date().isoformat()
                save_json(day_file, {"day": done})
                try:
                    from . import rules
                    from .tools import todo
                    today = now.date().isoformat()
                    # Only what is due today rings, and it is said three times at most. Overdue work
                    # rang every day for ten minutes - one forum post was said 61 times in a week
                    # (21-28 Oct 2026, "it starts speaking itself"); the briefings already mention it.
                    due = [t["text"] for t in todo.load()
                           if not t["done"] and t.get("due") and t["due"][:10] == today]
                    if due and not rules.paused():
                        ring("Due today: " + "; ".join(due[:3]), speak, busy, says=3)
                except Exception:
                    pass
            time.sleep(60)
    threading.Thread(target=loop, daemon=True, name="athena-alarm-tasks").start()


@tool(tags=["schedule"])
def stop_alarm() -> str:
    """Stop the ringing alarm."""
    return stop()


@tool(tags=["schedule"])
def snooze_alarm(minutes: int = 10) -> str:
    """Snooze the ringing alarm.

    minutes: how long to snooze
    """
    return snooze(minutes)
