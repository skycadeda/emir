# 02 Big blocks: CC simulation plus an R-only extraction (cc-grid)

An ADC top or a PLL top is too big to simulate RC-extracted with every branch current saved (RCC): the transient
converges poorly and slowly. The route that scales: simulate the devices with a C-only extraction (or the
schematic) and save only the instance-pin currents (CC), extract R-only for the grid, and let EMIR superpose the
pin currents through the R grid (`--mode cc-grid`). **Screening, not sign-off.**

## When to use

- `emir next --dspf <dspf>` reports `facts.size.class` `big` (more than 50 000 DSPF nodes, a heuristic), or
  `medium` and the person prefers the CC route.
- The person says the RCC simulation does not converge, runs for hours, or writes results too big to read.
- The block is one periodic or coherent operating mode (a locked PLL, an ADC converting a coherent tone).
- Not for a whole chip: screen each block this way, then playbook 03. Not for a small net (500 nodes or fewer)
  whose RC simulation runs in minutes: rcc there is exact, and the time cc-grid saves is seconds (playbook 01).
- Also for the mode decision of any block: `emir next` without `--mode` (its `choose-mode` step), and a cc-grid
  run whose IR drop is above about 2 % of the supply (EMIR-W520).

## Choosing the mode (the person's choice)

The mode is never yours to pick. With a simulation in the picture and no `--mode`, `emir next`'s first step is
`choose-mode` (`needs_human`): its `choices` are the three routes, each with what it simulates, its cost and its
measured accuracy (synthetic meshes, Xyce: `examples/06_big_block/results/`), whether it can work with what the
person has (`possible`, `why_not`) and the one the block's size `recommended`:

| route | what runs | cost (measured on the synthetic meshes) | accuracy against rcc |
|---|---|---|---|
| `rcc` | the RC netlist, every branch current saved | 2.0x the CC route at 132 nodes, 32.4x at 4 932; converges poorly and slowly at ADC/PLL-top size | the reference: exact |
| `cc-grid` | the devices on ideal rails, pin currents superposed through the R-only grid | one CC simulation (0.25 s to 0.40 s with 32 inverters, any mesh size) + the superposition | open loop: avg and IR within 0.4 % at 0.1-0.6 % droop, +1.34 % at 4.1 % (256 inverters, 5 156 nodes); rms and peak read high (+80 % peak there) |
| `cc-grid` self-consistent | cc-grid, then the CC testbench again with each pin fed its own droop waveform, until droop and currents agree | 4 or 5 simulations at 0.35-0.55 % droop, 6 to 10 at 4.1-7.9 %; on grids this small more than the RCC simulation (31 s against 1.9 s at 4.1 %), a big-block speed-up is extrapolated, not measured | the governing branch within 0.14 % (avg, rms, peak) on one net up to 432 nodes at reltol 1e-4 (VSS closed with VDD: peak +0.45 %), +0.01 / +0.34 / +0.78 % at 4.1 % droop (128 inverters, 384 nodes; open loop +1.0 / +34 / +70 %); each device's peak within 1.3 % (2 fF a node, or fed back from an RC DSPF); what remains is the grid's own capacitance: minor branches within 3.1 % on the 32-inverter meshes of 132 and 432 nodes (4.7 % in four phases), 11.3 % on 1 632, -4.1 % to +5.1 % with 128 inverters at 4.1-7.9 % droop, and from an R-only DSPF the devices' peaks low too (EMIR-W544: up to 4.8 % at 20 fF a node); from an RC DSPF the branches scored through the RC grid only with `--dynamic --em-basis decap-aware` (EMIR-W545: minor branches -19.5 % to +25.3 % without `--dynamic`, within 0.52 % with it, at 20 fF a node; the default basis, worst, keeps the R-only grid's high readings); decaps `cap_models` cannot size are left out of the droop fed back (EMIR-W547: the devices low, those decaps' straps not checked) (accurate, not conservative) |

Tell the person the recommendation and why (rcc for a small net; cc-grid for an ADC or PLL top, because RCC
transients converge poorly and slowly there), say where only one mode can work (a pin at several DSPF connections:
rcc only; a result holding only pin currents: cc-grid only), ask, and run the chosen route's `command`. A request
that already names the route ("the CC simulation", "screen it self-consistently") is their choice.

## Inputs

| input | what it must be | ask / check |
|---|---|---|
| R-only DSPF of the block | R extraction with `$W`/`$L`, via cut counts, coordinates, `*\|I` instance pins (RC instead, for `--dynamic`) | `emir nets <block_r.dspf> --format json` |
| C-only (coupled) extraction | the netlist the person simulates; from the same LVS run, so instance names match the R-only DSPF's `*\|I` records | ask the person |
| the CC simulation result | instance-pin currents saved (`emir save-lines --mode cc`), ASCII, whole periods of one operating mode | `emir check ... --cc-sim <result>` |
| the period | the PSS period, or the repetition period of a coherent transient, in s | ask the person |
| process file | the PDK's limits (playbook 04) | `emir check` |

## Commands

### Worked example (runs as printed from the repository root)

A synthetic mesh from example 06 (40 x 40 nodes of Metal1, 100 taps, invented process data) with a synthetic CC
result: 5 periods of 10 ns, written by the generator, no simulator needed.

<!-- test: run -->
```sh
python examples/06_big_block/mesh.py stream --n 40 --k 100 --nt 301 --out emir_out/big
emir next --dspf emir_out/big/s40_k100_t301.dspf --process examples/06_big_block/process.yaml --format json
```

<!-- test: expect -->
```json
{"schema": "emir.next/1", "facts": {"size": {"class": "medium", "total_nodes": 1701}},
 "mode": {"chosen": null}, "steps": [{"id": "choose-mode", "needs_human": true, "status": "waiting"}]}
```

The plan stops at the person's choice: `cc-grid` is recommended (1 701 nodes, above the small-net size). Say
which mode each route is, what it costs, and ask. Here the person chose cc-grid:

<!-- test: run -->
```sh
emir next --dspf emir_out/big/s40_k100_t301.dspf --process examples/06_big_block/process.yaml --mode cc-grid --format json
```

<!-- test: expect -->
```json
{"schema": "emir.next/1", "mode": {"chosen": "cc-grid"}}
```

With no result yet the plan asks for the CC simulation (its simulate step is `waiting` on the testbench and
`needs_human`: a long run). The save lines for a Spectre testbench whose instance of the block is `XTOP`:

<!-- test: run -->
```sh
emir save-lines emir_out/big/s40_k100_t301.dspf --mode cc-grid --prefix XTOP. --out emir_out/big/emir_save.scs --format json
```

<!-- test: expect -->
```json
{"schema": "emir.save_lines/1", "mode": "cc-grid", "count": 100}
```

With the result, check and run in cc-grid mode with the period:

<!-- test: run -->
```sh
emir check emir_out/big/s40_k100_t301.dspf --process examples/06_big_block/process.yaml --cc-sim emir_out/big/s40_k100_t301.raw --mode cc-grid --period 1e-8 --format json
emir run emir_out/big/s40_k100_t301.dspf --process examples/06_big_block/process.yaml --cc-sim emir_out/big/s40_k100_t301.raw --mode cc-grid --period 1e-8 --summary-out emir_out/big/summary.json --format json
```

<!-- test: expect -->
```json
{"schema": "emir.run/1", "verdict": "FAIL", "worst_net": {"net": "VDD", "ratio": 5.006},
 "nets": [{"mode": "cc-grid", "governing_metric": "avg", "ir_drop_mV": 4.117,
           "superposition": {"method": "dense", "taps": 101, "points": 301}, "dynamic": null}]}
```

The same run with the superposition streamed in time chunks (what `--superposition auto` does by itself when the
dense matrix would exceed `--max-memory`, default 2G): the same answer, bounded memory, notice EMIR-I450.

<!-- test: run -->
```sh
emir run emir_out/big/s40_k100_t301.dspf --process examples/06_big_block/process.yaml --cc-sim emir_out/big/s40_k100_t301.raw --mode cc-grid --period 1e-8 --superposition stream --summary-out emir_out/big/summary_stream.json --format json
emir report --from emir_out/big/summary_stream.json
```

`--dynamic` on this R-only DSPF gives `dynamic: null` and EMIR-W450 (no capacitance on the net): the dynamic droop
needs an RC extraction of the rails or decap devices the process file's `cap_models` can size.

### On the person's files

```sh
emir next --dspf <block_r.dspf> --process <pdk.yaml> --mode cc-grid --format json
emir save-lines <block_r.dspf> --mode cc-grid --prefix <XTOP.> --out emir_save.scs --format json
emir check <block_r.dspf> --process <pdk.yaml> --cc-sim <result> --mode cc-grid --period <T> --format json
emir run <block_r.dspf> --process <pdk.yaml> --cc-sim <result> --mode cc-grid --period <T> --vdd <V> --summary-out emir_out/summary.json --format json
emir report --from emir_out/summary.json
```

Give the run the rail's `--vdd`: then a droop above about 2 % of it is EMIR-W520, and the run's `next_steps` plan
the self-consistent route (a step with `needs_human: true`). That route, when the person asks for it (one net, or
with Xyce, ngspice or HSPICE several in one loop, `--net VDD,VSS`; Spectre one net per run; `--sim
xyce|ngspice|hspice|spectre`; Spectre and HSPICE only with `--run`, after their go-ahead):

```sh
emir next --dspf <block_r.dspf> --process <pdk.yaml> --net <rail> --mode cc-grid --self-consistent --vdd <V> --netlist <cc_testbench> --sim xyce --format json
emir run <block_r.dspf> --process <pdk.yaml> --net <rail> --mode cc-grid --self-consistent --netlist <cc_testbench> --vdd <V> --sim xyce --period <T> --summary-out emir_out/summary_sc.json --format json
```

Xyce, ngspice and HSPICE need no edit: EMIR writes a split copy of the testbench (a node and a 0 V probe per instance
pin) and an include of the split's sources (each pin's droop as an inline PWL) it rewrites each pass; `--net VDD,VSS`
closes both rails in one loop. Spectre's netlist is not read by EMIR: the person adds the split once (`emir run
<block_r.dspf> --net <rail> --split-stub` prints it: a `vsource type=pwl file=` per pin, UNVERIFIED). `nets[].self_consistency`
holds every pass (pass 0 is the open loop): the residual and its tolerance, how the droop was fed, the time points,
KCL, `converged`.

The simulation in between is the person's (Spectre: playbook 06; Xyce or ngspice: playbook 05, `--mode cc-grid`),
and needs their go-ahead. It must cover whole periods of one settled operating mode:

- a locked integer-N PLL or any periodic block: PSS, preferably its time-domain output (exact rms and peak;
  `--dynamic` needs waveforms); `--period` is the PSS period;
- a fractional-N PLL or an ADC: a transient with a coherent stimulus (the ADC's input at M/N of the sample rate,
  M and N coprime, so the record repeats every N conversions); `--period` is the repetition period;
- one simulation per operating mode (start-up, calibration, steady state, standby); never average across modes.

## Decision points

| you see | do |
|---|---|
| `facts.size.class` `small` | playbook 01 with rcc; this one is not needed |
| `nets[].superposition.method` `stream`, notice EMIR-I450 | nothing: the same answer in bounded memory |
| `nets[].superposition.method` `scalar` or `combined` | not the exact waveform superposition: `scalar` is per-tap in-phase upper bounds (`--scalars`, `--align inphase`), `combined` the blocks' alignment (`--align async`, a phase sweep); say rms and peak are bounds |
| EMIR-W452 (`--superposition dense` above `--max-memory`) | the run streamed instead: the same answer; nothing to do |
| EMIR-W550 (`--max-memory` above 80 % of the physical memory) | the budget was lowered to that; ask the person before raising it |
| EMIR-E418 out of memory | one `--net` at a time, or a lower `--max-memory` (the streamed path then takes over) |
| EMIR-W520, or a power net's `ir_drop_mV` above about 2 % of its supply (or its dynamic droop, when `--dynamic` ran) | the open-loop error grows with the droop (measured: avg +1.34 % at 4.1 % droop, rms and peak far more): report the result as open loop, and offer the person the self-consistent run (above; it re-simulates, so it is their call). Say what it does: it brings the device currents to RCC's (on the synthetic meshes rms and peak from +34 % / +70 % to +0.34 % / +0.78 % at 4.1 % droop, 128 inverters on 384 nodes) and leaves the grid's own capacitance (minor branches a few percent off, more on a bigger grid; from an R-only DSPF, EMIR-W544, the devices' peaks a few percent low): report both runs, labelled |
| the self-consistent run: `converged` false | EMIR-W407 (the pass limit), EMIR-W406 (the guard: the residual grew twice or the droop reached half the supply) or EMIR-W543 (a later pass ran past its time limit or failed: diverging, or just slow); `stopped` says which; in every case pass 0, the open loop, is reported: report it as the open loop, not as self-consistent, and which way it reads as the report says (when pass 1 drew more current with the droop fed back -- a load whose current rises as its supply falls, the usual cause of EMIR-W406 -- it reads LOW, not conservative); quote the passes' residuals; raise `--max-passes` or `--sim-timeout` (the run's next steps offer it) only with the person |
| the self-consistent run: `stopped` `settled`, or `at_pass_limit` | it converged by the metric rule (the answer held still, the residual above its tolerance: quote both) or on its last allowed pass: say so; more passes only with the person |
| EMIR-E540 | a device terminal on the net is no DSPF pin: the waveform loop would leave it on the ideal rail; check the testbench's instance names and `--save-prefix` with the person |
| EMIR-W540, EMIR-W541, EMIR-W542 | the testbench's reltol is above 1e-4 (tell the person: the simulator's error is as large as what the loop corrects); the split's KCL does not close (another load on the rail); the PWL was compressed more coarsely than its tolerance (more breakpoints than the cap): accuracy is lost, say so |
| EMIR-W544 | the DSPF has no capacitance on the net: the droop fed back is the R-only grid's, so the devices' peaks and minor branches can read low; tell the person, and offer the RC extraction of the rail |
| EMIR-W545 | the loop fed back the RC grid's droop but scored the branches on the R-only grid (no `--dynamic`): minor branches can read low or high; tell the person, and offer the same run with `--dynamic` and the block's `--period` (the run's next step; it re-simulates, and the EM basis it brings is their choice) |
| EMIR-W547 | decap devices `cap_models` cannot size are left out of the droop fed back: the devices, the decaps' straps and the branches near them read low; ask the person for the models' values (`emir process check --dspf`) |
| EMIR-W546 | the testbench's `.tran` saves from a start time: the loop held the first droop before it; tell the person to save from 0 |
| `nets[].dynamic` null with EMIR-W450 | read its message: no capacitance (extract the rails RC, or add `cap_models`), a harmonic result (use the PSS time-domain output), or a net in pieces |
| EMIR-W451 | the dynamic droop still holds start-up: simulate more periods |
| EMIR-W403 some pins not saved | they count as zero current: add their save lines and re-simulate |
| EMIR-E417 a pin at several connections | cc-grid cannot divide one saved current: playbook 03 (a black-boxed block), or rcc for that sub-block |
| EMIR-E550 | `--dynamic` needs waveforms: drop `--scalars`, or give a transient result |
| EMIR-W413 the window is not settled | give `--period`, or simulate longer |
| the person asks for EM with the decaps (their straps, decap-aware EM) | pass `--dynamic` (and their `--em-basis`, if they named one) to `emir next`: without it the plan is the static verdict, which gives a decap's straps nothing |
| EMIR-W409, W410 (`not_checked.decap_straps_not_covered`; `em_basis.not_computed`: decaps-not-sized) | a decap `cap_models` cannot size (a cox left null) is left out of the decap-aware solve: its straps are not checked and the basis asked for is not honoured (`--fail-on-violation` exits 1). Ask the person for the value (`emir next --run-summary <summary>` lists it in `missing[]`); never estimate it |
| `nets[].dynamic_em` (with `--dynamic`) | decap-aware EM beside decap-free: the verdict is on `nets[].em_basis.used` (`worst`, the default: per segment the larger of the two). Relay both; the basis is the person's choice, never yours, never to make a FAIL pass |
| the bases disagree (the report's "The person's choice"; EMIR-W436, W437) | `emir next --run-summary emir_out/summary.json --format json`: its `choose-em-basis` step (needs_human) lists each basis's verdict and worst ratio; ask the person and run the `command` of the choice they pick |
| EMIR-W430 .. W434 (`em_basis.fallback`) | decap-aware EM not computed on that net (no feed; `--ground` not the feed; not at periodic steady state; the window not whole periods; the step too coarse): its verdict is decap-free, NOT the basis asked for (`em_basis.honoured` false; `--fail-on-violation` exits 1). Tell the person; never relay it as a pass. `emir next --run-summary <summary>` plans the fix as their step (`--dyn-steps N`, the run without `--ground`, `--period`; past any practical `--dyn-steps`, a question for the person); never drop `--period`, add `--ground` or lower `--dyn-steps` to get a verdict |
| EMIR-W435 | the steps lost more than 2 % of a pin's AC rms (rare: they resolve the waveforms when they can); decap-aware rms is scaled up by it: the run's next step has twice the steps |
| EMIR-W428 | a cc-grid run without `--dynamic` over decap devices: the static verdict gives a decap's straps nothing. Say so; `--dynamic` (the person's choice) gives decap-aware EM: `emir next --run-summary <summary>` plans that re-run as their step (`rerun-with-dynamic`, needs_human) |
| EMIR-W429 | waveform features too narrow for the steps but negligible (below 1e-6 of their pin's charge and energy per period) were left out: decap-aware EM was computed. Relay it beside the peaks: a decap strap's current for less than the finest step is not in them |
| EMIR-E317 | a `cap_models` entry the decap model cannot use (a value not a number, a negative C or R, no capacitance at all, a fit frequency not positive, a fit range not low to high): ask the person for the PDK's values; never edit the process file |
| EMIR-W438, W439 | decap-aware leans on the decap model (coupling share, a `default` cap model, the closed-form access R, an ESR fitted far away), or decaps sit between two resistive rails (their straps read low): say so beside the numbers |
| EMIR-E430 | `--em-basis decap-aware` or `worst` without `--dynamic`: add `--dynamic` (cc-grid, a transient result), or drop `--em-basis` |
| a ratio from 0.98 to 1.0 on the governing branch (inside the +-2 % guard band) | marginal: say so; an rcc run of that sub-block decides it |
| a ratio between the warn fraction (0.8) and 0.98 | WARN: below the limit, inside the margin; report it |

cc-grid's error against RCC is measured in `examples/06_big_block/README.md` (synthetic meshes, Xyce, serial; not
a real design, not Spectre): the governing branch's average within -0.33 % to +1.34 % of RCC, its rms and peak
above RCC (conservative), but the peak of secondary and minor branches down to -19 %, and the error growing with
the droop. Quote it with that label, never as "always conservative". The average-droop loop the self-consistent
loop replaced (`examples/06_big_block/results/selfconsist.csv`, its record) took the average and the IR from
-0.33 %..+1.34 % to -0.34 %..-2.02 % (non-conservative) and left rms and peak a few points lower but still high
(+28.5 % and +58.6 % at 4.1 % droop); the waveform loop's numbers are in the table above.

## Success criteria

- `emir check` exits 0 with `--mode cc-grid`; `emir run` exits 0 (or 1 with `--fail-on-violation` and a FAIL).
- The summary has every screened net with `mode` `cc-grid` and a `superposition` entry.
- `emir report --from emir_out/summary.json` ran before you answered, and the report you relay names the operating
  mode the simulation covered and the period used.

## What to report

The verdict, per net the governing metric and worst ratio with the fix, the IR drop (and the dynamic droop when
computed), that the currents came from a CC simulation superposed through the R-only grid (cc-grid) over the given
period, what was not checked, the accuracy caveat above for ratios near 1.0, and "Screening, not sign-off." For the
worked example: FAIL, VDD avg 5.006x on Metal1 R1 near the feed (widen 0.400 -> 2.002 um), IR 4.117 mV, synthetic
data and invented limits.

With `--dynamic`, also the report's "Decap-aware EM" section as it is: the basis the verdict is on, each basis's
verdict and worst ratio, the feed current with and without the decaps, and the person's choice when the bases
disagree ([examples/13_decap_aware](../../examples/13_decap_aware/) shows one).

## Failure modes

| code | exit | meaning | do |
|---|---|---|---|
| EMIR-E418 | 2 | the analysis does not fit in memory | `--net` per run, `--max-memory` |
| EMIR-E417 | 2 | a pin at several connections | playbook 03 |
| EMIR-E410 | 2 | the result cannot be read (binary) | ASCII output (`-format psfascii`, `Xyce -a`) |
| EMIR-E550 | 2 | `--dynamic` without waveforms | a transient or PSS time-domain result |
| EMIR-W404 | 0 | PSS harmonics truncated | more harmonics, or the time-domain output |
| EMIR-W411 | 0 | resampling lost peak current in `--dynamic` | raise `--dyn-steps` |
| EMIR-E601 | 3 | Spectre not found for `--netlist --run` | simulate elsewhere and pass `--cc-sim` |
| EMIR-E520 | 2 | no `--mode` with a simulation | ask the person (`emir next` without `--mode` lists the routes) |
| EMIR-E522 | 2 | the self-consistent split matched no instance pin | the testbench's instance names are not the DSPF's, or `--save-prefix` is wrong |
| EMIR-E523 | 2 | `--vdd` differs from the testbench's source for the net | the wrong net or testbench: check with the person (without `--vdd` the run reads it) |
| EMIR-E524 | 2 | the Spectre netlist lacks the per-tap split | the person adds the `--split-stub` lines and reconnects each pin |
| EMIR-W406, EMIR-W407, EMIR-W543 | 0 | the self-consistent loop diverged, hit `--max-passes`, or a later pass's simulation ran past its time limit or failed | report it; pass 0 (the open loop) is what all three report |
