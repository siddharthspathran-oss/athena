"""Every request Athena fumbled, in data/failures.jsonl.

Guessing which commands go wrong is how the routers grew one bug report at
a time. This keeps the evidence instead: what was said, what went wrong
("not understood", "nothing ran", "rescued by the planner", a tool error or
timeout) and the answer given. `python run.py failures` summarises it -
the most common kinds, and the latest cases - so the next fix is the one
that happens most, not the one noticed last.

Local only, one line per case, capped at MAX_LINES (oldest dropped).
"""
from __future__ import annotations

import json
import threading
import time
from collections import Counter

from .config import DATA

FILE = DATA / "failures.jsonl"
MAX_LINES = 2000
_lock = threading.Lock()


def log(kind: str, said: str, answer: str = "", detail: str = "") -> None:
    """Record one fumble. Never raises: logging must not cost a reply."""
    row = {"t": time.time(), "kind": kind, "said": said[:300],
           "answer": answer[:300], "detail": detail[:300]}
    try:
        with _lock:
            with FILE.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            if FILE.stat().st_size > MAX_LINES * 400:
                lines = FILE.read_text(encoding="utf-8").splitlines()[-MAX_LINES:]
                tmp = FILE.with_suffix(".tmp")
                tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
                tmp.replace(FILE)
    except OSError:
        pass


def load(days: float = 30) -> list[dict]:
    try:
        rows = [json.loads(line) for line in FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, ValueError):
        return []
    cutoff = time.time() - days * 86400
    return [r for r in rows if r.get("t", 0) >= cutoff]


def report(days: float = 30, latest: int = 20) -> str:
    rows = load(days)
    if not rows:
        return f"No failures logged in the last {days:g} days."
    kinds = Counter(r["kind"] for r in rows)
    said = Counter(r["said"].strip().lower() for r in rows)
    out = [f"{len(rows)} failure(s) in the last {days:g} days:\n"]
    out += [f"  {n:4}  {k}" for k, n in kinds.most_common()]
    repeated = [(s, n) for s, n in said.most_common(10) if n > 1]
    if repeated:
        out.append("\nSaid more than once:")
        out += [f"  {n:4}x  {s}" for s, n in repeated]
    out.append(f"\nLatest {min(latest, len(rows))}:")
    for r in rows[-latest:]:
        when = time.strftime("%d %b %H:%M", time.localtime(r["t"]))
        extra = f"  ({r['detail']})" if r.get("detail") else ""
        out.append(f"  {when}  [{r['kind']}]  \"{r['said']}\"  ->  {r['answer'][:90]}{extra}")
    return "\n".join(out)
