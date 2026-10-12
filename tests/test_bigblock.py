"""Big blocks with rcc, the one method of a simulated run: every branch
current of the RC netlist is read, so every metric is checked (avg, rms and
peak EM, static and dynamic IR) and the report relays the dynamic droop;
`emir next` on a block of ADC-top or PLL-top size plans the RC simulation as
a long run and says why; the options of the removed methods are usage errors.

Every input is synthetic and every number invented; no simulator is needed.
"""
import json

import jsonschema
import pytest
from _helpers import DSPF, PERIOD, PROC, emir, rcc_result

from emir import plan as P
from emir import schemas
from emir.dspf import analyse, build_parser
from emir.summary import summary_document


def run_doc(*argv):
    """`emir run ARGV` in process: (RunResult, the emir.run/1 document)."""
    argv = [str(x) for x in argv]
    a = build_parser().parse_args(argv)
    a.argv = argv
    a.command = "emir run " + " ".join(a.argv)
    res = analyse(a)
    return res, summary_document(res)


# ------------------------------------------------------------ rcc


def test_rcc_checks_every_metric_and_its_dynamic_ir(tmp_path):
    """rcc measured dynamic IR and checks avg, rms and peak; nothing is left
    out."""
    _, doc = run_doc(DSPF, "--process", PROC, "--net", "VDD", "--cc-sim",
                     rcc_result(tmp_path), "--period", PERIOD)
    net = doc["nets"][0]
    assert net["mode"] == "rcc" and net["dynamic"]["worst_mV"] == net["dynamic_mV"]
    assert "simulated" in net["dynamic"]["source"] and net["dynamic"]["node"]
    assert net["metrics"] == ["avg", "rms", "peak"] and doc["metrics"] == net["metrics"]
    for gone in ("not_checked_metrics", "guard", "droop_fraction", "unsaved_pins"):
        assert gone not in net
    assert "cc_grid" not in doc["not_checked"] and "verdict_scope" not in doc
    jsonschema.validate(json.loads(json.dumps(doc, default=float)), schemas.get("run"))


def test_mode_rcc_is_accepted_and_changes_nothing(tmp_path):
    """`--mode rcc` is accepted (the one method) and may be left out."""
    rcc = rcc_result(tmp_path)
    _, a = run_doc(DSPF, "--process", PROC, "--net", "VDD", "--cc-sim", rcc,
                   "--period", PERIOD)
    _, b = run_doc(DSPF, "--process", PROC, "--net", "VDD", "--cc-sim", rcc,
                   "--period", PERIOD, "--mode", "rcc")
    assert a["nets"][0]["max_ratio"] == b["nets"][0]["max_ratio"]
    assert a["nets"][0]["ir_drop_mV"] == b["nets"][0]["ir_drop_mV"]


def test_the_report_relays_rcc_s_dynamic_droop(tmp_path):
    from emir.report import build, markdown
    _, doc = run_doc(DSPF, "--process", PROC, "--net", "VDD", "--cc-sim",
                     rcc_result(tmp_path), "--period", PERIOD, "--power-nets", "VDD")
    r = build(json.loads(json.dumps(doc, default=float)), "summary.json")
    assert r["ir_by_power_net"][0]["dynamic_mV"] == pytest.approx(
        doc["nets"][0]["dynamic_mV"])
    md = markdown(r)
    assert "(from the simulated branch currents)" in md and "cc-grid" not in md


@pytest.mark.parametrize("opt", [["--self-consistent"], ["--dynamic"],
                                 ["--em-basis", "worst"], ["--scalars"],
                                 ["--max-memory", "4G"], ["--superposition", "dense"],
                                 ["--align", "sync"], ["--split-stub"], ["--vdd", "1.0"],
                                 ["--testbench", "tb.cir"], ["--guard-sim", "g.raw"],
                                 ["--abstract", "a.json@X1"], ["--taps-out", "t.csv"],
                                 ["--save-list"], ["--rcc-nodes", "5"]])
def test_the_removed_options_are_usage_errors(tmp_path, opt):
    p = emir("run", DSPF, "--process", PROC, "--cc-sim", rcc_result(tmp_path), *opt,
             "--format", "json")
    assert p.returncode == 2, p.stdout + p.stderr
    d = json.loads(p.stdout)
    assert d["code"] == "EMIR-E507"
    assert d["details"]["did_you_mean"][opt[0]].startswith("not in 1.1: a simulated run is "
                                                           "rcc")


@pytest.mark.parametrize("mode", ["cc-grid", "cc", "auto"])
def test_any_mode_but_rcc_is_a_usage_error(tmp_path, mode):
    p = emir("run", DSPF, "--process", PROC, "--cc-sim", rcc_result(tmp_path),
             "--mode", mode, "--format", "json")
    assert p.returncode == 2, p.stdout + p.stderr
    d = json.loads(p.stdout)
    assert d["code"] == "EMIR-E507" and "--mode rcc, or leave --mode out" in d["message"]


# ------------------------------------------------------------ emir next


@pytest.fixture
def tools(monkeypatch):
    from emir import site
    monkeypatch.setattr(site, "tool", lambda name, explicit=None: (None, "$PATH"))


def plan_for(**kw):
    f = P.Facts(have_xyce=True, **kw)
    return P.build(f)


def test_a_big_block_plans_rcc_as_a_long_run(tools, monkeypatch):
    monkeypatch.setitem(P.THRESHOLDS, "big_block_nodes", 10)
    plan = plan_for(dspf=DSPF, process=PROC, net="VDD")
    adv = " | ".join(plan.advice)
    assert "ADC-top / PLL-top size: an RC transient with every branch current saved" in adv
    assert "cc-grid" not in adv and "C-only" not in adv
    assert plan.mode["chosen"] == "rcc"
    sim = next(s for s in plan.steps if s["id"] == "simulate")
    assert sim["long_run"] and sim["needs_human"]
    assert plan.advice[-1].startswith("never invent process limits")


def test_a_small_block_gets_no_big_block_note(tools):
    plan = plan_for(dspf=DSPF, process=PROC, net="VDD")
    assert not any(a.startswith("ADC-top / PLL-top size") for a in plan.advice)
    assert plan.mode["chosen"] == "rcc"


def test_next_json_validates(tools, monkeypatch):
    import io

    from emir.output import Out
    monkeypatch.setitem(P.THRESHOLDS, "big_block_nodes", 10)
    buf = io.StringIO()
    assert P.main(["--dspf", DSPF, "--process", PROC, "--net", "VDD", "--have-xyce",
                   "--format", "json"], sink=Out("json", buf)) == 0
    doc = json.loads(buf.getvalue())
    jsonschema.validate(doc, schemas.get("next"))
    assert doc["facts"]["size"]["class"] == "big" and doc["mode"]["chosen"] == "rcc"
    assert not any(s["id"] == "choose-mode" for s in doc["steps"])
