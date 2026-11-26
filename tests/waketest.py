"""Is Athena being addressed? The wake word has to open a sentence.

    python tests/waketest.py

"At the start" once meant the start of the whole recording. With a TV or
music in the room a recording never ends on a pause, so it runs to the cap
and "Athena" lands mid-transcript - and every command was thrown away.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.ui.voice import _strip_wake     # noqa: E402

CASES = [
    ("Athena, what time is it?", True),
    ("Hey Athena open Spotify", True),
    ("and then the team scored again. Athena, what time is it?", True),
    ("the weather tomorrow looks good. Hey Athena, brief me.", True),
    ("Athina, open WhatsApp.", True),
    ("I told Athena about it yesterday.", False),
    ("we were talking about how Athena works", False),
    ("nothing to see here", False),
]


# Near-names, matched against "athena" itself: real mishearings wake her; noise
# that only resembles the name ("Artha", "Atten" - both ran as commands in Oct
# 2026) and real words ("Athens") don't.
NEAR = [
    ("Atheno, play music.", True),
    ("Attena, lock my screen.", True),
    ("Artha, mister, from a mermaid who put on security.", False),
    ("Atten, make your next step actions.", False),
    ("Athens is the capital of Greece.", False),
]


def main() -> int:
    bad = 0
    for text, want, wake in [(t, w, "jarvis") for t, w in CASES] + [(t, w, "athena") for t, w in NEAR]:
        hit, _ = _strip_wake(text, wake, True)
        ok = hit == want
        bad += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {str(hit):5} {text}")
    print(f"\n{len(CASES) + len(NEAR)} cases, {bad} failure(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
