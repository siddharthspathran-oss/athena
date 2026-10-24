"""Usage: where the Claude limits go, in one view (idea from Symvibe, 20 Oct 2026).

Three Claude sources, each counted once, all at API prices (on a subscription
that's limit use, not a bill):
- Athena's missions - missions.json, the cost Claude Code reports per run
  (chain parents skipped: they only add up their steps);
- Claude Code itself (this desktop app and anything else) - ECC's cost log
  (~/.claude/metrics/costs.jsonl, a running total per session), minus the
  sessions the first two already count (their transcripts sit under
  athena-jobs / HarnessAgents).
Gemini and Argon cost nothing extra on the Google plan: they're counted in runs.
"""
from __future__ import annotations

import datetime as dt
import json
from collections import defaultdict
from pathlib import Path

from .config import cfg

SOURCES = (("athena", "Athena missions"), ("code", "Claude Code app"))   # Munder's office: uninstalled 20 Oct


def _rows(path: Path) -> list[dict]:
    out = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        pass
    return out


def _deltas(rows: list[tuple[str, float, float]], counted_before=lambda sid, ts: False):
    """(session, unix time, running total) -> [(day, session, spend)] - each step's increase on its day.
    counted_before(session, first ts): the session ran before the log began, so its first total
    is history, not that day's spend."""
    last: dict[str, float] = {}
    out = []
    for sid, ts, usd in sorted(rows, key=lambda r: r[1]):
        if sid not in last:
            last[sid] = usd if counted_before(sid, ts) else 0.0
        step = usd - last[sid]
        last[sid] = max(last[sid], usd)
        if step > 0:
            out.append((dt.date.fromtimestamp(ts), sid, step))
    return out


def _ecc_older(paths: dict[str, str]):
    def older(sid: str, ts: float) -> bool:
        try:
            return ts - Path(paths[sid]).stat().st_ctime > 6 * 3600   # transcript began well before this log line
        except (OSError, KeyError):
            return False
    return older


def _deck_sessions() -> set[str]:
    """Claude sessions of Athena Deck's consoles (deck/data/board.json)."""
    try:
        board = json.loads((Path(__file__).resolve().parents[1] / "deck" / "data" / "board.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {n["session"] for n in board.get("nodes", []) if n.get("kind") == "claude" and n.get("session")}


def summary(days: int = 14, today: dt.date | None = None) -> dict:
    from . import missions
    today = today or dt.date.today()
    span = [today - dt.timedelta(days=days - 1 - i) for i in range(days)]
    first = today.replace(day=1)
    spend = {k: defaultdict(float) for k, _ in SOURCES}
    runs = {"gemini": defaultdict(int), "argon": defaultdict(int)}
    for m in missions.load():
        day = dt.date.fromtimestamp(float(m.get("ended") or m["started"]))
        w = m.get("worker", "claude")
        if w == "claude":
            spend["athena"][day] += float(m.get("cost") or 0)
        elif w in runs:
            runs[w][day] += 1
    ecc = [r for r in _rows(Path.home() / ".claude" / "metrics" / "costs.jsonl")
           if r.get("session_id") and not any(k in str(r.get("transcript_path", "")).lower()
                                              for k in ("athena-jobs",))]
    paths = {r["session_id"]: r.get("transcript_path", "") for r in ecc}
    deck_ids, deck = _deck_sessions(), defaultdict(float)
    for day, sid, usd in _deltas([(r["session_id"], dt.datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00"))
                                 .timestamp(), float(r.get("estimated_cost_usd") or 0)) for r in ecc if r.get("timestamp")],
                               _ecc_older(paths)):
        spend["code"][day] += usd
        if sid in deck_ids:
            deck[day] += usd

    month = lambda d: sum(v for k, v in d.items() if k >= first)  # noqa: E731
    series = [{"key": k, "label": label, "values": [round(spend[k][d], 2) for d in span],
               "month": round(month(spend[k]), 2), "today": round(spend[k][today], 2)} for k, label in SOURCES]
    rows = [{"label": "Athena missions", "sub": "Claude", "key": "athena", "month": series[0]["month"],
             "values": series[0]["values"]}]
    rows += [{"label": "Claude Code app", "sub": "Claude", "key": "code", "month": series[1]["month"],
              "values": series[1]["values"]}]
    if deck:
        rows += [{"label": "  \u21b3 on Athena Deck", "sub": "part of the above", "key": "code", "month": round(month(deck), 2),
                  "values": [round(deck[d], 2) for d in span]}]
    rows += [{"label": label, "sub": "Google plan", "key": "runs", "runs": sum(v for k, v in runs[w].items() if k >= first),
              "values": [runs[w][d] for d in span]} for w, label in (("gemini", "Gemini missions"), ("argon", "Argon"))]
    return {"days": [d.isoformat() for d in span], "series": series, "rows": rows,
            "month_total": round(sum(s["month"] for s in series), 2), "month_name": today.strftime("%B"),
            "cap": float(cfg.get("missions.monthly_cap_usd", 15)), "athena_month": series[0]["month"]}


if __name__ == "__main__":
    assert _deltas([("a", 100, 1.0), ("a", 200, 3.0), ("b", 150, 0.5)]) == [
        (dt.date.fromtimestamp(100), "a", 1.0), (dt.date.fromtimestamp(150), "b", 0.5), (dt.date.fromtimestamp(200), "a", 2.0)]
    assert _deltas([("old", 100, 900.0), ("old", 200, 905.0)], lambda s, t: True) == [(dt.date.fromtimestamp(200), "old", 5.0)]
    s = summary()
    assert [x["key"] for x in s["series"]] == ["athena", "code"] and len(s["days"]) == 14
    assert all(len(x["values"]) == 14 for x in s["series"] + s["rows"])
    print("usage: checks pass;", {x["key"]: x["month"] for x in s["series"]}, "month", s["month_total"])
