"""examples/11_hspice_offline: run.sh reads the synthetic HSPICE ASCII results
(and their CSDF twins) through the HSPICE names sidecars, in rcc and
cc-grid, and its digest matches expected/summary.json; the committed inputs
are what make_results.py writes."""
import filecmp
import json
import os
import subprocess
import sys

import pytest
from _helpers import ROOT

EX = os.path.join(ROOT, "examples", "11_hspice_offline")


@pytest.mark.example
def test_example_11_hspice_offline(tmp_path):
    out = tmp_path / "out"
    env = {**os.environ, "EMIR": f"{sys.executable} -m emir", "PYTHONPATH": ROOT,
           "EMIR_PYTHON": sys.executable}
    p = subprocess.run(["sh", os.path.join(EX, "run.sh"), str(out)], env=env,
                       capture_output=True, text=True, cwd=str(tmp_path))
    assert p.returncode == 0, p.stdout[-3000:] + p.stderr[-3000:]
    assert "golden values match" in p.stdout
    # the report a person reads says the route is not verified on this machine
    assert "NOT verified on this machine" in p.stdout
    lines = json.load(open(out / "save_lines_rcc.json"))["lines"]
    assert lines[1] == ".option probe post=2 post_version=2001"
    assert (out / "rcc.hspice.inc").read_text().startswith("* emir: 16 currents for rcc")
    # the source-current sign was set from KCL, not assumed
    assert "EMIR-I465" in p.stderr


def test_example_11_inputs_are_what_the_generator_writes(tmp_path):
    p = subprocess.run([sys.executable, os.path.join(EX, "make_results.py"), str(tmp_path)],
                       capture_output=True, text=True, env={**os.environ, "PYTHONPATH": ROOT})
    assert p.returncode == 0, p.stderr
    for f in ("rcc.tr0", "cc.tr0", "rcc.csd", "cc.csd"):
        assert filecmp.cmp(tmp_path / f, os.path.join(EX, "inputs", f), shallow=False), f
    head = open(os.path.join(EX, "inputs", "rcc.tr0"), encoding="latin-1").read(400)
    assert "synthetic" in head and "Synopsys" not in head
