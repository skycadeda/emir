"""Examples 02_xyce and 03_ngspice: each run.sh runs as shipped, offline, and
its run summaries match the golden ones in expected/ within their tolerance.
The two examples simulate the same circuit with different simulators, so
their goldens must also agree with each other, and with the closed forms in
examples/02_xyce/README.md.

    EMIR_REGEN_GOLDEN=1 pytest tests/test_examples_opensim.py   # rewrite expected/

Skipped when the example's simulator is not installed. One simulator
process at a time (run.sh runs them in turn).
"""
import json
import os
import subprocess
import sys

import pytest
from _helpers import ROOT

from emir import site

pytestmark = pytest.mark.example

CASES = [pytest.param(ex, sim, marks=pytest.mark.skipif(site.tool(sim)[0] is None,
                                                        reason=f"{sim} not installed"))
         for ex, sim in (("02_xyce", "xyce"), ("03_ngspice", "ngspice"))]
INPUTS = ("loadrail.dspf", "models.sp", "cc.sp", "tb_cc.cir", "tb_rcc.cir")
TOL = {"rel": 1e-3, "abs": 1e-6}

#: the closed forms (examples/02_xyce/README.md)
F = (2.0 + 0.2 / 6) / 10.0
HAND = {"Rc3:peak": 2.5 / 2.4, "Rm1:avg": 10.0 * F / 2.4, "ir_drop_mV": 138.0 * F,
        "dynamic_mV": 2.5 * 36.0}


def subset(summary: dict) -> dict:
    """What a golden file pins of a run summary (no paths, no times)."""
    n = summary["nets"][0]
    return {"verdict": summary["verdict"], "counts": summary["counts"], "net": n["net"],
            "mode": n["mode"], "governing_metric": n["governing_metric"],
            "max_ratio": n["max_ratio"], "ir_drop_mV": n["ir_drop_mV"],
            "dynamic_mV": n["dynamic_mV"], "n_violations": n["n_violations"],
            "worst_items": {f"{i['name']}:{i['metric']}": i["ratio"]
                            for i in n["worst_items"]},
            "unsaved_pins": n["unsaved_pins"]}


def close(a, b, where):
    if isinstance(b, dict):
        assert isinstance(a, dict) and set(a) == set(b), (where, a, b)
        for k in b:
            close(a[k], b[k], f"{where}.{k}")
    elif isinstance(b, float):
        assert a == pytest.approx(b, rel=TOL["rel"], abs=TOL["abs"]), where
    else:
        assert a == b, (where, a, b)


@pytest.mark.parametrize("ex, sim", CASES)
def test_example_runs_and_matches_its_golden(ex, sim, tmp_path):
    here = os.path.join(ROOT, "examples", ex)
    env = {**os.environ, "EMIR_EXAMPLE_OUT": str(tmp_path), "EMIR_PYTHON": sys.executable}
    p = subprocess.run(["sh", os.path.join(here, "run.sh")], capture_output=True, text=True,
                       env=env, cwd="/")
    assert p.returncode == 0, p.stdout[-2000:] + p.stderr[-3000:]
    assert "Screening, not sign-off." in p.stdout
    got = {k: subset(json.load(open(tmp_path / f"{k}.json"))) for k in ("rcc", "cc")}
    gold = os.path.join(here, "expected", "summary.json")
    if os.environ.get("EMIR_REGEN_GOLDEN"):
        with open(gold, "w") as f:
            json.dump({"example": ex, "simulator": sim,
                       "made_by": f"examples/{ex}/run.sh (EMIR_REGEN_GOLDEN=1 pytest "
                                  f"tests/test_examples_opensim.py)",
                       "tolerance": TOL, "runs": got}, f, indent=1)
            f.write("\n")
    want = json.load(open(gold))
    assert want["simulator"] == sim
    for k in ("rcc", "cc"):
        close(got[k], want["runs"][k], f"{ex}.{k}")


def test_the_examples_share_their_circuit():
    for f in INPUTS:
        a = open(os.path.join(ROOT, "examples", "02_xyce", f)).read()
        b = open(os.path.join(ROOT, "examples", "03_ngspice", f)).read()
        assert a == b, f


def test_the_goldens_agree_with_each_other_and_the_hand_calculation():
    g = {ex: json.load(open(os.path.join(ROOT, "examples", ex, "expected", "summary.json")))
         for ex in ("02_xyce", "03_ngspice")}
    x, n = g["02_xyce"]["runs"], g["03_ngspice"]["runs"]
    for k in ("rcc", "cc"):
        close(n[k], x[k], f"ngspice vs xyce {k}")
        r = x[k]
        assert r["worst_items"]["Rc3:peak"] == pytest.approx(HAND["Rc3:peak"], rel=1e-6)
        assert r["worst_items"]["Rm1:avg"] == pytest.approx(HAND["Rm1:avg"], rel=1e-3)
        assert r["ir_drop_mV"] == pytest.approx(HAND["ir_drop_mV"], rel=1e-3)
    assert x["rcc"]["dynamic_mV"] == pytest.approx(HAND["dynamic_mV"], rel=1e-3)
