# 13 Decap-aware EM: decap-free against decap-aware

In the cc-grid route the devices are simulated on an ideal rail, so a decap across that
rail sees no dV/dt and carries no current: every edge of the load current is charged to
the grid from the feed. That is a board with its bypass capacitors unsoldered. With
`--dynamic`, `emir run` integrates `C dV/dt + G V = I(t)` on the R grid (the DSPF's
capacitance, the capacitor devices and the MOS decaps the process file's `cap_models`
size), and the same steps give every segment's current with the decaps soldered in:
**decap-aware** EM, beside the static **decap-free** one. The verdict uses one basis:

| `--em-basis` | the currents | reads |
|---|---|---|
| `worst` (the default with `--dynamic`) | per segment and metric the larger of the two | high on every branch class measured below with one resistive rail (on the feed path it is decap-free's numbers); not a guaranteed bound |
| `decap-free` | the static superposition (the 1.x verdict) | high on the feed path; nothing on a decap's straps |
| `decap-aware` | rms and peak from the dynamic solve, avg the static one | rests on the decap model and the extraction's capacitance |

Which one decides is **the person's choice**: the report names the basis and every
basis's verdict, and `emir next --run-summary` puts the choice to the person when the
bases disagree.

Everything here is **synthetic** (invented meshes, devices, decaps and process numbers)
and the simulator is **Xyce, serial**. Screening, not sign-off.

## Run it

```
./run.sh              # offline part (seconds), and N = 20 on Xyce if found (about a minute)
./run.sh --sweep      # + every case behind results/decap.* (a few minutes)
./run.sh --loop       # + the self-consistent loop with decaps, results/decap_loop.* (about six minutes)
```

From a clone, with numpy, scipy and PyYAML installed (`PYTHON=... ./run.sh` picks the
interpreter). Output goes to `out/` (or the directory given as an argument, `./run.sh
OUT_DIR`, or `OUT=...`). Each part is compared with its golden
file in `expected/` (`../06_big_block/compare.py`, tolerances inside the file); `run.sh`
exits non-zero on a mismatch.

## 1. Offline: one block, three bases, the person's choice

`offline.py` writes a 12 x 12 VDD mesh (`decap_mesh.small_case`): 0.25 ohm segments, 2 fF
per node, 20 device pins each drawing 0.05 mA plus a 10 mA, 20 ps spike per 1 ns, and 4
MOS decaps of 20 pF behind 1 ohm, each on its own 0.5 ohm Metal1 strap 0.2 um wide. Then
`emir run --dynamic` once per basis, and `emir next --run-summary` on the default run.
What it finds (`expected/decap_small.json`):

- decap-free: FAIL 1.164x, peak on the feed segment Rv0_0 (the feed's peak current
  18.50 mA);
- decap-aware: the feed's peak falls to 11.29 mA and Rv0_0 to 0.755x, but the decaps' own
  straps carry their current: Rsd2 FAIL 2.52x peak, the other three straps 2.21x to 2.40x
  (decap-free gives them nothing);
- worst: FAIL 2.52x, both the feed and the straps;
- `--em-basis decap-free` warns that it does not cover the straps (EMIR-W436) and that
  the basis is not the default (EMIR-W437); `emir next --run-summary` plans
  `choose-em-basis` (needs_human) with each basis's verdict.

No basis passes: choosing one never makes the straps narrower. Widening them (Rsd2: 0.200
-> 0.505 um, the report's fix) does.

## 2. With Xyce: RCC against decap-free and decap-aware

`decap_sweep.py` builds example 06's mesh (32 level-1 inverters at 1 GHz, 2 fF per node)
with decaps at fixed random mesh nodes, each a 5 pF capacitor behind its ESR on a 0.5 ohm
strap, written once into both decks: in the RCC deck the strap, the ESR and the capacitor
(linear), in the DSPF a MOS device that `cap_models` sizes to the same C and ESR. The CC
deck is example 06's (a decap on an ideal rail carries nothing). Over the CC window (3
periods), each basis's branch currents against RCC's, by branch class (major >= 50 % of
the largest RCC value, secondary 10-50 %, minor 1-10 %; the straps apart). Plus, at
N = 20, the **solver alone**: Xyce on the same RC network with decaps, driven by PWL
current sources equal to the CC pin waveforms (the window repeated once before itself),
against the decap-aware currents: no open loop in that comparison.

Measured (`results/decap.md`, `results/decap.csv`; relative to RCC, + is conservative):

| case | decap-free rms / peak, governing | decap-aware rms / peak, governing | decap-aware, every class and the straps | decap-free, the straps |
|---|---|---|---|---|
| N = 20, no added decap | +7.68 % / +16.59 % | +7.63 % / +15.64 % | rms +4.79 .. +8.03 %, peak +8.57 .. +20.87 % | |
| N = 20, 8 decaps, 2 ohm | +29.60 % / +47.81 % | +3.80 % / +10.12 % | rms +2.49 .. +13.94 %, peak +6.97 .. +20.56 % | -100 % |
| N = 40, 16 decaps, 2 ohm | +44.42 % / +79.06 % | +1.69 % / +7.80 % | rms +1.17 .. +12.97 %, peak +3.75 .. +21.98 % | -100 % |
| N = 40, 16 decaps, 20 ohm | +25.42 % / +37.14 % | +5.31 % / +13.10 % | rms +2.13 .. +14.63 %, peak +8.79 .. +25.14 % | -100 % |
| N = 70, 32 decaps, 2 ohm | +52.27 % / +110.95 % | +0.59 % / +5.46 % | rms +0.39 .. +12.13 %, peak +2.16 .. +23.99 % | -100 % |
| N = 70, 32 decaps, 20 ohm | +38.43 % / +58.34 % | +3.55 % / +11.14 % | rms +1.34 .. +14.08 %, peak +6.10 .. +23.30 % | -100 % |
| N = 20, 8 decaps between two resistive rails (each net) | +41.95 % / +75.73 % | +13.67 % / +30.92 % | rms -5.52 .. +14.18 % (straps -5.52 .. -0.88 %, a minor branch -3.81 %), peak +3.80 .. +42.02 % | -100 % |

- With decaps, decap-free reads the governing (feed) branch 25 % to 52 % high in rms and
  37 % to 111 % in peak, a decap's straps -100 % (they carry nothing without the decaps)
  and minor branches down to -96 % (rms): it is not conservative everywhere. Decap-aware brings the governing branch to +0.6 % .. +5.3 % rms,
  the open-loop band of cc-grid without decaps (+7.6 % here at N = 20).
- avg is the same in both bases on every branch (a capacitor carries no DC).
- **Two resistive rails**: each net is solved alone with the decap's other plate an ideal
  AC ground, so the current through a decap, its straps and the mesh near them reads low
  (here up to -5.52 % rms on the straps and -3.81 % on a minor branch, with the open-loop excess on top), and `worst` is not a bound there
  (EMIR-W439 says so on the run).
- **The solver alone** (N = 20): rms within -0.01 % .. +0.00 % and peak within -0.03 % ..
  +0.05 % of Xyce on every branch class, with and without decaps (measured on these two
  cases: another mesh, another load can differ; the steps resolve each pin's waveform to
  1e-3 of its own largest current and refine after every change of slope where a decap
  could make the trapezoidal rule ring).
- The RCC reference is periodic to 1.1e-3 (largest relative change of a branch's rms from
  one period to the next, N = 70 without decaps) or better; the averages' identity
  `G Vbar = Ibar - C (V_N - V_0)/T` held to 3e-12. Every decap's own current (all of them,
  not only the summary's 20 largest) is compared with RCC's in `results/decap.md`.
- Time (one shared machine, single-threaded, load 3 to 6): the dynamic solve with
  decap-aware EM took 1.3 to 1.4 s at N = 20, 2.9 to 3.2 s at N = 40 and 8.5 to 9.7 s at
  N = 70; the CC simulation 0.2 to 0.3 s, the RCC one 1.3 to 1.5 s, 5.3 to 5.7 s and 17.1 to
  19.7 s (`results/decap.csv`).

## 3. What `--dynamic` costs

`cost.py` measures `emir run --dynamic` on example 06's big meshes, one thread, in a
clean child: the N = 200 Xyce mesh (40 k grid nodes, 32 inverters; one Xyce run builds
its CC result), and two streamed meshes of 10 k and 90 k nodes with 2000 pins and 5 fF
per node. `--emir-src` measures another checkout the same way, so `results/cost.md`
holds this version beside emir without decap-aware EM (the droop-only `--dynamic`
integration), measured during development: that base is not part of this repository,
so its rows are not reproducible from it. CPU time, the least of two or three runs each. The machine is shared, so the times (and their ratio) move with its
load; they are not a benchmark. `--pairs K` runs the two back to back K times, so the
ratio is quoted as the range the pairs give.

- N = 200: 103.6 -> 129.4 s (1.25x), 1554 sub-steps added for the Xyce result's edges;
  five back-to-back pairs gave 1.45x to 1.85x (1.45x to 1.47x in the three whose
  decap-aware run started at a load below 5), so 1.25x to 1.85x in all;
- 10 k nodes, 2000 pins, 5001 evenly spaced samples: 51.5 -> 65.8 s (1.28x), no
  sub-step (the samples are grid points);
- 90 k nodes, 2000 pins: 65.5 -> 79.8 s (1.22x); peak memory 846 -> 1020 MB (the
  per-branch sums and the per-pin checks), the same as before on the other two.

The droop agrees to 0.002 mV. What decap-aware EM adds is the per-step branch sums
(every branch, every step: O(branches) memory) and the refined steps where the input
needs them.

## 4. Decaps in the self-consistent loop

`decap_loop.py` runs the loop as `emir run --mode cc-grid --self-consistent --sim xyce
--dynamic` runs it on example 06's high-droop mesh (16 x 16, 0.75 ohm segments, 128
inverters: 4.1 % droop in RCC) with 8 decaps of 5 pF behind 2 ohm on 0.5 ohm straps
(`decap_mesh.decap_case`), and scores every pass against RCC on each EM basis. Three
ways: the decaps sized by `cap_models` and fed back; the decap model's entry left empty
(nothing sized: EMIR-W547, W409); and decap 0 a MOS device of the testbench (the same
5 pF of gate capacitance), its pin fed by the split, at the default pass limit and at 16.

Measured (`results/decap_loop.md`, `results/decap_loop.csv`; relative to RCC, + is
conservative):

| how the decaps are in the loop | simulations | decap-aware: governing avg / rms / peak | every class (avg, rms, peak) and the straps (rms, peak) | devices' peaks |
|---|---|---|---|---|
| open loop (pass 0) | 1 | +2.20 / +6.64 / +21.22 % | straps +50.42 .. +52.04 % rms, +62.70 .. +69.72 % peak | +15.35 .. +28.06 % |
| sized and fed back | 6 (converged) | -0.03 / -0.02 / +0.00 % | -0.19 .. +1.14 % | -0.04 .. +0.02 % |
| not sized (EMIR-W547) | 8 (settled) | +1.14 / +13.90 / +31.94 % | straps not checked; minor branches -59.02 .. +71.16 % (peak) | -5.87 .. -1.00 % |
| one simulated, `--max-passes 16` | 11 (settled) | -0.03 / -0.01 / +0.18 % | -5.94 .. +1.74 % (the simulated decap's strap, peak) | -0.04 .. +0.02 % |

- Fed back, the decaps take the answer to RCC's on every branch class and strap; the
  worst basis keeps the R-only grid's readings, +23.48 % rms and +55.66 % peak on the
  governing branch.
- Not sized, they are left out of the droop fed back: the devices read low, the
  governing branch high (it misses the decaps' local charge), and their straps are not
  checked. Neither accurate nor conservative: add the `cap_models` entries.
- Simulated by the testbench, a decap loads the grid through its pin and is not added
  again, but its pin's current is all capacitive and the loop converges slower: at the
  default 10 passes it stopped at its pass limit and reported the open loop (EMIR-W407);
  at 16 it settled in 11 simulations, that decap's own current -1.08 % rms and -5.94 %
  peak.

## Files

| file | what |
|---|---|
| `decap_mesh.py` | the generators: `decap_case`, `two_rail_case`, `ref_deck`, `small_case` |
| `offline.py` | part 1 |
| `decap_sweep.py` | part 2 (`--cases`, `--csv`, `--md`, `--json`) |
| `decap_loop.py` | part 4: the self-consistent loop with decaps (`--cases`, `--csv`, `--md`, `--json`) |
| `cost.py` | part 3: what `--dynamic` costs on example 06's big meshes (`--emir-src` measures another checkout, `--pairs` the ratio back to back) |
| `process.yaml` | example 06's invented process file plus the invented decap model `ncap` (cox 10 fF/um^2, `esr_a` per case) |
| `expected/` | the golden files, with their tolerances |
| `results/` | the committed sweep: `decap.csv`, `decap.md`; `cost.json`, `cost.md` (part 3); `decap_loop.csv`, `decap_loop.md` (part 4) |
