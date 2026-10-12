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
`no_width`, `via_cuts`) keep their names and meaning. A net's `mode` is rcc
(every branch current of a simulation) or dc (--currents); both check avg,
rms and peak.
"""
from __future__ import annotations

import os
import shlex

#: how many of each net's worst items the summary lists
TOP_ITEMS = 10


def _abs(p):
    return os.path.abspath(p) if p else None


def current_source(a) -> dict:
    """Where the currents came from, in words and as data: in the order the
    run takes them (a simulation before DC currents; the run refuses both,
    EMIR-E514)."""
    if getattr(a, "netlist", None):
        sim = getattr(a, "sim", None) or "spectre"
        return {"kind": "netlist", "path": _abs(a.netlist), "simulator": sim,
                "result": _abs(a.cc_sim) if a.cc_sim else None,
                "description": f"the netlist simulated with {sim} (--netlist)"}
    if getattr(a, "cc_sim", None):
        return {"kind": "simulation", "path": _abs(a.cc_sim),
                "description": "a finished simulation result (--cc-sim): every branch "
                               "current of the RC netlist"}
    if getattr(a, "currents", None):
        return {"kind": "dc", "path": None,
                "description": "DC currents given with --currents (avg = rms = peak)"}
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
            "bbox_um": c.get("bbox_um")}


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
    does not give, vias checked as one cut, wires with no width, wires on
    diagonal shapes (checked at magic's tile extent), limits that are
    estimates, the parts of a net in pieces that could not be solved, the avg
    limits applied above the temperature they hold at, reversing currents and
    via arrays near their limit."""
    lmap_values = set()
    unmapped = set()
    out = {"unmapped_layers": [], "limits_missing": [], "estimated_limits": [],
           "vias_assumed_one_cut": {}, "wires_without_width": {},
           "unsolved": {}, "avg_not_derated": derating_gap(proc), "bidirectional": {},
           "via_crowding": {}}
    for net, r in solved.items():
        lmap_values |= set((r.get("layer_map") or {}).values())
        unmapped |= set(r.get("unmapped") or [])
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
    out["unmapped_layers"] = sorted(unmapped)
    if proc is not None:
        out["limits_missing"] = _limits_missing(proc, lmap_values)
        out["estimated_limits"] = _estimated(proc, lmap_values)
    return out


def dynamic_entry(r: dict) -> dict | None:
    """The run summary's `dynamic` object for one net: rcc's dynamic IR, from
    the simulated branch currents (None for DC currents)."""
    dy = r.get("dynamic")
    if not dy:
        return None
    nw = r.get("network")
    node = dy.get("worst_node")
    xy = None
    if nw is not None and node is not None:
        xy = nw.dspf.node_xy.get(node)
    return {"worst_mV": float(dy["worst_V"]) * 1e3, "node": node,
            "x_um": float(xy[0]) if xy else None, "y_um": float(xy[1]) if xy else None,
            "t_s": float(dy["worst_t"]), "source": dy.get("source"),
            "n_steps": dy.get("n_steps")}


def ir_resistance(r: dict, proc) -> dict | None:
    """The resistances a net's IR is computed with: rcc's are those the
    simulation ran, the DSPF's (`simulated`, at the temperature the DSPF
    states, else the process file's temp_reference: `stated` False); a DC
    solve's are each wire's scaled by its layer's TCR to the analysis
    temperature (`scaled`), or the DSPF's when it states that temperature
    (`as extracted`)."""
    b = r.get("r_basis")
    if b:
        return {"kind": "simulated", "temp_C": float(b["temp_C"]), "stated": bool(b["stated"])}
    if proc is None or "r_at_temp" not in r:
        return None
    return {"kind": "as extracted" if r["r_at_temp"] else "scaled",
            "temp_C": float(proc.temp), "stated": bool(r["r_at_temp"])}


def window_entry(w: dict | None) -> dict | None:
    """rcc's averaging window: the record's last whole periods of --period, up
    to three (`waveform.window`), where it starts and ends, how many periods,
    how many branches matter (`branches`: those carrying 1 % of the net's
    largest current) and the largest change of their avg, rms or peak between
    the record's last two periods (`change`, at most 2 %: a run over more is
    refused, EMIR-E429). None on DC runs and at an operating point (one time
    point)."""
    if not w:
        return None
    return {"t0_s": float(w["t0"]), "t1_s": float(w["t1"]), "period_s": float(w["period"]),
            "n_periods": int(w["n_periods"]), "change": float(w["change"]),
            "branches": int(w["branches"])}


def net_entry(net: str, r: dict, d, proc, rails) -> dict:
    from .dspf import _verdict
    from .engine import METRICS
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
        # the metrics checked on this net: avg, rms and peak
        "metrics": list(r.get("metrics") or METRICS),
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
        # the resistances the IR is computed with (rcc: those simulated)
        "ir_resistance": ir_resistance(r, proc),
        "dynamic_mV": (float(r["dynamic"]["worst_V"]) * 1e3 if r.get("dynamic") else None),
        "kcl_residual_A": float(r.get("kcl_residual", 0.0)),
        # rcc: the window the averages are taken over
        "window": window_entry(r.get("window")),
        "metric_note": r.get("metric_note"),
        "worst": worst,
        "n_violations": len(clusters),
        "worst_items": [item(c, net) for c in clusters[:TOP_ITEMS]],
        "unmapped": r.get("unmapped") or [],
        # wires with no width: not checked, so the verdict is FAIL
        "no_width": r.get("no_width") or [],
        # vias whose cut count was not extracted (see dspf.via_cuts)
        "via_cuts": {
            "from_area": len((r.get("via_cuts") or {}).get("area", [])),
            "assumed_one_cut": (r.get("via_cuts") or {}).get("assumed", [])},
        # a net in pieces: what was not solved (None on a whole net)
        "unsolved": r.get("unsolved"),
        # rcc's dynamic droop, from the simulated branch currents
        "dynamic": dynamic_entry(r),
    }


def _limits_provenance(solved: dict, proc) -> dict | None:
    from .process_file import limits_provenance
    used = set()
    for r in solved.values():
        used |= set((r.get("layer_map") or {}).values())
    return limits_provenance(proc, used)


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
    if argv and overall in ("FAIL", "WARN") and not a.html and not a.netlist:
        base = strip_options(argv, {"--format": 1, "--html": 1, "--open": 0})
        out.append(step("see each net's map and worst segments in a browser, and the EM/IR "
                        "chart of every net on its layout (emir_chart.png)",
                        command_line("run", base, "--html", "emir_out/html", fmt)))
    if argv and overall == "FAIL":
        out.append(step("after widening the failing wires or adding cuts, re-extract "
                        "and re-run the same command to confirm",
                        "emir run " + shlex.join(argv)))
    return out


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
        # the metrics the run checked: avg, rms and peak
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
        # the run's own arguments and where it ran: `emir next --run-summary`
        # re-plans from them (`plan_summary.summary_args`)
        argv=list(getattr(a, "argv", None) or []),
        cwd=os.getcwd(),
    )
