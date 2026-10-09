"""Routines: one spoken name, several actions, always in the same order.

The everyday model manages one to three tool calls per request and loses
the thread on longer chains, so a job you do often is written down once in
routines.yaml and run step by step here - no model involved, nothing to get
wrong. "study mode", "run study mode" and "start study mode" all run it.

A step is `tool: value` (the value goes to the tool's first parameter),
`tool: {param: value, ...}`, `wait: seconds`, `say: text` (added to the
reply), or `do: "a command"` - anything you could say to Athena that is
handled without the model ("open downloads", "open experiment 6.1").
"""
from __future__ import annotations

import inspect
import time

import yaml

from ..config import ROOT
from ..registry import tool

TAG = ["system"]
FILE = ROOT / "routines.yaml"


def load() -> dict[str, dict]:
    """{name: {"steps": [...], "say": str}}, names lower-cased."""
    if not FILE.exists():
        return {}
    data = yaml.safe_load(FILE.read_text(encoding="utf-8")) or {}
    out: dict[str, dict] = {}
    for name, body in data.items():
        if isinstance(body, list):
            body = {"steps": body}
        if isinstance(body, dict):
            out[str(name).strip().lower()] = body
    return out


def find(spoken: str) -> str | None:
    """The routine a phrase names: "study mode", "run study", "start the
    study mode routine" all find "study mode"."""
    import re

    s = re.sub(r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please)\W+)*", "", spoken.strip().lower())
    s = re.sub(r"^(?:run|start|begin|do|activate|enable|switch\s+to|turn\s+on|go\s+into|"
               r"enter|launch)\s+(?:the\s+|my\s+)?", "", s)
    s = re.sub(r"\s+(?:routine|workflow)$", "", s).strip(" .!?")
    names = load()
    if s in names:
        return s
    for n in names:
        if s and (s == n.removesuffix(" mode") or s + " mode" == n):
            return n
    return None


def _run_step(step, say: list[str]) -> str:
    from .. import registry

    if isinstance(step, str):
        step = {"do": step}
    if not isinstance(step, dict) or len(step) != 1:
        return f"skipped a step I could not read: {step!r}"
    (key, val), = step.items()
    key = str(key).strip()
    if key == "wait":
        time.sleep(min(float(val), 120))
        return ""
    if key == "say":
        say.append(str(val))
        return ""
    if key == "do":
        from ..agent import route

        hit = route(str(val))
        if hit is None:
            return f"'{val}' needs the model - write it as a tool step instead"
        key, args = hit
        t = registry.get(key)
    else:
        t = registry.get(key)
        if t is None:
            return f"no tool called '{key}'"
        if isinstance(val, dict):
            args = val
        else:
            first = next(iter(inspect.signature(t.fn).parameters), None)
            args = {first: val} if first else {}
    return t.run(**args)


@tool(tags=TAG)
def run_routine(name: str) -> str:
    """Run a saved routine - a list of actions stored under one name in routines.yaml.

    name: the routine's name, e.g. "study mode"
    """
    key = find(name) or name.strip().lower()
    routines = load()
    if key not in routines:
        have = ", ".join(routines) or "none yet"
        return f"ERROR: no routine called '{name}'. Saved routines: {have}. Edit {FILE} to add one."
    body = routines[key]
    say: list[str] = []
    problems: list[str] = []
    reports: list[str] = []
    for step in body.get("steps") or []:
        result = _run_step(step, say)
        if result.startswith("ERROR") or result.startswith("no tool") or \
                result.startswith("skipped") or "needs the model" in result:
            problems.append(result.removeprefix("ERROR:").strip()[:120])
        elif result and isinstance(step, dict) and next(iter(step)) in _REPORTS:
            reports.append(result)
    # Actions confirm themselves by happening ("Set volume to 25" read aloud
    # is noise); what a step went and fetched is the point of the step.
    head = body.get("say") or ("" if reports else f"{key.capitalize()} is on.")
    tail = f"\n{len(problems)} step(s) failed: " + " | ".join(problems) if problems else ""
    return "\n\n".join(p for p in [" ".join([head, *say]).strip(), *reports] if p) + tail


# Steps whose output is information to pass on, not an action to confirm.
_REPORTS = {"daily_brief", "get_weather", "battery", "system_info", "get_datetime",
            "list_reminders", "web_search", "read_webpage", "browser_read",
            "list_running_apps", "show_timetable", "recall"}


@tool(tags=TAG)
def list_routines() -> str:
    """List the saved routines and what each one does."""
    routines = load()
    if not routines:
        return f"No routines saved yet. Add them to {FILE}."
    lines = []
    for name, body in routines.items():
        steps = body.get("steps") or []
        desc = ", ".join(f"{k} {v}" if not isinstance(v, dict) else k
                         for s in steps if isinstance(s, dict) for k, v in s.items())
        lines.append(f"{name}: {desc[:160]}")
    return "\n".join(lines)
