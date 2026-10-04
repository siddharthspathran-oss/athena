"""Live data from the UPES portal (MyUPES) and LMS, read from this PC.

UPES Tracker (tracker.buildpod.tech) gets attendance by signing in to MyUPES
on its own server with your password. This does the same reading without
anyone holding the password: Athena keeps her own browser profile
(data/upes_profile), you sign in there once yourself, and she reuses that
session to read the portal - the same connectportal API the portal's own pages
call.

    python -m jarvis.upes login     # opens the window; sign in yourself
    python -m jarvis.upes discover  # (after login) list the API calls the portal makes

Nothing here ever types, stores or reads a password.
"""
from __future__ import annotations

import json
import re
import sys
import time

from .config import DATA

PROFILE = DATA / "upes_profile"
SEEN = DATA / "upes_api_seen.json"
PORTAL = "https://myupes-beta.upes.ac.in/"


def _context(p, headless: bool):
    PROFILE.mkdir(parents=True, exist_ok=True)
    return p.chromium.launch_persistent_context(str(PROFILE), channel="chrome", headless=headless,
                                                viewport={"width": 1280, "height": 860})


def login(minutes: int = 15) -> None:
    """Open the portal in Athena's own window for you to sign in. While it is
    open, every portal API address used is noted (addresses only - no bodies,
    no headers) so the reader knows where attendance and timetable live."""
    from playwright.sync_api import sync_playwright
    seen: dict[str, int] = json.loads(SEEN.read_text()) if SEEN.exists() else {}

    def note(req):
        u = req.url.split("?")[0]
        if ("connectportal" in u or "lms.upes" in u) and req.resource_type in ("xhr", "fetch"):
            seen[f"{req.method} {u}"] = seen.get(f"{req.method} {u}", 0) + 1

    with sync_playwright() as p:
        ctx = _context(p, headless=False)
        ctx.on("request", note)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(PORTAL)
        print("Sign in to MyUPES in the window that opened, then click around: attendance, timetable.")
        end = time.time() + minutes * 60
        while time.time() < end and ctx.pages:
            time.sleep(2)
            SEEN.write_text(json.dumps(seen, indent=1))
        try:
            ctx.close()
        except Exception:
            pass
    print(f"Noted {len(seen)} portal API addresses -> {SEEN}")


CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 9333


def _pages() -> list[dict]:
    """Open tabs, from Chrome's own debug listing - reading it does not attach to anything."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json", timeout=2) as r:
            return [p for p in json.loads(r.read()) if p.get("type") == "page"]
    except Exception:
        return []


SESSION = DATA / "upes_session.json"


def _browser(p):
    """(context, live): the open sign-in window if there is one, else a
    headless Chrome on the same profile. The portal's sign-in lives in session
    cookies, which Chrome drops when its window closes (that is what "signed
    out after 20 minutes" was, 3 Oct 2026) - so Athena keeps her own copy in
    data/ and puts back whichever ones the browser lost."""
    live = bool(_pages())
    ctx = (p.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}").contexts[0] if live
           else p.chromium.launch_persistent_context(str(PROFILE), channel="chrome", headless=True))
    from .store import load_json
    have = {(c["name"], c["domain"]) for c in ctx.cookies()}
    lost = [c for c in load_json(SESSION, []) if (c["name"], c["domain"]) not in have]
    if lost:
        try:
            ctx.add_cookies(lost)
        except Exception:
            pass
    return ctx, live


def _keep_session(ctx) -> None:
    """After a signed-in read: save the session (it may have been refreshed)."""
    from .store import save_json
    save_json(SESSION, [c for c in ctx.cookies() if "upes" in c["domain"]])


def open_chrome() -> None:
    """Plain Chrome on Athena's own profile, so Google's reCAPTCHA sees a normal
    browser. The first attempt (3 Oct 2026) drove Chrome through Playwright's
    launcher; Chrome then announces "controlled by automated test software" and
    the reCAPTCHA on the MyUPES sign-in never verified."""
    import subprocess
    PROFILE.mkdir(parents=True, exist_ok=True)
    subprocess.Popen([CHROME, f"--user-data-dir={PROFILE}", f"--remote-debugging-port={PORT}",
                      "--no-first-run", "--no-default-browser-check", PORTAL])


def signin_and_discover(minutes: int = 20) -> None:
    """Open the portal; wait (without attaching) until you are past the sign-in
    page; then note the portal's API addresses while you click around."""
    open_chrome()
    print("Sign in to MyUPES in the Chrome window - you solve the CAPTCHA and type the password.")
    end = time.time() + minutes * 60
    while time.time() < end:
        time.sleep(3)
        urls = [p.get("url", "") for p in _pages()]
        if any("upes" in u and not re.search(r"login|signin|auth|sso|microsoftonline|accounts\.google", u, re.I)
               for u in urls):
            break
    else:
        print("No sign-in seen.")
        return
    print("Signed in - noting which portal pages and APIs load (addresses only).")
    from playwright.sync_api import sync_playwright
    seen: dict[str, int] = json.loads(SEEN.read_text()) if SEEN.exists() else {}

    def note(req):
        u = req.url.split("?")[0]
        if "upes.ac.in" in u and req.resource_type in ("xhr", "fetch"):
            seen[f"{req.method} {u}"] = seen.get(f"{req.method} {u}", 0) + 1
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}")
        for ctx in browser.contexts:
            ctx.on("request", note)
        while time.time() < end and _pages():
            time.sleep(2)
            SEEN.write_text(json.dumps(seen, indent=1))
    print(f"Noted {len(seen)} portal API addresses -> {SEEN}")


# ---------- reading: load the dashboard, keep what the portal itself fetches ----------
# Discovered 3 Oct 2026: the dashboard loads attendance from
# POST /apigateway/attendance/api/attendance/summary and the week's classes from
# POST /apigateway/api/timetable. Rather than copy the portal's token handling,
# the page is loaded with Athena's saved sign-in and those replies are read.
CACHE = DATA / "upes_cache.json"
DASHBOARD = PORTAL + "connectportal/user/student/home/dashboard"
SCHEDULE = PORTAL + "connectportal/user/student/curriculum-scheduling"     # loads the whole semester


class NotSignedIn(RuntimeError):
    pass


def fetch(force: bool = False, max_age: float = 1800) -> dict:
    """{"attendance": [...], "timetable": [...], "t": time} - cached for half an hour."""
    from .store import load_json, save_json
    cached = load_json(CACHE, {})
    if not force and cached and time.time() - cached.get("t", 0) < max_age:
        return cached
    from playwright.sync_api import sync_playwright
    got: dict = {}

    def keep(r):
        u = r.url.split("?")[0]
        try:
            if u.endswith("/attendance/api/attendance/summary"):
                got["attendance"] = r.json()
            elif u.endswith("/apigateway/api/timetable"):
                got["timetable"] = r.json()
        except Exception:
            pass
    with sync_playwright() as p:
        ctx, live = _browser(p)         # the sign-in window if open: a profile opens once
        ctx.on("response", keep)
        # The portal tab already open, not a new one: a fresh tab at the
        # wrong moment is what set off the failed renewal on 3 Oct 2026.
        mine = next((pg for pg in ctx.pages if "myupes" in pg.url), None)
        page = mine or ctx.new_page()
        try:
            for url, key in ((DASHBOARD, "attendance"), (SCHEDULE, "timetable")):
                got.pop("timetable", None) if key == "timetable" else None     # the dashboard's is today only
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                for _ in range(50):
                    if key in got:
                        break
                    if re.search(r"/auth/login", page.url):
                        raise NotSignedIn("the UPES sign-in has expired")
                    page.wait_for_timeout(500)
            if "attendance" in got:
                _keep_session(ctx)
        finally:
            if mine is None:
                page.close()
            if not live:
                ctx.close()
    if "attendance" not in got:
        raise NotSignedIn("the portal did not return attendance - sign in again")
    got["t"] = time.time()
    save_json(CACHE, got)
    return got


_memo: dict = {"mtime": None, "data": {}}


def _cached() -> dict:
    """The saved portal read, parsed once per change of the file (950 KB of
    timetable: re-reading it for every day made "next class" take 0.9 s)."""
    from .store import load_json
    try:
        mtime = CACHE.stat().st_mtime
    except OSError:
        return {}
    if _memo["mtime"] != mtime:
        _memo.update(mtime=mtime, data=load_json(CACHE, {}))
    return _memo["data"]


_refreshing: dict = {}


def _in_background(key: str, work) -> None:
    """Run `work` once on its own thread; a second call while it runs does nothing."""
    import threading
    if _refreshing.get(key):
        return
    _refreshing[key] = True

    def go():
        try:
            work()
        except Exception:
            pass
        finally:
            _refreshing[key] = False
    threading.Thread(target=go, daemon=True, name=f"athena-refresh-{key}").start()


def _saved() -> dict:
    """What Athena last read from the portal, straight away. Asking used to
    open the portal every time - 6-7 s, and often only to find it signed
    out (3 Oct 2026). Over half an hour old, a fresh read starts in the
    background for next time; only with nothing saved at all is the portal
    read while you wait."""
    cached = _cached()
    if not cached:
        return fetch()
    if time.time() - cached.get("t", 0) > 1800:
        _in_background("portal", lambda: fetch(force=True))
    return cached


def as_of(t: float) -> str:
    """' (as of 5 hours ago)' when saved data is old enough to matter, else ''."""
    hours = (time.time() - (t or 0)) / 3600
    if hours < 2:
        return ""
    return f" (as of {int(hours)} hours ago)" if hours < 48 else f" (as of {int(hours // 24)} days ago)"


def attendance(force: bool = False, cached_only: bool = False) -> list[dict]:
    from .store import load_json
    rows = []
    for a in (_cached() if cached_only else fetch(force) if force else _saved()).get("attendance") or []:
        total, present = int(a.get("TotalSessions") or 0), int(a.get("PresentCount") or 0)
        need = float(a.get("ThresholdPercentage") or 75) / 100
        pct = float(a.get("AttendancePercentage") or (100 * present / total if total else 0))
        skip = max(0, int(present / need - total)) if need else 0                 # classes you can miss
        short = max(0, -(-(need * total - present) // (1 - need))) if pct < need * 100 else 0   # classes to attend
        rows.append({"course": a.get("ModuleName", "?").strip(), "percent": pct, "present": present, "total": total,
                     "threshold": need * 100, "can_skip": skip, "need": int(short)})
    return sorted(rows, key=lambda r: r["percent"])


def _hhmm(t: str) -> str:
    import datetime as dt
    try:
        return dt.datetime.strptime(t.strip(), "%I:%M %p").strftime("%H:%M")     # "02:00 PM"
    except ValueError:
        return t[:5]


def classes(day=None, cached_only: bool = False) -> list[dict]:
    """The day's classes from the semester timetable (SlotDate is "2026-Oct-03").
    cached_only: never open the portal (the HUD must not wait on it)."""
    import datetime as dt
    from .store import load_json
    day = day or dt.date.today()
    out = []
    for s in (_cached() if cached_only else _saved()).get("timetable") or []:
        try:
            if dt.datetime.strptime(str(s.get("SlotDate", ""))[:11], "%Y-%b-%d").date() != day:
                continue
        except ValueError:
            continue
        out.append({"course": ", ".join(m.get("ModuleName", "") for m in s.get("ModuleList") or []),
                    "start": _hhmm(str(s.get("SlotStartTime", ""))), "end": _hhmm(str(s.get("SlotEndTime", ""))),
                    "room": (s.get("FloorPlanDetails") or {}).get("VenueName", ""),
                    "teacher": ", ".join(t.get("Name", "") for t in s.get("TeacherList") or []),
                    "cancelled": bool(s.get("IsCancelledSlot"))})
    return sorted(out, key=lambda c: c["start"])


def _slot_dates() -> list:
    import datetime as dt
    from .store import load_json
    out = []
    for s in _cached().get("timetable") or []:
        try:
            out.append(dt.datetime.strptime(str(s.get("SlotDate", ""))[:11], "%Y-%b-%d").date())
        except ValueError:
            pass
    return out


def covers(day) -> bool:
    """True if the cached semester timetable spans `day` - then a day with no
    slots really has no classes (exams, holidays), rather than unknown."""
    dates = _slot_dates()
    return bool(dates) and min(dates) <= day <= max(dates)


def upcoming(days: int = 60) -> list[dict]:
    """Classes from now on, each with its "date", from the cached timetable."""
    import datetime as dt
    now = dt.datetime.now()
    out = []
    for d in sorted({x for x in _slot_dates() if now.date() <= x <= now.date() + dt.timedelta(days=days)}):
        for c in classes(d, cached_only=True):
            if not c["cancelled"] and (d > now.date() or c["end"] > now.strftime("%H:%M")):
                out.append({**c, "date": d.isoformat()})
    return out


# ---------- LMS (Moodle), reached through MyUPES ----------
# lms.upes.ac.in has no sign-in of its own for students: the portal's "LMS" menu
# asks /sso/user/oauth2/access-lms for a one-time key and Moodle logs in with it
# (/auth/userkey/login.php). So: open the dashboard, click LMS, then call
# Moodle's own AJAX service from an LMS page - the same calls its pages make.
LMS = "https://lms.upes.ac.in"
LMS_CACHE = DATA / "lms_cache.json"
_LMS_CALL = """async (m) => {
  const r = await fetch(M.cfg.wwwroot + "/lib/ajax/service.php?sesskey=" + M.cfg.sesskey + "&info=" + m.name,
    {method: "POST", headers: {"Content-Type": "application/json"},
     body: JSON.stringify([{index: 0, methodname: m.name, args: m.args}])});
  return (await r.json())[0];
}"""


def _goto(page, url: str) -> None:
    """page.goto, again if it was cut off by a redirect still running (ERR_ABORTED, 3 Oct 2026)."""
    from playwright.sync_api import Error
    for attempt in range(3):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            return
        except Error:
            if attempt == 2:
                raise
            page.wait_for_timeout(2000)


def _lms_in(page) -> bool:
    # Signed out, LMS sends you on to MyUPES (or its own /login/).
    return (page.url.startswith(LMS) and "/login/" not in page.url
            and page.evaluate('!!document.querySelector("a[href*=logout]")'))


def _lms_ready(page) -> None:
    """Get `page` signed in to LMS. Moodle's own session first: it lasts
    hours, while MyUPES ends its sign-in ~25 minutes in - its token renewal
    fails on the server (watched 3 Oct 2026: refresh-token 200, then
    validate/token 500, tokens wiped). Only if LMS has signed out too does
    this go through MyUPES, whose "LMS" link signs Moodle in again."""
    from playwright.sync_api import Error
    _goto(page, LMS + "/my/")
    if _lms_in(page):
        return
    page.goto(DASHBOARD, wait_until="networkidle", timeout=60000)
    if re.search(r"/auth/login", page.url):
        raise NotSignedIn("the UPES sign-in has expired")
    page.locator('a:text-is("LMS")').first.click()
    try:
        page.wait_for_url(re.compile(r"lms\.upes\.ac\.in/(?!auth)"), timeout=15000)
    except Error:
        pass
    _goto(page, LMS + "/my/")
    if not _lms_in(page):
        raise NotSignedIn("LMS did not sign in")


def lms_touch() -> bool:
    """Keep the LMS sign-in alive: one quiet request, no tab. True if signed in."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx, live = _browser(p)
        try:
            r = ctx.request.get(LMS + "/my/", timeout=30000)
            ok = r.ok and r.url.startswith(LMS) and "/login/" not in r.url
            if ok:
                _keep_session(ctx)
            return ok
        finally:
            if not live:
                ctx.close()


def lms_fetch(force: bool = False, max_age: float = 7200) -> dict:
    """{"courses": [...], "events": [...], "t"} - cached for two hours."""
    import html
    from .store import load_json, save_json
    cached = load_json(LMS_CACHE, {})
    if not force and cached and time.time() - cached.get("t", 0) < max_age:
        return cached
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx, live = _browser(p)
        lms = ctx.new_page()
        try:
            _lms_ready(lms)
            _keep_session(ctx)
            now = int(time.time())
            ev = lms.evaluate(_LMS_CALL, {"name": "core_calendar_get_action_events_by_timesort", "args": {
                "limitnum": 50, "timesortfrom": now - 14 * 86400, "timesortto": now + 90 * 86400,
                "limittononsuspendedevents": True}})
            cs = lms.evaluate(_LMS_CALL, {"name": "core_course_get_enrolled_courses_by_timeline_classification",
                                          "args": {"offset": 0, "limit": 0, "classification": "inprogress",
                                                   "sort": "fullname"}})
        finally:
            lms.close()
            if not live:
                ctx.close()
    courses = {c["id"]: re.sub(r"_Sem\d+$", "", c["fullname"]) for c in (cs.get("data") or {}).get("courses", [])}
    events = []
    for e in (ev.get("data") or {}).get("events", []):
        events.append({"id": e["id"], "name": html.unescape(re.sub(r"\s+is due$", "", e["name"])),
                       "course": courses.get((e.get("course") or {}).get("id"), (e.get("course") or {}).get("fullname", "")),
                       "due": e["timesort"], "type": e.get("modulename", ""), "url": e.get("url", ""),
                       "done": not (e.get("action") or {}).get("actionable", True)})
    data = {"t": time.time(), "courses": sorted(courses.values()), "events": events,
            "course_ids": {name: cid for cid, name in courses.items()}}
    save_json(LMS_CACHE, data)
    return data


def lms_sync_tasks() -> int:
    """Open LMS deadlines into Tasks (the study autopilot plans them from there)."""
    import datetime as dt
    from .tools import todo
    tasks = todo.load()
    have = {t["text"] for t in tasks}
    added = 0
    for e in lms_fetch().get("events", []):
        text = f"{e['name']} ({e['course']})"
        if e["done"] or text in have:
            continue
        tasks.append({"id": __import__("secrets").token_hex(3), "text": text,
                      "due": dt.date.fromtimestamp(e["due"]).isoformat(), "done": False,
                      "created": dt.datetime.now().isoformat(timespec="seconds"), "source": "lms"})
        added += 1
    if added:
        todo.save(tasks)
    return added


# ---------- what you can ask ----------
from .registry import tool  # noqa: E402


def _signin_help(e: Exception) -> str:
    return (f"I can't read the UPES portal right now ({e}). Say 'open the UPES login' and sign in once, "
            "ticking Remember me.")


def _match(courses: list[dict], name: str) -> dict | None:
    from difflib import SequenceMatcher
    want = name.lower().replace("&", "and")
    alias = {"dsa": "data structure", "ds": "data structure", "dsd": "digital system", "probability": "probability",
             "stats": "probability", "maths": "probability", "math": "probability", "signals": "signals",
             "analog": "analog", "marketing": "marketing", "leading": "leading"}
    want = next((v for k, v in alias.items() if re.search(rf"\b{k}\b", want)), want)
    best = max(courses, key=lambda c: (want in c["course"].lower(), SequenceMatcher(None, want, c["course"].lower()).ratio()),
               default=None)
    return best if best and (want in best["course"].lower() or SequenceMatcher(None, want, best["course"].lower()).ratio() > 0.45) else None


@tool(tags=["study"], timeout=120)
def my_attendance(course: str = "") -> str:
    """Live attendance from the UPES portal - all courses, or one.

    course: a course name or short form (DSA, signals, probability), or empty for all
    """
    try:
        rows = attendance()
    except Exception as e:
        return _signin_help(e)
    if course.strip():
        r = _match(rows, course)
        if r is None:
            return "I couldn't find that course. You have: " + ", ".join(x["course"] for x in rows) + "."
        rows = [r]
    low = [r for r in rows if r["percent"] < r["threshold"]]
    parts = [f"{r['course']} {r['percent']:.0f}% ({r['present']}/{r['total']})"
             + (f" - needs {r['need']} more" if r["need"] else f" - can skip {r['can_skip']}") for r in rows]
    head = f"{len(low)} below 75%. " if len(rows) > 1 and low else ""
    from .store import load_json
    return head + "; ".join(parts) + as_of(load_json(CACHE, {}).get("t", 0)) + "."


@tool(tags=["study"], timeout=120)
def can_i_skip(course: str) -> str:
    """Whether missing a class keeps attendance at or above 75%.

    course: the course name or short form
    """
    try:
        r = _match(attendance(), course)
    except Exception as e:
        return _signin_help(e)
    if r is None:
        return "I couldn't find that course on your attendance."
    after = 100 * r["present"] / (r["total"] + 1)
    if r["can_skip"] >= 1:
        return (f"Yes - {r['course']} is at {r['percent']:.0f}%, and missing one class leaves you at {after:.0f}%. "
                f"You can skip {r['can_skip']} in total.")
    return (f"No - {r['course']} is at {r['percent']:.0f}%; missing one takes you to {after:.0f}%. "
            f"You need {max(1, r['need'])} more class{'es' if r['need'] > 1 else ''} first.")


@tool(tags=["study"], timeout=120)
def upes_classes(day: str = "today") -> str:
    """Classes on the UPES timetable, with rooms and teachers.

    day: today, tomorrow, or next (the next class, however far ahead)
    """
    import datetime as dt
    if day.strip().lower().startswith("next"):
        try:
            ahead = upcoming()
        except Exception as e:
            return _signin_help(e)
        if not ahead:
            return "No more classes on your timetable."
        c = ahead[0]
        d = dt.date.fromisoformat(c["date"])
        gap = (d - dt.date.today()).days
        when = "today" if gap == 0 else "tomorrow" if gap == 1 else d.strftime("%A %d %B").replace(" 0", " ")
        return (f"Your next class is {c['course']}, {when} at {c['start']} in {c['room']}."
                + (" There are no classes before then." if gap > 3 else ""))
    d = dt.date.today() + dt.timedelta(days=1 if day.strip().lower().startswith("tom") else 0)
    try:
        cs = [c for c in classes(d) if not c["cancelled"]]
    except Exception as e:
        return _signin_help(e)
    if not cs:
        return f"No classes {day}."
    return f"{day.capitalize()}: " + "; ".join(f"{c['start']} {c['course']} in {c['room']}" for c in cs) + "."


@tool(tags=["study"], timeout=180)
def lms_deadlines() -> str:
    """Assignments, quizzes and forum posts due on UPES LMS."""
    import datetime as dt
    from .store import load_json
    saved = load_json(LMS_CACHE, {})
    try:
        if not saved:
            saved = lms_fetch(force=True)
            lms_sync_tasks()
        elif time.time() - saved.get("t", 0) > 1800:      # answer now, refresh for next time
            _in_background("lms", lambda: (lms_fetch(force=True), lms_sync_tasks()))
    except Exception as e:
        return _signin_help(e)
    events = saved.get("events", [])
    now = time.time()
    todo_ = [e for e in events if not e["done"]]
    if not todo_:
        return "Nothing due on LMS."
    def when(e):
        d = (dt.date.fromtimestamp(e["due"]) - dt.date.today()).days
        return "OVERDUE" if e["due"] < now else "today" if d == 0 else "tomorrow" if d == 1 else f"in {d} days"
    return ("On LMS: " + "; ".join(f"{e['name']} ({e['course']}) - {when(e)}" for e in sorted(todo_, key=lambda e: e["due"]))
            + as_of(saved.get("t", 0)) + ".")


def _lms_target(what: str) -> tuple[str, str] | None:
    """(label, url) for an LMS item or course named loosely."""
    from difflib import SequenceMatcher
    d = lms_fetch()
    w = re.sub(r"\b(?:the|my|on|in|lms|moodle|open|page|course)\b", " ", what.lower())
    w = re.sub(r"\s+", " ", w).strip()
    for short, full in (("dsa", "data structure"), ("ds", "data structure"), ("dsd", "digital system design"),
                        ("stats", "probability and statistics"), ("maths", "probability and statistics")):
        w = re.sub(rf"\b{short}\b", full, w)
    if not w:
        return "LMS", LMS + "/my/courses.php"
    def score(text: str) -> float:
        t = text.lower()
        words = [x for x in w.split() if len(x) > 2]
        return sum(1 for x in words if x in t) / max(1, len(words)) + SequenceMatcher(None, w, t).ratio() * 0.3
    items = [(e["name"] + " " + e["course"], e["name"], e["url"]) for e in d.get("events", []) if e.get("url")]
    items += [(name, name, f"{LMS}/course/view.php?id={cid}") for name, cid in d.get("course_ids", {}).items()]
    if not items:
        return None
    best = max(items, key=lambda i: score(i[0]))
    return (best[1], best[2]) if score(best[0]) >= 0.5 else None


@tool(tags=["study"], timeout=120)
def open_lms(what: str = "") -> str:
    """Open an assignment, forum, quiz or course on UPES LMS, signed in.

    what: the item or course in the user's words, e.g. "feedback forum", "DSA course"; empty for LMS home
    """
    try:
        target = _lms_target(what)
    except Exception as e:
        return _signin_help(e)
    if target is None:
        return f"I couldn't find '{what}' on LMS."
    label, url = target
    if not _pages():
        open_chrome()
        for _ in range(20):
            time.sleep(0.5)
            if _pages():
                break
    if not _open_in_window(url):
        _after_signin(lambda: _open_in_window(url))
        return f"You're signed out of UPES - sign in in the Chrome window and I'll open {label} straight after."
    return f"Opened {label} on LMS."


def _open_in_window(url: str) -> bool:
    """Open an LMS address in the visible window, signed in. False if UPES wants a sign-in."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx, _ = _browser(p)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            _lms_ready(page)
        except NotSignedIn:
            page.bring_to_front()
            return False
        _keep_session(ctx)
        _goto(page, url)
        page.bring_to_front()
    return True


def _after_signin(then) -> None:
    """Wait (in the background) for you to sign in, save the session, then do `then`."""
    import threading

    def run():
        if wait_signed_in(15):
            try:
                fetch(force=True)        # reads attendance and saves the session
            except Exception:
                pass
            if then:
                try:
                    then()
                except Exception:
                    pass
    threading.Thread(target=run, daemon=True, name="athena-upes-signin").start()


@tool(tags=["study"], timeout=60)
def open_upes_login() -> str:
    """Open the UPES portal in Athena's own Chrome window so you can sign in
    (you type the password and solve the CAPTCHA; tick Remember me)."""
    if not _pages():
        open_chrome()
    _after_signin(None)
    return "MyUPES is open in my Chrome window. If it asks, sign in - after that you can close the window."


def start() -> None:
    """In the voice process: the portal every 30 minutes, LMS kept signed in
    every 15, LMS deadlines into Tasks every two hours."""
    import threading

    def loop():
        from .store import load_json, save_json
        last_portal = last_tasks = 0.0
        flag = DATA / "upes_signed_out.json"
        while True:
            if time.time() - last_portal > 1800:
                last_portal = time.time()
                try:
                    fetch(force=True)
                    if flag.exists():
                        flag.unlink()
                except NotSignedIn:
                    # MyUPES ends every sign-in ~25 minutes in (see _lms_ready),
                    # so being signed out is normal. Only ask for a sign-in when
                    # the attendance Athena has is getting old - once a day.
                    f = load_json(flag, {})
                    f.setdefault("since", time.time())
                    age = time.time() - load_json(CACHE, {}).get("t", 0)
                    if age > 20 * 3600 and time.time() - f.get("told", 0) > 20 * 3600:
                        f["told"] = time.time()
                        try:
                            from . import alerts, live
                            live.note("UPES attendance is a day old - say 'open UPES' and sign in")
                            alerts.submit("reminders", f"upes-signed-out:{int(time.time() // 86400)}",
                                          "Your UPES attendance on Athena is a day old. Say 'open UPES' and sign in once.")
                        except Exception:
                            pass
                    save_json(flag, f)
                except Exception:
                    pass
            try:
                lms_touch()                              # keeps Moodle's own sign-in alive
            except Exception:
                pass
            if time.time() - last_tasks > 7200:          # LMS deadlines into Tasks
                last_tasks = time.time()
                try:
                    lms_sync_tasks()
                except Exception:
                    pass
            time.sleep(900)
    threading.Thread(target=loop, daemon=True, name="athena-upes").start()


def wait_signed_in(minutes: int = 20) -> bool:
    """Open plain Chrome on the portal and wait until you are past the sign-in page. Leaves it open."""
    if not _pages():
        open_chrome()
    end = time.time() + minutes * 60
    while time.time() < end:
        time.sleep(3)
        urls = [p.get("url", "") for p in _pages()]
        # the portal's own pages, not its root (which redirects to the sign-in)
        if any(re.search(r"/connectportal/|lms\.upes\.ac\.in/(?!auth|login)", u) for u in urls):
            return True
    return False


if __name__ == "__main__":
    import re
    cmd = (sys.argv[1:] or ["signin"])[0]
    if cmd == "signin":
        signin_and_discover()
    elif cmd == "wait":
        print("signed in" if wait_signed_in() else "not signed in")
    elif cmd == "login":
        login()
