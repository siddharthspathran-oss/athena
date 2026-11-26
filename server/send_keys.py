"""Give athena-server what it needs from this laptop: run this yourself, once
(and again after you change a key):

    python server/send_keys.py

It copies the brain, Telegram and Wolfram keys, the shared web token, your
phone-alert and Telegram links, your timetable and calendar feeds, and the
Google sign-in (for the morning brief and the mail alerts). Nothing is shown
on screen: it goes straight over Tailscale (SSH) into C:\\athena\\data on the
server, which only your account there can read.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jarvis import cloud  # noqa: E402
from jarvis.config import DATA, cfg  # noqa: E402

HOST = str(cfg.get("server.ssh", ""))       # user@address, in data/config.local.yaml (private)
SSH = ["-i", str(Path.home() / ".ssh" / "athena_imac"), "-o", "BatchMode=yes", "-o", "LogLevel=ERROR"]
KEYS = ["nvidia", "groq", "gemini", "telegram", "wolfram"]
FILES = ["web_token.txt", "phone.json", "telegram.json", "google_token.json", "feeds.json",
         "timetable.json", "config.local.yaml"]


def main() -> int:
    if not HOST:
        print("Set server.ssh (user@address) in data/config.local.yaml first.")
        return 1
    keys = {k: v for k in KEYS if (v := cloud.get_key(k))}
    files = {f: (DATA / f).read_text(encoding="utf-8") for f in FILES if (DATA / f).exists()}
    fd, tmp = tempfile.mkstemp(suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump({"keys": keys, "files": files}, out)
        subprocess.run(["scp", *SSH, tmp, f"{HOST}:/C:/athena/data/incoming.json"], check=True)
    finally:
        os.unlink(tmp)
    r = subprocess.run(["ssh", *SSH, HOST, r"py C:\athena\server\receive_keys.py"], text=True)
    print(f"\nSent {len(keys)} keys ({', '.join(keys) or 'none'}) and {len(files)} files ({', '.join(files)}).")
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
