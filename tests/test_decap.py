"""Decap-aware EM (`emir/decap.py`, `dynir.transient(em=...)`): the dynamic
solve's branch currents against closed forms and an exact reference, the
numerics that keep them honest (the carried wrap, the mean-corrected restart,
the step correction of rms, the refined peak), the three verdict bases, the
fallbacks, and the agent contract around the person's choice of basis.

Every input is synthetic and every number invented; no simulator is needed.
The closed forms are worked out in each test's docstring (and in
docs/big-blocks.md, "Decap-aware EM").
"""
import json
import os

import jsonschema
import numpy as np
import pytest
import yaml
from _helpers import PROC, emir, write
from scipy.linalg import expm

from emir import schemas
from emir.dspf import Network, analyse, build_parser, parse
from emir.dynir import EmSpec, transient
from emir.summary import summary_document
from emir.validate import RC_DSPF, _psf_tran


def quiet(*_a, **_k):
    pass


def run_doc(*argv):
    """`emir run ARGV` in process: (RunResult, the emir.run/1 document)."""
    a = build_parser().parse_args([str(x) for x in argv])
    a.argv = [str(x) for x in argv]
    a.command = "emir run " + " ".join(a.argv)
    res = analyse(a)
    return res, json.loads(json.dumps(summary_document(res), default=float))


def valid(doc, name):
    jsonschema.validate(doc, schemas.get(name))


# ------------------------------------------------------------ 5.1 closed form

#: the validation suite's R || C node (R = 100 ohm feed, 1 pF wire C at MA:s)
#: plus a MOS decap whose gate is MA:s and whose d/s/b are on VSS (a net with
#: a port and no resistance, so it is an ideal far plate): C = cox W L =
#: 1 F/m^2 x 100 um x 0.2 um = 20 pF, R_access = esr_a
R, CW, CD, IDC, IAC = 100.0, 1e-12, 20e-12, 1e-3, 0.8e-3
DECAP = RC_DSPF.replace(".ENDS\n.END\n", """*|NET VSS 0.0
*|P (VSS I *1 0.0000 -5.0000)
*|I (MD:d MD d I 0 10.0000 -5.0000)
*|I (MD:s MD s I 0 10.2000 -5.0000)
MD MD:d MA:s MD:s VSS ncap_demo L=2e-07 W=1e-04
.ENDS
.END
""")
#: the same decap hung off MA:s on its own 10 ohm strap (Metal2, node MS): a
#: dead-end segment that carries only the decap's current
STRAP = DECAP.replace("MD MD:d MA:s MD:s", "MD MD:d MS MD:s").replace(
    "CL MA:s 0 1.0e-12\n",
    "CL MA:s 0 1.0e-12\n*|S (MS 10.0000 2.0000)\n"
    "Rst MA:s MS 10.000000 $metal2 $L=2.0 $W=1.000000 $LLX=9.5 $LLY=0.0 $URX=10.5 "
    "$URY=2.0 $lvl=2\n")


def proc_file(tmp, esr=50.0, metal2=None, **model):
    """The sample process file with a cap model for the decap (and a Metal2
    for the strap: avg 1.0, rms 0.3, peak 0.4 mA/um, invented)."""
    with open(PROC, encoding="utf-8") as f:
        d = yaml.safe_load(f)
    m = {"cox": 1.0, "esr_a": esr} if esr is not None else {"cox": 1.0, "ron_sq": 5000.0}
    m.update(model)
    d["cap_models"] = {"ncap_demo": m}
    if metal2:
        d["layers"]["Metal2"] = {"gds": [103, 0], "sheet_res": 0.1, "tc1": 0.0, "tc2": 0.0,
                                 "min_width": 0.3, "jmax_avg": 1.0, "jmax_rms": 0.3,
                                 "jmax_peak": 0.4}
    return write(os.path.join(str(tmp), "proc.yaml"), yaml.safe_dump(d))


def cosine(tmp, wrc, c_tot=CW + CD, periods=4, per=200, name="cos.tran"):
    """`I_dc + I_ac cos(wt)` into MA:s's instance (Spectre's sign), wRC_tot =
    w R C_tot, `periods` periods of `per` samples; returns (path, f)."""
    w = wrc / (R * c_tot)
    f = w / (2 * np.pi)
    t = np.linspace(0.0, periods / f, periods * per + 1)
    return _psf_tran(os.path.join(str(tmp), name), t,
                     {"MA:s": IDC + IAC * np.cos(w * t)}), f


def exact(wrc, re_, rs=0.0, c_tot=CW + CD):
    """The closed form: `H = Z_sh / (R + Z_sh)`, `Z_sh = 1/(jwC_w) || Z_d`,
    `Z_d = R_s + R_e + 1/(jwC_d)`; feed rms = sqrt(I_dc^2 + |H|^2 I_ac^2/2),
    feed peak = I_dc + |H| I_ac, the decap's current amplitude I_ac
    |(R || Z_sh) / Z_d|."""
    w = wrc / (R * c_tot)
    zd = rs + re_ + 1 / (1j * w * CD)
    zsh = 1 / (1j * w * CW + 1 / zd)
    h = abs(zsh / (R + zsh))
    idec = IAC * abs((1 / (1 / R + 1 / zsh)) / zd)
    return {"rms": np.sqrt(IDC ** 2 + h ** 2 * IAC ** 2 / 2), "peak": IDC + h * IAC,
            "dec_rms": idec / np.sqrt(2), "dec_peak": idec, "h": h}


#: the hand numbers of DESIGN 5.1 (and its review), mA: (wRC_tot, R_e) ->
#: feed rms, feed peak, decap rms, decap peak
HAND = {(0.3, 50.0): (1.12868, 1.74017, 0.14803, 0.20935),
        (1.0, 50.0): (1.06078, 1.50049, 0.30431, 0.43036),
        (3.0, 50.0): (1.02375, 1.31004, 0.35920, 0.50799)}


@pytest.mark.parametrize("wrc,re_", sorted(HAND))
def test_closed_form_feed_and_decap_currents(tmp_path, wrc, re_):
    """The feed segment's rms and peak, and the decap's own current, against
    the closed form to 1e-3; decap-free unchanged (rms sqrt(1^2 + 0.8^2/2) =
    1.14891 mA, peak 1.8 mA); the avg identical in every basis, bit for bit."""
    x = exact(wrc, re_)
    assert (x["rms"] * 1e3, x["peak"] * 1e3, x["dec_rms"] * 1e3, x["dec_peak"] * 1e3) == \
        pytest.approx(HAND[(wrc, re_)], abs=1e-5)
    d = write(tmp_path / "dec.dspf", DECAP)
    res_path, f = cosine(tmp_path, wrc)
    res, doc = run_doc(d, "--process", proc_file(tmp_path, re_), "--net", "VDD",
                       "--cc-sim", res_path, "--mode", "cc-grid", "--period", 1 / f,
                       "--dynamic", "--em-basis", "decap-aware")
    valid(doc, "run")
    n = doc["nets"][0]
    em = n["dynamic_em"]
    assert n["em_basis"] == {"requested": "decap-aware", "used": "decap-aware",
                             "source": "option", "fallback": None, "fallback_reason": None}
    fe = em["feed"]
    assert fe["i_rms_A"]["decap-free"] == pytest.approx(1.14891e-3, rel=1e-5)
    assert fe["i_peak_A"]["decap-free"] == pytest.approx(1.8e-3, rel=1e-9)
    assert fe["i_rms_A"]["decap-aware"] == pytest.approx(x["rms"], rel=1e-3)
    assert fe["i_peak_A"]["decap-aware"] == pytest.approx(x["peak"], rel=1e-3)
    dec = em["decaps"]["top"][0]
    assert dec["name"] == "MD" and not dec["folded"] and dec["c_F"] == pytest.approx(CD)
    assert dec["i_rms_A"] == pytest.approx(x["dec_rms"], rel=1e-3)
    assert dec["i_peak_A"] == pytest.approx(x["dec_peak"], rel=1e-3)
    # a capacitor carries no DC: its average is the periodic-average check's
    # residual (tolerance 1e-4 of the largest branch average)
    assert abs(dec["i_avg_A"]) < 1e-4 * IDC
    r = res.solved["VDD"]
    # the verdict's currents are the decap-aware ones: Rw is the feed segment
    assert r["Ie"]["rms"][0] == pytest.approx(x["rms"], rel=1e-3)
    assert r["em_free"]["Ie"]["rms"][0] == pytest.approx(1.14891e-3, rel=1e-5)
    assert np.array_equal(r["Ie"]["avg"], r["em_free"]["Ie"]["avg"])
    num = em["numerics"]
    assert num["settled"] and num["avg_check"]["passed"]
    assert num["avg_check"]["max_rel"] <= 1e-4
    assert num["avg_identity_residual"] < 1e-10
    assert num["resample_avg_error"] < 1e-4


def test_folded_decap_takes_its_share_of_the_node_current(tmp_path):
    """R_e -> 0 (1 mohm: R C = 20 fs, far below the 16.5 ps step) folds the
    decap onto MA:s; its current is then its C share of the node's capacitor
    current, from KCL (no companion state to reset). wRC_tot = 1: |H| =
    0.70711, feed rms 1.07703 mA, peak 1.56569 mA; the decap's share rms
    0.38095 mA, peak 0.53875 mA."""
    x = exact(1.0, 0.0)
    assert x["h"] == pytest.approx(0.70711, abs=1e-5)
    assert (x["rms"] * 1e3, x["peak"] * 1e3) == pytest.approx((1.07703, 1.56569), abs=1e-5)
    share = IAC * abs(1 / (1 / R + 1j * (1 / (R * (CW + CD))) * (CW + CD))) \
        * (1 / (R * (CW + CD))) * CD
    assert share / np.sqrt(2) * 1e3 == pytest.approx(0.38095, abs=1e-5)
    d = write(tmp_path / "dec.dspf", DECAP)
    res_path, f = cosine(tmp_path, 1.0)
    _, doc = run_doc(d, "--process", proc_file(tmp_path, 1e-3), "--net", "VDD",
                     "--cc-sim", res_path, "--mode", "cc-grid", "--period", 1 / f,
                     "--dynamic")
    em = doc["nets"][0]["dynamic_em"]
    dec = em["decaps"]["top"][0]
    assert dec["folded"] and em["decaps"]["folded_F"] == pytest.approx(CD)
    assert dec["i_rms_A"] == pytest.approx(0.38095e-3, rel=1e-3)
    assert dec["i_peak_A"] == pytest.approx(0.53875e-3, rel=1e-3)
    assert em["feed"]["i_rms_A"]["decap-aware"] == pytest.approx(1.07703e-3, rel=1e-3)
    assert em["feed"]["i_peak_A"]["decap-aware"] == pytest.approx(1.56569e-3, rel=1e-3)


def strap_run(tmp_path, *extra, wrc=1.0, summary=None):
    d = write(tmp_path / "strap.dspf", STRAP)
    res_path, f = cosine(tmp_path, wrc)
    args = [d, "--process", proc_file(tmp_path, 50.0, metal2=True), "--net", "VDD",
            "--cc-sim", res_path, "--mode", "cc-grid", "--period", 1 / f, "--dynamic",
            *extra]
    if summary:
        args += ["--summary-out", summary]
    return run_doc(*args)


def test_a_decap_strap_carries_the_decaps_current(tmp_path):
    """The decap on its own 10 ohm strap: decap-free gives the strap no
    current, decap-aware the decap's (wRC_tot = 1, R_s = 10, R_e = 50 ohm:
    strap rms 0.29150 mA, peak 0.41225 mA; feed rms 1.06032 mA, peak 1.49855
    mA). On the invented Metal2 (rms 0.3, peak 0.4 mA/um, 1 um wide) the
    strap is a peak FAIL (1.031x) that the decap-free verdict does not see:
    the default basis (worst) takes it, decided by decap-aware."""
    x = exact(1.0, 50.0, rs=10.0)
    assert (x["rms"] * 1e3, x["peak"] * 1e3, x["dec_rms"] * 1e3, x["dec_peak"] * 1e3) == \
        pytest.approx((1.06032, 1.49855, 0.29150, 0.41225), abs=1e-5)
    res, doc = strap_run(tmp_path)
    valid(doc, "run")
    n = doc["nets"][0]
    em = n["dynamic_em"]
    r = res.solved["VDD"]
    i = [x_["name"] for x_ in r["network"].res].index("Rst")
    assert r["em_free"]["Ie"]["rms"][i] < 1e-12             # decap-free: nothing
    aware = r["Ie"]                                          # worst = max(free, aware)
    assert aware["rms"][i] == pytest.approx(x["dec_rms"], rel=1e-3)
    assert aware["peak"][i] == pytest.approx(x["dec_peak"], rel=1e-3)
    assert em["feed"]["i_rms_A"]["decap-aware"] == pytest.approx(x["rms"], rel=1e-3)
    assert em["feed"]["i_peak_A"]["decap-aware"] == pytest.approx(x["peak"], rel=1e-3)
    b = em["bases"]
    assert b["decap-free"]["verdict"] == "CLEAN" and b["decap-aware"]["verdict"] == "FAIL"
    assert b["worst"]["verdict"] == "FAIL" and n["verdict"] == "FAIL"
    assert n["em_basis"]["used"] == "worst" and n["em_basis"]["source"] == "default"
    st = next(it for it in n["worst_items"] if it["name"] == "Rst")
    assert st["metric"] == "peak" and st["ratio"] == pytest.approx(x["dec_peak"] / 0.4e-3,
                                                                  rel=1e-3)
    assert st["decided_by"] == "decap-aware" and st["ratio_decap_free"] < 1e-6
    assert st["ratio_decap_aware"] == pytest.approx(st["ratio"], rel=1e-12)
    assert em["higher_aware"]["n"] >= 1
    # a reversing current (avg 0, |I| large) shows in the basis's own notes
    assert r["bidirectional"] is not r["em_free"]["bidirectional"]


def test_decap_free_basis_names_what_it_does_not_cover(tmp_path):
    """--em-basis decap-free: the 1.x verdict (CLEAN), EMIR-W436 for the strap
    that reads higher with its decap, EMIR-W437 (not the default), and the
    run's next steps point at `emir next --run-summary` (a plan, not a
    re-run)."""
    s = str(tmp_path / "s.json")
    res, doc = strap_run(tmp_path, "--em-basis", "decap-free", summary=s)
    n = doc["nets"][0]
    assert n["verdict"] == "CLEAN" and n["em_basis"]["used"] == "decap-free"
    codes = {x["code"] for x in doc["notices"]}
    assert {"EMIR-W436", "EMIR-W437", "EMIR-I430"} <= codes
    cmds = [x["command"] for x in doc["next_steps"]]
    assert any(c.startswith("emir next --run-summary") for c in cmds)
    assert not any("--em-basis" in c and c.startswith("emir run") for c in cmds)


# ------------------------------------------------------------ 5.2 / 5.3: the ladder


LADDER = """*|DSPF 1.3
*|DESIGN "ladder"
*|OPERATING_TEMPERATURE 100
*|NET VDD 0
*|P (VDD I *1 0.0 0.0)
*|S (VDD:1 10.0 0.0)
*|S (VDD:2 20.0 0.0)
*|I (MA:s MA s I 0 30.0 0.0)
R1 VDD VDD:1 1.0 $metal1 $L=10 $W=1.0 $LLX=0 $LLY=-0.5 $URX=10 $URY=0.5 $lvl=1
R2 VDD:1 VDD:2 1.0 $metal1 $L=10 $W=1.0 $LLX=10 $LLY=-0.5 $URX=20 $URY=0.5 $lvl=1
R3 VDD:2 MA:s 1.0 $metal1 $L=10 $W=1.0 $LLX=20 $LLY=-0.5 $URX=30 $URY=0.5 $lvl=1
C1 VDD:1 0 10e-15
C2 VDD:2 0 10e-15
C3 MA:s 0 10e-15
*|NET VSS 0
*|P (VSS I *1 0.0 -5.0)
*|I (MD:d MD d I 0 20.0 -5.0)
*|I (MD:s MD s I 0 20.2 -5.0)
MD MD:d VDD:2 MD:s VSS ncap_demo L=1e-06 W=1e-04
.ENDS
"""
#: the decap: cox W L = 1 F/m^2 x 100 um x 1 um = 100 pF, R_access = esr_a


def pulse_train(per=1e-9, periods=2, width=10e-12, amp=20e-3, idc=1e-3, coarse=5e-12,
                fine=0.25e-12, edges=(0.2, 0.55)):
    """Triangular spikes of 20 and 12 mA on 1 mA, sampled as an adaptive
    simulator would (5 ps, 0.25 ps inside the spikes): 705 samples."""
    ts = [np.arange(0, periods * per + coarse / 2, coarse)]
    for p in range(periods):
        for e in edges:
            c = (p + e) * per
            ts.append(np.arange(c - width, c + width + fine / 2, fine))
    t = np.unique(np.round(np.concatenate(ts), 18))
    t = t[(t >= 0) & (t <= periods * per)]
    y = np.full_like(t, idc)
    for p in range(periods):
        for j, e in enumerate(edges):
            c = (p + e) * per
            y += amp * (1.0 if j == 0 else 0.6) * np.clip(1 - np.abs(t - c) / (width / 2),
                                                          0, None)
    return t, y


def exact_ladder(t, load, re_=0.5, cd=100e-12, cw=10e-15, sub=20):
    """The exact periodic steady state of the ladder (four states: the three
    nodes and the decap's internal node), driven by the PWL `load` drawn at
    MA:s: the matrix exponential of the PWL input over every sample interval,
    periods iterated until they repeat; branch currents at `sub` points
    inside every interval. Returns {R1, R2, R3, decap: current waveform}."""
    G = np.zeros((4, 4))

    def stamp(a, b, g):
        for x in (a, b):
            if x is not None:
                G[x, x] += g
        if a is not None and b is not None:
            G[a, b] -= g
            G[b, a] -= g
    stamp(None, 0, 1.0)
    stamp(0, 1, 1.0)
    stamp(1, 2, 1.0)
    stamp(1, 3, 1.0 / re_)
    Cn = np.array([cw, cw, cw, cd])
    B = np.zeros(4)
    B[2] = -1.0                                   # the load draws from MA:s
    M = np.zeros((6, 6))
    M[:4, :4] = -G / Cn[:, None]
    M[:4, 4] = B / Cn
    M[4, 5] = 1.0
    x = np.linalg.solve(G, B * load[0])
    cache = {}

    def E(dt):
        k = round(dt * 1e19)
        if k not in cache:
            cache[k] = expm(M * dt)
        return cache[k]
    dts = np.diff(t)
    for _ in range(300):
        x0 = x.copy()
        for i, dt in enumerate(dts):
            z = np.concatenate([x, [load[i], (load[i + 1] - load[i]) / dt]])
            x = (E(dt) @ z)[:4]
        if np.abs(x - x0).max() < 1e-14 * max(np.abs(x).max(), 1e-30):
            break
    xs = [x.copy()]
    for i, dt in enumerate(dts):
        z = np.concatenate([x, [load[i], (load[i + 1] - load[i]) / dt]])
        for s in range(1, sub):
            xs.append((E(dt * s / sub) @ z)[:4])
        x = (E(dt) @ z)[:4]
        xs.append(x.copy())
    X = np.array(xs).T
    ts = np.concatenate([[t[0]], np.concatenate(
        [t[i] + np.arange(1, sub + 1) * (t[i + 1] - t[i]) / sub for i in range(len(dts))])])
    return ts, {"R1": -X[0], "R2": X[0] - X[1], "R3": X[1] - X[2],
                "decap": (X[1] - X[3]) / re_}


def _metrics(ts, w):
    from emir.ccsim import trapz_weights
    wt = trapz_weights(ts) / (ts[-1] - ts[0])
    return float(np.sqrt(np.sum(wt * w * w))), float(np.abs(w).max())


@pytest.fixture(scope="module")
def ladder(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("ladder")
    t, y = pulse_train()
    res_path = _psf_tran(str(tmp / "pulse.tran"), t, {"MA:s": y})
    d = write(tmp / "ladder.dspf", LADDER)
    ts, ex = exact_ladder(t, y)
    ref = {k: _metrics(ts, v) for k, v in ex.items()}
    return {"tmp": tmp, "dspf": d, "result": res_path, "ref": ref,
            "proc": proc_file(tmp, 0.5)}


def test_pulse_train_against_the_exact_reference(ladder):
    """The ladder with a 100 pF / 0.5 ohm decap at VDD:2, an adaptive-like
    pulse train (spikes of 20 and 12 mA, 10 ps wide): at the default step
    (4 x 705 samples) the corrected rms is within 0.2 % of the exact one and
    the refined peak within -0.5 % .. +1.0 % on R1, R2 and R3; R1 and R2
    carry about a third less rms and three quarters less peak than without
    the decap (exact: R1 rms 1.1945 mA, peak 5.1686 mA, against decap-free
    1.7708 and 21.0 mA)."""
    ref = ladder["ref"]
    assert ref["R1"][0] * 1e3 == pytest.approx(1.1945, abs=2e-4)
    assert ref["R1"][1] * 1e3 == pytest.approx(5.1686, abs=2e-3)
    res, doc = run_doc(ladder["dspf"], "--process", ladder["proc"], "--net", "VDD",
                       "--cc-sim", ladder["result"], "--mode", "cc-grid", "--period", 1e-9,
                       "--dynamic", "--em-basis", "decap-aware")
    r = res.solved["VDD"]
    names = [x["name"] for x in r["network"].res]
    for k in ("R1", "R2", "R3"):
        i = names.index(k)
        rms, pk = r["Ie"]["rms"][i], r["Ie"]["peak"][i]
        assert rms / ref[k][0] - 1 == pytest.approx(0.0, abs=2e-3), k
        assert -5e-3 <= pk / ref[k][1] - 1 <= 1e-2, (k, pk / ref[k][1] - 1)
    free = r["em_free"]["Ie"]
    for k in ("R1", "R2"):
        i = names.index(k)
        assert r["Ie"]["rms"][i] / free["rms"][i] == pytest.approx(0.67, abs=0.03)
        assert r["Ie"]["peak"][i] / free["peak"][i] == pytest.approx(0.25, abs=0.03)
    # 5.3: the load segment keeps its decap-free current (the 10 fF at the load
    # node takes 10 fF x 1 ohm x 20 mA / 5 ps = 40 uA, 0.2 % of the spike)
    i3 = names.index("R3")
    assert r["Ie"]["peak"][i3] / free["peak"][i3] == pytest.approx(1.0, abs=4e-3)
    dec = doc["nets"][0]["dynamic_em"]["decaps"]["top"][0]
    assert dec["i_rms_A"] == pytest.approx(ref["decap"][0], rel=5e-3)


def test_the_ladder_bases_and_the_avg(ladder):
    """worst is the per-segment maximum of the two; avg is the static one in
    every basis; the summary's per-basis verdicts follow the arrays."""
    res, doc = run_doc(ladder["dspf"], "--process", ladder["proc"], "--net", "VDD",
                       "--cc-sim", ladder["result"], "--mode", "cc-grid", "--period", 1e-9,
                       "--dynamic")
    r = res.solved["VDD"]
    free = r["em_free"]["Ie"]
    _, aw = run_doc(ladder["dspf"], "--process", ladder["proc"], "--net", "VDD",
                    "--cc-sim", ladder["result"], "--mode", "cc-grid", "--period", 1e-9,
                    "--dynamic", "--em-basis", "decap-aware")
    for m in ("rms", "peak"):
        assert np.all(r["Ie"][m] >= free[m])
    assert np.array_equal(r["Ie"]["avg"], free["avg"])
    em = doc["nets"][0]["dynamic_em"]
    assert em["bases"]["worst"]["max_ratio"]["all"] == pytest.approx(
        max(em["bases"]["decap-free"]["max_ratio"]["all"],
            em["bases"]["decap-aware"]["max_ratio"]["all"]), rel=1e-12)
    assert aw["nets"][0]["max_ratio"]["all"] == pytest.approx(
        em["bases"]["decap-aware"]["max_ratio"]["all"], rel=1e-12)


# ------------------------------------------------------------ numerics


def _ladder_nw(tmp, caps=True):
    text = LADDER if caps else "\n".join(x for x in LADDER.splitlines()
                                         if not x.startswith("C")) + "\n"
    d = parse(write(tmp / "l.dspf", text))
    return d, Network(d, "VDD")


def _taps(t, load):
    return [{"name": "MA:s", "node": "MA:s", "wave": -load, "i_avg": float(-load.mean())},
            {"name": "feed", "node": "VDD", "wave": load, "i_avg": 0.0, "feed": True}]


def _plateau(t, per=1e-9):
    ph = (t % per) / per
    return 1e-3 + 20e-3 * np.clip(1 - np.abs(ph - 0.2) / 0.005, 0, None) + \
        5e-3 * ((ph > 0.55) & (ph < 0.86))


def test_the_wrap_is_carried_on_nodes_without_capacitance(tmp_path):
    """An R-only ladder with one decap lump: a pass ends in the state of the
    last sample, so the next starts from the last sample's injection. With
    the first sample's (as before), a window whose ends differ by 0.1 % of the
    peak rang in the trapezoid's (-1)^n mode and did not settle in 50 passes;
    now it settles at once and matches the matched window to 1e-4. A window
    of 1.7 periods (ends 4 mA apart) no longer rings either (R3's peak 20.3
    mA, the load's own)."""
    d, nw = _ladder_nw(tmp_path, caps=False)
    lump = [{"node": "VDD:2", "c": 100e-12, "r": 0.5, "name": "MD"}]
    t = np.linspace(0, 2e-9, 4001)
    load = _plateau(t)
    out = {}
    for tag, extra in (("matched", 0.0), ("0.1%", 0.02e-3)):
        y = load.copy()
        y[-1] += extra
        out[tag] = transient(nw, "VDD", _taps(t, y), t, node_c=np.zeros(len(nw.nodes)),
                             lumps=lump, n_steps=4001, periodic=True, log=quiet,
                             em=EmSpec())
        assert out[tag]["settled"] and out[tag]["passes"] <= 3, tag
    a, b = out["matched"]["em"]["branch"], out["0.1%"]["em"]["branch"]
    for m in ("rms", "peak"):
        assert np.max(np.abs(a[m] - b[m]) / np.max(a[m])) < 1e-4, m
    t17 = np.linspace(0, 1.7e-9, 4001)
    r = transient(nw, "VDD", _taps(t17, _plateau(t17)), t17, node_c=np.zeros(len(nw.nodes)),
                  lumps=lump, n_steps=4001, periodic=True, log=quiet, em=EmSpec())
    assert r["settled"] and r["passes"] <= 3 and r["wrap_mismatch"] > 0.1
    i3 = [x["name"] for x in nw.res].index("R3")
    assert r["em"]["branch"]["peak"][i3] == pytest.approx(20.3e-3, rel=2e-3)


def test_a_slow_decap_settles_with_the_mean_corrected_restart(tmp_path):
    """A decap time constant five times the window (0.5 ohm x 30 nF against
    3 ns): the plain restart contracts by e^-0.2 = 0.82 a pass (29 passes, or
    not settled with the average check); restarting from the end state
    shifted by its mean's error (G Vbar* = Ibar) settles in a few, at the same
    answer as a long plain run."""
    L = ["*|DSPF 1.3", '*|DESIGN "p"', "*|NET VDD 0", "*|P (VDD I *1 0.0 0.0)",
         "*|I (MA:s MA s I 0 30.0 0.0)", "R1 VDD VDD:1 0.5 $metal1 $L=10 $W=1.0 $lvl=1",
         "R2 VDD:1 MA:s 0.2 $metal1 $L=10 $W=1.0 $lvl=1", ".ENDS"]
    d = parse(write(tmp_path / "p.dspf", "\n".join(L) + "\n"))
    nw = Network(d, "VDD")
    t = np.linspace(0, 3e-9, 3001)
    ph = (t % 1e-9) / 1e-9
    load = 1e-3 + 20e-3 * np.clip(1 - np.abs(ph - 0.3) / 0.01, 0, None) + \
        5e-3 * ((ph > 0.6) & (ph < 0.8))
    C = np.full(len(nw.nodes), 10e-15)
    lump = [{"node": "VDD:1", "c": 30e-9, "r": 0.02}]
    r = transient(nw, "VDD", _taps(t, load), t, node_c=C, lumps=lump, n_steps=3001,
                  periodic=True, log=quiet, em=EmSpec())
    assert r["settled"] and r["passes"] <= 8 and r["restart"] == "mean-corrected"
    long = transient(nw, "VDD", _taps(t, load), t, node_c=C, lumps=lump, n_steps=3001,
                     periodic=True, log=quiet, em=EmSpec(), max_passes=400,
                     settle_tol=1e-9)
    for m in ("rms", "peak"):
        assert np.max(np.abs(r["em"]["branch"][m] - long["em"]["branch"][m])
                      / np.max(long["em"]["branch"][m])) < 1e-3


def test_a_flat_top_the_grid_cuts_is_sub_stepped(tmp_path, monkeypatch):
    """A 20 mA flat-top pulse, 22 ps long with 0.5 ps edges, whose rise
    starts and fall ends on grid points (2.002 ps step): on the grid alone
    the pulse loses the charge of its edges (the linear interpolation makes
    them 2 ps long), and the low-passed current on the feed side, which
    peaks at the pulse's fall, reads 5 % low -- with nothing in the pin
    waveform to flag it (its flat top keeps 100 % of its peak). The
    intervals holding the edges are integrated in sub-steps of the waveform
    itself (`dynir.StepSequence`): within 0.5 % of the exact reference."""
    from emir import dynir
    d, nw = _ladder_nw(tmp_path, caps=False)
    n = 1000
    h = 2e-9 / (n - 1)
    edges = []
    for k in (100, 100 + 499 + 3):
        a, b = k * h, (k + 11) * h
        edges.append((a, a + 0.5e-12, b - 0.5e-12, b))
    t = np.unique(np.concatenate([np.arange(0, 2e-9 + 2.5e-12, 5e-12),
                                  np.array([e for x in edges for e in x])]))
    t = t[t <= 2e-9]
    y = np.full_like(t, 1e-3)
    for (r0, r1, f0, f1) in edges:
        y += 20e-3 * np.interp(t, [r0, r1, f0, f1], [0, 1, 1, 0], left=0, right=0)
    C = np.full(len(nw.nodes), 10e-15)
    lump = [{"node": "VDD:2", "c": 100e-12, "r": 0.05}]
    ts, ex = exact_ladder(t, y, re_=0.05, sub=40)
    i1 = [x["name"] for x in nw.res].index("R1")
    ref = float(np.abs(ex["R1"]).max())
    r = transient(nw, "VDD", _taps(t, y), t, node_c=C, lumps=lump, n_steps=n,
                  periodic=True, log=quiet, em=EmSpec())
    assert r["sub_steps"]["intervals"] >= 4                 # the four edges' intervals
    assert -5e-3 <= r["em"]["branch"]["peak"][i1] / ref - 1 <= 1e-2
    with monkeypatch.context() as mp:                       # the grid alone
        mp.setattr(dynir.StepSequence, "refine", lambda self, *a, **k: None)
        g = transient(nw, "VDD", _taps(t, y), t, node_c=C, lumps=lump, n_steps=n,
                      periodic=True, log=quiet, em=EmSpec())
    assert g["em"]["branch"]["peak"][i1] / ref - 1 < -0.03


def test_the_rms_correction_on_a_coarse_step(ladder):
    """At a coarse step (--dyn-steps 1000, 2.002 ps) the grid loses rms on the
    spikes; each segment's decap-aware rms is scaled by the loss measured on
    its own decap-free current, so it stays within 0.3 % of the exact one."""
    res, doc = run_doc(ladder["dspf"], "--process", ladder["proc"], "--net", "VDD",
                       "--cc-sim", ladder["result"], "--mode", "cc-grid", "--period", 1e-9,
                       "--dynamic", "--em-basis", "decap-aware", "--dyn-steps", 1000)
    r = res.solved["VDD"]
    names = [x["name"] for x in r["network"].res]
    for k in ("R1", "R2", "R3"):
        i = names.index(k)
        assert r["Ie"]["rms"][i] / ladder["ref"][k][0] - 1 == pytest.approx(0.0, abs=3e-3)
        assert r["Ie"]["peak"][i] / ladder["ref"][k][1] - 1 >= -5e-3
    num = doc["nets"][0]["dynamic_em"]["numerics"]
    assert num["n_steps"] == 1000 and num["steps_from"] == "--dyn-steps"
    assert num["correction_max"]["rms"] >= 1.0


# ------------------------------------------------------------ 5.4 invariants


def mesh_text(n=8, k=10, c=1e-15, seed=0):
    """An n x n Metal1 mesh fed at one corner, k instance pins through 2-cut
    contacts, `c` farads from every mesh node to ground."""
    rng = np.random.default_rng(seed)
    nid = lambda i, j: "VDD" if (i, j) == (0, 0) else f"VDD:{i}_{j}"   # noqa: E731
    L = ["*|DSPF 1.3", '*|DESIGN "mesh"', "*|OPERATING_TEMPERATURE 100", "*|NET VDD 0",
         "*|P (VDD I *1 0.0 0.0)"]
    sites = rng.choice(n * n - 1, k, replace=False) + 1
    for q, s in enumerate(sites):
        i, j = divmod(int(s), n)
        L.append(f"*|I (M{q}:s M{q} s I 0 {i * 2.0:.3f} {j * 2.0:.3f})")
    r = 0
    for i in range(n):
        for j in range(n):
            for di, dj in ((1, 0), (0, 1)):
                if i + di < n and j + dj < n:
                    L.append(f"R{r} {nid(i, j)} {nid(i + di, j + dj)} 0.3 $metal1 $W=1.0 "
                             f"$L=2.0 $lvl=1")
                    r += 1
    for q, s in enumerate(sites):
        i, j = divmod(int(s), n)
        L.append(f"Rc{q} {nid(i, j)} M{q}:s 10.0 $cont $VC=2 $lvl=0")
    L += [f"Cn{i}_{j} {nid(i, j)} 0 {c:g}" for i in range(n) for j in range(n)
          if (i, j) != (0, 0)]
    L.append(".ENDS")
    return "\n".join(L) + "\n", [f"M{q}:s" for q in range(k)]


def mesh_pulses(tmp, pins, nt=241, periods=3, per=1e-9, name="cc.tran"):
    rng = np.random.default_rng(1)
    t = np.linspace(0.0, periods * per, nt)
    ph = rng.uniform(0.0, per, len(pins))
    sigs = {p: 1e-4 + 4e-4 * (((t - ph[q]) % per) < 0.3 * per) + 1e-5 * q
            for q, p in enumerate(pins)}
    return _psf_tran(os.path.join(str(tmp), name), t, sigs)


def exact_linear_free(res, net, result, period):
    """The decap-free branch currents on the simulator's own samples, reduced
    exactly for a current linear between them (`dynir.segment_sums`), as the
    dynamic solve reduces its own: (rms, peak) per branch. The static path's
    rms is the trapezoid of x^2, which reads a steep flank high by
    `(a - b)^2 / 6` per sample (its convention, conservative)."""
    from emir import ccsim
    from emir.dynir import segment_sums
    from emir.rcc import net_port
    r = res.solved[net]
    nw = r["network"]
    feed = net_port(res.dspf, net)
    ing = ccsim.cc_taps(res.dspf, net, result, period=period, feed=feed, log=quiet)
    waved = [x for x in ing["taps"] if x.get("wave") is not None and x["node"] != feed]
    Tm = np.array([nw.solve({x["node"]: 1.0, feed: -1.0}, feed)[1] for x in waved]).T
    I = Tm @ np.array([x["wave"] for x in waved])
    tt = np.asarray(ing["time"])
    _, s2, _ = segment_sums(I[:, :-1], I[:, 1:], np.diff(tt) / (tt[-1] - tt[0]))
    return np.sqrt(s2.sum(axis=1)), np.abs(I).max(axis=1)


def test_vanishing_capacitance_gives_the_decap_free_currents(tmp_path):
    """1e-21 F per node: decap-aware equals the decap-free currents reduced
    the same way (exact for a current linear between samples) within 1e-6 of
    the largest branch current in rms, and the static one in avg; never above
    the static rms (whose trapezoid of x^2 reads the pulses' flanks high: the
    step correction once mistook that convention for a loss of the step); in
    peak within the refinement's threshold (`dynir.EVENT_TOL` of the largest
    pin current)."""
    from emir.dynir import EVENT_TOL
    text, pins = mesh_text(c=1e-21)
    d = write(tmp_path / "m.dspf", text)
    result = mesh_pulses(tmp_path, pins)
    res, doc = run_doc(d, "--process", PROC, "--cc-sim", result,
                       "--mode", "cc-grid", "--period", 1e-9, "--dynamic",
                       "--em-basis", "decap-aware")
    r = res.solved["VDD"]
    free = r["em_free"]["Ie"]
    lin_rms, lin_pk = exact_linear_free(res, "VDD", result, 1e-9)
    assert np.max(np.abs(r["Ie"]["rms"] - lin_rms)) <= 1e-6 * np.max(lin_rms)
    assert np.all(r["Ie"]["rms"] <= free["rms"] * (1 + 1e-9))
    assert np.max(np.abs(r["Ie"]["peak"] - free["peak"])) <= EVENT_TOL * np.max(free["peak"])
    assert np.array_equal(r["Ie"]["avg"], free["avg"])
    assert doc["nets"][0]["dynamic_em"]["numerics"]["correction_max"]["rms"] < 1 + 1e-6


def test_decap_aware_em_does_not_depend_on_the_static_path(tmp_path):
    """The dynamic solve does not use the static superposition: dense or
    streamed, decap-aware is bit for bit the same, and decap-free and worst
    agree to 1e-10."""
    text, pins = mesh_text()
    d = write(tmp_path / "m.dspf", text)
    res_path = mesh_pulses(tmp_path, pins)
    out, em = {}, {}
    for p in ("dense", "stream"):
        res, doc = run_doc(d, "--process", PROC, "--cc-sim", res_path, "--mode", "cc-grid",
                           "--period", 1e-9, "--dynamic", "--superposition", p)
        r = res.solved["VDD"]
        out[p] = (r["em_free"]["Ie"], r["Ie"])
        em[p] = res.solved["VDD"]["dynamic"]["em"]["branch"]
        assert doc["nets"][0]["superposition"]["method"] == p
    for m in ("rms", "peak"):
        assert np.array_equal(em["dense"][m], em["stream"][m]), m
        for k in (0, 1):
            assert np.max(np.abs(out["dense"][k][m] - out["stream"][k][m])) <= \
                1e-10 * np.max(out["dense"][k][m]), m


def test_the_decap_free_basis_is_the_static_verdict_field_for_field(tmp_path):
    """--em-basis decap-free with --dynamic gives every field the run without
    --dynamic gives (the new ones aside)."""
    text, pins = mesh_text()
    d = write(tmp_path / "m.dspf", text)
    res_path = mesh_pulses(tmp_path, pins)
    _, plain = run_doc(d, "--process", PROC, "--cc-sim", res_path, "--mode", "cc-grid",
                       "--period", 1e-9)
    _, free = run_doc(d, "--process", PROC, "--cc-sim", res_path, "--mode", "cc-grid",
                      "--period", 1e-9, "--dynamic", "--em-basis", "decap-free")
    a, b = plain["nets"][0], free["nets"][0]
    for k in a:
        if k in ("dynamic", "dynamic_mV", "metric_note", "worst_items"):
            continue
        assert a[k] == b[k], k
    for x, y in zip(a["worst_items"], b["worst_items"]):
        assert {k: y[k] for k in x} == x
    for k in ("verdict", "counts", "worst_net"):
        assert plain[k] == free[k]


def test_every_basis_reports_its_own_notes(tmp_path):
    """The notes the verdict reads (reversing currents, marginal via arrays)
    come from the same currents as the verdict, in every basis."""
    res, _ = strap_run(tmp_path, "--em-basis", "decap-aware")
    r = res.solved["VDD"]
    from emir.decap import _basis_result
    again = _basis_result(r["network"], r["Ie"], res.proc, r["layer_map"], r)
    assert r["bidirectional"] == again["bidirectional"]
    assert r["via_crowding"] == again["via_crowding"]
    assert [c["name"] for c in r["clusters"]] == [c["name"] for c in again["clusters"]]


# ------------------------------------------------------------ the decap model


def test_a_decap_on_the_d_s_side_attaches_at_both_channel_ends(tmp_path):
    """Solving the net that holds a MOS decap's drain, source and bulk: the
    decap is C/2 with 2 R at the drain and at the source (the channel is
    contacted at both ends), so each strap carries half its current."""
    text = """*|DSPF 1.3
*|DESIGN "vss"
*|OPERATING_TEMPERATURE 100
*|NET VDD 0
*|P (VDD I *1 0.0 5.0)
*|I (MD:g MD g I 0 10.0 5.0)
Rg VDD MD:g 1.0 $metal1 $L=1 $W=1.0 $lvl=1
*|NET VSS 0
*|P (VSS I *1 0.0 0.0)
*|S (VSS:1 10.0 0.0)
*|I (MD:d MD d I 0 10.0 0.0)
*|I (MD:s MD s I 0 10.4 0.0)
*|I (MB:s MB s I 0 12.0 0.0)
Rv VSS VSS:1 2.0 $metal1 $L=10 $W=1.0 $lvl=1
Rd VSS:1 MD:d 0.5 $metal1 $L=1 $W=1.0 $lvl=1
Rs VSS:1 MD:s 0.5 $metal1 $L=1 $W=1.0 $lvl=1
Rb VSS:1 MB:s 0.5 $metal1 $L=1 $W=1.0 $lvl=1
CV VSS:1 0 1e-15
MD MD:d MD:g MD:s VSS ncap_demo L=2e-07 W=1e-04
.ENDS
"""
    d = write(tmp_path / "vss.dspf", text)
    t = np.linspace(0, 4e-9, 801)
    res_path = _psf_tran(str(tmp_path / "v.tran"), t, {
        "MB:s": -(1e-3 + 0.8e-3 * np.cos(2 * np.pi * t / 1e-9))})
    res, doc = run_doc(d, "--process", proc_file(tmp_path, 50.0), "--net", "VSS",
                       "--cc-sim", res_path, "--mode", "cc-grid", "--period", 1e-9,
                       "--dynamic", "--em-basis", "decap-aware")
    top = doc["nets"][0]["dynamic_em"]["decaps"]["top"]
    halves = sorted(x["half"] for x in top if x["name"] == "MD")
    assert halves == ["d", "s"]
    assert all(x["c_F"] == pytest.approx(CD / 2) for x in top if x["name"] == "MD")
    r = res.solved["VSS"]
    names = [x["name"] for x in r["network"].res]
    i_d, i_s = names.index("Rd"), names.index("Rs")
    assert r["Ie"]["rms"][i_d] == pytest.approx(r["Ie"]["rms"][i_s], rel=1e-6)
    assert r["Ie"]["rms"][i_d] > 0


def test_coupling_to_a_signal_net_is_not_decap(tmp_path):
    """A coupling cap from the rail to a switching signal is not counted in
    the decap-aware solve (in the CC simulation its current comes from the
    ideal rail, through no pin): EMIR-I431, its own solve; coupling to a rail
    is counted as an AC ground."""
    extra = """*|NET SIG 0
*|S (SIG:1 5.0 3.0)
*|I (MX:d MX d I 0 6.0 3.0)
Rsig SIG:1 MX:d 1.0 $metal1 $L=1 $W=1.0 $lvl=1
CCS MA:s SIG:1 0.5e-12
CCV MA:s MD:d 0.2e-12
.ENDS
.END
"""
    text = DECAP.replace(".ENDS\n.END\n", extra)
    d = write(tmp_path / "c.dspf", text)
    res_path, f = cosine(tmp_path, 1.0)
    _, doc = run_doc(d, "--process", proc_file(tmp_path, 50.0), "--net", "VDD",
                     "--cc-sim", res_path, "--mode", "cc-grid", "--period", 1 / f,
                     "--dynamic")
    em = doc["nets"][0]["dynamic_em"]
    cap = em["capacitance"]
    assert cap["coupling_signal_excluded_F"] == pytest.approx(0.5e-12)
    assert cap["coupling_quiet_F"] == pytest.approx(0.2e-12)
    assert em["numerics"]["separate_solve"]
    assert any(x["code"] == "EMIR-I431" for x in doc["notices"])


def test_decap_model_and_two_rail_warnings(tmp_path):
    """EMIR-W438 for the closed-form access resistance (R_channel/6, no
    fitted esr); EMIR-W439 when the decap's other plate is a net with
    resistance in the DSPF (each net solved alone reads its current low)."""
    two = DECAP.replace("MD MD:d MA:s MD:s", "Rd VSS MD:d 0.1 $metal1 $L=1 $W=1.0 $lvl=1\n"
                                             "Rs VSS MD:s 0.1 $metal1 $L=1 $W=1.0 $lvl=1\n"
                                             "MD MD:d MA:s MD:s")
    res_path, f = cosine(tmp_path, 1.0)
    for text, esr, code in ((DECAP, None, "EMIR-W438"), (two, 50.0, "EMIR-W439")):
        d = write(tmp_path / "w.dspf", text)
        _, doc = run_doc(d, "--process", proc_file(tmp_path, esr), "--net", "VDD",
                         "--cc-sim", res_path, "--mode", "cc-grid", "--period", 1 / f,
                         "--dynamic")
        codes = {x["code"] for x in doc["notices"]}
        assert code in codes, code
        if code == "EMIR-W439":
            assert doc["nets"][0]["dynamic_em"]["capacitance"]["two_rail"] == 1
        else:
            assert "EMIR-W439" not in codes


def test_the_feed_current_follows_branch_orientation(tmp_path):
    """The feed's current is the orientation-signed sum of its branches: two
    parallel branches written n1->n2 and n2->n1 deliver the load's current
    (as C -> 0, decap-aware equals the feed tap's), on a supply net and,
    with the current flowing the other way, on a ground net (same rms and
    peak)."""
    text = """*|DSPF 1.3
*|DESIGN "o"
*|OPERATING_TEMPERATURE 100
*|NET VDD 0
*|P (VDD I *1 0.0 0.0)
*|S (VDD:1 5.0 0.0)
*|I (MA:s MA s I 0 10.0 0.0)
Ra VDD VDD:1 2.0 $metal1 $L=5 $W=1.0 $lvl=1
Rb VDD:1 VDD 2.0 $metal1 $L=5 $W=1.0 $lvl=1
Rc VDD:1 MA:s 1.0 $metal1 $L=5 $W=1.0 $lvl=1
C1 VDD:1 0 1e-21
C2 MA:s 0 1e-21
.ENDS
"""
    d = write(tmp_path / "o.dspf", text)
    t = np.linspace(0, 4e-9, 801)
    out = {}
    for sign in (1.0, -1.0):
        res_path = _psf_tran(str(tmp_path / f"o{sign}.tran"), t, {
            "MA:s": sign * (1e-3 + 0.8e-3 * np.cos(2 * np.pi * t / 1e-9))})
        _, doc = run_doc(d, "--process", PROC, "--cc-sim", res_path, "--mode", "cc-grid",
                         "--period", 1e-9, "--dynamic")
        fe = doc["nets"][0]["dynamic_em"]["feed"]
        # the load's own rms, exact for a current linear between samples (the
        # static decap-free rms, the trapezoid of x^2, reads 2e-5 higher here)
        w = sign * (1e-3 + 0.8e-3 * np.cos(2 * np.pi * t / 1e-9))
        lin = np.sqrt(np.sum((w[:-1] ** 2 + w[:-1] * w[1:] + w[1:] ** 2) / 3 * np.diff(t))
                      / (t[-1] - t[0]))
        assert fe["i_rms_A"]["decap-aware"] == pytest.approx(lin, rel=1e-6)
        assert fe["i_rms_A"]["decap-aware"] <= fe["i_rms_A"]["decap-free"]
        assert fe["i_peak_A"]["decap-aware"] == pytest.approx(fe["i_peak_A"]["decap-free"],
                                                              rel=1e-5)
        out[sign] = fe
    assert out[1.0]["i_rms_A"] == pytest.approx(out[-1.0]["i_rms_A"])
    assert out[1.0]["i_avg_A"] == pytest.approx(-out[-1.0]["i_avg_A"])
    assert out[1.0]["i_avg_A"] == pytest.approx(1e-3, rel=1e-6)


# ------------------------------------------------------------ fallbacks


def test_fallbacks_say_why_with_their_own_code(tmp_path):
    """Not computed: the reason is machine-readable (`em_basis.fallback`) and
    has its own code; the verdict falls back to decap-free."""
    rc = write(tmp_path / "rc.dspf", DECAP)
    proc = proc_file(tmp_path, 50.0)
    res_path, f = cosine(tmp_path, 1.0)
    base = [rc, "--process", proc, "--net", "VDD", "--cc-sim", res_path, "--mode",
            "cc-grid", "--dynamic"]
    # the reference is not the feed
    _, doc = run_doc(*base, "--period", 1 / f, "--ground", "MA:s")
    n = doc["nets"][0]
    assert n["em_basis"]["fallback"] == "reference-not-feed"
    assert n["em_basis"]["used"] == "decap-free" and n["dynamic_em"] is None
    assert "EMIR-W431" in {x["code"] for x in doc["notices"]}
    # no port, so no feed: said under the default basis too (its verdict is
    # then not on the basis asked for: it once fell back without a word)
    noport = write(tmp_path / "np.dspf", DECAP.replace("*|P (VDD I *1 0.0000 0.0000)\n", ""))
    for extra in ([], ["--em-basis", "decap-aware"]):
        _, doc = run_doc(noport, *base[1:], "--period", 1 / f, *extra)
        n = next(x for x in doc["nets"] if x["net"] == "VDD")
        assert n["em_basis"]["fallback"] == "no-feed"
        assert "EMIR-W430" in {x["code"] for x in doc["notices"]}
        assert doc["em_basis"]["honoured"] is False
    # a window that is not whole periods (no period given or found)
    t = np.linspace(0, 4e-9, 801)
    ramp = _psf_tran(str(tmp_path / "ramp.tran"), t, {"MA:s": 1e-3 + 0.5e-3 * t / 4e-9})
    _, doc = run_doc(rc, "--process", proc, "--net", "VDD", "--cc-sim", ramp,
                     "--mode", "cc-grid", "--dynamic")
    assert doc["nets"][0]["em_basis"]["fallback"] == "window-not-periodic"
    assert "EMIR-W433" in {x["code"] for x in doc["notices"]}


def test_a_step_too_coarse_gives_the_finer_one(ladder, monkeypatch):
    """With sub-steps no finer than a quarter of the grid's step (as on a net
    whose factorisations exhaust the budget), a 10-step grid cannot resolve
    the spikes: decap-aware EM is not computed (EMIR-W434), the run gives the
    --dyn-steps that would, and that run computes it (the suggestion leads
    somewhere: it is never capped, so it never repeats itself)."""
    from emir import dynir
    monkeypatch.setattr(dynir, "MAX_DEPTH", 2)
    s = str(ladder["tmp"] / "coarse.json")
    _, doc = run_doc(ladder["dspf"], "--process", ladder["proc"], "--net", "VDD",
                     "--cc-sim", ladder["result"], "--mode", "cc-grid", "--period", 1e-9,
                     "--dynamic", "--dyn-steps", 10, "--summary-out", s)
    n = doc["nets"][0]
    assert n["em_basis"]["fallback"] == "step-too-coarse" and n["em_basis"]["dyn_steps"] > 10
    assert "EMIR-W434" in {x["code"] for x in doc["notices"]}
    cmds = [x["command"] for x in doc["next_steps"]]
    need = n["em_basis"]["dyn_steps"]
    assert any(f"--dyn-steps {need}" in c for c in cmds)
    # the verdict is not on the default basis there: `emir next` plans the
    # finer run as the person's step
    assert doc["em_basis"]["honoured"] is False
    plan = json.loads(emir("next", "--run-summary", s, "--format", "json").stdout)
    st = next(x for x in plan["steps"] if x["id"] == "rerun-dyn-steps")
    assert st["needs_human"] and f"--dyn-steps {need}" in st["command"]
    _, again = run_doc(ladder["dspf"], "--process", ladder["proc"], "--net", "VDD",
                       "--cc-sim", ladder["result"], "--mode", "cc-grid", "--period", 1e-9,
                       "--dynamic", "--dyn-steps", need)
    m = again["nets"][0]
    assert m["em_basis"]["fallback"] is None and m["dynamic_em"] is not None
    assert "EMIR-W434" not in {x["code"] for x in again["notices"]}


def test_not_at_periodic_steady_state_falls_back(tmp_path, monkeypatch):
    from emir import dynir
    real = dynir.transient
    monkeypatch.setattr(dynir, "transient", lambda *a, **k: real(*a, **{**k, "max_passes": 1}))
    rc = write(tmp_path / "rc.dspf", DECAP)
    res_path, f = cosine(tmp_path, 1.0)
    _, doc = run_doc(rc, "--process", proc_file(tmp_path, 50.0), "--net", "VDD",
                     "--cc-sim", res_path, "--mode", "cc-grid", "--period", 1 / f,
                     "--dynamic")
    assert doc["nets"][0]["em_basis"]["fallback"] == "not-settled"
    assert "EMIR-W432" in {x["code"] for x in doc["notices"]}


def test_r_only_and_harmonic_results_keep_w450(tmp_path):
    """No capacitance: EMIR-W450 says so (as before), and no decap code on top."""
    text, pins = mesh_text(c=0.0)
    text = "\n".join(x for x in text.splitlines() if not x.startswith("Cn")) + "\n"
    d = write(tmp_path / "r.dspf", text)
    _, doc = run_doc(d, "--process", PROC, "--cc-sim", mesh_pulses(tmp_path, pins),
                     "--mode", "cc-grid", "--period", 1e-9, "--dynamic")
    n = doc["nets"][0]
    assert n["em_basis"]["fallback"] == "no-capacitance" and n["dynamic"] is None
    codes = {x["code"] for x in doc["notices"]}
    assert "EMIR-W450" in codes and not codes & {f"EMIR-W43{i}" for i in range(10)}


@pytest.mark.parametrize("extra", [[], ["--force"], ["--no-check"]])
def test_a_basis_that_needs_dynamic_is_refused_without_it(tmp_path, extra):
    """--em-basis decap-aware without --dynamic: EMIR-E430, exit 2, before the
    input check (--force and --no-check do not run past it); its next step
    adds --dynamic."""
    rc = write(tmp_path / "rc.dspf", DECAP)
    res_path, f = cosine(tmp_path, 1.0)
    p = emir("run", rc, "--process", proc_file(tmp_path), "--net", "VDD", "--cc-sim",
             res_path, "--mode", "cc-grid", "--period", 1 / f, "--em-basis", "decap-aware",
             *extra, "--format", "json")
    assert p.returncode == 2
    doc = json.loads(p.stdout)
    assert doc["code"] == "EMIR-E430"
    assert doc["next_steps"] and "--dynamic" in doc["next_steps"][0]["command"]
    p = emir("check", rc, "--process", proc_file(tmp_path), "--net", "VDD", "--cc-sim",
             res_path, "--mode", "cc-grid", "--em-basis", "worst", "--format", "json")
    assert p.returncode == 2
    assert "EMIR-E430" in [x["code"] for x in json.loads(p.stdout)["findings"]
                           if x["level"] == "FAIL"]


def test_e430_names_what_blocks_dynamic_and_gives_no_command(tmp_path):
    """With DC --currents, --dynamic would be refused too (EMIR-E550): the
    message names it and no command is given (it would lead to a refusal)."""
    from _helpers import CUR, DSPF
    p = emir("run", DSPF, "--process", PROC, "--net", "VDD", "--currents", CUR,
             "--em-basis", "decap-aware", "--format", "json")
    doc = json.loads(p.stdout)
    assert p.returncode == 2 and doc["code"] == "EMIR-E430"
    assert "--currents" in doc["message"] and doc["next_steps"] == []


def test_rcc_measures_the_decaps(tmp_path):
    from _helpers import DSPF, rcc_result
    _, doc = run_doc(DSPF, "--process", PROC, "--net", "VDD", "--cc-sim",
                     rcc_result(tmp_path), "--mode", "rcc", "--period", 1e-9,
                     "--em-basis", "decap-aware")
    n = doc["nets"][0]
    assert n["em_basis"]["used"] == "measured" and n["dynamic_em"] is None
    assert "EMIR-I432" in {x["code"] for x in doc["notices"]}


# ------------------------------------------------------------ the agent contract


def test_the_report_names_the_basis_and_the_choice(tmp_path):
    """The headline names the basis and the other bases' verdicts; the Nets
    table says which basis each verdict is on; "Decap-aware EM" comes right
    after it with the segments of both bases; the bases disagree, so the
    person's choice is in the report, each with its command."""
    from emir.report import build, markdown
    s = str(tmp_path / "s.json")
    _, doc = strap_run(tmp_path, summary=s)
    r = build(json.load(open(s)), s)
    md = markdown(r)
    assert "**Verdict: FAIL** (EM basis worst (the default); decap-free CLEAN 0.313x)" in md
    assert "| VDD | FAIL (basis worst) |" in md
    assert md.index("## Decap-aware EM") < md.index("## Worst items")
    assert "The person's choice" in md
    assert f"emir next --run-summary {s} --em-basis decap-free" in md
    assert "| VDD | Rst |" in md and "decap-aware |" in md
    rep = json.loads(json.dumps({"schema": "emir.report/1", "emir_version": "x", **r,
                                 "markdown": md}, default=float))
    valid(rep, "report")
    assert [c["basis"] for c in r["em_choices"]] == ["decap-free", "decap-aware", "worst"]
    assert [c["verdict"] for c in r["em_choices"]] == ["CLEAN", "FAIL", "FAIL"]


def test_a_chosen_basis_is_a_warning_the_report_shows(tmp_path):
    from emir.report import build, markdown
    s = str(tmp_path / "s.json")
    strap_run(tmp_path, "--em-basis", "decap-free", summary=s)
    md = markdown(build(json.load(open(s)), s))
    warn = md[md.index("## Warnings raised during the run"):]
    assert "EMIR-W437" in warn and "EMIR-W436" in warn
    assert "(EM basis decap-free (--em-basis); decap-aware FAIL 1.031x, worst FAIL 1.031x)" \
        in md


def test_next_asks_the_person_after_the_run(tmp_path):
    """`emir next --run-summary S`: the run's inputs come from the summary; the
    bases disagree, so the first step is the person's choice (needs_human,
    with each basis's verdict), and no new run of what the summary holds."""
    s = str(tmp_path / "s.json")
    strap_run(tmp_path, summary=s)
    p = emir("next", "--run-summary", s, "--format", "json")
    assert p.returncode == 0, p.stderr
    plan = json.loads(p.stdout)
    valid(plan, "next")
    st = plan["steps"][0]
    assert st["id"] == "choose-em-basis" and st["needs_human"]
    assert [c["verdict"] for c in st["choices"]] == ["CLEAN", "FAIL", "FAIL"]
    assert st["choices"][2]["recommended"] and st["status"] == "waiting"
    assert not any(x["id"] in ("run", "check") for x in plan["steps"])
    assert plan["inputs"]["dspf"] == json.load(open(s))["dspf"]
    assert "their choice" in p.stderr or "ASK THE PERSON FIRST" in p.stderr
    # the choice re-plans: the same run with the basis, which loosens the
    # verdict, so the person decides
    p = emir("next", "--run-summary", s, "--em-basis", "decap-free", "--format", "json")
    plan = json.loads(p.stdout)
    run = next(x for x in plan["steps"] if x["id"] == "run")
    assert "--em-basis decap-free" in run["command"] and run["needs_human"]
    assert "--summary-out" in run["command"]
    assert any(x["id"] == "report" for x in plan["steps"])


def test_next_after_a_run_on_a_chosen_basis_asks_to_confirm(tmp_path):
    s = str(tmp_path / "s.json")
    rc = write(tmp_path / "rc.dspf", DECAP)
    res_path, f = cosine(tmp_path, 1.0)
    run_doc(rc, "--process", proc_file(tmp_path), "--net", "VDD", "--cc-sim", res_path,
            "--mode", "cc-grid", "--period", 1 / f, "--dynamic", "--em-basis",
            "decap-aware", "--summary-out", s)
    plan = json.loads(emir("next", "--run-summary", s, "--format", "json").stdout)
    st = next(x for x in plan["steps"] if x["id"] == "confirm-em-basis")
    assert st["needs_human"] and st["command"] == f"emir report --from {s}"


def test_next_plans_dynamic_and_the_basis_before_the_run(tmp_path):
    rc = write(tmp_path / "rc.dspf", DECAP)
    res_path, f = cosine(tmp_path, 1.0)
    p = emir("next", "--dspf", rc, "--process", proc_file(tmp_path), "--cc-sim", res_path,
             "--net", "VDD", "--mode", "cc-grid", "--em-basis", "decap-aware",
             "--format", "json")
    plan = json.loads(p.stdout)
    run = next(x for x in plan["steps"] if x["id"] == "run")
    assert "--dynamic --em-basis decap-aware" in run["command"] and run["needs_human"]
    chk = next(x for x in plan["steps"] if x["id"] == "check")
    assert "--dynamic --em-basis decap-aware" in chk["command"]
    assert any("--dynamic is added" in x for x in plan["advice"])


def test_next_refuses_a_file_that_is_not_a_run_summary(tmp_path):
    bad = write(tmp_path / "x.json", '{"schema": "emir.check/1"}')
    p = emir("next", "--run-summary", bad, "--format", "json")
    assert p.returncode == 2 and json.loads(p.stdout)["code"] == "EMIR-E104"


def test_the_run_points_at_the_choice_when_the_bases_disagree(tmp_path):
    s = str(tmp_path / "s.json")
    _, doc = strap_run(tmp_path, summary=s)
    cmds = [x["command"] for x in doc["next_steps"]]
    assert f"emir next --run-summary {s}" in cmds


def test_mcp_a_looser_basis_needs_the_persons_go_ahead(tmp_path):
    from emir.mcp_tools import by_command, gates
    spec = by_command("run")
    rc = write(tmp_path / "rc.dspf", DECAP)
    # with the period: a dynamic run without one has a gate of its own
    base = [rc, "--process", PROC, "--cc-sim", "x.tran", "--dynamic", "--period", "1e-9"]
    assert gates("run", base + ["--em-basis", "decap-aware"], spec.parser)
    assert gates("run", base + ["--em-basis", "decap-free"], spec.parser)
    assert not gates("run", base + ["--em-basis", "worst"], spec.parser)
    assert not gates("run", base, spec.parser)
    assert "em_basis" in {p.name for p in by_command("run").params}
    assert "em_basis" in {p.name for p in by_command("check").params}
    assert {"run_summary", "em_basis", "dynamic"} <= {p.name for p in by_command("next").params}


def test_console_and_html_show_both_bases(tmp_path, capsys):
    res, doc = strap_run(tmp_path, "--html", str(tmp_path / "html"))
    out = capsys.readouterr().out
    assert "decap-aware EM : verdict on worst (default)" in out
    assert "decap-free CLEAN" in out and "decap-aware FAIL" in out
    html = next((tmp_path / "html").glob("*_VDD.html")).read_text()
    assert "Decap-aware EM" in html and "Decap currents" in html and "Rst" in html


def test_the_summary_and_report_validate(tmp_path):
    s = str(tmp_path / "s.json")
    _, doc = strap_run(tmp_path, summary=s)
    valid(doc, "run")
    p = emir("report", "--from", s, "--format", "json")
    assert p.returncode == 0
    valid(json.loads(p.stdout), "report")


def test_an_esr_fitted_far_from_the_window_is_flagged(tmp_path):
    """ESR depends on frequency: a fit made more than 10x away from the
    window's fundamental is named (EMIR-W438), one near it is not."""
    rc = write(tmp_path / "rc.dspf", DECAP)
    res_path, f = cosine(tmp_path, 1.0)
    for fit, said in ((f * 1e3, True), (f * 2, False)):
        _, doc = run_doc(rc, "--process", proc_file(tmp_path, 50.0, esr_fit_freq=fit),
                         "--net", "VDD", "--cc-sim", res_path, "--mode", "cc-grid",
                         "--period", 1 / f, "--dynamic")
        msgs = [x["message"] for x in doc["notices"] if x["code"] == "EMIR-W438"]
        assert bool(msgs and "fitted at" in msgs[0]) is said, (fit, msgs)


# ------------------------------------------------------------ the verdict's basis is never loosened silently


def loose_case(tmp):
    """Example 13's small case with every limit x 1.6: decap-free CLEAN
    (0.728x), the default worst FAIL (1.574x, the decaps' straps)."""
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir, "examples",
                                    "13_decap_aware"))
    import decap_mesh
    case = decap_mesh.small_case(str(tmp / "small"))
    with open(case["process"], encoding="utf-8") as f:
        d = yaml.safe_load(f)
    for sec in ("layers", "vias"):
        for e in d[sec].values():
            for k in list(e):
                if k.startswith(("jmax_", "imax_")):
                    e[k] *= 1.6
    case["process"] = write(tmp / "loose.yaml", yaml.safe_dump(d))
    return case


@pytest.mark.parametrize("extra,why,code", [
    (["--ground", "VDD:6_9"], "reference-not-feed", "EMIR-W431"),
    ([], "window-not-periodic", "EMIR-W433")])
def test_a_fallback_never_loosens_the_default_verdict_silently(tmp_path, extra, why, code):
    """Under the default basis (worst), an option that keeps decap-aware EM
    from being computed turned FAIL 1.575x into CLEAN 0.728x with exit 0 and
    a report that named no basis. Now: the summary's em_basis.honoured is
    false with the net and the reason, --fail-on-violation exits 1 and says
    why, the report's headline says the verdict is decap-free there, and
    `emir next --run-summary` plans the fix as the person's step."""
    c = loose_case(tmp_path)
    s = str(tmp_path / "s.json")
    args = [c["dspf"], "--process", c["process"], "--cc-sim", c["raw"], "--net", "VDD",
            "--mode", "cc-grid", "--dynamic", "--summary-out", s, "--fail-on-violation",
            "--format", "json", *extra]
    if why != "window-not-periodic":
        args += ["--period", c["period"]]
    p = emir("run", *args)
    doc = json.loads(p.stdout)
    assert doc["verdict"] == "CLEAN" and doc["nets"][0]["em_basis"]["fallback"] == why
    assert p.returncode == 1, p.stderr[-1500:]
    assert f"EM basis worst not computed on VDD ({why})" in p.stderr
    assert doc["em_basis"]["honoured"] is False
    assert doc["em_basis"]["not_computed"][0]["net"] == "VDD"
    assert doc["em_basis"]["used"] == ["decap-free"]
    w = [x["message"] for x in doc["notices"] if x["code"] == code]
    assert w and "NOT the worst basis (the default)" in w[0]
    valid(doc, "run")
    from emir.report import build, markdown
    md = markdown(build(json.load(open(s)), s))
    assert "**Verdict: CLEAN** (EM basis decap-free on VDD: worst (the default) not computed" \
        in md and "does not cover a decap's straps" in md
    plan = json.loads(emir("next", "--run-summary", s, "--format", "json").stdout)
    valid(plan, "next")
    fix = [x for x in plan["steps"] if x["id"].startswith("rerun-")]
    assert fix and all(x["needs_human"] for x in fix)
    # the person may then choose a basis: the re-run says what was computed
    # (it once said "every basis was computed already" after a fallback)
    again = json.loads(emir("next", "--run-summary", s, "--em-basis", "decap-aware",
                            "--format", "json").stdout)
    rr = next(x for x in again["steps"] if x["id"] == "run")
    assert "computed already" not in rr["why"] and "was not computed" in rr["why"]
    if why == "reference-not-feed":
        assert "--ground" not in fix[0]["command"] and fix[0]["command"].startswith("emir run")
    else:
        assert "--period '<period>'" in fix[0]["command"] or "--period <period>" in \
            fix[0]["command"]
        assert any("period" in m["what"] for m in plan["missing"])


def test_the_default_run_of_the_loose_case_fails_honoured(tmp_path):
    """The same block with every option right: FAIL 1.574x on the straps,
    em_basis.honoured true, exit 1 for the FAIL itself."""
    c = loose_case(tmp_path)
    p = emir("run", c["dspf"], "--process", c["process"], "--cc-sim", c["raw"], "--net",
             "VDD", "--mode", "cc-grid", "--period", c["period"], "--dynamic",
             "--fail-on-violation", "--format", "json")
    doc = json.loads(p.stdout)
    assert p.returncode == 1 and "FAIL verdict" in p.stderr
    assert doc["verdict"] == "FAIL" and doc["em_basis"]["honoured"] is True
    assert doc["nets"][0]["max_ratio"]["all"] == pytest.approx(1.574, rel=1e-2)


def test_mcp_gates_options_that_would_loosen_the_verdict(tmp_path):
    """Over MCP, --dynamic with --ground, or with --dyn-steps below the
    default rule's least (2000), keeps decap-aware EM from being computed:
    the person's go-ahead first (EMIR-E590), as for a looser basis."""
    from emir.mcp_tools import by_command, gates
    spec = by_command("run")
    # with the period (without it, a gate of its own: the next test)
    base = ["x.dspf", "--process", PROC, "--cc-sim", "x.tran", "--dynamic", "--period",
            "1e-9"]
    assert gates("run", base + ["--ground", "VDD:3"], spec.parser)
    assert gates("run", base + ["--dyn-steps", "30"], spec.parser)
    assert not gates("run", base + ["--dyn-steps", "4000"], spec.parser)
    assert not gates("run", base, spec.parser)
    # decap-free asked for (itself gated): --ground no longer loosens anything
    g = gates("run", base + ["--em-basis", "decap-free", "--ground", "VDD:3"], spec.parser)
    assert len(g) == 1


def test_mcp_gates_dynamic_without_a_period_and_a_long_step_count(tmp_path):
    """Over MCP, `dynamic` with no `period` ran without the person's go-ahead
    and could leave decap-aware EM not computed (EMIR-W433: the detection
    needs more periods than a short window holds), and a `dyn_steps` above
    the default rule's most (200000, a long run in `emir next`) was not
    gated either."""
    from emir.mcp_tools import GATE_WHEN, by_command, gates
    spec = by_command("run")
    base = ["x.dspf", "--process", PROC, "--cc-sim", "x.tran", "--mode", "cc-grid",
            "--dynamic"]
    g = gates("run", base, spec.parser)
    assert len(g) == 1 and "period" in g[0].reason and "EMIR-W433" in g[0].reason
    assert not gates("run", base + ["--period", "1e-9"], spec.parser)
    # decap-free asked for (itself gated): a missing period loosens nothing more
    assert len(gates("run", base + ["--em-basis", "decap-free"], spec.parser)) == 1
    g = gates("run", base + ["--period", "1e-9", "--dyn-steps", "400000"], spec.parser)
    assert len(g) == 1 and g[0].long_run and "200000" in g[0].reason
    assert not gates("run", base + ["--period", "1e-9", "--dyn-steps", "200000"],
                     spec.parser)
    assert any("period" in w for w in GATE_WHEN["run"])
    assert any("200000" in w for w in GATE_WHEN["run"])


def test_the_outputs_name_the_basis(tmp_path):
    """The HTML index row, the net page's worst-EM tile and violations
    heading, its JSON sidecar, the viewer data and its KLayout markers, the
    report's Worst items: each says which EM basis the numbers are on."""
    c = loose_case(tmp_path)
    h, v = tmp_path / "H", tmp_path / "V"
    s = str(tmp_path / "s.json")
    run_doc(c["dspf"], "--process", c["process"], "--cc-sim", c["raw"], "--net", "VDD",
            "--mode", "cc-grid", "--period", c["period"], "--dynamic", "--em-basis",
            "decap-aware", "--html", h, "--viewer", v, "--summary-out", s)
    index = (h / "index.html").read_text()
    assert "basis decap-aware (--em-basis)" in index
    side = json.loads(next(h.glob("*_VDD.json")).read_text())
    assert side["em_basis"] == "decap-aware"
    page = next(h.glob("*_VDD.html")).read_text()
    assert "Rsd2); basis decap-aware (--em-basis)" in page
    assert "× · basis decap-aware (--em-basis)</h2>" in page
    viz = json.loads((v / "emir_viz.json").read_text())
    assert viz["em_basis"] == {"VDD": "basis decap-aware (--em-basis)"}
    assert all(x["basis"] == "basis decap-aware (--em-basis)" for x in viz["em"])
    assert "emirEmBasis" in (v / "emir_data.il").read_text()
    if (v / "emir.lyrdb").exists():
        assert "(basis decap-aware (--em-basis))" in (v / "emir.lyrdb").read_text()
    from emir.report import build, markdown
    md = markdown(build(json.load(open(s)), s))
    wi = md[md.index("## Worst items"):]
    assert "Ratios on each net's EM basis: VDD basis decap-aware (--em-basis)." in wi


def test_a_net_with_decap_em_must_name_its_basis(tmp_path):
    """The run schema refuses a net that carries dynamic_em and no em_basis
    (it accepted one), and accepts the same net with it."""
    _, doc = strap_run(tmp_path)
    valid(doc, "run")
    bad = json.loads(json.dumps(doc))
    del bad["nets"][0]["em_basis"]
    with pytest.raises(jsonschema.ValidationError):
        valid(bad, "run")


@pytest.mark.parametrize("model,what", [
    ({"cox": 1.0, "esr_a": -5.0}, "`esr_a` must be a non-negative number"),
    ({"cox": -0.01, "esr_a": 50.0}, "`cox` must be a non-negative number"),
    ({"cox": 1.0, "esr_a": 50.0, "esr_fit_freq": 0}, "`esr_fit_freq` must be a positive"),
    ({"cox": 1.0, "esr_a": 50.0, "esr_w_range": [5, 1]}, "`esr_w_range` must be [low, high]"),
    # a fit that is negative at the DSPF's own decap (W 100 um): `emir check`
    # passed it and the planned run then failed (now the check says so)
    ({"cox": 1.0, "esr_a": 0.1, "esr_w": -0.01}, "is -0.9 ohm, negative")])
def test_an_unusable_cap_model_is_refused(tmp_path, model, what):
    """A negative ESR was used silently (decap-aware 2.519 -> 3.914), a
    negative cox ended in a misleading not-settled fallback, a zero fit
    frequency passed: `emir check` FAILs them (EMIR-E317, exit 2), and a run
    past the check (--no-check) is refused before the dynamic solve."""
    rc = write(tmp_path / "rc.dspf", DECAP)
    res_path, f = cosine(tmp_path, 1.0)
    pp = proc_file(tmp_path, None, **model)
    p = emir("check", rc, "--process", pp, "--net", "VDD", "--cc-sim", res_path, "--mode",
             "cc-grid", "--period", 1 / f, "--dynamic", "--format", "json")
    assert p.returncode == 2
    fails = [x for x in json.loads(p.stdout)["findings"] if x["level"] == "FAIL"]
    assert any(x["code"] == "EMIR-E317" and what in x["message"] for x in fails), fails
    p = emir("run", rc, "--process", pp, "--net", "VDD", "--cc-sim", res_path, "--mode",
             "cc-grid", "--period", 1 / f, "--dynamic", "--no-check", "--format", "json")
    assert p.returncode == 2 and json.loads(p.stdout)["code"] == "EMIR-E317"


def test_a_cox_only_decap_is_said_to_be_ideal(tmp_path):
    """A MOS model with cox and no access resistance is an ideal capacitor:
    W438 says the feed path reads low and the straps high (it once said the
    opposite, as for the closed-form R_channel/6)."""
    rc = write(tmp_path / "rc.dspf", DECAP)
    res_path, f = cosine(tmp_path, 1.0)
    pp = proc_file(tmp_path, None, ron_sq=0.0)
    with open(pp, encoding="utf-8") as fh:
        d = yaml.safe_load(fh)
    d["cap_models"]["ncap_demo"] = {"cox": 1.0}
    pp = write(tmp_path / "coxonly.yaml", yaml.safe_dump(d))
    _, doc = run_doc(rc, "--process", pp, "--net", "VDD", "--cc-sim", res_path, "--mode",
                     "cc-grid", "--period", 1 / f, "--dynamic")
    w = [x["message"] for x in doc["notices"] if x["code"] == "EMIR-W438"]
    assert w and "no access resistance at all" in w[0] and "straps high" in w[0]
    assert "several-fold high" not in w[0]


def test_decaps_without_dynamic_are_said_not_covered(tmp_path):
    """A cc-grid run over a net with decap devices and no --dynamic: the
    static verdict gives the straps nothing. It said nothing; now EMIR-W428,
    the net's em_basis (fallback no-dynamic), and `emir next` advises
    --dynamic as the person's choice, before the run and after it (`emir
    next --run-summary` once planned only the report: the same run with
    --dynamic is now the person's step)."""
    c = loose_case(tmp_path)
    s = str(tmp_path / "s.json")
    _, doc = run_doc(c["dspf"], "--process", c["process"], "--cc-sim", c["raw"], "--net",
                     "VDD", "--mode", "cc-grid", "--period", c["period"], "--summary-out", s)
    n = doc["nets"][0]
    assert n["em_basis"]["fallback"] == "no-dynamic" and n["em_basis"]["used"] == "decap-free"
    assert "EMIR-W428" in {x["code"] for x in doc["notices"]}
    assert doc["em_basis"]["requested"] == "decap-free" and doc["em_basis"]["honoured"]
    valid(doc, "run")
    plan = json.loads(emir("next", "--dspf", c["dspf"], "--process", c["process"], "--cc-sim",
                           c["raw"], "--net", "VDD", "--mode", "cc-grid", "--period",
                           c["period"], "--format", "json").stdout)
    adv = [x for x in plan["advice"] if "decap device" in x]
    assert adv and "--dynamic" in adv[0] and "ask the person" in adv[0]
    run = next(x for x in plan["steps"] if x["id"] == "run")
    assert "--dynamic" not in run["command"]
    after = json.loads(emir("next", "--run-summary", s, "--format", "json").stdout)
    valid(after, "next")
    st = next(x for x in after["steps"] if x["id"] == "rerun-with-dynamic")
    assert st["needs_human"] and "EMIR-W428" in st["why"]
    import shlex
    cmd = shlex.split(st["command"])
    assert cmd[:2] == ["emir", "run"] and "--dynamic" in cmd and "--format" in cmd
    assert cmd[cmd.index("--period") + 1] == str(c["period"]) and "<period>" not in cmd
    assert [x["id"] for x in after["steps"]][-1] == "report"


def _without_cox(tmp_path, c) -> str:
    """The case's process file with its decap model's cox left empty, as
    `emir process new` writes it for a person to fill."""
    with open(c["process"], encoding="utf-8") as f:
        d = yaml.safe_load(f)
    d["cap_models"]["ncap"]["cox"] = None
    return write(tmp_path / "nocox.yaml", yaml.safe_dump(d))


def test_decaps_cap_models_cannot_size_are_not_a_silent_pass(tmp_path):
    """A cap model left empty (cox: null) dropped every decap from the
    decap-aware solve in silence: the default basis read CLEAN 0.728x, the
    report said "the other bases agree" and "Every item the run found was
    checked", and --fail-on-violation exited 0, where the same block with
    its cox FAILs at 1.574x on the decaps' straps. Now the basis asked for is
    not honoured (decaps-not-sized: exit 1 with --fail-on-violation), the
    straps are listed as not checked, W409 says so, `emir next --run-summary`
    asks the person for the value, and `emir process check --dspf` and
    `emir check --dynamic` warn before any run."""
    import shlex
    c = loose_case(tmp_path)
    p = _without_cox(tmp_path, c)
    s = str(tmp_path / "s.json")
    base = [c["dspf"], "--process", p, "--cc-sim", c["raw"], "--net", "VDD", "--mode",
            "cc-grid", "--period", c["period"], "--dynamic"]
    r = emir("run", *base, "--fail-on-violation", "--summary-out", s, "--format", "json")
    doc = json.loads(r.stdout)
    assert doc["verdict"] == "CLEAN"
    assert r.returncode == 1, r.stderr[-1500:]
    assert "EM basis worst not computed on VDD (decaps-not-sized)" in r.stderr
    eb = doc["em_basis"]
    assert eb["honoured"] is False and eb["used"] == ["worst"]
    nc = eb["not_computed"][0]
    assert (nc["net"], nc["fallback"], nc["models"]) == ("VDD", "decaps-not-sized", ["ncap"])
    assert doc["nets"][0]["em_basis"]["decaps_not_sized"] == {"n": 4, "models": ["ncap"]}
    unc = doc["not_checked"]["decap_straps_not_covered"]["VDD"]
    assert [(x["reason"], x["code"], x["n"]) for x in unc] == [("not-sized", "EMIR-W409", 4)]
    w = next(x["message"] for x in doc["notices"] if x["code"] == "EMIR-W409")
    assert "straps" in w and "NOT checked" in w
    valid(doc, "run")
    from emir.report import build, markdown
    md = markdown(build(json.load(open(s)), s))
    head = next(x for x in md.splitlines() if x.startswith("**Verdict:"))
    assert "cap_models cannot size" in head and "the other bases agree" not in head
    tail = md[md.index("## Not checked"):]
    assert "Every item the run found was checked" not in tail
    assert "VDD: 4 decap device(s) that cap_models cannot size (ncap)" in tail
    plan = json.loads(emir("next", "--run-summary", s, "--format", "json").stdout)
    valid(plan, "next")
    assert any("ncap" in m["what"] and "cox" in m["how"] for m in plan["missing"]), plan
    agree = [x for x in plan["advice"] if x.startswith("the EM bases agree")]
    assert agree and "without the decaps cap_models cannot size on VDD" in agree[0]
    # the run's own next steps lead there
    steps = [shlex.split(x["command"]) for x in doc["next_steps"]]
    assert any(x[:3] == ["emir", "next", "--run-summary"] for x in steps), steps
    # before any run
    d = json.loads(emir("process", "check", p, "--dspf", c["dspf"], "--format", "json").stdout)
    assert [x["level"] for x in d["findings"] if x["code"] == "EMIR-W409"] == ["WARN"], d
    d = json.loads(emir("check", *base, "--format", "json").stdout)
    assert [x["level"] for x in d["findings"] if x["code"] == "EMIR-W409"] == ["WARN"], d
    # with cox given: no such warning, honoured, the FAIL on the straps
    d = json.loads(emir("check", base[0], "--process", c["process"], *base[3:],
                        "--format", "json").stdout)
    assert "EMIR-W409" not in {x["code"] for x in d["findings"]}


def test_the_chart_and_not_checked_name_the_basis_and_the_straps(tmp_path, monkeypatch):
    """The chart (its scene) named no EM basis, and neither it nor the
    report's Not checked said that a decap's straps were left unchecked:
    on the decap-free basis (EMIR-W436) or without --dynamic (EMIR-W428),
    where the basis was also labelled "decap-free (the default)" although
    the default is worst. The scene is taken where --html draws the chart
    (W428's basis was once set only after the chart and the pages)."""
    from emir import scene as sc_mod
    from emir.report import build, markdown
    from emir.scene import run_scene
    c = loose_case(tmp_path)
    h = tmp_path / "H"
    base = [c["dspf"], "--process", c["process"], "--cc-sim", c["raw"], "--net", "VDD",
            "--mode", "cc-grid", "--period", c["period"], "--html", h]
    drawn = {}

    def at_the_chart(solved, d, proc, out_dir, **_kw):
        drawn["scene"] = run_scene(solved, d, proc, subtitle="emir run")
        return []
    monkeypatch.setattr(sc_mod, "write_charts", at_the_chart)

    def scene(_res):
        return drawn.pop("scene")
    res, doc = run_doc(*base, "--dynamic", "--em-basis", "decap-free")
    unc = doc["not_checked"]["decap_straps_not_covered"]["VDD"]
    assert [(x["reason"], x["code"]) for x in unc] == [("basis", "EMIR-W436")]
    assert unc[0]["n"] >= 1 and "Rsd2" in unc[0]["items"]
    sc = scene(res)
    assert "EM basis decap-free (--em-basis)" in sc["subtitle"]
    assert "read higher with their decaps" in sc["unchecked"]
    valid(doc, "run")
    res, doc = run_doc(*base, "--dynamic")
    assert not doc["not_checked"].get("decap_straps_not_covered")
    sc = scene(res)
    assert "EM basis worst (the default)" in sc["subtitle"]
    # the IR panel is the average drop on a --dynamic run, and says so; the
    # dynamic droop's peak is in a note (it was in the report only)
    dy = doc["nets"][0]["dynamic"]
    assert sc["ir_kind"] == "average"
    assert sc["notes"][0].startswith("IR is the average (static) drop; the dynamic droop "
                                     f"(open loop) peaks at {dy['worst_mV']:.4g} mV")
    s = str(tmp_path / "s.json")
    res, doc = run_doc(*base, "--summary-out", s)
    assert doc["nets"][0]["em_basis"]["source"] == "no-dynamic"
    assert doc["em_basis"]["source"] == "no-dynamic"
    unc = doc["not_checked"]["decap_straps_not_covered"]["VDD"]
    assert [(x["reason"], x["code"], x["n"]) for x in unc] == [("no-dynamic", "EMIR-W428", 4)]
    sc = scene(res)
    assert "EM basis decap-free (no --dynamic)" in sc["subtitle"] and sc["ir_kind"] == "drop"
    assert "no --dynamic" in sc["unchecked"]
    assert "basis decap-free (no --dynamic)" in (h / "index.html").read_text()
    valid(doc, "run")
    md = markdown(build(json.load(open(s)), s))
    assert "VDD basis decap-free (no --dynamic)." in md and "(the default)" not in md
    tail = md[md.index("## Not checked"):]
    assert "VDD: 4 decap device(s) and no --dynamic" in tail


def test_e430_from_run_gives_a_command_that_runs(tmp_path):
    """`emir run ... --fail-on-violation --em-basis worst` without
    --dynamic: E430's next step (`emir check ... --dynamic`) once carried
    --fail-on-violation, which check refuses (E507). It runs as printed."""
    rc = write(tmp_path / "rc.dspf", DECAP)
    res_path, f = cosine(tmp_path, 1.0)
    p = emir("run", rc, "--process", proc_file(tmp_path), "--net", "VDD", "--cc-sim",
             res_path, "--mode", "cc-grid", "--period", 1 / f, "--em-basis", "worst",
             "--fail-on-violation", "--format", "json")
    doc = json.loads(p.stdout)
    assert doc["code"] == "EMIR-E430"
    cmd = doc["next_steps"][0]["command"]
    assert cmd.startswith("emir check ") and "--fail-on-violation" not in cmd
    import shlex
    q = emir(*shlex.split(cmd)[1:])
    assert q.returncode == 0, q.stdout[-1500:] + q.stderr[-1500:]


def test_next_texts_follow_the_plan(tmp_path):
    """No `--dynamic also gives` advice when the plan has no --dynamic; every
    choice's command keeps --format; a summary without its argv still gets
    the re-run, as the person's step built from what it records."""
    c = loose_case(tmp_path)
    base = ["--dspf", c["dspf"], "--process", c["process"], "--cc-sim", c["raw"], "--net",
            "VDD", "--mode", "cc-grid", "--period", c["period"]]
    plan = json.loads(emir("next", *base, "--em-basis", "decap-free", "--format",
                           "json").stdout)
    assert not any("--dynamic also gives" in x for x in plan["advice"])
    assert any("without --dynamic" in x for x in plan["advice"])
    s = str(tmp_path / "s.json")
    run_doc(c["dspf"], "--process", c["process"], "--cc-sim", c["raw"], "--net", "VDD",
            "--mode", "cc-grid", "--period", c["period"], "--dynamic", "--summary-out", s)
    plan = json.loads(emir("next", "--run-summary", s, "--format", "json").stdout)
    st = plan["steps"][0]
    assert st["id"] == "choose-em-basis"
    assert all(ch["command"].endswith("--format json") for ch in st["choices"])
    doc = json.load(open(s))
    doc.pop("argv")
    doc.pop("command", None)
    s2 = write(tmp_path / "noargv.json", json.dumps(doc))
    plan = json.loads(emir("next", "--run-summary", s2, "--em-basis", "decap-aware",
                           "--format", "json").stdout)
    run = next(x for x in plan["steps"] if x["id"] == "run")
    assert run["needs_human"] and "--em-basis decap-aware" in run["command"]
    assert c["dspf"] in run["command"] and "--dynamic" in run["command"]


def test_a_valued_cap_to_a_resistive_rail_is_two_rail(tmp_path):
    """A valued capacitor between VDD and a VSS that has resistance in the
    DSPF (a MIM decap between two meshes): each net solved alone reads its
    straps low, so EMIR-W439 (it was raised for devices and MOS decaps only;
    the strap then read 12 % low in silence)."""
    text = DECAP.replace("MD MD:d MA:s MD:s VSS ncap_demo L=2e-07 W=1e-04\n", "").replace(
        ".ENDS\n.END\n", """*|S (VSS:x 10.0000 -4.0000)
Rvx VSS VSS:x 10.0 $metal1 $L=1 $W=1.0 $lvl=1
CMIM MA:s VSS:x 10e-12
.ENDS
.END
""")
    assert "ncap_demo" not in text
    d = write(tmp_path / "two.dspf", text)
    res_path, f = cosine(tmp_path, 1.0)
    _, doc = run_doc(d, "--process", proc_file(tmp_path, 50.0), "--net", "VDD", "--cc-sim",
                     res_path, "--mode", "cc-grid", "--period", 1 / f, "--dynamic")
    cap = doc["nets"][0]["dynamic_em"]["capacitance"]
    assert cap["two_rail"] == 1 and cap["coupling_quiet_F"] == pytest.approx(10e-12)
    assert "EMIR-W439" in {x["code"] for x in doc["notices"]}


def test_decaps_on_the_feed_node_are_not_counted(tmp_path):
    """A decap on the feed node sees no dV/dt and carries nothing: it is
    reported apart (on_feed_F) and EMIR-I430 no longer counts it as
    decoupling at work."""
    text = DECAP.replace("MD MD:d MA:s MD:s", "MD MD:d VDD MD:s")
    d = write(tmp_path / "feed.dspf", text)
    res_path, f = cosine(tmp_path, 1.0)
    _, doc = run_doc(d, "--process", proc_file(tmp_path, 50.0), "--net", "VDD", "--cc-sim",
                     res_path, "--mode", "cc-grid", "--period", 1 / f, "--dynamic")
    em = doc["nets"][0]["dynamic_em"]
    assert em["capacitance"]["on_feed_F"] == pytest.approx(CD)
    assert em["capacitance"]["counted_F"] == pytest.approx(CW, rel=1e-6)
    msg = next(x["message"] for x in doc["notices"] if x["code"] == "EMIR-I430")
    assert "on the feed node (inert" in msg
    assert all(x["i_rms_A"] < 1e-9 for x in em["decaps"]["top"] if x["name"] == "MD")


def test_the_default_basis_is_one_constant():
    """The maintainer may move the default basis: `emir/em_basis.py`'s
    DEFAULT_BASIS is the one place. With it switched, every text that names
    the default follows (the options' help, the schemas, the MCP gate, the
    registry), and no source file names worst as the default by hand."""
    import re
    import subprocess
    import sys
    code = (
        "import json, emir.em_basis as eb\n"
        "eb.DEFAULT_BASIS = 'decap-aware'\n"
        "from emir import decap, errors, mcp_tools, plan, schemas\n"
        "from emir.dspf import build_parser\n"
        "import argparse\n"
        "def helps(p):\n"
        "    return [a.help or '' for a in p._actions]\n"
        "ap = argparse.ArgumentParser()\n"
        "import emir.plan_decap as pd\n"
        "pd.add_arguments(ap)\n"
        "out = helps(build_parser()) + helps(ap) + [json.dumps(schemas.SCHEMAS),\n"
        "       ' '.join(mcp_tools.GATE_WHEN['run']), errors.lookup('EMIR-W437').meaning,\n"
        "       decap.requested(argparse.Namespace(em_basis=None))[0]]\n"
        "print(json.dumps(out))\n")
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       cwd=os.path.join(os.path.dirname(__file__), os.pardir))
    assert p.returncode == 0, p.stderr[-2000:]
    texts = json.loads(p.stdout)
    assert texts[-1] == "decap-aware"
    joined = " ".join(texts)
    for want in ("default decap-aware", "the default (decap-aware)",
                 "the default is decap-aware", "decap-aware, the default"):
        assert want in joined, want
    hand = re.compile(r"default[ :(,]*(basis[ ,(]*)?`?worst|worst`?[ ,(]*the default")
    root = os.path.join(os.path.dirname(__file__), os.pardir, "emir")
    for name in sorted(os.listdir(root)):
        if name.endswith(".py") and name != "em_basis.py":
            with open(os.path.join(root, name), encoding="utf-8") as f:
                src = f.read()
            assert not hand.search(src), (name, hand.search(src).group(0))


def test_the_plan_texts_follow_a_switched_default_and_a_kept_basis(tmp_path):
    """Two plan texts assumed the default basis is worst: with the constant
    switched, the advice read "the verdict uses decap-aware (the default: per
    segment the larger of decap-free and decap-aware)". And after a run with
    --em-basis decap-free and no --dynamic, `rerun-with-dynamic` said it adds
    "the default basis (worst)" and cited EMIR-W428 (which that run did not
    raise) while its command keeps --em-basis decap-free."""
    import subprocess
    import sys
    c = loose_case(tmp_path)
    code = (
        "import json, sys, emir.em_basis as eb\n"
        "eb.DEFAULT_BASIS = 'decap-aware'\n"
        "from emir import cli\n"
        "sys.exit(cli.main(sys.argv[1:]))\n")
    p = subprocess.run([sys.executable, "-c", code, "next", "--dspf", c["dspf"], "--process",
                        c["process"], "--cc-sim", c["raw"], "--net", "VDD", "--period",
                        str(c["period"]), "--mode", "cc-grid", "--dynamic", "--format",
                        "json"], capture_output=True, text=True,
                       cwd=os.path.join(os.path.dirname(__file__), os.pardir))
    assert p.returncode == 0, p.stderr[-2000:]
    adv = " ".join(json.loads(p.stdout)["advice"])
    assert "the verdict uses decap-aware (the default: rms and peak from the dynamic " \
           "solve" in adv, adv
    assert "the larger of decap-free and decap-aware" not in adv
    s = str(tmp_path / "s.json")
    _, doc = run_doc(c["dspf"], "--process", c["process"], "--cc-sim", c["raw"], "--net",
                     "VDD", "--mode", "cc-grid", "--period", c["period"], "--em-basis",
                     "decap-free", "--summary-out", s)
    assert "EMIR-W428" not in {x["code"] for x in doc["notices"]}
    plan = json.loads(emir("next", "--run-summary", s, "--format", "json").stdout)
    st = next(x for x in plan["steps"] if x["id"] == "rerun-with-dynamic")
    assert "--em-basis decap-free" in st["command"]
    assert "stays on --em-basis decap-free" in st["why"], st["why"]
    assert "default basis" not in st["why"] and "EMIR-W428" not in st["why"]


def test_the_go_ahead_lists_name_every_verdict_gate():
    """The MCP server asks for the person's go-ahead on an `em_basis` other
    than the default and on `dynamic` with `ground`, with `dyn_steps` below
    2000 or without `period` (each can leave decap-aware EM not computed).
    The go-ahead lists an agent reads named none of them, or only the
    basis."""
    from emir.errors import lookup
    root = os.path.join(os.path.dirname(__file__), os.pardir)
    texts = {"EMIR-E590": lookup("EMIR-E590").meaning}
    for rel in ("agent/mcp/README.md", "docs/agents.md", "agent/playbooks/10-mcp.md",
                "SECURITY.md", "agent/CONTRACT.md"):
        with open(os.path.join(root, rel), encoding="utf-8") as f:
            texts[rel] = f.read()
    for where, text in texts.items():
        flat = " ".join(text.replace("`", "").split())
        for want in ("em_basis other than the default", "dynamic with ground",
                     "dyn_steps below 2000", "without period"):
            assert want in flat, (where, want)
