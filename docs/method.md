# Method and accuracy

What EMIR checks, where its currents come from, how exact the result is and how that is
shown, and what EMIR leaves out. **Screening, not sign-off.**

## What EMIR checks

A net is a resistor network: the extracted resistors of a DSPF. Currents enter and
leave it at device pins and at the port where it is fed. EMIR takes the current in
every branch and checks it against the process file:

| item | quantity | limits (process file) | unit |
|---|---|---|---|
| a wire segment | current per um of drawn width, `I / W` | `jmax_avg`, `jmax_rms` (or `jmax_rms_k`), `jmax_peak` | mA/um |
| a via array | its mean current per cut, `I / cuts` | `imax_avg`, `imax_rms`, `imax_peak` | mA/cut |

The **ratio** is the quantity over its limit. An item is FAIL at 1.00x and above, WARN
from `warn_fraction` (default 0.8), clean below; its worst metric is the **governing
metric**. A net is FAIL when any item fails. A ratio that is not a number (an unsolved
current) and a wire with no width (`EMIR-E210`) are FAIL by rule. A metric with no limit
in the process file is **not checked**, and every report lists it: EMIR never invents a
limit (SKY130 publishes no peak limits, so peak is not checked with `pdks/sky130.yaml`).

- **avg**, the time average, drives electromigration; **rms** measures self-heating;
  **peak** guards against short high pulses. They are integrated with trapezoid
  weights on the simulator's own time steps, over the record's last whole periods
  (below). avg is exact for the samples joined by straight lines; rms is the trapezoid
  of i squared, as `.meas RMS` takes it (on a coarse record, a little high).
- **Currents that reverse**: the magnitude of the *signed* average is compared with the
  DC limit, the least conservative reading of a bidirectional rule; the report lists
  under "Not checked" each branch whose average of |I| reaches the warn fraction while
  its signed average does not.
- **A via's ratio is the array's mean per cut**; the edge cuts carry more. A via above
  about 0.9x is listed as marginal.
- **Temperature**: the average limits hold at `em_tref` (required), derated by a
  `jmax_factor` table or Black's equation (`derating`) at another analysis temperature
  (`--em-temp`); without either, a hotter analysis is not conservative and the run
  says so (`EMIR-W315`). Wire resistance is scaled by the TCRs for the IR drop. The rms
  limit is fixed (`jmax_rms`, a LEF's) or the self-heating form
  `sqrt(jmax_rms_k x delta_T)` with `--delta-t` (default 5 C).
- **IR drop** is the largest drop from the net's feed (its `*|P` port, or `--feed`) to
  any node, device pins included, from the average currents; the **dynamic** IR drop is
  the largest instantaneous one.
- **Fix hints**, at 1.00x and above: a wire at ratio r is widened to past W x r, to the
  next 5 nm (`Metal1: widen 0.750 -> 1.255 um (x1.67)`); a via with n cuts gets
  floor(n x r) + 1 cuts (`Cont: 1 -> 2 cuts (+1)`). They are first-order: re-extract
  and re-run after a fix.

Every run starts with the input checker (`emir check` runs it alone): a missing width,
an unmapped layer, a via with no cut count (checked as one cut, `EMIR-W207`), currents
that do not balance, a result that lacks a branch current. Each finding has a code and
the command that fixes it; a FAIL stops the run.

## Where the currents come from

| currents | what you simulate | checks |
|---|---|---|
| a simulation result (`--cc-sim`) | the RC extraction itself, every resistor's current saved | every metric: avg, rms and peak EM, static and dynamic IR, from the simulated branch currents |
| DC values (`--currents`, amps into the net) | nothing | avg = rms = peak, each the DC current's magnitude, and the IR drop: a screen of your current budget |

`--cc-sim` (a name kept from 1.0) is the simulation result rcc reads; a run takes it or
`--currents`, never both (`EMIR-E514`). **The window is the record's last whole periods of
`--period`** (`1e-08`, `10n`, `10NS`), up to three, the ones closest to its end: rcc is
exact for the simulated record over the whole periods you give (--period); whether the
testbench has settled is yours to establish, as for any .meas. A transient needs
`--period` (`EMIR-E428`: EMIR never guesses one), and so does a `--netlist` run (nothing
is simulated without it); beyond that, EMIR refuses a run, with no number, only for what
it can check for certain:

- `EMIR-E431`: fewer than two whole periods in the record (the message gives the stop time);
- `EMIR-E429`: the record's last two whole periods differ by more than 2 % in the average,
  rms or peak of a branch that matters, one carrying at least 1 % of its net's largest
  current (each change is a share of the larger of its two values, never of less than
  10 % of that branch's own rms). Give the right `--period`, or simulate longer if the
  block is still starting up; a peak that differs on a coarse record wants a smaller
  maximum time step.

`emir next` plans the fix. A record held at DC (a bias) is a transient too: any `--period`
with two or more whole ones in the record gives the same numbers. An operating point (one
time point) needs no period: avg = rms = peak. EMIR measures the activity the testbench
drives: a load the record never exercises reads zero.

**A simulated run is rcc.** The testbench includes the RC extraction, and the simulator
saves the current of every resistor (`emir save-lines` writes those lines for Spectre,
HSPICE, Xyce or ngspice). EMIR reads every branch current: nothing is reconstructed,
and each segment's avg, rms and peak come from its own waveform. Walking a spanning tree
from the feed turns the branch currents into node voltages, so static and dynamic IR
come from the same result; each branch off the tree checks them again (the **loop
residual**, which catches a wrong DSPF, a missing save line or a flipped sign). The
voltages use the resistances the simulation ran, the DSPF's: the IR is the simulated
circuit's. When the EM limits are taken at another temperature (`--em-temp`),
`EMIR-I407` says so; for IR at that temperature, extract or simulate at it.
`--mode rcc` is accepted and may be left out; any other value is a usage error
(`EMIR-E507`).

**Big blocks need RCC in this release.** The cost is the simulation: every resistor is
an element of the RC transient and its current one signal per time step, so on a block
the size of an ADC or PLL top the run is expected to be long and may converge poorly;
`emir next` plans it as a long run, for the person's go-ahead. A faster route for big
blocks (cc-grid: device currents solved through the R-only grid) and the chip-level
hierarchy are kept in the development repository (branch `cc-grid-guard`, not published)
for a later release.

## Accuracy

rcc adds no model of its own: the currents are the simulator's, read one branch at a
time. What it can add is the reduction to avg, rms and peak (the trapezoid rule on the
simulator's time steps, over the window), and that is measured:

| evidence | what was compared | agreement |
|---|---|---|
| [`examples/08_open_layout`](../examples/08_open_layout/) | EMIR's avg, rms and peak on magic's SKY130 network (440 checked branches) against the simulator's own output, reduced without EMIR | 7.8e-13 |
| the same | EMIR's static and dynamic IR on out and vss against the simulator's own node voltages (the deck re-run printing them) | 3.2e-7 |
| `scripts/opensim_proof.py`, DC loads | example 02's rail, Xyce and ngspice, against the hand calculation | 1.3e-9 |
| the same, pulsed loads | against the hand calculation (the ramps of each pulse integrated by the trapezoid rule) | 2.8e-4 |
| the same, Xyce against ngspice | every ratio and IR drop, three load cases | 1.1e-4 |
| `emir validate` | six closed-form cases (below) | exact, or to the stated tolerance |

What remains is outside EMIR, and each report names it: the simulator's own tolerances
and time steps; whether the testbench has settled over the periods you give (yours, as
for any .meas); the extraction (a DSPF with series-reduced resistors averages a neck
away); the limits (your process file's). DC currents are as good as the currents you give.

## What a run produces

| output | how | for |
|---|---|---|
| the run summary, `emir.run/1` | `--format json`, or `--summary-out FILE` | agents and scripts; `emir report` reads it |
| the report | `emir report --from FILE` | what an agent relays to a person |
| HTML | `--html DIR` | an index of nets with the run's EM/IR chart, a page per net with a zoomable map |
| viewer data | `--viewer DIR` | the Virtuoso Explorer and a KLayout marker database (`emir.lyrdb`) |

The summary's `not_checked` and the report's "Not checked" list what the run could not
judge: layers with no process entry, metrics with no limit, vias assumed to have one
cut, limits not derated to a hotter temperature, reversing currents. Relay it with the
verdict: a CLEAN net with half its layers unmapped is not clean.

Every chart uses one colour scale: blue below the warn fraction (by the ratio's log),
amber in the warn band, red at 1x and over **or not a number**, grey where no limit is
stated (not checked, never passed). The worst items are numbered on the map with their
fixes.

![Screenshot of EMIR's per-net HTML report of an rcc run: verdict FAIL, the worst EM item a contact at 1.042x of its peak limit, the net drawn as a vector map, coverage per metric and the violations table.](images/html_report.png#only-light)
![Screenshot of EMIR's per-net HTML report of an rcc run: verdict FAIL, the worst EM item a contact at 1.042x of its peak limit, the net drawn as a vector map, coverage per metric and the violations table.](images/html_report_dark.png#only-dark)

*The HTML report of examples/02_xyce (Xyce currents, illustrative limits).*

## Validation

| evidence | command | proves |
|---|---|---|
| the self-test | `emir selftest` | this install works, offline, on the bundled sample (19 checks) |
| the analytic cases | `emir validate` | 6 cases with closed-form answers: a necked strap, the hand-entry forms (width loss, Black's derating), transient metrics on uneven time steps, the window, simulated branch currents through a result file into rcc, rcc and its loop residual |
| the simulators | `emir verify-sim --sim <name>` | on your machine, that a simulator's route gives the hand-calculated currents ([Simulators](simulators.md)) |
| the examples | `bash examples/<name>/run.sh` | each flow end to end against golden results (below) |
| the tests | `pytest` | every feature, refusal code and fix, the agent contract, the leak guard |

## The examples, with golden results

| example | runs | golden | proves |
|---|---|---|---|
| [`02_xyce`](../examples/02_xyce/) | Xyce, rcc, on a rail worked out by hand | `expected/summary.json` | rcc against the hand values (FAIL on a contact's peak, 1.04167x) |
| [`03_ngspice`](../examples/03_ngspice/) | the same circuit through ngspice | `expected/summary.json` | the ngspice route; Xyce and ngspice agree |
| [`06_supply_mesh`](../examples/06_supply_mesh/) | a synthetic 10 x 10 supply mesh, Xyce, rcc, invented limits | `expected/screen_n10.json`, `expected/screen_n10_no_period.json` | the four straps that fail at the feed, and their fixes; without `--period`, refused |
| [`08_open_layout`](../examples/08_open_layout/) | the magic route on a SKY130 NMOS array: rcc | `expected/results.json` | rcc equals the simulator's own branch currents (7.8e-13) and node voltages (3.2e-7); without `--period`, refused |
| [`09_cadence_flow`](../examples/09_cadence_flow/) | the extraction plan, save lines and testbench, no Cadence tool | `expected/summary.json` | what a Cadence site would run |
| [`10_mcp_agent`](../examples/10_mcp_agent/) | a scripted MCP client following `emir_next` | `expected/transcript.json`, `expected/tolerances.json` | the MCP server and the go-ahead refusal |
| [`11_hspice_offline`](../examples/11_hspice_offline/) | the HSPICE route on synthetic `.tr0` and CSDF results | `expected/summary.json` | the HSPICE reader against the hand values |

## What EMIR leaves out

- **Sign-off and lifetime.** No mission profile: screen each operating mode on its own.
- **The limit model**: one limit per layer and metric, with a width loss
  (`I_max = J x (W - dW)`) and a temperature derating. No limit tables by width range,
  no Blech length (no short wire is credited), no thermal map: one temperature per run.
- **Your inputs decide**: the limits are your process file's (EMIR checks its form, not
  its values), the currents your simulation's, the geometry your extraction's.
- **One feed per net** (the net's first `*|P` port, or `--feed`), and one DSPF per run:
  no chip assembled from its blocks.
- **Text results only**: binary formats are detected and refused, never guessed at.
- **Tools**: no licensed tool runs where EMIR is built, and none is claimed verified
  anywhere ([Simulators](simulators.md), [Cadence](cadence.md)); Linux only. The
  examples are synthetic or illustrative: no real block has been screened in this
  repository.
