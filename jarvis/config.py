"""Configuration loading. Reads config.yaml once, exposes it as `cfg`."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)


class Config:
    def __init__(self, path: Path):
        self._path = path
        with open(path, "r", encoding="utf-8") as f:
            self._d: dict[str, Any] = yaml.safe_load(f) or {}

    def get(self, dotted: str, default: Any = None) -> Any:
        """cfg.get('llm.model') -> 'qwen2.5:7b'"""
        node: Any = self._d
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def reload(self) -> None:
        with open(self._path, "r", encoding="utf-8") as f:
            self._d = yaml.safe_load(f) or {}


cfg = Config(ROOT / "config.yaml")


def expand(p: str) -> Path:
    """Expand ~ and %ENVVARS% into a real absolute path."""
    return Path(os.path.expandvars(os.path.expanduser(p))).resolve()


def local_drives() -> list[Path]:
    """Every fixed or removable drive, found at run time - not assumed to be C:.

    Network and optical drives are left out: walking a mapped share or a
    spinning DVD for a file search would stall every request behind it.
    """
    import ctypes
    import string

    try:
        mask = ctypes.windll.kernel32.GetLogicalDrives()
    except Exception:
        return [Path(Path.home().anchor)]
    out = []
    for i, letter in enumerate(string.ascii_uppercase):
        if not (mask >> i) & 1:
            continue
        root = f"{letter}:\\"
        # 2 = removable (USB sticks), 3 = fixed
        if ctypes.windll.kernel32.GetDriveTypeW(root) in (2, 3):
            out.append(Path(root))
    return out


def allowed_roots() -> list[Path]:
    """Folders the file tools may reach. "*" in config means every local drive."""
    roots = []
    for r in cfg.get("safety.allowed_roots", ["~"]):
        if str(r).strip() == "*":
            roots.extend(local_drives())
            continue
        try:
            roots.append(expand(r))
        except Exception:
            pass
    return roots


def protected_paths() -> list[Path]:
    """Folders the file tools may read but never change (safety.protected_paths)."""
    out = []
    for r in cfg.get("safety.protected_paths", []) or []:
        try:
            out.append(expand(str(r)))
        except Exception:
            pass
    return out


def _inside(t: Path, root: Path) -> bool:
    try:
        t.relative_to(root)
        return True
    except ValueError:
        # Windows paths are case-insensitive; relative_to is not.
        low, base = str(t).lower(), str(root).lower().rstrip("\\")
        return low == base or low.startswith(base + "\\")


def path_refusal(target: str | Path, write: bool = False) -> str | None:
    """Why a file tool may not touch `target`, or None if it may.

    Reading, opening, searching and showing in Explorer are allowed anywhere
    under safety.allowed_roots. Changing something - write, move, rename,
    delete - is additionally refused inside safety.protected_paths and on a
    drive root itself, because with confirm_destructive off a misheard voice
    command goes straight through, and it must not be able to send
    the Windows folder to the Recycle Bin.
    """
    try:
        t = expand(str(target))
    except Exception:
        return f"{target} is not a valid path."
    if not any(_inside(t, root) for root in allowed_roots()):
        return f"{t} is outside the folders Athena may use (safety.allowed_roots in config.yaml)."
    if not write:
        return None
    if t.parent == t:
        return f"{t} is a whole drive - Athena will not change a drive root."
    for p in protected_paths():
        if _inside(t, p):
            return (f"{t} is inside {p}, which is protected - Athena can open and "
                    f"read files there but will not change, move or delete them.")
    if any(part.lower() in ("$recycle.bin", "system volume information")
           for part in t.parts):
        return f"{t} is a Windows system folder - Athena will not change it."
    return None


def is_path_allowed(target: str | Path, write: bool = False) -> bool:
    """True if a file tool may read (or, with write=True, change) `target`."""
    return path_refusal(target, write) is None
