"""Where features meet, case by case (each found by running them together):

* a self-consistent run with --dynamic: the dynamic droop and decap-aware EM
  come from the loop's reported pass, and the report, the chart's note, the
  console line and `dynamic.source` say so (open loop only when pass 0 is
  reported);
* `emir next` with --abstract refuses --dynamic and an --em-basis that needs
  it (EMIR-E811), as `check` and `run` do; after a chip-level run,
  `--run-summary` reads its blocks back and advises no --dynamic;
* the simulator verification: a records file this user may not read, a
  result whose version is unknown, and a finished run (`--run-summary`, the
  run's own next steps) get a verification only where one can help;
* the self-consistent loop refuses a later pass's foreign result before
  pass 0 simulates;
* every example's run.sh takes its output directory as an argument.

Every number is invented or computed here; the simulator cases skip when
the simulator is not installed (one simulator process at a time).
"""
import contextlib
import io
import json
import os
import shutil
import stat
import subprocess

import jsonschema
import pytest
from _helpers import CUR, DSPF, PROC, ROOT, emir
from test_decap import strap_run
from test_merge_interplay import _quiet, chip  # noqa: F401 -- `chip` is a fixture
from test_verify_sim import XYCE, _ngspice_result, _record

from emir import report, schemas, site, verify_sim
from emir import plan as P
from emir.errors import EmirError, collecting

EX12 = os.path.join(ROOT, "examples", "12_self_consistent")


def _doc(p, rc=0):
    assert p.returncode == rc, (p.returncode, p.stdout[-1500:], p.stderr[-1500:])
    return json.loads(p.stdout)


# ------------------------------------------- self-consistent x --dynamic


def _sc(reported):
    """A converged loop's `self_consistency` that reported pass `reported`."""
    passes = [{"pass": k, "droop_change_mV": 10.0 / 10 ** k, "ir_drop_mV": 5.0 - k * 0.1,
               "worst_ratio": 1.0, "current_change": None if k == 0 else 0.01}
              for k in range(3)]
    return {"simulator": "xyce", "vdd": 1.2, "rail": None, "tol_mV": 0.1, "max_passes": 8,
            "converged": reported > 0, "diverged": False, "reported_pass": reported,
            "n_passes": 3, "passes": passes}


@pytest.mark.parametrize("reported", [2, 0])
def test_the_dynamic_droop_is_worded_from_the_reported_pass(tmp_path, reported):
    """The dynamic solve takes the reported pass's currents: a converged
    loop's droop is not open loop, a loop that reported pass 0 is."""
    from emir.bigblock import print_dynamic
    from emir.scene import run_scene
    path = str(tmp_path / "s.json")
    res, _doc0 = strap_run(tmp_path, summary=path)
    with open(path) as f:
        s = json.load(f)
    s["nets"][0]["self_consistency"] = _sc(reported)
    md = report.markdown(report.build(s, path))
    ir = md.split("## IR drop per power net", 1)[1].split("\n## ", 1)[0]
    dyn = md.split("## Decap-aware EM", 1)[1].split("\n## ", 1)[0]
    res.solved["VDD"]["self_consistency"] = _sc(reported)
    note = run_scene(res.solved, res.dspf, res.proc)["notes"][0]
    with contextlib.redirect_stdout(io.StringIO()) as o:
        print_dynamic(res.solved["VDD"]["dynamic"], res.solved["VDD"]["ir_drop"],
                      res.solved["VDD"]["self_consistency"])
    if reported:
        assert "dynamic (open loop)" not in ir and "open loop" not in ir
        assert "pass 2 of the self-consistent loop, each pin fed its droop waveform" in ir
        assert "Open loop like every cc-grid number" not in dyn
        assert "self-consistent loop's reported pass (VDD: pass 2)" in dyn
        assert "dynamic droop (pass 2 of the self-consistent loop)" in note
        assert "(pass 2 of the self-consistent loop, " in o.getvalue()
    else:
        assert "dynamic (open loop)" in ir and "Open loop like every cc-grid number" in dyn
        assert "dynamic droop (open loop)" in note and "(open loop, " in o.getvalue()


SIMS = [pytest.param(s, marks=pytest.mark.skipif(site.tool(s)[0] is None,
                                                 reason=f"{s} not installed"))
        for s in ("xyce", "ngspice")]


@pytest.mark.parametrize("sim", SIMS)
def test_a_self_consistent_dynamic_run_on_a_simulator(tmp_path, sim):
    """Example 12's loop with --dynamic: it converges after pass 0, so its
    dynamic droop is the reported pass's, and nothing calls it open loop
    except the loop's own pass 0 (the report's IR "against ... open loop")."""
    s = str(tmp_path / "sc.json")
    p = emir("run", "biasrail.dspf", "--process", PROC, "--net", "VDD", "--mode", "cc-grid",
             "--self-consistent", "--sim", sim, "--netlist", "tb_cc.cir", "--vdd", "1.2",
             "--period", "1e-9", "--droop-file", tmp_path / "sc" / "droop.inc",
             "--summary-out", s, "--dynamic", cwd=EX12)
    assert p.returncode == 0, p.stderr[-2000:]
    doc = json.load(open(s))
    n = doc["nets"][0]
    k = n["self_consistency"]["reported_pass"]
    assert n["self_consistency"]["converged"] and k > 0
    assert f"pass {k} of the self-consistent loop" in n["dynamic"]["source"]
    assert "open loop" not in n["dynamic"]["source"]
    md = _doc(emir("report", "--from", s, "--format", "json"))
    text = emir("report", "--from", s).stdout
    assert md["ir_by_power_net"][0]["dynamic"]["source"] == n["dynamic"]["source"]
    assert "dynamic (open loop)" not in text and "Open loop like every" not in text
    assert f"pass {k} of the self-consistent loop" in text
    assert f"(pass {k} of the self-consistent loop, " in p.stdout


# --------------------------------------------- emir next x --abstract


def _next(*args):
    return _doc(emir("next", *args, "--format", "json"))


@pytest.mark.parametrize("extra", [["--dynamic"], ["--em-basis", "worst"],
                                   ["--em-basis", "decap-aware"]])
def test_next_refuses_the_dynamic_solve_with_abstracts(chip, extra):  # noqa: F811
    d = _next("--dspf", *chip.top, *extra)
    miss = [m for m in d["missing"] if "with --abstract" in m["what"]]
    assert miss and miss[0]["why"].startswith("EMIR-E811")
    ids = [s["id"] for s in d["steps"]]
    assert "check" not in ids and "run" not in ids
    assert not any("--dynamic" in s["command"] for s in d["steps"])
    # what the plan no longer prints, the check refuses
    rc, out, _err = _quiet(["check", *chip.top, *extra, "--format", "json"])
    assert rc == 2


def test_next_keeps_a_static_basis_with_abstracts(chip):  # noqa: F811
    d = _next("--dspf", *chip.top, "--em-basis", "decap-free")
    assert not d["missing"]
    run = next(s for s in d["steps"] if s["id"] == "run")
    assert "--em-basis decap-free" in run["command"] and "--dynamic" not in run["command"]
    assert any("chip-level run (--abstract) has no dynamic solve" in a for a in d["advice"])
    assert not any("--dynamic would add" in a for a in d["advice"])


def test_next_after_a_chip_level_run_reads_its_blocks_back(chip, tmp_path):  # noqa: F811
    s, pv = str(tmp_path / "top.json"), str(tmp_path / "pv.json")
    rc, out, err = _quiet(["run", *chip.top, "--budget-mv", "15", "--port-voltages-out", pv,
                           "--summary-out", s, "--format", "json"])
    assert rc == 0, err[-1500:]
    d = _next("--run-summary", s)
    assert not d["missing"], d["missing"]                 # no "--mode rcc" (EMIR-E417)
    assert d["mode"]["chosen"] == "cc-grid" and "hierarchical" in d["mode"]["why"]
    ids = [x["id"] for x in d["steps"]]
    assert "check" not in ids and "run" not in ids and ids[-1] == "report"
    ann = next(x for x in d["steps"] if x["id"] == "annotate:XPLL")
    assert f"{pv}@XPLL" in ann["command"] and ann["after"] == []
    assert not any("re-run it with --dynamic" in a or "black-boxed block" in a
                   for a in d["advice"])
    assert any("chip-level run (--abstract) has no dynamic solve" in a for a in d["advice"])
    d = _next("--run-summary", s, "--em-basis", "worst")
    assert any("with --abstract" in m["what"] for m in d["missing"])
    assert not any(x["id"] == "run" for x in d["steps"])


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
    plan = P.build(P.Facts(dspf=dspf, process=proc, net="VDD", mode="rcc", sim="xyce"))
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
    res = _ngspice_result(str(tmp_path / "cc.raw"))
    with open(res) as f:
        text = f.read().replace("Command: ngspice-45+, Build (stand-in)\n", "")
    with open(res, "w") as f:
        f.write(text)                         # the sidecar still says ngspice
    from emir import waves
    v = verify_sim.status(waves.peek(res))
    assert (v["reason"], v["command"]) == ("version unknown", None)
    plan = P.build(P.Facts(dspf=dspf, process=PROC, cc_sim=res, mode="cc-grid"))
    assert "verify-sim" not in [s["id"] for s in plan.steps]
    assert any("EMIR-W416" in a and "emir simulate --sim ngspice" in a for a in plan.advice)


def test_a_finished_run_plans_from_its_own_verification(tmp_path):
    dspf = verify_sim.build("ngspice", str(tmp_path / "v"))[0]["dspf"]
    res = _ngspice_result(str(tmp_path / "cc.raw"))
    s = str(tmp_path / "s.json")
    doc = _doc(emir("run", dspf, "--process", PROC, "--cc-sim", res, "--mode", "cc-grid",
                    "--summary-out", s, "--format", "json"))
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
         "record": None, "pin_kinds": None}
    _doc(emir("run", DSPF, "--process", PROC, "--currents", CUR, "--net", "VDD",
              "--summary-out", s, "--format", "json"))
    doc = json.load(open(s))
    doc["simulator_verification"] = {**doc["simulator_verification"], **v}
    s.write_text(json.dumps(doc))
    d = _next("--run-summary", s)
    assert "verify-sim" not in [x["id"] for x in d["steps"]]
    assert any("no emir verify-sim changes that" in a for a in d["advice"])


# ------------------------------------------- the self-consistent loop's files


def test_a_later_pass_s_foreign_result_is_refused_before_pass_0(tmp_path):
    from emir import selfconsist
    from emir.dspf import parse
    d = parse(DSPF)
    net = next(iter(n for n in d.nets if d.net_taps(n)))

    class Driver:
        relative = True
        sim = "xyce"

        def __init__(self):
            self.ran = []

        def pass_result(self, k):
            return str(tmp_path / f"pass{k}.raw")

        def pins(self, net_=None):
            # the waveform loop asks which pins the split feeds
            return sorted(d.pin_groups(net))

        def run(self, k, fed=None, timeout=None):
            self.ran.append(k)
            raise RuntimeError("simulated")

    (tmp_path / "pass2.raw").write_text("foreign\n")
    drv = Driver()
    with pytest.raises(EmirError) as e:
        selfconsist.converge(None, str(tmp_path / "droop.inc"), d, net, None, net, 1.2,
                             max_passes=4, driver=drv)
    assert e.value.code == "EMIR-E526" and "pass2.raw" in e.value.message
    assert drv.ran == [] and (tmp_path / "pass2.raw").read_text() == "foreign\n"
    # several nets closed in one loop (`converge_nets`) are guarded the same way
    drv = Driver()
    with pytest.raises(EmirError) as e:
        selfconsist.converge_nets(drv, d, [net], None, {net: net}, {net: 1.2},
                                  max_passes=4)
    assert e.value.code == "EMIR-E526" and "pass2.raw" in e.value.message
    assert drv.ran == []
    # beyond --max-passes it is not in the way; emir's own stamped file never is
    for n_passes in (2, 4):
        if n_passes == 4:
            (tmp_path / "pass2.raw.emir").write_text(selfconsist.HEADER + ": pass 2\n")
        drv = Driver()
        with pytest.raises(RuntimeError):
            selfconsist.converge(None, str(tmp_path / "droop.inc"), d, net, None, net, 1.2,
                                 max_passes=n_passes, driver=drv)
        assert drv.ran == [0]


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
