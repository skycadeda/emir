"""`emir next`: what to run next, and why -- the playbooks' decision tree as
code, so the tool and the docs cannot drift.

    emir next                                   # nothing yet: what is needed
    emir next --dspf blk.dspf --process pdk.yaml
    emir next --dspf blk.dspf --process pdk.yaml --cc-sim sim.raw --net VDD
    emir next ... --viewer --html --format json

It looks at what the agent passes (paths, a mode, a net, wishes) and what it
can learn cheaply from them (the DSPF's size, what a simulation result
saved, which tools `emir info` finds), and prints an ordered plan: the
commands to run with the reason for each, what is missing and how to get
it, which mode applies, and which steps need the person's go-ahead
(`needs_human`: a licensed tool, or a long run). It changes nothing and
runs nothing but cheap reads.

Every step either runs as printed (`status` todo, once the steps named in
its `after` have succeeded) or says why not (`status` waiting: its
`placeholders`, such as ``<result.raw>``, stand for a file an earlier step
produces). It plans only what the input check accepts: a mode the result
cannot serve (rcc without every branch current, cc-grid on a pin that
reaches the net at several connections) is replaced by the simulation that
serves it, and a result whose current names are in SPICE style (Xyce,
ngspice) first needs its `<result>.names` sidecar (`emir schema names`).

How it is built
---------------
* :class:`Facts` -- the inputs, plus probes computed on first use
  (`dspf_info`, `result_info`, `tools`).
* :class:`Plan` -- what the rules write: `steps`, `missing`, `advice`, `mode`.
* Rules -- small functions ``fn(facts, plan)`` registered with
  ``@rule(order)``; they run in `order` (gaps of 10 leave room), and one may
  `plan.stop()` the rest (no DSPF: nothing more to plan).

**Adding rules from a feature** (hierarchy, the open-source flow ...):
put them in a module named ``emir/plan_<feature>.py``; every such module is
imported when a plan is built, and its ``@rule`` functions join the tree::

    # emir/plan_hierarchy.py
    from .plan import rule

    @rule(45, "hierarchy")
    def _hierarchy(f, plan):
        if f.inputs.get("abstracts"):
            plan.add("abstract", "emir abstract ...", "why ...")

A feature's options of `emir next` live in its module too: ``add_arguments(ap)``
adds them to the parser and ``inputs(a) -> dict`` turns the parsed values into
`Facts` inputs (`main` calls both on every ``plan_<feature>.py`` that has them).

Thresholds (`THRESHOLDS`, printed in every plan):

* ``small_net_nodes`` 500 -- a policy, not a measured crossover: below it both
  routes run in seconds and ``rcc`` is exact, so a small net with every branch
  current saved is screened with ``rcc``. (At the original development site,
  on a generated inverter bank tapping a segmented rail, RCC and CC + grid
  took equal time at 41 nodes, and CC + grid was 1.75 times faster at 521:
  `emir/rcc.py`.)
* ``big_block_nodes`` 50 000 -- a heuristic, not a measurement: around
  ADC-top or PLL-top size an RCC transient converges poorly and slowly, so
  the advice is CC simulation (a C-only extraction) with an R-only
  extraction for the grid.
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

from .errors import EmirError
from .output import command_line
from .repo import pdk_files

THRESHOLDS = {"small_net_nodes": 500, "big_block_nodes": 50_000,
              "probe_result_mb": 200, "magic_gds_mb": 20}


# ------------------------------------------------------------------ facts


def net_names(net) -> list:
    """The nets of a --net as `emir run` takes it (`selfconsist.net_list`):
    a comma list (several nets closed in one self-consistent loop), a net
    named twice once, the order kept; [] for none."""
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
                                   "pins": len(d.pin_groups(n)), "port": bool(d.net_ports(n)),
                                   # pins with several DSPF connections: cc-grid
                                   # refuses them (EMIR-E417), rcc measures them
                                   "split": len(d.split_pins(n))}
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

    def split_nets(self) -> list:
        """The target nets with an instance pin at several DSPF connections."""
        info = self.dspf_info
        return [n for n in self.target_nets() if info["nets"][n].get("split")]

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
            # one net, or several closed in one self-consistent loop (--net VDD,VSS)
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
        "kind", "holds": {net: ...}}` (`dspf.result_holds`: which modes can
        screen it), and `hint` when its current names are SPICE style with no
        sidecar to map them. A result larger than `probe_result_mb` is not read
        (`"probed": False`): the check reads it at run time."""
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
                        d = self.dspf_info["_dspf"]
                        nets = self.target_nets()
                        info.update(ok=True, probed=True, kind=w.kind,
                                    holds={n: result_holds(d, n, w) for n in nets})
                        if not any(h["branches"][0] or h["pins"][0]
                                   for h in info["holds"].values()):
                            info["hint"] = waves.naming_hint(w).strip() or None
                else:
                    info.update(ok=True, note="the DSPF is not readable, so the result "
                                              "was not matched to its nets")
            self._cache["result"] = info
        return self._cache["result"]

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


def run_args(f: Facts, drive: list | None = None, mode: str | None = None) -> list:
    """`<dspf> --process P [--net N] <drive> [--mode M]` from the inputs."""
    a = [f.get("dspf"), "--process", f.get("process")]
    if f.get("net") and f.get("net") != "all":
        a += ["--net", f.get("net")]
    a += _drive_args(f) if drive is None else drive
    # the block's period aligns the window of a simulation result (without it
    # the run detects one and warns, EMIR-W413); DC currents have no window
    if f.get("period") and "--currents" not in a:
        a += ["--period", repr(float(f.get("period")))]
    m = mode or f.get("mode")
    if m:
        a += ["--mode", m]
    return a


def _simulate_step(f: Facts, plan: Plan, kind: str, after=("save-lines",)):
    """The simulation the person runs (or allows): the simulator's own command,
    with ASCII output (binary PSF and raw are not read). `emir save-lines`
    prints Spectre syntax; with Xyce or ngspice the same currents are saved
    in that simulator's syntax and a `<result>.names` sidecar maps their
    names onto the DSPF's (a step of its own)."""
    t = f.tools
    what = ("the RC-extracted netlist (every branch current saved)" if kind == "rcc"
            else "the CC netlist (devices only, the instance-pin currents saved)")
    # at a Cadence site the DSPF and the testbench come from Cadence tools, so
    # Spectre (licensed: the person's go-ahead first); else an open simulator.
    # --sim names the simulator the person uses (HSPICE: plan_opensim.py)
    want = f.get("sim")
    if want == "spectre" or (not want and (t["spectre"]["found"] or t["have_cadence"])):
        plan.add("simulate", "spectre <testbench.scs> -format psfascii -raw <result.raw>",
                 f"simulate {what} with the save lines above, over whole periods of "
                 f"the block's activity; write ASCII (PSF ASCII)",
                 licensed_tool="spectre", long_run=True, after=after)
        plan.result = "<result.raw>"
        if not t["spectre"]["found"]:
            plan.note("spectre is not found on this machine (`emir info`): the simulate "
                      "step runs where Spectre is ($SPECTRE_BIN, the site file or $PATH)")
        return
    if want == "hspice" or (not want and t["hspice"]["found"]):
        sim, cmd = "HSPICE", "hspice -i <testbench.sp> -o <result>"
        how = ("`.option probe post=2 post_version=2001` and `.probe tran` of i(R) for "
               "each branch, or of a 0 V source in series with each instance pin")
    elif want == "xyce" or (not want and t["have_xyce"]):
        sim, cmd = "Xyce", "Xyce -r <result.raw> -a <testbench.cir>"
        how = ("`.print tran format=raw` of I(R) for each branch, or of a 0 V source "
               "in series with each instance pin")
    elif want == "ngspice" or (not want and t["have_ngspice"]):
        sim, cmd = "ngspice", "ngspice -b <testbench.cir>"
        how = ("`.options filetype=ascii` and `.save` of i(V) for a 0 V source in "
               "series with each instance pin (or each branch), written to <result.raw>")
    else:
        plan.need("a circuit simulator",
                  "the currents come from a transient simulation of the extracted block",
                  "install Xyce or ngspice (open source), or use the site's Spectre or "
                  "HSPICE (--sim); `emir info` shows what is found")
        plan.blocked = True
        return
    plan.add("simulate", cmd,
             f"simulate {what} over whole periods of the block's activity, in ASCII. "
             f"The save lines above are Spectre syntax: in the {sim} deck save the same "
             f"currents ({how})", long_run=True, after=after,
             licensed_tool="hspice" if sim == "HSPICE" else None)
    plan.add("names-sidecar", command_line("schema", "names"),
             f"{sim} names currents I(...) / i(...), not the DSPF's inst:pin (cc-grid) or "
             f"R:1 (rcc) names: write <result.raw>.names beside the result, mapping each "
             f"saved name to the DSPF name and sign; this prints the format",
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
                  "extract with R (R-only for a CC run, R+C for an RCC run) and node, "
                  "geometry ($W $L) and via ($N or $A) annotation: Quantus at a Cadence "
                  "site (licensed: ask the person), or the open-source flow")
        plan.stop()
        return
    if not info["ok"]:
        plan.need("a readable DSPF", f"the DSPF {f.get('dspf')!r} is {info['error']}",
                  "check the path; the file must be text DSPF")
        plan.stop()
        return
    net = f.get("net")
    names = net_names(net)
    if len(names) > 1 and not (f.get("self_consistent") or f.get("run_summary")):
        plan.need("one net (or every net: --net all)",
                  f"--net {net}: several nets in one --net are closed in one self-consistent "
                  f"loop (--mode cc-grid --self-consistent, with Xyce, ngspice or HSPICE); "
                  f"any other run screens one net, or every net",
                  "ask the person which net, or whether they want the nets closed in one "
                  "self-consistent loop (then plan again with --self-consistent)")
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
        plan.note("ADC-top / PLL-top size: RCC transients converge poorly and slowly at "
                  "this size. Simulate the block with a C-only (coupled) extraction or the "
                  "schematic (CC), extract R-only for the grid DSPF, and screen in "
                  "cc-grid mode")


@rule(50, "currents")
def _currents(f: Facts, plan: Plan):
    """Where the currents come from, and so the mode -- the person's choice
    (`--mode`): with no mode and currents from a simulation, the plan is the
    choice itself (`choose-mode`), and nothing that simulates or runs is
    planned. Given a mode, only what the check accepts for these inputs."""
    if f.get("abstracts"):
        return                      # --- feature hierarchy --- (plan_hierarchy.py)
    req = f.get("mode")
    nets = f.target_nets()
    split = f.split_nets()
    if f.get("currents"):
        if not _currents_ok(f, plan):
            return
        plan.mode = {"chosen": "dc", "why": "DC currents given with --currents: the R grid "
                     "is solved directly (avg = rms = peak); no mode to choose"}
        if f.one_net() is None:
            plan.need("--net", "--currents drives exactly one net, and the DSPF has "
                      f"{len(nets)} nets with resistors", "give --net")
        if req == "rcc":
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
        _names_sidecar(f, plan, req)
        return
    if f.get("netlist") and not f.get("cc_sim") and not f.get("self_consistent") \
            and not f.tools["spectre"]["found"]:
        plan.need("Spectre, for --netlist",
                  "--netlist runs Spectre; none was found (`emir info`)",
                  "run the simulation with the simulator you have (save lines from "
                  "`emir save-lines`) and pass its ASCII result with --cc-sim")
        plan.stop()
        return
    if req is None:
        choose_mode(f, plan)
        return
    if f.get("self_consistent"):
        _self_consistent(f, plan, req, split)
        return
    if f.get("cc_sim"):
        _from_result(f, plan, req, nets, split)
        return
    if f.get("netlist"):
        if split and req == "cc-grid":
            _split_block(plan, split)
            return
        plan.mode = {"chosen": req, "why": f"--mode {req} given (the person's choice)"}
        return
    # nothing yet: the simulation the chosen mode needs
    if req == "cc-grid" and split:
        _split_block(plan, split)
        return
    kind = "rcc" if req == "rcc" else "cc"
    plan.mode = {"chosen": req, "why": f"--mode {req} given (the person's choice)"}
    plan.need("a simulation result", "the currents come from a transient simulation "
              "that saved what the mode needs",
              "add the save lines below to the testbench, simulate, then pass the "
              "result with --cc-sim (or DC currents with --currents)")
    net = f.get("net")
    one = ["--net", net] if net and net != "all" and "," not in net else []
    plan.add("save-lines", command_line("save-lines", f.get("dspf"), *one, "--mode", req,
                                        f.fmt()),
        f"the save lines a {req} run needs, for the testbench (Spectre syntax)")
    _simulate_step(f, plan, kind)


def choose_mode(f: Facts, plan: Plan):
    """No --mode, currents from a simulation: the first step is the person's
    choice. It lists the three routes (`modes.ROUTES`: what each simulates,
    costs and how accurate it is, measured, with the source), says which can
    work with what the person has and why not, and which the net's size
    recommends; the command is `emir next` again with the choice. Nothing
    that simulates or runs is planned until then (`plan.blocked`)."""
    from .modes import per_net, routes
    nets = f.target_nets()
    pn = per_net(f.dspf_info["_dspf"], nets)
    holds = f.result_info.get("holds", {}) if f.get("cc_sim") else {}
    bad = {"rcc": {}, "cc-grid": {}}
    for n in nets:
        if pn[n]["only"] == "rcc":
            bad["cc-grid"][n] = pn[n]["only_why"]
        h = holds.get(n)
        if h is not None:
            if not h["can"]["rcc"]:
                bad["rcc"][n] = (f"the result holds {h['branches'][0]} of the net's "
                                 f"{h['branches'][1]} branch currents"
                                 + ("" if h["time_domain"] else " and is harmonic")
                                 + ": rcc needs them all (re-simulate the RC netlist "
                                   "with every branch current saved)")
            if not h["can"]["cc-grid"] and n not in bad["cc-grid"]:
                bad["cc-grid"][n] = (f"the result holds {h['pins'][0]} of the net's "
                                     f"{h['pins'][1]} instance-pin currents")
    tb = f.get("netlist") or f._cache.get("opensim_tb")
    if tb and not f.get("cc_sim") and f._cache.get("opensim_tb"):
        # the testbench an open simulator would run: what it can give
        from .plan_opensim import _tb_facts
        tbf = _tb_facts(f, tb, f.dspf_info["_dspf"])
        if not tbf["ok"]:
            plan.need("a readable testbench", f"--netlist {tb}: {tbf['error']}",
                      "fix it (`emir explain " + tbf["code"] + "`)")
        elif not tbf["tran"]:
            plan.need("a transient analysis in the testbench",
                      f"{tb} has no .tran (EMIR-E462): EM needs the currents over time",
                      "add a .tran over whole periods of the block's activity")
        elif not tbf["rc"]:
            for n in nets:
                bad["rcc"].setdefault(n, f"the testbench {tb} does not hold every resistor "
                                         f"of the net (a CC netlist): rcc needs the RC "
                                         f"netlist simulated")
    size = f.size()
    rec = "rcc" if size["class"] == "small" else "cc-grid"
    rec_why = (pn[nets[0]]["why"] if len(nets) == 1 else size["why"])
    if bad[rec] and not bad["cc-grid" if rec == "rcc" else "rcc"]:
        other = "cc-grid" if rec == "rcc" else "rcc"
        rec, rec_why = other, f"only {other} can work here ({_first(bad[rec])})"
    base = ["--dspf", f.get("dspf")]
    # no process file yet, and the plan writes one by hand (plan_process.py):
    # the choice comes after it is checked, and plans with it
    by_hand = plan.has("process-new") and not f.get("process")
    if by_hand:
        from .plan_process import OUT
        base += ["--process", OUT]
    for opt, key in (("--process", "process"), ("--cc-sim", "cc_sim"),
                     ("--net", "net"), ("--sim", "sim")):
        if f.get(key):
            base += [opt, f.get(key)]
    # the testbench the person gave (an open-simulator plan keeps it aside as
    # `opensim_tb`): every route's plan needs it again
    if tb:
        base += ["--netlist", tb]
    if f.get("period"):
        base += ["--period", repr(float(f.get("period")))]
    if f.get("vdd") is not None:
        base += ["--vdd", repr(float(f.get("vdd")))]
    choices = []
    from .magic import made_by_emir
    # a DSPF the magic route wrote: each route also says what it measured on
    # such a layout (example 08), its signal nets included
    magic = bool(f.get("dspf")) and made_by_emir(f.get("dspf"))
    for r in routes(magic=magic):
        why_not = bad[r["mode"]]
        extra = ["--self-consistent"] if r["self_consistent"] else []
        choices.append({**r, "possible": not why_not,
                        "why_not": ("; ".join(f"{n}: {w}" for n, w in
                                              list(why_not.items())[:4]) or None),
                        "recommended": r["mode"] == rec and not r["self_consistent"],
                        "command": command_line("next", base, "--mode", r["mode"], extra,
                                                f.fmt())})
    only = [c["mode"] for c in choices if c["possible"] and not c["self_consistent"]]
    why = ("the currents come from a simulation, and how they are used is the person's "
           "choice: emir does not pick one. Show the person the routes in `choices` (what "
           "each simulates, what it costs and how accurate it is, measured, with its "
           f"source), say that the net's size recommends {rec} ({rec_why}), ask, and run "
           "this command with <mode> replaced by their choice (the `command` of the route "
           "they pick)")
    if len(only) == 1:
        o = next(c for c in choices if c["mode"] != only[0] and not c["self_consistent"])
        why += (f". Only {only[0]} can work with what they have: {o['why_not']} -- tell "
                f"them so; it is still theirs to confirm")
    if len(nets) > 1 and bad["cc-grid"] and len(bad["cc-grid"]) < len(nets):
        why += (f". The nets differ: {', '.join(list(bad['cc-grid'])[:4])} can be screened "
                f"in rcc only; one run has one mode, so screen those with --net, one run "
                f"each")
    plan.add("choose-mode", command_line("next", base, "--mode", "<mode>", f.fmt()), why,
             needs_human=True)
    st = plan.steps.pop()
    st["choices"] = choices
    if by_hand:
        # the process file first (its values typed and checked), then the choice
        st["after"] = ["process-check"]
        st["why"] += (". The process file is entered by hand first (process-new, "
                      "process-check): ask for the mode once it passes")
        plan.steps.insert(next(k for k, x in enumerate(plan.steps)
                               if x["id"] == "process-check") + 1, st)
    else:
        plan.steps.insert(0, st)
    for k, x in enumerate(plan.steps, 1):
        x["n"] = k
    plan.mode = {"chosen": None, "why": "not chosen: the person chooses rcc, cc-grid or "
                 f"cc-grid self-consistent (the choose-mode step); {rec} is recommended "
                 f"({rec_why})"}
    plan.blocked = True
    t = f.tools
    if not f.get("cc_sim") and not (t["spectre"]["found"] or t["have_cadence"] or
                                    t["hspice"]["found"] or t["have_xyce"] or
                                    t["have_ngspice"] or f.get("sim")):
        plan.need("a circuit simulator",
                  "whichever mode the person chooses, the currents come from a transient "
                  "simulation of the extracted block",
                  "install Xyce or ngspice (open source), or use the site's Spectre or "
                  "HSPICE (--sim); `emir info` shows what is found")


def _first(d: dict) -> str:
    n, w = next(iter(d.items()))
    return f"{n}: {w}"


def _self_consistent(f: Facts, plan: Plan, req: str, split: list):
    """--mode cc-grid --self-consistent: the loop re-simulates the CC
    testbench (--netlist) with each pin fed its own droop waveform, in series
    with the testbench's rail."""
    if req != "cc-grid":
        plan.need("--mode cc-grid", "--self-consistent corrects the cc-grid open loop; rcc "
                  "simulates the R network already (EMIR-E505)",
                  "ask the person: --mode cc-grid --self-consistent, or --mode rcc alone")
        plan.blocked = True
        return
    if split:
        _split_block(plan, split)
        return
    plan.mode = {"chosen": "cc-grid", "why": "--mode cc-grid --self-consistent given (the "
                 "person's choice): the CC testbench is simulated once per pass, each pin "
                 "fed its own droop waveform in series with the testbench's rail, until the "
                 "droop the currents produce is the droop they were fed"}
    sim = f.get("sim") or ("spectre" if (f.tools["spectre"]["found"] or
                                         f.tools["have_cadence"]) else
                           "xyce" if f.tools["have_xyce"] else
                           "ngspice" if f.tools["have_ngspice"] else None)
    given = f.get("net")
    several = bool(given) and "," in given and sim in ("xyce", "ngspice", "hspice")
    net = given if several else f.one_net()
    if net is None:
        plan.need("--net", "the self-consistent loop feeds the droop of the nets it is given "
                  "back (EMIR-E502): one net, or with Xyce, ngspice or HSPICE several "
                  "(--net VDD,VSS)",
                  "give --net with the rail(s) to close")
        plan.blocked = True
    tb = f.get("netlist") or f._cache.get("opensim_tb")
    vdd = f.get("vdd")
    if several:
        # each rail is read from the testbench by the run (EMIR-E503 when one
        # cannot be read); one --vdd cannot name several nets (EMIR-E541)
        plan.note(f"--net {net}: the nets are closed in one loop, each one's rail read from "
                  f"the testbench by the run (no --vdd)")
        vdd = "testbench"
    if vdd is None and tb and net and sim in ("xyce", "ngspice", "hspice"):
        # the rail the testbench drives the net with, when it can be read
        from .selfconsist import testbench_rail
        d = (f.dspf_info or {}).get("_dspf")
        rail = testbench_rail(tb, d, net) if d is not None and os.path.isfile(tb) else \
            {"volts": None}
        if rail["volts"] is not None:
            vdd = rail["volts"]
            f._cache["sc_vdd"] = vdd
            plan.note(f"--vdd {vdd:g} V: the voltage of {net} in the testbench "
                      f"({rail['why']}); the run reads it the same way and refuses a --vdd "
                      f"that differs (EMIR-E523)")
    if vdd is None:
        plan.need("the voltage of this net in the testbench (--vdd; 0 for a ground net)",
                  "the run reports each pin's supply as that rail plus its own droop "
                  "(EMIR-E503)" + (("; emir does not read a Spectre netlist" if sim == "spectre"
                                    else "; it could not be read from the testbench")
                                   if tb else ""),
                  "ask the person for the net's voltage in the testbench (0 V for a ground "
                  "net)")
        plan.blocked = True
    if sim is None:
        plan.need("a circuit simulator", "the loop re-simulates the CC testbench",
                  "install Xyce or ngspice (open source), or use the site's Spectre or "
                  "HSPICE (--sim)")
        plan.blocked = True
    if not tb:
        plan.need("the CC testbench (--netlist)",
                  "the loop simulates it again on every pass: the CC netlist (devices on "
                  "ideal rails, a C-only extraction or the schematic) with its stimulus, "
                  "models and a .tran"
                  + ("; for Spectre it must hold the per-tap split (`emir run <dspf> --net "
                     "N --split-stub --vdd V` prints it)" if sim == "spectre" else
                     "; emir writes the per-tap split itself"),
                  "ask the person for it, then plan again with --netlist")
        plan.blocked = True
    if plan.blocked:
        return
    f._cache["sc"] = {"sim": sim, "testbench": tb, "net": net, "vdd": vdd}
    if sim == "spectre":
        plan.add("split-stub", command_line("run", f.get("dspf"), "--net", net,
                                            "--split-stub", f.fmt()),
                 "the per-tap rail split the Spectre CC netlist must hold once (each "
                 "instance pin its own node, fed from the rail through its own `vsource "
                 "type=pwl file=`, reading the droop waveform the loop rewrites every pass; "
                 "UNVERIFIED until checked where Spectre runs): the person adds it by hand "
                 "(emir never edits their netlist) and reconnects each pin as its comments "
                 "list; pass 0 (every droop 0 V) reproduces the open-loop answer. The check "
                 "and the run refuse a netlist without it (EMIR-E524)", needs_human=True,
                 ask="the person adds the printed split to their CC netlist (emir never "
                     "edits it)")


@rule(52, "self-consistent")
def _self_consistent_run(f: Facts, plan: Plan):
    """The check, the loop and the report of a self-consistent plan."""
    sc = f._cache.pop("sc", None)
    if sc is None or plan.blocked or not f.get("process"):
        return
    sim, tb, net = sc["sim"], sc["testbench"], sc["net"]
    licensed = sim in ("spectre", "hspice")
    vdd = [] if sc["vdd"] == "testbench" else ["--vdd", repr(float(sc["vdd"]))]
    args = [f.get("dspf"), "--process", f.get("process"), "--net", net, "--mode", "cc-grid",
            "--self-consistent", "--netlist", tb, *vdd,
            *(["--sim", sim] if sim != "spectre" or f.get("sim") else []),
            *(["--period", repr(float(f.get("period")))] if f.get("period") else [])]
    if f.get("max_passes"):
        args += ["--max-passes", str(int(f.get("max_passes")))]
    if f.get("tol_mV"):
        args += ["--tol-mV", repr(float(f.get("tol_mV")))]
    after = ["split-stub"] if plan.has("split-stub") else []
    plan.add("check", command_line("check", args, f.fmt()),
             "check every input before the loop spends a simulation", after=after)
    extra = ["--summary-out", "emir_out/summary.json"]
    if f.get("html"):
        extra += ["--html", "emir_out/html"] + _chart_layout(f)
    plan.add("run", command_line("run", args, extra, *(["--run"] if licensed else []),
                                 f.fmt()),
             f"the self-consistent loop: pass 0 is the open-loop cc-grid result; each pass "
             f"after it feeds every pin its own droop waveform (in series with the "
             f"testbench's rail) and simulates the CC testbench again with {sim}, until the "
             f"droop the currents produce is within max(1 % of the droop, 0.2 mV) of the "
             f"droop fed, or until the answer settles: the governing branch, every reported "
             f"item and every device still on two passes, the residual under 5 % of the "
             f"droop and not growing (on the synthetic meshes: 4 or 5 simulations at 0.4 % "
             f"droop by the first rule, 6 to 10 at 4 % to 8 %, most by the second; the "
             f"summary keeps every pass and `stop_rule` says which). It re-simulates: ask "
             f"the person"
             + (f" ({sim} is licensed)" if licensed else ""),
             licensed_tool=sim if licensed else None, long_run=True, after=after + ["check"])
    plan.add("report", command_line("report", "--from", "emir_out/summary.json"),
             "the summary to relay to the person: verdict, the passes and whether the "
             "loop converged, worst items with fixes; it says screening, not sign-off",
             after=["run"])
    plan.result = None
    f._cache["sc_planned"] = True


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


def _split_block(plan: Plan, split: list):
    plan.need("--mode rcc for " + ", ".join(split[:6]),
              "an instance pin reaches these nets at several DSPF connections, and "
              "cc-grid cannot divide one saved terminal current between them "
              "(EMIR-E417)", "ask the person: these nets can be screened in rcc mode "
                             "(every branch current saved), or hierarchically")
    plan.blocked = True


def _from_result(f: Facts, plan: Plan, req: str, nets: list, split: list):
    """A finished result and the person's mode: which nets it can screen in
    that mode, and what to re-simulate (or map) for the rest."""
    info = f.result_info
    if not info["probed"]:
        plan.mode = {"chosen": req, "why": f"--mode {req} given (the person's choice); "
                     + info.get("note", "the result is read at run time")}
        return
    holds = info["holds"]
    lacking = {}
    for n in nets:
        if holds[n]["can"][req]:
            continue
        if req == "cc-grid" and n in split:
            lacking[n] = None                  # nothing cc-grid can do: E417
        else:
            lacking[n] = req
    if any(k is None for k in lacking.values()):
        _split_block(plan, [n for n, k in lacking.items() if k is None])
        return
    if lacking:
        _lacking(f, plan, req, lacking, holds)
    plan.mode = {"chosen": req, "why": f"--mode {req} given (the person's choice)"}


def _names_sidecar(f: Facts, plan: Plan, req):
    """SPICE-style names, none of which match: the currents may all be there,
    under the simulator's names -- map them first (whatever the mode), do not
    re-simulate."""
    info = f.result_info
    plan.need(f"a names sidecar for {f.get('cc_sim')}", info["hint"],
              "write it, then run `emir next` again with the same --cc-sim")
    plan.add("names-sidecar", command_line("schema", "names"),
             f"write {f.get('cc_sim')}.names, mapping each saved current to the "
             f"DSPF's inst:pin (cc-grid) or R:1 (rcc) name and its sign; this prints "
             f"the format")
    plan.mode = {"chosen": req, "why": (f"--mode {req} given; " if req else
                                        "not chosen yet (the person's choice); ")
                 + "the result's names are mapped onto the DSPF's by the sidecar first"}
    plan.blocked = True


def _lacking(f: Facts, plan: Plan, req: str, lacking: dict, holds: dict):
    """Nets the result cannot screen in the mode asked for: the save lines
    and the simulation that would, and what the result can do as it is."""
    names = list(lacking)
    why = []
    for n in names[:6]:
        h = holds[n]
        why.append(f"{n}: {h['branches'][0]} of {h['branches'][1]} branch currents, "
                   f"{h['pins'][0]} of {h['pins'][1]} instance-pin currents"
                   + (f", {h['split_pins']} pin(s) at several connections"
                      if h["split_pins"] else ""))
    plan.need("the currents of " + ", ".join(names[:6]),
              f"the result cannot screen them in mode {req}: " + "; ".join(why),
              "add the save lines below and re-simulate")
    for n in names[:3]:
        plan.add(f"save-lines:{n}", command_line(
            "save-lines", f.get("dspf"), "--net", n, "--mode", lacking[n], f.fmt()),
            f"the save lines net {n} needs (Spectre syntax)")
    _simulate_step(f, plan, "rcc" if req == "rcc" else "cc",
                   after=[f"save-lines:{n}" for n in names[:3]])
    other = "cc-grid" if req == "rcc" else "rcc"
    able = [n for n in names if holds[n]["can"][other]]
    if able:
        plan.note(f"the result as it is can screen {', '.join(able[:6])} in {other} mode: "
                  f"ask the person whether to use --mode {other} instead of re-simulating "
                  f"(their choice, with its trade-offs: `emir next` without --mode lists "
                  f"them)")


@rule(60, "analyse")
def _analyse(f: Facts, plan: Plan):
    """check -> run -> report, once everything they need is there."""
    if not (f.get("dspf") and f.get("process")) or plan.blocked or f.get("abstracts"):
        return
    if f._cache.get("sc_planned"):
        return                      # the self-consistent loop's own steps (rule 52)
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
    plan.add("run", command_line("run", args, extra, f.fmt()),
             "screen EM (avg/rms/peak per segment) and IR; the summary file feeds the "
             "report (a refused run leaves its error there instead)"
             + ("; --netlist runs Spectre first" if netlist else ""),
             licensed_tool="spectre" if netlist else None, long_run=big or netlist,
             after=["check"])
    plan.add("report", command_line("report", "--from", "emir_out/summary.json"),
             "the summary to relay to the person: verdict, worst items with fixes, what "
             "was not checked; it says screening, not sign-off", after=["run"])


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
    from .modes import mode_arg
    ap.add_argument("--mode", default=None, type=mode_arg, choices=("rcc", "cc-grid"),
                    help="the person's choice of mode, once they have made it (without it, "
                         "and with currents from a simulation, the plan asks for it in its "
                         "choose-mode step -- first, or after process-check when the "
                         "limits are typed by hand: the routes with their measured cost and "
                         "accuracy)")
    ap.add_argument("--self-consistent", action="store_true",
                    help="with --mode cc-grid: the person chose the self-consistent route "
                         "(the CC testbench, --netlist, simulated once per pass with each "
                         "pin's supply at the testbench's rail plus its own droop)")
    ap.add_argument("--vdd", type=float, default=None, metavar="V",
                    help="the voltage of the net in the testbench, in V, for "
                         "--self-consistent (0 for a ground net; read from a Xyce, ngspice "
                         "or HSPICE testbench when not given)")
    ap.add_argument("--net", default=None)
    ap.add_argument("--period", type=float, default=None, metavar="S",
                    help="the block's period (s): the planned check and run align their "
                         "window to it (a simulation result's; DC currents have none)")
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
              self_consistent=a.self_consistent, vdd=a.vdd,
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
            for c in s.get("choices") or []:
                tag = (" (recommended)" if c["recommended"] else "") + \
                    ("" if c["possible"] else f" -- cannot work here: {c['why_not']}")
                L.append(f"       * {c['route']}{tag}\n           what: {c['what']}\n"
                         f"           cost: {c['cost']}\n"
                         f"           accuracy: {c['accuracy']}\n"
                         f"           then: {c['command']}")
    if plan.advice:
        L.append("\nadvice:")
        L += [f"  - {x}" for x in plan.advice]
    return "\n".join(L)
