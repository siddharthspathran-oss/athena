"""The browser Athena uses for UPES: its own Chrome profile (where you signed in), the
debugging port it's driven through, the saved session, and finding its open pages.

Split out of upes.py (31 Oct 2026); upes.py and upes_lms.py use it, and upes.py
imports it all back.
"""
from __future__ import annotations

import json

from .config import DATA


PORTAL = "https://myupes-beta.upes.ac.in/"
DASHBOARD = PORTAL + "connectportal/user/student/home/dashboard"


PROFILE = DATA / "upes_profile"


def _context(p, headless: bool):
    PROFILE.mkdir(parents=True, exist_ok=True)
    return p.chromium.launch_persistent_context(str(PROFILE), channel="chrome", headless=headless,
                                                viewport={"width": 1280, "height": 860})


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


class NotSignedIn(RuntimeError):
    pass
