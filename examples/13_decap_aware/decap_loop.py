"""The self-consistent loop on a mesh with decaps, against RCC on Xyce, pass by
pass. SYNTHETIC (an invented mesh, devices and decaps), Xyce, serial.

    python decap_loop.py --out out/loop --json out/loop.json
    python decap_loop.py --out out/loop \\
        --csv results/decap_loop.csv --md results/decap_loop.md

The mesh is example 06's high-droop one, `hd16b` (16 x 16, 0.75 ohm segments,
128 level-1 inverters at 1 GHz: 4 % droop), with this example's decaps
(`decap_mesh.decap_case`: 8 MOS decaps of 5 pF behind 2 ohm, each on a 0.5 ohm
strap). Each case (`CASES`) runs, one simulator at a time:

* RCC: Xyce on `rcc.cir` (the RC mesh, the straps, each decap's ESR and
  capacitor; every branch current saved);
* the loop as `emir run --mode cc-grid --self-consistent --sim xyce --netlist
  cc_tb.cir --dynamic` runs it, in process (`emir.selfconsist.converge` with
  its Xyce driver and the default tolerances, on the RC DSPF with the decaps,
  `grid_decap.dspf`), once per variant:
  - `sized`: the process file's cap_models size the decaps, so the loop feeds
    the droop of the grid with them back;
  - `unsized`: the decap model's entry left empty (`ncap:` with no values):
    cap_models cannot size the decaps, so they are left out of the droop fed
    back (EMIR-W547) and of the decap-aware EM after it (EMIR-W409).

`hd16b_dec_sim` is the same mesh with decap 0 a device of the testbench
(`simulate_decap`): a level-1 MOS whose gate capacitance is the same 5 pF, its
gate an instance pin of VDD that the split feeds, so the loop simulates its
current instead of feeding it back (`simulated_decaps`); RCC holds the same
MOS behind 1 mohm. Its loop runs at the default pass limit (10, `sized`) and
at 16 (`sized_16`: the limit the run's retry step offers after EMIR-W407).

Every pass of each loop (pass 0 is the open loop exactly) is scored against
RCC over the window the loop chose at pass 0, as `emir run --dynamic` scores
it (`bigblock.cc_dynamic` on the pass's currents): branch by branch on each EM
basis (`free` the R-only grid, `aware` through the RC grid with the decaps it
can size, `worst` per segment the larger of the two) -- the governing branch
(the largest RCC value of the metric), the classes major >= 50 %, secondary
10-50 %, minor 1-10 % of the largest (straps apart) and the decaps' straps --
and each device's own current against RCC's current through its contact
(`dev_*`). Error is emir minus RCC over RCC: plus is conservative.

Writes one JSON document, and optionally a CSV row per variant and pass and a
Markdown table. The decap numbers of EMIR-W547, of the report, of the loop's
texts (docs/big-blocks.md "It feeds back the decaps", docs/method-modes.md "In
the decaps", the CHANGELOG) and of part 4 of the README come from `results/`
written by this script. Screening, not sign-off.
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
import decap_mesh  # noqa: E402
import decap_sweep  # noqa: E402
import mesh  # noqa: E402  (from ../06_big_block, via decap_mesh)
import selfconsist  # noqa: E402  (from ../06_big_block)

LABEL = "Xyce, synthetic, serial"
#: the mesh: example 06's hd16b (selfconsist.CASES) with example 13's decaps
MESH = {"n": 16, "k": 128, "r_seg": 0.75, "decaps": 8, "c_decap_F": 5e-12, "esr_ohm": 2.0}
#: case -> (its variants, the decaps the testbench simulates)
CASES = {"hd16b_dec": (("sized", "unsized"), 0), "hd16b_dec_sim": (("sized", "sized_16"), 1)}
#: a variant's pass limit: the default (10), or `_<N>` after its name
MAX_PASSES = 10
#: the testbench's own decap device: a level-1 NMOS whose gate capacitance
#: (drain, source and bulk at 0 V, the channel inverted: Cox W L) is the
#: decaps' 5 pF (cox = 3.45e-11 / tox = 0.01 F/m^2, as cap_models says)
DECAP_MODEL = ".model ncapm NMOS level=1 vto=0.3 kp=200u tox=3.45e-9"
BASES = ("free", "aware", "worst")
METRICS = ("avg", "rms", "peak")


def quiet(*_a, **_k):
    pass


def unsized_process(src: str, dst: str) -> str:
    """The case's process file with the decap model's entry left empty (no
    cox, no access resistance): cap_models cannot size any decap."""
    with open(src, encoding="utf-8") as f:
        lines = f.read().splitlines()
    keep = [ln for ln in lines if not ln.lstrip().startswith(("cox:", "esr_a:"))]
    with open(dst, "w", encoding="utf-8") as f:
        f.write("\n".join(keep) + "\n")
    return dst


def build(out: str, simulated: int = 0) -> dict:
    """A case's decks and DSPFs in `out` (see the module doc)."""
    if os.path.isdir(out):
        shutil.rmtree(out)
    info = decap_mesh.decap_case(MESH["n"], MESH["k"], MESH["decaps"], MESH["c_decap_F"],
                                 MESH["esr_ohm"], out, r_seg=MESH["r_seg"])
    unsized_process(os.path.join(out, "process.yaml"), os.path.join(out, "process_unsized.yaml"))
    for q in range(simulated):
        simulate_decap(out, q)
    return info


def _edit(path: str, reps: list) -> None:
    with open(path, encoding="utf-8") as f:
        text = f.read()
    for a, b in reps:
        if text.count(a) != 1:
            raise SystemExit(f"{path}: expected one {a!r}")
        text = text.replace(a, b)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def simulate_decap(out: str, q: int) -> None:
    """Decap `q` a device of the testbench: in the DSPF its gate is an
    instance pin of VDD at the end of its strap (no internal node), in the
    CC testbench a MOS on the rail the split feeds, in RCC the same MOS
    behind 1 mohm (in place of its ESR and linear capacitor)."""
    w = MESH["c_decap_F"] / (decap_mesh.COX * decap_mesh.DEC_L)
    mos = f"ncapm L={decap_mesh.DEC_L:g} W={w:g}"
    with open(os.path.join(out, "grid_decap.dspf"), encoding="utf-8") as f:
        text = f.read()
    xy = text.split(f"*|S (DS{q} ", 1)[1].split(")", 1)[0]
    _edit(os.path.join(out, "grid_decap.dspf"), [
        (f"*|S (DS{q} {xy})", f"*|I (MDC{q}:g MDC{q} g I 0 {xy})"),
        (f" DS{q} {decap_mesh.STRAP_R:g} $metal1", f" MDC{q}:g {decap_mesh.STRAP_R:g} $metal1"),
        (f"MDC{q} MDC{q}:d DS{q} MDC{q}:s VSS", f"MDC{q} MDC{q}:d MDC{q}:g MDC{q}:s VSS")])
    _edit(os.path.join(out, "cc_tb.cir"), [
        (".options timeint", f"{DECAP_MODEL}\nMDC{q} 0 VDD 0 0 {mos}\n.options timeint")])
    _edit(os.path.join(out, "rcc.cir"), [
        (f"Rdc{q} ds{q} dci{q} {MESH['esr_ohm']:g}\n", f"Rdc{q} ds{q} dci{q} 0.001\n"),
        (f"Cdc{q} dci{q} 0 {MESH['c_decap_F']:g}\n", f"MDC{q} 0 dci{q} 0 0 {mos}\n"),
        (".options timeint", f"{DECAP_MODEL}\n.options timeint")])


def score_pass(d, net, res, ing, proc, ref, devs, straps_of, simulated=()) -> dict:
    """One pass's currents scored against RCC on every EM basis, as `emir run
    --dynamic` scores the reported pass (`cc_dynamic`), and its devices."""
    import argparse as _a

    from emir.bigblock import cc_dynamic
    from emir.errors import collecting
    r = dict(res)
    r["_loop_simulated_decaps"] = list(simulated)
    a = _a.Namespace(em_basis="decap-aware", dynamic=True, dyn_steps=None, argv=None,
                     max_memory=None, superposition="auto", power_nets=None, mode="cc-grid")
    with collecting(propagate=False):
        cc_dynamic(d, net, r, net, ing["taps"], ing.get("time"), proc, a, log=quiet)
    names = [x["name"] for x in r["network"].res]
    ri = {x["name"]: i for i, x in enumerate(ref["network"].res)}
    idx = np.array([ri[n] for n in names])
    straps = straps_of(names)
    eb = r.get("em_basis") or {}
    unsized = (eb.get("decaps_not_sized") or {}).get("n") or 0
    row = {"aware_honoured": bool(eb.get("used") == "decap-aware" and not eb.get("fallback")
                                  and not unsized), "decaps_not_sized": unsized}
    free = {m: np.abs(np.asarray((r.get("em_free") or {}).get("Ie", r["Ie"])[m]))
            for m in METRICS}
    aware = {m: np.abs(np.asarray(r["Ie"][m])) for m in METRICS}
    worst = {m: np.maximum(free[m], aware[m]) for m in METRICS}
    for basis, Ie in (("free", free), ("aware", aware), ("worst", worst)):
        for m in METRICS:
            decap_sweep._classes(Ie[m], np.abs(ref["Ie"][m][idx]), f"{basis}_{m}", row,
                                 straps)
    sim = set(simulated)
    row.update(selfconsist.device_score(
        {"taps": [t for t in ing["taps"] if t.get("inst") not in sim]}, devs))
    # a decap the testbench simulates: its own current (its pin's) against
    # RCC's through its strap
    for m in METRICS:
        e = [(abs(t[f"i_{m}"]) - abs(devs[f"{t['inst']}:{t['pin']}"][m]))
             / abs(devs[f"{t['inst']}:{t['pin']}"][m])
             for t in ing["taps"] if not t.get("feed") and t.get("inst") in sim]
        row[f"simdec_{m}"] = min(e) if e else None       # several: the lowest
    row["ir_mV"] = float(res["ir_drop"]) * 1e3
    return row


def run_variant(out: str, variant: str) -> tuple:
    """One loop, as `emir run --self-consistent --sim xyce` runs it: its
    result and every pass's currents (scored later, against RCC)."""
    from emir import dspf as D
    from emir.engine import Process
    from emir.errors import collecting
    from emir.selfconsist import SpiceDriver, converge
    net = "VDD"
    kind, _, limit = variant.partition("_")
    proc = Process.load(os.path.join(out, "process.yaml" if kind == "sized"
                                     else "process_unsized.yaml"))
    d = D.parse(os.path.join(out, "grid_decap.dspf"))
    drv = SpiceDriver("xyce", os.path.join(out, "cc_tb.cir"), d, net, mesh.VDD,
                      os.path.join(out, f"sc_{variant}"), log=quiet)
    kept = {}

    def on_pass(k, res, ing):
        kept[k] = (res, ing)
        return {}
    t0 = time.perf_counter()
    with collecting(propagate=False) as notes:
        cv = converge(None, drv.droop, d, net, proc, net, mesh.VDD, period=mesh.PERIOD,
                      max_passes=int(limit or MAX_PASSES), driver=drv, on_pass=on_pass,
                      log=quiet)
    return {"cv": cv, "kept": kept, "proc": proc, "d": d, "max_passes": int(limit or MAX_PASSES),
            "codes": " ".join(sorted({x["code"] for x in notes})),
            "wall": time.perf_counter() - t0}


def variant_rows(case: str, variant: str, lp: dict, ref: dict, devs: dict,
                 window: dict) -> list:
    cv, kept = lp["cv"], lp["kept"]
    fb = cv.get("feedback") or {}
    sim = list(cv.get("simulated_decaps") or [])

    def straps_of(names):
        return np.array([n.startswith("Rsd") for n in names])
    rows = []
    for rec in cv["passes"]:
        k = rec["pass"]
        res, ing = kept[k]
        if (ing["window"]["t0"], ing["window"]["t1"]) != (window["t0"], window["t1"]):
            raise SystemExit(f"{case} {variant} pass {k}: window {ing['window']} is not "
                             f"pass 0's")
        rows.append({
            "case": case, "variant": variant, "pass": k,
            "reported": k == cv["reported_pass"], "stopped": cv["stopped"],
            "converged": cv["converged"], "reported_pass": cv["reported_pass"],
            "n_sims": len(cv["passes"]), "max_passes": lp["max_passes"],
            "residual_mV": rec["residual_mV"],
            "tolerance_mV": rec["tolerance_mV"], "sim_s": rec["seconds"],
            "loop_wall_s": lp["wall"], "feedback": fb.get("grid"),
            "feedback_decaps": fb.get("decaps"),
            "feedback_decaps_pF": (fb.get("decaps_F") or 0.0) * 1e12,
            "unsized_decaps": fb.get("unsized_decaps"),
            "simulated_decaps": fb.get("simulated_decaps"), "codes": lp["codes"],
            **score_pass(lp["d"], "VDD", res, ing, lp["proc"], ref, devs, straps_of, sim)})
    return rows


def run_case(root: str, case: str) -> list:
    from emir import dspf as D
    from emir import rcc
    from emir.engine import Process
    variants, simulated = CASES[case]
    out = os.path.join(root, case)
    info = build(out, simulated)
    base = {"N": MESH["n"], "devices": info["devices"], "grid_nodes": info["grid_nodes"],
            "r_seg_ohm": info["r_seg"], "decaps": MESH["decaps"],
            "c_decap_pF": MESH["c_decap_F"] * 1e12, "esr_ohm": MESH["esr_ohm"],
            "strap_ohm": decap_mesh.STRAP_R, "load_1min": os.getloadavg()[0]}
    base["rcc_sim_s"] = decap_sweep.xyce(os.path.join(out, "rcc.cir"))
    loops = {}
    for v in variants:
        print(f"[decap_loop] {case} {v}", file=sys.stderr)
        loops[v] = run_variant(out, v)
    # RCC over the window every loop chose at pass 0 (the open loop: the
    # same split testbench at 0 V in every variant)
    window = loops[variants[0]]["kept"][0][1]["window"]
    d = D.parse(os.path.join(out, "grid_decap.dspf"))
    proc = Process.load(os.path.join(out, "process.yaml"))
    ref = rcc.rcc_check(d, "VDD", proc, os.path.join(out, "rcc.raw"), "VDD",
                        period=mesh.PERIOD, window_sel=window, dynamic=False, log=quiet)
    devs = selfconsist.rcc_devices(d, "VDD", os.path.join(out, "rcc.raw"), window)
    base["droop_pct"] = 100 * float(ref["ir_drop"]) / mesh.VDD
    rows = []
    for v in variants:
        rows += [{**base, **r} for r in variant_rows(case, v, loops[v], ref, devs, window)]
    p = os.path.join(out, "rcc.raw")
    if os.path.exists(p):
        os.remove(p)
    return rows


def pct(v):
    return "" if v is None else f"{100 * v:+.2f}%"


def rng(row, key):
    lo, hi = row.get(f"{key}_min"), row.get(f"{key}_max")
    return "" if lo is None else f"{pct(lo)} .. {pct(hi)}"


def markdown(rows: list) -> str:
    r0 = rows[0]
    L = [f"# The self-consistent loop with decaps against RCC ({LABEL})", "",
         "Generated by `examples/13_decap_aware/decap_loop.py`. Example 06's high-droop mesh "
         f"({r0['N']} x {r0['N']}, {r0['r_seg_ohm']:g} ohm segments, 2 fF per node, "
         f"{r0['devices']} level-1 CMOS inverters at 1 GHz: {r0['droop_pct']:.2f} % droop "
         f"in RCC) with {r0['decaps']} MOS decaps of {r0['c_decap_pF']:g} pF behind "
         f"{r0['esr_ohm']:g} ohm, each on a {r0['strap_ohm']:g} ohm strap. The loop as `emir "
         "run --mode cc-grid --self-consistent --sim xyce --dynamic` runs it, on the RC DSPF "
         "with the decaps: `sized`, the process file's cap_models size the decaps and the "
         "loop feeds them back; `unsized`, the decap model's entry left empty, so they are "
         "left out of the droop fed back (EMIR-W547) and of the decap-aware EM (EMIR-W409). "
         "`hd16b_dec_sim`: decap 0 a MOS of the testbench (the same 5 pF), its gate fed by "
         "the split and its current simulated, not fed back (`sized_16`: at most 16 "
         "passes, not 10). Every pass (0 is the open "
         "loop) scored against RCC over the same window, branch by branch, on each EM "
         "basis: `free` the R-only grid, `aware` through the RC grid (decap-aware), `worst` "
         "per segment the larger. Relative error, + is conservative. Classes by the RCC "
         "value of the metric: major >= 50 % of the largest branch, secondary 10-50 %, "
         "minor 1-10 % (straps apart). Devices: each device's own current against RCC's "
         "through its contact (a simulated decap apart). Not a real design, not Spectre. "
         "Screening, not sign-off.", "",
         "## How each loop ended", "",
         "| case | variant | stopped | reported pass | simulations | decaps fed back | not "
         "sized | simulated | codes | loop s | RCC sim s |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for key in dict.fromkeys((r["case"], r["variant"]) for r in rows):
        r = next(x for x in rows if (x["case"], x["variant"]) == key)
        L.append(f"| {key[0]} | {key[1]} | {r['stopped']} | {r['reported_pass']} | "
                 f"{r['n_sims']} | {r['feedback_decaps']} ({r['feedback_decaps_pF']:.4g} pF) "
                 f"| {r['unsized_decaps']} | {r['simulated_decaps']} | {r['codes']} | "
                 f"{r['loop_wall_s']:.1f} | {r['rcc_sim_s']:.1f} |")
    L += ["", "## Governing branch, straps and devices, by pass", "",
          "| case | variant | pass | basis | avg gov | rms gov | peak gov | straps rms | "
          "straps peak | minor peak | devices peak (median; min .. max) | simulated decap "
          "rms / peak |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        for b in BASES:
            L.append(f"| {r['case']} | {r['variant']}{' (reported)' if r['reported'] else ''} "
                     f"| {r['pass']} | {b} | {pct(r.get(f'{b}_avg_gov'))} | "
                     f"{pct(r.get(f'{b}_rms_gov'))} | {pct(r.get(f'{b}_peak_gov'))} | "
                     f"{rng(r, f'{b}_rms_strap')} | {rng(r, f'{b}_peak_strap')} | "
                     f"{rng(r, f'{b}_peak_minor')} | {pct(r.get('dev_peak_med'))}; "
                     f"{rng(r, 'dev_peak')} | "
                     + (f"{pct(r['simdec_rms'])} / {pct(r['simdec_peak'])}"
                        if r.get("simdec_rms") is not None else "") + " |")
    L += ["", "## Every class at the reported pass (min .. max)", "",
          "| case | variant | metric | basis | major | secondary | minor | straps |",
          "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        if not r["reported"]:
            continue
        for m in METRICS:
            for b in BASES:
                L.append(f"| {r['case']} | {r['variant']} | {m} | {b} | "
                         f"{rng(r, f'{b}_{m}_major')} | {rng(r, f'{b}_{m}_secondary')} | "
                         f"{rng(r, f'{b}_{m}_minor')} | {rng(r, f'{b}_{m}_strap')} |")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], allow_abbrev=False)
    ap.add_argument("--cases", default="all",
                    help="comma-separated case names, or all (" + ", ".join(CASES) + ")")
    ap.add_argument("--out", required=True)
    ap.add_argument("--json", default=None)
    ap.add_argument("--csv", default=None)
    ap.add_argument("--md", default=None)
    a = ap.parse_args(argv)
    names = list(CASES) if a.cases == "all" else a.cases.split(",")
    rows = []
    for c in names:
        rows += run_case(a.out, c)
    text = json.dumps({"label": LABEL, "rows": rows}, indent=1, default=float)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    elif not (a.csv or a.md):
        print(text)
    if a.csv:
        keys = []
        for r in rows:
            keys += [k for k in r if k not in keys]
        with open(a.csv, "w", encoding="utf-8", newline="") as f:
            f.write(f"# {LABEL}. Generated by examples/13_decap_aware/decap_loop.py. Errors "
                    "relative to RCC (+ is conservative); times in s (wall)\n")
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k) for k in keys})
    if a.md:
        with open(a.md, "w", encoding="utf-8") as f:
            f.write(markdown(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
