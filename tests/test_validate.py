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


def test_suite_has_thirteen_core_cases():
    """Sixteen until the three GDS mesh cases left with `emir mesh`."""
    assert len(V.CASES) == 13
    assert {c[2] for c in V.CASES} == {"core"}


def test_cli_quick(tmp_path):
    """`emir validate --quick --out D` runs, passes and keeps its files."""
    p = subprocess.run([sys.executable, "-m", "emir", "validate", "--quick",
                        "--out", str(tmp_path / "v")], cwd=ROOT,
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "VALIDATION CASES PASS" in p.stdout
    assert "--quick" in p.stdout                     # the slow cases are named as skipped
    assert (tmp_path / "v").is_dir()


def test_the_preflight_playbook_says_what_quick_runs():
    """Playbook 00 said `emir validate --quick` runs "all 16" cases with the
    dev extra; --quick leaves out the slow transient ones."""
    fast = sum(1 for c in V.CASES if not c[3])
    slow = len(V.CASES) - fast
    with open(os.path.join(ROOT, "agent", "playbooks", "00-preflight.md"),
              encoding="utf-8") as f:
        text = " ".join(f.read().split())
    assert (f"the {fast} fast analytic validation cases of the {len(V.CASES)} (it leaves "
            f"out the {slow} slow transient ones, which `emir validate` runs)") in text
    assert f"(all {len(V.CASES)} with" not in text
