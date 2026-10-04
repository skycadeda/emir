"""Stage-1 correctness fixes on the simulation side: the mode the person gives
(`--mode`, required with a simulation: no `auto`), `--feed`, split instance
pins, and unreadable results.

The results are synthetic PSF ASCII files built from closed-form waveforms
(`_helpers`), so no simulator is needed.
"""
import json
import struct

import numpy as np
import pytest
from _helpers import (DSPF, PERIOD, PROC, SPLIT_DSPF, branch_waves, cc_result,
                      device_waves, emir, rcc_result, time_base, two_net_dspf, write)

from emir import waves
from emir.check import check_inputs
from emir.dspf import build_parser, parse, result_holds, run
from emir.validate import _psf_tran


def _run(dspf, *extra):
    a = build_parser().parse_args([dspf, "--process", PROC, "--period", str(PERIOD), *extra])
    return run(a)


def _levels(rep, area, level):
    return [f for f in rep.findings if f.area == area and f.level == level]


def _branch(r, name):
    return [x["name"] for x in r["network"].res].index(name)


# ------------------------------------------------------------ --mode


def test_cc_grid_on_a_cc_result_of_a_small_net(tmp_path):
    """The 17-node sample net is small, but a C-only result holds pin
    currents only: cc-grid is the mode that reads it (the person's choice)."""
    cc = cc_result(tmp_path)
    rep = check_inputs(DSPF, process=PROC, nets=["VDD"], cc_sim=cc, period=PERIOD,
                       mode="cc-grid")
    assert rep.passed, rep.table()
    r = _run(DSPF, "--net", "VDD", "--cc-sim", cc, "--mode", "cc-grid")["VDD"]
    assert r["mode"] == "cc-grid" and r["mode_reason"] == "--mode cc-grid"


def test_rcc_reads_every_branch_current(tmp_path):
    rcc = rcc_result(tmp_path)
    rep = check_inputs(DSPF, process=PROC, nets=["VDD"], cc_sim=rcc, period=PERIOD,
                       mode="rcc")
    assert rep.passed, rep.table()
    assert _run(DSPF, "--net", "VDD", "--cc-sim", rcc, "--mode", "rcc")["VDD"]["mode"] == "rcc"


def test_a_simulated_run_without_a_mode_is_refused(tmp_path):
    """The mode is the person's choice: emir never picks one (EMIR-E520),
    the check FAILs it, and DC currents need none."""
    from emir.errors import EmirError
    cc = cc_result(tmp_path)
    rep = check_inputs(DSPF, process=PROC, nets=["VDD"], cc_sim=cc, period=PERIOD)
    assert not rep.passed and "EMIR-E520" in rep.codes("FAIL")
    with pytest.raises(EmirError) as e:
        _run(DSPF, "--net", "VDD", "--cc-sim", cc)
    assert e.value.code == "EMIR-E520"
    assert "emir next" in e.value.next_steps()[0]["command"]
    from _helpers import CUR
    assert check_inputs(DSPF, process=PROC, nets=["VDD"], currents=CUR).passed
    p = emir("run", DSPF, "--net", "VDD", "--process", PROC, "--cc-sim", cc,
             "--mode", "auto", "--format", "json")
    assert p.returncode == 2 and json.loads(p.stdout)["code"] == "EMIR-E520"


def test_rcc_and_cc_grid_agree_on_consistent_results(tmp_path):
    """Both routes are exact here (R-only grid, ideal loads), so they must
    give the same branch currents, ratios and IR drop."""
    r_cc = _run(DSPF, "--net", "VDD", "--cc-sim", cc_result(tmp_path), "--mode",
                "cc-grid")["VDD"]
    r_rc = _run(DSPF, "--net", "VDD", "--cc-sim", rcc_result(tmp_path), "--mode",
                "rcc")["VDD"]
    assert (r_cc["mode"], r_rc["mode"]) == ("cc-grid", "rcc")
    assert abs(r_cc["ir_drop"] - r_rc["ir_drop"]) < 1e-12
    for m in ("avg", "rms", "peak"):
        assert np.allclose(r_cc["Ie"][m], r_rc["Ie"][m], rtol=1e-9, atol=1e-15), m
    assert abs(r_cc["max_ratio"]["all"] - r_rc["max_ratio"]["all"]) < 1e-9


def test_what_a_result_holds_per_net(tmp_path):
    """One result, three nets: every VDD branch current (rcc can read it), the
    VSS pin currents (cc-grid can), nothing for OUT (neither). The person's
    one mode decides the run: a net it cannot serve is a FAIL naming what is
    missing."""
    d = two_net_dspf(tmp_path)
    t = time_base()
    sigs = branch_waves(t)
    sigs.update({"MN0:s": -1e-3 * np.ones_like(t), "MN1:s": -2e-3 * np.ones_like(t)})
    res = _psf_tran(str(tmp_path / "mixed.tran"), t, sigs)
    w = waves.read(res)
    dd = parse(d)
    assert result_holds(dd, "VDD", w)["can"] == {"rcc": True, "cc-grid": False}
    assert result_holds(dd, "VSS", w)["can"] == {"rcc": False, "cc-grid": True}
    assert result_holds(dd, "OUT", w)["can"] == {"rcc": False, "cc-grid": False}

    rep = check_inputs(d, process=PROC, nets=None, cc_sim=res, period=PERIOD, mode="rcc")
    fails = _levels(rep, "sim", "FAIL")
    assert {f.code for f in fails} == {"EMIR-E415"}
    assert sorted(f.ctx.get("net") for f in fails) == ["OUT", "VSS"]
    assert all("save " in f.fix for f in fails)

    assert _run(d, "--net", "VDD", "--cc-sim", res, "--mode", "rcc")["VDD"]["mode"] == "rcc"
    vss = _run(d, "--net", "VSS", "--cc-sim", res, "--mode", "cc-grid")["VSS"]
    assert vss["mode"] == "cc-grid"
    # 3 mA into VSS from the two sources, all of it through Rs1 to the port
    assert abs(vss["Ie"]["avg"][_branch(vss, "Rs1")] - 3e-3) < 1e-9


def test_cc_grid_on_an_rcc_result_fails_on_the_pins(tmp_path):
    rcc = rcc_result(tmp_path)
    rep = check_inputs(DSPF, process=PROC, nets=["VDD"], cc_sim=rcc, mode="cc-grid")
    assert not rep.passed                     # cc-grid needs pin currents
    assert any("instance-pin currents were saved" in f.message
               for f in _levels(rep, "sim", "FAIL"))


# ------------------------------------------------------------ --feed


def test_feed_moves_the_feed_tap_not_only_the_reference(tmp_path):
    """Fed at VDD:6, nothing flows between the DSPF port and VDD:3 (Rm1-3):
    the port is a dead end. The feed tap used to stay at the port, so Rm1
    carried the whole 4 mA average."""
    cc = cc_result(tmp_path)
    r = _run(DSPF, "--net", "VDD", "--cc-sim", cc, "--mode", "cc-grid", "--feed",
             "VDD:6")["VDD"]
    for m in ("avg", "rms", "peak"):
        assert r["Ie"][m][_branch(r, "Rm1")] < 1e-12, m
    # MP0 (at VDD:3) is now fed from VDD:6 through Rm6..Rm4: its own waveform
    i0 = 1e-3 * (1 + 0.8 * np.sin(2 * np.pi * time_base() / PERIOD))
    assert abs(r["Ie"]["peak"][_branch(r, "Rm4")] - np.abs(i0).max()) < 1e-9


def test_branch_currents_do_not_depend_on_the_reference(tmp_path):
    """The feed tap carries the summed waveform, so the injection balances at
    every instant and the reference node only shifts potentials."""
    cc = cc_result(tmp_path)
    a = _run(DSPF, "--net", "VDD", "--cc-sim", cc, "--mode", "cc-grid", "--feed",
             "VDD:6")["VDD"]
    b = _run(DSPF, "--net", "VDD", "--cc-sim", cc, "--mode", "cc-grid", "--feed", "VDD:6",
             "--ground", "VDD:12")["VDD"]
    for m in ("avg", "rms", "peak"):
        assert np.allclose(a["Ie"][m], b["Ie"][m], rtol=1e-9, atol=1e-15), m
    assert abs(a["ir_drop"] - b["ir_drop"]) < 1e-12        # a spread: invariant


def test_feed_at_the_port_is_unchanged(tmp_path):
    """The default (feed = port = reference) gives what it always gave."""
    cc = cc_result(tmp_path)
    a = _run(DSPF, "--net", "VDD", "--cc-sim", cc, "--mode", "cc-grid")["VDD"]
    b = _run(DSPF, "--net", "VDD", "--cc-sim", cc, "--mode", "cc-grid", "--feed",
             "VDD")["VDD"]
    for m in ("avg", "rms", "peak"):
        assert np.allclose(a["Ie"][m], b["Ie"][m], rtol=1e-12, atol=1e-18), m
    rm1 = _branch(a, "Rm1")
    assert abs(a["Ie"]["avg"][rm1] - 4e-3) < 1e-9


# ------------------------------------------------------------ split pins


def _split_result(tmp_path):
    t = time_base()
    return _psf_tran(str(tmp_path / "top.tran"), t,
                     {"XA:VDDA": 1e-3 * np.ones_like(t), "XB:VDDB": 2e-3 * np.ones_like(t)})


def test_split_instance_pin_is_a_check_fail(tmp_path):
    """XB:VDDB reaches the net at two nodes but the sim saves one current. The
    old ingest kept only the last record: the whole 2 mA on one connection."""
    d = write(tmp_path / "top.dspf", SPLIT_DSPF)
    dd = parse(d)
    assert sorted(t["node"] for t in dd.split_pins("VDD")["XB:VDDB"]) == \
        ["XB:VDDB", "XB:VDDB#2"]
    rep = check_inputs(d, process=PROC, nets=["VDD"], cc_sim=_split_result(tmp_path),
                       mode="cc-grid")
    fails = _levels(rep, "sim", "FAIL")
    assert fails, rep.table()
    msg = fails[0].message
    # both routes: the hierarchical one for a black-boxed block (`emir
    # abstract`, then `emir run --abstract`), rcc for a small net
    assert "XB:VDDB" in msg and "XB:VDDB#2" in msg and "--mode rcc" in msg
    assert "emir abstract" in msg and "--abstract" in msg and "--place" in msg
    # and the finding's command plans them (it runs as printed)
    assert fails[0].command(dspf=d, net="VDD") == f"emir next --dspf {d} --net VDD"


def test_split_instance_pin_stops_the_run_with_a_message(tmp_path):
    d = write(tmp_path / "top.dspf", SPLIT_DSPF)
    p = emir("run", d, "--net", "VDD", "--process", PROC, "--cc-sim",
             _split_result(tmp_path), "--mode", "cc-grid", "--no-check")
    assert p.returncode == 2, p.stdout + p.stderr
    assert "XB:VDDB" in p.stderr and "Traceback" not in p.stderr


def test_split_pins_do_not_matter_for_dc_currents(tmp_path):
    """--currents names nodes, not pins, so it can drive each connection."""
    d = write(tmp_path / "top.dspf", SPLIT_DSPF)
    cur = '{"VDD": 0.003, "XA:VDDA": -0.001, "XB:VDDB": -0.001, "XB:VDDB#2": -0.001}'
    assert check_inputs(d, process=PROC, nets=["VDD"], currents=cur).passed


def test_check_counts_each_saved_pin_once(tmp_path):
    """A pin with two records is one saved signal, not one found and one
    missing."""
    d = write(tmp_path / "top.dspf", SPLIT_DSPF)
    rep = check_inputs(d, process=PROC, nets=["VDD"], cc_sim=_split_result(tmp_path),
                       mode="cc-grid")
    assert not any("not saved" in f.message for f in rep.findings)


# ------------------------------------------------------------ taps out


def test_taps_out_creates_its_directory(tmp_path):
    out = tmp_path / "deep" / "er" / "taps.csv"
    _run(DSPF, "--net", "VDD", "--cc-sim", cc_result(tmp_path), "--mode", "cc-grid",
         "--taps-out", str(out))
    rows = out.read_text().splitlines()
    assert rows[0].startswith("name,x_um") and len(rows) == 1 + 4 + 1   # + feed


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
    rep = check_inputs(DSPF, process=PROC, nets=["VDD"], cc_sim=p, mode="cc-grid")
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
    sigs = device_waves(t)
    sigs.update({"MN0:s": -1e-3 * np.ones_like(t), "MN1:s": -2e-3 * np.ones_like(t),
                 "MN0:d": 1e-3 * np.ones_like(t), "MN1:d": -1e-3 * np.ones_like(t)})
    res = _psf_tran(str(tmp_path / "all.tran"), t, sigs)
    calls = []
    real = waves.read
    monkeypatch.setattr(waves, "read", lambda *a, **k: calls.append(a) or real(*a, **k))
    solved = _run(d, "--net", "all", "--cc-sim", res, "--mode", "cc-grid", "--no-check",
                  "--json", str(tmp_path / "s.json"))
    assert sorted(solved) == ["OUT", "VDD", "VSS"] and len(calls) == 1
    modes = {n["net"]: n["mode"] for n in json.load(open(tmp_path / "s.json"))["nets"]}
    assert modes == {"OUT": "cc-grid", "VDD": "cc-grid", "VSS": "cc-grid"}


@pytest.mark.parametrize("extra,feed", [
    ([], "VDD"),                                   # the port, as before
    (["--ground", "VDD:12"], "VDD"),               # was VDD:12: droop from the wrong node
    (["--feed", "VDD:6", "--ground", "VDD:12"], "VDD:6"),
])
def test_self_consistent_droop_is_measured_from_the_feed(tmp_path, monkeypatch, extra, feed):
    """The loop feeds back V relative to the node it grounds, which must be
    where the rail is nominal: the feed, not whatever --ground names."""
    import emir.ccsim
    import emir.selfconsist

    class Stop(Exception):
        pass
    seen = {}

    def fake_converge(netlist, droop, d, net, proc, fd, vdd, **k):
        seen["feed"] = fd
        raise Stop
    monkeypatch.setattr(emir.ccsim, "run_spectre", lambda *a, **k: {"raw": "unused"})
    monkeypatch.setattr(emir.selfconsist, "converge", fake_converge)
    # a Spectre CC netlist that holds the split (the loop refuses one without
    # it, EMIR-E524)
    from _helpers import spectre_split_netlist
    droop = str(tmp_path / "droop.scs")
    nl = spectre_split_netlist(tmp_path / "cc.scs", DSPF, "VDD", droop)
    a = build_parser().parse_args([DSPF, "--net", "VDD", "--process", PROC, "--netlist", nl,
                                   "--mode", "cc-grid", "--self-consistent", "--vdd", "1.0",
                                   "--droop-file", droop, "--run", "--no-check", *extra])
    with pytest.raises(Stop):
        run(a)
    assert seen["feed"] == feed
