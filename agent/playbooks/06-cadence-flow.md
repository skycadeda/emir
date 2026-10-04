# 06 The Cadence flow (and HSPICE): PVS + Quantus, Spectre, Virtuoso

At a Cadence site the DSPF comes from PVS LVS and Quantus (`emir extract`), the currents from Spectre (with the
save lines `emir save-lines` writes, or the deck `emir simulate --sim spectre` writes), and the results go back
onto the layout in Virtuoso (the EMIR Explorer). EMIR itself needs no licence. Nothing in the repository says a
Cadence tool, or HSPICE, was verified anywhere: each is verified on site, on this machine, by you (the local
agent), with the person's go-ahead for every licensed run. **Screening, not sign-off.**

## When to use

- `emir info --format json` lists `spectre`, `virtuoso` or `quantus` as found, or the person says the layout is
  in Virtuoso, the extraction comes from Quantus or the simulation from Spectre (ADE).
- HSPICE: `emir info` lists `hspice` as found, or the person simulates with HSPICE (the HSPICE section below).
- Otherwise use playbook 05 (Xyce, ngspice) or 01 (a result the person already has).

## Inputs

| input | what it must be | ask / check |
|---|---|---|
| the layout and the netlist, for an extraction | GDS (stream out of Virtuoso) and CDL of the cell | ask the person |
| the site's PVS LVS rule deck, Quantus technology directory, corner and extraction temperature | from the site file, `$PVS_RULES` / `$QRC_TECH_DIR` / `$QRC_CORNER` / `$EMIR_EXTRACT_TEMP`, or the options | ask: never guess a deck, a corner or a temperature |
| a DSPF, when one exists already | text DSPF from Quantus, with `$W`/`$L` and via annotation | `emir nets <dspf> --format json` |
| the testbench's instance name of the block | for `--prefix` (with its dot: `XDUT.`) | ask the person |
| the process file | the PDK's limits (playbook 04) | `emir check` |

## Commands

### Worked example (runs as printed from the repository root; nothing licensed runs)

The extraction plan for a cell, with stand-in input files (the dry run checks only that they exist):

<!-- test: run -->
```sh
mkdir -p emir_out/extract
printf 'stand-in for the layout\n' > emir_out/extract/railblock.gds
printf '.SUBCKT railblock VDD\n.ENDS\n' > emir_out/extract/railblock.cdl
emir extract --gds emir_out/extract/railblock.gds --cdl emir_out/extract/railblock.cdl --cell railblock --kind r_only --work emir_out/extract/work --format json
```

<!-- test: expect -->
```json
{"schema": "emir.extract/1", "dry_run": true, "kind": "r_only",
 "steps": [{"id": "lvs", "licensed_tool": "pvs"}, {"id": "lvs-gate"}, {"id": "quantus", "licensed_tool": "quantus"}]}
```

`ready` is false here and `missing[]` names each setting (`pvs_lvs_rules`, `qrc_tech_dir`, `qrc_corner`,
`extract_temperature`) and tool (`pvs`, `qrc`), each with `how` to set it. The plan for the sample's rail, at a
site with Spectre, starts with the person's choice of mode (`choose-mode`: rcc, cc-grid or cc-grid self-consistent,
each with its cost and measured accuracy; rcc is recommended for this 17-node net):

<!-- test: run -->
```sh
emir next --dspf emir/data/sample/rail.dspf --process emir/data/sample/process.yaml --have-cadence --format json
```

<!-- test: expect -->
```json
{"schema": "emir.next/1", "mode": {"chosen": null},
 "steps": [{"id": "choose-mode", "needs_human": true}, {"id": "selftest"}, {"id": "nets"}]}
```

Show the person the routes and ask. With their answer (here rcc) the plan goes on:

<!-- test: run -->
```sh
emir next --dspf emir/data/sample/rail.dspf --process emir/data/sample/process.yaml --have-cadence --mode rcc --format json
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

The first Spectre step is the on-site verification: no passing record of Spectre on this machine, so before the
first run that uses it, `emir verify-sim --sim spectre --run` simulates testbenches whose currents are known by
hand (ideal loads, and real MOSFETs, a resistor and a subcircuit-wrapped device whose terminal currents are known).
It runs Spectre: ask the person first. Without `--run` it only writes the six decks and prints the
commands, which you can show them:

<!-- test: run -->
```sh
emir verify-sim --sim spectre --out emir_out/verify-sim/spectre --format json
```

<!-- test: expect -->
```json
{"schema": "emir.verify_sim/1", "simulator": "spectre", "ran": false, "passed": null}
```

The save lines for a testbench whose instance of the block is `XDUT`, written to the file it includes, and the
Virtuoso SKILL files the person loads in their own session:

<!-- test: run -->
```sh
emir save-lines emir/data/sample/rail.dspf --mode rcc --prefix XDUT. --out emir_out/tb/emir_save.scs --format json
emir gui-install --dir emir_out/skill --format json
```

<!-- test: expect -->
```json
{"schema": "emir.gui_install/1"}
```

`emir save-lines --mode rcc` wrote one `save` line of 16 `XDUT.R...:1` branch currents (rcc: every branch).
`emir gui-install` wrote `emir_main.il`, `emir_explorer.il`, `emir_display.drf` and `emir_site.il`; its `cdsinit`
field is the `load(...)` line for the person's `.cdsinit`.

### On the person's files

```sh
emir verify-sim --sim spectre --run --format json      # once per machine and Spectre version; ask first
emir extract --gds <layout.gds> --cdl <netlist.cdl> --cell <cell> --kind r_only --format json
emir extract --gds <layout.gds> --cdl <netlist.cdl> --cell <cell> --kind r_only --run --format json
emir save-lines <cell.r_only.dspf> --mode cc-grid --prefix <XDUT.> --out emir_save.scs --format json
emir check <cell.r_only.dspf> --process <pdk.yaml> --cc-sim <tb.raw> --mode cc-grid --period <T> --format json
emir run <cell.r_only.dspf> --process <pdk.yaml> --cc-sim <tb.raw> --mode cc-grid --period <T> --summary-out emir_out/summary.json --viewer emir_out/viewer --lib <LIB> --format json
emir report --from emir_out/summary.json
```

The `--run` extraction starts PVS and Quantus: only after the person agreed, and only when the dry run says
`ready: true`. A small block uses `--kind rc_coupled` and `--mode rcc` (every branch current); a big block uses
`--kind r_only` for the grid and `--kind c_only_coupled` for the simulation, from one LVS (playbook 02). The
simulation is the person's, or runs with their go-ahead:
`spectre <tb.scs> -format psfascii -raw <tb.raw> =log <tb.log>` (ASCII results only), or
`emir simulate <tb.scs> --sim spectre --dspf <cell.r_only.dspf> --prefix <XDUT.> --mode cc-grid --format json`, which
writes a deck that includes the testbench and the save statements and prints the command; add `--run` after the
person agrees. Spectre APS or Spectre X: pass their options with `--sim-args` (`'+aps'`, or `'+preset=cx
+postlpreset=off'`; `+postlayout=upa` keeps the parasitic resistors rcc needs), to `emir verify-sim` too.
Templates: `agent/cadence/spectre/tb_rcc.scs.tmpl`, `agent/cadence/spectre/tb_cc.scs.tmpl`. In ADE the save file
goes under the test's Definition Files. The mode in each command is the person's choice (the Virtuoso form's Mode
field starts at "choose" and refuses to run until they pick).

The self-consistent loop on Spectre (only when the person asks for it: Spectre runs once per pass): EMIR does not
read a Spectre netlist, so the person adds the per-pin split to the CC netlist once, as printed by
`emir run <cell.r_only.dspf> --net <rail> --split-stub` (the person reconnects each pin as its comments list;
the run refuses a netlist without the split, EMIR-E524); then
`emir run <cell.r_only.dspf> --process <pdk.yaml> --net <rail> --mode cc-grid --self-consistent --netlist <tb_cc.scs> --vdd <V> --run --format json`.
Without `--run` it writes pass 0's droop files (every droop 0 V: each pin's `.pwl` and the manifest `droop.scs`) in `emir_out/selfconsist/<rail>/` and
prints the command (`emir.simulate/1`, `ran` false; its next step carries `needs_human` and `licensed_tool`). UNVERIFIED until checked on site: `emir verify-sim --sim spectre --run` includes a self-consistent case
whose closed-loop answer is known by hand.

### HSPICE

HSPICE is licensed like Spectre: plan, then run only with the person's go-ahead. The route is `emir simulate`'s
(the testbench is SPICE syntax, so EMIR reads it, chooses the mode and puts 0 V probes on subcircuit pins):

```sh
emir verify-sim --sim hspice --format json            # the plan: the decks and the commands, nothing runs
emir verify-sim --sim hspice --run --format json      # after the person agrees: six short runs
emir simulate <tb.sp> --sim hspice --dspf <block.dspf> --mode rcc --out emir_out/sim/result.tr0 --format json
emir simulate <tb.sp> --sim hspice --dspf <block.dspf> --mode rcc --out emir_out/sim/result.tr0 --run --format json
emir run <block.dspf> --process <pdk.yaml> --cc-sim emir_out/sim/result.tr0 --mode rcc --period <T> --summary-out emir_out/summary.json --format json
```

The self-consistent loop on HSPICE: `emir run <block.dspf> --process <pdk.yaml> --net <rail> --mode cc-grid
--self-consistent --sim hspice --netlist <tb_cc.sp> --vdd <V>` writes the split copy of the testbench (a node and a
0 V probe per instance pin; the sources, each pin's droop as an inline PWL, in an included file) and prints the command; `--run` runs
the loop after the person agrees. Written from the HSPICE manuals' syntax: UNVERIFIED until `emir verify-sim --sim
hspice --run` passes on site (its self-consistent case included).

The deck asks for `.option probe post=2 post_version=2001` (ASCII, double precision) and `.probe tran` of the
currents; `<result>.tr0.names` maps HSPICE's names. A result the person made by hand:
`emir save-lines <dspf> --mode rcc --sim hspice --names-out <result.tr0>.names` (or `--mode cc-grid`)
gives the lines and the sidecar; a binary `.tr0` is refused (EMIR-E463: re-run with `post=2`); a swept
result needs `--analysis sweep:N` (EMIR-E465). The details and what is not confirmed: `docs/simulators.md`.

To see the results on the layout, the person (never you) loads the two `load(...)` lines the run prints into the
CIW, or uses **EMIR > Load results** from the menu `emir gui-install` installs; the Explorer opens the layout
read-only and only highlights. KLayout: `klayout <layout.gds> -m emir_out/viewer/emir.lyrdb`.

## Decision points

| you see | do |
|---|---|
| `emir extract` dry run, `ready: false` | show the person `missing[].what` and `how`; ask for each value; never guess a corner or temperature |
| `ready: true` | show the plan (`steps[].command`, `settings`); add `--run` only after the person agrees |
| EMIR-E280 LVS did not match | stop: tell the person; the report path is in `details`; never extract anyway |
| `lvs.reuse` true on the second kind | the matched LVS is reused: the R-only and C-only DSPFs come from one LVS |
| `choose-mode` (no `--mode`) | the person chooses; `facts.size.class` small / medium / big recommends rcc / cc-grid / cc-grid with an R-only grid (playbook 02); a pin at several connections allows rcc only |
| a step with `licensed_tool` | ask the person first, every time |
| a `verify-sim` step (Spectre, HSPICE) | ask; with the go-ahead run it with `--run`; without, run it as printed minus `--run` to show the decks and commands |
| `emir verify-sim` passed | the record is kept; runs from that simulator now say `verified` |
| `emir verify-sim` failed (exit 70, EMIR-E663) | stop using that simulator's results here; report the failing `checks[]` and the version |
| EMIR-W464 on a run | its simulator is not verified on this machine: say so in the report; plan `emir verify-sim` |
| EMIR-W414 on a run | the latest verification of that simulator version FAILED here: do not rely on the numbers; tell the person |
| EMIR-W416 on a run | the result does not say its simulator version (an HSPICE `.tr0` run by hand): simulate with `emir simulate --run` (it records the version), or report it as not verified |
| EMIR-E408 binary PSF | convert it with the `psf` command its `next_steps` gives (a shell command for the person), or re-simulate with `-format psfascii` |
| the person wants results on the layout | `--viewer emir_out/viewer --lib <LIB>`; they load it; say which Virtuoso parts are UNVERIFIED (below) |

## Success criteria

- `emir extract --run` exited 0 with `lvs.matched` true and `dspf` written (or the dry-run plan was handed over).
- `emir check` exited 0; `emir run` printed a verdict; `emir report` was relayed.
- No licensed tool ran without the person's go-ahead; their Virtuoso was not touched.
- Spectre (or HSPICE) was verified on this machine before the first run that used it, or the report says it was
  not.

## What to report

The report (playbook 07), plus which extraction kind and corner the DSPF came from and which Spectre analysis and
period gave the currents, and the run's `simulator_verification` (Spectre verified on this machine, or not: then
say so and that `emir verify-sim --sim spectre --run` is the check). When the person relies on the Virtuoso side,
say that the menu, the form and the Explorer are UNVERIFIED until the headless self-test and the live test pass on
their Virtuoso, and that the extraction is checked by its LVS MATCH gate and `emir check` on the DSPF
(`agent/cadence/README.md` has the status of every file). "Screening, not sign-off." And the research-project
disclaimer, once.

## Failure modes

| code | exit | meaning | do |
|---|---|---|---|
| EMIR-I680 | 0 | a dry run: nothing ran | show the plan; `--run` after the person agrees |
| EMIR-E101 | 2 | the GDS, CDL or `--site` file does not exist | check the path |
| EMIR-E580 | 2 | a setting is missing on `--run` | `details` says which; ask the person for the value |
| EMIR-E680 | 3 | pvs or qrc not found | ask where the site installs them: `--pvs`, `--qrc`, `$PVS_BIN`, `$QRC_BIN`, the site file |
| EMIR-E280 | 2 | LVS did not match: extraction refused | tell the person; do not extract |
| EMIR-E281 | 2 | Quantus wrote no DSPF | read the log its `next_steps` names (a wrong tech directory or corner, usually) |
| EMIR-E681 | 2 | a tool ran past `--timeout` | ask before re-running with a longer one |
| EMIR-E601 | 3 | Spectre not found for `emir run --netlist` | simulate at the site and pass `--cc-sim` |
| EMIR-E662 | 3 | Spectre or HSPICE not found for `--run` | ask where the site installs it: `$SPECTRE_BIN`, `$HSPICE_BIN`, the site file |
| EMIR-I661 | 0 | a plan: the licensed simulator was not run | show the deck and command; `--run` after the person agrees |
| EMIR-E663 | 70 | the on-site verification failed | report it; do not rely on that simulator's results here |
| EMIR-E463 | 2 | a binary HSPICE result | re-run HSPICE with `.option post=2 post_version=2001` |
| EMIR-E465 | 2 | a result with several sweep tables | `--analysis sweep:N` |
| EMIR-E410 | 2 | the result is binary PSF | re-simulate with `-format psfascii` |
| EMIR-I780 | 0 | the viewer data was trimmed (a big run) | `--viewer-all` if the person wants every segment |
