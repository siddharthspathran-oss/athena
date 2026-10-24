"""Flows: boxes wired together, Langflow-style (23 Oct 2026).

A wire carries one box's text into the next. Running a box runs everything
that feeds it first, in order:

  - note, text, checklist, kanban, file, web tile, console - give their text
    (a console: the end of its screen);
  - an Agent step runs its instructions on what flows in, headless and
    read-only (Claude with Read/Glob/Grep, or Codex's read-only sandbox -
    studio.run_agent), and gives its reply;
  - at the end of a wire, a note or text box is filled with what arrives, and
    a console is sent it as a message (real runs only, never tests).

Tests (Langflow's Playground): an Agent step keeps examples - an input and a
phrase the answer must contain. Each one runs the whole flow with the input
in place of the flow's first note, writing nothing anywhere, and keeps every
step's output, so a miss shows which step went wrong.
"""
from __future__ import annotations

import re
import threading
import time
from collections import defaultdict
from pathlib import Path

from . import studio

MAX_IN = 20_000            # characters one box hands on
MAX_OUT = 60_000           # characters kept on a box
MAX_TESTS = 12


def wires_of(state: dict, wid: str) -> list[dict]:
    w = next((w for w in state["workspaces"] if w["id"] == wid), None)
    return (w or {}).get("wires") or []


def feeds(wires: list[dict], frm: str, to: str) -> bool:
    """Does `frm` already reach `to` along the wires?"""
    out = defaultdict(list)
    for w in wires:
        out[w["from"]].append(w["to"])
    seen, stack = set(), [frm]
    while stack:
        n = stack.pop()
        if n == to:
            return True
        if n not in seen:
            seen.add(n)
            stack += out[n]
    return False


def order(state: dict, wid: str, targets: list[str]) -> list[str]:
    """The targets and every box feeding them, sources first (top to bottom
    among equals). ValueError if the wires go round in a circle."""
    wires = wires_of(state, wid)
    into = defaultdict(list)
    for w in wires:
        into[w["to"]].append(w["from"])
    need, stack = set(), list(targets)
    while stack:
        n = stack.pop()
        if n not in need:
            need.add(n)
            stack += into[n]
    y = {n["id"]: (n["y"], n["x"]) for n in state["nodes"]}
    indeg = {n: sum(1 for f in into[n] if f in need) for n in need}
    ready = sorted((n for n in need if not indeg[n]), key=lambda n: y.get(n, (0, 0)))
    out = []
    while ready:
        n = ready.pop(0)
        out.append(n)
        for w in wires:
            if w["from"] == n and w["to"] in need:
                indeg[w["to"]] -= 1
                if not indeg[w["to"]]:
                    ready = sorted(ready + [w["to"]], key=lambda m: y.get(m, (0, 0)))
    if len(out) != len(need):
        raise ValueError("These wires go round in a circle - a box can't feed itself.")
    return out


def component(state: dict, wid: str, nid: str) -> set[str]:
    """Every box joined to nid by wires, either way."""
    nb = defaultdict(set)
    for w in wires_of(state, wid):
        nb[w["from"]].add(w["to"])
        nb[w["to"]].add(w["from"])
    seen, stack = set(), [nid]
    while stack:
        n = stack.pop()
        if n not in seen:
            seen.add(n)
            stack += nb[n]
    return seen


def ends(state: dict, wid: str, members: set[str]) -> list[str]:
    """The boxes in a flow that feed nothing further: where its results land."""
    starts = {w["from"] for w in wires_of(state, wid) if w["from"] in members}
    return [n for n in members if n not in starts]


def reaches(state: dict, wid: str, nid: str) -> list[str]:
    """The ends a box's output flows on to (itself if nothing follows): running a step
    carries its result all the way, not just up to the step."""
    out = defaultdict(list)
    for w in wires_of(state, wid):
        out[w["from"]].append(w["to"])
    seen, stack, tail = set(), [nid], []
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        if not out[n]:
            tail.append(n)
        stack += out[n]
    return tail


def catalog(state: dict) -> list[dict]:
    """Every flow on the board, for Athena: named after where it ends."""
    out, seen = [], set()
    for w in state["workspaces"]:
        for wire in w.get("wires") or []:
            if wire["from"] in seen:
                continue
            members = component(state, w["id"], wire["from"])
            seen |= members
            nodes = {n["id"]: n for n in state["nodes"] if n["id"] in members}
            tail = sorted(ends(state, w["id"], members), key=lambda n: nodes.get(n, {}).get("y", 0))
            out.append({"ws": w["id"], "workspace": w["name"], "name": nodes[tail[0]]["title"] if tail else "Flow",
                        "ends": tail, "ids": sorted(members), "titles": [n["title"] for n in nodes.values()],
                        "steps": sum(1 for n in nodes.values() if n["type"] == "agent")})
    return out


def text_of(board, n: dict) -> str:
    """What a box hands on when nothing flows into it."""
    t = n["type"]
    if t in ("note", "text"):
        return n.get("text") or ""
    if t == "agent":
        return n.get("out") or ""
    if t == "checklist":
        return "\n".join(f"- [{'x' if i.get('done') else ' '}] {i.get('text', '')}" for i in n.get("items") or [])
    if t == "kanban":
        return "\n\n".join(f"{c.get('title', '')}:\n" + "\n".join(f"- {k.get('text', '')}" for k in c.get("cards") or [])
                           for c in n.get("columns") or [])
    if t == "file":
        p = Path(n.get("path") or "")
        try:
            return p.read_text(encoding="utf-8", errors="replace")[:MAX_IN] if p.is_file() else ""
        except OSError:
            return ""
    if t == "web":
        return n.get("url") or ""
    if t == "console":
        c = board.consoles.get(n["id"])
        return c.screen_tail(4000) if c else ""
    return ""


def gather(state: dict, wid: str, nid: str, vals: dict[str, str]) -> str:
    """What flows into a box: one input as it is, several under their boxes' titles."""
    title = {n["id"]: n["title"] for n in state["nodes"]}
    parts = [(title.get(w["from"], "Input"), vals.get(w["from"], "")) for w in wires_of(state, wid) if w["to"] == nid]
    parts = [(t, v) for t, v in parts if v.strip()]
    if len(parts) == 1:
        return parts[0][1][:MAX_IN]
    return "\n\n".join(f"## {t}\n{v[:MAX_IN // max(1, len(parts))]}" for t, v in parts)


def prompt(instructions: str, inp: str) -> str:
    """{input} in the instructions is replaced; otherwise the input follows them."""
    ins = instructions.strip() or "Carry out the request below."
    if "{input}" in ins:
        return ins.replace("{input}", inp)
    return f"{ins}\n\n---\nInput:\n{inp}" if inp.strip() else ins


def passes(expect: str, got: str) -> bool:
    """An example passes when the answer contains the expected phrase (case and spacing ignored);
    with nothing expected, when there is an answer at all."""
    norm = lambda s: re.sub(r"\s+", " ", s or "").strip().lower()       # noqa: E731
    return bool(norm(got)) and norm(expect) in norm(got)


class Flows:
    """Runs flows on the board in the background, one at a time per box."""

    def __init__(self, board, agent=studio.run_agent):
        self.board = board
        self.agent = agent                    # (engine, cwd, prompt) -> reply; swapped in the self-check
        self.busy: set[str] = set()
        self._lock = threading.Lock()

    def _claim(self, ids: list[str]) -> None:
        with self._lock:
            if self.busy & set(ids):
                raise ValueError("That flow is already running.")
            self.busy |= set(ids)

    def _start(self, ids: list[str], work) -> None:
        self._claim(ids)

        def go():
            try:
                work()
            except Exception as e:                       # already on the failed step; said on the board too
                self.board.emit({"type": "toast", "text": f"The flow stopped: {str(e)[:200]}"})
            finally:
                with self._lock:
                    self.busy -= set(ids)
                self.board.emit({"type": "board"})
        threading.Thread(target=go, daemon=True, name="deck-flow").start()

    def run(self, targets: list[str]) -> list[str]:
        """Run these boxes and everything that feeds them; returns the run order."""
        first = self.board.node(targets[0]) if targets else None
        if not first:
            raise ValueError("No such box.")
        steps = order(self.board.state, first["ws"], targets)
        self._start(steps, lambda: self._execute(first["ws"], steps, write=True))
        return steps

    def run_from(self, nid: str) -> list[str]:
        """A box, what feeds it, and everything its output flows on to."""
        n = self.board.node(nid)
        if not n:
            raise ValueError("No such box.")
        return self.run(reaches(self.board.state, n["ws"], nid))

    def run_flow(self, nid: str) -> list[str]:
        """The whole flow a box belongs to - every end, each upstream step once."""
        n = self.board.node(nid)
        if not n:
            raise ValueError("No such box.")
        return self.run(ends(self.board.state, n["ws"], component(self.board.state, n["ws"], nid)))

    def test(self, nid: str) -> int:
        """Run an Agent step's examples through its flow; results land on the step's tests."""
        n = self.board.node(nid)
        if not n or n["type"] != "agent":
            raise ValueError("Tests live on an Agent step.")
        tests = (n.get("tests") or [])[:MAX_TESTS]
        if not tests:
            raise ValueError("Add an example first.")
        st = self.board.state
        steps = order(st, n["ws"], [nid])
        into = {w["to"] for w in wires_of(st, n["ws"])}
        # The example goes where the flow starts: its first note or text box, else straight into this step.
        src = next((s for s in steps if s not in into and self.board.node(s)["type"] in ("note", "text")), None)

        def work():
            self.board.update_node(nid, run={"state": "testing", "at": time.time()})
            self.board.emit({"type": "board"})
            done = []
            for t in tests:
                feed = {src or nid: t.get("input", "")}
                r = {k: t.get(k, "") for k in ("id", "input", "expect")}
                try:
                    vals = self._execute(n["ws"], steps, write=False, feed=feed)
                    r.update(got=vals.get(nid, "")[:4000], ok=passes(t.get("expect", ""), vals.get(nid, "")), error="",
                             steps={s: vals.get(s, "")[:1500] for s in steps if self.board.node(s)["type"] == "agent"})
                except Exception as e:                                       # shown on the example, never swallowed
                    r.update(got="", ok=False, error=str(e)[:400], steps={})
                done.append(r)
                self.board.update_node(nid, tests=done + tests[len(done):])
                self.board.emit({"type": "board"})
            passed = sum(1 for r in done if r["ok"])
            self.board.update_node(nid, run={"state": "tested", "at": time.time(), "passed": passed, "of": len(done)})

        self._start(steps, work)
        return len(tests)

    def _execute(self, wid: str, steps: list[str], write: bool, feed: dict[str, str] | None = None) -> dict[str, str]:
        feed = feed or {}
        st, b = self.board.state, self.board
        cwd = next((w["folder"] for w in st["workspaces"] if w["id"] == wid), ".")
        vals: dict[str, str] = {}
        for nid in steps:
            n = b.node(nid)
            if not n:
                continue
            inp = gather(st, wid, nid, vals)
            if n["type"] == "agent":
                inp = feed.get(nid, inp)
                if write:
                    b.update_node(nid, run={"state": "running", "at": time.time()})
                    b.emit({"type": "board"})
                try:
                    out = self.agent(n.get("engine") or "claude", cwd, prompt(n.get("text") or "", inp)).strip()
                except Exception as e:
                    if write:
                        b.update_node(nid, run={"state": "failed", "at": time.time(), "error": str(e)[:400]})
                    raise RuntimeError(f"{n['title']}: {e}") from e
                vals[nid] = out[:MAX_OUT]
                if write:
                    b.update_node(nid, out=vals[nid], run={"state": "done", "at": time.time()})
                    b.emit({"type": "board"})
            elif nid in feed:
                vals[nid] = feed[nid]
            elif inp.strip() and n["type"] in ("note", "text"):          # a box at the end of a wire: filled with what arrives
                vals[nid] = inp
                if write:
                    b.update_node(nid, text=inp[:MAX_OUT])
                    b.emit({"type": "board"})
            elif inp.strip() and n["type"] == "console":                 # a live agent: told what arrives, works on it itself
                vals[nid] = inp
                c = b.consoles.get(nid)
                if write and c and c.alive:
                    c.send(inp)
            else:
                vals[nid] = text_of(b, n)
        return vals


if __name__ == "__main__":
    class FakeBoard:
        def __init__(self, state):
            self.state, self.consoles, self.events = state, {}, []

        def node(self, nid):
            return next((n for n in self.state["nodes"] if n["id"] == nid), None)

        def update_node(self, nid, **kw):
            self.node(nid).update(kw)

        def emit(self, ev):
            self.events.append(ev)

    def box(i, t, y, **kw):
        return {"id": i, "ws": "w", "type": t, "title": i.upper(), "x": 0, "y": y, **kw}

    state = {"workspaces": [{"id": "w", "name": "Feedback", "folder": ".",
                             "wires": [{"id": "1", "from": "src", "to": "sum"}, {"id": "2", "from": "calls", "to": "sum"},
                                       {"id": "3", "from": "sum", "to": "out"}]}],
             "nodes": [box("src", "note", 0, text="faster checkout please"), box("calls", "checklist", 50, items=[{"text": "slack", "done": True}]),
                       box("sum", "agent", 100, text="Summarise: {input}", engine="claude"), box("out", "note", 200, text="")]}
    b = FakeBoard(state)
    asked = []
    f = Flows(b, agent=lambda eng, cwd, p: asked.append(p) or f"SUMMARY<{p}>")
    assert order(state, "w", ["out"]) == ["src", "calls", "sum", "out"]
    assert reaches(state, "w", "sum") == ["out"] and reaches(state, "w", "out") == ["out"]
    assert feeds(state["workspaces"][0]["wires"], "src", "out") and not feeds(state["workspaces"][0]["wires"], "out", "src")
    f._execute("w", order(state, "w", ["out"]), write=True)
    assert "## SRC\nfaster checkout please" in asked[0] and "- [x] slack" in asked[0] and asked[0].startswith("Summarise: ")
    assert b.node("out")["text"].startswith("SUMMARY<") and b.node("sum")["run"]["state"] == "done"
    assert catalog(state)[0]["name"] == "OUT" and catalog(state)[0]["steps"] == 1
    # a test: the example replaces the first note; nothing written, the step's output kept
    b.node("sum")["tests"] = [{"id": "t1", "input": "slack integration", "expect": "slack integration"},
                              {"id": "t2", "input": "x", "expect": "mobile app"}]
    b.node("out")["text"] = "kept"
    f.test("sum")
    for _ in range(100):
        if not f.busy:
            break
        time.sleep(0.02)
    t1, t2 = b.node("sum")["tests"]
    assert t1["ok"] and not t2["ok"] and "sum" in t1["steps"] and b.node("out")["text"] == "kept"
    assert b.node("sum")["run"]["state"] == "tested" and (b.node("sum")["run"]["passed"], b.node("sum")["run"]["of"]) == (1, 2)
    # a failing step stops the run, marks the step, and says so
    f.agent = lambda *a: (_ for _ in ()).throw(RuntimeError("limit reached"))
    f.run(["out"])
    for _ in range(100):
        if not f.busy:
            break
        time.sleep(0.02)
    assert b.node("sum")["run"]["state"] == "failed" and any(e.get("type") == "toast" for e in b.events)
    # a loop in the wires is refused
    state["workspaces"][0]["wires"].append({"id": "4", "from": "out", "to": "src"})
    try:
        order(state, "w", ["out"])
        raise AssertionError("a circle was run")
    except ValueError:
        pass
    assert passes("", "anything") and not passes("", "  ") and passes("Faster  checkout", "1. faster checkout - 42")
    assert prompt("", "x").endswith("Input:\nx") and prompt("Do {input} now", "it") == "Do it now"
    print("flows: checks pass")
