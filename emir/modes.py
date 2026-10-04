"""The mode of a simulated run: the person's choice, never emir's.

Where a run's currents come from a simulation, two modes read them, and they
differ in what is simulated, what it costs and how exact it is:

* ``rcc`` -- the RC-extracted netlist is simulated with every branch current
  saved; the run reads them directly. Exact (measured currents, nothing is
  reconstructed), but the RC transient is slow and converges poorly on big
  blocks, and its result holds one current per resistor per time step.
* ``cc-grid`` -- the devices are simulated on ideal rails (a C-only
  extraction, or the schematic) with their pin currents saved; the run
  superposes them through the R-only grid. Fast and scales, open loop: the
  devices saw the undropped rail. ``--self-consistent`` closes the loop
  (`emir/selfconsist.py`, `emir/tdloop.py`): each pin is fed its own solved
  droop waveform and the CC netlist is simulated again, until the droop the
  currents produce is the droop they were fed.

`emir run`, `check`, `simulate`, `save-lines` and `abstract` take ``--mode``
and refuse to guess it (EMIR-E520): the choice trades accuracy against cost
and convergence, which is the person's to make. `emir next` with no mode
lists the routes (`ROUTES`, with the measured numbers behind them and their
sources) as its ``choose-mode`` step and plans nothing that simulates until
the agent passes the person's choice. A DC run (``--currents``) needs no mode.

`cc` -- the old name of the cc-grid save kind -- is still read (`mode_arg`)
so existing argument files parse; ``auto`` is gone.
"""
from __future__ import annotations

from .errors import EmirError

#: the modes of a simulated run
MODES = ("rcc", "cc-grid")
#: old spellings still read: the save kind `cc` of `save-lines` and `simulate`
ALIASES = {"cc": "cc-grid"}

#: where the measured numbers below come from (repository paths)
SWEEP_CSV = "examples/06_big_block/results/sweep.csv"
SC_CSV = "examples/06_big_block/results/selfconsist_waveform.csv"
#: the record of the average-droop loop the waveform loop replaced
SC_AVERAGE_CSV = "examples/06_big_block/results/selfconsist.csv"

#: the three routes `emir next` shows when no mode is given: what each
#: simulates, what it costs and how accurate it is, with the measurement
#: each number comes from (synthetic meshes, Xyce, serial: an illustration of
#: how the routes compare, not a statement about a real design)
ROUTES = (
    {"route": "rcc", "mode": "rcc", "self_consistent": False,
     "what": "simulate the RC-extracted netlist with every branch current saved; the run "
             "reads them directly",
     "cost": "one RC transient: on the synthetic meshes of examples/06_big_block it took "
             "2.0 times the whole CC route at 132 nodes, 16.9 times at 1 632 and 32.4 times "
             "at 4 932, and the RCC simulation alone 50.3 times at 40 032 nodes; its result "
             "file held 95 MiB at 1 632 nodes against 1.4 MiB for the CC simulation "
             f"({SWEEP_CSV}). At ADC-top or PLL-top size RCC transients converge poorly and "
             "slowly",
     "accuracy": "exact: the reference the other routes are measured against",
     "source": SWEEP_CSV},
    {"route": "cc-grid", "mode": "cc-grid", "self_consistent": False,
     "what": "simulate the devices on ideal rails (a C-only extraction or the schematic) "
             "with their pin currents saved; superpose them through the R-only grid",
     "cost": "one CC simulation (0.25 s to 0.40 s with 32 inverters at every mesh size "
             "from 132 to 40 032 nodes, 1.4 MiB of result) plus the superposition (0.42 s "
             f"at 132 nodes, 6.6 s at 40 032) ({SWEEP_CSV})",
     "accuracy": "open loop (the devices saw the undropped rail): against rcc on the "
                 "synthetic meshes, the governing branch's avg and the IR within 0.4 % at "
                 "0.1 % to 0.6 % droop, rms +4.6 % to +13.7 % and peak +8.3 % to +24.3 %; "
                 "at 4.1 % droop avg +1.34 %, IR +1.36 %, rms +38 % and peak +80 % (plus "
                 f"reads high) ({SWEEP_CSV})",
     "source": SWEEP_CSV},
    {"route": "cc-grid self-consistent", "mode": "cc-grid", "self_consistent": True,
     "what": "cc-grid, then simulate the CC testbench again with every instance pin fed its "
             "own droop waveform (a PWL source in series with the testbench's rail, solved "
             "on the DSPF's grid over the whole transient: R-only, or RC when the DSPF has "
             "capacitance), until the droop the currents produce is the droop they were fed "
             "(--self-consistent --netlist <testbench>; Xyce and ngspice measured, Spectre "
             "and HSPICE UNVERIFIED). One net, or with Xyce, ngspice or HSPICE several in "
             "one loop (--net VDD,VSS)",
     "cost": "one CC simulation per pass, each with 1.7 to 3.8 times the open loop's time "
             "points on one net at reltol 1e-4 with the companion update (up to 6.1 with the "
             "plain one, which diverged; 2.64 to 3.29 on the 32-inverter meshes on "
             "Xyce; 4.3 to 4.9 at reltol 1e-3) and 3.9 to 4.2 times with VDD and VSS "
             "together (every PWL point is a breakpoint; 394 to 1 148 a pin over 4 ns on one "
             "net of the meshes, 1 261 with both rails, 7 400 over 40 ns), plus the grid "
             "side: on the "
             "synthetic meshes the "
             "loop stopped after 4 or 5 simulations at 0.35 % to 0.55 % droop and after 6 to "
             "10 at 4.1 % to 7.9 %; the whole loop "
             "took 2.1 s to 8.1 s with 32 inverters on 132 to 1 632 nodes and 19 s to 35 s "
             "with 128 at 4.1 % to 7.9 %, where the RCC simulation alone took 0.5 s to 5.3 s "
             "and 1.3 s to 1.9 s: on grids this small the loop costs more than RCC. On a 40 ns "
             "transient a pass took 20 s against the open loop's 2.1 s. That it costs less on "
             "a big block is an extrapolation, not a measurement: RCC grows with the grid, the "
             f"CC simulation does not ({SC_CSV})",
     "accuracy": "against rcc on the synthetic inverter meshes (2 fF of grid capacitance per "
                 "node), the governing branch's avg, rms and peak within 0.14 % on the "
                 "32-inverter meshes of 132 and 432 nodes at reltol 1e-4, in eight or four "
                 "phases (0.4 % and 0.5 % droop; +0.53 % rms at reltol 1e-3, EMIR-W540; open "
                 "loop: rms +3.5 % to +8.5 %, peak +6.8 % to +19.3 %), "
                 "VSS closed with VDD on 264 nodes within 0.45 % (its peak, its window from 0), "
                 "and +0.01 % / +0.34 % / +0.78 % at 4.1 % droop (open loop +1.0 %, +34 % "
                 "and +70 %), up to +1.31 % (peak) at 7.9 %; each device's peak current within "
                 "1.3 % on the 2 fF meshes and fed back from an RC DSPF (reading low, median "
                 "-0.5 % to -1.1 % at 4 % droop). What remains is "
                 "the grid's own capacitance: the branch currents are scored on the R-only "
                 "grid, so minor-branch peaks are within 3.1 % on 132 and 432 nodes in eight "
                 "phases, 4.7 % in four, 11.3 % on 1 632 (with 32 inverters; -4.1 % to +5.1 % "
                 "with 128 at 4.1 % to 7.9 % droop), where against RCC without the grid "
                 "capacitors they are within 0.3 % (2.2 % at 4 % droop). With an R-only DSPF "
                 "the droop fed back has no capacitance either (EMIR-W544): at 20 fF per node "
                 "the devices' peaks read 1.1 % to 4.8 % low and minor branches down to -20 %; "
                 "the RC DSPF's droop fed back takes the devices to within 0.1 %, and "
                 "--dynamic with --em-basis decap-aware scores its branches through the RC "
                 "grid (minor branches within 0.52 %; scored on the R-only grid -19.5 % to "
                 "+25.3 %, EMIR-W545; the worst basis takes per segment the larger of the "
                 "two: at 20 fF a node the governing branch +1.98 % rms and +3.88 % peak, "
                 "minor branches up to +25.3 %, conservative). The decaps --dynamic "
                 "sizes (cap_models) are fed back "
                 "with the grid, each behind its access resistance (one cap_models cannot "
                 "size is left out, EMIR-W547: the devices then read low and its straps are "
                 "not checked). So, scored on the R-only grid or decap-aware, the answer is "
                 "accurate rather than conservative: a minor branch or a device can read a "
                 "few percent low. Loads whose current follows their supply land on rcc "
                 "(emir verify-sim's self-consistent case, example 12). The average-droop loop "
                 f"it replaced over-corrected avg and IR below rcc ({SC_AVERAGE_CSV}) ({SC_CSV})",
     "source": SC_CSV},
)


#: what each route measured on a layout magic extracted (example 08: a
#: synthetic SKY130 block of eight NMOS, its rails out and vss and its gate
#: net vb; the tables of its README, from its comparison.md), added to the
#: routes' accuracy when the DSPF is the magic route's (`magic.made_by_emir`):
#: a magic network holds every contact and the diffusion, and its signal nets
#: carry their own wire capacitance, unlike the synthetic meshes above
MAGIC_SOURCE = "examples/08_open_layout/README.md"
MAGIC_ACCURACY = {
    ("rcc", False): "On a layout magic extracted (example 08), EMIR's rcc matched the "
                    "simulator's own branch currents to 6e-12 on 440 branches.",
    ("cc-grid", False): "On a layout magic extracted (example 08, eight NMOS), the open loop "
                        "read the rails' governing branches high: avg +6.07 % and +6.14 %, "
                        "rms +5.86 % and +6.09 %, peak +14.79 % and +29.15 %; and the gate "
                        "net vb's rms +1.34 %, peak +5.31 %. That it reads high is measured "
                        "on supply nets and this one gate net, not a bound: part of a signal "
                        "net's current charges its own wire capacitance, which cc-grid's "
                        "branches do not carry.",
    ("cc-grid", True): "On a layout magic extracted (example 08, eight NMOS, out, vss and "
                       "the gate net vb in the loop), the rails' governing branches came "
                       "within 0.3 % on avg and rms, peak +1.90 % (out) and -0.56 % (vss); "
                       "the gate net vb read LOW, rms -6.55 % and peak -4.68 %: its own wire "
                       "capacitance carries part of its current, which the loop's branches, "
                       "scored on the R-only grid, do not (EMIR-W545). On a signal net "
                       "confirm a result that matters with rcc.",
}


def routes(magic: bool = False) -> list:
    """The three routes (copies of `ROUTES`); `magic`: with what each
    measured on a layout magic extracted (`MAGIC_ACCURACY`)."""
    out = [dict(r) for r in ROUTES]
    if magic:
        for r in out:
            r["accuracy"] += ". " + MAGIC_ACCURACY[(r["mode"], r["self_consistent"])]
            r["source"] += f"; {MAGIC_SOURCE}"
    return out


def mode_arg(text: str) -> str:
    """argparse `type` of --mode: the old save-kind name `cc` reads as
    cc-grid; anything else is checked against the choices as given."""
    return ALIASES.get(text, text)


mode_arg.example = "cc-grid"


def save_kind(mode: str | None) -> str:
    """The save kind of a mode: `rcc` (every branch current) or `cc` (the
    instance-pin currents), as `emir.opensim` and `emir.ccsim` name them."""
    return "rcc" if mode == "rcc" else "cc"


def plan_args(a) -> str:
    """The options of a run (or check) that `emir next` plans with as well,
    quoted for the shell: the period, the simulator and its rail, the
    self-consistent loop, --dynamic and the EM basis, and the outputs the
    person asked for. EMIR-E520's `emir next` carries them (`{plan_args}`),
    so the route the person then chooses keeps them (the same set the
    choose-mode routes keep: `plan_decap._with`)."""
    import shlex
    out = []
    for opt, dest in (("--period", "period"), ("--sim", "sim"), ("--vdd", "vdd")):
        v = getattr(a, dest, None)
        if v is not None and v != "":
            out += [opt, str(v)]
    for opt, dest in (("--self-consistent", "self_consistent"), ("--dynamic", "dynamic"),
                      ("--html", "html"), ("--viewer", "viewer")):
        if getattr(a, dest, None):
            out.append(opt)
    if getattr(a, "em_basis", None):
        out += ["--em-basis", a.em_basis]
    return shlex.join(out)


def not_chosen(command: str, **ctx) -> EmirError:
    """EMIR-E520: the mode was not given. `ctx` fills the code's `emir next`
    command (dspf, process, cc_sim, netlist, net, plan_args)."""
    return EmirError("EMIR-E520", f"emir {command}: --mode is not given, and it is the "
                     f"person's choice: rcc or cc-grid (emir does not pick one)",
                     command=command, **ctx)


def removed_auto(command: str) -> EmirError:
    """EMIR-E520 for `--mode auto`, which is gone."""
    return EmirError("EMIR-E520", f"emir {command}: --mode auto is gone: the person chooses "
                     f"rcc or cc-grid", command=command)


def per_net(d, nets=None) -> dict:
    """`{net: {"recommended", "why", "only", "only_why"}}`: what the net's size
    recommends and, where only one mode can work, which and why (`only`:
    `rcc` for a net with an instance pin at several DSPF connections, which
    cc-grid cannot divide (EMIR-E417), or with no instance pins at all; else
    None). The recommendation is advice the person sees, never a choice:

    * a block above `big_block_nodes` nodes (ADC top, PLL top) -> cc-grid:
      RCC transients converge poorly and slowly at that size;
    * a net up to `small_net_nodes` nodes -> rcc: exact, and the simulation
      takes seconds;
    * a bigger net -> cc-grid: it scales; rcc stays the reference.
    """
    from .plan import THRESHOLDS
    nets = nets or sorted(n for n in d.nets if d.net_resistors(n))
    total = len(d.node_xy)
    big = total > THRESHOLDS["big_block_nodes"]
    small = THRESHOLDS["small_net_nodes"]
    out = {}
    for n in nets:
        res = d.net_resistors(n)
        nodes = len({x for r in res for x in (r["n1"], r["n2"])})
        e = {"only": None, "only_why": None}
        if d.split_pins(n):
            e.update(only="rcc", only_why="an instance pin reaches the net at several DSPF "
                                          "connections, and cc-grid cannot divide one "
                                          "saved pin current between them (EMIR-E417)")
        elif not d.net_taps(n):
            e.update(only="rcc", only_why="the net has no instance pins: cc-grid has no "
                                          "pin current to superpose")
        if big:
            e.update(recommended="cc-grid", why=f"the block has {total} nodes (> "
                     f"{THRESHOLDS['big_block_nodes']}, ADC-top or PLL-top size): RCC "
                     f"transients converge poorly and slowly there")
        elif nodes <= small:
            e.update(recommended="rcc", why=f"{nodes} nodes (<= {small}): the RC "
                     f"simulation takes seconds and rcc is exact")
        else:
            e.update(recommended="cc-grid", why=f"{nodes} nodes (> {small}): cc-grid "
                     f"scales, and rcc stays the reference")
        out[n] = e
    return out
