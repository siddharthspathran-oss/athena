"""athena-server (jarvis/server.py): the tool gate, reminders kept on the
server, "say it" handovers, the morning brief's window. Nothing real is sent;
reminders go to a temp jobs.json.

    python tests/servertest.py
"""
import datetime as dt
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis import registry, server  # noqa: E402
import jarvis.tools  # noqa: E402,F401
from jarvis.tools import scheduler  # noqa: E402
from jarvis.ui import web_base  # noqa: E402

bad = []
scheduler.JOBS_FILE = Path(tempfile.mkdtemp()) / "jobs.json"
web_base.get_token = lambda: "tok-test"

# --- the tool gate: only TOOLS run or are shown on the server ---
registry.ONLY = set(server.TOOLS)
if not registry.get("set_volume").run(level=10).startswith("ERROR: that needs the laptop"):
    bad.append("a PC tool ran with the server's gate on")
if "set_volume" in registry.select("set the volume to 10", everything=True):
    bad.append("a PC tool was shown to the model on the server")
if "get_datetime" not in registry.select("what time is it"):
    bad.append("a server tool was hidden")
registry.ONLY = None
if "set_volume" not in registry.select("set the volume to 10"):
    bad.append("the gate stayed on after ONLY went back to None")

# --- no server set up, or unreachable: reminders stay local, as before ---
server.cfg.get = (lambda real: lambda k, d=None: {"server.url": ""}.get(k, real(k, d)))(server.cfg.get)
if server.remote("list") is not None:
    bad.append("remote() answered with no server set up")
said = scheduler.add_reminder("drink water", "in 20 minutes")
if not said.startswith("Reminder set for") or len(scheduler._load()) != 1:
    bad.append(f"a local reminder wasn't kept: {said}")

# --- a "say it" from the server: fires once, quietly (no second phone ring) ---
scheduler.say_now("dentist")
fired = scheduler._tick()
if fired != [("dentist", True)]:
    bad.append(f"say_now fired {fired}, not [('dentist', True)]")
if any(j["text"] == "dentist" for j in scheduler.pending()):
    bad.append("a 'say it' showed up as a pending reminder")
scheduler._append("old news", dt.datetime.now() - dt.timedelta(minutes=30), quiet=True)
if scheduler._tick() or any(j["text"] == "old news" for j in scheduler._load()):
    bad.append("a stale 'say it' (laptop was asleep) wasn't dropped silently")

# --- the server's reminder API, as the laptop calls it ---
api = ThreadingHTTPServer(("127.0.0.1", 0), server.Api)
threading.Thread(target=api.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{api.server_address[1]}"
server.ACTIVE = True                       # the handler must keep it here, not forward it on
out = server._post(url, "/reminders", {"op": "add", "text": "call mum", "when": "at 18:30"})
if not str(out.get("result", "")).startswith("Reminder set for"):
    bad.append(f"add over the API: {out}")
out = server._post(url, "/reminders", {"op": "add_at", "text": "lab record",
                                        "fire_at": "2030-01-01T09:00:00"})
again = server._post(url, "/reminders", {"op": "add_at", "text": "lab record",
                                          "fire_at": "2030-01-01T09:00:00"})
if (out.get("result"), again.get("result")) != ("Set.", "Already set."):
    bad.append(f"add_at twice: {out} then {again}")
jobs = server._post(url, "/reminders", {"op": "jobs"}).get("result")
if sorted(j["text"] for j in jobs) != ["call mum", "drink water", "lab record"]:
    bad.append(f"jobs over the API: {jobs}")
out = server._post(url, "/reminders", {"op": "cancel", "reminder_id": "call mum"})
if not str(out.get("result", "")).startswith("Cancelled"):
    bad.append(f"cancel over the API: {out}")
import httpx  # noqa: E402
if httpx.post(url + "/reminders", json={"op": "list"}, headers={"X-Token": "wrong"}).status_code != 401:
    bad.append("a wrong token wasn't refused")
server.ACTIVE = False
api.shutdown()
if server._post("http://127.0.0.1:9", "/reminders", {"op": "list"}) is not None:
    bad.append("an unreachable server didn't read as None (keep it local)")

# --- the morning brief: once a day, 8 to 11 ---
day = dt.datetime(2026, 11, 9)
for now, sent, want in ((day.replace(hour=7, minute=59), "", False), (day.replace(hour=8), "", True),
                        (day.replace(hour=10, minute=59), "", True), (day.replace(hour=11), "", False),
                        (day.replace(hour=8, minute=5), "2026-11-09", False),
                        (day.replace(hour=8, minute=5), "2026-11-08", True)):
    if server.brief_due(now, "08:00", sent) != want:
        bad.append(f"brief_due({now:%H:%M}, sent={sent!r}) should be {want}")

for x in bad:
    print("FAIL", x)
print(f"server: {len(bad)} failure(s)")
sys.exit(1 if bad else 0)
