"""HTML report for the extraction path — one net, whichever mode produced it.

It is built around **vector**: the net is drawn as inline SVG from the DSPF's
own node coordinates, one line per extracted resistor, so it zooms losslessly
in the browser instead of turning to mush like a PNG, and it costs no extra
tooling — the geometry is already in the file being analysed.

Everything the colour encodes is also in a table, sortable and readable without
colour vision, because a colour scale on its own is not a result.

Consumes the `check_net` / `rcc_check` return dict, so the same page serves
RCC, CC+grid and self-consistent runs — with the mode and the current
provenance stated at the top, since the number means different things depending
on which produced it.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from urllib.parse import quote

import numpy as np

from . import RESEARCH_DISCLAIMER, SCREENING_NOTE
from .dashboard import CSS, _bar, _e, _tile, legend_html
from .dspf import dyn_ratio, scale_text
from .errors import EmirError

#: what each route's numbers are, for the provenance table
_MODE_TEXT = {
    "rcc": "branch currents measured in the simulation",
    "cc-grid": "CC currents pushed through the R grid by superposition",
    "dc": "DC currents given on the command line, solved through the R grid",
}

#: index rows open their report: the URL is read from the row's data-href
ROWLINKS = """
document.querySelectorAll('tr.clickable[data-href]').forEach(function(r){
  r.addEventListener('click',function(){location.href=r.dataset.href;});});
"""

PANZOOM = """
(function(){
 var svg=document.getElementById('net'); if(!svg) return;
 var vb=svg.getAttribute('viewBox').split(/\\s+/).map(Number);
 var home=vb.slice(), cur=vb.slice(), drag=null;
 function apply(){svg.setAttribute('viewBox',cur.join(' '));
   document.getElementById('zlbl').textContent=
     (home[2]/cur[2]).toFixed(1)+'\\u00d7';}
 svg.addEventListener('wheel',function(e){e.preventDefault();
   var r=svg.getBoundingClientRect();
   var mx=cur[0]+(e.clientX-r.left)/r.width*cur[2];
   var my=cur[1]+(e.clientY-r.top)/r.height*cur[3];
   var k=e.deltaY<0?0.84:1/0.84;
   if(home[2]/(cur[2]*k)<0.9) k=home[2]/cur[2]/0.9;
   cur[2]*=k; cur[3]*=k;
   cur[0]=mx-(e.clientX-r.left)/r.width*cur[2];
   cur[1]=my-(e.clientY-r.top)/r.height*cur[3];
   apply();},{passive:false});
 svg.addEventListener('pointerdown',function(e){
   drag={x:e.clientX,y:e.clientY,vx:cur[0],vy:cur[1]};
   svg.setPointerCapture(e.pointerId);svg.style.cursor='grabbing';});
 svg.addEventListener('pointermove',function(e){ if(!drag)return;
   var r=svg.getBoundingClientRect();
   cur[0]=drag.vx-(e.clientX-drag.x)/r.width*cur[2];
   cur[1]=drag.vy-(e.clientY-drag.y)/r.height*cur[3];apply();});
 ['pointerup','pointercancel'].forEach(function(t){
   svg.addEventListener(t,function(){drag=null;svg.style.cursor='grab';});});
 document.getElementById('zreset').addEventListener('click',function(){
   cur=home.slice();apply();});
 // clicking a violation row frames that resistor
 document.querySelectorAll('tr[data-x]').forEach(function(tr){
   tr.addEventListener('click',function(){
     var x=+tr.dataset.x,y=+tr.dataset.y,w=home[2]/14;
     cur=[x-w/2,y-w/2*home[3]/home[2],w,w*home[3]/home[2]];apply();
     svg.scrollIntoView({behavior:'smooth',block:'center'});
     document.querySelectorAll('.hl').forEach(function(e){
       e.setAttribute('class',e.getAttribute('class').replace(' hl',''));});
     var seg=document.getElementById('seg-'+tr.dataset.seg);
     if(seg)seg.setAttribute('class',seg.getAttribute('class')+' hl');});
 });
 document.querySelectorAll('.zbar .mode').forEach(function(m){
   m.addEventListener('click',function(){var mode=m.dataset.mode;
     svg.setAttribute('data-mode',mode);
     document.querySelectorAll('.zbar .mode').forEach(function(o){
       o.setAttribute('aria-pressed',o===m?'true':'false');});
     document.querySelectorAll('.emleg').forEach(function(l){
       l.hidden=l.dataset.for!==mode;});
     svg.querySelectorAll('[data-em]').forEach(function(e){
       var c=e.getAttribute(mode==='ir'?'data-ir':'data-em');
       if(e.tagName==='line')e.setAttribute('stroke',c);else e.setAttribute('fill',c);});});});
 svg.addEventListener('pointermove',function(e){var r=svg.getBoundingClientRect();
   var x=cur[0]+(e.clientX-r.left)/r.width*cur[2],y=-(cur[1]+(e.clientY-r.top)/r.height*cur[3]);
   document.getElementById('zxy').textContent=x.toFixed(2)+', '+y.toFixed(2)+' \u00b5m';});
 var b=document.getElementById('themebtn');
 function eff(){var t=document.documentElement.getAttribute('data-theme');
   return t||(matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light');}
 if(b){b.textContent=eff()==='dark'?'\\u25D0 Light':'\\u25D1 Dark';
  b.addEventListener('click',function(){
   document.documentElement.setAttribute('data-theme',
     eff()==='dark'?'light':'dark');
   b.textContent=eff()==='dark'?'\\u25D0 Light':'\\u25D1 Dark';});}
 apply();
})();
"""

EXTRA_CSS = """
.netwrap{background:var(--surface);border:1px solid var(--ring);border-radius:12px;
 box-shadow:var(--shadow);padding:10px;position:relative}
#net{display:block;width:100%;height:auto;max-height:70vh;cursor:grab;
 touch-action:none;background:var(--page);border-radius:8px}
#net{background:#000}
#net .seg{stroke-linecap:butt;fill:none}
#net .hl{stroke:#ffffff;stroke-width:1.6px;vector-effect:non-scaling-stroke}
#net .tap{fill:#8a8f94} #net .port{fill:#ffffff;stroke:#000;stroke-width:1px;
 vector-effect:non-scaling-stroke}
#net .via{stroke:#000;stroke-width:1px;vector-effect:non-scaling-stroke}
#net .mark{fill:none;stroke:#ff3030;stroke-width:1.8px;vector-effect:non-scaling-stroke}
#net .ring{display:none} #net .ring circle{fill:none;stroke:#fff;stroke-width:2px;
 vector-effect:non-scaling-stroke} #net .ring text{fill:#fff;font-family:ui-monospace,monospace}
#net[data-mode="ir"] .mark{display:none} #net[data-mode="ir"] .ring{display:inline}
.sw.dot{border-radius:50%;width:9px;height:9px}
.sw.dot.port{background:#ffffff;border:1px solid #000} .sw.dot.tap{background:#8a8f94}
.zbar .mode[aria-pressed="true"]{color:var(--ink);font-weight:700}
.zbar{position:absolute;right:18px;top:18px;display:flex;gap:8px;align-items:center;
 background:var(--panel);border:1px solid var(--ring);border-radius:8px;
 padding:4px 8px;font-size:12px;color:var(--ink2)}
.zbar button{background:none;border:0;color:var(--accent);cursor:pointer;font:inherit}
@media (max-width:640px){
 .zbar{position:static;flex-wrap:wrap;margin:0 0 8px}
 #net{min-height:200px}
}
.hint{color:var(--muted);font-size:12px;margin:8px 2px 0}
.kv{display:grid;grid-template-columns:auto 1fr;gap:4px 16px;font-size:12.5px;
 color:var(--ink2)}
.kv b{color:var(--ink);font-weight:600}
"""



def _sc_line(sc: dict) -> str:
    """The self-consistent loop in one line of the provenance: how it ended,
    the pass reported, and the last residual against its tolerance."""
    from .selfconsist import state_text
    p = sc.get("passes") or []
    last = p[-1] if p else {}

    def mv(v):
        return "not a number" if v is None else f"{v:.4g} mV"
    return (f"self-consistent loop: {state_text(sc)}; pass {sc.get('reported_pass')} "
            f"reported of {len(p)} simulation(s); last residual "
            f"{mv(last.get('residual_mV'))} against a {mv(last.get('tolerance_mV'))} "
            f"tolerance")

def _mr(res) -> dict:
    """max_ratio, tolerating older results that stored a scalar."""
    m = res.get("max_ratio", 0.0)
    if isinstance(m, dict):
        return {"all": m.get("all", 0.0), "wire": m.get("wire", 0.0),
                "via": m.get("via", 0.0)}
    return {"all": float(m), "wire": float(m), "via": 0.0}


def _sev_of(ratio, warn):
    return "FAIL" if ratio >= 1.0 else ("WARN" if ratio >= warn else "clean")


def _svg(dspf, res, net, warn=0.8, max_px=1100, proc=None) -> str:
    """The net as vector, on the layout's black canvas: one line per
    extracted resistor at its real place and width, coloured by its
    governing EM ratio on EMIR's scale (`emir.style`), the worst drawn last
    and each violation outlined red; `data-ir` holds its IR colour for the
    IR view (the page's toggle), where a ring marks the worst node."""
    from . import style as S
    from .scene import _rank_key, segments
    nw = res["network"]
    xy = dspf.node_xy
    viol = {c["name"]: c for c in res["clusters"]}
    shapes = {s_["name"]: s_ for s_ in (segments(res, dspf, net, proc) if proc else [])}
    ir_top = max((v["ir"] for v in shapes.values() if S.finite(v.get("ir"))), default=0.0)
    segs, pts = [], []
    for i, r in enumerate(nw.res):
        a, b = xy.get(r["n1"]), xy.get(r["n2"])
        if a is None or b is None:
            continue
        pts += [a, b]
        c = viol.get(r["name"])
        sev = c["severity"] if c else "clean"
        sh = shapes.get(r["name"]) or {}
        ratio = sh.get("ratio") if sh else (c["ratio"] if c else 0.0)
        # metal width in um is the natural stroke: the picture is then a plan
        # view, and a neck reads as a neck
        w = r.get("w") or 0.05
        segs.append((sev, ratio, i, a, b, w, r, sh))
    if not pts:
        return ""
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    pad = 0.05 * max(x1 - x0, y1 - y0, 1e-6)
    x0, x1, y0, y1 = x0 - pad, x1 + pad, y0 - pad, y1 + pad
    W, H = x1 - x0, y1 - y0
    px = max_px
    py = max(220, int(px * H / max(W, 1e-9)))

    # the worst drawn last: the thing you are looking for is on top
    segs.sort(key=lambda s_: _rank_key(s_[1]))
    body, vias, marks = [], [], []
    lmap = res.get("layer_map") or {}
    for sev, ratio, i, a, b, w, r, sh in segs:
        em = S.em_colour(ratio, warn)
        ir = S.ir_colour(sh.get("ir"), ir_top) if sh.get("ir") is not None else S.UNCHECKED
        model = bool(sh.get("model"))
        rt = (S.MODEL_TEXT if model else "no limit stated" if ratio is None
              else S.fmt_ratio(ratio))
        title = (f'<title>{_e(r["name"])} {_e(r.get("layer") or "")} {_e(rt)}'
                 + (f' · IR {_e(S.fmt_mv(sh["ir"]))}' if sh.get("ir") is not None else "")
                 + '</title>')
        is_via = (lmap.get(r.get("layer")) or (None,))[0] == "via" or \
            (abs(a[0] - b[0]) < 1e-9 and abs(a[1] - b[1]) < 1e-9)
        if sev == "FAIL" or (ratio is not None and not S.finite(ratio)):
            marks.append((a, b, w))
        if is_via:
            # a via (often a zero-length segment, invisible as a line) is a
            # square marker, drawn after the taps and ports, so a failing via
            # is seen on the map
            vias.append((sev, i, (a[0] + b[0]) / 2, (a[1] + b[1]) / 2, title, em, ir))
            continue
        # y is flipped: layout up, SVG down
        body.append(
            f'<line id="seg-{i}" class="seg {sev}" x1="{a[0]:.4f}" '
            f'y1="{-a[1]:.4f}" x2="{b[0]:.4f}" y2="{-b[1]:.4f}" '
            f'stroke-width="{w:.4f}" stroke="{S.MODEL if model else em}" '
            f'data-em="{S.MODEL if model else em}" data-ir="{S.MODEL if model else ir}"'
            + (f' stroke-dasharray="{w:.4f} {2 * w:.4f}"' if model else "")
            + f'>{title}</line>')
    for t in dspf.net_taps(net):
        p = t["xy"]
        body.append(f'<circle class="tap" cx="{p[0]:.4f}" cy="{-p[1]:.4f}" '
                    f'r="{0.008 * max(W, H):.4f}"><title>tap {_e(t["name"])}'
                    f'</title></circle>')
    for nm, d in dspf.pins.items():
        if d.get("net") != net:
            continue
        p = d["xy"]
        body.append(f'<circle class="port" cx="{p[0]:.4f}" cy="{-p[1]:.4f}" '
                    f'r="{0.014 * max(W, H):.4f}"><title>port {_e(nm)}</title>'
                    f'</circle>')
    s = 0.015 * max(W, H)
    for sev, i, cx, cy, title, em, ir in vias:
        body.append(f'<rect id="seg-{i}" class="via {sev}" x="{cx - s / 2:.4f}" '
                    f'y="{-cy - s / 2:.4f}" width="{s:.4f}" height="{s:.4f}" fill="{em}" '
                    f'data-em="{em}" data-ir="{ir}">{title}</rect>')
    m = 0.006 * max(W, H)
    for a, b, w in marks:                  # violations outlined red (EM view)
        bx0, bx1 = min(a[0], b[0]) - w / 2 - m, max(a[0], b[0]) + w / 2 + m
        by0, by1 = min(a[1], b[1]) - w / 2 - m, max(a[1], b[1]) + w / 2 + m
        bw, bh = max(bx1 - bx0, s + 2 * m), max(by1 - by0, s + 2 * m)
        cxm, cym = (bx0 + bx1) / 2, (by0 + by1) / 2
        body.append(f'<rect class="mark" x="{cxm - bw / 2:.4f}" y="{-cym - bh / 2:.4f}" '
                    f'width="{bw:.4f}" height="{bh:.4f}"/>')
    V = res.get("V")
    if V is not None and len(V) and np.isfinite(np.asarray(V, dtype=float)).any():
        k = int(np.nanargmax(np.abs(np.asarray(V, dtype=float))))
        p = xy.get(nw.nodes[k])
        # a drop that prints as ~0 has no worst node worth a ring
        if p is not None and not S.fmt_mv(abs(float(V[k]))).startswith("~0"):
            rr = 0.018 * max(W, H)
            lab = f"worst node {S.fmt_mv(abs(float(V[k])))}"
            tx, ty, anchor, fs = ring_label(p, rr, lab, (x0, y0, x1, y1), max(W, H))
            body.append(f'<g class="ring"><circle cx="{p[0]:.4f}" cy="{-p[1]:.4f}" '
                        f'r="{rr:.4f}"/><text x="{tx:.4f}" y="{ty:.4f}" '
                        f'text-anchor="{anchor}" font-size="{fs:.4f}">{_e(lab)}</text></g>')
    return (f'<svg id="net" viewBox="{x0:.4f} {-y1:.4f} {W:.4f} {H:.4f}" '
            f'width="{px}" height="{py}" data-mode="em" '
            f'role="img" aria-label="extracted {net} net on the layout\'s black canvas, each '
            f'element coloured by its EM ratio, violations outlined">' + "".join(body)
            + "</svg>")


#: the worst-node label's size, as a fraction of the map's larger side (the
#: map is drawn about 1100 px wide: about 15 px)
RING_FONT = 0.014
#: the width of a character of the label, in font sizes (the page's font is
#: monospace: 0.6 em a character)
CHAR_EM = 0.6


def ring_label(p, rr: float, text: str, box, size: float) -> tuple:
    """`(x, y, anchor, font_size)` of the worst-node label in SVG coordinates
    (y down): beside the ring, on the side with room, so the whole label is
    inside the map's view box `box` = (x0, y0, x1, y1) in layout um. A rail's
    worst node is usually its far end, at the box's edge: the label then
    runs back towards the map, never off it."""
    x0, y0, x1, y1 = box
    fs = RING_FONT * size
    est = CHAR_EM * fs * len(text)
    gap = rr * 1.2
    if p[0] + gap + est <= x1:
        x, anchor = p[0] + gap, "start"
    elif p[0] - gap - est >= x0:
        x, anchor = p[0] - gap, "end"
    else:                                   # wider than either side: centred, clamped
        x = min(max(p[0], x0 + est / 2), x1 - est / 2)
        anchor = "middle"
    # above the ring when there is room (SVG y = -layout y), else below it
    y = -p[1] - rr * 1.2
    if y - fs < -y1:
        y = -p[1] + rr * 1.2 + fs
    y = min(max(y, -y1 + fs), -y0)
    return x, y, anchor, fs


def _legend_html(mode: str, warn: float, ir_top: float, unchecked: bool, lost: bool,
                 model: bool = False) -> str:
    """One legend of the net page (`dashboard.legend_html`), with the map's
    port and device-tap dots."""
    return legend_html(mode, warn, ir_top, unchecked=unchecked, lost=lost,
                       markers=mode == "em", rings=True, model=model,
                       extra='<span><i class="sw dot port"></i> port</span>'
                             '<span><i class="sw dot tap"></i> device tap</span>')


def _metric_rows(res, proc):
    from .engine import METRICS
    out = []
    for m in METRICS:
        lim = proc.limited(m)
        rs = [c["ratios"].get(m) for c in res["clusters"] if c.get("ratios")]
        rs = [r for r in rs if r is not None]
        worst = max(rs) if rs else None
        out.append((m, lim, worst))
    return out


def net_report(res, dspf, net, proc, out_dir, file_name=None, title=None,
               command=None) -> str:
    """Write the one-net HTML report. Returns the path."""
    from .output import contained, make_dir, safe_component
    make_dir(out_dir, code="EMIR-E701")
    # the file name comes from input names (the DSPF's design, the net):
    # one component, inside out_dir (`*|DESIGN "../../x"` wrote outside it)
    stem = (file_name[:-5] if file_name and file_name.endswith(".html")
            else file_name or net)
    path = contained(out_dir, os.path.join(out_dir, safe_component(stem) + ".html"))
    cl = res["clusters"]
    n_fail = sum(1 for c in cl if c["severity"] == "FAIL")
    n_warn = len(cl) - n_fail
    verdict = "FAIL" if n_fail else ("WARN" if n_warn else "CLEAN")
    w = res["worst"]
    dyn = res.get("dynamic")
    from .decap import basis_label                     # --- feature decap ---
    from .decap import html_section as decap_html
    basis = basis_label(res)
    mode = res.get("mode", "dc")
    nw = res["network"]

    tiles = [
        _tile("verdict", verdict, f"{n_fail} FAIL / {n_warn} WARN",
              {"FAIL": "f", "WARN": "w", "CLEAN": "g"}[verdict]),
        _tile("worst EM",
              f"{(w['ratio'] if w else _mr(res)['all']):.3f}×",
              f"{w['metric']} on {w['layer']} ({w['name']})"
              + (f"; {basis}" if basis else "") if w else
              f"worst checked branch, below the {proc.warn_fraction:.2f}× "
              f"warn fraction",
              "f" if w and w["ratio"] >= 1 else ("w" if w else "g")),
    ]
    # a net with no port has no feed to measure against, so the figure is the
    # spread across it -- invariant to the reference, but not an "IR drop"
    fed = bool(dspf.net_ports(net))
    tiles.append(_tile(
        "IR drop (avg currents)" if fed else "resistive spread",
        f"{res['ir_drop'] * 1e3:.2f} mV",
        "worst node vs the feed" if fed else
        "no port on this net — max minus min, not a drop from a supply"))
    if dyn:
        ratio, rnote = dyn_ratio(dyn["worst_V"], res["ir_drop"])
        tiles.append(_tile(
            "dynamic IR", f"{dyn['worst_V'] * 1e3:.4f} mV",
            (f"{ratio:.2f}× the average, " if ratio else f"{rnote}; ")
            + f"at {dyn['worst_node']} t={dyn['worst_t']:.4g} s",
            "w" if ratio and ratio > 1.5 else ""))
    tiles.append(_tile("network", f"{len(nw.res)}",
                       f"resistors, {len(nw.nodes)} nodes, "
                       f"{len(dspf.net_taps(net))} device taps"))

    # provenance: what produced these numbers, and what checks passed
    checks = [
        ("mode", f"<b>{_e(mode)}</b> — " + _MODE_TEXT.get(mode, "")
         + (" — " + _e(_sc_line(res.get("self_consistency") or {}))
            if res.get("self_consistent") else "")),
        ("currents", _e(res.get("metric_note", "?"))),
        ("temperature", f"analysis at <b>{proc.temp:g} °C</b>; extracted R "
         + ("already at temperature, solver scaling <b>off</b>"
            if res.get("r_at_temp") else
            _e(scale_text(False, res.get("rscale", 1.0), res.get("layer_map"))))),
        ("KCL residual", f"{res['kcl_residual']:.2e} A"),
    ]
    if "loop_residual" in res:
        n_off = res.get("n_offtree", 0)
        checks.append((
            "loop residual",
            f"{res['loop_residual'] * 1e3:.3e} mV over <b>{n_off}</b> "
            f"off-tree branches"
            + ("" if n_off else
               " — loop-free net, so this check proves nothing here")))
    lmap = {k: v[1] for k, v in res["layer_map"].items()}
    checks.append(("layer map", _e(", ".join(f"{k}→{v}"
                                             for k, v in sorted(lmap.items())))))
    # where this net's limits came from: entered by hand, imported, the
    # PDK's public data -- with each value's source, or the lack of one
    from .process_file import limits_lines, limits_provenance
    lim_lines = limits_lines(limits_provenance(proc, set(res["layer_map"].values())))
    if lim_lines:
        # a value with no source reads in the warn colour (EMIR-W320), as the
        # chart's note and the report name it
        checks.append(("limits from", "<br>".join(
            re.sub(r"`([^`<>]*)`", r"<code>\1</code>", _e(x))
            .replace("NO SOURCE", '<span class="sev WARN">NO SOURCE</span>')
            for x in lim_lines)))
    if res.get("unmapped"):
        checks.append(("not checked", _e(", ".join(res["unmapped"]))
                       + " — no PDK match"))
    vc = res.get("via_cuts") or {}
    if vc.get("assumed") or vc.get("area"):
        checks.append((
            "via cuts",
            (f"<b>{len(vc.get('assumed', []))}</b> via(s) with no cut count or "
             f"usable area checked as ONE cut (conservative); "
             if vc.get("assumed") else "")
            + (f"{len(vc['area'])} counted from the cut area and the process "
               f"vcwidth" if vc.get("area") else "")))

    mrows = "".join(
        f"<tr><td>{m}</td><td>"
        + ("<span class=\"sev WARN\">value only — no PDK limit</span>"
           if not lim else "checked")
        + "</td><td class=\"num\">"
        + (f"{worst:.3f}×" if worst is not None else "—")
        + "</td></tr>"
        for m, lim, worst in _metric_rows(res, proc))

    vrows = []
    names = [r["name"] for r in nw.res]
    from .layoutview import fix_hint
    for c in cl[:400]:
        i = names.index(c["name"])
        b = c.get("bbox") or (0, 0, 0, 0)
        x, y = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        vrows.append(
            f'<tr data-x="{x:.4f}" data-y="{-y:.4f}" data-seg="{i}">'
            f'<td><span class="sev {c["severity"]}">{c["severity"]}</span></td>'
            f'<td>{_e(c["name"])}</td><td>{_e(c["layer"])}</td>'
            f'<td>{_e(c["kind"])}</td><td>{_e(c["metric"])}</td>'
            f'<td class="num">{c["ratio"]:.3f}×{_bar(c["ratio"], c["severity"])}</td>'
            f'<td class="num">{c["value"] * 1e3:.4f}</td>'
            f'<td class="num">'
            + (f'{c["w"]:.3f} µm' if c.get("w") else
               (f'{c["cuts"]} cut{"" if c["cuts"] == 1 else "s"}' if c.get("cuts")
                else "—"))
            + f'</td><td class="num">{x:.3f}, {y:.3f}</td>'
            f'<td>{_e(fix_hint(c) or "none needed (below the limit)")}</td></tr>')

    drows = ""
    if dyn:
        order = np.argsort(-dyn["worst"])[:25]
        drows = "".join(
            f'<tr><td>{_e(dyn["nodes"][i])}</td>'
            f'<td class="num">{dyn["worst"][i] * 1e3:.4f}</td>'
            f'<td class="num">{dyn["t_worst"][i]:.4g}</td></tr>'
            for i in order)

    svg = _svg(dspf, res, net, warn=proc.warn_fraction, proc=proc)
    from . import style as S
    from .scene import segments
    _sh = segments(res, dspf, net, proc)
    _irt = max((v["ir"] for v in _sh if S.finite(v.get("ir"))), default=0.0)
    _model = any(v.get("model") for v in _sh)
    legends = (_legend_html("em", proc.warn_fraction, _irt,
                            any(v["ratio"] is None and not v.get("model") for v in _sh),
                            any(v["ratio"] is not None and not S.finite(v["ratio"])
                                for v in _sh), model=_model)
               + _legend_html("ir", proc.warn_fraction, _irt, False,
                              any(v["ir"] is not None and not S.finite(v["ir"]) for v in _sh),
                              model=_model)
               .replace('class="emleg" data-for="ir"', 'class="emleg" data-for="ir" hidden'))
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(title or f'{dspf.design} {net}')} — EMIR</title>
<style>{CSS}{EXTRA_CSS}</style></head><body>
<header class="top"><div class="topin">
 <span class="brand">EMIR · EXTRACTED</span>
 <span class="cell">{_e(dspf.design or '?')}</span>
 <span class="sub">net <b>{_e(net)}</b> · {_e(mode)} · {now}</span>
 <span class="chip {verdict}">{verdict}</span>
 <span class="spacer"></span>
 <button class="tgl" id="themebtn">Theme</button>
</div></header>
<div class="wrap">
<div class="tiles">{''.join(tiles)}</div>

<h2>Net — drag to pan, scroll to zoom</h2>
<div class="netwrap">
 <div class="zbar"><button class="mode" data-mode="em" aria-pressed="true">EM ratio</button>
  <button class="mode" data-mode="ir" aria-pressed="false">IR drop</button>
  <span id="zxy"></span><span id="zlbl">1.0×</span>
  <button id="zreset">reset</button></div>
 {svg or '<p class="hint">No node coordinates in the DSPF — nothing to draw.</p>'}
</div>
{legends}
<p class="hint">Vector, not a bitmap: it stays sharp at any zoom. Each element is drawn
 from the DSPF's own coordinates on the layout's black canvas, its stroke the drawn metal
 width (a neck reads as a neck), the worst on top; violations are outlined red. IR drop
 colours each element by its drop from the net's feed on the same ramp and rings the
 worst node. Hover an element for its name and numbers (and the cursor's x, y in µm);
 click a row in the table below to frame it.</p>

<h2>Provenance and self-checks</h2>
<div class="tblwrap" style="padding:14px 16px"><div class="kv">
{''.join(f'<span>{_e(k)}</span><span>{v}</span>' for k, v in checks)}
</div></div>

<h2>Per-metric coverage</h2>
<div class="tblwrap"><table><thead><tr><th>metric</th><th>status</th>
<th class="num">worst ratio</th></tr></thead><tbody>{mrows}</tbody></table></div>

<h2>Violations — {len(cl)} at or above {proc.warn_fraction:.2f}×{' · ' + _e(basis) if basis else ''}</h2>
<div class="tblwrap"><table><thead><tr><th></th><th>resistor</th><th>layer</th>
<th>kind</th><th>metric</th><th class="num">ratio</th>
<th class="num">value <span class="u">mA/µm or mA/cut</span></th>
<th class="num">width / cuts</th>
<th class="num">x, y <span class="u">µm</span></th><th>fix</th></tr></thead>
<tbody>{vrows and ''.join(vrows) or
        '<tr><td colspan="10" style="color:var(--muted)">nothing at or above the warn fraction</td></tr>'}
</tbody></table></div>
{'<h2>Worst dynamic excursion by node</h2><div class="tblwrap"><table><thead><tr><th>node</th><th class="num">worst mV</th><th class="num">at t (s)</th></tr></thead><tbody>' + drows + '</tbody></table></div>' if dyn else ''}
{decap_html(res)}

<footer class="foot">
<p><b>{_e(SCREENING_NOTE)}</b> Foundry-qualified EM/IR analysis, with the PDK's full
 width, length and temperature rules, remains the sign-off flow.</p>
<p>{_e(RESEARCH_DISCLAIMER)}</p>
{f'<p>Reproduce: <code>{_e(command)}</code></p>' if command else ''}
</footer>
</div>
<script>{PANZOOM}</script></body></html>"""
    from .output import write_text
    write_text(path, doc, code="EMIR-E701")
    # sidecar so the index can be rebuilt from the directory alone, without
    # re-running the analysis
    with _writing(path[:-5] + ".json") as f:
        json.dump({"href": os.path.basename(path), "design": dspf.design or "?",
                   "net": net, "mode": mode, "verdict": verdict,
                   "worst": float(w["ratio"]) if w else float(_mr(res)["all"]),
                   "worst_wire": float(_mr(res)["wire"]),
                   "worst_via": float(_mr(res)["via"]),
                   "ir": float(res["ir_drop"]),
                   "dyn": float(dyn["worst_V"]) if dyn else None,
                   "res": len(nw.res), "when": now,
                   "em_basis": (res.get("em_basis") or {}).get("used"),
                   "em_basis_label": basis or None}, f, indent=1)
    return path


class _writing:
    """`open(path, "w")` whose failure is EMIR-E701 (the --html directory)."""

    def __init__(self, path):
        self.path = path

    def __enter__(self):
        try:
            self.f = open(self.path, "w", encoding="utf-8")
        except OSError as e:
            raise EmirError("EMIR-E701", f"cannot write {self.path}: {e.strerror or e}",
                            path=self.path) from e
        return self.f

    def __exit__(self, *exc):
        self.f.close()
        return False


def index_dir(out_dir, file_name="index.html") -> str:
    """Rebuild the landing page from whatever reports are in `out_dir`."""
    entries = []
    for fn in sorted(os.listdir(out_dir)):
        if fn.endswith(".json"):
            try:
                entries.append(json.load(open(os.path.join(out_dir, fn),
                                              encoding="utf-8")))
            except (ValueError, OSError):
                continue
    entries.sort(key=lambda e: -e.get("worst", 0))
    return net_index(entries, out_dir, file_name=file_name)


def _vcls(e):
    """Colour the via column by its own verdict, and bold it when the via is
    the binding constraint -- otherwise a via failure hides behind a larger
    wire failure."""
    v, w = e.get("worst_via", 0.0), e.get("worst_wire", e.get("worst", 0.0))
    return _sev_style(v, bold=v > w)


def _wcls(e):
    """Colour the wire column by its own verdict (a failing wire is as red as
    a failing via)."""
    w, v = e.get("worst_wire", e.get("worst", 0.0)), e.get("worst_via", 0.0)
    return _sev_style(w, bold=w >= v)


def _sev_style(r, bold=False) -> str:
    colour = ("var(--fail)" if r >= 1.0 else "var(--warn)" if r >= 0.8 else None)
    if colour:
        return f' style="color:{colour};font-weight:700"'
    return ' style="font-weight:700"' if bold else ""


#: the index's chart images follow the page's theme (dark: the plain file,
#: light: its `_light` twin)
THEMED_IMAGES = """
(function(){var root=document.documentElement;
 function eff(){var t=root.getAttribute('data-theme');if(t)return t;
   return matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light';}
 function paint(){var d=eff()==='dark';
  document.querySelectorAll('img[data-dark]').forEach(function(im){
    var s=d?im.dataset.dark:(im.dataset.light||im.dataset.dark);
    if(im.getAttribute('src')!==s)im.setAttribute('src',s);});}
 var b=document.getElementById('themebtn');if(b)b.addEventListener('click',paint);
 matchMedia('(prefers-color-scheme:dark)').addEventListener('change',paint);paint();})();
"""


def _charts_html(out_dir) -> str:
    """The run's EM/IR chart (`emir.chart.write_run_charts`), when --html drew
    it: every net on the layout, EM and IR, beside the verdict and the worst
    items; the picture to relay to a person."""
    name = "emir_chart.png"
    if not os.path.isfile(os.path.join(out_dir, name)):
        return ""
    light = name.replace(".png", "_light.png")
    lt = light if os.path.isfile(os.path.join(out_dir, light)) else name
    what = "EM and IR of every net on the layout"
    return ('<h2>Chart</h2><style>.chart{margin:0 0 18px}.chart img{width:100%;'
            'height:auto;border-radius:8px;border:1px solid var(--ring)}.chart figcaption'
            '{color:var(--muted);font-size:12px;margin-top:6px}</style>'
            f'<figure class="chart"><img data-dark="{_e(name)}" data-light="{_e(lt)}" '
            f'src="{_e(name)}" alt="{_e(what)} (EMIR chart)"><figcaption>{_e(what)} · '
            f'<a href="{_e(name)}">{_e(name)}</a> · <a href="{_e(lt)}">{_e(lt)}</a>'
            f'</figcaption></figure>')


def net_index(entries, out_dir, file_name="index.html") -> str:
    """A tiny landing page over several per-net reports."""
    # the row links through a data attribute and one listener: an href built
    # into an onclick handler ran script from a quote in the design's name
    # (html.escape's &#x27; is decoded before the handler runs)
    rows = "".join(
        f'<tr class="clickable" data-href="{_e(quote(str(e["href"])))}">'
        f'<td><span class="sev {_e(e["verdict"])}">{_e(e["verdict"])}</span></td>'
        f'<td><a href="{_e(quote(str(e["href"])))}">{_e(e["design"])} · '
        f'<b>{_e(e["net"])}</b></a></td><td>{_e(e["mode"])}'
        + (f' · {_e(e["em_basis_label"])}' if e.get("em_basis_label") else "") + '</td>'
        f'<td class="num"{_wcls(e)}>{e.get("worst_wire", e["worst"]):.3f}×</td>'
        f'<td class="num"{_vcls(e)}>{e.get("worst_via", 0.0):.3f}×</td>'
        f'<td class="num">{e["ir"] * 1e3:.4f}</td>'
        f'<td class="num">' + (f'{e["dyn"] * 1e3:.4f}' if e.get("dyn") else "—")
        + f'</td><td class="num">{e["res"]}</td></tr>'
        for e in entries)
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>EMIR — extracted nets</title><style>{CSS}{EXTRA_CSS}</style></head><body>
<header class="top"><div class="topin"><span class="brand">EMIR · EXTRACTED</span>
<span class="cell">nets analysed</span><span class="spacer"></span>
<button class="tgl" id="themebtn">Theme</button></div></header>
<div class="wrap">{_charts_html(out_dir)}<h2>Runs</h2><div class="tblwrap"><table><thead><tr>
<th></th><th>design · net</th><th>mode</th><th class="num">worst wire</th><th class="num">worst via</th>
<th class="num">avg IR <span class="u">mV</span></th>
<th class="num">dyn IR <span class="u">mV</span></th>
<th class="num">resistors</th></tr></thead><tbody>{rows}</tbody></table></div>
<footer class="foot"><p><b>{_e(SCREENING_NOTE)}</b> Foundry-qualified EM/IR remains
 the sign-off flow; these numbers screen the layout.</p>
<p>{_e(RESEARCH_DISCLAIMER)}</p></footer>
</div><script>{PANZOOM}{ROWLINKS}{THEMED_IMAGES}</script></body></html>"""
    path = os.path.join(out_dir, file_name)
    from .output import write_text
    return write_text(path, doc, code="EMIR-E701")
