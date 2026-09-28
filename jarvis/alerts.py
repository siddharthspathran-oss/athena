"""Phoning the user when something actually matters.

A call wakes people up, so the interesting part is not placing one - that is
jarvis/phone.py - but deciding when not to. Every alert passes the same
rails, in this order:

  * alerts.enabled, the master switch.
  * De-duplication: each event carries a key ("class:2026-09-24:11:00:...",
    "mail:<gmail id>") and a key that has already been offered is dropped,
    across restarts - data/alerts_state.json remembers them for a month.
  * Quiet hours: outside the kinds listed in alerts.urgent, nothing rings
    at night. Skipped alerts are logged, not saved for the morning - a class
    reminder at 8am about a class that was at 11pm is noise.
  * A short settle window, then one call carrying everything waiting, so a
    reminder and an email arriving together ring once, not twice.
  * A minimum gap between calls (anything arriving inside it waits and is
    merged into the next call) and a daily cap.

Where alerts come from:

  * Watched here, in the voice process's thread: the timetable (a call
    shortly before each class), Gmail (new unread mail from the senders you
    name) and the battery.
  * Reported by other code through submit(): reminders firing, Ollama failing
    to come back from a restart, a long study answer finishing. Those can
    happen in the web or hotkey process too, so submit() drops a small file
    in data/alerts_inbox/ that the engine picks up - the same reason live.py
    uses a file: either side can restart without the other noticing, and one
    file per alert needs no lock.

The engine runs only in the voice process (the one autostart always launches
and the only writer of live.py's file), so every call placed or skipped shows
on the HUD's RT-LOG.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import threading
import time
import uuid
from typing import Callable

from .config import DATA, cfg
from .store import load_json, save_json

STATE_FILE = DATA / "alerts_state.json"
INBOX = DATA / "alerts_inbox"

KEEP_SEEN_DAYS = 30
SETTLE_SECONDS = 15        # wait this long for companions before ringing
STALE_MINUTES = 30         # an inbox alert older than this is history, not news
TICK_SECONDS = 20


# ---------- anyone, any process ----------

def submit(kind: str, key: str, message: str) -> None:
    """Ask for a call about something. Safe from any process; never raises.

    kind: "reminders", "classes", "email", "battery", "gpu", "study" -
        matched against alerts.urgent and alerts.<kind>.enabled.
    key: the same event must always give the same key; that is the dedupe.
    """
    try:
        INBOX.mkdir(parents=True, exist_ok=True)
        save_json(INBOX / f"{time.time():.3f}-{uuid.uuid4().hex[:6]}.json",
                  {"kind": kind, "key": key, "message": message, "ts": time.time()})
    except Exception:
        pass


def _kind_on(kind: str) -> bool:
    return bool(cfg.get("alerts.enabled", True)) and bool(
        cfg.get(f"alerts.{kind}.enabled", True))


# ---------- the rails ----------

def _hhmm(value: str, default: dt.time) -> dt.time:
    try:
        return dt.time.fromisoformat(str(value).strip())
    except ValueError:
        return default


def in_quiet_hours(now: dt.datetime, start: str = "23:00", end: str = "07:00") -> bool:
    """True inside [start, end), which may run across midnight."""
    s, e, t = _hhmm(start, dt.time(23)), _hhmm(end, dt.time(7)), now.time()
    if s == e:
        return False
    return (s <= t < e) if s < e else (t >= s or t < e)


def merge(messages: list[str]) -> str:
    """One call for several alerts, read in the order they arrived."""
    if len(messages) == 1:
        return messages[0]
    body = " ".join(f"{i}. {m.rstrip('.')}." for i, m in enumerate(messages, 1))
    return f"Athena here, {len(messages)} alerts. {body}"


class Engine:
    """The rails, separate from the thread so the tests can drive them.

    send: message -> (ok, words). note: where log lines go.
    """

    def __init__(self, send: Callable[[str], tuple[bool, str]],
                 note: Callable[[str], None] = print,
                 state_file=STATE_FILE, settings: dict | None = None):
        self.send = send
        self.note = note
        self.state_file = state_file
        s = settings or {}
        g = (lambda k, d: s.get(k, cfg.get(f"alerts.{k}", d)))
        self.enabled = bool(g("enabled", True))
        quiet = g("quiet_hours", ["23:00", "07:00"]) or ["00:00", "00:00"]
        self.quiet = (str(quiet[0]), str(quiet[1]))
        self.urgent = set(g("urgent", ["reminders"]) or [])
        self.cap = int(g("daily_cap", 10))
        self.gap = float(g("min_gap_minutes", 3)) * 60
        self.pending: list[dict] = []
        self._unreadable = False
        try:
            self.state = load_json(state_file, {})
        except Exception as e:
            # Unreadable state means we may have forgotten what was already
            # rung. store has already moved the damaged file aside, so a
            # fresh one loses nothing that can't still be recovered by hand.
            self.note(f"alerts: state unreadable ({e}); starting fresh")
            self.state = {}
            self._unreadable = True
        self.state.setdefault("seen", {})
        self.state.setdefault("calls", [])

    # --- bookkeeping ---

    def _refresh_calls(self) -> None:
        # call_now (a call the user asked for, possibly from the web process)
        # also records into this file, so fold its calls in rather than
        # erasing them from the daily cap on the next save.
        try:
            on_disk = load_json(self.state_file, {}).get("calls", [])
        except Exception:
            on_disk = []
        self.state["calls"] = sorted(set(self.state["calls"]) | set(on_disk))

    def _save(self) -> None:
        # A file that could not be read and is still in place (not a parse
        # failure, which store moves aside) is never saved over.
        if self._unreadable and self.state_file.exists():
            return
        self._refresh_calls()
        try:
            save_json(self.state_file, self.state)
        except Exception:
            pass

    def seen(self, key: str) -> bool:
        return key in self.state["seen"]

    def remember(self, key: str, now: float) -> None:
        self.state["seen"][key] = now
        cutoff = now - KEEP_SEEN_DAYS * 86400
        self.state["seen"] = {k: v for k, v in self.state["seen"].items()
                              if v >= cutoff}
        self._save()

    def calls_today(self, now: dt.datetime) -> int:
        day = now.date().isoformat()
        return sum(1 for ts in self.state["calls"]
                   if dt.datetime.fromtimestamp(ts).date().isoformat() == day)

    # --- the flow ---

    def offer(self, kind: str, key: str, message: str,
              now: dt.datetime | None = None) -> bool:
        """Queue an alert unless it is a repeat. True if queued."""
        now = now or dt.datetime.now()
        if not self.enabled:
            return False
        if self.seen(key):
            return False
        # Remembered at offer time, not at call time: an alert skipped for
        # quiet hours or the cap must not ring later when the rail lifts.
        self.remember(key, now.timestamp())
        self.pending.append({"kind": kind, "key": key, "message": message,
                             "at": now.timestamp()})
        return True

    def flush(self, now: dt.datetime | None = None) -> str | None:
        """Place one call for whatever is waiting, if the rails allow.

        Returns the merged message that was sent, or None.
        """
        now = now or dt.datetime.now()
        if not self.pending:
            return None
        t = now.timestamp()

        if in_quiet_hours(now, *self.quiet):
            keep = []
            for p in self.pending:
                if p["kind"] in self.urgent:
                    keep.append(p)
                else:
                    self.note(f"alert skipped (quiet hours): {p['message'][:60]}")
            self.pending = keep
            if not keep:
                return None

        if t - min(p["at"] for p in self.pending) < SETTLE_SECONDS:
            return None                      # let companions arrive
        self._refresh_calls()
        last = max(self.state["calls"], default=0)
        if t - last < self.gap:
            return None                      # waits, and merges with later ones

        if self.calls_today(now) >= self.cap:
            for p in self.pending:
                self.note(f"alert skipped (daily cap {self.cap}): {p['message'][:60]}")
            self.pending = []
            return None

        message = merge([p["message"] for p in self.pending])
        self.pending = []
        self.state["calls"] = [c for c in self.state["calls"]
                               if c > t - 2 * 86400] + [t]
        self._save()
        try:
            ok, words = self.send(message)
        except Exception as e:
            ok, words = False, f"ERROR: {e}"
        self.note(f"{'phoned' if ok else 'phone FAILED'}: {message[:60]} - {words[:80]}")
        return message


# ---------- watchers ----------

def class_alerts(events: list[dict], now: dt.datetime, lead_minutes: float) -> list[tuple[str, str]]:
    """(key, message) for every class starting within the lead time."""
    out = []
    lead = dt.timedelta(minutes=lead_minutes)
    for e in events:
        start = e.get("start")
        if not start or e.get("all_day"):
            continue
        if start - lead <= now < start:
            mins = max(1, round((start - now).total_seconds() / 60))
            where = f", room {e['where']}" if e.get("where") else ""
            key = f"class:{start.isoformat(timespec='minutes')}:{e['what']}"
            out.append((key, f"{e['what']} in {mins} minutes{where}."))
    return out


def _sender_name(header: str) -> str:
    name = header.split("<")[0].strip().strip('"')
    return name or header.strip("<> ")


def _sender_matches(header: str, wanted: list[str]) -> bool:
    low = header.lower()
    address = low.split("<")[-1].rstrip("> ").strip()
    for w in wanted:
        w = w.lower().strip().lstrip("@")
        if not w:
            continue
        if "@" in w:
            if address == w:
                return True
        elif address.endswith("@" + w) or address.endswith("." + w):
            return True
    return False


class _Watch:
    """The state the watchers keep between ticks, in the voice process."""

    def __init__(self, engine: Engine):
        self.engine = engine
        self.last_mail = 0.0
        self.battery_armed = True

    def classes(self, now: dt.datetime) -> None:
        if not _kind_on("classes"):
            return
        from .tools.brief import _timetable_events

        lead = float(cfg.get("alerts.classes.minutes_before", 10))
        for key, message in class_alerts(_timetable_events(now.date()), now, lead):
            self.engine.offer("classes", key, message, now)

    def mail(self, now: dt.datetime) -> None:
        if not _kind_on("email"):
            return
        every = float(cfg.get("alerts.email.poll_minutes", 5)) * 60
        if time.time() - self.last_mail < every:
            return
        self.last_mail = time.time()
        senders = list(cfg.get("alerts.email.senders", ["upes.ac.in"]) or [])
        if not senders:
            return

        from .tools import brief
        creds = brief._credentials()
        if not creds:
            return
        from googleapiclient.discovery import build

        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        who = " OR ".join(f"from:{s.lstrip('@')}" for s in senders)
        found = service.users().messages().list(
            userId="me", maxResults=20,
            q=f"is:unread newer_than:1d ({who})").execute().get("messages", [])

        # The first look ever just learns what is already there: mail that was
        # unread before alerts existed is not news.
        first = not self.engine.state.get("mail_seeded")
        for m in found:
            key = f"mail:{m['id']}"
            if self.engine.seen(key):
                continue
            if first:
                self.engine.remember(key, now.timestamp())
                continue
            msg = service.users().messages().get(
                userId="me", id=m["id"], format="metadata",
                metadataHeaders=["From"]).execute()
            headers = {h["name"]: h["value"]
                       for h in msg.get("payload", {}).get("headers", [])}
            sender = headers.get("From", "")
            if not _sender_matches(sender, senders):
                self.engine.remember(key, now.timestamp())
                continue
            # The sender only. The subject would go to the call and push
            # services, and a subject line can say more than it should.
            self.engine.offer("email", key,
                              f"Important email from {_sender_name(sender)[:40]}.", now)
        if first:
            self.engine.state["mail_seeded"] = True
            self.engine._save()

    def battery(self, now: dt.datetime) -> None:
        if not _kind_on("battery"):
            return
        import psutil

        b = psutil.sensors_battery()
        if b is None:
            return
        below = float(cfg.get("alerts.battery.below_percent", 15))
        if b.power_plugged or b.percent >= below + 5:
            self.battery_armed = True        # plugged in: the next drop is news
            return
        if b.percent < below and self.battery_armed:
            self.battery_armed = False
            self.engine.offer("battery", f"battery:{now.isoformat(timespec='minutes')}",
                              f"Laptop battery is at {int(b.percent)} percent "
                              f"and not charging.", now)

    def inbox(self, now: dt.datetime) -> None:
        if not INBOX.exists():
            return
        for path in sorted(INBOX.glob("*.json")):
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                item = None
            try:
                os.unlink(path)
            except OSError:
                continue                     # someone else has it
            if not item:
                continue
            kind = item.get("kind", "")
            if not _kind_on(kind):
                continue
            age = time.time() - float(item.get("ts") or 0)
            if age > STALE_MINUTES * 60:
                self.engine.note(f"alert dropped (stale): {item.get('message', '')[:60]}")
                continue
            self.engine.offer(kind, item.get("key", path.stem), item.get("message", ""), now)


_started = False


def start(note: Callable[[str], None] | None = None) -> None:
    """Start the watcher thread. Called once by the voice process."""
    global _started
    if _started:
        return
    _started = True
    from . import live, phone

    log = note or (lambda line: (print(f"[alerts] {line}"), live.note(line)))

    if not cfg.get("alerts.enabled", True):
        log("alerts off (alerts.enabled in config.yaml)")
        return

    def send(message: str) -> tuple[bool, str]:
        return phone.deliver(message)

    engine = Engine(send, log)
    watch = _Watch(engine)
    if not phone.configured():
        log("phone alerts not set up - run: python run.py alerts setup")

    def loop() -> None:
        while True:
            now = dt.datetime.now()
            for step in (watch.inbox, watch.classes, watch.mail, watch.battery):
                try:
                    step(now)
                except Exception as e:
                    print(f"[alerts] {step.__name__}: {e}")
            try:
                if engine.pending and not phone.configured():
                    # Nowhere to send it: say so on the HUD and let it go.
                    for p in engine.pending:
                        log(f"alert not sent (no phone set up): {p['message'][:60]}")
                    engine.pending = []
                else:
                    engine.flush(now)
            except Exception as e:
                print(f"[alerts] flush: {e}")
            time.sleep(TICK_SECONDS)

    threading.Thread(target=loop, daemon=True, name="jarvis-alerts").start()


# ---------- asked for directly ----------

def call_now(message: str) -> str:
    """Phone the user right now because they asked. Bypasses quiet hours and
    the gap - they asked - but still counts against the daily cap, so a model
    stuck calling the tool in a loop cannot ring the phone all night."""
    from . import phone

    if not phone.configured():
        return "Phone alerts are not set up yet. Run: python run.py alerts setup"
    try:
        state = load_json(STATE_FILE, {})
    except Exception:
        state = None                     # unreadable: call, but don't save over it
    now = time.time()
    today = dt.date.today().isoformat()
    calls = [c for c in (state or {}).get("calls", []) if c > now - 2 * 86400]
    cap = int(cfg.get("alerts.daily_cap", 10))
    if sum(1 for c in calls if dt.datetime.fromtimestamp(c).date().isoformat() == today) >= cap:
        return f"I've already phoned you {cap} times today, which is the daily cap in config.yaml."
    if state is not None:
        state["calls"] = calls + [now]
        try:
            save_json(STATE_FILE, state)
        except Exception:
            pass
    ok, words = phone.deliver(message, wait=8)
    return words if ok else f"ERROR: {words}"
