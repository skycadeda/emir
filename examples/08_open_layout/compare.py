"""Example 08's results against expected/results.json, and the measured
comparison the README quotes.

    python compare.py OUT_DIR expected/results.json            # exit 0 when it matches
    python compare.py OUT_DIR --write expected/results.json    # make the golden file

It reads what run.sh left in OUT_DIR and checks:

1. **the extraction** (extract.json): magic's resistors, capacitors, devices and
   the layers the process file gives no limit (counts within `count_rel`: another
   magic or technology file version may split a shape differently);
2. **rcc is exact**: every checked branch's avg, rms and peak in the rcc run
   (its viewer data, `--viewer-all`) against the same reduction of the
   simulator's own output (sim/rcc.raw, read here without emir: the trapezoidal
   rule over the window run.sh's --period gives, the record's last three 4 ns
   periods, `emir.waveform.window`), within `exact_rel`;
3. **rcc's IR is the simulator's**: the deck `emir simulate` wrote
   (sim/rcc.<sim>.cir) is run once more with every node voltage printed instead
   of the branch currents (sim/v.<sim>.cir, a few seconds), and each net's
   static IR (the spread of the nodes' average potential against the port) and
   dynamic IR (the largest excursion from it) over the same window are checked
   against the rcc run's within `ir_exact_rel`;
4. **the run numbers** (each net's IR drop and worst ratio, the violations, the
   worst item's fix) of every run against the golden file within `rel`
   (simulator and model versions);
5. **no number from a refused run**: the rcc runs without --period
   (rcc_no_period.json) and with a period 10 % off (3.6 ns,
   rcc_near_period.json) were refused with the golden file's codes (each an
   `emir.error/1`).

It writes OUT_DIR/comparison.json and OUT_DIR/comparison.md. Screening, not
sign-off.
"""
from __future__ import annotations

import glob
import json
import math
import os
import re
import shutil
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
RUNS = ("rcc", "op_rcc")
NETS = ("out", "vss")
#: the window run.sh's --period 4e-9 gives on a 16.5 ns transient: the
#: record's last three whole periods (`emir.waveform.window`)
WINDOW = (4.5e-9, 16.5e-9)
#: the refused runs: what each one gave
REFUSED = {"rcc_no_period": "no --period", "rcc_near_period": "--period 3.6e-9, 10 % off"}
TOLERANCE = {"rel": 0.05, "count_rel": 0.05, "exact_rel": 1e-6, "ir_exact_rel": 1e-5}


# ------------------------------------------------------------------ reading


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def raw(path) -> tuple:
    """(time, {name: current}) of an ASCII raw file (Xyce -a, ngspice
    `set filetype=ascii`): every variable, by its name."""
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    nvar = int(next(ln for ln in lines if ln.startswith("No. Variables:")).split(":")[1])
    k = lines.index("Variables:")
    names = [lines[k + 1 + j].split()[1] for j in range(nvar)]
    vals = [float(x) for ln in lines[lines.index("Values:") + 1:] for x in ln.split()]
    # each point: its index, then nvar values
    data = np.array(vals).reshape(-1, nvar + 1)[:, 1:]
    return data[:, 0], {n: data[:, j] for j, n in enumerate(names)}


def resistor(name: str) -> str | None:
    """The DSPF resistor a simulator's current name is (`I(XDUT:R12)`,
    `@r.xdut.r12[i]`, `i(...)`)."""
    m = re.search(r"[:.](R\d+)\W*(?:\[i\])?\)?$", name, re.I)
    return m.group(1).upper() if m else None


def reduce(t, y, t0, t1) -> dict:
    """avg, rms, peak of `y` over [t0, t1]: the endpoints interpolated, the
    trapezoidal rule on the samples and on their squares."""
    inner = (t > t0) & (t < t1)
    tt = np.concatenate([[t0], t[inner], [t1]])
    yy = np.concatenate([[np.interp(t0, t, y)], y[inner], [np.interp(t1, t, y)]])
    w = np.zeros_like(tt)
    dt = np.diff(tt)
    w[:-1] += dt / 2
    w[1:] += dt / 2
    T = tt[-1] - tt[0]
    return {"avg": abs(float(yy @ w) / T), "rms": math.sqrt(float((yy * yy) @ w) / T),
            "peak": float(np.abs(yy).max())}


def branches(out, run) -> dict:
    """(net, R) -> the run's viewer record (every checked branch)."""
    viz = load(os.path.join(out, f"{run}_viewer", "emir_viz.json"))
    return {(e["net"], e["name"]): e for e in viz["em"]}


# ------------------------------------------------------------------ measuring


def digest(out) -> dict:
    """What the golden file holds."""
    ext = load(os.path.join(out, "extract.json"))
    d = {"extraction": {k: ext[k] for k in ("resistors", "segments", "capacitors", "devices",
                                            "nets_with_resistors", "merged")},
         "unchecked_layers": ext["unchecked_layers"], "ports": ext["ports"], "runs": {}}
    for run in RUNS:
        files = ([f"{run}.json"] if not run.startswith("op_")
                 else [f"{run}_{net}.json" for net in NETS])
        ss = [load(os.path.join(out, f)) for f in files]
        d["runs"][run] = {"verdict": "FAIL" if any(s["verdict"] == "FAIL" for s in ss)
                          else ss[0]["verdict"], "nets": {}}
        for n in (n for s in ss for n in s["nets"]):
            if n["net"] not in NETS:
                continue
            w = (n.get("worst_items") or [None])[0]
            d["runs"][run]["nets"][n["net"]] = {
                "mode": n["mode"], "ir_drop_mV": n["ir_drop_mV"],
                "max_ratio": n["max_ratio"]["all"], "n_violations": n["n_violations"],
                "worst": {k: w[k] for k in ("layer", "metric", "fix")} if w else None}
    # the refused runs: each one's error document at --summary-out
    d["refused"] = {}
    for run in REFUSED:
        no = load(os.path.join(out, f"{run}.json"))
        d["refused"][run] = (no["code"] if no.get("schema") == "emir.error/1"
                             else f"not refused ({no.get('schema')})")
    return d


def exactness(out) -> dict:
    """rcc's branch currents against the simulator's own output."""
    t, sig = raw(os.path.join(out, "sim", "rcc.raw"))
    sim = {resistor(n): y for n, y in sig.items() if resistor(n)}
    worst, n = 0.0, 0
    for (net, name), e in branches(out, "rcc").items():
        ref = reduce(t, sim[name], *WINDOW)
        for m in ("avg", "rms", "peak"):
            a, b = abs(e["currents"][m]), ref[m]
            if b > 0:
                worst = max(worst, abs(a - b) / b)
        n += 1
    return {"branches": n, "max_rel_error": worst}


def dspf_nets(path) -> tuple:
    """`({net: {node}}, [port])` of the DSPF: the nodes of each `*|NET`
    section's resistors, and the subcircuit's ports."""
    nets, ports, net = {}, [], None
    with open(path, encoding="utf-8") as f:
        for ln in f:
            w = ln.split()
            if not w:
                continue
            if w[0].upper() == ".SUBCKT":
                ports = w[2:]
            elif w[0] == "*|NET":
                net = w[1]
                nets.setdefault(net, set())
            elif net and w[0][0] in "Rr" and len(w) >= 4:
                nets[net] |= {w[1], w[2]}
    return nets, ports


def voltage_run(out):
    """Run the deck emir simulate wrote once more, with every node voltage
    printed (Xyce `V(*)`; ngspice saves them all when the deck saves nothing
    else): (time, {name: volts}) of sim/v.raw, or None with the reason."""
    decks = glob.glob(os.path.join(out, "sim", "rcc.*.cir"))
    if len(decks) != 1:
        return None, "no deck of emir simulate in sim/"
    sim = os.path.basename(decks[0]).split(".")[1]
    exe = shutil.which("Xyce" if sim == "xyce" else sim)
    if exe is None or sim not in ("xyce", "ngspice"):
        return None, f"{sim} is not on PATH"
    with open(decks[0], encoding="utf-8") as f:
        lines = f.read().splitlines()
    keep, skip = [], False
    for ln in lines:
        # emir's saves: its comment lines, then .print / .save and their `+` lines
        if ln.startswith("* emir: ") or ln.startswith("* (interactive ngspice"):
            skip = True
            continue
        if skip and (ln.startswith((".print", ".save", ".options filetype", "+"))):
            continue
        skip = False
        keep.append(ln)
    end = max(i for i, ln in enumerate(keep) if ln.strip().lower() == ".end")
    if sim == "xyce":
        keep.insert(end, ".print tran format=raw file=v.raw V(*)")
        cmd = [exe, "-a", "v.xyce.cir"]
    else:
        keep.insert(end, ".options filetype=ascii")
        cmd = [exe, "-b", "-r", "v.raw", "v.ngspice.cir"]
    deck = os.path.join(out, "sim", f"v.{sim}.cir")
    with open(deck, "w", encoding="utf-8") as f:
        f.write("\n".join(keep) + "\n")
    with open(os.path.join(out, "sim", "v.log"), "w", encoding="utf-8") as log:
        p = subprocess.run(cmd, cwd=os.path.dirname(deck), stdout=log,
                           stderr=subprocess.STDOUT, timeout=120)
    if p.returncode != 0:
        raise SystemExit(f"{sim} failed on {deck} (see sim/v.log)")
    return raw(os.path.join(out, "sim", "v.raw")), None


def _voltage(sig, node, ports, inst="XDUT"):
    """The raw signal of a DSPF node: a port is the testbench's own node (the
    instance connects each port to the node of its name), any other node the
    instance's (`V(XDUT:n)` in Xyce, `xdut.n` in ngspice)."""
    names = ([f"V({node})", node] if node in ports
             else [f"V({inst}:{node})", f"{inst}.{node}", f"V({inst}.{node})"])
    up = {k.upper(): k for k in sig}
    for n in names:
        if n.upper() in up:
            return sig[up[n.upper()]]
    return None


def ir_exactness(out) -> dict:
    """The rcc run's static and dynamic IR per net against the same numbers
    from the simulator's own node voltages, over the same window."""
    got, why = voltage_run(out)
    if got is None:
        return {"ran": False, "why": why}
    t, sig = got
    nets, ports = dspf_nets(os.path.join(out, "extract", "sink.dspf"))
    run = {n["net"]: n for n in load(os.path.join(out, "rcc.json"))["nets"]}
    inner = (t > WINDOW[0]) & (t < WINDOW[1])
    tt = np.concatenate([[WINDOW[0]], t[inner], [WINDOW[1]]])
    res, worst = {}, 0.0
    for net in NETS:
        ref = _voltage(sig, net, ports)
        avg, peak, missing = [0.0], [0.0], 0
        for node in sorted(nets[net] - {net}):
            v = _voltage(sig, node, ports)
            if v is None:
                missing += 1
                continue
            d = v - ref
            dd = np.concatenate([[np.interp(WINDOW[0], t, d)], d[inner],
                                 [np.interp(WINDOW[1], t, d)]])
            w = np.zeros_like(tt)
            w[:-1] += np.diff(tt) / 2
            w[1:] += np.diff(tt) / 2
            avg.append(float(dd @ w) / (tt[-1] - tt[0]))
            peak.append(float(np.abs(dd).max()))
        sim = {"static_mV": (max(avg) - min(avg)) * 1e3, "dynamic_mV": max(peak) * 1e3}
        mine = {"static_mV": run[net]["ir_drop_mV"], "dynamic_mV": run[net]["dynamic_mV"]}
        rel = max(abs(mine[k] - sim[k]) / sim[k] for k in sim)
        worst = max(worst, rel)
        res[net] = {"simulator": sim, "rcc": mine, "nodes": len(avg) - 1,
                    "missing": missing, "rel": rel}
    return {"ran": True, "nets": res, "max_rel_error": worst}


# ------------------------------------------------------------------ checking


def _close(a, b, rel):
    return math.isclose(float(a), float(b), rel_tol=rel, abs_tol=1e-9)


def check(d, golden, tol) -> list:
    bad = []
    for k, v in golden["extraction"].items():
        if not _close(d["extraction"][k], v, tol["count_rel"]):
            bad.append(f"extraction.{k}: {d['extraction'][k]} != {v}")
    for k in ("unchecked_layers", "ports"):
        if d[k] != golden[k]:
            bad.append(f"{k}: {d[k]} != {golden[k]}")
    for run, g in golden["runs"].items():
        got = d["runs"][run]
        if got["verdict"] != g["verdict"]:
            bad.append(f"{run}.verdict: {got['verdict']} != {g['verdict']}")
        for net, gn in g["nets"].items():
            n = got["nets"].get(net)
            if n is None:
                bad.append(f"{run}.{net}: missing")
                continue
            for k in ("ir_drop_mV", "max_ratio"):
                if not _close(n[k], gn[k], tol["rel"]):
                    bad.append(f"{run}.{net}.{k}: {n[k]:.6g} != {gn[k]:.6g} "
                               f"(rel {tol['rel']:g})")
            for k in ("mode", "n_violations", "worst"):
                if n[k] != gn[k]:
                    bad.append(f"{run}.{net}.{k}: {n[k]!r} != {gn[k]!r}")
    for run, code in golden.get("refused", {}).items():
        if d["refused"].get(run) != code:
            bad.append(f"{run}: {d['refused'].get(run)} != refused with {code}")
    return bad


def check_exact(ex, tol) -> list:
    if ex["max_rel_error"] <= tol["exact_rel"]:
        return []
    return [f"rcc is not the simulator's own: max relative error "
            f"{ex['max_rel_error']:.3g} over {ex['branches']} branches"]


def check_ir(ir, tol) -> list:
    if not ir["ran"]:
        return [f"rcc's IR not checked against the simulator's node voltages: {ir['why']}"]
    bad = [f"{net}: {n['missing']} node voltage(s) missing" for net, n in ir["nets"].items()
           if n["missing"]]
    if ir["max_rel_error"] > tol["ir_exact_rel"]:
        bad.append(f"rcc's IR is not the simulator's: max relative error "
                   f"{ir['max_rel_error']:.3g}")
    return bad


# ------------------------------------------------------------------ the record


def markdown(d, ex, ir) -> str:
    L = ["# Example 08: measured", "",
         "Every number is from this run of run.sh (synthetic layout, public SKY130 data). "
         "Screening, not sign-off.", "",
         f"- Extraction: {d['extraction']['resistors']} resistors (every one a drawn "
         f"segment, {d['extraction']['merged']} merged), {d['extraction']['capacitors']} "
         f"capacitors, {d['extraction']['devices']} devices; layers with no limit in the "
         f"process file (their current is in the IR drop): "
         f"{', '.join(d['unchecked_layers'])}.",
         f"- rcc against the simulator's own output, {ex['branches']} checked branches: "
         f"largest relative difference {ex['max_rel_error']:.2g}."]
    if ir["ran"]:
        L.append("- rcc's IR against the simulator's own node voltages (static, then "
                 "dynamic): " + "; ".join(
                     f"{net} {n['rcc']['static_mV']:.4f} / {n['rcc']['dynamic_mV']:.4f} mV "
                     f"against {n['simulator']['static_mV']:.4f} / "
                     f"{n['simulator']['dynamic_mV']:.4f} mV over {n['nodes']} nodes"
                     for net, n in ir["nets"].items())
                 + f"; largest relative difference {ir['max_rel_error']:.2g}.")
    else:
        L.append(f"- rcc's IR was not checked against the simulator's node voltages: "
                 f"{ir['why']}.")
    L += [f"- {run} (the same result, {REFUSED.get(run, run)}): refused, {code}, no "
          f"number." for run, code in d.get("refused", {}).items()]
    L += ["",
         "| run | net | IR drop | worst ratio | violations | worst item's fix |",
         "|---|---|---|---|---|---|"]
    for run in RUNS:
        for net, n in d["runs"][run]["nets"].items():
            fix = (n["worst"] or {}).get("fix") or ""
            L.append(f"| {run} | {net} | {n['ir_drop_mV']:.4f} mV | {n['max_ratio']:.4f}x | "
                     f"{n['n_violations']} | {fix} |")
    return "\n".join(L) + "\n"


def main(argv):
    out = argv[0]
    d = digest(out)
    ex = exactness(out)
    ir = ir_exactness(out)
    with open(os.path.join(out, "comparison.json"), "w", encoding="utf-8") as f:
        json.dump({**d, "rcc_exactness": ex, "rcc_ir_exactness": ir}, f, indent=1)
        f.write("\n")
    with open(os.path.join(out, "comparison.md"), "w", encoding="utf-8") as f:
        f.write(markdown(d, ex, ir))
    if len(argv) == 3 and argv[1] == "--write":
        with open(argv[2], "w", encoding="utf-8") as f:
            json.dump({"tolerance": TOLERANCE, **d,
                       "note": "Xyce 7.10 with the open_pdks 1.0.510 SKY130 models; magic "
                               "8.3.515 with the geoout patch. `rel` covers other simulator "
                               "and model versions, `count_rel` other magic versions."},
                      f, indent=1)
            f.write("\n")
        print(f"wrote {argv[2]}")
        return 0
    golden = load(argv[1])
    tol = {**TOLERANCE, **golden["tolerance"]}
    bad = check(d, golden, tol) + check_exact(ex, tol) + check_ir(ir, tol)
    for b in bad:
        print(f"MISMATCH {b}")
    print(f"{argv[1]}: {'matches' if not bad else f'{len(bad)} mismatch(es)'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
