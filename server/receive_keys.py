"""The server's half of send_keys.py (run there over SSH, not by hand).

Keys go to data/api_keys.json, not Windows Credential Manager: the startup
task runs with nobody signed in, and then Windows can't unlock Credential
Manager. Instead data/ is locked to your account (plus SYSTEM and the
Administrators group, which Windows itself needs).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jarvis.cloud import KEYS_FILE  # noqa: E402
from jarvis.config import DATA  # noqa: E402
from jarvis.store import load_json, save_json  # noqa: E402

ALLOWED = {"web_token.txt", "phone.json", "telegram.json", "google_token.json", "feeds.json",
           "timetable.json", "config.local.yaml"}


def main() -> int:
    incoming = DATA / "incoming.json"
    try:
        got = json.loads(incoming.read_text(encoding="utf-8"))
    finally:
        incoming.unlink(missing_ok=True)
    save_json(KEYS_FILE, {**load_json(KEYS_FILE, {}), **got.get("keys", {})})
    for name, text in got.get("files", {}).items():
        if name in ALLOWED:                    # a name, never a path
            (DATA / name).write_text(text, encoding="utf-8")
    me = os.environ.get("USERNAME", "")
    # The folder gets the three, then everything in it inherits them. (Stripping
    # inheritance on the files too, with /T, left them readable by no one.)
    r = subprocess.run(["icacls", str(DATA), "/inheritance:r", "/grant:r", f"{me}:(OI)(CI)F",
                        "/grant:r", "*S-1-5-18:(OI)(CI)F", "/grant:r", "*S-1-5-32-544:(OI)(CI)F", "/Q"],
                       capture_output=True, text=True)
    if r.returncode == 0:
        r = subprocess.run(["icacls", str(DATA / "*"), "/reset", "/T", "/Q"], capture_output=True, text=True)
    print(f"server: saved {len(got.get('keys', {}))} keys and {len(got.get('files', {}))} files; "
          f"data folder {'locked to ' + me if r.returncode == 0 else 'NOT locked: ' + r.stderr.strip()[:120]}")
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
