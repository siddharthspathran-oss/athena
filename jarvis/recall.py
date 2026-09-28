"""Search past conversations, and learn how the user writes.

Past conversations
------------------
Every saved chat, and every spoken exchange, is cut into short overlapping
windows of a few turns and embedded with nomic-embed-text (already used for
document search), so "what did we decide about Q6 last week" finds the
conversation by meaning rather than exact words. The index is data/
chat_index.npz plus a JSON sidecar; it is brought up to date by the idle
learner and again, cheaply, before every search. Local only.

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
import time
from datetime import datetime

import numpy as np

from .config import DATA, cfg
from .store import StoreError, load_json, save_json

VECS = DATA / "chat_index.npz"
META = DATA / "chat_index.json"
VOICE_LOG = DATA / "voice_history.jsonl"     # spoken turns, kept for search
WINDOW, STEP = 6, 3                           # messages per chunk, stride


# ---------------------------------------------------------------- index

def _embed(texts: list[str], kind: str) -> np.ndarray:
    from .tools.knowledge import _embed as embed
    return embed([f"search_{kind}: {t}" for t in texts])


def _load() -> tuple[np.ndarray | None, dict]:
    try:
        meta = load_json(META, {"done": {}, "chunks": []})
    except StoreError:
        meta = {"done": {}, "chunks": []}
    vecs = None
    if VECS.exists() and meta["chunks"]:
        try:
            vecs = np.load(VECS)["v"]
            if len(vecs) != len(meta["chunks"]):
                vecs, meta = None, {"done": {}, "chunks": []}   # out of step: rebuild
        except Exception:
            vecs, meta = None, {"done": {}, "chunks": []}
    return vecs, meta


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
    from . import chats

    out = []
    for path in (DATA / "chats").glob("*.json"):
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        msgs = d.get("messages") if isinstance(d, dict) else d
        title = (d.get("title") if isinstance(d, dict) else "") or "chat"
        out.append((path.stem, title, path.stat().st_mtime, _turns(msgs or [])))
    if VOICE_LOG.exists():
        rows = []
        for line in VOICE_LOG.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
        # One pseudo-chat per day of speaking.
        by_day: dict[str, list[dict]] = {}
        for r in rows:
            by_day.setdefault(datetime.fromtimestamp(r["t"]).strftime("%Y-%m-%d"), []).append(r)
        for day, rs in by_day.items():
            out.append((f"voice-{day}", f"spoken, {day}", rs[-1]["t"], _turns(rs)))
    return out


def update_index() -> int:
    """Embed whatever is new since last time. Returns chunks added."""
    vecs, meta = _load()
    new_chunks, new_texts = [], []
    for sid, title, when, turns in _sources():
        done = int(meta["done"].get(sid, 0))
        if len(turns) <= done:
            continue
        start = max(0, done - WINDOW + STEP)          # overlap with the last chunk
        for i in range(start, len(turns), STEP):
            win = turns[i:i + WINDOW]
            if not win:
                continue
            new_chunks.append({"sid": sid, "title": title, "when": when,
                               "text": "\n".join(win)})
            new_texts.append("\n".join(win))
            if i + WINDOW >= len(turns):
                break
        meta["done"][sid] = len(turns)
    if not new_texts:
        return 0
    parts = [_embed(new_texts[i:i + 32], "document") for i in range(0, len(new_texts), 32)]
    added = np.vstack(parts)
    vecs = added if vecs is None else np.vstack([vecs, added])
    meta["chunks"].extend(new_chunks)
    np.savez_compressed(VECS, v=vecs)
    save_json(META, meta)
    return len(new_chunks)


def note_voice(role: str, content: str) -> None:
    """Keep a spoken turn for search (the learner's journal is emptied)."""
    if role in ("user", "assistant") and content:
        try:
            with VOICE_LOG.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"t": time.time(), "role": role,
                                    "content": content[:1500]}) + "\n")
        except OSError:
            pass


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
    try:
        update_index()
    except Exception:
        pass                                   # search what is there
    vecs, meta = _load()
    if vecs is None:
        return "I don't have any past conversations indexed yet."
    q = _embed([query], "query")[0]
    scores = vecs @ q
    order = np.argsort(-scores)
    picked, seen = [], set()
    for i in order:
        c = meta["chunks"][int(i)]
        key = (c["sid"], c["text"][:80])
        if key in seen or scores[i] < 0.45:
            continue
        seen.add(key)
        picked.append((float(scores[i]), c))
        if len(picked) >= k:
            break
    if not picked:
        return f"I couldn't find anything we talked about that matches '{query}'."
    out = []
    for score, c in picked:
        out.append(f"— {c['title']} ({_ago(c['when'])}):\n{c['text'][:700]}")
    return "From our past conversations:\n\n" + "\n\n".join(out)


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
