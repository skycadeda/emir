"""Example 08 (an open-PDK layout on the magic route): run.sh extracts the
layout with magic, simulates it with Xyce or ngspice in rcc and cc-grid (open
loop and self-consistent), and compare.py checks every result against
expected/results.json, rcc branch by branch against the simulator's own output.
It needs PDK_ROOT with sky130A, a magic that writes resistor geometry and Xyce
or ngspice; without them the script says what it skips and exits 0."""
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
    report = (tmp_path / "report.md").read_text()
    assert "Screening, not sign-off." in report
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
