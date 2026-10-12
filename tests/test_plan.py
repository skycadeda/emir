"""`emir next`: one test per branch of the decision tree (`emir/plan.py`),
the extension hook, and the CLI around it. Tools are pinned per test, so
nothing depends on what this machine has installed."""
import json
import shlex
import subprocess
import sys

import pytest
from _helpers import CUR, DSPF, PROC, ROOT, cc_result, rcc_result, time_base, \
    two_net_dspf, write

from emir import plan as P
from emir.validate import _psf_tran


@pytest.fixture
def tools(monkeypatch):
    """Pin which executables `emir info` would find: `tools(spectre=True)`."""
    from emir import site
    found = {}

    def fake(name, explicit=None):
        return (f"/opt/fake/{name}" if found.get(name) else None), "$PATH"
    monkeypatch.setattr(site, "tool", fake)

    def set_(**kw):
        found.clear()
        found.update(kw)
    return set_


def build(tools_set=None, **kw):
    f = P.Facts(**kw)
    return f, P.build(f)


def ids(plan):
    return [s["id"] for s in plan.steps]


def what(plan):
    return " | ".join(m["what"] for m in plan.missing)


# ------------------------------------------------------------ the branches


def test_nothing_yet_asks_for_a_dspf(tools):
    tools()
    f, plan = build()
    assert ids(plan) == ["selftest"] and plan.stopped
    assert "an extracted DSPF" in what(plan)
    assert "Quantus" in plan.missing[0]["how"] and "open-source" in plan.missing[0]["how"]


def test_a_missing_or_broken_dspf_stops_the_plan(tools, tmp_path):
    tools()
    _, plan = build(dspf=str(tmp_path / "none.dspf"))
    assert "a readable DSPF" in what(plan) and plan.stopped
    bad = write(tmp_path / "bad.dspf", "*|NET\n")
    _, plan = build(dspf=bad)
    assert "EMIR-E201" in plan.missing[0]["why"]


def test_an_unknown_net_is_named_with_a_suggestion(tools):
    tools()
    _, plan = build(dspf=DSPF, net="vdd")
    assert "net 'vdd'" in what(plan) and "did you mean 'VDD'" in plan.missing[0]["why"]
    assert ids(plan) == ["selftest", "nets"] and plan.stopped


def test_no_process_file_is_missing_and_never_invented(tools):
    tools(xyce=True)
    _, plan = build(dspf=DSPF, net="VDD")
    m = next(m for m in plan.missing if "process file" in m["what"])
    assert "Never invent a limit" in m["how"] and "pdks/TEMPLATE.yaml" in m["how"]
    assert "check" not in ids(plan) and "run" not in ids(plan)


def test_dc_currents_plan_check_run_report(tools, tmp_path):
    tools()
    f, plan = build(dspf=DSPF, process=PROC, currents=CUR, net="VDD", json=True)
    assert plan.mode["chosen"] == "dc"
    assert ids(plan) == ["selftest", "check", "run", "report"]
    run = plan.steps[2]
    assert not run["needs_human"] and "--summary-out emir_out/summary.json" in run["command"]
    assert "the bundled ILLUSTRATIVE sample" in " ".join(plan.advice)
    # the commands run as written
    for s in plan.steps[1:]:
        argv = shlex.split(s["command"])
        p = subprocess.run([sys.executable, "-m", "emir", *argv[1:]], cwd=tmp_path,
                           capture_output=True, text=True, env={"PYTHONPATH": ROOT})
        assert p.returncode == 0, (s["command"], p.stderr)


def test_dc_currents_need_one_net(tools, tmp_path):
    tools()
    _, plan = build(dspf=two_net_dspf(tmp_path), process=PROC, currents=CUR)
    assert "--net" in what(plan) and "run" not in ids(plan)


def test_dc_currents_on_a_one_net_dspf_need_no_net(tools, tmp_path):
    """`emir run` takes the only net; the plan once demanded --net anyway."""
    tools()
    _, plan = build(dspf=DSPF, process=PROC, currents=CUR)
    assert "--net" not in what(plan)
    assert ids(plan) == ["selftest", "nets", "check", "run", "report"]
    for s in plan.steps[1:4]:
        argv = shlex.split(s["command"])
        p = subprocess.run([sys.executable, "-m", "emir", *argv[1:]], cwd=tmp_path,
                           capture_output=True, text=True, env={"PYTHONPATH": ROOT})
        assert p.returncode == 0, (s["command"], p.stderr)


def test_a_simulation_is_planned_as_rcc(tools, tmp_path):
    """No --mode and currents from a simulation: the plan is rcc (every
    branch current of the RC netlist), with no step that asks for a mode."""
    tools(xyce=True)
    _, plan = build(dspf=DSPF, process=PROC, net="VDD", period=1e-9)
    assert plan.mode["chosen"] == "rcc" and "every branch current" in plan.mode["why"]
    assert "choose-mode" not in ids(plan) and not any("choices" in s for s in plan.steps)
    assert ids(plan) == ["selftest", "verify-sim", "simulate", "check", "run", "report"]
    assert not any("--mode" in s["command"] for s in plan.steps)
    # without --period the simulation's run is refused (EMIR-E428): the plan
    # asks for the period and plans no check or run (review of 93da011)
    _, plan = build(dspf=DSPF, process=PROC, net="VDD")
    assert ids(plan) == ["selftest", "verify-sim", "simulate"] and plan.blocked
    assert any("period" in m["what"] for m in plan.missing)


def test_a_pin_current_result_needs_the_rc_netlist_simulated(tools, tmp_path):
    """A result with pin currents only cannot be screened (rcc reads branch
    currents): the plan names what is missing and plans the save lines; it
    never checks that result."""
    tools()
    cc = cc_result(tmp_path)
    _, plan = build(dspf=DSPF, process=PROC, cc_sim=cc, net="VDD")
    assert plan.mode["chosen"] == "rcc" and plan.blocked
    m = plan.missing[0]
    assert m["what"] == "the branch currents of VDD" and "0 of 16 branch currents" in m["why"]
    assert ids(plan) == ["selftest", "save-lines:VDD"]
    assert not any(cc in s["command"] for s in plan.steps)


def test_every_branch_current_plans_the_run(tools, tmp_path):
    tools()
    res = rcc_result(tmp_path)
    # every input a simulated run needs: the result and the block's period
    _, plan = build(dspf=DSPF, process=PROC, cc_sim=res, period=1e-9)
    assert plan.mode["chosen"] == "rcc" and not plan.missing
    assert ids(plan)[-3:] == ["check", "run", "report"]
    assert f"--cc-sim {res}" in plan.steps[-2]["command"]
    assert "--mode" not in plan.steps[-2]["command"]


def test_a_result_without_the_net_currents_asks_for_saves(tools, tmp_path):
    tools(xyce=True)
    t = time_base()
    res = _psf_tran(str(tmp_path / "x.tran"), t, {"OTHER:1": 0 * t})
    _, plan = build(dspf=DSPF, process=PROC, cc_sim=res, net="VDD")
    assert "the branch currents of VDD" in what(plan)
    # the Xyce route is verified on this machine first (no record here)
    assert ids(plan) == ["selftest", "verify-sim", "save-lines:VDD", "simulate",
                         "names-sidecar", "check", "run", "report"]
    assert plan.steps[1]["command"] == "emir verify-sim --sim xyce"
    assert "--mode" not in plan.steps[2]["command"]
    sim = plan.steps[3]
    assert sim["command"].startswith("Xyce ") and sim["needs_human"] and sim["long_run"]
    # the save lines are Spectre syntax, and the Xyce names need the sidecar
    assert "Spectre syntax" in sim["why"] and sim["after"] == ["save-lines:VDD", "verify-sim"]
    assert plan.steps[4]["command"] == "emir schema names"
    check, run = plan.steps[5], plan.steps[6]
    assert "--cc-sim '<result.raw>'" in run["command"]               # the new result
    # not runnable as printed, and it says so
    for s in (check, run):
        assert s["status"] == "waiting" and s["placeholders"] == ["<result.raw>"]
    assert check["after"] == ["simulate", "names-sidecar"] and run["after"] == ["check"]


def test_rcc_on_pin_currents_plans_the_rcc_saves(tools, tmp_path):
    """--mode rcc (accepted, the one method) on a result with pin currents
    only: the check would refuse it (EMIR-E415), so the plan never checks
    that result: it plans the rcc saves and the simulation."""
    tools(spectre=True)
    cc = cc_result(tmp_path)
    _, plan = build(dspf=DSPF, process=PROC, cc_sim=cc, mode="rcc", net="VDD")
    assert plan.mode["chosen"] == "rcc"
    assert ids(plan)[1:4] == ["verify-sim", "save-lines:VDD", "simulate"]
    # Spectre is licensed: its verification needs the person's go-ahead
    assert plan.steps[1]["command"] == "emir verify-sim --sim spectre --run"
    assert plan.steps[1]["needs_human"] and plan.steps[1]["licensed_tool"] == "spectre"
    assert not any(cc in s["command"] for s in plan.steps)


def test_an_unreadable_result_stops_the_plan(tools, tmp_path):
    tools()
    b = tmp_path / "bin.raw"
    b.write_bytes(b"Title: x\nPlotname: Transient\nBinary:\n" + b"\0" * 16)
    _, plan = build(dspf=DSPF, process=PROC, cc_sim=str(b))
    assert "EMIR-E409" in plan.missing[0]["why"] and plan.stopped


def test_netlist_without_spectre_says_so(tools, tmp_path):
    # no simulator at all (with Xyce or ngspice the netlist is the testbench
    # `emir simulate` runs: tests/test_opensim.py)
    tools()
    _, plan = build(dspf=DSPF, process=PROC, netlist=write(tmp_path / "tb.cir", "x\n"),
                    have_cadence=False)
    assert "Spectre, for --netlist" in what(plan) and plan.stopped
    # a deck in Spectre's language says it is one (tests/test_plan_spectre_deck.py)
    _, plan = build(dspf=DSPF, process=PROC, netlist=write(tmp_path / "tb.scs", "x\n"),
                    have_cadence=False)
    assert "Spectre, for the Spectre testbench --netlist" in what(plan) and plan.stopped


def test_netlist_with_spectre_needs_the_person(tools, tmp_path):
    tools(spectre=True)
    tb = write(tmp_path / "tb.scs", "x\n")
    _, plan = build(dspf=DSPF, process=PROC, netlist=tb, period=1e-9)
    assert not plan.blocked and plan.mode["chosen"] == "rcc"
    run = next(s for s in plan.steps if s["id"] == "run")
    assert run["licensed_tool"] == "spectre" and run["needs_human"] and run["long_run"]
    assert "--netlist" in run["command"] and "--run" in run["command"]
    assert "--period 1e-09" in run["command"]
    # without --period the licensed run is certain to be refused (EMIR-E428):
    # it is not planned; the plan asks for the period (review of 93da011)
    _, plan = build(dspf=DSPF, process=PROC, netlist=tb)
    assert plan.blocked and not plan.has("run") and not plan.has("check")
    assert any("period" in m["what"] for m in plan.missing)


def test_small_net_without_a_result_plans_rcc(tools):
    tools(spectre=True)
    _, plan = build(dspf=DSPF, process=PROC, net="VDD")
    assert plan.mode["chosen"] == "rcc"
    assert ids(plan)[:4] == ["selftest", "verify-sim", "save-lines", "simulate"]
    assert plan.steps[2]["command"].endswith("--net VDD")
    sim = plan.steps[3]
    assert sim["licensed_tool"] == "spectre" and "psfascii" in sim["command"]


def test_a_medium_net_is_simulated_by_the_open_simulator(tools, monkeypatch):
    """Above small_net_nodes the simulation is flagged a long run; the open
    simulator runs through `emir simulate` (emir/plan_opensim.py)."""
    tools(ngspice=True)
    monkeypatch.setitem(P.THRESHOLDS, "small_net_nodes", 5)
    _, plan = build(dspf=DSPF, process=PROC, net="VDD")
    assert plan.mode["chosen"] == "rcc"
    assert plan.steps[1]["id"] == "verify-sim" and not plan.steps[1]["needs_human"]
    sim = plan.steps[2]
    assert sim["id"] == "simulate" and "--sim ngspice" in sim["command"]
    assert not sim["licensed_tool"] and sim["long_run"] and sim["needs_human"]


def test_a_big_block_is_a_long_rcc_run(tools, monkeypatch):
    tools(xyce=True)
    monkeypatch.setitem(P.THRESHOLDS, "big_block_nodes", 10)
    _, plan = build(dspf=DSPF, process=PROC, net="VDD")
    assert plan.mode["chosen"] == "rcc"
    assert any(a.startswith("ADC-top / PLL-top size") for a in plan.advice)
    assert not any("C-only" in a or "cc-grid" in a for a in plan.advice)


def test_a_big_block_run_is_flagged_long(tools, monkeypatch, tmp_path):
    tools()
    monkeypatch.setitem(P.THRESHOLDS, "big_block_nodes", 10)
    _, plan = build(dspf=DSPF, process=PROC, cc_sim=rcc_result(tmp_path), period=1e-9)
    run = next(s for s in plan.steps if s["id"] == "run")
    assert run["long_run"] and run["needs_human"]


def test_no_simulator_at_all_is_missing(tools):
    tools()
    _, plan = build(dspf=DSPF, process=PROC, net="VDD", have_cadence=False)
    assert "a circuit simulator" in what(plan)
    _, plan = build(dspf=DSPF, process=PROC, net="VDD", have_cadence=False, mode="rcc")
    assert "a circuit simulator" in what(plan)


def test_an_invalid_process_file_blocks_the_run(tools, tmp_path):
    tools()
    bad = write(tmp_path / "p.yaml", "layers:\n  M1: {gds: [1, 0], sheet_res: 0.1}\n")
    _, plan = build(dspf=DSPF, process=bad, currents=CUR, net="VDD")
    assert "a valid process file" in what(plan) and "EMIR-E312" in plan.missing[-1]["why"]
    assert "run" not in ids(plan)


@pytest.mark.parametrize("cadence,needle", [(False, "KLayout marker database"),
                                            (True, "never start, kill or restart")])
def test_viewer_wish(tools, cadence, needle):
    tools()
    _, plan = build(dspf=DSPF, process=PROC, currents=CUR, net="VDD", viewer=True,
                    have_cadence=cadence, lib="MYLIB")
    assert any(needle in a for a in plan.advice)
    run = next(s for s in plan.steps if s["id"] == "run")
    assert "--viewer emir_out/viewer --lib MYLIB" in run["command"]


def test_every_plan_ends_with_the_guardrail(tools):
    tools()
    _, plan = build(dspf=DSPF, process=PROC, currents=CUR, net="VDD")
    assert plan.advice[-1].startswith("never invent process limits")


# ------------------------------------------------------------ the hook


def test_a_rule_registers_and_runs_in_order(tools):
    tools()
    seen = []

    @P.rule(15, "test-hook")
    def _hook(f, plan):
        seen.append([s["id"] for s in plan.steps])
        plan.note("hook ran")
    try:
        _, plan = build(dspf=DSPF, process=PROC, currents=CUR, net="VDD")
    finally:
        P.RULES[:] = [r for r in P.RULES if r.name != "test-hook"]
    assert seen == [["selftest"]]                    # after preflight (10), before dspf (20)
    assert "test-hook" in plan.rules and "hook ran" in plan.advice


def test_plan_modules_are_discovered(tools, tmp_path, monkeypatch):
    """A feature adds `emir/plan_<feature>.py`; nothing else changes."""
    import emir
    (tmp_path / "plan_demo.py").write_text(
        "from emir.plan import rule\n\n"
        "@rule(55, 'demo')\n"
        "def _demo(f, plan):\n"
        "    plan.note('demo rule')\n")
    monkeypatch.setattr(emir, "__path__", [*emir.__path__, str(tmp_path)])
    monkeypatch.setattr(P, "_EXTENSIONS_LOADED", False)
    try:
        assert "plan_demo" in P.load_extensions()
        _, plan = build(dspf=DSPF, process=PROC, currents=CUR, net="VDD")
        assert "demo rule" in plan.advice and "demo" in plan.rules
    finally:
        P.RULES[:] = [r for r in P.RULES if r.name != "demo"]
        sys.modules.pop("emir.plan_demo", None)


# ------------------------------------------------------------ the command


def test_cli_text_flags_the_steps_that_need_the_person(tmp_path):
    p = subprocess.run([sys.executable, "-m", "emir", "next", "--dspf", DSPF, "--process",
                        PROC, "--net", "VDD", "--have-cadence"], cwd=ROOT,
                       capture_output=True, text=True,
                       env={"PYTHONPATH": ROOT, "PATH": "/nonexistent"})
    assert p.returncode == 0, p.stderr
    assert "[ASK THE PERSON FIRST: licensed tool: spectre, long run]" in p.stdout
    assert p.stdout.startswith("mode: rcc -- ")
    assert "their choice" not in p.stdout


def test_cli_json(tmp_path):
    p = subprocess.run([sys.executable, "-m", "emir", "next", "--format", "json"], cwd=ROOT,
                       capture_output=True, text=True, env={"PYTHONPATH": ROOT})
    d = json.loads(p.stdout)
    assert d["schema"] == "emir.next/1" and d["steps"][0]["command"] == \
        "emir selftest --format json"
    assert d["thresholds"]["small_net_nodes"] == 500
