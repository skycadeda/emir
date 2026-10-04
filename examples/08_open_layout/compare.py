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
   rule over the window run.sh's --period gives, the last three 4 ns periods),
   within `exact_rel`;
3. **the run numbers** (each net's IR drop and worst ratio, the violations, the
   worst item's fix) of every run against the golden file within `rel`
   (simulator and model versions);
4. **branch by branch**, cc-grid against rcc (relative error, plus is
   conservative, cc-grid above rcc), every checked branch carrying at least 1 %
   of its net's largest: on the rails out and vss, the open loop conservative
   on the governing branch (rms and peak above rcc), and the self-consistent
   loop within `loop`: what this example measured, stated beside the method's
   measured accuracy in docs/method-modes.md. The gate net vb (in the open
   loop and the loop that feeds it back) and the loop on the rails alone
   (`sc_rails`) are scored and reported, not bounded.

It writes OUT_DIR/comparison.json and OUT_DIR/comparison.md, and compares the
operating point on magic's network with what the removed `emir layout` reported
on the same GDS (expected/emir_layout_record.json): a record, not a check.
Screening, not sign-off.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = ("rcc", "open", "sc", "sc_rails", "op_rcc", "op_cc")
NETS = ("out", "vss")
#: the nets scored branch by branch against rcc: the two rails, bounded, and
#: the gate net vb, scored and reported, not bounded (its own wire
#: capacitance carries part of its current, which the cc-grid branches,
#: scored on the R-only grid, do not carry). A gate's average current is
#: about zero (its charge comes back), so vb is scored on rms and peak only:
#: an error relative to an average of about zero, and its IR drop, mean
#: nothing
SCORED = NETS + ("vb",)
#: the window run.sh's --period 4e-9 gives on a 12.5 ns transient: three
#: whole periods back from the end
WINDOW = (0.5e-9, 12.5e-9)
CLASSES = (("major", 0.5, None), ("secondary", 0.1, 0.5), ("minor", 0.01, 0.1))
TOLERANCE = {"rel": 0.05, "count_rel": 0.05, "exact_rel": 1e-6,
             # the self-consistent loop against rcc, governing branch and IR
             # (|relative error| at most), and every branch carrying >= 1 %
             "loop": {"avg": 0.005, "rms": 0.005, "peak": 0.03, "ir": 0.015,
                      "all": 0.15}}


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


def score(out, run) -> dict:
    """`run` against rcc, branch by branch, per net and metric: the governing
    branch (rcc's largest), every branch carrying >= 1 % of it, and the
    major / secondary / minor classes; the IR drop."""
    ref, got = branches(out, "rcc"), branches(out, run)
    rs = {n["net"]: n for n in load(os.path.join(out, "rcc.json"))["nets"]}
    gs = {n["net"]: n for n in load(os.path.join(out, f"{run}.json"))["nets"]}
    res = {}
    for net in SCORED:
        keys = sorted(k for k in ref if k[0] == net and k in got)
        if net not in gs or net not in rs or not keys:
            continue                          # vb is not in the rails-only loop
        rails = net in NETS
        r = {"branches": len(keys),
             "ir": ((gs[net]["ir_drop_mV"] - rs[net]["ir_drop_mV"]) / rs[net]["ir_drop_mV"]
                    if rails else None)}
        for m in ("avg", "rms", "peak") if rails else ("rms", "peak"):
            b = np.array([abs(ref[k]["currents"][m]) for k in keys])
            a = np.array([abs(got[k]["currents"][m]) for k in keys])
            e = (a - b) / np.where(b > 0, b, 1.0)
            g = int(np.argmax(b))
            every = b >= 0.01 * b.max()
            r[m] = {"governing": keys[g][1], "gov": float(e[g]),
                    "all": [float(e[every].min()), float(e[every].max())],
                    "n_all": int(every.sum())}
            for name, lo, hi in CLASSES:
                sel = (b >= lo * b.max()) & ((b < hi * b.max()) if hi else True)
                r[m][name] = [float(e[sel].min()), float(e[sel].max())] if sel.any() else None
        res[net] = r
    return res


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
    return bad


def check_scores(ex, sc, op, tol) -> list:
    bad = []
    if not ex["max_rel_error"] <= tol["exact_rel"]:
        bad.append(f"rcc is not the simulator's own: max relative error "
                   f"{ex['max_rel_error']:.3g} over {ex['branches']} branches")
    lp = tol["loop"]
    for net, r in ((n, x) for n, x in sc.items() if n in NETS):
        for m in ("avg", "rms", "peak"):
            if abs(r[m]["gov"]) > lp[m]:
                bad.append(f"sc.{net}.{m}: governing {r[m]['gov']:+.3%} beyond {lp[m]:.1%}")
            lo, hi = r[m]["all"]
            if max(-lo, hi) > lp["all"]:
                bad.append(f"sc.{net}.{m}: a branch at {lo:+.2%} .. {hi:+.2%}, beyond "
                           f"{lp['all']:.0%}")
        if abs(r["ir"]) > lp["ir"]:
            bad.append(f"sc.{net}.ir: {r['ir']:+.3%} beyond {lp['ir']:.1%}")
    for net, r in ((n, x) for n, x in op.items() if n in NETS):
        for m in ("rms", "peak"):
            if r[m]["gov"] < 0:
                bad.append(f"open.{net}.{m}: the open loop read the governing branch low "
                           f"({r[m]['gov']:+.3%})")
    return bad


# ------------------------------------------------------------------ the record


def pct(v):
    return f"{100 * v:+.2f} %"


def span(v):
    return "--" if v is None else f"{100 * v[0]:+.2f} % .. {100 * v[1]:+.2f} %"


def markdown(d, ex, scores, record) -> str:
    L = ["# Example 08: measured", "",
         "Every number is from this run of run.sh (synthetic layout, public SKY130 data). "
         "Screening, not sign-off.", "",
         f"- Extraction: {d['extraction']['resistors']} resistors (every one a drawn "
         f"segment, {d['extraction']['merged']} merged), {d['extraction']['capacitors']} "
         f"capacitors, {d['extraction']['devices']} devices; layers with no limit in the "
         f"process file (their current is in the IR drop): "
         f"{', '.join(d['unchecked_layers'])}.",
         f"- rcc against the simulator's own output, {ex['branches']} checked branches: "
         f"largest relative difference {ex['max_rel_error']:.2g}.", "",
         "| run | net | IR drop | worst ratio | violations | worst item's fix |",
         "|---|---|---|---|---|---|"]
    for run in RUNS:
        for net, n in d["runs"][run]["nets"].items():
            fix = (n["worst"] or {}).get("fix") or ""
            L.append(f"| {run} | {net} | {n['ir_drop_mV']:.4f} mV | {n['max_ratio']:.4f}x | "
                     f"{n['n_violations']} | {fix} |")
    L += ["", "cc-grid against rcc, branch by branch (relative error, plus reads high), every "
              "checked branch carrying at least 1 % of its net's largest; the gate net vb on "
              "rms and peak only (its average current is about zero), reported, not "
              "bounded:", "",
          "| run | net | metric | governing branch | every branch | major | secondary | minor "
          "| IR |", "|---|---|---|---|---|---|---|---|---|"]
    for run, sc in scores.items():
        for net, r in sc.items():
            ms = [m for m in ("avg", "rms", "peak") if m in r]
            for m in ms:
                x = r[m]
                ir = pct(r["ir"]) if m == ms[0] and r["ir"] is not None else ""
                L.append(f"| {run} | {net} | {m} | {pct(x['gov'])} ({x['governing']}) | "
                         f"{span(x['all'])} | {span(x['major'])} | {span(x['secondary'])} | "
                         f"{span(x['minor'])} | {ir} |")
    if record:
        L += ["", "The operating point on magic's network (op_cc: the CC netlist's currents, "
                  "as the removed layout command simulated them) against what it reported "
                  "on this GDS (expected/emir_layout_record.json):", "",
              "| net | IR drop, magic's network | IR drop, the layout command | worst ratio, "
              "magic's | worst ratio, the layout command |", "|---|---|---|---|---|"]
        for net, rec in record["nets"].items():
            n = d["runs"]["op_cc"]["nets"][net]
            L.append(f"| {net} | {n['ir_drop_mV']:.4f} mV | {rec['ir_drop_mV']:.4f} mV | "
                     f"{n['max_ratio']:.4f}x | {rec['max_ratio']:.4f}x |")
    return "\n".join(L) + "\n"


def main(argv):
    out = argv[0]
    d = digest(out)
    ex = exactness(out)
    scores = {"open": score(out, "open"), "sc": score(out, "sc"),
              "sc_rails": score(out, "sc_rails")}
    rec_path = os.path.join(HERE, "expected", "emir_layout_record.json")
    record = load(rec_path) if os.path.isfile(rec_path) else None
    with open(os.path.join(out, "comparison.json"), "w", encoding="utf-8") as f:
        json.dump({**d, "rcc_exactness": ex, "branch_scores": scores}, f, indent=1)
        f.write("\n")
    with open(os.path.join(out, "comparison.md"), "w", encoding="utf-8") as f:
        f.write(markdown(d, ex, scores, record))
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
    tol = golden["tolerance"]
    bad = check(d, golden, tol) + check_scores(ex, scores["sc"], scores["open"], tol)
    for b in bad:
        print(f"MISMATCH {b}")
    print(f"{argv[1]}: {'matches' if not bad else f'{len(bad)} mismatch(es)'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
