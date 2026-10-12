# 08 Open layout: EM/IR of a SKY130 layout on the magic route

An open-PDK block from its GDS to EM/IR with open-source tools only. magic extracts
the network, every resistor one drawn segment with its layer, width, length and
place (`emir extract --tool magic`, a magic built with the public `geoout` patch:
[Process files and extraction](../../docs/process-files.md#sky130-magic-extracts-the-dspf) says how to build it). EMIR writes
the DSPF, which is also the RC netlist an rcc simulation runs. Then Xyce or ngspice
simulates it with every branch current saved, and `emir run` checks every metric.

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

When one is missing, the script says which and exits 0. It runs three small
simulations, and magic's extraction took 0.2 s.
`compare.py` checks every result against `expected/results.json` and writes
`out/comparison.md`, the table below. The script exits non-zero on a mismatch.
`EMIR=...` picks another `emir` (the default is the clone's `bin/emir`), and
`EMIR_PYTHON=...` picks the interpreter.

## What it runs

```sh
emir extract --tool magic --gds out/sink.gds --cell sink --process pdks/sky130.yaml --out out/extract
#   out/extract/sink.dspf (the network, and the RC netlist)
# A. the gate pulsed from 0 to 1.0 V, four 4 ns periods, the drain held at 1.8 V
emir simulate out/tb_rcc.cir --sim xyce --dspf out/extract/sink.dspf --out out/sim/rcc.raw
emir run out/extract/sink.dspf --process pdks/sky130.yaml --cc-sim out/sim/rcc.raw --period 4e-9 ...
#   the same without --period: refused (EMIR-E428), no number
#   the same with --period 3.6e-9 (10 % off): refused (EMIR-E429), no number
# B. the DC bias (out at 1.8 V, vb at 1.0 V), every source held at DC over a 4 ns
#    transient, rcc, one net at a time, --period 1e-9
```

The window is the record's last three whole periods (4.5 to 16.5 ns). rcc is exact for
the simulated record over the whole periods you give (--period); whether the testbench
has settled is yours to establish, as for any .meas. Without `--period` the run is refused
with no number (`EMIR-E428`): EMIR never guesses a period. With a period 10 % off, the
record's last two periods of it differ by more than 2 %, so the run is refused too
(`EMIR-E429`). B's record is held at DC: any `--period` with two or more whole ones in
the record gives the same numbers; its runs give `--period 1e-9`, the last three 1 ns of
the 4 ns record.

The extraction has 1135 resistors and none of them is merged. There are 1456
capacitors: magic's lumped netlist counts each one once, and EMIR spreads it over the
nodes of its nets. There are 8 devices, with 32 terminals that carry their terminal
names (`d g s b`, from the process file's `magic: devices:`).

magic's network holds every conductor it extracts. That includes the diffusion and
its contacts, poly, the gate and the p-well under the substrate tap. SKY130's tech LEF
gives none of those an EM limit. Their current is still in the IR drop, and the run
lists them as not checked (`EMIR-W307`), with li1, which has no limit either.

## What you get

Every run analyses EM at 90 C, the temperature SKY130's tech LEF quotes its limits at. Every
number here comes from `out/comparison.md`, with Xyce 7.10 (development build), the
open_pdks 1.0.510 SKY130 models and magic 8.3.515 with the patch.

| run | net | IR drop | worst ratio | fix |
|---|---|---|---|---|
| rcc | out | 6.6910 mV | 1.3566x via2 | via2: 1 -> 2 cuts |
| rcc | vss | 4.8448 mV | 1.3566x via2 | via2: 1 -> 2 cuts |
| rcc, the DC bias | out | 14.4331 mV | 2.9263x via2 | via2: 1 -> 3 cuts |
| rcc, the DC bias | vss | 10.4506 mV | 2.9263x via2 | via2: 1 -> 3 cuts |

With ngspice 45 (`EMIR_EXAMPLE_SIM=ngspice`) rcc gives 6.6920 mV on out and 4.8456 mV
on vss.

**rcc is exact for the simulated record.** `compare.py` reads the simulator's own output without EMIR and reduces
each of the 440 checked branches over the same window. EMIR's avg, rms and peak agree
with that to 7.8e-13. It then runs the same deck once more, printing every node voltage,
and takes each net's static and dynamic IR from them: EMIR's agree to 3.2e-7. The IR is
the simulated circuit's, from the resistances the simulation ran (the DSPF's, at the
process file's 30 C: magic's DSPF states no temperature), while the EM limits are at
90 C (`EMIR-I407`).

## The files

| file | what |
|---|---|
| `make_layouts.py` | draws `sink.gds` |
| `tb_tran.cir.in`, `tb_op.cir.in` | the testbenches (`run.sh` fills in `@PDK_ROOT@` and the netlist, the DSPF) |
| `compare.py` | the checks and `out/comparison.md` |
| `expected/results.json` | the golden results (relative tolerance 5 %; rcc to 1e-6 against the simulator's branch currents, its IR to 1e-5 against its node voltages) |

To see the results on the layout, run `klayout out/sink.gds -m out/rcc_viewer/emir.lyrdb`.
`out/html/emir_chart.png` draws them over the GDS (`emir run --gds`).
