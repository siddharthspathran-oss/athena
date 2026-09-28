"""Drive a web page: read its fields and buttons, fill them in, click.

Athena gets a Chrome window of its own (profile in data/browser-profile), so
it never touches your everyday browser session, and anything you sign in to
there stays signed in for next time. Playwright drives it through the Chrome
already installed - no separate browser download.

Playwright's objects belong to the thread that made them, and Athena calls
tools from whichever thread took the request (the web server alone uses one
per request). So one worker thread owns the browser and every tool hands it
a job through a queue.

Two things it will not do, whatever the model asks:
  - type into password, card, CVV or OTP fields. Those you type yourself,
    in the same window; it tells you which field is waiting.
  - press a final submit / pay / place-order button on its own. It fills
    everything, then says what it is about to send and waits for you to say
    "submit it". Confirmations are off everywhere else by your choice, but a
    submitted form usually cannot be taken back.
"""
from __future__ import annotations

import queue
import re
import threading
from typing import Any, Callable

from ..config import DATA
from ..registry import tool

TAG = ["web"]
PROFILE = DATA / "browser-profile"

# Buttons whose press sends something off. Matched on the visible label.
# Always held: these finish something.
_FINAL = re.compile(
    r"\b(submit|pay|place\s+order|buy|purchase|checkout|check\s*out|confirm|complete|"
    r"donate|transfer|delete|remove|proceed\s+to\s+(?:cart|pay|payment|checkout)|add\s+to\s+cart)\b", re.I)
# Held only once something has been filled in. On Zomato and District the
# first "Book a table" just opens the booking form; holding it stalled a real
# booking at the front door (25 Sep 2026). The one at the end of the form,
# after the date and party size are in, is the one that matters.
_FINAL_IF_FILLED = re.compile(r"\b(book|reserve|register|sign\s*up|send|apply|request)\b", re.I)

# Fields Athena never types into.
_SECRET = re.compile(
    r"pass(word|code)?|pwd|\bpin\b|otp|one.?time|cvv|cvc|security\s*code|"
    r"card\s*(number|no)|cc-?num|\bssn\b|aadhaar|pan\s*(number|no)|\biban\b|"
    r"account\s*(number|no)|routing", re.I)

# Collects every visible control on the page and tags it with a number, so
# the model can say "fill 3" rather than guess at CSS selectors.
_SNAPSHOT_JS = r"""
() => {
  // Numbers from the last read are cleared first: left in place, a new read
  // gave the same number to a second element and the click was refused as
  // ambiguous ("resolved to 2 elements").
  document.querySelectorAll('[data-jarvis]').forEach(e => e.removeAttribute('data-jarvis'));
  const out = [];
  const seen = new Set();
  const vis = el => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const labelOf = el => {
    let t = '';
    if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) t = l.innerText; }
    if (!t && el.closest('label')) t = el.closest('label').innerText;
    t = t || el.getAttribute('aria-label') || el.getAttribute('placeholder') ||
        el.getAttribute('title') || el.getAttribute('name') || el.innerText || el.value || '';
    return t.replace(/\s+/g, ' ').trim().replace(/[:*]+$/, '').trim().slice(0, 80);
  };
  const els = document.querySelectorAll(
    'input, textarea, select, button, a[href], [role=button], [role=checkbox], [role=radio], [contenteditable=true]');
  let n = 0;
  for (const el of els) {
    if (seen.has(el) || !vis(el)) continue;
    seen.add(el);
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (type === 'hidden') continue;
    n += 1;
    el.setAttribute('data-jarvis', String(n));
    const tag = el.tagName.toLowerCase();
    let kind = tag === 'a' ? 'link' : tag === 'select' ? 'dropdown' : tag === 'textarea' ? 'textbox'
             : tag === 'button' || el.getAttribute('role') === 'button' || ['submit','button','reset'].includes(type) ? 'button'
             : type === 'checkbox' || el.getAttribute('role') === 'checkbox' ? 'checkbox'
             : type === 'radio' || el.getAttribute('role') === 'radio' ? 'radio'
             : 'textbox';
    const item = {n, kind, label: labelOf(el), type, name: el.getAttribute('name') || '',
                  autocomplete: el.getAttribute('autocomplete') || ''};
    if (kind === 'textbox') item.value = (el.value || el.innerText || '').slice(0, 60);
    if (kind === 'checkbox' || kind === 'radio') item.checked = !!el.checked;
    if (kind === 'dropdown') item.options = [...el.options].slice(0, 25).map(o => o.text.trim());
    if (kind === 'link') item.href = (el.getAttribute('href') || '').slice(0, 80);
    out.push(item);
    if (n >= 150) break;
  }
  // Clickable boxes that are not real buttons: date chips ("SAT 26 SEP"),
  // time slots ("9:00 PM"), option cards. barbequenation.com's booking panel
  // is built from these; without them the page looked like it had no dates
  // or times and the planner went round in circles (25 Sep 2026).
  if (n < 150) {
    for (const el of document.querySelectorAll('div,span,li,label,p')) {
      if (n >= 150) break;
      if (seen.has(el) || !vis(el)) continue;
      const s = getComputedStyle(el);
      if (s.cursor !== 'pointer') continue;
      if (el.closest('a,button,[role=button],[data-jarvis]')) continue;
      if (el.parentElement && getComputedStyle(el.parentElement).cursor === 'pointer'
          && !el.parentElement.closest('body > *:not(div)')) {
        const pt = (el.parentElement.innerText || '').trim();
        if (pt && pt.length < 40) continue;          // the parent is the chip; take it instead
      }
      const t = (el.innerText || '').replace(/\s+/g, ' ').trim();
      if (!t || t.length > 40) continue;
      if ([...el.querySelectorAll('*')].some(c => seen.has(c))) continue;
      seen.add(el);
      n += 1;
      el.setAttribute('data-jarvis', String(n));
      out.push({n, kind: 'button', label: t.slice(0, 60), type: '', name: '', autocomplete: '',
                selected: /active|selected|current|checked/i.test(el.className || '')});
    }
  }
  const text = (document.body ? document.body.innerText : '').replace(/\n{3,}/g, '\n\n').slice(0, 2500);
  // The text inside forms first: that is where booking choices live, and
  // on a long page they fall past the cut-off of the page text.
  const forms = [...document.forms].map(f => (f.innerText || '').replace(/\s+/g, ' ').trim())
                 .filter(Boolean).join(' | ').slice(0, 700);
  return {title: document.title, url: location.href, items: out, text, forms};
}
"""


class _Worker:
    """One thread that owns Playwright, the browser and its pages."""

    def __init__(self) -> None:
        self.jobs: queue.Queue = queue.Queue()
        self.thread: threading.Thread | None = None
        self.ctx = None
        self.page = None
        self.items: dict[int, dict] = {}
        self.pending: dict | None = None     # a final click waiting for "submit it"

    def call(self, fn: Callable[[], Any], timeout: float = 60) -> Any:
        if self.thread is None or not self.thread.is_alive():
            self.thread = threading.Thread(target=self._loop, daemon=True, name="jarvis-browser")
            self.thread.start()
        box: queue.Queue = queue.Queue(maxsize=1)
        self.jobs.put((fn, box))
        ok, value = box.get(timeout=timeout)
        if ok:
            return value
        raise value

    def _loop(self) -> None:
        from playwright.sync_api import sync_playwright

        pw = sync_playwright().start()
        while True:
            fn, box = self.jobs.get()
            try:
                if self.ctx is None or not self._alive():
                    self._launch(pw)
                box.put((True, fn()))
            except Exception as e:           # handed back to the caller's thread
                box.put((False, e))

    def _alive(self) -> bool:
        try:
            return bool(self.ctx.pages) and not self.page.is_closed()
        except Exception:
            return False

    def _launch(self, pw) -> None:
        if self.ctx is not None:
            try:
                if self.ctx.pages:           # window still open, page closed
                    self.page = self.ctx.pages[-1]
                    return
            except Exception:
                pass
        PROFILE.mkdir(parents=True, exist_ok=True)
        # No "Restore pages?" bubble after an unclean exit, and none of the
        # "unsupported command-line flag" / "controlled by automated software"
        # bars: this is the user's window too - they log in to sites in it.
        kw = dict(user_data_dir=str(PROFILE), headless=False, no_viewport=True,
                  args=["--start-maximized", "--hide-crash-restore-bubble",
                        "--disable-session-crashed-bubble"],
                  ignore_default_args=["--no-sandbox", "--enable-automation"])
        try:
            self.ctx = pw.chromium.launch_persistent_context(channel="chrome", **kw)
        except Exception:
            self.ctx = pw.chromium.launch_persistent_context(channel="msedge", **kw)
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        self.items = {}

    # ---- run inside the worker thread ----

    def snapshot(self) -> dict:
        p = self.ctx.pages[-1]
        self.page = p
        p.wait_for_load_state("domcontentloaded")
        snap = p.evaluate(_SNAPSHOT_JS)
        self.items = {i["n"]: i for i in snap["items"]}
        return snap

    def locate(self, which: str):
        """A control by its number from the last read, or by its label."""
        which = str(which).strip()
        if which.isdigit() and int(which) in self.items:
            return self.page.locator(f'[data-jarvis="{int(which)}"]').first, self.items[int(which)]
        low = which.lower()
        for i in self.items.values():
            if i["label"].lower() == low:
                return self.page.locator(f'[data-jarvis="{i["n"]}"]').first, i
        for i in self.items.values():
            if low and low in i["label"].lower():
                return self.page.locator(f'[data-jarvis="{i["n"]}"]').first, i
        # Not a numbered control: click whatever shows exactly this text.
        # Zomato's date/guest dropdowns and time slots are bare <div>s with
        # script handlers - no role, no tabindex, not even cursor:pointer -
        # so no scan finds them, but "8:30 PM" is right there on the page.
        variants = [which]
        m = re.fullmatch(r"\s*(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?\s*", which, re.I)
        if m:
            # Sites write times their own way: "8:00 PM" on Zomato, "08:00 PM",
            # "8 PM" or "20:00" elsewhere. EazyDiner's slots were missed for it.
            h, mi, ap = int(m.group(1)), m.group(2) or "00", m.group(3).upper()
            h24 = h % 12 + (12 if ap == "P" else 0)
            variants += [f"{h}:{mi} {ap}M", f"{h:02d}:{mi} {ap}M", f"{h}:{mi} {ap.lower()}m",
                         f"{h:02d}:{mi} {ap.lower()}m", f"{h}:{mi}{ap}M", f"{h} {ap}M", f"{h}{ap}M",
                         f"{h24:02d}:{mi}", f"{h24}:{mi}"]
        for exact, text in [(e, v) for e in (True, False) for v in dict.fromkeys(variants)]:
            loc = self.page.get_by_text(text, exact=exact)
            try:
                count = loc.count()
            except Exception:
                count = 0
            for i in range(min(count, 6)):
                cand = loc.nth(i)
                try:
                    if cand.is_visible():
                        return cand, {"n": -1, "kind": "button", "label": text, "type": ""}
                except Exception:
                    continue
        raise LookupError(f"nothing called '{which}' on the page - read the page again "
                          f"(browser_read) and use a number, or text exactly as the page shows it")


_W = _Worker()


def _describe(snap: dict, text: bool = True) -> str:
    lines = [f"Page: {snap['title']} ({snap['url']})"]
    if snap.get("forms"):
        lines.append(f"Form text (click these by exact text if not numbered): {snap['forms']}")
    lines.append("Controls (use the number):")
    for i in snap["items"]:
        bits = f"[{i['n']}] {i['kind']} '{i['label']}'"
        if i["kind"] == "textbox":
            secret = _is_secret(i)
            bits += " (you type this one)" if secret else (f" = '{i['value']}'" if i.get("value") else " (empty)")
        elif i["kind"] in ("checkbox", "radio"):
            bits += " [x]" if i.get("checked") else " [ ]"
        elif i["kind"] == "dropdown":
            bits += " options: " + " | ".join(i.get("options", [])[:12])
        elif i.get("selected"):
            bits += " (selected)"
        lines.append(bits)
    if text and snap.get("text"):
        lines += ["", "Page text:", snap["text"][:1500]]
    return "\n".join(lines)


def _is_secret(item: dict) -> bool:
    if item.get("type") == "password":
        return True
    blob = " ".join(str(item.get(k, "")) for k in ("label", "name", "autocomplete"))
    return bool(_SECRET.search(blob)) or item.get("autocomplete", "").startswith("cc-")


def _err(e: Exception) -> str:
    msg = str(e).split("\n")[0]
    if "Executable doesn't exist" in msg or "chrome" in msg.lower() and "not found" in msg.lower():
        return "ERROR: could not start Chrome for Athena. Is Google Chrome installed?"
    return f"ERROR: {msg[:200]}"


# ---------------------------------------------------------------- tools

@tool(tags=TAG)
def browser_open(url: str) -> str:
    """Open a website in Athena's own browser window, to read it or fill in a form.

    Use this (not open_url) when the user wants Athena to DO something on a
    site: fill a form, click through pages, read what a page says.

    url: the address, e.g. "forms.gle/abc" or "https://lms.upes.ac.in"
    """
    if not re.match(r"^[a-z]+://", url.strip(), re.I):
        url = "https://" + url.strip()

    def job():
        _W.page.goto(url, wait_until="domcontentloaded", timeout=45000)
        try:
            _W.page.wait_for_load_state("networkidle", timeout=6000)
        except Exception:
            pass
        _W.page.bring_to_front()
        return _describe(_W.snapshot())
    try:
        return _W.call(job, timeout=70)
    except Exception as e:
        return _err(e)


@tool(tags=TAG)
def browser_read() -> str:
    """Read the page open in Athena's browser: its text and every field and button, numbered.

    Call this before filling or clicking, and again after the page changes.
    """
    try:
        return _W.call(lambda: _describe(_W.snapshot()))
    except Exception as e:
        return _err(e)


@tool(tags=TAG)
def browser_fill(field: str, value: str) -> str:
    """Type into a text box, pick a dropdown option, or tick a checkbox/radio on the page.

    field: the control's number from browser_read, or its label
    value: text to type; the option to choose; or "yes"/"no" for a checkbox
    """
    def job():
        if not _W.items:
            _W.snapshot()
        try:
            loc, item = _W.locate(field)
        except LookupError:
            # "Pizza Size: Medium" - a radio group's name is a caption, not
            # a control; the option itself carries the label. Pick it.
            loc, item = _W.locate(value)
            if item["kind"] not in ("radio", "checkbox"):
                raise
            loc.check(timeout=5000)
            return f"Chose '{item['label']}'."
        if _is_secret(item):
            return (f"I don't type passwords, PINs, OTPs or card details. "
                    f"'{item['label']}' is waiting for you in the Athena browser window.")
        kind = item["kind"]
        if kind == "dropdown":
            try:
                loc.select_option(label=value, timeout=5000)
            except Exception:
                opts = item.get("options", [])
                best = next((o for o in opts if value.lower() in o.lower()), None)
                if best is None:
                    return f"ERROR: '{value}' is not one of: {', '.join(opts[:15])}"
                loc.select_option(label=best, timeout=5000)
                value_set = best
            else:
                value_set = value
            return f"Chose '{value_set}' in '{item['label']}'."
        if kind in ("checkbox", "radio"):
            want = value.strip().lower() not in ("no", "false", "off", "untick", "uncheck", "0")
            (loc.check if want else loc.uncheck)(timeout=5000)
            return f"{'Ticked' if want else 'Unticked'} '{item['label']}'."
        loc.fill(value, timeout=8000)
        return f"Typed into '{item['label']}'."
    try:
        return _W.call(job)
    except Exception as e:
        return _err(e)


@tool(tags=TAG)
def browser_click(target: str) -> str:
    """Click a button or link on the page in Athena's browser.

    A final button - submit, pay, place order, send, register - is not pressed
    straight away: Athena tells the user what is filled in and waits for them
    to say "submit it".

    target: the number from browser_read, or the exact text shown on the page (e.g. "8:30 PM", "Tomorrow")
    """
    def job():
        if not _W.items:
            _W.snapshot()
        loc, item = _W.locate(target)
        label = item["label"] or ""
        filled = [f"{i['label']}: {i.get('value') or ('ticked' if i.get('checked') else '')}"
                  for i in _W.snapshot()["items"]
                  if (i.get("value") or i.get("checked")) and not _is_secret(i)]
        if item["n"] > 0:
            loc = _W.page.locator(f'[data-jarvis="{item["n"]}"]').first  # re-read above renumbers
        if item["kind"] in ("button", "link") and (
                _FINAL.search(label)
                or (_FINAL_IF_FILLED.search(label) and filled)
                or (item.get("type") == "submit" and filled)):
            _W.pending = {"n": item["n"], "label": item["label"], "url": _W.page.url}
            summary = "; ".join(f for f in filled if f.split(": ", 1)[1])[:600] or "nothing filled in"
            return (f"Ready to press '{item['label']}'. Filled in: {summary}. "
                    f"Say 'submit it' to send, or tell me what to change.")
        try:
            loc.click(timeout=4000)
        except Exception:
            # Covered by an open dropdown or a sticky bar: "1 guest" timed out
            # behind Zomato's still-open date list. Click it directly.
            loc.evaluate("e => e.click()")
        try:
            _W.page.wait_for_load_state("domcontentloaded", timeout=8000)
        except Exception:
            pass
        return f"Clicked '{item['label']}'. Now on: {_W.page.title()}"
    try:
        return _W.call(job)
    except Exception as e:
        return _err(e)


def has_pending() -> bool:
    return _W.pending is not None


def submit_pending() -> str:
    """Press the final button that browser_click held back. Called only from
    the user's own "submit it" - never exposed to the model as a tool."""
    pend = _W.pending
    if pend is None:
        return "There's nothing waiting to be submitted."

    def job():
        _W.pending = None
        if _W.page.url != pend["url"]:
            return "The page has changed since then, so I did not submit. Tell me again."
        loc = (_W.page.locator(f'[data-jarvis="{pend["n"]}"]') if pend["n"] > 0
               else _W.page.get_by_text(pend["label"], exact=True).first)
        if loc.count() == 0:
            return "That button is no longer on the page, so I did not submit."
        loc.click(timeout=8000)
        try:
            _W.page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:
            pass
        return f"Pressed '{pend['label']}'. Now on: {_W.page.title()}"
    try:
        return _W.call(job)
    except Exception as e:
        return _err(e)


def cancel_pending() -> str:
    _W.pending = None
    return "OK, not submitting."
