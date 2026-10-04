"""Streaming superposition against the dense path. No simulator needed.

    python stream_check.py --out out/stream                  # small case, seconds
    python stream_check.py --n 300 --k 2000 --nt 501 --out out/big --csv results/stream_bench.csv

Builds a synthetic mesh with periodic tap currents (`mesh.stream_case`), then:

1. in-process, the per-branch avg / rms / peak currents of the dense and the
   streamed superposition, and their largest difference relative to the
   largest branch value (the claim: <= 1e-10);
2. `emir run` three times as a user would (`--superposition dense` with a
   budget it fits, `--superposition stream`, and the default `auto`), each
   timed and with its peak memory (`measure.run`: the process's max RSS,
   single-threaded numerical libraries, the fastest of --repeat runs). These
   run first, before this script holds any big array.

Writes one JSON document (stdout, or --json FILE) and optionally appends a CSV
row. SYNTHETIC case; times are this machine's, serial Python.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import measure  # noqa: E402
import mesh  # noqa: E402

PROC = os.path.join(HERE, "process.yaml")


def emir_cmd() -> list:
    return [sys.executable, "-m", "emir"]


def in_process(case: dict, budget_dense: int) -> dict:
    """Per-branch equality of the two paths, in one process."""
    from emir import ccsim
    from emir import dspf as D
    from emir.engine import Process
    quiet = lambda *a, **k: None                              # noqa: E731
    proc = Process.load(PROC)
    d = D.parse(case["dspf"])
    ing = ccsim.cc_taps(d, "VDD", case["raw"], feed="VDD", log=quiet)
    inj = {}
    for t in ing["taps"]:
        inj[t["node"]] = inj.get(t["node"], 0.0) + t["i_avg"]
    out, res = {}, {}
    for path in ("dense", "stream"):
        t0 = time.time()
        r = D.check_net(d, "VDD", proc, inj, "VDD", taps_list=ing["taps"],
                        tap_weights=ing["weights"], log=quiet, max_bytes=budget_dense,
                        superposition=path)
        res[path] = r
        out[f"{path}_s"] = time.time() - t0
        out[f"{path}_method"] = r["superposition"]["method"]
    a, b = res["dense"]["Ie"], res["stream"]["Ie"]
    out["rel_diff"] = {m: float(np.max(np.abs(a[m] - b[m])) / max(np.max(np.abs(a[m])), 1e-30))
                       for m in ("avg", "rms", "peak")}
    out["ir_drop_mV"] = res["dense"]["ir_drop"] * 1e3
    out["max_ratio"] = float(res["dense"]["max_ratio"]["all"])
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], allow_abbrev=False)
    ap.add_argument("--n", type=int, default=40, help="mesh is n x n (default 40)")
    ap.add_argument("--k", type=int, default=100, help="taps (default 100)")
    ap.add_argument("--nt", type=int, default=301, help="time points (default 301)")
    ap.add_argument("--out", default=os.path.join(HERE, "out", "stream"))
    ap.add_argument("--dense-memory", default="64G",
                    help="--max-memory for the dense run (default 64G: big enough)")
    ap.add_argument("--no-inprocess", action="store_true",
                    help="skip the in-process per-branch comparison")
    ap.add_argument("--repeat", type=int, default=1,
                    help="run each emir command this many times, keep the fastest")
    ap.add_argument("--json", default=None, help="write the document here too")
    ap.add_argument("--csv", default=None, help="append one row to this CSV")
    a = ap.parse_args(argv)
    from emir.bigblock import parse_size
    case = mesh.stream_case(a.n, a.k, a.nt, a.out)
    doc = {"label": "synthetic mesh, synthetic periodic currents, serial Python",
           "case": {k: case[k] for k in ("tag", "nodes", "resistors", "taps", "points")},
           "dense_matrix_GiB": (case["resistors"] * (case["taps"] + 1) * 8) / 2 ** 30}
    base = [*emir_cmd(), "run", case["dspf"], "--process", PROC, "--cc-sim", case["raw"],
            "--mode", "cc-grid", "--format", "json"]
    runs = {}
    for name, extra in (("dense", ["--superposition", "dense", "--max-memory", a.dense_memory]),
                        ("stream", ["--superposition", "stream"]),
                        ("auto", [])):
        summ = os.path.join(a.out, f"{case['tag']}.{name}.json")
        m = measure.run(base + extra + ["--summary-out", summ],
                        os.path.join(a.out, f"{case['tag']}.{name}.log"), repeat=a.repeat)
        s = json.load(open(summ, encoding="utf-8"))
        net = s["nets"][0]
        m.update(method=(net.get("superposition") or {}).get("method"),
                 ir_drop_mV=net["ir_drop_mV"], max_ratio=net["max_ratio"]["all"],
                 verdict=s["verdict"])
        runs[name] = m
    doc["runs"] = runs
    if not a.no_inprocess:
        doc["inprocess"] = in_process(case, parse_size(a.dense_memory))
    d, s = runs["dense"], runs["stream"]
    doc["summary_rel_diff"] = {
        "ir_drop": abs(d["ir_drop_mV"] - s["ir_drop_mV"]) / max(abs(d["ir_drop_mV"]), 1e-30),
        "max_ratio": abs(d["max_ratio"] - s["max_ratio"]) / max(abs(d["max_ratio"]), 1e-30)}
    text = json.dumps(doc, indent=1, default=float)
    print(text)
    if a.json:
        os.makedirs(os.path.dirname(os.path.abspath(a.json)), exist_ok=True)
        with open(a.json, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    if a.csv:
        row = {"case": case["tag"], "nodes": case["nodes"], "resistors": case["resistors"],
               "taps": case["taps"], "points": case["points"],
               "dense_matrix_GiB": round(doc["dense_matrix_GiB"], 3)}
        for name in ("dense", "stream", "auto"):
            row[f"{name}_wall_s"] = round(runs[name]["wall_s"], 2)
            row[f"{name}_peak_rss_MB"] = round(runs[name]["peak_rss_MB"], 1)
        row["load_1min"] = round(max(r["load_1min"] for r in runs.values()), 1)
        row["auto_method"] = runs["auto"]["method"]
        if "inprocess" in doc:
            for m, v in doc["inprocess"]["rel_diff"].items():
                row[f"rel_diff_{m}"] = f"{v:.2e}"
        new = not os.path.exists(a.csv)
        os.makedirs(os.path.dirname(os.path.abspath(a.csv)), exist_ok=True)
        with open(a.csv, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(row))
            if new:
                w.writeheader()
            w.writerow(row)
    bad = [k for k, v in (doc.get("inprocess", {}).get("rel_diff") or {}).items() if v > 1e-10]
    bad += [k for k, v in doc["summary_rel_diff"].items() if v > 1e-10]
    bad += [n for n, r in runs.items() if r["exit"] != 0]
    if bad:
        print(f"stream_check: FAILED ({', '.join(bad)})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
