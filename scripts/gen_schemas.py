#!/usr/bin/env python3
"""Write agent/schemas/<name>.schema.json, the JSON Schemas of every document
emir reads or writes, from `emir/schemas.py` (the single source; `emir
schema <name>` prints the same text).

    python scripts/gen_schemas.py            # write them
    python scripts/gen_schemas.py --check    # exit 1 if any is stale or extra

`tests/test_schemas.py` runs the check.
"""
from __future__ import annotations

import argparse
import glob
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
OUT = os.path.join(ROOT, "agent", "schemas")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="gen_schemas", allow_abbrev=False,
                                 description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true",
                    help="do not write; exit 1 when a file is stale, missing or extra")
    a = ap.parse_args(argv)
    sys.path.insert(0, ROOT)
    from emir import schemas
    want = {f"{n}.schema.json": schemas.text(n) for n in schemas.names()}
    have = {os.path.basename(p) for p in glob.glob(os.path.join(OUT, "*.schema.json"))}
    if a.check:
        bad = sorted(n for n, t in want.items()
                     if not os.path.isfile(os.path.join(OUT, n))
                     or open(os.path.join(OUT, n), encoding="utf-8").read() != t)
        extra = sorted(have - set(want))
        if bad or extra:
            print(f"agent/schemas is stale ({', '.join(bad + extra)}): run "
                  f"python scripts/gen_schemas.py", file=sys.stderr)
            return 1
        print(f"agent/schemas: {len(want)} schemas up to date")
        return 0
    os.makedirs(OUT, exist_ok=True)
    for n in sorted(have - set(want)):
        os.remove(os.path.join(OUT, n))
    schemas.write_all(OUT)
    print(f"wrote {len(want)} schemas to {os.path.relpath(OUT, ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
