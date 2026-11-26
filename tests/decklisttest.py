"""Deck checklists for Athena (deck_link.deck_list / deck_tick / deck_add_item) on a
made-up board in a temp folder - the real board is never touched.

    python tests/decklisttest.py
"""
import datetime as dt
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis import deck_link, routes  # noqa: E402
import jarvis.tools  # noqa: E402,F401

bad = []
today = dt.date.today()
day = deck_link._day
root = Path(tempfile.mkdtemp())
(root / "data").mkdir()
board = root / "data" / "board.json"
board.write_text(json.dumps({"workspaces": [{"id": "w1", "name": "Shorts"}], "nodes": [
    {"id": "c0", "ws": "w1", "type": "checklist", "title": f"{day(today - dt.timedelta(days=1))} – Batch",
     "items": [{"id": "a", "text": "Write script 1", "done": False}, {"id": "b", "text": "Write script 2", "done": True}]},
    {"id": "c1", "ws": "w1", "type": "checklist", "title": day(today),
     "items": [{"id": "x", "text": "Record episode 2", "done": False}, {"id": "y", "text": "Edit episode 1", "done": False}]},
    {"id": "n1", "ws": "w1", "type": "console", "kind": "claude", "title": "Builder"}]}), encoding="utf-8")
deck_link.DECK = root
deck_link.running = lambda timeout=2: False

if deck_link._title_date("Sat 24 Oct – Setup") is None or deck_link._title_date("Plan") is not None:
    bad.append("dates in titles aren't read")
out = deck_link.deck_list()
if not out.startswith(f"{day(today)}: 0 of 2 done") or "1 items are still unticked from earlier days" not in out:
    bad.append(f"today's list: {out!r}")
if "Batch" not in deck_link.deck_list("yesterday"):
    bad.append("yesterday's list not found")
out = deck_link.deck_tick("record episode")
saved = json.loads(board.read_text(encoding="utf-8"))["nodes"][1]["items"]
if not saved[0]["done"] or "Record episode 2" not in out or "1 left" not in out:
    bad.append(f"tick by words: {out!r} {saved}")
deck_link.deck_tick("1", done=False)
if json.loads(board.read_text(encoding="utf-8"))["nodes"][1]["items"][0]["done"]:
    bad.append("untick by number didn't stick")
if not deck_link.deck_tick("buy a spaceship").startswith("ERROR"):
    bad.append("an item that isn't there got ticked")
deck_link.deck_add_item("Post on Instagram")
if json.loads(board.read_text(encoding="utf-8"))["nodes"][1]["items"][-1]["text"] != "Post on Instagram":
    bad.append("add item didn't stick")
line = deck_link.today_line()
if not line.startswith(f"Today's Deck list ({day(today)}): 0 of 3 done - next: Record episode 2"):
    bad.append(f"brief line: {line!r}")
if not deck_link.on_today_list("record episode 2") or deck_link.on_today_list("my homework") or deck_link.on_today_list("the"):
    bad.append("the tick-off shortcut matches too loosely or not at all")

from jarvis import alerts  # noqa: E402
deck_link.deck_add_item("12:30 YT clip 11 → submit link")
timed = deck_link.timed_today()
if len(timed) != 1 or timed[0]["at"].strftime("%H:%M") != "12:30" or timed[0]["text"] != "YT clip 11 → submit link":
    bad.append(f"timed items: {timed}")
at = lambda h, m: dt.datetime.combine(today, dt.time(h, m))  # noqa: E731
got = alerts.deck_alerts(timed, at(12, 26), 5)
if got != [(timed[0]["key"], "In 4 minutes (12:30): YT clip 11 → submit link")]:
    bad.append(f"deck alert at 12:26: {got}")
if alerts.deck_alerts(timed, at(12, 20), 5) or alerts.deck_alerts(timed, at(12, 30), 5):
    bad.append("a deck alert came too early or too late")
deck_link.deck_tick("yt clip 11")
if deck_link.timed_today():
    bad.append("a ticked item still alerts")

for said, want in (("what's on my checklist today", ("deck_list", {"which": "today"})),
                   ("show me tomorrow's checklist", ("deck_list", {"which": "tomorrow"})),
                   ("what is the deck doing right now?", ("deck_status", {})),
                   ("add edit episode 3 to today's checklist", ("deck_add_item", {"text": "edit episode 3", "which": "today"})),
                   ("tick off record episode 2", ("deck_tick", {"item": "record episode 2"}))):
    got = routes.route(said)
    if got != want:
        bad.append(f"{said!r} -> {got}, not {want}")
if (routes.route("tick off my homework") or ("",))[0] == "deck_tick":
    bad.append("'tick off my homework' went to the Deck")

calls = []
deck_link.running = lambda timeout=2: True
deck_link.api = lambda m, p, body=None, timeout=10: calls.append((m, p, body)) or json.loads(board.read_text(encoding="utf-8"))
deck_link.deck_tick("edit episode")
patch = [c for c in calls if c[0] == "PATCH"]
if not patch or patch[0][1] != "/api/nodes/c1" or not patch[0][2].get("refresh"):
    bad.append(f"with the Deck open, a tick doesn't go through it (and redraw): {patch}")

for x in bad:
    print("FAIL", x)
print(f"decklist: {len(bad)} failure(s)")
sys.exit(1 if bad else 0)
