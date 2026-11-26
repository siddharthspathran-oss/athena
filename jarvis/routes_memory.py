"""Shortcuts for memory, reminders and routines: remember / forget / recall,
reminders, and running a routine by name.

Split out of routes.py (31 Oct 2026); route() there calls these.
"""
from __future__ import annotations

import re


# Memory controls. "remember that I ..." goes to the learned profile (it is
# about the user); "remember my wifi password is ..." is refused there by the
# secret filter, which is the point.
_MEM_SHOW = re.compile(
    r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please)\W+)*(?:what\s+do\s+you\s+(?:remember|know)\s+about\s+me|"
    r"what\s+have\s+you\s+(?:learned|learnt)\s+about\s+me|show\s+(?:me\s+)?(?:my\s+profile|your\s+memory|"
    r"what\s+you\s+remember)|what\s+do\s+you\s+remember)\W*$", re.I)


_MEM_FORGET = re.compile(
    r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please)\W+)*forget\s+(?:that\s+|about\s+|the\s+fact\s+that\s+)?(?P<x>.{3,})$", re.I)


_MEM_ADD = re.compile(
    r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please)\W+)*(?:remember|note|keep\s+in\s+mind)\s+that\s+(?P<x>.{6,})$", re.I)


# "what did we decide about Q6", "when did I ask you about the timetable",
# "did we talk about ltspice", "find our conversation about the pdf".
_MEM_SEARCH = re.compile(
    r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please)\W+)*(?:"
    r"what\s+did\s+(?:we|i|you)\s+(?:talk\s+about|discuss|decide|say|agree|conclude|find)"
    r"(?:\s+(?:about|on|regarding|for))?"
    r"|when\s+did\s+(?:i|we)\s+(?:ask(?:\s+you)?|talk|discuss|mention|tell\s+you)(?:\s+(?:about|for))?"
    r"|did\s+(?:we|i)\s+(?:ever\s+)?(?:talk|discuss|ask\s+you)(?:\s+about)?"
    r"|(?:find|search|look\s+up)\s+(?:our|my|the)?\s*(?:past\s+|old\s+|earlier\s+)?"
    r"(?:conversation|chat|discussion)s?\s+(?:about|on|where|when)"
    r"|remind\s+me\s+what\s+(?:we|you|i)\s+(?:said|decided|found|discussed)(?:\s+about)?"
    r")\s+(?P<q>.{3,})$", re.I)


_STYLE_CMD = re.compile(
    r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please)\W+)*(?:learn|study|update|analy[sz]e)\s+"
    r"(?:my|how\s+i)\s+(?:writing\s+style|write|style)\W*$"
    r"|^\W*(?:(?:athena|jarvis)\W+)?how\s+do\s+i\s+write\W*$", re.I)


# "remember my locker number 42", "save my wifi name as Home5G", "remember
# that my locker number is 42", "my locker number is 42, remember that".
# Sent to the model, qwen answered "Noted, locker number 42" without calling
# anything, twice, and the user got "no tool ran" (28 Sep 2026). A labelled
# fact is regular enough to parse here and save outright.
_MEM_VERB = (r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please)\W+)*(?:(?:can|could|would)\s+you\s+)?"
             r"(?:please\s+)?(?:remember|save|note(?:\s+down)?|store|keep\s+in\s+mind|write\s+down)"
             r"\s+(?:that\s+)?")


_LABEL = r"(?P<label>[a-z][\w'-]*(?:\s+[\w'-]+){0,5}?)"


_MEM_FACT_IS = re.compile(
    _MEM_VERB + r"(?:my|our|the)\s+" + _LABEL + r"\s*(?:\s(?:is|are|was|=|as)\s+|[:=]\s*)"
    r"(?P<value>\S.{0,150}?)\W*$", re.I)


# No "is": the value is whatever ends the sentence and has a digit in it -
# "locker number 42", "wifi name Home5G", "roll number 21BEC1023".
_MEM_FACT_BARE = re.compile(
    _MEM_VERB + r"(?:my|our|the)?\s*" + _LABEL
    + r"\s+(?P<value>[^\s]*\d[^\s]*(?:\s+[^\s]*\d[^\s]*)*)\W*$", re.I)


_MEM_FACT_AFTER = re.compile(
    r"^\W*(?:(?:athena|jarvis|hey|ok|okay)\W+)*(?:my|our)\s+" + _LABEL
    + r"\s+(?:is|are)\s+(?P<value>\S.{0,150}?)[\s,.;-]+(?:(?:please|so|and)\s+)?"
      r"(?:remember|save|note|store)(?:\s+(?:that|it|this))?\W*$", re.I)


# "what is my locker number", "what's my wifi name", "do you remember my
# roll number" - answered from saved facts. Only when one is actually saved
# under that name: otherwise "what is my X" stays with the other routers.
_MEM_ASK = re.compile(
    r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please)\W+)*(?:"
    r"what(?:'?s|\s+is|\s+was|\s+are)|tell\s+me|do\s+you\s+(?:remember|know)|"
    r"remind\s+me(?:\s+of|\s+what)?|recall)\s+(?:my|our|the)\s+(?P<q>.{2,60}?)"
    r"(?:\s+(?:is|again|please))*\W*$", re.I)


# "what did we talk about?", "what did I ask you yesterday", "recap" - no
# topic, so a search by meaning had nothing to look for; it searched for
# "yesterday", or for nothing (28 Sep 2026). recent_conversations reads the
# day's history instead.
_MEM_RECAP = re.compile(
    r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please|so)\W+)*(?:"
    r"what\s+(?:did|have|were)\s+(?:we|you|i)\s+(?:been\s+)?(?:talk(?:ing|ed)?|chat(?:ting|ted)?|"
    r"speak(?:ing)?|discuss(?:ing|ed)?)(?:\s+about)?"
    r"|what\s+did\s+i\s+ask(?:\s+you)?(?:\s+about)?"
    r"|(?:give\s+me\s+a\s+)?recap(?:\s+of\s+(?:our|the)\s+(?:conversation|chat))?"
    r")(?:\s+(?P<when>today|yesterday|earlier(?:\s+today)?|last\s+time|recently|before|so\s+far))?\W*$",
    re.I)


def _direct_memory(text: str) -> tuple[str, dict] | None:
    t = text.strip()
    for pattern in (_MEM_FACT_AFTER, _MEM_FACT_IS, _MEM_FACT_BARE):
        m = pattern.match(t)
        if m:
            label = re.sub(r"\s+", " ", m.group("label")).strip(" ,.")
            value = m.group("value").strip(" ,.")
            if label and value:
                return "remember", {"what": value, "label": label}
    m = _MEM_RECAP.match(t)
    if m:
        return "recent_conversations", {"when": (m.group("when") or "").strip().lower()}
    m = _MEM_ASK.match(t)
    if m:
        try:
            from .memory import find_facts
            if find_facts(m.group("q"), threshold=0.65):
                return "recall", {"query": m.group("q").strip(" ?.")}
        except Exception:
            pass
    if _MEM_SHOW.match(t):
        return "what_you_remember", {}
    m = _MEM_SEARCH.match(t)
    if m:
        return "search_past_conversations", {"query": m.group("q").strip(" ?.")}
    if _STYLE_CMD.match(t):
        return "learn_my_writing_style", {}
    m = _MEM_FORGET.match(t)
    if m:
        return "forget_about", {"thing": m.group("x").strip(" .")}
    m = _MEM_ADD.match(t)
    if m:
        return "remember_about_me", {"note": m.group("x").strip()}
    return None


# "any skill ideas", "what skills could we make" / "save idea 2 as uni stuff" (skillfinder.py).
# Anchored to the whole sentence: "search for skill ideas for my CV" is a web search.
_LEAD = r"^\W*(?:(?:hey|ok|okay|yes|yeah)\W+)?(?:(?:athena|jarvis)\W+)?"
_SKILL_IDEAS = re.compile(_LEAD + r"(?:(?:any|got\s+any|show\s+me|give\s+me|find(?:\s+me)?|some|more)\s+)?(?:new\s+)?"
                          r"(?:skill|routine)\s+ideas?\W*$|" + _LEAD + r"what\s+(?:skills|routines)\s+(?:could|can|should)"
                          r"\s+(?:we|you|i)\s+(?:make|create|build|save)\W*$", re.I)
_SAVE_IDEA = re.compile(_LEAD + r"(?:save|keep)\s+(?:skill\s+|routine\s+)?idea\s+(?:number\s+)?(?P<n>\d+|one|two|three|four|five)"
                        r"(?:\s+as\s+(?:a\s+)?(?:(?:skill|routine)\s+)?(?:(?:called|named)\s+)?(?P<name>[\w' -]{2,40}?))?\W*$",
                        re.I)
_SMALL = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}


def _direct_skill_ideas(text: str) -> tuple[str, dict] | None:
    m = _SAVE_IDEA.match(text)
    if m:
        n = m.group("n").lower()
        return "save_skill_idea", {"number": _SMALL.get(n) or int(n),
                                   **({"name": m.group("name").strip()} if m.group("name") else {})}
    return ("skill_ideas", {}) if _SKILL_IDEAS.match(text) else None


def _direct_routine(text: str) -> tuple[str, dict] | None:
    """"study mode" / "run study mode" -> run_routine, if one is saved."""
    try:
        from .tools import routines

        name = routines.find(text)
    except Exception:
        return None
    return ("run_routine", {"name": name}) if name else None


def _direct_reminder(text: str) -> tuple[str, dict] | None:
    """Turn an obvious reminder request straight into an add_reminder call."""
    from .tools.scheduler import parse_request

    parsed = parse_request(text)
    if parsed is None:
        return None
    return "add_reminder", {"text": parsed[0], "when": parsed[1]}
