"""The simulators' path into a run: save lines, names sidecars, `emir simulate`.

rcc reads every branch current of an RC simulation by the DSPF's names,
`R:1` (the current from the resistor's first node to its second). Spectre
saves currents under exactly those names; HSPICE, Xyce and ngspice do not,
and they print different things in different syntax. This module closes the
gap in two commands:

    emir save-lines <dspf> --sim spectre|hspice|xyce|ngspice [--names-out F]
        the `save` (Spectre), `.probe` (HSPICE), `.print` (Xyce) or `.save`
        (ngspice) lines for every branch current, and the `<result>.names`
        sidecar that maps the simulator's names back to the DSPF's;
    emir simulate <testbench> --sim spectre|hspice|xyce|ngspice --dspf D --out R.raw
        all of it: the deck with the lines in it, the run, a text result and
        its sidecar, checked; then `emir run --cc-sim R.raw` reads it.

How each simulator names a branch current (measured on this build: Xyce 7.10
development, ngspice 45; `emir verify-sim` checks each form on the machine
that uses it; HSPICE from its manuals, HSPICE Reference Manual: Commands and
Control Options B-2008.09 and E-2010.12):

| simulator | the current of resistor RM1 in instance XI0, first node to second |
|---|---|
| Spectre | `XI0.RM1:1` (saved and read under the DSPF's own name) |
| HSPICE | `i(xi0.rm1)` in a `.tr0` written with `.option probe post=2 post_version=2001` |
| Xyce | `I(XI0:RM1)` |
| ngspice | `@r.xi0.rm1[i]` |

Every sign is +1. Names are matched without regard to case. Spectre and
HSPICE need a licence: `emir simulate --sim spectre|hspice` writes the deck and
prints the exact command, and runs it only with `--run`.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import shlex
import subprocess
import sys
import time

from . import subproc
from .errors import EmirError, notice
from .opensim_netlist import Tree, Writer
from .output import command_line, document, parser, strip_options

SIMS = ("spectre", "hspice", "xyce", "ngspice")
OPEN_SIMS = ("xyce", "ngspice")
#: the simulators that need a licence: planned, run only with --run
LICENSED_SIMS = ("spectre", "hspice")
#: what HSPICE is told: ASCII output (post=2), double precision (2001), only
#: the probed signals written (probe)
HSPICE_OPTIONS = ".option probe post=2 post_version=2001"

#: signals per `.print` / `.save` line before a `+` continuation
PER_LINE = 8

def _stderr(line):
    print(line, file=sys.stderr)


# ------------------------------------------------------------------ names


def split_path(prefix: str | None) -> list:
    """`XI0.` / `XI0:` / `XTOP.XI0.` -> `["XI0"]` / `["XTOP", "XI0"]`."""
    if not prefix:
        return []
    return [p for p in re.split(r"[.:]", prefix) if p]


def xyce_name(path: list, name: str) -> str:
    """Xyce's hierarchical name of an element (`XI0:RM1`)."""
    return ":".join([*path, name])


def ngspice_name(path: list, name: str) -> str:
    """ngspice's flattened name of an element (`r.xi0.rm1`): inside a
    subcircuit its letter, then the instance path, then its own name."""
    if not path:
        return name.lower()
    return ".".join([name[0], *path, name]).lower()


def hspice_name(path: list, name: str) -> str:
    """HSPICE's hierarchical name of an element (`xi0.rm1`): lower case,
    `.` between the levels."""
    return ".".join([*path, name]).lower()


def _signal(sim: str, path: list, name: str) -> tuple:
    """`(what to print or save, its name in the result)` for the branch current
    of resistor `name` in the instance `path`."""
    if sim == "hspice":
        s = f"i({hspice_name(path, name)})"
        return s, s.upper()
    if sim == "xyce":
        s = f"I({xyce_name(path, name)})"
        return s, s.upper()
    s = f"@{ngspice_name(path, name)}[i]"
    return s, f"i({s})".upper()


class SaveSet:
    """What one simulation must save, in one simulator's syntax, and how its
    names map back to the DSPF's."""

    def __init__(self, sim, nets, path):
        self.sim, self.nets, self.path = sim, nets, path
        self.mode = "rcc"
        self.signals: list = []           # what to print / save
        self.names: dict = {}             # result name (upper) -> [dspf name, sign]
        self.dspf_names: list = []        # the DSPF names covered
        self.direct = 0
        self.elements: list = []          # element names the lines reference
                                          # [(path, name)] (checked by simulate)
        self.version: str | None = None   # the simulator's version, when run
        self.sim_args: str | None = None  # emir simulate's --sim-args (normalised)

    def add(self, signal, result_name, dspf_name, sign, element=None):
        if signal not in self.signals:
            self.signals.append(signal)
        self.names[result_name.upper()] = [dspf_name, int(sign)]
        if dspf_name not in self.dspf_names:
            self.dspf_names.append(dspf_name)
        if element:
            self.elements.append(element)

    def lines(self, raw: str = "emir.raw", deck: str = "<testbench.cir>") -> list:
        """The lines to put in the deck (`raw`: the result file Xyce writes)."""
        sig = self.signals
        chunks = [sig[i:i + PER_LINE] for i in range(0, len(sig), PER_LINE)] or [[]]
        if self.sim == "xyce":
            head = f".print tran format=raw file={raw}"
            out = [f"* emir: {len(sig)} branch currents for rcc; run: Xyce -a {deck}",
                   head + (" " + " ".join(chunks[0]) if chunks[0] else "")]
        elif self.sim == "hspice":
            stem = raw[:-len(".tr0")] if raw.lower().endswith(".tr0") else raw
            deck = "<testbench.sp>" if deck == "<testbench.cir>" else deck
            out = [f"* emir: {len(sig)} branch currents for rcc; run: hspice -i {deck} -o "
                   f"{stem}  (writes {stem}.tr0)",
                   HSPICE_OPTIONS,
                   ".probe tran " + " ".join(chunks[0])]
        else:
            out = [f"* emir: {len(sig)} branch currents for rcc; run: ngspice -b -r "
                   f"{raw} {deck}",
                   f"* (interactive ngspice: in .control, run, then write {raw})",
                   ".options filetype=ascii",
                   ".save " + " ".join(chunks[0])]
        out += ["+ " + " ".join(c) for c in chunks[1:]]
        return out

    def sidecar(self) -> dict:
        out = {"map": dict(self.names), "simulator": self.sim}
        if self.version:
            out["simulator_version"] = self.version
        if self.sim_args is not None:
            out["sim_args"] = self.sim_args
        return out


def target_nets(d, net) -> list:
    """The nets a save covers: `net`, or every net with resistors."""
    if net and net != "all":
        return [net]
    return [n for n in sorted(d.nets) if d.net_resistors(n)]


def save_set(d, nets, sim, path=()) -> SaveSet:
    """The `SaveSet` for `nets` of DSPF `d`: every resistor's branch current."""
    path = list(path)
    ss = SaveSet(sim, list(nets), path)
    seen = set()
    for n in nets:
        for r in d.net_resistors(n):
            if r["name"] in seen:
                continue
            seen.add(r["name"])
            s, key = _signal(sim, path, r["name"])
            ss.add(s, key, f"{r['name']}:1", 1, (tuple(path), r["name"]))
            ss.direct += 1
    if not ss.signals:
        raise EmirError("EMIR-E205", f"no resistors on {', '.join(nets) or 'any net'}",
                        net=nets[0] if len(nets) == 1 else None)
    return ss


def infer_names(d, nets, w) -> tuple | None:
    """`(sim, prefix, hits, total)` when a result's current names are the ones
    `save_set` gives for some simulator and instance path -- a result
    simulated with emir's lines but no sidecar -- else None. The candidate
    paths come from the result's own hierarchical names."""
    have = {k.upper() for k in w.data}
    cands = {("xyce", ()), ("ngspice", ()), ("hspice", ())}
    for k in have:
        m = re.match(r"^I\(([^@:()\[\]]+)\)$", k)
        if m and "." in m.group(1):
            cands.add(("hspice", tuple(m.group(1).split(".")[:-1])))
        m = re.match(r"^I\((.+)\)$", k)
        if m and ":" in m.group(1) and not m.group(1).startswith("@"):
            cands.add(("xyce", tuple(m.group(1).split(":")[:-1])))
        m = re.match(r"^I\(@[A-Z]\.(.+)\[[A-Z]+\]\)$", k)
        if m:
            cands.add(("ngspice", tuple(m.group(1).split(".")[:-1])))
    mnets = [n for n in nets if d.net_resistors(n)]
    if not mnets:
        return None
    best = None
    for sim, path in sorted(cands):
        try:
            ss = save_set(d, mnets, sim, list(path))
        except EmirError:
            continue
        hits = len(set(ss.names) & have)
        if hits and (best is None or hits > best[2]):
            best = (sim, ".".join(path) + "." if path else "", hits, len(ss.names))
    return best


def write_sidecar(path: str, sidecar: dict) -> str:
    from .output import write_text
    return write_text(path, json.dumps(sidecar, indent=1, sort_keys=False) + "\n")


def save_lines_command(a, d, out) -> int:
    """`emir save-lines --sim hspice|xyce|ngspice` (the CLI's `_cmd_save_lines`
    hands over here for the SPICE simulators)."""
    path = split_path(a.prefix)
    nets = target_nets(d, a.net)
    ss = save_set(d, nets, a.sim, path)
    raw = a.raw or (a.names_out[:-len(".names")] if a.names_out and
                    a.names_out.endswith(".names") else
                    "emir.tr0" if a.sim == "hspice" else "emir.raw")
    lines = ss.lines(raw)
    print("\n".join(lines))
    if a.out:
        from .output import write_text
        write_text(a.out, "\n".join(lines) + "\n")
        _stderr(f"[emir] {len(ss.signals)} saved signals -> {a.out}")
    sidecars = []
    if a.names_out:
        sidecars.append(os.path.abspath(write_sidecar(a.names_out, ss.sidecar())))
        _stderr(f"[emir] names sidecar: {sidecars[0]}")
        if os.path.abspath(a.names_out) != os.path.abspath(raw) + ".names":
            notice("EMIR-W462", f"{a.names_out} is not `{raw}.names`: emir reads the "
                   f"sidecar only beside the result, as <result>.names", _stderr)
    else:
        notice("EMIR-W460", f"no --names-out: the {a.sim} result will name its currents "
               f"{ss.signals[0]} ..., which match nothing in the DSPF until a "
               f"<result>.names sidecar maps them (the `names` field of the JSON "
               f"document, or re-run with --names-out <result>.names)", _stderr)
    out.emit(document("save_lines", dspf=os.path.abspath(a.dspf), net=a.net,
                      mode="rcc", prefix=a.prefix, simulator=a.sim, lines=lines,
                      count=len(ss.signals), sidecars=sidecars, raw=raw,
                      names=ss.sidecar()["map"], nets=nets, direct=ss.direct,
                      out=os.path.abspath(a.out) if a.out else None))
    return 0


def scope_of(tree: Tree, d=None, subckt=None) -> tuple:
    """`(scope, why)`: the subcircuit that holds the DSPF's elements (None:
    the top level) -- `--subckt`, else the DSPF's design when the tree defines
    a subcircuit of that name, else the one holding most of its elements,
    else the top level."""
    if subckt:
        if subckt.upper() not in tree.subckts:
            own = [s.name for s in tree.subckts.values()]
            raise EmirError("EMIR-E261", f"no .subckt {subckt!r} in {tree.top.path} or "
                            f"the files it includes (have: "
                            f"{', '.join(own[:20]) or 'none'}{' ...' if len(own) > 20 else ''})",
                            path=tree.top.path)
        return subckt.upper(), f"--subckt {subckt}"
    design = getattr(d, "design", None) if d is not None else None
    if design and design.upper() in tree.subckts:
        return design.upper(), f"the DSPF's design {design!r} is a .subckt of the netlist"
    if d is not None:
        # the subcircuit that holds most of the DSPF's elements (its resistors,
        # the instances of its pins), if the top level holds fewer
        names = {r["name"].upper() for r in d.res}
        names |= {v["inst"].upper() for v in d.inst_pins.values()}
        best, hits = None, len(names & {e.name.upper() for e in tree.top_elements})
        for k, sc in tree.subckts.items():
            n = len(names & {e.name.upper() for e in sc.elements})
            if n > hits:
                best, hits = k, n
        if best is not None:
            return best, (f".subckt {tree.subckts[best].name} holds {hits} of the DSPF's "
                          f"elements")
    return None, "the top level of the netlist"


def scope_path(tree: Tree, scope, prefix=None, command="simulate") -> tuple:
    """`(path, why)`: the instance path the simulator names `scope`'s
    elements under. `--prefix` wins; else found in the tree (exactly one
    instance). A subcircuit never instantiated (the block netlist alone) is
    named as if it were the top level, which `--prefix` corrects."""
    if prefix is not None:
        return split_path(prefix), f"--prefix {prefix}"
    if scope is None:
        return [], "no instance path"
    paths = tree.instance_paths(scope)
    if len(paths) == 1:
        return paths[0], f"the only instance of {tree.subckts[scope].name}"
    if not paths:
        notice("EMIR-W263", f"{tree.subckts[scope].name} is not instantiated in "
               f"{os.path.basename(tree.top.path)}: the names written assume it is the "
               f"top level of the simulation; give --prefix (e.g. XI0.) for the instance "
               f"path the testbench uses", _stderr)
        return [], "not instantiated here (assumed top level)"
    shown = ", ".join(".".join(p) for p in paths[:6])
    raise EmirError("EMIR-E261", f"{tree.subckts[scope].name} has {len(paths)} instances "
                    f"({shown}{' ...' if len(paths) > 6 else ''}): give --prefix with the "
                    f"one the DSPF was extracted from (e.g. --prefix {'.'.join(paths[0])}.)",
                    command=command)


def _net_args(nets) -> list:
    if not nets:
        return []
    if isinstance(nets, str):
        nets = [nets]
    return [x for n in nets if n and n != "all" for x in ("--net", n)]


def sim_exe(sim: str) -> str:
    """The simulator's executable (`emir info` resolves it); EMIR-E660
    (an open simulator) or EMIR-E662 (a licensed one), exit 3, when it is
    not found."""
    from . import site
    p, src = site.tool(sim)
    if not p:
        env = {"xyce": "XYCE_BIN", "ngspice": "NGSPICE_BIN", "spectre": "SPECTRE_BIN",
               "hspice": "HSPICE_BIN"}[sim]
        code = "EMIR-E662" if sim in LICENSED_SIMS else "EMIR-E660"
        raise EmirError(code, f"{sim} not found ({src}): put it on $PATH, set ${env} "
                        f"or `{sim}:` in the site file (see `emir info`)", sim=sim)
    return p


#: the flag each simulator prints its version with (documented: Spectre
#: Reference 19.1 `-V`; HSPICE Reference E-2010.12 `-v`; `Xyce -v` and
#: `ngspice -v` as these builds answer them)
VERSION_FLAG = {"spectre": "-V", "hspice": "-v", "xyce": "-v", "ngspice": "-v"}


def version_command(sim: str, exe: str) -> list:
    return [exe, VERSION_FLAG[sim]]


def sim_version(sim: str, exe: str, timeout: float = 60.0) -> str | None:
    """The first line of what the simulator prints for its version flag that
    holds a digit, or None. It runs the simulator: for a licensed one, only
    after the person's go-ahead (`--run`)."""
    try:
        p = subprocess.run(version_command(sim, exe), capture_output=True, text=True,
                           timeout=timeout, stdin=subprocess.DEVNULL, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    for ln in ((p.stdout or "") + "\n" + (p.stderr or "")).splitlines():
        t = ln.strip(" *\t")
        if t and re.search(r"\d", t) and re.search(r"[A-Za-z]", t):
            return t[:200]
    return None


def _check_elements(tree: Tree, scope, path, ss: SaveSet):
    """EMIR-E263 when the lines name elements the testbench does not have
    (Xyce stops on them; ngspice saves nothing for them)."""
    here = tree.element_names(scope)
    every = None
    missing = []
    for epath, name in ss.elements:
        if list(epath) == list(path):
            ok = name.upper() in here
        else:
            if every is None:
                every = {e.name.upper() for e in tree.top_elements}
                every |= {e.name.upper() for sc in tree.subckts.values() for e in sc.elements}
            ok = name.upper() in every
        if not ok and name not in missing:
            missing.append(name)
    if missing:
        where = f"subcircuit {tree.subckts[scope].name}" if scope else "the top level"
        raise EmirError("EMIR-E263", f"{len(missing)} resistor(s) whose branch currents rcc "
                        f"needs are not in {where} of {tree.top.path}: {', '.join(missing[:8])}"
                        f"{' ...' if len(missing) > 8 else ''}. Is this the testbench of this "
                        f"DSPF (the RC netlist)?", path=tree.top.path)


def _error_lines(text: str) -> str:
    keep = [ln for ln in text.splitlines()
            if re.search(r"error|fatal|abort|unknown|not found|could not|can't", ln, re.I)]
    return "\n".join(keep[:20])


#: lines of a deck or a `.spiceinit` that make ngspice run commands: a
#: `.control` block (its `shell` runs any program), a bare `shell` line
_CODE_LINE = re.compile(r"^\s*(\.control\b|shell\b)", re.I)
_INCLUDE_LINE = re.compile(r'^\s*\.(?:include|inc|lib)\s+["\']?([^"\'\s]+)', re.I | re.M)


def deck_runs_code(testbench: str, limit: int = 64) -> list:
    """The files among the testbench, the files its `.include`/`.lib` lines
    name (followed a few levels, relative to the including file) and the
    `.spiceinit` beside it that hold a `.control` block or a `shell` line:
    ngspice runs those as commands. [] when none (or nothing readable)."""
    base = os.path.dirname(os.path.abspath(testbench))
    todo = [os.path.abspath(testbench), os.path.join(base, ".spiceinit")]
    seen, hits = set(), []
    while todo and len(seen) < limit:
        path = todo.pop(0)
        if path in seen or not os.path.isfile(path):
            continue
        seen.add(path)
        try:
            if os.path.getsize(path) > 50_000_000:
                continue
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        for ln in text.splitlines():
            if _CODE_LINE.match(ln):
                hits.append(path)
                break
        for m in _INCLUDE_LINE.finditer(text):
            name = os.path.expanduser(m.group(1))
            todo.append(os.path.normpath(os.path.join(os.path.dirname(path), name)))
    return hits


def spiceinit(testbench: str, deck: str) -> str | None:
    """ngspice reads `.spiceinit` from the directory it runs in (else the
    home directory), and the deck runs beside the result: a `.spiceinit`
    beside the testbench (open PDKs ship one: compatibility mode, model
    checks) is copied there. One already there with other content is never
    overwritten (it may be the person's own). Returns what was done, or None."""
    import shutil
    src = os.path.join(os.path.dirname(os.path.abspath(testbench)), ".spiceinit")
    dst = os.path.join(os.path.dirname(deck), ".spiceinit")
    if not os.path.isfile(src) or os.path.abspath(src) == os.path.abspath(dst):
        return None
    if os.path.isfile(dst):
        with open(src, "rb") as a, open(dst, "rb") as b:
            if a.read() == b.read():
                return f"{dst} already holds the .spiceinit beside the testbench"
        raise EmirError("EMIR-E105", f"{dst} exists and differs from {src}: emir does not "
                        f"overwrite it (ngspice reads it where the deck runs); remove it or "
                        f"write the result to another directory", path=dst)
    try:
        shutil.copyfile(src, dst)
    except OSError as e:
        raise EmirError("EMIR-E105", f"cannot copy {src} to {dst}: {e.strerror or e}",
                        path=dst) from e
    return f"{src} copied beside the deck (ngspice reads .spiceinit where it runs)"


def sim_command(sim: str, exe: str, deck: str, raw: str, extra=()) -> list:
    """The command that runs `deck` (in the result's directory) and writes
    `raw`: Xyce `-a` (ASCII raw), ngspice `-b -r`, HSPICE `-i deck -o stem`
    (writes stem.tr0; the deck asks for ASCII), Spectre `-format psfascii
    -raw dir`; `extra` (--sim-args: Spectre `+aps`, `+preset=cx` ...) is
    appended."""
    if sim == "xyce":
        cmd = [exe, "-a", deck]
    elif sim == "ngspice":
        cmd = [exe, "-b", "-r", os.path.basename(raw), deck]
    elif sim == "hspice":
        stem = os.path.basename(raw)
        stem = stem[:-len(".tr0")] if stem.lower().endswith(".tr0") else stem
        cmd = [exe, "-i", os.path.basename(deck), "-o", stem]
    else:
        cmd = [exe, os.path.basename(deck), "-format", "psfascii", "-raw",
               os.path.basename(raw)]
    if extra:
        # ngspice and Xyce read the deck last: the options go before it
        cmd = cmd[:-1] + list(extra) + cmd[-1:] if sim in ("xyce", "ngspice") \
            else cmd + list(extra)
    return cmd


def sim_args(text: str | None, command: str = "simulate") -> list:
    """`--sim-args` split as a shell would (EMIR-E513 when it does not split)."""
    if not text:
        return []
    try:
        return shlex.split(text)
    except ValueError as e:
        raise EmirError("EMIR-E513", f"--sim-args {text!r}: {e}", command=command) from e


def run_simulator(sim: str, exe: str, deck: str, raw: str, timeout: float, log=_stderr,
                  extra=(), option: str = "--timeout"):
    """Run one deck; the result is `raw` (an ASCII result file, or Spectre's
    PSF ASCII directory). Coded failures; `option` is the command's option
    that sets `timeout` (EMIR-E605 names it)."""
    import shutil
    cwd = os.path.dirname(raw)
    cmd = sim_command(sim, exe, deck, raw, extra)
    logf = raw + ".log"
    log(f"[emir] {sim}: {shlex.join(cmd)}  (in {cwd})")
    t0 = time.time()

    def drop():
        # a stopped or failed run leaves no result that could pass for one
        with contextlib.suppress(OSError):
            if os.path.isdir(raw):
                shutil.rmtree(raw)
            else:
                os.remove(raw)
    try:
        # a wrapper's simulator is stopped with it (its process group)
        p = subproc.run(cmd, timeout=timeout, cwd=cwd)
    except subprocess.TimeoutExpired as e:
        part = e.stdout or ""
        _write_log(logf, part.decode("utf-8", "replace") if isinstance(part, bytes) else part)
        drop()
        raise EmirError("EMIR-E605", f"{sim} ran longer than {timeout:g} s on {deck} and was "
                        f"stopped; run it yourself (the deck is ready) or raise {option}",
                        path=logf) from e
    secs = time.time() - t0
    text = (p.stdout or "") + (p.stderr or "")
    if sim == "hspice":
        # HSPICE writes its messages to <stem>.lis
        lis = os.path.splitext(raw)[0] + ".lis"
        with contextlib.suppress(OSError):
            with open(lis, encoding="utf-8", errors="replace") as f:
                text += "\n" + f.read()
    _write_log(logf, text)
    if p.returncode != 0:
        raise EmirError("EMIR-E602", f"{sim} failed on {deck} (exit {p.returncode}):\n"
                        f"{_error_lines(text) or text[-2000:]}", path=logf)
    empty = (not os.path.exists(raw) or (os.path.isdir(raw) and not os.listdir(raw))
             or (os.path.isfile(raw) and os.path.getsize(raw) == 0))
    if empty:
        raise EmirError("EMIR-E661", f"{sim} exited 0 but wrote no result at {raw}: did the "
                        f"testbench run a transient analysis? (log: {logf})", path=logf)
    return secs, logf


def _write_log(path, text):
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError:
        pass


def run_spectre(netlist: str, out_dir=None, fmt="psfascii", extra=(),
                spectre=None, timeout=3600, log=print) -> dict:
    """Run one Spectre netlist (`emir run --netlist ... --run`) and return
    where the results landed.

    Always asks for a text format: binary PSF is not readable here (see
    `waves`), and being explicit beats depending on the site default.
    """
    from . import site
    exe = site.spectre_bin(spectre)
    netlist = os.path.abspath(netlist)
    out_dir = os.path.abspath(out_dir or os.path.splitext(netlist)[0] + ".raw")
    logf = out_dir + ".log"
    cmd = [exe, netlist, "-format", fmt, "-raw", out_dir, "+escchars",
           "=log", logf, *extra]
    log(f"[emir] spectre {os.path.basename(netlist)} -> {out_dir}")
    t0 = time.time()
    try:
        # a wrapper's simulator is stopped with it (its process group)
        p = subproc.run(cmd, timeout=timeout, cwd=os.path.dirname(netlist) or ".")
    except subprocess.TimeoutExpired as e:
        raise EmirError("EMIR-E605", f"spectre ran longer than {timeout:g} s on {netlist} "
                        f"and was stopped; run it yourself and pass the result with "
                        f"--cc-sim", path=logf) from e
    dt = time.time() - t0
    tail = (p.stdout or "") + (p.stderr or "")
    if p.returncode != 0:
        err = "\n".join(ln for ln in tail.splitlines()
                        if re.search(r"error|fatal", ln, re.I))[:2000]
        raise EmirError("EMIR-E602", f"spectre failed on {netlist} (exit {p.returncode})"
                           f"\n{err or tail[-2000:]}", path=logf)
    log(f"[emir] spectre finished in {dt:.2f} s")
    return {"raw": out_dir, "log": logf, "seconds": dt, "netlist": netlist,
            "stdout": tail}


def cmd_simulate(argv, out) -> int:
    """`emir simulate`."""
    ap = parser("emir simulate", description=(
        "Simulate a testbench so that `emir run --cc-sim` can read the currents: writes a "
        "copy of the testbench with the lines that save every branch current rcc reads "
        "(Xyce .print, ngspice .save, HSPICE .probe with ASCII output; Spectre: a deck "
        "that includes the testbench and a file of save statements), runs it to a text "
        "result, and writes the <result>.names sidecar that maps the simulator's names to "
        "the DSPF's. The testbench is the person's: stimulus, models, a .tran analysis, "
        "and the RC-extracted netlist (the DSPF itself). Spectre and HSPICE need a "
        "licence: without --run the deck is written and the exact command printed, "
        "nothing runs (ask the person, then add --run)."))
    ap.add_argument("testbench", help="the testbench (its .include files are followed; "
                                      "Spectre: any syntax Spectre reads)")
    ap.add_argument("--sim", required=True, choices=SIMS,
                    help="the simulator (spectre and hspice: licensed, planned unless --run)")
    ap.add_argument("--dspf", required=True, help="the DSPF the currents are for")
    ap.add_argument("--net", default=None, help="one net (default: every net)")
    ap.add_argument("--mode", default=None, choices=("rcc",),
                    help="rcc, the one method (it may be left out): the testbench simulates "
                         "the RC netlist and every branch current is saved")
    ap.add_argument("--out", default=os.path.join("emir_out", "sim", "result.raw"),
                    metavar="RAW", help="the result to write (default "
                                        "emir_out/sim/result.raw; HSPICE: <stem>.tr0; "
                                        "Spectre: a PSF ASCII directory); the deck, the log "
                                        "and the names sidecar go beside it")
    ap.add_argument("--prefix", default=None,
                    help="the instance path of the extracted block in the testbench, e.g. "
                         "XI0. (default: found in the testbench; Spectre: required, '' for "
                         "the top level)")
    ap.add_argument("--subckt", default=None,
                    help="the block's .subckt name (default: the DSPF's design)")
    ap.add_argument("--process", default=None,
                    help="the process file, so the next steps are the exact check and run "
                         "commands")
    ap.add_argument("--timeout", type=float, default=3600.0, metavar="S",
                    help="stop the simulator after this many seconds (default 3600)")
    ap.add_argument("--run", action="store_true",
                    help="spectre, hspice (licensed): run the simulator; without it the deck "
                         "is written and the command printed, and nothing runs (the open "
                         "simulators always run)")
    ap.add_argument("--sim-args", default=None, metavar="ARGS",
                    help="extra arguments for the simulator's command line, split as a "
                         "shell would: Spectre APS '+aps', Spectre X '+preset=cx "
                         "+postlpreset=off' (keep the parasitic resistors for rcc) ...")
    a = ap.parse_args(argv)
    a.extra = sim_args(a.sim_args)
    from .dspf import did_you_mean, parse
    d = parse(a.dspf)
    if a.net and a.net != "all" and not d.has_net(a.net):
        raise EmirError("EMIR-E204", f"net {a.net!r} is not in {a.dspf}"
                        f"{did_you_mean(a.net, d.nets)}", dspf=a.dspf)
    raw = os.path.abspath(a.out)
    if a.sim == "hspice" and not raw.lower().endswith(".tr0"):
        # HSPICE names its result <stem>.tr0 (`-o stem`)
        raw = os.path.splitext(raw)[0] + ".tr0"
    if re.search(r"\s", os.path.basename(raw)):
        raise EmirError("EMIR-E513", f"--out {a.out!r}: the result's file name cannot hold "
                        f"a space (Xyce reads `file=` up to the first blank)",
                        command="simulate")
    if a.sim == "spectre":
        return _simulate_spectre(a, argv, out, d, raw)
    tree = Tree(a.testbench)
    if not tree.analyses:
        raise EmirError("EMIR-E462", f"{a.testbench} (and its includes) has no .tran "
                        f"analysis: EM needs the currents over time", path=a.testbench)
    scope, why_scope = scope_of(tree, d, a.subckt)
    path, why_path = scope_path(tree, scope, a.prefix, command="simulate")
    nets = target_nets(d, a.net)
    if not nets:
        raise EmirError("EMIR-E209", f"no net of {a.dspf} has resistors", dspf=a.dspf)
    ss = save_set(d, nets, a.sim, path)
    _check_elements(tree, scope, path, ss)
    from .verify_sim import args_key
    # the sidecar says how the result was made: the simulator, its version
    # (when it runs here) and the arguments (`emir verify-sim` keys on them)
    ss.sim_args = args_key(a.sim_args)
    licensed = a.sim in LICENSED_SIMS
    exe = None
    if not licensed or a.run:
        exe = sim_exe(a.sim)
        notice("EMIR-I660", f"{a.sim} found: {exe}", _stderr)
    stem = os.path.splitext(raw)[0]
    deck = f"{stem}.{a.sim}.{'sp' if a.sim == 'hspice' else 'cir'}"
    for stale in (raw, raw + ".names"):
        if os.path.exists(stale):
            os.remove(stale)
    lines = ss.lines(raw=os.path.basename(raw), deck=os.path.basename(deck))
    copies = Writer(tree, deck, a.sim, inject=lines, strip=a.sim == "xyce",
                    command="simulate").write()
    if tree.top.alter is not None:
        notice("EMIR-I610", f"{a.testbench} has .alter cases: the {len(ss.signals)} saved "
               f"currents go before the first .alter (line "
               f"{tree.top.chunks[tree.top.alter].lineno}), so the main case and every "
               f"altered case save them; the result read is the main case's", _stderr)
    stripped = [p for p, sf in tree.files.items() if sf.annotated and a.sim == "xyce"]
    if stripped:
        notice("EMIR-I261", f"the DSPF $ annotations of {len(stripped)} file(s) are left out "
               f"of the {a.sim} deck (Xyce reads them as parameters): "
               f"{', '.join(os.path.basename(p) for p in stripped[:4])}", _stderr)
    if tree.has_control and a.sim == "ngspice":
        notice("EMIR-W660", "the testbench has a .control section: ngspice -b -r runs its "
               "commands too (what it sets applies to the result; a `shell` line runs a "
               "program): the deck is code, run it only if the person trusts it", _stderr)
    notes = []
    if a.sim == "ngspice":
        init = spiceinit(a.testbench, deck)
        if init:
            notes.append(init)
            notice("EMIR-I262", init, _stderr)
    common = dict(simulator=a.sim, testbench=os.path.abspath(a.testbench), deck=deck,
                  files=[{"from": s_, "to": t} for s_, t in copies.items()], result=raw,
                  mode="rcc", nets=nets,
                  scope=tree.subckts[scope].name if scope else None, path=path,
                  where=f"{why_scope}; {why_path}", signals=len(ss.signals),
                  direct=ss.direct, analysis=tree.analyses[0])
    if licensed and not a.run:
        side = write_sidecar(raw + ".names", ss.sidecar())
        return _simulate_plan(a, argv, out, raw, deck, os.path.abspath(side), common, notes)
    if a.sim in ("hspice", "spectre"):
        ss.version = sim_version(a.sim, exe)
    elif exe:
        ss.version = sim_version(a.sim, exe, timeout=30.0)
    secs, logf = run_simulator(a.sim, exe, deck, raw, a.timeout, extra=a.extra)
    side = write_sidecar(raw + ".names", ss.sidecar())
    from . import waves
    w = waves.read(raw)
    got = w.match(ss.dspf_names)
    missing = [n for n in ss.dspf_names if n not in got]
    if missing:
        notice("EMIR-W461", f"{len(missing)} of the {len(ss.dspf_names)} currents asked for "
               f"are not in {raw}: {', '.join(missing[:6])}"
               f"{' ...' if len(missing) > 6 else ''}", _stderr)
    print(f"[emir] {a.sim} finished in {secs:.2f} s: {w.summary()}")
    print(f"  result : {raw}\n  names  : {os.path.abspath(side)}\n  deck   : {deck}\n"
          f"  log    : {logf}")
    out.emit(document(
        "simulate", **common, ran=True, executable=exe, simulator_version=ss.version,
        command=sim_command(a.sim, exe, deck, raw, a.extra), cwd=os.path.dirname(raw),
        names_sidecar=os.path.abspath(side), log=logf, found=len(got), missing=missing,
        seconds=round(secs, 3), points=int(len(w.x)), notes=notes,
        next_steps=simulate_next_steps(a, raw, out, w=w)))
    return 0


def _simulate_plan(a, argv, out, raw, deck, side, common, notes) -> int:
    """A licensed simulator without --run: the deck is written, the command
    printed, nothing runs (EMIR-I661); the next step is the same command
    with --run, after the person's go-ahead."""
    from . import site
    exe, _src = site.tool(a.sim)
    cmd = sim_command(a.sim, exe or a.sim, deck, raw, a.extra)
    notice("EMIR-I661", f"{a.sim} needs a licence: nothing was run. Ask the person; then "
           f"re-run with --run (or run `{shlex.join(cmd)}` in {os.path.dirname(raw)})",
           _stderr)
    print(f"[emir] {a.sim} (licensed): the deck is ready, nothing was run")
    print(f"  deck    : {deck}\n  command : {shlex.join(cmd)}\n  in      : "
          f"{os.path.dirname(raw)}\n  result  : {raw}"
          + (f"\n  names   : {side}" if side else ""))
    steps = [{"why": f"ask the person first ({a.sim} is licensed); then run it: the same "
                     f"command with --run simulates, reads the result back and checks the "
                     f"currents",
              "command": command_line("simulate", strip_options(argv, {"--format": 1}),
                                      "--run", out.fmt_args())}]
    out.emit(document(
        "simulate", **common, ran=False, executable=exe, simulator_version=None,
        command=cmd, cwd=os.path.dirname(raw), names_sidecar=side, log=None, found=None,
        missing=[], seconds=None, points=None, notes=notes, next_steps=steps))
    return 0


def _simulate_spectre(a, argv, out, d, raw) -> int:
    """`emir simulate --sim spectre`: the testbench is not read (it may be in
    Spectre's own syntax); a deck includes it and a file of `save`
    statements, and Spectre writes PSF ASCII into the `raw` directory."""
    if a.prefix is None:
        raise EmirError("EMIR-E507", "--sim spectre needs --prefix: the instance path of the "
                        "extracted block in the testbench (e.g. XI0.), or --prefix '' when "
                        "its elements are at the top level (the testbench is not read)",
                        command="simulate")
    if not os.path.isfile(a.testbench):
        raise EmirError("EMIR-E101", f"{a.testbench}: not found", path=a.testbench)
    from .rcc import save_branches
    nets = target_nets(d, a.net)
    if not nets:
        raise EmirError("EMIR-E209", f"no net of {a.dspf} has resistors", dspf=a.dspf)
    text = "\n".join(save_branches(d, n, prefix=a.prefix) for n in nets)
    names = [t for ln in text.splitlines() if ln.startswith("save ")
             for t in ln.split()[1:]]
    stem = os.path.splitext(raw)[0]
    deck = f"{stem}.spectre.scs"
    save_file = f"{stem}.save.scs"
    from .output import write_text
    write_text(save_file, "// emir: the currents a run needs (Spectre save statements)\n"
               "simulator lang=spectre\n" + text + "\n")
    write_text(deck, spectre_deck(os.path.abspath(a.testbench), save_file))
    for stale in (raw,):
        if os.path.isdir(stale) and not os.listdir(stale):
            os.rmdir(stale)
    dspf_names = [n[len(a.prefix):] if a.prefix and n.startswith(a.prefix) else n
                  for n in names]
    common = dict(simulator="spectre", testbench=os.path.abspath(a.testbench), deck=deck,
                  files=[{"from": os.path.abspath(a.testbench), "to": deck},
                         {"from": "(save statements)", "to": save_file}], result=raw,
                  mode="rcc", nets=nets, scope=None, path=split_path(a.prefix),
                  where=f"--prefix {a.prefix!r}", signals=len(names), direct=len(names),
                  analysis=None)
    from .verify_sim import args_key
    # Spectre's names need no map; the sidecar beside the result directory
    # (`<result>.names`) keeps how it was made: the version (when Spectre
    # runs here) and the simulator arguments (`emir verify-sim` keys on them)
    facts = {"map": {}, "simulator": "spectre", "sim_args": args_key(a.sim_args)}
    side = raw.rstrip(os.sep) + ".names"
    if not a.run:
        write_sidecar(side, facts)
        return _simulate_plan(a, argv, out, raw, deck, os.path.abspath(side), common, [])
    exe = sim_exe("spectre")
    version = sim_version("spectre", exe)
    if os.path.isdir(raw):
        import shutil
        shutil.rmtree(raw)
    if os.path.exists(side):
        os.remove(side)
    secs, logf = run_simulator("spectre", exe, deck, raw, a.timeout, extra=a.extra)
    write_sidecar(side, {**facts, **({"simulator_version": version} if version else {})})
    from . import waves
    w = waves.read(raw)
    got = w.match(dspf_names)
    missing = [n for n in dspf_names if n not in got]
    if missing:
        notice("EMIR-W461", f"{len(missing)} of the {len(dspf_names)} currents asked for "
               f"are not in {raw}: {', '.join(missing[:6])}"
               f"{' ...' if len(missing) > 6 else ''}", _stderr)
    print(f"[emir] spectre finished in {secs:.2f} s: {w.summary()}")
    out.emit(document(
        "simulate", **common, ran=True, executable=exe, simulator_version=version,
        command=sim_command("spectre", exe, deck, raw, a.extra), cwd=os.path.dirname(raw),
        names_sidecar=os.path.abspath(side), log=logf, found=len(got), missing=missing,
        seconds=round(secs, 3), points=int(len(w.x)), notes=[],
        next_steps=simulate_next_steps(a, raw, out, w=w)))
    return 0


_SPECTRE_LANG = re.compile(r"^\s*simulator\s+lang\s*=\s*spectre\b", re.I)


def is_spectre(testbench: str, lines: int = 500) -> bool:
    """Whether a testbench is in Spectre's language: its name ends in .scs,
    or one of its first `lines` lines is `simulator lang=spectre` (a deck
    Xyce, ngspice and HSPICE cannot read)."""
    if str(testbench).lower().endswith(".scs"):
        return True
    try:
        with open(testbench, encoding="utf-8", errors="replace") as f:
            for _, ln in zip(range(lines), f):
                if _SPECTRE_LANG.match(ln):
                    return True
    except OSError:
        return False
    return False


def spectre_deck(testbench: str, save_file: str) -> str:
    """The deck `emir simulate --sim spectre` runs: the person's testbench,
    then the save statements. Spectre reads an included file in Spectre
    syntax when its name ends in .scs or it starts with `simulator
    lang=spectre`, else in SPICE syntax, and restores the language after it
    (Spectre Circuit Simulator Reference 19.1, "Include File")."""
    return ("// written by emir simulate --sim spectre: the testbench, then the save\n"
            "// statements EMIR needs. Run: spectre <this file> -format psfascii -raw <dir>\n"
            "simulator lang=spectre\n"
            f'include "{testbench}"\n'
            f'include "{os.path.basename(save_file)}"\n')


def simulate_next_steps(a, raw, out, w=None) -> list:
    """After a simulation: check, run and report (with --process), else
    `emir next` with the result -- which asks for the block's period when the
    result is a transient (rcc averages over whole periods of it, and a
    transient without --period is refused: EMIR-E428)."""
    net = _net_args(a.net)
    if a.process and w is not None and len(w.x) > 1:
        return [{"why": "plan the screening of this result: a transient is averaged over "
                        "whole periods of the block's --period (EMIR-E428 without it), so "
                        "the plan asks for it",
                 "command": command_line("next", "--dspf", os.path.abspath(a.dspf),
                                         "--process", os.path.abspath(a.process),
                                         "--cc-sim", raw, *net, out.fmt_args())}]
    if not a.process:
        return [{"why": "plan the screening of this result (it reads the result and the "
                        "sidecar beside it)",
                 "command": command_line("next", "--dspf", os.path.abspath(a.dspf), "--cc-sim",
                                         raw, *net, out.fmt_args())}]
    args = [os.path.abspath(a.dspf), "--process", os.path.abspath(a.process), *net,
            "--cc-sim", raw]
    summary = os.path.join("emir_out", "summary.json")
    return [{"why": "check the inputs before the run (exit 2 and coded FAILs if not)",
             "command": command_line("check", args, out.fmt_args())},
            {"why": "screen EM and IR from the simulated branch currents",
             "command": command_line("run", args, "--summary-out", summary, out.fmt_args())},
            {"why": "the summary to relay to the person (screening, not sign-off)",
             "command": command_line("report", "--from", summary)}]
