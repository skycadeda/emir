"""`emir validate` -- the analytic validation suite.

Six cases, each against an answer known in closed form, so a pass is a
real cross-check of the code rather than a comparison with its own earlier
output: the DSPF path, the two limit forms design manuals print (a width
loss and Black's derating), time-weighted metrics on non-uniform samples,
the window, rcc's ingest end to end (simulated branch currents read
from a result, windowed and checked), and RCC mode's voltages.

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


# ----------------------------------------------------- waveforms and windows

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
    from .waveform import metrics_tran
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
    """The window: the record's last whole periods, up to three, on a spike
    train with two start-ups.

    A start-up with a time constant of 0.2 periods has died out by the
    record's end: the window is its last three whole periods, and their
    average is the steady state's. One of 1.5 periods still changes the last
    two periods by more than 2 %: refused (EMIR-E429). A slower tail may
    change them by less, and is averaged: rcc is exact for the simulated
    record over the whole periods you give (--period); whether the testbench
    has settled is yours to establish, as for any .meas.
    """
    from .errors import EmirError
    from .waveform import metrics_tran, window
    P = 1e-9
    t, y0 = _spike_samples(period=P, cycles=8)

    y_fast = y0 + 2e-3 * np.exp(-t / (0.2 * P))
    fast = window(t, y_fast, period=P, log=_quiet)
    mf = metrics_tran(t, y_fast, fast["t0"], fast["t1"])
    ok_fast = (fast["n_periods"] == 3 and abs(fast["t0"] - 5 * P) < 1e-6 * P
               and fast["t1"] == t[-1] and abs(mf["avg"] * 1e3 - SPIKE_AVG) < 1e-6 * SPIKE_AVG)

    try:
        window(t, y0 + 2e-3 * np.exp(-t / (1.5 * P)), period=P, log=_quiet)
        slow = None
    except EmirError as e:
        slow = e
    ok_slow = slow is not None and slow.code == "EMIR-E429"

    ok = ok_fast and ok_slow
    print(f"{'PASS' if ok else 'FAIL'} the window: a start-up of tau = 0.2 periods -> the "
          f"last {fast['n_periods']} of 8 periods, avg {mf['avg'] * 1e3:.6f} mA (steady "
          f"state {SPIKE_AVG:.6f})")
    print("     a start-up of tau = 1.5 periods, under way at the end -> "
          + (f"refused ({slow.code}: the last two periods differ by "
             f"{slow.details['change']:.1%}, no number)" if ok_slow else "NOT refused")
          + "; whether a slower one has settled is yours to establish")
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


def validate_rcc_ingest(tmp: str) -> bool:
    """Simulated branch currents -> a result file -> rcc -> EM, end to end,
    against a closed form.

    Same necked strap as `validate_dspf`, but the current now arrives the way
    the real flow delivers it: as a simulated branch-current waveform per
    resistor (`R:1`, positive from its first node to its second) in a PSF
    ASCII result, matched by name, windowed over whole periods and reduced by
    trapezoid. The strap is a series chain, so every branch carries SPIKE,
    and the neck's avg, rms and peak, its ratios and the IR are exact -- a
    test of the *mapping*, the *time weighting*, the *window* (the last three
    of four periods) and every metric at once.
    """
    from .dspf import parse
    from .rcc import rcc_check
    dpath = os.path.join(tmp, "_emir_rcc_strap.dspf")
    with open(dpath, "w", encoding="utf-8") as f:
        f.write(STRAP_DSPF)
    d = parse(dpath)

    t, y = _spike_samples(period=1e-9, cycles=4)
    # MA:d -> VSS:1 -> VSS:2 -> MB:s: each resistor's n1 to n2, the chain's way
    rpath = _psf_tran(os.path.join(tmp, "_emir_rcc.tran"), t,
                      {"Rw1:1": y, "Rn:1": y, "Rw2:1": y})

    # the neck is 0.2 um wide: avg 0.195 mA -> 0.975 mA/um; rms and peak
    # limits chosen so the closed forms are round
    proc = _strap_proc(jmax_rms=1.0, jmax_peak=20.0)
    r = rcc_check(d, "VSS", proc, rpath, "MB:s", period=1e-9, log=_quiet)
    neck = [i for i, rr in enumerate(r["network"].res) if rr["name"] == "Rn"][0]
    avg, rms, peak = (float(r["Ie"][m][neck]) * 1e3 for m in ("avg", "rms", "peak"))
    ir = r["ir_drop"] * 1e3                         # 1.75 ohm x the average
    ok = (abs(avg - SPIKE_AVG) < 1e-9 * SPIKE_AVG and abs(rms - SPIKE_RMS) < 1e-4
          and abs(peak - SPIKE_PEAK) < 1e-12 and abs(ir - 1.75 * SPIKE_AVG) < 1e-9
          and r["window"]["n_periods"] == 3)
    print(f"{'PASS' if ok else 'FAIL'} rcc ingest -> EM: neck avg {avg:.6f} (exp "
          f"{SPIKE_AVG:.6f}), rms {rms:.6f} (exp {SPIKE_RMS:.6f}), peak {peak:.4f} (exp "
          f"{SPIKE_PEAK:.4f}) mA over {r['window']['n_periods']} periods; IR {ir:.6f} mV "
          f"(exp {1.75 * SPIKE_AVG:.6f})")
    for p in (dpath, rpath):
        os.remove(p)
    return ok


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


# ------------------------------------------------------------------ driver

#: (name, function, kind, slow). Every case is "core" (numpy, scipy, pyyaml
#: only) and none is slow (`--quick` would leave a slow one out).
CASES = [
    ("dspf", validate_dspf, "core", False),
    ("hand-entry forms", validate_hand_forms, "core", False),
    ("tran metrics", validate_metrics, "core", False),
    ("window", validate_window, "core", False),
    ("rcc ingest", validate_rcc_ingest, "core", False),
    ("rcc mode", validate_rcc_mode, "core", False),
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
                    help="leave out the slow cases (none is slow now: kept for the "
                         "scripts that pass it)")
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
