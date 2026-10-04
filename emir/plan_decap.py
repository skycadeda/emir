"""`emir next` rules for decap-aware EM (`emir/decap.py`): the EM verdict's
basis is the person's choice. Auto-imported by `plan.load_extensions`.

Before a run: `--dynamic` and `--em-basis` are `emir next` inputs, so the one
(possibly long) run is planned with them; a basis other than the default
(`em_basis.DEFAULT_BASIS`) makes the run step `needs_human`.

After a run: `emir next --run-summary FILE` reads the finished run's summary
(`emir.run/1`; anything else is refused with EMIR-E104 as `emir report`
refuses it) and takes the run's inputs from it (its DSPF, process file,
result, net, mode, period: from the summary's paths and `argv`), so the
command is just `emir next --run-summary FILE`. It plans no new run of what
the summary already holds. When the bases disagree it plans the person's
choice, `choose-em-basis` (`needs_human`, its `choices` carrying each
basis's verdict, worst ratio and violation count), whose command re-plans
with the choice: `emir next --run-summary FILE --em-basis <basis>`, which
plans the re-run with that basis (licensed and long-run flags from the run's
own arguments). A summary whose verdict is on a basis other than the default
gets a `confirm-em-basis` step: the agent never changes the basis itself. A
cc-grid run without --dynamic over nets with decap devices (EMIR-W428) gets
`rerun-with-dynamic`, the same run with --dynamic, as the person's step.
"""
from __future__ import annotations

import shlex

from .output import command_line, strip_options
from .plan import Facts, Plan, rule

#: the options of the run this feature sets (stripped before they are re-added)
_OWN = {"--em-basis": 1, "--dynamic": 0, "--format": 1}


def add_arguments(ap):
    from .em_basis import BASES, DEFAULT_BASIS
    ap.add_argument("--dynamic", action="store_true",
                    help="the person wants the dynamic droop and decap-aware EM (cc-grid, "
                         "--dynamic in the planned run)")
    ap.add_argument("--em-basis", default=None, choices=BASES,
                    help="the person's choice of EM verdict basis ("
                         + "; ".join(b + (", the default" if b == DEFAULT_BASIS else "")
                                     for b in sorted(BASES, key=lambda b: b != DEFAULT_BASIS))
                         + "), for the planned run or, with --run-summary, the re-run")
    ap.add_argument("--run-summary", default=None, metavar="FILE",
                    help="a finished run's summary (emir run --summary-out): plan what "
                         "follows it, the person's choice of EM basis when the bases "
                         "disagree; the run's inputs are read from it")


def inputs(a) -> dict:
    """The feature's inputs; with --run-summary, the run's own inputs are
    filled into the options the person did not give (they are what the run
    used: `emir next --run-summary FILE` needs nothing else)."""
    out = {"dynamic": bool(getattr(a, "dynamic", False)),
           "em_basis": getattr(a, "em_basis", None),
           "run_summary": getattr(a, "run_summary", None)}
    if not out["run_summary"]:
        return out
    from .report import load_summary
    s = load_summary(out["run_summary"])
    ra = summary_args(s)
    cs = s.get("current_source") or {}
    fill = {"dspf": s.get("dspf") or ra.get("dspf"),
            "process": s.get("process") or ra.get("process"),
            "cc_sim": ((cs.get("path") if cs.get("kind") == "simulation" else
                        cs.get("result")) or ra.get("cc_sim")),
            "netlist": ra.get("netlist"), "currents": ra.get("currents"),
            "net": ra.get("net"), "period": ra.get("period"), "sim": ra.get("sim")}
    if cs.get("kind") == "netlist":
        fill["netlist"] = cs.get("path") or fill["netlist"]
        fill["cc_sim"] = None
    for k, v in fill.items():
        if v is not None and hasattr(a, k) and getattr(a, k) in (None, False):
            setattr(a, k, v)
    modes = {n.get("mode") for n in s.get("nets") or []}
    mode = ra.get("mode") or (modes.pop() if len(modes) == 1 else None)
    if mode in ("rcc", "cc-grid") and getattr(a, "mode", None) in (None, "auto"):
        a.mode = mode
    return out


#: the options of `emir run` whose value names a file or a directory
_PATHS = {"--process", "--cc-sim", "--netlist", "--html", "--viewer", "--summary-out",
          "--json", "--taps-out", "--droop-file", "--port-voltages-out", "--dspf",
          "--currents", "--abstract", "--spectre"}


def _abs_value(name: str, v: str, cwd: str) -> str:
    """One path value of `name` made absolute against `cwd` (inline JSON of
    --currents, an executable named without a directory, and absolute paths
    kept)."""
    import os
    if name == "--currents" and v.lstrip().startswith(("{", "[")):
        return v
    if name == "--spectre" and os.sep not in v:
        return v
    if name == "--abstract" and "@" in v:
        f, _, inst = v.rpartition("@")
        return _abs_value("--abstract-file", f, cwd) + "@" + inst
    v2 = os.path.expanduser(v)
    return v if os.path.isabs(v2) else os.path.normpath(os.path.join(cwd, v2))


def resolve_argv(argv: list, cwd: str | None) -> list:
    """A run's `argv` with every path made absolute against the directory it
    ran in (`cwd`, the summary's): the DSPF (the first positional argument),
    the options in `_PATHS` (either form) and `@FILE` argument files. The
    re-run commands `emir next --run-summary` builds from it then work from
    any directory. Unchanged without `cwd` (an older summary)."""
    if not cwd:
        return list(argv)
    from .dspf import build_parser
    takes = {}
    for act in build_parser()._actions:
        for o in act.option_strings:
            takes[o] = 0 if act.nargs == 0 else 1
    out, want, seen_pos = [], None, False
    for tok in argv:
        if want is not None:
            out.append(_abs_value(want, tok, cwd) if want in _PATHS else tok)
            want = None
            continue
        if tok.startswith("@") and len(tok) > 1:
            out.append("@" + _abs_value("@", tok[1:], cwd))
            continue
        if tok.startswith("-") and tok != "-":
            name, eq, val = tok.partition("=")
            if eq:
                out.append(f"{name}={_abs_value(name, val, cwd)}" if name in _PATHS else tok)
            else:
                out.append(tok)
                if takes.get(name, 0):
                    want = name
            continue
        out.append(_abs_value("<dspf>", tok, cwd) if not seen_pos else tok)
        seen_pos = True
    return out


def summary_args(s: dict) -> dict:
    """The run's own options, from the summary's `argv` (or its command):
    `argv` and the parsed values of the options the plan reads back (a
    chip-level run's --abstract, --place and --align included). Relative
    paths are made absolute against the directory the run ran in (the
    summary's `cwd`)."""
    argv = s.get("argv")
    if argv is None and s.get("command"):
        argv = shlex.split(s["command"])[2:]
    argv = resolve_argv(list(argv or []), s.get("cwd"))
    out = {"argv": list(argv or [])}
    if not argv:
        return out
    from .dspf import build_parser
    from .errors import EmirError
    from .output import expand_argfiles
    try:
        ns, _rest = build_parser().parse_known_args(expand_argfiles(list(argv)))
    except (EmirError, SystemExit, OSError):
        return out
    for k in ("dspf", "process", "cc_sim", "netlist", "currents", "net", "period", "mode",
              "sim", "vdd", "self_consistent", "dynamic", "em_basis", "abstract", "place",
              "align", "port_voltages_out"):
        out[k] = getattr(ns, k, None)
    return out


def _needs_dynamic(b) -> bool:
    from .em_basis import NEEDS_DYNAMIC
    return b in NEEDS_DYNAMIC


@rule(61, "em-basis")
def _em_basis(f: Facts, plan: Plan):
    if f.get("run_summary"):
        _after_run(f, plan)
        return
    dyn, b = f.get("dynamic"), f.get("em_basis")
    if not (dyn or b):
        _decaps_note(f, plan)
        return
    if f.get("abstracts") and (dyn or _needs_dynamic(b)):
        # a chip-level run has no dynamic solve: refused (EMIR-E811, EMIR-E430),
        # and plan_hierarchy says so in missing[] and plans no check or run
        return
    from .decap import DEFAULT_BASIS
    mode = f.get("mode")
    if mode == "rcc":
        plan.note("--mode rcc: the RC simulation drives the decaps and measures every "
                  "branch current, so the EM basis does not apply (the summary says "
                  "`used: measured`)")
        return
    if f.get("currents"):
        plan.note("DC currents have no time base: the EM verdict is the static one "
                  "(decap-free); --dynamic and a decap-aware basis need a simulation result")
        return
    extra = []
    if dyn or _needs_dynamic(b):
        extra.append("--dynamic")
        if not dyn:
            plan.note(f"--em-basis {b} needs the dynamic solve: --dynamic is added to the "
                      f"planned run")
    if b:
        extra += ["--em-basis", b]
    # decap-aware EM needs a window of whole periods: without --period the run
    # never has one (EMIR-W433), so the period is the person's to give
    period = [] if (f.get("period") or "--dynamic" not in extra) else ["--period", "<period>"]
    planned = False
    for s in plan.steps:
        if s["id"] == "choose-mode":
            # the person's --dynamic and --em-basis stay in the plan of the
            # route they choose (the chosen route is re-planned with --mode)
            s["command"] = _with(s["command"], extra)
            for c in s.get("choices") or []:
                c["command"] = _with(c["command"], extra)
        if s["id"] in ("check", "run") and s["command"].startswith("emir "):
            s["command"] = _with(s["command"], extra + period)
            planned = True
            if period:
                from .plan import PLACEHOLDER
                s["placeholders"] = sorted(set(PLACEHOLDER.findall(s["command"])))
                s["status"] = "waiting"
            if s["id"] == "run" and b and b != DEFAULT_BASIS:
                s["needs_human"] = True
                from .em_basis import not_default
                s["why"] += f"; {not_default(b)}: only on the person's choice"
    if period and planned:
        plan.need("the block's period (--period)",
                  "--dynamic's decap-aware EM needs a window of whole periods of the block's "
                  "activity: without --period the run has none, and computes no decap-aware "
                  "EM (EMIR-W433), its verdict decap-free",
                  "ask the person for the period of the block's activity (in s) and put it "
                  "in place of <period>; never guess it from the waveforms")
    if "--dynamic" not in extra and f.get("abstracts"):
        plan.note(f"--em-basis {b}: a chip-level run (--abstract) has no dynamic solve "
                  f"(--dynamic does not combine with --abstract, EMIR-E811), so its verdict "
                  f"is the static one, decap-free; a block's decap-aware EM comes from a run "
                  f"of the block's own DSPF and CC result with --dynamic")
        return
    if "--dynamic" not in extra:
        plan.note(f"--em-basis {b} without --dynamic: the run has the static solve only, "
                  f"so its verdict is decap-free, which gives a decap's straps nothing; "
                  f"--dynamic would add decap-aware EM (the person's choice)")
        return
    from .em_basis import SHORT
    plan.note(f"--dynamic also gives decap-aware EM: every basis's verdict is in the "
              f"summary, and the verdict uses {b or DEFAULT_BASIS}"
              + (f" (the default: {SHORT[DEFAULT_BASIS]})"
                 if not b else " (the person's choice)")
              + ". After the run, `emir next --run-summary emir_out/summary.json` shows "
                "the bases side by side; the basis is the person's choice, never yours. "
                "Should decap-aware EM not be computed on a net (em_basis.honoured "
                "false), the verdict there is decap-free: plan from the summary, never "
                "drop --period or add --ground or a smaller --dyn-steps to get a pass")


def _decaps_note(f: Facts, plan: Plan):
    """A cc-grid run planned without --dynamic over nets with decap devices:
    its verdict is the static one, which gives a decap's straps nothing. An
    advice line, not a step: --dynamic costs a transient solve, so it is the
    person's choice (`long_run` when the block is big)."""
    if f.get("mode") == "rcc" or f.get("currents") or not f.dspf_info.get("ok") \
            or f.get("abstracts"):
        # (a chip-level run has no dynamic solve: --dynamic is refused with
        # --abstract, EMIR-E811)
        return
    from .decap import DEFAULT_BASIS, decap_devices
    d = f.dspf_info["_dspf"]
    have = {n: decap_devices(d, n) for n in f.target_nets()}
    have = {n: k for n, k in have.items() if k}
    if not have:
        return
    big = f.size()["class"] == "big"
    plan.note(f"{', '.join(f'{n} ({k} decap device(s))' for n, k in list(have.items())[:4])}: "
              f"a cc-grid run without --dynamic gives the static verdict (decap-free), "
              f"which draws every edge of the load from the feed and gives a decap's straps "
              f"nothing (EMIR-W428 on the run). --dynamic adds decap-aware EM, which covers "
              f"the straps, with every basis's verdict (the default, {DEFAULT_BASIS}, decides "
              f"unless the person chooses another): ask the person"
              + (" (on a block this big it is a long run)" if big else "")
              + "; it needs a transient result over whole periods (--period)")


def _with(cmd: str, extra: list) -> str:
    """`cmd` with this feature's options set to `extra` (keeping its --format)."""
    parts = shlex.split(cmd)
    fmt = parts[parts.index("--format"):parts.index("--format") + 2] \
        if "--format" in parts else []
    return shlex.join(strip_options(parts, _OWN) + list(extra) + fmt)


def _after_run(f: Facts, plan: Plan):
    """What follows a finished run: no new run of what its summary holds,
    the person's choice of basis where the bases disagree, the re-run with
    the basis they chose."""
    from .decap import BASIS_TEXT, DEFAULT_BASIS, report_fields
    from .report import load_summary
    path = f.get("run_summary")
    s = load_summary(path)
    ra = summary_args(s)
    # the run is done: its check, run and report are not planned again
    plan.steps[:] = [x for x in plan.steps if x["id"] not in ("check", "run", "report",
                                                             "save-lines", "simulate",
                                                             "names-sidecar")]
    plan.result = None
    fields = report_fields(s, path, fmt=f.fmt())
    per = fields.get("em_bases") or {}
    computed = {n: x for n, x in per.items() if x.get("computed")}
    want = f.get("em_basis")
    top = s.get("em_basis") or {}
    used = {x["em_basis"]["used"] for x in per.values()}
    chip = bool(ra.get("abstract"))
    if not per and chip:
        # --dynamic does not combine with --abstract (EMIR-E811): no re-run
        # with it is advised
        plan.note(f"{path}: a chip-level run (--abstract) has no dynamic solve (--dynamic "
                  f"does not combine with --abstract, EMIR-E811): its verdict is the static "
                  f"one (decap-free). A block's dynamic droop is in its abstract; its "
                  f"decap-aware EM comes from a run of the block's own DSPF and CC result "
                  f"with --dynamic")
    elif not per:
        plan.note(f"{path}: the run computed no decap-aware EM (no --dynamic): its verdict "
                  f"is the static one (decap-free). For every basis, re-run it with "
                  f"--dynamic (cc-grid, a transient result with capacitance on the rails)")
    for n, x in per.items():
        if not x.get("computed") and x["em_basis"]["used"] != "measured":
            eb = x["em_basis"]
            plan.note(f"{n}: decap-aware EM was not computed ({eb.get('fallback')}: "
                      f"{eb.get('fallback_reason')}); its verdict is decap-free")
    if top.get("honoured") is False and not want:
        _fix_fallbacks(f, plan, s, ra, top)
    no_dyn = [n for n, x in per.items() if x["em_basis"].get("fallback") == "no-dynamic"]
    if no_dyn and not want and not chip:
        _add_dynamic(f, plan, ra, no_dyn,
                     w428="EMIR-W428" in {x.get("code") for x in s.get("notices") or []})
    if want:
        if want in used and len(used) == 1:
            plan.note(f"the run's verdict already uses --em-basis {want}: nothing to re-run")
        elif not (chip and _needs_dynamic(want)):
            # (with --abstract a basis that needs --dynamic is refused,
            # EMIR-E811 and E430: plan_hierarchy says so in missing[])
            _rerun(f, plan, ra, want)
    elif computed:
        b = {k: {kk: v for kk, v in x["bases"].items()} for k, x in computed.items()}
        if fields.get("em_choices") and any(x.get("disagree") for x in computed.values()):
            ch = fields["em_choices"]
            line = "; ".join(f"{c['basis']} {c['verdict']} {_r(c['max_ratio'])}" for c in ch)
            plan.add("choose-em-basis",
                     command_line("next", "--run-summary", path, "--em-basis", "<basis>",
                                  f.fmt()),
                     f"the EM bases disagree ({line}); which one decides the verdict is the "
                     f"person's choice, never yours and never to make a FAIL pass. Show the "
                     f"person `choices` (what each basis is, its verdict, worst ratio and "
                     f"violations), say {DEFAULT_BASIS} is the default, ask, and run this "
                     f"with <basis> replaced by their choice (the `command` of the choice "
                     f"they pick)", needs_human=True)
            st = plan.steps.pop()
            st["choices"] = ch
            plan.steps.insert(0, st)
            for k, x in enumerate(plan.steps, 1):
                x["n"] = k
        else:
            unsized = [n for n, x in computed.items()
                       if x["em_basis"].get("decaps_not_sized")]
            plan.note("the EM bases agree on every net"
                      + (f" (without the decaps cap_models cannot size on "
                         f"{', '.join(unsized)}, whose straps no basis checked)"
                         if unsized else "") + ": "
                      + "; ".join(f"{n}: " + ", ".join(
                          f"{bb} {y['verdict']} {_r(y['worst_ratio'])}"
                          for bb, y in v.items()) for n, v in b.items()))
        if top.get("source") == "option" and top.get("requested") != DEFAULT_BASIS:
            plan.add("confirm-em-basis", command_line("report", "--from", path),
                     f"the run's verdict uses --em-basis {top.get('requested')}, not the "
                     f"default ({DEFAULT_BASIS}): {BASIS_TEXT[top.get('requested')]}. Confirm "
                     f"with the person that they chose it, and relay the default's verdict "
                     f"beside it (the report gives every basis)", needs_human=True)
    if not want:
        _loop_steps(f, plan, s, ra)
    if not plan.has("report") and not plan.has("confirm-em-basis") and not want:
        plan.add("report", command_line("report", "--from", path),
                 "the run's report to relay as it is: the verdict, the EM basis it is on and "
                 "every basis's verdict; it says screening, not sign-off")


def _loop_steps(f: Facts, plan: Plan, s: dict, ra: dict):
    """What follows a self-consistent run, from how each net's loop ended
    (`nets[].self_consistency`), as the run's own `next_steps` say it (an
    agent over MCP takes `emir next`'s steps, not the run's): the same run
    with more passes (the pass limit, or converged on the last allowed one)
    or longer passes (a pass's time limit); and, where the loop fed back the
    RC grid's droop and scored the branches on the R-only grid (EMIR-W545),
    the same run with --dynamic and the block's period. Each re-simulates:
    the person's step."""
    from types import SimpleNamespace

    from .summary import SC_DYNAMIC_WHY, _sc_retry_step, sc_dynamic_wanted
    argv = list(ra.get("argv") or [])
    if not argv:
        return
    a = SimpleNamespace(argv=argv, dynamic=bool(ra.get("dynamic")), period=ra.get("period"))
    sim = ra.get("sim") or "spectre"
    licensed = sim if sim in ("spectre", "hspice") else None
    dyn_nets = []
    for n in s.get("nets") or []:
        sc = n.get("self_consistency")
        if not sc:
            continue
        net = n.get("net")
        st = _sc_retry_step(a, net, sc, f.fmt())
        if st and not plan.has("rerun-loop"):
            plan.add("rerun-loop", st["command"], st["why"], licensed_tool=licensed,
                     long_run=True)
        if sc_dynamic_wanted(a, sc):
            dyn_nets.append(net)
    if dyn_nets and not plan.has("rerun-with-dynamic"):
        base = strip_options(argv, {"--dynamic": 0, "--format": 1})
        extra = ["--dynamic"]
        if _value(base, ("--period",)) is None:
            extra += ["--period", "<period>"]
            plan.need("the block's period", f"{', '.join(dyn_nets)}: --dynamic's decap-aware "
                      f"EM needs a window of whole periods (EMIR-W433)", "ask the person for "
                      "the period of the block's activity (in s) and put it in place of "
                      "<period>")
        plan.add("rerun-with-dynamic", command_line("run", base, extra, f.fmt()),
                 "; ".join(SC_DYNAMIC_WHY.format(net=n) for n in dyn_nets),
                 licensed_tool=licensed, long_run=True)


def _add_dynamic(f: Facts, plan: Plan, ra: dict, nets: list, w428: bool = True):
    """A cc-grid run without --dynamic over nets with decap devices (EMIR-W428,
    or a run whose --em-basis the person gave without --dynamic): its
    verdict is the static one, which gives a decap's straps nothing. The
    same run with --dynamic, as the person's step: it costs a transient solve
    (and a window of whole periods), so it is theirs to choose, never a fix.
    A basis the run was given stays, and the reason says so; W428 is cited
    only when the run raised it (`w428`)."""
    from .decap import DEFAULT_BASIS
    argv = list(ra.get("argv") or [])
    if not argv:
        from .plan import run_args
        argv = [str(x) for x in run_args(f)]
    # the person's --em-basis, if they gave one, stays
    base = strip_options(argv, {"--dynamic": 0, "--format": 1})
    extra = ["--dynamic"]
    if _value(base, ("--period",)) is None:
        extra += ["--period", "<period>"]
        plan.need("the block's period", f"{', '.join(nets)}: decap-aware EM needs a window "
                  f"of whole periods (EMIR-W433)", "ask the person for the period of the "
                  "block's activity (in s) and put it in place of <period>")
    sim = ra.get("sim") or "spectre"
    licensed = sim if (ra.get("netlist") and sim in ("spectre", "hspice")) else None
    long = bool(licensed or ra.get("netlist") or ra.get("self_consistent")
                or f.size()["class"] == "big")
    kept = _value(base, ("--em-basis",))
    plan.add("rerun-with-dynamic", command_line("run", base, extra, f.fmt()),
             f"{', '.join(nets)}: the run had no --dynamic, so its verdict is the static one "
             f"(decap-free) and the decaps' straps are not checked"
             + (" (EMIR-W428)" if w428 else "") + ". The same run with --dynamic adds "
             "decap-aware EM and every basis's verdict; "
             + (f"the verdict stays on --em-basis {kept} (the person's choice)" if kept
                else f"the verdict is then on the default basis ({DEFAULT_BASIS})")
             + "; it costs a transient solve over whole periods: the person's choice, ask "
               "first",
             needs_human=True, licensed_tool=licensed, long_run=long)


def _value(argv: list, names) -> str | None:
    """The value of the first of the options `names` in `argv` (either form)."""
    for i, t in enumerate(argv):
        name, eq, val = t.partition("=")
        if name in names:
            return val if eq else (argv[i + 1] if i + 1 < len(argv) else None)
    return None


def _r(v) -> str:
    return "not a number" if v is None else f"{v:.3f}x"


def _rerun(f: Facts, plan: Plan, ra: dict, basis: str):
    """The same run with the person's basis: its own arguments, --em-basis
    replaced, --dynamic where the basis needs it, its summary file kept."""
    from .decap import DEFAULT_BASIS
    argv = list(ra.get("argv") or [])
    if not argv:
        # the summary records neither `argv` nor `command`: the run's inputs
        # it does record (DSPF, process file, result, net, mode, period)
        from .plan import run_args
        argv = [str(x) for x in run_args(f)]
        plan.note(f"{f.get('run_summary')} records neither `argv` nor `command`: the re-run "
                  f"is built from the inputs it records; add any other option the run had")
    base = strip_options(argv, _OWN)
    extra = ["--dynamic"] if (_needs_dynamic(basis) or ra.get("dynamic")) else []
    extra += ["--em-basis", basis]
    out = _value(base, ("--summary-out", "--json"))
    if out is None:
        out = "emir_out/summary.json"
        extra += ["--summary-out", out]
    sim = ra.get("sim") or "spectre"
    licensed = sim if (ra.get("netlist") and sim in ("spectre", "hspice")) else None
    long = bool(licensed or ra.get("netlist") or ra.get("self_consistent")
                or f.size()["class"] == "big")
    computed = any(n.get("dynamic_em") for n in s_nets(f))
    why = (f"the same run with --em-basis {basis}, the person's choice: "
           + (f"every basis was computed already, this makes {basis} the verdict's (and "
              f"--fail-on-violation's)" if computed else
              f"decap-aware EM was not computed on the run (its summary says why), so "
              f"this run computes it again with {basis} deciding"))
    if ra.get("netlist") or ra.get("self_consistent"):
        why += ("; it simulates again (" + (f"{licensed}, licensed" if licensed else sim)
                + (", once per pass of the self-consistent loop" if ra.get("self_consistent")
                   else "") + ")")
    if basis != DEFAULT_BASIS:
        from .em_basis import not_default
        why += f"; {not_default(basis)}: confirm the person chose it"
    plan.add("run", command_line("run", base, extra, f.fmt()), why,
             needs_human=basis != DEFAULT_BASIS, licensed_tool=licensed, long_run=long)
    plan.add("report", command_line("report", "--from", out),
             "the report of the re-run to relay: the verdict on the chosen basis and every "
             "basis's verdict beside it", after=["run"])


def s_nets(f: Facts) -> list:
    """The nets of the summary `emir next --run-summary` reads."""
    from .report import load_summary
    try:
        return load_summary(f.get("run_summary")).get("nets") or []
    except Exception:  # noqa: BLE001 -- already refused by inputs() if unreadable
        return []


def _fix_fallbacks(f: Facts, plan: Plan, s: dict, ra: dict, top: dict):
    """The run asked for a basis that needs decap-aware EM and a net could not
    have it (`em_basis.honoured` false): its verdict is decap-free there,
    which the person did not choose. What would compute it, each the person's
    step (a re-run; a new simulation where the window is the cause)."""
    argv = list(ra.get("argv") or [])
    for x in top.get("not_computed") or []:
        net, why = x["net"], x["fallback"]
        if why == "step-too-coarse" and x.get("dyn_steps") and argv:
            base = strip_options(argv, {"--dyn-steps": 1, "--format": 1})
            plan.add("rerun-dyn-steps", command_line("run", base, "--dyn-steps",
                                                     x["dyn_steps"], f.fmt()),
                     f"{net}: decap-aware EM was not computed, the step too coarse "
                     f"(EMIR-W434), so the verdict there is decap-free, not the "
                     f"{top.get('requested')} basis asked for: the same run with the "
                     f"--dyn-steps that resolves it (longer; the person's go-ahead)",
                     needs_human=True, long_run=x["dyn_steps"] > 200_000)
        elif why == "step-too-coarse":
            plan.need(f"a decision on {net}'s narrowest waveform feature",
                      f"{net}: a waveform feature too narrow for any practical --dyn-steps, "
                      f"and not negligible (EMIR-W434), so decap-aware EM was not computed",
                      "ask the person whether the feature is real (a simulator's glitch is "
                      "common) and how to treat it; the run's notice gives its numbers")
        elif why == "reference-not-feed" and argv:
            plan.add("rerun-without-ground",
                     command_line("run", strip_options(argv, {"--ground": 1, "--format": 1}),
                                  f.fmt()),
                     f"{net}: --ground moved the reference off the feed (EMIR-W431), so "
                     f"decap-aware EM was not computed and the verdict there is decap-free: "
                     f"the same run without --ground (the person's go-ahead)",
                     needs_human=True)
        elif why == "window-not-periodic" and argv:
            base = strip_options(argv, {"--period": 1, "--format": 1})
            plan.add("rerun-with-period", command_line("run", base, "--period", "<period>",
                                                       f.fmt()),
                     f"{net}: the window is not whole periods (EMIR-W433), so decap-aware EM "
                     f"was not computed and the verdict there is decap-free: the same run "
                     f"with the block's period, which only the person knows",
                     needs_human=True)
            plan.need("the block's period", f"{net}: decap-aware EM needs a window of whole "
                      f"periods (EMIR-W433)", "ask the person for the period of the block's "
                      "activity (in s) and put it in place of <period>")
        elif why == "not-settled":
            plan.need("more periods of activity", f"{net}: the RC state did not reach periodic "
                      f"steady state (EMIR-W432)", "ask the person to simulate more periods "
                      "and re-run on the longer result")
        elif why == "no-feed":
            plan.need(f"a port on {net}", f"{net} has no port (*|P), so decap-aware EM has no "
                      f"feed (EMIR-W430)", "ask the person whether the net has a supply "
                      "port the extraction left out")
        elif why == "harmonic":
            plan.need("a transient result", f"{net}: the result holds no time-domain "
                      f"waveforms", "ask the person for a transient (or PSS time-domain) "
                      "result over whole periods")
        elif why == "pieces":
            plan.need(f"{net} in one piece", f"{net} is in pieces (EMIR-E207)",
                      "ask the person to check the extraction (`emir check` names the pieces)")
        elif why == "decaps-not-sized":
            models = ", ".join(x.get("models") or []) or "the decaps' models"
            plan.need(f"the capacitance of cap model {models}",
                      f"{net}: {x.get('fallback_reason') or 'decaps cap_models cannot size'} "
                      f"(EMIR-W409), so the {top.get('requested')} basis does not cover "
                      f"their straps",
                      f"ask the person for {models}'s cox (F/m^2, a MOS decap) or cj and "
                      f"cjsw (a capacitor) from the PDK's decap characterisation, with its "
                      f"source; they enter it in the process file (never estimate it), then "
                      f"`emir process check` and the same run again")
    lost = [x for x in top.get("not_computed") or [] if x["fallback"] != "decaps-not-sized"]
    unsized = [x for x in top.get("not_computed") or [] if x["fallback"] == "decaps-not-sized"]
    if lost:
        plan.note(f"the run asked for EM basis {top.get('requested')} and could not compute it "
                  f"on " + ", ".join(f"{x['net']} ({x['fallback']})" for x in lost)
                  + ": the verdict there is decap-free, which does not cover a decap's "
                    "straps. Tell the person; never present it as a pass of the basis asked "
                    "for")
    if unsized:
        plan.note(f"the run's EM basis {top.get('requested')} left out the decaps cap_models "
                  f"cannot size on " + ", ".join(x["net"] for x in unsized)
                  + ": their straps are not checked. Tell the person; never present it as a "
                    "pass of the basis asked for")
