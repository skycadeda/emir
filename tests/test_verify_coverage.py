"""What a simulator verification covers, and how its records are kept.

* coverage is per kind of current: a run reads branch currents (rcc), and a
  record covers them when its checks of that kind passed; a record made
  before records said what they cover verifies the route and no kind, so the
  run is not verified and the verification is planned again;
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
    covers = ["branch"] if covers is None else covers
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


def test_what_each_simulator_s_verification_can_cover():
    for sim in V.SIMS:
        assert V.coverable(sim) == ["branch"]


def test_a_run_reads_branch_currents():
    from emir.dspf import parse
    from emir.selftest import sample_paths
    d = parse(sample_paths()[0])
    pins = V.run_pins(d, {"VDD": {"mode": "rcc"}})
    assert list(pins) == ["branch"] and len(pins["branch"]) == 16
    assert V.run_pins(d, {"VDD": {"mode": "dc"}}) is None
    V.save_record(_record())
    st = V.status(dict(XYCE), pins=pins)
    assert st["status"] == "verified" and _codes(st)[0] == ["EMIR-I463"]
    assert st["current_kinds"] == [{"kind": "branch", "what": V.KINDS["branch"], "count": 16,
                                "examples": pins["branch"][:4], "covered": True,
                                "verify_sim_covers": True}]


def test_a_record_without_covers_verifies_the_route_and_no_kind():
    rec = _record()
    del rec["covers"]
    V.save_record(rec)
    assert V.status(dict(XYCE))["status"] == "verified"         # the route alone
    st = V.status(dict(XYCE), pins={"branch": ["Rm1:1"]})
    assert (st["status"], st["reason"]) == ("not verified", "current kinds not covered")
    # a kind a verification of this emir covers: verify again
    assert st["command"] == "emir verify-sim --sim xyce"
    codes, text = _codes(st)
    assert codes == ["EMIR-W464"] and "verify again" in text


def test_a_record_cannot_claim_a_kind_its_checks_never_proved():
    rec = _record(covers=["branch"])
    rec["checks"] = [{"name": "x", "ok": True}]          # hand-written, one check
    assert "no passing check" in V.record_problem(rec)
    V.save_record(rec)
    st = V.status(dict(XYCE), pins={"branch": ["Rm1:1"]})
    assert st["status"] == "not verified" and st["ignored_records"] == 1
    # a kind this release does not read (an earlier build's) is kept and
    # counts for nothing; one that is not a string makes the record invalid
    assert V.record_problem({**_record(), "covers": ["branch", "mos"]}) is None
    assert V.record_covers({**_record(), "covers": ["branch", "mos"]}) == ["branch"]
    assert V.record_problem({**_record(), "covers": [3]})


def test_a_run_says_whether_its_branch_currents_are_covered(tmp_path):
    """A run on a result of the verification's own netlist: verified with a
    record that covers branch currents; not verified, and the report says
    so, with one made before records said what they cover."""
    from _helpers import branch_waves, time_base
    dspf = V.build("xyce", str(tmp_path / "v"))[0]["dspf"]          # verify_r.dspf
    t = time_base()
    w = branch_waves(t)
    names = [f"I(XI0:{k[:-2].upper()})" for k in w]
    L = ["Title: stand-in", "Date: today", "Plotname: Transient Analysis", "Flags: real",
         f"No. Variables: {len(names) + 1}", f"No. Points: {len(t)}", "Variables:",
         "\t0\ttime\ttime"] + [f"\t{k + 1}\t{x}\tcurrent" for k, x in enumerate(names)]
    L.append("Values:")
    for i, ti in enumerate(t):
        L.append(f" {i}\t{ti:.12e}")
        L += [f"\t{v[i]:.12e}" for v in w.values()]
    res = tmp_path / "rcc.raw"
    res.write_text("\n".join(L) + "\n")
    side = {"map": {x: [k, 1] for x, k in zip(names, w)}, "simulator": "xyce",
            "simulator_version": "Xyce Release 7.10.0", "sim_args": ""}
    (tmp_path / "rcc.raw.names").write_text(json.dumps(side))
    rec = _record()
    del rec["covers"]
    V.save_record(rec)
    s = json.loads(emir("run", dspf, "--process", PROC, "--cc-sim", res, "--period", "1e-9",
                        "--format", "json").stdout)
    v = s["simulator_verification"]
    assert (v["status"], v["reason"]) == ("not verified", "current kinds not covered")
    assert [k["kind"] for k in v["current_kinds"]] == ["branch"] and v["current_kinds"][0]["count"] == 16
    codes = [n["code"] for n in s["notices"]]
    assert "EMIR-W464" in codes and "EMIR-I463" not in codes
    (tmp_path / "s.json").write_text(json.dumps(s))
    md = emir("report", "--from", tmp_path / "s.json").stdout
    assert "NOT verified on this machine" in md
    js = pytest.importorskip("jsonschema")
    from emir import schemas
    js.validate(s, schemas.get("run"))
    V.save_record(_record())
    s = json.loads(emir("run", dspf, "--process", PROC, "--cc-sim", res, "--period", "1e-9",
                        "--format", "json").stdout)
    assert s["simulator_verification"]["status"] == "verified"


def test_the_save_set_records_no_kinds():
    from emir.dspf import parse
    from emir.opensim import save_set
    from emir.selftest import sample_paths
    d = parse(sample_paths()[0])
    for sim in ("xyce", "ngspice", "hspice"):
        side = save_set(d, ["VDD"], sim, ["XI0"]).sidecar()
        assert set(side) == {"map", "simulator"} and len(side["map"]) == 16


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
        p = P.build(P.Facts(dspf=dspf, process=proc, net="VDD"))
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
    res = tmp_path / "rcc.csd"
    names = [f"Rm{k}:1" for k in range(1, 13)] + [f"Rc{k}:1" for k in range(4)]
    rows = [[k * 5e-9] + [1e-3] * len(names) for k in range(9)]
    csdf.write_csdf(str(res), names, [rows], source="HSPICE")
    plan = P.build(P.Facts(dspf=dspf, process=PROC, cc_sim=str(res), period=1e-8))
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
