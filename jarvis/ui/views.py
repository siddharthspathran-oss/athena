"""Data for the Command Center's pages: Calendar, Memory, Workflows (with the
music player), System, Tasks, Tools & Skills.

The pages show tables and live panels rather than chat answers, so each one
gets structured data here, served as /view/<name>. Everything is local reads
except the calendar (Google), which is cached for a few minutes.
"""
from __future__ import annotations

import base64
import datetime as dt
import threading
import time

import psutil

from .. import registry
from ..config import DATA, ROOT, cfg

_cal_cache: dict = {"t": 0.0, "data": None}


def _day_events(day: dt.date) -> list[dict]:
    from ..tools import brief
    from ..store import load_json

    events = brief._google_events(day) + brief._timetable_events(day)
    for feed in load_json(brief.FEEDS_FILE, []):
        try:
            events += brief._feed_events(feed["url"], day)
        except Exception:
            continue
    return events


def calendar(days: int = 7) -> dict:
    """The next `days` days of calendar and timetable, one row per event."""
    if _cal_cache["data"] and time.time() - _cal_cache["t"] < 180:
        return _cal_cache["data"]
    from ..tools import brief

    now = dt.datetime.now()
    rows = []
    for i in range(days):
        day = dt.date.today() + dt.timedelta(days=i)
        try:
            evs = _day_events(day)
        except Exception as e:
            return {"error": f"Could not read the calendar ({str(e)[:100]})", "rows": []}
        for e in sorted(evs, key=lambda e: (not e["all_day"], brief._local(e["start"]))):
            s = brief._local(e["start"])
            end = brief._local(e["end"]) if e.get("end") else s
            rows.append({
                "date": day.isoformat(),
                "day": "Today" if i == 0 else "Tomorrow" if i == 1 else day.strftime("%A"),
                "time": "All day" if e["all_day"] else f"{s:%H:%M}–{end:%H:%M}",
                "what": e["what"], "where": e.get("where", ""),
                "source": {"google": "Google Calendar", "timetable": "UPES timetable"}.get(e.get("source"), e.get("source") or "Calendar"),
                "state": "done" if end <= now else "now" if s <= now else "upcoming",
            })
    data = {"rows": rows, "google": brief.TOKEN_FILE.exists()}
    _cal_cache.update(t=time.time(), data=data)
    return data


def memory() -> dict:
    from .. import profile, recall
    from ..memory import MEM_FILE
    from ..store import load_json

    items = [i for i in profile.load() if i.live()]
    facts = load_json(MEM_FILE, {})
    return {
        "sections": [{"key": k, "title": t,
                      "items": [{"text": i.text, "added": i.added, "until": i.until}
                                for i in items if i.section == k]}
                     for k, t in profile.SECTIONS.items()],
        "facts": [{"key": k, "value": v} for k, v in facts.items()],
        "chats": len(list((DATA / "chats").glob("*.json"))),
        "snippets": len((recall._load()[1] or {}).get("chunks", [])),
        "file": str(profile.PROFILE),
    }


def forget(text: str) -> dict:
    from .. import profile
    return {"removed": profile.forget(text)}


# ---------------------------------------------------------------- music

def _media_session() -> dict | None:
    """What Windows says is playing - Spotify, a browser tab, any player -
    via the same media controls the taskbar uses."""
    import asyncio

    async def read():
        from winrt.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as Manager)
        mgr = await Manager.request_async()
        sessions = list(mgr.get_sessions())
        cur = mgr.get_current_session()
        spot = next((s for s in sessions if "spotify" in (s.source_app_user_model_id or "").lower()), None)
        s = spot or cur
        if s is None:
            return None
        props = await s.try_get_media_properties_async()
        info = s.get_playback_info()
        tl = s.get_timeline_properties()
        art = ""
        try:
            if props.thumbnail:
                from winrt.windows.storage.streams import Buffer, InputStreamOptions
                stream = await props.thumbnail.open_read_async()
                buf = Buffer(stream.size)
                got = await stream.read_async(buf, stream.size, InputStreamOptions.READ_AHEAD)
                art = "data:image/jpeg;base64," + base64.b64encode(bytes(got)).decode()
        except Exception:
            art = ""
        return {
            "app": s.source_app_user_model_id or "",
            "title": props.title or "", "artist": props.artist or "",
            "album": props.album_title or "",
            "playing": int(info.playback_status) == 4,    # 4 = Playing
            "position": tl.position.total_seconds() if tl and tl.position else 0,
            "length": tl.end_time.total_seconds() if tl and tl.end_time else 0,
            "art": art,
        }

    try:
        return asyncio.run(read())
    except Exception:
        return None


def workflows() -> dict:
    from ..tools import routines

    rows = []
    for name, body in routines.load().items():
        steps = []
        for s in body.get("steps") or []:
            if isinstance(s, dict):
                (k, v), = s.items()
                steps.append(k.replace("_", " ") + ("" if isinstance(v, dict) else f": {v}"))
            else:
                steps.append(str(s))
        rows.append({"name": name, "steps": steps, "say": body.get("say", "")})
    spotify = any((p.info["name"] or "").lower() == "spotify.exe"
                  for p in psutil.process_iter(["name"]))
    return {"routines": rows, "music": _media_session(), "spotify_running": spotify}


def music_action(action: str) -> dict:
    from ..tools import system

    if action == "open":
        return {"said": system.open_app("spotify")}
    if action in ("play", "pause", "next", "previous"):
        return {"said": system.music(action)}
    return {"said": "Unknown action."}


def run_routine(name: str) -> dict:
    from ..tools import routines
    return {"said": routines.run_routine(name)}


# ---------------------------------------------------------------- system

def system() -> dict:
    from ..tools.system import list_running_apps  # noqa: F401  (import check only)
    from .web import _system_status

    s = _system_status()
    rows = []

    def add(name, value, pct=None, warn_at=85, note=""):
        state = "ok" if pct is None or pct < warn_at else "warn"
        rows.append({"name": name, "value": value, "pct": pct, "state": state, "note": note})

    add("Processor (CPU)", f"{s['cpu']:.0f}%", s["cpu"], 90, f"{psutil.cpu_count()} threads")
    add("Memory (RAM)", f"{s['mem_used']} / {s['mem_total']} GB", s["mem_pct"], 90)
    add("Disk C:", f"{s['disk_pct']:.0f}% used", s["disk_pct"], 90)
    g = s.get("gpu")
    if g:
        add("Graphics (GPU)", f"{g['util']}%", g["util"], 95)
        add("Video memory (VRAM)", f"{g['vram_used']} / {g['vram_total']} GB",
            round(100 * g["vram_used"] / g["vram_total"]) if g["vram_total"] else None, 95)
        add("GPU temperature", f"{g['temp']} °C", g["temp"], 85)
    if s.get("battery") is not None:
        add("Battery", f"{s['battery']}%" + (" · charging" if s.get("plugged") else " · on battery"),
            None if s.get("plugged") else (100 - s["battery"]), 85)
    add("AI model runs on", (s.get("model_on") or "?").upper(), None,
        note="should be GPU" if (s.get("model_on") or "").upper() == "CPU" else "")
    if (s.get("model_on") or "").upper() == "CPU":
        rows[-1]["state"] = "warn"
    add("Network", f"↓ {s['down_kbs']:.0f}  ↑ {s['up_kbs']:.0f} KB/s")
    add("Uptime", f"{s['uptime_h']} h")

    procs = []
    for p in psutil.process_iter(["name", "memory_info", "cpu_percent"]):
        try:
            procs.append((p.info["memory_info"].rss, p.info["name"]))
        except Exception:
            continue
    by_name: dict[str, int] = {}
    for rss, name in procs:
        by_name[name] = by_name.get(name, 0) + rss
    top = sorted(by_name.items(), key=lambda kv: -kv[1])[:10]
    health = "Healthy" if all(r["state"] == "ok" for r in rows) else "Needs attention"
    return {"health": health, "rows": rows,
            "apps": [{"name": n, "mb": round(b / 1048576)} for n, b in top]}


# ---------------------------------------------------------------- tasks

def tasks() -> dict:
    from ..tools import todo
    from ..tools.scheduler import _load as load_jobs

    today = dt.date.today().isoformat()
    now = dt.datetime.now()
    open_ = [t for t in todo.load() if not t["done"]]
    done_today = [t for t in todo.load() if t["done"] and (t.get("finished") or "").startswith(today)]
    reminders = []
    for j in load_jobs():
        try:
            at = dt.datetime.fromisoformat(j["fire_at"])
        except Exception:
            continue
        reminders.append({"id": j["id"], "text": j["text"], "at": at.strftime("%H:%M"),
                          "date": at.date().isoformat(), "repeat": j.get("repeat", "once")})
    classes = []
    try:
        from ..tools import brief
        for e in _day_events(dt.date.today()):
            s, end = brief._local(e["start"]), brief._local(e.get("end") or e["start"])
            if not e["all_day"] and end > now:
                classes.append({"what": e["what"], "at": f"{s:%H:%M}", "where": e.get("where", "")})
    except Exception:
        pass
    return {
        "today": [t for t in open_ if t["due"] and t["due"] <= today],
        "remaining": [t for t in open_ if not (t["due"] and t["due"] <= today)],
        "done_today": done_today,
        "reminders_today": [r for r in reminders if r["date"] == today],
        "reminders_later": [r for r in reminders if r["date"] > today],
        "classes": classes,
    }


def task_action(body: dict) -> dict:
    from ..tools import todo

    act = body.get("action")
    if act == "add":
        return {"said": todo.add_task(body.get("text", ""), body.get("due", ""))}
    if act == "done":
        return {"said": todo.complete_task(body.get("id", ""))}
    if act == "delete":
        tasks = [t for t in todo.load() if t["id"] != body.get("id")]
        todo.save(tasks)
        return {"said": "Removed."}
    return {"said": "Unknown action."}


# ---------------------------------------------------------------- skills

AREAS = {"system": "PC & apps", "files": "Files", "web": "Web & browser", "shell": "Memory & system",
         "messaging": "Messages & calls", "schedule": "Reminders & tasks", "study": "Class work",
         "knowledge": "Your documents", "windows": "Windows settings", "brief": "Calendar & mail",
         "memory": "Memory"}


def skills() -> dict:
    rows = []
    for t in registry.REGISTRY.values():
        area = (t.tags or ["other"])[0]
        rows.append({"name": t.name.replace("_", " "), "area": AREAS.get(area, area.title()),
                     "what": (t.description or "").strip().split("\n")[0][:160],
                     "careful": bool(t.destructive)})
    rows.sort(key=lambda r: (r["area"], r["name"]))
    from ..tools import routines
    return {"rows": rows, "count": len(rows), "routines": list(routines.load()),
            "planner": cfg.get("planner.model", "") if cfg.get("planner.enabled", True) else ""}


# ---------------------------------------------------------------- calls & bookings

def phone() -> dict:
    """Contacts (names only - numbers stay on the PC side) and what is waiting."""
    from ..tools import browser, places
    from ..tools.messaging import _load as contacts

    names = []
    for name, val in (contacts() or {}).items():
        if name.lower() == "me":
            continue
        has = bool(val.get("phone")) if isinstance(val, dict) else bool(val)
        if has:
            names.append(name)
    held = None
    if places.has_pending():
        held = {"kind": "call", "text": f"Call {places._pending.get('who')} on {places._pending.get('number')}?"}
    elif browser.has_pending():
        p = browser._W.pending or {}
        held = {"kind": "form", "text": f"Press '{p.get('label')}' to finish the booking?"}
    return {"contacts": sorted(names, key=str.lower), "held": held,
            "city": cfg.get("assistant.city", "Dehradun")}


def phone_action(body: dict) -> dict:
    from ..tools import browser, places

    act = body.get("action")
    if act == "call":
        return {"said": places.call_number(str(body.get("who", "")))}
    if act == "hangup":
        return {"said": places.end_call()}
    if act == "info":
        return {"said": places.place_info(str(body.get("place", "")), str(body.get("question", "")))}
    if act == "book":
        try:
            people = int(body.get("people") or 2)
        except ValueError:
            people = 2
        return {"said": places.book_reservation(str(body.get("place", "")), str(body.get("when", "today")),
                                                people, str(body.get("notes", "")))}
    if act == "confirm":
        if places.has_pending():
            return {"said": places.submit_pending()}
        if browser.has_pending():
            return {"said": browser.submit_pending()}
        return {"said": "Nothing is waiting."}
    if act == "cancel":
        places.cancel_pending()
        return {"said": browser.cancel_pending()}
    return {"said": "Unknown action."}


# ---------------------------------------------------------------- brain

def brain() -> dict:
    """What Athena knows, as a graph: Athena in the middle, one hub per kind
    of knowledge, the things themselves around each hub. Built from the same
    data as the other pages - nothing new is collected for it."""
    import json
    from collections import Counter

    nodes = [{"id": "athena", "label": "ATHENA", "group": "core"}]
    edges = []

    def hub(key: str, label: str, children: list[tuple]) -> None:
        """children: (text, tooltip) or (text, tooltip, actions). An action is
        {"label", and "ask" (said to Athena) / "fill" (put in the chat box to
        finish) / "act": [page, body] (a HUD page action)} - shown when the
        dot is clicked."""
        if not children:
            return
        nodes.append({"id": key, "label": label, "group": "hub"})
        edges.append({"from": "athena", "to": key})
        for i, (text, tip, *acts) in enumerate(children):
            nid = f"{key}:{i}"
            short = text if len(text) <= 38 else text[:36] + "…"
            nodes.append({"id": nid, "label": short, "title": tip or text, "group": key,
                          "full": text, "acts": acts[0] if acts else []})
            edges.append({"from": key, "to": nid})

    mem = memory()
    for s in mem["sections"]:
        hub("mem_" + s["key"], s["title"].upper(),
            [(i["text"], f"{i['text']}\n(learned {i['added']})",
              [{"label": "🗑 Forget this", "act": ["forget", {"text": i["text"]}]}]) for i in s["items"]])
    hub("facts", "FACTS", [(f"{f['key']}: {f['value']}", "") for f in mem["facts"]])
    hub("people", "PEOPLE", [(n, "a saved contact",
                              [{"label": "📞 Call", "ask": f"call {n}"},
                               {"label": "💬 Message…", "fill": f"message {n} "}])
                             for n in phone()["contacts"]])

    # Music: artists of the songs you play, most played first. Only names
    # already looked up are used - no network from this page.
    try:
        from ..tools import taste
        names = json.loads(taste.NAMES.read_text(encoding="utf-8")) if taste.NAMES.exists() else {}
        plays = Counter(taste._spotify_history())
        artists: Counter = Counter()
        for tid, n in plays.items():
            if tid in names:
                artists[names[tid]["artist"].split(",")[0].strip()] += n
        for r in taste._read():
            artists[r["artist"].split(",")[0].strip()] += 1
        hub("music", "MUSIC", [(a, f"{c} plays", [{"label": f"▶ Play {a}", "ask": f"play {a}"}])
                               for a, c in artists.most_common(15) if a])
    except Exception:
        pass

    sk = skills()
    hub("routines", "ROUTINES", [(r, "a routine", [{"label": "▶ Run routine", "act": ["routine", {"name": r}]}])
                                 for r in sk["routines"]])
    areas: dict[str, list] = {}
    for r in sk["rows"]:
        areas.setdefault(r["area"], []).append(r)
    nodes.append({"id": "skills", "label": f"SKILLS · {sk['count']}", "group": "hub"})
    edges.append({"from": "athena", "to": "skills"})
    for a, rows in areas.items():
        aid = "area:" + a
        # Tools stay inside their area (listed on hover): 90 dots of their
        # own shrank every label on the page to nothing.
        nodes.append({"id": aid, "label": f"{a} · {len(rows)}", "group": "area",
                      "title": "\n".join(r["name"] for r in rows)})
        edges.append({"from": "skills", "to": aid})

    t = tasks()
    hub("tasks", "TASKS", [(x.get("text") or str(x), "due " + x["due"] if x.get("due") else "still to do",
                            [{"label": "✓ Mark done", "act": ["tasks", {"action": "done", "id": x.get("id")}]}])
                           for x in (t.get("today", []) + t.get("remaining", []))][:12])
    code_map = (ROOT.parent / "graphify-out" / "graph.html").exists()
    return {"nodes": nodes, "edges": edges, "code_map": code_map,
            "counts": {"things": len(nodes), "links": len(edges)}}


_code_cache: dict = {}


def _layout(ids: list[str], groups: list[int], edges: list[dict], steps: int = 150) -> dict:
    """Fruchterman-Reingold on numpy, so the page gets finished positions
    and draws a still picture - the browser settling 900 dots took ~15 s
    and wobbled all the while. Each group starts on its own spot of a
    circle, so graphify's communities come out as clusters.
    ponytail: O(n^2) per step, fine to a few thousand nodes."""
    import numpy as np
    n = len(ids)
    idx = {k: i for i, k in enumerate(ids)}
    rng = np.random.default_rng(7)
    g = np.array(groups, dtype=float)
    ang = (g * 2.399963) % (2 * np.pi)                 # golden angle per group
    pos = (np.c_[np.cos(ang), np.sin(ang)] * 40 + rng.normal(0, 6, (n, 2))).astype(np.float32)
    src = np.array([idx[e["from"]] for e in edges])
    dst = np.array([idx[e["to"]] for e in edges])
    k = 12.0
    t = 20.0
    for _ in range(steps):
        d = pos[:, None, :] - pos[None, :, :]
        dist = np.sqrt((d ** 2).sum(-1)) + 1e-3
        disp = (d * (k * k / dist ** 2)[..., None]).sum(1)          # repulsion
        e = pos[src] - pos[dst]
        el = np.sqrt((e ** 2).sum(-1)) + 1e-3
        f = e * (el / k)[:, None]                                    # attraction
        np.add.at(disp, src, -f)
        np.add.at(disp, dst, f)
        disp -= pos * 0.02                                           # pull to centre
        ln = np.sqrt((disp ** 2).sum(-1)) + 1e-3
        pos += disp / ln[:, None] * np.minimum(ln, t)[:, None]
        t = max(0.5, t * 0.975)
    pos *= 4
    return {i: (round(float(x), 1), round(float(y), 1)) for i, (x, y) in zip(ids, pos)}


def codegraph() -> dict:
    """graphify's map of Athena's own code, trimmed for the Brain page:
    code and concept nodes only (its 'rationale' notes double the count and
    say little on a picture), coloured by graphify's communities."""
    import json
    f = ROOT.parent / "graphify-out" / "graph.json"
    if not f.exists():
        return {"error": "No code map yet - run /graphify in Claude Code on this folder."}
    mt = f.stat().st_mtime
    if _code_cache.get("mt") == mt:
        return _code_cache["data"]
    saved = DATA / "codegraph.json"          # the layout takes seconds; keep it
    try:
        old = json.loads(saved.read_text(encoding="utf-8"))
        if old.get("mt") == mt:
            _code_cache.update(mt=mt, data=old["data"])
            return old["data"]
    except (OSError, ValueError):
        pass
    g = json.loads(f.read_text(encoding="utf-8"))
    keep = {n["id"]: n for n in g["nodes"] if n.get("file_type") != "rationale"}
    names: dict[int, str] = {}
    nodes = []
    for n in keep.values():
        c = int(n.get("community", -1))
        names.setdefault(c, n.get("community_name") or f"Group {c}")
        loc = f"{n.get('source_file', '')}:{str(n.get('source_location', '')).lstrip('L')}".strip(":")
        nodes.append({"id": n["id"], "label": n.get("label", n["id"]), "c": c,
                      "title": f"{n.get('label')}\n{loc}\n{names[c]}"})
    edges = [{"from": e["source"], "to": e["target"]} for e in g.get("links", [])
             if e["source"] in keep and e["target"] in keep and e["source"] != e["target"]]
    # Parts with no link fly to the edge of the picture and shrink the rest
    # to dust when it is fitted to the screen.
    linked = {e["from"] for e in edges} | {e["to"] for e in edges}
    nodes = [n for n in nodes if n["id"] in linked]
    xy = _layout([n["id"] for n in nodes], [n["c"] for n in nodes], edges)
    for n in nodes:
        n["x"], n["y"] = xy[n["id"]]
    data = {"nodes": nodes, "edges": edges, "groups": {str(k): v for k, v in names.items()},
            "counts": {"things": len(nodes), "links": len(edges)}}
    _code_cache.update(mt=mt, data=data)
    try:
        saved.write_text(json.dumps({"mt": mt, "data": data}), encoding="utf-8")
    except OSError:
        pass
    return data


def store() -> dict:
    """The RAGAZZA page: the shop's figures, straight from the website."""
    from ..tools import ragazza
    try:
        return ragazza.overview()
    except ragazza.StoreError as e:
        return {"error": str(e)}


GET = {"store": store, "phone": phone, "codegraph": codegraph, "calendar": calendar, "memory": memory, "workflows": workflows,
       "system": system, "tasks": tasks, "skills": skills, "brain": brain}
