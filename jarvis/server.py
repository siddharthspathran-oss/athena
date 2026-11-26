"""athena-server: the always-on mini PC (9 Nov 2026).

`python run.py server` there (a startup task, nobody signed in). It polls
Telegram, keeps the reminders and sends the morning brief, so all three work
with the laptop shut.

  * A Telegram message goes to the laptop's Athena first - every tool, same
    chat as the phone page. Only when the laptop can't be reached does the
    server answer itself: the cloud brain and the few TOOLS below, none of
    which can touch a PC.
  * Reminders live here. The laptop's reminder tools ask this server
    (scheduler.py) and keep one locally only when it can't be reached. When
    one fires, the server rings the phone and, if the laptop is awake, has it
    sound the alarm and say it too (/remind on the laptop).
  * Class and important-email alerts run here too; the laptop skips them
    while a server is set up (alerts.py), so nothing rings twice.

Both machines share config.yaml and data/web_token.txt, the token each side
sends the other. Keys reach the server with server/send_keys.py, run on the
laptop by you.
"""
from __future__ import annotations

import datetime as dt
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .config import DATA, cfg
from .store import load_json, save_json

ACTIVE = False                   # True only inside `run.py server`
STATE = DATA / "server_state.json"
ASK_SECONDS = 300                # the laptop can take minutes on a real job
BRIEF_LATE_HOURS = 3             # booted after 8 AM: still send it until 11, not at 3 PM
ASLEEP = "\n\n(Your laptop is asleep, so the mini PC answered - it can't use the PC right now.)"

# What the server may run when it answers on its own: lookups and reminders.
TOOLS = {"get_datetime", "world_time", "calculate", "wolfram", "web_search", "read_webpage",
         "get_weather", "explain", "add_reminder", "list_reminders", "cancel_reminder",
         "daily_brief", "show_timetable", "telegram_status"}


def elsewhere() -> bool:
    """On the laptop, with a server set up: Telegram, reminders, class and mail
    alerts are the server's job."""
    return bool(cfg.get("server.url", "")) and not ACTIVE


def _post(base: str, path: str, body: dict, read: float = 10.0) -> dict | None:
    """JSON to the other machine. None when it can't be reached at all (asleep,
    off, no network); a reply that is slow or broken raises."""
    import httpx

    from .ui.web_base import get_token
    try:
        r = httpx.post(base.rstrip("/") + path, json=body, headers={"X-Token": get_token()},
                       timeout=httpx.Timeout(read, connect=3.0))
    except (httpx.ConnectError, httpx.ConnectTimeout):
        return None
    if r.status_code == 401:
        raise RuntimeError("the laptop and server tokens differ - run server/send_keys.py again")
    return r.json()


def remote(op: str, **kw) -> object | None:
    """A reminder operation done on the server, from the laptop. None = no
    server set up, or it can't be reached: the caller keeps it local."""
    url = cfg.get("server.url", "")
    if ACTIVE or not url:
        return None
    try:
        out = _post(url, "/reminders", {"op": op, **kw})
    except Exception as e:
        print(f"[server: {str(e)[:120]}]")
        return None
    return None if out is None else out.get("result")


def _laptop(path: str, body: dict, read: float) -> dict | None:
    url = cfg.get("server.laptop_url", "")
    return _post(url, path, body, read) if url else None


def _say_on_laptop(text: str) -> None:
    """A reminder fired here: the laptop sounds its alarm too, if it's awake."""
    try:
        if _laptop("/remind", {"text": text}, 10) is None:
            print(f"reminder: laptop asleep, phone only: {text[:60]}")
    except Exception as e:
        print(f"reminder: laptop didn't take it ({str(e)[:80]})")


def _asker(agent, lock: threading.Lock):
    import httpx

    def ask(text: str) -> str:
        try:
            out = _laptop("/ask", {"text": text, "via": "telegram"}, ASK_SECONDS)
        except httpx.ReadTimeout:
            return "Your laptop is still working on that. Ask me again in a bit for how it went."
        except Exception as e:
            print(f"telegram: laptop failed ({str(e)[:80]}), answering here")
            out = None
        if out is not None:
            return out.get("answer") or out.get("error") or "Done."
        with lock:
            agent.confirm = agent._offer_instead
            return agent.ask(text) + ASLEEP
    return ask


def brief_due(now: dt.datetime, at: str, sent: str) -> bool:
    """True once a day, from `at` until BRIEF_LATE_HOURS later."""
    h, m = (int(x) for x in at.split(":"))
    start = dt.datetime.combine(now.date(), dt.time(h, m))
    return sent != now.date().isoformat() and start <= now < start + dt.timedelta(hours=BRIEF_LATE_HOURS)


def send_brief() -> str:
    """The day ahead plus the weather, to the phone (ntfy, Telegram)."""
    from . import phone
    from .tools.brief import daily_brief
    from .tools.web import get_weather

    text = f"{daily_brief('today')}\n\n{get_weather(cfg.get('server.brief_city', 'Dehradun'))}"
    return phone.push(text, title="Your today brief", priority="default", tags="sunrise")[1]


def _morning(log) -> None:
    while True:
        try:
            state = load_json(STATE, {})
            if brief_due(dt.datetime.now(), str(cfg.get("server.brief_at", "08:00")), state.get("brief", "")):
                # Marked first: a brief that keeps failing must not retry every 30 s all morning.
                save_json(STATE, {**state, "brief": dt.date.today().isoformat()})
                log(f"morning brief: {send_brief()}")
        except Exception as e:
            log(f"morning brief failed: {str(e)[:120]}")
        time.sleep(30)


def reminder_op(body: dict) -> object:
    """What the laptop asked of the reminder queue (POST /reminders)."""
    from .tools import scheduler as s

    op = body.get("op")
    if op == "add":
        return s.add_reminder(str(body.get("text", "")), str(body.get("when", "")), str(body.get("repeat", "once")))
    if op == "add_at":
        return s.add_at(str(body.get("text", "")), dt.datetime.fromisoformat(str(body.get("fire_at"))))
    if op == "list":
        return s.list_reminders()
    if op == "jobs":
        return s.pending()
    if op == "cancel":
        return s.cancel_reminder(str(body.get("reminder_id", "")))
    raise ValueError(f"unknown reminder op {op!r}")


class Api(BaseHTTPRequestHandler):
    def _json(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args) -> None:
        pass

    def do_GET(self) -> None:
        self._json(200, {"ok": True}) if self.path == "/health" else self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        from .ui.web_base import get_token
        if self.path != "/reminders":
            self._json(404, {"error": "not found"})
            return
        if not secrets.compare_digest(self.headers.get("X-Token", ""), get_token()):
            self._json(401, {"error": "Bad or missing token."})
            return
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            self._json(200, {"result": reminder_op(body)})
        except Exception as e:
            self._json(400, {"error": str(e)[:200]})


def run() -> None:
    global ACTIVE
    ACTIVE = True
    import os

    import certifi
    # A freshly reset Windows has few root certificates and fetches more only
    # for browsers, so Telegram failed CERTIFICATE_VERIFY_FAILED (9 Nov 2026).
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    from . import alerts, registry, render, telegram
    from .agent import Agent
    from .tools import scheduler

    def log(line: str) -> None:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {line}", flush=True)

    registry.ONLY = set(TOOLS)
    render.AUTO_OPEN = False               # no screen here: never open a browser
    agent, lock = Agent(), threading.Lock()
    scheduler.set_notifier(_say_on_laptop)
    scheduler.start_scheduler("server")
    alerts.start(log)
    telegram.start(_asker(agent, lock), log)
    threading.Thread(target=_morning, args=(log,), daemon=True, name="brief").start()
    port = int(cfg.get("server.port", 8421))
    log(f"athena-server up: reminders on port {port}, laptop at {cfg.get('server.laptop_url', '(none)')}")
    ThreadingHTTPServer(("0.0.0.0", port), Api).serve_forever()
