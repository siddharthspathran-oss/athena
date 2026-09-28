"""Guarded command execution, plus remember/recall and time helpers."""
from __future__ import annotations

import subprocess
from datetime import datetime

from ..config import cfg
from ..registry import tool

TAG = ["shell"]


def _is_blocked(command: str) -> str | None:
    """Return the matched pattern if the command is on the never-run list."""
    low = command.lower()
    for pat in cfg.get("safety.blocked_patterns", []):
        if pat.lower() in low:
            return pat
    return None


@tool(tags=TAG, destructive=True, timeout=0)
def run_powershell(command: str, timeout_seconds: int = 60) -> str:
    """Run a PowerShell command on this PC and return its output.

    Use this only when no dedicated tool exists for the job.

    command: the PowerShell command to run
    timeout_seconds: how long to wait before giving up
    """
    blocked = _is_blocked(command)
    if blocked:
        return f"REFUSED: that command matches the blocked pattern {blocked} in config.yaml."

    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, timeout=timeout_seconds, check=False,
        )
    except subprocess.TimeoutExpired:
        return f"ERROR: command timed out after {timeout_seconds}s."
    except Exception as e:
        return f"ERROR: {e}"

    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    if r.returncode != 0:
        return f"Exit code {r.returncode}\n{out}\n{err}".strip()
    return out or "(no output)"


@tool(tags=TAG)
def get_datetime(what: str = "both") -> str:
    """Get the current date, time, or day of the week.

    what: one of date, time, day, both
    """
    now = datetime.now()
    w = what.strip().lower()
    if w == "date":
        return now.strftime("%A %d %B %Y")
    if w == "time":
        return now.strftime("%I:%M %p").lstrip("0")
    if w == "day":
        return now.strftime("%A")
    return now.strftime("%A %d %B %Y, %I:%M %p")


def _contact_name_from_key(key: str) -> str:
    """Work out whose detail this is: 'maa's email' -> maa, 'my email' -> me."""
    import re

    k = key.lower().replace("_", " ")
    k = re.sub(r"\b(e-?mail|address|phone|mobile|number|contact|id)\b", " ", k)
    k = re.sub(r"['’]s\b", " ", k)
    k = re.sub(r"\b(the|of|for|my|mine)\b", " ", k)
    k = re.sub(r"\s+", " ", k).strip()
    return k or "me"


# ---------------------------------------------------------------- memory
# One remember, one recall, one forget for the model. There used to be six
# memory tools over three stores - labelled facts (data/memory.json), the
# learned profile (data/profile.md) and past conversations - and the local
# model regularly saved with one and looked in another. These three decide
# the store themselves and search all of them. The profile-only tools below
# are still there for the routers ("remember that I ...", "forget X").

def _save_fact(key: str, value: str) -> str:
    from ..memory import MEM_FILE, load_facts, save_facts
    from ..store import StoreError, load_json

    try:
        load_json(MEM_FILE, {})         # refuses, rather than overwrite, a damaged file
    except StoreError as e:
        return f"ERROR: could not save - {e}"
    facts = load_facts()
    old = facts.get(key)
    facts[key] = value
    save_facts(facts)
    if old and old != value:
        return f"Updated - your {key} is now {value} (it was {old})."
    return f"Got it - your {key} is {value}."


@tool(tags=["memory"])
def remember(what: str, label: str = "") -> str:
    """Save something so it survives restarts. Use for anything the user asks
    you to remember, save or note, and for lasting facts they state about
    themselves. For a person's phone number or email use add_contact instead.

    what: the thing to remember - a value like "42", or a sentence like "I prefer short answers"
    label: a short name to look it up by later, like "locker number" or "wifi name"; empty for a fact about the user
    """
    import re

    val = (what or "").strip()
    key = (label or "").strip()
    if not val:
        return "ERROR: nothing to remember."
    # Routing this by wording alone proved unreliable, so catch it by value:
    # a contact detail filed as a note is invisible to the messaging tools.
    is_email = bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", val))
    is_phone = bool(re.fullmatch(r"\+?\d[\d\s\-()]{9,}", val))
    if (is_email or is_phone) and key:
        who = _contact_name_from_key(key)
        from .messaging import add_contact as _add_contact

        field = {"email": val} if is_email else {"phone": val}
        result = _add_contact(who, **field)
        if result.startswith("ERROR"):
            return result
        return f"{result} (saved as a contact, not a note, so messaging can find it)"

    if key:
        # A label the user already has, said slightly differently, updates
        # that fact instead of starting a second one next to it.
        from ..memory import find_facts
        same = find_facts(key, threshold=0.9)
        return _save_fact(same[0][0] if same else key, val)

    from .. import profile
    why = profile.add(val, _profile_section(val), 14 if _profile_section(val) == "context" else None)
    if why is None:
        return "Got it, I'll remember that."
    if why == "already known":
        return "I already knew that."
    if why.startswith("too short") and len(val) < 60:
        return ("ERROR: that needs a label to be found again - call remember with "
                "what and a short label, like label='locker number'.")
    return f"I didn't save that - {why}."


def _profile_section(note: str) -> str:
    import re

    low = note.lower()
    if re.search(r"\b(prefer|like|love|hate|don'?t like|always|never|want|rather|"
                 r"favourite|favorite)\b", low):
        return "prefs"
    if re.search(r"\b(due|exam|deadline|this week|next week|tomorrow|working on|"
                 r"project|assignment)\b", low):
        return "context"
    return "about"


@tool(tags=["memory"])
def recall(query: str = "") -> str:
    """Look up something remembered earlier: saved facts, what you know about
    the user, and past conversations. Empty query lists everything saved.

    query: what to look for, in the user's words, e.g. "locker number" or "my exam"
    """
    from .. import profile
    from ..memory import find_facts, load_facts

    q = (query or "").strip()
    if not q:
        facts = load_facts()
        out = [f"{k}: {v}" for k, v in facts.items()]
        learned = profile.prompt_text()
        if learned:
            out.append(learned)
        return "\n".join(out) if out else "Nothing stored yet."

    facts = find_facts(q)[:5]
    learned = _profile_matches(q)[:5]
    if len(facts) == 1 and not learned:
        return f"Your {facts[0][0]} is {facts[0][1]}."      # read aloud as a sentence
    lines = [f"{k}: {v}" for k, v, _ in facts] + [f"- {t}" for t in learned]
    if lines:
        return "\n".join(lines)
    # Not saved anywhere on purpose - perhaps it was only said in passing.
    try:
        from .. import recall as past
        found = past.search(q, k=2)
        if not found.startswith("I couldn't") and not found.startswith("I don't"):
            return found
    except Exception:
        pass
    return f"Nothing saved about '{q}'."


def _profile_matches(query: str) -> list[str]:
    from .. import profile
    from ..memory import covers

    scored = [(covers(query, i.text), i.text) for i in profile.load() if i.live()]
    return [t for s, t in sorted(scored, reverse=True) if s >= 0.6]


def forget_everywhere(what: str) -> str:
    """Remove a saved fact and matching profile lines. Shared by forget and
    the "forget X" router, so either clears both stores."""
    from .. import profile
    from ..memory import find_facts, load_facts, save_facts, _norm

    removed: list[str] = []
    facts = load_facts()
    near = find_facts(what, threshold=0.6)
    exact = [h for h in near if _norm(h[0]) == _norm(what)]
    if exact:
        hits = exact[:1]
    elif len(near) == 1 and near[0][2] >= 0.75:
        hits = near
    elif near:
        # Deleting is not undoable: a loose or shared match is asked about,
        # never guessed ("forget my name" must not take "wifi name" with it).
        return ("Which one? " + "; ".join(f"{k}: {v}" for k, v, _ in near[:5])
                + ". Say the label exactly.")
    else:
        hits = []
    for k, v, _ in hits:
        facts.pop(k, None)
        removed.append(f"{k}: {v}")
    if hits:
        save_facts(facts)
    removed += profile.forget(what)
    if not removed:
        return f"I don't have anything saved about '{what}'."
    return "Forgotten: " + "; ".join(removed)


@tool(tags=["memory"], destructive=True)
def forget(what: str) -> str:
    """Delete something remembered earlier, from saved facts and from what
    you know about the user.

    what: what to forget, in the user's words, e.g. "locker number"
    """
    return forget_everywhere(what)


# ---------------------------------------------------------------- learned profile

@tool(tags=TAG + ["route_only"])
def what_you_remember() -> str:
    """Show everything Athena has learned about the user across conversations:
    preferences, ongoing work, people, corrections. Use for "what do you know about me"."""
    from .. import profile
    from ..memory import load_facts

    shown = profile.show()
    facts = load_facts()
    if facts:
        shown = "Saved facts:\n" + "\n".join(f"  - {k}: {v}" for k, v in facts.items()) + "\n" + shown
    return shown


@tool(tags=TAG + ["route_only"])
def remember_about_me(note: str) -> str:
    """Add something lasting about the user to their profile - a preference,
    who someone is, what they are working on. Not for passwords or codes.

    note: one sentence, e.g. "I prefer answers with every step shown"
    """
    from .. import profile

    section = _profile_section(note)
    days = 14 if section == "context" else None
    why = profile.add(note, section, days)
    if why == "already known":
        return "I already knew that."
    if why:
        return f"I didn't save that - {why}."
    return "Got it, I'll remember that."


@tool(tags=TAG + ["route_only"])
def forget_about(thing: str) -> str:
    """Remove things from what Athena has learned about the user.

    thing: what to forget, in the user's words, e.g. "my exam date"
    """
    return forget_everywhere(thing)


@tool(tags=TAG, timeout=90)
def search_past_conversations(query: str) -> str:
    """Search earlier conversations with the user - typed chats and spoken
    ones - by meaning. Use for "what did we decide about X", "when did I ask
    you about Y", "what was that site / file we talked about last week".

    query: what to look for, in plain words
    """
    from .. import recall
    return recall.search(query)


@tool(tags=TAG)
def recent_conversations(when: str = "") -> str:
    """Say what the user and Athena talked about recently, without a topic:
    "what did we talk about", "what did I ask you yesterday", "recap".

    when: "today", "yesterday", or empty for the most recent day
    """
    from .. import recall
    return recall.recent(when)


@tool(tags=TAG, timeout=0)
def learn_my_writing_style() -> str:
    """Study how the user writes (their typed messages, and the WhatsApp and
    email texts sent for them) and update the writing style Athena drafts in."""
    from .. import recall
    notes = recall.learn_style(min_samples=8)
    if not notes:
        return ("I need more of your own writing first - a few more typed messages, "
                "or messages and emails you've had me send.")
    return "Here's how you write, as I see it:\n" + "\n".join(f"- {n}" for n in notes)


@tool(tags=TAG)
def list_skills() -> str:
    """Summarise what Athena can do, grouped by area. Use for "what can you do",
    "what tools do you have", "tools and skills"."""
    from .. import registry
    groups: dict[str, list[str]] = {}
    for t in registry.REGISTRY.values():
        area = (t.tags or ["other"])[0]
        groups.setdefault(area, []).append(t.name.replace("_", " "))
    names = {"system": "PC & apps", "files": "Files", "web": "Web & browser", "shell": "Memory & system",
             "messaging": "Messages & calls", "scheduler": "Reminders", "study": "Class work",
             "knowledge": "Your documents", "windows": "Windows settings", "brief": "Calendar & mail"}
    out = [f"{len(registry.REGISTRY)} skills:"]
    for area, tools in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        out.append(f"  {names.get(area, area.title())} ({len(tools)}): " + ", ".join(sorted(tools)[:6])
                   + (" ..." if len(tools) > 6 else ""))
    return "\n".join(out)
