# 11: the HSPICE route, offline (synthetic results)

**HSPICE was not run for this example.** The results in [`inputs/`](inputs/) are
synthetic: [`make_results.py`](make_results.py) wrote them with EMIR's own writer
(`emir.hspice.write_tr0ascii`), which follows the documented HSPICE ASCII layout
(`.option post=2 post_version=2001`), and their CSDF twins (`emir.csdf.write_csdf`).
The example shows what EMIR does with such files: the save lines an HSPICE deck needs,
the names sidecar, the reader, rcc and cc-grid, and the "not verified on this machine"
line a run carries until `emir verify-sim --sim hspice --run` has passed where HSPICE
runs ([Simulators](../../docs/simulators.md)). The CSDF twins stay unverified even then:
`emir verify-sim` covers HSPICE's `.tr0` output only, so a run on a `.csd` file says
`format not covered` (EMIR-W464) and suggests no verification. Screening, not sign-off.

```sh
bash examples/11_hspice_offline/run.sh          # offline; nothing licensed runs
```

## The circuit and its currents

The bundled sample rail (`emir/data/sample/rail.dspf`, with its ILLUSTRATIVE process
file), instantiated as `XI0`: twelve 1-ohm Metal1 segments Rm1..Rm12 (0.75 um wide) fed at
VDD, and four PMOS loads MP0..MP3 at x = 15, 30, 45 and 60 um, through the contacts
Rc0..Rc2 (two cuts each) and Rc3 (one cut). Every load draws the same trapezoid, all in
phase: 0 to 2 mA, 1 ns delay, 0.1 ns edges, 4.9 ns on top, a 10 ns period; four periods,
on uneven time points (every 0.5 ns, and 11 points on each edge).

Per load: avg = 2 mA x (4.9 + 0.1) / 10 = 1.0 mA; peak 2 mA; rms = 2 mA x
sqrt((4.9 + 0.2 / 3) / 10) = 1.409492 mA.

| file | what it holds |
|---|---|
| `inputs/rcc.tr0`, `inputs/rcc.csd` | every branch current, named `i(xi0.rm1` .. `i(xi0.rc3` (HSPICE leaves out the closing parenthesis): Rm1..Rm3 carry four loads, Rm4..Rm6 three, Rm7..Rm9 two, Rm10..Rm12 and Rc0..Rc3 one |
| `inputs/cc.tr0`, `inputs/cc.csd` | each load's terminal currents `i1`..`i4(xi0.mpk`: into the drain -I, gate and bulk 0, and the source current i3 written **out** of the device (-I), as HSPICE's manuals draw it |

Whether a real HSPICE run reports i3 out of the device was not confirmed, so EMIR saves
all four terminals and sets i3's sign from KCL when it reads the file (EMIR-I465): here
it finds i1 + i2 - i3 + i4 = 0 and reads each MPk:s current as +I, into the load.

## What it runs

1. `emir save-lines rail.dspf --mode rcc --sim hspice --prefix XI0. --names-out
   out/rcc.tr0.names --out out/rcc.hspice.inc` (and `--mode cc-grid`): the lines to add to an
   HSPICE deck (`.option probe post=2 post_version=2001`, `.probe tran ...`) and the
   names sidecar. The sidecar is copied beside the CSDF twins too.
2. `emir run ... --cc-sim out/rcc.tr0 --mode rcc` and `--cc-sim out/cc.tr0 --mode
   cc-grid` (and the same on the `.csd` files), with `--period 1e-8`.
3. `compare.py` digests the four run summaries and compares them with
   `expected/summary.json` (relative tolerance 1e-4).

## The numbers, by hand

With the sample's limits (Metal1 3.2 mA/um avg, sqrt(28.8 x 5) = 12 mA/um rms, 16 mA/um
peak, on 0.75 um; Cont 0.75 mA avg, 1.5 mA rms, 2.4 mA peak per cut):

| item | metric | by hand | the runs |
|---|---|---|---|
| Rm1 (4 loads) | avg | 4 / 2.4 = 1.66667 | 1.66667 |
| | rms | 4 x 1.409492 / 9 = 0.626441 | 0.626462 |
| | peak | 8 / 12 = 0.666667 | 0.666667 |
| Rc3 (1 load, 1 cut) | avg | 1 / 0.75 = 1.33333 | 1.33333 |
| | rms | 1.409492 / 1.5 = 0.939661 | 0.939693 |
| | peak | 2 / 2.4 = 0.833333 | 0.833333 |
| VDD | IR drop (avg) | 3 x 1 ohm x (4 + 3 + 2 + 1) mA + 24 ohm x 1 mA = 54.0 mV | 54.000 mV |

The rms ratios read 3e-5 above the exact value: the time-weighted trapezoid rule on the
file's points over the 0.1 ns edges (a simulator's own points would do the same). rcc and
cc-grid agree, and so do the `.tr0` files and their CSDF twins. Every run's verdict is
FAIL (Rm1..Rm3 avg, Rc3 avg), and its `simulator_verification` is `not verified`: the
files' header names no simulator (it says they are synthetic), the names sidecar says
`hspice`, and no `emir verify-sim` record exists in the example's own state directory
(`out/state`).

Regenerate the inputs with `python examples/11_hspice_offline/make_results.py` (it needs
EMIR importable: a clone with its virtual environment active).
