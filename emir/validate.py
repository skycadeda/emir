"""`emir validate` -- the analytic validation suite.

Thirteen cases, each against an answer known in closed form, so a pass is a
real cross-check of the code rather than a comparison with its own earlier
output: the DSPF path, the two limit forms design manuals print (a width
loss and Black's derating), time-weighted metrics on non-uniform samples,
window selection, PSS harmonics, CC ingest end to end, the self-consistency
fixed point, dynamic IR against an exact R||C response, decap series R,
decap lumping, RCC mode, MOS decaps, decap-aware EM against a closed form.

Every case needs only numpy / scipy / pyyaml, so none is skipped for a
missing package (`--no-skip` is kept for the scripts that pass it).

Every process number here is invented for the test -- the cases are analytic,
so the expected values follow from whatever numbers are used.

    emir validate [--out D] [--keep] [--quick] [--no-skip]
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time

import numpy as np

from .engine import Process

def _quiet(*_a, **_k):
    pass


def _proc(layers, vias=None, warn=0.8):
    return Process({"warn_fraction": warn, "em_tref": 100, "layers": layers,
                    "vias": vias or {}})


# --------------------------------------------------------------- DSPF path


STRAP_DSPF = """*|DSPF 1.5
*|DESIGN "strap"
*|OPERATING_TEMPERATURE 100
.SUBCKT strap A B
*|NET VSS 0.0
*|P (VSS 0 0 0.0000 1.0000)
*|I (MA:d MA d X 0 0.0000 1.0000)
*|I (MB:s MB s X 0 10.0000 1.0000)
*|S (VSS:1 4.0000 1.0000)
*|S (VSS:2 6.0000 1.0000)
Rw1 MA:d VSS:1 0.250000 $metal1  $L=4.0 $W=2.000000 $TYPE=10 $LLX=0.0 $LLY=0.0 $URX=4.0 $URY=2.0 $lvl=1
Rn  VSS:1 VSS:2 1.250000 $metal1  $L=2.0 $W=0.200000 $TYPE=10 $LLX=4.0 $LLY=0.9 $URX=6.0 $URY=1.1 $lvl=1
Rw2 VSS:2 MB:s 0.250000 $metal1  $L=4.0 $W=2.000000 $TYPE=10 $LLX=6.0 $LLY=0.0 $URX=10.0 $URY=2.0 $lvl=1
.ENDS
.END
"""

#: the one-metal process every DSPF-path case uses (invented numbers; no TCR,
#: and the extraction temperature equals the EM temperature)
STRAP_METAL1 = {"gds": [101, 0], "sheet_res": 0.125, "jmax_avg": 1.0,
                "tc1": 0, "tc2": 0}


def _strap_proc(**extra) -> Process:
    return Process({"warn_fraction": 0.8, "temp_reference": 100,
                    "em_tref": 100, "vias": {},
                    "layers": {"Metal1": {**STRAP_METAL1, **extra}}})


def validate_dspf(tmp: str) -> bool:
    """Necked strap through the EXTRACTION path, against the same closed form.

    Geometry only goes in (R = Rsheet x squares); the expected neck density
    J = I/w = 1 mA / 0.2 um = 5.00 mA/um is analytic, so agreement is a real
    cross-check of the DSPF reader + network solve, not a tautology, and it
    should be *exact*.
    """
    from .dspf import check_net, parse
    path = os.path.join(tmp, "_emir_strap.dspf")
    with open(path, "w", encoding="utf-8") as f:
        f.write(STRAP_DSPF)
    proc = _strap_proc()
    d = parse(path)
    I = 1e-3
    r = check_net(d, "VSS", proc, {"MA:d": I, "MB:s": -I}, "MB:s", log=_quiet)
    w = r["worst"]
    ok = (abs(w["ratio"] - 5.0) < 1e-6 and len(r["clusters"]) == 1
          and abs(r["ir_drop"] / I - 1.75) < 1e-6
          and r["kcl_residual"] < 1e-12 and r["r_at_temp"])
    print(f"{'PASS' if ok else 'FAIL'} dspf necked strap: neck "
          f"{w['ratio']:.4f}x (exp 5.0000, exact), R={r['ir_drop'] / I:.4f} ohm "
          f"(exp 1.7500), {len(r['clusters'])} cluster, KCL "
          f"{r['kcl_residual']:.1e}, r_at_temp={r['r_at_temp']}")
    os.remove(path)
    return ok


def validate_hand_forms(tmp: str) -> bool:
    """The two forms a design manual prints, as `emir process` has them typed,
    on the same necked strap: a width loss dW = 0.05 um (I_max = J x (W - dW),
    W the drawn width) and Black's derating (Ea 0.9 eV, n 2) from em_tref
    100 C to an analysis at 125 C. By hand:

        Ea / (n k) = 0.9 / (2 x 8.617333262e-5 eV/K)   = 5222.03 K
        1/398.15 K - 1/373.15 K                       = -1.68271e-4 /K
        f = exp(5222.03 x -1.68271e-4) = exp(-0.878718) = 0.415315
        neck (W 0.2 um):   1 mA / (1 mA/um x f x 0.15 um) = 16.0521x
        straps (W 2 um):   1 mA / (1 mA/um x f x 1.95 um) =  1.23477x

    and a segment no wider than dW is refused (EMIR-E323), never checked
    against a limit of zero."""
    from .dspf import check_net, parse
    from .errors import EmirError
    path = os.path.join(tmp, "_emir_strap_forms.dspf")
    with open(path, "w", encoding="utf-8") as f:
        f.write(STRAP_DSPF)
    data = {"warn_fraction": 0.8, "temp_reference": 100, "em_tref": 100, "vias": {},
            "derating": {"model": "black", "ea_eV": 0.9, "n": 2},
            "layers": {"Metal1": {**STRAP_METAL1, "width_loss_um": 0.05}}}
    proc = Process(data, temp=125.0)
    d = parse(path)
    I = 1e-3
    r = check_net(d, "VSS", proc, {"MA:d": I, "MB:s": -I}, "MB:s", log=_quiet)
    ratios = sorted((c["ratio"] for c in r["clusters"]), reverse=True)
    f = 0.41531505
    ok = (abs(proc.derate - f) < 1e-7 and len(ratios) == 3
          and abs(ratios[0] - 1 / (f * 0.15)) < 1e-5
          and abs(ratios[1] - 1 / (f * 1.95)) < 1e-6)
    refused = False
    try:
        Process({**data, "layers": {"Metal1": {**STRAP_METAL1, "width_loss_um": 0.2}}},
                temp=125.0).wire_width("Metal1", 0.2)
    except EmirError as e:
        refused = e.code == "EMIR-E323"
    ok = ok and refused
    print(f"{'PASS' if ok else 'FAIL'} hand-entry forms: derate x{proc.derate:.6f} (exp "
          f"{f:.6f}), neck {ratios[0]:.4f}x (exp 16.0521), straps {ratios[1]:.5f}x (exp "
          f"1.23477), W <= dW refused: {refused}")
    os.remove(path)
    return ok


# --------------------------------------------------------- CC-sim ingest path

#: A switching-current shape with the property that matters: almost all of the
#: waveform is quiet, almost all of the *samples* are on the spike. Breakpoints
#: as (fraction of period, mA).
SPIKE = [(0.00, 0.1), (0.45, 0.1), (0.50, 2.0), (0.55, 0.1), (1.00, 0.1)]
#: exact metrics of SPIKE, in mA, by hand:
#:   avg  = 0.1*0.9 + 1.05*0.1                          = 0.195
#:   rms^2= 0.01*0.9 + 2*0.05*(0.1^2+0.1*2+2^2)/3       = 0.1493333
SPIKE_AVG, SPIKE_RMS, SPIKE_PEAK = 0.195, 0.14933333333333335 ** 0.5, 2.0


def _spike_samples(period=1e-9, cycles=3, dense=50, sparse=5):
    """Sample SPIKE the way an adaptive simulator would: densely across the
    edges, sparsely across the flats. Breakpoints are always included, which
    is what makes the trapezoid integral of the piecewise-linear signal exact.
    """
    t, y = [], []
    for c in range(cycles):
        for (f0, v0), (f1, v1) in zip(SPIKE, SPIKE[1:]):
            n = dense if (f1 - f0) < 0.2 else sparse
            for k in range(n):
                a = k / n
                t.append((c + f0 + a * (f1 - f0)) * period)
                y.append(v0 + a * (v1 - v0))
    t.append(cycles * period)
    y.append(SPIKE[-1][1])
    return np.array(t), np.array(y) * 1e-3


def validate_metrics(tmp: str) -> bool:
    """Trapezoid vs mean-over-samples, on a waveform with an exact answer.

    This is the check that protects the single most dangerous silent error in
    the flow: a simulator picks its own time steps, so the *number of samples*
    at a value has nothing to do with the *time spent* at it.
    """
    from .ccsim import metrics_tran
    t, y = _spike_samples()
    m = metrics_tran(t, y)
    naive = float(np.mean(y)) * 1e3
    ok = (abs(m["avg"] * 1e3 - SPIKE_AVG) < 1e-9
          and abs(m["rms"] * 1e3 - SPIKE_RMS) < 1e-4
          and abs(m["peak"] * 1e3 - SPIKE_PEAK) < 1e-12)
    print(f"{'PASS' if ok else 'FAIL'} tran metrics on non-uniform samples: "
          f"avg {m['avg'] * 1e3:.6f} (exp {SPIKE_AVG:.6f}), rms "
          f"{m['rms'] * 1e3:.6f} (exp {SPIKE_RMS:.6f}), peak "
          f"{m['peak'] * 1e3:.4f} (exp {SPIKE_PEAK:.4f}) mA")
    print(f"     mean-over-samples would give {naive:.4f} mA "
          f"-- {naive / SPIKE_AVG:.1f}x the true average")
    return ok


def validate_window(tmp: str) -> bool:
    """Start-up transient plus periodic steady state, two ways.

    A fast transient must be discarded and the remaining whole-period tail must
    reproduce the exact steady-state average. A transient still decaying at the
    end of the run cannot be averaged away -- so the requirement there is not
    accuracy but *honesty*: the window must come back short and the sensitivity
    number must be loud, rather than quietly returning a biased average.
    """
    from .ccsim import metrics_tran, window
    P = 1e-9
    t, y0 = _spike_samples(period=P, cycles=8)

    fast = window(t, y0 + 2e-3 * np.exp(-t / (0.2 * P)), period=P, tol=0.02,
                  log=_quiet)
    mf = metrics_tran(t, y0 + 2e-3 * np.exp(-t / (0.2 * P)),
                      fast["t0"], fast["t1"])
    whole = abs((fast["t1"] - fast["t0"]) / P
                - round((fast["t1"] - fast["t0"]) / P)) < 1e-9
    ok_fast = (abs(mf["avg"] * 1e3 - SPIKE_AVG) < 0.02 * SPIKE_AVG
               and whole and fast["n_periods"] >= 2
               and fast["sensitivity"] < 0.02)

    slow = window(t, y0 + 2e-3 * np.exp(-t / (1.5 * P)), period=P, tol=0.02,
                  log=_quiet)
    ok_slow = slow["n_periods"] <= 2 and slow["sensitivity"] > 0.02 \
        and any("sensitivity" in n for n in slow["notes"])

    ok = ok_fast and ok_slow
    print(f"{'PASS' if ok else 'FAIL'} window selection: fast transient -> "
          f"kept {fast['n_periods']} of 8 periods, avg {mf['avg'] * 1e3:.4f} mA "
          f"(steady state {SPIKE_AVG:.4f}), sensitivity "
          f"{fast['sensitivity']:.2%}")
    print(f"     slow transient -> kept {slow['n_periods']}, sensitivity "
          f"{slow['sensitivity']:.1%} and warned (never silently averaged)")
    return ok


def validate_pss(tmp: str) -> bool:
    """PSS harmonics -> avg/rms, against a waveform whose harmonics are known.

    Spectre writes single-sided harmonics (h>=1 is the peak amplitude of that
    cosine); reading them as two-sided coefficients inflates rms by up to
    sqrt(2)x per harmonic, which is why this convention is pinned by a test.
    """
    from .ccsim import metrics_pss
    # 1 + 0.6*cos(wt) + 0.2*cos(2wt):  avg = 1, rms^2 = 1 + 0.18 + 0.02
    X = np.array([1.0, 0.6, 0.2], dtype=complex)
    f = np.array([0.0, 1e9, 2e9])
    m = metrics_pss(f, X)
    exp_rms = (1.0 + 0.6 ** 2 / 2 + 0.2 ** 2 / 2) ** 0.5
    exp_peak = 1.8
    ok = (abs(m["avg"] - 1.0) < 1e-12 and abs(m["rms"] - exp_rms) < 1e-12
          and abs(m["peak"] - exp_peak) < 1e-3)
    print(f"{'PASS' if ok else 'FAIL'} PSS harmonics: avg {m['avg']:.6f} "
          f"(exp 1.0), rms {m['rms']:.6f} (exp {exp_rms:.6f}), peak "
          f"{m['peak']:.4f} (exp {exp_peak:.4f})")
    return ok


def _psf_tran(path, t, sigs):
    """Write a minimal PSF ASCII transient file (also round-trips the reader)."""
    L = ['HEADER', '"PSFversion" "1.00"', '"analysis type" "tran"',
         '"analysis name" "tr"', 'TYPE',
         '"I" FLOAT DOUBLE PROP(', '"units" "A"', ')',
         'SWEEP', '"time" "sweep" PROP(', '"units" "s"', ')', 'TRACE']
    for n in sigs:
        L += [f'"{n}" "I" PROP(', '"units" "A"', ')']
    L.append("VALUE")
    for i, ti in enumerate(t):
        L.append(f'"time" {ti:.15e}')
        for n, v in sigs.items():
            L.append(f'"{n}" {v[i]:.15e}')
    L.append("END")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    return path


def validate_cc_ingest(tmp: str) -> bool:
    """CC currents -> taps -> grid -> EM, end to end, against a closed form.

    Same necked strap as `validate_dspf`, but the current now arrives the way
    the real flow delivers it: as a simulated terminal-current waveform, in
    Spectre's sign convention, matched to grid nodes by instance-pin name.
    Because the strap is a series chain, every branch must carry exactly the
    tap waveform, so all three metrics have exact expected values -- which
    makes this a test of the *sign*, the *mapping*, the *time weighting* and
    the *superposition* at once.

    The process deliberately carries **no rms limit**. All three metrics must
    still come back as correct values: an unlimited metric means "no verdict",
    never "here is the average wearing the rms label".
    """
    from .ccsim import cc_taps
    from .dspf import check_net, parse
    dpath = os.path.join(tmp, "_emir_cc_strap.dspf")
    with open(dpath, "w", encoding="utf-8") as f:
        f.write(STRAP_DSPF)
    d = parse(dpath)

    t, y = _spike_samples(period=1e-9, cycles=4)
    # Spectre convention: inst:term is positive INTO the instance. MA sources
    # current into the net, so its terminal current is negative.
    rpath = _psf_tran(os.path.join(tmp, "_emir_cc.tran"), t,
                      {"MA:d": -y, "MB:s": y})

    proc = _strap_proc(jmax_peak=20.0)
    ing = cc_taps(d, "VSS", rpath, period=1e-9, log=_quiet)
    taps = ing["taps"]
    ma = next(t for t in taps if t["name"] == "MA:d")
    inj = {}
    for tp in taps:
        inj[tp["node"]] = inj.get(tp["node"], 0.0) + tp["i_avg"]
    r = check_net(d, "VSS", proc, inj, "MB:s", taps_list=taps,
                  tap_weights=ing["weights"], log=_quiet)
    neck = [i for i, rr in enumerate(r["network"].res) if rr["name"] == "Rn"][0]

    got = {m: float(r["Ie"][m][neck]) * 1e3 for m in ("avg", "rms", "peak")}
    exp = {"avg": SPIKE_AVG, "rms": SPIKE_RMS, "peak": SPIKE_PEAK}
    sign_ok = ma["i_avg"] > 0                       # MA sources INTO the net
    ok = sign_ok and all(abs(got[m] - exp[m]) < 1e-3 * exp[m] for m in exp)
    print(f"{'PASS' if ok else 'FAIL'} cc ingest -> grid: neck current "
          f"avg {got['avg']:.5f} rms {got['rms']:.5f} peak {got['peak']:.5f} mA "
          f"(exp {exp['avg']:.5f} / {exp['rms']:.5f} / {exp['peak']:.5f}), "
          f"tap sign {'into net' if sign_ok else 'WRONG'}")
    for p in (dpath, rpath):
        os.remove(p)
    return ok


def validate_selfconsist(tmp: str) -> bool:
    """The self-consistency loop against a load whose fixed point is exact.

    Replace the transistors with a plain resistor and the answer is closed
    form: a load `R_load` fed through grid resistance `R_grid` from a nominal
    rail settles at `I = V_nom / (R_load + R_grid)`, not at `V_nom / R_load`.
    The simulator is stubbed out, so what is under test is the loop itself --
    the sign of the correction and the fixed point it lands on.

    Both rail polarities are exercised. The correction is the pin's droop
    `V[node]`, relative to the feed and added to the rail, which must produce
    a *drop* on a supply net and a *rise* on a ground net from the same line
    of code. The loop is the waveform loop (`emir.tdloop`): the stub reads the
    droop waveform it is fed, as a simulator reads the PWL source.
    """
    from .dspf import parse
    from .selfconsist import converge
    dpath = os.path.join(tmp, "_emir_sc.dspf")
    with open(dpath, "w", encoding="utf-8") as f:
        f.write(STRAP_DSPF)
    d = parse(dpath)
    proc = _strap_proc()
    R_GRID, R_LOAD = 1.75, 100.0
    dro = os.path.join(tmp, "_emir_sc_droop.scs")
    rawp = os.path.join(tmp, "_emir_sc.tran")
    ok, lines = True, []

    for tag, v_nom, sgn in (("supply", 1.0, +1.0), ("ground", 0.0, -1.0)):
        def _stub(netlist, npass, fed, _s=sgn, _v=v_nom):
            # an ohmic load across the rail: the whole point is that it reacts
            # to the corrected rail voltage the loop hands it (the pin's
            # supply is the rail plus its droop)
            t = np.linspace(0, 1e-9, 21)
            dv = 0.0 if fed["t"] is None else np.interp(t, fed["t"], fed["v"]["MA:d"])
            i = _s * np.abs(_v + dv - (_v - _s)) / R_LOAD
            _psf_tran(rawp, t, {"MA:d": np.full_like(t, i),
                                "MB:s": np.full_like(t, -i)})
            return {"raw": rawp, "seconds": 0.0}

        cv = converge("(stub)", dro, d, "VSS", proc, "MB:s", v_nom,
                      period=1e-9, max_passes=6, tol_mV=1e-4, tol_rel=1e-6,
                      simulate=_stub, log=_quiet)
        i_got = abs(cv["result"]["Ie"]["avg"][
            [j for j, r in enumerate(cv["network"].res) if r["name"] == "Rn"][0]])
        i_exp = 1.0 / (R_LOAD + R_GRID)
        corr = float(cv["result"]["V"][cv["network"].idx["MA:d"]])
        right_way = (corr < 0) if sgn > 0 else (corr > 0)
        good = abs(i_got - i_exp) < 1e-4 * i_exp and right_way and cv["converged"]
        ok = ok and good
        lines.append(f"     {tag:7s} I {i_got * 1e3:.6f} mA (exact "
                     f"{i_exp * 1e3:.6f}), tap correction "
                     f"{corr * 1e3:+.3f} mV in {len(cv['passes'])} passes")
    print(f"{'PASS' if ok else 'FAIL'} self-consistency fixed point "
          f"(V_nom/(R_load+R_grid), not V_nom/R_load):")
    for m in lines:
        print(m)
    for p in (dpath, dro, rawp):
        if os.path.exists(p):
            os.remove(p)
    return ok


# ------------------------------------------------------------- dynamic IR

#: One rail resistance and one wire capacitance -- the smallest network whose
#: frequency response is exactly known.
RC_DSPF = """*|DSPF 1.3
*|DESIGN "rc1"
*|OPERATING_TEMPERATURE 100
*|NET VDD 1.0e-12
*|P (VDD I *1 0.0000 0.0000)
*|I (MA:s MA s I 0 10.0000 0.0000)
Rw VDD MA:s 100.000000 $metal1  $L=10.0 $W=1.000000 $LLX=0.0 $LLY=-0.5 $URX=10.0 $URY=0.5 $lvl=1
CL MA:s 0 1.0e-12
.ENDS
.END
"""

RC_R, RC_C = 100.0, 1.0e-12          # tau = 100 ps


def _rc_net(tmp):
    from .dspf import Network, parse
    path = os.path.join(tmp, "_emir_rc.dspf")
    with open(path, "w", encoding="utf-8") as f:
        f.write(RC_DSPF)
    d = parse(path)
    os.remove(path)
    return d, Network(d, "VDD")


def _sine_amplitude(nw, d, freq, i0, lumps=None, periods=20, n=8000,
                    n_steps=80000):
    """Drive one tap with a sinusoid and return the settled amplitude at it.

    Only the final period is measured. Enough periods run first that the
    network's own natural response -- including a decap branch's, which is the
    slowest thing here -- has decayed below the tolerance, so what is compared
    is the steady state and nothing else.
    """
    from .dynir import grounded_caps, transient
    T = periods / freq
    t = np.linspace(0.0, T, n)
    wave = i0 * np.sin(2 * np.pi * freq * t)
    tap = {"name": "MA:s", "node": "MA:s", "xy": (10.0, 0.0), "layer": None,
           "i_avg": 0.0, "i_rms": i0 / np.sqrt(2), "i_peak": i0, "wave": wave}
    C, _ = grounded_caps(d, "VDD", nw.idx, log=_quiet)
    r = transient(nw, "VDD", [tap], t, node_c=C, lumps=lumps,
                  n_steps=n_steps, probes=["MA:s"], log=_quiet)
    tail = r["trace"][0][int((1 - 1.0 / periods) * n_steps):]
    return float(np.abs(tail).max()), r


def validate_dynamic_ir(tmp: str) -> bool:
    """The RC transient against the network's exact frequency response.

    A sinusoid into one node of `R || C` settles at `|I| * |R || 1/(jwC)|`.
    Reading the settled amplitude rather than the run's global maximum keeps
    the check clean of the start-up transient, and testing at `wRC = 1` puts
    it exactly where R and C contribute equally -- a wrong capacitance or a
    wrong integrator cannot hide there.
    """
    d, nw = _rc_net(tmp)
    i0 = 1e-3
    ok, lines = True, []
    for tag, wrc in (("wRC=0.1", 0.1), ("wRC=1", 1.0), ("wRC=10", 10.0)):
        f = wrc / (2 * np.pi * RC_R * RC_C)
        got, r = _sine_amplitude(nw, d, f, i0)
        z = 1.0 / (1.0 / RC_R + 1j * 2 * np.pi * f * RC_C)
        exp = i0 * abs(z)
        err = abs(got - exp) / exp
        ok = ok and err < 5e-3
        lines.append(f"     {tag:8s} f={f / 1e9:6.3f} GHz  amplitude "
                     f"{got * 1e3:8.4f} mV (exact {exp * 1e3:8.4f}, "
                     f"{err:.2%})")
    print(f"{'PASS' if ok else 'FAIL'} dynamic IR vs the exact R||C response:")
    for m in lines:
        print(m)
    return ok


def validate_decap_series_r(tmp: str) -> bool:
    """A decap is only as good as its access resistance -- shown, not asserted.

    The claim is that modelling decap as pure C overstates how much droop it
    kills. Here the whole branch has a closed form, so both halves are
    checkable: the lumped `R_eff + C_eff` branch must reproduce
    `R || (1/jwC_wire) || (R_acc + 1/jwC_d)` exactly, AND dropping `R_acc`
    must predict materially less droop than the truth.
    """
    d, nw = _rc_net(tmp)
    i0, cd = 1e-3, 20e-12
    f = 1.0 / (2 * np.pi * RC_R * RC_C)          # wRC = 1
    w = 2 * np.pi * f

    def exact(racc):
        y = (1.0 / RC_R + 1j * w * RC_C
             + 1.0 / (racc + 1.0 / (1j * w * cd)))
        return i0 * abs(1.0 / y)

    out, ok = [], True
    for tag, racc in (("ideal decap (R_acc~0)", 1e-3), ("real (R_acc=50)", 50.0)):
        got, _ = _sine_amplitude(nw, d, f, i0,
                                 lumps=[{"node": "MA:s", "c": cd, "r": racc}])
        exp = exact(racc)
        err = abs(got - exp) / exp
        ok = ok and err < 5e-3
        out.append((tag, got, exp, err))
    ideal, real = out[0][1], out[1][1]
    overstated = real / ideal
    ok = ok and overstated > 1.05          # the whole point: R_acc matters
    print(f"{'PASS' if ok else 'FAIL'} decap series R (a pure-C lump "
          f"overstates suppression):")
    for tag, got, exp, err in out:
        print(f"     {tag:24s} droop {got * 1e3:7.4f} mV "
              f"(exact {exp * 1e3:7.4f}, {err:.2%})")
    print(f"     dropping R_acc would under-predict droop by "
          f"{100 * (1 - ideal / real):.1f}% -- which is why it is not optional")
    return ok


def validate_decap_lumping(tmp: str) -> bool:
    """Geometric reduction must conserve charge and combine access in parallel."""
    from .dynir import lump_decap
    xy = {f"n{i}": (float(i), 0.0) for i in range(8)}
    idx = {k: i for i, k in enumerate(xy)}
    items = [{"xy": (float(i), 0.0), "c": (i + 1) * 1e-15, "r": 40.0}
             for i in range(8)]
    lumps, note = lump_decap(items, xy, idx, max_lumps=4, log=_quiet)
    c_in = sum(it["c"] for it in items)
    c_out = sum(lp["c"] for lp in lumps)
    # each bin's access paths are in parallel: n identical 40 ohm -> 40/n
    r_ok = all(abs(lp["r"] - 40.0 / lp["n"]) < 1e-9 for lp in lumps)
    ok = abs(c_out - c_in) < 1e-21 and r_ok and len(lumps) <= 4
    print(f"{'PASS' if ok else 'FAIL'} decap lumping: {len(items)} -> "
          f"{len(lumps)} lumps, C {c_out * 1e15:.3f} fF conserved "
          f"(in {c_in * 1e15:.3f}), access R combined in parallel")
    return ok


# --------------------------------------------------------------- RCC mode

#: Ra and Rb in parallel put one genuine loop in the network, which is what
#: makes the off-tree consistency check non-vacuous.
LOOP_DSPF = """*|DSPF 1.3
*|DESIGN "loop"
*|OPERATING_TEMPERATURE 100
*|NET VDD 0.0
*|P (VDD I *1 0.0000 0.0000)
*|S (VDD:1 5.0000 0.0000)
*|I (MA:s MA s I 0 10.0000 0.0000)
Ra VDD VDD:1 1.000000 $metal1  $L=5.0 $W=1.000000 $LLX=0.0 $LLY=0.0 $URX=5.0 $URY=1.0 $lvl=1
Rb VDD VDD:1 3.000000 $metal1  $L=5.0 $W=1.000000 $LLX=0.0 $LLY=-1.0 $URX=5.0 $URY=0.0 $lvl=1
Rc VDD:1 MA:s 2.000000 $metal1  $L=5.0 $W=1.000000 $LLX=5.0 $LLY=0.0 $URX=10.0 $URY=1.0 $lvl=1
.ENDS
.END
"""


def validate_rcc_mode(tmp: str) -> bool:
    """Node voltages rebuilt from branch currents, and the check that guards it.

    RCC mode never saves node voltages -- it walks a spanning tree from the
    reference node accumulating `I*R`. Here the same network is solved
    directly, so the reconstruction has an exact target.

    The second half is the part that earns its keep. Every branch the tree did
    *not* use independently predicts a potential difference the tree already
    fixed, so corrupting one current must show up in `loop_residual`. That is
    the only signal RCC mode has that a netlist was paired with the wrong DSPF
    or a save was misread -- a grid solve would have produced a plausible
    number instead.
    """
    from .dspf import Network, parse
    from .rcc import internal_kcl, loop_residual, node_voltages, spanning_order
    path = os.path.join(tmp, "_emir_loop.dspf")
    with open(path, "w", encoding="utf-8") as f:
        f.write(LOOP_DSPF)
    d = parse(path)
    nw = Network(d, "VDD")
    I = 1e-3
    V, Ib = nw.solve({"MA:s": -I}, "VDD")

    steps, tree, unreached = spanning_order(nw, "VDD")
    Vr = node_voltages(nw, steps, Ib)
    n_off = int((~tree).sum())
    v_err = float(np.abs(Vr - V).max())
    resid = loop_residual(nw, steps, Ib, tree=tree)
    kcl = internal_kcl(nw, d, "VDD", Ib, "VDD")

    # 1 || 3 = 0.75, in series with 2 -> 2.75 ohm end to end
    r_end = abs(Vr[nw.idx["MA:s"]]) / I
    bad = Ib.copy()
    bad[[r["name"] for r in nw.res].index("Rb")] *= 1.1      # 10% corruption
    caught = loop_residual(nw, steps, bad, tree=tree)

    ok = (not unreached and n_off == 1 and v_err < 1e-15 and resid < 1e-15
          and kcl < 1e-15 and abs(r_end - 2.75) < 1e-12
          and caught > 1e-2 * abs(Vr).max())
    print(f"{'PASS' if ok else 'FAIL'} rcc mode: voltages rebuilt from branch "
          f"currents match the solve to {v_err:.1e} V, R_end {r_end:.6f} ohm "
          f"(exact 2.750000)")
    print(f"     loop residual {resid:.1e} V over {n_off} off-tree branch, "
          f"internal KCL {kcl:.1e} A; a 10% error on one branch is caught at "
          f"{caught * 1e3:.4f} mV")
    os.remove(path)
    return ok


#: A rail with a MOS decap across it: MD's gate on VDD, drain/source/bulk on
#: VSS. That is a capacitor, not a transistor, and it is a *device* line -- so
#: nothing that only reads `C` records will ever see it. `nmos_demo` is an
#: invented model name.
DECAP_DSPF = """*|DSPF 1.3
*|DESIGN "decap"
*|OPERATING_TEMPERATURE 100
*|NET VDD 0.0
*|P (VDD I *1 0.0000 0.0000)
*|S (VDD:1 10.0000 0.0000)
*|I (MD:g MD g I 0 10.0000 0.0000)
*|I (MX:d MX d I 0 12.0000 0.0000)
Rw VDD VDD:1 100.000000 $metal1  $L=10.0 $W=1.000000 $lvl=1
Rg VDD:1 MD:g 0.001000 $metal1  $L=0.01 $W=1.000000 $lvl=1
Rx VDD:1 MX:d 0.001000 $metal1  $L=0.01 $W=1.000000 $lvl=1
*|NET VSS 0.0
*|P (VSS I *1 0.0000 -5.0000)
*|S (VSS:1 10.0000 -5.0000)
*|I (MD:d MD d I 0 10.0000 -5.0000)
*|I (MD:s MD s I 0 10.2000 -5.0000)
*|I (MX:s MX s I 0 12.0000 -5.0000)
Rv VSS VSS:1 10.000000 $metal1  $L=10.0 $W=1.000000 $lvl=1
Rd VSS:1 MD:d 0.001000 $metal1  $L=0.01 $W=1.000000 $lvl=1
Rs VSS:1 MD:s 0.001000 $metal1  $L=0.01 $W=1.000000 $lvl=1
Rxs VSS:1 MX:s 0.001000 $metal1  $L=0.01 $W=1.000000 $lvl=1
MD MD:d MD:g MD:s VSS nmos_demo L=2e-07 W=1e-04
MX MX:d SIG MX:s VSS nmos_demo L=2e-07 W=1e-06
.ENDS
.END
"""

#: invented MOS-decap coefficients: gate oxide capacitance per area (F/m^2,
#: i.e. 10 fF/um^2) and channel sheet resistance in inversion (ohm/square)
DEMO_COX, DEMO_RON_SQ = 1.0e-2, 5000.0


def validate_mos_decap(tmp: str) -> bool:
    """MOS decaps: found, sized, and not confused with ordinary transistors.

    A MOS decap is a transistor -- gate on one rail, drain/source/bulk on the
    other -- so it appears as an `M` device line and is invisible to anything
    that only reads `C` records. It is also frequently the largest capacitance
    on a power net, and omitting it *overstates* droop, which looks safe.

    `MD` is a decap (gate VDD, body VSS). `MX` has its gate on a signal, so it
    is an ordinary device and must NOT be counted -- otherwise every transistor
    in the design would be mistaken for decoupling.
    """
    from .dspf import Network, mos_decaps, parse
    from .dynir import ACCESS_DIVISOR, mos_decap_lumps
    path = os.path.join(tmp, "_emir_decap.dspf")
    with open(path, "w", encoding="utf-8") as f:
        f.write(DECAP_DSPF)
    d = parse(path)
    COX, RON_SQ = DEMO_COX, DEMO_RON_SQ
    proc = Process({"warn_fraction": 0.8, "temp_reference": 100,
                    "em_tref": 100, "vias": {},
                    "cap_models": {"nmos_demo": {"cox": COX, "ron_sq": RON_SQ}},
                    "layers": {"Metal1": dict(STRAP_METAL1)}})
    found = mos_decaps(d, "VDD")
    nw = Network(d, "VDD")
    lumps, note = mos_decap_lumps(d, "VDD", proc, idx=nw.idx, log=_quiet)
    W, L = 1e-4, 2e-7
    exp_c = COX * W * L
    exp_r = (RON_SQ * L / W) / ACCESS_DIVISOR
    got_c = sum(x["c"] for x in lumps)
    got_r = lumps[0]["r"] if lumps else 0.0

    # with no model entry it must be excluded LOUDLY, never zeroed quietly
    bare = Process({**proc.data, "cap_models": {}})
    msgs = []
    mos_decap_lumps(d, "VDD", bare, idx=nw.idx, log=lambda m: msgs.append(m))
    warned = any("WARNING" in m and "OVERSTATES" in m for m in msgs)

    ok = (len(found) == 1 and found[0]["name"] == "MD" and len(lumps) == 1
          and abs(got_c - exp_c) < 1e-9 * exp_c
          and abs(got_r - exp_r) < 1e-9 * exp_r and warned)
    print(f"{'PASS' if ok else 'FAIL'} MOS decap: found {len(found)} of 2 "
          f"devices ({', '.join(m['name'] for m in found)}); C "
          f"{got_c * 1e15:.3f} fF (Cox*W*L = {exp_c * 1e15:.3f}), access R "
          f"{got_r:.4f} ohm (R_channel/{ACCESS_DIVISOR:g} = {exp_r:.4f})")
    print(f"     ordinary device MX excluded; unmodelled decap warned about "
          f"rather than silently zeroed: {'yes' if warned else 'NO'}")
    os.remove(path)
    return ok



# --------------------------------------------------- decap-aware EM (--dynamic)

#: the R || C node of `RC_DSPF` with a MOS decap on it: gate MA:s, d/s/b on VSS
#: (a net with a port and no resistance, so its plate is an ideal AC ground)
DECAP_EM_DSPF = RC_DSPF.replace(".ENDS\n.END\n", """*|NET VSS 0.0
*|P (VSS I *1 0.0000 -5.0000)
*|I (MD:d MD d I 0 10.0000 -5.0000)
*|I (MD:s MD s I 0 10.2000 -5.0000)
MD MD:d MA:s MD:s VSS ncap_demo L=2e-07 W=1e-04
.ENDS
.END
""")


def validate_decap_aware(tmp: str) -> bool:
    """Decap-aware EM against the closed form of a feed R into a node with
    wire C and a decap, driven by DC + a sinusoid.

    R = 100 ohm, C_w = 1 pF, the decap C_d = cox W L = 20 pF behind
    R_e = 50 ohm (`cap_models` esr_a); the load I(t) = 1 mA + 0.8 mA cos(wt).
    With Z_d = R_e + 1/(jwC_d), Z_sh = 1/(jwC_w) || Z_d, H = Z_sh/(R + Z_sh):
    the feed segment carries rms sqrt(I_dc^2 + |H|^2 I_ac^2/2) and peak
    I_dc + |H| I_ac, the decap I_ac |(R || Z_sh)/Z_d| / sqrt(2) rms. At
    wR(C_w + C_d) = 1: 1.06078 and 1.50049 mA, decap 0.30431 mA; at 3:
    1.02375 and 1.31004 mA, decap 0.35920 mA; decap-free (no decap current):
    1.14891 and 1.8 mA. The branch averages obey `G Vbar = Ibar - C (V_N -
    V_0)/T` (checked to 1e-10) and the avg in every basis is the static one.
    """
    import argparse

    from .bigblock import cc_dynamic
    from .ccsim import trapz_weights
    from .dspf import check_net, parse
    path = os.path.join(tmp, "_emir_decap_em.dspf")
    with open(path, "w", encoding="utf-8") as f:
        f.write(DECAP_EM_DSPF)
    d = parse(path)
    proc = Process({"warn_fraction": 0.8, "temp_reference": 100, "em_tref": 100, "vias": {},
                    "cap_models": {"ncap_demo": {"cox": 1.0, "esr_a": 50.0}},
                    "layers": {"Metal1": dict(STRAP_METAL1)}})
    R, cw, cd, idc, iac = RC_R, RC_C, 20e-12, 1e-3, 0.8e-3
    ok, lines = True, []
    for wrc in (1.0, 3.0):
        w = wrc / (R * (cw + cd))
        t = np.linspace(0.0, 4 * 2 * np.pi / w, 801)
        load = idc + iac * np.cos(w * t)
        wt = trapz_weights(t) / (t[-1] - t[0])
        rms_free = float(np.sqrt(np.sum(wt * load * load)))
        taps = [{"name": "MA:s", "node": "MA:s", "xy": (10.0, 0.0), "layer": None,
                 "i_avg": -idc, "i_rms": rms_free, "i_peak": float(load.max()),
                 "wave": -load, "window_periods": 4},
                {"name": "VDD:feed", "node": "VDD", "xy": (0.0, 0.0), "layer": None,
                 "i_avg": idc, "i_rms": rms_free, "i_peak": float(load.max()),
                 "wave": load, "feed": True, "window_periods": 4}]
        r = check_net(d, "VDD", proc, {"MA:s": -idc, "VDD": idc}, "VDD", taps_list=taps,
                      tap_weights=wt, log=_quiet)
        a = argparse.Namespace(em_basis="decap-aware", dynamic=True, dyn_steps=None,
                               argv=None, max_memory=None, superposition="auto",
                               power_nets=None, mode="cc-grid")
        cc_dynamic(d, "VDD", r, "VDD", taps, t, proc, a, log=_quiet)
        em = r["dynamic_em"]
        zd = 50.0 + 1 / (1j * w * cd)
        zsh = 1 / (1j * w * cw + 1 / zd)
        h = abs(zsh / (R + zsh))
        ex = {"rms": np.sqrt(idc ** 2 + h ** 2 * iac ** 2 / 2), "peak": idc + h * iac,
              "dec": iac * abs((1 / (1 / R + 1 / zsh)) / zd) / np.sqrt(2)}
        got = {"rms": em["feed"]["i_rms_A"]["decap-aware"],
               "peak": em["feed"]["i_peak_A"]["decap-aware"],
               "dec": em["decaps"]["top"][0]["i_rms_A"]}
        err = max(abs(got[k] / ex[k] - 1) for k in ex)
        ident = em["numerics"]["avg_identity_residual"]
        same_avg = bool(np.array_equal(r["Ie"]["avg"], r["em_free"]["Ie"]["avg"]))
        ok = ok and err < 1e-3 and ident < 1e-10 and same_avg \
            and em["numerics"]["avg_check"]["passed"]
        lines.append(f"     wRC={wrc:g}: feed rms {got['rms'] * 1e3:.5f} mA (exact "
                     f"{ex['rms'] * 1e3:.5f}), peak {got['peak'] * 1e3:.5f} (exact "
                     f"{ex['peak'] * 1e3:.5f}), decap rms {got['dec'] * 1e3:.5f} (exact "
                     f"{ex['dec'] * 1e3:.5f}); worst error {err:.1e}; decap-free "
                     f"{em['feed']['i_rms_A']['decap-free'] * 1e3:.5f} / "
                     f"{em['feed']['i_peak_A']['decap-free'] * 1e3:.5f} mA; avg identity "
                     f"{ident:.1e}")
    print(f"{'PASS' if ok else 'FAIL'} decap-aware EM vs the closed form (feed R, wire C, "
          f"decap with series R):")
    for m in lines:
        print(m)
    os.remove(path)
    return ok


# ------------------------------------------------------------------ driver

#: (name, function, kind, slow). Every case is "core" (numpy, scipy, pyyaml
#: only); slow cases are the transient ones `--quick` leaves out.
CASES = [
    ("dspf", validate_dspf, "core", False),
    ("hand-entry forms", validate_hand_forms, "core", False),
    ("tran metrics", validate_metrics, "core", False),
    ("window", validate_window, "core", False),
    ("pss", validate_pss, "core", False),
    ("cc ingest", validate_cc_ingest, "core", False),
    ("self-consistency", validate_selfconsist, "core", False),
    ("dynamic IR", validate_dynamic_ir, "core", True),
    ("decap series R", validate_decap_series_r, "core", True),
    ("decap lumping", validate_decap_lumping, "core", False),
    ("rcc mode", validate_rcc_mode, "core", False),
    ("mos decap", validate_mos_decap, "core", False),
    ("decap-aware EM", validate_decap_aware, "core", True),
]


def run_case(name: str, fn, out: str) -> tuple[bool, str]:
    """Run one case; `(passed, message)`. A raised assertion is a FAIL with its
    text, any other exception a FAIL with its type -- never a crash of the suite."""
    try:
        ok = fn(out)
    except AssertionError as e:
        msg = f"FAIL {name}: {e}"
        print(msg)
        return False, msg
    except Exception as e:                       # noqa: BLE001
        msg = f"FAIL {name}: {type(e).__name__}: {e}"
        print(msg)
        return False, msg
    return (ok is not False), ""


def run(out_dir: str, quick: bool = False) -> dict:
    """Run the suite into `out_dir`. Returns {"passed", "failed", "skipped",
    "seconds"} with case names (and, for skips and failures, the reason)."""
    os.makedirs(out_dir, exist_ok=True)
    res = {"passed": [], "failed": [], "skipped": [], "seconds": {}}
    for name, fn, _kind, slow in CASES:
        if quick and slow:
            print(f"SKIP {name}: --quick")
            res["skipped"].append((name, "--quick"))
            continue
        t0 = time.time()
        ok, msg = run_case(name, fn, out_dir)
        res["seconds"][name] = time.time() - t0
        if ok:
            res["passed"].append(name)
        else:
            res["failed"].append((name, msg or "result differs from the closed form"))
    return res


def main(argv=None, sink=None) -> int:
    """`emir validate`. Exit 0 when every run case passes, else 70
    (EMIR-E903). With `sink` (the CLI's `output.Out`), the `emir.validate/1`
    document is emitted to it."""
    from .errors import EXIT_INTERNAL
    from .output import Out, document, parser
    ap = parser("emir validate",
                description=f"Run the analytic validation suite ({len(CASES)} closed-form cases).")
    ap.add_argument("--out", default=None,
                    help="directory for the test files (default: a temp dir, "
                         "removed when every case passes)")
    ap.add_argument("--keep", action="store_true",
                    help="keep the output directory")
    ap.add_argument("--quick", action="store_true",
                    help="leave out the slow transient cases ("
                         + ", ".join(n for n, _fn, _kind, slow in CASES if slow) + ")")
    ap.add_argument("--no-skip", action="store_true",
                    help="kept for the scripts that pass it: every case needs only "
                         "numpy, scipy and PyYAML, so none is skipped for a missing "
                         "package")
    a = ap.parse_args(argv)
    sink = sink or Out(a.format)
    out = os.path.abspath(a.out) if a.out else tempfile.mkdtemp(prefix="emir_validate_")
    t0 = time.time()
    r = run(out, quick=a.quick)
    n_run = len(r["passed"]) + len(r["failed"])
    print()
    code = "EMIR-E903" if r["failed"] else None
    if r["failed"]:
        print(f"VALIDATION FAILED: {len(r['failed'])} of {n_run} run cases: "
              f"{', '.join(n for n, _ in r['failed'])} -- files kept in {out} [EMIR-E903]")
    else:
        skipped = (f" ({len(r['skipped'])} skipped: "
                   f"{', '.join(n for n, _ in r['skipped'])})" if r["skipped"] else "")
        print(f"ALL {n_run} VALIDATION CASES PASS{skipped} in {time.time() - t0:.1f} s")
    kept = None
    if not r["failed"] and not (a.keep or a.out):
        shutil.rmtree(out, ignore_errors=True)
    else:
        kept = out
        if a.keep or a.out:
            print(f"files in {out}")
    kinds = {name: kind for name, _fn, kind, _slow in CASES}
    failed, skipped = dict(r["failed"]), dict(r["skipped"])
    cases = []
    for name, _fn, _kind, _slow in CASES:
        status = ("FAIL" if name in failed else "SKIP" if name in skipped
                  else "PASS" if name in r["passed"] else "SKIP")
        cases.append({"name": name, "kind": kinds[name], "status": status,
                      "seconds": r["seconds"].get(name),
                      "reason": failed.get(name) or skipped.get(name)})
    ok = not r["failed"]
    sink.emit(document("validate", passed=ok, code=code,
                       cases=cases, n_run=n_run, n_failed=len(r["failed"]),
                       n_skipped=len(r["skipped"]), seconds=time.time() - t0,
                       out_dir=kept))
    return 0 if ok else EXIT_INTERNAL


if __name__ == "__main__":
    raise SystemExit(main())
