# EMIR with Cadence tools

The Cadence integration: extraction with PVS and Quantus (`emir extract`), Spectre
testbenches, and the Virtuoso side -- an `EMIR` menu, the Screening form and the
Explorer that puts the results on the layout. EMIR itself needs no Cadence licence
to analyse; Cadence tools make the DSPF and the currents, and Virtuoso shows the
result. **Screening, not sign-off.**

For an agent: running PVS, Quantus or Spectre needs the person's go-ahead (they are
licensed tools); `emir extract` runs nothing without `--run`. Never start, kill or
restart the person's Virtuoso or simulator: they load the EMIR menu and the
Explorer in their own session.

## Status of every file

Nothing Cadence runs where this repository is built and tested, and nothing here
claims that a Cadence tool was verified anywhere. Support is proven on the machine
that uses the tool, so every file below is **UNVERIFIED until checked on site**. For
most there is a check the local agent runs there, with the person's go-ahead for a
licensed tool (named in the status column); the others have no check yet and are
checked by hand. The on-site checks:

| tool | the check |
|---|---|
| Spectre | `emir verify-sim --sim spectre --run` (with `--sim-args` for APS or Spectre X): a hand-checkable testbench through Spectre, read back, rcc against the analytic answer ([Simulators](../../docs/simulators.md#on-site-verification)) |
| Virtuoso | the headless SKILL self-test, `python agent/cadence/python/virtuoso_batch.py --selftest` (batch Virtuoso loads `emir_main.il` and checks its procedures), then the live test, `LIVE_TEST.md` (the person drives the session) |
| PVS, Quantus | `emir extract --run` refuses to extract unless LVS says MATCH, then `emir check <cell>.<kind>.dspf --process <pdk.yaml>` names every annotation EMIR needs that the DSPF lacks |
| Calibre, StarRC | `emir check` on the DSPF they write, as above |
| OCEAN | none: UNVERIFIED until checked on site |

| file | what | status |
|---|---|---|
| `skill/emir_init.il` | entry point for `.cdsinit`, driven by `EMIR_HOME` and friends | UNVERIFIED until checked on site by the local agent (the headless self-test, then live test step 4) |
| `skill/emir_main.il` | the EMIR menu and the Screening form (generated copy of `emir/skillgui.py`) | UNVERIFIED until checked on site by the local agent (the headless self-test, then the live test) |
| `skill/emir_display.drf` | the Explorer's colour packets `EMIR_B0..11` (EMIR's colour scale: the blue ramp, the warn amber, the failure red), `EMIR_SEL` (generated) | UNVERIFIED until checked on site by the local agent (live test, step 3) |
| `emir_explorer.il` (written next to every run's viewer data, from `emir/skillapp.py`) | the Explorer | UNVERIFIED until checked on site by the local agent (live test) |
| `quantus/emir.ccl.tmpl` | the Quantus command file (generated copy of `emir/cadence.py`) | UNVERIFIED until checked on site by the local agent (`emir extract --run`, then `emir check`) |
| `quantus/render_ccl.py` | renders it without emir installed | renders the same command lines as `emir extract` (tested here, all three kinds); the file it writes: as above |
| `pvs/pvs_lvs_quantus.sh` | LVS, the MATCH gate, Quantus, from the shell | UNVERIFIED until checked on site by the local agent (its MATCH gate, then `emir check`); tested here with stand-in tools only |
| `spectre/tb_rcc.scs.tmpl` | the Spectre testbench of an rcc run (the RC DSPF simulated, every branch current saved) | the route they follow is UNVERIFIED until checked on site by the local agent (`emir verify-sim --sim spectre --run`); the templates are filled by hand |
| `ocean/emir_run.ocn.tmpl` | the same flow from OCEAN | UNVERIFIED until checked on site (a sketch) |
| `calibre/`, `starrc/` | what EMIR needs from those extractors, with public sources | UNVERIFIED until checked on site (`emir check` on the DSPF) |
| `python/virtuoso_batch.py` | batch Virtuoso from Python, and the headless self-test | tested here with a stand-in only; its `--selftest` is the on-site check |
| `site/emir_site.cadence.yaml.example` | the site-file keys | placeholders, UNVERIFIED for any real site |
| `test/emir_manual_test.il`, `LIVE_TEST.md` | the live test, run on site | not run yet |
| `SKILL_NOTES.md` | lessons for SKILL contributors | notes from development, UNVERIFIED until checked on site |

## 1. Load the EMIR menu in Virtuoso

**From a clone or the offline bundle** (no generated file):

```sh
export EMIR_HOME=/path/to/emir          # the clone or the unpacked bundle
export EMIR_PROCESS=/path/to/pdk.yaml   # optional: the default process file
export EMIR_LIB=MYLIB                   # optional: the default OA library
```

and one line in `~/.cdsinit`:

```skill
load(strcat(getShellEnvVar("EMIR_HOME") "/agent/cadence/skill/emir_init.il"))
```

`EMIR_BIN` (default `$EMIR_HOME/bin/emir`), `EMIR_WORK` (`emir_out`), `EMIR_VIEW`
(`layout`) and `EMIR_UNSET_LD=1` (start emir without Virtuoso's `LD_LIBRARY_PATH`)
are optional too. `emirSettings()` in the CIW prints what was taken.

**From any install** (pip included): `emir gui-install` writes `emir_main.il`,
`emir_explorer.il`, `emir_display.drf` and `emir_site.il` into `<clone>/skill/` or
`~/.emir/skill/` (or `--dir D`). `emir_site.il` is the only file holding this
machine's paths; `.cdsinit` loads it: `load("<dir>/emir_site.il")`.

**Colours:** append `emir_display.drf` to `~/display.drf` (create it if there is
none) and restart Virtuoso. Never put it in place of a project or PDK display.drf:
that removes every layer's colours. Without the packets the Explorer draws every
band in one colour and says so in the CIW. (`emir_init.il` also tries `drLoadDrf`
at start; whether that works is UNVERIFIED.)

The menu: **EMIR > Screening...** (the form: Check inputs, Run, Save lines, Open
HTML, Load viewer), **Spectre save lines...** (the same form), **Load results...**,
**Site info**, **Cadence integration notes** (this page).

## 2. Extraction: `emir extract`

```sh
emir extract --gds blk.gds --cdl blk.cdl --cell blk --kind rc_coupled          # the plan only
emir extract --gds blk.gds --cdl blk.cdl --cell blk --kind rc_coupled --run    # LVS + Quantus
```

Without `--run` it prints the plan (`--format json`: an `emir.extract/1` document
with `ready`, `missing`, the commands and the command file) and changes nothing.
With `--run`: PVS LVS, then the gate -- the LVS report must say MATCH, or the
extraction is refused (EMIR-E280) -- then Quantus, writing `<cell>.<kind>.dspf` in
the work directory (`emir_out/extract_<cell>`). A matched LVS newer than the GDS
and the CDL is reused, so a second kind comes from the same LVS.

Tools and decks: `--pvs`, `--qrc`, `--rules`, `--tech-dir`, `--corner`,
`--temperature`, or `$PVS_BIN`, `$QRC_BIN`, `$PVS_RULES`, `$QRC_TECH_DIR`,
`$QRC_CORNER`, `$EMIR_EXTRACT_TEMP`, or the site file
(`site/emir_site.cadence.yaml.example`). No corner and no temperature is assumed.

| kind | what | for |
|---|---|---|
| `r_only` | resistance only | the resistor grid `emir run` screens with DC currents (`--currents`) |
| `rc_coupled` | R and coupling C | the RC netlist rcc simulates (every branch current saved) and screens |
| `rc_decoupled` | R and grounded C | the same with grounded capacitance |

An `r_only` netlist is **not** the netlist to simulate: it has no capacitance.

**The command-file settings that must not be dropped** (a test checks each one):

| setting | why |
|---|---|
| `-analysis "em"` | keeps per-segment geometry; otherwise series R is reduced and a merged resistor hides the neck |
| `-include_cap_model "false"`, `-include_res_model "false"`, `-include_parasitic_cap_model "false"` | devices as plain elements; with model names Spectre stopped on a 3-terminal capacitor written with two terminals |
| `-temperature` | Quantus scales R and writes `*\|OPERATING_TEMPERATURE`; emir reads it and does not scale R again |
| `-sub_node_char ":"` | emir puts a node without a `*\|S` record on its net by splitting at `:` |
| `-disable_instances false` | keeps the `*\|I` instance-pin records simulated currents attach to (inferred) |
| `-net_name_space "SCHEMATIC"` | every kind of extraction names nets as the schematic does |
| `-output_xy`, `-include_parasitic_res_width_drawn true` | coordinates for the viewer; the width J = I/w needs |
| an absolute LVS-database path | Quantus runs in the work directory |

Other extractors: `calibre/NOTES.md`, `starrc/NOTES.md` (UNVERIFIED). Whatever made
the DSPF, `emir check <dspf> --process <pdk.yaml>` names every missing annotation.

## 3. Currents: Spectre

**rcc** (exact): simulate the RC extraction with every branch current saved, and
screen every metric (avg, rms and peak EM, static and dynamic IR) from them.

```sh
emir save-lines blk.rc_coupled.dspf --prefix XDUT. --out emir_save.scs
spectre tb_rcc.scs -format psfascii -raw tb_rcc.raw =log tb_rcc.log    # spectre/tb_rcc.scs.tmpl
emir run blk.rc_coupled.dspf --process pdk.yaml --cc-sim tb_rcc.raw --net all \
    --period 10n --summary-out emir_out/summary.json
```

`--period` is the block's period (here 10 ns): the window is the record's last whole
periods of it, up to three. rcc is exact for the simulated record over the whole periods
you give (--period); whether the testbench has settled is yours to establish, as for any
.meas. Refused, with no number: EMIR-E428 (no period), E431 (fewer than two periods), E429
(the last two differ).

On a big block (ADC top, PLL top) that RC transient is expected to run long and may
converge poorly: `emir next` plans it as a long run, for the person's go-ahead.
UNVERIFIED until checked where Spectre runs.

Results must be text: `-format psfascii` (or nutascii). Binary PSF is not read
(EMIR-E408 gives the `psf` conversion command).
`emir simulate <tb.scs> --sim spectre --dspf <dspf> --prefix XDUT.`
writes a deck that includes the testbench and the save statements, and prints the
command; `--run` runs it (ask the person first).
Before the first run that uses Spectre on a machine, `emir verify-sim --sim spectre
--run` proves the route there ([Simulators](../../docs/simulators.md)); APS and
Spectre X take `--sim-args` (`+postlayout=upa`, `+postlpreset=off` keep the parasitic
resistors rcc needs).

**From ADE:** add `emir_save.scs` under the test's Definition Files, write the
results as psfascii, then **EMIR > Screening...** with the result directory in
"Spectre result". `ocean/emir_run.ocn.tmpl` is an UNVERIFIED OCEAN sketch of the
same.

## 4. Results on the layout

The run's `--viewer DIR` writes `emir_data.il` and `emir_explorer.il`. **EMIR >
Load results...** (or the form's Load viewer, or Run with "Open Explorer when done")
opens the layout **read-only** and starts the Explorer: the segments at or above
80 % of their limit, ranked worst first, each with its fix (on the bundled sample:
"Metal1: widen 0.750 -> 1.255 um (x1.67)", "Cont: 1 -> 2 cuts (+1)"); EM or IR;
Next/Prev; zoom to selection. Highlights
never change the cellview. Coordinates are those of the extracted cell, so a
block's results open on the block's layout. A run with more than 20 000 segments
keeps the worst (`--viewer-all` keeps all). Without Virtuoso, `--viewer` also
writes a KLayout marker database (`emir.lyrdb`).

## Measured at the original development site

On an extracted OTA; internal measurements, quoted as the source of the numbers
below, not reproducible from this repository. They are numbers about the method, not
evidence that these tools work with EMIR: every Cadence route stays UNVERIFIED until its
on-site check (above) passes on the machine that runs the tools.

- GDS + CDL to DSPF in 12.7 s: PVS LVS 2.4 s, Quantus 10.3 s.
- Quantus per kind, one LVS: `r_only` 10.5 s (288 R, 249 nodes), `rc_coupled` 11.1 s.
- RCC simulation 1.00 s at 249 nodes.

On a generated test block (inverters on a segmented rail), RCC: 1.92 s at 41 rail
nodes, 3.83 s at 521, 9.53 s at 2057 (`emir/rcc.py`).

Cadence, Virtuoso, Spectre, Quantus, PVS and OCEAN are trademarks of Cadence Design
Systems, Inc. EMIR is not affiliated with or endorsed by Cadence. EMIR is a research
project, provided as is, with no warranty or guarantee of accuracy: test and verify
its results for your process, tools and design before relying on them.
