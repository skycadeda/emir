"""`emir verify-sim` and what runs, plans and reports do with its records.

Support for a simulator is proven on the machine that uses it: these tests
run the verification for real with Xyce and ngspice when they are installed,
and with stand-ins for HSPICE and Spectre (tests/_sim_fakes.py: not the
simulators, so a pass here says only that EMIR's side of the route is
consistent). Every test has its own state directory (tests/conftest.py).
"""
import json
import os

import pytest
from _helpers import PROC, emir

from emir import mcp_tools, verify_sim
from emir import plan as P

pytestmark = []

#: what a verification checks (module doc of emir/verify_sim.py): the version;
#: per case the read, the format, the simulator named, nothing missing (4 x 6);
#: dc and pulse rcc 16 branch currents each, dev rcc 19; dc cc 4 loads, pulse cc
#: 4 x 3, dev cc 7 pins; the run's worst ratio, IR and verdict (3 x 6); the
#: pulse cases' Rm1 and Rc3 ratios (6 x 2); rcc against cc-grid (6 x 3); the
#: self-consistent loop (7: converged, pass 0 IR and ratio, falling droop
#: change, closed-loop IR, worst ratio and Rc3 ratio)
N_CHECKS = 1 + 24 + 16 + 16 + 19 + 4 + 12 + 7 + 18 + 12 + 18 + 7


def _doc(p):
    assert p.stdout.strip(), p.stderr
    return json.loads(p.stdout)


def _schema_ok(doc, name):
    js = pytest.importorskip("jsonschema")
    from emir import schemas
    js.validate(doc, schemas.get(name))


# ------------------------------------------------------------ records and versions


def test_version_keys_compare_one_release_however_it_is_spelt():
    k = verify_sim.version_key
    assert k("19.1.0.237.isr4 64bit") == k("spectre (ver. 19.1.0.237) 64bit") == "19.1.0"
    assert k("Xyce DEVELOPMENT-202609282301-(Release-7.10.0-161-g10e78997)") == "7.10.0"
    assert k("ngspice-45+ : Circuit level simulation program") == "45"
    assert k("ngspice-45+, Build Wed") == "45"
    assert k(None) is None and k("no digits") is None


def _record(sim, version, fmt=None, passed=True, **kw):
    """A valid record (what `emir verify-sim` writes: one check per kind of
    current it covers, and `covers`), with `kw` on top."""
    from emir import __version__
    checks = [{"name": f"{k} check", "ok": passed, "kind": k}
              for k in verify_sim.coverable(sim)]
    rec = {"schema": "emir.verify_sim/1", "simulator": sim, "version": version,
           "format": fmt or verify_sim.FORMAT[sim], "sim_args": None,
           "emir_version": __version__, "date": "2026-10-02T00:00:00Z", "host": None,
           "passed": passed, "covers": verify_sim.covers_of(checks), "checks": checks}
    rec.update(kw)
    return rec


XYCE = {"simulator": "xyce", "simulator_version": "Xyce Release 7.10.0", "format": "nutascii"}


def test_a_run_is_verified_only_by_a_matching_passing_record(tmp_path):
    meta = dict(XYCE)
    assert verify_sim.status(meta)["status"] == "not verified"
    verify_sim.save_record(_record("xyce", "Xyce Release 7.9.0"))
    st = verify_sim.status(meta)
    assert st["status"] == "not verified" and st["reason"] == "no record"
    assert st["command"] == "emir verify-sim --sim xyce"
    verify_sim.save_record(_record("xyce", "Xyce 7.10.0-opensource"))
    st = verify_sim.status(meta)
    assert st["status"] == "verified" and st["version_checked"] and st["reason"] is None
    assert st["record"]["version"] == "Xyce 7.10.0-opensource"
    doc = json.load(open(verify_sim.records_path()))
    assert doc["schema"] == "emir.verified/1" and len(doc["records"]) == 2
    _schema_ok(doc, "verified")


def test_the_latest_record_decides_a_later_fail_cancels_a_pass():
    meta = dict(XYCE)
    verify_sim.save_record(_record("xyce", "7.10.0"))
    assert verify_sim.status(meta)["status"] == "verified"
    verify_sim.save_record(_record("xyce", "7.10.0", passed=False))
    st = verify_sim.status(meta)
    assert (st["status"], st["reason"]) == ("not verified", "failed")
    from emir.errors import collecting
    with collecting() as notes:
        verify_sim.report_status(st, log=None)
    assert [n["code"] for n in notes] == ["EMIR-W414"]
    # ... and a pass after the failure counts again
    verify_sim.save_record(_record("xyce", "7.10.0"))
    assert verify_sim.status(meta)["status"] == "verified"
    # another version's failure does not touch this one
    verify_sim.save_record(_record("xyce", "7.9.0", passed=False))
    assert verify_sim.status(meta)["status"] == "verified"


def test_the_key_holds_the_simulator_arguments():
    verify_sim.save_record(_record("spectre", "19.1.0.237"))
    verify_sim.save_record(_record("spectre", "19.1.0.237", sim_args="+aps", passed=False))
    base = {"simulator": "spectre", "simulator_version": "19.1.0.237.isr4 64bit",
            "format": "psfascii"}
    # a result simulated with +aps (its sidecar says so): the +aps record decides
    st = verify_sim.status({**base, "sim_args": "+aps"})
    assert (st["status"], st["reason"]) == ("not verified", "failed")
    assert st["sim_args"] == "+aps"
    # without arguments: the plain record
    st = verify_sim.status({**base, "sim_args": ""})
    assert st["status"] == "verified" and st["sim_args_known"] and st["note"] is None
    # a result that does not say: matched as given none, and the summary says so
    st = verify_sim.status(base)
    assert st["status"] == "verified" and not st["sim_args_known"] and st["note"]
    # the arguments are compared as the shell splits them
    verify_sim.save_record(_record("spectre", "19.1.0", sim_args="+preset=cx  +postlpreset=off"))
    st = verify_sim.status({**base, "sim_args": "'+preset=cx' +postlpreset=off"})
    assert st["status"] == "verified"
    assert verify_sim.status({**base, "sim_args": "+postlpreset=off +preset=cx"})[
        "reason"] == "no record"


@pytest.mark.parametrize("change", [
    {"passed": "false"}, {"passed": 1}, {"passed": None},
    {"checks": [{"name": "x", "ok": False}]},                 # passed true, a check failed
    {"checks": []},                                            # passed true with no checks
    {"checks": [{"name": "x", "ok": "yes"}]},
    {"schema": None}, {"schema": "emir.run/1"},
    {"format": "csdf"}, {"version": None}, {"version": "no digits"},
    {"emir_version": "dev"}, {"date": "yesterday"}, {"simulator": "eldo"},
    {"host": 7}, {"sim_args": ["+aps"]},
])
def test_an_invalid_record_counts_as_none(change):
    rec = {**_record("xyce", "7.10.0"), **change}
    assert verify_sim.record_problem(rec)
    verify_sim.save_record(rec)
    st = verify_sim.status(dict(XYCE))
    assert st["status"] == "not verified" and st["ignored_records"] == 1
    from emir.errors import collecting
    with collecting() as notes:
        verify_sim.report_status(st, log=None)
    assert [n["code"] for n in notes] == ["EMIR-W415", "EMIR-W464"]
    js = pytest.importorskip("jsonschema")
    from emir import schemas
    item = schemas.get("verified")["properties"]["records"]["items"]
    if change.get("checks") in ([], [{"name": "x", "ok": False}]) or \
            change.get("version", "x") in (None, "no digits"):
        js.validate(rec, item)       # only record_problem sees these: passed against
        #                              the checks, a passing record's version
    else:
        with pytest.raises(js.ValidationError):
            js.validate(rec, item)


@pytest.mark.parametrize("drop", verify_sim.RECORD_REQUIRED)
def test_a_record_without_a_required_field_counts_as_none(drop):
    rec = _record("xyce", "7.10.0")
    del rec[drop]
    assert verify_sim.record_problem(rec)
    verify_sim.save_record(rec)
    assert verify_sim.status(dict(XYCE))["status"] == "not verified"


def test_a_record_of_another_emir_minor_version_is_stale(tools):
    verify_sim.save_record(_record("xyce", "7.10.0", emir_version="1.0.0"))
    st = verify_sim.status(dict(XYCE))
    assert (st["status"], st["reason"]) == ("not verified", "stale")
    assert "1.0.0" in st["note"] and st["command"] == "emir verify-sim --sim xyce"
    from emir.errors import collecting
    with collecting() as notes:
        verify_sim.report_status(st, log=None)
    assert [n["code"] for n in notes] == ["EMIR-W464"]
    # emir next verifies again, and says why
    from emir.selftest import sample_paths
    dspf, proc = sample_paths()
    tools(xyce=True)
    plan = P.build(P.Facts(dspf=dspf, process=proc, net="VDD", mode="rcc"))
    v = next(s for s in plan.steps if s["id"] == "verify-sim")
    assert "stale" in v["why"] and "1.0.0" in v["why"]
    # the same minor version, another patch: not stale
    from emir import __version__
    major, minor_ = __version__.split(".")[:2]
    verify_sim.save_record(_record("xyce", "7.10.0", emir_version=f"{major}.{minor_}.99"))
    assert verify_sim.status(dict(XYCE))["status"] == "verified"


def test_a_result_without_its_version_is_never_matched():
    verify_sim.save_record(_record("hspice", "fakehspice 2023.12"))
    st = verify_sim.status({"simulator": "hspice", "format": "tr0ascii"})
    assert (st["status"], st["reason"]) == ("not verified", "version unknown")
    assert st["note"].startswith("not verified: version unknown") and st["record"] is None
    from emir.errors import collecting
    with collecting() as notes:
        verify_sim.report_status(st, log=None)
    assert [n["code"] for n in notes] == ["EMIR-W416"]
    from emir import report
    md = report.markdown(report.build({"schema": "emir.run/1", "verdict": "CLEAN",
                                       "nets": [], "counts": {},
                                       "simulator_verification": st}, "s.json"))
    assert "does not say which version" in md


def test_a_record_of_another_host_does_not_count(monkeypatch):
    monkeypatch.setattr(verify_sim, "this_host", lambda: "here")
    verify_sim.save_record(_record("xyce", "7.10.0", host="elsewhere"))
    st = verify_sim.status(dict(XYCE))
    assert (st["status"], st["reason"]) == ("not verified", "other host")
    verify_sim.save_record(_record("xyce", "7.10.0", host="here"))
    assert verify_sim.status(dict(XYCE))["status"] == "verified"
    # a failure kept on another host does not cancel this host's pass
    verify_sim.save_record(_record("xyce", "7.10.0", host="elsewhere", passed=False))
    assert verify_sim.status(dict(XYCE))["status"] == "verified"
    # no host recorded: counts (the state directory is this machine's)
    verify_sim.save_record(_record("xyce", "7.10.0", passed=False))
    assert verify_sim.status(dict(XYCE))["reason"] == "failed"


def test_info_and_next_follow_the_latest_record(tools):
    from emir.selftest import sample_paths
    dspf, proc = sample_paths()
    tools(ngspice=True)
    verify_sim.save_record(_record("ngspice", "ngspice-45+"))
    verify_sim.save_record(_record("ngspice", "ngspice-45+", passed=False))
    plan = P.build(P.Facts(dspf=dspf, process=proc, net="VDD", mode="rcc"))
    v = next(s for s in plan.steps if s["id"] == "verify-sim")
    assert "FAILED" in v["why"]
    assert verify_sim.verified_route("ngspice", "nutascii") is None


def test_unknown_and_not_applicable():
    assert verify_sim.status({})["status"] == "unknown simulator"
    assert verify_sim.status({"simulator": "eldo"})["status"] == "unknown simulator"
    assert verify_sim.status({}, sim_hint="hspice")["detected_from"] == "--sim"
    assert verify_sim.status(applicable=False)["status"] == "not applicable"


def test_an_unwritable_state_directory_is_a_warning(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv("EMIR_STATE_DIR", str(blocker / "sub"))
    from emir.errors import collecting
    with collecting() as notes:
        assert verify_sim.save_record(_record("xyce", "1.0")) is None
    assert [n["code"] for n in notes] == ["EMIR-W467"]
    assert verify_sim.load_records() == []


# ------------------------------------------------------------ the command


def _verify(tmp_path, sim, *extra, env=None):
    return emir("verify-sim", "--sim", sim, "--out", tmp_path / sim, *extra,
                "--format", "json", env=env)


@pytest.mark.parametrize("sim", ["xyce", "ngspice"])
def test_open_simulators_verify_for_real(tmp_path, sim):
    from emir import site
    if not site.tool(sim)[0]:
        pytest.skip(f"{sim} is not installed here")
    p = _verify(tmp_path, sim)
    doc = _doc(p)
    assert p.returncode == 0 and doc["passed"] and doc["ran"], \
        [c for c in doc["checks"] if not c["ok"]]
    assert len(doc["checks"]) == N_CHECKS and doc["code"] is None and doc["host"] is None
    # the self-consistent loop converged on the closed-loop answer, pass 0 the open loop
    sc = [c for c in doc["checks"] if c["name"].startswith("selfconsistent:")]
    assert len(sc) == 7 and all(c["ok"] for c in sc)
    # its testbench is emir's: it sets the reltol the loop asks for (no EMIR-W540)
    assert "EMIR-W540" not in p.stderr
    assert doc["format"] == "nutascii" and doc["version"]
    _schema_ok(doc, "verify_sim")
    recs = verify_sim.load_records()
    assert [r["simulator"] for r in recs] == [sim] and recs[0]["passed"]
    # a run on the verification's own cc result is now verified
    res = os.path.join(doc["out_dir"], "results", "dc_cc-grid.raw")
    r = emir("run", os.path.join(doc["out_dir"], "verify_r.dspf"), "--process", PROC,
             "--cc-sim", res, "--mode", "cc-grid", "--format", "json")
    s = _doc(r)
    assert s["simulator_verification"]["status"] == "verified"
    assert any(n["code"] == "EMIR-I463" for n in s["notices"])


@pytest.mark.parametrize("sim", ["xyce", "ngspice", "hspice", "spectre"])
def test_the_self_consistent_case_sets_the_loops_reltol(tmp_path, sim):
    """verify-sim warned EMIR-W540 about the testbench it had just written
    itself: its self-consistent case now sets reltol 1e-4 in the simulator's
    own syntax, which the loop's reader takes (and no other case changes)."""
    from emir.selfconsist import RELTOL_MAX, reltol_of
    files = verify_sim.build(sim, str(tmp_path))
    sc = next(f for f in files if f["case"] == verify_sim.SC_CASE)
    v, line = reltol_of([open(sc["testbench"]).read()], sim)
    assert v is not None and v <= RELTOL_MAX, line
    for f in files[:-1]:
        assert "reltol" not in open(f["testbench"]).read()


@pytest.mark.parametrize("sim", ["hspice", "spectre"])
def test_licensed_simulators_are_planned_without_run(tmp_path, monkeypatch, sim):
    monkeypatch.delenv("HSPICE_BIN", raising=False)
    monkeypatch.delenv("SPECTRE_BIN", raising=False)
    p = _verify(tmp_path, sim)
    doc = _doc(p)
    assert p.returncode == 0 and doc["ran"] is False and doc["passed"] is None
    assert len(doc["cases"]) == 7 and all(c["command"] for c in doc["cases"])
    sc = doc["cases"][6]
    assert sc["case"] == "selfconsistent" and "--self-consistent" in sc["emir_run"]
    assert "--run" not in sc["emir_run"] and sc["mode"] == "cc-grid"
    assert doc["next_steps"][0]["command"].endswith("--run --format json")
    assert "EMIR-I661" in p.stderr
    assert verify_sim.load_records() == []                   # nothing to record
    _schema_ok(doc, "verify_sim")
    tb = open(doc["cases"][1]["testbench"]).read()
    if sim == "spectre":
        assert "simulator lang=spice" in tb and "emirtran tran stop=40n" in tb
    else:
        assert ".tran 10p 40n" in tb


@pytest.mark.parametrize("sim", ["hspice", "spectre"])
def test_licensed_routes_with_stand_ins(tmp_path, monkeypatch, sim):
    from _sim_fakes import install
    install(tmp_path, monkeypatch)
    p = _verify(tmp_path, sim, "--run", "--record-host")
    doc = _doc(p)
    assert p.returncode == 0 and doc["passed"], [c for c in doc["checks"] if not c["ok"]]
    assert doc["version"].startswith("fake" + sim) and doc["host"]
    assert doc["format"] == {"hspice": "tr0ascii", "spectre": "psfascii"}[sim]
    rec = verify_sim.load_records()[-1]
    assert rec["simulator"] == sim and rec["passed"] and rec["host"] == doc["host"]


def test_a_wrong_sign_fails_the_verification(tmp_path, monkeypatch):
    from _sim_fakes import install
    install(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_SIGN", "-1")
    p = _verify(tmp_path, "hspice", "--run")
    doc = _doc(p)
    assert p.returncode == 70 and doc["passed"] is False and doc["code"] == "EMIR-E663"
    bad = [c["name"] for c in doc["checks"] if not c["ok"]]
    assert any("positive from the feed" in b for b in bad)
    assert any("positive into the load" in b for b in bad)
    assert verify_sim.load_records()[-1]["passed"] is False
    assert verify_sim.status({"simulator": "hspice", "format": "tr0ascii"})["status"] == \
        "not verified"


#: the forms the dev case's pins are saved with, per simulator (the sidecar
#: key, upper case, and its sign): exactly what emir's cc route writes
DEV_FORMS = {
    "xyce": {"IS(XI0:MP0)": ("MP0:s", 1), "IB(XI0:MP0)": ("MP0:b", 1),
             "ID(XI0:MN1)": ("MN1:d", 1), "IG(XI0:MN1)": ("MN1:g", 1),
             "I(XI0:RL2)": ("RL2:2", -1)},
    "ngspice": {"I(@R.XI0.RL2[I])": ("RL2:2", -1)},
    "hspice": {"I3(XI0.MP0)": ("MP0:s", -1), "I4(XI0.MP0)": ("MP0:b", 1),
               "I1(XI0.MN1)": ("MN1:d", 1), "I2(XI0.MN1)": ("MN1:g", 1),
               "I1(XI0.RL2)": ("RL2:2", -1)},
}


def _dev_sidecar(doc):
    return json.load(open(os.path.join(doc["out_dir"], "results", "dev_cc-grid." + (
        "tr0" if doc["simulator"] == "hspice" else "raw") + ".names")))


def _assert_dev_forms(doc):
    """The dev case was saved with the cc route's forms, and every pin's
    current was checked against its known value and direction."""
    sim = doc["simulator"]
    side = _dev_sidecar(doc)["map"]
    for key, (pin, sign) in DEV_FORMS[sim].items():
        assert side.get(key) == [pin, sign], (key, side.get(key))
    probed = {v[0] for k, v in side.items() if "VEMIR" in k}
    want = {"XM3:d", "XM3:g"} | ({"MP0:s", "MP0:b", "MN1:d", "MN1:g"}
                                 if sim == "ngspice" else set())
    assert probed == want
    if sim == "hspice":
        assert "I3(XI0.MP0)" in _dev_sidecar(doc)["kcl"]
    names = {c["name"] for c in doc["checks"]}
    for pin in verify_sim.DEV_PINS:
        assert any(n.startswith(f"dev_cc-grid: {pin} current into the device") for n in names), pin
    assert {"dev_rcc: Rb0 average current (positive from the feed)",
            "dev: rcc against cc-grid, Rc3 avg"} <= names


@pytest.mark.parametrize("sim", ["xyce", "ngspice"])
def test_the_dev_case_reads_real_device_terminals(tmp_path, sim):
    from emir import site
    if not site.tool(sim)[0]:
        pytest.skip(f"{sim} is not installed here")
    doc = _doc(_verify(tmp_path, sim))
    assert doc["passed"], [c for c in doc["checks"] if not c["ok"]]
    _assert_dev_forms(doc)
    side = _dev_sidecar(doc)
    assert side["simulator_version"] == doc["version"] and side["sim_args"] == ""


def test_the_licensed_dev_forms_with_stand_ins(tmp_path, monkeypatch):
    from _sim_fakes import install
    install(tmp_path, monkeypatch)
    doc = _doc(_verify(tmp_path, "hspice", "--run"))
    assert doc["passed"], [c for c in doc["checks"] if not c["ok"]]
    _assert_dev_forms(doc)
    doc = _doc(_verify(tmp_path, "spectre", "--run"))
    assert doc["passed"], [c for c in doc["checks"] if not c["ok"]]
    save = open(os.path.join(doc["out_dir"], "results", "dev_cc-grid.save.scs")).read()
    for pin in verify_sim.DEV_PINS:
        assert f"XI0.{pin}" in save, pin
    # Spectre's sidecar (beside the result directory): the facts, no map
    side = json.load(open(os.path.join(doc["out_dir"], "results", "dev_cc-grid.raw.names")))
    assert side == {"map": {}, "simulator": "spectre", "sim_args": "",
                    "simulator_version": doc["version"]}


def test_a_device_sign_error_fails_although_kcl_closes(tmp_path, monkeypatch):
    """Every terminal of every device written with the wrong sign: each
    device's currents still sum to zero, so KCL alone would accept them; the
    known source currents do not."""
    from _sim_fakes import install
    install(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_DEV_SIGN", "-1")
    p = _verify(tmp_path, "hspice", "--run")
    doc = _doc(p)
    assert p.returncode == 70 and doc["passed"] is False
    bad = {c["name"].split(" (")[0] for c in doc["checks"] if not c["ok"]}
    for pin in ("MP0:s", "MN1:d", "RL2:2", "XM3:d"):
        assert f"dev_cc-grid: {pin} current into the device" in bad
    assert not any(b.startswith(("dc", "pulse")) for b in bad)       # the loads are right
    assert "EMIR-W465" not in p.stderr                                  # KCL closed


def test_hspice_i3_out_of_the_device_is_set_by_kcl(tmp_path, monkeypatch):
    from _sim_fakes import install
    install(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_HSPICE_I3", "out")
    doc = _doc(_verify(tmp_path, "hspice", "--run"))
    assert doc["passed"], [c for c in doc["checks"] if not c["ok"]]


def test_a_later_failed_verification_cancels_the_pass_for_runs(tmp_path, monkeypatch):
    """A pass, then a failure of the same version; a
    run on a result of that version is not verified any more (EMIR-W414)."""
    from _sim_fakes import install
    install(tmp_path, monkeypatch)
    good = _doc(_verify(tmp_path / "a", "hspice", "--run"))
    assert good["passed"]
    run = ("run", os.path.join(good["out_dir"], "verify_r.dspf"), "--process", PROC,
           "--cc-sim", os.path.join(good["out_dir"], "results", "dc_cc-grid.tr0"), "--mode",
           "cc-grid", "--format", "json")
    s = _doc(emir(*run))
    assert s["simulator_verification"]["status"] == "verified"
    monkeypatch.setenv("FAKE_SIGN", "-1")
    assert _doc(_verify(tmp_path / "b", "hspice", "--run"))["passed"] is False
    s = _doc(emir(*run))
    v = s["simulator_verification"]
    assert (v["status"], v["reason"]) == ("not verified", "failed")
    codes = [n["code"] for n in s["notices"]]
    assert "EMIR-W414" in codes and "EMIR-I463" not in codes
    (tmp_path / "s.json").write_text(json.dumps(s))
    md = emir("report", "--from", tmp_path / "s.json").stdout
    assert "FAILED its verification" in md


def test_sim_args_key_the_verification_and_the_result(tmp_path, monkeypatch):
    from _sim_fakes import install
    install(tmp_path, monkeypatch)
    doc = _doc(_verify(tmp_path / "a", "hspice", "--run", "--sim-args", "-mt  2"))
    assert doc["passed"] and doc["sim_args"] == "-mt 2"
    assert verify_sim.load_records()[-1]["sim_args"] == "-mt 2"
    res = os.path.join(doc["out_dir"], "results", "dc_cc-grid.tr0")
    assert json.load(open(res + ".names"))["sim_args"] == "-mt 2"
    run = ("run", os.path.join(doc["out_dir"], "verify_r.dspf"), "--process", PROC,
           "--cc-sim", res, "--mode", "cc-grid", "--format", "json")
    assert _doc(emir(*run))["simulator_verification"]["status"] == "verified"
    # the same result, its sidecar saying it was simulated without them: no
    # record for that configuration
    side = json.load(open(res + ".names"))
    with open(res + ".names", "w") as f:
        json.dump({**side, "sim_args": ""}, f)
    v = _doc(emir(*run))["simulator_verification"]
    assert (v["status"], v["reason"]) == ("not verified", "no record")


def test_a_result_without_a_version_is_not_verified_by_a_run(tmp_path, monkeypatch):
    from _sim_fakes import install
    install(tmp_path, monkeypatch)
    doc = _doc(_verify(tmp_path, "hspice", "--run"))
    res = os.path.join(doc["out_dir"], "results", "dc_cc-grid.tr0")
    side = json.load(open(res + ".names"))
    del side["simulator_version"]                  # as `emir save-lines` writes it
    with open(res + ".names", "w") as f:
        json.dump(side, f)
    s = _doc(emir("run", os.path.join(doc["out_dir"], "verify_r.dspf"), "--process", PROC,
                  "--cc-sim", res, "--mode", "cc-grid", "--format", "json"))
    v = s["simulator_verification"]
    assert (v["status"], v["reason"]) == ("not verified", "version unknown")
    assert any(n["code"] == "EMIR-W416" for n in s["notices"])
    _schema_ok(s, "run")


# ------------------------------------------------------------ plans


@pytest.fixture
def tools(monkeypatch):
    from emir import site
    found = {}
    monkeypatch.setattr(site, "tool", lambda name, explicit=None: (
        (f"/opt/fake/{name}" if found.get(name) else None), "$PATH"))
    return lambda **kw: (found.clear(), found.update(kw))


def _ids(plan):
    return [s["id"] for s in plan.steps]


def test_plan_verifies_spectre_with_the_person_first(tools):
    from emir.selftest import sample_paths
    dspf, proc = sample_paths()
    tools(spectre=True)
    plan = P.build(P.Facts(dspf=dspf, process=proc, net="VDD", mode="rcc"))
    v = next(s for s in plan.steps if s["id"] == "verify-sim")
    assert v["command"] == "emir verify-sim --sim spectre --run"
    assert v["needs_human"] and v["licensed_tool"] == "spectre"
    assert _ids(plan).index("verify-sim") < _ids(plan).index("simulate")


def test_plan_skips_the_verification_when_a_record_passes(tools):
    from emir.selftest import sample_paths
    dspf, proc = sample_paths()
    tools(ngspice=True)
    verify_sim.save_record(_record("ngspice", "ngspice-45+", "nutascii"))
    plan = P.build(P.Facts(dspf=dspf, process=proc, net="VDD", mode="rcc"))
    assert "verify-sim" not in _ids(plan)
    assert any("verified on this machine" in a for a in plan.advice)


def test_plan_hspice_route_is_planned_then_run_with_the_person(tools):
    from emir.selftest import sample_paths
    dspf, proc = sample_paths()
    tools()
    plan = P.build(P.Facts(dspf=dspf, process=proc, net="VDD", sim="hspice", mode="rcc"))
    ids = _ids(plan)
    assert ids[1:4] == ["verify-sim", "simulate-plan", "simulate"]
    sp, run = plan.steps[2], plan.steps[3]
    assert "--sim hspice" in sp["command"] and "--run" not in sp["command"]
    assert not sp["needs_human"]
    assert run["command"].endswith("--run") and run["licensed_tool"] == "hspice"
    assert "emir_out/sim/result.tr0" in next(s for s in plan.steps
                                             if s["id"] == "check")["command"]
    assert any("hspice is not found" in a for a in plan.advice)


def _ngspice_result(path, n=4):
    """An ngspice-style nutascii result of the verification's CC netlist
    (`Command: ngspice-45+` in its header; probe currents named
    i(v.xi0.vemirK), mapped by its names sidecar)."""
    import numpy as np
    t = np.linspace(0, 40e-9, 9)
    names = [f"i(v.xi0.vemir{k})" for k in range(n)]
    L = ["Title: * stand-in", "Date: today", "Command: ngspice-45+, Build (stand-in)",
         "Plotname: Transient Analysis", "Flags: real", f"No. Variables: {n + 1}",
         f"No. Points: {len(t)}", "Variables:", "\t0\ttime\ttime"]
    L += [f"\t{k + 1}\t{x}\tcurrent" for k, x in enumerate(names)]
    L.append("Values:")
    for i, ti in enumerate(t):
        L.append(f" {i}\t{ti:.12e}")
        L += ["\t1.000000000000e-03"] * n
    with open(path, "w") as f:
        f.write("\n".join(L) + "\n")
    with open(path + ".names", "w") as f:
        json.dump({"map": {x.upper(): [f"ILOAD{k}:p", 1] for k, x in enumerate(names)},
                   "simulator": "ngspice"}, f)
    return path


def test_plan_verifies_the_simulator_of_a_finished_result(tools, tmp_path):
    tools(xyce=True)
    from emir.verify_sim import build
    dspf = build("ngspice", str(tmp_path / "v"))[0]["dspf"]
    res = _ngspice_result(str(tmp_path / "cc.raw"))
    plan = P.build(P.Facts(dspf=dspf, process=PROC, cc_sim=res, mode="cc-grid"))
    assert plan.mode["chosen"] == "cc-grid"
    ids = _ids(plan)
    assert ids.index("verify-sim") < ids.index("check") < ids.index("run")
    v = plan.steps[ids.index("verify-sim")]
    assert v["command"] == "emir verify-sim --sim ngspice" and not v["needs_human"]
    verify_sim.save_record(_record("ngspice", "ngspice-45+ : Circuit", "nutascii"))
    plan = P.build(P.Facts(dspf=dspf, process=PROC, cc_sim=res, mode="cc-grid"))
    assert "verify-sim" not in _ids(plan)
    # a result that does not say who wrote it: the plan asks for --sim
    os.remove(res + ".names")
    with open(res) as f:
        text = f.read().replace("Command: ngspice-45+, Build (stand-in)\n", "")
    with open(res, "w") as f:
        f.write(text.replace("i(v.xi0.vemir", "I(VEMIR"))
    with open(res + ".names", "w") as f:
        json.dump({"map": {f"I(VEMIR{k})": [f"ILOAD{k}:p", 1] for k in range(4)}}, f)
    plan = P.build(P.Facts(dspf=dspf, process=PROC, cc_sim=res, mode="cc-grid"))
    assert "verify-sim" not in _ids(plan)
    assert any("pass --sim" in a for a in plan.advice)


def test_next_takes_sim(tools):
    from emir.selftest import sample_paths
    dspf, proc = sample_paths()
    p = emir("next", "--dspf", dspf, "--process", proc, "--sim", "spectre", "--mode", "rcc",
             "--format", "json")
    doc = _doc(p)
    assert doc["inputs"]["sim"] == "spectre"
    v = next(s for s in doc["steps"] if s["id"] == "verify-sim")
    assert v["licensed_tool"] == "spectre" and v["needs_human"]


# ------------------------------------------------------------ MCP


def _gates(cmd, *argv):
    specs = mcp_tools.specs()
    return mcp_tools.gates(cmd, list(argv), specs[mcp_tools.tool_name(cmd)].parser)


def test_mcp_needs_confirm_for_a_licensed_simulator_run():
    g = _gates("verify-sim", "--sim", "spectre", "--run")
    assert g and g[0].licensed_tool == "spectre"
    assert _gates("verify-sim", "--sim", "spectre") == []      # plans only
    assert _gates("verify-sim", "--sim", "xyce") == []
    g = _gates("simulate", "tb.sp", "--sim", "hspice", "--dspf", "x.dspf", "--mode", "rcc",
               "--run")
    assert any(x.licensed_tool == "hspice" for x in g)
    assert not any(x.licensed_tool for x in _gates("simulate", "tb.sp", "--sim", "hspice",
                                                   "--dspf", "x.dspf", "--mode", "rcc"))
    # the self-consistent loop on a licensed simulator: only with run
    g = _gates("run", "x.dspf", "--netlist", "tb.sp", "--sim", "hspice", "--mode", "cc-grid",
               "--self-consistent", "--vdd", "1", "--run")
    assert any(x.licensed_tool == "hspice" for x in g)
    assert _gates("run", "x.dspf", "--netlist", "tb.sp", "--sim", "hspice", "--mode",
                  "cc-grid", "--self-consistent", "--vdd", "1") == []


def test_mcp_instructions_carry_the_disclaimer_and_the_verification():
    from emir import RESEARCH_DISCLAIMER
    from emir.mcp_server import instructions
    text = instructions("/w")
    assert RESEARCH_DISCLAIMER in text and "emir_verify_sim" in text and "HSPICE" in text


# ------------------------------------------------------------ report, info


def test_report_says_whether_the_simulator_is_verified(tmp_path):
    from emir import report
    base = {"schema": "emir.run/1", "verdict": "CLEAN", "nets": [], "counts": {}}
    for st, text in (("not verified", "NOT verified on this machine"),
                     ("verified", "verified on this machine"),
                     ("unknown simulator", "unknown simulator")):
        v = {"status": st, "simulator": "xyce", "version": "7.10.0", "format": "nutascii",
             "record": {"date": "2026-10-02"}, "command": "emir verify-sim --sim xyce",
             "note": "x", "version_checked": True}
        r = report.build({**base, "simulator_verification": v}, "s.json")
        md = report.markdown(r)
        assert text in md
        assert md.index(text) < md.index("## Nets")          # next to the verdict
    r = report.build({**base, "simulator_verification": {"status": "not applicable"}}, "s")
    assert "Simulator route" not in report.markdown(r)


def test_info_lists_hspice_as_licensed():
    doc = _doc(emir("info", "--format", "json"))
    t = {x["name"]: x for x in doc["tools"]}
    assert t["hspice"]["licensed"] and t["spectre"]["licensed"]
    assert not t["xyce"]["licensed"]
