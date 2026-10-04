"""The magic route: `emir extract --tool magic` (emir/magic.py).

Most tests read the committed magic outputs of tests/data/magic_tiny (a
synthetic one-transistor SKY130 cell, its `vss` rail labelled at both ends),
so the DSPF and the CC netlist are written and checked without magic. The
command itself runs against a stand-in magic (a script that answers the
geometry probe and copies those outputs into place). The test marked `magic`
extracts tiny.gds with a real magic that writes geometry, when there is one.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys

import numpy as np
import pytest
import yaml

from _helpers import ROOT, TINY, emir, fake_magic, write
from _helpers import TINY_OUTPUTS as OUTPUTS

SKY = os.path.join(ROOT, "pdks", "sky130.yaml")


def sky():
    with open(SKY, encoding="utf-8") as f:
        return yaml.safe_load(f)


def converted(tmp_path, pins="tied", data=None):
    from emir import magic
    work = tmp_path / "work"
    work.mkdir(parents=True, exist_ok=True)
    for f in OUTPUTS:
        shutil.copy(os.path.join(TINY, f), work)
    res = magic.convert(str(work), "tiny", sky() if data is None else data, pins=pins,
                        log=lambda *a: None)
    return res, open(res["dspf"], encoding="utf-8").read()


def resistors(text):
    """(n1, n2, ohms) of every R line of a SPICE text."""
    out = []
    for line in text.splitlines():
        w = line.split()
        if w and w[0][:1] in "Rr" and len(w) >= 4:
            out.append((w[1], w[2], round(float(w[3]), 3)))
    return sorted(out)


# ------------------------------------------------------------------ the DSPF


def test_the_dspf_keeps_every_resistor_magic_extracted(tmp_path):
    res, text = converted(tmp_path)
    pex = open(os.path.join(TINY, "tiny_pex.spice"), encoding="utf-8").read()
    # the same resistors between the same nodes (magic names the rail's
    # second pin `vss` too; the DSPF gives it its own node, `vss.p2`); magic's
    # netlist writes each value 0.5 milliohm above its .res.geo (`5.2805`)
    got = sorted((a.replace("vss.p2", "vss"), b.replace("vss.p2", "vss"), r)
                 for a, b, r in resistors(text))
    want = resistors(pex)
    assert [r[:2] for r in got] == [r[:2] for r in want]
    assert np.allclose([r[2] for r in got], [r[2] for r in want], atol=1.5e-3, rtol=0)
    assert res["resistors"] == res["segments"] == len(resistors(pex)) == 44
    assert res["merged"] == 0 and res["devices"] == 1


def test_each_resistor_carries_its_shape(tmp_path):
    from emir.dspf import parse
    res, text = converted(tmp_path)
    d = parse(res["dspf"])
    by = {r["name"]: r for r in d.res}
    wires = [ln for ln in text.splitlines() if ln.startswith("R") and "$dir=" in ln]
    vias = [ln for ln in text.splitlines() if ln.startswith("R") and "$cuts=" in ln]
    assert wires and vias and len(wires) + len(vias) == 44
    for ln in wires:
        r = by[ln.split()[0]]
        assert r["w"] > 0 and r["l"] is not None and r["bbox"][2] >= r["bbox"][0]
        assert r["via"] is False and r["layer"]
    for ln in vias:
        r = by[ln.split()[0]]
        m = re.search(r"\$cuts=(\d+)x(\d+)", ln)
        assert r["via"] is True and r["cuts"] == int(m.group(1)) * int(m.group(2))
    # the met1 rail between the two vss labels: 3.115 um at 0.4 um, as drawn
    rail = next(r for r in d.res if r["n1"] == "vss" and r["layer"] == "metal1")
    assert rail["w"] == pytest.approx(0.4) and rail["l"] == pytest.approx(3.115)
    assert rail["bbox"] == pytest.approx((0.685, -1.4, 3.8, -1.0))


def test_the_process_file_maps_magics_layers(tmp_path):
    from emir.dspf import map_layers, parse
    from emir.engine import Process
    res, _ = converted(tmp_path)
    lmap = map_layers(parse(res["dspf"]), Process.load(SKY, log=lambda *a: None))
    assert lmap["metal1"] == ("layer", "met1") and lmap["locali"] == ("layer", "li1")
    assert lmap["viali"] == ("via", "mcon") and lmap["via1"] == ("via", "via")
    unmapped = {"ndiff", "ndiffc", "nmos", "poly", "polycont", "psubdiffcont", "pwell"}
    assert unmapped.isdisjoint(lmap)


def test_a_port_with_two_pins_is_tied(tmp_path):
    """vss is labelled at both ends: two *|P nodes, joined by a 0 V source,
    which the network reads as a short: a current at the source terminal
    leaves by both pins."""
    from emir.dspf import Network, parse
    res, text = converted(tmp_path)
    assert res["extra_pins"] == res["ties"] == 1
    assert "*|P (vss B 0 3.8 -1.2)" in text and "*|P (vss.p2 B 0 -2.8 -1.2)" in text
    assert "Vpin0 vss.p2 vss 0" in text
    d = parse(res["dspf"])
    assert d.ties == [("vss.p2", "vss")] and d.tie_alias("vss") == {"vss.p2": "vss"}
    nw = Network(d, "vss")
    assert nw.idx["vss.p2"] == nw.idx["vss"]
    _, Ib = nw.solve({"vss.t1": 1e-3, "vss": -1e-3}, "vss")
    to = {r["name"]: Ib[i] for i, r in enumerate(nw.res)}
    left = next(r["name"] for r in nw.res if "vss.p2" in (r["n1"], r["n2"]))
    right = next(r["name"] for r in nw.res if r["layer"] == "metal1" and "vss" in
                 (r["n1"], r["n2"]))
    assert abs(to[left]) > 1e-4 and abs(to[right]) > 1e-4
    assert abs(to[left]) + abs(to[right]) == pytest.approx(1e-3, rel=1e-9)


def test_separate_pins_are_left_to_the_reader(tmp_path):
    from emir.dspf import Network, parse
    res, text = converted(tmp_path, pins="separate")
    assert res["ties"] == 0 and "Vpin" not in text and "*|P (vss.p2" in text
    nw = Network(parse(res["dspf"]), "vss")
    assert nw.idx["vss.p2"] != nw.idx["vss"]


def test_capacitors_are_counted_once_and_spread(tmp_path):
    """Each net pair's capacitance in the DSPF is the lumped netlist's: spread
    over the nets' nodes, never counted twice."""
    from emir.magic import net_of, number
    res, text = converted(tmp_path)

    def pairs(lines):
        out = {}
        for ln in lines:
            w = ln.split()
            if w and w[0][:1] == "C":
                k = frozenset((net_of(w[1]), net_of(w[2])))
                out[k] = out.get(k, 0.0) + number(w[3])
        return out
    lumped = pairs(open(os.path.join(TINY, "tiny_lumped.spice")).read().splitlines())
    got = pairs(text.splitlines())
    assert set(got) == set(lumped)
    for k, v in lumped.items():
        assert got[k] == pytest.approx(v, rel=1e-4)
    assert res["capacitors"] > len(lumped)            # spread over the nodes


def test_terminals_are_named_from_the_process_file(tmp_path, capsys):
    from emir.dspf import parse
    res, _ = converted(tmp_path)
    pins = {(t["inst"], t["pin"]) for t in parse(res["dspf"]).inst_pins.values()}
    assert pins == {("X0", "d"), ("X0", "g"), ("X0", "s"), ("X0", "b")}
    assert res["guessed_terminals"] == []
    # no table: named by position, and said so (EMIR-W643)
    from emir.errors import collecting
    with collecting(propagate=False) as notes:
        res2, _ = converted(tmp_path / "x", data={"magic": {}})
    assert res2["guessed_terminals"] == ["sky130_fd_pr__nfet_01v8"]
    assert "EMIR-W643" in {n["code"] for n in notes}
    assert {t["pin"] for t in parse(res2["dspf"]).inst_pins.values()} == {"t1", "t2", "t3",
                                                                         "t4"}


def test_the_cc_netlist_has_the_dspfs_instances_on_ideal_nets(tmp_path):
    from emir.dspf import parse
    res, _ = converted(tmp_path)
    cc = open(res["cc_netlist"], encoding="utf-8").read()
    assert ".subckt tiny out vss g" in cc and "R" not in "".join(
        ln[:1] for ln in cc.splitlines())
    x = next(ln for ln in cc.splitlines() if ln.startswith("X0 "))
    assert x.split()[1:5] == ["out", "g", "vss", "vss"]
    assert {t["inst"] for t in parse(res["dspf"]).inst_pins.values()} == {"X0"}
    assert sum(ln.startswith("C") for ln in cc.splitlines()) == 3


def test_emir_reads_it_as_any_dspf(tmp_path):
    res, _ = converted(tmp_path)
    p = emir("nets", res["dspf"], "--format", "json")
    assert p.returncode == 0, p.stderr
    nets = {n["name"]: n for n in json.loads(p.stdout)["nets"]}
    assert set(nets) == {"out", "vss", "g"} and nets["vss"]["port"]
    p = emir("pins", res["dspf"], "--net", "vss", "--format", "json")
    assert {(x["inst"], x["pin"]) for x in json.loads(p.stdout)["pins"]} == {("X0", "s"),
                                                                             ("X0", "b")}
    p = emir("run", res["dspf"], "--process", SKY, "--net", "vss", "--currents",
             '{"vss.t1": 1e-3, "vss": -1e-3}', "--format", "json")
    assert p.returncode == 0, p.stdout + p.stderr
    doc = json.loads(p.stdout)
    assert doc["nets"][0]["ir_drop_mV"] > 0 and doc["nets"][0]["kcl_residual_A"] < 1e-12


def test_a_word_annotation_does_not_break_the_reader(tmp_path):
    """`$dir=ew` once read as the number `e` (EMIR-E201)."""
    from emir.dspf import parse
    p = write(tmp_path / "w.dspf", "*|DSPF 1.3\n.SUBCKT w A\n*|NET A 0\n*|P (A B 0 0 0)\n"
              "*|S (A:1 1 0)\nR1 A A:1 1.0 $layer=metal1 $dir=ew $W=0.5 $L=1 $X=0 $Y=0\n"
              ".ENDS\n")
    r = parse(p).res[0]
    assert r["w"] == 0.5 and r["layer"] == "metal1" and r["xy"] == (0.0, 0.0)


def test_two_terminals_on_one_node_are_both_kept(tmp_path):
    from emir.dspf import parse
    p = write(tmp_path / "s.dspf", "*|DSPF 1.3\n.SUBCKT s A\n*|NET A 0\n*|P (A B 0 0 0)\n"
              "*|I (A.t0 X0 s B 0 1 0)\n*|I (A.t0 X1 d B 0 1 0)\n"
              "R1 A A.t0 1.0 $layer=metal1 $W=0.5\n.ENDS\n")
    d = parse(p)
    assert sorted((t["inst"], t["pin"], t["node"]) for t in d.net_taps("A")) == [
        ("X0", "s", "A.t0"), ("X1", "d", "A.t0")]


# ------------------------------------------------------------------ the command

def rcfile(tmp_path) -> str:
    return write(tmp_path / "sky130A.magicrc", "# a stand-in startup file\n")


def test_extract_runs_magic_and_writes_the_dspf(tmp_path):
    pytest.importorskip("klayout.db")
    from emir import schemas
    jsonschema = pytest.importorskip("jsonschema")
    exe = fake_magic(tmp_path)
    out = tmp_path / "ext"
    p = emir("extract", "--tool", "magic", "--gds", os.path.join(TINY, "tiny.gds"),
             "--process", SKY, "--magic", exe, "--magicrc", rcfile(tmp_path), "--out", str(out),
             "--format", "json", cwd=str(tmp_path))
    assert p.returncode == 0, p.stdout + p.stderr
    doc = json.loads(p.stdout)
    jsonschema.validate(doc, schemas.get(schemas.key("extract")))
    assert doc["tool"] == "magic" and doc["cell"] == "tiny"     # the only top cell
    assert doc["magic"]["path"] == exe and doc["magic"]["source"] == "--magic"
    assert os.path.isfile(doc["dspf"]) and os.path.isfile(doc["cc_netlist"])
    assert doc["rc_netlist"] == doc["dspf"] and doc["resistors"] == 44 and doc["ties"] == 1
    assert "pwell" in doc["unchecked_layers"] and "metal1" not in doc["unchecked_layers"]
    # magic runs in EMIR's own directory under --out; the DSPF and the CC
    # netlist go into --out itself
    work = out / "magic"
    assert doc["work_dir"] == str(work) and doc["out_dir"] == str(out)
    assert doc["dspf"] == str(out / "tiny.dspf") and (work / ".emir-magic-work").is_file()
    assert open(work / "extract.tcl").read().count("extresist geoout on") == 1
    assert "extresist simplify off" in open(work / "extract.tcl").read()
    assert os.path.islink(work / "input.gds") or os.path.isfile(work / "input.gds")
    assert doc["floating_capacitance"] == [] and doc["diagonal_resistors"] == []
    cmds = [s["command"] for s in doc["next_steps"]]
    assert any(c.startswith("emir nets ") for c in cmds)
    assert any(c.startswith("emir next --dspf ") and "--gds" in c for c in cmds)


def test_extract_refuses_a_magic_that_writes_no_geometry(tmp_path):
    exe = fake_magic(tmp_path, patched=False)
    p = emir("extract", "--tool", "magic", "--gds", os.path.join(TINY, "tiny.gds"),
             "--cell", "tiny", "--process", SKY, "--magic", exe, "--magicrc",
             rcfile(tmp_path), "--out", str(tmp_path / "ext"), "--format", "json")
    assert p.returncode == 3, p.stdout + p.stderr
    doc = json.loads(p.stdout)
    assert doc["code"] == "EMIR-E643"
    assert "https://github.com/RTimothyEdwards/magic/pull/562" in doc["message"]
    assert "extresist-geoout" in doc["hint"]
    assert doc["details"]["tried"][0]["result"] == "no geoout (unpatched)"


def test_magic_is_looked_for_in_order(tmp_path, monkeypatch):
    from emir import magic
    monkeypatch.setenv("EMIR_MAGIC", "/no/such/magic")
    monkeypatch.delenv("MAGIC_BIN", raising=False)
    got = magic.candidates({"magic": {"binary": ["magic-x", "magic-y"]}}, explicit="m0")
    assert [v for v, _ in got[:4]] == ["m0", "/no/such/magic", "magic-x", "magic-y"]
    assert [v for v, _ in got[-2:]] == list(magic.PATH_NAMES)


def test_extract_options_belong_to_their_tool(tmp_path):
    p = emir("extract", "--tool", "magic", "--gds", "x.gds", "--process", SKY,
             "--kind", "r_only", "--format", "json")
    assert p.returncode == 2 and json.loads(p.stdout)["code"] == "EMIR-E507"
    p = emir("extract", "--gds", "x.gds", "--cell", "c", "--format", "json")
    doc = json.loads(p.stdout)
    assert doc["code"] == "EMIR-E507" and "--cdl" in doc["message"]
    p = emir("extract", "--tool", "magic", "--gds", "x.gds", "--format", "json")
    assert "--process" in json.loads(p.stdout)["message"]


def test_a_cell_name_magic_cannot_carry_is_refused():
    from emir.errors import EmirError
    from emir.magic import check_cell
    assert check_cell("sink_v2") == "sink_v2"
    for bad in ("a b", "x[exec ls]", "$c", "a;b", ""):
        with pytest.raises(EmirError) as e:
            check_cell(bad)
        assert e.value.code == "EMIR-E243"


def test_the_startup_file_comes_from_the_pdk(tmp_path, monkeypatch):
    from emir.errors import EmirError
    from emir.magic import magicrc_for
    rc = tmp_path / "sky130A" / "libs.tech" / "magic" / "sky130A.magicrc"
    rc.parent.mkdir(parents=True)
    rc.write_text("#\n")
    monkeypatch.delenv("EMIR_MAGICRC", raising=False)
    monkeypatch.setenv("PDK_ROOT", str(tmp_path))
    assert magicrc_for({"pdk": "sky130A (SkyWater SKY130)"}, None) == str(rc)
    for pdk in ("../etc", "gf180mcuD"):
        with pytest.raises(EmirError) as e:
            magicrc_for({"pdk": pdk}, None)
        assert e.value.code == "EMIR-E641"


def test_next_plans_the_magic_route_for_a_layout():
    p = emir("next", "--gds", os.path.join(TINY, "tiny.gds"), "--process", SKY, "--cell",
             "tiny", "--format", "json")
    assert p.returncode == 0, p.stderr
    steps = {s["id"]: s for s in json.loads(p.stdout)["steps"]}
    assert steps["extract-magic"]["command"].startswith("emir extract --tool magic ")
    assert "--cell tiny" in steps["extract-magic"]["command"]
    assert steps["next-dspf"]["after"] == ["extract-magic"]
    assert "emir_out/extract_tiny/tiny.dspf" in steps["next-dspf"]["command"]


@pytest.mark.parametrize("text", ["magic: [\n", "foo: [\n"])
def test_next_on_a_malformed_process_file_never_crashes(tmp_path, text):
    """A process file YAML cannot parse is bad input (exit 0 or 2), never an
    internal error (EMIR-E901, exit 70)."""
    bad = write(tmp_path / "broken.yaml", text)
    p = emir("next", "--gds", os.path.join(TINY, "tiny.gds"), "--process", bad, "--cell",
             "tiny", "--format", "json")
    assert p.returncode in (0, 2), p.stdout + p.stderr
    assert "EMIR-E901" not in p.stdout + p.stderr


def test_next_does_not_plan_an_extraction_check_refuses(tmp_path):
    """A process file whose magic: binary: is not a magic program's bare name
    (EMIR-E648 in emir check and extract) is not planned as a runnable step."""
    data = yaml.safe_load(open(SKY, encoding="utf-8"))
    data["magic"]["binary"] = "./notmagic.sh"
    proc = write(tmp_path / "proc.yaml", yaml.safe_dump(data))
    p = emir("next", "--gds", os.path.join(TINY, "tiny.gds"), "--process", proc, "--cell",
             "tiny", "--format", "json")
    assert p.returncode in (0, 2), p.stderr
    doc = json.loads(p.stdout)
    assert "extract-magic" not in {s["id"] for s in doc["steps"]}
    assert any("EMIR-E648" in json.dumps(m) for m in doc["missing"])


def test_next_puts_the_layout_under_the_charts(tmp_path):
    res, _ = converted(tmp_path)
    p = emir("next", "--dspf", res["dspf"], "--process", SKY, "--currents",
             '{"vss.t1": 1e-3, "vss": -1e-3}', "--net", "vss", "--html", "--gds",
             os.path.join(TINY, "tiny.gds"), "--cell", "tiny", "--format", "json")
    run = next(s for s in json.loads(p.stdout)["steps"] if s["id"] == "run")
    assert "--gds" in run["command"] and "--cell tiny" in run["command"]


def test_a_big_layout_needs_the_persons_go_ahead_over_mcp(tmp_path, monkeypatch):
    from emir import mcp_tools, plan
    gds = write(tmp_path / "big.gds", "x" * 2000)
    ns = type("NS", (), {"tool": "magic", "gds": gds, "run": False})()
    assert mcp_tools._gate_extract(ns) == []
    monkeypatch.setitem(plan.THRESHOLDS, "magic_gds_mb", 0.001)
    gate = mcp_tools._gate_extract(ns)
    assert gate and gate[0].long_run


def test_the_process_files_magic_section_is_checked(tmp_path):
    from emir.check import Report, check_process
    d = sky()
    d["magic"]["layer"] = {"metal1": "met1"}                 # a typo of `layers`
    rep = Report()
    check_process(write(tmp_path / "p.yaml", yaml.safe_dump(d)), rep)
    assert "EMIR-E316" in rep.codes("FAIL")
    jsonschema = pytest.importorskip("jsonschema")
    from emir import schemas
    jsonschema.validate(sky(), schemas.get(schemas.key("process")))


# ------------------------------------------------------------------ a real magic


def _geo_magic():
    from emir import magic
    for value, _ in magic.candidates(sky()):
        exe = magic._executable(value)
        if exe and magic.geoout_accepted(exe):
            return exe
    return None


@pytest.mark.magic
def test_a_real_magic_extracts_the_committed_network(tmp_path):
    """tiny.gds through a magic that writes geometry gives the committed
    network: the same resistors (magic numbers its nodes afresh each run, so
    they are compared by layer, kind, value and size), the same devices and
    capacitance."""
    pytest.importorskip("klayout.db")
    root = os.environ.get("PDK_ROOT")
    if not (root and os.path.isdir(os.path.join(root, "sky130A"))):
        pytest.skip("needs PDK_ROOT with sky130A")
    exe = _geo_magic()
    if exe is None:
        pytest.skip("no magic that writes resistor geometry")
    p = emir("extract", "--tool", "magic", "--gds", os.path.join(TINY, "tiny.gds"),
             "--cell", "tiny", "--process", SKY, "--magic", exe, "--out",
             str(tmp_path / "ext"), "--format", "json")
    assert p.returncode == 0, p.stdout + p.stderr
    got = open(json.loads(p.stdout)["dspf"], encoding="utf-8").read()
    _, want = converted(tmp_path)

    def shapes(text):
        out = []
        for ln in text.splitlines():
            if ln.startswith("R"):
                w = ln.split()
                ann = dict(re.findall(r"\$(\w+)=(\S+)", ln))
                out.append((round(float(w[3]), 3), ann.get("layer"), ann.get("dir"),
                            ann.get("W"), ann.get("L"), ann.get("cuts")))
        return sorted(out, key=str)
    assert shapes(got) == shapes(want)
    for tag in ("*|NET", "*|P", "X0 "):
        assert sorted(x.split()[0] for x in got.splitlines() if x.startswith(tag)) == sorted(
            x.split()[0] for x in want.splitlines() if x.startswith(tag))


def test_the_fixture_is_the_generators(tmp_path):
    """tiny.gds is what make_tiny.py draws (the same shapes)."""
    db = pytest.importorskip("klayout.db")
    out = tmp_path / "tiny.gds"
    subprocess.run([sys.executable, os.path.join(TINY, "make_tiny.py"), str(out)], check=True)

    def shapes(path):
        ly = db.Layout()
        ly.read(str(path))
        top = ly.top_cell()
        return sorted((ly.get_info(li).layer, ly.get_info(li).datatype, str(s))
                      for li in ly.layer_indexes() for s in top.shapes(li).each())
    assert shapes(out) == shapes(os.path.join(TINY, "tiny.gds"))
    pair = tmp_path / "pair.gds"
    subprocess.run([sys.executable, os.path.join(TINY, "make_pair.py"), str(pair)], check=True)
    assert shapes(pair) == shapes(os.path.join(TINY, "pair.gds"))


# ------------------------------------------------------------ what the route changed elsewhere


def test_the_rcc_loop_check_uses_the_simulated_resistance(tmp_path):
    """Two resistors in parallel on two layers with different TCRs: currents
    that close Kirchhoff's voltage law at the simulated resistances (the
    DSPF's) leave no loop residual. Checked at the TCR-scaled resistances, the
    loop did not close, and EMIR-W405 said the currents were not the DSPF's."""
    from emir.dspf import Network, parse, scale_of
    from emir.rcc import loop_residual, node_voltages, simulated_r, spanning_order
    p = write(tmp_path / "loop.dspf", "*|DSPF 1.3\n.SUBCKT l A\n*|NET A 0\n*|P (A B 0 0 0)\n"
              "*|S (A.n1 1 0)\n"
              "R1 A A.n1 2.0 $layer=metal1 $W=0.5 $L=1\n"
              "R2 A A.n1 6.0 $layer=locali $W=0.5 $L=1\n.ENDS\n")
    d = parse(p)
    rscale = {"metal1": 1.19, "locali": 1.03}
    nw = Network(d, "A", rscale=rscale)
    assert scale_of(rscale, "metal1") != scale_of(rscale, "locali")
    Ib = np.array([3e-3, 1e-3])                 # 2 ohm x 3 mA = 6 ohm x 1 mA
    steps, tree, _ = spanning_order(nw, "A")
    R = simulated_r(nw)
    assert loop_residual(nw, steps, Ib, tree=tree, R=R,
                         V=node_voltages(nw, steps, Ib, R=R)) == pytest.approx(0.0, abs=1e-15)
    assert loop_residual(nw, steps, Ib, tree=tree) > 1e-4      # the scaled one did not close


def test_a_process_file_needs_no_gds_numbers(tmp_path):
    from emir.engine import Process
    p = write(tmp_path / "p.yaml", "em_tref: 90\nlayers:\n  met1: {sheet_res: 0.125, "
              "jmax_avg: 2.8}\nvias:\n  via: {below: met1, above: met1, res: 4.5}\n")
    proc = Process.load(p, log=lambda *a: None)
    assert proc.layers["met1"]["gds"] is None


def test_run_draws_the_layout_under_its_chart(tmp_path):
    pytest.importorskip("matplotlib")
    pytest.importorskip("klayout.db")
    res, _ = converted(tmp_path)
    args = ["run", res["dspf"], "--process", SKY, "--net", "vss", "--currents",
            '{"vss.t1": 1e-3, "vss": -1e-3}', "--html", str(tmp_path / "html"),
            "--summary-out", str(tmp_path / "s.json"), "--format", "json"]
    p = emir(*args, "--gds", str(tmp_path / "none.gds"))
    assert p.returncode == 2 and json.loads(p.stdout)["code"] == "EMIR-E101"
    assert not (tmp_path / "s.json").exists() or json.load(open(tmp_path / "s.json"))[
        "schema"] == "emir.error/1"
    p = emir(*args, "--gds", os.path.join(TINY, "tiny.gds"), "--cell", "tiny")
    assert p.returncode == 0, p.stdout[-2000:] + p.stderr[-2000:]
    s = json.load(open(tmp_path / "s.json"))
    assert len(s["charts"]) == 2 and os.path.getsize(tmp_path / "html" / "emir_chart.png") > 20000


# ------------------------------------------------------------ the pair: a net with no label

#: pair.gds (make_pair.py) and what a magic with the geoout patch wrote for it
#: with emir's script: its middle net has no label, so magic names it after a
#: place, `a_188_0#` in the lumped netlist and `a_188_0.tK` in the split one
PAIR_OUTPUTS = ("pair.ext", "pair.res.ext", "pair.res.geo", "pair_pex.spice",
                "pair_lumped.spice")


def converted_pair(tmp_path, edit=None):
    from emir import magic
    work = tmp_path / "work"
    work.mkdir(parents=True, exist_ok=True)
    for f in PAIR_OUTPUTS:
        shutil.copy(os.path.join(TINY, f), work)
    if edit:
        edit(work)
    res = magic.convert(str(work), "pair", sky(), log=lambda *a: None)
    return res, open(res["dspf"], encoding="utf-8").read()


def test_a_net_with_no_label_keeps_its_capacitance(tmp_path):
    """The lumped netlist's `a_188_0#` is the split netlist's `a_188_0`: its
    capacitors land on that net, in the DSPF and in the CC netlist, and no
    capacitor node floats. They sat on a node of their own, `a_188_0#`, that
    no resistor or device touched: the net's charging current was lost."""
    from emir.dspf import parse
    from emir.errors import collecting
    from emir.magic import net_of, number
    with collecting(propagate=False) as notes:
        res, text = converted_pair(tmp_path)
    assert "#" not in text and res["floating_capacitance"] == []
    assert "EMIR-W646" not in {n["code"] for n in notes}
    lumped = open(os.path.join(TINY, "pair_lumped.spice"), encoding="utf-8").read()
    total = sum(number(ln.split()[3]) for ln in lumped.splitlines()
                if ln.startswith("C") and "a_188_0#" in ln.split()[1:3])
    assert total > 0.9e-15
    assert f"*|NET a_188_0 {total:.6g}" in text
    d = parse(res["dspf"])
    rnets = {net_of(n) for r in d.res for n in (r["n1"], r["n2"])}
    assert "a_188_0" in rnets
    for ln in text.splitlines():
        if ln.startswith("C"):
            assert {net_of(n) for n in ln.split()[1:3]} <= rnets | {"0"}, ln
    cc = open(res["cc_netlist"], encoding="utf-8").read()
    caps = [ln.split() for ln in cc.splitlines() if ln.startswith("C")]
    assert len(caps) == 5 and all("#" not in " ".join(c) for c in caps)
    on_mid = sum(number(c[3]) for c in caps if "a_188_0" in c[1:3])
    assert on_mid == pytest.approx(total, rel=1e-9)
    assert {n for c in caps for n in c[1:3]} <= {"g", "out", "vss", "a_188_0"}


def test_capacitance_on_no_net_is_said(tmp_path):
    """A capacitor node on no device, resistor or port (unconnected metal):
    EMIR-W646 names it, and the extract document lists it."""
    from emir.errors import collecting

    def fill(work):
        p = work / "pair_lumped.spice"
        p.write_text(p.read_text().replace(".ends", "C9 a_900_900# vss 0.01f\n.ends"))
    with collecting(propagate=False) as notes:
        res, _ = converted_pair(tmp_path, edit=fill)
    assert res["floating_capacitance"] == ["a_900_900#"]
    w = [n for n in notes if n["code"] == "EMIR-W646"]
    assert w and "a_900_900#" in w[0]["message"]


def test_the_lumped_names_follow_the_device_terminals():
    """Where dropping `#` does not match, the device terminals do: the same
    instance's node at the same position."""
    from emir.magic import lumped_names
    split = (".subckt c a b\nX0 a.t0 w_5_5.t1 b.t0 b.t1 nfet\nR0 a a.t0 1\n"
             "R1 w_5_5.t1 w_5_5.n0 1\n.ends\n")
    lumped = ".subckt c a b\nX0 a net7 b b nfet\nC0 net7 b 1f\nC1 a b 1f\n.ends\n"
    assert lumped_names(split, lumped) == {"net7": "w_5_5"}


# ------------------------------------------------------------ hierarchy


def test_magic_flattens_the_cell_with_its_own_labels_only():
    """magic extracts a flat copy of the cell under the cell's own name (every
    subcell's paint, the top cell's labels: a subcell's label names no pin):
    a hierarchical layout extracts as the flat one. The layout's cell is
    renamed before it is loaded: magic 8.3.684 does not rename the cell being
    edited, and its copy then kept the temporary name (no <cell>.res.geo)."""
    from emir.magic import SCRIPT
    text = SCRIPT.format(gds="input.gds", cell="blk", lumped="l.spice", pex="p.spice",
                         tolerance="10")
    lines = text.splitlines()
    i = lines.index("flatten -dotoplabels blk")
    assert lines[i - 3:i] == ["gds read input.gds", "cellname rename blk blk__emir_hier",
                              "load blk__emir_hier"]
    assert lines[i + 1] == "load blk" and "load blk" not in lines[:i - 1]
    assert lines.index("port makeall") > i + 1 and lines.index("extract all") > i + 1


def test_a_netlist_of_several_subcircuits_is_refused(tmp_path):
    """magic outputs of a hierarchy extracted cell by cell (another script):
    the DSPF and the CC netlist would mix the subcells' nets with the top's,
    and Xyce rejected them while extract exited 0. Refused: EMIR-E647."""
    from emir import magic
    from emir.errors import EmirError
    work = tmp_path / "work"
    work.mkdir()
    for f in OUTPUTS:
        shutil.copy(os.path.join(TINY, f), work)
    p = work / "tiny_pex.spice"
    p.write_text(".subckt core a b\nX0 a b a a sky130_fd_pr__nfet_01v8 w=1 l=1\n.ends\n"
                 + p.read_text())
    with pytest.raises(EmirError) as e:
        magic.convert(str(work), "tiny", sky(), log=lambda *a: None)
    assert e.value.code == "EMIR-E647" and "core" in e.value.message
    assert not (work / "tiny.dspf").exists()


# ------------------------------------------------------------ diagonal wires


def test_diagonal_wires_are_said_and_not_called_checked(tmp_path):
    """A resistor magic marks `diag` (split tiles): its width is the tiles'
    extent, not the width across the current. extract says so (EMIR-W647,
    `diagonal`), its box is not empty, and `emir run` lists it under
    not_checked (`diagonal_wires`) and in its report."""
    from emir.errors import collecting
    from emir.dspf import parse
    work = tmp_path / "work"
    work.mkdir()
    for f in OUTPUTS:
        shutil.copy(os.path.join(TINY, f), work)
    geo = work / "tiny.res.geo"
    rows = geo.read_text().splitlines()
    k = next(i for i, r in enumerate(rows) if " metal1 ew " in r)
    # magic places a diagonal piece's two nodes on one level: an empty box
    w = rows[k].split()
    w[5], w[12] = "diag", w[10]
    rows[k] = " ".join(w)
    geo.write_text("\n".join(rows) + "\n")
    from emir import magic
    with collecting(propagate=False) as notes:
        res = magic.convert(str(work), "tiny", sky(), log=lambda *a: None)
    assert len(res["diagonal"]) == 1 and "EMIR-W647" in {n["code"] for n in notes}
    r = next(x for x in parse(res["dspf"]).res if x["name"] == res["diagonal"][0])
    assert r["diag"] is True and r["w"] > 0
    llx, lly, urx, ury = r["bbox"]
    assert urx > llx and ury > lly
    assert sum(x["diag"] for x in parse(res["dspf"]).res) == 1
    s = tmp_path / "s.json"
    net = "vss" if r["n1"].startswith("vss") else r["n1"].split(".")[0]
    taps = {t["node"] for t in parse(res["dspf"]).net_taps(net)}
    cur = {sorted(taps)[0]: 1e-3, net: -1e-3}
    p = emir("run", res["dspf"], "--process", SKY, "--net", net, "--currents",
             json.dumps(cur), "--summary-out", str(s), "--format", "json")
    assert p.returncode == 0, p.stdout[-2000:] + p.stderr[-2000:]
    nc = json.load(open(s))["not_checked"]
    assert nc["diagonal_wires"] == {net: res["diagonal"]}
    rep = emir("report", "--from", str(s))
    assert "on diagonal shapes" in rep.stdout and "EMIR-W647" in rep.stdout


# ------------------------------------------------------------ via places


def test_each_diffusion_contact_sits_at_its_cut():
    """magic gives a contact row from the diffusion one node, the strip's: each
    ndiffc contact was placed there, three of out's four at one point. Each
    now sits at its own cut, where the li1-to-met1 contact above it is."""
    from emir.magic import read_geometry
    g = read_geometry(open(os.path.join(TINY, "tiny.res.ext")).read(),
                      open(os.path.join(TINY, "tiny.res.geo")).read(), ("out", "vss", "g"))
    for net in ("out", "vss"):
        diff = sorted(s.ends[0] for s in g.segments if s.layer == "ndiffc"
                      and s.a.startswith(net))
        li = sorted(s.ends[0] for s in g.segments if s.layer == "viali"
                    and s.a.startswith(net) and s.ends[0][1] > 0)
        assert len(set(diff)) == 4 and diff == li, (net, diff, li)


# ------------------------------------------------------------ the --out directory


def _extract(tmp_path, out, gds=None, cell="tiny", **kw):
    exe = fake_magic(tmp_path)
    return emir("extract", "--tool", "magic", "--gds", gds or os.path.join(TINY, "tiny.gds"),
                "--cell", cell, "--process", SKY, "--magic", exe, "--magicrc",
                rcfile(tmp_path), "--out", str(out), "--format", "json", **kw)


def test_out_pointed_at_the_persons_directory_keeps_their_files(tmp_path):
    """--out naming the person's own directory: magic ran there, removed a
    layout called input.gds (left a link to itself) and overwrote their
    <cell>.dspf. Now magic runs in magic/ under it, EMIR's own, and a
    <cell>.dspf or <cell>.cc.spice EMIR did not write is refused (EMIR-E646)."""
    pytest.importorskip("klayout.db")
    d = tmp_path / "mine"
    d.mkdir()
    shutil.copy(os.path.join(TINY, "tiny.gds"), d / "input.gds")
    p = _extract(tmp_path, d, gds=str(d / "input.gds"))
    assert p.returncode == 0, p.stdout + p.stderr
    assert (d / "input.gds").is_file() and not (d / "input.gds").is_symlink()
    assert (d / "tiny.dspf").is_file() and (d / "magic" / "magic.log").is_file()
    # a second run replaces EMIR's own files
    assert _extract(tmp_path, d, gds=str(d / "input.gds")).returncode == 0
    for name in ("tiny.dspf", "tiny.cc.spice"):
        e = tmp_path / f"e_{name}"
        e.mkdir()
        (e / name).write_text("* the person's own file\n")
        p = _extract(tmp_path, e)
        assert p.returncode == 2, p.stdout + p.stderr
        doc = json.loads(p.stdout)
        assert doc["code"] == "EMIR-E646" and name in doc["message"]
        assert (e / name).read_text() == "* the person's own file\n"
        assert not (e / "magic").exists()
    # a magic/ directory of the person's
    f = tmp_path / "f"
    (f / "magic").mkdir(parents=True)
    (f / "magic" / "notes.txt").write_text("mine\n")
    p = _extract(tmp_path, f)
    assert json.loads(p.stdout)["code"] == "EMIR-E646"
    assert sorted(os.listdir(f / "magic")) == ["notes.txt"]


def test_the_layout_in_the_work_directory_is_never_removed(tmp_path):
    """The layout given is the work directory's input.gds itself: kept, not
    replaced by a link to itself."""
    from emir import magic
    work = tmp_path / "w"
    magic.own_work(str(work))
    gds = work / magic.GDS_NAME
    shutil.copy(os.path.join(TINY, "tiny.gds"), gds)
    magic._link_layout(str(gds), str(gds))
    assert gds.is_file() and not gds.is_symlink() and gds.stat().st_size > 1000
    link = tmp_path / "w2"
    magic.own_work(str(link))
    magic._link_layout(str(gds), str(link / magic.GDS_NAME))
    magic._link_layout(str(link / magic.GDS_NAME), str(link / magic.GDS_NAME))
    assert os.path.realpath(link / magic.GDS_NAME) == str(gds.resolve())


def test_out_outside_the_working_directory_over_mcp_is_refused_too(tmp_path, monkeypatch):
    """Over MCP the person's <cell>.dspf in `out` was overwritten with no
    go-ahead: the call is refused the same way (EMIR-E646)."""
    pytest.importorskip("klayout.db")
    from emir import mcp_tools
    here, d = tmp_path / "here", tmp_path / "theirs"
    here.mkdir()
    d.mkdir()
    (d / "tiny.dspf").write_text("* the person's own DSPF\n")
    exe = fake_magic(tmp_path)
    monkeypatch.chdir(here)
    spec = mcp_tools.by_command("extract")
    rc, doc, _ = mcp_tools.call(spec, {"tool": "magic", "gds": os.path.join(TINY, "tiny.gds"),
                                       "cell": "tiny", "process": SKY, "magic": exe,
                                       "magicrc": rcfile(tmp_path), "out": str(d)})
    assert (rc, doc["code"]) == (2, "EMIR-E646"), doc
    assert (d / "tiny.dspf").read_text() == "* the person's own DSPF\n"


# ------------------------------------------------------------ which magic a process file may name


def test_a_process_file_names_only_a_magic_on_path(tmp_path, monkeypatch):
    """`magic: binary: ./notmagic.sh` in a process file made EMIR run that
    script. A process file may name only a magic program on $PATH by its bare
    name: anything else is refused (EMIR-E648), by the run, the input check
    and the schema."""
    from emir import magic
    from emir.check import Report, check_process
    from emir.errors import EmirError
    marker = tmp_path / "ran"
    script = tmp_path / "notmagic.sh"
    script.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    script.chmod(0o755)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("EMIR_MAGIC", raising=False)
    monkeypatch.setenv("EMIR_SITE", os.devnull)
    for bad in ("./notmagic.sh", str(script), "sh", ["magic", "../x/magic"]):
        d = sky()
        d["magic"]["binary"] = bad
        with pytest.raises(EmirError) as e:
            magic.find_magic(d, None, log=lambda *a: None)
        assert e.value.code == "EMIR-E648", bad
        rep = Report()
        check_process(write(tmp_path / "p.yaml", yaml.safe_dump(d)), rep)
        assert "EMIR-E648" in rep.codes("FAIL")
        jsonschema = pytest.importorskip("jsonschema")
        from emir import schemas
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(d, schemas.get(schemas.key("process")))
    assert not marker.exists()
    # a bare magic name is looked up on $PATH only
    (tmp_path / "magic-x").write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    (tmp_path / "magic-x").chmod(0o755)
    monkeypatch.setenv("PATH", "/nonexistent")
    assert magic._executable("magic-x", path_only=True) is None
    d = sky()
    d["magic"]["binary"] = "magic-x"
    assert [v for v, s in magic.candidates(d) if s.startswith("the process")] == ["magic-x"]
    assert not marker.exists()


@pytest.mark.magic
def test_a_real_magic_extracts_the_pair_its_hierarchy_and_its_band(tmp_path):
    """With a magic that writes geometry: pair.gds gives the committed
    network (its resistors, nets, pins and devices; magic 8.3.684 extracts
    other capacitances than 8.3.515, which made the committed files), the
    unlabelled net with its capacitance; pair_h.gds (the same layout in a
    subcell with labels of its own) gives the same DSPF, its ports in the
    order of its labels; and pair_d.gds's 45-degree band is said (EMIR-W647)
    with magic's width the band's height, not its width across (height /
    sqrt(2))."""
    pytest.importorskip("klayout.db")
    root = os.environ.get("PDK_ROOT")
    if not (root and os.path.isdir(os.path.join(root, "sky130A"))):
        pytest.skip("needs PDK_ROOT with sky130A")
    exe = _geo_magic()
    if exe is None:
        pytest.skip("no magic that writes resistor geometry")
    sys.path.insert(0, TINY)
    try:
        import make_pair
    finally:
        sys.path.remove(TINY)
    paths = [str(tmp_path / f"{c}.gds") for c in ("pair", "pair_h", "pair_d")]
    make_pair.make(*paths)
    docs, texts = {}, {}
    for cell, gds in zip(("pair", "pair_h", "pair_d"), paths):
        p = emir("extract", "--tool", "magic", "--gds", gds, "--cell", cell, "--process", SKY,
                 "--magic", exe, "--out", str(tmp_path / cell), "--format", "json")
        assert p.returncode == 0, p.stdout + p.stderr
        docs[cell] = json.loads(p.stdout)
        texts[cell] = open(docs[cell]["dspf"], encoding="utf-8").read()
    _, want = converted_pair(tmp_path / "committed")

    def body(text, cell):
        """The DSPF but its date and name; its capacitors as a sorted list of
        (nodes, value): magic writes them in an order of its own, run to run."""
        lines = [ln.replace(cell, "pair") for ln in text.splitlines()
                 if not ln.startswith(("*|DATE", "*|DESIGN", ".SUBCKT", ".ENDS"))]
        caps = sorted((tuple(sorted(ln.split()[1:3])), ln.split()[3]) for ln in lines
                      if ln.startswith("C"))
        return [ln for ln in lines if not ln.startswith("C")], caps
    def network(text):
        """Resistors by layer, kind, value and size (magic numbers its nodes
        afresh each run), and the nets, pins, device terminals and devices."""
        out = []
        for ln in text.splitlines():
            w = ln.split()
            if ln.startswith("R"):
                ann = dict(re.findall(r"\$(\w+)=(\S+)", ln))
                out.append(("R", round(float(w[3]), 3), ann.get("layer"), ann.get("dir"),
                            ann.get("W"), ann.get("L"), ann.get("cuts")))
            elif ln.startswith(("*|NET", "*|P")):
                out.append((w[0], w[1].split(".")[0]))
            elif ln.startswith("*|I"):
                out.append((w[0], w[1].split(".")[0], w[2], w[3]))
            elif ln.startswith("X"):
                out.append((w[0], w[-1] if "=" not in w[-1] else
                            next(x for x in reversed(w) if "=" not in x)))
        return sorted(out, key=str)
    assert network(texts["pair"]) == network(want)
    cap = {ln.split()[1]: float(ln.split()[2]) for ln in texts["pair"].splitlines()
           if ln.startswith("*|NET")}
    assert set(cap) == {"a_188_0", "g", "out", "vss"} and min(cap.values()) > 0
    assert "#" not in texts["pair"]
    assert body(texts["pair_h"], "pair_h") == body(texts["pair"], "pair")
    assert sorted(docs["pair_h"]["ports"]) == sorted(docs["pair"]["ports"]) == ["g", "out",
                                                                                 "vss"]
    assert docs["pair"]["floating_capacitance"] == docs["pair_h"]["floating_capacitance"] == []
    d = docs["pair_d"]
    assert d["diagonal_resistors"] and "out" in d["ports"]
    widths = [float(re.search(r"\$W=(\S+)", ln).group(1)) for ln in texts["pair_d"].splitlines()
              if ln.startswith("R") and "$dir=diag" in ln]
    assert max(widths) == pytest.approx(make_pair.BAND_HEIGHT)


def test_an_empty_layout_is_said_to_hold_no_cell(tmp_path):
    """EMIR-E212 is raised for a GDS with no cell; its title and hint spoke of
    conductor geometry and the process file's gds: numbers, which no path
    reads any more."""
    db = pytest.importorskip("klayout.db")
    from emir.errors import lookup
    gds = str(tmp_path / "empty.gds")
    db.Layout().write(gds)
    p = emir("extract", "--tool", "magic", "--gds", gds, "--process", SKY, "--out",
             str(tmp_path / "o"), "--format", "json")
    doc = json.loads(p.stdout)
    assert (p.returncode, doc["code"]) == (2, "EMIR-E212") and "holds no cell" in doc["message"]
    c = lookup("EMIR-E212")
    assert c.title == "no cell in the layout"
    assert "gds:" not in c.hint and "conductor" not in c.meaning


def test_choose_mode_says_what_each_route_measured_on_a_magic_layout(tmp_path):
    """On a magic network the open loop and the loop read a signal net low
    (example 08's gate net vb in the loop: rms -6.55 %); choose-mode quoted
    only the synthetic meshes, where every net is a supply. For a DSPF the
    magic route wrote, each route now also says what example 08 measured,
    its gate net included, and names example 08's README as the source."""
    from emir.modes import MAGIC_ACCURACY, MAGIC_SOURCE
    res, _ = converted(tmp_path)

    def choices(dspf, net="vss", process=SKY):
        p = emir("next", "--dspf", dspf, "--process", process, "--net", net, "--format",
                 "json")
        assert p.returncode == 0, p.stderr
        steps = {s["id"]: s for s in json.loads(p.stdout)["steps"]}
        assert "choose-mode" in steps, list(steps)
        return {(c["mode"], c["self_consistent"]): c for c in steps["choose-mode"]["choices"]}
    got = choices(res["dspf"])
    for key, text in MAGIC_ACCURACY.items():
        assert got[key]["accuracy"].endswith(text) and MAGIC_SOURCE in got[key]["source"]
    assert "LOW" in got[("cc-grid", True)]["accuracy"] and "signal net" in got[
        ("cc-grid", False)]["accuracy"]
    sample = os.path.join(ROOT, "emir", "data", "sample")
    plain = choices(os.path.join(sample, "rail.dspf"), "VDD", os.path.join(sample,
                                                                         "process.yaml"))
    assert all(MAGIC_SOURCE not in c["source"] for c in plain.values())
    # every number quoted is example 08's README's
    readme = open(os.path.join(ROOT, MAGIC_SOURCE), encoding="utf-8").read()
    for text in MAGIC_ACCURACY.values():
        for num in re.findall(r"[+-]\d+\.\d+ %", text):
            assert num in readme, num
