"""The open-simulator path without a simulator: netlist trees and their
rewriting (`emir/opensim_netlist.py`), save sets and names sidecars, `emir
save-lines --sim`, `emir probe`, the input checks of `emir simulate`, and the
`emir next` rules (`emir/plan_opensim.py`). Runs with real Xyce / ngspice are
in tests/test_opensim_sims.py."""
import json
import os
import shutil

import jsonschema
import pytest
from _helpers import DSPF, PROC, ROOT, emir, write

from emir import plan as P
from emir import schemas
from emir.dspf import parse
from emir.errors import EmirError, collecting
from emir.opensim import (infer_names, lead, load_probes, ngspice_name, probe_keys,
                          save_set, split_path, xyce_name)
from emir.opensim_netlist import Tree, Writer, tokens
from emir.waves import Waves

EX = os.path.join(ROOT, "examples", "02_xyce")
NO_SIM = {**os.environ, "XYCE_BIN": "/nonexistent/Xyce", "NGSPICE_BIN": "/nonexistent/ngspice",
          "SPECTRE_BIN": "/nonexistent/spectre", "EMIR_SITE": os.devnull}


def jdoc(p, rc=0):
    assert p.returncode == rc, (p.returncode, p.stdout[-1500:], p.stderr[-1500:])
    d = json.loads(p.stdout)
    name = d["schema"].split(".", 1)[1].split("/")[0]
    jsonschema.Draft202012Validator(schemas.get(name)).validate(d)
    return d


@pytest.fixture
def ex(tmp_path):
    """A copy of the loadrail example's inputs."""
    for f in ("loadrail.dspf", "models.sp", "cc.sp", "tb_cc.cir", "tb_rcc.cir"):
        shutil.copy(os.path.join(EX, f), tmp_path / f)
    return tmp_path


# ------------------------------------------------------------ tokens and trees


@pytest.mark.parametrize("text, words", [
    ("R1 a b 1k ; a comment", ["R1", "a", "b", "1k"]),
    ("R1 a b 1k // a comment", ["R1", "a", "b", "1k"]),
    ("Rm1 VDD VDD:1 1.0 $metal1 $L=5 $W=0.75", ["Rm1", "VDD", "VDD:1", "1.0"]),
    ("X1 a b sub W = 1u L= 2u", ["X1", "a", "b", "sub", "W=1u", "L=2u"]),
    ("X1 a b sub W={w * 2} L='l + 1'", ["X1", "a", "b", "sub", "W={w * 2}", "L='l + 1'"]),
    ("R1 a$b c 1", ["R1", "a$b", "c", "1"]),
])
def test_tokens(text, words):
    assert tokens(text) == words


def test_tree_reads_titles_continuations_scopes_and_includes(tmp_path):
    (tmp_path / "sub").mkdir()
    write(tmp_path / "sub" / "inner.sp", ".model nx NMOS level=1\n")
    write(tmp_path / "sub" / "blk.sp",
          ".include inner.sp\n"
          ".subckt blk VDD VSS G params: W=1u\n"
          "M1 VDD G VSS VSS nx\n"
          "+ W=1u L=1u\n"
          "* a comment between\n"
          "Q1 c b e npn\n"
          "Q2 c b e s npn 2\n"
          "X1 VDD G VSS VSS wrap W=2u\n"
          ".ends blk\n")
    tb = write(tmp_path / "tb.cir",
               "M0 this is the title, not a device\n"
               ".include sub/blk.sp\n"
               ".lib 'models/lib.sp' tt\n"
               "XI0 vdd 0 g blk\n"
               ".control\nrun\nR9 inside control 1\n.endc\n"
               ".tran 1n 10n\n"
               ".end\n"
               "R8 after end 1\n")
    t = Tree(tb)
    assert [os.path.basename(p) for p in t.order] == ["tb.cir", "blk.sp", "inner.sp"]
    assert [e.name for e in t.top_elements] == ["XI0"]          # not the title, R9, R8
    blk = t.subckts["BLK"]
    assert blk.ports == ["VDD", "VSS", "G"]
    m1, q1, q2, x1 = blk.elements
    assert m1.nodes == ["VDD", "G", "VSS", "VSS"] and m1.ref == "nx"
    assert q1.nodes == ["c", "b", "e"] and q2.nodes == ["c", "b", "e", "s"]
    assert x1.nodes == ["VDD", "G", "VSS", "VSS"] and x1.ref == "wrap"
    assert t.instance_paths("blk") == [["XI0"]]
    assert t.has_control and t.analyses == [".tran 1n 10n"]
    lib = [i for i in t.top.includes if i.kind == "lib"][0]
    assert lib.target == os.path.join(str(tmp_path), "models", "lib.sp") and lib.section == "tt"


def test_nested_instances_and_several_instances(tmp_path):
    tb = write(tmp_path / "tb.cir", "* t\n.subckt blk a\nR1 a 0 1\n.ends\n"
               ".subckt top a\nXI0 a blk\n.ends\nXT a top\nXB b blk\n.end\n")
    t = Tree(tb)
    assert sorted(t.instance_paths("blk")) == [["XB"], ["XT", "XI0"]]


def test_missing_include_and_binary_netlist_are_coded(tmp_path):
    tb = write(tmp_path / "tb.cir", "* t\n.include nope.sp\n.end\n")
    with pytest.raises(EmirError) as e:
        Tree(tb)
    assert e.value.code == "EMIR-E101" and "included from" in e.value.message
    b = tmp_path / "b.cir"
    b.write_bytes(b"\x00\x01binary")
    with pytest.raises(EmirError) as e:
        Tree(str(b))
    assert e.value.code == "EMIR-E260"


def test_writer_copies_only_what_changes_and_rebases_includes(ex, tmp_path):
    t = Tree(str(ex / "tb_rcc.cir"))
    out = tmp_path / "o" / "deck.cir"
    copies = Writer(t, str(out), "xyce", inject=["* injected"], strip=True).write()
    # the DSPF has $ annotations: copied without them; models.sp is referenced
    assert sorted(os.path.basename(v) for v in copies.values()) == ["deck.cir",
                                                                    "loadrail.xyce.dspf"]
    deck = out.read_text()
    assert ".include ../models.sp" in deck and ".include loadrail.xyce.dspf" in deck
    assert deck.index("* injected") < deck.index(".end")
    dspf = (tmp_path / "o" / "loadrail.xyce.dspf").read_text()
    assert "Rm1 VDD VDD:1 1.000000\n" in dspf and "$metal1" not in dspf
    assert "*|I (XMN0:d XMN0 d I 0 15.0000 0.0000)" in dspf      # comments untouched
    with pytest.raises(EmirError) as e:
        Writer(t, str(ex / "tb_rcc.cir"), "x").write()
    assert e.value.code == "EMIR-E513"


def test_writer_keeps_absolute_includes_absolute(tmp_path):
    (tmp_path / "lib").mkdir()
    lib = write(tmp_path / "lib" / "models.sp", ".model nx NMOS level=1\n")
    tb = write(tmp_path / "tb.cir", f"* t\n.include {lib}\n.include rel.sp\n.end\n")
    write(tmp_path / "rel.sp", "R1 a 0 1\n")
    out = tmp_path / "deep" / "er" / "deck.cir"
    Writer(Tree(tb), str(out), "x").write()
    text = out.read_text()
    assert f".include {lib}\n" in text and ".include ../../rel.sp\n" in text


def test_writer_appends_when_there_is_no_end(tmp_path):
    tb = write(tmp_path / "tb.cir", "* t\nR1 a 0 1\n")
    out = tmp_path / "o.cir"
    Writer(Tree(tb), str(out), "x", inject=[".print tran I(R1)"]).write()
    assert out.read_text().endswith(".print tran I(R1)\n")


# ------------------------------------------------------------ names


def test_simulator_names():
    assert split_path("XI0.") == ["XI0"] == split_path("XI0:")
    assert split_path("XTOP.XI0.") == ["XTOP", "XI0"] and split_path("") == []
    assert xyce_name(["XI0"], "RM1") == "XI0:RM1" and xyce_name([], "RM1") == "RM1"
    assert ngspice_name([], "Rm1") == "rm1"
    assert ngspice_name(["XTOP", "XI0"], "Rm1") == "r.xtop.xi0.rm1"
    assert probe_keys(["XI0"], "VEMIR0") == ["I(XI0:VEMIR0)", "I(V.XI0.VEMIR0)",
                                             "V.XI0.VEMIR0#BRANCH", "I(XI0.VEMIR0)"]
    assert lead("M", "s") == ("S", 1) and lead("M", "SOURCE") == ("S", 1)
    assert lead("M", "3") == ("S", 1) and lead("Q", "c") == ("C", 1)
    assert lead("R", "2") == ("", -1) and lead("D", "anode") == ("", 1)
    assert lead("X", "d") is None and lead("M", "x") is None


def test_rcc_save_set_in_both_syntaxes():
    d = parse(DSPF)
    x = save_set(d, ["VDD"], "rcc", "xyce", ["XI0"])
    assert x.signals[0] == "I(XI0:Rm1)" and len(x.signals) == 16
    assert x.names["I(XI0:RM1)"] == ["Rm1:1", 1]
    n = save_set(d, ["VDD"], "rcc", "ngspice", ["XI0"])
    assert n.signals[0] == "@r.xi0.rm1[i]" and n.names["I(@R.XI0.RM1[I])"] == ["Rm1:1", 1]
    lines = x.lines("r.raw")
    assert lines[1].startswith(".print tran format=raw file=r.raw I(XI0:Rm1)")
    assert all(ln.startswith("+ ") for ln in lines[2:])
    nl = n.lines("r.raw")
    assert ".options filetype=ascii" in nl and nl[3].startswith(".save @r.xi0.rm1[i]")
    assert "write r.raw" in nl[1]


def test_cc_save_set_direct_leads_probes_and_unprintable():
    d = parse(DSPF)                                   # MP0..MP3: M devices, pin s
    x = save_set(d, ["VDD"], "cc", "xyce", ["XI0"])
    assert x.signals == [f"IS(XI0:MP{k})" for k in range(4)] and x.direct == 4
    assert x.names["IS(XI0:MP0)"] == ["MP0:s", 1]
    # ngspice: @mp0[is] carries the PMOS's polarity (the opposite sign of the
    # current into the pin, measured on ngspice 45): a probe reads them
    n = save_set(d, ["VDD"], "cc", "ngspice")
    assert not n.signals and n.unprintable == n.polarity == [f"MP{k}:s" for k in range(4)]
    # the netlist says they are subcircuit instances: not printable ...
    xk = save_set(d, ["VDD"], "cc", "xyce", inst_kinds={f"MP{k}": "X" for k in range(4)})
    assert xk.unprintable == [f"MP{k}:s" for k in range(4)] and not xk.signals
    # ... unless probed
    pr = {"MP0:S": {"source": "VEMIR0", "path": ["XI0"], "sign": 1, "pin": "MP0:s"}}
    xp = save_set(d, ["VDD"], "cc", "ngspice", inst_kinds={f"MP{k}": "X" for k in range(4)},
                  probes=pr)
    assert xp.signals == ["i(v.xi0.vemir0)"] and xp.names["I(V.XI0.VEMIR0)"] == ["MP0:s", 1]
    assert xp.probed == 1 and len(xp.unprintable) == 3


def test_both_pins_of_a_two_terminal_device_need_a_probe(tmp_path):
    """One current, I(RD0), for two DSPF pins: a sidecar maps one name to one
    pin, so the second pin is left for a probe instead of being misread."""
    text = (open(DSPF).read().replace(".ENDS", "")
            + "*|NET OUT 0\n*|I (RD0:2 RD0 2 I 0 70.0 0.0)\n"
              "Ro1 OUT OUT:1 1.0 $metal1 $L=5 $W=0.75\nRo2 OUT:1 RD0:2 1.0 $metal1 $L=5 $W=0.75\n"
              ".ENDS\n").replace("*|I (MP3:s MP3 s I 0 60.0000 0.0000)",
                                  "*|I (MP3:s MP3 s I 0 60.0000 0.0000)\n"
                                  "*|I (RD0:1 RD0 1 I 0 62.0000 0.0000)")
    d = parse(write(tmp_path / "r.dspf", text))
    ss = save_set(d, ["OUT", "VDD"], "cc", "xyce")
    assert ss.names["I(RD0)"] == ["RD0:2", -1] and ss.unprintable == ["RD0:1"]


def test_infer_names_finds_the_lines_a_result_was_made_with():
    d = parse(DSPF)
    ss = save_set(d, ["VDD"], "rcc", "xyce", ["XTOP", "XI0"])
    w = Waves([0, 1], {k: [0.0, 1.0] for k in ss.names})
    assert infer_names(d, ["VDD"], w) == ("xyce", "rcc", "XTOP.XI0.", 16, 16)
    assert infer_names(d, ["VDD"], Waves([0, 1], {"I(VX)": [0, 1]})) is None


# ------------------------------------------------------------ emir save-lines --sim


def test_save_lines_xyce_writes_the_sidecar(tmp_path):
    side = tmp_path / "r.raw.names"
    p = emir("save-lines", DSPF, "--net", "VDD", "--sim", "xyce", "--prefix", "XI0.",
             "--names-out", side, "--mode", "rcc")
    assert p.returncode == 0, p.stderr
    # text mode: stdout holds the lines only (it is redirected into a deck)
    out = p.stdout.splitlines()
    assert out[0].startswith("* emir: 16 currents for rcc")
    assert out[1].startswith(".print tran format=raw file=") and str(tmp_path / "r.raw") in out[1]
    assert "[emir]" not in p.stdout and "names sidecar" in p.stderr
    sc = json.loads(side.read_text())
    jsonschema.Draft202012Validator(schemas.get("names")).validate(sc)
    assert sc["map"]["I(XI0:RM1)"] == ["Rm1:1", 1]
    d = jdoc(emir("save-lines", DSPF, "--sim", "ngspice", "--mode", "rcc", "--format",
                  "json"))
    assert d["simulator"] == "ngspice" and d["count"] == 16 and d["sidecars"] == []
    assert d["names"]["I(@RM1[I])"] == ["Rm1:1", 1]
    assert d["lines"][2] == ".options filetype=ascii"
    # the sample's MOS pins: ngspice's @m[is] carries the model's polarity, so
    # save-lines refuses them and says to probe them
    d = jdoc(emir("save-lines", DSPF, "--sim", "ngspice", "--mode", "cc-grid", "--format",
                  "json"), rc=2)
    assert d["code"] == "EMIR-E460" and "polarity" in d["message"]
    assert d["details"]["pins"] == [f"MP{k}:s" for k in range(4)]


def test_save_lines_without_names_out_warns_and_spectre_refuses_the_options(tmp_path):
    p = emir("save-lines", DSPF, "--sim", "xyce", "--mode", "rcc")
    assert p.returncode == 0 and "EMIR-W460" in p.stderr
    p = emir("save-lines", DSPF, "--sim", "xyce", "--raw", tmp_path / "r.raw", "--names-out",
             tmp_path / "x.names", "--mode", "rcc")
    assert "EMIR-W462" in p.stderr                    # not <raw>.names: emir would not see it
    p = emir("save-lines", DSPF, "--sim", "xyce", "--names-out", tmp_path / "x.json",
             "--mode", "rcc")
    assert "EMIR-W462" in p.stderr and "file=emir.raw" in p.stdout
    d = jdoc(emir("save-lines", DSPF, "--names-out", tmp_path / "s.names", "--mode", "rcc",
                  "--format", "json"), rc=2)
    assert d["code"] == "EMIR-E513"
    # Spectre output is what it was
    p = emir("save-lines", DSPF, "--mode", "cc-grid")
    assert p.stdout.strip() == "save MP0:s MP1:s MP2:s MP3:s"


def test_save_lines_refuses_subcircuit_pins(ex):
    d = jdoc(emir("save-lines", ex / "loadrail.dspf", "--sim", "xyce", "--mode", "cc-grid",
                  "--format", "json"), rc=2)
    assert d["code"] == "EMIR-E460" and d["details"]["pins"][0] == "XMN0:d"
    assert d["next_steps"][0]["command"] == "emir probe --help --format json"


# ------------------------------------------------------------ emir probe


def test_probe_a_testbench(ex, tmp_path):
    out = tmp_path / "o" / "tb.probed.cir"
    side = tmp_path / "o" / "probes.names"
    d = jdoc(emir("probe", ex / "tb_cc.cir", "-o", out, "--dspf", ex / "loadrail.dspf",
                  "--net", "VDD", "--names-out", side, "--format", "json", env=NO_SIM))
    assert d["count"] == 4 and d["scope"] == "loadrail" and d["path"] == ["XI0"]
    assert [p["pin"] for p in d["probes"]] == [f"XMN{k}:d" for k in range(4)]
    assert all(p["matched"] for p in d["probes"]) and not d["uncovered"]
    assert d["next_steps"] == []                      # no simulator here to suggest
    probed = (tmp_path / "o" / "cc.probed.sp").read_text()
    assert "VEMIR0 VDD VDD__emir0 0\nXMN0 VDD__emir0 G0 VSS VSS nsink W=80u L=1u" in probed
    top = out.read_text()
    assert ".include cc.probed.sp" in top and ".include ../models.sp" in top
    sc = json.loads(side.read_text())
    jsonschema.Draft202012Validator(schemas.get("names")).validate(sc)
    assert sc["map"]["I(XI0:VEMIR0)"] == ["XMN0:d", 1] == sc["map"]["I(V.XI0.VEMIR0)"]
    assert load_probes(str(side))["XMN0:D"]["source"] == "VEMIR0"
    # the original is untouched
    assert "VEMIR" not in (ex / "cc.sp").read_text()


def test_probe_reports_pins_it_cannot_match(ex, tmp_path):
    cc = (ex / "cc.sp").read_text().replace("XMN3 VDD G3", "XMN3 VSS G3")  # off the rail
    cc = cc.replace("XMN2 VDD", "XOTHER VDD")                               # not in the DSPF
    write(ex / "cc.sp", cc)
    p = emir("probe", ex / "tb_cc.cir", "-o", tmp_path / "p.cir", "--dspf",
             ex / "loadrail.dspf", "--net", "VDD")
    assert p.returncode == 0, p.stderr
    assert "EMIR-W260" in p.stderr and "XMN2:d, XMN3:d" in p.stderr       # no probe
    assert "EMIR-W261" in p.stderr and "XOTHER:" in p.stderr               # not in the DSPF


def test_probe_names_unknown_subcircuit_pins_by_position(tmp_path):
    tb = write(tmp_path / "tb.cir", "* t\nV1 vdd 0 1\nX1 vdd g 0 0 pdkfet\n.tran 1n 2n\n.end\n")
    p = emir("probe", tb, "-o", tmp_path / "o.cir", "--net", "vdd", "--format", "json")
    d = jdoc(p)
    assert d["probes"][0]["pin"] == "X1:1" and d["guessed"] == ["pdkfet"]
    assert "EMIR-W262" in p.stderr
    d = jdoc(emir("probe", tb, "-o", tmp_path / "o.cir", "--net", "vdd", "--pins",
                  "pdkfet=d,g,s,b", "--format", "json"))
    assert d["probes"][0]["pin"] == "X1:d" and d["guessed"] == []


def test_probe_refusals(ex, tmp_path):
    d = jdoc(emir("probe", ex / "tb_cc.cir", "-o", tmp_path / "o.cir", "--net", "NOPE",
                  "--format", "json"), rc=2)
    assert d["code"] == "EMIR-E262"
    d = jdoc(emir("probe", ex / "tb_cc.cir", "-o", ex / "tb_cc.cir", "--net", "VDD",
                  "--format", "json"), rc=2)
    assert d["code"] == "EMIR-E513"
    tb = (ex / "tb_cc.cir").read_text().replace("XI0 vdd", "XI1 vdd 0 g0 g1 g2 g3 loadrail\n"
                                                           "XI0 vdd")
    write(ex / "tb2.cir", tb)
    d = jdoc(emir("probe", ex / "tb2.cir", "-o", tmp_path / "o.cir", "--dspf",
                  ex / "loadrail.dspf", "--format", "json"), rc=2)
    assert d["code"] == "EMIR-E261" and "--prefix" in d["message"]
    assert d["next_steps"][0]["command"].startswith("emir probe --help")
    d = jdoc(emir("probe", ex / "tb2.cir", "-o", tmp_path / "o.cir", "--dspf",
                  ex / "loadrail.dspf", "--prefix", "XI1.", "--format", "json"))
    assert d["path"] == ["XI1"]
    d = jdoc(emir("probe", ex / "tb_cc.cir", "-o", tmp_path / "o.cir", "--format", "json"),
             rc=2)
    assert d["code"] == "EMIR-E507"
    d = jdoc(emir("probe", ex / "tb_cc.cir", "-o", tmp_path / "o.cir", "--net", "VDD",
                  "--types", "M1", "--format", "json"), rc=2)
    assert d["code"] == "EMIR-E513"


@pytest.mark.parametrize("text, code", [
    ("{not json", "EMIR-E424"),
    ('{"map": {"I(V1)": "MP0:s"}}', "EMIR-E424"),
    ('{"map": {"I(V1)": ["MP0:s", 1]}}', "EMIR-E461"),
    ('{"map": {"I(V1)": ["MP0:s", 1]}, "probes": [{"source": 1}]}', "EMIR-E424"),
    ('{"map": {"I(V1)": ["MP0:s", 1]}, "probes": [{"source": "V1", "pin": "MP0:s", '
     '"path": [], "sign": "x"}]}', "EMIR-E424"),
])
def test_a_bad_probes_file_is_coded(tmp_path, text, code):
    f = write(tmp_path / "p.names", text)
    d = jdoc(emir("save-lines", DSPF, "--sim", "xyce", "--mode", "cc-grid", "--probes", f,
                  "--format", "json"), rc=2)
    assert d["code"] == code and d["message"].startswith(str(f))


# ------------------------------------------------------------ emir simulate: the checks


def test_a_spiceinit_beside_the_testbench_goes_beside_the_deck(tmp_path):
    from emir.opensim import spiceinit
    tb = write(tmp_path / "tb.cir", "* t\n.end\n")
    deck = tmp_path / "out" / "r.ngspice.cir"
    deck.parent.mkdir()
    assert spiceinit(tb, str(deck)) is None                       # none to copy
    write(tmp_path / ".spiceinit", "set ngbehavior=hsa\n")
    assert "copied beside the deck" in spiceinit(tb, str(deck))
    assert (deck.parent / ".spiceinit").read_text() == "set ngbehavior=hsa\n"
    assert spiceinit(tb, str(tmp_path / "deck.cir")) is None      # the same directory


def test_simulate_refuses_bad_inputs_before_running(ex, tmp_path):
    base = ["simulate", "--dspf", ex / "loadrail.dspf", "--sim", "xyce", "--format", "json"]
    d = jdoc(emir(*base, ex / "tb_cc.cir", "--out", tmp_path / "c.raw", env=NO_SIM), rc=2)
    assert d["code"] == "EMIR-E520"                   # the mode is the person's choice
    base += ["--mode", "cc-grid"]
    d = jdoc(emir(*base, ex / "tb_cc.cir", "--out", tmp_path / "c.raw", env=NO_SIM), rc=2)
    assert d["code"] == "EMIR-E460"
    again = d["next_steps"][0]["command"]
    assert again.endswith("--probe") and "--format json" in again
    d = jdoc(emir(*base, ex / "tb_cc.cir", "--mode", "rcc", "--out", tmp_path / "c.raw",
                  env=NO_SIM), rc=2)
    assert d["code"] == "EMIR-E263" and "Rm1" in d["message"]
    write(ex / "other.cir", "* t\n.include models.sp\nX9 vdd g 0 0 nsink\n.tran 1n 2n\n.end\n")
    d = jdoc(emir(*base, ex / "other.cir", "--mode", "cc-grid", "--probe", "--out",
                  tmp_path / "c.raw", env=NO_SIM), rc=2)
    assert d["code"] == "EMIR-E263" and "XMN0, XMN1, XMN2, XMN3" in d["message"]
    write(ex / "notran.cir", (ex / "tb_rcc.cir").read_text().replace(".tran", "*.tran"))
    d = jdoc(emir(*base, ex / "notran.cir", "--mode", "rcc", "--out", tmp_path / "c.raw",
                  env=NO_SIM), rc=2)
    assert d["code"] == "EMIR-E462"
    d = jdoc(emir(*base, ex / "tb_rcc.cir", "--mode", "rcc", "--out", tmp_path / "a b.raw",
                  env=NO_SIM), rc=2)
    assert d["code"] == "EMIR-E513"
    d = jdoc(emir(*base, ex / "tb_rcc.cir", "--net", "VSS", env=NO_SIM), rc=2)
    assert d["code"] == "EMIR-E204"
    # everything is in order but the simulator: exit 3
    d = jdoc(emir(*base, ex / "tb_rcc.cir", "--mode", "rcc", "--out", tmp_path / "r.raw",
                  env=NO_SIM), rc=3)
    assert d["code"] == "EMIR-E660"
    assert d["next_steps"][0]["command"] == "emir info --format json"
    assert not (tmp_path / "r.raw").exists()


def test_a_probe_that_leaves_pins_out_says_which_types_to_add(tmp_path):
    """--probe probes devices (MXQJDN); an inductor's pin is printable by no
    simulator and not probed: the refusal names the --types that would."""
    text = open(DSPF).read().replace("*|I (MP3:s MP3 s I 0 60.0000 0.0000)",
                                     "*|I (MP3:s MP3 s I 0 60.0000 0.0000)\n"
                                     "*|I (LX0:1 LX0 1 I 0 61.0000 0.0000)")
    dspf = write(tmp_path / "l.dspf", text)
    tb = write(tmp_path / "tb.cir", "* t\n.model p PMOS level=1\n"
               + "".join(f"MP{k} o g VDD VDD p\n" for k in range(4))
               + "LX0 VDD o 1n\nV1 VDD 0 1\n.tran 1n 2n\n.end\n")
    d = jdoc(emir("simulate", tb, "--sim", "ngspice", "--dspf", dspf, "--mode", "cc-grid",
                  "--probe", "--out", tmp_path / "r.raw", "--format", "json", env=NO_SIM),
             rc=2)
    assert d["code"] == "EMIR-E460" and d["details"]["pins"] == ["LX0:1"]
    assert "--types MXQJDNL" in d["next_steps"][0]["command"]
    assert "--probe" in d["next_steps"][0]["command"]


# ------------------------------------------------------------ emir next


@pytest.fixture
def tools(monkeypatch):
    from emir import site
    found = {}

    def fake(name, explicit=None):
        return (f"/opt/fake/{name}" if found.get(name) else None), "$PATH"
    monkeypatch.setattr(site, "tool", fake)

    def set_(**kw):
        found.clear()
        found.update(kw)
    return set_


def plan(**kw):
    f = P.Facts(**kw)
    return P.build(f)


def ids(pl):
    return [s["id"] for s in pl.steps]


def test_next_without_a_testbench_waits_for_one(tools, ex):
    tools(xyce=True)
    pl = plan(dspf=str(ex / "loadrail.dspf"), process=PROC, mode="rcc")
    sim = next(s for s in pl.steps if s["id"] == "simulate")
    assert sim["status"] == "waiting" and sim["placeholders"] == ["<testbench.cir>"]
    assert "--sim xyce" in sim["command"] and "--mode rcc" in sim["command"]
    assert any(m["what"] == "a xyce testbench of the block" for m in pl.missing)
    check = next(s for s in pl.steps if s["id"] == "check")
    assert "--cc-sim emir_out/sim/result.raw" in check["command"]
    assert check["status"] == "todo" and check["after"] == ["simulate"]
    assert "replace <result.raw>" not in check["why"]


def test_next_with_an_rc_testbench_plans_rcc_that_runs_as_printed(tools, ex):
    tools(ngspice=True)
    pl = plan(dspf=str(ex / "loadrail.dspf"), process=PROC, netlist=str(ex / "tb_rcc.cir"),
              mode="rcc")
    assert pl.mode["chosen"] == "rcc" and not pl.missing and not pl.blocked
    # the route is verified on this machine first (no record in the test's
    # state directory), then simulated
    assert ids(pl) == ["selftest", "nets", "verify-sim", "simulate", "check", "run", "report"]
    assert pl.steps[2]["command"] == "emir verify-sim --sim ngspice"
    assert not pl.steps[2]["needs_human"]             # an open simulator
    assert all(s["status"] == "todo" for s in pl.steps)
    sim = pl.steps[3]
    assert "--sim ngspice" in sim["command"] and "--probe" not in sim["command"]
    assert not sim["needs_human"]                     # a small net: not a long run


def test_next_with_a_cc_testbench_plans_probes(tools, ex):
    tools(xyce=True, ngspice=True)
    f = P.Facts(dspf=str(ex / "loadrail.dspf"), process=PROC, netlist=str(ex / "tb_cc.cir"))
    pl = P.build(f)
    assert f.inputs["netlist"] == str(ex / "tb_cc.cir")     # set aside, then put back
    # no mode: the person's choice; a CC testbench cannot give rcc's currents
    c = {x["route"]: x for x in pl.steps[0]["choices"]}
    assert not c["rcc"]["possible"] and "does not hold every resistor" in c["rcc"]["why_not"]
    f = P.Facts(dspf=str(ex / "loadrail.dspf"), process=PROC, netlist=str(ex / "tb_cc.cir"),
                mode="cc-grid")
    pl = P.build(f)
    assert pl.mode["chosen"] == "cc-grid"
    sim = next(s for s in pl.steps if s["id"] == "simulate")
    assert "--sim xyce" in sim["command"] and "--mode cc-grid --probe" in sim["command"]


def test_next_blocks_a_testbench_it_cannot_use(tools, ex):
    tools(xyce=True)
    write(ex / "notran.cir", (ex / "tb_rcc.cir").read_text().replace(".tran", "*.tran"))
    pl = plan(dspf=str(ex / "loadrail.dspf"), process=PROC, netlist=str(ex / "notran.cir"))
    assert pl.blocked and "simulate" not in ids(pl) and "run" not in ids(pl)
    assert any("transient" in m["what"] for m in pl.missing)
    write(ex / "noinc.cir", "* t\n.include nope.sp\n.tran 1n 2n\n.end\n")
    pl = plan(dspf=str(ex / "loadrail.dspf"), process=PROC, netlist=str(ex / "noinc.cir"))
    assert pl.blocked and any(m["what"] == "a readable testbench" for m in pl.missing)
    pl = plan(dspf=str(ex / "loadrail.dspf"), process=PROC, netlist=str(ex / "tb_cc.cir"),
              mode="rcc")
    assert pl.blocked and any("RC netlist" in m["what"] for m in pl.missing)


def test_next_keeps_the_spectre_route_at_a_cadence_site(tools, ex):
    tools(spectre=True, xyce=True)
    pl = plan(dspf=str(ex / "loadrail.dspf"), process=PROC, mode="rcc")
    sim = next(s for s in pl.steps if s["id"] == "simulate")
    assert sim["command"].startswith("spectre ") and sim["licensed_tool"] == "spectre"


def _nutascii(path, names, n=11):
    lines = ["Title: t", "Plotname: Transient Analysis", "Flags: real",
             f"No. Variables: {len(names) + 1}", f"No. Points: {n}", "Variables:",
             "\t0\tTIME\ttime"] + [f"\t{k}\t{x}\tcurrent" for k, x in enumerate(names, 1)]
    lines.append("Values:")
    for i in range(n):
        lines.append(f"{i}\t{i * 1e-10:.6e}")
        lines += [f"\t{1e-3:.6e}" for _ in names]
    write(path, "\n".join(lines) + "\n")
    return str(path)


def test_next_maps_a_result_made_with_emirs_lines(tools, ex, tmp_path):
    tools(xyce=True)
    d = parse(str(ex / "loadrail.dspf"))
    ss = save_set(d, ["VDD"], "rcc", "xyce", ["XI0"])
    raw = _nutascii(tmp_path / "r.raw", ss.signals)
    pl = plan(dspf=str(ex / "loadrail.dspf"), process=PROC, cc_sim=raw)
    step = next(s for s in pl.steps if s["id"] == "names-sidecar")
    assert step["command"] == (f"emir save-lines {ex / 'loadrail.dspf'} --mode rcc --sim xyce "
                               f"--prefix XI0. --names-out {raw}.names")
    assert ids(pl)[-1] == "next" and pl.steps[-1]["after"] == ["names-sidecar"]
    # the step does what it says: the plan then sees every branch current
    p = emir(*step["command"].split()[1:])
    assert p.returncode == 0, p.stderr
    pl = plan(dspf=str(ex / "loadrail.dspf"), process=PROC, cc_sim=raw, mode="rcc")
    assert pl.mode["chosen"] == "rcc" and "run" in ids(pl)


def test_next_points_probe_results_at_the_probe_sidecar(tools, ex, tmp_path):
    tools(ngspice=True)
    raw = _nutascii(tmp_path / "c.raw", ["i(v.xi0.vemir0)"])
    pl = plan(dspf=str(ex / "loadrail.dspf"), process=PROC, cc_sim=raw)
    assert any("emir probe" in a and "c.raw.names" in a for a in pl.advice)


def test_the_opensim_rules_are_registered():
    with collecting():
        P.load_extensions()
    names = {r.name for r in P.RULES}
    assert {"opensim-testbench", "opensim", "opensim-result"} <= names


def test_a_stopped_simulation_leaves_no_result(tmp_path, monkeypatch):
    """A simulator stopped by --timeout may already have opened and partly
    written its result; run_simulator removes it, so a stopped run never leaves
    a file that could pass for its result (seen with ngspice under load)."""
    import subprocess

    from emir import opensim
    raw = tmp_path / "r.raw"

    def slow(cmd, **kw):
        raw.write_text("Title: partial\n")              # what the simulator got to write
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout"), output="partial log")
    monkeypatch.setattr(opensim.subproc, "run", slow)
    with pytest.raises(EmirError) as e:
        opensim.run_simulator("ngspice", "ngspice", str(tmp_path / "tb.cir"), str(raw), 0.01,
                              log=lambda *a: None)
    assert e.value.code == "EMIR-E605"
    assert not raw.exists()
    assert (tmp_path / "r.raw.log").read_text() == "partial log"
