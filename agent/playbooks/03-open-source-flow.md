# 03 The open-source flow: Xyce or ngspice, and magic on SKY130

No licence anywhere: Xyce or ngspice simulates the block, `emir simulate` adds the lines that save the currents
and maps the simulator's names onto the DSPF's, and the run is the same as with Spectre. With a GDS on SKY130 and
no DSPF, magic extracts one (`emir extract --tool magic`). Before the first run that uses a simulator on this
machine, `emir verify-sim` proves its route here. **Screening, not sign-off.**

## When to use

- `emir info` shows `xyce` or `ngspice` found, and the person has no Spectre or does not want it used.
- The person has a GDS on SKY130 and no DSPF (`pdks/sky130.yaml` has the `magic:` section).
- With Spectre or HSPICE, playbook 04.

## Inputs

| input | what it must be | ask / check |
|---|---|---|
| DSPF | RC with its devices (the RC netlist rcc simulates); or from magic (below) | `emir nets <block.dspf> --format json` |
| a SPICE testbench | supplies, stimulus, models, a `.tran` over whole periods; it includes the DSPF | ask the person: EMIR never writes a stimulus. A testbench is code (an ngspice `.control` block can run programs): simulate only decks the person trusts |
| the period | `--period` | ask the person |
| for magic: the GDS, the cell, a magic with the `geoout` patch | `--cell`; `--magic`, `$EMIR_MAGIC`, or `magic-8.3.515-geo` / `magic` on `$PATH` | `emir extract` probes each (EMIR-E643 lists what it tried) |

## Commands

### Worked example: Xyce (runs as printed from the repository root)

Example 02's invented block: four NMOS sinks pulsing on a 60 um rail, a testbench that includes the RC DSPF, the
ILLUSTRATIVE process file.

<!-- test: run requires=xyce -->
```sh
emir next --dspf examples/02_xyce/loadrail.dspf --process emir/data/sample/process.yaml --netlist examples/02_xyce/tb_rcc.cir --period 1e-8 --format json
```

<!-- test: expect -->
```json
{"schema": "emir.next/1", "mode": {"chosen": "rcc"},
 "steps": [{"id": "selftest"}, {"id": "nets"},
           {"id": "verify-sim", "command": "emir verify-sim --sim xyce --format json", "needs_human": false},
           {"id": "simulate", "status": "todo"}, {"id": "check"}, {"id": "run"}, {"id": "report"}]}
```

<!-- test: run requires=xyce -->
```sh
emir verify-sim --sim xyce --out emir_out/verify-sim/xyce --format json
emir simulate examples/02_xyce/tb_rcc.cir --sim xyce --dspf examples/02_xyce/loadrail.dspf --process emir/data/sample/process.yaml --out emir_out/sim/result.raw --format json
emir run examples/02_xyce/loadrail.dspf --process emir/data/sample/process.yaml --cc-sim emir_out/sim/result.raw --period 1e-8 --summary-out emir_out/summary.json --format json
```

<!-- test: expect -->
```json
{"schema": "emir.run/1", "verdict": "FAIL", "worst_net": {"net": "VDD", "ratio": 1.0417},
 "nets": [{"mode": "rcc", "governing_metric": "peak", "ir_drop_mV": 28.07, "dynamic_mV": 90.0,
           "metrics": ["avg", "rms", "peak"]}]}
```

The same block with ngspice (every branch current read):

<!-- test: run requires=ngspice -->
```sh
emir simulate examples/03_ngspice/tb_rcc.cir --sim ngspice --dspf examples/03_ngspice/loadrail.dspf --out emir_out/ng/rcc.raw --format json
emir run examples/03_ngspice/loadrail.dspf --process emir/data/sample/process.yaml --cc-sim emir_out/ng/rcc.raw --period 1e-8 --summary-out emir_out/ng/summary.json --format json
```

<!-- test: expect -->
```json
{"schema": "emir.run/1", "verdict": "FAIL",
 "nets": [{"mode": "rcc", "governing_metric": "peak", "max_ratio": {"all": 1.0417}, "ir_drop_mV": 28.07,
           "dynamic_mV": 90.0}]}
```

Both find the far contact FAIL on its peak, 1.0417x, the feed segment at 0.847x avg, IR 28.07 mV and the dynamic
drop, 90.0 mV.

### Worked example: a GDS on SKY130 (runs as printed)

With a magic built with the `geoout` patch; without one, `missing` names the build instead (EMIR-E643).

<!-- test: run requires=magic_geo -->
```sh
emir next --gds tests/data/magic_tiny/tiny.gds --process pdks/sky130.yaml --cell tiny --format json
```

<!-- test: expect -->
```json
{"schema": "emir.next/1",
 "steps": [{"id": "selftest"}, {"id": "extract-magic"}, {"id": "next-dspf", "after": ["extract-magic"]}]}
```

### On the person's files

```sh
emir next --dspf <block.dspf> --process <pdk.yaml> --netlist <tb.cir> --period <T> --format json
emir verify-sim --sim xyce --format json
emir simulate <tb.cir> --sim xyce --dspf <block.dspf> --process <pdk.yaml> --out emir_out/sim/result.raw --format json
emir run <block.dspf> --process <pdk.yaml> --cc-sim emir_out/sim/result.raw --period <T> --summary-out emir_out/summary.json --viewer emir_out/viewer --format json
emir report --from emir_out/summary.json
emir extract --tool magic --gds <layout.gds> --cell <cell> --process pdks/sky130.yaml --out emir_out/extract_<cell> --format json
```

After `emir extract`, `<cell>.dspf` is the network and the RC netlist the testbench includes; plan again with
`emir next --dspf emir_out/extract_<cell>/<cell>.dspf --process pdks/sky130.yaml --gds <layout.gds> --cell <cell>`.
A result simulated by hand needs its names sidecar:
`emir save-lines <block.dspf> --sim ngspice --names-out <result.raw>.names`. To see the results,
the person opens `klayout <layout.gds> -m emir_out/viewer/emir.lyrdb` (a GUI: give them the command).

## Decision points

| you see | do |
|---|---|
| `missing`: the RC netlist in the testbench | the testbench must include the DSPF (every resistor simulated): ask the person |
| a `verify-sim` step | run it first (open simulators: seconds); a FAIL (EMIR-E663) stops the route |
| EMIR-W464, EMIR-W414, EMIR-W416 on a run | not verified here, failed, or version unknown: say so in the report |
| `missing`: a testbench | ask the person for one; never write the stimulus yourself |
| EMIR-W660 (a `.control` section) | confirm the person trusts the deck |
| `emir info`'s `magic_route` (or `emir next --gds`'s `facts.magic_route`) | the magic the route runs, and whether it writes resistor geometry: state that, never guess it |
| EMIR-E643 (no magic writes geometry) | tell the person: the patch is branch `extresist-geoout` of https://github.com/codervijay/magic (upstream PR RTimothyEdwards/magic#562); ask before building anything |
| `unchecked_layers` after `emir extract` | diffusion, poly, wells, li1 on sky130: report them as not checked |
| EMIR-W643 (terminals named by position) | the process file names no terminals for those models: tell the person |

## Success criteria

- `emir simulate` exited 0 with `missing: []`; `emir run` exited 0; `emir report` ran and was relayed.
- The simulator passed `emir verify-sim` here, or the report says it did not; at most one simulation ran at a time.

## What to report

The report (playbook 01), with which simulator gave the currents and the period, whether that simulator
is verified on this machine, for a magic extraction that the network is magic's (every contact and the diffusion
included) and which layers were not checked, and "Screening, not sign-off."

## Failure modes

| code | exit | meaning | do |
|---|---|---|---|
| EMIR-E660 | 3 | Xyce or ngspice not found | tell the person (`$XYCE_BIN`, `$NGSPICE_BIN`) |
| EMIR-E462 | 2 | the testbench has no `.tran` | ask the person to add one over whole periods |
| EMIR-E261 | 2 | the block is instantiated more than once | ask which; `--prefix XI0.` |
| EMIR-E602, EMIR-E661 | 2 | the simulator failed, or wrote no result | its log (`next_steps`) |
| EMIR-E641 | 2 | no magic technology | `PDK_ROOT`, or `--magicrc` |
| EMIR-E640 | 2 | magic wrote no netlist | the log in `magic/` under `--out` |
| EMIR-E646 | 2 | `--out` holds files EMIR did not write | ask, or another `--out` |
