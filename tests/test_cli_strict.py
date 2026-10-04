"""No option prefix ever selects an option (`allow_abbrev=False` on every
parser), and every `emir run` ends with a verdict line -- including the run
that had nothing to analyse, which exits 2 instead of a quiet 0."""
import ast
import glob
import json
import os
import subprocess
import sys

import numpy as np
import pytest
from _helpers import CUR, DSPF, PROC, ROOT, cc_result, device_waves, emir, time_base, \
    two_net_dspf

from emir.validate import _psf_tran

# ------------------------------------------------------------ allow_abbrev


def _parser_calls():
    for path in sorted(glob.glob(os.path.join(ROOT, "emir", "*.py"))
                       + glob.glob(os.path.join(ROOT, "scripts", "*.py"))):
        tree = ast.parse(open(path, encoding="utf-8").read())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            if name in ("ArgumentParser", "ArgParser", "_Parser", "parser"):
                yield os.path.relpath(path, ROOT), name, node


def test_every_parser_refuses_abbreviations():
    """Every emir parser is an `output.ArgParser` (built by `output.parser`
    or subclassed), which forces `allow_abbrev=False`; a plain
    `argparse.ArgumentParser` is allowed only with the keyword set (the
    stdlib-only leak guard)."""
    from emir.output import ArgParser, parser
    assert ArgParser(prog="x").allow_abbrev is False
    assert ArgParser(prog="x", allow_abbrev=True).allow_abbrev is False
    assert parser("emir x").allow_abbrev is False
    calls = list(_parser_calls())
    assert len([c for c in calls if c[1] in ("parser", "ArgParser", "_Parser")]) >= 12, calls
    for path, name, node in calls:
        if name != "ArgumentParser":
            continue
        kw = {k.arg: k.value for k in node.keywords}
        assert "allow_abbrev" in kw and isinstance(kw["allow_abbrev"], ast.Constant) \
            and kw["allow_abbrev"].value is False, f"{path}:{node.lineno}"


@pytest.mark.parametrize("argv", [
    ["run", "x.dspf", "--taps", "f"],              # was silently --taps-out
    ["check", "x.dspf", "--json-o", "f"],
    ["nets", "x.dspf", "--he"],
    ["save-lines", "x.dspf", "--mode", "rcc", "--pre", "XI0."],
    ["pins", "x.dspf", "--net", "VDD", "--ne", "X"],
    ["selftest", "--kee"],
    ["validate", "--qui"],
    ["gui-install", "--di", "x"],
    ["import-ict", "--ict", "f", "--emir", "y", "--conductor-pat", "{layer}"],
])
def test_an_abbreviated_option_is_an_error(argv):
    p = emir(*argv)
    assert p.returncode == 2, p.stdout + p.stderr
    assert "unrecognized arguments" in p.stderr, p.stderr


def test_leak_guard_refuses_abbreviations():
    p = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "leak_guard.py"),
                        "--stag"], capture_output=True, text=True)
    assert p.returncode == 2 and "unrecognized arguments" in p.stderr


def test_a_taps_file_is_never_overwritten(tmp_path):
    """`--taps f` on a CC run used to mean `--taps-out f`, which wrote the
    ingested taps over the user's file."""
    taps = tmp_path / "taps.csv"
    taps.write_text("mine\n")
    p = emir("run", DSPF, "--net", "VDD", "--process", PROC, "--cc-sim", cc_result(tmp_path),
             "--mode", "cc-grid", "--period", "1e-9", "--taps", taps)
    assert p.returncode == 2 and taps.read_text() == "mine\n"


# ------------------------------------------------------------ verdict lines


def _multi_result(tmp_path):
    t = time_base()
    sigs = device_waves(t)
    sigs.update({"MN0:s": -1e-3 * np.ones_like(t), "MN1:s": -2e-3 * np.ones_like(t),
                 "MN0:d": 1e-3 * np.ones_like(t), "MN1:d": -1e-3 * np.ones_like(t)})
    return _psf_tran(str(tmp_path / "all.tran"), t, sigs)


@pytest.mark.parametrize("case", ["one-net-dc", "all-nets-dc", "one-net-cc", "all-nets-cc"])
def test_every_analysis_run_ends_with_a_verdict(tmp_path, case):
    if case == "one-net-dc":
        args, nets = [DSPF, "--net", "VDD", "--process", PROC, "--currents", CUR], 1
    elif case == "all-nets-dc":
        args, nets = [DSPF, "--process", PROC, "--currents", CUR], 1
    elif case == "one-net-cc":
        args, nets = [DSPF, "--net", "VDD", "--process", PROC, "--cc-sim",
                      cc_result(tmp_path), "--mode", "cc-grid", "--period", "1e-9"], 1
    else:
        args, nets = [two_net_dspf(tmp_path), "--process", PROC, "--cc-sim",
                      _multi_result(tmp_path), "--mode", "cc-grid", "--period", "1e-9",
                      "--no-check",
                      "--json", tmp_path / "s.json"], 3
    p = emir("run", *args)
    assert p.returncode == 0, p.stdout + p.stderr
    lines = p.stdout.strip().splitlines()
    assert sum(1 for ln in lines if ln.strip().startswith("verdict ")) == nets
    assert lines[-1].startswith("overall verdict: ") and f"{nets} net(s)" in lines[-1]
    if case == "all-nets-cc":
        verdicts = [n["verdict"] for n in json.load(open(tmp_path / "s.json"))["nets"]]
        worst = "FAIL" if "FAIL" in verdicts else "WARN" if "WARN" in verdicts else "CLEAN"
        assert lines[-1].startswith(f"overall verdict: {worst} -- 3 net(s): "
                                    f"{verdicts.count('FAIL')} FAIL")


@pytest.mark.parametrize("args,missing", [
    ([], "--process and --currents, --cc-sim or --netlist"),
    (["--net", "VDD", "--process", PROC], "--currents, --cc-sim or --netlist"),
    (["--currents", CUR], "--process"),
])
def test_a_run_with_nothing_to_analyse_says_so_and_exits_2(args, missing, monkeypatch):
    monkeypatch.delenv("EMIR_PROCESS", raising=False)
    p = emir("run", DSPF, *args, env={**os.environ, "EMIR_SITE": os.devnull})
    assert p.returncode == 2, p.stdout + p.stderr
    assert "verdict        : NOT ANALYSED" in p.stdout
    assert f"nothing analysed: give {missing}" in p.stderr
