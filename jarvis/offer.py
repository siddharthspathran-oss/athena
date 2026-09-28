"""What Athena just offered to do, so a short reply can do it.

"I found 4 files for signals and systems - which one?" and "It comes up in
week3.pdf - want me to open it?" used to go nowhere: a bare "yes" was
dropped by the voice filter as too short, and had it arrived, the agent
treated "yes" as a fragment and said it had not caught that (28 Sep 2026).

A tool that asks sets the choices here; the next thing said is matched
against them first: yes / no, a number or an ordinal, "all", or words from
one of the names. Anything else clears the offer and is handled as a new
request. An offer lapses after OFFER_SECONDS.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass

OFFER_SECONDS = 90

_lock = threading.Lock()


@dataclass
class Choice:
    label: str          # what it is called when read out: "SS Unit 2.pdf"
    tool: str
    args: dict


_state: dict = {"choices": [], "t": 0.0}


def set_choices(choices: list[Choice]) -> None:
    with _lock:
        _state.update(choices=list(choices), t=time.time())


def pending() -> list[Choice]:
    with _lock:
        if _state["choices"] and time.time() - _state["t"] > OFFER_SECONDS:
            _state["choices"] = []
        return list(_state["choices"])


def clear() -> None:
    with _lock:
        _state["choices"] = []


_LEAD = r"^\W*(?:(?:athena|jarvis|ok|okay|um|uh|so|and|then|please)\W+)*"
YES = re.compile(_LEAD + r"(?:yes|yeah|yep|yup|ya|sure|ok|okay|alright|please|do\s+it|go\s+ahead|"
                 r"(?:yes\s+)?(?:please\s+)?open\s+(?:it|that|this|that\s+one|this\s+one)|"
                 r"that\s+one|this\s+one|of\s+course|definitely|why\s+not)"
                 r"(?:\W+(?:please|thanks|thank\s+you|open\s+it|do\s+it))*\W*$", re.I)
NO = re.compile(_LEAD + r"(?:no|nope|nah|don'?t|do\s+not|cancel|never\s*mind|leave\s+it|"
                r"not\s+now|no\s+thanks?|no\s+thank\s+you|skip\s+it|forget\s+it)"
                r"(?:\W+(?:thanks|thank\s+you|it|now))*\W*$", re.I)
ALL = re.compile(_LEAD + r"(?:(?:yes\W+)?(?:open\s+|show\s+)?(?:them\s+)?(?:all|both|every\s*one|"
                 r"all\s+of\s+them|them\s+all|all\s+(?:the\s+)?(?:files|of\s+those)|the\s+others|the\s+rest|"
                 r"(?:all\s+)?the\s+other\s+(?:ones|files))(?:\s+of\s+them)?)"
                 r"(?:\W+please)?\W*$", re.I)
_ORD = {"first": 1, "one": 1, "1": 1, "second": 2, "two": 2, "2": 2, "third": 3, "three": 3,
        "3": 3, "fourth": 4, "four": 4, "4": 4, "fifth": 5, "five": 5, "5": 5, "sixth": 6,
        "six": 6, "6": 6, "last": -1}
NUMBER = re.compile(_LEAD + r"(?:(?:open|show|play|use|the|number|no\.?|option)\s+)*"
                    r"(?P<n>first|second|third|fourth|fifth|sixth|last|one|two|three|four|five|six|[1-6])"
                    r"(?:st|nd|rd|th)?(?:\s+(?:one|file|option|please))*\W*$", re.I)

# What the voice loop should let through as an answer even though it would
# otherwise be too short to count as a command.
REPLY = re.compile("|".join(p.pattern for p in (YES, NO, ALL, NUMBER)), re.I)


def resolve(said: str) -> list[Choice] | str | None:
    """Match a reply against the pending offer.

    Returns the chosen choices, "no" for a refusal, or None when the reply
    is not an answer to the offer (it is then a new request)."""
    choices = pending()
    if not choices:
        return None
    t = said.strip()
    if NO.match(t):
        return "no"
    if ALL.match(t):
        return choices
    m = NUMBER.match(t)
    if m:
        n = _ORD[m.group("n").lower()]
        if n == -1:
            return [choices[-1]]
        return [choices[n - 1]] if 1 <= n <= len(choices) else None
    if YES.match(t):
        return [choices[0]]
    # "the unit two one", "open the lab manual" - words from one name only.
    words = {w for w in re.findall(r"[a-z0-9]+", t.lower())
             if w not in {"the", "open", "one", "file", "please", "a", "that", "this"}}
    if words:
        hits = [c for c in choices
                if len(words & set(re.findall(r"[a-z0-9]+", c.label.lower()))) / len(words) >= 0.6]
        if len(hits) == 1:
            return hits
    return None
