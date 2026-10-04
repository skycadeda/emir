"""What a simulator verification covers, and how its records are kept.

* coverage is per kind of current: a run that reads diode, BJT or JFET
  terminals (which no verification testbench holds) is never reported
  verified (EMIR-I463); it says which of its kinds are covered (`pin_kinds`);
* a result in a format `emir verify-sim` never writes (CSDF) is `format not
  covered`, with no verify-sim suggested or planned, and a version of `0` is
  no version;
* the records file is written under a lock and replaced atomically, and a
  damaged one is reported and moved aside, never read as empty and
  overwritten.

Every test has its own state directory (tests/conftest.py).
"""
import json
import multiprocessing as mp
import os

import pytest
from _helpers import PROC, emir

from emir import verify_sim as V
from emir.errors import collecting

XYCE = {"simulator": "xyce", "simulator_version": "Xyce Release 7.10.0", "format": "nutascii"}


def _record(sim="xyce", version="7.10.0", passed=True, covers=None, **kw):
    """A record as `emir verify-sim` writes it: one passing check per kind
    it covers."""
    from emir import __version__
    covers = ["branch", "mos", "probe", "two-terminal"] if covers is None else covers
    checks = [{"name": f"{k} check", "ok": passed, "kind": k} for k in covers] or \
        [{"name": "a check", "ok": passed}]
    rec = {"schema": "emir.verify_sim/1", "simulator": sim, "version": version,
           "format": V.FORMAT[sim], "sim_args": None, "emir_version": __version__,
           "date": "2026-10-02T00:00:00Z", "host": None, "passed": passed,
           "covers": V.covers_of(checks), "checks": checks}
    rec.update(kw)
    return rec


def _codes(st):
    with collecting() as notes:
        V.report_status(st, log=None)
    return [n["code"] for n in notes], " ".join(n["message"] for n in notes)


# ------------------------------------------------------------ kinds


def test_a_pin_kind_is_its_instance_letter_unless_the_sidecar_says():
    assert V.pin_kind("MP0:s") == "mos" and V.pin_kind("D5:k") == "diode"
    assert V.pin_kind("Q1:e") == "bjt" and V.pin_kind("J2:g") == "jfet"
    assert V.pin_kind("C9:2") == V.pin_kind("R1:1") == "two-terminal"
    assert V.pin_kind("XI0/MM1:d") == "mos"            # the last level of the name
    assert V.pin_kind("L1:1") == "other" and V.pin_kind("NM0:d") == "other"
    k = V.classify(["D5:k", "MP0:s"], {"D5:K": "probe"})
    assert k == {"probe": ["D5:k"], "mos": ["MP0:s"]}


def test_what_each_simulator_s_verification_can_cover():
    assert V.coverable("xyce") == ["branch", "mos", "probe", "two-terminal"]
    assert V.coverable("ngspice") == ["branch", "probe", "two-terminal"]   # MOS: probes
    assert V.coverable("hspice") == ["branch", "mos", "probe", "two-terminal"]
    assert V.coverable("spectre") == ["branch", "mos", "source", "subcircuit",
                                      "two-terminal"]
    for sim in V.SIMS:
        assert not {"diode", "bjt", "jfet", "other"} & set(V.coverable(sim))


def test_diode_pins_are_never_reported_verified():
    """Regression: a passing Xyce record, and a run whose pins are
    MOSFET sources, a capacitor's pin 2 and a diode's cathode."""
    V.save_record(_record())
    pins = V.classify(["MP0:s", "C9:2", "D5:k", "MP3:s"])
    st = V.status(dict(XYCE), pins=pins)
    assert (st["status"], st["reason"]) == ("partly verified", "pin kinds not covered")
    kinds = {k["kind"]: k for k in st["pin_kinds"]}
    assert kinds["diode"]["covered"] is False and kinds["diode"]["examples"] == ["D5:k"]
    assert kinds["diode"]["verify_sim_covers"] is False
    assert kinds["mos"]["covered"] and kinds["two-terminal"]["covered"]
    assert st["command"] is None                       # no verification can cover it
    codes, text = _codes(st)
    assert codes == ["EMIR-W464"] and "EMIR-I463" not in codes
    assert "D5:k" in text and "diode terminals" in text and "UNVERIFIED" in text
    # only diode pins: nothing the run reads is covered
    st = V.status(dict(XYCE), pins=V.classify(["D5:k"]))
    assert (st["status"], st["reason"]) == ("not verified", "pin kinds not covered")
    # BJT and JFET terminals alike
    st = V.status(dict(XYCE), pins=V.classify(["MP0:s", "Q1:e", "J2:d"]))
    assert st["status"] == "partly verified"
    assert {k["kind"] for k in st["pin_kinds"] if not k["covered"]} == {"bjt", "jfet"}
    # MOSFET pins and branch currents only: verified, as before
    st = V.status(dict(XYCE), pins={**V.classify(["MP0:s"]), "branch": ["Rm1:1"]})
    assert st["status"] == "verified" and _codes(st)[0] == ["EMIR-I463"]


def test_a_record_without_covers_verifies_the_route_and_no_kind_of_pin():
    rec = _record()
    del rec["covers"]
    V.save_record(rec)
    assert V.status(dict(XYCE))["status"] == "verified"         # the route alone
    st = V.status(dict(XYCE), pins=V.classify(["MP0:s"]))
    assert (st["status"], st["reason"]) == ("not verified", "pin kinds not covered")
    # a kind a verification of this emir covers: verify again
    assert st["command"] == "emir verify-sim --sim xyce"


def test_a_record_cannot_claim_a_kind_its_checks_never_proved():
    rec = _record(covers=["mos"])
    rec["checks"] = [{"name": "x", "ok": True}]          # hand-written, one check
    assert "no passing check" in V.record_problem(rec)
    V.save_record(rec)
    st = V.status(dict(XYCE), pins=V.classify(["MP0:s"]))
    assert st["status"] == "not verified" and st["ignored_records"] == 1
    assert V.record_problem({**_record(), "covers": ["gold"]})


def test_a_run_says_which_kinds_it_read(tmp_path):
    """A run on a result whose sidecar records the kinds (as emir simulate
    writes it): the summary's pin_kinds, the report line."""
    out = tmp_path / "v"
    dspf = V.build("xyce", str(out))[0]["dspf"]          # verify_r.dspf: ILOAD0..3:p
    import numpy as np
    t = np.linspace(0, 40e-9, 9)
    names = [f"I(XI0:D{k})" for k in range(4)]
    res = tmp_path / "cc.raw"
    L = ["Title: stand-in", "Date: today", "Plotname: Transient Analysis", "Flags: real",
         f"No. Variables: {len(names) + 1}", f"No. Points: {len(t)}", "Variables:",
         "\t0\ttime\ttime"] + [f"\t{k + 1}\t{x}\tcurrent" for k, x in enumerate(names)]
    L.append("Values:")
    for i, ti in enumerate(t):
        L.append(f" {i}\t{ti:.12e}")
        L += ["\t1.000000000000e-03"] * len(names)
    res.write_text("\n".join(L) + "\n")
    side = {"map": {x: [f"ILOAD{k}:p", 1] for k, x in enumerate(names)},
            "simulator": "xyce", "simulator_version": "Xyce Release 7.10.0", "sim_args": "",
            "kinds": {f"ILOAD{k}:p": "diode" for k in range(4)}}
    (tmp_path / "cc.raw.names").write_text(json.dumps(side))
    V.save_record(_record())
    p = emir("run", dspf, "--process", PROC, "--cc-sim", res, "--mode", "cc-grid",
             "--format", "json")
    s = json.loads(p.stdout)
    v = s["simulator_verification"]
    assert (v["status"], v["reason"]) == ("not verified", "pin kinds not covered")
    assert [k["kind"] for k in v["pin_kinds"]] == ["diode"] and v["pin_kinds"][0]["count"] == 4
    codes = [n["code"] for n in s["notices"]]
    assert "EMIR-W464" in codes and "EMIR-I463" not in codes
    (tmp_path / "s.json").write_text(json.dumps(s))
    md = emir("report", "--from", tmp_path / "s.json").stdout
    assert "NOT verified on this machine" in md and "diode terminals" in md
    js = pytest.importorskip("jsonschema")
    from emir import schemas
    js.validate(s, schemas.get("run"))
    # the probe kind (emir simulate --probe): covered
    side["kinds"] = {f"ILOAD{k}:p": "probe" for k in range(4)}
    (tmp_path / "cc.raw.names").write_text(json.dumps(side))
    s = json.loads(emir("run", dspf, "--process", PROC, "--cc-sim", res, "--mode", "cc-grid",
                        "--format", "json").stdout)
    assert s["simulator_verification"]["status"] == "verified"


def test_the_save_set_records_how_each_pin_is_read():
    from emir.dspf import parse
    from emir.opensim import save_set
    from emir.selftest import sample_paths
    d = parse(sample_paths()[0])
    ss = save_set(d, ["VDD"], "cc", "xyce", ["XI0"])
    assert set(ss.sidecar()["kinds"].values()) == {"mos"}            # MP0..MP3 sources
    ss = save_set(d, ["VDD"], "cc", "ngspice", ["XI0"])
    assert "kinds" not in ss.sidecar()                   # ngspice MOS pins: probes only
    ss = save_set(d, ["VDD"], "rcc", "hspice", ["XI0"])
    assert set(ss.sidecar()["kinds"].values()) == {"branch"}


def _stand_in_result(tmp_path, kind):
    """An Xyce-style result of the verification's CC netlist (ILOAD0..3:p),
    with a names sidecar recording each pin's kind as emir simulate does."""
    import numpy as np
    t = np.linspace(0, 40e-9, 9)
    names = [f"I(XI0:D{k})" for k in range(4)]
    res = tmp_path / f"cc_{kind}.raw"
    L = ["Title: stand-in", "Date: today", "Plotname: Transient Analysis", "Flags: real",
         f"No. Variables: {len(names) + 1}", f"No. Points: {len(t)}", "Variables:",
         "\t0\ttime\ttime"] + [f"\t{k + 1}\t{x}\tcurrent" for k, x in enumerate(names)]
    L.append("Values:")
    for i, ti in enumerate(t):
        L.append(f" {i}\t{ti:.12e}")
        L += ["\t1.000000000000e-03"] * len(names)
    res.write_text("\n".join(L) + "\n")
    side = {"map": {x: [f"ILOAD{k}:p", 1] for k, x in enumerate(names)},
            "simulator": "xyce", "simulator_version": "Xyce Release 7.10.0", "sim_args": "",
            "kinds": {f"ILOAD{k}:p": kind for k in range(4)}}
    (tmp_path / f"cc_{kind}.raw.names").write_text(json.dumps(side))
    return str(res)


def test_next_judges_a_finished_result_by_the_kinds_the_run_reads(tmp_path):
    """`emir next --cc-sim` says what `emir run` will say: a record that does
    not cover the kinds of current the run reads plans a verification (or,
    where none can cover them, says they stay UNVERIFIED), never "verified"."""
    from emir import plan as P
    dspf = V.build("xyce", str(tmp_path / "v"))[0]["dspf"]

    def plan(res):
        p = P.build(P.Facts(dspf=dspf, process=PROC, cc_sim=res, mode="cc-grid"))
        return p, [s["id"] for s in p.steps]

    def run(res):
        p = emir("run", dspf, "--process", PROC, "--cc-sim", res, "--mode", "cc-grid",
                 "--format", "json")
        return json.loads(p.stdout)["simulator_verification"]

    mos = _stand_in_result(tmp_path, "mos")
    diode = _stand_in_result(tmp_path, "diode")
    # no record yet: the verification is planned, and the diode pins it can
    # never cover are named
    p, ids = plan(diode)
    assert "verify-sim" in ids
    assert any("diode terminals" in a and "UNVERIFIED" in a for a in p.advice)
    # a record made before records said what they cover
    rec = _record()
    del rec["covers"]
    V.save_record(rec)
    p, ids = plan(mos)
    assert "verify-sim" in ids and ids.index("verify-sim") < ids.index("check")
    v = p.steps[ids.index("verify-sim")]
    assert v["command"] == "emir verify-sim --sim xyce" and "MOSFET terminals" in v["why"]
    assert not any("route was verified" in a for a in p.advice)
    assert (run(mos)["status"], run(mos)["command"]) == ("not verified",
                                                         "emir verify-sim --sim xyce")
    # a record covering branch currents only: the same
    V.save_record(_record(covers=["branch"]))
    assert "verify-sim" in plan(mos)[1]
    # a record of this emir covers MOSFET terminals: nothing to plan
    V.save_record(_record())
    p, ids = plan(mos)
    assert "verify-sim" not in ids and run(mos)["status"] == "verified"
    assert any("verified on this machine" in a for a in p.advice)
    # diode terminals: no verification covers them; none is planned, and the
    # plan says they stay UNVERIFIED, as the run does
    p, ids = plan(diode)
    assert "verify-sim" not in ids
    assert not any("route was verified" in a for a in p.advice)
    assert any("diode terminals" in a and "UNVERIFIED" in a and "EMIR-W464" in a
               for a in p.advice)
    assert run(diode)["status"] == "not verified"


@pytest.fixture
def xyce_found(monkeypatch):
    from emir import site
    monkeypatch.setattr(site, "tool", lambda name, explicit=None: (
        ("/opt/fake/xyce" if name == "xyce" else None), "$PATH"))


def test_a_record_that_does_not_cover_every_kind_is_verified_again(xyce_found):
    """Before a simulation it plans (and in `emir info`), a record counts only
    when it covers every kind this emir's verification covers: one made
    before records said what they cover is verified again, and the plan
    says why."""
    from emir import plan as P
    from emir.selftest import sample_paths
    dspf, proc = sample_paths()

    def plan():
        p = P.build(P.Facts(dspf=dspf, process=proc, net="VDD", mode="rcc"))
        return p, [s["id"] for s in p.steps]

    rec = _record()
    del rec["covers"]
    V.save_record(rec)
    assert V.status(dict(XYCE))["status"] == "verified"           # the route alone
    assert V.verified_route("xyce", "nutascii") is None
    p, ids = plan()
    assert "verify-sim" in ids
    why = p.steps[ids.index("verify-sim")]["why"]
    assert "does not cover" in why and "verify again" in why
    info = json.loads(emir("info", "--format", "json").stdout)
    assert next(t for t in info["tools"] if t["name"] == "xyce")["verified"] is None
    V.save_record(_record())
    assert V.verified_route("xyce", "nutascii")["covers"] == V.coverable("xyce")
    p, ids = plan()
    assert "verify-sim" not in ids


# ------------------------------------------------------------ formats, versions


def test_a_csdf_result_is_format_not_covered():
    """Regression (example 11's CSDF twin): no verify-sim advice,
    and no "hspice 0" version."""
    assert V.version_key("0") is None and V.version_key("0.0") is None
    assert V.version_key("10") == "10" and V.version_key("2023.12") == "2023.12"
    st = V.status({"simulator": "hspice", "format": "csdf", "simulator_version": "0"})
    assert (st["status"], st["reason"]) == ("not verified", "format not covered")
    assert st["command"] is None and "hspice 0" not in st["note"]
    assert "tr0ascii output only" in st["note"]
    codes, text = _codes(st)
    assert codes == ["EMIR-W464"] and "verify-sim --sim hspice" not in text
    # a Spectre nutascii result alike
    st = V.status({"simulator": "spectre", "format": "nutascii", "simulator_version": "19.1"})
    assert st["reason"] == "format not covered"


def test_next_plans_no_verification_for_a_format_it_cannot_cover(tmp_path):
    from emir import csdf
    from emir import plan as P
    out = tmp_path / "v"
    dspf = V.build("hspice", str(out))[0]["dspf"]
    res = tmp_path / "cc.csd"
    names = [f"ILOAD{k}:p" for k in range(4)]
    rows = [[k * 5e-9] + [1e-3] * len(names) for k in range(9)]
    csdf.write_csdf(str(res), names, [rows], source="HSPICE")
    plan = P.build(P.Facts(dspf=dspf, process=PROC, cc_sim=str(res), mode="cc-grid"))
    assert "verify-sim" not in [s["id"] for s in plan.steps]
    assert any("format not covered" in a or "csdf result stays UNVERIFIED" in a
               for a in plan.advice)


# ------------------------------------------------------------ the records file


def _save_many(state, tag, n):
    os.environ["EMIR_STATE_DIR"] = state
    for k in range(n):
        V.save_record(_record(version=f"7.{k}.0", date="2026-10-02T00:00:00Z",
                              checks=[{"name": f"{tag}{k}", "ok": True, "kind": "mos"}],
                              covers=["mos"]))


def test_two_verifications_writing_at_once_keep_every_record(tmp_path):
    """Regression, a load test: two processes saving 40 records each kept
    19 of 80 before the lock."""
    state = str(tmp_path / "state")
    ctx = mp.get_context("spawn")
    ps = [ctx.Process(target=_save_many, args=(state, t, 40)) for t in "ab"]
    for p in ps:
        p.start()
    for p in ps:
        p.join(120)
    assert [p.exitcode for p in ps] == [0, 0]
    recs = V.load_records(os.path.join(state, V.RECORDS))
    assert len(recs) == 80
    assert not [f for f in os.listdir(state) if f.endswith(".tmp")]


def test_a_damaged_records_file_is_reported_and_moved_aside(tmp_path):
    V.save_record(_record())
    V.save_record(_record(passed=False, covers=[]))
    path = V.records_path()
    text = open(path).read()
    with open(path, "w") as f:
        f.write(text[:-40])                               # cut short
    st = V.status(dict(XYCE))
    assert st["records_problem"] and "not JSON" in st["records_problem"]
    codes, msg = _codes(st)
    assert codes[0] == "EMIR-W415" and "left as it is" in msg
    assert open(path).read() == text[:-40]               # a run never rewrites it
    with collecting() as notes:
        assert V.save_record(_record()) == path
    assert [n["code"] for n in notes] == ["EMIR-W415"]
    bad = [f for f in os.listdir(os.path.dirname(path)) if ".bad-" in f]
    assert len(bad) == 1
    assert open(os.path.join(os.path.dirname(path), bad[0])).read() == text[:-40]
    assert len(V.load_records()) == 1 and V.read_records()[1] is None
    # not a records document at all
    with open(path, "w") as f:
        json.dump({"schema": "emir.verified/1"}, f)
    assert "no `records` list" in V.read_records()[1]


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_a_save_keeps_the_records_file_s_mode():
    """The atomic replace writes a temporary file (created 0600): the records
    file keeps its mode (a group-shared state directory stays readable to
    the group), and a new one gets the umask's."""
    old = os.umask(0o022)
    try:
        path = V.save_record(_record())
        assert os.stat(path).st_mode & 0o777 == 0o644          # new: 0666 & ~umask
        os.chmod(path, 0o664)
        V.save_record(_record())
        assert os.stat(path).st_mode & 0o777 == 0o664
    finally:
        os.umask(old)


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0,
                    reason="POSIX permissions (root reads everything)")
def test_a_records_file_that_cannot_be_read_is_left_alone():
    """A records file this user may not read (another user's, in a shared
    state directory) is not damaged: a save reports it (EMIR-W467) and keeps
    no record, and never moves the file aside."""
    path = V.save_record(_record())
    text = open(path).read()
    os.chmod(path, 0)
    try:
        codes, msg = _codes(V.status(dict(XYCE)))      # a run says so, truthfully
        assert codes[0] == "EMIR-W415" and "moves it aside" not in msg
        assert "read access" in msg
        with collecting() as notes:
            assert V.save_record(_record()) is None
        codes = [n["code"] for n in notes]
        assert codes == ["EMIR-W467"] and "cannot be read" in notes[0]["message"]
        assert not [f for f in os.listdir(os.path.dirname(path)) if ".bad-" in f]
    finally:
        os.chmod(path, 0o644)
    assert open(path).read() == text


def test_the_cadence_decade_holds_only_cadence_codes():
    """x80-x89 of every area belongs to the Cadence integration (emir/errors.py
    DECADES): the result reader's and the verification records' codes moved
    out of it (E425-E427, W414-W416)."""
    import glob
    import re
    from emir import errors
    root = os.path.dirname(os.path.dirname(os.path.abspath(errors.__file__)))
    cadence = {"cadence.py", "layoutview.py", "skillgui.py", "skillapp.py",
               "plan_cadence.py"}
    sources = {os.path.basename(p): open(p, encoding="utf-8").read()
               for p in glob.glob(os.path.join(root, "emir", "*.py"))
               if not p.endswith("errors.py")}
    for code in errors.REGISTRY:
        if code[6] == "8" or not 80 <= int(code[7:9]) <= 89:
            continue
        users = {f for f, t in sources.items() if re.search(rf"\b{code}\b", t)}
        assert users and users <= cadence, (code, users)
    for old in ("EMIR-E480", "EMIR-E481", "EMIR-E482", "EMIR-W480", "EMIR-W481",
                "EMIR-W482"):
        assert old not in errors.REGISTRY
