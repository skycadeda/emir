#!/usr/bin/env python3
"""Write the committed copies of the Cadence files the package generates:
agent/cadence/skill/emir_main.il (`emir.skillgui.MAIN`),
agent/cadence/skill/emir_display.drf (`emir.skillgui.display_drf()`) and
agent/cadence/quantus/emir.ccl.tmpl (`emir.cadence.CCL_TEMPLATE`). The
package is the single source (a pip install has no agent/ directory); the
copies are what a clone loads from `EMIR_HOME`. Never edit them by hand.

    python scripts/gen_cadence_skill.py            # write them
    python scripts/gen_cadence_skill.py --check    # exit 1 if one is stale

`tests/test_cadence_files.py` runs the check.
"""
from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
CADENCE = os.path.join(ROOT, "agent", "cadence")


def files() -> dict:
    """`{path: text}` of every generated copy."""
    sys.path.insert(0, ROOT)
    from emir.cadence import CCL_TEMPLATE
    from emir.skillgui import MAIN, display_drf
    return {os.path.join(CADENCE, "skill", "emir_main.il"): MAIN,
            os.path.join(CADENCE, "skill", "emir_display.drf"): display_drf(),
            os.path.join(CADENCE, "quantus", "emir.ccl.tmpl"): CCL_TEMPLATE}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="gen_cadence_skill", allow_abbrev=False,
                                 description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true",
                    help="do not write; exit 1 when a copy is stale")
    a = ap.parse_args(argv)
    stale = 0
    for path, text in files().items():
        rel = os.path.relpath(path, ROOT)
        old = open(path, encoding="utf-8").read() if os.path.isfile(path) else None
        if a.check:
            if old != text:
                print(f"{rel} is stale: run python scripts/gen_cadence_skill.py",
                      file=sys.stderr)
                stale += 1
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"wrote {rel}")
    if a.check:
        if stale:
            return 1
        print("agent/cadence generated copies are up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
