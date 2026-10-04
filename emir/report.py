"""`emir report`: the summary of a run that an agent relays to a person.

    emir report --from emir_out/summary.json            # Markdown (default)
    emir run ... --format json | emir report --from -   # from stdin
    emir report --from summary.json --format json       # emir.report/1

It reads a run summary (`emir.run/1`, written by `emir run --summary-out` or
printed by `emir run --format json`) and says, in this order: the verdict and
the "Screening, not sign-off." line; the mode and where the currents came
from; the analysis temperature; per net the verdict, governing metric, worst
ratio and IR; the worst items with their fix in engineer's units (a wider
wire, more cuts -- never a "fix" below 1.00x, which would narrow a wire);
the IR of each power net; what was NOT checked (layers or vias without
limits, unsaved pins, vias counted as one cut, wires without a width, parts
of a net in pieces, what DC currents cannot show); the input checker's
warnings; and the warnings the run itself raised. Every number comes from
the summary: the report computes nothing new, so it cannot disagree with the
run. A number the run could not compute (null in the summary) is reported
as "not a number", never as zero.

The header names the run (its command and when it finished), so a summary
left over from an earlier run cannot pass for a new one; a run that failed
leaves its `emir.error/1` document at --summary-out, which this command
refuses (EMIR-E104) with that run's error.
"""
from __future__ import annotations

import json
import os
import sys

from . import RESEARCH_DISCLAIMER, SCREENING_NOTE
from .errors import EmirError
from .process_file import limits_lines

#: how many worst items the report lists, over every net
TOP = 10


def load_summary(src: str) -> dict:
    """The run summary at `src` (`-` reads stdin); EMIR-E104 if it is not one,
    EMIR-E103 if `-` names a terminal (nothing was piped in)."""
    from .errors import step
    from .output import command_line
    if src == "-" and _isatty(sys.stdin):
        raise EmirError("EMIR-E103", "--from - reads the run summary from stdin, which is "
                        "a terminal: nothing was piped in. Pipe the document in "
                        "(`emir run ... --format json | emir report --from -`) or give "
                        "the file `emir run --summary-out` wrote",
                        hint="Pipe the run summary in, or give its file.",
                        next_steps=[step("the run summary file the run wrote (the default "
                                         "path `emir next` plans)",
                                         command_line("report", "--from",
                                                      "emir_out/summary.json"))])
    try:
        if src == "-":
            doc = json.load(sys.stdin)
        else:
            with open(src, encoding="utf-8") as f:
                doc = json.load(f)
    except json.JSONDecodeError as e:
        raise EmirError("EMIR-E104", f"{src} is not JSON ({e}); give the file "
                        f"`emir run --summary-out` wrote", schema="run") from e
    if isinstance(doc, dict) and doc.get("schema") == "emir.error/1":
        # what a failed run leaves at --summary-out (see cli._cmd_run)
        steps = doc.get("next_steps")
        raise EmirError("EMIR-E104", f"{src} holds the error of a run that did not "
                        f"finish ({doc.get('code')}: {doc.get('message')}), not a run "
                        f"summary; fix that and re-run", schema="run",
                        hint="The run that wrote this file failed: fix its error (the "
                             "next step is that run's own) and run it again.",
                        next_steps=steps if isinstance(steps, list) else None,
                        details={"run_error": {k: doc.get(k) for k in
                                               ("code", "message", "exit_code")}})
    if not isinstance(doc, dict) or doc.get("schema") != "emir.run/1":
        got = doc.get("schema") if isinstance(doc, dict) else type(doc).__name__
        raise EmirError("EMIR-E104", f"{src} is not a run summary (schema "
                        f"{got!r}, expected 'emir.run/1'); give the file `emir run "
                        f"--summary-out` wrote", schema="run")
    return doc


def _isatty(f) -> bool:
    try:
        return f.isatty()
    except (AttributeError, ValueError):
        return False


def _num(x):
    """A summary number as a float; None (the summary's null: NaN or Inf in
    the run) stays None -- it is never read as zero."""
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _ratio(n):
    mr = n.get("max_ratio")
    if isinstance(mr, dict):
        return _num(mr.get("all", 0.0))
    return _num(mr if mr is not None else None)


NAN_TEXT = "not a number"


def _x(v, unit="x") -> str:
    """`1.234x`, or "not a number" for a null."""
    return NAN_TEXT if v is None else f"{v:.3f}{unit}"


def not_checked_lines(s: dict) -> list:
    """What the verdict does not cover, one sentence each."""
    nc = s.get("not_checked") or {}
    out = []
    if nc.get("unmapped_layers"):
        out.append(f"DSPF layers with no process-file entry, not checked: "
                   f"{', '.join(nc['unmapped_layers'])}")
    for e in nc.get("limits_missing") or []:
        what = "via" if e["kind"] == "via" else "layer"
        out.append(f"{what} {e['name']}: no {', '.join(e['metrics'])} limit in the "
                   f"process file, so {'that metric is' if len(e['metrics']) == 1 else 'those metrics are'} "
                   f"not checked on it")
    if nc.get("estimated_limits"):
        out.append(f"limits flagged as estimates in the process file: "
                   f"{', '.join(nc['estimated_limits'])}")
    for net, pins in sorted((nc.get("unsaved_pins") or {}).items()):
        out.append(f"{net}: {len(pins)} instance pin(s) not saved by the simulation, "
                   f"their current taken as zero: {_few(pins)}")
    for net, vias in sorted((nc.get("vias_assumed_one_cut") or {}).items()):
        out.append(f"{net}: {len(vias)} via(s) with no cut count, checked as ONE cut "
                   f"(conservative): {_few(vias)}")
    for net, wires in sorted((nc.get("wires_without_width") or {}).items()):
        out.append(f"{net}: {len(wires)} wire(s) with no width, NOT checked: {_few(wires)}")
    for net, wires in sorted((nc.get("diagonal_wires") or {}).items()):
        out.append(f"{net}: {len(wires)} wire(s) on diagonal shapes, checked at the width "
                   f"magic gives (its tiles' extent, not the width across the current): "
                   f"their current density can read low, NOT conservative (EMIR-W647): "
                   f"{_few(wires)}")
    for net, u in sorted((nc.get("unsolved") or {}).items()):
        out.append(f"{net}: the net is in pieces; {u.get('n_nodes')} node(s) are not "
                   f"connected to the reference, so the IR covers the reference's piece "
                   f"only and branch currents in a piece with current but no path to "
                   f"the reference are not solved (a FAIL): {_few(u.get('nodes') or [])}")
    gap = nc.get("avg_not_derated")
    if gap:
        out.append(f"temperature: {gap['what']}, so the avg check is NOT conservative at "
                   f"this temperature (give the PDK's derating as jmax_factor)")
    for net, rows in sorted((nc.get("bidirectional") or {}).items()):
        out.append(f"{net}: {len(rows)} branch(es) carry a reversing current whose signed "
                   f"average cancels (|avg| below, but the average of |I| at or above the "
                   f"warn fraction of the avg limit: "
                   + ", ".join(f"{x['name']} {_x(_num(x.get('avg_ratio')))} vs "
                               f"{_x(_num(x.get('abs_avg_ratio')))}" for x in rows[:4])
                   + "); EMIR applies no bidirectional (recovery) rule: check them "
                     "against the PDK's AC rule")
    for net, rows in sorted((nc.get("decap_straps_not_covered") or {}).items()):
        for x in rows:
            out.append(f"{net}: {x['what']}" + (f" ({x['code']})" if x.get("code") else ""))
    for net, rows in sorted((nc.get("via_crowding") or {}).items()):
        out.append(f"{net}: {len(rows)} via array(s) within 10 % of the limit, checked at "
                   f"the array's mean current per cut; the cuts facing the current carry "
                   f"more: "
                   + ", ".join(f"{x['name']} {_x(_num(x.get('ratio')))}" for x in rows[:4])
                   + " -- confirm cut by cut (the extractor's per-cut vias)")
    if (s.get("current_source") or {}).get("kind") == "dc":
        out.append("DC currents (--currents): avg = rms = peak by construction, so "
                   "switching transients and their peaks are not covered; a simulation "
                   "result (--cc-sim) screens them")
    ic = s.get("input_check") or {}
    if not ic.get("ran", True):
        out.append("the input check was skipped (--no-check)")
    if ic.get("forced"):
        out.append(f"the run was forced past {len(ic.get('fails') or [])} input-check "
                   f"FAIL(s): " + "; ".join(f"[{f['code']}] {f['message']}"
                                           for f in ic.get("fails") or []))
    return out


def _few(names, n=6) -> str:
    names = list(names)
    return ", ".join(names[:n]) + (f" (+{len(names) - n} more)" if len(names) > n else "")


def build(s: dict, source: str, top: int = TOP) -> dict:
    """The `emir.report/1` document (without its `markdown`)."""
    from .decap import report_fields as decap_fields
    nets = []
    for n in s.get("nets") or []:
        mr = n.get("max_ratio") if isinstance(n.get("max_ratio"), dict) else {}
        nets.append({"net": n["net"], "verdict": n["verdict"], "mode": n["mode"],
                     "governing_metric": n.get("governing_metric"),
                     "worst_ratio": _ratio(n), "ir_drop_mV": _num(n.get("ir_drop_mV")),
                     # every layer's worst, failing or not: a passing layer's
                     # margin is the run's number, never one worked out by hand
                     "worst_wire": _num(mr.get("wire")), "worst_via": _num(mr.get("via")),
                     "ir_kind": n.get("ir_kind", "drop"), "power": bool(n.get("power"))})
    items = [it for n in s.get("nets") or [] for it in n.get("worst_items") or []]
    # a null ratio (not a number) is a failure, so it sorts first
    items.sort(key=lambda it: -(it["ratio"] if it.get("ratio") is not None
                                else float("inf")))
    ir = [{"net": n["net"], "ir_drop_mV": n["ir_drop_mV"], "ir_kind": n["ir_kind"]}
          for n in nets if n["power"]]
    # the dynamic droop beside the average one, where the run computed it
    # (rcc, or cc-grid --dynamic)
    dyn = {n["net"]: n for n in s.get("nets") or []}
    for x in ir:
        n = dyn.get(x["net"]) or {}
        if n.get("dynamic") or n.get("dynamic_mV") is not None:
            x["dynamic_mV"] = _num(n.get("dynamic_mV"))
            x["dynamic"] = n.get("dynamic")
    checked = (s.get("input_check") or {}).get("warnings") or []
    warns = [f"[{w['code']}] {w['message']}" for w in checked]
    # the run's own warnings, apart: they are not the input check's, unless
    # the input check already said the same
    said = {w["code"] for w in checked}
    run_warns = [f"[{w['code']}] {w['message']}" for w in s.get("notices") or []
                 if w.get("level") == "warning" and w["code"] not in said]
    return {"source": source, "verdict": s["verdict"], "disclaimer": SCREENING_NOTE,
            "research_disclaimer": RESEARCH_DISCLAIMER,
            "simulator_verification": s.get("simulator_verification"),
            "design": s.get("design"), "command": s.get("command"),
            "created": s.get("created"), "modes": sorted({n["mode"] for n in nets}),
            "current_source": s.get("current_source"), "temp_C": s.get("temp_C"),
            "em_tref_C": s.get("em_tref_C"), "counts": s.get("counts"),
            "worst_net": s.get("worst_net"), "nets": nets, "worst_items": items[:top],
            "ir_by_power_net": ir, "not_checked": not_checked_lines(s),
            "input_warnings": warns, "run_warnings": run_warns, "html": s.get("html"),
            "charts": list(s.get("charts") or []),
            "process": s.get("process"), "dspf": s.get("dspf"),
            # where the limits came from, per layer and via (hand-entered ...)
            "limits_provenance": s.get("limits_provenance"),
            "limits": limits_lines(s.get("limits_provenance")),
            # --- feature modes --- the self-consistent loop's passes, per net
            "self_consistency": {n["net"]: n["self_consistency"]
                                 for n in s.get("nets") or [] if n.get("self_consistency")},
            # --- feature hierarchy ---
            **({"hierarchy": s["hierarchy"]} if s.get("hierarchy") else {}),
            # --- feature decap --- the EM basis and every basis's verdict
            **decap_fields(s, source)}


def verification_text(v: dict | None) -> str:
    """The line beside the verdict that says whether the simulator that
    wrote the currents is verified on this machine ("" when no simulator
    did)."""
    if not v or v.get("status") in (None, "not applicable"):
        return ""
    from .verify_sim import version_key
    what = " ".join(x for x in (v.get("simulator") or "", version_key(v.get("version")) or "",
                                f"({v['format']})" if v.get("format") else "") if x)
    if v["status"] == "verified":
        rec = v.get("record") or {}
        return (f"**Simulator route: verified on this machine** ({what or 'the simulator'}; "
                f"emir verify-sim passed {rec.get('date') or 'earlier'}"
                + ("" if v.get("version_checked") else "; the result does not say its "
                                                       "version") + ").")
    if v["status"] == "not verified" and v.get("reason") == "failed":
        return (f"**Simulator route: FAILED its verification on this machine** ({what}): "
                f"{v.get('note') or 'the latest emir verify-sim failed'}. Do not rely on "
                f"these numbers.")
    if v.get("reason") in ("format not covered", "pin kinds not covered"):
        word = ("partly verified" if v["status"] == "partly verified" else "NOT verified")
        kinds = v.get("pin_kinds") or []
        out = [k for k in kinds if not k.get("covered")]
        return (f"**Simulator route: {word} on this machine** ({what}): "
                f"{v.get('note') or 'emir verify-sim does not cover these currents'}."
                + (f" Not verified: {', '.join(k['what'] for k in out)}; relay that those "
                   f"currents are UNVERIFIED." if out else "")
                + (f" `{v['command']}` covers the kinds a verification of this emir can "
                   f"cover." if v.get("command") else ""))
    if v["status"] == "not verified" and v.get("reason") == "records unreadable":
        return (f"**Simulator route: NOT verified on this machine** ({what}): "
                f"{v.get('note') or 'this user may not read the verification records'}.")
    if v["status"] == "not verified" and v.get("reason") == "version unknown":
        return (f"**Simulator route: NOT verified on this machine** ({what}): the result "
                f"does not say which version wrote it, so no verification matches it; "
                f"simulate through `emir simulate` (it records the version).")
    if v["status"] == "not verified":
        why = {"stale": "its verification is from another emir version",
               "other host": "its records name another host"}.get(v.get("reason"))
        never = [k["what"] for k in v.get("pin_kinds") or [] if not k.get("verify_sim_covers")]
        return (f"**Simulator route: NOT verified on this machine** ({what}"
                + (f"; {why}" if why else "") + "): run "
                f"`{v.get('command') or 'emir verify-sim'}` before relying on these "
                f"numbers."
                + (f" It does not cover the run's {', '.join(never)}: those currents stay "
                   f"UNVERIFIED." if never else ""))
    note = v.get("note") or "the result does not say"
    return (f"**Simulator route: unknown simulator** ({note}): pass --sim to the run so its "
            f"on-site verification can be looked up.")


#: the loop with decaps on its synthetic mesh, the `hd16b_dec` loop's reported
#: pass (examples/13_decap_aware/results/decap_loop.csv; tests tie them): the
#: worst basis's governing branch against RCC (rms, peak), and the largest
#: error of the decap-aware basis over every branch class and strap, in %
WORST_DECAP_RMS, WORST_DECAP_PEAK, AWARE_DECAP = "23.5", "55.7", "1.2"


def self_consistency_text(net: str, sc: dict) -> str:
    """The self-consistent loop of one net, for a person: how it ended, after
    how many passes, the open loop's IR (pass 0) beside the reported one, and
    what the reported answer is (the loop's, or the open loop's)."""
    from .selfconsist import state_text
    p = sc.get("passes") or []
    state = state_text(sc)
    last = p[-1] if p else {}
    rep = next((x for x in p if x.get("pass") == sc.get("reported_pass")), last)
    vdd = sc.get("vdd")
    rail = f"the testbench's rail ({vdd:g} V)" if isinstance(vdd, (int, float)) \
        else "the testbench's rail"
    nets = sc.get("nets") or []
    together = (f", closed together with {', '.join(n for n in nets if n != net)}"
                if len(nets) > 1 else "")
    tol = last.get("tolerance_mV")
    grid = (sc.get("feedback") or {}).get("grid")
    head = (f"Self-consistent ({net}{together}): {state} after {len(p)} simulation(s) with "
            f"{sc.get('simulator') or 'the simulator'}, each pin fed its own droop waveform "
            f"in series with {rail} (last residual "
            f"{_x(_num(last.get('residual_mV', last.get('droop_change_mV'))), ' mV')}"
            + (f", tolerance {_x(_num(tol), ' mV')}" if tol is not None else "")
            + f"); IR {_x(_num(rep.get('ir_drop_mV')), ' mV')} (pass "
            f"{sc.get('reported_pass')}) against "
            f"{_x(_num(p[0].get('ir_drop_mV')) if p else None, ' mV')} open loop. ")
    if not sc.get("converged"):
        from .tdloop import open_loop_reads
        how = open_loop_reads(p)
        if how["reads"] == "low":
            a0, a1 = how["avg_mA"]
            return head + (f"The reported answer is the open loop's, and it is not "
                           f"self-consistent. Here it reads LOW, not conservative: fed "
                           f"{how['droop_mV']:.4g} mV of droop on pass 1, the block drew "
                           f"{a1:.4g} mA against {a0:.4g} mA open loop (a load whose current "
                           f"rises as its supply falls, which is what makes the loop "
                           f"diverge), so its currents, EM ratios and IR drop are below the "
                           f"real ones.")
        if how["reads"] == "high":
            a0, a1 = how["avg_mA"]
            return head + (f"The reported answer is the open loop's, and it is not "
                           f"self-consistent. Fed {how['droop_mV']:.4g} mV of droop on pass "
                           f"1, the block drew less ({a1:.4g} mA against {a0:.4g} mA open "
                           f"loop), so the open loop reads high here, as it did on the "
                           f"governing branch of every mesh measured.")
        return head + ("The reported answer is the open loop's, and it is not "
                       "self-consistent. No pass fed a droop back, so whether it reads high "
                       "or low here was not measured (on the synthetic meshes measured it "
                       "read high on the governing branch; a load whose current rises as "
                       "its supply falls makes it read low).")
    fb = sc.get("feedback") or {}
    scg = (sc.get("scoring") or {}).get("grid")
    if scg in ("RC", "max(R-only, RC)") and (sc.get("scoring") or {}).get("basis") == "worst":
        # (a summary written before the worst basis had its own label said RC)
        scg = "max(R-only, RC)"
    left = int(fb.get("unsized_decaps") or 0) + int(fb.get("unsized_caps") or 0)
    if left:
        # decaps left out of the droop fed back (EMIR-W547): neither accurate
        # nor conservative (examples/13_decap_aware/results/decap_loop.md)
        body = (f"The answer is the grid with currents that agree with the droop they "
                f"produce, but without the {left} decap(s) the process file's cap_models "
                f"cannot size (EMIR-W547): neither accurate nor conservative. Without their "
                f"local charge the droop fed back is deeper than the real one, so the "
                f"devices' currents read LOW, the governing branch reads high, and those "
                f"decaps' straps are not checked (measured on a synthetic mesh with 8 decaps "
                f"left out: examples/13_decap_aware/results/decap_loop.md)")
    elif scg == "max(R-only, RC)":
        body = ("The answer is the grid with currents that agree with the droop they produce, "
                "scored on the worst EM basis: conservative rather than accurate on the "
                "governing branch (below)")
    else:
        body = ("The answer is the grid with currents that agree with the droop they "
                "produce: accurate rather than conservative on the governing branch, as "
                "measured on the synthetic meshes "
                "(examples/06_big_block/results/selfconsist_waveform.md)")
    if sc.get("stopped") == "settled":
        body += (", stopped settled: the answer held still on two passes while the residual "
                 "stayed above its tolerance")
    if sc.get("at_pass_limit"):
        body += f", on its last allowed pass (--max-passes {sc.get('max_passes')})"
    dec = (f" with its {fb['decaps']} decap(s) behind their access resistance"
           if fb.get("decaps") else "")
    if sc.get("reported_pass") == 0:
        body += (". No droop was fed back: the open loop's own droop was already within the "
                 "tolerance, so the currents are the open loop's (pass 0)")
    elif grid == "R-only":
        body += (". The droop fed back was the R-only grid's (the DSPF has no capacitance on "
                 "the net, EMIR-W544): where the real grid has capacitance, the devices' peak "
                 "currents (the contacts) and the branches that capacitance feeds read low")
    elif (sc.get("scoring") or {}).get("ac") is False:
        body += (f". The droop fed back was the RC grid's{dec}; the pins' currents are "
                 f"constant over the window, so the capacitance carries none and every "
                 f"scoring of the branches agrees")
    elif scg == "RC":
        body += (f". The droop fed back was the RC grid's{dec}, and the branch currents are "
                 f"scored through the RC grid (--dynamic, the decap-aware EM basis), with "
                 f"the current the grid's own capacitance carries")
    elif scg == "max(R-only, RC)":
        body += (f". The droop fed back was the RC grid's{dec}; the branch currents are "
                 f"scored per segment as the larger of their current on the R-only grid and "
                 f"through the RC grid (--dynamic, the worst EM basis): conservative, not "
                 f"accurate, as it keeps the R-only grid's high readings, by as much as the "
                 f"R-only grid reads above the RC grid on this net (against RCC, on one "
                 f"synthetic mesh at 20 fF a node: the governing branch +1.98 % rms and "
                 f"+3.88 % peak, the decap-aware basis alone within 0.52 %; with decaps, "
                 f"whose charge the R-only grid lacks, far more: +{WORST_DECAP_RMS} % rms and "
                 f"+{WORST_DECAP_PEAK} % peak on one with 8 decaps of 5 pF, the decap-aware "
                 f"basis within {AWARE_DECAP} %)")
    else:
        body += (f". The droop fed back was the RC grid's{dec}, but the branch currents are "
                 f"scored on the R-only grid (EMIR-W545): the current the grid's own "
                 f"capacitance carries is missing from them, so the branches it feeds (minor "
                 f"branches) can read low or high (measured at 20 fF a node: -19.5 % to +25.3 "
                 f"% against RCC; through the RC grid, --dynamic --em-basis decap-aware, "
                 f"within 0.52 %)")
    return head + body + ("; a resistive or bias load lands on rcc when the block's other "
                          "rails are ideal or closed in the same loop.")


def at_text(it: dict) -> str:
    """Where a worst item is: the centre of its box, in um ("-" when the
    extraction gave no coordinates)."""
    b = it.get("bbox_um")
    if not b or len(b) != 4:
        return "-"
    return f"({(b[0] + b[2]) / 2:.3f}, {(b[1] + b[3]) / 2:.3f})"


def _dynamic_text(x: dict, sc: dict | None = None) -> str:
    """`; dynamic ... mV` for one power net, when the run computed it. `sc`:
    the net's self-consistency, when the loop ran: the droop then comes from
    its reported pass's currents, open loop only when that is pass 0."""
    from .bigblock import loop_pass, loop_words
    dy = x.get("dynamic")
    if not dy:
        return ""
    where = f" at {dy['node']}" if dy.get("node") else ""
    if dy.get("periodic"):
        state = ("periodic steady state" if dy.get("settled")
                 else "NOT at periodic steady state")
        if loop_pass(sc) is None:
            return (f"; dynamic (open loop) {_x(x.get('dynamic_mV'), ' mV')}{where} "
                    f"(open loop, {state})")
        return (f"; dynamic {_x(x.get('dynamic_mV'), ' mV')}{where} (from "
                f"{loop_words(sc)}; {state})")
    return f"; dynamic {_x(x.get('dynamic_mV'), ' mV')}{where} (from measured branch currents)"


def markdown(r: dict) -> str:
    """The report as Markdown, for a person (or an agent to relay as is)."""
    from .decap import headline, report_lines, verdict_cell
    c = r.get("counts") or {}
    wn = r.get("worst_net")
    L = [f"# EMIR screening report: {r.get('design') or 'design'}", "",
         f"**Verdict: {r['verdict']}**{headline(r)} -- {len(r['nets'])} net(s): "
         f"{c.get('FAIL', 0)} FAIL, "
         f"{c.get('WARN', 0)} WARN, {c.get('CLEAN', 0)} CLEAN"
         + (f"; worst {_x(_num(wn.get('ratio')))} on {wn.get('net')}" if wn else "") + ".",
         ""]
    sv = verification_text(r.get("simulator_verification"))
    if sv:
        L += [sv, ""]
    sus = [x for x in (r.get("limits_provenance") or {}).get("suspects") or []
           if not x.get("confirmed")]
    if sus:
        L += [f"**The limits are SUSPECT** (EMIR-W321): {len(sus)} value(s) of the process "
              f"file are outside the range their quantity normally takes, most often a unit "
              f"slip (" + ", ".join(f"{x['path']} = {x['value']:g} {x['unit']}"
                                    for x in sus[:4])
              + "). The verdict is only as good as those values: check them against the "
                "design manual.", ""]
    L += [f"**{SCREENING_NOTE}** {RESEARCH_DISCLAIMER}", ""]
    if r.get("command") or r.get("created"):
        L.append("- Run: " + (f"`{r['command']}`" if r.get("command") else "(command not "
                                                                         "recorded)")
                 + (f", finished {r['created']}" if r.get("created") else ""))
    cs = r.get("current_source") or {}
    L.append(f"- Mode: {', '.join(r['modes']) or 'none'}")
    for net, sc in (r.get("self_consistency") or {}).items():
        L.append("- " + self_consistency_text(net, sc))
    L.append(f"- Currents: {cs.get('description', 'unknown')}"
             + (f" (`{cs['path']}`)" if cs.get("path") else ""))
    if r.get("temp_C") is not None:
        L.append(f"- Analysis temperature: {r['temp_C']:g} C"
                 + (f" (the process file's EM limits are quoted at {r['em_tref_C']:g} C)"
                    if r.get("em_tref_C") is not None else ""))
    if r.get("process"):
        L.append(f"- Process file: `{r['process']}`")
    if r.get("dspf"):
        L.append(f"- DSPF: `{r['dspf']}`")
    if r.get("html"):
        L.append(f"- HTML report: `{os.path.join(r['html'], 'index.html')}`")
    if r.get("charts"):
        L.append(f"- Chart (every net on the layout, EM and IR; attach it beside this report): "
                 f"`{r['charts'][0]}`")
    L += ["", "## Nets", "",
          "| net | verdict | mode | governing metric | worst ratio | worst wire | worst via "
          "| IR |",
          "|---|---|---|---|---|---|---|---|"]
    for n in r["nets"]:
        L.append(f"| {n['net']} | {verdict_cell(n, r)} | {n['mode']} | "
                 f"{n['governing_metric'] or '-'} | {_x(n['worst_ratio'])} | "
                 f"{_x(n.get('worst_wire'))} | {_x(n.get('worst_via'))} | "
                 f"{_x(n['ir_drop_mV'], ' mV')} {n['ir_kind']} |")
    # --- feature decap --- both bases, and the person's choice
    L += report_lines(r)
    L += ["", "## Worst items", ""]
    from .decap import worst_items_caption      # --- feature decap ---
    L += worst_items_caption(r)
    if r["worst_items"]:
        L += ["| net | segment | at (x, y) um | layer | kind | metric | ratio | fix |",
              "|---|---|---|---|---|---|---|---|"]
        for it in r["worst_items"]:
            ratio = _x(_num(it.get("ratio")))
            fix = it.get("fix") or "none needed (below the limit, inside the warn margin)"
            L.append(f"| {it['net']} | {it['name']} | {at_text(it)} | {it['layer']} | "
                     f"{it['kind']} | {it['metric']} | {ratio} | {fix} |")
    else:
        L.append("No segment at or above the warn fraction.")
    L += ["", "## IR drop per power net", ""]
    if r["ir_by_power_net"]:
        L += [f"- {x['net']}: {_x(x['ir_drop_mV'], ' mV')} "
              f"({'from the feed' if x['ir_kind'] == 'drop' else 'spread across the net'})"
              + _dynamic_text(x, (r.get("self_consistency") or {}).get(x["net"]))
              for x in r["ir_by_power_net"]]
    else:
        L.append("No power net among the analysed nets.")
    if r.get("hierarchy"):
        # --- feature hierarchy ---
        from .hier_cli import report_lines
        L += report_lines(r["hierarchy"])
    if r.get("limits"):
        L += ["", "## Where the limits came from", ""]
        L += [f"- {x}" for x in r["limits"]]
    L += ["", "## Not checked", ""]
    L += ([f"- {x}" for x in r["not_checked"]]
          or ["Every item the run found was checked against a limit."])
    L += ["", "## Input-check warnings", ""]
    L += [f"- {x}" for x in r["input_warnings"]] or ["None."]
    L += ["", "## Warnings raised during the run", ""]
    L += [f"- {x}" for x in r.get("run_warnings") or []] or ["None."]
    L += ["", f"{SCREENING_NOTE} Foundry-qualified EM/IR remains the sign-off flow; "
              f"these numbers screen the layout and say where to look. "
              f"{RESEARCH_DISCLAIMER}"]
    return "\n".join(L) + "\n"


def main(argv=None, sink=None) -> int:
    from .output import Out, document, parser
    ap = parser("emir report", formats=("md", "json", "text"),
                description="The summary of a run to relay to a person.")
    ap.add_argument("--from", dest="src", required=True, metavar="FILE",
                    help="the run summary (emir run --summary-out FILE), "
                         "or a block abstract (emir abstract -o FILE, back-annotated or "
                         "not); - reads stdin")
    ap.add_argument("--top", type=int, default=TOP,
                    help=f"how many worst items to list (default {TOP})")
    a = ap.parse_args(argv)
    sink = sink or Out("json" if a.format == "json" else "text")
    from .hier_cli import maybe_report as abstract_report
    done = abstract_report(a, sink)       # a block abstract (emir.abstract/1)
    if done is not None:
        return done
    r = build(load_summary(a.src), a.src, top=max(a.top, 0))
    md = markdown(r)
    if a.format != "json":
        print(md, end="")
    sink.emit(document("report", **r, markdown=md))
    return 0
