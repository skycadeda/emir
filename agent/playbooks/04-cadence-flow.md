# 04 The Cadence flow (and HSPICE): PVS + Quantus, Spectre, Virtuoso

At a Cadence site the DSPF comes from PVS LVS and Quantus (`emir extract`), the currents from Spectre (the save
lines `emir save-lines` writes, or the deck `emir simulate --sim spectre` writes), and the results go back onto the
layout in Virtuoso (the EMIR Explorer). EMIR itself needs no licence. Nothing in the repository says a Cadence tool,
or HSPICE, was verified anywhere: each is verified on site, on this machine, by you, with the person's go-ahead
for every licensed run. **Screening, not sign-off.**

## When to use

- `emir info` lists `spectre`, `virtuoso`, `pvs` or `quantus` as found, or the person works in Virtuoso, Quantus
  or ADE.
- HSPICE: `emir info` lists `hspice`, or the person simulates with HSPICE.
- Otherwise playbook 03 (Xyce, ngspice) or 01 (a result the person already has).

## Inputs

| input | what it must be | ask / check |
|---|---|---|
| for an extraction: GDS and CDL of the cell | stream out of Virtuoso | ask the person |
| the PVS rule deck, Quantus technology directory, corner, extraction temperature | the site file, `$PVS_RULES`, `$QRC_TECH_DIR`, `$QRC_CORNER`, `$EMIR_EXTRACT_TEMP`, or the options | ask: never guess a deck, a corner or a temperature |
| the testbench's instance of the block | for `--prefix` (with its dot: `XDUT.`) | ask the person |
| the process file | the PDK's limits (playbook 02) | `emir check` |

## Commands

### Worked example (runs as printed from the repository root; nothing licensed runs)

The extraction plan, with stand-in input files (the dry run only checks they exist):

<!-- test: run -->
```sh
mkdir -p emir_out/extract
printf 'stand-in for the layout\n' > emir_out/extract/railblock.gds
printf '.SUBCKT railblock VDD\n.ENDS\n' > emir_out/extract/railblock.cdl
emir extract --gds emir_out/extract/railblock.gds --cdl emir_out/extract/railblock.cdl --cell railblock --kind rc_coupled --work emir_out/extract/work --format json
```

<!-- test: expect -->
```json
{"schema": "emir.extract/1", "dry_run": true, "kind": "rc_coupled",
 "steps": [{"id": "lvs", "licensed_tool": "pvs"}, {"id": "lvs-gate"}, {"id": "quantus", "licensed_tool": "quantus"}]}
```

`ready` is false and `missing[]` names each setting and tool with `how` to set it. The plan for the sample's rail
at a site with Spectre (rcc: every branch current), with the block's period from the person (without it the plan
asks for it, and plans no check or run: they would be refused, EMIR-E428):

<!-- test: run -->
```sh
emir next --dspf emir/data/sample/rail.dspf --process emir/data/sample/process.yaml --have-cadence --period 1e-8 --format json
```

<!-- test: expect -->
```json
{"schema": "emir.next/1", "mode": {"chosen": "rcc"},
 "steps": [{"id": "selftest"}, {"id": "nets"},
           {"id": "verify-sim", "command": "emir verify-sim --sim spectre --run --format json",
            "licensed_tool": "spectre", "needs_human": true},
           {"id": "save-lines"},
           {"id": "simulate", "licensed_tool": "spectre", "needs_human": true, "status": "waiting"},
           {"id": "check"}, {"id": "run"}, {"id": "report"}]}
```

The first Spectre step is its on-site verification: it runs Spectre, so ask first. Without `--run` it only writes
the decks and prints the commands. The save lines and the Virtuoso SKILL files the person loads in their own
session:

<!-- test: run -->
```sh
emir verify-sim --sim spectre --out emir_out/verify-sim/spectre --format json
emir save-lines emir/data/sample/rail.dspf --prefix XDUT. --out emir_out/tb/emir_save.scs --format json
emir gui-install --dir emir_out/skill --format json
```

<!-- test: expect -->
```json
{"schema": "emir.gui_install/1"}
```

### On the person's files

```sh
emir verify-sim --sim spectre --run --format json      # once per machine and Spectre version; ask first
emir extract --gds <layout.gds> --cdl <netlist.cdl> --cell <cell> --kind rc_coupled --format json
emir extract --gds <layout.gds> --cdl <netlist.cdl> --cell <cell> --kind rc_coupled --run --format json
emir save-lines <cell.rc_coupled.dspf> --prefix <XDUT.> --out emir_save.scs --format json
emir run <cell.rc_coupled.dspf> --process <pdk.yaml> --cc-sim <tb.raw> --period <T> --summary-out emir_out/summary.json --viewer emir_out/viewer --lib <LIB> --format json
emir report --from emir_out/summary.json
```

The `--run` extraction starts PVS and Quantus: only after the person agreed, and only when the dry run says
`ready: true`. The RC extraction (`--kind rc_coupled`) is the netlist the testbench simulates and the DSPF EMIR
screens; `--kind r_only` gives a grid for DC currents. The simulation is the person's, or `emir simulate
<tb.scs> --sim spectre --dspf <dspf> --prefix <XDUT.>` with `--run` after their go-ahead; results in `-format
psfascii`. APS or Spectre X: their options in `--sim-args`, to `emir verify-sim` too. Templates:
`agent/cadence/spectre/`. On a big block the RC transient is a long run: ask first. UNVERIFIED until checked where
Spectre runs.

**HSPICE** is licensed like Spectre: `emir verify-sim --sim hspice` (the plan; `--run` after the person agrees),
then `emir simulate <tb.sp> --sim hspice --dspf <block.dspf> --out emir_out/sim/result.tr0` (add `--run`
with the go-ahead) and `emir run` on the `.tr0`. A result made by hand needs `emir save-lines <dspf>
--sim hspice --names-out <result.tr0>.names`.

To see the results, the person (never you) uses **EMIR > Load results** from the menu `emir gui-install`
installs; the Explorer opens the layout read-only and only highlights.

## Decision points

| you see | do |
|---|---|
| `emir extract` dry run, `ready: false` | show `missing[]` with `how`; ask for each value |
| `ready: true` | show the plan; add `--run` only after the person agrees |
| EMIR-E280 (LVS did not match) | stop and tell the person; never extract anyway |
| a step with `licensed_tool`, or a `verify-sim` step for Spectre or HSPICE | ask the person first, every time |
| EMIR-W464, EMIR-W414, EMIR-W416 on a run | not verified here, failed, or version unknown: say so |
| EMIR-E408 (binary PSF) | the `psf` conversion its `next_steps` gives (a shell command for the person), or `-format psfascii` |
| the person relies on Virtuoso | say the menu, the form and the Explorer are UNVERIFIED until the headless self-test and the live test pass on their Virtuoso (`agent/cadence/README.md`) |

## Success criteria

- No licensed tool ran without the person's go-ahead; their Virtuoso was not touched.
- `emir extract --run` exited 0 with `lvs.matched` true (or the dry-run plan was handed over); `emir run` printed a
  verdict; `emir report` was relayed.
- Spectre (or HSPICE) was verified here before the first run that used it, or the report says it was not.

## What to report

The report (playbook 01), plus the extraction kind and corner, the Spectre analysis and period, and the run's
`simulator_verification` (not verified: say so, and that `emir verify-sim --sim spectre --run` is the check).
"Screening, not sign-off." And the research-project disclaimer, once.

## Failure modes

| code | exit | meaning | do |
|---|---|---|---|
| EMIR-I680 | 0 | a dry run: nothing ran | show the plan |
| EMIR-E580 | 2 | a setting is missing on `--run` | `details` says which; ask the person |
| EMIR-E680 | 3 | pvs or qrc not found | ask where the site installs them |
| EMIR-E281 | 2 | Quantus wrote no DSPF | its log (`next_steps`) |
| EMIR-E662 | 3 | Spectre or HSPICE not found for `--run` | `$SPECTRE_BIN`, `$HSPICE_BIN`, the site file |
| EMIR-E663 | 70 | the on-site verification failed | do not rely on that simulator here |
| EMIR-E463, EMIR-E465 | 2 | a binary HSPICE result; several sweep tables | `.option post=2 post_version=2001`; `--analysis sweep:N` |
