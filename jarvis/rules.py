"""Automation: "when this happens, do that" - rules Athena runs on her own.

A rule is a trigger and a list of commands, each anything you could say to
Athena ("connect to echo", "play my playlist"). Commands the router knows run
directly; anything else goes through the agent. Rules live in
data/rules.json; everything Athena does on her own goes to data/activity.jsonl
(shown on the HUD); "pause automation" stops all of it - rules, Teams
auto-join, habit offers, the inbox autopilot - until "resume automation".

Triggers (rule["when"]):
    {"type": "time", "at": "23:00"}              every day at a time
    {"type": "every", "minutes": 120}            a standing job
    {"type": "wifi", "ssid": "HomeWiFi"}         arriving on a network ("home")
    {"type": "bluetooth", "device": "echo"}      a device connecting
    {"type": "app", "name": "fortnite"}          an app starting
    {"type": "class", "title": "any"}            a Teams class starting
    {"type": "message", "from": "maa"}           a message from someone
    {"type": "battery", "below": 20}             battery falling below a level
"""
from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
import threading
import time
import uuid

from .config import DATA, cfg
from .registry import tool
from .store import load_json, save_json

RULES = DATA / "rules.json"
PAUSE = DATA / "automation_pause.json"
ACTIVITY = DATA / "activity.jsonl"
TAG = ["system"]


# ---------- pause and the activity log (used by every automation) ----------

def paused() -> bool:
    p = load_json(PAUSE, {})
    until = float(p.get("until") or 0)
    return bool(p.get("paused")) and (until == 0 or time.time() < until)


def log(what: str, kind: str = "rule") -> None:
    """One line of 'Athena did this on her own' - for the HUD and the morning report."""
    try:
        with open(ACTIVITY, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.time(), "kind": kind, "what": what[:300]}) + "\n")
    except Exception:
        pass
    try:
        from . import live
        live.note(f"auto: {what[:90]}")
    except Exception:
        pass


def recent_activity(n: int = 20) -> list[dict]:
    try:
        lines = ACTIVITY.read_text(encoding="utf-8").splitlines()[-n:]
        return [json.loads(l) for l in reversed(lines) if l.strip()]
    except Exception:
        return []


# ---------- reading the world ----------

def _wifi() -> str:
    try:
        out = subprocess.run(["netsh", "wlan", "show", "interfaces"], capture_output=True, text=True, timeout=5,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        m = re.search(r"^\s+SSID\s+:\s*(.+)$", out, re.M)
        return m.group(1).strip() if m else ""
    except Exception:
        return ""


def _bt_connected() -> set[str]:
    try:
        from .tools.windows import _paired
        return {name.lower() for name, _a, on, _c in _paired() if on}
    except Exception:
        return set()


def _apps() -> set[str]:
    import psutil
    return {(p.info["name"] or "").lower().removesuffix(".exe") for p in psutil.process_iter(["name"])}


def _battery() -> int | None:
    import psutil
    b = psutil.sensors_battery()
    return None if b is None or b.power_plugged else int(b.percent)


# ---------- the rules ----------

def load() -> list[dict]:
    return load_json(RULES, [])


def _save(rules: list[dict]) -> None:
    save_json(RULES, rules)


def _every(m: int) -> str:
    """60 -> 'every hour', 360 -> 'every 6 hours', 1440 -> 'every day', 30 -> 'every 30 minutes'."""
    if m and m % 1440 == 0:
        return "every day" if m == 1440 else f"every {m // 1440} days"
    if m and m % 60 == 0:
        return "every hour" if m == 60 else f"every {m // 60} hours"
    return f"every {m} minutes"


def set_enabled(rule_id: str, on: bool) -> str:
    """Switch one automation on or off without deleting it (the HUD's toggle)."""
    rules = load()
    for r in rules:
        if r["id"] == rule_id:
            r["enabled"] = bool(on)
            _save(rules)
            return f"{'On' if on else 'Off'}: {describe(r)}."
    return "That automation no longer exists."


_WATCH_NAMES = {"what's due on lms": "tell me about new or removed LMS deadlines",
                "what classes do i have tomorrow": "tell me when tomorrow's classes change",
                "my attendance": "tell me when my attendance changes",
                "what are my tasks": "tell me when my tasks change"}


_DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _days_text(days) -> str:
    if not days or len(days) == 7:
        return "every day"
    if sorted(days, key=_DAY_NAMES.index) == _DAY_NAMES[:5]:
        return "every weekday"
    if sorted(days, key=_DAY_NAMES.index) == _DAY_NAMES[5:]:
        return "every weekend"
    full = {"mon": "Monday", "tue": "Tuesday", "wed": "Wednesday", "thu": "Thursday", "fri": "Friday",
            "sat": "Saturday", "sun": "Sunday"}
    return "every " + " and ".join(full[d] for d in sorted(days, key=_DAY_NAMES.index))


def _weekdays(s: str) -> list[str]:
    """'every monday', 'on sundays', 'weekdays', 'mon and thu' -> ['mon', ...]; [] = every day."""
    if re.search(r"\bweekdays?\b", s):
        return _DAY_NAMES[:5]
    if re.search(r"\bweekends?\b", s):
        return _DAY_NAMES[5:]
    return [d for d in _DAY_NAMES if re.search(rf"\b{d}(?:day|nesday|sday|urday|rsday)?s?\b", s)]


def _is_mission(r: dict) -> bool:
    return any("mission" in str(c).lower() for c in r.get("do") or [])


def describe(r: dict) -> str:
    w = r["when"]
    t = w["type"]
    when = {"time": f"{_days_text(w.get('days'))} at {w.get('at')}", "every": _every(int(w.get("minutes") or 0)),
            "wifi": f"when Wi-Fi '{w.get('ssid')}' connects", "bluetooth": f"when {w.get('device')} connects",
            "app": f"when {w.get('name')} opens", "class": f"when {w.get('title')} class starts" if w.get('title') != "any"
            else "when a Teams class starts", "message": f"when {w.get('from')} messages you",
            "battery": f"when battery drops below {w.get('below')}%"}.get(t, t)
    def step(c: str) -> str:
        if c.startswith("watch:"):
            name, _, arg = c[6:].partition(" ")
            return {"stuck_orders": f"check for RAGAZZA orders stuck over {arg or 2} days",
                    "due_soon": "tell me what's due by tomorrow",
                    "attendance": f"warn me if attendance drops below {arg or 75}%",
                    "changes": _WATCH_NAMES.get(arg, f"tell me what's new in '{arg}'")}.get(name, c)
        if c.startswith("tool:"):
            from .habits import _label
            name, _, raw = c[5:].partition(" ")
            return _label(name, json.loads(raw or "{}"))
        return c
    return f"{when}: " + ", then ".join(step(c) for c in r["do"])


# ---------- watchers: standing jobs that speak only when there is news ----------

def _watch_stuck_orders(days: str = "2") -> str:
    from .tools import ragazza
    try:
        orders = ragazza._call("GET", "/orders?status=to-ship").get("orders", [])
    except Exception:
        return ""                                   # store offline: nothing to report
    cut = (dt.datetime.now() - dt.timedelta(days=float(days or 2))).isoformat()
    old = [o for o in orders if str(o.get("createdAt", "")) < cut]
    return (f"{len(old)} RAGAZZA order(s) waiting more than {days} days to ship: "
            + ", ".join(f"{o['order']} ({o.get('shipName', '')})" for o in old[:5]) + ".") if old else ""


def _watch_due_soon(_: str = "") -> str:
    from .tools import todo
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    due = [t for t in todo.load() if not t["done"] and t.get("due") and t["due"][:10] <= tomorrow]
    return ("Due by tomorrow: " + "; ".join(t["text"] for t in due[:6]) + ".") if due else ""


def _watch_attendance(limit: str = "75") -> str:
    try:
        from . import upes
        low = [c for c in upes.attendance() if c["percent"] < float(limit or 75)]
    except Exception:
        return ""
    return ("Attendance below " + str(limit) + "%: " + "; ".join(
        f"{c['course']} {c['percent']:.0f}%" for c in low) + ".") if low else ""


LAST = DATA / "watch_last.json"
_LAST_LOCK = threading.Lock()
# Words that change by themselves ("in 3 days" becomes "in 2 days"), not news.
_DRIFT = re.compile(r"\b(?:overdue|today|tomorrow|in \d+ days?|as of \d+ \w+ ago|\d+ (?:min|hour|day)s? ago)\b|[^\w ]", re.I)


def _items(result: str) -> dict[str, str]:
    """'On LMS: a (x) - in 3 days; b (y) - today.' -> {key: item}, keys without the drift."""
    body = result.split(": ", 1)[1] if re.match(r"^[\w ]{1,25}: ", result) else result
    out = {}
    for it in re.split(r";\s*|\n|\.\s+(?=[A-Z])", body):
        it = it.strip(" .")
        key = " ".join(_DRIFT.sub(" ", it).lower().split())
        if key:
            out[key] = it
    return out


def _watch_changes(command: str) -> str:
    """Anything Athena can check directly, compared with last time: news is
    what was added or has gone, not the same list said again (9 Oct 2026)."""
    from . import agent as agent_mod, registry
    direct = agent_mod.route(command)
    if not direct or not registry.get(direct[0]):
        return ""
    result = str(registry.get(direct[0]).run(**direct[1]))
    if re.search(r"\b(?:sign in|signed out|failed|error|can't|couldn't)\b", result, re.I):
        return ""                                   # a failed check is not a change
    day = dt.date.today().isoformat() if re.search(r"\b(?:today|tomorrow)\b", command, re.I) else ""
    with _LAST_LOCK:        # the three watches fire together; parallel saves collided (WinError 5, 11 Oct 2026)
        seen = load_json(LAST, {})
        before = seen.get(command)
        now = _items(result)
        seen[command] = {"items": now, "day": day, "t": time.time()}
        save_json(LAST, seen)
    if before is None or before.get("day") != day:  # first look, or "today" is a new day
        return ""
    new = [v for k, v in now.items() if k not in before["items"]]
    gone = [v for k, v in before["items"].items() if k not in now]
    if not new and not gone:
        return ""
    # The same thing with a new number or room ("DBMS 80%" -> "DBMS 78%") is a change, not new + gone.
    from difflib import SequenceMatcher
    changed = []
    for n in list(new):
        old = max(gone, key=lambda g: SequenceMatcher(None, g, n).ratio(), default=None)
        if old and SequenceMatcher(None, old, n).ratio() >= 0.6:
            changed.append(n)
            new.remove(n)
            gone.remove(old)
    return " ".join(p for p in (
        "Changed: " + "; ".join(changed[:5]) + "." if changed else "",
        "New: " + "; ".join(new[:5]) + "." if new else "",
        "No longer there: " + "; ".join(gone[:5]) + "." if gone else "") if p)


WATCHERS = {"stuck_orders": _watch_stuck_orders, "due_soon": _watch_due_soon, "attendance": _watch_attendance,
            "changes": _watch_changes}


def _watch(spec: str) -> str:
    name, _, arg = spec.partition(" ")
    fn = WATCHERS.get(name)
    news = fn(arg) if fn else f"no watcher {name}"
    if news:
        if _speak:
            _speak(news)
        try:
            from . import alerts
            alerts.submit("reminders", f"watch:{name}:{dt.date.today()}:{news[:40]}", news)
        except Exception:
            pass
    return news or "nothing new"


_speak = None


def set_speaker(fn) -> None:
    """The voice process hands over its speaker, so "say ..." steps are heard."""
    global _speak
    _speak = fn


def run_commands(commands: list[str], why: str) -> str:
    """Each command as if you had said it; the router first, the agent otherwise."""
    from . import agent as agent_mod, habits, registry
    habits.automated(True)              # what a rule does is not a habit of yours
    said = []
    for c in commands:
        if c.startswith("watch:"):      # a standing job: speaks only if there is news
            said.append(f"{c} -> {_watch(c[6:])[:120]}")
            continue
        if c.startswith("tool:"):       # a learned habit: the exact tool call
            name, _, raw = c[5:].partition(" ")
            t = registry.get(name)
            result = t.run(**json.loads(raw or "{}")) if t else f"no tool {name}"
            said.append(f"{name} -> {str(result)[:120]}")
            continue
        if c.lower().startswith(("say ", "tell me ")) and _speak:
            _speak(c.split(" ", 1)[1] if c.lower().startswith("say ") else c[8:])
            said.append(c)
            continue
        try:
            direct = agent_mod.route(c)
            if direct and registry.get(direct[0]):
                result = registry.get(direct[0]).run(**direct[1])
            else:
                result = agent_mod.Agent().ask(c)
        except Exception as e:
            result = f"failed: {e}"
        said.append(f"{c} -> {str(result)[:120]}")
    habits.automated(False)
    log(f"{why}: " + " | ".join(said))
    return "; ".join(said)


class Engine:
    """Polls every few seconds; a trigger fires on the change, not the state
    (arriving home runs the rule once, not every 15 seconds while at home)."""

    def __init__(self):
        self.prev = {"wifi": _wifi(), "bt": _bt_connected(), "apps": _apps(), "battery": _battery()}
        self.fired_today: dict[str, str] = {}
        # When each Wi-Fi was last seen. "Arriving" means it had been gone a while:
        # one empty netsh reading, or a few seconds' drop, made "when I get home,
        # play my playlist" open Spotify 6 times in 8 minutes (17 Oct 2026).
        self.wifi_seen: dict[str, float] = {self.prev["wifi"].lower(): time.time()} if self.prev["wifi"] else {}

    def tick(self) -> None:
        if paused():
            return
        rules = [r for r in load() if r.get("enabled", True)]
        now = dt.datetime.now()
        wifi = _wifi() if any(r["when"]["type"] == "wifi" for r in rules) else self.prev["wifi"]
        bt = _bt_connected() if any(r["when"]["type"] == "bluetooth" for r in rules) else self.prev["bt"]
        apps = _apps() if any(r["when"]["type"] == "app" for r in rules) else self.prev["apps"]
        batt = _battery() if any(r["when"]["type"] == "battery" for r in rules) else self.prev["battery"]
        changed = False
        for r in rules:
            w, fire = r["when"], False
            t = w["type"]
            if t == "time":
                key = r["id"] + now.date().isoformat()
                # A scheduled mission runs later that day if the PC was asleep at its time
                # (and only once - "last" survives restarts); other rules keep a 10-minute window.
                window = 24 * 60 if _is_mission(r) else 10
                end = (dt.datetime.strptime(w["at"], "%H:%M") + dt.timedelta(minutes=window)).strftime("%H:%M") \
                    if window < 24 * 60 else "24:00"
                ran_today = dt.date.fromtimestamp(float(r.get("last") or 0)) == now.date()
                fire = (now.strftime("%H:%M") >= w["at"] and self.fired_today.get(r["id"]) != key and not ran_today
                        and now.strftime("%H:%M") < end
                        and (not w.get("days") or _DAY_NAMES[now.weekday()] in w["days"]))
                if fire:
                    self.fired_today[r["id"]] = key
            elif t == "every":
                fire = time.time() - float(r.get("last") or 0) >= float(w["minutes"]) * 60
            elif t == "wifi":
                gone = time.time() - self.wifi_seen.get(w["ssid"].lower(), 0)
                fire = (wifi.lower() == w["ssid"].lower()
                        and gone >= float(cfg.get("rules.wifi_away_minutes", 10)) * 60
                        and time.time() - float(r.get("last") or 0) >= float(cfg.get("rules.arrive_cooldown_minutes", 60)) * 60)
            elif t == "bluetooth":
                want = w["device"].lower()
                fire = any(want in d for d in bt) and not any(want in d for d in self.prev["bt"])
            elif t == "app":
                want = w["name"].lower().replace(" ", "")
                fire = any(want in a for a in apps) and not any(want in a for a in self.prev["apps"])
            elif t == "battery":
                fire = batt is not None and batt < int(w["below"]) and (self.prev["battery"] is None
                                                                       or self.prev["battery"] >= int(w["below"]))
            elif t == "class":
                fire = _class_starting(w.get("title", "any"), r)
            if fire:
                r["last"] = time.time()
                changed = True
                threading.Thread(target=run_commands, args=(r["do"], describe(r)), daemon=True).start()
        self.prev = {"wifi": wifi, "bt": bt, "apps": apps, "battery": batt}
        if wifi:
            self.wifi_seen[wifi.lower()] = time.time()
        if changed:
            stored = load()
            for s in stored:
                for r in rules:
                    if s["id"] == r["id"]:
                        s["last"] = r.get("last")
            _save(stored)


def _class_starting(title: str, rule: dict) -> bool:
    try:
        from .tools.teams import _meetings_today
    except Exception:
        return False
    now = dt.datetime.now()
    for m in _meetings_today():
        if m["canceled"] or (title != "any" and title.lower() not in m["title"].lower()):
            continue
        start = dt.datetime.combine(now.date(), dt.time.fromisoformat(m["start"]))
        key = m["date"] + m["start"]
        if start <= now <= start + dt.timedelta(minutes=5) and rule.get("class_key") != key:
            rule["class_key"] = key
            return True
    return False


def on_message(sender: str) -> None:
    """Called by phone_notify for every personal message (sender only)."""
    if paused():
        return
    for r in load():
        w = r["when"]
        if r.get("enabled", True) and w["type"] == "message" and w["from"].lower() in sender.lower():
            threading.Thread(target=run_commands, args=(r["do"], describe(r)), daemon=True).start()


def start() -> None:
    def loop():
        eng = Engine()
        while True:
            time.sleep(float(cfg.get("rules.every_seconds", 15)))
            try:
                eng.tick()
            except Exception as e:
                log(f"rules engine error: {e}", "error")
    threading.Thread(target=loop, daemon=True, name="athena-rules").start()


# ---------- understanding a spoken rule ----------

_SPLIT_DO = re.compile(r"\s*(?:,\s*(?:and\s+|then\s+)?|\s+and\s+then\s+|\s+then\s+|\s+and\s+)", re.I)


def parse_when(text: str) -> dict | None:
    s = text.lower().strip(" ,.")
    m = re.search(r"\b(?:every\s+day\s+)?at\s+(\d{1,2})(?::(\d\d))?\s*(am|pm)?\b", s) or \
        re.search(r"\bevery\s+day\s+at\s+(\d{1,2})(?::(\d\d))?\s*(am|pm)?", s)
    if m and not re.search(r"\bevery\s+\d+\s+(?:min|hour)", s):
        h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
        if ap == "pm" and h < 12:
            h += 12
        if ap == "am" and h == 12:
            h = 0
        return {"type": "time", "at": f"{h:02d}:{mi:02d}", **({"days": _weekdays(s)} if _weekdays(s) else {})}
    if _weekdays(s) and re.search(r"\b(?:every|on|each)\b", s):
        return {"type": "time", "at": "07:00", "days": _weekdays(s)}
    if re.search(r"\bevery\s+(?:morning)\b", s):
        return {"type": "time", "at": "07:00"}
    if re.search(r"\bevery\s+(?:night|evening)\b", s):
        return {"type": "time", "at": "22:00"}
    m = re.search(r"\bevery\s+(\d+|an?|one)\s*(min(?:ute)?s?|hours?|hrs?|days?)\b", s) or \
        re.search(r"\b(hourly|daily)\b", s)
    if m:
        if m.group(1) in ("hourly", "daily"):
            return {"type": "every", "minutes": 60 if m.group(1) == "hourly" else 1440}
        n = 1 if m.group(1) in ("a", "an", "one") else int(m.group(1))
        unit = m.group(2)
        return {"type": "every", "minutes": n * (1440 if unit.startswith("day") else 60 if unit.startswith(("hour", "hr")) else 1)}
    if re.search(r"\b(?:get|come|arrive|reach)\s+(?:back\s+)?home\b|\bi'?m\s+home\b|\bat\s+home\b", s):
        home = cfg.get("rules.home_wifi") or load_json(DATA / "home_wifi.json", {}).get("ssid") or _wifi()
        if not home:
            return None
        save_json(DATA / "home_wifi.json", {"ssid": home})
        return {"type": "wifi", "ssid": home}
    m = re.search(r"\b(?:connect(?:ed|s)?\s+to|join)\s+(?:the\s+)?(?:wi-?fi\s+)?['\"]?([\w .-]+?)['\"]?(?:\s+wi-?fi)?$", s)
    if m and "wifi" in s.replace("-", ""):
        return {"type": "wifi", "ssid": m.group(1).strip()}
    m = re.search(r"\b(?:my\s+)?([\w +.-]+?)\s+(?:connects|is\s+connected|gets\s+connected)\b", s)
    if m:
        return {"type": "bluetooth", "device": re.sub(r"^(?:the|my)\s+", "", m.group(1)).strip()}
    m = re.search(r"\b(?:open|start|launch|run)s?\s+([\w .+-]+?)$|\b([\w .+-]+?)\s+(?:opens|starts|launches)\b", s)
    if m and "class" not in s:
        return {"type": "app", "name": (m.group(1) or m.group(2)).replace("i ", "", 1).strip()}
    m = re.search(r"\b(?:my\s+)?([\w &.-]+?)?\s*(?:class|lecture|meeting)\s+(?:starts|begins)\b", s)
    if m:
        t = (m.group(1) or "").strip()
        return {"type": "class", "title": "any" if t in ("", "a", "my", "any", "the", "next") else t}
    m = re.search(r"\b([\w .'-]+?)\s+(?:messages|texts|whatsapps|writes\s+to)\s+me\b|\bmessage\s+from\s+([\w .'-]+)", s)
    if m:
        return {"type": "message", "from": (m.group(1) or m.group(2)).replace("when ", "").strip()}
    m = re.search(r"\bbattery\b.*?\b(?:below|under|less\s+than)\s+(\d+)", s)
    if m:
        return {"type": "battery", "below": int(m.group(1))}
    return None


@tool(tags=TAG)
def add_rule(when: str, do: str) -> str:
    """Create an automation that runs on its own: "every time I get home, play
    my playlist", "every day at 11pm turn on focus mode", "when I open VS Code
    play lofi", "every 2 hours organise my downloads".

    when: the trigger in the user's words, e.g. "when I get home", "every day at 23:00", "when echo connects"
    do: what to do, in the user's words; several actions joined with "and"
    """
    w = parse_when(when)
    if w is None:
        return (f"I didn't understand when to do it ('{when}'). Try 'every day at 11pm', 'when I get home', "
                "'when echo connects', 'when I open Spotify', 'when my class starts' or 'every 2 hours'.")
    steps = [c.strip(" .") for c in _SPLIT_DO.split(do.strip()) if c.strip(" .")]
    if re.match(r"\s*(?:start|run|launch|begin|do)\s+an?\s+(?:big\s+|new\s+)?mission\b", do, re.I):
        steps = [do.strip(" .")]       # one mission: its "and"s are part of the goal, not separate actions
    if not steps:
        return "What should I do then?"
    rule = {"id": uuid.uuid4().hex[:6], "when": w, "do": steps, "enabled": True,
            "created": dt.datetime.now().isoformat(timespec="seconds")}
    rules = load()
    rules.append(rule)
    _save(rules)
    note = f" (I've taken '{w['ssid']}' as your home Wi-Fi.)" if w["type"] == "wifi" and "home" in when.lower() else ""
    return f"Done - {describe(rule)}.{note}"


@tool(tags=TAG)
def add_watch(what: str, every_hours: float = 6, value: str = "") -> str:
    """A standing job that checks something and speaks up only when there is
    news: stuck RAGAZZA orders, tasks due tomorrow, attendance below 75%.

    what: stuck_orders, due_soon or attendance
    every_hours: how often to check
    value: days for stuck_orders (default 2), percent for attendance (default 75)
    """
    if what not in WATCHERS:
        return "I can watch: stuck RAGAZZA orders, what's due tomorrow, or attendance."
    rule = {"id": uuid.uuid4().hex[:6], "when": {"type": "every", "minutes": int(every_hours * 60)},
            "do": [f"watch:{what} {value}".strip()], "enabled": True,
            "created": dt.datetime.now().isoformat(timespec="seconds")}
    rules = load()
    rules.append(rule)
    _save(rules)
    return f"Done - {describe(rule)}. I'll only say something when there's news."


# What "keep an eye on my LMS" means: a check the router answers directly.
_WATCH_ALIASES = [(r"\blms\b|assignment|deadline|quiz", "what's due on lms"),
                  (r"time\s*table|classes|schedule", "what classes do i have tomorrow"),
                  (r"attendance", "my attendance"),
                  (r"tasks|to-?do", "what are my tasks")]


@tool(tags=TAG)
def watch_for_changes(what: str, every_hours: float = 1) -> str:
    """A background job: check something on its own every so often and speak up
    only when something is new or gone - "keep an eye on my LMS", "tell me when
    my timetable changes", "watch my attendance every 6 hours".

    what: the thing to watch, in the user's words (LMS, timetable, attendance, tasks, or a command)
    every_hours: how often to check
    """
    from . import agent as agent_mod
    cmd = next((c for p, c in _WATCH_ALIASES if re.search(p, what, re.I)), what.strip())
    if not agent_mod.route(cmd):
        return (f"I can't check '{what}' on my own reliably. I can keep an eye on your LMS, timetable, "
                "attendance, tasks, or anything I answer directly, like your phone battery.")
    first = _watch_changes(cmd)                       # the starting point to compare with
    rule = {"id": uuid.uuid4().hex[:6], "when": {"type": "every", "minutes": max(10, int(every_hours * 60))},
            "do": [f"watch:changes {cmd}"], "enabled": True, "last": time.time(),
            "created": dt.datetime.now().isoformat(timespec="seconds")}
    rules = load()
    rules.append(rule)
    _save(rules)
    every = f"{every_hours:g} hour" + ("" if every_hours == 1 else "s")
    return f"Done - I'll check '{cmd}' every {every} and tell you only when something changes." + (f" {first}" if first else "")


@tool(tags=TAG)
def list_rules() -> str:
    """List the automations Athena runs on her own."""
    rules = load()
    if not rules:
        return "No automations yet. Try: 'every time I get home, play my playlist'."
    state = " (all paused)" if paused() else ""
    return f"Your automations{state}: " + "; ".join(
        f"{i}. {describe(r)}" + ("" if r.get("enabled", True) else " [off]") for i, r in enumerate(rules, 1))


@tool(tags=TAG, destructive=True)
def remove_rule(which: str) -> str:
    """Delete an automation, by its number from list_rules or words from it.

    which: the number, or words from the rule
    """
    rules = load()
    w = which.strip().lower()
    hit = rules[int(w) - 1] if w.isdigit() and 0 < int(w) <= len(rules) else \
        next((r for r in rules if w in describe(r).lower()), None)
    if hit is None:
        return "I couldn't find that automation."
    _save([r for r in rules if r["id"] != hit["id"]])
    return f"Removed: {describe(hit)}."


@tool(tags=TAG)
def pause_automation(minutes: int = 0) -> str:
    """Stop everything Athena does on her own - automations, Teams auto-join,
    habit suggestions, the inbox autopilot - until resumed.

    minutes: how long; 0 means until "resume automation"
    """
    save_json(PAUSE, {"paused": True, "until": time.time() + minutes * 60 if minutes else 0})
    log("all automation paused" + (f" for {minutes} minutes" if minutes else ""), "pause")
    return "All automation paused" + (f" for {minutes} minutes." if minutes else " until you say resume.")


@tool(tags=TAG)
def resume_automation() -> str:
    """Turn automations back on after pause_automation."""
    save_json(PAUSE, {"paused": False, "until": 0})
    log("automation resumed", "pause")
    return "Automation is back on."


@tool(tags=TAG)
def what_did_you_do() -> str:
    """What Athena did on her own recently (automations, auto-joins, fixes)."""
    acts = recent_activity(8)
    if not acts:
        return "I haven't done anything on my own yet."
    return "Recently, on my own: " + "; ".join(
        f"{time.strftime('%H:%M', time.localtime(a['t']))} {a['what'][:90]}" for a in acts)


if __name__ == "__main__":
    for text, want in [("every time I get home", "wifi"), ("every day at 11pm", "time"), ("at 7:30 am", "time"),
                       ("when echo connects", "bluetooth"), ("when I open vs code", "app"),
                       ("when my class starts", "class"), ("when maa messages me", "message"),
                       ("every 2 hours", "every"), ("when battery is below 20", "battery"), ("hourly", "every")]:
        got = parse_when(text)
        assert got and got["type"] == want, (text, got)
    assert parse_when("every day at 11pm")["at"] == "23:00"
    assert parse_when("every 2 hours")["minutes"] == 120
    assert [s for s in _SPLIT_DO.split("connect to echo and play my playlist, then say welcome home")] == \
        ["connect to echo", "play my playlist", "say welcome home"]
    print("ok")
