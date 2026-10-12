"""What an EM/IR chart shows, as plain data: the scene every renderer draws.

A scene is a dict, built here from a run's solved nets (`run_scene`: `emir
run` on a DSPF, over its layout with --gds), and drawn by `emir.chart` (PNG,
matplotlib) or, for one net, by `emir.netreport` (inline SVG). Its keys:

* `shapes`: every element drawn, each {net, name, layer, kind ("wire" or
  "via"), box [x0, y0, x1, y1] um, ratio (the governing EM ratio; None when
  the process file states no limit for it: not checked; NaN when it is not a
  number), metric, ir (its drop from the net's feed in V, the larger of its
  two ends; None when unknown), fix};
* `markers`: the violations (1x and over, or not a number), worst first,
  each {box, rank};
* `items`: the worst items, numbered as on the map: {rank, net, name, layer,
  kind, metric, ratio, fix, xy, level};
* `nets`: one row per net: {net, ratio, at, ir, ir_xy};
* `outline`: the cell's box, drawn dashed; `underlay`: {gds, cell, lyp};
* `verdict`, `counts`, `warn`, `unchecked` (the "not checked" text, from
  the run's `not_checked`, as the report says it), `title`, `subtitle`,
  `alerts` (where limits a person typed came from, and values that look like
  a unit slip: drawn under "Not checked", never dropped), `notes`, `source`.

No plotting library is imported here.
"""
from __future__ import annotations

import math

import numpy as np

from . import style as S

#: the worst items a chart numbers and lists
TOP_ITEMS = 5
#: the most elements a run's chart draws: above it the worst half by EM ratio
#: and the worst half by IR drop are kept (each panel keeps what it shows),
#: and the chart says so in its notes
MAX_SHAPES = 100_000


def _box(r, a, b) -> list | None:
    """An element's box: the DSPF's own $LLX..$URY when it has an area, else
    the segment between its two nodes at its width (a via: a square)."""
    bb = r.get("bbox")
    if bb and bb[2] > bb[0] and bb[3] > bb[1]:
        return [float(v) for v in bb]
    if a is None or b is None:
        return None
    from .layoutview import _seg_rect
    w = r.get("w") or (math.sqrt(r["area"]) if r.get("area") else 0.05)
    return [round(float(c), 4) for c in _seg_rect(a, b, w)]


def segments(r: dict, d, net: str, proc) -> list:
    """Every element of one solved net (`check_net` / `rcc_check`'s result)
    as a scene shape, with its governing ratio (as `dspf.em_clusters` judges
    it: the wire limit on W - dW, the layer's width loss) and its IR drop. A
    wire with no width is not checked (ratio None), as the run reports it."""
    from .dspf import via_cuts
    from .engine import METRICS
    from .errors import EmirError
    from .layoutview import fix_hint
    nw, lmap, Ie, V = r["network"], r.get("layer_map") or {}, r.get("Ie") or {}, r.get("V")
    xy = d.node_xy
    out = []
    for i, res in enumerate(nw.res):
        a, b = xy.get(res["n1"]), xy.get(res["n2"])
        box = _box(res, a, b)
        if box is None:
            continue
        hit = lmap.get(res["layer"])
        kind = "via" if (hit and hit[0] == "via") else "wire"
        ratios, cuts, src, dw = {}, None, "count", None
        if hit:
            pdk = hit[1]
            weff = None
            if kind == "via":
                cuts, src = via_cuts(res, proc.vias[pdk])
            elif res.get("w"):
                # the width the limit applies to: W - dW (`width_loss_um`); a
                # wire no wider than dW has no limit (EMIR-E323): not a number
                dw = proc.layers[pdk].get("width_loss") or None
                try:
                    weff = proc.wire_width(pdk, res["w"])
                except EmirError:
                    weff = float("nan")
            for m in METRICS:
                if m not in Ie:
                    continue
                if kind == "via":
                    lim = proc.vias[pdk]["imax"].get(m)
                    if lim:
                        ratios[m] = abs(float(Ie[m][i])) / cuts / lim
                else:
                    lim = proc.layers[pdk]["jmax"].get(m)
                    # no width: J = I/w cannot be evaluated, so the wire is not
                    # checked (ratio None), as em_clusters and the report say
                    if lim and weff is not None:
                        ratios[m] = abs(float(Ie[m][i])) / weff / lim
        layer = hit[1] if hit else res.get("layer")
        if ratios:
            bad = [m for m, v in ratios.items() if not S.finite(v)]
            gov = bad[0] if bad else max(ratios, key=ratios.get)
            ratio = float(ratios[gov])
        else:
            gov, ratio = None, None
        ir = None
        if V is not None and len(V) and res["n1"] in nw.idx and res["n2"] in nw.idx:
            v1, v2 = float(V[nw.idx[res["n1"]]]), float(V[nw.idx[res["n2"]]])
            ir = v1 if abs(v1) >= abs(v2) else v2
            ir = abs(ir) if S.finite(ir) else float("nan")
        fix = ""
        if ratio is not None and (not S.finite(ratio) or ratio >= 1.0):
            fix = fix_hint({"ratio": ratio, "kind": kind, "layer": layer, "cuts": cuts,
                            "cuts_from": src, "w": res.get("w"), "dw": dw})
        out.append({"net": net, "name": res["name"], "layer": layer, "kind": kind,
                    "box": box, "ratio": ratio, "metric": gov, "ir": ir, "fix": fix,
                    "cuts": cuts, "w": res.get("w")})
    return out


def _rank_key(ratio) -> float:
    """Worst first: not a number above anything, no limit below everything."""
    if ratio is None:
        return -1.0
    return math.inf if not S.finite(ratio) else float(ratio)


def _order_key(s) -> tuple:
    """Worst first, and equal ratios (to 9 significant digits: the
    solve's rounding is no ranking) by net, then left to right,
    then bottom to top, so the numbers on the map read in order."""
    r = _rank_key(s.get("ratio"))
    if math.isfinite(r) and r > 0:
        r = float(f"{r:.9g}")
    x0, y0, x1, y1 = s.get("box") or (0.0, 0.0, 0.0, 0.0)
    return (-r, str(s.get("net") or ""), round((x0 + x1) / 2, 6), round((y0 + y1) / 2, 6))


def _centre(box) -> list:
    return [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2]


def items_of(shapes: list, warn: float, top: int = TOP_ITEMS) -> list:
    """The `top` worst checked elements, numbered (rank 1 the worst)."""
    ranked = sorted((s for s in shapes if s["ratio"] is not None), key=_order_key)
    out = []
    for k, s in enumerate(ranked[:top], start=1):
        out.append({"rank": k, "net": s["net"], "name": s["name"], "layer": s["layer"],
                    "kind": s["kind"], "metric": s.get("metric"), "ratio": s["ratio"],
                    "fix": s.get("fix") or "", "xy": _centre(s["box"]), "box": s["box"],
                    "level": S.em_level(s["ratio"], warn)})
    return out


def markers_of(shapes_or_items: list) -> list:
    """The violations' boxes (1x and over, or not a number), worst first."""
    bad = [s for s in shapes_or_items if s.get("ratio") is not None
           and (not S.finite(s["ratio"]) or s["ratio"] >= 1.0)]
    bad.sort(key=_order_key)
    return [{"box": s["box"], "rank": k} for k, s in enumerate(bad, start=1)]


def _ir_node(r: dict, d) -> list | None:
    """Where a net's largest drop from its feed is: the node of max |V|."""
    V, nw = r.get("V"), r.get("network")
    if V is None or nw is None or not len(V):
        return None
    a = np.abs(np.asarray(V, dtype=float))
    if not np.isfinite(a).any():
        return None
    k = int(np.nanargmax(a))
    p = d.node_xy.get(nw.nodes[k])
    return [float(p[0]), float(p[1])] if p is not None else None


def not_checked(solved: dict, proc) -> str:
    """The chart's "not checked" line: the run summary's `not_checked` (the
    structure `emir report` turns into sentences, `summary.not_checked`),
    each part in a few words, so the PNG says what the report says."""
    from .summary import not_checked as summary_nc
    return not_checked_text(dict(summary_nc(solved, proc)))


def _few(names, n=3) -> str:
    names = list(names)
    return ", ".join(map(str, names[:n])) + (f" +{len(names) - n}" if len(names) > n else "")


def not_checked_text(nc: dict) -> str:
    """`summary.not_checked`'s structure as one line of short parts."""
    parts = []
    by_metric = {}
    for e in nc.get("limits_missing") or []:
        for m in e.get("metrics") or []:
            by_metric.setdefault(m, []).append(e["name"])
    if by_metric:
        parts.append("no limit in the process file: " + "; ".join(
            f"{m} on {_few(names)}" for m, names in by_metric.items()))
    for m in nc.get("metrics_without_limits") or []:
        if m not in by_metric:
            parts.append(f"no {m} limit anywhere")
    if nc.get("unmapped_layers"):
        parts.append("layers with no process-file entry: " + _few(nc["unmapped_layers"]))
    if nc.get("estimated_limits"):
        parts.append("limits flagged estimates: " + _few(nc["estimated_limits"]))

    def per_net(key, what):
        d = nc.get(key) or {}
        n = sum(len(v) for v in d.values())
        if n:
            names = [x for v in d.values() for x in v]
            parts.append(f"{n} {what}: {_few(names)}")
    per_net("wires_without_width", "wire(s) with no width")
    per_net("diagonal_wires", "wire(s) on diagonal shapes (width not across the current)")
    per_net("vias_assumed_one_cut", "via(s) with no cut count (checked as one cut)")
    for net, u in sorted((nc.get("unsolved") or {}).items()):
        parts.append(f"{net} in pieces: {u.get('n_nodes')} node(s) not solved")
    gap = nc.get("avg_not_derated")
    if gap:
        parts.append(f"avg limits not derated at {gap.get('temp_C'):g} C (NOT conservative)")
    bi = nc.get("bidirectional") or {}
    n = sum(len(v) for v in bi.values())
    if n:
        parts.append(f"{n} branch(es) with a reversing current: no AC rule applied")
    return "; ".join(parts)


def keep_worst(shapes: list, n: int) -> list:
    """`n` of the shapes: the worst half by EM ratio and the worst half by IR
    drop (their union, topped up by ratio), so neither panel loses the
    elements it is about (a rail's far end has the worst drop and often the
    smallest current)."""
    def ir_key(s):
        v = s.get("ir")
        return -1.0 if v is None else math.inf if not S.finite(v) else float(v)
    by_em = sorted(shapes, key=lambda s: -_rank_key(s["ratio"]))
    by_ir = sorted(shapes, key=lambda s: -ir_key(s))
    keep, seen = [], set()
    for s in by_em[:n // 2] + by_ir[:n - n // 2] + by_em:
        if id(s) in seen:
            continue
        seen.add(id(s))
        keep.append(s)
        if len(keep) >= n:
            break
    return keep


def run_scene(solved: dict, d, proc, *, title: str = "", subtitle: str = "",
              source: str = "", gds: str | None = None, cell: str | None = None,
              pdk: str | None = None, notes=()) -> dict:
    """The scene of a run's solved nets: every element of every net, the
    worst items, one row per net; the layout under it when `gds` is given."""
    from .dspf import overall_verdict, worst_ratio
    warn = getattr(proc, "warn_fraction", S.WARN_DEFAULT) if proc else S.WARN_DEFAULT
    shapes, nets = [], []
    for net, r in solved.items():
        seg = segments(r, d, net, proc)
        shapes += seg
        worst = max((s for s in seg if s["ratio"] is not None), key=lambda s: _rank_key(
            s["ratio"]), default=None)
        nets.append({"net": net, "ratio": worst_ratio(r) if worst is not None else None,
                     "at": (f"{worst['layer']} {worst['metric']}" if worst is not None
                            and (not S.finite(worst['ratio']) or worst["ratio"] >= 1e-6)
                            else "no current" if worst is not None else "not checked"),
                     "ir": r.get("ir_drop"), "ir_xy": _ir_node(r, d)})
    nets.sort(key=lambda n: -_rank_key(n["ratio"]))
    verdict, counts, _top, _w = overall_verdict(solved, proc)
    notes = list(notes)
    # rcc: the IR panel is the average (static) drop; the dynamic droop, from
    # the simulated branch currents, is in the report, and its peak in a note
    dyn = [(net, r["dynamic"]) for net, r in solved.items() if r.get("dynamic")]
    if dyn:
        net, dy = max(dyn, key=lambda x: float(x[1].get("worst_V") or 0.0))
        notes.insert(0, f"IR is the average (static) drop; the dynamic droop (rcc, from the "
                        f"simulated branch currents) peaks at "
                        f"{float(dy['worst_V']) * 1e3:.4g} mV at "
                        f"{dy.get('worst_node')} on {net} (the report has every net's).")
    total = len(shapes)
    markers, items = markers_of(shapes), items_of(shapes, warn)
    if total > MAX_SHAPES:
        shapes = keep_worst(shapes, MAX_SHAPES)
        notes.append(f"{MAX_SHAPES:,} of {total:,} elements are drawn: the worst by EM ratio "
                     f"and the worst by IR drop, half each, so both panels show where their "
                     f"worst is; the run summary and the HTML pages hold every one.")
    underlay = None
    outline = None
    if gds:
        from .underlay import bbox, find_lyp
        underlay = {"gds": gds, "cell": cell, "lyp": find_lyp(pdk)}
        outline = bbox(gds, cell)
    return {"kind": "shapes", "shapes": shapes, "markers": markers,
            "items": items, "nets": nets, "verdict": verdict,
            "counts": counts, "warn": warn,
            "ir_kind": "average" if dyn else "drop",
            "unchecked": not_checked(solved, proc),
            "any_unchecked": any(s["ratio"] is None for s in shapes),
            "title": title, "subtitle": subtitle, "source": source, "notes": notes,
            "underlay": underlay, "outline": outline}


def mode_status(solved: dict) -> tuple:
    """`(mode words, alert lines)` of a run's chart: the modes of its nets
    (rcc, dc), no alert."""
    return ", ".join(sorted({r.get("mode", "dc") for r in solved.values()})), []


def limits_notes(lp: dict | None) -> list:
    """The chart's lines on where the limits came from when a person typed
    them (process data entered by hand, `emir process new`): which layers
    and vias, where the report names each value's source, and every value
    with no source (EMIR-W320). Nothing for a file that was not entered by
    hand: the report's "Where the limits came from" says the rest."""
    if not lp:
        return []
    from .process_file import HAND
    entries = lp.get("entries") or []
    out = []
    sus = [x for x in lp.get("suspects") or [] if not x.get("confirmed")]
    if sus:
        out.append("SUSPECT UNIT (EMIR-W321): " + "; ".join(
            f"{x['path']} = {x['value']:g} {x['unit']}" for x in sus[:4])
            + (f"; and {len(sus) - 4} more" if len(sus) > 4 else ""))
    hand = [e["name"] for e in entries if e.get("provenance") in HAND]
    if not hand:
        return out
    more = f" and {len(hand) - 6} more" if len(hand) > 6 else ""
    bare = [f"{e['name']} {', '.join(e['unsourced'])}" for e in entries if e.get("unsourced")]
    bare = list((lp.get("temperature") or {}).get("unsourced") or []) + bare
    if bare:
        out.append("NO SOURCE (EMIR-W320): " + "; ".join(bare[:4])
                   + (f"; and {len(bare) - 4} more" if len(bare) > 4 else ""))
    out.append(f"Limits entered by hand for {', '.join(hand[:6])}{more}: the report's "
               f"\"Where the limits came from\" names each value's source.")
    return out


def write_charts(solved: dict, d, proc, out_dir: str, *, gds: str | None = None,
                 cell: str | None = None, command: str = "", what: str = "emir run",
                 log=print) -> list:
    """The run's EM/IR chart PNG (dark, and light as `_light`) into the
    --html directory (`emir.chart.write_run_charts`). Without matplotlib nothing is
    drawn (EMIR-I720); a drawing that fails is EMIR-W720, never a failed run.
    Returns the paths written."""
    import os

    from .errors import EmirError, notice
    try:
        import matplotlib  # noqa: F401
    except ImportError:
        from .repo import install_hint
        notice("EMIR-I720", f"the EM/IR chart PNG was not drawn: matplotlib is not "
               f"installed ({install_hint('report')})", log)
        return []
    try:
        from .chart import write_run_charts
        paths, how = write_run_charts(solved, d, proc, out_dir, gds=gds, cell=cell,
                                      command=command, what=what)
    except EmirError:
        raise
    except Exception as e:                 # noqa: BLE001 - the picture only: the run stands
        if os.environ.get("EMIR_DEBUG"):
            raise
        notice("EMIR-W720", f"the EM/IR chart was not drawn: {type(e).__name__}: {e}", log)
        return []
    if gds and not how:
        notice("EMIR-I721", "the chart shows the results without the layout under them: "
               "neither klayout nor gdstk is installed", log)
    log(f"  chart          : {paths[0]}" if paths else "  chart          : none")
    return paths
