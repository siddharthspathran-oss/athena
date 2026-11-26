"""Asking first: which calls need the user's OK (and who counts as a new recipient),
the yes / no answers within one request, text from elsewhere that makes outward
actions ask for a while, and windows only when the request asked for one.

Split out of agent.py (31 Oct 2026). Agent inherits Guardrails; the helpers are
imported back into agent.py, so agent.opens_asked etc. still work.
"""
from __future__ import annotations

import re
import json
import time
from . import offer, registry
from .config import cfg
from .agent_defs import Step


def _new_recipient(args: dict) -> bool:
    """A message or call going to a number that is not one of your contacts -
    or, for call_number, to anyone who is not (a business, a raw number)."""
    from .tools import messaging
    to = str(args.get("to") or args.get("who") or "").strip()
    if not to:
        return False
    digits = messaging._normalise_phone(to)
    if len(digits) >= 10:
        return not any(messaging._normalise_phone(c.get("phone", ""))[-10:] == digits[-10:]
                       for c in messaging._load().values())
    return messaging._find_contact(to) is None


# Words that ask for a window: a request without one opens nothing (Agent.hold_open).
_OPEN_ASK = re.compile(r"\b(?:open|show|launch|start|play|watch|search|go\s+to|take\s+me\s+to|visit|"
                       r"pull\s+up|bring\s+up|load)\b", re.I)


def opens_asked(text: str) -> int:
    """How many windows a request asks for: none without an open-word, else one
    per thing listed ("open YouTube and Gmail" is two)."""
    return 1 + len(re.findall(r"\band\b|,", text or "")) if _OPEN_ASK.search(text or "") else 0


class Guardrails:
    """Mixed into Agent (agent.py); see the module docstring."""

    def _needs_confirm(self, tool: registry.Tool, args: dict | None = None) -> bool:
        """Risk tiers (9 Oct 2026 audit):

          critical  every time: safety.ask_first (PowerShell, deleting) and
                    safety.always_confirm_tags (the RAGAZZA shop - an
                    unconfirmed "set stock to 50" looped six times, 27 Sep 2026)
          high      a message or call to someone not in your contacts; and
                    anything in registry.OUTWARD while a web page, email, file
                    or another app's text is in the conversation - it may
                    carry instructions written for Athena (prompt injection)
          low       everything else just runs, as before (confirm_destructive off)
        """
        if tool.name in (cfg.get("safety.ask_first", []) or []) or                 set(tool.tags) & set(cfg.get("safety.always_confirm_tags", []) or []):
            return True
        if tool.name in registry.OUTWARD and time.time() < self._untrusted_until:
            return True
        if tool.name in ("whatsapp_message", "whatsapp_call", "call_number") and _new_recipient(args or {}):
            return True
        return bool(tool.destructive and cfg.get("safety.confirm_destructive", True))

    def allowed(self, tool: registry.Tool, args: dict) -> bool:
        """Ask if this call needs it - once per request: the router asking
        and then Tool.run asking again would be the same question twice."""
        getattr(self, "_ran_tools", set()).add(tool.name)
        key = tool.name + json.dumps(args, sort_keys=True, default=str)
        if key in self._approved or not self._needs_confirm(tool, args):
            return True
        if key in self._refused:
            return False
        # On the HUD's live feed: what is waiting for your OK, and what was refused.
        self.on_step(Step(tool=f"permission: {tool.name}", args=args, result="asked"))
        ok = bool(self.confirm(tool.name, args))
        (self._approved if ok else self._refused).add(key)
        if not ok:
            self.on_step(Step(tool=f"blocked: {tool.name}", args=args, result="not done"))
        return ok

    def saw_untrusted(self) -> None:
        """Text someone else wrote is now in the conversation; it stays in the
        history for a while, so outward actions ask for the next 10 minutes."""
        self._untrusted_until = time.time() + float(cfg.get("safety.untrusted_minutes", 10)) * 60

    def hold_open(self, args: dict) -> str | None:
        """None if this window may open; otherwise what the model gets instead, so
        the link or file goes into the answer and nothing jumps in front of you.
        Opens only what the request asked for (opens_asked) - research, a
        planner step or a half-heard sentence opens nothing (28 Oct 2026)."""
        left = getattr(self, "_opens_left", 1)
        if left > 0:
            self._opens_left = left - 1
            return None
        what = args.get("url") or args.get("path") or args.get("name") or ""
        return ("Not opened - the user didn't ask for a window, or already has what they asked for open. "
                f"Don't try again; mention it in your answer instead: {what}")

    def _offer_instead(self, name: str, args: dict) -> bool:
        from . import offer
        from .habits import _label
        offer.set_choices([offer.Choice(_label(name, args), name, args)])
        return False

    def _declined(self, name: str, args: dict) -> str:
        """End the turn when a confirmation is refused or still pending.

        Fed back as "Cancelled by the user" and left to carry on, a 7B model
        read the word "cancel" as a cue and called cancel_reminder("all")
        seven times in a row (27 Sep 2026). Nothing further runs this turn.
        """
        from . import offer
        from .habits import _label
        note = "Not done: waiting for the user's approval."
        self.on_step(Step(tool=name, args=args, result=note))
        self.memory.add_raw({"role": "tool", "name": name, "content": note})  # every tool call gets its result
        answer = (f"That needs your OK first: {_label(name, args)}. Say yes to go ahead." if offer.pending()
                  else "I haven't done that - it needs your OK first.")
        self.memory.add("assistant", answer)
        return answer
