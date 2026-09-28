"""Does the arithmetic checker catch mistakes without inventing them?

Run: python tests/verifytest.py

The second half matters more than the first. A checker that complains about a
correct answer is worse than no checker: it sends good work back to be redone,
and it puts a warning on a page that did not deserve one. Every CLEAN case
below is a line taken from an answer that was right.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis import plot, verify        # noqa: E402

# (name, markdown, should_complain)
CASES = [
    # ---- real mistakes, taken from the MATH2065 worksheet answers ----
    ("power slip",
     r"\[ q^9 = \left( \frac{4}{5} \right)^9 \approx 0.214748 \]", True),
    ("long sum",
     r"\[ \mu = \frac{0 + 7 + 120 + 594 + 1720 + 3655 + 5688 + 5929 + 4288 "
     r"+ 2313 + 710 + 121 + 0}{4096} = \frac{22806}{4096} \approx 5.57 \]", True),
    ("integer sum, out by one part in a thousand",
     r"\[ \frac{1250000000 - 937500000 + 187500000}{3} = \frac{499500000}{3} \]", True),
    ("division slip",
     r"\[ Z = \frac{179.5 - 190}{3.08} \approx -3.57 \]", True),
    ("negative probability in the conclusion",
     r"In short, the probabilities are \( P(X < 2) = 3 \) and "
     r"\( P(1 < X \leq 2) = 4 \).", True),
    ("probability above one in the conclusion",
     r"Final Answer: \[ P(A|B) = 1.4 \]", True),
    ("impossible result stated in prose, not as an equation",
     r"In short, the value of \( c \) is \( \frac{1}{3} \), and the "
     r"conditional probability is \( \frac{4}{3} \).", True),

    # ---- correct working that must not be complained about ----
    ("exact fractions", r"\[ P(D) = 0.0005 + 0.0005 + 0.002 = 0.003 \]", False),
    ("rounded division",
     r"\[ P(A_U|D) = \frac{0.0005}{0.003} = \frac{1}{6} \approx 0.1667 \]", False),
    ("rounding at two decimals",
     r"\[ \sigma = \sqrt{3.12} \approx 1.77 \]", False),
    ("theoretical sd", r"\[ \sigma = \sqrt{12 \cdot 0.5 \cdot 0.5} = \sqrt{3} "
     r"\approx 1.73 \]", False),
    ("correct alpha", r"\[ \alpha = \frac{0.46}{3.5} = 0.1314 \]", False),
    ("implicit multiplication",
     r"\[ p(0) = 3\left(\frac{1}{3}\right)^3 = \frac{1}{9} \]", False),
    ("a genuine inequality is not an equation",
     r"\[ F(x) \geq 1/3 \]", False),
    ("symbols are left alone",
     r"\[ \int_{0}^{50} 10000t^2 \, dt = 10000 \left[ \frac{t^3}{3} \right]_0^{50} \]",
     False),
    ("a sum is left alone",
     r"\[ P(X < 180) = \sum_{k=0}^{179} \binom{200}{k} (0.95)^k (0.05)^{200-k} \]",
     False),
    ("a rejected candidate root is working, not a conclusion",
     r"Try \( c = 1 \): \[ p(1) = 4(1) - 10(1)^2 = -6 \] A probability cannot "
     r"be negative, so \( c = 1 \) is discarded.\n\nIn short, \( c = 1/3 \) and "
     r"\( p(1) = 2/9 \).", False),
    ("percentages are not evaluated", r"\[ P = 0.02\% \]", False),
    ("a prose result that is a perfectly good probability",
     r"In short, the probability that a defective transistor was manufactured "
     r"in the US is \( \frac{1}{6} \).", False),
    ("prose near a count, not a probability value",
     r"In short, the probability that a packet of \( 200 \) seeds violates the "
     r"guarantee is \( 0.0003 \).", False),
    ("cases blocks are left alone",
     r"\[ F(x) = \begin{cases} 0 & x < 0 \\ 1 & x \geq 2 \end{cases} \]", False),
    # 23 Sep worksheet, questions 3 and 8: separate statements in one span
    # were read as one chain and a correct answer was sent back.
    ("aligned rows are separate statements",
     r"\[ \begin{aligned} P(D|U)P(U) &= 0.0005 \quad (\text{already computed})"
     r"\\[4pt] P(D|C)P(C) &= 0.002 \times \frac14 = 0.0005,\\[4pt] P(D|M)P(M) "
     r"&= 0.004 \times \frac12 = 0.004 \times 0.5 = 0.002. \end{aligned} \]", False),
    ("two results side by side",
     r"\[ \mu = np_f = 200(0.05)=10,\qquad \sigma = \sqrt{np_f(1-p_f)} = "
     r"\sqrt{200\cdot0.05\cdot0.95}= \sqrt{9.5}\approx3.082. \]", False),
    ("each candidate root has its own p(1)",
     r"For \( c = 1 \), \( p(1) = -6 \); for \( c = 1/3 \), \( p(1) = 2/9 \).",
     False),
    ("a chain continued on the next aligned row",
     r"\[ \begin{aligned} I(50) &= \frac{1250000000}{3} - 312500000 + 62500000"
     r"\\ &= \frac{500000000}{3} \end{aligned} \]", False),
    ("different quantities in consecutive displays",
     r"\[ E[X] = \frac{14}{9} \] \[ E[X^2] = \frac{26}{9} \]", False),

    ("a wrong step on a continued aligned row",
     r"\[ \begin{aligned} S &= 7 + 120 + 594 \\ &= 731 \end{aligned} \]", True),
    # The same 23 Sep page, question 6: the sum and its total in two displays,
    # each consistent on its own. 7 + 120 + ... + 121 is 25145, not 22354.
    ("a long sum split across two displays",
     r"\[ \bar{x} = \frac{0 + 7 + 120 + 594 + 1720 + 3655 + 5688 + 5929 + 4288"
     r" + 2313 + 710 + 121 + 0}{4096} \] \[ \bar{x} = \frac{22354}{4096} "
     r"\approx 5.45 \]", True),
    ("a display continued with a bare equals sign",
     r"\[ \sum x f(x) = 0 + 7 + 120 + 594 + 1720 + 3655 + 5688 + 5929 + 4288"
     r" + 2313 + 710 + 121 + 0 \] so \[ = 22806 \]", True),
]

PLOT_CASES = [
    ("good spec", '```plot\n{"xmin":0,"xmax":3,"series":[{"expr":"sin(x)"}]}\n```', False),
    ("undefined symbol", '```plot\n{"xmin":0,"xmax":3,"series":[{"expr":"A*x"}]}\n```', True),
    ("broken json", '```plot\n{"xmin":0,"xmax":3,\n```', True),
    ("no series", '```plot\n{"xmin":0,"xmax":3}\n```', True),
    ("empty range", '```plot\n{"xmin":2,"xmax":2,"series":[{"expr":"x"}]}\n```', True),
    ("step function", '```plot\n{"xmin":-1,"xmax":3,"series":'
     '[{"expr":"0.11*heaviside(x)+0.22*heaviside(x-1)"}]}\n```', False),
    # The Q2 graph from 24 Sep 2026: F(x) written correctly as 1/9, 1/3, 1,
    # then plotted with jumps 1/9, 1/9, 2/9 - it ends at 0.44.
    ("CDF that never reaches 1", '```plot\n{"title":"Cumulative distribution function of X",'
     '"ylabel":"F(x)","xmin":-1,"xmax":3,"series":[{"expr":"0","label":"x<0"},'
     '{"expr":"0.1111111111*heaviside(x) + 0.1111111111*heaviside(x-1) + '
     '0.2222222222*heaviside(x-2)"}]}\n```', True),
    ("CDF with the right jumps", '```plot\n{"title":"CDF of X","ylabel":"F(x)",'
     '"xmin":-1,"xmax":3,"series":[{"expr":"heaviside(x)/9 + 2*heaviside(x-1)/9 + '
     '2*heaviside(x-2)/3"}]}\n```', False),
    ("CDF that falls", '```plot\n{"title":"CDF","xmin":-1,"xmax":3,"series":'
     '[{"expr":"0.5*heaviside(x) - 0.2*heaviside(x-1) + 0.7*heaviside(x-2)"}]}\n```', True),
]


def main() -> int:
    failures = 0
    for name, text, should in CASES + PLOT_CASES:
        found = verify.problems(text) + plot.problems(text)
        got = bool(found)
        ok = got == should
        if not ok:
            failures += 1
        mark = "ok  " if ok else "FAIL"
        want = "complain" if should else "stay quiet"
        print(f"{mark} {name:46} (should {want})")
        if not ok:
            for f in found:
                print(f"       got: {f}")
    print(f"\n{len(CASES) + len(PLOT_CASES)} cases, {failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
