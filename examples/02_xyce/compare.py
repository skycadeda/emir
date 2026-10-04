"""Compare an example's run summaries with its golden file (examples 02 and 03).

    python compare.py OUT_DIR expected/summary.json

OUT_DIR holds the run summaries rcc.json and cc.json that run.sh wrote. What is
compared is what the golden pins (verdict, counts, mode, governing metric,
ratios, IR, the worst items; no paths, no times), each number within the
golden's tolerance. Exit 0 when they match, 1 with each difference otherwise.
"""
import json
import math
import sys


def subset(summary: dict) -> dict:
    """What a golden file pins of a run summary (as tests/test_examples_opensim.py)."""
    n = summary["nets"][0]
    return {"verdict": summary["verdict"], "counts": summary["counts"], "net": n["net"],
            "mode": n["mode"], "governing_metric": n["governing_metric"],
            "max_ratio": n["max_ratio"], "ir_drop_mV": n["ir_drop_mV"],
            "dynamic_mV": n["dynamic_mV"], "n_violations": n["n_violations"],
            "worst_items": {f"{i['name']}:{i['metric']}": i["ratio"]
                            for i in n["worst_items"]},
            "unsaved_pins": n["unsaved_pins"]}


def diff(got, want, tol, where, out):
    if isinstance(want, dict):
        if not isinstance(got, dict) or set(got) != set(want):
            out.append(f"{where}: keys {sorted(got) if isinstance(got, dict) else got!r} "
                       f"!= {sorted(want)}")
            return
        for k in want:
            diff(got[k], want[k], tol, f"{where}.{k}", out)
    elif isinstance(want, float):
        if not (isinstance(got, (int, float)) and
                math.isclose(got, want, rel_tol=tol["rel"], abs_tol=tol["abs"])):
            out.append(f"{where}: {got!r}, expected {want!r}")
    elif got != want:
        out.append(f"{where}: {got!r}, expected {want!r}")


def main(argv) -> int:
    out_dir, gold = argv[1], argv[2]
    want = json.load(open(gold, encoding="utf-8"))
    problems = []
    for k in ("rcc", "cc"):
        got = subset(json.load(open(f"{out_dir}/{k}.json", encoding="utf-8")))
        diff(got, want["runs"][k], want["tolerance"], k, problems)
    for p in problems:
        print(f"MISMATCH {p}")
    print(f"{'golden values match' if not problems else f'{len(problems)} mismatch(es)'} "
          f"({gold})")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
