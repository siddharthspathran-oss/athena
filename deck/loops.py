"""Loops on a console: the Deck types the same message into it again and again -

- every N minutes (when it's resting - never on top of work in progress),
- whenever it has sat idle for N seconds, or
- whenever its output matches a pattern (e.g. "FAILED" -> "fix the failing tests").

A loop with a goal asks the agent to end its reply with GOAL MET once the goal is
reached, and retires itself when it does. Every loop also stops after `max` runs.
Loops live on the console's node ("loops"), so they survive a restart.
"""
from __future__ import annotations

import re
import time
import uuid

MODES = ("every", "idle", "output")
GOAL_WORDS = "GOAL MET"
ASK = f"the words {GOAL_WORDS}"                 # how message() asks for them


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s)


def _bare(s: str) -> str:
    return re.sub(r"\s+", "", s)


def without(text: str, msg: str) -> str:
    """`text` minus every copy of `msg` in it, however it was drawn: spaces as cursor moves or wrapped
    lines ("fixtheFAILEDtests"), an input box's side borders. An exact match let a loop on output
    fire on its own redrawn message (24 Oct 2026)."""
    chars = _bare(msg)
    return re.sub(r"[\s│║┃]*".join(map(re.escape, chars)), "", text) if chars else text


def goal_met(reply: str) -> bool:
    """GOAL MET in what the agent printed. Spaces don't count - ConPTY draws many as cursor moves, so a
    redrawn message reads "...withthewordsGOALMET.)" - and the loop's own request isn't an answer: a
    Codex loop retired on its redrawn message while Codex said the goal was not met (24 Oct 2026)."""
    return _bare(GOAL_WORDS) in _bare(reply).replace(_bare(ASK), "")


def new(spec: dict) -> dict:
    """A loop from what the user filled in; raises ValueError on anything unusable."""
    mode = str(spec.get("mode") or "every")
    text = str(spec.get("text") or "").strip()
    if mode not in MODES:
        raise ValueError(f"A loop runs every N minutes, on idle or on output - not {mode!r}.")
    if not text:
        raise ValueError("Write the message the loop should send.")
    loop = {"id": uuid.uuid4().hex[:6], "mode": mode, "text": text[:4000], "goal": str(spec.get("goal") or "").strip()[:500],
            "every": max(1, min(1440, int(spec.get("every") or 10))), "idle": max(10, min(86400, int(spec.get("idle") or 60))),
            "match": str(spec.get("match") or "").strip()[:200], "max": max(1, min(500, int(spec.get("max") or 20))),
            "runs": 0, "on": True, "last": 0.0, "note": "", "created": time.time()}
    if mode == "output":
        if not loop["match"]:
            raise ValueError("Say what output should set the loop off.")
        try:
            re.compile(loop["match"], re.I)
        except re.error as e:
            raise ValueError(f"That pattern doesn't work: {e}") from None
    return loop


def message(loop: dict) -> str:
    if not loop["goal"]:
        return loop["text"]
    return f"{loop['text']}\n\n(Loop goal: {loop['goal']}. Once it is met, end your reply with {ASK}.)"


def describe(loop: dict) -> str:
    when = {"every": f"every {loop['every']} min", "idle": f"after {loop['idle']} s idle",
            "output": f"when output matches /{loop['match']}/"}[loop["mode"]]
    return f"{when}, {loop['runs']}/{loop['max']} runs" + (f", until: {loop['goal']}" if loop["goal"] else "")


class Runner:
    """Checked once a second by the board. What it remembers between ticks (where each loop last
    looked in the output, what it last sent) is in memory; the loops themselves are on the nodes."""

    def __init__(self):
        self.seen: dict[tuple, int] = {}         # (console, loop) -> output mark already scanned
        self.sent: dict[tuple, dict] = {}        # (console, loop) -> {"worked": output mark once it took the last run}

    def tick(self, consoles: dict, now: float | None = None) -> bool:
        """Fire what's due; True when any loop changed (so the board saves)."""
        now = now or time.time()
        changed = False
        for cid, c in list(consoles.items()):
            for lp in c.node.get("loops") or []:
                if lp.get("on"):
                    changed |= self._one(cid, c, lp, now)
        return changed

    def _one(self, cid: str, c, lp: dict, now: float) -> bool:
        key = (cid, lp["id"])
        sent = self.sent.get(key)
        if sent and sent["worked"] is None and c.state == "working":
            sent["worked"] = c.total              # it took the message: its reply is what comes after this
        if not c.alive or c.state != "resting":
            return False
        if sent and sent["worked"] is not None:  # it worked on the last run and stopped: was the goal met?
            self.sent.pop(key)                    # (never seen working = the message sat unsent: nothing to judge)
            if lp["goal"] and goal_met(c.output_since(sent["worked"])):
                lp.update(on=False, note="goal met")
                return True
        if lp["runs"] >= lp["max"]:
            lp.update(on=False, note=f"stopped after {lp['max']} runs")
            return True
        if lp["mode"] == "every":
            due = now - lp["last"] >= lp["every"] * 60
        elif lp["mode"] == "idle":
            due = now - c.since >= lp["idle"] and lp["last"] < c.since
        else:
            mark = self.seen.get(key, c.total)
            fresh = c.output_since(mark)
            self.seen[key] = c.total
            mine = message(lp) if lp["runs"] else ""
            due = bool(fresh) and bool(re.search(lp["match"], _norm(without(fresh, mine)), re.I))
        if not due:
            return False
        self.sent[key] = {"worked": None}
        self.seen[key] = c.total
        c.send(message(lp))
        lp.update(runs=lp["runs"] + 1, last=now, note="")
        return True


if __name__ == "__main__":
    class Fake:
        def __init__(self):
            self.node, self.state, self.since, self.total, self.out, self.got = {"loops": []}, "resting", 0.0, 0, "", []
            self.alive = True

        def output_since(self, mark):
            return self.out[mark:]

        def send(self, text):
            self.got.append(text)
            self.print(text)                      # a TUI echoes what you type

        def print(self, s):
            self.out += s
            self.total = len(self.out)

    for bad in ({"mode": "sometimes", "text": "x"}, {"text": " "}, {"mode": "output", "text": "x"}, {"mode": "output", "text": "x", "match": "("}):
        try:
            new(bad)
            raise AssertionError(bad)
        except ValueError:
            pass
    assert not goal_met("Once it is met, end your reply with the words GOAL MET.)") and not goal_met("withthewordsGOALMET.)")
    assert not goal_met("The goal is not met yet") and goal_met("All green.\n\nGOAL MET") and goal_met("Allgreen.GOALMET")
    r, c = Runner(), Fake()
    lp = new({"mode": "every", "every": 5, "text": "run the tests", "goal": "all tests pass", "max": 3})
    c.node["loops"].append(lp)
    assert r.tick({"a": c}, now=1000) and c.got == [message(lp)] and lp["runs"] == 1     # due at once
    redrawn = "›" + _bare(message(lp)).replace("GOALMET", "GOAL MET")                    # ConPTY keeps some spaces, drops others
    c.print(redrawn)                                                                     # sat unsent in Codex's box, redrawn
    assert not r.tick({"a": c}, now=1100) and lp["on"] and len(c.got) == 1              # never worked: not judged (24 Oct: "goal met")
    c.state = "working"
    assert not r.tick({"a": c}, now=1150)                                               # it took the message
    c.print(redrawn + "• The goal is not met yet: 2 failed")                            # its message in the history, then its answer
    c.state = "resting"
    assert r.tick({"a": c}, now=1400) and lp["on"] and len(c.got) == 2                  # not met: the next run goes out
    c.state = "working"
    assert not r.tick({"a": c}, now=1500)
    c.print(" all green now. GOAL MET")
    c.state = "resting"
    assert r.tick({"a": c}, now=1800) and lp["on"] is False and lp["note"] == "goal met"
    c2 = Fake()
    c2.state = "working"
    idle = new({"mode": "idle", "idle": 30, "text": "keep going"})
    c2.node["loops"].append(idle)
    assert not r.tick({"b": c2}, now=100)                                                # never on top of work
    c2.state, c2.since = "resting", 100
    assert not r.tick({"b": c2}, now=120) and r.tick({"b": c2}, now=131) and c2.got == ["keep going"]
    assert not r.tick({"b": c2}, now=500)                                                # once per idle spell
    c3 = Fake()
    out = new({"mode": "output", "match": "FAILED", "text": "fix the FAILED tests", "max": 2})
    c3.node["loops"].append(out)
    assert not r.tick({"c": c3}, now=1)                                                  # first look: nothing new
    c3.print("3 tests FAILED")
    assert r.tick({"c": c3}, now=2) and len(c3.got) == 1
    assert not r.tick({"c": c3}, now=3)                                                  # my own echo says FAILED: ignored
    c3.print("›fixthe FAILED\n  tests")                                                 # redrawn: spaces dropped, wrapped
    assert not r.tick({"c": c3}, now=3.5)                                                # still my own message: ignored
    assert without("│ > fix the│\n│ FAILED tests │ 1 FAILED", message(out)) == "│ >  │ 1 FAILED"   # boxed echo out, real output kept
    c3.print("›fixtheFAILEDtests still 1 FAILED")
    assert r.tick({"c": c3}, now=4) and len(c3.got) == 2
    c3.print("FAILED again")
    assert r.tick({"c": c3}, now=5) and out["on"] is False and "2 runs" in out["note"]   # max runs
    print("loops: checks pass")
