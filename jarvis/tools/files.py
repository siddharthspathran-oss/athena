"""File management: search, read, write, organise, and inspect files.

Every path is checked against safety.allowed_roots before anything happens.
Reading, opening and showing in Explorer work on every local drive; anything
that changes a file (write, move, rename, delete) is also refused inside
safety.protected_paths - Windows, Program Files, ProgramData, AppData - and on
drive roots, because nothing asks before a spoken delete runs.
"""
from __future__ import annotations

import re
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path

from .. import winapi
from . import finder
from ..config import expand, is_path_allowed, path_refusal
from ..registry import tool

TAG = ["files"]

# Extensions grouped for the organise tool
CATEGORIES = {
    "Images": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".heic"},
    "Documents": {".pdf", ".docx", ".doc", ".txt", ".md", ".rtf", ".odt", ".pptx", ".xlsx", ".csv"},
    "Video": {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm"},
    "Audio": {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg"},
    "Archives": {".zip", ".rar", ".7z", ".tar", ".gz"},
    "Installers": {".exe", ".msi"},
    "Code": {".py", ".js", ".ts", ".java", ".cpp", ".c", ".html", ".css", ".json", ".ipynb"},
}


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


def _resolve(path: str, want_dir: bool | None = None) -> tuple[Path | None, str]:
    """Turn whatever the model (or a spoken name) passed into a real path.

    The model guesses paths - "C:/claude code/scheduler.pdf",
    "C:/Users/Siddharth/resume.docx" - rather than searching first, however
    firmly the prompt tells it not to. So if the path does not exist, the
    name is looked up in the file index instead of failing.

    Returns (path, message). Path is None when the message should be returned.
    A confident match with equally good rivals takes the newest and says so
    in the message; the message is "" when there is nothing worth adding.
    """
    raw = (path or "").strip().strip("\"'")
    if not raw:
        return None, "ERROR: no file name given."
    looks_like_path = bool(re.match(r"^(?:[a-z]:[\\/]|~|%|\\\\)", raw, re.I))
    if looks_like_path:
        try:
            p = _redirected(expand(raw))
        except Exception:
            p = None
        if p is not None and p.exists():
            why = path_refusal(p)
            if why:
                return None, "ERROR: " + why
            if want_dir is True and not p.is_dir():
                return None, f"ERROR: {p} is a file, not a folder."
            return p, ""
        name = Path(raw).name
    else:
        name = raw

    res = finder.find(name, want_dir=want_dir)
    if not res.confident and Path(name).suffix:
        # The model invents extensions - it asks for "experiment 4.1.pdf"
        # when the file is a .docx - so retry on the name without one.
        again = finder.find(Path(name).stem, want_dir=want_dir)
        if again.confident:
            res = again
    if not res.confident:
        # A misheard "open bar, sir" had the model call open_file("bar"),
        # then "bar.docx", "bar.pdf", "bar.docx" again until the step limit -
        # every variant the same miss. Say so plainly the second time; the
        # wording is also what gets spoken if the user asks again, so it has
        # to read as an answer, not as an instruction to the model.
        key = tuple(res.query.words)
        seen = _MISSES.get(key, 0.0)
        _MISSES[key] = time.time()
        if key and time.time() - seen < 60:
            return None, (f"Still nothing called '{' '.join(key)}' - I searched "
                          f"every drive, with any extension.")
        return None, _not_found(name, res)

    best = Path(res.best.entry.path)
    why = path_refusal(best)
    if why:
        return None, f"Found {best}, but I can't use it: {why}"
    note = ""
    if res.ties:
        others = _offer_files([m.entry.path for m in res.ties[:5]])
        note = (f"{len(res.ties) + 1} files match equally well; I opened the newest. "
                f"Say 'all' or a number for the others: "
                + "; ".join(f"{i}, {c.label}" for i, c in enumerate(others, 1)) + ".")
    return best, note


_MISSES: dict[tuple, float] = {}


def _not_found(name: str, res) -> str:
    """Plain 'no' with the closest names - never a question back.

    Not prefixed ERROR: agent.py turns a routed ERROR into "I could not do
    that", and the closest names are the useful part of the answer.
    """
    what = " ".join(res.query.words) or name
    if res.near:
        seen, near = set(), []
        for m in res.near:
            if m.entry.name not in seen and not path_refusal(m.entry.path):
                seen.add(m.entry.name)
                near.append(m.entry.path)
        if near:
            # Asked as a question, and the answer is waiting in offer.py:
            # "yes" opens the first, "the second one" the next.
            choices = _offer_files(near[:3])
            names = "; ".join(c.label for c in choices)
            return (f"I couldn't find a file called {what}. Closest: {names}. "
                    f"Want me to open {choices[0].label}?")
    return f"I couldn't find anything called '{what}' on this PC."


def _where(p: Path) -> str:
    """'Downloads' rather than a path - this is read aloud."""
    for key in ("desktop", "downloads", "documents", "pictures", "videos", "music"):
        kf = finder.known_folder(key)
        if kf and str(p.parent).lower() == str(kf).lower():
            return key.capitalize()
    return str(p.parent)


def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


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


@tool(tags=TAG)
def list_folder(folder: str = "~", limit: int = 50) -> str:
    """List the contents of a folder.

    folder: the folder to list
    limit: maximum entries to return
    """
    root = _guard(folder)
    if root is None:
        return _refused(folder)
    if not root.is_dir():
        return f"ERROR: {root} is not a folder."
    entries = sorted(root.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    shown = entries[:limit]
    extra = f"\n... and {len(entries) - limit} more" if len(entries) > limit else ""
    return "\n".join(_row(p) for p in shown) + extra or "(empty folder)"


@tool(tags=TAG)
def read_file(path: str, max_chars: int = 4000) -> str:
    """Read the text content of a file. Handles txt, md, code, pdf and docx.

    path: full path to the file
    max_chars: stop after this many characters
    """
    p, msg = _resolve(path, want_dir=False)
    if p is None:
        return msg
    if not p.is_file():
        return f"ERROR: {p} is not a file."

    ext = p.suffix.lower()
    try:
        if ext == ".pdf":
            from pypdf import PdfReader

            text = "\n".join((pg.extract_text() or "") for pg in PdfReader(str(p)).pages)
        elif ext == ".docx":
            import docx

            text = "\n".join(par.text for par in docx.Document(str(p)).paragraphs)
        else:
            text = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"ERROR reading {p.name}: {e}"

    if len(text) > max_chars:
        return text[:max_chars] + f"\n... [truncated, {len(text) - max_chars} more characters]"
    return text or "(file is empty)"


@tool(tags=TAG)
def write_file(path: str, content: str, append: bool = False) -> str:
    """Create a file or write text into one.

    path: full path of the file to write
    content: the text to write
    append: true to add to the end instead of replacing
    """
    p = _guard(path, write=True)
    if p is None:
        return _refused(path, write=True)
    p.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    with open(p, mode, encoding="utf-8") as f:
        f.write(content)
    return f"{'Appended to' if append else 'Wrote'} {p} ({len(content)} characters)."


@tool(tags=TAG, destructive=True)
def move_file(source: str, destination: str) -> str:
    """Move or rename a file or folder.

    source: the existing path
    destination: the new path, or the folder to move it into
    """
    s, d = _guard(source, write=True), _guard(destination, write=True)
    if s is None:
        return _refused(source, write=True)
    if d is None:
        return _refused(destination, write=True)
    if not s.exists():
        return f"ERROR: {s} does not exist."
    if d.is_dir():
        d = d / s.name
    d.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(s), str(d))
    return f"Moved {s.name} to {d}."


@tool(tags=TAG, destructive=True)
def delete_file(path: str) -> str:
    """Send a file or folder to the Recycle Bin. Recoverable, not a hard delete.

    path: the file or folder to remove
    """
    p = _guard(path, write=True)
    if p is None:
        return _refused(path, write=True)
    if not p.exists():
        return f"ERROR: {p} does not exist."
    try:
        from send2trash import send2trash

        send2trash(str(p))
        return f"Sent {p.name} to the Recycle Bin."
    except ImportError:
        # Fall back to PowerShell's recycle-bin aware delete
        import subprocess

        ps = (
            "Add-Type -AssemblyName Microsoft.VisualBasic; "
            f"[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile('{p}', 'OnlyErrorDialogs', 'SendToRecycleBin')"
        )
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, check=False)
        if r.returncode != 0:
            return f"ERROR: {r.stderr.strip()}"
        return f"Sent {p.name} to the Recycle Bin."


@tool(tags=TAG, destructive=True, timeout=0)
def organize_folder(folder: str, dry_run: bool = True) -> str:
    """Sort a messy folder into Images, Documents, Video, Audio and other subfolders.

    folder: the folder to tidy, for example ~/Downloads
    dry_run: true to preview the plan without moving anything
    """
    root = _guard(folder, write=not dry_run)
    if root is None:
        return _refused(folder, write=not dry_run)
    if not root.is_dir():
        return f"ERROR: {root} is not a folder."

    plan: dict[str, list[Path]] = {}
    for p in root.iterdir():
        if p.is_dir() or p.name.startswith("."):
            continue
        category = next((c for c, exts in CATEGORIES.items() if p.suffix.lower() in exts), "Other")
        plan.setdefault(category, []).append(p)

    if not plan:
        return f"{root} has nothing loose to sort."

    summary = "\n".join(f"{c}: {len(v)} file(s)" for c, v in sorted(plan.items()))
    if dry_run:
        return f"Plan for {root} (nothing moved yet):\n{summary}\n\nSay 'do it' to apply."

    moved = 0
    for category, items in plan.items():
        dest = root / category
        dest.mkdir(exist_ok=True)
        for p in items:
            target = dest / p.name
            n = 1
            while target.exists():
                target = dest / f"{p.stem} ({n}){p.suffix}"
                n += 1
            try:
                shutil.move(str(p), str(target))
                moved += 1
            except Exception:
                pass
    return f"Organised {moved} file(s) in {root}:\n{summary}"


@tool(tags=TAG, timeout=120)
def folder_size(folder: str) -> str:
    """Report the total size of a folder and its largest items.

    folder: the folder to measure
    """
    root = _guard(folder)
    if root is None:
        return _refused(folder)
    if not root.is_dir():
        return f"ERROR: {root} is not a folder."

    total = 0
    sizes: list[tuple[int, Path]] = []
    for p in root.rglob("*"):
        try:
            if p.is_file():
                s = p.stat().st_size
                total += s
                sizes.append((s, p))
        except OSError:
            continue
    sizes.sort(reverse=True)
    top = "\n".join(f"  {_fmt_size(s)}  {p.name}" for s, p in sizes[:10])
    return f"{root} is {_fmt_size(total)} across {len(sizes)} files.\nLargest:\n{top}"


_OFFICE_DIRS = [
    r"C:\Program Files\Microsoft Office\root\Office16",
    r"C:\Program Files (x86)\Microsoft Office\root\Office16",
    r"C:\Program Files\Microsoft Office\Office16",
    r"C:\Program Files (x86)\Microsoft Office\Office16",
]

_EXT_APPS = {
    ".docx": "WINWORD.EXE", ".doc": "WINWORD.EXE", ".rtf": "WINWORD.EXE",
    ".xlsx": "EXCEL.EXE", ".xls": "EXCEL.EXE", ".csv": "EXCEL.EXE",
    ".pptx": "POWERPNT.EXE", ".ppt": "POWERPNT.EXE",
}


def _app_for(suffix: str) -> str | None:
    """Full path to the program that handles this file type, if installed."""
    exe = _EXT_APPS.get(suffix.lower())
    if not exe:
        return None
    for d in _OFFICE_DIRS:
        candidate = Path(d) / exe
        if candidate.exists():
            return str(candidate)
    return None


def _launch_and_confirm(p: Path, exe: str | None = None,
                        use_startfile: bool = False, seconds: float = 9.0) -> bool:
    """Open the file and confirm it really opened.

    Checks for a new process or a window naming the file, rather than any new
    window at all - a passing notification popup used to count as success.
    """
    import os
    import subprocess

    import psutil

    stem = p.stem.lower()[:24]
    procs_before = {pr.pid for pr in psutil.process_iter()}
    wins_before = {t for _h, t in winapi.list_windows()}

    try:
        if use_startfile:
            os.startfile(str(p))
        else:
            subprocess.Popen([exe, str(p)], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    except Exception:
        return False

    deadline = time.time() + seconds
    while time.time() < deadline:
        time.sleep(0.6)
        for _h, title in winapi.list_windows():
            if title not in wins_before and stem in title.lower():
                return True
        if {pr.pid for pr in psutil.process_iter()} - procs_before:
            # A new process appeared; give its window a moment to title itself.
            time.sleep(1.5)
            for _h, title in winapi.list_windows():
                if stem in title.lower():
                    return True
    return False


def _handler(suffix: str) -> str | None:
    """Name of the app Windows opens this type with, or None if it has none.

    Asked up front rather than inferred afterwards: the old open_file launched
    and then polled up to 9 s for a window naming the file, so every open took
    3-4 s even when it had worked at once. With no handler registered,
    os.startfile shows the "Pick an app" dialog, which is not an open.
    """
    import ctypes
    from ctypes import wintypes

    if suffix.lower() in (".lnk", ".url", ".exe", ".bat", ".cmd", ".msi"):
        return "Windows"
    try:
        n = wintypes.DWORD(512)
        buf = ctypes.create_unicode_buffer(512)
        # ASSOCSTR_FRIENDLYAPPNAME = 4
        hr = ctypes.windll.shlwapi.AssocQueryStringW(0, 4, suffix, None, buf, ctypes.byref(n))
    except Exception:
        return None
    name = buf.value.strip()
    if hr != 0 or not name or name.lower() == "pick an app":
        return None
    return name


def _start(p: Path) -> None:
    """Open with the default app. A seam so the tests can stub it out."""
    import os

    os.startfile(str(p))


def _explorer(*args: str) -> None:
    import subprocess

    subprocess.Popen(["explorer.exe", *args], stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)


# The file the user was last shown or opened, so "open it" has something to
# mean. On 24 Sep 2026 "open experiment 6.1 in file explorer" was followed by
# "open it", which the model sent to open_app("it") - and Git CMD launched.
last_file: Path | None = None


def _remember(p: Path) -> None:
    global last_file
    last_file = p


@tool(tags=TAG)
def open_file(path: str) -> str:
    """Open a document, PDF, image or any file in whatever app handles it.

    Use this whenever the user wants to open, view, read or edit a specific
    file - "open the scheduler pdf", "open my resume", "open experiment 4.1".
    Pass the user's own words for the name if you do not know the full path;
    this tool searches Desktop, Downloads, Documents, OneDrive and every other
    drive by name itself. Do not call find_files first.

    path: full path, or just the file's name as the user said it
    """
    p, note = _resolve(path, want_dir=None)
    if p is None:
        return note
    _remember(p)
    if p.is_dir():
        _explorer(str(p))
        return f"Opened the {p.name} folder."

    where = _where(p)
    handler = _handler(p.suffix) if p.suffix else None
    if handler:
        try:
            _start(p)
        except OSError as e:
            return f"ERROR: Windows could not open {p.name}: {e}"
        via = "" if handler == "Windows" else f" in {handler}"
        return f"Opened {p.name}{via}, from {where}." + (f" {note}" if note else "")

    # No default app for this type. Office files sometimes lose their
    # association; open them with the program directly.
    exe = _app_for(p.suffix)
    if exe:
        if _launch_and_confirm(p, exe=exe):
            return f"Opened {p.name} in {Path(exe).stem.title()}."
        return (f"ERROR: {Path(exe).stem.title()} did not open {p.name}. "
                f"Try opening it by hand to see what it reports.")
    _explorer("/select,", str(p))
    return (f"Windows has no app set for {p.suffix or 'extension-less'} files, so "
            f"I showed {p.name} in File Explorer instead ({where}).")


def _spoken(name: str) -> str:
    """A file name as it should be read out: no underscores, no extension
    unless two names differ only by it."""
    return re.sub(r"[_]+", " ", name).strip()


def _offer_files(paths: list[str]) -> list:
    from .. import offer

    choices = [offer.Choice(_spoken(Path(p).name), "open_file", {"path": p}) for p in paths[:6]]
    offer.set_choices(choices)
    return choices


@tool(tags=TAG, timeout=60)
def find_document(query: str, open_all: bool = False) -> str:
    """Find files or a folder by what the user calls them and open them; if
    nothing is named that, look inside the indexed documents. Use for "search
    my documents for X", "find X on my PC", "open all my X files".

    query: the file's name or subject, in the user's words
    open_all: true to open every matching file (up to six), as in "open all my X files"
    """
    # "Athena, search my documents for signal and system" reached the model,
    # which said it was opening the file and opened nothing; then, with
    # several Signals and Systems files, only ever the newest one opened and
    # the rest could not be reached (28 Sep 2026). Several matches are now
    # listed and asked about - the reply ("two", "all", "yes") is handled by
    # jarvis/offer.py - or all opened when that was asked for.
    q = (query or "").strip().strip("\"'.")
    if not q:
        return "Search for what?"
    found = [e for e in finder.find_all(q, limit=8) if not path_refusal(e.path)]
    if found:
        if open_all:
            from .. import offer

            offer.clear()
            opened, failed = [], []
            for e in found[:6]:
                r = open_file(e.path)
                (failed if r.startswith("ERROR") else opened).append(_spoken(e.name))
            more = f" There are {len(found) - 6} more; ask for them by name." if len(found) > 6 else ""
            bad = f" Couldn't open: {', '.join(failed)}." if failed else ""
            return f"Opened {len(opened)} file{'s' if len(opened) != 1 else ''}: {', '.join(opened)}.{bad}{more}"
        if len(found) == 1:
            return open_file(found[0].path)
        choices = _offer_files([e.path for e in found])
        listing = "; ".join(f"{i}, {c.label}" for i, c in enumerate(choices, 1))
        return (f"I found {len(found)} files for {q}: {listing}. "
                f"Which one? Say a number, or all.")

    p, note = _resolve(q, want_dir=None)        # a folder, or a looser file match
    if p is not None:
        return open_file(str(p)) + (f" {note}" if note else "")

    try:
        from .. import db
        from .knowledge import _embed_query

        if db.count("doc"):
            hits = db.search("doc", q, k=3, embed=_embed_query, min_score=0.5)
            paths = list(dict.fromkeys(h["source"] for h in hits))
            if paths:
                _remember(Path(paths[0]))           # "open it" works too
                choices = _offer_files(paths)
                names = ", ".join(c.label for c in choices)
                return (f"No file is called {q}, but it comes up in {names}. "
                        f"Want me to open {choices[0].label}?")
    except Exception:
        pass
    return note


@tool(tags=TAG)
def open_folder(name: str = "explorer") -> str:
    """Open File Explorer, or a folder in it.

    Use for "open file explorer", "open downloads", "open my documents",
    "open the desktop folder", "open the ltspice folder". Known folders
    (downloads, documents, desktop, pictures, music, videos, onedrive) are
    found where Windows really keeps them, including OneDrive.

    name: "explorer" for File Explorer itself, a known folder, a folder name, or a path
    """
    key = (name or "").strip().strip(".").lower()
    key = re.sub(r"^(?:the|my)\s+", "", key)
    key = re.sub(r"\s+(?:folder|directory)$", "", key)
    if key in ("", "explorer", "file explorer", "windows explorer",
               "file manager", "files", "my files", "this pc"):
        _explorer()
        return "Opened File Explorer."
    if key in finder.FOLDER_WORDS:
        kf = finder.known_folder(key)
        if kf is None:
            return f"This PC has no {key} folder."
        why = path_refusal(kf)
        if why:
            return "ERROR: " + why
        _explorer(str(kf))
        return f"Opened {finder.FOLDER_WORDS[key].capitalize()}."
    p, note = _resolve(name, want_dir=True)
    if p is None:
        return note
    _explorer(str(p))
    return f"Opened the {p.name} folder ({p.parent})." + (f" {note}" if note else "")


@tool(tags=TAG)
def reveal_path(path: str) -> str:
    """Show where a file or folder is, highlighted in a File Explorer window.

    Use for "show X in explorer", "where is X", "show me the file X". Takes a
    full path or just the name as the user said it.

    path: the file or folder, or its name
    """
    p, note = _resolve(path)
    if p is None:
        return note
    _remember(p)
    if p.is_file():
        _explorer("/select,", str(p))
    else:
        _explorer(str(p))
    return f"{p.name} is in {_where(p)} - showing it in File Explorer." + (
        f" {note}" if note else "")


# Build the file index while the rest of the assistant starts, so the first
# "open ..." does not pay for the walk.
finder.warm()
