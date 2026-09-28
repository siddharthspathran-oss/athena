"""Gaming mode, shared by every Athena process (voice, web, hotkey) through one file.

While it is on, background jobs that would load a model onto the GPU - the
memory learner, the deadline scan, the vision swap-back - skip their turn.
Anything you actually ask turns it off (Agent.ask), so asking a question
always works and simply brings the model back.
"""
from __future__ import annotations

from .config import DATA

FLAG = DATA / "gaming_mode"


def on() -> bool:
    return FLAG.exists()


def set_on(enabled: bool) -> None:
    try:
        if enabled:
            FLAG.write_text("on", encoding="utf-8")
        else:
            FLAG.unlink(missing_ok=True)
    except OSError:
        pass
