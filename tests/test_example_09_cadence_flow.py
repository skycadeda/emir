"""examples/09_cadence_flow: run.sh works offline from a clone and its digest
matches expected/summary.json (exact, except the numbers its `_tolerances`
names)."""
import json
import os
import subprocess
import sys

import pytest
from _helpers import ROOT

EX = os.path.join(ROOT, "examples", "09_cadence_flow")


def compare(got, want, tol, path=""):
    if isinstance(want, dict):
        assert isinstance(got, dict) and set(got) == set(want), (path, got, want)
        for k in want:
            compare(got[k], want[k], tol, f"{path}.{k}" if path else k)
    elif isinstance(want, float) and path in tol:
        assert got is not None and abs(got - want) <= tol[path], (path, got, want)
    else:
        assert got == want, (path, got, want)


@pytest.mark.example
def test_example_09_cadence_flow(tmp_path):
    out = tmp_path / "out"
    # no Cadence tool on $PATH here (and none needed): the plans only
    env = {**os.environ, "EMIR": f"{sys.executable} -m emir", "PYTHONPATH": ROOT}
    p = subprocess.run(["sh", os.path.join(EX, "run.sh"), str(out)], env=env,
                       capture_output=True, text=True, cwd=str(tmp_path))
    assert p.returncode == 0, p.stdout + p.stderr
    with open(os.path.join(EX, "expected", "summary.json"), encoding="utf-8") as f:
        want = json.load(f)
    tol = want.pop("_tolerances")
    want.pop("_about")
    with open(out / "summary.json", encoding="utf-8") as f:
        compare(json.load(f), want, tol)
    assert (out / "tb" / "emir_save.scs").read_text().startswith("save XDUT.Rm1:1 ")
    assert not (out / "work").exists()               # a dry run changes nothing


@pytest.mark.example
def test_example_09_never_deletes_a_directory_it_did_not_write(tmp_path):
    """run.sh began with `rm -rf "$OUT_DIR"`: `run.sh ~/work` wiped it."""
    out = tmp_path / "mine"
    out.mkdir()
    (out / "keep").write_text("the person's file\n")
    env = {**os.environ, "EMIR": f"{sys.executable} -m emir", "PYTHONPATH": ROOT}
    p = subprocess.run(["sh", os.path.join(EX, "run.sh"), str(out)], env=env,
                       capture_output=True, text=True, cwd=str(tmp_path))
    assert p.returncode == 2 and "did not write" in p.stderr
    assert (out / "keep").read_text() == "the person's file\n"
    # its own directory, run again: fine, and only its files are replaced
    fresh = tmp_path / "fresh"
    for _ in range(2):
        p = subprocess.run(["sh", os.path.join(EX, "run.sh"), str(fresh)], env=env,
                           capture_output=True, text=True, cwd=str(tmp_path))
        assert p.returncode == 0, p.stderr
    assert (fresh / ".emir-example-09").exists() and (fresh / "summary.json").exists()
