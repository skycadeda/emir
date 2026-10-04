"""examples/06_big_block: `run.sh` works offline from a clone and matches its
expected/ results; the committed result tables are labelled and consistent.
The Xyce half runs only where Xyce is on PATH."""
import csv
import json
import os
import re
import shutil
import subprocess
import sys

import pytest
from _helpers import ROOT

EX = os.path.join(ROOT, "examples", "06_big_block")
pytestmark = pytest.mark.example


def test_run_sh_reproduces_the_expected_results(tmp_path):
    env = {**os.environ, "OUT": str(tmp_path), "PYTHON": sys.executable}
    p = subprocess.run(["bash", os.path.join(EX, "run.sh")], env=env, capture_output=True,
                       text=True, timeout=900)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "16/16 checks hold" in p.stdout
    if shutil.which("Xyce"):
        assert p.stdout.count("checks hold") == 4, p.stdout
        assert "25/25 checks hold" in p.stdout, p.stdout
        assert "17/17 checks hold" in p.stdout, p.stdout
        assert (tmp_path / "sweep_n10.md").read_text().startswith(
            "# RCC against CC + cc-grid (Xyce, synthetic, serial)")
        assert (tmp_path / "screen" / "html" / "emir_chart.png").is_file()
    else:
        assert "skipped: Xyce is not on PATH" in p.stdout
    assert p.stdout.rstrip().endswith("run.sh: done")


def test_the_screen_is_the_run_the_figures_draw():
    """run.sh step 4 and scripts/make_figures.py (the banner, the blog's cover
    and carousel pages 1 and 2) screen the same mesh with the same command, so
    the golden values the blog quotes are the figures' (expected/screen_n10.json)."""
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


def _rows(path):
    with open(path, encoding="utf-8") as f:
        lines = [x for x in f if not x.startswith("#")]
    return list(csv.DictReader(lines))


def test_the_committed_sweep_is_labelled_and_consistent():
    path = os.path.join(EX, "results", "sweep.csv")
    with open(path, encoding="utf-8") as f:
        assert "Xyce, synthetic, serial" in f.readline()
    rows = _rows(path)
    base = [r for r in rows if r["case"] == "k32"]
    assert [int(r["N"]) for r in base] == sorted(int(r["N"]) for r in base)
    assert len(base) >= 5
    # the RCC result grows with the mesh, the CC one does not (the rail is one node)
    assert float(base[-1]["rcc_raw_MB"]) > 20 * float(base[0]["rcc_raw_MB"])
    assert float(base[-1]["cc_raw_MB"]) == pytest.approx(float(base[0]["cc_raw_MB"]), rel=0.01)
    for r in rows:
        if r.get("avg_gov"):
            # below 2 % droop (20 mV on the 1 V rail) the average is within 1 % of RCC;
            # above it the open-loop currents run high (the docs' self-consistency advice)
            if float(r["ir_rcc_mV"]) < 20:
                assert abs(float(r["avg_gov"])) < 0.01
            else:
                assert float(r["avg_gov"]) > 0
            assert float(r["rms_major_min"]) > 0 and float(r["peak_gov"]) > 0
    md = open(os.path.join(EX, "results", "sweep.md"), encoding="utf-8").read()
    assert md.startswith("# RCC against CC + cc-grid (Xyce, synthetic, serial)")


def test_the_committed_stream_benchmark_holds_its_claims():
    rows = _rows(os.path.join(EX, "results", "stream_bench.csv"))
    big = [r for r in rows if r["case"] == "s300_k2000_t501"]
    assert big and int(big[0]["nodes"]) == 92001 and int(big[0]["resistors"]) == 181401
    assert big[0]["auto_method"] == "stream" and float(big[0]["dense_matrix_GiB"]) > 2
    for r in rows:
        for m in ("avg", "rms", "peak"):
            assert float(r[f"rel_diff_{m}"]) <= 1e-10


def test_the_generator_writes_one_description_four_ways(tmp_path):
    """The R-only DSPF, the RC DSPF, the RCC deck and its sidecar name the same
    resistors; the CC deck's sidecar names the DSPF's instance pins."""
    sys.path.insert(0, EX)
    try:
        import mesh
    finally:
        sys.path.remove(EX)
    from emir.dspf import parse
    info = mesh.xyce_case(5, 4, str(tmp_path))
    r, rc = parse(str(tmp_path / "grid_r.dspf")), parse(str(tmp_path / "grid_rc.dspf"))
    names = sorted(x["name"] for x in r.net_resistors("VDD"))
    assert names == sorted(x["name"] for x in rc.net_resistors("VDD"))
    assert len(names) == info["resistors"] and len(rc.net_caps("VDD")) == 5 * 5 - 1
    printed = open(tmp_path / "rcc.cir").read().split(".print tran format=raw file=rcc.raw ")[1]
    assert sorted(t[2:-1] for t in printed.split()[:-1] if t.startswith("I(")) == names
    side = json.load(open(tmp_path / "rcc.raw.names"))["map"]
    assert sorted(v[0][:-2] for v in side.values()) == names
    cc = json.load(open(tmp_path / "cc.raw.names"))["map"]
    assert sorted(v[0] for v in cc.values()) == sorted(r.pin_groups("VDD"))
    again = mesh.xyce_case(5, 4, str(tmp_path / "again"))
    assert again == info
    assert (tmp_path / "grid_r.dspf").read_text() == (tmp_path / "again" / "grid_r.dspf").read_text()


def test_measure_reports_the_childs_own_peak(tmp_path):
    """A child that allocates 200 MiB reports at least that, one that does not
    reports far less, whatever this (test) process holds."""
    sys.path.insert(0, EX)
    try:
        import measure
    finally:
        sys.path.remove(EX)
    big = measure.run([sys.executable, "-c", "b = bytearray(200 << 20); b[::4096] = b'x' * len(b[::4096])"],
                      str(tmp_path / "big.log"))
    small = measure.run([sys.executable, "-c", "pass"], str(tmp_path / "small.log"))
    assert big["exit"] == 0 and small["exit"] == 0
    assert big["peak_rss_MB"] >= 200 and small["peak_rss_MB"] < 100
    assert big["wall_s"] > 0 and "load_1min" in big


@pytest.mark.parametrize("path", ["examples/06_big_block/README.md",
                                  "docs/big-blocks.md",
                                  "agent/playbooks/02-big-block-cc.md"])
def test_the_pages_carry_no_unfilled_placeholders(path):
    text = open(os.path.join(ROOT, path), encoding="utf-8").read()
    # a template placeholder ({{ name }}), not the braces of a JSON excerpt
    assert not re.search(r"\{\{\s*[\w.]+\s*\}\}", text)
    assert "Screening, not sign-off" in text or "screening, not sign-off" in text.lower()
