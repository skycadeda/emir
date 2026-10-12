"""EM/IR results in a layout viewer — the form a layout engineer can act on.

A report tells you a net fails. Fixing it means standing in the layout, on the
segment, knowing what to change. This module produces that: per-violation
geometry taken from the extraction's own node coordinates, ranked, filterable,
and carrying a **fix hint** — the width or cut count the segment actually needs.

Two targets:

* **Virtuoso** (`emir_explorer.il`) — primary: the layouts are in OA and it
  is where the fix gets made. Colours are EMIR's own display packets, so no
  PDK layer or packet is required; the one LPP the highlight sets sit on is
  `mark_layer` from the site file if one is set and the tech file has it,
  else the current entry layer. The cellview is opened read-only and never
  changed.
* **KLayout** (`emir.lyrdb`) — secondary, written through `klayout.rdb`. Its
  Marker Browser gives net/severity filtering and zoom-to for free. This path
  has not yet been checked in the KLayout GUI.

**Why highlight sets rather than marker layers.** The obvious approach — write
marker shapes onto dedicated layers — needs those layers to exist in the tech
file, and many PDKs have none to spare; a highlight emitted on an LPP the tech
file does not define is simply not drawn. Virtuoso highlight sets take an
existing LPP purely for its *colour*, draw over the layout without modifying
it, and can be destroyed and rebuilt cheaply — which is exactly what live
threshold and net filtering needs.

The palette is EMIR's one colour scale (`emir.style`, the scale of every
chart and HTML page), as twelve steps (`BAND_RGB`): steps 0..9 the blue ramp,
dark to light (the IR bands, and EM below the warn band by the ratio's log),
step 10 the warn band's amber, step 11 the failure red. A band is drawn with
the display packet `EMIR_B<step>` that `emir_display.drf` defines (written by
`emir gui-install`; committed as `agent/cadence/skill/emir_display.drf`).
Without those packets the Explorer draws every band in the one colour of its
mark LPP and says so in the CIW.

A big run's viewer data is bounded: above `VIEWER_MAX` segments the clean
ones (below 0.5x) and the IR segments off the rails are left out and the
worst `VIEWER_MAX` kept, with an EMIR-I780 notice; `--viewer-all` writes
every segment.
"""
from __future__ import annotations

import json
import math
import numbers
import os

from . import SCREENING_NOTE
from .errors import EmirError, notice

#: the IR bands: every step of the blue ramp -- a drop is a magnitude, and
#: every band is labelled with its own mV value, so colour never carries the
#: meaning alone
N_STEPS = 10
#: the steps of the warn and the failure colours, after the ramp's
WARN_STEP, FAIL_STEP = N_STEPS, N_STEPS + 1


def _ramp_step(ratio: float, warn: float) -> int:
    """The ramp step (0..9) of a ratio below the warn band, by its log."""
    from . import style as S
    return int(round(S.em_t(ratio, warn) * (N_STEPS - 1)))


def em_bands(warn: float = 0.8) -> list:
    """The EM bands of the viewers: (lower ratio, label, step of `BAND_RGB`).
    Below the warn fraction a band takes the ramp's shade at its geometric
    middle (the charts' log scale, from 0.001x); the warn band is amber and
    every band at 1x and over red, as on every chart."""
    from . import style as S
    edges = [e for e in (0.01, 0.1, 0.5) if e < warn * 0.95]
    out, lo = [], 0.0
    for hi in edges + [warn]:
        mid = math.sqrt(max(lo, S.EM_FLOOR) * hi)
        label = f"below {hi:.2f}x" if lo == 0.0 else f"{lo:.2f}-{hi:.2f}x"
        out.append((lo, label, _ramp_step(mid, warn)))
        lo = hi
    out += [(warn, f"{warn:.2f}-1.00x  WARN", WARN_STEP),
            (1.00, "1.00-1.50x  FAIL", FAIL_STEP),
            (1.50, "1.50-2.50x  FAIL", FAIL_STEP),
            (2.50, "over 2.50x  FAIL", FAIL_STEP)]
    return out


#: the bands at the default warn fraction (a run uses its process file's)
EM_BANDS = em_bands(0.8)

#: the most EM and the most IR segments the viewer data holds by default: above
#: it the clean segments and the IR off the rails are left out, worst first
#: (EMIR-I780); --viewer-all writes every segment. In emir_data.il an EM record
#: takes about 160 bytes and an IR record about 60 (measured on a synthetic
#: 50 000-resistor chain, tests/test_cadence_viewer.py)
VIEWER_MAX = 20_000
#: below this ratio a segment is clean enough to leave out of a trimmed run
VIEWER_TRIM_RATIO = 0.5

#: no default mark layer: unless the site file sets `mark_layer`, the Explorer
#: paints on the current entry layer (the packets supply the colour)
MARK_LAYER = ""


def em_band(ratio: float, bands=None) -> int:
    """The index of a ratio's band in `bands` (default `EM_BANDS`)."""
    b = 0
    for i, (lo, _, _) in enumerate(bands or EM_BANDS):
        if ratio >= lo:
            b = i
    return b


#: the width a fix prescribes is a multiple of this (um): a 5 nm grid
FIX_GRID_UM = 0.005


def _strictly_above(x: float, step: float) -> float:
    """The smallest multiple of `step` strictly above `x`: at exactly the
    limit the ratio is 1.00x, still a FAIL, so a fix must go past it."""
    return (math.floor(x / step + 1e-9) + 1) * step


def fix_hint(v: dict) -> str:
    """What to change, in the engineer's own units.

    A ratio is a diagnosis; this is the prescription. A wire at 2.03x needs its
    width multiplied by 2.03 -- rounded UP to the next 5 nm strictly above
    w x ratio, since at exactly the limit the ratio is 1.00x, still a FAIL (and
    a width rounded to the nearest digit could land below the limit); a via
    at 3.13x needs more cuts than n x ratio (`floor(n x ratio) + 1`). Stating
    it removes the arithmetic step where mistakes happen.
    """
    r = v["ratio"]
    if not math.isfinite(r):
        # nothing to scale: the current was not solved (see the run's warnings)
        return f"{v['layer']}: not checked -- the ratio is not a number (see the warnings)"
    # Only prescribe for something that actually fails. Below 1.00x the
    # arithmetic says "narrow this wire", which is not a fix but a hazard.
    # The viewer applies the same rule; keeping them in step matters because
    # this hint is what reaches emir_viz.json and the KLayout marker database.
    if r < 1.0:
        return ""
    if v["kind"] == "via" and v.get("cuts_from") == "assumed":
        # the DSPF gave no cut count: "1 -> N cuts" would invent a count
        return f"{v['layer']}: x{r:.2f} at ONE cut (cut count not in the DSPF)"
    if v["kind"] == "via":
        n = int(v.get("cuts") or 0)
        if n:
            need = int(round(_strictly_above(n * r, 1.0)))
            return f"{v['layer']}: {n} -> {need} cuts (+{need - n})"
        return f"{v['layer']}: over by x{r:.2f}"
    w = v.get("w") or 0.0
    dw = v.get("dw") or 0.0
    if w and dw and w > dw:
        # the limit is J x (W - dW): the effective width scales by the ratio,
        # the width loss stays
        return (f"{v['layer']}: widen {w:.3f} -> "
                f"{_strictly_above(dw + (w - dw) * r, FIX_GRID_UM):.3f} um "
                f"(x{r:.2f} of W - dW, dW {dw:g} um)")
    if w:
        return (f"{v['layer']}: widen {w:.3f} -> {_strictly_above(w * r, FIX_GRID_UM):.3f} um "
                f"(x{r:.2f})")
    return f"{v['layer']}: over by x{r:.2f}"


def _seg_rect(a, b, w):
    """Marker rectangle for a segment between two node points, `w` wide.

    Extracted routing is Manhattan, so the segment's own axis gives the box
    directly; a diagonal (or a zero-length via stub) falls back to a square of
    side `w` so it is still visible and still clickable.
    """
    (x1, y1), (x2, y2) = a, b
    h = max(w, 0.02) / 2.0
    if abs(y2 - y1) <= 1e-9 and abs(x2 - x1) > 1e-9:
        return (min(x1, x2), y1 - h, max(x1, x2), y1 + h)
    if abs(x2 - x1) <= 1e-9 and abs(y2 - y1) > 1e-9:
        return (x1 - h, min(y1, y2), x1 + h, max(y1, y2))
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    dx, dy = abs(x2 - x1) / 2 + h, abs(y2 - y1) / 2 + h
    return (cx - dx, cy - dy, cx + dx, cy + dy)


def violations(res, dspf, net, proc, include_clean=True) -> list:
    """Every checked resistor on `net` as a viewer record.

    Clean segments are included by default (band 0/1) so the viewer can show
    the whole current path, not only its failures — seeing where the current
    *goes* is most of understanding why a segment is hot.
    """
    from .dspf import via_cuts
    from .engine import METRICS
    nw = res["network"]
    lmap = res["layer_map"]
    xy = dspf.node_xy
    bands = em_bands(getattr(proc, "warn_fraction", 0.8))
    out = []
    for i, r in enumerate(nw.res):
        hit = lmap.get(r["layer"])
        if not hit:
            continue
        kind, pdkname = hit
        ratios, vals = {}, {}
        # the same cut count the verdict used (`dspf.em_clusters`), so a via
        # counted from its area, or assumed one cut, is drawn too
        n, src = via_cuts(r, proc.vias[pdkname]) if kind == "via" else (None, None)
        # W - dW, as the verdict used it (`dspf.em_clusters`)
        weff = proc.wire_width(pdkname, r["w"]) if kind != "via" and r.get("w") else None
        for m in METRICS:
            if m not in res["Ie"]:
                # a metric the run did not compute: no number at all (null in
                # emir_viz.json, nil in emir_data.il), never a 0 that reads as
                # clean
                continue
            if kind == "via":
                lim = proc.vias[pdkname]["imax"].get(m)
                if not lim:
                    continue
                vals[m] = abs(res["Ie"][m][i]) / n
            else:
                lim = proc.layers[pdkname]["jmax"].get(m)
                if not (lim and r.get("w")):
                    continue
                vals[m] = abs(res["Ie"][m][i]) / weff
            ratios[m] = vals[m] / lim
        if not ratios:
            continue
        gov = max(ratios, key=ratios.get)
        if not include_clean and ratios[gov] < proc.warn_fraction:
            continue
        a, b = xy.get(r["n1"]), xy.get(r["n2"])
        if a is None or b is None:
            continue
        w = r.get("w") or (math.sqrt(r["area"]) if r.get("area") else 0.05)
        rect = _seg_rect(a, b, w)
        v = {"net": net, "name": r["name"], "layer": pdkname,
             "kind": "via" if kind == "via" else "wire",
             "metric": gov, "ratio": float(ratios[gov]),
             # a metric with no limit: 0 (not checked, drawn grey); a metric
             # the run did not compute: null
             "ratios": {m: (float(ratios.get(m, 0.0)) if m in res["Ie"] else None)
                        for m in METRICS},
             "value": float(vals[gov]),
             "current": float(res["Ie"][gov][i]),
             # per-metric density and current: the viewer lets the engineer
             # switch metric, and every number on screen has to switch with it
             "values": {m: (float(vals.get(m, 0.0)) if m in res["Ie"] else None)
                        for m in METRICS},
             "currents": {m: float(res["Ie"][m][i]) if m in res["Ie"] else None
                          for m in METRICS},
             "w": r.get("w"), "cuts": n if kind == "via" else r.get("cuts"),
             "dw": (proc.layers[pdkname]["width_loss"] or None) if kind != "via" else None,
             "cuts_from": src,
             "rect": [round(c, 4) for c in rect],
             "band": em_band(float(ratios[gov]), bands)}
        v["hint"] = fix_hint(v)
        out.append(v)
    out.sort(key=lambda c: -c["ratio"])
    return out


def ir_segments(res, dspf, net, nbins=N_STEPS) -> tuple:
    """Segments coloured by their worse end's potential. `(items, bands)`.

    Per-segment rather than per-node because a segment is what you can see and
    select in a layout. Each segment takes the larger excursion of its two
    nodes, so the viewer's worst segment shows the same drop as the console
    and the summary. (The midpoint used before reads low wherever one segment
    holds a large `I*R`: on the sample, the 24 ohm one-cut contact carrying
    1 mA put the viewer's maximum at 42 mV against the reported 54 mV.)
    """
    nw = res["network"]
    V = res["V"]
    xy = dspf.node_xy
    if V is None or not len(V):
        return [], []
    # The solve puts the reference node -- the net's own pin -- at 0 and every
    # other potential is a signed excursion from it. The magnitude of that
    # excursion IS the IR drop, and taking it this way is the only form that
    # works for both polarities: a supply droops below its pin, a ground
    # bounces above its pin. Referencing to max(V) instead makes the pin of a
    # ground net the worst point on the net, which is backwards -- the pin is
    # where the drop is zero by definition.
    items = []
    for i, r in enumerate(nw.res):
        a, b = xy.get(r["n1"]), xy.get(r["n2"])
        if a is None or b is None:
            continue
        v1, v2 = V[nw.idx[r["n1"]]], V[nw.idx[r["n2"]]]
        v = v1 if abs(v1) >= abs(v2) else v2
        w = r.get("w") or 0.05
        # band is assigned later, against the worst drop across ALL nets --
        # see band_ir()
        items.append({"net": net, "name": r["name"],
                      "mv": abs(float(v)) * 1e3, "band": 0,
                      "rect": [round(c, 4) for c in _seg_rect(a, b, w)]})
    return items, []


#: name patterns that mean "this is a rail". Deliberately conservative: a
#: reference like VREF or a bias like VBIAS is NOT a rail, even though IR on it
#: matters in analog -- it is just not the quantity a PDN is signed off on.
_POWER_PREFIX = ("VDD", "VSS", "VCC", "VEE", "AVDD", "AVSS", "DVDD", "DVSS",
                 "VPWR", "VGND", "VBAT", "VNW", "VPW")
_POWER_EXACT = ("GND", "GROUND", "VPP", "VNN", "VBB", "SUB", "VSUB")


def power_nets(nets) -> list:
    """The supply/ground nets among `nets`, by name."""
    out = []
    for n in nets:
        u = str(n).upper().lstrip("\\")
        if u in _POWER_EXACT or u.startswith(_POWER_PREFIX):
            out.append(n)
    return sorted(out)


def band_ir(items, nbins=N_STEPS, scale_nets=()) -> tuple:
    """Assign IR bands against the worst drop across every net. `(peak, bands)`.

    Normalising per net would make each net's own worst segment red, so the
    same colour would mean 1 mV on one net and 4 mV on another and neither
    would match the legend. One scale for the whole block keeps colour
    comparable between nets and equal to what the colour bar says.

    `scale_nets` restricts what sets that scale — the rails, normally, so the
    ramp reads as PDN droop and is not compressed by a signal net that happens
    to drop more. Segments outside the scale still get a band; they clamp at
    the top rather than being dropped.
    """
    pool = [i for i in items if i["net"] in scale_nets] if scale_nets else items
    peak = max((i["mv"] for i in pool), default=0.0)
    if peak <= 0:                       # rails perfectly flat: fall back
        peak = max((i["mv"] for i in items), default=0.0)
    for it in items:
        frac = 0.0 if peak <= 0 else it["mv"] / peak
        it["band"] = min(nbins - 1, max(0, int(frac * nbins)))
    bands = [{"band": k, "lo_mv": peak * k / nbins,
              "hi_mv": peak * (k + 1) / nbins} for k in range(nbins)]
    return peak, bands


# ------------------------------------------------------------------ SKILL


def _sk(x) -> str:
    """One Python value as a SKILL literal. Everything written into
    `emir_data.il` goes through here, because that file is `load()`ed -- run --
    by Virtuoso: a name that could close a string literal would otherwise be
    code.

    * None -> `nil`; bool -> `t` / `nil`; integers as integers;
    * a real number -> `%.6g`, and a NaN or infinity raises: SKILL has no
      literal for it, and a non-finite result is a failure, never a value;
    * anything else -> a string literal with `\\` and `"` escaped, newline
      and tab written as `\\n` and `\\t`, and any other control character
      replaced by `?`, so the file holds no raw control character at all.
    """
    if x is None:
        return "nil"
    if isinstance(x, bool):
        return "t" if x else "nil"
    if isinstance(x, numbers.Integral):
        return str(int(x))
    if isinstance(x, numbers.Real):
        v = float(x)
        if not math.isfinite(v):
            raise EmirError("EMIR-E703", f"non-finite number {v!r} cannot go into the viewer "
                             f"data (a NaN or Inf result is a failure)")
        return f"{v:.6g}"
    out = []
    for ch in str(x):
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20 or ord(ch) == 0x7f:
            out.append("?")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def has_control_char(s) -> bool:
    """Does `s` hold a control character (a newline, a tab, NUL, DEL ...)?"""
    return s is not None and any(ord(c) < 0x20 or ord(c) == 0x7f for c in str(s))


def check_viewer_names(lib=None, cell=None, design=None) -> list:
    """Problems with the library, cell and DSPF design name the viewer data
    would carry: a control character in any of them is refused (they end up
    in SKILL, in file names and in the Explorer's title)."""
    out = []
    for what, v in (("--lib", lib), ("--cell", cell), ("the DSPF *|DESIGN name", design)):
        if has_control_char(v):
            out.append(f"{what} {v!r} contains a control character")
    return out


def _palette() -> list:
    """`BAND_RGB`: the blue ramp at `N_STEPS` even steps (`style.ramp`),
    then the warn amber and the failure red, as (r, g, b)."""
    from . import style as S
    steps = [S.ramp(k / (N_STEPS - 1)) for k in range(N_STEPS)] + [S.WARN, S.FAIL]
    return [S.hex_rgb(h) for h in steps]


#: EMIR's palette in the viewers: the display packets EMIR_B0..11 of the
#: Virtuoso Explorer (`emir_display.drf`) and its legend images are made from
#: it, so the layout, the form's legend, the HTML pages and the charts show one
#: scale (`emir.style`)
BAND_RGB = _palette()


def _png(path, rows) -> str:
    """Write a truecolour PNG. `rows` is a list of rows of (r, g, b).

    Hand-rolled because the legend is three colours wide and pulling in an
    imaging library for that is not a trade worth making.
    """
    import struct
    import zlib
    h, w = len(rows), len(rows[0])
    raw = b"".join(b"\x00" + bytes(c for px in r for c in px) for r in rows)

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    open(path, "wb").write(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b""))
    return path


#: 3x5 bitmap glyphs. The tick numbers are drawn INTO the bar image so they
#: line up with their colour block exactly; a separate text label beside the
#: image cannot, because the form's font is proportional.
_FONT = {
    "0": ("111", "101", "101", "101", "111"),
    "1": ("010", "110", "010", "010", "111"),
    "2": ("111", "001", "111", "100", "111"),
    "3": ("111", "001", "111", "001", "111"),
    "4": ("101", "101", "111", "001", "001"),
    "5": ("111", "100", "111", "001", "111"),
    "6": ("111", "100", "111", "101", "111"),
    "7": ("111", "001", "001", "001", "001"),
    "8": ("111", "101", "111", "101", "111"),
    "9": ("111", "101", "111", "001", "111"),
    ".": ("000", "000", "000", "000", "010"),
    "x": ("000", "101", "010", "101", "000"),
    "+": ("000", "010", "111", "010", "000"),
    " ": ("000", "000", "000", "000", "000"),
    "m": ("000", "000", "111", "111", "101"),
    "V": ("101", "101", "101", "101", "010"),
}
_GLYPH_W, _GLYPH_H, _SCALE = 3, 5, 2
_TEXT_W = (_GLYPH_W + 1) * _SCALE           # advance per character


def _draw_text(rows, x0, y0, text, ink=(20, 20, 20)):
    """Blit `text` at (x0, y0). Silently skips glyphs it has no bitmap for."""
    h, w = len(rows), len(rows[0])
    for ci, ch in enumerate(text):
        g = _FONT.get(ch)
        if not g:
            continue
        for gy in range(_GLYPH_H):
            for gx in range(_GLYPH_W):
                if g[gy][gx] != "1":
                    continue
                for sy in range(_SCALE):
                    for sx in range(_SCALE):
                        x = x0 + ci * _TEXT_W + gx * _SCALE + sx
                        y = y0 + gy * _SCALE + sy
                        if 0 <= x < w and 0 <= y < h:
                            rows[y][x] = ink


def colour_bar(path, bands, labels=(), width=620, ramp_h=14,
               bg=(240, 240, 240)) -> str:
    """A horizontal ramp, one block per band, with a value under each block
    edge: `labels` holds one label per edge from the left (n + 1 for n
    blocks; n when the last band is open-ended), each centred on its edge
    and kept inside the image.

    `bg` matches the form's background so the strip reads as part of the form
    rather than as a pasted-in image.
    """
    n = len(bands)
    text_h = _GLYPH_H * _SCALE + 4 if labels else 0
    height = ramp_h + text_h
    rows = [[bg] * width for _ in range(height)]

    edges = []
    for x in range(width):
        b = min(n - 1, x * n // width)
        edges.append(b)
        for y in range(ramp_h):
            rows[y][x] = BAND_RGB[bands[b] % len(BAND_RGB)]

    dark = (40, 40, 40)
    for x in range(width):                      # block separators + frame
        rows[0][x] = rows[ramp_h - 1][x] = dark
        if x and edges[x] != edges[x - 1]:
            for y in range(ramp_h):
                rows[y][x] = dark
    for y in range(ramp_h):
        rows[y][0] = rows[y][width - 1] = dark

    # each label on its block edge, clamped to stay inside the image; from the
    # right, so the last (the largest value, with its unit) is always drawn
    # and a label that would touch the one to its right is left out
    labels = list(labels[:n + 1])
    right = width + _TEXT_W
    for i in range(len(labels) - 1, -1, -1):
        tw = len(labels[i]) * _TEXT_W
        x = max(1, min(i * width // n - tw // 2, width - tw - 1))
        if x + tw + _TEXT_W > right and i not in (0, len(labels) - 1):
            continue
        _draw_text(rows, x, ramp_h + 3, labels[i])
        right = x
    return _png(path, rows)


def bar_labels(bands, ir_max_mv: float, n_ir: int) -> tuple:
    """The viewer bars' edge labels: EM the lower ratio of each band (the
    last band is open-ended); IR the drop at every edge in mV as the charts
    print numbers (`style.num`), the largest at the right end with its unit.
    The bars' origin is an exact 0."""
    from . import style as S
    em = [f"{lo:g}x" for lo, _, _ in bands]
    ir = ["0"] + [S.num(ir_max_mv * k / n_ir, 3) if ir_max_mv > 0 else "0"
                  for k in range(1, n_ir + 1)]
    ir[-1] += " mV"
    return em, ir


def skill_data(nets_data, lib, cell, path) -> str:
    """The results as SKILL literals — no parsing, no file IO in the viewer.

    Every value is written through `_sk`, the library and cell included (an
    unset library is `nil`, and the Explorer then asks the form or the window);
    the header comment names neither, so no input can end the comment early.
    """
    L = ['; EMIR viewer data -- generated by emir, do not edit',
         f'emirLib = {_sk(lib)}', f'emirCell = {_sk(cell)}']
    nets = sorted({d["net"] for d in nets_data["em"]})
    L.append("emirNets = list(" + " ".join(_sk(n) for n in nets) + ")")
    bands = nets_data.get("em_bands") or EM_BANDS
    L.append("emirBands = list(")
    for i, (lo, lab, step) in enumerate(bands):
        L.append(f"  list({_sk(i)} {_sk(lo)} {_sk(lab)} {_sk(int(step))})")
    L.append(")")
    # the metrics some net checked: the Explorer offers only these (a metric
    # with no number is nil in the rows below)
    checked = [m for m in ("avg", "rms", "peak")
               if any(v["ratios"].get(m) is not None for v in nets_data["em"])] or ["avg"]
    L.append("emirMetrics = list(" + " ".join(_sk(m) for m in checked) + ")")

    def ma(x):                      # mA, or nil for a metric not checked
        return None if x is None else x * 1e3
    L.append("emirViol = list(")
    for v in nets_data["em"]:
        r = v["rect"]
        L.append(f'  list({_sk(v["net"])} {_sk(v["name"])} {_sk(v["layer"])} '
                 f'{_sk(v["kind"])} {_sk(v["metric"])} {_sk(v["ratio"])} '
                 f'{_sk(v["value"] * 1e3)} {_sk(v["current"] * 1e3)} '
                 f'{_sk(r[0])} {_sk(r[1])} {_sk(r[2])} {_sk(r[3])} '
                 f'{_sk(int(v["band"]))} {_sk(v["hint"])} '
                 f'{_sk(v["ratios"]["avg"])} {_sk(v["ratios"]["rms"])} '
                 f'{_sk(v["ratios"]["peak"])} '
                 # geometry + per-metric numbers, so the viewer can re-derive
                 # the fix for whichever metric is selected
                 f'{_sk(float(v["w"] or 0.0))} {_sk(int(v["cuts"] or 0))} '
                 f'{_sk(ma(v["values"]["avg"]))} '
                 f'{_sk(ma(v["values"]["rms"]))} '
                 f'{_sk(ma(v["values"]["peak"]))} '
                 f'{_sk(ma(v["currents"]["avg"]))} '
                 f'{_sk(ma(v["currents"]["rms"]))} '
                 f'{_sk(ma(v["currents"]["peak"]))})')
    L.append(")")
    L.append("emirIR = list(")
    for v in nets_data["ir"]:
        r = v["rect"]
        L.append(f'  list({_sk(v["net"])} {_sk(v["mv"])} {_sk(int(v["band"]))} '
                 f'{_sk(r[0])} {_sk(r[1])} {_sk(r[2])} {_sk(r[3])} '
                 # the resistor name, so the IR list can identify a segment
                 # the same way the EM list does
                 f'{_sk(v["name"])})')
    L.append(")")
    L.append("emirIRBands = list(")
    for b in nets_data["ir_bands"]:
        L.append(f'  list({_sk(int(b["band"]))} {_sk(b["lo_mv"])} {_sk(b["hi_mv"])})')
    L.append(")")
    L.append(f'emirIRMax = {_sk(nets_data["ir_max_mv"])}')
    from . import site
    ml = site.get("mark_layer")[0]
    if isinstance(ml, str):
        ml = ml.replace("/", " ").split()
    layer, purpose = (list(ml) + ["drawing"])[:2] if ml else (MARK_LAYER, "drawing")
    L.append(f'emirMarkLayer = {_sk(str(layer))}')
    L.append(f'emirMarkPurpose = {_sk(str(purpose))}')
    L.append("emirRails = list(" +
             " ".join(_sk(n) for n in nets_data.get("rails") or []) + ")")

    # the legend lives in the form, as an image of the same ramp the layout
    # uses -- drawing it in the layout cost text primitives on every repaint
    d = os.path.dirname(os.path.abspath(path))
    em_idx = [step for _, _, step in bands]
    ir_idx = list(range(N_STEPS))
    em_lab, ir_lab = bar_labels(bands, nets_data["ir_max_mv"], len(ir_idx))
    em_png = colour_bar(os.path.join(d, "emir_em_bar.png"), em_idx, em_lab)
    ir_png = colour_bar(os.path.join(d, "emir_ir_bar.png"), ir_idx, ir_lab)
    L.append(f'emirEmBarPng = {_sk(em_png)}')
    L.append(f'emirIrBarPng = {_sk(ir_png)}')
    # the numbers live in the image now; these are just captions
    L.append('emirEmTicks = ' + _sk("EM   segment current / limit"))
    L.append('emirIrTicks = ' + _sk(
        "IR   |V - V(pin)| in mV   -- droop on a supply, bounce on a ground"))
    L.append('printf("EMIR: loaded %d violations, %d IR segments on %d nets. '
             + SCREENING_NOTE + '\\n" '
             'length(emirViol) length(emirIR) length(emirNets))')
    from .output import write_text
    return write_text(path, "\n".join(L) + "\n", code="EMIR-E701")


def trim(em, ir, rails, cap=VIEWER_MAX) -> tuple:
    """Bound the viewer data of a big run. `(em, ir, info)`; `info` is None
    when nothing was left out.

    EM: when there are more than `cap` segments, those below
    `VIEWER_TRIM_RATIO` are left out, then the worst `cap` kept. IR: when
    there are more than `cap`, only the rails' segments (all of them when no
    net is a rail), the `cap` largest drops. Below the cap nothing changes, so
    a small block keeps every segment, clean ones and bias lines included.
    Measured on a synthetic 50 000-resistor chain: 10 977 518 bytes untrimmed,
    1 190 721 trimmed. A projection, not a measurement: a 1M-resistor block
    untrimmed would give an emir_data.il of some 220 MB.
    """
    n_em, n_ir = len(em), len(ir)
    if n_em > cap:
        em = sorted((v for v in em if v["ratio"] >= VIEWER_TRIM_RATIO),
                    key=lambda c: -c["ratio"])[:cap]
    if n_ir > cap:
        pool = [r for r in ir if r["net"] in set(rails)] or ir
        ir = sorted(pool, key=lambda r: -r["mv"])[:cap]
    if len(em) == n_em and len(ir) == n_ir:
        return em, ir, None
    return em, ir, {"cap": cap, "em_total": n_em, "em_kept": len(em),
                    "ir_total": n_ir, "ir_kept": len(ir)}


def export(results, dspf, proc, out_dir, lib=None, cell=None,
           include_clean=True, rails=None, log=print, all_segments=False) -> dict:
    """Write everything the viewers need. `results`: {net: res dict}.

    Refused, before anything is written: a control character in the library,
    the cell or the DSPF design name, and any net whose results hold a NaN or
    an infinity (the viewer would draw a number that is not one). A big run
    is trimmed (`trim`) unless `all_segments`.
    """
    cell = cell or (dspf.design or "top")
    bad = check_viewer_names(lib, cell, dspf.design)
    if bad:
        raise EmirError("EMIR-E702", "viewer data not written: " + "; ".join(bad))
    nonfinite = sorted(n for n, r in results.items() if not _finite_result(r))
    if nonfinite:
        raise EmirError("EMIR-E703", f"viewer data not written: the results of net(s) "
                         f"{', '.join(nonfinite)} hold NaN or Inf -- check the "
                         f"currents or the simulation result")
    from .output import make_dir, write_text
    make_dir(out_dir, code="EMIR-E701")
    em, ir = [], []
    for net, res in sorted(results.items()):
        em += violations(res, dspf, net, proc, include_clean=include_clean)
        items, _ = ir_segments(res, dspf, net)
        ir += items
    # IR is a PDN quantity by convention, so the rails set the scale and are
    # what the viewer opens on; other nets stay available because on analog
    # bias and reference lines the drop is a real error term
    rails = list(rails) if rails else power_nets(sorted(results))
    em.sort(key=lambda c: -c["ratio"])
    trimmed = None
    if not all_segments:
        em, ir, trimmed = trim(em, ir, rails)
        if trimmed:
            notice("EMIR-I780", f"viewer data trimmed: {trimmed['em_total']} EM segments -> "
                   f"{trimmed['em_kept']} (below {VIEWER_TRIM_RATIO}x left out, worst "
                   f"{trimmed['cap']} kept), {trimmed['ir_total']} IR segments -> "
                   f"{trimmed['ir_kept']} (the rails, largest drops first); --viewer-all "
                   f"writes every segment", log)
    ir_max, ir_bands = band_ir(ir, scale_nets=set(rails))
    data = {"em": em, "ir": ir, "ir_bands": ir_bands, "ir_max_mv": ir_max,
            "rails": rails, "lib": lib, "cell": cell,
            "em_bands": [list(b) for b in em_bands(getattr(proc, "warn_fraction", 0.8))],
            "palette": [f"#{r:02x}{g:02x}{b:02x}" for r, g, b in BAND_RGB]}

    j = write_text(os.path.join(out_dir, "emir_viz.json"), json.dumps(data, indent=1),
                   code="EMIR-E701")
    d = skill_data(data, lib, cell, os.path.join(out_dir, "emir_data.il"))
    app = os.path.join(out_dir, "emir_explorer.il")
    _write_app(app)
    rdb = klayout_rdb(data, os.path.join(out_dir, "emir.lyrdb"), cell=cell)
    if rdb is None:
        from .repo import install_hint
        notice("EMIR-W703", "the KLayout marker database (emir.lyrdb) was not written: "
               f"the klayout Python package is not installed ({install_hint('klayout')})",
               log)
    n_fail = sum(1 for v in em if v["ratio"] >= 1.0)
    log(f"[emir] viewer data: {len(em)} segments ({n_fail} failing), "
        f"{len(ir)} IR segments, IR range 0..{ir_max:.3f} mV "
        f"scaled on {', '.join(rails) if rails else 'all nets'}")
    log("[emir] Virtuoso -- in the CIW:")
    log(f'         load("{os.path.abspath(d)}")')
    log(f'         load("{os.path.abspath(app)}")')
    log("         emirExplorer()")
    if rdb:
        log(f"[emir] KLayout:   klayout <layout.gds> -m {os.path.abspath(rdb)}")
    return {"json": j, "data": d, "app": app, "rdb": rdb, "n_em": len(em),
            "n_fail": n_fail, "n_ir": len(ir), "trimmed": trimmed}


def _finite_result(res) -> bool:
    """Every potential, branch current and ratio of one net is a finite number."""
    import numpy as np
    arrays = [res.get("V"), *(res.get("Ie") or {}).values()]
    if any(a is not None and not np.all(np.isfinite(np.asarray(a, dtype=float)))
           for a in arrays):
        return False
    mr = res.get("max_ratio")
    vals = list(mr.values()) if isinstance(mr, dict) else [mr]
    vals.append(res.get("ir_drop"))
    return all(v is None or math.isfinite(float(v)) for v in vals)


def _write_app(path) -> str:
    from .output import write_text
    from .skillapp import APP
    return write_text(path, APP, code="EMIR-E701")


# ---------------------------------------------------------------- KLayout


def klayout_rdb(data, path, cell=None) -> str | None:
    """A KLayout marker database: categories give filtering and zoom for free.

    Categories are `EM/<net>/<band label>` and `IR/<net>/<mV band>`, so the
    Marker Browser's own tree is the filter — pick a net, pick a severity, step
    through the items. Each marker carries its numbers and its fix hint in the
    description, which is what the browser shows on selection.

    Not yet checked in the KLayout GUI.
    """
    try:
        import klayout.rdb as krdb
    except ImportError:
        return None
    db = krdb.ReportDatabase("EMIR")
    cell = cell or data.get("cell") or "top"
    rcell = db.create_cell(cell)
    db.description = (f"EMIR screening -- {len(data['em'])} checked segments, "
                      f"{sum(1 for v in data['em'] if v['ratio'] >= 1.0)} failing. "
                      f"{SCREENING_NOTE}")
    top = {}

    def cat(path_parts, desc=""):
        key = "/".join(path_parts)
        if key in top:
            return top[key]
        parent = None
        for i in range(len(path_parts)):
            k = "/".join(path_parts[:i + 1])
            if k not in top:
                top[k] = (db.create_category(parent, path_parts[i]) if parent
                          else db.create_category(path_parts[i]))
                if i == len(path_parts) - 1 and desc:
                    top[k].description = desc
            parent = top[k]
        return top[key]

    for v in data["em"]:
        if v["ratio"] < 0.5:            # a browser full of clean shapes is noise
            continue
        lab = (data.get("em_bands") or EM_BANDS)[v["band"]][1]
        c = cat(["EM", v["net"], lab])
        it = db.create_item(rcell.rdb_id(), c.rdb_id())
        it.add_value(krdb.RdbItemValue(
            f"{v['name']} {v['layer']} {v['kind']} {v['metric']} "
            f"{v['ratio']:.3f}x  I={v['current'] * 1e3:.4f} mA  {v['hint']}"
            + (f"  ({v['basis']})" if v.get("basis") else "")))
        x0, y0, x1, y1 = v["rect"]
        it.add_value(krdb.RdbItemValue(
            __import__("klayout.db", fromlist=["db"]).DBox(x0, y0, x1, y1)))

    for r in data["ir"]:
        b = data["ir_bands"][r["band"]]
        c = cat(["IR", r["net"], f"{b['lo_mv']:.3f}-{b['hi_mv']:.3f} mV"])
        it = db.create_item(rcell.rdb_id(), c.rdb_id())
        it.add_value(krdb.RdbItemValue(f"{r['name']}  {r['mv']:.4f} mV"))
        x0, y0, x1, y1 = r["rect"]
        it.add_value(krdb.RdbItemValue(
            __import__("klayout.db", fromlist=["db"]).DBox(x0, y0, x1, y1)))

    db.save(path)
    return path
