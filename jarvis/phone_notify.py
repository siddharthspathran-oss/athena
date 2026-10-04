"""Read out important phone messages that reach the PC through Phone Link.

Windows keeps every toast notification in its own database
(%LOCALAPPDATA%/Microsoft/Windows/Notifications/wpndatabase.db). Phone
Link's texts and WhatsApp's messages land there with the sender on the first
line and the message on the second. This polls a copy of that file, so
nothing needs Windows' notification-listener permission.

Only important ones are spoken: from a saved contact, or saying something
like "urgent" / "call me". Codes are never read out - a message that looks
like an OTP is announced as "a verification code from X". Quiet hours are
respected, and nothing is said over Athena herself.
"""
from __future__ import annotations

import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
from pathlib import Path

from .config import DATA, cfg
from .store import load_json, save_json

STATE = DATA / "notify_state.json"
_DB = Path(os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Windows\Notifications\wpndatabase.db"))
_APPS = ("Microsoft.YourPhone", "WhatsAppDesktop")
_URGENT = re.compile(r"\b(urgent|emergency|call me|call back|asap|important|pick up|where are you|help)\b", re.I)
_CODE = re.compile(r"\b(otp|one[- ]time|verification|passcode|code)\b|\b\d{4,8}\b", re.I)


def _texts(payload) -> list[str]:
    s = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else str(payload or "")
    out = []
    for attrs, body in re.findall(r"<text([^>]*)>(.*?)</text>", s, re.S):
        if "attribution" in attrs or "hint-align" in attrs:
            continue
        body = re.sub(r"<[^>]+>", "", body)
        body = (body.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
                .replace("&quot;", '"').replace("&apos;", "'").strip())
        if body:
            out.append(body)
    return out


def _new_since(last: int) -> list[tuple[int, str, list[str]]]:
    """(arrival, app, texts) for message toasts newer than `last`."""
    tmp = Path(tempfile.gettempdir()) / "athena_wpn"
    tmp.mkdir(exist_ok=True)
    for f in _DB.parent.glob("wpndatabase.db*"):
        try:
            shutil.copy(f, tmp / f.name)
        except OSError:
            pass
    con = sqlite3.connect(tmp / _DB.name)
    try:
        rows = con.execute(
            "select n.ArrivalTime, h.PrimaryId, n.Payload from Notification n "
            "join NotificationHandler h on n.HandlerId = h.RecordId "
            "where n.Type = 'toast' and n.ArrivalTime > ? order by n.ArrivalTime", (last,)).fetchall()
    finally:
        con.close()
    return [(t, pid, _texts(p)) for t, pid, p in rows if any(a in pid for a in _APPS)]


def _contacts() -> list[tuple[str, str]]:
    """(name, last 10 digits of their number) for each saved contact, plus
    any extra names set in config (phone_notify.important)."""
    out = []
    try:
        from .tools.messaging import _load
        for n, v in (_load() or {}).items():
            if n.lower() == "me":
                continue
            ph = v.get("phone", "") if isinstance(v, dict) else str(v)
            out.append((n, re.sub(r"\D", "", ph or "")[-10:]))
    except Exception:
        pass
    out += [(str(n), "") for n in (cfg.get("phone_notify.important") or [])]
    return out


_APP_LINE = re.compile(r"whats\W?app|instagram|telegram|messenger|signal|snapchat|messages", re.I)


def is_message(app: str, texts: list[str]) -> bool:
    """A text or a chat message - not an app's own notification. Phone
    Link forwards every phone app; "Samsung Wallet: Get Rs 50 off on
    Swiggy" was read out as a personal message (27 Sep 2026)."""
    if "YourPhoneMessages" in app or "WhatsAppDesktop" in app:
        return True                     # SMS, and WhatsApp on the PC
    return len(texts) >= 3 and bool(_APP_LINE.search(texts[0]))   # a messaging app on the phone


def split(texts: list[str]) -> tuple[str, str]:
    """(sender, message). Phone Link shows phone-app notifications as three
    lines - app name, chat, message ("WhatsApp Business" / "Maa" / "...") -
    and texts and WhatsApp Desktop as two (sender, message). Reading the app
    name as the sender kept every WhatsApp message quiet (26 Sep 2026)."""
    if len(texts) >= 3 and _APP_LINE.search(texts[0]):
        return texts[1], " ".join(texts[2:])
    return texts[0], " ".join(texts[1:])


MUTE = DATA / "notify_mute.json"


def muted() -> bool:
    """The HUD's switch: read-outs off until switched back on. Read on every
    message, so it works at once in the voice process without a restart."""
    return bool(load_json(MUTE, {}).get("muted"))


def set_muted(on: bool) -> dict:
    save_json(MUTE, {"muted": bool(on)})
    return {"muted": bool(on)}


def recent_senders(hours: float = 24, limit: int = 4) -> list[dict]:
    """Who messaged in the last `hours`, newest first, for the HUD - the
    sender and a count only, never what they wrote."""
    since = int((time.time() + 11644473600 - hours * 3600) * 10**7)   # Windows FILETIME
    seen: dict[str, dict] = {}
    for t, app, texts in _new_since(since):
        if not texts or not is_message(app, texts):
            continue
        who = split(texts)[0].strip()[:30]
        if not who or _CODE.search(who):
            continue
        kind = "WhatsApp" if "whatsapp" in (app + texts[0]).lower() else "SMS"
        s = seen.setdefault(who, {"who": who, "app": kind, "n": 0, "t": 0})
        s["n"] += 1
        s["t"] = max(s["t"], t)
    rows = sorted(seen.values(), key=lambda s: -s["t"])[:limit]
    return [{k: s[k] for k in ("who", "app", "n")} for s in rows]


def worth_saying(sender: str, message: str, contacts: list[tuple[str, str]]) -> str | None:
    """What to say for this message, or None to stay quiet. Only who it is
    from - never what it says (asked for 27 Sep 2026)."""
    who = sender.strip()
    digits = re.sub(r"\D", "", who)[-10:]
    known = next((n for n, ph in contacts
                  if (len(digits) >= 10 and ph == digits)
                  or re.search(rf"\b{re.escape(n.lower())}\b", who.lower())), None)
    if _CODE.search(message) and re.search(r"\d{4,8}", message):
        # Never read a code aloud, whoever sent it.
        return f"A verification code arrived from {known or who}." if known else None
    if known:
        return f"You have a message from {known}."
    if cfg.get("phone_notify.who", "all") == "all":
        # Every personal message (asked for 26 Sep 2026) - but not companies
        # ("AD-HDFCBK", short codes), not WhatsApp's "5 new messages"
        # summaries, and not group chats ("Name: text" inside a chat).
        company = re.match(r"^[A-Z]{2}-", who) or (digits and len(re.sub(r"\D", "", who)) < 8 and not re.search(r"[a-z]", who, re.I))
        summary = re.search(r"\b\d+\s+(?:new\s+)?messages?\b", message, re.I) and len(message) < 40
        group = re.match(r"^[^:\n]{1,30}:\s", message) or "@" in who
        if not (company or summary or group):
            name = f"a number ending {digits[-4:]}" if len(digits) >= 10 and not re.search(r"[a-z]", who, re.I) else who
            return f"You have a message from {name}."
    if _URGENT.search(message) and not re.match(r"^[A-Z]{2}-", who):   # "AD-HDFCBK" style senders are companies
        return f"You have a message from {who}."
    return None


def start(say, busy=lambda: False) -> None:
    """Poll every few seconds; `say` speaks, `busy` is True while Athena talks."""
    if not cfg.get("phone_notify.enabled", True) or not _DB.exists():
        return

    def loop():
        from . import live
        from .alerts import in_quiet_hours
        import datetime as dt
        state = load_json(STATE, {})
        last = int(state.get("last", 0))
        if not last:                    # first run: start from now, not the backlog
            try:
                rows = _new_since(0)
                last = max((r[0] for r in rows), default=0)
            except Exception:
                last = 0
            save_json(STATE, {"last": last})
        contacts, refreshed = _contacts(), time.time()
        while True:
            time.sleep(float(cfg.get("phone_notify.every_seconds", 8)))
            try:
                if time.time() - refreshed > 600:
                    contacts, refreshed = _contacts(), time.time()
                rows = _new_since(last)
            except Exception:
                continue
            for arrival, app, texts in rows:
                last = max(last, arrival)
                if len(texts) < 2 or not is_message(app, texts):
                    continue
                try:                        # automations: "when maa messages me ..."
                    from . import rules
                    rules.on_message(split(texts)[0])
                except Exception:
                    pass
                line = worth_saying(*split(texts), contacts)
                if not line:
                    continue
                live.note(f"📱 {line[:90]}")
                # Its own quiet hours: a message at 23:11 went unspoken under
                # the alerts' 23:00-07:00 while you were up and waiting for it.
                if in_quiet_hours(dt.datetime.now(), cfg.get("phone_notify.quiet_start", "01:00"),
                                  cfg.get("phone_notify.quiet_end", "07:00")) \
                        or not cfg.get("phone_notify.speak", True) or muted():
                    continue
                for _ in range(60):         # wait for Athena to finish talking
                    if not busy():
                        break
                    time.sleep(0.5)
                say(line)
            save_json(STATE, {"last": last})
    threading.Thread(target=loop, daemon=True, name="athena-phone-notify").start()
