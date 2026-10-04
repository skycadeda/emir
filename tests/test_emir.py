"""Regression tests for the shipped package. Run: python -m pytest tests/

Everything here is offline and uses the sample rail; the numbers are the
ones emir/data/sample/rail.dspf was designed to produce.
"""
import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, ROOT)

from emir.check import check_inputs, check_process, Report  # noqa: E402
from emir.dspf import build_parser, parse, run  # noqa: E402
from emir.selftest import SAMPLE_CURRENTS, sample_paths  # noqa: E402

DSPF, PROC = sample_paths()                # emir/data/sample, as installed
CUR = SAMPLE_CURRENTS


def _levels(rep, area=None):
    return [(f.level, f.message) for f in rep.findings if area in (None, f.area)]


# ------------------------------------------------------------------ checker


def test_sample_inputs_pass():
    rep = check_inputs(DSPF, process=PROC, nets=["VDD"], currents=CUR)
    assert rep.passed, rep.table()
    assert any("ILLUSTRATIVE" in m for _, m in _levels(rep, "process"))


def test_missing_net_is_fail_with_hint():
    rep = check_inputs(DSPF, process=PROC, nets=["vdd"], currents=CUR)
    fails = [m for lvl, m in _levels(rep, "nets") if lvl == "FAIL"]
    assert fails and "not in the DSPF" in fails[0]
    hints = [f.fix for f in rep.findings if f.area == "nets" and f.level == "FAIL"]
    assert "'VDD'" in hints[0]                 # did-you-mean


def test_unbalanced_and_unknown_currents():
    rep = check_inputs(DSPF, process=PROC, nets=["VDD"],
                       currents='{"VDD": 0.004, "MP9:s": -0.001}')
    msgs = " | ".join(m for lvl, m in _levels(rep, "currents") if lvl == "FAIL")
    assert "not on net" in msgs and "do not balance" in msgs


def test_bad_json_currents():
    rep = check_inputs(DSPF, process=PROC, nets=["VDD"], currents="{VDD: 1}")
    assert not rep.passed
    assert any("not a JSON object" in m for _, m in _levels(rep, "currents"))


def test_missing_files():
    rep = check_inputs("/nonexistent.dspf")
    assert not rep.passed and any("not found" in m for _, m in _levels(rep, "files"))
    rep = check_inputs(DSPF, process="/nonexistent.yaml", nets=["VDD"], currents=CUR)
    assert not rep.passed


def test_process_schema(tmp_path):
    bad = tmp_path / "p.yaml"
    bad.write_text("layers:\n  Metal1:\n    sheet_res: -1\n  M2: 3\nvias:\n  V1:\n    gds: [1]\n"
                   "warn_fraction: 1.5\n")
    rep = Report()
    assert check_process(str(bad), rep) is None
    msgs = " | ".join(m for lvl, m in _levels(rep, "process") if lvl == "FAIL")
    for needle in ("non-negative", "not a mapping", "must be [layer, datatype]",
                   "missing `res`", "warn_fraction"):
        assert needle in msgs, needle


def test_unmapped_layer_is_warned(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text("em_tref: 100.0\nlayers:\n  Metal1:\n    gds: [101, 0]\n    sheet_res: 0.125\n"
                 "    jmax_avg: 3.2\n")
    rep = check_inputs(DSPF, process=str(p), nets=["VDD"], currents=CUR)
    warns = [m for lvl, m in _levels(rep, "layers") if lvl == "WARN"]
    assert warns and "NOT checked" in warns[0] and "cont" in warns[0].lower()


def test_self_consistent_option_rules():
    rep = check_inputs(DSPF, process=PROC, nets=None, currents=CUR, self_consistent=True)
    fails = [m for lvl, m in _levels(rep, "options") if lvl == "FAIL"]
    assert any("--self-consistent needs --net" in m for m in fails)
    assert any("--vdd" in m for m in fails)
    assert any("--netlist" in m for m in fails)


def test_sim_result_missing_and_binary(tmp_path):
    rep = check_inputs(DSPF, process=PROC, nets=["VDD"], cc_sim=str(tmp_path / "none.raw"))
    assert any("not found" in m for lvl, m in _levels(rep, "files") if lvl == "FAIL")
    b = tmp_path / "bin.tran"
    b.write_bytes(b"\x00\x00PSFversion\x00" + b"\x00" * 64)
    rep = check_inputs(DSPF, process=PROC, nets=["VDD"], cc_sim=str(b))
    assert any("binary PSF" in m for lvl, m in _levels(rep, "sim") if lvl == "FAIL")


def test_report_json_roundtrip():
    rep = check_inputs(DSPF, process=PROC, nets=["VDD"], currents=CUR)
    d = json.loads(rep.to_json())
    assert d["passed"] is True and d["context"]["design"] == "railblock"
    assert {f["level"] for f in d["findings"]} <= {"FAIL", "WARN", "INFO", "OK"}


# ------------------------------------------------------------------ analysis


@pytest.fixture(scope="module")
def solved(tmp_path_factory):
    out = tmp_path_factory.mktemp("run")
    a = build_parser().parse_args(
        [DSPF, "--net", "VDD", "--process", PROC, "--currents", CUR,
         "--viewer", str(out / "viewer"), "--lib", "L",
         "--json", str(out / "summary.json")])
    return run(a), out


def test_numbers(solved):
    r, _ = solved
    v = r["VDD"]
    # hand calculation: emir/selftest.py, step 3
    assert abs(v["max_ratio"]["all"] - 4.0 / 2.4) < 1e-6
    assert abs(v["max_ratio"]["via"] - 1.0 / 0.75) < 1e-6
    assert abs(v["ir_drop"] * 1e3 - 54.0) < 1e-3
    assert len(v["clusters"]) == 10
    assert v["worst"]["metric"] == "avg" and v["worst"]["layer"] == "Metal1"
    assert [c["name"] for c in v["clusters"] if c["kind"] == "via"] == ["Rc3"]


def test_summary_and_viewer_files(solved):
    _, out = solved
    s = json.load(open(out / "summary.json"))
    assert s["nets"][0]["verdict"] == "FAIL" and s["nets"][0]["mode"] == "dc"
    for f in ("emir_data.il", "emir_explorer.il", "emir_viz.json", "emir_em_bar.png"):
        assert (out / "viewer" / f).exists()
    viz = json.load(open(out / "viewer" / "emir_viz.json"))
    assert len(viz["em"]) == 16
    assert all(c["hint"] == "" for c in viz["em"] if c["ratio"] < 1.0)
    data = (out / "viewer" / "emir_data.il").read_text()
    assert 'emirMarkLayer = "' in data and 'emirMarkPurpose = "' in data


def test_run_refuses_bad_inputs():
    from emir.errors import EmirError
    a = build_parser().parse_args([DSPF, "--net", "NOPE", "--process", PROC, "--currents", CUR])
    with pytest.raises(EmirError) as e:
        run(a)
    assert e.value.code == "EMIR-E501" and e.value.exit_code == 2
    assert "EMIR-E204" in e.value.details["causes"]


def test_dspf_layers_override(tmp_path):
    from emir.dspf import map_layers
    from emir.engine import Process
    import yaml
    d = yaml.safe_load(open(PROC))
    d["dspf_layers"] = {"cont": "Metal1"}
    proc = Process(d)
    lm = map_layers(parse(DSPF), proc)
    assert lm["cont"] == ("layer", "Metal1")


# ------------------------------------------------------------------ CLI


def _emir(*args):
    return subprocess.run([sys.executable, "-m", "emir", *args], cwd=ROOT,
                          capture_output=True, text=True)


def test_cli_check_exit_codes():
    assert _emir("check", DSPF, "--process", PROC, "--net", "VDD", "--currents", CUR).returncode == 0
    assert _emir("check", DSPF, "--process", PROC, "--net", "X", "--currents", CUR).returncode == 2


def test_cli_argfile(tmp_path):
    af = tmp_path / "args.txt"
    af.write_text("\n".join([DSPF, "--process", PROC, "--net", "VDD", "--currents", CUR]) + "\n")
    p = _emir("check", f"@{af}")
    assert p.returncode == 0, p.stdout + p.stderr
    assert "INPUTS OK" in p.stdout


def test_cli_nets_pins_savelines():
    assert "VDD" in _emir("nets", DSPF).stdout
    assert "MP0:s" in _emir("pins", DSPF, "--net", "VDD").stdout
    out = _emir("save-lines", DSPF, "--net", "VDD", "--mode", "rcc").stdout
    assert out.startswith("save") and "Rm1:1" in out
    out = _emir("save-lines", DSPF, "--net", "VDD", "--mode", "cc-grid").stdout
    assert "MP0:s" in out


def test_cli_info_and_version():
    assert _emir("--version").stdout.startswith("emir ")
    assert "spectre" in _emir("info").stdout


def test_skill_files_balanced(tmp_path):
    from emir.selftest import _parens_balanced
    from emir.skillgui import install
    files = install(str(tmp_path))
    assert "drDefinePacket" in open(files["drf"]).read()
    for p in (files["main"], files["app"], files["site"]):
        assert _parens_balanced(open(p).read()), p
    main = open(files["main"]).read()
    assert "emirMain(" in main and "deRegUserTriggers" in main
