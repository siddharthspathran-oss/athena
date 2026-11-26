"""The skill finder: what you keep doing by hand, offered back as one-name routines
(10 Nov 2026, from the "skill backbone" idea: "based on the last 30 days, are
there any skills we could create?").

Athena reads her own chats - typed, phone, Telegram and spoken, each kept with
the tools it ran - and looks for two or more of the same actions done within
WINDOW requests of each other, in sessions on MIN_DAYS different days. Each such
group is an idea; "save idea 1" makes it a routine (routines.yaml) that runs by
name with no model involved.

Never offered: a step that reaches a person, can't be undone or touches private
things (routines.unsafe), one that failed, or one whose arguments weren't kept.
Nothing is saved until you say so. New ideas go to your phone once a week
(`skills` in config.yaml): ntfy and Telegram, where "save idea 1" works as a reply.
"""
from __future__ import annotations

import datetime as dt
import inspect
import itertools
import json
import re
import threading
import time

from .config import DATA, cfg
from .registry import tool
from .store import load_json, save_json

STATE = DATA / "skill_ideas.json"
LOOKBACK_DAYS = 30
WINDOW = 3            # requests apart that still count as done together
MIN_DAYS = 2          # done together on this many days -> an idea ("done it by hand twice")
MAX_STEPS = 4
MAX_ACTIONS = 8       # per window, so the combinations stay small
SHOWN = 5
CHECK_SECONDS = 1800
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
FOOTER = '\n\nSay "save idea 1", or "save idea 1 as uni stuff" to choose the name.'
_NAME_PREFIX = re.compile(r"^\W*(?:(?:hey|ok|okay)\W+)?(?:athena|jarvis)\b\W*", re.I)

Action = tuple[str, dict]


def _args(raw) -> dict:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    return raw if isinstance(raw, dict) else {}


def _route(text: str) -> Action | None:
    from .routes import route
    try:
        return route(text)
    except Exception:
        return None


def _requests(messages: list[dict]) -> list[tuple[str, list[Action]]]:
    """Each thing you asked, with the actions that ran for it, worked and are safe to replay."""
    from .tools.routines import unsafe

    out: list[tuple[str, list[Action]]] = []
    calls: list[Action] = []
    worked: dict[str, bool] = {}

    def close() -> None:
        if not out:
            return
        text, acts = out[-1]
        named = {n for n, _ in calls}
        routed: Action | None = None
        for name, args in calls + [(n, None) for n in worked if n not in named]:
            if args is None:            # ran straight from the router: it knows the arguments
                routed = routed or _route(text) or ("", {})
                args = routed[1] if routed[0] == name else None
            if args is not None and worked.get(name) and not unsafe(name) and (name, args) not in acts:
                acts.append((name, args))

    for m in messages:
        if m.get("private"):
            continue
        role = m.get("role")
        if role == "user":
            close()
            out.append((str(m.get("content") or ""), []))
            calls, worked = [], {}
        elif role == "assistant":
            calls += [((c.get("function") or {}).get("name", ""), _args((c.get("function") or {}).get("arguments")))
                      for c in m.get("tool_calls") or []]
        elif role == "tool":
            name = m.get("name", "")
            worked[name] = worked.get(name, False) or not str(m.get("content", "")).startswith(("ERROR", "NOT "))
    close()
    return out


def _key(action: Action) -> str:
    name, args = action
    return name + json.dumps({k: v.strip().lower() if isinstance(v, str) else v for k, v in args.items()},
                             sort_keys=True, default=str)


def _as_action(step) -> Action | None:
    """A saved routine step as (tool, args), to tell what's already a routine."""
    from . import registry
    if isinstance(step, str):
        step = {"do": step}
    if not isinstance(step, dict) or len(step) != 1:
        return None
    (name, val), = step.items()
    if name == "do":
        return _route(str(val))
    if isinstance(val, dict):
        return (name, val)
    t = registry.get(name)
    first = next(iter(inspect.signature(t.fn).parameters), None) if t else None
    return (name, {first: val} if first else {})


def _said(text: str) -> str:
    return _NAME_PREFIX.sub("", text).strip(" .?!") or text


def _name(said: list[str]) -> str:
    from .tools.routines import suggest_name
    parts = list(dict.fromkeys(" ".join(suggest_name(s).split()[:2]) for s in said))
    return " and ".join(parts[:2])


def find(chat_list: list[dict], now: float | None = None) -> list[dict]:
    """Ideas, done-on-most-days first: {"steps", "said", "days", "name", "key"}."""
    from .tools import routines

    now = now or time.time()
    groups: dict[frozenset, dict] = {}
    for chat in sorted(chat_list, key=lambda c: float(c.get("created") or 0)):
        created = float(chat.get("created") or 0)
        if created < now - LOOKBACK_DAYS * 86400:
            continue
        day = dt.date.fromtimestamp(created).isoformat()
        reqs = _requests(chat.get("messages") or [])
        for i in range(len(reqs)):
            acts: list[Action] = []
            said: dict[str, str] = {}
            for text, done in reqs[i:i + WINDOW]:
                for a in done:
                    if _key(a) not in said:
                        acts.append(a)
                        said[_key(a)] = _said(text)
            for n in range(2, min(MAX_STEPS, len(acts[:MAX_ACTIONS])) + 1):
                for combo in itertools.combinations(acts[:MAX_ACTIONS], n):
                    keys = frozenset(_key(a) for a in combo)
                    g = groups.setdefault(keys, {"days": set(), "keys": keys})
                    g["days"].add(day)
                    g["steps"] = list(combo)                  # the latest wording and order win
                    g["said"] = list(dict.fromkeys(said[_key(a)] for a in combo))

    have = [frozenset(_key(a) for a in map(_as_action, body.get("steps") or []) if a)
            for body in routines.load().values()]
    ideas = [g for g in groups.values() if len(g["days"]) >= MIN_DAYS and not any(g["keys"] <= r for r in have)]
    # The whole group done together, not every pair inside it as well.
    ideas = [g for g in ideas if not any(g["keys"] < o["keys"] and len(o["days"]) >= len(g["days"]) for o in ideas)]
    ideas.sort(key=lambda g: (-len(g["days"]), -len(g["steps"])))
    return [{"steps": [{n: a} for n, a in g["steps"]], "said": g["said"], "days": sorted(g["days"]),
             "name": _name(g["said"]), "key": "|".join(sorted(g["keys"]))} for g in ideas]


def _chats() -> list[dict]:
    from .chats import CHATS
    cutoff = time.time() - LOOKBACK_DAYS * 86400
    out = []
    for path in CHATS.glob("*.json"):
        try:
            if path.stat().st_mtime >= cutoff:
                out.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out


def _when(days: list[str]) -> str:
    words = [f"{d.day} {d:%b}" for d in map(dt.date.fromisoformat, days[-4:])]
    return ", ".join(words[:-2] + [" and ".join(words[-2:])])


def describe(ideas: list[dict]) -> str:
    return "\n".join(
        f'{i}. On {len(idea["days"])} days ({_when(idea["days"])}) you did these together: '
        + ", then ".join(f'"{s}"' for s in idea["said"]) + f'. A routine called "{idea["name"]}" would do them all.'
        for i, idea in enumerate(ideas[:SHOWN], 1))


@tool(tags=["system"])
def skill_ideas() -> str:
    """Look through the last month of requests for things the user keeps doing together
    by hand, and offer each as a routine they can run by name. Use for "any skill ideas",
    "what could be a skill", "what routines should I make"."""
    ideas = find(_chats())
    save_json(STATE, {**load_json(STATE, {}), "ideas": ideas})
    if not ideas:
        return ("No skill ideas yet: nothing you did by hand came up together on two different days "
                "in the last month. I'll keep looking every week.")
    return describe(ideas) + FOOTER


@tool(tags=["system"])
def save_skill_idea(number: int, name: str = "") -> str:
    """Save one of the skill ideas Athena offered as a routine.

    number: the idea's number, 1 for the first
    name: what the user will say to run it; empty keeps the suggested name
    """
    from .tools import routines

    ideas = load_json(STATE, {}).get("ideas") or []
    if not ideas:
        return "ERROR: there are no skill ideas to save - ask me for skill ideas first."
    if not 1 <= int(number) <= len(ideas):
        return f"ERROR: there's no idea {number} - pick 1 to {min(len(ideas), SHOWN)}."
    idea = ideas[int(number) - 1]
    return routines.save(name.strip() or routines.unique_name(idea["name"]), idea["steps"],
                         "a skill idea from your history")


def due(now: dt.datetime, day: str, at: str, sent: str) -> bool:
    """Once a week, from the set day and time until the week ends."""
    h, m = (int(x) for x in at.split(":"))
    start = (DAYS.index(day[:3].lower()), dt.time(h, m))
    return sent != now.strftime("%G-W%V") and (now.weekday(), now.time()) >= start


def weekly_push() -> str:
    """Ideas not sent before, to the phone (ntfy and Telegram)."""
    from . import phone

    state = load_json(STATE, {})
    pushed = set(state.get("pushed") or [])
    ideas = find(_chats())
    new = [i for i in ideas if i["key"] not in pushed]
    if not new:
        return "no new skill ideas this week"
    # New ones first, so the numbers in the message are the ones "save idea N" uses.
    ideas = new + [i for i in ideas if i["key"] in pushed]
    save_json(STATE, {**state, "ideas": ideas, "pushed": sorted(pushed | {i["key"] for i in new[:SHOWN]})})
    return phone.push(describe(new) + FOOTER, title="Skill ideas", priority="default", tags="bulb")[1]


def _weekly(log) -> None:
    while True:
        try:
            now = dt.datetime.now()
            state = load_json(STATE, {})
            if due(now, str(cfg.get("skills.finder_day", "sat")), str(cfg.get("skills.finder_at", "11:00")),
                   state.get("week", "")):
                save_json(STATE, {**state, "week": now.strftime("%G-W%V")})   # first: a failure mustn't repeat all day
                log(f"skill finder: {weekly_push()}")
        except Exception as e:
            log(f"skill finder failed: {str(e)[:120]}")
        time.sleep(CHECK_SECONDS)


_started = False


def start(log=print) -> None:
    """The weekly look, in the laptop's web process (where the chats are)."""
    global _started
    if _started or not cfg.get("skills.finder", True):
        return
    _started = True
    threading.Thread(target=_weekly, args=(log,), daemon=True, name="skill-finder").start()
