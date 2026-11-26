"""Apps: what's installed (the Start menu, plus your own names for apps), matching what
was said to one of them, opening, closing and listing them.

Split out of system.py (31 Oct 2026); system.py imports all of it back, so
`from jarvis.tools.system import _start_apps` still works.
"""
from __future__ import annotations

import json
import re
import subprocess
import time

import psutil

from .. import winapi
from ..config import cfg
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
