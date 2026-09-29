"""Watch a web portal while you use it, and write down how its pages get
their data - so a tool can be built for it without guessing.

    python run.py portal-capture

Chrome opens with its own Athena profile (data/portal_profile). Log in to
the portal (UPES attendance, timetable, LMS), open each page whose data you
want Athena to have, then press Enter in the console. Two files are written:

  data/portal_capture.json   everything seen, kept on this PC only
  portal_capture.txt         (Athena's folder) the same with your login taken
                             out: header *names* but not their values, form
                             field names but not what you typed, and data
                             cut down to its shape and a few sample values.
                             This is the one to send.

The profile stays, so a later UPES tool can reuse the login you made here
instead of asking for your password.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .config import DATA, ROOT

PROFILE = DATA / "portal_profile"
RAW = DATA / "portal_capture.json"
REPORT = ROOT / "portal_capture.txt"

# Header values safe to show; every other header shows as "<hidden>".
_SAFE_HEADERS = {"accept", "content-type", "origin", "referer", "x-requested-with",
                 "sec-fetch-mode", "sec-fetch-site", "sec-fetch-dest"}
_SECRET_KEY = re.compile(r"pass|pwd|token|secret|session|cookie|auth|otp|captcha|jwt|key|"
                         r"csrf|xsrf|sid$|signature|credential|code_verifier|nonce", re.I)
_TOKENISH = re.compile(r"^[A-Za-z0-9_\-+/=.]{24,}$")
_SKIP_TYPES = {"image", "stylesheet", "font", "media", "manifest", "texttrack", "other", "ping"}
_SKIP_HOSTS = re.compile(r"google-analytics|googletagmanager|doubleclick|clarity\.ms|hotjar|"
                         r"facebook|sentry|newrelic|nr-data|gstatic|fonts\.googleapis", re.I)


def _hide(key: str, value):
    """A value as it may appear in the report."""
    if isinstance(value, str):
        if _SECRET_KEY.search(key or "") or _TOKENISH.match(value.strip()):
            return "<hidden>"
        return value if len(value) <= 60 else value[:57] + "..."
    if isinstance(value, (int, float, bool)) or value is None:
        return "<hidden>" if _SECRET_KEY.search(key or "") else value
    return value


def _shape(obj, key: str = "", depth: int = 0):
    """JSON cut down to its structure: every key kept, secrets hidden, lists
    to their first two items (with the count), strings shortened."""
    if depth > 8:
        return "..."
    if isinstance(obj, dict):
        return {k: _shape(v, k, depth + 1) for k, v in list(obj.items())[:60]}
    if isinstance(obj, list):
        items = [_shape(v, key, depth + 1) for v in obj[:2]]
        return items + ([f"... {len(obj)} items in all"] if len(obj) > 2 else [])
    return _hide(key, obj)


def _clean_url(url: str) -> str:
    parts = urlsplit(url)
    query = urlencode([(k, _hide(k, v)) for k, v in parse_qsl(parts.query, keep_blank_values=True)])
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def _clean_body(body: str | None, ctype: str) -> object:
    if not body:
        return None
    try:
        return _shape(json.loads(body))
    except (ValueError, TypeError):
        pass
    if "form" in ctype or ("=" in body and "&" in body):
        return {k: _hide(k, v) if not _SECRET_KEY.search(k) else "<hidden>"
                for k, v in parse_qsl(body, keep_blank_values=True)}
    return f"<{len(body)} characters, not JSON>"


def _report(entries: list[dict]) -> str:
    out = ["Athena portal capture - login values removed. Safe to send.",
           f"Taken {time.strftime('%Y-%m-%d %H:%M')}, {len(entries)} requests.", ""]
    for i, e in enumerate(entries, 1):
        out.append(f"#{i}  {e['method']} {_clean_url(e['url'])}  -> {e.get('status')}")
        out.append(f"    kind: {e['type']}   page: {_clean_url(e.get('page') or '')}")
        names = [f"{k}: {v}" if k in _SAFE_HEADERS else f"{k}: <hidden>"
                 for k, v in sorted(e.get("req_headers", {}).items()) if not k.startswith(":")]
        if names:
            out.append("    request headers: " + " | ".join(names))
        if e.get("set_cookie"):
            out.append("    sets cookies: " + ", ".join(e["set_cookie"]))
        body = _clean_body(e.get("post"), e.get("req_headers", {}).get("content-type", ""))
        if body is not None:
            out.append("    sends: " + json.dumps(body, ensure_ascii=False)[:1500])
        if e.get("title"):
            out.append(f"    page title: {e['title']}")
        if e.get("response") is not None:
            shaped = _clean_body(e["response"], e.get("res_type", ""))
            out.append(f"    answers ({e.get('res_type', '?')}): "
                       + json.dumps(shaped, ensure_ascii=False, indent=1)[:4000].replace("\n", "\n    "))
        out.append("")
    return "\n".join(out)


def run(start_url: str = "") -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright is missing:  pip install playwright")
        return 1
    entries: list[dict] = []

    def on_response(resp) -> None:
        try:
            _record(resp)
        except Exception:
            pass            # one odd request (a service worker's) never stops the capture

    def _record(resp) -> None:
        req = resp.request
        try:
            page_url = req.frame.url
        except Exception:
            page_url = ""
        if req.resource_type in _SKIP_TYPES or _SKIP_HOSTS.search(req.url):
            return
        if req.url.startswith(("data:", "blob:", "chrome")):
            return
        e = {"url": req.url, "method": req.method, "type": req.resource_type,
             "status": resp.status, "page": page_url,
             "req_headers": {k.lower(): v for k, v in req.headers.items()},
             "post": req.post_data if req.method != "GET" else None,
             "res_type": resp.headers.get("content-type", "")}
        cookies = resp.headers.get("set-cookie", "")
        if cookies:
            e["set_cookie"] = sorted({c.split("=", 1)[0].strip() for c in cookies.split("\n") if "=" in c})
        if req.resource_type in ("xhr", "fetch") and "json" in e["res_type"]:
            try:
                e["response"] = resp.text()[:400_000]
            except Exception:
                pass
        elif req.resource_type == "document":
            try:
                m = re.search(r"<title[^>]*>(.*?)</title>", resp.text()[:200_000], re.S | re.I)
                e["title"] = m.group(1).strip()[:120] if m else ""
            except Exception:
                pass
        entries.append(e)

    PROFILE.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        kw = dict(user_data_dir=str(PROFILE), headless=False, no_viewport=True)
        try:
            ctx = pw.chromium.launch_persistent_context(channel="chrome", **kw)
        except Exception:
            ctx = pw.chromium.launch_persistent_context(channel="msedge", **kw)
        ctx.on("response", on_response)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        if start_url:
            page.goto(start_url)
        print("\nChrome is open. Log in to the portal, then open each page you want")
        print("Athena to read: attendance, timetable, LMS courses and assignments.")
        print("Wait for each page to finish loading.")
        input("\nWhen you are done, press Enter here... ")
        ctx.close()

    RAW.write_text(json.dumps(entries, indent=1, ensure_ascii=False), encoding="utf-8")
    REPORT.write_text(_report(entries), encoding="utf-8")
    print(f"\nSeen {len(entries)} requests.")
    print(f"Send this file (your login is already taken out):\n  {REPORT}")
    print(f"The full capture stays on this PC: {RAW}")
    return 0
