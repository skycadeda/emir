"""examples/10_mcp_agent: run.sh works offline from a clone and its results
match expected/ (and the comparison does catch a difference)."""
import json
import os
import shutil
import subprocess
import sys

import pytest
from _helpers import ROOT

EX = os.path.join(ROOT, "examples", "10_mcp_agent")

pytestmark = pytest.mark.example


def _has_sdk():
    from importlib import metadata
    try:
        metadata.version("mcp")
    except metadata.PackageNotFoundError:
        return False
    return True


@pytest.mark.skipif(not _has_sdk(), reason="the mcp package is not installed")
def test_run_sh_matches_expected(tmp_path):
    out = tmp_path / "out"
    p = subprocess.run(["sh", os.path.join(EX, "run.sh"), str(out)], capture_output=True,
                       text=True, cwd=str(tmp_path), timeout=600,
                       env={**os.environ, "EMIR_PYTHON": sys.executable})
    assert p.returncode == 0, p.stdout + p.stderr[-3000:]
    assert "matches expected/" in p.stdout
    report = (out / "report.md").read_text()
    assert "Screening, not sign-off." in report and "**Verdict: FAIL**" in report
    assert (out / "emir_out" / "summary.json").is_file()
    # the comparison is not a rubber stamp
    t = json.loads((out / "transcript.json").read_text())
    run = next(r for r in t if r.get("schema") == "emir.run/1")
    run["nets"][0]["ir_drop_mV"] *= 1.01
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "transcript.json").write_text(json.dumps(t))
    p = subprocess.run([sys.executable, os.path.join(EX, "compare.py"), str(bad),
                        os.path.join(EX, "expected")], capture_output=True, text=True)
    assert p.returncode == 1 and "ir_drop_mV" in p.stderr


def test_expected_numbers_are_the_samples_designed_values():
    """The golden numbers are the sample's hand-checked ones (`emir
    selftest` expects the same): worst ratio 5/3, IR drop 54 mV."""
    t = json.load(open(os.path.join(EX, "expected", "transcript.json"), encoding="utf-8"))
    run = next(r for r in t if r.get("schema") == "emir.run/1")
    assert run["worst_net"] == {"net": "VDD", "ratio": 1.6666666666666667}
    assert run["nets"][0]["ir_drop_mV"] == 54.0 and run["nets"][0]["n_violations"] == 10
    assert t[-1]["code"] == "EMIR-E590"
    assert shutil.which("sh")
