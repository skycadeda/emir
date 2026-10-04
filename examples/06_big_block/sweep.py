"""RCC against CC + emir cc-grid on Xyce, over a mesh-size sweep.
SYNTHETIC (an invented mesh and level-1 inverters), Xyce, serial.

    python sweep.py --sizes 10 --out out/sweep                     # seconds
    python sweep.py --sizes 10,20,40,70,100,140 --variants stiff,k256 \\
        --csv results/sweep.csv --md results/sweep.md              # the table

For each mesh (`mesh.xyce_case`) it runs, one simulator at a time:

* the RCC route: Xyce on the RC netlist with every branch current saved,
  then `emir run --mode rcc` (only up to --post-max; the result file is the
  problem). Two more RCC runs isolate the cost: nothing saved, and node
  voltages only.
* the CC route: Xyce on the devices with an ideal rail (the C-only CC
  simulation), then `emir run --mode cc-grid` on the R-only DSPF, and again
  with `--dynamic` on the RC DSPF.

Each run's wall time and peak memory come from `measure.run` (single-threaded
numerical libraries; each emir run twice, the faster kept; `load_1min` is the
machine's load when the simulations started, as other work shares it);
result sizes from the files. Up to --post-max it also compares, branch by branch, the
cc-grid currents with the RCC ones over the same window, by class of branch
(major: >= 50 % of the largest RCC value of that metric, secondary: 10-50 %,
minor: 1-10 %), and the IR and dynamic IR.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import measure  # noqa: E402
import mesh  # noqa: E402

PROC = os.path.join(HERE, "process.yaml")
LABEL = "Xyce, synthetic, serial"
CLASSES = (("major", 0.5, None), ("secondary", 0.1, 0.5), ("minor", 0.01, 0.1))
#: case name -> (N, mesh options): `stiff` 2.5 mohm segments; `k256` eight
#: times the devices; `contact0` and `nooverlap` probe the small average error
#: (a 1 mohm device contact; no gate-overlap capacitance)
VARIANTS = {"stiff": (40, {"r_seg": 0.0025}), "k256": (70, {"k": 256}),
            "contact0": (40, {"r_cont": 0.001}), "nooverlap": (40, {"overlap": False})}


def xyce_steps(log: str) -> tuple:
    """(successful, failed) transient steps from a Xyce log, or (None, None)."""
    text = open(log, encoding="utf-8", errors="replace").read()
    ok = re.search(r"Successful Step\s+(\d+)", text)
    bad = re.search(r"Failed Steps\s+(\d+)", text)
    return (int(ok.group(1)) if ok else None, int(bad.group(1)) if bad else None)


def xyce(cir: str) -> dict:
    d = os.path.dirname(cir)
    log = cir + ".log"
    m = measure.run([shutil.which("Xyce") or "Xyce", "-a", os.path.basename(cir)], log, cwd=d)
    if m["exit"] != 0:
        raise SystemExit(f"Xyce failed on {cir}; see {log}")
    m["steps"], m["failed_steps"] = xyce_steps(log)
    return m


def emir_run(args: list, log: str, summary: str) -> dict:
    m = measure.run([sys.executable, "-m", "emir", "run", *args, "--format", "json",
                     "--summary-out", summary], log, repeat=2)
    if m["exit"] not in (0, 1):
        raise SystemExit(f"emir run failed (exit {m['exit']}); see {log}")
    m["summary"] = json.load(open(summary, encoding="utf-8"))
    return m


def mb(path: str) -> float:
    return os.path.getsize(path) / 2 ** 20


def accuracy(out: str) -> dict:
    """cc-grid against RCC, branch by branch, over the CC window."""
    from emir import ccsim, rcc
    from emir import dspf as D
    from emir.dynir import dynamic_ir
    from emir.engine import Process
    quiet = lambda *a, **k: None                              # noqa: E731
    proc = Process.load(PROC)
    d = D.parse(os.path.join(out, "grid_rc.dspf"))
    ing = ccsim.cc_taps(d, "VDD", os.path.join(out, "cc.raw"), period=mesh.PERIOD,
                        feed="VDD", log=quiet)
    inj = {}
    for t in ing["taps"]:
        inj[t["node"]] = inj.get(t["node"], 0.0) + t["i_avg"]
    cc = D.check_net(d, "VDD", proc, inj, "VDD", taps_list=ing["taps"],
                     tap_weights=ing["weights"], log=quiet)
    rr = rcc.rcc_check(d, "VDD", proc, os.path.join(out, "rcc.raw"), "VDD",
                       period=mesh.PERIOD, window_sel=ing["window"], dynamic=True, log=quiet)
    names = [r["name"] for r in cc["network"].res]
    ri = {r["name"]: i for i, r in enumerate(rr["network"].res)}
    idx = np.array([ri[n] for n in names])
    acc = {}
    for m in ("avg", "rms", "peak"):
        a, b = cc["Ie"][m], rr["Ie"][m][idx]
        g = int(np.argmax(b))
        acc[f"{m}_gov"] = float((a[g] - b[g]) / b[g])
        for name, lo, hi in CLASSES:
            sel = (b >= lo * b.max()) & ((b < hi * b.max()) if hi else True)
            e = (a[sel] - b[sel]) / b[sel]
            acc[f"{m}_{name}_n"] = int(sel.sum())
            acc[f"{m}_{name}_min"] = float(e.min()) if sel.any() else None
            acc[f"{m}_{name}_max"] = float(e.max()) if sel.any() else None
    acc["ir_cc_mV"] = cc["ir_drop"] * 1e3
    acc["ir_rcc_mV"] = rr["ir_drop"] * 1e3
    acc["ir_err"] = (cc["ir_drop"] - rr["ir_drop"]) / rr["ir_drop"]
    dy = dynamic_ir(d, "VDD", cc["network"], "VDD", ing["taps"], ing["time"], proc=proc,
                    periodic=True, log=quiet)
    acc["dyn_cc_mV"] = dy["worst_V"] * 1e3
    acc["dyn_rcc_mV"] = rr["dynamic"]["worst_V"] * 1e3
    acc["dyn_err"] = (dy["worst_V"] - rr["dynamic"]["worst_V"]) / rr["dynamic"]["worst_V"]
    acc["window_s"] = [ing["window"]["t0"], ing["window"]["t1"]]
    return acc


def one(case: str, n: int, k: int, opts: dict, root: str, post_max: int, keep: bool) -> dict:
    out = os.path.join(root, f"{case}_n{n}")
    info = mesh.xyce_case(n, k, out, **opts)
    row = {"case": case, "N": n, "grid_nodes": info["grid_nodes"],
           "resistors": info["resistors"], "devices": info["devices"],
           "r_seg_ohm": info["r_seg"], "r_cont_ohm": info["r_cont"],
           "overlap_caps": info["overlap"]}
    p = lambda f: os.path.join(out, f)                         # noqa: E731
    # ---- the CC route
    cs = xyce(p("cc.cir"))
    row.update(cc_sim_s=cs["wall_s"], cc_steps=cs["steps"], cc_sim_rss_MB=cs["peak_rss_MB"],
               cc_raw_MB=mb(p("cc.raw")))
    cp = emir_run([p("grid_r.dspf"), "--process", PROC, "--cc-sim", p("cc.raw"),
                   "--mode", "cc-grid", "--period", mesh.PERIOD], p("cc_post.log"),
                  p("cc_post.json"))
    row.update(cc_post_s=cp["wall_s"], cc_post_rss_MB=cp["peak_rss_MB"],
               cc_ir_mV=cp["summary"]["nets"][0]["ir_drop_mV"],
               cc_method=(cp["summary"]["nets"][0]["superposition"] or {}).get("method"))
    cd = emir_run([p("grid_rc.dspf"), "--process", PROC, "--cc-sim", p("cc.raw"),
                   "--mode", "cc-grid", "--period", mesh.PERIOD, "--dynamic"],
                  p("cc_dyn.log"), p("cc_dyn.json"))
    row.update(cc_dyn_post_s=cd["wall_s"],
               cc_dyn_mV=cd["summary"]["nets"][0]["dynamic_mV"])
    # ---- the RCC route
    rs = xyce(p("rcc.cir"))
    row["load_1min"] = max(cs["load_1min"], rs["load_1min"])
    row.update(rcc_sim_s=rs["wall_s"], rcc_steps=rs["steps"],
               rcc_failed_steps=rs["failed_steps"], rcc_sim_rss_MB=rs["peak_rss_MB"],
               rcc_raw_MB=mb(p("rcc.raw")))
    row["rcc_nosave_sim_s"] = xyce(p("rcc0.cir"))["wall_s"]
    rv = xyce(p("rccv.cir"))
    row.update(rcc_vonly_sim_s=rv["wall_s"], rcc_vonly_raw_MB=mb(p("rccv.raw")))
    if n <= post_max:
        rp = emir_run([p("grid_r.dspf"), "--process", PROC, "--cc-sim", p("rcc.raw"),
                       "--mode", "rcc", "--period", mesh.PERIOD], p("rcc_post.log"),
                      p("rcc_post.json"))
        row.update(rcc_post_s=rp["wall_s"], rcc_post_rss_MB=rp["peak_rss_MB"],
                   rcc_ir_mV=rp["summary"]["nets"][0]["ir_drop_mV"],
                   rcc_dyn_mV=rp["summary"]["nets"][0]["dynamic_mV"])
        row.update(accuracy(out))
        row["speedup_route"] = ((row["rcc_sim_s"] + row["rcc_post_s"])
                                / (row["cc_sim_s"] + row["cc_post_s"]))
    row["speedup_sim"] = row["rcc_sim_s"] / row["cc_sim_s"]
    row["speedup_sim_vs_cc_route"] = row["rcc_sim_s"] / (row["cc_sim_s"] + row["cc_post_s"])
    if not keep:
        for f in ("rcc.raw", "rccv.raw", "rcc0.raw"):
            if os.path.exists(p(f)):
                os.remove(p(f))
    return row


def fmt(v, nd=3):
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.{nd}g}" if abs(v) < 1e3 else f"{v:.0f}"
    return str(v)


def pct(v):
    return "" if v is None else f"{100 * v:+.2f}%"


def markdown(rows: list) -> str:
    L = [f"# RCC against CC + cc-grid ({LABEL})", "",
         "Generated by `examples/06_big_block/sweep.py`. An invented N x N mesh "
         "(0.25 ohm segments, 2 fF per node) fed at one corner, with level-1 CMOS "
         "inverters switching at 1 GHz in eight phases, 4 ns simulated. Not a real "
         "design, not Spectre: an illustration of how the two routes scale. Times are "
         "wall seconds on a shared machine with 24 hardware threads, single-threaded; `load` is its "
         "1-minute load average when the row's simulations started (other work ran "
         "too), so read ratios, not absolute times.", "",
         "## Runtime, memory, result size", "",
         "| case | N | nodes | R | RCC sim s | steps (failed) | RCC raw MiB | RCC no-save s "
         "| RCC V-only s | emir rcc s | CC sim s | CC raw MiB | emir cc-grid s | "
         "RCC sim peak MiB | cc-grid peak MiB | RCC route / CC route | load |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        ratio = (f"{r['speedup_route']:.1f}x" if r.get("speedup_route")
                 else f"{r['speedup_sim_vs_cc_route']:.1f}x (sim only)")
        L.append(f"| {r['case']} | {r['N']} | {r['grid_nodes']} | {r['resistors']} | "
                 f"{fmt(r['rcc_sim_s'])} | {r['rcc_steps']} ({r['rcc_failed_steps']}) | "
                 f"{fmt(r['rcc_raw_MB'])} | {fmt(r['rcc_nosave_sim_s'])} | "
                 f"{fmt(r['rcc_vonly_sim_s'])} | {fmt(r.get('rcc_post_s'))} | "
                 f"{fmt(r['cc_sim_s'])} | {fmt(r['cc_raw_MB'])} | {fmt(r['cc_post_s'])} | "
                 f"{fmt(r['rcc_sim_rss_MB'])} | {fmt(r['cc_post_rss_MB'])} | {ratio} | "
                 f"{r['load_1min']:.0f} |")
    acc = [r for r in rows if r.get("avg_gov") is not None]
    if acc:
        L += ["", "## Accuracy of cc-grid against RCC (relative error, + is conservative)", "",
              "Over the same window. Classes by the RCC value of that metric: major >= 50 % "
              "of the largest branch, secondary 10-50 %, minor 1-10 %. Each cell is the "
              "range (min .. max) over the branches of the class.", "",
              "| case | N | metric | governing | major | secondary | minor |",
              "|---|---|---|---|---|---|---|"]
        for r in acc:
            for m in ("avg", "rms", "peak"):
                cells = [f"{pct(r[f'{m}_{c}_min'])} .. {pct(r[f'{m}_{c}_max'])}"
                         if r.get(f"{m}_{c}_n") else "-" for c, _, _ in CLASSES]
                L.append(f"| {r['case']} | {r['N']} | {m} | {pct(r[f'{m}_gov'])} | "
                         + " | ".join(cells) + " |")
        L += ["", "| case | N | IR cc-grid mV | IR RCC mV | IR error | dynamic cc-grid mV "
              "| dynamic RCC mV | dynamic error |", "|---|---|---|---|---|---|---|---|"]
        for r in acc:
            L.append(f"| {r['case']} | {r['N']} | {r['ir_cc_mV']:.4f} | {r['ir_rcc_mV']:.4f} | "
                     f"{pct(r['ir_err'])} | {r['dyn_cc_mV']:.4f} | {r['dyn_rcc_mV']:.4f} | "
                     f"{pct(r['dyn_err'])} |")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], allow_abbrev=False)
    ap.add_argument("--sizes", default="10", help="mesh sizes N, comma-separated")
    ap.add_argument("--k", type=int, default=32, help="inverters (default 32)")
    ap.add_argument("--variants", default="",
                    help="extra rows: stiff (2.5 mohm segments, N=40), k256 (256 "
                         "inverters, N=70), contact0 (1 mohm device contacts, N=40), "
                         "nooverlap (no gate-overlap capacitance, N=40)")
    ap.add_argument("--include", action="append", default=[], metavar="JSON",
                    help="rows of an earlier run (its --json) to put first in the outputs")
    ap.add_argument("--post-max", type=int, default=70,
                    help="largest N whose RCC result emir reads (default 70)")
    ap.add_argument("--out", default=os.path.join(HERE, "out", "sweep"))
    ap.add_argument("--keep", action="store_true", help="keep the RCC result files")
    ap.add_argument("--csv", default=None)
    ap.add_argument("--md", default=None)
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)
    plan = [(f"k{a.k}", int(s), a.k, {}) for s in a.sizes.split(",") if s]
    for v in [x for x in a.variants.split(",") if x]:
        n, opts = VARIANTS[v]
        opts = dict(opts)
        plan.append((v, n, opts.pop("k", a.k), opts))
    if plan and not shutil.which("Xyce"):
        print("sweep: Xyce is not on PATH; nothing to run", file=sys.stderr)
        return 3
    rows = []
    for path in a.include:
        rows += json.load(open(path, encoding="utf-8"))["rows"]
    for case, n, k, opts in plan:
        print(f"[sweep] {case} N={n} ...", file=sys.stderr, flush=True)
        rows.append(one(case, n, k, opts, a.out, a.post_max, a.keep))
        print(json.dumps(rows[-1], default=float), flush=True)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"label": LABEL, "rows": rows}, f, indent=1, default=float)
    if a.csv:
        os.makedirs(os.path.dirname(os.path.abspath(a.csv)), exist_ok=True)
        keys = []
        for r in rows:
            keys += [k for k in r if k not in keys and k != "window_s"]
        with open(a.csv, "w", newline="", encoding="utf-8") as f:
            f.write(f"# {LABEL}. Generated by examples/06_big_block/sweep.py. Times in s "
                    f"(wall), sizes and peak memory (_MB) in MiB; errors relative, + is "
                    f"conservative\n")
            w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow({k: (f"{v:.6g}" if isinstance(v, float) else v)
                            for k, v in r.items()})
    if a.md:
        with open(a.md, "w", encoding="utf-8") as f:
            f.write(markdown(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
