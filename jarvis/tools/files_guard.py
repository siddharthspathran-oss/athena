"""Where Athena may touch files: inside safety.allowed_roots, never in a protected
path, following Windows' redirected folders (Documents on OneDrive); plus sizes
written for people.

Split out of files.py (31 Oct 2026); files.py and files_search.py use it, and
files.py imports it all back.
"""
from __future__ import annotations

from pathlib import Path

from . import finder
from ..config import expand, is_path_allowed, path_refusal


TAG = ["files"]


def _redirected(p: Path) -> Path:
    """Follow OneDrive folder redirection.

    Windows moves Documents, Desktop and Pictures into OneDrive but leaves the
    original folders in place and nearly empty, so a literal ~/Documents finds
    nothing while the real files sit elsewhere.
    """
    home = Path.home()
    onedrive = home / "OneDrive"
    if not onedrive.is_dir():
        return p
    try:
        rel = p.relative_to(home)
    except ValueError:
        return p
    parts = rel.parts
    if not parts or parts[0] not in ("Documents", "Desktop", "Pictures", "Music", "Videos"):
        return p
    moved = onedrive / rel
    # Prefer wherever the file actually is; fall back to the redirected folder.
    if p.exists() and not moved.exists():
        return p
    if moved.exists():
        return moved
    return p


def _guard(path: str | Path, write: bool = False) -> Path | None:
    """Resolve a path, or return None if the tools may not touch it.

    write=True for anything that changes the file system. Use _refused() for
    the message to return.
    """
    kf = finder.known_folder(str(path)) if str(path).strip().lower() in finder.FOLDER_WORDS else None
    if kf is not None:
        path = kf     # "downloads" -> wherever Downloads really is
    if not is_path_allowed(path, write=write):
        return None
    return _redirected(expand(str(path)))


def _refused(path: str | Path, write: bool = False) -> str:
    return "ERROR: " + (path_refusal(path, write)
                        or f"{path} is outside the allowed folders in config.yaml.")


def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
