# 12 Self-consistent cc-grid: the droop fed back, on Xyce and ngspice

cc-grid's one approximation is the ideal rail: the CC simulation's devices draw their
current from the nominal supply, not from the drooped one, so the currents and the IR
drop read high by an amount that grows with the droop. `--mode cc-grid
--self-consistent` closes that loop: each instance pin is fed its own solved droop
waveform, in series with the testbench's rail, and the CC testbench is simulated again,
until the droop the currents produce is the droop they were fed. This example runs the open
loop, the self-consistent loop and the RCC reference
on a block small enough to work out by hand, with Xyce or ngspice. Everything here is
**invented** (the block, the loads, the process file's limits). Screening, not sign-off.

The mode is the person's choice: `emir next --dspf biasrail.dspf --process ...
--netlist tb_cc.cir` lists rcc, cc-grid and cc-grid self-consistent with what each
costs and how accurate it is, and plans nothing until one is chosen.

## Run it

```sh
sh examples/12_self_consistent/run.sh                          # Xyce
EMIR_EXAMPLE_SIM=ngspice sh examples/12_self_consistent/run.sh  # ngspice
```

From a clone (`bin/emir`, or `EMIR=...`), seconds. Outputs go to `emir_out/` beside this
file (or the directory given as an argument, `run.sh OUT_DIR`, or
`EMIR_EXAMPLE_OUT=...`). `compare.py` checks the three runs against
`expected/summary.json` and against the hand numbers below (relative tolerance 1e-3);
`run.sh` exits 1 on a mismatch. `tests/test_example_self_consistent.py` runs it with
both simulators.

## The block

`biasrail.dspf`: the 60 um Metal1 rail of example 02 (twelve 1 ohm segments fed at
x = 0, contacts of 12, 12, 12 and 24 ohm to four loads at x = 15, 30, 45 and 60 um), its
loads `XL0..XL3` resistors of 600 ohm to ground (`loads.sp`): a load whose current
follows its supply, as a bias network or a reference ladder does. The rail is 1.2 V.

| file | what |
|---|---|
| `biasrail.dspf` | the RC DSPF with its loads (so also the RC netlist rcc simulates) |
| `loads.sp` | the load: `rload`, a 600 ohm resistor between its pins `p` and `n` |
| `cc.sp`, `tb_cc.cir` | the CC netlist (the loads on an ideal rail) and its testbench |
| `tb_rcc.cir` | the RC testbench: the DSPF itself |
| `compare.py`, `expected/summary.json` | the golden values and the hand numbers |

## What run.sh does

```sh
emir simulate tb_cc.cir --sim xyce --dspf biasrail.dspf --mode cc-grid --probe --out emir_out/sim/cc.raw
emir run biasrail.dspf --process <sample process> --net VDD --cc-sim emir_out/sim/cc.raw --mode cc-grid \
    --vdd 1.2 --period 1e-9 --summary-out emir_out/open.json
emir run biasrail.dspf --process <sample process> --net VDD --mode cc-grid --self-consistent --sim xyce \
    --netlist tb_cc.cir --vdd 1.2 --period 1e-9 --droop-file emir_out/sc/droop.inc --summary-out emir_out/sc.json
emir simulate tb_rcc.cir --sim xyce --dspf biasrail.dspf --mode rcc --out emir_out/sim/rcc.raw
emir run biasrail.dspf --process <sample process> --net VDD --cc-sim emir_out/sim/rcc.raw --mode rcc \
    --period 1e-9 --summary-out emir_out/rcc.json
emir report --from emir_out/sc.json
```

The open-loop run is given the rail's `--vdd`, so it can judge its droop: 108 mV is 9 %
of 1.2 V, above about 2 %, so it warns (`EMIR-W520`) and its `next_steps` plan the
self-consistent route (`needs_human`: it re-simulates, so the person decides).

The self-consistent run reads `tb_cc.cir` and never edits it: it writes a split copy
into `emir_out/sc/` (`split.xyce.cir`, and `cc.split_xyce.sp` in which each load's pin
`p` hangs on a node of its own, fed from the rail through the 0 V probe `VEMIR<k>` that
measures its current) and `droop.inc`, the include it rewrites each pass: the sources
`VEMIRSC<k>` between the rail and each pin's node, each at its pin's droop as an inline
PWL (`DC 0` on pass 0, the open loop). The run reads the testbench's source `VDD`
(1.2 V) and checks `--vdd` against it (`EMIR-E523` when they differ; without `--vdd` it
takes the 1.2 V from there), and checks on every pass that the split pins' currents add
up to that source's (KCL). The testbenches set `.options timeint reltol=1e-4` (ngspice
reads its reltol too): above 1e-4, or unset, the run warns (`EMIR-W540`: the simulators'
default is 1e-3, as large an error as what the loop corrects on a switching block). The
DSPF has capacitance on the rail, so the droop fed back is the RC grid's (an R-only DSPF
would warn, `EMIR-W544`); with DC loads it is the same. For the same reason the run raises
no `EMIR-W545`: the loads' currents are constant over the window, so the grid's capacitors
carry none and the branches scored on the R-only grid are exact (on a switching block
they would miss what the capacitors carry, and the run would say so).

## The numbers by hand

**Open loop.** Each load sees the ideal 1.2 V: 2 mA. The rail's three sections from the
feed carry 8, 6, 4 and 2 mA, so the drop at the far load's pin is 3 ohm x (8 + 6 + 4 +
2) mA + 24 ohm x 2 mA = 108 mV, and the worst ratio, Rm1..Rm3 with the sample's
illustrative 2.4 mA limit, 8 / 2.4 = 3.333x (the one-cut contact Rc3: 2 / 0.75 =
2.667x).

**Closed loop.** A load draws its pin voltage over 600 ohm, so with the 12 or 24 ohm
contact in series each branch from the rail to ground is 612, 612, 612 or 624 ohm. With
v1..v4 the rail voltages at the four taps, 3 ohm between taps and 1.2 V at the feed,
nodal analysis gives

    v1 (1/3 + 1/3 + 1/612) - v2/3              = 1.2/3
    v2 (1/3 + 1/3 + 1/612) - (v1 + v3)/3       = 0
    v3 (1/3 + 1/3 + 1/612) - (v2 + v4)/3       = 0
    v4 (1/3 + 1/624)       - v3/3              = 0

so v = 1.177405, 1.160582, 1.149447, 1.143948 V and the loads draw 1.923864, 1.896375,
1.878182 and 1.833249 mA. Rm1..Rm3 carry their sum, 7.531671 mA (3.13820x); the far
load's pin sits at 600 ohm x 1.833249 mA = 1.099950 V: an IR drop of 100.050 mV, and Rc3
2.44433x. The open loop read the IR 7.9 % high and the worst ratio 6.2 % high.

## What it shows

| run | IR drop | worst ratio (Rm1) | Rc3 | passes |
|---|---|---|---|---|
| cc-grid, open loop | 108.000 mV | 3.333x | 2.667x | |
| cc-grid, self-consistent | 100.050 mV | 3.138x | 2.444x | 3: the residual 108.0, 8.55, 0.000 mV |
| rcc (the reference) | 100.050 mV | 3.138x | 2.444x | |
| by hand, closed loop | 100.050 mV | 3.13820x | 2.44433x | |

(Xyce 7.10 and ngspice 45 on this machine; the currents are DC, so both give the same
numbers.) Pass 1 fed back the open loop's droop (the plain update); pass 2 was fed by the
companion update, which fitted each load's admittance from passes 0 and 1 (1/600 ohm,
exact for a resistor) and so landed on the fixed point: its residual is 0.000 mV, and its
IR is the closed form to 1e-9. For these loads the closed loop is the resistive network
itself, so the self-consistent run lands on rcc. That needs the block's other rail to be
ideal, as it is here (the loads' `n` pins on the testbench's ground), or closed in the
same loop: with VSS extracted too, `--net VDD,VSS` feeds both rails' droop back (Xyce,
ngspice, HSPICE).

**What it does not show.** These loads draw a DC current, so the droop is a constant and
one companion step is exact. A switching CMOS block is where the waveform matters: each
device's spike is degenerated by its own contact, pulled down by its neighbours and fed by
the idle devices beside it. On the synthetic inverter meshes of
[`06_big_block`](../06_big_block/) (`results/selfconsist_waveform.md`) the loop took the
governing branch from +1.0 % / +34 % / +70 % (avg / rms / peak) open loop to +0.01 % /
+0.34 % / +0.78 % of RCC at 4.1 % droop, in 8 simulations. The loop it replaced fed back
each pin's average droop and over-corrected there (`results/selfconsist.csv`: the
average went from +1.34 % to -2.02 % of RCC). See
[Methods and modes](../../docs/method-modes.md#closing-the-loop-self-consistency).
