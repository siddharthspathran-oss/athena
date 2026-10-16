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


def replay(days: float = 1) -> dict:
    """Yesterday's failed commands, run through today's routing - a dry run:
    nothing is executed, so no message is sent and no app opens. Says which
    now go straight to a tool, and which still need the model (and what it is
    shown). Written to data/replay.txt; the nightly health check reports it."""
    from . import agent, registry
    rows = [r for r in load(days) if r.get("said") and not r["kind"].startswith("slow")]
    seen, fixed, still = set(), [], []
    for r in rows:
        said = r["said"].strip()
        if said.lower() in seen:
            continue
        seen.add(said.lower())
        try:
            got = agent.route(said)
        except Exception as e:
            got = None
            r = {**r, "kind": f"{r['kind']}; route crashed: {e}"}
        if got:
            fixed.append(f"  now -> {got[0]}: \"{said}\"  (was: {r['kind']})")
        else:
            shown = ", ".join(registry.select(said)[-6:])
            still.append(f"  still the model: \"{said}\"  (was: {r['kind']}; tools offered ...{shown})")
    text = (f"Replay of {len(seen)} failed command(s) from the last {days:g} day(s), "
            f"{time.strftime('%d %b %Y %H:%M')}:\n" + "\n".join(fixed + still))
    (FILE.parent / "replay.txt").write_text(text + "\n", encoding="utf-8")
    return {"total": len(seen), "routed": len(fixed), "text": text}


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
