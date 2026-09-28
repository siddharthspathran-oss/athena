"""Opening files and folders the way people ask for them, and what the file
tools may change.

    python tests/filestest.py

"athena still cant access the file explorer and open any file i tell me":
"open my downloads" came back "(no response)", "open documents folder" went
to open_app and failed, "open resume.docx" had the model guess
C:/Users/Siddharth/resume.docx and then call `remember`. These cases pin the
routing, the spoken-name matching (on a throwaway folder tree, not the real
disk) and the read-anywhere / change-outside-system-folders rule.

Nothing is really opened: os.startfile, explorer.exe and send2trash are all
stubbed out.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jarvis.tools  # noqa: E402,F401  registers every tool
from jarvis import agent as A  # noqa: E402
from jarvis import config  # noqa: E402
from jarvis.tools import files, finder  # noqa: E402

# (said, tool it must route to or None for "not mine", key arg or None)
# Only the folder/file routers are exercised here, so the result does not
# depend on which apps happen to be installed.
FOLDER_ROUTING = [
    ("open file explorer", "open_folder", "explorer"),
    ("Open file explorer.", "open_folder", "explorer"),
    ("open explorer", "open_folder", "explorer"),
    ("athena open the file explorer", "open_folder", "explorer"),
    ("open my downloads", "open_folder", "downloads"),
    ("Open my downloads.", "open_folder", "downloads"),
    ("open downloads folder", "open_folder", "downloads"),
    ("open documents folder", "open_folder", "documents"),
    ("open my documents", "open_folder", "documents"),
    ("open desktop", "open_folder", "desktop"),
    ("open the pictures folder", "open_folder", "pictures"),
    ("open the music folder", "open_folder", "music"),
    ("go to downloads", "open_folder", "downloads"),
    ("open the ltspice folder", "open_folder", "ltspice"),
    ("show me the file scheduler in explorer", "reveal_path", "scheduler"),
    ("show scheduler.pdf in file explorer", "reveal_path", "scheduler.pdf"),
    ("where is the scheduler pdf", "reveal_path", "scheduler pdf"),
    ("where is scheduler", "reveal_path", "scheduler"),
    # Not folders: must be left for the app router or the model.
    ("open music", None, None),          # Spotify, by alias
    ("show desktop", None, None),         # the minimise-all gesture
    ("where is my phone", None, None),
    ("open spotify", None, None),
    ("open chrome", None, None),
    ("open whatsapp", None, None),
    ("open the camera", None, None),
    ("close file explorer", None, None),
    ("explain kirchhoff voltage law", None, None),
]

FILE_ROUTING = [
    ("open the scheduler pdf", "open_file", "the scheduler pdf"),
    ("Open the scheduler PDF.", "open_file", "the scheduler PDF"),
    ("open probability assignment", "open_file", "probability assignment"),
    ("open resume.docx", "open_file", "resume.docx"),
    ("open scheduler dot pdf", "open_file", "scheduler dot pdf"),
    ("open experiment one", "open_file", "experiment one"),  # real file in tree
    ("open scheduler", "open_file", "scheduler"),            # real file in tree
    ("athena, open the signals and systems notes", "open_file",
     "the signals and systems notes"),
    # Not files.
    ("open youtube", None, None),
    ("open spotify", None, None),
    ("open whatsapp", None, None),
    ("open the camera", None, None),
    ("open chrome and play music", None, None),
    ("open a new word document", None, None),
    ("open google.com", None, None),
    ("open setup", None, None),                 # only an installer matches
    ("explain the scheduler algorithm", None, None),
]

# (spoken name, file name the best match must have, or None for no match)
MATCHING = [
    ("the scheduler pdf", "scheduler (1).pdf"),     # newest of equal copies
    ("scheduler", "scheduler (1).pdf"),
    ("sheduler", "scheduler (1).pdf"),              # misheard
    ("scheduler two", "scheduler2.pdf"),
    ("experiment one", "Experiment 1 - A Student.docx"),   # not 6.1
    ("experiment 6.1", "Experiment 6.1.docx"),
    ("experiment 6.1.pdf", "Experiment 6.1.docx"),  # wrong type said
    ("signals and systems notes", "Signals_and_Systems_Unit_1_Notes.pdf"),
    ("the course plan word file", "Course Plan Theory.docx"),
    ("course plan pdf", "Course Plan Digital System Design.pdf"),
    ("geophone", "PBL1_Geophone_Siddharth.pdf"),
    ("power point", "PowerPoint Notes.pptx"),
    ("hello cpp", "hello.cpp"),
    ("far away report", "far_away_report.pdf"),     # other drive, wide index
    ("probability assignment", None),               # two half-matches only
    ("resume", None),
    ("secret dll", None),                            # inside Windows\: skipped
    # Off the user's folders a loose match is not enough: a misheard "open
    # bar, sir" opened a Roblox texture, scroll_bar.png, in Photos.
    ("bar", None),
]


def _tree(root: Path) -> None:
    """A small fake home: Downloads, Documents, Desktop and another drive."""
    now = time.time()
    files_ = {
        "Downloads/scheduler.pdf": now - 3000,
        "Downloads/scheduler (1).pdf": now - 100,
        "Downloads/scheduler2.pdf": now - 50,
        "Downloads/Course Plan Digital System Design.pdf": now,
        "Downloads/Course Plan Theory.docx": now,
        "Downloads/Install Setup.exe": now,
        "Downloads/Probability and Statistics.pdf": now,
        "Downloads/Assignment 1_MATH 2065.pdf": now,
        "Documents/LTspice/Experiment 6.1.docx": now,
        "Documents/Experiment 1 - A Student.docx": now - 10,
        "Documents/PBL1_Geophone_Siddharth.pdf": now,
        "Documents/PowerPoint Notes.pptx": now,
        "Documents/code/hello.cpp": now,
        "Desktop/Signals_and_Systems_Unit_1_Notes.pdf": now,
        "OtherDrive/projects/deep/far_away_report.pdf": now,
        "OtherDrive/Windows/System32/secret.dll": now,
        "OtherDrive/games/ui/textures/scroll_bar.png": now,
    }
    for rel, mtime in files_.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")
        os.utime(p, (mtime, mtime))


def _use_tree(root: Path) -> None:
    finder._personal_roots = lambda: [(root / "Downloads", 5),
                                      (root / "Documents", 5),
                                      (root / "Desktop", 5)]
    finder._wide_roots = lambda: [(root / "OtherDrive", 8)]
    finder._PERSONAL = finder._Index(finder._build_personal, max_age=600)
    finder._WIDE = finder._Index(finder._build_wide, max_age=600)
    real_known = finder.known_folder

    def known(key: str):
        k = finder.FOLDER_WORDS.get(key.strip().lower(), key.strip().lower())
        sub = {"downloads": "Downloads", "documents": "Documents",
               "desktop": "Desktop"}.get(k)
        return root / sub if sub else real_known(key)
    finder.known_folder = known


def main() -> int:
    bad = 0
    total = 0

    def check(ok: bool, label: str, detail: str = "") -> None:
        nonlocal bad, total
        total += 1
        bad += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {label}" + (f"   ({detail})" if not ok and detail else ""))

    tmp = Path(tempfile.mkdtemp(prefix="jarvis-files-"))
    started: list[str] = []
    explored: list[tuple] = []
    trashed: list[str] = []
    try:
        _tree(tmp)
        _use_tree(tmp)
        files._start = lambda p: started.append(str(p))
        files._explorer = lambda *a: explored.append(a)
        files._handler = lambda suffix: "Reader" if suffix else None

        # --- routing
        for said, want_tool, want_arg in FOLDER_ROUTING:
            got = A._direct_folder(said)
            tool = got[0] if got else None
            arg = (got[1].get("name") or got[1].get("path")) if got else None
            ok = tool == want_tool and (want_arg is None or arg == want_arg)
            check(ok, f"folder {said:44} -> {tool} {arg or ''}",
                  f"wanted {want_tool} {want_arg or ''}")
        for said, want_tool, want_arg in FILE_ROUTING:
            got = A._direct_file(said)
            tool = got[0] if got else None
            arg = got[1].get("path") if got else None
            ok = tool == want_tool and (want_arg is None or arg == want_arg)
            check(ok, f"file   {said:44} -> {tool} {arg or ''}",
                  f"wanted {want_tool} {want_arg or ''}")
        # A file named exactly this beats an app that only contains the word
        # (Task Scheduler), but an app's own name is never taken over.
        check(A._exact_personal_file("scheduler"), "exact  scheduler is a file")
        check(not A._exact_personal_file("chrome"), "exact  chrome is not a file")

        # --- matching
        for said, want in MATCHING:
            t = time.time()
            r = finder.find(said, wide_wait=5)
            ms = (time.time() - t) * 1000
            name = r.best.entry.name if r.confident else None
            check(name == want, f"match  {said:32} -> {name}  [{ms:.0f} ms]",
                  f"wanted {want}; near {[m.entry.name for m in r.near]}")

        # --- the tools, with nothing really opened
        started.clear()
        out = files.open_file("the scheduler pdf")
        check(started == [str(tmp / "Downloads" / "scheduler (1).pdf")]
              and "scheduler.pdf" in out and "newest" in out,
              f"open_file newest copy, names the others: {out}")
        started.clear()
        out = files.open_file("resume.docx")
        check(not started and "couldn't find" in out and not out.startswith("ERROR"),
              f"open_file missing file says so: {out}")
        out = files.open_file("probability assignment")
        check(not started and "Closest" in out and "Probability and Statistics.pdf" in out,
              f"open_file offers closest names: {out}")
        out = files.open_file(r"C:/Users/Nobody/course plan pdf")
        check(started == [str(tmp / "Downloads" / "Course Plan Digital System Design.pdf")],
              f"open_file recovers from an invented path: {out}")
        explored.clear()
        out = files.open_folder("explorer")
        check(explored == [()], f"open_folder explorer launches explorer.exe: {out}")
        explored.clear()
        out = files.open_folder("downloads")
        check(explored == [(str(tmp / "Downloads"),)], f"open_folder downloads: {out}")
        explored.clear()
        out = files.open_folder("ltspice")
        check(explored == [(str(tmp / "Documents" / "LTspice"),)], f"open_folder by name: {out}")
        explored.clear()
        out = files.reveal_path("geophone")
        check(explored == [("/select,", str(tmp / "Documents" / "PBL1_Geophone_Siddharth.pdf"))],
              f"reveal_path selects the file: {out}")

        # --- read anywhere, change only outside system folders
        real_drives = config.local_drives
        config.local_drives = lambda: [Path("C:\\"), Path("D:\\")]
        try:
            check(config.is_path_allowed(r"D:\Movies\film.mkv"), "safety read on another drive (D:)")
            check(config.is_path_allowed(r"D:\notes\a.txt", write=True), "safety change on D: allowed")
            check(config.is_path_allowed(r"C:\Windows\win.ini"), "safety read inside C:\\Windows")
            check(not config.is_path_allowed(r"C:\Windows\System32\drivers\etc\hosts", write=True),
                  "safety change inside C:\\Windows refused")
            check(not config.is_path_allowed(r"C:\Program Files\App\x.dll", write=True),
                  "safety change inside Program Files refused")
            check(not config.is_path_allowed(r"c:\program files (x86)\App", write=True),
                  "safety change inside Program Files (x86) refused")
            check(not config.is_path_allowed(r"C:\ProgramData\x", write=True),
                  "safety change inside ProgramData refused")
            check(not config.is_path_allowed(str(Path.home() / "AppData" / "Roaming" / "x"), write=True),
                  "safety change inside AppData refused")
            check(not config.is_path_allowed("D:\\", write=True), "safety change a drive root refused")
            check(config.is_path_allowed(r"C:\somefolder\a.txt", write=True),
                  "safety change in C:\\somefolder allowed")
            check(not config.is_path_allowed(r"Q:\x.txt"), "safety a drive that is not there")
        finally:
            config.local_drives = real_drives

        sys.modules["send2trash"] = types.SimpleNamespace(
            send2trash=lambda p: trashed.append(p))
        out = files.delete_file(r"C:\Windows\win.ini")
        check(not trashed and "protected" in out, f"delete in C:\\Windows refused: {out}")
        pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        victim = next((str(p) for p in Path(pf).iterdir()), pf + r"\x")
        out = files.delete_file(victim)
        check(not trashed and "protected" in out, f"delete in Program Files refused: {out}")
        out = files.move_file(r"C:\Windows\win.ini", r"C:\somefolder")
        check("protected" in out, f"move out of C:\\Windows refused: {out}")
        # C:\somefolder stands in for any ordinary folder outside the user's
        # profile - kept inside this project so the test leaves nothing on C:\.
        somefolder = Path(__file__).resolve().parent.parent / "data" / "filestest_somefolder"
        somefolder.mkdir(parents=True, exist_ok=True)
        (somefolder / "a.txt").write_text("hello", encoding="utf-8")
        try:
            out = files.delete_file(str(somefolder / "a.txt"))
            check(trashed == [str(somefolder / "a.txt")] and "Recycle Bin" in out,
                  f"delete elsewhere goes to the Recycle Bin: {out}")
            out = files.read_file(r"C:\Windows\win.ini")
            check(not out.startswith("ERROR"), f"read inside C:\\Windows allowed: {out[:40]!r}")
        finally:
            shutil.rmtree(somefolder, ignore_errors=True)
    finally:
        sys.modules.pop("send2trash", None)
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{total} cases, {bad} failure(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
