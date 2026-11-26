"""PC control: launch apps, windows, volume, screenshots, power, system info."""
from __future__ import annotations

import re
import subprocess
import time
from datetime import datetime

import psutil

from .. import winapi
from ..config import DATA, cfg
from ..registry import tool
from .system_apps import (  # noqa: F401 - split out 31 Oct 2026; still importable from here
    TAG,
    APP_ALIASES,
    _START_APPS,
    _user_aliases,
    _start_apps,
    _match_app,
    _find_window_for,
    _BROWSERS,
    _browser_window,
    _running,
    open_app,
    list_installed_apps,
    _squash,
    _sounds_like_running,
    close_app,
    _close,
    list_running_apps,
)
from .system_media import (  # noqa: F401 - split out 31 Oct 2026; still importable from here
    _MEDIA_PLAYERS,
    _MEDIA_SESSION_PS,
    _media_status,
    _media_player_running,
    _endpoint_volume,
    set_volume,
    get_volume,
    music,
    now_playing,
    _TRACK_ROW,
    _spotify_hwnd,
    _visible_tracks,
    play_track,
    play_song,
)


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
