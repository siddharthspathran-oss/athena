"""Sound and music: the volume, what's playing (Windows' media session), Spotify
controls, and playing a particular song or a track by its place in a list.

Split out of system.py (31 Oct 2026); system.py imports all of it back.
"""
from __future__ import annotations

import re
import subprocess
import time

import psutil

from .. import uia, winapi
from ..registry import tool
from .system_apps import TAG, _match_app


# Apps that respond to the media keys. Browsers count: YouTube and Spotify Web
# both react to them.
_MEDIA_PLAYERS = {
    "spotify", "vlc", "wmplayer", "musicbee", "foobar2000", "itunes",
    "aimp", "winamp", "groove", "chrome", "msedge", "firefox", "opera",
    "opera_gx", "brave", "discord",
}


_MEDIA_SESSION_PS = r"""
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$as = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($t, $rt) {
    $m = $as.MakeGenericMethod($rt); $task = $m.Invoke($null, @($t))
    $null = $task.Wait(6000); $task.Result
}
[Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager,Windows.Media,ContentType=WindowsRuntime] | Out-Null
$mgr = Await ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager])
$out = @()
foreach ($s in $mgr.GetSessions()) {
    $out += "$($s.GetPlaybackInfo().PlaybackStatus)|$($s.SourceAppUserModelId)"
}
Write-Output ($out -join ",")
"""


def _media_status() -> str:
    """'playing', 'paused', 'none', or 'unknown'.

    Windows tracks media sessions properly, so ask it rather than guessing
    from process names - a browser being open says nothing about whether
    anything is actually playing.
    """
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _MEDIA_SESSION_PS],
            capture_output=True, text=True, timeout=20, check=False,
        )
        states = [s.split("|")[0].strip().lower() for s in (r.stdout or "").strip().split(",") if s.strip()]
    except Exception:
        return "unknown"
    if not states:
        return "none"
    if any(s == "playing" for s in states):
        return "playing"
    return "paused"


def _media_player_running() -> bool:
    for p in psutil.process_iter(["name"]):
        n = (p.info.get("name") or "").lower().removesuffix(".exe")
        if n in _MEDIA_PLAYERS:
            return True
    return False


def _endpoint_volume():
    """Get the master volume interface across pycaw versions.

    Recent pycaw hands back an AudioDevice wrapper exposing EndpointVolume;
    older releases return a raw IMMDevice that has to be Activated.
    """
    from pycaw.pycaw import AudioUtilities

    speakers = AudioUtilities.GetSpeakers()
    endpoint = getattr(speakers, "EndpointVolume", None)
    if endpoint is not None:
        return endpoint

    from ctypes import POINTER, cast

    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import IAudioEndpointVolume

    iface = speakers.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(iface, POINTER(IAudioEndpointVolume))


@tool(tags=TAG)
def set_volume(level: int = -1, step: int = 0) -> str:
    """Set the master system volume, or move it up or down.

    level: volume percentage from 0 to 100
    step: instead of a level, how much to move it: 10 for louder, -10 for quieter
    """
    try:
        ev = _endpoint_volume()
        if step:
            level = round(ev.GetMasterVolumeLevelScalar() * 100) + int(step)
        if int(level) < 0 and not step:
            return "ERROR: give a level from 0 to 100, or a step up or down."
        level = max(0, min(100, int(level)))
        ev.SetMasterVolumeLevelScalar(level / 100.0, None)
        return f"Volume set to {level}%."
    except Exception as e:
        return f"ERROR: volume control failed ({e}). Is pycaw installed?"


@tool(tags=TAG)
def get_volume() -> str:
    """Report the speaker loudness setting. This is about sound, not battery."""
    try:
        return f"{round(_endpoint_volume().GetMasterVolumeLevelScalar() * 100)}%"
    except Exception as e:
        return f"ERROR: {e}"


@tool(tags=TAG)
def music(action: str, song: str = "", position: int = 0) -> str:
    """Every music request: play, pause, skip, go back, play a named song,
    play the Nth song in Spotify's list, play something the user likes, or
    play their saved playlist. Works with Spotify, YouTube and any player,
    even in the background. "skip" and "next" move FORWARD.

    action: play, pause, next, previous, mute, song (play a named song, or an artist: "play arijit singh" -> song="arijit singh"), track (play song number `position` in the list), surprise (only when no song or artist is named) or playlist (their saved playlist)
    song: the song to play, optionally "by" the artist - for action song, or a title for action track
    position: which song in Spotify's list, counting from 1, or -1 for the last song - for action track
    """
    # One tool instead of five (music, play_song, play_track, play_my_taste,
    # play_my_playlist): the model kept picking the media key for a named
    # song and the song search for "next". The five still exist for the
    # routers; the model sees only this one.
    act = (action or "").strip().lower()
    if act in ("song", "search") or (act in ("play", "") and song.strip() and not position):
        return play_song(song)
    if act in ("track", "number") or position:
        return play_track(position=int(position or 0), name=song)
    if act in ("surprise", "choice", "random", "taste", "something"):
        from .taste import play_my_taste
        return play_my_taste()
    if act in ("playlist", "my playlist"):
        from .taste import play_my_playlist
        return play_my_playlist()
    action = act
    mapping = {
        "play": "playpause", "pause": "playpause", "playpause": "playpause",
        "resume": "playpause", "play music": "playpause", "music": "playpause",
        "next": "next", "next track": "next", "skip": "next", "forward": "next",
        "previous": "previous", "prev": "previous", "back": "previous",
        "stop": "stop", "mute": "mute", "unmute": "mute",
    }
    key = mapping.get(action.strip().lower())
    if not key:
        return f"ERROR: unknown media action {action}. Use play, pause, next, previous or mute."

    # The keystroke itself always "succeeds", so ask Windows what is actually
    # playing rather than reporting a result nothing received.
    if key != "mute":
        status = _media_status()
        if status == "none" and not _media_player_running():
            return ("Nothing is playing and no player is open, so there is nothing "
                    "to control. Open Spotify first, then ask again.")
        if status == "none":
            return ("A player is open but nothing is loaded, so the key would do "
                    "nothing. Start something playing first.")

    if not winapi.media_key(key):
        return f"ERROR: could not send {action}."

    if key == "mute":
        return "Sent mute."

    # Confirm the state actually changed rather than assuming it did.
    time.sleep(1.0)
    after = _media_status()
    if key == "playpause":
        return f"Playback is now {after}." if after in ("playing", "paused") else "Sent play/pause."
    return f"Sent {action}; playback is {after}."


@tool(tags=TAG + ["route_only"])
def now_playing() -> str:
    """What song is on. Asked "what song is playing", the model pressed Next
    twice and skipped it (28 Sep 2026)."""
    from ..ui.views import _media_session
    m = _media_session()
    if not m or not m.get("title"):
        return "Nothing is playing right now."
    who = f" by {m['artist']}" if m.get("artist") else ""
    return f"{'Playing' if m.get('playing') else 'Paused on'} {m['title']}{who}."


_TRACK_ROW = re.compile(r"^\s*(\d+)\s+Play\s+(.+?)\s*$")


def _spotify_hwnd() -> int | None:
    """Spotify's main window, found by process rather than by title.

    Matching the title worked only while nothing was playing: once a track
    starts, the window is renamed to "Artist - Track" and the word Spotify
    disappears, so the second request in a row could not find the app it had
    just been driving.
    """
    for hwnd, _title, pid in winapi.windows_with_pids():
        try:
            if psutil.Process(pid).name().lower().startswith("spotify"):
                return hwnd
        except Exception:
            continue
    return None


def _visible_tracks(hwnd: int) -> dict[int, tuple[str, tuple[int, int, int, int]]]:
    """{track number: (description, rect)} for the rows currently on screen.

    The rectangle matters as much as the name: the row's play button is found
    by sitting in the same horizontal band, which is steadier than matching
    two long strings that Spotify renders with its own duplication.
    """
    found: dict[int, tuple[str, tuple[int, int, int, int]]] = {}
    for el in uia.elements(hwnd, uia.DATA_ITEM):
        if not el.on_screen or not el.name:
            continue
        m = _TRACK_ROW.match(el.name)
        if m:
            found.setdefault(int(m.group(1)), (m.group(2).strip(), el.rect))
    return found


@tool(tags=TAG + ["route_only"])
def play_track(position: int = 0, name: str = "") -> str:
    """Play a particular song from the list showing in Spotify.

    Use this for "play the first song", "play track 3" or "play the one
    called archangel". The media keys cannot do this - they only skip - so
    asking for the first song with those plays the second.

    position: which song in the list, counting from 1; -1 for the last song
    name: part of the song title, if the position is not known
    """
    hwnd = _spotify_hwnd()
    if hwnd is None:
        return "Spotify is not open, so there is no track list to play from."
    # Foregrounding is refused occasionally, more often from the background
    # voice process than from a terminal. It is worth a second attempt before
    # telling the user it cannot be done.
    if not winapi.focus_hwnd(hwnd):
        time.sleep(0.6)
        if not winapi.focus_hwnd(hwnd):
            return ("Could not bring Spotify to the front - click its window "
                    "once, then ask again.")
    uia.wake(hwnd, settle=0.8)

    tracks = _visible_tracks(hwnd)
    if not tracks:
        return ("No track list is showing in Spotify - open a playlist or "
                "album first, then say which song to play.")

    # Only what is on screen can be read, and a playlist scrolled down cannot
    # see track 1 - "play the first song" failed with the first song sitting
    # just above the view. Scroll towards it before giving up.
    if not name.strip() and position:
        for _ in range(8):
            if position in tracks:
                break
            if position < min(tracks):
                direction = 3
            elif position > max(tracks):
                direction = -3
            else:
                break
            any_rect = next(iter(tracks.values()))[1]
            left, top, right, bottom = winapi.window_rect(hwnd)
            winapi.scroll(any_rect[0] + 60, (top + bottom) // 2, direction)
            time.sleep(0.5)
            uia.wake(hwnd, settle=0.3)
            fresh = _visible_tracks(hwnd)
            if not fresh or fresh == tracks:
                break
            tracks = fresh

    if position < 0 and not name.strip():
        # "the last song": scroll down until the list stops moving, then take
        # the highest number on screen. Asked three times on 3 Oct 2026, the
        # model guessed track 4, then found the song but never played it.
        for _ in range(60):
            any_rect = next(iter(tracks.values()))[1]
            left, top, right, bottom = winapi.window_rect(hwnd)
            winapi.scroll(any_rect[0] + 60, (top + bottom) // 2, -5)
            time.sleep(0.35)
            uia.wake(hwnd, settle=0.2)
            fresh = _visible_tracks(hwnd)
            if not fresh or max(fresh) == max(tracks):
                tracks = fresh or tracks
                break
            tracks = fresh
        position = max(tracks)

    if name.strip():
        want = name.strip().lower()
        hit = next((n for n, d in sorted(tracks.items())
                    if want in d.lower()), None)
        if hit is None:
            return (f"No song matching '{name}' is on screen. Showing tracks "
                    f"1 to {max(tracks)}.")
        number = hit
    else:
        number = position or 1
        if number not in tracks and min(tracks) < number < max(tracks):
            # A row only loses its number when it is the one playing: Spotify
            # swaps the digit for an equalizer icon. So a gap inside the
            # visible range is not a missing track, it is the current one.
            now = winapi._title(hwnd)
            if now and "spotify" not in now.lower():
                return f"Track {number} is already playing: {now}."
            return f"Track {number} is already playing."
        if number not in tracks:
            return (f"There is no track {number} on screen - the list shows "
                    f"{min(tracks)} to {max(tracks)}. Scroll to it and ask again.")

    desc, _rect = tracks[number]
    plays = [e for e in uia.elements(hwnd, uia.BUTTON)
             if e.on_screen and e.name.startswith("Play ")]

    # Match on the name, not the position. Picking whichever play button sat
    # in the row's band played the neighbouring track: Spotify swaps the
    # playing row's number for an equalizer icon, so that row drops out of
    # the numbering and every band below it lines up one out.
    def same_song(button_name: str) -> bool:
        tail = button_name[len("Play "):].strip()
        if not tail:
            return False
        # Row text repeats itself - "Swing Lynn - Slowed by ONIBI Swing Lynn
        # - Slowed ONIBI" - so a prefix match beats an equality test.
        return desc.startswith(tail) or tail.startswith(desc[:30])

    button = next((e for e in plays if e.name.strip() == f"Play {desc}"), None)
    if button is None:
        button = next((e for e in plays if same_song(e.name)), None)
    if button is None:
        return f"Found track {number} ({desc}) but no play button for it."

    before = winapi._title(hwnd)
    if not button.press(hwnd):
        return f"Could not press play for track {number}."

    # Spotify puts the playing track in its window title, so there is no need
    # to take the button press on trust.
    # Spotify puts the playing track in its window title. Requiring the title
    # to *change* was wrong: asking for a song that is already playing is a
    # perfectly good request, and it reported failure every time. What counts
    # is that the title now names this track.
    # Words this playlist repeats in every title would match anything, and
    # did: "play the first song" was confirmed as playing a different track
    # because both are "- Slowed".
    _GENERIC = {"slowed", "ultra", "reverb", "remix", "version", "feat",
                "with", "extended", "sped", "edit", "mix", "official"}
    title_part = re.split(r"\s+by\s+", desc, maxsplit=1)[0]
    words = {w for w in re.split(r"[^\w']+", title_part.lower())
             if len(w) > 3 and w not in _GENERIC}
    for _ in range(6):
        time.sleep(0.7)
        now = winapi._title(hwnd)
        if not now or "spotify" in now.lower():
            continue
        shown = {w for w in re.split(r"[^\w']+", now.lower()) if len(w) > 3}
        if words & shown:
            return f"Playing {now}."
        if now != before:
            return f"Playing {now}."
    return f"Pressed play on track {number}: {desc}."


@tool(tags=TAG + ["route_only"])
def play_song(query: str) -> str:
    """Search Spotify for a song and play it.

    Use this whenever a song is named - "play bohemian rhapsody", "play
    archangel by DJ Anemia". The artist is optional and helps when several
    songs share a title.

    query: the song, optionally followed by "by" and the artist
    """
    if not query.strip():
        return "ERROR: say which song to play."

    hwnd = _spotify_hwnd()
    if hwnd is None:
        hit = _match_app("spotify")
        if hit is None:
            return "Spotify is not installed."
        try:
            subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{hit[1]}"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            return f"Could not start Spotify: {e}"
        for _ in range(20):
            time.sleep(1.0)
            hwnd = _spotify_hwnd()
            if hwnd:
                break
        if hwnd is None:
            return "Could not open Spotify."

    # The search box is not exposed to automation at all - no edit control
    # appears, and the keyboard shortcuts produce nothing - but Spotify takes
    # a search as a URI, which lands on the same results page.
    import urllib.parse

    def results() -> list:
        """Play buttons in the results pane, top one first.

        The sidebar has play buttons too, at x=104 against the results'
        x=544, and sorting everything by height alone interleaves them - the
        first button on screen is a playlist, not the song asked for.
        """
        found = [e for e in uia.elements(hwnd, uia.BUTTON)
                 if e.on_screen and e.name.startswith("Play ") and e.rect[0] > 400]
        return sorted(found, key=lambda e: e.rect[1])

    # Asked for what is already on: searching again leaves the results page
    # unchanged, and the wait below then fell back to whatever sat on top -
    # once a podcast episode.
    words = [w for w in re.findall(r"[a-z0-9']+", query.lower()) if w != "by"]
    title_now = (winapi._title(hwnd) or "").lower()
    if words and "spotify" not in title_now and all(w in title_now for w in words):
        return f"Already playing {winapi._title(hwnd)}."

    if not winapi.focus_hwnd(hwnd):
        time.sleep(0.5)
        winapi.focus_hwnd(hwnd)
    uia.wake(hwnd, settle=0.2)
    # What is on screen before the search, taken before it is sent. The search
    # used to be sent twice - once before this snapshot - so the snapshot could
    # already hold the new results and the wait below ran out its full time.
    stale = {e.name for e in results()}

    try:
        subprocess.Popen(["explorer.exe",
                          "spotify:search:" + urllib.parse.quote(query.strip())],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        return f"ERROR: could not search Spotify: {e}"

    # Wait for the page to be about this search and not the previous one.
    # Matching the query against whatever was on screen picked "We Will Rock
    # You" for "shape of you" - the old results were still showing, and the
    # word "you" was enough. Spotify ranks its own results better than any
    # matching here, so wait for the page to change and take the top one.
    # Checked every quarter second rather than slept through in 1.2 s steps;
    # results that never change (the same search twice) are taken after 3 s.
    best = None
    deadline = time.time() + 3.0
    while time.time() < deadline:
        time.sleep(0.15)
        # Spotify only exposes a new page to automation after being asked
        # for its tree again; without this the results never appear.
        uia.wake(hwnd, settle=0.1)
        rows = results()
        if rows and {e.name for e in rows} != stale:
            best = rows[0]
            break

    if best is None:
        rows = results()
        if not rows:
            return f"Spotify found nothing playable for '{query}'."
        # The page never changed (the same search as before): the rows are
        # right but not re-ranked, so take the one naming most of the words.
        best = max(rows, key=lambda e: sum(w in e.name.lower() for w in words))

    before = winapi._title(hwnd)
    song = best.name[len("Play "):].strip()
    if not best.press(hwnd):
        return f"Found '{song}' but could not open it."

    # Clicking a result opens the song's page rather than playing it - the
    # button in the results list belongs to the card, not the track. The page
    # that opens has its own play button, and that one does start it. Only
    # press it if it still says Play: if the first click did begin playback,
    # this same control reads Pause and pressing it would stop the song.
    # Polled instead of a flat 2.5 s wait: stop as soon as the song is
    # playing (the title changes) or the page's Play button has appeared.
    def playing() -> str | None:
        now = winapi._title(hwnd)
        return now if now and "spotify" not in now.lower() and now != before else None

    pressed = False
    deadline = time.time() + 4.0
    while time.time() < deadline:
        time.sleep(0.2)
        if (now := playing()):
            return f"Playing {now}."
        page_play = next((e for e in uia.elements(hwnd, uia.BUTTON)
                          if e.on_screen and e.name.strip() == "Play"
                          and e.rect[0] > 400 and e.rect[1] < 500), None)
        if page_play is not None:
            page_play.press(hwnd)
            pressed = True
            break

    for _ in range(25 if pressed else 10):
        time.sleep(0.2)
        if (now := playing()):
            return f"Playing {now}."
    return f"Opened {song} but could not confirm it started playing."
