"""CSDF results (Common Simulation Data Format): read and write.

CSDF is the text format of Xyce's `.print tran format=probe` (a `.csd`
file), PSpice's probe format, WRspice, Eldo's `-gwl csdf`; HSPICE manuals up
to E-2010.12 list `.option post=csdf` too, but HSPICE's own flavour of the
file was not found, so EMIR's HSPICE route writes the ASCII `.tr0` instead
(`emir.hspice`) and this reader is UNVERIFIED on HSPICE's CSDF.

The layout (Xyce's writer, N_IO_OutputterTimeProbe.C; WRspice's csdffile.cc;
checked on a real Xyce 7.11 file):

* ``#H`` opens a block; its header lines hold ``KEY='value'`` pairs (SOURCE,
  VERSION, TITLE, ANALYSIS, SWEEPVAR, COMPLEXVALUES, NODES ...);
* ``#N`` lists the quoted signal names, on its own line or the lines after;
* ``#C <x> <count>`` starts one point; its values follow, on the same line or
  the next ones, either plain (WRspice) or as ``value:index`` (Xyce);
* ``#;`` closes the block. A sweep writes one ``#H .. #;`` block per step.

The result is the same `waves.Waves` the other readers return.
"""
from __future__ import annotations

import re

import numpy as np

from .errors import EmirError


def _misuse(msg: str) -> EmirError:
    """The writer was handed a table it cannot write: a bug in the caller
    (the writer serves the tests and the synthetic example)."""
    return EmirError("EMIR-E905", f"cannot write the synthetic result: {msg}")

_PAIR = re.compile(r"([A-Za-z_][\w]*)\s*=\s*'([^']*)'")
_QUOTED = re.compile(r"'([^']*)'")


def is_csdf(head: bytes) -> bool:
    return head.lstrip().startswith(b"#H")


def _bad(path, why) -> EmirError:
    return EmirError("EMIR-E466", f"{path}: not a readable CSDF result: {why}", path=path)


def simulator_of(source: str | None) -> str | None:
    s = (source or "").lower()
    for name in ("hspice", "xyce", "ngspice", "spectre", "eldo", "pspice", "wrspice"):
        if name in s:
            return name
    return None


def _value(tok: str, path, lineno) -> float:
    try:
        return float(tok.split(":", 1)[0])
    except ValueError as e:
        raise _bad(path, f"line {lineno}: {tok!r} is not a number") from e


def parse(path: str) -> list:
    """Every block: `[{"header": {...}, "names": [...], "x": [...],
    "rows": [[...]]}]`."""
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.read().split("\n")
    blocks, cur, state, row, count = [], None, None, None, 0

    def close_point(lineno):
        if row is None:
            return
        if len(row) != count:
            raise _bad(path, f"line {lineno}: a point announces {count} values but holds "
                             f"{len(row)}")
        cur["rows"].append(row)

    for k, raw in enumerate(lines, 1):
        s = raw.strip()
        if not s:
            continue
        if s.startswith("#H"):
            if cur is not None:
                raise _bad(path, f"line {k}: #H inside a block (no #; before it)")
            cur, state, row = {"header": {}, "names": [], "x": [], "rows": []}, "H", None
            s = s[2:].strip()
            if not s:
                continue
        if cur is None:
            raise _bad(path, f"line {k}: data outside a #H .. #; block")
        if s.startswith("#N"):
            state = "N"
            cur["names"] += _QUOTED.findall(s[2:])
            continue
        if s.startswith("#C"):
            close_point(k)
            parts = s[2:].split()
            if len(parts) < 2:
                raise _bad(path, f"line {k}: #C needs the sweep value and the count")
            cur["x"].append(_value(parts[0], path, k))
            try:
                count = int(parts[1])
            except ValueError as e:
                raise _bad(path, f"line {k}: #C count {parts[1]!r} is not a number") from e
            row = [_value(t, path, k) for t in parts[2:]]
            state = "C"
            continue
        if s.startswith("#;"):
            close_point(k)
            blocks.append(cur)
            cur, state, row = None, None, None
            continue
        if state == "H":
            cur["header"].update({key.upper(): val for key, val in _PAIR.findall(s)})
        elif state == "N":
            cur["names"] += _QUOTED.findall(s)
        elif state == "C":
            row += [_value(t, path, k) for t in s.split()]
    if cur is not None:
        raise _bad(path, "the last block has no #; (the file is truncated, or the simulation "
                         "did not finish)")
    if not blocks:
        raise _bad(path, "no #H .. #; block")
    return blocks


def read_csdf(path: str, analysis: str | None = None):
    """One block of a CSDF result as `waves.Waves`; several blocks (a sweep)
    need `analysis` `sweep:N` (EMIR-E465 otherwise)."""
    from .hspice import select_sweep
    from .waves import Waves
    blocks = parse(path)
    from .hspice import _refuse_duplicates
    for b in blocks:
        h = b["header"]
        if h.get("COMPLEXVALUES", "NO").upper() == "YES":
            raise EmirError("EMIR-E467", f"{path}: the result holds complex values "
                            f"(COMPLEXVALUES='YES', ANALYSIS {h.get('ANALYSIS', '?')!r}): an AC "
                            f"or other frequency-domain result, not a transient; EM needs the "
                            f"currents over time", path=path)
        if h.get("SWEEPVAR", "time").lower() not in ("time", "t"):
            raise EmirError("EMIR-E467", f"{path}: the result is swept over "
                            f"{h.get('SWEEPVAR', '?')!r} (ANALYSIS {h.get('ANALYSIS', '?')!r}), "
                            f"not time: EM needs a transient result", path=path)
        if not b["names"]:
            raise _bad(path, "a block names no signals (#N)")
        _refuse_duplicates(path, b["names"])
        if any(len(r) != len(b["names"]) for r in b["rows"]):
            raise _bad(path, f"a point holds a number of values other than the "
                             f"{len(b['names'])} names")
        if not b["rows"]:
            raise _bad(path, "a block holds no points")
    pick = select_sweep(path, analysis,
                        [b["header"].get("SUBTITLE") or "?" for b in blocks], ["sweep"])
    b = blocks[pick]
    rows = np.array(b["rows"], dtype=float)
    h = b["header"]
    data = {n: rows[:, j].copy() for j, n in enumerate(b["names"])}
    meta = {"path": path, "format": "csdf", "simulator": simulator_of(h.get("SOURCE")),
            "simulator_version": h.get("VERSION") or None, "source": h.get("SOURCE"),
            "title": h.get("TITLE"), "analysis": h.get("ANALYSIS"),
            "tables": len(blocks), "table": pick + 1, "subtitle": h.get("SUBTITLE")}
    return Waves(np.array(b["x"], dtype=float), data, name=h.get("TITLE") or "tran",
                 kind="tran", meta=meta)


def write_csdf(path: str, names: list, blocks: list, source: str = "SYNTHETIC",
               version: str = "0", title: str = "* emir synthetic result",
               indexed: bool = True) -> str:
    """Write a CSDF file: `blocks` is a list of rows lists, each row
    `[x, v1, v2 ...]` (one block per sweep step). `indexed` writes Xyce's
    `value:index` form, else WRspice's plain values. Used by the tests; it is
    not a simulator."""
    out = []
    for rows in blocks:
        out += ["#H", f"SOURCE='{source}' VERSION='{version}'", f"TITLE='{title}'",
                "SUBTITLE=''", "ANALYSIS='Transient Analysis' SERIALNO='0'",
                f"ALLVALUES='NO' COMPLEXVALUES='NO' NODES='{len(names)}'",
                "SWEEPVAR='Time' SWEEPMODE='VAR_STEP'",
                "FORMAT='0 VOLTSorAMPS;EFLOAT : NODEorBRANCH;NODE  '", "DGTLDATA='NO'",
                "#N", " ".join(f"'{n}'" for n in names)]
        for r in rows:
            if len(r) != len(names) + 1:
                raise _misuse("each row: x, then one value per name")
            out.append(f"#C {r[0]:.8e} {len(names)}")
            cells = ([f"{v:.8e}:{k + 1}" for k, v in enumerate(r[1:])] if indexed
                     else [f"{v:.8e}" for v in r[1:]])
            out += ["   ".join(cells[i:i + 4]) for i in range(0, len(cells), 4)]
        out.append("#;")
    from .output import write_text
    return write_text(path, "\n".join(out) + "\n")
