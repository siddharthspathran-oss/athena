"""Sending a file from this PC to your phone, and opening what the phone sent.

The transfer itself lives in jarvis/share.py and the web interface; these
are the two things you ask for by voice or from the chat.
"""
from __future__ import annotations

import re
from pathlib import Path

from .. import share
from ..config import cfg
from ..registry import tool

TAG = ["files"]

_PRONOUN = re.compile(r"^\s*(?:it|that|this|them|the\s+same|(?:that|this|the)\s+(?:file|one|document|pdf))\s*$", re.I)


def _share_one(p: Path) -> str:
    try:
        s = share.create(p)
    except ValueError as e:
        return f"I can't send that - {e}."
    share.notify(s["name"])
    minutes = int(float(cfg.get("phone_files.share_minutes", 15)))
    # [share:<id>] is turned into a Download button by the phone's chat and
    # never read aloud. It sits inside the first sentence, which is the part
    # kept when a reply is shortened.
    return (f"Sent to your phone [share:{s['id']}]: {s['name']} ({share.human_size(s['size'])}). "
            f"Tap Download in Athena's chat on the phone - it works for {minutes} minutes.")


@tool(tags=TAG, timeout=60)
def send_to_phone(file: str) -> str:
    """Send a file from this PC to the user's phone. Use for "send me X",
    "send X to my phone", "share X with my phone", "I need X on my phone".

    file: the file's name in the user's words, a full path, or "it" for the file just found or opened
    """
    from . import files, finder
    from .. import offer

    q = (file or "").strip().strip("\"'.")
    if not q:
        return "Which file should I send?"
    if _PRONOUN.match(q):
        if files.last_file is None:
            return "Which file? I haven't opened or found one yet."
        return _share_one(Path(files.last_file))
    if re.match(r"^(?:[a-z]:[\\/]|/|~|%|\\\\)", q, re.I):
        p = Path(q).expanduser()
        if p.is_file():
            return _share_one(p)

    found = [e for e in finder.find_all(q, limit=8) if not share.refusal(Path(e.path))]
    if len(found) == 1:
        files._remember(Path(found[0].path))
        return _share_one(Path(found[0].path))
    if len(found) > 1:
        choices = [offer.Choice(files._spoken(e.name), "send_to_phone", {"file": e.path})
                   for e in found[:6]]
        offer.set_choices(choices)
        listing = "; ".join(f"{i}, {c.label}" for i, c in enumerate(choices, 1))
        return f"I found {len(found)} files for {q}: {listing}. Which one should I send? Say a number, or all."

    p, note = files._resolve(q, want_dir=False)        # looser match, with "closest" offers
    if p is None:
        # The closest-names offer made by _resolve opens a file; sending is
        # what was asked, so point those choices at this tool instead.
        choices = offer.pending()
        if choices:
            offer.set_choices([offer.Choice(c.label, "send_to_phone", {"file": c.args.get("path", "")})
                               for c in choices])
            note = note.replace("Want me to open", "Want me to send")
        return note
    offer.clear()               # _resolve may have offered "open the others"
    return _share_one(p)


@tool(tags=TAG)
def open_from_phone(which: str = "latest") -> str:
    """Open what was sent from the phone to this PC: the latest file, or the
    whole folder. Use for "open the file I sent from my phone", "show what my
    phone sent".

    which: "latest" for the newest file, or "folder" for the whole folder
    """
    from . import files

    folder = share.receive_folder()
    if not folder.is_dir():
        return "Nothing has come from your phone yet."
    if (which or "").strip().lower().startswith("folder"):
        return files.open_folder(str(folder))
    items = [p for p in folder.iterdir() if p.is_file() and not p.name.endswith(".part")]
    if not items:
        return "Nothing has come from your phone yet."
    newest = max(items, key=lambda p: p.stat().st_mtime)
    return files.open_file(str(newest))
