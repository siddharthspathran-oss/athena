"""Daily backup of what makes Athena yours, to OneDrive (9 Oct 2026).

data/ is not in git and holds what cannot be downloaded again: memory and the
profile she learned, chats, rules, contacts, tasks, reminders, habits, the
document and conversation index (athena.db), study answers, the wake-word
recordings. Once a day it all goes into one zip in OneDrive (backup.dest);
the last backup.keep zips are kept.

What is never copied: secrets and sign-ins - Google tokens, API keys, the
UPES session, the web interface's token, your phone push topic, browser
profiles - and what can be rebuilt (models, caches, logs, screenshots).
Restore: unzip into data/ with Athena stopped.
"""
from __future__ import annotations

import datetime as dt
import re
import sqlite3
import tempfile
import threading
import time
import zipfile
from pathlib import Path

from .config import DATA, cfg, expand
from .registry import tool

# Never leaves the PC, whatever else changes.
SECRET = re.compile(r"token|key|credential|session|secret|password|cookie|^phone\.json$|^shares\.json$", re.I)
FILES = re.compile(r".+\.(?:json|jsonl|md|yaml|onnx)$|^athena\.db$", re.I)
SKIP = re.compile(r"^(?:live_voice|lms_cache|upes_cache|codegraph|index_mtimes|upes_api_seen)\.json$|\.tmp$", re.I)
FOLDERS = ["chats", "chats_trash", "answers", "wake_mine", "wake_room", "farfield"]


def dest() -> Path:
    return expand(cfg.get("backup.dest", "~/OneDrive/Athena Backup"))


def _wanted() -> list[Path]:
    out = [p for p in DATA.iterdir() if p.is_file() and FILES.match(p.name)
           and not SECRET.search(p.name) and not SKIP.search(p.name)]
    for d in FOLDERS:
        if (DATA / d).is_dir():
            out += [p for p in (DATA / d).rglob("*") if p.is_file() and not SECRET.search(p.name)]
    return out


def run() -> Path:
    """One zip of today's state; returns its path."""
    d = dest()
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"athena-{dt.date.today().isoformat()}.zip"
    tmp = path.with_suffix(".part")
    with tempfile.TemporaryDirectory() as td, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for p in _wanted():
            if p.name == "athena.db":
                # SQLite's own copy: a file copy of a database in use can be torn.
                snap = Path(td) / "athena.db"
                src, dst = sqlite3.connect(p), sqlite3.connect(snap)
                try:
                    src.backup(dst)
                finally:
                    src.close()
                    dst.close()                  # ("with" only commits; an open file can't be zipped and removed)
                z.write(snap, "athena.db")
            else:
                z.write(p, p.relative_to(DATA).as_posix())
    tmp.replace(path)
    keep = int(cfg.get("backup.keep", 7))
    for old in sorted(d.glob("athena-*.zip"))[:-keep]:
        old.unlink(missing_ok=True)          # only Athena's own backup zips
    return path


@tool(tags=["system"], timeout=300)
def backup_now() -> str:
    """Back up Athena's memory, chats, rules, contacts, tasks and indexes to
    OneDrive now (secrets and sign-ins are never included). Use for "back up
    athena", "backup now", "when was the last backup"."""
    try:
        p = run()
    except Exception as e:
        return f"ERROR: the backup failed ({e})."
    return f"Backed up to {p.parent.name}\\{p.name} ({p.stat().st_size / 1e6:.0f} MB). Secrets and sign-ins were left out."


def start() -> None:
    """Once a day, after backup.after (2 am) - the first time the PC is on then."""
    def loop():
        while True:
            time.sleep(600)
            try:
                if not cfg.get("backup.enabled", True):
                    continue
                today = dest() / f"athena-{dt.date.today().isoformat()}.zip"
                if not today.exists() and time.strftime("%H:%M") >= str(cfg.get("backup.after", "02:00")):
                    p = run()
                    from . import rules
                    rules.log(f"backup: {p.name} ({p.stat().st_size / 1e6:.0f} MB)", "backup")
            except Exception as e:
                print(f"[backup: {e}]")
    threading.Thread(target=loop, daemon=True, name="athena-backup").start()


if __name__ == "__main__":
    names = [p.name for p in _wanted()]
    leaked = [n for n in names if SECRET.search(n)]
    assert not leaked, leaked
    for must_not in ("google_token.json", "api_keys.json", "upes_session.json", "web_token.txt", "phone.json"):
        assert must_not not in names, must_not
    for must in ("memory.json", "profile.md", "contacts.json", "rules.json", "tasks.json", "athena.db"):
        assert must in names, must
    print(len(names), "files would be backed up; no secrets")
