"""Digest example 11's four runs and compare them with the golden file.

    python compare.py OUT_DIR expected/summary.json [--write]

The digest: per run (rcc, from the HSPICE ASCII .tr0 and from its CSDF twin)
the verdict, the mode, the IR drop, the simulator verification status, and the
ratios of Rm1 and Rc3 on the metrics the run checked (avg, rms and peak).
Numbers compare to the golden's `_tolerance` (relative); the rest exactly. Exit 0 when they match,
1 with each difference otherwise. `--write` writes the golden instead (after
checking the numbers by hand: README.md). Standard library only.
"""
import json
import os
import sys

RUNS = ("rcc_tr0", "rcc_csd")


def digest(out: str) -> dict:
    d = {}
    for run in RUNS:
        with open(os.path.join(out, f"{run}.json"), encoding="utf-8") as f:
            s = json.load(f)
        net = s["nets"][0]
        items = {it["name"]: it["ratios"] for it in net["worst_items"]}
        ms = net.get("metrics") or ("avg", "rms", "peak")
        v = s["simulator_verification"]
        d[run] = {"verdict": s["verdict"], "mode": net["mode"], "metrics": list(ms),
                  "ir_drop_mV": net["ir_drop_mV"],
                  "simulator": v["simulator"], "verification": v["status"]}
        for name in ("Rm1", "Rc3"):
            if name in items:
                d[run][name] = {m: items[name][m] for m in ms}
    return d


def diff(got, want, tol, path, out):
    if isinstance(want, dict):
        if not isinstance(got, dict) or set(got) != set(want):
            out.append(f"{path or '.'}: keys differ ({sorted(got) if isinstance(got, dict) else got})")
            return
        for k in want:
            diff(got[k], want[k], tol, f"{path}.{k}" if path else k, out)
    elif isinstance(want, float):
        if got is None or abs(got - want) > tol * max(abs(want), 1e-12):
            out.append(f"{path}: {got!r}, expected {want!r}")
    elif got != want:
        out.append(f"{path}: {got!r}, expected {want!r}")


def main(argv) -> int:
    got = digest(argv[1])
    if "--write" in argv:
        doc = {"_about": "example 11: the HSPICE route on synthetic files (HSPICE did not run); "
                         "README.md works out every number",
               "_tolerance": 1e-4, **got}
        with open(argv[2], "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=1)
            f.write("\n")
        print(f"wrote {argv[2]}")
        return 0
    with open(argv[2], encoding="utf-8") as f:
        want = json.load(f)
    tol = want.pop("_tolerance")
    want.pop("_about")
    problems = []
    diff(got, want, tol, "", problems)
    for p in problems:
        print(f"MISMATCH {p}")
    print(f"{'golden values match' if not problems else f'{len(problems)} mismatch(es)'} "
          f"({argv[2]})")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
