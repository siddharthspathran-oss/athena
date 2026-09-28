"""Send WhatsApp messages and compose emails.

Neither service offers a usable local API, so:

  WhatsApp - the whatsapp:// protocol opens the right chat with the message
             already typed, then a keystroke sends it. This drives the real
             app, so it is inherently more fragile than a real API would be.
  Email    - mailto: opens your mail client with everything filled in. Nothing
             is ever sent without you pressing send, which is deliberate.

Names like "papa" mean nothing to a computer, so both resolve against a
contact book you build up in data/contacts.json.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.parse
from difflib import SequenceMatcher

from .. import uia, winapi
from ..config import DATA
from ..store import load_json, save_json
from ..registry import tool

TAG = ["messaging"]

CONTACTS_FILE = DATA / "contacts.json"


# ---------- contact book ----------

def _load() -> dict[str, dict]:
    """Contacts, or {} if none saved yet. Raises StoreError on corruption."""
    return load_json(CONTACTS_FILE, {})


def _save(data: dict[str, dict]) -> None:
    save_json(CONTACTS_FILE, data)


# The same person gets called several things, and none of them are stored.
_RELATIONS = {
    "maa": ("mom", "mum", "mummy", "mother", "mama", "amma", "ma"),
    "papa": ("dad", "daddy", "father", "pop", "pa", "baba"),
    "me": ("myself", "my own", "self"),
}


def _find_contact(name: str) -> tuple[str, dict] | None:
    """Resolve a spoken name to a contact, tolerating mishearing."""
    contacts = _load()
    if not contacts:
        return None
    want = name.strip().lower()

    # "email mom" should reach maa even though nothing is stored under "mom".
    for canonical, aliases in _RELATIONS.items():
        if want in aliases and canonical in contacts:
            return canonical, contacts[canonical]

    for key, val in contacts.items():
        if key.lower() == want:
            return key, val
    for key, val in contacts.items():
        if want in key.lower() or key.lower() in want:
            return key, val

    best, score = None, 0.0
    for key, val in contacts.items():
        s = SequenceMatcher(None, want, key.lower()).ratio()
        if s > score:
            best, score = (key, val), s
    return best if score >= 0.72 else None


def _normalise_phone(phone: str) -> str:
    """Digits only, which is what the whatsapp: protocol expects."""
    return re.sub(r"[^\d]", "", phone)


@tool(tags=TAG)
def add_contact(name: str, phone: str = "", email: str = "") -> str:
    """Save a person's email address or phone number.

    Use this for any "save my email", "save papa's number" or "her address is"
    request - including the user's own details, where the name is "me". This
    is the only store the WhatsApp and email tools read.

    name: what you call them, for example papa, maa, Rohit, or me
    phone: their number with country code, for example 919876543210
    email: their email address
    """
    if not phone and not email:
        return "ERROR: give a phone number, an email address, or both."

    contacts = _load()
    # Match an existing entry regardless of case, otherwise saying the same
    # name twice creates "PAPA" and "papa" as separate contacts - and the
    # stale one can shadow the correct number at lookup time.
    existing = next((k for k in contacts if k.lower() == name.strip().lower()), None)
    if existing and existing != name:
        contacts[name] = contacts.pop(existing)
    name = existing if existing == name else name

    entry = contacts.get(name, {})
    if phone:
        digits = _normalise_phone(phone)
        if len(digits) < 10:
            return f"ERROR: '{phone}' does not look like a phone number."
        if len(digits) <= 10:
            # wa.me needs a country code; a bare local number silently routes
            # to the wrong country rather than failing, so refuse it here.
            return (f"ERROR: '{phone}' has no country code, so WhatsApp cannot route it. "
                    f"For India that would be 91{digits}.")
        entry["phone"] = digits
    if email:
        # Speech mangles addresses badly - "x@gmail.com" comes back as
        # "richy.sungive.rush" - so reject anything malformed loudly and send
        # the user to an interface where they can type.
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", email.strip()):
            return (f"ERROR: '{email}' is not a valid email address. Speech cannot "
                    f"reliably capture addresses - type it instead, on your phone "
                    f"page or with Ctrl+Alt+Space.")
        entry["email"] = email.strip()
    contacts[name] = entry
    _save(contacts)

    bits = [f"phone {entry['phone']}"] if entry.get("phone") else []
    if entry.get("email"):
        bits.append(f"email {entry['email']}")
    return f"Saved {name}: {', '.join(bits)}."


@tool(tags=TAG)
def list_contacts() -> str:
    """List everyone in the contact book."""
    contacts = _load()
    if not contacts:
        return ("The contact book is empty. Add someone with add_contact, "
                "for example: add_contact('papa', phone='919876543210').")
    rows = []
    for name, v in sorted(contacts.items()):
        bits = [v[k] for k in ("phone", "email") if v.get(k)]
        rows.append(f"{name}: {', '.join(bits)}")
    return "\n".join(rows)


@tool(tags=TAG, destructive=True)
def remove_contact(name: str) -> str:
    """Delete someone from the contact book.

    name: the contact to remove
    """
    contacts = _load()
    hit = _find_contact(name)
    if not hit:
        return f"No contact called {name}."
    del contacts[hit[0]]
    _save(contacts)
    return f"Removed {hit[0]}."


# ---------- whatsapp ----------

# The app's own window, not a browser sitting on whatsapp.com. Matching the
# substring counted "api.whatsapp.com/send/... - Opera" as success, so this
# reported the chat was open in WhatsApp about eight seconds before it was,
# and focused the browser instead.
_WA_WINDOW = re.compile(r"^(?:\(\d+\)\s*)?whatsapp(?:\s+business)?$", re.I)


def _wa_hwnd() -> int | None:
    """The WhatsApp window that can actually be driven.

    The app puts up two matching windows - "WhatsApp" and "(11) WhatsApp
    Business" - and only one of them accepts the foreground. Picking whichever
    the enumeration returned first meant every click silently went nowhere,
    so the one that can be raised is the one we use.
    """
    cands = [h for h, t in winapi.list_windows() if _WA_WINDOW.match(t.strip())]
    for hwnd in cands:
        if winapi.focus_hwnd(hwnd):
            return hwnd
    return cands[0] if cands else None


def _wait_for_whatsapp(seconds: float = 20.0) -> bool:
    """Wait until the WhatsApp app window exists, then bring it forward."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        for _hwnd, title in winapi.list_windows():
            if _WA_WINDOW.match(title.strip()):
                winapi.focus_window(title.strip())
                return True
        time.sleep(0.5)
    return False


def parse_request(utterance: str) -> tuple[str, str] | None:
    """Read a spoken WhatsApp request as (to, text), or None if it isn't one.

    Speech mangles imperatives into statements - "send a message saying hello
    to Riya" came back as "I was just saying hello to riya on WhatsApp" and
    the model read it as a remark and did nothing. Rather than hope for a
    clean transcript, this looks for the two things that actually matter: a
    contact we know, and something to say. Both must be present, so ordinary
    talk about WhatsApp does not turn into a message.
    """
    if not utterance:
        return None
    low = utterance.lower().strip()

    # "what did riya say" is a question about a message, not one to send.
    if re.match(r"^\W*(what|who|when|where|why|how|did|does|do|is|are|was|"
                r"were|has|have|can|could|should|would)\b", low):
        return None

    # "tell me ..." is how you ask Athena something, not how you send a
    # message. With a contact saved under your own name it parsed as "send a
    # WhatsApp to me", so "tell me a joke" and "tell me what my pc is running"
    # both silently opened a chat and typed the question into it.
    #
    # "message me" and "send me a whatsapp" still work: those name the channel,
    # so they really are asking for a message.
    if re.match(r"^\W*(?:(?:athena|jarvis)\W+)?(?:please\s+)?tell\s+(?:me|myself)\b", low) \
            and not re.search(r"\b(whats ?app|message|msg|text)\b", low):
        return None

    wants_message = bool(re.search(
        r"\b(whats ?app|message|msg|text|tell|send|say|saying)\b", low))
    if not wants_message:
        return None

    # The recipient is the reliable half: match against the contact book
    # rather than trying to parse a name out of the sentence.
    contacts = _load()
    names: list[str] = []
    for key in contacts:
        names.append(key.lower())
        for canonical, aliases in _RELATIONS.items():
            if canonical == key.lower():
                names.extend(aliases)
    hit = next((n for n in sorted(names, key=len, reverse=True)
                if re.search(r"\b" + re.escape(n) + r"\b", low)), None)
    if not hit:
        return None

    body = ""
    for pat in (r"\bsay(?:ing|s)?\s+(?:that\s+)?(.+)$",
                r"\bthat\s+says?\s+(.+)$",
                r"\bmessage\s+(?:to\s+\S+\s+)?(.+)$",
                r"\btell(?:ing)?\s+\S+\s+(.+)$",
                r"\"([^\"]+)\""):
        m = re.search(pat, utterance, re.I)
        if m:
            body = m.group(1)
            break
    if not body:
        # "whatsapp riya hello" - whatever follows the name
        m = re.search(re.escape(hit) + r"\b[\s,:-]*(.+)$", utterance, re.I)
        body = m.group(1) if m else ""

    # The recipient and the channel often trail the message itself.
    body = re.sub(r"\bto\s+" + re.escape(hit) + r"\b", " ", body, flags=re.I)
    body = re.sub(r"\bon\s+whats ?app\b", " ", body, flags=re.I)
    body = re.sub(r"\bover\s+whats ?app\b", " ", body, flags=re.I)
    body = re.sub(r"\bplease\b", " ", body, flags=re.I)
    body = re.sub(r"\s+", " ", body).strip(" .,!?:-\"'")

    if not body or body.lower() == hit:
        return None

    # Answer with the name the contact book actually uses, so the step the
    # user sees says "maa" rather than whichever word they happened to say.
    for canonical, aliases in _RELATIONS.items():
        if hit in aliases and canonical in contacts:
            return canonical, body
    return hit, body


def _chat_header(items) -> str:
    """Name of the conversation currently open, as WhatsApp displays it."""
    best = ""
    for el in items:
        if not el.on_screen or el.control_type != 50000:
            continue
        left, top, _r, _b = el.rect
        # The header sits above the messages and right of the chat list.
        if 30 < top < 110 and 450 < left < 1650 and el.name:
            if el.name.lower() not in ("profile details", "lists", "menu"):
                if not best or left < 700:
                    best = el.name
    return best


def _same_person(contact: str, header: str) -> bool:
    """Is the open chat the contact we asked for?

    WhatsApp shows whatever the other person calls themselves - "riya" is
    "Massa And The Bear" - so an exact match would refuse every real call.
    A token that is close to the contact name is enough to proceed; anything
    less and we do not dial.
    """
    c = contact.strip().lower()
    h = header.strip().lower()
    if not c or not h:
        return False
    if c in h:
        return True
    return any(SequenceMatcher(None, c, word).ratio() >= 0.75
               for word in re.split(r"[\s,._-]+", h) if word)


def _ensure_whatsapp(seconds: float = 30.0) -> int | None:
    """The WhatsApp app window, launching the app if it is not showing."""
    hwnd = _wa_hwnd()
    if hwnd is not None:
        return hwnd
    try:
        subprocess.Popen(["explorer.exe", "whatsapp://"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        return None
    deadline = time.time() + seconds
    while time.time() < deadline:
        time.sleep(1.0)
        hwnd = _wa_hwnd()
        if hwnd is not None:
            return hwnd
    return None


# Where the fixed furniture sits, remembered per window size. Finding the
# Chats tab and the search box cost a tree walk each - about nine seconds of
# every call - to locate controls that never move unless the window is
# resized. The key includes the window rectangle, so a resize invalidates it.
_UI_POS: dict[tuple, tuple[int, int]] = {}

# Which conversation this last opened, per window.
_LAST_CHAT: dict[int, str] = {}


def _click_fixed(hwnd: int, label: str, find) -> bool:
    """Click a control that stays put, looking it up only the first time."""
    key = (winapi.window_rect(hwnd), label)
    point = _UI_POS.get(key)
    if point is not None:
        winapi.focus_hwnd(hwnd)
        return winapi.click_input(*point)

    el = find()
    if el is None:
        return False
    left, top, right, bottom = el.rect
    if left < 0 or top < 0:
        return False
    _UI_POS[key] = ((left + right) // 2, (top + bottom) // 2)
    return el.press(hwnd)


def _pick_row(hwnd: int, label: str):
    """Choose the search result that belongs to this contact.

    Taking the topmost row was wrong: searching papa's number opened a
    lecture group instead. The row carries the person's WhatsApp name, so it
    can be checked before it is clicked rather than after - and checking
    before means a wrong guess never opens the wrong conversation at all.

    Rows are DataItems, not Buttons. Asking for buttons returned only
    "Clear all" and nothing was selectable.
    """
    # "Chats", "Contacts" and "Messages" are section headings in the results,
    # not people. Falling back to the topmost row would otherwise pick one.
    headings = {"chats", "contacts", "messages", "groups", "status", "calls"}
    rows = [e for e in uia.elements(hwnd, uia.DATA_ITEM)
            if e.on_screen and e.name and e.rect[0] < 450 and e.rect[1] > 160
            and e.name.strip().lower() not in headings]
    seen: set[tuple[str, int]] = set()
    ordered = []
    for e in sorted(rows, key=lambda e: (e.rect[1], e.rect[0])):
        key = (e.name, e.rect[1])
        if key in seen:
            continue
        seen.add(key)
        ordered.append(e)

    named = next((e for e in ordered if _same_person(label, e.name)), None)
    return named or (ordered[0] if ordered else None)


def _open_chat(hwnd: int, number: str, label: str) -> str:
    """Open a contact's conversation inside the app. Returns the header.

    Driving the app beats handing the number to wa.me: that route goes
    through the browser, and when WhatsApp Web is signed in there the chat
    opens in Opera and the desktop app never appears at all. Searching by
    number rather than by name matters too - the contact saved as "riya"
    shows up in WhatsApp as "Massa And The Bear".
    """
    if not winapi.focus_hwnd(hwnd):
        return "", []
    uia.wake(hwnd, settle=0.6)

    # Checking "is this chat already open?" costs a full tree walk, and it
    # only pays off when the answer is yes. Ask only when the last chat this
    # opened was this person; otherwise go straight to searching.
    if _LAST_CHAT.get(hwnd) == label:
        buttons = uia.elements(hwnd, uia.BUTTON)
        header = _chat_header(buttons)
        if header and _same_person(label, header):
            return header, buttons      # already the right conversation

    # Whatever tab WhatsApp was left on, start from Chats: the search box and
    # the result rows belong to that tab, and a call attempt that began on the
    # Calls tab found neither and gave up without dialling.
    _click_fixed(hwnd, "Chats", lambda: uia.find_button(hwnd, "Chats"))
    time.sleep(0.8)

    winapi.press_vk(0x1B)      # Escape, in case a search is already open
    time.sleep(0.4)
    # Matched by position, not by name: the box is called "Search or start a
    # new chat" only while it is empty. Once anything has been typed into it
    # the name goes, and requiring one found nothing at all.
    def find_box():
        return next((e for e in uia.elements(hwnd, uia.EDIT)
                     if e.on_screen and e.rect[0] < 450 and e.rect[1] < 200), None)

    if not _click_fixed(hwnd, "search", find_box):
        return "", []
    # Clear whatever a previous search left behind.
    winapi.press_keys(0x11, ord("A"))
    time.sleep(0.2)
    winapi.press_vk(0x2E)      # Delete
    # The box needs about a second after being cleared before it will accept
    # typing. Trimming this to 0.4s to save time made calls fail outright:
    # the number went nowhere, the search never filtered, and no chat opened.
    time.sleep(1.1)
    winapi.type_text(number)
    time.sleep(2.5)

    uia.wake(hwnd, settle=0.6)
    row = _pick_row(hwnd, label)
    if row is None or not row.press(hwnd):
        return "", []
    # Identity was already settled before the click: _pick_row only chose this
    # row because its name matched the contact. Re-reading the header four
    # times to confirm what we knew cost four tree walks, about twenty
    # seconds. One walk now, and the row's own name stands in when the header
    # has not painted yet.
    # The header takes a variable moment to paint after the row is clicked -
    # long enough that reading it once made calls to some contacts fail every
    # time. Poll instead of guessing a single delay.
    buttons: list = []
    header = ""
    for attempt in range(3):
        time.sleep(2.0 if attempt == 0 else 1.2)
        uia.wake(hwnd, settle=0.4)
        buttons = uia.elements(hwnd, uia.BUTTON)
        header = _chat_header(buttons)
        if header and _same_person(label, header):
            _LAST_CHAT[hwnd] = label
            return header, buttons

    if not header and _same_person(label, row.name):
        header = row.name
    return header, buttons


# What appears once a call is actually under way. Checked after pressing,
# because a button that was pressed and a call that started are not the same
# thing and the user should never be told the second when only the first is true.
_IN_CALL = ("End call", "Mute", "Unmute", "Turn off camera",
            "Turn on camera", "Speaker", "Hang up")


@tool(tags=TAG, destructive=True)
def whatsapp_call(to: str, video: bool = False) -> str:
    """Place a WhatsApp voice or video call to a contact.

    Opens their chat, checks the header really is that person, then presses
    the call button. The press is a click at the coordinates UI Automation
    reports for the named button - the Invoke pattern claims success on these
    web-view controls without doing anything, so it cannot be trusted.

    The window has to be foregrounded first or the click lands on whatever is
    in front instead. If any step cannot be confirmed, it stops and leaves
    the call to you rather than risk ringing the wrong person.

    to: contact name, or a full phone number with country code
    video: true for a video call, false for a voice call
    """
    resolved = _resolve_recipient(to)
    if isinstance(resolved, str):
        return resolved
    number, label = resolved
    kind = "Video call" if video else "Voice call"

    hwnd = _ensure_whatsapp()
    if hwnd is None:
        return "ERROR: could not open the WhatsApp app."

    header, buttons = _open_chat(hwnd, number, label)
    if not header:
        # Opening the chat fails occasionally - a click landing while the app
        # is still redrawing. Retrying costs time only when it went wrong,
        # which is a better trade than padding every call with longer waits.
        time.sleep(1.0)
        header, buttons = _open_chat(hwnd, number, label)

    if not header:
        return (f"Opened {label}'s chat but could not read who is on screen, "
                f"so I did not call. Press the {kind.lower()} button yourself.")
    if not _same_person(label, header):
        return (f"Did not call: the chat on screen says \"{header}\", which I "
                f"cannot match to {label}. Press the button yourself if that "
                f"is right.")

    button = uia.find_button(buttons, kind)
    if button is None:
        return (f"{header}'s chat is open but there is no {kind.lower()} "
                f"button - group chats and some contacts do not offer one.")

    if not button.press(hwnd):
        return (f"{header}'s chat is open but I could not press the "
                f"{kind.lower()} button. Press it yourself.")

    # Pressed is not the same as ringing. Nothing touches the window between
    # locating the button and pressing it - an element sweep in that gap is
    # seconds of work against a live UI, and the press is what matters.
    # One check, not two: each is a full walk of the window's controls.
    time.sleep(1.6)
    try:
        names = {e.name.lower() for e in uia.elements(hwnd, uia.BUTTON)
                 if e.on_screen and e.name}
        if any(any(m.lower() in n for m in _IN_CALL) for n in names):
            return f"Calling {header}."
    except Exception:
        pass
    return (f"Pressed the {kind.lower()} button for {header}, but could not "
            f"confirm the call started - check the screen.")


# Anchored deliberately: "call riya" is an instruction, "call me later" is
# not, and "me" is a contact. Nothing may trail the name except the channel.
_CALL_CMD = re.compile(
    r"^\W*(?:(?:athena|jarvis|and|um|uh|so|now|then|just|please|ok|okay|hey)\W+)*"
    r"(?P<video>video\s+|voice\s+)?"
    # "call" reliably loses its consonant: heard as "oll" and "all in" in one
    # session. Safe to be generous because the name after it still has to be
    # a contact we know, exactly.
    r"(?:call|ring|phone|dial|oll|all|aul|awl|cool|coal|col|kol|carl|karl|gall)"
    # Speech punctuates: "Oll, riya." needs the comma to count as a gap.
    r"[\s,]+(?:up\s+|to\s+|in\s+|on\s+)?"
    r"(?P<name>[a-z][a-z .'-]{0,30}?)"
    r"(?:\s+(?:on|over|via|through)\s+whats\s?app)?"
    r"(?:\s+(?:for|on)\s+a?\s*(?:video|voice)\s*call)?"
    r"\W*$",
    re.I,
)


def parse_call_request(utterance: str) -> tuple[str, bool] | None:
    """Read a spoken call request as (to, video), or None if it isn't one."""
    if not utterance:
        return None
    m = _CALL_CMD.match(utterance.strip())
    if not m:
        return None
    name = (m.group("name") or "").strip().lower()
    if not name:
        return None

    # _find_contact matches on substrings, which is right when the model has
    # already decided this is a recipient but wrong here: "call me later"
    # resolves to the "me" contact and opens a chat. Placing a call needs the
    # name to be one we actually know, exactly.
    contacts = _load()
    known = set(contacts)
    for canonical, aliases in _RELATIONS.items():
        if canonical in contacts:
            known.update(aliases)
    if name not in known:
        return None

    return name, bool((m.group("video") or "").strip().lower() == "video")


def _resolve_recipient(to: str) -> tuple[str, str] | str:
    """(number, label) for a name or raw number, or an ERROR string."""
    digits = _normalise_phone(to)
    if len(digits) >= 10:
        return digits, to

    hit = _find_contact(to)
    if not hit:
        known = ", ".join(sorted(_load())) or "nobody yet"
        return (f"ERROR: no contact called '{to}'. Known contacts: {known}. "
                f"Add them with add_contact.")
    label, entry = hit
    number = entry.get("phone", "")
    if not number:
        return f"ERROR: {label} has no phone number saved. Add one with add_contact."
    return number, label


@tool(tags=TAG)
def whatsapp_message(to: str, text: str) -> str:
    """Send a WhatsApp message to a contact.

    Runs entirely in the WhatsApp app. The old route handed the number to
    wa.me, which goes through the browser - with WhatsApp Web signed in there
    the chat opened in Opera and the desktop app never appeared.

    Before typing a word it checks two things name the right person: the chat
    header, and the message box itself, which WhatsApp labels "Type a message
    to <name>". Only then does it type and press Send.

    to: contact name, or a full phone number with country code
    text: the message to send
    """
    if not text.strip():
        return "ERROR: the message is empty."

    resolved = _resolve_recipient(to)
    if isinstance(resolved, str):
        return resolved
    number, label = resolved

    hwnd = _ensure_whatsapp()
    if hwnd is None:
        return "ERROR: could not open the WhatsApp app."

    header, _buttons = _open_chat(hwnd, number, label)
    if not header:
        time.sleep(1.0)
        header, _buttons = _open_chat(hwnd, number, label)
    if not header:
        return (f"Could not open {label}'s chat, so nothing was sent.")
    if not _same_person(label, header):
        return (f"Did not send: the chat on screen says \"{header}\", which I "
                f"cannot match to {label}.")

    box = next((e for e in uia.elements(hwnd, uia.EDIT)
                if e.on_screen and e.name.lower().startswith("type a message")), None)
    if box is None:
        return (f"{header}'s chat is open but I could not find the message "
                f"box, so nothing was sent.")

    # The box names its own recipient. Checking it costs nothing and catches
    # the case where the header was read from a chat that has since changed.
    # Compare it against the header just verified, not against the nickname:
    # the contact saved as "me" is "Siddharth" in WhatsApp, and matching the
    # nickname refused to send to a chat that was demonstrably the right one.
    named = box.name[len("Type a message to"):].strip(" :")
    if named and not (_same_person(label, named) or _same_person(named, header)):
        return (f"Did not send: the message box is addressed to \"{named}\", "
                f"but the chat header says \"{header}\".")

    if not box.press(hwnd):
        return f"Could not click the message box in {header}'s chat."
    time.sleep(0.5)
    if not winapi.type_text(text):
        return f"Could not type the message into {header}'s chat."
    time.sleep(0.6)

    send = uia.find_button(hwnd, "Send")
    if send is None or not send.press(hwnd):
        return (f"Typed the message to {header} but could not press Send - "
                f"press it yourself.")

    # Sent means the box is empty again and the text is in the conversation.
    for _ in range(6):
        time.sleep(0.8)
        uia.wake(hwnd, settle=0.3)
        shown = {e.name for e in uia.elements(hwnd, uia.DATA_ITEM)
                 if e.on_screen and e.name}
        if any(text.strip() in n for n in shown):
            return f"Sent to {label}: \"{text}\""
    return (f"Pressed Send for {label} but could not confirm the message "
            f"appeared - check the chat.")


# ---------- email ----------

@tool(tags=TAG)
def compose_email(to: str, subject: str = "", body: str = "") -> str:
    """Write a NEW email to someone, ready to review and send.

    Only for actually writing a message to a named person. To just launch the
    mail application, use open_app instead.

    It never sends on its own - you press send.

    to: contact name, or an email address
    subject: the subject line
    body: the message body
    """
    if "@" in to:
        address, label = to, to
    else:
        hit = _find_contact(to)
        if not hit:
            known = ", ".join(sorted(_load())) or "nobody yet"
            return (f"ERROR: no contact called '{to}'. Known contacts: {known}. "
                    f"Add them with add_contact, or give a full email address.")
        label, entry = hit
        address = entry.get("email", "")
        if not address:
            return f"ERROR: {label} has no email address saved. Add one with add_contact."

    params = {}
    if subject:
        params["subject"] = subject
    if body:
        params["body"] = body
    query = ("?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)) if params else ""
    uri = f"mailto:{address}{query}"

    # os.startfile hands the URI to its registered protocol handler, which is
    # what actually opens a compose window. Going through explorer.exe passes
    # mailto: as a command-line argument instead, and Explorer discards it -
    # no window, no error, nothing drafted.
    import os

    before = {t for _h, t in winapi.list_windows()}
    try:
        os.startfile(uri)
    except Exception as e:
        return f"ERROR: could not open your mail app: {e}"

    # Confirm a draft window actually appeared rather than assuming it did.
    for _ in range(16):
        time.sleep(0.75)
        if {t for _h, t in winapi.list_windows()} - before:
            return (f"Draft to {label} ({address}) is open. "
                    f"Review it and press send - I do not send email on your behalf.")
    return (f"Asked your mail app to draft a message to {label} ({address}), but no "
            f"compose window appeared. Open Outlook and try again, or check that it "
            f"is set as the default mail app.")


# ---------- phoning the user themselves ----------

@tool(tags=TAG)
def call_me(message: str = "") -> str:
    """Phone the user's own mobile and read a message aloud, with a push too.

    Use this when the user asks to be called ("call me", "ring my phone"),
    not for calling a contact - that is whatsapp_call. The message goes
    through outside call and push services, so keep it short and leave out
    anything private.

    message: what to say on the call; leave empty for a test call
    """
    from .. import alerts, phone

    return alerts.call_now(message.strip() or phone.TEST_MESSAGE)
