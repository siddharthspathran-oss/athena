"""Publishing and outreach: put a mission's site on GitHub Pages (athena-sites) and open
its outreach draft - always on the user's yes, never on Athena's own.

Split out of missions.py (31 Oct 2026); missions.py imports all of it back.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from .config import cfg
from .missions_store import _update, _note, _slug, _get


_WEB = {".html", ".htm", ".css", ".js", ".json", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico",
        ".woff", ".woff2", ".ttf", ".txt", ".xml", ".webmanifest"}


def _site_slug(m: dict) -> str:
    return _slug(m["title"])[:40] + "-" + m["id"][:4]


def publish_target(m: dict) -> tuple[Path | None, str]:
    folder = Path(str(cfg.get("missions.publish.dir", r"C:\claude code\athena-sites")))
    base = str(cfg.get("missions.publish.url", "")).rstrip("/")
    if not (folder / ".git").exists() or not base:
        return None, ""
    return folder, f"{base}/{_site_slug(m)}/"


def _site_index(repo: Path) -> None:
    """The repo's front page: a list of the sites published so far."""
    import html
    rows = []
    for d in sorted((p for p in repo.iterdir() if p.is_dir() and (p / "index.html").exists()),
                    key=lambda p: p.stat().st_mtime, reverse=True):
        title = re.search(r"<title>(.*?)</title>", (d / "index.html").read_text(encoding="utf-8", errors="replace"),
                          re.I | re.S)
        name = html.escape((title.group(1).strip() if title else d.name)[:90])
        rows.append(f'<li><a href="{html.escape(d.name)}/">{name}</a></li>')
    (repo / "index.html").write_text(
        "<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Athena sites</title><style>body{font:16px/1.6 system-ui,sans-serif;max-width:640px;margin:48px auto;"
        "padding:0 16px;color:#1d1b26;background:#faf9fd}a{color:#5b3fd6}h1{font-size:24px}</style>"
        "<h1>Sites built with Athena</h1><ul>" + "".join(rows or ["<li>Nothing published yet.</li>"]) + "</ul>",
        encoding="utf-8")


def publish(mid: str) -> str:
    """Copy a mission's web files into the Pages repo and push. Returns the URL."""
    m = _get(mid)
    if m is None:
        raise RuntimeError("no such mission")
    repo, url = publish_target(m)
    if repo is None:
        raise RuntimeError("publishing isn't set up yet (missions.publish in config.yaml)")
    src = Path(m["folder"])
    if not (src / "index.html").exists():
        raise RuntimeError("there's no index.html to publish")
    dest = repo / _site_slug(m)
    if dest.exists():
        shutil.rmtree(dest)
    for f in src.rglob("*"):
        rel = f.relative_to(src).parts
        if (f.is_file() and f.suffix.lower() in _WEB and "mission_logs" not in rel
                and not any(part.startswith(".") for part in rel)):      # .review/ screenshots stay home
            (dest / f.relative_to(src)).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest / f.relative_to(src))
    _site_index(repo)
    git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, timeout=120,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    git("add", "-A")
    out = git("commit", "-m", f"Publish {m['title']}")
    if out.returncode and "nothing to commit" not in out.stdout + out.stderr:
        raise RuntimeError(f"git commit failed: {(out.stderr or out.stdout)[:160]}")
    out = git("push")
    if out.returncode:
        raise RuntimeError(f"git push failed: {(out.stderr or out.stdout)[:160]}")
    _update(mid, published=url)
    _note(mid, f"published at {url}")
    return url


def draft_file(m: dict) -> Path | None:
    """The outreach draft a chain wrote (email_draft.md and the like), if any."""
    files = sorted(Path(m["folder"]).glob("*draft*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def open_draft(mid: str) -> str:
    """Open the mission's email draft in the mail app, filled in. Sending is the user's own press."""
    import os
    import urllib.parse
    m = _get(mid)
    f = draft_file(m) if m else None
    if f is None:
        raise RuntimeError("this mission has no email draft")
    text = f.read_text(encoding="utf-8")
    to = re.search(r"^\**to:?\**:?\s*(\S+@\S+)", text, re.I | re.M)
    subject = re.search(r"^\**subject:?\**:?\s*(.+)$", text, re.I | re.M)
    body = re.sub(r"^\**(?:to|subject):?\**:?.*$\n?", "", text, flags=re.I | re.M).strip()
    body = re.sub(r"^#+\s*", "", body, flags=re.M)
    query = urllib.parse.urlencode({k: v for k, v in (("subject", subject.group(1).strip() if subject else ""),
                                                       ("body", body[:1800])) if v}, quote_via=urllib.parse.quote)
    os.startfile(f"mailto:{to.group(1).strip('<>') if to else ''}?{query}")
    return f"Opened the draft in your mail app{' to ' + to.group(1) if to else ''} - read it, then send it yourself."
