"""A time budget for one request, so a slow model never means a 3-minute wait.

15 Oct 2026: "what should I study next?" took 208 s - every model in the study
chain failed in turn and their timeouts stacked (45 s each, then the local
model). Agent.ask sets a budget (voice 25 s, typed 60 s); every hosted call
caps its timeout to what is left, and the model chains stop trying once it is
spent, so the answer becomes "the models are busy" instead of a long silence.

Process-wide rather than per-thread: tools run in worker threads (registry),
and each process (voice, web) answers one request at a time. Background jobs
(goals, rules) run with no budget set, so they are not cut short.
"""
from __future__ import annotations

import time

_until: float | None = None


def start(seconds: float) -> None:
    global _until
    _until = time.time() + float(seconds)


def clear() -> None:
    global _until
    _until = None


def left() -> float | None:
    """Seconds left, or None when no budget is set."""
    return None if _until is None else _until - time.time()


def expired() -> bool:
    remaining = left()
    return remaining is not None and remaining <= 0


def cap(timeout: float) -> float:
    """A call's timeout, shortened to what the budget has left (never under 2 s)."""
    remaining = left()
    return timeout if remaining is None else max(2.0, min(float(timeout), remaining))


BUSY = "The models are busy right now and I ran out of time - try again in a minute."


if __name__ == "__main__":
    assert left() is None and cap(45) == 45 and not expired()
    start(10)
    assert 9 < left() <= 10 and cap(45) <= 10 and cap(5) == 5
    start(-1)
    assert expired() and cap(45) == 2.0
    clear()
    assert left() is None
    print("deadline: all checks pass")
