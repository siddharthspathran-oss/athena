"""Text Athena from Telegram, from anywhere.

The bot is yours: make it with @BotFather and save its token with
`python add_keys.py` (provider "telegram") - it lives in Windows Credential
Manager, never in a file or a chat. The web process long-polls Telegram (no
open port, works behind any router) and answers with the same agent and
conversation as the phone page, so "yes" to "that needs your OK first" works
the same way (offer.py).

Only you: until a chat is linked, the PC shows a six-digit code (HUD feed,
and "telegram status" asks Athena for it); the first private chat to send it
becomes the owner in data/telegram.json and every other chat is ignored
without a reply. Five wrong codes and linking stops until Athena restarts.
Anything sent while Athena was off is not run when she comes back - an old
"shut down the PC" must not fire hours later.

What passes through Telegram's servers: your messages and her text replies.
Files never do - "send me X" still waits on Athena's own phone page.
"""
from __future__ import annotations

import json
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from typing import Callable

from . import registry
from .config import DATA
from .store import load_json, save_json

STATE = DATA / "telegram.json"
API = "https://api.telegram.org/bot{token}/{method}"
LIMIT = 4096                 # Telegram's longest message
POLL_SECONDS = 50            # long poll: Telegram holds the request until a message comes
STALE_SECONDS = 600          # sent longer ago than this (Athena was off): not run
MAX_WRONG_CODES = 5
_SHARE = re.compile(r"\s*\[share:([\w-]+)\]")

BOT: "Bot | None" = None


def chunks(text: str, n: int = LIMIT) -> list[str]:
    """Split at a line break near the limit where there is one."""
    out = []
    while len(text) > n:
        cut = text.rfind("\n", 0, n)
        cut = cut if cut > n // 2 else n
        out.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return out + [text] if text.strip() else out


def _files_named(answer: str) -> str:
    """[share:id] markers become the file's name: the file itself stays on
    Athena's phone page and never goes through Telegram."""
    from . import share

    def name(m: re.Match) -> str:
        path = share.resolve(m.group(1))
        return f"\n(Waiting on Athena's phone page: {path.name})" if path else ""
    return _SHARE.sub(name, answer)


def call(token: str, method: str, api: str = API, **params) -> dict | list:
    # The URL holds the token, so it is never logged or put in an error.
    req = urllib.request.Request(api.format(token=token, method=method),
                                 data=json.dumps(params).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=POLL_SECONDS + 15) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            data = json.loads(e.read())
        except Exception:
            data = {"error_code": e.code, "description": f"HTTP {e.code}"}
    if not data.get("ok"):
        raise TelegramError(data.get("error_code", 0), data.get("description", "Telegram said no"))
    return data["result"]


class Bot:
    def __init__(self, token: str, ask: Callable[[str], str], log: Callable[[str], None] = print,
                 api: str = API) -> None:
        self.token, self.ask, self.log, self.api = token, ask, log, api
        self.state = load_json(STATE, {})
        self.wrong = 0
        self.stopped = False
        if not self.state.get("owner"):
            self.state = {"code": f"{secrets.randbelow(10 ** 6):06d}"}
            save_json(STATE, self.state)
            self.log(f"Telegram: send {self.state['code']} to your bot to link it")

    def call(self, method: str, **params) -> dict | list:
        return call(self.token, method, self.api, **params)

    def send(self, chat: int, text: str) -> None:
        for part in chunks(text) or ["Done."]:
            self.call("sendMessage", chat_id=chat, text=part, disable_web_page_preview=True)

    def _typing(self, chat: int, done: threading.Event) -> None:
        while not done.is_set():           # the dots last 5 s, a request can take a minute
            try:
                self.call("sendChatAction", chat_id=chat, action="typing")
            except Exception:
                return
            done.wait(4)

    def handle(self, update: dict) -> None:
        msg = update.get("message") or {}
        chat = msg.get("chat") or {}
        if chat.get("type") != "private":
            return
        cid, text = chat["id"], (msg.get("text") or "").strip()
        owner = self.state.get("owner")
        if not owner:
            self._link(cid, text)
            return
        if cid != owner:
            return                         # not yours: no reply, so the bot gives nothing away
        if time.time() - msg.get("date", 0) > STALE_SECONDS:
            self.send(cid, "I was off when you sent that, so I didn't do it. Send it again if you still want it.")
            return
        if not text:
            self.send(cid, "I can only read text here.")
            return
        if text in ("/start", "/help"):
            self.send(cid, "Text me anything you'd say to me at the PC.")
            return
        done = threading.Event()
        threading.Thread(target=self._typing, args=(cid, done), daemon=True).start()
        try:
            answer = self.ask(text)
        except Exception as e:
            answer = f"That failed: {str(e)[:200]}"
        finally:
            done.set()
        self.send(cid, _files_named(answer))

    def _link(self, cid: int, text: str) -> None:
        if self.wrong >= MAX_WRONG_CODES:
            return
        code = re.sub(r"\D", "", text)       # "/start 123456", "123 456"
        if code and secrets.compare_digest(code, self.state.get("code", "")):
            self.state = {"owner": cid}
            save_json(STATE, self.state)
            self.log("Telegram: linked to your chat")
            self.send(cid, "Linked. Text me anything you'd say to me at the PC.")
            return
        if code:
            self.wrong += 1
        self.send(cid, "Send the six-digit code shown on your PC to link this chat.")

    def run(self) -> None:
        offset, fails = None, 0
        while not self.stopped:
            try:
                updates = self.call("getUpdates", timeout=POLL_SECONDS, offset=offset, allowed_updates=["message"])
                fails = 0
            except TelegramError as e:
                if e.code == 401:
                    self.log("Telegram: the saved bot key doesn't work - save it again with add_keys.py")
                    return
                fails += 1                 # 409: another Athena is polling the same bot
                self.log(f"Telegram: {e.description[:80]}")
                time.sleep(min(60, 2 ** fails))
                continue
            except Exception as e:
                fails += 1
                if fails in (1, 5):
                    self.log(f"Telegram: can't reach it ({str(e)[:60]})")
                time.sleep(min(60, 2 ** fails))
                continue
            for u in updates:
                offset = u["update_id"] + 1
                try:
                    self.handle(u)
                except Exception as e:
                    self.log(f"Telegram: {str(e)[:80]}")


class TelegramError(RuntimeError):
    def __init__(self, code: int, description: str) -> None:
        super().__init__(description)
        self.code, self.description = code, description


def check(token: str) -> str:
    """The bot's @name, which also proves the token works (add_keys.py)."""
    return "@" + call(token, "getMe")["username"]


def start(ask: Callable[[str], str], log: Callable[[str], None] = print) -> None:
    """Called by the web process; does nothing until a bot key is saved."""
    global BOT
    from . import cloud
    token = cloud.get_key("telegram")
    if not token or BOT:
        return
    BOT = Bot(token, ask, log)
    threading.Thread(target=BOT.run, daemon=True, name="telegram").start()


@registry.tool(tags=["system"])
def telegram_status() -> str:
    """Whether Athena can be texted on Telegram, and the code to link it. Use for
    "set up telegram", "telegram code", "is telegram linked"."""
    from . import cloud
    if not cloud.get_key("telegram"):
        return ("Telegram isn't set up. Make a bot with @BotFather in Telegram, then run "
                "python add_keys.py and save its token as provider telegram.")
    state = load_json(STATE, {})
    if state.get("owner"):
        return "Telegram is linked to your chat - text the bot any time."
    if state.get("code"):
        return f"Send {state['code']} to your bot in Telegram to link it."
    return "The bot key is saved - restart Athena's web process and I'll give you a code."


if __name__ == "__main__":
    assert chunks("a" * 10, 4) == ["aaaa", "aaaa", "aa"]
    assert chunks("ab\ncd\nef", 6) == ["ab\ncd", "ef"]
    assert chunks("") == []
    print("telegram: chunks ok")
