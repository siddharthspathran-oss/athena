"""Is anyone at the PC - and what Athena says about it.

  - Away and back: no keyboard or mouse for a while and (with the camera on)
    no face in front of the webcam means you've gone. Music she paused for
    you is resumed and you're caught up when you're back: the next class,
    what's overdue, the battery, who messaged.
  - Speaking up on her own goes through interject(): she waits for a gap
    (not while you're talking or she is) and says "sorry to interrupt" when
    you were mid-conversation.
  - Messages that would be read out to an empty room are held for the
    welcome-back instead.

The camera is only opened for a single frame while the keyboard is idle -
about one look every 30 seconds - and the frame never leaves this process or
touches the disk. presence.camera: false in config.yaml turns it off; idle
time alone then decides, and music is never paused on a guess.
"""
from __future__ import annotations

import datetime as dt
import re
import threading
import time

from .config import cfg

_say = None
_busy = lambda: False          # noqa: E731
_state = {"here": True, "away_since": 0.0, "misses": 0, "paused_music": False, "face_at": 0.0,
          "greeted": 0.0, "held": [], "last_look": 0.0, "talk": 0.0}
_lock = threading.Lock()


def set_speaker(say, busy) -> None:
    global _say, _busy
    _say, _busy = say, busy


def talked() -> None:
    """The voice loop calls this on every exchange - for 'sorry to interrupt'."""
    _state["talk"] = time.time()


def away() -> bool:
    return not _state["here"]


# ---------- speaking up ----------

def interject(text: str, wait: float = 30) -> None:
    """Say something nobody asked for, at a natural moment."""
    if _say is None:
        return
    from . import live
    mid = time.time() - _state["talk"] < 25 or _busy()
    end = time.time() + wait
    while time.time() < end and (_busy() or live._state.get("state") in ("listening", "thinking")):
        time.sleep(0.3)
    if mid:                                      # "DSD starts..." keeps its capitals
        text = "Sorry to interrupt - " + (text[0].lower() + text[1:] if text[1:2].islower() else text)
    _say(text)


def say_or_hold(line: str, say) -> None:
    """A message read out - or kept for when you're back if nobody's here."""
    if away():
        with _lock:
            _state["held"].append(line)
        return
    say(line)


# ---------- seeing ----------

_cascade = None


def face_seen() -> bool | None:
    """One frame from the webcam: True/False, or None if there's no camera
    (or another app such as Teams is using it)."""
    global _cascade
    try:
        import cv2
    except ImportError:
        return None
    cap = cv2.VideoCapture(int(cfg.get("presence.camera_index", 0)), cv2.CAP_DSHOW)
    try:
        if not cap.isOpened():
            return None
        frame = None
        for _ in range(5):                       # the first frames are dark while exposure settles
            ok, f = cap.read()
            if ok:
                frame = f
        if frame is None or float(frame.mean()) < 3:     # black: shutter closed / camera-off key
            return None
    finally:
        cap.release()
    if _cascade is None:
        _cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    gray = cv2.equalizeHist(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
    return len(_cascade.detectMultiScale(gray, 1.1, 5, minSize=(60, 60))) > 0


def _idle() -> float:
    from .tools.teams import _idle_seconds
    return _idle_seconds()


# ---------- coming back ----------

def _short(task: str) -> str:
    """'Voice in Action – Practice & Peer Feedback Forum (Leading ...)' -> 'Voice in Action'."""
    t = re.split(r"\s+[–—-]\s+|\s*\(", task)[0].strip()
    return t if len(t) <= 40 else " ".join(t.split()[:5])


def _and(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def welcome(minutes_away: float) -> str:
    now = dt.datetime.now()
    name = str(cfg.get("assistant.call_me", "") or "").strip()
    part = "morning" if now.hour < 12 else "afternoon" if now.hour < 17 else "evening"
    first_today = dt.date.fromtimestamp(_state["greeted"] or 0) != now.date()
    n = f", {name}" if name else ""
    if minutes_away >= 120 and 16 <= now.hour < 23:      # back after a day out
        hello = f"Welcome back{n}. How was your day?"
    elif first_today or minutes_away > 180:
        hello = f"Good {part}{n}."
    else:
        import random
        hello = random.choice([f"Welcome back{n}.", f"There you are{n}.", f"Hey{n}, welcome back."])
    items: list[str] = []
    try:                                         # the next class, from the cached timetable and Teams
        from . import upes
        from .tools import teams
        hhmm = now.strftime("%H:%M")
        nxt = [(c["start"], f"{c['course'].split(',')[0]} at {_spoken_time(c['start'])}"
                + (f" in {c['room']}" if c.get("room") else ""))
               for c in upes.classes(now.date(), cached_only=True) if not c["cancelled"] and c["start"] > hhmm]
        nxt += [(m["start"], f"{m['title']} on Teams at {_spoken_time(m['start'])}")
                for m in teams._meetings_today() if not m["canceled"] and m["start"] > hhmm]
        if nxt:
            items.append(min(nxt)[1])
    except Exception:
        pass
    try:
        from .tools import todo
        today = now.date().isoformat()
        late = [_short(t["text"]) for t in todo.load() if not t["done"] and t.get("due") and t["due"][:10] < today]
        due = [_short(t["text"]) for t in todo.load() if not t["done"] and (t.get("due") or "")[:10] == today]
        # Said once a day, not in every welcome - "Voice in Action is still
        # overdue" came with every greeting (11 Oct 2026).
        from .config import DATA
        from .store import load_json, save_json
        saved = load_json(DATA / "presence.json", {})
        nag = sorted(late[:2]) if late else sorted(due[:2])
        if nag and saved.get("nagged") != [today, nag]:
            items.append(f"{_and(late[:2])} {'is' if len(late) == 1 else 'are'} still overdue" if late
                         else f"{_and(due[:2])} {'is' if len(due) == 1 else 'are'} due today")
            save_json(DATA / "presence.json", {**saved, "nagged": [today, nag]})
    except Exception:
        pass
    try:
        import psutil
        b = psutil.sensors_battery()
        if b and not b.power_plugged and b.percent <= 30:
            items.append(f"you're on {int(b.percent)} percent")
    except Exception:
        pass
    with _lock:
        held, _state["held"] = _state["held"], []
    who = []
    for line in held:
        m = re.search(r"message from (.+?)\.?$", line)
        if m and m.group(1) not in who:
            who.append(m.group(1))
    if who:
        items.append(f"{_and(who[:3])} messaged you")
    if not items:
        return hello
    return hello + " " + _and(items)[0].upper() + _and(items)[1:] + "."


def _spoken_time(hhmm: str) -> str:
    h, m = map(int, hhmm.split(":"))
    return f"{(h - 1) % 12 + 1}" + (f":{m:02d}" if m else "")


_greet_lock = threading.Lock()


def greet(reason: str = "", minutes_away: float = 30) -> None:
    """Called when you're back (or the laptop opens). At most once in 20 minutes.

    11 Oct 2026, "it starts talking by itself": every restart of Athena's
    voice greeted you as if you had just arrived, and three triggers at once
    (wake, unlock, back) each passed the 20-minute check before any had
    written it down, so the same welcome was said three times in a row.
    """
    from .config import DATA
    from .store import load_json, save_json
    if not cfg.get("presence.greet", True):
        return
    if reason == "start":
        try:                                     # Athena restarting is not you arriving:
            import psutil                        # greet at start only just after Windows did
            if time.time() - psutil.boot_time() > 15 * 60:
                return
        except Exception:
            return
    try:
        from . import gaming
        if gaming.on():
            return
    except Exception:
        pass
    with _greet_lock:                            # claim the slot before the slow part
        saved = load_json(DATA / "presence.json", {})
        _state["greeted"] = max(_state["greeted"], saved.get("greeted", 0))
        if time.time() - _state["greeted"] < float(cfg.get("presence.greet_gap_minutes", 120)) * 60:
            return
        _state["greeted"] = time.time()
        save_json(DATA / "presence.json", {**saved, "greeted": _state["greeted"]})
    interject(welcome(minutes_away))


def _back(minutes: float) -> None:
    from . import live, winapi
    live.note(f"you're back (away {minutes:.0f} min)")
    if _state["paused_music"]:
        _state["paused_music"] = False
        try:
            winapi.media_key("playpause")
        except Exception:
            pass
    # Only when the camera saw you leave: idle time alone (watching from the
    # bed, a dark room) gave four welcome-backs a day while you never left
    # (12 Oct 2026, "it starts talking by itself").
    if _state.pop("saw_gone", False) and minutes >= float(cfg.get("presence.greet_after_minutes", 30)):
        greet("back", minutes)


def _gone(since: float, saw: bool) -> None:
    from . import live, winapi
    live.note("you've stepped away")
    _state["saw_gone"] = saw
    # Only on the camera's word: with idle time alone, a film you're watching
    # from the bed would be paused.
    # Music apps only (presence.music_apps): the play/pause key goes to
    # whatever is playing, and a video is never something to pause on a guess.
    if saw and cfg.get("presence.pause_music", True):
        try:
            from .tools.system import playing_apps
            apps = playing_apps()
            if apps and all(_music(a) for a in apps) and winapi.media_key("playpause"):
                _state["paused_music"] = True
        except Exception:
            pass


def _music(app: str) -> bool:
    return any(m in app.lower() for m in cfg.get("presence.music_apps", ["spotify"]))


def _watching() -> bool:
    """Something other than a music app is playing - a match, a film, a
    video in the browser. Watching means sitting still: FanCode in Opera was
    paused every four minutes as "away" and played again a few seconds
    later (4 Oct 2026)."""
    if time.time() - _state.get("watch_t", 0) < 60:          # a PowerShell call: once a minute is plenty
        return _state.get("watching", False)
    try:
        from .tools.system import playing_apps
        _state["watching"] = any(not _music(a) for a in playing_apps())
    except Exception:
        _state["watching"] = False
    _state["watch_t"] = time.time()
    return _state["watching"]


def _tick() -> None:
    """Away = not seen for a while; back = a real sign of you returning.

    "Seen" is the last key or mouse press OR the last time the camera found
    your face. Measuring "away" from the last key press alone made an hour of
    reading or watching count as an hour away, and the camera finding a face it
    had briefly lost counted as coming back - so "Welcome back" came out every
    few minutes while you never left (5 Oct 2026, "away 88 min ... 94 min").
    """
    now = time.time()
    idle = _idle()
    # Talking to Athena counts as being here: from the bed, out of the camera's
    # view, without touching the keyboard, you were marked away mid-conversation
    # and the next sentence got a "welcome back" (16 Oct 2026, twice).
    seen_at = max(now - idle, _state.get("face_at", 0.0), _state["talk"])
    camera = bool(cfg.get("presence.camera", True))
    away_after = float(cfg.get("presence.away_after_minutes", 3)) * 60
    if _state["here"]:
        if now - seen_at < away_after or _watching():
            _state["misses"] = 0
            return
        if camera and now - _state["last_look"] >= 30:
            _state["last_look"] = now
            seen = face_seen()
            if seen is None:                     # no camera / in use: idle time alone, and only when long
                if idle >= 3 * away_after:
                    _state.update(here=False, away_since=seen_at, misses=0)
                    _gone(seen_at, saw=False)
                return
            if seen:
                _state.update(face_at=now, misses=0)
                return
            _state["misses"] += 1
            if _state["misses"] >= 3:            # three looks, ~1.5 min: not a glance away
                _state.update(here=False, away_since=seen_at, misses=0)
                _gone(seen_at, saw=True)
        elif not camera and idle >= 3 * away_after:
            _state.update(here=False, away_since=seen_at)
            _gone(seen_at, saw=False)
        return
    # Back means you did something: a key or the mouse (talking to Athena,
    # unlocking and opening the lid are handled where they happen). The camera
    # finding a face again is only noted - it may never have lost you.
    if camera and now - _state["last_look"] >= 30 and idle >= 15:
        _state["last_look"] = now
        if face_seen() is True:
            _state["face_at"] = now
    if idle < 15 or now - _state["talk"] < 15:
        _state["here"] = True
        _back((now - _state["away_since"]) / 60)


def start() -> None:
    if not cfg.get("presence.enabled", True):
        return

    def loop():
        while True:
            try:
                _tick()
            except Exception:
                pass
            time.sleep(10)
    threading.Thread(target=loop, daemon=True, name="athena-presence").start()


if __name__ == "__main__":
    assert _short("Voice in Action – Practice & Peer Feedback Forum (Leading Conversations)") == "Voice in Action"
    assert _and(["a", "b", "c"]) == "a, b and c"
    assert _spoken_time("14:00") == "2" and _spoken_time("09:30") == "9:30"
    print(welcome(30))
    print("face:", face_seen())
