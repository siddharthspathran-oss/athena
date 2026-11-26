"""What the routes_*.py shortcut modules share: the polite lead-in every command may
start with, the question patterns, and the file lookup.

Split out of routes.py (31 Oct 2026).
"""
from __future__ import annotations

import re


# Without a verb the sentence has to look like a name, not a question.
_QUESTION_WORD = re.compile(r"^(what|who|when|where|why|how|is|are|do|does|"
                            r"can|could|should|would|will|tell|give|show me)\b", re.I)


def _best_file(q: str):
    """The index's confident match for q, or None. Never waits on the disk:
    the router runs on every command, including ones that are not files."""
    try:
        from .tools import finder

        r = finder.find(q, refresh=False, wide_wait=0)
    except Exception:
        return None
    return r.best if r.confident else None


# A question about the user's own data that no router above caught. Left to
# the model, it guessed: "your next MS Teams meeting is tomorrow at 14:00",
# repeated even after "but tomorrow is Sunday" (3 Oct 2026). These always go
# to the tool that has the real answer.
_G_ASK = re.compile(r"^\W*(?:(?:athena|jarvis|so|and|hey|ok|okay|but|then|no|yes|yeah|please|similarly|also|actually)\W+)*(?:what|when|which|where|who|how\s+(?:many|much|is|are|'?s)|"
                    r"do\s+i|did\s+i|am\s+i|is\s+there|are\s+there|any|anything|tell\s+me|show|check|list|have\s+i|"
                    r"can\s+you\s+(?:tell|check|show)|is\s+my|are\s+my|what'?s)\b", re.I)


# Automations (jarvis/rules.py). A rule needs its trigger set off by a comma -
# "when I get home, play my playlist" - or the action first - "organise my
# downloads every 2 hours"; a bare "when is my next class" is a question.
_R_LEAD = r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please)\W+)*(?:can\s+you\s+)?(?:please\s+)?"
