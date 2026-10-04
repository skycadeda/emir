"""The run summary: the `emir.run/1` document.

One document serves three readers: `emir run --format json` prints it,
`--summary-out FILE` writes it (the Virtuoso form reads it back), and
`emir report --from FILE` turns it into the summary a person reads. It holds
references and numbers, never arrays: per net the verdict, mode, governing
metric, IR, the worst items with their fix in engineer's units; for the run
the current source, the analysis temperature, what was NOT checked, the input
checker's warnings, the coded notices met on the way and the next steps.

The fields of 1.0's summary file (`design`, `dspf`, `process`, `temp_C`,
`metrics`, `html`, `charts`, `viewer`, and per net `net`, `mode`, `max_ratio`,
`ir_drop_mV`, `dynamic_mV`, `verdict`, `worst`, `n_violations`, `unmapped`,
`no_width`, `self_consistent`, `via_cuts`) keep their names and meaning.
"""
from __future__ import annotations

import os
import shlex

#: how many of each net's worst items the summary lists
TOP_ITEMS = 10


def _abs(p):
    return os.path.abspath(p) if p else None


def current_source(a) -> dict:
    """Where the currents came from, in words and as data."""
    if getattr(a, "abstract", None):
        # --- feature hierarchy ---
        return {"kind": "abstracts", "path": _abs(getattr(a, "cc_sim", None)),
                "abstracts": [_abs(x.rsplit("@", 1)[0]) + "@" + x.rsplit("@", 1)[1]
                              if "@" in x else x for x in a.abstract],
                "description": "block abstracts (emir abstract) at the black-box pins of "
                               f"the top DSPF, align {getattr(a, 'align', 'inphase')}"
                               + ("; the top's own devices from its simulation (--cc-sim)"
                                  if getattr(a, "cc_sim", None) else "")}
    if getattr(a, "currents", None):
        return {"kind": "dc", "path": None,
                "description": "DC currents given with --currents (avg = rms = peak)"}
    if getattr(a, "netlist", None):
        sim = getattr(a, "sim", None) or "spectre"
        sc = getattr(a, "self_consistent", False)
        return {"kind": "netlist", "path": _abs(a.netlist), "simulator": sim,
                "result": _abs(a.cc_sim) if a.cc_sim else None,
                "description": f"the netlist simulated with {sim} (--netlist)"
                               + (", through the self-consistent loop (each pin fed its own "
                                  "droop waveform in series with the testbench's rail; "
                                  "result: the pass the loop reports, "
                                  "`self_consistency.reported_pass`: pass 0, the open loop, "
                                  "when it did not converge)" if sc else "")}
    if getattr(a, "cc_sim", None):
        return {"kind": "simulation", "path": _abs(a.cc_sim),
                "description": "a finished simulation result (--cc-sim)"}
    return {"kind": "none", "path": None, "description": "nothing drove the run"}


def item(c: dict, net: str) -> dict:
    """One violating segment, with its fix in the engineer's units (none
    below 1.00x: a fix never narrows a wire or removes a cut)."""
    from .layoutview import fix_hint
    return {"net": net, "name": c["name"], "layer": c["layer"], "kind": c["kind"],
            "metric": c["metric"], "ratio": float(c["ratio"]),
            "ratios": {k: float(v) for k, v in (c.get("ratios") or {}).items()},
            "severity": c["severity"], "w_um": c.get("w"), "cuts": c.get("cuts"),
            # the layer's width loss: the wire limit applied to w_um - this
            **({"width_loss_um": c["dw"]} if c.get("dw") else {}),
            "cuts_from": c.get("cuts_from"), "fix": fix_hint(c) or None,
            # where the item is (um): the person finds the failing via by it
            "bbox_um": c.get("bbox_um"),
            # --- feature decap --- every EM basis's ratio of this segment
            **(c.get("by_basis") or {})}


def _limits_missing(proc, lmap_values) -> list:
    """Process entries in use with a metric that has no limit (not checked)."""
    from .engine import METRICS
    out, seen = [], set()
    for kind, name in sorted(lmap_values):
        if (kind, name) in seen:
            continue
        seen.add((kind, name))
        lim = (proc.vias[name]["imax"] if kind == "via" else proc.layers[name]["jmax"])
        missing = [m for m in METRICS if not lim.get(m)]
        if missing:
            out.append({"name": name, "kind": "via" if kind == "via" else "wire",
                        "metrics": missing})
    return out


def _estimated(proc, lmap_values) -> list:
    names = set()
    for kind, name in lmap_values:
        d = (proc.data.get("vias") if kind == "via" else proc.data.get("layers")) or {}
        e = d.get(name) or {}
        if e.get("estimate") or e.get("rms_peak_estimate"):
            names.add(name)
    return sorted(names)


def derating_gap(proc) -> dict | None:
    """The avg limits applied above the temperature they hold at -- with no
    `jmax_factor` table, or above the table's top, where it is clamped. EM
    limits fall with temperature, so the avg check is NOT conservative
    there. None when the limits were derated to the analysis temperature,
    which Black's equation (`derating:`) does at any temperature above
    em_tref."""
    if proc is None or getattr(proc, "derating", None):
        return None
    t, tref, jf = float(proc.temp), float(proc.em_tref), proc.jmax_factor or {}
    if jf:
        top = max(jf)
        if t > top + 0.5:
            return {"temp_C": t, "em_tref_C": tref, "table_top_C": float(top),
                    "what": f"the analysis temperature {t:g} C is above the jmax_factor "
                            f"table's top ({top:g} C): the avg limits are derated only to "
                            f"{top:g} C (clamped)",
                    "direction": "non-conservative"}
        return None
    if t > tref + 0.5:
        return {"temp_C": t, "em_tref_C": tref, "table_top_C": None,
                "what": f"the avg limits, quoted at {tref:g} C, are applied unchanged at "
                        f"{t:g} C (no jmax_factor table): EM limits fall with temperature",
                "direction": "non-conservative"}
    return None


def diagonal_wires(r: dict) -> list:
    """A net's checked wires on diagonal shapes (`$dir=diag`): their width is
    magic's tile extent, not the width across the current (EMIR-W647)."""
    nw, lmap = r.get("network"), r.get("layer_map") or {}
    return [x["name"] for x in getattr(nw, "res", None) or []
            if x.get("diag") and (lmap.get(x.get("layer")) or ("",))[0] == "layer"]


def not_checked(solved: dict, proc) -> dict:
    """What the verdict does NOT cover, so nobody reads a clean net as more
    than it is: DSPF layers with no process entry, limits the process file
    does not give, pins the simulation did not save (taken as zero current),
    vias checked as one cut, wires with no width, wires on diagonal shapes
    (checked at magic's tile extent), limits that are estimates,
    the parts of a net in pieces that could not be solved, and the decaps
    whose straps the EM basis does not cover (no --dynamic, a decap
    cap_models cannot size, the decap-free basis, decap-aware EM not
    computed)."""
    lmap_values = set()
    unmapped = set()
    out = {"unmapped_layers": [], "limits_missing": [], "estimated_limits": [],
           "unsaved_pins": {}, "vias_assumed_one_cut": {}, "wires_without_width": {},
           "unsolved": {}, "avg_not_derated": derating_gap(proc), "bidirectional": {},
           "via_crowding": {}}
    for net, r in solved.items():
        lmap_values |= set((r.get("layer_map") or {}).values())
        unmapped |= set(r.get("unmapped") or [])
        if r.get("unsaved_pins"):
            out["unsaved_pins"][net] = list(r["unsaved_pins"])
        assumed = (r.get("via_cuts") or {}).get("assumed") or []
        if assumed:
            out["vias_assumed_one_cut"][net] = list(assumed)
        if r.get("no_width"):
            out["wires_without_width"][net] = list(r["no_width"])
        if r.get("unsolved"):
            # a net in pieces (a run forced past EMIR-E207): see dspf.Network.solve
            out["unsolved"][net] = dict(r["unsolved"])
        if r.get("bidirectional"):
            out["bidirectional"][net] = list(r["bidirectional"])
        if r.get("via_crowding"):
            out["via_crowding"][net] = list(r["via_crowding"])
        diag = diagonal_wires(r)
        if diag:
            out.setdefault("diagonal_wires", {})[net] = diag
        if r.get("decaps_uncovered"):
            # decaps whose straps the EM verdict does not cover (`decap.uncovered`)
            out.setdefault("decap_straps_not_covered", {})[net] = [
                dict(x) for x in r["decaps_uncovered"]]
    out["unmapped_layers"] = sorted(unmapped)
    if proc is not None:
        out["limits_missing"] = _limits_missing(proc, lmap_values)
        out["estimated_limits"] = _estimated(proc, lmap_values)
    return out


def net_entry(net: str, r: dict, d, proc, rails) -> dict:
    from .bigblock import dynamic_entry
    from .decap import net_fields as decap_net_fields
    from .dspf import _verdict
    w = r.get("worst")
    mr = r.get("max_ratio") or {}
    worst = None
    if w:
        worst = {k: w.get(k) for k in ("name", "layer", "kind", "metric",
                                        "ratio", "hint", "current") if k in w}
    clusters = r.get("clusters") or []
    return {
        "net": net, "mode": r.get("mode") or "dc",
        "mode_reason": r.get("mode_reason"),
        "verdict": _verdict(r, proc),
        "power": net in rails,
        "governing_metric": w["metric"] if w else None,
        "max_ratio": ({k: float(v) for k, v in mr.items()} if isinstance(mr, dict)
                      else float(mr)),
        # NaN (written as null) when nothing could be solved
        "ir_drop_mV": float(r.get("ir_drop", 0.0)) * 1e3,
        # a net with a port is fed there: the figure is the drop from the
        # feed. Without one it is the spread across the net.
        "ir_kind": "drop" if d.net_ports(net) else "spread",
        "dynamic_mV": (float(r["dynamic"]["worst_V"]) * 1e3 if r.get("dynamic") else None),
        "kcl_residual_A": float(r.get("kcl_residual", 0.0)),
        "metric_note": r.get("metric_note"),
        "worst": worst,
        "n_violations": len(clusters),
        "worst_items": [item(c, net) for c in clusters[:TOP_ITEMS]],
        "unmapped": r.get("unmapped") or [],
        # wires with no width: not checked, so the verdict is FAIL
        "no_width": r.get("no_width") or [],
        "unsaved_pins": list(r.get("unsaved_pins") or []),
        "self_consistent": bool(r.get("self_consistent")),
        # --- feature modes --- the self-consistent loop's passes (each with its
        # droop and current change), and an open-loop droop over the supply
        "self_consistency": r.get("self_consistency"),
        "droop_fraction": r.get("droop_fraction"),
        # vias whose cut count was not extracted (see dspf.via_cuts)
        "via_cuts": {
            "from_area": len((r.get("via_cuts") or {}).get("area", [])),
            "assumed_one_cut": (r.get("via_cuts") or {}).get("assumed", [])},
        # a net in pieces: what was not solved (None on a whole net)
        "unsolved": r.get("unsolved"),
        # big blocks: how the waveform superposition ran (dense or streamed,
        # `bigblock.superpose`), and the dynamic droop (`--dynamic`, or rcc)
        "superposition": r.get("superposition"),
        "dynamic": dynamic_entry(r),
        # --- feature decap --- the EM verdict's basis and every basis's
        # numbers (present with --dynamic or --em-basis)
        **decap_net_fields(r),
    }


def _limits_provenance(solved: dict, proc) -> dict | None:
    from .process_file import limits_provenance
    used = set()
    for r in solved.values():
        used |= set((r.get("layer_map") or {}).values())
    # a --dynamic run sized its capacitor devices and decaps from cap_models
    dyn = any(r.get("dynamic") for r in solved.values())
    return limits_provenance(proc, used, cap_models=dyn)


def input_check(res) -> dict:
    rep = res.report
    a = res.args
    if rep is None:
        return {"ran": False, "passed": None, "forced": False, "warnings": [], "fails": []}
    return {"ran": True, "passed": rep.passed,
            "forced": bool(getattr(a, "force", False) and not rep.passed),
            "warnings": [f.as_dict(**rep.args) for f in rep.sorted() if f.level == "WARN"],
            "fails": [f.as_dict(**rep.args) for f in rep.sorted() if f.level == "FAIL"]}


def next_steps(res, overall: str) -> list:
    """What to do with the result, as commands built from the run's own
    arguments: relay it (`emir report`), look at it, act on it."""
    from .errors import step
    from .output import command_line, strip_options
    a = res.args
    fmt = ["--format", "json"] if getattr(a, "format", "text") == "json" else []
    argv = list(getattr(a, "argv", None) or [])
    out = []
    if res.summary_path:
        out.append(step("turn the summary into the report to relay to the person "
                        "(verdict, worst items with fixes, what was not checked)",
                        command_line("report", "--from", res.summary_path)))
    elif argv:
        # no file to report from. `emir report --from -` reads this document
        # from a pipe, but on its own it has nothing to read; the runnable
        # route is the summary file (pass --summary-out from the start to
        # save the re-run)
        kept = "emir_out/summary.json"
        out.append(step("keep the run summary in a file for the report (this re-runs "
                        "the analysis; next time pass --summary-out from the start, or "
                        "pipe this JSON into `emir report --from -`)",
                        command_line("run", strip_options(argv, {"--summary-out": 1,
                                                                 "--json": 1}),
                                     "--summary-out", kept)))
        out.append(step("turn the summary into the report to relay to the person",
                        command_line("report", "--from", kept)))
    # the simulator route, where a verification here would change the answer
    # (`simulator_verification.command`: none for an unknown version, a format
    # verify-sim never writes, or a records file this user may not read)
    v = getattr(res, "sim_verification", None) or {}
    if v.get("command") and v.get("status") in ("not verified", "partly verified"):
        lic = v.get("simulator") in ("spectre", "hspice")
        out.append({"why": f"the {v.get('simulator')} route is {v['status']} on this "
                           f"machine ({v.get('reason')}): verify it here, then the same "
                           f"run again so its summary records it"
                           + (f" ({v.get('simulator')} is licensed: ask the person first)"
                              if lic else ""),
                    "command": v["command"] + (" --run" if lic else "")
                    + (" " + " ".join(fmt) if fmt else ""),
                    **({"needs_human": True} if lic else {})})
    # --- feature decap --- the person's choice of EM basis, the finer step
    from .decap import next_steps as decap_steps
    out += decap_steps(res, fmt)
    if argv and overall in ("FAIL", "WARN") and not a.html and not a.netlist:
        base = strip_options(argv, {"--format": 1, "--html": 1, "--open": 0})
        out.append(step("see each net's map and worst segments in a browser, and the EM/IR "
                        "chart of every net on its layout (emir_chart.png)",
                        command_line("run", base, "--html", "emir_out/html", fmt)))
    if argv and overall == "FAIL":
        out.append(step("after widening the failing wires or adding cuts, re-extract "
                        "and re-run the same command to confirm",
                        "emir run " + shlex.join(argv)))
    out += _self_consistent_steps(res, fmt)
    # --- feature hierarchy --- (back-annotation of the blocks)
    out += list(getattr(res, "extra_steps", None) or [])
    return out


def _self_consistent_steps(res, fmt) -> list:
    """--- feature modes --- After an open-loop cc-grid run whose IR drop is
    above about 2 % of the supply (--vdd): plan the self-consistent route.
    It re-simulates, so the person decides (`needs_human`)."""
    from .output import command_line
    from .plan_bigblock import DROOP_FRACTION
    from .selfconsist import vdd_of
    a = res.args
    out = []
    dyn_offered = False
    for net, r in res.solved.items():
        sc = r.get("self_consistency")
        if r.get("self_consistent") and sc and (not sc.get("converged")
                                                or sc.get("at_pass_limit")):
            step_ = _sc_retry_step(a, net, sc, fmt)
            if step_:
                out.append(step_)
            if not sc.get("converged"):
                continue
        if r.get("self_consistent") and sc and not dyn_offered:
            step_ = _sc_dynamic_step(a, net, sc, fmt, getattr(res, "summary_path", None))
            if step_:
                out.append(step_)
                dyn_offered = True
            continue
        frac = r.get("droop_fraction")
        if r.get("mode") != "cc-grid" or r.get("self_consistent") or frac is None \
                or frac <= DROOP_FRACTION:
            continue
        args = ["--dspf", _abs(a.dspf)]
        if a.process:
            args += ["--process", _abs(a.process)]
        vdd = vdd_of(a.vdd, net)
        args += ["--net", net, "--mode", "cc-grid", "--self-consistent", "--vdd",
                 repr(float(vdd))]
        if getattr(a, "sim", None):
            args += ["--sim", a.sim]
        out.append({"why": f"{net}: the IR drop is {frac:.1%} of the {vdd:g} V supply "
                           f"(above about {DROOP_FRACTION:.0%}) and this run was open loop, "
                           f"so its currents read high: plan the self-consistent run (it "
                           f"re-simulates the CC testbench once per pass: ask the person)",
                    "command": command_line("next", args, fmt), "needs_human": True})
    return out


def sc_dynamic_wanted(a, sc: dict) -> bool:
    """A converged loop that fed back the RC grid's droop and scored its
    branches on the R-only grid (EMIR-W545), on a run without --dynamic: the
    same run with --dynamic is the person's to choose. Not at pass 0 (no droop
    was fed back), nor when the pins' currents are constant (no capacitor
    carries any: every scoring agrees)."""
    sco = sc.get("scoring") or {}
    return bool(not getattr(a, "dynamic", False) and sc.get("converged")
                and sc.get("reported_pass") != 0
                and (sc.get("feedback") or {}).get("grid") == "RC"
                and sco.get("grid") == "R-only" and sco.get("ac") is not False)


#: why the W545 step is offered (`net` filled in)
SC_DYNAMIC_WHY = ("{net}: the loop fed back the RC grid's droop, but its branch currents were "
                  "scored on the R-only grid (EMIR-W545): minor branches can read low or "
                  "high; --dynamic scores them through the RC grid: its decap-aware EM needs "
                  "a window of whole periods, so the block's period (--period), and it "
                  "re-simulates the loop: ask the person")


def _sc_dynamic_step(a, net: str, sc: dict, fmt, summary_path: str | None = None
                     ) -> dict | None:
    """--- feature modes --- A converged loop that fed back the RC grid's droop
    and scored its branches on the R-only grid (EMIR-W545), run without
    --dynamic: the same run with --dynamic scores them through the RC grid
    (its default EM basis; it re-simulates the loop, so the person decides).
    Without --period the re-run would compute no decap-aware EM (no window of
    whole periods, EMIR-W433): the step is then a plan that asks for the
    period (a run's next step runs as printed: it holds no placeholder). With
    the run's summary (`summary_path`, its --summary-out) that plan is `emir
    next --run-summary`, whose re-run is this run's whole command (its loop
    options, --droop-file, --summary-out and per-net --vdd kept) with
    --dynamic and the period; without one, `emir next` with the run's inputs
    and --dynamic, and the reason names the run's own options the plan does
    not carry. With --dynamic already given (the decap-free basis chosen)
    nothing is offered: the basis is the person's."""
    from .output import command_line
    argv = list(getattr(a, "argv", None) or [])
    if not argv or not sc_dynamic_wanted(a, sc):
        return None
    why = SC_DYNAMIC_WHY.format(net=net)
    if getattr(a, "period", None):
        return {"why": why, "command": command_line("run", argv, ["--dynamic"],
                                                    fmt if "--format" not in argv else []),
                "needs_human": True}
    if summary_path:
        return {"why": why + " (no --period on this run: emir next plans the same run with "
                             "--dynamic and asks for the period)",
                "command": command_line("next", "--run-summary", summary_path, fmt),
                "needs_human": True}
    from .selfconsist import vdd_of
    if not (getattr(a, "dspf", None) and getattr(a, "netlist", None)):
        return None
    args = ["--dspf", _abs(a.dspf)]
    if getattr(a, "process", None):
        args += ["--process", _abs(a.process)]
    nets = getattr(a, "net", None) or net
    args += ["--netlist", _abs(a.netlist), "--net", nets, "--mode", "cc-grid",
             "--self-consistent", "--dynamic"]
    vdd = vdd_of(getattr(a, "vdd", None), net) if "," not in str(nets) else None
    if vdd is not None:
        args += ["--vdd", repr(float(vdd))]
    if getattr(a, "sim", None):
        args += ["--sim", a.sim]
    kept = []
    for i, t in enumerate(argv):
        name, eq, val = t.partition("=")
        if name in LOOP_OPTIONS and (name != "--vdd" or vdd is None):
            val = val if eq else (argv[i + 1] if i + 1 < len(argv) else "")
            kept.append(f"{name} {val}".strip())
    return {"why": why + " (no --period on this run: plan the re-run with the period"
                   + (f"; the plan starts from the run's inputs, so give its run this "
                      f"run's own {', '.join(kept)} again" if kept else "") + ")",
            "command": command_line("next", args, fmt), "needs_human": True}


#: the options of a self-consistent run that `emir next` does not plan (a
#: re-planned run without its summary would lose them; --vdd: a multi-net
#: run's per-net values)
LOOP_OPTIONS = ("--max-passes", "--tol-mV", "--tol-rel", "--sim-timeout", "--droop-file",
                "--summary-out", "--vdd")


def _sc_retry_step(a, net: str, sc: dict, fmt) -> dict | None:
    """--- feature modes --- A self-consistent loop that reported pass 0 (not
    converged), or converged only on its last allowed pass: the same run with
    more passes (or a longer time limit), for the person to decide (it
    re-simulates). A loop that converged before its last allowed pass gets
    none: nothing in how it ended asks for more passes, whoever the caller."""
    from .output import command_line, strip_options
    argv = list(getattr(a, "argv", None) or [])
    if not argv or (sc.get("converged") and not sc.get("at_pass_limit")):
        return None
    st = sc.get("stopped")
    if st == "time limit":
        last = max((p.get("seconds") or 0.0) for p in sc.get("passes") or [{}])
        extra, why = (["--sim-timeout", f"{max(4 * last, 7200.0):g}"],
                      "a pass's simulation ran past its time limit and pass 0, the open "
                      "loop, is reported: a diverging droop makes the simulator crawl, but a "
                      "long block can also be just slow; give the passes longer")
    elif sc.get("converged"):
        extra, why = (["--max-passes", str(int(sc.get("max_passes") or 10) + 6)],
                      "the loop converged only on its last allowed pass: more passes show "
                      "whether the answer holds")
    elif st == "pass limit":
        from .tdloop import open_loop_reads
        reads = open_loop_reads(sc.get("passes") or [])["reads"]
        extra, why = (["--max-passes", str(int(sc.get("max_passes") or 10) + 6)],
                      "the loop stopped at its pass limit and pass 0, the open loop, is "
                      "reported (" + {"low": "here it reads LOW: pass 1 drew more current "
                                             "with the droop fed back",
                                      "high": "here it reads high: pass 1 drew less current "
                                              "with the droop fed back"}.get(
                          reads, "which way it reads was not measured") + "); more passes "
                      "may settle it when its residual was still falling")
    else:
        # diverging (more passes would not help) or a failed simulation (its
        # log says why): nothing to re-run as it is
        return None
    base = strip_options(argv, {"--max-passes": 1, "--sim-timeout": 1})
    return {"why": f"{net}: {why} (it re-simulates once per pass: ask the person)",
            "command": command_line("run", base, extra, fmt if "--format" not in base else []),
            "needs_human": True}


def summary_document(res) -> dict:
    """The `emir.run/1` document of a finished run (`dspf.RunResult`)."""
    from . import RESEARCH_DISCLAIMER, SCREENING_NOTE
    from .verify_sim import status as verification_status
    from .dspf import overall_verdict, power_net_list
    from .engine import METRICS
    from .layoutview import power_nets
    from .output import document
    import datetime
    a, d, proc, solved = res.args, res.dspf, res.proc, res.solved
    overall, counts, top, worst = overall_verdict(solved, proc)
    rails = power_net_list(a) or power_nets(sorted(solved))
    # --- feature decap --- every net's EM basis (rcc: measured)
    from .decap import prepare_summary, summary_fields
    prepare_summary(res)
    viewer = res.viewer
    # one time per run: the --summary-out file and the printed document are
    # built separately, and once differed when a second ticked between them
    created = getattr(res, "created", None) or \
        datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        res.created = created
    except AttributeError:                       # pragma: no cover - a frozen result
        pass
    return document(
        "run",
        disclaimer=SCREENING_NOTE,
        research_disclaimer=RESEARCH_DISCLAIMER,
        verdict=overall,
        counts=counts,
        worst_net=({"net": top, "ratio": worst} if top is not None else None),
        command=getattr(a, "command", None),
        # when the run finished (UTC), so a report says which run it relays
        created=created,
        design=d.design,
        dspf=_abs(a.dspf),
        process=_abs(a.process),
        temp_C=proc.temp if proc else None,
        em_tref_C=proc.em_tref if proc else None,
        avg_derate=proc.derate if proc else None,
        metrics=list(METRICS),
        current_source=current_source(a),
        # whether this machine verified the simulator that wrote the
        # currents (`emir verify-sim`): verified / not verified / unknown
        # simulator / not applicable, with the matching record
        simulator_verification=(getattr(res, "sim_verification", None)
                                or verification_status(applicable=False)),
        html=_abs(a.html),
        # --html: the EM/IR chart PNGs (emir.chart), dark and light
        charts=list(getattr(res, "charts", None) or []),
        viewer=({k: os.path.abspath(v) for k, v in viewer.items() if isinstance(v, str)}
                if viewer else None),
        power_nets=list(rails),
        nets=[net_entry(n, r, d, proc, rails) for n, r in solved.items()],
        not_checked=not_checked(solved, proc),
        # where the applied limits came from, per layer and via in use
        # (`process_file.limits_provenance`): entered by hand, imported ...
        limits_provenance=_limits_provenance(solved, proc),
        input_check=input_check(res),
        notices=list(res.notices),
        next_steps=next_steps(res, overall),
        # --- feature decap --- the basis asked for, and the run's arguments
        **summary_fields(res),
        # --- feature hierarchy --- (present only on a run with --abstract)
        **({"hierarchy": res.hierarchy} if getattr(res, "hierarchy", None) else {}),
    )
