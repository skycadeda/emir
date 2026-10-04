"""Decap-aware EM against RCC on Xyce: decap-free and decap-aware, branch by
branch. SYNTHETIC (invented meshes, devices and decaps), Xyce, serial.

    python decap_sweep.py --cases n20_2ohm --out out/sweep --json out/n20.json
    python decap_sweep.py --cases all --out out/sweep_full \\
        --csv results/decap.csv --md results/decap.md --json results/decap.json

For each case (`CASES`: mesh size, decaps, their C and ESR; `two_rail`: a
mirrored resistive VSS mesh with the decaps between the meshes) it runs, one
simulator at a time:

* Xyce on the CC deck (devices on ideal rails) and on the RCC deck (the RC
  mesh with the decaps, every branch current saved), then in-process emir:
  `cc_taps` of the CC result over its settled whole periods, `check_net` (the
  decap-free currents, exact on the simulator's samples) and the dynamic solve
  (`bigblock.cc_dynamic`, decap-aware), and `rcc_check` of the RCC result
  over the same window. Per metric, the error of each basis against RCC on
  the governing branch, on each class of branch (major >= 50 % of the
  largest RCC value of that metric, secondary 10-50 %, minor 1-10 %) and on
  the decaps' straps; the decaps' own currents; the RCC result's own
  periodicity (rms of its last period against the one before, every branch).
* the solver-only reference (`decap_mesh.ref_deck`): Xyce on the same RC
  network with decaps driven by the CC pin waveforms, the window repeated
  before itself; the decap-aware currents against it, every branch class
  (what the solver alone contributes, without the open loop).

Writes one JSON document, and optionally a CSV row per case and a Markdown
table. Every number in docs/big-blocks.md "Decap-aware EM" comes from
`results/` written by this script.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import decap_mesh  # noqa: E402
import mesh  # noqa: E402  (from ../06_big_block, via decap_mesh)

LABEL = "Xyce, synthetic, serial"
CLASSES = (("major", 0.5, None), ("secondary", 0.1, 0.5), ("minor", 0.01, 0.1))
#: case -> (N, decaps, C (F), ESR (ohm), two rails)
CASES = {
    "n20_none": (20, 0, 0.0, 2.0, False),
    "n20_2ohm": (20, 8, 5e-12, 2.0, False),
    "n40_none": (40, 0, 0.0, 2.0, False),
    "n40_2ohm": (40, 16, 5e-12, 2.0, False),
    "n40_20ohm": (40, 16, 5e-12, 20.0, False),
    "n70_none": (70, 0, 0.0, 2.0, False),
    "n70_2ohm": (70, 32, 5e-12, 2.0, False),
    "n70_20ohm": (70, 32, 5e-12, 20.0, False),
    "two_rail": (20, 8, 5e-12, 2.0, True),
}
K = 32                  # devices, as example 06
#: the solver-only reference runs up to this mesh size (its result holds every
#: branch at every breakpoint of 32 PWL sources: 1.3 GB at N = 40)
REF_MAX_N = 20
MAX_SIMS = 12           # the machine's simulation limit (ours included)


def quiet(*_a, **_k):
    pass


def sims_running() -> int:
    p = subprocess.run(["pgrep", "-c", "-f", "Xyce|ngspice|spectre|hspice"],
                       capture_output=True, text=True)
    return int((p.stdout.strip() or "0").splitlines()[0])


def xyce(cir: str) -> float:
    """Xyce on `cir` in its directory, after the machine's simulation count
    leaves room for one more; wall seconds."""
    while sims_running() + 1 > MAX_SIMS:
        time.sleep(10)
    t0 = time.time()
    p = subprocess.run([shutil.which("Xyce") or "Xyce", "-a", os.path.basename(cir)],
                       cwd=os.path.dirname(cir), capture_output=True, text=True)
    if p.returncode:
        raise SystemExit(f"Xyce failed on {cir}:\n{p.stdout[-3000:]}\n{p.stderr[-2000:]}")
    return time.time() - t0


def _classes(a, b, prefix: str, out: dict, straps=None):
    """Errors of `a` against `b` (per branch): the governing branch (the
    largest of b), each class by b's size, and the straps."""
    g = int(np.argmax(b))
    out[f"{prefix}_gov"] = float((a[g] - b[g]) / b[g])
    for name, lo, hi in CLASSES:
        sel = (b >= lo * b.max()) & ((b < hi * b.max()) if hi else True)
        if straps is not None:
            sel &= ~straps
        e = (a[sel] - b[sel]) / b[sel]
        out[f"{prefix}_{name}_n"] = int(sel.sum())
        out[f"{prefix}_{name}_min"] = float(e.min()) if sel.any() else None
        out[f"{prefix}_{name}_max"] = float(e.max()) if sel.any() else None
    if straps is not None and straps.any():
        s = straps & (b > 0)
        e = (a[s] - b[s]) / b[s]
        out[f"{prefix}_strap_n"] = int(s.sum())
        out[f"{prefix}_strap_min"] = float(e.min()) if s.any() else None
        out[f"{prefix}_strap_max"] = float(e.max()) if s.any() else None


def _periodicity(raw: str, names: list, period: float) -> float:
    """The RCC result's own steady state: over every branch above 1 % of the
    largest, the largest relative change of the rms from the period before
    the last to the last."""
    from emir import waves
    from emir.ccsim import clip_window, trapz_weights
    w = waves.read(raw)
    m = w.match([f"{n}:1" for n in names])
    Y = np.array([w[m[f"{n}:1"]] for n in names if f"{n}:1" in m])
    t1 = float(w.x[-1])
    r = []
    for k in (1, 2):
        tt, YY = clip_window(w.x, Y, t1 - k * period, t1 - (k - 1) * period)
        wt = trapz_weights(tt) / (tt[-1] - tt[0])
        r.append(np.sqrt((YY * YY) @ wt))
    big = r[0] > 0.01 * r[0].max()
    return float(np.max(np.abs(r[0][big] - r[1][big]) / r[0][big]))


def _branch_metrics(raw: str, names: list, t0: float, t1: float) -> dict:
    """avg / rms / peak of every branch current in `raw` over [t0, t1] (the
    simulator's own samples, trapezoid weights)."""
    from emir import waves
    from emir.ccsim import clip_window, trapz_weights
    w = waves.read(raw)
    m = w.match([f"{n}:1" for n in names])
    Y = np.array([w[m[f"{n}:1"]] for n in names])
    tt, YY = clip_window(w.x, Y, t0, t1)
    wt = trapz_weights(tt) / (tt[-1] - tt[0])
    return {"avg": np.abs(YY @ wt), "rms": np.sqrt((YY * YY) @ wt),
            "peak": np.abs(YY).max(axis=1)}


def one_net(out: str, dspf: str, net: str, cc_raw: str, rcc_raw: str, row: dict,
            prefix: str = "", ref: bool = True, info: dict | None = None) -> None:
    import argparse as _a

    from emir import ccsim, rcc
    from emir import dspf as D
    from emir.bigblock import cc_dynamic
    from emir.engine import Process
    proc = Process.load(os.path.join(out, "process.yaml"))
    from emir.rcc import net_port
    d = D.parse(dspf)
    feed = net_port(d, net)
    ing = ccsim.cc_taps(d, net, cc_raw, period=mesh.PERIOD, feed=feed, log=quiet)
    inj = {}
    for t in ing["taps"]:
        inj[t["node"]] = inj.get(t["node"], 0.0) + t["i_avg"]
    r = D.check_net(d, net, proc, inj, feed, taps_list=ing["taps"],
                    tap_weights=ing["weights"], log=quiet)
    a = _a.Namespace(em_basis="decap-aware", dynamic=True, dyn_steps=None, argv=None,
                     max_memory=None, superposition="auto", power_nets=None, mode="cc-grid")
    t0 = time.time()
    dyn = cc_dynamic(d, net, r, feed, ing["taps"], ing["time"], proc, a, log=quiet)
    row[f"{prefix}emir_dynamic_s"] = time.time() - t0
    em = r.get("dynamic_em")
    if em is None:
        raise SystemExit(f"{out} {net}: decap-aware EM not computed: {r['em_basis']}")
    nw = r["network"]
    names = [x["name"] for x in nw.res]
    free, aware = r["em_free"]["Ie"], r["Ie"]
    rr = rcc.rcc_check(d, net, proc, rcc_raw, feed, period=mesh.PERIOD,
                       window_sel=ing["window"], dynamic=False, log=quiet)
    ri = {x["name"]: i for i, x in enumerate(rr["network"].res)}
    idx = np.array([ri[n] for n in names])
    straps = np.array([n.startswith(("Rsd", "RsV", "RsG")) for n in names])
    for m in ("avg", "rms", "peak"):
        b = rr["Ie"][m][idx]
        _classes(free[m], b, f"{prefix}free_{m}", row, straps)
        _classes(aware[m], b, f"{prefix}aware_{m}", row, straps)
    num = em["numerics"]
    row.update({f"{prefix}n_steps": num["n_steps"], f"{prefix}passes": num["passes"],
                f"{prefix}sub_intervals": num["sub_steps"]["intervals"],
                f"{prefix}sub_added": num["sub_steps"]["added"],
                f"{prefix}avg_identity": num["avg_identity_residual"],
                f"{prefix}avg_check": num["avg_check"]["max_rel"],
                f"{prefix}corr_rms_max": num["correction_max"]["rms"],
                f"{prefix}orig_points": len(ing["time"]),
                f"{prefix}window_s": f"{ing['window']['t0']:.4g}..{ing['window']['t1']:.4g}",
                f"{prefix}droop_mV": dyn["worst_V"] * 1e3,
                f"{prefix}verdict_free": em["bases"]["decap-free"]["verdict"],
                f"{prefix}verdict_aware": em["bases"]["decap-aware"]["verdict"],
                f"{prefix}verdict_worst": em["bases"]["worst"]["verdict"],
                f"{prefix}max_free": em["bases"]["decap-free"]["max_ratio"]["all"],
                f"{prefix}max_aware": em["bases"]["decap-aware"]["max_ratio"]["all"],
                f"{prefix}max_worst": em["bases"]["worst"]["max_ratio"]["all"]})
    row[f"{prefix}rcc_periodicity"] = _periodicity(rcc_raw, names, mesh.PERIOD)
    # every decap's own current against RCC's (single rail: the ESR branch),
    # from the solve's own arrays (the summary lists only the 20 largest)
    run_em = (dyn or {}).get("em") or {}
    lu = run_em.get("lump") or {}
    dec = [(L["name"], float(lu["rms"][k]), float(lu["peak"][k]))
           for k, L in enumerate(run_em.get("lumps") or [])
           if str(L.get("name", "")).startswith("MDC")]
    if dec and not prefix:
        rd = _branch_metrics(rcc_raw, [f"Rdc{x[0][3:]}" for x in dec],
                             ing["window"]["t0"], ing["window"]["t1"])
        e = [x[1] / v - 1 for x, v in zip(dec, rd["rms"])]
        ep = [x[2] / v - 1 for x, v in zip(dec, rd["peak"])]
        row.update(decap_n=len(dec), decap_rms_min=min(e), decap_rms_max=max(e),
                   decap_peak_min=min(ep), decap_peak_max=max(ep))
    if ref and info is not None and info["n"] <= REF_MAX_N:
        tw = ing["window"]["t1"] - ing["window"]["t0"]
        decap_mesh.ref_deck(out, ing["taps"], {**ing["window"], "t": ing["time"]},
                            info["n"], info.get("sites", []), info["c_decap_F"],
                            info["r_esr_ohm"])
        row["ref_sim_s"] = xyce(os.path.join(out, "ref.cir"))
        rf = _branch_metrics(os.path.join(out, "ref.raw"), names, tw, 2 * tw)
        for m in ("avg", "rms", "peak"):
            _classes(aware[m], rf[m], f"solver_{m}", row, straps)


def run_case(name: str, root: str) -> dict:
    n, ndec, cdec, resr, two = CASES[name]
    out = os.path.join(root, name)
    row = {"case": name, "N": n, "devices": K, "decaps": ndec, "c_decap_pF": cdec * 1e12,
           "esr_ohm": resr, "strap_ohm": decap_mesh.STRAP_R, "two_rail": two,
           "load_1min": os.getloadavg()[0]}
    if two:
        info = decap_mesh.two_rail_case(n, K, ndec, cdec, resr, out)
        dspf = os.path.join(out, "grid_two.dspf")
    else:
        info = decap_mesh.decap_case(n, K, ndec, cdec, resr, out)
        dspf = os.path.join(out, "grid_decap.dspf")
    row["cc_sim_s"] = xyce(os.path.join(out, "cc.cir"))
    row["rcc_sim_s"] = xyce(os.path.join(out, "rcc.cir"))
    cc_raw, rcc_raw = os.path.join(out, "cc.raw"), os.path.join(out, "rcc.raw")
    if two:
        one_net(out, dspf, "VDD", cc_raw, rcc_raw, row, prefix="vdd_", ref=False)
        one_net(out, dspf, "VSS", cc_raw, rcc_raw, row, prefix="vss_", ref=False)
    else:
        one_net(out, dspf, "VDD", cc_raw, rcc_raw, row, info=info)
    for f in ("rcc.raw", "ref.raw"):
        p = os.path.join(out, f)
        if os.path.exists(p):
            os.remove(p)
    return row


def pct(v):
    return "" if v is None else f"{100 * v:+.2f}%"


def rng(row, key):
    lo, hi = row.get(f"{key}_min"), row.get(f"{key}_max")
    return "" if lo is None else f"{pct(lo)} .. {pct(hi)}"


def markdown(rows: list) -> str:
    L = [f"# Decap-aware EM against RCC ({LABEL})", "",
         "Generated by `examples/13_decap_aware/decap_sweep.py`. Example 06's invented "
         "N x N mesh (0.25 ohm segments, 2 fF per node) with 32 level-1 CMOS inverters at "
         "1 GHz, plus decaps at fixed random mesh nodes, each a MOS decap on a 0.5 ohm "
         "strap: in the RCC deck its ESR and capacitor (linear), in the DSPF a MOS device "
         "the process file's cap_models size to the same C and ESR. Over the CC window "
         "(whole periods), each basis's branch current against RCC's, relative, + is "
         "conservative. Classes by the RCC value of that metric: major >= 50 % of the "
         "largest branch, secondary 10-50 %, minor 1-10 % (straps apart). `solver`: the "
         "decap-aware currents against Xyce on the same RC network driven by the same CC "
         "pin currents (no open loop). Not a real design, not Spectre. Screening, not "
         "sign-off.", ""]
    L += ["## Governing branch and straps", "",
          "| case | N | decaps | metric | decap-free gov | decap-aware gov | decap-free straps "
          "| decap-aware straps |", "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        for p in ([""] if not r["two_rail"] else ["vdd_", "vss_"]):
            for m in ("rms", "peak"):
                L.append(f"| {r['case']}{' ' + p[:-1].upper() if p else ''} | {r['N']} | "
                         f"{r['decaps']} | {m} | {pct(r.get(p + 'free_' + m + '_gov'))} | "
                         f"{pct(r.get(p + 'aware_' + m + '_gov'))} | "
                         f"{rng(r, p + 'free_' + m + '_strap')} | "
                         f"{rng(r, p + 'aware_' + m + '_strap')} |")
    L += ["", "## Every class (min .. max)", "",
          "| case | metric | basis | major | secondary | minor |", "|---|---|---|---|---|---|"]
    for r in rows:
        for p in ([""] if not r["two_rail"] else ["vdd_", "vss_"]):
            for m in ("avg", "rms", "peak"):
                for b in ("free", "aware"):
                    L.append(f"| {r['case']}{' ' + p[:-1].upper() if p else ''} | {m} | "
                             f"{'decap-free' if b == 'free' else 'decap-aware'} | "
                             f"{rng(r, f'{p}{b}_{m}_major')} | {rng(r, f'{p}{b}_{m}_secondary')} "
                             f"| {rng(r, f'{p}{b}_{m}_minor')} |")
    L += ["", "## The solver alone, the decaps, the numerics", "",
          "| case | solver rms (every class) | solver peak (every class) | decap rms "
          "(every decap) | decap peak (every decap) | RCC periodicity | avg identity | steps "
          "| passes | load |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        if r["two_rail"]:
            continue
        cls = lambda m: [r.get(f"solver_{m}_{c}_{e}") for c in ("major", "secondary",  # noqa: E731
                                                               "minor") for e in ("min", "max")]
        s_rms = [x for x in cls("rms") if x is not None]
        s_pk = [x for x in cls("peak") if x is not None]
        L.append(f"| {r['case']} | "
                 + (f"{pct(min(s_rms))} .. {pct(max(s_rms))}" if s_rms else "") + " | "
                 + (f"{pct(min(s_pk))} .. {pct(max(s_pk))}" if s_pk else "") + " | "
                 + f"{rng(r, 'decap_rms')} | {rng(r, 'decap_peak')} | "
                 f"{r.get('rcc_periodicity', 0):.1e} | {r.get('avg_identity', 0):.1e} | "
                 f"{r.get('n_steps')} | {r.get('passes')} | {r['load_1min']:.0f} |")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], allow_abbrev=False)
    ap.add_argument("--cases", default="n20_2ohm",
                    help="comma-separated case names, or all (" + ", ".join(CASES) + ")")
    ap.add_argument("--out", required=True)
    ap.add_argument("--json", default=None)
    ap.add_argument("--csv", default=None)
    ap.add_argument("--md", default=None)
    a = ap.parse_args(argv)
    names = list(CASES) if a.cases == "all" else a.cases.split(",")
    rows = []
    for nm in names:
        print(f"[decap_sweep] {nm}", file=sys.stderr)
        rows.append(run_case(nm, a.out))
    doc = {"label": LABEL, "rows": rows}
    text = json.dumps(doc, indent=1, default=float)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    else:
        print(text)
    if a.csv:
        keys = []
        for r in rows:
            keys += [k for k in r if k not in keys]
        with open(a.csv, "w", encoding="utf-8", newline="") as f:
            f.write(f"# {LABEL}. Generated by examples/13_decap_aware/decap_sweep.py. Errors "
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
