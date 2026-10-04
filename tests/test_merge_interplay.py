"""Where two features meet in one command: behaviour only their combination
shows, each case checked end to end.

* `emir save-lines --mode rcc|cc-grid` with `--sim xyce|ngspice` and `--out`
  (opensim): the person's mode, printed in the open simulator's syntax with
  its sidecar.
* `emir run --abstract` (hierarchy) with the big-block options (bigblock):
  --max-memory bounds the top-level superposition (it streams, same
  answer); --dynamic is refused (EMIR-E811) instead of silently ignored,
  because the top-level solve is resistive.
* `emir next --gds` (the magic route): the options parse once (`--gds` is
  emir next's own; `--abstract`... come from the feature modules); a layout
  alone plans `emir extract --tool magic` and then the DSPF's plan; with a
  DSPF the DSPF plan stands, the layout under its charts.
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
* `emir next` with no process file and no mode (process and modes): the
  preflight, then the process file entered by hand (process-new,
  process-check), then the person's choice of mode, whose commands plan
  with the checked file.
* `cap_models` (process and decap): one check, EMIR-E317, the one the
  decap-aware solve applies under --no-check too: a fitted model with signed
  coefficients (esr_w, esr_l) and a fit range from 0 passes; each unusable
  value fails once, never under two codes; the process schema's minimums are
  the check's.
* `emir next --dynamic --em-basis B` with no mode (modes and decap): the
  choose-mode step and each route's command keep --dynamic and --em-basis.
"""
import contextlib
import importlib.util
import io
import json
import os
import types

import jsonschema
import pytest
from _helpers import DSPF, PROC, ROOT, emir, write

from emir import cli, schemas


def jdoc(p, rc=0):
    assert p.returncode == rc, (p.returncode, p.stdout[-1500:], p.stderr[-1500:])
    d = json.loads(p.stdout)
    name = d["schema"].split(".", 1)[1].split("/")[0]
    jsonschema.Draft202012Validator(schemas.get(name)).validate(d)
    return d


def two_net_dspf(tmp, n_big=600) -> str:
    """VDD: `n_big` series resistors to one pin (above the 500-node rcc
    limit -> cc); VSS: two resistors to one pin (small -> rcc)."""
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


def test_save_lines_in_xyce_syntax_per_mode(tmp_path):
    """One mode per call (the person's): VDD's pin current for cc-grid, VSS's
    branch currents for rcc, each with its sidecar and --out."""
    dspf = two_net_dspf(tmp_path)
    side = tmp_path / "r.raw.names"
    out = tmp_path / "tb" / "emir_save.cir"
    d = jdoc(emir("save-lines", dspf, "--mode", "cc-grid", "--net", "VDD", "--sim", "xyce",
                  "--names-out", side, "--out", out, "--format", "json"))
    assert d["mode"] == "cc-grid" and d["simulator"] == "xyce"
    assert d["names"] == {"IS(M0)": ["M0:s", 1]} and d["direct"] == 1
    assert out.read_text() == "\n".join(d["lines"]) + "\n" and d["out"] == str(out)
    assert json.loads(side.read_text())["map"] == d["names"]
    assert "* emir: 1 currents for cc" in d["lines"][0]
    d = jdoc(emir("save-lines", dspf, "--mode", "rcc", "--net", "VSS", "--sim", "xyce",
                  "--format", "json"))
    assert d["names"]["I(RS0)"] == ["Rs0:1", 1] and d["names"]["I(RS1)"] == ["Rs1:1", 1]
    assert d["count"] == 2 and d["nets"] == ["VSS"]


def test_save_lines_in_ngspice_syntax_on_the_sample(tmp_path):
    p = emir("save-lines", DSPF, "--mode", "rcc", "--sim", "ngspice", "--names-out",
             tmp_path / "r.raw.names")
    assert p.returncode == 0, p.stderr
    assert p.stdout.splitlines()[3].startswith(".save @rm1[i] @rm2[i] ")
    assert ".options filetype=ascii" in p.stdout
    side = json.loads((tmp_path / "r.raw.names").read_text())
    assert len(side["map"]) == 16


def test_spectre_save_lines_keep_their_out(tmp_path):
    d = jdoc(emir("save-lines", DSPF, "--mode", "rcc", "--out", tmp_path / "s.scs",
                  "--format", "json"))
    assert d["simulator"] == "spectre" and d["mode"] == "rcc"
    assert (tmp_path / "s.scs").read_text().startswith("save Rm1:1 ")


# ------------------------------------------------------------ hierarchy x bigblock

EX05 = os.path.join(ROOT, "examples", "05_chip_level")


def _quiet(args):
    with contextlib.redirect_stdout(io.StringIO()) as o, \
            contextlib.redirect_stderr(io.StringIO()) as e:
        rc = cli.main([str(x) for x in args])
    return rc, o.getvalue(), e.getvalue()


@pytest.fixture(scope="module")
def chip(tmp_path_factory):
    spec = importlib.util.spec_from_file_location("make_inputs",
                                                  os.path.join(EX05, "make_inputs.py"))
    mi = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mi)
    d = str(tmp_path_factory.mktemp("chip"))
    with contextlib.redirect_stdout(io.StringIO()):
        mi.main(d)
    j = lambda f: os.path.join(d, f)                                     # noqa: E731
    for name, per, pts in (("adc", "10e-9", "4000"), ("pll", "2.5e-9", "1000")):
        rc, o, e = _quiet(["abstract", j(f"{name}.dspf"), "--process", j("process.yaml"),
                           "--cc-sim", j(f"{name}.raw"), "--mode", "cc-grid", "--period", per,
                           "--points", pts,
                           "--vdd", "1.0", "-o", j(f"{name}.abstract.json")])
        assert rc == 0, o + e
    top = [j("top.dspf"), "--process", j("process.yaml"), "--net", "VDD",
           "--abstract", j("adc.abstract.json") + "@XADC",
           "--abstract", j("pll.abstract.json") + "@XPLL",
           "--place", "XPLL=40,0,R0", "--align", "sync"]
    return types.SimpleNamespace(dir=d, top=top)


def _run(args, rc=0):
    code, out, err = _quiet(["run", *args, "--format", "json"])
    assert code == rc, (code, out[-1500:], err[-1500:])
    d = json.loads(out)
    jsonschema.Draft202012Validator(schemas.get(d["schema"].split(".", 1)[1].split("/")[0])
                                    ).validate(d)
    return d


def test_the_top_run_takes_the_big_block_superposition_options(chip):
    dense = _run(chip.top)
    stream = _run([*chip.top, "--superposition", "stream", "--max-memory", "1M"])
    a, b = dense["nets"][0], stream["nets"][0]
    assert a["superposition"]["method"] == "dense"
    assert b["superposition"]["method"] == "stream"
    assert b["superposition"]["budget_bytes"] == 1 << 20
    assert b["ir_drop_mV"] == pytest.approx(a["ir_drop_mV"], rel=1e-10)
    assert [w["name"] for w in b["worst_items"]] == [w["name"] for w in a["worst_items"]]
    assert [w["ratio"] for w in b["worst_items"]] == \
        pytest.approx([w["ratio"] for w in a["worst_items"]], rel=1e-10)


def test_dynamic_with_abstracts_is_refused_not_ignored(chip):
    code, out, err = _quiet(["check", *chip.top, "--dynamic", "--format", "json"])
    doc = json.loads(out)
    assert code == 2 and any(f["code"] == "EMIR-E811" and "--dynamic" in f["message"]
                             for f in doc["findings"])
    d = _run([*chip.top, "--dynamic"], rc=2)                 # the run's input check
    assert d["code"] == "EMIR-E501"
    d = _run([*chip.top, "--dynamic", "--no-check"], rc=2)   # and the run itself
    assert d["code"] == "EMIR-E811" and "--dynamic" in d["message"]


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


def _next(*args):
    p = emir("next", *args, "--format", "json")
    assert p.returncode == 0, p.stderr
    d = json.loads(p.stdout)
    jsonschema.Draft202012Validator(schemas.get("next")).validate(d)
    return d


def test_next_routes_a_layout_to_the_magic_route(tmp_path):
    pytest.importorskip("klayout.db")           # draws the layout (the klayout extra)
    g, proc = strap(tmp_path), proc_file(tmp_path)
    d = _next("--gds", g, "--process", proc)
    assert [s["id"] for s in d["steps"]] == ["selftest", "extract-magic", "next-dspf"]
    assert "mesh" not in d["inputs"] and "taps" not in d["inputs"]
    nxt = d["steps"][2]
    assert nxt["status"] == "waiting" and nxt["placeholders"] == ["<cell>"]
    d = _next("--gds", g, "--process", proc, "--cell", "top")
    assert d["steps"][2]["status"] == "todo" and "extract_top/top.dspf" in d["steps"][2][
        "command"]
    # a DSPF as well: its plan stands, the layout under its charts
    d = _next("--gds", g, "--dspf", DSPF, "--process", proc, "--currents",
              '{"VDD": 0.004, "MP0:s": -0.004}', "--net", "VDD", "--html")
    ids = [s["id"] for s in d["steps"]]
    assert "run" in ids and "extract-magic" not in ids
    run = next(s for s in d["steps"] if s["id"] == "run")
    assert f"--gds {g}" in run["command"]
    # the hierarchy options still parse (from plan_hierarchy.add_arguments)
    d = _next("--dspf", DSPF, "--abstract", "x.json@XA", "--align", "sync")
    assert d["inputs"]["abstracts"] == ["x.json@XA"] and d["inputs"]["align"] == "sync"


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
    assert gate("simulate", tran, "--sim", "xyce", "--dspf", DSPF, "--mode", "rcc") == []
    big = two_net_dspf(tmp_path)                                             # 601 nodes
    assert gate("simulate", tran, "--sim", "xyce", "--dspf", big, "--net", "VDD", "--mode",
                "rcc")[0].long_run
    assert gate("abstract", DSPF, "--process", "p.yaml", "--cc-sim", "r.raw", "--mode",
                "cc-grid", "-o", "x.json") == []
    assert gate("probe", tran, "-o", "p.cir", "--net", "a") == []
    assert specs["emir_abstract"].outputs == ["abstract_summary", "error"] or \
        specs["emir_abstract"].output_schema()["anyOf"][0]["properties"]["schema"]


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


def test_next_plans_the_process_file_before_the_choice_of_mode():
    d = json.loads(emir("next", "--dspf", DSPF, "--net", "VDD", "--have-xyce",
                        "--format", "json").stdout)
    ids = [s["id"] for s in d["steps"]]
    assert ids == ["selftest", "process-new", "process-check", "choose-mode"], ids
    st = d["steps"][3]
    assert st["needs_human"] and st["after"] == ["process-check"] and st["n"] == 4
    assert "--process emir_out/process.yaml" in st["command"]
    for c in st["choices"]:
        assert "--process emir_out/process.yaml" in c["command"], c["command"]
    # with a process file, the choice comes first, as before
    d = json.loads(emir("next", "--dspf", DSPF, "--process", PROC, "--net", "VDD",
                        "--have-xyce", "--format", "json").stdout)
    assert [s["id"] for s in d["steps"]] == ["choose-mode", "selftest"]


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


# ------------------------------------------------- cap_models: process + decap

CAP_FIT = {"cox": 0.01, "esr_a": 20.0, "esr_w": -0.5, "esr_l": -1.5,
           "esr_w_range": [0, 30], "esr_l_range": [0.15, 4.0], "esr_fit_freq": 1e9}


def _cap_findings(tmp_path, model):
    import yaml

    from emir.check import Report, check_process
    with open(PROC, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    data["cap_models"] = {"ncap": model}
    p = write(tmp_path / "cap.yaml", yaml.safe_dump(data))
    rep = Report()
    check_process(p, rep)
    return [(x.code, x.message) for x in rep.findings if x.level == "FAIL"]


def test_a_fitted_decap_model_passes_the_one_cap_check(tmp_path):
    """The merged check once refused a valid fit twice (EMIR-E308: `esr_w`
    and `esr_w_range` "must be a positive number"): the fit's coefficients
    are signed and a range may start at 0."""
    assert _cap_findings(tmp_path, CAP_FIT) == []
    cap = schemas.get("process")["properties"]["cap_models"]
    jsonschema.Draft202012Validator(cap).validate({"ncap": CAP_FIT})


@pytest.mark.parametrize("change,what", [
    ({"cox": -0.01}, "`cox` must be a non-negative number"),
    ({"cox": 0}, "no capacitance (`cox` 0)"),
    ({"esr_a": "ten"}, "`esr_a` must be a number"),
    ({"esr_w": True}, "`esr_w` must be a number"),
    ({"esr_fit_freq": 0}, "`esr_fit_freq` must be a positive frequency"),
    ({"esr_w_range": [30, 0]}, "`esr_w_range` must be [low, high]"),
    ({"esr_l_range": [-1, 4]}, "`esr_l_range` must be [low, high]")])
def test_each_unusable_cap_value_fails_once(tmp_path, change, what):
    """One code family for one value: EMIR-E317 once (with main's E308 loop
    beside the branch's check, a negative cox failed under both)."""
    fails = _cap_findings(tmp_path, {**CAP_FIT, **change})
    assert [c for c, _m in fails] == ["EMIR-E317"], fails
    assert what in fails[0][1]


def test_the_process_schema_holds_the_cap_checks_minimums():
    from emir.decap import CAP_NONNEG, CAP_RANGES
    cap = schemas.get("process")["properties"]["cap_models"]["additionalProperties"][
        "properties"]
    assert {k for k, v in cap.items() if v.get("minimum") == 0} == set(CAP_NONNEG)
    assert cap["esr_fit_freq"]["exclusiveMinimum"] == 0
    assert "minimum" not in cap["esr_w"] and "minimum" not in cap["esr_l"]
    for k in CAP_RANGES:
        assert cap[k]["items"]["minimum"] == 0 and cap[k]["minItems"] == 2


def test_cap_models_not_a_mapping_is_refused_not_a_crash(tmp_path):
    """`cap_models: [1, 2]` crashed the key check (EMIR-E901); now it fails
    once, EMIR-E317."""
    import yaml

    from emir.check import Report, check_process
    with open(PROC, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    data["cap_models"] = [1, 2]
    rep = Report()
    check_process(write(tmp_path / "cap.yaml", yaml.safe_dump(data)), rep)
    assert [x.code for x in rep.findings if x.level == "FAIL"] == ["EMIR-E317"]


def _decap_case(tmp_path):
    """Example 13's small case (a mesh, 4 MOS decaps of model `ncap`, a CC
    result over 3 periods): (case, the process file's data)."""
    import sys

    import yaml
    sys.path.insert(0, os.path.join(ROOT, "examples", "13_decap_aware"))
    import decap_mesh
    c = decap_mesh.small_case(str(tmp_path / "small"))
    with open(c["process"], encoding="utf-8") as f:
        return c, yaml.safe_load(f)


def _with_cap(tmp_path, data, model, name="cap.yaml"):
    import yaml
    return write(tmp_path / name, yaml.safe_dump({**data, "cap_models": {"ncap": model}}))


@pytest.mark.parametrize("entry", [5, [1, 2], "ncap"])
def test_a_cap_model_entry_that_is_not_a_mapping_is_refused(tmp_path, entry):
    """`cap_models: {ncap: 5}` (or a list, or text) crashed `emir check`,
    `emir process check` and `emir next` (EMIR-E901, exit 70) and gave
    EMIR-E315 under --no-check: now EMIR-E317 once, exit 2, everywhere."""
    c, data = _decap_case(tmp_path)
    p = _with_cap(tmp_path, data, entry)
    drive = ["--cc-sim", c["raw"], "--net", "VDD", "--mode", "cc-grid"]
    for argv in (["check", c["dspf"], "--process", p, *drive], ["process", "check", p]):
        d = jdoc(emir(*argv, "--format", "json"), rc=2)
        fails = [x for x in d["findings"] if x["level"] == "FAIL"]
        assert [x["code"] for x in fails] == ["EMIR-E317"], fails
        assert "cap_models/ncap must be a mapping" in fails[0]["message"]
    r = emir("next", "--dspf", c["dspf"], "--process", p, "--net", "VDD", "--format", "json")
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
    r = emir("run", c["dspf"], "--process", p, "--cc-sim", c["raw"], "--net", "VDD", "--mode",
             "cc-grid", "--period", c["period"], "--dynamic", "--no-check", "--format", "json")
    assert r.returncode == 2 and json.loads(r.stdout)["code"] == "EMIR-E317", r.stdout[-800:]


@pytest.mark.parametrize("cox", ["0.01", True, "abc"])
def test_a_cap_value_that_is_not_a_number_is_refused_under_no_check(tmp_path, cox):
    """The dynamic solve's refusal under --no-check read the values after
    the engine had converted them: the string "0.01" and `true` passed (as
    0.01 and 1.0), and "abc" got EMIR-E315. It reads the file's own values
    now: EMIR-E317, as `emir check` says."""
    c, data = _decap_case(tmp_path)
    p = _with_cap(tmp_path, data, {"cox": cox, "esr_a": 1.0})
    d = jdoc(emir("check", c["dspf"], "--process", p, "--cc-sim", c["raw"], "--net", "VDD",
                  "--mode", "cc-grid", "--format", "json"), rc=2)
    assert [x["code"] for x in d["findings"] if x["level"] == "FAIL"] == ["EMIR-E317"]
    r = emir("run", c["dspf"], "--process", p, "--cc-sim", c["raw"], "--net", "VDD", "--mode",
             "cc-grid", "--period", c["period"], "--dynamic", "--no-check", "--format", "json")
    doc = json.loads(r.stdout)
    assert r.returncode == 2 and doc["code"] == "EMIR-E317", r.stdout[-800:]
    assert "`cox` must be a number" in doc["message"]


def test_the_choice_of_mode_keeps_dynamic_and_the_basis(tmp_path):
    """`emir next --dynamic --em-basis decap-free` with no --mode (modes and
    decap): the choose-mode step re-plans with the route the person picks,
    and its commands once dropped --dynamic and --em-basis, so the plan of
    the chosen route ran without decap-aware EM."""
    import shlex
    import sys
    sys.path.insert(0, os.path.join(ROOT, "examples", "13_decap_aware"))
    import decap_mesh
    c = decap_mesh.small_case(str(tmp_path / "small"))
    d = jdoc(emir("next", "--dspf", c["dspf"], "--process", c["process"], "--cc-sim",
                  c["raw"], "--net", "VDD", "--period", c["period"], "--dynamic",
                  "--em-basis", "decap-free", "--format", "json"))
    st = next(s for s in d["steps"] if s["id"] == "choose-mode")
    for cmd in [st["command"]] + [x["command"] for x in st["choices"]]:
        a = shlex.split(cmd)
        assert "--dynamic" in a and a[a.index("--em-basis") + 1] == "decap-free", cmd
        assert a[-2:] == ["--format", "json"], cmd
    pick = next(x for x in st["choices"] if x["mode"] == "cc-grid" and
                not x["self_consistent"])
    then = jdoc(emir(*shlex.split(pick["command"])[1:]))
    run = next(s for s in then["steps"] if s["id"] == "run")
    assert "--dynamic" in run["command"] and "--em-basis decap-free" in run["command"]


def test_the_mode_refusal_keeps_the_planning_options(tmp_path):
    """EMIR-E520 (no --mode) on a run with --dynamic, --em-basis and
    --period: its next step, `emir next ...`, dropped all three, so the
    route the person then chose ran without decap-aware EM (and without
    the period it needs). It keeps them now, from `emir run` and from
    `emir check`, and so does the chain from EMIR-E430 (--em-basis worst
    with neither --dynamic nor --mode) through `emir check ... --dynamic`."""
    import shlex
    c, _ = _decap_case(tmp_path)
    args = [c["dspf"], "--process", c["process"], "--cc-sim", c["raw"], "--net", "VDD",
            "--period", str(c["period"]), "--dynamic", "--em-basis", "decap-aware",
            "--format", "json"]

    def keeps(cmd):
        a = shlex.split(cmd)
        assert a[:2] == ["emir", "next"] and "--dynamic" in a, cmd
        assert a[a.index("--em-basis") + 1] == "decap-aware", cmd
        assert a[a.index("--period") + 1] == str(c["period"]), cmd
        return a
    r = emir("run", *args)
    doc = json.loads(r.stdout)
    assert r.returncode == 2 and doc["code"] == "EMIR-E520"
    a = keeps(doc["next_steps"][0]["command"])
    plan = jdoc(emir(*a[1:]))
    st = next(x for x in plan["steps"] if x["id"] == "choose-mode")
    pick = next(x for x in st["choices"] if x["mode"] == "cc-grid" and
                not x["self_consistent"])
    then = jdoc(emir(*shlex.split(pick["command"])[1:]))
    run = shlex.split(next(x for x in then["steps"] if x["id"] == "run")["command"])
    assert "--dynamic" in run and run[run.index("--em-basis") + 1] == "decap-aware"
    assert run[run.index("--period") + 1] == str(c["period"])
    d = jdoc(emir("check", *args), rc=2)
    keeps(next(x for x in d["findings"] if x["code"] == "EMIR-E520")["command"])
    # E430 -> its `emir check ... --dynamic` -> E520 -> emir next with both
    r = emir("run", *args[:9], "--em-basis", "worst", "--format", "json")
    doc = json.loads(r.stdout)
    assert doc["code"] == "EMIR-E430", doc
    nxt = shlex.split(doc["next_steps"][0]["command"])
    d = jdoc(emir(*nxt[1:]), rc=2)
    cmd = shlex.split(next(x for x in d["findings"] if x["code"] == "EMIR-E520")["command"])
    assert "--dynamic" in cmd and cmd[cmd.index("--em-basis") + 1] == "worst", cmd


def test_a_refused_cap_value_is_not_also_a_unit_slip_and_fits_are_checked(tmp_path):
    """A cap value the one check refuses (EMIR-E317) also got a unit-slip
    warning (EMIR-W321), and a hand-entered ESR fit had no unit check at all
    (1000 typed for 1000 mOhm = 1 ohm passed). `emir process new` now offers
    the fit's slots beside ron_sq, each with its unit."""
    from emir.process_file import build_model, from_dspf, render
    c, data = _decap_case(tmp_path)
    for bad in (-0.01, 0, float("inf")):
        p = _with_cap(tmp_path, data, {"cox": bad, "esr_a": 1.0})
        d = jdoc(emir("process", "check", p, "--format", "json"), rc=2)
        codes = [(x["code"], x["level"]) for x in d["findings"]
                 if x["code"] in ("EMIR-E317", "EMIR-W321")]
        assert codes == [("EMIR-E317", "FAIL")], (bad, codes)
    p = _with_cap(tmp_path, data, {"cox": 0.01, "esr_a": 1000.0, "esr_fit_freq": 1.0})
    d = jdoc(emir("process", "check", p, "--format", "json"))
    slips = {x["path"] for x in d["suspects"]}
    assert {"cap_models/ncap/esr_a", "cap_models/ncap/esr_fit_freq"} <= slips, slips
    info = from_dspf(c["dspf"])
    model = build_model(info, None)["cap_models"]["ncap"]
    assert {"esr_a", "esr_w", "esr_l", "esr_fit_freq"} <= set(model)
    text = render(build_model(info, None), info, c["dspf"])
    assert "esr_a: null" in text and "# ohm," in text



def test_the_next_schema_keeps_a_routes_tool_call_and_its_fields():
    """The EM basis choices (decap) replaced the choose-mode routes' schema
    (modes, mcp) in `emir.next/1`: `choices[].tool`, the typed MCP tool call
    of a route, was gone, and a route no longer needed its mode, cost or
    accuracy. One choice schema now holds both kinds, each with its own
    required fields."""
    for name in ("next", "report"):
        sc = schemas.get(name)
        ch = (sc["properties"]["steps"]["items"]["properties"]["choices"]["items"]
              if name == "next" else sc["properties"]["em_choices"]["items"])
        assert "tool" in ch["properties"], name
        v = jsonschema.Draft202012Validator(ch)
        route = {"route": "cc-grid", "mode": "cc-grid", "self_consistent": False,
                 "what": "w", "cost": "c", "accuracy": "a", "possible": True,
                 "recommended": True, "command": "emir next --mode cc-grid",
                 "tool": {"name": "emir_next", "arguments": {"mode": "cc-grid"}}}
        v.validate(route)
        basis = {"route": "EM basis worst", "option": "--em-basis worst", "basis": "worst",
                 "what": "w", "cost": "c", "accuracy": "a", "possible": True,
                 "recommended": True, "verdict": "FAIL", "max_ratio": 1.5,
                 "n_violations": 2, "in_use": True}
        v.validate(basis)
        for bad in ({k: x for k, x in route.items() if k != "cost"},
                    {k: x for k, x in basis.items() if k != "verdict"}):
            assert list(v.iter_errors(bad)), bad
