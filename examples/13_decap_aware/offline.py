"""Decap-aware EM offline: no simulator. SYNTHETIC.

    python offline.py --out out/small --json out/small.json

A small decap mesh (`decap_mesh.small_case`: 20 pins drawing 10 mA spikes,
4 decaps of 20 pF / 1 ohm on thin straps) screened with `emir run --dynamic`
three times, once per EM basis, as a user would, and planned after with
`emir next --run-summary`. One JSON document (stdout or --json): per basis
the verdict, worst ratio, governing metric and worst segment, the feed
current in both bases, the numerics, the codes each run raised, and the
plan's first step. `run.sh` compares it with `expected/decap_small.json`.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import decap_mesh  # noqa: E402

BASES = ("worst", "decap-free", "decap-aware")


def emir(*args) -> tuple:
    p = subprocess.run([sys.executable, "-m", "emir", *map(str, args), "--format", "json"],
                       capture_output=True, text=True)
    return p.returncode, json.loads(p.stdout)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], allow_abbrev=False)
    ap.add_argument("--out", required=True)
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)
    case = decap_mesh.small_case(a.out)
    doc = {"label": "examples/13_decap_aware offline: a synthetic 12 x 12 mesh, 20 pins, "
                    "4 decaps of 20 pF / 1 ohm on 0.2 um straps; no simulator",
           "runs": {}}
    for b in BASES:
        summary = os.path.join(a.out, f"summary_{b}.json")
        args = [case["dspf"], "--process", case["process"], "--cc-sim", case["raw"],
                "--net", "VDD", "--mode", "cc-grid", "--period", case["period"], "--dynamic",
                "--summary-out", summary]
        if b != "worst":
            args += ["--em-basis", b]
        code, s = emir("run", *args)
        n = s["nets"][0]
        em = n["dynamic_em"]
        doc["runs"][b] = {
            "exit": code, "verdict": s["verdict"], "em_basis": n["em_basis"],
            "worst": n["worst"], "max_ratio": n["max_ratio"]["all"],
            "bases": {k: {"verdict": v["verdict"], "max_ratio": v["max_ratio"]["all"],
                          "governing_metric": v["governing_metric"],
                          "worst": (v["worst"] or {}).get("name"),
                          "n_violations": v["n_violations"]}
                      for k, v in em["bases"].items()},
            "feed": em["feed"], "decaps": em["decaps"]["n"],
            "numerics": {k: em["numerics"][k] for k in
                         ("n_steps", "passes", "settled", "avg_check", "correction_max",
                          "avg_identity_residual", "sub_steps")},
            # this feature's codes (x430-x439), and the step's peak warning
            "codes": sorted({x["code"] for x in s["notices"]
                             if x["code"][6:8] == "43" or x["code"] == "EMIR-W411"}),
            "next_steps": [x["command"].split()[:3] for x in s["next_steps"]]}
    code, plan = emir("next", "--run-summary", os.path.join(a.out, "summary_worst.json"))
    st = plan["steps"][0]
    doc["next"] = {"exit": code, "first_step": st["id"], "needs_human": st["needs_human"],
                   "choices": [{"basis": c["basis"], "verdict": c["verdict"],
                                "max_ratio": c["max_ratio"], "recommended": c["recommended"]}
                               for c in st.get("choices") or []]}
    text = json.dumps(doc, indent=1, default=float)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
