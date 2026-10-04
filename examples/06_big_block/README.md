# 06 Big block: CC simulation + R-only extraction at scale

An ADC top or a PLL top is too big to simulate RC-extracted with every branch current
saved (RCC). This example shows the route EMIR uses instead, and measures it:

1. **Streamed superposition == dense** (no simulator): a synthetic mesh with periodic
   tap currents, screened with the exact superposition built dense and streamed in time
   chunks. The two agree branch by branch to rounding, and the 92 k-node, 181 k-resistor,
   2 000-tap case the dense path could not hold now runs in bounded memory.
2. **RCC against CC + cc-grid on Xyce** (when Xyce is on PATH): an invented N x N metal
   mesh with level-1 CMOS inverters, simulated both ways from one description, over a
   size sweep. Time, peak memory, result size, and the accuracy of cc-grid against RCC
   branch by branch (avg / rms / peak by branch class, IR, dynamic IR).
3. **The self-consistent loop against RCC on Xyce**: small meshes (N <= 20, and one
   N = 40 cost point), the loop of `emir run --mode cc-grid --self-consistent --sim xyce`
   run pass by pass on the plain CC testbench (`cc_tb.cir`), each pass scored against
   RCC: what feeding each pin's droop waveform back fixes, and what it leaves.
4. **The mesh screened** (when Xyce is on PATH): the N = 10 mesh through the same loop,
   with invented limits that make a few straps fail: the verdict, the failures and their
   fixes, as a person gets them.

Everything here is **synthetic** (an invented mesh, invented devices, an invented process
file) and the simulator is **Xyce, serial**: an illustration of how the two routes
scale, not a statement about Spectre or a real design. Screening, not sign-off.

## Run it

```
./run.sh              # seconds: part 1, and parts 2 to 4 at N = 10 if Xyce is found
./run.sh --big        # + the 92 k-node case (a few minutes)
./run.sh --sweep      # + the full sweep behind results/ (about an hour)
./run.sh --selfconsist   # + the self-consistent loop on every case (a few minutes)
```

From a clone, with numpy, scipy and PyYAML installed (`PYTHON=... ./run.sh` picks the
interpreter; emir is found through `PYTHONPATH` when it is not installed). Output goes
to `out/` (or the directory given as an argument, `./run.sh OUT_DIR`, or `OUT=...`). Each part is compared with its golden file in `expected/`
(`compare.py`, tolerances inside the file); `run.sh` exits non-zero on a mismatch.

## Files

| file | what |
|---|---|
| `mesh.py` | the generators: `xyce_case` (R-only and RC DSPFs, the RCC and CC Xyce decks, the `.names` sidecars) and `stream_case` (a mesh and a nutascii result of synthetic currents) |
| `process.yaml` | invented process data (round numbers, synthetic GDS layers) |
| `process_screen.yaml` | part 4's invented limits: `process.yaml`'s Metal1 limits, the contact limits doubled, so that at N = 10 a few straps fail (with `process.yaml` every contact fails its peak limit) |
| `stream_check.py` | part 1: in-process per-branch comparison and three timed `emir run`s (dense, stream, auto) |
| `sweep.py` | part 2: the Xyce sweep; writes the CSV, the Markdown tables and a JSON |
| `selfconsist.py` | part 3: the waveform self-consistent loop, every pass scored against RCC (and, with `--nocap`, against RCC without the grid's capacitors); writes the CSV, the Markdown table and a JSON. `--guard` runs a case with the companion update off: the divergence guard's test |
| `solve_blocks.py` | the solver measurement behind the streamed path's block and chunk sizes |
| `measure.py` | wall time and peak memory of a command, single-threaded, in a clean child |
| `compare.py` | checks a result JSON against a golden file |
| `expected/` | golden files: `stream_small.json`, `xyce_n10.json`, `selfconsist_n10.json`, `screen_n10.json` |
| `results/` | the committed tables: `sweep.csv`, `sweep.md`, `stream_bench.csv`, `solve_blocks.csv`, `selfconsist_waveform.csv`, `selfconsist_waveform.md` (the waveform loop), and `selfconsist.csv`, `selfconsist.md`: the record of the average-droop loop it replaced |

## The CC route, as the example runs it

```
python mesh.py xyce --n 40 --k 32 --out out/n40          # writes the decks and DSPFs
cd out/n40 && Xyce -a cc.cir                             # devices on an ideal rail
emir run grid_r.dspf --process ../../process.yaml --cc-sim cc.raw \
    --mode cc-grid --period 1e-9 --summary-out cc.json --format json
emir run grid_rc.dspf --process ../../process.yaml --cc-sim cc.raw \
    --mode cc-grid --period 1e-9 --dynamic --format json      # + dynamic IR
```

`cc.raw.names` maps Xyce's `I(VA0)` onto the DSPF's `MP0:s` (`emir schema names`). The
RCC reference is `Xyce -a rcc.cir` (every branch current printed) read with
`--mode rcc`. The mode is the person's choice; `emir next` recommends cc-grid at these
sizes. The self-consistent route on the plain CC testbench (`cc_tb.cir`, the devices
straight on the rail: EMIR writes the per-pin split itself):

```
emir run grid_r.dspf --process ../../process.yaml --net VDD --mode cc-grid --self-consistent \
    --sim xyce --netlist cc_tb.cir --vdd 1.0 --period 1e-9 --summary-out sc.json --format json
```

With `mesh.xyce_case(..., vss=True)` the mesh has a VSS twin (the NMOS sources on it
through their own contacts) and both rails close in one loop: `--net VDD,VSS` (no
`--vdd`: each rail is read from the testbench, VSUP at 1 V and VGND at 0 V).

## Part 4: the mesh screened, with invented limits

Parts 2 and 3 measure the method against RCC; part 4 shows what a person gets from it.
The N = 10 mesh is screened through the same loop with `process_screen.yaml`, whose
invented limits make a few straps fail, and its golden file holds the verdict, the
failures and their fixes (`run.sh` step 4; the HTML report and the EM/IR chart land in
`out/screen/html/`):

```
python mesh.py xyce --n 10 --k 32 --out out/screen && cd out/screen
emir run grid_r.dspf --process ../../process_screen.yaml --net VDD --mode cc-grid \
    --self-consistent --sim xyce --netlist cc_tb.cir --vdd 1.0 --period 1e-9 \
    --summary-out screen.json --html html
```

Measured with Xyce 7.10: the loop converged after 4 simulations; FAIL, on four Metal1
straps over their average limit of 1 mA/um. The two that leave the feed carry 3.655 mA
(up) and 3.509 mA (right), which with the corner inverter's 0.231 mA are the supply's
7.395 mA, and read 1.83x and 1.75x (fixes: widen 2.000 -> 3.655 um and -> 3.510 um); the
next two read 1.07x and 1.03x; every contact passes (worst 0.727x, its peak); the IR drop
is 3.82 mV. The README banner and the blog's cover and first two carousel pages are drawn
from this run (`scripts/make_figures.py`).

## Results

### Streamed against dense superposition (`results/stream_bench.csv`)

Measured on the synthetic meshes of `examples/06_big_block` (`stream_check.py`: emir single-threaded, the faster of two runs, on a shared machine whose load average is the last column). "dense" ran with a 64G budget so that it never streams; "auto" with the default 2G. The matrix size counts the taps plus the feed.

| case | nodes | resistors | taps | samples | dense matrix | dense s | dense peak MiB | streamed s | streamed peak MiB | auto picks | auto s | auto peak MiB | max difference | load |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| s40_k100_t301 | 1 701 | 3 221 | 100 | 301 | 2.5 MiB | 0.4 | 85 | 0.4 | 73 | dense | 0.4 | 85 | 1.5e-15 | 10 |
| s150_k200_t2001 | 22 701 | 44 901 | 200 | 2 001 | 69 MiB | 4.4 | 1622 | 5.2 | 241 | dense | 4.3 | 1241 | 4.8e-15 | 10 |
| s150_k1000_t2001 | 23 501 | 45 701 | 1 000 | 2 001 | 349 MiB | 12.4 | 2018 | 7.8 | 424 | stream | 7.7 | 425 | 4.0e-15 | 15 |
| s150_k1000_t1001 | 23 501 | 45 701 | 1 000 | 1 001 | 349 MiB | 10.5 | 1271 | 4.6 | 282 | stream | 4.4 | 280 | 4.6e-15 | 19 |
| s300_k2000_t501 | 92 001 | 181 401 | 2 000 | 501 | 2.70 GiB | 61.2 | 4593 | 12.8 | 806 | stream | 11.7 | 804 | 6.9e-15 | 26 |

The last row is the case the dense path refused under its old fixed 2 GiB cap: `auto` now streams it in 12 s at 0.8 GiB peak, where the dense path, given 64G of budget to prove the point, took 61 s and 4.5 GiB. "max difference" is the largest per-branch difference between the dense and the streamed currents over avg, rms and peak, relative to the largest branch value.

### RCC against CC + cc-grid: runtime, memory, result size (`results/sweep.csv`, `results/sweep.md`)

Xyce, synthetic, serial. "RCC route" is the RCC simulation plus `emir run --mode rcc` (read up to N = 70); beyond that the ratio is the RCC simulation alone against the whole CC route.

| case | N | nodes | resistors | RCC sim s | steps (failed) | RCC result MiB | RCC sim peak MiB | CC sim s | CC result MiB | cc-grid s | cc-grid peak MiB | cc-grid --dynamic s | RCC route / CC route | load |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| k32 | 10 | 132 | 212 | 0.644 | 2870 (254) | 9.52 | 52.1 | 0.286 | 1.41 | 0.424 | 70.8 | 0.845 | 2.0x | 22 |
| k32 | 20 | 432 | 792 | 1.95 | 2169 (187) | 26.8 | 54.8 | 0.299 | 1.41 | 0.471 | 90.6 | 1.69 | 4.7x | 22 |
| k32 | 40 | 1 632 | 3 152 | 7.45 | 1942 (156) | 95.3 | 70.2 | 0.257 | 1.41 | 0.548 | 171 | 2.49 | 16.9x | 10 |
| k32 | 70 | 4 932 | 9 692 | 30.1 | 1631 (112) | 246 | 114 | 0.286 | 1.41 | 1.08 | 394 | 11.2 | 32.4x | 29 |
| k32 | 100 | 10 032 | 19 832 | 88.2 | 1632 (96) | 503 | 174 | 0.399 | 1.41 | 2.07 | 739 | 44.7 | 35.7x (sim only) | 34 |
| k32 | 140 | 19 632 | 38 952 | 176 | 1308 (121) | 792 | 298 | 0.343 | 1.41 | 3.38 | 1 166 | 76.9 | 47.4x (sim only) | 21 |
| k32 | 200 | 40 032 | 79 632 | 350 | 1049 (95) | 1 298 | 562 | 0.368 | 1.41 | 6.59 | 1 756 | 150 | 50.3x (sim only) | 20 |
| stiff | 40 | 1 632 | 3 152 | 7.07 | 1796 (223) | 88.1 | 70.4 | 0.247 | 1.41 | 0.506 | 171 | 1.56 | 16.2x | 10 |
| k256 | 70 | 5 156 | 9 916 | 59.6 | 3108 (138) | 476 | 118 | 1.98 | 11.2 | 1.7 | 468 | 9.02 | 23.1x | 12 |
| contact0 | 40 | 1 632 | 3 152 | 8.8 | 1872 (174) | 91.9 | 70.2 | 0.31 | 1.41 | 0.613 | 171 | 3.21 | 16.4x | 31 |
| nooverlap | 40 | 1 632 | 3 152 | 7.73 | 1815 (189) | 88.1 | 70.4 | 0.359 | 1.31 | 0.646 | 166 | 3.13 | 14.4x | 22 |

### Accuracy of cc-grid against RCC (plus is conservative)

Branch classes by the RCC value of that metric: major >= 50 % of the largest branch, secondary 10-50 %, minor 1-10 %; each cell is the range over the branches of the class. `contact0` (1 mohm device contacts) and `nooverlap` (no gate-overlap capacitance) probe the small negative average error.

| case | N | avg, governing | avg, all branches | rms, governing | rms, all branches | peak, governing | peak, major | peak, secondary | peak, minor | IR | dynamic IR |
|---|---|---|---|---|---|---|---|---|---|---|---|
| k32 | 10 | -0.12 % | -0.16 % .. -0.02 % | +6.83 % | +4.38 % .. +7.02 % | +15.28 % | +10.45 % .. +16.48 % | +9.63 % .. +19.80 % | +9.53 % .. +19.18 % | -0.13 % | +14.49 % |
| k32 | 20 | -0.12 % | -0.21 % .. -0.07 % | +7.68 % | +4.82 % .. +8.08 % | +16.59 % | +11.41 % .. +17.43 % | +8.55 % .. +24.01 % | +5.71 % .. +23.90 % | -0.13 % | +16.49 % |
| k32 | 40 | -0.13 % | -0.21 % .. -0.07 % | +9.21 % | +5.31 % .. +9.40 % | +18.11 % | +12.25 % .. +19.00 % | -5.96 % .. +28.38 % | -2.53 % .. +33.17 % | -0.14 % | +16.66 % |
| k32 | 70 | -0.15 % | -0.20 % .. -0.06 % | +13.68 % | +4.13 % .. +13.80 % | +24.29 % | +12.27 % .. +25.04 % | -17.15 % .. +32.50 % | -18.95 % .. +37.75 % | -0.15 % | +16.24 % |
| stiff | 40 | -0.33 % | -0.40 % .. -0.26 % | +4.60 % | +4.44 % .. +4.61 % | +10.13 % | +10.09 % .. +10.52 % | +10.06 % .. +11.43 % | +9.61 % .. +11.41 % | -0.35 % | +10.45 % |
| k256 | 70 | +1.34 % | +1.30 % .. +1.45 % | +38.21 % | +7.60 % .. +39.22 % | +79.90 % | +79.88 % .. +80.55 % | +14.65 % .. +83.58 % | -18.18 % .. +91.90 % | +1.36 % | +65.89 % |
| contact0 | 40 | -0.02 % | -0.09 % .. +0.05 % | +5.11 % | +0.97 % .. +5.31 % | +8.34 % | +1.88 % .. +9.34 % | -14.80 % .. +17.79 % | -12.05 % .. +22.39 % | -0.02 % | +9.27 % |
| nooverlap | 40 | -0.02 % | -0.15 % .. +0.10 % | +9.14 % | +5.22 % .. +9.34 % | +18.03 % | +11.66 % .. +19.22 % | +5.62 % .. +20.75 % | -6.83 % .. +20.62 % | -0.02 % | +16.40 % |

### The waveform self-consistent loop against RCC (`results/selfconsist_waveform.csv`, `.md`)

`selfconsist.py`: Xyce, synthetic, serial. Each case is a mesh of `mesh.xyce_case` with
level-1 inverters switching in eight phases (four in the `p4` cases) on a 1 V rail, 2 fF
of grid capacitance per node (20 fF in the `c20` cases), 4 ns (40 ns in `k32_n10_long`);
the loop is the one `emir run --mode cc-grid --self-consistent --sim xyce` runs on the
plain CC testbench (`cc_tb.cir`, ngspice in `k32_n10_ng`) and the R-only DSPF (the RC
one in `hd16b_c20rc`), with its defaults (stop at a residual under max(1 % of the droop,
0.2 mV) with the governing branch moving less than 0.5 %, or settled: the governing
branch, every reported item and every device still on two passes, the residual under
5 % of the droop and not growing; at most 10 simulations). The reported pass against
RCC, governing branch and the minor branches (1-10 % of the largest), and against RCC
without the grid's capacitors (`--nocap`):

| case | nodes | droop | open loop avg / rms / peak | simulations | reported avg / rms / peak | minor peak | each device's peak | without grid capacitors: rms / peak, minor peak |
|---|---|---|---|---|---|---|---|---|
| k32_n10 | 132 | 0.38 % | -0.12 / +6.83 / +15.28 % | 4 | -0.02 / +0.02 / +0.01 % | -0.77 .. +0.63 % | +0.01 .. +0.05 % | +0.01 / +0.00, +0.01 .. +0.14 % |
| k32_n20 | 432 | 0.47 % | -0.12 / +7.68 / +16.59 % | 5 | -0.03 / +0.07 / +0.08 % | -2.34 .. +2.94 % | -0.08 .. +0.02 % | +0.01 / +0.02, -0.07 .. +0.17 % |
| p4_n20 (four phases) | 432 | 0.47 % | -0.14 / +8.47 / +19.28 % | 4 | -0.02 / +0.07 / +0.07 % | -4.63 .. +3.53 % | -0.19 .. +0.00 % | +0.01 / +0.03, -0.17 .. +0.14 % |
| c0_n20 (no contacts) | 432 | 0.35 % | -0.02 / +3.46 / +6.76 % | 4 | -0.04 / +0.09 / +0.13 % | -2.46 .. +3.06 % | -0.11 .. -0.02 % | +0.02 / +0.03, -0.06 .. +0.21 % |
| hd16b (128 inverters) | 384 | 4.14 % | +1.01 / +34.16 / +69.78 % | 8 (settled) | +0.01 / +0.34 / +0.78 % | -2.98 .. +2.19 % | -0.58 .. -0.11 % | +0.09 / +0.38, -0.45 .. +0.61 % |
| hd16b_p4 (four phases) | 384 | 4.19 % | -0.17 / +42.00 / +92.49 % | 9 (settled) | -0.01 / +0.37 / +0.60 % | -3.71 .. +5.05 % | -1.28 .. -0.25 % | +0.10 / +0.33, -0.38 .. +2.17 % |
| hd16x (1.5 ohm segments) | 384 | 7.94 % | +3.80 / +50.46 / +113.85 % | 10 (settled, the last allowed) | +0.06 / +0.36 / +1.31 % | -4.10 .. +3.03 % | -0.93 .. -0.17 % | +0.10 / +0.75, -0.71 .. +1.43 % |
| k32_n40 | 1 632 | 0.55 % | -0.13 / +9.21 / +18.11 % | 5 | -0.04 / +0.67 / +0.67 % | -10.55 .. +11.29 % | -0.55 .. -0.17 % | -0.01 / +0.12, -0.16 .. +0.19 % |
| one inverter, no mesh | 2 | 0.12 % | -0.13 / +4.39 / +10.45 % | 4 | -0.01 / +0.01 / +0.03 % | | +0.03 % | |
| null_n20 (stiff mesh) | 432 | 0.00 % | +0.03 / +0.07 / +0.13 % | 1 | +0.06 / +0.08 / +0.13 % (pass 0: self-consistent already) | -0.04 .. +0.17 % | +0.02 .. +0.06 % | +0.08 / +0.10, -0.01 .. +0.14 % |
| vss_n10, VDD | 264 | 0.38 % | -0.22 / +6.76 / +15.28 % | 4 | -0.03 / +0.03 / +0.01 % | -0.77 .. +0.60 % | +0.03 .. +0.06 % | +0.02 / -0.01, -0.02 .. +0.15 % |
| vss_n10, VSS (window from 0) | 264 | 0.38 % | -0.14 / +8.07 / +17.09 % | 4 | -0.03 / +0.07 / +0.45 % | -0.81 .. +0.45 % | -0.01 .. +0.03 % | +0.06 / -0.04, -0.04 .. +0.16 % |
| hd16b_c20 (20 fF a node, R-only DSPF) | 384 | 4.13 % | +1.25 / +37.41 / +76.76 % | 8 (settled) | +0.25 / +2.77 / +4.93 % | -19.86 .. +10.94 % | -4.78 .. -1.12 % | +0.09 / +0.38, -0.45 .. +0.61 % |
| hd16b_c20rc (the same, RC DSPF) | 384 | 4.13 % | +1.25 / +37.41 / +76.76 % | 6 | -0.01 / +1.98 / +3.88 % | -19.52 .. +25.30 % | -0.08 .. +0.01 % | -0.67 / -0.62, -7.55 .. +15.70 % |
| k32_n10_ng (ngspice) | 132 | 0.38 % | -0.12 / +6.83 / +15.28 % | 5 | -0.04 / +0.01 / +0.01 % | -0.78 .. +0.56 % | +0.01 .. +0.04 % | -0.00 / +0.00, +0.01 .. +0.11 % |
| k32_n10_long (40 ns) | 132 | 0.38 % | -0.13 / +6.83 / +15.27 % | 5 (settled) | -0.02 / +0.02 / +0.06 % | -0.76 .. +0.70 % | +0.07 .. +0.08 % | +0.01 / +0.05, +0.02 .. +0.20 % |

(`hd16b_c20*` are scored against RCC with its 20 fF a node. `hd16b_c20` runs the same
loop as `hd16b`, on the same R-only DSPF: only RCC differs. Without the grid's capacitors
RCC is the same reference for all three, and the RC loop reads below it there: its
droop holds the capacitance that reference lacks. The table scores the branches on the
R-only grid, as a run without `--dynamic` does; `hd16b_c20rc` is also scored through the
RC grid, as `--dynamic --em-basis decap-aware` scores it (the `*_rcs` columns): its
reported pass's governing branch -0.01 / +0.12 / +0.25 %, its minor branches -0.51 ..
+0.52 %. The default basis with `--dynamic`, worst, takes per segment the larger of the
two, so it keeps the R-only grid's readings where they are higher: governing +1.98 % rms
and +3.88 % peak, minor branches up to +25.3 %.)

What it shows, on these meshes:

- **The open loop's rms and peak error is the device currents', and the loop removes it.**
  The ideal rail makes each spike too high and too narrow: the device's own contact
  degenerates its source (`one`: +4.39 % rms, +10.45 % peak with no mesh at all),
  neighbours switching in the same phase pull the shared mesh down (more of them in four
  phases: +19.3 % and +92.5 % peak), and idle inverters (each a 200 fF load behind its ON
  PMOS) give charge back. All three need the pin to move in time, so the loop feeds each
  pin its droop waveform; each device's own peak current then lands within 1.3 % of RCC
  with 2 fF a node, or fed back from an RC DSPF (reading low at high droop: median -0.5 %
  to -1.1 % at 4 %).
- **What remains is the grid's own capacitance**, which these R-only DSPFs do not have. In
  the branch currents (scored on the R-only grid) it grows with the grid: minor-branch
  peaks within 0.8 % on 132 nodes, 3.1 % on 432 (4.7 % in four phases) and 11.3 % on 1 632
  (rms +0.67 % on the governing branch there), while against RCC without the grid's
  capacitors the same results are within 0.3 % (2.2 % at 4 % droop). In the droop fed
  back it grows with the capacitance: with ten times as much (`hd16b_c20`) the devices'
  peaks read 1.1 % to 4.8 % low and minor branches down to -20 % (EMIR-W544 says so on
  every R-only run); the RC DSPF's droop fed back (`hd16b_c20rc`) takes the devices back
  to within 0.1 %. Its branches, scored on the R-only grid, still miss the current the
  grid's capacitors carry (minor branches -19.5 % to +25.3 %, lower than the open loop's
  -7.1 %, whose high device currents hid it: `EMIR-W545` says so); scored through the RC
  grid, as `--dynamic --em-basis decap-aware` scores them, they are within 0.52 %. The
  answer is accurate rather than conservative: a minor branch or a device can read a few
  percent low.
- **Where the loop gain is above 1 the companion update is needed.** At 4.1 % droop the
  plain update (each pass feeding back the droop the last one produced) diverged:
  `hd16b_plain`'s residual went 173, 152, 279, 1 772 mV (it was fed 150, 152 and 239 mV:
  the first update scaled to 15 % of the supply, none above half of it) and the guard
  stopped it and reported pass 0 (`EMIR-W406`). The companion update, which fits each
  pin's device admittance from the two passes before, settled in 8 simulations; its
  residual levels off at about 1.3 times its tolerance there while the answer no longer
  moves, which is the rule it stopped by.
- **Beyond it** (`hd16x`, 7.9 % droop): settled on its tenth simulation, the last one the
  default allows; the summary says so (`at_pass_limit`) and its next steps offer more
  passes.
- **VDD and VSS close together** (`vss_n10`): both rails' droop fed back on every pass,
  4 simulations, the ground bounce corrected like the supply droop. (The run chose 0 to
  4 ns as VSS's window, start-up included, and 1 to 4 ns as VDD's.)
- **ngspice** (`k32_n10_ng`): the same answer as Xyce's on the same mesh, in 5
  simulations.
- **A long transient** (`k32_n10_long`, 40 ns, 39 periods in the window): 7 400 PWL points
  a pin (more than 1 500: the cap grows with the run, at 0.3 x pass 0's time points;
  `EMIR-W542` as it coarsened the PWL to 0.67 x the loop's tolerance), and as accurate as
  4 ns.
- **Pass 0 is the open loop**: the split testbench at 0 V reproduces `cc.cir` (both on
  Xyce at reltol 1e-4) within 0.05 points on VDD and 0.07 on VSS on the governing branch
  and the IR, 0.09 on every branch (two decks, the simulator's step control).
- **KCL closes on every pass** (the split pins against the rail source: within 3.5e-9 of
  the average).
- **The testbench's tolerance matters as much as the loop** (`k32_n20_reltol3`: every deck
  at Xyce's default reltol 1e-3 instead of 1e-4): the open loop's governing avg, rms and
  peak moved 0.29 to 0.41 points and the converged loop's 0.09 to 0.45 (+0.06 / +0.53 /
  +0.48 % against RCC at 1e-3), so the run warns above 1e-4 (`EMIR-W540`).

**Cost.** Each pass simulates the CC testbench once with every PWL point as a breakpoint:
2.64 to 3.29 times the open loop's time points on one net on the 32-inverter meshes on Xyce
at reltol 1e-4 (1.7 to 3.8 over every one-net case at reltol 1e-4 with the companion
update, up to 6.1 with the plain one, which diverged; 4.3 to 4.9 at 1e-3; 394
to 1 148 PWL points per pin over 4 ns on the meshes), 3.9 to 4.2 times with both rails
(about 1 154 to 1 261 points), and a pass's simulation took 2.7 to 4.2 times the open
loop's on one net of the meshes at reltol 1e-4 (5.1 to 5.5 with both rails); over 40 ns,
7 400 points, 2.4 to 2.5 times the time points and
about 9.5 times the simulation time (20 s against 2.1 s). The whole loop took 2.1 s to
8.1 s with 32 inverters and 19 s to 35 s at 4.1 % to 7.9 % droop, where the RCC
simulation alone took 0.5 s to 5.3 s and 1.3 s to 1.9 s (a shared machine at a load of
about 3, `hd16b_c20rc` re-measured at 0.3; read ratios). On grids this small the loop costs more than RCC; the grid side
(the solve and the companion's backward-Euler steps) took 0.1 s to 0.9 s a pass on 132
to 432 nodes and 0.6 s to 1.0 s on 1 632. That the loop pays off on a big block is an
extrapolation, not a measurement here: the CC simulation does not grow with the grid
(part 2), RCC does, and the grid side does, at one back-substitution per time step.

### The average-droop loop it replaced (`results/selfconsist.csv`, `results/selfconsist.md`)

The loop's first version fed each pin its *average* droop. These are its measurements,
kept as they were taken (by an earlier version of this script, not in this repository;
the k256 case is 256 inverters at N = 70, 4.1 % droop), the governing branch against
RCC, open loop -> settled:

| case | N | droop | passes to settle | avg | rms | peak | IR | settled loop s | RCC sim s |
|---|---|---|---|---|---|---|---|---|---|
| k32 | 10 | 0.38 % | 1 (2 simulations) | -0.12 % -> -0.40 % | +6.83 % -> +6.13 % | +15.28 % -> +14.11 % | -0.13 % -> -0.41 % | 0.90 | 0.68 |
| k32 | 20 | 0.47 % | 1 | -0.12 % -> -0.47 % | +7.68 % -> +6.85 % | +16.59 % -> +15.17 % | -0.13 % -> -0.49 % | 0.97 | 1.53 |
| k32 | 40 | 0.55 % | 1 | -0.13 % -> -0.56 % | +9.21 % -> +8.18 % | +18.11 % -> +16.36 % | -0.14 % -> -0.57 % | 1.21 | 7.03 |
| k32 | 70 | 0.62 % | 1 | -0.15 % -> -0.64 % | +13.68 % -> +12.51 % | +24.29 % -> +22.17 % | -0.15 % -> -0.65 % | 1.70 | 26.2 |
| k256 | 70 | 4.14 % | 2 (3 simulations) | +1.34 % -> -2.02 % | +38.21 % -> +28.50 % | +79.90 % -> +58.57 % | +1.36 % -> -2.05 % | 10.0 | 56.6 |
| stiff | 40 | 0.12 % | 1 | -0.33 % -> -0.41 % | +4.60 % -> +4.37 % | +10.13 % -> +9.70 % | -0.35 % -> -0.41 % | 1.31 | 6.55 |
| contact0 | 40 | 0.44 % | 1 | -0.02 % -> -0.34 % | +5.11 % -> +4.37 % | +8.34 % -> +7.14 % | -0.02 % -> -0.35 % | 1.14 | 7.34 |
| nooverlap | 40 | 0.48 % | 1 | -0.02 % -> -0.42 % | +9.14 % -> +8.28 % | +18.03 % -> +16.51 % | -0.02 % -> -0.44 % | 1.00 | 5.93 |

It over-corrected: a device's average current falls about 0.89 % per 1 % of DC supply drop
(measured while the waveform loop was developed; that probe is not committed), but what a
switching inverter's load holds each cycle is the rail at the moment its PMOS turns off,
not the average rail; so avg and IR went below RCC (no longer conservative), and rms and
peak kept about three quarters of their open-loop error. The waveform loop replaced it.

### SuperLU in blocks (`results/solve_blocks.csv`)

`solve_blocks.py` times SciPy's SuperLU back-substitution on the 92 k-node grid with 1 to 64 right-hand sides at once, and the whole streamed superposition with chunks of 8 to 512 samples, with one BLAS thread and with the library's default. It is why the streamed path solves in blocks of 8 columns and at most 64 samples per chunk (`SOLVE_BLOCK`, `CHUNK_MAX` in `emir/bigblock.py`).


## Reading the numbers

- Times are wall seconds on a shared machine with 24 hardware threads with other work running; every
  process single-threaded; the `load` column is the machine's 1-minute load average.
  Read the ratios and the trends, not the absolute times.
- Peak memory is the process's maximum resident set (`ru_maxrss`).
- Accuracy is cc-grid minus RCC over RCC, branch by branch, over the same window; plus
  is conservative.
- The synthetic mesh is far weaker than a real power grid on purpose (so EM fails and
  the comparison has something to show); its EM verdicts mean nothing.
- `results/selfconsist_waveform.csv` came from six invocations, at most two run side by side
  (at most two simulators at once), merged with `--include` (the commands of `run.sh
  --selfconsist`): `selfconsist.py --cases k32_n10,k32_n20,hd16b,k32_n40 --nocap`,
  `--cases null_n20,one,vss_n10,c0_n20 --nocap`, `--cases hd16b --guard`, `--cases
  k32_n20 --reltol 1e-3`, `--cases p4_n20,hd16b_p4,hd16x --nocap` and `--cases
  hd16b_c20,hd16b_c20rc,k32_n10_ng,k32_n10_long --nocap` (each with `--json`), then
  `selfconsist.py --cases "" --include ... --csv results/selfconsist_waveform.csv --md
  results/selfconsist_waveform.md`; `hd16b_c20rc` was run again with its RC scoring
  (`--cases hd16b_c20rc --nocap --include results/selfconsist_waveform.csv`: a case run
  again replaces its rows). Xyce 7.10 (development build), ngspice 45.
- `results/selfconsist.csv` (the replaced average-droop loop) came from an earlier
  version of `selfconsist.py` (the average-droop loop; not in this repository), three
  invocations merged with `--include`.
- The committed tables came from several invocations of the same scripts, merged with
  `--include`: the sweep (`sweep.py --sizes 10,20,40,70,100,140,200 --variants
  stiff,k256`); the probes (`--sizes "" --variants contact0,nooverlap`); the k32 N = 140
  row again (`--sizes 140`), after the dense superposition's time chunks were bounded by
  the memory budget, a change that affects no other row; and the k32 N = 40 and `stiff`
  rows again (`--sizes 40 --variants stiff`), back to back, so that their times compare
  at one machine load. Xyce 7.10 (development build).
