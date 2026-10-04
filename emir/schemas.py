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

from .em_basis import BASES, DEFAULT_BASIS

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

#: --- feature modes --- the self-consistent loop of one net (the waveform
#: loop, emir/tdloop.py)
SC_COMPANION = opt(obj(
    {"g_mS": arr(NUM), "C_fF": arr(NUM), "r2_median": NUM, "frozen": B},
    ["g_mS", "C_fF", "frozen"],
    description="the per-pin device admittance g + C d/dt that computed the droop this "
                "pass simulated (the companion update; null for pass 0 and pass 1): "
                "[median, max] over the pins, fitted from the two passes before (the "
                "secant of current against droop, clipped at >= 0); `frozen`: the fit "
                "was kept from an earlier pass (the residual was small)"))
SC_KCL = opt(obj(
    {"source": opt(S), "pins_avg_A": NUM, "source_avg_A": NUM, "avg_rel": NUM,
     "instant_rel": NUM},
    ["pins_avg_A", "source_avg_A", "avg_rel"],
    description="the split's KCL on this pass: the split pins' currents against the "
                "testbench's rail source over the window (`avg_rel`), and at every instant "
                "relative to its peak (`instant_rel`); EMIR-W541 above 1e-5 / 1e-4; null "
                "where the rail source's current is not saved (Spectre, a ground net)"))
SC_PASS = obj({"pass": I, "residual_mV": opt(N), "droop_change_mV": opt(N),
               "tolerance_mV": opt(N), "current_change": opt(N), "max_droop_mV": opt(N),
               "simulated_droop_mV": opt(N), "worst_ratio": opt(N), "ir_drop_mV": opt(N),
               "total_avg_A": opt(N), "seconds": opt(N), "post_seconds": opt(N),
               "points": opt(I), "pwl_points": opt(I),
               "pwl_tol_mV": opt({**N, "description": "the PWL compression tolerance of the "
                                  "droop this pass simulated (0.3 x the loop's tolerance, "
                                  "coarser when the point cap forced it: EMIR-W542)"}),
               "fed_by": opt({"enum": ["open loop", "plain", "plain (scaled)", "companion",
                                       "companion (frozen)"]}),
               "fed_scale": opt({**N, "description": "the first update's scale: 1, or below "
                                 "it when the plain droop was above 15 % of the supply "
                                 "(`plain (scaled)`)"}),
               "metric_change": opt(obj({"avg": NUM, "rms": NUM, "peak": NUM, "items": NUM,
                                         "devices": NUM, "branches": NUM},
                                        description="how far the answer moved from the pass "
                                                    "before (relative; null on pass 0): the "
                                                    "governing branch's avg, rms and peak; "
                                                    "`items`, any item the run reports (its "
                                                    "ratio at or above the warning "
                                                    "fraction); `devices`, any device's own "
                                                    "avg, rms or peak current; `branches`, "
                                                    "any branch carrying at least 1 % of the "
                                                    "largest (reported, not a stopping "
                                                    "condition)")),
               "companion": SC_COMPANION, "kcl": SC_KCL, "converged": B,
               "stop_rule": opt({"enum": ["residual", "metrics"]})},
              ["pass", "droop_change_mV", "current_change", "max_droop_mV", "converged"])
SELF_CONSISTENCY = opt(obj(
    {"method": {"enum": ["waveform"]}, "simulator": opt(S), "vdd": opt(N),
     "tol_mV": N, "tol_rel": opt(N), "max_passes": I, "converged": B, "diverged": B,
     "stopped": opt({"enum": ["converged", "settled", "residual grew twice",
                              "droop above half the supply", "pass limit", "time limit",
                              "simulation failed"]}),
     "reported_pass": I, "n_passes": I, "steps": opt(I),
     "at_pass_limit": B, "pwl_cap": opt(I),
     "feedback": opt(obj({"grid": {"enum": ["R-only", "RC"]}, "caps": I, "caps_F": NUM,
                          "unsized_caps": I, "decaps": I, "decaps_F": NUM,
                          "unsized_decaps": I, "simulated_decaps": I}, ["grid"],
                         description="the grid whose droop is fed back: `RC` when it has "
                                     "capacitance (`caps` capacitors of the DSPF, capacitor "
                                     "devices sized by cap_models included, `caps_F` in "
                                     "all, each to ground; `decaps` MOS decaps, `decaps_F` "
                                     "in all, each behind its access resistance, as "
                                     "--dynamic models them), else `R-only` (EMIR-W544 "
                                     "when the DSPF has no capacitance on the net). "
                                     "`unsized_caps` capacitor devices and `unsized_decaps` "
                                     "MOS decaps the process file cannot size are left out "
                                     "(EMIR-W547: the devices then read low, those "
                                     "decaps' straps are not checked); "
                                     "`simulated_decaps` decaps the testbench simulates "
                                     "(their current loads the grid through their pins)")),
     "scoring": opt(obj({"grid": {"enum": ["R-only", "RC", "max(R-only, RC)"]},
                         "basis": opt(S), "ac": opt(B)}, ["grid"],
                        description="how the net's branch currents were scored: `RC` through "
                                    "the RC grid (--dynamic's decap-aware EM, `basis` "
                                    "decap-aware); `max(R-only, RC)` per segment the larger "
                                    "of the two (--dynamic, the worst basis: conservative, it "
                                    "keeps the R-only grid's high readings); else `R-only`, "
                                    "the superposition of the pin currents on the R-only grid "
                                    "(EMIR-W545 when `feedback` is RC and the pins' currents "
                                    "vary). `ac`: whether the pins' currents vary over the "
                                    "window (constant ones charge no capacitor: every "
                                    "scoring agrees)")),
     "nets": arr(S), "passes": arr(SC_PASS),
     "rail": opt(obj({"volts": opt(N), "source": opt(S), "node": opt(S), "sign": opt(N),
                      "why": S}, ["volts", "why"],
                     description="the DC voltage the testbench drives the net with, read "
                                 "from it (Xyce, ngspice, HSPICE; null volts when it cannot "
                                 "be read): --vdd is checked against it (EMIR-E523)"))},
    ["vdd", "tol_mV", "converged", "diverged", "reported_pass", "passes"],
    description="--self-consistent: every pass of the waveform loop (emir/tdloop.py). Pass "
                "0 is the open-loop cc-grid result. Each pass feeds every pin of the net its "
                "own droop waveform (`fed_by`: plain after pass 0, scaled down when it is "
                "above 15 % of the supply, the companion update after that). `residual_mV` "
                "(also `droop_change_mV`): the largest difference, over the pins and the "
                "whole transient, between the droop the pass's currents produce through the "
                "grid (`feedback`) and the droop the pass simulated, the PWL as written "
                "(pass 0: the open-loop droop); `tolerance_mV`: max(`tol_rel` x the largest "
                "droop, `tol_mV`). `converged` (the pass stops the loop; `stop_rule` says "
                "by which rule): `residual`, the residual is below the tolerance and the "
                "governing branch's avg, rms and peak moved less than 0.5 % "
                "(`metric_change`); or `metrics` (`stopped: settled`), on this pass and the "
                "one before the governing branch, every reported item and every device "
                "moved less than 0.5 %, with the residual under 5 % of the droop, not "
                "grown by more than 10 % on either pass, and the PWL not coarsened past "
                "the tolerance: the residual then stays above its tolerance. "
                "`at_pass_limit`: it converged on the last pass --max-passes allows. "
                "`current_change`: the largest change of a branch's average current from the "
                "pass before, relative to the largest branch (null on pass 0). `seconds`: the "
                "pass's simulation; `post_seconds`: the grid side; `points`: the simulator's "
                "time points; `pwl_points`: the PWL points per pin it was fed, one time base "
                "for every pin of every net (at most `pwl_cap`: max(1 500, 0.3 x pass 0's "
                "time points)). "
                "`reported_pass` is the pass the net's results come from: the converged one, "
                "or pass 0 (the open loop) when the loop stopped at --max-passes (EMIR-W407), "
                "was stopped by its guard (EMIR-W406) or a later pass's simulation ran past "
                "its time limit or failed (EMIR-W543); `stopped` says which. `nets`: every "
                "net the loop closed together. Each pin's supply is the testbench's rail plus "
                "its droop; `vdd` is that rail's voltage (--vdd, or read from the "
                "testbench)."))

RUN_NET = obj({
    "net": S, "mode": {"enum": ["dc", "rcc", "cc-grid"]}, "mode_reason": opt(S),
    "verdict": VERDICT, "power": B, "governing_metric": opt(METRIC),
    "max_ratio": {"anyOf": [NUM, {"type": "object", "additionalProperties": NUM}]},
    "ir_drop_mV": NUM, "ir_kind": {"enum": ["drop", "spread"]}, "dynamic_mV": NUM,
    "kcl_residual_A": NUM, "metric_note": opt(S), "worst": opt({"type": "object"}),
    "n_violations": I, "worst_items": arr(ITEM), "unmapped": arr(S), "no_width": arr(S),
    "unsaved_pins": arr(S), "self_consistent": B,
    "self_consistency": SELF_CONSISTENCY,
    "droop_fraction": opt({"type": "number", "description": "an open-loop cc-grid run "
                           "with --vdd: the IR drop over the supply (EMIR-W520 above "
                           "about 0.02)"}),
    "via_cuts": obj({"from_area": I, "assumed_one_cut": arr(S)},
                    ["from_area", "assumed_one_cut"]),
    "unsolved": opt(obj({"pieces": I, "n_nodes": I, "nodes": arr(S)},
                        ["pieces", "n_nodes", "nodes"])),
    # --- feature bigblock ---
    "superposition": opt(obj({
        "method": {"enum": ["dense", "stream", "scalar", "combined"]}, "reason": S,
        "taps": I, "points": opt(I), "branches": I,
        "nodes": I, "dense_bytes": opt(I), "budget_bytes": opt(I), "chunk": opt(I),
        "chunks": opt(I), "working_bytes": opt(I)},
        ["method", "reason", "taps", "points", "branches", "dense_bytes", "budget_bytes"],
        description="How the tap currents were superposed through the grid, and why "
                    "(`reason`). Exact, from waveforms: `dense` (the transfer matrix, "
                    "branches x taps) or `stream` (time chunks, multi-column "
                    "back-substitution; when the matrix would exceed --max-memory, or "
                    "the result has fewer than twice as many time samples as taps): the "
                    "same answer either way. `scalar`: per-tap scalars (--scalars, a "
                    "tap file with rms and peak, a chip-level run with --align inphase): "
                    "avg exact, rms and peak in-phase upper bounds. `combined`: a "
                    "chip-level run with --align async or a phase sweep, per-block rows "
                    "combined as `reason` says. null for the DC and rcc routes.")),
    "dynamic": opt(obj({
        "worst_mV": NUM, "node": opt(S), "x_um": NUM, "y_um": NUM, "t_s": NUM,
        "source": opt(S), "n_steps": opt(I), "step_s": NUM, "peak_retention": NUM,
        "c_total_F": NUM, "decap_lumps": opt(I), "periodic": opt(B), "passes": opt(I),
        "settled": opt(B), "cap_note": opt(S), "decap_note": opt(S)},
        ["worst_mV", "node", "t_s"],
        description="The worst dynamic droop on the net and where: rcc from the "
                    "measured branch currents; cc-grid (--dynamic) from the CC tap "
                    "waveforms through the RC grid, open loop, or with "
                    "--self-consistent from the loop's reported pass (each pin fed its "
                    "droop waveform; `source` says which). null "
                    "when not computed (a notice says why).")),
    # --- end feature bigblock ---
}, ["net", "mode", "verdict", "power", "governing_metric", "max_ratio", "ir_drop_mV",
    "ir_kind", "worst_items", "n_violations", "no_width", "unsaved_pins", "via_cuts"])

NOT_CHECKED = obj({
    "unmapped_layers": arr(S),
    "limits_missing": arr(obj({"name": S, "kind": {"enum": ["wire", "via"]},
                               "metrics": arr(METRIC)}, ["name", "kind", "metrics"])),
    "estimated_limits": arr(S),
    "unsaved_pins": {"type": "object", "additionalProperties": arr(S)},
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
    "decap_straps_not_covered": {"type": "object", "additionalProperties": arr(obj(
        {"reason": {"enum": ["no-dynamic", "not-sized", "basis", "not-computed"]},
         "code": opt(CODE), "n": I, "what": S, "items": arr(S)},
        ["reason", "n", "what"])),
        "description": "Per net, the decaps whose straps the EM verdict does not cover: "
                       "no --dynamic (EMIR-W428), decaps cap_models cannot size (EMIR-W409, "
                       "W410), the decap-free basis (the segments that read higher with "
                       "their decaps, EMIR-W436), or decap-aware EM not computed."},
}, ["unmapped_layers", "limits_missing", "estimated_limits", "unsaved_pins",
    "vias_assumed_one_cut", "wires_without_width"])

#: one entry of a names sidecar's map: [DSPF name, sign]
NAME_ENTRY = {"type": "array", "minItems": 2, "maxItems": 2,
              "prefixItems": [{"type": "string", "minLength": 1}, {"type": "number"}]}

CURRENT_SOURCE = obj({"kind": {"enum": ["dc", "simulation", "netlist", "abstracts", "none"]},
                      "path": opt(S), "description": S,
                      "simulator": opt(S), "result": opt(S)},
                     ["kind", "path", "description"])

#: --- feature modes --- one route of `emir next`'s choose-mode step
ROUTE = obj({"route": S, "mode": {"enum": ["rcc", "cc-grid"]}, "self_consistent": B,
             "what": S, "cost": S, "accuracy": S, "source": S,
             "possible": B, "why_not": opt(S), "recommended": B, "command": opt(S),
             "tool": opt(obj({"name": S, "arguments": {"type": "object"}}, ["name"],
                             description="over MCP (`emir mcp`): the typed tool call that "
                                         "runs this route's command, once the person "
                                         "chose it"))},
            ["route", "mode", "self_consistent", "what", "cost", "accuracy", "possible",
             "recommended"])

PLAN_STEP = obj({"n": I, "id": S, "command": S, "why": S, "needs_human": B,
                 "licensed_tool": opt(S), "long_run": B,
                 "ask": {"type": "string", "description": "present when the step needs "
                         "something only the person can give or agree to (values typed "
                         "from a document): ask them first"},
                 "status": {"enum": ["todo", "waiting"]},
                 "after": arr(S), "placeholders": arr(S),
                 "choices": arr(ROUTE)},
                ["n", "id", "command", "why", "needs_human", "licensed_tool", "long_run",
                 "status", "after", "placeholders"],
                description="One step of the plan. The `choose-mode` step (no --mode given, "
                            "currents from a simulation) carries `choices`: the routes, "
                            "what each costs and how accurate it is (measured, with its "
                            "source), whether it can work here (`possible`, `why_not`) and "
                            "the one the net's size recommends. The person chooses; its "
                            "command is `emir next` again with <mode> replaced.")

#: the maintainer's research-project disclaimer (`emir.RESEARCH_DISCLAIMER`)
RESEARCH = {"const": ("EMIR is a research project, provided as is, with no warranty or "
                      "guarantee of accuracy. It is your responsibility to test and verify "
                      "its results for your process, tools and design before relying on "
                      "them.")}
SIM_NAME = {"enum": ["spectre", "hspice", "xyce", "ngspice"]}
#: the kinds of current a run reads (emir/verify_sim.py `KINDS`)
CURRENT_KIND = {"enum": ["branch", "mos", "two-terminal", "source", "subcircuit", "probe",
                         "diode", "bjt", "jfet", "other"]}
#: the brief of a record a run cites (emir/verify_sim.py `_brief`)
VERIFY_RECORD = obj({"simulator": SIM_NAME, "version": opt(S), "format": opt(S),
                     "date": opt(S), "passed": opt(B), "emir_version": opt(S),
                     "host": opt(S), "sim_args": opt(S), "covers": opt(arr(CURRENT_KIND))},
                    ["simulator", "passed"])
#: the run summary's `simulator_verification` (emir/verify_sim.py `status`)
SIM_VERIFICATION = obj({
    "status": {"enum": ["verified", "partly verified", "not verified", "unknown simulator",
                        "not applicable"]},
    "reason": opt({"enum": ["no record", "failed", "stale", "version unknown",
                            "other host", "format not covered", "pin kinds not covered",
                            "records unreadable"]}),
    "simulator": opt(S), "version": opt(S), "format": opt(S),
    "detected_from": opt({"enum": ["result header", "names sidecar", "--sim"]}),
    "sim_args": opt(S), "sim_args_known": B,
    "version_checked": B, "record": opt(VERIFY_RECORD), "records_file": S,
    "ignored_records": I, "records_problem": opt(S),
    "pin_kinds": opt(arr(obj({"kind": CURRENT_KIND, "what": S, "count": I,
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
                "covers kinds of "
                "current (`pin_kinds`, one entry per kind the run read: `covered` by the "
                "record, `verify_sim_covers` whether any verification of this emir could): "
                "a run whose kinds are not all covered is `partly verified` (or `not "
                "verified` when none is), `reason` `pin kinds not covered`, with EMIR-W464 "
                "naming them; diode, BJT and JFET terminals are never covered. `sim_args` "
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
     "host": opt(S), "passed": B, "covers": arr(CURRENT_KIND), "checks": arr(CHECK_ITEM)},
    ["schema", "simulator", "version", "format", "emir_version", "date", "passed", "checks"],
    description="One verification: what `emir verify-sim` appends to verified.json. A run "
                "counts only valid records (`passed` a JSON boolean that agrees with the "
                "checks, a passing record with its simulator's version); the latest one for "
                "a key (simulator, version, format, sim_args) decides; a record with a "
                "`host` counts only on that host. `covers` lists the kinds of current its "
                "checks prove (each backed by passing checks of that `kind`); a record "
                "without it covers no kind of pin.")


# --- feature hierarchy ---
#: block abstracts, the top-level run with blocks black-boxed, back-annotation
_RATIOS = {"type": "object", "additionalProperties": NUM}
_WORST = opt(obj({"name": S, "layer": S, "kind": {"enum": ["wire", "via"]}, "metric": METRIC,
                  "ratio": NUM}, ["name", "layer", "kind", "metric", "ratio"]))
_INTERNAL = obj({
    "verdict": VERDICT, "boundary": S, "max_ratio": _RATIOS, "worst": _WORST,
    "n_violations": I, "worst_items": arr(ITEM), "ir_drop_mV": NUM, "ir_worst_node": S,
    "dynamic": opt(obj({"worst_mV": NUM, "node": opt(S), "t_s": NUM}, ["worst_mV"])),
    "metric_note": opt(S), "unmapped": arr(S), "no_width": arr(S),
    "vias_assumed_one_cut": arr(S), "kcl_residual_A": NUM,
}, ["verdict", "boundary", "max_ratio", "worst", "n_violations", "worst_items",
    "ir_drop_mV", "dynamic"],
    description="The block's own EM/IR verdict with every port of the net held at one "
                "ideal potential (`boundary`).")
_BACK = obj({
    "source": S, "instance": S, "top_net": opt(S), "align": opt(S), "exact": B,
    "boundary": S, "verdict": VERDICT, "max_ratio": _RATIOS, "worst": _WORST,
    "n_violations": I, "worst_items": arr(ITEM), "metric_note": S,
    "ir_reference": {"enum": ["pad"]}, "ir_drop_mV": NUM,
    "ir_worst_node": S, "ir_drop_peak_mV": NUM,
    "budget_mV": opt(N), "budget_verdict": opt({"enum": ["PASS", "FAIL"]}),
    "pin_V_avg_mV": {"type": "object", "additionalProperties": NUM},
    "pass_through_A": {"type": "object", "additionalProperties": NUM},
    "short_circuit_max_ratio": _RATIOS,
}, ["source", "instance", "exact", "boundary", "verdict", "max_ratio", "worst",
    "worst_items", "ir_drop_mV", "pass_through_A"],
    description="The block re-checked with its top-level pin voltages (emir abstract "
                "--port-voltages): exact from a sync run's pin waveforms; otherwise avg "
                "exact and rms/peak upper bounds. `ir_drop_mV` is the droop from the top "
                "reference (the pad, `ir_reference`), not from the pins: the top-level drop "
                "is already in it, so compare it with the IR budget as it is (never add the "
                "top drop again). With the top run's --budget-mv, `budget_mV` and "
                "`budget_verdict` judge it.")
_SCALARS = {"i_avg": N, "i_rms": N, "i_peak": N}
_PORT_BASE = {"name": S, "x_um": N, "y_um": N,
              "layer": opt({"type": ["string", "integer", "number"]}), **_SCALARS,
              "uniform": obj(_SCALARS, ["i_avg", "i_rms", "i_peak"])}
_PORT_REQ = ["name", "x_um", "y_um", "layer", "i_avg", "i_rms", "i_peak"]
_PORT_FILE = obj({**_PORT_BASE, "wave": {"type": "array", "items": N, "minItems": 1}},
                 _PORT_REQ + ["wave"],
                 description="One pin of the block net: the current it pushes INTO the "
                             "top-level net (A; a load is negative). i_* are exact over "
                             "the simulation window; `wave` is one period (`n_samples` "
                             "uniform samples from `t_ref_s`). The port list is a tap "
                             "file for taps.load_taps.")
_NET_PROPS = {
    "net": S, "v_nominal": NUM, "period_s": opt(N), "aperiodic": B, "t_ref_s": N,
    "n_samples": I, "dt_s": N, "window": {"type": "object"}, "n_taps": I,
    "y_ports": opt(arr(arr(N))),
    "decap": opt(obj({"c_F": N, "r_ohm": N, "note": S}, ["c_F", "r_ohm"])),
    "internal": _INTERNAL, "back_annotation": opt(_BACK), "peak_retention": NUM,
    "kcl_A": NUM, "verdict": VERDICT,
    "sub_blocks": arr(obj({"instance": S, "block_net": S, "abstract": opt(S), "n_pins": I,
                           "verdict": VERDICT}, ["instance", "block_net", "verdict"],
                          description="A sub-block black-boxed in this block (emir abstract "
                                      "--abstract): its abstract drove its pins; its own "
                                      "verdict is in that abstract."))}
_NET_REQ = ["net", "period_s", "aperiodic", "t_ref_s", "n_samples", "dt_s", "ports",
            "y_ports", "decap", "internal", "back_annotation", "verdict"]
_PV_PORT = obj({"name": S, "node": S, "x_um": N, "y_um": N, "V_avg_V": NUM, "V_rms_V": NUM,
                "V_peak_V": NUM, "dV_avg_V": NUM, "dV_rms_V": NUM, "dV_peak_V": NUM,
                "wave_V": opt(arr(N))},
               ["name", "node", "V_avg_V", "V_rms_V", "V_peak_V", "dV_avg_V", "dV_rms_V",
                "dV_peak_V", "wave_V"])
_PV_NET = obj({
    "top_net": S, "align": {"enum": ["inphase", "sync", "async"]}, "exact": B,
    "phase_s": N, "t_ref_s": N, "period_s": N, "n_samples": I, "dt_s": N,
    "t_common": opt(obj({"dt_s": N, "n_samples": I, "period_s": N},
                        ["dt_s", "n_samples", "period_s"])),
    "ref_port": S, "ports": arr(_PV_PORT),
    "droop_mV": obj({"min": NUM, "max": NUM, "current_weighted": NUM}, ["min", "max"]),
    "v_nominal": NUM, "v_delivered_V": NUM,
}, ["top_net", "align", "exact", "phase_s", "t_ref_s", "period_s", "n_samples", "dt_s",
    "t_common", "ref_port", "ports", "droop_mV"])
_HIER_RUN = obj({
    "align": {"enum": ["inphase", "sync", "async"]},
    "phases": {"type": "object", "additionalProperties": N},
    "phase_sweep": {"type": "object", "additionalProperties": I},
    "place": {"type": "object"},
    "blocks": arr(obj({"instance": S, "block": opt(S), "block_net": S, "abstract": opt(S),
                       "n_pins": I, "verdict": VERDICT, "internal_verdict": VERDICT,
                       "boundary": S, "back_annotated": B, "needs_back_annotation": B,
                       "internal_ir_mV": NUM},
                      ["instance", "block_net", "n_pins", "verdict", "back_annotated",
                       "needs_back_annotation"])),
    "nets": arr(obj({
        "net": S, "feed": S, "pads": I, "align": S,
        "pins": arr(obj({"instance": S, "block_net": S, "port": S, "node": S, "x_um": N,
                         "y_um": N, "V_avg_mV": NUM, "V_rms_mV": NUM, "V_peak_mV": NUM,
                         "i_avg_A": NUM, "i_rms_A": NUM, "i_peak_A": NUM},
                        ["instance", "block_net", "port", "node", "V_avg_mV", "V_peak_mV",
                         "i_avg_A"])),
        "pass_through": arr(obj({"instance": S, "block_net": S, "between": arr(S),
                                 "nodes": arr(S), "R_ohm": NUM, "I_avg_A": NUM,
                                 "I_rms_A": NUM, "I_peak_A": NUM},
                                ["instance", "block_net", "between", "R_ohm", "I_avg_A"])),
        "sweep": opt({"type": "object"}), "t_common": opt({"type": "object"}),
    }, ["net", "feed", "pins", "pass_through"])),
    "budget": opt(obj({
        "budget_mV": N, "verdict": {"enum": ["PASS", "FAIL"]},
        "rows": arr(obj({"instance": S, "block_net": S, "top_net": S, "n_pins": I,
                         "top_mV": NUM, "internal_mV": NUM, "total_mV": NUM,
                         "budget_mV": N, "verdict": {"enum": ["PASS", "FAIL"]},
                         "exact": B, "top_peak_mV": NUM, "internal_dynamic_mV": NUM},
                        ["instance", "block_net", "top_mV", "internal_mV", "total_mV",
                         "budget_mV", "verdict", "exact"]))},
        ["budget_mV", "verdict", "rows"])),
    "port_voltages": opt(S), "notes": arr(S),
}, ["align", "blocks", "nets", "budget", "port_voltages", "notes"],
    description="A run with --abstract: the blocks, the voltage at every block pin, the "
                "pass-through inside blocks fed at several pins, the IR budget per block "
                "(exact for one pin, an upper bound for several) and the port-voltages "
                "file for back-annotation.")


def _hierarchy(out: dict):
    """The hierarchy flow's documents; `run` and `report` gain `hierarchy`."""
    out["run"]["properties"]["hierarchy"] = _HIER_RUN
    out["report"]["properties"]["hierarchy"] = _HIER_RUN
    out["abstract"] = document(
        "abstract", "emir abstract (the block abstract file)",
        "A block characterised once for a chip-level run (`emir abstract -o FILE`; read by "
        "`emir run TOP --abstract FILE@INST`): per block net, the current each pin pushes "
        "into the top-level net (one period, uniform samples, and the exact window "
        "scalars), the Norton conductance between the pins (`y_ports`, S), the decap lump, "
        "the block's own verdict with its pins held at one potential, and, after "
        "back-annotation, its verdict with the top's pin voltages. Screening, not sign-off.",
        {"disclaimer": {"const": "Screening, not sign-off."},
         "research_disclaimer": RESEARCH, "verdict": VERDICT,
         "block": opt(S), "dspf": S, "sim": S, "process": opt(S), "temp_C": N,
         "em_tref_C": opt(N), "created": S, "inputs": {"type": "object"},
         "command": S, "notices": arr(NOTICE),
         "nets": {"type": "object", "minProperties": 1,
                  "additionalProperties": obj({**_NET_PROPS, "ports": arr(_PORT_FILE)},
                                              _NET_REQ)}},
        ["disclaimer", "verdict", "dspf", "sim", "temp_C", "nets"])
    out["abstract_summary"] = document(
        "abstract_summary", "emir abstract (what --format json prints)",
        "What `emir abstract --format json` prints: the abstract written to `out` without "
        "its waveforms, the `--abstract` argument that uses it (`use`, INSTANCE to fill "
        "in), the notices and the next steps. Exit 1 with --fail-on-violation when "
        "`verdict` is FAIL.",
        {"disclaimer": {"const": "Screening, not sign-off."},
         "research_disclaimer": RESEARCH, "verdict": VERDICT, "out": S,
         "use": S, "block": opt(S), "dspf": S, "sim": S, "process": opt(S), "temp_C": N,
         "nets": arr(obj({**_NET_PROPS, "ports": arr(obj(_PORT_BASE, _PORT_REQ))},
                         _NET_REQ)),
         "notices": arr(NOTICE), "next_steps": NEXT_STEPS},
        ["disclaimer", "verdict", "out", "use", "dspf", "nets", "notices", "next_steps"])
    out["port_voltages"] = document(
        "port_voltages", "emir run --port-voltages-out (the top-level pin voltages)",
        "The voltage at every block pin of a hierarchical run (V, from the top's reference, "
        "the pad), and between the pins of one block net (`dV_*`, from `ref_port`), for "
        "`emir abstract --port-voltages FILE@INST`. From a sync run without a phase sweep "
        "`wave_V` holds the waveform on the common grid (`t_common`) and back-annotation is "
        "exact; otherwise rms and peak are bounds (inphase), long-time values (async) or "
        "the worst over the swept phases. `v_delivered_V` is the supply to re-simulate the "
        "block at (the least-drooped pin) in the top-level self-consistency loop.",
        {"created": S, "top_dspf": S, "process": opt(S),
         "align": {"enum": ["inphase", "sync", "async"]}, "budget_mV": opt(N),
         "instances": {"type": "object", "additionalProperties": obj(
             {"abstract": opt(S), "block": opt(S),
              "nets": {"type": "object", "additionalProperties": _PV_NET}}, ["nets"])}},
        ["top_dspf", "align", "instances"])
# --- feature hierarchy ---


# --- feature decap --- decap-aware EM (`emir/decap.py`): the EM verdict's
# basis, every basis's numbers, and the person's choice of basis
_BASIS = {"enum": list(BASES)}
_BASIS_USED = {"enum": [*BASES, "measured"]}
_BASIS_BY = {"type": "object", "properties": {b: NUM for b in _BASIS["enum"]}}
#: where the basis asked for comes from: the default (with --dynamic), the
#: person's --em-basis, or a run with neither --dynamic nor --em-basis over
#: decap devices (EMIR-W428: decap-free because there is no dynamic solve)
_BASIS_SOURCE = {"enum": ["default", "option", "no-dynamic"]}
EM_BASIS_NET = obj({
    "requested": _BASIS, "used": _BASIS_USED, "source": _BASIS_SOURCE,
    "fallback": opt({"enum": ["no-dynamic", "dc", "no-capacitance", "harmonic", "pieces",
                              "no-feed", "reference-not-feed", "not-settled",
                              "window-not-periodic", "step-too-coarse"]}),
    "fallback_reason": opt(S), "dyn_steps": opt(I),
    "decaps_not_sized": obj({"n": I, "models": arr(S)}, ["n", "models"])},
    ["requested", "used", "source", "fallback"],
    description="The EM verdict's basis on this net: `requested` (--em-basis, or the "
                f"default {DEFAULT_BASIS}), `used` (the basis the net's verdict, max_ratio, worst, "
                "worst_items and governing_metric are on; `measured` in rcc, where the "
                "decaps' currents are simulated), `source` (default or option: a basis "
                "other than the default is the person's choice, EMIR-W437; no-dynamic: "
                "decap-free because the run has neither --dynamic nor --em-basis, "
                "EMIR-W428) and, when "
                "decap-aware EM was not computed, `fallback` (why, machine-readable; the "
                "verdict is then on decap-free) and `fallback_reason` (in words). "
                "`decaps_not_sized`: decap devices cap_models cannot size (EMIR-W409, "
                "W410), left out of the decap-aware solve: their straps are not checked, "
                "and a basis that needs decap-aware EM is not honoured on the net.")
_EM_BASIS_RESULT = obj({
    "verdict": VERDICT, "max_ratio": {"type": "object", "additionalProperties": NUM},
    "governing_metric": opt(METRIC), "worst": opt({"type": "object"}),
    "n_violations": I}, ["verdict", "max_ratio", "governing_metric", "n_violations"])
DYNAMIC_EM = opt(obj({
    "bases": obj({b: _EM_BASIS_RESULT for b in _BASIS["enum"]}, list(_BASIS["enum"])),
    "higher_aware": obj({"n": I, "worst": opt({"type": "object"})}, ["n", "worst"]),
    "feed": obj({"node": S, "i_avg_A": NUM, "i_rms_A": _BASIS_BY, "i_peak_A": _BASIS_BY},
                ["node", "i_rms_A", "i_peak_A"]),
    "decaps": obj({"n": I, "c_total_F": NUM, "folded_F": NUM,
                   "top": arr(obj({"name": S, "kind": {"enum": ["mos", "device"]},
                                   "node": S, "c_F": NUM, "r_ohm": NUM, "r_model": opt(S),
                                   "folded": B, "half": {"enum": ["d", "s"]},
                                   "i_avg_A": NUM, "i_rms_A": NUM, "i_peak_A": NUM},
                                  ["name", "kind", "node", "c_F", "folded", "i_rms_A",
                                   "i_peak_A"]))}, ["n", "c_total_F", "top"]),
    "capacitance": {"type": "object"},
    "items": arr({"type": "object"}),
    "numerics": obj({"n_steps": I, "step_s": NUM, "passes": I, "settled": B,
                     "avg_check": obj({"max_rel": NUM, "tolerance": N, "passed": B},
                                      ["max_rel", "tolerance", "passed"]),
                     "correction_max": {"type": "object"},
                     "sub_steps": {"type": "object"}},
                    ["n_steps", "step_s", "passes", "settled", "avg_check"])},
    ["bases", "feed", "decaps", "capacitance", "numerics"],
    description="--dynamic in cc-grid: the net's EM from the dynamic solve. `bases`: "
                "per basis (decap-free, the static superposition; decap-aware, rms and "
                "peak from the dynamic solve with the decaps, avg the static one; worst, "
                "per segment the larger) its verdict, worst ratios, governing metric, "
                "worst segment and violation count. `higher_aware`: segments at or "
                "above the warn fraction that read higher with their decaps than "
                "without (what decap-free does not cover). `feed`: the current the feed "
                "delivers in both bases. `decaps`: the decaps' own currents (devices, "
                "not checked against a metal limit; at most 20). `capacitance`: what "
                "the solve counted, and the coupling to signal nets it left out. "
                "`items`: the segments of every basis with each basis's own ratio. "
                "`numerics`: the step, the passes, the periodic-average check, the step "
                "correction of rms, the sub-steps of the waveforms' fast edges. null when "
                "not computed "
                "(`em_basis.fallback` says why)."))
#: one choice of a `needs_human` step (the person decides): a route of
#: `choose-mode` (feature modes: `ROUTE`, its fields and its MCP `tool` call)
#: or an EM basis of `choose-em-basis`, each with its own required fields
CHOICE = obj({**ROUTE["properties"], "option": S, "basis": _BASIS, "verdict": VERDICT,
              "max_ratio": NUM, "n_violations": I, "in_use": B},
             ["route", "what", "possible", "recommended"],
             anyOf=[{"required": ["mode", "self_consistent", "cost", "accuracy"]},
                    {"required": ["basis", "verdict", "max_ratio", "n_violations"]}],
             description="A route of `choose-mode` (mode, self_consistent, cost, accuracy, "
                         "and over MCP the `tool` call of its command) or an EM basis of "
                         "`choose-em-basis` (basis, its verdict, worst ratio and "
                         "violations).")
#: a run's (and report's) worst item, with its ratio in every EM basis
RUN_ITEM = {**ITEM, "properties": {
    **ITEM["properties"],
    "ratio_decap_free": NUM, "ratio_decap_aware": NUM, "ratio_worst": NUM,
    "metric_decap_free": opt(METRIC), "metric_decap_aware": opt(METRIC),
    "metric_worst": opt(METRIC), "decided_by": {"enum": ["decap-free", "decap-aware"]}}}
RUN_NET["properties"].update({"em_basis": EM_BASIS_NET, "dynamic_em": DYNAMIC_EM,
                              "worst_items": arr(RUN_ITEM)})
#: a net with decap-aware EM always says which basis its verdict is on (the
#: draft-7 form of `dependentRequired: {dynamic_em: [em_basis]}`)
RUN_NET["anyOf"] = [{"not": {"required": ["dynamic_em"]}}, {"required": ["em_basis"]}]
PLAN_STEP["properties"]["choices"] = arr(CHOICE)


def _decap(out: dict):
    """`run`, `report` and `next` gain the EM basis (additive: /1 stays)."""
    top = obj({"requested": _BASIS, "source": _BASIS_SOURCE,
               "default": _BASIS, "used": arr(_BASIS_USED), "honoured": B,
               "not_computed": arr(obj({"net": S, "fallback": S, "fallback_reason": opt(S),
                                        "dyn_steps": opt(I), "models": arr(S)},
                                       ["net", "fallback"]))},
              ["requested", "source"],
              description="--dynamic or --em-basis (or a net with decap devices on a run "
                          "without --dynamic: requested decap-free, EMIR-W428): the basis "
                          f"asked for (with --dynamic the default is {DEFAULT_BASIS}); `used`, the "
                          "bases the nets' verdicts are on (each net's `em_basis.used`); "
                          "`honoured` false when a net could not have the basis asked for "
                          "(`not_computed`: which and why; its verdict is then decap-free, "
                          "or, for decaps-not-sized, the basis without the decaps "
                          "cap_models cannot size (`models`); --fail-on-violation exits 1)")
    out["run"]["properties"]["em_basis"] = top
    out["run"]["properties"]["argv"] = arr(S)
    out["run"]["properties"]["cwd"] = {
        "type": "string", "description": "the directory the run ran in: the relative paths "
        "of `argv` are relative to it (`emir next --run-summary` resolves them, so its "
        "re-run commands work from any directory)"}
    rp = out["report"]["properties"]
    rp["worst_items"] = arr(RUN_ITEM)
    rp["em_basis"] = top
    rp["em_bases"] = {"type": "object", "additionalProperties": obj(
        {"em_basis": EM_BASIS_NET, "computed": B, "bases": {"type": "object"},
         "feed": {"type": "object"}, "disagree": B}, ["em_basis", "computed"])}
    rp["em_choices"] = arr(CHOICE)
    rp["decap_items"] = arr({"type": "object"})
    # the cap models the decap-aware solve can use (EMIR-E317, decap.cap_model_problems,
    # whose CAP_NONNEG and CAP_RANGES these are: tests/test_merge_interplay.py):
    # capacitances and access resistances non-negative (an entry whose
    # capacitances are all 0 is refused by the check), esr_w and esr_l signed
    cap = out["process"]["properties"]["cap_models"]["additionalProperties"]["properties"]
    for k in ("cox", "cj", "cjsw", "esr_a", "ron_sq"):
        cap[k] = {**cap[k], "minimum": 0}
    cap["esr_fit_freq"] = {**cap["esr_fit_freq"], "exclusiveMinimum": 0}
    for k in ("esr_w_range", "esr_l_range"):
        cap[k] = {**cap[k], "items": {"type": "number", "minimum": 0},
                  "minItems": 2, "maxItems": 2}
# --- end feature decap ---


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
                                                "date": opt(S)}))},
                          ["name", "path", "found", "source", "licensed"])),
         "records_file": S,
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
         "split": arr(S)},
        ["dspf", "net", "pins", "split"])
    out["save_lines"] = document(
        "save_lines", "emir save-lines",
        "The simulator save lines a run needs (also what `emir run --save-list`, "
        "`--save-branches` and `--split-stub` print in JSON mode): Spectre `save`, HSPICE "
        "`.probe` (with `.option probe post=2 post_version=2001`), Xyce `.print`, ngspice "
        "`.save` (`simulator`). For HSPICE, Xyce and ngspice, `names` is the map of the "
        "`<result>.names` sidecar (`emir schema names`), written to each path in "
        "`sidecars`, and `raw` the result the lines write.",
        {"dspf": S, "net": opt(S), "mode": {"enum": ["rcc", "cc-grid", "split-stub"]},
         "prefix": S, "simulator": SIM_NAME,
         "lines": arr(S), "count": opt(I), "sidecars": arr(S), "raw": opt(S),
         "names": {"type": "object", "additionalProperties": NAME_ENTRY},
         "nets": arr(S), "direct": I, "probed": I,
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
        {"source": S, "verdict": VERDICT, "disclaimer": {"const": "Screening, not sign-off."},
         "research_disclaimer": RESEARCH,
         "simulator_verification": opt(SIM_VERIFICATION),
         "modes": arr(S), "current_source": CURRENT_SOURCE, "temp_C": opt(N),
         "command": opt(S), "created": opt(S),
         "nets": arr(obj({"net": S, "verdict": VERDICT, "mode": S,
                          "governing_metric": opt(METRIC), "worst_ratio": NUM,
                          "ir_drop_mV": NUM, "ir_kind": S, "power": B},
                         ["net", "verdict", "mode", "governing_metric", "worst_ratio"])),
         "worst_items": arr(ITEM),
         "ir_by_power_net": arr(obj({"net": S, "ir_drop_mV": NUM, "ir_kind": S,
                                     # --- feature bigblock ---
                                     "dynamic_mV": NUM, "dynamic": opt({"type": "object"})},
                                    ["net", "ir_drop_mV"])),
         "not_checked": arr(S), "input_warnings": arr(S), "run_warnings": arr(S),
         # --- feature modes --- per net, the self-consistent loop's passes
         "self_consistency": {"type": "object", "additionalProperties": SELF_CONSISTENCY},
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
        {"inputs": {"type": "object"}, "facts": {"type": "object"},
         "tools": {"type": "object"},
         "mode": obj({"chosen": opt(S), "why": S}, ["chosen", "why"]),
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
    out["probe"] = document(
        "probe", "emir probe",
        "A netlist rewritten with a 0 V source in series with each device terminal on "
        "the nets (`probes`; `count` of them), and the files written (`files`: the "
        "output and copies of included files that got probes). `names_out` is the names "
        "sidecar (with its `probes` list) for `emir save-lines --probes` and `emir "
        "simulate --probes`. `uncovered`: DSPF instance pins with no probe; "
        "`unmatched`: probes on pins the DSPF does not list; `guessed`: subcircuits "
        "whose pins were named by position.",
        {"netlist": S, "out": S, "files": files, "nets": arr(S), "scope": opt(S),
         "path": arr(S), "why": S, "count": I,
         "probes": arr(obj({"source": S, "pin": S, "net": S, "element": S, "terminal": S,
                            "matched": B, "file": S, "line": I},
                           ["source", "pin", "net", "element", "terminal", "matched"])),
         "unmatched": arr(S), "uncovered": arr(S), "guessed": arr(S),
         "names_out": opt(S), "has_tran": B, "notices": arr(NOTICE),
         "next_steps": NEXT_STEPS},
        ["netlist", "out", "files", "nets", "count", "probes", "unmatched", "uncovered",
         "names_out", "next_steps"])
    out["simulate"] = document(
        "simulate", "emir simulate",
        "One simulation for the DSPF flow: the deck written (`deck`, `files`), the text "
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
         "mode": {"enum": ["rcc", "cc-grid"]}, "mode_reason": S, "nets": arr(S),
         "scope": opt(S), "path": arr(S), "where": S, "signals": I, "direct": I,
         "probed": I, "found": opt(I), "missing": arr(S), "seconds": opt(N),
         "points": opt(I), "analysis": opt(S), "notes": arr(S),
         # --- feature modes --- `emir run --self-consistent` on a licensed
         # simulator without --run prints this document: the loop it would run
         "self_consistent": opt(obj({"net": S, "vdd": opt(N), "max_passes": I,
                                     "tol_mV": N, "tol_rel": opt(N), "droop_file": S,
                                     "pins": I, "nets": arr(S)},
                                    ["net", "vdd", "max_passes", "tol_mV", "droop_file"])),
         "next_steps": NEXT_STEPS},
        ["simulator", "ran", "command", "testbench", "deck", "result", "names_sidecar",
         "mode", "nets", "signals", "found", "missing", "next_steps"])
    out["verify_sim"] = document(
        "verify_sim", "emir verify-sim",
        "The on-site verification of one simulator's route into EMIR: six simulations "
        "of testbenches with hand-checkable answers (`cases`: ideal loads dc and pulsed; "
        "`dev`, real devices whose terminal currents are known, read with the forms the "
        "cc route uses), read back, screened in rcc and cc-grid and compared with the "
        "analytic answers and between the modes (`checks`). `passed` true exits 0, false exits 70 (`code` EMIR-E663); a "
        "licensed simulator without --run is only planned (`ran` false, `passed` null). "
        "A finished verification is kept as a record in `records_file` "
        "($EMIR_STATE_DIR or ~/.emir, verified.json): `version` is what the simulator "
        "printed for `version_command`; `host` only with --record-host. `covers` lists "
        "the kinds of current its checks prove (each check's `kind`): a run is verified "
        "only for those (diode, BJT and JFET terminals never are).",
        {"simulator": SIM_NAME, "ran": B, "passed": opt(B), "version": opt(S),
         "sim_args": opt(S),
         "version_command": arr(S), "format": S, "date": opt(S), "host": opt(S),
         "out_dir": S, "covers": arr(CURRENT_KIND),
         "cases": arr(obj({"case": {"enum": ["dc", "pulse", "dev", "selfconsistent"]},
                           "mode": {"enum": ["rcc", "cc-grid"]},
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
                       "`<result>.names` (cc.raw -> cc.raw.names). It maps a "
                       "simulator's signal names to the DSPF's: a terminal current "
                       "`inst:pin` (cc-grid) or a branch current `R:1` (rcc), with "
                       "the sign that turns the saved current into the current INTO "
                       "the instance pin (or from the resistor's first node to its "
                       "second): 1 or -1. Xyce and ngspice name currents I(V1), "
                       "i(v_mp0), V1#branch ...; keys are matched without regard to "
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
            # written by `emir probe --names-out` (and read by --probes)
            "probes": arr(obj({"source": S, "path": arr(S), "pin": S, "sign": N, "net": S,
                               "element": S, "terminal": S}, ["source", "path", "pin"])),
            "generator": S, "netlist": S, "probed_netlist": S,
            # which simulator the lines were written for (and its version,
            # when emir ran it): the on-site verification is looked up by it
            "simulator": SIM_NAME, "simulator_version": S,
            # the --sim-args emir simulate passed ("" for none), normalised
            "sim_args": S,
            # HSPICE: a terminal current whose sign is set from KCL when the
            # result is read (a MOSFET's i3): its device's other terminals
            # how the route reads each DSPF name (emir/verify_sim.py KINDS): what
            # the on-site verification's records are matched against
            "kinds": {"type": "object", "additionalProperties": CURRENT_KIND},
            "kcl": {"type": "object", "additionalProperties": obj(
                {"device": S, "terms": arr(S)}, ["terms"])},
        },
        "required": ["map"],
        "examples": [{"map": {"I(V_MP0)": ["MP0:s", 1], "I(V_MP1)": ["MP1:s", 1]}}],
    }
    _hierarchy(out)                                   # --- feature hierarchy ---
    _decap(out)                                       # --- feature decap ---
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
    magic_required = ["process", "cc_netlist", "rc_netlist", "magic", "magicrc", "files",
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
        "wrote `dspf` (also the RC netlist an rcc simulation runs, `rc_netlist`) and the "
        "CC netlist a cc-grid simulation runs (`cc_netlist`); `unchecked_layers`: magic's "
        "conductors the process file gives no limit (their current is in the IR drop); "
        "`work_dir`: where magic ran (`magic/` under `out_dir`, a directory of emir's "
        "own); `floating_capacitance`: capacitor nodes on no net of the network "
        "(EMIR-W646); `diagonal_resistors`: the DSPF's resistors on diagonal shapes, "
        "whose width is magic's tile extent (EMIR-W647; `emir run` lists them as not "
        "checked).",
        {"tool": {"enum": ["quantus", "magic"]}, "dry_run": B, "ready": B, "cell": S,
         "source_cell": S,
         "kind": {"enum": ["r_only", "c_only_coupled", "rc_coupled", "rc_decoupled",
                           "c_only_decoupled"]},
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
         "process": S, "cc_netlist": S, "rc_netlist": S,
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
    cap = obj({"cj": num, "cjsw": num, "cox": num, "ron_sq": num, "esr_a": num, "esr_w": num,
               "esr_l": num, "esr_w_range": arr(N), "esr_l_range": arr(N),
               "esr_fit_freq": num, "sources": sources, "provenance": entry_prov}, extra=False)
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
            "cap_models": {"type": "object", "additionalProperties": cap},
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
        "name": S, "kind": {"enum": ["wire", "via", "cap"]}, "provenance": opt(_PROV),
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
                "its width loss (and, on a --dynamic run, the cap models: kind `cap`); the "
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
        "... check` note, or null), the cap models, and what is still empty. It never fills "
        "in a value. `next_steps`: the person types the values with their sources, then "
        "`emir process check`.",
        {"out": S, "dspf": opt(S), "design": opt(S), "pdk": opt(S),
         "provenance": {"const": "hand-entered"},
         "layers": arr(obj({"name": S, "note": S}, ["name"])),
         "vias": arr(obj({"name": S, "below": opt(S), "above": opt(S), "stacking": opt(S)},
                         ["name", "below", "above", "stacking"])),
         "cap_models": arr(S), "dspf_layers": {"type": "object", "additionalProperties": S},
         "empty": _EMPTY, "interactive": B, "values_given": I, "next_steps": NEXT_STEPS},
        ["out", "layers", "vias", "cap_models", "empty", "interactive", "values_given",
         "next_steps"])
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
