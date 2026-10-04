"""The waveform self-consistent loop on the open simulators (Xyce, ngspice):
the constructs it writes and the loop on small synthetic meshes.

* PWL round trips: the per-pass include with inline PWL sources inside a
  subcircuit (Xyce, ngspice) and Xyce's `PWL FILE` of emir's two-column
  writer: the source equals the file at every corner and holds its last value
  after the last point;
* the split deck at zero droop (pass 0) against the unsplit testbench;
* KCL every pass; a terminal on the net that is no DSPF pin (EMIR-E540); the
  reltol warning (EMIR-W540); the cost of a pass (time points against the open
  loop's, PWL points per pin); VDD and VSS closed together.

Every case is a few seconds; one simulator runs at a time.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import numpy as np
import pytest
from _helpers import ROOT, emir

from emir import pwl, site

HAVE = {s: site.tool(s)[0] is not None for s in ("xyce", "ngspice")}
EX06 = os.path.join(ROOT, "examples", "06_big_block")
PROC06 = os.path.join(EX06, "process.yaml")


def jdoc(p, rc=0):
    assert p.returncode == rc, (p.returncode, p.stdout[-3000:], p.stderr[-3000:])
    return json.loads(p.stdout)


def _mesh(out, n, k, **kw):
    sys.path.insert(0, EX06)
    try:
        import mesh
    finally:
        sys.path.remove(EX06)
    return mesh.xyce_case(n, k, str(out), **kw)


# ------------------------------------------------------------------ the constructs

WAVE_T = np.array([0.0, 1e-9, 1.2e-9, 2e-9, 2.5e-9])
WAVE_V = np.array([0.0, -0.05, -0.08, -0.02, -0.03])


def _include_deck(tmp_path, sim):
    """A subcircuit that includes the per-pass sources (as the split does),
    a 1 kohm load on the source's node; the rail at 1 V."""
    (tmp_path / "src.inc").write_text(
        "* emir.selfconsist -- test\n"
        + pwl.spice_source("VEMIRSC0", "a__s0", "a", WAVE_T, WAVE_V, sim=sim) + "\n")
    deck = ["* the per-pass include inside a subcircuit", ".subckt blk a",
            ".include src.inc", "R1 a__s0 0 1k", ".ends", "V1 vdd 0 DC 1", "X0 vdd blk"]
    return deck


def _corners_ok(t, v):
    want = 1.0 + np.interp(np.r_[WAVE_T, 3e-9, 3.9e-9], WAVE_T, WAVE_V)
    got = np.interp(np.r_[WAVE_T, 3e-9, 3.9e-9], t, v)
    return np.abs(got - want).max()


@pytest.mark.skipif(not HAVE["xyce"], reason="Xyce is not installed")
def test_xyce_reads_the_include_and_the_column_file(tmp_path):
    deck = _include_deck(tmp_path, "xyce")
    pwl.write_columns(str(tmp_path / "w.txt"), WAVE_T, 1.0 + WAVE_V)
    deck += ['V2 b 0 PWL FILE "w.txt"', "R2 b 0 1k", ".options timeint reltol=1e-4",
             ".tran 10p 4e-9",
             ".print tran format=csv file=x.csv V(X0:a__s0) V(b)", ".end"]
    (tmp_path / "x.cir").write_text("\n".join(deck) + "\n")
    p = subprocess.run([site.tool("xyce")[0], "x.cir"], cwd=tmp_path, capture_output=True,
                       text=True, timeout=120)
    assert p.returncode == 0, p.stdout[-2000:]
    rows = np.loadtxt(tmp_path / "x.csv", delimiter=",", skiprows=1)
    assert _corners_ok(rows[:, 0], rows[:, 1]) < 1e-9          # the include
    assert _corners_ok(rows[:, 0], rows[:, 2]) < 1e-9          # PWL FILE


@pytest.mark.skipif(not HAVE["ngspice"], reason="ngspice is not installed")
def test_ngspice_reads_the_include(tmp_path):
    deck = _include_deck(tmp_path, "ngspice")
    probes = np.r_[WAVE_T[1:], 3e-9, 3.9e-9]
    deck += [".tran 10p 4e-9", ".control", "run"]
    deck += [f"meas tran m{i} find v(x0.a__s0) at={t:g}" for i, t in enumerate(probes)]
    deck += [".endc", ".end"]
    (tmp_path / "n.cir").write_text("\n".join(deck) + "\n")
    p = subprocess.run([site.tool("ngspice")[0], "-b", "n.cir"], cwd=tmp_path,
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stdout[-2000:] + p.stderr[-2000:]
    got = {}
    for ln in p.stdout.splitlines():
        w = ln.split()
        if len(w) >= 3 and w[0].startswith("m") and w[1] == "=":
            got[int(w[0][1:])] = float(w[2])
    want = 1.0 + np.interp(probes, WAVE_T, WAVE_V)
    assert len(got) == len(probes)
    assert max(abs(got[i] - want[i]) for i in got) < 1e-6


# ------------------------------------------------------------------ the loop on a mesh


def _sc(out, sim="xyce", *extra, net="VDD"):
    return emir("run", out / "grid_r.dspf", "--process", PROC06, "--net", net, "--mode",
                "cc-grid", "--self-consistent", "--sim", sim, "--netlist", out / "cc_tb.cir",
                "--period", "1e-9", "--droop-file", out / "sc" / "droop.inc",
                "--summary-out", out / "sc.json", *extra, "--format", "json", cwd=out)


@pytest.mark.skipif(not HAVE["xyce"], reason="Xyce is not installed")
def test_the_loop_on_a_small_mesh_closes_kcl_and_stays_cheap(tmp_path):
    out = tmp_path / "m"
    _mesh(out, 6, 8)
    d = jdoc(_sc(out))
    n = d["nets"][0]
    sc = n["self_consistency"]
    assert sc["converged"] and sc["method"] == "waveform" and sc["n_passes"] <= 5
    p = sc["passes"]
    # pass 0 is the open loop exactly: every split source at 0 V
    assert p[0]["fed_by"] == "open loop" and p[0]["pwl_points"] is None
    # KCL: the split pins carry all of the rail source's current, every pass
    assert all(x["kcl"] is not None and x["kcl"]["avg_rel"] < 1e-5 and
               x["kcl"]["instant_rel"] < 1e-4 for x in p)
    # the cost of a pass: its time points within 4 x the open loop's, and
    # within the PWL cap
    assert all(x["points"] <= 4 * p[0]["points"] for x in p[1:])
    assert all(x["pwl_points"] <= pwl.MAX_POINTS for x in p[1:])
    assert "EMIR-W541" not in [x["code"] for x in d["notices"]]
    # the testbench sets reltol=1e-4: no warning
    assert "EMIR-W540" not in [x["code"] for x in d["notices"]]
    # pass 0 against the unsplit testbench simulated with probes (the open loop)
    s = jdoc(emir("simulate", out / "cc_tb.cir", "--sim", "xyce", "--dspf",
                  out / "grid_r.dspf", "--mode", "cc-grid", "--probe", "--out",
                  out / "ol" / "cc.raw", "--format", "json", cwd=out))
    ol = jdoc(emir("run", out / "grid_r.dspf", "--process", PROC06, "--cc-sim", s["result"],
                   "--mode", "cc-grid", "--period", "1e-9", "--net", "VDD", "--format",
                   "json", cwd=out))["nets"][0]
    assert p[0]["ir_drop_mV"] == pytest.approx(ol["ir_drop_mV"], rel=5e-4)
    assert p[0]["worst_ratio"] == pytest.approx(ol["max_ratio"]["all"], rel=5e-4)


@pytest.mark.skipif(not HAVE["xyce"], reason="Xyce is not installed")
def test_a_terminal_on_the_net_that_is_no_pin_is_refused(tmp_path):
    out = tmp_path / "m"
    _mesh(out, 4, 4)
    tb = (out / "cc_tb.cir").read_text().replace(
        ".options", "MPX outx in0 VDD VDD pch W=40u L=0.1u\nCLX outx 0 200f\n.options")
    (out / "cc_tb.cir").write_text(tb)
    d = jdoc(_sc(out, "xyce", "--no-check"), 2)
    assert d["code"] == "EMIR-E540" and "MPX" in d["message"]
    assert not (out / "sc" / "pass0.raw").exists()               # nothing was simulated


@pytest.mark.skipif(not HAVE["ngspice"], reason="ngspice is not installed")
def test_the_loop_on_ngspice_warns_on_a_loose_reltol(tmp_path):
    out = tmp_path / "m"
    _mesh(out, 4, 4)
    tb = (out / "cc_tb.cir").read_text().replace(".options timeint reltol=1e-4\n", "")
    (out / "cc_tb.cir").write_text(tb)
    d = jdoc(_sc(out, "ngspice"))
    sc = d["nets"][0]["self_consistency"]
    assert sc["converged"] and sc["simulator"] == "ngspice"
    assert "EMIR-W540" in [x["code"] for x in d["notices"]]
    assert all(x["kcl"]["avg_rel"] < 1e-5 for x in sc["passes"])


@pytest.mark.skipif(not HAVE["xyce"], reason="Xyce is not installed")
def test_vdd_and_vss_close_together(tmp_path):
    out = tmp_path / "m"
    _mesh(out, 5, 8, vss=True)
    d = jdoc(_sc(out, "xyce", net="VDD,VSS"))
    nets = {n["net"]: n for n in d["nets"]}
    assert set(nets) == {"VDD", "VSS"}
    a, b = nets["VDD"]["self_consistency"], nets["VSS"]["self_consistency"]
    assert a["converged"] and b["converged"] and a["n_passes"] == b["n_passes"]
    assert a["nets"] == b["nets"] == ["VDD", "VSS"]
    assert a["vdd"] == 1.0 and b["vdd"] == 0.0
    # each rail's KCL against its own source (VSUP, VGND)
    assert a["passes"][0]["kcl"]["source"] == "VSUP" and b["passes"][0]["kcl"]["source"] == "VGND"
    assert all(x["kcl"]["avg_rel"] < 1e-5 for x in a["passes"] + b["passes"])
    # the include holds both nets' sources
    inc = (out / "sc" / "droop.inc").read_text()
    assert inc.count("VEMIRSC") == 16
    # --vdd with several nets is refused: the rails come from the testbench
    p = _sc(out, "xyce", "--vdd", "1.0", "--no-check", net="VDD,VSS")
    assert jdoc(p, 2)["code"] == "EMIR-E541"
    shutil.rmtree(out / "sc")


@pytest.mark.skipif(not HAVE["xyce"], reason="Xyce is not installed")
def test_a_load_on_the_rail_outside_the_split_breaks_kcl(tmp_path):
    """EMIR-W541: a resistor of the testbench on the rail (an element type the
    split does not probe) draws from the rail source, not through a split pin."""
    out = tmp_path / "m"
    _mesh(out, 4, 4)
    tb = (out / "cc_tb.cir").read_text().replace(".options", "RLEAK VDD 0 1k\n.options")
    (out / "cc_tb.cir").write_text(tb)
    d = jdoc(_sc(out, "xyce", "--max-passes", "2", "--no-check"))
    assert "EMIR-W541" in [x["code"] for x in d["notices"]]
    k = d["nets"][0]["self_consistency"]["passes"][0]["kcl"]
    assert k["avg_rel"] > 1e-3 and k["source"] == "VSUP"


@pytest.mark.skipif(not HAVE["xyce"], reason="Xyce is not installed")
def test_the_rc_dspf_feeds_back_the_rc_grid_and_labels_its_dynamic_ir(tmp_path):
    """From the RC DSPF the droop fed back is the RC grid's (no EMIR-W544), and
    --dynamic after a converged loop is the reported pass's droop through the
    RC grid: labelled so, not "open loop: an upper bound"."""
    out = tmp_path / "m"
    _mesh(out, 4, 4)
    p = emir("run", out / "grid_rc.dspf", "--process", PROC06, "--net", "VDD", "--mode",
             "cc-grid", "--self-consistent", "--sim", "xyce", "--netlist", out / "cc_tb.cir",
             "--period", "1e-9", "--droop-file", out / "sc" / "droop.inc", "--dynamic",
             "--summary-out", out / "sc.json", cwd=out)
    assert p.returncode == 0, p.stderr[-2000:]
    s = json.loads((out / "sc.json").read_text())
    n = s["nets"][0]
    sc = n["self_consistency"]
    assert sc["converged"] and sc["feedback"]["grid"] == "RC" and sc["feedback"]["caps"] > 0
    assert "EMIR-W544" not in (p.stdout + p.stderr)
    k = sc["reported_pass"]
    assert k > 0 and n["dynamic"]["source"] == (
        f"cc-grid: CC tap waveforms through the RC grid (the currents of pass {k} of the "
        f"self-consistent loop, each pin fed its droop waveform)")
    assert "upper bound" not in n["dynamic"]["source"]
    assert f"(pass {k} of the self-consistent loop," in p.stdout
    # --dynamic scores the branches with the RC grid (its default basis, worst:
    # per segment the larger of the R-only and the RC grid's current): no
    # EMIR-W545; without it they are scored on the R-only grid, and it says so
    assert sc["scoring"] == {"grid": "max(R-only, RC)", "basis": "worst", "ac": True}
    assert "EMIR-W545" not in (p.stdout + p.stderr)
    d = jdoc(emir("run", out / "grid_rc.dspf", "--process", PROC06, "--net", "VDD", "--mode",
                  "cc-grid", "--self-consistent", "--sim", "xyce", "--netlist",
                  out / "cc_tb.cir", "--period", "1e-9", "--droop-file",
                  out / "sc" / "droop.inc", "--format", "json", cwd=out))
    sc = d["nets"][0]["self_consistency"]
    assert sc["converged"] and sc["scoring"] == {"grid": "R-only", "basis": None, "ac": True}
    assert "EMIR-W545" in [x["code"] for x in d["notices"]]
    step = next(s for s in d["next_steps"] if "EMIR-W545" in s["why"])
    assert step["needs_human"] and "--dynamic" in step["command"]
    # and the R-only DSPF of the same mesh says its droop has no capacitance
    d = jdoc(_sc(out, "xyce", "--max-passes", "1"))
    assert "EMIR-W544" in [x["code"] for x in d["notices"]]
    assert d["nets"][0]["self_consistency"]["feedback"]["grid"] == "R-only"


@pytest.mark.skipif(not HAVE["xyce"], reason="Xyce is not installed")
def test_the_waveform_loop_keeps_the_pass_result_guards(tmp_path):
    """The loop never overwrites a pass result it did not write (EMIR-E526):
    a later pass's result someone left in the loop's directory is refused
    before pass 0 simulates, and kept; every pass the loop runs stamps its
    result before the simulator writes it; a second run replaces emir's own
    files, and a run that names the same net twice closes it once."""
    out = tmp_path / "m"
    _mesh(out, 4, 4)
    sc = out / "sc"
    sc.mkdir()
    theirs = sc / "pass2.raw"
    theirs.write_text("someone's result\n")
    e = jdoc(_sc(out, "xyce", "--max-passes", "3"), 2)
    assert e["code"] == "EMIR-E526" and "pass2.raw" in e["message"]
    assert theirs.read_text() == "someone's result\n"
    assert not (sc / "pass0.raw").exists() and not (sc / "pass0.raw.emir").exists()
    theirs.unlink()
    for net in ("VDD", "VDD,VDD"):
        d = jdoc(_sc(out, "xyce", "--max-passes", "3", net=net))
        assert [n["net"] for n in d["nets"]] == ["VDD"]
        n_sims = d["nets"][0]["self_consistency"]["n_passes"]
        assert n_sims >= 2
        for k in range(n_sims):
            assert (sc / f"pass{k}.raw").is_file() and (sc / f"pass{k}.raw.emir").is_file()


@pytest.mark.skipif(not HAVE["xyce"], reason="Xyce is not installed")
def test_a_rail_the_testbench_ramps_takes_a_per_net_vdd(tmp_path):
    """Two nets, VDD driven by a PWL ramp (no DC value to read): one --vdd is
    refused (EMIR-E541, it cannot say which net), none is refused (EMIR-E503),
    and one per net runs -- the net left out (VSS) read from the testbench."""
    out = tmp_path / "m"
    _mesh(out, 4, 4, vss=True)
    tb = (out / "cc_tb.cir").read_text().replace("VSUP VDD 0 DC 1", "VSUP VDD 0 PWL(0 0 100p 1)")
    (out / "cc_tb.cir").write_text(tb)
    e = jdoc(_sc(out, "xyce", "--no-check", net="VDD,VSS"), 2)
    assert e["code"] == "EMIR-E503" and "--vdd VDD=<volts>" in e["message"]
    e = jdoc(_sc(out, "xyce", "--vdd", "1", "--no-check", net="VDD,VSS"), 2)
    assert e["code"] == "EMIR-E541" and "VDD=<volts>" in e["message"]
    d = jdoc(_sc(out, "xyce", "--vdd", "VDD=1", "--max-passes", "2", net="VDD,VSS"))
    nets = {n["net"]: n["self_consistency"] for n in d["nets"]}
    assert nets["VDD"]["vdd"] == 1.0 and nets["VSS"]["vdd"] == 0.0
    e = jdoc(_sc(out, "xyce", "--vdd", "VCC=1", "--no-check", net="VDD,VSS"), 2)
    assert e["code"] == "EMIR-E541"
