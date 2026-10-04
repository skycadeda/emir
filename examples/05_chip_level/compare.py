#!/usr/bin/env python3
"""Compare the example's outputs with expected/ (golden values, tolerances).

    python compare.py OUT

expected/<file>.json holds `{"tolerance": {"rel": R, "abs": A}, "values":
{"dotted.path": value}}` for each output file OUT/<file>.json; a path walks
dicts by key and lists by index. Numbers match within max(abs, rel x |value|);
everything else exactly. Exit 1 on any difference.
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def walk(doc, path):
    for k in path.split("."):
        doc = doc[int(k)] if isinstance(doc, list) else doc[k]
    return doc


def main(out: str) -> int:
    bad, n = [], 0
    exp_dir = os.path.join(HERE, "expected")
    for name in sorted(os.listdir(exp_dir)):
        with open(os.path.join(exp_dir, name), encoding="utf-8") as f:
            exp = json.load(f)
        with open(os.path.join(out, name), encoding="utf-8") as f:
            got_doc = json.load(f)
        tol = exp.get("tolerance", {})
        rel, ab = float(tol.get("rel", 1e-6)), float(tol.get("abs", 1e-12))
        for path, want in exp["values"].items():
            n += 1
            try:
                got = walk(got_doc, path)
            except (KeyError, IndexError, TypeError):
                bad.append(f"{name}: {path}: missing")
                continue
            if isinstance(want, (int, float)) and not isinstance(want, bool) \
                    and isinstance(got, (int, float)) and not isinstance(got, bool):
                if abs(got - want) > max(ab, rel * abs(want)):
                    bad.append(f"{name}: {path}: {got!r} != {want!r} (rel {rel:g}, abs {ab:g})")
            elif got != want:
                bad.append(f"{name}: {path}: {got!r} != {want!r}")
    for b in bad:
        print(f"  DIFF {b}")
    print(f"compare: {n - len(bad)} of {n} expected values match"
          + ("" if not bad else f"; {len(bad)} differ"))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "out")))
