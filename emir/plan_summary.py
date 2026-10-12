"""`emir next --run-summary FILE`: what follows a finished run. Auto-imported
by `plan.load_extensions`.

It reads the run's summary (`emir.run/1`; anything else is refused with
EMIR-E104 as `emir report` refuses it) and takes the run's inputs from it
(its DSPF, process file, result, net, period: from the summary's paths and
`argv`), so the command is just `emir next --run-summary FILE`. It plans no
new run of what the summary already holds: the report to relay, and what the
other features add after a run (the simulator's verification,
`plan_verify.py`).
"""
from __future__ import annotations

import shlex

from .output import command_line
from .plan import Facts, Plan, rule
from .waveform import REFUSALS as WINDOW_REFUSALS


def add_arguments(ap):
    ap.add_argument("--run-summary", default=None, metavar="FILE",
                    help="a finished run's summary (emir run --summary-out): plan what "
                         "follows it; the run's inputs are read from it")


#: the refusals of a run's averaging window: `emir next
#: --run-summary` plans their fix (the period, or a longer simulation)
REFUSALS = WINDOW_REFUSALS


def refused(path) -> dict | None:
    """`{"code", "message", "run"}` when `path` holds the error document a run
    refused for its window left at --summary-out (`emir.error/1` with one of
    `REFUSALS`, its inputs in `details.run`), else None."""
    import json
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("schema") != "emir.error/1" \
            or doc.get("code") not in REFUSALS:
        return None
    det = doc.get("details") if isinstance(doc.get("details"), dict) else {}
    return {"code": doc["code"], "message": doc.get("message"),
            "run": det.get("run") if isinstance(det.get("run"), dict) else {}}


def inputs(a) -> dict:
    """The feature's inputs; with --run-summary, the run's own inputs are
    filled into the options the person did not give (they are what the run
    used: `emir next --run-summary FILE` needs nothing else). A run refused for
    its window left its error there instead (`refused`): its inputs come from
    the error, and the plan is its fix."""
    out = {"run_summary": getattr(a, "run_summary", None)}
    if not out["run_summary"]:
        return out
    ref = refused(out["run_summary"])
    if ref:
        out["refused"] = {k: ref[k] for k in ("code", "message")}
        for k in ("dspf", "process", "cc_sim", "netlist", "net", "period", "sim"):
            v = ref["run"].get(k)
            if v is not None and hasattr(a, k) and getattr(a, k) in (None, False):
                setattr(a, k, v)
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
    return out


#: the options of `emir run` whose value names a file or a directory
_PATHS = {"--process", "--cc-sim", "--netlist", "--html", "--viewer", "--summary-out",
          "--json", "--dspf", "--currents", "--spectre", "--gds"}


def _abs_value(name: str, v: str, cwd: str) -> str:
    """One path value of `name` made absolute against `cwd` (inline JSON of
    --currents, an executable named without a directory, and absolute paths
    kept)."""
    import os
    if name == "--currents" and v.lstrip().startswith(("{", "[")):
        return v
    if name == "--spectre" and os.sep not in v:
        return v
    v2 = os.path.expanduser(v)
    return v if os.path.isabs(v2) else os.path.normpath(os.path.join(cwd, v2))


def resolve_argv(argv: list, cwd: str | None) -> list:
    """A run's `argv` with every path made absolute against the directory it
    ran in (`cwd`, the summary's): the DSPF (the first positional argument),
    the options in `_PATHS` (either form) and `@FILE` argument files. The
    commands `emir next --run-summary` builds from it then work from any
    directory. Unchanged without `cwd` (an older summary)."""
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
    `argv` and the parsed values of the options the plan reads back.
    Relative paths are made absolute against the directory the run ran in
    (the summary's `cwd`)."""
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
              "sim"):
        out[k] = getattr(ns, k, None)
    return out


@rule(61, "run-summary")
def _after_run(f: Facts, plan: Plan):
    """A finished run: its check, run and report are not planned again; the
    report to relay is, unless a later rule plans one."""
    path = f.get("run_summary")
    if not path:
        return
    ref = f.get("refused")
    if ref:
        # the run was refused for its window: the plan is its fix (the window
        # rule's longer simulation, or the period to ask for), then the run
        plan.note(f"the run that wrote {path} was refused ({ref['code']}): "
                  f"{ref['message']}")
        return
    plan.steps[:] = [x for x in plan.steps if x["id"] not in ("check", "run", "report",
                                                             "save-lines", "simulate",
                                                             "names-sidecar")]
    plan.result = None
    for k, x in enumerate(plan.steps, 1):
        x["n"] = k


@rule(95, "run-summary-report")
def _report(f: Facts, plan: Plan):
    path = f.get("run_summary")
    if not path or plan.has("report") or f.get("refused"):
        return
    plan.add("report", command_line("report", "--from", path),
             "the run's report to relay as it is: the verdict, the worst items with their "
             "fixes, what was not checked; it says screening, not sign-off")
