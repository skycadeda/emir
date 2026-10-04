"""examples/05_chip_level end to end: `run.sh` from a clean directory, its
comparison with expected/, and the claims its README makes -- the
hierarchical result equals a flat run of the same network on every top-level
branch (avg, rms, peak) to 1e-9, and the hand numbers."""
import json
import os
import subprocess
import sys

import pytest
from _helpers import ROOT

pytestmark = pytest.mark.example

EXAMPLE = os.path.join(ROOT, "examples", "05_chip_level")


@pytest.fixture(scope="module")
def out(tmp_path_factory):
    d = str(tmp_path_factory.mktemp("chip_level"))
    env = {**os.environ, "PYTHON": sys.executable, "EMIR": f"{sys.executable} -m emir",
           "PYTHONPATH": ROOT}
    p = subprocess.run(["bash", os.path.join(EXAMPLE, "run.sh"), d], capture_output=True,
                       text=True, env=env, cwd=d)
    assert p.returncode == 0, p.stdout[-3000:] + p.stderr[-3000:]
    assert "ALL CHECKS PASSED" in p.stdout
    assert "compare:" in p.stdout and "differ" not in p.stdout
    return d


def load(out, name):
    with open(os.path.join(out, name), encoding="utf-8") as f:
        return json.load(f)


def test_every_check_of_the_example_passes(out):
    m = load(out, "measured.json")
    assert m["passed"] and len(m["checks"]) >= 25
    assert all(c["ok"] for c in m["checks"])


def test_hierarchical_equals_flat(out):
    m = load(out, "measured.json")
    assert m["top_vs_flat_max_rel"] <= 1e-9
    assert max(m["annotated_vs_flat_max_rel"].values()) <= 1e-9


def test_the_hand_numbers(out):
    top = load(out, "top.summary.json")
    pins = {f"{p['instance']}:{p['port']}": p["V_avg_mV"]
            for p in top["hierarchy"]["nets"][0]["pins"]}
    assert pins == pytest.approx({"XADC:VDDA": -4.0, "XPLL:VDDP": -92 / 27,
                                  "XPLL:VDDP:E": -98 / 27}, rel=1e-9)
    adc, pll = load(out, "adc.summary.json"), load(out, "pll.summary.json")
    assert adc["nets"][0]["ports"][0]["i_avg"] == pytest.approx(-2e-3, rel=1e-9)
    assert [p["i_avg"] for p in pll["nets"][0]["ports"]] == pytest.approx(
        [-4e-3 / 3, -2e-3 / 3], rel=1e-9)
    assert pll["nets"][0]["y_ports"][0][1] == pytest.approx(-1 / 3, rel=1e-9)
    m = load(out, "measured.json")
    assert m["pll_Rm1"]["under_read"] == pytest.approx(1 - (4 / 3) / (38 / 27), rel=1e-9)
    assert (m["pll_Rm1"]["verdict_short"], m["pll_Rm1"]["verdict_annotated"]) == ("WARN", "FAIL")
    assert m["budget_mV"]["XADC"]["total"] == pytest.approx(11.5, rel=1e-9)
    assert m["self_consistency"]["pass0_over_read"] == pytest.approx(0.03, rel=1e-9)


def test_the_report_relays_the_hierarchy(out):
    with open(os.path.join(out, "report.md"), encoding="utf-8") as f:
        md = f.read()
    assert "## Hierarchy (blocks black-boxed)" in md and "Screening, not sign-off." in md
    assert "Pass-through inside XPLL" in md
