"""The steps of the dynamic solve against exact references (`dynir.StepSequence`):
a feature narrower than the grid's step, the trapezoidal rule's ringing after an
edge, a decap lump whose time constant the step crosses, the rms reduction of a
record sampled only at its corners, and the memory a run keeps.

Every reference is the exact periodic steady state of a one-state circuit: a
feed R from the ideal rail to the pin's node, a strap Re from it to a capacitor
C to ground, the load drawing a piecewise-linear I(t) at the pin. On each
interval of the load's knots, with I = a + b s, the capacitor's voltage is
`v = -R I + R b tau + K e^(-s/tau)` (tau = C (R + Re)), so the strap carries
`i_d = (R b tau + K e^(-s/tau)) / (R + Re)` and the feed `i_R = I - i_d`: their
squares integrate in closed form, and their peaks are at an interval's ends or
where d i_R / ds = 0. Every input is synthetic, every number invented.
"""
import json
import os

import numpy as np
import pytest
import yaml
from _helpers import emir, write

from emir.validate import _psf_tran
from test_decap import run_doc

#: every limit 1 mA/um on a 1 um wide Metal1, so a ratio reads as mA
PROC_TEXT = """pdk: decap steps test (illustrative, invented)
warn_fraction: 0.001
temp_reference: 100.0
em_tref: 100.0
layers:
  Poly: {gds: [102, 0], sheet_res: 10.0, min_width: 0.1}
  Metal1: {gds: [101, 0], sheet_res: 0.05, tc1: 0.0, tc2: 0.0, min_width: 0.1,
           jmax_avg: 1.0, jmax_rms_k: 0.2, jmax_peak: 1.0}
vias:
  Cont: {gds: [100, 0], below: Poly, above: Metal1, res: 10.0, vcwidth: 0.1,
         imax_avg: 1.0, imax_rms: 1.0, imax_peak: 1.0}
"""


def proc(tmp, **cap):
    d = yaml.safe_load(PROC_TEXT)
    if cap:
        d["cap_models"] = {"ncap": cap}
    return write(os.path.join(str(tmp), "proc.yaml"), yaml.safe_dump(d))


def seg(name, a, b, r, x0, x1):
    return (f"{name} {a} {b} {r:.9g} $metal1 $L={abs(x1 - x0):.3f} $W=1.0 $LLX={min(x0, x1)} "
            f"$LLY=-0.5 $URX={max(x0, x1)} $URY=0.5 $lvl=1")


def one_state_dspf(tmp, R, Re, C):
    """Feed Rf (VDD -> MA:s), strap Rs (MA:s -> VDD:x), Cd at VDD:x."""
    text = "\n".join([
        "*|DSPF 1.3", '*|DESIGN "one"', "*|OPERATING_TEMPERATURE 100", "*|GROUND_NET 0",
        "*|NET VDD 0.0", "*|P (VDD I *1 0.0000 0.0000)", "*|S (VDD:x 12.0000 0.0000)",
        "*|I (MA:s MA s I 0 10.0000 0.0000)",
        seg("Rf", "VDD", "MA:s", R, 0, 10), seg("Rs", "MA:s", "VDD:x", Re, 10, 12),
        f"Cd VDD:x 0 {C:.9g}", ".ENDS", ".END", ""])
    return write(os.path.join(str(tmp), "one.dspf"), text)


def mos_dspf(tmp, R, Rg, W, L):
    """Feed Rf (VDD -> MA:s), gate strap Rg to a MOS decap whose d/s sit on
    an ideal VSS (the lump: C = cox W L, R = esr_a)."""
    text = "\n".join([
        "*|DSPF 1.3", '*|DESIGN "mos"', "*|OPERATING_TEMPERATURE 100", "*|GROUND_NET 0",
        "*|NET VDD 0.0", "*|P (VDD I *1 0.0000 0.0000)",
        "*|I (MA:s MA s I 0 10.0000 0.0000)", "*|I (MD:g MD g I 0 11.0000 0.0000)",
        seg("Rf", "VDD", "MA:s", R, 0, 10), seg("Rg", "MA:s", "MD:g", Rg, 10, 11),
        "*|NET VSS 0.0", "*|P (VSS I *1 0.0000 -5.0000)",
        "*|I (MD:d MD d I 0 11.0000 -5.0000)", "*|I (MD:s MD s I 0 11.2000 -5.0000)",
        f"MD MD:d MD:g MD:s VSS ncap L={L} W={W}", ".ENDS", ".END", ""])
    return write(os.path.join(str(tmp), "mos.dspf"), text)


def exact_one_state(R, Re, C, t, I):
    """The exact periodic steady state over one period of the knots (t, I),
    I[0] == I[-1]: {"feed": (rms, peak), "strap": (rms, peak)}."""
    tau = C * (R + Re)
    h = np.diff(t)
    b = np.diff(I) / h
    a = I[:-1]
    e = np.exp(-h / tau)
    # v(s) = vp(s) + K e^(-s/tau), vp = -R (a + b s) + R b tau; v(h) chains
    phi, q = 1.0, 0.0
    for k in range(len(h)):
        vp0 = -R * a[k] + R * b[k] * tau
        vph = -R * (a[k] + b[k] * h[k]) + R * b[k] * tau
        q = vph + (q - vp0) * e[k]
        phi *= e[k]
    v = q / (1.0 - phi)
    s2d = s2f = 0.0
    pkd = pkf = 0.0
    for k in range(len(h)):
        hk, ak, bk, ek = h[k], a[k], b[k], e[k]
        K = v - (-R * ak + R * bk * tau)
        al, be = R * bk * tau / (R + Re), K / (R + Re)
        # strap: al + be e^(-s/tau)
        s2d += al * al * hk + 2 * al * be * tau * (1 - ek) + be * be * tau / 2 * (1 - ek * ek)
        pkd = max(pkd, abs(al + be), abs(al + be * ek))
        # feed: c0 + b s - be e^(-s/tau)
        c0 = ak - al
        s2f += (c0 * c0 * hk + c0 * bk * hk * hk + bk * bk * hk ** 3 / 3
                + be * be * tau / 2 * (1 - ek * ek)
                - 2 * be * (c0 * tau * (1 - ek) + bk * (tau * tau * (1 - ek) - tau * hk * ek)))
        cand = [0.0, hk]
        if be != 0 and -bk * tau / be > 0:
            sx = -tau * np.log(-bk * tau / be)
            if 0 < sx < hk:
                cand.append(sx)
        pkf = max(pkf, max(abs(c0 + bk * s - be * np.exp(-s / tau)) for s in cand))
        v = -R * (ak + bk * hk) + R * bk * tau + K * ek
    T = t[-1] - t[0]
    return {"feed": (np.sqrt(s2f / T), pkf), "strap": (np.sqrt(s2d / T), pkd)}


def periodic(tmp, ts, ys, n=4, name="load.tran"):
    """The one period (ts, ys) repeated n times, as a transient result of the
    pin MA:s (positive into the instance: drawn from the net)."""
    t = np.concatenate([ts[:-1] + k * (ts[-1] - ts[0]) for k in range(n)]
                       + [[ts[0] + n * (ts[-1] - ts[0])]])
    y = np.concatenate([ys[:-1]] * n + [[ys[-1]]])
    return _psf_tran(os.path.join(str(tmp), name), t, {"MA:s": y})


def branch(res, name, net="VDD"):
    r = res.solved[net]
    i = [x["name"] for x in r["network"].res].index(name)
    return {m: float(r["Ie"][m][i]) for m in ("avg", "rms", "peak")}


def codes(doc):
    return {x["code"] for x in doc["notices"]}


def test_the_reference_is_exact():
    """The closed form against a direct integration of the same circuit
    (10^6 implicit steps over one period of a sine): rms and peak to 1e-5."""
    R, Re, C = 10.0, 2.0, 10e-12
    T = 2 * np.pi * C * (R + Re)
    ts = np.linspace(0, T, 401)
    ys = 1e-3 + 0.8e-3 * np.cos(2 * np.pi * ts / T)
    ex = exact_one_state(R, Re, C, ts, ys)
    w = 2 * np.pi / T
    zd = Re + 1 / (1j * w * C)
    hd = abs(R / (R + zd))
    # the PWL of 400 points is the sine to 3e-5; the decap's share |R/(R + Zd)|
    assert ex["strap"][0] == pytest.approx(hd * 0.8e-3 / np.sqrt(2), rel=1e-4)
    assert ex["strap"][1] == pytest.approx(hd * 0.8e-3, rel=1e-4)


# ------------------------------------------------------------ a narrow spike


def narrow_spike(per=1e-6, spw=10e-12, coarse=2e-9, fine=0.25e-12):
    """A slow 10 mA pulse (the pin's largest current) and a 6 mA spike 10 ps
    wide at 0.6 of the period, sampled at 0.25 ps around the spike's knots
    and 2 ns elsewhere: (knots, load)."""
    base, amp, sp = 0.5e-3, 10e-3, 6e-3
    t0, tr, flat = 0.1 * per, 0.1 * per, 0.2 * per
    spt = 0.6000013 * per
    fx = [0, t0, t0 + tr, t0 + tr + flat, t0 + 2 * tr + flat, spt - spw / 2, spt,
          spt + spw / 2, per]
    fy = [base, base, base + amp, base + amp, base, base, base + sp, base, base]
    ts = set(np.round(np.arange(0, per + 1e-18, coarse), 18))
    for x in fx[5:8]:
        ts.update(np.round(x + np.arange(-8, 9) * fine, 18))
    ts = np.unique(np.concatenate([np.array([v for v in ts if 0 <= v <= per]), fx]))
    return ts, np.interp(ts, fx, fy)


def test_a_spike_narrower_than_the_step_keeps_its_strap_current(tmp_path):
    """A 1 us window (default steps of 453 ps) and a 10 ps spike that is not
    the pin's largest current: the strap of a 1 pF decap carries the spike's
    fast part, peak 4.089 mA and rms 7.867 uA exactly. The capped sub-steps
    read them 30 % and 15 % low with no code; the dyadic steps resolve the
    spike to its tip: within 0.5 % (or a refusal code)."""
    R, Re, C = 10.0, 2.0, 1e-12
    ts, ys = narrow_spike()
    ex = exact_one_state(R, Re, C, ts, ys)
    assert ex["strap"][1] * 1e3 == pytest.approx(4.0891, abs=1e-4)
    assert ex["strap"][0] * 1e6 == pytest.approx(7.8669, abs=1e-3)
    res, doc = run_doc(one_state_dspf(tmp_path, R, Re, C), "--process", proc(tmp_path),
                       "--cc-sim", periodic(tmp_path, ts, ys), "--net", "VDD",
                       "--mode", "cc-grid", "--period", 1e-6, "--dynamic",
                       "--em-basis", "decap-aware")
    n = doc["nets"][0]
    assert n["dynamic"]["step_s"] == pytest.approx(453e-12, rel=1e-3)
    if n["em_basis"]["fallback"] == "step-too-coarse":
        assert "EMIR-W434" in codes(doc)
        return
    s = branch(res, "Rs")
    assert s["peak"] == pytest.approx(ex["strap"][1], rel=5e-3)
    assert s["rms"] == pytest.approx(ex["strap"][0], rel=5e-3)
    f = branch(res, "Rf")
    assert f["peak"] == pytest.approx(ex["feed"][1], rel=5e-3)
    assert f["rms"] == pytest.approx(ex["feed"][0], rel=5e-3)
    assert n["dynamic_em"]["numerics"]["peak_retention"] >= 0.99


def test_a_negligible_spike_is_ignored_and_said(tmp_path):
    """A 2 fs, 6 mA spike (6e-18 C, 1.7e-9 of its pin's charge per period)
    is narrower than the finest sub-step the net allows: it refused
    decap-aware EM (EMIR-W434, with a --dyn-steps of 300 digits) under the
    default basis, so --fail-on-violation exited 1. Its charge and energy are below
    dynir.NEGLIGIBLE of the pin's: it is ignored and said (EMIR-W429), and
    decap-aware EM is computed, the feed exact and the strap's rms^2 within
    that share of the pin's (its own 2 fs peak is the stated exception)."""
    from emir.dynir import NEGLIGIBLE
    R, Re, C = 10.0, 2.0, 1e-12
    ts, ys = narrow_spike(spw=2e-15, fine=5e-17)
    ex = exact_one_state(R, Re, C, ts, ys)
    res, doc = run_doc(one_state_dspf(tmp_path, R, Re, C), "--process", proc(tmp_path),
                       "--cc-sim", periodic(tmp_path, ts, ys), "--net", "VDD",
                       "--mode", "cc-grid", "--period", 1e-6, "--dynamic")
    n = doc["nets"][0]
    assert "EMIR-W429" in codes(doc) and "EMIR-W434" not in codes(doc)
    assert n["em_basis"]["fallback"] is None and doc["em_basis"]["honoured"]
    ig = n["dynamic_em"]["numerics"]["features_ignored"]
    assert ig["n"] == 4                                    # one per period
    assert ig["largest"]["charge_share"] == pytest.approx(1.7e-9, rel=0.05)
    assert ig["largest"]["energy_share"] < NEGLIGIBLE
    f, s = branch(res, "Rf"), branch(res, "Rs")
    # the stated exception: the strap's 2 fs sliver (the resistive division
    # of the spike, 6 mA x 10 / 12) is not in the decap-aware peak
    assert ex["strap"][1] * 1e3 == pytest.approx(5.0, abs=1e-3)
    assert s["peak"] < 0.01 * ex["strap"][1]
    assert f["rms"] == pytest.approx(ex["feed"][0], rel=5e-3)
    assert f["peak"] == pytest.approx(ex["feed"][1], rel=5e-3)
    assert abs(s["rms"] ** 2 - ex["strap"][0] ** 2) <= NEGLIGIBLE * ex["feed"][0] ** 2


def test_a_feature_beyond_any_practical_step_gives_no_steps():
    """W434's suggestion once had 300 digits (the final review's case read
    18055169). Past
    decap.DYN_STEPS_MAX no --dyn-steps is given (a smaller one only repeats
    the refusal): the reason says so and asks."""
    from emir.decap import DYN_STEPS_MAX, step_checks
    run = {"n_steps": 2001, "h": 5e-10, "peak_retention_points": 0.0,
           "feature_need": 1e-19,
           "sub_steps": {"dropped": 0, "max_depth": 16, "finest_s": 7.6e-15}}
    why, need = step_checks(run)
    assert need is None and f"more than {DYN_STEPS_MAX}" in why and "ask the person" in why
    run["feature_need"] = 1e-15
    why, need = step_checks(run)
    assert need is not None and need <= DYN_STEPS_MAX


# ------------------------------------------------------------ ringing


def pulse_train(per=1e-6, n=10, amp=10e-3, tr=20e-12, flat=200e-12):
    """Ten 10 mA pulses with 20 ps edges in a 1 us period, sampled at 1 ps
    around each knot and 2 ns elsewhere."""
    fx, fy = [0.0], [0.5e-3]
    for k in range(n):
        s = 50e-9 + k * 90e-9
        fx += [s, s + tr, s + tr + flat, s + 2 * tr + flat]
        fy += [0.5e-3, 0.5e-3 + amp, 0.5e-3 + amp, 0.5e-3]
    fx += [per]
    fy += [0.5e-3]
    ts = set(np.round(np.arange(0, per + 1e-18, 2e-9), 18))
    for x in fx[1:-1]:
        ts.update(np.round(x + np.arange(-20, 21) * 1e-12, 18))
    ts = np.unique(np.concatenate([np.array([v for v in ts if 0 <= v <= per]), fx]))
    return ts, np.interp(ts, fx, fy)


def test_no_ringing_after_a_sub_stepped_edge(tmp_path):
    """A 2 pF decap on a 2 ohm strap (tau 24 ps) and 20 ps edges, at the
    default 144 ps step: the trapezoidal rule's first full step after the
    sub-stepped edge rang (factor (1 - h/2tau)/(1 + h/2tau) = -0.5) and read
    the feed at 12.46 mA, above the load's own 10.5 mA peak, which a passive
    RC network cannot do. The steps after a change of slope are refined and
    grow back geometrically: the feed within 0.5 % of the exact 10.4986 mA
    and never above the load's peak, the strap within 0.5 %, the feed check
    passed."""
    R, Re, C = 10.0, 2.0, 2e-12
    ts, ys = pulse_train()
    ex = exact_one_state(R, Re, C, ts, ys)
    assert ex["feed"][1] * 1e3 == pytest.approx(10.4986, abs=1e-4)
    res, doc = run_doc(one_state_dspf(tmp_path, R, Re, C), "--process", proc(tmp_path),
                       "--cc-sim", periodic(tmp_path, ts, ys), "--net", "VDD",
                       "--mode", "cc-grid", "--period", 1e-6, "--dynamic",
                       "--em-basis", "decap-aware")
    em = doc["nets"][0]["dynamic_em"]
    assert em["numerics"]["step_s"] == pytest.approx(144e-12, rel=2e-3)
    f, s = branch(res, "Rf"), branch(res, "Rs")
    assert f["peak"] <= 10.5e-3 * 1.005
    assert f["peak"] == pytest.approx(ex["feed"][1], rel=5e-3)
    assert f["rms"] == pytest.approx(ex["feed"][0], rel=5e-3)
    assert s["peak"] == pytest.approx(ex["strap"][1], rel=5e-3)
    assert s["rms"] == pytest.approx(ex["strap"][0], rel=5e-3)
    fc = em["numerics"]["feed_check"]
    assert fc["peak_ratio"] <= 1.005 and fc["rms_ratio"] <= 1.005


def test_a_step_that_rings_is_not_used(tmp_path, monkeypatch):
    """Without the ringing guard and the geometric growth (switched off
    here), the feed reads above the load's own current on the same steps:
    decap-aware EM is not computed (EMIR-W434, the message names the
    ringing) rather than taken."""
    from emir import dynir
    monkeypatch.setattr(dynir, "RING_TOL", float("inf"))
    monkeypatch.setattr(dynir, "GRADE", 10 ** 6)
    monkeypatch.setattr(dynir.StepSequence, "_grade",
                        lambda self, P: np.zeros(len(P) - 1, bool))
    R, Re, C = 10.0, 2.0, 2e-12
    ts, ys = pulse_train()
    _, doc = run_doc(one_state_dspf(tmp_path, R, Re, C), "--process", proc(tmp_path),
                     "--cc-sim", periodic(tmp_path, ts, ys), "--net", "VDD",
                     "--mode", "cc-grid", "--period", 1e-6, "--dynamic")
    eb = doc["nets"][0]["em_basis"]
    assert eb["fallback"] == "step-too-coarse" and "rang" in eb["fallback_reason"]
    assert "EMIR-W434" in codes(doc)


# ------------------------------------------------------------ folding


def spike_train(per=100e-12):
    """A 3 ps triangle of 10 mA on 0.5 mA every 100 ps, sampled at 0.1 ps
    across it and 2 ps elsewhere."""
    fx = [0, 40e-12, 41.5e-12, 43e-12, per]
    fy = [0.5e-3, 0.5e-3, 10.5e-3, 0.5e-3, 0.5e-3]
    ts = np.unique(np.concatenate([np.arange(0, per + 1e-18, 2e-12),
                                   40e-12 + np.arange(0, 31) * 0.1e-12, fx]))
    ts = ts[ts <= per + 1e-18]
    return ts, np.interp(ts, fx, fy)


@pytest.mark.parametrize("steps", [150, 180, 210])
def test_a_lump_is_not_folded_while_the_steps_resolve_it(tmp_path, steps):
    """A MOS decap of 0.3 pF with a 10 ohm ESR (its own R C = 3 ps) on a
    0.01 ohm strap, under 3 ps spikes: at 150 and 180 grid steps (4.0 and 3.4
    ps) the lump's R C was below the step, so it was folded and its ESR
    dropped while the sub-steps integrated the spike much faster, and the
    feed read 32 % low (at 210 steps, 2.9 ps, it was right). Folded only
    below a tenth of the finest step taken, the lump is kept: the feed and
    the strap within 0.5 % of the exact, at every step count."""
    R, Rg, esr, W, L = 10.0, 0.01, 10.0, 30e-6, 1e-6
    C = 0.01 * W * L
    ts, ys = spike_train()
    ex = exact_one_state(R, Rg + esr, C, ts, ys)
    res, doc = run_doc(mos_dspf(tmp_path, R, Rg, W, L),
                       "--process", proc(tmp_path, cox=0.01, esr_a=esr),
                       "--cc-sim", periodic(tmp_path, ts, ys, n=6), "--net", "VDD",
                       "--mode", "cc-grid", "--period", 100e-12, "--dynamic",
                       "--em-basis", "decap-aware", "--dyn-steps", steps)
    em = doc["nets"][0]["dynamic_em"]
    assert em is not None, doc["nets"][0]["em_basis"]
    assert em["decaps"]["folded_F"] == 0.0
    f, g = branch(res, "Rf"), branch(res, "Rg")
    assert f["peak"] == pytest.approx(ex["feed"][1], rel=5e-3)
    assert f["rms"] == pytest.approx(ex["feed"][0], rel=5e-3)
    assert g["peak"] == pytest.approx(ex["strap"][1], rel=5e-3)
    assert g["rms"] == pytest.approx(ex["strap"][0], rel=5e-3)


def test_a_folded_lump_is_named(tmp_path):
    """A lump whose access R C is far below every step (1 nohm x 0.3 pF) is
    folded onto its node, and EMIR-W438 names it with its share of the decap
    C and the ESR dropped."""
    R, Rg, W, L = 10.0, 0.01, 30e-6, 1e-6
    ts, ys = spike_train()
    _, doc = run_doc(mos_dspf(tmp_path, R, Rg, W, L),
                     "--process", proc(tmp_path, cox=0.01, esr_a=1e-9),
                     "--cc-sim", periodic(tmp_path, ts, ys, n=6), "--net", "VDD",
                     "--mode", "cc-grid", "--period", 100e-12, "--dynamic")
    em = doc["nets"][0]["dynamic_em"]
    assert em["decaps"]["folded_F"] == pytest.approx(0.3e-12)
    w = [x["message"] for x in doc["notices"] if x["code"] == "EMIR-W438"]
    assert w and "folded onto their nodes" in w[0] and "100% of the decap C" in w[0]


# ------------------------------------------------------------ the rms, like with like


def corner_triangles(per=1e-9):
    """Two triangles (10 and 6 mA on 0.5 mA, 100 ps at the base) sampled only
    at their corners: an exact PWL record whose static rms (the trapezoid of
    x^2) reads 18.6 % high."""
    fx = [0, 100e-12, 150e-12, 200e-12, 500e-12, 550e-12, 600e-12, per]
    fy = [0.5e-3, 0.5e-3, 10.5e-3, 0.5e-3, 0.5e-3, 6.5e-3, 0.5e-3, 0.5e-3]
    return np.array(fx), np.array(fy)


def test_the_rms_correction_is_like_with_like(tmp_path):
    """The step correction once divided the static rms (trapezoid of x^2 on
    the simulator's samples, which reads a corner-sampled triangle 18.6 %
    high) by the exact rms on the steps, took the convention's bias for a
    loss of the step, raised EMIR-W435 at every step count (2000 to 200000)
    and stood decap-free in: the feed read 82 % high. Like with like (each
    pin's rms exact on both), the factor is 1, no W435, and decap-aware is
    the exact current."""
    R, Re, C = 10.0, 2.0, 10e-12
    ts, ys = corner_triangles()
    ex = exact_one_state(R, Re, C, ts, ys)
    lin = np.sqrt(np.sum((ys[:-1] ** 2 + ys[:-1] * ys[1:] + ys[1:] ** 2) / 3 * np.diff(ts))
                  / (ts[-1] - ts[0]))
    for steps in (2000, 8000):
        res, doc = run_doc(one_state_dspf(tmp_path, R, Re, C), "--process", proc(tmp_path),
                           "--cc-sim", periodic(tmp_path, ts, ys), "--net", "VDD",
                           "--mode", "cc-grid", "--period", 1e-9, "--dynamic",
                           "--em-basis", "decap-aware", "--dyn-steps", steps)
        num = doc["nets"][0]["dynamic_em"]["numerics"]
        assert num["correction_max"]["rms"] < 1.001
        assert num["n_stand_in"] == 0 and "EMIR-W435" not in codes(doc)
        f, s = branch(res, "Rf"), branch(res, "Rs")
        assert f["rms"] == pytest.approx(ex["feed"][0], rel=2e-3)
        assert s["rms"] == pytest.approx(ex["strap"][0], rel=2e-3)
        # the static decap-free rms, the trapezoid of x^2, reads it 18.6 % high
        free = res.solved["VDD"]["em_free"]["Ie"]["rms"][0]
        assert free / lin - 1 == pytest.approx(0.186, abs=1e-3)


def test_a_genuine_loss_of_the_step_is_corrected_and_said(tmp_path, monkeypatch):
    """With the refinement held to one level and the features let through
    (switched off here), the steps lose a pin's AC rms: EMIR-W435 says by how
    much, the decap-aware rms is scaled up by it (so it does not read low
    against the exact), and its next step is twice the steps."""
    from emir import dynir
    monkeypatch.setattr(dynir, "MAX_DEPTH", 0)
    monkeypatch.setattr(dynir, "FEATURE_KEEP", 0.0)
    R, Re, C = 10.0, 2.0, 10e-12
    ts, ys = corner_triangles()
    ex = exact_one_state(R, Re, C, ts, ys)
    s = str(tmp_path / "s.json")
    res, doc = run_doc(one_state_dspf(tmp_path, R, Re, C), "--process", proc(tmp_path),
                       "--cc-sim", periodic(tmp_path, ts, ys), "--net", "VDD",
                       "--mode", "cc-grid", "--period", 1e-9, "--dynamic",
                       "--em-basis", "decap-aware", "--dyn-steps", 333, "--summary-out", s)
    num = doc["nets"][0]["dynamic_em"]["numerics"]
    assert num["correction_max"]["rms"] > 1.02
    assert "EMIR-W435" in codes(doc)
    assert branch(res, "Rs")["rms"] >= ex["strap"][0] * (1 - 5e-3)
    assert any("--dyn-steps 666" in x["command"] for x in doc["next_steps"])


# ------------------------------------------------------------ cost


def test_the_dynamic_result_keeps_no_waveforms(tmp_path):
    """The run keeps the dynamic solve's reductions (O(branches + nodes +
    steps)), never a resampled copy of the waveforms (taps x points: 1.5 MB a
    net on example 13's small case, 1.3 GB on a 2000-tap block)."""
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir, "examples",
                                    "13_decap_aware"))
    import decap_mesh
    case = decap_mesh.small_case(str(tmp_path / "small"))
    res, _ = run_doc(case["dspf"], "--process", case["process"], "--cc-sim", case["raw"],
                     "--net", "VDD", "--mode", "cc-grid", "--period", case["period"],
                     "--dynamic")
    r = res.solved["VDD"]
    dyn = r["dynamic"]
    nb, nn = len(r["network"].res), len(r["network"].nodes)
    limit = max(nb, nn + 64, dyn["n_steps"])

    def arrays(x, path=""):
        if isinstance(x, np.ndarray):
            yield path, x
        elif isinstance(x, dict):
            for k, v in x.items():
                yield from arrays(v, f"{path}.{k}")
        elif isinstance(x, (list, tuple)):
            for k, v in enumerate(x):
                yield from arrays(v, f"{path}[{k}]")
    big = [(p, a.shape) for p, a in arrays(dyn) if a.size > limit]
    assert not big, big


def test_evenly_spaced_samples_need_no_input_sub_steps(tmp_path):
    """A result on a fixed print step: the default steps hold every sample
    (a whole multiple of its intervals), so the grid's linear interpolation
    misses nothing and no interval is refined for the input (the old
    default, 4 x the points, put the samples between grid points and every
    edge was sub-stepped); on a mesh of 1 fF nodes the ringing guard adds
    nothing either."""
    from test_decap import mesh_pulses, mesh_text

    from emir.dynir import default_steps
    assert default_steps(2001) == 4 * 2000 + 1          # four steps an interval
    assert default_steps(241) == 9 * 240 + 1            # and at least 2000
    assert default_steps(10 ** 6) == 200_000             # at most 200000
    text, pins = mesh_text()
    d = write(tmp_path / "m.dspf", text)
    _, doc = run_doc(d, "--process", proc(tmp_path), "--cc-sim", mesh_pulses(tmp_path, pins),
                     "--mode", "cc-grid", "--period", 1e-9, "--dynamic")
    num = doc["nets"][0]["dynamic_em"]["numerics"]
    assert num["n_steps"] == 9 * 240 + 1
    assert num["sub_steps"]["added"] == 0


def test_cli_run_of_the_spike(tmp_path):
    """The narrow spike through the command line: exit 0, the run's
    numerics in the summary validate."""
    import jsonschema

    from emir import schemas
    ts, ys = narrow_spike()
    s = str(tmp_path / "s.json")
    p = emir("run", one_state_dspf(tmp_path, 10.0, 2.0, 1e-12), "--process", proc(tmp_path),
             "--cc-sim", periodic(tmp_path, ts, ys), "--net", "VDD", "--mode", "cc-grid",
             "--period", 1e-6, "--dynamic", "--summary-out", s, "--format", "json")
    assert p.returncode == 0, p.stderr[-2000:]
    doc = json.loads(p.stdout)
    jsonschema.validate(doc, schemas.get("run"))
    assert doc["em_basis"]["honoured"] is True
