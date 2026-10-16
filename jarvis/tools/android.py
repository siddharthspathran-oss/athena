"""The Android phone, controlled over Wi-Fi with adb (Wireless debugging).

Set up once (4 Oct 2026, Galaxy S24): Developer options > Wireless debugging
on (Samsung's Auto Blocker has to be off for it), paired with this PC by the
user typing the code themselves (data/scrcpy/pair.ps1). adb and scrcpy live in
data/scrcpy. The phone is found by its network name, so the port it picks
each time Wireless debugging is switched on does not matter.

Athena never types a PIN, password or OTP on the phone, and never confirms a
payment - the same rules as on the PC.
"""
from __future__ import annotations

import datetime as dt
import re
import subprocess
import time
from difflib import SequenceMatcher

from ..config import DATA, cfg
from ..registry import tool

TAG = ["phone_control"]
TOOLS = DATA / "scrcpy"
ADB = TOOLS / "adb.exe"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Spoken name -> package, for the apps on this phone whose package name does
# not say what they are.
APPS = {"whatsapp": "com.whatsapp", "instagram": "com.instagram.android", "insta": "com.instagram.android",
        "youtube": "com.google.android.youtube", "spotify": "com.spotify.music",
        "camera": "com.sec.android.app.camera", "chrome": "com.android.chrome",
        "gallery": "com.sec.android.gallery3d", "photos": "com.sec.android.gallery3d",
        "phone": "com.samsung.android.dialer", "dialer": "com.samsung.android.dialer",
        "messages": "com.samsung.android.messaging", "sms": "com.samsung.android.messaging",
        "maps": "com.google.android.apps.maps", "google maps": "com.google.android.apps.maps",
        "gmail": "com.google.android.gm", "mail": "com.google.android.gm", "teams": "com.microsoft.teams",
        "settings": "com.android.settings", "snapchat": "com.snapchat.android",
        "play store": "com.android.vending", "calculator": "com.sec.android.app.popupcalculator",
        "clock": "com.sec.android.app.clockpackage", "alarm": "com.sec.android.app.clockpackage",
        "calendar": "com.samsung.android.calendar", "notes": "com.samsung.android.app.notes",
        "files": "com.sec.android.app.myfiles", "my files": "com.sec.android.app.myfiles"}
SONG = "/sdcard/Music/Athena/aria_math.mp3"     # pushed from data/sounds (the user's own copy)


def _run(*args: str, timeout: float = 15) -> subprocess.CompletedProcess:
    return subprocess.run([str(ADB), *args], capture_output=True, text=True, timeout=timeout,
                          creationflags=_NO_WINDOW)


_fixed_at = {"t": 0.0}
_away = {"until": 0.0}      # Tailscale connect timed out: skip it until then


def _serial() -> str | None:
    """The phone's adb name, connecting first if adb has not found it yet.

    Two ways in: on the same Wi-Fi, by the phone's network name (its port
    changes every time Wireless debugging is switched on); anywhere else the
    phone has Wi-Fi, through Tailscale to a fixed port (phone.tailscale_ip,
    port 5555). The fixed port is lost when the phone restarts, so it is set
    again the next time the phone is found on the home Wi-Fi.
    """
    remote = f"{cfg.get('phone.tailscale_ip', '')}:{int(cfg.get('phone.adb_port', 5555))}"

    def listed() -> list[str]:
        out = _run("devices").stdout.splitlines()[1:]
        return [ln.split()[0] for ln in out if ln.strip().endswith("device")]
    found = listed()
    if not found:
        for line in _run("mdns", "services").stdout.splitlines():
            if "_adb-tls-connect" in line:
                _run("connect", line.split()[-1])
        time.sleep(1)
        found = listed()
    local = next((s for s in found if "_adb-tls-connect" in s), None)
    if remote.startswith(":"):
        return local or (found[0] if found else None)
    if remote not in found and time.time() > _away["until"]:
        # Unreachable over Tailscale (phone asleep or offline): an 8 s timeout
        # that crashed the tool, after which the model said "already open"
        # (14 Oct 2026). Say we can't reach it, and don't wait again for a minute.
        try:
            _run("connect", remote, timeout=8)
        except subprocess.TimeoutExpired:
            _away["until"] = time.time() + 60
        found = listed()
    if remote not in found and local and time.time() - _fixed_at["t"] > 600:
        # Home, but the fixed port is gone (the phone restarted): set it again.
        _fixed_at["t"] = time.time()
        _run("-s", local, "tcpip", str(int(cfg.get("phone.adb_port", 5555))))
        time.sleep(4)
        try:
            _run("connect", remote, timeout=8)
        except subprocess.TimeoutExpired:
            _away["until"] = time.time() + 60
        found = listed()
    # Same Wi-Fi first (quicker), else Tailscale.
    return local if local in found else remote if remote in found else (found[0] if found else None)


class NoPhone(RuntimeError):
    pass


def adb(*args: str, timeout: float = 15) -> str:
    s = _serial()
    if s is None:
        raise NoPhone("I can't reach your phone - is Wireless debugging on, and is it on the same Wi-Fi?")
    return _run("-s", s, *args, timeout=timeout).stdout


def _phone(fn):
    """A tool's NoPhone becomes its answer."""
    def wrapped(*a, **k):
        try:
            return fn(*a, **k)
        except NoPhone as e:
            return str(e)
    wrapped.__name__, wrapped.__doc__ = fn.__name__, fn.__doc__
    import inspect
    wrapped.__signature__ = inspect.signature(fn)
    return wrapped


def _awake() -> bool:
    return "mWakefulness=Awake" in adb("shell", "dumpsys", "power")


def _locked() -> bool:
    out = adb("shell", "dumpsys", "window")
    return bool(re.search(r"mDreamingLockscreen=true|isKeyguardShowing=true|mShowingLockscreen=true", out))


def _package(name: str) -> str | None:
    want = name.lower().strip()
    want = re.sub(r"^(?:the|my)\s+|\s+app$", "", want)
    if want in APPS:
        return APPS[want]
    out = adb("shell", "cmd", "package", "query-activities", "-a", "android.intent.action.MAIN",
              "-c", "android.intent.category.LAUNCHER")
    packages = sorted(set(re.findall(r"packageName=([\w.]+)", out)))
    squashed = want.replace(" ", "")
    for p in packages:                                   # "netflix" -> com.netflix.mediaclient
        if squashed in p.lower().split(".") or any(squashed == part for part in p.lower().split(".")):
            return p
    best = max(packages, key=lambda p: max(SequenceMatcher(None, squashed, part).ratio()
                                           for part in p.lower().split(".")), default=None)
    if best and max(SequenceMatcher(None, squashed, part).ratio() for part in best.lower().split(".")) >= 0.75:
        return best
    return None


@tool(tags=TAG, timeout=30)
@_phone
def show_phone() -> str:
    """Show the phone's screen on the PC, in a window you can use with mouse and keyboard."""
    s = _serial()
    if s is None:
        raise NoPhone("I can't reach your phone - is Wireless debugging on, and is it on the same Wi-Fi?")
    subprocess.Popen([str(TOOLS / "scrcpy.exe"), "-s", s, "--window-title", "Phone", "--stay-awake",
                      "--no-audio"], cwd=str(TOOLS), creationflags=_NO_WINDOW)
    return "Your phone's screen is open on the PC."


@tool(tags=TAG, timeout=30)
@_phone
def phone_open_app(name: str) -> str:
    """Open an app on the phone.

    name: the app, e.g. WhatsApp, Instagram, camera
    """
    pkg = _package(name)
    if pkg is None:
        return f"I couldn't find {name} on your phone."
    if not _awake():
        adb("shell", "input", "keyevent", "KEYCODE_WAKEUP")
    adb("shell", "monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1")
    if _locked():
        return f"Opened {name} - unlock your phone to see it."
    return f"Opened {name} on your phone."


@tool(tags=TAG, timeout=30)
@_phone
def find_my_phone() -> str:
    """Ring the phone loudly, even on silent, so it can be found."""
    adb("shell", "input", "keyevent", "KEYCODE_WAKEUP")
    adb("shell", "cmd", "media_session", "volume", "--stream", "3", "--set", "15")   # media, not the ringer: silent mode can't stop it
    # Only your copy of Aria Math, kept on the phone (asked for, 4 Oct 2026 -
    # no Spotify, no ringtone). Put back on the phone if it has gone.
    local = DATA / "sounds" / "aria_math.mp3"
    if adb("shell", "ls", SONG).strip() != SONG:
        if not local.exists():
            return "The Aria Math file is missing from the PC (data/sounds/aria_math.mp3)."
        adb("shell", "mkdir", "-p", SONG.rsplit("/", 1)[0])
        adb("push", str(local), SONG, timeout=90)
    adb("shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", f"file://{SONG}", "-t", "audio/mpeg")
    for _ in range(6):
        time.sleep(1)
        if "state=PLAYING" in adb("shell", "dumpsys", "media_session"):
            return "Your phone is playing Aria Math."
    return "I started Aria Math on your phone, but it doesn't seem to be playing - it may need unlocking first."


@tool(tags=TAG, timeout=20)
@_phone
def phone_status() -> str:
    """The phone's battery, and whether it is charging."""
    out = adb("shell", "dumpsys", "battery")
    level = re.search(r"^\s*level:\s*(\d+)", out, re.M)
    charging = re.search(r"^\s*(?:AC|USB|Wireless) powered:\s*true", out, re.M)
    if not level:
        return "I couldn't read your phone's battery."
    return f"Your phone is on {level.group(1)} percent" + (", charging." if charging else ".")


@tool(tags=TAG, timeout=20)
@_phone
def lock_phone() -> str:
    """Lock the phone (turn its screen off)."""
    adb("shell", "input", "keyevent", "KEYCODE_SLEEP")
    return "Your phone is locked."


@tool(tags=TAG, timeout=30)
@_phone
def phone_screenshot() -> str:
    """Take a screenshot of the phone and open it on the PC."""
    import os
    s = _serial()
    if s is None:
        raise NoPhone("I can't reach your phone - is Wireless debugging on, and is it on the same Wi-Fi?")
    folder = _pictures() / "Phone screenshots"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"phone {dt.datetime.now():%Y-%m-%d %H-%M-%S}.png"
    png = subprocess.run([str(ADB), "-s", s, "exec-out", "screencap", "-p"], capture_output=True, timeout=20,
                         creationflags=_NO_WINDOW).stdout
    if not png.startswith(b"\x89PNG"):
        return "I couldn't take a screenshot of your phone."
    path.write_bytes(png)
    os.startfile(path)
    return f"Took a screenshot of your phone - it's in Pictures, Phone screenshots."


def _pictures():
    from pathlib import Path
    return Path.home() / "Pictures"


_KEYS = {"pause": "KEYCODE_MEDIA_PAUSE", "play": "KEYCODE_MEDIA_PLAY", "toggle": "KEYCODE_MEDIA_PLAY_PAUSE",
         "next": "KEYCODE_MEDIA_NEXT", "previous": "KEYCODE_MEDIA_PREVIOUS",
         "volume up": "KEYCODE_VOLUME_UP", "volume down": "KEYCODE_VOLUME_DOWN", "mute": "KEYCODE_VOLUME_MUTE"}


@tool(tags=TAG, timeout=20)
@_phone
def phone_media(action: str) -> str:
    """Control music or video playing on the phone.

    action: pause, play, next, previous, volume up, volume down or mute
    """
    key = _KEYS.get(action.strip().lower())
    if key is None:
        return f"I can't do '{action}' on the phone's music."
    times = 3 if action.startswith("volume") else 1
    for _ in range(times):
        adb("shell", "input", "keyevent", key)
    return {"pause": "Paused your phone.", "play": "Playing on your phone.", "next": "Next track on your phone.",
            "previous": "Previous track on your phone.", "mute": "Muted your phone."}.get(
        action.strip().lower(), f"Turned your phone's volume {action.split()[-1]}.")


# ---------- every connected device at once ("on all devices", 6 Oct 2026) ----------

def _all_serials() -> list[str]:
    """One adb name per device (the same phone can be listed twice: by Wi-Fi name and by address)."""
    _serial()                                            # connects whatever can be found
    out, models = [], set()
    for line in _run("devices", "-l").stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            model = next((p.split(":", 1)[1] for p in parts if p.startswith("model:")), parts[0])
            if model not in models:
                models.add(model)
                out.append(parts[0])
    return out


def _on(serials: list[str], *args: str) -> None:
    for s in serials:
        _run("-s", s, *args)


@tool(tags=TAG, timeout=20)
def phone_devices() -> str:
    """How many phones and tablets are connected, and which."""
    serials = _all_serials()
    if not serials:
        return "No phones are connected - switch on Wireless debugging on the phone."
    names = [(_run("-s", s, "shell", "getprop", "ro.product.model").stdout.strip() or s) for s in serials]
    return f"{len(names)} device{'s' if len(names) > 1 else ''} connected: {', '.join(names)}, plus this PC."


@tool(tags=TAG, timeout=40)
def phone_youtube(query: str, everywhere: bool = False) -> str:
    """Search YouTube on the phone (or on every connected device).

    query: what to search for
    everywhere: true for every connected phone and tablet
    """
    serials = _all_serials() if everywhere else [s for s in [_serial()] if s]
    if not serials:
        return "I can't reach your phone - is Wireless debugging on?"
    _on(serials, "shell", "input", "keyevent", "KEYCODE_WAKEUP")
    _on(serials, "shell", "am", "start", "-a", "android.intent.action.SEARCH", "-p", "com.google.android.youtube",
        "-e", "query", f"'{query}'")
    where = f"on all {len(serials)} devices" if len(serials) > 1 else "on your phone"
    return f"Searching YouTube for {query} {where}."


@tool(tags=TAG, timeout=40)
def phone_open_everywhere(name: str) -> str:
    """Open an app on every connected phone and tablet at once.

    name: the app, e.g. YouTube, Spotify
    """
    serials = _all_serials()
    if not serials:
        return "No phones are connected."
    pkg = _package(name)
    if pkg is None:
        return f"I couldn't find {name}."
    _on(serials, "shell", "input", "keyevent", "KEYCODE_WAKEUP")
    _on(serials, "shell", "monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1")
    return f"Opened {name} on {len(serials)} device{'s' if len(serials) > 1 else ''}."
