"""The waveform self-consistent loop against RCC on Xyce, pass by pass.
SYNTHETIC (an invented mesh and level-1 inverters), Xyce, serial.

    python selfconsist.py --cases k32_n10 --out out/selfconsist          # seconds
    python selfconsist.py --cases k32_n10,k32_n20,hd16b,null_n20,one,vss_n10 \\
        --csv results/selfconsist_waveform.csv --md results/selfconsist_waveform.md

For each case (`CASES`: a mesh of `mesh.xyce_case`) it runs, one simulator
at a time:

* the RCC reference: Xyce on the RC netlist with every branch current saved;
* the open loop as `sweep.py` measures it: Xyce on `cc.cir` (the devices on an
  ideal rail behind hand-written ammeters), superposed through the R grid;
* the self-consistent loop as `emir run --mode cc-grid --self-consistent --sim
  xyce` runs it (`emir.selfconsist.converge` with its Xyce driver, the default
  tolerances), on `cc_tb.cir`, the plain CC testbench: emir splits it per
  instance pin and feeds each pin its own droop waveform. The loop stops
  where the run would stop; every pass is scored against RCC.

Every pass is scored over the window the loop chose at pass 0, branch by
branch (the governing branch -- the largest RCC value of the metric -- and
the classes major >= 50 %, secondary 10-50 %, minor 1-10 % of the largest),
for avg, rms and peak, the IR drop, and each device's own current against
RCC's current through its contact (`dev_*`: median, min and max over the
devices). Times: the simulation (`sim_s`), the grid side and PWL write
(`post_s`), the scoring (not counted), their running total (`total_s`), on a
shared machine (read ratios, not absolute times; `load_1min`).

Where the loop's DSPF has capacitance on the net (`hd16b_c20rc`), every pass
and the open loop are also scored through the RC grid, as `emir run
--dynamic` scores them (decap-aware EM: the branch currents of the RC grid's
transient, its capacitors to ground), in the `*_rcs` columns: the run's EM
otherwise scores the branches on the R-only grid (EMIR-W545).

`--guard` runs a case with the companion update off (the plain update on
every pass): where the loop gain is above 1 it diverges, and the guard must
stop it and report pass 0. `--include` takes an earlier run's rows (its
`--json`, or a CSV this script wrote, such as the committed record); a case
run again replaces its included rows. `results/selfconsist.csv` is the record
of the average-droop loop this one replaced (made by this script's earlier
version, before the waveform loop).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mesh  # noqa: E402
from sweep import CLASSES, LABEL, PROC, xyce  # noqa: E402

#: case -> mesh options: `k32_n<N>` 32 inverters on an N x N mesh (0.25 ohm
#: segments, 5 ohm contacts); `hd16b` the high-droop mesh (128 inverters,
#: 0.75 ohm segments: 4 % droop); `null_n20` a stiff mesh (2.5 mohm segments,
#: 1 mohm contacts: the numerical floor); `one` one inverter behind its 5 ohm
#: contact, no mesh; `vss_n10` k32_n10 with a VSS mesh too, both rails closed;
#: `p4_*` the inverters in four phases, not eight (twice as many switch
#: together); `hd16x` 1.5 ohm segments (about 8 % droop, beyond hd16b);
#: `hd16b_c20` 20 fF of grid capacitance per node instead of 2 fF, the loop on
#: the R-only DSPF (its feedback has no capacitance, EMIR-W544), and
#: `hd16b_c20rc` the same mesh with the loop on the RC DSPF (the RC grid's
#: droop fed back); `k32_n10_ng` the loop on ngspice (RCC on Xyce);
#: `k32_n10_long` k32_n10 simulated for 40 ns, not 4 (the window then holds
#: 39 periods: the most PWL points a pin needs). Options
#: the mesh does not take: `dspf` (the loop's DSPF, `grid_r.dspf` by
#: default) and `sim` (the loop's simulator, Xyce by default)
CASES = {
    "k32_n10": {"n": 10, "k": 32},
    "k32_n20": {"n": 20, "k": 32},
    "k32_n40": {"n": 40, "k": 32},
    "c0_n20": {"n": 20, "k": 32, "r_cont": 0.001},
    "hd16b": {"n": 16, "k": 128, "r_seg": 0.75},
    "null_n20": {"n": 20, "k": 32, "r_seg": 0.0025, "r_cont": 0.001},
    "one": {"n": 1, "k": 1},
    "vss_n10": {"n": 10, "k": 32, "vss": True},
    "p4_n20": {"n": 20, "k": 32, "phases": 4},
    "hd16b_p4": {"n": 16, "k": 128, "r_seg": 0.75, "phases": 4},
    "hd16x": {"n": 16, "k": 128, "r_seg": 1.5},
    "hd16b_c20": {"n": 16, "k": 128, "r_seg": 0.75, "c_node": 20e-15},
    "hd16b_c20rc": {"n": 16, "k": 128, "r_seg": 0.75, "c_node": 20e-15,
                    "dspf": "grid_rc.dspf"},
    "k32_n10_ng": {"n": 10, "k": 32, "sim": "ngspice"},
    "k32_n10_long": {"n": 10, "k": 32, "stop": 40e-9},
}


def score(res, rr) -> dict:
    """One cc-grid result (`check_net`'s) against the RCC one, branch by
    branch: relative error, plus is conservative (cc-grid above RCC)."""
    names = [r["name"] for r in res["network"].res]
    ri = {r["name"]: i for i, r in enumerate(rr["network"].res)}
    idx = np.array([ri[n] for n in names])
    out = {}
    for m in ("avg", "rms", "peak"):
        a, b = np.abs(res["Ie"][m]), np.abs(rr["Ie"][m][idx])
        g = int(np.argmax(b))
        out[f"{m}_gov"] = float((a[g] - b[g]) / b[g])
        every = b >= 0.01 * b.max()
        e = (a[every] - b[every]) / b[every]
        out[f"{m}_all_min"], out[f"{m}_all_max"] = float(e.min()), float(e.max())
        for name, lo, hi in CLASSES:
            sel = (b >= lo * b.max()) & ((b < hi * b.max()) if hi else True)
            e = (a[sel] - b[sel]) / b[sel]
            out[f"{m}_{name}_min"] = float(e.min()) if sel.any() else None
            out[f"{m}_{name}_max"] = float(e.max()) if sel.any() else None
    out["ir_cc_mV"] = float(res["ir_drop"]) * 1e3
    out["ir_rcc_mV"] = float(rr["ir_drop"]) * 1e3
    out["ir_err"] = float((res["ir_drop"] - rr["ir_drop"]) / rr["ir_drop"])
    return out


def device_score(ing, rcc_dev: dict) -> dict:
    """Each device's own current (the pass's tap metrics) against RCC's
    current through its contact (`rcc_dev`: pin -> {avg, rms, peak}):
    median, min and max over the devices of the relative error."""
    out = {}
    taps = [t for t in ing["taps"] if not t.get("feed")]
    for m in ("avg", "rms", "peak"):
        e = []
        for t in taps:
            pin = f"{t['inst']}:{t['pin']}"
            ref = abs(rcc_dev[pin][m])
            if ref > 0:
                e.append((abs(t[f"i_{m}"]) - ref) / ref)
        e = np.array(e)
        out[f"dev_{m}_med"] = float(np.median(e))
        out[f"dev_{m}_min"], out[f"dev_{m}_max"] = float(e.min()), float(e.max())
    return out


def rcc_devices(d, net: str, raw: str, window: dict) -> dict:
    """RCC's current through each device's contact (the resistor that ends
    on its instance pin), over `window`: pin -> {avg, rms, peak}."""
    from emir import ccsim, waves
    w = waves.load(raw)
    out = {}
    pins = set(d.pin_groups(net))
    for r in d.net_resistors(net):
        pin = r["n2"] if r["n2"] in pins else r["n1"] if r["n1"] in pins else None
        if pin is None:
            continue
        sig = w.match([f"{r['name']}:1"]).get(f"{r['name']}:1")
        if sig is None:
            continue
        m = ccsim.metrics_tran(w.x, np.asarray(w[sig]), window["t0"], window["t1"])
        out[pin] = {"avg": m["avg"], "rms": m["rms"], "peak": m["peak"]}
    return out


def nocap_deck(rcc: str, out: str) -> str:
    """The RCC deck without the grid's capacitors (the R-only grid's own
    reference: what the loop's fixed point should match)."""
    with open(rcc, encoding="utf-8") as f:
        lines = [ln for ln in f.read().splitlines() if not ln.startswith(("Cn", "Cg"))]
    text = "\n".join(lines).replace("file=rcc.raw", "file=rccnc.raw") + "\n"
    with open(out, "w", encoding="utf-8") as f:
        f.write(text)
    shutil.copy(rcc.replace(".cir", ".raw.names"), out.replace(".cir", ".raw.names"))
    return out


def nc_score(res, rr) -> dict:
    """`score` against RCC without the grid's capacitance: the governing
    branch and the minor peaks, suffixed `_nc`."""
    sc = score(res, rr)
    return {f"{k}_nc": sc[k] for k in ("avg_gov", "rms_gov", "peak_gov", "peak_minor_min",
                                       "peak_minor_max", "ir_err")}


#: the `*_rcs` columns: the pass scored through the RC grid (`rc_score`)
RCS_KEYS = ("avg_gov", "rms_gov", "peak_gov", "peak_minor_min", "peak_minor_max",
            "peak_secondary_min", "peak_secondary_max", "peak_all_min", "peak_all_max")


def rc_score(dl, net: str, res, ing, proc, rr) -> dict:
    """One pass scored through the RC grid of the loop's DSPF `dl`, as `emir
    run --dynamic` scores it (`decap.solve`, the decap-aware basis: the
    branch currents of the grid's transient with the pass's pin currents),
    against RCC (`rr`): `score`'s keys in RCS_KEYS, suffixed `_rcs`."""
    from types import SimpleNamespace

    from emir import decap
    from emir.errors import collecting
    r = dict(res)
    a = SimpleNamespace(em_basis="decap-aware", dyn_steps=None, dynamic=True)
    with collecting(propagate=False):
        decap.solve(dl, net, r, net, ing["taps"], ing.get("time"), proc, a,
                    log=lambda *x, **k: None)
    sc = score(r, rr)
    return {f"{k}_rcs": sc[k] for k in RCS_KEYS}


def read_rows(path: str) -> list:
    """The rows of an earlier run: its --json, or a CSV this script wrote
    (the values stay as written)."""
    if path.endswith(".csv"):
        with open(path, newline="", encoding="utf-8") as f:
            lines = [ln for ln in f if not ln.startswith("#")]
        return [dict(r) for r in csv.DictReader(lines)]
    return json.load(open(path, encoding="utf-8"))["rows"]


def ngspice_options(path: str, reltol: float):
    """The CC testbench for ngspice: its own options line (Xyce's `.options
    timeint` is not ngspice's) at the same relative tolerance."""
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text.replace(".options timeint reltol=1e-4", f".options reltol={reltol:g}"))


def with_reltol(out: str, reltol: float):
    """Every deck of a case simulated at `reltol` instead of the generator's
    1e-4 (Xyce's default is 1e-3): what the testbench's tolerance does."""
    for f in os.listdir(out):
        if f.endswith(".cir"):
            path = os.path.join(out, f)
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text.replace(".options timeint reltol=1e-4",
                                      f".options timeint reltol={reltol:g}"))


def one(case: str, opts: dict, root: str, passes: int, companion: bool = True,
        tag: str = "", nocap: bool = False, reltol: float | None = None) -> list:
    from emir import ccsim, rcc
    from emir import dspf as D
    from emir.engine import Process
    from emir.errors import collecting
    from emir.selfconsist import SpiceDriver, converge, merged
    quiet = lambda *a, **kw: None                               # noqa: E731
    out = os.path.join(root, case + tag)
    if os.path.isdir(out):
        shutil.rmtree(out)
    opts = dict(opts)
    n, k = opts.pop("n"), opts.pop("k")
    loop_dspf, sim = opts.pop("dspf", "grid_r.dspf"), opts.pop("sim", "xyce")
    info = mesh.xyce_case(n, k, out, **opts)
    if reltol is not None:
        with_reltol(out, reltol)
    if sim == "ngspice":
        ngspice_options(os.path.join(out, "cc_tb.cir"), reltol or 1e-4)
    nets = ["VDD", "VSS"] if info.get("vss") else ["VDD"]
    p = lambda f: os.path.join(out, f)                           # noqa: E731
    proc = Process.load(PROC)
    d = D.parse(p("grid_r.dspf"))
    dl = D.parse(p(loop_dspf))
    base = {"case": case + tag, "N": n, "grid_nodes": info["grid_nodes"],
            "resistors": info["resistors"], "devices": info["devices"],
            "r_seg_ohm": info["r_seg"], "r_cont_ohm": info["r_cont"],
            "vdd_V": mesh.VDD, "companion": companion, "phases": info["phases"],
            "c_node_fF": info["c_node"] * 1e15, "loop_dspf": loop_dspf, "loop_sim": sim}
    # ---- the RCC reference, and the open loop of sweep.py (cc.cir)
    rs = xyce(p("rcc.cir"))
    cs = xyce(p("cc.cir"))
    if nocap:
        xyce(nocap_deck(p("rcc.cir"), p("rccnc.cir")))
    ref, ol, win, devs, refnc = {}, {}, {}, {}, {}
    for net in nets:
        ing0 = ccsim.cc_taps(d, net, p("cc.raw"), period=mesh.PERIOD, feed=net, log=quiet)
        win[net] = ing0["window"]
        ref[net] = rcc.rcc_check(d, net, proc, p("rcc.raw"), net, period=mesh.PERIOD,
                                 window_sel=ing0["window"], dynamic=False, log=quiet)
        devs[net] = rcc_devices(d, net, p("rcc.raw"), ing0["window"])
        if nocap:
            refnc[net] = rcc.rcc_check(d, net, proc, p("rccnc.raw"), net, period=mesh.PERIOD,
                                       window_sel=ing0["window"], dynamic=False, log=quiet)
        inj = {}
        for t in ing0["taps"]:
            inj[t["node"]] = inj.get(t["node"], 0.0) + t["i_avg"]
        ol[net] = (D.check_net(d, net, proc, inj, net, taps_list=ing0["taps"],
                               tap_weights=ing0["weights"], log=quiet), ing0)
    # the loop's DSPF with capacitance on the net: every pass (and the open
    # loop, through the same DSPF) scored through the RC grid too
    rcs = {net: bool(dl.net_caps(net)) for net in nets}
    ol_rcs = {}
    for net in nets:
        if rcs[net]:
            ingl = ccsim.cc_taps(dl, net, p("cc.raw"), period=mesh.PERIOD, feed=net,
                                 log=quiet)
            inj = {}
            for t in ingl["taps"]:
                inj[t["node"]] = inj.get(t["node"], 0.0) + t["i_avg"]
            rl = D.check_net(dl, net, proc, inj, net, taps_list=ingl["taps"],
                             tap_weights=ingl["weights"], log=quiet)
            ol_rcs[net] = rc_score(dl, net, rl, ingl, proc, ref[net])
    base.update(rcc_sim_s=rs["wall_s"], rcc_steps=rs["steps"],
                load_1min=max(rs["load_1min"], cs["load_1min"]))
    rows = []
    for net in nets:
        res0, ing0 = ol[net]
        rows.append({**base, "net": net, "deck": "cc.cir", "pass": 0,
                     "droop_pct": 100 * float(ref[net]["ir_drop"]) / mesh.VDD,
                     "sim_s": cs["wall_s"], "steps": cs["steps"], "post_s": None,
                     "total_s": cs["wall_s"], **score(res0, ref[net]),
                     **device_score(ing0, devs[net]),
                     **(nc_score(res0, refnc[net]) if nocap else {}),
                     **ol_rcs.get(net, {})})
    # ---- the self-consistent loop on the plain CC testbench
    drv = SpiceDriver(sim, p("cc_tb.cir"), dl, nets if len(nets) > 1 else nets[0],
                      mesh.VDD, p("sc"), log=quiet)
    scored, later = {}, {}

    def on_pass(kk, net, res, ing):
        scored[(kk, net)] = {**score(res, ref[net]), **device_score(ing, devs[net]),
                             **(nc_score(res, refnc[net]) if nocap else {})}
        if rcs[net]:
            # through the RC grid after the loop: the scoring is not counted
            later[(kk, net)] = (res, ing)
        return {}
    t0 = time.perf_counter()
    with collecting(propagate=False) as notes:
        if len(nets) == 1:
            cv = {nets[0]: converge(None, drv.droop, dl, nets[0], proc, nets[0], mesh.VDD,
                                    period=mesh.PERIOD, max_passes=passes, driver=drv,
                                    on_pass=lambda kk, res, ing: on_pass(kk, nets[0], res,
                                                                         ing),
                                    companion=companion, log=quiet)}
        else:
            from emir import tdloop
            raw = tdloop.run(dl, nets, proc, {nt: nt for nt in nets},
                             {"VDD": mesh.VDD, "VSS": 0.0}, driver=drv, period=mesh.PERIOD,
                             max_passes=passes, on_pass=on_pass, companion=companion,
                             log=quiet, guard_V=mesh.VDD / 2)
            cv = {nt: merged(raw, nt) for nt in nets}
    wall = time.perf_counter() - t0
    for (kk, net), (res, ing) in later.items():
        scored[(kk, net)].update(rc_score(dl, net, res, ing, proc, ref[net]))
    codes = sorted({x["code"] for x in notes})
    for net in nets:
        c = cv[net]
        total = 0.0
        for rec in c["passes"]:
            total += rec["seconds"] + (rec.get("post_seconds") or 0.0)
            sc = scored[(rec["pass"], net)]
            comp = rec.get("companion") or {}
            kcl = rec.get("kcl") or {}
            rows.append({
                **base, "net": net, "deck": "cc_tb.cir split", "pass": rec["pass"],
                "droop_pct": 100 * float(ref[net]["ir_drop"]) / mesh.VDD,
                "fed_by": rec["fed_by"], "residual_mV": rec["residual_mV"],
                "tolerance_mV": rec["tolerance_mV"], "max_droop_mV": rec["max_droop_mV"],
                "converged": rec["converged"], "stop_rule": rec.get("stop_rule"),
                "stopped": c["stopped"],
                "reported_pass": c["reported_pass"],
                "sim_s": rec["seconds"], "steps": rec["points"],
                "pwl_points": rec["pwl_points"], "post_s": rec.get("post_seconds"),
                "total_s": total, "loop_wall_s": wall,
                "g_mS_med": (comp.get("g_mS") or [None])[0],
                "C_fF_med": (comp.get("C_fF") or [None])[0],
                "kcl_avg_rel": kcl.get("avg_rel"), "codes": " ".join(codes),
                # the droop the pass simulated (its largest), the PWL's own
                # tolerance and the first update's scale; how far the
                # reported items, the devices and every branch moved
                "fed_droop_mV": rec.get("simulated_droop_mV"),
                "pwl_tol_mV": rec.get("pwl_tol_mV"), "fed_scale": rec.get("fed_scale"),
                "items_move": (rec.get("metric_change") or {}).get("items"),
                "devices_move": (rec.get("metric_change") or {}).get("devices"),
                "branches_move": (rec.get("metric_change") or {}).get("branches"),
                "at_pass_limit": c.get("at_pass_limit"),
                "feedback": (c.get("feedback") or {}).get("grid"), **sc})
    return rows


def summarize(rows: list) -> dict:
    """Per case and net (`case` for VDD, `case:VSS` for VSS): how the loop
    ended, the open loop (`cc.cir`), pass 0, the reported pass, and the cost
    and KCL over the passes (what the goldens check)."""
    out = {}
    for key in dict.fromkeys((r["case"], r["net"]) for r in rows):
        rs = [r for r in rows if (r["case"], r["net"]) == key]
        ol = next((r for r in rs if r["deck"] == "cc.cir"), None)
        sp = [r for r in rs if r["deck"] != "cc.cir"]
        if not sp:
            continue
        last = sp[-1]
        rep = next(r for r in sp if int(r["pass"]) == int(last["reported_pass"]))
        steps0 = float(sp[0]["steps"])
        kcl = [float(r["kcl_avg_rel"]) for r in sp if r.get("kcl_avg_rel") not in (None, "")]
        out[key[0] if key[1] == "VDD" else f"{key[0]}:{key[1]}"] = {
            "stopped": last["stopped"], "reported_pass": int(last["reported_pass"]),
            "n_sims": int(last["pass"]) + 1, "open_loop": ol, "pass0": sp[0],
            "reported": rep,
            "max_steps_ratio": max(float(r["steps"]) for r in sp[1:]) / steps0
            if len(sp) > 1 else 1.0,
            "max_pwl_points": max((int(r["pwl_points"]) for r in sp[1:]
                                   if r.get("pwl_points") not in (None, "")), default=0),
            "max_kcl_avg_rel": max(kcl) if kcl else None,
            "loop_s": float(last["total_s"]), "rcc_sim_s": float(last["rcc_sim_s"])}
    return out


def pct(v, digits=2):
    return "" if v is None or v == "" else f"{100 * float(v):+.{digits}f} %"


def markdown(rows: list) -> str:
    L = [f"# The waveform self-consistent loop against RCC ({LABEL})", "",
         "Generated by `examples/06_big_block/selfconsist.py`. Synthetic meshes of "
         "`mesh.xyce_case` (level-1 CMOS inverters switching at 1 GHz in eight phases on a "
         "1 V rail, 200 fF loads, 5 ohm device contacts, 2 fF of grid capacitance per node, "
         "4 ns; the cases that differ: `p4_*` four phases, `hd16x` 1.5 ohm segments, "
         "`hd16b_c20*` 20 fF per node with the loop on the R-only DSPF (`hd16b_c20`) or the "
         "RC DSPF (`hd16b_c20rc`: the RC grid's droop fed back), `k32_n10_ng` the loop on "
         "ngspice, `k32_n10_long` 40 ns; columns `phases`, `c_node_fF`, `loop_dspf`, "
         "`loop_sim`, `feedback`). `cc.cir`: the open loop as "
         "`sweep.py` measures it; `split`: the plain CC testbench as `emir run --mode "
         "cc-grid --self-consistent --sim xyce` splits it, pass 0 being its open loop, every "
         "pass fed by `fed_by`. Relative error against RCC, branch by branch over the window "
         "the loop chose at pass 0; plus is conservative. `minor peak`: the range over the "
         "branches that carry 1-10 % of the largest. `device peak`: each device's own peak "
         "current against RCC's through its contact (median, and the range). Residual: the "
         "largest difference over the pins and the transient between the droop the pass's "
         "currents produce and the droop it simulated (the PWL as written). Times are wall "
         "seconds on a shared "
         "machine (single-threaded; read ratios, not absolute times): `sim` the simulation, "
         "`post` the grid side and the PWL file, `total` the loop so far; `steps` the "
         "simulator's time points.", "",
         "| case | net | droop | deck | pass | fed by | residual mV | avg gov | rms gov | "
         "peak gov | minor peak | device peak (median, range) | IR | sim s | steps | "
         "PWL points | post s | total s |", "|" + "---|" * 18]
    for r in rows:
        dev = (f"{pct(r['dev_peak_med'])} ({pct(r['dev_peak_min'])} .. "
               f"{pct(r['dev_peak_max'])})")
        minor = (f"{pct(r['peak_minor_min'])} .. {pct(r['peak_minor_max'])}"
                 if r.get("peak_minor_min") not in (None, "") else "")
        stop = ""
        if r["deck"] != "cc.cir" and r.get("converged") in (True, "True"):
            stop = f" (stops: {r.get('stop_rule') or 'residual'})"
        L.append(f"| {r['case']} | {r['net']} | {float(r['droop_pct']):.2f} % | {r['deck']} | "
                 f"{r['pass']}{stop} | {r.get('fed_by') or ''} | "
                 + ("" if r.get("residual_mV") in (None, "") else
                    f"{float(r['residual_mV']):.3g}")
                 + f" | {pct(r['avg_gov'])} | {pct(r['rms_gov'])} | {pct(r['peak_gov'])} | "
                 f"{minor} | {dev} | {pct(r['ir_err'])} | {float(r['sim_s']):.3g} | "
                 f"{r.get('steps') or ''} | {r.get('pwl_points') or ''} | "
                 + ("" if r.get("post_s") in (None, "") else f"{float(r['post_s']):.3g}")
                 + " | " + ("" if r.get("total_s") in (None, "") else
                            f"{float(r['total_s']):.3g}") + " |")
    nc = [r for r in rows if r.get("rms_gov_nc") not in (None, "")]
    if nc:
        L += ["", "Against RCC **without the grid's capacitors** (`--nocap`: the R-only grid's own "
              "reference, the fixed point the loop aims at). What the loop leaves against RCC "
              "above is the grid's capacitance, which an R-only grid does not have:", "",
              "| case | net | deck | pass | avg gov | rms gov | peak gov | minor peak | IR |",
              "|" + "---|" * 9]
        for r in nc:
            minor = (f"{pct(r['peak_minor_min_nc'])} .. {pct(r['peak_minor_max_nc'])}"
                     if r.get("peak_minor_min_nc") not in (None, "") else "")
            L.append(f"| {r['case']} | {r['net']} | {r['deck']} | {r['pass']} | "
                     f"{pct(r['avg_gov_nc'])} | {pct(r['rms_gov_nc'])} | {pct(r['peak_gov_nc'])} | "
                     f"{minor} | {pct(r['ir_err_nc'])} |")
    rcs = [r for r in rows if r.get("rms_gov_rcs") not in (None, "")]
    if rcs:
        L += ["", "Scored **through the RC grid** (`*_rcs`: the loop's DSPF has capacitance on "
              "the net, and `emir run --dynamic` scores the branches as here, decap-aware EM: "
              "the branch currents of the RC grid's transient with the pass's pin currents, "
              "its capacitors to ground). The rows above score the same currents on the "
              "R-only grid, as the run's EM does without --dynamic (EMIR-W545): the current "
              "the grid's capacitors carry is missing from those branches.", "",
              "| case | net | deck | pass | rms gov | peak gov | minor peak | secondary peak | "
              "every branch's peak |", "|" + "---|" * 9]
        for r in rcs:
            L.append(f"| {r['case']} | {r['net']} | {r['deck']} | {r['pass']} | "
                     f"{pct(r['rms_gov_rcs'])} | {pct(r['peak_gov_rcs'])} | "
                     f"{pct(r['peak_minor_min_rcs'])} .. {pct(r['peak_minor_max_rcs'])} | "
                     f"{pct(r['peak_secondary_min_rcs'])} .. "
                     f"{pct(r['peak_secondary_max_rcs'])} | "
                     f"{pct(r['peak_all_min_rcs'])} .. {pct(r['peak_all_max_rcs'])} |")
    L += ["", "How each loop ended (`stopped`), the pass the run reports, the RCC simulation's "
          "time, and the codes the loop raised:", ""]
    seen = set()
    for r in rows:
        key = (r["case"], r["net"])
        if r["deck"] == "cc.cir" or key in seen or r["net"] != "VDD":
            continue
        seen.add(key)
        last = [x for x in rows if (x["case"], x["net"]) == key and x["deck"] != "cc.cir"][-1]
        sims = int(last["pass"]) + 1
        lim = (" (the last allowed pass)" if str(last.get("at_pass_limit")) == "True"
               and last["stopped"] in ("converged", "settled") else "")
        fb = f"; the droop fed back: the {last['feedback']} grid's" if last.get("feedback") \
            else ""
        L.append(f"- {r['case']}: {last['stopped']}, reports pass {last['reported_pass']}"
                 f"{lim} ({sims} simulation{'s' if sims != 1 else ''}, "
                 f"{float(last['total_s']):.3g} s); RCC "
                 f"simulation {float(r['rcc_sim_s']):.3g} s{fb}; codes: "
                 f"{last['codes'] or 'none'}")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], allow_abbrev=False)
    ap.add_argument("--cases", default="k32_n10",
                    help=f"cases, comma-separated, of: {', '.join(CASES)}")
    ap.add_argument("--passes", type=int, default=10,
                    help="the loop's --max-passes (default 10, the run's)")
    ap.add_argument("--guard", action="store_true",
                    help="run the cases with the companion update off (the plain update): "
                         "the divergence guard's test")
    ap.add_argument("--nocap", action="store_true",
                    help="also simulate RCC without the grid's capacitors and score every "
                         "pass against it (`*_nc`): the R-only grid's own reference")
    ap.add_argument("--reltol", type=float, default=None,
                    help="simulate every deck at this relative tolerance instead of 1e-4 "
                         "(the case is tagged _reltol<exponent>)")
    ap.add_argument("--include", action="append", default=[], metavar="JSON",
                    help="rows of an earlier run (its --json) to put first in the outputs")
    ap.add_argument("--out", default=os.path.join(HERE, "out", "selfconsist"))
    ap.add_argument("--csv", default=None)
    ap.add_argument("--md", default=None)
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)
    plan = [c for c in a.cases.split(",") if c]
    for c in plan:
        if c not in CASES:
            ap.error(f"unknown case {c!r} (have {', '.join(CASES)})")
    if plan and not shutil.which("Xyce"):
        print("selfconsist: Xyce is not on PATH; nothing to run", file=sys.stderr)
        return 3
    rows = []
    for path in a.include:
        rows += read_rows(path)
    for c in plan:
        print(f"[selfconsist] {c}{' (plain update: the guard)' if a.guard else ''} ...",
              file=sys.stderr, flush=True)
        tag = ("_plain" if a.guard else "") + (
            f"_reltol{-int(round(np.log10(a.reltol)))}" if a.reltol else "")
        new = one(c, CASES[c], a.out, a.passes, companion=not a.guard, tag=tag,
                  nocap=a.nocap, reltol=a.reltol)
        # a case run again replaces its included rows, where they stood
        at = next((i for i, r in enumerate(rows) if r["case"] == c + tag), len(rows))
        rows = [r for r in rows[:at] if r["case"] != c + tag] + new + \
            [r for r in rows[at:] if r["case"] != c + tag]
        for r in new:
            print(json.dumps(r, default=float), flush=True)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"label": LABEL, "rows": rows, "cases": summarize(rows)}, f, indent=1,
                      default=float)
    if a.csv:
        os.makedirs(os.path.dirname(os.path.abspath(a.csv)), exist_ok=True)
        keys = []
        for r in rows:
            keys += [k for k in r if k not in keys]
        with open(a.csv, "w", newline="", encoding="utf-8") as f:
            f.write(f"# {LABEL}. Generated by examples/06_big_block/selfconsist.py (the "
                    f"waveform loop). Errors relative to RCC, + is conservative; residual and "
                    f"tolerance in mV per pass; times in s (wall)\n")
            w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                # floats in exponent notation, six digits (a plain decimal of
                # one measured value spelled a licensed PDK's number: the leak
                # guard)
                w.writerow({k: (f"{v:.6e}" if isinstance(v, float) else v)
                            for k, v in r.items()})
    if a.md:
        with open(a.md, "w", encoding="utf-8") as f:
            f.write(markdown(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
