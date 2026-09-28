"""Conversation history plus a small persistent fact store.

Short-term: the rolling message list for the current session.
Long-term:  data/memory.json - things you tell it to remember.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from .config import DATA
from .store import StoreError, load_json, save_json

MEM_FILE = DATA / "memory.json"


class Memory:
    def __init__(self, system_prompt: str, max_messages: int = 40):
        self.system_prompt = system_prompt
        self.max_messages = max_messages
        self.messages: list[dict[str, Any]] = []
        self.facts: dict[str, str] = self._load()
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

    def _load(self) -> dict[str, str]:
        try:
            return load_json(MEM_FILE, {})
        except StoreError as e:
            # Start the session empty rather than crash, but never save over
            # the damaged file - it has been quarantined for recovery.
            print(f"[memory: {e}]")
            return {}

    def _save(self) -> None:
        save_json(MEM_FILE, self.facts)

    def remember(self, key: str, value: str) -> None:
        self.facts[key] = value
        self._save()

    def forget(self, key: str) -> bool:
        if key in self.facts:
            del self.facts[key]
            self._save()
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
        if len(self.messages) <= self.max_messages:
            return
        while len(self.messages) > self.max_messages:
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
        holds nothing that changes between turns: the facts are read once per
        process (the remember tool writes the file, not self.facts), so they
        are fixed for its life. The clock is the volatile part. It goes on
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
        if self.facts:
            lines = "\n".join(f"- {k}: {v}" for k, v in self.facts.items())
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


def _stamp() -> str:
    return datetime.now().strftime("%A %d %B %Y, %H:%M")
