"""`next_steps` hold exact, runnable commands built from the caller's own
arguments, paths quoted: the tests run them, from a directory with a space
in its name."""
import json
import shlex
import shutil
import subprocess
import sys

import pytest
from _helpers import CUR, DSPF, PROC, ROOT, cc_result


@pytest.fixture
def spaced(tmp_path):
    """The sample copied under a directory whose name has a space."""
    d = tmp_path / "my data"
    d.mkdir()
    return str(shutil.copy(DSPF, d / "rail.dspf")), str(shutil.copy(PROC, d / "pdk.yaml")), d


def run_cmd(command, cwd, stdin=None):
    """Run an `emir ...` command line exactly as written (through the shell's
    quoting rules), with this interpreter's emir."""
    argv = shlex.split(command)
    assert argv[0] == "emir", command
    return subprocess.run([sys.executable, "-m", "emir", *argv[1:]], cwd=cwd,
                          capture_output=True, text=True, input=stdin,
                          env={"PYTHONPATH": ROOT, "PATH": "/usr/bin:/bin"})


def emir_json(*args, cwd, rc=None):
    p = subprocess.run([sys.executable, "-m", "emir", *map(str, args), "--format", "json"],
                       cwd=cwd, capture_output=True, text=True,
                       env={"PYTHONPATH": ROOT, "PATH": "/usr/bin:/bin"})
    if rc is not None:
        assert p.returncode == rc, p.stdout + p.stderr
    return json.loads(p.stdout)


def test_check_pass_suggests_the_run_and_it_runs(spaced):
    dspf, proc, d = spaced
    doc = emir_json("check", dspf, "--process", proc, "--net", "VDD", "--currents", CUR,
                    cwd=d, rc=0)
    (step,) = doc["next_steps"]
    argv = shlex.split(step["command"])
    assert argv[:3] == ["emir", "run", dspf] and proc in argv and CUR in argv
    assert argv[-4:] == ["--summary-out", "emir_out/summary.json", "--format", "json"]
    p = run_cmd(step["command"], d)
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["schema"] == "emir.run/1"
    assert (d / "emir_out" / "summary.json").exists()


def test_check_failures_suggest_their_fixes_and_they_run(spaced, tmp_path):
    dspf, proc, d = spaced
    doc = emir_json("check", dspf, "--process", proc, "--net", "vdd", "--currents", CUR,
                    cwd=d, rc=2)
    cmds = [s["command"] for s in doc["next_steps"]]
    # the caller's --format is kept: each step prints one JSON document too
    assert f"emir nets {shlex.quote(dspf)} --format json" in cmds
    for c in cmds:
        assert run_cmd(c, d).returncode == 0, c


def test_unsaved_branches_suggest_the_save_lines(spaced, tmp_path):
    dspf, proc, d = spaced
    res = cc_result(tmp_path, extra={})                 # pin currents only: no branch
    doc = emir_json("check", dspf, "--process", proc, "--net", "VDD", "--cc-sim", res,
                    cwd=d, rc=2)
    cmds = [s["command"] for s in doc["next_steps"]]
    want = f"emir save-lines {shlex.quote(dspf)} --net VDD --format json"
    assert want in cmds, cmds
    p = run_cmd(want, d)
    assert p.returncode == 0 and json.loads(p.stdout)["lines"][0].startswith("save Rm1:1")


def test_run_suggests_the_report_and_it_runs(spaced):
    dspf, proc, d = spaced
    out = d / "out dir" / "summary.json"
    doc = emir_json("run", dspf, "--process", proc, "--net", "VDD", "--currents", CUR,
                    "--summary-out", out, cwd=d, rc=0)
    first = doc["next_steps"][0]["command"]
    assert shlex.split(first) == ["emir", "report", "--from", str(out)]
    p = run_cmd(first, d)
    assert p.returncode == 0 and p.stdout.startswith("# EMIR screening report")
    # a FAIL verdict also suggests the browser view and the re-run, both runnable
    ids = [s["command"] for s in doc["next_steps"][1:]]
    assert any("--html emir_out/html" in c for c in ids)
    for c in ids:
        assert run_cmd(c, d).returncode == 0, c


def test_run_without_a_summary_file_suggests_runnable_report_steps(spaced):
    """`emir report --from -` alone has nothing to read (and once blocked on a
    terminal); without --summary-out the steps keep the summary in a file
    and report from it, each runnable as printed. Piping still works."""
    dspf, proc, d = spaced
    p = subprocess.run([sys.executable, "-m", "emir", "run", dspf, "--process", proc,
                        "--net", "VDD", "--currents", CUR, "--format", "json"], cwd=d,
                       capture_output=True, text=True, env={"PYTHONPATH": ROOT})
    doc = json.loads(p.stdout)
    first, second = (s["command"] for s in doc["next_steps"][:2])
    assert "emir report --from -" not in [s["command"] for s in doc["next_steps"]]
    assert shlex.split(first)[:2] == ["emir", "run"]
    assert shlex.split(first)[-2:] == ["--summary-out", "emir_out/summary.json"]
    assert second == "emir report --from emir_out/summary.json"
    assert run_cmd(first, d).returncode == 0
    r = run_cmd(second, d)
    assert r.returncode == 0 and "**Verdict: FAIL**" in r.stdout
    r = run_cmd("emir report --from -", d, stdin=p.stdout)
    assert r.returncode == 0 and "**Verdict: FAIL**" in r.stdout


def test_error_documents_carry_runnable_next_steps(spaced):
    dspf, _proc, d = spaced
    doc = emir_json("pins", dspf, "--net", "vdd", cwd=d, rc=2)
    (step,) = doc["next_steps"]
    assert step["command"] == f"emir nets {shlex.quote(dspf)} --format json"
    assert "shell" not in step
    p = run_cmd(step["command"], d)
    assert p.returncode == 0 and json.loads(p.stdout)["schema"] == "emir.nets/1"
    # in text mode the same step has no --format
    p = subprocess.run([sys.executable, "-m", "emir", "pins", dspf, "--net", "vdd"], cwd=d,
                       capture_output=True, text=True, env={"PYTHONPATH": ROOT})
    assert p.returncode == 2 and f"next: emir nets {shlex.quote(dspf)}\n" in p.stderr + "\n"
