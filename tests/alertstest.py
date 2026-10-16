"""Phone alerts: when Athena rings, and - mostly - when it must not.

    python tests/alertstest.py

A call costs the user's attention and can wake them, so the rails matter more
than the call: the same event never rings twice, nothing non-urgent rings at
night, alerts arriving together ring once, and there is a daily cap. Every
HTTP call is replaced before anything runs - this file must never ring a
phone or send a push, whatever is saved in data/.
"""
from __future__ import annotations

import datetime as dt
import sys
import tempfile
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx                                                   # noqa: E402

# ---- no network, before any Athena module can reach for it ----
SENT: list[tuple[str, str, dict]] = []
REPLY = {"status": 200, "text": "<html>Call queued for @someone</html>"}


class _Fake:
    def __init__(self, status: int, text: str):
        self.status_code, self.text = status, text


def _fake_get(url, **kw):
    SENT.append(("GET", url, kw))
    return _Fake(REPLY["status"], REPLY["text"])


def _fake_post(url, **kw):
    SENT.append(("POST", url, kw))
    return _Fake(200, "{}")


httpx.get = _fake_get
httpx.post = _fake_post

from jarvis import alerts, phone, webpush                      # noqa: E402
from jarvis.agent import _direct_phone_me                      # noqa: E402

# The Athena app's own notifications (webpush.py, 16 Oct 2026) don't go through
# httpx: with a phone subscribed, this test pushed its fake alerts to the real
# phone. Off here, so phone.push() takes the faked ntfy path as before.
webpush.subscribers = lambda: 0
webpush.send = lambda *a, **k: 0
from jarvis import upes                                         # noqa: E402
upes.covers = lambda day: False          # the test's own weekly table, not the real semester timetable
from jarvis.tools import brief                                 # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="jarvis-alertstest-"))
phone.CONTACT_FILE = TMP / "phone.json"
alerts.STATE_FILE = TMP / "alerts_state.json"
alerts.INBOX = TMP / "inbox"
brief.TIMETABLE_FILE = TMP / "timetable.json"

DAY = dt.datetime(2026, 9, 24)            # a Thursday


def at(hhmm: str, seconds: int = 0) -> dt.datetime:
    h, m = map(int, hhmm.split(":"))
    return DAY.replace(hour=h, minute=m) + dt.timedelta(seconds=seconds)


def engine(name: str, **settings) -> tuple[alerts.Engine, list[str], list[str]]:
    calls: list[str] = []
    log: list[str] = []
    base = {"enabled": True, "quiet_hours": ["23:00", "07:00"],
            "urgent": ["reminders"], "daily_cap": 10, "min_gap_minutes": 3}
    base.update(settings)
    e = alerts.Engine(lambda m: (calls.append(m), (True, "ok"))[1], log.append,
                      state_file=TMP / f"{name}.json", settings=base)
    return e, calls, log


# ---------- cases: each returns (ok, what it saw) ----------

def shorten_fits():
    long = "Probability and Statistics in ten minutes, room 1003. " * 10
    got = phone.shorten(long)
    return len(got) <= 256 and got.endswith("...") and " roo..." not in got, f"{len(got)} chars"


def shorten_leaves_short():
    return phone.shorten("  Email   from  Prof Example. ") == "Email from Prof Example.", phone.shorten("  Email   from  Prof Example. ")


def call_is_encoded():
    SENT.clear()
    ok, _ = phone.call("Maths & Stats in 10 min, room #3?", user="@someone")
    url = SENT[-1][1]
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    good = (ok and q["text"] == ["Maths & Stats in 10 min, room #3?"]
            and q["user"] == ["@someone"] and "api.callmebot.com/start.php" in url)
    return good, url[:90]


def call_not_authorised():
    REPLY.update(status=200, text="<p>User @someone not authorized. Send /start first</p>")
    try:
        ok, words = phone.call("hi", user="@someone")
    finally:
        REPLY.update(status=200, text="<html>Call queued for @someone</html>")
    return (not ok) and "/start" in words and "CallMeBot_txtbot" in words, words[:70]


def push_is_urgent():
    SENT.clear()
    ok, _ = phone.push("Battery low.", topic="jarvis-test-topic")
    method, url, kw = SENT[-1]
    h = kw.get("headers", {})
    return (ok and method == "POST" and url.endswith("/jarvis-test-topic")
            and h.get("Priority") == "urgent" and kw.get("content") == b"Battery low."), url


def deliver_needs_setup():
    ok, words = phone.deliver("hi")
    return (not ok) and "alerts setup" in words, words


class _Cfg:
    """config.yaml with a few keys pinned, so a case tests the code rather
    than whatever the live config happens to say today."""

    def __init__(self, real, **pinned):
        self.real, self.pinned = real, pinned

    def get(self, dotted, default=None):
        return self.pinned.get(dotted, self.real.get(dotted, default))


def _deliver_with(telegram_on: bool) -> tuple[bool, list[str], str]:
    phone.save_contact(telegram="@someone", topic="jarvis-test-topic")
    real = phone.cfg
    phone.cfg = _Cfg(real, **{"alerts.telegram.enabled": telegram_on,
                              "alerts.ntfy.enabled": True})
    SENT.clear()
    try:
        ok, words = phone.deliver("Test.")
    finally:
        phone.cfg = real
    return ok, sorted(m for m, _u, _k in SENT), words


def deliver_both():
    ok, kinds, words = _deliver_with(telegram_on=True)
    return ok and kinds == ["GET", "POST"], f"{kinds} {words}"


def deliver_push_only():
    # The live setting since 24 Sep 2026: Telegram's spam flag on CallMeBot's
    # calling account made the call unusable without paying, so ntfy alone.
    ok, kinds, words = _deliver_with(telegram_on=False)
    return ok and kinds == ["POST"], f"{kinds} {words}"


def normalise_numbers():
    got = [phone.normalise_user(x) for x in
           ("9876543210", "+91 98765 43210", "@some_user", "some_user", "ab", "call me")]
    want = ["+919876543210", "+919876543210", "@some_user", "@some_user", "", ""]
    return got == want, str(got)


def dedupe():
    e, calls, _ = engine("dedupe")
    first = e.offer("email", "mail:abc", "Email from Rao.", at("10:00"))
    second = e.offer("email", "mail:abc", "Email from Rao.", at("10:01"))
    e.flush(at("10:05"))
    # and across a restart: a new engine on the same file still knows it
    again, calls2, _ = engine("dedupe")
    third = again.offer("email", "mail:abc", "Email from Rao.", at("11:00"))
    return first and not second and not third and len(calls) == 1, f"{first} {second} {third} calls={len(calls)}"


def quiet_hours_window():
    q = alerts.in_quiet_hours
    got = [q(at("23:30")), q(at("02:00")), q(at("06:59")), q(at("07:00")),
           q(at("12:00")), q(at("22:59")), q(at("12:00"), "12:00", "13:00"),
           q(at("12:00"), "00:00", "00:00")]
    return got == [True, True, True, False, False, False, True, False], str(got)


def quiet_skips_non_urgent():
    e, calls, log = engine("quiet")
    e.offer("email", "mail:q1", "Email from Rao.", at("23:30"))
    e.offer("reminders", "rem:q1", "Reminder: wake up.", at("23:30"))
    e.flush(at("23:30", 30))
    return (calls == ["Reminder: wake up."]
            and any("quiet hours" in line for line in log)), f"{calls} {log}"


def gap_and_merge():
    e, calls, _ = engine("gap")
    e.offer("classes", "c1", "Maths in 10 minutes.", at("10:00"))
    e.offer("email", "m1", "Email from Rao.", at("10:00", 5))
    early = e.flush(at("10:00", 8))                  # still settling
    e.flush(at("10:00", 30))                         # one call, both inside
    e.offer("email", "m2", "Email from Iyer.", at("10:01"))
    e.offer("battery", "b1", "Battery low.", at("10:01", 10))
    blocked = e.flush(at("10:02"))                   # inside the 3 minute gap
    e.flush(at("10:04"))                             # gap over: merged
    ok = (early is None and blocked is None and len(calls) == 2
          and "Maths" in calls[0] and "Rao" in calls[0] and calls[0].startswith("Athena here, 2 alerts")
          and "Iyer" in calls[1] and "Battery" in calls[1])
    return ok, " | ".join(c[:50] for c in calls)


def daily_cap():
    e, calls, log = engine("cap", daily_cap=2)
    for i in range(4):
        e.offer("email", f"cap{i}", f"Email {i}.", at("09:00") + dt.timedelta(minutes=10 * i))
        e.flush(at("09:01") + dt.timedelta(minutes=10 * i))
    return len(calls) == 2 and sum("daily cap" in line for line in log) == 2, f"calls={len(calls)}"


def master_switch():
    e, calls, _ = engine("off", enabled=False)
    queued = e.offer("email", "x", "Email.", at("10:00"))
    e.flush(at("10:05"))
    return not queued and not calls, f"queued={queued} calls={len(calls)}"


def class_timing():
    brief.save_json(brief.TIMETABLE_FILE, [
        {"day": "thu", "start": "11:00", "end": "11:55", "what": "Probability", "where": "1003"},
        {"day": "thu", "start": "14:00", "end": "14:55", "what": "Marketing", "where": ""},
        {"day": "fri", "start": "11:00", "end": "11:55", "what": "Friday class", "where": ""},
    ])
    events = brief._timetable_events(DAY.date())
    got = {
        "10:49": alerts.class_alerts(events, at("10:49"), 10),
        "10:50": alerts.class_alerts(events, at("10:50"), 10),
        "10:56": alerts.class_alerts(events, at("10:56"), 10),
        "11:00": alerts.class_alerts(events, at("11:00"), 10),
        "13:52": alerts.class_alerts(events, at("13:52"), 10),
    }
    msgs = {k: [m for _key, m in v] for k, v in got.items()}
    ok = (msgs["10:49"] == [] and msgs["10:50"] == ["Probability in 10 minutes, room 1003."]
          and msgs["10:56"] == ["Probability in 4 minutes, room 1003."]
          and msgs["11:00"] == [] and msgs["13:52"] == ["Marketing in 8 minutes."]
          and got["10:50"][0][0] == got["10:56"][0][0])      # same key: rings once
    return ok, str(msgs)


def class_rings_once():
    e, calls, _ = engine("classonce")
    events = brief._timetable_events(DAY.date())
    for minute in ("10:50", "10:51", "10:52", "10:55"):
        for key, message in alerts.class_alerts(events, at(minute), 10):
            e.offer("classes", key, message, at(minute))
        e.flush(at(minute, 30))
    return len(calls) == 1, f"calls={len(calls)}"


def inbox_round_trip():
    alerts.submit("reminders", "rem:inbox", "Reminder: call papa.")
    e, calls, _ = engine("inbox")
    watch = alerts._Watch(e)
    now = dt.datetime.now()
    watch.inbox(now)
    left = list(alerts.INBOX.glob("*.json"))
    return (len(e.pending) == 1 and e.pending[0]["message"] == "Reminder: call papa."
            and not left), f"pending={len(e.pending)} left={len(left)}"


def sender_matching():
    m = alerts._sender_matches
    got = [m('"Prof Example" <prof.example@upes.ac.in>', ["upes.ac.in"]),
           m("x@stu.upes.ac.in", ["upes.ac.in"]),
           m("Spam <x@notupes.ac.in>", ["upes.ac.in"]),
           m("Boss <boss@corp.com>", ["boss@corp.com"]),
           m("Other <other@corp.com>", ["boss@corp.com"])]
    return got == [True, True, False, True, False], str(got)


# "call me" is the user's own phone; everything else still goes to WhatsApp.
ROUTER = [
    ("call me", "call_me"),
    ("Athena, call me.", "call_me"),
    ("test call", "call_me"),
    ("give me a test call", "call_me"),
    ("ring my phone", "call_me"),
    ("call me now please", "call_me"),
    ("call mom", None),
    ("call papa on whatsapp", None),
    ("video call papa", None),
    ("call my mom", None),
    ("call me later", None),
    ("remind me to call papa at 9pm", None),
    ("what is a test call in software", None),
]


def main() -> int:
    bad = 0
    cases = [shorten_fits, shorten_leaves_short, call_is_encoded,
             call_not_authorised, push_is_urgent, deliver_needs_setup,
             deliver_both, deliver_push_only, normalise_numbers, dedupe, quiet_hours_window,
             quiet_skips_non_urgent, gap_and_merge, daily_cap, master_switch,
             class_timing, class_rings_once, inbox_round_trip, sender_matching]
    for case in cases:
        try:
            ok, seen = case()
        except Exception as e:
            ok, seen = False, f"raised {e!r}"
        bad += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {case.__name__:24} {'' if ok else seen}")

    for text, want in ROUTER:
        got = _direct_phone_me(text)
        name = got[0] if got else None
        ok = name == want
        bad += not ok
        print(f"{'ok  ' if ok else 'FAIL'} router {text:34} -> {name or 'not ours'}")

    # Nothing may have left for a real address other than the faked ones.
    stray = [u for _m, u, _k in SENT if "callmebot" not in u and "jarvis-test-topic" not in u]
    ok = not stray
    bad += not ok
    print(f"{'ok  ' if ok else 'FAIL'} no stray requests {'' if ok else stray}")

    total = len(cases) + len(ROUTER) + 1
    print(f"\n{total} cases, {bad} failure(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
