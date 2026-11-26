"""Commands that go straight to a tool, without asking the model - route() and
its _direct_* helpers, plus the phrase patterns the agent loop shares.

Moved out of agent.py on 16 Oct 2026 (it had reached 2,735 lines); agent.py
re-exports what it uses, and anything else is still reachable as agent.<name>.

Split again on 31 Oct 2026 (2,022 lines): this file keeps route(), split_simple()
and the shared reply helpers; the shortcuts themselves are by area in
    routes_basic.py       apps, the brief, screen, volume, tasks, places, power, "it"
    routes_automation.py  rules, loops, missions, goals, the Deck, Claude builds
    routes_study.py       quick facts, explain, grounded answers, lab reports, quizzes
    routes_files.py       files, folders, documents, phone file transfer
    routes_phone.py       calls, messages, contacts, Teams, Bluetooth, the phone
    routes_memory.py      remember / forget / recall, reminders, routines
    routes_media.py       songs and tracks
    routes_common.py      what they share: the lead-in, question patterns, file lookup
All of it is imported back here, so routes.<name> still works.
"""
from __future__ import annotations

import json
import re

from . import registry
from .config import cfg
from .routes_common import (  # noqa: F401 - split out 31 Oct 2026; still importable from here
    _QUESTION_WORD,
    _best_file,
    _G_ASK,
    _R_LEAD,
)
from .routes_media import (  # noqa: F401 - split out 31 Oct 2026; still importable from here
    _ORDINALS,
    _TRACK_CMD,
    _direct_track,
    _SONG_GENERIC,
    _SONG_CMD,
    _direct_song,
)
from .routes_files import (  # noqa: F401 - split out 31 Oct 2026; still importable from here
    _LEAD,
    _EXPLORER_CMD,
    _FOLDER_CMD,
    _BARE_FOLDERS,
    _REVEAL_CMD,
    _OPEN_CMD,
    _FILE_HINT,
    _NOT_A_FILE,
    _confident_file,
    _direct_folder,
    _direct_file,
    _DOC_FIND,
    _OPEN_ALL,
    _NOT_A_FILE_TO_SEND,
    _SEND_TO_PHONE,
    _NOT_A_FILE_NO_VERB,
    _SEND_ME,
    _NOT_A_FILE_TO_SEND_ME,
    _FROM_PHONE,
    _direct_phone_files,
    _direct_find_document,
)
from .routes_study import (  # noqa: F401 - split out 31 Oct 2026; still importable from here
    _SYSTEM_TOPIC,
    _STUDY_VERB,
    _STUDY_WHAT,
    _STUDY_PREFIX,
    _ARITHMETIC,
    _INFO_LEAD,
    _TIME_IN,
    _NOW_PLAYING,
    _WIFI_NAME,
    _IP,
    _direct_info,
    _G_MINE,
    _GROUNDS,
    _direct_grounded,
    _direct_explain,
    _QUICK_FACT,
    _direct_labreport,
    _direct_quiz,
    _direct_answer,
    _direct_mode,
)
from .routes_automation import (  # noqa: F401 - split out 31 Oct 2026; still importable from here
    _R_PAUSE,
    _R_RESUME,
    _R_LIST,
    _R_DID,
    _R_DAYS,
    _R_TRIGGER,
    _R_SCHEDULED_MISSION,
    _R_WHEN_FIRST,
    _R_DO_FIRST,
    _direct_watch,
    _R_EVERY,
    _R_UNTIL,
    _R_LOOP_A,
    _R_LOOP_B,
    _loop_minutes,
    _R_N,
    _R_K,
    _R_CON,
    _R_ON,
    _R_WHO,
    _R_CL,
    _R_BUILD_VERB,
    _R_CLAUDE_CODE,
    _R_BUILD_WITH,
    _R_HAVE_CLAUDE,
    _claude_build,
    _deck_build_newer,
    _direct_deck,
    _direct_rules,
    _site_known,
    _missions_exist,
    _goals_active,
)
from .routes_phone import (  # noqa: F401 - split out 31 Oct 2026; still importable from here
    _mentioned_contact,
    _direct_contact_q,
    _TEAMS_LIST,
    _TEAMS_JOIN,
    _P_LEAD,
    _P_MY,
    _direct_phone_ctl,
    _direct_teams,
    _BT_LEAD,
    _BT_CONNECT,
    _BT_TOGGLE,
    _direct_bluetooth,
    _PHONE_ME,
    _direct_phone_me,
    _direct_call,
    _direct_message,
)
from .routes_memory import (  # noqa: F401 - split out 31 Oct 2026; still importable from here
    _MEM_SHOW,
    _MEM_FORGET,
    _MEM_ADD,
    _MEM_SEARCH,
    _STYLE_CMD,
    _MEM_VERB,
    _LABEL,
    _MEM_FACT_IS,
    _MEM_FACT_BARE,
    _MEM_FACT_AFTER,
    _MEM_ASK,
    _MEM_RECAP,
    _direct_memory,
    _direct_routine,
    _direct_reminder,
    _direct_skill_ideas,
)
from .routes_basic import (  # noqa: F401 - split out 31 Oct 2026; still importable from here
    _CLOSE_VERBS,
    _APP_CMD,
    _direct_app,
    _exact_personal_file,
    _BRIEF,
    _TOMORROW,
    _direct_brief,
    _BASIC_LEAD,
    _BASIC,
    _SCREEN_TEXT,
    _SCREEN,
    _WHOLE,
    _ASK,
    _ON_PC,
    _VOLUME,
    _VOLUME_STEP,
    _ADD_TASK,
    _direct_basic,
    _PL,
    _CALL_PHONE,
    _CALL_ANY,
    _BOOK,
    _PLACE_Q,
    _NUMW,
    _NOT_PLACE,
    _direct_places,
    _IT_CMD,
    _direct_it,
    _PC,
    _POWER_LEAD,
    _WHEN,
    _TAIL,
    _POWER,
    _WORD_NUM,
    _direct_power,
)


# A person does not greet you with the same sentence every time. A few
# ways of saying the fixed lines, picked at random but never the same one
# twice in a row.
_LINES = {
    "hello": ["Hey{you}. What can I do?", "Hi{you} - what do you need?",
              "Hey{you}, I'm here. What's up?", "Hello{you}. What can I do for you?"],
    "missed": ["Sorry, I didn't catch that.", "Say that again?",
               "I missed that - what was it?", "Sorry, could you repeat that?"],
}
_last_line: dict[str, str] = {}


def _pick_line(kind: str) -> str:
    import random

    who = str(cfg.get("assistant.user_name", "") or "").strip()
    you = "" if not who or who.lower() in ("operator", "user") else f" {who}"
    options = [o for o in _LINES[kind] if o != _last_line.get(kind)] or _LINES[kind]
    line = random.choice(options)
    _last_line[kind] = line
    return line.format(you=you)


# Past-tense claims that something was carried out. Checked only when the turn
# used no tools at all, so a genuine "volume set to 50%" after a real call is
# never affected.
# Matched on the verb stem with any ending, because the model dodges a fixed
# list by changing tense: blocked "closed", it answers "closing WhatsApp".
_ACTION_CLAIMS = re.compile(
    r"\b("
    # Stems, so "clos" covers close/closed/closing. Irregular past tenses
    # (sent, wrote) need spelling out - no suffix rule produces them.
    r"lock|unlock|send|sent|open|launch|start|clos|quit|exit|"
    r"delet|remov|mov|renam|creat|writ|wrote|sav|download|"
    r"remember|note|stor|schedul|sett|set|turn|mut|unmut|"
    r"play|paus|skipp|skip|resum|"
    # "shutting" doubles its t, which no suffix below produces - "Shutting
    # down the PC." went through unchecked on 24 Sep 2026 with nothing run.
    r"shutting|shut|restart|reboot|organi[sz]|cop|typ|clear|cancel"
    r")(s|ed|ing|e|es|d)?\b",
    re.IGNORECASE,
)

# "I can't do that" with no tool used: on a narrowed tool list the right
# tool may simply not have been shown, so the request is retried with all.
_CANT = re.compile(
    r"\b(?:cannot|can't|can not|could not|couldn't|unable to|not able to|no tool|"
    r"don't have (?:a |the |any )?(?:tool|way|ability|access)|"
    r"do not have (?:a |the |any )?(?:tool|way|ability|access)|not possible for me)\b",
    re.IGNORECASE,
)

# Phrases that report a fact or a refusal rather than claiming an action.
_NOT_A_CLAIM = re.compile(
    r"\b(cannot|can't|could not|couldn't|unable|no tool|don't have|"
    r"do not have|not saved|no contact|already|is at|are at|"
    r"nothing is|nothing to|no music|no player|not playing|"
    r"would you|shall i|should i|do you want)\b",
    re.IGNORECASE,
)


def _parse_text_tool_call(content: str) -> tuple[str, dict] | None:
    """Recover a tool call the model wrote as text instead of calling.

    Small models intermittently emit `open_app {"name": "outlook"}` in the
    message body rather than through the tool-call channel. Left alone, that
    text gets read aloud and nothing happens. Recognised forms:

        open_app {"name": "outlook"}
        {"name": "open_app", "arguments": {"name": "outlook"}}
        ```json { ... } ```
        open_app(name="outlook")
    """
    if not content:
        return None
    text = content.strip()
    text = re.sub(r"^```(?:json|tool_code)?\s*|\s*```$", "", text, flags=re.I).strip()

    # {"name": "tool", "arguments": {...}}
    if text.startswith("{"):
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            obj = None
        if isinstance(obj, dict):
            name = obj.get("name") or obj.get("tool") or obj.get("function")
            args = obj.get("arguments") or obj.get("parameters") or obj.get("args") or {}
            if isinstance(name, str) and registry.get(name) and isinstance(args, dict):
                return name, args

    # A bare tool name, no arguments: "get_volume"
    bare = re.fullmatch(r"([a-z_][a-z0-9_]*)\s*(?:\(\s*\))?\.?", text, re.I)
    if bare:
        t = registry.get(bare.group(1))
        if t and not t.schema["function"]["parameters"]["required"]:
            return bare.group(1), {}

    # toolname {"a": 1}
    m = re.match(r"^([a-z_][a-z0-9_]*)\s*(\{.*\})\s*$", text, re.S | re.I)
    if m and registry.get(m.group(1)):
        try:
            args = json.loads(m.group(2))
        except json.JSONDecodeError:
            return None
        if isinstance(args, dict):
            return m.group(1), args

    # toolname followed by a bare argument, no quotes or braces:
    #   open_file C:/some/path/Experiment 4.1
    m = re.match(r"^([a-z_][a-z0-9_]*)[ \t]+(\S.*)$", text, re.S | re.I)
    if m:
        t = registry.get(m.group(1))
        if t:
            required = t.schema["function"]["parameters"]["required"]
            if len(required) == 1:
                value = m.group(2).strip().strip("\"'").replace("\\ ", " ")
                return m.group(1), {required[0]: value}

    # toolname(a="b")
    m = re.match(r"^([a-z_][a-z0-9_]*)\s*\((.*)\)\s*$", text, re.S | re.I)
    if m and registry.get(m.group(1)):
        args: dict = {}
        for k, v in re.findall(r"([a-z_][a-z0-9_]*)\s*=\s*(\"[^\"]*\"|'[^']*'|[^,\s)]+)",
                               m.group(2), re.I):
            args[k] = v.strip("\"'")
        return m.group(1), args

    return None


# Fragments that carry no instruction. Speech produces these constantly when
# it catches the tail of a sentence or a noise.
_VAGUE = re.compile(
    r"^\W*("
    r"what|what'?s? (that|this|it|up)|huh|eh|sorry|pardon|come again|"
    r"say (that )?again|again|wait|hold on|hmm+|um+|uh+|ah+|oh|ok(ay)?|"
    r"yes|no|yeah|nope|and|so|then|now|this|that|it|"
    r"(now|and|so|but|well) (this|that|it|then)"
    r")\W*$",
    re.IGNORECASE,
)


_GREETING = re.compile(
    r"^\W*(?:hi|hey|hello|yo|hiya|good\s+(?:morning|afternoon|evening)|"
    r"what'?s?\s*up|sup)"
    r"(?:\s+(?:athena|jarvis|there|buddy|mate))?\W*$", re.I)


# "Athena, unlock" / "unlock my laptop" / "log me in". Not "unlock my phone
# screen rotation" or anything naming another thing to unlock.
# What the last reply offered to do: "let me know if you want to play it or
# add it to your queue" -> "play it". The first option only, up to " or ".
_NO_RECIPIENT = re.compile(r"^\W*(?:(?:athena|jarvis|ok|okay|please)\W+)*(?:can\s+you\s+)?(?:please\s+)?send\s+(?:a|an)\s+"
                           r"(?P<b>[\w' ]{1,30}?)\s+(?:message|msg|text)(?:\s+(?:on|over|through|via|using)\s+"
                           r"(?:whats\s?app|my\s+phone|the\s+phone|phone))*\W*$", re.I)

_SLOW_TOOLS = {"whatsapp_message": "Sending it.", "whatsapp_call": "Calling.", "open_lms": "Opening it.",
               "find_my_phone": "Ringing your phone.", "show_phone": "Bringing up your phone."}

_FOLLOW = re.compile(r"^\W*(?:(?:athena|jarvis|ok|okay|so|hmm|and)\W+)*(?:and\s+)?(?:what\s+about|how\s+about|what\s+of|"
                     r"and\s+for|same\s+for|and)\s+(?P<x>[^.?!]+?)\W*$|"
                     r"^\W*(?:(?:athena|jarvis|and|so)\W+)*(?P<x2>today|tomorrow)\W*$", re.I)
_OPEN_IT = re.compile(r"^\W*(?:(?:athena|jarvis|ok|okay|then|so|yes)\W+)*(?:open|show|start)\s+"
                      r"(?:it|that|that\s+one|the\s+first\s+one)(?:\s+please)?\W*$", re.I)

_OFFERED = re.compile(
    r"\b(?:let me know if you(?:'d)? (?:want|like) (?:me )?to|(?:do )?you want me to|want me to|"
    r"shall i|should i|would you like me to|i can)\s+(.+?)(?:\s+or\s+.*)?[.?!]?\s*$", re.I)

# Shortcuts that only look something up - a project's files can answer these better.
_LOOKUPS = {"lms_deadlines", "check_deadlines", "upes_classes", "teams_meetings", "my_attendance",
            "web_search", "show_timetable", "recall", "daily_brief", "explain", "read_webpage", "lms_files"}
# A question or a piece of study work, as opposed to a command.
_QUESTION = re.compile(r"\s*(?:athena[,\s]+)?(?:what|whats|what's|why|how|when|where|which|who|whom|whose|is|are|was|were|"
                       r"do|does|did|can|could|should|would|will|explain|derive|solve|find|calculate|compute|prove|"
                       r"show\s+that|list|summari[sz]e|summary|compare|differentiate|define|describe|give\s+me|tell\s+me|"
                       r"make\s+(?:me\s+)?(?:notes|a\s+summary|questions)|quiz|revise|teach)\b|.*\?\s*$", re.I)

_OPEN_WHATSAPP = re.compile(
    r"^\W*(?:(?:athena|jarvis)\W+)?(?:please\s+)?open\s+(?:the\s+)?whats\s?app(?:\s+app)?\s*(?:,\s*)?(?:and|then|&)?\s+", re.I)

_UNLOCK = re.compile(
    r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please)\W+)*(?:(?:can|could|would|will)\s+you\s+)?"
    r"(?:please\s+)?(?:unlock|log\s*me\s+in|sign\s+me\s+in|log\s+in|sign\s+in)"
    r"(?:\s+(?:the\s+|my\s+|this\s+)?(?:pc|computer|laptop|screen|lock\s*screen|"
    r"windows|system|device))?(?:\s+(?:now|please))*\W*$", re.I)


def _is_vague(text: str) -> bool:
    """True if the input is a fragment with nothing actionable in it."""
    return bool(_VAGUE.match(text.strip()))


def _is_greeting(text: str) -> bool:
    """A hello is not a half-heard fragment.

    "hi", "hello" and "hey" used to sit in the vague list, which made sense
    when the only ear was a microphone - a stray "hi" is usually someone
    talking in the room. Typed into the chat window it is a person saying
    hello, and answering "Sorry, I didn't catch that" reads as broken.
    """
    return bool(_GREETING.match(text.strip()))


# ---------------------------------------------------------------- files
# "open my downloads" reached the model and came back "(no response)";
# "open documents folder" became open_app("documents folder"), failed, and
# was then reported as done after File Explorer was merely brought forward;
# "open resume.docx" made the model guess C:/Users/Siddharth/resume.docx and
# then call `remember`. The phrasings are regular and the file index answers
# in milliseconds, so these are routed here. Anything that does not clearly
# name a folder or a file still falls through - "open youtube" must reach
# the model - and apps are tried before files.
_FILE_TOOLS = {"open_file", "open_folder", "reveal_path"}


# Not "time", "speed", "area" or "length": "the time constant of an RC
# circuit" and "the area of a circle" are class questions.


# Whisper's stock endings, added to speech that never said them: "call maa
# on phone. And we'll see you in the next one." (25 Sep 2026).
_OUTRO = re.compile(r"[\s.,!?]+(?:and\s+)?(?:we'?ll\s+)?(?:see\s+you\s+(?:in\s+the\s+next\s+(?:one|video)|next\s+time|soon)|"
                    r"thanks?\s+(?:you\s+)?for\s+watching|please\s+subscribe|bye(?:\s+bye)?)[\s.!]*$", re.I)


# What Whisper makes up in the silence after you stop, as a sentence of its
# own: "play the music. Thank you." played a song called "Thank You For The
# Love" (26 Sep 2026); "brief me. Okay.", "...your choice. first." likewise.
_FILLER_TAIL = re.compile(
    r"(?<=\S)[.!?]+\s+(?:thanks?(?:\s+you)?(?:\s+(?:so\s+much|very\s+much))?|ok(?:ay)?|you\s+know|do\s+you\s+know|"
    r"first|yeah|yes|yep|right|alright|all\s+right|hmm+|um+|uh+|so|bye|you|oh|ah|well|i\s+mean|"
    r"good|great|nice|cool|sure|there\s+you\s+go|here\s+we\s+go)[\s.!?,]*$", re.I)


def clean_speech(text: str) -> str:
    t = _OUTRO.sub("", text.strip())
    for _ in range(2):                     # "... Okay. Thank you."
        t = _FILLER_TAIL.sub(".", t)
    return re.sub(r"[\s,.]+(?:and|so|but|then)\W*$", "", t, flags=re.I).strip()


def _direct_store(said: str) -> tuple[str, dict] | None:
    from .tools import ragazza
    return ragazza.route(said)


def route(text: str) -> tuple[str, dict] | None:
    """The tool call a command maps to without asking the model, or None.

    Shared by Agent.ask and by routines' `do:` steps, so a routine can say
    "open downloads" exactly as you would.
    """
    said = _fix_verb(clean_speech(text))
    return (_direct_skill_ideas(said) or _direct_watch(said) or _direct_answer(said) or _direct_mode(said) or _direct_quiz(said) or _direct_labreport(said) or _direct_phone_ctl(said) or _direct_teams(said) or _direct_power(said) or _direct_basic(said) or _direct_info(said) or _direct_memory(said)
            or _direct_phone_files(said) or _direct_find_document(said)
            or _direct_store(said)
            or _direct_routine(said) or _direct_it(said)
            or _direct_reminder(said) or _direct_rules(said) or _direct_track(said)
            or _direct_phone_me(said)
            or _direct_call(said) or _direct_places(said) or _direct_folder(said)
            or _direct_bluetooth(said)
            or _direct_app(said) or _direct_file(said)
            or _direct_message(said) or _direct_song(said)
            or _direct_brief(said) or _direct_contact_q(said) or _direct_grounded(said) or _direct_explain(said))


def split_simple(text: str, strict: bool = False) -> list[tuple[str, dict]] | None:
    """"open spotify and play music" -> two quick tool calls, if EVERY part
    is something the quick handlers know. Otherwise None, and it is the
    planner's. Two plain actions do not need a hosted model, its rate
    limits, or twenty seconds."""
    parts = [p for p in re.split(r"\s*(?:,\s*(?:and\s+)?(?:then\s+)?|\band\s+then\b|\bthen\b|\band\b)\s*",
                                 text.strip(), flags=re.I) if p and p.strip()]
    if len(parts) < 2 or len(parts) > 4:
        return None
    # strict: the sentence was not obviously several requests, so each part
    # must be a command of its own - "play simon and garfunkel" is one song.
    if strict and any(len(p.split()) < 2 for p in parts):
        return None
    calls = []
    for p in parts:
        hit = route(p)
        if hit is None or hit[0] in ("explain", "run_routine"):
            return None
        calls.append(hit)
    return calls


# "submit it" / "don't submit" after browser_click held back a final button.
_SUBMIT_YES = re.compile(
    r"^\W*(?:(?:athena|jarvis|ok|okay|yes|yeah|yep|sure|alright)\W+)*"
    r"(?:submit(?:\s+it)?|send\s+it|go\s+ahead|confirm(?:\s+it)?|do\s+it|press\s+it|(?:call|dial|ring)\s+(?:it|them)|"
    r"yes|yeah|yep|place\s+(?:the\s+)?order|pay)(?:\s+(?:now|please))*\W*$", re.I)
_SUBMIT_NO = re.compile(
    r"^\W*(?:(?:athena|jarvis|no|nope)\W+)*(?:no|nope|don'?t(?:\s+submit)?|cancel(?:\s+it)?|"
    r"stop|wait|hold\s+on|not\s+yet)(?:\s+(?:it|now|please))*\W*$", re.I)


# Typed commands arrive with typos the routers cannot see past: "oopen
# scheduler 2" (24 Sep 2026) missed every "open" pattern, reached the model,
# and became open_app("scheduler 2") - Task Scheduler - while "open
# scheduler 2" routes straight to scheduler (2).pdf. Only the leading verb is
# corrected; the rest of the sentence is left for the finder's own fuzziness.
_VERB_TYPO = re.compile(
    r"^(\W*(?:(?:athena|jarvis)\W+)?)(?:o+pe+n+|opne|opn|oepn|opem|oprn|ipen|pen|ope)(?=\s)", re.I)


# "Athena, close Spotify" came back from speech as "close, Spotify" (the
# comma kept every router from matching) and as "Claude, Spotify" (25 Sep
# 2026). "close" before an app name is also heard as "clothes" and "clause".
_CLOSE_HEARD = re.compile(
    r"^(\W*(?:(?:athena|jarvis)\W+)?)(?:claude|clothes|clause|cloze|klose|clos|closed)(?=[\s,.]+\w)", re.I)
_VERB_COMMA = re.compile(
    r"^(\W*(?:(?:athena|jarvis)\W+)?(?:open|close|quit|exit|launch|start|play|pause|kill))\s*[,.:;]+\s*", re.I)


def _fix_verb(text: str) -> str:
    text = _VERB_TYPO.sub(lambda m: m.group(1) + "open", text, count=1)
    text = _CLOSE_HEARD.sub(lambda m: m.group(1) + "close", text, count=1)
    return _VERB_COMMA.sub(lambda m: m.group(1) + " ", text, count=1)


# Some claims contain no verb at all: "Media playback is now next."
_ACTION_PHRASES = re.compile(
    "is now (next|previous|playing|paused|muted|on|off|open|closed)"
    "|moved to the (next|previous)"
    "|now playing|now paused",
    re.IGNORECASE,
)


# Requests about messages, contacts, calls or email: handled on this PC.
_PRIVATE = re.compile(
    r"\b(?:whats\s?app|messages?|msg|sms|dm|texted|text\s+(?:to\s+)?(?!(?:on|in|of|from|at|the|file|size|box|editor|here|there|below|above)\b)(?:him|her|them|my|maa|papa|mom|dad|[a-z]+)|"
    r"call(?:ing)?\s+(?!it\b|this\b|that\b)\w+|ring|dial|phone\s+number|contacts?|e-?mails?|mail|inbox|reply|"
    r"tell\s+\w+\s+(?:that|to|i'?m|i\s+am|we)|let\s+\w+\s+know)\b", re.I)
PRIVATE_TOOLS = {"whatsapp_message", "whatsapp_call", "compose_email", "add_contact", "contact_info",
                 "list_contacts", "remove_contact", "inbox_summary", "call_number", "end_call",
                 "connect_gmail_drafts", "call_me"}


def _claims_success(text: str) -> bool:
    """A reply that says something happened - for when the tool just failed."""
    return _claims_action(text) or bool(re.search(
        r"\b(already (?:open|on|running|done|sent)|(?:is|are) (?:now )?open|opened|sent|launched|started|"
        r"playing|done|joined|set (?:to|for))\b", text, re.I))


def _why(error: str) -> str:
    """'ERROR in phone_open_app: TimeoutExpired: Command ...' -> a short reason."""
    e = re.sub(r"^ERROR(?: in \w+)?:\s*", "", error.strip())
    if "TimeoutExpired" in e or "timed out" in e.lower():
        return "it timed out (is the phone or app reachable?)."
    e = re.sub(r"^\w+(?:Error|Exception|Expired):\s*", "", e)
    return (e[:140].rstrip(" .") + ".") if e else "something went wrong."


def _claims_action(answer: str) -> bool:
    """True if the reply asserts a real-world change was made."""
    if not answer:
        return False
    if _NOT_A_CLAIM.search(answer):
        return False
    return bool(_ACTION_CLAIMS.search(answer) or _ACTION_PHRASES.search(answer))


def _truncate(text: str, limit: int = 4000) -> str:
    """Keep one tool result from eating the whole context window."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated, {len(text) - limit} more characters]"
