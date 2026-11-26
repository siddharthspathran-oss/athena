"""Where missions are kept: data/missions.json, read and changed under one lock, plus
the small helpers every part of missions shares (a note on a mission, a folder slug,
a page preview).

Split out of missions.py (31 Oct 2026); missions.py imports all of it back.
"""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path

from .config import DATA, cfg
from .store import load_json, save_json


FILE = DATA / "missions.json"
_lock = threading.Lock()


def load() -> list[dict]:
    return load_json(FILE, [])


def _save(ms: list[dict]) -> None:
    save_json(FILE, ms)


def _update(mid: str, **fields) -> dict | None:
    with _lock:
        ms = load()
        m = next((x for x in ms if x["id"] == mid), None)
        if m is not None:
            for k, v in fields.items():
                if k == "progress":
                    m.setdefault("progress", []).append(v)
                    m["progress"] = m["progress"][-60:]
                else:
                    m[k] = v
            _save(ms)
        return m


def _preview(folder: Path) -> str:
    """The file to show: index.html, else the newest .html, else the newest
    .md (research and Argon's answers), so the phone can read the result."""
    videos = sorted(folder.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    if videos:
        return videos[0].name
    if (folder / "index.html").exists():
        return "index.html"
    for pattern in ("*.html", "*.pdf", "*.md"):
        files = sorted(folder.rglob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
        if files:
            return str(files[0].relative_to(folder)).replace("\\", "/")
    return ""


def _note(mid: str, text: str) -> None:
    _update(mid, progress={"t": time.time(), "text": text})
    try:
        from . import live
        live.note(f"mission: {text}")
    except Exception:
        pass


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "mission"


def _get(mid: str) -> dict | None:
    return next((x for x in load() if x["id"] == mid), None)


ROOT = Path(str(cfg.get("missions.folder", r"C:\claude code\athena-jobs")))
WORKERS = {"claude": "Claude", "gemini": "Gemini", "argon": "Gemini Argon", "maps": "Google Maps", "video": "Video"}


def _title(task: str) -> str:
    title = re.sub(r"^(?:please\s+)?(?:build|make|create|write|design|find|research)\s+(?:me\s+)?", "", task.strip(), flags=re.I)
    title = re.split(r"\s*[:;,.(—]\s*|\s+-\s+|\s+and\s+(?:then\s+)?(?:build|make|create|write|draft|design|code)\b",
                     title, maxsplit=1, flags=re.I)[0]                # the first clause / the first action
    title = " ".join(title.split()[:9])
    return title[:1].upper() + title[1:] if title else "Mission"
