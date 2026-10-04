#!/usr/bin/env python3
"""A preview GDS of a DSPF's conductors, to look at emir's KLayout markers on.

Each resistor of the DSPF that carries a box ($LLX $LLY $URX $URY, um) becomes
a rectangle on a layer of its own per DSPF layer token: 1000/0 for the first
token in sorted order, 1001/0 for the next ... (synthetic numbers, not a
PDK's; the layer names are the tokens). The cell is the DSPF's design name,
the one `emir run --viewer` puts the markers in. Use it when the block's own
GDS is not at hand; with the real GDS, open that instead.

    python agent/opensource/dspf_preview_gds.py block.dspf -o preview.gds
    klayout preview.gds -m emir_out/viewer/emir.lyrdb

Needs the klayout Python package (`pip install -e '.[klayout]'` from the clone root,
else `pip install klayout`).
"""
from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], allow_abbrev=False)
    ap.add_argument("dspf")
    ap.add_argument("-o", "--out", required=True, help="the GDS to write")
    ap.add_argument("--cell", default=None, help="cell name (default: the DSPF design)")
    a = ap.parse_args(argv)
    try:
        import klayout.db as kdb
    except ImportError:
        print("needs the klayout Python package: pip install -e '.[klayout]' from the clone "
              "root, else pip install klayout", file=sys.stderr)
        return 3
    from emir.dspf import parse
    d = parse(a.dspf)
    ly = kdb.Layout()
    ly.dbu = 0.001
    cell = ly.create_cell(a.cell or d.design or "top")
    tokens = sorted({r["layer"] for r in d.res if r.get("layer")})
    layer = {t: ly.layer(kdb.LayerInfo(1000 + k, 0, t)) for k, t in enumerate(tokens)}
    n = 0
    for r in d.res:
        box = r.get("bbox")
        if not box or not r.get("layer"):
            continue
        x0, y0, x1, y1 = box
        if x1 <= x0 or y1 <= y0:
            continue
        cell.shapes(layer[r["layer"]]).insert(kdb.DBox(x0, y0, x1, y1))
        n += 1
    ly.write(a.out)
    print(f"{a.out}: cell {cell.name}, {n} rectangles on "
          + ", ".join(f"{t} {1000 + k}/0" for k, t in enumerate(tokens)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
