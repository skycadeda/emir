"""examples/13_decap_aware: `run.sh` works offline from a clone and matches its
expected/ results; the committed result table is labelled and says what the
docs quote from it. The Xyce half runs only where Xyce is on PATH."""
import csv
import os
import shutil
import subprocess
import sys

import pytest
from _helpers import ROOT

EX = os.path.join(ROOT, "examples", "13_decap_aware")
pytestmark = pytest.mark.example


def test_run_sh_reproduces_the_expected_results(tmp_path):
    env = {**os.environ, "OUT": str(tmp_path), "PYTHON": sys.executable}
    p = subprocess.run(["bash", os.path.join(EX, "run.sh")], env=env, capture_output=True,
                       text=True, timeout=1800)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "52/52 checks hold" in p.stdout
    if shutil.which("Xyce"):
        assert "42/42 checks hold" in p.stdout, p.stdout
        assert (tmp_path / "xyce_n20.md").read_text().startswith(
            "# Decap-aware EM against RCC (Xyce, synthetic, serial)")
    else:
        assert "skipped: Xyce is not on PATH" in p.stdout
    assert p.stdout.rstrip().endswith("run.sh: done")


def test_run_sh_refuses_an_unknown_option():
    p = subprocess.run(["bash", os.path.join(EX, "run.sh"), "--fast"], capture_output=True,
                       text=True)
    assert p.returncode == 2 and "unknown option --fast" in p.stderr


def _rows():
    path = os.path.join(EX, "results", "decap.csv")
    with open(path, encoding="utf-8") as f:
        first = f.readline()
        rows = list(csv.DictReader(x for x in f if not x.startswith("#")))
    return first, {r["case"]: r for r in rows}


def _f(r, k):
    return float(r[k]) if r.get(k) not in (None, "") else None


def test_the_committed_table_is_labelled_and_holds_what_the_docs_quote():
    first, rows = _rows()
    assert "Xyce, synthetic, serial" in first
    assert set(rows) >= {"n20_none", "n20_2ohm", "n40_2ohm", "n40_20ohm", "n70_2ohm",
                         "two_rail"}
    for name, r in rows.items():
        if r["two_rail"] == "True":
            continue
        # avg is the static one in both bases
        assert _f(r, "aware_avg_gov") == pytest.approx(_f(r, "free_avg_gov"), abs=1e-12)
        if int(r["decaps"]):
            # decap-aware at or above RCC on the governing branch, every class and
            # the straps (one resistive rail; the decap model is RCC's own)
            for m in ("rms", "peak"):
                assert _f(r, f"aware_{m}_gov") >= 0, (name, m)
                for c in ("major", "secondary", "minor", "strap"):
                    v = _f(r, f"aware_{m}_{c}_min")
                    assert v is None or v >= -0.01, (name, m, c, v)
                # decap-free gives the straps nothing
                assert _f(r, f"free_{m}_strap_max") == pytest.approx(-1.0, abs=1e-9)
            assert _f(r, "free_rms_gov") > _f(r, "aware_rms_gov")
        # the reference itself: its last period against the one before (0.11 % on the
        # largest mesh without decaps, the floor of what that row can claim)
        assert _f(r, "rcc_periodicity") < 2e-3
        assert _f(r, "avg_identity") < 1e-10
        if r.get("solver_rms_major_min"):
            for m in ("rms", "peak"):
                for c in ("major", "secondary", "minor"):
                    for e in ("min", "max"):
                        v = _f(r, f"solver_{m}_{c}_{e}")
                        assert v is None or abs(v) <= 5e-3, (name, m, c, e, v)
    # two resistive rails: each net solved alone reads the straps' current low
    two = rows["two_rail"]
    assert _f(two, "vdd_aware_rms_strap_min") < 0


def test_the_cost_table_is_what_cost_py_writes():
    """results/cost.md is generated from results/cost.json by cost.py (the
    rows, and the back-to-back pairs whose range the docs quote)."""
    import importlib.util
    import json
    spec = importlib.util.spec_from_file_location("emir_ex13_cost",
                                                  os.path.join(EX, "cost.py"))
    cost = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cost)
    with open(os.path.join(EX, "results", "cost.json"), encoding="utf-8") as f:
        doc = json.load(f)
    with open(os.path.join(EX, "results", "cost.md"), encoding="utf-8") as f:
        assert f.read() == cost.markdown(doc["rows"], doc.get("pairs") or [])
    assert len([p for p in doc.get("pairs", []) if p["case"] == "n200"]) >= 3
