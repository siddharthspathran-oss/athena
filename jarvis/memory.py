"""Conversation history plus a small persistent fact store.

Short-term: the rolling message list for the current session.
Long-term:  data/memory.json - things you tell it to remember.
"""
from __future__ import annotations

import re
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any

from .config import DATA
from .store import StoreError, load_json, save_json

MEM_FILE = DATA / "memory.json"


# ---------------------------------------------------------------- saved facts
# Labelled facts ("locker: 42"), shared by every Athena process through
# data/memory.json. Each process used to read the file once, at start: a
# fact saved by voice was missing from the prompt - and from the web and
# hotkey windows - until each was restarted. Now it is re-read whenever the
# file changes, which is rare enough that the prompt prefix Ollama caches
# stays put between turns.
_facts_cache: dict[str, Any] = {"mtime": None, "data": {}}


def load_facts() -> dict[str, str]:
    try:
        mtime = MEM_FILE.stat().st_mtime
    except OSError:
        _facts_cache.update(mtime=None, data={})
        return {}
    if mtime != _facts_cache["mtime"]:
        try:
            data = load_json(MEM_FILE, {})
        except StoreError as e:
            # Keep what was loaded last rather than crash, and never save over
            # the damaged file - it has been quarantined for recovery.
            print(f"[memory: {e}]")
            return dict(_facts_cache["data"])
        _facts_cache.update(mtime=mtime, data=data if isinstance(data, dict) else {})
    return dict(_facts_cache["data"])


def save_facts(facts: dict[str, str]) -> None:
    save_json(MEM_FILE, facts)          # atomic: a crash never leaves it half-written
    _facts_cache.update(mtime=None)


_FILLER_WORDS = {"my", "the", "a", "an", "of", "is", "are", "was", "what", "whats", "where",
                 "when", "who", "me", "your", "our", "i", "do", "you", "know", "about",
                 "remember", "did", "tell", "that", "it", "s", "please", "for", "to"}


def _words(text: str) -> set[str]:
    out = set()
    for w in re.findall(r"[a-z0-9]+", text.lower().replace("'s", "").replace("’s", "")):
        if w in _FILLER_WORDS:
            continue
        out.add(w[:-1] if len(w) > 3 and w.endswith("s") else w)
    return out


def _norm(text: str) -> str:
    return " ".join(sorted(_words(text)))


def fact_score(query: str, key: str, value: str = "") -> float:
    """How well `query` names the fact saved under `key`, 0 to 1.

    recall("my birthday") used to miss a fact saved as "birthday", and
    "bday" or "birth date" never had a chance: only the exact label worked.
    """
    q, k = _norm(query), _norm(key)
    if not q:
        return 0.0
    if q == k:
        return 1.0
    qw, kw, vw = _words(query), _words(key), _words(value)
    seq = SequenceMatcher(None, q, k).ratio()
    # Shared words over the longer of the two, so "name" is only half of
    # "wifi name": asked "what is my name", it must not answer the wifi.
    on_key = len(qw & kw) / max(len(qw), len(kw) or 1)
    on_value = len(qw & vw) / len(qw)            # "which one is Home5G"
    return max(seq, on_key, 0.85 * on_value)


def covers(query: str, text: str) -> float:
    """Share of the query's words found in a longer text (profile lines)."""
    qw = _words(query)
    if not qw:
        return 0.0
    return max(len(qw & _words(text)) / len(qw),
               SequenceMatcher(None, _norm(query), _norm(text)).ratio())


def find_facts(query: str, threshold: float = 0.6) -> list[tuple[str, str, float]]:
    """Saved facts matching `query`, best first, as (key, value, score)."""
    hits = [(k, str(v), fact_score(query, k, str(v))) for k, v in load_facts().items()]
    return sorted((h for h in hits if h[2] >= threshold), key=lambda h: -h[2])


class Memory:
    def __init__(self, system_prompt: str, max_messages: int = 40):
        self.system_prompt = system_prompt
        self.max_messages = max_messages
        self.messages: list[dict[str, Any]] = []
        # The conversation this belongs to. None means nothing is written to
        # disk - which is what the voice and hotkey interfaces want, since a
        # spoken one-liner is not a conversation worth keeping.
        self.chat_id: str | None = None
        # What the model is shown, which is not what is kept. Trimming holds
        # the transcript inside the context window; the saved history is the
        # whole thing, so reopening a chat shows everything that was said.
        self.full: list[dict[str, Any]] = []
        # When each user message was sent, keyed by id() of its dict. Kept out
        # of the dict itself so it never reaches the saved chat on disk. The
        # dicts stay alive in self.full while their entry is here, so an id
        # cannot be reused under it; both are reset together.
        self._sent: dict[int, str] = {}

    # ---------- long term ----------

    @property
    def facts(self) -> dict[str, str]:
        return load_facts()

    def remember(self, key: str, value: str) -> None:
        facts = load_facts()
        facts[key] = value
        save_facts(facts)

    def forget(self, key: str) -> bool:
        facts = load_facts()
        if key in facts:
            del facts[key]
            save_facts(facts)
            return True
        return False

    # ---------- short term ----------

    def add(self, role: str, content: str, **extra: Any) -> None:
        msg = {"role": role, "content": content}
        msg.update(extra)
        if role == "user":
            self._sent[id(msg)] = _stamp()
        self.messages.append(msg)
        self.full.append(msg)
        self._trim()
        self.persist()
        if not self.chat_id:
            # Voice and popup turns are not saved as chats; note them so the
            # profile learner still hears what was said.
            from . import profile, recall
            profile.journal(role, content)
            recall.note_voice(role, content)

    def add_raw(self, msg: dict[str, Any]) -> None:
        self.messages.append(msg)
        self.full.append(msg)
        self._trim()
        self.persist()

    # ---------- saved conversations ----------

    def persist(self) -> None:
        """Write the conversation out, if it belongs to one.

        Called after every message rather than on close: the window can be
        killed, the machine can sleep, and a conversation half-saved is worse
        than useless. Writing a few kilobytes per turn costs nothing.
        """
        if not self.chat_id:
            return
        try:
            from . import chats
            chats.save(self.chat_id, self.full)
        except Exception:
            pass            # never lose a reply over a failed write

    def start_chat(self, chat_id: str | None = None) -> str:
        """Begin a new conversation, or resume a saved one."""
        from . import chats

        self._sent = {}
        if chat_id:
            self.full = chats.load(chat_id)
            # Only the tail goes to the model; the rest is history for the
            # person reading it.
            self.messages = self.full[-self.max_messages:]
            while self.messages and self.messages[0].get("role") == "tool":
                self.messages.pop(0)
        else:
            chat_id = chats.new_id()
            self.full = []
            self.messages = []
        self.chat_id = chat_id
        return chat_id

    def _trim(self) -> None:
        """Keep the transcript inside the context window, dropping oldest first.

        An assistant turn carrying tool_calls must stay paired with the tool
        results that answer it, so trimming happens in whole turns: drop from
        the front, then discard any tool results left orphaned at the start.
        """
        budget = _history_budget()
        if len(self.messages) <= self.max_messages and _size(self.messages) <= budget:
            return
        while len(self.messages) > self.max_messages:
            self.messages.pop(0)
        # Forty messages is no limit when tool results run to 4,000 characters
        # each: the history outgrew the context window, and Ollama then cut
        # the prompt itself - from wherever it liked. Drop oldest first, but
        # never the request being worked on or anything after it.
        while _size(self.messages) > budget:
            current = max((i for i, m in enumerate(self.messages) if m.get("role") == "user"),
                          default=len(self.messages) - 1)
            if current <= 0:
                break
            self.messages.pop(0)
        # a leading 'tool' message whose assistant call was just dropped is
        # meaningless to the model and rejected by some backends
        while self.messages and self.messages[0].get("role") == "tool":
            self.messages.pop(0)

    def clear(self) -> None:
        self.messages = []
        self.full = []
        self._sent = {}

    # ---------- assembly ----------

    def build(self) -> list[dict[str, Any]]:
        """Full message list handed to the model.

        Laid out so each call is the previous one with new text on the end,
        because Ollama reuses its KV cache only for a byte-identical prefix,
        and qwen2.5's template renders the system message and then ~4k tokens
        of tool definitions ahead of everything else. So the system message
        holds nothing that changes between turns: saved facts and the profile
        are re-read only when their files change, which is rare. The clock is the volatile part. It goes on
        each user message as that message is sent and never changes after, so
        earlier turns are not re-read either. A separate system message near
        the end would not have worked: Ollama merges every system message
        into the one at the top.

        Measured on qwen2.5:7b, 5.5k-token prompt, first question after the
        clock minute rolled over: 3.3 s wall / 2.8 s prompt eval with the time
        in the system message, 0.5-0.7 s / 0.15-0.3 s with it here (only the
        ~50 new tokens are read; a warm repeat is 0.35 s / 0.03 s).
        """
        sys = self.system_prompt
        facts = load_facts()
        if facts:
            lines = "\n".join(f"- {k}: {v}" for k, v in facts.items())
            sys += f"\n\nThings the user has asked you to remember:\n{lines}"
        # What has been learned across earlier chats. Re-read only when the
        # file changes (a few times a day at most), so the prefix stays put.
        from . import profile
        learned = profile.prompt_text()
        if learned:
            sys += ("\n\nWhat you know about the user from earlier conversations "
                    "(use it; don't recite it):\n" + learned)
        out: list[dict[str, Any]] = [{"role": "system", "content": sys}]
        stamped = False
        for m in self.messages:
            sent = self._sent.get(id(m))
            if sent:
                m = {**m, "content": f"{m['content']}\n[{sent}]"}
                stamped = True
            out.append(m)
        # A resumed chat's messages predate this process and carry no stamp;
        # give the latest question the current time so the model has a clock.
        if not stamped:
            for i in range(len(out) - 1, 0, -1):
                if out[i].get("role") == "user":
                    out[i] = {**out[i], "content": f"{out[i]['content']}\n[{_stamp()}]"}
                    break
        return out


def _size(messages: list[dict[str, Any]]) -> int:
    return sum(len(str(m.get("content") or "")) + len(str(m.get("tool_calls") or ""))
               for m in messages)


def _history_budget() -> int:
    """How many characters of conversation the model is shown (llm.history_chars).

    About four characters a token: 12,000 is ~3k tokens, which leaves the
    system prompt, the tools this request needs and the reply room inside an
    8k window.
    """
    from .config import cfg

    try:
        return int(cfg.get("llm.history_chars", 12000))
    except (TypeError, ValueError):
        return 12000


def _stamp() -> str:
    return datetime.now().strftime("%A %d %B %Y, %H:%M")
