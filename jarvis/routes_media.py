"""Shortcuts for music: play a song, a track by number, next/previous.

Split out of routes.py (31 Oct 2026); route() there calls these.
"""
from __future__ import annotations

import re


# "Play the first song" went to the media keys, which can only skip, so it
# played the second. The position is stated plainly enough to read.
_ORDINALS = {
    "first": 1, "1st": 1, "one": 1, "second": 2, "2nd": 2, "two": 2,
    "third": 3, "3rd": 3, "three": 3, "fourth": 4, "4th": 4, "four": 4,
    "fifth": 5, "5th": 5, "five": 5, "sixth": 6, "6th": 6, "six": 6,
    "seventh": 7, "7th": 7, "seven": 7, "eighth": 8, "8th": 8, "eight": 8,
    "ninth": 9, "9th": 9, "nine": 9, "tenth": 10, "10th": 10, "ten": 10,
    "eleventh": 11, "11th": 11, "eleven": 11, "twelfth": 12, "12th": 12,
    "twelve": 12, "thirteenth": 13, "13th": 13, "thirteen": 13,
    "fourteenth": 14, "14th": 14, "fourteen": 14, "fifteenth": 15,
    "15th": 15, "fifteen": 15, "twentieth": 20, "20th": 20, "twenty": 20,
    "last": -1, "final": -1,          # play_track scrolls to the end of the list
}


# Speech does not deliver "the seventh song" tidily. It came back as "a seven
# song" and as "fifth number music", so the article, the filler and whatever
# noun lands at the end all have to be optional.
_TRACK_CMD = re.compile(
    r"^\W*(?:(?:athena|jarvis)\W+)?(?:please\s+)?(?:can you\s+)?"
    r"play\s+(?:me\s+)?(?:the\s+|a\s+|an\s+)?"
    r"(?:(?:song|track|number)\s+){0,2}(?:the\s+)?"
    r"(?P<num>\d+|" + "|".join(sorted(_ORDINALS, key=len, reverse=True)) + r")"
    r"(?:\s*(?:st|nd|rd|th))?"
    r"(?:\s+(?:number|song|songs|track|music|one))*"
    r"(?:\s+(?:of|in|from|on)\s+(?:my|the|this)\s+(?:playlist|list|queue|album|liked songs))?"
    r"\W*$",
    re.I,
)


def _direct_track(text: str) -> tuple[str, dict] | None:
    """Turn 'play the first song' into play_track, not a media-key skip."""
    m = _TRACK_CMD.match(text.strip())
    if not m:
        return None
    raw = m.group("num").lower()
    number = int(raw) if raw.isdigit() else _ORDINALS.get(raw, 0)
    if number == 0:
        return None
    return "play_track", {"position": number}


# "Play <something>" where the something is a song, not a position and not
# one of the words that mean "just play anything".
_SONG_GENERIC = {
    "music", "song", "songs", "some music", "something", "anything",
    "next", "previous", "last", "spotify", "playlist", "my playlist",
    "a song", "some songs", "it", "that", "this", "one", "more",
    "this song", "that song", "the song", "it again", "this one",
}


_SONG_CMD = re.compile(
    r"^\W*(?:(?:athena|jarvis)\W+)?(?:please\s+)?(?:can you\s+)?"
    r"play\s+(?:me\s+)?(?P<q>.+?)\s*\W*$",
    re.I,
)


def _direct_song(text: str) -> tuple[str, dict] | None:
    """Turn 'play <song>' into a Spotify search, once nothing else fits.

    Runs after the position and app routers, so "play the third song" and
    "play spotify" are already gone by the time this sees anything.
    """
    m = _SONG_CMD.match(text.strip())
    if not m:
        return None
    query = re.sub(r"\s+", " ", m.group("q")).strip()
    trimmed = re.sub(r"^(?:the|a|an)\s+", "", query, flags=re.I)
    trimmed = re.sub(r"\s+(?:on|in)\s+spotify$", "", trimmed, flags=re.I).strip()
    # "something by arijit singh" searched Spotify for the word "something".
    trimmed = re.sub(r"^(?:something|anything|songs?|music|some\s+(?:songs?|music)|a\s+song)\s+"
                     r"(?:by|from|of)\s+", "", trimmed, flags=re.I).strip()

    low = trimmed.lower()
    # "songs that I like" is a request for her pick, not a title.
    if re.match(r"^(?:songs?|music|tracks?)\s+(?:that\s+)?i\s+(?:like|love|listen\s+to)\b", low):
        return "play_my_taste", {}
    if not low or low in _SONG_GENERIC:
        return None
    if re.match(r"^(?:next|previous|last|some|any|more)\b", low):
        return None
    if "playlist" in low:
        return None
    if len(low) < 4 and len(low.split()) == 1:
        return None
    return "play_song", {"query": trimmed}
