"""The UPES LMS (Moodle), reached through the same signed-in browser: is it signed in,
assignment deadlines into Tasks, and downloading new course files.

Split out of upes.py (31 Oct 2026); upes.py imports all of it back.
"""
from __future__ import annotations

import re
import time

from .config import DATA
from .upes_browser import DASHBOARD, NotSignedIn, _browser, _keep_session


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


FILES_SEEN = DATA / "lms_files.json"


def lms_download_files() -> list[str]:
    """Course files posted on LMS (slides, notes, course plans) into
    Documents/LMS/<course>/ - only ones not fetched before - and into document
    search, so "search my documents for Laplace" reaches the lecture slides
    (9 Oct 2026). Read with your own LMS session; nothing is uploaded."""
    import urllib.parse
    from playwright.sync_api import sync_playwright
    from .config import cfg, expand
    from .store import load_json, save_json
    folder = expand(cfg.get("lms.files_dir", "~/OneDrive/Documents/LMS"))
    seen: dict[str, str] = load_json(FILES_SEEN, {})
    new: list[str] = []
    safe = lambda s: " ".join(re.sub(r'[\\/:*?"<>|]+', " ", s).split())[:80]      # noqa: E731
    with sync_playwright() as p:
        ctx, live = _browser(p)
        page = ctx.new_page()
        try:
            _lms_ready(page)
            _keep_session(ctx)
            for course, cid in lms_fetch().get("course_ids", {}).items():
                _goto(page, LMS + f"/course/view.php?id={cid}")
                hrefs = set(page.eval_on_selector_all("a[href*='/mod/resource/view.php']",
                                                      "els => els.map(e => e.href.split('#')[0])"))
                for href in sorted(hrefs - set(seen)):
                    r = ctx.request.get(href + "&redirect=1", timeout=120000)
                    kind = r.headers.get("content-type", "")
                    if not r.ok or "text/html" in kind:
                        seen[href] = "not a file"        # a page, not a download
                        continue
                    body = r.body()
                    if len(body) > 50 * 1024 * 1024:
                        seen[href] = "too big"
                        continue
                    name = safe(urllib.parse.unquote(r.url.split("?")[0].rsplit("/", 1)[-1])) or f"file {len(seen)}"
                    d = folder / safe(course)
                    d.mkdir(parents=True, exist_ok=True)
                    (d / name).write_bytes(body)
                    seen[href] = str(d / name)
                    new.append(f"{name} ({course})")
                    save_json(FILES_SEEN, seen)          # each one kept, should a later one fail
        finally:
            page.close()
            if not live:
                ctx.close()
    save_json(FILES_SEEN, seen)
    if new:
        from .tools.knowledge import index_documents
        index_documents(str(folder))
    return new
