"""The same question twice: an index of past study answers, so asking again opens the
page already written instead of working it out from scratch.

Split out of study.py (31 Oct 2026); study.py imports all of it back.
"""
from __future__ import annotations

import re
import time

from .. import render
from ..config import DATA
from ..store import load_json, save_json


_ANSWER_INDEX = DATA / "answer_index.json"
_FILLER = {"please", "athena", "hey", "can", "could", "would", "you", "me", "for", "us", "now", "quickly"}


def _answer_key(question: str) -> str:
    from .. import chats
    words = [w for w in re.findall(r"[a-z0-9]+", question.lower()) if w not in _FILLER]
    project = (chats.ACTIVE or {}).get("id", "") if isinstance(chats.ACTIVE, dict) else ""
    return f"{project}|{' '.join(words)}"


def _reuse(question: str) -> str:
    if re.search(r"\b(again|fresh|redo|new answer|regenerate|differently|another way)\b", question, re.I):
        return ""
    hit = load_json(_ANSWER_INDEX, {}).get(_answer_key(question))
    path = render.ANSWERS / str((hit or {}).get("file", ""))
    if not hit or not path.is_file():
        return ""
    if render.AUTO_OPEN:
        try:
            import webbrowser
            webbrowser.open(path.as_uri())
        except Exception:
            pass
    opened = f"Here's your answer from earlier - say \"answer it again\" for a fresh one. [{path.name}]"
    return f"{hit.get('lead', '')}\n\n{opened}" if hit.get("lead") else opened


def _remember_answer(question: str, path, lead: str) -> None:
    try:
        index = load_json(_ANSWER_INDEX, {})
        index[_answer_key(question)] = {"file": path.name, "lead": lead, "t": time.time()}
        save_json(_ANSWER_INDEX, index)
    except Exception:
        pass
