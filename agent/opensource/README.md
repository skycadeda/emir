# Open-source tools: Xyce, ngspice, KLayout, magic

EMIR's DSPF flow with no licensed tool: **Xyce** or **ngspice** simulates the
block and supplies the currents; **KLayout** shows the results on the layout.
On an open PDK with no DSPF yet, **magic** (built with the public `geoout`
patch) extracts one: `emir extract --tool magic` (playbook 09).
An agent drives it through the `emir` commands (each one runs as printed and
answers in JSON); a person can run the one-shot scripts here.

| file | what it does |
|---|---|
| `run_xyce.sh`, `run_ngspice.sh` | the whole flow in one go: simulate, check, run, report (both call `emir_open_flow.sh`) |
| `emir_open_flow.sh` | the flow itself: `emir simulate` -> `emir check` -> `emir run` (HTML, KLayout markers) -> `emir report` |
| `dspf_preview_gds.py` | a preview GDS of a DSPF's conductors (each resistor's box), to look at the markers on when the block's GDS is not at hand |
| `lyrdb_summary.py` | what a KLayout marker database holds (categories, marker counts), without a window |
| `render_markers.py` | a picture of the markers over a layout, made by KLayout headless |

Tested with Xyce 7.10 (a development build) and ngspice 45 on Linux, KLayout
0.30.2 (the application) and 0.30.12 (the Python package).

## For an agent

```sh
emir next --dspf block.dspf --process pdk.yaml --netlist tb.cir --format json
```

With Xyce or ngspice found and no Spectre, the plan's simulation step is
`emir simulate` on the testbench given with `--netlist`. It reads the
testbench to choose the mode: the DSPF included (the RC netlist) means rcc,
a CC netlist means cc. It adds `--probe` when the devices are subcircuits.
Then come `emir check`, `emir run` and `emir report` on the result it writes
(`emir_out/sim/result.raw`). Every step runs as printed. Without `--netlist`, the
simulate step waits for a testbench (`<testbench.cir>`). Before the first simulation
on a machine the plan also runs `emir verify-sim --sim xyce` (or ngspice): seconds, no
licence, and it proves the simulator's route into EMIR on that machine; a run from a
simulator with no passing record there says "not verified on this machine" (EMIR-W464;
EMIR-W414 when the latest verification failed, EMIR-W416 when the result does not say
its version).

## For a person

```sh
agent/opensource/run_xyce.sh --mode cc-grid [--net VDD] [--probe] [--period 1e-8] \
    [--out emir_out] tb.cir block.dspf pdk.yaml
```

`--mode` (rcc or cc-grid) is required: it is the person's choice (`emir next` lists what
each costs and how accurate it is). rcc needs the DSPF itself in the testbench; cc-grid a CC
netlist (with `--probe` when the devices are subcircuits).

It prints the report and where the HTML and the marker database went.
Exit status: the first failing step's (`emir`'s contract), else 0, whatever the
verdict.

## The testbench

The person's, in SPICE: supplies, stimulus, device models, a `.tran` over whole
periods of the block's activity, and the extracted netlist instantiated once:

* **rcc**: the DSPF itself (`.include block.dspf`). Every resistor current
  is printed and read directly: exact, and the right mode for small nets.
* **cc** (cc-grid): a CC netlist (the devices on ideal nets: a C-only
  extraction, or the schematic). Each instance pin's current is printed and
  superposed through the R-only DSPF: scales to big blocks.

## What `emir simulate` does to it

It writes a copy beside the result: `<result>.<sim>.cir`, plus copies of the
included files it changes. The testbench itself is never touched. In the copy:

* the print lines go in before `.end`:
  * Xyce: `.print tran format=raw file=result.raw I(XI0:Rm1) ...`, run as
    `Xyce -a` (`-a` makes the raw file ASCII; without it the file is binary,
    which emir does not read);
  * ngspice: `.save @r.xi0.rm1[i] ...` and `.options filetype=ascii`, run as
    `ngspice -b -r result.raw deck` (in that mode a `.control` section is not
    run: `EMIR-W660`);
* relative `.include` / `.lib` paths are rebased onto the copy's directory
  (both simulators resolve them from the including file's directory, measured);
* for Xyce, the DSPF's `$KEY=value` annotations are left out of its copy
  (Xyce rejects them as unknown parameters; ngspice reads them as comments);
* with `--probe` (cc), each device terminal on the net gets a 0 V source in
  series, `VEMIRk node node__emirk 0`, whose current is the pin's.

Then it writes `<result>.names`, which maps the simulator's names to the DSPF's.
It reads the result back and reports any current asked for that is missing
(`EMIR-W461`).

| current | Xyce | ngspice | DSPF name |
|---|---|---|---|
| resistor Rm1 in XI0 | `I(XI0:RM1)` | `i(@r.xi0.rm1[i])` | `Rm1:1` |
| MOSFET MN0's drain in XI0 | `ID(XI0:MN0)` | a 0 V probe (`--probe`): ngspice's `@m[id]` carries the model's polarity | `MN0:d` |
| a probe on XMN0's drain | `I(XI0:VEMIR0)` | `i(v.xi0.vemir0)` | `XMN0:d` |
| a subcircuit instance's pin | none: probe it | none: probe it | `XMN0:d` |

Every sign is +1 (a two-terminal element's pin 2: -1): Xyce and ngspice give a
resistor's current from its first node to its second and a terminal current into the
terminal, as Spectre does.

Devices inside library subcircuits (sky130's `sky130_fd_pr__nfet_01v8`, for
example, is a `.subckt` with ports `d g s b`) are named by their ports when the
netlist defines the subcircuit. A `.lib` model file is not read for that: give
`--pins sky130_fd_pr__nfet_01v8=d,g,s,b`, or `--dspf`, which settles the name
when a device has one pin on the net.

## By hand, without `emir simulate`

```sh
# the lines, and the sidecar beside the result they write (sim/result.raw)
emir save-lines block.dspf --mode rcc --sim xyce --prefix XI0. \
    --names-out sim/result.raw.names > saves.inc
# Xyce only: a copy of the DSPF without its $ annotations (comment lines kept)
sed -e '/^[^*]/s/ \$[^ ]*//g' block.dspf > block.xyce.sp
# in the testbench: include block.xyce.sp (not the DSPF), and saves.inc before .end
Xyce -a tb.cir                     # from the directory that holds sim/
emir run block.dspf --process pdk.yaml --cc-sim sim/result.raw --mode rcc --summary-out emir_out/summary.json
```

(Measured on the 02 example: these five steps reproduce `emir simulate`'s
result. With ngspice: `--sim ngspice`, no `sed`, and `ngspice -b -r
sim/result.raw tb.cir`.)

For devices in subcircuits, `emir probe tb.cir -o tb.probed.cir --dspf block.dspf
--net VDD --names-out probes.names` writes the probed netlist. Then
`emir save-lines ... --mode cc-grid --probes probes.names` prints their lines.

A result simulated with emir's lines but no sidecar is recognised:
`emir next --cc-sim result.raw ...` plans the `emir save-lines ... --names-out
result.raw.names` that maps it.

## KLayout: the marker database

`emir run ... --viewer DIR` writes `DIR/emir.lyrdb` (with the `klayout` Python
package: `pip install -e '.[klayout]'` from a clone, else `pip install klayout`; without it,
`EMIR-W703`). Open it over
the block's layout:

```sh
klayout block.gds -m emir_out/viewer/emir.lyrdb
```

The Marker Browser lists categories `EM/<net>/<ratio band>` (FAIL at 1.00x and
above, WARN from the process file's warn fraction) and `IR/<net>/<mV band>`.
Each marker is the segment's box in the DSPF's coordinates (um). Its text gives
the segment, layer, kind, metric, ratio, current and the fix. The cell is the
DSPF's design name: open the layout at that cell.

Without the block's GDS:

```sh
python agent/opensource/dspf_preview_gds.py block.dspf -o emir_out/preview.gds
klayout emir_out/preview.gds -m emir_out/viewer/emir.lyrdb
```

Without a window (an agent checking before it tells a person to look):

```sh
python agent/opensource/lyrdb_summary.py emir_out/viewer/emir.lyrdb --json
klayout -b -r agent/opensource/lyrdb_summary.py -rd lyrdb=emir_out/viewer/emir.lyrdb
QT_QPA_PLATFORM=offscreen klayout -z -r agent/opensource/render_markers.py \
    -rd gds=emir_out/preview.gds -rd lyrdb=emir_out/viewer/emir.lyrdb -rd png=markers.png
```

What was checked here: KLayout 0.30.2 (the application, batch mode) and 0.30.12
(the Python package) both load the database. On the 02 example, a headless
render puts the FAIL marker on the far contact at x = 60 um, where the DSPF
has it. Not checked: the interactive Marker Browser itself (no display on the
test machine).

Screening, not sign-off.
