"""Compare a result JSON with a golden file that carries its own tolerances.

    python compare.py expected/stream_small.json out/stream.json

The golden file is `{"label", "checks": [{"path", ...}]}`; `path` is a dotted
path into the result (list items by index: `rows.0.avg_gov`), and each check
is one of `{"value", "rel"}` / `{"value", "abs"}` (within the tolerance),
`{"max"}` / `{"min"}` (a bound), `{"equals"}` (exact). Exit 0 when every check
holds, 1 otherwise (each failure printed).
"""
from __future__ import annotations

import json
import sys


def get(doc, path: str):
    cur = doc
    for part in path.split("."):
        cur = cur[int(part)] if isinstance(cur, list) else cur[part]
    return cur


def check(doc, c: dict) -> str | None:
    """None when the check holds, else what is wrong."""
    try:
        v = get(doc, c["path"])
    except (KeyError, IndexError, TypeError):
        return f"{c['path']}: missing"
    if "equals" in c:
        return None if v == c["equals"] else f"{c['path']}: {v!r} != {c['equals']!r}"
    if v is None:
        return f"{c['path']}: null"
    if "value" in c:
        tol = c.get("abs", abs(c["value"]) * c.get("rel", 0.0))
        return (None if abs(v - c["value"]) <= tol
                else f"{c['path']}: {v:.6g} is not {c['value']:.6g} +- {tol:.3g}")
    if "max" in c and v > c["max"]:
        return f"{c['path']}: {v:.6g} > {c['max']:.6g}"
    if "min" in c and v < c["min"]:
        return f"{c['path']}: {v:.6g} < {c['min']:.6g}"
    return None


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    gold = json.load(open(argv[0], encoding="utf-8"))
    doc = json.load(open(argv[1], encoding="utf-8"))
    bad = [m for m in (check(doc, c) for c in gold["checks"]) if m]
    for m in bad:
        print(f"compare: {m}", file=sys.stderr)
    print(f"compare: {argv[1]} against {argv[0]}: "
          f"{len(gold['checks']) - len(bad)}/{len(gold['checks'])} checks hold")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
