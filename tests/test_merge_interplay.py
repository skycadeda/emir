"""Where two features meet in one command: behaviour only their combination
shows, each case checked end to end.

* `emir save-lines` with `--sim xyce|ngspice` and `--out` (opensim): every
  branch current, printed in the open simulator's syntax with its sidecar.
* `emir next --gds` (the magic route): the options parse once (`--gds` is
  emir next's own); a layout alone plans `emir extract --tool magic` and then
  the DSPF's plan; with a DSPF the DSPF plan stands, the layout under its
  charts.
* `emir mcp` (mcp) serves the other features' commands: each is classified
  (no command falls back to "always ask"), the licensed and long ones ask
  first exactly when `emir next` marks the same step `needs_human`, and a
  feature command in a plan carries its typed tool call.
* One layout reader (emir/gds.py) for the magic route: the shared codes for
  an unreadable file and several top cells (E170, E270), its E270 next steps
  re-running the command with each `--cell`.
* Limits typed by hand (process) on the charts' outputs (charts): the run's
  chart names the hand-entered layers and the values with no source; the
  HTML net page shows NO SOURCE in the warn colour.
* `emir next` with no process file (process): the preflight, then the
  process file entered by hand (process-new, process-check), then the
  simulation, planned with the checked file.
"""
import importlib.util
import json
import os

import jsonschema
import pytest
from _helpers import DSPF, PROC, emir, fake_magic, write

from emir import schemas


def jdoc(p, rc=0):
    assert p.returncode == rc, (p.returncode, p.stdout[-1500:], p.stderr[-1500:])
    d = json.loads(p.stdout)
    name = d["schema"].split(".", 1)[1].split("/")[0]
    jsonschema.Draft202012Validator(schemas.get(name)).validate(d)
    return d


def two_net_dspf(tmp, n_big=600) -> str:
    """VDD: `n_big` series resistors to one pin (above the 500-node small-net
    size); VSS: two resistors to one pin (small)."""
    L = ['*|DSPF 1.3', '*|DESIGN "two"', '*|OPERATING_TEMPERATURE 100',
         '*|NET VDD 0', '*|P (VDD I *1 0.0 0.0)']
    for k in range(1, n_big):
        L.append(f"*|S (VDD:{k} {k * 0.1:.1f} 0.0)")
    L.append(f"*|I (M0:s M0 s I 0 {n_big * 0.1:.1f} 0.0)")
    nodes = ["VDD"] + [f"VDD:{k}" for k in range(1, n_big)] + ["M0:s"]
    for k in range(n_big):
        L.append(f"Rm{k} {nodes[k]} {nodes[k + 1]} 0.01 $metal1 $L=0.1 $W=0.75")
    L += ['*|NET VSS 0', '*|P (VSS I *1 0.0 5.0)', '*|S (VSS:1 0.5 5.0)',
          '*|I (M1:s M1 s I 0 1.0 5.0)',
          "Rs0 VSS VSS:1 0.02 $metal1 $L=0.5 $W=0.75",
          "Rs1 VSS:1 M1:s 0.02 $metal1 $L=0.5 $W=0.75"]
    return write(os.path.join(str(tmp), "two.dspf"), "\n".join(L) + "\n.ENDS\n")


def test_save_lines_in_xyce_syntax(tmp_path):
    """Every branch current of the net, with its sidecar and --out."""
    dspf = two_net_dspf(tmp_path)
    side = tmp_path / "r.raw.names"
    out = tmp_path / "tb" / "emir_save.cir"
    d = jdoc(emir("save-lines", dspf, "--net", "VSS", "--sim", "xyce",
                  "--names-out", side, "--out", out, "--format", "json"))
    assert d["mode"] == "rcc" and d["simulator"] == "xyce" and d["direct"] == 2
    assert out.read_text() == "\n".join(d["lines"]) + "\n" and d["out"] == str(out)
    assert json.loads(side.read_text())["map"] == d["names"]
    assert "* emir: 2 branch currents for rcc" in d["lines"][0]
    d = jdoc(emir("save-lines", dspf, "--net", "VSS", "--sim", "xyce", "--format", "json"))
    assert d["names"]["I(RS0)"] == ["Rs0:1", 1] and d["names"]["I(RS1)"] == ["Rs1:1", 1]
    assert d["count"] == 2 and d["nets"] == ["VSS"]


def test_save_lines_in_ngspice_syntax_on_the_sample(tmp_path):
    p = emir("save-lines", DSPF, "--sim", "ngspice", "--names-out",
             tmp_path / "r.raw.names")
    assert p.returncode == 0, p.stderr
    assert p.stdout.splitlines()[3].startswith(".save @rm1[i] @rm2[i] ")
    assert ".options filetype=ascii" in p.stdout
    side = json.loads((tmp_path / "r.raw.names").read_text())
    assert len(side["map"]) == 16


def test_spectre_save_lines_keep_their_out(tmp_path):
    d = jdoc(emir("save-lines", DSPF, "--out", tmp_path / "s.scs", "--format", "json"))
    assert d["simulator"] == "spectre" and d["mode"] == "rcc"
    assert (tmp_path / "s.scs").read_text().startswith("save Rm1:1 ")


# ------------------------------------------------------------ emir next --gds: the magic route

#: a synthetic GDS layer pair and an invented process (round numbers)
M1 = (101, 0)


def proc_file(tmp):
    import yaml
    d = {"pdk": "test (invented)", "warn_fraction": 0.8, "temp_reference": 25.0,
         "em_tref": 25.0,
         "layers": {"Metal1": {"gds": list(M1), "sheet_res": 0.1, "tc1": 0.0, "tc2": 0.0,
                               "jmax_avg": 1.0}}}
    return write(os.path.join(str(tmp), "proc.yaml"), yaml.safe_dump(d))


def gds(tmp, boxes, labels=(), name="t.gds", cells=("top",)):
    """A layout: `boxes` [((layer, dt), (x0, y0, x1, y1))] and `labels`
    [(text, (x, y), (layer, dt))] in the first cell; any further cell names
    become extra top cells."""
    import klayout.db as kdb
    ly = kdb.Layout()
    ly.dbu = 0.001
    top = ly.create_cell(cells[0])
    for g, b in boxes:
        top.shapes(ly.layer(*g)).insert(kdb.DBox(*b))
    for text, (x, y), g in labels:
        top.shapes(ly.layer(*g)).insert(kdb.DText(text, kdb.DTrans(x, y)))
    for c in cells[1:]:
        ly.create_cell(c).shapes(ly.layer(*M1)).insert(kdb.DBox(0, 0, 1, 1))
    path = os.path.join(str(tmp), name)
    ly.write(path)
    return path


def strap(tmp, w=1.0, length=10.0):
    """A Metal1 strap, A at the left end, B at the right end."""
    return gds(tmp, [(M1, (0, 0, length, w))],
               [("A", (0.1, w / 2), M1), ("B", (length - 0.1, w / 2), M1)], name="strap.gds")


def _next(*args, env=None):
    p = emir("next", *args, "--format", "json", env=env)
    assert p.returncode == 0, p.stderr
    d = json.loads(p.stdout)
    jsonschema.Draft202012Validator(schemas.get("next")).validate(d)
    return d


def test_next_routes_a_layout_to_the_magic_route(tmp_path):
    pytest.importorskip("klayout.db")           # draws the layout (the klayout extra)
    g, proc = strap(tmp_path), proc_file(tmp_path)
    # a magic that writes resistor geometry (without one the plan asks for it)
    env = {**os.environ, "EMIR_MAGIC": fake_magic(tmp_path, patched=True)}
    d = _next("--gds", g, "--process", proc, env=env)
    assert [s["id"] for s in d["steps"]] == ["selftest", "extract-magic", "next-dspf"]
    assert "mesh" not in d["inputs"] and "taps" not in d["inputs"]
    nxt = d["steps"][2]
    assert nxt["status"] == "waiting" and nxt["placeholders"] == ["<cell>"]
    d = _next("--gds", g, "--process", proc, "--cell", "top", env=env)
    assert d["steps"][2]["status"] == "todo" and "extract_top/top.dspf" in d["steps"][2][
        "command"]
    # a DSPF as well: its plan stands, the layout under its charts
    d = _next("--gds", g, "--dspf", DSPF, "--process", proc, "--currents",
              '{"VDD": 0.004, "MP0:s": -0.004}', "--net", "VDD", "--html")
    ids = [s["id"] for s in d["steps"]]
    assert "run" in ids and "extract-magic" not in ids
    run = next(s for s in d["steps"] if s["id"] == "run")
    assert f"--gds {g}" in run["command"]


# ------------------------------------------------------------ mcp x every feature


def test_mcp_classifies_every_feature_command(tmp_path):
    from emir import mcp_tools
    specs = mcp_tools.specs()
    served = mcp_tools.served_commands()            # `process new`, not `process`
    assert [k for k in served if k not in mcp_tools.UNGATED | set(mcp_tools.GATES)] == []
    assert {mcp_tools.tool_name(k) for k in served} | {"emir_cli"} == set(specs)

    def gate(cmd, *argv):
        return mcp_tools.gates(cmd, [str(x) for x in argv], specs[mcp_tools.tool_name(cmd)].parser)

    ext = ["--gds", "a.gds", "--cdl", "a.cdl", "--cell", "A", "--kind", "r_only"]
    assert gate("extract", *ext) == []                       # the dry run: the plan only
    g = gate("extract", *ext, "--run")
    assert g and g[0].licensed_tool and g[0].long_run
    tran = write(tmp_path / "tran.cir", "* t\nXdut a b blk\n.tran 1p 1n\n.end\n")
    small = write(tmp_path / "small.gds", "x" * 100)
    assert gate("extract", "--tool", "magic", "--gds", small, "--process", "p.yaml") == []
    assert gate("simulate", tran, "--sim", "xyce", "--dspf", DSPF) == []
    big = two_net_dspf(tmp_path)                                             # 601 nodes
    assert gate("simulate", tran, "--sim", "xyce", "--dspf", big, "--net", "VDD")[0].long_run
    for gone in ("emir_abstract", "emir_probe", "emir_split_stub"):
        assert gone not in specs


def test_mcp_plan_of_a_layout_carries_the_extract_tool_call(tmp_path):
    pytest.importorskip("klayout.db")           # draws the layout (the klayout extra)
    from emir import mcp_tools
    nxt = mcp_tools.specs()["emir_next"]
    rc, doc, _ = mcp_tools.call(nxt, {"gds": strap(tmp_path), "process": proc_file(tmp_path),
                                      "cell": "top"})
    assert rc == 0
    step = next(x for x in doc["steps"] if x["id"] == "extract-magic")
    assert step["tool"]["name"] == "emir_extract"
    spec = mcp_tools.specs()["emir_extract"]
    import shlex
    want = vars(spec.parser.parse_args(shlex.split(step["command"])[2:]))
    got = vars(spec.parser.parse_args(mcp_tools.to_argv(spec, step["tool"]["arguments"])))
    want.pop("format"), got.pop("format")
    assert got == want and got["tool"] == "magic"


# ------------------------------------------------------------ one layout reader


def test_the_magic_route_reads_a_layout_through_the_one_reader(tmp_path):
    pytest.importorskip("klayout.db")           # draws the layout (the klayout extra)
    two = gds(tmp_path, [((1, 0), (0, 0, 10, 1))], [("A", (0.1, 0.5), (1, 0))],
              cells=("top", "other"), name="two.gds")
    bad = write(tmp_path / "bad.gds", "not a layout at all\n")
    proc = proc_file(tmp_path)
    cmd, extra = "extract", ["--tool", "magic"]
    p = emir(cmd, *extra, "--gds", two, "--process", proc, "--format", "json")
    d = json.loads(p.stdout)
    assert p.returncode == 2 and d["code"] == "EMIR-E270", d
    steps = [s["command"] for s in d["next_steps"]]
    assert [c.split("--cell ")[1].split()[0] for c in steps] == ["other", "top"]
    assert all(c.startswith(f"emir {cmd} ") and "--tool magic" in c for c in steps)
    p = emir(cmd, *extra, "--gds", bad, "--process", proc, "--format", "json")
    assert json.loads(p.stdout)["code"] == "EMIR-E170"


def test_next_plans_the_process_file_before_the_simulation():
    d = json.loads(emir("next", "--dspf", DSPF, "--net", "VDD", "--have-xyce",
                        "--format", "json").stdout)
    ids = [s["id"] for s in d["steps"]]
    assert ids[:3] == ["selftest", "process-new", "process-check"], ids
    assert "simulate" in ids and "choose-mode" not in ids and d["mode"]["chosen"] == "rcc"
    # with a process file and the block's period, the check and the run are
    # planned after the simulation (without the period they would be refused)
    d = json.loads(emir("next", "--dspf", DSPF, "--process", PROC, "--net", "VDD",
                        "--period", "1e-9", "--have-xyce", "--format", "json").stdout)
    ids = [s["id"] for s in d["steps"]]
    assert ids[0] == "selftest" and ids.index("simulate") < ids.index("check")


def test_hand_entered_limits_on_the_chart_and_the_html_page(tmp_path):
    from test_process_entry import SAMPLE_CUR, filled

    from emir.scene import limits_notes
    p = filled(tmp_path, **{"layers/metal1/sources/jmax_avg": None})
    out = tmp_path / "html"
    r = emir("run", DSPF, "--process", p, "--currents", SAMPLE_CUR, "--html", str(out),
             "--summary-out", str(tmp_path / "s.json"), "--format", "json")
    assert r.returncode == 0, r.stderr
    d = json.loads(r.stdout)
    assert "EMIR-W720" not in {n["code"] for n in d["notices"]}
    if importlib.util.find_spec("matplotlib") is not None:      # else EMIR-I720, no chart
        assert (out / "emir_chart.png").is_file() and (out / "emir_chart_light.png").is_file()
    notes = limits_notes(d["limits_provenance"])
    # the warning first (the chart draws these lines under "Not checked", in order)
    assert notes[0] == "NO SOURCE (EMIR-W320): metal1 jmax_avg"
    assert notes[1].startswith("Limits entered by hand for ") and "metal1" in notes[1]
    pages = "".join(f.read_text(encoding="utf-8") for f in out.rglob("*.html"))
    assert '<span class="sev WARN">NO SOURCE</span>' in pages
    # a file that is not entered by hand: no note
    assert limits_notes(None) == []
    assert limits_notes({"file": "public-pdk-data", "entries": [
        {"name": "met1", "provenance": "public-pdk-data", "unsourced": []}]}) == []
