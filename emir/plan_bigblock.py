"""`emir next` rules for big blocks (an ADC top, a PLL top): the CC + R-only
recipe, the memory the superposition will need, and when dynamic IR is
possible. Auto-imported by `plan.load_extensions`; see `emir/bigblock.py` for
the methods and docs/big-blocks.md for the reasoning.

The rule fires on a DSPF of ADC-top / PLL-top size (`THRESHOLDS
big_block_nodes`, a heuristic) and, at any size, when a net's dense transfer
matrix would not fit the default memory budget. It only advises: the base
rules plan the simulation of the mode the person chose (`emir next` without
--mode lists the routes, and recommends cc-grid at this size).
"""
from __future__ import annotations

from .bigblock import DEFAULT_MAX_MEMORY, dense_bytes, human
from .plan import Facts, Plan, rule

#: above this supply droop, the open-loop CC currents are worth closing
DROOP_FRACTION = 0.02


def memory_estimate(f: Facts) -> list:
    """`[(net, resistors, taps, dense bytes)]` for the target nets: the exact
    superposition's dense transfer matrix (one tap per instance pin, plus the
    feed)."""
    info = f.dspf_info
    d = info.get("_dspf")
    out = []
    for n in f.target_nets():
        pins = info["nets"][n]["pins"]
        nres = len(d.net_resistors(n)) if d is not None else 0
        taps = pins + (1 if info["nets"][n]["port"] else 0)
        out.append((n, nres, taps, dense_bytes(nres, taps)))
    return out


def _caps(f: Facts, nets: list) -> dict:
    """{net: capacitors + MOS decaps on it} from the DSPF."""
    from .dspf import mos_decaps
    d = f.dspf_info.get("_dspf")
    if d is None:
        return {}
    return {n: len(d.net_caps(n)) + len(mos_decaps(d, n)) for n in nets}


@rule(45, "bigblock")
def _bigblock(f: Facts, plan: Plan):
    if not f.dspf_info.get("ok"):
        return
    big = f.size()["class"] == "big"
    heavy = [x for x in memory_estimate(f) if x[3] > DEFAULT_MAX_MEMORY]
    for net, nres, taps, need in heavy[:3]:
        plan.note(f"{net}: the exact superposition's dense transfer matrix would need "
                  f"{human(need)} ({nres} branches x {taps} taps), above the default "
                  f"--max-memory 2G: the run streams it in time chunks instead (same "
                  f"answer, bounded memory; `superposition` in the run summary says "
                  f"which path ran)")
    if not big:
        return
    req = f.get("mode")
    if req == "rcc":
        plan.note("--mode rcc on a block this size means an RC transient with every "
                  "branch current saved: it can converge slowly or not at all, and it writes "
                  "one current per resistor per time step. Prefer the CC route below "
                  "unless the person insists")
    plan.note("big-block recipe: extract the block twice from the same layout and LVS "
              "run -- C-only with coupling caps for the simulation (each rail becomes one "
              "ideal node, so the simulation is small and converges) and R-only with "
              "geometry ($W $L) and via ($N or $A) annotation for the grid DSPF given to "
              "emir. An RC extraction of the rails in place of R-only also gives "
              "--dynamic its capacitance")
    plan.note("save only the instance-pin currents (`emir save-lines <dspf> --mode cc-grid`, "
              "with --prefix for the testbench's instance path), never every branch "
              "current or `save *:currents`: at this size that result file is the "
              "problem")
    plan.note("simulate whole periods of a settled operating point: PSS (and its "
              "time-domain output, for peak) for a periodic block such as a locked "
              "integer-N PLL; a transient with a coherent stimulus for an ADC or a "
              "fractional-N PLL, long enough that the record repeats; pass its "
              "repetition period with --period and check the window sensitivity the "
              "run prints")
    plan.note("one EM run per operating mode (start-up, calibration, steady state, "
              "standby): each is its own simulation and its own report, never one "
              "average across modes")
    plan.note(f"cc-grid is open loop (the devices see the undropped rail); quote it with "
              f"a guard band, give the run --vdd so it can judge the droop, and when a "
              f"power net's IR drop exceeds about {DROOP_FRACTION:.0%} of its supply ask "
              f"the person about the self-consistent run (--mode cc-grid --self-consistent "
              f"--netlist <CC testbench> --vdd V; Xyce, ngspice, and Spectre or HSPICE "
              f"UNVERIFIED: it re-simulates once per pass with each pin fed its own "
              f"droop waveform until droop and currents agree; on the synthetic meshes it "
              f"took avg, rms and peak to +0.01 %, +0.34 % and +0.78 % of rcc at 4.1 % "
              f"droop, where the open loop read +1 %, +34 % and +70 %, and what it leaves is "
              f"the grid's own capacitance (more where the DSPF has none to feed back: "
              f"EMIR-W544; from an RC DSPF --dynamic scores the branches through the RC "
              f"grid: EMIR-W545), so report both, labelled), or report the "
              f"open-loop value as open loop")
    nets = f.target_nets()
    caps = _caps(f, nets)
    with_c = [n for n in nets if caps.get(n)]
    if with_c:
        plan.note(f"the DSPF carries capacitance on {', '.join(with_c[:6])}: add "
                  f"--dynamic to the run for the dynamic droop (open loop, periodic "
                  f"steady state; decap devices are sized from the process file's "
                  f"cap_models)")
    else:
        plan.note("dynamic IR needs capacitance on the rails, which an R-only DSPF does "
                  "not carry: for --dynamic, extract the rails RC (or give decap devices "
                  "cap_models entries in the process file)")
    from .repo import ref
    plan.note("for a whole chip, screen each block like this and combine them "
              f"hierarchically (the playbook: {ref('agent/playbooks/03-chip-level-hierarchy.md')})"
              " instead of flattening the chip into one DSPF")
