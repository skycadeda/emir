"""Self-consistency: close the loop between rail droop and device current.

The CC simulation's one approximation is that every device sees the ideal
rail. It does not: it sees the rail minus whatever the grid drops on the way
to it, and that drop moves in time with the currents. This module closes the
loop without ever running an RCC transient: each instance pin gets its own
supply source in series with the testbench's rail, at the pin's own droop
**waveform**, and the CC testbench is simulated again until the droop fed in
equals the droop the currents produce. The loop itself (the companion
update, the stopping rule, the guard, KCL, several nets) is `emir.tdloop`;
the PWL constructs are `emir.pwl`. This module writes the split testbench
and runs the simulator for each pass.

**Why a waveform, not the average droop.** An earlier version fed back each
pin's *average* droop, on the reasoning that the R-only grid has no decap, so
its droop waveform would over-correct, and that the dynamic part (rms, peak)
needs the RC grid, not this loop. The measurements contradict both
(`examples/06_big_block/results/selfconsist_waveform.md`). On those meshes
(2 fF of grid capacitance per node) the rms and peak error of the open loop
does not come from the grid's capacitance (removing every grid capacitor from
RCC moved the governing branch by at most 0.67 points at 4.1 % droop) but
from the device currents, which the ideal rail
makes too high and too narrow: each device's own contact resistor degenerates
its source, neighbours switching in the same phase pull the shared grid down,
and idle devices give charge back (an inverter whose output is high is its
load behind its ON PMOS: a decap). All three need the pin to move *in time*,
and fed back as waveforms they converge (with the companion update where the
loop gain is above 1). The average loop over-corrected the averages: a
switching inverter's load holds the rail at the moment its PMOS turns off,
not the average rail, and at low droop the open loop's small average error is
the gate-overlap kick through the contact, not droop
(`results/selfconsist.csv`, the record of that loop: the governing branch's
avg went from +1.34 % to -2.02 % of RCC at 4.1 % droop, below it, and rms and
peak kept about three quarters of their error). The waveform loop took the
4.1 % mesh to +0.01 % (avg), +0.34 % (rms) and +0.78 % (peak) of RCC. On a
grid with more capacitance the droop fed back must hold it too: from an RC
DSPF the loop feeds back the RC grid's droop; from an R-only one it cannot,
and says so (EMIR-W544). The branches are then scored through the RC grid only
with --dynamic (decap-aware EM); without it they are scored on the R-only grid,
and the run says so (EMIR-W545).

The open loop's error has been measured elsewhere too: at the original
development site, on a generated test block (a bank of inverters tapping a
segmented metal supply rail, the CC-versus-RCC acceptance gate), it was
about +1.5 % on the worst branch current, in the conservative direction; on
an extracted OTA there it was +0.6 % to +1.1 %.

**Each pin's source is in series with the testbench's own rail.** The split
puts a voltage source between the rail node the testbench drives and the
pin's own node, at the pin's droop (0 V on pass 0). So pass 0 is the
testbench's open loop whatever drives its rail (an ideal source, a package
resistance, a ramp), and every later pass adds only the grid's droop to it.
Pass 0 reproduces the unsplit open loop to the simulator's step control
(`tests/test_tdloop_sims.py`). `--vdd`, the rail's voltage in the testbench,
is checked against the testbench's source where it can be read (EMIR-E523),
or read from it when it is not given.

**The sign is general.** The droop is the pin node's solved potential
relative to the feed: negative on a supply net (the pin's supply drops),
positive on a ground net (it rises). There is no separate VDD/VSS case, and
several nets (VDD and VSS together, `--net VDD,VSS`) close in one loop.

**How the netlist is rewritten: it is not, between passes.** Each pass
writes only the droop file(s):

* Xyce, ngspice, HSPICE: emir writes the split itself (`split_testbench`),
  with `emir probe`'s netlist machinery: every device terminal on the net
  that is one of the DSPF's instance pins is moved to a node of its own, fed
  from the rail through `VEMIRSC<k>` and a 0 V probe `VEMIR<k>` that
  measures its current; another terminal of the same device on the net (a
  bulk) follows its pin. A terminal on the net that is no DSPF pin would
  stay on the ideal rail: refused (EMIR-E540). The `VEMIRSC<k>` lines
  themselves are the per-pass include (`--droop-file`, default
  `droop.inc`, HSPICE `droop.sp`), which the split's subcircuit includes and
  each pass rewrites with every pin's droop as an inline PWL. The person's
  testbench is read, never changed: a copy is written (the deck, and copies
  of the files the split touches). The HSPICE deck is written from the
  HSPICE manuals' syntax and is UNVERIFIED until `emir verify-sim --sim
  hspice --run` passes where HSPICE runs.
* Spectre: the person adds the per-pin split to the CC netlist once
  (`rail_split_fragment`, `emir run --split-stub`): one `vsource type=pwl
  file=` per pin, reading a two-column file emir rewrites every pass beside
  the manifest `droop.scs` (in `emir_out/selfconsist/<net>/` unless
  --droop-file says where). emir checks that the netlist names every pin's
  file and connects every split node, runs Spectre on that netlist, and keeps
  each pass's result as `pass<k>.raw`. UNVERIFIED until checked where
  Spectre runs.

Spectre and HSPICE need a licence: the loop runs them only with `--run`;
without it the files are written and the command printed (`plan`).
"""
from __future__ import annotations

import os
import re
import shutil
import sys

import numpy as np

from . import ccsim, pwl, tdloop
from .errors import EmirError, notice

#: the simulators the loop drives; the SPICE dialects get the split from emir
SIMS = ("spectre", "xyce", "ngspice", "hspice")
SPICE_SIMS = ("xyce", "ngspice", "hspice")
LICENSED = ("spectre", "hspice")
#: the loop's defaults (`emir.tdloop`): simulations (pass 0, the open loop,
#: included), and the stopping rule: the residual below max(TOL_REL x the
#: largest droop, TOL_MV)
MAX_PASSES = tdloop.MAX_PASSES
TOL_MV = tdloop.TOL_MV
TOL_REL = tdloop.TOL_REL
#: the prefix of the split's supply sources and of the nodes they drive
SPLIT_SOURCE = "VEMIRSC"
SPLIT_NODE = "__emirsc"
#: how far --vdd may sit from the testbench's source before the run refuses
#: (EMIR-E523): 1 mV, or 1 % of the rail
RAIL_TOL_V = 1e-3
RAIL_TOL_REL = 0.01
#: the first line of every droop file emir writes (it overwrites only these)
HEADER = "emir.selfconsist"
#: the testbench's relative tolerance above which the loop warns (EMIR-W540)
RELTOL_MAX = 1e-4


def _stderr(line):
    print(line, file=sys.stderr)


# ------------------------------------------------------------------ names


def param_name(tap: str) -> str:
    """`MP0:s` -> `vd_MP0_s`, a legal Spectre (and SPICE) name: the name of
    the pin's PWL file (Spectre)."""
    return "vd_" + re.sub(r"\W", "_", tap)


def param_names(pins) -> dict:
    """`{pin: name}` for `pins`, unique without regard to case (SPICE
    compares names so): `MP0:s` and `MP0_s` would both be `vd_MP0_s`, so the
    second becomes `vd_MP0_s_2`."""
    out, used = {}, set()
    for p in pins:
        base = param_name(p)
        name, k = base, 1
        while name.lower() in used:
            k += 1
            name = f"{base}_{k}"
        used.add(name.lower())
        out[p] = name
    return out


def node_name(tap: str) -> str:
    return "VT_" + re.sub(r"\W", "_", tap)


def _guard(path: str):
    """Refuse (EMIR-E526) to overwrite a droop file emir did not write."""
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            first = f.readline()
    except OSError:
        first = ""
    if HEADER not in first:
        raise EmirError("EMIR-E526", f"{path} exists and emir did not write it: the "
                        f"self-consistent loop rewrites its droop file on every pass "
                        f"and will not overwrite this one", path=path)


def _fed_wave(fed: dict | None, pin: str) -> tuple:
    """(times or None, values) of `pin` in the loop's `fed` (None times:
    0 V, the open loop)."""
    if not fed or fed.get("t") is None:
        return None, None
    v = fed["v"].get(pin)
    if v is None:
        return None, None
    return fed["t"], v


def write_droop(path: str, sources: list, fed: dict | None, npass: int = 0,
                sim: str = "xyce", guard: bool = False) -> str:
    """The per-pass include of the SPICE dialects: the split's sources
    (`sources`: [{source, node, rail, pin}]), each at its pin's droop
    waveform as an inline PWL (`fed`: the loop's; None or no times: 0 V, the
    open loop). `guard`: refuse (EMIR-E526) to overwrite a file there that
    emir did not write (the run guards its first write, at the default path
    or the one --droop-file names)."""
    from .output import write_text
    if guard:
        _guard(path)
    L = [f"* {HEADER} -- pass {npass}: each split pin's droop waveform (V, relative to "
         f"its rail; 0 on pass 0). emir rewrites this file on every pass ({sim})"]
    for s in sources:
        t, v = _fed_wave(fed, s["pin"])
        L.append(pwl.spice_source(s["source"], s["node"], s["rail"], t, v, sim=sim))
    return write_text(path, "\n".join(L) + "\n")


#: beside each pass's result: emir's stamp, written before the pass runs
STAMP_EXT = ".emir"


def claim(result: str, k: int, also=()) -> str:
    """Before pass `k` writes `result` (with its `.log` and `.names`, and
    `also`: other files the pass replaces), refuse (EMIR-E526) when one of
    them exists that emir did not write -- no stamp `<result>.emir` of emir's
    beside it -- and otherwise write the stamp. A file the person (or another
    tool) left there is never overwritten or removed silently, whether the
    directory is the default one or the --droop-file's."""
    from .output import write_text
    _refuse_theirs(result, k, also)
    stamp = result + STAMP_EXT
    write_text(stamp, f"{HEADER}: pass {k} writes {os.path.basename(result)} here (and its "
                      f".log and .names); emir replaces them on its next run\n")
    return stamp


def _refuse_theirs(result: str, k: int, also=()):
    """EMIR-E526 when `result` (with its `.log` and `.names`, and `also`)
    exists without emir's stamp `<result>.emir` beside it (`claim`)."""
    stamp = result + STAMP_EXT
    mine = False
    if os.path.isfile(stamp):
        try:
            with open(stamp, encoding="utf-8", errors="replace") as f:
                mine = HEADER in f.readline()
        except OSError:
            mine = False
    if mine:
        return
    theirs = [p for p in (result, result + ".log", result + ".names", *also)
              if os.path.lexists(p)]
    if theirs:
        raise EmirError("EMIR-E526", f"{theirs[0]} exists and emir did not write it: pass "
                        f"{k} of the self-consistent loop writes its result there and "
                        f"will not overwrite it", path=theirs[0])


def guard_passes(driver, max_passes: int):
    """Before pass 0 simulates: refuse (EMIR-E526) a later pass's result
    (`pass<k>.raw`, its `.log` or `.names`, k < `max_passes`) that emir did
    not write, as `claim` would before that pass -- not after the passes
    before it have simulated (a licensed run, perhaps) for nothing. Nothing
    is written: each pass still claims its own files."""
    for k in range(1, max_passes):
        _refuse_theirs(driver.pass_result(k), k)


def drop_stale_passes(driver) -> list:
    """Before pass 0 simulates, after `guard_passes`: remove the later passes
    of an earlier run in the loop's directory (`pass<k>` k >= 1, with its
    `.log`, `.names` and stamp) that emir's stamp marks as its own. A shorter
    run would otherwise leave them beside its own, the directory mixing two
    runs (their stamps say emir replaces them on its next run: this is that
    run). A file without emir's stamp is never touched. Returns what was
    removed."""
    first = driver.pass_result(0)
    where = os.path.dirname(first)
    ext = os.path.splitext(first)[1]
    pat = re.compile(r"^pass(\d+)" + re.escape(ext) + "$")
    gone = []
    try:
        names = os.listdir(where)
    except OSError:
        return gone
    for name in sorted(names):
        m = pat.match(name)
        if not m or int(m.group(1)) < 1:
            continue
        result = os.path.join(where, name)
        stamp = result + STAMP_EXT
        try:
            with open(stamp, encoding="utf-8", errors="replace") as f:
                mine = HEADER in f.readline()
        except OSError:
            mine = False
        if not mine:
            continue
        for p in (result, result + ".log", result + ".names", stamp):
            if os.path.isdir(p) and not os.path.islink(p):
                shutil.rmtree(p, ignore_errors=True)
            elif os.path.lexists(p):
                os.remove(p)
            else:
                continue
            gone.append(p)
    return gone


def guard_split(stamp: str):
    """A `Writer.write` check for the split testbench's files: refuse
    (EMIR-E526) a file there that emir did not write -- not named in emir's
    stamp `stamp` from an earlier run -- before any is written, then stamp
    the ones it is about to write."""
    def check(paths):
        from .output import write_text
        mine: set = set()
        try:
            with open(stamp, encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
            if lines and HEADER in lines[0]:
                mine = {ln.strip() for ln in lines[1:] if ln.strip()}
        except OSError:
            pass
        theirs = [p for p in paths
                  if os.path.lexists(p) and os.path.basename(p) not in mine]
        if theirs:
            raise EmirError("EMIR-E526", f"{theirs[0]} exists and emir did not write it: "
                            f"the self-consistent loop writes its split testbench there and "
                            f"will not overwrite it", path=theirs[0])
        write_text(stamp, f"{HEADER}: the split testbench emir writes here (it replaces "
                          f"these files on its next run)\n"
                   + "".join(os.path.basename(p) + "\n" for p in paths))
    return check


def write_spectre_droop(manifest: str, files: dict, fed: dict | None, npass: int = 0,
                        guard: bool = False) -> str:
    """Spectre: every pin's two-column PWL file (`files`: {pin: path}),
    and the manifest `manifest` that says which pass wrote them (it is what
    E526 guards: a file without emir's header is never overwritten)."""
    from .output import write_text
    if guard:
        _guard(manifest)
        for p in files.values():
            if os.path.exists(p) and not os.path.exists(manifest):
                raise EmirError("EMIR-E526", f"{p} exists and emir did not write it: the "
                                f"self-consistent loop rewrites each pin's PWL file on "
                                f"every pass and will not overwrite this one", path=p)
    for pin, p in files.items():
        t, v = _fed_wave(fed, pin)
        if t is None:
            t, v = [0.0, 1.0], [0.0, 0.0]
        pwl.write_columns(p, t, v)
    return write_text(manifest, f"// {HEADER} -- pass {npass}: each pin's droop waveform "
                                f"(V, relative to its rail) is in its .pwl file here\n"
                      + "".join(f"// {pin}: {os.path.basename(p)}\n"
                                for pin, p in sorted(files.items())))


def net_list(a) -> list:
    """The nets of a run's --net: one, or with --self-consistent several
    (`--net VDD,VSS`, closed in one loop)."""
    net = a.net
    if getattr(a, "self_consistent", False) and net and "," in net:
        # a net named twice is closed once (each net's loop state is its own)
        return list(dict.fromkeys(n for n in (x.strip() for x in net.split(",")) if n))
    return [net]


def default_dir(net) -> str:
    """Where the loop writes for `net` (a name, or a list: several nets in one
    loop, `VDD+VSS`) unless --droop-file says otherwise:
    `emir_out/selfconsist/<net>` (absolute)."""
    from .output import safe_component
    name = net if isinstance(net, str) else "+".join(net)
    return os.path.abspath(os.path.join("emir_out", "selfconsist", safe_component(name)))


def pwl_files(pins, directory: str) -> dict:
    """{pin: its PWL file} (Spectre): `<directory>/<name>.pwl`."""
    names = param_names(sorted(pins))
    return {p: os.path.join(directory, names[p] + ".pwl") for p in sorted(pins)}


def rail_split_fragment(dspf, net: str, v_nominal: float | None = None,
                        droop_file: str | None = None) -> str:
    """The Spectre netlist edit a user has to make once, spelled out.

    Every instance pin on `net` gets its own node, fed from the net's node
    through its own PWL source, reading the pin's droop waveform from a file
    emir rewrites every pass (0 V on pass 0: the split netlist is the
    unsplit one plus a 0 V source per pin, so pass 0 is the open loop
    whatever drives the rail). A device's other terminals on the net (a
    bulk) move with its pin, as the SPICE split moves them. (Xyce, ngspice
    and HSPICE need no hand edit: `split_testbench` writes it.) `v_nominal`
    is not used (the sources are relative to the rail); kept for old
    callers. The `vsource type=pwl file=` construct is UNVERIFIED until
    checked where Spectre runs."""
    taps = dspf.net_taps(net)
    droop_file = droop_file or os.path.join(default_dir(net), "droop.scs")
    files = pwl_files({f"{t['inst']}:{t['pin']}" for t in taps}, os.path.dirname(droop_file))
    L = [f"// --- per-tap rail split for net {net} ({len(taps)} taps) ---",
         f"// emir rewrites each pin's .pwl file in {os.path.dirname(droop_file)} on every "
         f"pass: the pin's droop waveform in V (time value, one point a line), 0 on pass 0"]
    for t in taps:
        nm = f"{t['inst']}:{t['pin']}"
        src = "V" + re.sub(r"\W", "_", nm)
        L.append(f'{src} ({node_name(nm)} {net}) vsource type=pwl file="{files[nm]}"')
    L.append(f"// {net} above is the net's node in your CC netlist: use that node's name if "
             f"it differs there.")
    L.append("// then reconnect each pin below to its node above, and move the same "
             "device's other terminals on this net (a bulk tied to the rail) to that "
             "node too, as the SPICE split does:")
    for t in taps:
        nm = f"{t['inst']}:{t['pin']}"
        L.append(f"//   {t['inst']} terminal '{t['pin']}'  ->  {node_name(nm)}")
    return "\n".join(L)


def _spectre_texts(netlist: str, skip=()) -> list:
    seen, texts, todo = set(), [], [os.path.abspath(netlist)]
    inc = re.compile(r"""^\s*\.?(?:include|ahdl_include)\s+["']?([^"'\s]+)""", re.I | re.M)
    skip = {os.path.abspath(s) for s in skip}
    while todo and len(seen) < 64:
        p = todo.pop()
        if p in seen or not os.path.isfile(p):
            continue
        seen.add(p)
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        texts.append(text)
        for m in inc.finditer(text):
            t = os.path.expanduser(m.group(1))
            t = t if os.path.isabs(t) else os.path.join(os.path.dirname(p), t)
            if os.path.abspath(t) not in skip:
                todo.append(os.path.abspath(t))
    return texts


def spectre_split_problems(netlist: str, droop_path: str, pins) -> list:
    """What keeps a Spectre netlist from closing the loop, read from its text
    (and the files it includes): it does not name a pin's PWL file (the
    stub's `vsource type=pwl file=` lines), or a split node (`VT_<pin>`) is
    on fewer than two statements (its source and the pin it feeds: pasting
    the stub without reconnecting the pins leaves every node on its source
    alone). [] when the split is there."""
    every = "\n".join(_spectre_texts(netlist, skip=(droop_path,)))
    lines = [ln for ln in every.splitlines() if not ln.lstrip().startswith(("//", "*"))]
    body = "\n".join(lines)
    out = []
    files = pwl_files(pins, os.path.dirname(os.path.abspath(droop_path)))
    unnamed = [os.path.basename(f) for f in files.values()
               if os.path.basename(f) not in body]
    if unnamed:
        out.append(f"it does not read {len(unnamed)} pin(s)' PWL file(s): "
                   f"{', '.join(unnamed[:6])}{' ...' if len(unnamed) > 6 else ''} (the "
                   f"stub's `vsource type=pwl file=` lines)")
    alone = []
    for p in sorted(pins):
        node = node_name(p)
        pat = re.compile(r"(?<![\w.])" + re.escape(node) + r"(?![\w])")
        if sum(1 for ln in lines if pat.search(ln)) < 2:
            alone.append(node)
    if alone:
        out.append(f"{len(alone)} split node(s) are on fewer than two statements, so no "
                   f"pin is connected to them: {', '.join(alone[:6])}"
                   f"{' ...' if len(alone) > 6 else ''} (reconnect each pin to its node, "
                   f"as the stub's comments list)")
    return out


_RELTOL = re.compile(r"\breltol\s*=\s*([^\s,)]+)", re.I)


def reltol_of(texts: list, sim: str) -> tuple:
    """(the relative tolerance a testbench sets, where) read from its text:
    Xyce's `.options timeint reltol=`, ngspice's and HSPICE's `.option(s)
    reltol=`, Spectre's `reltol=` (an options statement); (None, why) when it
    sets none (the simulator's default applies)."""
    found = None
    for text in texts:
        for ln in text.splitlines():
            s = ln.strip()
            if not s or s.startswith(("*", "//")):
                continue
            low = s.lower()
            if sim == "xyce" and not re.match(r"^\.options?\s+timeint\b", low):
                continue
            if sim in ("ngspice", "hspice") and not re.match(r"^\.opt(ion|ions)?\b", low):
                continue
            m = _RELTOL.search(s)
            if m:
                v = _number(m.group(1))
                if v is not None:
                    found = (v, s)
    if found is None:
        return None, "not set"
    return found


def reltol_notice(texts: list, sim: str, where: str, log=_stderr):
    """EMIR-W540 when the testbench's reltol is above 1e-4 or not set."""
    v, line = reltol_of(texts, sim)
    if v is None:
        notice("EMIR-W540", f"{where} sets no relative tolerance ({sim}'s default applies: "
               f"1e-3 in Xyce, ngspice and Spectre): set reltol=1e-4 or below, or the "
               f"simulator's own error is as large as what the self-consistent loop "
               f"corrects", log)
    elif v > RELTOL_MAX:
        notice("EMIR-W540", f"{where} simulates at reltol={v:g} ({line!r}): above 1e-4, the "
               f"simulator's own error is as large as what the self-consistent loop corrects; "
               f"set reltol=1e-4 or below", log)


def testbench_rail(testbench: str, dspf, net: str, prefix=None, subckt=None) -> dict:
    """The DC voltage the testbench drives `net` at, read from its text:
    `{"volts", "source", "why"}`, and the top-level `node` and the `sign`
    of the source (+1: the rail is its + node) when a source drives it
    (`volts` None when it cannot be read: the
    net is not a port of the block's subcircuit, the rail is driven through
    another element, or the source has a transient function or a parameter).
    The node is followed up the instance path to the top level, where it is
    ground (`0`, `gnd`) or driven by one independent voltage source to
    ground with a DC value."""
    from .opensim import scope_of, scope_path
    from .opensim_netlist import Tree
    try:
        tree = Tree(testbench)
        scope, _w = scope_of(tree, dspf, subckt)
        path, _w = scope_path(tree, scope, prefix, command="run")
    except EmirError as e:
        return {"volts": None, "source": None, "why": f"{e.code}: {e.message}"}
    gnd = {"0", "GND", "GND!"}
    node = net
    if scope is not None and not path:
        return {"volts": None, "source": None,
                "why": "the block's subcircuit is not instantiated in the testbench"}
    # up the instance path: the node in each level's subcircuit is one of its
    # ports, joined to the instance's node at the same position one level up
    for depth in range(len(path), 0, -1):
        if node.upper() in gnd:
            break
        sub = tree.subckts.get(_ref_of(tree, path[:depth]) or "")
        if sub is None:
            return {"volts": None, "source": None,
                    "why": f"instance {'.'.join(path[:depth])} is not a subcircuit of the "
                           f"testbench"}
        ports = [p_.upper() for p_ in sub.ports]
        if node.upper() not in ports:
            return {"volts": None, "source": None,
                    "why": f"{node} is not a port of .subckt {sub.name} (a rail made "
                           f"inside it)"}
        parent = _ref_of(tree, path[:depth - 1])
        inst = next((e for e in tree.elements(parent)
                     if e.name.upper() == path[depth - 1].upper()), None)
        if inst is None or len(inst.nodes) != len(ports):
            return {"volts": None, "source": None,
                    "why": f"instance {path[depth - 1]} does not match .subckt {sub.name}"}
        node = inst.nodes[ports.index(node.upper())]
    if node.upper() in gnd:
        return {"volts": 0.0, "source": node, "why": f"{net} is on ground ({node}) in the "
                f"testbench"}
    srcs = []
    for e in tree.elements(None):
        if e.kind != "V" or len(e.nodes) < 2:
            continue
        a, b = e.nodes[0].upper(), e.nodes[1].upper()
        if node.upper() in (a, b) and ({a, b} - {node.upper()}) <= gnd and a != b:
            srcs.append((e, 1.0 if a == node.upper() else -1.0))
    if len(srcs) != 1:
        return {"volts": None, "source": None,
                "why": (f"no independent voltage source drives node {node} to ground at "
                        f"the top level" if not srcs else
                        f"{len(srcs)} sources drive node {node}")}
    e, sign = srcs[0]
    v = _dc_value(e.words[3:])
    if v is None:
        return {"volts": None, "source": e.name,
                "why": f"source {e.name} has no plain DC value (a transient function or "
                       f"a parameter)"}
    return {"volts": sign * v, "source": e.name, "node": node, "sign": sign,
            "why": f"source {e.name} drives {node} at {sign * v:g} V"}


def _ref_of(tree, path):
    """The subcircuit (upper case) the instance at the end of `path` (a
    list of instance names from the top) instantiates; None for []."""
    scope = None
    for name in path:
        el = next((e for e in tree.elements(scope) if e.name.upper() == name.upper()), None)
        if el is None or not el.ref:
            return None
        scope = el.ref.upper()
    return scope


_SUFFIX = {"T": 1e12, "G": 1e9, "MEG": 1e6, "K": 1e3, "M": 1e-3, "MIL": 25.4e-6,
           "U": 1e-6, "N": 1e-9, "P": 1e-12, "F": 1e-15}


def _number(w: str):
    m = re.match(r"^([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)([a-zA-Z]*)$", w)
    if not m:
        return None
    unit = m.group(2).upper()
    for s in ("MEG", "MIL", "T", "G", "K", "M", "U", "N", "P", "F"):
        if unit.startswith(s):
            return float(m.group(1)) * _SUFFIX[s]
    return float(m.group(1))


def _dc_value(words):
    """The DC value of an independent source's value words (`DC 1.2`,
    `1.2`, `dc=1.2`; an AC part after it is fine), or None when it has a
    transient function (PULSE, PWL, SIN ...) or its value is a parameter."""
    ws = list(words)
    up = [w.upper() for w in ws]
    if any(u.split("(")[0] in ("PULSE", "PWL", "SIN", "EXP", "SFFM", "AM", "PAT", "TRNOISE",
                               "TRRANDOM") for u in up):
        return None
    for i, u in enumerate(up):
        if u == "DC" and i + 1 < len(ws):
            return _number(ws[i + 1])
        if u.startswith("DC="):
            return _number(ws[i][3:])
    if ws:
        return _number(ws[0])
    return None


def rail_check(testbench: str, dspf, net: str, vdd, prefix=None) -> dict:
    """--vdd against the testbench (Xyce, ngspice, HSPICE): `{"vdd", "rail",
    "code", "level", "message"}`. Given and read: EMIR-E523 (FAIL) beyond
    1 mV or 1 %, else EMIR-I521; not given: the testbench's value (EMIR-I521),
    or EMIR-E503 when it cannot be read; given, not read: EMIR-I521 saying
    the value is as given (the loop does not depend on it: each pin's source
    is in series with the testbench's own rail)."""
    r = testbench_rail(testbench, dspf, net, prefix=prefix)
    v = r["volts"]
    if vdd is None:
        if v is None:
            return {"vdd": None, "rail": r, "code": "EMIR-E503", "level": "FAIL",
                    "message": f"--self-consistent needs --vdd, the voltage of net {net} in "
                               f"the testbench (0 for a ground net), and it cannot be read "
                               f"from the testbench: {r['why']}"}
        return {"vdd": v, "rail": r, "code": "EMIR-I521", "level": "INFO",
                "message": f"--vdd not given: {v:g} V, read from the testbench ({r['why']})"}
    if v is None:
        return {"vdd": vdd, "rail": r, "code": "EMIR-I521", "level": "INFO",
                "message": f"--vdd {vdd:g} V as given: the testbench's rail could not be read "
                           f"({r['why']}); the loop does not use it (each pin's source is "
                           f"in series with the testbench's own rail)"}
    tol = max(RAIL_TOL_V, RAIL_TOL_REL * max(abs(v), abs(vdd)))
    if abs(v - vdd) > tol:
        return {"vdd": vdd, "rail": r, "code": "EMIR-E523", "level": "FAIL",
                "message": f"--vdd {vdd:g} V, but the testbench holds net {net} at {v:g} V "
                           f"({r['why']}): is this the net and the testbench you meant? "
                           f"Give --vdd {v:g}, or leave it out to take the testbench's"}
    return {"vdd": vdd, "rail": r, "code": "EMIR-I521", "level": "OK",
            "message": f"--vdd {vdd:g} V matches the testbench ({r['why']})"}



#: how each way the loop ends reads in a report (`self_consistency.stopped`);
#: every one but converged and settled reports pass 0, the open loop
STATES = {
    "converged": "converged",
    "settled": "settled (the answer held still on two passes; the residual stayed above "
               "its tolerance)",
    "residual grew twice": "DIVERGING (the residual grew on two passes in a row): pass 0, "
                           "the open loop, is reported",
    "droop above half the supply": "DIVERGING (the droop to feed back reached its guard): "
                                   "pass 0, the open loop, is reported",
    "pass limit": "NOT converged at the pass limit: pass 0, the open loop, is reported",
    "time limit": "STOPPED (a pass's simulation ran past its time limit): pass 0, the open "
                  "loop, is reported",
    "simulation failed": "STOPPED (a pass's simulation failed): pass 0, the open loop, is "
                         "reported",
}


#: a loop that reports pass 0, by how it ended (`self_consistency.stopped`):
#: (its state in a chart's words, its code, what happened) -- the chart's alert
STOP_ALERTS = {
    "residual grew twice": ("DIVERGING", "EMIR-W406",
                            "is diverging (the residual grew on two passes in a row)"),
    "droop above half the supply": ("DIVERGING", "EMIR-W406",
                                    "is diverging (the droop to feed back reached its guard)"),
    "pass limit": ("NOT converged", "EMIR-W407", "did not converge by its pass limit"),
    "time limit": ("STOPPED", "EMIR-W543",
                   "stopped (a pass's simulation ran past its time limit)"),
    "simulation failed": ("STOPPED", "EMIR-W543", "stopped (a pass's simulation failed)"),
}


def stop_alert(sc: dict) -> tuple:
    """(state, code, what happened) of a loop that reported pass 0, the open
    loop (`STOP_ALERTS`; a summary without `stopped`: from `diverged`)."""
    st = sc.get("stopped")
    if st in STOP_ALERTS:
        return STOP_ALERTS[st]
    if sc.get("diverged"):
        return "DIVERGING", "EMIR-W406", "is diverging"
    return "NOT converged", "EMIR-W407", "did not converge"


def state_text(sc: dict) -> str:
    """How a loop ended, in words (`sc`: a net's `self_consistency` entry, or
    `converge`'s result)."""
    st = sc.get("stopped")
    if st in STATES:
        return STATES[st]
    if sc.get("converged"):
        return "converged"
    return ("DIVERGING: pass 0, the open loop, is reported" if sc.get("diverged") else
            "NOT converged: pass 0, the open loop, is reported")

def vdd_of(vdd, net: str):
    """The --vdd of `net`: the one voltage given, or the net's own of a
    per-net --vdd (None when it names no voltage for it)."""
    if isinstance(vdd, dict):
        return vdd.get(net)
    return vdd


def vdd_for(vdd, nets: list) -> tuple:
    """(`{net: volts or None}`, the problem or None) of --vdd for the nets a
    self-consistent run closes: one voltage serves one net; several nets
    take one per net (`VDD=1.2,VSS=0`), and a net left out is read from the
    testbench."""
    if vdd is None:
        return {n: None for n in nets}, None
    if not isinstance(vdd, dict):
        if len(nets) > 1:
            return None, (f"--vdd {vdd:g} with --net {','.join(nets)}: one --vdd cannot say "
                          f"which net it is; give one per net (--vdd "
                          f"{','.join(f'{n}=<volts>' for n in nets)}), or leave it out to "
                          f"read each rail from the testbench")
        return {nets[0]: float(vdd)}, None
    other = [n for n in vdd if n not in nets]
    if other:
        return None, (f"--vdd names {', '.join(other)}, which --net ({','.join(nets)}) does "
                      f"not close")
    return {n: vdd.get(n) for n in nets}, None

def supply_span(testbench: str) -> float | None:
    """The largest DC voltage, in magnitude, of the testbench's top-level
    independent voltage sources from a node to ground: the supply a ground
    net's droop guard is taken from (a ground net reads 0 V itself). None
    when no source has a plain DC value."""
    from .opensim_netlist import Tree
    try:
        tree = Tree(testbench)
    except (EmirError, OSError):
        return None
    gnd = {"0", "GND", "GND!"}
    best = None
    for e in tree.elements(None):
        if e.kind != "V" or len(e.nodes) < 2:
            continue
        a, b = e.nodes[0].upper(), e.nodes[1].upper()
        if (a in gnd) == (b in gnd):
            continue
        v = _dc_value(e.words[3:])
        if v is not None and (best is None or abs(v) > best):
            best = abs(v)
    return best


def vdd_arg(text: str):
    """--vdd: one voltage (`1.2`), or with several nets one per net
    (`VDD=1.2,VSS=0`): a float, or {net: volts}."""
    import argparse
    t = str(text).strip()
    if "=" not in t:
        try:
            return float(t)
        except ValueError:
            raise argparse.ArgumentTypeError(f"invalid --vdd {text!r}: a voltage (1.2), or "
                                             f"one per net (VDD=1.2,VSS=0)") from None
    out = {}
    for part in t.split(","):
        if not part.strip():
            continue
        net, _, v = part.partition("=")
        try:
            out[net.strip()] = float(v)
        except ValueError:
            raise argparse.ArgumentTypeError(f"invalid --vdd {text!r}: {part!r} is not "
                                             f"NET=volts (VDD=1.2,VSS=0)") from None
        if not net.strip():
            raise argparse.ArgumentTypeError(f"invalid --vdd {text!r}: {part!r} names no net")
    if not out:
        raise argparse.ArgumentTypeError(f"invalid --vdd {text!r}: no NET=volts")
    return out


#: a value --vdd accepts, and what the MCP tools take for it: one number (the
#: per-net form goes through the generic `emir_cli` tool, or a shell)
vdd_arg.example = 1.2
vdd_arg.json_type = "number"



# ------------------------------------------------------------------ drivers


class SpectreDriver:
    """Spectre: the person's CC netlist holds the split (`rail_split_fragment`):
    one `vsource type=pwl file=` per pin. Each pass rewrites every pin's PWL
    file and the manifest (`droop.scs`) and runs Spectre, keeping the pass's
    result as `pass<k>.raw` in `out_dir` (by default
    `emir_out/selfconsist/<net>`, where the files are too): nothing is
    written beside the person's netlist. UNVERIFIED until checked where
    Spectre runs."""

    sim = "spectre"
    #: the droop is each pin's, relative to the rail (0 on pass 0)
    relative = True

    def __init__(self, netlist: str, droop_path: str, pins, spectre=None,
                 timeout: float = 3600.0, log=print, out_dir=None, guard=False, net=None):
        self.netlist = os.path.abspath(netlist)
        self.droop = os.path.abspath(droop_path)
        self.out_dir = os.path.abspath(out_dir or os.path.dirname(self.droop))
        self.names = param_names(sorted(pins))
        self.files = pwl_files(pins, os.path.dirname(self.droop))
        self.spectre = spectre
        self.timeout = timeout
        self.log = log
        self.version = None
        self.guard = guard
        self.net = net
        if guard:
            _guard(self.droop)

    def pins(self, net=None) -> list:
        return sorted(self.files)

    def kcl_source(self, net):
        """Spectre's netlist is not read: no rail source to check KCL against."""
        return None

    def files_written(self) -> list:
        return [{"from": "(each pin's droop waveform, every pass)", "to": p}
                for p in [self.droop, *self.files.values()]]

    def pass_result(self, k: int) -> str:
        return os.path.join(self.out_dir, f"pass{k}.raw")

    def result_path(self) -> str:
        return self.pass_result(0)

    def command(self, exe=None, k: int = 0) -> list:
        out = self.pass_result(k)
        return [exe or "spectre", self.netlist, "-format", "psfascii", "-raw", out,
                "+escchars", "=log", out + ".log"]

    def write(self, k: int, fed=None):
        write_spectre_droop(self.droop, self.files, fed, npass=k,
                            guard=self.guard and k == 0)

    def run(self, k: int, fed=None, timeout=None) -> dict:
        claim(self.pass_result(k), k)
        self.write(k, fed)
        return ccsim.run_spectre(self.netlist, out_dir=self.pass_result(k),
                                 spectre=self.spectre, timeout=timeout or self.timeout,
                                 log=self.log)


class SpiceDriver:
    """Xyce, ngspice, HSPICE: emir writes the split testbench once
    (`split_testbench`), and each pass rewrites the include that holds the
    split's sources (each pin's droop as an inline PWL), runs the simulator,
    and keeps the pass's result as `pass<k>.raw` (HSPICE: `pass<k>.tr0`) with
    its names sidecar beside it. `net` is one net or a list (several nets in
    one loop)."""

    #: the droop is each pin's, relative to the rail (0 on pass 0)
    relative = True

    def __init__(self, sim: str, testbench: str, dspf, net, v_nominal, out_dir: str,
                 prefix=None, subckt=None, timeout: float = 3600.0, extra=(),
                 droop_path=None, types=None, log=_stderr):
        self.sim = sim
        self.testbench = os.path.abspath(testbench)
        self.out_dir = os.path.abspath(out_dir)
        self.nets = [net] if isinstance(net, str) else list(net)
        self.net = self.nets[0]
        self.timeout = timeout
        self.extra = list(extra)
        self.log = log
        self.version = None
        self.exe = None
        ext = ".sp" if sim == "hspice" else ".inc"
        self.droop = os.path.abspath(droop_path or os.path.join(self.out_dir, "droop" + ext))
        # the droop file is rewritten every pass, at the default place or where
        # --droop-file says: one emir did not write (the testbench, the DSPF,
        # a file of the person's) is refused before the split is written
        self.guard = True
        _guard(self.droop)
        self.split = split_testbench(sim, testbench, dspf, self.nets, v_nominal, self.out_dir,
                                     self.droop, prefix=prefix, subckt=subckt, types=types,
                                     guard=self.guard)
        self.names = self.split["names"]
        self.deck = self.split["deck"]

    def pins(self, net=None) -> list:
        """The pins the split feeds (of `net`, or of every net)."""
        return sorted({s["pin"] for s in self.split["sources"]
                       if net is None or s["net"] == net})

    def kcl_source(self, net):
        """The rail source of `net` whose current the deck saves (KCL), or None."""
        return self.split["rails"].get(net)

    def corners(self):
        """The testbench's stimulus corners (`stimulus_corners`), or None when
        it has none: the loop then finds them in the simulation's points."""
        return np.array(self.split["corners"]) if self.split["corners"] else None

    @property
    def raw(self) -> str:
        """The result the deck writes (renamed per pass)."""
        return os.path.join(self.out_dir, "sc.tr0" if self.sim == "hspice" else "sc.raw")

    def pass_result(self, k: int) -> str:
        return os.path.join(self.out_dir, f"pass{k}.{'tr0' if self.sim == 'hspice' else 'raw'}")

    @staticmethod
    def side_files(raw: str, sim: str) -> list:
        """What a pass writes beside the deck's result `raw`, and replaces on
        the next: its names sidecar, the simulator's log (`run_simulator`:
        `<raw>.log`) and, for HSPICE, the other outputs of its `-o` stem."""
        out = [raw, raw + ".names", raw + ".log"]
        if sim == "hspice":
            stem = os.path.splitext(raw)[0]
            out += [stem + ext for ext in (".lis", ".st0", ".pa0", ".ic0", ".mt0")]
        return out

    def files_written(self) -> list:
        return [*self.split["files"], {"from": "(the split's sources, each pin's droop, "
                                               "every pass)", "to": self.droop}]

    def result_path(self) -> str:
        return self.pass_result(0)

    def command(self, exe=None) -> list:
        from .opensim import sim_command
        return sim_command(self.sim, exe or self.sim, self.deck, self.raw, self.extra)

    def write(self, k: int, fed=None):
        write_droop(self.droop, self.split["sources"], fed, npass=k, sim=self.sim,
                    guard=self.guard and k == 0)

    def run(self, k: int, fed=None, timeout=None) -> dict:
        from .opensim import run_simulator, sim_exe, sim_version, write_sidecar
        if self.exe is None:
            self.exe = sim_exe(self.sim)
            self.version = sim_version(self.sim, self.exe, timeout=30.0)
        dest = self.pass_result(k)
        # the simulator's other outputs (HSPICE's .lis, .st0 ...) stay where it
        # wrote them, pass after pass: the first pass of a run claims them
        side = self.side_files(self.raw, self.sim)
        claim(dest, k, also=side if k == 0 else side[:3])
        self.write(k, fed)
        for stale in (self.raw, self.raw + ".names", self.raw + ".log"):
            if os.path.exists(stale):
                os.remove(stale)
        secs, logf = run_simulator(self.sim, self.exe, self.deck, self.raw,
                                   timeout or self.timeout, log=self.log, extra=self.extra,
                                   option="--sim-timeout")
        if os.path.exists(dest):
            os.remove(dest)
        shutil.move(self.raw, dest)
        side = dict(self.split["sidecar"])
        if self.version:
            side["simulator_version"] = self.version
        write_sidecar(dest + ".names", side)
        if os.path.exists(logf):
            shutil.move(logf, dest + ".log")
        return {"raw": dest, "seconds": secs, "log": dest + ".log"}


def split_testbench(sim: str, testbench: str, dspf, net, v_nominal: float | None,
                    out_dir: str, droop_path: str, prefix=None, subckt=None,
                    types=None, pins=None, guard=False) -> dict:
    """Write the split copy of a SPICE testbench (Xyce, ngspice, HSPICE) into
    `out_dir`: every device terminal on `net` (a name, or a list of nets)
    that is an instance pin of the DSPF gets a node of its own, fed from the
    terminal's rail node by `VEMIRSC<k>` (the pin's droop, in series with
    whatever drives the rail) through a 0 V probe `VEMIR<k>` (the probe
    measures the pin's current, positive into the device). The `VEMIRSC<k>`
    lines are the per-pass include `droop_path`, written at 0 V here (pass 0:
    the testbench's open loop) and included where the split is (the block's
    subcircuit); the print / save lines of the probes, and of each net's rail
    source (KCL), go before `.end`. `v_nominal` is not used (kept for old
    callers). Returns `{deck, files, names, sidecar, probes, pins, sources,
    rails, uncovered, scope, path, why}`.

    A terminal on the net that is no instance pin of the DSPF (and not
    another terminal of a device whose pin is: a bulk follows its pin) is
    refused (EMIR-E540): it would stay on the ideal rail while the net
    droops. EMIR-E522 when no terminal matched a pin."""
    from .opensim import (_probe_notices, _probe_signal, probe_tree, save_set, spiceinit)
    from .opensim_netlist import DEFAULT_TYPES, Tree, Writer
    nets = [net] if isinstance(net, str) else list(net)
    tree = Tree(testbench)
    if not tree.analyses:
        raise EmirError("EMIR-E462", f"{testbench} (and its includes) has no .tran analysis: "
                        f"EM needs the currents over time", path=testbench)
    pres, scope, path, why = probe_tree(tree, nets, d=dspf, subckt=subckt, prefix=prefix,
                                        types=types or DEFAULT_TYPES, pins=pins,
                                        command="run")
    matched = [p for p in pres.probes if p["matched"]]
    if not matched:
        raise EmirError("EMIR-E522", f"the rail split of {', '.join(nets)} found "
                        f"{len(pres.probes)} device terminal(s) on it in {testbench}, and none "
                        f"is an instance pin of the DSPF ({', '.join(p['pin'] for p in pres.probes[:4])} "
                        f"...)", path=testbench, dspf=getattr(dspf, "path", None), net=nets[0])
    pins_ = sorted({p["pin"] for p in matched})
    names = param_names(pins_)
    used = {e.upper() for e in tree.element_names(scope)} | \
        {p["source"].upper() for p in pres.probes}
    by_src = {p["source"]: p for p in matched}
    other = {p["source"]: p for p in pres.probes if not p["matched"]}
    edits, k, followed, sources = {}, 0, set(), []
    for key, lines in pres.edits.items():
        new, home = [], None
        for ln in lines:
            w = ln.split()
            p = by_src.get(w[0]) if w else None
            if p is None:
                new.append(ln)
                continue
            while f"{SPLIT_SOURCE}{k}".upper() in used:
                k += 1
            src = f"{SPLIT_SOURCE}{k}"
            used.add(src.upper())
            node = f"{p['node']}{SPLIT_NODE}{k}"
            k += 1
            home = home or (p["node"].upper(), node)
            # the probe now runs from the pin's own supply node to the terminal,
            # and that node sits at the rail plus the pin's droop: the source
            # (in the per-pass include) is in series with whatever the
            # testbench drives the rail with
            new.append(f"{w[0]} {node} {w[2]} 0")
            sources.append({"source": src, "node": node, "rail": w[1], "pin": p["pin"],
                            "net": p["net"]})
        if home is not None:
            # a terminal of the same device on the same net that is no DSPF pin
            # (a bulk the extraction merged into the source pin) follows its
            # device's pin: in the layout it is on that pin's node, not on the
            # far rail (pass 0 is unchanged: every node is at the nominal)
            for i, ln in enumerate(new):
                w = ln.split()
                q = other.get(w[0]) if w else None
                if q is not None and q["node"].upper() == home[0]:
                    new[i] = f"{w[0]} {home[1]} {w[2]} 0"
                    followed.add(q["pin"])
        edits[key] = new
    pres.unmatched = [x for x in pres.unmatched if x not in followed]
    if pres.unmatched:
        raise EmirError("EMIR-E540", f"{len(pres.unmatched)} device terminal(s) on "
                        f"{', '.join(nets)} in {testbench} are no instance pin of the DSPF: "
                        f"{', '.join(pres.unmatched[:6])}"
                        f"{' ...' if len(pres.unmatched) > 6 else ''}. The waveform loop "
                        f"moves every terminal on the net with its droop, and these would stay "
                        f"on the ideal rail (an idle device's charge, a decap's, missing from "
                        f"the loop)", path=testbench, dspf=getattr(dspf, "path", None),
                        net=nets[0], details={"terminals": list(pres.unmatched)})
    _probe_notices(pres, nets)
    os.makedirs(out_dir, exist_ok=True)
    # the include of the per-pass sources goes where the split is: before the
    # first statement the split edits (in the block's subcircuit, or the top)
    first = min(edits, key=lambda fk: (tree.order.index(fk[0]), fk[1]))
    inc = os.path.relpath(droop_path, out_dir)
    inc = f'"{inc}"' if re.search(r"\s", inc) and sim != "hspice" else inc
    edits[first] = ["* emir.selfconsist: the split's sources, each pin's droop waveform, "
                    "rewritten every pass",
                    f".include '{inc}'" if sim == "hspice" else f".include {inc}",
                    *edits[first]]
    raw = "sc.tr0" if sim == "hspice" else "sc.raw"
    probes = {p["pin"].upper(): p for p in matched}
    ss = save_set(dspf, nets, "cc", sim, path, probes=probes)
    # each net's rail source (a top-level independent source to ground): its
    # current is saved, and every pass checks KCL against it
    rails = {}
    for n in nets:
        r = testbench_rail(testbench, dspf, n, prefix=prefix, subckt=subckt)
        if r.get("source") and r.get("sign"):
            sig = _probe_signal(sim, [], r["source"])
            ss.save_only(sig)
            rails[n] = {"name": r["source"], "signal": sig, "sign": r["sign"]}
    deck = os.path.join(out_dir, f"split.{sim}.{'sp' if sim == 'hspice' else 'cir'}")
    lines = ss.lines(raw=raw, deck=os.path.basename(deck))
    copies = Writer(tree, deck, f"split_{sim}", edits=edits, inject=lines,
                    strip=sim == "xyce", command="run").write(
                        check=guard_split(deck + STAMP_EXT))
    if sim == "ngspice":
        init = spiceinit(testbench, deck)
        if init:
            notice("EMIR-I262", init, _stderr)
    reltol_notice([sf_text(p) for p in tree.order], sim, f"the testbench {testbench}")
    corners = stimulus_corners(tree)
    write_droop(droop_path, sources, None, npass=0, sim=sim, guard=guard)
    sidecar = ss.sidecar()
    sidecar["generator"] = "emir run --self-consistent"
    return {"deck": deck, "files": [{"from": s, "to": t} for s, t in copies.items()],
            "names": names, "sidecar": sidecar, "probes": matched, "pins": pins_,
            "sources": sources, "rails": rails, "corners": corners,
            "uncovered": list(pres.uncovered),
            "scope": scope, "path": path, "why": why}


_PULSE = re.compile(r"\bPULSE\s*\(([^)]*)\)", re.I)
_PWL = re.compile(r"\bPWL\s*\(([^)]*)\)", re.I)
#: the most stimulus corners read (a long run of fast pulses)
MAX_CORNERS = 200_000


def stimulus_corners(tree) -> list:
    """The corners of a testbench's stimuli, in s: every PULSE source's
    edges (`PULSE(v1 v2 td tr tf pw per)`, repeated every period until the
    transient's stop) and every PWL source's points. The simulator puts a
    breakpoint on each, and the loop puts a point of its time base there
    (`tdloop.base_grid`). A value that is a parameter is skipped."""
    stop = None
    for a in tree.analyses:
        w = a.split()
        if len(w) >= 3:
            stop = _number(w[2])
            break
    out = []
    for sf in tree.files.values():
        for ch in sf.chunks:
            if ch.kind != "element" or not ch.words or ch.words[0][:1].upper() not in "VI":
                continue
            text = ch.text
            for m in _PULSE.finditer(text):
                v = [_number(x) for x in m.group(1).replace(",", " ").split()]
                if len(v) < 3 or any(x is None for x in v):
                    continue
                td = v[2]
                tr = v[3] if len(v) > 3 else 0.0
                tf = v[4] if len(v) > 4 else 0.0
                pw = v[5] if len(v) > 5 else None
                per = v[6] if len(v) > 6 else None
                edges = [0.0, tr] + ([tr + pw, tr + pw + tf] if pw is not None else [])
                n = 1
                if per and per > 0 and stop:
                    n = int(min(max(1, np.ceil((stop - td) / per) + 1), MAX_CORNERS))
                for k in range(n):
                    out += [td + k * (per or 0.0) + e for e in edges]
                    if len(out) > MAX_CORNERS:
                        return sorted(set(out))
            for m in _PWL.finditer(text):
                v = [_number(x) for x in m.group(1).replace(",", " ").split()]
                if v and all(x is not None for x in v):
                    out += v[0::2]
    if stop:
        out = [t for t in out if 0 <= t <= stop]
    return sorted(set(out))


def sf_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


# ------------------------------------------------------------------ the loop


def converge(netlist, droop_path, dspf, net: str, proc, feed: str, v_nominal: float,
             period=None, max_passes=MAX_PASSES, tol_mV=TOL_MV, analysis=None,
             waveforms=True, on_pass=None, simulate=None, driver=None, max_bytes=None,
             superposition="auto", spectre=None, log=print, tol_rel=TOL_REL,
             sim_timeout=None, companion=True, guard_V=None) -> dict:
    """The waveform loop on one net (`emir.tdloop.run`), behind the
    interface the run calls: `driver` runs one pass (`SpectreDriver`,
    `SpiceDriver`); without one the Spectre driver runs `netlist`.
    `simulate(netlist, k, fed)` replaces the driver (`emir validate`, the
    tests): `fed` is `{"t": times or None, "v": {pin: droop}}` and it returns
    `{"raw"}` or `{"waves"}` with `seconds`. `on_pass(k, res, ing)` may return
    a dict to merge into the pass's record (how a measurement scores each
    pass against its RCC reference).

    Returns the net's every pass (the evidence that the loop converged), the
    reported pass's result (`result`, `taps`, `ingest`, `raw`), and
    `converged`, `diverged`, `stopped`, `reported_pass`."""
    if driver is None and simulate is None:
        pins = sorted({f"{t['inst']}:{t['pin']}" for t in dspf.net_taps(net)})
        driver = SpectreDriver(netlist, droop_path, pins, spectre=spectre, log=log)
    if simulate is None and hasattr(driver, "pass_result"):
        guard_passes(driver, max_passes)
        drop_stale_passes(driver)
    hook = (lambda k, n, res, ing: on_pass(k, res, ing)) if on_pass else None
    out = tdloop.run(dspf, [net], proc, {net: feed}, {net: v_nominal}, driver=driver,
                     simulate=simulate, netlist=netlist, period=period,
                     max_passes=max_passes, tol_mV=tol_mV, tol_rel=tol_rel,
                     analysis=analysis, waveforms=waveforms, on_pass=hook,
                     max_bytes=max_bytes, superposition=superposition,
                     sim_timeout=sim_timeout, companion=companion, guard_V=guard_V,
                     log=log)
    return merged(out, net)


def converge_nets(driver, dspf, nets: list, proc, feeds: dict, vdds: dict, **kw) -> dict:
    """The waveform loop on several nets at once (`emir.tdloop.run`): one
    simulation per pass, every net's droop fed back. `{net: per-net result}`
    as `converge` returns it."""
    if kw.get("simulate") is None and hasattr(driver, "pass_result"):
        guard_passes(driver, kw.get("max_passes", MAX_PASSES))
        drop_stale_passes(driver)
    out = tdloop.run(dspf, nets, proc, feeds, vdds, driver=driver, **kw)
    return {n: merged(out, n) for n in nets}


def merged(out: dict, net: str) -> dict:
    """One net's result of `tdloop.run`, with the loop's per-pass cost
    (simulation seconds, time points, PWL points, post-processing) in each of
    its passes."""
    cv = dict(out["nets"][net])
    rows = []
    for g, p in zip(out["passes"], cv["passes"]):
        rows.append({**p, "post_seconds": g.get("post_seconds")})
    cv["passes"] = rows
    cv.update(steps=out.get("steps"), dt=out.get("dt"), net_list=out.get("net_list"))
    return cv


def summary(cv: dict) -> dict:
    """The run summary's `self_consistency` entry of one net."""
    keep = ("pass", "residual_mV", "droop_change_mV", "tolerance_mV", "current_change",
            "max_droop_mV", "simulated_droop_mV", "worst_ratio", "ir_drop_mV", "total_avg_A",
            "seconds", "post_seconds", "points", "pwl_points", "pwl_tol_mV", "fed_by",
            "fed_scale", "converged", "stop_rule")

    def row(p):
        r = {k: _num(p.get(k)) for k in keep}
        mc = p.get("metric_change")
        r["metric_change"] = None if mc is None else {k: _num(v) for k, v in mc.items()}
        comp = p.get("companion")
        r["companion"] = None if comp is None else {
            "g_mS": [_num(x) for x in comp["g_mS"]], "C_fF": [_num(x) for x in comp["C_fF"]],
            "r2_median": _num(comp["r2_median"]), "frozen": bool(comp.get("frozen"))}
        kc = p.get("kcl")
        r["kcl"] = None if kc is None else {k: (_num(v) if k != "source" else v)
                                            for k, v in kc.items()}
        return r
    return {"method": "waveform", "simulator": cv.get("simulator"), "vdd": cv["v_nominal"],
            "rail": cv.get("rail"), "nets": list(cv.get("nets") or []),
            "tol_mV": cv["tol_mV"], "tol_rel": cv.get("tol_rel"),
            "max_passes": cv["max_passes"], "converged": cv["converged"],
            "diverged": cv["diverged"], "stopped": cv.get("stopped"),
            "reported_pass": cv["reported_pass"], "n_passes": len(cv["passes"]),
            "at_pass_limit": bool(cv.get("at_pass_limit")), "pwl_cap": cv.get("pwl_cap"),
            "feedback": _feedback(cv.get("feedback")),
            "steps": cv.get("steps"), "passes": [row(p) for p in cv["passes"]]}


#: the scoring grid of the worst basis: per segment and metric the larger of
#: the R-only grid's current and the RC grid's
WORST_GRID = "max(R-only, RC)"


def scoring(r: dict, taps=None) -> dict:
    """How a self-consistent net's branch currents were scored: through the
    RC grid when --dynamic's decap-aware EM ran and decap-aware gives the
    verdict (`RC`); per segment the larger of the R-only and the RC grid's
    when the worst basis does (`max(R-only, RC)`: conservative, it keeps the
    R-only grid's high readings); else on the R-only grid (the superposition
    of the pin currents, `R-only`). `ac`: whether the pins' currents vary over
    the window (`taps`: the reported pass's); constant currents charge no
    capacitor, so every scoring is the same (None: not known)."""
    eb = r.get("em_basis") or {}
    used = eb.get("used")
    dyn = bool(r.get("dynamic"))
    grid = ("RC" if dyn and used == "decap-aware" else
            WORST_GRID if dyn and used == "worst" else "R-only")
    return {"grid": grid, "basis": used, "ac": _ac(taps)}


def _ac(taps) -> bool | None:
    """Whether any pin's current waveform varies over the window (by more
    than 1e-6 of the largest pin's peak); None without waveforms."""
    waves_ = [np.asarray(t["wave"], float) for t in taps or ()
              if not t.get("feed") and t.get("wave") is not None and len(t["wave"])]
    if not waves_:
        return None
    scale = max(float(np.abs(w).max()) for w in waves_)
    return any(float(w.max() - w.min()) > 1e-6 * max(scale, 1e-30) for w in waves_)


def scoring_notice(net: str, sc: dict, log=_stderr):
    """EMIR-W545 when a converged loop fed back the RC grid's droop and its
    branches were scored on the R-only grid (`sc`: the net's summary entry,
    with `scoring`). Not when the loop converged at pass 0: no droop was fed
    back, the currents are the open loop's (as on any cc-grid run)."""
    if not sc.get("converged") or (sc.get("feedback") or {}).get("grid") != "RC" \
            or (sc.get("scoring") or {}).get("grid") != "R-only" \
            or sc.get("reported_pass") == 0 or (sc.get("scoring") or {}).get("ac") is False:
        # (constant pin currents charge no capacitor: every scoring agrees)
        return
    notice("EMIR-W545", f"net {net!r}: the loop fed back the RC grid's droop, but the branch "
           f"currents are scored on the R-only grid (no --dynamic, or the decap-free "
           f"basis): the current the grid's capacitors carry is missing from them, so a "
           f"branch it feeds (a minor branch) can read low or high (measured at 20 fF a "
           f"node: -19.5 % to +25.3 % against RCC; within 0.52 % through the RC grid, "
           f"--em-basis decap-aware); the same run with --dynamic and the block's --period "
           f"scores them through the RC grid", log)


def _feedback(fb):
    """The grid the droop was fed back from (`tdloop.grid_caps`,
    `tdloop.grid_decaps`)."""
    if not fb:
        return None
    return {"grid": fb["grid"], "caps": int(fb["caps"]), "caps_F": _num(fb["caps_F"]),
            "unsized_caps": int(fb["unsized_caps"]), "decaps": int(fb.get("decaps") or 0),
            "decaps_F": _num(fb.get("decaps_F") or 0.0),
            "unsized_decaps": int(fb.get("unsized_decaps") or 0),
            "simulated_decaps": int(fb.get("simulated_decaps") or 0)}


def _num(v):
    if isinstance(v, (bool, int)) or v is None or isinstance(v, str):
        return v
    v = float(v)
    return v if np.isfinite(v) else None
