"""Handoffs: pass a console's work to another agent.

The outgoing agent writes its own brief (goal, done, left, key files, gotchas)
into <repo>/.deck/handoffs/, then the incoming agent - an existing console or a
new one opened beside it - is told to read that brief and carry on. A console
that isn't an agent (a shell), or one that doesn't write the brief in time,
hands over its last screen instead.
"""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path

AGENTS = ("claude", "codex", "gemini")
WAIT = 600                                  # seconds for the outgoing agent to write its brief
BRIEF = ("Hand-off: another agent is taking over this work. Write a hand-off brief to {path} - Markdown, "
         "under 300 words: the goal, what's done, what's left, the key files, decisions made and any gotchas. "
         "Only write that file, change nothing else, then reply: Brief written.")


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40] or "console"


def incoming_message(src_title: str, path: Path, task: str) -> str:
    then = f"then: {task.strip()}" if task.strip() else "then carry on where they left off."
    return f"You're taking over from {src_title}, another agent. Read the hand-off brief at {path} first, {then}"


def start(board, src_id: str, task: str = "", to_id: str = "", kind: str = "", summarize: bool = True) -> dict:
    """Check the request, then do the slow part (waiting for the brief) on a thread."""
    src = board.node(src_id)
    if not src or src["type"] != "console":
        return {"ok": False, "error": "No such console to hand off from."}
    if to_id:
        dst = board.node(to_id)
        if not dst or dst["type"] != "console" or to_id == src_id:
            return {"ok": False, "error": "Pick another console to hand off to."}
    elif kind not in AGENTS:
        return {"ok": False, "error": "Hand off to an existing console or a new Claude, Codex or Antigravity one."}
    threading.Thread(target=_run, args=(board, src, task, to_id, kind, summarize), daemon=True, name="deck-handoff").start()
    return {"ok": True}


def _run(board, src: dict, task: str, to_id: str, kind: str, summarize: bool) -> None:
    say = lambda text: board.emit({"type": "toast", "text": text})              # noqa: E731
    c = board.consoles.get(src["id"])
    folder = Path(src.get("cwd") or ".") / ".deck" / "handoffs"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        ignore = folder.parent / ".gitignore"                 # .deck/ stays out of the repo's git, without touching theirs
        if not ignore.exists():
            ignore.write_text("*\n", encoding="utf-8")
    except OSError as e:
        say(f"Hand-off failed: can't write in {folder} ({e})")
        return
    path = folder / f"{time.strftime('%Y%m%d-%H%M%S')}-{_slug(src['title'])}.md"
    if summarize and c and c.alive and src.get("kind") in AGENTS:
        say(f"{src['title']} is writing its hand-off brief…")
        c.send(BRIEF.format(path=path))
        end = time.time() + WAIT
        while time.time() < end and not (path.exists() and path.stat().st_size > 40 and c.state == "resting"):
            time.sleep(2)
    if not path.exists() or path.stat().st_size <= 40:                # a shell, or no brief in time: its last screen
        screen = c.screen_tail(6000) if c else ""
        path.write_text(f"# Hand-off from {src['title']}\n\nNo written brief - this is the end of its console.\n\n"
                        f"```\n{screen}\n```\n", encoding="utf-8")
    msg = incoming_message(src["title"], path, task)
    if to_id:
        dst = board.consoles.get(to_id)
        if not dst or not dst.alive:
            say("Hand-off failed: the console you picked isn't running.")
            return
        dst.send(msg)
        target = board.node(to_id)["title"]
    else:
        n = board.add_node(src["ws"], "console", kind=kind, cwd=src.get("cwd", ""), x=src["x"] + src["w"] + 40, y=src["y"],
                           prompt=msg, team=src.get("team", ""), role=src.get("role", ""))
        target = n["title"]
        board.emit({"type": "focus", "id": n["id"], "fit": [src["id"], n["id"]]})
    board.update_node(src["id"], brief=str(path))
    board.emit({"type": "board"})
    say(f"Handed off {src['title']} → {target}")


if __name__ == "__main__":
    import tempfile

    class FakeConsole:
        def __init__(self, alive=True):
            self.alive, self.state, self.sent = alive, "resting", []

        def send(self, text):
            self.sent.append(text)
            m = re.search(r"brief to (\S+\.md)", text)
            if m:                                                   # the outgoing agent writes its brief
                Path(m.group(1)).write_text("# Brief\n\nGoal: ship it. Done: tests. Left: docs. Key files: app.py\n", encoding="utf-8")

        def screen_tail(self, n):
            return "PS> npm test\n3 passing"

    class FakeBoard:
        def __init__(self, d):
            self.nodes = {"a": {"id": "a", "type": "console", "kind": "claude", "title": "Claude Code 1", "cwd": d, "ws": "w",
                                "x": 0, "y": 0, "w": 700, "h": 400},
                          "s": {"id": "s", "type": "console", "kind": "shell", "title": "PowerShell 1", "cwd": d, "ws": "w",
                                "x": 0, "y": 500, "w": 700, "h": 400},
                          "b": {"id": "b", "type": "console", "kind": "codex", "title": "Codex 1", "cwd": d, "ws": "w"}}
            self.consoles = {"a": FakeConsole(), "s": FakeConsole(), "b": FakeConsole()}
            self.events, self.added = [], []

        def node(self, i):
            return self.nodes.get(i)

        def emit(self, ev):
            self.events.append(ev)

        def update_node(self, i, **kw):
            self.nodes[i].update(kw)

        def add_node(self, ws, type_, **kw):
            n = {"id": "n", "title": "Codex 2", **kw}
            self.added.append(n)
            return n

    d = tempfile.mkdtemp()
    b = FakeBoard(d)
    assert not start(b, "a", to_id="a")["ok"] and not start(b, "zz", kind="claude")["ok"] and not start(b, "a", kind="shell")["ok"]
    _run(b, b.nodes["a"], "write the docs", "b", "", True)
    brief = Path(b.nodes["a"]["brief"])
    assert brief.read_text(encoding="utf-8").startswith("# Brief") and brief.parent.name == "handoffs"
    assert b.consoles["b"].sent[-1] == incoming_message("Claude Code 1", brief, "write the docs")
    _run(b, b.nodes["s"], "", "", "codex", True)                  # a shell: its last screen is the brief
    shell_brief = Path(b.nodes["s"]["brief"]).read_text(encoding="utf-8")
    assert "3 passing" in shell_brief and b.added[0]["prompt"].endswith("carry on where they left off.")
    assert b.events[-1]["text"] == "Handed off PowerShell 1 → Codex 2"
    print("handoff: checks pass")
