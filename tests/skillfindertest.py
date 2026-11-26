"""The skill finder (jarvis/skillfinder.py) on made-up chats: what becomes an idea,
what never does, saving one, the weekly timing and push. Nothing real is read,
saved or sent.

    python tests/skillfindertest.py
"""
import datetime as dt
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jarvis.tools  # noqa: E402,F401
from jarvis import phone, skillfinder as sf  # noqa: E402
from jarvis.routes import route  # noqa: E402
from jarvis.tools import routines  # noqa: E402

bad = []
tmp = Path(tempfile.mkdtemp())
sf.STATE = tmp / "skill_ideas.json"
routines.FILE = tmp / "routines.yaml"
routines.FILE.write_text("# mine\n", encoding="utf-8")
NOW = time.time()


def asked(text, *calls, ok=True, routed=None, private=False):
    """A request: the user's words, the model's tool calls (or a routed tool), their results."""
    msgs = [{"role": "user", "content": text}]
    if calls:
        msgs.append({"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": n, "arguments": a}} for n, a in calls]})
    for n in [n for n, _ in calls] + ([routed] if routed else []):
        msgs.append({"role": "tool", "name": n, "content": "done" if ok else "ERROR: no"})
    msgs.append({"role": "assistant", "content": "ok"})
    return [dict(m, private=True) for m in msgs] if private else msgs


def chat(days_ago, *requests):
    return {"created": NOW - days_ago * 86400, "messages": [m for r in requests for m in r]}


VOL = ("set_volume", {"level": 20})
LOFI = ("music", {"action": "song", "song": "lofi"})
WA = ("whatsapp_message", {"to": "papa", "text": "hi"})
filler = [asked(f"tell me a fact about {w}") for w in ("owls", "rome", "tea")]

ideas = sf.find([
    chat(3, asked("set the volume to 20", VOL), asked("message papa hi", WA), asked("play lofi", LOFI),
         asked("open opera", ("open_app", {"name": "Opera"}), ok=False),
         asked("battery?", routed="battery"), *filler, asked("weather", ("get_weather", {"city": "Dehradun"}))),
    chat(1, asked("volume 20", VOL), asked("text papa", WA), asked("lofi please", LOFI),
         asked("open opera", ("open_app", {"name": "opera"}), ok=False),
         asked("battery?", routed="battery"), *filler, asked("weather", ("get_weather", {"city": "Dehradun"}))),
    chat(1, asked("secret", ("get_weather", {"city": "Goa"}), private=True),
         asked("secret 2", ("get_datetime", {}), private=True)),
    chat(2, asked("what's the time", ("get_datetime", {})), asked("news", ("web_search", {"query": "news"}))),
    chat(40, asked("what's the time", ("get_datetime", {})), asked("news", ("web_search", {"query": "news"}))),
], NOW)
steps = [i["steps"] for i in ideas]
if [{"set_volume": {"level": 20}}, {"music": {"action": "song", "song": "lofi"}}] not in steps:
    bad.append(f"volume + lofi on two days wasn't offered: {steps}")
if any("whatsapp_message" in s for i in ideas for s in i["steps"]):
    bad.append("a step that messages someone was offered")
if any("open_app" in s for i in ideas for s in i["steps"]):
    bad.append("a step that failed was offered")
if any({"battery", "get_weather"} <= {k for s in i["steps"] for k in s} for i in ideas):
    bad.append("two things six requests apart counted as done together")
if any("get_datetime" in s and "web_search" in str(i["steps"]) for i in ideas for s in i["steps"]):
    bad.append("a pair seen on one day in the last month (and once 40 days ago) was offered")
if any(s.get("get_weather") == {"city": "Goa"} for i in ideas for s in i["steps"]):
    bad.append("a private turn was read")
if ideas and (ideas[0]["said"][:2] != ["volume 20", "lofi please"] or len(ideas[0]["days"]) != 2):
    bad.append(f"the idea doesn't show the latest wording and both days: {ideas[0]}")

# An idea that's already a routine isn't offered again.
routines.FILE.write_text("music time:\n  steps:\n    - set_volume: 20\n    - music: {action: song, song: lofi}\n",
                         encoding="utf-8")
again = sf.find([chat(3, asked("v", VOL), asked("l", LOFI)), chat(1, asked("v", VOL), asked("l", LOFI))], NOW)
if again:
    bad.append(f"an idea that's already a routine was offered: {again}")
routines.FILE.write_text("# mine\n", encoding="utf-8")

# A routed request (no model call): the router supplies the arguments.
hit = route("what time is it")
pair = sf.find([chat(3, asked("what time is it", routed=hit[0]), asked("v", VOL)),
                chat(1, asked("what time is it", routed=hit[0]), asked("v", VOL))], NOW) if hit else []
if not pair or {hit[0]: hit[1]} not in pair[0]["steps"]:
    bad.append(f"a routed request's action was lost: {hit} -> {pair}")

# Asking, then saving one.
sf._chats = lambda: [chat(3, asked("v", VOL), asked("l", LOFI)), chat(1, asked("v", VOL), asked("l", LOFI))]
said = sf.skill_ideas()
if not said.startswith('1. On 2 days') or "save idea 1" not in said:
    bad.append(f"skill_ideas reply: {said[:200]}")
if not sf.save_skill_idea(1, "night music").startswith("Saved"):
    bad.append("save_skill_idea didn't save")
if routines.load().get("night music", {}).get("steps") != [{"set_volume": {"level": 20}},
                                                           {"music": {"action": "song", "song": "lofi"}}]:
    bad.append(f"the saved routine's steps are wrong: {routines.load()}")
if not sf.save_skill_idea(1, "night music").startswith("ERROR"):
    bad.append("the same name was saved twice")
if not sf.save_skill_idea(7).startswith("ERROR"):
    bad.append("an idea number that doesn't exist was accepted")

# The weekly push: new ideas once, then nothing new.
routines.FILE.write_text("# mine\n", encoding="utf-8")
sent = []
phone.push = lambda text, **k: (sent.append(text), (True, "Sent to Telegram."))[1]
first, second = sf.weekly_push(), sf.weekly_push()
if first != "Sent to Telegram." or len(sent) != 1 or second != "no new skill ideas this week":
    bad.append(f"weekly push: {first!r}, then {second!r}, {len(sent)} sent")

# Saturday 11:00 until the week ends, once.
sat = dt.datetime(2026, 11, 14, 11, 0)
for now, sent_week, want in ((sat - dt.timedelta(minutes=1), "", False), (sat, "", True),
                             (sat + dt.timedelta(days=1, hours=12), "", True),
                             (sat, "2026-W46", False), (dt.datetime(2026, 11, 16, 12), "2026-W46", False)):
    if sf.due(now, "sat", "11:00", sent_week) != want:
        bad.append(f"due({now:%a %H:%M}, sent={sent_week!r}) should be {want}")

# What reaches it from speech, and what doesn't.
for text, want in (("Athena, any skill ideas?", ("skill_ideas", {})), ("what skills could we make", ("skill_ideas", {})),
                   ("save idea 2 as uni stuff.", ("save_skill_idea", {"number": 2, "name": "uni stuff"})),
                   ("Yes, save idea one", ("save_skill_idea", {"number": 1}))):
    if route(text) != want:
        bad.append(f"route({text!r}) = {route(text)}, not {want}")
if (route("search the web for skill ideas for my CV") or ("",))[0] in ("skill_ideas", "save_skill_idea"):
    bad.append("a web search mentioning skill ideas went to the finder")

for x in bad:
    print("FAIL", x)
print(f"skillfinder: {len(bad)} failure(s)")
sys.exit(1 if bad else 0)
