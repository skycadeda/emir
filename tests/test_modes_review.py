"""The self-consistent loop and the mode surface: regression tests, one per case.

* the loop's supplies are relative to the testbench's own rail: pass 0 is the
  testbench's open loop whatever drives the rail, and --vdd is checked
  against the testbench's source (EMIR-E523), or read from it (EMIR-I521);
* a loop that does not settle reports pass 0, never a pass in between
  (EMIR-W407), and a strongly coupled loop settles through its companion
  update instead of oscillating;
* a Spectre netlist without the split is refused (EMIR-E524), before Spectre
  runs and after a pass that moved no current; the Spectre loop writes into
  emir_out/, never beside the person's netlist (EMIR-E526);
* `emir next`: the routes keep the testbench and --vdd, the loop plans
  verify-sim, the choices carry MCP tool calls; --cc-sim and --currents are
  refused with --self-consistent (EMIR-E525);
* the texts: what the loop does to switching CMOS, the ideal-other-rail
  condition, the usage lines name --mode, the layout testbench is cc-grid.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import sys

import numpy as np
import pytest
from _helpers import PROC, ROOT, emir, write

from emir import selfconsist, site
from emir.errors import EmirError

EX12 = os.path.join(ROOT, "examples", "12_self_consistent")
HAVE = {s: site.tool(s)[0] is not None for s in ("xyce", "ngspice")}
OPEN = next((s for s in ("xyce", "ngspice") if HAVE[s]), None)


def jdoc(p, rc=0):
    assert p.returncode == rc, (p.returncode, p.stdout[-2000:], p.stderr[-2000:])
    return json.loads(p.stdout)


def ex12(tmp_path, loads_ohm=None, gnd=False, tb=None):
    """Example 12 copied into tmp_path; `loads_ohm` changes its loads,
    `gnd` mirrors it onto VSS (the loads' n pins on the extracted rail, their
    p pins on the testbench's 1.2 V), `tb` replaces the CC testbench."""
    for f in ("biasrail.dspf", "cc.sp", "loads.sp", "tb_cc.cir", "tb_rcc.cir"):
        shutil.copy(os.path.join(EX12, f), tmp_path / f)
    if loads_ohm:
        t = (tmp_path / "loads.sp").read_text()
        (tmp_path / "loads.sp").write_text(re.sub(r"R1 p n 600", f"R1 p n {loads_ohm:g}", t))
    if gnd:
        d = (tmp_path / "biasrail.dspf").read_text()
        d = d.replace("VDD", "VSS").replace(":p", ":n").replace(" p I ", " n I ")
        d = re.sub(r"^(XL\d) XL(\d):n VSS rload", r"\1 VDD XL\2:n rload", d, flags=re.M)
        d = d.replace(".SUBCKT biasrail VSS VSS", ".SUBCKT biasrail VDD VSS")
        (tmp_path / "biasrail.dspf").write_text(d)
    if tb is not None:
        (tmp_path / "tb_cc.cir").write_text(tb)
    return tmp_path


def sc_args(ex, net="VDD", sim="xyce", *extra):
    return ["run", ex / "biasrail.dspf", "--process", PROC, "--net", net, "--mode",
            "cc-grid", "--self-consistent", "--sim", sim, "--netlist", ex / "tb_cc.cir",
            "--period", "1e-9", *extra, "--format", "json"]


# ------------------------------------------------------------ --vdd and the rail


def test_the_testbench_rail_is_read_where_it_can_be(tmp_path):
    from emir.dspf import parse
    ex = ex12(tmp_path)
    d = parse(str(ex / "biasrail.dspf"))
    r = selfconsist.testbench_rail(str(ex / "tb_cc.cir"), d, "VDD")
    assert r["volts"] == pytest.approx(1.2) and r["source"] == "VDD"
    # the block's VSS port is on the testbench's ground
    assert selfconsist.testbench_rail(str(ex / "tb_cc.cir"), d, "VSS")["volts"] == 0.0
    cases = {"VDD vdd 0 1.5": 1.5, "VDD 0 vdd DC 1.2": -1.2, "VDD vdd 0 dc=0.9": 0.9,
             "VDD vdd 0 PWL(0 0 1n 1.2)": None, "VDD vdd 0 {vsup}": None,
             "VDD vs 0 DC 1.2\nRPKG vs vdd 0.5": None}
    for line, want in cases.items():
        tb = (ex / "tb_cc.cir").read_text().replace("VDD vdd 0 DC 1.2", line)
        p = write(tmp_path / "t.cir", tb)
        got = selfconsist.testbench_rail(p, d, "VDD")
        assert got["volts"] == (pytest.approx(want) if want is not None else None), line
        assert got["why"]


@pytest.mark.parametrize("net,vdd,ok", [("VSS", "1.2", False), ("VDD", "1.5", False),
                                        ("VDD", "1.2", True), ("VSS", "0", True)])
def test_a_vdd_the_testbench_contradicts_is_refused_before_any_simulation(tmp_path, net,
                                                                           vdd, ok):
    """A ground net run with the supply's voltage came out CLEAN at 0 mV, and
    --vdd 1.5 on a 1.2 V testbench settled 25 % high, with no warning."""
    ex = ex12(tmp_path, gnd=(net == "VSS"))
    p = emir("check", ex / "biasrail.dspf", "--process", PROC, "--net", net, "--mode",
             "cc-grid", "--self-consistent", "--sim", "xyce", "--netlist", ex / "tb_cc.cir",
             "--vdd", vdd, "--format", "json")
    d = json.loads(p.stdout)
    codes = {f["code"]: f["level"] for f in d["findings"]}
    if ok:
        assert codes.get("EMIR-I521") == "OK" and "EMIR-E523" not in codes
    else:
        assert p.returncode == 2 and codes.get("EMIR-E523") == "FAIL"
        # the run refuses too, and nothing was simulated
        p = emir(*sc_args(ex, net, "xyce", "--vdd", vdd), cwd=ex)
        doc = jdoc(p, 2)
        assert doc["code"] == "EMIR-E523" and not (ex / "emir_out").exists()


def test_without_vdd_the_testbench_says_it(tmp_path):
    ex = ex12(tmp_path)
    p = emir("check", ex / "biasrail.dspf", "--process", PROC, "--net", "VDD", "--mode",
             "cc-grid", "--self-consistent", "--sim", "ngspice", "--netlist",
             ex / "tb_cc.cir", "--format", "json")
    f = {x["code"]: x for x in json.loads(p.stdout)["findings"]}
    assert "EMIR-E503" not in f and "1.2 V, read from the testbench" in f["EMIR-I521"]["message"]
    # Spectre's netlist is not read: --vdd stays required
    write(ex / "cc.scs", "// spectre\n")
    p = emir("check", ex / "biasrail.dspf", "--process", PROC, "--net", "VDD", "--mode",
             "cc-grid", "--self-consistent", "--netlist", ex / "cc.scs", "--format", "json")
    assert "EMIR-E503" in [x["code"] for x in json.loads(p.stdout)["findings"]
                           if x["level"] == "FAIL"]


def test_the_split_is_relative_to_the_rail(tmp_path):
    """Each pin's source sits between the rail node and the pin's own node, at
    the pin's droop (0 V on pass 0): the sources are the per-pass include,
    which the block's subcircuit includes; the deck comment names the real
    deck."""
    from emir.dspf import parse
    ex = ex12(tmp_path)
    out = tmp_path / "sc"
    sp = selfconsist.split_testbench("xyce", str(ex / "tb_cc.cir"),
                                     parse(str(ex / "biasrail.dspf")), "VDD", None, str(out),
                                     str(out / "droop.inc"))
    block = (out / "cc.split_xyce.sp").read_text()
    assert ".include droop.inc" in block and not any(
        ln.startswith("VEMIRSC") for ln in block.splitlines())
    src = [ln.split() for ln in (out / "droop.inc").read_text().splitlines()
           if ln.startswith("VEMIRSC")]
    assert len(src) == 4 and all(w[2].upper() == "VDD" and w[3:] == ["DC", "0"] for w in src)
    assert [s["pin"] for s in sp["sources"]] == [f"XL{k}:p" for k in range(4)]
    deck = open(sp["deck"]).read()
    assert "split.xyce.cir" in deck and "Xyce -a split.cir" not in deck
    # every terminal on the net moves: the rail source's current is saved for KCL
    assert sp["rails"]["VDD"]["name"] == "VDD" and "I(VDD)" in deck


@pytest.mark.skipif(OPEN is None, reason="neither Xyce nor ngspice is installed")
def test_pass_zero_is_the_testbench_open_loop_even_through_a_package_resistance(tmp_path):
    """The rail is not an ideal source at --vdd here: pass 0 still equals the
    open loop of the testbench as it is (its source and series resistance)."""
    tb = (open(os.path.join(EX12, "tb_cc.cir")).read()
          .replace("VDD vdd 0 DC 1.2", "VDD vs 0 DC 1.2\nRPKG vs vdd 0.5"))
    ex = ex12(tmp_path, tb=tb)
    s = jdoc(emir("simulate", ex / "tb_cc.cir", "--sim", OPEN, "--dspf", ex / "biasrail.dspf",
                  "--mode", "cc-grid", "--probe", "--out", ex / "ol" / "cc.raw", "--format",
                  "json", cwd=ex))
    ol = jdoc(emir("run", ex / "biasrail.dspf", "--process", PROC, "--cc-sim", s["result"],
                   "--mode", "cc-grid", "--period", "1e-9", "--net", "VDD", "--format", "json",
                   cwd=ex))["nets"][0]
    sc = jdoc(emir(*sc_args(ex, "VDD", OPEN, "--vdd", "1.2", "--max-passes", "1"), cwd=ex))
    p0 = sc["nets"][0]["self_consistency"]["passes"][0]
    assert p0["ir_drop_mV"] == pytest.approx(ol["ir_drop_mV"], rel=1e-6)
    assert ol["ir_drop_mV"] < 108.0           # the package resistance lowered the loads' current


@pytest.mark.skipif(OPEN is None, reason="neither Xyce nor ngspice is installed")
def test_a_ground_net_without_vdd_settles_on_the_closed_loop(tmp_path):
    ex = ex12(tmp_path, gnd=True)
    d = jdoc(emir(*sc_args(ex, "VSS", OPEN), cwd=ex))
    n = d["nets"][0]
    assert n["self_consistency"]["vdd"] == 0.0 and n["self_consistency"]["converged"]
    assert n["ir_drop_mV"] == pytest.approx(100.050, rel=1e-3)     # README, by hand
    assert "EMIR-I521" in [x["code"] for x in d["notices"]]


# ------------------------------------------------------------ settling


def _stub(tmp_path, r_load, gain):
    """The sample rail with loads drawing 1.2/r + gain*v/r at their droop v
    (the test_modes stub): `gain` sets the loop gain."""
    from emir.validate import _psf_tran
    t = np.linspace(0, 1e-9, 21)
    raw = str(tmp_path / "s.tran")

    def sim(netlist, k, fed):
        def droop(p):
            return 0.0 if fed["t"] is None else np.interp(t, fed["t"], fed["v"][p])
        _psf_tran(raw, t, {p: np.full_like(t, 1.2 / r_load) + gain * droop(p) / r_load
                           for p in fed["v"]})
        return {"raw": raw, "seconds": 0.0}
    return sim


def _converge(tmp_path, **kw):
    from emir.dspf import parse
    from emir.engine import Process
    from emir.errors import collecting
    d = parse(os.path.join(ROOT, "emir", "data", "sample", "rail.dspf"))
    with collecting() as notes:
        cv = selfconsist.converge(None, str(tmp_path / "droop.scs"), d, "VDD",
                                  Process.load(PROC), "VDD", 1.2, period=1e-9,
                                  log=lambda *a: None, **kw)
    return cv, [n["code"] for n in notes]


def test_a_loop_stopped_at_the_pass_limit_reports_the_open_loop(tmp_path):
    """It reported its last pass, which on a slowly settling loop was 57 %
    below the closed loop; pass 0 (the open loop) is what it reports now."""
    cv, codes = _converge(tmp_path, max_passes=2, tol_mV=1e-9,
                          simulate=_stub(tmp_path, 1200.0, 1.0))
    assert "EMIR-W407" in codes and not cv["converged"]
    assert cv["reported_pass"] == 0
    assert cv["result"]["ir_drop"] * 1e3 == pytest.approx(cv["passes"][0]["ir_drop_mV"])


@pytest.mark.parametrize("gain", [16.0, 30.0])
def test_a_strongly_coupled_loop_settles_through_the_companion(tmp_path, gain):
    """Loop gains near 1 (the plain update shrinks the error by 10 % a pass)
    and above 1 (it grows): the companion update fits each load's admittance
    (gain / r) from the two passes before, and lands on the fixed point."""
    cv, codes = _converge(tmp_path, max_passes=8, tol_mV=1e-3, tol_rel=0.0,
                          simulate=_stub(tmp_path, 1200.0, gain))
    assert cv["converged"] and not cv["diverged"], codes
    assert "EMIR-W406" not in codes and len(cv["passes"]) <= 4
    assert [p["fed_by"] for p in cv["passes"][:3]] == ["open loop", "plain", "companion"]
    g = cv["passes"][2]["companion"]["g_mS"][0]
    assert g == pytest.approx(gain / 1200.0 * 1e3, rel=1e-6)
    # the fixed point: the droop the last pass's currents produce is the one fed
    assert cv["passes"][-1]["residual_mV"] < 1e-3


def test_positive_feedback_still_diverges(tmp_path):
    cv, codes = _converge(tmp_path, max_passes=6, tol_mV=1e-6,
                          simulate=_stub(tmp_path, 30.0, -3.0))
    assert cv["diverged"] and cv["reported_pass"] == 0 and "EMIR-W406" in codes


def _rcc(ex):
    s = jdoc(emir("simulate", ex / "tb_rcc.cir", "--sim", OPEN, "--dspf", ex / "biasrail.dspf",
                  "--mode", "rcc", "--out", ex / "rcc" / "rcc.raw", "--format", "json", cwd=ex))
    return jdoc(emir("run", ex / "biasrail.dspf", "--process", PROC, "--cc-sim", s["result"],
                     "--mode", "rcc", "--period", "1e-9", "--net", "VDD", "--format", "json",
                     cwd=ex))["nets"][0]


@pytest.mark.skipif(OPEN is None, reason="neither Xyce nor ngspice is installed")
def test_a_high_gain_load_settles_on_rcc(tmp_path):
    """Example 12 with 120 ohm loads (a 450 mV droop on the 1.2 V rail, 540 mV
    open loop): the plain update and then the companion land on rcc."""
    ex = ex12(tmp_path, loads_ohm=120)
    rcc = _rcc(ex)
    n = jdoc(emir(*sc_args(ex, "VDD", OPEN, "--droop-file", ex / "a" / "droop.inc"),
                  cwd=ex))["nets"][0]
    assert n["self_consistency"]["converged"], n["self_consistency"]
    assert n["ir_drop_mV"] == pytest.approx(rcc["ir_drop_mV"], rel=1e-3)
    assert n["max_ratio"]["all"] == pytest.approx(rcc["max_ratio"]["all"], rel=1e-3)


@pytest.mark.skipif(OPEN is None, reason="neither Xyce nor ngspice is installed")
def test_a_droop_above_half_the_supply_is_never_simulated(tmp_path):
    """Example 12 with 50 ohm loads: the open loop's droop (1296 mV) is above
    half the 1.2 V supply. The first update is fed at 15 % of the supply (the
    open loop's droop scaled down), and the guard stops the loop before any
    droop above half the supply is simulated; it reports pass 0 (EMIR-W406),
    which reads above rcc: conservative."""
    ex = ex12(tmp_path, loads_ohm=50)
    rcc = _rcc(ex)
    d = jdoc(emir(*sc_args(ex, "VDD", OPEN, "--droop-file", ex / "a" / "droop.inc"), cwd=ex))
    sc = d["nets"][0]["self_consistency"]
    assert sc["diverged"] and sc["reported_pass"] == 0
    assert sc["passes"][0]["max_droop_mV"] > 600
    assert all(p["simulated_droop_mV"] <= 600 for p in sc["passes"])
    assert sc["n_passes"] == 2 and sc["passes"][1]["fed_by"] == "plain (scaled)"
    assert sc["passes"][1]["simulated_droop_mV"] == pytest.approx(0.15 * 1200, rel=1e-6)
    assert sc["stopped"] == "droop above half the supply"
    assert "EMIR-W406" in [x["code"] for x in d["notices"]]
    assert d["nets"][0]["ir_drop_mV"] >= rcc["ir_drop_mV"]


# ------------------------------------------------------------ Spectre


def _fake_spectre(tmp_path, result):
    """A stand-in `spectre` that ignores the netlist and copies one result:
    what Spectre gives on a netlist that does not read the droop file."""
    exe = tmp_path / "spectre"
    exe.write_text(f"#!{sys.executable}\nimport shutil, sys\na = sys.argv\n"
                   f"out = a[a.index('-raw') + 1]\nlog = a[a.index('=log') + 1]\n"
                   f"shutil.copy({str(result)!r}, out)\nopen(log, 'w').write('ok')\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return exe


def _pin_result(tmp_path):
    from emir.validate import _psf_tran
    t = np.linspace(0, 1e-9, 21)
    return _psf_tran(str(tmp_path / "fixed.tran"), t,
                     {f"XL{k}:p": np.full_like(t, 2e-3) for k in range(4)})


def test_a_spectre_netlist_without_the_split_is_refused(tmp_path):
    ex = ex12(tmp_path)
    write(ex / "cc.scs", "// a CC netlist without the split\nsimulator lang=spectre\n")
    base = ["run", ex / "biasrail.dspf", "--process", PROC, "--net", "VDD", "--mode",
            "cc-grid", "--self-consistent", "--netlist", ex / "cc.scs", "--vdd", "1.2",
            "--period", "1e-9", "--format", "json"]
    for extra in ([], ["--run", "--spectre", _fake_spectre(tmp_path, ex / "x")]):
        d = jdoc(emir(*base, *extra, cwd=ex), 2)
        assert "EMIR-E524" in (d.get("details") or {}).get("causes", [d["code"]])
    # the input check says it, before anything is planned or run
    p = emir("check", *base[1:], cwd=ex)
    f = [x for x in json.loads(p.stdout)["findings"] if x["code"] == "EMIR-E524"]
    assert p.returncode == 2 and f and "PWL file(s)" in f[0]["message"]
    # the stub's executable lines alone (no pin reconnected): still refused
    p = emir("run", ex / "biasrail.dspf", "--net", "VDD", "--split-stub", cwd=ex)
    lines = [ln for ln in p.stdout.splitlines() if ln and not ln.startswith("//")]
    write(ex / "cc.scs", "simulator lang=spectre\n" + "\n".join(lines) + "\n")
    d = jdoc(emir(*base, "--no-check", cwd=ex), 2)
    assert d["code"] == "EMIR-E524" and "fewer than two statements" in d["message"]


def test_a_spectre_loop_whose_currents_never_move_is_refused(tmp_path):
    """The split looks right, but Spectre's currents do not follow the droop
    (here: a stand-in that returns one fixed result): it 'converged' at pass
    1 on the open loop and was labelled self-consistent."""
    from _helpers import spectre_split_netlist
    ex = ex12(tmp_path)
    droop = ex / "emir_out" / "selfconsist" / "VDD" / "droop.scs"
    spectre_split_netlist(ex / "cc.scs", ex / "biasrail.dspf", "VDD", droop)
    exe = _fake_spectre(tmp_path, _pin_result(tmp_path))
    d = jdoc(emir("run", ex / "biasrail.dspf", "--process", PROC, "--net", "VDD", "--mode",
                  "cc-grid", "--self-consistent", "--netlist", ex / "cc.scs", "--vdd", "1.2",
                  "--period", "1e-9", "--run", "--spectre", exe, "--format", "json", cwd=ex), 2)
    assert d["code"] == "EMIR-E524" and "no pin current of VDD moved" in d["message"]
    # every pass's result is kept in emir_out/, nothing beside the netlist
    assert {"droop.scs", "pass0.raw", "pass0.raw.log", "pass0.raw.emir"} <= set(
        os.listdir(droop.parent))
    assert not (ex / "cc.raw").exists() and not (ex / "droop.scs").exists()


def test_a_pass_result_emir_did_not_write_is_never_overwritten(tmp_path):
    """A pass0.raw someone left at the loop's place: the loop refuses
    (EMIR-E526) before Spectre runs, and the file is untouched; a result emir
    wrote (its stamp beside it) is replaced on the next run."""
    from _helpers import spectre_split_netlist
    ex = ex12(tmp_path)
    droop = ex / "emir_out" / "selfconsist" / "VDD" / "droop.scs"
    spectre_split_netlist(ex / "cc.scs", ex / "biasrail.dspf", "VDD", droop)
    theirs = droop.parent / "pass0.raw"
    theirs.parent.mkdir(parents=True, exist_ok=True)
    theirs.write_text("someone's result\n")
    exe = _fake_spectre(tmp_path, _pin_result(tmp_path))
    run = ("run", ex / "biasrail.dspf", "--process", PROC, "--net", "VDD", "--mode",
           "cc-grid", "--self-consistent", "--netlist", ex / "cc.scs", "--vdd", "1.2",
           "--period", "1e-9", "--run", "--spectre", exe, "--format", "json")
    d = jdoc(emir(*run, cwd=ex), 2)
    assert d["code"] == "EMIR-E526" and "pass0.raw" in d["message"]
    assert theirs.read_text() == "someone's result\n"
    assert not (droop.parent / "pass0.raw.emir").exists()
    # emir's own result from an earlier run: replaced (the loop then stops on
    # the stand-in's fixed currents, EMIR-E524, as above)
    theirs.unlink()
    d = jdoc(emir(*run, cwd=ex), 2)
    assert d["code"] == "EMIR-E524" and (droop.parent / "pass0.raw.emir").is_file()
    d = jdoc(emir(*run, cwd=ex), 2)
    assert d["code"] == "EMIR-E524"


def test_the_spice_loop_never_removes_a_result_it_did_not_write(tmp_path):
    from emir.dspf import parse
    out = tmp_path / "sc"
    out.mkdir()
    (out / "pass0.raw").write_text("mine\n")
    drv = selfconsist.SpiceDriver("xyce", os.path.join(EX12, "tb_cc.cir"),
                                  parse(os.path.join(EX12, "biasrail.dspf")), "VDD", 1.2,
                                  str(out))
    drv.exe, drv.version = "xyce-not-run", "7.10"
    with pytest.raises(EmirError) as e:
        drv.run(0, {})
    assert e.value.code == "EMIR-E526" and (out / "pass0.raw").read_text() == "mine\n"


def test_the_spectre_plan_writes_only_in_emir_out(tmp_path):
    from _helpers import spectre_split_netlist
    ex = ex12(tmp_path)
    droop = ex / "emir_out" / "selfconsist" / "VDD" / "droop.scs"
    spectre_split_netlist(ex / "cc.scs", ex / "biasrail.dspf", "VDD", droop)
    d = jdoc(emir("run", ex / "biasrail.dspf", "--process", PROC, "--net", "VDD", "--mode",
                  "cc-grid", "--self-consistent", "--netlist", ex / "cc.scs", "--vdd", "1.2",
                  "--period", "1e-9", "--format", "json", cwd=ex))
    assert d["ran"] is False and d["result"] == str(droop.parent / "pass0.raw")
    assert droop.is_file() and "vd_XL0_p.pwl" in droop.read_text()
    assert (droop.parent / "vd_XL0_p.pwl").read_text().split() == ["0", "0", "1", "0"]
    assert sorted(os.listdir(ex)) == sorted(["biasrail.dspf", "cc.sp", "loads.sp", "tb_cc.cir",
                                             "tb_rcc.cir", "cc.scs", "emir_out"])
    step = d["next_steps"][0]
    assert step["needs_human"] is True and step["licensed_tool"] == "spectre"
    # a file there that emir did not write is never overwritten
    droop.write_text("parameters mine=1\n")
    d = jdoc(emir("run", ex / "biasrail.dspf", "--process", PROC, "--net", "VDD", "--mode",
                  "cc-grid", "--self-consistent", "--netlist", ex / "cc.scs", "--vdd", "1.2",
                  "--period", "1e-9", "--format", "json", cwd=ex), 2)
    assert d["code"] == "EMIR-E526" and droop.read_text() == "parameters mine=1\n"


def test_the_split_stub_moves_the_bulk_too():
    from emir.dspf import parse
    text = selfconsist.rail_split_fragment(parse(os.path.join(EX12, "biasrail.dspf")), "VDD")
    assert re.search(r'\(VT_XL0_p VDD\) vsource type=pwl file="[^"]*vd_XL0_p\.pwl"', text)
    assert "bulk" in text and "emir_out" in text


def test_the_licensed_plan_asks_first(tmp_path):
    ex = ex12(tmp_path)
    tb = write(ex / "tb.sp", (ex / "tb_cc.cir").read_text())
    d = jdoc(emir(*sc_args(ex, "VDD", "hspice", "--vdd", "1.2")[:11], "--netlist", tb,
                  "--period", "1e-9", "--format", "json", cwd=ex))
    step = d["next_steps"][0]
    assert step["needs_human"] is True and step["licensed_tool"] == "hspice"
    assert step["command"].endswith("--run --format json")


# ------------------------------------------------------------ options


@pytest.mark.parametrize("extra", [["--cc-sim", "RESULT"], ["--currents", '{"XL0:p": 0.002}']])
def test_inputs_the_loop_would_ignore_are_refused(tmp_path, extra):
    ex = ex12(tmp_path)
    extra = [str(_pin_result(tmp_path)) if x == "RESULT" else x for x in extra]
    d = jdoc(emir(*sc_args(ex, "VDD", "xyce", "--vdd", "1.2", *extra), cwd=ex), 2)
    assert d["code"] == "EMIR-E525"
    p = emir("check", ex / "biasrail.dspf", "--process", PROC, "--net", "VDD", "--mode",
             "cc-grid", "--self-consistent", "--sim", "xyce", "--netlist", ex / "tb_cc.cir",
             "--vdd", "1.2", *extra, "--format", "json")
    assert "EMIR-E525" in [f["code"] for f in json.loads(p.stdout)["findings"]]


# ------------------------------------------------------------ emir next


@pytest.mark.skipif(OPEN is None, reason="the open-simulator route needs Xyce or ngspice "
                    "(without one, --netlist plans Spectre)")
def test_every_route_keeps_the_testbench_and_the_supply(tmp_path):
    ex = ex12(tmp_path)
    d = jdoc(emir("next", "--dspf", ex / "biasrail.dspf", "--process", PROC, "--netlist",
                  ex / "tb_cc.cir", "--vdd", "1.2", "--format", "json", cwd=ex))
    step = next(s for s in d["steps"] if s["id"] == "choose-mode")
    for c in step["choices"]:
        assert "--netlist" in c["command"] and "--vdd 1.2" in c["command"], c["command"]
        argv = __import__("shlex").split(c["command"])[1:]
        nd = jdoc(emir(*argv, cwd=ex))
        what = " | ".join(m["what"] for m in nd["missing"])
        if c["mode"] == "cc-grid":
            # the CC testbench given is what both cc-grid routes simulate (rcc
            # needs the RC netlist: that one is rightly asked for)
            assert "testbench" not in what and "--vdd" not in what, (c["route"], what)


@pytest.mark.parametrize("sim,licensed", [("xyce", False), ("ngspice", False),
                                          ("hspice", True), ("spectre", True)])
def test_the_loop_plans_the_simulator_verification(tmp_path, sim, licensed):
    ex = ex12(tmp_path)
    d = jdoc(emir("next", "--dspf", ex / "biasrail.dspf", "--process", PROC, "--net", "VDD",
                  "--mode", "cc-grid", "--self-consistent", "--vdd", "1.2", "--netlist",
                  ex / "tb_cc.cir", "--sim", sim, "--format", "json", cwd=ex))
    ids = [s["id"] for s in d["steps"]]
    assert "verify-sim" in ids and ids.index("verify-sim") < ids.index("check") < ids.index("run")
    v = d["steps"][ids.index("verify-sim")]
    assert (v["licensed_tool"] == sim) == licensed and v["needs_human"] == licensed
    if sim == "spectre":
        assert ids.index("split-stub") < ids.index("check")
        assert "split-stub" in d["steps"][ids.index("run")]["after"]


def test_next_reads_the_supply_from_the_testbench(tmp_path):
    ex = ex12(tmp_path, gnd=True)
    d = jdoc(emir("next", "--dspf", ex / "biasrail.dspf", "--process", PROC, "--net", "VSS",
                  "--mode", "cc-grid", "--self-consistent", "--netlist", ex / "tb_cc.cir",
                  "--sim", "ngspice", "--format", "json", cwd=ex))
    run = next(s for s in d["steps"] if s["id"] == "run")
    assert "--vdd 0.0" in run["command"] and not d["missing"]


@pytest.mark.skipif(OPEN is None, reason="the open-simulator route needs Xyce or ngspice "
                    "(without one, --netlist plans Spectre)")
def test_the_choices_carry_their_mcp_tool_calls(tmp_path):
    pytest.importorskip("mcp")
    from emir import mcp_tools
    ex = ex12(tmp_path)
    rc, doc, _ = mcp_tools.call(mcp_tools.specs()["emir_next"],
                                {"dspf": str(ex / "biasrail.dspf"), "process": PROC,
                                 "netlist": str(ex / "tb_cc.cir")})
    step = next(s for s in doc["steps"] if s["id"] == "choose-mode")
    assert all(c["tool"]["name"] == "emir_next" and c["tool"]["arguments"]["mode"] == c["mode"]
               for c in step["choices"])
    assert any("choose-mode" in a for a in doc["advice"])


# ------------------------------------------------------------ the texts


def test_the_texts_say_what_the_loop_does_to_switching_cmos():
    """The loop feeds each pin its droop waveform (the average droop it fed
    before over-corrected switching CMOS); every text says what it fixes and
    what it leaves (the grid's own capacitance), with its measurement."""
    from emir import errors, modes, report
    from emir.plan_bigblock import DROOP_FRACTION  # noqa: F401  (the note's module)
    hint = errors.lookup("EMIR-W520").hint
    assert "droop waveform" in hint and "selfconsist_waveform" in hint
    assert "average droop" not in hint
    src = open(os.path.join(ROOT, "emir", "plan_bigblock.py")).read()
    assert "corrects the average" not in src and "droop waveform" in src
    sc = modes.routes()[2]["accuracy"]
    assert "own capacitance" in sc and "accurate rather than conservative" in sc
    assert "over-corrected" in sc and modes.SC_AVERAGE_CSV in sc
    text = report.self_consistency_text("VDD", {"converged": True, "vdd": 1.2, "tol_mV": 0.2,
                                                "passes": [{"pass": 0, "ir_drop_mV": 1.0}]})
    assert "droop waveform" in text and "own capacitance" in text
    assert "rails are ideal" in text
    doc = " ".join(open(os.path.join(ROOT, "emir", "selfconsist.py")).read().split())
    assert "corrects the average" not in doc and "contradict" in doc
    # nor any page that describes the loop (docs/chip-level.md said it)
    for d in ("docs", "agent", "examples"):
        for root, _dirs, files in os.walk(os.path.join(ROOT, d)):
            for f in files:
                if f.endswith(".md"):
                    p = os.path.join(root, f)
                    text = " ".join(open(p, encoding="utf-8").read().split())
                    assert " corrects the average" not in text, os.path.relpath(p, ROOT)


def test_lands_on_rcc_names_its_condition():
    """'Lands on RCC for resistive loads' holds when the other rail is ideal."""
    bad = []
    for rel in ("docs/method-modes.md", "docs/big-blocks.md", "blog/linkedin-article.md",
                "blog/linkedin-post.md", "examples/12_self_consistent/README.md",
                "examples/06_big_block/README.md", "docs/chip-level.md",
                "scripts/make_figures.py"):
        text = " ".join(open(os.path.join(ROOT, rel), encoding="utf-8").read().split())
        for m in re.finditer(r"lands on (?:the )?(?:RCC|rcc|the exact|the closed-loop)", text):
            window = text[max(0, m.start() - 200): m.end() + 300]
            if "ideal" not in window:
                bad.append(f"{rel}: ...{text[m.start() - 60: m.end() + 60]}...")
    assert not bad, "\n".join(bad)


def test_cited_test_files_exist():
    """A docstring or doc that points at a test file names one that exists
    (selfconsist.py pointed at tests/test_selfconsist.py)."""
    bad = []
    for d in ("emir", "docs"):
        for root, _dirs, files in os.walk(os.path.join(ROOT, d)):
            for f in files:
                if not f.endswith((".py", ".md")):
                    continue
                p = os.path.join(root, f)
                for m in re.finditer(r"tests/(test_\w+\.py)", open(p, encoding="utf-8").read()):
                    if not os.path.isfile(os.path.join(ROOT, "tests", m.group(1))):
                        bad.append(f"{os.path.relpath(p, ROOT)}: {m.group(0)}")
    assert not bad, bad


def test_the_usage_lines_name_the_mode():
    from emir import check, cli, opensim
    texts = {"cli": cli.__doc__, "check": check.__doc__, "opensim": opensim.__doc__,
             "bug_report": open(os.path.join(ROOT, ".github", "ISSUE_TEMPLATE",
                                             "bug_report.yml")).read(),
             "cadence": open(os.path.join(ROOT, "docs", "cadence.md")).read()}
    bad = []
    for name, text in texts.items():
        # an entry: an `emir <cmd>` line and the indented lines under it
        entries = re.split(r"\n(?=\s*(?:\w\[?\"?\s*-->\s*)?[\w\"\[]*\s*emir )", text)
        for e in entries:
            for m in re.finditer(r"emir (simulate|abstract|save-lines|run|check)\b([^\n`]*"
                                 r"(?:\n\s{8,}[^\n]*)*)", e):
                cmd, rest = m.group(1), m.group(2)
                if cmd in ("run", "check") and "--cc-sim" not in rest:
                    continue
                if cmd == "save-lines" and "--sim" not in rest and "--out" not in rest \
                        and "--net" not in rest:
                    continue
                if cmd in ("simulate", "abstract") and not rest.strip().startswith(("<", "/",
                                                                                    "R", "T")):
                    continue
                if "--mode" not in rest and "--abstract" not in rest:
                    bad.append(f"{name}: emir {cmd}{rest[:100]}")
    assert not bad, "\n".join(bad)
    from emir import errors  # noqa: F401
    fix = open(os.path.join(ROOT, "emir", "check.py")).read()
    assert "`emir simulate <testbench> --sim {sim}` and" not in fix


@pytest.mark.parametrize("theirs", ["split.xyce.cir", "cc.split_xyce.sp"])
def test_the_spice_loop_never_overwrites_a_split_file_it_did_not_write(tmp_path, theirs):
    """The split deck and the split copies of its includes are emir's files:
    one someone left in the loop's directory is refused (EMIR-E526) before
    anything is written there, and kept; emir's own are replaced on the next
    run."""
    from emir.dspf import parse
    d = parse(os.path.join(EX12, "biasrail.dspf"))
    tb = os.path.join(EX12, "tb_cc.cir")
    out = tmp_path / "sc"
    out.mkdir()
    (out / theirs).write_text("* my own file\n")
    with pytest.raises(EmirError) as e:
        selfconsist.SpiceDriver("xyce", tb, d, "VDD", 1.2, str(out),
                                droop_path=str(out / "droop.inc"))
    assert e.value.code == "EMIR-E526" and theirs in e.value.message
    assert (out / theirs).read_text() == "* my own file\n"
    assert sorted(os.listdir(out)) == [theirs]                  # nothing else written
    (out / theirs).unlink()
    for _ in range(2):                                         # emir's own: replaced
        drv = selfconsist.SpiceDriver("xyce", tb, d, "VDD", 1.2, str(out),
                                      droop_path=str(out / "droop.inc"))
    assert "* my own file" not in (out / theirs).read_text()
    assert os.path.isfile(drv.deck)


def test_the_spice_loop_never_overwrites_a_simulator_log_it_did_not_write(tmp_path):
    """The simulator writes its log beside the result (sc.raw.log) before the
    loop moves it to the pass's: one emir did not write is refused (EMIR-E526)
    before the simulator runs, and kept."""
    from emir.dspf import parse
    out = tmp_path / "sc"
    out.mkdir()
    drv = selfconsist.SpiceDriver("xyce", os.path.join(EX12, "tb_cc.cir"),
                                  parse(os.path.join(EX12, "biasrail.dspf")), "VDD", 1.2,
                                  str(out), droop_path=str(out / "droop.inc"))
    (out / "sc.raw.log").write_text("my sc log\n")
    drv.exe, drv.version = "xyce-not-run", "7.10"
    with pytest.raises(EmirError) as e:
        drv.run(0, {})
    assert e.value.code == "EMIR-E526" and "sc.raw.log" in e.value.message
    assert (out / "sc.raw.log").read_text() == "my sc log\n"
    # HSPICE writes <stem>.lis and <stem>.st0 beside its result as well
    assert {str(out / "sc.lis"), str(out / "sc.st0")} <= set(
        selfconsist.SpiceDriver.side_files(str(out / "sc.tr0"), "hspice"))
    hs = tmp_path / "hs"
    hs.mkdir()
    drv = selfconsist.SpiceDriver("hspice", os.path.join(EX12, "tb_cc.cir"),
                                  parse(os.path.join(EX12, "biasrail.dspf")), "VDD", 1.2,
                                  str(hs), droop_path=str(hs / "droop.sp"))
    (hs / "sc.lis").write_text("my listing\n")
    drv.exe, drv.version = "hspice-not-run", "2023.12"
    with pytest.raises(EmirError) as e:
        drv.run(0, {})
    assert e.value.code == "EMIR-E526" and "sc.lis" in e.value.message
    assert (hs / "sc.lis").read_text() == "my listing\n"
