"""Checking the model's arithmetic before the answer is shown.

Every wrong answer on the probability worksheet was arithmetic, not method:
(4/5)^9 written as 0.214748 instead of 0.134218, a twelve-term sum adding to
22806 instead of 25145, 10.5/3.08 called 3.57. The method around them was
right. Asking the model in its prompt to "check your work" does not fix this -
it checked, said "this is incorrect because probabilities cannot be negative",
and then wrote the negative probability down anyway.

So the checking happens here instead, in Python, where 25145 is 25145.

Two checks, both deliberately narrow:

  * every "a = b = c" chain in the answer is evaluated, and the parts that are
    pure arithmetic must agree with each other;
  * anything the answer's conclusion states as a probability - P(...) or
    F(...) - must land in [0, 1]. Only the conclusion, because a model that
    solves a cubic properly writes "p(1) = -6, so c = 1 is rejected" on the
    way past, and that is good work, not a mistake.

Narrow because a false complaint is worse than a missed one: it sends the
model back to rewrite an answer that was already right. Anything this module
cannot evaluate with certainty it ignores. It will not catch a conceptual
mistake - substituting P(X >= 3) where the question asked for P(X > 3) is
arithmetic that adds up perfectly - and it is not meant to.
"""
from __future__ import annotations

import ast
import re
from fractions import Fraction

# How far two sides of a "=" may drift before it counts as a mistake. The
# model rounds as it goes - "sqrt(3.12) ~ 1.77" is 0.2% out and perfectly
# normal - so the tolerance has to sit above ordinary rounding and below a
# real slip. The real ones missed by a long way: 4.7% (3.57 for 3.41), 9%
# (22806 for 25145), 60% (0.214748 for 0.134218).
_TOLERANCE = 0.015

# Exact arithmetic gets no tolerance at all. "1250000000 - 937500000 +
# 187500000 = 499500000" is out by one part in a thousand, which any relative
# tolerance would wave through, and it is still simply wrong.
_MAX_COMPLAINTS = 6


# ---------- LaTeX to something evaluable ----------

def _group(s: str, i: int) -> tuple[str, int]:
    """Read a {...} group starting at s[i] == '{'. Returns (inside, next i)."""
    depth = 0
    for j in range(i, len(s)):
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                return s[i + 1:j], j + 1
    return s[i + 1:], len(s)


def _arg(s: str, i: int) -> tuple[str, int]:
    """Read one LaTeX argument: a {group} or a single following character."""
    while i < len(s) and s[i] == " ":
        i += 1
    if i < len(s) and s[i] == "{":
        return _group(s, i)
    return (s[i], i + 1) if i < len(s) else ("", i)


_DROP = ("\\left", "\\right", "\\,", "\\;", "\\!", "\\:", "\\quad", "\\qquad",
         "\\ ", "\\displaystyle", "$", "\\big", "\\Big", "\\bigl", "\\bigr")


def _latex_to_expr(tex: str) -> str | None:
    """Rewrite a scrap of LaTeX as a Python expression, or None if it holds
    anything this module will not vouch for."""
    s = tex.strip()
    if not s:
        return None
    # Digits either side of a thin space: "125\,000" is one number, but
    # "50\,6\,250\,000" on the 23 Sep worksheet meant 50 x 6,250,000. There is
    # no telling which, and joining them made a correct step look wrong.
    if re.search(r"\d\s*\\[,;:!]\s*\d", s):
        return None
    # Anything with a symbol, a limit, a sum, a case split or a percent sign
    # is not plain arithmetic, and guessing at it is how false complaints get
    # made. Bail out rather than half-understand it.
    for bad in ("\\sum", "\\int", "\\lim", "\\begin", "\\prod", "\\binom",
                "\\%", "%", "\\ldots", "\\dots", "\\cdots", "\\infty",
                "\\pm", "\\mp", "\\log", "\\ln", "\\sin", "\\cos", "\\tan",
                "\\boxed", "\\text", "\\mathrm", "\\overline", "\\bar"):
        if bad in s:
            return None

    for junk in _DROP:
        s = s.replace(junk, " " if junk.startswith("\\ ") else "")
    s = s.replace("\\cdot", "*").replace("\\times", "*").replace("\\div", "/")

    out: list[str] = []
    i = 0
    while i < len(s):
        if s.startswith(("\\frac", "\\dfrac", "\\tfrac"), i):
            i += 6 if s[i:i + 6] in ("\\dfrac", "\\tfrac") else 5
            num, i = _arg(s, i)
            den, i = _arg(s, i)
            top = _latex_to_expr(num)
            bot = _latex_to_expr(den)
            if top is None or bot is None:
                return None
            out.append(f"(({top})/({bot}))")
            continue
        if s.startswith("\\sqrt", i):
            i += 5
            if i < len(s) and s[i] == "[":      # an nth root, not handled
                return None
            arg, i = _arg(s, i)
            inner = _latex_to_expr(arg)
            if inner is None:
                return None
            out.append(f"sqrt({inner})")
            continue
        if s[i] == "^":
            i += 1
            arg, i = _arg(s, i)
            power = _latex_to_expr(arg)
            if power is None:
                return None
            out.append(f"**({power})")
            continue
        if s[i] == "\\":                        # some other command: give up
            return None
        if s[i] in "{}":                        # a leftover grouping brace
            out.append("(" if s[i] == "{" else ")")
            i += 1
            continue
        out.append(s[i])
        i += 1

    expr = "".join(out)
    # The model writes "4(1) - 10(1)^2" and "2(0.5)(3)"; Python needs the
    # multiplication spelled out.
    expr = re.sub(r"(\d|\))\s*\(", r"\1*(", expr)
    expr = re.sub(r"\)\s*(\d)", r")*\1", expr)

    stripped = re.sub(r"sqrt", "", expr)
    if not stripped.strip() or re.search(r"[A-Za-z]", stripped):
        return None
    if not re.fullmatch(r"[\d\s.+\-*/()]*", stripped):
        return None
    return expr


# ---------- evaluating it ----------

def _eval(node: ast.AST) -> Fraction | float:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, int):
            return Fraction(node.value)
        if isinstance(node.value, float):
            return Fraction(node.value)     # exact value of the written float
        raise ValueError("constant")
    if isinstance(node, ast.UnaryOp):
        val = _eval(node.operand)
        if isinstance(node.op, ast.USub):
            return -val
        if isinstance(node.op, ast.UAdd):
            return val
        raise ValueError("unary")
    if isinstance(node, ast.BinOp):
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            if right == 0:
                raise ValueError("divide by zero")
            return left / right
        if isinstance(node.op, ast.Pow):
            if isinstance(right, Fraction) and right.denominator == 1:
                exp = int(right)
                if abs(exp) > 64:               # keeps a typo from hanging us
                    raise ValueError("exponent")
                if left == 0 and exp < 0:
                    raise ValueError("divide by zero")
                return left ** exp
            return float(left) ** float(right)
        raise ValueError("operator")
    if isinstance(node, ast.Call):
        if (isinstance(node.func, ast.Name) and node.func.id == "sqrt"
                and len(node.args) == 1 and not node.keywords):
            val = float(_eval(node.args[0]))
            if val < 0:
                raise ValueError("sqrt of a negative")
            return val ** 0.5
        raise ValueError("call")
    raise ValueError("node")


def value_of(tex: str) -> tuple[Fraction | float | None, bool]:
    """(value, is_exact) for a scrap of LaTeX, or (None, False).

    is_exact is False as soon as a decimal point or a square root appears: a
    number the model rounded on the way past cannot be held to the last digit.
    """
    # "= 0.0005," - the sentence's comma is not part of the number, and left
    # on it made the whole step unreadable, so the check silently skipped it.
    expr = _latex_to_expr(tex.strip().rstrip(",;:"))
    if expr is None:
        return None, False
    try:
        value = _eval(ast.parse(expr, mode="eval"))
    except Exception:
        return None, False
    exact = "." not in expr and "sqrt" not in expr
    return value, exact


def _agree(a, b, exact: bool) -> bool:
    if a == b:
        return True
    if exact:
        return False
    fa, fb = float(a), float(b)
    scale = max(abs(fa), abs(fb))
    if scale < 1e-9:
        return abs(fa - fb) < 1e-9
    return abs(fa - fb) / scale <= _TOLERANCE


def _short(value) -> str:
    if isinstance(value, Fraction) and value.denominator == 1:
        return str(value.numerator)
    f = float(value)
    return f"{f:.6g}"


def _clip(tex: str, limit: int = 60) -> str:
    tex = " ".join(tex.split())
    return tex if len(tex) <= limit else tex[:limit - 1] + "…"


# ---------- pulling the equations out of the answer ----------

_SPAN = re.compile(r"\\\[(?P<d>.+?)\\\]|\$\$(?P<dd>.+?)\$\$|\\\((?P<i>.+?)\\\)", re.S)


def _spans(markdown: str) -> list[tuple[int, int, str, bool]]:
    """Every \\[ ... \\], \\( ... \\) and $$ ... $$ span in the answer, in
    order: (start, end, contents, is_display)."""
    out = []
    for m in _SPAN.finditer(markdown):
        display = m.group("i") is None
        body = m.group("d") or m.group("dd") or m.group("i") or ""
        out.append((m.start(), m.end(), body, display))
    return out




# Where one statement ends and the next begins inside a single maths span:
# an aligned/cases row break ("\\", "\\[4pt]") or a \quad gap. Without this,
#   \begin{aligned} P(D|C)P(C) &= 0.002 \times \frac14 = 0.0005,\\[4pt]
#                   P(D|M)P(M) &= 0.004 \times \frac12 = 0.002 \end{aligned}
# read as one long chain claiming 0.0005 = 0.002, and
#   \mu = 200(0.05) = 10,\qquad \sigma = \sqrt{9.5} \approx 3.082
# as 10 = 3.08. Both were flagged on the 23 Sep worksheet (questions 3 and
# 8), each costing a pointless re-ask and a warning on a correct answer.
_BREAK = re.compile(r"\\\\(?:\[[^\]]*\])?|\\qquad|\\quad")
# ...but a row that opens with "=" continues the chain above it - that is
# how an aligned derivation is written, and it is where long sums go wrong.
_CONTINUES = re.compile(r"^\s*&?\s*(?:=|\\approx)")


def _statements(segment: str) -> list[str]:
    # The environment wrapper is layout, not maths; left in, it made the
    # first and last step of every aligned derivation unreadable.
    segment = re.sub(r"\\(?:begin|end)\{\w+\*?\}(?:\{[^{}]*\})?", " ", segment)
    rows: list[str] = []
    for piece in _BREAK.split(segment):
        if rows and _CONTINUES.match(piece):
            rows[-1] += " " + piece.replace("&", " ", 1)
        else:
            rows.append(piece)
    return [r.replace("&", " ") for r in rows if r.strip()]


# Two displayed equations in a row that are one chain split in two:
#   \[ \bar x = \frac{0 + 7 + 120 + ... + 0}{4096} \]
#   \[ \bar x = \frac{22354}{4096} \approx 5.45 \]
# Each half is self-consistent, so the wrong twelve-term total - the mistake
# behind question 6 being wrong on every model - sailed through. Joined only
# when both are displayed, nothing but a connective word sits between them,
# and the second restates the same left-hand side or opens with "=": two
# inline values of p(1) for two candidate roots are different claims.
_BRIDGE = re.compile(r"^[\s,.;:]*(?:(?:so|hence|thus|therefore|then|giving|"
                     r"which\s+gives|and|i\.e\.)[\s,.;:]*)?$", re.I)


_RELATION = re.compile(r"\\(?:leq|geq|neq|le|ge|ne|lt|gt|sim|simeq|propto|to"
                       r"|implies|Rightarrow|Longrightarrow)(?![A-Za-z])")


def _split_top(segment: str) -> list[str] | None:
    """Split on '=' and '\\approx', but only outside brackets.

    The '=' in "P(X = 0)" is part of the name of the thing, not a claim that
    two expressions are equal; splitting there produced nonsense.
    """
    parts: list[str] = []
    depth = 0
    start = 0
    i = 0
    while i < len(segment):
        ch = segment[i]
        if ch in "({[":
            depth += 1
        elif ch in ")}]":
            depth -= 1
            if depth < 0:
                return None
        elif depth == 0:
            if segment.startswith("\\approx", i):
                parts.append(segment[start:i])
                i += 7
                start = i
                continue
            # "\leq" and friends mean this is an inequality: the sides are not
            # claimed to be equal, so there is nothing here to check.
            #
            # Matched with a boundary, not startswith: "\le" is a prefix of
            # "\left", so every bracketed expression in the worksheet looked
            # like an inequality and was skipped. That silently cost the check
            # on "(4/5)^9 = 0.214748", which is the one that was wrong.
            if _RELATION.match(segment, i):
                return None
            if ch in "<>":
                return None
            if ch == "=":
                parts.append(segment[start:i])
                i += 1
                start = i
                continue
        i += 1
    if depth != 0:
        return None
    parts.append(segment[start:])
    return parts if len(parts) >= 2 else None


_PROBABILITY = re.compile(r"^\s*\\?[PpF]\s*(?:_\{?\w*\}?)?\s*[({]")

# "...and the conditional probability is \( \frac{4}{3} \)." A summary line
# states its result in prose rather than as an equation, so there is no chain
# to check and nothing called P(...) to recognise. A worksheet answer came
# back with every other part corrected and this one still impossible.
#
# The gap cannot contain a full stop, which keeps the match inside one
# sentence and stops it reaching across into an unrelated number.
_PROSE_PROBABILITY = re.compile(
    r"probabilit(?:y|ies)[^.]{0,80}?\b(?:is|are|=|:)\s*"
    r"(?:approximately\s+|about\s+|roughly\s+)?\\\((.+?)\\\)")

_CONCLUSION = re.compile(r"(?i)(?:^|\n|\.\s)\s*\**\s*(?:in short|final answer|"
                         r"conclusion|in summary|to summari[sz]e)\b")


def _conclusion(markdown: str) -> str:
    """The part of the answer that states what the answer is.

    An out-of-range probability is only a mistake if the answer ends up
    believing it. A model that solves a cubic properly writes "p(1) = -6, so
    c = 1 is rejected" on the way past, and complaining about that would send
    a correct answer back to be redone - which is exactly what happened when
    this check read the whole text.
    """
    matches = list(_CONCLUSION.finditer(markdown))
    if matches:
        return markdown[matches[-1].start():]
    return markdown[int(len(markdown) * 0.75):]


# ---------- the checks ----------

def problems(markdown: str) -> list[str]:
    """Everything demonstrably wrong with the arithmetic in an answer.

    An empty list does not mean the answer is right - only that nothing this
    module can evaluate disagrees with itself.
    """
    found: list[str] = []
    seen: set[str] = set()

    def add(complaint: str) -> None:
        if complaint not in seen:
            seen.add(complaint)
            found.append(complaint)

    ending = _conclusion(markdown)

    # A step that does not add up is wrong wherever it stands, so the
    # arithmetic is checked over the whole answer; a probability outside
    # [0, 1] is only wrong if the answer finishes holding it, so that is
    # checked over the conclusion alone.
    # The last chain of the previous displayed equation, for _BRIDGE.
    prev: tuple[int, str, tuple[str, object, bool] | None] | None = None
    chains: list[tuple[str, list[str], list, bool]] = []
    for start, end, span, display in _spans(markdown):
        rows = _statements(span)
        last: tuple[str, object | None] = ("\0", None)   # (lhs, last value)
        for n, row in enumerate(rows):
            parts = _split_top(row)
            if not parts:
                last = ("\0", None)
                continue
            valued: list[tuple[str, object, bool]] = []
            for part in parts:
                value, exact = value_of(part)
                if value is not None:
                    valued.append((part, value, exact))
            lhs = " ".join(parts[0].split())
            if (n == 0 and display and prev and prev[2] and valued
                    and _BRIDGE.match(markdown[prev[0]:start])
                    and (not lhs or lhs == prev[1])):
                valued.insert(0, prev[2])
            chains.append((span, parts, valued, display))
            last = (lhs, valued[-1] if valued else None)
        prev = (end, last[0], last[1]) if display else None

    for segment, parts, valued, _display in chains:
        # Every evaluable step in one chain claims to be the same number.
        for (tex_a, val_a, exact_a), (tex_b, val_b, exact_b) in zip(valued, valued[1:]):
            if not _agree(val_a, val_b, exact_a and exact_b):
                add(f'"{_clip(tex_a)}" = {_short(val_a)}, '
                    f'but the next step says it is {_short(val_b)} '
                    f'("{_clip(tex_b)}")')

        if _PROBABILITY.match(parts[0]) and segment in ending:
            for tex, value, _exact in valued:
                f = float(value)
                if f < -1e-9 or f > 1 + 1e-9:
                    add(f'the answer finishes with "{_clip(parts[0])}" equal to '
                        f'{_short(value)}, which is impossible - a probability '
                        f'lies between 0 and 1 (from "{_clip(tex)}")')
                    break

        if len(found) >= _MAX_COMPLAINTS:
            break

    # A result stated in words rather than as an equation.
    for m in _PROSE_PROBABILITY.finditer(ending):
        value, _exact = value_of(m.group(1))
        if value is None:
            continue
        f = float(value)
        if f < -1e-9 or f > 1 + 1e-9:
            add(f'the answer finishes by calling {_short(value)} a probability '
                f'("{_clip(m.group(0))}"), which is impossible - a probability '
                f'lies between 0 and 1')

    return found[:_MAX_COMPLAINTS]
