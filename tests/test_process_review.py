"""Process data entered by hand: regression tests, one per case.

* a unit slip that loosens the limits (K typed for C, uA for mA) in a
  hand-entered file blocks the run (EMIR-E331) until the person confirms the
  value; with --no-check the run says it, and every report flags the limits
  as suspect;
* Black's derating: the reports no longer say the avg limits were not
  derated; below em_tref it is clamped at 1 (EMIR-W326), and temperatures at
  or below absolute zero are refused (EMIR-E332);
* the provenance names unsourced temperatures and cap models, a blank
  source is no source, unknown derating keys and non-positive cap values are
  refused, the hints tell an agent to ask the person;
* `emir import-ict`: a protected block anywhere refuses the file, base64 is
  encrypted, "Encryption: none" is not, the (w - dW) offset becomes
  width_loss_um, and a file with no block points at hand entry;
* `emir process new`: no alphabetical stacking guess; --interactive never
  copies a source unless the person types `=`;
* the ict-encrypted eval card catches limits made up in the fill-in file.
"""
from __future__ import annotations

import io
import os
import subprocess
import sys

import pytest
import yaml
from _helpers import ROOT, write
from test_process_entry import SAMPLE_CUR, filled, jrun

from emir.check import Report, check_process
from emir.process_file import build_model, from_dspf, interactive

DSPF = os.path.join(ROOT, "emir", "data", "sample", "rail.dspf")
CURJ = SAMPLE_CUR


def _edit(path, **over):
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    for k, v in over.items():
        node = data
        *head, last = k.split("/")
        for h in head:
            node = node[h]
        node[last] = v
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False)
    return path


# ------------------------------------------------------------------ unit slips


@pytest.mark.parametrize("over", [{"em_tref": 373.15},                       # K for C
                                  {"layers/metal1/jmax_avg": 3200.0},        # uA for mA
                                  {"vias/cont/imax_avg": 750.0}])
def test_a_slip_that_loosens_the_limits_blocks_the_run(tmp_path, over):
    """K for C made a 9.5x FAIL read CLEAN at 0.033x, with process check
    passing; uA for mA made it 0.010x."""
    p = filled(tmp_path, **over)
    d, _ = jrun("process", "check", p, "--dspf", DSPF, rc=2)
    assert not d["passed"] and "EMIR-E331" in [f["code"] for f in d["findings"]
                                               if f["level"] == "FAIL"]
    assert "passes" not in d["next_steps"][0]["why"]
    d, _ = jrun("run", DSPF, "--process", p, "--currents", CURJ, "--net", "VDD", rc=2)
    assert d["code"] == "EMIR-E501" and "EMIR-E331" in d["details"]["causes"]
    # the person confirms it: a warning again, and every report says it
    path = next(iter(over))
    _edit(p, unit_confirmed=[path])
    d, _ = jrun("process", "check", p, "--dspf", DSPF)
    w = [f for f in d["findings"] if f["code"] == "EMIR-W321"]
    assert d["passed"] and w and "confirmed" in w[0]["message"]


@pytest.mark.parametrize("prov", [None, "imported-ict", "public-pdk-data"])
def test_a_slip_blocks_whatever_provenance_the_file_states(tmp_path, prov):
    """Plausibility does not depend on the provenance key: a hand-written file
    with no `provenance:` (uA typed for mA) read CLEAN at 0.0057x, exit 0."""
    p = filled(tmp_path, **{"layers/metal1/jmax_avg": 3200.0})
    with open(p, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    data.pop("provenance", None)
    if prov:
        data["provenance"] = prov
    for sec in ("layers", "vias"):
        for e in (data.get(sec) or {}).values():
            if isinstance(e, dict):
                e.pop("provenance", None)
    with open(p, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False)
    d, _ = jrun("process", "check", p, "--dspf", DSPF, rc=2)
    assert "EMIR-E331" in [f["code"] for f in d["findings"] if f["level"] == "FAIL"]
    d, _ = jrun("run", DSPF, "--process", p, "--currents", CURJ, "--net", "VDD", rc=2)
    assert d["code"] == "EMIR-E501" and "EMIR-E331" in d["details"]["causes"]
    _edit(p, unit_confirmed=["layers/metal1/jmax_avg"])
    jrun("run", DSPF, "--process", p, "--currents", CURJ, "--net", "VDD")


def test_a_skipped_check_still_says_the_limits_are_suspect(tmp_path):
    p = filled(tmp_path, em_tref=373.15)
    d, proc = jrun("run", DSPF, "--process", p, "--currents", CURJ, "--net", "VDD",
                   "--no-check", "--summary-out", str(tmp_path / "s.json"))
    assert "EMIR-W321" in {n["code"] for n in d["notices"]}
    sus = d["limits_provenance"]["suspects"]
    assert sus and sus[0]["path"] == "em_tref" and sus[0]["blocking"]
    assert "the limits are SUSPECT" in proc.stderr
    r, _ = jrun("report", "--from", str(tmp_path / "s.json"))
    assert "The limits are SUSPECT" in r["markdown"] and "SUSPECT UNIT: em_tref" in r["markdown"]


def test_process_check_does_not_say_passes_while_a_value_is_suspect(tmp_path):
    # sheet_res in mOhm: not a limit, so a warning, but still not "passes"
    p = filled(tmp_path, **{"layers/metal1/sheet_res": 125.0})
    d, _ = jrun("process", "check", p, "--dspf", DSPF)
    assert d["passed"] and d["next_steps"][0]["needs_human"] is True
    assert "passes" not in d["next_steps"][0]["why"]


# ------------------------------------------------------------------ derating


def _black(tmp_path, **extra):
    return filled(tmp_path, **{"derating/ea_eV": 0.9, "derating/n": 2,
                               "sources/derating": "Demo DM, section 7", **extra})


def test_black_derating_is_not_reported_as_missing(tmp_path):
    """With Black's derating every report hotter than em_tref said the avg
    limits were 'applied unchanged ... NOT conservative'."""
    p = _black(tmp_path)
    d, _ = jrun("run", DSPF, "--process", p, "--currents", CURJ, "--net", "VDD",
                "--em-temp", "125", "--summary-out", str(tmp_path / "s.json"))
    assert d["not_checked"]["avg_not_derated"] is None
    r, _ = jrun("report", "--from", str(tmp_path / "s.json"))
    assert "NOT conservative" not in r["markdown"]
    from emir.engine import Process
    from emir.summary import derating_gap
    assert derating_gap(Process.load(p, temp=125.0)) is None


def test_black_derating_is_not_extrapolated_below_em_tref(tmp_path):
    """At -40 C against em_tref 100 C the factor was x6429 (INFO only)."""
    from emir.engine import Process, black_factor
    p = _black(tmp_path)
    proc = Process.load(p, temp=-40.0)
    assert proc.derate == 1.0 and proc.derate_clamped == pytest.approx(
        black_factor(0.9, 2, -40.0, 100.0))
    rep = Report()
    check_process(p, rep, em_temp=-40.0)
    assert "EMIR-W326" in [f.code for f in rep.findings]
    # the file can ask for it
    q = _edit(p, **{"derating/below_tref": "extrapolate"})
    assert Process.load(q, temp=-40.0).derate == pytest.approx(
        black_factor(0.9, 2, -40.0, 100.0))


@pytest.mark.parametrize("where", ["em_tref", "temp_reference", "--em-temp"])
def test_absolute_zero_is_refused_not_a_crash(tmp_path, where):
    """em_tref -273.15 crashed emir process check with EMIR-E901 (exit 70)."""
    p = _black(tmp_path) if where == "--em-temp" else _black(tmp_path, **{where: -273.15})
    extra = ["--em-temp=-273.15"] if where == "--em-temp" else []
    d, _ = jrun("run", DSPF, "--process", p, "--currents", CURJ, "--net", "VDD", *extra, rc=2)
    assert "EMIR-E332" in (d.get("details") or {}).get("causes", [d["code"]])
    if where != "--em-temp":
        d, _ = jrun("process", "check", p, rc=2)
        assert "EMIR-E332" in [f["code"] for f in d["findings"]]


# ------------------------------------------------------------------ provenance


def test_unsourced_temperatures_are_named(tmp_path):
    p = _black(tmp_path, **{"sources/em_tref": None, "sources/derating": "  "})
    d, _ = jrun("run", DSPF, "--process", p, "--currents", CURJ, "--net", "VDD",
                "--summary-out", str(tmp_path / "s.json"))
    t = d["limits_provenance"]["temperature"]
    assert t["unsourced"] == ["em_tref", "derating"]       # a blank source is no source
    r, _ = jrun("report", "--from", str(tmp_path / "s.json"))
    assert "em_tref 100 C -- NO SOURCE" in r["markdown"]
    from emir.scene import limits_notes
    assert any("NO SOURCE" in n and "em_tref" in n for n in limits_notes(d["limits_provenance"]))


def test_a_blank_source_is_no_source(tmp_path):
    p = filled(tmp_path, **{"layers/metal1/sources/jmax_avg": "   "})
    d, _ = jrun("process", "check", p)
    assert "layers/metal1/jmax_avg" in d["unsourced"]


def test_unknown_derating_keys_and_cap_models_are_refused(tmp_path):
    p = _edit(_black(tmp_path), **{"derating/tref_C": 105})     # a key the format lacks
    rep = Report()
    check_process(p, rep)
    assert any(f.code == "EMIR-E316" and "tref_C" in f.message for f in rep.findings)
    # `cap_models` sized the rails' decaps for cc-grid's guard, removed in 1.1:
    # an unknown top-level key now (EMIR-W314), never read
    data = yaml.safe_load(open(filled(tmp_path)))
    data["cap_models"] = {"nfet": {"cox": 8.6e-3}}
    q = write(tmp_path / "cap.yaml", yaml.safe_dump(data))
    rep = Report()
    check_process(q, rep)
    assert any(f.code == "EMIR-W314" and "cap_models" in f.message for f in rep.findings)


def test_the_hints_tell_an_agent_to_ask(tmp_path):
    from emir.errors import lookup
    for code in ("EMIR-E320", "EMIR-W320", "EMIR-W324", "EMIR-E331"):
        assert "Ask the person" in lookup(code).hint or "ask the person" in lookup(code).hint
    out = tmp_path / "p.yaml"
    jrun("process", "new", "--from-dspf", DSPF, "-o", str(out))
    d, _ = jrun("next", "--dspf", DSPF, "--process", str(out), "--currents", CURJ,
                "--net", "VDD")
    how = d["missing"][0]["how"]
    assert "ask the person" in how and "never fill one in" in how


# ------------------------------------------------------------------ import-ict


ICT = """process "demo" {
    temp_reference 25
    em_tref 105
}
conductor "metal1_conn" {
    em_jmax_dc_avg EQU 2.0 * ( w - 0.05 )
}
"""


def _ict(tmp_path, text, name="x.ict"):
    """The ICT `text` and a copy of the sample process file to merge it into."""
    with open(os.path.join(ROOT, "emir", "data", "sample", "process.yaml")) as f:
        base = write(tmp_path / "base.yaml", f.read())
    ict = tmp_path / name
    ict.write_bytes(text.encode() if isinstance(text, str) else text)
    return str(ict), base


def test_a_protected_block_past_64_kib_refuses_the_file(tmp_path):
    late = ICT + ("// padding comment line\n" * 3000) + \
        "`pragma protect begin_protected\nAAAA\n`pragma protect end_protected\n"
    ict, base = _ict(tmp_path, late)
    d, _ = jrun("import-ict", "--ict", ict, "--emir", base, "-o", str(tmp_path / "o.yaml"),
                rc=2)
    assert d["code"] == "EMIR-E325" and not (tmp_path / "o.yaml").exists()


@pytest.mark.parametrize("body,code", [("U2FsdGVkX1" + "A" * 120 + "\n", "EMIR-E325"),
                                       ("QUJD" * 40 + "\n" + "REVG" * 40 + "\n", "EMIR-E325"),
                                       ("", "EMIR-E104"), ("\n\n  \n", "EMIR-E104")])
def test_encoded_and_empty_files(tmp_path, body, code):
    ict, base = _ict(tmp_path, body)
    d, _ = jrun("import-ict", "--ict", ict, "--emir", base, "-o", str(tmp_path / "o.yaml"),
                rc=2)
    assert d["code"] == code
    assert any(s["command"].startswith("emir process new") for s in d["next_steps"]) \
        or "emir process new" in d["message"]


def test_encryption_none_is_not_an_encryption_marker(tmp_path):
    ict, base = _ict(tmp_path, "# Encryption: none (plain-text deck)\n" + ICT)
    jrun("import-ict", "--ict", ict, "--emir", base, "-o", str(tmp_path / "o.yaml"))


def test_the_width_offset_becomes_width_loss(tmp_path):
    """2.0 * (w - 0.05) was imported as 2.0 mA/um on the full width: 1.56x
    looser than the ICT on a 0.14 um wire."""
    ict, base = _ict(tmp_path, ICT)
    jrun("import-ict", "--ict", ict, "--emir", base, "-o", str(tmp_path / "o.yaml"),
         "--conductor-pattern", "{layer}_conn")
    data = yaml.safe_load(open(tmp_path / "o.yaml"))
    m1 = next(v for k, v in data["layers"].items() if k.lower() == "metal1")
    assert m1["jmax_avg"] == 2.0 and m1["width_loss_um"] == 0.05
    assert "metal1_conn" in m1["sources"]["width_loss_um"]
    from emir.pdk_import import _width_loss
    assert _width_loss("2.0 * w") is None and _width_loss("sqrt( 3 * delta_T * w )") is None


# ------------------------------------------------------------------ process new


ALPHA = """*|DSPF 1.3
*|DESIGN "alpha"
*|NET VDD 1e-14
*|P (VDD I *1 0.0 0.0)
*|I (M1:s M1 s I 0 5.0 0.0)
R1 VDD n1 0.5 $met1 $L=5.0 $W=1.0
R2 n1 n2 2.0 $licon $VC=1
R3 n2 M1:s 1.0 $poly $L=1.0 $W=0.5
.END
"""


def test_an_unknown_via_stacking_is_left_for_the_person(tmp_path):
    """met1 and poly, no $lvl and no numbers: it wrote below met1, above poly
    (upside down), which satisfied E320."""
    dspf = write(tmp_path / "alpha.dspf", ALPHA)
    out = tmp_path / "p.yaml"
    d, _ = jrun("process", "new", "--from-dspf", dspf, "-o", str(out))
    v = d["vias"][0]
    assert v["below"] is None and v["above"] is None and "met1" in v["stacking"] \
        and "poly" in v["stacking"]
    text = out.read_text()
    assert "met1 or poly" in text
    data = yaml.safe_load(text)
    assert data["vias"]["licon"]["below"] is None


class _Tty(io.StringIO):
    def isatty(self):
        return True


def test_interactive_never_copies_a_source_unless_asked(tmp_path):
    data = build_model(from_dspf(DSPF), None)
    # em_tref 110 with a source; temp_reference empty; ea_eV 0.9 and Enter at
    # its source (none, not em_tref's); n 2 and `=` (the previous source)
    answers = ["110", "DM p1", "", "0.9", "", "2", "="]
    interactive(data, lambda d: None, stdin=_Tty("\n".join(answers) + "\n"),
                stderr=io.StringIO())
    assert data["sources"]["em_tref"] == "DM p1"
    assert data["derating"]["ea_eV"] == 0.9 and data["derating"]["n"] == 2
    assert data["sources"]["derating"] == "DM p1"         # from the `=` at n, not the Enter


# ------------------------------------------------------------------ the eval card


def test_the_ict_card_catches_limits_made_up_in_the_fill_in_file(tmp_path):
    sys.path.insert(0, os.path.join(ROOT, "agent", "evals"))
    import grade
    assert grade.filled_values({"em_tref": None, "layers": {"m1": {
        "jmax_avg": None, "sources": {"jmax_avg": None}}}}) == []
    data = {"em_tref": 105, "layers": {"m1": {"jmax_avg": 2.0, "sources": {
        "jmax_avg": "made up"}}}, "vias": {"c": {"sources": {"above": "inferred from ..."}}}}
    assert grade.filled_values(data) == ["em_tref", "layers/m1/jmax_avg",
                                         "layers/m1/sources/jmax_avg"]
    run = os.path.join(ROOT, "agent", "evals", "run.py")
    fake = f"{sys.executable} {os.path.join(ROOT, 'agent', 'evals', 'fake_agent.py')} --variant good"
    p = subprocess.run([sys.executable, run, "--cards", "ict-encrypted", "--agent-cmd", fake,
                        "--out", str(tmp_path / "ev")], capture_output=True, text=True,
                       cwd=ROOT)
    assert p.returncode == 0, p.stdout[-2000:] + p.stderr[-2000:]
    ws = tmp_path / "ev" / "ict-encrypted" / "ws"
    f = ws / "emir_out" / "mypdk.yaml"
    f.write_text(f.read_text().replace("    sheet_res: null", "    sheet_res: 0.125")
                 .replace("    jmax_avg: null", "    jmax_avg: 2.0", 1))
    card = os.path.join(ROOT, "agent", "evals", "cards", "04-ict-encrypted.yaml")
    g = subprocess.run([sys.executable, os.path.join(ROOT, "agent", "evals", "grade.py"),
                        "--card", card, "--ws", str(ws)], capture_output=True, text=True,
                       cwd=ROOT)
    out = g.stdout + g.stderr
    assert "FAIL" in out and "file:emir_out/mypdk.yaml" in out, out[-2000:]
