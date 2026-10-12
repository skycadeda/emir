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
limits, vias counted as one cut, wires without a width, parts of a net in
pieces, what DC currents cannot show); the input checker's
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

from . import RCC_CONTRACT, RESEARCH_DISCLAIMER, SCREENING_NOTE
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
        msg = (f"{src} holds the error of a run that did not finish ({doc.get('code')}: "
               f"{doc.get('message')}), not a run summary; fix that and re-run")
        hint = ("The run that wrote this file failed: fix its error (the next step is "
                "that run's own) and run it again.")
        raise EmirError("EMIR-E104", msg, schema="run", hint=hint,
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


def _seconds(v) -> str:
    """A time in engineering units: `4 ns`, `12.5 ns`, `1.5 us`; `0 s`."""
    if v == 0:
        return "0 s"
    for unit, k in (("s", 1.0), ("ms", 1e-3), ("us", 1e-6), ("ns", 1e-9), ("ps", 1e-12),
                    ("fs", 1e-15)):
        if abs(v) >= k * 0.9995 or unit == "fs":
            return f"{v / k:.4g} {unit}"
    return f"{v:g} s"


def windows(s: dict) -> list:
    """rcc's averaging windows, one entry per distinct window (the same
    period and span) with the nets averaged over it (`nets`), the largest
    `change` among them and the branches that matter in all; `window` null at
    an operating point."""
    out = []
    for n in s.get("nets") or []:
        if n.get("mode") != "rcc":
            continue
        w = n.get("window")
        key = None if not w else tuple(w.get(k) for k in ("period_s", "n_periods", "t0_s",
                                                          "t1_s"))
        x = next((x for x in out if x["key"] == key), None)
        if x is None:
            out.append({"key": key, "nets": [n["net"]], "window": dict(w) if w else None})
            continue
        x["nets"].append(n["net"])
        if w:
            m = x["window"]
            m["change"] = max(m.get("change") or 0.0, w.get("change") or 0.0)
            m["branches"] = (m.get("branches") or 0) + (w.get("branches") or 0)
    return [{"nets": x["nets"], "window": x["window"]} for x in out]


def window_text(x: dict) -> str:
    """One window line of the report."""
    w, nets = x["window"], ", ".join(x["nets"])
    if not w:
        return f"Operating point (one time point: avg = rms = peak): {nets}"
    return (f"Window: the record's last {w['n_periods']} whole periods of "
            f"{_seconds(w['period_s'])} (--period), {_seconds(w['t0_s'])} to "
            f"{_seconds(w['t1_s'])} ({nets}); the last two agree within "
            f"{w['change'] * 100:.2f} % on the branches carrying 1 % of the net's largest "
            f"current. {RCC_CONTRACT[:-1]}")


def _few(names, n=6) -> str:
    names = list(names)
    return ", ".join(names[:n]) + (f" (+{len(names) - n} more)" if len(names) > n else "")


def build(s: dict, source: str, top: int = TOP) -> dict:
    """The `emir.report/1` document (without its `markdown`)."""
    from .engine import METRICS
    nets = []
    for n in s.get("nets") or []:
        mr = n.get("max_ratio") if isinstance(n.get("max_ratio"), dict) else {}
        nets.append({"net": n["net"], "verdict": n["verdict"], "mode": n["mode"],
                     "governing_metric": n.get("governing_metric"),
                     "metrics": list(n.get("metrics") or METRICS),
                     "worst_ratio": _ratio(n), "ir_drop_mV": _num(n.get("ir_drop_mV")),
                     # every layer's worst, failing or not: a passing layer's
                     # margin is the run's number, never one worked out by hand
                     "worst_wire": _num(mr.get("wire")), "worst_via": _num(mr.get("via")),
                     "ir_kind": n.get("ir_kind", "drop"), "power": bool(n.get("power"))})
    items = [it for n in s.get("nets") or [] for it in n.get("worst_items") or []]
    # a null ratio (not a number) is a failure, so it sorts first
    items.sort(key=lambda it: -(it["ratio"] if it.get("ratio") is not None
                                else float("inf")))
    src = {n["net"]: n.get("ir_resistance") for n in s.get("nets") or []}
    ir = [{"net": n["net"], "ir_drop_mV": n["ir_drop_mV"], "ir_kind": n["ir_kind"],
           "ir_resistance": src.get(n["net"])}
          for n in nets if n["power"]]
    # the dynamic droop beside the average one, where the run computed it
    # (rcc, from the simulated branch currents)
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
    return {"source": source, "verdict": s["verdict"],
            "disclaimer": SCREENING_NOTE,
            "research_disclaimer": RESEARCH_DISCLAIMER,
            "simulator_verification": s.get("simulator_verification"),
            "design": s.get("design"), "command": s.get("command"),
            "created": s.get("created"), "modes": sorted({n["mode"] for n in nets}),
            "current_source": s.get("current_source"), "windows": windows(s),
            "temp_C": s.get("temp_C"),
            "em_tref_C": s.get("em_tref_C"), "counts": s.get("counts"),
            "worst_net": s.get("worst_net"), "nets": nets, "worst_items": items[:top],
            "ir_by_power_net": ir, "not_checked": not_checked_lines(s),
            "input_warnings": warns, "run_warnings": run_warns, "html": s.get("html"),
            "charts": list(s.get("charts") or []),
            "process": s.get("process"), "dspf": s.get("dspf"),
            # where the limits came from, per layer and via (hand-entered ...)
            "limits_provenance": s.get("limits_provenance"),
            "limits": limits_lines(s.get("limits_provenance"))}


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
    if v.get("reason") in ("format not covered", "current kinds not covered"):
        word = ("partly verified" if v["status"] == "partly verified" else "NOT verified")
        kinds = v.get("current_kinds") or []
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
        never = [k["what"] for k in v.get("current_kinds") or [] if not k.get("verify_sim_covers")]
        return (f"**Simulator route: NOT verified on this machine** ({what}"
                + (f"; {why}" if why else "") + "): run "
                f"`{v.get('command') or 'emir verify-sim'}` before relying on these "
                f"numbers."
                + (f" It does not cover the run's {', '.join(never)}: those currents stay "
                   f"UNVERIFIED." if never else ""))
    note = v.get("note") or "the result does not say"
    return (f"**Simulator route: unknown simulator** ({note}): pass --sim to the run so its "
            f"on-site verification can be looked up.")


def at_text(it: dict) -> str:
    """Where a worst item is: the centre of its box, in um ("-" when the
    extraction gave no coordinates)."""
    b = it.get("bbox_um")
    if not b or len(b) != 4:
        return "-"
    return f"({(b[0] + b[2]) / 2:.3f}, {(b[1] + b[3]) / 2:.3f})"


def _dynamic_text(x: dict) -> str:
    """`; dynamic ... mV` for one power net, when the run computed it (rcc,
    from the simulated branch currents)."""
    dy = x.get("dynamic")
    if not dy:
        return ""
    where = f" at {dy['node']}" if dy.get("node") else ""
    return f"; dynamic {_x(x.get('dynamic_mV'), ' mV')}{where} (from the simulated branch currents)"


def _resistance_text(x: dict, temp) -> str:
    """`; R as simulated ...` for one power net of an rcc run: the IR is the
    simulated circuit's, at the temperature its resistances hold at."""
    b = x.get("ir_resistance") or {}
    if b.get("kind") != "simulated":
        return ""
    at = f"{b['temp_C']:g} C" + ("" if b.get("stated") else " (the process file's "
                                                          "temp_reference)")
    other = (f"; the EM limits are at {temp:g} C" if temp is not None
             and abs(float(temp) - float(b["temp_C"])) >= 0.5 else "")
    return f"; R as simulated: the DSPF's, at {at}{other}"


def markdown(r: dict) -> str:
    """The report as Markdown, for a person (or an agent to relay as is)."""
    c = r.get("counts") or {}
    wn = r.get("worst_net")
    L = [f"# EMIR screening report: {r.get('design') or 'design'}", "",
         f"**Verdict: {r['verdict']}** -- "
         f"{len(r['nets'])} net(s): {c.get('FAIL', 0)} FAIL, "
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
    L.append(f"- Currents: {cs.get('description', 'unknown')}"
             + (f" (`{cs['path']}`)" if cs.get("path") else ""))
    L += [f"- {window_text(x)}" for x in r.get("windows") or []]
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
        L.append(f"| {n['net']} | {n['verdict']} | {n['mode']} | "
                 f"{n['governing_metric'] or '-'} | {_x(n['worst_ratio'])} | "
                 f"{_x(n.get('worst_wire'))} | {_x(n.get('worst_via'))} | "
                 f"{_x(n['ir_drop_mV'], ' mV')} {n['ir_kind']} |")
    L += ["", "## Worst items", ""]
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
              + _dynamic_text(x) + _resistance_text(x, r.get("temp_C"))
              for x in r["ir_by_power_net"]]
    else:
        L.append("No power net among the analysed nets.")
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
                    help="the run summary (emir run --summary-out FILE); - reads stdin")
    ap.add_argument("--top", type=int, default=TOP,
                    help=f"how many worst items to list (default {TOP})")
    a = ap.parse_args(argv)
    sink = sink or Out("json" if a.format == "json" else "text")
    r = build(load_summary(a.src), a.src, top=max(a.top, 0))
    md = markdown(r)
    if a.format != "json":
        print(md, end="")
    sink.emit(document("report", **r, markdown=md))
    return 0
