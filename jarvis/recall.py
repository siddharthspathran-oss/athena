"""Search past conversations, and learn how the user writes.

Past conversations
------------------
Every saved chat, and every spoken exchange, is cut into short overlapping
windows of a few turns and embedded with nomic-embed-text (already used for
document search), so "what did we decide about Q6 last week" finds the
conversation by meaning - and, through the full-text index beside it, by
the exact words too ("Q6", "RGZ1004"). It lives in data/athena.db
(jarvis/db.py), and is brought up to date by the idle learner and again,
cheaply, before every search. Local only.

nomic-embed-text is trained with task prefixes - "search_document:" on what
is stored, "search_query:" on what is asked - and ranks noticeably worse
without them, so both are always added.

Writing style
-------------
The user's own typed messages, and the WhatsApp and email texts Athena
sent or drafted for them, are the evidence. From a sample of them the local
model writes a handful of plain style notes - length, tone, language mix,
casing, punctuation, greetings and sign-offs - which replace the "Writing
style" section of the profile. The profile goes into every system prompt,
so a drafted message or email follows it. Spoken commands are not used:
speech is not how anyone writes.
"""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime

import numpy as np

from .config import DATA

WINDOW, STEP = 6, 3                           # messages per chunk, stride


# ---------------------------------------------------------------- index

def _embed(texts: list[str], kind: str) -> np.ndarray:
    from .tools.knowledge import _embed as embed
    return embed([f"search_{kind}: {t}" for t in texts])


def _embed_query(text: str) -> np.ndarray:
    return _embed([text], "query")[0]


def _turns(msgs: list[dict]) -> list[str]:
    out = []
    for m in msgs:
        role = m.get("role")
        if role in ("user", "assistant"):
            text = re.sub(r"\s+", " ", str(m.get("content") or "")).strip()
            if text and not is_nudge(text):
                out.append(f"{'You' if role == 'user' else 'Athena'}: {text[:400]}")
    return out


def _sources() -> list[tuple[str, str, float, list[str]]]:
    """(source id, title, time, turns) for every chat and the voice log."""
    from . import db

    out = []
    for path in (DATA / "chats").glob("*.json"):
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        msgs = d.get("messages") if isinstance(d, dict) else d
        title = (d.get("title") if isinstance(d, dict) else "") or "chat"
        out.append((path.stem, title, path.stat().st_mtime, _turns(msgs or [])))
    rows = db.rows("voice_log")
    if rows:
        # One pseudo-chat per day of speaking.
        by_day: dict[str, list[dict]] = {}
        for r in rows:
            by_day.setdefault(datetime.fromtimestamp(r["t"]).strftime("%Y-%m-%d"), []).append(r)
        for day, rs in by_day.items():
            out.append((f"voice-{day}", f"spoken, {day}", rs[-1]["t"], _turns(rs)))
    return out


_index_lock = threading.Lock()


def update_index(max_chunks: int | None = None, wait: bool = True) -> int:
    """Embed whatever is new since last time. Returns chunks added.

    max_chunks stops after roughly that many, newest conversations first,
    leaving the rest for the next call. Only one indexer runs at a time; with
    wait=False a call that finds one already running returns 0 at once
    instead of queueing behind it.
    """
    if not _index_lock.acquire(blocking=wait):
        return 0
    try:
        return _index(max_chunks)
    finally:
        _index_lock.release()


def _index(max_chunks: int | None) -> int:
    from . import db

    done_by = db.progress("chat")
    new_chunks, new_texts, reached = [], [], {}
    # Newest first, so a capped run indexes what is most likely asked about.
    for sid, title, when, turns in sorted(_sources(), key=lambda s: -s[2]):
        done = int(done_by.get(sid, 0))
        if len(turns) <= done:
            continue
        if max_chunks is not None and len(new_chunks) >= max_chunks:
            break                        # whole sources only: progress stays exact
        start = max(0, done - WINDOW + STEP)          # overlap with the last chunk
        for i in range(start, len(turns), STEP):
            win = turns[i:i + WINDOW]
            if not win:
                continue
            new_chunks.append({"source": sid, "title": title, "at": when, "pos": i,
                               "text": "\n".join(win)})
            new_texts.append("\n".join(win))
            if i + WINDOW >= len(turns):
                break
        reached[sid] = len(turns)
    if not new_texts:
        return 0
    parts = [_embed(new_texts[i:i + 32], "document") for i in range(0, len(new_texts), 32)]
    # Chunks and how far each source has been read are stored together, so
    # a crash can neither lose the progress nor index the same turns twice.
    return db.add_chunks("chat", new_chunks, np.vstack(parts), progress=reached)


def _index_in_background() -> None:
    def run():
        try:
            update_index(wait=True)
        except Exception as e:
            print(f"[recall: background indexing failed: {e}]")
    if not _index_lock.locked():
        threading.Thread(target=run, daemon=True, name="recall-index").start()


def backlog() -> int:
    """How many conversation turns are not indexed yet (cheap: no embedding)."""
    from . import db

    done_by = db.progress("chat")
    return sum(max(0, len(turns) - int(done_by.get(sid, 0)))
               for sid, _, _, turns in _sources())


def note_voice(role: str, content: str) -> None:
    """Keep a spoken turn for search (the learner's journal is emptied)."""
    from . import db

    db.log("voice_log", role, content)


# "what did we talk about" asked of recent() itself - never part of the answer.
_RECAP_ASK = re.compile(r"\b(?:what\s+(?:did|have|were)\s+(?:we|you|i)\s+(?:been\s+)?"
                        r"(?:talk|talking|talked|chat|speak|discuss|ask)|recap)\b", re.I)


def recent(when: str = "") -> str:
    """What was talked about today, yesterday, or on the latest day there was
    anything - read straight from the history, no embedding, so it answers at
    once. The user's side only: that is what the conversation was about."""
    from . import db

    items: list[tuple[float, str]] = []
    for r in db.rows("voice_log"):
        if r["role"] == "user" and not _RECAP_ASK.search(r["content"]):
            items.append((r["t"], r["content"]))
    for path in (DATA / "chats").glob("*.json"):
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        msgs = d.get("messages") if isinstance(d, dict) else d
        asked = [str(m.get("content") or "") for m in msgs or [] if m.get("role") == "user"
                 and not is_nudge(str(m.get("content") or ""))
                 and not _RECAP_ASK.search(str(m.get("content") or ""))]
        if asked:
            title = (d.get("title") if isinstance(d, dict) else "") or asked[0]
            items.append((path.stat().st_mtime, f"{title} (typed chat)"))
    if not items:
        return "We haven't talked about anything yet that I kept."

    w = (when or "").strip().lower()
    today = datetime.now().date()
    if "yesterday" in w:
        from datetime import timedelta
        day = today - timedelta(days=1)
    elif "today" in w:
        day = today
    else:                                   # latest day with anything
        day = datetime.fromtimestamp(max(t for t, _ in items)).date()
    picked = sorted((t, x) for t, x in items if datetime.fromtimestamp(t).date() == day)
    label = ("today" if day == today else "yesterday" if (today - day).days == 1
             else day.strftime("on %A %d %B"))
    if not picked:
        return f"We didn't talk about anything {label}."
    seen, lines = set(), []
    for t, x in picked:
        text = re.sub(r"\s+", " ", x).strip().rstrip("?.!")
        key = text.lower()[:60]
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"{text[:80]} ({datetime.fromtimestamp(t).strftime('%H:%M')})")
    shown = lines[-10:]
    more = f" - and {len(lines) - 10} earlier" if len(lines) > 10 else ""
    return f"{label.capitalize()} you asked me about: " + "; ".join(shown) + more + "."


def _ago(ts: float) -> str:
    days = (time.time() - ts) / 86400
    if days < 1:
        return "today"
    if days < 2:
        return "yesterday"
    if days < 14:
        return f"{int(days)} days ago"
    return datetime.fromtimestamp(ts).strftime("%d %b")


def search(query: str, k: int = 4) -> str:
    from . import db

    # A search used to index everything not yet indexed before it answered:
    # after a busy week that was minutes of embedding inside one request, and
    # "what did we talk about" sat on "recalling" (28 Sep 2026). Now it takes
    # a small, recent slice here - skipped if the learner is already at it -
    # and leaves the rest to a background thread.
    try:
        update_index(max_chunks=48, wait=False)
    except Exception:
        pass                                   # search what is there
    behind = ""
    try:
        if backlog():
            _index_in_background()
            behind = "\n\n(Still indexing older conversations - ask again in a minute for those.)"
    except Exception:
        pass
    if not db.count("chat"):
        return "I don't have any past conversations indexed yet." + behind
    hits = db.search("chat", query, k=k * 2, embed=_embed_query, min_score=0.45)
    picked, seen = [], set()
    for h in hits:
        key = (h["source"], h["text"][:80])
        if key in seen:
            continue
        seen.add(key)
        picked.append(h)
        if len(picked) >= k:
            break
    if not picked:
        return f"I couldn't find anything we talked about that matches '{query}'." + behind
    out = [f"— {h['title']} ({_ago(h['at'])}):\n{h['text'][:700]}" for h in picked]
    return "From our past conversations:\n\n" + "\n\n".join(out) + behind


# ---------------------------------------------------------------- style

_STYLE_PROMPT = """Below are messages written by one person - things they typed, and \
WhatsApp messages and emails sent in their name. Describe HOW they write, so an assistant \
can draft messages that sound like them.

Write 4 to 7 short bullet points covering: typical length; tone and formality (to friends \
vs. teachers if it differs); language (English, Hindi, Hinglish mix); capitalisation and \
punctuation habits; common greetings, sign-offs, abbreviations or emoji. Describe the \
style only - do not quote private details, names, numbers or content. Only state a habit \
you can see in at least three of the messages; a one-off typo is not a habit, and do not \
guess at how they write to people who do not appear below.

Messages:
{samples}

Reply with the bullet points only, each starting with "- "."""


def is_nudge(text: str) -> bool:
    """Athena's own push-back to the model, stored with role "user" in the
    chat - not something the user said."""
    return text.startswith(("You said that was done, but you did not call",
                            "You have not run any tool, so you cannot know"))


_COMMAND = re.compile(
    r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please|can\s+you|could\s+you)\W+)*"
    r"(?:open|close|play|pause|stop|start|launch|shut|shutdown|restart|set|turn|mute|"
    r"call|send|search|find|show|take|lock|type|remind|what|whats|what's|how|when|where|"
    r"who|why|is|are|do|does|explain|tell|give|check|brief|list|delete|move|rename|"
    r"go|run|fill|download|read|solve|calculate)\b", re.I)


def _samples(limit: int = 80) -> list[str]:
    """The user's own writing, newest first."""
    out: list[tuple[float, str]] = []
    for path in (DATA / "chats").glob("*.json"):
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        msgs = d.get("messages") if isinstance(d, dict) else d
        when = path.stat().st_mtime
        for m in msgs or []:
            if m.get("role") == "user":
                t = str(m.get("content") or "").strip()
                # Commands to Athena are not writing. The first style built
                # from them (25 Sep 2026) said "messages are one to three
                # words" and read a typo, "oc" for "pc", as an abbreviation.
                if len(t.split()) >= 6 and not _COMMAND.match(t) and not is_nudge(t):
                    out.append((when, t[:300]))
            for call in m.get("tool_calls") or []:
                fn = (call.get("function") or {})
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {}
                if fn.get("name") == "whatsapp_message" and args.get("text"):
                    out.append((when, "[WhatsApp] " + str(args["text"])[:300]))
                if fn.get("name") == "compose_email" and args.get("body"):
                    out.append((when, "[Email] " + str(args["body"])[:600]))
    out.sort(key=lambda x: -x[0])
    return [t for _, t in out[:limit]]


def learn_style(min_samples: int = 15) -> list[str]:
    """Rewrite the profile's Writing style section from the user's samples.
    Returns the new notes, or [] if there is not enough to go on."""
    from . import profile
    from .llm import LLM

    samples = _samples()
    if len(samples) < min_samples:
        return []
    text = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(samples))
    msg = LLM().chat([{"role": "user", "content": _STYLE_PROMPT.format(samples=text)}],
                     temperature=0.2, max_tokens=400)
    notes = [re.sub(r"^\s*[-*•]\s*", "", ln).strip()
             for ln in (msg.content or "").splitlines() if re.match(r"^\s*[-*•]\s+\S", ln)]
    notes = [n for n in notes if 10 <= len(n) <= 200 and not profile._SECRET.search(n)][:7]
    if len(notes) < 3:
        return []
    profile.set_style(notes)
    return notes
