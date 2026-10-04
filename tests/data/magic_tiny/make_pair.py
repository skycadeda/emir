"""Draw pair.gds, the magic route's second test cell (tests/test_magic.py),
and two variants of it, from scratch.

    python make_pair.py pair.gds [pair_h.gds [pair_d.gds]]

Two NMOS (W = 1 um, L = 0.5 um) in a chain: the first's gate `g` labelled on
li1, its drain on a met1 strap that runs to the second's gate pad, a net with
NO label (magic names it itself); the second's drain on an `out` strap; both
sources on a met1 rail `vss` over a substrate tap. `pair_h.gds` is the same
layout with the transistors and their straps in a subcell `core`, which
carries labels of its own (`a`, `y`) that are not the top cell's pins.
`pair_d.gds` continues the `out` strap with a 45-degree met1 band, 0.6 um
high (0.6/sqrt(2) = 0.424 um across), to a pad that carries the label.
Synthetic: every coordinate is chosen here, on SkyWater SKY130's public GDS
layer numbers (sky130A.lyp).
"""
import sys

import klayout.db as db

LAYER = {"diff": (65, 20), "tap": (65, 44), "poly": (66, 20), "licon": (66, 44),
         "li1": (67, 20), "mcon": (67, 44), "met1": (68, 20), "nsdm": (93, 44),
         "psdm": (94, 20), "npc": (95, 20), "met1.label": (68, 5), "li1.label": (67, 5)}
W, GL, SD = 1.0, 0.5, 0.44


def u(v):
    return int(round(v * 1000))


class Draw:
    def __init__(self, ly, cell):
        self.ly, self.cell = ly, cell

    def box(self, layer, x0, y0, x1, y1):
        self.cell.shapes(self.ly.layer(*LAYER[layer])).insert(
            db.Box(u(x0), u(y0), u(x1), u(y1)))

    def text(self, layer, name, x, y):
        self.cell.shapes(self.ly.layer(*LAYER[layer])).insert(
            db.Text(name, db.Trans(db.Vector(u(x), u(y)))))


def nmos(c, x):
    """One NMOS with its left edge at x: its source and drain contact columns
    (li1 and mcon over each licon) and its gate's poly up to the pad."""
    c.box("diff", x, 0, x + 2 * SD + GL, W)
    c.box("nsdm", x - 0.125, -0.125, x + 2 * SD + GL + 0.125, W + 0.125)
    c.box("poly", x + SD, -0.13, x + SD + GL, W + 0.6)
    gx = x + SD + GL / 2
    c.box("npc", gx - 0.185, W + 0.2, gx + 0.185, W + 0.6)
    c.box("licon", gx - 0.085, W + 0.3, gx + 0.085, W + 0.47)
    c.box("li1", gx - 0.16, W + 0.25, gx + 0.16, W + 0.52)
    for xc in (x + SD / 2, x + SD + GL + SD / 2):
        for y in (0.2, 0.56):
            c.box("licon", xc - 0.085, y, xc + 0.085, y + 0.17)
            c.box("mcon", xc - 0.085, y, xc + 0.085, y + 0.17)
        c.box("li1", xc - 0.1, 0.1, xc + 0.1, W - 0.1)
    return gx


def devices(c):
    """The two transistors and their straps; returns where the pins sit."""
    g1 = nmos(c, 0.0)
    g2 = nmos(c, 3.0)
    s1, d1 = SD / 2, SD + GL + SD / 2
    s2, d2 = 3.0 + s1, 3.0 + d1
    for xc in (s1, s2):                                   # sources down to the rail
        c.box("met1", xc - 0.13, -1.4, xc + 0.13, W - 0.1)
    # the first drain up and across to the second gate's pad: no label
    c.box("met1", d1 - 0.13, 0.1, d1 + 0.13, W + 1.2)
    c.box("met1", d1 - 0.13, W + 0.9, g2 + 0.13, W + 1.2)
    c.box("mcon", g2 - 0.085, W + 0.3, g2 + 0.085, W + 0.47)
    c.box("met1", g2 - 0.13, W + 0.2, g2 + 0.13, W + 1.2)
    # the second drain: the `out` strap
    c.box("met1", d2 - 0.13, 0.1, d2 + 0.13, W + 2.0)
    return {"g": (g1, W + 0.38), "out": (d2, W + 1.8), "mid": (d1, W + 1.05)}


def rail(c):
    """The vss rail with the substrate tap under it."""
    c.box("met1", -0.5, -1.4, 4.8, -1.0)
    c.box("tap", -0.3, -1.35, 4.6, -1.05)
    c.box("psdm", -0.425, -1.475, 4.725, -0.925)
    for x in (0.6, 2.2, 3.8):
        c.box("licon", x, -1.285, x + 0.17, -1.115)
        c.box("mcon", x, -1.285, x + 0.17, -1.115)
    c.box("li1", -0.3, -1.35, 4.6, -1.05)


def labels(c, at):
    c.text("li1.label", "g", *at["g"])
    c.text("met1.label", "out", *at["out"])
    c.text("met1.label", "vss", -0.3, -1.2)


#: pair_d's band: its height (vertical extent) and run, um
BAND_HEIGHT, BAND_RUN = 0.6, 2.0


def band(c, at):
    """A 45-degree met1 band from the top of the `out` strap, up and right, to
    a Manhattan pad; returns the pad's label place."""
    x, y = at["out"][0] - 0.13, W + 2.0
    h, r = BAND_HEIGHT, BAND_RUN
    pts = [(x, y - h / 2), (x + r, y - h / 2 + r), (x + r, y + h / 2 + r), (x, y + h / 2)]
    c.cell.shapes(c.ly.layer(*LAYER["met1"])).insert(
        db.Polygon([db.Point(u(a), u(b)) for a, b in pts]))
    px, py = x + r, y + r
    c.box("met1", px - 0.2, py - 0.3, px + 0.6, py + 0.5)
    return px + 0.2, py + 0.1


def make(flat_path, hier_path=None, diag_path=None):
    ly = db.Layout()
    ly.dbu = 0.001
    top = Draw(ly, ly.create_cell("pair"))
    at = devices(top)
    rail(top)
    labels(top, at)
    ly.write(flat_path)
    if not hier_path:
        return
    ly = db.Layout()
    ly.dbu = 0.001
    t, core = ly.create_cell("pair_h"), ly.create_cell("core")
    at = devices(Draw(ly, core))
    # the subcell's own labels: magic would name nets after them, never pins of the top
    Draw(ly, core).text("met1.label", "y", *at["out"])
    Draw(ly, core).text("met1.label", "a", *at["mid"])
    top = Draw(ly, t)
    rail(top)
    labels(top, at)
    t.insert(db.CellInstArray(core.cell_index(), db.Trans()))
    ly.write(hier_path)
    if not diag_path:
        return
    ly = db.Layout()
    ly.dbu = 0.001
    top = Draw(ly, ly.create_cell("pair_d"))
    at = devices(top)
    rail(top)
    labels(top, {**at, "out": band(top, at)})
    ly.write(diag_path)


if __name__ == "__main__":
    make(*(sys.argv[1:4] if len(sys.argv) > 1 else ("pair.gds", "pair_h.gds", "pair_d.gds")))
