"""What Athena is doing right now, for the HUD to show while you talk.

Voice runs in its own process and the HUD is served by another, so they meet
in a small file: the voice loop writes its state (idle, listening, thinking,
speaking), the microphone level, what it heard and what it said; the web
server reads it and hands it to the page several times a second.

A file rather than a socket because either side can restart without the
other noticing, and a stale file is harmless - it carries a timestamp, and
the HUD treats anything older than a few seconds as "voice is not running".

The web server keeps its own log for typed commands and merges the two, so
this file has exactly one writer and never needs a lock.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

from .config import DATA

VOICE_FILE = DATA / "live_voice.json"
WAKE_FILE = DATA / "live_wake.json"
MAX_LOG = 40

_lock = threading.Lock()
_state: dict[str, Any] = {"state": "idle", "level": 0.0, "heard": "",
                          "reply": "", "log": [], "ts": 0.0}
_last_level_write = 0.0


def _write() -> None:
    _state["ts"] = time.time()
    tmp = VOICE_FILE.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(_state), encoding="utf-8")
        os.replace(tmp, VOICE_FILE)          # atomic: the reader never sees half
    except OSError:
        pass                                 # a missed frame is not an error


def _note(line: str) -> None:
    _state["log"] = (_state["log"] + [[time.strftime("%H:%M:%S"), line]])[-MAX_LOG:]


# ---------- written by the voice process ----------

def start_heartbeat() -> None:
    """Keep the timestamp fresh while nothing else is being written.

    The microphone level refreshes the file constantly while listening, but
    not while Athena is transcribing, thinking or speaking - and a long answer
    would otherwise age past the HUD's cut-off and show voice as offline in
    the middle of talking.
    """
    def beat():
        while True:
            time.sleep(2)
            with _lock:
                _write()

    threading.Thread(target=beat, daemon=True, name="live-heartbeat").start()

def state(name: str) -> None:
    with _lock:
        if _state["state"] != name:
            _state["state"] = name
            _note({"listening": "listening", "thinking": "processing request",
                   "speaking": "responding", "idle": "standing by",
                   "muted": "MICROPHONE SILENT - check the mute key"}.get(name, name))
        _write()


def set_value(key: str, value) -> None:
    """A small value for the HUD, e.g. core_scale from the hand gestures."""
    with _lock:
        if _state.get(key) != value:
            _state[key] = value
            _write()


def note(line: str) -> None:
    with _lock:
        _note(line)
        _write()


def level(rms: float) -> None:
    """Microphone level, throttled - it arrives many times a second and the
    HUD only needs enough of it to make the waveform move."""
    global _last_level_write
    now = time.time()
    if now - _last_level_write < 0.08:
        return
    value = round(min(1.0, float(rms) * 12), 2)
    # A quiet room reads exactly 0 from this laptop's mic array, over and
    # over: rewriting the file twelve times a second to say so again was the
    # voice process's busiest disk work while idle. The heartbeat keeps the
    # timestamp fresh.
    if value == _state["level"]:
        return
    _last_level_write = now
    with _lock:
        _state["level"] = value
        _write()


def heard(text: str) -> None:
    with _lock:
        _state["heard"] = text
        _note(f"heard: {text[:70]}")
        _write()


def replied(text: str) -> None:
    with _lock:
        _state["reply"] = text
        _note(f"replied: {text[:70]}")
        _write()


def step(tool: str) -> None:
    with _lock:
        _note(f"tool: {tool}")
        _write()


def take_wake() -> bool:
    """True if the HUD's mic button was pressed in the last 30 seconds.

    Consumed on read, so one press addresses one sentence - exactly as saying
    "Athena" would.
    """
    try:
        pressed = json.loads(WAKE_FILE.read_text(encoding="utf-8")).get("ts", 0)
    except (OSError, ValueError):
        return False
    try:
        WAKE_FILE.unlink()
    except OSError:
        pass
    return time.time() - pressed < 30


# ---------- read by the web server ----------

def request_wake() -> None:
    WAKE_FILE.write_text(json.dumps({"ts": time.time()}), encoding="utf-8")


def read_voice() -> dict[str, Any]:
    try:
        data = json.loads(VOICE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"state": "offline", "level": 0, "heard": "", "reply": "",
                "log": [], "running": False}
    data["running"] = time.time() - data.get("ts", 0) < 6
    if not data["running"]:
        # "stopped" = told to stop ("stop listening"); anything else is offline.
        data["state"] = "stopped" if data.get("state") == "stopped" else "offline"
        data["level"] = 0
    return data
