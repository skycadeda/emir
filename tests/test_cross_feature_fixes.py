"""Where features meet, case by case (each found by running them together):

* the simulator verification: a records file this user may not read, a
  result whose version is unknown, and a finished run (`--run-summary`, the
  run's own next steps) get a verification only where one can help;
* every example's run.sh takes its output directory as an argument.

Every number is invented or computed here; the simulator cases skip when
the simulator is not installed (one simulator process at a time).
"""
import json
import os
import shutil
import stat
import subprocess

import jsonschema
import pytest
from _helpers import CUR, DSPF, PROC, ROOT, emir
from test_verify_sim import XYCE, _ngspice_result, _record

from emir import plan as P
from emir import report, schemas, site, verify_sim
from emir.errors import collecting


def _doc(p, rc=0):
    assert p.returncode == rc, (p.returncode, p.stdout[-1500:], p.stderr[-1500:])
    return json.loads(p.stdout)


def _next(*args):
    return _doc(emir("next", *args, "--format", "json"))


# ------------------------------------------------ simulator verification


def _no_read(path):
    os.chmod(path, 0)
    if os.access(path, os.R_OK):                    # root reads it anyway
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        pytest.skip("this user reads a file of mode 000 (root)")


@pytest.fixture
def unreadable_records():
    verify_sim.save_record(_record("xyce", "Xyce Release 7.10.0"))
    path = verify_sim.records_path()
    _no_read(path)
    yield path
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def test_an_unreadable_records_file_suggests_no_verification(unreadable_records, tmp_path,
                                                             monkeypatch):
    v = verify_sim.status(XYCE)
    assert (v["status"], v["reason"], v["command"]) == ("not verified",
                                                        "records unreadable", None)
    assert "read access" in v["note"]
    with collecting() as found:
        verify_sim.report_status(v, log=None)
    w = {x["code"]: x["message"] for x in found}
    assert "EMIR-W415" in w and "EMIR-W464" in w
    assert "verify-sim --sim xyce" not in w["EMIR-W464"].split("(give")[0]
    assert "NOT verified" in report.verification_text(v)
    # verify-sim refuses before it simulates; no simulator is needed for that
    p = emir("verify-sim", "--sim", "xyce", "--out", tmp_path / "v", "--format", "json")
    doc = _doc(p, rc=2)
    assert doc["code"] == "EMIR-E102" and not (tmp_path / "v").exists()
    # the plan asks the person for access instead of a verification
    monkeypatch.setattr(site, "tool", lambda name, explicit=None: (
        "/opt/fake/xyce" if name == "xyce" else None, "$PATH"))
    from emir.selftest import sample_paths
    dspf, proc = sample_paths()
    plan = P.build(P.Facts(dspf=dspf, process=proc, net="VDD", sim="xyce"))
    assert "verify-sim" not in [s["id"] for s in plan.steps]
    assert any(m["what"].startswith("read access to the verification records")
               for m in plan.missing)


def test_verify_sim_refuses_a_state_directory_it_cannot_write(tmp_path, monkeypatch):
    """Before it simulates: a record that could not be kept is no reason to
    run (EMIR-E105), whatever the simulator."""
    blocker = tmp_path / "file"
    blocker.write_text("x")
    env = {**os.environ, "EMIR_STATE_DIR": str(blocker / "sub")}
    doc = _doc(emir("verify-sim", "--sim", "ngspice", "--out", tmp_path / "v", "--format",
                    "json", env=env), rc=2)
    assert doc["code"] == "EMIR-E105" and not (tmp_path / "v").exists()


def test_a_result_of_unknown_version_gets_no_verification(tmp_path, monkeypatch):
    monkeypatch.setattr(site, "tool", lambda name, explicit=None: (None, "$PATH"))
    dspf = verify_sim.build("ngspice", str(tmp_path / "v"))[0]["dspf"]
    res = _ngspice_result(str(tmp_path / "rcc.raw"))
    with open(res) as f:
        text = f.read().replace("Command: ngspice-45+, Build (stand-in)\n", "")
    with open(res, "w") as f:
        f.write(text)                         # the sidecar still says ngspice
    from emir import waves
    v = verify_sim.status(waves.peek(res))
    assert (v["reason"], v["command"]) == ("version unknown", None)
    plan = P.build(P.Facts(dspf=dspf, process=PROC, cc_sim=res, period=1e-8))
    assert "verify-sim" not in [s["id"] for s in plan.steps]
    assert any("EMIR-W416" in a and "emir simulate --sim ngspice" in a for a in plan.advice)


def test_a_finished_run_plans_from_its_own_verification(tmp_path):
    dspf = verify_sim.build("ngspice", str(tmp_path / "v"))[0]["dspf"]
    res = _ngspice_result(str(tmp_path / "rcc.raw"))
    s = str(tmp_path / "s.json")
    args = [dspf, "--process", PROC, "--cc-sim", res, "--period", "10ns"]
    doc = _doc(emir("run", *args, "--summary-out", s, "--format", "json"))
    v = doc["simulator_verification"]
    assert (v["status"], v["reason"]) == ("not verified", "no record")
    # the run's own next steps verify the route
    step = next(x for x in doc["next_steps"] if x["command"].startswith("emir verify-sim"))
    assert step["command"] == "emir verify-sim --sim ngspice --format json"
    assert "needs_human" not in step                     # an open simulator: no licence
    jsonschema.validate(doc, schemas.get("run"))
    # so does the plan after it, before the report
    d = _next("--run-summary", s)
    ids = [x["id"] for x in d["steps"]]
    assert ids.index("verify-sim") < ids.index("report")
    # once a verification passed, the summary is older than it: no new one
    verify_sim.save_record(_record("ngspice", "ngspice-45+ : Circuit", "nutascii"))
    d = _next("--run-summary", s)
    assert "verify-sim" not in [x["id"] for x in d["steps"]]
    assert any("recorded here since" in a for a in d["advice"])


def test_a_format_no_verification_covers_gets_none_after_the_run(tmp_path):
    s = tmp_path / "s.json"
    v = {"status": "not verified", "reason": "format not covered", "simulator": "hspice",
         "version": "2024.09", "format": "csdf", "command": None, "note": "CSDF",
         "record": None, "current_kinds": None}
    _doc(emir("run", DSPF, "--process", PROC, "--currents", CUR, "--net", "VDD",
              "--summary-out", s, "--format", "json"))
    doc = json.load(open(s))
    doc["simulator_verification"] = {**doc["simulator_verification"], **v}
    s.write_text(json.dumps(doc))
    d = _next("--run-summary", s)
    assert "verify-sim" not in [x["id"] for x in d["steps"]]
    assert any("no emir verify-sim changes that" in a for a in d["advice"])


# ---------------------------------------------- the examples' output directory

STUB = """#!/bin/sh
printf '%s\\n' "$*" >> "$STUB_LOG"
case "$1" in -c) exit 0 ;; esac
exit 3
"""
EXAMPLES = sorted(x for x in os.listdir(os.path.join(ROOT, "examples"))
                  if os.path.isfile(os.path.join(ROOT, "examples", x, "run.sh")))


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not installed")
@pytest.mark.parametrize("name", EXAMPLES)
def test_every_example_takes_its_output_directory_as_an_argument(tmp_path, name):
    """`run.sh OUT_DIR` writes into OUT_DIR, whatever else the script reads
    (`OUT`, `EMIR_EXAMPLE_OUT` stay the fallback). emir and Python are a stub
    that logs its arguments and stops the script at its first real step."""
    stub = tmp_path / "stub"
    stub.write_text(STUB)
    stub.chmod(0o755)
    log, out = tmp_path / "log", tmp_path / "out"
    env = {k: v for k, v in os.environ.items() if k not in ("OUT", "EMIR_EXAMPLE_OUT")}
    env.update(EMIR=str(stub), PYTHON=str(stub), EMIR_PYTHON=str(stub), STUB_LOG=str(log))
    p = subprocess.run(["bash", os.path.join(ROOT, "examples", name, "run.sh"), str(out)],
                       env=env, cwd=str(tmp_path), capture_output=True, text=True,
                       timeout=120)
    assert "unknown option" not in p.stderr, p.stderr
    logged = log.read_text() if log.exists() else ""
    assert (out.is_dir() and any(out.iterdir())) or str(out) in logged, \
        (name, p.returncode, p.stderr[-800:], logged[-800:])


def _path_without(*tools) -> str:
    """$PATH without the directories that hold any of `tools` (a GitHub
    runner has no Xyce or ngspice)."""
    keep = [d for d in os.environ.get("PATH", "").split(os.pathsep)
            if d and not any(os.path.exists(os.path.join(d, t)) for t in tools)]
    return os.pathsep.join(keep)


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not installed")
@pytest.mark.parametrize("name", EXAMPLES)
def test_every_example_takes_its_output_directory_without_simulators(tmp_path, name):
    """The same as the runner sees it: no Xyce or ngspice on PATH. A run that
    skips writes why into OUT_DIR (example 06 once exited 0 having written
    nothing there, so the CI core job failed while the local one passed)."""
    stub = tmp_path / "stub"
    stub.write_text(STUB)
    stub.chmod(0o755)
    log, out = tmp_path / "log", tmp_path / "out"
    env = {k: v for k, v in os.environ.items() if k not in ("OUT", "EMIR_EXAMPLE_OUT")}
    env.update(EMIR=str(stub), PYTHON=str(stub), EMIR_PYTHON=str(stub), STUB_LOG=str(log),
               PATH=_path_without("Xyce", "ngspice"))
    p = subprocess.run([shutil.which("bash"), os.path.join(ROOT, "examples", name, "run.sh"),
                        str(out)], env=env, cwd=str(tmp_path), capture_output=True,
                       text=True, timeout=120)
    logged = log.read_text() if log.exists() else ""
    assert (out.is_dir() and any(out.iterdir())) or str(out) in logged, \
        (name, p.returncode, p.stdout[-800:], p.stderr[-800:], logged[-800:])
