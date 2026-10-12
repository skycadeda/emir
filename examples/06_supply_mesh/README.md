# 06 A supply mesh screened with rcc

An invented N x N metal supply mesh with level-1 CMOS inverters on it, simulated
RC-extracted with every branch current saved and screened with rcc: every metric (avg,
rms and peak EM, static and dynamic IR) from the measured branch currents. Its invented
limits make a few straps fail, so the run shows what a person gets: the verdict, the
failures and their fixes. The README banner and the blog's cover and first two carousel
pages are drawn from this run (`scripts/make_figures.py`).

Everything here is **synthetic** (an invented mesh, invented devices, an invented process
file) and the simulator is **Xyce, serial**: an illustration, not a statement about
Spectre or a real design. Screening, not sign-off.

## Run it

```
./run.sh              # seconds with Xyce
```

From a clone, with numpy, scipy and PyYAML installed (`PYTHON=... ./run.sh` picks the
interpreter; emir is found through `PYTHONPATH` when it is not installed). Output goes
to `out/` (or the directory given as an argument, `./run.sh OUT_DIR`, or `OUT=...`).
The run is compared with its golden file, `expected/screen_n10.json` (`compare.py`,
tolerances inside the file); `run.sh` exits non-zero on a mismatch. Without Xyce it says
so and exits 0.

## Files

| file | what |
|---|---|
| `mesh.py` | the generator, `xyce_case`: the RC DSPF, the Xyce deck that prints every branch current, and its `.names` sidecar, from one description |
| `process_screen.yaml` | the invented limits: Metal1 1 mA/um avg, so that at N = 10 a few straps fail; contact limits high enough that every contact passes |
| `compare.py` | checks a result JSON against a golden file |
| `expected/screen_n10.json` | the golden values of the run |
| `expected/screen_n10_no_period.json` | the same run without `--period`: refused with no number (`EMIR-E428`) |

## What run.sh does

```
python mesh.py xyce --n 10 --k 32 --out out/screen && cd out/screen
Xyce -a rcc.cir                       # the RC mesh and its devices, every branch current printed
emir run grid_rc.dspf --process ../../process_screen.yaml --net VDD --cc-sim rcc.raw \
    --period 1e-9 --sim xyce --summary-out screen.json --html html
```

`rcc.raw.names` maps Xyce's `I(Rv0_0)` onto the DSPF's `Rv0_0:1` (`emir schema names`).
The window is the record's last three whole periods of `--period` (1 to 4 ns). rcc is
exact for the simulated record over the whole periods you give (--period); whether the
testbench has settled is yours to establish, as for any .meas. Then the same run without
`--period`: refused with no number (`EMIR-E428`). Given `--period 125p` instead (the feed
repeats every 125 ps, eight inverter phases, but each contact only every 1 ns), the last
two periods differ and the run is refused too (`EMIR-E429`).
The HTML report and the EM/IR chart land in `out/screen/html/`.

Measured with Xyce 7.10: FAIL, on four Metal1 straps over their average limit of
1 mA/um. The two that leave the feed carry 3.656 mA (up) and 3.510 mA (right) on
average, which with the corner inverter's 0.231 mA are the supply's 7.397 mA, and read
1.83x and 1.75x (fixes: widen 2.000 -> 3.660 um and -> 3.510 um); the next two read
1.07x and 1.03x; every contact passes (worst 0.727x, a peak); the IR drop is 3.82 mV
and the dynamic IR drop 37.3 mV.

## Reading the numbers

- The synthetic mesh is far weaker than a real power grid on purpose (so EM fails and
  the run has something to show); its EM verdicts mean nothing.
- The ratios are held to 0.1 % in the golden file: another Xyce version takes other time
  steps; the averages are charge, and move far less.
