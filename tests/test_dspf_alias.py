"""`--dspf FILE` is the DSPF on every command that reads one.

`emir next` takes the DSPF as `--dspf`; the commands it prints take it as
their first argument. Real agents carried `--dspf` over: the two screen-dc
eval runs of the final review failed on exactly the two commands below (the
`emir run` of one run, the `emir check` of the other). They now run as typed;
a command that reads no DSPF says which ones do."""
import json
import os
import shutil
import sys

import pytest
from _helpers import DSPF, PROC, ROOT, emir

from emir import cli, mcp_tools

AGENT_DIR = os.path.join(ROOT, "agent", "evals")

#: the currents of the two eval runs (cur.json is the second run's file)
CURRENTS = '{"VDD":0.004,"MP0:s":-0.001,"MP1:s":-0.001,"MP2:s":-0.001,"MP3:s":-0.001}'


@pytest.fixture
def ws(tmp_path):
    """The eval workspace: inputs/rail.dspf, inputs/process.yaml, cur.json."""
    (tmp_path / "inputs").mkdir()
    shutil.copy(DSPF, tmp_path / "inputs" / "rail.dspf")
    shutil.copy(PROC, tmp_path / "inputs" / "process.yaml")
    (tmp_path / "cur.json").write_text(CURRENTS + "\n")
    return tmp_path


def jdoc(p, rc=0):
    assert p.returncode == rc, (p.returncode, p.stdout[-1500:], p.stderr[-1500:])
    return json.loads(p.stdout)


# ------------------------------------------------------------ the failing commands, as typed


def test_the_eval_run_with_dspf_runs(ws):
    """screen-dc, first real-agent run: `emir run --dspf ...` (EMIR-E507 before)."""
    d = jdoc(emir("run", "--dspf", "inputs/rail.dspf", "--process", "inputs/process.yaml",
                  "--currents", CURRENTS, "--summary-out", "emir_out/summary.json",
                  "--format", "json", cwd=ws))
    assert d["schema"] == "emir.run/1" and d["verdict"] == "FAIL"
    assert d["dspf"] == str(ws / "inputs" / "rail.dspf")
    summ = json.loads((ws / "emir_out" / "summary.json").read_text())
    assert summ["schema"] == "emir.run/1"
    # the same result as the positional form
    p = jdoc(emir("run", "inputs/rail.dspf", "--process", "inputs/process.yaml",
                  "--currents", CURRENTS, "--format", "json", cwd=ws))
    assert [n["max_ratio"] for n in p["nets"]] == [n["max_ratio"] for n in d["nets"]]


def test_the_eval_check_with_dspf_runs(ws):
    """screen-dc, second real-agent run: `emir check --dspf ...`."""
    d = jdoc(emir("check", "--dspf", "inputs/rail.dspf", "--process", "inputs/process.yaml",
                  "--currents", "cur.json", "--format", "json", cwd=ws))
    assert d["schema"] == "emir.check/1" and d["passed"] is True
    # its next step, the run, keeps --dspf and runs as printed
    run = next(s["command"] for s in d["next_steps"] if " run " in f" {s['command']} ")
    assert "--dspf inputs/rail.dspf" in run
    import shlex
    r = jdoc(emir(*shlex.split(run)[1:], cwd=ws))
    assert r["schema"] == "emir.run/1"


def test_the_final_reviews_repro_runs():
    d = jdoc(emir("run", "--dspf", "emir/data/sample/rail.dspf", "--process",
                  "emir/data/sample/process.yaml", "--currents",
                  '{"VDD":0.004,"MP0:s":-0.004}', "--format", "json", cwd=ROOT))
    assert d["schema"] == "emir.run/1"


def test_the_eval_grader_accepts_them():
    sys.path.insert(0, AGENT_DIR)
    try:
        import emircmd
    finally:
        sys.path.remove(AGENT_DIR)
    assert emircmd.check_argv(["run", "--dspf", "inputs/rail.dspf", "--process", "p.yaml",
                               "--currents", CURRENTS, "--summary-out", "s.json",
                               "--format", "json"]) is None
    assert emircmd.check_argv(["check", "--dspf", "inputs/rail.dspf", "--process", "p.yaml",
                               "--currents", "cur.json", "--format", "json"]) is None
    assert emircmd.unknown_options(["run", "--dspf", "x.dspf"]) == []


# ------------------------------------------------------------ every command with a DSPF


def _main(capsys, *args):
    code = cli.main([*map(str, args), "--format", "json"])
    return code, json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("args", [
    ["nets"], ["pins", "--net", "VDD"], ["save-lines", "--net", "VDD", "--mode", "rcc"],
    ["check", "--process", PROC, "--currents", CURRENTS],
])
def test_every_dspf_command_takes_the_option(capsys, args):
    code, a = _main(capsys, args[0], DSPF, *args[1:])
    code2, b = _main(capsys, args[0], *args[1:], "--dspf", DSPF)
    code3, c = _main(capsys, args[0], f"--dspf={DSPF}", *args[1:])
    assert code == code2 == code3 == 0
    # the same document, but for the commands it echoes (as typed)
    assert _results(a) == _results(b) == _results(c)


def _results(doc):
    if isinstance(doc, dict):
        return {k: _results(v) for k, v in doc.items() if k not in ("command", "next_steps")}
    if isinstance(doc, list):
        return [_results(v) for v in doc]
    return doc


def test_abstract_takes_the_option(tmp_path, capsys):
    """An abstract needs a block with ports: its refusal is enough to show
    that `--dspf` reached the DSPF (the same code either way)."""
    base = ["--process", PROC, "--cc-sim", str(tmp_path / "none.raw"), "--mode", "cc-grid",
            "-o", str(tmp_path / "a.json")]
    c1, a = _main(capsys, "abstract", DSPF, *base)
    c2, b = _main(capsys, "abstract", "--dspf", DSPF, *base)
    assert c1 == c2 and a["code"] == b["code"] and a["code"] != "EMIR-E507"


def test_an_argument_file_may_carry_it(tmp_path, capsys):
    f = tmp_path / "args.txt"
    f.write_text(f"--dspf\n{DSPF}\n")
    code, d = _main(capsys, "nets", f"@{f}")
    assert code == 0 and d["dspf"] == DSPF


def test_two_different_dspfs_are_refused_and_the_same_one_twice_is_not(tmp_path, capsys):
    other = shutil.copy(DSPF, tmp_path / "other.dspf")
    code, d = _main(capsys, "nets", DSPF, "--dspf", other)
    assert code == 2 and d["code"] == "EMIR-E507" and "two DSPFs" in d["message"]
    code, d = _main(capsys, "nets", DSPF, "--dspf", DSPF)
    assert code == 0 and d["schema"] == "emir.nets/1"


def test_a_missing_dspf_says_it_is_the_first_argument(capsys):
    code, d = _main(capsys, "run", "--process", PROC)
    assert code == 2 and d["code"] == "EMIR-E507"
    assert "the DSPF is the first argument: emir run <dspf>" in d["message"]


# ------------------------------------------------------------ commands with no DSPF


@pytest.mark.parametrize("args, needle", [
    (["extract", "--tool", "magic", "--gds", "x.gds", "--process", PROC],
     "extract writes a DSPF from --gds"),
    (["report", "--from", "s.json"], "emir report --from"),
    (["explain", "EMIR-E101"], "emir explain takes no DSPF"),
    (["selftest"], "emir selftest takes no DSPF"),
])
def test_a_command_with_no_dspf_says_which_ones_take_it(capsys, args, needle):
    code, d = _main(capsys, *args, "--dspf", DSPF)
    assert code == 2 and d["code"] == "EMIR-E507"
    assert needle in d["details"]["did_you_mean"]["--dspf"]


# ------------------------------------------------------------ the documented form


def test_the_positional_stays_the_documented_form():
    """`--dspf` is out of the help, the usage and the MCP tool parameters:
    the DSPF is a required positional there, as before."""
    p = emir("run", "--help")
    assert "--dspf" not in p.stdout.split("positional arguments")[0]
    assert "`--dspf FILE` is accepted as the same" in " ".join(p.stdout.split())
    for command in ("run", "nets", "pins", "save-lines", "abstract"):
        params = {q.name: q for q in mcp_tools.params_of(
            command, mcp_tools.capture_parser(command))}
        assert params["dspf"].required and params["dspf"].flag is None, command
        assert not [n for n in params if "given" in n], command


def test_next_prints_the_positional_form(ws):
    d = jdoc(emir("next", "--dspf", "inputs/rail.dspf", "--process", "inputs/process.yaml",
                  "--currents", "cur.json", "--format", "json", cwd=ws))
    steps = {s["id"]: s["command"] for s in d["steps"]}
    for sid in ("nets", "check", "run"):
        assert steps[sid].startswith(f"emir {sid} inputs/rail.dspf "), steps[sid]
        assert "--dspf" not in steps[sid]


def test_agents_md_says_the_dspf_is_positional():
    text = open(os.path.join(ROOT, "AGENTS.md"), encoding="utf-8").read()
    section3 = text.split("## 3.")[1].split("## 4.")[0]
    assert "first argument" in section3 and "emir run <dspf>" in section3
