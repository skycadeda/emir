# Methods and modes

Where a run's branch currents come from, how exact each route is, and the measured
evidence for each. **Screening, not sign-off.**

## The three modes of a DSPF run

| mode | currents from | what is simulated | what is saved | the branch currents are | use it for |
|---|---|---|---|---|---|
| `dc` | `--currents` (JSON, amps into the net) | nothing | nothing | solved once from the DC injections; avg = rms = peak | a budget check, a hand-made scenario |
| `rcc` | `--cc-sim RESULT` | the RC extraction, devices included | every resistor's current (`R:1`) | **measured**: read straight from the simulation | small nets, and as the reference for everything else |
| `cc-grid` | `--cc-sim RESULT` | a C-only (CC) netlist: devices on ideal rails | every device pin's current on the net (`inst:pin`) | **reconstructed**: the pin currents pushed through the R-only grid by superposition | big nets and big blocks |

The same verdict function judges every mode: where the currents came from does not
change how they are checked.

**The mode is the person's choice.** It trades accuracy against cost and convergence,
which only the person can weigh for their block, so EMIR never picks one: `emir run`
and `emir check` with `--cc-sim` or `--netlist`, `emir simulate`, `emir save-lines` and
`emir abstract` take `--mode rcc` or `--mode cc-grid` and refuse to run without it
(`EMIR-E520`, whose hint names both modes and their trade-offs). A DC run (`--currents`)
needs none. The Virtuoso form's Mode field starts at "choose" and refuses to run until
it is set.

`emir next` without `--mode` plans the choice instead of a run: its first step,
`choose-mode`, lists the three routes (below) with what each simulates, costs and how
accurate it is (measured, with the source), whether it can work with what the person has,
and a recommendation from the block's size:

- `rcc` for a net up to 500 nodes: both routes run in seconds there and rcc is exact;
- `cc-grid` above it, and for a block above 50 000 nodes (an ADC or PLL top), where RCC
  transients converge poorly and slowly.

Where only one mode can work it says so and why: a net whose device pin reaches it at
several DSPF connections can be screened in rcc only (cc-grid cannot divide one saved
current between the connections, `EMIR-E417`); a result that holds only pin currents can
be read in cc-grid only (and one with every branch current but no pin currents, in rcc
only). That is information for the person; the choice is still theirs.

One route is cc-grid by construction, and the report says so (`mode: cc-grid`): a
chip-level run with block abstracts (`emir abstract --mode cc-grid`, `emir run
--abstract`). It is open loop: the devices saw the undropped rail, so its error grows
with the droop, and rms and peak read high on the governing branch as on any cc-grid run
(the accuracy table below).
The two thresholds are policy, not measured crossovers. On a generated test block at the
original development site RCC and CC + grid took equal time at 41 nodes, and CC + grid
was already 1.75 times faster at 521 (the table under Speed); 50 000 is a heuristic for
an ADC-top or PLL-top size block.

| route | `--mode` | what it costs | how accurate (measured, against rcc) |
|---|---|---|---|
| rcc | `rcc` | one RC transient, every branch current saved: 2.0x the CC route at 132 nodes, 16.9x at 1 632, 32.4x at 4 932 (`examples/06_big_block/results/sweep.csv`) | the reference |
| cc-grid | `cc-grid` | one CC simulation (0.25 s to 0.40 s with 32 inverters at every mesh size) and the superposition | open loop: below |
| cc-grid self-consistent | `cc-grid --self-consistent` | one CC simulation per pass, each pin fed its droop waveform: 4 or 5 simulations at 0.35 % to 0.55 % droop, 6 to 10 at 4.1 % to 7.9 %, each with 1.7 to 3.8 times the open loop's time points on one net with the companion update (`examples/06_big_block/results/selfconsist_waveform.csv`) | below |

## rcc: the direct route

Every extracted resistor is an element of the simulation, so its current is
measured, not reconstructed. `emir save-lines <dspf> --mode rcc [--prefix XI0.]`
prints the save lines (Spectre `save`; `--sim xyce|ngspice` for the open
simulators), covering every resistor of every net by default: EM applies to any wire
that carries current, not only the rails.

- **Potentials come free.** Walking a spanning tree from the feed and accumulating
  `I x R` gives every node's potential, so static and dynamic IR follow from the
  branch currents alone.
- **A built-in consistency check.** Each branch not on the tree predicts the same
  potential difference independently; the run reports the worst disagreement as the
  **loop residual**. A result paired with the wrong DSPF, a missing save or a flipped
  sign shows up there.
- **The window** is chosen on the current into the net at its feed, the block's own
  periodicity. (It used to be chosen on the branch with the largest peak; on an
  inverter bank in the open-simulator proof a contact out-peaked the feed, the window
  took in the start-up period, and the feed's average read 12.4 % low. The fix has a
  test, `tests/test_rcc_window.py`.)

The cost is the simulation: every rail sub-node is an unknown of the simulator, and the
result holds one current per resistor per time step. On big blocks that is slow and
convergence-prone ([Big blocks](big-blocks.md) measures it).

## cc-grid: superposition through the R-only grid

1. **Simulate C-only.** A C-only (coupled) extraction collapses each net to one node:
   the rails are ideal, the simulation is the size of the schematic plus parasitic
   capacitance, and it converges like one.
2. **Save the device pin currents.** `emir save-lines <dspf> --mode cc-grid` prints them
   from the R-only DSPF's `*|I` records, so the save list, the tap list and the grid
   nodes come from one source. The instance names match because both extractions
   come from the same layout and LVS run.
3. **Superpose.** The grid is linear: `G V = P I(t)`, where `P` scatters each pin's
   current onto the node its `*|I` record names and the feed takes the balance. One
   sparse LU factorisation of `G` serves every right-hand side.
   - **avg** is one solve with the averaged injection: exact.
   - **rms and peak** come from the waveforms, `I_e(t) = sum_k T_ek I_k(t)`, reduced
     over the window: exact for the simulated currents. The run either builds the
     transfer matrix `T` (dense) or streams the time samples through the
     factorisation in chunks; both give the same answer to rounding (within 7e-15 of
     the largest branch on every case measured, `examples/06_big_block`), and the
     choice is made on memory (`--max-memory`, `--superposition`).
   - With per-tap **scalars** instead of waveforms (`--scalars`, a PSS harmonic
     result) rms and peak become the in-phase
     upper bound `sum_k |T_ek| I_k`: never below the value the per-tap scalars imply,
     and labelled as a bound. The scalars themselves can be low: a truncated PSS
     harmonic set makes the per-tap rms low and its rebuilt peak band-limited
     (`EMIR-W404`).

**Decap-aware EM (`--dynamic`).** On that ideal rail a decap carries nothing, so the
superposition charges every edge of the load current to the grid from the feed: high on
the feed path, nothing on a decap's straps. `--dynamic` integrates the RC grid with the
decaps, and the same steps give each segment's current with them (decap-aware); the
verdict uses the larger of the two per segment (`--em-basis worst`, the default) unless
the person chooses `decap-free` or `decap-aware`. Measured against RCC on Xyce
(`examples/13_decap_aware`), decap-free read the governing branch 25 % to 52 % high in
rms and a decap's straps not at all; decap-aware read it +0.59 % to +5.31 % high and
every branch class at or above RCC with one resistive rail, but a decap's straps low
(down to -5.52 %) between two resistive rails ([Big blocks](big-blocks.md#decap-aware-em)).

**What it assumes: decoupling.** The devices draw their current from an ideal rail.
To first order they draw more than they would from the drooped rail, so cc-grid errs
on the conservative side, but that is a first-order argument, not a guarantee: the
measurements below show where it is and is not conservative.

### Closing the loop: self-consistency

`--mode cc-grid --self-consistent` drives the simulation itself:

```sh
emir run block_r.dspf --process pdk.yaml --net VDD --mode cc-grid --self-consistent \
    --netlist cc_tb.cir --sim xyce --vdd 1.8 [--max-passes 10] [--tol-rel 0.01] [--tol-mV 0.2]
```

**Why the open loop's rms and peak read high.** A CC simulation runs every device on an
ideal rail; on the extracted grid each device's pin moves, and in time with the
currents. Measured on the synthetic inverter meshes of `examples/06_big_block`, three
mechanisms make each device's current spike lower and wider than the CC simulation's,
with nearly the same charge:

1. **Its own contact degenerates its source.** A 5 ohm contact drops about 28 mV at the
   device's 5.7 mA peak, like an unbypassed source resistor: one inverter with no mesh at
   all read rms +4.4 % and peak +10.5 % on an ideal rail.
2. **Neighbours switching in the same phase** pull the shared grid down at the same
   instant.
3. **Idle devices give charge back.** An inverter whose output is high is its load
   capacitor behind its ON PMOS: a decap. On the grid it feeds its neighbours' spikes
   locally; on an ideal rail its pin cannot move, so it returns nothing.

On these meshes (2 fF of grid capacitance per node) none of this is the grid's own
capacitance (removing every grid capacitor from RCC moved the governing branch by well
under a point at N <= 20): it is the device currents. So the loop feeds the droop back as
a waveform, every pin its own, idle pins included. On a grid with more capacitance the
capacitance matters too (below), and the droop fed back must hold it.

**The loop.** Each instance pin gets its own supply node, fed from the rail the testbench
drives through a source at that pin's droop waveform (a PWL), in series with whatever
drives the rail (an ideal source, a package resistance, a ramp).

- **Pass 0** feeds 0 V everywhere: the testbench's own open loop (it reproduces the
  unsplit open-loop result on the meshes, both decks on Xyce at reltol 1e-4, within 0.05
  points on VDD and 0.07 on VSS on the governing branch and the IR, and within 0.09 points
  on every branch carrying at least 1 % of the largest: the simulator's step control on two
  decks).
- **The droop** of each pass is solved on the DSPF's grid from that pass's pin currents
  over the whole transient: its resistors, and its capacitance when it has some on the net
  (an RC extraction: the grid is then stepped by backward Euler, each capacitor to ground,
  a coupling capacitor's far plate taken as ground). It is written as one PWL per pin on
  one time grid shared by every pin of every net closed (Douglas-Peucker at 0.3 times the
  loop's tolerance, at least 0.05 mV; at most max(1 500, 0.3 x pass 0's time points) a
  pin, `EMIR-W542` when that cap coarsens it; every PWL point is a simulator breakpoint, so
  the grid is shared and lands on the stimuli's corners, where the spikes peak).
- **The update.** After pass 0 the plain one: the droop pass 0's currents produce, scaled
  down to 15 % of the supply when it is above that (the open loop's droop overstates the
  real one, about twice at 4 % droop, and a block with state can change state under a dip
  it would never see). After that the **companion** update: each pin's device is
  modelled as an admittance g + C d/dt, fitted per pin from the last two passes (the
  secant of current against droop, clipped at >= 0), and the grid is solved by backward
  Euler with those admittances in it. At the fixed point the companion terms cancel, so
  the answer is still the grid with self-consistent currents; the companion only changes
  the path. It is needed where the loop gain (the grid's impedance at the pins times the
  devices' admittance) is above 1 at the edge rate: at 4.1 % droop the idle loads act as
  decap behind the grid's resistance, and the plain update diverged there
  (`hd16b_plain`: residuals 173, 152, 279, 1 772 mV), like a regulator with too little
  phase margin. The fit is frozen once the residual is within twice the tolerance.
- **When it stops.** The residual is the largest difference, over the pins and the
  transient, between the droop the pass's currents produce and the droop the pass
  simulated (the PWL as written). The loop has converged (`stop_rule: residual`) when it
  is below max(--tol-rel x the largest droop, --tol-mV) (1 % and 0.2 mV by default) and
  the governing branch's avg, rms and peak moved less than 0.5 % from the pass before. Or
  it has settled (`stopped: settled`, `stop_rule: metrics`): on two passes in a row the
  governing branch, every item the run reports (an EM ratio at or above the warning
  fraction) and every device's own avg, rms and peak current moved less than 0.5 %, with
  the residual under 5 % of the droop, grown by no more than 10 % on either pass, and the
  PWL not coarsened past the tolerance. A settled loop's residual stays above its
  tolerance (at 4 % droop it levelled off at 1.3 times it, at 7.9 % at 3 times it); the
  message gives both, and how far the branches below the warning fraction still moved.
  It reports that pass (`EMIR-I520`), and says so when it was the last one --max-passes
  allowed (`at_pass_limit`; the next steps then offer more passes).
- **The guard.** Two growths of the residual in a row (each above 10 %), or a droop to
  feed back above half the supply (a ground net closed alone takes half the testbench's
  largest supply; with no supply known, three times the open loop's droop), stops the
  loop and reports pass 0 (`EMIR-W406`); so does `--max-passes` (10 simulations,
  `EMIR-W407`), and a later pass whose simulation runs past its time limit or fails
  (`EMIR-W543`: max(20 x pass 0's time, 60 s, --sim-timeout); a diverging droop makes a
  simulator crawl or fail, but a long block's pass can also be just slow). Never a pass
  in between.
- **Every terminal on the net moves.** A device terminal on the net that is no DSPF
  instance pin would stay on the ideal rail and take its charge out of the loop: refused
  (`EMIR-E540`). Every pass checks KCL, the split pins' currents against the rail
  source's (`EMIR-W541` when part of the rail's current goes elsewhere).
- **The testbench's reltol.** The simulator's own error must be below what the loop
  corrects: above 1e-4 (or unset: 1e-3 is the default) the run warns (`EMIR-W540`).
  Measured on the 432-node mesh (`k32_n20_reltol3` in the results), Xyce at 1e-3 on both
  the loop and RCC moved the open loop's governing avg, rms and peak by 0.29 to 0.41
  points and the converged loop's by 0.09 to 0.45 (+0.06 / +0.53 / +0.48 % against
  -0.03 / +0.07 / +0.08 % at 1e-4).
- **Several nets.** `--net VDD,VSS` (Xyce, ngspice, HSPICE) closes both rails in one loop:
  each net's grid solved on its own, every net's droop fed back on every pass. The
  single-net run stays the documented default.
- **The start.** The loop knows the currents only from the transient's saved start: a
  `.tran` with a start time above 0 has every PWL hold its first value before it
  (`EMIR-W546`: the IR and the currents can read low).

The run summary keeps every pass (`nets[].self_consistency.passes`: the residual and its
tolerance, how the droop was fed, the companion's g and C, the simulator's time points,
the PWL points, KCL, `converged`).

`--vdd` is the net's voltage in the testbench (0 for a ground net); with several nets,
one per net (`--vdd VDD=1.2,VSS=0`; a net left out is read from the testbench). With
Xyce, ngspice and HSPICE the run reads the testbench's source for the net and refuses a
`--vdd` that differs from it (`EMIR-E523`: the wrong net or testbench), or takes the
testbench's value when `--vdd` is not given (`EMIR-I521`); a rail it cannot read (a ramp,
a parameter) needs `--vdd` (`EMIR-E503`). The loop itself does not depend on it, except
for its guard.

The testbench is never rewritten between passes. Xyce, ngspice and HSPICE need no hand
edit: EMIR writes a copy of the testbench in which every device terminal on the net that
is a DSPF instance pin has a node of its own, fed through a 0 V probe that measures its
current (`emir probe`'s machinery; the person's files are untouched), and the sources
that hold each node at its droop are an include (`droop.inc`, `droop.sp` for HSPICE) in
the block's subcircuit, rewritten every pass with inline PWL. EMIR does not read a
Spectre netlist, so for Spectre the person adds the split once (`emir run <dspf> --net
VDD --split-stub` prints it): every pin moves to its own node, fed by a `vsource
type=pwl file=` that reads the pin's droop from a file EMIR rewrites every pass in
`emir_out/selfconsist/<net>/`, and a device's other terminals on the net (a bulk) move
with it. Before Spectre runs, the check refuses a netlist that does not name every pin's
file or leaves a split node unconnected, and a pass that fed a droop but moved no pin
current stops the loop (`EMIR-E524`). Spectre and HSPICE are licensed: the loop runs
them only with `--run`; over MCP the call needs the person's go-ahead, and so does the
loop on Xyce and ngspice, because it re-simulates. The PWL constructs are confirmed on
Xyce and ngspice here and UNVERIFIED on Spectre and HSPICE
([Simulators](simulators.md#the-self-consistent-loops-pwl-sources)); `emir verify-sim`
checks the loop on each simulator against a rail of resistive loads whose closed-loop
answer is worked out by hand.

**What it fixes, measured.** With Xyce on the synthetic meshes of `examples/06_big_block`
(`selfconsist.py`, committed as `results/selfconsist_waveform.csv`; level-1 CMOS inverters
switching at 1 GHz, a 1 V rail, 2 fF of grid capacitance per node, R-only DSPFs), the
governing branch against RCC, open loop -> the reported pass:

| case | droop | avg | rms | peak | simulations |
|---|---|---|---|---|---|
| 32 inverters, 132 nodes | 0.38 % | -0.12 % -> -0.02 % | +6.83 % -> +0.02 % | +15.28 % -> +0.01 % | 4 |
| 32 inverters, 432 nodes | 0.47 % | -0.12 % -> -0.03 % | +7.68 % -> +0.07 % | +16.59 % -> +0.08 % | 5 |
| 32 inverters, 432 nodes, four phases | 0.47 % | -0.14 % -> -0.02 % | +8.47 % -> +0.07 % | +19.28 % -> +0.07 % | 4 |
| 128 inverters, 384 nodes | 4.14 % | +1.01 % -> +0.01 % | +34.16 % -> +0.34 % | +69.78 % -> +0.78 % | 8 (settled) |
| 128 inverters, 384 nodes, four phases | 4.19 % | -0.17 % -> -0.01 % | +42.00 % -> +0.37 % | +92.49 % -> +0.60 % | 9 (settled) |
| 128 inverters, 384 nodes, 1.5 ohm segments | 7.94 % | +3.80 % -> +0.06 % | +50.46 % -> +0.36 % | +113.85 % -> +1.31 % | 10 (settled, the last allowed) |
| 32 inverters, 1 632 nodes | 0.55 % | -0.13 % -> -0.04 % | +9.21 % -> +0.67 % | +18.11 % -> +0.67 % | 5 |
| VDD and VSS meshes, 264 nodes | 0.38 % | VDD -0.22 % -> -0.03 %, VSS -0.14 % -> -0.03 % | +6.76 % -> +0.03 %, +8.07 % -> +0.07 % | +15.28 % -> +0.01 %, +17.09 % -> +0.45 % (VSS window from 0) | 4 |
| 32 inverters, 132 nodes, the loop on ngspice | 0.38 % | -0.12 % -> -0.04 % | +6.83 % -> +0.01 % | +15.28 % -> +0.01 % | 5 |
| 32 inverters, 132 nodes, 40 ns (39 periods in the window) | 0.38 % | -0.13 % -> -0.02 % | +6.83 % -> +0.02 % | +15.27 % -> +0.06 % | 5 (settled) |

(VSS's window: the run chose 0 to 4 ns there, start-up included, and 1 to 4 ns on VDD.)

Each device's own peak current lands within 1.3 % of RCC's, reading low at high droop
(median -0.5 % to -1.1 % at 4 %). **What remains is the grid's own capacitance**, and it
grows with the grid:

- **In the branch currents.** They are scored on the R-only grid, so minor-branch peaks
  (1-10 % of the largest branch) are within 0.8 % of RCC on 132 nodes, 3.1 % on 432, 4.7 %
  on 432 in four phases and 11.3 % on 1 632; against RCC simulated without the grid's
  capacitors the same results are within 0.3 % (2.2 % at 4 % droop). From an RC DSPF,
  `--dynamic` scores the converged currents through the RC grid (decap-aware EM) and takes
  that out (below); an R-only DSPF has no capacitance to score them with.
- **In the droop fed back.** An R-only DSPF has no capacitance to feed back, so its droop
  at the pins is deeper than the real grid's (`EMIR-W544`, on every such run). With ten
  times the grid capacitance (20 fF per node, 384 nodes, 4.1 % droop, `hd16b_c20`) the
  devices' peaks read 1.1 % to 4.8 % low (median 4.2 %) and minor branches down to -20 %,
  the governing branch conservative (+0.25 / +2.77 / +4.93 %), where the open loop read
  those devices 15 % to 30 % high. From the RC DSPF of the same mesh (`hd16b_c20rc`) the
  loop feeds back the RC grid's droop: the devices land within 0.1 % of RCC (-0.08 % to
  +0.01 %). Scored on the R-only grid, its branch currents still miss the capacitance's
  own currents: minor branches -19.5 % to +25.3 %, lower than the open loop's -7.1 %,
  whose high device currents hid it (`EMIR-W545` says so). Scored through the RC grid, as
  `--dynamic --em-basis decap-aware` scores them, the minor branches are within 0.52 % and
  the governing branch's rms and peak +0.12 % and +0.25 % (the `*_rcs` columns). The
  default basis with `--dynamic`, worst, takes per segment the larger of the R-only and
  the RC grid's current, so it keeps the R-only grid's high readings: the governing
  branch +1.98 % rms and +3.88 % peak, minor branches up to +25.3 %, none below the RC
  grid's -0.51 % (conservative, not accurate).
- **In the decaps.** The capacitor devices and MOS decaps the process file's `cap_models`
  size are fed back with the grid, each MOS decap behind its access resistance, as
  `--dynamic` models them: with 8 decaps of 5 pF on the 4.1 % droop mesh, every branch
  class, and the straps' rms and peak, within 1.2 % of RCC scored decap-aware, the
  devices within 0.05 %, in 6 simulations (`examples/13_decap_aware/results/decap_loop.md`). One that
  `cap_models` cannot size is left out, `EMIR-W547`: the droop fed back is then deeper
  than the real one, so the devices read low (down to -5.87 %), the governing branch high
  (+31.94 % peak) and those decaps' straps are not checked. A decap the testbench
  simulates loads the grid through its pin instead, and the loop converges slower: one
  of the 8 simulated took 11 simulations (`--max-passes 16`; at the default 10 the loop
  stopped at its pass limit, `EMIR-W407`), its own peak -5.94 %.

So the answer is accurate rather than conservative: a minor branch or a device can read a
few percent low, more on a bigger grid or with more grid capacitance. Loads whose
current follows their supply -- bias currents, resistive loads -- land on rcc when the
block's other rails are ideal or closed in the same loop (verify-sim's case and example
12: to 1e-9 in 3 simulations).

**Why the average-droop loop it replaced over-corrected.** The loop's first version fed
each pin its *average* droop (`results/selfconsist.csv`, its record): it lowered every
current by the share the average droop takes from the supply. But a switching inverter's
load holds the rail at the moment its PMOS turns off, not the average rail; and at low
droop the open loop's small average error is not droop at all but the gate-overlap kick
through the device's contact (it vanishes without the contact or without the overlap
capacitance: -0.02 % against -0.13 %, `contact0` and `nooverlap` in
`results/sweep.csv`). So the loop moved the average the wrong way at low droop (-0.12 % to
-0.40 % of RCC at 0.38 % droop) and too far at 4.1 % (+1.34 % to -2.02 %: below RCC, no
longer conservative), and left about three quarters of the rms and peak error (+38.2 %
to +28.5 % and +79.9 % to +58.6 % there).

**What it costs.** One CC simulation per pass, with every PWL point a breakpoint: 2.64 to
3.29 times the open loop's time points on one net on the 32-inverter meshes on Xyce at
reltol 1e-4 (1.7 to 3.8 over every one-net case at reltol 1e-4 with the companion
update, up to 6.1 with the plain one, which diverged; 4.3 to 4.9 at 1e-3), 3.9
to 4.2 times with VDD and VSS, with 394 to 1 148 PWL points a pin over 4 ns on one net of
the meshes (1 261 with both rails); a pass's simulation took 2.7 to 4.2 times the open
loop's on one net of the meshes at reltol 1e-4, 5.1 to 5.5 times with both rails. A longer
transient needs more points: 40 ns (39 periods in the window) took 7 400 a pin, 2.4 to 2.5
times the open loop's time points and about 9.5 times its simulation time (20 s against
2.1 s). The
grid side (the solve, the companion's backward-Euler steps, one back-substitution per
time step) took 0.1 s to 0.9 s a pass on 132 to 432 nodes and 0.6 s to 1.0 s on 1 632. The
whole loop took 2.1 s to 8.1 s with 32 inverters and 19 s to 35 s with 128 at 4.1 % to
7.9 % droop, where the RCC simulation alone took 0.5 s to 5.3 s and 1.3 s to 1.9 s: on
grids this small the loop costs more than RCC. On a big block the CC simulation does not
grow with the grid while RCC does (30 s at 4 932 nodes and 350 s at 40 032 in
`results/sweep.csv`), so the loop should pay off there; that is an extrapolation, not a
measurement here, and the grid side grows with the grid.

So: offer the loop when the droop is above about 2 % of the supply (an open-loop
cc-grid run with `--vdd` says so, `EMIR-W520`, and its `next_steps` plan the loop), run
it only with the person's go-ahead, and report both results, each labelled: the open loop
with rms and peak conservative on the governing branch in every case measured on a supply
net (avg and IR not always: -0.17 % at 4.19 % droop in four phases), the self-consistent
one accurate (and on the minor branches of a big grid, possibly a few percent low). On a
signal net neither is a bound: part of its current charges the net's own wire
capacitance, which cc-grid's branches do not carry. On example 08's gate net vb (a layout
magic extracted) the open loop read rms +1.34 % and peak +5.31 %, the loop rms -6.55 % and
peak -4.68 % (`examples/08_open_layout/README.md`); confirm a signal net's result that
matters with rcc.

## Accuracy, with its sources

cc-grid against the rcc reference, relative error, **plus is conservative** (cc-grid
reads high). Each row says where it was measured. Both sides of a comparison reduce rms
the same way, on their own simulator's time points: the trapezoidal rule on the square of
the samples (`ccsim.metrics_tran`), which for a piecewise-linear waveform reads at or
above its exact rms (the integral of the square exceeds the exact one by the sum of
dt (a - b)^2 / 6 over the steps, a and b a step's end values) and so slightly higher
where the time points are sparser across a spike; part of a small rms difference can
come from that rule rather than from the method.

| measured | object | avg | rms | peak | IR |
|---|---|---|---|---|---|
| this repository, `scripts/opensim_proof.py`, Xyce and ngspice | pulsed level-1 sinks on a hand-calculable rail (example 02) | within 5.2e-6 (Xyce) and 1.1e-4 (ngspice) of rcc; both within 2.9e-4 of the hand values | same | same | same |
| the same | an inverter bank, level-1 CMOS, 0.5 pF loads | +0.26 % | +10 % (feed segment) | +24 % (feed segment), +40 % (far contact) | +0.3 % |
| the same, opt-in | the sky130 PDK's own `sky130_fd_pr__nfet_01v8` through ngspice | +0.5 % to +1.6 % on every segment and metric (48 of 48 conservative) | | | +1.4 % |
| this repository, `examples/06_big_block` sweep, Xyce | synthetic mesh, 132 to 4 932 nodes, 32 inverters, governing branch | -0.12 % to -0.15 % | +6.83 % to +13.68 % | +15.28 % to +24.29 % | -0.13 % to -0.15 % |
| the same | every branch carrying at least 1 % of the largest | -0.21 % to -0.02 % | +4.13 % to +13.80 % | -18.95 % to +37.75 % | |
| the same, 256 inverters (4.1 % static droop) | governing branch | +1.34 % | +38.21 % | +79.90 % | +1.36 % |
| this repository, `examples/06_big_block/selfconsist.py`, Xyce: cc-grid self-consistent (the waveform loop) | synthetic meshes, 32 inverters, 132 to 1 632 nodes (0.35 % to 0.55 % droop), governing branch | -0.04 % to -0.02 % | +0.02 % to +0.67 % | +0.01 % to +0.67 % | -0.04 % to -0.02 % |
| the same | 128 inverters (4.1 % to 7.9 % droop), 2 fF of grid capacitance a node, governing branch | -0.01 % to +0.06 % | +0.34 % to +0.37 % | +0.60 % to +1.31 % | -0.01 % to +0.06 % |
| the same | minor branches (1 % to 10 % of the largest), 2 fF a node, 32 inverters, 132 to 432 nodes / 1 632 nodes | | | -4.7 % to +3.6 % / -10.6 % to +11.3 % | |
| the same | minor branches, 2 fF a node, 128 inverters (4.1 % to 7.9 % droop), 384 nodes | | | -4.1 % to +5.1 % | |
| the same | each device's own current, 2 fF a node, and 20 fF fed back from an RC DSPF | | | -1.3 % to +0.1 % | |
| the same | 128 inverters, 20 fF a node, the loop on an R-only DSPF (`EMIR-W544`): governing branch / each device's own current | +0.25 % | +2.77 % | +4.93 % / -4.8 % to -1.1 % | |
| this repository, `examples/08_open_layout`, Xyce: cc-grid open loop on magic's network | a SKY130 NMOS array (8 fingers, the gate pulsed), out and vss, governing branch | +6.07 %, +6.14 % | +5.86 %, +6.09 % | +14.79 %, +29.15 % | +6.20 %, +7.42 % |
| the same, self-consistent on out, vss and the gate net vb (8 simulations) | governing branch / every branch carrying >= 1 % | -0.24 %, -0.23 % / -2.14 % to +9.85 % | -0.21 %, -0.28 % / -4.21 % to +9.58 % | +1.90 %, -0.56 % / -6.30 % to +9.77 % | -0.18 %, +0.90 % |
| the same, self-consistent on out and vss only | governing branch | -0.02 %, -0.01 % | +0.00 %, -0.04 % | +10.76 %, +21.77 % | +0.04 %, +1.13 % |
| original development site (Spectre) | a generated inverter-bank test rail | +1.5 % (largest branch error) | +2.6 % | +11.1 % | +1.81 % |

Measured at the original development site means: internal measurements made while
EMIR was developed, quoted with what was measured, and not reproducible from this
repository. Every other row is reproducible with the script named. The rows measure
the method (cc-grid against RCC); the tools named in them are how the numbers were
made, not a claim that those tools work with EMIR anywhere: that is UNVERIFIED until
checked on the machine that runs them, by `emir verify-sim`
([Simulators](simulators.md#on-site-verification)).

What the table says:

- **Averages and IR are within about 2 %** wherever the droop is small, and not always
  on the conservative side (on the synthetic mesh every branch's average read 0.02 %
  to 0.21 % low). Treat a cc-grid average or IR within 2 % of its limit as marginal.
- **rms and peak are conservative on the branches that carry the most current**, by
  a margin that depends on the block, but **not on every branch**: on secondary
  branches the peak went 17 % low on the synthetic mesh. Where a secondary branch's
  rms or peak decides a fix, confirm it with an rcc run of that sub-block.
- **The open-loop error grows with the droop**: eight times the devices (4.1 % static
  droop) took the governing branch's rms error to +38 % and peak to +80 % (and its
  average to +1.34 %). Not every quantity follows the static droop: rms and peak
  follow the dynamic one (at 0.6 % static and 4.0 % dynamic droop, rms read +13.7 % and
  peak +24 %). Above about 2 % droop (static, or dynamic when `--dynamic` ran), report
  the value as open loop -- rms and peak conservative on the governing branch in every
  case measured on a supply net, avg and IR not always (-0.17 % at 4.19 % droop in four
  phases), not a bound on every branch, nor on a signal net (below) -- and offer the
  self-consistent run, with what it does: on these meshes it took the governing branch
  to within 0.1 % (avg, IR), 0.7 % (rms) and 1.4 % (peak) of RCC, and what it leaves is
  the grid's own capacitance.
- **On magic's network of a SKY130 layout** (example 08, the PDK's own models, the gate
  pulsed), the loop on the two rails alone left the governing peaks +10.8 % (out) and
  +21.8 % (vss) high, with avg and rms within 0.04 %. Closing the loop on the gate net as
  well fed back its own drop (up to 54 mV). That took the peaks to +1.9 % and -0.6 %,
  and avg and rms to within 0.3 %: a little outside the meshes' 0.1 % and 1.4 % above.
  vss's minor branches (the substrate under its tap) stayed within -6 % to +10 %.
- **A signal net is not a supply.** On that gate net vb the loop read the governing
  branch LOW, rms -6.55 % and peak -4.68 % (the open loop +1.34 % and +5.31 %): part of a
  signal net's current charges its own wire capacitance, which cc-grid's branches,
  scored on the R-only grid, do not carry. Neither is a bound there: confirm a signal
  net's result that matters with rcc.

[Big blocks](big-blocks.md) has the full tables and the reasoning behind each
number.

## Speed

On a generated test block at the original development site (inverters tapping a
segmented rail, Spectre), refining the rail while the CC netlist stayed the same:

| rail nodes | RCC | CC + grid |
|---|---|---|
| 41 | 1.92 s | 1.97 s |
| 521 | 3.83 s | 2.19 s |
| 2 057 | 9.53 s | 2.61 s |

In this repository, on the synthetic Xyce mesh of `examples/06_big_block`, the RCC
route took 2.0 times as long as the CC route at 132 nodes and 32.4 times at 4 932
nodes, and the RCC simulation alone 50.3 times the whole CC route at 40 032 nodes.
The crossover is a property of the block; measure it on yours.

## Choosing

The person chooses; these are the recommendations `emir next`'s `choose-mode` step
shows them, with the measured numbers above:

| situation | recommended |
|---|---|
| a small net (up to a few hundred nodes), RC extraction simulated with every branch current saved | `rcc`: exact, and fast enough (seconds); cc-grid would save seconds |
| a medium net | either; cc-grid is faster, rcc is the reference |
| a block of ADC-top or PLL-top size | `cc-grid` with an R-only grid and a C-only simulation ([Big blocks](big-blocks.md)): RCC transients converge poorly and slowly there |
| a cc-grid run whose droop is above about 2 % of the supply | report it as open loop and offer `--self-consistent` (it re-simulates; what it corrects is above) |
| a device pin reaching the net at several places (a black-boxed block's supply pin) | `rcc` only (cc-grid cannot divide the pin's current) for a small net, or the [hierarchical flow](chip-level.md) |
| a result holding only pin currents | `cc-grid` only, unless the person re-simulates for rcc |
| a chip | the [hierarchical flow](chip-level.md) (cc-grid by construction: `emir abstract --mode cc-grid`) |
| no simulation yet | `dc` with a current budget, to find the obvious problems early (no mode) |

`emir next --mode rcc|cc-grid [--self-consistent]` then plans the commands.
