"""HSPICE ASCII results (`.trN`, written with `.option post=2`): read and write.

HSPICE writes its waveform file in binary unless `.option post=2` (or
`post=ascii`) asks for text; `post_version=2001` keeps double precision (the
default 9601 keeps five significant digits, 9007 also cuts names to 16
characters). This module reads the text form into the same `waves.Waves` the
PSF ASCII and nutascii readers return, so a run sees no difference; and
writes it, for the tests and the synthetic example
(`examples/11_hspice_offline`).

The layout (no Synopsys document describes the text form; this reader
follows the format rules described in the sources of the open-source readers
of gwave (doc/hspice-output.txt), Electric (HSpiceOut.java) and PyOPUS -- it
was not run against those programs -- and was tried, privately, on real
third-party files of formats 9601 and 2001; the samples in tests/data/hspice
are synthetic):

* a header **record** of fixed-width fields, wrapped into 80-column lines;
  the lines are joined with no added blank (a long name can straddle a line
  break) and the record ends at ``$&%#``:

  ======== =====================================================
  [0:4]    variables written automatically (with the sweep one)
  [4:8]    probed variables
  [8:12]   sweep condition count (0: no sweep)
  [16:20]  format ``9007`` or ``9601``; ``2001`` is at [20:24]
  [24:88]  title (the netlist's first line)
  [88:104] date, [104:112] time, [112:184] copyright
  [184:188] tables in the file (0: one)
  [264:]   one 8-character type code per variable, then the
           variable names (each padded to a multiple of 16
           characters), then the sweep names, then ``$&%#``
  ======== =====================================================

  Names keep no closing parenthesis (``i(vdd``, ``i1(xi0.mn0``) and are
  lower case, with ``.`` between hierarchy levels; the first one is the
  independent variable (``TIME`` for a transient).
* the data: fixed-width values -- 11 characters in 9007 / 9601
  (``0.12345E-03``, ``-.12345E-03``), 13 in 2001 (``0.1234567E-03``, or
  ``0.123456E-003``) -- that never split across a line, while the rows do
  not line up with the lines; a value of 1e29 or more ends a table. With a
  sweep, each table starts with its sweep value(s): one when the condition
  count is 1 or 2, else the count less one.
* time points are the simulator's own, unevenly spaced: averages and rms are
  time-weighted downstream (`waveform`), never per sample.

A field that reads NaN or Inf is a value, not an end marker (`emir check`
FAILs on such currents, EMIR-E426); a file that ends inside a value is
refused as truncated; two columns of one name (written twice, or 9007 names
cut alike) are refused (EMIR-E427). The header is parsed in time linear in
its length, and the values are kept as packed doubles (8 bytes each).

The binary form (`.option post=1`, the default when `post` is given without
a value) is refused with EMIR-E463: the text form is what this reader was
checked on.
"""
from __future__ import annotations

import array
import math
import re

import numpy as np

from .errors import EmirError


def _misuse(msg: str) -> EmirError:
    """The writer was handed a table it cannot write: a bug in the caller
    (the writer serves the tests and the synthetic example)."""
    return EmirError("EMIR-E905", f"cannot write the synthetic result: {msg}")

#: the end of the header record
END_MARK = "$&%#"
#: a value at or above this ends a table
TABLE_END = 1e29
#: value width and values per line, by format
WIDTH = {"9007": 11, "9601": 11, "2001": 13}
PER_LINE = {"9007": 7, "9601": 7, "2001": 6}
#: significant digits each format keeps
DIGITS = {"9007": 5, "9601": 5, "2001": 7}
#: type codes of the independent variable
X_TYPES = {1: "time", 2: "frequency", 3: "dc sweep"}

_BIN_LE = b"\x04\x00\x00\x00"
_BIN_BE = b"\x00\x00\x00\x04"


def is_binary(head: bytes) -> bool:
    """A binary HSPICE result: blocks that start with the int32 4, an item
    count, 4 again and a byte count (either byte order)."""
    return (len(head) >= 16 and head[0:4] in (_BIN_LE, _BIN_BE)
            and head[8:12] == head[0:4])


def format_of(first_line: str) -> str | None:
    """`"9007"`, `"9601"` or `"2001"` when the line opens an HSPICE ASCII
    header record, else None."""
    s = first_line
    if len(s) < 24 or not s[:12].isdigit():
        return None
    if s[20:24] == "2001" and s[12:20].isdigit():
        return "2001"
    if s[16:20] in ("9007", "9601") and s[12:16].isdigit():
        return s[16:20]
    return None


def is_ascii(head: bytes) -> bool:
    """An HSPICE ASCII result (`.option post=2`)."""
    line = head.split(b"\n", 1)[0].decode("latin-1").rstrip("\r")
    return format_of(line) is not None


def normal_name(name: str) -> str:
    """The name with the closing parenthesis HSPICE leaves out: `i(vdd` ->
    `i(vdd)`, `i1(xi0.mn0` -> `i1(xi0.mn0)`; a bare node name is unchanged."""
    missing = name.count("(") - name.count(")")
    return name + ")" * missing if missing > 0 else name


def simulator_of(copyright_text: str) -> str | None:
    """Which simulator wrote the file, from the header's copyright field
    (HSPICE: Synopsys); None when it names no simulator emir knows."""
    c = copyright_text.lower()
    if "synopsys" in c:
        return "hspice"
    if "cadence" in c:
        return "spectre"
    return None


def _bad(path, why) -> EmirError:
    return EmirError("EMIR-E464", f"{path}: not a readable HSPICE ASCII result: {why}",
                     path=path)


def _names(region: str, n: int, version: str) -> list:
    """Split the name region of the record into names: blank-separated,
    except that a 9007 name is cut at 16 characters and may fill its slot
    (Electric's rule), so a run of more than 16 characters is several
    names."""
    out = []
    for tok in region.split(" "):
        if not tok:
            continue
        if version == "9007" and len(tok) > 16:
            out += [tok[i:i + 16] for i in range(0, len(tok), 16)]
        else:
            out.append(tok)
    return out


def parse_header(lines: list, path: str = "<result>") -> tuple:
    """`(header dict, index of the first data line)` from the file's lines."""
    version = format_of(lines[0].rstrip("\r")) if lines else None
    if version is None:
        raise _bad(path, "the first line is not an HSPICE header (digits, then 9007, 9601 "
                         "or 2001)")
    # the record is wrapped at 80 columns; a line whose trailing blanks were
    # stripped is padded back, except the one that holds the end mark. Each
    # line is looked at once (with the 3 characters before it, for a mark
    # that straddles a line break): linear in the header's length
    parts, i, tail = [], 0, ""
    while True:
        if i >= len(lines):
            raise _bad(path, f"the header never ends (no {END_MARK})")
        line = lines[i].rstrip("\r\n")
        i += 1
        if END_MARK in tail + line:
            parts.append(line)
            break
        line = line.ljust(80)
        parts.append(line)
        tail = line[-3:]
    rec = "".join(parts)
    rec = rec[:rec.index(END_MARK)]
    try:
        nauto, nprobe, ncond = int(rec[0:4]), int(rec[4:8]), int(rec[8:12])
    except ValueError as e:
        raise _bad(path, f"the counts at the start are not numbers ({rec[:12]!r})") from e
    nt = rec[184:188].strip()
    if nt and not nt.isdigit():
        raise _bad(path, f"the table count is not a number ({rec[184:188]!r})")
    toks = rec[264:].split()
    k = 0
    while k < len(toks) and toks[k].isdigit():
        k += 1
    types = [int(t) for t in toks[:k]]
    if not types:
        raise _bad(path, "no variable type codes in the header")
    names = _names(rec[264:], len(types), version)[len(types):]
    if len(names) < len(types):
        raise _bad(path, f"{len(types)} type codes but {len(names)} names")
    var, sweep = names[:len(types)], names[len(types):]
    head = {"post_version": version, "nauto": nauto, "nprobe": nprobe, "conditions": ncond,
            "title": rec[24:88].strip(), "date": rec[88:104].strip(),
            "time": rec[104:112].strip(), "copyright": rec[112:184].strip(),
            "ntables": int(nt) if nt else 0, "types": types, "names": var,
            "sweep_names": sweep}
    return head, i


def _values(lines, start, width, path) -> list:
    """The data as tables of packed doubles (each table without its end
    marker). A finite value >= 1e29 ends a table; NaN and Inf are values
    (the check refuses them where a run needs them, EMIR-E426)."""
    tables, cur = [], array.array("d")
    last = len(lines) - 1
    while last >= start and not lines[last].strip():
        last -= 1
    for k in range(start, last + 1):
        ln = lines[k].rstrip("\r\n").rstrip()
        for j in range(0, len(ln), width):
            f = ln[j:j + width]
            if not f.strip():
                continue
            try:
                v = float(f)
            except ValueError as e:
                if k == last and j + width >= len(ln) and len(f) < width:
                    raise _bad(path, f"line {k + 1}: the file ends inside a value ({f!r}, "
                                     f"{len(f)} of its {width} characters): it is "
                                     f"truncated, or the simulation did not finish") from e
                raise _bad(path, f"line {k + 1}: {f!r} is not a number (the values are "
                                 f"{width} characters wide in this format)") from e
            if TABLE_END <= v < math.inf:
                tables.append(cur)
                cur = array.array("d")
            else:
                cur.append(v)
    if len(cur):
        raise _bad(path, f"the last table has no end marker (a value >= {TABLE_END:g}): the "
                         f"file is truncated, or the simulation did not finish")
    return tables


def sweep_prefix(conditions: int) -> int:
    """How many sweep values open each table (Electric's rule, matched on a
    real 2001 file with two conditions)."""
    if conditions <= 0:
        return 0
    return 1 if conditions <= 2 else conditions - 1


def read_tr0ascii(path: str, analysis: str | None = None):
    """One table of an HSPICE ASCII result as `waves.Waves` (time-domain).

    A result with several tables (a sweep) is refused unless `analysis`
    picks one: `sweep:N` (1-based). Names are given back their closing
    parenthesis (`i(vdd)`); the header is in `meta`.
    """
    from .waves import Waves
    with open(path, encoding="latin-1") as f:
        lines = f.read().split("\n")
    head, start = parse_header(lines, path)
    ver = head["post_version"]
    xtype = head["types"][0]
    if xtype != 1:
        what = X_TYPES.get(xtype, f"type code {xtype}")
        raise EmirError("EMIR-E467", f"{path}: the result's independent variable is "
                        f"{what}, not time: EM needs a transient (.tran) result", path=path)
    tables = _values(lines, start, WIDTH[ver], path)
    if not tables:
        raise _bad(path, "no data after the header")
    npre = sweep_prefix(head["conditions"])
    n = len(head["names"])
    names = [normal_name(x) for x in head["names"]]
    _refuse_duplicates(path, names, ver)
    parsed = []
    for k, t in enumerate(tables, 1):
        pre, rest = list(t[:npre]), np.frombuffer(t, dtype=float)[npre:]
        if len(rest) % n:
            raise _bad(path, f"table {k} holds {len(rest)} values after {npre} sweep "
                             f"value(s), not a whole number of rows of {n}")
        parsed.append((pre, rest.reshape(-1, n)))
    pick = select_sweep(path, analysis,
                        [", ".join(f"{v:g}" for v in p) or "?" for p, _ in parsed],
                        head["sweep_names"][:1] or ["sweep"])
    pre, rows = parsed[pick]
    if not len(rows):
        raise _bad(path, f"table {pick + 1} has no rows")
    if not np.all(np.isfinite(rows[:, 0])):
        raise _bad(path, f"table {pick + 1}: the time column holds NaN or Inf")
    data = {nm: rows[:, j].copy() for j, nm in enumerate(names) if j > 0}
    meta = {"path": path, "format": "tr0ascii", "post_version": ver, "simulator": simulator_of(
                head["copyright"]), "simulator_version": None,
            "copyright": head["copyright"], "date": head["date"], "time": head["time"],
            "title": head["title"], "tables": len(parsed), "table": pick + 1,
            "sweep": dict(zip(head["sweep_names"], pre)) if pre else None,
            "precision_digits": DIGITS[ver]}
    return Waves(rows[:, 0].copy(), data, name=head["title"] or "tran", kind="tran",
                 meta=meta)


def _refuse_duplicates(path, names: list, version: str | None = None):
    """EMIR-E427 when two columns have one name (compared without regard to
    case, as the names sidecar keys are): which one a current is cannot be
    told, and keeping either would be a guess."""
    seen, dup = set(), []
    for nm in names:
        u = nm.upper()
        if u in seen and nm not in dup:
            dup.append(nm)
        seen.add(u)
    if dup:
        cut = (" (format 9007 cuts names to 16 characters: simulate with .option "
               "post_version=2001)" if version == "9007" else "")
        raise EmirError("EMIR-E427", f"{path}: {len(dup)} signal name(s) name more than one "
                        f"column: {', '.join(dup[:6])}{' ...' if len(dup) > 6 else ''}{cut}",
                        path=path, details={"names": dup})


def select_sweep(path, analysis, labels: list, sweep_names: list) -> int:
    """The 0-based table `analysis` picks (`sweep:N`), or 0 for a file of one
    table; EMIR-E465 when there are several and none is picked. `labels`
    says what each table is (its sweep values), for the message."""
    prefixes = labels
    m = re.fullmatch(r"\s*(?:sweep|table)\s*[:=]\s*(\d+)\s*", analysis or "", re.I)
    if len(prefixes) <= 1:
        if m and int(m.group(1)) != 1:
            raise EmirError("EMIR-E465", f"{path} holds one table; --analysis "
                            f"{analysis!r} asks for table {m.group(1)}", path=path)
        return 0
    shown = "; ".join(f"sweep:{k} = {p}" for k, p in enumerate(prefixes[:8], 1))
    if not m:
        raise EmirError("EMIR-E465", f"{path} holds {len(prefixes)} sweep tables "
                        f"({sweep_names[0] if sweep_names else 'sweep'}: {shown}"
                        f"{' ...' if len(prefixes) > 8 else ''}): EM is checked on one "
                        f"waveform; pick one with --analysis sweep:N", path=path,
                        details={"tables": len(prefixes)})
    k = int(m.group(1))
    if not 1 <= k <= len(prefixes):
        raise EmirError("EMIR-E465", f"{path} holds {len(prefixes)} sweep tables; "
                        f"--analysis {analysis!r} is out of range (1..{len(prefixes)})",
                        path=path, details={"tables": len(prefixes)})
    return k - 1


# ------------------------------------------------------------------ the writer


def number(v: float, version: str = "2001") -> str:
    """One value as HSPICE writes it: the mantissa in [0.1, 1), a sign (or a
    leading 0, or a blank in 9007), a two-digit exponent."""
    digits = DIGITS[version]
    if not math.isfinite(v):
        raise _misuse(f"{v!r} cannot be written")
    if v == 0.0:
        m, e = 0.0, 0
    else:
        e = math.floor(math.log10(abs(v))) + 1
        m = round(abs(v) / 10.0 ** e, digits)
        if m >= 1.0:
            m, e = m / 10.0, e + 1
        if m < 0.1 and m != 0.0:            # rounding at a power of ten
            m, e = m * 10.0, e - 1
    mant = f"{m:.{digits}f}"[1:]            # '.12345'
    lead = "-" if v < 0 else (" " if version == "9007" else "0")
    out = f"{lead}{mant}E{'-' if e < 0 else '+'}{abs(e):02d}"
    if len(out) != WIDTH[version]:
        raise _misuse(f"{v!r} does not fit the {version} field ({out!r})")
    return out


def end_marker(version: str = "2001") -> str:
    return number(1e30, version)


def header_text(names: list, types: list | None = None, version: str = "2001",
                title: str = "* emir", date: str = "", time_: str = "",
                copyright_: str = "", ntables: int = 0, sweep_names=(),
                conditions: int | None = None, nauto: int = 1) -> str:
    """The header record, wrapped at 80 columns. `names` starts with the
    independent variable (TIME)."""
    if types is None:
        types = [1] + [1 if n.lower().startswith("v(") else 8 for n in names[1:]]
    if len(types) != len(names):
        raise _misuse("one type code per name")
    cond = (1 if sweep_names else 0) if conditions is None else conditions
    nprobe = len(names) - nauto
    if version == "2001":
        rec = f"{nauto:04d}{nprobe:04d}{cond:04d}{0:08d}2001"
    else:
        rec = f"{nauto:04d}{nprobe:04d}{cond:04d}0000{version}    "
    rec += f"{title:<64.64}{date:<16.16}{time_:<8.8} {copyright_:<71.71}{ntables:4d}"
    rec += " " * 76
    assert len(rec) == 264
    rec += "".join(f"{t:3d}" + " " * 5 for t in types)
    for nm in [*names, *sweep_names]:
        if version == "9007":
            nm = nm[:16]
        rec += nm.ljust(((len(nm) + 16) // 16) * 16)
    rec += END_MARK
    rec += " " * (-len(rec) % 80)
    return "\n".join(rec[i:i + 80] for i in range(0, len(rec), 80)) + "\n"


def write_tr0ascii(path: str, names: list, tables: list, version: str = "2001",
                   title: str = "* emir synthetic result", sweep_names=(),
                   types: list | None = None, date: str = "", time_: str = "",
                   copyright_: str = "", conditions: int | None = None) -> str:
    """Write an HSPICE ASCII result in the documented layout above.

    `names` are written as HSPICE writes them (no closing parenthesis: pass
    `i(vdd` or `i(vdd)`, the parenthesis is dropped); `tables` is a list of
    `(sweep values, rows)`, each row one value per name. Used by the tests
    and the synthetic example; it is not HSPICE."""
    if version not in WIDTH:
        raise _misuse(f"version {version!r}: one of {', '.join(WIDTH)}")
    bare = [n[:-1] if n.endswith(")") and n.count("(") == n.count(")") else n
            for n in names]
    cond = conditions if conditions is not None else (
        (1 if len(sweep_names) <= 1 else len(sweep_names)) if sweep_names else 0)
    text = header_text(bare, types, version, title, date, time_, copyright_,
                       ntables=len(tables) if sweep_names else 0, sweep_names=sweep_names,
                       conditions=cond)
    npre = sweep_prefix(cond)
    per = PER_LINE[version]
    out = [text]
    for pre, rows in tables:
        if len(pre) != npre:
            raise _misuse(f"each table needs {npre} sweep value(s)")
        vals = [number(float(v), version) for v in pre]
        for r in rows:
            if len(r) != len(names):
                raise _misuse("one value per name in every row")
            vals += [number(float(v), version) for v in r]
        vals.append(end_marker(version))
        out += ["".join(vals[i:i + per]) + "\n" for i in range(0, len(vals), per)]
    from .output import write_text
    return write_text(path, "".join(out))
