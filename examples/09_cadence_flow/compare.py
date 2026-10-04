"""Compare example 09's digest with its golden file.

    python compare.py OUT_DIR/summary.json expected/summary.json

Exact, except the numbers the golden's `_tolerances` names (absolute). Exit 0
when they match, 1 with each difference otherwise.
"""
import json
import sys


def diff(got, want, tol, path, out):
    if isinstance(want, dict):
        if not isinstance(got, dict) or set(got) != set(want):
            out.append(f"{path or '.'}: keys differ")
            return
        for k in want:
            diff(got[k], want[k], tol, f"{path}.{k}" if path else k, out)
    elif isinstance(want, float) and path in tol:
        if got is None or abs(got - want) > tol[path]:
            out.append(f"{path}: {got!r}, expected {want!r}")
    elif got != want:
        out.append(f"{path}: {got!r}, expected {want!r}")


def main(argv) -> int:
    got = json.load(open(argv[1], encoding="utf-8"))
    want = json.load(open(argv[2], encoding="utf-8"))
    tol = want.pop("_tolerances")
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
