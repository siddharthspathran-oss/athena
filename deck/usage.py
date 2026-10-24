"""Token analytics from the CLIs' own session logs - nothing is sent anywhere.

- Claude Code: every assistant message in ~/.claude/projects/*/*.jsonl carries its
  usage; a reply split over several lines repeats it, so lines are counted once
  per message id.
- Codex: each session's rollout (~/.codex/sessions/y/m/d/rollout-*.jsonl) logs a
  running total ("token_count"); the day's share is the growth that day. The last
  event also says how much of the ChatGPT plan's Codex allowance is used.

Antigravity keeps no readable token log, so it isn't counted.
Files are re-read only when they change (size + mtime), so polling is cheap.
"""
from __future__ import annotations

import datetime as dt
import json
import time
from collections import defaultdict
from pathlib import Path

DAYS = 14
_cache: dict[str, tuple] = {}                 # path -> (size, mtime, parsed)


def _day(ts: str) -> str:
    """ISO UTC timestamp -> local date (the user's day)."""
    try:
        return dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d")
    except ValueError:
        return ""


def _cached(path: Path, parse):
    try:
        st = path.stat()
    except OSError:
        return None
    hit = _cache.get(str(path))
    if hit and hit[0] == st.st_size and hit[1] == st.st_mtime:
        return hit[2]
    out = parse(path)
    _cache[str(path)] = (st.st_size, st.st_mtime, out)
    return out


def _claude_file(path: Path) -> dict:
    """{day: {"in", "out", "cache"}} for one transcript; each message id once."""
    days: dict[str, dict] = defaultdict(lambda: {"in": 0, "out": 0, "cache": 0})
    seen = set()
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"usage"' not in line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            m = r.get("message") or {}
            u = m.get("usage") or {}
            mid = m.get("id") or r.get("requestId") or r.get("uuid")
            if not u or mid in seen:
                continue
            seen.add(mid)
            d = days[_day(r.get("timestamp", ""))]
            d["in"] += int(u.get("input_tokens") or 0)
            d["out"] += int(u.get("output_tokens") or 0)
            d["cache"] += int(u.get("cache_read_input_tokens") or 0) + int(u.get("cache_creation_input_tokens") or 0)
    return dict(days)


def _codex_file(path: Path) -> dict:
    """{"days": {day: {"in", "out", "cache"}}, "limits": last rate_limits, "at": its time} for one rollout."""
    days: dict[str, dict] = defaultdict(lambda: {"in": 0, "out": 0, "cache": 0})
    prev = {"in": 0, "out": 0, "cache": 0}
    limits, at = None, ""
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"token_count"' not in line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            p = r.get("payload") or {}
            if p.get("type") != "token_count":
                continue
            tot = ((p.get("info") or {}).get("total_token_usage")) or {}
            now = {"in": int(tot.get("input_tokens") or 0) - int(tot.get("cached_input_tokens") or 0),
                   "out": int(tot.get("output_tokens") or 0), "cache": int(tot.get("cached_input_tokens") or 0)}
            d = days[_day(r.get("timestamp", ""))]
            for k in d:
                d[k] += max(0, now[k] - prev[k])
            prev = now
            if p.get("rate_limits"):
                limits, at = p["rate_limits"], r.get("timestamp", "")
    return {"days": dict(days), "limits": limits, "at": at}


def report(home: Path | None = None, now: float | None = None) -> dict:
    """The last DAYS days of tokens per CLI, today's totals, and Codex's plan allowance."""
    home = home or Path.home()
    today = dt.datetime.fromtimestamp(now or time.time())
    span = [(today - dt.timedelta(days=i)).strftime("%Y-%m-%d") for i in range(DAYS - 1, -1, -1)]
    cutoff = (now or time.time()) - (DAYS + 1) * 86400
    series = {k: {d: {"in": 0, "out": 0, "cache": 0} for d in span} for k in ("claude", "codex")}

    def add(kind, days):
        for day, v in (days or {}).items():
            if day in series[kind]:
                for k in v:
                    series[kind][day][k] += v[k]

    for f in (home / ".claude" / "projects").glob("*/*.jsonl"):
        try:
            if f.stat().st_mtime >= cutoff:
                add("claude", _cached(f, _claude_file))
        except OSError:
            continue
    limits, at = None, ""
    root = home / ".codex" / "sessions"
    for day in span:
        for f in (root / day.replace("-", "/")).glob("rollout-*.jsonl"):
            got = _cached(f, _codex_file)
            if not got:
                continue
            add("codex", got["days"])
            if got["limits"] and got["at"] > at:
                limits, at = got["limits"], got["at"]
    out = {"days": span, "series": {}, "today": {}}
    for kind, s in series.items():
        out["series"][kind] = [{"day": d, **s[d], "total": s[d]["in"] + s[d]["out"] + s[d]["cache"]} for d in span]
        out["today"][kind] = out["series"][kind][-1]
    p = (limits or {}).get("primary") or {}
    out["codex_limit"] = {"used": p.get("used_percent"), "window_min": p.get("window_minutes"), "resets_at": p.get("resets_at")} if p else None
    return out


if __name__ == "__main__":
    import tempfile

    home = Path(tempfile.mkdtemp())
    now = dt.datetime(2026, 10, 20, 15, 0).timestamp()
    proj = home / ".claude" / "projects" / "p"
    proj.mkdir(parents=True)
    ts = dt.datetime(2026, 10, 20, 9, 0).astimezone().isoformat()
    old = dt.datetime(2026, 10, 19, 9, 0).astimezone().isoformat()
    msg = lambda mid, t, i, o, c: json.dumps({"timestamp": t, "message": {"id": mid, "usage": {"input_tokens": i, "output_tokens": o, "cache_read_input_tokens": c}}})
    (proj / "s.jsonl").write_text("\n".join([msg("m1", ts, 10, 100, 1000), msg("m1", ts, 10, 100, 1000), msg("m2", old, 5, 50, 0),
                                            json.dumps({"type": "user", "message": {"content": "hi"}})]), encoding="utf-8")
    cx = home / ".codex" / "sessions" / "2026" / "10" / "20"
    cx.mkdir(parents=True)
    ev = lambda t, i, cached, o, used: json.dumps({"timestamp": t, "type": "event_msg", "payload": {"type": "token_count",
        "info": {"total_token_usage": {"input_tokens": i, "cached_input_tokens": cached, "output_tokens": o}},
        "rate_limits": {"primary": {"used_percent": used, "window_minutes": 43200, "resets_at": 1795070084}}}})
    (cx / "rollout-2026-10-20T11-38-34-x.jsonl").write_text("\n".join([ev(ts, 1000, 400, 10, 1.0), ev(ts, 3000, 1400, 30, 2.5)]), encoding="utf-8")
    r = report(home, now)
    assert r["days"][-1] == "2026-10-20" and len(r["days"]) == DAYS
    assert r["today"]["claude"] == {"day": "2026-10-20", "in": 10, "out": 100, "cache": 1000, "total": 1110}, r["today"]["claude"]  # m1 once
    assert r["series"]["claude"][-2]["total"] == 55
    assert r["today"]["codex"]["total"] == 3030 and r["today"]["codex"]["cache"] == 1400, r["today"]["codex"]
    assert r["codex_limit"] == {"used": 2.5, "window_min": 43200, "resets_at": 1795070084}
    assert report(home, now) == r                                                     # cached: same answer
    print("usage: checks pass")
