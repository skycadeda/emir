"""Tap currents: the per-device injection vector that drives the EM solve.

A transistor circuit taps its supply rail at every device contact, not at the
labelled top-level pins. This module is how those currents get in, and how they
are combined through the linear grid.

Input (CSV or JSON), one row per tap::

    name,x_um,y_um,layer,i_avg,i_rms,i_peak
    MN0:S,0.42,-0.50,Metal1,1.8e-5,2.4e-5,3.9e-5

`i_rms` and `i_peak` are optional; missing values fall back to |i_avg|, which
is exact for a DC tap. Sign convention matches `--currents`: positive is
current *into* the net. KCL is enforced on `i_avg`.

**Why superposition.** The grid is linear, so one sparse LU factorisation
serves every injection pattern: each tap costs a back-substitution, not a
solve. The three metrics then combine differently, and the difference matters:

* **avg is linear.** One solve with the summed average injection vector gives
  the exact average branch currents. No per-tap work needed.
* **rms and peak are not.** From per-tap *scalars* alone the branch value is
  not determined -- it depends on how the taps correlate in time. This module
  returns the **worst case**, `sum_k |T_ek| * I_k`, which assumes every tap
  peaks simultaneously and in phase. That is an upper bound, never an
  underestimate, and it is labelled as such in the report.
* Give it **waveforms** instead and the answer is exact: `I_e(t) = sum_k T_ek
  I_k(t)`, then average / rms / max over t. That is what the CC-sim path
  feeds in.
"""
from __future__ import annotations

import csv
import json
import os

import numpy as np

from .errors import EmirError

METRIC_COLS = {"avg": "i_avg", "rms": "i_rms", "peak": "i_peak"}


def load_taps(path: str) -> list[dict]:
    """Read a tap file. Returns [{name, xy, layer, i_avg, i_rms, i_peak}]."""
    ext = os.path.splitext(path)[1].lower()
    rows = []
    if ext == ".json":
        raw = json.load(open(path, encoding="utf-8"))
        rows = raw["taps"] if isinstance(raw, dict) else raw
    else:
        with open(path, newline="", encoding="utf-8") as f:
            rows = [r for r in csv.DictReader(f)]
    taps = []
    for i, r in enumerate(rows):
        try:
            avg = float(r["i_avg"])
        except (KeyError, TypeError, ValueError) as e:
            raise EmirError("EMIR-E421", f"{path}: row {i + 1} has no usable i_avg", path=path) from e
        t = {
            "name": r.get("name") or f"tap{i}",
            "xy": (float(r["x_um"]), float(r["y_um"])),
            "layer": r["layer"],
            "i_avg": avg,
        }
        for m in ("rms", "peak"):
            v = r.get(METRIC_COLS[m])
            t[f"i_{m}"] = abs(float(v)) if v not in (None, "") else abs(avg)
        w = r.get("wave") or r.get("waveform")
        if w is not None:
            t["wave"] = np.asarray(w, dtype=float)
            # a waveform is the truth; the scalars are derived from it
            t["i_avg"] = float(t["wave"].mean())
            t["i_rms"] = float(np.sqrt((t["wave"] ** 2).mean()))
            t["i_peak"] = float(np.abs(t["wave"]).max())
        taps.append(t)
    if not taps:
        raise EmirError("EMIR-E421", f"{path}: no taps", path=path)
    return taps


def check_kcl(taps: list[dict], tol: float = 1e-6) -> None:
    tot = sum(t["i_avg"] for t in taps)
    mx = max(abs(t["i_avg"]) for t in taps)
    if abs(tot) > tol * mx:
        raise EmirError("EMIR-E406",
            f"KCL violation: tap i_avg sums to {tot:.3e} A "
            f"(tolerance {tol * mx:.3e}); the average injection must balance")


def check_metric_sanity(taps: list[dict]) -> list[str]:
    """rms >= |avg| and peak >= rms hold for every waveform. Report breaches."""
    bad = []
    for t in taps:
        if t["i_rms"] < abs(t["i_avg"]) * (1 - 1e-9):
            bad.append(f"{t['name']}: i_rms {t['i_rms']:.4g} < |i_avg| "
                       f"{abs(t['i_avg']):.4g}")
        if t["i_peak"] < t["i_rms"] * (1 - 1e-9):
            bad.append(f"{t['name']}: i_peak {t['i_peak']:.4g} < i_rms "
                       f"{t['i_rms']:.4g}")
    return bad


class GridSolver:
    """One LU factorisation of the grid, reused for every injection pattern.

    `G` is the reduced (grounded) conductance matrix; `solve` returns node
    voltages for an injection vector. Factorising once turns each additional
    tap from a solve into a back-substitution -- the whole point of keeping the
    grid separate from the device simulation.
    """

    def __init__(self, G, free_idx, n_nodes):
        from scipy.sparse.linalg import splu
        self.lu = splu(G.tocsc())
        self.free = free_idx
        self.n = n_nodes
        self.n_solves = 0

    def solve(self, inj: np.ndarray) -> np.ndarray:
        v = np.zeros(self.n)
        v[self.free] = self.lu.solve(inj[self.free])
        self.n_solves += 1
        return v


def branch_currents(V: np.ndarray, Ea, Eb, Eg) -> np.ndarray:
    return np.nan_to_num(Eg * (V[Ea] - V[Eb]))


def has_waveforms(taps: list[dict]) -> bool:
    return any(t.get("wave") is not None for t in taps)


def superpose(solve_branch, taps, n_branch, metrics, log=print) -> tuple:
    """Per-metric |branch current| from per-tap **scalars**.

    `solve_branch(tap, amps)` returns the branch currents for injecting `amps`
    at one tap. `avg` is exact -- averaging is linear, so one solve with the
    summed average vector is the answer. `rms`/`peak` are the **in-phase worst
    case**: from scalars alone the branch value depends on how the taps
    correlate in time, and `sum_k |T_ek| I_k` is the upper bound over all
    correlations. Returns (per-metric arrays, note).
    """
    out = {}
    need = [m for m in ("rms", "peak") if m in metrics]
    acc = {m: np.zeros(n_branch) for m in need}
    avg = np.zeros(n_branch)
    for t in taps:
        tr = solve_branch(t, 1.0)                # unit transfer for this tap
        avg += tr * t["i_avg"]
        for m in need:
            acc[m] += np.abs(tr) * t[f"i_{m}"]
    if "avg" in metrics:
        out["avg"] = np.abs(avg)
    out.update(acc)
    note = ("rms/peak are the in-phase worst case (per-tap scalars carry no "
            "correlation information) - upper bounds, never underestimates")
    log(f"[emir] superposition over {len(taps)} taps: {note}")
    return out, note


def superpose_waveforms(solve_branch, taps, n_branch, metrics, weights=None,
                        max_bytes=2 << 30, log=print, chunk=None) -> tuple:
    """Exact per-metric |branch current| from per-tap **waveforms**.

    `I_e(t) = sum_k T_ek I_k(t)`, then average / rms / max over t. One
    back-substitution per tap builds T; the reduction over time is plain
    matrix algebra, so no extra solves regardless of how many timesteps there
    are. This is the exact answer the scalar path can only bound.

    `weights` are per-sample time weights summing to 1 -- pass the trapezoid
    weights from `ccsim.trapz_weights` whenever the waveforms come off a
    variable-timestep simulator, where an unweighted mean would count a
    densely-sampled switching edge as heavily as a long quiet stretch and
    bias both avg and rms. Uniform sampling is assumed only if they are
    omitted.

    The reduction runs in time chunks, so the branches x time product is never
    materialised: memory stays at `n_branch x chunk`, and the number of solves
    is unchanged. `chunk` (samples) defaults to what `max_bytes` holds of one
    branches-long array; `bigblock.superpose` passes one that keeps the
    matrix and the chunk's temporaries together inside the budget.
    """
    waved = [t for t in taps if t.get("wave") is not None]
    nt = len(waved[0]["wave"])
    for t in waved:
        if len(t["wave"]) != nt:
            raise EmirError("EMIR-E905", f"tap {t['name']}: waveform length {len(t['wave'])}"
                             f" != {nt}; all taps must share one time base")
    if weights is None:
        weights = np.full(nt, 1.0 / nt)
        wnote = "uniform time weights"
    else:
        weights = np.asarray(weights, dtype=float)
        if len(weights) != nt:
            raise EmirError("EMIR-E905", f"weights length {len(weights)} != waveform "
                             f"length {nt}")
        if abs(weights.sum() - 1.0) > 1e-6:
            raise EmirError("EMIR-E905", f"time weights sum to {weights.sum():.6g}, not 1 "
                             f"-- they must normalise the window")
        wnote = "trapezoid time weights"
    need = n_branch * len(waved) * 8
    if need > max_bytes:
        raise EmirError("EMIR-E418",
            f"transfer matrix would need {need / 2**30:.1f} GiB "
            f"({n_branch} branches x {len(waved)} taps). Pre-screen with the "
            "avg map and reconstruct only the branches that could violate.")
    T = np.empty((n_branch, len(waved)))
    for j, t in enumerate(waved):
        T[:, j] = solve_branch(t, 1.0)
    W = np.array([t["wave"] for t in waved])          # taps x time
    scalars = [t for t in taps if t.get("wave") is None]
    base = np.zeros(n_branch)
    for t in scalars:                                  # DC taps ride along
        base += solve_branch(t, t["i_avg"])

    chunk = int(chunk) if chunk else max(1, int(max_bytes / 8 / max(n_branch, 1)))
    s1 = np.zeros(n_branch)
    s2 = np.zeros(n_branch)
    sa = np.zeros(n_branch)
    pk = np.zeros(n_branch)
    for i in range(0, nt, chunk):
        Ie = T @ W[:, i:i + chunk] + base[:, None]     # branches x chunk
        wc = weights[i:i + chunk]
        s1 += Ie @ wc
        s2 += (Ie * Ie) @ wc
        np.abs(Ie, out=Ie)
        sa += Ie @ wc
        np.maximum(pk, Ie.max(axis=1), out=pk)
    out = {}
    if "avg" in metrics:
        out["avg"] = np.abs(s1)
        out["absavg"] = sa            # the time average of |I| (not a checked metric)
    if "rms" in metrics:
        out["rms"] = np.sqrt(np.maximum(s2, 0.0))
    if "peak" in metrics:
        out["peak"] = pk
    note = (f"exact from {nt}-point waveforms on {len(waved)} taps, {wnote}"
            + (f" (+{len(scalars)} DC taps)" if scalars else ""))
    log(f"[emir] superposition: {note}")
    return out, note
