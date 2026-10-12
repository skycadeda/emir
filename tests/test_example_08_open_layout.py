"""Example 08 (an open-PDK layout on the magic route): run.sh extracts the
layout with magic, simulates it with Xyce or ngspice and screens it with rcc,
and compare.py checks every result against expected/results.json and rcc branch
by branch against the simulator's own output.
It needs PDK_ROOT with sky130A, a magic that writes resistor geometry and Xyce
or ngspice; without them the script says what it skips and exits 0."""
import json
import os
import shutil
import subprocess
import sys

import pytest

from _helpers import ROOT

pytest.importorskip("klayout.db")
HERE = os.path.join(ROOT, "examples", "08_open_layout")


@pytest.mark.example
@pytest.mark.magic
def test_example_08_runs_and_matches_its_golden_results(tmp_path):
    if shutil.which("bash") is None:
        pytest.skip("run.sh needs bash")
    env = {**os.environ, "EMIR_PYTHON": sys.executable}
    p = subprocess.run(["bash", os.path.join(HERE, "run.sh"), str(tmp_path)], env=env,
                       capture_output=True, text=True, timeout=900)
    assert p.returncode == 0, p.stdout + p.stderr
    if "skipped" in p.stdout:
        pytest.skip(p.stdout.strip().splitlines()[-1])
    assert "expected/results.json: matches" in p.stdout
    md = (tmp_path / "comparison.md").read_text()
    assert "rcc against the simulator's own output" in md
    assert "| op_rcc | vss |" in md and "cc-grid" not in md and "cc_grid" not in md
    # the window: the record's last three 4 ns periods; without --period, and
    # with a period 10 % off, refused with no number
    for n in json.loads((tmp_path / "rcc.json").read_text())["nets"]:
        assert n["window"]["n_periods"] == 3 and n["window"]["period_s"] == 4e-9
    for run, code in (("rcc_no_period", "EMIR-E428"), ("rcc_near_period", "EMIR-E429")):
        doc = json.loads((tmp_path / f"{run}.json").read_text())
        assert doc["schema"] == "emir.error/1" and doc["code"] == code
    assert "rcc_no_period (the same result, no --period): refused, EMIR-E428" in md
    assert "rcc_near_period (the same result, --period 3.6e-9, 10 % off): refused, " \
           "EMIR-E429" in md
    report = (tmp_path / "report.md").read_text()
    assert "Screening, not sign-off." in report and "Mode: rcc" in report
    assert json.loads((tmp_path / "rcc.json").read_text())["nets"][0]["mode"] == "rcc"
    assert not (tmp_path / "extract" / "sink.cc.spice").exists()
    assert os.path.isfile(tmp_path / "html" / "emir_chart.png")


def test_a_skipped_run_says_why_in_its_output_directory(tmp_path):
    """Without a sky130 PDK (every GitHub runner) run.sh stops before any
    tool and exits 0; the reason lands in OUT_DIR as well as on stdout, so a
    caller that reads only OUT_DIR (the examples' shared test) sees it."""
    if shutil.which("bash") is None:
        pytest.skip("run.sh needs bash")
    env = {k: v for k, v in os.environ.items() if k != "PDK_ROOT"}
    out = tmp_path / "out"
    p = subprocess.run(["bash", os.path.join(HERE, "run.sh"), str(out)], env=env,
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "example 08 skipped" in p.stdout
    assert "needs PDK_ROOT" in (out / "skipped.txt").read_text()
