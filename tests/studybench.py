"""Which model should answer class work?

Swapping the study model on a hunch is how this got worse twice before: once
to qwen2.5:14b, which took 858s for a worksheet, and once back again. So the
choice is measured instead.

The key below is the MATH2065 worksheet, every answer verified independently
with sympy rather than taken from any model's output. Scoring is deliberately
crude - does the final number appear, to the tolerance given - because the
thing being compared is whether the model gets the answer right, and a
partial-credit scheme would only hide that behind a number.

    python tests/studybench.py                      # the configured model
    python tests/studybench.py qwen3:8b deepseek-r1:8b
    python tests/studybench.py --questions 2,3,5    # just the hard ones
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis import plot, verify                           # noqa: E402
from jarvis.llm import LLM                                # noqa: E402
from jarvis.tools.knowledge import _extract               # noqa: E402
from jarvis.tools import study                             # noqa: E402
from jarvis.tools.study import (                          # noqa: E402
    STUDY_PROMPT, _ask_checked, _thinking, split_questions)

OUT = Path(__file__).resolve().parent.parent / "data" / "benchmarks"

WORKSHEET = Path.home() / "Downloads" / "Assignment 1_MATH 2065.pdf"

# (question, what the answer must contain, tolerance, one-line description)
#
# Each target is a number the answer has to arrive at. Several are given as
# alternatives where more than one convention is defensible - Q8 may or may
# not use the continuity correction, and both are markable.
KEY: dict[str, list[tuple[list[float], float, str]]] = {
    # Not c = 1/3 itself: question 2(iv) asks for the smallest x with
    # F(x) >= 1/3, so "1/3" is printed in the question and would score a hit
    # for an answer that never found it. p(0) = 1/9 and p(1) = 2/9 appear only
    # if the right root was chosen.
    "2": [([1 / 9], 0.02, "p(0) = 1/9, so c = 1/3 was the root chosen"),
          ([2 / 9], 0.02, "p(1) = 2/9")],
    "3": [([1 / 6, 16.67], 0.02, "P(US | defective) = 1/6")],
    "4": [([0.3087], 0.01, "P(dies 60-70 | survives to 50) = 0.3087")],
    "5": [([0.6993], 0.01, "P(>=2 hits | >=1 hit) = 0.6993")],
    "6": [([6.139], 0.02, "observed mean = 6.139"),
          ([924], 0.01, "expected frequency at x=6 is 924")],
    "7": [([1 / 41, 0.0244], 0.02, "alpha = 1/41 for the density"),
          ([0.2182], 0.02, "P(X > 3 | X > 0) = 0.2182 for the mass function")],
    "8": [([0.00033, 0.00058, 0.00116], 0.25,
           "P(violates guarantee), any of the three conventions")],
}

_NUMBER = re.compile(r"-?\d+(?:\.\d+)?(?:[eE]-?\d+)?")


def _numbers(text: str) -> list[float]:
    """Every number in the answer, including the two halves of a fraction."""
    found = []
    for raw in _NUMBER.findall(text.replace(",", "")):
        try:
            found.append(float(raw))
        except ValueError:
            pass
    # \frac{1}{41} is the answer 0.0244 written as two integers.
    for a, b in re.findall(r"\\d?frac\{(-?[\d.]+)\}\{(-?[\d.]+)\}", text):
        try:
            if float(b):
                found.append(float(a) / float(b))
        except ValueError:
            pass
    return found


def _hit(targets: list[float], tolerance: float, text: str) -> bool:
    numbers = _numbers(text)
    for want in targets:
        for got in numbers:
            if want == 0:
                if abs(got) < 1e-9:
                    return True
            elif abs(got - want) <= abs(want) * tolerance:
                return True
    return False


def run(model: str, wanted: list[str] | None) -> dict:
    questions = split_questions(_extract(WORKSHEET))
    if not questions:
        raise SystemExit(f"Could not read the worksheet at {WORKSHEET}")

    # _ask takes its model from current_model() and walks study.fallback, so
    # a model named on the command line has to be put there - handing it to
    # LLM() alone was ignored, and "studybench.py qwen3:8b" quietly measured
    # whatever the picker had chosen instead.
    if model:
        study.current_model = lambda: model
    llm = LLM(None)
    scored = total = 0
    faults = graphs = 0
    started = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []

    for number, body in questions:
        if number not in KEY or (wanted and number not in wanted):
            continue
        t0 = time.time()
        study.last_answered_by = ""
        try:
            # Deliberately the same call the real study path makes -
            # answer_worksheet's prompt, the thinking heuristic, and the
            # arithmetic recheck - rather than a plain chat. Benchmarking
            # different settings from the ones in use measures nothing.
            answer = _ask_checked(llm, [
                {"role": "system", "content": STUDY_PROMPT},
                {"role": "user", "content":
                    f"This is question {number} of an assignment. Answer only "
                    f"this question, completely, with every step:\n\n{body}"},
            ], think=_thinking(body))
        except Exception as e:
            answer = ""
            print(f"  Q{number}: failed ({e})")

        left = verify.problems(answer) + plot.problems(answer)
        faults += len(left)
        graphs += plot.expand(answer)[1]
        saved.append(f"## Question {number}  ({study.last_answered_by}, "
                     f"{time.time() - t0:.0f}s)\n\n{answer}")

        marks = []
        for targets, tolerance, description in KEY[number]:
            ok = _hit(targets, tolerance, answer)
            scored += ok
            total += 1
            marks.append(("ok " if ok else "MISS") + " " + description)
        print(f"  Q{number}  {time.time() - t0:5.0f}s  "
              f"{study.last_answered_by or '?':28} {len(answer):6} chars  "
              f"{len(left)} flag(s)\n           " + "\n           ".join(marks),
              flush=True)

    name = re.sub(r"[^\w.-]+", "_", model or study.current_model() or "default")
    path = OUT / f"answers-{name}-{time.strftime('%Y%m%d-%H%M%S')}.md"
    path.write_text("\n\n".join(saved), encoding="utf-8")
    print(f"  answers saved to {path}")
    return {"model": model or "(configured)", "score": scored, "out_of": total,
            "seconds": time.time() - started, "faults": faults, "graphs": graphs}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("models", nargs="*", help="models to compare")
    ap.add_argument("--questions", default="", help="e.g. 2,3,5")
    args = ap.parse_args()
    wanted = [q.strip() for q in args.questions.split(",") if q.strip()] or None

    results = []
    for model in args.models or [""]:
        print(f"\n=== {model or '(configured study model)'}")
        results.append(run(model, wanted))

    print(f"\n{'model':22} {'score':>8} {'time':>8} {'faults':>7} {'graphs':>7}")
    for r in sorted(results, key=lambda r: (-r["score"], r["seconds"])):
        print(f"{r['model']:22} {r['score']:>4}/{r['out_of']:<3} "
              f"{r['seconds']:>7.0f}s {r['faults']:>7} {r['graphs']:>7}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
