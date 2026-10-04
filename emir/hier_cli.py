"""The command-line side of the hierarchy flow (`emir/hier.py` is the maths).

    emir abstract BLOCK.dspf --process P --cc-sim R --mode cc-grid [--net A,B] [--period S]
                  [--points N] [--vdd V | --vdd NET=V ...]
                  [--port-voltages F@INST] -o X.abstract.json
    emir run TOP.dspf --process P --abstract X.abstract.json@INST [...]
                  [--place INST=DX,DY,ORIENT] [--align inphase|sync|async]
                  [--phase INST=S | --phase-sweep INST:N] [--budget-mv MV]
                  [--port-voltages-out F]

Nothing here imports numpy, so `emir --help` and the parsers stay fast; the
numerical work is imported when a command runs.
"""
from __future__ import annotations

import math
import os
import shlex
import sys

from . import RESEARCH_DISCLAIMER, RESEARCH_LINE
from .errors import EmirError

ALIGNS = ("inphase", "sync", "async")
#: samples of the one stored period of each pin current
DEFAULT_POINTS = 4096
#: how far (um) a placed block port may sit from its top-level record
PLACE_TOL_UM = 1.0
#: the most phase combinations --phase-sweep evaluates
MAX_SWEEP = 4096

#: orientation -> (a, b, c, d): x' = a x + b y + dx, y' = c x + d y + dy
ORIENT = {"R0": (1, 0, 0, 1), "R90": (0, -1, 1, 0), "R180": (-1, 0, 0, -1),
          "R270": (0, 1, -1, 0), "MX": (1, 0, 0, -1), "MY": (-1, 0, 0, 1),
          "MXR90": (0, 1, 1, 0), "MYR90": (0, -1, -1, 0)}


def split_at(text: str, sep: str, what: str, command: str | None = None):
    """`FILE@INST` -> (FILE, INST) (the last `@`); `INST=V`, `INST:N` at the
    first `=` / `:`. A usage error (EMIR-E507) otherwise."""
    form = {"@": "FILE@INST", "=": "INST=VALUE", ":": "INST:N"}[sep]
    if sep not in text:
        raise EmirError("EMIR-E507", f"{what} takes {form}, got {text!r}", command=command)
    a, b = text.rsplit(sep, 1) if sep == "@" else text.split(sep, 1)
    if not a or not b:
        raise EmirError("EMIR-E507", f"{what} takes {form}: {text!r} has an empty part",
                        command=command)
    return a, b


# ===================================================================== run / check options


class Options:
    """The hierarchy options of one `emir run` / `emir check`, parsed and
    checked (EMIR-E507 for a malformed value, EMIR-E802 for an instance no
    --abstract gives)."""

    def __init__(self, a, command="run"):
        self.abstracts: dict = {}
        for s in getattr(a, "abstract", None) or []:
            f, inst = split_at(s, "@", "--abstract", command)
            if inst in self.abstracts:
                raise EmirError("EMIR-E507", f"--abstract: instance {inst!r} given twice",
                                command=command)
            self.abstracts[inst] = f
        self.align = getattr(a, "align", None) or "inphase"
        tol = getattr(a, "place_tol", None)
        self.tol = float(PLACE_TOL_UM if tol is None else tol)
        self.budget = getattr(a, "budget_mv", None)
        self.pv_out = getattr(a, "port_voltages_out", None)
        self.place: dict = {}
        for s in getattr(a, "place", None) or []:
            inst, v = split_at(s, "=", "--place", command)
            self.place[inst] = _placement(s, v, command)
        self.phase: dict = {}
        for s in getattr(a, "phase", None) or []:
            inst, v = split_at(s, "=", "--phase", command)
            x = _number(v)
            if x is None:
                raise EmirError("EMIR-E507", f"--phase {s!r}: not a number of seconds",
                                command=command)
            self.phase[inst] = x
        self.sweep: dict = {}
        for s in getattr(a, "phase_sweep", None) or []:
            inst, v = split_at(s, ":", "--phase-sweep", command)
            if not (v.strip().isdigit() and int(v) >= 1):
                raise EmirError("EMIR-E507", f"--phase-sweep {s!r}: N must be a whole "
                                f"number >= 1", command=command)
            self.sweep[inst] = int(v)
        if (self.phase or self.sweep) and self.align != "sync":
            raise EmirError("EMIR-E507", "--phase and --phase-sweep shift blocks on one "
                            "clock reference: they need --align sync", command=command)
        n = math.prod(self.sweep.values()) if self.sweep else 1
        if n > MAX_SWEEP:
            raise EmirError("EMIR-E507", f"--phase-sweep: {n} phase combinations, more than "
                            f"{MAX_SWEEP}", command=command)
        if self.budget is not None and not (math.isfinite(self.budget) and self.budget > 0):
            raise EmirError("EMIR-E507", "--budget-mv must be a positive number",
                            command=command)
        if not (math.isfinite(self.tol) and self.tol > 0):
            raise EmirError("EMIR-E507", "--place-tol must be a positive number",
                            command=command)
        for what, m in (("--place", self.place), ("--phase", self.phase),
                        ("--phase-sweep", self.sweep)):
            for inst in m:
                if inst not in self.abstracts:
                    raise EmirError("EMIR-E802", f"{what} names {inst!r}, which no "
                                    f"--abstract FILE@INST gives",
                                    dspf=getattr(a, "dspf", None))

    def incompatible(self, a) -> list:
        """The options of `a` that are not combined with --abstract (E811)."""
        bad = []
        for opt, attr in (("--currents", "currents"), ("--netlist", "netlist"),
                          ("--self-consistent", "self_consistent"), ("--ground", "ground"),
                          ("--taps-out", "taps_out"), ("--dynamic", "dynamic")):
            if getattr(a, attr, None):
                bad.append(opt)
        if getattr(a, "mode", None) == "rcc":
            bad.append("--mode rcc")
        if getattr(a, "scalars", False) and self.align != "inphase":
            bad.append(f"--scalars with --align {self.align}")
        return bad


def _number(text):
    """A finite float, or None."""
    try:
        x = float(text)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _placement(s, v, command):
    """`DX,DY[,ORIENT]` -> (dx, dy, orient); EMIR-E507 when malformed."""
    f = [x.strip() for x in v.split(",")]
    o = f[2].upper() if len(f) == 3 else "R0"
    dx, dy = (_number(f[0]), _number(f[1])) if len(f) in (2, 3) else (None, None)
    if dx is None or dy is None or o not in ORIENT:
        raise EmirError("EMIR-E507", f"--place {s!r}: give INST=DX,DY[,ORIENT] with DX, DY "
                        f"in um and ORIENT one of {', '.join(ORIENT)}", command=command)
    return (dx, dy, o)


def add_run_options(ap):
    """The hierarchy options of `emir run` and `emir check`."""
    g = ap.add_argument_group(
        "hierarchy: blocks black-boxed in the top DSPF, characterised by `emir abstract`")
    g.add_argument("--abstract", action="append", default=None, metavar="FILE@INST",
                   help="a block abstract (emir abstract -o FILE) at instance INST of the "
                        "top DSPF; repeat per block. The run's currents then come from the "
                        "abstracts.")
    g.add_argument("--place", action="append", default=None, metavar="INST=DX,DY[,ORIENT]",
                   help="the block's origin in the top frame (um) and its orientation "
                        "(R0 R90 R180 R270 MX MY MXR90 MYR90; default R0): needed when a "
                        "block pin reaches the top at several places")
    g.add_argument("--place-tol", type=float, default=PLACE_TOL_UM, metavar="UM",
                   help=f"how far a placed port may sit from its record (default "
                        f"{PLACE_TOL_UM:g} um)")
    g.add_argument("--align", choices=ALIGNS, default="inphase",
                   help="how the blocks' currents line up in time: inphase (default; the "
                        "upper bound), sync (one clock reference; exact for it), async "
                        "(unrelated clocks only: NOT an upper bound for related clocks)")
    g.add_argument("--phase", action="append", default=None, metavar="INST=SECONDS",
                   help="with --align sync: shift a block's period by this much")
    g.add_argument("--phase-sweep", action="append", default=None, metavar="INST:N",
                   help="with --align sync: the worst over N phases of INST's period "
                        "(related clocks of unknown phase)")
    g.add_argument("--budget-mv", type=float, default=None, metavar="MV",
                   help="IR budget per block: the top-level drop at its worst pin plus its "
                        "internal drop must stay within MV")
    g.add_argument("--port-voltages-out", default=None, metavar="FILE",
                   help="write the top-level voltage at every block pin "
                        "(emir.port_voltages/1) for `emir abstract --port-voltages "
                        "FILE@INST` (back-annotation)")
    return ap


# ===================================================================== emir abstract


def parse_vdd(items) -> dict:
    """`--vdd V` (every net) and `--vdd NET=V` -> `{net or "*": volts}`."""
    out = {}
    for s in items or []:
        net, v = s.split("=", 1) if "=" in s else ("*", s)
        x = _number(v)
        if x is None or not net:
            raise EmirError("EMIR-E507", f"--vdd {s!r}: give V or NET=V (volts)",
                            command="abstract")
        out[net] = x
    return out


def abstract_parser():
    from . import site
    from .output import add_dspf, parser
    ap = parser(
        "emir abstract",
        description="Characterise a block once for a chip-level (hierarchical) run: its "
                    "own EM/IR verdict with every pin of a supply held at one potential, "
                    "one period of the current each pin draws, the exact conductance "
                    "between its pins, and its decap lump. The top-level run then places "
                    "it with `emir run TOP.dspf --abstract FILE@INST`. Screening, not "
                    "sign-off.")
    add_dspf(ap, "the block's R-only DSPF, every supply pin shape a *|P port with "
                 "coordinates")
    ap.add_argument("--process", default=site.get("process")[0],
                    help="the process file (default from the site file)")
    ap.add_argument("--cc-sim", required=True, metavar="RESULT",
                    help="the block's CC simulation (instance-pin currents saved, ASCII), "
                         "over whole periods of its activity")
    from .modes import mode_arg
    ap.add_argument("--mode", required=True, type=mode_arg, choices=("rcc", "cc-grid"),
                    help="the person's choice of mode, given explicitly: an abstract is "
                         "built from the pin currents of a CC simulation superposed "
                         "through the block's R-only grid, so only cc-grid works here "
                         "(rcc is refused, EMIR-E521)")
    ap.add_argument("--net", default=None, metavar="A,B",
                    help="the block nets to abstract (default: every net with a port and "
                         "instance pins)")
    ap.add_argument("--period", type=float, default=None, metavar="S",
                    help="the block's period in s (auto-detected if omitted); the stored "
                         "period is the last settled one")
    ap.add_argument("--points", type=int, default=DEFAULT_POINTS, metavar="N",
                    help=f"samples of the stored period (default {DEFAULT_POINTS})")
    ap.add_argument("--analysis", default=None,
                    help="which analysis to read from a result directory")
    ap.add_argument("--vdd", action="append", default=None, metavar="V|NET=V",
                    help="the nominal supply of the nets (repeatable): the top-level run "
                         "warns when the droop at a pin exceeds 2 %% of it")
    ap.add_argument("--em-temp", type=float, default=None, metavar="C",
                    help="the EM analysis temperature (default: the process file's "
                         "em_tref); use the top run's")
    ap.add_argument("--delta-t", type=float, default=5.0, metavar="C",
                    help="the self-heating rise the rms limits allow (default 5)")
    ap.add_argument("--port-voltages", default=None, metavar="FILE@INST",
                    help="back-annotation: re-check the block with the top-level pin "
                         "voltages of instance INST (emir run ... --port-voltages-out FILE)")
    ap.add_argument("--abstract", action="append", default=None, metavar="FILE@INST",
                    help="a sub-block black-boxed in this block's DSPF, by its own abstract "
                         "(recursion: a mid-level block abstracted from its sub-blocks); its "
                         "currents and this block's devices go on one clock reference")
    ap.add_argument("--place", action="append", default=None, metavar="INST=DX,DY[,ORIENT]",
                    help="a sub-block's placement in this block, for its pins at several "
                         "connections")
    ap.add_argument("--place-tol", type=float, default=PLACE_TOL_UM, metavar="UM",
                    help=f"how far a placed port may sit from its record (default "
                         f"{PLACE_TOL_UM:g} um)")
    ap.add_argument("--phase", action="append", default=None, metavar="INST=SECONDS",
                    help="shift a sub-block's period against this block's clock")
    ap.add_argument("-o", "--out", required=True, metavar="FILE",
                    help="the abstract to write (emir.abstract/1)")
    ap.add_argument("--fail-on-violation", action="store_true",
                    help="exit 1 when the block's verdict is FAIL")
    ap.add_argument("--no-check", action="store_true",
                    help="skip the input check of the process file and layer map")
    ap.add_argument("--force", action="store_true",
                    help="run even if the input check reports FAIL")
    ap.add_argument("--top", default=None, metavar="TOP.dspf@INST",
                    help="the top DSPF this block is placed in and its instance there: the "
                         "summary's next step is then `emir next` on the top with this "
                         "abstract, the way back to the chip-level run (`emir next` plans "
                         "it so)")
    return ap


def abstract_command(ab: dict, extra=(), fmt=()) -> str | None:
    """The `emir abstract` command that rebuilds `ab` (its recorded inputs),
    plus `extra`; None when one of its input files is no longer there."""
    from .output import command_line
    i = ab.get("inputs") or {}
    files = [i.get("dspf"), i.get("cc_sim"), i.get("process")]
    if not all(isinstance(p, str) and os.path.isfile(p) for p in files):
        return None
    args = ["abstract", i["dspf"], "--process", i["process"], "--cc-sim", i["cc_sim"],
            "--mode", "cc-grid"]
    if i.get("nets"):
        args += ["--net", ",".join(i["nets"])]
    if i.get("period") is not None:
        args += ["--period", repr(float(i["period"]))]
    if i.get("points") is not None:
        args += ["--points", str(int(i["points"]))]
    if i.get("analysis"):
        args += ["--analysis", i["analysis"]]
    for v in i.get("vdd") or []:
        args += ["--vdd", v]
    if i.get("em_temp") is not None:
        args += ["--em-temp", repr(float(i["em_temp"]))]
    if i.get("delta_t") is not None and float(i["delta_t"]) != 5.0:
        args += ["--delta-t", repr(float(i["delta_t"]))]
    for x in i.get("abstracts") or []:
        if not os.path.isfile(x.rsplit("@", 1)[0]):
            return None
        args += ["--abstract", x]
    for x in i.get("place") or []:
        args += ["--place", x]
    if i.get("place_tol") is not None and float(i["place_tol"]) != PLACE_TOL_UM:
        args += ["--place-tol", repr(float(i["place_tol"]))]
    for x in i.get("phase") or []:
        args += ["--phase", x]
    return command_line(*args, list(extra), list(fmt))


def cmd_abstract(argv, out):
    """`emir abstract`: the block's abstract (emir.abstract/1 to -o), and its
    summary (emir.abstract_summary/1) in JSON mode."""
    from .errors import collecting
    from .output import write_json
    ap = abstract_parser()
    a = ap.parse_args(argv)
    if a.mode != "cc-grid":
        raise EmirError("EMIR-E521", "emir abstract --mode rcc: an abstract is built from "
                        "the instance-pin currents of a CC simulation superposed through "
                        "the block's R-only grid -- cc-grid only. Pass --mode cc-grid with "
                        "the block's CC simulation, or screen the block itself with `emir "
                        "run --mode rcc`", dspf=a.dspf)
    if not a.process:
        raise EmirError("EMIR-E103", "no process file: give --process (the PDK's EM data)",
                        dspf=a.dspf)
    if a.points < 16:
        raise EmirError("EMIR-E507", "--points must be at least 16", command="abstract")
    if a.period is not None and not (math.isfinite(a.period) and a.period > 0):
        raise EmirError("EMIR-E507", "--period must be a positive number of seconds",
                        command="abstract")
    nets = [n.strip() for n in a.net.split(",") if n.strip()] if a.net else None
    vdd = parse_vdd(a.vdd)
    pv = (split_at(a.port_voltages, "@", "--port-voltages", "abstract")
          if a.port_voltages else None)
    top = split_at(a.top, "@", "--top", "abstract") if a.top else None
    for p in (a.dspf, a.cc_sim, a.process) + ((pv[0],) if pv else ()) + \
            ((top[0],) if top else ()):
        if not os.path.exists(p):
            raise EmirError("EMIR-E101", f"{p} not found", path=p)
    import argparse
    subs_opts = Options(argparse.Namespace(abstract=a.abstract, place=a.place,
                                           place_tol=a.place_tol, align="sync",
                                           phase=a.phase, phase_sweep=None, budget_mv=None,
                                           port_voltages_out=None, dspf=a.dspf),
                        command="abstract")
    if not a.no_check:
        _input_check(a)
    from .engine import Process
    from .hier import block_abstract, load_abstract
    proc = Process.load(a.process, temp=a.em_temp, delta_t=a.delta_t,
                        warn_provenance=bool(a.no_check))
    subs = None
    if subs_opts.abstracts:
        subs = {"opts": subs_opts, "insts": {}}
        for inst, f in subs_opts.abstracts.items():
            ab = load_abstract(f)
            ab["_path"] = os.path.abspath(f)
            subs["insts"][inst] = ab
    inputs = {"dspf": os.path.abspath(a.dspf), "cc_sim": os.path.abspath(a.cc_sim),
              "process": os.path.abspath(a.process), "nets": nets, "period": a.period,
              "points": a.points, "analysis": a.analysis, "vdd": list(a.vdd or []),
              "em_temp": a.em_temp, "delta_t": a.delta_t,
              "port_voltages": ([os.path.abspath(pv[0]), pv[1]] if pv else None),
              "abstracts": [f"{os.path.abspath(f)}@{i}" for i, f in subs_opts.abstracts.items()],
              "place": list(a.place or []), "place_tol": a.place_tol,
              "phase": list(a.phase or [])}
    with collecting() as found:
        doc = block_abstract(a.dspf, a.cc_sim, proc, nets=nets, period=a.period,
                             analysis=a.analysis, points=a.points, v_nominal=vdd,
                             port_voltages=pv, inputs=inputs, subs=subs, log=print)
    doc["notices"] = list(found)
    doc["command"] = "emir abstract " + shlex.join(argv)
    write_json(a.out, doc)
    summary = summary_document(doc, os.path.abspath(a.out), argv, out, top=top,
                               process=a.process)
    _print(doc, a.out)
    out.emit(summary)
    if a.fail_on_violation and doc["verdict"] == "FAIL":
        print("[emir] exit 1: FAIL verdict (--fail-on-violation)", file=sys.stderr)
        return 1
    return 0


def _input_check(a):
    """The process file and the DSPF's layer map, before any work (the block
    needs no feed or net check: its ports are held at one potential)."""
    from .check import Report, check_layer_map, check_process
    from .dspf import parse, refusal
    rep = Report()
    rep.args = {"dspf": a.dspf, "process": a.process}
    proc = check_process(a.process, rep, em_temp=a.em_temp)
    if proc is not None:
        check_layer_map(parse(a.dspf), proc, rep)
    print("-- input check --")
    print(rep.table(show_ok=False))
    if not rep.passed and not a.force:
        raise refusal(rep)


def summary_document(doc: dict, path: str, argv, out, top=None, process=None) -> dict:
    """The `emir.abstract_summary/1` document: the abstract without its
    waveforms, where it was written, and what to do next: with `top` (`--top
    TOP.dspf@INST`), the plan of the chip-level run with this abstract."""
    from .errors import step
    from .output import command_line, document
    nets = []
    for name, nb in doc["nets"].items():
        e = {k: v for k, v in nb.items() if k != "ports"}
        e["ports"] = [{k: v for k, v in p.items() if k != "wave"} for p in nb["ports"]]
        nets.append(e)
    steps = []
    if doc["verdict"] == "FAIL":
        steps.append(step("after widening the failing wires or adding cuts in the block, "
                          "re-extract it and re-run this command to confirm",
                          "emir abstract " + shlex.join(argv)))
    if top:
        steps.append(step(
            f"back to the chip-level run: plan the top with this abstract at {top[1]} (add "
            f"the other blocks' --abstract FILE@INST and any --place the plan asks for)",
            command_line("next", "--dspf", os.path.abspath(top[0]), "--process",
                         os.path.abspath(process), "--abstract", f"{path}@{top[1]}",
                         out.fmt_args())))
    return document(
        "abstract_summary", disclaimer=doc["disclaimer"],
        research_disclaimer=RESEARCH_DISCLAIMER, verdict=doc["verdict"], out=path,
        use=f"--abstract {shlex.quote(path)}@<INSTANCE>", block=doc.get("block"),
        dspf=doc["dspf"], sim=doc["sim"], process=doc.get("process"), temp_C=doc["temp_C"],
        nets=nets, notices=doc.get("notices") or [], next_steps=steps)


def _ratio(v) -> str:
    return "not a number" if v is None or v != v else f"{v:.3f}x"


def _print(doc: dict, path: str):
    from . import SCREENING_NOTE
    for name, nb in doc["nets"].items():
        it = nb["internal"]
        w = it.get("worst")
        print(f"\n{doc.get('block') or 'block'} {name}: {len(nb['ports'])} pin(s), "
              + (f"period {nb['period_s']:.6g} s" if nb.get("period_s") else "aperiodic window")
              + f", {nb['n_taps']} instance pins")
        print(f"  internal       : {it['verdict']} ({it['boundary']})"
              + (f"; worst {_ratio(w['ratio'])} {w['metric']} {w['kind']} {w['name']} on "
                 f"{w['layer']}" if w else ""))
        print(f"  internal IR    : {it['ir_drop_mV']:.4f} mV at {it['ir_worst_node']}"
              + (f"; dynamic {it['dynamic']['worst_mV']:.4f} mV" if it.get("dynamic") else ""))
        for p in nb["ports"]:
            print(f"  pin {p['name']:<10s} ({p['x_um']:g}, {p['y_um']:g}): avg "
                  f"{p['i_avg'] * 1e3:+.4f} rms {p['i_rms'] * 1e3:.4f} peak "
                  f"{p['i_peak'] * 1e3:.4f} mA into the top net")
        if nb.get("y_ports"):
            print(f"  port conductance: {len(nb['ports'])}x{len(nb['ports'])} (stamped "
                  f"between the pins at the top level)")
        ba = nb.get("back_annotation")
        if ba:
            print(f"  back-annotated : {ba['verdict']} with the top's pin voltages of "
                  f"{ba['instance']} ({'exact' if ba['exact'] else 'rms/peak bounds'}); worst "
                  f"{_ratio(ba['max_ratio'].get('all'))}, droop {ba['ir_drop_mV']:.4f} mV "
                  f"from the top reference")
    print(f"\nwrote {path}: use it as --abstract {path}@<INSTANCE>")
    print(RESEARCH_LINE)
    print(f"abstract verdict: {doc['verdict']} -- {len(doc['nets'])} net(s). {SCREENING_NOTE}")


# ===================================================================== emir report


def report_lines(h: dict) -> list:
    """The `emir report` section of a hierarchical run."""
    L = ["", "## Hierarchy (blocks black-boxed)", ""]
    L.append(f"- Alignment: {h['align']}"
             + {"inphase": " (rms and peak: upper bounds over every alignment)",
                "sync": " (one clock reference: exact for it"
                        + (f", worst over the swept phases of "
                           f"{', '.join(h['phase_sweep'])}" if h.get("phase_sweep") else "")
                        + ")",
                "async": " (unrelated clocks: NOT an upper bound for related clocks)"}
             .get(h["align"], ""))
    for b in h.get("blocks") or []:
        L.append(f"- {b['instance']} ({b.get('block') or 'block'}) {b['block_net']}: block "
                 f"verdict {b['verdict']}, {b['n_pins']} pin(s), internal IR "
                 f"{b['internal_ir_mV']:.4f} mV"
                 + (" -- back-annotated with the top's pin voltages" if b["back_annotated"]
                    else " -- pins held at one potential; NEEDS back-annotation (several "
                         "pins)" if b["needs_back_annotation"] else ""))
    for n in h.get("nets") or []:
        L += ["", f"Pins on {n['net']}:", "", "| block | pin | node | V avg (mV) | "
              "V peak (mV) | I avg (mA) |", "|---|---|---|---|---|---|"]
        for p in n["pins"]:
            L.append(f"| {p['instance']} | {p['port']} | {p['node']} | {p['V_avg_mV']:+.4f} | "
                     f"{p['V_peak_mV']:.4f} | {p['i_avg_A'] * 1e3:+.4f} |")
        for t in n.get("pass_through") or []:
            L.append(f"\nPass-through inside {t['instance']} ({t['block_net']}, "
                     f"{t['between'][0]} to {t['between'][1]}): {t['I_avg_A'] * 1e3:+.5f} mA "
                     f"avg through {t['R_ohm']:.4g} ohm of block grid.")
    b = h.get("budget")
    if b:
        L += ["", f"IR budget ({b['budget_mV']:g} mV per block): **{b['verdict']}**", "",
              "| block | net | top at worst pin (mV) | internal (mV) | total (mV) | verdict |",
              "|---|---|---|---|---|---|"]
        for r in b["rows"]:
            L.append(f"| {r['instance']} | {r['block_net']} | {r['top_mV']:.4f} | "
                     f"{r['internal_mV']:.4f} | {r['total_mV']:.4f} | {r['verdict']}"
                     f"{'' if r['exact'] else ' (bound)'} |")
    for x in h.get("notes") or []:
        L += ["", x]
    return L


# ------------------------------------------------------------------ `emir report` on an abstract


def maybe_report(a, sink):
    """`emir report --from` on an `emir.abstract/1` document (a block's
    abstract, back-annotated or not): returns the exit status, or None when
    the source is not one (the run-summary path goes on; stdin is put back)."""
    import io
    import json
    src = a.src
    if src == "-":
        from .report import _isatty
        if _isatty(sys.stdin):
            return None
        text = sys.stdin.read()
        sys.stdin = io.StringIO(text)
    else:
        try:
            with open(src, encoding="utf-8") as f:
                text = f.read()
        except (OSError, UnicodeError):
            return None
    try:
        doc = json.loads(text)
    except ValueError:
        return None
    if not (isinstance(doc, dict) and doc.get("schema") == "emir.abstract/1"):
        return None
    from .output import document
    r = abstract_report(doc, src, max(a.top, 0))
    md = abstract_markdown(doc, r)
    if a.format != "json":
        print(md, end="")
    sink.emit(document("report", **r, markdown=md))
    return 0


def _governing(nb: dict) -> dict:
    """The part of a block net that decides: its back-annotation when there is
    one (the block with the top's pin voltages), else the block alone."""
    return nb.get("back_annotation") or nb.get("internal") or {}


def abstract_report(doc: dict, source: str, top: int = 10) -> dict:
    """The `emir.report/1` document of a block abstract (without `markdown`)."""
    from . import SCREENING_NOTE
    nets, items, ir, notes = [], [], [], []
    for name, nb in sorted((doc.get("nets") or {}).items()):
        g = _governing(nb)
        w = g.get("worst") or {}
        ba = nb.get("back_annotation")
        nets.append({"net": name, "verdict": nb.get("verdict") or g.get("verdict") or "CLEAN",
                     "mode": "abstract, back-annotated" if ba else "abstract, block alone",
                     "governing_metric": w.get("metric"),
                     "worst_ratio": (g.get("max_ratio") or {}).get("all"),
                     "ir_drop_mV": g.get("ir_drop_mV"),
                     "ir_kind": "from the pad (top + block)" if ba else "from the block's pins",
                     "power": True})
        items += list(g.get("worst_items") or [])
        ir.append({"net": name, "ir_drop_mV": g.get("ir_drop_mV"),
                   "ir_kind": nets[-1]["ir_kind"]})
        internal = nb.get("internal") or {}
        for key, what in (("unmapped", "DSPF layers with no process-file entry, not checked"),
                          ("no_width", "wires with no width, not checked"),
                          ("vias_assumed_one_cut", "vias with no cut count, checked as ONE "
                                                   "cut (conservative)")):
            if internal.get(key):
                notes.append(f"{name}: {what}: {', '.join(internal[key][:6])}")
        if len(nb.get("ports") or []) > 1 and not ba:
            notes.append(f"{name}: fed at {len(nb['ports'])} pins and NOT back-annotated: "
                         f"the block-alone verdict holds its pins at one potential, so the "
                         f"current passing through the block between its pins is not in it "
                         f"(run the top with --port-voltages-out, then emir abstract "
                         f"--port-voltages)")
    items.sort(key=lambda it: -(it["ratio"] if it.get("ratio") is not None else float("inf")))
    warns = [f"[{n['code']}] {n['message']}" for n in doc.get("notices") or []
             if n.get("level") == "warning"]
    order = {"FAIL": 0, "WARN": 1, "CLEAN": 2}
    verdict = doc.get("verdict") or min((n["verdict"] for n in nets),
                                        key=lambda v: order.get(v, 3), default="CLEAN")
    return {"source": source, "verdict": verdict, "disclaimer": SCREENING_NOTE,
            "research_disclaimer": RESEARCH_DISCLAIMER,
            "design": doc.get("block"), "command": doc.get("command"),
            "created": doc.get("created"), "modes": sorted({n["mode"] for n in nets}),
            "current_source": {"kind": "simulation", "path": doc.get("sim"),
                               "description": "the block's own simulation (its device "
                                              "currents), characterised once"},
            "temp_C": doc.get("temp_C"), "em_tref_C": doc.get("em_tref_C"),
            "nets": nets, "worst_items": items[:top], "ir_by_power_net": ir,
            "not_checked": notes, "input_warnings": [], "run_warnings": warns,
            "process": doc.get("process"), "dspf": doc.get("dspf")}


def abstract_markdown(doc: dict, r: dict) -> str:
    """A block abstract's report for a person: per net the block-alone and the
    back-annotated verdicts, the IR from the pad against the budget."""
    from . import SCREENING_NOTE
    from .report import _x
    L = [f"# EMIR block abstract report: {r.get('design') or 'block'}", "",
         f"**Verdict: {r['verdict']}** -- {len(r['nets'])} block net(s).", "",
         f"**{SCREENING_NOTE}** {RESEARCH_DISCLAIMER}", ""]
    if r.get("command") or r.get("created"):
        L.append("- Abstract: " + (f"`{r['command']}`" if r.get("command") else "(command not "
                                                                            "recorded)")
                 + (f", written {r['created']}" if r.get("created") else ""))
    L.append(f"- Currents: the block's own simulation (`{doc.get('sim')}`)")
    if r.get("temp_C") is not None:
        L.append(f"- Analysis temperature: {r['temp_C']:g} C"
                 + (f" (the process file's EM limits are quoted at {r['em_tref_C']:g} C)"
                    if r.get("em_tref_C") is not None else ""))
    L += ["", "## Block nets", "",
          "| net | verdict | pins | block alone (pins held at one potential) | "
          "with the top's pin voltages | IR | budget |",
          "|---|---|---|---|---|---|---|"]
    for name, nb in sorted((doc.get("nets") or {}).items()):
        it, ba = nb.get("internal") or {}, nb.get("back_annotation")
        alone = f"{it.get('verdict', '-')} {_x((it.get('max_ratio') or {}).get('all'))}"
        if ba:
            annotated = f"{ba['verdict']} {_x((ba.get('max_ratio') or {}).get('all'))}"
            irtxt = f"{_x(ba.get('ir_drop_mV'), ' mV')} from the pad (the top's drop included)"
            bud = (f"{ba['budget_verdict']} against {ba['budget_mV']:g} mV"
                   if ba.get("budget_verdict") else "no budget given to the top run")
        else:
            annotated = ("not back-annotated (needed: fed at several pins)"
                         if len(nb.get("ports") or []) > 1 else "not needed (one pin)")
            irtxt = f"{_x(it.get('ir_drop_mV'), ' mV')} from the block's pins"
            bud = "the top run's budget table"
        L.append(f"| {name} | {nb.get('verdict')} | {len(nb.get('ports') or [])} | {alone} | "
                 f"{annotated} | {irtxt} | {bud} |")
    L += ["", "The back-annotated IR is measured from the pad: it already holds the top-level "
              "drop to the pins. Compare it with the budget as it is; never add the top drop "
              "again.", "", "## Worst items", ""]
    if r["worst_items"]:
        from .report import at_text
        L += ["| net | segment | at (x, y) um | layer | kind | metric | ratio | fix |",
              "|---|---|---|---|---|---|---|---|"]
        for x in r["worst_items"]:
            fix = x.get("fix") or "none needed (below the limit, inside the warn margin)"
            L.append(f"| {x['net']} | {x['name']} | {at_text(x)} | {x['layer']} | "
                     f"{x['kind']} | {x['metric']} | {_x(x.get('ratio'))} | {fix} |")
    else:
        L.append("No segment at or above the warn fraction.")
    L += ["", "## Not checked", ""]
    L += [f"- {x}" for x in r["not_checked"]] or ["Every item the abstract found was checked "
                                                  "against a limit."]
    L += ["", "## Warnings", ""]
    L += [f"- {x}" for x in r["run_warnings"]] or ["None."]
    L += ["", f"{SCREENING_NOTE} Foundry-qualified EM/IR remains the sign-off flow; "
              f"these numbers screen the layout and say where to look. "
              f"{RESEARCH_DISCLAIMER}"]
    return "\n".join(L) + "\n"
