"""Saved conversations, so closing the window does not throw the thread away.

Until now the transcript lived only in memory: every restart began with a
blank slate, and anything worked out in a previous session was gone. The
facts you explicitly asked it to remember survived (memory.json), but the
conversation around them did not.

One file per conversation in data/chats/, newest-first by when it was last
touched. Deliberately files rather than a database - they can be read, backed
up, and deleted with Explorer, and a corrupt one costs a single conversation
rather than the lot.
"""
from __future__ import annotations

import re
import time
import uuid
from pathlib import Path
from typing import Any

from .config import DATA
from .store import load_json, save_json

CHATS = DATA / "chats"

# Enough to find a conversation again, not enough to fill the disk. A long
# session of worksheet answers can run to megabytes, and the model only ever
# sees the last few turns anyway.
MAX_STORED = 400


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def _path(chat_id: str) -> Path:
    # The id is ours, but a caller could pass anything; never leave the folder.
    return CHATS / f"{Path(chat_id).name}.json"


def title_for(messages: list[dict[str, Any]]) -> str:
    """Name a conversation after the first thing asked in it."""
    for m in messages:
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            text = re.sub(r"\s+", " ", m["content"]).strip()
            if text:
                return text[:48] + ("…" if len(text) > 48 else "")
    return "New chat"


def save(chat_id: str, messages: list[dict[str, Any]],
         title: str = "") -> None:
    """Write a conversation to disk. Empty ones are not worth keeping."""
    real = [m for m in messages if m.get("role") in ("user", "assistant")]
    if not real:
        return
    CHATS.mkdir(parents=True, exist_ok=True)
    existing = load_json(_path(chat_id), {})
    save_json(_path(chat_id), {
        "id": chat_id,
        "title": title or existing.get("title") or title_for(messages),
        "created": existing.get("created") or time.time(),
        "updated": time.time(),
        "messages": messages[-MAX_STORED:],
    })


def load(chat_id: str) -> list[dict[str, Any]]:
    """The messages of one conversation, or an empty list."""
    data = load_json(_path(chat_id), {})
    messages = data.get("messages")
    return messages if isinstance(messages, list) else []


def listing() -> list[dict[str, Any]]:
    """Every saved conversation, most recently used first."""
    if not CHATS.is_dir():
        return []
    out = []
    for f in CHATS.glob("*.json"):
        data = load_json(f, {})
        if not data.get("id"):
            continue
        out.append({
            "id": data["id"],
            "title": data.get("title") or "New chat",
            "updated": data.get("updated", 0),
            "turns": sum(1 for m in data.get("messages", [])
                         if m.get("role") == "user"),
        })
    out.sort(key=lambda c: c["updated"], reverse=True)
    return out


TRASH = DATA / "chats_trash"     # deleted chats, kept 30 days so a wrong tap can be undone


def delete(chat_id: str) -> bool:
    """Into the recycle folder, not erased: one tap deletes, so Undo must work."""
    p = _path(chat_id)
    if not p.exists():
        return False
    TRASH.mkdir(exist_ok=True)
    p.replace(TRASH / p.name)
    for old in TRASH.glob("*.json"):                       # tidy: older than 30 days
        if time.time() - old.stat().st_mtime > 30 * 86400:
            old.unlink(missing_ok=True)
    return True


def restore(chat_id: str) -> bool:
    t = TRASH / f"{Path(chat_id).name}.json"
    if not t.exists():
        return False
    t.replace(_path(chat_id))
    return True


def when(stamp: float) -> str:
    """"14:32", "Yesterday" or a date - whichever identifies it fastest."""
    if not stamp:
        return ""
    now = time.localtime()
    then = time.localtime(stamp)
    if then[:3] == now[:3]:
        return time.strftime("%H:%M", then)
    if (time.time() - stamp) < 48 * 3600:
        return "Yesterday"
    if then.tm_year == now.tm_year:
        return time.strftime("%d %b", then)
    return time.strftime("%d %b %y", then)
