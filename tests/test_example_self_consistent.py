"""Example 12_self_consistent: run.sh runs as shipped, offline, with Xyce and
with ngspice, and its three runs (the open loop, the self-consistent loop and
the RCC reference) match the golden values in expected/ and the hand numbers
of its README (compare.py checks both).

    EMIR_REGEN_GOLDEN=1 pytest tests/test_example_self_consistent.py   # rewrite expected/

Skipped when the simulator is not installed. One simulator process at a time
(run.sh runs them in turn).
"""
import json
import os
import subprocess
import sys

import pytest
from _helpers import ROOT

from emir import site

pytestmark = pytest.mark.example

HERE = os.path.join(ROOT, "examples", "12_self_consistent")
SIMS = [pytest.param(s, marks=pytest.mark.skipif(site.tool(s)[0] is None,
                                                 reason=f"{s} not installed"))
        for s in ("xyce", "ngspice")]


@pytest.mark.parametrize("sim", SIMS)
def test_example_runs_and_matches_its_golden_and_the_hand_numbers(sim, tmp_path):
    env = {**os.environ, "EMIR_EXAMPLE_OUT": str(tmp_path), "EMIR_EXAMPLE_SIM": sim,
           "EMIR_PYTHON": sys.executable}
    p = subprocess.run(["sh", os.path.join(HERE, "run.sh")], capture_output=True, text=True,
                       env=env, cwd="/")
    assert p.returncode == 0, p.stdout[-2000:] + p.stderr[-3000:]
    assert "Screening, not sign-off." in p.stdout and "golden and hand values match" in p.stdout
    if os.environ.get("EMIR_REGEN_GOLDEN"):
        sys.path.insert(0, HERE)
        import compare
        got = {k: compare.subset(json.load(open(tmp_path / f"{k}.json")))
               for k in ("open", "sc", "rcc")}
        gold = os.path.join(HERE, "expected", "summary.json")
        doc = json.load(open(gold))
        doc["runs"] = got
        with open(gold, "w") as f:
            json.dump(doc, f, indent=1)
            f.write("\n")
    # the open loop warned and planned the self-consistent route (the person's call)
    o = json.load(open(tmp_path / "open.json"))
    assert "EMIR-W520" in [n["code"] for n in o["notices"]]
    step = next(s for s in o["next_steps"] if "--self-consistent" in s["command"])
    assert step["needs_human"] is True
    sc = json.load(open(tmp_path / "sc.json"))["nets"][0]["self_consistency"]
    assert sc["simulator"] == sim and sc["converged"]
    ch = [x["droop_change_mV"] for x in sc["passes"]]
    assert ch[0] == pytest.approx(108.0, rel=1e-3) and all(b < a for a, b in zip(ch, ch[1:]))
