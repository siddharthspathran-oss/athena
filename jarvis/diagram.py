"""Diagrams in study answers.

Asked for "the JK flip-flop with its circuit diagram" on 2 Oct 2026, the
answer had a heading "Circuit Diagram" and a paragraph describing one: a
language model cannot draw wires, and one that writes drawing code gets the
connections wrong. So circuits are not drawn per answer at all:

  ```circuit jk_flipflop```   a textbook drawing from jarvis/circuits - made
                              once with schemdraw and checked by eye; the
                              model only names it
  ```wavedrom {...}```        a timing diagram (WaveJSON)
  ```mermaid ...```           a state diagram, block diagram or flowchart

The last two are text the page's own scripts draw (render.py), so nothing
the model writes runs on this PC.
"""
from __future__ import annotations

import base64
import re
from pathlib import Path

DIR = Path(__file__).parent / "circuits"
TITLES = {
    "jk_flipflop": "JK flip-flop (NAND gates)",
    "t_flipflop": "T flip-flop (JK with J = K = T)",
    "d_flipflop": "D flip-flop (gated D latch, NAND gates)",
    "sr_flipflop": "Clocked SR flip-flop (NAND gates)",
    "sr_latch_nor": "SR latch (NOR gates)",
    "sr_latch_nand": "S̄R̄ latch (NAND gates)",
    "half_adder": "Half adder",
    "full_adder": "Full adder",
    "mux_2to1": "2-to-1 multiplexer",
    "rc_lowpass": "RC low-pass filter (1st order)",
    "rc_highpass": "RC high-pass filter (1st order)",
    "inverting_amp": "Inverting amplifier",
    "noninverting_amp": "Non-inverting amplifier",
    "voltage_follower": "Voltage follower (unity-gain buffer)",
    "sallen_key_lowpass": "Sallen-Key low-pass filter (unity gain, 2nd order)",
    "sallen_key_highpass": "Sallen-Key high-pass filter (unity gain, 2nd order)",
}
_ALIASES = {
    "jk": "jk_flipflop", "t": "t_flipflop", "toggle_flipflop": "t_flipflop",
    "d": "d_flipflop", "d_latch": "d_flipflop", "sr": "sr_flipflop", "rs_flipflop": "sr_flipflop",
    "sr_latch": "sr_latch_nor", "rs_latch": "sr_latch_nor", "nand_latch": "sr_latch_nand",
    "nor_latch": "sr_latch_nor",
    "low_pass": "rc_lowpass", "lowpass": "rc_lowpass", "rc_low_pass": "rc_lowpass", "high_pass": "rc_highpass",
    "highpass": "rc_highpass", "rc_high_pass": "rc_highpass", "inverting": "inverting_amp",
    "inverting_amplifier": "inverting_amp", "non_inverting_amp": "noninverting_amp",
    "non_inverting_amplifier": "noninverting_amp", "noninverting_amplifier": "noninverting_amp",
    "buffer": "voltage_follower", "unity_gain_buffer": "voltage_follower",
    "sallen_key": "sallen_key_lowpass", "sallen_key_low_pass": "sallen_key_lowpass",
    "butterworth": "sallen_key_lowpass", "butterworth_lowpass": "sallen_key_lowpass",
    "second_order_butterworth": "sallen_key_lowpass", "2nd_order_butterworth": "sallen_key_lowpass",
    "sallen_key_high_pass": "sallen_key_highpass", "butterworth_highpass": "sallen_key_highpass", "mux": "mux_2to1", "2_to_1_mux": "mux_2to1", "multiplexer": "mux_2to1",
}
_FENCE = re.compile(r"```circuit[ \t]*([^\n`]*)\n?(.*?)```", re.S | re.I)


def _key(name: str) -> str | None:
    k = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    k = k.replace("flip_flop", "flipflop").replace("_circuit", "").replace("_diagram", "")
    k = _ALIASES.get(k, k)
    return k if k in TITLES and (DIR / f"{k}.svg").is_file() else None


def _named(m: re.Match) -> str:
    return (m.group(1).strip() or m.group(2).strip()).split("\n")[0]


def problems(markdown: str) -> list[str]:
    """A wrong JK equation - told to the model so it can fix it. (A circuit with
    no stored drawing is no longer a fault: expand() finds one online.)"""
    return _eq_problems(markdown)


def expand(markdown: str) -> str:
    """Replace each ```circuit block with its drawing, embedded as a data URI."""
    def swap(m: re.Match) -> str:
        name = _named(m)
        k = _key(name)
        if not k:
            # Not in the library: the best drawing Wikimedia Commons or the web
            # has, checked by a model that can see it (figures.py).
            try:
                from . import figures
                f = figures.find(name)
            except Exception:
                f = None
            if not f:
                return f"_(I could not find a drawing of the {name} that passed the check.)_"
            credit = f"\n\n_Source: [{f['credit']}]({f['page']})_" if f.get("page") else ""
            return f"![Circuit: {name}]({f['uri']})" + credit
        uri = "data:image/svg+xml;base64," + base64.b64encode((DIR / f"{k}.svg").read_bytes()).decode()
        return f"![Circuit: {TITLES[k]}]({uri})"
    return _FENCE.sub(swap, markdown)


# ---------- timing diagrams: Q is worked out here, not taken from the model ----------
# The model draws J, K and CLK fine and then gets Q wrong edge by edge. A flip-flop's
# output follows from its inputs, so for JK / D / T / SR the Q (and Q̄) rows are
# recomputed: inputs sampled just before each active clock edge.
_WAVE = re.compile(r"(```wavedrom[ \t]*\n)(.*?)(```)", re.S | re.I)
_NEXT = {
    ("j", "k"): lambda q, j, k: q if (j, k) == (0, 0) else 0 if (j, k) == (0, 1) else 1 if (j, k) == (1, 0) else 1 - q,
    ("s", "r"): lambda q, s, r: q if (s, r) == (0, 0) else 0 if (s, r) == (0, 1) else 1 if (s, r) == (1, 0) else q,
    ("d",): lambda q, d: d,
    ("t",): lambda q, t: q ^ t,
}


def _levels(wave: str, n: int) -> list:
    out, cur = [], None
    for ch in wave:
        if ch != ".":
            cur = int(ch) if ch in "01" else 1 if ch in "hH" else 0 if ch in "lL" else None
        out.append(cur)
    return (out + [out[-1] if out else None] * n)[:n]


def _wave(levels: list[int]) -> str:
    return "".join("." if i and v == levels[i - 1] else str(v) for i, v in enumerate(levels))


def _edges(clk: str) -> list[int]:
    """Periods that begin with the active clock edge."""
    if clk[:1] in "pPnN":
        return list(range(1, len(clk)))
    lv = _levels(clk, len(clk))
    return [i for i in range(1, len(lv)) if lv[i - 1] == 0 and lv[i] == 1]


def fix_timing(markdown: str) -> str:
    import json

    def fix(m: re.Match) -> str:
        try:
            spec = json.loads(m.group(2))
            sig = {str(s.get("name", "")).strip().lower(): s for s in spec["signal"] if isinstance(s, dict)}
        except Exception:
            return m.group(0)       # not strict JSON: drawn as written
        clk = next((sig[k] for k in ("clk", "clock", "cp") if k in sig), None)
        q = sig.get("q")
        rule = next((r for r in _NEXT if all(n in sig for n in r)), None)
        if not (clk and q and rule):
            return m.group(0)
        n = len(clk.get("wave", ""))
        ins = [_levels(sig[k].get("wave", ""), n) for k in rule]
        start = _levels(q.get("wave", ""), n)[0]
        lv = [start if start in (0, 1) else 0]
        edges = set(_edges(clk["wave"]))
        for i in range(1, n):
            vals = [x[i - 1] for x in ins]
            lv.append(_NEXT[rule](lv[-1], *vals) if i in edges and None not in vals else lv[-1])
        q["wave"] = _wave(lv)
        for k in ("q̄", "q'", "qbar", "q_bar", "~q", "nq", "q̅"):
            if k in sig:
                sig[k]["wave"] = _wave([1 - v for v in lv])
        return m.group(1) + json.dumps(spec) + "\n" + m.group(3)
    return _WAVE.sub(fix, markdown)


# ---------- mermaid: labels quoted so punctuation cannot break the parse ----------
# "Input (Vin)", "R1 || C1", "f_c = 1/(2πRC)" inside an unquoted [label] or |label|
# is a mermaid syntax error - the page showed "Syntax error in text" (2 Oct 2026).
_MERMAID = re.compile(r"(```mermaid[ \t]*\n)(.*?)(```)", re.S | re.I)
_NODE = re.compile(r"(\b[A-Za-z][\w-]*)(?:\[(?!\")([^\]\n]+)\]|\((?!\")([^\n]+?)\)(?=\s|$|;|-|=)|\{(?!\")([^}\n]+)\})")
_EDGE = re.compile(r"\|(?!\")([^|\n]+)\|")


def _quote(label: str) -> str:
    return '"' + label.strip().replace('"', "'") + '"'


def _node(n: re.Match) -> str:
    for g, (o, c) in zip((2, 3, 4), ("[]", "()", "{}")):
        if n.group(g) is not None:
            return n.group(1) + o + _quote(n.group(g)) + c
    return n.group(0)


def fix_mermaid(markdown: str) -> str:
    def fix(m: re.Match) -> str:
        body = m.group(2)
        if re.match(r"\s*(?:flowchart|graph)\b", body, re.I):
            body = "\n".join(_EDGE.sub(lambda e: "|" + _quote(e.group(1)) + "|",
                                       _NODE.sub(_node, line))
                             for line in body.split("\n"))
        return m.group(1) + body + m.group(3)
    return _MERMAID.sub(fix, markdown)


# ---------- characteristic tables and equations ----------
# A flip-flop's table is fixed by definition, and the 7B study model still wrote
# J=0,K=1 -> 1 and J=1,K=0 -> 0 (2 Oct 2026). Tables are corrected in place;
# a wrong JK equation goes back to the model through problems().
def _norm(cell: str) -> str:
    c = re.sub(r"\\[()\[\]]|\$|\\mathrm|\\text|\s|[{}]", "", cell.lower())
    return c.replace("\\overline", "").replace("\\bar", "")


_CUR = {"qn", "q_n", "q(n)", "q(t)", "q_t", "q"}
_NXT = {"qn+1", "q_n+1", "q(n+1)", "q(t+1)", "q_t+1", "q+", "q*", "qnext", "q_next"}


def fix_tables(markdown: str) -> str:
    lines = markdown.split("\n")
    i = 0
    while i < len(lines):
        if not lines[i].strip().startswith("|"):
            i += 1
            continue
        j = i
        while j < len(lines) and lines[j].strip().startswith("|"):
            j += 1
        rows = [[c.strip() for c in l.strip().strip("|").split("|")] for l in lines[i:j]]
        head = [_norm(c) for c in rows[0]]
        rule = next((r for r in _NEXT if all(n in head for n in r)), None)
        nxt = next((k for k, h in enumerate(head) if h in _NXT), None)
        cur = next((k for k, h in enumerate(head) if h in _CUR and k != nxt), None)
        if rule and nxt is not None and cur is not None:
            idx = [head.index(n) for n in rule]
            for r, row in enumerate(rows[1:], start=i + 1):
                try:
                    vals = [int(row[k]) for k in idx] + [int(row[cur])]
                    have = int(row[nxt])
                except (ValueError, IndexError):
                    continue
                if rule == ("s", "r") and vals[:2] == [1, 1]:
                    continue            # S = R = 1 is not allowed; left as written
                want = _NEXT[rule](vals[-1], *vals[:-1])
                if have != want:
                    row[nxt] = str(want)
                    lines[r] = "| " + " | ".join(row) + " |"
        i = j
    return "\n".join(lines)


_JK_OK = {"jqn'+k'qn", "k'qn+jqn'", "jq'+k'q", "k'q+jq'"}


def _eq_problems(markdown: str) -> list[str]:
    out = []
    for m in re.finditer(r"\\\((.*?)\\\)|\\\[(.*?)\\\]|\$\$(.*?)\$\$", markdown, re.S):
        s = (m.group(1) or m.group(2) or m.group(3) or "").replace(" ", "").replace("\\cdot", "").replace("\\,", "")
        s = re.sub(r"\\(?:overline|bar)\{([^}]*)\}", r"\1'", s)
        s = re.sub(r"Q_\{?n\}?|Q\(n\)", "Qn", s).replace("{", "").replace("}", "")
        mm = re.match(r"Q_?\(?n\+1\)?=(.*)$", s)
        if mm and "J" in mm.group(1) and "K" in mm.group(1) and mm.group(1).lower() not in _JK_OK:
            out.append("the JK characteristic equation is wrong. It is "
                       "\\(Q_{n+1} = J\\overline{Q_n} + \\overline{K}Q_n\\) - fix it everywhere it appears")
            break
    return out


if __name__ == "__main__":
    fm = fix_mermaid('```mermaid\nflowchart LR\n  A[Input (Vin)] -->|R1 (10k)| B(Op-amp +)\n  B --> C["ok"]\n```')
    assert 'A["Input (Vin)"] -->|"R1 (10k)"| B("Op-amp +")' in fm and 'C["ok"]' in fm, fm
    bad = "| J | K | \\(Q_n\\) | \\(Q_{n+1}\\) |\n|---|---|---|---|\n| 0 | 1 | 0 | 1 |\n| 1 | 0 | 0 | 0 |\n| 1 | 1 | 1 | 0 |"
    good = fix_tables(bad)
    assert "| 0 | 1 | 0 | 0 |" in good and "| 1 | 0 | 0 | 1 |" in good and "| 1 | 1 | 1 | 0 |" in good, good
    assert _eq_problems(r"\[Q_{n+1} = J\overline{Q_n} + K\overline{Q_n}\]")
    assert not _eq_problems(r"\[Q_{n+1} = J\overline{Q_n} + \overline{K}Q_n\]")
    # J=1,K=0 sets; J=K=1 toggles; J=0,K=1 resets (inputs read in the period before each edge)
    w = fix_timing('```wavedrom\n{"signal":[{"name":"CLK","wave":"p....."},{"name":"J","wave":"1.1.0."},'
                   '{"name":"K","wave":"0.1.1."},{"name":"Q","wave":"0.1010"}]}\n```')
    assert '"wave": "01.010"' in w, w
    assert fix_timing("```wavedrom\n{signal: [nope]}\n```").startswith("```wavedrom")
    assert _key("JK flip-flop") == "jk_flipflop" and _key("jk") == "jk_flipflop"
    assert _key("full adder") == "full_adder" and _key("warp drive") is None
    out = expand("a\n```circuit jk_flipflop```\nb")
    assert "data:image/svg+xml;base64," in out and "```" not in out
    assert _key("d flip flop") == "d_flipflop" and not problems("```circuit flux_capacitor```")
    print("ok")
