#!/usr/bin/env python3
"""End-to-end proof of the open-simulator path, with numbers to quote.

Runs `emir simulate` + `emir run` on real MOS loads (level-1 models) on a rail,
with Xyce and with ngspice, in cc-grid (CC netlist, devices probed) and rcc
(the RC netlist, every branch current) modes, and compares:

* each simulator's cc-grid with its own rcc (the two modes agree?);
* Xyce with ngspice (the two simulators agree?);
* both with the closed-form hand calculation, where one exists.

Three load cases on the loadrail example (examples/02_xyce: a 12 x 1 ohm
metal rail, four contacts, four NMOS sinks; every value invented):

``dc``      the sinks' gates held at 1 V: each draws I_on = 25 uA * W/L
            (2.0, 2.5, 3.0, 2.5 mA) for the whole run. The hand calculation is
            exact: branch currents are sums of I_on, IR = sum(I * R).
``pulse``   the example as it ships: each gate pulses 2 ns (0.1 ns ramps)
            every 10 ns, one sink after another. Average = I_on * (2 + 0.2/6)
            / 10 per sink; the pulses never overlap, so a segment's rms^2 is the
            sum of its sinks' rms^2 = I_on^2 * 16 * (0.0625 * 2 + 0.2 / 160) / 10;
            peak = the largest I_on it carries.
``inverter`` four CMOS inverters (lambda 0.05, gate and junction capacitance)
            switching 0.5 pF loads, sources on the rail: their currents depend
            on the rail's droop, which the CC netlist does not have -- the
            case where cc-grid and rcc may differ. No closed form.
``sky130``  (opt-in: `--cases sky130`, ngspice only) the pulsed sinks as the
            sky130 PDK's own `sky130_fd_pr__nfet_01v8` (W = 10, 12, 15, 12 um,
            L = 0.15 um, gates pulsed to 1.2 V), read from $PDK_ROOT/sky130A
            (`.lib .../sky130.lib.spice tt`, its spinit as the testbench's
            .spiceinit). Open models, short-channel: the current depends on
            the drain voltage, so cc-grid and rcc differ. No closed form.

    python scripts/opensim_proof.py [--out DIR] [--sims xyce,ngspice]
    PDK_ROOT=/path/to/pdks python scripts/opensim_proof.py --cases sky130 --sims ngspice

Writes DIR/proof.json (every number) and DIR/proof.md (the tables), and
exits 1 when an agreement check fails. One simulator process at a time.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
EX = os.path.join(ROOT, "examples", "02_xyce")
PROC = os.path.join(ROOT, "emir", "data", "sample", "process.yaml")

I_ON = (2.0, 2.5, 3.0, 2.5)                  # mA, the four sinks
TAP_NODE = (3, 6, 9, 12)                     # the rail node each sink's contact is on
R_SEG, R_CONT = 1.0, (12.0, 12.0, 12.0, 24.0)
CUTS = (2, 2, 2, 1)
#: the sample process file's limits (mA): Metal1 0.75 um wide; Cont per cut
WIRE = {"avg": 3.2 * 0.75, "rms": math.sqrt(28.8 * 5.0) * 0.75, "peak": 16.0 * 0.75}
VIA = {"avg": 0.75, "rms": 1.5, "peak": 2.4}

#: agreement thresholds (relative) the proof enforces. cc-grid vs rcc: the
#: sinks' currents do not depend on the rail (lambda = 0), but the RC netlist
#: also charges the rail's own capacitance, which the CC netlist does not have
#: (a 1e-4 effect on the pulse case's peaks)
TOL = {"dc_vs_hand": 1e-6, "pulse_vs_hand": 1e-3, "sim_vs_sim": 1e-3, "cc_vs_rcc": 1e-3}


def emir(*args, cwd):
    p = subprocess.run([sys.executable, "-m", "emir", *map(str, args), "--format", "json"],
                       cwd=cwd, capture_output=True, text=True,
                       env={**os.environ, "PYTHONPATH": ROOT})
    try:
        doc = json.loads(p.stdout)
    except json.JSONDecodeError as e:
        raise SystemExit(f"emir {' '.join(map(str, args))}: no JSON\n{p.stderr[-2000:]}") from e
    if p.returncode != 0:
        raise SystemExit(f"emir {' '.join(map(str, args))}: exit {p.returncode}\n"
                         f"{json.dumps(doc, indent=1)[:2000]}")
    return doc


# ------------------------------------------------------------------ the cases


def hand(case: str) -> dict | None:
    """`{(name, metric): ratio}` and the IR (mV) by hand, or None."""
    if case not in ("dc", "pulse"):          # no closed form: inverter, sky130
        return None
    if case == "dc":
        avg = rms = list(I_ON)
        peak = list(I_ON)
    else:
        f = (2.0 + 0.2 / 6) / 10.0
        g = 16 * (0.0625 * 2.0 + 0.2 / 160) / 10.0       # rms^2 / I_on^2
        avg = [i * f for i in I_ON]
        rms = [i * math.sqrt(g) for i in I_ON]
        peak = list(I_ON)
    out = {}
    for seg in range(1, 13):
        down = [k for k in range(4) if TAP_NODE[k] >= seg]   # sinks beyond this segment
        out[(f"Rm{seg}", "avg")] = sum(avg[k] for k in down) / WIRE["avg"]
        if case == "dc":
            out[(f"Rm{seg}", "rms")] = sum(rms[k] for k in down) / WIRE["rms"]
            out[(f"Rm{seg}", "peak")] = sum(peak[k] for k in down) / WIRE["peak"]
        else:                                                # pulses never overlap
            out[(f"Rm{seg}", "rms")] = math.sqrt(sum(rms[k] ** 2 for k in down)) / WIRE["rms"]
            out[(f"Rm{seg}", "peak")] = max(peak[k] for k in down) / WIRE["peak"]
    for k in range(4):
        for m, v in (("avg", avg), ("rms", rms), ("peak", peak)):
            out[(f"Rc{k}", m)] = v[k] / (VIA[m] * CUTS[k])
    seg_avg = [sum(avg[k] for k in range(4) if TAP_NODE[k] >= s) for s in range(1, 13)]
    ir = sum(seg_avg) * R_SEG + avg[3] * R_CONT[3]
    return {"ratios": out, "ir_mV": ir}


def make_case(case: str, work: str):
    """The inputs of one case in `work`: the example, varied."""
    os.makedirs(work, exist_ok=True)
    for f in ("loadrail.dspf", "models.sp", "cc.sp", "tb_cc.cir", "tb_rcc.cir"):
        shutil.copy(os.path.join(EX, f), os.path.join(work, f))
    if case == "dc":
        for tb in ("tb_cc.cir", "tb_rcc.cir"):
            p = os.path.join(work, tb)
            text = open(p).read()
            text = re.sub(r"PULSE\([^)]*\)", "DC 1.0", text)
            open(p, "w").write(text)
    if case == "inverter":
        _inverters(work)
    if case == "sky130":
        _sky130(work)


#: the sky130 case's device widths (um), one per sink
SKY_W = (10, 12, 15, 12)


def sky130_lib() -> str | None:
    """$PDK_ROOT/sky130A/libs.tech/ngspice/sky130.lib.spice, if it is there."""
    root = os.environ.get("PDK_ROOT")
    p = os.path.join(root, "sky130A", "libs.tech", "ngspice", "sky130.lib.spice") if root else None
    return p if p and os.path.isfile(p) else None


def _sky130(work: str):
    """The pulsed sinks as sky130 nfets (the PDK's files are read, not copied)."""
    lib = sky130_lib()
    if lib is None:
        raise SystemExit("the sky130 case needs $PDK_ROOT/sky130A (the open PDK)")
    shutil.copy(os.path.join(os.path.dirname(lib), "spinit"), os.path.join(work, ".spiceinit"))
    for name in ("loadrail.dspf", "cc.sp"):
        p = os.path.join(work, name)
        text = open(p).read()
        for k, w in enumerate(SKY_W):
            text = re.sub(rf"(XMN{k} \S+ G{k} VSS VSS) nsink W=\d+u L=1u",
                          rf"\1 sky130_fd_pr__nfet_01v8 W={w} L=0.15", text)
        open(p, "w").write(text)
    for tb in ("tb_cc.cir", "tb_rcc.cir"):
        p = os.path.join(work, tb)
        text = open(p).read().replace(".include models.sp", f".lib {lib} tt")
        open(p, "w").write(text.replace("PULSE(0 1 ", "PULSE(0 1.2 "))


def _inverters(work: str):
    """Four CMOS inverters on the rail: the PMOS sources are the DSPF's pins."""
    dspf = open(os.path.join(EX, "loadrail.dspf")).read()
    dspf = dspf.replace("XMN", "XMP").replace("XMP0:d XMP0 d", "XMP0:s XMP0 s")
    for k in range(4):
        dspf = dspf.replace(f"XMP{k}:d XMP{k} d", f"XMP{k}:s XMP{k} s")
        dspf = dspf.replace(f"XMP{k}:d", f"XMP{k}:s")
    lines = [ln for ln in dspf.splitlines() if not ln.startswith("XMP")]
    end = lines.index(".ENDS loadrail")
    dev = []
    for k in range(4):
        dev += [f"XMP{k} OUT{k} G{k} XMP{k}:s VNW pfet W=40u L=0.5u",
                f"XMN{k} OUT{k} G{k} VSS VSS nfet W=20u L=0.5u",
                f"CL{k} OUT{k} VSS 0.5p"]
    lines[end:end] = dev
    head = lines.index(".SUBCKT loadrail VDD VSS G0 G1 G2 G3")
    lines[head] = ".SUBCKT loadrail VDD VSS VNW G0 G1 G2 G3"
    open(os.path.join(work, "loadrail.dspf"), "w").write("\n".join(lines) + "\n")
    cc = ["* inverter bank, CC netlist: the devices on an ideal rail",
          ".SUBCKT loadrail VDD VSS VNW G0 G1 G2 G3"]
    for k in range(4):
        cc += [f"XMP{k} OUT{k} G{k} VDD VNW pfet W=40u L=0.5u",
               f"XMN{k} OUT{k} G{k} VSS VSS nfet W=20u L=0.5u", f"CL{k} OUT{k} VSS 0.5p"]
    cc.append(".ENDS loadrail")
    open(os.path.join(work, "cc.sp"), "w").write("\n".join(cc) + "\n")
    open(os.path.join(work, "models.sp"), "w").write(
        "* invented level-1 models with lambda and capacitances (not any foundry's)\n"
        ".model nlv NMOS level=1 vto=0.5 kp=200u lambda=0.05 cgso=0.3n cgdo=0.3n "
        "cj=1m cjsw=0.1n pb=0.8\n"
        ".model plv PMOS level=1 vto=-0.5 kp=80u lambda=0.05 cgso=0.3n cgdo=0.3n "
        "cj=1m cjsw=0.1n pb=0.8\n"
        ".subckt nfet d g s b params: W=1u L=1u\nM1 d g s b nlv W={W} L={L} "
        "AD={W*1u} AS={W*1u} PD={2*W} PS={2*W}\n.ends nfet\n"
        ".subckt pfet d g s b params: W=1u L=1u\nM1 d g s b plv W={W} L={L} "
        "AD={W*1u} AS={W*1u} PD={2*W} PS={2*W}\n.ends pfet\n")
    for kind, inc in (("cc", "cc.sp"), ("rcc", "loadrail.dspf")):
        open(os.path.join(work, f"tb_{kind}.cir"), "w").write(
            f"* inverter bank, {kind}\n.include models.sp\n.include {inc}\n"
            "VDD vdd 0 DC 1.8\nVNW vnw 0 DC 1.8\n"
            + "".join(f"VG{k} g{k} 0 PULSE(0 1.8 {2.5 * k}n 50p 50p 4.9n 10n)\n"
                      for k in range(4))
            + "XI0 vdd 0 vnw g0 g1 g2 g3 loadrail\n.tran 5p 40n 0 5p\n.end\n")


# ------------------------------------------------------------------ the runs


def run_one(case, sim, mode, work) -> dict:
    """simulate + run; the numbers of the run summary."""
    raw = os.path.join(work, "sim", f"{sim}_{mode}.raw")
    tb = "tb_rcc.cir" if mode == "rcc" else "tb_cc.cir"
    t0 = time.time()
    s = emir("simulate", tb, "--sim", sim, "--dspf", "loadrail.dspf", "--mode", mode,
             *(["--probe"] if mode == "cc" else []), "--out", raw, cwd=work)
    if s["missing"]:
        raise SystemExit(f"{case} {sim} {mode}: currents missing: {s['missing']}")
    d = emir("run", "loadrail.dspf", "--process", PROC, "--cc-sim", raw, "--mode",
             "rcc" if mode == "rcc" else "cc-grid", "--period", "1e-8", "--em-temp", "100",
             cwd=work)
    n = d["nets"][0]
    return {"sim_seconds": s["seconds"], "wall_seconds": round(time.time() - t0, 2),
            "points": s["points"], "signals": s["signals"], "verdict": n["verdict"],
            "governing": n["governing_metric"], "max_ratio": n["max_ratio"],
            "ir_mV": n["ir_drop_mV"], "dynamic_mV": n["dynamic_mV"],
            "worst": [(i["name"], i["metric"], i["ratio"]) for i in n["worst_items"]],
            "ratios": _all_ratios(work, raw, mode)}


def _all_ratios(work, raw, mode) -> dict:
    """Every segment's avg / rms / peak ratio, through the Python API (the
    summary lists the worst items only)."""
    sys.path.insert(0, ROOT)
    from emir import ccsim, rcc
    from emir.dspf import check_net, parse
    from emir.engine import Process
    from emir.errors import collecting
    d = parse(os.path.join(work, "loadrail.dspf"))
    proc = Process.load(PROC, temp=100.0, delta_t=5.0)
    quiet = lambda *a, **k: None                      # noqa: E731
    with collecting():
        if mode == "rcc":
            r = rcc.rcc_check(d, "VDD", proc, raw, "VDD", period=1e-8, log=quiet)
            ie = r["Ie"]
            names = [x["name"] for x in r["network"].res]
        else:
            # as `emir run` does it (emir/dspf.py, the --cc-sim branch)
            ing = ccsim.cc_taps(d, "VDD", raw, period=1e-8, feed="VDD", log=quiet)
            inj: dict = {}
            for t in ing["taps"]:
                inj[t["node"]] = inj.get(t["node"], 0.0) + t["i_avg"]
            r = check_net(d, "VDD", proc, inj, "VDD", taps_list=ing["taps"],
                          tap_weights=ing["weights"], log=quiet)
            ie = r["Ie"]
            names = [x["name"] for x in r["network"].res]
    out = {}
    for k, nm in enumerate(names):
        cuts = CUTS[int(nm[2:])] if nm.startswith("Rc") else None
        for m in ("avg", "rms", "peak"):
            amps = float(abs(ie[m][k])) * 1e3
            out[f"{nm}:{m}"] = amps / (VIA[m] * cuts if cuts else WIRE[m])
    return out


def rel(a, b) -> float:
    return abs(a - b) / max(abs(b), 1e-30)


def worst_dev(x: dict, y: dict) -> tuple:
    """`(max relative deviation, key)` over the keys both have (ratios
    above 1 % of the largest, so a near-zero segment does not dominate)."""
    big = max(abs(v) for v in y.values())
    keys = [k for k in x if k in y and abs(y[k]) > 0.01 * big]
    k = max(keys, key=lambda k: rel(x[k], y[k]))
    return rel(x[k], y[k]), k


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], allow_abbrev=False)
    ap.add_argument("--out", default=os.path.join("emir_out", "opensim_proof"))
    ap.add_argument("--sims", default="xyce,ngspice")
    ap.add_argument("--cases", default="dc,pulse,inverter")
    a = ap.parse_args(argv)
    sims = [s for s in a.sims.split(",") if s]
    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    res: dict = {"emir": None, "tools": {}, "cases": {}, "checks": []}
    sys.path.insert(0, ROOT)
    import emir as _e
    res["emir"] = _e.__version__
    for s in sims:
        from emir import site
        exe = site.tool(s)[0]
        if not exe:
            raise SystemExit(f"{s} not found")
        v = subprocess.run([exe, "-v"] if s == "xyce" else [exe, "--version"],
                           capture_output=True, text=True)
        text = v.stdout + v.stderr
        m = re.search(r"ngspice-\S+|Xyce \S+", text)
        res["tools"][s] = m.group(0) if m else text.strip().splitlines()[0][:120]
    res["machine"] = f"{platform.system()} {platform.machine()}, python {platform.python_version()}"
    ok = True

    def check(name, dev, tol, where):
        nonlocal ok
        good = dev <= tol
        ok = ok and good
        res["checks"].append({"check": name, "max_rel_dev": dev, "tolerance": tol,
                              "where": where, "pass": good})

    for case in [c for c in a.cases.split(",") if c]:
        work = os.path.join(out, case)
        shutil.rmtree(work, ignore_errors=True)
        make_case(case, work)
        runs = {}
        csims = [x for x in sims if case != "sky130" or x == "ngspice"]
        for sim in csims:
            for mode in ("cc", "rcc"):
                runs[f"{sim}/{mode}"] = run_one(case, sim, mode, work)
                print(f"{case:8s} {sim:7s} {mode:3s} {runs[f'{sim}/{mode}']['verdict']} "
                      f"max {runs[f'{sim}/{mode}']['max_ratio']} "
                      f"IR {runs[f'{sim}/{mode}']['ir_mV']:.4f} mV", file=sys.stderr)
        h = hand(case)
        res["cases"][case] = {"runs": runs, "hand": None if h is None else
                              {"ir_mV": h["ir_mV"],
                               "ratios": {f"{k[0]}:{k[1]}": v for k, v in h["ratios"].items()}}}
        for sim in csims:
            cc, rc = runs[f"{sim}/cc"], runs[f"{sim}/rcc"]
            dev, k = worst_dev(cc["ratios"], rc["ratios"])
            irdev = rel(cc["ir_mV"], rc["ir_mV"])
            res["cases"][case][f"{sim}_cc_vs_rcc"] = {"max_rel_dev": dev, "at": k,
                                                       "ir_rel_dev": irdev}
            if case in ("dc", "pulse"):
                check(f"{case}: {sim} cc-grid vs rcc", max(dev, irdev), TOL["cc_vs_rcc"], k)
            if h is not None:
                hr = {f"{kk[0]}:{kk[1]}": v for kk, v in h["ratios"].items()}
                tol = TOL["dc_vs_hand"] if case == "dc" else TOL["pulse_vs_hand"]
                for mode in ("cc", "rcc"):
                    r = runs[f"{sim}/{mode}"]
                    dev, k = worst_dev(r["ratios"], hr)
                    irdev = rel(r["ir_mV"], h["ir_mV"])
                    res["cases"][case][f"{sim}_{mode}_vs_hand"] = {
                        "max_rel_dev": dev, "at": k, "ir_rel_dev": irdev}
                    check(f"{case}: {sim} {mode} vs hand", max(dev, irdev), tol, k)
        if len(csims) == 2:
            for mode in ("cc", "rcc"):
                x, y = runs[f"{sims[0]}/{mode}"], runs[f"{sims[1]}/{mode}"]
                dev, k = worst_dev(x["ratios"], y["ratios"])
                irdev = rel(x["ir_mV"], y["ir_mV"])
                res["cases"][case][f"{mode}_{sims[0]}_vs_{sims[1]}"] = {
                    "max_rel_dev": dev, "at": k, "ir_rel_dev": irdev}
                check(f"{case}: {mode} {sims[0]} vs {sims[1]}", max(dev, irdev),
                      TOL["sim_vs_sim"] if case != "inverter" else 0.02, k)
    with open(os.path.join(out, "proof.json"), "w") as f:
        json.dump(res, f, indent=1)
    md = render(res, sims)
    with open(os.path.join(out, "proof.md"), "w") as f:
        f.write(md)
    print(md)
    print(f"\n{'ALL CHECKS PASS' if ok else 'A CHECK FAILED'} ({out}/proof.json)")
    return 0 if ok else 1


def render(res, sims) -> str:
    L = [f"emir {res['emir']}; " + "; ".join(f"{k}: {v}" for k, v in res["tools"].items()),
         f"{res['machine']}", ""]
    for case, c in res["cases"].items():
        L += [f"### {case}", "", "| run | verdict | worst | Rm1 avg | Rm1 rms | Rm1 peak | "
              "Rc3 avg | Rc3 peak | IR mV | dyn IR mV | sim s |", "|---|---|---|---|---|---|---|---|---|---|---|"]
        rows = list(c["runs"].items())
        if c["hand"]:
            rows.append(("hand", {"verdict": "", "worst": [], "ratios": c["hand"]["ratios"],
                                  "ir_mV": c["hand"]["ir_mV"], "dynamic_mV": None,
                                  "sim_seconds": None}))
        for name, r in rows:
            q = r["ratios"]
            w = r["worst"][0] if r["worst"] else None

            def f(v, p=4):
                return "" if v is None else f"{v:.{p}f}"
            L.append(f"| {name} | {r['verdict']} | "
                     + (f"{w[0]} {w[1]} {w[2]:.4f}x" if w else "")
                     + f" | {f(q.get('Rm1:avg'))} | {f(q.get('Rm1:rms'))} | "
                       f"{f(q.get('Rm1:peak'))} | {f(q.get('Rc3:avg'))} | {f(q.get('Rc3:peak'))} | "
                       f"{f(r['ir_mV'])} | {f(r['dynamic_mV'], 3)} | {f(r['sim_seconds'], 2)} |")
        L.append("")
        for k, v in c.items():
            if isinstance(v, dict) and "max_rel_dev" in v:
                L.append(f"- {k}: worst ratio deviation {v['max_rel_dev']:.2e} ({v['at']}), "
                         f"IR {v['ir_rel_dev']:.2e}")
        L.append("")
    L += ["### checks", ""] + [f"- {'PASS' if c['pass'] else 'FAIL'} {c['check']}: "
                               f"{c['max_rel_dev']:.2e} <= {c['tolerance']:g} ({c['where']})"
                               for c in res["checks"]]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
