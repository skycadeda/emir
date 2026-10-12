#!/usr/bin/env python3
"""Render the EMIR Quantus command file (emir.ccl.tmpl) for one extraction.

    python render_ccl.py --svdb run/svdb --cell myblock --tech-dir "$QRC_TECH_DIR" \\
        --corner <corner> --temperature 25 --kind r_only > qrc_r_only.ccl
    (cd run && qrc -cmd ../qrc_r_only.ccl)

Status: UNVERIFIED until checked on site. The command file it renders (the
same as `emir extract`'s, a test keeps them equal) is verified on site by the
local agent: Quantus run on a matched LVS, then `emir check` on the DSPF it
writes. This script needs only the Python standard library, for a site that
wants the command file without installing emir.

Every site value (technology directory, corner, temperature) is an argument
or an environment variable; nothing is baked in. The DSPF is named
`<cell>.<kind>.dspf` by default, so two kinds of extraction in one work
directory never overwrite each other.
"""
from __future__ import annotations

import argparse
import os
import re
import string
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "emir.ccl.tmpl")
KINDS = ("r_only", "rc_coupled", "rc_decoupled")
#: input_db types Quantus reads LVS connectivity from (pvs the default); each
#: is UNVERIFIED until checked on site
LVS_DB_TYPES = ("pvs", "pegasus", "assura", "calibre")


#: a plain cell name; and what the Tcl-parsed command file cannot carry
CELL_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.$<>!#+-]*$")
UNSAFE = re.compile(r'["\[\]${}\\\n\r]')


def render(svdb, cell, tech_dir, corner, temperature, kind="r_only", out_file=None,
           lvs_db_type="pvs") -> str:
    """The command file. `svdb` is made absolute: Quantus runs in the work
    directory, where a relative path would be resolved a second time. The
    file is Tcl-parsed: a quote or bracket in a value would end it or run
    code, so such values are refused."""
    if not CELL_NAME.match(cell or ""):
        raise SystemExit(f"render_ccl: --cell {cell!r} is not a plain cell name")
    for what, v in (("--svdb", os.path.abspath(svdb)), ("--tech-dir", tech_dir),
                    ("--corner", corner), ("--out", out_file or "")):
        if UNSAFE.search(str(v)):
            raise SystemExit(f"render_ccl: {what} {v!r} holds a quote, bracket, brace, $, "
                             f"backslash or line break")
    if kind not in KINDS:
        raise SystemExit(f"render_ccl: --kind must be one of {', '.join(KINDS)}")
    if lvs_db_type not in LVS_DB_TYPES:
        raise SystemExit(f"render_ccl: --lvs-db-type must be one of {', '.join(LVS_DB_TYPES)}")
    with open(TEMPLATE, encoding="utf-8") as f:
        tmpl = string.Template(f.read())
    return tmpl.substitute(
        KIND=kind, SVDB=os.path.abspath(svdb), RUN_NAME=cell,
        OUT_FILE=os.path.basename(out_file or f"{cell}.{kind}.dspf"),
        TECH_DIR=tech_dir.rstrip("/"), CORNER=corner,
        TEMPERATURE=f"{float(temperature):f}", LVS_DB_TYPE=lvs_db_type)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="render_ccl", allow_abbrev=False,
                                 description=__doc__.split("\n\n")[0])
    ap.add_argument("--svdb", default="svdb", help="the LVS database directory")
    ap.add_argument("--cell", required=True, help="the layout top cell (the LVS run name)")
    ap.add_argument("--tech-dir", default=os.environ.get("QRC_TECH_DIR"),
                    help="Quantus technology directory, one sub-directory per corner "
                         "(default $QRC_TECH_DIR)")
    ap.add_argument("--corner", default=os.environ.get("QRC_CORNER"),
                    help="Quantus corner (default $QRC_CORNER)")
    ap.add_argument("--temperature", type=float, required=True,
                    help="extraction temperature in C (written as *|OPERATING_TEMPERATURE)")
    ap.add_argument("--kind", default="r_only", choices=KINDS)
    ap.add_argument("--lvs-db-type", default="pvs", choices=LVS_DB_TYPES)
    ap.add_argument("--out-file", default=None,
                    help="DSPF file name (default <cell>.<kind>.dspf)")
    a = ap.parse_args(argv)
    if not a.tech_dir:
        ap.error("--tech-dir or $QRC_TECH_DIR is required")
    if not a.corner:
        ap.error("--corner or $QRC_CORNER is required")
    sys.stdout.write(render(a.svdb, a.cell, a.tech_dir, a.corner, a.temperature, a.kind,
                            out_file=a.out_file, lvs_db_type=a.lvs_db_type))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
