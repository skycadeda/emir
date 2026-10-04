# 01 Screen a block: a DSPF, a process file and currents

The everyday task: EM (avg / rms / peak per segment and per via) and IR drop of one extracted block, from its DSPF,
the PDK's process file and the block's currents. **Screening, not sign-off.**

## When to use

- The person has a DSPF of the block (from Quantus, Calibre xRC, StarRC or magic) and wants EM, IR, or both.
- The currents are DC values the person gives (a budget, a datasheet number) or a finished simulation result.
- Not for: a block above about 50 000 DSPF nodes or an RCC simulation that does not converge (playbook 02); a chip
  made of blocks (playbook 03); a layout with no DSPF (playbook 09); no simulation result yet with Xyce or ngspice
  (playbook 05) or Spectre (playbook 06), which then come back here.

## Inputs

| input | what it must be | check it with |
|---|---|---|
| DSPF | text DSPF: resistors with layer, `$W`/`$L` on wires, cut counts (`$N`, `$VC` or `$A`) on vias, node coordinates, `*\|I` instance pins. R-only for cc-grid; RC with devices for rcc | `emir nets <block.dspf> --format json` |
| process file | the PDK's EM limits: `pdks/<pdk>.yaml`, or the person's file. Never write limits yourself (playbook 04) | `emir check` validates it |
| currents, DC | `--currents '{"VDD": 0.004, "M0:s": -0.004}'`: amps INTO the net, the feed positive, each load negative, summing to zero; keys are the net's port and `inst:pin` names (`emir pins`); one net; inline as below, or the path of a file holding the same JSON (`--currents currents.json`) | `emir schema currents` |
| currents, simulated | `--cc-sim <result>`: PSF ASCII or nutascii (Spectre), HSPICE ASCII `.tr0` or ASCII raw plus its `<result>.names` sidecar (HSPICE, Xyce, ngspice); every branch current saved (rcc) or every instance-pin current (cc-grid); the simulator verified on this machine (`emir verify-sim`) | `emir next ... --cc-sim <result>` |
| the mode (simulated currents only) | `--mode rcc` or `--mode cc-grid`: the person's choice, never yours. `emir next` without it lists the routes (its `choose-mode` step: cost, measured accuracy, which can work with this result, the recommendation); ask and pass their answer. DC currents need none | ask the person |
| the period | whole periods of the block's activity, in s, for a periodic simulation (`--period`) | ask the person |
| the temperature | the process file's `em_tref` by default; `--em-temp` only when the person names one | ask the person |

## Commands

### Worked example (runs as printed from the repository root)

The bundled sample `railblock`: a 60 um Metal1 rail fed at its VDD pin, four PMOS sources drawing 1 mA each, an
ILLUSTRATIVE process file (invented limits).

<!-- test: run -->
```sh
emir selftest --format json
emir next --dspf emir/data/sample/rail.dspf --process emir/data/sample/process.yaml --currents '{"VDD": 0.004, "MP0:s": -0.001, "MP1:s": -0.001, "MP2:s": -0.001, "MP3:s": -0.001}' --format json
```

<!-- test: expect -->
```json
{"schema": "emir.next/1", "mode": {"chosen": "dc"}, "missing": [],
 "steps": [{"id": "selftest"}, {"id": "nets"}, {"id": "check"}, {"id": "run"}, {"id": "report"}]}
```

The plan's steps, run in order:

<!-- test: run -->
```sh
emir nets emir/data/sample/rail.dspf --format json
emir check emir/data/sample/rail.dspf --process emir/data/sample/process.yaml --currents '{"VDD": 0.004, "MP0:s": -0.001, "MP1:s": -0.001, "MP2:s": -0.001, "MP3:s": -0.001}' --format json
```

<!-- test: expect -->
```json
{"schema": "emir.check/1", "passed": true}
```

`findings[]` holds EMIR-W306 (the illustrative process file); DC currents need no mode. Then the run:

<!-- test: run -->
```sh
emir run emir/data/sample/rail.dspf --process emir/data/sample/process.yaml --currents '{"VDD": 0.004, "MP0:s": -0.001, "MP1:s": -0.001, "MP2:s": -0.001, "MP3:s": -0.001}' --summary-out emir_out/summary.json --html emir_out/html --format json
```

<!-- test: expect -->
```json
{"schema": "emir.run/1", "disclaimer": "Screening, not sign-off.", "verdict": "FAIL",
 "worst_net": {"net": "VDD", "ratio": 1.667},
 "nets": [{"net": "VDD", "mode": "dc", "verdict": "FAIL", "governing_metric": "avg",
           "max_ratio": {"all": 1.667, "wire": 1.667, "via": 1.333}, "ir_drop_mV": 54.0,
           "n_violations": 10,
           "worst_items": [{"name": "Rm1", "layer": "Metal1", "kind": "wire", "metric": "avg",
                            "ratio": 1.667, "fix": "Metal1: widen 0.750 -> 1.255 um (x1.67)"}]}]}
```

<!-- test: run -->
```sh
emir report --from emir_out/summary.json
```

The report's first lines: `**Verdict: FAIL** -- 1 net(s): 1 FAIL, 0 WARN, 0 CLEAN; worst 1.667x on VDD.`, then
`**Screening, not sign-off.**`, the run line, the mode (dc), the temperature (100 C), the nets table, the worst
items (Rm1-Rm3 at 1.667x: widen to 1.255 um; the contact Rc3 at 1.333x: `Cont: 1 -> 2 cuts (+1)`), the IR drop
(54.000 mV from the feed) and what was not checked (DC currents: switching transients and their peaks).

### On the person's files

With a simulation result (the usual case): `emir next` first, without `--mode`. Its `choose-mode` step shows the
routes; the result decides what can work (every branch current saved: rcc; only the pin currents: cc-grid). Show
the person, ask, then plan again with their mode:

```sh
emir next --dspf <block.dspf> --process <pdk.yaml> --cc-sim <result.raw> --format json
emir next --dspf <block.dspf> --process <pdk.yaml> --cc-sim <result.raw> --mode cc-grid --format json
emir check <block.dspf> --process <pdk.yaml> --cc-sim <result.raw> --mode cc-grid --period <T> --format json
emir run <block.dspf> --process <pdk.yaml> --cc-sim <result.raw> --mode cc-grid --period <T> --summary-out emir_out/summary.json --html emir_out/html --format json
emir report --from emir_out/summary.json
```

Here the person chose cc-grid; with `rcc` the commands are the same with `--mode rcc`. With cc-grid, add `--vdd <V>`
(the rail's supply) to the run:
a droop above about 2 % of it is EMIR-W520 and the run's `next_steps` plan the self-consistent route (playbook 02).

With DC currents on one net, the worked example's commands with `--net <net>` and the person's `--currents`.
With no result yet, `emir next` plans the save lines and the simulation; follow playbook 05 (Xyce, ngspice) or 06
(Spectre), then come back with `--cc-sim`. To see the results on the layout add `--viewer emir_out/viewer`
(KLayout marker database, Virtuoso Explorer data); `--fail-on-violation` makes a FAIL exit 1 (CI).

## Decision points

| you see | do |
|---|---|
| `check` exits 2, `passed: false` | run each FAIL finding's `command`, or fix what its `code` says; never `--force` unless the person decides to |
| EMIR-E204 net not in the DSPF | `emir nets` lists the nets; use one of them |
| EMIR-E401, EMIR-E406 | `--currents` is not a JSON object of numbers, or does not sum to zero: fix it with the person |
| EMIR-W403 some instance pins not saved | they count as zero current: add their save lines (`emir save-lines`) and re-simulate, or report it |
| EMIR-E410 the result cannot be read | binary PSF or raw: re-simulate with ASCII output (`-format psfascii`, `Xyce -a`, `set filetype=ascii`) |
| EMIR-E520 the mode is not chosen | ask the person: `emir next` without `--mode` lists rcc, cc-grid and cc-grid self-consistent with their cost and accuracy; never pick one |
| EMIR-E415 / EMIR-E416 the result lacks what the mode needs | every branch current (rcc) or the pin currents (cc-grid) are missing: `emir next ... --cc-sim <result> --mode <mode>` plans the save lines; if the result serves the other mode, tell the person (their choice) |
| EMIR-W520 the droop of a cc-grid run is above about 2 % of `--vdd` | report it as open loop; offer the self-consistent run (playbook 02): it re-simulates, the person decides |
| EMIR-E417 an instance pin at several connections | a black-boxed block: playbook 03; a small net: rcc mode |
| EMIR-W413 averaging window not settled | give `--period` (whole periods of the activity) or simulate longer |
| EMIR-W306 illustrative process file | the limits are invented: say so in every result, or get the PDK's file (playbook 04) |
| `not_checked.limits_missing` or `unmapped_layers` non-empty | those layers or metrics are not checked: report them; never fill a limit in |
| `facts.size.class` is `big` in `emir next` | playbook 02 |
| a ratio between the warn fraction (0.8 by default) and 1.0 | WARN: below the limit, inside the margin; report it, it is not a FAIL |
| a FAIL verdict | a result, not an error (exit 0); relay the `fix` of each worst item; re-run the same command after the layout is fixed |

## Success criteria

- `emir check` exited 0 (`passed: true`), or every FAIL was resolved.
- `emir run` exited 0 (or 1 with `--fail-on-violation` and a FAIL verdict) and wrote `emir_out/summary.json`
  (`schema` `emir.run/1`), one `nets[]` entry per screened net.
- `emir report --from emir_out/summary.json` ran before you answered, and printed the report you relayed.

## What to report

From `emir report` (playbook 07): the verdict; per net the governing metric and the worst ratio with the item and
its fix; the IR drop per power net; the mode and where the currents came from; the analysis temperature; every
`not_checked` entry; "Screening, not sign-off." For the worked example: FAIL, VDD avg 1.667x on Metal1 Rm1-Rm3
(widen 0.750 -> 1.255 um), the contact Rc3 1.333x (1 -> 2 cuts), IR 54.0 mV, DC currents at 100 C, and the
limits are the sample's invented ones.

## Failure modes

| code | exit | meaning | do |
|---|---|---|---|
| EMIR-E101 | 2 | a file does not exist | check the path; relative paths are from the working directory |
| EMIR-E217 | 2 | not a DSPF | ask for the DSPF itself (text, `*\|DSPF` header) |
| EMIR-E204 | 2 | net not in the DSPF | `emir nets` |
| EMIR-E406 | 2 | DC currents do not balance | the feed equals the sum of the loads |
| EMIR-E417 | 2 | a pin reaches the net at several connections | playbook 03 or rcc |
| EMIR-E418 | 2 | the analysis does not fit in memory | one `--net` at a time; playbook 02 |
| EMIR-E506 | 2 | `--mode rcc` without a simulation | rcc reads branch currents: give `--cc-sim`, or drop `--mode` |
| EMIR-E520 | 2 | `--cc-sim` without `--mode` | ask the person; `emir next` lists the routes |
| EMIR-E507 | 2 | usage error | `emir run --help`; options are never abbreviated |
| EMIR-W703 | 0 | `--viewer` wrote no KLayout marker database (the klayout package is missing) | `emir info`; install the klayout package with the person's go-ahead (`pip install -e '<clone>[klayout]'`, or `pip install klayout`), or use `--html` |
| any other | | | `emir explain <code>`; playbook 08 |
