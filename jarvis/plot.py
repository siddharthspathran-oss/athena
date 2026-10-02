"""Drawing the graphs that appear in an answer.

The model asks for a plot by writing a fenced ```plot block holding JSON - a
range, some expressions, labels. It does not write Python. A 1-billion-to-7
billion parameter model producing code that then gets executed on the machine
it is running on is not a trade worth making, and a declarative spec covers
what a class answer actually needs: y against x, several curves, log axes,
a few marked points.

Expressions are evaluated against numpy with nothing else in scope, so a
spec cannot reach the filesystem even if the model asks it to.
"""
from __future__ import annotations

import base64
import io
import json
import math
import re

import numpy as np

_FENCE = re.compile(r"```plot\s*\n(.*?)```", re.S | re.I)

# Everything an expression is allowed to touch.
_SAFE = {
    "pi": math.pi, "e": math.e, "inf": math.inf,
    "sin": np.sin, "cos": np.cos, "tan": np.tan,
    "asin": np.arcsin, "acos": np.arccos, "atan": np.arctan,
    "sinh": np.sinh, "cosh": np.cosh, "tanh": np.tanh,
    "exp": np.exp, "log": np.log, "ln": np.log, "log10": np.log10, "log2": np.log2,
    "sqrt": np.sqrt, "abs": np.abs, "sign": np.sign,
    "floor": np.floor, "ceil": np.ceil, "round": np.round,
    "min": np.minimum, "max": np.maximum,
    "where": np.where, "clip": np.clip, "real": np.real, "imag": np.imag,
    "angle": np.angle, "deg": np.degrees, "rad": np.radians,
    "heaviside": lambda v: np.heaviside(v, 0.5),
}

_STYLE = {
    "bg": "#faf9f7", "ink": "#1c1b19", "grid": "#ddd9d2",
    "series": ["#2f6fb3", "#c2572c", "#3f8a58", "#8a4fa8", "#b08b1e", "#527a8a"],
}


def _values(expr: str, x: np.ndarray) -> np.ndarray | None:
    """Evaluate one expression over x, or None if it will not evaluate."""
    # Written as maths, not Python: x^4 is a power ("20*log10(1/sqrt(1+x^4))"
    # failed as XOR on 2 Oct 2026), and ln is the natural log.
    expr = expr.replace("^", "**")
    scope = dict(_SAFE)
    scope["x"] = x
    try:
        with np.errstate(all="ignore"):
            out = eval(expr, {"__builtins__": {}}, scope)   # noqa: S307 - see module docstring
    except Exception:
        return None
    arr = np.asarray(out, dtype=float)
    if arr.ndim == 0:
        arr = np.full_like(x, float(arr))
    return arr if arr.shape == x.shape else None


def render(spec: dict) -> str | None:
    """Draw one plot. Returns a data URI, or None if the spec is unusable."""
    import matplotlib
    matplotlib.use("Agg")           # no window: this runs on a worker thread
    import matplotlib.pyplot as plt

    curves = spec.get("series") or spec.get("curves") or []
    if isinstance(curves, dict):
        curves = [curves]
    if not curves:
        return None

    lo = float(spec.get("xmin", spec.get("x_min", 0)))
    hi = float(spec.get("xmax", spec.get("x_max", 10)))
    if not math.isfinite(lo) or not math.isfinite(hi) or lo == hi:
        return None
    points = int(spec.get("points", 400))
    points = max(20, min(points, 4000))

    log_x = str(spec.get("xscale", "")).lower() == "log"
    if log_x and lo > 0:
        x = np.logspace(math.log10(lo), math.log10(hi), points)
    else:
        x = np.linspace(lo, hi, points)

    fig, ax = plt.subplots(figsize=(7.2, 4.0), dpi=150)
    fig.patch.set_facecolor(_STYLE["bg"])
    ax.set_facecolor(_STYLE["bg"])

    drawn = 0
    for i, curve in enumerate(curves):
        if isinstance(curve, str):
            curve = {"expr": curve}
        expr = str(curve.get("expr") or curve.get("y") or "").strip()
        if not expr:
            continue
        y = _values(expr, x)
        if y is None:
            continue
        ax.plot(x, y, linewidth=2,
                color=_STYLE["series"][i % len(_STYLE["series"])],
                label=str(curve.get("label") or expr))
        drawn += 1

    if not drawn:
        plt.close(fig)
        return None

    if log_x:
        ax.set_xscale("log")
    if str(spec.get("yscale", "")).lower() == "log":
        ax.set_yscale("log")

    ax.set_xlabel(str(spec.get("xlabel", "x")), color=_STYLE["ink"])
    ax.set_ylabel(str(spec.get("ylabel", "y")), color=_STYLE["ink"])
    if spec.get("title"):
        ax.set_title(str(spec["title"]), color=_STYLE["ink"], fontsize=12)
    ax.grid(True, color=_STYLE["grid"], linewidth=0.8)
    ax.tick_params(colors=_STYLE["ink"])
    for edge in ax.spines.values():
        edge.set_color(_STYLE["grid"])
    if drawn > 1 or curves and isinstance(curves[0], dict) and curves[0].get("label"):
        ax.legend(facecolor=_STYLE["bg"], edgecolor=_STYLE["grid"],
                  labelcolor=_STYLE["ink"])
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=_STYLE["bg"])
    plt.close(fig)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def problems(markdown: str) -> list[str]:
    """Why the plot blocks in an answer will not draw, if they will not.

    Worth knowing before the answer is shown rather than after: a plot block
    that fails leaves "a graph was requested but could not be drawn" on the
    page, which is no use to anyone, and the model can usually fix its own
    spec when told what was wrong with it.
    """
    found: list[str] = []
    for m in _FENCE.finditer(markdown):
        try:
            spec = json.loads(m.group(1))
        except Exception as e:
            found.append(f"a ```plot block is not valid JSON ({e}); "
                         f"write it as a single JSON object")
            continue
        if not isinstance(spec, dict):
            found.append("a ```plot block must be a JSON object, not a list")
            continue
        curves = spec.get("series") or spec.get("curves") or []
        if isinstance(curves, dict):
            curves = [curves]
        if not curves:
            found.append('a ```plot block has no "series" to draw')
            continue
        lo = spec.get("xmin", spec.get("x_min", 0))
        hi = spec.get("xmax", spec.get("x_max", 10))
        try:
            lo, hi = float(lo), float(hi)
        except (TypeError, ValueError):
            found.append("a ```plot block has an xmin/xmax that is not a number")
            continue
        if lo == hi or not (math.isfinite(lo) and math.isfinite(hi)):
            found.append(f"a ```plot block has an empty x range ({lo} to {hi})")
            continue
        x = np.linspace(lo, hi, 64)
        is_cdf = bool(_CDF_WORDS.search(" ".join(
            str(spec.get(k, "")) for k in ("title", "ylabel", "y_label"))))
        for curve in curves:
            if isinstance(curve, str):
                curve = {"expr": curve}
            expr = str(curve.get("expr") or curve.get("y") or "").strip()
            y = _values(expr, x) if expr else None
            if not expr:
                found.append('a ```plot series has no "expr"')
            elif y is None:
                found.append(f'the plot expression "{expr}" does not evaluate. '
                             f"Write it in terms of x using only real numbers and "
                             f"sin, cos, exp, log, sqrt, abs, heaviside")
            elif is_cdf and np.ptp(y) > 1e-9:
                found.extend(_cdf_problems(expr, y, hi))
    return found


# A distribution function has two properties any plot of one can be held to:
# it never falls, and it ends at 1. On 24 Sep 2026 gpt-oss-120b wrote the
# right F(x) in words - 1/9, 1/3, 1 - and then plotted each jump as the
# heaviside coefficient it had just used for the level, 1/9, 2/9, 4/9: a
# graph that tops out at 0.44 under a correct answer. verify.py cannot see
# inside a plot block, so it is checked here.
_CDF_WORDS = re.compile(r"\bcdf\b|distribution\s+function|cumulative|\bF\s*\(\s*x\s*\)", re.I)


def _cdf_problems(expr: str, y: np.ndarray, hi: float) -> list[str]:
    out: list[str] = []
    if np.any(np.diff(y) < -1e-6):
        out.append(f'the CDF plotted by "{expr}" goes down somewhere; a '
                   f"distribution function never decreases")
    end = float(y[-1])
    if abs(end - 1.0) > 0.02:
        out.append(f'the CDF plotted by "{expr}" ends at {end:.3f} at x = {hi:g}, '
                   f"but a distribution function must reach 1. With heaviside "
                   f"steps, each coefficient is the jump p(x) at that point, "
                   f"not the level F(x) - check them against the F(x) you wrote")
    return out


def expand(markdown: str) -> tuple[str, int]:
    """Replace every ```plot block with an image. Returns (markdown, drawn)."""
    drawn = 0

    def swap(m: re.Match) -> str:
        nonlocal drawn
        try:
            spec = json.loads(m.group(1))
        except Exception:
            return "_(a graph was requested but its description was not valid JSON)_"
        try:
            uri = render(spec)
        except Exception:
            uri = None
        if not uri:
            return "_(a graph was requested but could not be drawn)_"
        drawn += 1
        caption = str(spec.get("title") or "Figure")
        return f"![{caption}]({uri})"

    return _FENCE.sub(swap, markdown), drawn
