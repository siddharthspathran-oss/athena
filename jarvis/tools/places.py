"""Places: look a business up, phone it through your phone, or book it online.

  place_info        opening hours, "open now?", phone, address, menu, prices -
                    from a web search, answered by the local model from what
                    the results actually say.
  call_number       dials a number through Phone Link, so the call goes out on
                    your own phone and you talk on the laptop. The number can
                    be said outright, come from your contacts, or be looked
                    up ("call Domino's").
  book_reservation  hands the booking to the planner: find the place's online
                    booking page, fill in date, time and party size, and stop
                    at the final Book / Confirm button for you to say
                    "submit it".

No paid calling service and nothing that pretends to be you on the phone:
Athena dials, you speak.
"""
from __future__ import annotations

import re
import time

from ..config import cfg
from ..registry import tool

TAG = ["messaging"]

# Indian landlines (0135-2760973), mobiles (98765 43210), +91 either way.
_PHONE = re.compile(
    r"(?<![\d])(?:\+91[\s-]?|0091[\s-]?)?(?:0\d{2,4}[\s-]?\d{6,8}|[6-9]\d{4}[\s-]?\d{5}|1800[\s-]?\d{3}[\s-]?\d{4})(?![\d])")


def _city() -> str:
    return str(cfg.get("assistant.city", "Dehradun"))


def _digits(n: str) -> str:
    d = re.sub(r"[^\d+]", "", n)
    if d.startswith("0091"):
        d = "+91" + d[4:]
    # "919876543210" saved without its plus is dialled as a local number.
    if re.fullmatch(r"91[6-9]\d{9}", d):
        d = "+" + d
    return d


def _search(q: str, n: int = 6) -> str:
    from .web import web_search
    return web_search(q, n)


def find_phone(place: str) -> tuple[str, str]:
    """(number, where it came from) for a business, or ("", reason)."""
    res = _search(f"{place} {_city()} phone number contact", 6)
    if res.startswith("ERROR"):
        return "", res
    found = []
    for block in res.split("\n\n"):
        url = next((ln.strip() for ln in block.splitlines() if ln.strip().startswith("http")), "")
        if _BAD_SOURCES.search(url) or _BAD_SOURCES.search(block[:200]):
            continue
        found += [(_digits(m.group(0)), url) for m in _PHONE.finditer(block)]
    # A branch's own number over a chain's 1800 line: "call Domino's" means
    # the one down the road.
    if not found:
        # Snippets often leave the number out (Justdial hides it in the page);
        # read the top two results themselves.
        from .web import read_webpage
        urls = [ln.strip() for ln in res.splitlines() if ln.strip().startswith("http")
                and not _BAD_SOURCES.search(ln)][:2]
        for url in urls:
            try:
                page = read_webpage(url, 8000)
            except Exception:
                continue
            found += [(_digits(m.group(0)), url) for m in _PHONE.finditer(page)]
            if found:
                break
    local = [f for f in found if not f[0].startswith("1800")]
    if local or found:
        return (local or found)[0]
    return "", "no phone number in the search results"


def _contact_number(name: str) -> str:
    """A saved contact's number, understanding relations (mom -> maa)."""
    try:
        from .messaging import _find_contact
        hit = _find_contact(name)
    except Exception:
        return ""
    if not hit:
        return ""
    val = hit[1]
    return _digits((val.get("phone") if isinstance(val, dict) else str(val)) or "")


def _clean_who(who: str) -> str:
    """The name alone. Speech arrived as "mom on my phone. and" and as "maa
    on phone. And we'll see you in the next one." (25 Sep 2026) - looked up
    whole, those went to a web search and dialled strangers."""
    w = re.split(r"(?<=\w)[.!?]\s", who.strip() + " ", maxsplit=1)[0]
    w = re.sub(r"\s+(?:on|from|using|via|through)\s+(?:my\s+|the\s+)?(?:phone|mobile|cell|sim|number)\b.*$", "", w, flags=re.I)
    w = re.sub(r"[\s,.!?]+(?:and|so|but|then|please|now)?\W*$", "", w, flags=re.I)
    return w.strip(" .,!?")


# Never a source for a number to dial: adult and classified-ad sites, where a
# search for a person's name lands on strangers' numbers.
_BAD_SOURCES = re.compile(r"skokka|locanto|quikr|olx|escort|call-?girl|adult|dating|backpage|"
                          r"indiacustomercare|customer-?care-?number|helpline-?number", re.I)
_pending: dict = {}


def has_pending() -> bool:
    return bool(_pending)


def submit_pending() -> str:
    """Dial the web-found number, on the user's "call it"."""
    item = dict(_pending)
    _pending.clear()
    if not item:
        return "There's nothing waiting to be dialled."
    return _dial(item["number"], item["who"])


def cancel_pending() -> str:
    _pending.clear()
    return "OK, not calling."


@tool(tags=TAG)
def place_info(place: str, question: str = "") -> str:
    """Look up a shop, restaurant, clinic or any business: opening hours,
    whether it is open now, phone number, address, menu or prices.

    place: the business, e.g. "Domino's Rajpur Road" or "Doon Hospital"
    question: what to find out, e.g. "is it open now", "phone number", "menu prices"
    """
    q = question.strip() or "opening hours phone number address"
    res = _search(f"{place} {_city()} {q}", 6)
    if res.startswith("ERROR"):
        return res
    from ..llm import LLM

    now = time.strftime("%A %H:%M")
    msg = LLM().chat([
        {"role": "system", "content": (
            "Answer the user's question about a place using ONLY the search results given. "
            "Quote phone numbers, hours and addresses exactly as they appear. If the results "
            "do not say, say you could not find it. Two or three short sentences.")},
        {"role": "user", "content": f"It is {now} in {_city()}.\nQuestion: {place} - {q}\n\nSearch results:\n{res[:3500]}"},
    ], temperature=0.1, max_tokens=220)
    answer = (msg.content or "").strip()
    src = next((ln.strip() for ln in res.splitlines() if ln.strip().startswith("http")), "")
    return answer + (f"\n(Source: {src})" if src else "")


def _phone_link_window() -> int:
    from .. import winapi
    for h, t in winapi.list_windows():
        if t.strip().lower() in ("phone link", "link to windows") or t.lower().startswith("phone link"):
            return h
    return 0


@tool(tags=TAG, destructive=True)
def call_number(who: str) -> str:
    """Phone a number or a business through Phone Link: the call goes out on
    the user's own phone and they talk on the laptop. Use for "call Domino's",
    "call 0135 2760973", "ring the hospital". For WhatsApp calls to contacts,
    use whatsapp_call instead.

    who: a phone number, a saved contact, or a business name to look up
    """
    raw = _clean_who(who)
    if not raw:
        return "Who should I call?"
    m = _PHONE.search(raw)
    if m or re.fullmatch(r"[\d\s+()-]{7,}", raw):
        return _dial(_digits(m.group(0) if m else raw), raw)
    number = _contact_number(raw)
    if number:
        return _dial(number, raw.title())
    # A business, looked up online. Never dialled straight away: the number
    # came from a web page, so it is said first and waits for "call it".
    number, source = find_phone(raw)
    if not number:
        return f"I couldn't find a number for {raw}. Tell me the number and I'll dial it."
    host = re.sub(r"^https?://(?:www\.)?([^/]+).*$", r"\1", source)
    _pending.update(number=number, who=raw)
    return f"I found {number} for {raw} on {host}. Say 'call it' to dial, or 'no'."


def _dial(number: str, who: str) -> str:
    import os

    try:
        os.startfile(f"tel:{number}")
    except OSError as e:
        return f"Couldn't hand the number to Phone Link ({e})."
    time.sleep(3)
    hwnd = _phone_link_window()
    pressed = False
    if hwnd:
        try:
            from .. import uia
            uia.wake(hwnd, settle=0.8)
            # Phone Link names it "Call Dialled number 9 1 8 ..." - so the
            # button whose name starts with "Call " (not "Calls ...").
            for el in uia.elements(hwnd, uia.BUTTON):
                n = el.name
                if el.on_screen and (n == "Call" or n.lower().startswith("call dialled")):
                    pressed = el.press(hwnd)
                    break
        except Exception:
            pass
    try:
        from .. import live
        live.note(f"calling {who} on {number}")
    except Exception:
        pass
    if pressed and cfg.get("places.audio_on_pc", True):
        # The first test call rang with the audio on the phone and "Transfer
        # call to PC" showing; dialling from the laptop means talking on it.
        time.sleep(3)
        _press_in_call(("transfer call to pc",))
    # Said aloud, so short: the number and its source go to the HUD log.
    if pressed:
        return f"Calling {who}."
    if hwnd:
        return f"Phone Link has the number for {who} ready - press Call there."
    return "Phone Link didn't open - is it set up with your phone?"


def _press_in_call(prefixes: tuple[str, ...]) -> bool:
    """Press a button in Phone Link's call window whose name starts with one of these."""
    from .. import uia, winapi
    for h, t in winapi.list_windows():
        if "phone link" not in t.lower():
            continue
        try:
            uia.wake(h, settle=0.4)
            for e in uia.elements(h, uia.BUTTON):
                if e.on_screen and e.name.lower().startswith(prefixes):
                    return e.press(h)
        except Exception:
            continue
    return False


@tool(tags=TAG)
def end_call() -> str:
    """Hang up the phone call that is going on through Phone Link."""
    if _press_in_call(("end call", "hang up")):
        return "Call ended."
    return "I don't see a call going on in Phone Link."


@tool(tags=TAG, timeout=120)
def book_reservation(place: str, when: str, people: int = 2, notes: str = "") -> str:
    """Book a table or an appointment online: finds the place's booking page,
    fills in the details, and stops at the final Book/Confirm button for the
    user to say "submit it".

    place: the restaurant, clinic, salon or venue
    when: date and time, e.g. "tonight 8 pm", "Saturday 1 pm"
    people: party size
    notes: anything else to enter, e.g. "window seat", "birthday"
    """
    from .. import planner, profile

    about = profile.prompt_text()
    request = (
        f"Book {place} in {_city()} for {people} people, {when}."
        + (f" Notes: {notes}." if notes else "")
        + " Steps: web_search for its online table/appointment booking page. Prefer Zomato when the "
          "results have it - the user is logged in there and it works; otherwise the venue's own site, "
          "District, EazyDiner, Dineout or OpenTable. browser_open it, "
          "browser_read, fill date, time and party size, and the user's name if asked. Do NOT type a phone "
          "number, email or payment details - leave those for the user and say which fields need them. "
          "Stop when the final Book/Confirm/Reserve button is reached: it will be held for the user. "
          "Only if a login popup or login form actually blocks the booking (the date/guest/slot choices "
          "are not on the page) stop and tell the user to log in to that site once in the Athena browser "
          "window, then ask again. A 'Log in' link in the page header is NOT that - if booking choices are "
          "visible, the user is logged in: use them. Booking choices are often NOT in the numbered controls - "
          "look for them in the page text (e.g. 'Today', '1 guest', 'Dinner', '8:30 PM') and browser_click "
          "them by that exact text. Dropdowns open when clicked; read the page again to see their options "
          "and click the one you want by its text. If the exact time is not offered or cannot be clicked, "
          "pick the nearest open slot and say so. In the final reply name the branch/location, the date, "
          "time and party size that are filled in. "
          "If there is no online booking, say so and give the phone number from the results."
        + (f"\nWhat is known about the user:\n{about}" if about else ""))
    try:
        return planner.run(request, only=["web_search", "read_webpage", "browser_open", "browser_read",
                                          "browser_fill", "browser_click", "place_info"],
                           result_chars=1800,
                           # Browser tasks take a dozen steps; Groq's free
                           # per-minute budget runs dry half-way. Gemini
                           # Flash-Lite has far more headroom per minute.
                           models=cfg.get("planner.browser_models") or [
                               "gemini:gemini-3.5-flash-lite", "groq:openai/gpt-oss-120b",
                               "groq:openai/gpt-oss-20b"])
    except planner.Stuck as e:
        return f"The online planner is busy right now ({str(e)[:80]}). Try again in a minute."
