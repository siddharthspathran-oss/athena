"""Your taste in music, and a song picked from it.

What you listen to is noted in data/music.jsonl:
  - a sampler in the web process notes whatever Spotify is playing once it
    has played for 30 seconds - songs you skip straight away never count;
  - the first time, past "Playing <artist> - <title>" answers are imported,
    but only those whose request actually named the song.

"Play a music of your choice" / "surprise me" picks from that: tracks you
play often come up more, and nothing from the last few plays repeats.
"""
from __future__ import annotations

import json
import random
import re
import threading
import time

from ..config import DATA
from ..registry import tool

TAG = ["media"]
LOG = DATA / "music.jsonl"
_lock = threading.Lock()


def _read() -> list[dict]:
    if not LOG.exists():
        _seed()
    out = []
    try:
        for ln in LOG.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(ln))
            except ValueError:
                continue
    except OSError:
        pass
    return out


def note(artist: str, title: str, source: str) -> None:
    artist, title = artist.strip(), title.strip()
    if not title:
        return
    with _lock:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.time(), "artist": artist, "title": title,
                                "source": source}, ensure_ascii=False) + "\n")


# Words that say nothing about a song: a request sharing only these with the
# result did not name it ("play the music. Thank you." -> "Thank You For The Love").
_NOISE = {"play", "the", "music", "song", "songs", "some", "thank", "thanks", "you", "okay",
          "please", "athena", "jarvis", "open", "spotify", "and", "with", "this", "that", "it's"}


def _seed() -> None:
    """Import past plays from the conversation history, once."""
    LOG.parent.mkdir(parents=True, exist_ok=True)
    from .. import db

    rows = []
    for table in ("voice_log", "journal"):
        last_user = ""
        for r in db.rows(table):
            if r.get("role") == "user":
                last_user = str(r.get("content", ""))
                continue
            m = re.match(r"(?:Launched Spotify\.\s*)?Playing (.+?) - (.+?)\.?$", str(r.get("content", "")).strip())
            if not m:
                continue
            asked = set(re.findall(r"[a-z0-9']{3,}", last_user.lower())) - _NOISE
            got = set(re.findall(r"[a-z0-9']{3,}", (m.group(1) + " " + m.group(2)).lower()))
            if asked & got:
                rows.append({"t": r.get("t", 0), "artist": m.group(1), "title": m.group(2), "source": "history"})
    seen = set()
    with LOG.open("w", encoding="utf-8") as f:
        for r in rows:
            k = (r["artist"].lower(), r["title"].lower(), int(r["t"]))
            if k in seen:
                continue
            seen.add(k)
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


NAMES = DATA / "music_names.json"


def _spotify_history() -> list[str]:
    """Track IDs from Spotify's own play history on this PC, oldest first.
    The desktop app keeps it in list_player_play_history; only the IDs are
    read."""
    import os
    from pathlib import Path
    ids: list[str] = []
    roots = [Path(os.environ.get("APPDATA", "")) / "Spotify" / "Users"]
    pk = Path(os.environ.get("LOCALAPPDATA", "")) / "Packages"
    if pk.exists():
        roots += [d / "LocalState" / "Spotify" / "Users" for d in pk.glob("SpotifyAB.SpotifyMusic_*")]
    for root in roots:
        for f in root.glob("*/list_player_play_history"):
            try:
                ids += [m.decode() for m in re.findall(rb"spotify:track:([A-Za-z0-9]{22})", f.read_bytes())]
            except OSError:
                continue
    return ids


def _name(track_id: str) -> dict | None:
    """Title and artist for a Spotify track ID, from its public page. Cached."""
    import html
    import urllib.request
    try:
        cache = json.loads(NAMES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    if track_id in cache:
        return cache[track_id]
    try:
        req = urllib.request.Request(f"https://open.spotify.com/track/{track_id}",
                                     headers={"User-Agent": "Mozilla/5.0"})
        page = urllib.request.urlopen(req, timeout=8).read().decode("utf-8", "replace")
        t = html.unescape(re.search(r"<title>(.*?)</title>", page, re.S).group(1))
        m = re.match(r"^(.*?) - (?:song|single)(?: and lyrics)? by (.*?) \| Spotify", t)
        if not m:
            return None
        got = {"title": m.group(1).strip(), "artist": m.group(2).strip()}
    except Exception:
        return None
    cache[track_id] = got
    try:
        NAMES.write_text(json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8")
    except OSError:
        pass
    return got


def _pick_spotify() -> dict | None:
    ids = _spotify_history()
    if not ids:
        return None
    counts: dict[str, int] = {}
    for i in ids:
        counts[i] = counts.get(i, 0) + 1
    recent = set(ids[-5:])
    mine = {(r["artist"].lower(), r["title"].lower()) for r in _read()[-5:]}
    pool = [i for i in counts if i not in recent] or list(counts)
    for _ in range(4):
        i = random.choices(pool, weights=[counts[x] for x in pool])[0]
        got = _name(i)
        if got and (got["artist"].lower(), got["title"].lower()) not in mine:
            return got
    return None


def pick() -> dict | None:
    """A track from your history, weighted by how often you played it,
    never one of the last five. Spotify's own history first; Athena's
    notes when Spotify has none."""
    got = _pick_spotify()
    if got:
        return got
    rows = _read()
    if not rows:
        return None
    recent = {(r["artist"].lower(), r["title"].lower()) for r in rows[-5:]}
    counts: dict[tuple, int] = {}
    shown: dict[tuple, dict] = {}
    for r in rows:
        k = (r["artist"].lower(), r["title"].lower())
        counts[k] = counts.get(k, 0) + 1
        shown[k] = r
    pool = [k for k in counts if k not in recent] or list(counts)
    k = random.choices(pool, weights=[counts[x] for x in pool])[0]
    return shown[k]


@tool(tags=TAG + ["route_only"])
def play_my_taste() -> str:
    """Play a song Athena picks herself, from what the user listens to. Use for
    "play a music of your choice", "play something", "surprise me",
    "play something I like"."""
    from .system import play_song
    choice = pick()
    if choice is None:
        return play_song("chill hits")
    note(choice["artist"], choice["title"], "picked")   # so the next pick is a different one
    return play_song(f"{choice['title']} {choice['artist']}".strip())


@tool(tags=TAG + ["route_only"])
def play_my_playlist() -> str:
    """Play the user's own Spotify playlist, the one saved in memory. Use for
    "play my playlist", "put on my playlist"."""
    import subprocess
    from ..memory import MEM_FILE
    from .. import uia, winapi
    from .system import _spotify_hwnd
    try:
        facts = json.loads(MEM_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        facts = {}
    link = next((str(v) for k, v in facts.items() if "playlist" in k.lower()), "")
    m = re.search(r"playlist[/:]([A-Za-z0-9]{22})", link)
    if not m:
        return ("I don't have your playlist saved. Say 'remember my spotify playlist is' "
                "followed by its link.")
    # The URI opens the playlist page and starts Spotify if it is closed.
    subprocess.Popen(["explorer.exe", f"spotify:playlist:{m.group(1)}"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    hwnd = None
    for _ in range(20):
        time.sleep(1.0)
        hwnd = _spotify_hwnd()
        if hwnd:
            break
    if not hwnd:
        return "Spotify didn't open."
    time.sleep(2.5)                      # the playlist page loading
    before = winapi._title(hwnd)
    winapi.focus_hwnd(hwnd)
    uia.wake(hwnd, settle=0.6)
    # The playlist's big Play button, top of the page - the same one play_song uses.
    play = next((e for e in uia.elements(hwnd, uia.BUTTON)
                 if e.on_screen and e.name.strip().startswith("Play")
                 and e.rect[0] > 400 and e.rect[1] < 500), None)
    if play is None or not play.press(hwnd):
        return "Opened your playlist in Spotify - press play there."
    for _ in range(8):
        time.sleep(0.7)
        now = winapi._title(hwnd)
        if now and "spotify" not in now.lower() and now != before:
            return f"Playing your playlist - {now}."
    return "Opened your playlist and pressed play."


def start_sampler(every: float = 20.0) -> None:
    """Note each Spotify track you listen to for 30 s or more."""
    def loop():
        from ..ui.views import _media_session
        # Name every song in Spotify's history once (cached), so the Brain
        # page shows all your artists, not only the ones already picked.
        for tid in dict.fromkeys(_spotify_history()):
            _name(tid)
            time.sleep(0.5)
        done: tuple | None = None
        while True:
            time.sleep(every)
            try:
                s = _media_session()
            except Exception:
                continue
            if not s or "spotify" not in s.get("app", "").lower() or not s.get("playing"):
                continue
            k = (s.get("artist", ""), s.get("title", ""))
            if k != done and s.get("position", 0) >= 30 and k[1]:
                done = k
                note(k[0], k[1], "listened")
    threading.Thread(target=loop, daemon=True, name="athena-taste").start()
