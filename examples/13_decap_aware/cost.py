"""What `--dynamic` (with decap-aware EM) costs on big meshes: wall time and peak
memory of `emir run --dynamic`, one thread, in a clean child
(`../06_big_block/measure.py`). SYNTHETIC: example 06's generators.

    python cost.py --out out/cost --json out/cost.json --md out/cost.md
    python cost.py --out out/cost --emir-src /path/to/other/checkout --tag base ...

Cases (built once into OUT, then reused):

* `n200`: example 06's N = 200 Xyce mesh (`mesh.xyce_case`, 40 k grid nodes, 32
  inverters at 1 GHz, 2 fF per node), its CC result simulated with Xyce (one
  simulation, serial; the machine's simulation count is checked first);
* `s100`, `s300`: example 06's streamed meshes (`mesh.stream_case`: 10 k and 90 k
  nodes, 2000 pins with a random-phase pulse, 5001 and 501 evenly spaced
  samples over 50 ns), with 5 fF from every mesh node to ground added so the
  dynamic solve has capacitance.

`--emir-src DIR` runs another checkout of emir (its directory on PYTHONPATH),
so the same cases measure two versions: `results/cost.md` holds this version
against emir without decap-aware EM (the droop-only `--dynamic` integration),
measured during development; that base is not part of this repository, so its
rows are not reproducible from it.

`--pairs K` (with `--emir-src`, the base) runs each case K times as a pair,
the base and this version back to back, alternating which goes first, and
keeps every pair's CPU ratio (`pairs` in the JSON, its own table in the
Markdown): on a shared machine the load moves the ratio, so it is quoted as
the range the pairs give, not as one pair. The rows are left as they are.

    python cost.py --out out/cost --json results/cost.json --md results/cost.md \
        --cases n200 --pairs 3 --emir-src /path/to/base/checkout
"""
from __future__ import annotations

import argparse
import json
import os
import resource
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
B06 = os.path.join(HERE, os.pardir, "06_big_block")
sys.path.insert(0, B06)
import measure  # noqa: E402
import mesh  # noqa: E402

PROC = os.path.join(B06, "process.yaml")
MAX_SIMS = 12           # the machine's simulation limit (ours included)
CASES = ("n200", "s100", "s300")


def _sims() -> int:
    p = subprocess.run(["pgrep", "-c", "-f", "Xyce|ngspice|spectre|hspice"],
                       capture_output=True, text=True)
    return int((p.stdout.strip() or "0").splitlines()[0])


def build(name: str, root: str) -> dict:
    """The case's DSPF, result and period (built once)."""
    out = os.path.join(root, name)
    if name == "n200":
        dspf, raw = os.path.join(out, "grid_rc.dspf"), os.path.join(out, "cc.raw")
        if not os.path.exists(raw):
            mesh.xyce_case(200, 32, out)
            while _sims() + 1 > MAX_SIMS:
                time.sleep(10)
            subprocess.run([shutil.which("Xyce") or "Xyce", "-a", "cc.cir"], cwd=out,
                           check=True, capture_output=True)
        return {"dspf": dspf, "raw": raw, "period": mesh.PERIOD}
    n, nt = {"s100": (100, 5001), "s300": (300, 501)}[name]
    dspf = os.path.join(out, "rc.dspf")
    info = {"tag": f"s{n}_k2000_t{nt}"}
    raw = os.path.join(out, info["tag"] + ".raw")
    if not os.path.exists(dspf):
        c = mesh.stream_case(n, 2000, nt, out)
        with open(c["dspf"], encoding="utf-8") as f:
            src = f.read().splitlines()
        end = src.index(".ENDS")
        caps = [f"Cw{i} VDD:{i} 0 5e-15" for i in range(1, n * n + 1)]
        with open(dspf, "w", encoding="utf-8") as f:
            f.write("\n".join(src[:end] + caps + src[end:]) + "\n")
        raw = c["raw"]
    return {"dspf": dspf, "raw": raw, "period": mesh.S_PERIOD}


def run(name: str, case: dict, root: str, tag: str, src: str | None,
        repeat: int = 1) -> dict:
    out = os.path.join(root, name)
    summary = os.path.join(out, f"summary_{tag}.json")
    cmd = [sys.executable, "-m", "emir", "run", case["dspf"], "--process", PROC,
           "--cc-sim", case["raw"], "--mode", "cc-grid", "--period", repr(case["period"]),
           "--dynamic", "--summary-out", summary, "--format", "json"]
    env_old = os.environ.get("PYTHONPATH")
    os.environ["PYTHONPATH"] = (src if src else os.path.join(HERE, os.pardir, os.pardir)) \
        + (os.pathsep + env_old if env_old else "")
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        # wall time swings with a shared machine's load; CPU time (user + system,
        # of the run and its launcher, which waits for it) much less: each the
        # least of `repeat` runs
        r, cpu = None, None
        for _ in range(max(1, repeat)):
            u0 = resource.getrusage(resource.RUSAGE_CHILDREN)
            x = measure.run(cmd, os.path.join(out, f"log_{tag}.txt"))
            u1 = resource.getrusage(resource.RUSAGE_CHILDREN)
            c = (u1.ru_utime - u0.ru_utime) + (u1.ru_stime - u0.ru_stime)
            cpu = c if cpu is None else min(cpu, c)
            if r is None or x["wall_s"] < r["wall_s"]:
                r = x
    finally:
        if env_old is None:
            os.environ.pop("PYTHONPATH", None)
        else:
            os.environ["PYTHONPATH"] = env_old
    with open(summary, encoding="utf-8") as f:
        s = json.load(f)
    n = s["nets"][0]
    dy, em = n.get("dynamic") or {}, n.get("dynamic_em") or {}
    num = em.get("numerics") or {}
    return {"case": name, "tag": tag, "wall_s": r["wall_s"], "cpu_s": cpu,
            "peak_rss_MB": r["peak_rss_MB"],
            "load_1min": r["load_1min"], "exit": r["exit"], "droop_mV": dy.get("worst_mV"),
            "n_steps": dy.get("n_steps"), "sub_steps_added": (num.get("sub_steps") or {})
            .get("added"), "decap_aware": bool(em)}


def run_pairs(names: list, root: str, base_src: str, k: int) -> list:
    """`k` pairs per case, the base and this version back to back,
    alternating which runs first: each pair's CPU times and their ratio."""
    out = []
    for name in names:
        case = build(name, root)
        for i in range(k):
            order = [("base", base_src), ("decap", None)]
            if i % 2:
                order.reverse()
            got = {}
            for tag, src in order:
                print(f"[cost] {name} pair {i + 1}/{k} ({tag})", file=sys.stderr)
                got[tag] = run(name, case, root, tag, src)
            out.append({"case": name, "first": order[0][0],
                        "base_cpu_s": got["base"]["cpu_s"], "decap_cpu_s": got["decap"]["cpu_s"],
                        "ratio": got["decap"]["cpu_s"] / got["base"]["cpu_s"],
                        "load_base": got["base"]["load_1min"],
                        "load_decap": got["decap"]["load_1min"]})
    return out


def markdown(rows: list, pairs=()) -> str:
    L = ["# What `--dynamic` costs (synthetic, one thread)", "",
         "Generated by `examples/13_decap_aware/cost.py`: `emir run --dynamic` on example "
         "06's meshes, wall time and peak memory in a clean child "
         "(`../06_big_block/measure.py`), one thread, a shared machine: CPU time (user + "
         "system) and wall time, each the least of its runs, and `load` the 1-minute load "
         "average when the fastest started (wall time swings with it). `tag` names the emir "
         "measured: `base`, emir without decap-aware EM (the droop-only `--dynamic` "
         "integration), measured during development and not part of this repository "
         "(`--emir-src` a checkout of it); `decap`, this one. "
         "Screening, not sign-off.", "",
         "| case | tag | CPU s | wall s | peak MB | droop mV | steps | sub-steps added | "
         "decap-aware EM | load |", "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['case']} | {r['tag']} | {r['cpu_s']:.1f} | {r['wall_s']:.1f} | "
                 f"{r['peak_rss_MB']:.0f} | "
                 f"{r['droop_mV']:.4f} | {r['n_steps']} | "
                 f"{'' if r['sub_steps_added'] is None else r['sub_steps_added']} | "
                 f"{'yes' if r['decap_aware'] else 'no'} | {r['load_1min']:.1f} |")
    if pairs:
        L += ["", "## Pairs: the ratio on a shared machine", "",
              "`--pairs`: the base and this version run back to back, alternating which "
              "goes first; CPU time of each, and `load` the 1-minute load average when each "
              "started.", "",
              "| case | first | base CPU s | decap CPU s | decap / base | load (base, decap) |",
              "|---|---|---|---|---|---|"]
        for x in pairs:
            L.append(f"| {x['case']} | {x['first']} | {x['base_cpu_s']:.1f} | "
                     f"{x['decap_cpu_s']:.1f} | {x['ratio']:.2f}x | "
                     f"{x['load_base']:.1f}, {x['load_decap']:.1f} |")
        L.append("")
        for case in sorted({x["case"] for x in pairs}, key=CASES.index):
            rr = [x["ratio"] for x in pairs if x["case"] == case]
            L.append(f"- {case}: {min(rr):.2f}x to {max(rr):.2f}x over {len(rr)} pairs.")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], allow_abbrev=False)
    ap.add_argument("--cases", default=",".join(CASES))
    ap.add_argument("--out", required=True)
    ap.add_argument("--emir-src", default=None, help="another checkout of emir to measure")
    ap.add_argument("--tag", default="decap")
    ap.add_argument("--repeat", type=int, default=1,
                    help="runs per case, the fastest kept (the least disturbed by other work)")
    ap.add_argument("--pairs", type=int, default=0,
                    help="with --emir-src: this many back-to-back pairs per case (base, "
                         "this version), their CPU ratios kept; the rows are left as they are")
    ap.add_argument("--json", default=None, help="add the rows to this JSON file")
    ap.add_argument("--md", default=None, help="the Markdown table of every row in --json")
    a = ap.parse_args(argv)
    if a.pairs and not a.emir_src:
        ap.error("--pairs needs --emir-src (the base checkout)")
    rows, pairs = [], []
    if a.json and os.path.exists(a.json):
        with open(a.json, encoding="utf-8") as f:
            old = json.load(f)
        pairs = list(old.get("pairs") or [])
        rows = [r for r in old["rows"]
                if a.pairs or not (r["tag"] == a.tag and r["case"] in a.cases.split(","))]
    if a.pairs:
        pairs += run_pairs(a.cases.split(","), a.out, a.emir_src, a.pairs)
    else:
        for name in a.cases.split(","):
            case = build(name, a.out)
            print(f"[cost] {name} ({a.tag})", file=sys.stderr)
            rows.append(run(name, case, a.out, a.tag, a.emir_src, a.repeat))
    rows.sort(key=lambda r: (CASES.index(r["case"]), r["tag"]))
    doc = {"label": "emir run --dynamic on example 06's meshes, one thread", "rows": rows}
    if pairs:
        doc["pairs"] = pairs
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            f.write(json.dumps(doc, indent=1) + "\n")
    else:
        print(json.dumps(doc, indent=1))
    if a.md:
        with open(a.md, "w", encoding="utf-8") as f:
            f.write(markdown(rows, pairs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
