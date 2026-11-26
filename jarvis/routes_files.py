"""Shortcuts for files and folders: open a file or folder, find a document, send a
file to the phone or take one from it.

Split out of routes.py (31 Oct 2026); route() there calls these.
"""
from __future__ import annotations

import re

from .routes_common import _QUESTION_WORD, _best_file


_LEAD = (r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please|now|just|and|um|uh|so|then)\W+)*"
         r"(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?")


_EXPLORER_CMD = re.compile(
    _LEAD + r"(?:open|launch|start|show|bring\s+up|pull\s+up)\s+(?:up\s+)?"
    r"(?:the\s+|my\s+)?(?:file\s+explorer|windows\s+explorer|explorer|"
    r"file\s+manager|files\s+app|my\s+files|this\s+pc)"
    r"(?:\s+(?:app|window))?(?:\s+please)?\W*$", re.I)


_FOLDER_CMD = re.compile(
    _LEAD + r"(?P<verb>open|show(?:\s+me)?|go\s+to|take\s+me\s+to|browse|"
    r"bring\s+up|pull\s+up)\s+(?:up\s+)?(?P<my>the\s+|my\s+)?"
    r"(?P<name>.+?)(?:\s+(?P<suffix>folder|directory))?"
    r"(?P<exp>\s+in\s+(?:the\s+)?(?:file\s+)?explorer)?(?:\s+please)?\W*$", re.I)


# Known folders a bare "open X" may mean. "music" is left out on purpose -
# "open music" is Spotify here (APP_ALIASES) - and "desktop" alone only with
# open/go to, since "show desktop" is the minimise-everything gesture.
_BARE_FOLDERS = {"downloads", "download", "documents", "my documents",
                 "pictures", "onedrive", "one drive", "desktop", "videos"}


_REVEAL_CMD = re.compile(
    _LEAD + r"(?:"
    r"(?:show|find|locate|reveal|open)\s+(?:me\s+)?(?:where\s+)?(?:the\s+|my\s+)?"
    r"(?:file\s+|folder\s+)?(?P<a>.+?)\s+(?:is\s+)?in\s+(?:the\s+)?(?:file\s+)?explorer"
    r"|(?:where\s+is|where'?s|show\s+me\s+where)\s+(?:the\s+|my\s+)?"
    r"(?P<file>file\s+|folder\s+)?(?P<b>.+?)(?:\s+is)?"
    r"(?:\s+(?:saved|stored|located|kept))?"
    r")\W*$", re.I)


_OPEN_CMD = re.compile(
    _LEAD + r"(?:open|view|display|show\s+me|pull\s+up|bring\s+up)\s+(?:up\s+)?"
    r"(?P<q>.+?)(?:\s+(?:for\s+me|please))?\W*$", re.I)


# Words said about a file, never about an app or a website.
_FILE_HINT = re.compile(
    r"\b(?:file|document|pdf|docx?|pptx?|xlsx?|csv|txt|power\s*point|"
    r"presentation|slides|spreadsheet|word\s+(?:file|doc)|excel\s+file|"
    r"screenshot|image|photo|picture|video|notes|report|assignment|"
    r"worksheet|syllabus)\b|\.\w{2,5}$|\s+dot\s+\w{2,5}$", re.I)


_NOT_A_FILE = re.compile(
    r"^(?:a\s+)?(?:new|blank)\b|https?:|www\.|\.(?:com|org|net|in|io)\b|"
    r"\s(?:and|then)\s+(?:open|play|search|send|call|close|start|tell)\b", re.I)


def _confident_file(q: str) -> bool:
    b = _best_file(q)
    if b is None:
        return False
    e = b.entry
    ext = e.name.rsplit(".", 1)[-1].lower() if "." in e.name else ""
    # Only the user's own folders, only files, never an installer: "open
    # valorant" must not run "Install VALORANT.exe" out of Downloads, and
    # "open youtube" must not find a youtube.png deep inside a driver folder.
    return (e.rank == 0 and not e.is_dir and ext not in ("exe", "msi")
            and b.coverage >= 0.85)     # no guessing on a misheard word


def _direct_folder(text: str) -> tuple[str, dict] | None:
    """File Explorer, a known folder, or "show X in explorer" / "where is X"."""
    t = text.strip()
    if _EXPLORER_CMD.match(t):
        return "open_folder", {"name": "explorer"}

    m = _REVEAL_CMD.match(t)
    if m:
        if m.group("a"):
            return "reveal_path", {"path": m.group("a").strip()}
        target = m.group("b").strip()
        # "where is my phone" is not a file question. Only take it when it
        # says file, or the name really is a file in the user's folders.
        if m.group("file") or _FILE_HINT.search(target) or _confident_file(target):
            return "reveal_path", {"path": target}
        return None

    m = _FOLDER_CMD.match(t)
    if not m:
        return None
    name = re.sub(r"\s+", " ", m.group("name")).strip().lower()
    verb = m.group("verb").lower()
    explicit = bool(m.group("suffix") or m.group("exp"))
    from .tools.finder import FOLDER_WORDS

    if name in FOLDER_WORDS and name not in ("home", "user folder", "photos folder"):
        if explicit:
            return "open_folder", {"name": name}
        if name in _BARE_FOLDERS:
            if name == "desktop" and not verb.startswith(("open", "go", "take")):
                return None
            return "open_folder", {"name": name}
        return None
    if m.group("suffix") and not _NOT_A_FILE.search(name):
        return "open_folder", {"name": name}   # "open the ltspice folder"
    return None


def _direct_file(text: str) -> tuple[str, dict] | None:
    """Turn "open <file>" into open_file once it is clearly not an app.

    Runs after _direct_app. Taken when the words say it is a file ("pdf",
    "notes", "resume.docx") or the name really is a file in the user's own
    folders; otherwise the model gets it.
    """
    m = _OPEN_CMD.match(text.strip())
    if not m:
        return None
    q = re.sub(r"\s+", " ", m.group("q")).strip()
    if not q or _NOT_A_FILE.search(q) or _QUESTION_WORD.match(q):
        return None
    if _FILE_HINT.search(q) or _confident_file(q):
        return "open_file", {"path": q}
    return None


# "search my documents for signals and systems", "find the lab manual on my
# PC", "look for my DSP notes in my files".
_DOC_FIND = re.compile(
    r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please|now|just)\W+)*(?:(?:can|could|would|will)\s+you\s+)?"
    r"(?:please\s+)?(?:search|find|look\s+for|look\s+up|locate|check)\s+"
    r"(?:(?:in|through)\s+)?(?:my|the)\s+(?:documents?|docs|files|pc|computer|laptop|downloads|"
    r"drives?|folders?|notes)\s+(?:for|about)\s+(?:the\s+|my\s+)?(?P<a>.+?)(?:\s+please)?\W*$"
    r"|^\W*(?:(?:athena|jarvis|hey|ok|okay|please|now|just)\W+)*(?:(?:can|could|would|will)\s+you\s+)?"
    r"(?:please\s+)?(?:search|find|look\s+for|locate)\s+(?:for\s+)?(?:the\s+|my\s+)?(?P<b>.+?)"
    r"\s+(?:in|on|from)\s+(?:my|the)\s+(?:documents?|docs|files|pc|computer|laptop|downloads|drives?|folders?)"
    r"(?:\s+please)?\W*$", re.I)


# "open all my signals and systems files", "open all the DSP pdfs".
_OPEN_ALL = re.compile(
    r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please|now|just)\W+)*(?:(?:can|could|would|will)\s+you\s+)?"
    r"(?:please\s+)?(?:open|show|pull\s+up)\s+(?:up\s+)?(?:all|every|each)\s+(?:of\s+)?(?:the\s+|my\s+)?"
    r"(?P<q>.+?)\s*(?:files?|documents?|docs|pdfs?|notes|slides|ppts?|sheets?)\W*$", re.I)


# ---------------------------------------------------------------- phone files
# "send the lab manual to my phone", "share it with my phone", and - typed on
# the phone itself - just "send me the lab manual".
_NOT_A_FILE_TO_SEND = re.compile(
    r"^(?:a|an|the|my)?\s*(?:message|text|msg|whatsapp|email|mail|reminder|notification|"
    r"call|ring|alert|push|update|reply)\b", re.I)


_SEND_TO_PHONE = re.compile(
    r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please|now|just)\W+)*(?:(?:can|could|would|will)\s+you\s+)?"
    r"(?:please\s+)?(?P<verb>(?:send|share|transfer|move|copy|put|push|get)\s+)?(?:me\s+)?(?:over\s+)?"
    r"(?P<q>.+?)\s+(?:to|on|onto|with|over\s+to|on\s+to)\s+(?:my\s+|the\s+)?(?:phone|mobile|cell(?:\s*phone)?)"
    r"(?:\s+(?:please|now))*\W*$", re.I)


# Whisper drops a short "send" after the name: "Athena, send the reel to my
# phone" came out as "Athena reel to my phone", nothing ran, and the model
# made up "a timeout issue" (9 Oct 2026). Without a verb, it is still a send -
# unless what is left is itself an action ("connect to my phone").
_NOT_A_FILE_NO_VERB = re.compile(
    r"^(?:connect|open|show|play|cast|mirror|switch|turn|lock|unlock|call|ring|text|message|go|come|reply|"
    r"talk|listen|look|stay|login|log|sign|pair|link|sync|it'?s|is|was|what|how|why|who|where|when)\b", re.I)


_SEND_ME = re.compile(
    r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please|now|just)\W+)*(?:(?:can|could|would|will)\s+you\s+)?"
    r"(?:please\s+)?(?:send|give|share|pass)\s+me\s+(?P<q>.+?)(?:\s+(?:please|now))*\W*$", re.I)


# "send me ..." typed on the phone, without "to my phone": a file is "the X"
# or "my X"; "send me a joke", "send me some ideas" are asks for an answer.
_NOT_A_FILE_TO_SEND_ME = re.compile(
    r"^(?:a|an|some|any|more|another)\b|^(?:the\s+)?(?:jokes?|story|poem|quote|facts?|summary|list|"
    r"links?|recipe|news|weather|info(?:rmation)?|details|answer|songs?|playlist|ideas?|tips?|"
    r"suggestions?|something|anything)\b", re.I)


_FROM_PHONE = re.compile(
    r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please|now|just)\W+)*(?:(?:can|could|would|will)\s+you\s+)?"
    r"(?:please\s+)?(?:open|show)\s+(?:me\s+)?(?:"
    r"(?:the\s+)?(?P<latest>last|latest|newest|recent)?\s*(?P<kind>files?|photos?|pictures?|documents?|pdfs?|things?)\s+"
    r"(?:(?:i|that\s+i|you)\s+)?(?:just\s+)?(?:sent|uploaded|shared|got|received)\s+(?:me\s+)?from\s+(?:my\s+|the\s+)?phone"
    r"|(?:the\s+)?(?:files?\s+)?from\s+(?:my\s+|the\s+)?phone(?:\s+folder)?"
    r"|what\s+(?:my\s+|the\s+)?phone\s+sent"
    r")\W*$", re.I)


def _direct_phone_files(text: str) -> tuple[str, dict] | None:
    t = text.strip()
    m = _FROM_PHONE.match(t)
    if m:
        kind = (m.group("kind") or "").lower()
        latest = m.group("latest") or (kind and not kind.endswith("s"))
        return "open_from_phone", {"which": "latest" if latest else "folder"}
    m = _SEND_TO_PHONE.match(t)
    if m and not m.group("verb") and _NOT_A_FILE_NO_VERB.match(m.group("q").strip()):
        m = None
    if not m:
        from . import share
        m = _SEND_ME.match(t) if share.from_phone else None
        if m and _NOT_A_FILE_TO_SEND_ME.match(m.group("q").strip()):
            m = None
    if m:
        q = re.sub(r"^(?:the|my|a|an)\s+", "", m.group("q").strip(" .?"), flags=re.I)
        if q and not _NOT_A_FILE_TO_SEND.match(q):
            return "send_to_phone", {"file": q}
    return None


def _direct_find_document(text: str) -> tuple[str, dict] | None:
    m = _OPEN_ALL.match(text.strip())
    if m and m.group("q").strip():
        q = m.group("q").strip()
        kind = re.search(r"(pdfs?|docs?|documents?|notes|slides|ppts?|sheets?)\W*$", text.strip(), re.I)
        if kind and kind.group(1).lower().startswith(("pdf", "slide", "ppt", "sheet")):
            q += " " + kind.group(1).lower().rstrip("s")     # keep the type: "dsp pdf"
        return "find_document", {"query": q, "open_all": True}
    # Obsidian notes (9 Oct 2026): "open my note on X", "find X in my notes".
    m = re.match(r"^\W*(?:(?:athena|hey|ok|okay|please)\W+)*(?:can\s+you\s+)?(?:open|show|find|get)\s+(?:me\s+)?(?:my\s+|the\s+)?"
                 r"notes?\s+(?:on|about|for|of)\s+(?P<a>.+?)\W*$|"
                 r"^\W*(?:(?:athena|hey|ok|okay|please)\W+)*(?:find|look\s+up|look\s+for)\s+(?P<b>.+?)\s+in\s+my\s+(?:notes|vault|obsidian)\W*$",
                 text.strip(), re.I)
    if m:
        return "find_document", {"query": (m.group("a") or m.group("b")).strip()}
    m = _DOC_FIND.match(text.strip())
    if not m:
        return None
    q = (m.group("a") or m.group("b") or "").strip(" .?")
    return ("find_document", {"query": q}) if q else None
