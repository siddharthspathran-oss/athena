"""UPES attendance from myUPES, read the way the portal's own page reads it.

How the portal works (seen in the browser's Network tab, 29 Sep 2026):
the attendance page (connectportal/user/student/student-attendance) posts
to apigateway/student-attendance/studentattendancesummary with the student
id, the term, a date range and ONE course from the dropdown; the answer has
TotalAttendance, TotalSessionCount and TotalPresentPercentage.

Athena never stores the login. It opens that page in its own Chrome
profile (data/portal_profile - log in there once with `python run.py
upes-login`), lets the page make its own request with its own fresh token,
and then asks the same question for every course in the page's course list
- the list the dropdown is filled from, found as the list that contains the
course the page asked about. The token lives in memory for those few
seconds only.

Saved in data/upes_attendance.json; answers come from there, refreshed every
upes.every_hours in the background and when older than that on asking.
"""
from __future__ import annotations

import json
import math
import re
import threading
import time
from pathlib import Path

from ..config import DATA, cfg
from ..registry import tool
from ..store import StoreError, load_json, save_json

TAG = ["schedule"]
SAVED = DATA / "upes_attendance.json"
PROFILE = DATA / "portal_profile"          # shared with portal_capture
PAGE = "https://myupes-beta.upes.ac.in/connectportal/user/student/student-attendance"
SUMMARY = "studentattendancesummary"

_lock = threading.Lock()


class NotLoggedIn(RuntimeError):
    pass


# ---------------------------------------------------------------- numbers

def minimum() -> float:
    """The attendance a subject must keep, as a fraction (upes.min_attendance)."""
    v = float(cfg.get("upes.min_attendance", 75))
    return v / 100 if v > 1 else v


def can_miss(attended: int, total: int, need: float) -> int:
    """Classes that can still be missed in a row with the share staying at
    or above `need`. 0 when already at or below it."""
    if total <= 0:
        return 0
    return max(0, math.floor(attended / need - total + 1e-9))


def must_attend(attended: int, total: int, need: float) -> int:
    """Classes to attend in a row to get back up to `need`."""
    if total <= 0 or attended / total >= need:
        return 0
    return math.ceil((need * total - attended) / (1 - need) - 1e-9)


def _course(summary: dict, name: str, cid) -> dict:
    s = summary.get("AttendanceSummary") or {}
    attended = int(s.get("TotalAttendance") or 0)
    total = int(s.get("TotalSessionCount") or 0)
    pct = s.get("TotalPresentPercentage")
    if pct is None and total:
        pct = round(100 * attended / total, 2)
    return {"id": cid, "name": name, "attended": attended, "total": total,
            "condoned": int(s.get("TotalCondonedAttendanceCount") or 0),
            "percent": float(pct or 0)}


# ---------------------------------------------------------------- the course list

def _find_courses(responses: list, known_id, known_name: str) -> list[dict]:
    """The dropdown's course list: a list, in any JSON the page loaded, that
    holds an item with the known course's id and name. Returned as
    [{"ID":..., "Name":...}] - the shape the summary request takes."""
    def walk(node):
        if isinstance(node, list):
            dicts = [x for x in node if isinstance(x, dict)]
            for d in dicts:
                id_key = next((k for k, v in d.items() if str(v) == str(known_id)), None)
                name_key = next((k for k, v in d.items()
                                 if isinstance(v, str) and v.strip().lower() == known_name.strip().lower()), None)
                if id_key and name_key:
                    return [{"ID": x.get(id_key), "Name": x.get(name_key)}
                            for x in dicts if x.get(id_key) is not None and x.get(name_key)]
            for x in node:
                got = walk(x)
                if got:
                    return got
        elif isinstance(node, dict):
            for v in node.values():
                got = walk(v)
                if got:
                    return got
        return []

    for body in responses:
        got = walk(body)
        if got:
            seen, out = set(), []
            for c in got:
                if str(c["ID"]) not in seen:
                    seen.add(str(c["ID"]))
                    out.append(c)
            return out
    return []


# ---------------------------------------------------------------- the browser

# Without these Chrome announces it is automated (navigator.webdriver and
# the "controlled by automated software" bar), and Google's reCAPTCHA on
# the myUPES login would not load.
_QUIET = dict(ignore_default_args=["--enable-automation"],
              args=["--disable-blink-features=AutomationControlled"])


def _open(pw, headless: bool):
    PROFILE.mkdir(parents=True, exist_ok=True)
    kw = dict(user_data_dir=str(PROFILE), headless=headless, no_viewport=True, **_QUIET)
    try:
        return pw.chromium.launch_persistent_context(channel="chrome", **kw)
    except Exception:
        return pw.chromium.launch_persistent_context(channel="msedge", **kw)


def _pick_first_course(page) -> None:
    """If the page waits for a choice, choose the first course, as a person would."""
    for sel in ("mat-select", "[role=combobox]", "select", ".dropdown-toggle", "ng-select"):
        el = page.query_selector(sel)
        if not el:
            continue
        try:
            if sel == "select":
                opts = el.query_selector_all("option")
                vals = [o.get_attribute("value") for o in opts if o.get_attribute("value")]
                if vals:
                    el.select_option(vals[0])
                    return
            el.click()
            page.wait_for_timeout(800)
            opt = page.query_selector("[role=option], mat-option, .dropdown-item, .ng-option")
            if opt:
                opt.click()
                return
        except Exception:
            continue


def fetch(headless: bool = True, wait: float = 40) -> list[dict]:
    """Every course's attendance, read live from myUPES."""
    from playwright.sync_api import sync_playwright

    seen: dict = {}
    loaded: list = []

    def on_response(resp):
        try:
            req = resp.request
            if req.resource_type not in ("xhr", "fetch"):
                return
            if "json" not in (resp.headers.get("content-type") or ""):
                return
            body = resp.json()
            if SUMMARY in req.url.lower() and req.method == "POST" and "req" not in seen:
                seen["req"] = (req.url, dict(req.all_headers()), req.post_data or "{}")
                seen["first"] = body
            else:
                loaded.append(body)
        except Exception:
            pass

    with sync_playwright() as pw:
        ctx = _open(pw, headless)
        try:
            ctx.on("response", on_response)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(PAGE, wait_until="domcontentloaded", timeout=60_000)
            end = time.time() + wait
            tried_pick = False
            while "req" not in seen and time.time() < end:
                page.wait_for_timeout(500)
                if not tried_pick and time.time() > end - wait / 2:
                    tried_pick = True
                    _pick_first_course(page)
            if "req" not in seen:
                url = page.url.lower()
                if "student-attendance" not in url or re.search(r"login|signin|idp|auth", url):
                    raise NotLoggedIn(url)
                raise RuntimeError("the attendance page loaded but never asked for attendance")
            page.wait_for_timeout(1500)          # the rest of the page's lists

            url, headers, raw = seen["req"]
            asked = json.loads(raw)
            headers = {k: v for k, v in headers.items()
                       if not k.startswith(":") and k.lower() not in ("content-length", "host", "cookie")}
            first = (asked.get("CourseList") or [{}])[0]
            courses = _find_courses(loaded, first.get("ID"), first.get("Name") or "") or [first]
            out = []
            for c in courses:
                if str(c.get("ID")) == str(first.get("ID")):
                    body = seen["first"]
                else:
                    payload = dict(asked, CourseList=[{"ID": c["ID"], "Name": c["Name"]}])
                    r = ctx.request.post(url, headers=headers, data=json.dumps(payload), timeout=30_000)
                    if not r.ok:
                        continue
                    body = r.json()
                out.append(_course(body, c.get("Name") or "?", c.get("ID")))
            return out
        finally:
            ctx.close()


VISIBLE_FLAG = DATA / "upes_visible"      # set when reading only works in a shown window


def refresh(headless: bool | None = None) -> list[dict]:
    if headless is None:
        headless = bool(cfg.get("upes.headless", True)) and not VISIBLE_FLAG.exists()
    with _lock:
        courses = fetch(headless=headless)
        if courses:
            save_json(SAVED, {"updated": time.time(), "courses": courses})
        return courses


def saved() -> dict:
    try:
        data = load_json(SAVED, {})
    except StoreError:
        return {}
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------- words

def _line(c: dict, need: float) -> str:
    base = f"{c['name']}: {c['percent']:.1f}% ({c['attended']} of {c['total']})"
    if c["total"] <= 0:
        return base
    if c["attended"] / c["total"] >= need:
        n = can_miss(c["attended"], c["total"], need)
        return base + (f" - you can miss {n} more" if n else " - don't miss the next one")
    n = must_attend(c["attended"], c["total"], need)
    return base + f" - below {need * 100:.0f}%, attend the next {n} to get back"


def _match(courses: list[dict], subject: str) -> list[dict]:
    words = [w for w in re.findall(r"[a-z0-9]+", subject.lower()) if w not in ("and", "the", "of", "in", "my")]
    if not words:
        return courses
    def score(c):
        name = c["name"].lower()
        initials = "".join(w[0] for w in re.findall(r"[a-z]+", name))
        return sum(1 for w in words if w in name or w == initials)
    best = max((score(c) for c in courses), default=0)
    return [c for c in courses if best and score(c) == best]


def _age(ts: float) -> str:
    mins = int((time.time() - ts) / 60)
    if mins < 2:
        return "just now"
    if mins < 90:
        return f"{mins} minutes ago"
    return f"{mins // 60} hours ago"


_LOGIN_HELP = ("I'm not logged in to myUPES. Run `python run.py upes-login` on the PC, "
               "log in in the window that opens, and ask me again.")


@tool(tags=TAG, timeout=150)
def attendance(subject: str = "", refresh_now: bool = False) -> str:
    """The user's UPES class attendance from myUPES: percentage per subject and
    how many classes they can still miss. Use for "what's my attendance",
    "attendance in data structures", "how many classes can I bunk".

    subject: one subject, in the user's words; empty for all
    refresh_now: true to read myUPES again now instead of the saved numbers
    """
    data = saved()
    stale = time.time() - data.get("updated", 0) > float(cfg.get("upes.every_hours", 6)) * 3600
    note = ""
    if refresh_now or not data.get("courses") or stale:
        try:
            refresh()
            data = saved()
        except NotLoggedIn:
            if not data.get("courses"):
                return _LOGIN_HELP
            note = " (myUPES wants you to log in again: python run.py upes-login)"
        except Exception as e:
            if not data.get("courses"):
                return f"I couldn't read myUPES: {e}."
            note = " (couldn't refresh just now)"
    courses = data.get("courses") or []
    if not courses:
        return "myUPES showed no courses."
    need = minimum()
    picked = _match(courses, subject) if subject else courses
    if not picked:
        names = ", ".join(c["name"] for c in courses)
        return f"No subject matches {subject}. Your subjects: {names}."
    when = _age(data.get("updated", 0))
    return "; ".join(_line(c, need) for c in picked) + f". Updated {when}{note}."


# ---------------------------------------------------------------- background

def _warn(courses: list[dict]) -> None:
    need = minimum()
    low = [c for c in courses if c["total"] and (c["attended"] / c["total"] < need
                                                 or can_miss(c["attended"], c["total"], need) == 0)]
    if not low:
        return
    text = "Attendance: " + "; ".join(_line(c, need) for c in low)
    try:
        from .. import live
        live.note(text[:200])
    except Exception:
        pass
    if cfg.get("upes.push_warnings", True):
        try:
            from .. import phone
            phone.push(text[:300], title="Athena · attendance")
        except Exception:
            pass


def start() -> None:
    if not cfg.get("upes.enabled", True) or not PROFILE.exists():
        return                    # never logged in: nothing to read yet
    hours = float(cfg.get("upes.every_hours", 6))

    def loop():
        time.sleep(180)
        while True:
            try:
                from .. import gaming
                if not gaming.on():
                    before = {c["id"]: c["attended"] for c in saved().get("courses", [])}
                    courses = refresh()
                    after = {c["id"]: c["attended"] for c in courses}
                    if courses and after != before:
                        _warn(courses)
            except NotLoggedIn:
                print("[upes] logged out of myUPES - run: python run.py upes-login")
            except Exception as e:
                print(f"[upes] {e}")
            time.sleep(hours * 3600)
    threading.Thread(target=loop, daemon=True, name="athena-upes").start()


def _real_browser() -> list[str] | None:
    """Chrome (or Edge) itself, not driven by Playwright."""
    import os
    import shutil

    found = shutil.which("chrome") or shutil.which("msedge")
    if found:
        return [found]
    for base in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"),
                 os.environ.get("LOCALAPPDATA")):
        if not base:
            continue
        for rel in (r"Google\Chrome\Application\chrome.exe", r"Microsoft\Edge\Application\msedge.exe"):
            p = Path(base) / rel
            if p.exists():
                return [str(p)]
    return None


def login() -> int:
    """`python run.py upes-login`: log in once in a plain Chrome window, then a
    test read.

    The login window is ordinary Chrome started with Athena's profile - not
    one Playwright drives - because reCAPTCHA would not load in an automated
    browser (29 Sep 2026). Reads afterwards reuse the cookies it leaves.
    """
    import subprocess

    PROFILE.mkdir(parents=True, exist_ok=True)
    exe = _real_browser()
    if not exe:
        print("Chrome or Edge was not found. Install Chrome and run this again.")
        return 1
    proc = subprocess.Popen(exe + [f"--user-data-dir={PROFILE}", "--no-first-run",
                                   "--no-default-browser-check", "--new-window", PAGE])
    print("\nA Chrome window opened with Athena's own profile.")
    print("1. Log in to myUPES there (the captcha works normally in it).")
    print("2. When your attendance page shows, CLOSE that Chrome window.")
    print("Waiting for the window to close...")
    try:
        proc.wait()
    except KeyboardInterrupt:
        pass
    time.sleep(2)                       # let Chrome finish writing the profile
    print("Reading your attendance...")
    try:
        courses = refresh(headless=True)
        VISIBLE_FLAG.unlink(missing_ok=True)
    except NotLoggedIn:
        print("Not visible to a hidden window - trying once with the window shown...")
        try:
            courses = refresh(headless=False)
            VISIBLE_FLAG.write_text("reads need a shown window")
            print("That worked: Athena will show a Chrome window briefly when it reads.")
        except NotLoggedIn:
            print("Still not logged in - myUPES sent the page back to its login.")
            print("Run this again and make sure the attendance page shows before")
            print("closing the window.")
            return 1
        except Exception as e:
            print(f"Could not read attendance: {e}")
            return 1
    except Exception as e:
        print(f"Could not read attendance: {e}")
        return 1
    need = minimum()
    for c in courses:
        print("  " + _line(c, need))
    print(f"\n{len(courses)} subjects saved. Ask Athena \"what's my attendance\".")
    return 0
