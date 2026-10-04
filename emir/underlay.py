"""The layout under a chart: the GDS drawn on black, to be dimmed under the
results (`emir.style.UNDERLAY_ALPHA`).

* With the `klayout` Python package, KLayout draws it (a headless
  `klayout.lay.LayoutView`), in the PDK's own layer colours when its layer
  properties file is found (`find_lyp`: `$PDK_ROOT/<pdk>/libs.tech/klayout/
  tech/*.lyp`, `<pdk>` the first word of the process file's `pdk:`), else in
  a neutral grey per layer, each with its own stipple.
* Without it, gdstk's polygons (`polygons`), for the chart to draw in grey.
* Without either, no underlay: the chart draws the results on the black
  canvas alone and says so (EMIR-I721).

Nothing here changes the layout: it is read, drawn and closed.

`render` follows Skyforge's layout renderer: KLayout's headless calls in its
order, on a black background. Skyforge is
Copyright (c) 2026 Skycadeda, licensed under the MIT License by its copyright
holder (THIRD_PARTY_NOTICES.md).
"""
from __future__ import annotations

import glob
import os

#: the greys and KLayout stipples of the neutral layer properties, by layer
#: (sorted by GDS number), so neighbouring layers stay apart
GREYS = (0xa8a8a8, 0x808080, 0xc8c8c8, 0x949494, 0x6c6c6c, 0xbababa)
STIPPLES = (5, 9, 1, 13, 3, 17)


def find_lyp(pdk: str | None) -> str | None:
    """The PDK's KLayout layer properties (.lyp) under $PDK_ROOT, or None.
    `pdk` is the process file's `pdk:` value; its first word names the PDK's
    directory (sky130A, gf180mcuD ...)."""
    root = os.environ.get("PDK_ROOT")
    name = str(pdk or "").split()[0] if pdk else ""
    if not (root and name) or "/" in name or name in (".", ".."):
        return None
    tech = os.path.join(root, name, "libs.tech", "klayout", "tech")
    own = os.path.join(tech, f"{name}.lyp")
    if os.path.isfile(own):
        return own
    found = sorted(glob.glob(os.path.join(tech, "*.lyp")))
    return found[0] if found else None


def have_klayout() -> bool:
    try:
        import klayout.lay  # noqa: F401
        return True
    except Exception:          # ImportError, or a build without the lay module
        return False


def render(gds: str, cell: str | None, box, width: int, height: int,
           lyp: str | None = None) -> tuple:
    """The layout in `box` (x0, y0, x1, y1 um) as an RGB uint8 array of
    `height` x `width` pixels on black, and how it was drawn ("the PDK's
    layer properties" or "a neutral grey per layer"). None, "" without the
    klayout package."""
    if not have_klayout():
        return None, ""
    import io

    import klayout.db as kdb
    import klayout.lay as klay
    import numpy as np
    view = klay.LayoutView()
    view.load_layout(str(gds), True)
    cv = view.cellview(0)
    ly = cv.layout()
    if cell and ly.has_cell(cell):
        cv.cell = ly.cell(cell)
    how = "the PDK's layer properties"
    if lyp and os.path.isfile(lyp):
        view.load_layer_props(lyp)
    else:
        how = "a neutral grey per layer"
        view.clear_layers()
        infos = sorted((ly.get_info(i).layer, ly.get_info(i).datatype)
                       for i in ly.layer_indexes())
        for k, (ln, dt) in enumerate(infos):
            p = klay.LayerPropertiesNode()
            p.source = f"{ln}/{dt}@1"
            p.fill_color = p.frame_color = GREYS[k % len(GREYS)]
            p.dither_pattern = STIPPLES[k % len(STIPPLES)]
            view.insert_layer(view.end_layers(), p)
    view.max_hier()
    for key, val in (("background-color", "#000000"), ("grid-visible", "false"),
                     ("grid-show-ruler", "false"), ("text-visible", "false")):
        view.set_config(key, val)
    view.zoom_box(kdb.DBox(*box))
    png = view.get_pixels_with_options(int(width), int(height)).to_png_data()
    from PIL import Image
    return np.asarray(Image.open(io.BytesIO(png)).convert("RGB")), how


def polygons(gds: str, cell: str | None) -> dict:
    """{(layer, datatype): [N x 2 arrays in um]} of the cell, flattened, by
    gdstk; {} without gdstk."""
    try:
        import gdstk
    except ImportError:
        return {}
    lib = gdstk.read_gds(str(gds))
    tops = lib.top_level()
    c = next((x for x in lib.cells if x.name == cell), None) if cell else None
    c = c or (tops[0] if tops else None)
    if c is None:
        return {}
    out: dict = {}
    for p in c.get_polygons(depth=None):
        out.setdefault((p.layer, p.datatype), []).append(p.points)
    return out


def bbox(gds: str, cell: str | None) -> list | None:
    """The cell's bounding box [x0, y0, x1, y1] in um (its outline), or None."""
    try:
        import klayout.db as kdb
        ly = kdb.Layout()
        ly.read(str(gds))
        c = ly.cell(cell) if cell and ly.has_cell(cell) else (
            ly.top_cells()[0] if ly.top_cells() else None)
        if c is None:
            return None
        b = c.dbbox()
        return None if b.empty() else [b.left, b.bottom, b.right, b.top]
    except ImportError:
        pass
    polys = polygons(gds, cell)
    pts = [q for ps in polys.values() for p in ps for q in p]
    if not pts:
        return None
    xs, ys = [q[0] for q in pts], [q[1] for q in pts]
    return [min(xs), min(ys), max(xs), max(ys)]
