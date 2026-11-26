"""What every part of Athena's web server shares: the access token, the orb's key,
this PC's addresses (local Wi-Fi and Tailscale), and the lock that lets one
request use the agent at a time.

Split out of web.py (31 Oct 2026); web.py imports all of it back, so
`from jarvis.ui.web import get_token` still works everywhere.
"""
from __future__ import annotations

import secrets
import socket
import threading
from pathlib import Path

from ..config import DATA


TOKEN_FILE = DATA / "web_token.txt"
_lock = threading.Lock()


def get_token() -> str:
    """The shared secret, created once and reused."""
    if TOKEN_FILE.exists():
        tok = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if tok:
            return tok
    tok = secrets.token_urlsafe(24)
    TOKEN_FILE.write_text(tok, encoding="utf-8")
    return tok


ORB_KEY_FILE = DATA / "orb_key.txt"


def orb_key() -> str:
    """The desk orb's own key - read state, press listen; nothing more."""
    if not ORB_KEY_FILE.exists() or not ORB_KEY_FILE.read_text(encoding="utf-8").strip():
        ORB_KEY_FILE.write_text(secrets.token_urlsafe(18), encoding="utf-8")
    return ORB_KEY_FILE.read_text(encoding="utf-8").strip()


def _tailscale_https() -> str:
    """The https:// address Tailscale Serve is publishing, if it is on.

    Worth singling out: a browser will only offer to *install* a site, run a
    service worker, or hand it the camera over HTTPS. Over plain http the
    phone can still reach Athena, but only as a bookmark.
    """
    import os
    import subprocess

    exe = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tailscale" / "tailscale.exe"
    if not exe.exists():
        return ""
    try:
        out = subprocess.run([str(exe), "serve", "status"], capture_output=True,
                             text=True, timeout=8,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        return ""
    for line in out.stdout.splitlines():
        line = line.strip()
        if line.startswith("https://"):
            return line.split()[0].rstrip("/")
    return ""


def local_ips() -> list[str]:
    """Addresses this machine can be reached on, Tailscale included."""
    found = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in found and not ip.startswith("127."):
                found.append(ip)
    except Exception:
        pass
    return found
