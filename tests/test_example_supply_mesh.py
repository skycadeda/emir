"""examples/06_supply_mesh: `run.sh` works offline from a clone and matches its
expected/ results; the mesh generator writes one description. The Xyce half
runs only where Xyce is on PATH."""
import json
import os
import re
import shutil
import subprocess
import sys

import pytest
from _helpers import ROOT

EX = os.path.join(ROOT, "examples", "06_supply_mesh")
pytestmark = pytest.mark.example


def test_run_sh_reproduces_the_expected_results(tmp_path):
    env = {**os.environ, "OUT": str(tmp_path), "PYTHON": sys.executable}
    p = subprocess.run(["bash", os.path.join(EX, "run.sh")], env=env, capture_output=True,
                       text=True, timeout=900)
    assert p.returncode == 0, p.stdout + p.stderr
    if shutil.which("Xyce"):
        assert p.stdout.count("checks hold") == 2, p.stdout
        assert p.stdout.count("17/17 checks hold") == 1, p.stdout
        assert p.stdout.count("4/4 checks hold") == 1, p.stdout
        assert (tmp_path / "screen" / "html" / "emir_chart.png").is_file()
        log = (tmp_path / "screen" / "screen.log").read_text()
        assert "currents       : RCC branch currents read directly" in log
        _the_window_is_the_last_periods_or_the_run_is_refused(tmp_path / "screen")
    else:
        assert "skipped: Xyce is not on PATH" in p.stdout
    assert p.stdout.rstrip().endswith("run.sh: done")


def _the_window_is_the_last_periods_or_the_run_is_refused(screen):
    """With --period 1e-9 the window is the record's last three whole periods
    (1 to 4 ns); without --period the run is refused with no number
    (EMIR-E428). (--period 125p, the feed's sub-period, is refused too:
    tests/test_rcc_exact.py.)"""
    w = json.loads((screen / "screen.json").read_text())["nets"][0]["window"]
    assert w["period_s"] == 1e-9 and w["n_periods"] == 3
    assert abs(w["t0_s"] - 1e-9) < 1e-18 and abs(w["t1_s"] - 4e-9) < 1e-18
    assert w["change"] <= 0.02 and w["branches"] > 100
    no = json.loads((screen / "screen_no_period.json").read_text())
    assert no["schema"] == "emir.error/1" and no["code"] == "EMIR-E428"
    assert "worst_items" not in json.dumps(no)


def test_the_screen_is_the_run_the_figures_draw():
    """run.sh and scripts/make_figures.py (the banner, the blog's cover and
    carousel pages 1 and 2) screen the same mesh with the same command, so the
    golden values the blog quotes are the figures' (expected/screen_n10.json)."""
    import importlib.util
    import shlex
    pytest.importorskip("matplotlib")
    pytest.importorskip("PIL")
    spec = importlib.util.spec_from_file_location(
        "make_figures", os.path.join(ROOT, "scripts", "make_figures.py"))
    mf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mf)
    text = open(os.path.join(EX, "run.sh"), encoding="utf-8").read()
    m = re.search(r'"\$PY" mesh\.py xyce --n (\d+) --k (\d+) --out "\$OUT/screen"', text)
    assert m and (int(m.group(1)), int(m.group(2))) == (mf.SCREEN_N, mf.SCREEN_K)
    m = re.search(r'"\$PY" -m emir run (.*?) --summary-out', text, re.S)
    argv = shlex.split(m.group(1).replace("\\\n", " "))
    assert argv[argv.index("--process") + 1] == "$HERE/process_screen.yaml"
    argv[argv.index("--process") + 1] = mf.SCREEN_PROCESS
    assert argv == mf.screen_args()
    assert os.path.isfile(mf.SCREEN_PROCESS)


def test_run_sh_refuses_an_unknown_option():
    p = subprocess.run(["bash", os.path.join(EX, "run.sh"), "--fast"], capture_output=True,
                       text=True)
    assert p.returncode == 2 and "unknown option --fast" in p.stderr


def test_compare_fails_on_a_mismatch(tmp_path):
    gold = tmp_path / "gold.json"
    gold.write_text(json.dumps({"label": "t", "checks": [
        {"path": "a.0.x", "value": 1.0, "rel": 1e-3}, {"path": "b", "max": 1e-10},
        {"path": "c", "equals": "dense"}, {"path": "d", "min": 0.0}]}))
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"a": [{"x": 1.0005}], "b": 0.0, "c": "dense", "d": 0.1}))
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"a": [{"x": 1.1}], "b": 1e-6, "c": "stream", "d": None}))
    run = lambda f: subprocess.run([sys.executable, os.path.join(EX, "compare.py"),  # noqa: E731
                                    str(gold), str(f)], capture_output=True, text=True)
    assert run(good).returncode == 0
    p = run(bad)
    assert p.returncode == 1 and "0/4 checks hold" in p.stdout
    for needle in ("a.0.x: 1.1 is not 1", "b: 1e-06 > 1e-10", "c: 'stream' != 'dense'",
                   "d: null"):
        assert needle in p.stderr, needle


def test_the_generator_writes_one_description(tmp_path):
    """The RC DSPF, the deck and its sidecar name the same resistors, and
    the generator is deterministic."""
    sys.path.insert(0, EX)
    try:
        import mesh
    finally:
        sys.path.remove(EX)
    from emir.dspf import parse
    info = mesh.xyce_case(5, 4, str(tmp_path))
    rc = parse(str(tmp_path / "grid_rc.dspf"))
    names = sorted(x["name"] for x in rc.net_resistors("VDD"))
    caps = [c for c in rc.cap if "VDD" in (rc.node_net.get(c["n1"]), rc.node_net.get(c["n2"]))]
    assert len(names) == info["resistors"] and len(caps) == 5 * 5 - 1
    printed = open(tmp_path / "rcc.cir").read().split(".print tran format=raw file=rcc.raw ")[1]
    assert sorted(t[2:-1] for t in printed.split()[:-1] if t.startswith("I(")) == names
    side = json.load(open(tmp_path / "rcc.raw.names"))["map"]
    assert sorted(v[0][:-2] for v in side.values()) == names
    assert not (tmp_path / "cc.cir").exists() and not (tmp_path / "grid_r.dspf").exists()
    again = mesh.xyce_case(5, 4, str(tmp_path / "again"))
    assert again == info
    assert (tmp_path / "grid_rc.dspf").read_text() == \
        (tmp_path / "again" / "grid_rc.dspf").read_text()


@pytest.mark.parametrize("path", ["examples/06_supply_mesh/README.md",
                                  "agent/playbooks/01-screen-a-block.md"])
def test_the_pages_carry_no_unfilled_placeholders(path):
    text = open(os.path.join(ROOT, path), encoding="utf-8").read()
    # a template placeholder ({{ name }}), not the braces of a JSON excerpt
    assert not re.search(r"\{\{\s*[\w.]+\s*\}\}", text)
    assert "Screening, not sign-off" in text or "screening, not sign-off" in text.lower()
