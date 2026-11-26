"""The HUD's live panels: CPU/GPU samplers, the overview (agents, missions, jobs,
the brief), system status, where the model runs, and the live feed.

Split out of web.py (31 Oct 2026); web.py imports all of it back.
"""
from __future__ import annotations

import threading
import time

from .. import live, registry
from ..config import DATA, cfg


_web_log: list[list[str]] = []          # typed commands, merged with voice's


def _log(line: str) -> None:
    _web_log.append([time.strftime("%H:%M:%S"), line])
    del _web_log[:-40]


_net_prev: dict = {}
_cpu = {"now": 0.0, "hist": []}
_status_seen = {"t": 0.0}


def _watched() -> bool:
    """True while a HUD has asked for readings in the last 15 seconds.

    The samplers used to run from the first HUD poll until the server
    exited - an nvidia-smi process every two seconds and a CPU reading every
    second, all day, with no window open to show them. They now stop when
    nobody is looking and start again on the next poll.
    """
    return time.time() - _status_seen["t"] < 15


def _cpu_sampler() -> None:
    """One CPU reading a second, shared by every open HUD.

    psutil measures CPU as the change since its previous call. With the
    phone, the desktop window and a browser all polling, calls landed
    milliseconds apart and each measured an interval too short to see any
    load - the panel sat at 0% on a busy machine.
    """
    import psutil

    while _watched():
        v = psutil.cpu_percent(interval=1.0)
        _cpu["now"] = v
        _cpu["hist"] = (_cpu["hist"] + [v])[-24:]
    _cpu["started"] = False


_gpu: dict = {"now": None}


def _gpu_sampler() -> None:
    """One nvidia-smi reading every two seconds, shared by every open HUD.

    Each nvidia-smi call took 77-104 ms here, so running it inside /status
    would add that to every poll from every open HUD. A missing or failing
    nvidia-smi leaves the reading at None and the HUD shows dashes.
    """
    import subprocess

    while _watched():
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,"
                 "memory.total,temperature.gpu", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
            # First GPU only - the laptop has one; an iGPU doesn't show up here.
            util, used, total, temp = (float(x) for x in out.splitlines()[0].split(","))
            _gpu["now"] = {"util": round(util), "vram_used": round(used / 1024, 1),
                           "vram_total": round(total / 1024, 1), "temp": round(temp)}
        except Exception:
            _gpu["now"] = None
        time.sleep(2.0)
    _gpu["started"] = False


# ---------------------------------------------------------------- overview
# The Command Center's slower panels: today's timeline, memory, providers,
# weather. Reading them means the calendar, Gmail and a weather service, so
# they are gathered on a background thread every few minutes and the page is
# handed the last result at once - never a page load waiting on the network.
_overview_cache: dict = {"data": None, "t": 0.0}


_overview_lock = threading.Lock()


def _gather_overview() -> dict:
    import datetime as dt
    from .. import cloud
    from ..config import DATA

    out: dict = {"t": time.time()}
    # Today's timeline, each item marked done / now / next.
    try:
        from ..tools import brief
        events, mail = brief.collect(dt.date.today())
        now = dt.datetime.now()
        rows = []
        for e in events:
            s = brief._local(e["start"])
            end = brief._local(e["end"]) if e.get("end") else s
            state = ("now" if s <= now < end else "done" if end <= now else "next")
            rows.append({"what": e["what"], "at": "all day" if e["all_day"] else s.strftime("%H:%M"),
                         "until": end.strftime("%H:%M"), "where": e.get("where", ""),
                         "state": state, "mins": int((s - now).total_seconds() // 60)})
        # Teams classes, as read by tools/teams.py this morning (cancelled ones left out).
        try:
            from ..tools import teams
            seen = {r["what"].lower() for r in rows}
            for m in teams._meetings_today():
                if m["canceled"] or m["title"].lower() in seen:
                    continue
                s = dt.datetime.combine(dt.date.today(), dt.time.fromisoformat(m["start"]))
                e = dt.datetime.combine(dt.date.today(), dt.time.fromisoformat(m["end"]))
                rows.append({"what": m["title"], "at": m["start"], "until": m["end"], "where": "Teams",
                             "state": "now" if s <= now < e else "done" if e <= now else "next",
                             "mins": int((s - now).total_seconds() // 60)})
        except Exception:
            pass
        # UPES timetable (upes.py), from the last portal read - with rooms.
        try:
            from .. import upes
            seen = {r["what"].lower() for r in rows}
            for c in upes.classes(cached_only=True):
                if c["cancelled"] or c["course"].lower() in seen:
                    continue
                s = dt.datetime.combine(dt.date.today(), dt.time.fromisoformat(c["start"]))
                e = dt.datetime.combine(dt.date.today(), dt.time.fromisoformat(c["end"]))
                rows.append({"what": c["course"], "at": c["start"], "until": c["end"], "where": c["room"],
                             "state": "now" if s <= now < e else "done" if e <= now else "next",
                             "mins": int((s - now).total_seconds() // 60)})
        except Exception:
            pass
        rows.sort(key=lambda r: r["at"])
        out["schedule"], out["unread"] = rows, len(mail)
        # Senders only - brief gives "subject - from who"; the subject stays out.
        out["mail_from"] = [m.rsplit(" - from ", 1)[-1] for m in mail if " - from " in m][:4]
        tomorrow = brief._first_tomorrow()
        out["tomorrow"] = ({"what": tomorrow["what"],
                            "at": brief._local(tomorrow["start"]).strftime("%H:%M")}
                           if tomorrow else None)
    except Exception as e:
        out["schedule"], out["unread"], out["schedule_error"] = [], None, str(e)[:120]
    # Memory.
    try:
        from .. import db, profile
        items = [i for i in profile.load() if i.live()]
        out["memory"] = {
            "learned": len(items),
            "sections": {k: sum(1 for i in items if i.section == k) for k in profile.SECTIONS},
            "chats": len(list((DATA / "chats").glob("*.json"))),
            "snippets": db.count("chat"),
            "style": any(i.section == "style" for i in items),
        }
    except Exception:
        out["memory"] = None
    # Providers: a saved key is "connected"; Ollama lists what is pulled.
    prov = {}
    for name in ("groq", "gemini", "openrouter", "cerebras"):
        try:
            prov[name] = bool(cloud.get_key(name))
        except Exception:
            prov[name] = False
    try:
        import ollama
        prov["ollama"] = len(ollama.Client(host=cfg.get("llm.host")).list().models)
    except Exception:
        prov["ollama"] = 0
    out["providers"] = prov
    out["planner"] = cfg.get("planner.model", "") if cfg.get("planner.enabled", True) else ""
    try:
        from ..tools import routines
        out["routines"] = list(routines.load())[:8]
    except Exception:
        out["routines"] = []
    # Command Centre: who messaged (sender only), open deadlines, today's reminders.
    try:
        from .. import phone_notify
        out["senders"] = phone_notify.recent_senders()
        out["notify_muted"] = phone_notify.muted()
    except Exception:
        out["senders"] = []
    try:
        from ..tools import todo
        today = dt.date.today()
        due = sorted((t for t in todo.load() if not t["done"] and t.get("due")), key=lambda t: t["due"])
        out["deadlines"] = [{"text": t["text"][:60], "due": t["due"],
                             "days": (dt.date.fromisoformat(t["due"][:10]) - today).days} for t in due[:3]]
    except Exception:
        out["deadlines"] = []
    try:
        from ..tools.scheduler import pending as load_jobs
        rem = []
        for j in load_jobs():
            at = dt.datetime.fromisoformat(j["fire_at"])
            if at.date() == dt.date.today():
                rem.append({"what": j["text"][:50], "at": at.strftime("%H:%M"),
                            "mins": int((at - dt.datetime.now()).total_seconds() // 60)})
        out["reminders"] = sorted(rem, key=lambda r: r["at"])
    except Exception:
        out["reminders"] = []
    out["tools"] = len(registry.REGISTRY)
    out["city"] = cfg.get("assistant.city", "Dehradun")
    try:
        from ..tools.web import get_weather
        w = get_weather(out["city"])
        out["weather"] = "" if w.startswith("ERROR") else w.split("\n")[0].split(": ", 1)[-1][:60]
    except Exception:
        out["weather"] = ""
    return out


def _overview_cached() -> dict:
    from .views import _sources_changed
    fresh = time.time() - _overview_cache["t"] < 180 and _overview_cache["t"] > _sources_changed()
    if not fresh and _overview_lock.acquire(blocking=False):
        def work():
            try:
                _overview_cache["data"] = _gather_overview()
                _overview_cache["t"] = time.time()
            finally:
                _overview_lock.release()
        threading.Thread(target=work, daemon=True, name="overview").start()
    return _overview_cache["data"] or {}


def _system_status() -> dict:
    """Real readings for the side panels - nothing on the HUD is decoration."""
    import psutil

    _status_seen["t"] = time.time()
    if not _cpu.get("started"):
        _cpu["started"] = True
        threading.Thread(target=_cpu_sampler, daemon=True, name="cpu").start()
    if not _gpu.get("started"):
        _gpu["started"] = True
        threading.Thread(target=_gpu_sampler, daemon=True, name="gpu").start()

    vm = psutil.virtual_memory()
    try:
        disk = psutil.disk_usage("C:\\")
    except Exception:
        disk = None
    bat = psutil.sensors_battery()

    # Network speed is a difference between two readings, so remember the last.
    now = time.time()
    io = psutil.net_io_counters()
    up = down = 0.0
    if _net_prev:
        dt = max(0.001, now - _net_prev["t"])
        up = (io.bytes_sent - _net_prev["s"]) / dt
        down = (io.bytes_recv - _net_prev["r"]) / dt
    _net_prev.update(t=now, s=io.bytes_sent, r=io.bytes_recv)

    from ..tools import study
    return {
        "cpu": _cpu["now"],
        "cpu_hist": _cpu["hist"],
        "cores": psutil.cpu_count(),
        "gpu": _gpu["now"],     # {util %, vram_used/total GB, temp C} or None
        "mem_used": round(vm.used / 2**30, 1),
        "mem_total": round(vm.total / 2**30, 1),
        "mem_pct": vm.percent,
        "disk_pct": disk.percent if disk else 0,
        "disk_free": round(disk.free / 2**30) if disk else 0,
        "battery": round(bat.percent) if bat else None,
        "plugged": bool(bat.power_plugged) if bat else None,
        "up_kbs": round(up / 1024, 1),
        "down_kbs": round(down / 1024, 1),
        "uptime_h": round((now - psutil.boot_time()) / 3600, 1),
        "study_model": study.current_model(),
        "everyday_model": cfg.get("llm.model", ""),
        "procs": len(psutil.pids()),
        "user": cfg.get("assistant.user_name", "OPERATOR"),
        "model_on": _where_model_runs(),
    }


_where = {"t": 0.0, "v": "unknown"}


def _where_model_runs() -> str:
    """GPU, CPU or mixed - checked every ten seconds, not on every poll."""
    if time.time() - _where["t"] > 10:
        try:
            from ..llm import LLM
            _where["v"] = LLM().processor()
        except Exception:
            _where["v"] = "unknown"
        _where["t"] = time.time()
    return _where["v"]


def _live_payload() -> dict:
    voice = live.read_voice()
    merged = sorted(voice.get("log", []) + _web_log, key=lambda e: e[0])[-40:]
    voice["log"] = merged
    voice["time"] = time.strftime("%H:%M:%S")
    from .. import modes
    voice["mode"] = modes.current()          # read here: data/mode.json is shared by every process
    voice["jobs"] = _jobs()
    return voice


_JOBS: dict = {"t": 0.0, "v": []}


def _jobs() -> list[dict]:
    """What is working in the background, for the HUD's timer chips: Deck
    consoles Athena started on a task, and running missions (Hedwig's
    "Claude Code · Building your website · 11s", 23 Oct 2026). /live is polled
    every 300 ms, so this is worked out at most every 2 s."""
    if time.time() - _JOBS["t"] < 2:
        return _JOBS["v"]
    out: list[dict] = []
    try:
        from .. import deck_link
        out += [{"who": j["who"], "title": j["title"], "since": j["started"], "state": j["state"], "console": j["id"]}
                for j in deck_link.jobs() if deck_link.job_active(j)]
    except Exception:
        pass
    try:
        from .. import missions
        out += [{"who": missions.WORKERS.get(m.get("worker"), "Athena"), "title": m["title"], "since": m["started"],
                 "state": m["status"], "mission": m["id"]}
                for m in missions.load() if m["status"] in ("running", "starting") and not m.get("parent") and not m.get("loop")]
    except Exception:
        pass
    _JOBS.update(t=time.time(), v=out)
    return out
