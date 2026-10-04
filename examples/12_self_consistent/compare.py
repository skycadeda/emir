"""Compare example 12's run summaries with its golden file and the hand numbers.

    python compare.py OUT_DIR expected/summary.json

OUT_DIR holds open.json, sc.json and rcc.json, the summaries run.sh wrote. Three
things are checked, each number within the golden's tolerance:

* each run against the golden values it pins (verdict, mode, ratios, IR; the
  self-consistent run's pass count and that it converged);
* the closed forms of README.md: the open loop at 2 mA per load (IR 108.0 mV,
  Rm1 avg 8 mA / 2.4 mA), the closed loop solved by nodal analysis of the
  resistive network (IR 100.050 mV, Rm1 avg 3.13820x);
* the self-consistent run against the rcc one: the loads follow their supply,
  so the closed loop is the network itself and the two must agree.

Exit 0 when everything holds, 1 with each difference otherwise.
"""
import json
import math
import sys

#: README.md, "The numbers by hand"
HAND = {"open": {"ir_drop_mV": 108.0, "Rm1:avg": 8.0 / 2.4, "Rc3:avg": 2.0 / 0.75},
        "closed": {"ir_drop_mV": 100.05045, "Rm1:avg": 3.1381961, "Rc3:avg": 2.4443323}}


def subset(summary: dict) -> dict:
    n = summary["nets"][0]
    out = {"verdict": summary["verdict"], "mode": n["mode"],
           "self_consistent": n["self_consistent"],
           "governing_metric": n["governing_metric"], "max_ratio": n["max_ratio"],
           "ir_drop_mV": n["ir_drop_mV"],
           "worst_items": {f"{i['name']}:{i['metric']}": i["ratio"]
                           for i in n["worst_items"]}}
    sc = n.get("self_consistency")
    if sc:
        out["self_consistency"] = {"converged": sc["converged"], "diverged": sc["diverged"],
                                   "n_passes": sc["n_passes"]}
    return out


def close(a, b, tol):
    return isinstance(a, (int, float)) and math.isclose(a, b, rel_tol=tol["rel"],
                                                         abs_tol=tol["abs"])


def diff(got, want, tol, where, out):
    if isinstance(want, dict):
        if not isinstance(got, dict) or set(got) != set(want):
            out.append(f"{where}: keys {sorted(got) if isinstance(got, dict) else got!r} "
                       f"!= {sorted(want)}")
            return
        for k in want:
            diff(got[k], want[k], tol, f"{where}.{k}", out)
    elif isinstance(want, float):
        if not close(got, want, tol):
            out.append(f"{where}: {got!r}, expected {want!r}")
    elif got != want:
        out.append(f"{where}: {got!r}, expected {want!r}")


def main(argv) -> int:
    out_dir, gold = argv[1], argv[2]
    want = json.load(open(gold, encoding="utf-8"))
    tol = want["tolerance"]
    got = {k: subset(json.load(open(f"{out_dir}/{k}.json", encoding="utf-8")))
           for k in ("open", "sc", "rcc")}
    problems = []
    for k in got:
        diff(got[k], want["runs"][k], tol, k, problems)
    for run, form in (("open", "open"), ("sc", "closed"), ("rcc", "closed")):
        g = got[run]
        for key, value in HAND[form].items():
            v = g["ir_drop_mV"] if key == "ir_drop_mV" else g["worst_items"].get(key)
            if not close(v, value, tol):
                problems.append(f"{run} against the hand {form}-loop {key}: {v!r}, "
                                f"expected {value!r}")
    for key in ("ir_drop_mV",):
        if not close(got["sc"][key], got["rcc"][key], tol):
            problems.append(f"sc against rcc {key}: {got['sc'][key]!r} != "
                            f"{got['rcc'][key]!r}")
    for k, v in got["rcc"]["max_ratio"].items():
        if not close(got["sc"]["max_ratio"][k], v, tol):
            problems.append(f"sc against rcc max_ratio.{k}: {got['sc']['max_ratio'][k]!r} "
                            f"!= {v!r}")
    for p in problems:
        print(f"MISMATCH {p}")
    print(f"{'golden and hand values match' if not problems else f'{len(problems)} mismatch(es)'}"
          f" ({gold})")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
