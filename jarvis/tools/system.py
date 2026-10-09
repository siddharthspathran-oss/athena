"""PC control: launch apps, windows, volume, screenshots, power, system info."""
from __future__ import annotations

import json
import re
import subprocess
import time
from datetime import datetime

import psutil

from .. import uia, winapi
from ..config import DATA, cfg
from ..registry import tool

TAG = ["system"]

# Spoken shorthand -> the name Windows actually lists the app under.
# Anything not listed here is fuzzy-matched against the Start menu directly.
APP_ALIASES = {
    "chrome": "google chrome",
    "browser": "comet",
    "edge": "microsoft edge",
    "files": "file explorer",
    "explorer": "file explorer",
    "calc": "calculator",
    "vscode": "visual studio code",
    "vs code": "visual studio code",
    "code": "visual studio code",
    "word": "microsoft word",
    "excel": "microsoft excel",
    "powerpoint": "microsoft powerpoint",
    "cmd": "command prompt",
    "task manager": "task manager",
    "opera": "opera gx browser",
    "mail": "outlook",
    "email": "outlook",
    "my email": "outlook",
    "inbox": "outlook",
    "whatsapp": "whatsapp",
    "settings": "settings",
    "notes": "onenote",
    # Known mishearings. Speech turns unusual product names into common words,
    # and no amount of priming fixes it completely.
    "comment": "comet",
    "comments": "comet",
    "commit": "comet",
    "comet browser": "comet",
    "comment browser": "comet",
    "music": "spotify",
    "open music": "spotify",
    # "Claude" has no common-English neighbour, so speech reaches for one:
    # heard as Lord, Plague, Cloud and Clod in a single session.
    "lord": "claude",
    "lord app": "claude",
    "cloud": "claude",
    "cloud app": "claude",
    "plague": "claude",
    "clod": "claude",
    # "close Fortnite" came back as "40 point" and "fortified" (3 Oct 2026).
    "40 point": "fortnite", "forty point": "fortnite", "fortified": "fortnite",
    "fort night": "fortnite", "fortnight": "fortnite", "for tonight": "fortnite",
    "4 night": "fortnite", "four night": "fortnite",
    "claud": "claude",
    "clawed": "claude",
    "clode": "claude",
    "glaude": "claude",
    "claude app": "claude",
    "claude desktop": "claude",
}


def _user_aliases() -> dict[str, str]:
    """Extra spoken-name mappings from config.yaml, so new mishearings can be
    fixed without touching code."""
    try:
        extra = cfg.get("apps.aliases", {}) or {}
        return {str(k).strip().lower(): str(v).strip() for k, v in extra.items()}
    except Exception:
        return {}


_START_APPS: list[tuple[str, str]] = []


def _start_apps(refresh: bool = False) -> list[tuple[str, str]]:
    """Every app on the Start menu as (name, AppID), via Get-StartApps.

    This is the only listing that covers Store apps, desktop programs and
    shortcuts alike - PATH lookups miss Store apps entirely, which is why
    'open whatsapp' used to silently do nothing.
    """
    global _START_APPS
    if _START_APPS and not refresh:
        return _START_APPS
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "Get-StartApps | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        data = json.loads(r.stdout or "[]")
        if isinstance(data, dict):
            data = [data]
        _START_APPS = [(d["Name"], d["AppID"]) for d in data if d.get("AppID")]
    except Exception:
        _START_APPS = []
    return _START_APPS


def _match_app(name: str) -> tuple[str, str] | None:
    """Find the best Start-menu match for a spoken or typed app name."""
    from difflib import SequenceMatcher

    apps = _start_apps()
    if not apps:
        return None
    key = name.strip().lower()
    want = (_user_aliases().get(key) or APP_ALIASES.get(key) or name).strip().lower()

    exact = [a for a in apps if a[0].lower() == want]
    if exact:
        return exact[0]
    starts = [a for a in apps if a[0].lower().startswith(want)]
    if starts:
        return min(starts, key=lambda a: len(a[0]))
    contains = [a for a in apps if want in a[0].lower()]
    if contains:
        return min(contains, key=lambda a: len(a[0]))

    # Words that carry no identifying information, so they must never be what
    # a match is based on - otherwise "open comment" finds "OpenGL Switcher".
    FILLER = {
        "app", "the", "my", "browser", "application", "program", "window",
        "open", "close", "launch", "start", "run", "quit", "exit", "please",
        "microsoft", "google", "windows",
    }

    def _significant(term: str) -> list[str]:
        return [w for w in term.split() if w not in FILLER and len(w) > 2]

    def _plausible(term: str, candidate: str) -> bool:
        """Guard a fuzzy hit: a distinctive word must actually correspond.

        Without this, 'microsoft edge' matches 'Microsoft Store' on the shared
        word alone and silently launches the wrong thing.
        """
        keys = _significant(term)
        if not keys:
            return True
        cand_words = candidate.lower().split()
        for k in keys:
            if k in candidate.lower():
                return True
            if any(SequenceMatcher(None, k, cw).ratio() >= 0.8 for cw in cand_words):
                return True
        return False

    def _closest(term: str, threshold: float) -> tuple[str, str] | None:
        best, score = None, 0.0
        for a in apps:
            s = SequenceMatcher(None, term, a[0].lower()).ratio()
            if s > score:
                best, score = a, s
        if best and score >= threshold and _plausible(term, best[0]):
            return best
        return None

    hit = _closest(want, 0.7)
    if hit:
        return hit

    # Speech turns product names into ordinary words ("Comet" -> "comment")
    # and tacks on filler ("comet browser"), so try the significant words too.
    for word in _significant(want):
        for a in apps:
            if a[0].lower() == word or a[0].lower().startswith(word):
                return a
        hit = _closest(word, 0.75)
        if hit:
            return hit
    return None


def _find_window_for(app_name: str) -> str | None:
    """Title of an open window that looks like it belongs to this app.

    Window titles carry extra text - "Email - Siddharth Pathran - Outlook" -
    so this matches on the app's distinctive words rather than the whole name.
    """
    keys = [w for w in app_name.lower().split()
            if len(w) > 2 and w not in ("the", "app", "microsoft", "google", "windows")]
    if not keys:
        return None
    want_browser = any(k in _BROWSERS for k in keys)
    for _hwnd, title in winapi.list_windows():
        low = title.lower()
        if not any(k in low for k in keys):
            continue
        # A page open in a browser is not the app. "WhatsApp - Opera" made
        # open_app report WhatsApp was already running and refuse to launch
        # it, which is why "open WhatsApp" quietly did nothing.
        if not want_browser and _browser_window(low):
            continue
        return title
    return None


_BROWSERS = ("opera", "opera gx", "chrome", "google chrome", "comet", "edge",
             "microsoft edge", "firefox", "mozilla firefox", "brave",
             "vivaldi", "chromium", "safari")


def _browser_window(title_low: str) -> bool:
    """True if this window belongs to a browser rather than an app."""
    return any(title_low.endswith(" - " + b) or (" - " + b + " ") in title_low
               or title_low.endswith(" - " + b + " browser")
               for b in _BROWSERS)


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


def playing_apps() -> list[str]:
    """The apps playing right now, as Windows names them ("Spotify.exe",
    "Chrome", "...MSEdge"), from the same media sessions."""
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _MEDIA_SESSION_PS],
                           capture_output=True, text=True, timeout=20, check=False)
    except Exception:
        return []
    out = []
    for item in (r.stdout or "").strip().split(","):
        state, _, app = item.partition("|")
        if state.strip().lower() == "playing":
            out.append(app.strip())
    return out


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


def _running() -> set[str]:
    names = set()
    for p in psutil.process_iter(["name"]):
        n = p.info.get("name")
        if n:
            names.add(n.lower())
    return names


@tool(tags=TAG)
def open_app(name: str) -> str:
    """Launch or switch to an application, for example chrome, whatsapp or outlook.

    Use this whenever the user says open, start, launch or show an app -
    including "open my email", which means launching the mail app, not
    writing a message.

    Pass the user's own word through unchanged, even if it looks like an
    ordinary English word or an app you do not recognise. This tool knows
    which apps are installed and corrects mishearings itself. Never swap in
    a different app you happen to know - "comment" is not Notepad.

    name: the application to open, in the user's own words
    """
    # "it" is too short for the fuzzy matcher to reject, so open_app("it")
    # launched Git CMD (24 Sep 2026). A pronoun names no app; it means the
    # file the user was just shown.
    if re.fullmatch(r"\s*(?:it|that|this|them|the\s+file|that\s+file)\s*", name or "", re.I):
        from . import files

        if files.last_file is not None:
            return files.open_file(str(files.last_file))
        return "ERROR: 'it' is not an app. Ask which file or app the user means."

    match = _match_app(name)
    if match is None:
        available = ", ".join(sorted(n for n, _ in _start_apps())[:12])
        return (f"ERROR: no installed app matches '{name}'. "
                f"Use list_installed_apps to see what is available. Some examples: {available}")

    app_name, app_id = match

    # Already open? Bring it forward. Launching again produces no new process,
    # which the check below would otherwise read as a failure.
    existing = _find_window_for(app_name)
    if existing:
        winapi.focus_window(existing)
        return f"{app_name} is already open - brought it to the front."

    before = _running()
    try:
        # shell:AppsFolder launches Store apps and desktop programs alike.
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app_id}"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        return f"ERROR: could not launch {app_name}: {e}"

    # Confirm something actually started rather than reporting blind success.
    for _ in range(12):
        time.sleep(0.5)
        if _running() - before:
            return f"Launched {app_name}."
    return (f"Asked Windows to launch {app_name}, but no new process appeared. "
            f"It may already be running, or it may have failed to start.")


@tool(tags=TAG)
def list_installed_apps(filter_text: str = "") -> str:
    """List applications installed on this PC that can be opened.

    filter_text: only show apps whose name contains this, or empty for all
    """
    apps = _start_apps()
    if not apps:
        return "ERROR: could not read the Start menu app list."
    names = sorted(n for n, _ in apps)
    if filter_text:
        names = [n for n in names if filter_text.lower() in n.lower()]
    if not names:
        return f"No installed app matching '{filter_text}'."
    shown = names[:60]
    extra = f"\n... and {len(names) - 60} more" if len(names) > 60 else ""
    return "\n".join(shown) + extra


def _squash(text: str) -> str:
    """Letters only, numbers spelled out: "40 point" -> "fortypoint"."""
    words = {"0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", "5": "five",
             "6": "six", "7": "seven", "8": "eight", "9": "nine", "10": "ten", "20": "twenty",
             "30": "thirty", "40": "forty", "50": "fifty"}
    text = re.sub(r"\d+", lambda m: words.get(m.group(), m.group()), text.lower())
    return re.sub(r"[^a-z]", "", text)


def _sounds_like_running(spoken: str) -> str | None:
    """The one open app whose name sounds like what was said, or None."""
    from difflib import SequenceMatcher
    said = _squash(spoken)
    if len(said) < 4:
        return None
    names = set()
    for _hwnd, title, pid in winapi.windows_with_pids():
        try:
            names.add(psutil.Process(pid).name().lower().removesuffix(".exe").split("-")[0])
        except Exception:
            pass
        if title:
            names.add(title.lower().split(" - ")[0][:40])
    scored = sorted(((SequenceMatcher(None, said, _squash(n)).ratio(), n) for n in names if len(_squash(n)) >= 4),
                    reverse=True)
    good = [(r, n) for r, n in scored if r >= 0.62 and _squash(n)[:1] == said[:1]]
    return good[0][1] if len(good) == 1 else None


@tool(tags=TAG, destructive=True)
def close_app(name: str) -> str:
    """Close a running application by name, for example chrome, whatsapp or notepad.

    Pass the user's own word through unchanged - this tool resolves app names
    and mishearings itself. Do not substitute a different app.

    name: the application to close, in the user's own words
    """
    return _close(name, guess_allowed=True)


def _close(name: str, guess_allowed: bool) -> str:
    spoken = name.strip().lower()
    key = (_user_aliases().get(spoken) or APP_ALIASES.get(spoken) or spoken).lower().removesuffix(".exe")
    # An alias names the app the way the Start menu does ("opera" -> "opera gx
    # browser"); the process is just "opera.exe". Closing searched for the
    # whole Start-menu name and reported Opera as not running (3 Oct 2026).
    _filler = {"browser", "app", "application", "the", "my", "window", "game"}
    keys = {k for k in {spoken, key, *(w for w in (spoken.split()[:1] + key.split()[:1])
                                      if len(w) >= 4 and w not in _filler)} if k}
    flat = {_squash(k) for k in keys}

    # Process names rarely match what people say: WhatsApp runs as
    # "WhatsApp.Root", Chrome spawns a dozen children. Match loosely, and
    # search window titles too so "close whatsapp" finds it either way.
    targets: dict[int, str] = {}
    for p in psutil.process_iter(["name", "pid"]):
        pname = (p.info.get("name") or "").lower().removesuffix(".exe")
        if not pname:
            continue
        if any(pname == k or pname.startswith(k) or k in pname for k in keys) or                 (len(pname) >= 4 and any(f.startswith(_squash(pname)) for f in flat)):    # "operagx" -> opera
            targets[p.info["pid"]] = p.info["name"]

    for hwnd, title, pid in winapi.windows_with_pids():
        if any(k in title.lower() for k in keys):
            targets.setdefault(pid, title)

    if not targets:
        # Speech bends game and app names into ordinary words. Before giving
        # up, compare by sound with what actually has a window open - and only
        # act on a single clear match, since closing the wrong thing is worse
        # than closing nothing. Once: a guess that still matches nothing used
        # to call back into itself forever (RecursionError, 3 Oct 2026).
        guess = _sounds_like_running(spoken) if guess_allowed else None
        if guess:
            return _close(guess, guess_allowed=False)
        return f"Nothing called {name} appears to be running."

    # Ask windows to close politely first so the app can save its state.
    closed_windows = 0
    for hwnd, _title, pid in winapi.windows_with_pids():
        if pid in targets:
            winapi.close_window(hwnd)
            closed_windows += 1

    if closed_windows:
        time.sleep(2.0)

    gone = [pid for pid in targets if not psutil.pid_exists(pid)]
    remaining = [pid for pid in targets if psutil.pid_exists(pid)]

    # Anything that ignored the polite request gets terminated.
    forced = 0
    for pid in remaining:
        try:
            psutil.Process(pid).terminate()
            forced += 1
        except Exception:
            pass
    if forced:
        time.sleep(1.0)

    still_alive = [pid for pid in targets if psutil.pid_exists(pid)]
    # Report the name that was asked for - window titles make for noisy labels.
    label = name.strip()
    if still_alive:
        return f"Asked {label} to close but {len(still_alive)} process(es) are still running."
    return f"Closed {label} ({len(gone) + forced} process(es))."


@tool(tags=TAG)
def list_running_apps(limit: int = 15) -> str:
    """List the applications currently using the most memory.

    limit: how many to return
    """
    procs = []
    for p in psutil.process_iter(["name", "memory_info"]):
        try:
            mem = p.info["memory_info"].rss / (1024 * 1024)
            if mem > 20:
                procs.append((p.info["name"], mem))
        except Exception:
            continue
    # collapse the many chrome.exe style children into one row
    merged: dict[str, float] = {}
    for name, mem in procs:
        merged[name] = merged.get(name, 0.0) + mem
    top = sorted(merged.items(), key=lambda x: -x[1])[:limit]
    return "\n".join(f"{n} - {m:.0f} MB" for n, m in top) or "Nothing notable running."


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
def set_volume(level: int) -> str:
    """Set the master system volume.

    level: volume percentage from 0 to 100
    """
    level = max(0, min(100, int(level)))
    try:
        _endpoint_volume().SetMasterVolumeLevelScalar(level / 100.0, None)
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

    action: play, pause, next, previous, mute, song (play a named song), track (play song number `position` in the list), surprise (pick something the user likes) or playlist (their saved playlist)
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


@tool(tags=TAG)
def take_screenshot(region: str = "full") -> str:
    """Capture the screen and save it as a PNG. Returns the saved file path.

    region: full for the whole screen, or x,y,width,height for a box
    """
    shots = DATA / "screenshots"
    shots.mkdir(exist_ok=True)
    path = shots / f"shot_{datetime.now():%Y%m%d_%H%M%S}.png"

    box = None
    if region and region != "full":
        try:
            x, y, w, h = (int(v) for v in region.split(","))
            box = (x, y, w, h)
        except ValueError:
            return "ERROR: region must be full or x,y,width,height."

    winapi.screenshot(path, box)
    return f"Saved screenshot to {path}"


@tool(tags=TAG)
def list_windows() -> str:
    """List the titles of all open windows."""
    titles = [t for _h, t in winapi.list_windows()]
    return "\n".join(titles) or "No titled windows open."


@tool(tags=TAG)
def focus_window(title: str) -> str:
    """Bring a window to the front by partial title match.

    title: part of the window title, for example chrome or notepad
    """
    matched = winapi.focus_window(title)
    if matched is None:
        return f"No open window matching {title}."
    return f"Focused {matched}."


@tool(tags=TAG)
def resize_window(title: str, state: str) -> str:
    """Minimise, maximise or restore a window by partial title match.

    title: part of the window title
    state: one of minimize, maximize, restore
    """
    matched = winapi.window_state(title, state.strip().lower())
    if matched is None:
        return f"No open window matching {title}, or unknown state {state}."
    return f"Set {matched} to {state}."


@tool(tags=TAG)
def battery() -> str:
    """Report the battery percentage and whether the laptop is charging.

    Use this for any question about battery, charge or power level.
    """
    batt = psutil.sensors_battery()
    if batt is None:
        return "This PC has no battery."
    state = "charging" if batt.power_plugged else "on battery"
    out = f"{batt.percent:.0f}%, {state}."
    if not batt.power_plugged and batt.secsleft and batt.secsleft > 0:
        hrs, mins = divmod(int(batt.secsleft) // 60, 60)
        out += f" About {hrs}h {mins}m left."
    return out


@tool(tags=TAG)
def system_info() -> str:
    """Report CPU, memory, disk and battery status for this PC."""
    vm = psutil.virtual_memory()
    du = psutil.disk_usage("C:\\")
    lines = [
        f"CPU:     {psutil.cpu_percent(interval=0.4):.0f}% across {psutil.cpu_count()} threads",
        f"Memory:  {vm.used / 1e9:.1f} / {vm.total / 1e9:.1f} GB ({vm.percent:.0f}%)",
        f"Disk C:  {du.used / 1e9:.0f} / {du.total / 1e9:.0f} GB used, {du.free / 1e9:.0f} GB free",
    ]
    batt = psutil.sensors_battery()
    if batt:
        state = "charging" if batt.power_plugged else "on battery"
        lines.append(f"Battery: {batt.percent:.0f}% ({state})")
    up = time.time() - psutil.boot_time()
    lines.append(f"Uptime:  {int(up // 3600)}h {int((up % 3600) // 60)}m")
    return "\n".join(lines)


@tool(tags=TAG + ["route_only"])
def pc_status(what: str) -> str:
    """One spoken line about the PC: disk, memory, cpu or uptime.

    Asked "how much free space is on my C drive", the local model answered
    "124.56 GB free" without calling anything - a made-up number, different
    each time (28 Sep 2026). These are read straight from the system instead.
    """
    if what == "disk":
        parts = []
        for p in psutil.disk_partitions(all=False):
            if "cdrom" in p.opts or not p.fstype:
                continue
            try:
                du = psutil.disk_usage(p.mountpoint)
            except OSError:
                continue
            parts.append(f"{p.mountpoint.rstrip(chr(92))} has {du.free / 1e9:.0f} GB free "
                         f"of {du.total / 1e9:.0f}")
        return ("; ".join(parts) + ".") if parts else system_info()
    if what == "memory":
        vm = psutil.virtual_memory()
        return (f"{vm.used / 1e9:.1f} of {vm.total / 1e9:.0f} GB of RAM in use "
                f"({vm.percent:.0f}%), {vm.available / 1e9:.1f} GB free.")
    if what == "cpu":
        return f"CPU at {psutil.cpu_percent(interval=0.4):.0f}% right now."
    if what == "uptime":
        up = time.time() - psutil.boot_time()
        return f"On for {int(up // 3600)} hours {int((up % 3600) // 60)} minutes."
    if what == "wifi":
        # "which wifi am I connected to" went to get_weather.
        r = subprocess.run(["netsh", "wlan", "show", "interfaces"], capture_output=True,
                           text=True, timeout=8, check=False)
        ssid = re.search(r"^\s*SSID\s*:\s*(.+)$", r.stdout or "", re.M)
        state = re.search(r"^\s*State\s*:\s*(.+)$", r.stdout or "", re.M)
        if ssid and state and "connected" == state.group(1).strip().lower():
            sig = re.search(r"^\s*Signal\s*:\s*(\d+)%", r.stdout or "", re.M)
            return f"Connected to {ssid.group(1).strip()}" + (f", signal {sig.group(1)}%." if sig else ".")
        return "Not connected to any Wi-Fi network."
    if what == "ip":
        import socket
        local = [a.address for name, addrs in psutil.net_if_addrs().items()
                 if psutil.net_if_stats().get(name) and psutil.net_if_stats()[name].isup
                 for a in addrs if a.family == socket.AF_INET
                 and not a.address.startswith(("127.", "169.254."))]
        out = f"Local IP {', '.join(local[:3])}." if local else "No local IP address."
        try:
            import urllib.request
            with urllib.request.urlopen("https://api.ipify.org", timeout=3) as r:
                out += f" Public IP {r.read().decode().strip()}."
        except Exception:
            pass
        return out
    return system_info()


# Places people ask the time in, by how they say them. Anything else is
# looked up among the IANA zone names ("what time is it in Lisbon").
_ZONES = {
    "london": "Europe/London", "uk": "Europe/London", "england": "Europe/London",
    "new york": "America/New_York", "nyc": "America/New_York", "usa": "America/New_York",
    "america": "America/New_York", "us": "America/New_York", "washington": "America/New_York",
    "los angeles": "America/Los_Angeles", "la": "America/Los_Angeles", "california": "America/Los_Angeles",
    "san francisco": "America/Los_Angeles", "seattle": "America/Los_Angeles", "chicago": "America/Chicago",
    "texas": "America/Chicago", "toronto": "America/Toronto", "canada": "America/Toronto",
    "vancouver": "America/Vancouver", "mexico": "America/Mexico_City", "brazil": "America/Sao_Paulo",
    "paris": "Europe/Paris", "france": "Europe/Paris", "berlin": "Europe/Berlin", "germany": "Europe/Berlin",
    "rome": "Europe/Rome", "italy": "Europe/Rome", "madrid": "Europe/Madrid", "spain": "Europe/Madrid",
    "amsterdam": "Europe/Amsterdam", "moscow": "Europe/Moscow", "russia": "Europe/Moscow",
    "istanbul": "Europe/Istanbul", "turkey": "Europe/Istanbul", "dubai": "Asia/Dubai", "uae": "Asia/Dubai",
    "india": "Asia/Kolkata", "delhi": "Asia/Kolkata", "new delhi": "Asia/Kolkata", "mumbai": "Asia/Kolkata",
    "bangalore": "Asia/Kolkata", "pakistan": "Asia/Karachi", "nepal": "Asia/Kathmandu",
    "bangladesh": "Asia/Dhaka", "sri lanka": "Asia/Colombo", "bangkok": "Asia/Bangkok",
    "thailand": "Asia/Bangkok", "singapore": "Asia/Singapore", "hong kong": "Asia/Hong_Kong",
    "china": "Asia/Shanghai", "beijing": "Asia/Shanghai", "shanghai": "Asia/Shanghai",
    "tokyo": "Asia/Tokyo", "japan": "Asia/Tokyo", "seoul": "Asia/Seoul", "korea": "Asia/Seoul",
    "sydney": "Australia/Sydney", "australia": "Australia/Sydney", "melbourne": "Australia/Melbourne",
    "new zealand": "Pacific/Auckland", "cairo": "Africa/Cairo", "egypt": "Africa/Cairo",
    "nairobi": "Africa/Nairobi", "johannesburg": "Africa/Johannesburg", "south africa": "Africa/Johannesburg",
}


@tool(tags=TAG + ["route_only"])
def world_time(place: str) -> str:
    """The time somewhere else. Asked "what time is it in London", the model
    read the local clock and said 10:19 PM for London (28 Sep 2026)."""
    from datetime import datetime
    from zoneinfo import ZoneInfo, available_timezones
    key = re.sub(r"[^a-z ]", "", place.lower()).strip()
    zone = _ZONES.get(key)
    if zone is None:
        zone = next((z for z in sorted(available_timezones())
                     if z.split("/")[-1].replace("_", " ").lower() == key), None)
    if zone is None:
        return f"I don't know the time zone for {place}."
    there, here = datetime.now(ZoneInfo(zone)), datetime.now().astimezone()
    diff = (there.utcoffset() - here.utcoffset()).total_seconds() / 3600
    rel = ("the same as here" if diff == 0 else
           f"{abs(diff):g} hours {'ahead of' if diff > 0 else 'behind'} you")
    return f"It's {there.strftime('%I:%M %p').lstrip('0')} on {there.strftime('%A')} in {place.title()}, {rel}."


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


@tool(tags=TAG)
def lock_screen() -> str:
    """Lock the Windows session."""
    subprocess.run(["rundll32.exe", "user32.dll,LockWorkStation"], check=False)
    return "Locked."


@tool(tags=TAG, destructive=True)
def power_action(action: str, delay_seconds: int = 30) -> str:
    """Shut down, restart, or sleep the PC, or cancel a pending shutdown.

    action: one of shutdown, restart, sleep, cancel
    delay_seconds: grace period before shutdown or restart
    """
    a = action.strip().lower()
    if a == "cancel":
        subprocess.run(["shutdown", "/a"], check=False)
        return "Cancelled any pending shutdown."
    if a == "sleep":
        subprocess.run(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], check=False)
        return "Sleeping."
    flag = {"shutdown": "/s", "restart": "/r"}.get(a)
    if not flag:
        return f"ERROR: unknown power action {action}."
    subprocess.run(["shutdown", flag, "/t", str(int(delay_seconds))], check=False)
    return f"{a.capitalize()} scheduled in {delay_seconds}s. Ask me to cancel shutdown to stop it."


@tool(tags=TAG)
def type_text(text: str) -> str:
    """Type text into whatever window currently has focus.

    text: the text to type
    """
    time.sleep(0.3)  # give the target window a moment to take focus
    winapi.type_text(text)
    return f"Typed {len(text)} characters."


@tool(tags=TAG)
def clipboard(action: str, text: str = "") -> str:
    """Read from or write to the Windows clipboard.

    action: get to read the clipboard, set to write to it
    text: the text to copy, when action is set
    """
    if action.strip().lower() == "set":
        subprocess.run("clip", input=text.encode("utf-16-le"), shell=True, check=False)
        return "Copied to clipboard."
    out = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
                         capture_output=True, text=True, check=False)
    return out.stdout.strip() or "(clipboard is empty)"


# ---------- playing a particular song ----------
#
# The media keys can only say "next". Asked to play the first song they play
# the second, because next is the only idea they have. Spotify lists every
# visible track as a row named "3 Play <title> by <artists>", with a matching
# play button, so the position the user actually said can be honoured.

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


@tool(tags=TAG)
def system_report() -> str:
    """What the PC is doing right now: load, memory, disk, battery, GPU and the
    busiest apps. Use for "what is my pc doing", "system report", "why is it slow"."""
    lines = [system_info()]
    try:
        import subprocess
        r = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
                            "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        u, used, total, temp = [x.strip() for x in r.stdout.strip().split(",")]
        lines.append(f"GPU:     {u}% busy, {int(used) / 1024:.1f} / {int(total) / 1024:.1f} GB VRAM, {temp}°C")
    except Exception:
        pass
    apps = list_running_apps(6).splitlines()
    if apps:
        lines.append("Busiest apps:\n" + "\n".join("  " + a for a in apps))
    return "\n".join(lines)


@tool(tags=TAG + ["route_only"], timeout=120)
def free_gpu() -> str:
    """Gaming mode: take every AI model off the graphics card to free VRAM.

    Asking Ollama politely (keep_alive=0) was not enough: on 27 Sep 2026 the
    vision model sat in "Stopping..." holding 5.3 GB, and each unload request
    for it made Ollama load it again. So this ends Ollama's model runners
    (llama-server.exe) outright - the same thing llm.heal_gpu does. Ollama
    itself keeps running and starts a fresh runner on the next question.
    Voice keeps working: Whisper lives in the voice process, not in Ollama.
    """
    import ollama

    # A picture question starts a 45 s timer that would load the chat model
    # straight back; cancel it (it lives in this process).
    from .. import gaming
    gaming.set_on(True)      # background jobs in every Athena process stand down

    try:
        from .. import vision
        with vision._lock:
            if vision._timer is not None:
                vision._timer.cancel()
                vision._timer = None
            vision._gen += 1
    except Exception:
        pass

    try:
        loaded = ollama.Client(host=cfg.get("llm.host", "http://127.0.0.1:11434")).ps().models
    except Exception:
        loaded = []
    names = [m.model.split(":")[0] for m in loaded]
    held = sum((getattr(m, "size_vram", 0) or 0) for m in loaded)

    ended = 0
    for p in psutil.process_iter(["name"]):
        if (p.info["name"] or "").lower() == "llama-server.exe":
            try:
                p.kill()
                ended += 1
            except psutil.Error:
                pass
    if not ended:
        return "Gaming mode on. Nothing was loaded on the GPU, so it is already free."
    return (f"Gaming mode on: {', '.join(names) or 'the AI models'} off the GPU, about {held / 1e9:.1f} GB freed. "
            "Say 'gaming mode off' to load me back, or just ask me something.")


@tool(tags=TAG + ["route_only"], timeout=120)
def load_gpu() -> str:
    """Gaming mode off: load the chat model back onto the GPU so replies are quick again."""
    from .. import gaming
    from ..llm import LLM

    gaming.set_on(False)

    llm = LLM()
    try:
        # Loading it is the whole point - with the voice prompt, so the first
        # command afterwards finds it already cached.
        from ..agent import warm
        warm(voice=True, load=True)
        if llm.processor() == "not loaded":
            llm.chat([{"role": "user", "content": "hi"}])
    except Exception as e:
        return f"ERROR: could not load the model ({str(e)[:80]})."
    where = llm.processor()
    return f"Gaming mode off. {llm.model} is loaded ({where}) and ready." + (
        "" if where == "GPU" else " It is not fully on the GPU, so replies may be slow.")
