"""Write the synthetic HSPICE ASCII results of example 11 (and their CSDF twins).

    python examples/11_hspice_offline/make_results.py [OUT_DIR]   # default: inputs/ here

HSPICE did NOT write these files: this script does, with `emir.hspice`'s writer,
which follows the documented HSPICE ASCII layout (`.option post=2
post_version=2001`), and `emir.csdf`'s. The circuit is the bundled sample rail
(`emir/data/sample/rail.dspf`) instantiated as XI0: twelve 1-ohm metal1 segments
fed at VDD, four PMOS loads MP0..MP3 drawing current into their sources through
the contacts Rc0..Rc3. Every load draws the same trapezoid, all in phase: 0 to
2 mA, 1 ns delay, 0.1 ns edges, 4.9 ns on top, a 10 ns period, four periods.
Per load: avg 1.0 mA, peak 2 mA, rms 2 mA x sqrt((4.9 + 0.2/3) / 10) = 1.409492
mA (README.md works out every ratio from these).

* `rcc.tr0`: every branch current of the RC netlist, named as
  `emir save-lines rail.dspf --sim hspice --prefix XI0.` prints them
  (`i(xi0.rm1` ...): Rm1..Rm3 carry four loads, Rm4..Rm6 three, Rm7..Rm9 two,
  Rm10..Rm12 and Rc0..Rc3 one, from the feed towards the loads.
* `rcc.csd`: the same data as CSDF (Xyce's `value:index` form).

Time points are uneven, as a simulator's are: every 0.5 ns, and 11 points on
each 0.1 ns edge.
"""
from __future__ import annotations

import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))

from emir.csdf import write_csdf  # noqa: E402
from emir.hspice import write_tr0ascii  # noqa: E402

PERIOD, DELAY, EDGE, TOP, PEAK, STOP = 10e-9, 1e-9, 0.1e-9, 4.9e-9, 2e-3, 40e-9
LOADS = {f"rm{k}": (4 if k <= 3 else 3 if k <= 6 else 2 if k <= 9 else 1) for k in range(1, 13)}
LOADS.update({f"rc{k}": 1 for k in range(4)})


def load(t: float) -> float:
    x = t - DELAY
    if x < 0:
        return 0.0
    x %= PERIOD
    if x < EDGE:
        return PEAK * x / EDGE
    if x < EDGE + TOP:
        return PEAK
    if x < 2 * EDGE + TOP:
        return PEAK * (2 * EDGE + TOP - x) / EDGE
    return 0.0


def times() -> list:
    pts = {round(k * 0.5e-9, 15) for k in range(int(STOP / 0.5e-9) + 1)}
    for p in range(4):
        for start in (0.0, EDGE + TOP):
            for j in range(11):
                pts.add(round(DELAY + p * PERIOD + start + j * EDGE / 10, 15))
    return sorted(t for t in pts if t <= STOP + 1e-18)


def main(out: str) -> list:
    os.makedirs(out, exist_ok=True)
    ts = times()
    title = "* emir example 11: synthetic, not written by HSPICE"
    note = "synthetic: emir.hspice.write_tr0ascii, not HSPICE"
    rcc_names = [f"i(xi0.{r}" for r in LOADS]
    rcc_rows = [[t, *[n * load(t) for n in LOADS.values()]] for t in ts]
    written = [
        write_tr0ascii(os.path.join(out, "rcc.tr0"), ["TIME", *rcc_names], [([], rcc_rows)],
                       version="2001", title=title, date="10/02/2026", time_="00:00:00",
                       copyright_=note),
        write_csdf(os.path.join(out, "rcc.csd"), [n + ")" for n in rcc_names], [rcc_rows],
                   title=title),
    ]
    rms = PEAK * math.sqrt((TOP + 2 * EDGE / 3) / PERIOD)
    print(f"{len(ts)} time points; per load avg 1.0 mA, peak 2 mA, rms {rms * 1e3:.6f} mA")
    return written


if __name__ == "__main__":
    for p in main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "inputs")):
        print(p)
