#!/usr/bin/env python3
"""What an emir KLayout marker database holds, without opening a window.

Loads `emir.lyrdb` (written by `emir run --viewer DIR`) with KLayout's own
report-database reader and prints its description, its categories
(EM/<net>/<band>, IR/<net>/<mV band>) and how many markers each holds -- a
check an agent can run before telling a person to open it in KLayout.

    python agent/opensource/lyrdb_summary.py emir_out/viewer/emir.lyrdb [--json]
    klayout -b -r agent/opensource/lyrdb_summary.py -rd lyrdb=emir_out/viewer/emir.lyrdb

The second form runs inside the KLayout application itself (its `pya`), the
reader the GUI's Marker Browser uses.
"""
from __future__ import annotations

import json
import sys


def summary(path: str, rdb) -> dict:
    db = rdb.ReportDatabase("")
    db.load(path)
    cats = {}

    def walk(cat, prefix):
        name = f"{prefix}/{cat.name()}" if prefix else cat.name()
        cats[name] = cat.num_items()
        for sub in cat.each_sub_category():
            walk(sub, name)
    for c in db.each_category():
        walk(c, "")
    cells = [c.name() for c in db.each_cell()]
    return {"file": path, "description": db.description, "cells": cells,
            "items": db.num_items(), "categories": cats}


def main(argv) -> int:
    try:
        import klayout.rdb as rdb
    except ImportError:
        try:
            import pya as rdb                       # inside the KLayout application
        except ImportError:
            print("needs KLayout: pip install -e '.[klayout]' (or pip install klayout), or run it with "
                  "klayout -b -r", file=sys.stderr)
            return 3
    args = [a for a in argv if not a.startswith("--")]
    path = args[0] if args else globals().get("lyrdb")
    if not path:
        print("usage: lyrdb_summary.py FILE [--json]", file=sys.stderr)
        return 2
    s = summary(path, rdb)
    if "--json" in argv:
        print(json.dumps(s, indent=1))
    else:
        print(f"{s['file']}: {s['items']} markers in cell(s) {', '.join(s['cells'])}")
        print(f"  {s['description']}")
        for k, v in sorted(s["categories"].items()):
            print(f"  {k:40s} {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
elif "lyrdb" in globals():                          # klayout -b -r ... -rd lyrdb=FILE
    main([])
