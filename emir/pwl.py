"""Piecewise-linear droop waveforms for the self-consistent loop.

The waveform loop (`emir.tdloop`) feeds each instance pin's droop back to
the simulator as a voltage source whose value is a waveform. This module
turns the solved droop -- one row per pin on a fine uniform time grid --
into the few points a simulator needs, and writes them in each simulator's
syntax.

**One shared time grid.** Every PWL point is a breakpoint: the simulator
steps onto it. An uncompressed 1 ps PWL ran 12 times slower than the open
loop on a synthetic mesh of `examples/06_big_block` (measured while the loop
was developed; that probe is not committed), and per-pin grids
would make the union of every pin's points the breakpoints. So the points
are chosen once for all pins (`compress`, Douglas-Peucker on every row at
once): a sample is dropped when the straight line between the kept ones
stays within the tolerance on every pin. The tolerance is a share of the
loop's own tolerance, `FRACTION` (0.3), at least 0.05 mV (`tolerance`):
where the loop gain is above 1 at the edge rate the compression's error
comes back amplified in the next pass's droop, so a PWL as coarse as the
loop's tolerance leaves a residual floor above it. A waveform that would
still need more points per pin than the loop allows (`tdloop`: at least
`MAX_POINTS`, more on a long transient) is compressed with a coarser
tolerance until it fits (`compress_capped`), and the loop says so
(EMIR-W542).

**The constructs** (the per-pass include file holds the split's sources,
`VEMIRSC<k>`, with their waveforms written inline):

* Xyce, ngspice, HSPICE: `VEMIRSC<k> <pin node> <rail node> PWL(t v ...)`
  with `+` continuation lines (`spice_source`). Measured here on Xyce 7.10
  and ngspice 45 (`tests/test_tdloop_sims.py`): the source equals the file at
  every corner and holds its last value after the last point. HSPICE: the
  same syntax, UNVERIFIED until `emir verify-sim --sim hspice --run` passes
  where HSPICE runs. (Xyce also reads `PWL FILE "f"`, a two-column file;
  ngspice rejects `PWL file=`, so the inline form is the portable one.)
* Spectre: the person's split holds `vsource type=pwl file="<pin>.pwl"` per
  pin; emir writes each pin's two-column file (`write_columns`) every pass.
  UNVERIFIED until checked where Spectre runs.
"""
from __future__ import annotations

import numpy as np

#: the compression tolerance: this share of the loop's tolerance, at least
#: TOL_MIN_V
FRACTION = 0.3
TOL_MIN_V = 0.05e-3
#: the fewest points a pin's waveform may always take (every point is a
#: breakpoint; `tdloop` allows more on a long transient)
MAX_POINTS = 1500
#: point pairs per line of an inline PWL
PAIRS_PER_LINE = 4


def tolerance(loop_tol_V: float) -> float:
    """The compression tolerance (V) where the loop's own tolerance is
    `loop_tol_V`: max(0.05 mV, 0.3 x it)."""
    return max(TOL_MIN_V, FRACTION * abs(float(loop_tol_V)))


def compress(t, Y, tol: float) -> np.ndarray:
    """The indices of `t` kept for every row of `Y` (rows x len(t)): the
    first and the last, and every sample the straight line between its kept
    neighbours misses by more than `tol` on some row (Douglas-Peucker on all
    rows at once). Exact for a piecewise-linear `Y` whose corners are samples."""
    t = np.asarray(t, dtype=float)
    Y = np.atleast_2d(np.asarray(Y, dtype=float))
    n = len(t)
    if n <= 2:
        return np.arange(n)
    keep = np.zeros(n, bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        f = (t[a + 1:b] - t[a]) / (t[b] - t[a])
        lin = Y[:, a:a + 1] + (Y[:, b:b + 1] - Y[:, a:a + 1]) * f
        err = np.abs(Y[:, a + 1:b] - lin).max(axis=0)
        m = int(np.argmax(err))
        if err[m] > tol:
            c = a + 1 + m
            keep[c] = True
            stack += [(a, c), (c, b)]
    return np.flatnonzero(keep)


def compress_capped(t, Y, tol: float, max_points: int = MAX_POINTS) -> tuple:
    """`(indices, tol used)`: `compress` at `tol`, coarsened (x1.5 a step)
    until at most `max_points` points remain."""
    tol = float(tol)
    while True:
        ix = compress(t, Y, tol)
        if len(ix) <= max_points:
            return ix, tol
        tol *= 1.5


def interp_rows(t_new, t, Y) -> np.ndarray:
    """Every row of `Y` (rows x len(t)) linearly interpolated at `t_new`,
    held at its first and last value outside `t` (as a PWL source holds its
    last value). One search for all rows."""
    t = np.asarray(t, dtype=float)
    t_new = np.asarray(t_new, dtype=float)
    Y = np.atleast_2d(np.asarray(Y, dtype=float))
    if len(t) == 1:
        return np.repeat(Y[:, :1], len(t_new), axis=1)
    j = np.clip(np.searchsorted(t, t_new, side="right") - 1, 0, len(t) - 2)
    dt = t[j + 1] - t[j]
    f = np.where(dt > 0, (t_new - t[j]) / np.where(dt > 0, dt, 1.0), 0.0)
    f = np.clip(f, 0.0, 1.0)
    return Y[:, j] + (Y[:, j + 1] - Y[:, j]) * f


def _pairs(t, v) -> list:
    """`time value` pairs as written, the times strictly increasing as
    printed (a point whose printed time does not advance is dropped: two
    points at one printed time would be a step in the source)."""
    out, last = [], None
    for a, b in zip(t, v):
        ta = f"{float(a):.15g}"
        if last is not None and float(ta) <= last:
            continue
        last = float(ta)
        out.append(f"{ta} {float(b):.9g}")
    return out


def spice_source(name: str, plus: str, minus: str, t, v, sim: str = "xyce") -> str:
    """One independent voltage source with an inline PWL, in SPICE syntax
    (Xyce, ngspice, HSPICE): `name plus minus PWL(t0 v0 t1 v1 ...)` with `+`
    continuation lines. `t` None: a 0 V DC source (pass 0, the open loop)."""
    if t is None:
        return f"{name} {plus} {minus} DC 0"
    pairs = _pairs(t, v)
    lines = []
    for i in range(0, len(pairs), PAIRS_PER_LINE):
        chunk = " ".join(pairs[i:i + PAIRS_PER_LINE])
        lines.append(chunk)
    head = f"{name} {plus} {minus} PWL({lines[0]}"
    body = [f"+ {x}" for x in lines[1:]]
    if body:
        body[-1] += ")"
        return "\n".join([head, *body])
    return head + ")"


def write_columns(path: str, t, v) -> str:
    """A two-column PWL file (time, value; one point a line): Spectre's
    `vsource type=pwl file=` (and Xyce's `PWL FILE`)."""
    from .output import write_text
    if t is None:
        t, v = [0.0], [0.0]
    return write_text(path, "\n".join(_pairs(t, v)) + "\n")
