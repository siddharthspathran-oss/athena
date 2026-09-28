"""Durable JSON storage for the small files Athena must not lose.

Two failure modes bit us, both silent:

  * A plain write truncates the file first, so a process killed mid-write
    leaves it empty or half-written.
  * Loading then treated the unreadable file as "no data", and the next save
    wrote a fresh one - quietly destroying contacts that were still there.

So writes go to a temporary file and are swapped in atomically, and a file
that fails to parse is preserved as a .corrupt copy and reported, never
treated as empty.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path


class StoreError(RuntimeError):
    """The file exists but could not be read. Callers must not overwrite it."""


def load_json(path: Path, default: dict | list | None = None):
    """Read JSON, or return `default` if the file does not exist yet.

    Raises StoreError if the file exists but is unreadable, so a caller never
    mistakes corruption for emptiness and saves over real data.
    """
    if default is None:
        default = {}
    if not path.exists():
        return default
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise StoreError(f"cannot read {path.name}: {e}") from e

    if not text.strip():
        # An empty file is the signature of an interrupted write.
        backup = _quarantine(path)
        raise StoreError(f"{path.name} was empty (interrupted write). Kept as {backup.name}.")

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        backup = _quarantine(path)
        raise StoreError(f"{path.name} is not valid JSON ({e}). Kept as {backup.name}.")


def save_json(path: Path, data) -> None:
    """Write JSON atomically: a reader sees either the old file or the new one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)          # atomic on Windows and POSIX alike
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _quarantine(path: Path) -> Path:
    """Move a damaged file aside so it can be inspected or recovered."""
    backup = path.with_suffix(path.suffix + ".corrupt")
    n = 1
    while backup.exists():
        backup = path.with_suffix(f"{path.suffix}.corrupt{n}")
        n += 1
    try:
        shutil.move(str(path), str(backup))
    except OSError:
        pass
    return backup
