"""JSON Schemas (draft 2020-12) of every document emir reads or writes.

`emir schema <name>` prints one, `emir schema --list` lists them, and
`scripts/gen_schemas.py` writes them to `agent/schemas/<name>.schema.json`
(a test fails when those copies are stale). The schemas are the contract an
agent codes against: every `--format json` output names its schema in its
``schema`` field (``emir.<name>/1``), and `tests/test_schemas.py` validates
the real output of every command against it.

Compatibility: a document may gain fields (objects allow additional
properties) without a version change; removing or changing the meaning of a
field moves the version (``/2``). The process-file schema is stricter -- an
unknown key inside a layer or via entry is most likely a typo, and `emir
check` FAILs on it too (EMIR-E316) -- and describes the file as JSON sees it
(YAML integer keys become strings).

Numbers: a value the run could not compute (NaN or Inf) is written as null,
so every computed number in a result document may be null; a reader reports
it as "not a number", never as zero.

Inputs have schemas too: `process` (the process file), `currents` (the
--currents JSON) and `names` (the `<result>.names` sidecar that maps a
simulator's signal names onto the DSPF's).
"""
from __future__ import annotations

import json
import os

DRAFT = "https://json-schema.org/draft/2020-12/schema"
CODE_PATTERN = r"^EMIR-[EWI][1-9][0-9]{2}$"

S = {"type": "string"}
N = {"type": "number"}
I = {"type": "integer"}       # noqa: E741
B = {"type": "boolean"}
NULL = {"type": "null"}


def opt(x: dict) -> dict:
    """`x` or null."""
    return {"anyOf": [x, NULL]}


def arr(items: dict) -> dict:
    return {"type": "array", "items": items}


def obj(props: dict, required=(), extra=True, **kw) -> dict:
    d = {"type": "object", "properties": props}
    if required:
        d["required"] = list(required)
    if not extra:
        d["additionalProperties"] = False
    d.update(kw)
    return d


CODE = {"type": "string", "pattern": CODE_PATTERN}
NEXT_STEPS = arr(obj({"why": S, "command": S, "shell": {
    "const": True, "description": "present (true) when the command is not an emir command "
    "(ls, tail, klayout ...): it needs a shell, which the MCP server does not have "
    "(emir_cli refuses it with EMIR-E591)"}, "needs_human": {
    "const": True, "description": "present (true) when the step leads to a run the person "
    "decides on (it re-simulates, or runs a licensed tool): ask them first"}},
    ["why", "command"],
    description="Exact commands to run next, in order; each runs as printed. An emir "
                "command keeps the caller's --format, except a run's `emir report` step "
                "(its Markdown is what to relay)."))
NOTICE = obj({"code": CODE, "level": {"enum": ["warning", "info"]}, "message": S},
             ["code", "level", "message"])
FINDING = obj({"level": {"enum": ["FAIL", "WARN", "INFO", "OK"]}, "code": CODE,
               "area": S, "message": S, "fix": S, "command": S},
              ["level", "code", "area", "message", "fix", "command"])
VERDICT = {"enum": ["FAIL", "WARN", "CLEAN"]}
METRIC = {"enum": ["avg", "rms", "peak"]}


def document(name: str, title: str, description: str, props: dict, required=()) -> dict:
    """A document schema: the envelope (`schema`, `emir_version`) plus `props`."""
    return {
        "$schema": DRAFT,
        "$id": f"urn:emir:schema:{name}:1",
        "title": title,
        "description": description,
        "type": "object",
        "properties": {"schema": {"const": f"emir.{name}/1"},
                       "emir_version": S, **props},
        "required": ["schema", "emir_version", *required],
    }


# ------------------------------------------------------------------ the documents

#: a computed number: null when the run could not compute it (NaN or Inf)
NUM = opt(N)

ITEM = obj({"net": S, "name": S, "layer": S, "kind": {"enum": ["wire", "via"]},
            "metric": METRIC, "ratio": opt(N), "ratios": {"type": "object"},
            "severity": {"enum": ["FAIL", "WARN"]}, "w_um": opt(N), "cuts": opt(I),
            "cuts_from": opt(S), "fix": opt(S),
            "bbox_um": opt({"type": "array", "items": N, "minItems": 4, "maxItems": 4,
                            "description": "where the item is: [llx, lly, urx, ury] in um "
                                           "(the DSPF's $LLX..$URY, or the layout's "
                                           "rectangle); null when the extraction wrote no "
                                           "coordinates"})},
           ["net", "name", "layer", "kind", "metric", "ratio", "severity", "fix"])

RUN_NET = obj({
    "net": S, "mode": {"enum": ["dc", "rcc"],
                       "description": "rcc: every branch current of a simulation of the RC "
                                      "netlist; dc: the DC currents of --currents"},
    "mode_reason": opt(S),
    "verdict": VERDICT, "power": B, "governing_metric": opt(METRIC),
    "max_ratio": {"anyOf": [NUM, {"type": "object", "additionalProperties": NUM}]},
    "metrics": {**arr(METRIC), "description": "the metrics checked on this net: avg, rms "
                "and peak"},
    "ir_drop_mV": NUM, "ir_kind": {"enum": ["drop", "spread"]}, "dynamic_mV": NUM,
    "ir_resistance": opt(obj(
        {"kind": {"enum": ["simulated", "scaled", "as extracted"]}, "temp_C": N,
         "stated": B}, ["kind", "temp_C", "stated"],
        description="the resistances the IR (static and dynamic) is computed with. "
                    "`simulated` (rcc): those the simulation ran, the DSPF's, at `temp_C`: "
                    "the temperature the DSPF states (`stated`), else the process file's "
                    "temp_reference; the IR is the simulated circuit's, and EMIR-I407 says "
                    "when the EM limits are at another temperature. `scaled` (DC): each "
                    "wire's scaled by its layer's TCR to the analysis temperature `temp_C` "
                    "(vias not scaled); `as extracted` (DC): the DSPF states that "
                    "temperature")),
    "kcl_residual_A": NUM, "metric_note": opt(S), "worst": opt({"type": "object"}),
    "window": opt(obj(
        {"t0_s": N, "t1_s": N, "period_s": N, "n_periods": I, "change": N, "branches": I},
        ["t0_s", "t1_s", "period_s", "n_periods", "change", "branches"],
        description="rcc: the window every average is taken over, the record's last "
                    "`n_periods` (up to 3) whole periods of --period (`period_s`). rcc is "
                    "exact for the simulated record over the whole periods you give "
                    "(--period); whether the testbench has settled is yours to establish, "
                    "as for any .meas. `branches`: how many matter (those carrying 1 % of "
                    "the net's largest average, rms or peak); `change`: the largest change "
                    "of their average, rms or peak between the record's last two periods "
                    "(at most 0.02: over it the run is refused, EMIR-E429). null on DC runs "
                    "and at an operating point (one time point: avg = rms = peak).")),
    "n_violations": I, "worst_items": arr(ITEM), "unmapped": arr(S), "no_width": arr(S),
    "via_cuts": obj({"from_area": I, "assumed_one_cut": arr(S)},
                    ["from_area", "assumed_one_cut"]),
    "unsolved": opt(obj({"pieces": I, "n_nodes": I, "nodes": arr(S)},
                        ["pieces", "n_nodes", "nodes"])),
    "dynamic": opt(obj({
        "worst_mV": NUM, "node": opt(S), "x_um": NUM, "y_um": NUM, "t_s": NUM,
        "source": opt(S), "n_steps": opt(I)},
        ["worst_mV", "node", "t_s"],
        description="rcc: the worst dynamic droop on the net and where, from the simulated "
                    "branch currents. null on DC runs.")),
}, ["net", "mode", "metrics", "verdict", "power", "governing_metric",
    "max_ratio", "ir_drop_mV", "ir_kind", "worst_items", "n_violations", "no_width",
    "via_cuts"])

NOT_CHECKED = obj({
    "unmapped_layers": arr(S),
    "limits_missing": arr(obj({"name": S, "kind": {"enum": ["wire", "via"]},
                               "metrics": arr(METRIC)}, ["name", "kind", "metrics"])),
    "estimated_limits": arr(S),
    "vias_assumed_one_cut": {"type": "object", "additionalProperties": arr(S)},
    "wires_without_width": {"type": "object", "additionalProperties": arr(S)},
    "unsolved": {"type": "object", "additionalProperties": {"type": "object"}},
    "avg_not_derated": opt(obj(
        {"temp_C": N, "em_tref_C": N, "table_top_C": opt(N), "what": S,
         "direction": {"const": "non-conservative"}}, ["temp_C", "em_tref_C", "what"],
        description="The avg limits applied above the temperature they hold at (no "
                    "jmax_factor table, or above its top): not conservative there")),
    "bidirectional": {"type": "object", "additionalProperties": arr(obj(
        {"name": S, "layer": S, "avg_ratio": NUM, "abs_avg_ratio": NUM},
        ["name", "avg_ratio", "abs_avg_ratio"])),
        "description": "Per net, branches whose current reverses: the signed average the "
                       "avg check uses cancels, while the time average of |I| reaches the "
                       "warn fraction of the avg limit. EMIR applies no bidirectional "
                       "(recovery) rule: check them against the PDK's AC rule."},
    "diagonal_wires": {"type": "object", "additionalProperties": arr(S),
                       "description": "Per net, the wires on diagonal (non-Manhattan) "
                                      "shapes (`$dir=diag`, the magic route): checked at "
                                      "the width magic gives, its tiles' extent and not "
                                      "the width across the current, so their current "
                                      "density can read low (EMIR-W647)."},
    "via_crowding": {"type": "object", "additionalProperties": arr(obj(
        {"name": S, "layer": S, "ratio": NUM, "cuts": opt(I)}, ["name", "ratio"])),
        "description": "Per net, via arrays within 10 % of the limit: the per-cut check "
                       "uses the array's mean current, and the cuts facing the current "
                       "carry more; confirm them cut by cut (the extractor's per-cut "
                       "vias)."},
}, ["unmapped_layers", "limits_missing", "estimated_limits",
    "vias_assumed_one_cut", "wires_without_width"])

#: one entry of a names sidecar's map: [DSPF name, sign]
NAME_ENTRY = {"type": "array", "minItems": 2, "maxItems": 2,
              "prefixItems": [{"type": "string", "minLength": 1}, {"type": "number"}]}

CURRENT_SOURCE = obj({"kind": {"enum": ["dc", "simulation", "netlist", "none"]},
                      "path": opt(S), "description": S,
                      "simulator": opt(S), "result": opt(S)},
                     ["kind", "path", "description"])

PLAN_STEP = obj({"n": I, "id": S, "command": S, "why": S, "needs_human": B,
                 "licensed_tool": opt(S), "long_run": B,
                 "ask": {"type": "string", "description": "present when the step needs "
                         "something only the person can give or agree to (values typed "
                         "from a document): ask them first"},
                 "status": {"enum": ["todo", "waiting"]},
                 "after": arr(S), "placeholders": arr(S),
                 "tool": opt(obj({"name": S, "arguments": {"type": "object"}}, ["name"],
                                 description="over MCP (`emir mcp`): the typed tool call "
                                             "that runs the step's command (null: not an "
                                             "emir command)"))},
                ["n", "id", "command", "why", "needs_human", "licensed_tool", "long_run",
                 "status", "after", "placeholders"],
                description="One step of the plan.")

#: the maintainer's research-project disclaimer (`emir.RESEARCH_DISCLAIMER`)
RESEARCH = {"const": ("EMIR is a research project, provided as is, with no warranty or "
                      "guarantee of accuracy. It is your responsibility to test and verify "
                      "its results for your process, tools and design before relying on "
                      "them.")}
SIM_NAME = {"enum": ["spectre", "hspice", "xyce", "ngspice"]}
#: the kinds of current a run reads (emir/verify_sim.py `KINDS`)
CURRENT_KIND = {"enum": ["branch"]}
#: the brief of a record a run cites (emir/verify_sim.py `_brief`)
VERIFY_RECORD = obj({"simulator": SIM_NAME, "version": opt(S), "format": opt(S),
                     "date": opt(S), "passed": opt(B), "emir_version": opt(S),
                     "host": opt(S), "sim_args": opt(S), "covers": opt(arr(S))},
                    ["simulator", "passed"])
#: the run summary's `simulator_verification` (emir/verify_sim.py `status`)
SIM_VERIFICATION = obj({
    "status": {"enum": ["verified", "partly verified", "not verified", "unknown simulator",
                        "not applicable"]},
    "reason": opt({"enum": ["no record", "failed", "stale", "version unknown",
                            "other host", "format not covered", "current kinds not covered",
                            "records unreadable"]}),
    "simulator": opt(S), "version": opt(S), "format": opt(S),
    "detected_from": opt({"enum": ["result header", "names sidecar", "--sim"]}),
    "sim_args": opt(S), "sim_args_known": B,
    "version_checked": B, "record": opt(VERIFY_RECORD), "records_file": S,
    "ignored_records": I, "records_problem": opt(S),
    "current_kinds": opt(arr(obj({"kind": CURRENT_KIND, "what": S, "count": I,
                              "examples": arr(S), "covered": B, "verify_sim_covers": B},
                             ["kind", "count", "covered"]))),
    "command": opt(S), "note": opt(S)},
    ["status", "simulator", "version", "format", "record", "command"],
    description="Whether the latest valid `emir verify-sim` record of this machine for the "
                "simulator, version, output format and simulator arguments that wrote the "
                "run's currents passed with this emir minor version (`verified`, EMIR-I463). "
                "`not verified` gives its `reason`: `no record` or `other host` or `stale` "
                "(a pass of another emir minor version) with EMIR-W464 and `command`, the "
                "verification to run; `failed` (the latest record failed: a later failure "
                "cancels an earlier pass) with EMIR-W414; `version unknown` (neither the "
                "result's header nor the names sidecar of `emir simulate` says the version: "
                "never matched to a record, `command` null) with EMIR-W416; `format not "
                "covered` (an output format emir verify-sim never writes, such as CSDF: no "
                "record can match, `command` null) with EMIR-W464; `records unreadable` "
                "(this user may not read the records file: no record counts and none "
                "could be kept, `command` null; EMIR-W415 and EMIR-W464). A verification "
                "covers kinds of current (`current_kinds`, one entry per kind the run read: "
                "the branch currents, `covered` by the record, `verify_sim_covers` whether "
                "a verification of this emir could): a run whose kinds are not all covered "
                "(a record made before records said what they cover) is `not verified`, "
                "`reason` `current kinds not covered`, with EMIR-W464. `sim_args` "
                "is what the result's names sidecar says the simulator was given "
                "(`sim_args_known` false: the result does not say, and it is matched as "
                "given none). `ignored_records` counts invalid records, `records_problem` "
                "says why a records file cannot be read at all (EMIR-W415).")
CHECK_ITEM = obj({"name": S, "ok": B, "expected": {}, "got": {}, "tolerance": opt(N),
                  "detail": opt(S), "kind": CURRENT_KIND}, ["name", "ok"])
#: one `emir verify-sim` record as verified.json keeps it, validated strictly
#: (emir/verify_sim.py `record_problem` adds what a schema cannot say:
#: `passed` agrees with the checks; a passing record has a version)
VERIFY_RECORD_FULL = obj(
    {"schema": {"const": "emir.verify_sim/1"}, "simulator": SIM_NAME, "version": opt(S),
     "version_command": arr(S),
     "format": {"enum": ["psfascii", "tr0ascii", "nutascii"]}, "sim_args": opt(S),
     "emir_version": {"type": "string", "pattern": r"^\d+\.\d+"},
     "date": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"},
     "host": opt(S), "passed": B, "covers": arr(S), "checks": arr(CHECK_ITEM)},
    ["schema", "simulator", "version", "format", "emir_version", "date", "passed", "checks"],
    description="One verification: what `emir verify-sim` appends to verified.json. A run "
                "counts only valid records (`passed` a JSON boolean that agrees with the "
                "checks, a passing record with its simulator's version); the latest one for "
                "a key (simulator, version, format, sim_args) decides; a record with a "
                "`host` counts only on that host. `covers` lists the kinds of current its "
                "checks prove (each backed by passing checks of that `kind`: `branch`, the "
                "branch currents rcc reads); a record without it covers no kind.")


def _run_args(out: dict):
    """`run` records its own arguments (`argv`) and where it ran (`cwd`), so
    `emir next --run-summary` re-plans from them."""
    out["run"]["properties"]["argv"] = arr(S)
    out["run"]["properties"]["cwd"] = {
        "type": "string", "description": "the directory the run ran in: the relative paths "
        "of `argv` are relative to it (`emir next --run-summary` resolves them, so its "
        "commands work from any directory)"}


def _schemas() -> dict:
    out = {}
    out["error"] = document(
        "error", "emir error",
        "What every command prints in --format json when it cannot do its job. "
        "`code` is the registry code (`emir explain`); the exit status is "
        "`exit_code`.",
        {"command": opt(S), "code": CODE, "level": {"const": "error"}, "title": S,
         "message": S, "hint": S, "exit_code": I, "next_steps": NEXT_STEPS,
         "details": {"type": "object"}},
        ["command", "code", "level", "title", "message", "hint", "exit_code",
         "next_steps", "details"])
    out["info"] = document(
        "info", "emir info", "Where the tools, packages and settings were found.",
        {"python": S, "package_dir": S, "sample_dir": S,
         "tools": arr(obj({"name": S, "path": opt(S), "found": B, "source": S,
                           "licensed": B, "version": opt(S),
                           # simulators: the newest passing `emir verify-sim`
                           # record on this machine, or null
                           "verified": opt(obj({"version": opt(S), "format": opt(S),
                                                "date": opt(S)})),
                           # magic: the one the SKY130 route runs (`magic_route`),
                           # which the stock `magic` above often is not
                           "route_path": opt(S), "writes_geometry": B},
                          ["name", "path", "found", "source", "licensed"])),
         "records_file": S,
         "magic_route": obj(
             {"path": opt(S), "source": opt(S), "version": opt(S), "writes_geometry": B,
              "tried": arr({"type": "object"})},
             ["path", "writes_geometry"],
             description="The magic `emir extract --tool magic` would run (the SKY130 "
                         "route): the first of --magic, $EMIR_MAGIC, the site file's "
                         "magic:, magic-8.3.515-geo and magic on $PATH that writes resistor "
                         "geometry (the geoout patch); path null when none does (the "
                         "extraction stops with EMIR-E643). A process file's `magic: "
                         "binary:` is tried first by `emir extract` and `emir next --gds`."),
         "settings": arr(obj({"key": S, "value": opt(S), "source": S},
                             ["key", "value", "source"])),
         "site_file": opt(S),
         "packages": arr(obj({"name": S, "version": opt(S), "required": B, "purpose": S},
                             ["name", "version", "required"])),
         "commands": arr(S), "exit_codes": {"type": "object"}, "notices": arr(NOTICE)},
        ["python", "tools", "packages", "commands", "exit_codes"])
    out["selftest"] = document(
        "selftest", "emir selftest",
        "The offline self-test on the bundled sample. Exit 0 when `passed`, else "
        "70 with `code` EMIR-E902.",
        {"passed": B, "code": opt(CODE),
         "checks": arr(obj({"step": S, "ok": B, "message": S}, ["step", "ok", "message"])),
         "out_dir": opt(S), "kept": B},
        ["passed", "code", "checks"])
    out["validate"] = document(
        "validate", "emir validate",
        "The analytic validation suite. Exit 0 when `passed`, else 70 with `code` "
        "EMIR-E903.",
        {"passed": B, "code": opt(CODE),
         "cases": arr(obj({"name": S, "kind": S, "status": {"enum": ["PASS", "FAIL", "SKIP"]},
                           "seconds": opt(N), "reason": opt(S)},
                          ["name", "kind", "status"])),
         "n_run": I, "n_failed": I, "n_skipped": I, "seconds": N, "out_dir": opt(S)},
        ["passed", "code", "cases", "n_run", "n_failed", "n_skipped"])
    out["nets"] = document(
        "nets", "emir nets", "The nets of a DSPF and their sizes.",
        {"dspf": S, "design": opt(S), "summary": S, "n_nodes": I, "n_resistors": I,
         "nets": arr(obj({"name": S, "resistors": I, "nodes": I, "inst_pins": I, "port": B},
                         ["name", "resistors", "nodes", "inst_pins", "port"]))},
        ["dspf", "nets"])
    out["pins"] = document(
        "pins", "emir pins", "The instance pins on one net.",
        {"dspf": S, "net": S,
         "pins": arr(obj({"name": S, "inst": S, "pin": S, "node": S, "x": N, "y": N,
                          "layer": opt({"type": ["string", "integer", "number"]})},
                         ["name", "inst", "pin", "node", "x", "y"])),
         "split": {**arr(S), "description": "the instance pins (inst:pin) that reach the "
                   "net at more than one place: one terminal, several `*|I` records, each "
                   "with its own node in `pins`"}},
        ["dspf", "net", "pins", "split"])
    out["save_lines"] = document(
        "save_lines", "emir save-lines",
        "The simulator lines that save every branch current an rcc run reads (also what "
        "`emir run --save-branches` prints in JSON mode): Spectre `save`, HSPICE "
        "`.probe` (with `.option probe post=2 post_version=2001`), Xyce `.print`, ngspice "
        "`.save` (`simulator`). For HSPICE, Xyce and ngspice, `names` is the map of the "
        "`<result>.names` sidecar (`emir schema names`), written to each path in "
        "`sidecars`, and `raw` the result the lines write.",
        {"dspf": S, "net": opt(S), "mode": {"enum": ["rcc"]},
         "prefix": S, "simulator": SIM_NAME,
         "lines": arr(S), "count": opt(I), "sidecars": arr(S), "raw": opt(S),
         "names": {"type": "object", "additionalProperties": NAME_ENTRY},
         "nets": arr(S), "direct": I,
         # --out: the file written
         "out": opt(S)},
        ["dspf", "net", "mode", "lines", "sidecars"])
    out["check"] = document(
        "check", "emir check",
        "The input check. Exit 0 when `passed`, else 2. Each finding carries its "
        "code and the command that fixes or diagnoses it; `next_steps` is the run "
        "command when the inputs pass, the fixes when they do not.",
        {"passed": B, "n_fail": I, "n_warn": I, "findings": arr(FINDING),
         "context": {"type": "object"}, "next_steps": NEXT_STEPS},
        ["passed", "n_fail", "n_warn", "findings", "next_steps"])
    out["run"] = document(
        "run", "emir run (the run summary)",
        "One run's results: what `--format json` prints and `--summary-out` "
        "writes. Screening, not sign-off (`disclaimer`); a research project, no "
        "guarantee of accuracy (`research_disclaimer`). `simulator_verification` says "
        "whether this machine verified the simulator that wrote the currents.",
        {"disclaimer": {"const": "Screening, not sign-off."},
         "research_disclaimer": RESEARCH, "verdict": VERDICT,
         "counts": obj({"FAIL": I, "WARN": I, "CLEAN": I}, ["FAIL", "WARN", "CLEAN"]),
         "worst_net": opt(obj({"net": S, "ratio": NUM}, ["net", "ratio"])),
         "command": opt(S), "created": opt(S), "design": opt(S), "dspf": S,
         "process": opt(S),
         "temp_C": opt(N), "em_tref_C": opt(N), "avg_derate": opt(N), "metrics": arr(METRIC),
         "current_source": CURRENT_SOURCE, "simulator_verification": SIM_VERIFICATION,
         "html": opt(S),
         "charts": {"type": "array", "items": S,
                    "description": "with --html: the run's EM/IR chart, every net on the "
                                   "layout (emir_chart.png, and emir_chart_light.png), "
                                   "absolute paths; empty when it was not drawn (the "
                                   "notices say why: EMIR-I720, EMIR-W720)"},
         "viewer": opt({"type": "object"}), "power_nets": arr(S), "nets": arr(RUN_NET),
         "not_checked": NOT_CHECKED,
         "input_check": obj({"ran": B, "passed": opt(B), "forced": B,
                             "warnings": arr(FINDING), "fails": arr(FINDING)},
                            ["ran", "passed", "forced", "warnings"]),
         "notices": arr(NOTICE), "next_steps": NEXT_STEPS},
        ["disclaimer", "research_disclaimer", "verdict", "counts", "dspf", "temp_C",
         "current_source", "simulator_verification", "nets", "not_checked", "input_check",
         "notices", "next_steps"])
    out["report"] = document(
        "report", "emir report",
        "The summary of a run an agent relays to a person (`markdown` is the "
        "same content as --format md).",
        {"source": S, "verdict": VERDICT,
         "disclaimer": {"const": "Screening, not sign-off."},
         "research_disclaimer": RESEARCH,
         "simulator_verification": opt(SIM_VERIFICATION),
         "modes": arr(S), "current_source": CURRENT_SOURCE, "temp_C": opt(N),
         "command": opt(S), "created": opt(S),
         "windows": {**arr(obj({"nets": arr(S), "window": opt({"type": "object"})},
                               ["nets", "window"])),
                     "description": "rcc's averaging windows, one per distinct window "
                                    "with the nets averaged over it: the run summary's "
                                    "`nets[].window` (null at an operating point). Empty "
                                    "on DC runs."},
         "nets": arr(obj({"net": S, "verdict": VERDICT, "mode": S,
                          "governing_metric": opt(METRIC), "metrics": arr(METRIC),
                          "worst_ratio": NUM, "ir_drop_mV": NUM, "ir_kind": S, "power": B},
                         ["net", "verdict", "mode", "governing_metric", "worst_ratio"])),
         "worst_items": arr(ITEM),
         "ir_by_power_net": arr(obj({"net": S, "ir_drop_mV": NUM, "ir_kind": S,
                                     # rcc's dynamic droop (simulated branch currents)
                                     "dynamic_mV": NUM, "dynamic": opt({"type": "object"}),
                                     # the resistances the IR is computed with
                                     "ir_resistance": opt({"type": "object"})},
                                    ["net", "ir_drop_mV"])),
         "not_checked": arr(S), "input_warnings": arr(S), "run_warnings": arr(S),
         "charts": {"type": "array", "items": S,
                    "description": "the run's chart to attach beside the text when the "
                                   "reader sees images (dark, then light): a run's "
                                   "emir_chart.png (--html)"},
         "markdown": S},
        ["source", "verdict", "disclaimer", "research_disclaimer", "nets", "worst_items",
         "ir_by_power_net", "not_checked", "input_warnings", "run_warnings", "markdown"])
    out["next"] = document(
        "next", "emir next",
        "The recommended command sequence for what the agent has. Run the steps "
        "in order and stop at the first that exits non-zero. Steps with "
        "`needs_human` true need the person's go-ahead (a licensed tool, or a "
        "long run) before the agent runs them. A step with `status` waiting does "
        "not run as printed: its `placeholders` (`<result.raw>` ...) stand for "
        "files the steps in `after` produce, to be replaced first.",
        {"inputs": {"type": "object"},
         "facts": {"type": "object", "description": "what the plan learnt from the inputs "
                   "(`size`, `dspf`, `result`); on the magic route (--gds, no DSPF) also "
                   "`magic_route` (the magic the extraction runs, as `emir info` gives it) "
                   "and `pdk_root`"},
         "tools": {"type": "object"},
         "mode": obj({"chosen": opt({"enum": ["rcc", "dc"]}), "why": S}, ["chosen", "why"],
                     description="Where the planned run's currents come from: rcc (a "
                                 "simulation of the RC netlist, every branch current read), "
                                 "dc (--currents), or null (not known yet)"),
         "missing": arr(obj({"what": S, "why": S, "how": S}, ["what", "why", "how"])),
         "steps": arr(PLAN_STEP), "advice": arr(S), "thresholds": {"type": "object"},
         "rules": arr(S)},
        ["inputs", "facts", "tools", "mode", "missing", "steps", "advice", "rules"])
    out["import_ict"] = document(
        "import_ict", "emir import-ict", "What an ICT import changed in a process file.",
        {"ict": S, "emir": S, "out": S, "process": S, "temp_reference": opt(N),
         "em_tref": opt(N), "jmax_factor": {"type": "object"}, "changes": arr(S),
         "provenance": opt({"enum": ["imported-ict", "mixed"],
                            "description": "the file's provenance after the import: "
                                           "imported-ict, or mixed when it also holds values "
                                           "entered by hand or from public PDK data; each "
                                           "imported value names the ICT under `sources:`"}),
         "notices": arr(NOTICE)},
        ["ict", "emir", "out", "changes", "notices"])
    out["gui_install"] = document(
        "gui_install", "emir gui-install", "The Virtuoso SKILL files written.",
        {"dir": S, "files": obj({"main": S, "app": S, "drf": S, "site": S},
                                ["main", "app", "drf"]),
         "cdsinit": S, "drf_note": S},
        ["dir", "files", "cdsinit"])
    # --- feature cadence ---
    out["extract"] = _extract_schema()
    # --- end feature cadence ---
    out["explain"] = document(
        "explain", "emir explain", "Registry entries of error codes.",
        {"codes": arr(obj({"code": CODE, "level": S, "area": S, "title": S, "meaning": S,
                           "hint": S, "command": S, "fallback": S, "exit_code": opt(I),
                           "retired": B,
                           "retired_reason": opt(S)},
                          ["code", "level", "title", "meaning", "hint", "command",
                           "exit_code", "retired"]))},
        ["codes"])
    out["schema_list"] = document(
        "schema_list", "emir schema --list", "The schemas `emir schema` prints.",
        {"schemas": arr(obj({"name": S, "id": S, "title": S, "file": S},
                            ["name", "id", "title", "file"]))},
        ["schemas"])
    out["help"] = document(
        "help", "emir --help",
        "What `--help` prints in --format json: the help text of the command "
        "(`command`), or of `emir` itself (`command` null).",
        {"command": opt(S), "text": S, "commands": arr(S)},
        ["command", "text"])
    # --- feature opensim ---
    files = arr(obj({"from": S, "to": S}, ["from", "to"]))
    out["simulate"] = document(
        "simulate", "emir simulate",
        "One simulation for the DSPF flow, the RC netlist with every branch current saved "
        "(rcc): the deck written (`deck`, `files`), the text "
        "result (`result`: nutascii, HSPICE ASCII .tr0, or Spectre's PSF ASCII "
        "directory) and its names sidecar (`names_sidecar`: the name map, the simulator, "
        "its version when it ran, the --sim-args; for Spectre beside the result directory, "
        "with no map), which `emir run --cc-sim <result>` reads. `found` of the `signals` currents asked for "
        "are in the result; `missing` names the DSPF currents that are not. Spectre and "
        "HSPICE are licensed: without --run `ran` is false, `command` is what would run "
        "(in `cwd`), and the next step is the same command with --run, after the "
        "person's go-ahead.",
        {"simulator": SIM_NAME, "ran": B, "executable": opt(S), "simulator_version": opt(S),
         "command": arr(S), "cwd": S, "testbench": S,
         "deck": S, "files": files, "result": S, "names_sidecar": opt(S), "log": opt(S),
         "mode": {"enum": ["rcc"]}, "nets": arr(S),
         "scope": opt(S), "path": arr(S), "where": S, "signals": I, "direct": I,
         "found": opt(I), "missing": arr(S), "seconds": opt(N),
         "points": opt(I), "analysis": opt(S), "notes": arr(S),
         "next_steps": NEXT_STEPS},
        ["simulator", "ran", "command", "testbench", "deck", "result", "names_sidecar",
         "mode", "nets", "signals", "found", "missing", "next_steps"])
    out["verify_sim"] = document(
        "verify_sim", "emir verify-sim",
        "The on-site verification of one simulator's route into EMIR: three simulations "
        "of testbenches with hand-checkable answers (`cases`: ideal loads dc and pulsed; "
        "`dev`, real devices on the rail), every branch current saved, read back, screened "
        "in rcc and compared with the analytic answers (`checks`). `passed` true exits 0, "
        "false exits 70 (`code` EMIR-E663); a "
        "licensed simulator without --run is only planned (`ran` false, `passed` null). "
        "A finished verification is kept as a record in `records_file` "
        "($EMIR_STATE_DIR or ~/.emir, verified.json): `version` is what the simulator "
        "printed for `version_command`; `host` only with --record-host. `covers` lists "
        "the kinds of current its checks prove (each check's `kind`: `branch`, the branch "
        "currents rcc reads): a run is verified only for those.",
        {"simulator": SIM_NAME, "ran": B, "passed": opt(B), "version": opt(S),
         "sim_args": opt(S),
         "version_command": arr(S), "format": S, "date": opt(S), "host": opt(S),
         "out_dir": S, "covers": arr(CURRENT_KIND),
         "cases": arr(obj({"case": {"enum": ["dc", "pulse", "dev"]},
                           "mode": {"enum": ["rcc"]},
                           "testbench": S, "deck": opt(S), "command": opt(arr(S)),
                           "cwd": opt(S), "result": opt(S), "emir_simulate": S,
                           "emir_run": S},
                          ["case", "mode", "testbench"])),
         "checks": arr(CHECK_ITEM), "record": opt(VERIFY_RECORD_FULL), "records_file": S,
         "code": opt(CODE), "next_steps": NEXT_STEPS},
        ["simulator", "ran", "passed", "format", "out_dir", "cases", "checks",
         "records_file", "code", "next_steps"])
    out["verified"] = document(
        "verified", "the verification records (verified.json)",
        "What `emir verify-sim` keeps in the EMIR state directory ($EMIR_STATE_DIR, else "
        "~/.emir): one record per verification, newest last, each an emir.verify_sim/1 "
        "record (simulator, version, format, emir_version, date, passed, checks; host "
        "only when asked). Runs look a passing record up by simulator, version and "
        "format (`simulator_verification` in the run summary).",
        {"records": arr(VERIFY_RECORD_FULL)},
        ["records"])
    # --- end feature opensim ---
    # --- feature mcp ---
    out["mcp"] = document(
        "mcp", "emir mcp --list",
        "What `emir mcp` serves over MCP (stdio): one typed tool per emir command "
        "(each returns that command's own JSON document), `emir_cli` for a whole "
        "command line, and the resources (error catalog, schemas, playbooks). "
        "`confirm` says when a tool needs the person's go-ahead (confirm=true on the "
        "call); `clients` holds this machine's configuration snippets.",
        {"sdk": obj({"installed": B, "version": opt(S),
                     "api": opt({"enum": ["MCPServer", "FastMCP"]}), "supported": B,
                     "requirement": S}, ["installed", "version", "supported"]),
         "server": obj({"name": S, "version": S, "transport": {"const": "stdio"},
                        "command": arr(S), "cwd": S, "agent_dir": opt(S),
                        "output_schemas": B, "isolate": B, "instructions": S},
                       ["name", "transport", "command", "cwd"]),
         "tools": arr(obj({"name": S, "command": opt(S), "title": S, "description": S,
                           "input_schema": {"type": "object"}, "outputs": opt(arr(S)),
                           "annotations": {"type": "object"},
                           "confirm": {"enum": ["never", "conditional", "always"]},
                           "confirm_when": arr(S)},
                          ["name", "command", "description", "input_schema", "confirm"])),
         "resources": arr(obj({"uri": S, "name": S, "mime_type": S, "description": S},
                              ["uri", "name", "mime_type"])),
         "clients": {"type": "object",
                     "additionalProperties": obj({"file": S, "format": {"enum": [
                         "json", "toml", "shell"]}, "snippet": S},
                         ["file", "format", "snippet"])},
         "next_steps": NEXT_STEPS},
        ["sdk", "server", "tools", "resources", "clients", "next_steps"])
    # --- end feature mcp ---
    out["process"] = process_schema()
    out["currents"] = {
        "$schema": DRAFT,
        "$id": "urn:emir:schema:currents:1",
        "title": "emir --currents",
        "description": "DC currents for `emir run --currents` / `emir check --currents`: "
                       "node name -> amps INTO the net at that node (a load drawing "
                       "current is negative, the feed positive). Finite numbers that "
                       "sum to zero (KCL), on nodes of one net (`emir pins` lists the "
                       "instance pins).",
        "type": "object",
        "minProperties": 1,
        "additionalProperties": {"type": "number"},
    }
    out["names"] = {
        "$schema": DRAFT,
        "$id": "urn:emir:schema:names:1",
        "title": "simulator names sidecar (<result>.names)",
        "description": "A JSON file written beside a simulation result as "
                       "`<result>.names` (result.raw -> result.raw.names). It maps a "
                       "simulator's signal names to the DSPF's branch currents `R:1`, "
                       "with the sign that turns the saved current into the current "
                       "from the resistor's first node to its second: 1 or -1. HSPICE, "
                       "Xyce and ngspice name currents i(xi0.rm1), I(XI0:RM1), "
                       "@r.xi0.rm1[i] ...; keys are matched without regard to "
                       "case. Without the sidecar those names do not match the DSPF "
                       "and the result reads as holding none of the net's currents. "
                       "`emir simulate` also records how the result was made: the "
                       "simulator, its version (when it ran here) and `sim_args`, which "
                       "the on-site verification (`emir verify-sim`) is keyed on; for "
                       "Spectre, whose names need no map, the sidecar beside the result "
                       "directory holds only those (an empty `map`).",
        "type": "object",
        "properties": {
            "map": {"type": "object", "additionalProperties": NAME_ENTRY},
            # which simulator the lines were written for (and its version,
            # when emir ran it): the on-site verification is looked up by it
            "simulator": SIM_NAME, "simulator_version": S,
            # the --sim-args emir simulate passed ("" for none), normalised
            "sim_args": S,
        },
        "required": ["map"],
        "examples": [{"map": {"I(XI0:RM1)": ["Rm1:1", 1], "I(XI0:RM2)": ["Rm2:1", 1]}}],
    }
    _run_args(out)
    return out


# --- feature cadence ---
def _extract_schema() -> dict:
    """`emir extract`: the plan (a dry run) or what the extraction did. `tool`
    says which: quantus (PVS LVS + Quantus) or magic (`emir extract --tool
    magic`, the open-PDK route)."""
    tool = obj({"path": opt(S), "found": B, "source": S, "looked_for": S},
               ["path", "found", "source"])
    setting = obj({"value": {"type": ["string", "number", "null"]}, "source": S},
                  ["value", "source"])
    step = obj({"id": S, "argv": arr(S), "command": S, "cwd": S, "why": S,
                "licensed_tool": opt(S),
                "status": {"enum": ["planned", "reused", "passed", "done", "failed"]},
                "exit_code": opt(I), "seconds": opt(N)},
               ["id", "argv", "command", "why", "licensed_tool", "status"])
    tried = obj({"value": S, "source": S, "path": opt(S), "result": S},
                ["value", "source", "path", "result"])
    quantus_required = ["ready", "kind", "cdl", "tools", "settings", "missing", "lvs",
                        "ccl", "steps"]
    magic_required = ["process", "rc_netlist", "magic", "magicrc", "files",
                      "ports", "port_pins", "nets", "resistors", "capacitors", "devices",
                      "layers", "unchecked_layers", "nets_without_resistance",
                      "floating_capacitance", "diagonal_resistors", "guessed_terminals",
                      "out_dir"]
    out = document(
        "extract", "emir extract",
        "GDS -> DSPF. `tool` quantus: PVS LVS and Quantus; `dry_run` true: the plan "
        "only, nothing was run (`ready` says whether --run could run it; `missing` says "
        "what is not); `dry_run` false: LVS matched and Quantus wrote `dspf`. PVS and "
        "Quantus are licensed tools: an agent asks the person before --run. `tool` "
        "magic: magic (open source) extracted the cell with every resistor's layer, "
        "width, length and place (`magic.path`: a magic with the geoout patch) and emir "
        "wrote `dspf` (also the RC netlist an rcc simulation runs, `rc_netlist`); "
        "`unchecked_layers`: magic's "
        "conductors the process file gives no limit (their current is in the IR drop); "
        "`work_dir`: where magic ran (`magic/` under `out_dir`, a directory of emir's "
        "own); `floating_capacitance`: capacitor nodes on no net of the network "
        "(EMIR-W646); `diagonal_resistors`: the DSPF's resistors on diagonal shapes, "
        "whose width is magic's tile extent (EMIR-W647; `emir run` lists them as not "
        "checked).",
        {"tool": {"enum": ["quantus", "magic"]}, "dry_run": B, "ready": B, "cell": S,
         "source_cell": S,
         "kind": {"enum": ["r_only", "rc_coupled", "rc_decoupled"]},
         "kind_use": S, "gds": S, "cdl": S, "work_dir": S, "dspf": S,
         "tools": obj({"pvs": tool, "qrc": tool}, ["pvs", "qrc"]),
         "settings": {"type": "object", "additionalProperties": setting},
         "missing": arr(obj({"what": S, "why": S, "how": S}, ["what", "why", "how"])),
         "lvs": obj({"svdb": S, "report": S, "match_regex": S, "matched": opt(B),
                     "reuse": B, "seconds": opt(N)},
                    ["svdb", "report", "matched", "reuse"]),
         "ccl": opt(S), "ccl_path": S, "steps": arr(step), "load_bearing": arr(S),
         "provenance": S,
         # --tool magic
         "process": S, "rc_netlist": S,
         "magic": obj({"path": S, "source": S, "version": opt(S), "tried": arr(tried)},
                      ["path", "source", "tried"]),
         "magicrc": S, "files": {"type": "object", "additionalProperties": S},
         "seconds": opt(N), "ports": arr(S), "port_pins": {"enum": ["tied", "separate"]},
         "tolerance": N, "nets": I, "nets_with_resistors": I, "resistors": I,
         "capacitors": I, "devices": I, "extra_pins": I, "ties": I, "segments": I,
         "merged": I,
         "layers": arr(obj({"magic": S, "process": opt(S)}, ["magic", "process"])),
         "unchecked_layers": arr(S), "nets_without_resistance": arr(S),
         "floating_capacitance": arr(S), "diagonal_resistors": arr(S), "out_dir": S,
         "guessed_terminals": arr(S),
         "next_steps": NEXT_STEPS},
        ["tool", "dry_run", "cell", "gds", "work_dir", "dspf", "next_steps"])
    # which fields each tool's document holds (the schema states them in its
    # description: `if`/`then` is not a keyword every MCP client validates)
    out["description"] += (f" Quantus documents also hold {', '.join(quantus_required)}; "
                           f"magic ones {', '.join(magic_required)}.")
    return out
# --- end feature cadence ---


#: what a process file's `magic: binary:` may name (`magic.PROCESS_BINARY`)
MAGIC_NAME = {"type": "string", "pattern": r"^magic[A-Za-z0-9_.+-]*$"}


def process_schema() -> dict:
    """The process file (YAML; `pdks/TEMPLATE.yaml` documents every key). A
    value may be null: not given (`emir process new` writes every value
    null for a person to fill; `emir process check` lists what is still
    empty)."""
    nonneg = opt({"type": "number", "minimum": 0})
    num = opt(N)
    gds = opt({"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2})
    sources = {"type": "object", "additionalProperties": opt(S),
               "description": "Where each value of this entry came from: key -> "
                              "\"document, table or page\" (null: not said). A hand-entered "
                              "file's values each need one (EMIR-W320)."}
    entry_prov = {"enum": ["hand-entered", "imported-ict", "public-pdk-data"],
                  "description": "This entry's own provenance, in a `mixed` file."}
    layer = obj({
        "gds": gds, "sheet_res": nonneg, "tc1": num, "tc2": num, "min_width": nonneg,
        "jmax_avg": nonneg, "jmax_rms": nonneg, "jmax_rms_k": nonneg, "jmax_peak": nonneg,
        "width_loss_um": {**nonneg, "description": "um, the width loss dW of the design "
                          "manual's I_max = J x (W - dW), W the drawn width; applied to the "
                          "avg, rms and peak wire limits"},
        "estimate": B, "ict_name": S, "sources": sources, "provenance": entry_prov,
    }, ["sheet_res"], extra=False,
        description="A conductor layer. Limits in mA per um of width; give "
                    "jmax_rms (fixed) or jmax_rms_k (sqrt(k * delta_T)), not both.",
        **{"not": {"required": ["jmax_rms", "jmax_rms_k"],
                   "properties": {"jmax_rms": {"type": "number"},
                                  "jmax_rms_k": {"type": "number"}}}})
    via = obj({
        "gds": gds, "below": opt(S), "above": opt(S), "res": nonneg, "vcwidth": nonneg,
        "imax_avg": nonneg, "imax_rms": nonneg, "imax_peak": nonneg,
        "estimate": B, "rms_peak_estimate": B, "ict_name": S, "sources": sources,
        "provenance": entry_prov,
    }, ["below", "above", "res"], extra=False,
        description="A via (cut) layer. Limits in mA per cut; res in ohm per cut; "
                    "vcwidth (um, the cut side) turns a cut area into a count.")
    return {
        "$schema": DRAFT,
        "$id": "urn:emir:schema:process:1",
        "title": "emir process file",
        "description": "PDK EM/IR data for emir (YAML). Units: mA/um and mA/cut "
                       "for limits, ohm/square and ohm/cut, um, C. Every limit "
                       "comes from the PDK; never invent one. A null value is not given.",
        "type": "object",
        "properties": {
            "pdk": opt(S),
            "provenance": {"enum": ["hand-entered", "imported-ict", "public-pdk-data",
                                    "mixed"],
                           "description": "Where the file's values came from; reports say "
                                          "it per layer."},
            "warn_fraction": opt({"type": "number", "exclusiveMinimum": 0, "maximum": 1}),
            "temp_reference": num,
            "em_tref": num,
            "jmax_factor": {"type": "object", "additionalProperties": N,
                            "propertyNames": {"pattern": r"^-?[0-9]+(\.[0-9]*)?$"}},
            "derating": opt(obj({"model": {"const": "black"}, "ea_eV": nonneg, "n": nonneg,
                                 "below_tref": opt({"enum": ["clamp", "extrapolate"]})},
                                extra=False,
                                description="Black's equation, the alternative to a "
                                            "jmax_factor table: J(T)/J(em_tref) = "
                                            "exp[(ea_eV/(n k)) (1/T - 1/Tref)], T in kelvin. "
                                            "Below em_tref the limits stay as quoted "
                                            "(`below_tref: clamp`, the default) unless the "
                                            "file says `extrapolate` (EMIR-W326).")),
            "unit_confirmed": {"type": "array", "items": S,
                               "description": "Paths (`em_tref`, `layers/<name>/jmax_avg` ...) "
                                              "of values outside the range their quantity "
                                              "normally takes that the person checked against "
                                              "the design manual: such a value on a "
                                              "temperature, the derating or a limit blocks "
                                              "the run (EMIR-E331) until it is listed here."},
            "sources": {**sources, "description": "Where em_tref, temp_reference, derating "
                                                  "and jmax_factor came from."},
            "dspf_layers": {"type": "object", "additionalProperties": S},
            "magic": opt(obj({
                "binary": {"anyOf": [MAGIC_NAME, arr(MAGIC_NAME), NULL],
                           "description": "the magic to try, after --magic and $EMIR_MAGIC: "
                                          "a magic program's bare name on $PATH (starting "
                                          "with `magic`, no path: EMIR-E648), or a list; "
                                          "one built with the geoout patch"},
                "layers": {"type": "object", "additionalProperties": S,
                           "description": "magic's layer names (the PDK's magic technology "
                                          "file) -> this file's layers and vias; a name "
                                          "not listed folds as `dspf_layers` does"},
                "devices": {"type": "object", "additionalProperties": arr(S),
                            "description": "a device model (or a glob, e.g. "
                                           "sky130_fd_pr__*fet*) -> its terminal names in "
                                           "the order magic writes its nodes (the PDK's "
                                           ".subckt order): the DSPF's *|I pin names"},
                "sources": sources},
                extra=False,
                description="The magic route (`emir extract --tool magic`): magic's layer "
                            "names, the devices' terminal names and the magic to run.")),
            "layers": {"type": "object", "minProperties": 1, "additionalProperties": layer},
            "vias": {"type": "object", "additionalProperties": via},
            "ict_conductor_pattern": S,
            "ict_process": S,
        },
        "required": ["em_tref", "layers"],
    }


SCHEMAS = _schemas()

# --- feature process --- (process data entered by hand: emir/process_file.py)
_PROV = {"enum": ["hand-entered", "imported-ict", "public-pdk-data", "mixed"]}
#: the run summary's `limits_provenance`: where the applied limits came from
LIMITS_PROVENANCE = obj({
    "file": opt(_PROV),
    "entries": arr(obj({
        "name": S, "kind": {"enum": ["wire", "via"]}, "provenance": opt(_PROV),
        "sources": {"type": "object", "additionalProperties": opt(S)},
        "unsourced": arr(S), "limits": arr(S), "width_loss_um": opt(N)},
        ["name", "kind", "provenance", "sources", "unsourced", "limits"])),
    "temperature": obj({"em_tref_C": N, "em_tref_source": opt(S),
                        "derating": opt({"enum": ["black", "table"]}),
                        "black": opt(obj({"model": {"const": "black"}, "ea_eV": N, "n": N,
                                          "below_tref": opt(S)})),
                        "derating_source": opt(S),
                        "unsourced": arr(S)}, ["em_tref_C", "derating"]),
    "suspects": arr(obj({"path": S, "value": N, "unit": S, "likely": opt(S),
                         "confirmed": B, "blocking": B}, ["path", "value", "unit"]))},
    ["file", "entries", "temperature"],
    description="Where the limits the run applied came from: the process file's "
                "`provenance` (null: not stated), and per layer and via in use its own "
                "provenance, the source (document, table or page) of each value, the values "
                "a hand-entered file gives with no source (EMIR-W320), its EM limit keys and "
                "its width loss; the "
                "temperature the limits hold at, their derating and the temperatures with no "
                "source (`temperature.unsourced`); and `suspects`: the values outside the "
                "range their quantity normally takes, most often a unit slip (EMIR-W321; "
                "`blocking`: the input check refuses them, EMIR-E331; `confirmed`: listed "
                "under the file's `unit_confirmed`).")
_EMPTY = obj({"required": arr(obj({"path": S, "unit": S, "what": S}, ["path", "unit", "what"])),
              "optional": arr(obj({"path": S, "unit": S, "what": S}, ["path", "unit", "what"]))},
             ["required", "optional"],
             description="The values present but empty (null): required (emir refuses to run) "
                         "and optional (what they would check or scale is not used).")


def _process_schemas(out: dict):
    out["run"]["properties"]["limits_provenance"] = opt(LIMITS_PROVENANCE)
    out["report"]["properties"]["limits_provenance"] = opt(LIMITS_PROVENANCE)
    out["report"]["properties"]["limits"] = arr(S)
    out["process_new"] = document(
        "process_new", "emir process new",
        "The fill-in process file `emir process new` wrote (`out`), for a person to type "
        "the PDK's EM data into by hand when no readable process data exists: the layers "
        "and vias it covers (from the DSPF's layer tokens and $lvl numbers, or the lists "
        "given), each via's stacking as inferred from the DSPF (`stacking`: an `inferred "
        "... check` note, or null), and what is still empty. It never fills "
        "in a value. `next_steps`: the person types the values with their sources, then "
        "`emir process check`.",
        {"out": S, "dspf": opt(S), "design": opt(S), "pdk": opt(S),
         "provenance": {"const": "hand-entered"},
         "layers": arr(obj({"name": S, "note": S}, ["name"])),
         "vias": arr(obj({"name": S, "below": opt(S), "above": opt(S), "stacking": opt(S)},
                         ["name", "below", "above", "stacking"])),
         "dspf_layers": {"type": "object", "additionalProperties": S},
         "empty": _EMPTY, "interactive": B, "values_given": I, "next_steps": NEXT_STEPS},
        ["out", "layers", "vias", "empty", "interactive", "values_given", "next_steps"])
    out["process_check"] = document(
        "process_check", "emir process check",
        "What a process file still lacks before a run. Exit 0 when `passed`, else 2 (a "
        "required value empty, EMIR-E320, or another FAIL). `empty` lists the values still "
        "null; `unsourced` the values of a hand-entered file that name no source; "
        "`inferred` the stacking `emir process new` inferred and nobody confirmed; "
        "`suspects` the values outside their quantity's plausible range (a likely unit "
        "slip: `likely`); `coverage` (with --dspf) the DSPF layer tokens the file does not "
        "cover. The findings are `emir check`'s, which runs the same checks.",
        {"process": S, "dspf": opt(S), "pdk": opt(S), "provenance": opt(_PROV),
         "passed": B, "n_fail": I, "n_warn": I, "empty": _EMPTY, "unsourced": arr(S),
         "inferred": arr(S),
         "suspects": arr(obj({"path": S, "value": N, "unit": S, "range": arr(opt(N)),
                              "likely": opt(S)}, ["path", "value", "unit", "range"])),
         "coverage": opt(obj({"tokens": arr(S), "missing": arr(S),
                              "segments_not_covered": I}, ["tokens", "missing"])),
         "findings": arr(FINDING), "next_steps": NEXT_STEPS},
        ["process", "passed", "n_fail", "n_warn", "empty", "unsourced", "inferred",
         "suspects", "findings", "next_steps"])


# --- end feature process --- (applied below, once every document is defined)


# --- feature process --- (it adds `limits_provenance` to the run and report documents)
_process_schemas(SCHEMAS)


def names() -> list:
    return sorted(SCHEMAS)


def key(name: str) -> str:
    """The schema's key for `name`: `save-lines`, `save_lines` and
    `emir.save_lines/1` all name `save_lines`. EMIR-E512 if none does."""
    from .errors import EmirError
    k = name.replace("-", "_").removeprefix("emir.").split("/")[0]
    if k not in SCHEMAS:
        raise EmirError("EMIR-E512", f"no schema {name!r}; one of: {', '.join(names())}")
    return k


def get(name: str) -> dict:
    """One schema by name (see `key`)."""
    return SCHEMAS[key(name)]


def listing() -> list:
    return [{"name": n, "id": SCHEMAS[n]["$id"], "title": SCHEMAS[n]["title"],
             "file": f"agent/schemas/{n}.schema.json"} for n in names()]


def text(name: str) -> str:
    """The schema as written to `agent/schemas/<name>.schema.json`."""
    return json.dumps(SCHEMAS[name], indent=2) + "\n"


def write_all(directory: str) -> list:
    """Write every schema into `directory`; returns the paths."""
    os.makedirs(directory, exist_ok=True)
    out = []
    for n in names():
        p = os.path.join(directory, f"{n}.schema.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write(text(n))
        out.append(p)
    return out
