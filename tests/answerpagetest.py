"""Do study answers turn into correct pages? Every case is a way one broke.

Run: python tests/answerpagetest.py

2 Oct 2026: the JK flip-flop answer had no circuit, a wrong truth table and an
equation with its bars dropped; the 2nd-order Butterworth answer came out as
raw LaTeX, a mermaid "Syntax error" bomb and "a graph was requested but could
not be drawn". Each of those is pinned here so it cannot come back unnoticed.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis import diagram, plot, render        # noqa: E402

fails = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global fails
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"\n     {detail[:300]}"))
    fails += not ok


# |H(jw)| at the start of a line inside \[ \] must stay maths, not become a table
butter = ("A Butterworth filter of order n:\n\\[\n|H(j\\omega)| = \\frac{1}{\\sqrt{1+(\\omega/\\omega_c)^{4}}}"
          " \\tag{1}\n\\]\nwhere\n- \\(\\omega\\) is the angular frequency\n\n"
          "| Item | Value |\n|---|---|\n| magnitude | \\(|H(j\\omega)|\\) |")
h = render.to_html_body(butter)
check("display maths with |...| survives", "<table><tr><th>Item" in h and h.count("<table>") == 1
      and "\\frac{1}{\\sqrt{1+(\\omega/\\omega_c)^{4}}}" in h, h)
check("|...| inside a table cell stays in one cell", "<td>\\(|H(j\\omega)|\\)</td>" in h, h)

# x^4 in a plot is a power
check("plot with x^4 draws", not plot.problems(
    '```plot\n{"xmin":0.01,"xmax":100,"xscale":"log","series":[{"expr":"20*log10(1/sqrt(1+x^4))"}]}\n```'))

# mermaid labels with brackets get quoted
m = diagram.fix_mermaid('```mermaid\nflowchart LR\n  A[Vin (input)] -->|R1 (10k)| B[Op-amp +]\n```')
check("mermaid labels quoted", 'A["Vin (input)"] -->|"R1 (10k)"| B["Op-amp +"]' in m, m)

# circuits by name, including the names people use
for name in ("JK flip-flop", "2nd order Butterworth", "Sallen-Key", "full adder", "non-inverting amplifier"):
    check(f"circuit drawing: {name}", "data:image/svg+xml;base64," in diagram.expand(f"```circuit {name}```"))

# flip-flop tables are corrected; a wrong JK equation is reported
t = diagram.fix_tables("| J | K | Qn | Qn+1 |\n|---|---|---|---|\n| 0 | 1 | 0 | 1 |\n| 1 | 0 | 0 | 0 |")
check("JK truth table corrected", "| 0 | 1 | 0 | 0 |" in t and "| 1 | 0 | 0 | 1 |" in t, t)
check("wrong JK equation caught", bool(diagram.problems(r"\[Q_{n+1} = J\overline{Q_n} + K\overline{Q_n}\]")))
check("right JK equation left alone", not diagram.problems(r"\[Q_{n+1} = J\overline{Q_n} + \overline{K}Q_n\]"))

# bars typed as characters become \overline inside maths
check("Q-bar in maths kept", r"\overline{Q}" in render.bars_in_math("\\(J Q\u0304\\)"))

# a whole page renders without throwing
p = render.write_page("answer page test", butter + "\n\n```circuit jk_flipflop```\n\n```mermaid\nflowchart LR\n A[x (1)] --> B\n```",
                      open_it=False)
page = p.read_text(encoding="utf-8")
p.unlink()
check("full page has circuit, mermaid script and fallback", "Circuit: JK" in page and "mermaid.parse" in page)

print(f"\n{'all passed' if not fails else str(fails) + ' FAILED'}")
sys.exit(1 if fails else 0)
