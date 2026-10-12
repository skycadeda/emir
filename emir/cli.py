"""The `emir` command.

    emir check  <dspf> [run options]   check the inputs, change nothing
    emir run    <dspf> [run options]   check, analyse, report, viewer data;
                                       ends with an `overall verdict:` line
                [--summary-out FILE]   the run summary (emir.run/1 JSON)
                [--fail-on-violation]  exit 1 when any net's verdict is FAIL
    emir report --from FILE            the summary to relay to a person
                                       (--format md|json; FILE `-` = stdin)
    emir next   [--dspf F --process P --cc-sim R ...]
                                       what to run next, and why
    emir nets   <dspf>                 list the nets and their sizes
    emir pins   <dspf> --net N         list the instance pins on a net
    emir save-lines <dspf> [--net N] [--prefix XI0.]
                [--sim spectre|hspice|xyce|ngspice] [--names-out F] [--out FILE]
                                       the lines that save every branch current
    emir simulate <testbench> --sim spectre|hspice|xyce|ngspice --dspf D
                [--out R] [--run]      run a simulator for --cc-sim (Spectre and
                                       HSPICE: the plan only, unless --run)
    emir verify-sim --sim spectre|hspice|xyce|ngspice [--run] [--out D]
                                       prove on this machine that the simulator's
                                       route into emir gives the right numbers
    emir extract --gds G --cdl C --cell X --kind K [--run]
                                       GDS -> DSPF with PVS LVS + Quantus
                                       (the plan only, unless --run)
    emir extract --tool magic --gds G --cell X --process P [--out D]
                                       GDS -> DSPF with magic (open source; a
                                       magic that writes resistor geometry)
    emir import-ict --ict F --emir Y   fill a process file from an ICT
    emir process new --from-dspf D [-o F] [--interactive]
                                       a fill-in process file for the layers a
                                       DSPF uses, to type the PDK's EM data by
                                       hand (no readable ICT)
    emir process check F [--dspf D]    what a process file still lacks
    emir info                          where the tools were found
    emir selftest                      offline proof that the install works
    emir validate [--out D] [--quick]  the analytic validation suite (6 cases)
    emir gui-install [--dir D]         write the Virtuoso SKILL files
    emir explain <code> | --list       what an error code means, how to fix it
    emir schema <name> | --list        the JSON Schema of a document
    emir mcp [--list]                  serve these commands as MCP tools (stdio;
                                       the [mcp] extra)

The currents come from DC values (--currents: avg = rms = peak = |I|) or from
a simulation of the RC-extracted netlist with every branch current saved
(--cc-sim, rcc: avg, rms and peak EM, static and dynamic IR, exactly).

Every command takes --format text|json. With json, stdout carries exactly one
JSON document (its `schema` field names it; `emir schema` prints the schema)
and everything else goes to stderr. Failures carry a code (`emir explain`).

Exit status: 0 done; 1 with --fail-on-violation, a FAIL verdict; 2 bad input
or refused (`check`: inputs not OK); 3 a required tool or package is missing;
70 internal error or a failed self-test / validation; 130 interrupted
(SIGINT); 143 terminated (SIGTERM).
"""
from __future__ import annotations

import contextlib
import os
import shlex
import signal
import sys
import threading

from . import __version__
from .cadence import cmd_extract
from .errors import EmirError, collecting, notice
from .mcp_server import cmd_mcp
from .opensim import cmd_simulate
from .process_file import cmd_process
from .verify_sim import cmd_verify_sim
from .output import (Out, add_dspf, command_line, document, error_document, error_text,
                     last_help, parser, pipe_safe, prescan_format, reset_help,
                     strip_options, write_json)


class _Terminated(BaseException):
    """SIGTERM, turned into an exception so the command ends with its coded
    document (EMIR-E906, exit 143) instead of dying silently. A
    BaseException, like KeyboardInterrupt, so no `except Exception` in a
    command can swallow it."""


def _abs(p):
    return os.path.abspath(p) if p else None


# ------------------------------------------------------------------ check


#: options of `check` that `run` does not take, dropped when a check's
#: arguments are turned into the run command it suggests
_CHECK_ONLY = {"--json-out": 1, "--all": 0}


def _cmd_check(argv, out):
    from .check import from_args
    from .dspf import build_parser
    ap = build_parser(prog="emir check")
    ap.add_argument("--json-out", default=None, metavar="FILE",
                    help="write the findings (the emir.check/1 document) here")
    ap.add_argument("--all", action="store_true",
                    help="show the OK lines too")
    # `emir check --process P` alone checks a process file with no DSPF: a
    # new PDK's file before any extraction exists (docs/process-files.md)
    act = ap.dspf_positional
    act.nargs, act.default, act.required = "?", None, False
    act.help = ("extracted DSPF (R or RC); leave it out to check the --process file on "
                "its own; `--dspf FILE` is accepted as the same")
    a = ap.parse_args(argv)
    if a.dspf is None:
        return _check_process_only(a, argv, out)
    if a.summary_out and not a.json_out:
        # `--json` / `--summary-out` were accepted here and once silently
        # ignored; for `check` the machine-readable output is the findings
        a.json_out = a.summary_out
    run_args = strip_options(argv, {**_CHECK_ONLY, "--summary-out": 1, "--json": 1,
                                    "--format": 1})
    a.argv, a.argv_text = argv, shlex.join(strip_options(argv, _CHECK_ONLY))
    want = bool(a.process or a.currents or a.cc_sim or a.netlist)
    rep = from_args(a, want_analysis=want)
    if not want and not a.net and rep.passed:
        # bare `emir check <dspf>`: describe the file, list the nets
        from .dspf import parse
        d = parse(a.dspf)
        for n in sorted(d.nets):
            print(f"  net {n:16s} {len(d.net_resistors(n)):5d} R  "
                  f"{len(d.net_taps(n)):4d} inst pins")
    print(rep.table(show_ok=a.all or not want))
    run_cmd = (command_line("run", run_args, "--summary-out", "emir_out/summary.json",
                            out.fmt_args()) if want else None)
    from .output import keep_format
    doc = document("check", **rep.as_dict(),
                   next_steps=keep_format(rep.next_steps(run_cmd), out.fmt_args()))
    if a.json_out:
        write_json(a.json_out, doc)
    out.emit(doc)
    return 0 if rep.passed else 2


def _check_process_only(a, argv, out) -> int:
    """`emir check --process P`: the process file's own checks (schema, units,
    limits that cannot bind, layers with no limit, the temperature table)."""
    from .check import Report, check_process
    if not a.process:
        raise EmirError("EMIR-E103", "give a DSPF to check, or --process FILE to check a "
                        "process file on its own", command="check")
    if not os.path.isfile(a.process):
        raise EmirError("EMIR-E101", f"process file not found: {a.process}", path=a.process)
    rep = Report()
    check_process(a.process, rep, em_temp=a.em_temp)
    print(rep.table(show_ok=True))
    from .output import keep_format
    doc = document("check", **rep.as_dict(), next_steps=keep_format([], out.fmt_args()))
    if a.json_out:
        write_json(a.json_out, doc)
    out.emit(doc)
    return 0 if rep.passed else 2


# ------------------------------------------------------------------ run


def _cmd_run(argv, out):
    from .dspf import analyse, build_parser
    from .summary import summary_document
    ap = build_parser(prog="emir run")
    ap.add_argument("--fail-on-violation", action="store_true",
                    help="exit 1 when any net's verdict is FAIL (for CI and agents); "
                         "without it a completed run exits 0")
    a = ap.parse_args(argv)
    # the report's provenance line, and the commands the result suggests:
    # the command as typed (`{args}` in a code's command template is reused by
    # `emir check`, which has no --fail-on-violation)
    a.argv = argv
    a.argv_text = shlex.join(strip_options(argv, {"--fail-on-violation": 0}))
    a.command = "emir run " + shlex.join(argv)
    try:
        res = analyse(a)
    except BaseException as e:
        # a refused or failed run must not leave the previous run's summary
        # at --summary-out, where `emir report --from` would relay it as this
        # one: the file gets this run's error document instead
        if a.summary_out:
            _summary_failed(a.summary_out, e, argv)
        _html_refused(a.html, e, viewer=a.viewer)
        raise
    if res.planned is not None:
        # a licensed --netlist without --run: what would run, nothing ran (the
        # `emir simulate` plan document; --summary-out gets it too, so no
        # earlier run's summary stays there to be relayed)
        doc = document("simulate", **res.planned)
        if a.summary_out:
            write_json(a.summary_out, doc)
        out.emit(doc)
        return 0
    if res.stub is not None:
        mode, text = res.stub
        lines = text.splitlines()
        out.emit(document("save_lines", dspf=_abs(a.dspf),
                          net=None if a.net in (None, "all") else a.net, mode=mode,
                          prefix=a.save_prefix, simulator="spectre", lines=lines,
                          count=_count_saved(lines),
                          sidecars=[]))
        return 0
    out.emit(summary_document(res))
    if a.fail_on_violation and res.overall == "FAIL":
        print("[emir] exit 1: FAIL verdict (--fail-on-violation)", file=sys.stderr)
        return 1
    return 0


def _summary_failed(path, exc, argv, command: str = "run"):
    """Replace the --summary-out file by the `emir.error/1` document of the
    `command` (`run`) that failed (best effort: the run's own error
    is what matters). Its next steps keep the run's --format."""
    from .output import error_document
    err = _coded_any(exc)
    fmt = ["--format", "json"] if prescan_format(argv) == "json" else []
    try:
        write_json(path, error_document(err, command=command, fmt=fmt,
                                        ctx={"args": shlex.join(argv), "command": command}))
    except (EmirError, OSError):
        pass


def _html_refused(html, exc, viewer=None):
    """A run refused for its window (`waveform.REFUSALS`): an earlier run's
    numbers in --html and --viewer are moved to their `stale/` (`set_aside`),
    and the --html index says the run was refused, with no number (best
    effort, like `_summary_failed`)."""
    from .waveform import REFUSALS
    if not isinstance(exc, EmirError) or exc.code not in REFUSALS or not (html or viewer):
        return
    from .netreport import refused_index, set_aside
    try:
        moved = set_aside(html, viewer)
        if html:
            refused_index(html, exc, moved=moved)
    except Exception:                           # noqa: BLE001 -- best effort: the
        pass                                    # refusal itself is what is raised


def _count_saved(lines) -> int:
    return sum(len(ln.split()) - 1 for ln in lines if ln.startswith("save "))


# ------------------------------------------------------------------ report / next


def _cmd_report(argv, out):
    from .report import main as m
    return m(argv, out)


def _cmd_next(argv, out):
    from .plan import main as m
    return m(argv, out)


# ------------------------------------------------------------------ DSPF queries


def _cmd_nets(argv, out):
    from .dspf import parse
    ap = parser("emir nets", description="List the nets of a DSPF and their sizes.")
    add_dspf(ap, "the DSPF")
    a = ap.parse_args(argv)
    d = parse(a.dspf)
    print(d.summary())
    nets = []
    for n in sorted(d.nets):
        res = d.net_resistors(n)
        port = bool(d.net_ports(n))
        nodes = len({x for r in res for x in (r["n1"], r["n2"])})
        nets.append({"name": n, "resistors": len(res), "nodes": nodes,
                     "inst_pins": len(d.net_taps(n)), "port": port})
        print(f"  net {n:16s} {len(res):5d} R  "
              f"{len(d.net_taps(n)):4d} inst pins  {'port' if port else ''}")
    out.emit(document("nets", dspf=_abs(a.dspf), design=d.design, summary=d.summary(),
                      n_nodes=len(d.node_xy), n_resistors=len(d.res), nets=nets))
    return 0


def _cmd_pins(argv, out):
    from .dspf import did_you_mean, parse
    ap = parser("emir pins", description="List the instance pins on one net.")
    add_dspf(ap, "the DSPF")
    ap.add_argument("--net", required=True)
    a = ap.parse_args(argv)
    d = parse(a.dspf)
    if not d.has_net(a.net):
        raise EmirError("EMIR-E204", f"net {a.net!r} is not in {a.dspf}"
                        f"{did_you_mean(a.net, d.nets)}; `emir nets` lists them",
                        dspf=a.dspf)
    taps = d.net_taps(a.net)
    if not taps:
        notice("EMIR-W205", f"net {a.net!r} has no instance pins", log=_stderr)
    for t in taps:
        print(f"  tap {t['name']:24s} node {t['node']:20s} "
              f"({t['xy'][0]:.4f}, {t['xy'][1]:.4f})")
    out.emit(document("pins", dspf=_abs(a.dspf), net=a.net,
                      pins=[{"name": t["name"], "inst": t["inst"], "pin": t["pin"],
                             "node": t["node"], "x": t["xy"][0], "y": t["xy"][1],
                             "layer": t.get("layer")} for t in taps],
                      split=sorted(d.split_pins(a.net))))
    return 0


def _cmd_save_lines(argv, out):
    from .dspf import parse
    ap = parser("emir save-lines",
                description="Print the lines that save every branch current an rcc run "
                            "reads: Spectre `save`, HSPICE `.probe` (with the ASCII output "
                            "option), Xyce `.print`, ngspice `.save` (--sim).")
    add_dspf(ap, "the DSPF")
    ap.add_argument("--net", default=None, help="one net (default: all)")
    ap.add_argument("--mode", default=None, choices=("rcc",),
                    help="rcc, the one method (it may be left out): every branch current, "
                         "for a simulation of the RC netlist")
    ap.add_argument("--prefix", default="",
                    help='instance path in the testbench, e.g. "XI0."')
    ap.add_argument("--out", default=None, metavar="FILE",
                    help="also write the lines to FILE (to include in the testbench)")
    ap.add_argument("--sim", default="spectre",
                    choices=("spectre", "hspice", "xyce", "ngspice"),
                    help="the simulator whose syntax to print (default spectre)")
    ap.add_argument("--names-out", default=None, metavar="FILE",
                    help="hspice/xyce/ngspice: write the names sidecar here "
                         "(<result>.names), mapping the simulator's current names to the "
                         "DSPF's")
    ap.add_argument("--raw", default=None, metavar="FILE",
                    help="hspice/xyce: the result file the lines write (HSPICE: the .tr0 "
                         "of `hspice -o <stem>`; default: --names-out without .names)")
    a = ap.parse_args(argv)
    d = parse(a.dspf)
    if a.net and not d.has_net(a.net):
        from .dspf import did_you_mean
        raise EmirError("EMIR-E204", f"net {a.net!r} is not in {a.dspf}"
                        f"{did_you_mean(a.net, d.nets)}", dspf=a.dspf)
    if a.sim != "spectre":
        from .opensim import save_lines_command
        return save_lines_command(a, d, out)
    if a.names_out or a.raw:
        raise EmirError("EMIR-E513", "--names-out and --raw are for --sim hspice, xyce or "
                        "ngspice: Spectre saves currents under the DSPF's own names",
                        command="save-lines")
    from .rcc import save_branches
    text = save_branches(d, a.net, prefix=a.prefix)
    print(text)
    lines = text.splitlines()
    if a.out:
        from .output import write_text
        write_text(a.out, text + "\n")
        print(f"[emir] {_count_saved(lines)} saved signals -> {a.out}", file=sys.stderr)
    out.emit(document("save_lines", dspf=_abs(a.dspf), net=a.net, mode="rcc",
                      prefix=a.prefix, simulator="spectre", lines=lines,
                      count=_count_saved(lines), sidecars=[], out=_abs(a.out)))
    return 0


# ------------------------------------------------------------------ setup


def _cmd_import_ict(argv, out):
    from .pdk_import import main as m
    return m(argv, out)


#: Python packages `emir info` reports: (module, distribution, required, purpose)
PACKAGES = (("numpy", "numpy", True, "arrays (required)"),
            ("scipy", "scipy", True, "sparse solves (required)"),
            ("yaml", "PyYAML", True, "process files (required)"),
            ("matplotlib", "matplotlib", False,
             "the PNG charts of --html; the HTML reports do not need it"),
            ("klayout", "klayout", False, "KLayout marker database; the layout under a chart"),
            ("mcp", "mcp", False, "`emir mcp`, the MCP server"),
            ("jsonschema", "jsonschema", False, "validating JSON documents (dev)"))


def _package_version(mod: str, dist: str) -> str | None:
    """The installed version, or None when the module does not import."""
    from importlib import metadata
    try:
        m = __import__(mod)
    except ImportError:
        return None
    try:
        return metadata.version(dist)
    except metadata.PackageNotFoundError:
        return str(getattr(m, "__version__", "ok"))


def _cmd_info(argv, out):
    from . import site
    ap = parser("emir info", description="Where the tools were found.")
    ap.parse_args(argv)
    print(f"emir {__version__}  python {sys.version.split()[0]}  "
          f"{os.path.dirname(os.path.abspath(__file__))}")
    rows = site.describe()
    tools, warnings = [], []
    # the magic the SKY130 route runs (`emir extract --tool magic`): often not
    # the stock `magic` on $PATH, which may be a build without resistor geometry
    from .magic import route_magic
    mr = route_magic()
    from .verify_sim import FORMAT, records_path, verified_route
    for k in site.SIMULATORS:
        p, src = site.tool(k)
        ver = site.tool_version(k, p)
        entry = {"name": k, "path": p, "found": p is not None, "source": src,
                 "licensed": k in site.LICENSED, "version": ver}
        if k in FORMAT:
            # the newest record on this machine that verifies a version of it
            # (the latest record of that version passed, with this emir)
            rec = verified_route(k, FORMAT[k])
            entry["verified"] = ({x: rec.get(x) for x in ("version", "format", "date")}
                                 if rec else None)
        if k == "magic":
            entry["route_path"] = mr["path"]
            entry["writes_geometry"] = bool(mr["writes_geometry"] and p is not None and
                                            os.path.realpath(p) == os.path.realpath(
                                                mr["path"]))
        tools.append(entry)
        warn = site.magic_warning(p, ver) if k == "magic" else None
        if warn:
            warnings.append(warn)
    versions = {t["name"]: t["version"] for t in tools}
    unset = {"process": "the default --process", "lib": "a Cadence site key: the OA "
             "library the Virtuoso viewer opens", "mark_layer": "a Cadence site key: the "
             "layer the Explorer paints on", "work_dir": "the default output root (emir_out)"}
    verified = {t["name"]: t.get("verified") for t in tools}
    for k, v, src in rows:
        if not v and k in unset:
            v, src = f"(not set: {unset[k]})", ""
        route = ""
        if k == "magic" and mr["path"] and not any(
                t["name"] == "magic" and t.get("writes_geometry") for t in tools):
            route = f"  (the SKY130 route runs magic-geo: {mr['path']})"
        print(f"  {k:10s} {v:50s} {src}"
              + (f"  (version {versions[k]})" if versions.get(k) else "")
              + (f"  [verified here {verified[k]['date']}]" if verified.get(k) else "")
              + route)
    print(f"  {'verified':10s} {records_path():50s} emir verify-sim records "
          f"($EMIR_STATE_DIR)")
    from .selftest import sample_dir
    print(f"  {'sample':10s} {sample_dir():50s} the bundled sample (rail.dspf, "
          f"process.yaml): the quickstart's files")
    print(f"  {'magic-geo':10s} "
          + (f"{mr['path']:50s} {mr['source']} (version {mr['version']}): writes resistor "
             f"geometry" if mr["writes_geometry"] else
             f"{'NOT FOUND':50s} no magic that writes resistor geometry: `emir extract "
             f"--tool magic` stops with EMIR-E643, which says how to build one"))
    pdk_root = os.environ.get("PDK_ROOT") or None
    print(f"  {'PDK_ROOT':10s} {pdk_root or '(not set)':50s} $PDK_ROOT: the open PDKs "
          f"(sky130A), for magic's technology")
    with collecting() as notes:
        for w in warnings:
            notice("EMIR-W640", w, log=print)
    pkgs = []
    for mod, dist, required, purpose in PACKAGES:
        ver = _package_version(mod, dist)
        pkgs.append({"name": dist, "version": ver, "required": required,
                     "purpose": purpose})
        if mod == "jsonschema":
            continue
        print(f"  {mod:10s} {ver or 'not installed'}"
              + (f"  (optional: {purpose})" if ver is None and not required else ""))
    settings = [{"key": k, "value": v or None, "source": src}
                for k, v, src in rows if k not in site.SIMULATORS and k != "site file"]
    settings.append({"key": "PDK_ROOT", "value": pdk_root, "source": "$PDK_ROOT"})
    out.emit(document("info", python=sys.version.split()[0],
                      package_dir=os.path.dirname(os.path.abspath(__file__)),
                      sample_dir=sample_dir(), records_file=records_path(),
                      tools=tools, magic_route=mr, settings=settings,
                      site_file=site.site_file(),
                      packages=pkgs, commands=sorted(COMMANDS),
                      exit_codes={str(k): v for k, v in _exit_codes().items()},
                      notices=list(notes)))
    return 0


def _exit_codes():
    from .errors import EXIT_CODES
    return EXIT_CODES


def _cmd_selftest(argv, out):
    from .selftest import main as m
    return m(argv, out)


def _cmd_validate(argv, out):
    from .validate import main as m
    return m(argv, out)


def _cmd_gui_install(argv, out):
    from .skillgui import install
    ap = parser("emir gui-install", description="Write the Virtuoso SKILL files.")
    ap.add_argument("--dir", default=None,
                    help="where to write the .il files (default: <clone or "
                         "bundle>/skill, else ~/.emir/skill)")
    a = ap.parse_args(argv)
    files = install(a.dir)
    for p in files.values():
        print(p)
    out.emit(document("gui_install", dir=os.path.dirname(files["main"]), files=files,
                      cdsinit=f'load("{files["site"]}")',
                      drf_note=f"append {files['drf']} to ~/display.drf (create it if "
                               f"absent; never replace a project display.drf): the "
                               f"Explorer colours"))
    return 0


# ------------------------------------------------------------------ the contract


def _cmd_explain(argv, out):
    from .errors import REGISTRY, RETIRED, explain, normalise
    ap = parser("emir explain",
                description="What an error code means, and the command that fixes "
                            "or diagnoses it.")
    ap.add_argument("codes", nargs="*", metavar="CODE",
                    help="EMIR-E204, E204, e204 ...")
    ap.add_argument("--list", action="store_true", help="every code")
    a = ap.parse_args(argv)
    if a.list:
        recs = [c.as_dict() for c in REGISTRY.values()] + [explain(c) for c in RETIRED]
    elif a.codes:
        recs = []
        for c in a.codes:
            n = normalise(c)
            if n not in REGISTRY and n not in RETIRED:
                raise EmirError("EMIR-E511", f"{c!r} is not an EMIR code; "
                                f"`emir explain --list` lists them")
            recs.append(explain(n))
    else:
        raise EmirError("EMIR-E507", "give a code, or --list", command="explain")
    for r in recs:
        if a.list:
            print(f"{r['code']}  {r['level']:7s}  {r['title']}"
                  + ("  (retired)" if r["retired"] else ""))
            continue
        if r["retired"]:
            print(f"{r['code']}  RETIRED: {r['retired_reason']}")
            continue
        ex = f", exit {r['exit_code']}" if r["exit_code"] is not None else ""
        print(f"{r['code']}  {r['title']}\n"
              f"  level    {r['level']}{ex}; area: {r['area']}\n"
              f"  meaning  {r['meaning']}\n"
              f"  hint     {r['hint']}"
              + (f"\n  command  {_template(r['command'])}" if r["command"] else "")
              + (f"\n  otherwise {_template(r['fallback'])}" if r.get("fallback") else ""))
    out.emit(document("explain", codes=recs))
    return 0


def _template(cmd):
    from .errors import fill
    return fill(cmd)


def _cmd_schema(argv, out):
    from . import schemas
    ap = parser("emir schema",
                description="Print the JSON Schema (2020-12) of a document emir "
                            "reads or writes.")
    ap.add_argument("name", nargs="?", help="e.g. run, check, process, error")
    ap.add_argument("--list", action="store_true", help="every schema")
    a = ap.parse_args(argv)
    if a.list or not a.name:
        if not a.list:
            raise EmirError("EMIR-E507", "give a schema name, or --list", command="schema")
        rows = schemas.listing()
        for r in rows:
            print(f"  {r['name']:12s} {r['id']:22s} {r['title']}")
        out.emit(document("schema_list", schemas=rows))
        return 0
    k = schemas.key(a.name)
    out.emit_raw(schemas.get(k), schemas.text(k))
    return 0


COMMANDS = {
    "check": _cmd_check, "run": _cmd_run, "report": _cmd_report, "next": _cmd_next,
    "nets": _cmd_nets, "pins": _cmd_pins, "save-lines": _cmd_save_lines,
    "import-ict": _cmd_import_ict, "info": _cmd_info, "selftest": _cmd_selftest,
    "validate": _cmd_validate, "gui-install": _cmd_gui_install,
    "explain": _cmd_explain, "schema": _cmd_schema,
    "extract": cmd_extract,
    "simulate": cmd_simulate, "verify-sim": cmd_verify_sim,
    "mcp": cmd_mcp,
    "process": cmd_process,
}

#: commands made of sub-commands (`emir process new`): each sub-command has
#: its own parser, MCP tool (`emir_process_new`) and contract section
SUBCOMMANDS = {"process": ("new", "check")}


# ------------------------------------------------------------------ main


def _stderr(line):
    print(line, file=sys.stderr)


def main(argv=None) -> int:
    """Run one command; returns its exit status (see the module doc)."""
    with _pipe_safe_streams():
        return _main(argv)


@contextlib.contextmanager
def _pipe_safe_streams():
    """While a command runs, stdout and stderr are `PipeSafe`: a reader that
    leaves early (`emir run ... 2>&1 | head`) costs only the lines it would
    have read. The command still finishes, writes its files (--summary-out,
    --out) and exits with its own status. Afterwards the streams are the
    caller's again (unless the command replaced them: `emir mcp`)."""
    saved = sys.stdout, sys.stderr
    wrapped = pipe_safe(saved[0]), pipe_safe(saved[1])
    sys.stdout, sys.stderr = wrapped
    try:
        yield
    finally:
        for w in wrapped:
            w.flush()       # what is still buffered, while a broken pipe is caught
        if sys.stdout is wrapped[0]:
            sys.stdout = saved[0]
        if sys.stderr is wrapped[1]:
            sys.stderr = saved[1]


def _main(argv) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help") or argv[0].startswith("--format"):
        # `emir`, `emir --help`, `emir --help --format json`
        if prescan_format(argv) == "json":
            Out("json", sys.stdout).emit(document(
                "help", command=None, text=__doc__.strip() + "\n",
                commands=sorted(COMMANDS)))
        else:
            print(__doc__.strip())
        return 0
    if argv[0] in ("-V", "--version"):
        print(f"emir {__version__}")
        return 0
    out = Out(prescan_format(argv[1:]), sys.stdout, command=argv[0])
    ctx = {"args": shlex.join(argv[1:]), "command": argv[0]}
    reset_help()
    with collecting(), _sigterm():
        try:
            cmd = COMMANDS.get(argv[0])
            if cmd is None:
                raise EmirError("EMIR-E508", f"unknown command {argv[0]!r}; "
                                f"`emir --help` lists them")
            # JSON mode: the one document is the only thing on stdout
            redirect = (contextlib.redirect_stdout(sys.stderr) if out.json
                        else contextlib.nullcontext())
            with redirect:
                return int(cmd(argv[1:], out) or 0)
        except EmirError as e:
            return _failed(out, e, ctx)
        except SystemExit as e:
            # `--help` (0); nothing in emir raises SystemExit with a message
            if e.code is None or isinstance(e.code, int):
                help_text = last_help()
                if out.json and out.emitted is None and help_text and not e.code:
                    # the help went to stderr with the rest; the document
                    # carries it, so JSON mode still prints one document
                    out.emit(document("help", command=argv[0], text=help_text,
                                      commands=sorted(COMMANDS)))
                return int(e.code or 0)
            return _failed(out, EmirError("EMIR-E901", f"internal error: {e.code}"), ctx)
        except _Terminated:
            return _failed(out, EmirError("EMIR-E906", "terminated (SIGTERM)"), ctx)
        except KeyboardInterrupt:
            return _failed(out, EmirError("EMIR-E904", "interrupted"), ctx)
        except Exception as e:                      # noqa: BLE001
            if os.environ.get("EMIR_DEBUG"):
                raise
            return _failed(out, coded(e), ctx)


@contextlib.contextmanager
def _sigterm():
    """While a command runs, SIGTERM raises `_Terminated` (main thread only;
    the previous handler comes back afterwards)."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def _raise(signum, frame):
        raise _Terminated()
    try:
        prev = signal.signal(signal.SIGTERM, _raise)
    except (ValueError, OSError):            # pragma: no cover - no signals here
        yield
        return
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, prev)


def _coded_any(e: BaseException) -> EmirError:
    """`coded`, plus the two interruptions."""
    if isinstance(e, KeyboardInterrupt):
        return EmirError("EMIR-E904", "interrupted")
    if isinstance(e, _Terminated):
        return EmirError("EMIR-E906", "terminated (SIGTERM)")
    return coded(e)


def coded(e: BaseException) -> EmirError:
    """The coded error for an exception that reached the command line.
    Anything not anticipated is an internal error (EMIR-E901, exit 70)."""
    if isinstance(e, EmirError):
        return e
    if isinstance(e, MemoryError):
        return EmirError("EMIR-E418", f"out of memory: {e or 'the analysis does not fit'}"
                         f" -- try one --net at a time")
    if isinstance(e, ImportError):
        name = getattr(e, "name", None) or str(e)
        from .repo import install_hint
        top = (name or "").split(".")[0]
        if top in ("numpy", "scipy", "yaml"):
            # a core dependency: emir cannot run without it
            pkg = {"yaml": "pyyaml"}.get(top, top)
            return EmirError("EMIR-E603", f"a required package is missing ({name}): {e}. "
                             f"emir needs it to run: `pip install {pkg}` (or reinstall emir)")
        return EmirError("EMIR-E603", f"an optional package is missing ({name}): {e}. "
                         f"`{install_hint('all')}` installs every optional one")
    if isinstance(e, FileNotFoundError):
        # "input file not found: x.yaml", not Python's "[Errno 2] No such ..."
        return EmirError("EMIR-E101", f"input file not found: {e.filename}" if e.filename
                         else str(e), path=e.filename)
    if isinstance(e, OSError):
        return EmirError("EMIR-E107", str(e), path=getattr(e, "filename", None))
    return EmirError("EMIR-E901", f"internal error: {type(e).__name__}: {e} -- re-run "
                     f"with EMIR_DEBUG=1 for the traceback and report it")


def _failed(out: Out, err: EmirError, ctx: dict) -> int:
    if out.json and out.emitted is None:
        out.emit(error_document(err, command=ctx.get("command"), ctx=ctx,
                                fmt=out.fmt_args()))
    else:
        print(error_text(err, ctx), file=sys.stderr)
    return err.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
