"""EMIR's EM/IR chart: the layout on a black canvas, dimmed, with every
checked element filled by its ratio (or its IR drop), beside the verdict, the
worst items numbered as on the map, one row per net, what was not checked
and a legend that names every colour. Screening, not sign-off.

One renderer for every PNG EMIR draws: the run and layout charts of `--html`
(`write_run_charts`) and the figures of the docs and the blog
(`scripts/make_figures.py`). The colours, the legend
words and the number formats are `emir.style`'s; the data is a scene
(`emir.scene`).

The style: the layout rendered by KLayout (`emir.underlay`) and dimmed on
black, the elements on top with the worst drawn last, violations outlined red,
the cell outline dashed, the axes in um; the IR view colours each element by
its drop on the same ramp and rings each net's worst node, the three worst
labelled.

matplotlib is imported at module level: only the chart writers import this
module, after checking that matplotlib is installed (`scene.write_charts`).

The map's drawing (the dimmed underlay on black, the elements by colour with
the worst last, the red markers, the dashed outline), the per-net table (net,
worst EM, at, IR worst) and `fit_window` are ported in part from Skyforge; the
rest is EMIR's own.
Skyforge is Copyright (c) 2026 Skycadeda, licensed under the MIT License by its copyright
holder (THIRD_PARTY_NOTICES.md).
"""
from __future__ import annotations

import math
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.font_manager import FontProperties  # noqa: E402
from matplotlib.patches import Circle, Rectangle  # noqa: E402

from . import RESEARCH_DISCLAIMER, SCREENING_NOTE  # noqa: E402
from . import style as S  # noqa: E402

#: every size below is in pixels: a chart is W x H px at this dpi
DPI = 100
SANS = "DejaVu Sans"
MONO = "DejaVu Sans Mono"
#: the default chart: 16:9, legible at LinkedIn's 1200 px
SIZE = (1600, 900)
#: the smallest an element is drawn, in pixels (a via of 0.15 um on a 40 um
#: layout is 4 px; below this it would vanish)
MIN_PX = 3.0


#: text widths measured so far, by (text, size, bold, italic, mono): a run
#: draws each chart twice (light, dark) after sizing it once
_WIDTHS: dict = {}


def fs(px: float) -> float:
    """A font size given in pixels, in points."""
    return px * 72.0 / DPI


def pt(px: float) -> float:
    return px * 72.0 / DPI


# ------------------------------------------------------------------ the page


class Page:
    """A figure in pixel coordinates (y down) whose text is measured in
    pixels, so a layout can wrap, fit and stack it without overlap."""

    def __init__(self, w: int, h: int, theme: str = "dark", fig=None, ax=None):
        self.w, self.h, self.theme = w, h, theme
        self.t = S.tokens(theme)
        if fig is None:
            fig = plt.figure(figsize=(w / DPI, h / DPI), dpi=DPI)
            fig.patch.set_facecolor(self.t["page"])
            ax = fig.add_axes((0, 0, 1, 1))
            ax.set_xlim(0, w)
            ax.set_ylim(h, 0)
            ax.axis("off")
            ax.set_autoscale_on(False)          # an image or a patch never moves the page
        self.fig, self.ax = fig, ax
        self._r = fig.canvas.get_renderer()
        self._fp: dict = {}

    def font(self, px, bold=False, italic=False, mono=False) -> FontProperties:
        key = (px, bold, italic, mono)
        if key not in self._fp:
            self._fp[key] = FontProperties(family=MONO if mono else SANS, size=fs(px),
                                           weight="bold" if bold else "normal",
                                           style="italic" if italic else "normal")
        return self._fp[key]

    def width(self, s: str, px, bold=False, italic=False, mono=False) -> float:
        """The width of `s` in pixels (measured by the Agg renderer once per
        string and font: a chart wraps and fits hundreds of strings)."""
        if not s:
            return 0.0
        key = (s, px, bold, italic, mono)
        w = _WIDTHS.get(key)
        if w is None:
            w, _h, _d = self._r.get_text_width_height_descent(
                s, self.font(px, bold, italic, mono), ismath=False)
            w = _WIDTHS[key] = w * DPI / self.fig.dpi
        return w

    def text(self, x, y, s, px, color, bold=False, italic=False, ha="left", va="top",
             mono=False, z=5, **kw):
        return self.ax.text(x, y, s, fontproperties=self.font(px, bold, italic, mono),
                            color=color, ha=ha, va=va, zorder=z, parse_math=False, **kw)

    def fit(self, s: str, px, width, bold=False, italic=False, mono=False) -> str:
        """`s`, cut with an ellipsis to fit `width` pixels."""
        if self.width(s, px, bold, italic, mono) <= width:
            return s
        # the cut from the characters' own widths, then checked as a whole
        acc, cut = self.width("…", px, bold, italic, mono), 0
        for ch in s:
            acc += self.width(ch, px, bold, italic, mono)
            if acc > width:
                break
            cut += 1
        while cut > 0 and self.width(s[:cut].rstrip() + "…", px, bold, italic, mono) > width:
            cut -= 1
        return s[:cut].rstrip() + "…"

    def wrap(self, s: str, px, width, bold=False, italic=False, mono=False) -> list:
        """`s` broken at spaces into lines of at most `width` pixels (a word
        longer than a line is cut with an ellipsis)."""
        space = self.width("a a", px, bold, italic, mono) - 2 * self.width("a", px, bold,
                                                                           italic, mono)
        lines, cur, cw = [], "", 0.0
        for word in s.split(" "):
            ww = self.width(word, px, bold, italic, mono)
            if cur and cw + space + ww <= width:
                cur, cw = f"{cur} {word}", cw + space + ww
                continue
            if not cur and ww <= width:
                cur, cw = word, ww
                continue
            if cur:
                lines.append(cur)
            cur = self.fit(word, px, width, bold, italic, mono)
            cw = self.width(cur, px, bold, italic, mono)
        if cur:
            lines.append(cur)
        return lines

    def para(self, x, y, s, px, width, color, bold=False, italic=False, lead=1.32,
             max_lines=None) -> float:
        """A wrapped paragraph from (x, y); returns the y below it."""
        lines = self.wrap(s, px, width, bold, italic)
        if max_lines and len(lines) > max_lines:
            lines = lines[:max_lines]
            lines[-1] = self.fit(lines[-1] + " …", px, width, bold, italic)
        for ln in lines:
            self.text(x, y, ln, px, color, bold, italic)
            y += px * lead
        return y

    def rect(self, x, y, w, h, fc, ec="none", lw=1.0, z=1, ls="-"):
        self.ax.add_patch(Rectangle((x, y), w, h, facecolor=fc, edgecolor=ec, lw=lw,
                                    zorder=z, ls=ls))

    def axes(self, x, y, w, h):
        """An axes on the pixel box (x, y) w x h (y down)."""
        return self.fig.add_axes((x / self.w, 1 - (y + h) / self.h, w / self.w, h / self.h))

    def save(self, path: str) -> str:
        self.fig.savefig(path, dpi=DPI, facecolor=self.t["page"])
        plt.close(self.fig)
        return path


# -------------------------------------------------------------- the window


def scene_window(scene: dict, pad: float = 0.04) -> list:
    """[x0, x1, y0, y1] um: the scene's own window when it sets one, else
    everything drawn (elements, outline), padded."""
    if scene.get("window"):
        return list(scene["window"])
    boxes = [s["box"] for s in scene.get("shapes") or []]
    if scene.get("outline"):
        boxes.append(scene["outline"])
    xs = [b[0] for b in boxes] + [b[2] for b in boxes]
    ys = [b[1] for b in boxes] + [b[3] for b in boxes]
    if not xs:
        return [0.0, 1.0, 0.0, 1.0]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    span = max(x1 - x0, y1 - y0, 1e-3)
    px, py = max((x1 - x0) * pad, span * 0.02), max((y1 - y0) * pad, span * 0.02)
    return [x0 - px, x1 + px, y0 - py, y1 + py]


def fit_window(win, w: float, h: float) -> list:
    """`win` grown to the pixel box's aspect, so um map to pixels exactly."""
    x0, x1, y0, y1 = win
    ww, wh = max(x1 - x0, 1e-6), max(y1 - y0, 1e-6)
    if ww / wh > w / h:
        grow = ww * h / w - wh
        y0, y1 = y0 - grow / 2, y1 + grow / 2
    else:
        grow = wh * w / h - ww
        x0, x1 = x0 - grow / 2, x1 + grow / 2
    return [x0, x1, y0, y1]


def ir_max(scene: dict) -> float:
    """The top of the IR ramp (V): the scene's `ir_top` when it sets one (so
    several maps can share one scale), else the largest finite drop it
    shows."""
    if S.finite(scene.get("ir_top")) and scene["ir_top"] > 0:
        return float(scene["ir_top"])
    vals = [s["ir"] for s in scene.get("shapes") or [] if S.finite(s.get("ir"))]
    vals += [n["ir"] for n in scene.get("nets") or [] if S.finite(n.get("ir"))]
    return max(vals, default=0.0)


# ------------------------------------------------------------ the layout panel


class _Placer:
    """Labels and badges placed in pixels so none overlaps another. What is
    taken is hard (a label, a badge: never covered) or soft (a marker, an
    item's own spot: covered only when nothing else is free)."""

    def __init__(self, ax, bounds):
        self.ax, self.bounds, self.taken = ax, bounds, []

    def px(self, x, y):
        return self.ax.transData.transform((x, y)) * DPI / self.ax.figure.dpi

    def free(self, cx, cy, w, h, soft=True) -> bool:
        x0, y0, x1, y1 = self.bounds
        if cx - w / 2 < x0 + 2 or cx + w / 2 > x1 - 2 or cy - h / 2 < y0 + 2 or cy + h / 2 > y1 - 2:
            return False
        return all(abs(cx - a) * 2 >= w + aw or abs(cy - b) * 2 >= h + bh
                   for a, b, aw, bh, hard in self.taken if hard or soft)

    def place(self, x, y, w, h, offsets):
        """The first offset (dx, dy px, y up) whose box is free of everything,
        else of every label and badge (at up to three times the distance);
        taken."""
        ax_, ay_ = self.px(x, y)
        tries = [(offsets, True), (offsets, False),
                 ([(2 * a, 2 * b) for a, b in offsets], False),
                 ([(3 * a, 3 * b) for a, b in offsets], False)]
        for offs, soft in tries:
            for dx, dy in offs:
                cx, cy = ax_ + dx, ay_ + dy
                if self.free(cx, cy, w, h, soft):
                    self.taken.append((cx, cy, w, h, True))
                    return dx, dy
        dx, dy = offsets[0]
        self.taken.append((ax_ + dx, ay_ + dy, w, h, True))
        return dx, dy

    def block(self, x, y, w, h, hard=False):
        a, b = self.px(x, y)
        self.taken.append((a, b, w, h, hard))

    def block_px(self, cx, cy, w, h, hard=True):
        self.taken.append((cx, cy, w, h, hard))

    def block_box(self, box, pad=4):
        """A box in um ([x0, y0, x1, y1]) a label covers only as a last resort."""
        (a, b), (c, d) = self.px(box[0], box[1]), self.px(box[2], box[3])
        self.taken.append(((a + c) / 2, (b + d) / 2, abs(c - a) + 2 * pad,
                           abs(d - b) + 2 * pad, False))

    def inside(self, x, y) -> bool:
        a, b = self.px(x, y)
        x0, y0, x1, y1 = self.bounds
        return x0 <= a <= x1 and y0 <= b <= y1


_BADGE_OFFSETS = [(14, 14), (-14, 14), (14, -14), (-14, -14), (0, 22), (24, 0), (-24, 0),
                  (0, -22), (26, 24), (-26, 24), (26, -24), (-26, -24), (0, 40), (0, -40),
                  (40, 0), (-40, 0)]
_LABEL_OFFSETS = [(1, 1), (-1, 1), (1, -1), (-1, -1), (1, 2.2), (-1, 2.2), (1, -2.2),
                  (-1, -2.2), (0, 3.4), (0, -3.4)]


def _underlay(ax, ul: dict | None, win, w: int, h: int) -> str:
    """The layout under the results, dimmed on black; how it was drawn."""
    if not ul or not ul.get("gds") or not os.path.isfile(ul["gds"]):
        return ""
    from . import underlay as U
    img, how = U.render(ul["gds"], ul.get("cell"), (win[0], win[2], win[1], win[3]),
                        int(round(w)), int(round(h)), ul.get("lyp"))
    if img is not None:
        ax.imshow(img, extent=(win[0], win[1], win[2], win[3]), origin="upper",
                  interpolation="none", alpha=S.UNDERLAY_ALPHA, zorder=1, aspect="auto")
        return f"layout by KLayout, {how}"
    polys = U.polygons(ul["gds"], ul.get("cell"))
    if not polys:
        return ""
    from matplotlib.collections import PolyCollection
    for k, (_ld, ps) in enumerate(sorted(polys.items())):
        g = U.GREYS[k % len(U.GREYS)]
        col = f"#{g:06x}"
        ax.add_collection(PolyCollection(ps, facecolors=col, edgecolors=col, linewidths=0.4,
                                         alpha=S.UNDERLAY_ALPHA * 0.6, zorder=1))
    return "layout by gdstk, a neutral grey per layer"


def _shapes(ax, scene, mode, win, w, h, top):
    """Every element painted into one image at the panel's own pixels, the
    worst painted last (on top), none smaller than `MIN_PX`: a block's tens of
    thousands of elements draw in a fraction of a second, which as patches
    took seconds."""
    W, H = max(1, int(round(w))), max(1, int(round(h)))
    sx, sy = W / (win[1] - win[0]), H / (win[3] - win[2])
    warn = scene.get("warn", S.WARN_DEFAULT)
    shapes = [s for s in scene.get("shapes") or [] if not s.get("model")]
    _models(ax, scene)
    if mode == "ir":
        order = sorted(shapes, key=lambda s: math.inf if not S.finite(s.get("ir"))
                       else s["ir"])
        colour = [S.ir_colour(s.get("ir"), top) if s.get("ir") is not None else S.UNCHECKED
                  for s in order]
    else:
        from .scene import _rank_key
        order = sorted(shapes, key=lambda s: _rank_key(s["ratio"]))
        colour = [S.em_colour(s["ratio"], warn) for s in order]
    if not order:
        return
    img = np.zeros((H, W, 4), dtype=np.uint8)
    rgba: dict = {}

    def span(a, b, n):
        if b - a < MIN_PX:
            mid = (a + b) / 2
            a, b = mid - MIN_PX / 2, mid + MIN_PX / 2
        lo, hi = max(0, int(math.floor(a + 0.5))), min(n, int(math.floor(b + 0.5)))
        return lo, max(hi, min(lo + 1, n))
    for sh, c in zip(order, colour):
        x0, y0, x1, y1 = sh["box"]
        c0, c1 = span((x0 - win[0]) * sx, (x1 - win[0]) * sx, W)
        r0, r1 = span((win[3] - y1) * sy, (win[3] - y0) * sy, H)
        if c1 <= c0 or r1 <= r0:
            continue
        img[r0:r1, c0:c1] = rgba.get(c) or rgba.setdefault(c, (*S.hex_rgb(c), 255))
    ax.imshow(img, extent=(win[0], win[1], win[2], win[3]), origin="upper",
              interpolation="nearest", zorder=2, aspect="auto")


def _models(ax, scene):
    """A block model's elements (a chip-level run's abstracts): dotted
    outlines, off the scale -- their EM is checked in the block's abstract."""
    for s in scene.get("shapes") or []:
        if s.get("model"):
            x0, y0, x1, y1 = s["box"]
            ax.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0, fill=False,
                                   edgecolor=S.MODEL, lw=1.3, ls=(0, (1, 2)), zorder=3))


def _badge(ax, placer, it, xy, px_size, ink, face, edge, z=9):
    w = h = px_size * 1.55
    dx, dy = placer.place(xy[0], xy[1], w, h, _BADGE_OFFSETS)
    ax.annotate(str(it["rank"]), xy, xytext=(pt(dx), pt(dy)), textcoords="offset points",
                ha="center", va="center", fontsize=fs(px_size), color=ink, fontweight="bold",
                zorder=z, annotation_clip=False,
                bbox=dict(boxstyle="circle,pad=0.22", fc=face, ec=edge, lw=1.0),
                arrowprops=dict(arrowstyle="-", color=face, lw=1.0, shrinkA=0, shrinkB=2))


def _level_colours(level: str):
    """(face, ink) of a numbered badge by its item's level."""
    if level == "fail":
        return S.FAIL, "#ffffff"
    if level == "warn":
        return S.WARN, "#241a00"
    return "#ffffff", "#0b0b0b"


def panel(page: Page, box, scene: dict, mode: str = "em", *, badges: int = 5,
          callouts=None, rings: bool = True, window=None,
          scalebar: float | None = None, ticks: bool = True, px: float = 14) -> dict:
    """The layout panel in the pixel box (x, y, w, h): the canvas, the
    underlay, the elements, the outline, the markers, the numbered worst
    items (or `callouts`: {rank: (x, y, ha)} label places in um), the IR
    rings. Returns {"ax", "window", "underlay", "ir_max"}."""
    x, y, w, h = box
    win = fit_window(window or scene_window(scene), w, h)
    ax = page.axes(x, y, w, h)
    ax.set_facecolor(S.CANVAS)
    ax.set_xlim(win[0], win[1])
    ax.set_ylim(win[2], win[3])
    how = _underlay(ax, scene.get("underlay"), win, w, h)
    top = ir_max(scene)
    _shapes(ax, scene, mode, win, w, h, top)
    span = max(win[1] - win[0], win[3] - win[2])
    if scene.get("outline"):
        o = scene["outline"]
        ax.add_patch(Rectangle((o[0], o[1]), o[2] - o[0], o[3] - o[1], fill=False,
                               edgecolor=S.OUTLINE, lw=1.1, ls=(0, (5, 4)), zorder=3))
    sx = w / (win[1] - win[0])
    pad = max(4.0 / sx, 0.006 * span)
    if mode == "em":
        for mk in scene.get("markers") or []:
            x0, y0, x1, y1 = mk["box"]
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            bw, bh = max(x1 - x0, MIN_PX / sx), max(y1 - y0, MIN_PX / sx)
            ax.add_patch(Rectangle((cx - bw / 2 - pad, cy - bh / 2 - pad), bw + 2 * pad,
                                   bh + 2 * pad, fill=False, edgecolor=S.MARKER, lw=1.7,
                                   zorder=6))
    a, b = ax.transAxes.transform((0, 0)), ax.transAxes.transform((1, 1))
    k = DPI / ax.figure.dpi
    placer = _Placer(ax, (a[0] * k, a[1] * k, b[0] * k, b[1] * k))
    if scalebar:
        _scalebar(ax, win, scalebar, px)
        placer.block_px(a[0] * k + 70, a[1] * k + 22, 140, 44)
    items = scene.get("items") or []
    for it in items:
        placer.block(it["xy"][0], it["xy"][1], 8, 8)
    if mode == "em":
        for mk in scene.get("markers") or []:
            placer.block_box(mk["box"])
    if mode == "em" and callouts:
        _callouts(ax, items, callouts, px, placer)
    elif mode == "em" and badges:
        for it in items[:badges]:
            if not placer.inside(*it["xy"]):
                continue
            face, ink = _level_colours(it["level"])
            _badge(ax, placer, it, it["xy"], px, ink, face, S.CANVAS)
    if mode == "ir" and rings:
        _rings(ax, placer, scene, px)
    if ticks:
        ink = S.AXIS_INK if page.theme == "dark" else page.t["muted"]
        ax.tick_params(labelsize=fs(px - 1), colors=ink, length=3)
        for sp in ax.spines.values():
            sp.set_color(page.t["axis"])
        ax.set_xlabel("x (µm)", fontsize=fs(px - 1), color=ink, labelpad=2)
        ax.set_ylabel("y (µm)", fontsize=fs(px - 1), color=ink, labelpad=2)
    else:
        ax.set_xticks([])
        ax.set_yticks([])
        for sp in ax.spines.values():
            sp.set_color(page.t["axis"])
    return {"ax": ax, "window": win, "underlay": how, "ir_max": top}


def _scalebar(ax, win, length, px):
    x0, x1, y0, y1 = win
    bx, by = x0 + 0.04 * (x1 - x0), y0 + 0.07 * (y1 - y0)
    ax.plot([bx, bx + length], [by, by], color="#ffffff", lw=3, zorder=12,
            solid_capstyle="butt")
    ax.text(bx + length / 2, by + 0.02 * (y1 - y0), f"{length:g} µm", ha="center",
            va="bottom", fontsize=fs(px), color="#ffffff", zorder=12)


def _callouts(ax, items, callouts, px, placer=None):
    by_rank = {it["rank"]: it for it in items}
    for rank, (tx, ty, ha) in callouts.items():
        it = by_rank.get(rank)
        if it is None:
            continue
        face, _ink = _level_colours(it["level"])
        how = (it.get("fix") or "").split(": ", 1)[-1]
        label = f"#{rank}  {S.fmt_ratio(it['ratio'])} {it['level'].upper()}" + (
            f"\n{how}" if how else "")
        if placer is not None:            # the label's box: no port label covers it
            lw = max(len(ln) for ln in label.split("\n")) * px * 0.66 + 16
            lh = (label.count("\n") + 1) * px * 1.35 + 12
            a, b = placer.px(tx, ty)
            cx = a + (lw / 2 if ha == "left" else -lw / 2 if ha == "right" else 0)
            placer.block_px(cx, b, lw, lh)
            ex, ey = placer.px(*it["xy"])          # and its leader, as a row of dots
            for f in np.linspace(0.0, 1.0, 9):
                placer.block_px(cx + (ex - cx) * f, b + (ey - b) * f, 8, 8)
        ax.plot([it["xy"][0]], [it["xy"][1]], "o", ms=pt(px * 0.55), mfc=face,
                mec=S.CANVAS, mew=1.0, zorder=11)
        ann = ax.annotate(label, it["xy"], xytext=(tx, ty), textcoords="data", ha=ha,
                          va="center", fontsize=fs(px), color="#ffffff", fontweight="bold",
                          zorder=10, linespacing=1.3, annotation_clip=False,
                          bbox=dict(boxstyle="round,pad=0.45", fc="#111111", ec=face, lw=1.6),
                          arrowprops=dict(arrowstyle="-", color=face, lw=1.5, shrinkA=0,
                                          shrinkB=4))
        ann.set_gid(CALLOUT_GID)


#: the gid of a callout's annotation (`callout_crossings` finds them by it)
CALLOUT_GID = "emir-callout"


def callout_crossings(fig) -> list:
    """Where the callouts of a drawn figure get in each other's way: two
    callout boxes that overlap, or a callout's leader that runs under another
    callout's box (it would seem to leave that box). `[(callout, what it
    meets), ...]`, each named by its label's first line; [] when every leader
    reaches its item in the clear. Draws `fig` to measure it."""
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    calls = []
    for ax in fig.axes:
        for t in ax.texts:
            if t.get_gid() == CALLOUT_GID and t.get_bbox_patch() is not None:
                bb = t.get_bbox_patch().get_window_extent(r)
                end = ax.transData.transform(t.xy)
                calls.append((t.get_text().split("\n")[0], bb, tuple(end)))
    out = []
    for i, (name, box, _end) in enumerate(calls):
        for j, (other, obox, oend) in enumerate(calls):
            if i == j:
                continue
            if i < j and box.overlaps(obox):
                out.append((name, f"the box of {other}"))
            start = ((obox.x0 + obox.x1) / 2, (obox.y0 + obox.y1) / 2)
            inside = [(start[0] + (oend[0] - start[0]) * f, start[1] + (oend[1] - start[1]) * f)
                      for f in np.linspace(0.0, 1.0, 401)]
            if any(box.x0 + 1 < x < box.x1 - 1 and box.y0 + 1 < y < box.y1 - 1
                   and not (obox.x0 <= x <= obox.x1 and obox.y0 <= y <= obox.y1)
                   for x, y in inside):
                out.append((other, f"its leader runs under the box of {name}"))
    return out


def _rings(ax, placer, scene, px):
    """A ring at each net's worst node, the three worst labelled."""
    # a drop that prints as ~0 has no worst node worth a ring
    nets = [n for n in scene.get("nets") or [] if n.get("ir_xy") and S.finite(n.get("ir"))
            and n["ir"] > 0 and not S.fmt_mv(n["ir"]).startswith("~0")
            and placer.inside(*n["ir_xy"])]
    nets.sort(key=lambda n: -n["ir"])
    for k, n in enumerate(nets):
        x, y = n["ir_xy"]
        ax.plot([x], [y], "o", ms=pt(16), mfc="none", mec=S.RING, mew=2.0, zorder=8)
        ax.plot([x], [y], "o", ms=pt(20), mfc="none", mec="#000000", mew=1.0, zorder=8)
        if k >= 3:
            continue
        label = f"{n['net']} {S.fmt_mv(n['ir'])}"
        w = len(label) * px * 0.62 + 10
        hh = px * 1.6
        offs = [(sx * (w / 2 + 14), sy * (hh / 2 + 6)) for sx, sy in _LABEL_OFFSETS]
        dx, dy = placer.place(x, y, w, hh, offs)
        ax.annotate(label, (x, y), xytext=(pt(dx), pt(dy)), textcoords="offset points",
                    ha="center", va="center", fontsize=fs(px), color="#ffffff",
                    fontfamily=MONO, zorder=9, annotation_clip=False,
                    bbox=dict(boxstyle="square,pad=0.3", fc=(0, 0, 0, 0.8), ec="none"),
                    arrowprops=dict(arrowstyle="-", color="#ffffff", lw=0.9, shrinkA=0,
                                    shrinkB=8))


# ---------------------------------------------------------------- the legend


def draw_legend(page: Page, x, y, w, mode: str, scene: dict, px: float = 14,
                top: float | None = None) -> float:
    """The legend of `mode` at (x, y), `w` wide: its title, the ramp with
    its ticks and every other colour named. Returns the y below it."""
    t = page.t
    lost = (mode == "em" and any(s["ratio"] is not None and not S.finite(s["ratio"])
                                 for s in scene.get("shapes") or [])) or \
           (mode == "ir" and any(s.get("ir") is not None and not S.finite(s["ir"])
                                 for s in scene.get("shapes") or []))
    lg = S.legend(mode, scene.get("warn", S.WARN_DEFAULT),
                  ir_max=top if top is not None else ir_max(scene),
                  unchecked=bool(scene.get("any_unchecked")), lost=lost,
                  markers=bool(scene.get("markers")), model=bool(scene.get("any_model")),
                  rings=any(n.get("ir_xy") for n in scene.get("nets") or []))
    page.text(x, y, lg["title"], px + 1, t["ink"], bold=True)
    tw = page.width(lg["title"], px + 1, bold=True)
    page.text(x + tw + 8, y + 1, page.fit(lg["sub"], px - 1, w - tw - 8), px - 1, t["muted"])
    y += px * 1.6
    bar_w, bar_h = w - 16, 14
    grad = np.linspace(0, 1, 256)[None, :]
    from matplotlib.colors import LinearSegmentedColormap
    cmap = LinearSegmentedColormap.from_list("emir", [(p, c) for p, c in lg["bar"]["stops"]])
    bx = x + 8
    page.ax.imshow(grad, cmap=cmap, extent=(bx, bx + bar_w, y + bar_h, y), aspect="auto",
                   zorder=3, interpolation="bilinear")
    page.rect(bx, y, bar_w, bar_h, "none", t["axis"], lw=0.8, z=4)
    y += bar_h + 3
    for pos, lab in lg["bar"]["ticks"]:
        tx = bx + pos * bar_w
        page.ax.plot([tx, tx], [y - 3, y + 1], color=t["muted"], lw=1, zorder=4)
        ha = "left" if pos <= 0.001 else "right" if pos >= 0.999 else "center"
        page.text(tx, y + 2, lab, px - 1, t["ink2"], ha=ha)
    y += px * 1.55
    col_w = (w - 4) / 2
    keys = lg["keys"]
    for i, (kind, colour, text) in enumerate(keys):
        cx = x + (i % 2) * col_w
        cy = y + (i // 2) * (px * 1.5)
        if kind == "swatch":
            page.rect(cx, cy + 2, 18, px * 0.85, colour, "none", z=4)
        elif kind == "outline":
            page.rect(cx + 1, cy + 2, 16, px * 0.85, "none", colour, lw=1.8, z=4)
        elif kind == "dotted":
            page.rect(cx + 1, cy + 2, 16, px * 0.85, "none",
                      colour if page.theme == "dark" else t["ink"], lw=1.6, z=4,
                      ls=(0, (1, 2)))
        else:
            page.ax.add_patch(Circle((cx + 9, cy + 2 + px * 0.42), px * 0.42, fill=False,
                                     ec=colour if page.theme == "dark" else t["ink"], lw=1.8,
                                     zorder=4))
        page.text(cx + 25, cy, page.fit(text, px - 1, col_w - 30), px - 1, t["ink2"])
    return y + ((len(keys) + 1) // 2) * px * 1.5 + 4


# ------------------------------------------------------------- the side panel


def _verdict_colour(page, verdict):
    t = page.t
    return {"FAIL": t["fail_ink"], "WARN": t["warn_ink"], "CLEAN": t["pass"]}.get(
        verdict, t["ink"])


def _ratio_ink(page, ratio, warn):
    lv = S.em_level(ratio, warn)
    t = page.t
    return {"fail": t["fail_ink"], "warn": t["warn_ink"]}.get(lv, t["ink"])


def warn_prefixes() -> tuple:
    """How an alert drawn in the warn colour starts: hand-entered limits
    without a source, suspect units, an open loop above its droop, and every
    way a self-consistent loop reports pass 0 (`selfconsist.STOP_ALERTS`'s
    labels: a new one can never be drawn in the plain colour)."""
    from .selfconsist import STOP_ALERTS
    loop = sorted({label.upper() for label, _code, _what in STOP_ALERTS.values()})
    return ("NO SOURCE", "SUSPECT", "OPEN LOOP", *loop)


def side_panel(page: Page, x, y, w, bottom, scene: dict, modes, px: float = 15) -> float:
    """The verdict, the numbered worst items, the per-net table, what was not
    checked, the legend(s) and the notes, top to bottom from `y`; the notes
    and the extra net rows are dropped when there is no room."""
    t = page.t
    verdict = scene.get("verdict", "CLEAN")
    counts = scene.get("counts") or {}
    nv = len(scene.get("markers") or [])
    page.text(x, y, verdict, px + 9, _verdict_colour(page, verdict), bold=True)
    vw = page.width(verdict, px + 9, bold=True)
    what = (f"{nv} violation{'s' if nv != 1 else ''}" if nv else
            f"{counts.get('WARN', 0)} in the warn band" if verdict == "WARN" else
            "nothing at or above the warn fraction")
    page.text(x + vw + 12, y + 7, page.fit(what, px + 1, w - vw - 12), px + 1, t["ink"])
    y += px * 2.3
    nets = scene.get("nets") or []
    wn = max(nets, key=lambda n: -1 if n["ratio"] is None else math.inf
             if not S.finite(n["ratio"]) else n["ratio"], default=None)
    wi = max((n for n in nets if n.get("ir") is not None),
             key=lambda n: math.inf if not S.finite(n["ir"]) else n["ir"], default=None)
    bits = []
    if wn is not None and wn["ratio"] is not None:
        bits.append(f"worst EM {S.fmt_ratio(wn['ratio'])} ({wn['net']})")
    if wi is not None:
        avg = " average" if scene.get("ir_kind") == "average" else ""
        bits.append(f"worst{avg} IR {S.fmt_mv(wi['ir'])} ({wi['net']})")
    if bits:
        y = page.para(x, y, " · ".join(bits), px, w, t["ink2"], lead=1.35)
    y += 8
    if "em" in modes:
        y = _item_list(page, x, y, w, scene, px)
    else:
        y = _ir_list(page, x, y, w, scene, px)
    y = _net_table(page, x, y, w, bottom, scene, px - 1)
    nc = scene.get("unchecked") or ""
    y += 4
    page.text(x, y, "Not checked:", px - 1, t["ink"], bold=True)
    lw = page.width("Not checked:", px - 1, bold=True) + 6
    lines = page.wrap(nc or "nothing: every metric has a limit.", px - 1, w - lw)
    first, rest = lines[0], " ".join(lines[1:])
    page.text(x + lw, y, first, px - 1, t["ink2"])
    y += (px - 1) * 1.35
    if rest:
        y = page.para(x, y, rest, px - 1, w, t["ink2"], lead=1.35, max_lines=3)
    y += 6
    # where the limits came from when a person typed them, and values that
    # look like a unit slip: never dropped, the warnings in the warn colour
    for n in scene.get("alerts") or []:
        warn_line = n.startswith(warn_prefixes())
        y = page.para(x, y, n, px - 2, w, t["warn_ink"] if warn_line else t["ink2"],
                      lead=1.3) + 2
    y += 4
    top_ir = ir_max(scene)
    for mode in modes:
        y = draw_legend(page, x, y, w, mode, scene, px - 1, top=top_ir) + 6
    notes = [n for n in scene.get("notes") or [] if n]
    for n in notes:
        lines = page.wrap(n, px - 3, w)
        if y + len(lines) * (px - 3) * 1.3 > bottom:
            break
        y = page.para(x, y, n, px - 3, w, t["muted"], lead=1.3)
    return y


def item_label(it: dict) -> str:
    """"VDD R4 via avg", "VDD Rm1 Metal1 wire peak": the net, the element
    (its DSPF name), the layer, wire or via (unless the layer says so) and the
    governing metric."""
    kind = it.get("kind") or ""
    layer = str(it.get("layer") or "")
    parts = [it.get("net"), it.get("name"), layer,
             None if kind and kind in layer.lower() else kind, it.get("metric")]
    return " ".join(str(v) for v in parts if v)


def _item_list(page, x, y, w, scene, px) -> float:
    """The worst items, numbered as on the map, each with where and its fix."""
    t = page.t
    warn = scene.get("warn", S.WARN_DEFAULT)
    items = scene.get("items") or []
    if not items:
        return y
    page.text(x, y, "Worst items, numbered as on the map", px, t["ink"], bold=True)
    y += px * 1.6
    for it in items:
        face, ink = _level_colours(it["level"])
        r = px * 0.62
        page.ax.add_patch(Circle((x + r + 1, y + px * 0.62), r, fc=face,
                                 ec=t["axis"] if face == "#ffffff" else face, lw=0.8,
                                 zorder=4))
        page.text(x + r + 1, y + px * 0.62, str(it["rank"]), px - 3, ink, bold=True,
                  ha="center", va="center")
        ratio = S.fmt_ratio(it["ratio"])
        rw = page.width(ratio, px, bold=True)
        page.text(x + w, y, ratio, px, _ratio_ink(page, it["ratio"], warn), bold=True,
                  ha="right")
        page.text(x + 2 * r + 8, y, page.fit(item_label(it), px, w - 2 * r - 16 - rw), px,
                  t["ink"])
        y += px * 1.35
        where = f"at ({it['xy'][0]:.2f}, {it['xy'][1]:.2f}) µm"
        fix = it.get("fix") or ""
        sub = f"{where} · fix: {fix.split(': ', 1)[-1]}" if fix else where
        page.text(x + 2 * r + 8, y, page.fit(sub, px - 2, w - 2 * r - 8), px - 2, t["muted"])
        y += px * 1.45
    return y + 6


def _ir_list(page, x, y, w, scene, px) -> float:
    """The IR view's list: each net's worst node, the worst first (the three
    worst are labelled on the map)."""
    t = page.t
    nets = sorted((n for n in scene.get("nets") or [] if n.get("ir") is not None),
                  key=lambda n: -(math.inf if not S.finite(n["ir"]) else n["ir"]))
    if not nets:
        return y
    page.text(x, y, "Worst IR drop per net, ringed on the map", px, t["ink"], bold=True)
    y += px * 1.6
    for n in nets[:5]:
        v = S.fmt_mv(n["ir"])
        vw = page.width(v, px, bold=True)
        page.text(x + w, y, v, px, t["fail_ink"] if not S.finite(n["ir"]) else t["ink"],
                  bold=True, ha="right")
        page.text(x, y, page.fit(str(n["net"]), px, w - vw - 12), px, t["ink"])
        y += px * 1.35
        at = (f"worst node at ({n['ir_xy'][0]:.2f}, {n['ir_xy'][1]:.2f}) µm"
              if n.get("ir_xy") else "worst node not placed (no coordinates)")
        page.text(x, y, page.fit(at, px - 2, w), px - 2, t["muted"])
        y += px * 1.45
    if len(nets) > 5:
        page.text(x, y, f"and {len(nets) - 5} more nets", px - 2, t["muted"])
        y += px * 1.4
    return y + 6


def _net_table(page, x, y, w, bottom, scene, px) -> float:
    """net | worst EM | at | IR worst, the worst net first."""
    t = page.t
    nets = scene.get("nets") or []
    if not nets:
        return y
    warn = scene.get("warn", S.WARN_DEFAULT)
    cols = [("net", 0.24), ("worst EM", 0.24), ("at", 0.30), ("IR worst", 0.22)]
    rh = px * 1.75
    page.rect(x, y, w, rh, t["head"], z=2)
    cx = x
    for name, f in cols:
        right = name in ("worst EM", "IR worst")
        page.text(cx + (w * f - 8 if right else 6), y + rh / 2, name, px, t["ink"], bold=True,
                  ha="right" if right else "left", va="center")
        cx += w * f
    y += rh
    room = max(2, int((bottom - y - 230) // rh))
    shown = nets[:max(1, min(len(nets), 8, room))]
    for i, n in enumerate(shown):
        if i % 2:
            page.rect(x, y, w, rh, t["zebra"], z=2)
        cx = x
        ratio = n["ratio"]
        cells = [(n["net"], t["ink"], True, False),
                 (S.fmt_ratio(ratio) if ratio is not None else "not checked",
                  _ratio_ink(page, ratio, warn) if ratio is not None else t["muted"], True, True),
                 (n.get("at") or "", t["ink2"], False, False),
                 (S.fmt_mv(n["ir"]) if n.get("ir") is not None else "-", t["ink2"], False, True)]
        for (text, colour, bold, right), (_name, f) in zip(cells, cols):
            cw = w * f
            if right:
                page.text(cx + cw - 8, y + rh / 2, page.fit(text, px, cw - 30), px, colour,
                          bold=bold, ha="right", va="center")
            else:
                page.text(cx + 6, y + rh / 2, page.fit(text, px, cw - 10), px, colour,
                          bold=bold, va="center")
            cx += cw
        if ratio is not None:          # the ratio's own colour, as on the map
            page.rect(x + w * cols[0][1] + 6, y + rh / 2 - 5, 10, 10,
                      S.em_colour(ratio, warn), "none", z=4)
        y += rh
    if len(nets) > len(shown):
        y += 4
        page.text(x, y, f"and {len(nets) - len(shown)} more nets (every net in the HTML "
                  f"report and the run summary)", px - 2, t["muted"])
        y += px * 1.4
    return y + 6


# ---------------------------------------------------------------- the chart


MODE_TITLE = {"em": "EM: each element by its governing ratio",
              "ir": "IR drop: each element by its drop from the net's feed"}


def draw_footer(page: Page, x, y, w, source: str = "", px: float = 13) -> float:
    """"Screening, not sign-off." and the research disclaimer (and the
    source) from `y`; returns the y below."""
    t = page.t
    page.ax.plot([x, x + w], [y - 8, y - 8], color=t["grid"], lw=1.0, zorder=2)
    head = SCREENING_NOTE
    hw = page.width(head + "  ", px, bold=True)
    page.text(x, y, head, px, t["ink"], bold=True)
    lines = page.wrap(RESEARCH_DISCLAIMER, px, w - hw)
    for ln in lines:
        page.text(x + hw, y, ln, px, t["muted"])
        y += px * 1.35
    if source:
        y = page.para(x, y, source, px - 1, w, t["muted"], lead=1.3, max_lines=2)
    return y


def footer_height(page: Page, w, source: str = "", px: float = 13) -> float:
    hw = page.width(SCREENING_NOTE + "  ", px, bold=True)
    n = len(page.wrap(RESEARCH_DISCLAIMER, px, w - hw))
    m = min(2, len(page.wrap(source, px - 1, w))) if source else 0
    return n * px * 1.35 + m * (px - 1) * 1.3 + 14


def _aspect(scene) -> float:
    win0 = scene_window(scene)
    return (win0[1] - win0[0]) / max(win0[3] - win0[2], 1e-9)


def _side_by_side(scene, modes) -> bool:
    """Two panels of a tall layout go side by side, else one above the other."""
    return len(modes) > 1 and _aspect(scene) < 1.0


def _layout_sizes(scene, W, H, top, bottom, modes, side_w):
    """The panels' axes boxes [(x, y, w, h)] in the left column: one above
    the other (side by side for a tall layout), each at the layout's aspect
    when it fits."""
    M = 40
    left_w = W - 2 * M - side_w - 36
    n = len(modes)
    gap = 18
    aspect = _aspect(scene)
    row = _side_by_side(scene, modes)
    boxes = []
    for k in range(n):
        if row:
            cw = (left_w - (n - 1) * 30) / n
            px, py, pw, ph = M + k * (cw + 30), top + 26, cw, bottom - top - 26
        else:
            each = (bottom - top - (n - 1) * gap) / n
            px, py, pw, ph = M, top + k * (each + gap), left_w, each
            if n > 1:
                py += 26
                ph -= 26
        bw, bh = pw - 60, ph - 46
        aw, ah = bw, bh
        if aspect > bw / bh:
            ah = max(bw / aspect, bh * 0.55)
        else:
            aw = max(bh * aspect, bw * 0.55)
        boxes.append((px + 58, py, aw, ah))
    return boxes


def _head(pg, scene, W, draw=True) -> float:
    """The title and the subtitle; returns the y below them."""
    t, M, y = pg.t, 40, 24
    if draw:
        pg.text(M, y, pg.fit(scene.get("title") or "EM/IR", 30, W - 2 * M), 30, t["ink"],
                bold=True)
    y += 44
    if scene.get("subtitle"):
        lines = pg.wrap(scene["subtitle"], 16, W - 2 * M)[:2]
        if draw:
            pg.para(M, y, scene["subtitle"], 16, W - 2 * M, t["muted"], lead=1.3, max_lines=2)
        y += len(lines) * 16 * 1.3
    return y + 12


def auto_height(scene: dict, W: int, theme: str, modes, side_w: int) -> int:
    """The chart's height for its content: the layout at its own aspect (as
    tall as the side panel needs, at least) and no empty band."""
    pg = Page(W, 4000, theme)
    try:
        top = _head(pg, scene, W, draw=False)
        fh = footer_height(pg, W - 80, scene.get("source", ""))
        M = 40
        sx = W - M - side_w
        side_bottom = side_panel(pg, sx, top, side_w, 10_000, scene, modes)
        aspect = _aspect(scene)
        left_w = W - 2 * M - side_w - 36 - 60
        if _side_by_side(scene, modes):
            cw = (left_w + 60 - 30 * (len(modes) - 1)) / len(modes) - 60
            left = top + 26 + cw / aspect + 46
        else:
            per = left_w / aspect + 46 + (26 if len(modes) > 1 else 0)
            left = top + len(modes) * per + (len(modes) - 1) * 18
        h = max(side_bottom + 10, left) + fh + 30
    finally:
        plt.close(pg.fig)
    return int(min(max(h, 0.42 * W), 1.3 * W))


def draw(scene: dict, path: str, theme: str = "dark", modes=("em",), size=SIZE) -> dict:
    """The chart of `scene` in `modes` ("em", "ir" or both, stacked), as a
    PNG at `path`; `size` (W, H) px, H None: as tall as the content needs.
    Returns {"path", "underlay"} (how the layout was drawn, "" when it was
    not)."""
    W, H = size
    side_w = max(440, int(W * 0.34))
    if H is None:
        H = auto_height(scene, W, theme, modes, side_w)
    pg = Page(W, H, theme)
    M = 40
    top = _head(pg, scene, W)
    fh = footer_height(pg, W - 2 * M, scene.get("source", ""))
    bottom = H - fh - 18
    hows, right = [], M
    for mode, box in zip(modes, _layout_sizes(scene, W, H, top, bottom, modes, side_w)):
        if len(modes) > 1:
            pg.text(box[0] - 58, box[1] - 26, pg.fit(MODE_TITLE[mode], 16, box[2] + 58), 16,
                    pg.t["ink"], bold=True)
        info = panel(pg, box, scene, mode)
        hows.append(info["underlay"])
        right = max(right, box[0] + box[2])
    sx = max(right + 40, W - M - int(side_w * 1.25))
    side_panel(pg, sx, top, W - M - sx, bottom, scene, modes)
    draw_footer(pg, M, H - fh - 2, W - 2 * M, scene.get("source", ""))
    pg.save(path)
    return {"path": path, "underlay": next((h for h in hows if h), "")}


# -------------------------------------------------- the charts of a run's --html


#: the chart of `--html`, EM and IR on one picture: dark is the plain name,
#: light `_light`
CHART_FILE = "emir_chart.png"


def write_run_charts(solved: dict, d, proc, out_dir: str, *, gds: str | None = None,
                     cell: str | None = None, command: str = "",
                     what: str = "emir run", not_checked_extra: dict | None = None) -> tuple:
    """The chart of a run's nets, EM and IR, dark and light, into `out_dir`
    (the --html directory): every element drawn from the DSPF's coordinates
    on black, over the GDS when there is one (`emir run --gds`). Returns (the
    paths, how the layout was drawn: "" when it was not)."""
    from .scene import run_scene
    pdk = (getattr(proc, "data", None) or {}).get("pdk")
    design = d.design or "design"
    src = os.path.basename(gds) if gds else os.path.basename(getattr(d, "path", "") or "")
    temp = getattr(proc, "temp", None)
    from .scene import loop_status
    mode_words, loop_alerts = loop_status(solved)
    sub = (f"{what} · {design}" + (f" ({src})" if src else "")
           + f" · {len(solved)} net{'s' if len(solved) != 1 else ''} · mode "
           + mode_words + (f" · EM at {temp:g} °C" if temp is not None else ""))
    scene = run_scene(solved, d, proc, title=f"EM/IR · {design}", subtitle=sub,
                      source=f"Reproduce: {command}" if command else "",
                      gds=gds, cell=cell, pdk=pdk, not_checked_extra=not_checked_extra)
    if not gds:
        scene["notes"].append("No layout under the elements: a DSPF run draws each "
                              "element from the DSPF's own coordinates, on black.")
    # limits a person typed (emir process new): the chart says so, as the reports do
    from .scene import limits_notes
    from .summary import _limits_provenance
    scene["alerts"] = loop_alerts + limits_notes(
        _limits_provenance(solved, proc) if proc else None)
    out, how = [], ""
    for theme in ("dark", "light"):
        stem = CHART_FILE if theme == "dark" else CHART_FILE.replace(".png", "_light.png")
        p = os.path.join(out_dir, stem)
        how = draw(scene, p, theme, modes=("em", "ir"), size=(SIZE[0], None))["underlay"] or how
        out.append(os.path.abspath(p))
    return out, how
