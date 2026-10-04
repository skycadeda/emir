# Concepts

What EMIR checks, what its numbers mean, and where each one comes from. Read this
once before acting on a result.

## Screening, not sign-off

EMIR is a **screening** tool. It tells you, early and cheaply, which wires and vias
are at risk and what to change, so the layout is close to right before the
sign-off run. It is not the foundry-qualified EM/IR flow, and nothing it prints is a
sign-off result:

- it applies the limits in *your* process file, which you (or a script from public
  PDK data) wrote, not the foundry's qualified rule deck;
- it does not model what a sign-off tool models beyond that: no Blech length, no
  per-segment length or width dependence of the limits, no thermal coupling between
  wires (see [Limits](limits.md));
- its currents come from your simulation, under your stimulus and operating mode.

Every result says so: the console's verdict line, the HTML pages, the run summary's
`disclaimer` field and `emir report` all carry "Screening, not sign-off."

## What EMIR checks

A **net** is screened as a resistor network: the extracted resistors of a DSPF (or the
network EMIR builds from a GDS layout). Currents enter and leave it at device pins
and at the port where it is fed. EMIR solves for the current in every branch and
checks each one:

| item | quantity | limit in the process file | unit |
|---|---|---|---|
| a wire segment | current per um of drawn width, `J = I / W` | `jmax_avg`, `jmax_rms` (or `jmax_rms_k`), `jmax_peak` | mA/um |
| a via (a cut array) | the array's **mean** current per cut, `I / cuts` (edge cuts carry more: see below) | `imax_avg`, `imax_rms`, `imax_peak` | mA/cut |

The **ratio** of a check is the quantity over its limit: 1.00x is exactly at the
limit. Each item is judged on its worst metric, the **governing metric**, and gets a
severity:

| ratio | severity |
|---|---|
| at or above 1.00x | FAIL |
| at or above `warn_fraction` (default 0.8) and below 1.00x | WARN |
| below `warn_fraction` | clean |

A **net's verdict** is FAIL when any item fails, WARN when any item warns, CLEAN
otherwise. Two cases are FAIL by rule, never a pass: a ratio that is not a number
(a current that could not be solved), and a wire with no width (it cannot be checked;
`EMIR-E210`).

A metric the process file gives no limit for is **not checked**, and every report
lists it under "Not checked": EMIR never invents a limit (`emir import-ict` writes no
via rms or peak limit an ICT does not give, and a limit an older import estimated is
not applied). SKY130, for example, publishes no peak limits, so peak is not checked
with `pdks/sky130.yaml`.

**A via's ratio is the array's mean per cut.** A DSPF carries an array as one
resistor with a cut count (magic's extraction too: `$cuts`), so the check divides the
array's current by its cuts. The cuts at the edge that faces the current carry more
than that mean. Treat a via ratio above about 0.9x as marginal (the report lists these
under "Not checked"), and confirm it cut by cut (the extractor's per-cut vias). The
cut-count fix (`n -> m cuts`) assumes the current shares equally over the cuts.

## avg, rms and peak

The three metrics answer three different questions:

- **avg**, the time average of the current, is what drives electromigration (metal
  atoms pushed along by the electron flow). All three PDK files in `pdks/` give it.
- **rms**, the root mean square, measures Joule self-heating: a wire heats with I^2
  whatever the direction of the current.
- **peak**, the largest instantaneous |I|, guards against short, high pulses.

How they are computed from a simulated waveform `I(t)`, over the analysis window:

- avg = (1/T) integral I dt, with its sign; the check takes its magnitude.
- rms = sqrt((1/T) integral I^2 dt).
- peak = max |I|.

The integrals use **trapezoid weights on the simulator's own time steps**. A plain
mean over the samples is not the average: a simulator crowds its steps onto edges,
and on the validation suite's non-uniform test signal the mean over samples reads 4.9
times the true average (`emir validate`, case "tran metrics").

**The window** is a whole number of periods: EMIR walks back from the end of the
simulation one period at a time while each period's avg and rms match the last
period's within 2 %, which drops the start-up transient. Give `--period` (the
block's fundamental period); without it the period is detected, and the run prints a
**sensitivity** (how much the average moves when the window is shortened). A large
sensitivity means the answer depends on where the window stops: simulate longer, or
give `--period`. If no period is given and none is detected (aperiodic or
fractional-N activity: no autocorrelation peak of at least 0.90), the whole tail of
the simulation is used, **not period-aligned** (`EMIR-W413`): give `--period`.

From a **PSS** result (harmonics), avg is the DC term, rms comes from the harmonic
amplitudes (Spectre writes single-sided peak amplitudes: rms^2 = |X0|^2 + 1/2 sum
|Xh|^2), and peak from the waveform rebuilt from the harmonics.

With **DC currents** (`--currents`), avg = rms = peak = |I| by construction: the
result is a budget check, and the report lists switching transients as not checked.

**Currents that reverse.** The average of a current that flows both ways cancels toward
zero, and EMIR compares the magnitude of the *signed* average with the process file's
average limit. That is the **least conservative** reading of a bidirectional EM rule:
PDKs that rule on reversing current use a recovery factor (I+ minus r times I-, r below
1) or the average of |I|, and EMIR applies neither. Where it has waveforms (rcc,
cc-grid), EMIR also computes the time average of |I| per branch and lists, under "Not
checked", every branch whose average of |I| reaches the warn fraction of the average
limit while its signed average is much lower: check those against the PDK's AC rule.
The rms check covers the self-heating of such a current, not its EM. `--net all`
screens signal nets by default, so this matters there most.

## Temperature and derating

EM limits depend strongly on temperature, and the process file says at which
temperature its limits hold.

- **`em_tref`** (required, in C) is the temperature the average limits are quoted at.
  There is no default: a guessed temperature shifts every ratio (`EMIR-E312`).
- **The analysis temperature** is `em_tref` unless `--em-temp C` says otherwise.
- **`jmax_factor`** (optional) is a table `{temperature: factor}` that derates the
  *average* limits of wires and vias at the analysis temperature: piecewise linear
  between its points, clamped at its ends; it should be 1.0 at `em_tref` (`EMIR-W316`
  when it is not). Without it, the average limits are used as quoted at any
  temperature. EM limits fall with temperature, so **above `em_tref` without a table,
  or above the table's top (clamped), the average check is not conservative**: the
  input check warns (`EMIR-W315`, `EMIR-W305`) and the run lists it under "Not
  checked". rms and peak limits are not derated.
- **Resistance** follows the analysis temperature. A DSPF that states its extraction
  temperature `T_x` (`*|OPERATING_TEMPERATURE`) equal to the analysis temperature
  `T_a` (within 0.5 C) is used as it is. Otherwise each wire resistor is scaled by its
  own layer's `f(T_a) / f(T_x)`, with `f(T) = 1 + tc1 (T - T_ref) + tc2 (T - T_ref)^2`
  and `T_ref` the process file's `temp_reference`; a DSPF that states no temperature is
  taken as extracted at `temp_reference` (`EMIR-I308` reports a DSPF extracted at
  another temperature). Via and contact resistances are not scaled (the process file
  gives vias no TCR). Only the IR drop depends on this. Best practice: extract at the
  EM analysis temperature.
- **The rms limit** comes in one of two forms. A fixed value, as a tech LEF publishes
  it (`jmax_rms`, mA/um), or a self-heating constant, as an ICT gives it
  (`jmax_rms_k`): the limit is `sqrt(jmax_rms_k x delta_T)` mA/um with `delta_T` the
  temperature rise you allow, `--delta-t` (default 5 C). Where that limit is below the
  average limit, the self-heating limit governs: rms can never be below |avg|, so a DC
  current fails rms before avg. EMIR keeps your `delta_T` and warns (`EMIR-W301`,
  which names the `delta_T` at which the two limits would meet); it never raises the
  budget by itself. Confirm the self-heating budget against the PDK, and change it
  with `--delta-t` only from there.

## IR drop and its reference

The **feed** of a net is where its current comes from: its `*|P` port in the DSPF (the
first one, if it has several), or the node you name with `--feed`. Potentials are
solved against it, and the current that closes Kirchhoff's current law enters there.

- **IR drop** is the largest difference in potential across the net, from the
  average currents. On a net with a port it is the drop from the feed to the worst
  node, device pins included (so contact resistance counts); the report calls it
  "drop". On a net with no port it is the spread from the lowest to the highest
  node; the report calls it "spread". It is always reported as a magnitude, for
  supply and ground nets alike.
- **`--ground NODE`** measures potentials against another node of the net; the branch
  currents do not change, only the reference.
- **Power nets** set the IR colour scale and are what the viewers open on. They are
  inferred from their names (VDD, VSS, VCC, GND, AVDD, VPWR, VGND and similar); a bias
  or reference line such as VREF is deliberately not a rail. `--power-nets` names
  them explicitly.
- **Dynamic IR**, the instantaneous droop, needs the capacitance and the waveforms.
  In rcc mode it is rebuilt from the measured branch currents; in cc-grid mode
  `--dynamic` integrates the R-C grid with the device currents as drive (see
  [Big blocks](big-blocks.md)).
- A net fed from several pads is fed at one of them in a flat run. Use `--feed`
  deliberately, or the [hierarchical flow](chip-level.md), which holds several pads at
  one potential.

## The input checker

Every `emir run` starts with the input checker, and `emir check` runs it alone,
changing nothing. It reads the DSPF, the process file, the currents or simulation
result, and the output settings, and reports each finding with a level, a code and the
fix:

| area | examples of what it catches |
|---|---|
| files | a missing or unreadable input (`E101`, `E102`) |
| DSPF | not a DSPF (`E217`), no resistors (`E202`), no widths at all (`E203`), no node coordinates (`W202`), no instance-pin records (`W203`) |
| process | a missing `em_tref` (`E312`), a layer with both rms forms (`E310`), an unknown key in an entry (`E316`), a layer or via with no limit (`W302`, `W304`), the illustrative sample file (`W306`) |
| layers | DSPF layers with no process entry, which would go unchecked (`W307`) |
| vias | a cut area with no `vcwidth` to turn it into a count (`W308`), a via with neither count nor area, checked as one cut (`W207`) |
| nets | a net that is not in the DSPF (`E204`), a net in pieces (`E207`), nothing to drive it (`E208`) |
| currents | not JSON, NaN, nodes off the net, currents that do not balance (`E401` to `E406`) |
| simulation | binary or truncated results, branch or pin currents not saved (`E415`, `E416`), a pin that reaches the net at several places (`E417`) |
| outputs | an output directory that cannot be written (`E701`) |

FAIL findings stop the run (exit status 2) unless `--force` is given; WARN findings are
printed and the run goes on; INFO lines say what was decided, such as the mode chosen
for each net. `emir check --format json` gives the findings with a `command` each, and
`next_steps` holds the commands that fix or diagnose the failures. `emir explain
<code>` prints any code's meaning, hint and command; the whole catalog is
[Error codes](errors.md).

## Fix hints

A ratio is a diagnosis; the fix hint is the prescription, in the units a layout
engineer works in:

- a wire at ratio r: **widen W to past W x r**, rounded up to the next 5 nm strictly
  above it (`Metal1: widen 0.750 -> 1.255 um (x1.67)`: 0.75 x 1.667 = 1.250 um would be
  exactly at the limit, 1.00x, still a FAIL);
- a via with n cuts at ratio r: **floor(n x r) + 1 cuts**, the fewest that bring it
  below the limit (`Cont: 1 -> 2 cuts (+1)`);
- a via whose cut count the DSPF does not give: `x1.33 at ONE cut`, since writing
  "1 -> 2" would invent a count.

Hints are given only at 1.00x and above (below the limit the arithmetic would say
"narrow this wire", which is not a fix). They are first-order: widening one segment
changes how current divides in a mesh, so re-extract and re-run after a fix. The
hints reach the report, the HTML, the run summary and the markers in Virtuoso and
KLayout.

## What a run produces

| output | how | for |
|---|---|---|
| the console log | always (stderr with `--format json`) | a person watching |
| the run summary, `emir.run/1` | `--format json` (stdout) or `--summary-out FILE` | agents and scripts; `emir report` reads it |
| the report | `emir report --from FILE` (Markdown, or `--format json`) | what an agent relays to a person |
| HTML | `--html DIR` | a person: an index of nets with the run's EM/IR chart (`emir_chart.png`, with matplotlib), and a page per net with a zoomable map |
| viewer data | `--viewer DIR` | the results on the layout: the Virtuoso Explorer (`emir_data.il`, `emir_explorer.il`) and a KLayout marker database (`emir.lyrdb`, with the `klayout` extra) |
| tap file | `--taps-out FILE` | each instance pin's avg, rms and peak current at its coordinates (CSV) |

The summary's `not_checked` field, and the report's "Not checked" section, list what
the run could not judge: layers with no process entry, metrics with no limit, limits
flagged as estimates, pins whose current was not saved, vias assumed to have one cut,
wires without a width, average limits not derated to a hotter analysis temperature,
branches whose reversing current cancels in the signed average, via arrays within
10 % of the limit (checked at their mean per cut), and the decaps whose straps the EM
basis does not cover (no `--dynamic`, a decap `cap_models` cannot size, the decap-free
basis, decap-aware EM not computed: `decap_straps_not_covered`). Relay it with the verdict: a CLEAN net with half its layers
unmapped is not clean.

## Reading the charts

Every picture EMIR draws uses one colour scale (`emir/style.py`): the run's chart and
the net pages of `--html`, the Virtuoso Explorer's colour packets, and the figures of
these docs. The layout is drawn on black, dimmed under the results (by KLayout, in the
PDK's own layer colours when its `.lyp` is found under `$PDK_ROOT`, else a neutral grey
per layer; a DSPF-only run draws each element from the DSPF's coordinates, with no
layout under it).

| colour | EM: an element's governing ratio (its current over its limit) | IR: an element's drop from its net's feed |
|---|---|---|
| blue, dark to light | below the warn fraction, by the ratio's log: dark at 0.001x and below, light at the warn fraction (0.8x by default) | 0 (dark) to the largest drop shown (light) |
| amber | the warn band: the warn fraction to 1x | |
| red | 1x and over, **or not a number** (a failed current is a failure, never a shade) | a drop that is not a number |
| grey | no limit stated in the process file: not checked, never passed | |
| red outline | a violation's marker | |
| dotted, light | a block model in a chip-level run (an abstract's port-to-port conductance): its EM is checked in the block's own abstract run, not here | the same |
| white ring | | each net's worst node (none where the drop prints as ~0); the three worst are labelled |

The worst items are numbered on the map and listed beside it with where they are and
their fix, equal ratios left to right; the cell outline is dashed. The subtitle names
the mode, and for cc-grid whether the droop was fed back: "self-consistent (converged
in 4 passes, tol 0.2 mV)" (or settled; the tolerance the last pass was held to), or
"open loop (droop 9.0% of the supply)"; an open loop above about 2 % (EMIR-W520) and a
loop that reported pass 0, the open loop (diverging, EMIR-W406; at its pass limit, W407;
a pass's simulation past its time limit or failed, W543), are warnings under "Not
checked", in the warn colour. The subtitle also names the EM basis the ratios are on
("EM basis worst (the default)", "EM basis decap-free (no --dynamic)": see
[Decap-aware EM](big-blocks.md#decap-aware-em)). On a `--dynamic`
run the IR panel is still the average (static) drop: a note gives the dynamic droop's
peak and where it is (open loop, or from the self-consistent loop's reported pass), and
the report has every net's. Each chart names every colour in its legend and ends with
"Screening, not sign-off." and the research-project disclaimer.

![Screenshot of EMIR's per-net HTML report: verdict FAIL, the worst EM item just over its limit on a contact, the net drawn as a vector map, coverage per metric and the violations table.](images/html_report.png#only-light)
![Screenshot of EMIR's per-net HTML report: verdict FAIL, the worst EM item just over its limit on a contact, the net drawn as a vector map, coverage per metric and the violations table.](images/html_report_dark.png#only-dark)

*The HTML report of examples/02_xyce (Xyce currents, illustrative limits).*
