"""Telegram chat (jarvis/telegram.py) against a fake Telegram - no network, no real bot.

    python tests/telegramtest.py
"""
import json
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis import cloud, share, telegram  # noqa: E402

bad = []
telegram.STATE = Path(tempfile.mkdtemp()) / "telegram.json"


# -- the HTTP call itself, against a local server that answers like Telegram
class Fake(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path.endswith("/getMe"):
            code, out = 200, {"ok": True, "result": {"username": "athena_test_bot"}}
        else:
            code, out = 401, {"ok": False, "error_code": 401, "description": "Unauthorized"}
        self.send_response(code)
        self.end_headers()
        self.wfile.write(json.dumps(out | {"echo": body}).encode())

    def log_message(self, *a):
        pass


srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()
api = f"http://127.0.0.1:{srv.server_port}/bot{{token}}/{{method}}"
if telegram.call("SECRET123", "getMe", api)["username"] != "athena_test_bot":
    bad.append("getMe didn't come back")
try:
    telegram.call("SECRET123", "sendMessage", api, chat_id=1, text="x")
    bad.append("a refused call didn't raise")
except telegram.TelegramError as e:
    if e.code != 401 or "SECRET123" in str(e):
        bad.append(f"refusal came back as {e.code} {e!r}")
srv.shutdown()


# -- the bot, with Telegram replaced by a script
class Scripted(telegram.Bot):
    def __init__(self, *a, **k):
        self.sent, self.actions = [], []
        super().__init__(*a, **k)

    def call(self, method, **p):
        if method == "sendMessage":
            self.sent.append((p["chat_id"], p["text"]))
        elif method == "sendChatAction":
            self.actions.append(p["chat_id"])
        return {}


asked, logs = [], []


def ask(text):
    asked.append(text)
    time.sleep(0.1)
    return {"long": "line\n" * 1500, "file": "Here it is. [share:abc123]"}.get(text, f"you said {text}")


def msg(chat, text, kind="private", ago=0):
    return {"update_id": 1, "message": {"chat": {"id": chat, "type": kind}, "text": text, "date": time.time() - ago}}


b = Scripted("t", ask, logs.append)
code = b.state["code"]
if len(code) != 6 or not any(code in line for line in logs) or json.loads(telegram.STATE.read_text())["code"] != code:
    bad.append(f"no pairing code shown/saved: {logs}")
b.handle(msg(5, code, kind="group"))
if b.sent or b.state.get("owner"):
    bad.append("a group chat could link")
b.handle(msg(7, "000000" if code != "000000" else "111111"))
if b.state.get("owner") or "six-digit code" not in b.sent[-1][1]:
    bad.append("a wrong code linked, or got no hint")
b.handle(msg(7, f"/start {code}"))
if b.state.get("owner") != 7 or json.loads(telegram.STATE.read_text()) != {"owner": 7} or "Linked" not in b.sent[-1][1]:
    bad.append(f"the right code didn't link: {b.state} {b.sent[-1:]}")
n = len(b.sent)
b.handle(msg(9, "open chrome"))
if len(b.sent) != n or asked:
    bad.append("someone else's chat was answered")
b.handle(msg(7, "what's the time"))
if asked != ["what's the time"] or b.sent[-1] != (7, "you said what's the time") or 7 not in b.actions:
    bad.append(f"the owner's message wasn't answered: {asked} {b.sent[-1:]} typing={b.actions}")
b.handle(msg(7, "shut down the pc", ago=3600))
if "shut down the pc" in asked or "I was off" not in b.sent[-1][1]:
    bad.append("an old message was run")
n = len(b.sent)
b.handle(msg(7, "long"))
parts = b.sent[n:]
if len(parts) < 2 or any(len(t) > telegram.LIMIT for _, t in parts):
    bad.append(f"a long reply wasn't split: {[len(t) for _, t in parts]}")
share.resolve = lambda i: Path("C:/x/lab manual.pdf") if i == "abc123" else None
b.handle(msg(7, "file"))
if "[share:" in b.sent[-1][1] or "lab manual.pdf" not in b.sent[-1][1]:
    bad.append(f"share marker reached Telegram: {b.sent[-1][1]!r}")

telegram.STATE.unlink()
c = Scripted("t", ask, logs.append)
for _ in range(telegram.MAX_WRONG_CODES):
    c.handle(msg(8, "000000" if c.state["code"] != "000000" else "111111"))
c.handle(msg(8, c.state["code"]))
if c.state.get("owner"):
    bad.append("linking still worked after five wrong codes")


class Refused(Scripted):
    def call(self, method, **p):
        if method == "getUpdates":
            raise telegram.TelegramError(401, "Unauthorized")
        return super().call(method, **p)


r = Refused("t", ask, logs.append)
t = threading.Thread(target=r.run, daemon=True)
t.start()
t.join(3)
if t.is_alive() or "doesn't work" not in logs[-1]:
    bad.append("a bad key kept polling")

cloud.get_key = lambda p: ""
if "@BotFather" not in telegram.telegram_status():
    bad.append("status without a key doesn't say how to set it up")
cloud.get_key = lambda p: "t"
telegram.save_json(telegram.STATE, {"code": "123456"})
if "123456" not in telegram.telegram_status():
    bad.append("status doesn't give the code")

for x in bad:
    print("FAIL", x)
print(f"telegram: {len(bad)} failure(s)")
sys.exit(1 if bad else 0)
