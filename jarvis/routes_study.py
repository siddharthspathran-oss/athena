"""Shortcuts for questions and study: quick facts, explain-this, grounded answers from
your notes, lab reports, quizzes and modes.

Split out of routes.py (31 Oct 2026); route() there calls these.
"""
from __future__ import annotations

import re

from . import registry
from .routes_common import _G_ASK, _R_LEAD


# Questions about this machine, which look like study questions but are not.
# "What is my battery" must not open a page about electrochemistry.
_SYSTEM_TOPIC = re.compile(
    r"\b(battery|volume|time|date|day|weather|wifi|wi-fi|bluetooth|reminder|"
    r"reminders|alarm|file|files|folder|folders|downloads|documents|desktop|"
    r"explorer|pdf|song|track|playing|playlist|spotify|"
    r"whatsapp|outlook|email|screen|screenshot|window|windows|cpu|ram|disk|"
    r"storage|contact|contacts|my name|running|open apps)\b"
    # "what is my pc doing" is a question about this machine, and went to the
    # explainer, which spent 44 seconds writing an essay on what computers do
    # in general. Matched as whole phrases rather than by adding "pc",
    # "process" or "memory" to the words above: an engineering student really
    # does ask about the Poisson process, task scheduling and cache memory,
    # and those must still reach the explainer.
    r"|\b(?:my|this|the)\s+(?:pc|computer|laptop|machine|desktop)\b"
    r"|\btask\s*manager\b"
    r"|\b(?:tasks?|processes|programs|apps|applications)\s+(?:are\s+|is\s+)?"
    r"(?:running|open|active)\b"
    r"|\brunning\s+(?:tasks?|processes|programs|apps|applications)\b",
    re.I,
)


_STUDY_VERB = re.compile(
    r"^\W*(?:(?:athena|jarvis)\W+)?(?:please\s+|can you\s+|could you\s+)*"
    r"(?:explain|derive|solve|prove|calculate|compute|define|describe|"
    r"compare|contrast|simplify|integrate|differentiate|"
    r"summari[sz]e|teach me|walk me through|help me understand|"
    r"what is the difference between|difference between|how does|how do|"
    r"why does|why do|what causes)\b",
    re.I,
)


# "what is X" is a class question when X is not something on this PC.
_STUDY_WHAT = re.compile(
    r"^\W*(?:(?:athena|jarvis)\W+)?what\s+(?:is|are|was|were)\s+(?P<topic>.+)$", re.I)


# Whatever opens the question, so the rest can be looked at on its own.
_STUDY_PREFIX = re.compile(
    r"^\W*(?:(?:athena|jarvis)\W+)?(?:please\s+|can you\s+|could you\s+)*"
    r"(?:what\s+(?:is|are|was|were)|explain|calculate|compute|solve|"
    r"work out|tell me)\s+", re.I)


# A bare sum: digits, operators, and the words we say them with. No letters
# beyond those words and no "=", so an equation to solve is not caught.
_ARITHMETIC = re.compile(
    r"(?:[\d\s.,+\-*/^()%]|\b(?:plus|minus|times|multiplied|divided|by|of|"
    r"percent|into|and|point)\b)+", re.I)


# Things the model got wrong by answering from nothing or reaching for the
# wrong tool (28 Sep 2026): the time abroad, the song playing, the Wi-Fi
# network and the IP address.
_INFO_LEAD = r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please)\W+)*(?:(?:can|could)\s+you\s+)?(?:(?:tell|show)\s+me\s+)?"


_TIME_IN = re.compile(
    _INFO_LEAD + r"(?:what(?:'?s|\s+is)\s+the\s+time|what\s+time\s+is\s+it|(?:the\s+)?(?:current\s+|local\s+)?time)"
    r"\s+(?:right\s+now\s+|now\s+)?in\s+(?P<place>[a-z][a-z .'-]{1,30}?)(?:\s+(?:right\s+)?now)?\W*$", re.I)


_NOW_PLAYING = re.compile(
    _INFO_LEAD + r"(?:what(?:'?s|\s+is)\s+(?:this|the|that)\s+song(?:\s+(?:called|playing|name))?|"
    r"what\s+song\s+is\s+(?:this|playing|on|that)(?:\s+(?:right\s+)?now)?|what(?:'?s|\s+is)\s+playing(?:\s+(?:right\s+)?now)?|"
    r"which\s+song\s+is\s+(?:this|playing|on)|what\s+am\s+i\s+listening\s+to|name\s+(?:of\s+)?(?:this|the)\s+song|"
    r"who\s+(?:sings|sang|is\s+singing)\s+(?:this|this\s+song|the\s+song))\W*$", re.I)


_WIFI_NAME = re.compile(
    _INFO_LEAD + r"(?:(?:which|what)\s+(?:wi-?fi|network|wifi\s+network)\s+(?:am\s+i|are\s+we|is\s+(?:this|the)\s+(?:pc|laptop))\s+(?:connected|on)(?:\s+to)?|"
    r"what(?:'?s|\s+is)\s+(?:my|the)\s+(?:wi-?fi|network)(?:\s+name)?|am\s+i\s+connected\s+to\s+(?:the\s+)?(?:wi-?fi|internet))\W*$", re.I)


_IP = re.compile(
    _INFO_LEAD + r"(?:what(?:'?s|\s+is)\s+)?(?:my|the|this\s+(?:pc'?s|laptop'?s))\s+(?:(?:public|local|private)\s+)?ip(?:\s+address)?\W*$", re.I)


def _direct_info(text: str) -> tuple[str, dict] | None:
    t = text.strip()
    m = _TIME_IN.match(t)
    if m and not re.fullmatch(r"(?:the\s+)?(?:morning|evening|afternoon|night|here|india\s+time)", m.group("place"), re.I):
        return "world_time", {"place": m.group("place").strip()}
    if _NOW_PLAYING.match(t):
        return "now_playing", {}
    if _WIFI_NAME.match(t):
        return "pc_status", {"what": "wifi"}
    if _IP.match(t):
        return "pc_status", {"what": "ip"}
    return None


_G_MINE = re.compile(r"\b(?:my|i|me|mine|i'?ve|i'?m)\b", re.I)


_GROUNDS = (
    (r"\battendance\b|\bbunk|\bskip\b", "my_attendance", None),
    (r"\bteams?\b.*\b(?:meeting|class|lecture|call)s?\b|\bmeetings?\b", "teams_meetings", "day"),
    (r"\b(?:class|classes|lecture|lectures|timetable|lab)\b", "upes_classes", "day"),
    (r"\b(?:deadline|deadlines|assignment|assignments|due|submission|quiz|quizzes|homework|lms)\b", "lms_deadlines", None),
    (r"\b(?:task|tasks|to-?dos?)\b", "list_tasks", None),
    (r"\b(?:reminder|reminders|alarm|alarms|timer|timers)\b", "list_reminders", None),
    (r"\b(?:schedule|agenda|calendar|plans?|busy|free)\b", "daily_brief", "brief"),
    (r"\b(?:battery|charge|charging)\b", "battery", None),
    (r"\b(?:mail|mails|email|emails|inbox)\b", "inbox_summary", None),
)


def _direct_grounded(text: str) -> tuple[str, dict] | None:
    if not (_G_ASK.match(text) and _G_MINE.search(text)):
        return None
    day = ("next" if re.search(r"\bnext\b|\bwhen\b", text, re.I) else
           "tomorrow" if re.search(r"\btomorrow\b", text, re.I) else "today")
    for pattern, name, arg in _GROUNDS:
        if re.search(pattern, text, re.I) and registry.get(name) is not None:
            if arg == "day":
                return name, {"day": day}
            if arg == "brief":
                return name, {"day": "tomorrow" if day == "tomorrow" else "today", "full": False}
            return name, {}
    return None


def _direct_explain(text: str) -> tuple[str, dict] | None:
    """Send a class question to the explainer rather than the spoken reply.

    The ordinary voice answer is tuned to be short, which is wrong for
    anything with a derivation in it - and the LaTeX the model writes gets
    read aloud as "backslash frac backslash".
    """
    q = text.strip()
    if len(q.split()) < 3 or _SYSTEM_TOPIC.search(q):
        return None

    # "what is 2 plus 2" is a question, not a class question. The explainer
    # hands it to study.model, which reasons at length and renders a page -
    # thirty seconds and a browser tab to be told the answer is 4. Sums with
    # a letter or an equals sign in them ("solve x^2 - 5x + 6 = 0") are real
    # work and still go through.
    if _ARITHMETIC.fullmatch(_STUDY_PREFIX.sub("", q).strip(" ?.")):
        return None

    if _STUDY_VERB.match(q):
        return "explain", {"question": q}

    m = _STUDY_WHAT.match(q)
    # "what are you doing" is talk, not a topic (it rendered a study page, 3 Oct 2026).
    if m and len(m.group("topic").split()) >= 2 and not _QUICK_FACT.match(m.group("topic")) \
            and not re.match(r"(?:you|your|yours|we|i|me|my|he|she|they|it|this|that|there|up|going|happening|"
                             r"new|wrong|next|the\s+(?:matter|time|date|day|weather|plan|status|news))\b",
                             m.group("topic"), re.I):     # "what is going on" rendered a junk study page (16 Oct)
        return "explain", {"question": q}
    return None


# "what is the capital of france" is a one-line fact, not class work: the
# explainer sent it to study.model and opened a formatted page to say Paris.
_QUICK_FACT = re.compile(
    r"^(?:the\s+)?(?:capital|population|currency|president|prime\s+minister|ceo|founder|owner|"
    r"height|age|distance|price|cost|score|weather|full\s+form|spelling|synonym|"
    r"opposite|plural|birthday|address|phone\s+number|name|national\s+\w+|"
    r"official\s+language)\b", re.I)


def _direct_labreport(text: str) -> tuple[str, dict] | None:
    """ "make my lab report for experiment 5.1", "build my lab report" (tools/labreports.py)."""
    m = re.match(_R_LEAD + r"(?:make|build|create|write|generate|do|start|prepare|finish)\s+(?:my\s+|the\s+|a\s+)?"
                 r"lab\s+(?:report|record)(?:\s+(?:for|of|on)\s+(?P<x>.+?))?\W*$|"
                 + _R_LEAD + r"lab\s+(?:report|record)\s+(?:for|of|on)\s+(?P<x2>.+?)\W*$", text, re.I)
    if not m:
        return None
    x = re.sub(r"^(?:the|my)\s+", "", (m.group("x") or m.group("x2") or "").strip(), flags=re.I)
    return "lab_report", {"experiment": x}


def _direct_quiz(text: str) -> tuple[str, dict] | None:
    """ "quiz me on convolution", "test me on Fourier series with 8 questions" (quiz.py)."""
    m = re.match(_R_LEAD + r"(?:review|revise|go\s+through|do)\s+(?:my\s+)?(?:flash\s*cards?|what\s+i\s+(?:got\s+wrong|missed))"
                 r"(?:\s+(?:on|about|for)\s+(?P<t>.+?))?\W*$", text, re.I)
    if m:
        return "review_flashcards", {"topic": (m.group("t") or "").strip()}
    m = re.match(_R_LEAD + r"(?:quiz|test)\s+me(?:\s+(?:on|about|in|from))?\s+(?P<t>.+?)"
                 r"(?:\s+with\s+(?P<n>\d+)\s+questions?)?\W*$|"
                 + _R_LEAD + r"ask\s+me\s+(?:some\s+)?(?:(?P<n2>\d+)\s+)?questions\s+(?:on|about)\s+(?P<t2>.+?)\W*$", text, re.I)
    if not m:
        return None
    topic = re.sub(r"^(?:my|the)\s+", "", (m.group("t") or m.group("t2")).strip(), flags=re.I)
    n = int(m.group("n") or m.group("n2") or 5)
    return "quiz_me", {"topic": topic, "questions": n}


def _direct_answer(text: str) -> tuple[str, dict] | None:
    """"Open the answer (in the browser)", "show me the full answer": the newest
    study page. 13 Oct 2026: the model said "I've already opened it" and opened
    nothing."""
    if not re.match(_R_LEAD + r"(?:open|show|display|bring\s+up)\s+(?:me\s+)?(?:the\s+|yhe\s+|teh\s+|tge\s+|that\s+|this\s+|your\s+)?"
                    r"(?:full\s+|last\s+|latest\s+)?(?:answer|solution|working)(?:\s+page)?"
                    r"(?:\s+(?:again|in\s+(?:the\s+|my\s+)?(?:browser|chrome|edge)))*\W*$", text, re.I):
        return None
    from .render import ANSWERS
    pages = sorted(ANSWERS.glob("*.html"), key=lambda f: f.stat().st_mtime)
    return ("open_file", {"path": str(pages[-1])}) if pages else None


def _direct_mode(text: str) -> tuple[str, dict] | None:
    """Modes (modes.py): "coding mode", "switch to research mode", "normal
    mode", "what mode are you in". Study mode is the routine of that name,
    which switches the mode as one of its steps."""
    m = re.match(_R_LEAD + r"(?:(?:switch|change|go|get)\s+(?:back\s+)?(?:in)?to\s+|turn\s+on\s+|enter\s+|start\s+|use\s+)?"
                 r"(?P<m>coding|code|research|system|general|normal|default)\s+mode(?:\s+on)?\W*$|"
                 + _R_LEAD + r"(?:exit|leave|stop|turn\s+off|end)\s+(?:the\s+)?(?:coding|code|research|system)\s+mode\W*$|"
                 + _R_LEAD + r"(?P<q>what|which)\s+mode\s+(?:are\s+you|am\s+i)\s+in\W*$", text, re.I)
    if not m:
        return None
    if m.group("q"):
        return "set_mode", {"mode": "status"}
    return "set_mode", {"mode": (m.group("m") or "general").lower()}
