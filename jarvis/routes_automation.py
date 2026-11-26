"""Shortcuts for automation: rules and loops, scheduled missions, study goals, the
Athena Deck (consoles, builds, checklists) and Claude builds.

Split out of routes.py (31 Oct 2026); route() there calls these.
"""
from __future__ import annotations

import re

from .routes_common import _R_LEAD


_R_PAUSE = re.compile(_R_LEAD + r"(?:pause|stop|disable|turn\s+off)\s+(?:all\s+)?(?:the\s+|your\s+)?"
                      r"(?:automations?|autopilot|rules)(?:\s+for\s+(?P<n>\d+)\s+(?P<u>min|minute|hour)s?)?\W*$", re.I)


_R_RESUME = re.compile(_R_LEAD + r"(?:resume|restart|enable|turn\s+on|start)\s+(?:all\s+)?(?:the\s+|your\s+)?"
                       r"(?:automations?|autopilot|rules)\W*$", re.I)


_R_LIST = re.compile(_R_LEAD + r"(?:list|show|what\s+are)\s+(?:me\s+)?(?:my\s+|your\s+|all\s+)?(?:automations|rules)\W*$", re.I)


_R_DID = re.compile(_R_LEAD + r"what\s+(?:did|have)\s+you\s+(?:done|do)\s+(?:on\s+your\s+own|by\s+yourself|"
                    r"automatically)(?:\s+today)?\W*$", re.I)


_R_DAYS = r"(?:mon|tues?|wed(?:nes)?|thu(?:rs)?|fri|sat(?:ur)?|sun)(?:day)?s?|week(?:day|end)s?"


_R_TRIGGER = (r"(?:every\s*time|whenever|each\s+time|when|every\s+(?:day|morning|night|evening|\d+\s+\w+|hour|minute)|"
              r"(?:every|on|each)\s+(?:" + _R_DAYS + r")|daily|hourly|at\s+\d)")


# "every Monday at 7am start a mission to ..." - a scheduled mission (rules.py runs it; missions.py does it)
_R_SCHEDULED_MISSION = re.compile(r"^\W*(?:(?:athena|hey|ok|okay|please)\W+)*(?P<when>(?:every|on|each)\s+.{2,50}?)\s*,?\s*"
                                  r"(?P<do>(?:start|run|launch|begin|do)\s+an?\s+(?:big\s+|new\s+)?mission\b.{5,})$", re.I)


_R_WHEN_FIRST = re.compile(_R_LEAD + r"(?P<when>" + _R_TRIGGER + r"[^,]{2,60}?)\s*,\s*(?P<do>.{3,})$", re.I)


_R_DO_FIRST = re.compile(_R_LEAD + r"(?P<do>(?!remind)(?:play|open|close|connect|turn|start|lock|organi[sz]e|tidy|clean|"
                         r"check|run|say|send|switch)\b.{2,80}?)\s+(?P<when>(?:every\s*time|whenever|every\s+(?:day|morning|"
                         r"night|evening|\d+\s+(?:min|minute|hour|day)s?|hour)|daily|hourly)\b.*)$", re.I)


def _direct_watch(text: str) -> tuple[str, dict] | None:
    """Background jobs: "keep an eye on my LMS", "tell me when my timetable
    changes", "watch my attendance every 6 hours" (rules.watch_for_changes)."""
    m = re.match(_R_LEAD + r"(?:keep\s+(?:an\s+)?eye\s+on|watch|monitor|track\s+changes\s+(?:in|to|on))\s+(?P<w>.+?)"
                 r"(?:\s+every\s+(?P<n>\d+|an?|one)\s*(?P<u>hours?|hrs?|min\w*))?\W*$|"
                 + _R_LEAD + r"(?:tell|let)\s+me\s+(?:know\s+)?(?:when|if|whenever)\s+(?:anything\s+(?:new\s+)?(?:comes\s+up\s+)?(?:on|in)\s+)?"
                 r"(?P<w2>.+?)(?:\s+(?:changes?|updates?|gets?\s+updated|has\s+anything\s+new))?\W*$", text, re.I)
    if not m:
        return None
    what = (m.group("w") or m.group("w2") or "").strip()
    if not re.search(r"\blms\b|assignment|deadline|quiz|time\s*table|classes|schedule|attendance|tasks|to-?do", what, re.I):
        return None                                   # "watch a movie", "tell me when it's 5"
    n = m.group("n")
    hours = 1.0 if not n else (1 if n in ("a", "an", "one") else int(n)) / (60 if (m.group("u") or "").startswith("min") else 1)
    return "watch_for_changes", {"what": what, "every_hours": hours}


_R_EVERY = r"every\s+(?:(?P<n>\d+(?:\.\d+)?)\s*|(?P<half>half\s+(?:an?\s+)?)|an?\s+)?(?P<u>min(?:ute)?s?|hours?|hrs?)"
_R_UNTIL = r"\s*,?\s+(?:until|till|untill|and\s+stop\s+(?:when|once))\s+(?P<goal>.{3,}?)\W*$"


# "every 30 minutes, check the orb site on phones until it works" - a loop (loops.py) that retires itself
_R_LOOP_A = re.compile(_R_LEAD + _R_EVERY + r"\s*,?\s*(?P<task>.{6,}?)" + _R_UNTIL, re.I)


_R_LOOP_B = re.compile(_R_LEAD + r"(?:keep\s+)?(?P<task>.{6,}?)\s*,?\s+" + _R_EVERY + _R_UNTIL, re.I)


def _loop_minutes(m: re.Match) -> float:
    n = 0.5 if m.group("half") else float(m.group("n") or 1)
    return n * (60 if m.group("u").lower().startswith("h") else 1)


_R_N = r"(?P<n>\d+|an?|one|two|three|four|five|six)"
_R_K = r"(?P<k>codex|chat[\s-]*gpt|gpt|claude|cloud|clawed|gemini|jemini|antigravity|powershell|shell)"
_R_CON = r"(?:consoles?|agents?|terminals?|sessions?)"
_R_ON = r"(?:\s+(?:on|in|for|at)\s+(?:the\s+|my\s+)?(?P<f>.+?))?(?:\s+(?:to|and\s+(?:have|get|tell)\s+(?:it|them)(?:\s+to)?)\s+(?P<t>.+?))?\W*$"
_R_WHO = r"(?P<to>(?:console|terminal)\s+\S+|(?:the\s+)?(?:\S+\s+){0,2}(?:console|terminal)|(?:claude|gemini)\s+\d+)"


# A build said to Claude opens a live Claude console on the Deck, in a folder
# of its own, instead of a hidden mission - Hedwig's "start a Claude Code for my
# friend's peptide shop website" (23 Oct 2026). Whisper hears "clod", "cloud".
# Gemini, Argon and "start a mission ..." stay missions.
_R_CL = r"(?:claude|cloud|clawed|clod|clode)"


_R_BUILD_VERB = r"(?:build|make|create|write|design|code|program|develop|generate|put\s+together)\b"


_R_CLAUDE_CODE = re.compile(_R_LEAD + r"(?:start|open|launch|spin\s+up|fire\s+up|use|get)\s+(?:up\s+)?(?:a\s+|an\s+)?(?:new\s+)?"
                            + _R_CL + r"\s+code(?:\s+(?:session|console|agent))?\s+(?P<p>for|to|and)\s+(?P<t>.+?)\W*$", re.I)


_R_BUILD_WITH = re.compile(_R_LEAD + r"(?P<t>" + _R_BUILD_VERB + r".+?)\s+(?:with|using|in|on)\s+" + _R_CL + r"(?:\s+code)?\W*$", re.I)


_R_HAVE_CLAUDE = re.compile(_R_LEAD + r"(?:have|ask|get|tell|let)\s+" + _R_CL + r"(?:\s+code)?\s+(?:to\s+)?(?P<t>" + _R_BUILD_VERB + r".+?)\W*$",
                            re.I)


def _claude_build(text: str) -> tuple[str, dict] | None:
    m = _R_CLAUDE_CODE.match(text) or _R_BUILD_WITH.match(text) or _R_HAVE_CLAUDE.match(text)
    if not m:
        return None
    task = m.group("t").strip()
    if m.groupdict().get("p") == "for" and not re.match(_R_BUILD_VERB, task, re.I):
        task = "build " + task                    # "a claude code for my cafe's website"
    return "deck_spawn", {"kind": "claude", "count": 1, "folder": "", "task": task}


def _deck_build_newer() -> bool:
    """The last thing Claude was asked to build is a Deck console, not a mission."""
    try:
        from . import deck_link, missions
        last = deck_link.last_build()
        return bool(last) and last > max((m["started"] for m in missions.load() if m.get("worker", "claude") == "claude"),
                                          default=0)
    except Exception:
        return False


def _direct_deck(text: str) -> tuple[str, dict] | None:
    """Athena Deck (deck_link.py): the board of agent consoles."""
    build = _claude_build(text)
    if build:
        return build
    # A flow on the board (deck/flows.py), run like a tool: "run the weekly summary flow" ("workflow" is a routine).
    m = re.match(_R_LEAD + r"(?:run|start|kick\s+off|trigger)\s+(?:the\s+|my\s+)?(?:(?P<n>.+?)\s+flow|flow\s+(?:called\s+)?(?P<n2>.+?))"
                 r"(?:\s+(?:on|in)\s+the\s+deck)?\W*$", text, re.I)
    if m:
        return "deck_flow_run", {"name": (m.group("n") or m.group("n2") or "").strip()}
    m = re.match(_R_LEAD + r"(?:open|load|show(?:\s+me)?|pull\s+up|search(?:\s+for)?)\s+(?P<s>.{2,80}?)\s+(?:on|in)\s+(?:the\s+|my\s+)?deck\W*$",
                 text, re.I)
    if m and not re.match(r"(?:the\s+|my\s+)?(?:deck|board)$", m.group("s"), re.I):
        return "deck_browse", {"site": m.group("s").strip()}
    m = re.match(_R_LEAD + r"(?:open|launch|start|show(?:\s+me)?|bring\s+up)\s+(?:the\s+|my\s+|athena\s+)?(?:deck|agent\s+board)"
                 r"(?:\s+(?:on|for|in)\s+(?:the\s+|my\s+)?(?P<f>.+?))?\W*$", text, re.I)
    if m:
        return "deck_open", {"folder": (m.group("f") or "").strip()}
    m = re.match(_R_LEAD + r"(?:open|start|launch|spin\s+up|fire\s+up|give\s+me|put)\s+" + _R_N + r"\s+(?:new\s+)?" + _R_K + r"s?"
                 r"(?:\s+" + _R_CON + r")?" + _R_ON, text, re.I) \
        or re.match(_R_LEAD + r"(?:open|start|launch|spin\s+up|fire\s+up)\s+(?:a\s+new\s+)?" + _R_K + r"\s+" + _R_CON + _R_ON, text, re.I)
    if m:
        n = (m.groupdict().get("n") or "1").lower()
        count = int(n) if n.isdigit() else {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}[n]
        return "deck_spawn", {"kind": re.sub(r"[\s-]+", "", m.group("k").lower()), "count": count, "folder": (m.group("f") or "").strip(),
                              "task": (m.group("t") or "").strip()}
    # "what is the deck doing right now?" went to explain: "not enough information" (20 Oct 2026)
    if re.match(_R_LEAD + r"(?:what\s+are|how\s+are|what'?s|what\s+is|how\s+is|check(?:\s+on)?)\s+(?:my\s+|the\s+)?(?:consoles|deck)"
                r"(?:\s+(?:doing|up\s+to|going|status))?(?:\s+(?:right\s+)?now)?\W*$|^\W*(?:deck|consoles?)\s+status\W*$", text, re.I):
        return "deck_status", {}
    # Your day-by-day checklists on the Deck (deck_link.deck_list / deck_add_item)
    m = re.match(_R_LEAD + r"(?:what'?s|what\s+is|show(?:\s+me)?|read(?:\s+me)?|open)\s+(?:on\s+|left\s+on\s+|in\s+)?"
                 r"(?:my\s+|the\s+)?(?:(?P<w0>today|tomorrow|yesterday)'?s\s+)?(?:deck\s+)?(?:check\s*list|deck\s+list)"
                 r"(?:\s+(?:for\s+)?(?P<w>today|tomorrow|yesterday))?\W*$", text, re.I)
    if m:
        return "deck_list", {"which": (m.group("w0") or m.group("w") or "today").lower()}
    m = re.match(_R_LEAD + r"add\s+(?P<t>.+?)\s+to\s+(?:my\s+|the\s+)?(?:(?P<w>today|tomorrow)'?s\s+)?(?:deck\s+)?(?:check\s*list|deck\s+list)\W*$",
                 text, re.I)
    if m:
        return "deck_add_item", {"text": m.group("t"), "which": (m.group("w") or "today").lower()}
    # "hand off console 1 to codex: finish the docs", "hand console 2 over to a new claude and have it write the tests"
    m = re.match(_R_LEAD + r"(?:hand\s+off|hand|pass)\s+" + _R_WHO + r"(?:'s\s+work)?\s+(?:off\s+|over\s+)?to\s+(?:a\s+new\s+|new\s+|the\s+)?"
                 r"(?P<dst>[\w\s-]+?)(?:(?:\s*[:,]\s*|\s+and\s+(?:have|get|tell)\s+(?:it|them)(?:\s+to)?\s+)(?P<t>.+?))?\W*$", text, re.I)
    if m:
        return "deck_handoff", {"frm": m.group("to").strip(), "to": m.group("dst").strip(), "task": (m.group("t") or "").strip()}
    # "loop console 2 every 10 minutes: run the tests until they pass", "every 5 minutes tell console 2 to check the build"
    _until = r"(?:\s+until\s+(?P<g>.+?))?\W*$"
    m = re.match(_R_LEAD + r"loop\s+" + _R_WHO + r"\s+every\s+(?P<m>\d+)\s*(?:min(?:ute)?s?|m)\b\s*[:,]?\s*(?:to\s+)?(?P<t>.+?)" + _until, text, re.I) \
        or re.match(_R_LEAD + r"every\s+(?P<m>\d+)\s*(?:min(?:ute)?s?|m)\b\s*,?\s*(?:tell|ask)\s+" + _R_WHO + r"\s+(?:to\s+)?(?P<t>.+?)" + _until, text, re.I)
    if m:
        return "deck_loop", {"to": m.group("to").strip(), "text": m.group("t").strip(), "every": int(m.group("m")), "goal": (m.group("g") or "").strip()}
    m = re.match(_R_LEAD + r"(?:stop|pause|cancel|end)\s+(?:the\s+|all\s+(?:the\s+)?)?loops?\s+on\s+" + _R_WHO + r"\W*$", text, re.I)
    if m:
        return "deck_loop_stop", {"to": m.group("to").strip()}
    m = re.match(_R_LEAD + r"(?:tell|ask)\s+" + _R_WHO + r"\s+(?:to\s+)?(?P<t>.+?)\W*$", text, re.I)
    if m:
        return "deck_send", {"text": m.group("t").strip(), "to": m.group("to").strip()}
    m = re.match(_R_LEAD + r"what\s+(?:does|did|is)\s+" + _R_WHO + r"\s+(?:say|saying|asking|showing|want|need)\W*$", text, re.I) \
        or re.match(_R_LEAD + r"read\s+(?:me\s+)?" + _R_WHO + r"\W*$", text, re.I)
    if m:
        return "deck_read", {"to": m.group("to").strip()}
    m = re.match(_R_LEAD + r"(?:show\s+me|go\s+to|jump\s+to|switch\s+to|focus(?:\s+on)?)\s+" + _R_WHO + r"\W*$", text, re.I)
    if m:
        return "deck_focus", {"to": m.group("to").strip()}
    return None


def _direct_rules(text: str) -> tuple[str, dict] | None:
    deck = _direct_deck(text)
    if deck:
        return deck
    m = _R_LOOP_A.match(text) or _R_LOOP_B.match(text)
    if m:
        task = re.sub(r"^(?:to|and)\s+", "", m.group("task").strip(" ,"), flags=re.I)
        return "loop_start", {"task": task, "until": m.group("goal").strip(), "every_minutes": _loop_minutes(m)}
    m = re.match(_R_LEAD + r"(?:stop|cancel|end|kill)\s+(?:the\s+|my\s+)?(?P<w>.{0,40}?)\s*loop(?:ing)?\W*$", text, re.I)
    if m:
        return "loop_stop", {"which": m.group("w").strip()}
    if re.match(_R_LEAD + r"(?:how\s+are|what\s+are|show(?:\s+me)?|check(?:\s+on)?)\s+(?:my\s+|the\s+)?loops?"
                r"(?:\s+(?:doing|going))?\W*$|^\W*loops?\s+status\W*$", text, re.I):
        return "loop_status", {}
    if re.match(_R_LEAD + r"(?:does\s+)?(?:anything|anyone|anybody|something|who|what)\s+(?:else\s+)?(?:needs?\s+me|"
                r"is\s+waiting\s+(?:for|on)\s+me|needs\s+my\s+(?:attention|help|ok|answer))\W*$", text, re.I):
        return "needs_me", {}
    m = _R_PAUSE.match(text)
    if m:
        n = int(m.group("n") or 0) * (60 if (m.group("u") or "").startswith("hour") else 1)
        return "pause_automation", {"minutes": n}
    if _R_RESUME.match(text):
        return "resume_automation", {}
    if _R_LIST.match(text):
        return "list_rules", {}
    if _R_DID.match(text):
        return "what_did_you_do", {}
    if re.match(_R_LEAD + r"(?:back\s*up|backup)\s+(?:athena|yourself|everything|my\s+data|now)(?:\s+now)?\W*$|"
                + _R_LEAD + r"(?:make|do|run|take)\s+a\s+backup(?:\s+now)?\W*$", text, re.I):
        return "backup_now", {}
    if re.match(_R_LEAD + r"(?:run\s+(?:a\s+)?)?(?:health\s*check|check\s+yourself|self[\s-]?check|"
                r"how\s+are\s+you\s+running|any\s+problems\s+with\s+you)\W*$", text, re.I):
        return "health_check", {}
    if re.match(_R_LEAD + r"(?:retrain|re-train|train)\s+(?:athena|yourself)(?:\s+tonight)?\W*$", text, re.I):
        return "retrain_athena", {}
    if re.match(_R_LEAD + r"(?:check|read|summari[sz]e|go\s+through|what'?s\s+(?:new\s+)?in)\s+(?:my\s+)?(?:e-?mails?|inbox|mail)"
                r"(?:\s+and\s+draft\s+repl(?:y|ies))?\W*$|" + _R_LEAD + r"draft\s+(?:my\s+)?(?:e-?mail\s+)?repl(?:y|ies)\W*$",
                text, re.I):
        return "inbox_summary", {"draft_replies": True}
    if re.match(_R_LEAD + r"connect\s+(?:my\s+)?gmail\s+drafts?\W*$", text, re.I):
        return "connect_gmail_drafts", {}
    if re.match(_R_LEAD + r"(?:stop|turn\s+off|dismiss|silence)\s+(?:the\s+)?alarm\W*$|" + _R_LEAD + r"alarm\s+off\W*$", text, re.I):
        return "stop_alarm", {}
    m = re.match(_R_LEAD + r"snooze(?:\s+(?:it|the\s+alarm))?(?:\s+for\s+(?P<n>\d+)\s*min\w*)?\W*$", text, re.I)
    if m:
        return "snooze_alarm", {"minutes": int(m.group("n") or 10)}
    # UPES portal (upes.py): attendance, bunk maths, classes with rooms.
    m = re.match(_R_LEAD + r"(?:can|should|could)\s+i\s+(?:skip|bunk|miss|leave)\s+(?:the\s+|my\s+|today'?s\s+|tomorrow'?s\s+)?"
                 r"(?P<c>.+?)(?:\s+(?:class|lecture|lab))?(?:\s+(?:today|tomorrow|this\s+week))?\W*$", text, re.I)
    if m and m.group("c").lower() not in ("it", "that", "this", "class", "the class", "today", "tomorrow"):
        return "can_i_skip", {"course": m.group("c")}
    m = re.match(_R_LEAD + r"(?:what'?s|what\s+is|how'?s|how\s+is|show|check|tell\s+me)\s+(?:me\s+)?(?:my\s+)?attendance"
                 r"(?:\s+(?:in|for|of)\s+(?P<c>.+?))?\W*$|" + _R_LEAD + r"(?:my\s+)?attendance(?:\s+(?:in|for)\s+(?P<c2>.+?))?\W*$",
                 text, re.I)
    if m:
        return "my_attendance", {"course": (m.group("c") or m.group("c2") or "").strip()}
    m = re.match(_R_LEAD + r"(?:what|which)\s+classes\s+(?:do\s+i\s+have|have\s+i\s+got|are\s+there)\s*(?P<d>today|tomorrow)?\W*$|"
                 + _R_LEAD + r"(?:where|which\s+room)\s+is\s+my\s+(?:next\s+)?class\W*$", text, re.I)
    if m:
        return "upes_classes", {"day": (m.group("d") or "today").lower()}
    if re.match(_R_LEAD + r"(?:what'?s|what\s+is|anything|check|show)\s+(?:me\s+)?(?:due\s+)?(?:on\s+)?(?:my\s+)?(?:the\s+)?lms"
                r"(?:\s+deadlines?|\s+assignments?)?\W*$|" + _R_LEAD + r"(?:what'?s|what\s+is)\s+due\s+on\s+(?:the\s+)?lms\W*$|"
                + _R_LEAD + r"(?:lms|moodle)\s+(?:deadlines?|assignments?)\W*$", text, re.I):
        return "lms_deadlines", {}
    if re.match(_R_LEAD + r"(?:get|download|fetch|sync|update)\s+(?:my\s+|the\s+)?(?:lms|course|class)\s+(?:files|material|notes|slides)"
                r"(?:\s+from\s+(?:the\s+)?lms)?\W*$|" + _R_LEAD + r"(?:any\s+)?new\s+(?:course\s+)?(?:material|files|slides)"
                r"(?:\s+on\s+(?:the\s+)?lms)?\W*$", text, re.I):
        return "lms_files", {}
    # LMS items open signed in (upes.open_lms) - "open the feedback forum for
    # leading conversation" went to open_url with a guessed address (3 Oct 2026).
    m = re.match(_R_LEAD + r"open\s+(?:up\s+)?(?:the\s+|my\s+)?(?P<w>.*?\b(?:forum|assignment|quiz|submission|lab\s+record|"
                 r"discussion|course|lms|moodle)\b.*?)(?:\s+(?:on|in|from)\s+(?:the\s+)?(?:lms|moodle))?\W*$", text, re.I)
    if m and not re.search(r"\b(?:file|folder|pdf|docx?|pptx?)\b", m.group("w"), re.I):
        w = m.group("w").strip()
        return "open_lms", {"what": "" if w.lower() in ("lms", "the lms", "moodle") else w}
    # Whisper hears UPES as "UPS" (3 Oct 2026); bare "open UPES" means the portal too.
    if re.match(_R_LEAD + r"open\s+(?:the\s+|my\s+)?(?:upes|ups|u\.?\s*p\.?\s*e\.?\s*s|university|uni|college)\s*"
                r"(?:portal\s*)?(?:login|log\s+in|sign[\s-]?in|website|site)?\W*$", text, re.I) \
            and not re.match(_R_LEAD + r"open\s+(?:the\s+|my\s+)?(?:university|uni|college)\W*$", text, re.I):
        return "open_upes_login", {}
    if re.match(_R_LEAD + r"(?:plan|schedule|organi[sz]e)\s+(?:my\s+|the\s+)?(?:assignments?|deadlines|homework)\W*$", text, re.I):
        return "plan_my_assignments", {}
    if re.match(_R_LEAD + r"(?:prepare|prep|get)\s+(?:me\s+)?(?:ready\s+)?for\s+tomorrow(?:'?s)?(?:\s+classes)?\W*$"
                r"|" + _R_LEAD + r"(?:make|write)\s+(?:me\s+)?primers?\s+for\s+tomorrow", text, re.I):
        return "prepare_for_tomorrow", {}
    m = re.match(_R_LEAD + r"(?:make|create|give)\s+(?:me\s+)?(?:a\s+)?(?:revision|study|exam)\s+(?:plan|timetable|schedule)"
                 r"\s+for\s+(?:my\s+)?(?P<subs>.+?)\s+(?:exams?\s+)?(?:on|by|before)\s+(?:the\s+)?(?P<date>[\w -]+?)\W*$", text, re.I)
    if m:
        return "set_goal", {"goal": "prepare for " + re.sub(r"\bexams?\b", "", m.group("subs")).strip() + " exams",
                            "deadline": m.group("date")}
    # Long-running goals (goals.py): "prepare me for my signals end sem by friday"
    m = re.match(_R_LEAD + r"(?:help\s+me\s+|i\s+want\s+to\s+|let'?s\s+)?(?:prepare|prep|get)\s+(?:me\s+)?(?:ready\s+)?for\s+(?P<g>.+?)\s+"
                 r"(?:by|before|until|till|on)\s+(?P<d>.+?)\W*$", text, re.I)
    if m and not re.search(r"\btomorrow'?s?\s+class", m.group("g"), re.I):
        return "set_goal", {"goal": "prepare for " + m.group("g").strip(), "deadline": m.group("d").strip()}
    if re.match(_R_LEAD + r"(?:what'?s|what\s+is|show(?:\s+me)?|how'?s|how\s+is|how\s+am\s+i\s+doing\s+on)\s+(?:my\s+|the\s+)?"
                r"(?:goals?|goal\s+plan|study\s+plan|plan\s+for\s+today|today'?s\s+(?:goal\s+)?plan)(?:\s+going)?\W*$", text, re.I):
        return "goal_status", {}
    m = re.match(_R_LEAD + r"(?:i\s+(?:have\s+)?(?:finished|completed|did|done)|(?:i'?m\s+)?done\s+with|tick\s+off|mark)\s+(?P<t>.+?)"
                 r"(?:\s+(?:as\s+)?done)?\W*$", text, re.I)
    if m:
        from . import deck_link                       # an item on today's Deck list ticks there first
        if deck_link.on_today_list(m.group("t")):
            return "deck_tick", {"item": m.group("t")}
    if m and _goals_active():
        return "goal_done", {"task": m.group("t")}
    if re.match(_R_LEAD + r"(?:replan|re-plan|reschedule)\s+(?:my\s+)?(?:goals?|plan|study\s+plan)\W*$", text, re.I):
        return "replan_goal", {}
    m = re.match(_R_LEAD + r"(?:start|begin|do|let'?s\s+do)\s+(?:my\s+|the\s+)?next\s+(?:(?P<g>.+?)\s+)?(?:goal\s+)?(?:task|lesson|topic)"
                 r"(?:\s+(?:for|of|in)\s+(?P<g2>.+?))?\W*$", text, re.I)
    if m and _goals_active():
        return "goal_start", {"goal": (m.group("g") or m.group("g2") or "").strip()}
    # "open my orb website" - one of the user's own sites (a mission's, or a page in athena-sites);
    # only when one matches, so "open the youtube website" still reaches the browser.
    m = re.match(_R_LEAD + r"(?:open|show(?:\s+me)?|pull\s+up|launch|load|play|watch)\s+(?:up\s+)?(?:the\s+|my\s+)?(?P<w>.{2,60}?)\s+"
                 r"(?:web\s*site|site|landing\s+page|home\s*page|web\s*page|page|video|clip|animation|reel)\W*$", text, re.I)
    if m and _site_known(m.group("w")):
        return "site_open", {"which": m.group("w").strip()}
    # Missions (missions.py): Claude Code or Google's Gemini agent does a big job
    # in the background. Whisper often hears "Claude" as "cloud" or "clawed".
    # "Argon" (Gemini 4 Argon) runs in the Gemini app; Whisper may hear "argan" or "aragon".
    cl = r"(?P<w>(?:gemini\s+)?(?:4\s+)?(?:argon|argan|aragon)|claude|cloud|clawed|clod|gemini|jemini)"
    worker = lambda m: ("argon" if re.search(r"arg|arag", m.group("w") or "", re.I) else
                        "gemini" if (m.group("w") or "").lower() in ("gemini", "jemini") else "claude")
    m = re.match(_R_LEAD + r"(?:have|ask|get|tell|let)\s+" + cl + r"\s+(?:to\s+)?(?P<v>change|add|fix|update|tweak|edit|remove|make\s+the|make\s+it)\b"
                 r"(?P<t>.*)$", text, re.I)
    if m and worker(m) == "claude" and _deck_build_newer():
        return "deck_send", {"text": (m.group("v") + m.group("t")).strip(), "to": "claude"}
    if m and _missions_exist():
        return "mission_follow_up", {"instruction": (m.group("v") + m.group("t")).strip(), "worker": worker(m)}
    m = re.match(_R_LEAD + r"(?:have|ask|get|tell|let)\s+" + cl + r"\s+(?:to\s+)?"
                 r"(?P<t>(?:build|make|create|write|design|code|program|develop|research|draft|prepare|generate|put\s+together|find)\b.+?)\W*$",
                 text, re.I)
    if m:
        return "mission_start", {"task": m.group("t").strip(), "worker": worker(m)}
    # No worker named: Athena plans it as a chain (Gemini researches, Claude builds).
    m = re.match(_R_LEAD + r"(?:start|begin|launch|run)\s+an?\s+(?:big\s+|new\s+)?mission\s*(?:to|:|for)?\s+(?P<t>.{8,}?)\W*$"
                 r"|^\W*mission\s*:\s*(?P<t2>.{8,}?)\W*$", text, re.I)
    if m:
        return "mission_chain", {"goal": (m.group("t") or m.group("t2")).strip()}
    if re.match(_R_LEAD + r"(?:what'?s|what\s+is|how'?s|how\s+is|show(?:\s+me)?|check)\s+(?:on\s+)?(?:the\s+status\s+of\s+)?(?:my\s+|the\s+)?missions?"
                r"(?:\s+(?:going|doing|status))?\W*$|^\W*missions?\s+status\W*$", text, re.I) \
            or re.match(_R_LEAD + r"(?:is|are|has|have)\s+(?:the\s+|my\s+)?(?:missions?|emissions?|machines?|mission's)\s+"
                        r"(?:been\s+)?(?:done|finished|ready|complete|completed|over)\W*$", text, re.I):
        # "is the mission done" - Whisper also hears "emission" and "machines" (16 Oct 2026)
        return "mission_status", {}
    if re.match(_R_LEAD + r"(?:stop|cancel|kill|abort)\s+(?:the\s+|my\s+)?mission\W*$", text, re.I):
        return "mission_stop", {}
    m = re.match(_R_LEAD + r"(?:publish|put|post)\s+(?:it|that|the\s+(?P<w>.+?)\s*(?:site|website|page)?)\s*(?:online|live|on\s+the\s+(?:web|internet))?\W*$"
                 r"|" + _R_LEAD[1:] + r"(?:make|put)\s+(?:the\s+)?(?:site|website|page)\s+(?:live|online)\W*$", text, re.I)
    if m:                                     # a destructive tool: Athena asks before it goes public
        return "mission_publish", {"which": (m.group("w") or "").strip()}
    if re.match(_R_LEAD + r"(?:open|show)\s+(?:me\s+)?(?:the\s+|my\s+)?(?:email\s+|outreach\s+)?draft\W*$", text, re.I):
        return "mission_draft", {}
    if re.match(_R_LEAD + r"(?:watch|keep\s+an?\s+eye\s+on|monitor|check)\s+(?:my\s+)?(?:ragazza\s+|store\s+)?orders\b"
                r"|.*\b(?:order|orders)\b.*\bstuck\b", text, re.I):
        d = re.search(r"(\d+)\s*days?", text)
        return "add_watch", {"what": "stuck_orders", "every_hours": 6, "value": d.group(1) if d else "2"}
    if re.match(_R_LEAD + r"(?:warn|tell|alert)\s+me\s+(?:if|when)\s+(?:my\s+)?attendance\b", text, re.I):
        p = re.search(r"(\d+)\s*(?:%|percent)", text)
        return "add_watch", {"what": "attendance", "every_hours": 12, "value": p.group(1) if p else "75"}
    if re.match(_R_LEAD + r"(?:every\s+(?:evening|night)\s*,?\s*)?(?:tell|remind|warn)\s+me\s+(?:what'?s|what\s+is|about\s+"
                r"(?:anything|things|tasks))\s+due\s+(?:by\s+)?tomorrow(?:\s+every\s+(?:evening|night))?\W*$", text, re.I):
        return "add_rule", {"when": "every day at 8pm", "do": "watch:due_soon"}
    m = _R_SCHEDULED_MISSION.match(text)
    if m:
        from .rules import parse_when
        if parse_when(m.group("when")):
            return "add_rule", {"when": m.group("when").strip(" ,"), "do": m.group("do").strip(" .")}
    m = _R_WHEN_FIRST.match(text) or _R_DO_FIRST.match(text)
    if m:
        from .rules import parse_when
        if parse_when(m.group("when")):
            return "add_rule", {"when": m.group("when").strip(), "do": m.group("do").strip(" .")}
    return None


def _site_known(which: str) -> bool:
    try:
        from . import missions
        return missions.find_site(which) is not None
    except Exception:
        return False


def _missions_exist() -> bool:
    try:
        from . import missions
        return bool(missions.load())
    except Exception:
        return False


def _goals_active() -> bool:
    try:
        from . import goals
        return any(g["status"] == "active" for g in goals.load())
    except Exception:
        return False
