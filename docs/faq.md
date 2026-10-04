# FAQ

**Is an EMIR result a sign-off?**
No. EMIR is a screening tool: it finds EM and IR problems early and says what to
change. Foundry-qualified EM/IR analysis remains the sign-off flow. Every report says
"Screening, not sign-off."

**Do I need a Cadence licence, or any licence?**
No. EMIR's analysis needs only Python with numpy, SciPy and PyYAML. Cadence tools are
one way to make its inputs (the DSPF and the currents) and to see its results on the
layout; Xyce, ngspice, magic and KLayout are another ([Open-source flow](open-source-flow.md)).

**Which PDKs does it support?**
Any PDK you write a process file for ([Onboarding a PDK](pdk-onboarding.md)). The
repository ships files for SkyWater SKY130, GF180MCU and IHP SG13G2, built from their
public data with a citation on every value. Data from a licensed PDK stays with your
PDK installation, never in this repository.

**Can it read SPEF?**
No. EM needs each resistor's width (`J = I / W`) and each via's cut count; SPEF carries
no geometry. Extract a DSPF with geometry annotation ([DSPF requirements](dspf-requirements.md));
on an open PDK, `emir extract --tool magic` writes one from the GDS.

**What are avg, rms and peak, and which one matters?**
The time average drives electromigration; rms measures self-heating; peak guards
against short high pulses. Each is checked where the process file gives a limit, and
an item is judged on its worst one, the governing metric ([Concepts](concepts.md#avg-rms-and-peak)).

**Why does the report list a layer or a metric under "Not checked"?**
Because the process file gives no limit for it, or the DSPF's layer name matches no
process entry. EMIR never invents a limit. Add the limit (with its source) or map the
layer with `dspf_layers`; a CLEAN verdict with unchecked layers that carry current is
not clean.

**rcc or cc-grid?**
rcc (every branch current saved from the RC extraction's simulation) is exact and, on a
small net, fast enough (seconds). cc-grid (device pin currents from a C-only simulation,
pushed through the R-only grid) scales to ADC and PLL tops, and is conservative to first
order, not guaranteed: averages and IR within about 2 %, rms and peak conservative on
the governing branch but not on every branch ([Methods and modes](method-modes.md#accuracy-with-its-sources)).
The choice is yours, never EMIR's: `emir next` without `--mode` lists the routes (rcc,
cc-grid, cc-grid self-consistent) with what each costs and how accurate it is, and
recommends one from the block's size ([Methods and modes](method-modes.md#choosing)).

**Should I run the self-consistent loop?**
When a cc-grid run's droop is above about 2 % of the supply (give the run `--vdd`; it
says so, `EMIR-W520`), the open-loop currents read high. `--mode cc-grid --self-consistent`
re-simulates the CC testbench with each pin fed its own droop waveform until the droop and
the currents agree: on the synthetic meshes it brought rms and peak from +34 % and +70 % to
+0.34 % and +0.78 % of RCC at 4.1 % droop; what it leaves is the grid's own capacitance (a
few percent on minor branches, more on a bigger grid; from an R-only DSPF a few percent on
the devices' peaks too, `EMIR-W544`; from an RC DSPF, `--dynamic --em-basis decap-aware`
scores the branches through the RC grid, `EMIR-W545`, and the default basis, worst, keeps
the R-only grid's high readings; the decaps `cap_models` size are fed back too, one it
cannot size is left out, `EMIR-W547`). Report both, labelled
([Big blocks](big-blocks.md#self-consistency)).

**My ratio differs from my hand calculation. Why?**
Check, in order: the analysis temperature (`em_tref` unless `--em-temp`) and the
`jmax_factor` derating; the resistance scaling when the DSPF was extracted at another
temperature (`EMIR-I308`); the cut count of a via (`EMIR-W207` or `W308`: one cut
assumed); and the window of a transient (the sensitivity the run prints). The run's
console lines (`R at ...`, `rscale`) and the run summary's `temp_C`, `em_tref_C`
and `avg_derate` fields say which applied.

**Why is my via checked as one cut?**
The DSPF gives neither a cut count (`$N`, `$VC`) nor a cut area with a `vcwidth` in
the process file to turn it into one. One cut is the fewest a via can have, so the
check can only over-state the current per cut, and the hint says "at ONE cut" rather
than inventing a count. Extract with via annotation, or add `vcwidth`.

**The run says my simulation result is binary.**
EMIR reads text only. Spectre: `-format psfascii` (or nutascii; a binary PSF result
converts with `psf -i <file> -o <file>.psfascii`). HSPICE: `.option post=2
post_version=2001`. Xyce: `Xyce -a`. ngspice: `.options filetype=ascii`. `emir simulate`
and `emir save-lines` set this up for you.

**My Xyce or ngspice result's names do not match the DSPF.**
Xyce and ngspice name currents their own way. `emir simulate` writes the names
sidecar (`<result>.names`) that maps them; for a result you simulated yourself with
EMIR's save lines, `emir next --cc-sim result.raw ...` plans the
`emir save-lines ... --names-out` step that writes it ([Open-source flow](open-source-flow.md#names-and-signs)).

**`EMIR-E417`: an instance pin reaches the net at several places.**
The simulation saves one current per pin, and how it divides between the connections
depends on the block's own grid, so cc-grid cannot split it. On a small net use rcc;
for a black-boxed block use the [hierarchical flow](chip-level.md). `emir next` plans
either.

**How do I give `emir run` currents from a file?**
Give the file's path: `--currents currents.json`. `--currents` takes inline JSON or the
path of a file holding it, in `emir run`, `check` and `next` alike (`emir schema
currents` describes it).

**How do I see the results on the layout?**
`emir run ... --viewer DIR` writes both: a KLayout marker database
(`klayout block.gds -m DIR/emir.lyrdb`, with the `klayout` extra) and the data the
Virtuoso Explorer loads (**EMIR > Load results...**). `--html DIR` writes a browser
report with a zoomable map of each net, and (with matplotlib) the run's EM/IR chart,
`emir_chart.png`: every net on the layout, the worst items numbered. How to read its
colours: [Concepts](concepts.md#reading-the-charts).

**Can I trust the fix hints?**
As a first-order prescription: widen a wire by its ratio, multiply a via's cuts by its
ratio and round up. Widening one segment changes how current divides in a mesh, so
re-extract and re-run after a fix.

**What does "measured at the original development site" mean?**
Internal measurements made while EMIR was developed, on real test objects that cannot
be published. They are quoted with what was measured, and they cannot be reproduced
from this repository; every other number in these pages can be, with the script named
next to it. They measure the method, never a tool's support: whether a simulator or a
Cadence tool works with EMIR is checked on the machine that uses it
([Simulators](simulators.md#on-site-verification), [Cadence flow](cadence.md)).

**Which simulators does EMIR support, and how do I know they work here?**
Spectre, HSPICE, Xyce and ngspice ([Simulators](simulators.md)). Run
`emir verify-sim --sim xyce` (your simulator's name) once on your machine (with `--run` for Spectre and
HSPICE, which are licensed): it simulates testbenches whose answers are known by hand
(ideal current loads, and real MOSFETs, a resistor and a subcircuit-wrapped device whose
terminal currents and directions are known) and checks every current, ratio and IR drop
EMIR reads back. It covers that simulator version, output format and `--sim-args`, and
the device forms it exercises, not BJTs, diodes or your PDK's models
([what it covers](simulators.md#on-site-verification)). Until the latest verification
for a run's simulator version passes, every run from it says "simulator route not
verified on this machine" (`EMIR-W464`; `EMIR-W414` when that verification failed,
`EMIR-W416` when the result does not say its version).

**Is EMIR's output guaranteed?**
No. EMIR is a research project, provided as is, with no warranty or guarantee of
accuracy. It is your responsibility to test and verify its results for your process,
tools and design before relying on them.

**Does it run on macOS or Windows?**
It is built and tested on Linux; macOS and Windows are not tested.

**An agent ran EMIR for me. What should I check?**
That the report it relayed carries the verdict, the "Not checked" section and the
screening line; that the process file is your PDK's (not the illustrative sample,
`EMIR-W306`); and that it asked you before running any licensed tool or long job.

**Where do I report a problem?**
Run `emir explain <code>` first: most refusals name the command that fixes them. For a
bug, include the code, `emir info`, and the `emir.error/1` document (`--format json`;
`EMIR_DEBUG=1` adds the traceback of an internal error).
