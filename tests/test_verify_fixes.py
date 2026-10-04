"""Commands as an agent runs them (each test names the case):
the commands an agent is told to run must run as printed, the documents must
say what they did, and the codes must sit in their areas."""
import json
import os
import shlex
import subprocess
import sys

import jsonschema
import pytest
from _helpers import CUR, DSPF, PROC, ROOT, write

from emir import cli, plan as P, schemas


def _doc(capsys, argv, rc=0):
    """`emir ARGV --format json` in process: the one document."""
    code = cli.main([*map(str, argv), "--format", "json"])
    cap = capsys.readouterr()
    assert code == rc, (code, cap.out[-2000:], cap.err[-2000:])
    d = json.loads(cap.out)
    name = d["schema"].split(".", 1)[1].split("/")[0]
    jsonschema.Draft202012Validator(schemas.get(name)).validate(d)
    return d


def _pretty_currents(tmp_path, name="loads.json") -> str:
    """The sample's DC currents as a pretty-printed file (several lines, as
    an agent or a person writes it)."""
    return write(tmp_path / name, json.dumps(json.loads(CUR), indent=2) + "\n")


@pytest.fixture
def no_sims(monkeypatch):
    from emir import site
    monkeypatch.setattr(site, "tool", lambda name, explicit=None: (None, "$PATH"))


# ------------------------------------------------------------ H2 --currents FILE


def test_h2_run_and_check_take_a_currents_file(tmp_path, capsys):
    cur = _pretty_currents(tmp_path)
    inline = _doc(capsys, ["run", DSPF, "--process", PROC, "--net", "VDD", "--currents", CUR])
    filed = _doc(capsys, ["run", DSPF, "--process", PROC, "--net", "VDD", "--currents", cur])
    assert filed["verdict"] == inline["verdict"]
    assert filed["nets"][0]["max_ratio"] == inline["nets"][0]["max_ratio"]
    assert filed["nets"][0]["ir_drop_mV"] == inline["nets"][0]["ir_drop_mV"]
    chk = _doc(capsys, ["check", DSPF, "--process", PROC, "--net", "VDD", "--currents", cur])
    assert chk["passed"] is True
    # `@file` (no argument-file expansion over MCP) reads the same file
    at = _doc(capsys, ["run", DSPF, "--process", PROC, "--net", "VDD", f"--currents=@{cur}"])
    assert at["nets"][0]["max_ratio"] == inline["nets"][0]["max_ratio"]


def test_h2_a_missing_or_broken_currents_file_is_coded(tmp_path, capsys):
    gone = str(tmp_path / "gone.json")
    d = _doc(capsys, ["check", DSPF, "--process", PROC, "--net", "VDD", "--currents", gone],
             rc=2)
    f = next(x for x in d["findings"] if x["level"] == "FAIL")
    assert f["code"] == "EMIR-E101" and "neither inline JSON" in f["message"]
    bad = write(tmp_path / "bad.json", "{VDD: 1}\n")
    d = _doc(capsys, ["check", DSPF, "--process", PROC, "--net", "VDD", "--currents", bad],
             rc=2)
    assert next(x for x in d["findings"] if x["level"] == "FAIL")["code"] == "EMIR-E401"
    # the hint of E401 says a file is fine
    from emir.errors import lookup
    assert "file" in lookup("EMIR-E401").hint and "file" in lookup("EMIR-E401").meaning


def test_h2_next_plans_a_currents_file_that_runs_as_printed(tmp_path, no_sims):
    cur = _pretty_currents(tmp_path)
    p = subprocess.run([sys.executable, "-m", "emir", "next", "--dspf", DSPF, "--process",
                        PROC, "--currents", cur, "--format", "json"], cwd=tmp_path,
                       capture_output=True, text=True, env={**os.environ, "PYTHONPATH": ROOT})
    assert p.returncode == 0, p.stderr
    plan = json.loads(p.stdout)
    assert [s["id"] for s in plan["steps"]] == ["selftest", "nets", "check", "run", "report"]
    for s in plan["steps"][1:]:
        argv = shlex.split(s["command"])
        r = subprocess.run([sys.executable, "-m", "emir", *argv[1:]], cwd=tmp_path,
                           capture_output=True, text=True,
                           env={**os.environ, "PYTHONPATH": ROOT})
        assert r.returncode == 0, (s["command"], r.stdout[-800:], r.stderr[-800:])


@pytest.mark.parametrize("value, code", [("gone.json", "EMIR-E101"),
                                         ("bad.json", "EMIR-E401"),
                                         ('{"VDD": NaN}', "EMIR-E402")])
def test_h2_next_refuses_to_plan_currents_the_run_would_refuse(tmp_path, no_sims, value,
                                                               code):
    write(tmp_path / "bad.json", '["not", "an", "object"]\n')
    if value.endswith(".json"):
        value = str(tmp_path / value)
    f = P.Facts(dspf=DSPF, process=PROC, currents=value, net="VDD")
    plan = P.build(f)
    m = next(m for m in plan.missing if m["what"] == "usable --currents")
    assert code in m["why"] and plan.blocked
    assert not any(s["id"] in ("check", "run") for s in plan.steps)


def test_h2_mcp_run_takes_a_currents_file(tmp_path):
    from emir import mcp_tools as T
    cur = _pretty_currents(tmp_path)
    rc, doc, _line = T.call(T.by_command("run"), {"dspf": DSPF, "process": PROC,
                                                   "net": "VDD", "currents": cur})
    assert rc == 0 and doc["schema"] == "emir.run/1", doc
    # the generic tool's own example command line runs too
    rc, doc, _line = T.call_line(f"emir run {shlex.quote(DSPF)} --process "
                                 f"{shlex.quote(PROC)} --net VDD --currents {shlex.quote(cur)}")
    assert rc == 0 and doc["schema"] == "emir.run/1", doc
    assert "--currents c.json" in T.specs()[T.GENERIC].description


# ------------------------------------------------------------ M1 MCP arguments


def test_m1_unknown_and_missing_tool_arguments_are_refused_by_name(tmp_path):
    from emir import mcp_tools as T
    rc, doc, line = T.call(T.by_command("report"), {})
    assert rc == 2 and doc["code"] == "EMIR-E592" and line is None
    assert doc["details"]["missing"] == ["from_file"] and "from_file" in doc["message"]
    assert "--from" not in doc["message"]
    rc, doc, _ = T.call(T.by_command("run"), {"dspf": DSPF, "process": PROC,
                                              "currents": CUR, "summary_ou": "s.json"})
    assert doc["code"] == "EMIR-E592" and doc["details"]["unknown"] == ["summary_ou"]
    assert "did you mean 'summary_out'?" in doc["message"]
    rc, doc, _ = T.call(T.specs()[T.GENERIC], {"command_line": "emir info", "cwd": "/"})
    assert doc["code"] == "EMIR-E592"
    # confirm is accepted by every tool; a complete call runs
    rc, doc, _ = T.call(T.by_command("info"), {"confirm": True})
    assert rc == 0 and doc["schema"] == "emir.info/1"
    jsonschema.Draft202012Validator(schemas.get("error")).validate(
        T.call(T.by_command("nets"), {})[1])


# ------------------------------------------------------------ L4 extraction refusals


def test_l4_a_refused_extraction_names_the_missing_file(tmp_path, capsys):
    d = _doc(capsys, ["extract", "--tool", "magic", "--gds", tmp_path / "none.gds",
                      "--process", PROC], rc=2)
    assert d["command"] == "extract" and d["code"] == "EMIR-E101"
    # a missing process file: a sentence, not Python's "[Errno 2] ..."
    gds = write(tmp_path / "x.gds", "")
    d = _doc(capsys, ["extract", "--tool", "magic", "--gds", gds, "--process",
                      tmp_path / "none.yaml"], rc=2)
    assert d["code"] == "EMIR-E101" and "Errno" not in d["message"]
    assert d["message"] == f"process file not found: {tmp_path / 'none.yaml'}"


# ------------------------------------------------------------ L5 next steps


def test_l5_error_next_steps_keep_the_format_and_mark_shell_commands(tmp_path, capsys):
    d = _doc(capsys, ["pins", DSPF, "--net", "vdd"], rc=2)
    assert [s["command"] for s in d["next_steps"]] == [f"emir nets {DSPF} --format json"]
    assert all("shell" not in s for s in d["next_steps"])
    d = _doc(capsys, ["nets", tmp_path / "none.dspf"], rc=2)
    (st,) = d["next_steps"]
    assert st["command"].startswith("ls -l ") and st["shell"] is True
    # the MCP server refuses that step (no shell) with the code the mark names
    from emir import mcp_tools as T
    _rc, doc, _ = T.call_line(st["command"])
    assert doc["code"] == "EMIR-E591"
    # text mode: no --format added
    code = cli.main(["pins", DSPF, "--net", "vdd"])
    assert code == 2 and f"next: emir nets {DSPF}\n" in capsys.readouterr().err + "\n"


# ------------------------------------------------------------ L2 emir info


def _fake_magic(tmp_path, version, crash=False):
    """A `magic` on PATH that reports `version` (and, with `crash`, dies of
    SIGSEGV when it is asked to extract, as an old magic did on sky130)."""
    d = tmp_path / f"bin{version}"
    d.mkdir()
    body = (f'#!/bin/sh\nif [ "$1" = "--version" ]; then echo {version}; exit 0; fi\n'
            + ('kill -SEGV $$\n' if crash else 'exit 0\n'))
    p = d / "magic"
    p.write_text(body)
    p.chmod(0o755)
    return str(d)


def test_l2_info_lists_every_tool_a_command_runs(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("EMIR_SITE", os.devnull)
    for k in ("MAGIC_BIN", "PVS_BIN", "QRC_BIN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("PATH", _fake_magic(tmp_path, "8.3.105") + os.pathsep + "/bin")
    d = _doc(capsys, ["info"])
    tools = {t["name"]: t for t in d["tools"]}
    assert {"magic", "pvs", "quantus", "xyce", "ngspice", "spectre"} <= set(tools)
    assert tools["pvs"]["licensed"] and tools["quantus"]["licensed"]
    assert not tools["magic"]["licensed"] and tools["magic"]["version"] == "8.3.105"
    (w,) = [n for n in d["notices"] if n["code"] == "EMIR-W640"]
    assert "8.3.105" in w["message"] and "8.3.515" in w["message"]
    # a magic as new as the one checked: no warning
    monkeypatch.setenv("PATH", _fake_magic(tmp_path, "8.3.515") + os.pathsep + "/bin")
    d = _doc(capsys, ["info"])
    assert not [n for n in d["notices"] if n["code"] == "EMIR-W640"]
    # $MAGIC_BIN names the one to use, as `magic:` in the site file does
    monkeypatch.setenv("MAGIC_BIN", os.path.join(_fake_magic(tmp_path, "9.0.1"), "magic"))
    d = _doc(capsys, ["info"])
    m = next(t for t in d["tools"] if t["name"] == "magic")
    assert m["source"] == "$MAGIC_BIN" and m["version"] == "9.0.1"


def test_l2_a_crashing_magic_is_named_as_the_cause(tmp_path, monkeypatch):
    from emir import magic
    from emir.errors import EmirError, collecting
    monkeypatch.setenv("EMIR_SITE", os.devnull)
    monkeypatch.delenv("MAGIC_BIN", raising=False)
    exe = os.path.join(_fake_magic(tmp_path, "8.3.105", crash=True), "magic")
    gds = write(tmp_path / "x.gds", "")
    with collecting() as notes, pytest.raises(EmirError) as e:
        magic.run_magic(gds, "blk", "rc", exe, str(tmp_path / "ext"), log=lambda *_: None)
    assert e.value.code == "EMIR-E640"
    assert "CRASHED (SIGSEGV)" in e.value.message and "not a problem of the layout" \
        in e.value.message
    assert [n["code"] for n in notes] == ["EMIR-W640"]


# ------------------------------------------------------------ L3 probe of a block


def test_l3_probing_a_block_suggests_no_simulation_that_would_fail(tmp_path, capsys,
                                                                   monkeypatch):
    from emir import site
    ex = os.path.join(ROOT, "examples", "02_xyce")
    real = site.tool
    monkeypatch.setattr(site, "tool", lambda name, explicit=None: (
        ("/opt/fake/Xyce", "$PATH") if name == "xyce" else real(name, explicit)))
    base = ["--dspf", os.path.join(ex, "loadrail.dspf"), "--net", "VDD"]
    d = _doc(capsys, ["probe", os.path.join(ex, "cc.sp"), "-o", tmp_path / "b" / "cc.sp",
                      *base, "--names-out", tmp_path / "b" / "p.names"])
    assert d["has_tran"] is False and d["next_steps"] == []
    assert "EMIR-W463" in [n["code"] for n in d["notices"]]
    # the testbench that includes it: the simulate step, which runs as printed
    d = _doc(capsys, ["probe", os.path.join(ex, "tb_cc.cir"), "-o", tmp_path / "t" / "tb.cir",
                      *base, "--names-out", tmp_path / "t" / "p.names"])
    assert d["has_tran"] is True and "EMIR-W463" not in [n["code"] for n in d["notices"]]
    (st,) = d["next_steps"]
    assert shlex.split(st["command"])[:2] == ["emir", "simulate"]


# ------------------------------------------------------------ L9 illustrative files


def test_l9_only_the_bundled_sample_is_called_the_bundled_sample(tmp_path, no_sims):
    other = write(tmp_path / "example.yaml", open(PROC, encoding="utf-8").read())
    plan = P.build(P.Facts(dspf=DSPF, process=PROC, currents=CUR, net="VDD"))
    assert any("the bundled ILLUSTRATIVE sample" in a for a in plan.advice)
    plan = P.build(P.Facts(dspf=DSPF, process=other, currents=CUR, net="VDD"))
    advice = " ".join(plan.advice)
    assert "the bundled ILLUSTRATIVE" not in advice and f"{other} is marked illustrative" in advice
    from emir.check import check_inputs
    w = [f for f in check_inputs(DSPF, process=other, nets=["VDD"], currents=CUR).findings
         if f.code == "EMIR-W306"]
    assert w and "the bundled ILLUSTRATIVE" not in w[0].message and other in w[0].message


def test_the_summary_file_and_the_printed_summary_share_one_time():
    """The --summary-out file and the printed document were built apart and
    once differed by a second (a flaky `saved == printed`): a run has one
    `created`."""
    from emir.dspf import analyse, build_parser
    from emir.summary import summary_document
    a = build_parser().parse_args([DSPF, "--process", PROC, "--net", "VDD", "--currents",
                                   CUR])
    a.argv, a.command = [], "emir run"
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()):
        res = analyse(a)
    first = summary_document(res)["created"]
    assert res.created == first
    res.created = "2000-01-01T00:00:00Z"            # the run's time, whenever asked
    assert summary_document(res)["created"] == "2000-01-01T00:00:00Z"


# ------------------------------------------------------------ L6 documents


@pytest.mark.parametrize("clone", [True, False])
def test_l6_advice_cites_documents_an_install_can_open(clone, monkeypatch, tools_pinned):
    """In a clone the advice names the file in it; from a wheel (no agent/
    or pdks/ on disk) the file's page in the repository (REPO_URL, a
    placeholder until the maintainer decides)."""
    from emir import repo
    if not clone:
        monkeypatch.setattr(repo, "clone_root", lambda: None)
    play = "agent/playbooks/03-chip-level-hierarchy.md"
    want = (os.path.join(ROOT, play) if clone
            else f"{repo.REPO_URL}/blob/main/{play}")
    assert repo.ref(play) == want
    assert repo.ref("agent/cadence/spectre/") == (
        os.path.join(ROOT, "agent/cadence/spectre") if clone
        else f"{repo.REPO_URL}/tree/main/agent/cadence/spectre")
    # a plan with no process file cites the open PDKs' files the same way
    plan = P.build(P.Facts(dspf=DSPF, net="VDD"))
    how = next(m["how"] for m in plan.missing if "process file" in m["what"])
    assert repo.ref("pdks/TEMPLATE.yaml") in how and repo.ref("pdks/sky130.yaml") in how
    # a big block's advice points at the playbook; at a Cadence site, at the
    # Spectre testbench templates
    monkeypatch.setitem(P.THRESHOLDS, "big_block_nodes", 5)
    tools_pinned(spectre=True)
    plan = P.build(P.Facts(dspf=DSPF, process=PROC, net="VDD"))
    advice = " ".join(plan.advice)
    assert repo.ref(play) in advice
    assert repo.ref("agent/cadence/spectre/") + "/tb_cc.scs.tmpl" in advice
    if not clone:
        assert repo.REPO_URL in advice and " agent/cadence/spectre" not in advice


def test_l6_every_repository_path_the_package_cites_exists():
    """The advice, hints, next steps and MCP resources name repository files
    (`agent/...`, `pdks/...`, `docs/...`, `examples/...`, `scripts/...`), in the
    clone or as a page of the repository: each one exists, so neither breaks."""
    import glob
    import re
    cite = re.compile(r"(?<![\w./-])((?:agent|docs|pdks|examples|scripts)/[\w./-]*[\w/])")
    sources = (glob.glob(os.path.join(ROOT, "emir", "*.py"))
               + glob.glob(os.path.join(ROOT, "agent", "cadence", "skill", "*.il")))
    missing = set()
    for path in sources:
        with open(path, encoding="utf-8") as f:
            for rel in cite.findall(f.read()):
                if not os.path.exists(os.path.join(ROOT, rel.rstrip("/"))):
                    missing.add((os.path.relpath(path, ROOT), rel))
    assert not missing, sorted(missing)


@pytest.fixture
def tools_pinned(monkeypatch):
    from emir import site
    found = {}
    monkeypatch.setattr(site, "tool", lambda name, explicit=None: (
        (f"/opt/fake/{name}" if found.get(name) else None), "$PATH"))
    return lambda **kw: (found.clear(), found.update(kw))


# ------------------------------------------------------------ L10 run.sh with sh


def test_l10_every_example_runs_under_sh(tmp_path):
    """`sh examples/NN/run.sh` failed for the bash scripts ("Illegal option -o
    pipefail"): each is POSIX sh, or hands itself to bash before any bash
    syntax. The hand-over is run here with a stub that stops at once."""
    import glob
    guard = 'if [ -z "${BASH_VERSION:-}" ]; then exec bash "$0" "$@"; fi'
    scripts = sorted(glob.glob(os.path.join(ROOT, "examples", "*", "run.sh")))
    if not scripts:
        pytest.skip("no examples/ in this tree")
    for path in scripts:
        text = open(path, encoding="utf-8").read()
        first = text.splitlines()[0]
        if first == "#!/bin/sh":
            assert "pipefail" not in text and "${BASH_SOURCE" not in text, path
            continue
        assert "bash" in first, path
        code = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
        # the first line sh executes is the hand-over (comments before it)
        assert code[0] == guard, (path, code[0])
    # the hand-over itself: a script that needs bash, started with sh
    stub = tmp_path / "x.sh"
    stub.write_text(f"#!/usr/bin/env bash\n{guard}\nset -euo pipefail\na=(1 2)\n"
                    'echo "bash ${a[1]} $1"\n')
    p = subprocess.run(["sh", str(stub), "arg"], capture_output=True, text=True)
    assert p.returncode == 0 and p.stdout == "bash 2 arg\n", p.stderr


def test_a_stopped_simulation_leaves_no_partial_result(tmp_path):
    """A simulator stopped by --timeout may already have written part of its
    result: it is removed (it once survived, and a flaky test showed it)."""
    from emir.errors import EmirError
    from emir.opensim import run_simulator
    exe = tmp_path / "fake_ngspice"
    exe.write_text('#!/bin/sh\necho "partial" > "$3"\nsleep 5\n')
    exe.chmod(0o755)
    raw = tmp_path / "r.raw"
    with pytest.raises(EmirError) as e:
        run_simulator("ngspice", str(exe), str(tmp_path / "deck.cir"), str(raw), 1.0,
                      log=lambda *_: None)
    assert e.value.code == "EMIR-E605" and not raw.exists()
