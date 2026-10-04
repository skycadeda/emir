"""Compare an example's output with its expected/ results.

    python compare.py OUT_DIR EXPECTED_DIR

Every JSON file in EXPECTED_DIR must exist in OUT_DIR with the same
structure: strings, booleans and nulls equal; numbers equal within the
tolerances in EXPECTED_DIR/tolerances.json (`rel`, `abs`). Exit 0 when they
match, 1 (with every difference listed) when they do not.
"""
from __future__ import annotations

import json
import math
import os
import sys


def diff(want, got, where, tol) -> list:
    if isinstance(want, bool) or want is None or isinstance(want, str):
        return [] if want == got else [f"{where}: expected {want!r}, got {got!r}"]
    if isinstance(want, (int, float)):
        if isinstance(got, bool) or not isinstance(got, (int, float)):
            return [f"{where}: expected a number {want!r}, got {got!r}"]
        ok = math.isclose(got, want, rel_tol=tol["rel"], abs_tol=tol["abs"])
        return [] if ok else [f"{where}: expected {want!r} (rel {tol['rel']}, abs "
                              f"{tol['abs']}), got {got!r}"]
    if isinstance(want, list):
        if not isinstance(got, list) or len(got) != len(want):
            return [f"{where}: expected a list of {len(want)}, got {got!r}"[:300]]
        return [d for i, (w, g) in enumerate(zip(want, got)) for d in diff(w, g, f"{where}[{i}]", tol)]
    if isinstance(want, dict):
        if not isinstance(got, dict):
            return [f"{where}: expected an object, got {got!r}"[:300]]
        out = [f"{where}: missing {k!r}" for k in want if k not in got]
        out += [f"{where}: unexpected {k!r}" for k in got if k not in want]
        return out + [d for k in want if k in got for d in diff(want[k], got[k], f"{where}.{k}", tol)]
    return [f"{where}: cannot compare {type(want).__name__}"]


def main(argv) -> int:
    out_dir, exp_dir = argv
    tol = {"rel": 1e-9, "abs": 0.0}
    tp = os.path.join(exp_dir, "tolerances.json")
    if os.path.isfile(tp):
        with open(tp, encoding="utf-8") as f:
            tol.update(json.load(f))
    problems = []
    for name in sorted(os.listdir(exp_dir)):
        if not name.endswith(".json") or name == "tolerances.json":
            continue
        got_path = os.path.join(out_dir, name)
        if not os.path.isfile(got_path):
            problems.append(f"{name}: not written")
            continue
        with open(os.path.join(exp_dir, name), encoding="utf-8") as f:
            want = json.load(f)
        with open(got_path, encoding="utf-8") as f:
            got = json.load(f)
        problems += diff(want, got, name, tol)
    for p in problems:
        print("DIFFERS  " + p, file=sys.stderr)
    print("matches expected/" if not problems else f"{len(problems)} difference(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
