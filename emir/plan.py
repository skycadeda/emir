"""`emir next`: what to run next, and why -- the playbooks' decision tree as
code, so the tool and the docs cannot drift.

    emir next                                   # nothing yet: what is needed
    emir next --dspf blk.dspf --process pdk.yaml
    emir next --dspf blk.dspf --process pdk.yaml --cc-sim sim.raw --net VDD
    emir next ... --viewer --html --format json

It looks at what the agent passes (paths, a net, wishes) and what it can
learn cheaply from them (the DSPF's size, what a simulation result saved,
which tools `emir info` finds), and prints an ordered plan: the commands to
run with the reason for each, what is missing and how to get it, where the
currents come from (`mode`: rcc, every branch current of a simulation of the
RC netlist; or dc, the person's DC currents), and which steps need the
person's go-ahead (`needs_human`: a licensed tool, or a long run). It changes
nothing and runs nothing but cheap reads.

Every step either runs as printed (`status` todo, once the steps named in
its `after` have succeeded) or says why not (`status` waiting: its
`placeholders`, such as ``<result.raw>``, stand for a file an earlier step
produces). It plans only what the input check accepts: a result that does
not hold every branch current of a net is replaced by the simulation that
does, and a result whose current names are in SPICE style (HSPICE, Xyce,
ngspice) first needs its `<result>.names` sidecar (`emir schema names`).

How it is built
---------------
* :class:`Facts` -- the inputs, plus probes computed on first use
  (`dspf_info`, `result_info`, `tools`).
* :class:`Plan` -- what the rules write: `steps`, `missing`, `advice`, `mode`.
* Rules -- small functions ``fn(facts, plan)`` registered with
  ``@rule(order)``; they run in `order` (gaps of 10 leave room), and one may
  `plan.stop()` the rest (no DSPF: nothing more to plan).

**Adding rules from a feature** (the open-source flow, Cadence ...): put them
in a module named ``emir/plan_<feature>.py``; every such module is imported
when a plan is built, and its ``@rule`` functions join the tree::

    # emir/plan_myflow.py
    from .plan import rule

    @rule(45, "myflow")
    def _myflow(f, plan):
        if f.inputs.get("something"):
            plan.add("my-step", "emir x ...", "why ...")

A feature's options of `emir next` live in its module too: ``add_arguments(ap)``
adds them to the parser and ``inputs(a) -> dict`` turns the parsed values into
`Facts` inputs (`main` calls both on every ``plan_<feature>.py`` that has them).

Thresholds (`THRESHOLDS`, printed in every plan):

* ``small_net_nodes`` 500 -- a policy, not a measured crossover: the largest
  net of a "small" block, whose RC simulation runs in seconds.
* ``big_block_nodes`` 50 000 -- a heuristic, not a measurement: around
  ADC-top or PLL-top size an RC transient with every branch current saved
  is expected to run long and may converge poorly, so the simulation there is planned as a
  long run (the person's go-ahead first).
* ``probe_result_mb`` 200 -- a result larger than this is not read by the
  plan; the check reads it at run time.
* ``magic_gds_mb`` 20 -- a heuristic, not a measurement: magic extracted
  example 08's layout (31 kB) in 0.2 s; a layout of tens of MB is a big
  block, whose extraction with every resistor kept can run long, so over
  MCP it needs the person's go-ahead.
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import pkgutil
import re
from dataclasses import dataclass, field

from . import RCC_CONTRACT
from .errors import EmirError
from .output import command_line
from .repo import pdk_files

THRESHOLDS = {"small_net_nodes": 500, "big_block_nodes": 50_000,
              "probe_result_mb": 200, "magic_gds_mb": 20}

#: the plan's `mode` when the currents come from a simulation: rcc, the one
#: method (DC currents are mode dc)
RCC = {"chosen": "rcc", "why": "currents from a simulation of the RC netlist: rcc reads "
       "every branch current and checks every metric (avg, rms and peak EM, static and "
       "dynamic IR), exactly"}


# ------------------------------------------------------------------ facts


def net_names(net) -> list:
    """The nets of a --net, read as a comma list (a net named twice once, the
    order kept; [] for none): `emir run` takes one net or `all`, so the plan
    asks which when several are given."""
    if not net:
        return []
    return list(dict.fromkeys(n for n in (x.strip() for x in str(net).split(",")) if n))


class Facts:
    """What the agent has. `inputs` is what it passed; the probes read the
    files once, on first use."""

    def __init__(self, dspf=None, process=None, cc_sim=None, netlist=None, currents=None,
                 mode=None, net=None, viewer=False, html=False, lib=None, cell=None,
                 have_cadence=None, have_xyce=None, have_ngspice=None, json=False,
                 **extra):
        # `mode` is accepted (rcc, the one method; `emir next --mode rcc`) and
        # changes nothing: the currents' source decides, rcc or dc
        self.inputs = {"dspf": dspf, "process": process, "cc_sim": cc_sim,
                       "netlist": netlist, "currents": currents, "mode": mode or None,
                       "net": net, "viewer": bool(viewer), "html": bool(html),
                       "lib": lib, "cell": cell, **extra}
        self._have = {"cadence": have_cadence, "xyce": have_xyce, "ngspice": have_ngspice}
        self.json = json
        self._cache: dict = {}

    def get(self, k):
        return self.inputs.get(k)

    def fmt(self) -> list:
        return ["--format", "json"] if self.json else []

    # ---- tools
    @property
    def tools(self) -> dict:
        """`{name: {"found", "path", "source"}}` for every tool `emir info`
        knows, and `have_cadence` / `have_xyce` / `have_ngspice` (an explicit
        `--have-*` wins over detection)."""
        if "tools" not in self._cache:
            from . import site
            t = {}
            for k in site.SIMULATORS:
                p, src = site.tool(k)
                t[k] = {"found": p is not None, "path": p, "source": src,
                        "licensed": k in site.LICENSED}
            cad = self._have["cadence"]
            t["have_cadence"] = (cad if cad is not None else
                                 any(t[k]["found"] for k in ("spectre", "virtuoso", "quantus")))
            for k in ("xyce", "ngspice"):
                h = self._have[k]
                t[f"have_{k}"] = h if h is not None else t[k]["found"]
            self._cache["tools"] = t
        return self._cache["tools"]

    # ---- the DSPF
    @property
    def dspf_info(self) -> dict:
        """`{"ok", "error", "design", "n_nets", "n_nodes", "nets": {name:
        {"nodes", "pins", "port"}}}` -- or `{"ok": False, "error": ...}`."""
        if "dspf" not in self._cache:
            p = self.get("dspf")
            info = {"ok": False, "error": None}
            if not p:
                info["error"] = "not given"
            elif os.path.isdir(p):
                info["error"] = f"a directory, not a DSPF file: {p}"
            elif not os.path.isfile(p):
                info["error"] = f"not found: {p}"
            else:
                from .dspf import parse
                try:
                    d = parse(p)
                except EmirError as e:
                    info["error"] = f"{e.code}: {e.message}"
                else:
                    nets = {}
                    for n in sorted(d.nets):
                        res = d.net_resistors(n)
                        if not res:
                            continue
                        nets[n] = {"nodes": len({x for r in res for x in (r["n1"], r["n2"])}),
                                   "pins": len(d.pin_groups(n)), "port": bool(d.net_ports(n))}
                    info = {"ok": True, "error": None, "design": d.design,
                            "n_nets": len(d.nets), "n_nodes": len(d.node_xy),
                            "n_resistors": len(d.res), "nets": nets, "_dspf": d}
            self._cache["dspf"] = info
        return self._cache["dspf"]

    # ---- --currents
    @property
    def currents_info(self) -> dict:
        """What --currents holds, read the way `emir run` and `check` read it
        (inline JSON, or the path of a file holding it): `{"ok", "error",
        "code", "path", "n"}`. A plan never names a currents file
        the run would refuse."""
        if "currents" not in self._cache:
            v = self.get("currents")
            info = {"ok": False, "error": None, "code": None, "path": None, "n": 0}
            if not v:
                info["error"] = "not given"
            else:
                from .jsonarg import currents
                try:
                    obj, path = currents(v)
                except EmirError as e:
                    info.update(error=e.message, code=e.code)
                else:
                    info.update(ok=True, path=path, n=len(obj))
            self._cache["currents"] = info
        return self._cache["currents"]

    def one_net(self) -> str | None:
        """The one net a single-net option (`--currents`) drives: --net, or
        the DSPF's only net with resistors (as `emir run` takes it)."""
        net = self.get("net")
        if net and net != "all":
            return net
        nets = self.target_nets()
        return nets[0] if len(nets) == 1 else None

    def target_nets(self) -> list:
        info = self.dspf_info
        if not info["ok"]:
            return []
        net = self.get("net")
        if net and net != "all":
            return [n for n in net_names(net) if n in info["nets"]]
        return list(info["nets"])

    def size(self) -> dict:
        """`{"class": small|medium|big, "net_nodes", "total_nodes", "why"}`."""
        info = self.dspf_info
        if not info["ok"]:
            return {"class": None, "net_nodes": None, "total_nodes": None, "why": "no DSPF"}
        nets = self.target_nets()
        nn = max((info["nets"][n]["nodes"] for n in nets), default=0)
        tot = info["n_nodes"]
        if tot > THRESHOLDS["big_block_nodes"]:
            cls, why = "big", (f"{tot} nodes in the DSPF: ADC-top / PLL-top size "
                               f"(> {THRESHOLDS['big_block_nodes']})")
        elif nn <= THRESHOLDS["small_net_nodes"]:
            cls, why = "small", (f"the largest net to screen has {nn} nodes "
                                 f"(<= {THRESHOLDS['small_net_nodes']})")
        else:
            cls, why = "medium", (f"the largest net to screen has {nn} nodes "
                                  f"(> {THRESHOLDS['small_net_nodes']})")
        return {"class": cls, "net_nodes": nn, "total_nodes": tot, "why": why}

    # ---- the simulation result
    @property
    def result_info(self) -> dict:
        """What the `--cc-sim` result holds, per target net: `{"ok", "error",
        "kind", "holds": {net: ...}}` (`dspf.result_holds`: whether it holds
        every branch current rcc reads), and `hint` when its current names are
        SPICE style with no sidecar to map them. A result larger than
        `probe_result_mb` is not read (`"probed": False`): the check reads it
        at run time."""
        if "result" not in self._cache:
            p = self.get("cc_sim")
            info = {"ok": False, "error": None, "probed": False, "kind": None,
                    "holds": {}, "hint": None}
            if not p:
                info["error"] = "not given"
            elif not os.path.exists(p):
                info["error"] = f"not found: {p}"
            else:
                size = _size_mb(p)
                if size > THRESHOLDS["probe_result_mb"]:
                    info.update(ok=True, error=None,
                                note=f"{size:.0f} MB: not read here; the check reads it "
                                     f"at run time")
                elif self.dspf_info["ok"]:
                    from . import waves
                    from .dspf import result_holds
                    try:
                        w = waves.read(p)
                    except EmirError as e:
                        info["error"] = f"{e.code}: {e.message}"
                        info["code"] = e.code
                    else:
                        self._cache["waves"] = w
                        d = self.dspf_info["_dspf"]
                        nets = self.target_nets()
                        info.update(ok=True, probed=True, kind=w.kind,
                                    holds={n: result_holds(d, n, w) for n in nets})
                        if not any(h["branches"][0] for h in info["holds"].values()):
                            info["hint"] = waves.naming_hint(w).strip() or None
                else:
                    info.update(ok=True, note="the DSPF is not readable, so the result "
                                              "was not matched to its nets")
            self._cache["result"] = info
        return self._cache["result"]

    @property
    def window_info(self) -> dict:
        """`{net: {"ok", "code", "message", "details", "window"}}`: whether the
        `--cc-sim` result gives each target net its window with the given
        --period, as the run takes it (`rcc.net_windows`); {} when the result
        was not read here or does not hold every branch current of the nets."""
        if "window" not in self._cache:
            out = {}
            info = self.result_info if self.get("cc_sim") else {}
            nets = self.target_nets()
            if info.get("probed") and nets and all(info["holds"][n]["rcc"] for n in nets):
                from .rcc import net_windows
                d = self.dspf_info["_dspf"]
                w = self._cache["waves"]
                try:
                    wins = net_windows(d, nets, w, period=self.get("period"),
                                       log=lambda *a, **k: None)
                except EmirError as e:
                    refused = {x["net"]: x for x in e.details.get("nets") or []}
                    for n in nets:
                        x = refused.get(n) if refused else {"code": e.code,
                                                             "message": e.message}
                        out[n] = {"ok": x is None, "window": None,
                                  **({"code": x["code"], "message": x["message"],
                                      "details": {k: v for k, v in e.details.items()
                                                  if k not in ("run", "nets")}}
                                     if x else {"code": None})}
                else:
                    out = {n: {"ok": True, "code": None, "window": wins[n]} for n in nets}
            self._cache["window"] = out
        return self._cache["window"]

    def as_dict(self) -> dict:
        out = {"size": self.size()}
        di = self.dspf_info
        out["dspf"] = {k: v for k, v in di.items() if not k.startswith("_")}
        if di.get("ok") and len(di["nets"]) > 50:
            # keep the plan readable: the nets to screen only
            keep = set(self.target_nets()[:50])
            out["dspf"]["nets"] = {k: v for k, v in di["nets"].items() if k in keep}
            out["dspf"]["nets_shown"] = len(keep)
        if self.get("cc_sim"):
            out["result"] = self.result_info
        # what a rule probed for the plan (the magic route: the magic it runs)
        out.update(self._cache.get("extra_facts") or {})
        return out


def _size_mb(p) -> float:
    if os.path.isdir(p):
        return sum(os.path.getsize(os.path.join(p, f)) for f in os.listdir(p)
                   if os.path.isfile(os.path.join(p, f))) / 2 ** 20
    return os.path.getsize(p) / 2 ** 20


# ------------------------------------------------------------------ the plan


#: a placeholder in a planned command: a file an earlier step produces
PLACEHOLDER = re.compile(r"<[A-Za-z][\w.\-]*>")


@dataclass
class Plan:
    steps: list = field(default_factory=list)
    missing: list = field(default_factory=list)
    advice: list = field(default_factory=list)
    mode: dict = field(default_factory=lambda: {"chosen": None, "why": "not decided yet"})
    rules: list = field(default_factory=list)
    stopped: bool = False
    #: set when the plan includes a simulation: the result path the later
    #: steps read (a placeholder for the person's file)
    result: str | None = None
    #: set when the check would refuse whatever the plan ran (a mode the
    #: inputs cannot serve): no check / run / report steps are planned
    blocked: bool = False

    def add(self, id: str, command: str, why: str, needs_human: bool = False,
            licensed_tool: str | None = None, long_run: bool = False, after=(),
            ask: str | None = None):
        """One step. A step that runs a licensed tool or takes long needs the
        person's go-ahead (`needs_human`), whatever the caller passed; `ask`
        says what else the person must do or agree to (values only a person
        can give). A step whose command holds a `<placeholder>` is `waiting`:
        it does not run as printed (its `placeholders` list what to replace,
        from the steps in `after`)."""
        ph = sorted(set(PLACEHOLDER.findall(command)))
        step = {"n": len(self.steps) + 1, "id": id, "command": command,
                "why": why,
                "needs_human": bool(needs_human or licensed_tool or long_run or ask),
                "licensed_tool": licensed_tool, "long_run": bool(long_run),
                "status": "waiting" if ph else "todo",
                "after": [a for a in after if a], "placeholders": ph}
        if ask:
            step["ask"] = ask
        self.steps.append(step)

    def has(self, id: str) -> bool:
        return any(s["id"] == id for s in self.steps)

    def need(self, what: str, why: str, how: str):
        self.missing.append({"what": what, "why": why, "how": how})

    def note(self, text: str):
        if text not in self.advice:
            self.advice.append(text)

    def stop(self):
        self.stopped = True

    def as_dict(self) -> dict:
        return {"mode": self.mode, "missing": self.missing, "steps": self.steps,
                "advice": self.advice, "rules": self.rules}


@dataclass(order=True)
class Rule:
    order: float
    name: str
    fn: object = field(compare=False)
    final: bool = field(default=False, compare=False)


RULES: list = []
_EXTENSIONS_LOADED = False


def rule(order: float, name: str | None = None, final: bool = False):
    """Register `fn(facts, plan)` as a rule of the tree at position `order`.
    A `final` rule runs even after another rule stopped the plan (it decorates
    the steps planned, whoever planned them)."""
    def deco(fn):
        RULES.append(Rule(order, name or fn.__name__.strip("_"), fn, final))
        return fn
    return deco


def load_extensions() -> list:
    """Import every `emir/plan_<feature>.py`, once; returns their names."""
    global _EXTENSIONS_LOADED
    from . import __path__ as pkg_path
    names = sorted(m.name for m in pkgutil.iter_modules(pkg_path)
                   if m.name.startswith("plan_"))
    if not _EXTENSIONS_LOADED:
        for n in names:
            importlib.import_module(f"{__package__}.{n}")
        _EXTENSIONS_LOADED = True
    return names


def build(facts: Facts) -> Plan:
    """Run the rules in order over `facts`."""
    load_extensions()
    plan = Plan()
    for r in sorted(RULES):
        if plan.stopped and not r.final:
            continue
        before = (len(plan.steps), len(plan.missing), len(plan.advice), dict(plan.mode))
        r.fn(facts, plan)
        if before != (len(plan.steps), len(plan.missing), len(plan.advice), plan.mode):
            plan.rules.append(r.name)
    return plan


# ------------------------------------------------------------------ helpers


def _drive_args(f: Facts) -> list:
    """The run options that say where the currents come from."""
    for opt, key in (("--currents", "currents"), ("--cc-sim", "cc_sim"),
                     ("--netlist", "netlist")):
        if f.get(key):
            return [opt, f.get(key)]
    return []


def run_args(f: Facts, drive: list | None = None) -> list:
    """`<dspf> --process P [--net N] <drive> [--period S]` from the inputs."""
    a = [f.get("dspf"), "--process", f.get("process")]
    if f.get("net") and f.get("net") != "all":
        a += ["--net", f.get("net")]
    a += _drive_args(f) if drive is None else drive
    # the block's period sets the window of a simulation result (without it
    # the plan asks for it: a transient's run is refused, EMIR-E428); DC
    # currents have no window
    if f.get("period") and "--currents" not in a:
        a += ["--period", repr(float(f.get("period")))]
    return a


def _simulate_step(f: Facts, plan: Plan, after=("save-lines",), length: str | None = None):
    """The simulation the person runs (or allows): the RC netlist with every
    branch current saved, in the simulator's own command, with ASCII output
    (binary PSF and raw are not read). `emir save-lines` prints Spectre
    syntax; with HSPICE, Xyce or ngspice the same currents are saved in that
    simulator's syntax and a `<result>.names` sidecar maps their names onto
    the DSPF's (a step of its own)."""
    t = f.tools
    what = "the RC-extracted netlist (every branch current saved)"
    span = length or "over whole periods of the block's activity"
    saves = ("with the save lines above" if any(s["id"].startswith("save-lines")
                                                for s in plan.steps)
             else "saving every branch current (`emir save-lines` prints the lines)")
    if f.size()["class"] == "big":
        what += (", a block of ADC-top or PLL-top size: the RC transient is expected to run "
                 "long and may converge poorly")
    # at a Cadence site the DSPF and the testbench come from Cadence tools, so
    # Spectre (licensed: the person's go-ahead first); else an open simulator.
    # --sim names the simulator the person uses (HSPICE: plan_opensim.py)
    want = f.get("sim")
    if want == "spectre" or (not want and (t["spectre"]["found"] or t["have_cadence"])):
        plan.add("simulate", "spectre <testbench.scs> -format psfascii -raw <result.raw>",
                 f"simulate {what} {saves}, {span}; write ASCII (PSF ASCII)",
                 licensed_tool="spectre", long_run=True, after=after)
        plan.result = "<result.raw>"
        if not t["spectre"]["found"]:
            plan.note("spectre is not found on this machine (`emir info`): the simulate "
                      "step runs where Spectre is ($SPECTRE_BIN, the site file or $PATH)")
        return
    if want == "hspice" or (not want and t["hspice"]["found"]):
        sim, cmd = "HSPICE", "hspice -i <testbench.sp> -o <result>"
        how = "`.option probe post=2 post_version=2001` and `.probe tran` of i(R) for each branch"
    elif want == "xyce" or (not want and t["have_xyce"]):
        sim, cmd = "Xyce", "Xyce -r <result.raw> -a <testbench.cir>"
        how = "`.print tran format=raw` of I(R) for each branch"
    elif want == "ngspice" or (not want and t["have_ngspice"]):
        sim, cmd = "ngspice", "ngspice -b <testbench.cir>"
        how = ("`.options filetype=ascii` and `.save` of @r[i] for each branch, written to "
               "<result.raw>")
    else:
        plan.need("a circuit simulator",
                  "the currents come from a transient simulation of the extracted block",
                  "install Xyce or ngspice (open source), or use the site's Spectre or "
                  "HSPICE (--sim); `emir info` shows what is found")
        plan.blocked = True
        return
    lines = ("The save lines above are Spectre syntax: in the" if saves.startswith("with")
             else "In the")
    plan.add("simulate", cmd,
             f"simulate {what} {span}, in ASCII. {lines} {sim} deck save every branch "
             f"current ({how})", long_run=True, after=after,
             licensed_tool="hspice" if sim == "HSPICE" else None)
    plan.add("names-sidecar", command_line("schema", "names"),
             f"{sim} names currents I(...) / @...[i], not the DSPF's R:1 names: write "
             f"<result.raw>.names beside the result, mapping each saved name to the DSPF "
             f"name and sign; this prints the format",
             after=["simulate"])
    plan.result = "<result.raw>"


def _is_sample(path) -> bool:
    """Whether `path` is the bundled sample's process file."""
    from .selftest import sample_paths
    try:
        return os.path.samefile(path, sample_paths()[1])
    except OSError:
        return False


# ------------------------------------------------------------------ the rules


@rule(10, "preflight")
def _preflight(f: Facts, plan: Plan):
    plan.add("selftest", command_line("selftest", f.fmt()),
             "prove the install works before touching user data (offline, seconds)")


@rule(20, "dspf")
def _dspf(f: Facts, plan: Plan):
    info = f.dspf_info
    if not f.get("dspf"):
        plan.need("an extracted DSPF of the block",
                  "EM and IR are computed on the extracted resistor network; DSPF "
                  "carries the widths, cut counts and coordinates the checks need",
                  "extract with R and C (the RC netlist rcc simulates; R alone serves DC "
                  "currents) and node, geometry ($W $L) and via ($N or $A) annotation: "
                  "Quantus at a Cadence site (licensed: ask the person), or the open-source "
                  "flow")
        plan.stop()
        return
    if not info["ok"]:
        plan.need("a readable DSPF", f"the DSPF {f.get('dspf')!r} is {info['error']}",
                  "check the path; the file must be text DSPF")
        plan.stop()
        return
    net = f.get("net")
    names = net_names(net)
    if len(names) > 1 and not f.get("run_summary"):
        plan.need("one net (or every net: --net all)",
                  f"--net {net}: a run screens one net, or every net",
                  "ask the person which net, or screen every net (--net all)")
        plan.stop()
        return
    bad = [n for n in names if n != "all" and n not in info["nets"]]
    if bad:
        from .dspf import did_you_mean
        for b in bad:
            plan.need(f"net {b!r}", f"it is not a net with resistors in the DSPF"
                      f"{did_you_mean(b, info['nets'])}",
                      "pick a net from `emir nets`")
        plan.add("nets", command_line("nets", f.get("dspf"), f.fmt()),
                 "list the nets and their sizes to pick the one to screen")
        plan.stop()
        return
    if names and ",".join(names) != net:
        # as the run takes it: a net named twice is closed once, the order kept
        f.inputs["net"] = net = ",".join(names)
    if not net:
        plan.add("nets", command_line("nets", f.get("dspf"), f.fmt()),
                 "list the nets and their sizes; without --net every net with "
                 "resistors is screened (EM applies to every net that carries current)")


@rule(30, "process")
def _process(f: Facts, plan: Plan):
    p = f.get("process")
    if not p:
        plan.need("a process file with your PDK's EM limits",
                  "the limits (mA/um per layer, mA/cut per via) and resistances come "
                  "from the PDK; emir never assumes one",
                  f"{pdk_files()} and fill every value from "
                  "the PDK's documentation (or `emir import-ict` from an ICT). No readable "
                  "file (the ICT missing or encrypted): `emir process new --from-dspf` writes "
                  "the fill-in file for the DSPF's layers, the person types each value with "
                  "its source, and `emir process check` must pass. Never invent a limit: ask "
                  "the person for the PDK's EM data")
        return
    if not os.path.isfile(p):
        plan.need("a readable process file", f"{p!r} is not found", "check the path")
        return
    from .check import Report, check_process
    rep = Report()
    check_process(p, rep)
    facts = getattr(rep, "process_facts", None) or {}
    hand = facts.get("provenance") in ("hand-entered", "mixed")
    if not rep.passed:
        plan.need("a valid process file",
                  "it fails the checks: " + "; ".join(
                      f"[{x.code}] {x.message}" for x in rep.findings if x.level == "FAIL"),
                  ("the values were entered by hand: ask the person to type each value with "
                   "its source (or to confirm each one you transcribe from a document they "
                   "give you), and to check each suspect unit; never fill one in. "
                   if hand else "fix each line; ")
                  + "`emir explain <code>` says what each means; `emir schema process` "
                    "lists the keys")
    sus = [x for x in facts.get("suspects") or [] if not x.get("confirmed")]
    if rep.passed and sus:
        plan.note("the process file has value(s) outside the range their quantity normally "
                  "takes (EMIR-W321: " + ", ".join(f"{x['path']} = {x['value']:g} {x['unit']}"
                                                  for x in sus[:4])
                  + "): ask the person to check each unit against the design manual before "
                    "relying on the result")
    if any(x.code == "EMIR-W306" for x in rep.findings):
        if _is_sample(p):
            plan.note("the process file is the bundled ILLUSTRATIVE sample: its limits are "
                      "not real; use it only to learn the flow")
        else:
            plan.note(f"the process file {p} is marked illustrative (EMIR-W306): its "
                      f"limits are not a real PDK's, so a verdict on them screens nothing "
                      f"real; for a design, use a process file with the PDK's own EM data")


@rule(40, "size")
def _size(f: Facts, plan: Plan):
    s = f.size()
    if s["class"] == "big":
        plan.note("ADC-top / PLL-top size: an RC transient with every branch current saved "
                  "is expected to run long at this size and may converge poorly, and its "
                  "result holds one current per resistor per time step; the simulation is "
                  "planned as a long run (ask the person first). Screening one net at a time "
                  "(--net) keeps the result smaller")


@rule(50, "currents")
def _currents(f: Facts, plan: Plan):
    """Where the currents come from: DC values (--currents, mode dc) or a
    simulation of the RC netlist with every branch current saved (mode rcc,
    the one method of a simulated run). Plans only what the check accepts
    for these inputs."""
    nets = f.target_nets()
    if f.get("currents") and (f.get("cc_sim") or f.get("netlist")):
        plan.need("one source of currents",
                  "--currents (DC values) and a simulation ("
                  + ("--cc-sim" if f.get("cc_sim") else "--netlist") + ") were both given; "
                  "a run takes its currents from one (EMIR-E514)",
                  "ask the person which: the simulation for rcc (every metric), or the DC "
                  "currents for a screen; then plan again with that one")
        plan.blocked = True
        return
    if f.get("currents"):
        if not _currents_ok(f, plan):
            return
        plan.mode = {"chosen": "dc", "why": "DC currents given with --currents: the R grid "
                     "is solved directly (avg = rms = peak)"}
        if f.one_net() is None:
            plan.need("--net", "--currents drives exactly one net, and the DSPF has "
                      f"{len(nets)} nets with resistors", "give --net")
        if f.get("mode") == "rcc":
            plan.need("no --mode rcc", "rcc reads branch currents from a simulation "
                      "(EMIR-E506); DC currents are solved directly",
                      "drop --mode rcc (or give a simulation result with --cc-sim)")
            plan.blocked = True
        return
    if not nets:
        return
    if f.get("cc_sim") and not f.result_info["ok"]:
        info = f.result_info
        plan.need("a readable simulation result", f"--cc-sim is {info['error']}",
                  "write the result as text: PSF ASCII, nutascii, HSPICE ASCII or CSDF; one "
                  "sweep table at a time (`emir explain "
                  f"{info.get('code') or 'EMIR-E410'}`)")
        plan.stop()
        return
    if f.get("cc_sim") and f.result_info.get("probed") and f.result_info.get("hint"):
        _names_sidecar(f, plan)
        return
    if f.get("netlist") and not f.get("cc_sim") and not f.tools["spectre"]["found"]:
        from .opensim import is_spectre
        if is_spectre(f.get("netlist")):
            plan.need("Spectre, for the Spectre testbench --netlist",
                      f"{f.get('netlist')} is in Spectre's language, and Spectre was not "
                      f"found on this machine ($SPECTRE_BIN, the site file's spectre:, "
                      f"$PATH; `emir info`)",
                      "point $SPECTRE_BIN (or the site file) at Spectre where it is "
                      "installed; or run the testbench where Spectre is, with the save "
                      "lines from `emir save-lines`, and pass its PSF ASCII result with "
                      "--cc-sim")
        else:
            plan.need("Spectre, for --netlist",
                      "--netlist runs Spectre; none was found (`emir info`)",
                      "run the simulation with the simulator you have (save lines from "
                      "`emir save-lines`) and pass its ASCII result with --cc-sim")
        plan.stop()
        return
    plan.mode = dict(RCC)
    if f.get("cc_sim"):
        _from_result(f, plan, nets)
        return
    if f.get("netlist"):
        return
    # nothing yet: the simulation rcc reads
    plan.need("a simulation result", "the currents come from a transient simulation of "
              "the RC netlist that saved every branch current",
              "add the save lines below to the testbench, simulate, then pass the "
              "result with --cc-sim (or DC currents with --currents)")
    net = f.get("net")
    one = ["--net", net] if net and net != "all" and "," not in net else []
    plan.add("save-lines", command_line("save-lines", f.get("dspf"), *one, f.fmt()),
             "the lines that save every branch current, for the testbench (Spectre "
             "syntax)")
    _simulate_step(f, plan)


def _currents_ok(f: Facts, plan: Plan) -> bool:
    """--currents is readable as the run reads it; else what is wrong, and the
    plan is blocked (a run on it would be refused)."""
    ci = f.currents_info
    if ci["ok"]:
        return True
    plan.need("usable --currents", f"--currents {f.get('currents')!r}: {ci['error']} "
              f"({ci['code']})",
              "give a JSON object of node: amps (summing to zero), inline or as the path of "
              "a file holding it; `emir schema currents` prints the format. Ask the person "
              "for the currents: never invent them")
    plan.blocked = True
    return False


def _from_result(f: Facts, plan: Plan, nets: list):
    """A finished result: which nets it holds every branch current of, and
    what to re-simulate (or map) for the rest."""
    info = f.result_info
    if not info["probed"]:
        plan.mode = {"chosen": "rcc", "why": RCC["why"] + "; " +
                     info.get("note", "the result is read at run time")}
        return
    holds = info["holds"]
    lacking = [n for n in nets if not holds[n]["rcc"]]
    if lacking:
        _lacking(f, plan, lacking, holds)


def _names_sidecar(f: Facts, plan: Plan):
    """SPICE-style names, none of which match: the currents may all be there,
    under the simulator's names -- map them first, do not re-simulate."""
    info = f.result_info
    plan.need(f"a names sidecar for {f.get('cc_sim')}", info["hint"],
              "write it, then run `emir next` again with the same --cc-sim")
    plan.add("names-sidecar", command_line("schema", "names"),
             f"write {f.get('cc_sim')}.names, mapping each saved current to the DSPF's "
             f"R:1 name and its sign; this prints the format")
    plan.mode = {"chosen": "rcc", "why": RCC["why"] + "; the result's names are mapped "
                 "onto the DSPF's by the sidecar first"}
    plan.blocked = True


def _lacking(f: Facts, plan: Plan, lacking: list, holds: dict):
    """Nets the result does not hold every branch current of: the save lines
    and the simulation that would."""
    why = []
    for n in lacking[:6]:
        h = holds[n]
        why.append(f"{n}: {h['branches'][0]} of {h['branches'][1]} branch currents"
                   + ("" if h["time_domain"] else ", and the result is harmonic"))
    plan.need("the branch currents of " + ", ".join(lacking[:6]),
              "the result cannot screen them: " + "; ".join(why),
              "simulate the RC netlist (the DSPF) with every branch current saved: add the "
              "save lines below and re-simulate")
    for n in lacking[:3]:
        plan.add(f"save-lines:{n}", command_line(
            "save-lines", f.get("dspf"), "--net", n, f.fmt()),
            f"the save lines net {n} needs (Spectre syntax)")
    _simulate_step(f, plan, after=[f"save-lines:{n}" for n in lacking[:3]])


@rule(54, "window")
def _window(f: Facts, plan: Plan):
    """rcc averages over the record's last whole periods of --period: a
    result read here is taken as the run takes it (`Facts.window_info`), and a
    refusal plans its fix instead of a run that would be refused -- the
    longer simulation (EMIR-E431; E429 when the block is still starting up) or
    the block's period from the person (E428, E429)."""
    if plan.mode.get("chosen") != "rcc" or plan.blocked or not f.get("cc_sim"):
        return
    refused = [(n, x) for n, x in f.window_info.items() if not x["ok"]]
    if not refused:
        return
    net, x = refused[0]
    code, det = x["code"], x.get("details") or {}
    if code == "EMIR-E428":
        # no run is planned until the person gives the period (planning one
        # would only repeat the refusal)
        plan.need("the block's period (--period, in seconds)",
                  f"the run would be refused ({code}): {x['message']}",
                  PERIOD_HOW + "; then plan again with it")
        plan.blocked = True
        return
    if code not in ("EMIR-E429", "EMIR-E431"):
        plan.need("a simulation result rcc can screen", f"{code}: {x['message']}",
                  f"`emir explain {code}`")
        plan.blocked = True
        return
    per, stop = det.get("period_s"), det.get("stop_time_s")
    if code == "EMIR-E431" and det.get("periods_in_record") == 0:
        # a period longer than the whole record: more likely a wrong --period
        # than a simulation twice as long
        plan.need("the block's period (--period, in seconds)",
                  f"--period {per:.6g} s is longer than the whole record ({code})",
                  "ask the person whether that is the block's period before planning a "
                  "simulation of two of it; never guess it from the waveforms")
        plan.blocked = True
        return
    if code == "EMIR-E429":
        plan.need("the block's period, confirmed by the person (--period, in seconds)",
                  f"the run would be refused ({code}): {x['message']}",
                  f"ask the person whether --period {per:.6g} s is the block's period: if "
                  f"not, plan again with the right one (no simulation needed); if it is, the "
                  f"block is still starting up at the end of the record, and the "
                  f"simulation below runs it longer. Never guess a period")
        length = (f"at least twice as long (a .tran to {stop:.6g} s or later)" if stop
                  else "longer")
    else:
        n = det.get("periods_needed")
        length = (f"over at least {n} periods of {per:.6g} s (a .tran to {stop:.6g} s or "
                  f"later)" if n and per and stop else "over more periods")
        plan.need(f"a longer simulation ({code})", x["message"],
                  f"the person simulates the same testbench {length} (EMIR never edits "
                  f"it), then screen the new result with the same --period")
    # the open simulators' rule turns this into `emir simulate` (it writes the
    # names sidecar itself)
    f._cache["longer_sim"] = {"length": length, "stop": stop}
    _simulate_step(f, plan, after=(), length=length)


@rule(60, "analyse")
def _analyse(f: Facts, plan: Plan):
    """check -> run -> report, once everything they need is there."""
    if not (f.get("dspf") and f.get("process")) or plan.blocked:
        return
    if any(m["what"].startswith(("a valid process", "a readable")) for m in plan.missing):
        return
    # after a simulation the plan asks for, the run reads its result
    drive = ["--cc-sim", plan.result] if plan.result else _drive_args(f)
    if not drive:
        return
    if f.get("currents") and f.one_net() is None:
        return
    before = [x for x in ("simulate", "names-sidecar") if plan.has(x)]
    args = run_args(f, drive)
    plan.add("check", command_line("check", args, f.fmt()),
             "check every input before spending a run (exit 2 and coded FAILs if not)"
             + ("; replace <result.raw> with the simulation's result file"
                if plan.result else ""), after=before)
    extra = ["--summary-out", "emir_out/summary.json"]
    if f.get("html"):
        extra += ["--html", "emir_out/html"] + _chart_layout(f, cell=not f.get("viewer"))
    if f.get("viewer"):
        extra += ["--viewer", "emir_out/viewer"]
        if f.get("lib"):
            extra += ["--lib", f.get("lib")]
        if f.get("cell"):
            extra += ["--cell", f.get("cell")]
    big = f.size()["class"] == "big"
    netlist = bool(f.get("netlist")) and not plan.result
    if netlist:
        extra += ["--run"]          # Spectre is licensed: --run, after the go-ahead
    what = "screen EM (avg/rms/peak per segment) and IR (static, and dynamic from rcc)"
    plan.add("run", command_line("run", args, extra, f.fmt()),
             f"{what}; the summary file feeds the "
             "report (a refused run leaves its error there instead)"
             + ("; --netlist runs Spectre first" if netlist else ""),
             licensed_tool="spectre" if netlist else None, long_run=big or netlist,
             after=["check"])
    plan.add("report", command_line("report", "--from", "emir_out/summary.json"),
             "the summary to relay to the person: verdict, worst items with fixes, what "
             "was not checked; it says screening, not sign-off", after=["run"])


#: how to give --period (the plan's `how`, as EMIR-E428's hint)
PERIOD_HOW = ("ask the person for the period of the block's activity (its clock, or the "
              "period of the testbench's PULSE or SIN sources) and pass it with --period; "
              "never guess it from the waveforms. A record held at DC: any --period with two "
              "or more whole ones in the record gives the same numbers")


@rule(57, "period")
def _period(f: Facts, plan: Plan):
    """A transient result is averaged over the record's last whole periods of
    the block's activity: the period is the person's to give (without it the
    run is refused, with no number: EMIR-E428). An operating point needs
    none. A simulation the plan runs is a transient (`emir simulate` needs a
    .tran, EMIR-E462; a --netlist run is refused without --period): no check
    or run is planned on it until the person gives the period (they were, and
    were certain to be refused)."""
    if plan.mode.get("chosen") != "rcc" or plan.blocked or f.get("period"):
        return
    wins = f.window_info if f.get("cc_sim") else {}
    if wins and all(x["ok"] and x["window"] is None for x in wins.values()):
        return                          # an operating point: no window, no period
    plan.need("the block's period (--period, in seconds)",
              "rcc averages every branch current over the record's last whole periods of "
              "the block's activity; a transient without --period is refused with no "
              "number (EMIR-E428)" + ("" if f.get("cc_sim") else
                                      ", and the simulation planned here is a transient"),
              PERIOD_HOW + ("" if f.get("cc_sim") else "; then plan again with it"))
    if not f.get("cc_sim"):
        plan.blocked = True


def _chart_layout(f, cell: bool = True) -> list:
    """`--gds G` (and, with `cell`, its `--cell`) when the person gave the
    layout: the layout under the --html charts."""
    if not f.get("gds"):
        return []
    return ["--gds", f.get("gds")] + (["--cell", f.get("cell")]
                                       if cell and f.get("cell") else [])


@rule(70, "viewer")
def _viewer(f: Facts, plan: Plan):
    if not f.get("viewer"):
        return
    has_klayout = importlib.util.find_spec("klayout") is not None
    if not f.tools["have_cadence"]:
        if has_klayout:
            plan.note("no Cadence tools here: --viewer still writes the KLayout marker "
                      "database (emir_out/viewer/emir.lyrdb); open it with "
                      "`klayout <layout.gds> -m emir_out/viewer/emir.lyrdb`")
        else:
            from .repo import install_hint
            plan.note("no Cadence tools here, and the klayout Python package is not "
                      "installed, so --viewer writes neither viewer's database you can "
                      f"use (EMIR-W703): `{install_hint('klayout')}` for the KLayout "
                      "marker database, or use --html")
    else:
        plan.note("the Virtuoso Explorer is loaded by the person, in their own session "
                  "(the run prints the two load() lines); never start, kill or restart "
                  "their Virtuoso")
    if not f.get("lib"):
        plan.note("--viewer works best with --lib (the OA library the Explorer opens)")


@rule(90, "guardrails")
def _guardrails(f: Facts, plan: Plan):
    if plan.steps:
        plan.note("run the steps in order and stop at the first that exits non-zero: "
                  "its JSON document (or stderr) carries the code and the next command")
    plan.note("never invent process limits or measured values; report with "
              "`emir report`, which says screening, not sign-off")


# ------------------------------------------------------------------ the command


def main(argv=None, sink=None) -> int:
    import argparse

    from .output import Out, document, parser
    ap = parser("emir next", description="What to run next, and why.")
    ap.add_argument("--dspf", default=None, help="the extracted DSPF, if there is one")
    ap.add_argument("--process", default=None, help="the process file, if there is one")
    ap.add_argument("--cc-sim", default=None, help="a finished simulation result")
    ap.add_argument("--netlist", default=None, help="a netlist emir would simulate")
    ap.add_argument("--currents", default=None, metavar="JSON|FILE",
                    help="DC currents {node: amps}, inline JSON or the path of a file "
                         "holding it (read here as `emir run` reads it)")
    ap.add_argument("--mode", default=None, choices=("rcc",),
                    help="rcc, the one method of a simulated run (accepted; it may be left "
                         "out)")
    ap.add_argument("--net", default=None)
    from .output import seconds
    ap.add_argument("--period", type=seconds, default=None, metavar="S",
                    help="the block's period in seconds (1e-08; on the command line also "
                         "10n or 10ns): the planned check and run average over the "
                         "record's last whole periods of it, and the plan checks that the "
                         "result gives that window (a transient needs it; DC currents and "
                         "an operating point have none). " + RCC_CONTRACT)
    ap.add_argument("--viewer", action="store_true",
                    help="the person wants the results on the layout")
    ap.add_argument("--html", action="store_true", help="the person wants HTML reports")
    ap.add_argument("--lib", default=None, help="OA library, for the viewer")
    ap.add_argument("--cell", default=None, help="cell, for the viewer")
    for t in ("cadence", "xyce", "ngspice"):
        ap.add_argument(f"--have-{t}", action=argparse.BooleanOptionalAction, default=None,
                        help=f"whether {t} is available (default: detected, see `emir info`)")
    # the layout (plan_magic.py)
    ap.add_argument("--gds", default=None,
                    help="the block's layout (GDS): without a DSPF, magic extracts one "
                         "(`emir extract --tool magic`); with one, the layout under the "
                         "--html charts")
    # a feature's plan_<feature>.py may add options (`add_arguments(ap)`) and
    # turn them into inputs (`inputs(a) -> dict`)
    mods = [importlib.import_module(f"{__package__}.{n}") for n in load_extensions()]
    for m in mods:
        if hasattr(m, "add_arguments"):
            m.add_arguments(ap)
    a = ap.parse_args(argv)
    extra = {"gds": a.gds}
    for m in mods:
        if hasattr(m, "inputs"):
            extra.update(m.inputs(a))
    sink = sink or Out(a.format)
    f = Facts(dspf=a.dspf, process=a.process, cc_sim=a.cc_sim, netlist=a.netlist,
              currents=a.currents, mode=a.mode, net=a.net, period=a.period,
              viewer=a.viewer, html=a.html,
              lib=a.lib, cell=a.cell, have_cadence=a.have_cadence, have_xyce=a.have_xyce,
              have_ngspice=a.have_ngspice, json=sink.json, **extra)
    plan = build(f)
    print(render(plan))
    tools = {k: v for k, v in f.tools.items()}
    sink.emit(document("next", inputs=f.inputs, facts=f.as_dict(), tools=tools,
                       thresholds=THRESHOLDS, **plan.as_dict()))
    return 0


def render(plan: Plan) -> str:
    """The plan for a person."""
    L = []
    m = plan.mode
    L.append(f"mode: {m['chosen']} -- {m['why']}" if m["chosen"] else
             f"mode: {m['why']}" if m.get("why") and m["why"] != "not decided yet" else
             "mode: not decided yet (it depends on inputs still missing)")
    if plan.missing:
        L.append("\nmissing:")
        for x in plan.missing:
            L.append(f"  - {x['what']}: {x['why']}\n      how: {x['how']}")
    if plan.steps:
        L.append("\nsteps:")
        for s in plan.steps:
            flag = ""
            if s["needs_human"]:
                why = ([f"licensed tool: {s['licensed_tool']}"] if s["licensed_tool"] else []) \
                    + (["long run"] if s["long_run"] else []) \
                    + ([s["ask"]] if s.get("ask") else [])
                flag = f"  [ASK THE PERSON FIRST: {', '.join(why) or 'their choice'}]"
            wait = (f"  [WAITING: replace {', '.join(s['placeholders'])}]"
                    if s["status"] == "waiting" else "")
            L.append(f"  {s['n']}. {s['command']}{flag}{wait}\n       {s['why']}")
    if plan.advice:
        L.append("\nadvice:")
        L += [f"  - {x}" for x in plan.advice]
    return "\n".join(L)
