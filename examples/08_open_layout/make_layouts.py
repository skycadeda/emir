"""Draw example 08's layout, from scratch.

    python make_layouts.py OUT_DIR

writes OUT_DIR/sink.gds: a synthetic cell, every coordinate chosen here, no
PDK cell or real design copied, on SkyWater SKY130's public GDS layer numbers
(the PDK's sky130A.lyp), so magic's sky130 technology reads it.

`sink`: four two-finger NMOS (W = 5 um, L = 0.5 um per finger), sources to a
vss bus over a substrate tap, drains to an out bus, gates to vb; out and vss
each on a met3 pad reached by one via2 cut, vb labelled on its met2 bus.
(The removed `emir layout` read this same layout; README.md compares.)
"""
from __future__ import annotations

import os
import sys

import klayout.db as db

#: SKY130 GDS layer/datatype pairs (public: sky130A.lyp)
LAYER = {"diff": (65, 20), "tap": (65, 44), "poly": (66, 20), "licon": (66, 44),
         "li1": (67, 20), "mcon": (67, 44), "met1": (68, 20), "via": (68, 44),
         "met2": (69, 20), "via2": (69, 44), "met3": (70, 20), "nsdm": (93, 44),
         "psdm": (94, 20), "npc": (95, 20), "met2.label": (69, 5), "met3.label": (70, 5)}


class Drawing:
    def __init__(self, cell):
        self.ly = db.Layout()
        self.ly.dbu = 0.001
        self.top = self.ly.create_cell(cell)
        self.idx = {n: self.ly.layer(*v) for n, v in LAYER.items()}

    @staticmethod
    def um(v):
        return int(round(v * 1000))

    def box(self, layer, x0, y0, x1, y1):
        u = self.um
        self.top.shapes(self.idx[layer]).insert(db.Box(u(x0), u(y0), u(x1), u(y1)))

    def text(self, layer, name, x, y):
        self.top.shapes(self.idx[layer]).insert(
            db.Text(name, db.Trans(db.Vector(self.um(x), self.um(y)))))

    def column(self, layer, xc, y0, y1, size, space):
        y = y0
        while y + size <= y1 + 1e-9:
            self.box(layer, xc - size / 2, y, xc + size / 2, y + size)
            y += size + space

    def write(self, path):
        self.ly.write(path)
        return path


# ------------------------------------------------------------------ the sink



def sink(path, k=4, w=5.0, gl=0.5, pitch=3.2):
    d = Drawing("sink")
    sd = 0.44                                                  # a source/drain strip
    for i in range(k):
        x = i * pitch
        strips = [x, x + sd + gl, x + 2 * (sd + gl)]           # S, D, S
        width = 2 * (sd + gl) + sd
        d.box("diff", x, 0, x + width, w)
        d.box("nsdm", x - 0.125, -0.125, x + width + 0.125, w + 0.125)
        for g in (x + sd, x + 2 * sd + gl):                    # two gates
            d.box("poly", g, -0.13, g + gl, w + 0.3)
        d.box("poly", x + sd, w + 0.3, x + 2 * sd + 2 * gl, w + 0.63)    # the gate bar
        gx = x + sd + gl + sd / 2
        d.box("npc", gx - 0.185, w + 0.28, gx + 0.185, w + 0.68)
        d.box("licon", gx - 0.085, w + 0.38, gx + 0.085, w + 0.55)
        d.box("li1", gx - 0.16, w + 0.33, gx + 0.16, w + 0.6)
        d.box("mcon", gx - 0.085, w + 0.38, gx + 0.085, w + 0.55)
        d.box("met1", gx - 0.16, w + 0.33, gx + 0.16, w + 0.72)
        d.box("via", gx - 0.075, w + 0.44, gx + 0.075, w + 0.59)
        for j, xl in enumerate(strips):
            xc = xl + sd / 2
            d.column("licon", xc, 0.2, w - 0.2, 0.17, 0.17)
            d.box("li1", xc - 0.1, 0.1, xc + 0.1, w - 0.1)
            d.column("mcon", xc, 0.2, w - 0.2, 0.17, 0.19)
            if j == 1:                                         # the drain, to `out`
                d.box("met1", xc - 0.13, 0.0, xc + 0.13, w)
                d.box("via", xc - 0.075, w / 2 - 0.075, xc + 0.075, w / 2 + 0.075)
            else:                                              # a source, down to the tap
                d.box("met1", xc - 0.13, -2.4, xc + 0.13, w)
                d.box("via", xc - 0.075, -1.075, xc + 0.075, -0.925)
    span = (k - 1) * pitch + 2 * (sd + gl) + sd
    d.box("met2", -0.5, w / 2 - 0.3, span + 0.5, w / 2 + 0.3)        # out
    d.box("met2", -0.5, -1.4, span + 0.5, -0.6)                       # vss
    d.box("met2", -0.5, w + 0.37, span + 0.5, w + 0.67)               # vb
    d.box("tap", 0, -2.4, span, -2.0)                                 # the substrate tap
    d.box("psdm", -0.125, -2.525, span + 0.125, -1.875)
    cx = 0.2
    while cx + 0.17 <= span - 0.2:
        d.box("licon", cx, -2.285, cx + 0.17, -2.115)
        d.box("mcon", cx, -2.285, cx + 0.17, -2.115)
        cx += 0.34
    d.box("li1", 0, -2.4, span, -2.0)
    d.box("met1", 0, -2.4, span, -2.0)

    def pin(name, yc):
        d.box("met3", -3.0, yc - 0.6, -0.2, yc + 0.6)
        d.box("met2", -3.0, yc - 0.3, -0.4, yc + 0.3)
        d.box("via2", -0.9, yc - 0.1, -0.7, yc + 0.1)                  # one cut
        d.text("met3.label", name, -2.6, yc)
    pin("out", w / 2)
    pin("vss", -1.0)
    d.text("met2.label", "vb", -0.3, w + 0.52)
    return d.write(path)


def main(out):
    os.makedirs(out, exist_ok=True)
    print(sink(os.path.join(out, "sink.gds")))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "out")
