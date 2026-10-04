# 08 Open layout: EM/IR of a SKY130 layout on the magic route

An open-PDK block from its GDS to EM/IR with open-source tools only. magic extracts
the network, every resistor one drawn segment with its layer, width, length and
place (`emir extract --tool magic`, a magic built with the public `geoout` patch:
[playbook 09](../../agent/playbooks/09-gds-only.md) says how to build it). EMIR writes
the DSPF, which is also the RC netlist an rcc simulation runs, and the CC netlist a
cc-grid simulation runs. Then Xyce or ngspice simulates, and `emir run` checks.

The layout is synthetic: `make_layouts.py` draws it from scratch on SkyWater SKY130's
public GDS layer numbers. It has four two-finger NMOS, 8 fingers of W = 5 um and
L = 0.5 um. The sources go to a `vss` bus over a substrate tap, the drains to an
`out` bus and the gates to `vb`. `out` and `vss` each reach a met3 pad through a
single via2 cut.

Screening, not sign-off.

## Run it

```sh
examples/08_open_layout/run.sh            # results in examples/08_open_layout/out
```

It needs:

- Python with `numpy`, `scipy`, `pyyaml` and `klayout` (`pip install -e '.[klayout]'`
  from the clone root);
- `PDK_ROOT` holding `sky130A`;
- a magic that writes resistor geometry;
- Xyce or ngspice (`EMIR_EXAMPLE_SIM=ngspice` picks ngspice).

When one is missing, the script says which and exits 0. On the development machine
the whole script took about 40 s. That is 16 small simulations of 0.4 s to 1 s
each, 12 of them in the two self-consistent loops, and magic's extraction took 0.2 s.
`compare.py` checks every result against `expected/results.json` and writes
`out/comparison.md`, the table below. The script exits non-zero on a mismatch.
`EMIR=...` picks another `emir` (the default is the clone's `bin/emir`), and
`EMIR_PYTHON=...` picks the interpreter.

## What it runs

```sh
emir extract --tool magic --gds out/sink.gds --cell sink --process pdks/sky130.yaml --out out/extract
#   out/extract/sink.dspf (the network, and the RC netlist), out/extract/sink.cc.spice (the CC netlist)
# A. the gate pulsed from 0 to 1.0 V, three 4 ns periods, the drain held at 1.8 V
emir simulate out/tb_rcc.cir --sim xyce --dspf out/extract/sink.dspf --mode rcc --out out/sim/rcc.raw
emir run out/extract/sink.dspf --process pdks/sky130.yaml --cc-sim out/sim/rcc.raw --mode rcc --period 4e-9 ...
emir simulate out/tb_cc.cir --sim xyce --dspf out/extract/sink.dspf --mode cc-grid --probe --out out/sim/cc.raw
emir run ... --cc-sim out/sim/cc.raw --mode cc-grid --period 4e-9 --vdd out=1.8,vss=0 ...
emir run ... --net out,vss,vb --mode cc-grid --self-consistent --sim xyce --netlist out/tb_cc.cir \
    --vdd out=1.8,vss=0,vb=1.0 --period 4e-9 ...
# B. the operating point (out at 1.8 V, vb at 1.0 V), rcc and cc-grid, one net at a time
```

The extraction has 1135 resistors and none of them is merged. There are 1456
capacitors: magic's lumped netlist counts each one once, and EMIR spreads it over the
nodes of its nets. There are 8 devices, with 32 terminals that carry their terminal
names (`d g s b`, from the process file's `magic: devices:`).

magic's network holds every conductor it extracts. That includes the diffusion and
its contacts, poly, the gate and the p-well under the substrate tap. SKY130's tech LEF
gives none of those an EM limit. Their current is still in the IR drop, and the run
lists them as not checked (`EMIR-W307`), with li1, which has no limit either.

`--vdd vb=1.0` is the gate's high level. The loop uses it only to judge the size of
the droop: it feeds each gate pin its own drop in series with the testbench's pulse.

## What you get

All runs are at 90 C, the temperature SKY130's tech LEF quotes its limits at. Every
number here comes from `out/comparison.md`, with Xyce 7.10, the open_pdks 1.0.510
SKY130 models and magic 8.3.515 with the patch.

| run | net | IR drop | worst ratio | fix |
|---|---|---|---|---|
| rcc (the reference) | out | 6.8744 mV | 1.3566x via2 | via2: 1 -> 2 cuts |
| rcc | vss | 4.9185 mV | 1.3566x via2 | via2: 1 -> 2 cuts |
| cc-grid, open loop | out | 7.3006 mV | 1.4389x | |
| cc-grid, open loop | vss | 5.2834 mV | 1.4400x | |
| cc-grid, self-consistent | out | 6.8620 mV | 1.3534x | via2: 1 -> 2 cuts |
| cc-grid, self-consistent | vss | 4.9630 mV | 1.3535x | via2: 1 -> 2 cuts |

With ngspice 45 (`EMIR_EXAMPLE_SIM=ngspice`, about 55 s) every check holds as well. rcc
gives 6.8754 mV on out and 4.9193 mV on vss, and the loop's governing branches land
within 0.3 % (avg, rms) and 1.7 % (peak) of rcc.

**rcc is exact.** `compare.py` reads the simulator's own output without EMIR and reduces
each of the 440 checked branches over the same window. EMIR's avg, rms and peak agree
with that to 6e-12.

**cc-grid against rcc, branch by branch.** The table gives the relative error (plus
reads high) of every checked branch that carries at least 1 % of its net's largest
current, and the governing branch, the one that carries the most:

| run | net | avg, governing | rms, governing | peak, governing | every branch (avg / peak) | IR |
|---|---|---|---|---|---|---|
| open loop | out | +6.07 % | +5.86 % | +14.79 % | +5.65 .. +7.07 % / +13.76 .. +16.08 % | +6.20 % |
| open loop | vss | +6.14 % | +6.09 % | +29.15 % | +4.19 .. +17.28 % / +2.13 .. +44.09 % | +7.42 % |
| self-consistent | out | -0.24 % | -0.21 % | +1.90 % | -0.54 .. +0.65 % / +1.15 .. +3.11 % | -0.18 % |
| self-consistent | vss | -0.23 % | -0.28 % | -0.56 % | -2.14 .. +9.85 % / -6.30 .. +9.77 % | +0.90 % |

The gate net vb is scored on rms and peak only: a gate's average current is about zero
(its charge comes back), so an error relative to it, and its IR drop, mean nothing.
`compare.py` reports it and does not bound it:

| run | net | rms, governing | peak, governing | every branch (rms / peak) |
|---|---|---|---|---|
| open loop | vb | +1.34 % | +5.31 % | +1.34 .. +2.32 % / +5.29 .. +6.12 % |
| self-consistent | vb | -6.55 % | -4.68 % | -6.55 .. -5.71 % / -4.68 .. -4.03 % |

- The open loop reads high: the CC simulation's devices see ideal rails and an ideal
  gate. It is conservative on every branch here, vb's included.
- The loop reads vb low: rms -6.55 % and peak -4.68 % on its governing branch. Part of
  a signal net's current charges the net's own wire capacitance, and cc-grid's branches,
  scored on the R-only grid, do not carry it (`EMIR-W545`). The open loop's ideal drive
  read vb high here; with the gate's own drop fed back, that margin is gone. On a signal
  net neither is a bound: confirm one that matters with rcc.
- The loop converged in 8 simulations (about 15 s). The governing branches land
  within 0.3 % on avg and rms and within 1.9 % on peak. Every branch of out is within
  0.7 % on avg and rms and within 3.1 % on peak.
- On the measured meshes of `examples/06_big_block`, the method's stated accuracy for
  the governing branch is 0.1 % on avg, 0.7 % on rms and 1.4 % on peak
  ([cc-grid accuracy](../../docs/method-modes.md#accuracy-with-its-sources)). Here the
  governing avg is 0.24 % low and the out rail's peak 1.9 % high: a little outside
  those figures, so both rows are in that table now.
- What remains is on vss's minor branches, -6 % to +10 %. vss holds the substrate (the
  p-well under the tap, the devices' bulk terminals) and the grid's own capacitance,
  which the loop's branches do not carry (`EMIR-W545`, scored on the R-only grid).
- With only out and vss in the loop (`sc_rails`, `--net out,vss`), the governing avg
  and rms are within 0.04 %, but the governing peaks stay at +10.8 % (out) and
  +21.8 % (vss). With vb in the loop as well, its gate network's own
  drop (up to 54 mV) is fed back, and the peaks come to the +1.90 % and -0.56 % above.

## The removed layout command, on the same GDS

Until this release, EMIR's layout command built its own network from the layout's rectangles,
on the process file's layers from li1 up. `expected/emir_layout_record.json` keeps what
it reported on this GDS at the operating point. Its currents came from magic's devices,
simulated by Xyce on ideal rails. Run B gives magic's network the same CC currents
(1.4822 mA through each port):

| net | IR drop, magic's network | IR drop, layout command | worst ratio (via2), both |
|---|---|---|---|
| out | 15.6668 mV | 24.5489 mV | 3.0879x |
| vss | 11.3284 mV | 15.4120 mV | 3.0879x |

The single via2 cut carries the whole current in both networks, so its ratio agrees.
The IR does not: on this GDS the rectangle network read the drop high, by 57 % on out
and 36 % on vss. The two networks differ in more than one way. The rectangle network
modelled the layers from li1 up, grouped each column of contacts into one array, and
fed each device's current into its strap spread over the channel's width. magic's
network has every contact and the diffusion, and each terminal's current enters at its
own node. Which of these makes most of the gap was not measured. Leaving out the
contacts and the diffusion would by itself make the drop read low, so a rectangle
network's error depends on the layout and can go either way. magic's network is the
one magic extracts and the one rcc simulates. With RC currents (`op_rcc`), magic's
network gives 14.83 mV on out and 10.61 mV on vss, and a ratio of 2.93x.

## The files

| file | what |
|---|---|
| `make_layouts.py` | draws `sink.gds` |
| `tb_tran.cir.in`, `tb_op.cir.in` | the testbenches (`run.sh` fills in `@PDK_ROOT@` and the netlist: the DSPF for rcc, the CC netlist for cc-grid) |
| `compare.py` | the checks and `out/comparison.md` |
| `expected/results.json` | the golden results (relative tolerance 5 %; the loop's bounds against rcc) |
| `expected/emir_layout_record.json` | what the removed layout command reported on this GDS |

To see the results on the layout, run `klayout out/sink.gds -m out/rcc_viewer/emir.lyrdb`.
`out/html/emir_chart.png` draws them over the GDS (`emir run --gds`).
