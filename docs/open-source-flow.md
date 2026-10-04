# The open-source flow

EMIR with no licensed tool at all: Xyce or ngspice for the currents, magic for the
extraction, KLayout for the layout, an open PDK's own data for the limits. Two routes,
by what you have:

| you have | route | command |
|---|---|---|
| a DSPF (from any extractor) and a SPICE testbench | simulate with Xyce or ngspice, then the usual check and run | `emir simulate`, `emir run` |
| a GDS layout on an open PDK, and a SPICE testbench | magic extracts the DSPF (every resistor with its geometry), then route 1 | `emir extract --tool magic` |

The results land on the layout in KLayout either way. `emir next` plans each route:
`--netlist TB` for the first, `--gds G --cell C` for the second.
HSPICE and Spectre have routes of their own ([Simulators](simulators.md)).

**Screening, not sign-off.** The open PDK files in [`pdks/`](../pdks/) are built from
the PDKs' public data and cite every value; they are not foundry-qualified rule decks.

## Route 1: a DSPF with Xyce or ngspice

EMIR reads a simulation's currents by the DSPF's own names: `R:1` for a branch current
(rcc mode), `inst:pin` for an instance pin's current (cc-grid mode). Spectre saves
currents under those names; Xyce and ngspice print different things, in their own
syntax, under their own names. Three commands bridge the gap:

- `emir save-lines --sim xyce|ngspice` prints the lines in that simulator's syntax and
  writes the **names sidecar** that maps the simulator's names to the DSPF's;
- `emir probe` puts a 0 V source in series with device terminals that no simulator
  prints directly (a subcircuit's pins: how open PDKs ship their transistors);
- `emir simulate` does both into a copy of your testbench, runs the simulator to an
  ASCII result, writes the sidecar and checks that every current asked for is there.

```sh
emir next --dspf block.dspf --process pdks/sky130.yaml --netlist tb.cir --format json
# the first step, choose-mode: the person picks rcc or cc-grid (here rcc)
emir next --dspf block.dspf --process pdks/sky130.yaml --netlist tb.cir --mode rcc --format json
emir verify-sim --sim xyce          # once per machine and Xyce version (seconds)
emir simulate tb.cir --sim xyce --dspf block.dspf --mode rcc --out emir_out/sim/result.raw
emir check block.dspf --process pdks/sky130.yaml --cc-sim emir_out/sim/result.raw --mode rcc
emir run   block.dspf --process pdks/sky130.yaml --cc-sim emir_out/sim/result.raw --mode rcc \
    --summary-out emir_out/summary.json --html emir_out/html --viewer emir_out/viewer
emir report --from emir_out/summary.json
```

The mode is the person's choice: `emir next` without `--mode` lists the routes and
what the testbench can give (the DSPF included, the RC netlist: rcc or cc-grid; a CC
netlist: cc-grid only), and plans nothing that simulates until one is chosen. With it,
`emir simulate` saves what that mode needs (rcc: every branch current; cc-grid: each
device pin's current, `--probe` when the devices are subcircuits, and on
ngspice for MOSFETs, BJTs and JFETs, whose `@m[id]` carries the model's polarity). With Xyce or ngspice
found and no Spectre, `emir next` then plans exactly these steps, each runnable as
printed. `--mode cc-grid --self-consistent --netlist tb.cir --vdd V` closes the droop
loop on a CC testbench: EMIR writes the per-pin split copy itself
([Methods and modes](method-modes.md#closing-the-loop-self-consistency),
[`examples/12_self_consistent`](../examples/12_self_consistent/)). `emir verify-sim` proves the simulator's route on this
machine first: a run from a simulator whose latest record here is not a pass for its
version carries EMIR-W464 (EMIR-W414 when that verification failed, EMIR-W416 when the
result does not say its version) ([On-site verification](simulators.md#on-site-verification)).

### The testbench

It is yours, in SPICE: supplies, stimulus, device models, a `.tran` over whole periods
of the block's activity, and the extracted netlist instantiated once:

- **rcc**: the DSPF itself (`.include block.dspf`). Every resistor current is printed
  and read directly: exact, and the right mode for small nets.
- **cc** (cc-grid): a CC netlist (the devices on ideal nets: a C-only extraction, or the
  schematic). Each instance pin's current is printed and superposed through the R-only
  DSPF: scales to big blocks.

### What `emir simulate` does to it

It writes a copy beside the result, `<result>.<sim>.cir`, plus copies of the included
files it changes; your testbench is never touched. In the copy:

- the print lines go in before `.end`. Xyce: `.print tran format=raw file=result.raw
  I(XI0:Rm1) ...`, run as `Xyce -a` (`-a` makes the raw file ASCII; without it the
  file is binary, which EMIR does not read). ngspice: `.save @r.xi0.rm1[i] ...` and
  `.options filetype=ascii`, run as `ngspice -b -r result.raw deck`. A `.control`
  section of the testbench runs in that mode too (checked with ngspice 45: what it
  sets applies to the result, and a `shell` line runs a program), so EMIR warns
  (`EMIR-W660`): a testbench is code, run only decks you trust (SECURITY.md);
- relative `.include` and `.lib` paths are rebased onto the copy's directory (both
  simulators resolve them from the including file's directory, measured);
- for Xyce, the DSPF's `$KEY=value` annotations are left out of its copy (Xyce rejects
  them as unknown parameters; ngspice reads them as comments);
- for ngspice, a `.spiceinit` beside the testbench (open PDKs ship one) is copied
  beside the deck, because ngspice reads it from the directory it runs in;
- with `--probe` (cc), each device terminal on the net gets a 0 V source in series,
  `VEMIRk node node__emirk 0`, whose current is the pin's.

Then it writes `<result>.names`, reads the result back through it, and reports any
current asked for that is not there (`EMIR-W461`).

### Names and signs

| current | Xyce prints | ngspice saves | DSPF name | sign |
|---|---|---|---|---|
| resistor Rm1 in XI0, first node to second | `I(XI0:RM1)` | `i(@r.xi0.rm1[i])` | `Rm1:1` | +1 |
| pin 2 of a two-terminal element RL2 (a resistor, a diode's cathode) | `I(XI0:RL2)` | `i(@r.xi0.rl2[i])` | `RL2:2` | -1 |
| MOSFET MN0's drain in XI0, into the pin (`IG`, `IS`, `IB` likewise) | `ID(XI0:MN0)` | a 0 V probe (below) | `MN0:d` | +1 |
| a BJT's collector (base, emitter likewise) | `IC(...)` | a 0 V probe | `Q0:c` | +1 |
| a 0 V probe on XMN0's drain | `I(XI0:VEMIR0)` | `i(v.xi0.vemir0)` | `XMN0:d` | +1 |
| a subcircuit instance's pin | none: probe it | none: probe it | `XMN0:d` | |

Spectre's `inst:pin` is positive into the instance, and `R:1` positive from the
resistor's first node to its second; Xyce and ngspice follow the same conventions, so
every sign is +1 but a two-terminal element's pin 2 (the negative of its current). ngspice's
`@m...[id]` (and a BJT's or JFET's) is the model's internal current, which for a PMOS has
the opposite sign of the current into the pin, so emir reads those pins through 0 V
probes on ngspice ([Simulators](simulators.md#xyce-and-ngspice)). The instance path (`--prefix`, or found in the testbench) becomes
`XI0:` in Xyce; ngspice flattens it into the element name (`r.xi0.rm1`). Both were
measured with the block one and two levels down (`tests/test_opensim_sims.py`).

**The names sidecar**, `<result>.names`, is
`{"map": {SIMULATOR_NAME: ["inst:pin" or "R:1", sign]}}`, keys matched without
regard to case (`emir schema names`). A result simulated with EMIR's lines but no
sidecar is recognised: `emir next --cc-sim result.raw ...` plans the
`emir save-lines ... --names-out result.raw.names` that maps it.

### By hand, without `emir simulate`

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

With ngspice: `--sim ngspice`, no `sed`, and `ngspice -b -r sim/result.raw tb.cir`.
On example 02 these steps reproduce `emir simulate`'s result. For devices in
subcircuits, `emir probe tb.cir -o tb.probed.cir --dspf block.dspf --net VDD
--names-out probes.names` writes the probed netlist, and
`emir save-lines ... --mode cc-grid --probes probes.names` prints their lines. Probe the
testbench, not the block's own netlist: a file with no `.tran` (a bare `.subckt`) gets
no simulate step and the warning `EMIR-W463` (`has_tran` false in its document).

PDK devices defined in a `.lib` file (sky130's `sky130_fd_pr__nfet_01v8` is a
`.subckt` with ports `d g s b`) are not read for their port names: give
`--pins sky130_fd_pr__nfet_01v8=d,g,s,b`, or `--dspf`, which settles the name when a
device has one pin on the net.

### Measured: the open-simulator proof

`scripts/opensim_proof.py` runs real MOS loads (level-1 models) on the rail of
example 02 (12 x 1 ohm Metal1 segments, contacts of 12 ohm with 2 cuts and 24 ohm with
1 cut, the bundled illustrative limits) through Xyce and ngspice, in cc-grid (the CC
netlist, devices probed) and rcc (the RC netlist), at 100 C. Measured on Linux x86_64
with Xyce 7.10 (a development build) and ngspice 45.

| case | quantity | hand | Xyce cc-grid | Xyce rcc | ngspice cc-grid | ngspice rcc |
|---|---|---|---|---|---|---|
| dc (sinks always on) | Rm1 avg | 4.16667x | 4.16667x | 4.16667x | 4.16667x | 4.16667x |
| | Rc3 avg | 3.33333x | 3.33333x | 3.33333x | 3.33333x | 3.33333x |
| | IR | 138.000 mV | 138.000 | 138.000 | 138.000 | 138.000 |
| pulse (example 02) | Rm1 avg | 0.84722x | 0.84746x | 0.84746x | 0.84746x | 0.84746x |
| | Rm1 rms | 0.25218x | 0.25223x | 0.25223x | 0.25223x | 0.25223x |
| | Rc3 peak | 1.04167x | 1.04167x | 1.04167x | 1.04167x | 1.04167x |
| | IR | 28.060 mV | 28.068 | 28.068 | 28.068 | 28.068 |
| | dynamic IR | 90.000 mV | | 90.000 | | 90.002 |
| inverter (CMOS, 0.5 pF loads) | Rm1 avg | | 0.17557x | 0.17512x | 0.17558x | 0.17512x |
| | Rm1 rms | | 0.14492x | 0.13164x | 0.14492x | 0.13163x |
| | Rm1 peak | | 0.52559x | 0.42386x | 0.52559x | 0.42386x |
| | Rc3 peak | | 2.62795x | 1.88273x | 2.62796x | 1.88278x |
| | IR | | 5.689 mV | 5.672 | 5.689 | 5.672 |
| | dynamic IR | | | 158.201 mV | | 158.199 |

- **dc** is exact: the sinks' currents do not depend on the rail, so every mode and
  simulator lands on the hand values to the solver's tolerance (within 1.3e-9).
- **pulse**: the 2.9e-4 to the hand values (2.83e-4 at worst) is the trapezoid rule on the 0.1 ns gate
  ramps, sampled every 10 ps. Between ngspice's two modes, 1.1e-4 on Rm1's peak: the
  RC netlist also charges the rail's own capacitance, which the CC netlist does not
  have.
- **inverter**: the averages agree (cc-grid 0.26 % above rcc: with an ideal rail the
  devices draw slightly more charge per period). The spikes
  do not: with an ideal rail the CC netlist's devices switch harder, so cc-grid reads
  the far contact's peak 40 % higher, the feed's rms 10 % higher and its peak 24 %
  higher. Conservative here, by a margin that depends on the block.
- **Xyce against ngspice** on the same circuit: within 9.1e-10 (dc), 6.8e-6 and 1.0e-4
  (pulse, cc-grid and rcc), 8.6e-5 and 1.1e-4 (inverter).
- **With the sky130 PDK's own nfet** (opt-in: `PDK_ROOT=... python
  scripts/opensim_proof.py --cases sky130 --sims ngspice`): cc-grid read above rcc on
  all 48 segment-metric pairs, by 0.5 % (Rc0's peak) to 1.6 % (Rc3's rms), and by 1.4 %
  on the IR (29.128 against 28.728 mV). Each simulation took about 12 to 20 s on the
  development machine (indicative), nearly all of it ngspice loading the PDK's models.

Simulation times on the development machine (indicative): ngspice 0.02 to 0.14 s, Xyce
0.2 to 0.9 s per run, 4 001 to 8 236 time points.

### Examples 02 and 03

[`examples/02_xyce`](../examples/02_xyce/) and
[`examples/03_ngspice`](../examples/03_ngspice/) run the same invented circuit (four
subcircuit NMOS sinks on the sample-style rail, every number worked by hand in 02's
README) through each simulator, in both modes. Both end FAIL on Rc3's peak, 1.04167x,
the one-cut contact of the 2.5 mA sink. About 3 s each:

```sh
bash examples/02_xyce/run.sh       # needs Xyce on PATH (or $XYCE_BIN); exit 3 without it
bash examples/03_ngspice/run.sh    # needs ngspice on PATH (or $NGSPICE_BIN)
```

### Limits of route 1

- The netlist's instance and resistor names must be the DSPF's (the same extraction or
  LVS naming). The DSPF is flat, under one instance of the block in the testbench
  (more instances: `--prefix` picks one, `EMIR-E261`).
- A transient (`.tran`) over whole periods; no PSS or harmonic balance on this route
  (`EMIR-E462` without a `.tran`). EMIR reads time-domain ASCII raw files only.
- Port names of a subcircuit defined only in a `.lib` file are not read (above).
- The deck runs in the result's directory: data files a testbench names relative to
  its own directory (a PWL `file=`) are not rebased, only includes.
- A two-terminal device with both pins on the saved nets has one current for two DSPF
  pins; the second is left to a probe (`EMIR-E460`).
- Probes add unknowns, so the simulator's time steps change a little: on Xyce, direct
  leads and probes gave ratios 3.5e-6 apart.
- Tested with one Xyce build (7.10, a development build) and ngspice 45; gf180mcu and
  IHP SG13G2 models were not tried, and netlist probing was not measured on big blocks.

## Route 2: a GDS layout on an open PDK, extracted by magic

When there is no DSPF yet, magic extracts one from the GDS. magic is the open-source
extractor that open PDKs ship a technology file for. It computes every resistor of a
net from one drawn shape. `emir extract --tool magic` has it keep each one: its layer,
width, length and place. EMIR writes the result as a DSPF, and from there it is route 1:

```sh
emir next --gds blk.gds --process pdks/sky130.yaml --cell blk --format json   # plans the two steps below
emir extract --tool magic --gds blk.gds --cell blk --process pdks/sky130.yaml --out emir_out/extract_blk
emir next --dspf emir_out/extract_blk/blk.dspf --process pdks/sky130.yaml --netlist tb.cir \
    --gds blk.gds --cell blk --format json                                        # route 1, the mode asked for
```

`<out>/<cell>.dspf` is the network. Its testbench `.include` is also the RC netlist for
rcc. `<out>/<cell>.cc.spice` is the CC netlist for cc-grid: the same device instances
on ideal nets. With `--html`, `emir run --gds blk.gds --cell blk` draws the layout under
the chart.

### The magic it needs

A stock magic writes each extracted resistor's value only. The `geoout` patch adds
`extresist geoout on`, which writes `<cell>.res.geo` beside `<cell>.res.ext`, one line
per resistor:

    resgeo "node1" "node2" ohms layer dir width length cl x1 y1 x2 y2 merged

With `extresist simplify off` as well, magic merges nothing, so every resistor is one
drawn segment. Without `geoout` the patch changes nothing. It is public: branch
[`extresist-geoout`](https://github.com/codervijay/magic/tree/extresist-geoout) of
`codervijay/magic`, offered upstream as
[RTimothyEdwards/magic#562](https://github.com/RTimothyEdwards/magic/pull/562) (open).
To build it:

```sh
git clone -b extresist-geoout https://github.com/codervijay/magic magic-geo
cd magic-geo && ./configure --prefix=$HOME/magic-geo && make && make install
export EMIR_MAGIC=$HOME/magic-geo/bin/magic
```

The branch is on magic 8.3.684; EMIR's examples were made with magic 8.3.515 and the
same patch. The test cells in `tests/data/magic_tiny` give the same resistor network
with both; 8.3.684 extracts other capacitances.

EMIR tries, in order: `--magic`, `$EMIR_MAGIC`, the process file's `magic: binary:` (a
magic program's bare name on `$PATH`, starting with `magic`: a process file comes with a
PDK, so a path or another program there is refused, `EMIR-E648`), the site file's
`magic:` (`$MAGIC_BIN`), then `magic-8.3.515-geo` and `magic` on `$PATH`
(`magic-8.3.515-geo` is only the name the patched build had where EMIR was developed). It asks each one `extresist geoout` and uses the first that answers `on`.
When none does, `emir extract` exits 3 with `EMIR-E643`, listing what it tried and how
to build the patched magic. magic's technology is the PDK's startup file,
`$PDK_ROOT/<pdk>/libs.tech/magic/<pdk>.magicrc` for the process file's `pdk:` name
(`--magicrc` overrides it).

### What it writes

- **The script** is the one the extraction runs: `gds read`, the cell flattened
  (`flatten -dotoplabels`: every subcell's shapes, the top cell's labels only, renamed
  back to the cell's name), `port makeall`, `extract all`, the lumped netlist,
  `extresist simplify off`, `extresist geoout on`, `extresist all`, and the split
  netlist. It names only a fixed file for the layout (a link to yours) and the cell. A
  cell name that a Tcl script could misread is refused (`EMIR-E243`). A hierarchical
  layout therefore extracts as the flat one would (checked on a small test cell, its
  devices in a subcell with labels of its own); magic outputs of a hierarchy extracted
  cell by cell are refused (`EMIR-E647`).
- **Where it writes.** The DSPF and the CC netlist go into `--out` (default
  `emir_out/extract_<cell>`). magic runs in `magic/` under it, a directory of EMIR's own
  (a `.emir-magic-work` file marks it), where EMIR replaces magic's files on every run.
  A `<cell>.dspf` or `<cell>.cc.spice` in `--out` that EMIR did not write, or a `magic/`
  there that is not EMIR's, is never overwritten (`EMIR-E646`).
- **Each resistor** carries its shape: a wire `$layer $dir $W $L` with its centerline
  ends `$X $Y $X2 $Y2` and its rectangle `$LLX $LLY $URX $URY` (um), a via or contact
  array `$layer $cuts` (`$N`, the cut count) at `$X $Y`. The layer is magic's name. The
  process file's `magic: layers:` maps the names onto its own: `metal1` is `met1`,
  `viali` is `mcon`, and so on (`pdks/sky130.yaml` cites the PDK's magic technology
  file for each).
- **Every conductor magic extracts is in the network**: contacts, local interconnect,
  diffusion, poly, the gate, the wells under a substrate tap. A layer the process file
  gives no limit still carries its current in the IR drop and is reported as not
  checked (`EMIR-W307`; `unchecked_layers` in the extraction's document).
- **Each device terminal** is a `*|I` record at its own node, named as the process
  file's `magic: devices:` names the model's terminals (`d g s b` for sky130's
  MOSFETs, the order of the models' subcircuits). A model it does not name gets
  `t1, t2 ...` (`EMIR-W643`).
- **Ports.** A port drawn with several pins (a rail labelled at both ends) has a node
  per pin, `vss` and `vss.p2`. By default each extra pin is joined to the port by a
  0 V source (`--port-pins tied`). `emir run` reads such a source as a short, so every
  pin is fed. With `--port-pins separate` they are left apart.
- **Capacitors** are counted once: magic's lumped netlist of the same run counts each
  coupling once, where the split netlist counts it again on each net's ground. Each
  is spread over its nets' nodes by their share (magic's per-node capacitance, else
  their drawn area), half from each net to the other net's nearest node. Every net's
  total is kept.
- **The CC netlist** has the split netlist's device instances, each node on its net,
  and the lumped capacitors.

A net that magic leaves whole (its resistance small next to its devices', at
`--tolerance`, default 10) has no resistor to check (`EMIR-W645`).

The DSPF writer and the geometry reader are ported from the Skycadeda Analog Framework
(SAF), with credit and the licence in
[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).
The tests check the writer against committed magic outputs (`tests/test_magic.py`): every
resistor magic extracted, the tied pins, the capacitance counted once. A test marked
`magic` extracts the same cell with a real patched magic when one is present.

### The currents

Two routes, each route 1's, and the mode is the person's choice:

- **rcc**: the testbench includes the DSPF. `emir simulate --mode rcc` prints every
  resistor's current, and `emir run --mode rcc` reads them: exact.
- **cc-grid**: the testbench includes `<cell>.cc.spice`. `emir simulate --mode cc-grid
  --probe` puts a 0 V probe on every device terminal. sky130's MOSFETs are subcircuits,
  and the probes name their pins from the models' `.subckt` lines, as the DSPF names
  them. `--self-consistent` feeds each pin its droop
  ([Methods and modes](method-modes.md#closing-the-loop-self-consistency)).

### Example 08

[`examples/08_open_layout`](../examples/08_open_layout/) runs the route on a synthetic
SKY130 cell: four two-finger NMOS, whose `out` and `vss` each reach their pad through
one via2 cut. magic extracted 1135 resistors in 0.2 s. Each run is at the process
file's 90 C. The gate is pulsed, three 4 ns periods, with Xyce 7.10:

| run | out: IR, worst ratio | vss: IR, worst ratio |
|---|---|---|
| rcc (the reference) | 6.8744 mV, 1.3566x (via2: 1 -> 2 cuts) | 4.9185 mV, 1.3566x |
| cc-grid, open loop | 7.3006 mV, 1.4389x | 5.2834 mV, 1.4400x |
| cc-grid, self-consistent (out, vss, vb) | 6.8620 mV, 1.3534x | 4.9630 mV, 1.3535x |

- rcc's 440 checked branches equal the simulator's own currents to 6e-12.
- Branch by branch against rcc, the open loop reads high on every branch (governing
  avg +6.1 %, peak +14.8 % on out and +29.2 % on vss).
- The loop brings the governing branches within 0.3 % (avg, rms) and 1.9 % (peak).
  What it leaves is on vss's minor branches (-6 % to +10 %): the substrate, and the
  grid's own capacitance that its branches do not carry.
- The example's README has every number, and how they compare with the stated accuracy
  of cc-grid.

![EM and IR chart of example 08's SKY130 NMOS array over its layout, from magic's network: out and vss FAIL on their single via2 cut (1.36x avg, fix 1 -> 2 cuts); IR 6.87 mV on out and 4.92 mV on vss; the diffusion, poly and well under the devices drawn grey, not checked.](images/example08_sink.png#only-light)
![EM and IR chart of example 08's SKY130 NMOS array over its layout, from magic's network: out and vss FAIL on their single via2 cut (1.36x avg, fix 1 -> 2 cuts); IR 6.87 mV on out and 4.92 mV on vss; the diffusion, poly and well under the devices drawn grey, not checked.](images/example08_sink_dark.png#only-dark)

*Example 08: the chart of its rcc run, `emir run --html DIR --gds sink.gds` (synthetic layout, the SKY130 limits of pdks/sky130.yaml).*

Until this release, EMIR's layout command built a network of its own from the layout's
rectangles, from li1 up. It is removed. On the same GDS at the operating point, with
the same currents, it reported an IR of 24.55 mV on out and 15.41 mV on vss. magic's
network gives 15.67 mV and 11.33 mV, and the same ratio on the single via2 cut (3.09x).
The rectangle network differed from magic's in more than one way (no contacts or
diffusion, contact columns grouped, currents spread along the straps). Which of these
made the gap was not measured. A rectangle network's error depends on the layout and
can go either way.

### Limits of route 2

- It needs the patched magic until the patch is in a magic release.
- Tested on sky130 only, with magic 8.3.515 and open_pdks 1.0.510's technology file.
  A hierarchical layout is flattened first; that was checked on small test cells only.
  gf180mcu and IHP SG13G2 were not tried. Extraction time and network size on big
  blocks were not measured.
- **A net with no label** gets a name from magic, written differently in its two
  netlists (`a_114_0#` in the lumped one, `a_114_0.t2` in the split one). EMIR matches
  them, so the net keeps its capacitance; a capacitor node it cannot place on any net of
  the network is named (`EMIR-W646`). Labelling every net gives magic one name for it.
- **Diagonal shapes.** magic keeps a non-Manhattan shape as split tiles, and the width
  it gives their resistors is the tiles' extent along an axis, not the width across the
  current: a 45-degree band 0.6 um high is 0.42 um wide, so its current density reads
  29 % low, and its resistance is magic's estimate. `emir extract` names them
  (`EMIR-W647`) and `emir run` lists them under not checked (`diagonal_wires`). A label
  on a diagonal shape may not attach to its net: put labels on Manhattan shapes.
- **On a signal net** (a gate, a clock) part of the current charges the net's own wire
  capacitance, which cc-grid's branches do not carry: on example 08's gate net vb the
  loop read the governing branch's rms 6.55 % low. Confirm a signal net's result that
  matters with rcc.
- `pdks/sky130.yaml` names the terminals of the core MOSFET models (`nfet_`, `pfet_`,
  `special_`, `esd_`). Resistors, MIM capacitors, bipolars and the RF models are named
  `t1, t2 ...` until the person adds them (`EMIR-W643`).
- Only the layers with a limit in the process file are checked. On sky130 that is
  met1 to met5 and their vias: not li1, contacts below it, diffusion, poly or wells.
- The CC netlist holds the lumped capacitance on each net, not the grid's: the
  self-consistent loop scores the branches on the R-only grid (`EMIR-W545`).

## Looking at the results in KLayout

`emir run ... --viewer DIR` writes `DIR/emir.lyrdb`,
a KLayout marker database (with the `klayout` extra; without it, `EMIR-W703`):

```sh
klayout block.gds -m emir_out/viewer/emir.lyrdb
```

The Marker Browser lists categories `EM/<net>/<ratio band>` (FAIL at 1.00x and above,
WARN from the warn fraction) and `IR/<net>/<mV band>`. Each marker is the segment's box
in the DSPF's coordinates (um); its text gives the segment, layer, kind, metric,
ratio, current and the fix. The cell is the DSPF's design name: open the layout at that
cell.

Helpers in [`agent/opensource/`](../agent/opensource/):

```sh
python agent/opensource/dspf_preview_gds.py block.dspf -o emir_out/preview.gds   # no GDS at hand
python agent/opensource/lyrdb_summary.py emir_out/viewer/emir.lyrdb --json        # without a window
QT_QPA_PLATFORM=offscreen klayout -z -r agent/opensource/render_markers.py \
    -rd gds=emir_out/preview.gds -rd lyrdb=emir_out/viewer/emir.lyrdb -rd png=markers.png
agent/opensource/run_xyce.sh tb.cir block.dspf pdk.yaml   # route 1 in one go (run_ngspice.sh too)
```

Checked in KLayout 0.30.2 in batch mode and the 0.30.12 Python package: the databases
load and a headless render puts the markers where the segments are. The interactive
Marker Browser was not exercised (no display on the development machine).
