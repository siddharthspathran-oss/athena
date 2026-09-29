"""Files between this PC and your phone, over Athena's own web interface.

PC -> phone: "send me the lab manual" makes a *share*: an unguessable id for
one file, valid for phone_files.share_minutes. The phone downloads it from
/get/<id>, which also needs the interface's token, so a leaked id alone is
not enough. Shares live in data/shares.json because the voice process makes
them and the web process serves them.

Phone -> PC: the HUD's "send to PC" button streams each file to /upload,
which writes it into phone_files.receive_folder (Downloads\\From phone by
default) under a cleaned-up name, never over an existing file.

Nothing leaves your own network: the file goes from the PC to the phone
directly, on the same Wi-Fi or over Tailscale. A push (ntfy) says a file is
waiting, never what is in it and never a link or the token.

Never shared, whatever is asked: anything in Athena's own data folder (keys,
the token, chats), files that look like keys or passwords, and anything
outside safety.allowed_roots or inside safety.protected_paths.
"""
from __future__ import annotations

import re
import secrets
import time
from pathlib import Path
from typing import BinaryIO

from .config import DATA, cfg, expand, path_refusal
from .store import StoreError, load_json, save_json

SHARES = DATA / "shares.json"
MAX_SHARES = 30

# Set by the web server while it answers a request typed on the phone, so a
# tool knows the person is already looking at the HUD (no push needed) and
# "send me X" means "to this phone".
from_phone = False

_SECRET_NAME = re.compile(
    r"(?:^\.env$|\.env$|\.pem$|\.key$|\.p12$|\.pfx$|\.kdbx$|^id_(?:rsa|ed25519|ecdsa)|"
    r"password|passwd|secret|credential|token|api[_ -]?keys?)", re.I)


# ---------------------------------------------------------------- names

_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
             *(f"lpt{i}" for i in range(1, 10))}


def safe_name(name: str) -> str:
    """A file name the phone sent, made safe to write on Windows.

    Only the last path part is kept ("../../x" is "x"), characters Windows
    forbids become "_", and reserved device names ("CON", "NUL") are
    prefixed, because writing to them is not writing a file at all.
    """
    raw = (name or "").replace("\\", "/").split("/")[-1]
    clean = _BAD_CHARS.sub("_", raw).strip(" .")
    if not clean:
        return "file"
    stem, dot, ext = clean.rpartition(".")
    if not dot:
        stem, ext = clean, ""
    if stem.lower() in _RESERVED:
        stem = "_" + stem
    ext = ext[:16]
    stem = stem[:150]
    return f"{stem}.{ext}" if ext else stem


def unique_path(folder: Path, name: str) -> Path:
    """folder/name, or folder/name (1), (2)... if that is taken."""
    target = folder / name
    if not target.exists():
        return target
    stem, ext = target.stem, target.suffix
    for n in range(1, 1000):
        candidate = folder / f"{stem} ({n}){ext}"
        if not candidate.exists():
            return candidate
    return folder / f"{stem} ({secrets.token_hex(3)}){ext}"


def receive_folder() -> Path:
    return expand(str(cfg.get("phone_files.receive_folder", "~/Downloads/From phone")))


def human_size(n: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


# ---------------------------------------------------------------- PC -> phone

def refusal(path: Path) -> str | None:
    """Why this file may not be sent to the phone, or None."""
    try:
        p = Path(path).resolve()
    except OSError:
        return "that path is not valid"
    if not p.is_file():
        return f"{p.name} is not a file" if p.exists() else f"{p.name} does not exist"
    try:
        p.relative_to(DATA.resolve())
        return "files in Athena's own data folder are never shared - they hold your keys and chats"
    except ValueError:
        pass
    if _SECRET_NAME.search(p.name):
        return f"{p.name} looks like a key or password file, so it is not shared"
    why = path_refusal(p)
    return why


def _load() -> dict:
    try:
        data = load_json(SHARES, {})
    except StoreError:
        return {}
    return data if isinstance(data, dict) else {}


def _live(data: dict, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    return {k: v for k, v in data.items() if isinstance(v, dict) and v.get("expires", 0) > now}


def create(path: Path) -> dict:
    """Share one file with the phone. Returns the share; raises ValueError
    with a plain reason when the file may not be shared."""
    why = refusal(path)
    if why:
        raise ValueError(why)
    p = Path(path).resolve()
    minutes = float(cfg.get("phone_files.share_minutes", 15))
    share = {"id": secrets.token_urlsafe(18), "path": str(p), "name": p.name,
             "size": p.stat().st_size, "created": time.time(),
             "expires": time.time() + minutes * 60}
    data = _live(_load())
    data[share["id"]] = share
    if len(data) > MAX_SHARES:                      # oldest out
        for k in sorted(data, key=lambda k: data[k]["created"])[:len(data) - MAX_SHARES]:
            data.pop(k)
    save_json(SHARES, data)
    return share


def pending() -> list[dict]:
    """Shares still valid, newest first - what the phone offers to download."""
    now = time.time()
    return sorted(({"id": s["id"], "name": s["name"], "size": s["size"],
                    "minutes_left": max(1, int((s["expires"] - now) // 60))}
                   for s in _live(_load(), now).values()),
                  key=lambda s: -s["minutes_left"])


def resolve(share_id: str) -> Path | None:
    """The file behind a share, re-checked now: still valid, still there,
    still allowed. None otherwise."""
    if not re.fullmatch(r"[\w-]{10,64}", share_id or ""):
        return None
    share = _live(_load()).get(share_id)
    if not share:
        return None
    p = Path(share["path"])
    return None if refusal(p) else p


def notify(name: str) -> None:
    """Tell the phone a file is waiting, when the request did not come from
    the phone itself. The push carries the file name only: ntfy topics are
    public to anyone who knows them, so never a link or the token."""
    if from_phone or not cfg.get("phone_files.notify", True):
        return
    try:
        from . import phone
        phone.push(f"{name} is ready - open Athena on your phone to download it.",
                   title="Athena · file for you", priority="default")
    except Exception:
        pass


# ---------------------------------------------------------------- phone -> PC

class UploadError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def receive(stream: BinaryIO, length: int, name: str) -> Path:
    """Write an upload of `length` bytes from `stream` into the receive folder.

    Streamed in 1 MB pieces to a .part file and renamed only once complete,
    so an interrupted upload never leaves a half file under the real name.
    """
    limit = int(float(cfg.get("phone_files.max_upload_mb", 2048)) * 1024 * 1024)
    if length <= 0:
        raise UploadError(411, "The upload had no size - try again.")
    if length > limit:
        raise UploadError(413, f"That file is larger than the {human_size(limit)} limit "
                               f"(phone_files.max_upload_mb).")
    folder = receive_folder()
    why = path_refusal(folder, write=True)
    if why:
        raise UploadError(403, why)
    folder.mkdir(parents=True, exist_ok=True)
    target = unique_path(folder, safe_name(name))
    if target.resolve().parent != folder.resolve():
        raise UploadError(400, "Bad file name.")
    part = target.with_name(f"{target.name}.{secrets.token_hex(4)}.part")
    got = 0
    try:
        with open(part, "wb") as out:
            while got < length:
                block = stream.read(min(1 << 20, length - got))
                if not block:
                    break
                out.write(block)
                got += len(block)
        if got != length:
            raise UploadError(400, "The upload stopped part-way - try again.")
        final = unique_path(folder, target.name)     # taken meanwhile? next free name
        part.replace(final)
    finally:
        if part.exists():
            part.unlink(missing_ok=True)
    return final
