"""Process data entered by hand (emir/process_file.py): `emir process new`
writes the fill-in file for a DSPF's layers, `emir process check` (and `emir
check --process`) says what it lacks, the two forms design manuals print
(a width loss, Black's derating) are applied as printed, every report says
where the limits came from, and an ICT emir cannot read is refused.

Every number here is invented, and every expected value is worked out by
hand in the test that holds it.
"""
import io
import json

import os
import subprocess
import sys

import pytest
import yaml
from _helpers import CUR, DSPF, PROC, ROOT, emir, write

from emir import cli, mcp_tools
from emir.check import Report, check_process
from emir.engine import Process, black_factor
from emir.errors import EmirError
from emir.process_file import (build_model, empty_values, from_dspf, interactive, render,
                               required_empty, unsourced)

#: two metals and a via between them, by $lvl; a MOS decap (gate on VDD,
#: d/s/b on VSS) and a MIM capacitor device
BLOCK = """*|DSPF 1.3
*|DESIGN "blk"
*|OPERATING_TEMPERATURE 25
*|NET VDD 1e-14
*|P (VDD I *1 0.0 0.0)
*|S (VDD:1 5.0 0.0)
*|S (VDD:2 5.0 0.0)
*|I (MD:g MD g I 0 9.0 0.0)
R1 VDD VDD:1 0.5 $m1 $L=5.0 $W=1.0 $LLX=0 $LLY=-0.5 $URX=5 $URY=0.5 $lvl=1
R2 VDD:1 VDD:2 2.0 $v12 $VC=2 $LLX=4.9 $LLY=-0.1 $URX=5.1 $URY=0.1 $lvl=2
R3 VDD:2 MD:g 0.25 $m2 $L=4.0 $W=2.0 $LLX=5 $LLY=-1 $URX=9 $URY=1 $lvl=3
CC1 VDD:2 0 mimcap area=1e-11 pj=1.3e-5
*|NET VSS 1e-14
*|P (VSS I *1 0.0 -5.0)
*|I (MD:d MD d I 0 9.0 -5.0)
*|I (MD:s MD s I 0 9.5 -5.0)
R4 VSS MD:d 0.5 $m1 $L=5 $W=1.0 $lvl=1
R5 MD:d MD:s 0.1 $m1 $L=1 $W=1.0 $lvl=1
MD MD:d MD:g MD:s VSS nmos_cap L=2e-06 W=4e-06
.END
"""

#: the sample's VDD currents, 4 mA in at the pin
SAMPLE_CUR = CUR


def jrun(*args, rc=0, cwd=None, stdin=None):
    p = subprocess.run([sys.executable, "-m", "emir", *args, "--format", "json"],
                       capture_output=True, text=True, cwd=cwd or ROOT,
                       stdin=stdin if stdin is not None else subprocess.DEVNULL)
    assert p.returncode == rc, (p.returncode, p.stdout[-2000:], p.stderr[-2000:])
    return json.loads(p.stdout), p


def filled(tmp_path, **over):
    """The sample's own numbers typed into a scaffold of the sample DSPF, with
    sources: the hand-entered twin of emir/data/sample/process.yaml."""
    data = build_model(from_dspf(DSPF), "demo")
    data.update(em_tref=100.0, temp_reference=50.0)
    data["sources"].update(em_tref="Demo DM rev 1, table 3", temp_reference="Demo DM, table 2")
    m1 = data["layers"]["metal1"]
    m1.update(sheet_res=0.125, tc1=0.004, jmax_avg=3.2)
    m1["sources"].update(sheet_res="Demo DM, table 2", tc1="Demo DM, table 2",
                         jmax_avg="Demo DM, table 5")
    c = data["vias"]["cont"]
    c.update(below="poly", res=24.0, vcwidth=0.13, imax_avg=0.75)
    c["sources"].update(below="Demo DM, stack figure 1", above="Demo DM, stack figure 1",
                        res="Demo DM, table 6", vcwidth="Demo DM, table 6",
                        imax_avg="Demo DM, table 7")
    for path, v in over.items():
        node = data
        *head, last = path.split("/")
        for k in head:
            node = node[k]
        if v is None:
            node.pop(last, None)
        else:
            node[last] = v
    p = tmp_path / "hand.yaml"
    p.write_text(render(data, from_dspf(DSPF), DSPF), encoding="utf-8")
    return str(p)


# ------------------------------------------------------------------ process new


def test_new_covers_exactly_the_dspf_layers_with_every_value_empty(tmp_path):
    out = tmp_path / "p.yaml"
    d, _ = jrun("process", "new", "--from-dspf", DSPF, "-o", str(out), "--pdk", "demo")
    assert d["schema"] == "emir.process_new/1" and d["provenance"] == "hand-entered"
    assert [x["name"] for x in d["layers"]] == ["metal1"]
    assert [x["name"] for x in d["vias"]] == ["cont"]
    # the contact joins metal1 ($lvl 1) above its own $lvl 0; below it is not in the DSPF
    assert d["vias"][0]["above"] == "metal1" and d["vias"][0]["below"] is None
    assert d["vias"][0]["stacking"].startswith("inferred from the DSPF")
    data = yaml.safe_load(out.read_text())
    assert data["dspf_layers"] == {"cont": "cont", "metal1": "metal1"}
    assert data["provenance"] == "hand-entered" and data["pdk"] == "demo"
    m1 = data["layers"]["metal1"]
    assert all(v is None for k, v in m1.items() if k != "sources")
    assert set(m1["sources"]) == set(m1) - {"sources"}
    assert data["vias"]["cont"]["sources"]["above"].startswith("inferred")
    assert required_empty(data) == ["em_tref", "layers/metal1/sheet_res", "vias/cont/res",
                                    "vias/cont/below"]
    assert [x["path"] for x in d["empty"]["required"]] == [
        "em_tref", "layers/metal1/sheet_res", "vias/cont/below", "vias/cont/res"]
    # each value's unit is on its line, for the person who types it
    text = out.read_text()
    assert "jmax_avg: null" in text and "# mA/um" in text and "# R. ohm per cut" in text
    assert d["next_steps"][0]["command"].startswith("emir process check")


def test_new_infers_stacking_and_writes_no_cap_models(tmp_path):
    dspf = write(tmp_path / "blk.dspf", BLOCK)
    info = from_dspf(dspf)
    assert [e.name for e in info["layers"]] == ["m1", "m2"]
    v12 = info["vias"][0]
    assert (v12.name, v12.below, v12.above) == ("v12", "m1", "m2")    # by $lvl 1 < 3
    assert "by their $lvl" in v12.stack_note
    data = build_model(info, None)
    # capacitor devices and MOS decaps are not asked for: nothing sizes them
    # (cc-grid has no dynamic IR, and decap-aware EM was removed)
    assert "cap_models" not in data and "caps" not in info
    # a file written from it parses, and passes the schema's shape
    text = render(data, info, dspf)
    assert yaml.safe_load(text)["vias"]["v12"]["below"] == "m1"


def test_new_orders_a_via_by_the_numbers_in_names_without_lvl(tmp_path):
    text = "\n".join(l.split(" $lvl")[0] if l.startswith("R") else l
                     for l in BLOCK.splitlines()).replace("$m1", "$metal1").replace(
        "$m2", "$metal2").replace("$v12", "$via1")
    info = from_dspf(write(tmp_path / "nolvl.dspf", text))
    v = info["vias"][0]
    assert (v.below, v.above) == ("metal1", "metal2") and "numbers in their names" in v.stack_note


def test_new_from_lists_and_its_refusals(tmp_path):
    out = tmp_path / "l.yaml"
    d, _ = jrun("process", "new", "--layers", "M1,M2", "--vias", "V1", "-o", str(out))
    assert d["dspf"] is None and [x["name"] for x in d["layers"]] == ["M1", "M2"]
    assert d["vias"] == [{"name": "V1", "below": None, "above": None, "stacking": None}]
    # never overwrites: a process file may hold typed values
    d, _ = jrun("process", "new", "--layers", "M1", "-o", str(out), rc=2)
    assert d["code"] == "EMIR-E326"
    d, _ = jrun("process", "new", "-o", str(tmp_path / "x.yaml"), rc=2)
    assert d["code"] == "EMIR-E328"
    d, _ = jrun("process", "new", "--from-dspf", DSPF, "--layers", "M1", "-o",
                str(tmp_path / "y.yaml"), rc=2)
    assert d["code"] == "EMIR-E513"
    bare = write(tmp_path / "bare.dspf", "*|DSPF 1.3\n*|NET A 0\nR1 A A:1 1.0\n.END\n")
    d, _ = jrun("process", "new", "--from-dspf", bare, "-o", str(tmp_path / "z.yaml"), rc=2)
    assert d["code"] == "EMIR-E328"
    assert not (tmp_path / "z.yaml").exists()


def test_interactive_refuses_a_stdin_that_is_not_a_terminal(tmp_path):
    out = tmp_path / "i.yaml"
    d, _ = jrun("process", "new", "--from-dspf", DSPF, "-o", str(out), "--interactive", rc=2)
    assert d["code"] == "EMIR-E327" and not out.exists()


class _Tty(io.StringIO):
    def isatty(self):
        return True


def test_interactive_asks_each_value_refuses_non_numbers_and_asks_sources(tmp_path):
    info = from_dspf(DSPF)
    data = build_model(info, "demo")
    writes = []
    # em_tref: "hot" is refused, then 105 with its source; temp_reference empty;
    # derating ea_eV and n empty; metal1 sheet_res 0.125 with `=` = the same
    # source; then the input ends (Ctrl-D): what was typed is written
    answers = ["hot", "105", "DM rev 2, table 9-4", "", "", "", "0.125", "="]
    err = io.StringIO()
    n = interactive(data, lambda d: writes.append(json.dumps(d)), stdin=_Tty(
        "\n".join(answers) + "\n"), stderr=err)
    assert n == 2
    assert data["em_tref"] == 105.0 and data["sources"]["em_tref"] == "DM rev 2, table 9-4"
    assert data["temp_reference"] is None and data["derating"]["ea_eV"] is None
    m1 = data["layers"]["metal1"]
    assert m1["sheet_res"] == 0.125 and m1["sources"]["sheet_res"] == "DM rev 2, table 9-4"
    assert m1["tc1"] is None                     # the input ended before it
    assert "not a number: 'hot'" in err.getvalue() and "stopped" in err.getvalue()
    assert writes and json.loads(writes[-1])["em_tref"] == 105.0
    with pytest.raises(EmirError) as e:
        interactive(build_model(info, None), lambda d: None, stdin=io.StringIO("1\n"))
    assert e.value.code == "EMIR-E327"


def test_interactive_notes_a_value_outside_its_range_and_keeps_it(tmp_path):
    data = build_model(from_dspf(DSPF), None)
    err = io.StringIO()
    # em_tref typed in kelvin: kept as typed, with a note naming the slip
    interactive(data, lambda d: None, stdin=_Tty("378.15\nDM\n"), stderr=err)
    assert data["em_tref"] == 378.15 and "kelvin" in err.getvalue()


# ------------------------------------------------------------------ process check


def test_check_of_the_empty_file_exits_2_and_lists_what_is_missing(tmp_path):
    out = tmp_path / "p.yaml"
    jrun("process", "new", "--from-dspf", DSPF, "-o", str(out))
    d, _ = jrun("process", "check", str(out), "--dspf", DSPF, rc=2)
    assert d["schema"] == "emir.process_check/1" and not d["passed"]
    assert [f["code"] for f in d["findings"] if f["level"] == "FAIL"] == ["EMIR-E320"] * 4
    assert d["provenance"] == "hand-entered" and d["coverage"]["missing"] == []
    assert d["inferred"] == ["vias/cont/above"]
    assert any(f["code"] == "EMIR-W324" for f in d["findings"])
    assert d["next_steps"][0]["command"].startswith(f"emir process check {out}")
    # emir check --process runs the same checks
    c, _ = jrun("check", "--process", str(out), rc=2)
    assert {f["code"] for f in c["findings"] if f["level"] == "FAIL"} == {"EMIR-E320"}


def test_check_of_a_filled_file_passes_and_plans_the_run(tmp_path):
    p = filled(tmp_path)
    d, _ = jrun("process", "check", p, "--dspf", DSPF)
    assert d["passed"] and d["unsourced"] == [] and d["inferred"] == []
    assert d["next_steps"][0]["command"].startswith("emir next --dspf")
    # the sample's numbers: the same ratios as the illustrative sample file
    d, _ = jrun("run", DSPF, "--process", p, "--currents", SAMPLE_CUR)
    assert d["worst_net"]["ratio"] == pytest.approx(4 / (3.2 * 0.75), rel=1e-9)


def test_values_without_a_source_are_named_on_every_run(tmp_path):
    p = filled(tmp_path, **{"layers/metal1/sources/jmax_avg": None})
    d, _ = jrun("process", "check", p)
    assert d["passed"] and d["unsourced"] == ["layers/metal1/jmax_avg"]
    w = [f for f in d["findings"] if f["code"] == "EMIR-W320"]
    assert w and "layers/metal1/jmax_avg" in w[0]["message"]
    # the run's input check says it, and so does a run that skips the check
    d, _ = jrun("run", DSPF, "--process", p, "--currents", SAMPLE_CUR)
    assert "EMIR-W320" in {f["code"] for f in d["input_check"]["warnings"]}
    d, _ = jrun("run", DSPF, "--process", p, "--currents", SAMPLE_CUR, "--no-check")
    assert "EMIR-W320" in {n["code"] for n in d["notices"]}
    entry = next(e for e in d["limits_provenance"]["entries"] if e["name"] == "metal1")
    assert entry["unsourced"] == ["jmax_avg"] and entry["provenance"] == "hand-entered"


@pytest.mark.parametrize("path,value,likely", [
    ("layers/metal1/jmax_avg", 0.0032, "A/um"),          # typed in A/um
    ("layers/metal1/jmax_avg", 3200.0, "uA/um"),          # typed in uA/um
    ("layers/metal1/sheet_res", 125.0, "mOhm"),           # typed in mOhm/sq
    ("em_tref", 373.15, "kelvin"),                        # typed in K
    ("vias/cont/imax_avg", 0.00075, "A per cut"),         # typed in A
    ("vias/cont/vcwidth", 130.0, "nm"),                   # typed in nm
    ("layers/metal1/tc1", 4000.0, "ppm/C"),               # typed in ppm/C
])
def test_likely_unit_slips_are_named(tmp_path, path, value, likely):
    """In a hand-entered file a slip on a temperature or a limit blocks the
    run (EMIR-E331) until the person confirms the value; elsewhere it is a
    warning (EMIR-W321). Either way the message names the likely slip."""
    from emir.process_file import _limiting
    p = filled(tmp_path, **{path: value})
    rep = Report()
    check_process(p, rep)
    code = "EMIR-E331" if _limiting(path) else "EMIR-W321"
    w = [f for f in rep.findings if f.code == code]
    assert len(w) == 1 and path in w[0].message and likely in w[0].message, \
        [f.message for f in rep.findings]
    assert (not rep.passed) == _limiting(path)


def test_limits_out_of_order_and_an_upper_metal_more_resistive(tmp_path):
    p = filled(tmp_path, **{"vias/cont/imax_rms": 0.5, "vias/cont/sources/imax_rms": "DM"})
    rep = Report()
    check_process(p, rep)
    w = [f.message for f in rep.findings if f.code == "EMIR-W322"]
    assert len(w) == 1 and "rms 0.5 < avg 0.75" in w[0]
    # metal2 over metal1, at ten times its sheet resistance (a mOhm slip, say)
    data = yaml.safe_load(open(PROC))
    data["layers"]["Metal2"] = {"gds": [103, 0], "sheet_res": 1.25}
    data["vias"]["Via1"] = {"gds": [104, 0], "below": "Metal1", "above": "Metal2", "res": 4.0}
    q = write(tmp_path / "stack.yaml", yaml.safe_dump(data))
    rep = Report()
    check_process(q, rep)
    assert [f.code for f in rep.findings if f.code == "EMIR-W323"] == ["EMIR-W323"]


def test_malformed_provenance_and_sources(tmp_path):
    p = filled(tmp_path, provenance="typed")
    rep = Report()
    assert check_process(p, rep) is None
    assert "EMIR-E329" in rep.codes("FAIL")
    p = filled(tmp_path, **{"layers/metal1/sources/jmax_avgg": "DM"})
    rep = Report()
    check_process(p, rep)
    assert any(f.code == "EMIR-W325" and "jmax_avgg" in f.message for f in rep.findings)


def test_shipped_files_say_their_provenance_and_the_template_is_clean():
    for name in ("sky130", "gf180mcu", "ihp_sg13g2"):
        data = yaml.safe_load(open(os.path.join(ROOT, "pdks", f"{name}.yaml")))
        assert data["provenance"] == "public-pdk-data" and unsourced(data) == []
    rep = Report()
    check_process(PROC, rep)
    assert "EMIR-I321" in rep.codes("INFO")       # the sample does not say
    assert not [f for f in rep.findings if f.code.startswith("EMIR-W32")]


# ------------------------------------------------------------------ the two forms


def _strap_proc(**metal1):
    from emir.validate import STRAP_METAL1
    return {"warn_fraction": 0.8, "temp_reference": 100, "em_tref": 100, "vias": {},
            "layers": {"Metal1": {**STRAP_METAL1, **metal1}}}


def test_width_loss_by_hand_and_its_fix(tmp_path):
    """The sample: 4 mA in metal1, 0.75 um wide, at 3.2 mA/um. With dW = 0.05 um
    the limit is 3.2 x (0.75 - 0.05) = 2.24 mA, so 4 / 2.24 = 1.7857x (1.6667x
    without); the fix widens W - dW by the ratio: 0.05 + 0.70 x 1.7857 = 1.30 um,
    rounded up past it to 1.305 um."""
    p = filled(tmp_path, **{"layers/metal1/width_loss_um": 0.05,
                            "layers/metal1/sources/width_loss_um": "DM, table 5"})
    d, _ = jrun("run", DSPF, "--process", p, "--currents", SAMPLE_CUR)
    w = d["nets"][0]["worst_items"][0]
    assert w["ratio"] == pytest.approx(4 / (3.2 * 0.70), rel=1e-9)
    assert w["width_loss_um"] == 0.05
    assert w["fix"].startswith("metal1: widen 0.750 -> 1.305 um")
    rep = Report()
    check_process(p, rep)
    assert "EMIR-I323" in rep.codes("INFO")


def test_a_wire_no_wider_than_its_width_loss_is_refused(tmp_path):
    p = filled(tmp_path, **{"layers/metal1/width_loss_um": 0.75,
                            "layers/metal1/sources/width_loss_um": "DM"})
    d, _ = jrun("process", "check", p, "--dspf", DSPF, rc=2)
    assert "EMIR-E323" in [f["code"] for f in d["findings"] if f["level"] == "FAIL"]
    d, _ = jrun("run", DSPF, "--process", p, "--currents", SAMPLE_CUR, "--no-check", rc=2)
    assert d["code"] == "EMIR-E323"


def test_black_derating_by_hand():
    """Ea 0.9 eV, n 2, em_tref 105 C, analysed at 125 C:
    exp[(0.9 / (2 x 8.617333262e-5)) (1/398.15 - 1/378.15)] = exp(-0.693679) = 0.499734;
    1 at em_tref; above 1 below it."""
    assert black_factor(0.9, 2, 125.0, 105.0) == pytest.approx(0.499734, rel=1e-6)
    assert black_factor(0.9, 2, 105.0, 105.0) == 1.0
    assert black_factor(0.9, 2, 25.0, 105.0) > 1.0
    data = _strap_proc()
    data["em_tref"] = 105
    data["derating"] = {"model": "black", "ea_eV": 0.9, "n": 2}
    proc = Process(data, temp=125.0)
    assert proc.derate == pytest.approx(0.499734, rel=1e-6)
    assert proc.layers["Metal1"]["jmax"]["avg"] == pytest.approx(1e-3 * 0.499734, rel=1e-6)
    rep = Report()
    p = write_yaml(data)
    check_process(p, rep, em_temp=125.0)
    i = [f.message for f in rep.findings if f.code == "EMIR-I322"]
    assert i and "x0.4997 at 125 C" in i[0]
    assert "EMIR-W315" not in rep.codes("WARN")    # derated: not the "no derating" warning


def write_yaml(data):
    import tempfile
    fd, p = tempfile.mkstemp(suffix=".yaml")
    os.close(fd)
    with open(p, "w") as f:
        yaml.safe_dump(data, f)
    return p


@pytest.mark.parametrize("derating,jf,code", [
    ({"model": "black", "ea_eV": 0.9, "n": 2}, {25: 2.0, 100: 1.0}, "EMIR-E321"),
    ({"model": "black", "ea_eV": 0.9, "n": None}, None, "EMIR-E322"),
    ({"model": "arrhenius", "ea_eV": 0.9, "n": 2}, None, "EMIR-E322"),
    ({"model": "black", "ea_eV": -0.9, "n": 2}, None, "EMIR-E322"),
])
def test_derating_refusals(derating, jf, code):
    data = _strap_proc()
    data["derating"] = derating
    if jf:
        data["jmax_factor"] = jf
    with pytest.raises(EmirError) as e:
        Process(data)
    assert e.value.code == code
    rep = Report()
    assert check_process(write_yaml(data), rep) is None and code in rep.codes("FAIL")


def test_empty_values_are_not_given():
    data = _strap_proc(tc1=None, jmax_peak=None)
    data.update(derating={"model": "black", "ea_eV": None, "n": None}, temp_reference=None,
                warn_fraction=None)
    data["layers"]["Metal1"]["gds"] = None
    proc = Process(data)
    m1 = proc.layers["Metal1"]
    assert proc.derating is None and proc.derate == 1.0 and proc.temp_ref == 25.0
    assert proc.warn_fraction == 0.8 and m1["gds"] is None
    assert m1["jmax"]["peak"] is None
    p = write_yaml(data)
    Process.load(p)                                # no path needs GDS numbers
    assert {x["path"] for x in empty_values(data)["optional"]} >= {
        "temp_reference", "derating/ea_eV", "layers/Metal1/gds"}


# ------------------------------------------------------------------ what reports say


def test_reports_say_per_layer_where_the_limits_came_from(tmp_path):
    p = filled(tmp_path)
    s = tmp_path / "s.json"
    d, _ = jrun("run", DSPF, "--process", p, "--currents", SAMPLE_CUR, "--summary-out",
                str(s), "--html", str(tmp_path / "html"))
    lp = d["limits_provenance"]
    assert lp["file"] == "hand-entered"
    assert lp["temperature"]["em_tref_source"] == "Demo DM rev 1, table 3"
    m1 = next(e for e in lp["entries"] if e["name"] == "metal1")
    assert m1["sources"]["jmax_avg"] == "Demo DM, table 5" and m1["limits"] == ["jmax_avg"]
    r, _ = jrun("report", "--from", str(s))
    text = r["markdown"]
    assert "## Where the limits came from" in text
    assert "layer metal1: limits entered by hand -- jmax_avg from Demo DM, table 5" in text
    assert "via cont: limits entered by hand -- imax_avg from Demo DM, table 7" in text
    html = (tmp_path / "html" / "railblock_VDD.html").read_text()
    assert "limits from" in html and "entered by hand" in html
    # the shipped files: public data; the sample: not stated
    d, _ = jrun("run", DSPF, "--process", PROC, "--currents", SAMPLE_CUR)
    assert d["limits_provenance"]["file"] is None
    assert d["limits_provenance"]["entries"][0]["provenance"] is None


# ------------------------------------------------------------------ the ICT


def _ict_files(tmp_path):
    import gzip
    readable = ('process "DEMO" {\n    temp_reference 50.0\n    em_tref 100.0\n}\n'
                'conductor "metal1_conn" {\n    em_jmax_dc_avg EQU 2.0 * w\n}\n')
    return {
        "binary.ict": bytes(range(256)) * 8,
        "gzip.ict": gzip.compress(readable.encode()),
        "openssl.ict": b"Salted__" + bytes(range(1, 200)),
        "protected.ict": (b"`pragma protect begin_protected\n`pragma protect data_block\n"
                          b"QUJDREVGR0g=\n`pragma protect end_protected\n"),
        "pgp.ict": b"-----BEGIN PGP MESSAGE-----\nhQEMA\n-----END PGP MESSAGE-----\n",
        "marked.ict": b"# ENCRYPTED with the vendor's key\nAbCdEf0123\n",
        "noise.ict": bytes((i * 37 + 11) % 256 for i in range(4000) if (i * 37 + 11) % 256),
    }, readable


def test_an_ict_emir_cannot_read_is_refused_and_points_to_hand_entry(tmp_path):
    files, _ = _ict_files(tmp_path)
    target = tmp_path / "p.yaml"
    for name, body in files.items():
        (tmp_path / name).write_bytes(body)
        d, _ = jrun("import-ict", "--ict", str(tmp_path / name), "--emir", str(target), rc=2)
        assert d["code"] == "EMIR-E325", (name, d)
        assert "never tries to decrypt" in d["message"] and not target.exists()
        assert d["next_steps"][0]["command"] == "emir process new --help --format json"


def test_a_readable_ict_records_its_sources_and_provenance(tmp_path):
    _, readable = _ict_files(tmp_path)
    ict = write(tmp_path / "demo.ict", readable.replace("\n}\nconductor",
                                                       "\n}\n# 25\xb0C note\nconductor"))
    # a stray byte in another code page (a degree sign in a comment) still reads
    raw = open(ict, "rb").read().replace("\xb0".encode(), b"\xb0")
    open(ict, "wb").write(raw)
    base = {"pdk": "demo", "layers": {"Metal1": {"gds": [101, 0], "sheet_res": 0.1}}}
    p = write(tmp_path / "p.yaml", yaml.safe_dump(base))
    d, _ = jrun("import-ict", "--ict", ict, "--emir", p, "-o", str(tmp_path / "o.yaml"))
    assert d["provenance"] == "imported-ict"
    o = yaml.safe_load(open(tmp_path / "o.yaml"))
    assert o["layers"]["Metal1"]["jmax_avg"] == 2.0
    assert o["layers"]["Metal1"]["sources"]["jmax_avg"] == "ICT demo.ict: conductor metal1_conn"
    assert o["sources"]["em_tref"] == "ICT demo.ict: process block, em_tref"
    # onto a file entered by hand: mixed
    p = write(tmp_path / "h.yaml", yaml.safe_dump({**base, "provenance": "hand-entered"}))
    d, _ = jrun("import-ict", "--ict", ict, "--emir", p, "-o", str(tmp_path / "m.yaml"))
    assert d["provenance"] == "mixed"


# ------------------------------------------------------------------ the agent layer


def test_next_plans_the_fill_in_file_when_no_process_is_given():
    d, _ = jrun("next", "--dspf", DSPF, "--net", "VDD", "--currents", SAMPLE_CUR)
    ids = [s["id"] for s in d["steps"]]
    assert ids[:3] == ["selftest", "process-new", "process-check"]
    new = d["steps"][1]
    assert new["needs_human"] and new["ask"] and not new["licensed_tool"]
    assert new["command"].startswith("emir process new --from-dspf")
    assert d["steps"][2]["after"] == ["process-new"]
    assert "check" not in ids and "run" not in ids
    d, _ = jrun("next", "--dspf", DSPF, "--process", PROC, "--net", "VDD", "--currents",
                SAMPLE_CUR)
    assert "process-new" not in [s["id"] for s in d["steps"]]


def test_the_mcp_tools_of_the_sub_commands(tmp_path, monkeypatch):
    specs = mcp_tools.specs()
    new, chk = specs["emir_process_new"], specs["emir_process_check"]
    assert "interactive" not in {p.name for p in new.params}       # a person's, never MCP's
    assert {p.name for p in new.params} >= {"from_dspf", "out", "pdk", "layers", "vias"}
    assert chk.read_only and not new.read_only and new.confirm == "conditional"
    assert mcp_tools.tool_call_for(f"emir process check x.yaml --dspf {DSPF}") == {
        "name": "emir_process_check", "arguments": {"process": "x.yaml", "dspf": DSPF}}
    monkeypatch.chdir(tmp_path)
    rc, doc, line = mcp_tools.call(new, {"from_dspf": DSPF, "out": "p.yaml"})
    assert rc == 0 and doc["schema"] == "emir.process_new/1" and (tmp_path / "p.yaml").exists()
    assert line.startswith("emir process new ")
    rc, doc, _ = mcp_tools.call(chk, {"process": "p.yaml"})
    assert rc == 2 and doc["schema"] == "emir.process_check/1" and not doc["passed"]
    # over emir_cli too
    rc, doc, _ = mcp_tools.call_line("emir process check p.yaml")
    assert rc == 2 and doc["schema"] == "emir.process_check/1"


def test_the_process_command_and_its_usage_errors():
    assert cli.SUBCOMMANDS["process"] == ("new", "check")
    p = emir("process", "--format", "json")
    d = json.loads(p.stdout)
    assert p.returncode == 2 and d["code"] == "EMIR-E507"
    assert d["next_steps"][0]["command"] == "emir process --help --format json"
    p = emir("process", "new", "--bogus", "--format", "json")
    assert json.loads(p.stdout)["code"] == "EMIR-E507"
    # --format before the sub-command is passed on
    p = emir("process", "--format", "json", "check", PROC)
    assert p.returncode == 0 and json.loads(p.stdout)["schema"] == "emir.process_check/1"


def test_schemas_hold_the_new_documents(tmp_path):
    jsonschema = pytest.importorskip("jsonschema")
    from emir import schemas
    out = tmp_path / "p.yaml"
    d, _ = jrun("process", "new", "--from-dspf", DSPF, "-o", str(out))
    jsonschema.Draft202012Validator(schemas.get("process_new")).validate(d)
    c, _ = jrun("process", "check", str(out), "--dspf", DSPF, rc=2)
    jsonschema.Draft202012Validator(schemas.get("process_check")).validate(c)
    # the fill-in file itself is a process file as the schema describes it
    data = json.loads(json.dumps(yaml.safe_load(out.read_text())))
    jsonschema.Draft202012Validator(schemas.get("process")).validate(data)


def test_a_dspf_with_level_numbers_only_maps_them(tmp_path):
    """Resistors that carry only `$lvl=N` become entries `lvlN`, mapped by the
    level number (`dspf_layers: {N: lvlN}`), so the run finds them."""
    text = BLOCK.replace(" $m1 ", " ").replace(" $m2 ", " ").replace(" $v12 ", " ")
    dspf = write(tmp_path / "lvl.dspf", text)
    info = from_dspf(dspf)
    assert [e.name for e in info["layers"]] == ["lvl1", "lvl3"]
    assert info["dspf_layers"] == {1: "lvl1", 2: "lvl2", 3: "lvl3"}
    v = info["vias"][0]
    assert (v.name, v.below, v.above) == ("lvl2", "lvl1", "lvl3")
    data = build_model(info, None)
    out = tmp_path / "lvl.yaml"
    out.write_text(render(data, info, dspf), encoding="utf-8")
    back = yaml.safe_load(out.read_text())
    assert back["dspf_layers"] == {1: "lvl1", 2: "lvl2", 3: "lvl3"}
    from types import SimpleNamespace

    from emir.dspf import map_layers, parse
    lmap = map_layers(parse(dspf), SimpleNamespace(layers=back["layers"], vias=back["vias"],
                                                   data=back))
    assert lmap == {"lvl:1": ("layer", "lvl1"), "lvl:2": ("via", "lvl2"),
                    "lvl:3": ("layer", "lvl3")}


def test_a_malformed_section_is_coded_not_a_crash(tmp_path):
    p = write(tmp_path / "bad.yaml", "em_tref: 100\nlayers:\n  M1: {gds: [1, 0], sheet_res: 0.1}\n"
                                    "vias: [V1]\n")
    d, _ = jrun("run", DSPF, "--net", "VDD", "--process", p, "--currents", SAMPLE_CUR,
                "--no-check", rc=2)
    assert d["code"] == "EMIR-E315"
    d, _ = jrun("process", "check", p, rc=2)
    assert "EMIR-E304" in [f["code"] for f in d["findings"]]
