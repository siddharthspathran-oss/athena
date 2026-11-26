"""Finding files by name: which folders to search, which to skip (node_modules, .git,
AppData...), and the find_files tool.

Split out of files.py (31 Oct 2026); files.py imports all of it back.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path

from . import finder
from ..registry import tool
from .files_guard import TAG, _guard, _refused, _fmt_size


_SKIP_DIRS = {"node_modules", ".git", "AppData", "$Recycle.Bin", "venv",
              "__pycache__", "Windows", "Program Files", "Program Files (x86)"}


def _search_roots() -> list[Path]:
    """Where to look, most likely first.

    Walking the whole home folder takes over a minute on a real machine, which
    reads as a hang. Documents and Desktop hold what people actually ask for.
    """
    home = Path.home()
    onedrive = home / "OneDrive"
    candidates = [
        onedrive / "Documents", onedrive / "Desktop", onedrive / "Pictures",
        home / "Documents", home / "Desktop", home / "Downloads",
        onedrive, home,
    ]
    out: list[Path] = []
    for c in candidates:
        if c.is_dir() and c not in out:
            out.append(c)
    return out


def _search_by_name(name: str, limit: int = 12, budget: float = 6.0) -> list[Path]:
    """Find files or folders whose name resembles `name`.

    Bounded by a time budget and by pruning junk directories as it walks, so a
    miss costs a few seconds rather than a minute and a half.
    """
    import os

    want = name.strip().lower()
    if not want:
        return []
    deadline = time.time() + budget

    exact: list[Path] = []
    partial: list[Path] = []
    seen: set[str] = set()

    for root in _search_roots():
        if time.time() > deadline:
            break
        for dirpath, dirnames, filenames in os.walk(root, topdown=True):
            if time.time() > deadline:
                break
            # Prune in place so os.walk never descends into these at all.
            dirnames[:] = [d for d in dirnames
                           if d not in _SKIP_DIRS and not d.startswith(".")]
            for entry in list(dirnames) + filenames:
                low = entry.lower()
                if want not in low:
                    continue
                full = os.path.join(dirpath, entry)
                if full in seen:
                    continue
                seen.add(full)
                stem = low.rsplit(".", 1)[0] if "." in low else low
                (exact if (low == want or stem == want) else partial).append(Path(full))
            # An exact hit is almost certainly the one meant - stop early.
            if exact:
                break
        if exact:
            break

    return (exact + partial)[:limit]


def _row(p: Path) -> str:
    try:
        st = p.stat()
        when = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d")
        kind = "DIR " if p.is_dir() else _fmt_size(st.st_size)
        return f"{when}  {kind:>8}  {p}"
    except OSError:
        return f"(unreadable)  {p}"


@tool(tags=TAG)
def find_files(pattern: str, folder: str = "~", days_old: int = 0, limit: int = 40) -> str:
    """Search for files by name pattern, optionally only recent ones.

    pattern: glob or substring to match, for example *.pdf or tax
    folder: folder to search inside, defaults to your user folder
    days_old: if above 0, only return files modified within this many days
    limit: maximum number of results
    """
    root = _guard(folder)
    if root is None:
        return _refused(folder)
    if not root.exists():
        return f"ERROR: {root} does not exist."

    import fnmatch
    import os

    glob_pattern = pattern if any(c in pattern for c in "*?[") else f"*{pattern}*"
    cutoff = datetime.now() - timedelta(days=days_old) if days_old > 0 else None

    # Walking the home folder with rglob takes about a minute here. os.walk
    # lets us prune whole branches before descending, and a deadline keeps a
    # fruitless search from looking like a hang.
    roots = _search_roots() if str(root) == str(Path.home()) else [root]
    deadline = time.time() + 8.0

    hits: list[Path] = []
    seen: set[str] = set()
    for base in roots:
        if len(hits) >= limit or time.time() > deadline:
            break
        for dirpath, dirnames, filenames in os.walk(base, topdown=True):
            if len(hits) >= limit or time.time() > deadline:
                break
            dirnames[:] = [d for d in dirnames
                           if d not in _SKIP_DIRS and not d.startswith(".")]
            for entry in list(dirnames) + filenames:
                if not fnmatch.fnmatch(entry.lower(), glob_pattern.lower()):
                    continue
                full = os.path.join(dirpath, entry)
                if full in seen:
                    continue
                seen.add(full)
                p = Path(full)
                if cutoff:
                    try:
                        if datetime.fromtimestamp(p.stat().st_mtime) < cutoff:
                            continue
                    except OSError:
                        continue
                hits.append(p)
                if len(hits) >= limit:
                    break

    if not hits and not days_old and not any(c in pattern for c in "*?["):
        # The model passes the user's words as the pattern - "signals and
        # systems course plan" - and guesses the folder (C:/claude code).
        # Neither is a glob problem: look the words up everywhere instead.
        res = finder.find(pattern)
        found = ([res.best] + res.ties if res.confident else []) + res.near
        rows = [_row(Path(m.entry.path)) for m in found[:limit]]
        if res.confident:
            return (f"Best match for '{pattern}' (pass its path to open_file):\n"
                    + "\n".join(rows))
        if rows:
            return (f"Nothing is called exactly '{pattern}'. Closest names:\n"
                    + "\n".join(rows))
    if not hits:
        # The model guesses extensions - it looked for "experiment 4.1.pdf"
        # when the file is a .docx - so retry on the name alone before
        # reporting that nothing exists.
        stem = Path(pattern).stem.strip("*")
        if stem and stem != pattern:
            hits = _search_by_name(stem)
        if not hits:
            hits = _search_by_name(pattern.strip("*"))
        if hits:
            return (f"No exact match for {pattern}, but found by name:\n"
                    + "\n".join(_row(p) for p in hits[:10]))
        return f"No files matching {pattern} under {root}."
    return f"Found {len(hits)} match(es):\n" + "\n".join(_row(p) for p in hits)
