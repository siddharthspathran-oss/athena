"""Athena's notes in your Obsidian vault (obsidian.vault in config.yaml).

A vault is only a folder of Markdown files, so no plugin or API is needed:
Athena writes notes there (study answers in Study/, the evening summary in
Daily/), the document index reads them back for "search my notes for ...",
and Obsidian shows the same files with its links and graph (9 Oct 2026).
"""
from __future__ import annotations

import datetime as dt
import re
import threading
import time
from pathlib import Path

from .config import cfg, expand


def root() -> Path | None:
    v = str(cfg.get("obsidian.vault", "") or "")
    p = expand(v) if v else None
    return p if p and p.is_dir() else None


def obsidian_installed() -> bool:
    """Obsidian registers the obsidian:// link scheme when it is installed."""
    import winreg
    try:
        winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "obsidian").Close()
        return True
    except OSError:
        return False


def _md(text: str) -> str:
    """Obsidian's maths is $...$ and $$...$$; the model writes \\(...\\) and \\[...\\]."""
    text = re.sub(r"\\\[(.+?)\\\]", lambda m: f"$${m.group(1)}$$", text, flags=re.S)
    return re.sub(r"\\\((.+?)\\\)", lambda m: f"${m.group(1)}$", text, flags=re.S)


def _name(title: str) -> str:
    return " ".join(re.sub(r'[\\/:*?"<>|#^\[\]]+', " ", title).split())[:80] or "Note"


def save(folder: str, title: str, body: str, tags: list[str] | None = None) -> Path | None:
    """A new note; never overwrites one - a second "Fourier transform" gets (2)."""
    base = root()
    if base is None:
        return None
    d = base / folder
    d.mkdir(parents=True, exist_ok=True)
    path, n = d / f"{_name(title)}.md", 2
    while path.exists():
        path, n = d / f"{_name(title)} ({n}).md", n + 1
    front = f"---\ncreated: {dt.datetime.now():%Y-%m-%d %H:%M}\ntags: [{', '.join(['athena', *(tags or [])])}]\n---\n\n"
    path.write_text(front + f"# {title}\n\n" + _md(body).strip() + "\n", encoding="utf-8")
    return path


def append_daily(heading: str, text: str) -> Path | None:
    """A section added to today's daily note (Daily/2026-10-09.md)."""
    base = root()
    if base is None:
        return None
    d = base / "Daily"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{dt.date.today().isoformat()}.md"
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"\n## {heading}\n\n{_md(text).strip()}\n")
    return path


def start() -> None:
    """Notes you write or edit in Obsidian reach "search my notes" within
    obsidian.index_minutes (only changed files are read again)."""
    def loop():
        while True:
            time.sleep(60 * float(cfg.get("obsidian.index_minutes", 15)))
            try:
                from . import gaming
                base = root()
                if base and not gaming.on():          # embedding loads a model on the GPU
                    from .tools.knowledge import index_documents
                    index_documents(str(base))
            except Exception as e:
                print(f"[vault index: {e}]")
    threading.Thread(target=loop, daemon=True, name="athena-vault").start()


if __name__ == "__main__":
    assert _md(r"so \(x^2\) and \[\int f\]") == "so $x^2$ and $$\\int f$$", _md(r"so \(x^2\) and \[\int f\]")
    assert _name('a/b: "c"?') == "a b c"
    print("ok")
