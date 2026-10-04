"""Windows radios and Settings pages: Bluetooth, Wi-Fi, airplane mode.

Toggling a radio goes through the WinRT Radio API, driven from PowerShell.
That is the only route that works without administrator rights - disabling
the adapter via PnP needs elevation and leaves the device in a worse state.
"""
from __future__ import annotations

import json
import subprocess

from ..registry import tool

TAG = ["windows"]

# Boilerplate that makes WinRT's async API usable from Windows PowerShell 5.1.
_WINRT_PRELUDE = r"""
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$as = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($t, $rt) {
    $m = $as.MakeGenericMethod($rt)
    $task = $m.Invoke($null, @($t))
    $null = $task.Wait(8000)
    $task.Result
}
[Windows.Devices.Radios.Radio,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
[Windows.Devices.Radios.RadioAccessStatus,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
$access = Await ([Windows.Devices.Radios.Radio]::RequestAccessAsync()) ([Windows.Devices.Radios.RadioAccessStatus])
if ("$access" -ne "Allowed") { Write-Output "DENIED:$access"; exit 1 }
$radios = Await ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) ([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]])
"""


def _run_ps(script: str, timeout: int = 45) -> tuple[bool, str]:
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        return False, "the radio API did not respond in time"
    out = (r.stdout or "").strip()
    if out.startswith("DENIED:"):
        return False, (f"Windows denied radio access ({out.split(':', 1)[1]}). "
                       "Check Settings > Privacy > Radios.")
    if r.returncode != 0 and not out:
        return False, (r.stderr or "unknown error").strip()[:200]
    return True, out


def _radio_state(kind: str) -> tuple[bool, str]:
    ok, out = _run_ps(_WINRT_PRELUDE + f"""
$r = $radios | Where-Object {{ "$($_.Kind)" -eq "{kind}" }} | Select-Object -First 1
if (-not $r) {{ Write-Output "MISSING" }} else {{ Write-Output "$($r.State)" }}
""")
    return ok, out


def _set_radio(kind: str, state: str) -> str:
    """state is 'On' or 'Off'."""
    ok, current = _radio_state(kind)
    if not ok:
        return f"ERROR: {current}"
    if current == "MISSING":
        return f"ERROR: this PC has no {kind} radio."
    if current.lower() == state.lower():
        return f"{kind} is already {state.lower()}."

    ok, out = _run_ps(_WINRT_PRELUDE + f"""
[Windows.Devices.Radios.RadioState,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
$r = $radios | Where-Object {{ "$($_.Kind)" -eq "{kind}" }} | Select-Object -First 1
if (-not $r) {{ Write-Output "MISSING"; exit 1 }}
$res = Await ($r.SetStateAsync([Windows.Devices.Radios.RadioState]::{state})) ([Windows.Devices.Radios.RadioAccessStatus])
Write-Output "$res"
""")
    if not ok:
        return f"ERROR: {out}"

    # Confirm rather than trust the return code.
    ok, now = _radio_state(kind)
    if ok and now.lower() == state.lower():
        return f"{kind} turned {state.lower()}."
    return f"Asked to turn {kind} {state.lower()}, but it is now reported as {now}."


# ---------- connecting a paired device ----------
# The tool used to have no way to connect anything: asked to "connect to echo"
# on 3 Oct 2026 it checked Bluetooth was on and the model announced "Echo is
# now connected" while the speaker sat disconnected. Switching the device's
# audio service off and on (BluetoothSetServiceState) makes Windows connect it.

def _bt():
    import ctypes
    from ctypes import wintypes as w

    class SYSTEMTIME(ctypes.Structure):
        _fields_ = [(n, w.WORD) for n in ("y", "mo", "dw", "d", "h", "mi", "s", "ms")]

    class Info(ctypes.Structure):
        _fields_ = [("dwSize", w.DWORD), ("Address", ctypes.c_ulonglong), ("ulClassofDevice", w.ULONG),
                    ("fConnected", w.BOOL), ("fRemembered", w.BOOL), ("fAuthenticated", w.BOOL),
                    ("stLastSeen", SYSTEMTIME), ("stLastUsed", SYSTEMTIME), ("szName", w.WCHAR * 248)]

    class Search(ctypes.Structure):
        _fields_ = [("dwSize", w.DWORD), ("fReturnAuthenticated", w.BOOL), ("fReturnRemembered", w.BOOL),
                    ("fReturnUnknown", w.BOOL), ("fReturnConnected", w.BOOL), ("fIssueInquiry", w.BOOL),
                    ("cTimeoutMultiplier", ctypes.c_ubyte), ("hRadio", w.HANDLE)]

    class GUID(ctypes.Structure):
        _fields_ = [("a", w.DWORD), ("b", w.WORD), ("c", w.WORD), ("d", ctypes.c_ubyte * 8)]

    lib = ctypes.WinDLL("bthprops.cpl")
    lib.BluetoothFindFirstDevice.restype = w.HANDLE
    return ctypes, lib, Info, Search, GUID


def _paired() -> list:
    ctypes, lib, Info, Search, _ = _bt()
    search = Search(dwSize=ctypes.sizeof(Search), fReturnAuthenticated=1, fReturnRemembered=1,
                    fReturnConnected=1)
    info = Info(dwSize=ctypes.sizeof(Info))
    out = []
    h = lib.BluetoothFindFirstDevice(ctypes.byref(search), ctypes.byref(info))
    if not h:
        return out
    while True:
        out.append((info.szName, info.Address, bool(info.fConnected), info.ulClassofDevice))
        info = Info(dwSize=ctypes.sizeof(Info))
        if not lib.BluetoothFindNextDevice(w_handle(h), ctypes.byref(info)):
            break
    lib.BluetoothFindDeviceClose(w_handle(h))
    return out


def w_handle(h):
    import ctypes
    return ctypes.c_void_p(h)


def connect_device(name: str) -> str:
    from difflib import SequenceMatcher
    import time as _t

    want = name.strip().lower()
    devices = _paired()
    if not devices:
        return "I couldn't read the list of paired Bluetooth devices - is Bluetooth on?"
    scored = sorted(((1.0 if want in d[0].lower() else SequenceMatcher(None, want, d[0].lower()).ratio(), d)
                     for d in devices), key=lambda x: -x[0])
    score, (label, address, connected, _cod) = scored[0]
    if score < 0.5:
        # "my headphones" names a kind, not a device: use the class each
        # device reports (major 4 = audio; minor 1/2/6 worn, 5 speaker).
        kinds = {"headphones": {1, 2, 6}, "headphone": {1, 2, 6}, "earphones": {1, 2, 6},
                 "earbuds": {1, 2, 6}, "buds": {1, 2, 6}, "headset": {1, 2, 6}, "speaker": {5, 7},
                 "speakers": {5, 7}}
        kind = next((v for k, v in kinds.items() if k in want.split()), None)
        if kind:
            fit = [d for d in devices if (d[3] >> 8) & 0x1F == 4 and (d[3] >> 2) & 0x3F in kind]
            if len(fit) == 1:
                score, (label, address, connected, _cod) = 1.0, fit[0]
            elif fit:
                return "Which one - " + " or ".join(d[0] for d in fit) + "?"
    if score < 0.5:
        return (f"No paired device sounds like {name}. Paired: "
                + ", ".join(sorted({d[0] for d in devices})) + ".")
    if connected:
        return f"{label} is already connected."
    ctypes, lib, Info, _, GUID = _bt()
    info = Info(dwSize=ctypes.sizeof(Info), Address=address)
    lib.BluetoothGetDeviceInfo(None, ctypes.byref(info))
    sink = GUID(0x110B, 0, 0x1000, (ctypes.c_ubyte * 8)(0x80, 0, 0, 0x80, 0x5F, 0x9B, 0x34, 0xFB))
    lib.BluetoothSetServiceState(None, ctypes.byref(info), ctypes.byref(sink), 0)
    _t.sleep(1.5)
    lib.BluetoothSetServiceState(None, ctypes.byref(info), ctypes.byref(sink), 1)
    for _ in range(12):                 # connected only when Windows says so
        _t.sleep(1)
        lib.BluetoothGetDeviceInfo(None, ctypes.byref(info))
        if info.fConnected:
            return f"Connected to {label}."
    return f"Asked {label} to connect, but it didn't - is it switched on and nearby?"


@tool(tags=TAG)
def bluetooth(action: str = "status", device: str = "") -> str:
    """Turn Bluetooth on or off, report whether it is on, or connect a paired
    device such as a speaker or headphones ("connect to echo").

    action: one of on, off, status, settings, connect
    device: for connect, the device's name in the user's words, e.g. echo, buds, headphones
    """
    a = action.strip().lower()
    if a == "connect" or (device and a not in ("on", "off", "status", "settings")):
        if not device.strip():
            return "Which device should I connect?"
        return connect_device(device)
    if a in ("settings", "open"):
        return open_windows_settings("bluetooth")
    if a in ("on", "enable", "true"):
        return _set_radio("Bluetooth", "On")
    if a in ("off", "disable", "false"):
        return _set_radio("Bluetooth", "Off")
    ok, state = _radio_state("Bluetooth")
    if not ok:
        return f"ERROR: {state}"
    if state == "MISSING":
        return "This PC has no Bluetooth radio."
    return f"Bluetooth is {state.lower()}."


@tool(tags=TAG)
def wifi(action: str = "status") -> str:
    """Turn Wi-Fi on or off, or report whether it is currently on.

    action: one of on, off, status, settings
    """
    a = action.strip().lower()
    if a in ("settings", "open"):
        return open_windows_settings("wifi")
    if a in ("on", "enable", "true"):
        return _set_radio("WiFi", "On")
    if a in ("off", "disable", "false"):
        return _set_radio("WiFi", "Off")
    ok, state = _radio_state("WiFi")
    if not ok:
        return f"ERROR: {state}"
    if state == "MISSING":
        return "This PC has no Wi-Fi radio."
    return f"Wi-Fi is {state.lower()}."


@tool(tags=TAG)
def list_bluetooth_devices() -> str:
    """List Bluetooth devices paired with this PC and whether they are connected."""
    ok, out = _run_ps(
        "Get-PnpDevice -Class Bluetooth -ErrorAction SilentlyContinue | "
        "Select-Object FriendlyName,Status | ConvertTo-Json -Compress"
    )
    if not ok:
        return f"ERROR: {out}"
    try:
        data = json.loads(out or "[]")
    except json.JSONDecodeError:
        return "No Bluetooth devices found."
    if isinstance(data, dict):
        data = [data]
    # The adapter's own driver entries are noise to a person asking this.
    rows = [d for d in data if d.get("FriendlyName")
            and not str(d["FriendlyName"]).lower().startswith(("bluetooth ", "microsoft bluetooth"))]
    if not rows:
        return "No paired Bluetooth devices."
    return "\n".join(f"{d['FriendlyName']} - {d['Status']}" for d in rows)


# Friendly name -> the ms-settings: URI Windows uses for that page.
SETTINGS_PAGES = {
    "bluetooth": "bluetooth",
    "wifi": "network-wifi",
    "wi-fi": "network-wifi",
    "network": "network",
    "internet": "network",
    "display": "display",
    "screen": "display",
    "sound": "sound",
    "audio": "sound",
    "volume": "sound",
    "battery": "batterysaver",
    "power": "powersleep",
    "apps": "appsfeatures",
    "installed apps": "appsfeatures",
    "update": "windowsupdate",
    "updates": "windowsupdate",
    "storage": "storagesense",
    "printers": "printers",
    "mouse": "mousetouchpad",
    "keyboard": "keyboard",
    "notifications": "notifications",
    "privacy": "privacy",
    "accounts": "yourinfo",
    "time": "dateandtime",
    "language": "regionlanguage",
    "defaults": "defaultapps",
    "startup": "startupapps",
    "home": "",
    "settings": "",
}


@tool(tags=TAG)
def open_windows_settings(page: str = "home") -> str:
    """Open Windows Settings, optionally straight to a specific page.

    page: for example bluetooth, wifi, display, sound, battery, apps, update
    """
    key = page.strip().lower()
    if key not in SETTINGS_PAGES:
        options = ", ".join(sorted(set(SETTINGS_PAGES)))
        return f"ERROR: unknown settings page '{page}'. Try one of: {options}"
    uri = f"ms-settings:{SETTINGS_PAGES[key]}"
    try:
        subprocess.Popen(["explorer.exe", uri],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        return f"ERROR: could not open settings: {e}"
    return f"Opened Windows Settings at {key}."
