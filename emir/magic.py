"""The magic route: a GDS through magic's own extraction to the DSPF EMIR reads.

    emir extract --tool magic --gds blk.gds --cell blk --process pdks/sky130.yaml

magic (open source) computes every resistor of a net from one drawn shape but
writes only its value. A magic built with the `geoout` patch writes the shape
too: `extresist geoout on` adds `<cell>.res.geo` beside `<cell>.res.ext`, one
line for each `resist` line, in the same order::

    resgeo "node1" "node2" ohms layer dir width length cl x1 y1 x2 y2 merged

`dir` is a wire's current direction (`ew`, `ns`, `diag`) with its `width`
across and `length` along it (length = R x width / sheet, exact) and `cl` its
centerline, or `via`: a contact array of `width` x `cl` cuts. Units are the
`.res.ext`'s (its `scale` line). With `extresist simplify off` as well, magic
merges nothing, so every resistor is one drawn segment (`merged` 0). The patch
is public: branch `extresist-geoout` of https://github.com/codervijay/magic,
offered upstream as https://github.com/RTimothyEdwards/magic/pull/562 (open).
Without `geoout` it changes nothing.

From one extraction this module writes, into the --out directory:

* `<cell>.dspf` -- the network as DSPF: per net `*|NET` (its capacitance, F),
  `*|P` for each pin of a port, `*|I` for each device terminal (its terminal
  name from the process file's `magic: devices:`), `*|S` for each subnode, all
  placed in um, then the net's resistors, each with its shape::

      R12 inp inp.n10 3.26973 $layer=metal2 $dir=ew $W=0.26 $L=6.8 $X=-0.3 $Y=-1.22 $X2=6.5 $Y2=-1.22 $LLX=... $URY=...
      R13 inp.n19 inp.n11 9.3005 $layer=viali $cuts=1x1 $N=1 $X=19 $Y=0.285

  then the capacitors and the device instances. It is also the RC netlist an
  rcc simulation runs (`emir simulate`; Xyce reads `$` fields as parameters,
  and `emir simulate` leaves them out of its deck).

The capacitors are the extraction's, each counted once (magic's `extresist`
moves each coupling capacitor to ground on both its nets for its own RC
estimate, so the split netlist counts a coupling three times; the lumped
netlist of the same run counts it once), spread over the nodes of their nets:
half from each net, over its nodes by their weights (magic's share of the
net's capacitance, else their drawn area), each piece to the other net's
nearest node, so each net's total is kept and its charging current enters
where it is drawn. A port drawn with several pins has one node per pin
(`vss`, `vss.p2`, ...): `tied` (the default) joins each extra pin to the port
by a 0 V source (every pin fed; EMIR's network reads the source as a short),
`separate` leaves that to the reader.

Ported, with changes, from the Skycadeda Analog Framework (SAF), Copyright (c)
2026 Skycadeda, licensed under the MIT License by its copyright holder
(THIRD_PARTY_NOTICES.md): the magic script (`SCRIPT`), the counted-once
capacitors (`counted_once`, `subckt_ports`), `logical_lines`, the geometry
reader (`read_geometry` and its helpers, the Segment and Geometry records) and
the DSPF writer (`write_dspf` and its helpers). The changes: the
cell flattened in the script; the capacitances in farads, on the split
netlist's nets (`lumped_names`); `$N` and the rectangle (`$LLX`..`$URY`) beside
the annotations, so EMIR's reader needs nothing new; each via at its own cut
(`via_places`); the `*|I` node is the device's own terminal node; and the
terminal names come from the process file. Finding and probing magic, its work
directory, running it, the checks on its output (`floating_caps`, `subckts`,
the diagonal wires) and the command are EMIR's own.

Screening, not sign-off.
"""
from __future__ import annotations

import datetime
import fnmatch
import os
import re
import shutil
import time
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from .errors import EmirError, notice

#: where the patch lives (the hint of EMIR-E643 names both)
FORK = "https://github.com/codervijay/magic/tree/extresist-geoout"
FORK_GIT = "https://github.com/codervijay/magic"
FORK_BRANCH = "extresist-geoout"
UPSTREAM_PR = "https://github.com/RTimothyEdwards/magic/pull/562"

#: the magic names tried on $PATH after $EMIR_MAGIC, the process file's
#: `magic: binary:` and the site file's `magic:` (`magic-8.3.515-geo` is the
#: name the patched build was given where EMIR was developed: an example, any
#: name works)
PATH_NAMES = ("magic-8.3.515-geo", "magic")

#: what the process file's `magic: binary:` may name: a magic program on
#: $PATH, by a bare name that starts with `magic`. A process file comes with
#: the PDK or the layout, not from the person's own setup, so a path or any
#: other program there is refused (EMIR-E648): --magic, $EMIR_MAGIC and the
#: site file name any program
PROCESS_BINARY = re.compile(r"^magic[A-Za-z0-9_.+-]*$")

#: the directory under --out where magic runs (EMIR's own: `WORK_MARK` says
#: so), and the file in it that marks it
WORK_DIR = "magic"
WORK_MARK = ".emir-magic-work"

#: the file magic reads the layout from, in its work directory: a fixed name,
#: so no path the person chose is Tcl in magic's script
GDS_NAME = "input.gds"

#: magic's script (SAF's extraction script, with geometry on): the cell flattened first, so a hierarchical layout
#: extracts as the flat one would. The layout's cell is renamed before it is
#: loaded (magic 8.3.684 does not rename the cell being edited), and its flat
#: copy takes its name: every subcell's paint, the top cell's labels only (a
#: subcell's labels name no pin). Then the lumped netlist (each capacitor
#: counted once) and the resistance split with every resistor one drawn segment
SCRIPT = """crashbackups stop
drc off
gds read {gds}
cellname rename {cell} {cell}__emir_hier
load {cell}__emir_hier
flatten -dotoplabels {cell}
load {cell}
select top cell
port makeall
extract do local
extract do resistance
extract all
ext2spice lvs
ext2spice cthresh 0
ext2spice -o {lumped}
ext2sim labels on
ext2sim
extresist tolerance {tolerance}
extresist simplify off
extresist geoout on
extresist all
ext2spice extresist on
ext2spice -o {pex}
quit -noprompt
"""

#: magic's answer to `extresist geoout` when the option exists and is on
_PROBE = "extresist geoout on\nputs EMIR_PROBE\nextresist geoout\nquit -noprompt\n"

#: a cell name magic's script, the netlists and file names can carry as one
#: word: no white space, quotes, brackets, braces, `$`, `;` or a backslash
SAFE_CELL = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.+-]*$")

#: the subnode suffixes magic gives a net's nodes: `.nK` a subnode, `.tK` a
#: device terminal, and `.pK` a port's further pin (this module's)
_SUFFIX = re.compile(r"\.[ntp]\d+$")


# ------------------------------------------------------------------ magic


def geoout_accepted(exe: str, timeout: float = 30.0) -> bool:
    """Whether `exe` is a magic that knows `extresist geoout` (the patch): it
    answers `on` after `extresist geoout on`, where an unpatched one says
    "Unknown option: geoout"."""
    import tempfile

    from . import subproc
    with tempfile.TemporaryDirectory(prefix="emir-magic-") as d:
        script = os.path.join(d, "probe.tcl")
        with open(script, "w", encoding="utf-8") as f:
            f.write(_PROBE)
        try:
            p = subproc.run([exe, "-dnull", "-noconsole", "-rcfile", os.devnull, script],
                            timeout=timeout, cwd=d)
        except (OSError, Exception):        # noqa: BLE001 - not a magic, or it hung
            return False
    text = (p.stdout or "") + (p.stderr or "")
    after = text.split("EMIR_PROBE", 1)[1] if "EMIR_PROBE" in text else ""
    return ("Unknown option" not in text
            and any(ln.strip() == "on" for ln in after.splitlines()))


def _executable(value: str, path_only: bool = False) -> str | None:
    """The program `value` names: a path (not `path_only`), else a name on
    $PATH."""
    v = os.path.expanduser(str(value))
    if os.sep in v or (os.altsep and os.altsep in v):
        if path_only:
            return None
        return os.path.abspath(v) if os.path.isfile(v) and os.access(v, os.X_OK) else None
    return shutil.which(v)


def process_binaries(proc_data: dict | None) -> list:
    """The process file's `magic: binary:` names (a name or a list), each a
    magic program's bare name (`PROCESS_BINARY`), else EMIR-E648: EMIR runs
    the program it names, and a process file is not the person's own setup."""
    hint = ((proc_data or {}).get("magic") or {}).get("binary")
    names = [hint] if isinstance(hint, str) else list(hint or [])
    bad = [str(v) for v in names if not PROCESS_BINARY.match(str(v))]
    if bad:
        raise EmirError("EMIR-E648", f"the process file's magic: binary: names {bad[0]!r}: "
                        f"a process file may name only a magic program on $PATH by its "
                        f"bare name (starting with `magic`, no path); give a path with "
                        f"--magic, $EMIR_MAGIC or the site file's magic:", name=bad[0])
    return [str(v) for v in names]


def candidates(proc_data: dict | None, explicit: str | None = None) -> list:
    """`[(value, source)]` in the order they are tried: --magic, $EMIR_MAGIC,
    the process file's `magic: binary:` (a magic program's name on $PATH, or a
    list: `process_binaries`), the site file's `magic:` ($MAGIC_BIN), then
    `PATH_NAMES` on $PATH."""
    from . import site
    out = []
    if explicit:
        out.append((explicit, "--magic"))
    if os.environ.get("EMIR_MAGIC"):
        out.append((os.environ["EMIR_MAGIC"], "$EMIR_MAGIC"))
    for v in process_binaries(proc_data):
        out.append((v, "the process file's magic: binary:"))
    v, src = site.get("magic")
    if v:
        out.append((str(v), src))
    out += [(n, "$PATH") for n in PATH_NAMES]
    return out


def find_magic(proc_data: dict | None, explicit: str | None = None, log=print) -> dict:
    """The first magic that writes resistor geometry: `{path, source, version,
    tried}`. None does: EMIR-E643 (exit 3), naming what was tried and where the
    patch is. An explicit --magic that is not one is refused, not skipped."""
    from . import site
    tried, seen = [], set()
    for value, source in candidates(proc_data, explicit):
        # a process file's name is looked up on $PATH only, never as a path
        exe = _executable(value, path_only=source.startswith("the process file"))
        if exe is None:
            tried.append({"value": value, "source": source, "path": None,
                          "result": "not found"})
            continue
        if exe in seen:
            continue
        seen.add(exe)
        ok = geoout_accepted(exe)
        tried.append({"value": value, "source": source, "path": exe,
                      "result": "writes geometry" if ok else "no geoout (unpatched)"})
        if ok:
            version = site.tool_version("magic", exe)
            warn = site.magic_warning(exe, version)
            if warn:
                notice("EMIR-W640", warn, log)
            return {"path": exe, "source": source, "version": version, "tried": tried}
        if source == "--magic":
            break
    shown = "; ".join(f"{t['value']} ({t['source']}): {t['result']}" for t in tried)
    raise EmirError(
        "EMIR-E643", f"no magic that writes resistor geometry (`extresist geoout`) was "
        f"found: {shown}. Build the patched magic: git clone -b {FORK_BRANCH} {FORK_GIT}; "
        f"cd magic; ./configure --prefix=$HOME/magic-geo; make; make install; then "
        f"EMIR_MAGIC=$HOME/magic-geo/bin/magic (the patch: {FORK}; upstream: "
        f"{UPSTREAM_PR})", details={"tried": tried})


def route_magic(proc_data: dict | None = None) -> dict:
    """The magic `emir extract --tool magic` would run, without raising:
    `{path, source, version, writes_geometry, tried}` (`find_magic`'s search;
    `path` None and `writes_geometry` False when none writes resistor
    geometry). `emir info` and `emir next --gds` report it, so an agent
    knows before the extraction whether the route can run here (the stock
    `magic` on $PATH is often not the one it uses)."""
    from . import site
    try:
        found = find_magic(proc_data, log=None)
    except EmirError as e:
        tried = (e.details or {}).get("tried") or []
        return {"path": None, "source": None, "version": None, "writes_geometry": False,
                "tried": tried}
    return {"path": found["path"], "source": found["source"],
            "version": found["version"] or site.tool_version("magic", found["path"]),
            "writes_geometry": True, "tried": found["tried"]}


#: a plain directory name (the PDK's, under $PDK_ROOT): the magicrc is a
#: script magic runs, so a path or `..` from the process file is not used
_SAFE_WORD = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.+-]*$")


def magicrc_for(proc_data: dict | None, explicit: str | None) -> str:
    """magic's startup file for the process: `explicit` (--magicrc), else
    $EMIR_MAGICRC, else ``$PDK_ROOT/<pdk>/libs.tech/magic/<pdk>.magicrc`` for
    the process file's `pdk:` name (its first word: the PDK's directory under
    $PDK_ROOT, e.g. `sky130A`). EMIR-E641 when there is none."""
    for p in (explicit, os.environ.get("EMIR_MAGICRC")):
        if p:
            if not os.path.isfile(p):
                raise EmirError("EMIR-E101", f"magic startup file not found: {p}", path=p)
            return os.path.abspath(p)
    pdk = str((proc_data or {}).get("pdk") or "")
    root = os.environ.get("PDK_ROOT")
    name = pdk.split()[0] if pdk.split() else ""
    if name and not _SAFE_WORD.match(name):
        raise EmirError("EMIR-E641", f"the process file's pdk: name {name!r} is not a "
                        f"directory name under $PDK_ROOT (a plain name such as sky130A): "
                        f"give --magicrc")
    if root and name:
        p = os.path.join(root, name, "libs.tech", "magic", f"{name}.magicrc")
        if os.path.isfile(p):
            return p
    raise EmirError("EMIR-E641", "no magic startup file for this process: give --magicrc "
                    f"(looked for $PDK_ROOT/{name or '<pdk>'}/libs.tech/magic/"
                    f"{name or '<pdk>'}.magicrc; PDK_ROOT is "
                    f"{'set' if root else 'not set'})")


def check_cell(cell: str) -> str:
    """`cell` if magic's script and the netlists can carry it, else EMIR-E243."""
    if not SAFE_CELL.match(cell or ""):
        raise EmirError("EMIR-E243", f"the cell name {cell!r} cannot be written into magic's "
                        f"script or a netlist safely (white space, quotes, brackets, braces, "
                        f"`$`, `;` or a backslash): rename the cell", name=cell)
    return cell


#: what EMIR writes into the files it makes on this route (the DSPF's
#: `*|PROGRAM`): a file without it is not EMIR's
MADE_BY = "emir extract --tool magic"


def made_by_emir(path: str) -> bool:
    """Whether `path` is a DSPF this route wrote (`MADE_BY` in its first
    lines)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            head = f.read(2048)
    except OSError:
        return False
    return MADE_BY in head


def own_work(work: str) -> str:
    """Make `work` EMIR's work directory (`WORK_MARK` in it), or refuse
    (EMIR-E646) a directory that holds anything and is not: magic writes
    files named after the cell and its subcells there, and EMIR clears its
    own before each run."""
    from .output import make_dir, write_text
    mark = os.path.join(work, WORK_MARK)
    if os.path.isdir(work) and os.listdir(work) and not os.path.isfile(mark):
        raise EmirError("EMIR-E646", f"{work} exists, holds files and EMIR did not make it: "
                        f"magic runs in a directory of EMIR's own (`{WORK_DIR}/` under "
                        f"--out); move it, or give another --out", path=work)
    if os.path.lexists(work) and not os.path.isdir(work):
        raise EmirError("EMIR-E646", f"{work} exists and is not a directory: magic runs in "
                        f"a directory of EMIR's own; move it, or give another --out",
                        path=work)
    make_dir(work)
    if not os.path.isfile(mark):
        write_text(mark, "EMIR's magic work directory (emir extract --tool magic): EMIR "
                         "replaces the files in it on every run\n")
    return work


def guard_outputs(out_dir: str, cell: str) -> None:
    """Refuse (EMIR-E646) to replace a `<cell>.dspf` in `out_dir` that EMIR
    did not write."""
    for ext in (".dspf",):
        p = os.path.join(out_dir, f"{cell}{ext}")
        if os.path.lexists(p) and not made_by_emir(p):
            raise EmirError("EMIR-E646", f"{p} exists and EMIR did not write it: emir "
                            f"extract --tool magic writes {cell}{ext} there; move it, or "
                            f"give another --out", path=p)


def _link_layout(gds: str, dst: str) -> None:
    """`dst` (the work directory's `GDS_NAME`) is the layout: a link to it,
    else a copy. A `dst` that is the layout itself is left as it is: never
    removed, never linked to itself."""
    src = os.path.realpath(gds)
    if os.path.lexists(dst):
        if not os.path.islink(dst) and os.path.exists(dst) and os.path.samefile(dst, src):
            return
        os.remove(dst)
    try:
        os.symlink(src, dst)
    except OSError:
        shutil.copyfile(src, dst)


def run_magic(gds: str, cell: str, rcfile: str, exe: str, work: str,
              tolerance: float = 10.0, timeout: float = 1800.0, log=print) -> dict:
    """Extract `cell` of `gds` in `work` (EMIR's own directory: `own_work`):
    the layout is linked (or copied) in as `GDS_NAME`, magic runs `SCRIPT`
    there. Returns the paths it wrote."""
    from . import subproc
    from .output import write_text
    check_cell(cell)
    if not os.path.isfile(gds):
        raise EmirError("EMIR-E101", f"layout not found: {gds}", path=gds)
    own_work(work)
    _link_layout(gds, os.path.join(work, GDS_NAME))
    paths = {k: os.path.join(work, f"{cell}{ext}") for k, ext in (
        ("lumped", "_lumped.spice"), ("pex", "_pex.spice"), ("ext", ".ext"),
        ("res_ext", ".res.ext"), ("res_geo", ".res.geo"))}
    for p in paths.values():                 # a stale file must not pass for this run's
        if os.path.exists(p):
            os.remove(p)
    script = write_text(os.path.join(work, "extract.tcl"), SCRIPT.format(
        gds=GDS_NAME, cell=cell, lumped=os.path.basename(paths["lumped"]),
        pex=os.path.basename(paths["pex"]), tolerance=f"{float(tolerance):g}"))
    logf = os.path.join(work, "magic.log")
    log(f"[emir] magic: extracting {cell} with every resistor's geometry")
    t0 = time.time()
    try:
        p = subproc.run([exe, "-dnull", "-noconsole", "-rcfile", rcfile,
                         os.path.basename(script)], timeout=timeout, cwd=work)
    except Exception as e:                   # noqa: BLE001 - TimeoutExpired, OSError
        import subprocess
        if isinstance(e, subprocess.TimeoutExpired):
            raise EmirError("EMIR-E645", f"magic ran longer than {timeout:g} s and was "
                            f"stopped (raise --timeout)", path=logf) from e
        raise EmirError("EMIR-E642", f"{exe} cannot be started: {e}") from e
    seconds = time.time() - t0
    write_text(logf, (p.stdout or "") + (p.stderr or ""))
    if not os.path.isfile(paths["pex"]) or not os.path.isfile(paths["lumped"]):
        tail = ((p.stdout or "") + (p.stderr or ""))[-600:]
        raise EmirError("EMIR-E640", f"magic (exit {p.returncode}) wrote no extracted "
                        f"netlist for {cell}{crashed(exe, p.returncode, log)}; its log "
                        f"ends: {tail}", path=logf)
    if not os.path.isfile(paths["res_geo"]):
        raise EmirError("EMIR-E644", f"magic wrote {os.path.basename(paths['pex'])} but no "
                        f"{os.path.basename(paths['res_geo'])}: it extracted no net's "
                        f"resistance with geometry", path=logf)
    log(f"[emir] magic finished in {seconds:.1f} s")
    return {**paths, "log": logf, "script": script, "seconds": seconds}


def crashed(exe: str, returncode: int, log=print) -> str:
    """"" for a magic that exited, else what killed it (a signal): not a
    problem of the layout, and an old magic is the usual cause (EMIR-W640)."""
    if returncode >= 0:
        return ""
    import signal

    from . import site
    try:
        sig = signal.Signals(-returncode).name
    except ValueError:
        sig = f"signal {-returncode}"
    version = site.tool_version("magic", exe)
    warn = site.magic_warning(exe, version)
    if warn:
        notice("EMIR-W640", warn, log)
    return (f"; magic {version or '(version unknown)'} at {exe} CRASHED ({sig}): not a "
            f"problem of the layout -- " + (warn or "try a newer magic"))


# ------------------------------------------------------------------ SPICE text


def logical_lines(text: str) -> list:
    """`[(line number, line)]` with `+` continuations joined, comments and
    blank lines left out."""
    out: list = []
    for no, raw in enumerate(text.splitlines(), 1):
        s = raw.strip()
        if not s or s.startswith("*"):
            continue
        if s.startswith("+") and out:
            out[-1] = (out[-1][0], out[-1][1] + " " + s[1:].strip())
        else:
            out.append((no, s))
    return out


_SCALE = {"T": 1e12, "G": 1e9, "MEG": 1e6, "K": 1e3, "M": 1e-3, "MIL": 25.4e-6,
          "U": 1e-6, "N": 1e-9, "P": 1e-12, "F": 1e-15, "A": 1e-18}


def number(word: str) -> float:
    """A SPICE number (`1.95307f`, `2.2`, `3meg`) as a float."""
    m = re.match(r"^([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)([A-Za-z]*)$", word.strip())
    if not m:
        raise EmirError("EMIR-E644", f"magic wrote {word!r} where a number belongs: its "
                        f"output is not what EMIR reads")
    unit = m.group(2).upper()
    for s in ("MEG", "MIL", "T", "G", "K", "M", "U", "N", "P", "F", "A"):
        if unit.startswith(s):
            return float(m.group(1)) * _SCALE[s]
    return float(m.group(1))


def subckt_ports(text: str, top: str) -> tuple:
    """The `.subckt top` ports in order (its `+` continuations included)."""
    for _, line in logical_lines(text):
        w = line.split()
        if len(w) > 1 and w[0].lower() == ".subckt" and w[1].lower() == top.lower():
            return tuple(x for x in w[2:] if "=" not in x)
    raise EmirError("EMIR-E640", f"magic's netlist has no .subckt {top}: magic extracts the "
                    f"layout's cell by that name; check --cell")


def split_nets(split: str) -> set:
    """The nets of the resistance-split netlist: its ports' and every node's
    of its devices and resistors (`net_of`)."""
    out = set()
    for _, line in logical_lines(split):
        w = line.split()
        if w[0].lower() == ".subckt":
            out.update(x for x in w[2:] if "=" not in x)
        elif w[0][:1] in "Xx":
            out.update(net_of(n) for n in device_nodes(line)[1])
        elif w[0][:1] in "Rr" and len(w) >= 3:
            out.update(net_of(n) for n in w[1:3])
    return out


def lumped_names(split: str, lumped: str) -> dict:
    """Each node of the lumped netlist that the split one names otherwise ->
    the split one's net. magic names a net with no label after a place, and
    the two netlists write that name differently: `a_114_0#` in the lumped
    one, `a_114_0.t2`, `a_114_0.n5` ... in the split one. A name is matched by
    dropping the `#`, else by the device terminals it sits on (the same
    instance, the same position); a node matched by neither keeps its name."""
    nets = split_nets(split)
    out: dict = {}
    by_inst = {device_nodes(line)[0]: device_nodes(line)[1] for line in device_lines(split)}
    for line in device_lines(lumped):
        inst, nodes, _ = device_nodes(line)
        for j, n in enumerate(nodes):
            there = by_inst.get(inst)
            if n not in nets and there and j < len(there):
                out.setdefault(n, net_of(there[j]))
    for _, line in logical_lines(lumped):
        w = line.split()
        if w[0][:1] not in "Cc" or len(w) < 4:
            continue
        for n in w[1:3]:
            if n not in nets and n != "0" and n.endswith("#") and n[:-1] in nets:
                out[n] = n[:-1]
    return out


def _renamed(line: str, names: dict) -> str:
    """A capacitor line with its two nodes renamed (`lumped_names`)."""
    w = line.split()
    if len(w) < 3 or not names:
        return line
    return " ".join([w[0], names.get(w[1], w[1]), names.get(w[2], w[2]), *w[3:]])


def counted_once(split: str, lumped: str) -> str:
    """The resistance-split netlist with every capacitor taken from the lumped
    one of the same extraction: there each net's capacitance to the substrate
    is magic's `.ext` value, on its main node, and each coupling is counted
    once (SAF, `counted_once`). Each capacitor's nodes are the split
    netlist's nets (`lumped_names`): a net with no label keeps its
    capacitance."""
    if not lumped:
        return split
    is_cap = lambda line: line[:1].upper() == "C"                       # noqa: E731
    names = lumped_names(split, lumped)
    caps = [_renamed(line, names) for line in lumped.splitlines() if is_cap(line)]
    lines = [line for line in split.splitlines() if not is_cap(line)]
    end = max(k for k, line in enumerate(lines) if line.lower().startswith(".ends"))
    return "\n".join(lines[:end] + caps + lines[end:]) + "\n"


def floating_caps(spice: str) -> list:
    """The capacitor nodes of `spice` (the split netlist, its capacitors
    counted once) on no net of its devices, resistors or ports, nor ground:
    their capacitance charges nothing EMIR checks (EMIR-W646)."""
    nets = split_nets(spice)
    out = set()
    for _, line in logical_lines(spice):
        w = line.split()
        if w[0][:1] in "Cc" and len(w) >= 4:
            out.update(n for n in w[1:3] if n != "0" and net_of(n) not in nets)
    return sorted(out)


def subckts(spice: str) -> list:
    """The names of the `.subckt` definitions in an extracted netlist."""
    return [line.split()[1] for _, line in logical_lines(spice)
            if line.split()[0].lower() == ".subckt" and len(line.split()) > 1]


def net_of(node: str) -> str:
    """A node's net: `vss` for `vss`, `vss.t3` (a device terminal), `vss.n12`
    (a subnode) and `vss.p2` (a port's further pin)."""
    return _SUFFIX.sub("", node)


def device_lines(spice: str) -> list:
    """The device instance lines (`X ...`) of an extracted netlist."""
    return [line for _, line in logical_lines(spice) if line[:1] in "Xx"]


def device_nodes(line: str) -> tuple:
    """(instance, nodes, model) of an extracted device line."""
    words = [w for w in line.split() if "=" not in w]
    return words[0], words[1:-1], words[-1]


def terminal_names(model: str, n: int, table: dict | None) -> tuple:
    """The terminal names of a device of `model` with `n` nodes: the process
    file's `magic: devices:` entry (a model name or a glob such as
    `sky130_fd_pr__*fet*`; the exact name wins) when it has `n` names, else
    `t1 .. tn` (`guessed` true)."""
    table = table or {}
    names = table.get(model)
    if names is None:
        for pat, v in table.items():
            if fnmatch.fnmatchcase(model, str(pat)):
                names = v
                break
    if names is not None and len(names) == n:
        return tuple(str(x) for x in names), False
    return tuple(f"t{k + 1}" for k in range(n)), True


# ------------------------------------------------------------------ geometry


Point = tuple


@dataclass(frozen=True)
class Segment:
    """One extracted resistor between nodes `a` and `b`, on magic's `layer`.
    A wire (`ew`, `ns`, `diag`) is `width` across by `length` along, nm, from
    `ends[0]` to `ends[1]` on its centerline; a via array (`via`) has `cuts`
    (x, y) at `ends[0]`. `merged`: magic's simplifier combined it with others."""
    a: str
    b: str
    ohms: float
    layer: str
    kind: str
    width: int = 0
    length: int = 0
    cuts: tuple = (0, 0)
    ends: tuple = ((0, 0), (0, 0))
    merged: bool = False


@dataclass(frozen=True)
class Geometry:
    """An extraction's resistor network as drawn: its segments, every node's
    place (nm), each port's pin nodes (the port's own name first), and each
    node's share of its net's capacitance (`weights`, relative in the net)."""
    segments: tuple
    nodes: dict
    pins: dict
    weights: dict = field(default_factory=dict)
    nm_per_unit: float = 1.0


def read_geometry(res_ext: str, res_geo: str, ports) -> Geometry:
    """The network magic wrote (`<cell>.res.ext`, `<cell>.res.geo` texts)."""
    m = re.search(r"^scale \S+ \S+ (\S+)", res_ext, re.M)
    if not m:
        raise EmirError("EMIR-E644", "magic's .res.ext has no `scale` line: it is not a "
                        "resistance extraction")
    scale = float(m.group(1)) / 1e5                       # nm per magic unit
    nm = lambda v: round(int(v) * scale)                  # noqa: E731
    found = re.findall(r'^rnode "([^"]+)" \S+ (\S+) (-?\d+) (-?\d+)', res_ext, re.M)
    nodes = {name: (nm(x), nm(y)) for name, _, x, y in found}
    rows = [line.split() for line in res_geo.splitlines() if line.startswith("resgeo ")]
    bad = [r for r in rows if len(r) != 14]
    if bad:
        raise EmirError("EMIR-E644", f"{len(bad)} line(s) of magic's .res.geo are not "
                        f"`resgeo n1 n2 ohms layer dir width length cl x1 y1 x2 y2 merged` "
                        f"(e.g. {' '.join(bad[0])[:80]!r})")
    names = pin_names(rows, nodes, set(ports), nm)
    segments = []
    for w in rows:
        p1, p2 = (nm(w[9]), nm(w[10])), (nm(w[11]), nm(w[12]))
        a = names.get((w[1].strip('"'), p1), w[1].strip('"'))
        b = names.get((w[2].strip('"'), p2), w[2].strip('"'))
        nodes.setdefault(a, p1)
        nodes.setdefault(b, p2)
        segments.append(segment(w, a, b, (p1, p2), scale))
    segments = via_places(segments)
    pins: dict = {}
    for (name, _), new in sorted(names.items(),
                                 key=lambda kv: (kv[0][0], int(kv[1].rsplit(".p", 1)[1]))):
        pins.setdefault(name, [name]).append(new)
    return Geometry(tuple(segments), nodes, {p: tuple(v) for p, v in pins.items()},
                    weights({name: float(c) for name, c, _, _ in found}, segments), scale)


def weights(magic: dict, segments: list) -> dict:
    """Each node's share of its net's capacitance: magic's own (an rnode's)
    where the net has any, else its drawn area (each wire segment's, half to
    each end): magic gives none to the substrate's own net."""
    area: dict = {}
    for s in segments:
        if s.kind != "via":
            for node in (s.a, s.b):
                area[node] = area.get(node, 0.0) + s.width * s.length / 2
    total: dict = {}
    for node, c in magic.items():
        total[net_of(node)] = total.get(net_of(node), 0.0) + c
    out = {node: c for node, c in magic.items() if c > 0}
    out.update({node: a for node, a in area.items() if a > 0 and not total.get(net_of(node))})
    return out


def pin_names(rows, nodes: dict, ports: set, nm) -> dict:
    """(port, place) -> `<port>.pK` for each of a port's pin nodes but its
    first (magic's rnode for the port, else the lowest-left)."""
    places: dict = {}
    for w in rows:
        for name, x, y in ((w[1], w[9], w[10]), (w[2], w[11], w[12])):
            name, p = name.strip('"'), (nm(x), nm(y))
            if name in ports and p not in places.setdefault(name, []):
                places[name].append(p)
    out = {}
    for name, ps in places.items():
        first = nodes.get(name, min(ps))
        others = sorted(p for p in ps if p != first)
        out.update({(name, p): f"{name}.p{k}" for k, p in enumerate(others, start=2)})
    return out


def segment(w: list, a: str, b: str, ends: tuple, scale: float) -> Segment:
    """A resgeo record (split into words) as a Segment, nm, its ends on the
    wire's centerline (a via's: its two nodes' places, until `via_places`
    places it)."""
    ohms, layer, kind, merged = float(w[3]), w[4], w[5], w[13] == "1"
    if kind == "via":
        return Segment(a, b, ohms, layer, kind, cuts=(int(w[6]), int(w[8])),
                       ends=tuple(ends), merged=merged)
    width, length = round(int(w[6]) * scale), round(float(w[7]) * scale)
    cl = round(int(w[8]) * scale)
    return Segment(a, b, ohms, layer, kind, width, length,
                   ends=placed(kind, ends, cl, length, (a, b)), merged=merged)


def via_places(segments: list) -> list:
    """Each via at its cut: where its two nodes sit apart, the place of the
    node fewer vias of its layer share. magic gives a row of contacts from a
    diffusion one node, the strip's, at one place, and each cut its own node
    on the layer above: every contact was placed at the strip's node. Where
    both nodes are shared alike, the first node's place (magic's order)."""
    from collections import Counter
    share = Counter((s.layer, n) for s in segments if s.kind == "via" for n in (s.a, s.b))
    out = []
    for s in segments:
        if s.kind == "via":
            p1, p2 = s.ends
            k = 1 if p1 != p2 and share[(s.layer, s.b)] < share[(s.layer, s.a)] else 0
            s = Segment(s.a, s.b, s.ohms, s.layer, s.kind, cuts=s.cuts,
                        ends=(s.ends[k], s.ends[k]), merged=s.merged)
        out.append(s)
    return out


def placed(kind: str, ends: tuple, cl: int, length: int, names: tuple) -> tuple:
    """A wire's ends on its centerline. Its length is exact; a device
    terminal's node (`.tN`) sits at the device, so where the ends' span is not
    the length, the segment runs `length` from its other end toward it.
    Elsewhere the span is kept as magic placed the nodes, and it can be
    longer than the length: a node where the wire meets a wider shape sits at
    that shape's far edge (a met1 wire's node on the bar it joins; a poly
    gate's past the diffusion). Only the drawn place and the viewer see it:
    the resistance and the current density use the length and the width."""
    axis = {"ew": 0, "ns": 1}.get(kind)
    if axis is None:
        return ends
    p = [list(e) for e in ends]
    for e in p:
        e[1 - axis] = cl
    if abs(abs(p[1][axis] - p[0][axis]) - length) > 5:
        k = (1 if re.search(r"\.t\d+$", names[1])
             else 0 if re.search(r"\.t\d+$", names[0]) else None)
        if k is not None:
            sign = 1 if p[k][axis] >= p[1 - k][axis] else -1
            p[k][axis] = p[1 - k][axis] + sign * length
    return (p[0][0], p[0][1]), (p[1][0], p[1][1])


# ------------------------------------------------------------------ the DSPF


def um(nm) -> str:
    return f"{nm / 1000:.4f}".rstrip("0").rstrip(".") if nm else "0"


def at(g: Geometry, node: str) -> str:
    """A node's place, um, after its other fields ("" where magic placed none)."""
    return f" {um(g.nodes[node][0])} {um(g.nodes[node][1])}" if node in g.nodes else ""


def annotation(s: Segment) -> str:
    """A resistor's shape as DSPF parameters, um: SAF's (`$layer $dir $W $L
    $X $Y $X2 $Y2`, a via's `$cuts`), with a via's cut count `$N` and a wire's
    rectangle `$LLX $LLY $URX $URY`, which EMIR's reader takes as they are."""
    (x1, y1), (x2, y2) = s.ends
    if s.kind == "via":
        text = (f"$layer={s.layer} $cuts={s.cuts[0]}x{s.cuts[1]} "
                f"$N={max(s.cuts[0] * s.cuts[1], 1)} $X={um(x1)} $Y={um(y1)}")
    else:
        h = s.width / 2
        if s.kind == "ew":
            box = (min(x1, x2), y1 - h, max(x1, x2), y1 + h)
        elif s.kind == "ns":
            box = (x1 - h, min(y1, y2), x1 + h, max(y1, y2))
        else:
            # a diagonal wire (split tiles): its band lies around the line
            # between its nodes, which magic often places on one level (an
            # empty box): the ends' box, half the width around it
            box = (min(x1, x2) - h, min(y1, y2) - h, max(x1, x2) + h, max(y1, y2) + h)
        text = (f"$layer={s.layer} $dir={s.kind} $W={um(s.width)} $L={um(s.length)} "
                f"$X={um(x1)} $Y={um(y1)} $X2={um(x2)} $Y2={um(y2)} "
                f"$LLX={um(box[0])} $LLY={um(box[1])} $URX={um(box[2])} $URY={um(box[3])}")
    return text + (" $merged=1" if s.merged else "")


def terminals(devices: list, table: dict | None) -> tuple:
    """(net -> [(node, instance, terminal)], guessed models): each device
    terminal, named in the model's order (`terminal_names`)."""
    out: dict = defaultdict(list)
    guessed = set()
    for line in devices:
        inst, nodes, model = device_nodes(line)
        names, g = terminal_names(model, len(nodes), table)
        if g:
            guessed.add(model)
        for j, node in enumerate(nodes):
            out[net_of(node)].append((node, inst, names[j]))
    return out, sorted(guessed)


def write_dspf(path: str, top: str, ports, spice: str, g: Geometry, table: dict | None,
               pins: str = "tied", program: str = "") -> dict:
    """Write the extraction (`spice`: magic's split netlist with its
    capacitors counted once; `g`: its geometry) as DSPF to `path`. Returns
    what it wrote: nets, resistors, capacitors, devices, ties, guessed models."""
    from .output import write_text
    if pins not in ("tied", "separate"):
        raise EmirError("EMIR-E513", f"--port-pins {pins!r}: tied or separate")
    lines = logical_lines(spice)
    devices = [line for _, line in lines if line[:1] in "Xx"]
    caps = [(no, line) for no, line in lines if line[:1] in "Cc"]
    terms, guessed = terminals(devices, table)
    out = header(top, ports, program)
    body, stats = nets(ports, g, terms, caps)
    out += body
    out += ["* capacitors (F), each counted once, spread over their nets' nodes"]
    spread_lines = spread(caps, g)
    out += spread_lines
    out += ["", "* instances"] + devices
    ties = extra_pins(g, ports)
    if pins == "tied" and ties:
        out += ["", "* each further pin of a port, joined to it (0 V: its current is the pin's)"]
        out += [f"Vpin{k} {p} {port} 0" for k, (port, p) in enumerate(ties)]
    write_text(path, "\n".join(out + [f".ENDS {top}", ""]))
    return {**stats, "capacitors": len(spread_lines), "devices": len(devices),
            "ties": len(ties) if pins == "tied" else 0, "extra_pins": len(ties),
            "guessed_terminals": guessed}


def header(top: str, ports, program: str) -> list:
    return ["*|DSPF 1.3", f'*|DESIGN "{top}"', f'*|DATE "{datetime.date.today().isoformat()}"',
            '*|VENDOR "EMIR"', f'*|PROGRAM "{program or "emir extract --tool magic"}"',
            '*|VERSION "1"', "*|DIVIDER /", "*|DELIMITER :", "*|GROUND_NET 0",
            "* places and sizes in um, resistance in ohm, capacitance in F; each resistor",
            "* one segment as magic drew it (extresist geoout on, simplify off)", "",
            f".SUBCKT {top} {' '.join(ports)}", ""]


def nets(ports, g: Geometry, terms: dict, caps: list) -> tuple:
    """Each net's section: its capacitance, pins, instance terminals and
    subnodes (placed), and its resistors."""
    cap: dict = defaultdict(float)
    for _, line in caps:
        w = line.split()
        for node in {net_of(w[1]), net_of(w[2])} - {"0"}:
            cap[node] += number(w[3])
    by_net: dict = defaultdict(list)
    for s in g.segments:
        by_net[net_of(s.a)].append(s)
    out, k = [], 0
    unplaced = 0
    diagonal: list = []
    for net in sorted(set(by_net) | set(terms) | set(cap)):
        pins = g.pins.get(net, (net,)) if net in ports else ()
        named = set(pins) | {node for node, _, _ in terms.get(net, [])}
        subs = sorted({n for s in by_net[net] for n in (s.a, s.b)} - named)
        out.append(f"*|NET {net} {cap[net]:.6g}")
        out += [f"*|P ({p} B 0{at(g, p)})" for p in pins]
        for node, inst, pin in terms.get(net, []):
            unplaced += node not in g.nodes
            out.append(f"*|I ({node} {inst} {pin} B 0{at(g, node)})")
        out += [f"*|S ({n}{at(g, n)})" for n in subs]
        for s in by_net[net]:
            out.append(f"R{k} {s.a} {s.b} {s.ohms:.6g} {annotation(s)}")
            if s.kind == "diag":
                diagonal.append(f"R{k}")
            k += 1
        out.append("")
    return out, {"nets": len(set(by_net) | set(terms) | set(cap)), "resistors": k,
                 "nets_with_resistors": len(by_net), "unplaced_terminals": unplaced,
                 "diagonal": diagonal}


def spread(caps: list, g: Geometry) -> list:
    """The capacitors spread over their nets' nodes, summed per node pair."""
    groups = weighted(g)
    parts: dict = defaultdict(float)
    for _, line in caps:
        w = line.split()
        farads = number(w[3])
        for this, other in ((w[1], w[2]), (w[2], w[1])):
            for node, to, share in pieces(groups, g, this, other):
                parts[(node, to)] += farads / 2 * share
    return [f"C{k} {a} {b} {f:.6g}" for k, ((a, b), f) in enumerate(parts.items())]


def weighted(g: Geometry) -> dict:
    """net -> its weighted nodes, their places (nm) and their shares (sum 1)."""
    by: dict = defaultdict(list)
    for node in g.weights:
        if node in g.nodes:
            by[net_of(node)].append(node)
    out = {}
    for net, names in by.items():
        w = np.array([g.weights[n] for n in names])
        out[net] = (names, np.array([g.nodes[n] for n in names], dtype=float), w / w.sum())
    return out


def pieces(groups: dict, g: Geometry, this: str, other: str) -> list:
    """`this` side's half of a capacitor to `other` (a net's main node, or
    "0"): (node, its other end, share). A net with no weighted node gives its
    half from its main node."""
    if net_of(this) in groups:
        names, xy, share = groups[net_of(this)]
    else:
        names, share = [this], [1.0]
        xy = np.array([g.nodes[this]], dtype=float) if this in g.nodes else None
    if other == "0" or xy is None:
        ends = [other] * len(names)
    else:
        ends = [e or other for e in nearest(groups, net_of(other), xy)]
    return [(n, e, float(f)) for n, e, f in zip(names, ends, share)]


def nearest(groups: dict, net: str, xy) -> list:
    """For each place, `net`'s nearest weighted node ("" when it has none)."""
    if net not in groups:
        return [""] * len(xy)
    names, there, _ = groups[net]
    out = []
    for k in range(0, len(xy), 512):                     # in blocks: memory stays small
        d = ((xy[k:k + 512, None, :] - there[None, :, :]) ** 2).sum(axis=2)
        out += [names[j] for j in d.argmin(axis=1)]
    return out


def extra_pins(g: Geometry, ports) -> list:
    return [(port, p) for port in ports for p in g.pins.get(port, ())[1:]]


def magic_layers(proc_data: dict | None) -> dict:
    """The process file's `magic: layers:` map (magic's names -> its own)."""
    return dict(((proc_data or {}).get("magic") or {}).get("layers") or {})


def device_table(proc_data: dict | None) -> dict:
    """The process file's `magic: devices:` map (model or glob -> terminals)."""
    return dict(((proc_data or {}).get("magic") or {}).get("devices") or {})


def convert(work: str, cell: str, proc_data: dict | None, pins: str = "tied",
            out_dir: str | None = None, log=print) -> dict:
    """The DSPF from what magic wrote in `work` for `cell` (`<cell>_pex.spice`,
    `_lumped.spice`, `.res.ext`, `.res.geo`). Runs nothing: what CI does with
    committed magic outputs."""
    out_dir = out_dir or work

    def read(name):
        p = os.path.join(work, name)
        if not os.path.isfile(p):
            raise EmirError("EMIR-E101", f"magic output not found: {p}", path=p)
        with open(p, encoding="utf-8", errors="replace") as f:
            return f.read()
    pex, lumped = read(f"{cell}_pex.spice"), read(f"{cell}_lumped.spice")
    res_ext, res_geo = read(f"{cell}.res.ext"), read(f"{cell}.res.geo")
    for text, name in ((pex, f"{cell}_pex.spice"), (lumped, f"{cell}_lumped.spice")):
        defs = subckts(text)
        if len(defs) > 1:
            # a hierarchy magic extracted cell by cell: the netlists would mix
            # the subcells' own nets with the top's, and no simulator reads them
            raise EmirError("EMIR-E647", f"magic's {name} defines {len(defs)} subcircuits "
                            f"({', '.join(defs[:4])}{' ...' if len(defs) > 4 else ''}): the "
                            f"layout was extracted as a hierarchy, and EMIR reads a flat "
                            f"cell; extract it again with emir extract --tool magic, which "
                            f"flattens it first", path=os.path.join(work, name))
    spice = counted_once(pex, lumped)
    ports = subckt_ports(spice, cell)
    g = read_geometry(res_ext, res_geo, ports)
    dspf = os.path.join(out_dir, f"{cell}.dspf")
    stats = write_dspf(dspf, cell, ports, spice, g, device_table(proc_data), pins=pins)
    layers = sorted({s.layer for s in g.segments})
    mapped = magic_layers(proc_data)
    merged = sum(s.merged for s in g.segments)
    if stats["guessed_terminals"]:
        notice("EMIR-W643", f"{len(stats['guessed_terminals'])} device model(s) have no "
               f"terminal names in the process file's magic: devices: "
               f"{', '.join(stats['guessed_terminals'][:6])}: their *|I pins are named t1, "
               f"t2 ... by position", log)
    if merged:
        notice("EMIR-W644", f"{merged} of {len(g.segments)} resistors were merged by magic's "
               f"simplifier: their width and length are not one drawn shape's", log)
    floating = floating_caps(spice)
    if floating:
        notice("EMIR-W646", f"{len(floating)} capacitor node(s) are on no net of the "
               f"extraction's devices, resistors or ports: "
               f"{', '.join(floating[:6])}{' ...' if len(floating) > 6 else ''}: their "
               f"capacitance charges nothing EMIR checks", log)
    if stats["diagonal"]:
        dg = stats["diagonal"]
        notice("EMIR-W647", f"{len(dg)} resistor(s) lie on diagonal (non-Manhattan) shapes: "
               f"{', '.join(dg[:6])}{' ...' if len(dg) > 6 else ''}: magic's width there "
               f"is its tiles' extent, not the width across the current, so their current "
               f"density can read low (29 % on a 45-degree band) and their resistance is "
               f"magic's estimate; `emir run` lists them as not checked", log)
    devnets = sorted({net_of(n) for line in device_lines(spice)
                      for n in device_nodes(line)[1]})
    bare = [n for n in devnets if n not in {net_of(s.a) for s in g.segments}]
    return {"dspf": dspf, "ports": list(ports), **stats,
            "segments": len(g.segments), "merged": merged,
            "layers": [{"magic": name, "process": mapped.get(name)} for name in layers],
            "nets_without_resistance": bare, "floating_capacitance": floating,
            "pins": pins, "nm_per_unit": g.nm_per_unit}


# ------------------------------------------------------------------ the command


def _stderr(line):
    import sys
    print(line, file=sys.stderr)


def _cell(gds: str, cell: str | None, argv) -> str:
    """The cell to extract: --cell, checked against the layout when the
    klayout package can read it, else the layout's only top cell."""
    from . import gds as G
    try:
        G.klayout_db()
    except EmirError:
        if not cell:
            raise EmirError("EMIR-E507", "--tool magic without --cell needs the klayout "
                            "package to find the layout's top cell: give --cell",
                            command="extract") from None
        if not os.path.isfile(gds):
            raise EmirError("EMIR-E101", f"layout not found: {gds}", path=gds) from None
        return cell
    _, c = G.open_cell(gds, cell, command="extract", argv=argv)
    return c.name


def unchecked_layers(dspf_path: str, proc) -> list:
    """The DSPF's layer tokens the process file does not map (`emir run`
    carries their current and reports them as not checked, EMIR-W307)."""
    from .dspf import map_layers, parse
    d = parse(dspf_path)
    lmap = map_layers(d, proc)
    return [t for t in d.layer_tokens() if t not in lmap]


def extract(a, argv, out) -> int:
    """`emir extract --tool magic`: magic's extraction with every resistor's
    geometry, then the DSPF (`convert`)."""
    from . import site
    from .engine import Process
    from .output import command_line, document, safe_component
    if not os.path.isfile(a.gds):
        raise EmirError("EMIR-E101", f"layout not found: {a.gds}", path=a.gds)
    if not os.path.isfile(a.process):
        raise EmirError("EMIR-E101", f"process file not found: {a.process}", path=a.process)
    proc = Process.load(a.process, log=_stderr)
    data = proc.data
    cell = check_cell(_cell(a.gds, a.cell, argv))
    rcfile = magicrc_for(data, a.magicrc)
    m = find_magic(data, a.magic, log=_stderr)
    _stderr(f"[emir] magic: {m['path']} ({m['source']}, version {m['version'] or '?'}): "
            f"writes resistor geometry")
    root = site.get("work_dir", default="emir_out")[0] or "emir_out"
    out_dir = os.path.abspath(a.out or os.path.join(str(root),
                                                    f"extract_{safe_component(cell)}"))
    # magic runs in a directory of EMIR's own under --out; the DSPF goes into
    # --out, never over a file EMIR did not write
    work = os.path.join(out_dir, WORK_DIR)
    guard_outputs(out_dir, cell)
    ran = run_magic(a.gds, cell, rcfile, m["path"], work, tolerance=a.tolerance,
                    timeout=a.timeout, log=_stderr)
    res = convert(work, cell, data, pins=a.port_pins, out_dir=out_dir, log=_stderr)
    unchecked = unchecked_layers(res["dspf"], proc)
    if res["nets_without_resistance"]:
        bare = res["nets_without_resistance"]
        notice("EMIR-W645", f"{len(bare)} net(s) with devices have no resistor in magic's "
               f"network (extresist did not split them at --tolerance {a.tolerance:g}): "
               f"{', '.join(bare[:8])}{' ...' if len(bare) > 8 else ''}: EMIR cannot check "
               f"them", _stderr)
    print(f"emir extract --tool magic: {cell}")
    print(f"  magic    {m['path']} ({m['source']})")
    print(f"  dspf     {res['dspf']}  ({res['resistors']} R in {res['nets_with_resistors']} "
          f"net(s), {res['capacitors']} C, {res['devices']} devices)")
    print(f"  ports    {' '.join(res['ports'])}"
          + (f"  ({res['extra_pins']} further pin(s), {a.port_pins})"
             if res["extra_pins"] else ""))
    if unchecked:
        print(f"  not checked (no limits in {os.path.basename(a.process)}): "
              f"{', '.join(unchecked)} (their current is in the IR drop)")
    fmt = out.fmt_args()
    steps = [
        {"why": "the nets and their sizes in the new DSPF",
         "command": command_line("nets", res["dspf"], fmt)},
        {"why": "what to run next with this DSPF: the currents (a simulation of the DSPF "
                "itself, every branch current saved; or DC currents); --gds draws the "
                "layout under the charts",
         "command": command_line("next", "--dspf", res["dspf"], "--process", a.process,
                                 "--gds", a.gds, "--cell", cell, fmt)}]
    files = {k: ran[k] for k in ("lumped", "pex", "ext", "res_ext", "res_geo", "log",
                                 "script")}
    out.emit(document(
        "extract", tool="magic", dry_run=False, cell=cell, gds=os.path.abspath(a.gds),
        process=os.path.abspath(a.process), work_dir=work, out_dir=out_dir,
        dspf=res["dspf"], rc_netlist=res["dspf"],
        magic={"path": m["path"], "source": m["source"], "version": m["version"],
               "tried": m["tried"]},
        magicrc=rcfile, files=files, seconds=round(ran["seconds"], 3),
        ports=res["ports"], port_pins=a.port_pins, tolerance=a.tolerance,
        nets=res["nets"], nets_with_resistors=res["nets_with_resistors"],
        resistors=res["resistors"], capacitors=res["capacitors"], devices=res["devices"],
        extra_pins=res["extra_pins"], ties=res["ties"], segments=res["segments"],
        merged=res["merged"], layers=res["layers"], unchecked_layers=unchecked,
        nets_without_resistance=res["nets_without_resistance"],
        floating_capacitance=res["floating_capacitance"],
        diagonal_resistors=res["diagonal"],
        guessed_terminals=res["guessed_terminals"],
        provenance="magic's own extraction (open source), every resistor one drawn segment "
                   "(extresist geoout on, simplify off); the DSPF written by EMIR",
        next_steps=steps))
    return 0
