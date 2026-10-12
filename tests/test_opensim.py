"""The open-simulator path without a simulator: netlist trees and their
rewriting (`emir/opensim_netlist.py`), save sets and names sidecars, `emir
save-lines --sim`, the input checks of `emir simulate`, and the `emir next`
rules (`emir/plan_opensim.py`). Runs with real Xyce / ngspice are
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
from emir.opensim import infer_names, ngspice_name, save_set, split_path, xyce_name
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
    """A copy of the loadrail example's inputs, and a testbench of the
    schematic (the four devices on ideal nets: no resistor of the DSPF)."""
    for f in ("loadrail.dspf", "models.sp", "tb_rcc.cir"):
        shutil.copy(os.path.join(EX, f), tmp_path / f)
    write(tmp_path / "sch.sp", ".SUBCKT loadrail VDD VSS G0 G1 G2 G3\n"
          + "".join(f"XMN{k} VDD G{k} VSS VSS nsink W=80u L=1u\n" for k in range(4))
          + ".ENDS loadrail\n")
    write(tmp_path / "tb_sch.cir", (tmp_path / "tb_rcc.cir").read_text()
          .replace(".include loadrail.dspf", ".include sch.sp"))
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


def test_rcc_save_set_in_both_syntaxes():
    d = parse(DSPF)
    x = save_set(d, ["VDD"], "xyce", ["XI0"])
    assert x.signals[0] == "I(XI0:Rm1)" and len(x.signals) == 16
    assert x.names["I(XI0:RM1)"] == ["Rm1:1", 1]
    n = save_set(d, ["VDD"], "ngspice", ["XI0"])
    assert n.signals[0] == "@r.xi0.rm1[i]" and n.names["I(@R.XI0.RM1[I])"] == ["Rm1:1", 1]
    lines = x.lines("r.raw")
    assert lines[1].startswith(".print tran format=raw file=r.raw I(XI0:Rm1)")
    assert all(ln.startswith("+ ") for ln in lines[2:])
    nl = n.lines("r.raw")
    assert ".options filetype=ascii" in nl and nl[3].startswith(".save @r.xi0.rm1[i]")
    assert "write r.raw" in nl[1]


def test_infer_names_finds_the_lines_a_result_was_made_with():
    d = parse(DSPF)
    ss = save_set(d, ["VDD"], "xyce", ["XTOP", "XI0"])
    w = Waves([0, 1], {k: [0.0, 1.0] for k in ss.names})
    assert infer_names(d, ["VDD"], w) == ("xyce", "XTOP.XI0.", 16, 16)
    assert infer_names(d, ["VDD"], Waves([0, 1], {"I(VX)": [0, 1]})) is None


# ------------------------------------------------------------ emir save-lines --sim


def test_save_lines_xyce_writes_the_sidecar(tmp_path):
    side = tmp_path / "r.raw.names"
    p = emir("save-lines", DSPF, "--net", "VDD", "--sim", "xyce", "--prefix", "XI0.",
             "--names-out", side)
    assert p.returncode == 0, p.stderr
    # text mode: stdout holds the lines only (it is redirected into a deck)
    out = p.stdout.splitlines()
    assert out[0].startswith("* emir: 16 branch currents for rcc")
    assert out[1].startswith(".print tran format=raw file=") and str(tmp_path / "r.raw") in out[1]
    assert "[emir]" not in p.stdout and "names sidecar" in p.stderr
    sc = json.loads(side.read_text())
    jsonschema.Draft202012Validator(schemas.get("names")).validate(sc)
    assert sc["map"]["I(XI0:RM1)"] == ["Rm1:1", 1]
    d = jdoc(emir("save-lines", DSPF, "--sim", "ngspice", "--format", "json"))
    assert d["simulator"] == "ngspice" and d["count"] == 16 and d["sidecars"] == []
    assert d["names"]["I(@RM1[I])"] == ["Rm1:1", 1] and d["mode"] == "rcc"
    assert d["lines"][2] == ".options filetype=ascii"


def test_save_lines_without_names_out_warns_and_spectre_refuses_the_options(tmp_path):
    p = emir("save-lines", DSPF, "--sim", "xyce")
    assert p.returncode == 0 and "EMIR-W460" in p.stderr
    p = emir("save-lines", DSPF, "--sim", "xyce", "--raw", tmp_path / "r.raw", "--names-out",
             tmp_path / "x.names")
    assert "EMIR-W462" in p.stderr                    # not <raw>.names: emir would not see it
    p = emir("save-lines", DSPF, "--sim", "xyce", "--names-out", tmp_path / "x.json")
    assert "EMIR-W462" in p.stderr and "file=emir.raw" in p.stdout
    d = jdoc(emir("save-lines", DSPF, "--names-out", tmp_path / "s.names", "--format",
                  "json"), rc=2)
    assert d["code"] == "EMIR-E513"
    # Spectre output: every branch current, in Spectre's syntax
    p = emir("save-lines", DSPF)
    assert p.stdout.startswith("save Rm1:1 Rm2:1")


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
    # the schematic's testbench has none of the RC netlist's resistors
    d = jdoc(emir(*base, ex / "tb_sch.cir", "--out", tmp_path / "c.raw", env=NO_SIM), rc=2)
    assert d["code"] == "EMIR-E263" and "Rm1" in d["message"]
    write(ex / "notran.cir", (ex / "tb_rcc.cir").read_text().replace(".tran", "*.tran"))
    d = jdoc(emir(*base, ex / "notran.cir", "--out", tmp_path / "c.raw", env=NO_SIM), rc=2)
    assert d["code"] == "EMIR-E462"
    d = jdoc(emir(*base, ex / "tb_rcc.cir", "--out", tmp_path / "a b.raw", env=NO_SIM), rc=2)
    assert d["code"] == "EMIR-E513"
    d = jdoc(emir(*base, ex / "tb_rcc.cir", "--net", "VSS", env=NO_SIM), rc=2)
    assert d["code"] == "EMIR-E204"
    # everything is in order but the simulator (--mode rcc may be given): exit 3
    d = jdoc(emir(*base, ex / "tb_rcc.cir", "--mode", "rcc", "--out", tmp_path / "r.raw",
                  env=NO_SIM), rc=3)
    assert d["code"] == "EMIR-E660"
    assert d["next_steps"][0]["command"] == "emir info --format json"
    assert not (tmp_path / "r.raw").exists()


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
    pl = plan(dspf=str(ex / "loadrail.dspf"), process=PROC, period=1e-8)
    sim = next(s for s in pl.steps if s["id"] == "simulate")
    assert sim["status"] == "waiting" and sim["placeholders"] == ["<testbench.cir>"]
    assert "--sim xyce" in sim["command"] and "--mode" not in sim["command"]
    assert any(m["what"] == "a xyce testbench of the block" for m in pl.missing)
    check = next(s for s in pl.steps if s["id"] == "check")
    assert "--cc-sim emir_out/sim/result.raw" in check["command"]
    assert check["status"] == "todo" and check["after"] == ["simulate"]
    assert "replace <result.raw>" not in check["why"]


def test_next_with_an_rc_testbench_plans_rcc_that_runs_as_printed(tools, ex):
    tools(ngspice=True)
    pl = plan(dspf=str(ex / "loadrail.dspf"), process=PROC, netlist=str(ex / "tb_rcc.cir"),
              period=1e-8)
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


def test_next_blocks_a_testbench_it_cannot_use(tools, ex):
    tools(xyce=True)
    write(ex / "notran.cir", (ex / "tb_rcc.cir").read_text().replace(".tran", "*.tran"))
    pl = plan(dspf=str(ex / "loadrail.dspf"), process=PROC, netlist=str(ex / "notran.cir"))
    assert pl.blocked and "simulate" not in ids(pl) and "run" not in ids(pl)
    assert any("transient" in m["what"] for m in pl.missing)
    write(ex / "noinc.cir", "* t\n.include nope.sp\n.tran 1n 2n\n.end\n")
    pl = plan(dspf=str(ex / "loadrail.dspf"), process=PROC, netlist=str(ex / "noinc.cir"))
    assert pl.blocked and any(m["what"] == "a readable testbench" for m in pl.missing)
    f = P.Facts(dspf=str(ex / "loadrail.dspf"), process=PROC, netlist=str(ex / "tb_sch.cir"))
    pl = P.build(f)
    assert f.inputs["netlist"] == str(ex / "tb_sch.cir")
    assert pl.blocked and any("RC netlist" in m["what"] for m in pl.missing)


def test_next_keeps_the_spectre_route_at_a_cadence_site(tools, ex):
    tools(spectre=True, xyce=True)
    pl = plan(dspf=str(ex / "loadrail.dspf"), process=PROC)
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
    ss = save_set(d, ["VDD"], "xyce", ["XI0"])
    raw = _nutascii(tmp_path / "r.raw", ss.signals)
    pl = plan(dspf=str(ex / "loadrail.dspf"), process=PROC, cc_sim=raw)
    step = next(s for s in pl.steps if s["id"] == "names-sidecar")
    assert step["command"] == (f"emir save-lines {ex / 'loadrail.dspf'} --sim xyce "
                               f"--prefix XI0. --names-out {raw}.names")
    assert ids(pl)[-1] == "next" and pl.steps[-1]["after"] == ["names-sidecar"]
    # the step does what it says: the plan then sees every branch current
    p = emir(*step["command"].split()[1:])
    assert p.returncode == 0, p.stderr
    pl = plan(dspf=str(ex / "loadrail.dspf"), process=PROC, cc_sim=raw, period=5e-10)
    assert pl.mode["chosen"] == "rcc" and "run" in ids(pl)


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
