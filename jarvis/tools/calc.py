"""calculate: maths done by running Python, not by the model's mental arithmetic.

Benchmarked 2 Oct 2026 on 15 engineering/maths questions: qwen2.5:7b answered
6 right on its own and 11 when it wrote a program that was run; the 3B coder
model 9 and 13. Models are bad at arithmetic and good at writing it down.

The code comes from a model, so it runs in a separate, isolated Python with a
time limit, and may import only maths libraries: no files, no network, no
processes. Anything else is refused before it runs.
"""
from __future__ import annotations

import re
import subprocess
import sys

from ..registry import tool

ALLOWED = {"math", "cmath", "statistics", "fractions", "decimal", "itertools", "functools",
           "random", "numpy", "sympy", "scipy", "mpmath", "datetime"}

# Runs first inside the child, a second line of defence after the static check
# below: an import made by the generated code itself (module __main__) must be
# an allowed one. numpy/sympy importing their own internals is not affected.
_PRELUDE = r'''
import builtins as _b
_ok = %r
_real = _b.__import__

def _imp(name, globals=None, *a, **k):
    if (globals or {}).get("__name__") == "__main__" and name.split(".")[0] not in _ok:
        raise ImportError("not allowed in calculate: " + name)
    return _real(name, globals, *a, **k)
_b.__import__ = _imp
del _b
'''

_FORBIDDEN = re.compile(r"\b(?:__\w+__|globals|locals|getattr|setattr|delattr|vars|open|exec|eval|compile|"
                        r"input|breakpoint|os|sys|subprocess|socket|shutil|pathlib|ctypes|importlib|"
                        r"pickle|marshal|threading|multiprocessing|signal|builtins|"
                        # numpy/sympy calls that touch files or run text as code
                        r"load|loadtxt|genfromtxt|save|savez\w*|savetxt|fromfile|tofile|memmap|open_memmap|"
                        r"DataSource|sympify|parse_expr|lambdify|preview|init_printing)\b")


def _imports(code: str) -> set[str]:
    return {m.split(".")[0] for m in re.findall(r"^\s*(?:from|import)\s+([\w.]+)", code, re.M)}


@tool(tags=["math"], timeout=20)
def calculate(code: str) -> str:
    """Work out a maths, physics or engineering number by running Python. Use this
    for ANY calculation - arithmetic, percentages, interest, roots, integrals,
    matrices, statistics, circuit values, unit conversions - instead of
    computing it yourself. Print the final answer.

    code: a short Python program that prints the result; may use math, statistics, fractions, numpy, sympy, scipy
    """
    code = (code or "").strip()
    m = re.search(r"```(?:python)?\s*(.*?)```", code, re.S)
    if m:
        code = m.group(1).strip()
    if not code:
        return "ERROR: no code given."
    if "print(" not in code:
        # A bare expression ("17/100*8450") is the commonest thing to pass:
        # print the last statement when it is an expression.
        import ast
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return f"ERROR: SyntaxError: {e.msg}"
        if tree.body and isinstance(tree.body[-1], ast.Expr):
            last = tree.body[-1]
            lines = code.splitlines()
            expr = ast.get_source_segment(code, last)
            code = "\n".join(lines[:last.lineno - 1]) + f"\nprint({expr})"
    bad = _imports(code) - ALLOWED
    if bad:
        return f"ERROR: calculate may only use maths libraries, not {', '.join(sorted(bad))}."
    if _FORBIDDEN.search(code):
        return "ERROR: that code uses something calculate does not allow (files, system, introspection)."
    try:
        import tempfile
        with tempfile.TemporaryDirectory() as empty:     # nothing to find even if something slipped through
            p = subprocess.run([sys.executable, "-I", "-c", _PRELUDE % (sorted(ALLOWED),) + "\n" + code],
                               capture_output=True, text=True, timeout=15, cwd=empty)
    except subprocess.TimeoutExpired:
        return "ERROR: the calculation took longer than 15 seconds."
    out = (p.stdout or "").strip()
    if p.returncode != 0:
        err = (p.stderr or "").strip().splitlines()
        return f"ERROR: {err[-1] if err else 'the code failed'}"
    return out[-1500:] if out else "ERROR: the code printed nothing - print the answer."


# Wolfram|Alpha's API made for AI tools: one GET, plain text back (28 Oct 2026).
# Built in rather than an MCP server - none of those is official or maintained.
WOLFRAM = "https://www.wolframalpha.com/api/v1/llm-api"


@tool(tags=["math"], timeout=25)
def wolfram(query: str) -> str:
    """Ask Wolfram|Alpha, for working shown step by step: integrals, derivatives,
    Fourier/Laplace/Z transforms, equations, probability distributions, units,
    constants and science facts. Use for "step by step", "Laplace transform of",
    "solve ...", "what is the density of ...". Send only the maths or science
    question - never names, messages or anything personal.

    query: the question in plain words or maths notation, e.g. "Laplace transform of t*e^(-2t)"
    """
    from ..cloud import get_key
    key = get_key("wolfram")
    if not key:
        return ("Wolfram isn't set up: make a free LLM API AppID at developer.wolframalpha.com, "
                "then save it with python add_keys.py, provider wolfram.")
    return ask_wolfram(query, key)


def ask_wolfram(query: str, key: str) -> str:
    import urllib.error
    import urllib.parse
    import urllib.request
    url = WOLFRAM + "?" + urllib.parse.urlencode({"input": query, "appid": key, "maxchars": 3000})
    try:
        with urllib.request.urlopen(url, timeout=20) as r:          # the URL holds the key: never logged
            return r.read().decode("utf-8", "replace").strip()
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return "ERROR: the saved Wolfram AppID doesn't work - save it again with add_keys.py."
        body = e.read().decode("utf-8", "replace").strip()[:600]
        return f"ERROR: Wolfram couldn't answer that (HTTP {e.code}). {body}"
    except OSError as e:
        return f"ERROR: couldn't reach Wolfram ({str(e)[:80]})"
