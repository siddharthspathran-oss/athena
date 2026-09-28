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


@tool(tags=TAG)
def bluetooth(action: str = "status") -> str:
    """Turn Bluetooth on or off, or report whether it is currently on.

    action: one of on, off, status, settings
    """
    a = action.strip().lower()
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
