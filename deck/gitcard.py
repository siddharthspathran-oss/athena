"""Git on the board: a live card for one repo - branches, what's changed, recent
commits - and the few actions you'd take from it: commit everything, pull,
push, fetch, switch branch.

Runs the user's own git (their config, their credential manager) with argument
lists, never a shell; a branch name is only used if git itself listed it.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"}   # never hang on a password prompt


def _git(cwd: str, *args: str, timeout: float = 20) -> tuple[int, str]:
    try:
        p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, env=ENV, creationflags=NO_WINDOW)
        return p.returncode, (p.stdout + p.stderr).strip()
    except FileNotFoundError:
        return 127, "git isn't installed"
    except subprocess.TimeoutExpired:
        return 124, f"git {args[0]} took longer than {int(timeout)}s"


def status(cwd: str) -> dict:
    """Everything the card shows, or {"repo": False} when the folder isn't in a git repo."""
    if not Path(cwd).is_dir():
        return {"repo": False, "error": "That folder doesn't exist."}
    code, top = _git(cwd, "rev-parse", "--show-toplevel")
    if code:
        return {"repo": False, "error": "Not a git repository." if code != 127 else top}
    _, head = _git(cwd, "status", "--porcelain=v1", "--branch")
    lines = head.splitlines()
    first = lines[0][3:] if lines and lines[0].startswith("## ") else ""
    track = re.search(r"\[(.*?)\]", first)
    ahead = re.search(r"ahead (\d+)", track.group(1)) if track else None
    behind = re.search(r"behind (\d+)", track.group(1)) if track else None
    files = [{"xy": ln[:2], "path": ln[3:]} for ln in lines[1:] if len(ln) > 3]
    _, refs = _git(cwd, "for-each-ref", "--format=%(HEAD)|%(refname:short)|%(upstream:short)|%(upstream:track)",
                   "--sort=-committerdate", "refs/heads")
    branches = []
    for ln in refs.splitlines():
        cur, name, up, tr = (ln.split("|") + ["", "", "", ""])[:4]
        branches.append({"name": name, "current": cur == "*", "upstream": up, "track": tr.strip("[]")})
    _, log = _git(cwd, "log", "-n", "8", "--format=%h|%ct|%an|%s")
    commits = [dict(zip(("hash", "time", "author", "subject"), (ln.split("|", 3) + ["", "", "", ""])[:4]))
               for ln in log.splitlines()] if branches else []
    for c in commits:
        c["time"] = int(c["time"] or 0)
    _, remote = _git(cwd, "remote", "get-url", "origin")
    return {"repo": True, "root": top, "branch": next((b["name"] for b in branches if b["current"]), first.split("...")[0]),
            "upstream": first.split("...")[1].split(" ")[0] if "..." in first else "",
            "ahead": int(ahead.group(1)) if ahead else 0, "behind": int(behind.group(1)) if behind else 0,
            "files": files[:200], "changed": len(files), "branches": branches[:30], "commits": commits,
            "remote": remote if remote.startswith(("http", "git@", "ssh")) else "", "at": time.time()}


def action(cwd: str, act: str, arg: str = "") -> dict:
    """commit (arg = message: stages everything first), pull, push, fetch, checkout (arg = branch)."""
    st = status(cwd)
    if not st.get("repo"):
        return {"ok": False, "out": st.get("error", "Not a git repository.")}
    if act == "commit":
        msg = arg.strip()
        if not msg:
            return {"ok": False, "out": "Write a commit message first."}
        code, out = _git(cwd, "add", "-A")
        if not code:
            code, out = _git(cwd, "commit", "-m", msg)
    elif act == "pull":
        code, out = _git(cwd, "pull", "--ff-only", timeout=120)
    elif act == "push":
        code, out = _git(cwd, "push", timeout=120) if st["upstream"] else \
            _git(cwd, "push", "-u", "origin", st["branch"], timeout=120)
    elif act == "fetch":
        code, out = _git(cwd, "fetch", "--prune", timeout=120)
    elif act == "checkout":
        if arg not in {b["name"] for b in st["branches"]}:
            return {"ok": False, "out": f"No branch called {arg!r}."}
        code, out = _git(cwd, "checkout", arg)
    else:
        return {"ok": False, "out": f"Unknown git action {act!r}."}
    return {"ok": code == 0, "out": out[-2000:] or ("Done." if code == 0 else f"git exited with {code}")}


if __name__ == "__main__":
    import tempfile

    d = tempfile.mkdtemp()
    assert status(d) == {"repo": False, "error": "Not a git repository."}
    _git(d, "init", "-b", "main")
    _git(d, "config", "user.email", "deck@test")
    _git(d, "config", "user.name", "Deck")
    Path(d, "a.txt").write_text("hi", encoding="utf-8")
    s = status(d)
    assert s["repo"] and s["changed"] == 1 and s["files"][0]["path"] == "a.txt", s
    assert action(d, "commit", "")["ok"] is False
    r = action(d, "commit", "first")
    assert r["ok"], r
    s = status(d)
    assert s["changed"] == 0 and s["commits"][0]["subject"] == "first" and s["branch"] == "main", s
    assert action(d, "checkout", "nope; rm -rf /")["ok"] is False                # only branches git listed
    print("gitcard: checks pass")
