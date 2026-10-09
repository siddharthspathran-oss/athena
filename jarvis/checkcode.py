"""Final answers checked by running code.

verify.py checks the arithmetic it can read on the page. This goes further:
a calculated answer ends with a ```check block - a few lines of Python that
work each final result out again from the values in the question and print
it:

    ```check
    R = 2200; C = 100e-9
    fc = 1 / (2 * pi * R * C)
    print("fc", fc)
    ```

The block is run here, and every number it prints must appear in the
**Final answer:** line (within 1.5%, the same allowance verify.py gives for
rounding). If one does not, the answer goes back to be fixed. The block never
reaches the page; a line saying the results were checked does.

The code is the model's, so it runs locked down: maths only (math, cmath,
sympy, numpy, fractions), no other imports, no file or network access, no
names starting with "_", in a separate Python process that is killed after
ten seconds.
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys

_BLOCK = re.compile(r"```check[ \t]*\n(.*?)```", re.S)
_ALLOWED_MODULES = {"math", "cmath", "sympy", "numpy", "fractions", "statistics"}
_BANNED_NAMES = {"open", "exec", "eval", "compile", "__import__", "globals", "locals", "vars", "getattr",
                 "setattr", "delattr", "input", "breakpoint", "help", "exit", "quit", "memoryview"}
_TOLERANCE = 0.015
_PRELUDE = "from math import *\n"


def _safe(code: str) -> str | None:
    """Why the code may not run, or None if it is plain maths."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"the check block is not valid Python ({e.msg})"
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            if any(m.split(".")[0] not in _ALLOWED_MODULES for m in mods):
                return "the check block imports something other than maths libraries"
        elif isinstance(node, ast.Name) and (node.id in _BANNED_NAMES | _BANNED_ATTRS or node.id.startswith("__")):
            return f"the check block uses {node.id}, which is not allowed"
        elif isinstance(node, ast.Attribute) and (node.attr.startswith("_") or node.attr in _BANNED_ATTRS):
            return f"the check block uses .{node.attr}, which is not allowed"
    return None


# Maths libraries can still reach files or run text as code: numpy's load and
# save, sympy's sympify/S/parse_expr (which evaluate strings).
_BANNED_ATTRS = {"sympify", "parse_expr", "S", "lambdify", "load", "save", "savez", "savez_compressed",
                 "fromfile", "tofile", "loadtxt", "savetxt", "genfromtxt", "memmap", "DataSource", "system",
                 "popen", "ctypeslib", "f2py", "testing", "utilities", "external", "printing", "preview",
                 "init_printing", "lib"}


def run(code: str, timeout: float = 10) -> tuple[list[tuple[str, float]], str | None]:
    """(printed label/number pairs, error) for one check block."""
    why = _safe(code)
    if why:
        return [], why
    try:
        r = subprocess.run([sys.executable, "-I", "-c", _PRELUDE + code], capture_output=True, text=True,
                           timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return [], "the check block took too long"
    if r.returncode != 0:
        last = (r.stderr.strip().splitlines() or ["error"])[-1]
        return [], f"the check block failed: {last[:120]}"
    out = []
    for line in r.stdout.splitlines():
        nums = re.findall(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", line)
        # A printed list or table ("seq [1, 2, 4]") is working, not a final
        # value: only plain "label value" lines are compared.
        if nums and not re.search(r"[\[\](){},]", line):
            label = re.sub(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", "", line).strip(" :=,") or "value"
            out.append((label, float(nums[-1])))
    return out, None


_FINAL = re.compile(r"\*\*Final answer:?\*\*:?(.*?)(?:\n\s*\n|\Z)", re.S | re.I)
_SI = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "μ": 1e-6, "m": 1e-3, "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9}


def _final_numbers(markdown: str) -> list[float]:
    """Every number in the Final answer, also read with its SI prefix (2.2 kΩ is 2200)."""
    m = _FINAL.search(markdown)
    if not m:
        return []
    text = re.sub(r"\\times\s*10\^\{?(-?\d+)\}?", r"e\1", m.group(1))
    text = re.sub(r"(?<=\d),(?=\d)", "", text)           # 1,24,750 or 124,750 -> 124750
    out = []
    for num, pre in re.findall(r"(-?\d+(?:\.\d+)?(?:e-?\d+)?)\s*\\?,?\s*(?:\\mathrm\{)?([pnuµμmkKMG])?", text):
        v = float(num)
        out.append(v)
        if pre:
            out.append(v * _SI[pre])
    return out


def _close(a: float, b: float) -> bool:
    if a == b:
        return True
    return abs(a - b) <= _TOLERANCE * max(abs(a), abs(b))


def problems(markdown: str) -> list[str]:
    """What the check blocks found wrong (empty when every printed result is in the final answer)."""
    found = []
    for code in _BLOCK.findall(markdown):
        results, error = run(code)
        if error:
            found.append(error)
            continue
        finals = _final_numbers(markdown)
        for label, value in results:
            if finals and not any(_close(value, f) or _close(round(value, 4), f) for f in finals):
                found.append(f"code says {label} = {value:.6g}, but the Final answer line does not have that value")
    return found


def strip(markdown: str) -> str:
    """The page without its check blocks - a note in their place if they passed."""
    if not _BLOCK.search(markdown):
        return markdown
    ok = not problems(markdown)
    note = "\n\n*✓ Final results checked by running the calculation.*\n" if ok else "\n"
    return _BLOCK.sub("", markdown).rstrip() + note


if __name__ == "__main__":
    page = ("Working...\n\n**Final answer:** \\(f_c \\approx 723.4\\) Hz\n\n"
            "```check\nR = 2200; C = 100e-9\nprint('fc', 1/(2*pi*R*C))\n```\n")
    assert problems(page) == [], problems(page)
    wrong = page.replace("723.4", "823.4")
    assert problems(wrong) and "723" in problems(wrong)[0]
    assert "imports" in problems("```check\nimport os\nprint(os.getcwd())\n```")[0]
    assert "not allowed" in problems("```check\nopen('x')\n```")[0]
    assert "not allowed" in problems("```check\nimport numpy as np\nnp.load('x')\n```")[0]
    assert "not allowed" in problems("```check\nfrom sympy import sympify\nsympify('1')\n```")[0]
    sym = ("**Final answer:** x = 3\n\n```check\nfrom sympy import symbols, solve\nx = symbols('x')\n"
           "print('x', [r for r in solve(x**2 - 9, x) if r > 0][0])\n```")
    assert problems(sym) == [], problems(sym)
    assert "2200" not in strip(page) and "checked" in strip(page)
    assert _final_numbers("**Final answer:** R = 2.2 kΩ") == [2.2, 2200.0]
    print("ok")
