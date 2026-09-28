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


@tool(tags=TAG, destructive=True)
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


@tool(tags=["memory"])
def remember(key: str, value: str) -> str:
    """Save a general fact to disk so it survives restarts.

    For a person's email address or phone number use add_contact instead -
    that is the only store the messaging tools read. Anything saved here is a
    note, not a contact.

    key: a short label, for example 'name' or 'birthday' or 'work folder'
    value: the thing to remember
    """
    import re

    # Routing this by wording alone proved unreliable, so catch it by value:
    # a contact detail filed as a note is invisible to the messaging tools.
    val = value.strip()
    is_email = bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", val))
    is_phone = bool(re.fullmatch(r"\+?\d[\d\s\-()]{9,}", val))
    if is_email or is_phone:
        who = _contact_name_from_key(key)
        from .messaging import add_contact as _add_contact

        field = {"email": val} if is_email else {"phone": val}
        result = _add_contact.__wrapped__(who, **field) if hasattr(_add_contact, "__wrapped__") \
            else _add_contact(who, **field)
        return f"{result} (saved as a contact, not a note, so messaging can find it)"
    from ..memory import MEM_FILE
    import json

    facts = {}
    if MEM_FILE.exists():
        try:
            facts = json.loads(MEM_FILE.read_text(encoding="utf-8"))
        except Exception:
            facts = {}
    facts[key] = value
    MEM_FILE.write_text(json.dumps(facts, indent=2), encoding="utf-8")
    return f"Noted: {key} = {value}"


@tool(tags=["memory"])
def recall(key: str = "") -> str:
    """Look up something previously remembered, or list everything remembered.

    key: the label to look up, or empty to list all stored facts
    """
    from ..memory import MEM_FILE
    import json

    if not MEM_FILE.exists():
        return "Nothing stored yet."
    try:
        facts = json.loads(MEM_FILE.read_text(encoding="utf-8"))
    except Exception:
        return "Nothing stored yet."
    if not facts:
        return "Nothing stored yet."
    if key:
        return facts.get(key, f"Nothing stored under {key}.")
    return "\n".join(f"{k}: {v}" for k, v in facts.items())


@tool(tags=["memory"], destructive=True)
def forget(key: str) -> str:
    """Delete a previously remembered fact.

    key: the label to remove
    """
    from ..memory import MEM_FILE
    import json

    if not MEM_FILE.exists():
        return "Nothing stored yet."
    facts = json.loads(MEM_FILE.read_text(encoding="utf-8"))
    if key not in facts:
        return f"Nothing stored under {key}."
    del facts[key]
    MEM_FILE.write_text(json.dumps(facts, indent=2), encoding="utf-8")
    return f"Forgot {key}."


# ---------------------------------------------------------------- learned profile

@tool(tags=TAG)
def what_you_remember() -> str:
    """Show everything Athena has learned about the user across conversations:
    preferences, ongoing work, people, corrections. Use for "what do you know about me"."""
    from .. import profile
    return profile.show()


@tool(tags=TAG)
def remember_about_me(note: str) -> str:
    """Add something lasting about the user to their profile - a preference,
    who someone is, what they are working on. Not for passwords or codes.

    note: one sentence, e.g. "I prefer answers with every step shown"
    """
    import re as _re
    from .. import profile

    low = note.lower()
    section = ("prefs" if _re.search(r"\b(prefer|like|love|hate|don'?t like|always|never|want|"
                                      r"rather|favourite|favorite)\b", low)
               else "context" if _re.search(r"\b(due|exam|deadline|this week|next week|tomorrow|"
                                            r"working on|project|assignment)\b", low)
               else "about")
    days = 14 if section == "context" else None
    why = profile.add(note, section, days)
    if why == "already known":
        return "I already knew that."
    if why:
        return f"I didn't save that - {why}."
    return "Got it, I'll remember that."


@tool(tags=TAG)
def forget_about(thing: str) -> str:
    """Remove things from what Athena has learned about the user.

    thing: what to forget, in the user's words, e.g. "my exam date"
    """
    from .. import profile
    gone = profile.forget(thing)
    if not gone:
        return f"I don't have anything about '{thing}' in what I remember."
    return "Forgotten: " + "; ".join(gone)


@tool(tags=TAG)
def search_past_conversations(query: str) -> str:
    """Search earlier conversations with the user - typed chats and spoken
    ones - by meaning. Use for "what did we decide about X", "when did I ask
    you about Y", "what was that site / file we talked about last week".

    query: what to look for, in plain words
    """
    from .. import recall
    return recall.search(query)


@tool(tags=TAG)
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
