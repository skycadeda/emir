"""Stage-1 correctness fixes on the simulation side, for rcc: a result must
hold every branch current of the net (EMIR-E415 names what is missing),
`--feed` and `--ground` move the reference only, a pin reaching the net at
several connections needs no special handling (the branches measure how its
current divides), and unreadable results are explained.

The results are synthetic PSF ASCII files built from closed-form waveforms
(`_helpers`), so no simulator is needed.
"""
import json
import struct

import numpy as np
import pytest
from _helpers import (DSPF, PERIOD, PROC, SPLIT_DSPF, branch_waves, cc_result, emir,
                      rcc_result, time_base, two_net_branches, two_net_dspf, write)

from emir import waves
from emir.check import check_inputs
from emir.dspf import build_parser, parse, result_holds, run
from emir.validate import _psf_tran


def _run(dspf, *extra):
    argv = [str(x) for x in (dspf, "--process", PROC, "--period", str(PERIOD), *extra)]
    a = build_parser().parse_args(argv)
    return run(a)


def _levels(rep, area, level):
    return [f for f in rep.findings if f.area == area and f.level == level]


def _branch(r, name):
    return [x["name"] for x in r["network"].res].index(name)


# ------------------------------------------------------------ what a result holds


def test_rcc_reads_every_branch_current(tmp_path):
    rcc = rcc_result(tmp_path)
    rep = check_inputs(DSPF, process=PROC, nets=["VDD"], cc_sim=rcc, period=PERIOD)
    assert rep.passed, rep.table()
    r = _run(DSPF, "--net", "VDD", "--cc-sim", rcc)["VDD"]
    assert r["mode"] == "rcc" and set(r["Ie"]) >= {"avg", "rms", "peak"}


def test_a_result_of_pin_currents_fails_on_the_branches(tmp_path):
    """A result that saved the instance pins and no branch current cannot be
    screened: the check FAILs (EMIR-E415) with the save lines to add."""
    rep = check_inputs(DSPF, process=PROC, nets=["VDD"], cc_sim=cc_result(tmp_path),
                       period=PERIOD)
    fails = _levels(rep, "sim", "FAIL")
    assert [f.code for f in fails] == ["EMIR-E415"]
    assert "none of its 16 branch currents" in fails[0].message
    assert "save Rm1:1" in fails[0].fix


def test_what_a_result_holds_per_net(tmp_path):
    """One result, three nets: every VDD branch current, none of VSS or OUT.
    A net the result cannot serve is a FAIL naming what is missing."""
    d = two_net_dspf(tmp_path)
    t = time_base()
    res = _psf_tran(str(tmp_path / "vdd.tran"), t, branch_waves(t))
    w = waves.read(res)
    dd = parse(d)
    assert result_holds(dd, "VDD", w) == {"branches": [16, 16], "time_domain": True,
                                          "rcc": True}
    assert not result_holds(dd, "VSS", w)["rcc"] and not result_holds(dd, "OUT", w)["rcc"]
    rep = check_inputs(d, process=PROC, nets=None, cc_sim=res, period=PERIOD)
    fails = _levels(rep, "sim", "FAIL")
    assert {f.code for f in fails} == {"EMIR-E415"}
    assert sorted(f.ctx.get("net") for f in fails) == ["OUT", "VSS"]
    assert all("save " in f.fix for f in fails)
    assert _run(d, "--net", "VDD", "--cc-sim", res)["VDD"]["mode"] == "rcc"


# ------------------------------------------------------------ --feed, --ground


def test_feed_and_ground_move_the_reference_only(tmp_path):
    """rcc measures the branch currents: where the potentials are referred
    to changes none of them, nor the IR spread."""
    rcc = rcc_result(tmp_path)
    a = _run(DSPF, "--net", "VDD", "--cc-sim", rcc)["VDD"]
    for extra in (["--feed", "VDD:6"], ["--ground", "VDD:12"]):
        b = _run(DSPF, "--net", "VDD", "--cc-sim", rcc, *extra)["VDD"]
        assert np.allclose(a["Ie"]["avg"], b["Ie"]["avg"], rtol=1e-12, atol=1e-18)
        assert abs(a["ir_drop"] - b["ir_drop"]) < 1e-12
    assert abs(a["Ie"]["avg"][_branch(a, "Rm1")] - 4e-3) < 1e-9


def test_dc_branch_currents_do_not_depend_on_the_reference():
    from _helpers import CUR
    a = _run(DSPF, "--net", "VDD", "--currents", CUR)["VDD"]
    b = _run(DSPF, "--net", "VDD", "--currents", CUR, "--ground", "VDD:12")["VDD"]
    assert np.allclose(a["Ie"]["avg"], b["Ie"]["avg"], rtol=1e-9, atol=1e-15)
    assert abs(a["ir_drop"] - b["ir_drop"]) < 1e-12        # a spread: invariant


# ------------------------------------------------------------ split pins


def test_split_pins_are_measured_by_rcc(tmp_path):
    """XB:VDDB reaches the net at two nodes: its saved current would have to
    be divided between them, but rcc reads every branch, so how it divides is
    measured (here 1.2 mA and 0.8 mA)."""
    d = write(tmp_path / "top.dspf", SPLIT_DSPF)
    t = time_base()
    one = np.ones_like(t)
    res = _psf_tran(str(tmp_path / "top.tran"), t,
                    {"Rt0:1": 3e-3 * one, "Rt1:1": 1e-3 * one, "Rt2:1": 1.2e-3 * one,
                     "Rt3:1": 0.8e-3 * one, "Rt4:1": 0.8e-3 * one})
    rep = check_inputs(d, process=PROC, nets=["VDD"], cc_sim=res, period=PERIOD)
    assert rep.passed, rep.table()
    r = _run(d, "--net", "VDD", "--cc-sim", res)["VDD"]
    assert abs(r["Ie"]["avg"][_branch(r, "Rt4")] - 0.8e-3) < 1e-12


def test_split_pins_do_not_matter_for_dc_currents(tmp_path):
    """--currents names nodes, not pins, so it can drive each connection."""
    d = write(tmp_path / "top.dspf", SPLIT_DSPF)
    cur = '{"VDD": 0.003, "XA:VDDA": -0.001, "XB:VDDB": -0.001, "XB:VDDB#2": -0.001}'
    assert check_inputs(d, process=PROC, nets=["VDD"], currents=cur).passed


# ------------------------------------------------------------ result files


def _binary_raw(path, names=("time", "i(vdd)"), npt=3):
    head = ["Title: test", "Date: today", "Plotname: Transient Analysis",
            "Flags: real", f"No. Variables: {len(names)}", f"No. Points: {npt}",
            "Variables:"]
    head += [f"\t{i}\t{n}\t{'time' if i == 0 else 'current'}" for i, n in enumerate(names)]
    head.append("Binary:")
    body = struct.pack(f"<{npt * len(names)}d", *range(npt * len(names)))
    with open(path, "wb") as f:
        f.write(("\n".join(head) + "\n").encode() + body)
    return str(path)


def test_binary_spice_raw_is_refused_with_the_fix(tmp_path):
    p = _binary_raw(tmp_path / "x.raw")
    with pytest.raises(ValueError, match="binary SPICE raw") as e:
        waves.read(p)
    assert "filetype=ascii" in str(e.value) and "-a" in str(e.value)
    # a long variable list pushes `Binary:` past the sniffed header: the
    # nutascii reader still refuses it (it used to die with an IndexError)
    p = _binary_raw(tmp_path / "y.raw", names=["time"] + [f"i(x{k})" for k in range(60)])
    with pytest.raises(ValueError, match="binary SPICE raw"):
        waves.read(p)
    rep = check_inputs(DSPF, process=PROC, nets=["VDD"], cc_sim=p)
    f = _levels(rep, "sim", "FAIL")
    assert f and "binary SPICE raw" in f[0].message and "Xyce" in f[0].fix


def test_truncated_nutascii_is_explained(tmp_path):
    text = ("Title: t\nPlotname: Transient Analysis\nFlags: real\nNo. Variables: 2\n"
            "No. Points: 3\nVariables:\n\t0\ttime\ttime\n\t1\ti(v1)\tcurrent\n"
            "Values:\n0\t0.0\n\t1.0\n1\t1e-9\n")
    p = write(tmp_path / "t.raw", text)
    with pytest.raises(ValueError, match="truncated"):
        waves.read(p)


def test_match_suffix_rule_through_the_index(tmp_path):
    t = np.linspace(0, 1, 3)
    w = waves.Waves(t, {"XTOP.XI0.MP0:s": t, "XTOP.XI1.MP5:s": t, "XTOP.XI2.MP5:s": t,
                        "I(Rm1)": t})
    got = w.match(["MP0:s", "XI0.MP0:s", "MP5:s", "i(rm1)", "nope"])
    assert got == {"MP0:s": "XTOP.XI0.MP0:s", "XI0.MP0:s": "XTOP.XI0.MP0:s",
                   "i(rm1)": "I(Rm1)"}                     # MP5:s is ambiguous


def test_run_reads_the_result_once_for_every_net(tmp_path, monkeypatch):
    d = two_net_dspf(tmp_path)
    t = time_base()
    res = _psf_tran(str(tmp_path / "all.tran"), t, two_net_branches(t))
    calls = []
    real = waves.read
    monkeypatch.setattr(waves, "read", lambda *a, **k: calls.append(a) or real(*a, **k))
    argv = [d, "--process", PROC, "--period", str(PERIOD), "--net", "all", "--cc-sim", res,
            "--no-check", "--json", str(tmp_path / "s.json")]
    solved = run(build_parser().parse_args(argv))
    assert sorted(solved) == ["OUT", "VDD", "VSS"]
    assert [c[0] for c in calls] == [res]
    modes = {n["net"]: n["mode"] for n in json.load(open(tmp_path / "s.json"))["nets"]}
    assert modes == {"OUT": "rcc", "VDD": "rcc", "VSS": "rcc"}
    vss = solved["VSS"]
    assert abs(vss["Ie"]["avg"][_branch(vss, "Rs1")] - 3e-3) < 1e-9


def test_a_run_on_a_result_without_the_branches_stops_with_the_code(tmp_path):
    p = emir("run", DSPF, "--net", "VDD", "--process", PROC, "--cc-sim",
             cc_result(tmp_path), "--format", "json")
    assert p.returncode == 2, p.stdout + p.stderr
    d = json.loads(p.stdout)
    assert d["code"] == "EMIR-E501" and d["details"]["causes"] == ["EMIR-E415"]
