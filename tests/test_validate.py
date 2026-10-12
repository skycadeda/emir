"""The analytic validation suite (`emir validate`) as pytest cases.

Marked `validate`: `pytest -m validate` runs only these, `-m "not validate"`
leaves them out. Every case needs only numpy, scipy and PyYAML.
"""
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, ROOT)

from emir import validate as V  # noqa: E402

pytestmark = pytest.mark.validate


@pytest.mark.parametrize("name,fn", [(n, f) for n, f, _k, _s in V.CASES],
                         ids=[n for n, *_ in V.CASES])
def test_case(name, fn, tmp_path):
    ok, msg = V.run_case(name, fn, str(tmp_path))
    assert ok, msg or f"{name}: result differs from the closed form"


def test_suite_has_six_core_cases():
    """Sixteen until the three GDS mesh cases left with `emir mesh`; thirteen
    until the six cases of the removed self-consistent loop, cc-grid dynamic
    IR and decap-aware EM left with them; seven until the PSS and CC ingest
    cases left with cc-grid (1.1) and an rcc ingest case took their place.
    None is slow."""
    assert len(V.CASES) == 6
    assert {c[2] for c in V.CASES} == {"core"} and not any(c[3] for c in V.CASES)


def test_cli_quick(tmp_path):
    """`emir validate --quick --out D` runs, passes and keeps its files."""
    p = subprocess.run([sys.executable, "-m", "emir", "validate", "--quick",
                        "--out", str(tmp_path / "v")], cwd=ROOT,
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "ALL 6 VALIDATION CASES PASS" in p.stdout     # none is slow: --quick runs all
    assert (tmp_path / "v").is_dir()


def test_the_preflight_playbook_says_what_quick_runs():
    """Playbook 00 says how many cases `emir validate --quick` runs."""
    with open(os.path.join(ROOT, "agent", "playbooks", "00-preflight.md"),
              encoding="utf-8") as f:
        text = " ".join(f.read().split())
    assert f"the {len(V.CASES)} analytic validation cases (none is slow" in text
