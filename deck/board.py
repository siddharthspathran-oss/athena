"""The board: workspaces (one per project folder) and what sits on them -
consoles, notes, text, checklists, kanbans, images, git cards, pinned files,
diagrams, live web tiles and freehand ink - saved as JSON on every change, so
quitting loses nothing. On launch every console comes back where it was and
picks its conversation up again (Claude --resume, agy --conversation, codex resume).
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path

from . import loops
from .consoles import DATA, KINDS, Console, installed

AGENTS = ("claude", "codex", "gemini")                  # consoles that take a first instruction

FILE = DATA / "board.json"
CONSOLE_W, CONSOLE_H = 760, 470
SIZES = {"console": (CONSOLE_W, CONSOLE_H), "note": (300, 260), "web": (640, 460), "text": (380, 90), "checklist": (320, 320),
         "kanban": (880, 480), "image": (480, 360), "git": (420, 460), "file": (640, 560), "diagram": (760, 520),
         "agent": (420, 380)}
TITLES = {"note": "Note", "web": "Preview", "text": "Text", "checklist": "Checklist", "kanban": "Kanban", "image": "Image",
          "git": "Git", "file": "File", "diagram": "Diagram", "agent": "Agent step"}
# What a PATCH may change, and what a new node may carry besides its place and size.
# An Agent step (flows.py): text = its instructions, engine = claude|codex, out = its last reply,
# run = {state, at, error, passed, of}, tests = [{id, input, expect, got, ok, steps, error}].
FIELDS = ("x", "y", "w", "h", "title", "text", "url", "ws", "items", "columns", "src", "path", "data", "team", "role",
          "loops", "color", "size", "brief", "engine", "out", "run", "tests")
WIRES_MAX = 400                                                   # per workspace
KANBAN = [{"id": "todo", "title": "To do", "cards": []}, {"id": "doing", "title": "Doing", "cards": []},
          {"id": "done", "title": "Done", "cards": []}]
INK_MAX = 4000                                                    # strokes per workspace
COSTS = Path.home() / ".claude" / "metrics" / "costs.jsonl"     # ECC's cost log: a running total per Claude session


class Board:
    def __init__(self, emit):
        self.emit = emit                                  # thread-safe: (event dict) -> None
        self._lock = threading.RLock()
        self.consoles: dict[str, Console] = {}
        self.costs: dict[str, float] = {}
        self.runner = loops.Runner()
        try:
            self.state = json.loads(FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.state = {"workspaces": [], "nodes": [], "active": ""}
        if not self.state["workspaces"]:
            self.add_workspace("Home", r"C:\claude code")

    # ---- storage ----
    def save(self) -> None:
        with self._lock:
            DATA.mkdir(parents=True, exist_ok=True)
            tmp = FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.state, indent=1, ensure_ascii=False), encoding="utf-8")
            tmp.replace(FILE)

    def ws(self, wid: str) -> dict | None:
        return next((w for w in self.state["workspaces"] if w["id"] == wid), None)

    def node(self, nid: str) -> dict | None:
        return next((n for n in self.state["nodes"] if n["id"] == nid), None)

    # ---- workspaces ----
    def add_workspace(self, name: str, folder: str) -> dict:
        w = {"id": uuid.uuid4().hex[:8], "name": name.strip() or Path(folder).name or "Workspace",
             "folder": folder, "view": {"x": 40, "y": 30, "z": 0.85}}
        with self._lock:
            self.state["workspaces"].append(w)
            self.state["active"] = w["id"]
            self.save()
        self.emit({"type": "board"})
        return w

    def update_workspace(self, wid: str, **kw) -> dict | None:
        with self._lock:
            w = self.ws(wid)
            if w:
                w.update({k: v for k, v in kw.items() if k in ("name", "folder", "view")})
                if kw.get("activate"):
                    self.state["active"] = wid
                self.save()
        return w

    def remove_workspace(self, wid: str) -> None:
        for n in [n for n in self.state["nodes"] if n["ws"] == wid]:
            self.remove_node(n["id"], quiet=True)
        with self._lock:
            self.state["workspaces"] = [w for w in self.state["workspaces"] if w["id"] != wid]
            if not self.state["workspaces"]:
                self.state["workspaces"].append({"id": uuid.uuid4().hex[:8], "name": "Home", "folder": r"C:\claude code",
                                                 "view": {"x": 40, "y": 30, "z": 0.85}})
            if self.state["active"] == wid:
                self.state["active"] = self.state["workspaces"][0]["id"]
            self.save()
        self.emit({"type": "board"})

    # ---- nodes ----
    def _free_spot(self, wid: str, w: int, h: int) -> tuple[int, int]:
        taken = [(n["x"], n["y"], n["w"], n["h"]) for n in self.state["nodes"] if n["ws"] == wid]
        for row in range(40):
            for col in range(3):
                x, y = 40 + col * (CONSOLE_W + 36), 40 + row * (CONSOLE_H + 36)
                if not any(x < tx + tw and tx < x + w and y < ty + th and ty < y + h for tx, ty, tw, th in taken):
                    return x, y
        return 40, 40

    def add_node(self, wid: str, type_: str, *, kind: str = "shell", title: str = "", cwd: str = "", x=None, y=None,
                 w=None, h=None, cmd: str = "", prompt: str = "", model: str = "", profile: str = "", **extra) -> dict:
        ws = self.ws(wid) or self.ws(self.state["active"]) or self.state["workspaces"][0]
        size = SIZES[type_]
        w, h = int(w or size[0]), int(h or size[1])
        if x is None or y is None:
            x, y = self._free_spot(ws["id"], w, h)
        n = {"id": uuid.uuid4().hex[:8], "ws": ws["id"], "type": type_, "x": int(x), "y": int(y), "w": w, "h": h,
             "created": time.time()}
        if type_ == "console":
            kind = kind if kind in KINDS else "shell"
            same = sum(1 for m in self.state["nodes"] if m.get("kind") == kind and m["ws"] == ws["id"]) + 1
            prof = next((p for p in self.state.get("profiles", []) if p["id"] == profile or p["dir"] == profile), None)
            profile = prof["dir"] if prof and prof["cli"] == kind else ""
            label = f"{KINDS[kind]['label']} {same}" + (f" ({prof['name']})" if profile else "")
            n.update(kind=kind, cwd=cwd or ws["folder"], title=title or label,
                     cmd=cmd, prompt=prompt, model=model, profile=profile,
                     team=str(extra.get("team") or ""), role=str(extra.get("role") or ""), loops=[])
        else:
            n.update(title=title or TITLES[type_], **{k: v for k, v in extra.items() if k in FIELDS})
            if type_ == "web":
                n.setdefault("url", "http://localhost:3000")
            elif type_ == "kanban":
                n.setdefault("columns", json.loads(json.dumps(KANBAN)))
            elif type_ == "checklist":
                n.setdefault("items", [])
            elif type_ in ("git", "file"):
                n.setdefault("path", ws["folder"])
            elif type_ == "agent":
                n.setdefault("engine", "claude")
                n.setdefault("text", "")
        with self._lock:
            self.state["nodes"].append(n)
            self.save()
        if type_ == "console":
            self.start_console(n, resume=False)
        self.emit({"type": "board"})
        return n

    def update_node(self, nid: str, **kw) -> dict | None:
        with self._lock:
            n = self.node(nid)
            if n:
                n.update({k: (int(v) if k in ("x", "y", "w", "h") else v) for k, v in kw.items() if k in FIELDS})
                self.save()
        return n

    # ---- ink: freehand strokes on a workspace, in board coordinates ----
    def add_stroke(self, wid: str, stroke: dict) -> dict | None:
        pts = [[round(float(p[0]), 1), round(float(p[1]), 1)] for p in (stroke.get("pts") or [])[:4000] if len(p) >= 2]
        if len(pts) < 2:
            return None
        s = {"id": uuid.uuid4().hex[:8], "pts": pts, "c": str(stroke.get("c") or "#ffffff")[:32],
             "s": max(1.0, min(40.0, float(stroke.get("s") or 3)))}
        with self._lock:
            w = self.ws(wid)
            if not w:
                return None
            w["ink"] = (w.get("ink") or [])[-(INK_MAX - 1):] + [s]
            self.save()
        self.emit({"type": "ink", "ws": wid, "add": s})
        return s

    def remove_stroke(self, wid: str, sid: str = "") -> None:
        """One stroke, or every stroke on the workspace when sid is empty."""
        with self._lock:
            w = self.ws(wid)
            if not w:
                return
            w["ink"] = [s for s in (w.get("ink") or []) if sid and s["id"] != sid]
            self.save()
        self.emit({"type": "ink", "ws": wid, "del": sid or "*"})

    # ---- wires: one box's text flowing into another (flows.py) ----
    def add_wire(self, frm: str, to: str) -> dict:
        from .flows import feeds
        a, b = self.node(frm), self.node(to)
        if not a or not b or a["ws"] != b["ws"]:
            raise ValueError("Wire two boxes on the same workspace.")
        if frm == to:
            raise ValueError("A box can't feed itself.")
        with self._lock:
            w = self.ws(a["ws"])
            wires = w.get("wires") or []
            if any(x["from"] == frm and x["to"] == to for x in wires):
                raise ValueError("Those two are already wired.")
            if feeds(wires, to, frm):
                raise ValueError("That wire would go round in a circle.")
            if len(wires) >= WIRES_MAX:
                raise ValueError("That's as many wires as one workspace takes.")
            wire = {"id": uuid.uuid4().hex[:8], "from": frm, "to": to}
            w["wires"] = wires + [wire]
            self.save()
        self.emit({"type": "board"})
        return wire

    def remove_wire(self, wid: str, wire_id: str) -> None:
        with self._lock:
            w = self.ws(wid)
            if w:
                w["wires"] = [x for x in w.get("wires") or [] if x["id"] != wire_id]
                self.save()
        self.emit({"type": "board"})

    def remove_node(self, nid: str, quiet: bool = False) -> None:
        c = self.consoles.pop(nid, None)
        if c:
            c.kill()
        with self._lock:
            self.state["nodes"] = [n for n in self.state["nodes"] if n["id"] != nid]
            for w in self.state["workspaces"]:                     # its wires go with it
                if w.get("wires"):
                    w["wires"] = [x for x in w["wires"] if nid not in (x["from"], x["to"])]
            self.save()
        if not quiet:
            self.emit({"type": "board"})

    # ---- consoles ----
    def start_console(self, n: dict, resume: bool = True) -> Console:
        old = self.consoles.get(n["id"])
        if old:
            old.kill()
        c = Console(n, self._on_status, lambda _c: self.save())
        self.consoles[n["id"]] = c
        try:
            c.start(resume=resume)
        except Exception as e:                      # CLI missing, bad folder: show it on the console
            c.detail = f"couldn't start: {e}"
            c.state = "exited"
            self._on_status(c)
        self.save()                                 # a new Claude console has its session id now
        return c

    def restore(self) -> None:
        for n in [n for n in self.state["nodes"] if n["type"] == "console"]:
            self.start_console(n, resume=True)

    def _on_status(self, c: Console) -> None:
        self.emit({"type": "status", **c.info()})

    def hook(self, cid: str, event: str, message: str, session: str) -> None:
        c = self.consoles.get(cid)
        if not c:
            return
        if session and c.node.get("kind") == "claude" and session != c.node.get("session"):
            c.node["session"] = session             # /clear starts a new session: resume that one next time
            self.save()
        c.hook(event, message)

    def tick(self) -> None:
        for c in list(self.consoles.values()):
            c.tick()
        if self.runner.tick(self.consoles):
            self.save()
            self.emit({"type": "board"})

    # ---- loops on a console ----
    def add_loop(self, nid: str, spec: dict) -> dict:
        n = self.node(nid)
        if not n or n["type"] != "console":
            raise ValueError("No such console.")
        lp = loops.new(spec)
        with self._lock:
            n.setdefault("loops", []).append(lp)
            self.save()
        self.emit({"type": "board"})
        return lp

    def set_loop(self, nid: str, lid: str, on: bool | None = None, remove: bool = False) -> bool:
        n = self.node(nid)
        lp = next((x for x in (n or {}).get("loops") or [] if x["id"] == lid), None)
        if not lp:
            return False
        with self._lock:
            if remove:
                n["loops"] = [x for x in n["loops"] if x["id"] != lid]
            elif on is not None:
                lp.update(on=bool(on), note="" if on else "paused")
                if on and lp["runs"] >= lp["max"]:
                    lp["runs"] = 0                                   # switched back on after its limit: a fresh allowance
            self.save()
        self.emit({"type": "board"})
        return True

    def read_costs(self) -> None:
        """Each Claude console's spend so far (API-price estimate) from ECC's log."""
        sessions = {n.get("session") for n in self.state["nodes"] if n.get("kind") == "claude"}
        costs: dict[str, float] = {}
        try:
            for line in COSTS.read_text(encoding="utf-8").splitlines():
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("session_id") in sessions:
                    costs[r["session_id"]] = float(r.get("estimated_cost_usd") or 0)
        except OSError:
            return
        if costs != self.costs:
            self.costs = costs
            self.emit({"type": "costs", "costs": self.by_node()})

    def by_node(self) -> dict[str, float]:
        return {n["id"]: round(self.costs[n["session"]], 2) for n in self.state["nodes"] if n.get("session") in self.costs}

    def composer(self, wid: str, slots: list[dict], cwd: str = "", prompt: str = "", origin=None, team: str = "") -> list[dict]:
        """Launch a whole team at once, laid out in a grid from origin. Each slot may carry a role;
        an agent with a role starts on the shared instruction told what its part is."""
        slots = [s for s in slots if s.get("kind") in KINDS][:12]
        cols = 2 if len(slots) <= 4 else 3
        ox, oy = origin or self._free_spot(wid, CONSOLE_W, CONSOLE_H)
        team = team.strip()[:40]
        made = []
        for i, s in enumerate(slots):
            x, y = ox + (i % cols) * (CONSOLE_W + 32), oy + (i // cols) * (CONSOLE_H + 32)
            role = str(s.get("role") or "").strip()[:40]
            first = prompt.strip() if s["kind"] in AGENTS else ""
            if first and role:
                first = f"You're the {role}{f' on team {team}' if team else ''}. {first}"
            made.append(self.add_node(wid, "console", kind=s["kind"], title=s.get("title", ""), cwd=cwd, x=x, y=y,
                                      prompt=first, team=team, role=role, profile=str(s.get("profile") or "")))
        return made

    def snapshot(self) -> dict:
        with self._lock:
            st = json.loads(json.dumps(self.state))
        return {**st, "status": {cid: c.info() for cid, c in self.consoles.items()}, "costs": self.by_node(),
                "kinds": {k: {"label": v["label"], "extra": bool(v.get("extra")), "installed": installed(k) if v.get("extra") else True}
                          for k, v in KINDS.items()}}

    # ---- account profiles: a second sign-in for Claude Code or Codex, side by side ----
    def add_profile(self, cli: str, name: str) -> dict:
        name = name.strip()[:30]
        if cli not in ("claude", "codex") or not name:
            raise ValueError("A profile is a name for a second Claude Code or Codex account.")
        slug = "".join(ch if ch.isalnum() else "-" for ch in name.lower()).strip("-") or "profile"
        p = {"id": uuid.uuid4().hex[:6], "cli": cli, "name": name, "dir": str(DATA / "profiles" / f"{cli}-{slug}")}
        with self._lock:
            if any(x["dir"] == p["dir"] for x in self.state.get("profiles", [])):
                raise ValueError("There's already a profile with that name.")
            self.state.setdefault("profiles", []).append(p)
            self.save()
        self.emit({"type": "board"})
        return p

    def remove_profile(self, pid: str) -> None:
        """Forget the profile; its sign-in folder stays on disk (deck/data/profiles) until the user deletes it."""
        with self._lock:
            self.state["profiles"] = [p for p in self.state.get("profiles", []) if p["id"] != pid]
            self.save()
        self.emit({"type": "board"})

    # ---- project memory: notes that reach a console only when the user pushes them ----
    def set_memory(self, scope: str, text: str) -> None:
        if scope != "global" and not self.ws(scope):
            raise ValueError("No such workspace.")
        with self._lock:
            self.state.setdefault("memory", {})[scope] = text[:100_000]
            self.save()

    def shutdown(self) -> None:
        self.save()
        for c in list(self.consoles.values()):
            c.kill()
