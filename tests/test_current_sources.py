"""A run takes its currents from one source. Given --cc-sim and --currents
together, rcc ran on the simulation and --currents was ignored, while the
summary and the report said the currents were DC (`current_source.kind`
dc) and listed, as not checked, the transients the run had in fact read."""
import json

from _helpers import CUR, DSPF, PROC, emir, rcc_result

from emir import plan as P


def test_check_refuses_both(tmp_path):
    p = emir("check", DSPF, "--process", PROC, "--net", "VDD", "--cc-sim",
             rcc_result(tmp_path), "--currents", json.dumps(CUR), "--format", "json")
    assert p.returncode == 2
    doc = json.loads(p.stdout)
    assert "EMIR-E514" in {f["code"] for f in doc["findings"]}


def test_run_refuses_both_even_when_forced(tmp_path):
    for extra in ([], ["--force"], ["--no-check"]):
        p = emir("run", DSPF, "--process", PROC, "--net", "VDD", "--cc-sim",
                 rcc_result(tmp_path), "--currents", json.dumps(CUR), "--period", "1e-9",
                 "--format", "json", *extra)
        assert p.returncode == 2, extra
        assert json.loads(p.stdout)["code"] == "EMIR-E514", extra


def test_emir_next_asks_which(tmp_path):
    plan = P.build(P.Facts(dspf=DSPF, process=PROC, cc_sim=rcc_result(tmp_path),
                           currents=json.dumps(CUR), net="VDD", period=1e-9))
    assert any("one source of currents" in m["what"] for m in plan.missing)
    assert not {"check", "run", "report"} & {s["id"] for s in plan.steps}
