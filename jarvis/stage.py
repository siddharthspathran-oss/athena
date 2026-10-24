"""The Stage (24 Oct 2026, after Hedwig): a small floating panel that comes up
when you talk to Athena - your words appear as you say them, a mascot reacts,
and what she did pops up as a card instead of a window opening somewhere.

The panel is its own process (`run.py stage`, ui/stage_window.py) showing the
page ui/stage.html. This module is the voice side: it decides the cards and
puts them, and the words heard so far, into the live file the panel reads.

Cards (live "card": {id, t, type, ...}):
    music    what's playing (Windows' media session), with controls
    console  a Deck console Athena started on a task, its screen as it works
    web      a page that allows being shown inside another (YouTube via its embed)
    answer   a long answer, to read as well as hear
"""
from __future__ import annotations

import re
import time
import uuid

from . import live

MUSIC = {"music", "play_song", "play_track", "play_my_taste", "now_playing"}
ANSWER_MIN = 220                    # characters: shorter answers are just said
_last = {"t": 0.0}


def show(card: dict) -> None:
    _last["t"] = time.time()
    live.set_value("card", {**card, "id": uuid.uuid4().hex[:8], "t": _last["t"]})


def shown_since(t: float) -> bool:
    return _last["t"] >= t


def partial(text: str) -> None:
    """The words heard so far, while you're still holding the key."""
    live.set_value("partial", text)


def up() -> bool:
    """Is the panel running? (run.py holds a named mutex per process.)"""
    import ctypes
    k = ctypes.windll.kernel32
    h = k.OpenMutexW(0x00100000, False, "Local\\Athena_stage")          # SYNCHRONIZE
    if h:
        k.CloseHandle(h)
    return bool(h)


def card_for(tool: str, args: dict, result: str) -> dict | None:
    """The card for a tool that just ran, if it has one."""
    if str(result or "").startswith("ERROR"):
        return None
    if tool in MUSIC:
        return {"type": "music"}
    if tool == "deck_spawn" and (args or {}).get("task"):
        from . import deck_link
        jobs = deck_link._jobs_file()
        cid, job = max(jobs.items(), key=lambda kv: kv[1]["started"], default=(None, None))
        if job and time.time() - job["started"] < 90:
            return {"type": "console", "console": cid, "title": job["title"], "who": job["who"]}
    return None


def on_step(tool: str, args: dict, result: str) -> None:
    card = card_for(tool, args, result)
    if card:
        show(card)


def answer_card(said: str, answer: str) -> dict | None:
    a = (answer or "").strip()
    if len(a) < ANSWER_MIN and a.count("\n") < 2:
        return None
    return {"type": "answer", "title": re.sub(r"^\W*(?:athena|jarvis)\W+", "", said or "", flags=re.I)[:90], "text": a[:8000]}


_YT = re.compile(r"(?:youtube\.com/watch\?(?:.*&)?v=|youtu\.be/|youtube\.com/shorts/)([\w-]{11})")


def embeddable(url: str) -> str:
    """The address to show inside a card, or "" to open a browser as before.
    Only what is sure to show and costs no wait: YouTube (through its embed
    player) and sites on this PC. Most big sites refuse to be framed, and
    asking each first would hold every "open X" up for a second or more."""
    m = _YT.search(url)
    if m:
        return f"https://www.youtube.com/embed/{m.group(1)}?autoplay=1"
    if re.match(r"https?://(?:localhost|127\.0\.0\.1)(?::\d+)?(?:/|$)", url):
        return url
    return ""


def web_card(url: str, title: str = "") -> bool:
    """Show a page in the panel instead of opening a browser - when the panel is up
    and the page allows it. True if it's on the panel."""
    try:
        if not up():
            return False
    except Exception:
        return False
    src = embeddable(url)
    if not src:
        return False
    host = re.sub(r"^https?://(?:www\.)?", "", url).split("/")[0]
    show({"type": "web", "url": src, "open": url, "title": title or host})
    return True


if __name__ == "__main__":
    from unittest import mock

    with mock.patch.object(live, "set_value", lambda k, v: None):
        assert card_for("music", {"action": "play"}, "Playing Scuba Juke.") == {"type": "music"}
        assert card_for("music", {}, "ERROR: Spotify isn't running.") is None
        assert card_for("open_app", {"name": "notepad"}, "Opened Notepad.") is None
        with mock.patch("jarvis.deck_link._jobs_file", lambda: {"c1": {"title": "Peptide shop website", "who": "Claude",
                                                                         "started": time.time() - 5, "done": 0}}):
            assert card_for("deck_spawn", {"task": "build a site"}, "Opened 1 Claude console")["console"] == "c1"
            assert card_for("deck_spawn", {"task": ""}, "Opened 2 Claude consoles") is None
        t0 = time.time()
        show({"type": "music"})
        assert shown_since(t0)
    assert answer_card("Athena, explain fourier series", "short") is None
    assert answer_card("Athena, explain fourier series", "x" * 300)["title"] == "explain fourier series"
    assert embeddable("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=3") == "https://www.youtube.com/embed/dQw4w9WgXcQ?autoplay=1"
    assert embeddable("http://localhost:5173/") == "http://localhost:5173/" and embeddable("https://github.com") == ""
    print("stage: checks pass")
