# 01 Screen a block: DSPF, process file, currents; the report

The everyday task: EM (avg / rms / peak per segment and via) and IR drop of one extracted block, from its DSPF,
the PDK's process file and the block's currents, then the report the person reads. **Screening, not sign-off.**

## When to use

- The person has a DSPF (from Quantus, Calibre, StarRC or magic) and DC currents or a finished simulation result.
- The block is big (`facts.size.class` `big`: above 50 000 nodes): it is screened the same way, but its RC
  simulation is a long run, planned for the person's go-ahead (one `--net` at a time keeps the result smaller).
- A finished run, or a summary file, to explain to a person.
- Not for: no process file (playbook 02); no result yet (playbook 03 for Xyce, ngspice or a GDS on SKY130; playbook
  04 for Spectre or HSPICE), which then come back here.

## Inputs

| input | what it must be | check it with |
|---|---|---|
| DSPF | text DSPF: `$W`/`$L` on wires, cut counts on vias, coordinates, `*\|I` instance pins; RC for a simulated run | `emir nets <block.dspf> --format json` |
| process file | `pdks/<pdk>.yaml` or the person's; never write limits yourself (playbook 02) | `emir check` |
| DC currents | `--currents '{"VDD": 0.004, "M0:s": -0.004}'`: amps INTO the net, summing to zero, keys from `emir pins` (or a file's path) | `emir schema currents` |
| a simulation result | `--cc-sim <result>`, ASCII; every branch current of the RC netlist saved (rcc) | `emir next ... --cc-sim <result>` |
| the period | `--period`, the period of the activity: a transient and a `--netlist` run need it (EMIR-E428); a record held at DC: any period that fits twice | ask the person |

## Commands

### Worked example: DC currents (runs as printed from the repository root)

The bundled sample: a 60 um Metal1 rail, four 1 mA loads, an ILLUSTRATIVE process file (invented limits).

<!-- test: run -->
```sh
emir next --dspf emir/data/sample/rail.dspf --process emir/data/sample/process.yaml --currents '{"VDD": 0.004, "MP0:s": -0.001, "MP1:s": -0.001, "MP2:s": -0.001, "MP3:s": -0.001}' --format json
```

<!-- test: expect -->
```json
{"schema": "emir.next/1", "mode": {"chosen": "dc"}, "missing": [],
 "steps": [{"id": "selftest"}, {"id": "nets"}, {"id": "check"}, {"id": "run"}, {"id": "report"}]}
```

<!-- test: run -->
```sh
emir check emir/data/sample/rail.dspf --process emir/data/sample/process.yaml --currents '{"VDD": 0.004, "MP0:s": -0.001, "MP1:s": -0.001, "MP2:s": -0.001, "MP3:s": -0.001}' --format json
emir run emir/data/sample/rail.dspf --process emir/data/sample/process.yaml --currents '{"VDD": 0.004, "MP0:s": -0.001, "MP1:s": -0.001, "MP2:s": -0.001, "MP3:s": -0.001}' --summary-out emir_out/summary.json --html emir_out/html --format json
```

<!-- test: expect -->
```json
{"schema": "emir.run/1", "disclaimer": "Screening, not sign-off.", "verdict": "FAIL",
 "nets": [{"net": "VDD", "mode": "dc", "governing_metric": "avg", "ir_drop_mV": 54.0,
           "worst_items": [{"name": "Rm1", "ratio": 1.667, "fix": "Metal1: widen 0.750 -> 1.255 um (x1.67)"}]}]}
```

<!-- test: run -->
```sh
emir report --from emir_out/summary.json --top 3 --format json
```

<!-- test: expect -->
```json
{"schema": "emir.report/1", "verdict": "FAIL", "modes": ["dc"], "temp_C": 100.0,
 "ir_by_power_net": [{"net": "VDD", "ir_drop_mV": 54.0}]}
```

### Worked example: a supply mesh with rcc (runs as printed)

Example 06's synthetic mesh (10 x 10 nodes, 32 inverters, invented limits): its RC DSPF, and the Xyce testbench
`rcc.cir` that simulates the RC mesh with its devices and prints every branch current.

<!-- test: run -->
```sh
python examples/06_supply_mesh/mesh.py xyce --n 10 --k 32 --out emir_out/mesh
emir next --dspf emir_out/mesh/grid_rc.dspf --process examples/06_supply_mesh/process_screen.yaml --format json
```

<!-- test: expect -->
```json
{"schema": "emir.next/1", "facts": {"size": {"class": "small", "total_nodes": 132}},
 "mode": {"chosen": "rcc"}}
```

The plan is rcc: every branch current of the RC netlist. The testbench is the person's; here the example's:

<!-- test: run requires=xyce -->
```sh
(cd emir_out/mesh && Xyce -a rcc.cir > xyce.log)
emir run emir_out/mesh/grid_rc.dspf --process examples/06_supply_mesh/process_screen.yaml --net VDD --cc-sim emir_out/mesh/rcc.raw --period 1e-9 --sim xyce --summary-out emir_out/mesh/summary.json --format json
```

<!-- test: expect -->
```json
{"schema": "emir.run/1", "verdict": "FAIL", "worst_net": {"net": "VDD", "ratio": 1.8278},
 "nets": [{"mode": "rcc", "governing_metric": "avg", "ir_drop_mV": 3.8203,
           "metrics": ["avg", "rms", "peak"]}]}
```

### On the person's files

```sh
emir next --dspf <block.dspf> --process <pdk.yaml> --cc-sim <result.raw> --period <T> --format json
emir check <block.dspf> --process <pdk.yaml> --cc-sim <result.raw> --period <T> --format json
emir run <block.dspf> --process <pdk.yaml> --cc-sim <result.raw> --period <T> --summary-out emir_out/summary.json --html emir_out/html --format json
emir report --from emir_out/summary.json
```

The simulation is the person's: the RC extraction in the testbench, whole periods of the activity, one simulation
per operating mode; never average across modes. rcc is exact for the simulated record over the whole periods you give
(--period); whether the testbench has settled is yours to establish, as for any .meas. The window is the record's last
whole periods, up to three.

## Decision points

| you see | do |
|---|---|
| `check` exits 2 | run each FAIL finding's `command`, or fix what its code says; never `--force` unless the person decides |
| EMIR-E415 (the result lacks the net's branch currents) | the RC netlist was not simulated with every branch current saved: `emir next` plans the save lines and the simulation; the person re-simulates |
| a long run planned (`long_run: true`) | ask the person first: an RC transient of a big block is expected to run long and may converge poorly |
| EMIR-E428: a transient (or a `--netlist` run) and no period | ask the person for the block's period; never guess it (a record held at DC: any period with two whole ones in it) |
| EMIR-E429: the last two periods differ | ask the person whether `--period` is the block's: if not, the right one; if it is, the block is still starting up and `emir next ... --period <T>` plans the longer simulation (the person's go-ahead first) |
| EMIR-E431: fewer than two periods in the record | `emir next ... --period <T>` plans the longer simulation (the person's go-ahead first) |
| EMIR-W306 | the limits are invented: say so with every number |
| `not_checked` with unmapped layers or missing limits | report them; never fill a limit in |
| a FAIL verdict | a result, not an error (exit 0): relay each worst item's `fix` |

## Success criteria

- `emir check` exited 0, or every FAIL was resolved; `emir run` exited 0 and wrote its summary.
- `emir report --from <summary>` ran before you answered, and its report is what you relayed.

## What to report

Relay `emir report` as it is, or build the message from `emir report --format json`, never from your own numbers:

```text
EMIR screening of <design>: <verdict> (<counts>). Screening, not sign-off.
- Worst: <net> <metric> <ratio>x on <layer> <name> at <x, y> -> fix: <fix>
- IR drop: <net> <ir_drop_mV> mV
- Currents: <current_source>; mode <modes>; analysed at <temp_C> C
- Simulator route: <simulator_verification.status> on this machine
- Limits: <where each layer's limits came from>
- Not checked: <not_checked, one line each>
EMIR is a research project, provided as is, with no guarantee of accuracy: test and verify these results before
relying on them.
```

A null number is "not a number", never 0. When the person can see images, attach the chart (`charts`: `--html`'s
`emir_chart.png`) beside the text. For the DC example: FAIL, VDD avg 1.667x on Metal1 Rm1 (widen 0.750 -> 1.255 um),
the contact Rc3 1.333x (1 -> 2 cuts), IR 54.0 mV, DC currents at 100 C, invented limits.

## Failure modes

| code | exit | meaning | do |
|---|---|---|---|
| EMIR-E217 | 2 | not a DSPF | ask for the text DSPF |
| EMIR-E406 | 2 | DC currents do not balance | the feed equals the sum of the loads |
| EMIR-E415 | 2 | the result lacks the branch currents rcc reads | `emir next ... --cc-sim <result>` plans the save lines |
| EMIR-E418 | 2 | the analysis does not fit in memory | one `--net` at a time |
| EMIR-E104 | 2 | `emir report` on a refused run's summary | report that the run did not finish, with its code |
| EMIR-W703 | 0 | no KLayout marker database (no klayout package) | `--html`, or the klayout extra with the person's go-ahead |
