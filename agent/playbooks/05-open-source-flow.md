# 05 The open-source flow: Xyce or ngspice supply the currents

No licence anywhere: Xyce or ngspice simulates the block, `emir simulate` adds the lines that save the currents
and maps the simulator's names onto the DSPF's, and the run is the same as with Spectre. KLayout shows the results
on the layout. Before the first run that uses the simulator on this machine, `emir verify-sim` proves its route here.
**Screening, not sign-off.**

## When to use

- `emir info --format json` shows `xyce` or `ngspice` with `found: true`, and the person has no Spectre or does
  not want it used.
- The person asks for an open-source flow, or the PDK is an open one (sky130, gf180mcu, IHP SG13G2).
- If Spectre is found, `emir next` plans the Spectre route instead (playbook 06); it is licensed: ask before
  running it.

## Inputs

| input | what it must be | ask / check |
|---|---|---|
| DSPF | RC with its devices for rcc (the testbench simulates the DSPF itself), or R-only for cc-grid; with only a GDS on an open PDK, `emir extract --tool magic` writes one, and a CC netlist with it (playbook 09) | `emir nets <block.dspf> --format json` |
| process file | `pdks/<pdk>.yaml` or the person's (playbook 04) | `emir check` |
| a testbench, in SPICE | supplies, stimulus, models, a `.tran` over whole periods of the block's activity; it includes the DSPF (rcc) or a CC netlist of the same devices (cc-grid), the block instantiated once | ask the person: EMIR never writes a stimulus. A testbench is code: an ngspice `.control` block, or the `.spiceinit` beside it, can run any program (`shell`); simulate only decks the person trusts |
| the mode | `--mode rcc` or `--mode cc-grid` (or cc-grid `--self-consistent`): the person's choice; `emir next` without it lists the routes and what the testbench can give | ask the person |
| the period of the activity | for `--period` | ask the person |

## Commands

### Worked example (runs as printed from the repository root)

Example 02's invented block: a 60 um Metal1 rail with four NMOS sinks that pulse one after another every 10 ns,
the bundled ILLUSTRATIVE process file. With Xyce, the CC route (devices on an ideal rail, a 0 V probe on each
subcircuit pin):

<!-- test: run requires=xyce -->
```sh
emir next --dspf examples/02_xyce/loadrail.dspf --process emir/data/sample/process.yaml --netlist examples/02_xyce/tb_cc.cir --period 1e-8 --format json
```

<!-- test: expect -->
```json
{"schema": "emir.next/1", "mode": {"chosen": null},
 "steps": [{"id": "choose-mode", "needs_human": true}, {"id": "selftest"}, {"id": "nets"}]}
```

The first step is the person's choice of mode. Its `choices` say that rcc cannot work with this testbench (a CC
netlist: it does not hold the DSPF's resistors), so cc-grid is the recommendation; the person confirms it (their
choice, not yours), and the plan follows:

<!-- test: run requires=xyce -->
```sh
emir next --dspf examples/02_xyce/loadrail.dspf --process emir/data/sample/process.yaml --netlist examples/02_xyce/tb_cc.cir --period 1e-8 --mode cc-grid --format json
```

<!-- test: expect -->
```json
{"schema": "emir.next/1", "mode": {"chosen": "cc-grid"},
 "steps": [{"id": "selftest"}, {"id": "nets"},
           {"id": "verify-sim", "command": "emir verify-sim --sim xyce --format json", "needs_human": false},
           {"id": "simulate", "status": "todo"}, {"id": "check"}, {"id": "run"}, {"id": "report"}]}
```

The plan verifies Xyce's route on this machine first (no record of it here yet). It runs in seconds: six short
simulations of testbenches whose currents are known by hand (ideal loads, and real devices read with the
forms the cc route uses), read back and screened in both modes:

<!-- test: run requires=xyce -->
```sh
emir verify-sim --sim xyce --out emir_out/verify-sim/xyce --format json
```

<!-- test: expect -->
```json
{"schema": "emir.verify_sim/1", "simulator": "xyce", "ran": true, "passed": true, "format": "nutascii"}
```

From now on a run from this Xyce says `simulator_verification.status` `verified`; without the record it says `not
verified` and carries EMIR-W464. Then simulate the person's testbench:

<!-- test: run requires=xyce -->
```sh
emir simulate examples/02_xyce/tb_cc.cir --sim xyce --dspf examples/02_xyce/loadrail.dspf --mode cc-grid --probe --process emir/data/sample/process.yaml --out emir_out/sim/result.raw --format json
```

<!-- test: expect -->
```json
{"schema": "emir.simulate/1", "mode": "cc-grid", "signals": 4, "found": 4, "missing": []}
```

`emir simulate` wrote the deck it ran beside the result (the testbench is never edited), `result.raw` (ASCII) and
`result.raw.names`, the sidecar that maps Xyce's names onto the DSPF's `inst:pin`. Then:

<!-- test: run requires=xyce -->
```sh
emir check examples/02_xyce/loadrail.dspf --process emir/data/sample/process.yaml --cc-sim emir_out/sim/result.raw --mode cc-grid --period 1e-8 --format json
emir run examples/02_xyce/loadrail.dspf --process emir/data/sample/process.yaml --cc-sim emir_out/sim/result.raw --mode cc-grid --period 1e-8 --summary-out emir_out/summary.json --format json
```

<!-- test: expect -->
```json
{"schema": "emir.run/1", "verdict": "FAIL", "worst_net": {"net": "VDD", "ratio": 1.0417},
 "nets": [{"mode": "cc-grid", "governing_metric": "peak", "ir_drop_mV": 28.07,
           "worst": {"name": "Rc3", "layer": "Cont", "kind": "via", "metric": "peak"}}]}
```

The same block with ngspice and the rcc route (the testbench includes the RC DSPF; every branch current read):

<!-- test: run requires=ngspice -->
```sh
emir simulate examples/03_ngspice/tb_rcc.cir --sim ngspice --dspf examples/03_ngspice/loadrail.dspf --mode rcc --out emir_out/ng/rcc.raw --format json
emir run examples/03_ngspice/loadrail.dspf --process emir/data/sample/process.yaml --cc-sim emir_out/ng/rcc.raw --mode rcc --period 1e-8 --summary-out emir_out/ng/summary.json --format json
```

<!-- test: expect -->
```json
{"schema": "emir.run/1", "verdict": "FAIL",
 "nets": [{"mode": "rcc", "governing_metric": "peak", "max_ratio": {"all": 1.0417}, "ir_drop_mV": 28.07,
           "dynamic_mV": 90.0}]}
```

<!-- test: run requires=ngspice -->
```sh
emir report --from emir_out/ng/summary.json
```

Both routes agree: FAIL on the far contact Rc3's peak, 1.0417x (`Cont: 1 -> 2 cuts`), the feed segment at
0.847x avg (WARN), IR 28.07 mV; rcc also gives the dynamic drop, 90.0 mV. Example 02's README works every number
out by hand.

### The self-consistent loop (when the person asks for it)

cc-grid's devices saw the undropped rail. `--self-consistent` simulates the CC testbench again with each instance
pin fed its own solved droop waveform (in series with the testbench's rail), until the droop the currents produce is
the droop they were fed (`--vdd`, the net's voltage in the testbench, is checked against its source: EMIR-E523).
EMIR writes the split copy of the testbench itself (a node and a 0 V probe per pin) and an include of the split's
sources it rewrites each pass (inline PWL); the person's testbench is never edited. Every device terminal on the net
must be a DSPF pin (EMIR-E540), and the testbench should set `reltol=1e-4` or below (EMIR-W540). Several rails close
in one loop with `--net VDD,VSS` (each rail read from the testbench, or given per net: `--vdd VDD=1.2,VSS=0`). An
R-only DSPF feeds back the R-only grid's droop (EMIR-W544: the devices' peaks can read low); the RC one feeds back
the RC grid's, and its branches are scored through the RC grid only with `--dynamic` (EMIR-W545 without it: offer
the person that run). It re-simulates, so it needs the person's go-ahead (over MCP:
`confirm`):

<!-- test: run requires=xyce -->
```sh
emir run examples/02_xyce/loadrail.dspf --process emir/data/sample/process.yaml --net VDD --mode cc-grid --self-consistent --sim xyce --netlist examples/02_xyce/tb_cc.cir --vdd 1.8 --period 1e-8 --droop-file emir_out/sc/droop.inc --summary-out emir_out/sc/summary.json --format json
```

<!-- test: expect -->
```json
{"schema": "emir.run/1", "verdict": "FAIL",
 "nets": [{"mode": "cc-grid", "self_consistent": true, "ir_drop_mV": 28.07,
           "self_consistency": {"simulator": "xyce", "converged": true, "n_passes": 3}}]}
```

Example 02's sinks are saturated MOSFETs with `lambda = 0`: their current hardly depends on the supply. The first
pass after the open loop still moves the droop by about 1 mV, because its simulation steps finer (every PWL point is
a breakpoint) and so resolves the sinks' turn-on, which the open loop sampled every 10 ps; the next pass reproduces it
(0.27 mV, under the 0.9 mV tolerance) and the loop stops. This run raises EMIR-W540 (example 02's testbench sets
no reltol, so Xyce's default 1e-3 applies) and EMIR-W545 (its DSPF has capacitance on the rail and the run has no
`--dynamic`): relay both. Loads whose current follows the supply move: `examples/12_self_consistent` shows both.
ngspice runs the same with `--sim ngspice`.

### On the person's files

```sh
emir next --dspf <block.dspf> --process <pdk.yaml> --netlist <tb.cir> --period <T> --format json
emir verify-sim --sim xyce --format json        # once per machine and Xyce version (the plan says when)
emir simulate <tb.cir> --sim xyce --dspf <block.dspf> --mode rcc --process <pdk.yaml> --out emir_out/sim/result.raw --format json
emir check <block.dspf> --process <pdk.yaml> --cc-sim emir_out/sim/result.raw --mode rcc --format json
emir run <block.dspf> --process <pdk.yaml> --cc-sim emir_out/sim/result.raw --mode rcc --period <T> --summary-out emir_out/summary.json --format json
emir report --from emir_out/summary.json
```

`emir next` without `--mode` first: the mode is the person's choice (here they chose rcc: the testbench includes
the RC DSPF). Run the plan's steps in order. `--mode cc-grid` (with `--probe` when the devices are subcircuits, as
open PDKs ship their transistors) for a CC netlist. A result the person simulated by hand needs its names sidecar:

```sh
emir save-lines <block.dspf> --mode cc-grid --sim ngspice --names-out <result.raw>.names --format json
```

To see the results on the layout: add `--viewer emir_out/viewer` to the run, then the person opens
`klayout <layout.gds> -m emir_out/viewer/emir.lyrdb` (a GUI: give them the command, do not run it).

## Decision points

| you see | do |
|---|---|
| `choose-mode` first, `mode.chosen` null | show the person the routes (`choices`): a testbench holding the DSPF's resistors can give rcc (exact) or cc-grid; a CC netlist only cc-grid; ask, then plan with their `--mode` |
| `mode.chosen` rcc or cc-grid | the plan as printed; `--probe` is planned for cc-grid when the devices are subcircuits |
| EMIR-W520 on a cc-grid run (droop above about 2 % of `--vdd`) | offer the self-consistent loop (above; it re-simulates: the person decides) |
| `missing`: "a xyce testbench of the block", simulate step `waiting` | ask the person for a testbench, then `emir next ... --netlist <tb.cir>`; never write the stimulus yourself |
| the simulate step has `needs_human` (`long_run`: a net above 500 nodes) | ask before simulating; for a big block prefer a CC testbench and cc-grid (playbook 02) |
| a result with SPICE names and no sidecar (`names-sidecar` step) | run the `emir save-lines ... --names-out` step it plans, then `emir next` again |
| EMIR-W260, EMIR-W261, EMIR-W262 probes and DSPF disagree on names | compare `emir pins <block.dspf> --net <net>` with the netlist; `--pins SUBCKT=d,g,s,b` for PDK subcircuits |
| EMIR-W461 currents missing from the result | check `--prefix` (the instance path); missing currents count as zero (EMIR-W403) |
| EMIR-W660 the testbench has a `.control` section | ngspice runs it too (what it sets applies; a `shell` line runs a program): confirm the person trusts the deck; over MCP the call needs their go-ahead |
| EMIR-W463 from `emir probe` (`has_tran` false) | a block, not a testbench: no simulate step follows; probe the testbench that includes it instead |
| EMIR-W413 the window is not settled | give `--period` |
| a `verify-sim` step in the plan | run it first (open simulators: no licence, seconds); a FAIL (exit 70, EMIR-E663) stops the route: report the failing checks |
| EMIR-W464 on a run (`simulator_verification` `not verified`) | the simulator's route was not verified here (`reason`: no record, stale, other host): run the code's command (`emir verify-sim --sim xyce`, or `--sim ngspice`), then the run again; say so in the report until then |
| EMIR-W414 on a run (`reason` `failed`) | the latest verification of that simulator version here FAILED: do not rely on the numbers; tell the person and report the failing checks of the record |
| EMIR-W416 on a run (`reason` `version unknown`) | the result does not say which version wrote it (a deck run by hand, with `save-lines`' sidecar): simulate through `emir simulate` (it records the version), or report the run as not verified |
| EMIR-E460 naming ngspice and a MOSFET's pin | ngspice's `@m[id]` carries the model's polarity: simulate with `--probe` (the code's next step) |

## Success criteria

- `emir simulate` exited 0 with `missing: []` and `found == signals`; `names_sidecar` is `result + ".names"`.
- `emir check` exited 0; `emir run` exited 0 (a FAIL verdict is a result).
- `emir report --from emir_out/summary.json` ran before you answered, and you relayed it.
- At most one simulation ran at a time (or the person's limit).
- `emir verify-sim --sim xyce` (or ngspice) passed on this machine, and the run's `simulator_verification` says
  `verified`.

## What to report

The verdict, the governing metric and the worst item with its fix, the IR drop (and rcc's dynamic drop), that the
currents came from Xyce or ngspice (which, which mode, which period), whether that simulator is verified on this
machine (`simulator_verification`; the verification's version and date), the temperature, what was not checked,
"Screening, not sign-off.", and that EMIR is a research project with no guarantee of accuracy, whose results the
person tests and verifies before relying on them.

## Failure modes

| code | exit | meaning | do |
|---|---|---|---|
| EMIR-E660 | 3 | Xyce or ngspice not found | tell the person (`$XYCE_BIN`, `$NGSPICE_BIN`, the site file); do not install software without asking |
| EMIR-E460 | 2 | a subcircuit pin's current cannot be printed | re-run with `--probe` (the error's `next_steps`) |
| EMIR-E462 | 2 | the testbench has no `.tran` | ask the person to add one over whole periods |
| EMIR-E263 | 2 | wrong testbench, mode or instance path | rcc needs the DSPF in the testbench; check `--prefix` |
| EMIR-E261 | 2 | the block is instantiated more than once | ask which instance; `--prefix XI0.` |
| EMIR-E602 | 2 | the simulator failed | read the quoted lines and the log (`next_steps`); fix the testbench with the person |
| EMIR-E605 | 2 | the simulation timed out | ask before raising `--timeout` |
| EMIR-E661 | 2 | no result was written | the testbench ran no transient, or the simulator stopped early: read its log |
| EMIR-E424, EMIR-E461 | 2 | a malformed names sidecar or probe file | regenerate it (`emir simulate`, `emir probe --names-out`) |
| EMIR-E663 | 70 | `emir verify-sim` failed on this machine | do not rely on this simulator's results here; report the failing checks and the version |
| EMIR-E520 | 2 | no `--mode` | ask the person; `emir next` lists the routes |
| EMIR-E522 | 2 | the self-consistent split found no instance pin of the DSPF | the testbench's instance names differ from the DSPF's, or `--save-prefix` is wrong |
