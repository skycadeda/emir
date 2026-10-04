"""Hierarchical (chip-level) EM/IR: block abstracts at black-boxed pins.

A chip top is too big to screen flat: an RCC transient of it converges poorly
and slowly, and even the cc-grid route needs one solve per device tap. The
hierarchy splits the work the way the design is split.

1. **Each block, once** (`emir abstract`). The block's R-only DSPF and its CC
   simulation give
   * its own EM verdict and IR, with every pin of a supply held at one ideal
     potential (the boundary an ideal top level gives it);
   * the current each pin pushes into the top-level net: one period of its
     waveform on a uniform grid (`ports[].wave`) and the exact window scalars;
   * the exact Norton conductance between the pins of a supply
     (`y_ports`, a Schur complement of the block grid), so the top sees the
     block's own metal between two pins of a supply fed from two sides;
   * a two-moment R + C lump of the block's capacitance, and its internal
     dynamic droop when the DSPF carries capacitance.
2. **The top, with the blocks black-boxed** (`emir run TOP.dspf --abstract
   FILE@INST ...`). A block pin is an `*|I` record of the top DSPF; each
   abstract port lands on its record (by name, or by coordinates through
   `--place`), injects its current there, and `y_ports` is stamped between
   the records. The stock solve and the one verdict function
   (`dspf.check_net`, `dspf.em_clusters`) do the rest.
3. **Budgets and back-annotation.** The IR budget of a block is the top-level
   drop at its worst pin plus its internal drop (`--budget-mv`). The
   top-level pin voltages (`--port-voltages-out`) go back into the block
   (`emir abstract --port-voltages F@INST`), which re-checks its EM with the
   pass-through current a supply fed from several sides carries.

4. **Recursion and the top's own devices.** A mid-level block is abstracted
   from its own devices and its sub-blocks' abstracts (`emir abstract MID.dspf
   --cc-sim MID.raw --mode cc-grid --abstract SUB.abstract.json@XSUB`): the sub-blocks'
   currents and conductances join its grid, so its abstract composes theirs.
   The top's devices outside the blocks come from the top's CC simulation
   (`emir run ... --abstract ... --cc-sim TOP.raw`).

The grid is linear, so the split is exact: on the synthetic chip of
`examples/05_chip_level` every top-level branch (avg, rms, peak) matches a
flat run of the same network to machine precision, and the back-annotated
block branches do too.

**Sign convention.** A current is positive INTO the net it is on (the
package's convention). An abstract port current is what the block pushes INTO
THE TOP-LEVEL NET at that pin, so a supply load is negative.

**Alignment of the blocks in time** (`--align`):

* ``inphase`` (the default): per-pin scalars, `sum |T| I` -- the upper bound
  over every alignment.
* ``sync``: one clock reference. Each block's stored period goes on a common
  grid (the least common multiple of the periods), shifted by its phase
  reference plus `--phase`. Exact for that alignment; `--phase-sweep INST:N`
  takes the worst over N phases of one block (related clocks whose phase is
  not known).
* ``async``: unrelated clocks. avg exact, rms the long-time value
  (cross-block covariances average out), peak the worst over every relative
  phase. NOT an upper bound for related clocks (EMIR-W802).

Screening, not sign-off.
"""
from __future__ import annotations

import copy
import datetime
import itertools
import json
import math
import os

import numpy as np

from .errors import EmirError, notice, step

from .hier_cli import ALIGNS, DEFAULT_POINTS, ORIENT, PLACE_TOL_UM  # noqa: F401

ABSTRACT_SCHEMA = "emir.abstract/1"
PV_SCHEMA = "emir.port_voltages/1"
#: the longest common period sync alignment builds (samples)
MAX_COMMON_SAMPLES = 1 << 21
#: top-level droop at a block pin, as a fraction of its nominal supply, above
#: which the re-characterisation loop is advised (EMIR-W805)
DROOP_FRACTION = 0.02
#: below this fraction of the peak kept by resampling, EMIR-W803
PEAK_KEEP = 0.99
#: memory per chunk of a time-stepped back-annotation (bytes)
CHUNK_BYTES = 1 << 28
BOUNDARY_SHORT = "every port of the net held at one ideal potential (short-circuit)"


def _quiet(*_a, **_k):
    return None


# ===================================================================== DSPF helpers


def ports_of(d, net: str) -> list:
    """The `*|P` port nodes of `net` that its resistors touch, sorted."""
    nodes = {n for r in d.net_resistors(net) for n in (r["n1"], r["n2"])}
    return [p for p in d.net_ports(net) if p in nodes]


def _copy(d):
    """A copy of a parsed DSPF whose resistor list and node maps can change."""
    d2 = copy.copy(d)
    d2.res = list(d.res)
    d2.node_net = dict(d.node_net)
    d2.node_xy = dict(d.node_xy)
    d2.reindex()
    return d2


def with_short(d, net: str, ports: list):
    """`(copy, feed)`: `d` with the `ports` of `net` merged into the first one.

    Holding every pin at one potential is the boundary an ideal top level
    gives a block. Merging the nodes is exact (a star of small resistors is
    not: 1e-7 errors); each resistor keeps its original ends in `_n1o` and
    `_n2o`, so the current through every pin can be split back out.
    """
    feed = ports[0]
    pset = set(ports)

    def m(n):
        return feed if n in pset else n
    d2 = copy.copy(d)
    d2.res = [dict(r, n1=m(r["n1"]), n2=m(r["n2"]), _n1o=r["n1"], _n2o=r["n2"])
              for r in d.res]
    d2.inst_pins = {k: (dict(v, node=feed) if v["node"] in pset else v)
                    for k, v in d.inst_pins.items()}
    d2.cap = [dict(c, n1=m(c["n1"]), n2=m(c["n2"])) for c in d.cap]
    d2.dev = [dict(x, **{t: m(x[t]) for t in ("d", "g", "s", "b") if t in x})
              for x in d.dev]
    d2.node_net = dict(d.node_net)
    d2.node_xy = dict(d.node_xy)
    d2.reindex()
    return d2, feed


def _virtual_res(name, n1, n2, r) -> dict:
    """A resistor the run adds (a block's port conductance): no layer, so it
    is solved but never checked."""
    return {"name": name, "n1": n1, "n2": n2, "r": r, "layer": None, "w": None,
            "l": None, "via": None, "area": None, "cuts": None, "lvl": None,
            "bbox": None, "xy": None, "virtual": True}


def _laplacian(nw):
    import scipy.sparse as sp
    n = len(nw.nodes)
    g = nw.g
    return sp.coo_matrix(
        (np.concatenate([g, g, -g, -g]),
         (np.concatenate([nw.a, nw.b, nw.a, nw.b]),
          np.concatenate([nw.a, nw.b, nw.b, nw.a]))), shape=(n, n)).tocsr()


def _reached(nw, nodes) -> np.ndarray:
    """Mask of the nodes in a connected piece holding any of `nodes`."""
    lab = nw.pieces()
    keep = sorted({int(lab[nw.idx[p]]) for p in nodes})
    return np.isin(lab, keep)


def schur_ports(nw, ports: list) -> np.ndarray:
    """`Y = G_PP - G_PI G_II^-1 G_IP`: the block grid reduced to its ports.

    The exact Norton conductance of a linear grid. It is a Laplacian (rows
    sum to zero, off-diagonals not positive), so stamping `-Y[i, j]` as a
    conductance between ports i and j reproduces it. Interior nodes of a
    piece that holds no port do not take part.
    """
    from scipy.sparse.linalg import splu
    G = _laplacian(nw)
    P = [nw.idx[p] for p in ports]
    mask = _reached(nw, ports)
    mask[P] = False
    inner = np.flatnonzero(mask)
    Gpp = G[P][:, P].toarray()
    if not len(inner):
        return Gpp
    Gip = G[inner][:, P].toarray()
    X = splu(G[inner][:, inner].tocsc()).solve(Gip)
    Y = Gpp - Gip.T @ X
    return 0.5 * (Y + Y.T)


def _decap_lump(nw, feed, C, note) -> dict | None:
    """Two-moment R + C of the node capacitance `C` seen from the (shorted)
    pins: `C = sum c`, `R = c^T G^-1 c / C^2` (the s^2 term of the
    admittance matches a series R + C)."""
    from scipy.sparse.linalg import splu
    main = nw.main_piece(feed)
    if not C[main].sum() > 0:
        return None
    free = main.copy()
    free[nw.idx[feed]] = False
    fi = np.flatnonzero(free)
    ctot = float(C[main].sum())
    r = 0.0
    if len(fi):
        G = _laplacian(nw)
        x = splu(G[fi][:, fi].tocsc()).solve(C[fi])
        r = float(C[fi] @ x) / ctot ** 2
    return {"c_F": ctot, "r_ohm": r, "note": note}


# ===================================================================== waveforms


def _periodic_resample(w: np.ndarray, n: int) -> np.ndarray:
    """One period sampled at `len(w)` uniform points, re-sampled at `n`."""
    w = np.asarray(w, dtype=float)
    if len(w) == n:
        return w
    src = np.arange(len(w)) / len(w)
    return np.interp(np.arange(n) / n, src, w, period=1.0)


def _tile(w: np.ndarray, L: int, shift: int) -> np.ndarray:
    """`w` repeated over `L` samples, sample 0 moved to index `shift`."""
    return np.roll(np.tile(w, L // len(w)), shift)


def _stats(T: np.ndarray, W: np.ndarray, chunk_bytes: int = CHUNK_BYTES) -> tuple:
    """`(mean, rms, max, min)` over time of each row of `T @ W` (uniform
    samples), in time chunks so `rows x time` is never held at once."""
    rows, nt = T.shape[0], W.shape[1]
    chunk = max(1, int(chunk_bytes / 8 / max(rows, 1)))
    s1 = np.zeros(rows)
    s2 = np.zeros(rows)
    hi = np.full(rows, -np.inf)
    lo = np.full(rows, np.inf)
    for i in range(0, nt, chunk):
        Y = T @ W[:, i:i + chunk]
        s1 += Y.sum(axis=1)
        s2 += (Y * Y).sum(axis=1)
        np.maximum(hi, Y.max(axis=1), out=hi)
        np.minimum(lo, Y.min(axis=1), out=lo)
    return s1 / nt, np.sqrt(np.maximum(s2 / nt, 0.0)), hi, lo


# ===================================================================== 1. the block abstract


def _pin_transfer(d, net, nw, ports, feed, taps) -> np.ndarray:
    """`T[k, j]`: the share of tap j's current that leaves through port k,
    from the branch currents of the shorted network. A tap sitting on a port
    node drains straight through that port."""
    k_of = {p: k for k, p in enumerate(ports)}
    orig = {f"{t['inst']}:{t['pin']}": t["node"] for t in d.net_taps(net)}
    M = np.zeros((len(ports), len(nw.res)))
    for e, r in enumerate(nw.res):
        # Ib flows n1 -> n2: what leaves port p into the block's resistors
        # came in from the top level at p
        if r["_n1o"] in k_of:
            M[k_of[r["_n1o"]], e] += 1.0
        if r["_n2o"] in k_of:
            M[k_of[r["_n2o"]], e] -= 1.0
    T = np.zeros((len(ports), len(taps)))
    for j, t in enumerate(taps):
        o = t.get("_orig") or orig.get(f"{t['inst']}:{t['pin']}")
        if o in k_of:
            T[k_of[o], j] = 1.0
        elif len(ports) == 1:
            T[0, j] = 1.0
        else:
            # a unit current INTO the block net at the tap reaches the top
            # through the pins: what enters the top net at p is minus what
            # the top feeds into the block at p
            _, Ib = nw.solve({t["node"]: 1.0}, feed)
            T[:, j] = -(M @ Ib)
    return T


def _item_dicts(clusters, net, n=10) -> list:
    from .summary import item
    return [item(c, net) for c in clusters[:n]]


def _worst(res) -> dict | None:
    w = res.get("worst")
    if not w:
        return None
    return {k: w.get(k) for k in ("name", "layer", "kind", "metric", "ratio")}


#: the group of a CC simulation's own instance pins, next to the abstracts
DEVICES = "(devices)"


def characterise(d, net, w, proc, period=None, analysis=None, points=DEFAULT_POINTS,
                 v_nominal=None, subs=None, log=_quiet) -> tuple:
    """One block net: `(entry, ctx)`. `entry` is the abstract's record of the
    net; `ctx` keeps what back-annotation needs.

    `subs` (`{"insts": {INST: abstract}, "opts": hier_cli.Options}`) are the
    block's own sub-blocks, black-boxed in its DSPF: their abstracts drive
    their pins, their port conductances join the block's grid, and the
    block's devices (from `w`) and the sub-blocks go on one common grid
    (sync alignment). That is the recursion: a mid-level block abstracted
    from its sub-blocks' abstracts."""
    from . import ccsim, dynir
    from .dspf import Network, _verdict, check_net
    path = getattr(d, "path", None)
    if not d.has_net(net):
        from .dspf import did_you_mean
        raise EmirError("EMIR-E204", f"net {net!r} is not in {path}"
                        f"{did_you_mean(net, d.nets)}", dspf=path)
    if not d.net_resistors(net):
        raise EmirError("EMIR-E205", f"net {net!r} has no resistors in {path}", dspf=path,
                        net=net)
    ports = ports_of(d, net)
    if not ports:
        raise EmirError("EMIR-E803", f"block net {net!r} of {path} has no *|P port on its "
                        f"resistors: its pins must be DSPF ports (with coordinates) for "
                        f"its current to be characterised", dspf=path)
    ds, sub_taps, sets = d, [], []
    if subs:
        sub_taps, sets, stamps, _ = _block_taps(d, net, subs["insts"], subs["opts"], path,
                                                log=log)
        if stamps:
            ds = _copy(d)
            # a virtual resistor has no layer: the network leaves it unscaled
            for st in stamps:
                ds.res.append(_virtual_res(st["name"], *st["nodes"], 1.0 / st["g"]))
            ds.reindex()
    d2, feed = with_short(ds, net, ports)
    away = Network(d2, net).unreached(feed)
    if away:
        raise EmirError("EMIR-E207", f"block net {net!r}: {len(away)} node(s) are connected "
                        f"to none of its {len(ports)} port(s) (e.g. {', '.join(away[:3])}): "
                        f"the extracted net has an open", dspf=path)
    pset = set(ports)
    Wu = None
    if not sub_taps:
        dd = d2
        if subs:
            # a sub-block whose abstract does not cover this net: not a device
            dd = copy.copy(d2)
            dd.inst_pins = {k: v for k, v in d2.inst_pins.items()
                            if v["inst"] not in subs["insts"]}
            dd.reindex()
        ing = ccsim.cc_taps(dd, net, w, analysis=analysis, period=period, feed=None,
                            waveforms=True, log=log)
        taps, wts, tt = ing["taps"], ing["weights"], np.asarray(ing["time"], dtype=float)
        res = check_net(d2, net, proc, _inj(taps), feed, taps_list=taps, tap_weights=wts,
                        log=log)
        win = dict(ing["window"] or {})
        per = win.get("period")
    else:
        # the devices and the sub-blocks on one common grid (sync)
        for t in sub_taps:
            t["node"] = feed if t["node"] in pset else t["node"]
        orig = {f"{t['inst']}:{t['pin']}": t["node"] for t in d.net_taps(net)}
        dev, ing = _device_taps(d2, net, w, subs["insts"], analysis=analysis, period=period,
                                dt=min(t["_dt"] for t in sub_taps), orig=orig, log=log)
        all_taps = dev + sub_taps
        dt_c, L, Wc = _sync_grid(all_taps, subs["opts"].phase, log=log)
        taps = [{k: v for k, v in t.items() if k != "_wave"}
                | {"wave": Wc[j], "i_avg": float(Wc[j].mean()),
                   "i_rms": float(np.sqrt((Wc[j] * Wc[j]).mean())),
                   "i_peak": float(np.abs(Wc[j]).max())} for j, t in enumerate(all_taps)]
        wts = np.full(L, 1.0 / L)
        tt = np.arange(L) * dt_c
        res = check_net(d2, net, proc, _inj(taps), feed, taps_list=taps, tap_weights=wts,
                        log=log)
        per = L * dt_c
        win = {"t0": 0.0, "t1": per, "n_periods": 1, "period": per,
               "sensitivity": ((ing or {}).get("window") or {}).get("sensitivity", 0.0)}
        Wu = Wc
    nw = res["network"]
    W = np.array([t["wave"] for t in taps])                    # taps x time
    T = _pin_transfer(ds, net, nw, ports, feed, taps)          # ports x taps
    P = T @ W                                                  # ports x time
    kcl = float(np.abs(P.sum(axis=0) - W.sum(axis=0)).max())
    s_avg, s_rms, s_pk = P @ wts, np.sqrt((P * P) @ wts), np.abs(P).max(axis=1)
    # one representative period on a uniform grid: a phase shift is a roll
    span = float(per) if per else float(tt[-1] - tt[0])
    if Wu is not None:                         # already one common period, uniform
        t_ref, n = 0.0, int(W.shape[1])
        Pu = P
    else:
        t_ref = float(tt[-1] - span)
        n = int(points)
        tu = t_ref + np.arange(n) * (span / n)
        Pu = np.array([np.interp(tu, tt, row) for row in P])
    dt = span / n
    u_avg, u_rms, u_pk = Pu.mean(axis=1), np.sqrt((Pu * Pu).mean(axis=1)), np.abs(Pu).max(axis=1)
    big = s_pk > 0
    keep = float(np.min(u_pk[big] / s_pk[big])) if big.any() else 1.0
    if keep < PEAK_KEEP:
        notice("EMIR-W803", f"block net {net}: the {n}-point period kept {keep:.1%} of the "
               f"peak pin current -- raise --points", log)
    # capacitance seen at the shorted pins, and the internal dynamic droop
    C, cnote = dynir.grounded_caps(d2, net, nw.idx, proc=proc, log=log)
    lump = _decap_lump(nw, feed, C, cnote)
    dyn = None
    try:
        if not (C.sum() > 0 or d2.dev):
            raise EmirError("EMIR-E423", "no capacitance on the net")
        dr = dynir.dynamic_ir(d2, net, nw, feed, taps, tt, proc=proc, log=log)
        dyn = {"worst_mV": float(dr["worst_V"]) * 1e3, "node": dr["worst_node"],
               "t_s": float(dr["worst_t"])}
    except EmirError as e:
        if e.code != "EMIR-E423":              # no capacitance: no dynamic droop
            raise
    y = schur_ports(Network(ds, net, rscale=res["rscale"]), ports) if len(ports) > 1 else None
    main = nw.main_piece(feed)
    V = res["V"]
    av = np.where(main, np.abs(np.nan_to_num(V, nan=0.0)), 0.0)
    kw = int(np.argmax(av))
    verdict = _verdict(res, proc)
    entry = {
        "net": net, "v_nominal": v_nominal,
        "period_s": float(per) if per else None, "aperiodic": not per,
        "t_ref_s": t_ref, "n_samples": n, "dt_s": dt,
        "window": {k: win.get(k) for k in ("t0", "t1", "n_periods", "sensitivity")},
        "n_taps": len(taps),
        "ports": [{"name": p, "x_um": float(d.node_xy[p][0]), "y_um": float(d.node_xy[p][1]),
                   "layer": d.pins[p].get("layer"),
                   "i_avg": float(s_avg[j]), "i_rms": float(s_rms[j]),
                   "i_peak": float(s_pk[j]),
                   "uniform": {"i_avg": float(u_avg[j]), "i_rms": float(u_rms[j]),
                               "i_peak": float(u_pk[j])},
                   "wave": [float(v) for v in Pu[j]]} for j, p in enumerate(ports)],
        "y_ports": y.tolist() if y is not None else None,
        "decap": lump,
        "internal": {
            "verdict": verdict, "boundary": BOUNDARY_SHORT,
            "max_ratio": {k: float(v) for k, v in res["max_ratio"].items()},
            "worst": _worst(res), "n_violations": len(res["clusters"]),
            "worst_items": _item_dicts(res["clusters"], net),
            "ir_drop_mV": float(av[kw]) * 1e3, "ir_worst_node": nw.nodes[kw],
            "dynamic": dyn, "metric_note": res["metric_note"],
            "unmapped": list(res["unmapped"]), "no_width": list(res["no_width"]),
            "vias_assumed_one_cut": list(res["via_cuts"].get("assumed") or []),
            "kcl_residual_A": float(res["kcl_residual"]),
        },
        "sub_blocks": [{"instance": st["inst"], "block_net": st["bnet"],
                        "abstract": st.get("path"), "n_pins": len(st["ix"]),
                        "verdict": st["abstract"].get("verdict")
                        or st["abstract"]["internal"]["verdict"]} for st in sets],
        "back_annotation": None,
        "peak_retention": keep, "kcl_A": kcl, "verdict": verdict,
    }
    ctx = {"d": ds, "net": net, "ports": ports, "d2": d2, "feed": feed, "res": res,
           "taps": taps, "tt": tt, "t_ref": t_ref, "per": span, "n": n, "proc": proc,
           "Wu": Wu}
    return entry, ctx


def block_nets(d) -> list:
    """The nets an abstract covers by default: a port and instance pins."""
    return [n for n in sorted(d.nets) if ports_of(d, n) and d.net_taps(n)]


def _worst_verdict(vs) -> str:
    vs = list(vs)
    return "FAIL" if "FAIL" in vs else ("WARN" if "WARN" in vs else "CLEAN")


def block_abstract(dspf_path, sim_path, proc, nets=None, period=None, analysis=None,
                   points=DEFAULT_POINTS, v_nominal=None, port_voltages=None,
                   inputs=None, subs=None, log=_quiet) -> dict:
    """The `emir.abstract/1` document of a block.

    `v_nominal` is `{net or "*": volts}` (the nominal supply, for the
    top-level droop warning). `port_voltages` is `(path, instance)`: the
    nets that file covers are re-checked with that instance's top-level pin
    voltages (back-annotation)."""
    from . import RESEARCH_DISCLAIMER, SCREENING_NOTE, waves
    from .dspf import parse
    from .output import document
    d = parse(dspf_path)
    d.path = dspf_path
    w = waves.read(sim_path, analysis=analysis)
    if w.is_complex:
        raise EmirError("EMIR-E805", f"{sim_path} holds harmonics (PSS fd); an abstract "
                        f"needs the transient or PSS time-domain output", path=sim_path)
    if nets:
        missing = [n for n in nets if not d.has_net(n)]
        if missing:
            from .dspf import did_you_mean
            raise EmirError("EMIR-E204", f"net {missing[0]!r} is not in {dspf_path}"
                            f"{did_you_mean(missing[0], d.nets)}", dspf=dspf_path)
    else:
        nets = block_nets(d)
        if not nets:
            raise EmirError("EMIR-E804", f"{dspf_path}: no net has both a *|P port and "
                            f"instance pins; give --net", dspf=dspf_path)
    pv = load_port_voltages(port_voltages[0]) if port_voltages else None
    if pv is not None and port_voltages[1] not in pv["instances"]:
        raise EmirError("EMIR-E812", f"{port_voltages[0]} has no instance "
                        f"{port_voltages[1]!r} (it has "
                        f"{', '.join(sorted(pv['instances'])) or 'none'})",
                        path=port_voltages[0])
    if subs:
        have = {t["inst"] for t in d.inst_pins.values()}
        for inst in subs["insts"]:
            if inst not in have:
                raise EmirError("EMIR-E802", f"--abstract ...@{inst}: no *|I record of "
                                f"instance {inst!r} in {dspf_path}", dspf=dspf_path)
    vn = dict(v_nominal or {})
    doc = document(
        "abstract", disclaimer=SCREENING_NOTE, research_disclaimer=RESEARCH_DISCLAIMER,
        verdict=None, block=d.design,
        dspf=os.path.abspath(dspf_path), sim=os.path.abspath(sim_path),
        process=(inputs or {}).get("process"), temp_C=proc.temp, em_tref_C=proc.em_tref,
        created=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        inputs=dict(inputs or {}), nets={})
    annotated = 0
    for net in nets:
        entry, ctx = characterise(d, net, w, proc, period=period, analysis=analysis,
                                  points=points, v_nominal=vn.get(net, vn.get("*")),
                                  subs=subs, log=log)
        if pv is not None:
            ba = back_annotate(ctx, entry, pv, port_voltages, log=log)
            if ba is not None:
                entry["back_annotation"] = ba
                entry["verdict"] = ba["verdict"]
                annotated += 1
        if len(entry["ports"]) > 1 and entry["back_annotation"] is None:
            notice("EMIR-W804", f"block net {net}: its {len(entry['ports'])} ports were held "
                   f"at one potential; a supply fed from several sides also carries "
                   f"pass-through current from the top level -- back-annotate after the top "
                   f"run (--port-voltages-out, then emir abstract --port-voltages)", log)
        doc["nets"][net] = entry
    if pv is not None and not annotated:
        raise EmirError("EMIR-E812", f"{port_voltages[0]} has no entry for "
                        f"{port_voltages[1]} on {', '.join(nets)} (it has "
                        f"{', '.join(sorted(pv['instances'][port_voltages[1]].get('nets') or {}))})",
                        path=port_voltages[0])
    doc["verdict"] = _worst_verdict(e["verdict"] for e in doc["nets"].values())
    return doc


# ===================================================================== reading the files


def _bad(code, path, why, **ctx):
    return EmirError(code, f"{path}: {why}", path=path, **ctx)


def _read_json(path, code, schema):
    if not os.path.isfile(path):
        raise EmirError("EMIR-E101", f"{path} not found", path=path)
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (json.JSONDecodeError, UnicodeError) as e:
        raise _bad(code, path, f"not JSON ({e})") from e
    if not isinstance(doc, dict) or doc.get("schema") != schema:
        got = doc.get("schema") if isinstance(doc, dict) else type(doc).__name__
        raise _bad(code, path, f"not an {schema} document (schema: {got!r})")
    return doc


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def load_abstract(path: str) -> dict:
    """An `emir.abstract/1` file, checked for what a run needs (EMIR-E801)."""
    doc = _read_json(path, "EMIR-E801", ABSTRACT_SCHEMA)
    nets = doc.get("nets")
    if not isinstance(nets, dict) or not nets:
        raise _bad("EMIR-E801", path, "no `nets`")
    for bnet, nb in nets.items():
        where = f"net {bnet!r}"
        if not isinstance(nb, dict):
            raise _bad("EMIR-E801", path, f"{where} is not an object")
        n = nb.get("n_samples")
        if not (isinstance(n, int) and n > 0 and _num(nb.get("dt_s")) and nb["dt_s"] > 0
                and _num(nb.get("t_ref_s"))):
            raise _bad("EMIR-E801", path, f"{where}: n_samples, dt_s or t_ref_s missing")
        if nb.get("period_s") is not None and not (_num(nb["period_s"]) and nb["period_s"] > 0):
            raise _bad("EMIR-E801", path, f"{where}: period_s is not a positive number")
        ports = nb.get("ports")
        if not isinstance(ports, list) or not ports:
            raise _bad("EMIR-E801", path, f"{where}: no ports")
        for p in ports:
            ok = (isinstance(p, dict) and isinstance(p.get("name"), str)
                  and all(_num(p.get(k)) for k in ("x_um", "y_um", "i_avg", "i_rms", "i_peak"))
                  and isinstance(p.get("wave"), list) and len(p["wave"]) == n
                  and all(_num(v) for v in p["wave"]))
            if not ok:
                raise _bad("EMIR-E801", path, f"{where}: port {p.get('name') if isinstance(p, dict) else p!r} "
                           f"lacks name, x_um, y_um, i_avg, i_rms, i_peak or an "
                           f"{n}-sample wave")
        y = nb.get("y_ports")
        if y is not None:
            Y = np.asarray(y, dtype=float) if isinstance(y, list) else None
            if Y is None or Y.shape != (len(ports), len(ports)) or not np.isfinite(Y).all():
                raise _bad("EMIR-E801", path, f"{where}: y_ports is not a "
                           f"{len(ports)}x{len(ports)} matrix of numbers")
        if not isinstance(nb.get("internal"), dict):
            raise _bad("EMIR-E801", path, f"{where}: no `internal` verdict")
    return doc


def load_port_voltages(path: str) -> dict:
    """An `emir.port_voltages/1` file (EMIR-E812)."""
    doc = _read_json(path, "EMIR-E812", PV_SCHEMA)
    if not isinstance(doc.get("instances"), dict):
        raise _bad("EMIR-E812", path, "no `instances`")
    return doc


# ===================================================================== 2. the top level


def _xf(port, place):
    dx, dy, o = place
    a, b, c, e = ORIENT[o]
    x, y = port["x_um"], port["y_um"]
    return (a * x + b * y + dx, c * x + e * y + dy)


def match_ports(inst, bnet, ports, recs, place=None, tol=PLACE_TOL_UM, dspf=None,
                net=None, log=_quiet) -> dict:
    """`{port name: top node}` for the abstract ports of one block pin.

    One port and one record: by name. Several: by coordinates through the
    placement, refusing anything ambiguous -- never guess which pin shape
    takes which share of the current."""
    if len(ports) == 1 and len(recs) == 1:
        return {ports[0]["name"]: recs[0]["node"]}
    shown = ", ".join(f"{r['node']} ({r['xy'][0]:g}, {r['xy'][1]:g})" for r in recs)
    if len(recs) == 1 or len(ports) == 1:
        raise EmirError(
            "EMIR-E807", f"{inst}:{bnet}: the abstract has {len(ports)} port(s) but the top "
            f"DSPF connects the pin at {len(recs)} place(s) ({shown}): how the current "
            f"divides cannot be decided", dspf=dspf, net=net)
    if place is None:
        raise EmirError(
            "EMIR-E806", f"{inst}:{bnet} reaches net {net!r} at {len(recs)} records ({shown}) "
            f"and the abstract has {len(ports)} ports: give --place {inst}=DX,DY,ORIENT so "
            f"each port is matched by coordinates", dspf=dspf, net=net)
    out, used = {}, {}
    for p in ports:
        x, y = _xf(p, place)
        near = sorted((math.hypot(x - r["xy"][0], y - r["xy"][1]), r["node"]) for r in recs)
        hits = [nd for dist, nd in near if dist <= tol]
        if len(hits) != 1 or hits[0] in used:
            why = ("no record" if not hits else
                   f"{len(hits)} records" if len(hits) > 1 else
                   f"the record port {used.get(hits[0])} took")
            raise EmirError(
                "EMIR-E808", f"{inst}:{bnet} port {p['name']} placed at ({x:g}, {y:g}) "
                f"finds {why} within {tol:g} um (records: {shown}); check --place "
                f"{inst}={place[0]:g},{place[1]:g},{place[2]}", dspf=dspf, net=net)
        used[hits[0]] = p["name"]
        out[p["name"]] = hits[0]
    notice("EMIR-I802", f"{inst}:{bnet}: {len(ports)} ports matched to the top's records by "
           f"coordinates (--place {inst}={place[0]:g},{place[1]:g},{place[2]})", log)
    return out


def _lcm(ns) -> int:
    L = 1
    for n in ns:
        L = L * n // math.gcd(L, n)
    return L


def _sync_grid(taps, phases, warn=True, log=_quiet) -> tuple:
    """`(dt, L, waves)`: every tap's stored period on one common grid of `L`
    samples of `dt`, each shifted by its phase reference plus its phase."""
    dt = min(t["_dt"] for t in taps)
    ns = {}
    for t in taps:
        per = t["_per"]
        n = int(round(per / dt))
        if n < 1 or abs(n * dt - per) > 1e-6 * per:
            raise EmirError(
                "EMIR-E810", f"{t['name']}: its period {per:.6g} s is not a whole number of "
                f"the common step {dt:.6g} s, so sync cannot put the blocks on one grid")
        ns[t["name"]] = n
    L = _lcm(set(ns.values()))
    if L > MAX_COMMON_SAMPLES:
        raise EmirError(
            "EMIR-E810", f"the common period of {', '.join(sorted({t['inst'] for t in taps}))} "
            f"is {L} steps of {dt:.6g} s ({L * dt:.6g} s), more than {MAX_COMMON_SAMPLES} "
            f"samples: the periods are nearly incommensurate")
    out, keep = [], 1.0
    for t in taps:
        n = ns[t["name"]]
        w = _periodic_resample(t["_wave"], n)
        pk0 = float(np.abs(t["_wave"]).max())
        if pk0 > 0:
            keep = min(keep, float(np.abs(w).max()) / pk0)
        sh = int(round((t["_tref"] + phases.get(t["inst"], 0.0)) / dt)) % n
        out.append(_tile(w, L, sh))
    if warn and keep < PEAK_KEEP:
        notice("EMIR-W803", f"putting the blocks' periods on the common {dt:.4g} s step kept "
               f"{keep:.1%} of the peak pin current", log)
    return dt, L, np.array(out)




def _transfer(nw, taps, gnd, pins, branches=True, max_bytes=2 << 30, steps=None) -> tuple:
    """`(T, Z)` for a unit current into the net at each tap: the branch
    currents `T` (branches x taps; None unless `branches`) and the potentials
    `Z` at the nodes of the taps `pins` (len(pins) x taps). One
    back-substitution per tap; `T` is refused above `max_bytes` (EMIR-E418),
    as the stock superposition refuses its transfer matrix."""
    need = len(nw.res) * len(taps) * 8 if branches else 0
    if need > max_bytes:
        raise EmirError("EMIR-E418", f"the transfer matrix would need {need / 2**30:.1f} GiB "
                        f"({len(nw.res)} branches x {len(taps)} taps): use --align inphase or "
                        f"sync without --phase-sweep, or fewer top-level devices",
                        next_steps=steps)
    T = np.empty((len(nw.res), len(taps))) if branches else None
    Z = np.empty((len(pins), len(taps)))
    rows = [nw.idx[taps[i]["node"]] for i in pins]
    for j, t in enumerate(taps):
        V, Ib = nw.solve({t["node"]: 1.0}, gnd)
        if branches:
            T[:, j] = Ib
        Z[:, j] = V[rows]
    return T, Z


class _Combiner:
    """How the blocks' pin currents combine, per --align, through a linear
    map `A` (rows x taps): branch currents, pin potentials, or the potential
    between two pins of one block. `rows(A)` -> `{avg, rms, peak, wave}`.

    * inphase: avg exact; rms and peak the bounds `|A| I_rms`, `|A| I_peak`.
    * sync: exact on the common grid; with --phase-sweep, rms and peak are
      the worst over every phase combination (avg does not move).
    * async: per block on its own grid; avg exact, rms the long-time value of
      unrelated blocks, peak the worst over every relative phase.
    """

    def __init__(self, taps, groups, opts, log=_quiet):
        self.taps, self.groups, self.align, self.log = taps, groups, opts.align, log
        self.combos = [dict(opts.phase)]
        self.swept = sorted(opts.sweep)
        self.worst_combo = None
        if self.align == "sync":
            if self.swept:
                per = {i: max(t["_per"] for t in taps if t["inst"] == i) for i in self.swept}
                grids = [[k * per[i] / opts.sweep[i] for k in range(opts.sweep[i])]
                         for i in self.swept]
                self.combos = [dict(opts.phase) | dict(zip(self.swept, c))
                               for c in itertools.product(*grids)]
            self.dt, self.L, self.W0 = _sync_grid(taps, self.combos[0], log=log)
            W = self.W0
            self.i_avg = W.mean(axis=1)
            self.i_rms = np.sqrt((W * W).mean(axis=1))
            self.i_peak = np.abs(W).max(axis=1)
        elif self.align == "async":
            self.gW = {inst: _sync_grid([taps[i] for i in ix], {}, log=log)[2]
                       for inst, ix in groups.items()}
            ws = [t["_wave"] for t in taps]
            self.i_avg = np.array([w.mean() for w in ws])
            self.i_rms = np.array([np.sqrt((w * w).mean()) for w in ws])
            self.i_peak = np.array([np.abs(w).max() for w in ws])
        else:
            self.i_avg = np.array([t["i_avg"] for t in taps])
            self.i_rms = np.array([t["i_rms"] for t in taps])
            self.i_peak = np.array([t["i_peak"] for t in taps])

    @property
    def single(self) -> bool:
        """One exact alignment (sync without a sweep): waveforms exist."""
        return self.align == "sync" and len(self.combos) == 1

    def rows(self, A, want_wave=False, track=False) -> dict:
        A = np.atleast_2d(A)
        if self.align == "inphase":
            return {"avg": A @ self.i_avg, "rms": np.abs(A) @ self.i_rms,
                    "peak": np.abs(A) @ self.i_peak, "wave": None}
        if self.align == "async":
            n = A.shape[0]
            avg, var, hi, lo = np.zeros(n), np.zeros(n), np.zeros(n), np.zeros(n)
            for inst, ix in self.groups.items():
                m, r, h, low = _stats(A[:, ix], self.gW[inst])
                avg += m
                var += r * r - m * m
                hi += h
                lo += low
            return {"avg": avg, "rms": np.sqrt(np.maximum(avg * avg + var, 0.0)),
                    "peak": np.maximum(hi, -lo), "wave": None}
        out, best = None, -1.0
        for k, c in enumerate(self.combos):
            W = self.W0 if k == 0 else _sync_grid(self.taps, c, warn=False)[2]
            m, r, h, low = _stats(A, W)
            p = np.maximum(h, -low)
            if track and p.size and float(np.nanmax(p)) > best:
                best, self.worst_combo = float(np.nanmax(p)), {i: c[i] for i in self.swept}
            if out is None:
                out = {"avg": m, "rms": r, "peak": p,
                       "wave": A @ W if (want_wave and self.single) else None}
            else:
                np.maximum(out["rms"], r, out=out["rms"])
                np.maximum(out["peak"], p, out=out["peak"])
        return out


def _block_taps(d, net, insts, opts, dpath=None, log=_quiet) -> tuple:
    """The abstract ports of the blocks black-boxed on `net`:
    `(taps, sets, stamps, records)`. A tap sits on the node of the record its
    port matched (`node`, kept in `_orig`); `sets` groups the taps of one
    block net (`ix`); `stamps` are its port conductances, between those nodes."""
    by = {}
    for t in d.net_taps(net):
        by.setdefault((t["inst"], t["pin"]), []).append(t)
    taps, sets, stamps = [], [], []
    for inst, ab in insts.items():
        for bnet, nb in ab["nets"].items():
            recs = by.get((inst, bnet))
            if not recs:
                continue
            m = match_ports(inst, bnet, nb["ports"], recs, opts.place.get(inst), opts.tol,
                            dspf=dpath, net=net, log=log)
            per = nb.get("period_s") or nb["n_samples"] * nb["dt_s"]
            ix = []
            for p in nb["ports"]:
                node = m[p["name"]]
                taps.append({"name": f"{inst}:{p['name']}", "node": node, "_orig": node,
                             "xy": d.node_xy[node], "layer": None, "inst": inst,
                             "pin": bnet, "port": p["name"],
                             "i_avg": float(p["i_avg"]), "i_rms": float(p["i_rms"]),
                             "i_peak": float(p["i_peak"]),
                             "_wave": np.asarray(p["wave"], dtype=float), "_per": float(per),
                             "_dt": float(nb["dt_s"]), "_tref": float(nb["t_ref_s"])})
                ix.append(len(taps) - 1)
            sets.append({"inst": inst, "bnet": bnet, "ix": ix, "abstract": nb,
                         "path": ab.get("_path")})
            if nb.get("aperiodic") and opts.align == "sync":
                notice("EMIR-W801", f"{inst}:{bnet}: the abstract has no period; sync "
                       f"repeats its {per:.4g} s window as if it were one", log)
            if not nb.get("y_ports"):
                continue
            Y = np.asarray(nb["y_ports"], dtype=float)
            names = [p["name"] for p in nb["ports"]]
            scale = float(np.abs(np.diag(Y)).max()) or 1.0
            for i in range(len(names)):
                for j in range(i + 1, len(names)):
                    g = -float(Y[i, j])
                    if g > 1e-12 * scale:
                        stamps.append({"name": f"Y:{inst}:{names[i]}:{names[j]}",
                                       "inst": inst, "bnet": bnet,
                                       "between": [names[i], names[j]],
                                       "nodes": [m[names[i]], m[names[j]]], "g": g})
    for (inst, pin), recs in sorted(by.items()):
        if inst in insts and pin not in insts[inst]["nets"]:
            notice("EMIR-W806", f"net {net}: {inst}:{pin} ({len(recs)} record(s)) is not in "
                   f"{inst}'s abstract (nets {', '.join(insts[inst]['nets'])}): taken as zero "
                   f"current", log)
    return taps, sets, stamps, by


def _device_taps(d, net, w, insts, analysis=None, period=None, dt=None, orig=None,
                 log=_quiet) -> tuple:
    """`(taps, ingest)`: the instance pins of `net` outside the abstracted
    instances, from the CC simulation `w`, each holding one period of its
    current on a uniform grid (of step `dt`, rounded to divide the period),
    ready for the common grid of sync alignment. `([], None)` when the net
    has no such pin. `orig` maps `inst:pin` to the node before any merge."""
    from . import ccsim
    dd = copy.copy(d)
    dd.inst_pins = {k: v for k, v in d.inst_pins.items() if v["inst"] not in insts}
    dd.reindex()
    if not dd.net_taps(net):
        return [], None
    if w.is_complex:
        raise EmirError("EMIR-E805", f"{w.name or 'the simulation'} holds harmonics (PSS fd); "
                        f"the devices next to the abstracts need the transient or PSS "
                        f"time-domain output")
    ing = ccsim.cc_taps(dd, net, w, analysis=analysis, period=period, feed=None,
                        waveforms=True, log=log)
    tt = np.asarray(ing["time"], dtype=float)
    per = (ing["window"] or {}).get("period")
    span = float(per) if per else float(tt[-1] - tt[0])
    t_ref = float(tt[-1] - span)
    n = max(16, int(round(span / dt))) if dt else DEFAULT_POINTS
    tu = t_ref + np.arange(n) * (span / n)
    taps, keep = [], 1.0
    for t in ing["taps"]:
        wu = np.interp(tu, tt, t["wave"])
        pk = float(np.abs(t["wave"]).max())
        if pk > 0:
            keep = min(keep, float(np.abs(wu).max()) / pk)
        o = (orig or {}).get(f"{t['inst']}:{t['pin']}", t["node"])
        taps.append({"name": t["name"], "node": t["node"], "_orig": o, "xy": t["xy"],
                     "layer": t.get("layer"), "inst": DEVICES, "pin": t["pin"],
                     "port": t["name"], "device": t["inst"],
                     "i_avg": float(t["i_avg"]), "i_rms": float(t["i_rms"]),
                     "i_peak": float(t["i_peak"]), "_wave": wu, "_per": span,
                     "_dt": span / n, "_tref": t_ref})
    if keep < PEAK_KEEP:
        notice("EMIR-W803", f"net {net}: the {n}-point period of the simulation kept "
               f"{keep:.1%} of a device's peak current", log)
    return taps, ing


def top_nets(d, insts: dict, net=None) -> list:
    """The top nets a hierarchical run analyses: `net`, or every net with
    resistors on which an abstracted instance has a pin its abstract covers."""
    if net not in (None, "all"):
        return [net]
    out = []
    for n in sorted(d.nets):
        if not d.net_resistors(n):
            continue
        if any(t["inst"] in insts and t["pin"] in insts[t["inst"]]["nets"]
               for t in d.net_taps(n)):
            out.append(n)
    return out


def top_net(d0, net, proc, insts, opts, a, w=None, log=print) -> tuple:
    """One top-level net with the blocks black-boxed: `(result, info)`.

    `w` is the top's own CC simulation (--cc-sim): the instance pins outside
    the abstracted blocks take their currents from it. `result` has the
    shape `dspf.check_net` returns (the summary, HTML and viewer read it);
    `info` holds the hierarchy's own numbers."""
    from .dspf import Network, check_net, em_clusters, map_layers, net_scaling
    from .rcc import reference_node
    dpath = getattr(a, "dspf", None)
    feed_opt = getattr(a, "feed", None) if getattr(a, "_single", True) else None
    if not d0.has_net(net):
        from .dspf import did_you_mean
        raise EmirError("EMIR-E204", f"net {net!r} is not in {dpath}"
                        f"{did_you_mean(net, d0.nets)}", dspf=dpath)
    ports = ports_of(d0, net)
    pads = 1
    if feed_opt:
        feed = reference_node(d0, net, feed_opt)
        d = _copy(d0)
    elif not ports:
        raise EmirError("EMIR-E813", f"top net {net!r} has no *|P port: give --feed NODE, the "
                        f"node the net is fed at", dspf=dpath, net=net)
    elif len(ports) > 1:
        d, feed = with_short(d0, net, ports)
        pads = len(ports)
        notice("EMIR-I801", f"net {net}: {len(ports)} ports ({', '.join(ports[:4])}"
               f"{' ...' if len(ports) > 4 else ''}) held at one ideal potential, the feed",
               log)
    else:
        d, feed = _copy(d0), ports[0]
    # ---- the block pins of this net, and the port conductances between them
    rscale = net_scaling(d, proc, map_layers(d, proc))[1]
    btaps, sets, stamps, by = _block_taps(d, net, insts, opts, dpath, log=log)
    for st in stamps:
        # Y is at the block's EM temperature; a virtual resistor has no
        # layer, so the network leaves it as it is
        d.res.append(_virtual_res(st["name"], *st["nodes"], 1.0 / st["g"]))
    d.reindex()
    if not btaps:
        raise EmirError("EMIR-E809", f"no --abstract instance has a pin on net {net!r}",
                        dspf=dpath, net=net)
    dev, ing = [], None
    if w is not None:
        # the top's own devices, from its CC simulation (--cc-sim)
        orig = {f"{t['inst']}:{t['pin']}": t["node"] for t in d0.net_taps(net)}
        dev, ing = _device_taps(d, net, w, insts, analysis=getattr(a, "analysis", None),
                                period=getattr(a, "period", None),
                                dt=min(t["_dt"] for t in btaps), orig=orig, log=log)
        if dev and opts.align == "sync" and not (ing["window"] or {}).get("period"):
            notice("EMIR-W801", f"net {net}: the top's simulation has no period (give "
                   f"--period); sync repeats its {dev[0]['_per']:.4g} s window as if it were "
                   f"one", log)
    else:
        other = sorted({f"{i}:{p}" for (i, p) in by if i not in insts})
        if other:
            notice("EMIR-W807", f"net {net}: {len(other)} instance pin(s) belong to no "
                   f"--abstract instance and carry no current in this run (give the top's CC "
                   f"simulation with --cc-sim): {', '.join(other[:6])}"
                   f"{' ...' if len(other) > 6 else ''}", log)
    taps = btaps + dev
    groups = {}
    for j, t in enumerate(taps):
        groups.setdefault(t["inst"], []).append(j)
    # ---- solve
    nw = Network(d, net, rscale=rscale)
    if opts.align == "async":
        notice("EMIR-W802", f"net {net}: --align async treats the blocks' clocks as "
               f"unrelated; for related clocks it is NOT an upper bound (use inphase, or "
               f"sync with --phase-sweep)", log)
    comb = _Combiner(taps, groups, opts, log=log)
    tl = [{k: v for k, v in t.items() if not k.startswith("_")}
          | {"i_avg": float(comb.i_avg[j]), "i_rms": float(comb.i_rms[j]),
             "i_peak": float(comb.i_peak[j])} for j, t in enumerate(taps)]
    argv = getattr(a, "argv_text", None)
    # the run's memory budget (--max-memory, the big-block option) bounds the
    # dense matrices here too; the exact superposition streams above it
    from .bigblock import memory_budget
    budget = memory_budget(getattr(a, "max_memory", None) or (2 << 30), log)
    T, Z = _transfer(nw, taps, feed, range(len(btaps)),
                     branches=not (comb.single or opts.align == "inphase"),
                     max_bytes=budget,
                     steps=[step("the in-phase bound needs no transfer matrix",
                                 f"emir run {argv} --align inphase")] if argv else [])
    if comb.single:
        for j, t in enumerate(tl):
            t["wave"] = comb.W0[j]
        res = check_net(d, net, proc, _inj(tl), feed, taps_list=tl,
                        tap_weights=np.full(comb.L, 1.0 / comb.L), network=nw, log=log,
                        max_bytes=budget,
                        superposition=getattr(a, "superposition", None) or "auto")
    elif opts.align == "inphase":
        res = check_net(d, net, proc, _inj(tl), feed, taps_list=tl, network=nw, log=log)
    else:
        # async, or sync over a phase sweep: the branch metrics are combined
        # here, then judged by the one verdict function
        res = check_net(d, net, proc, _inj(tl), feed, network=nw, log=log)
        b = comb.rows(T, track=True)
        Ie = {"avg": np.abs(b["avg"]), "rms": b["rms"], "peak": b["peak"]}
        notes = {}
        viol, mr = em_clusters(nw, Ie, proc, res["layer_map"], notes=notes)
        res.update({"clusters": viol, "worst": viol[0] if viol else None, "max_ratio": mr,
                    "Ie": Ie, "via_cuts": notes["via_cuts"], "no_width": notes["no_width"]})
    res["metric_note"] = _note(comb, opts)
    # which path ran: the exact waveform superposition (sync, one phase: dense
    # or stream, from check_net), the in-phase scalars (inphase), or the
    # per-block rows combined here
    if opts.align == "inphase":
        res["superposition"] = {**(res.get("superposition") or {}), "method": "scalar",
                                "reason": "--align inphase: " + _note(comb, opts)}
    elif not comb.single:
        res["superposition"] = {
            "method": "combined", "reason": f"--align {opts.align}: " + _note(comb, opts),
            "taps": len(taps), "points": getattr(comb, "L", None), "branches": len(nw.res),
            "nodes": len(nw.nodes), "dense_bytes": len(nw.res) * len(taps) * 8,
            "budget_bytes": int(budget), "chunk": None, "chunks": None}
    # ---- the voltage at every block pin, and between the pins of a block net
    pv = comb.rows(Z, want_wave=bool(opts.pv_out))
    pv["avg"] = np.array([res["V"][nw.idx[t["node"]]] for t in btaps])
    for s in sets:
        Zd = Z[s["ix"]] - Z[s["ix"][0]]
        s["diff"] = comb.rows(Zd, want_wave=False)
    pins = []
    for j, t in enumerate(btaps):
        pins.append({"instance": t["inst"], "block_net": t["pin"], "port": t["port"],
                     "node": t["node"], "x_um": float(t["xy"][0]), "y_um": float(t["xy"][1]),
                     "V_avg_mV": float(pv["avg"][j]) * 1e3,
                     "V_rms_mV": float(pv["rms"][j]) * 1e3,
                     "V_peak_mV": float(pv["peak"][j]) * 1e3,
                     "i_avg_A": float(comb.i_avg[j]), "i_rms_A": float(comb.i_rms[j]),
                     "i_peak_A": float(comb.i_peak[j])})
    names = [r["name"] for r in nw.res]
    through = []
    for s in stamps:
        e = names.index(s["name"])
        through.append({"instance": s["inst"], "block_net": s["bnet"], "between": s["between"],
                        "nodes": s["nodes"], "R_ohm": 1.0 / s["g"],
                        "I_avg_A": float(res["Ib"][e]), "I_rms_A": float(res["Ie"]["rms"][e]),
                        "I_peak_A": float(res["Ie"]["peak"][e])})
    res["mode"] = "cc-grid"
    res["mode_reason"] = (f"hierarchical: {len(sets)} block abstract(s) at the black-box "
                          f"pins" + (f" and {len(dev)} top-level instance pin(s) from the "
                                     f"simulation" if dev else "")
                          + f", align {opts.align}")
    res["unsaved_pins"] = list((ing or {}).get("missing") or [])
    t_common = ({"dt_s": comb.dt, "n_samples": comb.L, "period_s": comb.L * comb.dt}
                if opts.align == "sync" else None)
    sweep = None
    if comb.swept:
        sweep = {"instances": comb.swept, "steps": {i: opts.sweep[i] for i in comb.swept},
                 "combinations": len(comb.combos), "worst_peak_phase_s": comb.worst_combo}
    info = {"net": net, "feed": feed, "pads": pads, "align": opts.align, "pins": pins,
            "pass_through": through, "sweep": sweep, "t_common": t_common,
            "devices": len(dev), "_sets": sets, "_taps": btaps, "_pv": pv, "_comb": comb}
    return res, info


def _note(comb, opts) -> str:
    if opts.align == "inphase":
        return ("inphase: rms and peak are the in-phase upper bound over every alignment of "
                "the blocks (sum |T| I per pin); avg exact")
    if opts.align == "async":
        return ("async: avg exact, rms the long-time value of unrelated clocks, peak the "
                "worst over every relative phase (NOT an upper bound for related clocks)")
    s = (f"sync: exact for the blocks on one clock reference, over the common period "
         f"{comb.L * comb.dt:.4g} s ({comb.L} steps of {comb.dt:.4g} s)")
    if comb.swept:
        s += (f"; rms and peak the worst over {len(comb.combos)} phase combination(s) of "
              f"{', '.join(comb.swept)}")
    return s


def _inj(taps) -> dict:
    out = {}
    for t in taps:
        out[t["node"]] = out.get(t["node"], 0.0) + t["i_avg"]
    return out


# ===================================================================== budgets, port voltages


def budget_rows(infos: list, insts: dict, budget_mV: float) -> list:
    """Per block net: the top-level drop at its worst pin plus its internal
    drop, against the budget. Exact for a one-pin supply; for several pins an
    upper bound (an internal node's top-induced shift is a weighted mean of
    its pin voltages, so it never exceeds the worst pin's)."""
    rows = []
    for info in infos:
        for s in info["_sets"]:
            ix = s["ix"]
            pins = [info["pins"][j] for j in ix]
            top = max(abs(p["V_avg_mV"]) for p in pins)
            top_pk = max(abs(p["V_peak_mV"]) for p in pins)
            itn = s["abstract"]["internal"]
            internal = float(itn["ir_drop_mV"])
            tot = top + internal
            dyn = itn.get("dynamic") or {}
            rows.append({"instance": s["inst"], "block_net": s["bnet"], "top_net": info["net"],
                         "n_pins": len(ix), "top_mV": top, "internal_mV": internal,
                         "total_mV": tot, "budget_mV": float(budget_mV),
                         "verdict": "PASS" if tot <= budget_mV else "FAIL",
                         "exact": len(ix) == 1, "top_peak_mV": top_pk,
                         "internal_dynamic_mV": dyn.get("worst_mV")})
    return rows


def port_voltages_doc(infos: list, insts: dict, opts, a) -> dict:
    """The `emir.port_voltages/1` document: the voltage at every block pin,
    and between the pins of each block net, for back-annotation."""
    from .output import document
    out = {}
    for info in infos:
        pv = info["_pv"]
        for s in info["_sets"]:
            nb, inst = s["abstract"], s["inst"]
            dv = s["diff"]
            ports, wv = [], pv.get("wave")
            for k, j in enumerate(s["ix"]):
                t = info["_taps"][j]
                ports.append({"name": t["port"], "node": t["node"],
                              "x_um": float(t["xy"][0]), "y_um": float(t["xy"][1]),
                              "V_avg_V": float(pv["avg"][j]), "V_rms_V": float(pv["rms"][j]),
                              "V_peak_V": float(pv["peak"][j]),
                              "dV_avg_V": float(dv["avg"][k]), "dV_rms_V": float(dv["rms"][k]),
                              "dV_peak_V": float(dv["peak"][k]),
                              "wave_V": ([float(v) for v in wv[j]] if wv is not None else None)})
            drops = [abs(p["V_avg_V"]) for p in ports]
            cur = [abs(info["pins"][j]["i_avg_A"]) for j in s["ix"]]
            k_min = int(np.argmin(drops))
            vnom = nb.get("v_nominal")
            ent = {"top_net": info["net"], "align": opts.align, "exact": wv is not None,
                   "phase_s": float(opts.phase.get(inst, 0.0)),
                   "t_ref_s": float(nb["t_ref_s"]),
                   "period_s": float(nb.get("period_s") or nb["n_samples"] * nb["dt_s"]),
                   "n_samples": int(nb["n_samples"]), "dt_s": float(nb["dt_s"]),
                   "t_common": info["t_common"] if wv is not None else None,
                   "ref_port": ports[0]["name"], "ports": ports,
                   "droop_mV": {"min": min(drops) * 1e3, "max": max(drops) * 1e3,
                                "current_weighted": (float(np.dot(cur, drops) / sum(cur)) * 1e3
                                                     if sum(cur) > 0 else None)},
                   "v_nominal": vnom,
                   # the supply to re-simulate the block at (least-drooped pin:
                   # its currents stay conservative)
                   "v_delivered_V": (vnom + ports[k_min]["V_avg_V"]) if vnom is not None
                   else None}
            e = out.setdefault(inst, {"abstract": s.get("path"), "block": None, "nets": {}})
            e["block"] = insts[inst].get("block")
            e["nets"][s["bnet"]] = ent
    return document("port_voltages", created=datetime.datetime.now(
        datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        top_dspf=os.path.abspath(a.dspf), process=os.path.abspath(a.process),
        align=opts.align,
        # the run's --budget-mv, so the back-annotation can judge its own drop
        budget_mV=float(opts.budget) if getattr(opts, "budget", None) else None,
        instances=out)


# ===================================================================== 3. back-annotation


def _budget_check(budget_mV, drop_mV) -> dict:
    """`budget_mV` and `budget_verdict` of a back-annotation, when the top run
    that wrote the port voltages had --budget-mv (the drop is from the pad)."""
    if not budget_mV:
        return {}
    return {"budget_mV": float(budget_mV),
            "budget_verdict": "PASS" if drop_mV <= float(budget_mV) else "FAIL"}


def back_annotate(ctx, entry, pv, pv_arg, log=_quiet, detail=None) -> dict | None:
    """Re-check a block net with its top-level pin voltages (None when the
    file has no entry for this net).

    The block current is the sum of two solutions of its own grid: the
    devices with every pin at one potential (the abstract), and the pin
    voltages with no devices (the pass-through a supply fed from several
    sides carries). With the top's pin waveforms (a sync run) the result is
    exact; otherwise avg is exact and rms and peak are upper bounds (the
    pass-through bounded from the top's pin-voltage differences). Pass a dict
    as `detail` to get the per-branch currents and node potentials too.
    """
    from .dspf import Network, _verdict, em_clusters, map_layers
    from scipy.sparse.linalg import splu
    path, inst = pv_arg
    ne = (pv["instances"][inst].get("nets") or {}).get(ctx["net"])
    if ne is None:
        return None
    names = [p["name"] for p in entry["ports"]]
    got = [p.get("name") for p in ne.get("ports") or []]
    if got != names:
        raise EmirError("EMIR-E812", f"{path}: {inst}:{ctx['net']} has ports {got}, the "
                        f"block has {names}", path=path)
    d, net, ports, res, proc = ctx["d"], ctx["net"], ctx["ports"], ctx["res"], ctx["proc"]
    for p in ne["ports"]:
        if not all(_num(p.get(k)) for k in ("V_avg_V", "dV_avg_V", "dV_rms_V", "dV_peak_V")):
            raise EmirError("EMIR-E812", f"{path}: {inst}:{net} port {p['name']} lacks its "
                            f"voltages", path=path)
    tc = ne.get("t_common")
    exact = bool(ne.get("exact") and tc and all(isinstance(p.get("wave_V"), list)
                                               for p in ne["ports"]))
    if exact:
        same = (abs(float(ne["t_ref_s"]) - ctx["t_ref"]) <= 1e-9 * ctx["per"]
                and abs(float(ne["period_s"]) - ctx["per"]) <= 1e-9 * ctx["per"]
                and int(ne["n_samples"]) == ctx["n"])
        if not same:
            raise EmirError("EMIR-E812", f"{path}: {inst}:{net} was written from another "
                            f"result of this block (period {ne['period_s']:.6g} s, phase "
                            f"reference {ne['t_ref_s']:.6g} s, {ne['n_samples']} samples; "
                            f"this run: {ctx['per']:.6g} s, {ctx['t_ref']:.6g} s, "
                            f"{ctx['n']}): re-run the top with this abstract", path=path)
    nw0 = Network(d, net, rscale=res["rscale"])
    if [r["name"] for r in nw0.res] != [r["name"] for r in res["network"].res]:
        raise EmirError("EMIR-E905", f"{net}: the shorted and the original network list "
                        f"their resistors differently")
    G = _laplacian(nw0)
    P = [nw0.idx[p] for p in ports]
    mask = _reached(nw0, ports)
    mask[P] = False
    inner = np.flatnonzero(mask)
    pos = np.full(len(nw0.nodes), -1)
    pos[inner] = np.arange(len(inner))
    lu = splu(G[inner][:, inner].tocsc()) if len(inner) else None
    Gip = G[inner][:, P]
    pset = set(ports)
    orig = {f"{t['inst']}:{t['pin']}": t["node"] for t in d.net_taps(net)}
    rows = []
    for t in ctx["taps"]:
        o = t.get("_orig") or orig.get(f"{t['inst']}:{t['pin']}", t["node"])
        rows.append(-1 if o in pset else int(pos[nw0.idx[o]]))
    Vp = np.array([p["V_avg_V"] for p in ne["ports"]])
    nn, nb = len(nw0.nodes), len(nw0.res)
    if exact:
        L, dt = int(tc["n_samples"]), float(tc["dt_s"])
        n_c = int(round(ctx["per"] / dt))
        sh = int(round((ctx["t_ref"] + float(ne.get("phase_s") or 0.0)) / dt)) % n_c
        if ctx.get("Wu") is not None:
            Wu = ctx["Wu"]
        else:
            tu = ctx["t_ref"] + np.arange(ctx["n"]) * (ctx["per"] / ctx["n"])
            Wu = [np.interp(tu, ctx["tt"], t["wave"]) for t in ctx["taps"]]
        Wc = np.array([_tile(_periodic_resample(wu, n_c), L, sh) for wu in Wu])
        VP = np.array([p["wave_V"] for p in ne["ports"]], dtype=float)
        if VP.shape != (len(ports), L):
            raise EmirError("EMIR-E812", f"{path}: {inst}:{net} pin waveforms are not "
                            f"{L} samples", path=path)
        s1, s2, pk = np.zeros(nb), np.zeros(nb), np.zeros(nb)
        vsum, vpk = np.zeros(nn), np.zeros(nn)
        chunk = max(1, int(CHUNK_BYTES / 8 / max(nn + nb, 1)))
        for c0 in range(0, L, chunk):
            sl = slice(c0, c0 + chunk)
            V = np.zeros((nn, Wc[:, sl].shape[1]))
            V[P] = VP[:, sl]
            if lu is not None:
                rhs = -(Gip @ VP[:, sl])
                for k, r in enumerate(rows):
                    if r >= 0:
                        rhs[r] += Wc[k, sl]
                V[inner] = lu.solve(rhs)
            Ib = nw0.g[:, None] * (V[nw0.a] - V[nw0.b])
            s1 += Ib.sum(axis=1)
            s2 += (Ib * Ib).sum(axis=1)
            np.maximum(pk, np.abs(Ib).max(axis=1), out=pk)
            vsum += V.sum(axis=1)
            np.maximum(vpk, np.abs(V).max(axis=1), out=vpk)
        Ie = {"avg": np.abs(s1 / L), "rms": np.sqrt(s2 / L), "peak": pk}
        v_avg = vsum / L
        peak_mV = float(vpk.max()) * 1e3
        note = ("exact: the block's device currents and the top's pin waveforms on the "
                "common grid of the sync run")
    else:
        # devices with the pins shorted (the abstract's own solve) ...
        a_sc = np.asarray(res["Ib"], dtype=float)
        r_sc, p_sc = res["Ie"]["rms"], res["Ie"]["peak"]
        # ... plus the pass-through of one volt on each pin
        Tpt = np.zeros((nb, len(ports)))
        H = np.zeros((nn, len(ports)))
        for k in range(len(ports)):
            V = np.zeros(nn)
            V[P[k]] = 1.0
            if lu is not None:
                V[inner] = lu.solve(-np.asarray(Gip[:, k].todense()).ravel())
            Tpt[:, k] = nw0.g * (V[nw0.a] - V[nw0.b])
            H[:, k] = V
        dr = np.array([p["dV_rms_V"] for p in ne["ports"]])
        dp = np.array([p["dV_peak_V"] for p in ne["ports"]])
        Ie = {"avg": np.abs(a_sc + Tpt @ Vp),
              "rms": r_sc + np.abs(Tpt[:, 1:]) @ dr[1:],
              "peak": p_sc + np.abs(Tpt[:, 1:]) @ dp[1:]}
        vs = np.zeros(nn)
        nwm = res["network"]
        for i, nd in enumerate(nw0.nodes):
            if nd not in pset:
                vs[i] = np.nan_to_num(res["V"][nwm.idx[nd]])
        v_avg = vs + H @ Vp
        peak_mV = None
        note = ("exact: a one-pin supply carries no pass-through" if len(ports) == 1 else
                "avg exact; rms and peak upper bounds: the block's own currents plus the "
                f"pass-through bounded from the top's pin-voltage differences "
                f"(align {ne.get('align')})")
        exact = len(ports) == 1
    if len(ports) == 1:
        notice("EMIR-I804", f"{inst}:{net}: one pin, no pass-through; the back-annotated "
               f"droop adds the top-level drop at the pin", log)
    notes = {}
    viol, mr = em_clusters(nw0, Ie, proc, map_layers(d, proc), notes=notes)
    verdict = _verdict({"clusters": viol, "no_width": notes["no_width"]}, proc)
    if detail is not None:
        # per branch and per node, for a caller that compares them
        detail.update(names=[r["name"] for r in nw0.res], Ie=Ie, nodes=list(nw0.nodes),
                      V_avg=v_avg)
    Y = np.asarray(entry["y_ports"], dtype=float) if entry.get("y_ports") else None
    kmax = int(np.argmax(np.abs(v_avg)))
    return {"source": os.path.abspath(path), "instance": inst, "top_net": ne.get("top_net"),
            "align": ne.get("align"), "exact": bool(exact),
            "boundary": f"the top-level pin voltages of {inst} ({ne.get('align')})",
            "verdict": verdict, "max_ratio": {k: float(v) for k, v in mr.items()},
            "worst": _worst({"worst": viol[0] if viol else None}),
            "n_violations": len(viol), "worst_items": _item_dicts(viol, net),
            "metric_note": note,
            # measured from the top's reference (the pad): the top-level drop to
            # the pins is already in it -- compare it with the budget as it is
            "ir_reference": "pad",
            "ir_drop_mV": float(abs(v_avg[kmax])) * 1e3, "ir_worst_node": nw0.nodes[kmax],
            "ir_drop_peak_mV": peak_mV,
            **_budget_check(pv.get("budget_mV"), float(abs(v_avg[kmax])) * 1e3),
            "pin_V_avg_mV": {p["name"]: float(p["V_avg_V"]) * 1e3 for p in ne["ports"]},
            "pass_through_A": ({n: float(v) for n, v in zip(names, Y @ Vp)}
                               if Y is not None else {n: 0.0 for n in names}),
            "short_circuit_max_ratio": entry["internal"]["max_ratio"]}


# ===================================================================== the run and check hooks


def analyse_top(d, proc, a, res, log=print):
    """`emir run TOP.dspf --abstract ...`: every top net with block pins,
    the budgets, the port voltages; fills the `RunResult` `res`."""
    from .dspf import _maybe_html, _print_net
    from .hier_cli import Options
    from .output import write_json
    opts = Options(a)
    bad = opts.incompatible(a)
    if bad:
        raise EmirError("EMIR-E811", f"--abstract drives the run from block abstracts; it "
                        f"does not combine with {', '.join(bad)}", dspf=a.dspf)
    insts = {}
    for inst, path in opts.abstracts.items():
        ab = load_abstract(path)
        ab["_path"] = os.path.abspath(path)
        insts[inst] = ab
        if _num(ab.get("temp_C")) and abs(float(ab["temp_C"]) - proc.temp) > 0.5:
            notice("EMIR-W808", f"{inst}: {path} was characterised at {ab['temp_C']:g} C, "
                   f"this run analyses at {proc.temp:g} C", log)
    have = {t["inst"] for t in d.inst_pins.values()}
    for inst in insts:
        if inst not in have:
            raise EmirError("EMIR-E802", f"--abstract {opts.abstracts[inst]}@{inst}: no *|I "
                            f"record of instance {inst!r} in {a.dspf}", dspf=a.dspf,
                            net=None if a.net in (None, "all") else a.net)
    nets = top_nets(d, insts, a.net)
    if not nets:
        raise EmirError("EMIR-E809", f"no top net of {a.dspf} has a pin an abstract covers",
                        dspf=a.dspf)
    landed = set()
    infos = []
    multi = len(nets) > 1
    a._single = not multi                  # --feed names one net's node, as in a stock run
    w = None
    if getattr(a, "cc_sim", None):
        from . import waves
        from .dspf import checked_waves
        w = checked_waves(res, a) or waves.read(a.cc_sim, analysis=getattr(a, "analysis", None))
        res.waves = w
    for net in nets:
        if multi:
            print(f"\n=== {net} ===")
        r, info = top_net(d, net, proc, insts, opts, a, w=w, log=log)
        res.solved[net] = r
        infos.append(info)
        landed |= {(s["inst"], s["bnet"]) for s in info["_sets"]}
        _print_net(r, proc, a, d, net=net)
        _print_pins(info)
        _maybe_html(r, d, a, proc, net=net, quiet=multi)
    for inst, ab in insts.items():
        for bnet in ab["nets"]:
            if (inst, bnet) not in landed:
                notice("EMIR-W806", f"{inst}:{bnet} lands on no analysed top net: its "
                       f"current is in no top-level solve of this run", log)
    rows = budget_rows(infos, insts, opts.budget) if opts.budget is not None else []
    res.budget_failed = any(r["verdict"] == "FAIL" for r in rows)
    for info in infos:
        for s in info["_sets"]:
            vn = s["abstract"].get("v_nominal")
            if vn:
                worst = max(abs(info["pins"][j]["V_avg_mV"]) for j in s["ix"]) * 1e-3
                if worst > DROOP_FRACTION * abs(vn):
                    notice("EMIR-W805", f"{s['inst']}:{s['bnet']}: {worst * 1e3:.4g} mV "
                           f"top-level droop at a pin is {worst / abs(vn):.1%} of its "
                           f"{vn:g} V supply: re-simulate the block at the delivered "
                           f"voltage and re-run (the first pass over-reads its current)", log)
    if rows:
        print(f"\nIR budget per block ({opts.budget:g} mV: top drop at the worst pin + "
              f"internal drop):")
        for r in rows:
            print(f"  {r['instance']} {r['block_net']}: {r['top_mV']:.4f} + "
                  f"{r['internal_mV']:.4f} = {r['total_mV']:.4f} mV  {r['verdict']}"
                  f"  ({'exact, one pin' if r['exact'] else 'upper bound, several pins'})")
        notice("EMIR-I803", "IR budget: exact for a one-pin supply, an upper bound for "
               "several pins", None)
    pv_path = None
    if opts.pv_out:
        pv_path = os.path.abspath(opts.pv_out)
        write_json(opts.pv_out, port_voltages_doc(infos, insts, opts, a))
        print(f"  port voltages  : {pv_path}")
    res.hierarchy = hierarchy_section(infos, insts, opts, rows, pv_path)
    res.extra_steps = hierarchy_steps(infos, insts, opts, a, pv_path)
    if a.html:
        # the run's EM/IR chart, as a flat run draws it: the top's own
        # elements (the black-boxed blocks are their abstracts' pins); it
        # prints the index of a multi-net run
        from .dspf import _charts
        _charts(res, a)
    if a.viewer:
        from .dspf import power_net_list
        from .layoutview import export
        res.viewer = export(res.solved, d, proc, a.viewer, lib=a.lib, cell=a.cell,
                            rails=power_net_list(a))
    return res


def _print_pins(info):
    for p in info["pins"]:
        print(f"  block pin      : {p['instance']}:{p['port']:<10s} at {p['node']}: V avg "
              f"{p['V_avg_mV']:+.4f} mV, peak {p['V_peak_mV']:.4f} mV; current "
              f"{p['i_avg_A'] * 1e3:+.4f} mA avg into the net")
    for t in info["pass_through"]:
        print(f"  pass-through   : {t['instance']} {t['block_net']} {t['between'][0]} -> "
              f"{t['between'][1]}: {t['I_avg_A'] * 1e3:+.5f} mA avg through "
              f"{t['R_ohm']:.4g} ohm of block grid")


def hierarchy_section(infos, insts, opts, rows, pv_path) -> dict:
    """The run summary's `hierarchy` field."""
    blocks = []
    for inst, ab in insts.items():
        for bnet, nb in ab["nets"].items():
            ba = nb.get("back_annotation")
            blocks.append({"instance": inst, "block": ab.get("block"), "block_net": bnet,
                           "abstract": ab.get("_path"), "n_pins": len(nb["ports"]),
                           "verdict": nb.get("verdict") or nb["internal"]["verdict"],
                           "internal_verdict": nb["internal"]["verdict"],
                           "boundary": (ba or {}).get("boundary") or nb["internal"]["boundary"],
                           "back_annotated": ba is not None,
                           "needs_back_annotation": len(nb["ports"]) > 1 and ba is None,
                           "internal_ir_mV": nb["internal"]["ir_drop_mV"]})
    nets = [{k: v for k, v in i.items() if not k.startswith("_")} for i in infos]
    notes = ["Two verdicts per level: the top-level grid above (block pins as current "
             "sources, block-to-block conductance stamped), and each block's own EM in its "
             "abstract (pins held at one potential; re-checked with the top's pin voltages "
             "for a supply with several pins)."]
    if opts.align == "async":
        notes.append("async alignment is NOT an upper bound for blocks on related clocks.")
    elif opts.align == "inphase":
        notes.append("inphase alignment: rms and peak are upper bounds over every alignment.")
    return {"align": opts.align, "phases": dict(opts.phase), "phase_sweep": dict(opts.sweep),
            "place": {k: list(v) for k, v in opts.place.items()},
            "blocks": blocks, "nets": nets,
            "budget": ({"budget_mV": float(opts.budget),
                        "verdict": "FAIL" if any(r["verdict"] == "FAIL" for r in rows)
                        else "PASS", "rows": rows} if opts.budget is not None else None),
            "port_voltages": pv_path, "notes": notes}


def hierarchy_steps(infos, insts, opts, a, pv_path) -> list:
    """Runnable next steps: write the pin voltages if a block needs them, then
    back-annotate every block net with several pins."""
    from .hier_cli import abstract_command
    from .output import command_line, strip_options
    fmt = ["--format", "json"] if getattr(a, "format", "text") == "json" else []
    need = []
    for info in infos:
        for s in info["_sets"]:
            if len(s["ix"]) > 1 and not s["abstract"].get("back_annotation"):
                need.append(s)
    if not need:
        return []
    out = []
    pv = pv_path
    if pv is None:
        pv = "emir_out/port_voltages.json"
        argv = strip_options(list(getattr(a, "argv", None) or []),
                             {"--port-voltages-out": 1, "--format": 1})
        out.append(step("write the top-level voltage at every block pin, for the "
                        "back-annotation of the blocks fed at several pins",
                        command_line("run", argv, "--port-voltages-out", pv, fmt)))
    seen = set()
    for s in need:
        ab = insts[s["inst"]]
        if s["inst"] in seen:
            continue
        seen.add(s["inst"])
        target = ab.get("_path") or "block.abstract.json"
        # into the output directory, never beside the person's own abstract
        outp = os.path.join("emir_out",
                            os.path.basename(os.path.splitext(target)[0]) + ".annotated.json")
        cmd = abstract_command(ab, ["--port-voltages", f"{pv}@{s['inst']}", "-o", outp], fmt)
        if cmd:
            out.append(step(f"re-check {s['inst']} with the top's pin voltages: a supply fed "
                            f"at several pins carries pass-through current the block-alone "
                            f"verdict does not include", cmd))
    return out


def hier_nets(d, a, default: list) -> list:
    """For `emir check` with --abstract and no --net: the nets the run will
    analyse (`top_nets`); when an abstract cannot be read (the check reports
    it), the nets with a record of an abstracted instance."""
    from .hier_cli import Options
    try:
        paths = Options(a, command="check").abstracts
    except EmirError:
        return default
    try:
        insts = {i: load_abstract(p) for i, p in paths.items()}
    except EmirError:
        return [n for n in default if any(t["inst"] in paths for t in d.net_taps(n))]
    return [n for n in top_nets(d, insts) if n in default]


def hier_sim_view(d, a):
    """The top DSPF as `emir check` sees it for a --cc-sim next to --abstract:
    the abstracted instances' pins are not the simulation's to save."""
    from .hier_cli import Options
    try:
        insts = set(Options(a, command="check").abstracts)
    except EmirError:
        return d
    dd = copy.copy(d)
    dd.inst_pins = {k: v for k, v in d.inst_pins.items() if v["inst"] not in insts}
    dd.reindex()
    return dd


def check_hierarchy(d, nets, a, rep, proc=None):
    """`emir check` (and the run's input check) with --abstract: the
    options, every abstract, and how its ports land on the top's records."""
    from .hier_cli import Options
    try:
        opts = Options(a, command="check")
    except EmirError as e:
        rep.fail(e.code, "hierarchy", e.message, e.hint)
        return
    bad = opts.incompatible(a)
    if bad:
        rep.fail("EMIR-E811", "hierarchy", f"--abstract does not combine with "
                 f"{', '.join(bad)}", "drop the other drive option")
    insts = {}
    for inst, path in opts.abstracts.items():
        try:
            ab = load_abstract(path)
        except EmirError as e:
            rep.fail(e.code, "hierarchy", e.message, e.hint, path=path)
            continue
        insts[inst] = ab
        rep.ok("EMIR-I805", "hierarchy", f"{inst}: {path}: block {ab.get('block')}, nets "
               + ", ".join(f"{n} ({len(nb['ports'])} port(s))" for n, nb in ab["nets"].items()))
        if proc is not None and _num(ab.get("temp_C")) and abs(ab["temp_C"] - proc.temp) > 0.5:
            rep.warn("EMIR-W808", "hierarchy", f"{inst}: characterised at {ab['temp_C']:g} C, "
                     f"checked at {proc.temp:g} C")
    have = {t["inst"] for t in d.inst_pins.values()}
    for inst in insts:
        if inst not in have:
            rep.fail("EMIR-E802", "hierarchy", f"no *|I record of instance {inst!r} in the "
                     f"top DSPF")
    landed = False
    for net in nets:
        if not d.has_net(net) or not d.net_resistors(net):
            continue
        by = {}
        for t in d.net_taps(net):
            by.setdefault((t["inst"], t["pin"]), []).append(t)
        here = []
        for inst, ab in insts.items():
            for bnet, nb in ab["nets"].items():
                recs = by.get((inst, bnet))
                if not recs:
                    continue
                landed = True
                try:
                    match_ports(inst, bnet, nb["ports"], recs, opts.place.get(inst), opts.tol,
                                dspf=a.dspf, net=net, log=None)
                except EmirError as e:
                    rep.fail(e.code, "hierarchy", e.message, e.hint, net=net)
                    continue
                per = nb.get("period_s") or nb["n_samples"] * nb["dt_s"]
                here += [{"name": f"{inst}:{p['name']}", "inst": inst,
                          "_wave": np.asarray(p["wave"], dtype=float), "_per": float(per),
                          "_dt": float(nb["dt_s"]), "_tref": float(nb["t_ref_s"])}
                         for p in nb["ports"]]
        if here and not getattr(a, "feed", None) and not ports_of(d, net):
            rep.fail("EMIR-E813", "hierarchy", f"top net {net!r} has no *|P port: give --feed",
                     net=net)
        if here and opts.align == "sync":
            try:
                _sync_grid(here, opts.phase, warn=False)
            except EmirError as e:
                rep.fail(e.code, "hierarchy", f"net {net!r}: {e.message}", e.hint, net=net)
        if here:
            rep.ok("EMIR-I805", "hierarchy", f"net {net!r}: {len(here)} block port(s) land on "
                   f"its records", net=net)
    if insts and not landed:
        one = nets[0] if len(nets) == 1 else None
        rep.fail("EMIR-E809", "hierarchy", "no abstract lands on the net(s) to analyse",
                 net=one)
