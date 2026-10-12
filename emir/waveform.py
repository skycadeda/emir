"""Waveform arithmetic: avg, rms and peak over the record's last whole periods.

rcc (`emir/rcc.py`) reads every branch current of an RC simulation and takes
each one's avg, rms and peak over a window of the simulated time. The
contract, in one sentence: rcc is exact for the simulated record over the
whole periods you give (--period); whether the testbench has settled is yours
to establish, as for any .meas.

The window is the record's last whole periods of `--period`, up to three
(`MAX_PERIODS`, the ones closest to its end). EMIR refuses a run, with a code
and no number, only for what it can check for certain:

* a transient with no `--period` (EMIR-E428): EMIR never guesses a period;
* fewer than two whole periods in the record (EMIR-E431, `record_window`);
* the record's last two whole periods differ by more than 2 % (`TOL`) in the
  avg, rms or peak of a branch that matters (EMIR-E429, `window`): a wrong
  --period, or a block still starting up. A branch matters when it carries at
  least 1 % of its net's largest current (`MATTERS`, `matters`).

An operating point (one time point) has no window: avg = rms = peak.

avg is exact for the record as the simulator's samples joined by straight
lines; rms is the trapezoid of i squared over the same samples, as `.meas
RMS` computes it (on a coarse record it reads a little high: conservative).

Two things here are easy to get wrong, so they are done explicitly:

* **Non-uniform time steps.** A simulator picks its own steps. `y.mean()` over
  them is *not* the average -- it weights a densely-sampled edge as heavily as
  a long flat stretch. Everything integrates by trapezoid.
* **Whole periods.** A window that is not a whole number of periods leaves a
  residual that biases the average, and the average is exactly what EM cares
  about. The window's ends are interpolated on the simulator's samples
  (`clip_window`), so it is exactly whole periods long.
"""
from __future__ import annotations

import math

import numpy as np

from .errors import EmirError

#: the largest change of a branch's avg, rms or peak between the record's last
#: two whole periods (the maintainer's bar for a number EMIR offers)
TOL = 0.02
#: the window: the record's last whole periods, up to this many
MAX_PERIODS = 3
#: the whole periods a record needs: two, so its last two can be compared
MIN_PERIODS = 2
#: a branch matters when its avg, rms or peak reaches this share of its net's
#: largest
MATTERS = 0.01
#: a change is a share of the larger of its two values, and of no less than
#: this share of the branch's own rms: noise on a capacitor's near-zero
#: average refuses nothing, and a small DC tap on a net of large AC currents
#: is held to itself (held to 1 % of the net's largest rms, such a tap moved
#: 20 % between the last two periods unrefused)
FLOOR = 0.10
#: currents below SPICE's default ABSTOL (1 pA) are the simulator's noise
ABSTOL = 1e-12
#: the refusals of a window: no period, a period the record's last two whole
#: periods do not repeat over, a record too short for it
REFUSALS = ("EMIR-E428", "EMIR-E429", "EMIR-E431")


# --------------------------------------------------------------- integration


def trapz_weights(t: np.ndarray) -> np.ndarray:
    """Per-sample weights `w` with `sum(w * y) == integral(y dt)`.

    Carrying the weights instead of resampling keeps the simulator's own
    adaptive time steps -- which is where the accuracy is. The weighted sum
    of `y` is exact for piecewise-linear data (the average); that of `y * y`
    is the trapezoid of the square (the rms, as `.meas RMS` takes it), not
    the exact integral of a straight segment's square.
    """
    t = np.asarray(t, dtype=float)
    w = np.zeros_like(t)
    dt = np.diff(t)
    w[:-1] += dt / 2.0
    w[1:] += dt / 2.0
    return w


def _at(t, y, x):
    """Every row of `y` (rows x samples) at time `x`, linearly interpolated
    (`np.interp` row by row, in one step)."""
    i = int(np.clip(np.searchsorted(t, x, side="right"), 1, len(t) - 1))
    t0, t1 = t[i - 1], t[i]
    w = 0.0 if t1 == t0 else min(max((x - t0) / (t1 - t0), 0.0), 1.0)
    return y[:, i - 1] * (1.0 - w) + y[:, i] * w


def clip_window(t, y, t0=None, t1=None):
    """Trim to [t0, t1], interpolating the endpoints so the span is exact.

    Snapping to the nearest sample instead would quietly change the window
    length, which is the whole-period error this module exists to avoid.
    """
    t = np.asarray(t, dtype=float)
    y = np.atleast_2d(np.asarray(y, dtype=float))
    t0 = t[0] if t0 is None else max(float(t0), t[0])
    t1 = t[-1] if t1 is None else min(float(t1), t[-1])
    if not t1 > t0:
        raise EmirError("EMIR-E420", f"empty window [{t0:g}, {t1:g}]")
    inner = (t > t0) & (t < t1)
    tt = np.concatenate([[t0], t[inner], [t1]])
    yy = np.concatenate([_at(t, y, t0)[:, None], y[:, inner], _at(t, y, t1)[:, None]],
                        axis=1)
    return tt, yy


def metrics_tran(t, y, t0=None, t1=None) -> dict:
    """avg / rms / peak of one or more signals over [t0, t1].

    `avg` keeps its sign (the EM check takes the magnitude); `rms` and `peak`
    are magnitudes by construction.
    """
    one = np.asarray(y).ndim == 1
    tt, yy = clip_window(t, y, t0, t1)
    w = trapz_weights(tt)
    T = tt[-1] - tt[0]
    out = {"avg": (yy @ w) / T,
           "rms": np.sqrt((yy * yy) @ w / T),
           "peak": np.abs(yy).max(axis=1),
           "t0": float(tt[0]), "t1": float(tt[-1]), "n": int(len(tt))}
    if one:
        for k in ("avg", "rms", "peak"):
            out[k] = float(out[k][0])
    return out


#: whole periods are counted to float rounding only: a record a sliver of a
#: period short of k periods holds k - 1 (a tolerance of 1e-4 once averaged
#: over 2.99995 periods, labelled 3, and a late load read 33 % low)
ROUNDING = 1e-9


def whole(span: float, P: float) -> int:
    """How many whole periods of `P` fit in `span` (to float rounding)."""
    return int(math.floor(span / P + ROUNDING))


# ---------------------------------------------------------- the window


def _refuse(code, message, ctx=None, **details):
    """Raise a window's refusal: its code, a message that says what to give,
    and the run's inputs (`ctx`) for the code's command and `details.run`."""
    ctx = dict(ctx or {})
    raise EmirError(code, message, details={**details, "run": ctx},
                    **{k: v for k, v in ctx.items()
                       if k in ("dspf", "process", "cc_sim", "net", "period", "sim")})


def record_window(t, period=None, ctx=None) -> dict | None:
    """The window of a record, whatever the net: `{t0, t1, period, n_periods}`,
    the record's last whole periods of `period`, up to `MAX_PERIODS`; None for
    an operating point (one time point). Refused with EMIR-E428 when a
    transient has no period, EMIR-E431 when it holds fewer than two whole
    periods of it."""
    t = np.asarray(t, dtype=float)
    if len(t) < 2:
        return None
    t_beg, t_end = float(t[0]), float(t[-1])
    rec = {"record_s": [t_beg, t_end]}
    if period is None:
        _refuse("EMIR-E428", f"the result is a transient ({t_beg:.4g} .. {t_end:.4g} s, "
                f"{len(t)} time points) and no --period was given: rcc averages over "
                f"whole periods of the block's activity, and EMIR never guesses them. "
                f"Give --period, the block's period (its clock, or the period of the "
                f"testbench's PULSE or SIN sources)", ctx, period_s=None, **rec)
    P = float(period)
    n = whole(t_end - t_beg, P)
    if n < MIN_PERIODS:
        stop = t_beg + MIN_PERIODS * P
        _refuse("EMIR-E431", f"the record ({t_beg:.4g} .. {t_end:.4g} s) holds {n} whole "
                f"period(s) of {P:.6g} s; rcc needs at least {MIN_PERIODS}, so that the "
                f"last two can be compared. Simulate at least {MIN_PERIODS} periods (to "
                f"t = {stop:.4g} s or later)"
                + (", and check --period: it is longer than the whole record" if n == 0
                   else ""), ctx, period_s=P, periods_in_record=n,
                periods_needed=MIN_PERIODS, stop_time_s=stop, **rec)
    k = min(n, MAX_PERIODS)
    t0 = t_end - k * P
    # exactly k periods long: only float rounding may reach past the start
    assert t0 >= t_beg - 2 * ROUNDING * P, (t_beg, t0, k, P)
    return {"t0": max(t0, t_beg), "t1": t_end, "period": P, "n_periods": k}


def last_periods(t, Y, P) -> tuple:
    """The avg, rms and peak of every row of `Y` over the record's last whole
    period of `P` and over the one before it."""
    t_end = float(t[-1])
    return (metrics_tran(t, Y, t_end - P, t_end),
            metrics_tran(t, Y, t_end - 2 * P, t_end - P))


def _finite(m, rows) -> dict:
    """`m`'s avg (as a magnitude), rms and peak of `rows`, a branch that is
    not a number set to 0 (it matters to nothing and prints no number)."""
    return {k: np.where(rows, np.abs(np.atleast_1d(m[k])), 0.0) for k in ("avg", "rms", "peak")}


def matters(last, before) -> np.ndarray:
    """The rows that matter (a boolean per row): a branch whose avg, rms or
    peak in either of the record's last two periods (`last_periods`) reaches
    `MATTERS` of the largest on its net, and the simulator's noise floor
    (`ABSTOL`). A branch that is not a number matters to nothing (one used to
    make every share NaN and switch the comparison off)."""
    ok = np.ones(len(np.atleast_1d(last["avg"])), dtype=bool)
    for m in (last, before):
        for k in ("avg", "rms", "peak"):
            ok &= np.isfinite(np.atleast_1d(m[k]))
    a, b = _finite(last, ok), _finite(before, ok)
    hit = np.zeros_like(ok)
    for k in ("avg", "rms", "peak"):
        v = np.maximum(a[k], b[k])
        hit |= v >= MATTERS * v.max(initial=0.0)
    return ok & hit & (np.maximum(a["peak"], b["peak"]) >= ABSTOL)


def change(last, before, rows=None) -> tuple:
    """`(fraction, row, metric)`: the largest change of an avg, rms or peak
    between two periods' metrics, over the `rows` that matter (a boolean per
    row; all when None). Each is a share of the larger of its two values, and
    never of less than `FLOOR` of that branch's own rms (nor of `ABSTOL`): a
    capacitor's near-zero average is held to that, not to itself."""
    n = len(np.atleast_1d(last["avg"]))
    rows = np.ones(n, dtype=bool) if rows is None else np.asarray(rows, dtype=bool)
    if not rows.any():
        return 0.0, None, None
    a, b = _finite(last, rows), _finite(before, rows)
    floor = np.maximum(FLOOR * np.maximum(a["rms"], b["rms"]), ABSTOL)
    best = (0.0, None, None)
    for k in ("avg", "rms", "peak"):
        x, y = np.atleast_1d(last[k]), np.atleast_1d(before[k])
        with np.errstate(invalid="ignore"):
            d = np.abs(np.where(rows, x - y, 0.0)) / np.maximum(np.maximum(a[k], b[k]), floor)
        i = int(np.argmax(d))
        if d[i] > best[0]:
            best = (float(d[i]), i, k)
    return best


_WHAT = {"avg": "average", "rms": "rms", "peak": "peak"}


def window(t, items, period=None, names=None, net=None, log=print, ctx=None) -> dict | None:
    """The window rcc averages a net over (`record_window`), its last two
    whole periods compared on the branches that matter (`matters`, `change`):
    `{t0, t1, period, n_periods, change, branches}`, or None for an operating
    point. `items` are the net's branch currents (rows x samples, named by
    `names`). Refused with EMIR-E428 or E431 (the record's) or EMIR-E429 (the
    last two periods differ by more than `TOL`). `ctx` fills the codes'
    commands (dspf, process, cc_sim, net, period) and `details.run`."""
    t = np.asarray(t, dtype=float)
    win = record_window(t, period, ctx)
    if win is None:
        return None
    Y = np.asarray(items, dtype=float)
    Y = Y.reshape(0, len(t)) if Y.size == 0 else np.atleast_2d(Y)
    on = f"net {net}: " if net else ""
    P, rows, d = win["period"], np.zeros(0, dtype=bool), 0.0
    if len(Y):
        last, before = last_periods(t, Y, P)
        rows = matters(last, before)
        d, r, m = change(last, before, rows)
        if d > TOL:
            name = str(names[r]) if names is not None else f"row {r}"
            t_beg, t_end = float(t[0]), float(t[-1])
            stop = t_beg + 2 * (t_end - t_beg)
            _refuse("EMIR-E429", f"{on}the record's last two whole periods of {P:.6g} s "
                    f"differ: branch {name}'s {_WHAT[m]} changes by {d:.1%} "
                    f"(at most {TOL:.0%} on a branch carrying {MATTERS:.0%} of its net's "
                    f"largest current). Give the right --period, or simulate longer if the "
                    f"block is still starting up", ctx, net=net, period_s=P, branch=name,
                    metric=m, change=d, record_s=[t_beg, t_end],
                    periods_in_record=whole(t_end - t_beg, P), stop_time_s=stop)
    n = int(rows.sum())
    log(f"[emir] {on}window {win['t0']:.4g} .. {win['t1']:.4g} s: the last "
        f"{win['n_periods']} whole period(s) of --period {P:.6g} s; the last two agree "
        f"within {d:.2%} on the {n} branch(es) that matter")
    return {**win, "change": float(d), "branches": n}
