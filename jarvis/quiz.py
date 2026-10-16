"""Quiz me: questions from your own course files, marked as you answer.

"Athena, quiz me on convolution" -> the best passages on it from your notes,
LMS slides and textbooks (the document index), five exam-style questions
written from them by the study model, asked one at a time. Each answer -
spoken or typed - is marked against the material, with the right answer and
the file it came from. Questions you miss go into Obsidian as flashcards
(Flashcards/<topic>.md, the Spaced Repetition plugin's "question::answer"
format). Built for the mid-sems, 9 Oct 2026.

While a quiz runs, whatever you say is taken as your answer, except "skip",
"repeat" and "stop the quiz". Each question ends with "?", so voice listens
for the answer without the wake word (Agent.expects_reply).
"""
from __future__ import annotations

import json
import re
import time

from .registry import tool

IDLE_MINUTES = 15
_state: dict = {}

# ---------- flashcards: what you missed comes back until you know it ----------
# Missed questions were saved to Obsidian (16 Oct 2026) and never asked again.
# Now they also go in a local deck, reviewed on a Leitner schedule: right moves
# a card further out (1, 3, 7, 14, 30 days), wrong brings it back tomorrow.
from .config import DATA                                    # noqa: E402
from .store import load_json, save_json                     # noqa: E402

DECK = DATA / "flashcards.json"
GAPS = [1, 3, 7, 14, 30]


def _today() -> str:
    import datetime as dt
    return dt.date.today().isoformat()


def _add_cards(topic: str, missed: list[dict]) -> None:
    import datetime as dt
    deck = load_json(DECK, [])
    have = {c["q"].strip().lower() for c in deck}
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    for q in missed:
        if q["q"].strip().lower() not in have:
            deck.append({"q": q["q"].strip(), "a": q["a"].strip(), "source": q.get("source", ""),
                         "topic": topic, "box": 0, "due": tomorrow})
    save_json(DECK, deck)


def _mark_card(q: str, right: bool) -> None:
    import datetime as dt
    deck = load_json(DECK, [])
    for c in deck:
        if c["q"] == q:
            c["box"] = min(c["box"] + 1, len(GAPS) - 1) if right else 0
            c["due"] = (dt.date.today() + dt.timedelta(days=GAPS[c["box"]] if right else 1)).isoformat()
    save_json(DECK, deck)


def due_cards(topic: str = "") -> list[dict]:
    words = set(re.findall(r"[a-z]{3,}", topic.lower()))
    return [c for c in load_json(DECK, []) if c["due"] <= _today()
            and (not words or words & set(re.findall(r"[a-z]{3,}", (c["topic"] + " " + c["q"]).lower())))]


def brief_line() -> str:
    """For the morning brief: how many cards are due."""
    n = len(due_cards())
    return f"{n} flashcard{'s' if n != 1 else ''} to review - say 'review my flashcards'." if n else ""

STOP = re.compile(r"^\W*(?:athena\W+)?(?:stop|end|quit|finish|cancel)\s+(?:the\s+|this\s+)?(?:quiz|test)\W*$|"
                  r"^\W*(?:athena\W+)?(?:that'?s\s+enough|i'?m\s+done)\W*$", re.I)
SKIP = re.compile(r"^\W*(?:athena\W+)?(?:skip(?:\s+it|\s+this(?:\s+one)?)?|next(?:\s+question)?|"
                  r"i\s+don'?t\s+know|no\s+idea|pass)\W*$", re.I)
REPEAT = re.compile(r"^\W*(?:athena\W+)?(?:repeat(?:\s+(?:it|that|the\s+question))?|say\s+(?:it|that)\s+again|"
                    r"what\s+was\s+the\s+question)\W*$", re.I)


def _model(prompt: str) -> str:
    from .inbox import _ask              # the study model first, the local one if it is out of reach
    return _ask(prompt)


def _json(text: str):
    m = re.search(r"(\[.*\]|\{.*\})", text, re.S)
    return json.loads(m.group(1)) if m else None


def active() -> bool:
    if _state and time.time() - _state["t"] > IDLE_MINUTES * 60:
        _state.clear()
    return bool(_state)


def _ask_current(lead: str = "") -> str:
    i = _state["i"]
    return f"{lead}Question {i + 1} of {len(_state['qs'])}: {_state['qs'][i]['q'].rstrip('?. ')}?"


def _finish() -> str:
    topic, qs, right, missed = _state["topic"], _state["qs"], _state["right"], _state["missed"]
    asked = _state["i"]
    _state_review = _state.get("review", False)
    _state.clear()
    if not asked:
        return "Quiz stopped."
    saved = ""
    if _state_review:
        pass
    elif missed:
        try:
            _add_cards(topic, missed)
        except Exception:
            pass
    if missed and not _state_review:
        from . import vault
        base = vault.root()
        if base:
            d = base / "Flashcards"
            d.mkdir(parents=True, exist_ok=True)
            path = d / f"{vault._name(topic)}.md"
            new = not path.exists()
            with open(path, "a", encoding="utf-8") as f:
                if new:
                    f.write(f"#flashcards/{re.sub(r'[^a-z0-9]+', '-', topic.lower()).strip('-')}\n\n")
                for q in missed:
                    f.write(f"{q['q'].strip()}::{q['a'].strip()} *({q.get('source', '')})*\n")
            saved = f" I saved the {len(missed)} you missed as flashcards in Obsidian."
    score = f"You got {right:g} out of {asked} on {topic}."
    if _state_review:
        left = len(due_cards())
        return score + (f" {left} still due today." if left else " That's all your flashcards for today.")
    try:
        from . import goals
        saved += goals.quiz_finished(topic, right, asked)
    except Exception:
        pass
    return score + saved + (" Nice work." if right >= asked * 0.8 else "")


def handle(text: str) -> str | None:
    """Your answer to the current question, or None when no quiz is running."""
    if not active():
        return None
    _state["t"] = time.time()
    if STOP.match(text):
        return _finish()
    if REPEAT.match(text):
        return _ask_current()
    q = _state["qs"][_state["i"]]
    if SKIP.match(text):
        _state["missed"].append(q)
        if _state.get("review"):
            _mark_card(q["q"], False)
        verdict = f"The answer: {q['a']} "
    else:
        reply = _model(
            "You are marking a student's spoken answer in a quiz. Be fair: accept the right idea in other "
            "words, ignore speech-to-text slips. Reply with JSON only: "
            '{"mark": "right" | "partly" | "wrong", "say": "one or two short sentences: what was right or '
            'missing, and the correct answer if they missed it"}\n\n'
            f"Question: {q['q']}\nModel answer: {q['a']}\nStudent's answer: {text}")
        try:
            got = _json(reply) or {}
        except ValueError:
            got = {}
        mark = str(got.get("mark", "wrong")).lower()
        _state["right"] += 1 if mark == "right" else 0.5 if mark == "partly" else 0
        if mark != "right":
            _state["missed"].append(q)
        if _state.get("review"):
            _mark_card(q["q"], mark == "right")
        verdict = {"right": "Right. ", "partly": "Partly. "}.get(mark, "Not quite. ") + \
            str(got.get("say", "") or f"The answer: {q['a']}").strip() + " "
    if q.get("source"):
        verdict += f"(From {q['source']}.) "
    _state["i"] += 1
    if _state["i"] >= len(_state["qs"]):
        return verdict + _finish()
    return _ask_current(verdict)


@tool(tags=["study"], timeout=120)
def quiz_me(topic: str, questions: int = 5) -> str:
    """Quiz the user on a topic with questions made from their own notes, LMS
    slides and textbooks, marking each answer. Use for "quiz me on
    convolution", "test me on Fourier series", "ask me questions about BJTs".

    topic: what to be quizzed on
    questions: how many questions (3 to 10)
    """
    from . import db
    from .tools.knowledge import _embed_query
    topic = topic.strip(" .?") or "my course"
    n = max(3, min(10, int(questions or 5)))
    # In a project (its chat, or a goal's task): its own files, OCR'd slides
    # and scanned practice sets included. Else the document index.
    from . import chats
    material = ""
    if chats.ACTIVE and chats.project_files(chats.ACTIVE["id"]):
        material = chats.relevant(chats.ACTIVE["id"], topic, budget=9000)
    if not material:
        try:
            hits = db.search("doc", topic, k=6, embed=_embed_query)
        except Exception:
            hits = []
        if not hits:
            return (f"I couldn't find anything on {topic} in your notes or course files. "
                    "Say 'get my LMS files', or add notes to your Obsidian vault.")
        material = "\n\n".join(f"[{h['title']}]\n{h['text'][:1500]}" for h in hits)
    reply = _model(
        f"Write {n} exam-style questions on {topic} for a university engineering student, answerable from "
        "the material below and nothing else. Mix: definitions, why/how, and a small calculation if the "
        "material has one. Each must be answerable aloud in one or two sentences. Reply with a JSON list "
        'only: [{"q": "question", "a": "model answer, 1-2 sentences", "source": "the [file] it came from"}]'
        f"\n\nMATERIAL:\n{material}")
    try:
        qs = [q for q in (_json(reply) or []) if isinstance(q, dict) and q.get("q") and q.get("a")][:n]
    except ValueError:
        qs = []
    if not qs:                       # a busy model sometimes returns half an answer: ask once more
        try:
            qs = [q for q in (_json(_model(
                f"Write {n} exam-style questions on {topic} from this material. JSON list only: "
                f'[{{"q": "...", "a": "1-2 sentences", "source": "file"}}]\n\nMATERIAL:\n{material}')) or [])
                  if isinstance(q, dict) and q.get("q") and q.get("a")][:n]
        except ValueError:
            qs = []
    for q in qs:
        q["source"] = str(q.get("source", "")).strip("[] ")
    if not qs:
        return "I couldn't make questions out of that material - try a more specific topic."
    _state.clear()
    _state.update(topic=topic, qs=qs, i=0, right=0, missed=[], t=time.time())
    files = sorted({q["source"] for q in qs if q["source"]})
    intro = f"Quiz on {topic}: {len(qs)} questions" + (f" from {', '.join(files[:3])}" if files else "") + \
        ". Say skip, repeat, or stop the quiz. "
    return _ask_current(intro)


@tool(tags=["study"], timeout=30)
def review_flashcards(topic: str = "") -> str:
    """Review the flashcards that are due: the quiz questions the user missed
    before, asked again on a spaced schedule. Use for "review my flashcards",
    "revise what I got wrong", "flashcards on Fourier".

    topic: only cards about this, or empty for all that are due
    """
    cards = due_cards(topic)[:10]
    if not cards:
        upcoming = sorted(c["due"] for c in load_json(DECK, []))
        return ("No flashcards due" + (f" on {topic}" if topic else "") + "." +
                (f" The next ones are due {upcoming[0]}." if upcoming else " Missed quiz questions become flashcards."))
    _state.clear()
    _state.update(topic=topic or "your flashcards", qs=[dict(c) for c in cards], i=0, right=0, missed=[],
                  t=time.time(), review=True)
    return _ask_current(f"{len(cards)} flashcard{'s' if len(cards) > 1 else ''} due. Say skip, repeat, or stop the quiz. ")


if __name__ == "__main__":
    assert STOP.match("stop the quiz") and STOP.match("Athena, end quiz") and not STOP.match("stop music")
    assert SKIP.match("skip") and SKIP.match("I don't know") and not SKIP.match("convolution is a sum")
    assert REPEAT.match("repeat the question")
    from . import vault
    vault.root = lambda: None                  # the self-check must not write flashcards
    _state.update(topic="t", qs=[{"q": "What is 2+2", "a": "4", "source": "a.pdf"}], i=0, right=0, missed=[], t=time.time())
    import tempfile
    from pathlib import Path as _P
    DECK = _P(tempfile.gettempdir()) / "flashcards_selfcheck.json"
    DECK.unlink(missing_ok=True)
    print(handle("skip"))
    assert not active()
    assert load_json(DECK, [])[0]["q"] == "What is 2+2", "a missed question becomes a card"
    deck = load_json(DECK, []); deck[0]["due"] = _today(); save_json(DECK, deck)
    review_flashcards()
    assert _state.get("review") and len(_state["qs"]) == 1
    _mark_card("What is 2+2", True)
    assert load_json(DECK, [])[0]["box"] == 1 and load_json(DECK, [])[0]["due"] > _today()
    _state.clear()
    DECK.unlink(missing_ok=True)
    print("ok")
