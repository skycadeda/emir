"""The waveform loop's edges: the rail options
(per-net --vdd, a ground net's supply), how a run says how its loop ended
(the report, the HTML provenance, the chart, the next steps), the timeout's
option name, the Spectre route's reltol warning, and the examples' testbench
tolerance. No simulator."""
from __future__ import annotations

import argparse
import os
import subprocess
from types import SimpleNamespace

import pytest
from _helpers import DSPF, ROOT, write

from emir.errors import EmirError, collecting


# ------------------------------------------------------------------ --vdd


def test_vdd_takes_one_voltage_or_one_per_net():
    from emir.selfconsist import vdd_arg, vdd_for, vdd_of
    assert vdd_arg("1.2") == 1.2
    assert vdd_arg("VDD=1.2,VSS=0") == {"VDD": 1.2, "VSS": 0.0}
    for bad in ("VDD=x", "=1", "abc"):
        with pytest.raises(argparse.ArgumentTypeError):
            vdd_arg(bad)
    assert vdd_for(None, ["VDD", "VSS"]) == ({"VDD": None, "VSS": None}, None)
    assert vdd_for({"VDD": 1.2}, ["VDD", "VSS"]) == ({"VDD": 1.2, "VSS": None}, None)
    per, why = vdd_for(1.2, ["VDD", "VSS"])
    assert per is None and "VDD=<volts>" in why
    per, why = vdd_for({"VCC": 1.0}, ["VDD"])
    assert per is None and "VCC" in why
    assert vdd_of({"VDD": 1.2}, "VDD") == 1.2 and vdd_of(1.0, "X") == 1.0


def test_a_ground_nets_guard_is_the_testbenchs_supply(tmp_path):
    from emir.selfconsist import supply_span
    tb = write(tmp_path / "tb.cir", "* t\nVSUP VDD 0 DC 1.1\nVGND VSS 0 DC 0\n"
               "VB vb 0 PWL(0 0 1n 3)\nR1 VDD 0 1k\n.tran 1p 1n\n.end\n")
    assert supply_span(tb) == pytest.approx(1.1)
    assert supply_span(write(tmp_path / "n.cir", "* t\nR1 a 0 1\n.end\n")) is None


# ------------------------------------------------------------------ how a loop ended


def _sc(stopped, converged, diverged=False, reported=0, n=3, grid="R-only", limit=False):
    passes = [{"pass": k, "residual_mV": 10.0 / (k + 1), "tolerance_mV": 1.0,
               "ir_drop_mV": 40.0 - k} for k in range(n)]
    return {"converged": converged, "diverged": diverged, "stopped": stopped,
            "reported_pass": reported, "passes": passes, "vdd": 1.0, "simulator": "xyce",
            "nets": ["VDD"], "feedback": {"grid": grid}, "at_pass_limit": limit,
            "max_passes": 10}


@pytest.mark.parametrize("stopped,diverged", [("residual grew twice", True),
                                              ("droop above half the supply", True),
                                              ("pass limit", False), ("time limit", False),
                                              ("simulation failed", False)])
def test_the_report_does_not_call_the_open_loop_self_consistent(stopped, diverged):
    """A loop that reports pass 0 is the open loop: the report once said
    'accurate rather than conservative' whatever the state."""
    from emir.report import self_consistency_text
    t = self_consistency_text("VDD", _sc(stopped, False, diverged))
    assert "accurate rather than conservative" not in t
    assert "open loop" in t and "is reported" in t and "not self-consistent" in t


def test_the_report_says_settled_and_the_grid_fed_back():
    from emir.report import self_consistency_text
    t = self_consistency_text("VDD", _sc("settled", True, reported=7, n=8, limit=True))
    assert "settled" in t and "accurate rather than conservative" in t
    assert "last allowed pass" in t and "EMIR-W544" in t
    t = self_consistency_text("VDD", _sc("converged", True, reported=4, n=5, grid="RC"))
    assert "converged" in t and "EMIR-W544" not in t and "R-only grid" in t


def test_the_html_and_the_chart_say_how_the_loop_ended():
    from emir.netreport import _sc_line
    line = _sc_line(_sc("pass limit", False))
    assert "NOT converged" in line and "pass 0 reported" in line and "tolerance" in line
    assert "converged" in _sc_line(_sc("converged", True, reported=2))
    # the chart's subtitle and alerts (`scene.loop_status`): each stop state
    # with its own code, settled as its own state, the tolerance the last pass
    # was held to (here 1.0 mV), not the --tol-mV floor
    from emir.scene import loop_status

    def chart(sc):
        return loop_status({"VDD": {"mode": "cc-grid", "self_consistent": True,
                                    "self_consistency": {**sc, "tol_mV": 0.2}}})
    for stopped, diverged, code, label in (
            ("time limit", False, "EMIR-W543", "STOPPED"),
            ("simulation failed", False, "EMIR-W543", "STOPPED"),
            ("pass limit", False, "EMIR-W407", "NOT converged"),
            ("residual grew twice", True, "EMIR-W406", "DIVERGING"),
            ("droop above half the supply", True, "EMIR-W406", "DIVERGING")):
        words, alerts = chart(_sc(stopped, False, diverged))
        assert f"({label} after 3 passes, tol 1 mV)" in words, words
        assert len(alerts) == 1 and alerts[0].startswith(label.upper() + ": VDD's")
        assert f"({code})" in alerts[0] and "pass 0, the open loop, is reported" in alerts[0]
    words, alerts = chart(_sc("settled", True, reported=7, n=8, limit=True))
    assert words == ("cc-grid, self-consistent (settled in 8 passes, the --max-passes "
                     "limit, tol 1 mV)") and alerts == []
    words, alerts = chart(_sc("converged", True, reported=2))
    assert words == "cc-grid, self-consistent (converged in 3 passes, tol 1 mV)"
    assert alerts == []


def test_an_rc_loop_scored_on_the_r_only_grid_says_so():
    """From an RC DSPF the loop converged with no code while its minor branches,
    scored on the R-only grid, read 18.5 % low against RCC (the grid's
    capacitors' own currents missing; the open loop's high device currents had
    hid it). EMIR-W545 says so, the report quotes it, and the next step offers
    --dynamic, which scores them through the RC grid; with that scoring (the
    worst or decap-aware basis) nothing is raised."""
    from emir.report import self_consistency_text
    from emir.selfconsist import scoring, scoring_notice
    from emir.summary import _sc_dynamic_step
    assert scoring({}) == {"grid": "R-only", "basis": None, "ac": None}
    # the worst basis is per segment the larger of the R-only and the RC grid's
    # current: its own label (it keeps the R-only grid's high readings)
    assert scoring({"dynamic": {"worst_V": 1e-3},
                    "em_basis": {"used": "worst"}})["grid"] == "max(R-only, RC)"
    assert scoring({"dynamic": {"worst_V": 1e-3},
                    "em_basis": {"used": "decap-aware"}})["grid"] == "RC"
    assert scoring({"dynamic": {"worst_V": 1e-3},
                    "em_basis": {"used": "decap-free"}})["grid"] == "R-only"
    # not computed: em_basis falls back to decap-free
    assert scoring({"dynamic": None, "em_basis": {"used": "decap-free"}})["grid"] == "R-only"

    def codes(sc):
        with collecting(propagate=False) as notes:
            scoring_notice("VDD", sc, log=lambda *a, **k: None)
        return [x["code"] for x in notes]
    rc = {**_sc("converged", True, reported=5, n=6, grid="RC"),
          "scoring": {"grid": "R-only", "basis": None}}
    assert codes(rc) == ["EMIR-W545"]
    assert codes({**rc, "scoring": {"grid": "RC", "basis": "worst"}}) == []
    assert codes({**rc, "feedback": {"grid": "R-only"}}) == []          # W544's case
    assert codes({**rc, "converged": False}) == []                      # the open loop
    t = self_consistency_text("VDD", rc)
    assert "EMIR-W545" in t and "R-only grid" in t and "--dynamic" in t
    t = self_consistency_text("VDD", {**rc, "scoring": {"grid": "RC", "basis": "worst"}})
    assert "EMIR-W545" not in t and "through the RC grid" in t and "worst" in t
    # (with the block's period: without it the step plans the re-run with
    # `emir next`, which asks for it -- tests/test_loop_handoffs.py)
    a = SimpleNamespace(argv=["d.dspf", "--self-consistent", "--net", "VDD", "--period",
                              "1e-09"], dynamic=False, period=1e-9)
    st = _sc_dynamic_step(a, "VDD", rc, ["--format", "json"])
    assert st["needs_human"] and st["command"].endswith("--dynamic --format json")
    assert "EMIR-W545" in st["why"]
    a.dynamic = True                        # --dynamic given, decap-free chosen: theirs
    assert _sc_dynamic_step(a, "VDD", rc, []) is None


def test_the_next_step_after_an_open_loop_or_last_pass_answer():
    from emir.summary import _sc_retry_step
    a = SimpleNamespace(argv=["d.dspf", "--self-consistent", "--max-passes", "4"])
    st = _sc_retry_step(a, "VDD", _sc("pass limit", False), [])
    assert st["needs_human"] and "--max-passes 16" in st["command"]
    assert "--max-passes 4" not in st["command"]
    st = _sc_retry_step(a, "VDD", {**_sc("time limit", False),
                                   "passes": [{"seconds": 3000.0}]}, [])
    assert "--sim-timeout 12000" in st["command"]
    st = _sc_retry_step(a, "VDD", _sc("settled", True, limit=True), [])
    assert "last allowed pass" in st["why"]
    # diverging: more passes would not help; a failed simulation: its log says why
    for stopped in ("residual grew twice", "droop above half the supply", "simulation failed"):
        assert _sc_retry_step(a, "VDD", _sc(stopped, False, stopped != "simulation failed"),
                              []) is None
    assert _sc_retry_step(SimpleNamespace(argv=[]), "VDD", _sc("pass limit", False), []) is None


# ------------------------------------------------------------------ the simulators


def test_a_timeout_names_the_commands_option(tmp_path, monkeypatch):
    """`emir run` sets the simulation's limit with --sim-timeout: EMIR-E605
    said --timeout (`emir simulate`'s)."""
    from emir import opensim

    def slow(*a, **k):
        raise subprocess.TimeoutExpired(cmd="Xyce", timeout=1.0)
    monkeypatch.setattr(opensim.subproc, "run", slow)
    for option in ("--timeout", "--sim-timeout"):
        with pytest.raises(EmirError) as e:
            opensim.run_simulator("xyce", "Xyce", str(tmp_path / "d.cir"),
                                  str(tmp_path / "r.raw"), 1.0, log=lambda *a: None,
                                  option=option)
        assert e.value.code == "EMIR-E605" and e.value.message.endswith(option)


def test_the_spectre_route_warns_on_a_loose_reltol(tmp_path, monkeypatch):
    """EMIR-W540 names Spectre's reltol; the Spectre route never raised it."""
    from emir import dspf as D
    from emir.selfconsist import rail_split_fragment
    monkeypatch.chdir(tmp_path)
    d = D.parse(DSPF)
    frag = rail_split_fragment(d, "VDD")
    pins = sorted(f"{t['inst']}:{t['pin']}" for t in d.net_taps("VDD"))
    from emir.selfconsist import node_name
    body = [ln for ln in frag.splitlines() if not ln.startswith("//")]
    body += [f"{p.split(':')[0]} (out in {node_name(p)} 0) pch" for p in pins]
    net = write(tmp_path / "cc.scs", "// cc\n" + "\n".join(body) + "\ntran tran stop=1n\n")
    a = SimpleNamespace(sim="spectre", droop_file=None, netlist=net, spectre=None,
                        sim_timeout=None, save_prefix=None)
    with collecting() as notes:
        D._sc_driver(a, d, "VDD")
    assert "EMIR-W540" in [n["code"] for n in notes]
    write(net, open(net).read().replace("tran tran", "opts options reltol=1e-5\ntran tran"))
    with collecting() as notes:
        D._sc_driver(a, d, "VDD")
    assert "EMIR-W540" not in [n["code"] for n in notes]


def test_the_self_consistent_examples_simulate_at_reltol_1e_4():
    """Example 12 (the documented self-consistent example) raised its own
    EMIR-W540: its testbenches set no relative tolerance."""
    from emir.selfconsist import reltol_of
    ex = os.path.join(ROOT, "examples", "12_self_consistent")
    for f in ("tb_cc.cir", "tb_rcc.cir"):
        text = open(os.path.join(ex, f), encoding="utf-8").read()
        for sim in ("xyce", "ngspice"):
            v, _line = reltol_of([text], sim)
            assert v is not None and v <= 1e-4, (f, sim)
