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
    _state.clear()
    if not asked:
        return "Quiz stopped."
    saved = ""
    if missed:
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


if __name__ == "__main__":
    assert STOP.match("stop the quiz") and STOP.match("Athena, end quiz") and not STOP.match("stop music")
    assert SKIP.match("skip") and SKIP.match("I don't know") and not SKIP.match("convolution is a sum")
    assert REPEAT.match("repeat the question")
    from . import vault
    vault.root = lambda: None                  # the self-check must not write flashcards
    _state.update(topic="t", qs=[{"q": "What is 2+2", "a": "4", "source": "a.pdf"}], i=0, right=0, missed=[], t=time.time())
    print(handle("skip"))
    assert not active()
    print("ok")
