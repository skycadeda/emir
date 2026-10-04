#!/usr/bin/env python3
"""Write agent/errors.md, the error-code catalog, from the registry
(`emir/errors.py`). The registry is the single source; never edit the
catalog by hand.

    python scripts/gen_errors_md.py            # write it
    python scripts/gen_errors_md.py --check    # exit 1 if it is stale

`tests/test_errors.py` runs the check.
"""
from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
OUT = os.path.join(ROOT, "agent", "errors.md")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="gen_errors_md", allow_abbrev=False,
                                 description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true",
                    help="do not write; exit 1 when the catalog is stale")
    a = ap.parse_args(argv)
    sys.path.insert(0, ROOT)
    from emir.errors import catalog_markdown
    text = catalog_markdown()
    old = open(OUT, encoding="utf-8").read() if os.path.isfile(OUT) else None
    if a.check:
        if old != text:
            print(f"{os.path.relpath(OUT, ROOT)} is stale: run "
                  f"python scripts/gen_errors_md.py", file=sys.stderr)
            return 1
        print(f"{os.path.relpath(OUT, ROOT)} is up to date")
        return 0
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"wrote {os.path.relpath(OUT, ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
