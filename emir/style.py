"""EMIR's one colour scale, its legend and its numbers: the style every
renderer draws with.

The PNG charts (`emir.chart`: the run and layout charts of `--html`), the
HTML pages (`emir.netreport`, `emir.dashboard`), the Virtuoso Explorer's
colour packets and the KLayout layer properties EMIR writes
(`emir.layoutview`) and the figures of the docs and the blog
(`scripts/make_figures.py`) all take their colours, their legend words and
their number formats from here, so a colour means the same thing everywhere.

The scale, on the layout's black canvas:

* **EM**, by an element's governing ratio (its current over its limit):
  below the warn band, one blue ramp by the ratio's log10, from `EM_FLOOR`
  (dark, `EM_RAMP[0]`) to the warn fraction (light, `EM_RAMP[-1]`); the warn
  band (the warn fraction to 1x) is `WARN`; at 1x and over, **or not a
  number**, `FAIL`; an element with no limit stated is `UNCHECKED`, grey.
* **IR**, by a drop: the same ramp from 0 (dark) to the largest drop shown
  (light); a drop that is not a number is `FAIL`, never a shade.
* Violations are outlined in `MARKER`; the cell outline is dashed `OUTLINE`;
  the layout under the results is dimmed to `UNDERLAY_ALPHA` on `CANVAS`.

The legend names every colour (`legend`), never colour alone, and a number
that is not a number prints as "not a number", never as 0 (`num`).

This module imports no plotting library (the HTML reports must work without
matplotlib), and only the standard library at import (the Cadence files'
generator, `scripts/gen_cadence_skill.py`, runs without numpy). `emir.chart`
draws with matplotlib.

Ported from Skyforge: the scale (`EM_RAMP`, `ramp`, `em_colour`, `EM_FLOOR`,
`UNCHECKED`, `MARKER`, `OUTLINE`), the legend's words (`legend`,
`not_checked_text`, `colour_key`) and the number format (`num`). Skyforge is
Copyright (c) 2026 Skycadeda, licensed under the MIT License by its copyright
holder (THIRD_PARTY_NOTICES.md).
"""
from __future__ import annotations

import math

# ---------------------------------------------------------------- the scale

#: the blue ramp below the warn band, dark (EM_FLOOR) to light (the band)
EM_RAMP = ("#104281", "#1c5cab", "#2a78d6", "#5598e7", "#86b6ef", "#b7d3f6")
#: the warn band: from the warn fraction up to 1x
WARN = "#fab219"
#: at or over the limit, or not a number
FAIL = "#d03b3b"
#: an element whose limit the process file does not state: not checked
UNCHECKED = "#6b6b6b"
#: the outline of a violation's marker
MARKER = "#ff3030"
#: a block model's element (a block abstract's port-to-port conductance in a
#: chip-level run): dotted, not on the scale -- its EM is checked in the
#: block's own abstract run, not here
MODEL = "#e6e6e6"
MODEL_TEXT = "block model: EM checked in its abstract"
#: its legend key (short: the chart's note says the rest)
MODEL_KEY = "block model (its abstract)"
#: the ratio the ramp starts from (its darkest colour): 1e-3x
EM_FLOOR = 1e-3
#: the warn fraction when the process file states none
WARN_DEFAULT = 0.8
#: the layout's canvas, in every theme (KLayout's black)
CANVAS = "#000000"
#: the layout drawn under the results is dimmed to this opacity
UNDERLAY_ALPHA = 0.35
#: the cell outline, dashed
OUTLINE = "#2fb3ab"
#: the rings round each net's worst IR node
RING = "#ffffff"
#: the axes' ink around the black canvas
AXIS_INK = "#8a8f94"

#: the status colours of the page chrome (verdict chips, table cells): the
#: same three as the map, so a FAIL reads the same in a table and on a shape
STATUS = {"FAIL": FAIL, "WARN": WARN, "CLEAN": "#1f9a5a"}
STATUS_INK = {"FAIL": "#ffffff", "WARN": "#241a00", "CLEAN": "#ffffff"}

#: the page around the canvas: light and dark, each chosen against its own
#: surface (the canvas itself stays black in both)
THEMES = {
    "dark": {"page": "#0d0d0d", "panel": "#171716", "card": "#1d1d1b", "ink": "#ffffff",
             "ink2": "#c3c2b7", "muted": "#8f8e88", "grid": "#2c2c2a", "axis": "#4a4a46",
             "zebra": "#1a1a19", "head": "#242422", "pass": "#3fbf7f",
             "fail_ink": "#ff6b6b", "warn_ink": "#fab219"},
    "light": {"page": "#ffffff", "panel": "#f2f6f7", "card": "#ffffff", "ink": "#152e43",
              "ink2": "#3d5566", "muted": "#536b7b", "grid": "#dce5e9", "axis": "#b8c4ca",
              "zebra": "#f6f9fa", "head": "#e3ecef", "pass": "#177a55",
              "fail_ink": "#a32738", "warn_ink": "#a86b0c"},
}


def tokens(theme: str = "dark") -> dict:
    """The page colours of a theme (dark unless "light")."""
    return THEMES["light" if theme == "light" else "dark"]


def finite(v) -> bool:
    """A real number that is neither NaN nor infinite (None is not one)."""
    try:
        return v is not None and math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def hex_rgb(h: str) -> tuple:
    """"#1c5cab" -> (28, 92, 171)."""
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def rgb_hex(rgb) -> str:
    return "#" + "".join(f"{int(c):02x}" for c in rgb)


def ramp(t) -> str:
    """The colour at `t` (0..1, clamped) along `EM_RAMP`, interpolated in
    sRGB and rounded per channel; a `t` that is not a number is `FAIL`."""
    if not finite(t):
        return FAIL
    n = len(EM_RAMP) - 1
    x = min(1.0, max(0.0, float(t))) * n
    k = min(n - 1, int(x))
    f = x - k
    a, b = hex_rgb(EM_RAMP[k]), hex_rgb(EM_RAMP[k + 1])
    return rgb_hex(round(p + (q - p) * f) for p, q in zip(a, b))


def em_t(ratio, warn: float = WARN_DEFAULT) -> float:
    """Where a ratio below the warn band sits on the ramp: 0 at `EM_FLOOR`
    (and below), 1 at the warn fraction, by log10."""
    lo, hi = math.log10(EM_FLOOR), math.log10(max(warn, EM_FLOOR * 10))
    return min(1.0, max(0.0, (math.log10(max(float(ratio), EM_FLOOR)) - lo) / (hi - lo)))


def em_level(ratio, warn: float = WARN_DEFAULT) -> str:
    """"unchecked" (None: no limit stated), "fail" (1x and over, or not a
    number), "warn" (the warn fraction to 1x) or "ok"."""
    if ratio is None:
        return "unchecked"
    if not finite(ratio) or float(ratio) >= 1.0:
        return "fail"
    return "warn" if float(ratio) >= warn else "ok"


def em_colour(ratio, warn: float = WARN_DEFAULT) -> str:
    """A governing ratio's colour: `FAIL` at 1x and over or not a number,
    `WARN` in the warn band, `UNCHECKED` for None (no limit), else the ramp."""
    lv = em_level(ratio, warn)
    if lv == "unchecked":
        return UNCHECKED
    if lv == "fail":
        return FAIL
    if lv == "warn":
        return WARN
    return ramp(em_t(ratio, warn))


def ir_colour(drop, ir_max) -> str:
    """A drop's colour: the ramp from 0 to `ir_max`; not a number is `FAIL`."""
    if not finite(drop):
        return FAIL
    top = float(ir_max) if finite(ir_max) and float(ir_max) > 0 else 0.0
    return ramp(abs(float(drop)) / top if top else 0.0)


# ------------------------------------------------------------- the numbers


def num(v, digits: int = 3) -> str:
    """A number as every chart prints it: `digits` significant digits (kept,
    so 1.00 stays 1.00), a numerical zero (below 1e-6) as ~0, below 1e-3 in
    exponent form, 1000 and over with thousands separators, and a value that
    is not a number as "not a number" -- never as 0."""
    if not finite(v):
        return "not a number"
    v = float(v)
    a = abs(v)
    if a < 1e-6:
        return "~0"
    if a < 1e-3:
        return f"{v:.{digits - 1}e}".replace("e-0", "e-")
    if a >= 1000:
        return f"{v:,.0f}"
    return f"{v:#.{digits}g}".rstrip(".")


def fmt_ratio(r, sign: str = "×") -> str:
    """A ratio: "2.48×", "0.0123×", "~0×", "not a number"."""
    s = num(r)
    return s if s == "not a number" else s + sign


def fmt_mv(volts) -> str:
    """A drop given in volts, printed in mV: "7.82 mV"."""
    s = num(float(volts) * 1e3 if finite(volts) else volts)
    return s if s == "not a number" else s + " mV"


# ----------------------------------------------------------------- the legend


def em_ticks(warn: float = WARN_DEFAULT) -> list:
    """The EM bar's ticks: [(position 0..1, label)] at 0.001x, 0.01x, 0.1x
    and the warn fraction."""
    out = []
    for v in (EM_FLOOR, 0.01, 0.1):
        if v < warn * 0.75:
            out.append((em_t(v, warn), f"{v:g}×"))
    out.append((1.0, f"{warn:g}×"))
    return out


def ir_ticks(ir_max: float) -> list:
    """The IR bar's ticks: 0, half and the largest drop shown (volts in,
    mV labels)."""
    if not finite(ir_max) or ir_max <= 0:
        return [(0.0, "0")]
    return [(0.0, "0"), (0.5, fmt_mv(ir_max / 2)), (1.0, fmt_mv(ir_max))]


def legend(mode: str = "em", warn: float = WARN_DEFAULT, ir_max: float | None = None,
           unchecked: bool = False, lost: bool = False, markers: bool = True,
           rings: bool = True, model: bool = False) -> dict:
    """What a chart's legend says, for any renderer: `title`, `sub`, the
    gradient `bar` (its `stops` and `ticks`, positions 0..1) and the `keys`,
    each (kind, colour, text) with kind "swatch", "outline", "dotted" (a block
    model's element: `model`) or "ring". Every
    colour on the map is named here; nothing is colour alone."""
    stops = [(i / (len(EM_RAMP) - 1), c) for i, c in enumerate(EM_RAMP)]
    if mode == "ir":
        keys = []
        if rings:
            keys.append(("ring", RING, "each net's worst node"))
        if lost:
            keys.append(("swatch", FAIL, "IR drop not a number (a failed current)"))
        if model:
            keys.append(("dotted", MODEL, MODEL_KEY))
        return {"title": "IR drop", "sub": "each element by the larger drop of its two ends, "
                "from the net's feed", "bar": {"stops": stops, "ticks": ir_ticks(ir_max or 0)},
                "keys": keys}
    keys = [("swatch", EM_RAMP[3], f"ok: below {warn:g}× (shade by ratio)"),
            ("swatch", WARN, f"warn: {warn:g}× to 1×"),
            ("swatch", FAIL, "fail: 1× and over, or not a number")]
    if markers:
        keys.append(("outline", MARKER, "violation marker"))
    if unchecked:
        keys.append(("swatch", UNCHECKED, "no limit stated: not checked"))
    if model:
        keys.append(("dotted", MODEL, MODEL_KEY))
    return {"title": "EM ratio", "sub": "current over its limit, the governing metric "
            f"(log scale from {EM_FLOOR:g}×)", "bar": {"stops": stops, "ticks": em_ticks(warn)},
            "keys": keys}


def colour_key(mode: str = "em", warn: float = WARN_DEFAULT) -> str:
    """The legend in one line, for a caption or a slide."""
    if mode == "ir":
        return ("Colour: each element by its IR drop from the net's feed, on the same blue "
                "ramp from 0 (dark) to the largest drop (light); red: not a number; white "
                "rings: each net's worst node.")
    return (f"Colour: each wire segment and via array by its governing ratio (current over "
            f"its limit): blue shades below {warn:g}× (log scale from {EM_FLOOR:g}×), amber "
            f"{warn:g}× to 1×, red at 1× and over or not a number; grey: no limit stated; "
            f"violations outlined red.")


def css_gradient(direction: str = "to right") -> str:
    """The ramp as a CSS linear-gradient (the HTML legends)."""
    n = len(EM_RAMP) - 1
    return (f"linear-gradient({direction}, "
            + ", ".join(f"{c} {100 * i / n:.0f}%" for i, c in enumerate(EM_RAMP)) + ")")


def not_checked_text(missing) -> str:
    """[{"name", "kind", "metrics"}] -> "peak: met1, met2 · rms: via" (by
    metric)."""
    by: dict = {}
    for e in missing or []:
        for m in e.get("metrics") or []:
            by.setdefault(m, []).append(e["name"])
    return " · ".join(f"{m}: {', '.join(names)}" for m, names in by.items())
