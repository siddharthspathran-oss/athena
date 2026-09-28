"""Reaching the user's phone when something matters: a ringing call and a push.

Two free services, used together because neither is enough alone:

  * CallMeBot's Telegram call API rings the phone like a real Telegram call
    and reads the message aloud (Google text-to-speech). It is a single shared
    bot run by one person, so it can be slow, queued or rate-limited - which
    is fine for "your class is in ten minutes" and useless as the only path.
  * ntfy.sh delivers a push to the ntfy app at "urgent" priority - a long,
    alarm-style buzz that can break through Do Not Disturb if the user allows
    it in the app. It is quick and dependable, so it goes out on every alert,
    and if the call fails the push has still arrived.

No account, key or payment on either. What that costs is privacy: the text
goes to CallMeBot, Google's TTS and ntfy.sh. So alert text is kept to the bare
fact ("Email from Prof Example", never the subject), and the ntfy topic is a long
random name, because ntfy.sh topics are public to anyone who knows the name -
the name is the only lock. Both identifiers live in data/phone.json, never in
config.yaml, which gets pasted into chats.

Set up with:  python run.py alerts setup
Try it with:  python run.py call-test
"""
from __future__ import annotations

import re
import secrets
import threading
import urllib.parse

from .config import DATA, cfg
from .store import load_json, save_json

CONTACT_FILE = DATA / "phone.json"

CALLMEBOT_URL = "api.callmebot.com/start.php"
AUTH_BOT = "@CallMeBot_txtbot"
TEXT_LIMIT = 256            # CallMeBot's documented maximum

TEST_MESSAGE = "Athena here. This is a test call. Alerts are working."

# Which scheme the call API answered on. https is tried first; the documented
# address is plain http, so an https failure falls back rather than failing.
_scheme = "https"


# ---------- who to reach ----------

def contact() -> dict:
    """{"telegram": "@name", "ntfy_topic": "..."} - either may be missing."""
    try:
        return load_json(CONTACT_FILE, {})
    except Exception:
        return {}


def configured() -> bool:
    c = contact()
    return bool(c.get("telegram") or c.get("ntfy_topic"))


def normalise_user(user: str) -> str:
    """"@name" for a username, "+91..." for a phone number, or "".

    CallMeBot accepts either. A bare 10-digit number is taken as Indian,
    because that is whose phone this is; anything else needs its country code.
    """
    u = (user or "").strip()
    if not u:
        return ""
    digits = re.sub(r"[\s\-()]", "", u)
    if re.fullmatch(r"\+?\d{10,15}", digits):
        if not digits.startswith("+"):
            digits = "+91" + digits if len(digits) == 10 else "+" + digits
        return digits
    name = u.lstrip("@")
    # Telegram usernames: 5-32 characters, letters, digits and underscores.
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}", name):
        return "@" + name
    return ""


def new_topic() -> str:
    """An unguessable ntfy topic. Anyone who knows it can read the alerts."""
    return "jarvis-" + secrets.token_urlsafe(18).replace("_", "x").replace("-", "y")


def save_contact(telegram: str | None = None, topic: str | None = None) -> dict:
    c = contact()
    if telegram is not None:
        c["telegram"] = telegram
    if topic is not None:
        c["ntfy_topic"] = topic
    save_json(CONTACT_FILE, c)
    try:                         # best effort: keep it out of other accounts
        CONTACT_FILE.chmod(0o600)
    except OSError:
        pass
    return c


# ---------- the message ----------

def shorten(message: str, limit: int = TEXT_LIMIT) -> str:
    """Fit the call API's 256 characters without cutting a word in half."""
    text = re.sub(r"\s+", " ", message or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit - 3].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return cut + "..."


# ---------- the call (CallMeBot) ----------

def _call_advice(status: int, body: str) -> tuple[bool, str]:
    """Read CallMeBot's reply as (ok, plain words).

    The reply is an HTML page whose wording is not documented, so this looks
    for what a failure would have to say rather than for one exact string.
    """
    # The page opens with a Google Analytics <script>; left in, it filled the
    # whole 160-character error and pushed the actual reason off the end.
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", body or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    low = text.lower()
    if ("not authori" in low or "/start" in low or "authorize" in low
            or "authorise" in low or "permission" in low):
        return False, (f"Telegram has not authorised the call bot yet. On your "
                       f"phone, open Telegram, message {AUTH_BOT} and send "
                       f"/start, then try again.")
    if any(w in low for w in ("too many", "rate limit", "try again later",
                              "please wait", "flood", "within 65 seconds",
                              "two calls to the same user")):
        return False, ("the free call bot is busy or rate-limiting - the push "
                       "notification still went out. Try again in a few minutes.")
    # Seen 24 Sep 2026: authorisation passes, then the calling account (one of
    # several, e.g. @CallMeBot_API16) is refused by Telegram's spam flag until
    # you add it as a contact and message it. Which account varies, so name
    # the one this reply used.
    if "spammer" in low or "into your contacts" in low:
        who = re.search(r"add (@\w+) into your contacts", text, re.I) \
            or re.search(r"using bot:\s*(@\w+)", text, re.I)
        bot = who.group(1) if who else "the CallMeBot account that calls you"
        return False, (f"Telegram is blocking the call bot's account. In "
                       f"Telegram, search {bot}, add it to your contacts and "
                       f"send it any message (e.g. hi), then try again.")
    if "wrong format" in low:
        return False, ("CallMeBot could not read that Telegram user. Use your "
                       "@username or +91 number: python run.py alerts setup.")
    if any(w in low for w in ("user not found", "username not found",
                              "invalid user", "unknown user", "does not exist")):
        return False, ("CallMeBot does not know that Telegram user. Check the "
                       "@username (it must be set in Telegram > Settings) with "
                       "python run.py alerts setup.")
    if status >= 400 or re.search(r"\b(error|invalid|failed|wrong)\b", low):
        return False, f"the call bot refused: {text[:160] or f'HTTP {status}'}"
    return True, "Telegram call placed."


def call(message: str, user: str | None = None, timeout: float = 60) -> tuple[bool, str]:
    """Ring the user on Telegram and read `message` aloud. (ok, plain words)."""
    global _scheme
    import httpx

    user = user or contact().get("telegram", "")
    if not user:
        return False, "no Telegram user set up (python run.py alerts setup)."

    params = {"user": user, "text": shorten(message),
              "rpt": str(int(cfg.get("alerts.telegram.repeat", 2))),
              "cc": str(cfg.get("alerts.telegram.text_copy", "missed"))}
    voice = str(cfg.get("alerts.telegram.voice", "") or "").strip()
    if voice:
        params["lang"] = voice
    # CallMeBot reads `user` without URL-decoding it: "%2B91..." came back as
    # "User %2B91... has wrong format", while a literal "+91..." was looked up
    # (measured 24 Sep 2026). So "+" and "@" go through as they are. The text
    # uses "+" for spaces, the form CallMeBot's own examples use.
    query = "&".join(
        f"{k}={urllib.parse.quote(v, safe='+@') if k == 'user' else urllib.parse.quote_plus(v)}"
        for k, v in params.items())

    last_error = ""
    for scheme in dict.fromkeys([_scheme, "http"]):
        try:
            # The request can stay open while the bot rings, so the timeout
            # is generous; the alert thread and call-test can afford to wait.
            r = httpx.get(f"{scheme}://{CALLMEBOT_URL}?{query}", timeout=timeout,
                          follow_redirects=True)
        except httpx.TimeoutException:
            return False, ("the call bot did not answer in time - it may still "
                           "ring. The push notification went out regardless.")
        except httpx.HTTPError as e:
            last_error = str(e) or e.__class__.__name__
            continue
        _scheme = scheme
        # CallMeBot's replies are undocumented; keep the last one so a
        # misread can be checked against what it actually said.
        try:
            (DATA / "callmebot_last.html").write_text(r.text, encoding="utf-8")
        except OSError:
            pass
        return _call_advice(r.status_code, r.text)
    return False, f"could not reach CallMeBot ({last_error[:120]})."


# ---------- the push (ntfy) ----------

def push(message: str, topic: str | None = None, title: str = "Athena",
         priority: str = "urgent") -> tuple[bool, str]:
    """Send a push to the ntfy app. (ok, plain words)."""
    import httpx

    topic = topic or contact().get("ntfy_topic", "")
    if not topic:
        return False, "no ntfy topic set up (python run.py alerts setup)."
    server = str(cfg.get("alerts.ntfy.server", "https://ntfy.sh")).rstrip("/")
    try:
        r = httpx.post(f"{server}/{topic}", content=(message or "").encode("utf-8"),
                       headers={"Title": title, "Priority": priority,
                                "Tags": "rotating_light"},
                       timeout=20)
    except httpx.HTTPError as e:
        return False, f"could not reach ntfy ({str(e)[:120] or 'network error'})."
    if r.status_code == 429:
        return False, "ntfy is rate-limiting this PC; try again in a minute."
    if r.status_code >= 400:
        return False, f"ntfy refused the push (HTTP {r.status_code})."
    return True, "Push sent."


# ---------- both ----------

def deliver(message: str, wait: float | None = None) -> tuple[bool, str]:
    """Push, then ring. True if at least one of them reached the phone.

    wait: how long to wait for the call before answering anyway. The call bot
        can hold the request open for as long as it rings, which is too long
        to leave a spoken reply hanging; None waits for it.
    """
    c = contact()
    if not (c.get("telegram") or c.get("ntfy_topic")):
        return False, ("Phone alerts are not set up. Run: python run.py alerts setup")

    parts: list[str] = []
    ok_any = False
    if c.get("ntfy_topic") and cfg.get("alerts.ntfy.enabled", True):
        ok, words = push(message, c["ntfy_topic"])
        ok_any |= ok
        parts.append(words if ok else f"Push failed: {words}")

    if c.get("telegram") and cfg.get("alerts.telegram.enabled", True):
        result: list[tuple[bool, str]] = []
        worker = threading.Thread(
            target=lambda: result.append(call(message, c["telegram"])),
            daemon=True, name="jarvis-call")
        worker.start()
        worker.join(wait)
        if result:
            ok, words = result[0]
            ok_any |= ok
            parts.append(words if ok else f"Call failed: {words}")
        else:
            # Still going: count it as sent, the bot has the request.
            ok_any = True
            parts.append("Telegram call is on its way.")

    return ok_any, " ".join(parts) or "Nothing is switched on under alerts in config.yaml."
