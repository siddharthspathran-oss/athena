"""What today actually looks like: classes, meetings, and what needs a reply.

The difference between an assistant that opens apps and one worth having is
that the second one tells you something you did not already know. "Four items,
sir, and a quieter morning - the call at half one collides with the team sync,
and you have not yet replied to it" is not a harder trick than opening Spotify;
it just requires knowing what is in the calendar.

Three sources, deliberately different in how much they are trusted:

  * Google Calendar and Gmail, read through the official API. You sign in
    yourself in your own browser; the token lands in data/google_token.json
    and nothing here ever handles a password.
  * Any iCalendar feed, for a timetable that is not in Google - a university
    LMS publishes one, and subscribing to it needs a URL and no credentials
    at all. That URL is private, so treat it like a password: it lives in
    data/feeds.json, not in config.yaml.
  * Reminders already in Athena.

Everything is read-only. Nothing here sends, deletes, accepts or replies.
"""
from __future__ import annotations

import datetime as dt
import urllib.request

from ..config import DATA, cfg
from ..registry import tool
from ..store import load_json, save_json

TAG = ["brief"]

CREDENTIALS_FILE = DATA / "google_credentials.json"
TOKEN_FILE = DATA / "google_token.json"
FEEDS_FILE = DATA / "feeds.json"
TIMETABLE_FILE = DATA / "timetable.json"

_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_DAY_NAMES = {"mon": "Monday", "tue": "Tuesday", "wed": "Wednesday",
              "thu": "Thursday", "fri": "Friday", "sat": "Saturday",
              "sun": "Sunday"}

# Read-only throughout. Athena can see the calendar and the inbox; it cannot
# alter either, and asking for less access is the difference between a token
# that leaks a summary and one that can empty an inbox.
SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
]

SETUP_HELP = f"""Google is not connected yet. This is a one-time setup, and
you do the signing in - Athena never sees your password.

1. Go to https://console.cloud.google.com/projectcreate and make a project
   (any name; it is free).
2. Under "APIs & Services" > "Enabled APIs", enable **Google Calendar API**
   and **Gmail API**.
3. Under "APIs & Services" > "OAuth consent screen", choose External, fill in
   the app name and your own email, and add yourself under "Test users".
4. Under "Credentials", create an **OAuth client ID** of type **Desktop app**,
   then download the JSON.
5. Save that file as:
   {CREDENTIALS_FILE}

Then say "connect my google account" and a browser will open for you to
sign in. Athena asks only for read-only calendar and mail."""


# ---------- google ----------

def _credentials():
    """A live Google credential, or None with the reason left to the caller."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = None
    if TOKEN_FILE.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        except Exception:
            creds = None
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
            return creds
        except Exception:
            return None
    return None


@tool(tags=TAG, timeout=0)
def connect_google() -> str:
    """Sign in to Google so Athena can read your calendar and mail.

    Opens your browser so you can sign in yourself. Athena never sees your
    password and asks only for read-only access.
    """
    if not CREDENTIALS_FILE.exists():
        return SETUP_HELP
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        return ("ERROR: run: pip install google-auth-oauthlib "
                "google-api-python-client")

    if _credentials():
        return "Google is already connected. Ask me what your day looks like."
    try:
        flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
        creds = flow.run_local_server(port=0, prompt="consent",
                                      authorization_prompt_message="")
    except Exception as e:
        return f"ERROR: sign-in did not complete ({e})."
    TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    return "Google is connected. Ask me what your day looks like."


def _google_events(day: dt.date) -> list[dict]:
    creds = _credentials()
    if not creds:
        return []
    from googleapiclient.discovery import build

    service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    start = dt.datetime.combine(day, dt.time.min).astimezone()
    end = dt.datetime.combine(day, dt.time.max).astimezone()
    try:
        found = service.events().list(
            calendarId="primary", timeMin=start.isoformat(),
            timeMax=end.isoformat(), singleEvents=True,
            orderBy="startTime", maxResults=50).execute().get("items", [])
    except Exception:
        return []

    events = []
    for e in found:
        starts = e.get("start", {})
        when = starts.get("dateTime") or starts.get("date")
        ends = e.get("end", {})
        until = ends.get("dateTime") or ends.get("date")
        events.append({
            "what": e.get("summary") or "(no title)",
            "start": _parse_time(when),
            "end": _parse_time(until),
            "all_day": "date" in starts,
            "where": (e.get("location") or "").split(",")[0][:40],
            "who": [a.get("email", "").split("@")[0]
                    for a in e.get("attendees", [])[:4]
                    if not a.get("self")],
            "source": "calendar",
        })
    return events


def _unanswered_mail(limit: int = 5) -> list[str]:
    """Subjects of recent unread mail addressed to you.

    Not every unread message - only ones sent directly to you, which is a
    reasonable stand-in for "needs a reply" without pretending to judge
    importance.
    """
    creds = _credentials()
    if not creds:
        return []
    from googleapiclient.discovery import build

    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    try:
        ids = service.users().messages().list(
            userId="me", maxResults=limit,
            q="is:unread to:me -category:promotions -category:social",
        ).execute().get("messages", [])
    except Exception:
        return []

    # One batched request for every message's headers, instead of one round
    # trip each - five unread mails were ~2.5 s of the brief (28 Sep 2026).
    got: dict[str, dict] = {}

    def keep(request_id, response, exception):
        if exception is None:
            got[request_id] = response

    batch = service.new_batch_http_request(callback=keep)
    for m in ids:
        batch.add(service.users().messages().get(
            userId="me", id=m["id"], format="metadata",
            metadataHeaders=["Subject", "From"]), request_id=m["id"])
    try:
        batch.execute()
    except Exception:
        return []

    out = []
    for m in ids:
        msg = got.get(m["id"])
        if not msg:
            continue
        headers = {h["name"]: h["value"]
                   for h in msg.get("payload", {}).get("headers", [])}
        who = headers.get("From", "")
        who = who.split("<")[0].strip().strip('"') or who
        out.append(f"{headers.get('Subject', '(no subject)')[:60]} - from {who[:30]}")
    return out


# ---------- icalendar feeds (a timetable that is not in Google) ----------

@tool(tags=TAG, timeout=90)
def add_calendar_feed(url: str, name: str = "") -> str:
    """Subscribe to a timetable published as an iCalendar (.ics) feed.

    Use this for a university LMS timetable or any calendar that is not in
    Google. Find the "subscribe"/"export"/"iCal feed" link in that site and
    give the URL here. It needs no password.

    url: the webcal:// or https:// .ics address
    name: what to call it, e.g. "UPES"
    """
    url = url.strip().replace("webcal://", "https://")
    if not url.lower().startswith("https://"):
        return "ERROR: give the https:// address of the .ics feed."
    try:
        events = _feed_events(url, dt.date.today())
    except Exception as e:
        return f"ERROR: could not read that feed ({e})."

    feeds = load_json(FEEDS_FILE, [])
    label = name.strip() or "timetable"
    feeds = [f for f in feeds if f.get("url") != url]
    feeds.append({"name": label, "url": url})
    save_json(FEEDS_FILE, feeds)
    return (f"Subscribed to {label}. It has {len(events)} event(s) today. "
            f"The address is private, so it is kept in data/feeds.json.")


@tool(tags=TAG)
def list_calendar_feeds() -> str:
    """Show which timetable feeds Athena is subscribed to."""
    feeds = load_json(FEEDS_FILE, [])
    if not feeds:
        return "No timetable feeds yet. Use add_calendar_feed with an .ics address."
    return "\n".join(f"  {f['name']}" for f in feeds)


def _feed_events(url: str, day: dt.date) -> list[dict]:
    from icalendar import Calendar

    request = urllib.request.Request(url, headers={"User-Agent": "Athena/1.0"})
    with urllib.request.urlopen(request, timeout=20) as response:
        raw = response.read()

    events = []
    for item in Calendar.from_ical(raw).walk("VEVENT"):
        start = item.get("DTSTART")
        if start is None:
            continue
        value = start.dt
        when = value if isinstance(value, dt.datetime) else dt.datetime.combine(
            value, dt.time.min)
        if when.date() != day:
            continue
        end = item.get("DTEND")
        until = end.dt if end is not None else None
        events.append({
            "what": str(item.get("SUMMARY") or "(no title)"),
            "start": when,
            "end": until if isinstance(until, dt.datetime) else None,
            "all_day": not isinstance(value, dt.datetime),
            "where": str(item.get("LOCATION") or "")[:40],
            "who": [],
            "source": "timetable",
        })
    return events


# ---------- a fixed weekly timetable ----------
#
# A university timetable repeats every week and changes once a semester, so
# storing it beats fetching it. UPES publishes it only through a portal that
# needs an OAuth token expiring hourly, and whose printed export omits the
# course names entirely - a login Athena would have to hold, to re-fetch
# something that does not change. This needs no credentials and cannot break.


@tool(tags=TAG)
def show_timetable() -> str:
    """Show the weekly class timetable Athena knows about."""
    table = load_json(TIMETABLE_FILE, [])
    if not table:
        return ("No timetable saved. Tell me your classes and I will store "
                "them, or use set_timetable.")
    lines = []
    for day in _DAYS:
        today = sorted([c for c in table if c["day"] == day],
                       key=lambda c: c["start"])
        if not today:
            continue
        lines.append(f"{_DAY_NAMES[day]}:")
        for c in today:
            where = f" ({c['where']})" if c.get("where") else ""
            lines.append(f"  {c['start']}-{c['end']}  {c['what']}{where}")
    return "\n".join(lines)


@tool(tags=TAG)
def set_timetable(entries: str) -> str:
    """Replace the weekly class timetable.

    entries: one class per line, as "day start-end subject @ room", e.g.
        mon 11:00-11:55 Analog Electronics II @ 1003
        tue 13:00-13:55 Marketing Fundamentals @ MS Teams
    """
    table = []
    bad = []
    for line in entries.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 2)
        if len(parts) < 3 or parts[0][:3].lower() not in _DAYS:
            bad.append(line)
            continue
        day = parts[0][:3].lower()
        times = parts[1].replace("–", "-").split("-")
        if len(times) != 2:
            bad.append(line)
            continue
        what, _, where = parts[2].partition("@")
        table.append({"day": day, "start": times[0].strip(),
                      "end": times[1].strip(), "what": what.strip(),
                      "where": where.strip()})
    if not table:
        return "ERROR: nothing recognised. Use: mon 11:00-11:55 Subject @ room"
    save_json(TIMETABLE_FILE, table)
    note = f" ({len(bad)} line(s) not understood)" if bad else ""
    return f"Saved {len(table)} classes across the week.{note}"


def _timetable_events(day: dt.date) -> list[dict]:
    # The UPES semester timetable when Athena has read it: it knows about
    # mid-sems, holidays and cancelled classes, which a fixed weekly table
    # shows as ordinary class days (mid-sems 5-10 Oct 2026). The weekly
    # table is only for days the semester timetable does not cover.
    try:
        from .. import upes
        if upes.covers(day):
            return [{"what": c["course"], "where": c["room"], "who": [], "all_day": False, "source": "timetable",
                     "start": dt.datetime.combine(day, dt.time.fromisoformat(c["start"])),
                     "end": dt.datetime.combine(day, dt.time.fromisoformat(c["end"]))}
                    for c in upes.classes(day, cached_only=True) if not c["cancelled"]]
    except Exception:
        pass
    table = load_json(TIMETABLE_FILE, [])
    if not table:
        return []
    name = _DAYS[day.weekday()]
    events = []
    for c in table:
        if c["day"] != name:
            continue
        try:
            start = dt.datetime.combine(
                day, dt.time.fromisoformat(c["start"]))
            end = dt.datetime.combine(day, dt.time.fromisoformat(c["end"]))
        except ValueError:
            continue
        events.append({"what": c["what"], "start": start, "end": end,
                       "all_day": False, "where": c.get("where", ""),
                       "who": [], "source": "timetable"})
    return events


# ---------- putting it together ----------

def _parse_time(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        if len(value) == 10:                 # an all-day date
            return dt.datetime.fromisoformat(value + "T00:00:00")
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _clashes(events: list[dict]) -> list[tuple[dict, dict]]:
    """Pairs of events that overlap in time."""
    timed = sorted([e for e in events if e["start"] and e["end"] and not e["all_day"]],
                   key=lambda e: e["start"])
    found = []
    for i, a in enumerate(timed):
        for b in timed[i + 1:]:
            if b["start"] >= a["end"]:
                break
            found.append((a, b))
    return found


def _clock(when: dt.datetime | None) -> str:
    if not when:
        return ""
    return when.strftime("%H:%M")


def _events(day: dt.date) -> list[dict]:
    events = _google_events(day) + _timetable_events(day)
    for feed in load_json(FEEDS_FILE, []):
        try:
            events += _feed_events(feed["url"], day)
        except Exception:
            continue                          # one dead feed is not the brief
    events.sort(key=lambda e: (e["all_day"] is False, e["start"] or dt.datetime.max))
    return events


def collect(day: dt.date) -> tuple[list[dict], list[str]]:
    """Everything on for a day, and what is waiting for a reply - the calendar
    and the inbox read side by side rather than one after the other."""
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(2) as pool:
        mail = pool.submit(_unanswered_mail)
        return _events(day), mail.result()


def _local(t: dt.datetime) -> dt.datetime:
    """Calendar times can be timezone-aware; the timetable's are not."""
    return t.astimezone().replace(tzinfo=None) if t.tzinfo else t


def _first_tomorrow() -> dict | None:
    # Events only: this used to read the whole inbox a second time.
    try:
        events = _events(dt.date.today() + dt.timedelta(days=1))
    except Exception:
        return None
    timed = [e for e in events if not e["all_day"]]
    return min(timed, key=lambda e: _local(e["start"])) if timed else None


@tool(tags=TAG, timeout=90)
def daily_brief(day: str = "today", full: bool = False) -> str:
    """Say what the day looks like: classes, meetings, clashes and unread mail.

    Reads your calendar, any subscribed timetable, and your unread mail, and
    reports them together. Read-only - it never replies to anything. For
    today it covers what is still ahead, unless the whole day is asked for.

    day: "today" or "tomorrow"
    full: true to include today's classes that are already over
    """
    when = dt.date.today()
    if day.strip().lower().startswith("tom"):
        when += dt.timedelta(days=1)

    if not (TOKEN_FILE.exists() or load_json(FEEDS_FILE, [])
            or load_json(TIMETABLE_FILE, [])):
        return SETUP_HELP

    try:
        events, mail = collect(when)
    except Exception as e:
        return f"ERROR: could not read the calendar ({e})."

    label = "today" if when == dt.date.today() else "tomorrow"
    if not events and not mail:
        return f"Nothing in the calendar for {label}, and no unread mail."

    # Asked at 13:33, "what's my schedule" read out the 8, 9 and 10 o'clock
    # classes that were long over (25 Sep 2026). For today, only what is
    # still ahead - or under way - is news.
    now = dt.datetime.now()
    over = 0
    if label == "today" and not full:
        ahead = [e for e in events
                 if e["all_day"] or _local(e.get("end") or e["start"]) > now]
        over = len(events) - len(ahead)
        events = ahead

    lines: list[str] = []
    count = len(events)
    if label == "today" and not full:
        if count:
            lines.append(f"{count} more {'thing' if count == 1 else 'things'} today"
                         + (f" ({over} already done)." if over else "."))
        elif over:
            lines.append(f"You're done for today - all {over} classes are over.")
            nxt = _first_tomorrow()
            if nxt:
                lines.append(f"Tomorrow starts at {_clock(nxt['start'])} with {nxt['what']}.")
        else:
            lines.append("Nothing scheduled today.")
    else:
        lines.append(f"{count} item{'s' if count != 1 else ''} {label}."
                     if count else f"Nothing scheduled {label}.")

    for e in events:
        start = _local(e["start"])
        if e["all_day"]:
            at = "all day"
        elif (label == "today" and start <= now
              and (not e.get("end") or _local(e["end"]) > now)):
            at = "now" + (f", until {_clock(_local(e['end']))}" if e.get("end") else "")
        else:
            at = _clock(e["start"])
        bits = [f"  {at} - {e['what']}"]
        if e["where"]:
            bits.append(f"({e['where']})")
        if e["who"]:
            bits.append("with " + ", ".join(e["who"]))
        lines.append(" ".join(bits))

    for a, b in _clashes(events):
        lines.append(f"  Clash: {a['what']} at {_clock(a['start'])} runs into "
                     f"{b['what']} at {_clock(b['start'])}.")

    if mail:
        lines.append(f"{len(mail)} unread, addressed to you:")
        lines += [f"  {m}" for m in mail]

    if label == "today":                # goals in progress (goals.py): progress and today's tasks
        try:
            from .. import goals
            lines += goals.brief_lines()
        except Exception:
            pass
        try:                            # spaced review of missed quiz questions (quiz.py)
            from .. import quiz
            if quiz.brief_line():
                lines.append(quiz.brief_line())
        except Exception:
            pass

    if label == "today":                # last night's self-check, if it found anything
        try:
            from .. import health
            note = health.morning_line()
            if note:
                lines.append(note)
        except Exception:
            pass

    return "\n".join(lines)


# ---------- the evening summary (9 Oct 2026) ----------

def _evening() -> str:
    """What got done today, what is still open, and tomorrow's start."""
    from .. import upes
    from ..store import load_json
    from . import todo
    today, tomorrow = dt.date.today(), dt.date.today() + dt.timedelta(days=1)
    tasks = todo.load()
    done = [t["text"] for t in tasks if t["done"] and (t.get("finished") or "")[:10] == today.isoformat()]
    late = [t["text"] for t in tasks if not t["done"] and t.get("due") and t["due"][:10] <= today.isoformat()]
    parts = [f"Today you finished {len(done)} task{'s' if len(done) != 1 else ''}"
             + (f": {', '.join(done[:3])}." if done else ".")]
    if late:
        parts.append(f"Still open: {', '.join(late[:3])}" + (f" and {len(late) - 3} more." if len(late) > 3 else "."))
    lms = [e["name"] for e in load_json(upes.LMS_CACHE, {}).get("events", [])
           if not e["done"] and dt.date.fromtimestamp(e["due"]) == tomorrow]
    if lms:
        parts.append(f"Due tomorrow on LMS: {', '.join(lms[:3])}.")
    try:
        cs = [c for c in upes.classes(tomorrow, cached_only=True) if not c["cancelled"]]
    except Exception:
        cs = []
    if cs:
        c = cs[0]
        parts.append(f"Tomorrow starts with {c['course'].split(',')[0]} at {c['start']}"
                     + (f" in {c['room']}." if c.get("room") else "."))
    else:
        first = _first_tomorrow()
        parts.append(f"Tomorrow your first thing is {first['title']} at {_clock(_local(first['start']))}."
                     if first else "No classes tomorrow.")
    return " ".join(parts)


@tool(tags=TAG, timeout=60)
def evening_summary() -> str:
    """The day wrapped up: tasks finished today, what is still open, what is
    due tomorrow, and tomorrow's first class. Use for "evening summary",
    "how did today go", "wrap up my day"."""
    return _evening()


def start_evening(speak) -> None:
    """Said once each evening at brief.evening_at, only if you're at the PC
    (presence) and automation isn't paused; brief.evening: false turns it off."""
    import threading
    import time

    def loop():
        from .. import presence, rules
        said = load_json(DATA / "evening.json", {}).get("said", "")   # a restart must not repeat it
        while True:
            time.sleep(60)
            try:
                now = dt.datetime.now()
                at = str(cfg.get("brief.evening_at", "21:30"))
                late = (dt.datetime.strptime(at, "%H:%M") + dt.timedelta(hours=2)).strftime("%H:%M")
                if (not cfg.get("brief.evening", True) or said == now.date().isoformat()
                        or not at <= now.strftime("%H:%M") < late or presence.away() or rules.paused()):
                    continue
                said = now.date().isoformat()
                save_json(DATA / "evening.json", {"said": said})
                summary = _evening()
                speak(summary)
                from .. import vault
                vault.append_daily("Evening summary", summary)   # kept in Obsidian's daily note
            except Exception as e:
                print(f"[evening summary: {e}]")
    threading.Thread(target=loop, daemon=True, name="athena-evening").start()
