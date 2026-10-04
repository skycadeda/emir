"""Draw tiny.gds, the magic route's test cell (tests/test_magic.py), from scratch.

    python make_tiny.py tiny.gds

One NMOS (W = 2 um, L = 0.5 um): its drain on an `out` met1 strap, its gate
`g` labelled on li1, its source on a met1 rail `vss` labelled at both ends (two
pins of one port, which the DSPF ties), its body through a substrate tap under
the rail. Synthetic: every coordinate is chosen here, on SkyWater SKY130's
public GDS layer numbers (sky130A.lyp).
"""
import sys

import klayout.db as db

LAYER = {"diff": (65, 20), "tap": (65, 44), "poly": (66, 20), "licon": (66, 44),
         "li1": (67, 20), "mcon": (67, 44), "met1": (68, 20), "nsdm": (93, 44),
         "psdm": (94, 20), "npc": (95, 20), "met1.label": (68, 5), "li1.label": (67, 5)}


def make(path):
    ly = db.Layout()
    ly.dbu = 0.001
    top = ly.create_cell("tiny")
    idx = {n: ly.layer(*v) for n, v in LAYER.items()}
    u = lambda v: int(round(v * 1000))

    def box(layer, x0, y0, x1, y1):
        top.shapes(idx[layer]).insert(db.Box(u(x0), u(y0), u(x1), u(y1)))

    def text(layer, name, x, y):
        top.shapes(idx[layer]).insert(db.Text(name, db.Trans(db.Vector(u(x), u(y)))))
    w, gl, sd = 2.0, 0.5, 0.44
    box("diff", 0, 0, 2 * sd + gl, w)
    box("nsdm", -0.125, -0.125, 2 * sd + gl + 0.125, w + 0.125)
    box("poly", sd, -0.13, sd + gl, w + 0.6)
    box("npc", sd + gl / 2 - 0.185, w + 0.2, sd + gl / 2 + 0.185, w + 0.6)
    box("licon", sd + gl / 2 - 0.085, w + 0.3, sd + gl / 2 + 0.085, w + 0.47)
    box("li1", sd + gl / 2 - 0.16, w + 0.25, sd + gl / 2 + 0.16, w + 0.52)
    text("li1.label", "g", sd + gl / 2, w + 0.38)
    for xc, net in ((sd / 2, "vss"), (sd + gl + sd / 2, "out")):
        y = 0.2
        while y + 0.17 <= w - 0.2:
            box("licon", xc - 0.085, y, xc + 0.085, y + 0.17)
            box("mcon", xc - 0.085, y, xc + 0.085, y + 0.17)
            y += 0.36
        box("li1", xc - 0.1, 0.1, xc + 0.1, w - 0.1)
        if net == "vss":
            box("met1", xc - 0.13, -1.4, xc + 0.13, w - 0.1)
        else:
            box("met1", xc - 0.13, 0.1, xc + 0.13, w + 1.2)
            text("met1.label", "out", xc, w + 1.0)
    # the vss rail, labelled at both ends: two pins of one port
    box("met1", -3.0, -1.4, 4.0, -1.0)
    text("met1.label", "vss", -2.8, -1.2)
    text("met1.label", "vss", 3.8, -1.2)
    # the body: a substrate tap under the rail
    box("tap", -0.5, -1.35, 1.9, -1.05)
    box("psdm", -0.625, -1.475, 2.025, -0.925)
    box("licon", 0.6, -1.285, 0.77, -1.115)
    box("li1", -0.5, -1.35, 1.9, -1.05)
    box("mcon", 0.6, -1.285, 0.77, -1.115)
    ly.write(path)


if __name__ == "__main__":
    make(sys.argv[1] if len(sys.argv) > 1 else "tiny.gds")
