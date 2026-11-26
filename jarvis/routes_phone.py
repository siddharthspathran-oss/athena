"""Shortcuts for people and the phone: calls, messages, contacts, Teams meetings,
Bluetooth, and controlling the phone itself.

Split out of routes.py (31 Oct 2026); route() there calls these.
"""
from __future__ import annotations

import re

from .routes_common import _G_ASK, _R_LEAD


def _mentioned_contact(text: str) -> str | None:
    """The saved contact (or nickname) a sentence names, longest first."""
    try:
        from .tools import messaging
        contacts = messaging._load()
        names = list(contacts) + list(messaging._aliases(contacts))
    except Exception:
        return None
    low = text.lower()
    return next((n for n in sorted(names, key=len, reverse=True)
                 if len(n) > 2 and re.search(r"\b" + re.escape(n.lower()) + r"\b", low)), None)


def _direct_contact_q(text: str) -> tuple[str, dict] | None:
    """"What is Neha also known as?" - asked of the contact book. It went to
    the explainer and came back as "the Neha River" (4 Oct 2026)."""
    if not _G_ASK.match(text):
        return None
    if not re.search(r"\b(?:alternate|alternative|other|nick|also|aka|known|call|called|who|name)\w*\b", text, re.I):
        return None
    who = _mentioned_contact(text)
    return ("contact_info", {"name": who}) if who and who not in ("me", "myself", "self") else None


# Teams classes (tools/teams.py).
_TEAMS_LIST = re.compile(r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please)\W+)*(?:can\s+you\s+)?(?:please\s+)?"
                         r"(?=.*\b(?:teams?|online)\b)(?=.*\b(?:class|classes|meeting|meetings|lecture|lectures|calendar)\b)"
                         r"(?:what|which|any|show|list|tell\s+me|check|do\s+i\s+have|when|so)\b", re.I)


_TEAMS_JOIN = re.compile(r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please)\W+)*(?:can\s+you\s+)?(?:please\s+)?join\s+"
                         r"(?:my\s+|the\s+)?(?:(?P<next>next|current|ongoing)\s+)?(?:(?:online|teams)\s+)?"
                         r"(?P<what>class|meeting|lecture|call|.+?)(?:\s+(?:on|in)\s+teams)?\W*$", re.I)


# The phone over Wi-Fi (tools/android.py). First in the chain: "phone battery"
# must not be read as the laptop's, nor "open WhatsApp on my phone" as the PC app.
_P_LEAD = r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please|can\s+you|could\s+you)\W+)*"


_P_MY = r"(?:my\s+|the\s+)?(?:phone|mobile|galaxy|samsung)"


def _direct_phone_ctl(text: str) -> tuple[str, dict] | None:
    t = text.strip()
    # Anything about gestures goes to the gesture switch - never to the model:
    # "turn on gesture mode" misheard as "Tower now of gesture mode" went to
    # recall, which answered "gesture mode is currently on" when it was not (6 Oct 2026).
    if re.search(r"\bgestures?\b", t, re.I):
        if re.search(r"\b(?:off|stop|disable|close|end|exit|quit|kill|deactivate|cancel)\b", t, re.I):
            return "gesture_mode", {"action": "off"}
        if re.search(r"^\W*(?:(?:athena|jarvis)\W+)?(?:is|are|was|what'?s|what\s+is|status|whether|check)\b|\?\s*$", t, re.I) \
                and not re.search(r"\b(?:turn|switch|start|enable|activate)\b", t, re.I):
            return "gesture_mode", {"action": "status"}
        return "gesture_mode", {"action": "on"}
    every = bool(re.search(r"\b(?:on|in)\s+(?:all|every|both)\s+(?:(?:the|my)\s+)?(?:phones?|devices?|screens?|youtubes?)\b"
                           r"|\ball\s+(?:three|two|my)\s+(?:phones|devices|youtubes)\b", t, re.I))
    if re.match(_P_LEAD + r"how\s+many\s+(?:devices|phones)\b|" + _P_LEAD + r"(?:which|what)\s+devices\s+(?:are\s+)?connected", t, re.I):
        return "phone_devices", {}
    m = re.match(_P_LEAD + r"(?:search|look\s+up|find)\s+(?P<q>.+?)\s+(?:on|in)\s+(?:all\s+(?:three|two|the|my)\s+)?(?:the\s+)?you\s*tubes?\b", t, re.I)
    if m and (every or re.search(r"\b(?:phone|mobile|devices?|phones)\b", t, re.I)):
        return "phone_youtube", {"query": m.group("q"), "everywhere": every}
    m = re.match(_P_LEAD + r"(?:open|launch|start)\s+(?P<app>.+?)\s+(?:on|in)\s+(?:all|every|both)\b", t, re.I)
    if m and every:
        return "phone_open_everywhere", {"name": m.group("app")}
    if re.match(_P_LEAD + r"(?:show|open|mirror|cast)\s+(?:me\s+)?" + _P_MY + r"(?:'?s)?(?:\s+screen)?(?:\s+on\s+(?:the\s+)?(?:pc|laptop|screen))?\W*$", t, re.I):
        return "show_phone", {}
    m = re.match(_P_LEAD + r"(?:open|launch|start)\s+(?P<app>.+?)\s+(?:on|in)\s+" + _P_MY + r"\W*$", t, re.I)
    if m:
        return "phone_open_app", {"name": m.group("app")}
    if re.match(_P_LEAD + r"(?:where'?s|where\s+is|find|ring)\s+" + _P_MY + r"\W*$", t, re.I) \
            or re.match(_P_LEAD + r"i\s+(?:can'?t|cannot)\s+find\s+" + _P_MY + r"\W*$", t, re.I):
        return "find_my_phone", {}
    if re.search(r"\b(?:battery|charge|charging)\b", t, re.I) and re.search(r"\b(?:phone|mobile)\b", t, re.I):
        return "phone_status", {}
    if re.match(_P_LEAD + r"lock\s+" + _P_MY + r"\W*$", t, re.I):
        return "lock_phone", {}
    if re.match(_P_LEAD + r"(?:take\s+(?:a\s+)?)?(?:screenshot|screen\s*shot)\s+(?:of|on)\s+" + _P_MY + r"\W*$|"
                + _P_LEAD + r"(?:take\s+(?:a\s+)?)?(?:phone|mobile)\s+screenshot\W*$", t, re.I):
        return "phone_screenshot", {}
    m = re.match(_P_LEAD + r"(?P<a>pause|play|resume|next|skip|previous|mute|volume\s+up|volume\s+down|turn\s+(?:it\s+)?up|turn\s+(?:it\s+)?down)"
                 r"(?:\s+(?:the\s+)?(?:music|song|track|video))?\s+(?:on|in)\s+" + _P_MY + r"\W*$", t, re.I)
    if m:
        a = m.group("a").lower()
        a = {"resume": "play", "skip": "next", "turn up": "volume up", "turn it up": "volume up",
             "turn down": "volume down", "turn it down": "volume down"}.get(a, a)
        return "phone_media", {"action": re.sub(r"\s+", " ", a)}
    return None


def _direct_teams(text: str) -> tuple[str, dict] | None:
    # "do I have any MS meetings?" came back as "MS Mutings" and the model
    # invented a "mute" tool (3 Oct 2026).
    # "when is my next MS team meeting" (team, singular) missed this and the
    # model made up "tomorrow at 14:00" (3 Oct 2026).
    day = ("next" if re.search(r"\bnext\b|\bwhen\b", text, re.I) else
           "tomorrow" if re.search(r"tomorrow", text, re.I) else "today")
    if re.search(r"\b(?:ms|microsoft|m\.s\.)\s+(?:teams?\s+)?(?:meetings?|mutings?|meating?s?|classes|calls?)\b", text, re.I):
        return "teams_meetings", {"day": day}
    m = _TEAMS_LIST.match(text)
    if m:
        return "teams_meetings", {"day": day}
    # "do you have any meetings today" - heard for "do I"; the model invented a
    # list_meetings tool for it (16 Oct 2026, found by the nightly replay).
    if re.match(_R_LEAD + r"(?:do|have)\s+(?:i|you|we)\s+(?:have|got)\s+(?:any\s+)?(?:online\s+)?meetings?"
                r"(?:\s+(?:today|tomorrow|now|next))?\W*$", text, re.I):
        return "teams_meetings", {"day": day}
    m = _TEAMS_JOIN.match(text)
    if m and (re.search(r"\bteams\b", text, re.I) or m.group("what").lower() in ("class", "meeting", "lecture")):
        what = m.group("what").strip()
        return "join_teams_meeting", {"which": "next" if what.lower() in ("class", "meeting", "lecture", "call")
                                      else what}
    return None


# Bluetooth. "connect to echo" reached the model, which had no connect action
# and announced a connection that never happened (3 Oct 2026).
_BT_LEAD = r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please)\W+)*(?:can\s+you\s+|could\s+you\s+)?(?:please\s+)?"


_BT_CONNECT = re.compile(_BT_LEAD + r"(?:connect|pair)\s+(?:(?:to|with)\s+)?(?:my\s+|the\s+)?"
                         r"(?P<d>[\w'+.\- ]{2,40}?)(?:\s+(?:via|over|on|through|with)\s+bluetooth)?\W*$", re.I)


_BT_TOGGLE = re.compile(_BT_LEAD + r"(?:turn|switch)\s+(?:(?P<a>on|off)\s+(?:the\s+)?bluetooth|"
                        r"(?:the\s+)?bluetooth\s+(?P<b>on|off))\W*$|" + _BT_LEAD + r"bluetooth\s+(?P<c>on|off)\W*$", re.I)


def _direct_bluetooth(text: str) -> tuple[str, dict] | None:
    m = _BT_CONNECT.match(text)
    if m and not re.search(r"\b(?:wi-?fi|internet|vpn|network|hotspot|server|database|it|that|this)\b",
                           m.group("d"), re.I):
        return "bluetooth", {"action": "connect", "device": m.group("d").strip()}
    m = _BT_TOGGLE.match(text)
    if m:
        return "bluetooth", {"action": (m.group("a") or m.group("b") or m.group("c")).lower()}
    return None


# "call me" / "test call": the user's own phone, not a contact. Anchored at
# both ends and limited to "me"/"my phone" so "call mom" and "call papa on
# whatsapp" still reach _direct_call - and it runs first, because a contact
# saved as "me" would otherwise turn "call me" into a WhatsApp call to
# yourself.
_PHONE_ME = re.compile(
    r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please|now|just)\W+)*"
    r"(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?"
    r"(?:"
    r"(?:(?:give\s+me|do|place|make|send\s+me|run)\s+a\s+)?test\s+(?:phone\s+)?call"
    r"|(?:call|ring|phone)\s+(?:me|my\s+(?:phone|mobile|cell))(?:\s+on\s+my\s+(?:phone|mobile|cell))?"
    r"|give\s+me\s+a\s+(?:call|ring)"
    r")"
    r"(?:\s+(?:now|right\s+now))?(?:\s+please)?\W*$",
    re.I,
)


def _direct_phone_me(text: str) -> tuple[str, dict] | None:
    """Turn "call me" / "test call" into a call to the user's own phone."""
    if not _PHONE_ME.match(text.strip()):
        return None
    from .phone import TEST_MESSAGE

    return "call_me", {"message": TEST_MESSAGE}


def _direct_call(text: str) -> tuple[str, dict] | None:
    """Turn 'call <contact>' into a whatsapp_call, when the name is known."""
    from .tools.messaging import parse_call_request

    parsed = parse_call_request(text)
    if parsed is None:
        return None
    return "whatsapp_call", {"to": parsed[0], "video": parsed[1]}


def _direct_message(text: str) -> tuple[str, dict] | None:
    """Turn an obvious WhatsApp request straight into a whatsapp_message call.

    Safe to route: the tool opens the chat with the text typed and never
    sends. A wrong guess costs a window, not a sent message.
    """
    from .tools.messaging import parse_request

    parsed = parse_request(text)
    if parsed is None:
        return None
    return "whatsapp_message", {"to": parsed[0], "text": parsed[1]}
