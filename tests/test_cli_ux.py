"""Stage-1 robustness and UX fixes of the command line and the reports."""
import contextlib
import io
import json
import subprocess
import sys
import time

import pytest
from _helpers import CUR, DSPF, PROC, ROOT, cc_result, emir, many_nets_dspf

from emir import cli
from emir.dspf import parse

# ------------------------------------------------------------ per-net index


def test_per_net_queries_use_one_index(tmp_path):
    p = many_nets_dspf(tmp_path, 300)
    d = parse(p)
    naive = {n: [r["name"] for r in d.res
                 if d.node_net.get(r["n1"]) == n or d.node_net.get(r["n2"]) == n]
             for n in d.nets}
    assert {n: [r["name"] for r in d.net_resistors(n)] for n in d.nets} == naive
    ix = d._index()
    for n in d.nets:
        d.net_resistors(n), d.net_taps(n), d.net_ports(n)
    assert d._index() is ix                     # built once, not per net
    assert d.net_ports("N7") == ["N7"] and d.has_net("N7") and not d.has_net("N9999")
    # adding a resistor is seen without an explicit reindex
    d.res.append({"name": "Rx", "n1": "N7", "n2": "N7:1", "r": 1.0, "layer": "metal1"})
    assert [r["name"] for r in d.net_resistors("N7")][-1] == "Rx"


def test_listing_many_nets_is_linear(tmp_path):
    """Listing used to rescan every resistor and pin per net: on this file,
    2.0 s against a 0.03 s parse. Measured against the parse of the same
    file, so the bound does not depend on the machine."""
    p = many_nets_dspf(tmp_path, 3000)
    t0 = time.perf_counter()
    parse(p)
    t_parse = time.perf_counter() - t0
    t0 = time.perf_counter()
    with contextlib.redirect_stdout(io.StringIO()):
        assert cli.main(["nets", p]) == 0      # parses again, then lists
    t_list = time.perf_counter() - t0 - t_parse
    assert t_list < 5 * t_parse + 0.2, (t_list, t_parse)


# ------------------------------------------------------------ errors


def test_broken_pipe_is_quiet(tmp_path):
    p = many_nets_dspf(tmp_path, 3000)          # ~180 KB of output, > a pipe buffer
    cmd = f'"{sys.executable}" -m emir nets "{p}" | head -n 1'
    r = subprocess.run(cmd, shell=True, cwd=ROOT, capture_output=True, text=True)
    assert r.stdout.startswith("many:")
    assert "Traceback" not in r.stderr and "BrokenPipe" not in r.stderr, r.stderr


@pytest.mark.parametrize("exc,code,needle,ecode", [
    (MemoryError("transfer matrix would need 9 GiB"), 2, "--scalars", "EMIR-E418"),
    (ModuleNotFoundError("No module named 'klayout'", name="klayout"), 3, "pip install",
     "EMIR-E603"),
    (FileNotFoundError(2, "No such file or directory", "x.dspf"), 2, "x.dspf", "EMIR-E101"),
    (PermissionError(13, "Permission denied", "x.dspf"), 2, "x.dspf", "EMIR-E107"),
    (IsADirectoryError(21, "Is a directory", "x.dspf"), 2, "x.dspf", "EMIR-E107"),
    (TypeError("unsupported operand"), 70, "internal error", "EMIR-E901"),
    (KeyboardInterrupt(), 130, "interrupted", "EMIR-E904"),
])
def test_exceptions_are_one_line_messages(monkeypatch, capsys, exc, code, needle, ecode):
    """Every exception that reaches the command line is a coded one-line error
    with the contract's exit status: 2 bad input, 3 a missing package, 70 a
    bug (EX_SOFTWARE), 130 interrupted."""
    def boom(argv, out):
        raise exc
    monkeypatch.setitem(cli.COMMANDS, "nets", boom)
    monkeypatch.delenv("EMIR_DEBUG", raising=False)
    assert cli.main(["nets", "x"]) == code
    err = capsys.readouterr().err
    assert err.startswith(f"emir: {ecode}: ") and needle in err and "Traceback" not in err


def test_emir_debug_shows_the_traceback(monkeypatch):
    def boom(argv, out):
        raise TypeError("bug")
    monkeypatch.setitem(cli.COMMANDS, "nets", boom)
    monkeypatch.setenv("EMIR_DEBUG", "1")
    with pytest.raises(TypeError):
        cli.main(["nets", "x"])


# ------------------------------------------------------------ check / run options


def test_check_json_writes_the_findings(tmp_path):
    out = tmp_path / "sub" / "check.json"
    p = emir("check", DSPF, "--process", PROC, "--net", "VDD", "--currents", CUR,
             "--json", out)
    assert p.returncode == 0, p.stderr
    d = json.loads(out.read_text())
    assert d["passed"] is True and d["findings"]


def test_summary_goes_to_stderr_when_stdout_is_spice():
    p = emir("run", DSPF, "--net", "VDD", "--save-branches")
    assert p.returncode == 0, p.stderr
    assert p.stdout.startswith("save ") and "railblock:" in p.stderr
    p = emir("run", DSPF, "--save-list", "--save-prefix", "XI0.")
    assert p.stdout.startswith("save XI0.MP0:s"), p.stdout


def test_run_without_net_analyses_every_net(tmp_path):
    p = emir("run", DSPF, "--process", PROC, "--currents", CUR,
             "--json", tmp_path / "s.json")
    assert p.returncode == 0, p.stdout + p.stderr
    assert "IR drop" in p.stdout and "verdict" in p.stdout
    assert [n["net"] for n in json.load(open(tmp_path / "s.json"))["nets"]] == ["VDD"]
    # with nothing to analyse it lists the nets, says so and exits 2
    p = emir("run", DSPF)
    assert p.returncode == 2 and "net VDD" in p.stdout
    assert "verdict        : NOT ANALYSED" in p.stdout and "nothing analysed" in p.stderr


def test_fail_on_violation_sets_the_exit_code():
    args = ["run", DSPF, "--net", "VDD", "--process", PROC, "--currents", CUR]
    assert emir(*args).returncode == 0
    p = emir(*args, "--fail-on-violation")
    assert p.returncode == 1 and "FAIL verdict" in p.stderr


def test_html_and_viewer_always_take_a_directory(tmp_path):
    out = tmp_path / "nets"
    p = emir("run", "--html", out, DSPF, "--net", "VDD", "--process", PROC,
             "--currents", CUR)
    assert p.returncode == 0, p.stderr
    assert (out / "index.html").exists()
    # a bare --html no longer silently means a default directory, so it can
    # no longer swallow the next word either
    p = emir("run", DSPF, "--net", "VDD", "--html")
    assert p.returncode == 2 and "expected one argument" in p.stderr
    p = emir("run", "--html", DSPF, "--net", "VDD")
    assert p.returncode == 2 and "--html DIR" in p.stderr


# ------------------------------------------------------------ reports


def test_html_needs_no_matplotlib(tmp_path):
    """`--html` reached matplotlib through netreport -> dashboard -> style."""
    code = ("import sys; sys.modules['matplotlib'] = None\n"
            "from emir.cli import main\n"
            f"rc = main(['run', {DSPF!r}, '--net', 'VDD', '--process', {PROC!r},"
            f" '--currents', {CUR!r}, '--html', {str(tmp_path / 'nets')!r}])\n"
            "assert 'matplotlib' not in [m for m in sys.modules if sys.modules[m]]\n"
            "raise SystemExit(rc)\n")
    p = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    assert (tmp_path / "nets" / "index.html").exists()


def test_provenance_and_mode_labels(tmp_path):
    out = tmp_path / "nets"
    p = emir("run", DSPF, "--net", "VDD", "--process", PROC, "--currents", CUR,
             "--html", out, "--json", tmp_path / "s.json")
    assert p.returncode == 0, p.stderr
    page = (out / "railblock_VDD.html").read_text()
    assert "emir run run" not in page and f"emir run {DSPF} --net VDD" in page
    # one label for the DC route everywhere (the HTML used to say cc-grid)
    assert json.load(open(tmp_path / "s.json"))["nets"][0]["mode"] == "dc"
    assert json.load(open(out / "railblock_VDD.json"))["mode"] == "dc"
    assert "<b>dc</b>" in page


def test_cc_run_is_labelled_cc_grid(tmp_path):
    p = emir("run", DSPF, "--net", "VDD", "--process", PROC, "--cc-sim",
             cc_result(tmp_path), "--mode", "cc-grid", "--period", "1e-9", "--json",
             tmp_path / "s.json")
    assert p.returncode == 0, p.stdout + p.stderr
    net = json.load(open(tmp_path / "s.json"))["nets"][0]
    assert net["mode"] == "cc-grid" and net["mode_reason"] == "--mode cc-grid"


def test_viewer_ir_agrees_with_the_console(tmp_path):
    """The viewer coloured each segment by its midpoint: 42 mV at most on the
    sample against the reported 54 mV (the 24 ohm one-cut contact at 1 mA)."""
    p = emir("run", DSPF, "--net", "VDD", "--process", PROC, "--currents", CUR,
             "--viewer", tmp_path / "v")
    assert p.returncode == 0, p.stderr
    viz = json.load(open(tmp_path / "v" / "emir_viz.json"))
    assert abs(viz["ir_max_mv"] - 54.0) < 1e-6
    assert abs(max(s["mv"] for s in viz["ir"]) - 54.0) < 1e-6
    assert "IR drop        : 54.0000 mV" in p.stdout


def test_info_no_longer_says_html_needs_matplotlib():
    code = ("import sys; sys.modules['matplotlib'] = None\n"
            "from emir.cli import main; raise SystemExit(main(['info']))\n")
    p = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    assert "HTML reports need it" not in p.stdout
    assert "HTML reports do not need it" in p.stdout
