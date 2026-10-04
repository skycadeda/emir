"""CC-sim ingest: simulated device currents become grid tap currents.

This is the step that turns EM from a budgeting exercise into a measurement.
A transistor circuit taps its rail at every device contact, and how much each
one pulls -- and *when* -- only a simulation knows.

The route, and why it is fast:

1. Simulate the design **CC-extracted** (capacitance only, no interconnect R).
   The rail is an ideal equipotential, so the sim is small and converges.
2. Read every element current on the supply/gnd net off that result.
3. Push them through the **R-only** extracted grid by superposition.

The mapping between the two costs nothing: the R-only DSPF's `*|I` records
already name every instance pin on the net, and the instance names match
because both netlists come from the same layout. So `save` list, tap list and
grid nodes are all generated from the same source.

**The result is pessimistic, not merely approximate.** With no interconnect R
the devices see the full rail and draw slightly more than they really would, so
CC currents bound RCC currents from above -- the correct error direction for a
screening tool.

Three things here are easy to get wrong, so they are done explicitly:

* **Non-uniform time steps.** Spectre picks its own steps. `y.mean()` over
  them is *not* the average -- it weights a densely-sampled edge as heavily as
  a long flat stretch. Everything integrates by trapezoid.
* **Whole periods.** A window that is not a whole number of periods leaves a
  residual that biases the average, and the average is exactly what EM cares
  about. `window()` aligns to periods and reports a sensitivity number so a
  bad window is visible rather than silent.
* **Sign.** Spectre's `inst:term` is positive *into the instance*, i.e. out of
  the net. Tap currents are positive *into the net*. One negation, in one
  place: `_tap_current`.
"""
from __future__ import annotations

import os
import re
import subprocess
import time

import numpy as np

from . import subproc, waves
from .errors import EmirError, notice


# --------------------------------------------------------------- integration


def trapz_weights(t: np.ndarray) -> np.ndarray:
    """Per-sample weights `w` with `sum(w * y) == integral(y dt)`.

    Carrying the weights instead of resampling keeps the simulator's own
    adaptive time steps -- which is where the accuracy is -- and makes the
    weighted reduction exact for piecewise-linear data.
    """
    t = np.asarray(t, dtype=float)
    w = np.zeros_like(t)
    dt = np.diff(t)
    w[:-1] += dt / 2.0
    w[1:] += dt / 2.0
    return w


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
    y0 = np.array([np.interp(t0, t, row) for row in y])[:, None]
    y1 = np.array([np.interp(t1, t, row) for row in y])[:, None]
    yy = np.concatenate([y0, y[:, inner], y1], axis=1)
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


def metrics_pss(freqs, X) -> dict:
    """avg / rms / peak from PSS harmonics.

    Spectre writes single-sided harmonics: `X[0]` is the DC term and `X[h]` for
    h >= 1 is the **peak amplitude** of that cosine. So

        avg  = Re(X0)
        rms^2 = |X0|^2 + (1/2) * sum_{h>=1} |X_h|^2

    (verified against the same run's time-domain output: 4 ppm agreement, where
    reading them as two-sided coefficients was 52% high.)

    `peak` needs the waveform back, so it is reconstructed on a dense grid over
    one period. That reconstruction is band-limited to the harmonics that were
    saved, so it rings at sharp edges -- `truncation` reports how much energy
    sits in the top decile of harmonics, which is the signal that more `harms`
    are needed. Prefer the PSS `td` output for peak when it exists.
    """
    X = np.asarray(X, dtype=complex)
    f = np.asarray(freqs, dtype=float)
    ac = np.abs(X[1:]) ** 2
    avg = float(X[0].real)
    rms = float(np.sqrt(abs(X[0]) ** 2 + 0.5 * ac.sum()))
    n = max(1024, 8 * len(X))
    tau = np.linspace(0.0, 1.0, n, endpoint=False)
    fund = f[1] if len(f) > 1 and f[1] > 0 else 1.0
    ph = 2j * np.pi * np.outer(tau, f[1:] / fund)
    rec = avg + np.real(np.exp(ph) @ X[1:])
    top = max(1, len(ac) // 10)
    trunc = float(ac[-top:].sum() / ac.sum()) if ac.sum() > 0 else 0.0
    return {"avg": avg, "rms": rms, "peak": float(np.abs(rec).max()),
            "truncation": trunc, "harmonics": int(len(X))}


# ---------------------------------------------------------- window selection


def detect_period(t, y, lo=None, hi=None, min_corr=0.90) -> float | None:
    """Guess the repeat period of `y` by autocorrelation of its trailing half.

    A suggestion, not an assertion: it is accepted only above `min_corr`, and
    the caller reports what it used. An explicit `--period` always wins.
    """
    t = np.asarray(t, dtype=float)
    y = np.asarray(y, dtype=float)
    if len(t) < 32:
        return None
    half = t[0] + 0.5 * (t[-1] - t[0])
    tt, yy = clip_window(t, y, half, t[-1])
    yy = yy[0]
    n = 4096
    tu = np.linspace(tt[0], tt[-1], n)
    yu = np.interp(tu, tt, yy)
    yu = yu - yu.mean()
    if not np.any(yu):
        return None
    ac = np.correlate(yu, yu, mode="full")[n - 1:]
    ac = ac / ac[0]
    span = tu[-1] - tu[0]
    lo_l = max(4, int(n * (lo / span))) if lo else 4
    hi_l = min(n // 2, int(n * (hi / span))) if hi else n // 2
    if hi_l <= lo_l:
        return None
    seg = ac[lo_l:hi_l]
    # first local maximum that clears the threshold -- the fundamental, not one
    # of its multiples, which correlate just as well
    k = None
    for i in range(1, len(seg) - 1):
        if seg[i] >= min_corr and seg[i] >= seg[i - 1] and seg[i] >= seg[i + 1]:
            k = i + lo_l
            break
    return float(k * span / (n - 1)) if k else None


def window(t, y, period=None, tol=0.02, min_periods=2, t_start=None,
           log=print) -> dict:
    """Choose the integration window: the longest settled whole-period tail.

    Walks back from the end one period at a time and keeps going while each
    earlier period's avg and rms still match the last period's within `tol`.
    That does two jobs at once -- it discards the start-up
    transient (the periods that do not match) and it guarantees a whole number
    of periods (the walk is in period steps).

    Without a period the whole post-`t_start` tail is used, and
    `sensitivity` -- how much the average moves when the window is shortened --
    becomes the number to look at. Near zero means the window is honest;
    large means the answer depends on where the window happens to stop, which
    is the silent-bias case.
    """
    t = np.asarray(t, dtype=float)
    agg = np.asarray(y, dtype=float)
    if agg.ndim > 1:
        agg = agg.sum(axis=0)
    t_end = t[-1]
    t_start = t[0] if t_start is None else float(t_start)
    note, n_per = [], None

    if period and period > 0:
        n_max = int(np.floor((t_end - t_start) / period + 1e-9))
        if n_max < 1:
            raise EmirError("EMIR-E419",
                f"period {period:g} s does not fit in the available "
                f"{t_end - t_start:g} s of data")
        ref = metrics_tran(t, agg, t_end - period, t_end)
        scale = max(abs(ref["avg"]), ref["rms"], 1e-30)
        n_per, prev_avg = 1, None
        for k in range(2, n_max + 1):
            m = metrics_tran(t, agg, t_end - k * period, t_end - (k - 1) * period)
            if (abs(m["avg"] - ref["avg"]) > tol * scale
                    or abs(m["rms"] - ref["rms"]) > tol * scale):
                prev_avg = m["avg"]      # the period that failed the match
                break
            n_per = k
        t0 = t_end - n_per * period
        if n_per < min_periods:
            note.append(f"only {n_per} settled period(s) of {n_max} available "
                        f"-- the circuit may still be settling, or tol={tol} "
                        f"is tight for it")
        # Sensitivity wants the answer from a window one period different. With
        # more than one settled period that is the shorter window; with only
        # one there is no shorter *whole-period* window, so the honest
        # comparison is the period that was rejected -- which is exactly the
        # drift that stopped the walk, and must not be hidden behind a
        # half-period probe that is not period-aligned at all.
        if n_per > 1:
            short = metrics_tran(t, agg, t0 + period, t_end)["avg"]
        elif prev_avg is not None:
            short = prev_avg
        else:
            short = metrics_tran(t, agg, t0 + 0.5 * period, t_end)["avg"]
    else:
        t0 = t_start
        note.append("no period given -- the window is not period-aligned, so "
                    "the average carries a partial-cycle residual; check "
                    "`sensitivity`")
        short = metrics_tran(t, agg, t0 + 0.1 * (t_end - t0), t_end)["avg"]

    full = metrics_tran(t, agg, t0, t_end)
    scale = max(abs(full["avg"]), full["rms"], 1e-30)
    sens = abs(short - full["avg"]) / scale
    if sens > tol:
        note.append(f"window sensitivity {sens:.1%} of scale -- the average "
                    f"moves when the window is shortened, so it is NOT a clean "
                    f"whole-period average")
    out = {"t0": float(t0), "t1": float(t_end), "period": period,
           "n_periods": n_per, "sensitivity": float(sens), "notes": note,
           "aggregate": full}
    span = f"{t0:.4g} .. {t_end:.4g} s"
    per = f"{n_per} x {period:.4g} s" if n_per else "no period"
    log(f"[emir] window {span} ({per}), sensitivity {sens:.2%}")
    for m in note:
        notice("EMIR-W413", f"window - {m}", log)
    return out


# ------------------------------------------------------------- tap extraction


def save_directives(dspf, net, extra=(), prefix="") -> str:
    """The Spectre `save` line for every element pin on `net`, or on all nets.

    Generated from the R-only DSPF, so the CC sim saves exactly what the grid
    needs and nothing else -- the alternative, `save *:currents`, is what makes
    these result files unmanageable. `prefix` is the instance
    path when the extracted subckt sits inside a testbench (e.g. `XI0.`).
    """
    nets = sorted(dspf.nets) if net is None else [net]
    names = sorted({f"{t['inst']}:{t['pin']}"
                    for n in nets for t in dspf.net_taps(n)})
    names += [n for n in extra if n not in names]
    if not names:
        raise EmirError("EMIR-E211", f"no instance pins on net {net!r} in the DSPF -- "
                         f"nothing to save", net=net)
    return "save " + " ".join(f"{prefix}{n}" for n in names)


def _tap_current(w, sig):
    """Signed current **into the net** from a Spectre terminal current.

    Spectre's `inst:term` is positive into the instance, i.e. *out of* the net.
    This negation is the only place that convention is applied.
    """
    return -np.asarray(w[sig])


def taps_from_waves(dspf, net: str, w, window_sel=None, feed=None,
                    waveforms=True, log=print) -> dict:
    """Turn a CC-sim result into tap dicts for `dspf.check_net`.

    Each returned tap carries `node` (the DSPF sub-node it attaches to), `xy`,
    `layer`, the three scalars, and -- when `waveforms` and the analysis is
    time-domain -- the windowed waveform plus the shared trapezoid `weights`,
    which is what makes the superposed rms and peak exact rather than an
    in-phase upper bound.
    """
    groups = dspf.pin_groups(net)
    if not groups:
        raise EmirError("EMIR-E211", f"no instance pins on net {net!r} in the DSPF", net=net)
    split = {k: v for k, v in groups.items() if len(v) > 1}
    if split:
        # one saved terminal current, several places it enters the net: any
        # choice of where to put it is a guess (all on one connection
        # under-reads the others), so refuse rather than pick one
        from .dspf import split_pin_message
        raise EmirError("EMIR-E417", split_pin_message(net, split), net=net,
                        dspf=getattr(dspf, "path", None),
                        next_steps=split_pin_steps(getattr(dspf, "path", None), net))
    want = {k: v[0] for k, v in groups.items()}
    found = w.match(list(want))
    missing = sorted(set(want) - set(found))
    if not found:
        raise EmirError("EMIR-E416",
            f"none of the {len(want)} instance pins on {net!r} were saved in "
            f"{w.name or 'the result'} -- add:\n  {save_directives(dspf, net)}", net=net)
    if missing:
        notice("EMIR-W403", f"{len(missing)} of {len(want)} pins on {net} not saved "
            f"(their current is treated as zero): "
            f"{', '.join(missing[:6])}{' ...' if len(missing) > 6 else ''}", log)

    names = sorted(found)
    fd = w.is_complex
    taps, weights, twin = [], None, None

    feed_m = feed_wave = None
    if fd:
        mets = {n: metrics_pss(w.x, _tap_current(w, found[n])) for n in names}
        for n in names:
            taps.append(_tap(want[n], n, mets[n]))
        # the feed returns the SUM of the tap currents: its harmonics are the
        # negated sum of theirs, so its rms/peak are exact, not |avg|
        feed_m = metrics_pss(
            w.x, -np.sum([_tap_current(w, found[n]) for n in names], axis=0))
        nh = mets[names[0]]["harmonics"]
        note = (f"PSS harmonics ({nh} saved); avg = DC term, rms by Parseval, "
                f"peak from the band-limited reconstruction")
        trunc = max(m["truncation"] for m in mets.values())
        if trunc > 0.01:
            notice("EMIR-W404", f"{trunc:.1%} of AC energy sits in the top "
                f"decile of harmonics -- rms is truncated LOW; raise `harms` "
                f"or read the PSS td output instead", log)
    else:
        Y = np.array([_tap_current(w, found[n]) for n in names])
        ws = window_sel or window(w.x, Y, log=log)
        tt, YY = clip_window(w.x, Y, ws["t0"], ws["t1"])
        wt = trapz_weights(tt)
        T = tt[-1] - tt[0]
        avg = (YY @ wt) / T
        rms = np.sqrt((YY * YY) @ wt / T)
        pk = np.abs(YY).max(axis=1)
        for j, n in enumerate(names):
            taps.append(_tap(want[n], n,
                             {"avg": float(avg[j]), "rms": float(rms[j]),
                              "peak": float(pk[j])},
                             wave=YY[j] if waveforms else None))
        if waveforms:
            weights, twin = wt / T, tt
            # whole periods the waveforms span (None: not period-aligned): a
            # periodic steady state of the window is meaningful only then
            # (`decap.solve`)
            for tp in taps:
                tp["window_periods"] = ws.get("n_periods")
        ysum = -YY.sum(axis=0)                 # what the feed must supply
        feed_m = {"avg": float(ysum @ wt / T),
                  "rms": float(np.sqrt((ysum * ysum) @ wt / T)),
                  "peak": float(np.abs(ysum).max())}
        feed_wave = ysum if waveforms else None
        note = (f"{'waveform' if waveforms else 'scalar'} currents over "
                f"{T:.4g} s"
                + (f" ({ws['n_periods']} periods)" if ws.get("n_periods") else "")
                + f", window sensitivity {ws['sensitivity']:.2%}")

    total = sum(t["i_avg"] for t in taps)
    if feed:
        node = feed if feed in dspf.node_xy else None
        if node is None:
            raise EmirError("EMIR-E206", f"feed node {feed!r} is not a node of the DSPF", net=net)
        # The feed supplies, at every instant, what the taps draw: its
        # waveform is the negated sum of theirs, so the injection balances in
        # time, not only on average. Branch currents then do not depend on
        # which node the solve grounds, and `--feed` can move the feed away
        # from the reference. (It used to carry only the average, with
        # rms = peak = |avg|; that was right only with the feed AT the
        # reference node, where a tap contributes nothing.)
        ft = {"name": f"{net}:feed", "node": node,
              "xy": dspf.node_xy[node], "layer": None,
              "i_avg": -total, "i_rms": float(feed_m["rms"]),
              "i_peak": float(feed_m["peak"]), "feed": True}
        if feed_wave is not None:
            ft["wave"] = feed_wave
            ft["window_periods"] = ws.get("n_periods")
        taps.append(ft)
    log(f"[emir] {len(taps)} taps from {w.name or 'sim'} [{w.kind}] on {net}: "
        f"net average {total * 1e3:+.4f} mA into the net")
    return {"taps": taps, "weights": weights, "time": twin, "note": note,
            "missing": missing, "total_avg": float(total),
            "window": None if fd else ws}


def split_pin_steps(path, net) -> list | None:
    """The runnable next steps of EMIR-E417 (None: the code's template)."""
    if not path:
        return None
    from .errors import step
    from .output import command_line
    return [step("see where each instance pin reaches the net, with coordinates (a "
                 "black-boxed block's pin shapes; --place matches them)",
                 command_line("pins", path, "--net", net)),
            step("plan the route: the hierarchical one (emir abstract per block, then "
                 "emir run --abstract) for black-boxed blocks, rcc for a small net",
                 command_line("next", "--dspf", path, "--net", net)),
            step("how to characterise a black-boxed block for the hierarchical run",
                 command_line("abstract", "--help"))]


def _tap(pin, signame, m, wave=None) -> dict:
    t = {"name": signame, "node": pin["node"], "xy": pin["xy"],
         "layer": pin.get("layer"), "inst": pin["inst"], "pin": pin["pin"],
         "i_avg": float(m["avg"]), "i_rms": float(m["rms"]),
         "i_peak": float(m["peak"])}
    if wave is not None:
        t["wave"] = np.asarray(wave, dtype=float)
    return t


# ---------------------------------------------------------- Spectre driver


def spectre_bin(explicit=None) -> str:
    from . import site
    return site.spectre_bin(explicit)


def run_spectre(netlist: str, out_dir=None, fmt="psfascii", extra=(),
                spectre=None, timeout=3600, log=print) -> dict:
    """Run one Spectre netlist and return where the results landed.

    Always asks for a text format: binary PSF is not readable here (see
    `waves`), and being explicit beats depending on the site default.
    """
    exe = spectre_bin(spectre)
    netlist = os.path.abspath(netlist)
    out_dir = os.path.abspath(out_dir or os.path.splitext(netlist)[0] + ".raw")
    logf = out_dir + ".log"
    cmd = [exe, netlist, "-format", fmt, "-raw", out_dir, "+escchars",
           "=log", logf, *extra]
    log(f"[emir] spectre {os.path.basename(netlist)} -> {out_dir}")
    t0 = time.time()
    try:
        # a wrapper's simulator is stopped with it (its process group)
        p = subproc.run(cmd, timeout=timeout, cwd=os.path.dirname(netlist) or ".")
    except subprocess.TimeoutExpired as e:
        raise EmirError("EMIR-E605", f"spectre ran longer than {timeout:g} s on {netlist} "
                        f"and was stopped; run it yourself and pass the result with "
                        f"--cc-sim", path=logf) from e
    dt = time.time() - t0
    tail = (p.stdout or "") + (p.stderr or "")
    if p.returncode != 0:
        err = "\n".join(l for l in tail.splitlines()
                        if re.search(r"error|fatal", l, re.I))[:2000]
        raise EmirError("EMIR-E602", f"spectre failed on {netlist} (exit {p.returncode})"
                           f"\n{err or tail[-2000:]}", path=logf)
    log(f"[emir] spectre finished in {dt:.2f} s")
    return {"raw": out_dir, "log": logf, "seconds": dt, "netlist": netlist,
            "stdout": tail}


def run_sim(netlist: str, sim: str = "spectre", **kw) -> dict:
    """Dispatch to the simulator. Only Spectre is supported in this release."""
    sim = (sim or os.environ.get("EMIR_SIM") or "spectre").lower()
    if sim != "spectre":
        raise EmirError("EMIR-E604", f"unsupported simulator {sim!r}: --netlist drives "
                         f"Spectre only (results from any simulator can still be read if "
                         f"written as PSF ASCII, nutascii, HSPICE ASCII or CSDF; `emir "
                         f"simulate` drives HSPICE, Xyce and ngspice)")
    kw.pop("corner", None)
    return run_spectre(netlist, **kw)


def cc_taps(dspf, net, result, analysis=None, period=None, feed=None,
            waveforms=True, log=print) -> dict:
    """Convenience: read a result directory/file and build the tap list.

    `result` is a path, or a `waves.Waves` already read.
    """
    w = waves.load(result, analysis=analysis)
    log(f"[emir] {w.summary()}")
    ws = None
    if not w.is_complex:
        want = set(dspf.pin_groups(net))
        found = w.match(sorted(want))
        if found:
            Y = np.array([_tap_current(w, s) for s in found.values()])
            if period is None:
                period = detect_period(w.x, Y.sum(axis=0))
                if period:
                    log(f"[emir] period auto-detected: {period:.6g} s "
                        f"({1 / period:.6g} Hz) -- override with --period")
            ws = window(w.x, Y, period=period, log=log)
    return taps_from_waves(dspf, net, w, window_sel=ws, feed=feed,
                           waveforms=waveforms, log=log)
