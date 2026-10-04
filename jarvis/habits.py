"""Habit learning: notice what you do at the same time most days, and offer to
make it an automation.

Every action you ask for (music, apps, devices, focus...) is noted with the
time in data/habits.jsonl - the tool and its arguments only. Every few minutes
the last two weeks are checked: the same action within a 45-minute window on
at least habits.min_days days is a habit. Around that time, once, Athena asks
"You usually ... around now - want me to do that every day?" and "yes" makes
it a rule (rules.py). Actions an automation ran are not counted.
"""
from __future__ import annotations

import datetime as dt
import json
import statistics
import threading
import time

from .config import DATA, cfg
from .store import load_json, save_json

LOG = DATA / "habits.jsonl"
OFFERED = DATA / "habits_offered.json"
# Things you do, not things you ask about.
ACTIONS = {"open_app", "close_app", "play_song", "play_track", "play_my_taste", "play_my_playlist", "music",
           "bluetooth", "set_volume", "wifi", "free_gpu", "run_routine", "lock_screen", "open_url",
           "organize_folder", "open_folder"}
_quiet = threading.local()        # set while an automation runs, so it is not counted


def automated(on: bool) -> None:
    _quiet.on = on


def record(name: str, args: dict, result: str) -> None:
    if name not in ACTIONS or getattr(_quiet, "on", False) or str(result).startswith("ERROR"):
        return
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.time(), "tool": name, "args": args}, sort_keys=True) + "\n")
    except Exception:
        pass


def _label(name: str, args: dict) -> str:
    val = next((str(v) for v in args.values() if v not in ("", None, 0)), "")
    return {"play_my_playlist": "play your playlist", "play_my_taste": "play something you like",
            "free_gpu": "turn on game mode", "lock_screen": "lock the PC"}.get(
        name, f"{name.replace('_', ' ')} {val}".strip())


def find(days: int = 14) -> list[dict]:
    """[{tool, args, at "HH:MM", days, label}] for repeated actions."""
    since = time.time() - days * 86400
    groups: dict[str, list[float]] = {}
    try:
        for line in LOG.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if r["t"] >= since:
                groups.setdefault(json.dumps([r["tool"], r["args"]], sort_keys=True), []).append(r["t"])
    except Exception:
        return []
    out = []
    need = int(cfg.get("habits.min_days", 4))
    for key, ts in groups.items():
        mins = [(dt.datetime.fromtimestamp(t).hour * 60 + dt.datetime.fromtimestamp(t).minute, dt.date.fromtimestamp(t))
                for t in ts]
        centre = statistics.median(m for m, _ in mins)
        near = {d for m, d in mins if abs(m - centre) <= 45}
        if len(near) >= need:
            name, args = json.loads(key)
            out.append({"tool": name, "args": args, "at": f"{int(centre) // 60:02d}:{int(centre) % 60:02d}",
                        "days": len(near), "label": _label(name, args)})
    return out


def check_and_offer(speak) -> str | None:
    """At a habit's usual time, offer it once. Returns what was said."""
    from . import offer, rules
    if rules.paused():
        return None
    offered = load_json(OFFERED, {})
    now = dt.datetime.now()
    existing = {json.dumps(r["do"]) for r in rules.load()}
    for h in find():
        key = json.dumps([h["tool"], h["args"]], sort_keys=True)
        cmd = f"tool:{h['tool']} {json.dumps(h['args'])}"
        if key in offered or json.dumps([cmd]) in existing:
            continue
        at = dt.datetime.combine(now.date(), dt.time.fromisoformat(h["at"]))
        if abs((now - at).total_seconds()) > 15 * 60:
            continue
        offered[key] = now.isoformat(timespec="seconds")
        save_json(OFFERED, offered)
        line = (f"You usually {h['label']} around {h['at']} - {h['days']} days out of the last two weeks. "
                f"Want me to do it automatically every day?")
        offer.set_choices([offer.Choice(h["label"], "add_rule", {"when": f"every day at {h['at']}", "do": cmd})])
        speak(line)
        rules.log(f"offered a habit: {h['label']} at {h['at']}", "habit")
        return line
    return None


def start(speak) -> None:
    def loop():
        while True:
            time.sleep(300)
            try:
                check_and_offer(speak)
            except Exception:
                pass
    threading.Thread(target=loop, daemon=True, name="athena-habits").start()


if __name__ == "__main__":
    import os
    import tempfile
    from pathlib import Path
    tmp = Path(tempfile.mkdtemp()) / "h.jsonl"
    LOG = tmp
    base = dt.datetime.now().replace(hour=22, minute=5, second=0)
    with open(tmp, "w") as f:
        for d in range(5):
            t = (base - dt.timedelta(days=d, minutes=d * 7)).timestamp()
            f.write(json.dumps({"t": t, "tool": "play_my_playlist", "args": {}}) + "\n")
        f.write(json.dumps({"t": base.timestamp(), "tool": "open_app", "args": {"name": "chrome"}}) + "\n")
    got = find()
    assert len(got) == 1 and got[0]["tool"] == "play_my_playlist" and got[0]["at"].startswith("21:5"), got
    print("ok", got)
