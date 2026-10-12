"""The open-PDK process files in `pdks/`: each one loads and passes the
checker's process-file checks, its numbers carry the right units, its
citations point at lines that hold the value, and TEMPLATE.yaml documents
every key the checker knows. Also the fixed rms limit (`jmax_rms`) the
tech-LEF-based files need.

The unit cases are hand-checked: sky130 met1 is 2.8 mA/um (tech LEF,
DCCURRENTDENSITY AVERAGE), so a minimum-width 0.14 um wire may carry
2.8 x 0.14 = 0.392 mA on average; a via cut 0.29 mA.
"""
import glob
import os
import re
import subprocess
import sys

import pytest
import yaml
from _helpers import CUR, DSPF, PROC, write

from emir import check as chk
from emir.check import Report, check_process
from emir.dspf import build_parser, run, via_cuts
from emir.engine import Process

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
PDKS = os.path.join(ROOT, "pdks")
FILES = sorted(glob.glob(os.path.join(PDKS, "*.yaml")))
REAL = [f for f in FILES if os.path.basename(f) != "TEMPLATE.yaml"]
TEMPLATE = os.path.join(PDKS, "TEMPLATE.yaml")


def _load(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _ids(files):
    return [os.path.basename(f) for f in files]


# ------------------------------------------------------------- every file


def test_the_files_are_there():
    names = set(_ids(FILES))
    assert {"TEMPLATE.yaml", "sky130.yaml", "gf180mcu.yaml", "ihp_sg13g2.yaml"} <= names
    assert os.path.isfile(os.path.join(PDKS, "README.md"))


@pytest.mark.parametrize("path", FILES, ids=_ids(FILES))
def test_loads_and_passes_the_process_checks(path):
    proc = Process.load(path)
    assert proc.layers and proc.vias
    rep = Report()
    assert check_process(path, rep) is not None
    fails = [f.message for f in rep.findings if f.level == "FAIL"]
    assert not fails, fails


@pytest.mark.parametrize("path", REAL, ids=_ids(REAL))
def test_real_files_warn_only_about_unpublished_limits(path):
    """A real PDK file is not the illustrative sample, and the only warnings
    it may raise are layers with no published EM limit (sky130 li1)."""
    rep = Report()
    check_process(path, rep)
    warns = [f.message for f in rep.findings if f.level == "WARN"]
    assert all("no jmax_* limit" in w for w in warns), warns
    with open(path, encoding="utf-8") as f:
        assert "screening, not sign-off" in f.read()


@pytest.mark.parametrize("path", FILES, ids=_ids(FILES))
def test_stack_and_layer_map_name_real_entries(path):
    d = _load(path)
    names = set(d["layers"]) | set(d["vias"])
    for v, vd in d["vias"].items():
        assert vd["below"] in d["layers"] and vd["above"] in d["layers"], v
    for tok, target in (d.get("dspf_layers") or {}).items():
        assert target in names, (tok, target)
    gds = [tuple(x["gds"]) for s in ("layers", "vias") for x in d[s].values()]
    assert len(gds) == len(set(gds)), "two entries share a GDS layer"


@pytest.mark.parametrize("path", REAL, ids=_ids(REAL))
def test_real_files_give_avg_limits_everywhere_they_are_published(path):
    """Units: mA/um and mA/cut, in the ranges open PDKs publish (a value in
    A/um or uA would be off by 1000)."""
    d = _load(path)
    assert 50 <= d["em_tref"] <= 150
    for name, x in d["layers"].items():
        if "jmax_avg" in x:
            assert 0.1 <= x["jmax_avg"] <= 50, name
        assert 0.001 <= x["sheet_res"] <= 50, name
        assert 0.05 <= x["min_width"] <= 5, name
    for name, x in d["vias"].items():
        assert 0.05 <= x["imax_avg"] <= 20, name
        assert 0.1 <= x["vcwidth"] <= 2, name
        assert 0.01 <= x["res"] <= 50, name


# ------------------------------------------------------------------ units


def test_sky130_met1_minimum_wire_carries_0p392_mA():
    proc = Process.load(os.path.join(PDKS, "sky130.yaml"))
    met1 = proc.layers["met1"]
    assert met1["jmax"]["avg"] == pytest.approx(2.8e-3)          # A/um
    assert met1["jmax"]["avg"] * 0.14 == pytest.approx(0.392e-3)  # A
    assert met1["jmax"]["rms"] == pytest.approx(6.1e-3)           # fixed, A/um
    assert met1["jmax"]["peak"] is None                          # not published
    assert proc.vias["via"]["imax"]["avg"] == pytest.approx(0.29e-3)  # A/cut
    assert proc.vias["via"]["imax"]["rms"] is None
    # a via's cut area recovers its cut count from the LEF cut width
    assert via_cuts({"area": 4 * 0.15 ** 2}, proc.vias["via"]) == (4, "area")
    # analysed at em_tref (Tj = 90 C), R is scaled from the models' 30 C:
    # 1 + 3.179e-3 * 60 + 3.094e-7 * 3600 = 1.19185
    assert met1["rscale"] == pytest.approx(1.19185, rel=1e-5)


def test_sky130_end_to_end_ratios_are_the_hand_numbers(tmp_path):
    """A 10 um met1 strap, 0.14 um wide, and one via cut, written with
    magic's layer names (metal1, via1) so the file's dspf_layers map is used.
    0.196 mA through both: met1 avg 0.196 / 0.392 = 0.5 of its limit, the via
    0.196 / 0.29 = 0.676."""
    dspf = write(tmp_path / "strap.dspf", "\n".join([
        "*|DSPF 1.3", '*|DESIGN "strap"', "*|OPERATING_TEMPERATURE 90",
        "*|GROUND_NET 0", "*|NET VDD 1e-15", "*|P (VDD I *1 0.0 0.0)",
        "*|S (VDD:1 10.0 0.0)", "*|I (M0:s M0 s I 0 10.0 0.0)",
        "R1 VDD VDD:1 8.928571 $metal1 $L=10.0 $W=0.14 "
        "$LLX=0 $LLY=-0.07 $URX=10 $URY=0.07",
        "R2 VDD:1 M0:s 4.5 $via1 $VC=1 $LLX=9.925 $LLY=-0.075 $URX=10.075 $URY=0.075",
        ".ENDS", ""]))
    a = build_parser().parse_args(
        [dspf, "--net", "VDD", "--process", os.path.join(PDKS, "sky130.yaml"),
         "--currents", '{"VDD": 0.000196, "M0:s": -0.000196}',
         "--json", str(tmp_path / "s.json")])
    r = run(a)["VDD"]
    assert r["max_ratio"]["wire"] == pytest.approx(0.5, rel=1e-6)
    assert r["max_ratio"]["via"] == pytest.approx(0.196 / 0.29, rel=1e-6)


# --------------------------------------------------------------- template


def test_template_documents_every_key_the_checker_knows():
    d = _load(TEMPLATE)
    layer_keys = set().union(*(set(x) for x in d["layers"].values()))
    via_keys = set().union(*(set(x) for x in d["vias"].values()))
    assert set(chk._LAYER_REQ) | set(chk._LAYER_NUM) | set(chk._LAYER_SIGNED) <= layer_keys
    assert {"estimate", "ict_name"} <= layer_keys
    assert set(chk._VIA_REQ) | set(chk._VIA_NUM) <= via_keys
    assert {"estimate", "rms_peak_estimate", "ict_name"} <= via_keys
    assert {"pdk", "warn_fraction", "temp_reference", "em_tref", "jmax_factor",
            "dspf_layers", "ict_conductor_pattern"} <= set(d)
    # every top-level key the template shows is one the format has
    from emir.schemas import process_schema
    assert set(d) <= set(process_schema()["properties"])
    # the keys of a file entered by hand: a source per value, the width loss
    # and, as comments (each excludes something the template shows), the
    # provenance and Black's derating
    assert "sources" in layer_keys and "sources" in via_keys and "sources" in d
    text = open(TEMPLATE, encoding="utf-8").read()
    for key in ("# provenance: hand-entered", "# derating:", "#   ea_eV:", "#   n:",
                "emir process new --from-dspf"):
        assert key in text, key


@pytest.mark.parametrize("path", REAL, ids=_ids(REAL))
def test_real_files_say_they_are_public_pdk_data(path):
    assert _load(path)["provenance"] == "public-pdk-data"


def test_template_warns_only_that_it_is_illustrative():
    rep = Report()
    assert check_process(TEMPLATE, rep) is not None
    warns = [f.message for f in rep.findings if f.level == "WARN"]
    assert len(warns) == 1 and "ILLUSTRATIVE" in warns[0], warns


def test_a_filled_copy_of_the_template_passes_emir_check(tmp_path):
    """Copied and renamed (no 'illustrative'), the template is a valid process
    file for `emir check`: exit 0, inputs OK, no warning about the sample."""
    text = open(TEMPLATE, encoding="utf-8").read()
    text = text.replace("ILLUSTRATIVE TEMPLATE -- NOT A FOUNDRY DECK.", "My PDK.")
    text = text.replace("pdk: TEMPLATE (illustrative)", "pdk: mypdk")
    proc = write(tmp_path / "mypdk.yaml", text)
    p = subprocess.run([sys.executable, "-m", "emir", "check", DSPF, "--net", "VDD",
                        "--process", proc, "--currents", CUR],
                       capture_output=True, text=True, cwd=ROOT)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "INPUTS OK" in p.stdout and "ILLUSTRATIVE" not in p.stdout


# ---------------------------------------------------------- fixed rms limit


def _proc_dict(**metal1):
    d = _load(PROC)
    d["layers"]["Metal1"].update(metal1)
    return d


def test_fixed_rms_limit_ignores_delta_t():
    d = _proc_dict(jmax_rms=12.0)
    del d["layers"]["Metal1"]["jmax_rms_k"]
    for dt in (5.0, 20.0):
        m1 = Process(d, delta_t=dt).layers["Metal1"]
        assert m1["jmax"]["rms"] == pytest.approx(12.0e-3)
        assert m1["rms_fixed"]
    # the self-heating form still follows delta_T: sqrt(28.8 * 20) = 24 mA/um
    k = Process(_load(PROC), delta_t=20.0).layers["Metal1"]
    assert k["jmax"]["rms"] == pytest.approx(24.0e-3) and not k["rms_fixed"]


def test_both_rms_forms_are_refused(tmp_path):
    d = _proc_dict(jmax_rms=12.0)               # the sample already has jmax_rms_k
    with pytest.raises(ValueError, match="not both"):
        Process(d)
    path = write(tmp_path / "both.yaml", yaml.safe_dump(d))
    rep = Report()
    assert check_process(path, rep) is None
    assert any(f.level == "FAIL" and "jmax_rms" in f.message for f in rep.findings)


def test_fixed_rms_below_avg_is_warned(tmp_path):
    d = _proc_dict(jmax_rms=1.0)               # below jmax_avg 3.2
    del d["layers"]["Metal1"]["jmax_rms_k"]
    rep = Report()
    check_process(write(tmp_path / "low.yaml", yaml.safe_dump(d)), rep)
    assert any(f.level == "WARN" and "can never bind" in f.message for f in rep.findings)


def test_import_ict_replaces_a_fixed_rms_with_the_icts_form():
    from emir.pdk_import import merge
    emir = {"layers": {"Metal1": {"gds": [101, 0], "sheet_res": 0.1, "jmax_rms": 6.0}},
            "vias": {}}
    ict = {"process": "x", "temp_reference": 25.0, "em_tref": 100.0, "jmax_factor": {},
           "conductors": {"metal1_conn": {"jmax_avg": 2.0, "jmax_rms_k": 30.0,
                                          "jmax_peak": 9.0}},
           "vias": {}}
    out, log = merge(emir, ict)
    m1 = out["layers"]["Metal1"]
    assert "jmax_rms" not in m1 and m1["jmax_rms_k"] == 30.0
    assert any("jmax_rms" in line and "(ICT)" in line for line in log)


# --------------------------------------------------------------- citations

_TAG_DEF = re.compile(r"^#\s+\[([A-Z]+)\]\s+(\S+)")
_ROOT_VAR = re.compile(r"relative to \$([A-Z0-9_]+)")
_ENTRY = re.compile(r"^\s*([\w.-]+):\s*(\S.*?)\s+#\s*(.*)$")
_CITE = re.compile(r"\[([A-Z]+)\](?::([\d,\- ]+))?")
_NUM = re.compile(r"(?<![\w.])[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def _lines(spec):
    out = []
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b or a) + 1))
    return out


def _holds(value, key, line):
    nums = [float(x) for x in _NUM.findall(line)]
    try:
        v = yaml.safe_load(value)
    except yaml.YAMLError:
        v = value
    if isinstance(v, bool) or v is None:
        return True
    if isinstance(v, (int, float)):
        return any(abs(n - v) <= 1e-9 * max(abs(v), 1.0) for n in nums)
    if isinstance(v, list):
        return all(any(abs(n - x) <= 1e-9 for n in nums) for x in v)
    # a name map (dspf_layers): the cited line holds the extractor's token
    return re.search(rf"(?<![\w]){re.escape(key)}(?![\w])", line) is not None


@pytest.mark.parametrize("path", REAL, ids=_ids(REAL))
def test_citations_point_at_lines_that_hold_the_value(path):
    """Each `key: value  # [TAG]:line` names a PDK file line that contains the
    value. Needs the PDK installed (its root in the variable the file's header
    names); skipped otherwise. PDF and URL sources are not machine-checked."""
    text = open(path, encoding="utf-8").read().splitlines()
    tags, var = {}, None
    for line in text:
        m = _TAG_DEF.match(line)
        if m:
            tags[m.group(1)] = m.group(2)
        m = _ROOT_VAR.search(line)
        var = var or (m.group(1) if m else None)
    assert var and tags, "header must name its root variable and its sources"
    root = os.environ.get(var)
    if not root or not os.path.isdir(root):
        pytest.skip(f"${var} not set: the PDK files are not on this machine")
    cache, checked, bad = {}, 0, []
    for n, line in enumerate(text, 1):
        m = _ENTRY.match(line)
        if not m or m.group(1) in ("pdk",):
            continue
        key, value, comment = m.groups()
        c = _CITE.search(comment)
        if not c or not c.group(2):
            continue
        src = tags.get(c.group(1))
        assert src, f"{os.path.basename(path)}:{n}: unknown source tag [{c.group(1)}]"
        if src.startswith("http") or src.endswith(".pdf"):
            continue
        if src not in cache:
            with open(os.path.join(root, src), encoding="utf-8", errors="replace") as f:
                cache[src] = f.read().splitlines()
        for ln in _lines(c.group(2)):
            checked += 1
            if not _holds(value, key, cache[src][ln - 1]):
                bad.append(f"{os.path.basename(path)}:{n} {key}: {value} not in "
                           f"[{c.group(1)}]:{ln}: {cache[src][ln - 1].strip()}")
    assert not bad, "\n".join(bad)
    assert checked >= 20


def test_readme_covers_every_file():
    text = open(os.path.join(PDKS, "README.md"), encoding="utf-8").read()
    for name in _ids(FILES):
        assert name in text
    assert "Apache-2.0" in text and "import-ict" in text
