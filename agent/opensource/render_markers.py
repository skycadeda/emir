"""Draw emir's KLayout markers over a layout and save a picture: for a person
reading what an agent found, or for a report, with no window opened.

Runs inside the KLayout application:

    klayout -z -r agent/opensource/render_markers.py \\
        -rd gds=block.gds -rd lyrdb=emir_out/viewer/emir.lyrdb \\
        -rd png=emir_out/markers.png [-rd only=FAIL] [-rd width=1200] [-rd height=400] \\
        [-rd cell=TOP]

Every marker whose category path contains `only` (default FAIL: the failing
EM segments; use `only=EM` for every EM band, `only=IR` for the IR map) is
drawn as an outlined box on a layer of its own (2000/0, "emir markers") added
to the in-memory layout -- the GDS file is not changed. The boxes go into
`cell` (the cell emir screened), else the cell KLayout shows: a layout with
several top cells has no single top cell. Headless runs may need
QT_QPA_PLATFORM=offscreen.
"""
import pya  # noqa: F401  (provided by the KLayout application)

_g = globals()
gds, lyrdb, png = _g.get("gds"), _g.get("lyrdb"), _g.get("png", "markers.png")
only = _g.get("only", "FAIL")
width, height = int(_g.get("width", 1200)), int(_g.get("height", 400))
if not (gds and lyrdb):
    raise SystemExit("give -rd gds=FILE -rd lyrdb=FILE [-rd png=FILE]")
lv = pya.LayoutView()
lv.load_layout(gds, True)
lv.max_hier()
db = pya.ReportDatabase("")
db.load(lyrdb)
cv = lv.active_cellview()
ly = cv.layout()
name = _g.get("cell")
if name and not ly.has_cell(name):
    raise SystemExit(f"no cell {name!r} in {gds}")
top = ly.cell(name) if name else cv.cell
mk = ly.layer(pya.LayerInfo(2000, 0, "emir markers"))
n = 0
for it in db.each_item():
    if only not in db.category_by_id(it.category_id()).path():
        continue
    for v in it.each_value():
        if v.is_box():
            top.shapes(mk).insert(v.box().enlarged(0.5))
            n += 1
lv.add_missing_layers()
lv.zoom_fit()
lv.save_image(png, width, height)
print(f"{png}: {n} marker(s) matching {only!r} of {db.num_items()} drawn on {top.name}")
