"""Needs you: everything that is waiting on the user, in one list (idea from
Symvibe's "working / resting / needs you" map, 20 Oct 2026).

Sources:
- Athena Deck (deck_link.py): a console stuck at a prompt only you can answer
  - a permission request, a y/n question;
- Athena: missions that failed today, loops that gave up or paused.

The voice process (start_watch) says new Deck ones aloud and pushes them to the
phone; the rest were already said when they happened. The HUD shows the lot at
the top of the Missions page, each with what can be done about it: reply (typed
into the console), retry, open the Deck, dismiss.

(Munder Difflin's office was a source too until the user uninstalled it, 20 Oct.)
"""
from __future__ import annotations

import datetime as dt
import threading
import time

from .config import DATA, cfg
from .registry import tool
from .store import load_json, save_json

DISMISSED = DATA / "attention_dismissed.json"
TOLD = DATA / "attention_told.json"
SPOKEN = ("console",)               # the rest were said when they happened (missions, loops)
DAY = 24 * 3600


def _deck_items() -> list[dict]:
    """Athena Deck consoles waiting on you (a permission prompt, a y/n question)."""
    from . import deck_link
    b = deck_link.brief()
    return [{"key": f"console:{c['id']}:{int(c.get('since') or 0)}", "kind": "console", "who": c["title"], "console": c["id"],
             "title": "needs you on the Deck", "text": c.get("detail") or "It's waiting for your answer.",
             "since": float(c.get("since") or time.time()), "can_reply": True,
             # Said aloud only for the AI agents: a PowerShell running a menu ("←/→ navigate • enter
             # confirm") looked like a question and was read out (21-28 Oct 2026). Still on the HUD.
             "aloud": c.get("kind") in ("claude", "codex", "gemini")}
            for c in b.get("consoles", []) if c.get("state") == "needs"] if b.get("running") else []


def _athena_items() -> list[dict]:
    from . import loops, missions
    out = []
    for m in missions.load():
        if m["status"] == "failed" and not m.get("parent") and not m.get("loop") \
                and time.time() - float(m.get("ended") or m["started"]) < DAY:
            out.append({"key": f"mission:{m['id']}", "kind": "failed", "who": missions.WORKERS.get(m.get("worker"), "Athena")
                        if m.get("worker") != "chain" else "Athena", "mission": m["id"], "title": f"got stuck on {m['title']}",
                        "text": (m.get("result") or "")[:400], "since": float(m.get("ended") or m["started"]), "can_retry": True})
    for lp in loops.load():
        if lp["status"] in ("gave_up", "paused") and time.time() - float(lp.get("ended") or 0) < DAY:
            out.append({"key": f"loop:{lp['id']}:{lp['status']}", "kind": "loop", "who": "Loop", "loop": lp["id"],
                        "title": ("gave up" if lp["status"] == "gave_up" else "paused") + f": {lp['task'][:80]}",
                        "text": lp.get("note", ""), "since": float(lp.get("ended") or 0)})
    return out


def items() -> list[dict]:
    """What is waiting on the user right now, newest first (dismissed ones left out)."""
    out = []
    for source in (_deck_items, _athena_items):
        try:
            out += source()
        except Exception as e:                    # another app's state: never break the page
            print(f"[attention: {source.__name__}: {e}]")
    gone = set(load_json(DISMISSED, []) if DISMISSED.exists() else [])
    return sorted((i for i in out if i["key"] not in gone), key=lambda i: -i["since"])


def dismiss(key: str) -> None:
    gone = load_json(DISMISSED, []) if DISMISSED.exists() else []
    save_json(DISMISSED, (gone + [key])[-500:])


def answer(key: str, text: str) -> str:
    """Type your answer into the console that's asking, then Enter."""
    text = text.strip()
    if not text:
        return "Type the answer first."
    item = next((i for i in items() if i["key"] == key), None)
    if not item or item["kind"] != "console":
        return "That's already been dealt with."
    from . import deck_link
    deck_link.api("POST", f"/api/consoles/{item['console']}/send", {"text": text, "submit": True})
    dismiss(key)
    return f"Typed your answer into {item['who']}."


def retry(key: str) -> str:
    from . import missions
    item = next((i for i in items() if i["key"] == key), None)
    m = next((x for x in missions.load() if item and x["id"] == item.get("mission")), None)
    if not m:
        return "Nothing to retry."
    new = missions.start_chain(m["task"]) if m.get("worker") == "chain" else missions.start(m["task"], m.get("worker", "claude"))
    dismiss(key)
    return f"Trying again: {new['title']}."


def tell(announce, line: str, title: str, tags: str = "warning", priority: str = "high") -> None:
    """Say it (unless you're away or it's quiet hours) and push it to the phone."""
    from . import presence
    from .alerts import in_quiet_hours
    quiet = in_quiet_hours(dt.datetime.now(), *(cfg.get("alerts.quiet_hours") or ["23:00", "07:00"]))
    if announce and not presence.away() and not quiet:
        announce(line)
    try:
        from . import phone
        phone.push(line, title=title, priority=priority, tags=tags, click="")
    except Exception as e:
        print(f"[attention push: {e}]")


def _line(i: dict) -> str:
    first = i["text"].strip().splitlines()[0] if i["text"].strip() else ""
    return f"{i['who']} {i['title']}" + (f": {first[:200]}" if first else ".")


def _tell_finished(announce) -> None:
    """A console Athena started on a task ("have Claude build ...") has stopped
    working: say so once, with how long it took (deck_link.jobs)."""
    from . import deck_link
    for j in deck_link.jobs():
        if deck_link.job_active(j):
            continue
        deck_link.mark_done(j["id"])
        if j["state"] == "gone":                  # closed by hand: nothing to tell
            continue
        took = max(1, round((time.time() - j["started"]) / 60))
        tell(announce, f"{j['who']} finished {j['title']} after about {took} minute{'s' if took > 1 else ''} - it's on the Deck.",
             title=f"{j['who']} finished", tags="white_check_mark", priority="default")


def start_watch(announce) -> None:
    """In the voice process: say each new Deck console that needs you, once,
    and each job Athena started on the Deck once it's done."""
    def loop() -> None:
        while True:
            time.sleep(10)
            try:
                now = [i for i in items() if i["kind"] in SPOKEN and i.get("aloud", True)]
                told = load_json(TOLD, {}) if TOLD.exists() else {}
                for i in now:
                    if i["key"] not in told:
                        tell(announce, _line(i), title=f"{i['who']} needs you")
                        told[i["key"]] = time.time()
                keep = {i["key"] for i in now}          # a console that asks again later is said again
                save_json(TOLD, {k: v for k, v in told.items() if k in keep})
                _tell_finished(announce)
            except Exception as e:
                print(f"[attention watch: {e}]")

    threading.Thread(target=loop, daemon=True, name="athena-attention").start()


@tool(tags=["attention"])
def needs_me() -> str:
    """What is waiting on the user: Deck consoles asking something, missions
    that got stuck today, loops that gave up. Use for "does anything need me",
    "who needs me", "what's waiting for me"."""
    now = items()
    if not now:
        return "Nothing needs you right now."
    return f"{len(now)} thing{'s' if len(now) > 1 else ''} need{'s' if len(now) == 1 else ''} you. " + \
        " ".join(_line(i) for i in now[:4])


if __name__ == "__main__":
    import tempfile
    from pathlib import Path
    from unittest import mock

    d = Path(tempfile.mkdtemp())
    deck = {"running": True, "consoles": [{"id": "c1", "title": "Claude Code 1", "state": "needs", "detail": "Allow npm install? (y/n)",
                                           "since": time.time() - 30}, {"id": "c2", "title": "Gemini 1", "state": "working"}]}
    failed = [{"id": "m1", "title": "LOQ table", "status": "failed", "worker": "claude", "started": time.time() - 99,
               "ended": time.time() - 60, "result": "Stopped at the $2.00 limit."}]
    with mock.patch(__name__ + ".DISMISSED", d / "gone.json"), mock.patch("jarvis.deck_link.brief", lambda: deck), \
            mock.patch("jarvis.missions.load", lambda: failed), mock.patch("jarvis.loops.load", lambda: []):
        got = {i["kind"]: i for i in items()}
        assert set(got) == {"console", "failed"}, got
        assert got["console"]["who"] == "Claude Code 1" and got["console"]["can_reply"]
        assert _line(got["console"]) == "Claude Code 1 needs you on the Deck: Allow npm install? (y/n)"
        dismiss(got["failed"]["key"])
        assert {i["kind"] for i in items()} == {"console"}
    said = []
    job = {"id": "c9", "who": "Claude", "title": "Peptide shop website", "started": time.time() - 130, "state": "resting"}
    with mock.patch("jarvis.deck_link.jobs", lambda: [job]), mock.patch("jarvis.deck_link.mark_done", lambda _id: None), \
            mock.patch(__name__ + ".tell", lambda _a, line, **_k: said.append(line)):
        _tell_finished(None)
        job["state"] = "working"
        _tell_finished(None)
    assert said == ["Claude finished Peptide shop website after about 2 minutes - it's on the Deck."], said
    print("attention: checks pass")
