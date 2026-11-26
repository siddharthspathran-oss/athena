"""The contact book: names, nicknames and relations ("Papa", "Maa"), phone numbers,
and finding the one meant - plus the tools to add, look up, list and remove them.

Split out of messaging.py (31 Oct 2026); messaging.py imports all of it back.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from ..config import DATA
from ..store import load_json, save_json
from ..registry import tool


TAG = ["messaging"]
CONTACTS_FILE = DATA / "contacts.json"


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


def _aliases(contacts: dict) -> dict[str, str]:
    """Every other name for a saved contact -> the name it is saved under:
    the relations above, and a contact's own "aka" list in contacts.json
    ("sleepyhead panda" for neha, 4 Oct 2026)."""
    out = {a: c for c, names in _RELATIONS.items() if c in contacts for a in names}
    for key, val in contacts.items():
        for a in (val.get("aka") or []) if isinstance(val, dict) else []:
            out[a.lower().strip()] = key
    return out


def _find_contact(name: str) -> tuple[str, dict] | None:
    """Resolve a spoken name to a contact, tolerating mishearing."""
    contacts = _load()
    if not contacts:
        return None
    want = name.strip().lower()

    # "email mom" should reach maa even though nothing is stored under "mom".
    canonical = _aliases(contacts).get(want)
    if canonical:
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
def contact_info(name: str) -> str:
    """What is saved about one contact: the names they go by, and whether a
    number and an email are saved. Use for "who is X", "what's X's other name".

    name: the contact's name or one of their nicknames
    """
    found = _find_contact(name)
    if found is None:
        return f"I don't have anyone called {name} in your contacts."
    key, v = found
    aka: dict[str, str] = {}                 # "sleepyhead panda" and "sleepy head panda" are one name
    for a, k in _aliases(_load()).items():
        if k == key and a not in _RELATIONS.get(key, ()):
            aka.setdefault(a.replace(" ", ""), a)
    aka = list(aka.values())
    known = [w for w, f in (("a number", "phone"), ("an email", "email")) if v.get(f)]
    return (f"{key.capitalize()}" + (f" is also called {' or '.join(dict.fromkeys(a.title() for a in aka))}" if aka else
                                     " has no other name saved")
            + (f", and I have {' and '.join(known)} for them." if known else "."))


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
