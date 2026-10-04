"""Inputs that tried to become code or to write outside the output directory
(the pre-publication audit): every repro is a test here.

- names read from input files (a DSPF's design, a net, a GDS cell) reach file
  names only as one safe component, inside the directory the option names;
- a design name cannot put script into the HTML index;
- a site file in the working directory is not read (it names programs);
- magic's script names only fixed, safe words (a cell `x[exec ...]` once ran);
- the Quantus command file refuses values its Tcl parser would run;
- a testbench that runs commands of its own is detected, and an existing
  `.spiceinit` is never overwritten.
"""
import json
import os
import re
import stat
import subprocess
import sys

import pytest

from _helpers import ROOT

from emir import cli
from emir.errors import EmirError
from emir.output import contained, safe_component
from emir.selftest import SAMPLE_CURRENTS, sample_paths

DSPF, PROC = sample_paths()


def _files_under(root):
    return {os.path.relpath(os.path.join(d, f), root)
            for d, _dirs, fs in os.walk(root) for f in fs}


def _sample(tmp_path, design=None, net=None, name="x.dspf"):
    text = open(DSPF, encoding="utf-8").read()
    if design is not None:
        text = text.replace('*|DESIGN "railblock"', f'*|DESIGN "{design}"')
    currents = json.loads(SAMPLE_CURRENTS)
    if net is not None:
        text = re.sub(r"\bVDD\b", net, text)
        currents = {(net if k == "VDD" else k): v for k, v in currents.items()}
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p), json.dumps(currents)


# ------------------------------------------------------------ file names from inputs


@pytest.mark.parametrize("bad", ["x", "../../x", "/tmp/x/../y", "..", "a\nb", "XI0/VDD"])
def test_safe_component_is_one_harmless_component(bad):
    s = safe_component(bad)
    assert s and "/" not in s and "\\" not in s and s not in (".", "..")
    assert re.fullmatch(r"[A-Za-z0-9_.+-]+", s)
    assert (s == bad) == (bad == "x")
    assert safe_component("XI0/VDD") != safe_component("XI0_VDD")


def test_contained_refuses_a_path_outside(tmp_path):
    assert contained(str(tmp_path), str(tmp_path / "a" / "b.html"))
    with pytest.raises(EmirError) as e:
        contained(str(tmp_path / "out"), str(tmp_path / "out" / ".." / "x.html"))
    assert e.value.code == "EMIR-E701"


@pytest.mark.parametrize("design", ["../../escaped", "/tmp/emir_abs_escape"])
def test_a_design_name_cannot_write_outside_html(tmp_path, capsys, design):
    work = tmp_path / "work"
    work.mkdir()
    dspf, cur = _sample(work, design=design)
    html = work / "out" / "html"
    before = _files_under(tmp_path)
    code = cli.main(["run", dspf, "--process", PROC, "--currents", cur, "--html", str(html),
                     "--format", "json"])
    capsys.readouterr()
    assert code == 0
    new = _files_under(tmp_path) - before
    assert new and all(f.startswith(os.path.join("work", "out", "html") + os.sep) for f in new), new
    assert not os.path.exists("/tmp/emir_abs_escape_VDD.html")


def test_a_hierarchical_net_gets_its_own_report_in_the_index(tmp_path, capsys):
    dspf, cur = _sample(tmp_path, net="XI0/VDD")
    html = tmp_path / "html"
    code = cli.main(["run", dspf, "--process", PROC, "--currents", cur, "--html", str(html),
                     "--format", "json"])
    capsys.readouterr()
    assert code == 0
    reports = [f for f in os.listdir(html) if f.endswith(".html") and f != "index.html"]
    assert len(reports) == 1 and "/" not in reports[0]
    assert not any(os.path.isdir(html / f) for f in os.listdir(html))
    index = (html / "index.html").read_text(encoding="utf-8")
    assert reports[0] in index and "XI0/VDD" in index


@pytest.mark.parametrize("design", ["r';alert(document.cookie);'", "</script><script>alert(1)"])
def test_a_design_name_puts_no_script_into_the_index(tmp_path, capsys, design):
    dspf, cur = _sample(tmp_path, design=design)
    html = tmp_path / "html"
    code = cli.main(["run", dspf, "--process", PROC, "--currents", cur, "--html", str(html),
                     "--format", "json"])
    capsys.readouterr()
    assert code == 0
    index = (html / "index.html").read_text(encoding="utf-8")
    # no handler built from data, the only script element is the page's own,
    # and the row's link holds no quote or angle bracket
    assert "onclick" not in index
    assert index.count("<script") == 1 and "</script><script>alert" not in index
    rows = re.findall(r'data-href="([^"]*)"', index)
    assert rows and all(not set("'\"<>") & set(r) for r in rows)


# ------------------------------------------------------------ GDS cell names


def _gds_cell(path, cell):
    db = pytest.importorskip("klayout.db")
    ly = db.Layout()
    ly.dbu = 0.001
    top = ly.create_cell(cell)
    m1 = ly.layer(68, 20)
    top.shapes(m1).insert(db.Box(0, 0, 20000, 1000))
    top.shapes(ly.layer(68, 5)).insert(db.Text("VDD", db.Trans(db.Vector(500, 500))))
    ly.write(str(path))
    return str(path)


def test_a_gds_cell_name_cannot_reach_magics_script_or_the_files(tmp_path, capsys):
    """A cell named like a path or a Tcl command is refused before magic runs
    (EMIR-E243), and nothing is written."""
    pytest.importorskip("klayout.db")
    work = tmp_path / "work"
    work.mkdir()
    for cell in ("../../pwned_cell", "blk[exec touch MARK]"):
        gds = _gds_cell(work / "evil.gds", cell)
        before = _files_under(tmp_path)
        code = cli.main(["extract", "--tool", "magic", "--gds", gds, "--process",
                         os.path.join(ROOT, "pdks", "sky130.yaml"), "--magicrc", os.devnull,
                         "--magic", "/no/such/magic", "--out", str(work / "out"),
                         "--format", "json"])
        doc = json.loads(capsys.readouterr().out)
        assert code == 2 and doc["code"] == "EMIR-E243", doc
        assert _files_under(tmp_path) == before


# ------------------------------------------------------------ the site file


def test_a_site_file_in_the_working_directory_runs_nothing(tmp_path):
    mark = tmp_path / "RAN_FROM_CWD_SITE_FILE"
    evil = tmp_path / "evil.sh"
    evil.write_text(f"#!/bin/sh\ntouch {mark}\necho 'magic 9.9.9'\n")
    evil.chmod(evil.stat().st_mode | stat.S_IEXEC)
    (tmp_path / "emir_site.yaml").write_text(f"magic: {evil}\n")
    env = {k: v for k, v in os.environ.items() if k not in ("EMIR_SITE", "MAGIC_BIN")}
    env["HOME"] = str(tmp_path / "home")             # no ~/.emir/site.yaml either
    env["PYTHONPATH"] = ROOT
    p = subprocess.run([sys.executable, "-m", "emir", "info", "--format", "json"],
                       cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stderr
    assert not mark.exists()
    assert str(evil) not in p.stdout
    # named explicitly, the same file is the site's own choice
    env["EMIR_SITE"] = str(tmp_path / "emir_site.yaml")
    p = subprocess.run([sys.executable, "-m", "emir", "info", "--format", "json"],
                       cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    assert p.returncode == 0 and str(evil) in p.stdout


# ------------------------------------------------------------ magic's script


def test_magic_script_names_only_fixed_words(tmp_path):
    """A fake magic records the script it is given: no path from the command
    line is in it, the layout is read as a fixed name, and an unsafe cell is
    refused before magic runs."""
    from emir import magic
    work = tmp_path / "a b" / "ext"
    rec = tmp_path / "script.seen"
    fake = tmp_path / "magic"
    fake.write_text("#!/bin/sh\n"
                    f"for a; do s=$a; done\ncp \"$s\" '{rec}'\n"
                    "touch blk_pex.spice blk_lumped.spice blk.res.geo\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    gds = tmp_path / "a b" / "my layout.gds"
    gds.parent.mkdir()
    gds.write_text("")
    magic.run_magic(str(gds), "blk", os.devnull, str(fake), str(work), log=lambda *a: None)
    script = rec.read_text()
    assert "a b" not in script and str(tmp_path) not in script
    assert re.search(rf"^gds read {magic.GDS_NAME}$", script, re.M)
    assert re.search(r"^load blk$", script, re.M)
    with pytest.raises(EmirError) as e:
        magic.run_magic(str(gds), "blk[exec touch X]", os.devnull, str(fake), str(work))
    assert e.value.code == "EMIR-E243"


@pytest.mark.parametrize("pdk", ["/tmp/x", "../x", "a/b"])
def test_a_pdk_name_that_is_a_path_is_not_used(pdk, monkeypatch):
    from emir import magic
    monkeypatch.delenv("EMIR_MAGICRC", raising=False)
    monkeypatch.setenv("PDK_ROOT", "/nonexistent")
    with pytest.raises(EmirError) as e:
        magic.magicrc_for({"pdk": pdk}, None)
    assert e.value.code == "EMIR-E641"


@pytest.mark.parametrize("name", ["blk", "ota_1", "a.b", "x-1+2"])
def test_plain_cell_names_pass(name):
    from emir import magic
    assert magic.check_cell(name) == name


@pytest.mark.parametrize("name", ["blk[exec x]", "a b", "a\n.control", "a;b", "a$b", "a\"b",
                                  "../x", "x<0>"])
def test_unsafe_cell_names_are_refused(name):
    from emir import magic
    with pytest.raises(EmirError) as e:
        magic.check_cell(name)
    assert e.value.code == "EMIR-E243"


# ------------------------------------------------------------ the Quantus command file


@pytest.mark.parametrize("cell", ["../x[exec id]", 'a"b', "a b", "x$y{z}"])
def test_the_ccl_refuses_a_cell_its_parser_would_run(tmp_path, cell):
    from emir import cadence
    with pytest.raises(EmirError) as e:
        cadence.render_ccl(str(tmp_path / "svdb"), cell, "x.dspf", "/t", "typ", 25.0)
    assert e.value.code == "EMIR-E513"


def test_the_ccl_refuses_a_path_its_parser_would_run(tmp_path):
    from emir import cadence
    with pytest.raises(EmirError):
        cadence.render_ccl(str(tmp_path / "s[exec id]"), "blk", "x.dspf", "/t", "typ", 25.0)
    with pytest.raises(EmirError):
        cadence.render_ccl(str(tmp_path / "svdb"), "blk", "x.dspf", '/t"; exec id; "', "typ",
                           25.0)
    assert "blk" in cadence.render_ccl(str(tmp_path / "svdb"), "blk", "x.dspf", "/t", "typ",
                                       25.0)


def test_extract_refuses_an_unsafe_cell(tmp_path, capsys):
    gds, cdl = tmp_path / "a.gds", tmp_path / "a.cdl"
    gds.write_text("")
    cdl.write_text("")
    code = cli.main(["extract", "--gds", str(gds), "--cdl", str(cdl), "--cell", "../x[exec id]",
                     "--kind", "r_only", "--format", "json"])
    d = json.loads(capsys.readouterr().out)
    assert code == 2 and d["code"] == "EMIR-E513"


# ------------------------------------------------------------ testbenches are code


def test_a_deck_that_runs_commands_is_detected(tmp_path):
    from emir.opensim import deck_runs_code
    tb = tmp_path / "tb.cir"
    tb.write_text("* tb\n.include sub/models.lib\nV1 a 0 1\n.tran 1n 10n\n.end\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "models.lib").write_text("* models\n")
    assert deck_runs_code(str(tb)) == []
    (tmp_path / "sub" / "models.lib").write_text("* models\n.control\nshell id\n.endc\n")
    assert deck_runs_code(str(tb)) == [str(tmp_path / "sub" / "models.lib")]
    (tmp_path / "sub" / "models.lib").write_text("* models\n")
    (tmp_path / ".spiceinit").write_text("shell touch X\n")
    assert deck_runs_code(str(tb)) == [str(tmp_path / ".spiceinit")]


def test_an_existing_spiceinit_with_other_content_is_not_overwritten(tmp_path):
    from emir.opensim import spiceinit
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    (src / ".spiceinit").write_text("set ngbehavior=hsa\n")
    tb, deck = src / "tb.cir", dst / "deck.cir"
    assert spiceinit(str(tb), str(deck))                          # copied
    assert spiceinit(str(tb), str(deck))                          # same content: fine
    (dst / ".spiceinit").write_text("the person's own\n")
    with pytest.raises(EmirError) as e:
        spiceinit(str(tb), str(deck))
    assert e.value.code == "EMIR-E105"
    assert (dst / ".spiceinit").read_text() == "the person's own\n"


def test_mcp_simulate_of_a_deck_that_runs_commands_needs_the_go_ahead(tmp_path):
    from emir import mcp_tools
    tb = tmp_path / "tb.cir"
    tb.write_text("* tb\nV1 a 0 1\n.tran 1n 10n\n.control\nshell id\n.endc\n.end\n")
    spec = mcp_tools.by_command("simulate")
    argv = mcp_tools.to_argv(spec, {"testbench": str(tb), "sim": "ngspice", "dspf": DSPF,
                                    "mode": "rcc"})
    why = mcp_tools.gates("simulate", argv, spec.parser)
    assert why and any(".control" in g.reason for g in why)
    tb.write_text("* tb\nV1 a 0 1\n.tran 1n 10n\n.end\n")
    argv = mcp_tools.to_argv(spec, {"testbench": str(tb), "sim": "ngspice", "dspf": DSPF,
                                    "mode": "rcc"})
    assert not any(".control" in g.reason for g in mcp_tools.gates("simulate", argv,
                                                                     spec.parser))
