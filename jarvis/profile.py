"""What Athena has learned about you, kept across chats and across weeks.

One Markdown file, data/profile.md, is the whole of it - readable, and
editable by hand: add a line, delete a line, and the next reply uses it. It
has four sections (About you, Preferences, Ongoing context, Corrections);
each bullet carries the date it was learned and, for things that stop being
true ("assignment due Friday"), the date it lapses.

Nobody has to say "remember that". When a conversation has been quiet for
ten minutes, the local model reads what was said and proposes additions,
updates and removals, which are applied here - dedupe, a size limit, and a
filter that keeps passwords, OTPs, card and account numbers and keys out
whatever the model proposes. Spoken commands are not saved as chats, so the
voice and hotkey windows note each exchange in a journal (in data/athena.db)
for this to read; it is emptied as it is learned from.

All of it runs on the local model and stays in data/. The short profile
does travel with a request that already goes to a hosted model (a study
question, a multi-step task), because a request without it forgets you.
"""
from __future__ import annotations

import difflib
import re
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from .config import DATA, cfg
from .store import StoreError, load_json, save_json

PROFILE = DATA / "profile.md"
PROGRESS = DATA / "learned.json"

SECTIONS = {
    "about": "About you",
    "prefs": "Preferences",
    "context": "Ongoing context",
    "corrections": "Corrections",
    "style": "Writing style",
}
MAX_ITEMS = 80

# Never kept, whatever the model proposes: things that are only useful to
# someone who has stolen them.
_SECRET = re.compile(
    r"pass(word|code|phrase)|\bpin\b|\botp\b|one.?time|cvv|cvc|"
    r"\b(?:\d[ -]?){12,19}\b|"                       # card / account numbers
    r"\b(?:sk|gsk|AIza|ghp|xox[bp])[-_A-Za-z0-9]{12,}|"  # API keys and tokens
    r"api[_ ]?key|secret|token|aadhaar|\bifsc\b|account\s*(?:number|no)",
    re.I)


@dataclass
class Item:
    section: str
    text: str
    added: str                  # YYYY-MM-DD
    until: str | None = None    # YYYY-MM-DD, or None for lasting

    def live(self, today: date | None = None) -> bool:
        return not self.until or self.until >= (today or date.today()).isoformat()


# ---------------------------------------------------------------- file

_LINE = re.compile(r"^\s*[-*]\s+(?P<text>.+?)\s*(?:<!--\s*(?P<meta>.*?)\s*-->)?\s*$")


def load() -> list[Item]:
    if not PROFILE.exists():
        return []
    items: list[Item] = []
    section = "about"
    titles = {v.lower(): k for k, v in SECTIONS.items()}
    for raw in PROFILE.read_text(encoding="utf-8").splitlines():
        if raw.startswith("## "):
            section = titles.get(raw[3:].strip().lower(), section)
            continue
        m = _LINE.match(raw)
        if not m:
            continue
        meta = m.group("meta") or ""
        added = re.search(r"\d{4}-\d{2}-\d{2}", meta)
        until = re.search(r"until\s+(\d{4}-\d{2}-\d{2})", meta)
        items.append(Item(section, m.group("text").strip(),
                          added.group(0) if added else date.today().isoformat(),
                          until.group(1) if until else None))
    return items


def save(items: list[Item]) -> None:
    lines = ["# What Athena remembers about you",
             "",
             "Learned from your conversations. Edit freely: delete a line to make",
             "Athena forget it, add one to tell it something. Dates are when it was",
             "learned; 'until' is when it stops applying.",
             ""]
    for key, title in SECTIONS.items():
        lines.append(f"## {title}")
        for it in items:
            if it.section == key:
                meta = it.added + (f" until {it.until}" if it.until else "")
                lines.append(f"- {it.text}  <!-- {meta} -->")
        lines.append("")
    tmp = PROFILE.with_suffix(".tmp")
    tmp.write_text("\n".join(lines), encoding="utf-8")
    tmp.replace(PROFILE)


# ---------------------------------------------------------------- edits

def _similar(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def add(text: str, section: str = "about", days: int | None = None,
        items: list[Item] | None = None) -> str | None:
    """Add one item unless it is a secret, junk, or already known.
    Returns why it was refused, or None when added."""
    text = re.sub(r"\s+", " ", text or "").strip().rstrip(".") + "."
    if not 8 <= len(text) <= 200 or len(text.split()) < 3:
        return "too short or too long"
    # Addresses and numbers live in the contact book, which is what the
    # messaging tools read; one filed here helps nobody.
    if re.search(r"[\w.+-]+@[\w-]+\.[\w.]+|\+?\d[\d\s-]{8,}\d", text):
        return "contact details go in contacts (say 'add contact ...')"
    if _SECRET.search(text):
        return "looks like a secret - never stored"
    section = section if section in SECTIONS else "about"
    own = items is None
    items = load() if own else items
    for it in items:
        if _similar(it.text, text) > 0.85:
            it.added = date.today().isoformat()          # re-confirmed
            if own:
                save(items)
            return "already known"
    until = (date.today() + timedelta(days=days)).isoformat() if days else None
    items.append(Item(section, text, date.today().isoformat(), until))
    if own:
        save(_prune(items))
    return None


def forget(query: str) -> list[str]:
    """Remove every item that matches `query`; returns what was removed."""
    q = query.strip().lower().strip(" .")
    q = re.sub(r"^(?:that|about|the fact that)\s+", "", q)
    q = re.sub(r"^i\s+", "", q)
    words = {w for w in re.findall(r"[a-z0-9]+", q) if len(w) > 2}
    items = load()
    keep, gone = [], []
    for it in items:
        low = it.text.lower()
        hit = q and (q in low or _similar(q, low) > 0.7)
        if not hit and words:
            have = set(re.findall(r"[a-z0-9]+", low))
            hit = len(words & have) / len(words) >= 0.75
        (gone if hit else keep).append(it)
    if gone:
        save(keep)
    return [g.text for g in gone]


def set_style(notes: list[str]) -> None:
    """Replace the Writing style section wholesale - it is rebuilt from the
    latest samples each time, not accumulated."""
    items = [i for i in load() if i.section != "style"]
    today = date.today().isoformat()
    items += [Item("style", n.rstrip(".") + ".", today) for n in notes]
    save(items)


def _prune(items: list[Item]) -> list[Item]:
    """Drop lapsed items, then the oldest if over the limit."""
    items = [i for i in items if i.live()]
    if len(items) > MAX_ITEMS:
        items = sorted(items, key=lambda i: i.added)[-MAX_ITEMS:]
    return items


# ---------------------------------------------------------------- the prompt

_cache = {"mtime": None, "text": ""}


def prompt_text() -> str:
    """The profile as a few lines for a system prompt, or "".

    Re-read only when the file changes, so the system prompt - and with it
    Ollama's cached prefix - stays identical between turns.
    """
    try:
        mtime = PROFILE.stat().st_mtime
    except OSError:
        return ""
    if mtime != _cache["mtime"]:
        live = [i for i in load() if i.live()]
        parts = []
        for key, title in SECTIONS.items():
            rows = [f"- {i.text}" + (f" (until {i.until})" if i.until else "")
                    for i in live if i.section == key]
            if rows:
                if key == "style":
                    title = ("Writing style - follow it whenever you write a message, "
                             "email or post on the user's behalf")
                parts.append(f"{title}:\n" + "\n".join(rows))
        _cache.update(mtime=mtime, text="\n".join(parts))
    return _cache["text"]


def show() -> str:
    live = [i for i in load() if i.live()]
    if not live:
        return ("I haven't learned anything about you yet. I pick things up from our "
                "conversations as we go, or tell me: 'remember that ...'.")
    out = []
    for key, title in SECTIONS.items():
        rows = [f"  - {i.text}" for i in live if i.section == key]
        if rows:
            out.append(f"{title}:\n" + "\n".join(rows))
    return "\n".join(out) + f"\n\n(Kept in {PROFILE} - edit it any time.)"


# ---------------------------------------------------------------- journal

def journal(role: str, content: str) -> None:
    """Note one turn of a spoken or popup exchange, for the learner."""
    from . import db

    db.log("journal", role, content)


# ---------------------------------------------------------------- learning

_LEARN_PROMPT = """You maintain a short profile of ONE person - the user of a personal \
assistant called Athena - so that Athena remembers them across conversations.

Read the conversation below and decide what, if anything, is worth remembering for WEEKS. \
Keep only things about the user themself:
- about: who they are, where they study/work, people in their life and who those people are
- prefs: how they like things done - answer style, tools, apps, music, tone, what annoys them
- context: what they are working on or have coming up (projects, exams, deadlines, plans). \
Give "days" for how long it stays true (e.g. 7 for "exam next week").
- corrections: things Athena got wrong that the user corrected, stated as the right behaviour

Do NOT keep: one-off requests ("open spotify"), general knowledge, anything Athena said that \
the user did not confirm, guesses, passwords/PINs/OTPs/card or account numbers/keys, or \
anything already in the profile (unless it changed - then update it).

Current profile (numbered):
{profile}

Conversation:
{convo}

Reply with ONE JSON object, nothing else:
{{"add": [{{"section": "about|prefs|context|corrections", "text": "one short sentence", \
"days": null, "quote": "the user's exact words that show it"}}], \
"update": [{{"n": 3, "text": "new wording", "quote": "..."}}], "remove": [5]}}
"quote" must be copied word for word from a "User:" line above - not from Athena's lines. \
If you cannot quote the user saying it, do not add it.
Most conversations teach nothing lasting - then reply {{"add": [], "update": [], "remove": []}}. \
At most 4 additions."""


def _progress() -> dict:
    try:
        return load_json(PROGRESS, {"chats": {}, "journal": 0})
    except StoreError:
        return {"chats": {}, "journal": 0}


def _render(msgs: list[dict]) -> str:
    lines = []
    for m in msgs:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        text = re.sub(r"\s+", " ", str(m.get("content") or "")).strip()
        from .recall import is_nudge
        if text and not is_nudge(text):
            lines.append(f"{'User' if role == 'user' else 'Athena'}: {text[:500]}")
    return "\n".join(lines)[-5000:]


def _quoted(entry: dict, said: str) -> bool:
    """The model must show the user's own words for anything it keeps.

    Asked to learn from a chat full of "no confirmations please", qwen2.5:7b
    once proposed "User prefers explicit confirmation when a task is
    completed" - a guess, and the opposite of what was said (25 Sep 2026).
    Requiring a verbatim quote from the user's lines, checked here rather
    than trusted, throws such guesses away.
    """
    q = re.sub(r"\s+", " ", str(entry.get("quote") or "")).strip(" .\"'").lower()
    return len(q.split()) >= 3 and q in said


def _apply(plan: dict, items: list[Item], said: str = "") -> list[str]:
    notes = []
    for n in sorted({int(x) for x in plan.get("remove") or [] if str(x).isdigit()}, reverse=True):
        if 1 <= n <= len(items):
            notes.append(f"dropped: {items.pop(n - 1).text}")
    for up in plan.get("update") or []:
        try:
            n, text = int(up["n"]), str(up["text"]).strip()
        except (KeyError, TypeError, ValueError):
            continue
        if 1 <= n <= len(items) and text and not _SECRET.search(text) and _quoted(up, said):
            notes.append(f"updated: {text}")
            items[n - 1].text, items[n - 1].added = text, date.today().isoformat()
    for new in (plan.get("add") or [])[:4]:
        if not isinstance(new, dict) or not _quoted(new, said):
            continue
        days = new.get("days")
        days = int(days) if isinstance(days, (int, float)) and days > 0 else None
        section = str(new.get("section", "about"))
        section = "prefs" if section == "style" else section   # style is rebuilt, not added to
        why = add(str(new.get("text", "")), section, days, items)
        if why is None:
            notes.append(f"learned: {new.get('text')}")
    return notes


def _learn_from(convo: str) -> list[str]:
    from .llm import LLM
    from .planner import _parse

    items = [i for i in load() if i.live()]
    numbered = "\n".join(f"{n}. [{i.section}] {i.text}" for n, i in enumerate(items, 1)) or "(empty)"
    msg = LLM().chat([{"role": "user", "content": _LEARN_PROMPT.format(profile=numbered, convo=convo)}],
                     temperature=0.0, max_tokens=600)
    plan = _parse(msg.content or "")
    if not plan:
        return []
    said = " ".join(re.sub(r"\s+", " ", line[5:]).lower()
                    for line in convo.splitlines() if line.startswith("User:"))
    notes = _apply(plan, items, said)
    if notes:
        save(_prune(items))
    return notes


def learn_pending(quiet_minutes: float = 10) -> list[str]:
    """Learn from every chat quiet for `quiet_minutes`, and from the voice
    journal, that has not been learned from yet. Returns what changed."""
    from . import chats

    prog = _progress()
    notes: list[str] = []
    cutoff = time.time() - quiet_minutes * 60
    for path in sorted((DATA / "chats").glob("*.json"), key=lambda p: p.stat().st_mtime):
        if path.stat().st_mtime > cutoff:
            continue                                  # still going
        cid = path.stem
        msgs = chats.load(cid)
        done = int(prog["chats"].get(cid, 0))
        if len(msgs) <= done:
            continue
        convo = _render(msgs[max(0, done - 4):])      # a little overlap for context
        if convo.count("User:") >= 1:
            notes += _learn_from(convo)
        prog["chats"][cid] = len(msgs)
        save_json(PROGRESS, prog)

    from . import db

    last = db.last_time("journal")
    if last is not None and last <= cutoff:          # the spoken session has gone quiet
        rows = db.rows("journal")
        for i in range(0, len(rows), 40):
            notes += _learn_from(_render(rows[i:i + 40]))
        if rows:
            db.drop_rows("journal", rows[-1]["id"])  # only what was read; newer turns stay
    return notes


def _style_due() -> bool:
    """Rebuild the writing style at most once a day, and only once there are
    at least 20 new samples of the user's writing since the last time."""
    from . import recall

    prog = _progress()
    if time.time() - float(prog.get("style_time", 0)) < 86400:
        return False
    count = len(recall._samples(limit=10_000))
    if count - int(prog.get("style_count", 0)) < 20 and prog.get("style_time"):
        return False
    prog.update(style_time=time.time(), style_count=count)
    save_json(PROGRESS, prog)
    return True


def start_learner(is_idle=lambda: True, log=lambda s: None) -> None:
    """Background thread: every few minutes, learn from quiet conversations
    - only while Athena is idle, so it never delays a reply."""
    if not cfg.get("memory.learn", True):
        return

    def loop():
        time.sleep(60)
        while True:
            try:
                from . import gaming
                if gaming.on():          # loads the chat and embedding models; wait
                    time.sleep(300)
                    continue
                if is_idle():
                    for note in learn_pending(float(cfg.get("memory.quiet_minutes", 10))):
                        log(f"memory {note[:90]}")
                if is_idle():
                    from . import recall
                    n = recall.update_index(wait=False)   # a search may be indexing
                    if n:
                        log(f"memory indexed {n} new conversation snippet(s)")
                if is_idle() and _style_due():
                    notes = recall.learn_style()
                    if notes:
                        log(f"memory updated your writing style ({len(notes)} notes)")
            except Exception as e:
                log(f"memory learner error: {str(e)[:80]}")
            time.sleep(300)

    threading.Thread(target=loop, daemon=True, name="jarvis-learner").start()
