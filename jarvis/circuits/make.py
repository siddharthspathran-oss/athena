"""Athena's circuit library (jarvis/circuits/*.svg), drawn with schemdraw.
Rebuild: pip install schemdraw, then  python make.py <outdir> [png]  and check every drawing by eye."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "sdlib"))
import schemdraw
import schemdraw.elements as elm
import schemdraw.logic as logic

OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
PNG = len(sys.argv) > 2
schemdraw.use("matplotlib" if PNG else "svg")
schemdraw.config(fontsize=13, lw=1.6)


def wire(d, a, b, shape="-"):
    d += elm.Wire(shape).at(a).to(b)


def poly(d, *pts):
    for a, b in zip(pts, pts[1:]):
        d += elm.Line().at(a).to(b)


def dot(d, p):
    d += elm.Dot(radius=0.07).at(p)


def label(d, p, text, loc="left"):
    d += elm.Label().at(p).label(text, loc=loc)


def save(d, name):
    d.save(str(OUT / f"{name}.{'png' if PNG else 'svg'}"), **({"dpi": 110} if PNG else {}))


def latch_pair(d, top_in, bot_in, x0=6.0, y_top=3.0, y_bot=0.0, gate=logic.Nand, outs=("Q", "Q̄")):
    """Cross-coupled pair; top_in/bot_in are the points feeding in1 of each gate.
    Returns (top gate, bottom gate)."""
    g3 = gate(inputs=2).at((x0, y_top)).anchor("in1")
    g4 = gate(inputs=2).at((x0, y_bot)).anchor("in2")
    d += g3; d += g4
    wire(d, top_in, g3.in1, "-|") if top_in[1] != g3.in1[1] else wire(d, top_in, g3.in1)
    wire(d, bot_in, g4.in2, "-|") if bot_in[1] != g4.in2[1] else wire(d, bot_in, g4.in2)
    # outputs
    qx = g3.out[0] + 1.6
    d += elm.Line().at(g3.out).to((qx, g3.out[1]))
    d += elm.Line().at(g4.out).to((qx, g4.out[1]))
    label(d, (qx, g3.out[1]), outs[0], "right"); label(d, (qx, g4.out[1]), outs[1], "right")
    # cross coupling: Q -> g4.in1, Q̄ -> g3.in2 (the textbook X)
    tap1 = (g3.out[0] + 0.5, g3.out[1]); tap2 = (g4.out[0] + 0.5, g4.out[1])
    dot(d, tap1); dot(d, tap2)
    mid = (g3.in2[1] + g4.in1[1]) / 2
    poly(d, tap1, (tap1[0], mid + 0.3), (x0 - 0.5, mid - 0.3), (x0 - 0.5, g4.in1[1]), g4.in1)
    poly(d, tap2, (tap2[0], mid - 0.3), (x0 - 0.9, mid + 0.3), (x0 - 0.9, g3.in2[1]), g3.in2)
    return g3, g4, qx


def jk_flipflop(name="jk_flipflop", toggle=False):
    with schemdraw.Drawing(show=False) as d:
        g1 = logic.Nand(inputs=3).at((2.5, 3.0)).anchor("in2"); d += g1
        g2 = logic.Nand(inputs=3).at((2.5, 0.0)).anchor("in2"); d += g2
        g3, g4, qx = latch_pair(d, g1.out, g2.out, x0=7.0, y_top=g1.out[1] + 0.0, y_bot=g2.out[1])
        # inputs
        if toggle:      # T flip-flop: J and K tied together
            tx = 0.5
            poly(d, g1.in2, (tx, g1.in2[1]), (tx, g2.in2[1]), g2.in2)
            t = (tx, g1.in2[1] + (g2.in2[1] - g1.in2[1]) * 0.15); dot(d, t)
            poly(d, t, (0, t[1])); label(d, (0, t[1]), "T")
        else:
            d += elm.Line().at(g1.in2).to((0, g1.in2[1])); label(d, (0, g1.in2[1]), "J")
            d += elm.Line().at(g2.in2).to((0, g2.in2[1])); label(d, (0, g2.in2[1]), "K")
        clk_x = 0.9
        d += elm.Line().at(g1.in3).to((clk_x, g1.in3[1]))
        d += elm.Line().at(g2.in1).to((clk_x, g2.in1[1]))
        d += elm.Line().at((clk_x, g1.in3[1])).to((clk_x, g2.in1[1]))
        mid = (clk_x, (g1.in3[1] + g2.in1[1]) / 2)
        dot(d, mid); d += elm.Line().at(mid).to((0, mid[1])); label(d, (0, mid[1]), "CLK")
        # feedback, as drawn in textbooks: Q down and round to K's gate, Q̄ up and round to J's gate
        top = g1.in1[1] + 1.0; bot = g2.in3[1] - 1.0
        q_tap = (qx - 0.3, g3.out[1]); qb_tap = (qx - 0.7, g4.out[1])
        dot(d, q_tap); dot(d, qb_tap)
        poly(d, q_tap, (q_tap[0], bot), (g2.in3[0] - 0.5, bot), (g2.in3[0] - 0.5, g2.in3[1]), g2.in3)
        poly(d, qb_tap, (qb_tap[0], top), (g1.in1[0] - 0.5, top), (g1.in1[0] - 0.5, g1.in1[1]), g1.in1)
        save(d, name)


def sr_latch_nor():
    with schemdraw.Drawing(show=False) as d:
        g3, g4, qx = latch_pair(d, (2.0, 3.0), (2.0, 0.0), x0=3.5, y_top=3.0, y_bot=0.0, gate=logic.Nor)
        label(d, (2.0, 3.0), "R"); label(d, (2.0, 0.0), "S")
        save(d, "sr_latch_nor")


def sr_latch_nand():
    with schemdraw.Drawing(show=False) as d:
        g3, g4, qx = latch_pair(d, (2.0, 3.0), (2.0, 0.0), x0=3.5, y_top=3.0, y_bot=0.0)
        label(d, (2.0, 3.0), "S̄"); label(d, (2.0, 0.0), "R̄")
        save(d, "sr_latch_nand")


def gated_pair(name, top_lab, bot_lab, d_type=False):
    with schemdraw.Drawing(show=False) as d:
        gx = 4.2 if d_type else 2.5         # room for the inverter under a D input
        g1 = logic.Nand(inputs=2).at((gx, 3.0)).anchor("in1"); d += g1
        g2 = logic.Nand(inputs=2).at((gx, 0.0)).anchor("in2"); d += g2
        latch_pair(d, g1.out, g2.out, x0=gx + 3.5, y_top=g1.out[1], y_bot=g2.out[1])
        clk_x = gx - 1.1
        d += elm.Line().at(g1.in2).to((clk_x, g1.in2[1])); d += elm.Line().at(g2.in1).to((clk_x, g2.in1[1]))
        d += elm.Line().at((clk_x, g1.in2[1])).to((clk_x, g2.in1[1]))
        mid = (clk_x, (g1.in2[1] + g2.in1[1]) / 2); dot(d, mid)
        d += elm.Line().at(mid).to((0, mid[1])); label(d, (0, mid[1]), "CLK")
        d += elm.Line().at(g1.in1).to((0, g1.in1[1])); label(d, (0, g1.in1[1]), top_lab)
        if d_type:
            # D -> top; D̄ (through a NOT) -> bottom
            tap = (0.6, g1.in1[1]); dot(d, tap)
            d += elm.Line().at(tap).to((0.6, g2.in2[1] - 1.2))
            n = logic.Not(scale=0.55).at((0.6, g2.in2[1] - 1.2)).right(); d += n
            poly(d, n.out, (g2.in2[0] - 0.5, n.out[1]), (g2.in2[0] - 0.5, g2.in2[1]), g2.in2)
        else:
            d += elm.Line().at(g2.in2).to((0, g2.in2[1])); label(d, (0, g2.in2[1]), bot_lab)
        save(d, name)


def symbol(name, pins_left, title, toggle_bar=True):
    with schemdraw.Drawing(show=False) as d:
        ic = elm.Ic(pins=[elm.IcPin(name=p, side="left", **({"invert": False, "anchorname": p} if p != ">" else {})) for p in pins_left]
                    + [elm.IcPin(name="Q", side="right"), elm.IcPin(name="Q̄", side="right")],
                    edgepadW=1.4, pinspacing=1.2, label=title)
        d += ic
        save(d, name)


def half_adder():
    with schemdraw.Drawing(show=False) as d:
        x = logic.Xor().at((2.5, 2.0)).anchor("in1"); d += x
        a = logic.And().at((2.5, 0.0)).anchor("in1"); d += a
        d += elm.Line().at(x.in1).to((0, x.in1[1])); label(d, (0, x.in1[1]), "A")
        d += elm.Line().at(x.in2).to((0.9, x.in2[1]))
        d += elm.Line().at(a.in2).to((0, a.in2[1])); label(d, (0, a.in2[1]), "B")
        tA = (0.6, x.in1[1]); tB = (0.9, a.in2[1]); dot(d, tA); dot(d, tB)
        poly(d, tA, (0.6, a.in1[1]), a.in1)
        d += elm.Line().at(tB).to((0.9, x.in2[1]))
        d += elm.Line().at(x.out).to((x.out[0] + 1, x.out[1])); label(d, (x.out[0] + 1, x.out[1]), "Sum", "right")
        d += elm.Line().at(a.out).to((a.out[0] + 1, a.out[1])); label(d, (a.out[0] + 1, a.out[1]), "Carry", "right")
        save(d, "half_adder")


def full_adder():
    with schemdraw.Drawing(show=False) as d:
        x1 = logic.Xor().at((2.5, 4.0)).anchor("in1"); d += x1
        x2 = logic.Xor().at((6.5, 3.6)).anchor("in1"); d += x2
        a1 = logic.And().at((6.5, 1.6)).anchor("in1"); d += a1
        a2 = logic.And().at((6.5, -0.4)).anchor("in1"); d += a2
        o = logic.Or().at((10.0, 0.9)).anchor("in1"); d += o
        for p, lab in ((x1.in1, "A"), (x1.in2, "B")):
            d += elm.Line().at(p).to((0, p[1])); label(d, (0, p[1]), lab)
        cin_y = 2.7
        d += elm.Line().at((0, cin_y)).to((5.2, cin_y)); label(d, (0, cin_y), "Cin")
        # x1 out -> x2.in1 and a1.in1
        t = (x1.out[0] + 0.6, x1.out[1]); dot(d, t)
        d += elm.Line().at(x1.out).to(t); poly(d, t, (t[0], x2.in1[1]), x2.in1)
        poly(d, t, (t[0], a1.in1[1]), a1.in1)
        # Cin -> x2.in2 and a1.in2
        c = (5.2, cin_y); dot(d, c)
        poly(d, c, (5.2, x2.in2[1]), x2.in2)
        poly(d, c, (5.2, a1.in2[1]), a1.in2)
        # A, B -> a2
        ta = (1.0, x1.in1[1]); tb = (1.4, x1.in2[1]); dot(d, ta); dot(d, tb)
        poly(d, ta, (1.0, a2.in1[1]), a2.in1)
        poly(d, tb, (1.4, a2.in2[1]), a2.in2)
        poly(d, a1.out, (o.in1[0] - 0.5, a1.out[1]), (o.in1[0] - 0.5, o.in1[1]), o.in1)
        poly(d, a2.out, (o.in2[0] - 0.5, a2.out[1]), (o.in2[0] - 0.5, o.in2[1]), o.in2)
        d += elm.Line().at(x2.out).to((o.out[0] + 1, x2.out[1])); label(d, (o.out[0] + 1, x2.out[1]), "Sum", "right")
        d += elm.Line().at(o.out).to((o.out[0] + 1, o.out[1])); label(d, (o.out[0] + 1, o.out[1]), "Cout", "right")
        save(d, "full_adder")


def mux_2to1():
    with schemdraw.Drawing(show=False) as d:
        a1 = logic.And().at((3.0, 2.0)).anchor("in1"); d += a1
        a2 = logic.And().at((3.0, 0.0)).anchor("in1"); d += a2
        o = logic.Or().at((6.5, 1.0)).anchor("in1"); d += o
        d += elm.Line().at(a1.in1).to((0, a1.in1[1])); label(d, (0, a1.in1[1]), "I0")
        d += elm.Line().at(a2.in1).to((0, a2.in1[1])); label(d, (0, a2.in1[1]), "I1")
        s_y = -1.4
        d += elm.Line().at((0, s_y)).to((2.4, s_y)); label(d, (0, s_y), "S")
        t = (0.6, s_y); dot(d, t)
        poly(d, (2.4, s_y), (2.4, a2.in2[1]), a2.in2)
        d += elm.Line().at(t).to((0.6, a1.in2[1]))
        nn = logic.Not(scale=0.7).at((0.6, a1.in2[1])).right(); d += nn
        d += elm.Line().at(nn.out).to(a1.in2)
        poly(d, a1.out, (o.in1[0] - 0.4, a1.out[1]), (o.in1[0] - 0.4, o.in1[1]), o.in1)
        poly(d, a2.out, (o.in2[0] - 0.4, a2.out[1]), (o.in2[0] - 0.4, o.in2[1]), o.in2)
        d += elm.Line().at(o.out).to((o.out[0] + 1, o.out[1])); label(d, (o.out[0] + 1, o.out[1]), "Y", "right")
        save(d, "mux_2to1")


def rc(name, first, second, title_in="Vin", title_out="Vout"):
    with schemdraw.Drawing(show=False) as d:
        d += elm.Dot(open=True).label(title_in, "left")
        d += first().right().label(first.__name__[0] if False else ("R" if first is elm.Resistor else "C"))
        d += elm.Dot()
        d.push()
        d += second().down().label("C" if second is elm.Capacitor else "R", loc="bottom")
        d += elm.Ground()
        d.pop()
        d += elm.Line().right(1.5)
        d += elm.Dot(open=True).label(title_out, "right")
        save(d, name)


def inverting_amp():
    with schemdraw.Drawing(show=False) as d:
        op = elm.Opamp(leads=True); d += op
        d += elm.Line().at(op.in1).left(0.8)
        d += (j := elm.Dot())
        d += elm.Resistor().left().label("Rin"); d += elm.Dot(open=True).label("Vin", "left")
        d += elm.Line().at(j.center).up(1.6)
        d += elm.Resistor().tox(op.out).label("Rf")
        d += elm.Line().toy(op.out); d += elm.Dot()
        d += elm.Line().at(op.out).right(1); d += elm.Dot(open=True).label("Vout", "right")
        d += elm.Line().at(op.in2).left(0.6); d += elm.Ground()
        save(d, "inverting_amp")


def noninverting_amp():
    with schemdraw.Drawing(show=False) as d:
        op = elm.Opamp(leads=True); d += op
        d += elm.Line().at(op.in2).left(4.2); d += elm.Dot(open=True).label("Vin", "left")
        d += elm.Line().at(op.in1).left(0.8)
        d += (j := elm.Dot())
        d += elm.Resistor().left().label("R1"); d += elm.Ground()
        d += elm.Line().at(j.center).up(1.6)
        d += elm.Resistor().tox(op.out).label("Rf")
        d += elm.Line().toy(op.out); d += elm.Dot()
        d += elm.Line().at(op.out).right(1); d += elm.Dot(open=True).label("Vout", "right")
        save(d, "noninverting_amp")


def voltage_follower():
    with schemdraw.Drawing(show=False) as d:
        op = elm.Opamp(leads=True); d += op
        d += elm.Line().at(op.in2).left(1.2); d += elm.Dot(open=True).label("Vin", "left")
        d += elm.Line().at(op.in1).left(0.6)
        d += elm.Line().up(1.6)
        d += elm.Line().tox(op.out.x + 0.5 if hasattr(op.out, "x") else op.out[0] + 0.5)
        d += elm.Line().toy(op.out); d += elm.Dot()
        d += elm.Line().at(op.out).right(1.2); d += elm.Dot(open=True).label("Vout", "right")
        save(d, "voltage_follower")


def sallen_key(name, low=True):
    """Unity-gain Sallen-Key: low-pass = R, R in series with C1 feedback and C2 to ground."""
    A, B = (elm.Resistor, elm.Capacitor) if low else (elm.Capacitor, elm.Resistor)
    n1, n2 = ("R1", "R2") if low else ("C1", "C2")
    m1, m2 = ("C1", "C2") if low else ("R1", "R2")
    with schemdraw.Drawing(show=False) as d:
        d += elm.Dot(open=True).label("Vin", "left")
        d += A().right().label(n1)
        d += (a := elm.Dot())
        d += A().right().label(n2)
        d += (b := elm.Dot())
        d += elm.Line().right(1.0)
        op = elm.Opamp(leads=True).anchor("in2"); d += op
        d += B().at(b.center).down().label(m2, loc="bottom"); d += elm.Ground()
        # unity-gain: output to the inverting input
        d += elm.Line().at(op.in1).left(0.5)
        d += elm.Line().up(1.0)
        d += (t := elm.Line().tox(op.out[0] + 0.6))
        d += elm.Line().toy(op.out); d += elm.Dot()
        d += elm.Line().at(op.out).right(1.2); d += elm.Dot(open=True).label("Vout", "right")
        # the feedback element from the middle node to the output
        top = op.in1[1] + 2.4
        d += elm.Line().at(a.center).toy(top)
        d += B().right().tox(op.out[0] + 0.6).label(m1)
        d += elm.Line().toy(op.in1[1] + 1.0)
        save(d, name)


jk_flipflop(); jk_flipflop("t_flipflop", toggle=True); sr_latch_nor(); sr_latch_nand()
gated_pair("sr_flipflop", "S", "R"); gated_pair("d_flipflop", "D", "", d_type=True)
half_adder(); full_adder(); mux_2to1()
rc("rc_lowpass", elm.Resistor, elm.Capacitor); rc("rc_highpass", elm.Capacitor, elm.Resistor)
inverting_amp(); noninverting_amp(); voltage_follower()
sallen_key("sallen_key_lowpass", True); sallen_key("sallen_key_highpass", False)
print("done")
