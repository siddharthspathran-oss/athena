"""Find a file from how people say its name, fast enough to feel instant.

"open the scheduler pdf", "open probability assignment", "open experiment one"
- none of these is a path or a glob. They are a few words that should land on
one file in Desktop, Downloads or Documents. This module keeps a small index of
the user's own folders and scores every entry against the spoken words.

Why an index rather than a walk per request: the old per-request os.walk had a
6 s budget and still said "nothing called 'probability assignment.txt'
exists", because it only did substring matches on whatever the model
invented. Walking the known folders to depth 5 here takes ~0.3 s for ~3.5k
entries (measured), so the index is rebuilt in the background once it is a
minute old and a request never waits on the disk after the first.

No tools live here; files.py and agent.py call in.
"""
from __future__ import annotations

import ctypes
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path


# Windows known-folder GUIDs. Desktop, Documents and Pictures are redirected
# into OneDrive on this machine (C:\Users\siddh\OneDrive\Desktop) while the
# old C:\Users\siddh\Desktop still exists and is empty, so a hard-coded
# ~/Desktop finds nothing. Ask the shell where each one really is.
_KNOWN_GUIDS = {
    "desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",
    "downloads": "374DE290-123F-4565-9164-39C4925E467B",
    "documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
    "pictures": "33E28130-4E1E-4676-835A-98395C3BC3BB",
    "music": "4BD8D571-6D19-48D3-BE97-422220080E43",
    "videos": "18989B1D-99B5-455B-841C-AB7C74E4DDFC",
}

# What people call each one.
FOLDER_WORDS = {
    "downloads": "downloads", "download": "downloads",
    "documents": "documents", "document": "documents", "docs": "documents",
    "my documents": "documents",
    "desktop": "desktop",
    "pictures": "pictures", "picture": "pictures", "photos folder": "pictures",
    "music": "music",
    "videos": "videos", "video": "videos",
    "onedrive": "onedrive", "one drive": "onedrive",
    "home": "home", "user folder": "home",
}


def known_folder(key: str) -> Path | None:
    """Real location of a known folder ('downloads', 'desktop', ...)."""
    key = FOLDER_WORDS.get(key.strip().lower(), key.strip().lower())
    if key == "home":
        return Path.home()
    if key == "onedrive":
        od = os.environ.get("OneDrive") or str(Path.home() / "OneDrive")
        return Path(od) if Path(od).is_dir() else None
    guid = _KNOWN_GUIDS.get(key)
    if not guid:
        return None
    try:
        buf = ctypes.c_wchar_p()
        hr = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.c_char_p(uuid.UUID(guid).bytes_le), 0, None, ctypes.byref(buf))
        if hr == 0 and buf.value:
            p = Path(buf.value)
            ctypes.windll.ole32.CoTaskMemFree(buf)
            if p.is_dir():
                return p
    except Exception:
        pass
    fallback = Path.home() / key.capitalize()
    return fallback if fallback.is_dir() else None


# ---------------------------------------------------------------- the index

# Never worth descending into: system folders, caches, package trees. Kept
# out of both indexes so "open notes" cannot land on a game's notes.txt
# three levels inside Program Files.
_SKIP_DIRS = {"node_modules", "appdata", "$recycle.bin", "venv", ".venv",
              "__pycache__", "site-packages", "dist-packages", "windows",
              "windows.old", "program files", "program files (x86)",
              "programdata", "system volume information", "recovery",
              "perflogs", "msys64", "crossdevice", "application data",
              "local settings", "cookies", "nethood", "printhood", "recent",
              "sendto", "start menu", "templates", "my documents",
              "onedrivetemp", "config.msi", ".git", ".cache",
              # Game and driver installs: thousands of asset files with
              # ordinary words for names (scroll_bar.png, youtube.png).
              "xboxgames", "steamapps", "riot games", "driver", "drivers",
              "epic games", "windowsapps"}
_SKIP_EXT = {".pyc", ".tmp", ".ini", ".crdownload", ".part"}
_MAX_ENTRIES = 250000


@dataclass
class Entry:
    path: str
    name: str
    is_dir: bool
    mtime: float
    rank: int            # 0 = the user's own folders, 1 = elsewhere on a drive
    tokens: tuple[str, ...]
    compact: str         # tokens joined without separators
    ext: str = ""        # "pdf", so "hello cpp" can name hello.cpp
    first_num: str = ""  # "6" for "Experiment 6.1"


def _personal_roots() -> list[tuple[Path, int]]:
    """(folder, depth) for the fast index: where people keep their files."""
    out: list[tuple[Path, int]] = []
    for key in ("desktop", "downloads", "documents", "pictures", "videos",
                "music", "onedrive"):
        p = known_folder(key)
        if p:
            out.append((p, 5))
    # The home folder itself is mostly AppData and dot-folders; the parts
    # worth finding are the known folders above. Two levels catch loose
    # files like ~/meeting_notes.txt without walking half the disk.
    out.append((Path.home(), 2))
    return out


def local_drives() -> list[Path]:
    from ..config import local_drives as _drives
    return _drives()


def _wide_roots() -> list[tuple[Path, int]]:
    """Every local drive, for what the fast index does not have. Depth 8 over
    all of C: here is ~15k entries in 0.6 s (measured) once the skip list
    prunes Windows, Program Files and AppData."""
    return [(d, 8) for d in local_drives()]


_WORD_NUM = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
             "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
             "ten": "10", "eleven": "11", "twelve": "12", "first": "1",
             "second": "2", "third": "3", "fourth": "4", "fifth": "5"}
_STOP = {"the", "a", "an", "of", "and", "for", "to", "in", "on", "my", "&"}
_COPY_MARK = re.compile(r"\s*\(\d+\)$")


def tokens(text: str) -> list[str]:
    """Lower-case words, split at punctuation and letter/digit boundaries.

    "PBL1_Geophone_Siddharth (1)" -> pbl 1 geophone siddharth 1
    """
    low = text.lower()
    low = re.sub(r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])", " ", low)
    return [t for t in re.split(r"[^a-z0-9]+", low) if t]


def _entry(path: str, name: str, is_dir: bool, mtime: float, rank: int) -> Entry:
    stem = name if is_dir else os.path.splitext(name)[0]
    # "scheduler (2).pdf" is a browser's copy of scheduler.pdf, not a
    # different file with an extra word in it - do not penalise the marker.
    stem = _COPY_MARK.sub("", stem)
    toks = tuple(t for t in tokens(stem) if t not in _STOP)
    ext = "" if is_dir else os.path.splitext(name)[1].lower().lstrip(".")
    first_num = next((t for t in toks if t.isdigit()), "")
    return Entry(path, name, is_dir, mtime, rank, toks, "".join(toks), ext, first_num)


def _walk(roots: list[tuple[Path, int]], rank: int,
          exclude: set[str] = frozenset(), budget: float = 20.0) -> list[Entry]:
    entries: list[Entry] = []
    seen: set[str] = set()
    deadline = time.time() + budget
    for root, max_depth in roots:
        stack = [(str(root), 0)]
        while stack and len(entries) < _MAX_ENTRIES and time.time() < deadline:
            folder, depth = stack.pop()
            try:
                it = os.scandir(folder)
            except OSError:
                continue
            with it:
                for de in it:
                    name = de.name
                    if name.startswith((".", "~$", "$")) or name.lower() == "desktop.ini":
                        continue
                    key = de.path.lower()
                    if key in seen or key in exclude:
                        continue
                    try:
                        is_dir = de.is_dir(follow_symlinks=False)
                        mtime = de.stat(follow_symlinks=False).st_mtime
                    except OSError:
                        continue
                    if is_dir:
                        if name.lower() in _SKIP_DIRS:
                            continue
                    elif os.path.splitext(name)[1].lower() in _SKIP_EXT:
                        continue
                    seen.add(key)
                    entries.append(_entry(de.path, name, is_dir, mtime, rank))
                    if is_dir and depth + 1 < max_depth:
                        stack.append((de.path, depth + 1))
    return entries


class _Index:
    """A list of entries rebuilt in the background once it is stale.

    Built synchronously only when there is none at all (or `fresh` is asked
    for, after a miss); otherwise a stale one is served while a new one
    builds behind it."""

    def __init__(self, build, max_age: float):
        self._build = build
        self._max_age = max_age
        self._entries: list[Entry] = []
        self.built_at = 0.0
        self._lock = threading.Lock()
        self._building = threading.Event()

    def _rebuild(self) -> None:
        try:
            idx = self._build()
            with self._lock:
                self._entries, self.built_at = idx, time.time()
        finally:
            self._building.clear()

    def warm(self) -> None:
        if self._building.is_set():
            return
        self._building.set()
        threading.Thread(target=self._rebuild, daemon=True,
                         name="file-index").start()

    def get(self, fresh: bool = False, wait: float = 30.0) -> list[Entry]:
        with self._lock:
            idx, age = self._entries, time.time() - self.built_at
        if idx and not fresh:
            if age > self._max_age:
                self.warm()
            return idx
        if self._building.is_set():
            # A build is under way - wait for it rather than walking the same
            # folders twice.
            deadline = time.time() + wait
            while self._building.is_set() and time.time() < deadline:
                time.sleep(0.05)
            with self._lock:
                if self._entries and (not fresh or self.built_at > time.time() - 5):
                    return self._entries
        self._building.set()
        self._rebuild()
        with self._lock:
            return self._entries


def _build_personal() -> list[Entry]:
    return _walk(_personal_roots(), rank=0)


def _build_wide() -> list[Entry]:
    # The user's folders are already in the fast index; the home folder is
    # only there two levels deep, so the rest of it is walked here.
    home = Path.home()
    exclude = {str(p).lower() for p, _d in _personal_roots() if p != home}
    return _walk(_wide_roots(), rank=1, exclude=exclude)


_PERSONAL = _Index(_build_personal, max_age=60.0)
_WIDE = _Index(_build_wide, max_age=600.0)


def warm() -> None:
    """Start building both indexes in the background."""
    _PERSONAL.warm()
    _WIDE.warm()


# ---------------------------------------------------------------- queries

# Spoken type hints -> extensions. "the scheduler pdf", "resume word file".
_TYPE_HINTS: list[tuple[str, set[str]]] = [
    (r"\bpdf\b", {".pdf"}),
    (r"\bword\s+(?:file|document|doc)\b|\bdocx?\b", {".docx", ".doc", ".rtf"}),
    (r"\bpower\s*point\b|\bpptx?\b|\bslides?\b|\bslide\s+deck\b|\bdeck\b",
     {".pptx", ".ppt"}),
    (r"\bexcel\s+(?:file|sheet|workbook)?\b|\bspreadsheet\b|\bxlsx?\b|\bcsv\b",
     {".xlsx", ".xls", ".xlsm", ".csv"}),
    (r"\b(?:image|picture|photo|png|jpe?g)\b",
     {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".heic"}),
    (r"\b(?:video|mp4|clip)\b", {".mp4", ".mkv", ".mov", ".avi", ".webm"}),
    (r"\btext\s+file\b|\btxt\b", {".txt", ".md"}),
    (r"\bzip\b|\barchive\b", {".zip", ".rar", ".7z"}),
    (r"\bspice\s+file\b|\bltspice\s+file\b|\bschematic\b", {".asc"}),
    (r"\bhtml\b|\bweb\s*page\b", {".html", ".htm"}),
]

_KNOWN_EXT = {e for _p, exts in _TYPE_HINTS for e in exts} | {
    ".asc", ".py", ".ipynb", ".blend", ".json", ".md", ".exe", ".msi", ".lnk",
    ".docm", ".xlsm", ".c", ".cpp", ".java", ".m", ".mat", ".url", ".rmskin"}

_FILLER = re.compile(
    r"^(?:(?:the|my|a|an|that|this|our)\s+)+|"
    r"\s+(?:file|document|doc|folder|please|for me|now)$", re.I)


@dataclass
class Query:
    words: list[str]           # what the name must contain
    exts: set[str] | None      # wanted types, from a hint or an extension
    want_dir: bool | None
    raw: str


def parse(text: str) -> Query:
    """Split "the scheduler pdf" into words to match and a wanted type."""
    raw = text.strip().strip(" .?!\"'")
    q = raw.lower()
    # Whisper writes an extension out as a word.
    q = re.sub(r"\s+dot\s+([a-z0-9]{1,5})\b", r".\1", q)
    want_dir: bool | None = None
    if re.search(r"\b(?:folder|directory)$", q):
        want_dir = True
        q = re.sub(r"\s*\b(?:folder|directory)$", "", q)

    exts: set[str] | None = None
    m = re.search(r"(\.[a-z0-9]{1,5})$", q)
    if m and m.group(1) in _KNOWN_EXT:
        exts = {m.group(1)}
        if m.group(1) in (".doc", ".docx"):
            exts = {".doc", ".docx"}
        q = q[: m.start()]
    else:
        for pattern, e in _TYPE_HINTS:
            hm = re.search(pattern, q)
            if not hm:
                continue
            rest = (q[: hm.start()] + " " + q[hm.end():]).strip()
            rest_words = [w for w in tokens(rest) if w not in _STOP
                          and w not in ("file", "files", "document")]
            # "open the screenshot" - the hint is the only word, so it is
            # the name, not a type.
            if rest_words:
                exts = set(e)
                q = rest
            break
    if exts:
        want_dir = False

    prev = None
    while prev != q:
        prev = q
        q = _FILLER.sub("", q.strip()).strip()
    words = [(_WORD_NUM.get(w, w)) for w in tokens(q)]
    words = [w for w in words if w not in _STOP and w not in ("file", "files")]
    return Query(words, exts, want_dir, raw)


def _word_score(w: str, e: Entry) -> float:
    if w.isdigit():
        # "experiment one" is Experiment 1, not Experiment 6.1 - a spoken
        # number is the file's number, so it has to be the first one in the
        # name to count fully.
        if w == e.first_num:
            return 1.0
        return 0.5 if w in e.tokens else 0.0
    if w in e.tokens:
        return 1.0
    if w == e.ext:
        return 1.0             # "hello cpp", "config yaml"
    if len(w) >= 3 and any(t.startswith(w) for t in e.tokens):
        return 0.85            # "prob" -> probability, "sched" -> scheduler
    if len(w) >= 4 and w in e.compact and any(
            "".join(e.tokens[i:]).startswith(w) for i in range(len(e.tokens))):
        # Run-together names: "smarthome" for smart-home. Only from the start
        # of a word - "phone" must not match inside "geophone".
        return 0.8
    if len(w) >= 4:
        # Only worth the SequenceMatcher on plausible words: this runs over
        # ~15k entries for a miss, and the unfiltered version took 1.4 s.
        for t in e.tokens:
            if abs(len(t) - len(w)) > 2 or len(t) < 4 or (t[0] != w[0] and t[-1] != w[-1]):
                continue
            sm = SequenceMatcher(None, w, t)
            if sm.quick_ratio() >= 0.8 and sm.ratio() >= 0.8:
                return 0.7     # speech: "sheduler", "experiments"
    return 0.0


@dataclass
class Match:
    entry: Entry
    coverage: float    # mean per-word score, 0..1
    full: bool         # every spoken word found
    extra: int         # words in the name that were not asked for
    scores: tuple = ()  # per spoken word
    near_score: float = 0.0

    def key(self) -> tuple:
        """Sort key without mtime - equal keys are 'equally good'."""
        return (self.full, round(self.coverage, 2), -min(self.extra, 8),
                -self.entry.rank, not self.entry.is_dir)


def _score(q: Query, e: Entry) -> Match | None:
    if not q.words:
        return None
    joined = "".join(q.words)
    if len(q.words) > 1 and len(joined) >= 5 and joined in e.compact:
        scores = [1.0] * len(q.words)       # "power point" vs "PowerPoint"
    else:
        scores = [_word_score(w, e) for w in q.words]
    hits = sum(1 for s in scores if s > 0)
    if hits == 0:
        return None
    matched = sum(1 for t in e.tokens
                  if any(t == w if w.isdigit() else (t.startswith(w) or w in t)
                         for w in q.words))
    return Match(e, sum(scores) / len(scores), hits == len(scores),
                 max(0, len(e.tokens) - matched), tuple(scores))


@dataclass
class Result:
    query: Query
    best: Match | None       # the one to open, if confident
    ties: list[Match]        # equally good, older than best
    near: list[Match]        # closest names, for "did you mean"

    @property
    def confident(self) -> bool:
        return self.best is not None


def _matches(q: Query, entries: list[Entry]) -> list[Match]:
    out: list[Match] = []
    for e in entries:
        if q.want_dir is True and not e.is_dir:
            continue
        if q.want_dir is False and e.is_dir:
            continue
        m = _score(q, e)
        if m:
            out.append(m)
    if q.exts:
        typed = [m for m in out
                 if os.path.splitext(m.entry.name)[1].lower() in q.exts]
        # A wrong type hint ("the pdf" when it is a docx) should not hide the
        # file altogether - only prefer the right type when there is one.
        if any(m.full for m in typed):
            out = typed
    out.sort(key=lambda m: (m.key(), m.entry.mtime), reverse=True)
    return out


def _pick(q: Query, matches: list[Match], strict: bool = False) -> Result:
    if not matches:
        return Result(q, None, [], [])
    top = matches[0]
    ok = top.full and top.coverage >= 0.7
    if strict and ok:
        # Outside the user's own folders the disk is full of other people's
        # files. A misheard "open bar, sir" reached open_file("bar") and
        # opened scroll_bar.png from a Roblox texture folder in Photos. Out
        # there, every word must match exactly and the name must be (almost)
        # nothing but those words.
        exact = all(sc == 1.0 for sc in top.scores)
        limit = 0 if len(q.words) == 1 else 1
        ok = exact and top.extra <= limit
    if not ok:
        return Result(q, None, [], _closest(q, matches))
    ties = [m for m in matches[1:] if m.key() == top.key()]
    rest = [m for m in matches[1:] if m.key() != top.key()][:3]
    return Result(q, top, ties, rest)


def find(text: str, want_dir: bool | None = None, refresh: bool = True,
         wide_wait: float = 8.0) -> Result:
    """Best file (or folder) for a spoken name.

    Looks in the user's own folders first, then everywhere else on the local
    drives. `refresh` re-reads the user's folders after a miss (a file
    downloaded seconds ago); `wide_wait` is how long to wait for the
    all-drives index if it is not built yet - 0 means use it only if ready,
    which is what the router does so that a non-file "open youtube" never
    waits on the disk.
    """
    q = parse(text)
    if want_dir is not None:
        q.want_dir = want_dir
    if not q.words:
        return Result(q, None, [], [])

    near: list[Match] = []
    personal = _matches(q, _PERSONAL.get())
    r = _pick(q, personal)
    if r.confident:
        return r
    if refresh and _PERSONAL.built_at < time.time() - 5:
        personal = _matches(q, _PERSONAL.get(fresh=True))
        r = _pick(q, personal)
        if r.confident:
            return r
    near += r.near

    if wide_wait > 0 or _WIDE.built_at:
        wide_entries = (_WIDE.get(wait=wide_wait) if wide_wait > 0
                        else _WIDE.get() if _WIDE.built_at else [])
        w = _pick(q, _matches(q, wide_entries), strict=True)
        if w.confident:
            return w
        near += w.near
    near.sort(key=lambda m: (m.near_score, -m.entry.rank, m.entry.mtime),
              reverse=True)
    return Result(q, None, [], near[:4])


def find_all(text: str, limit: int = 8, want_dir: bool | None = False) -> list[Entry]:
    """Every file whose name really matches, best first - for "open all my
    signals and systems files", where find() picks one and hides the rest.

    Same bar as find() for the one it would open: every spoken word found and
    a good average score. The user's own folders are enough when they have
    any; elsewhere on the drives the strict rule applies (see _pick).
    """
    q = parse(text)
    if want_dir is not None and q.want_dir is None:
        q.want_dir = want_dir
    if not q.words:
        return []
    good = [m for m in _matches(q, _PERSONAL.get(fresh=_PERSONAL.built_at < time.time() - 5))
            if m.full and m.coverage >= 0.7]
    if not good and _WIDE.built_at:
        good = [m for m in _matches(q, _WIDE.get())
                if m.full and all(s == 1.0 for s in m.scores) and m.extra <= 3]
    seen, out = set(), []
    for m in good:                       # already sorted best first, newest first
        key = m.entry.path.lower()
        if key not in seen:
            seen.add(key)
            out.append(m.entry)
        if len(out) >= limit:
            break
    return out


def _closest(q: Query, matches: list[Match], n: int = 4) -> list[Match]:
    """The nearest misses, rarest words first.

    "geophone report" matched nothing fully, and a plain coverage sort put
    Report.png above PBL1_Geophone_Siddharth.pdf - both hit one word of two,
    but "report" is in dozens of names and "geophone" in three. Weighting
    each word by how rare it is among the candidates (idf) lists the one
    the user almost certainly meant first.
    """
    import math

    total = len(matches) + 1
    df = [sum(1 for m in matches if len(m.scores) > i and m.scores[i] > 0)
          for i in range(len(q.words))]
    weights = [math.log(total / (1 + d)) + 1 for d in df]
    wsum = sum(weights) or 1.0
    for m in matches:
        m.near_score = (sum(w * sc for w, sc in zip(weights, m.scores)) / wsum
                        - 0.01 * min(m.extra, 8))
    ranked = sorted(matches, key=lambda m: (m.near_score, m.entry.mtime),
                    reverse=True)
    return ranked[:n]
