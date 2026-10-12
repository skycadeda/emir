# EMIR agent contract

Generated from the code by `scripts/gen_contract.py`; do not edit by hand (a test fails when it is stale). It is what an agent may rely on: the commands, the documents they print, the exit statuses, the codes, the next steps and the MCP tools of emir 1.1.0.dev0. Screening, not sign-off. EMIR is a research project, provided as is, with no warranty or guarantee of accuracy. It is your responsibility to test and verify its results for your process, tools and design before relying on them. Relay that with every result.

## 1. Commands

19 commands. Each takes `--format text|json` (default text; `emir report` takes md|json|text, md being its text). Options are never abbreviated (a prefix is a usage error, EMIR-E507), and `@FILE` reads arguments from FILE, one per line. `emir <command> --help` prints the full help (`emir.help/1` in JSON mode). `--format` and `--help` are left out of the lists below. `emir process` is made of sub-commands (`emir process new`, `emir process check`), each with its own options.

### emir check

EM / IR screening of one DSPF.

- `<dspf>`: extracted DSPF (R or RC); leave it out to check the --process file on its own; `--dspf FILE` is accepted as the same.
- `--net` (NET): net to analyse, or `all` (the default when the run is driven: every net with resistors)
- `--process` (PROCESS): PDK EM/IR data file (emir.yaml); default from the site file.
- `--em-temp` (C): the temperature (C) to analyse EM at (default: the process file's em_tref): R is scaled by the layer TCR, avg limits are derated by `jmax_factor`.
- `--delta-t` (C; default 5.0): the self-heating temperature rise (C) the rms limits allow, for layers whose rms limit is sqrt(k * delta_T) (`jmax_rms_k`); default 5.
- `--currents` (JSON|FILE): DC currents {"node": amps} into the net, inline JSON or the path of a file holding it; must balance (`emir schema currents`)
- `--ground` (GROUND): reference node.
- `--cc-sim` (CC_SIM): the simulation result rcc reads: a transient of the RC-extracted netlist with every branch current saved (`emir save-lines`), as PSF ASCII or nutascii (a file, or a Spectre -raw directory), HSPICE ASCII (.tr0, .option post=2) or CSDF. The currents come from here instead of --currents.
- `--analysis` (ANALYSIS): which analysis to read from a --cc-sim directory; for a result with several sweep tables, sweep:N picks one.
- `--sim` (spectre | hspice | xyce | ngspice): the simulator that wrote --cc-sim, when neither the result's header nor its names sidecar says (for the on-site verification, `emir verify-sim`); with --netlist, the simulator that runs it (default spectre)
- `--period` (S): the block's period in seconds (1e-08; on the command line also 10n or 10ns), needed for a transient result and a --netlist run (EMIR-E428): every average is over the record's last whole periods of it, up to three. rcc is exact for the simulated record over the whole periods you give (--period); whether the testbench has settled is yours to establish, as for any .meas. Refused, with no number: fewer than two whole periods (EMIR-E431), or the last two differ by more than 2 % on a branch carrying 1 % of its net's largest current (EMIR-E429)
- `--feed` (FEED): node the net is fed at (default: the DSPF port): the IR reference unless --ground is given. One net only.
- `--netlist` (NETLIST): the testbench of the RC netlist to SIMULATE with Spectre instead of reading a finished --cc-sim result (Spectre is licensed: it runs only with --run; `emir simulate` drives HSPICE, Xyce and ngspice)
- `--mode` (rcc): rcc, the one method of a simulated run (it may be left out): the RC netlist was simulated with every branch current saved, and every metric (avg, rms and peak EM, static and dynamic IR) is read from them, exactly. A DC run (--currents) takes no mode.
- `--run` (flag): spectre (licensed): run it on --netlist; without it the command is printed and nothing runs (ask the person first)
- `--sim-timeout` (S): stop the Spectre run of --netlist after this many seconds (default 3600)
- `--save-branches` (flag): print the Spectre `save` lines an RCC run needs. With --net all (or no --net) that covers EVERY net, which is what EM needs -- any wire carrying current is subject to it, not just the rails.
- `--save-prefix` (SAVE_PREFIX): instance path to prepend to saved names when the extracted subckt sits in a testbench, e.g. "XI0.".
- `--html` (DIR): write a self-contained HTML report to this directory (e.g. emir_out/nets). The net map is inline SVG, so it zooms losslessly in a browser; with matplotlib, the run's EM/IR chart beside it (emir_chart.png and emir_chart_light.png: every net, the worst items numbered).
- `--gds` (GDS): with --html: the layout under the chart, drawn by KLayout (a DSPF whose coordinates are the layout's: `emir extract --tool magic` writes them so); --cell names its cell.
- `--open` (flag): open the HTML report in a browser when done.
- `--viewer` (DIR): write the layout-viewer data to this directory (e.g. emir_out/viewer): SKILL for the Virtuoso EMIR Explorer and a marker database for KLayout. Results land on the layout itself, which is where the fix happens.
- `--viewer-all` (flag): write every segment into the viewer data; by default a run with more than 20000 segments keeps the worst (clean ones and IR off the rails left out)
- `--lib` (LIB): OA library the viewer should open (default from the site file)
- `--spectre` (SPECTRE): spectre executable for --netlist (default: $SPECTRE_BIN, site file, then $PATH)
- `--no-check` (flag): skip the input checker (not recommended)
- `--force` (flag): run even if the input checker reports FAIL.
- `--summary-out` (FILE): write the run summary (the emir.run/1 JSON document, the same one --format json prints) to this file; `emir report --from FILE` turns it into the summary for a person.
- `--power-nets` (POWER_NETS): comma-separated supply/ground nets. IR is a PDN quantity by convention, so these set the IR colour scale and are what the viewer opens on; other nets stay selectable (analog bias and reference lines have real drop). Inferred from net names if omitted.
- `--cell` (CELL): cell the viewer should open, and the --gds cell under the chart (default: the DSPF design name)
- `--json-out` (FILE): write the findings (the emir.check/1 document) here.
- `--all` (flag): show the OK lines too.

### emir explain

What an error code means, and the command that fixes or diagnoses it.

- `<CODE>` (any number): EMIR-E204, E204, e204 ...
- `--list` (flag): every code.

### emir extract

GDS -> DSPF. --tool quantus (the default): GDS + CDL with PVS LVS and Quantus; without --run it prints the plan and runs nothing (licensed tools: ask the person before --run). --tool magic: magic (open source) extracts the cell with every resistor's layer, width, length and place (a magic that writes resistor geometry: `extresist geoout`), and EMIR writes the DSPF, which is also the RC netlist rcc simulates; it runs at once.

- `--tool` (quantus | magic; default quantus): quantus: PVS LVS + Quantus (licensed; planned unless --run); magic: magic's extraction with resistor geometry (open source)
- `--gds` (GDS; required): the layout (GDS)
- `--cdl` (CDL): quantus: the netlist LVS compares against (CDL; required)
- `--cell` (CELL): the layout top cell (quantus: required; magic: default the layout's only top cell, read with the klayout package)
- `--kind` (r_only | rc_coupled | rc_decoupled): quantus (required): rc_coupled: the RC netlist rcc simulates and screens; rc_decoupled: the same with grounded capacitance; r_only: the resistor grid, for DC currents.
- `--source-cell` (SOURCE_CELL): quantus: the CDL top cell, if it is not --cell.
- `--work` (WORK): quantus: work directory (default <work_dir>/extract_<cell>)
- `--rules` (RULES): quantus: PVS LVS rule deck (site: pvs_lvs_rules)
- `--tech-dir` (TECH_DIR): quantus: Quantus technology directory (site: qrc_tech_dir)
- `--corner` (CORNER): quantus: Quantus corner (site: qrc_corner)
- `--temperature` (C): quantus: extraction temperature in C (site: extract_temperature)
- `--pvs` (PVS): quantus: pvs executable (default: $PVS_BIN, site file, $PATH)
- `--qrc` (QRC): quantus: qrc executable (default: $QRC_BIN, site file `quantus:`, $PATH)
- `--site` (FILE): the site file to read the tools and decks from (default: $EMIR_SITE, ~/.emir/site.yaml)
- `--rerun-lvs` (flag): quantus: run LVS again even when a matched, up-to-date one is there.
- `--timeout` (S; default 7200.0): seconds each tool may run (default 7200)
- `--run` (flag): quantus: run PVS and Quantus (licensed tools); without it, the plan only (magic always runs: it is open source)
- `--process` (PROCESS): magic (required): the process file: its `pdk:` name finds magic's startup file, its `magic:` section names magic's layers, the devices' terminals and the magic to use.
- `--magicrc` (FILE): magic: its startup file (default: $EMIR_MAGICRC, else $PDK_ROOT/<pdk>/libs.tech/magic/<pdk>.magicrc for the process file's pdk: name)
- `--magic` (EXE): magic: the magic to run, one that writes resistor geometry (default: $EMIR_MAGIC, the process file's magic: binary: (a magic's bare name on $PATH), the site file's magic:, then magic-8.3.515-geo and magic on $PATH)
- `--out` (DIR): magic: where the DSPF goes; magic runs in its magic/ subdirectory, EMIR's own (default <work_dir>/extract_<cell>; a <cell>.dspf there that EMIR did not write is never overwritten: EMIR-E646)
- `--tolerance` (TOLERANCE; default 10.0): magic: `extresist tolerance` (default 10): a net whose resistance is small next to its devices' is not split into resistors (EMIR-W645 lists such nets)
- `--port-pins` (tied | separate; default tied): magic: what to do with a port labelled at several places (each place its own node): tied (default) shorts them to the port with 0 V sources, so current may leave by any of them; separate leaves them apart.

### emir gui-install

Write the Virtuoso SKILL files.

- `--dir` (DIR): where to write the .il files (default: <clone or bundle>/skill, else ~/.emir/skill)

### emir import-ict

Generate the per-PDK EMIR data file from a foundry ICT.

- `--ict` (ICT; required)
- `--emir` (EMIR; required): emir.yaml to update in place.
- `-o`, `--out` (OUT)
- `--conductor-pattern` (CONDUCTOR_PATTERN): ICT conductor block name for a layer, e.g. '{layer}_conn' (the default; {layer} lower-cased, {Layer} as written). Also settable in the file as `ict_conductor_pattern:`; `ict_name:` on one layer names its block outright.

### emir info

Where the tools were found.

No options.

### emir mcp

Serve EMIR over the Model Context Protocol (stdio) for MCP clients: one typed tool per command, returning the command's JSON document. An MCP client starts it; stdout is the protocol channel, logs go to stderr. Needs the MCP Python SDK (the `mcp` extra: pip install -e '.[mcp]' from a clone, or pip install mcp).

- `--list` (flag): print the tools, resources and this machine's client configuration snippets, and exit (works without the SDK)
- `--cwd` (DIR): the working directory for the tools' relative paths (default: where the client starts the server)
- `--agent-dir` (DIR): a clone of the emir repository, to serve AGENTS.md and the agent playbooks as resources (default: found beside the package, in a clone)
- `--isolate` (flag): run every tool call in a fresh Python process: slower, but each call starts clean and a big run's memory is freed.
- `--no-output-schema` (flag): do not advertise the tools' output schemas (for a client whose validator rejects them)

### emir nets

List the nets of a DSPF and their sizes.

- `<dspf>` (required): the DSPF; `--dspf FILE` is accepted as the same.

### emir next

What to run next, and why.

- `--dspf` (DSPF): the extracted DSPF, if there is one.
- `--process` (PROCESS): the process file, if there is one.
- `--cc-sim` (CC_SIM): a finished simulation result.
- `--netlist` (NETLIST): a netlist emir would simulate.
- `--currents` (JSON|FILE): DC currents {node: amps}, inline JSON or the path of a file holding it (read here as `emir run` reads it)
- `--mode` (rcc): rcc, the one method of a simulated run (accepted; it may be left out)
- `--net` (NET)
- `--period` (S): the block's period in seconds (1e-08; on the command line also 10n or 10ns): the planned check and run average over the record's last whole periods of it, and the plan checks that the result gives that window (a transient needs it; DC currents and an operating point have none). rcc is exact for the simulated record over the whole periods you give (--period); whether the testbench has settled is yours to establish, as for any .meas.
- `--viewer` (flag): the person wants the results on the layout.
- `--html` (flag): the person wants HTML reports.
- `--lib` (LIB): OA library, for the viewer.
- `--cell` (CELL): cell, for the viewer.
- `--have-cadence`, `--no-have-cadence` (flag (and its `--no-` form; absent: detected)): whether cadence is available (default: detected, see `emir info`)
- `--have-xyce`, `--no-have-xyce` (flag (and its `--no-` form; absent: detected)): whether xyce is available (default: detected, see `emir info`)
- `--have-ngspice`, `--no-have-ngspice` (flag (and its `--no-` form; absent: detected)): whether ngspice is available (default: detected, see `emir info`)
- `--gds` (GDS): the block's layout (GDS): without a DSPF, magic extracts one (`emir extract --tool magic`); with one, the layout under the --html charts.
- `--run-summary` (FILE): a finished run's summary (emir run --summary-out): plan what follows it; the run's inputs are read from it.
- `--sim` (spectre | hspice | xyce | ngspice): the simulator the person uses (default: detected: Spectre at a Cadence site, then HSPICE, Xyce, ngspice); for a finished --cc-sim, the one that wrote it when the result does not say.

### emir pins

List the instance pins on one net.

- `<dspf>` (required): the DSPF; `--dspf FILE` is accepted as the same.
- `--net` (NET; required)

### emir process

Process data entered by hand, when no readable process file exists for the PDK (the ICT is missing or encrypted). `emir process new` writes the fill-in file for the layers and vias a DSPF uses; `emir process check` says what it still lacks. `emir process new --help` and `emir process check --help` for each.

- `<subcommand>` (new | check; required): new: write the fill-in file; check: what it still lacks.

### emir process new

Write a fill-in process file for the layers and vias a DSPF uses (or the ones you list), every value empty with its unit and a source slot, for a person to type from the PDK's design manual when no readable process data exists (the ICT is missing or encrypted). It never fills in a value.

- `--from-dspf` (DSPF): the DSPF whose layer tokens, $lvl numbers and vias the file covers.
- `--pdk` (NAME): the PDK's name, written as `pdk:` (shown in reports)
- `-o`, `--out` (FILE; default emir_out/process.yaml): the file to write (default emir_out/process.yaml); an existing file is never overwritten (EMIR-E326)
- `--layers` (L1,L2): without --from-dspf: the conductor layers, comma-separated.
- `--vias` (V1,V2): without --from-dspf: the vias, comma-separated (their below/above are left for the person)
- `--interactive` (flag): ask a person for each value and its source at a terminal; refused when stdin is not a terminal (agents never use it)

### emir process check

What a process file still lacks before a run: empty values (required: emir refuses to run; optional: that check is not made), values with no source in a hand-entered file, likely unit slips, limits out of order, and with --dspf the DSPF layers it does not cover. Exit 2 when a required value is missing or another check FAILs. `emir check --process` runs the same checks.

- `<PROCESS>` (required): the process file to check.
- `--dspf` (DSPF): a DSPF of the block: the layers it uses must be in the file, and its wires wider than their width loss.
- `--em-temp` (C): the analysis temperature to check the derating at (default: the file's em_tref)

### emir report

The summary of a run to relay to a person.

- `--from` (FILE; required): the run summary (emir run --summary-out FILE); - reads stdin.
- `--top` (TOP; default 10): how many worst items to list (default 10)

### emir run

EM / IR screening of one DSPF.

- `<dspf>` (required): extracted DSPF (R or RC); `--dspf FILE` is accepted as the same.
- `--net` (NET): net to analyse, or `all` (the default when the run is driven: every net with resistors)
- `--process` (PROCESS): PDK EM/IR data file (emir.yaml); default from the site file.
- `--em-temp` (C): the temperature (C) to analyse EM at (default: the process file's em_tref): R is scaled by the layer TCR, avg limits are derated by `jmax_factor`.
- `--delta-t` (C; default 5.0): the self-heating temperature rise (C) the rms limits allow, for layers whose rms limit is sqrt(k * delta_T) (`jmax_rms_k`); default 5.
- `--currents` (JSON|FILE): DC currents {"node": amps} into the net, inline JSON or the path of a file holding it; must balance (`emir schema currents`)
- `--ground` (GROUND): reference node.
- `--cc-sim` (CC_SIM): the simulation result rcc reads: a transient of the RC-extracted netlist with every branch current saved (`emir save-lines`), as PSF ASCII or nutascii (a file, or a Spectre -raw directory), HSPICE ASCII (.tr0, .option post=2) or CSDF. The currents come from here instead of --currents.
- `--analysis` (ANALYSIS): which analysis to read from a --cc-sim directory; for a result with several sweep tables, sweep:N picks one.
- `--sim` (spectre | hspice | xyce | ngspice): the simulator that wrote --cc-sim, when neither the result's header nor its names sidecar says (for the on-site verification, `emir verify-sim`); with --netlist, the simulator that runs it (default spectre)
- `--period` (S): the block's period in seconds (1e-08; on the command line also 10n or 10ns), needed for a transient result and a --netlist run (EMIR-E428): every average is over the record's last whole periods of it, up to three. rcc is exact for the simulated record over the whole periods you give (--period); whether the testbench has settled is yours to establish, as for any .meas. Refused, with no number: fewer than two whole periods (EMIR-E431), or the last two differ by more than 2 % on a branch carrying 1 % of its net's largest current (EMIR-E429)
- `--feed` (FEED): node the net is fed at (default: the DSPF port): the IR reference unless --ground is given. One net only.
- `--netlist` (NETLIST): the testbench of the RC netlist to SIMULATE with Spectre instead of reading a finished --cc-sim result (Spectre is licensed: it runs only with --run; `emir simulate` drives HSPICE, Xyce and ngspice)
- `--mode` (rcc): rcc, the one method of a simulated run (it may be left out): the RC netlist was simulated with every branch current saved, and every metric (avg, rms and peak EM, static and dynamic IR) is read from them, exactly. A DC run (--currents) takes no mode.
- `--run` (flag): spectre (licensed): run it on --netlist; without it the command is printed and nothing runs (ask the person first)
- `--sim-timeout` (S): stop the Spectre run of --netlist after this many seconds (default 3600)
- `--save-branches` (flag): print the Spectre `save` lines an RCC run needs. With --net all (or no --net) that covers EVERY net, which is what EM needs -- any wire carrying current is subject to it, not just the rails.
- `--save-prefix` (SAVE_PREFIX): instance path to prepend to saved names when the extracted subckt sits in a testbench, e.g. "XI0.".
- `--html` (DIR): write a self-contained HTML report to this directory (e.g. emir_out/nets). The net map is inline SVG, so it zooms losslessly in a browser; with matplotlib, the run's EM/IR chart beside it (emir_chart.png and emir_chart_light.png: every net, the worst items numbered).
- `--gds` (GDS): with --html: the layout under the chart, drawn by KLayout (a DSPF whose coordinates are the layout's: `emir extract --tool magic` writes them so); --cell names its cell.
- `--open` (flag): open the HTML report in a browser when done.
- `--viewer` (DIR): write the layout-viewer data to this directory (e.g. emir_out/viewer): SKILL for the Virtuoso EMIR Explorer and a marker database for KLayout. Results land on the layout itself, which is where the fix happens.
- `--viewer-all` (flag): write every segment into the viewer data; by default a run with more than 20000 segments keeps the worst (clean ones and IR off the rails left out)
- `--lib` (LIB): OA library the viewer should open (default from the site file)
- `--spectre` (SPECTRE): spectre executable for --netlist (default: $SPECTRE_BIN, site file, then $PATH)
- `--no-check` (flag): skip the input checker (not recommended)
- `--force` (flag): run even if the input checker reports FAIL.
- `--summary-out` (FILE): write the run summary (the emir.run/1 JSON document, the same one --format json prints) to this file; `emir report --from FILE` turns it into the summary for a person.
- `--power-nets` (POWER_NETS): comma-separated supply/ground nets. IR is a PDN quantity by convention, so these set the IR colour scale and are what the viewer opens on; other nets stay selectable (analog bias and reference lines have real drop). Inferred from net names if omitted.
- `--cell` (CELL): cell the viewer should open, and the --gds cell under the chart (default: the DSPF design name)
- `--fail-on-violation` (flag): exit 1 when any net's verdict is FAIL (for CI and agents); without it a completed run exits 0.

### emir save-lines

Print the lines that save every branch current an rcc run reads: Spectre `save`, HSPICE `.probe` (with the ASCII output option), Xyce `.print`, ngspice `.save` (--sim).

- `<dspf>` (required): the DSPF; `--dspf FILE` is accepted as the same.
- `--net` (NET): one net (default: all)
- `--mode` (rcc): rcc, the one method (it may be left out): every branch current, for a simulation of the RC netlist.
- `--prefix` (PREFIX): instance path in the testbench, e.g. "XI0.".
- `--out` (FILE): also write the lines to FILE (to include in the testbench)
- `--sim` (spectre | hspice | xyce | ngspice; default spectre): the simulator whose syntax to print (default spectre)
- `--names-out` (FILE): hspice/xyce/ngspice: write the names sidecar here (<result>.names), mapping the simulator's current names to the DSPF's.
- `--raw` (FILE): hspice/xyce: the result file the lines write (HSPICE: the .tr0 of `hspice -o <stem>`; default: --names-out without .names)

### emir schema

Print the JSON Schema (2020-12) of a document emir reads or writes.

- `<name>`: e.g. run, check, process, error.
- `--list` (flag): every schema.

### emir selftest

Offline proof that the install works (a few seconds).

- `--keep` (flag): keep the output directory.
- `--out` (OUT): output directory (default: temp)

### emir simulate

Simulate a testbench so that `emir run --cc-sim` can read the currents: writes a copy of the testbench with the lines that save every branch current rcc reads (Xyce .print, ngspice .save, HSPICE .probe with ASCII output; Spectre: a deck that includes the testbench and a file of save statements), runs it to a text result, and writes the <result>.names sidecar that maps the simulator's names to the DSPF's. The testbench is the person's: stimulus, models, a .tran analysis, and the RC-extracted netlist (the DSPF itself). Spectre and HSPICE need a licence: without --run the deck is written and the exact command printed, nothing runs (ask the person, then add --run).

- `<testbench>` (required): the testbench (its .include files are followed; Spectre: any syntax Spectre reads)
- `--sim` (spectre | hspice | xyce | ngspice; required): the simulator (spectre and hspice: licensed, planned unless --run)
- `--dspf` (DSPF; required): the DSPF the currents are for.
- `--net` (NET): one net (default: every net)
- `--mode` (rcc): rcc, the one method (it may be left out): the testbench simulates the RC netlist and every branch current is saved.
- `--out` (RAW; default emir_out/sim/result.raw): the result to write (default emir_out/sim/result.raw; HSPICE: <stem>.tr0; Spectre: a PSF ASCII directory); the deck, the log and the names sidecar go beside it.
- `--prefix` (PREFIX): the instance path of the extracted block in the testbench, e.g. XI0. (default: found in the testbench; Spectre: required, '' for the top level)
- `--subckt` (SUBCKT): the block's .subckt name (default: the DSPF's design)
- `--process` (PROCESS): the process file, so the next steps are the exact check and run commands.
- `--timeout` (S; default 3600.0): stop the simulator after this many seconds (default 3600)
- `--run` (flag): spectre, hspice (licensed): run the simulator; without it the deck is written and the command printed, and nothing runs (the open simulators always run)
- `--sim-args` (ARGS): extra arguments for the simulator's command line, split as a shell would: Spectre APS '+aps', Spectre X '+preset=cx +postlpreset=off' (keep the parasitic resistors for rcc) ...

### emir validate

Run the analytic validation suite (6 closed-form cases).

- `--out` (OUT): directory for the test files (default: a temp dir, removed when every case passes)
- `--keep` (flag): keep the output directory.
- `--quick` (flag): leave out the slow cases (none is slow now: kept for the scripts that pass it)
- `--no-skip` (flag): kept for the scripts that pass it: every case needs only numpy, scipy and PyYAML, so none is skipped for a missing package.

### emir verify-sim

Prove on this machine that a simulator's route into EMIR gives the right numbers: simulate small testbenches with hand-checkable answers (the sample rail with ideal current-source loads, DC and pulsed, R and RC; and with real devices on it -- a diode-connected PMOS and NMOS, a resistor, a subcircuit-wrapped NMOS), every branch current saved, through the same route a run takes (save lines, names sidecar, reader), run rcc on them and compare with the analytic answers; and record the outcome in the EMIR state directory ($EMIR_STATE_DIR, else ~/.emir/verified.json). Runs then count a result as verified only when the latest record for its simulator, version, format and --sim-args passed with this emir minor version. Spectre and HSPICE need a licence: without --run the decks are written and the commands printed, nothing runs. Screening, not sign-off.

- `--sim` (spectre | hspice | xyce | ngspice; required): the simulator to verify.
- `--run` (flag): spectre, hspice (licensed): run them (ask the person first); xyce and ngspice always run.
- `--out` (DIR): where the testbenches and results go (default emir_out/verify-sim/<sim>)
- `--timeout` (S; default 600.0): stop each simulation after this many seconds (default 600)
- `--record-host` (flag): also keep this machine's host name in the record (left out unless asked); a record with a host counts only on that host.
- `--sim-args` (ARGS): extra simulator arguments, as your runs use them (Spectre APS '+aps', Spectre X '+preset=cx +postlpreset=off' ...): the verification then covers that configuration only (runs simulated with the same --sim-args); kept in the record.

## 2. JSON output and documents

- With `--format json`, stdout carries exactly ONE JSON document; everything else (progress, tables, logs) goes to stderr. Every document starts with `schema` (`emir.<name>/1`) and `emir_version`.
- A value emir could not compute (NaN, Inf) is written as null: every computed number may be null, and a reader reports it as "not a number", never as 0.
- A command that cannot do its job prints `emir.error/1` instead (its fields: command, code, level, title, message, hint, exit_code, next_steps, details).
- A refused, failed or interrupted `emir run` also writes its `emir.error/1` to `--summary-out`, replacing any previous summary there.
- Documents may gain fields without a version change; removing a field or changing its meaning moves the version (`/2`).
- `emir schema <name>` prints a schema (draft 2020-12, ids `urn:emir:schema:<name>:1`); `emir schema --list` lists them; the files are `agent/schemas/<name>.schema.json`.

23 documents:

| document | schema | printed by |
|---|---|---|
| `emir.check/1` | `emir schema check`: emir check | `emir check` |
| `emir.error/1` | `emir schema error`: emir error | every command, when it cannot do its job |
| `emir.explain/1` | `emir schema explain`: emir explain | `emir explain` |
| `emir.extract/1` | `emir schema extract`: emir extract | `emir extract` |
| `emir.gui_install/1` | `emir schema gui_install`: emir gui-install | `emir gui-install` |
| `emir.help/1` | `emir schema help`: emir --help | `--help` in JSON mode |
| `emir.import_ict/1` | `emir schema import_ict`: emir import-ict | `emir import-ict` |
| `emir.info/1` | `emir schema info`: emir info | `emir info` |
| `emir.mcp/1` | `emir schema mcp`: emir mcp --list | `emir mcp` |
| `emir.nets/1` | `emir schema nets`: emir nets | `emir nets` |
| `emir.next/1` | `emir schema next`: emir next | `emir next` |
| `emir.pins/1` | `emir schema pins`: emir pins | `emir pins` |
| `emir.process_check/1` | `emir schema process_check`: emir process check | `emir process check` |
| `emir.process_new/1` | `emir schema process_new`: emir process new | `emir process new` |
| `emir.report/1` | `emir schema report`: emir report | `emir report` |
| `emir.run/1` | `emir schema run`: emir run (the run summary) | `emir run` |
| `emir.save_lines/1` | `emir schema save_lines`: emir save-lines | `emir run`, `emir save-lines` |
| `emir.schema_list/1` | `emir schema schema_list`: emir schema --list | `emir schema --list` |
| `emir.selftest/1` | `emir schema selftest`: emir selftest | `emir selftest` |
| `emir.simulate/1` | `emir schema simulate`: emir simulate | `emir run`, `emir simulate` |
| `emir.validate/1` | `emir schema validate`: emir validate | `emir validate` |
| `emir.verified/1` | `emir schema verified`: the verification records (verified.json) | written to a file (see its schema) |
| `emir.verify_sim/1` | `emir schema verify_sim`: emir verify-sim | `emir verify-sim` |

3 inputs and written files without the envelope:

- `currents`: DC currents for `emir run --currents` / `emir check --currents`: node name -> amps INTO the net at that node (a load drawing current is negative, the feed positive).
- `names`: A JSON file written beside a simulation result as `<result>.names` (result.raw -> result.raw.names).
- `process`: PDK EM/IR data for emir (YAML).

`emir schema NAME` itself prints the raw schema (no envelope) in either format.

## 3. Exit status

| status | meaning |
|---|---|
| 0 | done (check: the inputs are OK) |
| 1 | a FAIL verdict, with --fail-on-violation |
| 2 | bad input, or refused |
| 3 | a required external tool or Python package is missing |
| 70 | internal error (EX_SOFTWARE): a bug, or a failed self-test |
| 130 | interrupted (SIGINT, Ctrl-C) |
| 143 | terminated (SIGTERM) |

`check`, `selftest`, `validate`, `verify-sim` and `run` judge something: they always print their own document, and their status follows the verdict (`check`: 2 when an input FAILs; `selftest`, `validate`, `verify-sim`: 70 on a failure; `run`: 1 on a FAIL verdict, only with `--fail-on-violation`). Text-mode errors go to stderr as `emir: EMIR-Exxx: message`, then `  hint:` and `  next:` lines. `EMIR_DEBUG=1` re-raises an uncoded exception with its traceback. emir never prompts an agent: the one prompt, `emir process new --interactive`, is a person's at a terminal and refuses any other stdin (EMIR-E327).

## 4. Codes

Format `EMIR-<L><area><nn>`; the letter is the level (`E` error, `W` warning, `I` info). Branch on the code, never on the message. Every `E` code has an exit status; `W` and `I` codes are notices (`notices[]` in the documents). 210 codes are registered and 158 retired; `agent/errors.md` (generated) is the catalog, and `emir explain <code>` prints one entry.

| area | what | E | W | I |
|---|---|---|---|---|
| 1xx | files and I/O | 8 | 0 | 0 |
| 2xx | DSPF and extracted layout | 19 | 9 | 4 |
| 3xx | process file and layer mapping | 27 | 23 | 11 |
| 4xx | currents and simulation results | 28 | 11 | 7 |
| 5xx | options and command-line usage | 13 | 0 | 0 |
| 6xx | external tools | 20 | 7 | 7 |
| 7xx | outputs and viewers | 3 | 4 | 3 |
| 8xx | hierarchy (removed) | 0 | 0 | 0 |
| 9xx | internal | 6 | 0 | 0 |

Within an area each part of emir has its own decade (so parallel work never collides); area 8 is retired whole:

| numbers | owner |
|---|---|
| x01-x39 | the base package |
| x40-x49 | the magic route (`emir extract --tool magic`; the removed layout command before it) |
| x50-x59 | big blocks (removed: retired) |
| x60-x69 | the simulators (`emir simulate`, `save-lines --sim`, `emir verify-sim`, the HSPICE and CSDF readers) |
| x70-x79 | the GDS layout reader (`emir/gds.py`) and the PDK files |
| x80-x89 | the Cadence integration (`emir extract`) |
| x90-x99 | the MCP server (`emir mcp`) |

- A code's `command` template may hold `[...]` optional parts and may have a `fallback`; its command may be empty, and then the hint says what to do. No code's command is `emir explain` of itself.
- Codes are never reused: a code no longer raised moves to `RETIRED` with the reason.
- To add a code: add it to `emir/errors.py`, raise or notice it, run `scripts/gen_errors_md.py` (and this script). The tests enforce: every code registered and used, the letter matches the level, no plain `raise`, no uncoded warning line, the catalog current.

## 5. Next steps

Documents carry `next_steps`, a list of `{why, command}`. Exact commands to run next, in order; each runs as printed. An emir command keeps the caller's --format, except a run's `emir report` step (its Markdown is what to relay).

- Every command runs as printed: paths are shell-quoted, and a step whose command cannot be filled completely is not given.
- An emir command keeps the caller's `--format` (`output.keep_format`), error next steps and the fixes `emir check` suggests included; the `emir report` step of a run is given without it, because its Markdown is what to relay (add `--format json` for the JSON document).
- A step whose command is not an emir command (`ls`, `tail`, `klayout` ...) carries `"shell": true` (`output.mark_shell`): it needs a shell, which the MCP server does not have (`emir_cli` refuses it with EMIR-E591).
- `emir check`: when the inputs pass, the run command with `--summary-out emir_out/summary.json`; when they fail, each FAIL's command, once per distinct command.
- `emir run`: `emir report --from <summary>` (with `--summary-out`; without it, the run again with `--summary-out emir_out/summary.json`, then the report); on FAIL or WARN the run with `--html emir_out/html`; on FAIL the same command again, to re-run after the fix.
- Errors: the code's filled command (or its fallback), or none.

### emir next

`emir next` (`emir.next/1`) is the playbooks' decision tree as code. Its `steps[]` items have: n, id, command, why, needs_human, licensed_tool, long_run, ask, status, after, placeholders, tool. Run them in order and stop at the first that exits non-zero. A step with `needs_human` true (a licensed tool, a long run, or an `ask`: values only the person can give) needs the person's go-ahead first. A `waiting` step does not run as printed: replace its `placeholders` (`<result.raw>`, `<testbench.cir>` ...; the pattern is `<[A-Za-z][\w.\-]*>`) with what the steps in `after` produced. Inputs it is given (a currents file, a tap file, a process file) are read as the run reads them: it plans no step the run would refuse, and `missing[]` says what is needed.

Thresholds (printed in every plan): `small_net_nodes` 500, `big_block_nodes` 50000, `probe_result_mb` 200, `magic_gds_mb` 20.

Rules, in order (a feature adds its own in `emir/plan_<feature>.py`):

10 preflight, 15 cadence-extract, 15 magic, 20 dspf, 30 process, 31 process-by-hand, 40 size, 45 opensim-testbench, 50 currents, 54 window, 55 cadence-testbench, 55 opensim, 57 period, 58 verify-sim, 60 analyse, 61 run-summary, 62 verify-sim-result, 65 opensim-result, 70 viewer, 75 cadence-viewer, 90 guardrails, 95 run-summary-report, 99 mcp.

## 6. MCP

`emir mcp` (the `[mcp]` extra) serves every command but `mcp` as a typed tool `emir_<command>` (`-` becomes `_`; a command made of sub-commands is one tool per sub-command, `emir_process_new`), plus `emir_cli` for a whole emir command line (a `next_steps` command as printed; emir commands only, no shell). A tool's parameters are its command's options, named after the long option (`--cc-sim` -> `cc_sim`; `from` -> `from_file`). Options no tool has: `--format`, `--help` (every tool); `--open` (`check`); `--interactive` (`process new`); `--open` (`run`). A call runs `emir <command> ... --format json` and returns its one document (`structuredContent`; `isError` for `emir.error/1`; the exit status and command line in `_meta`).

- Arguments are the tool's parameter names exactly: an unknown one, or a required one left out, is refused with EMIR-E592 and nothing runs.
- `confirm: true` is the person's go-ahead. A call that would run a licensed tool, run long, rewrite a file in place, or run a command the server has not classified is refused with EMIR-E590 without it; ask the person first, every time.
- `emir next` over MCP gives each step its `tool` call (null for a step that is not an emir command).

| tool | command | go-ahead (`confirm`) | read-only |
|---|---|---|---|
| `emir_check` | `emir check` | an output parameter names an existing file outside the server's working directory (it would be overwritten) | no |
| `emir_explain` | `emir explain` | never | yes |
| `emir_extract` | `emir extract` | run is true: it runs PVS and Quantus (licensed), possibly for a long time; tool is magic and the layout is above the size where magic's extraction can run long (plan.THRESHOLDS magic_gds_mb); an output parameter names an existing file outside the server's working directory (it would be overwritten) | no |
| `emir_gui_install` | `emir gui-install` | an output parameter names an existing file outside the server's working directory (it would be overwritten) | no |
| `emir_import_ict` | `emir import-ict` | out is not given: it rewrites the process file in place; an output parameter names an existing file outside the server's working directory (it would be overwritten) | no |
| `emir_info` | `emir info` | never | yes |
| `emir_nets` | `emir nets` | never | yes |
| `emir_next` | `emir next` | never | yes |
| `emir_pins` | `emir pins` | never | yes |
| `emir_process_check` | `emir process check` | never | yes |
| `emir_process_new` | `emir process new` | an output parameter names an existing file outside the server's working directory (it would be overwritten) | no |
| `emir_report` | `emir report` | never | yes |
| `emir_run` | `emir run` | netlist is given with run true and sim spectre (the default): it runs a licensed simulator (without run it only plans); the DSPF is ADC-top or PLL-top size (the run is long); an output parameter names an existing file outside the server's working directory (it would be overwritten) | no |
| `emir_save_lines` | `emir save-lines` | an output parameter names an existing file outside the server's working directory (it would be overwritten) | no |
| `emir_schema` | `emir schema` | never | yes |
| `emir_selftest` | `emir selftest` | an output parameter names an existing file outside the server's working directory (it would be overwritten) | no |
| `emir_simulate` | `emir simulate` | run is true with sim spectre or hspice: it runs a licensed simulator; the DSPF is above the small-net size (a long simulation); the testbench or the .spiceinit beside it holds a .control block or a shell line (the simulator runs those commands); an output parameter names an existing file outside the server's working directory (it would be overwritten) | no |
| `emir_validate` | `emir validate` | an output parameter names an existing file outside the server's working directory (it would be overwritten) | no |
| `emir_verify_sim` | `emir verify-sim` | run is true with sim spectre or hspice: it runs a licensed simulator (three short simulations); an output parameter names an existing file outside the server's working directory (it would be overwritten) | no |
| `emir_cli` | a command line | the command runs a licensed tool, runs long, rewrites a file in place, or is not classified by this server | no |

## 7. Python API (for contributors)

- `EmirError(code, message, *, hint, next_steps, details, **ctx)`: a refusal; `ctx` fills the code's command template.
- `notice(code, msg, log)` inside `collecting()`: a coded warning or info.
- Checker findings: `Report.fail/warn/info/ok(code, area, msg, fix, **ctx)`.
- A new command: `def cmd(argv, out)` that parses with `output.parser`, calls `out.emit(document(...))` once, is registered in `cli.COMMANDS`, and has its schema in `emir/schemas.py`; its MCP tool follows from its parser.
- Options that take JSON inline or from a file read it with `emir/jsonarg.py`; references to the repository's files go through `emir/repo.py`.
