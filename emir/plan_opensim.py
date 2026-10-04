"""`emir next` rules for the simulators `emir simulate` drives: Xyce, ngspice
and HSPICE.

With no Spectre (and no Cadence tools) but Xyce or ngspice found -- or HSPICE
found or asked for with --sim hspice -- the base plan's simulation is in a
simulator's own syntax that the person would have to translate, and its
result would need a names sidecar nobody writes. These rules turn it into
commands that run as printed (HSPICE is licensed: its simulate step is
planned twice, without --run -- the deck and the command, nothing runs --
then with --run, which needs the person's go-ahead):

* the simulation is `emir simulate` -- the deck with the print / save lines,
  the run, the ASCII result and its `<result>.names` sidecar -- writing
  `emir_out/sim/result.raw`, which the check and the run then read;
* `--netlist TB` (which the base plan reserves for Spectre) is the
  testbench `emir simulate` runs; it is read to choose the mode (the RC
  netlist -> rcc, a CC netlist -> cc) and whether the devices need probes
  (a subcircuit pin's current is printable by no simulator -> `--probe`);
* without a testbench the simulate step waits for one (`<testbench.cir>`);
* a finished result with Xyce / ngspice names and no sidecar gets the
  `emir save-lines ... --names-out <result>.names` that maps it, when the
  names are the ones emir's own lines produce.
"""
from __future__ import annotations

import os

from .errors import EmirError
from .output import command_line
from .plan import Facts, Plan, rule

#: where the plan's simulation writes its result
RESULT = os.path.join("emir_out", "sim", "result.raw")
#: HSPICE names its result <stem>.tr0
RESULT_TR0 = os.path.join("emir_out", "sim", "result.tr0")


def open_sim(f: Facts) -> str | None:
    """The simulator the plan runs through `emir simulate`: the one --sim
    names (hspice, xyce, ngspice); else None when Spectre or the Cadence
    tools are there (the base plan's Spectre route); else HSPICE if found,
    then Xyce, then ngspice; None when none is."""
    want = f.get("sim")
    if want in ("hspice", "xyce", "ngspice"):
        return want
    if want == "spectre":
        return None
    t = f.tools
    if t["spectre"]["found"] or t["have_cadence"]:
        return None
    if t["hspice"]["found"]:
        return "hspice"
    for s in ("xyce", "ngspice"):
        if t[f"have_{s}"]:
            return s
    return None


def result_path(sim: str) -> str:
    return RESULT_TR0 if sim == "hspice" else RESULT


@rule(45, "opensim-testbench")
def _testbench(f: Facts, plan: Plan):
    """--netlist without Spectre: with an open simulator it is the testbench
    `emir simulate` runs. The base rule would stop the plan ("Spectre, for
    --netlist"), so it is set aside here and put back by `_opensim`."""
    if f.get("netlist") and not (f.get("cc_sim") or f.get("currents")) and open_sim(f):
        f._cache["opensim_tb"] = f.inputs["netlist"]
        f.inputs["netlist"] = None


def _tb_facts(f: Facts, tb: str, d) -> dict:
    """What the testbench says: `{"ok", "error", "rc", "kinds", "tran",
    "scope", "path"}` (rc: it holds every resistor of the target nets)."""
    from .opensim import scope_of, scope_path
    from .opensim_netlist import Tree
    try:
        tree = Tree(tb)
        scope, _ = scope_of(tree, d)
        path, _ = scope_path(tree, scope, None, command="next")
    except EmirError as e:
        return {"ok": False, "error": f"{e.code}: {e.message}", "code": e.code}
    names = tree.element_names(scope)
    res = [r["name"].upper() for n in f.target_nets() for r in d.net_resistors(n)]
    return {"ok": True, "error": None, "tran": bool(tree.analyses),
            "rc": bool(res) and all(r in names for r in res),
            "kinds": {e.name.upper(): e.kind for e in tree.elements(scope)},
            "scope": scope, "path": path}


def _needs_probe(f: Facts, d, kinds: dict | None, sim: str = "xyce") -> bool:
    """cc: some instance pin's current is not printable directly (from the
    testbench's element letters, else the DSPF instance names' letters)."""
    from .opensim import save_set
    nets = [n for n in f.target_nets() if d.net_taps(n)]
    if not nets:
        return False
    try:
        ss = save_set(d, nets, "cc", sim, inst_kinds=kinds)
    except EmirError:
        return False
    return bool(ss.unprintable)


@rule(55, "opensim")
def _opensim(f: Facts, plan: Plan):
    tb = f._cache.pop("opensim_tb", None)
    if tb is not None:
        f.inputs["netlist"] = tb
    sim = open_sim(f)
    if not sim or not f.dspf_info.get("ok"):
        return
    if f.get("cc_sim") and plan.has("names-sidecar"):
        _map_result(f, plan, sim)
        return
    if not plan.has("simulate"):
        return
    d = f.dspf_info["_dspf"]
    modes = {_mode_of(s["command"]) for s in plan.steps if s["id"].startswith("save-lines")}
    kind = "rcc" if "rcc" in modes else "cc"
    req = f.get("mode")
    tbf = None
    if tb:
        tbf = _tb_facts(f, tb, d)
        if not tbf["ok"]:
            plan.need("a readable testbench", f"--netlist {tb}: {tbf['error']}",
                      "fix it (`emir explain " + tbf["code"] + "`)")
            plan.blocked = True
            _drop(plan, ("save-lines", "simulate", "names-sidecar"))
            return
        if not tbf["tran"]:
            plan.need("a transient analysis in the testbench",
                      f"{tb} has no .tran (EMIR-E462): EM needs the currents over time",
                      "add a .tran over whole periods of the block's activity")
            plan.blocked = True
            _drop(plan, ("save-lines", "simulate", "names-sidecar"))
            return
        # the person's mode decides what is saved; the testbench must be able
        # to give it (rcc: it simulates every resistor of the nets)
        if req == "rcc" and not tbf["rc"]:
            nets = f.target_nets()
            plan.need("the RC netlist in the testbench",
                      f"{tb} does not hold every resistor of {', '.join(nets[:6])}, so it "
                      f"cannot give the branch currents rcc needs",
                      "include the RC netlist (the DSPF) in the testbench in place of the "
                      "CC netlist, or ask the person about --mode cc-grid")
            plan.blocked = True
            _drop(plan, ("save-lines", "simulate", "names-sidecar"))
            return
        kind = "rcc" if req == "rcc" else "cc"
        plan.mode = {"chosen": req,
                     "why": f"--mode {req} given (the person's choice): "
                            + ("the testbench simulates the RC netlist, every branch current "
                               "is printed and read directly" if kind == "rcc" else
                               "the testbench's instance-pin currents are superposed "
                               "through the R grid")}
    probe = kind == "cc" and _needs_probe(f, d, tbf["kinds"] if tbf else None, sim)
    net = f.get("net")
    res = result_path(sim)
    licensed = sim == "hspice"
    ph = "<testbench.sp>" if licensed else "<testbench.cir>"
    rmode = "rcc" if kind == "rcc" else "cc-grid"
    args = [tb or ph, "--sim", sim, "--dspf", f.get("dspf"),
            *(["--net", net] if net and net != "all" and "," not in net else []), "--mode", rmode,
            *(["--probe"] if probe else []),
            *(["--process", f.get("process")] if f.get("process") else []),
            "--out", res, f.fmt()]
    big = f.size()["class"] != "small"
    what = ("the RC netlist (every branch current printed)" if kind == "rcc"
            else "the CC netlist (each instance pin's current"
            + (", through a 0 V probe in series with it" if probe else "") + ")")
    manual = command_line("save-lines", f.get("dspf"),
                          *(["--net", net] if net and net != "all" and "," not in net else []),
                          "--mode", rmode,
                          "--sim", sim, "--names-out", res + ".names")
    lines = {"xyce": ".print", "ngspice": ".save", "hspice": ".probe"}[sim]
    _drop(plan, ("save-lines", "names-sidecar"))
    i = next(k for k, s in enumerate(plan.steps) if s["id"] == "simulate")
    del plan.steps[i]
    replace = ("; replace " + ph + " with the block's testbench (stimulus, models, "
               ".tran, and the " + ("DSPF" if kind == "rcc" else "CC netlist") + ")"
               if not tb else "")
    if licensed:
        plan.add("simulate-plan", command_line("simulate", *args),
                 f"write the {sim} deck with the {lines} lines (and the ASCII output "
                 f"option) and {res}.names, and print the exact command: nothing runs, no "
                 f"licence is used" + replace)
        plan.add("simulate", command_line("simulate", *args, "--run"),
                 f"simulate {what} with {sim} (licensed: ask the person first), read the "
                 f"result back and check the currents" + replace,
                 licensed_tool=sim, long_run=True, after=["simulate-plan"])
    else:
        plan.add("simulate", command_line("simulate", *args),
                 f"simulate {what} with {sim}: emir writes the deck with the {lines} "
                 f"lines, runs it to an ASCII result and writes {res}.names, which maps "
                 f"{sim}'s names to the DSPF's" + replace,
                 long_run=big)
    moved = [plan.steps.pop() for _ in range(2 if licensed else 1)][::-1]
    for k, st in enumerate(moved):
        plan.steps.insert(i + k, st)
    _renumber(plan)
    plan.result = res
    if tb:
        # nothing is missing: the simulate step makes the result
        plan.missing[:] = [m for m in plan.missing if m["what"] != "a simulation result"]
    for m in plan.missing:
        if m["what"] == "a simulation result":
            # the simulator is one route; DC currents the person already has are
            # the other (the base rule said so, this text must too)
            m["how"] = (f"the `simulate` step writes {res} and its names sidecar; "
                        f"the check and the run read it -- or, with DC currents for the "
                        f"net, re-plan with --currents JSON (no simulation)")
    if not tb:
        plan.need(f"a {sim} testbench of the block",
                  "the stimulus that makes the block draw its real currents (supplies, "
                  "inputs, clocks), the device models and a .tran over whole periods",
                  f"write it as SPICE that includes the {'DSPF (the RC netlist)' if kind == 'rcc' else 'CC netlist (devices; a C-only extraction or the schematic)'}"
                  f", then pass it with --netlist to plan with it")
    if not f.tools[sim]["found"]:
        plan.note(f"{sim} is not found on this machine (`emir info`): the simulate step "
                  f"needs it (${sim.upper()}_BIN, the site file or $PATH)")
    run_by_hand = {"xyce": "Xyce -a <deck>", "ngspice": f"ngspice -b -r {res} <deck>",
                   "hspice": f"hspice -i <deck> -o {res[:-len('.tr0')]}"}[sim]
    plan.note(f"by hand instead of `emir simulate`: `{manual}` prints the lines for your "
              f"deck and writes the sidecar; run `{run_by_hand}`"
              + (" (probe subcircuit pins first: `emir probe`)" if probe else ""))


@rule(65, "opensim-result")
def _result(f: Facts, plan: Plan):
    """The check step reads the simulate step's result, a concrete path: no
    placeholder to replace (the base rule's wording assumes one)."""
    if plan.result not in (RESULT, RESULT_TR0):
        return
    for s in plan.steps:
        if s["id"] == "check":
            s["why"] = s["why"].replace("; replace <result.raw> with the simulation's "
                                        "result file", f"; it reads {plan.result}")


def _mode_of(command: str) -> str:
    """The save kind (rcc, cc) of a planned save-lines command's --mode."""
    parts = command.split()
    m = parts[parts.index("--mode") + 1] if "--mode" in parts else "cc"
    return "rcc" if m == "rcc" else "cc"


def _drop(plan: Plan, prefixes: tuple):
    plan.steps[:] = [s for s in plan.steps if not s["id"].startswith(prefixes)]
    _renumber(plan)


def _renumber(plan: Plan):
    for k, s in enumerate(plan.steps, 1):
        s["n"] = k
    ids = {s["id"] for s in plan.steps}
    for s in plan.steps:
        s["after"] = [a for a in s["after"] if a in ids]


def _map_result(f: Facts, plan: Plan, sim: str):
    """A result named in a SPICE style, with no sidecar: when its names are
    the ones `emir save-lines --sim` prints, the step that writes the sidecar
    for it (then plan again)."""
    from . import waves
    from .opensim import infer_names
    try:
        w = waves.read(f.get("cc_sim"))
    except EmirError:
        return
    d = f.dspf_info["_dspf"]
    best = infer_names(d, f.target_nets(), w)
    if best is None:
        from .opensim_netlist import PROBE_SOURCE
        if any(PROBE_SOURCE in k.upper() for k in w.data):
            plan.note(f"the result holds the currents of emir probes ({PROBE_SOURCE}...): "
                      f"the --names-out file of that `emir probe` maps them; copy it to "
                      f"{f.get('cc_sim')}.names (`emir simulate --probes` writes it for you)")
        return
    s_sim, mode, prefix, hits, total = best
    net = f.get("net")
    i = next(k for k, s in enumerate(plan.steps) if s["id"] == "names-sidecar")
    side = f.get("cc_sim") + ".names"
    plan.steps[i]["command"] = command_line(
        "save-lines", f.get("dspf"),
        *(["--net", net] if net and net != "all" and "," not in net else []),
        "--mode", "rcc" if mode == "rcc" else "cc-grid", "--sim", s_sim,
        *(["--prefix", prefix] if prefix else []), "--names-out", side, f.fmt())
    plan.steps[i]["why"] = (f"{hits} of the {total} {mode} currents in the result carry the "
                            f"names emir's {s_sim} lines give them: this writes {side}, "
                            f"which maps them onto the DSPF's")
    again = ["--dspf", f.get("dspf")]
    for opt, key in (("--process", "process"), ("--cc-sim", "cc_sim"), ("--net", "net")):
        if f.get(key):
            again += [opt, f.get(key)]
    if f.get("mode"):
        again += ["--mode", f.get("mode")]
    plan.add("next", command_line("next", again, f.fmt()),
             "plan again with the sidecar in place (it reads the result's names through it)",
             after=["names-sidecar"])
