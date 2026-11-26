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
    g = getattr(registry._guard, "agent", None)
    if t is not None and t.name in registry.OPENS and g is not None:
        g._opens_left = getattr(g, "_opens_left", 0) + 1      # a routine you wrote asked for this window
    return t.run(**args)


# ---------- skills Athena saves herself (28 Oct 2026, Hermes Agent's learning loop) ----------
# When the planner works out a new multi-step job, Athena offers to keep its steps as a
# routine: next time the name alone runs it, no model involved. Never steps that reach
# another person, can't be undone, or touch private things - a skill runs without asking.

def unsafe(name: str) -> bool:
    """A step no skill replays: it reaches another person, can't be undone, touches private
    things, runs only from its exact phrase, or is about skills themselves."""
    from .. import registry
    from ..routes import PRIVATE_TOOLS
    t = registry.get(name)
    return (t is None or t.destructive or name in registry.OUTWARD or name in PRIVATE_TOOLS
            or "route_only" in t.tags or name in ("run_routine", "save_skill", "skill_ideas", "save_skill_idea"))


def skill_steps(steps: list[tuple[str, dict, str]]) -> list[dict] | None:
    """The planner's steps (tool, args, result) as routine steps, or None when they
    shouldn't become a skill: fewer than two that worked, or one that isn't safe to replay."""
    good: list[tuple[str, dict]] = []
    for name, args, result in steps:
        if str(result).startswith("ERROR"):
            continue
        if unsafe(name):
            return None
        if not good or good[-1] != (name, args):                     # the same call twice is one step
            good.append((name, dict(args or {})))
    return [{name: args} for name, args in good] if len(good) >= 2 else None


def suggest_name(request: str) -> str:
    """A short name to say next time: "athena, open spotify and play lofi" -> "spotify and play lofi"."""
    import re
    s = re.sub(r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please|can\s+you|could\s+you)\W+)*", "", request.lower())
    s = re.sub(r"\b(?:please|the|my|a|an|for\s+me|now|then|also)\b", " ", s)
    s = re.sub(r"^\s*(?:open|start|play|set|turn|put|make|get|show|tell|find)\s+", "", s)
    words = re.findall(r"[a-z0-9']+", s)[:4]
    return " ".join(words) or "my skill"


def unique_name(name: str) -> str:
    have, n, base = load(), 2, name
    while name in have:
        name, n = f"{base} {n}", n + 1
    return name


def save(name: str, steps: list[dict], note: str = "") -> str:
    """Append one routine to routines.yaml - the rest of the file, comments and all, untouched."""
    name = name.strip().lower()
    if not name or not steps:
        return "ERROR: a skill needs a name and at least one step."
    if name in load():
        return f"ERROR: there's already a routine called {name} - pick another name."
    block = yaml.safe_dump({name: {"steps": steps}}, sort_keys=False, allow_unicode=True, default_flow_style=False)
    with FILE.open("a", encoding="utf-8") as f:
        f.write(f"\n# Saved by Athena {time.strftime('%d %b %Y')}{': ' + note if note else ''}\n{block}")
    return f'Saved. Say "{name}" and I\'ll do it again.'


@tool(tags=TAG + ["route_only"])
def save_skill(name: str, steps: list) -> str:
    """Keep a job Athena just worked out as a routine she can run by name (offered after a
    multi-step job; reached by "yes" or "save it as ...", never on the model's initiative).

    name: what you'll say to run it
    steps: the routine's steps
    """
    return save(name, steps, "from a job she worked out")


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
            "list_running_apps", "show_timetable", "recall",
            "open_lms", "open_upes_login"}     # "you're signed out - sign in first" must reach you


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


if __name__ == "__main__":
    import tempfile
    from pathlib import Path

    from .. import tools  # noqa: F401 - registers every tool
    from ..agent import _SAVE_SKILL
    FILE = Path(tempfile.mkdtemp()) / "routines.yaml"
    FILE.write_text("# mine\nmorning:\n  steps:\n    - get_weather: Dehradun\n", encoding="utf-8")
    job = [("set_volume", {"level": 20}, "Volume set to 20%."), ("music", {"action": "song", "song": "lofi"}, "Playing lofi."),
           ("music", {"action": "song", "song": "lofi"}, "Playing lofi.")]
    plan = skill_steps(job)
    assert plan == [{"set_volume": {"level": 20}}, {"music": {"action": "song", "song": "lofi"}}], plan
    assert skill_steps(job + [("whatsapp_message", {"to": "papa", "text": "hi"}, "Sent.")]) is None    # reaches a person
    assert skill_steps([("set_volume", {"level": 20}, "ok")]) is None                                    # one step isn't a job
    assert skill_steps([("set_volume", {}, "ERROR: x"), ("music", {}, "ok")]) is None                     # only one worked
    name = unique_name(suggest_name("Athena, set the volume low and play lofi"))
    assert name == "volume low and play", name
    assert save(name, plan).startswith("Saved") and save(name, plan).startswith("ERROR")
    got = load()
    assert got["volume low and play"]["steps"] == plan and "morning" in got and "# mine" in FILE.read_text(encoding="utf-8")
    assert find("run volume low and play") == "volume low and play"
    assert _SAVE_SKILL.match("save it as study start").group("n") == "study start"
    assert _SAVE_SKILL.match("Athena, keep that as a skill called night setup.").group("n") == "night setup"
    print("routines: checks pass")
