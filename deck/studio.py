"""The studios' server side.

Files: a workspace folder's tree, a file's text, and writing Markdown/text notes -
always inside a folder that is (or is under) one of the board's workspaces.

Jobs: an agent reads the repo headless and writes something - a document
(Markdown) or a diagram (nodes and edges as JSON). Claude runs with read-only
tools (Read, Glob, Grep); Codex runs in its read-only sandbox. Both use the
user's own sign-in and limits.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

from .consoles import clean_env, npm_cli

SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv", "env", "dist", "build", ".next", ".deck", ".idea",
        ".vscode", "target", ".cache", ".pytest_cache", ".mypy_cache", "coverage", ".turbo"}
TEXT_MAX = 1_000_000
WRITABLE = {".md", ".markdown", ".txt"}
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
JOB_TIMEOUT = 900


class Denied(Exception):
    pass


def resolve(roots: list[str], root: str, rel: str = "") -> tuple[Path, Path]:
    """(root, path) when root is a workspace folder or under one and path stays inside root."""
    r = Path(root or ".").resolve()
    allowed = [Path(x).resolve() for x in roots if x]
    if not any(r == a or a in r.parents for a in allowed):
        raise Denied("That folder isn't one of the board's workspaces.")
    p = (r / (rel or "")).resolve()
    if p != r and r not in p.parents:
        raise Denied("That path leaves the workspace folder.")
    return r, p


def tree(roots: list[str], root: str, rel: str = "") -> list[dict]:
    r, p = resolve(roots, root, rel)
    if not p.is_dir():
        raise Denied("Not a folder.")
    out = []
    for e in p.iterdir():
        if e.name in SKIP:
            continue
        try:
            st = e.stat()
        except OSError:
            continue
        out.append({"name": e.name, "path": e.relative_to(r).as_posix(), "dir": e.is_dir(), "size": st.st_size, "mtime": st.st_mtime})
    return sorted(out, key=lambda x: (not x["dir"], x["name"].lower()))[:2000]


def read(roots: list[str], root: str, rel: str) -> dict:
    _, p = resolve(roots, root, rel)
    if not p.is_file():
        raise Denied("No such file.")
    st = p.stat()
    with p.open("rb") as f:
        head = f.read(TEXT_MAX + 1)
    if b"\x00" in head[:8000]:
        return {"binary": True, "size": st.st_size, "mtime": st.st_mtime, "text": ""}
    return {"binary": False, "size": st.st_size, "mtime": st.st_mtime, "truncated": len(head) > TEXT_MAX,
            "text": head[:TEXT_MAX].decode("utf-8", errors="replace")}


def write(roots: list[str], root: str, rel: str, text: str) -> dict:
    _, p = resolve(roots, root, rel)
    if p.suffix.lower() not in WRITABLE:
        raise Denied("The studios only write Markdown and text files.")
    if len(text) > TEXT_MAX:
        raise Denied("That note is too long.")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return {"ok": True, "mtime": p.stat().st_mtime}


LINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]|\]\(([^)\s]+\.md)(?:#[^)]*)?\)", re.I)


def notes(roots: list[str], root: str) -> list[dict]:
    """Every Markdown note under root with the notes it links to ([[wiki]] or [text](file.md))."""
    r, _ = resolve(roots, root)
    found = []
    for dirpath, dirs, files in os.walk(r):
        dirs[:] = [d for d in dirs if d not in SKIP and not d.startswith(".")]
        found += [Path(dirpath) / f for f in files if f.lower().endswith((".md", ".markdown"))]
        if len(found) > 3000:
            break
    texts: dict[Path, str] = {}
    for p in found[:3000]:
        try:
            texts[p] = p.read_text(encoding="utf-8", errors="replace")[:200_000]
        except OSError:
            continue
    titles = {p: next((ln.lstrip("# ").strip() for ln in t.splitlines() if ln.startswith("# ")), p.stem) for p, t in texts.items()}
    by_name = {t.lower(): p.relative_to(r).as_posix() for p, t in titles.items()}      # [[Title]] ...
    by_name.update({p.stem.lower(): p.relative_to(r).as_posix() for p in texts})       # ... but a file name wins, as in Obsidian
    out = []
    for p, text in texts.items():
        rel = p.relative_to(r).as_posix()
        links = set()
        for wiki, md in LINK.findall(text):
            if wiki:
                hit = by_name.get(Path(wiki.strip()).stem.lower())
            else:
                hit = (p.parent / md).resolve()
                hit = hit.relative_to(r).as_posix() if hit.is_file() and r in hit.parents else None
            if hit and hit != rel:
                links.add(hit)
        out.append({"path": rel, "title": titles[p][:120], "links": sorted(links), "mtime": p.stat().st_mtime})
    return out


# ---------- headless agent jobs ----------
DOCS = {
    "readme": "Write a README.md for this repository: what it is, why it exists, how to install, configure and run it, and how it's laid out.",
    "architecture": "Write an architecture overview of this repository: the main components, how they talk to each other, the data they keep, and the key design decisions.",
    "api": "Write an API reference for this repository's public interface - endpoints, commands or exported functions - with parameters, return values and short examples.",
    "onboarding": "Write an onboarding guide for a developer new to this repository: how to set it up, where things live, how to make a first change, test it and ship it.",
}
DIAGRAMS = {
    "architecture": "the system's architecture: its main components and services and how they depend on or call each other",
    "modules": "the main source modules and which import or use which",
    "dataflow": "how data flows through the system, from inputs to storage to outputs",
    "flow": "the main user-facing flow, step by step, from the first action to the result",
}
DOC_TAIL = ("\n\nRead the repository first (only read - don't change anything). Reply with ONLY the Markdown document, "
            "starting with a # title - no preamble and no closing remarks.")
DIAGRAM_TAIL = ('\n\nRead the repository first (only read - change nothing). Then reply with ONLY one JSON object, no prose and '
                'no code fence, shaped like: {"title": "...", "nodes": [{"id": "a", "label": "Short name", "group": "ui"}], '
                '"edges": [{"from": "a", "to": "b", "label": "calls"}]}. Use 4 to 24 nodes; group is one of ui, service, '
                'data, external, other; labels under 40 characters; edge labels optional and short.')


def doc_prompt(kind: str, custom: str = "") -> str:
    base = custom.strip() if kind == "custom" else DOCS.get(kind, "")
    if not base:
        raise ValueError("Pick a document type, or describe the document you want.")
    return base + DOC_TAIL


def diagram_prompt(kind: str, custom: str = "") -> str:
    what = custom.strip() if kind == "custom" else DIAGRAMS.get(kind, "")
    if not what:
        raise ValueError("Pick a diagram type, or describe the diagram you want.")
    return f"Make a diagram of {what}." + DIAGRAM_TAIL


def clean_markdown(out: str) -> str:
    s = out.strip()
    m = re.fullmatch(r"```(?:markdown|md)?\s*\n(.*)\n```", s, re.S)
    s = m.group(1).strip() if m else s
    i = s.find("# ")
    return s[i:] if 0 < i < 400 and not s.startswith("#") else s


def parse_diagram(out: str) -> dict:
    """The agent's JSON, checked and trimmed: ids unique, edges only between known nodes."""
    a, b = out.find("{"), out.rfind("}")
    if a < 0 or b <= a:
        raise ValueError("The agent didn't reply with a diagram.")
    d = json.loads(out[a:b + 1])
    nodes, seen = [], set()
    for n in d.get("nodes", [])[:40]:
        nid = str(n.get("id") or "").strip()[:40]
        if nid and nid not in seen:
            seen.add(nid)
            group = str(n.get("group") or "other").lower()
            nodes.append({"id": nid, "label": str(n.get("label") or nid)[:60],
                          "group": group if group in ("ui", "service", "data", "external", "other") else "other"})
    edges = [{"from": str(e.get("from")), "to": str(e.get("to")), "label": str(e.get("label") or "")[:30]}
             for e in d.get("edges", [])[:80] if str(e.get("from")) in seen and str(e.get("to")) in seen and e.get("from") != e.get("to")]
    if len(nodes) < 2:
        raise ValueError("The diagram came back empty.")
    return {"title": str(d.get("title") or "Diagram")[:80], "nodes": nodes, "edges": edges}


class Jobs:
    def __init__(self, emit):
        self.emit = emit
        self.jobs: dict[str, dict] = {}

    def start(self, kind: str, agent: str, cwd: str, prompt: str) -> dict:
        if agent not in ("claude", "codex"):
            raise ValueError("Studios run on Claude or Codex.")
        if agent == "claude" and not shutil.which("claude"):
            raise ValueError("Claude Code isn't installed.")
        if agent == "codex" and not shutil.which("codex"):
            raise ValueError("Codex isn't installed.")
        job = {"id": uuid.uuid4().hex[:8], "kind": kind, "agent": agent, "cwd": cwd, "status": "running",
               "started": time.time(), "ended": 0, "out": "", "error": ""}
        self.jobs[job["id"]] = job
        threading.Thread(target=self._run, args=(job, prompt), daemon=True, name=f"deck-job-{job['id']}").start()
        return job

    def _run(self, job: dict, prompt: str) -> None:
        try:
            raw = run_agent(job["agent"], job["cwd"], prompt)
            job["out"] = clean_markdown(raw) if job["kind"] == "doc" else json.dumps(parse_diagram(raw))
            job["status"] = "done"
        except Exception as e:                                   # shown on the studio, never swallowed
            job.update(status="failed", error=str(e)[:600])
        job["ended"] = time.time()
        self.emit({"type": "job", "id": job["id"], "status": job["status"]})


def run_agent(agent: str, cwd: str, prompt: str) -> str:
    env = {**clean_env(), "NO_COLOR": "1"}
    if agent == "claude":
        cmd = [shutil.which("claude") or "claude", "-p", "--output-format", "text", "--no-session-persistence",
               "--allowedTools", "Read", "Glob", "Grep"]
        p = subprocess.run(cmd, input=prompt, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=JOB_TIMEOUT, env=env, creationflags=NO_WINDOW)
        if p.returncode or not p.stdout.strip():
            raise RuntimeError((p.stderr or p.stdout or f"claude exited with {p.returncode}").strip()[-400:])
        return p.stdout
    with tempfile.TemporaryDirectory() as tmp:
        last = Path(tmp) / "last.txt"
        cmd = npm_cli("@openai/codex", "codex") + ["exec", "--skip-git-repo-check", "-s", "read-only", "-o", str(last), "-"]
        p = subprocess.run(cmd, input=prompt, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=JOB_TIMEOUT, env=env, creationflags=NO_WINDOW)
        out = last.read_text(encoding="utf-8", errors="replace") if last.exists() else ""
        if not out.strip():
            raise RuntimeError((p.stderr or p.stdout or f"codex exited with {p.returncode}").strip()[-400:])
        return out


if __name__ == "__main__":
    d = Path(tempfile.mkdtemp())
    (d / "docs").mkdir()
    (d / "node_modules").mkdir()
    (d / "a.md").write_text("# Alpha\nSee [[beta]] and [gamma](docs/gamma.md).", encoding="utf-8")
    (d / "beta.md").write_text("# Beta\nBack to [[Alpha|the start]].", encoding="utf-8")
    (d / "docs" / "gamma.md").write_text("no title, links [[nowhere]]", encoding="utf-8")
    (d / "bin.dat").write_bytes(b"\x00\x01\x02")
    roots = [str(d)]
    assert [e["name"] for e in tree(roots, str(d))] == ["docs", "a.md", "beta.md", "bin.dat"]   # dirs first, junk skipped
    assert read(roots, str(d), "bin.dat")["binary"] and read(roots, str(d), "a.md")["text"].startswith("# Alpha")
    for bad in (lambda: read(roots, str(d), "../outside.txt"), lambda: tree(roots, tempfile.gettempdir()),
                lambda: write(roots, str(d), "evil.py", "x"), lambda: read(roots, str(d), "missing.md")):
        try:
            bad()
            raise AssertionError("not denied")
        except Denied:
            pass
    write(roots, str(d), "new/note.md", "# New")
    g = {n["path"]: n for n in notes(roots, str(d))}
    assert g["a.md"]["links"] == ["beta.md", "docs/gamma.md"] and g["beta.md"]["links"] == ["a.md"] and g["docs/gamma.md"]["links"] == []
    assert g["a.md"]["title"] == "Alpha" and g["docs/gamma.md"]["title"] == "gamma" and "new/note.md" in g
    assert clean_markdown("```markdown\n# T\nbody\n```") == "# T\nbody"
    assert clean_markdown("Sure! Here it is:\n\n# T\nx") == "# T\nx"
    dg = parse_diagram('Here: {"title": "T", "nodes": [{"id": "a", "label": "A", "group": "ui"}, {"id": "b", "label": "B", "group": "zzz"},'
                       ' {"id": "a", "label": "dup"}], "edges": [{"from": "a", "to": "b"}, {"from": "a", "to": "x"}, {"from": "b", "to": "b"}]}')
    assert [n["id"] for n in dg["nodes"]] == ["a", "b"] and dg["nodes"][1]["group"] == "other" and len(dg["edges"]) == 1
    for bad in ("no json", '{"nodes": [{"id": "a"}]}'):
        try:
            parse_diagram(bad)
            raise AssertionError(bad)
        except ValueError:
            pass
    assert doc_prompt("readme").endswith(DOC_TAIL.strip()[-20:]) and "Make a diagram of" in diagram_prompt("modules")
    print("studio: checks pass")
