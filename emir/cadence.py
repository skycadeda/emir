"""The Cadence side of EMIR: `emir extract` (GDS + CDL -> DSPF with PVS LVS
and Quantus). (The Virtuoso form's save lines are `emir save-lines --mode
rcc|cc-grid`: the person picks the mode in the form.) `emir extract --tool
magic`, the open-PDK extractor, is `emir.magic`; this module parses both.

Status. This repository has no Cadence tool and claims none was verified
anywhere: the PVS command line, the LVS MATCH gate and the Quantus command
file are tested here as text and against stand-in executables only. They are
verified on site by the local agent, on the machine that has the tools:
`emir extract --run` refuses to extract unless LVS says MATCH, and
`emir check <cell>.<kind>.dspf --process P` on the DSPF it writes names every
annotation EMIR needs that is missing (widths, cut counts, coordinates,
instance pins). Until that passes there, they are UNVERIFIED.

`emir extract` is a licensed-tool action. Without `--run` it prints the plan
-- the commands, the command file, where each tool and deck was found, what
is missing -- and runs nothing (EMIR-I680). With `--run`:

1. **PVS LVS** writes the LVS database (`svdb`) with the cross reference
   Quantus reads (`-rc_data`). The report must say MATCH, or the extraction
   is refused (EMIR-E280): a netlist extracted on connectivity LVS has not
   verified simulates fine and means nothing. A matched LVS in the work
   directory that is newer than the GDS and the CDL is reused, so the R-only
   and the C-only extraction of one block share one LVS and their names agree.
2. **Quantus** reads that database and writes `<cell>.<kind>.dspf` (one file
   per kind, so a second kind never overwrites the first).

Which kind for which flow:

* `r_only` -- the EM/IR grid: the DSPF `emir run` screens. For a big block
  (ADC top, PLL top) it is screened in cc-grid mode.
* `c_only_coupled` -- the CC netlist: devices and coupling capacitance, no
  resistance, so the rails are ideal and a big block simulates. Its
  instance-pin currents drive the R-only grid (`--mode cc-grid`).
* `rc_coupled` -- the RC netlist of a small block, simulated with every
  branch current saved (`--mode rcc`): the exact reference. An `r_only`
  netlist is not an RCC reference (it has no capacitance at all).
* `rc_decoupled`, `c_only_decoupled` -- grounded instead of coupling
  capacitance (decap and dynamic-IR studies).

The settings in `LOAD_BEARING` carry the flow; `tests/test_cadence_extract.py`
checks each one is in the command file of every kind.
"""
from __future__ import annotations

import os
import re
import shlex
import string
import subprocess
import sys
import time

from .errors import EmirError, notice

#: the extraction kinds (Quantus `extract -type`)
KINDS = ("r_only", "c_only_coupled", "rc_coupled", "rc_decoupled", "c_only_decoupled")

#: what each kind is for (the plan and the docs say it)
KIND_USE = {
    "r_only": "the EM/IR grid DSPF that `emir run` screens (cc-grid for big blocks)",
    "c_only_coupled": "the CC netlist simulated for instance-pin currents (--mode cc-grid)",
    "rc_coupled": "the RC netlist of a small block, simulated with every branch "
                  "current saved (--mode rcc)",
    "rc_decoupled": "R plus grounded C (decap and dynamic-IR studies)",
    "c_only_decoupled": "grounded C only (decap studies)",
}

#: The Quantus command file. `${...}` fields are filled by `render_ccl`; the
#: same text is `agent/cadence/quantus/emir.ccl.tmpl` (a test keeps them equal).
CCL_TEMPLATE = r"""# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Vijay Gupta and contributors
# Part of EMIR, the open-source EM/IR screening tool. Not a Cadence-supplied file.
# EMIR -- Quantus command file template. UNVERIFIED until checked on site; verified
# on site by the local agent when `emir extract --run` passes its LVS MATCH gate
# and `emir check` passes on the DSPF it writes.
# Rendered by `emir extract` and by agent/cadence/quantus/render_ccl.py.
# KIND: r_only (the EM/IR grid) | c_only_coupled, c_only_decoupled (the CC
#       netlist for --mode cc-grid) | rc_coupled, rc_decoupled (RC netlists)
# LOAD-BEARING (do not drop): -analysis "em", the three -include_*_model false
# flags, -temperature, -sub_node_char ":", -disable_instances false,
# -net_name_space "SCHEMATIC", -output_xy, an absolute LVS database path.
log_file -file_name "qrc.${KIND}.log"

extract \
		-selection "all" \
		-type "${KIND}"

extraction_setup \
		-analysis "em" \
		-array_vias_spacing "auto" \
		-max_fracture_length infinite \
		-max_fracture_length_unit "MICRONS" \
		-max_fracture_via_count 1 \
		-max_via_array_count 4 \
		-net_name_space "SCHEMATIC"

filter_res \
		-min_res 0.001 \
		-remove_dangling_res "false"

output_db \
		-type dspf \
		-subtype extended \
		-add_explicit_vias true \
		-disable_instances false \
		-hierarchy_delimiter "/" \
		-include_cap_model "false" \
		-include_res_model "false" \
		-include_parasitic_cap_model "false" \
		-include_parasitic_res_model "comment" \
		-include_parasitic_res_length "true" \
		-include_parasitic_res_width false \
		-include_parasitic_res_width_drawn true \
		-output_xy \
		"CANONICAL_RES" \
		"PARASITIC_RES" \
		"GENERIC" \
		-netlist_coupling_values "single" \
		-add_bulk_terminal false \
		-sub_node_char ":" \
		-device_finger_delimiter "@"

output_setup \
		-directory_name "qrc_${KIND}" \
		-file_name "${OUT_FILE}" \
		-net_name_space "SCHEMATIC" \
		-temporary_directory_name "qrc_tmp_${KIND}" \
		-keep_temporary_files "false" \
		-compressed false

process_technology \
		-technology_directory "${TECH_DIR}/${CORNER}" \
		-technology_name "${CORNER}" \
		-temperature ${TEMPERATURE}

input_db \
		-type ${LVS_DB_TYPE} \
		-directory_name "${SVDB}" \
		-hierarchy_delimiter "/" \
		-run_name "${RUN_NAME}"
"""

#: The command-file settings the EMIR flow depends on: (text in the command
#: file, why). Losing any of them gives a DSPF that parses and is wrong.
LOAD_BEARING = (
    ('-analysis "em"',
     "keeps per-segment geometry; without it series resistance is reduced and "
     "a merged resistor hides the neck the check exists to find"),
    ('-include_cap_model "false"',
     "devices are written as plain elements; with the model name Spectre wants "
     "the model's full terminal list and stopped on a 3-terminal capacitor "
     "written with two terminals (seen during development)"),
    ('-include_res_model "false"', "as -include_cap_model"),
    ('-include_parasitic_cap_model "false"', "as -include_cap_model"),
    ("-temperature ",
     "Quantus scales R and writes *|OPERATING_TEMPERATURE; emir reads it and "
     "does not scale R a second time (a wrong pairing is a silent error on "
     "every voltage)"),
    ('-sub_node_char ":"',
     "emir puts a node that has no *|S record on its net by splitting its "
     "name at ':'; with another character such a net falls apart"),
    ("-disable_instances false",
     "keeps the instance-pin (*|I) records that simulated currents attach to "
     "(inferred, not tested with the flag on)"),
    ('-net_name_space "SCHEMATIC"',
     "the R-only and the C-only extraction name nets alike, so CC currents map "
     "onto the R-only grid"),
    ("-output_xy", "node and resistor coordinates: the viewer places results by them"),
    ("-include_parasitic_res_width_drawn true", "the wire width ($W) that J = I/w needs"),
)

#: input_db types Quantus can read connectivity from (`pvs` the default).
#: Every one is UNVERIFIED until checked on site (`emir extract --run`, then
#: `emir check` on the DSPF it writes).
LVS_DB_TYPES = ("pvs", "pegasus", "assura", "calibre")

#: the LVS report EMIR reads, and the line that says the comparison matched
#: (both configurable: `lvs_report`, `lvs_match` in the site file)
LVS_REPORT = "{cell}.lvsrpt.cls"
LVS_MATCH = r"Run Result\s*:\s*MATCH"

#: settings: name -> (option dest, environment variable, what it is)
SETTINGS = {
    "pvs_lvs_rules": ("rules", "PVS_RULES", "the PVS LVS rule deck of the PDK"),
    "qrc_tech_dir": ("tech_dir", "QRC_TECH_DIR",
                     "the Quantus technology directory (one sub-directory per corner)"),
    "qrc_corner": ("corner", "QRC_CORNER",
                   "the Quantus corner (a sub-directory of the technology directory)"),
    "extract_temperature": ("temperature", "EMIR_EXTRACT_TEMP",
                            "the extraction temperature in C (Quantus scales R to it)"),
}


#: a cell name Quantus's command file, PVS's command line and a file name
#: can carry as one word
CELL_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.$<>!#+-]*$")
#: characters a value in the Tcl-parsed command file must not hold
CCL_UNSAFE = re.compile(r'["\[\]${}\\\n\r]')


def check_cell(value: str, option: str) -> str:
    """`value` if it is a plain cell name, else EMIR-E513."""
    if not CELL_NAME.match(value or ""):
        raise EmirError("EMIR-E513", f"{option} {value!r} is not a plain cell name (letters, "
                        f"digits and _ . $ < > ! # + -): it is written into the Quantus command "
                        f"file and names the work directory", command="extract")
    return value


def check_ccl_value(value: str, what: str) -> str:
    """`value` if the Tcl-parsed command file can carry it inside quotes."""
    if CCL_UNSAFE.search(str(value)):
        raise EmirError("EMIR-E513", f"{what} {value!r} holds a quote, bracket, brace, `$`, "
                        f"backslash or line break, which the Quantus command file cannot "
                        f"carry", command="extract")
    return value


def render_ccl(svdb: str, cell: str, out_file: str, tech_dir: str, corner: str,
               temperature: float, kind: str = "r_only", lvs_db_type: str = "pvs") -> str:
    """The Quantus command file for one extraction. `svdb` is made absolute:
    Quantus runs in the work directory, where a relative path would be
    resolved a second time and not found. Every value is checked first: the
    file is Tcl-parsed, so a quote or bracket in one would end or run code."""
    check_cell(cell, "--cell")
    for what, v in (("the LVS database path", os.path.abspath(svdb)),
                    ("the DSPF name", os.path.basename(out_file)),
                    ("the technology directory", tech_dir), ("the corner", corner)):
        check_ccl_value(v, what)
    if kind not in KINDS:
        raise EmirError("EMIR-E513", f"--kind {kind!r}: one of {', '.join(KINDS)}",
                        command="extract")
    if lvs_db_type not in LVS_DB_TYPES:
        raise EmirError("EMIR-E513", f"LVS database type {lvs_db_type!r}: one of "
                        f"{', '.join(LVS_DB_TYPES)}", command="extract")
    return string.Template(CCL_TEMPLATE).substitute(
        KIND=kind, SVDB=os.path.abspath(svdb), RUN_NAME=cell,
        OUT_FILE=os.path.basename(out_file), TECH_DIR=tech_dir.rstrip("/"),
        CORNER=corner, TEMPERATURE=f"{float(temperature):f}", LVS_DB_TYPE=lvs_db_type)


def dspf_name(cell: str, kind: str) -> str:
    """`<cell>.<kind>.dspf`: one file per kind."""
    return f"{cell}.{kind}.dspf"


# ------------------------------------------------------------------ settings


def _env(name):
    v = os.environ.get(name) if name else None
    return v if v not in (None, "") else None


def _setting(key: str, explicit) -> tuple:
    """`(value, source)` of one extraction setting: the option, else its
    environment variable, else the site file, else (None, "not set")."""
    from . import site
    _dest, env, _what = SETTINGS[key]
    if explicit not in (None, ""):
        return explicit, "argument"
    v = _env(env)
    if v is not None:
        return v, f"${env}"
    v, src = site.get(key)
    if v not in (None, ""):
        return v, src
    return None, "not set"


#: tool -> (environment variable, site-file key, executable name on $PATH)
TOOLS = {"pvs": ("PVS_BIN", "pvs", "pvs"), "qrc": ("QRC_BIN", "quantus", "qrc")}


def _exe(value: str) -> str | None:
    """An executable: a path (it must exist and be executable) or a name
    looked up on $PATH."""
    import shutil
    v = os.path.expanduser(str(value))
    if os.sep in v:
        return os.path.abspath(v) if os.path.isfile(v) and os.access(v, os.X_OK) else None
    return shutil.which(v)


def _tool(name: str, explicit) -> dict:
    """Where `pvs` or `qrc` is: the option, $PVS_BIN / $QRC_BIN, the site file
    (`pvs:`, `quantus:`), then $PATH. The first one given is the one used."""
    import shutil

    from . import site
    env, key, exe = TOOLS[name]
    site_v, site_src = site.get(key)
    for value, src in ((explicit, "argument"), (_env(env), f"${env}"),
                       (site_v, site_src if site_v not in (None, "") else "")):
        if value not in (None, ""):
            p = _exe(value)
            return {"path": p, "found": p is not None, "source": src, "looked_for": str(value)}
    p = shutil.which(exe)
    return {"path": p, "found": p is not None, "source": "$PATH", "looked_for": exe}


def _abs(p):
    return os.path.abspath(os.path.expanduser(str(p))) if p else None


def lvs_matched(report: str, pattern: str = LVS_MATCH) -> bool | None:
    """True when the report says MATCH, False when it does not, None when
    there is no report."""
    if not os.path.isfile(report):
        return None
    with open(report, encoding="utf-8", errors="replace") as f:
        return re.search(pattern, f.read()) is not None


# ------------------------------------------------------------------ the plan


def plan(a) -> dict:
    """Everything `emir extract` would do, from its parsed options `a`. Reads
    nothing but file metadata (and an existing LVS report); runs nothing."""
    from . import site
    for what, p in (("--gds", a.gds), ("--cdl", a.cdl)):
        if not os.path.isfile(p):
            raise EmirError("EMIR-E101", f"{what} {p}: no such file", path=p)
    gds, cdl = _abs(a.gds), _abs(a.cdl)
    check_cell(a.cell, "--cell")
    if a.source_cell:
        check_cell(a.source_cell, "--source-cell")
    work_root = site.get("work_dir", default="emir_out")[0] or "emir_out"
    work = _abs(a.work or os.path.join(str(work_root), f"extract_{a.cell}"))
    dspf_file = dspf_name(a.cell, a.kind)
    tools = {"pvs": _tool("pvs", a.pvs), "qrc": _tool("qrc", a.qrc)}
    settings, missing = {}, []
    for key, (dest, env, what) in SETTINGS.items():
        v, src = _setting(key, getattr(a, dest))
        settings[key] = {"value": None if v is None else str(v), "source": src}
        if v is None:
            missing.append({"what": key, "why": f"{what} is not set",
                            "how": f"--{dest.replace('_', '-')} on the command line, "
                                   f"${env}, or `{key}:` in the site file"})
    for key in ("pvs_lvs_rules", "qrc_tech_dir"):
        v = settings[key]["value"]
        if v is not None:
            v = _abs(v)
            settings[key]["value"] = v
            exists = os.path.isfile(v) if key == "pvs_lvs_rules" else os.path.isdir(v)
            if not exists:
                missing.append({"what": key, "why": f"{v} does not exist",
                                "how": f"point `{key}` at the PDK's "
                                       f"{'rule deck' if key == 'pvs_lvs_rules' else 'directory'}"})
    t = settings["extract_temperature"]["value"]
    if t is not None:
        try:
            settings["extract_temperature"]["value"] = float(t)
        except ValueError:
            missing.append({"what": "extract_temperature", "why": f"{t!r} is not a number",
                            "how": "give the temperature in C, e.g. --temperature 25"})
            settings["extract_temperature"]["value"] = None
    corner, tech = settings["qrc_corner"]["value"], settings["qrc_tech_dir"]["value"]
    if corner and tech and os.path.isdir(tech) and not os.path.isdir(os.path.join(tech, corner)):
        missing.append({"what": "qrc_corner", "why": f"{tech} has no sub-directory {corner!r}",
                        "how": "name a corner the technology directory holds"})
    for name, tl in tools.items():
        if not tl["found"]:
            env, key, _exe_name = TOOLS[name]
            missing.append({"what": name,
                            "why": f"{name} was not found (looked for "
                                   f"{tl['looked_for']!r}, from {tl['source']})",
                            "how": f"--{name}, ${env}, `{key}:` in the site file, or $PATH"})

    report_pat = str(site.get("lvs_report", default=LVS_REPORT)[0] or LVS_REPORT)
    report = os.path.join(work, report_pat.format(cell=a.cell))
    match_re = str(site.get("lvs_match", default=LVS_MATCH)[0] or LVS_MATCH)
    svdb = os.path.join(work, "svdb")
    matched = lvs_matched(report, match_re)
    fresh = (matched is True and os.path.isdir(svdb)
             and all(os.path.getmtime(report) >= os.path.getmtime(p) for p in (gds, cdl)))
    reuse = bool(fresh and not a.rerun_lvs)

    rules = settings["pvs_lvs_rules"]["value"]
    pvs_argv = [tools["pvs"]["path"] or "pvs", "-lvs", "-rc_data", "-ui_data",
                "-gds", gds, "-layout_top_cell", a.cell, "-source_cdl", cdl,
                "-source_top_cell", a.source_cell or a.cell, rules or "<pvs_lvs_rules>"]
    ccl_file = f"qrc_{a.kind}.ccl"
    ccl = None
    if tech and corner and settings["extract_temperature"]["value"] is not None:
        ccl = render_ccl(svdb, a.cell, dspf_file, tech, corner,
                         settings["extract_temperature"]["value"], a.kind)
    qrc_argv = [tools["qrc"]["path"] or "qrc", "-cmd", ccl_file]
    ctl = os.path.join(os.path.dirname(rules), "pvs_control_file") if rules else None
    steps = []
    if ctl and os.path.isfile(ctl):
        steps.append(_step("control-file", ["cp", ctl, work], work,
                           "the deck includes ./pvs_control_file from the run directory",
                           None, "reused" if reuse else "planned"))
    steps.append(_step("lvs", pvs_argv, work,
                       "PVS LVS: connectivity and the cross reference Quantus reads "
                       "(-rc_data)", "pvs",
                       "reused" if reuse else "planned", log=os.path.join(work, "pvs.log")))
    steps.append(_step("lvs-gate", [], work,
                       f"the LVS report must say MATCH ({report})", None,
                       ("passed" if reuse else "planned"), report=report))
    steps.append(_step("quantus", qrc_argv, work,
                       f"Quantus, kind {a.kind}: {KIND_USE[a.kind]}", "quantus", "planned",
                       log=os.path.join(work, f"qrc_{a.kind}.stdout")))
    return {"cell": a.cell, "source_cell": a.source_cell or a.cell, "kind": a.kind,
            "kind_use": KIND_USE[a.kind], "gds": gds, "cdl": cdl, "work_dir": work,
            "dspf": os.path.join(work, dspf_file), "tools": tools, "settings": settings,
            "missing": missing, "ready": not missing,
            "lvs": {"svdb": svdb, "report": report, "match_regex": match_re,
                    "matched": matched, "reuse": reuse, "seconds": None},
            "ccl": ccl, "ccl_path": os.path.join(work, ccl_file), "steps": steps,
            "load_bearing": [flag.strip() for flag, _why in LOAD_BEARING],
            "provenance": "UNVERIFIED until checked on site: verified on site by the local "
                          "agent when this extraction passes its LVS MATCH gate and `emir "
                          "check` passes on the DSPF it writes; tested in the repository "
                          "with stand-in tools only"}


def _step(id, argv, cwd, why, licensed, status, **extra) -> dict:
    cmd = f"cd {shlex.quote(cwd)} && {shlex.join(argv)}" if argv else ""
    return {"id": id, "argv": list(argv), "command": cmd, "cwd": cwd, "why": why,
            "licensed_tool": licensed, "status": status, "exit_code": None,
            "seconds": None, **extra}


# ------------------------------------------------------------------ running


def _run_tool(argv, cwd, log_path, timeout, ctx) -> tuple:
    """Run one tool with its output in `log_path`. `(exit status, seconds)`."""
    t0 = time.time()
    try:
        with open(log_path, "w", encoding="utf-8") as lf:
            p = subprocess.run(argv, cwd=cwd, stdout=lf, stderr=subprocess.STDOUT,
                               stdin=subprocess.DEVNULL, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise EmirError("EMIR-E681", f"{os.path.basename(argv[0])} ran longer than "
                        f"{timeout:g} s and was stopped (log: {log_path})",
                        path=log_path) from e
    except FileNotFoundError as e:
        raise EmirError("EMIR-E680", f"{argv[0]} cannot be started: {e.strerror}",
                        **ctx) from e
    return p.returncode, time.time() - t0


def run(p: dict, a, log=print) -> dict:
    """Run the plan `p`: LVS (unless reused), the MATCH gate, Quantus."""
    import shutil
    ctx = {"gds": a.gds, "cdl": a.cdl, "cell": a.cell, "kind": a.kind}
    for m in p["missing"]:
        if m["what"] in ("pvs", "qrc"):
            raise EmirError("EMIR-E680", f"{m['why']}; give it with {m['how']}",
                            details={"missing": p["missing"]}, **ctx)
    if p["missing"]:
        raise EmirError("EMIR-E580", "; ".join(f"{m['what']}: {m['why']}"
                                               for m in p["missing"]),
                        details={"missing": p["missing"]}, **ctx)
    from .output import make_dir, write_text
    work = make_dir(p["work_dir"])
    by_id = {s["id"]: s for s in p["steps"]}
    lvs = by_id["lvs"]
    if lvs["status"] != "reused":
        if "control-file" in by_id:
            shutil.copy(by_id["control-file"]["argv"][1], work)
            by_id["control-file"]["status"] = "done"
        log(f"[emir] PVS LVS {p['cell']} ...")
        rc, dt = _run_tool(lvs["argv"], work, lvs["log"], a.timeout, ctx)
        lvs.update(status="done", exit_code=rc, seconds=round(dt, 3))
        p["lvs"]["seconds"] = round(dt, 3)
        matched = lvs_matched(p["lvs"]["report"], p["lvs"]["match_regex"])
        p["lvs"]["matched"] = matched
        gate = by_id["lvs-gate"]
        if not matched:
            gate["status"] = "failed"
            path = p["lvs"]["report"] if matched is False else lvs["log"]
            why = ("does not say MATCH" if matched is False
                   else "was not written (see the PVS log)")
            raise EmirError("EMIR-E280", f"LVS of {p['cell']}: the report "
                            f"{p['lvs']['report']} {why}; extraction refused",
                            path=path, details={"report": p["lvs"]["report"],
                                                "log": lvs["log"], "exit_code": rc})
        gate["status"] = "passed"
        log(f"[emir] PVS LVS MATCH in {dt:.1f} s")
    else:
        log(f"[emir] reusing the matched LVS in {p['lvs']['svdb']}")
    write_text(p["ccl_path"], p["ccl"])
    q = by_id["quantus"]
    log(f"[emir] Quantus {p['kind']} ...")
    rc, dt = _run_tool(q["argv"], work, q["log"], a.timeout, ctx)
    q.update(status="done", exit_code=rc, seconds=round(dt, 3))
    name = os.path.basename(p["dspf"])
    found = next((c for c in (p["dspf"], os.path.join(work, f"qrc_{p['kind']}", name))
                  if os.path.isfile(c)), None)
    if found is None:
        q["status"] = "failed"
        qlog = os.path.join(work, f"qrc.{p['kind']}.log")
        raise EmirError("EMIR-E281", f"Quantus (exit {rc}) wrote no {name} in {work}",
                        path=qlog if os.path.isfile(qlog) else q["log"],
                        details={"log": qlog, "stdout": q["log"], "exit_code": rc})
    p["dspf"] = found
    log(f"[emir] Quantus done in {dt:.1f} s -> {found}")
    return p


# ------------------------------------------------------------------ the command


def _text(p: dict, dry: bool) -> str:
    L = [f"emir extract: {p['cell']}, kind {p['kind']} -- {p['kind_use']}"]
    if dry:
        L.append("DRY RUN: nothing was run. PVS and Quantus are licensed tools: add "
                 "--run once the person agrees.")
    for k in ("gds", "cdl", "work_dir", "dspf"):
        L.append(f"  {k:8s} {p[k]}")
    L.append("tools:")
    for k, t in p["tools"].items():
        L.append(f"  {k:8s} {t['path'] or 'NOT FOUND'}  ({t['source']})")
    L.append("settings:")
    for k, s in p["settings"].items():
        L.append(f"  {k:20s} {s['value'] if s['value'] is not None else 'NOT SET'}"
                 f"  ({s['source']})")
    L.append("steps:")
    for i, s in enumerate(p["steps"], 1):
        lic = f"  [licensed: {s['licensed_tool']}]" if s["licensed_tool"] else ""
        L.append(f"  {i}. {s['id']:12s} [{s['status']}] {s['command'] or s['why']}{lic}")
    if p["missing"]:
        L.append("missing:")
        L += [f"  - {m['what']}: {m['why']} ({m['how']})" for m in p["missing"]]
    if dry and p["ccl"]:
        L.append(f"command file ({p['ccl_path']}):")
        L += ["  " + ln for ln in p["ccl"].rstrip().splitlines()]
    return "\n".join(L)


def build_parser():
    from .output import parser
    ap = parser("emir extract", description=(
        "GDS -> DSPF. --tool quantus (the default): GDS + CDL with PVS LVS and Quantus; "
        "without --run it prints the plan and runs nothing (licensed tools: ask the person "
        "before --run). --tool magic: magic (open source) extracts the cell with every "
        "resistor's layer, width, length and place (a magic that writes resistor "
        "geometry: `extresist geoout`), and EMIR writes the DSPF, the RC netlist rcc "
        "simulates, and the CC netlist cc-grid simulates; it runs at once."))
    ap.add_argument("--tool", default="quantus", choices=("quantus", "magic"),
                    help="quantus: PVS LVS + Quantus (licensed; planned unless --run); "
                         "magic: magic's extraction with resistor geometry (open source)")
    ap.add_argument("--gds", required=True, help="the layout (GDS)")
    ap.add_argument("--cdl", default=None,
                    help="quantus: the netlist LVS compares against (CDL; required)")
    ap.add_argument("--cell", default=None,
                    help="the layout top cell (quantus: required; magic: default the "
                         "layout's only top cell, read with the klayout package)")
    ap.add_argument("--kind", default=None, choices=KINDS,
                    help="quantus (required): r_only: the EM/IR grid; c_only_coupled: the "
                         "CC netlist (cc-grid); rc_coupled: the RC netlist of a small block "
                         "(rcc)")
    ap.add_argument("--source-cell", default=None,
                    help="quantus: the CDL top cell, if it is not --cell")
    ap.add_argument("--work", default=None,
                    help="quantus: work directory (default <work_dir>/extract_<cell>)")
    ap.add_argument("--rules", default=None,
                    help="quantus: PVS LVS rule deck (site: pvs_lvs_rules)")
    ap.add_argument("--tech-dir", default=None,
                    help="quantus: Quantus technology directory (site: qrc_tech_dir)")
    ap.add_argument("--corner", default=None, help="quantus: Quantus corner (site: qrc_corner)")
    ap.add_argument("--temperature", default=None, metavar="C",
                    help="quantus: extraction temperature in C (site: extract_temperature)")
    ap.add_argument("--pvs", default=None, help="quantus: pvs executable (default: "
                    "$PVS_BIN, site file, $PATH)")
    ap.add_argument("--qrc", default=None, help="quantus: qrc executable (default: $QRC_BIN, "
                    "site file `quantus:`, $PATH)")
    ap.add_argument("--site", default=None, metavar="FILE",
                    help="the site file to read the tools and decks from (default: "
                         "$EMIR_SITE, ~/.emir/site.yaml)")
    ap.add_argument("--rerun-lvs", action="store_true",
                    help="quantus: run LVS again even when a matched, up-to-date one is there")
    ap.add_argument("--timeout", type=float, default=7200.0, metavar="S",
                    help="seconds each tool may run (default 7200)")
    ap.add_argument("--run", action="store_true",
                    help="quantus: run PVS and Quantus (licensed tools); without it, the plan "
                         "only (magic always runs: it is open source)")
    ap.add_argument("--process", default=None,
                    help="magic (required): the process file: its `pdk:` name finds magic's "
                         "startup file, its `magic:` section names magic's layers, the "
                         "devices' terminals and the magic to use")
    ap.add_argument("--magicrc", default=None, metavar="FILE",
                    help="magic: its startup file (default: $EMIR_MAGICRC, else "
                         "$PDK_ROOT/<pdk>/libs.tech/magic/<pdk>.magicrc for the process file's "
                         "pdk: name)")
    ap.add_argument("--magic", default=None, metavar="EXE",
                    help="magic: the magic to run, one that writes resistor geometry (default: "
                         "$EMIR_MAGIC, the process file's magic: binary: (a magic's bare name "
                         "on $PATH), the site file's magic:, then magic-8.3.515-geo and magic "
                         "on $PATH)")
    ap.add_argument("--out", default=None, metavar="DIR",
                    help="magic: where the DSPF and the CC netlist go; magic runs in its "
                         "magic/ subdirectory, EMIR's own (default <work_dir>/extract_<cell>;"
                         " a <cell>.dspf or <cell>.cc.spice there that EMIR did not write is "
                         "never overwritten: EMIR-E646)")
    ap.add_argument("--tolerance", type=float, default=10.0,
                    help="magic: `extresist tolerance` (default 10): a net "
                         "whose resistance is small next to its devices' is not split into "
                         "resistors (EMIR-W645 lists such nets)")
    ap.add_argument("--port-pins", default="tied", choices=("tied", "separate"),
                    help="magic: what to do with a port labelled at several places (each "
                         "place its own node): tied (default) shorts them to the port with "
                         "0 V sources, so current may leave by any of them; separate leaves "
                         "them apart")
    return ap


#: options of one tool that the other does not take: (option, dest, default)
QUANTUS_ONLY = (("--cdl", "cdl", None), ("--kind", "kind", None),
                ("--source-cell", "source_cell", None), ("--work", "work", None),
                ("--rules", "rules", None), ("--tech-dir", "tech_dir", None),
                ("--corner", "corner", None), ("--temperature", "temperature", None),
                ("--pvs", "pvs", None), ("--qrc", "qrc", None),
                ("--rerun-lvs", "rerun_lvs", False), ("--run", "run", False))
MAGIC_ONLY = (("--process", "process", None), ("--magicrc", "magicrc", None),
              ("--magic", "magic", None), ("--out", "out", None),
              ("--tolerance", "tolerance", 10.0), ("--port-pins", "port_pins", "tied"))


def _check_tool_options(a):
    """Refuse the other tool's options (EMIR-E507), and the ones this tool
    needs that are missing."""
    other = QUANTUS_ONLY if a.tool == "magic" else MAGIC_ONLY
    given = [opt for opt, dest, default in other if getattr(a, dest) != default]
    if given:
        raise EmirError("EMIR-E507", f"{', '.join(given)}: not an option of --tool {a.tool}",
                        command="extract")
    need = ((("--process", a.process),) if a.tool == "magic"
            else (("--cdl", a.cdl), ("--kind", a.kind), ("--cell", a.cell)))
    missing = [opt for opt, v in need if not v]
    if missing:
        raise EmirError("EMIR-E507", f"--tool {a.tool} needs {' and '.join(missing)}",
                        command="extract")


def cmd_extract(argv, out):
    """`emir extract`: the plan (default) or the extraction (`--run`); with
    `--tool magic`, magic's extraction (`emir.magic`)."""
    a = build_parser().parse_args(argv)
    _check_tool_options(a)
    if a.tool == "magic":
        from .magic import extract
        return _with_site(a, lambda: extract(a, argv, out))
    return _with_site(a, lambda: _extract(a, out))


def _with_site(a, fn):
    """`fn()` with --site's file as the site file, if given."""
    if not a.site:
        return fn()
    from . import site
    if not os.path.isfile(a.site):
        raise EmirError("EMIR-E101", f"--site {a.site}: no such file", path=a.site)
    old = os.environ.get("EMIR_SITE")
    os.environ["EMIR_SITE"] = os.path.abspath(a.site)
    site.load(reload=True)
    try:
        return fn()
    finally:
        if old is None:
            os.environ.pop("EMIR_SITE", None)
        else:
            os.environ["EMIR_SITE"] = old
        site.load(reload=True)


def _extract(a, out):
    from .output import command_line, document
    p = plan(a)
    base = ["extract", "--gds", a.gds, "--cdl", a.cdl, "--cell", a.cell, "--kind", a.kind]
    keep = [x for opt in ("source_cell", "work", "rules", "tech_dir", "corner",
                          "temperature", "pvs", "qrc", "site")
            for x in ((f"--{opt.replace('_', '-')}", getattr(a, opt))
                      if getattr(a, opt) not in (None, "") else ())]
    steps = []
    if not a.run:
        notice("EMIR-I680", "dry run: nothing was run; add --run to run PVS and Quantus "
               "(licensed tools: ask the person first)", log=_stderr)
        print(_text(p, dry=True))
        if p["ready"]:
            steps.append({"why": "run LVS and Quantus as planned (licensed tools: ask "
                                 "the person first)",
                          "command": command_line(base, keep, "--run", out.fmt_args())})
    else:
        print(_text(p, dry=False), file=sys.stderr)
        run(p, a, log=_stderr)
        steps.append({"why": "the nets and their sizes in the new DSPF",
                      "command": command_line("nets", p["dspf"], out.fmt_args())})
        if a.kind == "r_only":
            steps.append({"why": "what to run next with this grid DSPF",
                          "command": command_line("next", "--dspf", p["dspf"],
                                                  out.fmt_args())})
        print(f"DSPF: {p['dspf']}")
    out.emit(document("extract", tool="quantus", dry_run=not a.run, **p, next_steps=steps))
    return 0


def _stderr(line):
    print(line, file=sys.stderr)
