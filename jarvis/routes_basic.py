"""Shortcuts for the PC itself: apps, the brief, the screen, volume, tasks, places,
power, and "it" follow-ups.

Split out of routes.py (31 Oct 2026); route() there calls these.
"""
from __future__ import annotations

import re

from . import offer
from .routes_common import _QUESTION_WORD, _best_file


# Opening and closing apps is the most-used command and the one speech mangles
# most, because app names are proper nouns with common-English neighbours. The
# model was picking open_file for "and Claude" and inventing apps for "Lord
# App". Resolving the name against what is actually installed is both more
# accurate than the model and two round-trips faster.
_CLOSE_VERBS = {"close", "quit", "exit", "kill", "stop", "shut", "shut down"}


_APP_CMD = re.compile(
    r"^\W*(?:(?:athena|jarvis|and|um|uh|so|now|then|just|please|ok|okay|hey)\W+)*"
    r"(?:(?P<verb>open|launch|start|run|close|quit|exit|kill|"
    r"shut\s*down|shut|stop)\s+)?"
    r"(?:up\s+)?(?:the\s+|my\s+)?"
    r"(?P<name>[a-z0-9][a-z0-9 .+&'_-]{0,40}?)"
    r"(?:\s+(?:app|application|program|window))?"
    r"\W*$",
    re.I,
)


def _direct_app(text: str) -> tuple[str, dict] | None:
    """Turn 'open X' / 'close X' into a tool call when X is a real app.

    Falls through to the model whenever the name does not resolve, so
    "open experiment 4.1" still reaches the file tools.
    """
    m = _APP_CMD.match(text.strip())
    if not m:
        return None
    name = (m.group("name") or "").strip()
    verb = (m.group("verb") or "").strip().lower()
    if not name:
        return None
    # A lone verb or filler is not an app: a cut-off "open" launched OpenGL
    # Switcher, the nearest name starting with "open" (27 Sep 2026).
    if name.lower().strip(" .,!?") in ("open", "close", "launch", "start", "quit", "exit", "run",
                                       "play", "stop", "it", "the", "this", "that", "a", "an", "app"):
        return None

    # A bare app name is a fair instruction - "Spotify" means open it - but
    # only when it is short and not a question.
    if not verb:
        if len(name.split()) > 3 or _QUESTION_WORD.match(name):
            return None
        # An answer, not an app: a bare "no" opened Node.js (4 Oct 2026).
        if offer.YES.match(name) or offer.NO.match(name) or re.fullmatch(
                r"\W*(?:nah|ok|okay|yes|no|yeah|yep|nope|sure|fine|cool|thanks?|thank\s+you|done|right|good|now|wait|hmm|huh|what|hello|hi|hey|later|so|well)\W*", name, re.I):
            return None

    # _match_app falls back to fuzzy matching, which is right when the model
    # has already decided this is an app but far too loose here: it matched
    # "lock my screen" to an installed program. Routing away from the model
    # needs certainty, so require the resolved name to really correspond.
    try:
        from .tools.system import APP_ALIASES, _match_app, _user_aliases

        hit = _match_app(name)
        if hit is None:
            return None
        low = name.lower()
        want = (_user_aliases().get(low) or APP_ALIASES.get(low) or low).lower()
        found = hit[0].lower()
        if not (found == want or found.startswith(want) or want in found):
            return None
        # "open scheduler" launched Task Scheduler on the word inside its
        # name while scheduler.pdf sat in Downloads. A file named exactly
        # that beats an app that merely contains the word.
        if (verb not in _CLOSE_VERBS and found != want
                and not found.startswith(want) and _exact_personal_file(name)):
            return None
    except Exception:
        return None

    tool = "close_app" if verb in _CLOSE_VERBS else "open_app"
    return tool, {"name": name}


def _exact_personal_file(name: str) -> bool:
    """True if a file in the user's own folders is named exactly this."""
    b = _best_file(name)
    return bool(b and b.entry.rank == 0 and not b.entry.is_dir
                and b.coverage == 1.0 and b.extra == 0)


_BRIEF = re.compile(
    r"^\W*(?:(?:athena|jarvis)\W+)?(?:please\s+|can you\s+)*"
    # "what's" arrives as "whats" as often as not - Whisper drops the
    # apostrophe and so does anyone typing quickly.
    r"(?:"
    r"(?:what'?s?(?:\s+is|\s+does)?\s+(?:my|the)\s+(?:(?:full|whole|entire|complete|remaining|rest\s+of\s+(?:my|the))\s+)?(?:day|schedule|calendar|"
    r"timetable|agenda|plan)\b)"
    r"|(?:what'?s?(?:\s+is)?\s+(?:on|up|happening)\s+(?:today|tomorrow)\b)"
    # "what's on my calendar today" checked the date and said "no events".
    r"|(?:what'?s?(?:\s+is)?\s+(?:on|in)\s+(?:my|the)\s+(?:calendar|schedule|agenda|timetable)\b)"
    r"|(?:(?:check|show|open|read)\s+(?:me\s+)?(?:my|the)\s+(?:calendar|schedule|agenda)\b)"
    r"|(?:do\s+i\s+have\s+(?:any(?:thing)?\s+)?(?:classes|lectures|meetings|events|plans)\b)"
    r"|(?:what\s+(?:have i|do i have)\s+(?:on|got)\b)"
    r"|(?:(?:brief|breathe|breef|brieve)\s+me\b)|(?:my\s+brief\b)|(?:daily\s+brief\b)"
    r"|(?:what\s+(?:classes|lectures|meetings)\s+(?:do i have|are)\b)"
    r"|(?:am i\s+free\b)"
    r")", re.I)


_TOMORROW = re.compile(r"\btomorrow\b", re.I)


def _direct_brief(text: str) -> tuple[str, dict] | None:
    """Turn "what's my day" into the brief, rather than a guess at a tool.

    The model reaches for list_reminders or answers from nothing at all when
    asked this, because the question does not name a tool. The phrasing is
    regular enough to route outright.
    """
    if not _BRIEF.match(text.strip()):
        return None
    day = "tomorrow" if _TOMORROW.search(text) else "today"
    # Today's brief skips what is already over; "full", "whole day", "all my
    # classes" or "including earlier" asks for everything.
    full = bool(re.search(r"\b(full|whole|entire|all\s+(?:my\s+|of\s+(?:my|the)\s+|the\s+)?"
                          r"(?:classes|lectures|day|schedule)|including|earlier|already)\b",
                          text, re.I))
    return "daily_brief", {"day": day, "full": full}


# Everyday one-liners that should never need the model: music keys, volume,
# screenshot, lock. "play music" used to reach the model - and, joined to
# "open Spotify", the planner - for what is one media key.
_BASIC_LEAD = r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please|now|just|and|then)\W+)*(?:(?:can|could|would)\s+you\s+)?(?:please\s+)?"


_BASIC = [
    (re.compile(_BASIC_LEAD + r"(?:play|put\s+on|start|open)\s+(?:(?:my|the)\s+)?(?:spotify\s+)?playlist"
                r"(?:\s+(?:in|on|from)\s+(?:my\s+)?spotify)?(?:\s+that\s+you\s+(?:have|know|saved|remember).*)?\W*$", re.I),
     "play_my_playlist", {}),
    (re.compile(_BASIC_LEAD + r"(?:check|scan|look\s+for|find|any)\s+(?:my\s+|for\s+|new\s+)*"
                r"(?:deadlines?|assignments?|due\s+dates?)(?:\s+in\s+my\s+(?:mail|email|inbox))?\W*$", re.I),
     "check_deadlines", {}),
    # A song Athena picks from what you listen to. Before "play music",
    # which only presses play on whatever was paused.
    (re.compile(_BASIC_LEAD + r"(?:"
                r"(?:play\s+(?:me\s+)?)?(?:(?:a|some|any)\s+)?(?:music|songs?|tracks?|something|anything)?\s*"
                r"(?:of|from|by|at)?\s*your\s+(?:own\s+)?(?:choice|pick|liking|wish)"
                r"|play\s+(?:me\s+)?(?:something|anything|a\s+song|some(?:thing)?\s+(?:music|songs?)|a\s+track)\s+"
                r"(?:(?:that\s+)?i\s+(?:like|love|listen\s+to|would\s+like)|for\s+me|(?:that\s+)?you\s+(?:like|love|prefer|think\s+i'?d\s+like|choose)|good|nice|random)"
                r"|play\s+(?:me\s+)?(?:something|anything|a\s+(?:random\s+)?(?:song|track)|any\s+(?:song|track|music)|random\s+(?:music|songs?))"
                r"|play\s+(?:me\s+)?(?:my\s+)?(?:kind|type|style)\s+of\s+(?:music|songs?)|play\s+(?:me\s+)?my\s+(?:vibe|taste|style)"
                r"|surprise\s+me(?:\s+with\s+(?:a\s+song|(?:some\s+)?music))?"
                r"|(?:choose|pick)\s+(?:a\s+|some\s+)?(?:song|music|track)(?:\s+for\s+me)?"
                r")\W*$", re.I), "play_my_taste", {}),
    (re.compile(_BASIC_LEAD + r"(?:play|resume|start|put\s+on)\s+(?:some\s+|the\s+|my\s+)?(?:music|songs?|playback)"
                r"(?:\s+on\s+spotify)?\W*$", re.I), "music", {"action": "play"}),
    (re.compile(_BASIC_LEAD + r"(?:pause|stop)\s+(?:the\s+)?(?:music|song|playback|spotify)\W*$|"
                + _BASIC_LEAD + r"pause\W*$", re.I), "music", {"action": "pause"}),
    (re.compile(_BASIC_LEAD + r"(?:next|skip)(?:\s+(?:the\s+)?(?:song|track|this))?\W*$|"
                + _BASIC_LEAD + r"(?:play\s+the\s+)?next\s+(?:song|track)\W*$", re.I), "music", {"action": "next"}),
    (re.compile(_BASIC_LEAD + r"(?:previous|last|go\s+back\s+a)\s+(?:song|track)\W*$", re.I),
     "music", {"action": "previous"}),
    (re.compile(_BASIC_LEAD + r"take\s+a\s+screenshot\W*$|" + _BASIC_LEAD + r"screenshot\W*$", re.I),
     "take_screenshot", {}),
    (re.compile(_BASIC_LEAD + r"lock\s+(?:the\s+|my\s+)?(?:screen|pc|computer|laptop)\W*$", re.I),
     "lock_screen", {}),
]


# The Command Center's menu, and the same questions asked aloud: answered by
# their tool outright. "list my routines" and "what is my pc doing" went to
# the model and took seconds; from a menu click that reads as broken.
_BASIC += [
    # Gaming mode off: load the model back. Checked before "on" below.
    (re.compile(_BASIC_LEAD + r"(?:(?:turn|switch|shut)\s+off|stop|exit|end|disable|leave|quit|cancel)\s+(?:the\s+)?gam(?:e|er|ing)\s+mode\W*$|"
                + _BASIC_LEAD + r"gam(?:e|er|ing)\s+mode\s+(?:off|over|done|finished)\W*$|"
                + _BASIC_LEAD + r"(?:i'?m\s+)?done\s+(?:gaming|playing)\W*$|"
                + _BASIC_LEAD + r"(?:back\s+to\s+|switch\s+to\s+)?normal\s+mode\W*$|"
                + _BASIC_LEAD + r"(?:re)?load\s+(?:the\s+|your\s+)?(?:ai\s+)?models?(?:\s+back)?\W*$", re.I),
     "load_gpu", {}),
    # Frees the GPU for a game or an editor. Routed, so it works even when the
    # model itself is the thing filling the VRAM.
    (re.compile(_BASIC_LEAD + r"(?:(?:turn\s+on|start|enable|switch\s+to|go\s+(?:in)?to|enter)\s+)?gam(?:e|er|ing)\s+mode(?:\s+on)?\W*$|"
                + _BASIC_LEAD + r"(?:i'?m\s+(?:going\s+to\s+|gonna\s+)?(?:start\s+)?(?:gaming|playing\s+(?:a\s+)?games?)|time\s+to\s+game)(?:\s+now)?\W*$|"
                + _BASIC_LEAD + r"(?:free|clear|release|empty)\s+(?:up\s+)?(?:the\s+|my\s+|some\s+)?(?:gpu|vram|graphics\s+(?:card|memory)|video\s+memory)\W*$|"
                + _BASIC_LEAD + r"unload\s+(?:the\s+|all\s+(?:the\s+)?|your\s+)?(?:ai\s+)?models?\W*$", re.I),
     "free_gpu", {}),
    (re.compile(_BASIC_LEAD + r"(?:list|show|what\s+are)\s+(?:me\s+)?(?:my\s+|the\s+|your\s+)?"
                r"(?:routines|workflows)\W*$|" + _BASIC_LEAD + r"(?:my\s+)?(?:routines|workflows)\W*$", re.I),
     "list_routines", {}),
    (re.compile(_BASIC_LEAD + r"(?:list|show|what\s+are)\s+(?:me\s+)?(?:my\s+)?reminders\W*$|"
                + _BASIC_LEAD + r"(?:my\s+)?reminders\W*$", re.I), "list_reminders", {}),
    (re.compile(_BASIC_LEAD + r"(?:list|show|what\s+are|what'?s\s+on)\s+(?:me\s+)?(?:my\s+)?(?:tasks|to-?dos?|to-?do\s+list)"
                r"(?:\s+(?:for\s+)?today)?\W*$|" + _BASIC_LEAD + r"(?:my\s+)?(?:tasks|to-?do\s+list)\W*$|"
                + _BASIC_LEAD + r"what\s+do\s+i\s+(?:need|have)\s+to\s+do(?:\s+today)?\W*$", re.I), "list_tasks", {}),
    (re.compile(_BASIC_LEAD + r"(?:what\s+(?:tools|skills)\s+do\s+you\s+have|what\s+can\s+you\s+do|"
                r"(?:list|show)\s+(?:me\s+)?(?:your\s+)?(?:tools|skills)|tools\s+(?:and|&)\s+skills)\W*$", re.I),
     "list_skills", {}),
    (re.compile(_BASIC_LEAD + r"(?:what\s+is\s+my\s+(?:pc|computer|laptop)\s+doing|system\s+report|"
                r"(?:give\s+me\s+a\s+)?(?:pc|system)\s+(?:report|status))(?:\s+right\s+now)?\W*$", re.I),
     "system_report", {}),
]


# Screen awareness: questions about what is on the screen right now.
# The words themselves, not an opinion on them: Windows OCR, no vision model (9 Oct 2026).
_SCREEN_TEXT = re.compile(
    _BASIC_LEAD + r"(?:(?:read(?:\s+out)?|copy|get|grab|extract)\s+(?:all\s+)?(?:the\s+)?(?:text|words)\s+(?:on|from|in|off)\s+"
    r"(?:my|the|this)\s+(?:(?:whole|entire|full)\s+)?(?:screen|window)|ocr\s+(?:my|the|this)\s+(?:screen|window)|"
    r"what\s+does\s+(?:my|the)\s+screen\s+say)\W*$", re.I)


_SCREEN = re.compile(
    _BASIC_LEAD + r"(?:"
    r"what(?:'?s|\s+is)\s+(?:on|in)\s+(?:my|the|this)\s+screen|what\s+am\s+i\s+(?:looking\s+at|seeing|doing)|"
    r"(?:look|glance)\s+at\s+(?:my|the|this)\s+(?:(?:whole|entire|full)\s+)?screens?|can\s+you\s+see\s+(?:my|the)\s+screen|check\s+my\s+screen|"
    r"(?:read|summari[sz]e|explain|describe|translate)\s+(?:this|that|what'?s\s+on\s+(?:my|the)\s+screen|my\s+screen|"
    r"(?:this|the)\s+(?:page|screen|error|message|window|code|article|email|document|question|chart|graph|table))|"
    r"what\s+does\s+(?:this|that)\s+(?:say|mean)|help\s+me\s+with\s+(?:this|what'?s\s+on\s+(?:my|the)\s+screen)|"
    r"is\s+there\s+(?:an?\s+)?(?:error|problem|mistake)\s+on\s+(?:my|the)\s+screen"
    r")\b.*$", re.I)


_WHOLE = re.compile(r"\b(?:whole|entire|full|all)\s+(?:screen|screens|desktop|monitors?)\b", re.I)


# Battery, time and date: a tool answers each instantly; the model took
# seconds to decide to call it.
_BASIC += [
    (re.compile(_BASIC_LEAD + r"(?:(?:what(?:'?s|\s+is)|how(?:'?s|\s+is)|check)\s+(?:my\s+|the\s+)?)?"
                r"(?:battery|charge)(?:\s+(?:level|status|percentage|percent|life|left|at|now))*\W*$|"
                + _BASIC_LEAD + r"how\s+much\s+(?:battery|charge)(?:\s+(?:do\s+i\s+have|is\s+left|left|"
                r"have\s+i\s+got))?(?:\s+left)?\W*$|"
                + _BASIC_LEAD + r"(?:am\s+i|is\s+(?:my|the)\s+(?:laptop|pc|computer)|are\s+we)\s+"
                r"(?:charging|plugged\s+in|on\s+battery)\W*$", re.I),
     "battery", {}),
    (re.compile(_BASIC_LEAD + r"(?:what(?:'?s|\s+is)\s+the\s+time(?:\s+now)?|what\s+time\s+is\s+it(?:\s+now)?|"
                r"(?:tell\s+me\s+)?the\s+time(?:\s+please)?|time\s+(?:now|please)|"
                r"what'?s\s+the\s+time\s+right\s+now)\W*$", re.I),
     "get_datetime", {"what": "time"}),
    (re.compile(_BASIC_LEAD + r"(?:what(?:'?s|\s+is)\s+(?:the\s+date|today'?s\s+date)(?:\s+today)?|"
                r"what\s+(?:day|date)\s+is\s+it(?:\s+today)?|what\s+is\s+today|which\s+day\s+is\s+(?:it|today))\W*$", re.I),
     "get_datetime", {"what": "date"}),
]


# Disk, RAM, CPU and uptime: read from the system. Left to the model, "how
# much free space is on my C drive" came back as an invented figure.
_ASK = r"(?:(?:how\s+much|what(?:'?s|\s+is)(?:\s+(?:the|my))?|check(?:\s+(?:my|the))?|show(?:\s+me)?(?:\s+(?:my|the))?|tell\s+me(?:\s+(?:my|the))?)\s+)?"


_ON_PC = r"(?:\s+(?:on|in|of)\s+(?:my\s+|the\s+|this\s+)?(?:[a-z]\s*(?::|\s+drive)|laptop|pc|computer|disk|drive|system|ssd))?"


_BASIC += [
    (re.compile(_BASIC_LEAD + _ASK + r"(?:free\s+|available\s+|remaining\s+)?(?:disk\s+|storage\s+|drive\s+|hard\s+drive\s+)?"
                r"(?:space|storage)(?:\s+(?:is|do\s+i\s+have|have\s+i\s+got))?(?:\s+(?:left|free|available|remaining))?"
                + _ON_PC + r"(?:\s+(?:left|free|available|remaining))?\W*$|"
                + _BASIC_LEAD + r"(?:is\s+(?:my\s+)?(?:disk|drive|storage|[a-z]\s+drive)\s+(?:full|almost\s+full))\W*$", re.I),
     "pc_status", {"what": "disk"}),
    (re.compile(_BASIC_LEAD + _ASK + r"(?:ram|memory\s+usage|ram\s+usage|(?:ram|memory)\s+(?:is\s+)?(?:used|in\s+use|free|left|available))"
                + _ON_PC + r"(?:\s+(?:am\s+i\s+using|is\s+(?:used|free|left)|left|free|used))?\W*$|"
                + _BASIC_LEAD + r"how\s+much\s+(?:ram|memory)\s+(?:am\s+i\s+using|is\s+(?:being\s+)?used|is\s+(?:free|left)|do\s+i\s+have(?:\s+(?:left|free))?|left|free)"
                + _ON_PC + r"\W*$", re.I),
     "pc_status", {"what": "memory"}),
    (re.compile(_BASIC_LEAD + _ASK + r"(?:cpu|processor)\s*(?:usage|use|load|at)?" + _ON_PC + r"\W*$|"
                + _BASIC_LEAD + r"how\s+busy\s+is\s+(?:my\s+|the\s+)?(?:cpu|processor)\W*$", re.I),
     "pc_status", {"what": "cpu"}),
    (re.compile(_BASIC_LEAD + r"(?:(?:what(?:'?s|\s+is)\s+(?:my\s+|the\s+)?)?uptime|how\s+long\s+(?:has|have)\s+(?:my\s+|the\s+|this\s+)?"
                r"(?:pc|laptop|computer|system|i)\s+been\s+(?:on|running|up))\W*$", re.I),
     "pc_status", {"what": "uptime"}),
]


_BASIC.append((re.compile(_BASIC_LEAD + r"(?:hang\s+up|end\s+(?:the\s+)?call|cut\s+(?:the\s+)?call|disconnect(?:\s+the\s+call)?)"
                          r"(?:\s+now)?\W*$", re.I), "end_call", {}))


_VOLUME = re.compile(_BASIC_LEAD + r"(?:(?:set|turn|put|change)\s+(?:the\s+)?)?volume\s+(?:up\s+|down\s+)?(?:to\s+)?"
                     r"(\d{1,3})(?:\s*%|\s+percent)?\W*$", re.I)


# "volume up", "turn it down a bit", "louder" - went to the model, which had to
# look the level up first (28 Oct 2026).
_VOLUME_STEP = re.compile(_BASIC_LEAD + r"(?:(?:turn|put)\s+(?:the\s+)?(?:volume|sound|it)\s+(?P<a>up|down)|"
                          r"turn\s+(?P<d>up|down)\s+(?:the\s+)?(?:volume|sound)|"
                          r"volume\s+(?P<b>up|down)|(?:make\s+it\s+)?(?P<c>louder|quieter|softer))"
                          r"(?:\s+(?:a\s+)?(?:bit|little|more))*(?:\s+please)?\W*$", re.I)
# "what is the volume" - the model answered "[get_volume result] 60%" without calling
# anything, at 94% (31 Oct 2026); a plain question needs no model.
_VOLUME_ASK = re.compile(_BASIC_LEAD + r"(?:what'?s|what\s+is|how\s+(?:loud|high)\s+is)\s+(?:the\s+|my\s+)?(?:volume|sound)"
                         r"(?:\s+(?:level|at|now|right\s+now|set\s+to))*\W*$|^\W*(?:current\s+)?volume\s+level\W*$", re.I)


_ADD_TASK = re.compile(
    _BASIC_LEAD + r"(?:add|put|create|new)\s+(?:a\s+)?(?:task|to-?do)(?:\s+to\s+(?:my\s+)?(?:list|tasks))?\s*[:,-]?\s+(?P<t>.+?)"
    r"(?:\s+(?:for|by|due)\s+(?P<d>today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday))?\W*$|"
    + _BASIC_LEAD + r"add\s+(?P<t2>.+?)\s+to\s+my\s+(?:tasks|to-?do(?:\s+list)?|list)"
    r"(?:\s+(?:for|by|due)\s+(?P<d2>today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday))?\W*$", re.I)


def _direct_basic(text: str) -> tuple[str, dict] | None:
    t = text.strip()
    if re.match(_BASIC_LEAD + r"(?:(?:give\s+me\s+)?(?:my\s+|the\s+)?evening\s+(?:summary|brief|report|wrap[\s-]?up)|"
                r"wrap\s+up\s+(?:my|the)\s+day|how\s+did\s+(?:my\s+)?(?:today|day)\s+go)\W*$", t, re.I):
        return "evening_summary", {}
    if _SCREEN_TEXT.match(t):
        return "read_text", {"whole_screen": bool(_WHOLE.search(t))}
    if _SCREEN.match(t):
        return "look_at_screen", {"question": t, "whole_screen": bool(_WHOLE.search(t))}
    m = _ADD_TASK.match(t)
    if m:
        return "add_task", {"text": m.group("t") or m.group("t2"),
                            "due": m.group("d") or m.group("d2") or ""}
    m = _VOLUME.match(t)
    if m:
        return "set_volume", {"level": min(100, int(m.group(1)))}
    m = _VOLUME_STEP.match(t)
    if m:
        up = (m.group("a") or m.group("b") or m.group("c") or m.group("d")).lower() in ("up", "louder")
        return "set_volume", {"step": 10 if up else -10}
    if _VOLUME_ASK.match(t):
        return "get_volume", {}
    for pattern, name, args in _BASIC:
        if pattern.match(t):
            return name, dict(args)
    return None


# Places: phone a business, book online, check hours. Contacts said plainly
# ("call mom") still reach _direct_call first and ring on WhatsApp; "on my
# phone" or "dial" means a normal call through Phone Link.
_PL = r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please)\W+)*(?:(?:can|could|would)\s+you\s+)?(?:please\s+)?"


_CALL_PHONE = re.compile(_PL + r"(?:call|phone|ring|dial)\s+(?P<x>.+?)\s+(?:on|from|using)\s+(?:my\s+)?(?:phone|mobile|cell|sim)\W*$|"
                         + _PL + r"(?:dial|make\s+a\s+(?:phone\s+)?call\s+to)\s+(?P<x2>.+?)\W*$|"
                         + _PL + r"(?:call|phone|ring)\s+(?P<x3>(?:\+?\d[\d\s-]{6,}\d))\W*$", re.I)


_CALL_ANY = re.compile(_PL + r"(?:call|phone|ring)\s+(?:up\s+)?(?:the\s+)?(?P<x>.+?)\W*$", re.I)


_BOOK = re.compile(_PL + r"(?:book|reserve|get)\s+(?:me\s+|us\s+)?(?:a\s+|an\s+)?(?P<kind>table|appointment|slot|reservation|booking)"
                   r"\s+(?:at|in|with|for)\s+(?P<place>.+?)(?:\s+for\s+(?P<n>\d+|two|three|four|five|six|seven|eight)(?:\s+(?:people|persons|of\s+us|guests|pax))?)?"
                   r"(?:\s+(?P<when>(?:at|on|for|tonight|today|tomorrow|this|next).*?))?\W*$", re.I)


_PLACE_Q = re.compile(_PL + r"(?:"
                      r"is\s+(?P<a>.+?)\s+open(?:\s+(?:now|today|right\s+now|tonight))?"
                      r"|what\s+time\s+does\s+(?P<b>.+?)\s+(?:open|close)(?:\s+today)?"
                      r"|(?:what\s+(?:is|are)\s+)?(?:the\s+)?(?:opening\s+hours|timings?|phone\s+number|contact\s+number|address)\s+(?:of|for)\s+(?P<c>.+?)"
                      r"|(?P<d>.+?)(?:'s|s)?\s+(?:phone\s+number|contact\s+number|opening\s+hours|timings)"
                      r")\W*$", re.I)


_NUMW = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8}


# Things "call" is followed by that are not a place to phone: "call it a
# day", "call off the meeting", "call back later".
_NOT_PLACE = re.compile(r"^(?:me|my\s+phone|now|everyone)$|^(?:it|this|that|him|her|them|a|an|off|out|back|in|up|for|over)\b", re.I)


def _direct_places(text: str) -> tuple[str, dict] | None:
    t = text.strip()
    m = _BOOK.match(t)
    if m:
        n = m.group("n") or "2"
        people = int(n) if n.isdigit() else _NUMW.get(n.lower(), 2)
        return "book_reservation", {"place": m.group("place"), "when": (m.group("when") or "today").strip(),
                                    "people": people}
    m = _CALL_PHONE.match(t)
    if m:
        who = (m.group("x") or m.group("x2") or m.group("x3")).strip()
        if not _NOT_PLACE.match(who):
            return "call_number", {"who": who}
    m = _PLACE_Q.match(t)
    if m:
        place = next(g for g in (m.group("a"), m.group("b"), m.group("c"), m.group("d")) if g)
        if len(place.split()) <= 6 and not re.match(r"^(?:my|the\s+pc|this|it|that)\b", place, re.I):
            return "place_info", {"place": place, "question": t}
    m = _CALL_ANY.match(t)
    if m and not _NOT_PLACE.match(m.group("x").strip()):
        # Reached only when _direct_call did not know the name as a contact.
        return "call_number", {"who": m.group("x").strip()}
    return None


# "open it", "show it in explorer" - the file last opened or shown.
_IT_CMD = re.compile(
    r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please|now|just|and|then|so)\W+)*"
    r"(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?"
    r"(?P<verb>open|show|reveal|launch|view)\s+(?:me\s+)?"
    r"(?:it|that|this|them|the\s+same|(?:that|this|the)\s+(?:file|one|document|pdf))"
    r"(?P<exp>\s+in\s+(?:the\s+)?(?:file\s+)?explorer)?"
    r"(?:\s+(?:up|again|now|please))*\W*$", re.I)


def _direct_it(text: str) -> tuple[str, dict] | None:
    """Turn "open it" into opening the file just found, opened or shown."""
    m = _IT_CMD.match(text.strip())
    if not m:
        return None
    from .tools import files

    if files.last_file is None:
        return None             # nothing to point at; let the model ask
    path = str(files.last_file)
    if m.group("exp") or m.group("verb").lower() == "reveal":
        return "reveal_path", {"path": path}
    return "open_file", {"path": path}


# Shutdown, restart, sleep and cancelling them. On 24 Sep 2026 "Shutdown the
# pc" reached _direct_app, which matched "pc" to the WinPcap Web Site
# shortcut and opened it; plain "Shutdown" went to the model, which replied
# "Shutting down the PC." without calling anything. So these are parsed here,
# ahead of every other router. "turn off" and "restart" need the PC named -
# "turn off the lights" and "restart spotify" are not about this machine -
# but "shutdown" and "reboot" on their own can only mean one thing.
_PC = r"(?:the\s+|my\s+|this\s+)?(?:pc|computer|laptop|system|machine|windows)"


_POWER_LEAD = r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please|now|just)\W+)*(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?"


_WHEN = (r"(?:\s+(?:in|after)\s+(?P<n>\d+|a|an|one|two|five|ten|fifteen|thirty)\s*"
         r"(?P<unit>sec(?:ond)?s?|min(?:ute)?s?|hours?|hrs?))?"
         r"(?:\s+(?P<now>now|right\s+now|immediately))?")


_TAIL = r"(?:\s+please)?\W*$"


_POWER = [
    ("cancel", re.compile(_POWER_LEAD + r"(?:cancel|abort|stop|don'?t)\s+(?:the\s+)?"
                          r"(?:shut\s*down|restart|reboot|power\s*off)" + _TAIL, re.I)),
    ("shutdown", re.compile(_POWER_LEAD + r"(?:shut\s*down|power\s*off|switch\s+off|turn\s+off)"
                            r"(?:\s+" + _PC + r")?" + _WHEN + _TAIL, re.I)),
    ("restart", re.compile(_POWER_LEAD + r"(?:(?:restart|reboot)\s+" + _PC + r"|reboot)"
                           + _WHEN + _TAIL, re.I)),
    ("sleep", re.compile(_POWER_LEAD + r"(?:(?:put\s+" + _PC + r"\s+to|send\s+" + _PC
                         + r"\s+to)\s+sleep|sleep\s+" + _PC + r"|" + _PC
                         + r"\s+(?:go\s+)?to\s+sleep)" + _TAIL, re.I)),
]


_WORD_NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "five": 5, "ten": 10,
             "fifteen": 15, "thirty": 30}


def _direct_power(text: str) -> tuple[str, dict] | None:
    """Turn "shut down the pc", "restart in 5 minutes", "cancel shutdown"
    into power_action, before any other router can mistake them."""
    t = text.strip()
    for action, pattern in _POWER:
        m = pattern.match(t)
        if not m:
            continue
        # "turn off" / "switch off" with nothing named is ambiguous (the
        # music? the screen?), so only route it when the PC was named.
        if action == "shutdown" and re.search(r"\b(?:turn|switch)\s+off\b", t, re.I) \
                and not re.search(_PC, t, re.I):
            return None
        args: dict = {"action": action}
        if action in ("shutdown", "restart"):
            gd = m.groupdict()
            if gd.get("now"):
                args["delay_seconds"] = 5
            elif gd.get("n"):
                n = gd["n"].lower()
                n = int(n) if n.isdigit() else _WORD_NUM.get(n, 1)
                unit = gd["unit"].lower()
                mult = 3600 if unit.startswith("h") else 60 if unit.startswith("m") else 1
                args["delay_seconds"] = n * mult
        return "power_action", args
    return None
