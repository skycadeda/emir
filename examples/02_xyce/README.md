# 02 · The open-simulator flow with Xyce

Screening an extracted rail with **Xyce** supplying the currents, on a block
small enough to work out by hand: **rcc** simulates the RC netlist (the DSPF
itself, with its devices) and reads every resistor's current.

The devices are subcircuits, the way open PDKs ship their transistors (sky130's
ngspice models define `sky130_fd_pr__nfet_01v8` as a `.subckt` with ports
`d g s b`). rcc reads the rail's resistor currents, so it needs no device
current.

```sh
sh examples/02_xyce/run.sh        # about 3 s; outputs in examples/02_xyce/emir_out/
```

You need Xyce on `$PATH` (or `$XYCE_BIN`) and the emir dependencies (numpy,
scipy, PyYAML) for `bin/emir`. Nothing else: no PDK, no licence, no network.
If Xyce is not found, the script stops with exit status 3 (`EMIR-E660`).

## The block (invented, round numbers)

```
 feed                    XMN0          XMN1          XMN2          XMN3
 VDD o--[1]--[1]--[1]--+--[1]--[1]--[1]--+--[1]--[1]--[1]--+--[1]--[1]--[1]--+
      Rm1  Rm2  Rm3    |   Rm4 ...       |   Rm7 ...       |   Rm10 ...      |
                     [12]Rc0           [12]Rc1           [12]Rc2           [24]Rc3
                       |  2 cuts         |  2 cuts         |  2 cuts         |  1 cut
                     drain             drain             drain             drain
```

| file | what it is |
|---|---|
| `loadrail.dspf` | the extraction: a 60 um Metal1 rail (12 segments of 1 ohm, 0.75 um wide), four contacts (12 ohm with 2 cuts, 24 ohm with 1 cut), 1 fF per rail node, and the four devices on their instance-pin nodes. An RC DSPF with its devices: the RC netlist. |
| `models.sp` | a level-1 NMOS with `lambda = 0` and no capacitance, wrapped in a subcircuit `nsink` |
| `tb_rcc.cir` | the testbench: 1.8 V supply, a gate pulse per device, a `.tran` over four periods |
| `expected/summary.json` | the golden results, with their tolerance (`tests/test_examples_opensim.py` compares them) |

The process file is the bundled **illustrative** one (`emir/data/sample/process.yaml`):
its limits are invented, the right shape for the tool and the wrong values for
any silicon.

## What run.sh does

```sh
emir simulate tb_rcc.cir --sim xyce --dspf loadrail.dspf --out emir_out/sim/rcc.raw
emir run loadrail.dspf --process <sample process> --cc-sim emir_out/sim/rcc.raw \
    --period 1e-8 --em-temp 100 --summary-out emir_out/rcc.json
emir report --from emir_out/rcc.json
```

`emir simulate` writes the deck it runs beside the result (`rcc.xyce.cir`): the
testbench with a `.print tran format=raw file=rcc.raw I(XI0:Rm1) ...` line, and
a copy of the DSPF without its `$` annotations, which Xyce would reject as
unknown parameters. It runs `Xyce -a` (ASCII output), then writes
`rcc.raw.names`, the sidecar that maps Xyce's names (`I(XI0:RM1)`) to the
DSPF's (`Rm1:1`). `--em-temp 100` analyses at the DSPF's own temperature, so the
resistances are the DSPF's and the IR below needs no temperature correction.

## The numbers, by hand

**Currents.** In saturation a level-1 NMOS with `lambda = 0` draws
`I = (kp/2)(W/L)(Vgs - vto)^2 = 100 uA/V^2 x W/L x (0.5 V)^2 = 25 uA x W/L` at
`Vgs = 1 V`, whatever its drain voltage. That makes the result independent of the rail's droop.
W/L = 80, 100, 120, 100 gives
**I_on = 2.0, 2.5, 3.0, 2.5 mA**.

**Waveforms.** Each gate pulses 0 to 1 V with 0.1 ns ramps, 2 ns flat, every
10 ns, the four 2.5 ns apart. A sink conducts while its gate is above
`vto = 0.5 V`. On a ramp that is the second half, where
`I = 4 I_on (t/tr - 1/2)^2`, which integrates to `I_on tr / 6` per ramp. So per period:

* average: `I_on (2 + 2 x 0.1/6) / 10 = 0.20333 I_on`;
* rms: `I_on sqrt(16 (0.0625 x 2 + 2 x 0.1/160) / 10) = 0.44944 I_on`;
* peak: `I_on`.

The pulses never overlap (each lasts 2.2 ns, they start 2.5 ns apart). So a
rail segment's average is the sum of its sinks' averages, its rms^2 the sum
of their rms^2, and its peak the largest `I_on` it carries.

**Limits** (the illustrative process): Metal1 3.2 / 12 / 16 mA per um (avg /
rms with `sqrt(28.8 x 5)` / peak), so 2.4 / 9 / 12 mA at 0.75 um; Cont
0.75 / 1.5 / 2.4 mA per cut.

| item | current | limit | ratio by hand | measured (rcc) |
|---|---|---|---|---|
| Rm1 avg (the feed segment: all four sinks) | 0.20333 x 10 = 2.0333 mA | 2.4 mA | 0.84722x (WARN) | 0.84746x |
| Rm1 rms | 0.44944 x sqrt(25.5) = 2.2696 mA | 9 mA | 0.25218x | 0.25223x |
| Rc3 peak (the 1-cut contact of the 2.5 mA sink) | 2.5 mA | 2.4 mA | **1.04167x (FAIL)** | 1.04167x |
| IR at XMN3's drain (average currents) | 0.20333 x (3 x (10 + 8 + 5.5 + 2.5) + 24 x 2.5) | | 28.060 mV | 28.068 mV |
| dynamic IR: XMN3 alone, 2.5 mA x (12 x 1 + 24) ohm | | | 90.000 mV | 90.000 mV |

The measured values are from `expected/summary.json` and
`scripts/opensim_proof.py` (Xyce 7.10, development build). The averages read
0.03 % high and the rms 0.02 %. That is the trapezoid rule on the 0.1 ns
ramps: sampled every 10 ps, it overestimates a quadratic by `K h^2 / (12 tr)`
per ramp, about 3e-4 of the average here. The peak, which has no ramp in it,
is exact.

rcc's verdict is **FAIL on Rc3's peak**: the far device's contact has one cut
where the peak current needs two. The report says so (`Cont: 1 -> 2 cuts`).
The feed segment's average is inside the warn margin (0.80 to 1.00).

## Changing it for your block

* Put your testbench in place of `tb_rcc.cir`: supplies, stimulus, models, a
  `.tran` over whole periods of the block's activity, and the extracted
  netlist (`.include block.dspf`, instantiated once).
* `emir next --dspf block.dspf --process pdk.yaml --netlist tb.cir --period <T>`
  plans the same commands, each one runnable as printed (without the block's
  period it asks for it: the run would be refused, EMIR-E428).

Screening, not sign-off.
