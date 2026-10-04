"""Stage-1 correctness fixes on the DSPF side: layer tokens, `$lvl` mapping,
and via cut counts (the process `vcwidth`, and vias with no count at all)."""
import json

import pytest
import yaml
from _helpers import CUR, DSPF, PROC, proc_with, rewrite_vias, sample_text, write

from emir.check import check_inputs
from emir.dspf import _layer_token, build_parser, parse, run, via_cuts
from emir.engine import Process


def _run(dspf, proc, tmp_path, *extra):
    a = build_parser().parse_args(
        [dspf, "--net", "VDD", "--process", proc, "--currents", CUR,
         "--json", str(tmp_path / "summary.json"), *extra])
    return run(a)["VDD"]


def _ratio(res, name):
    return next(c["ratio"] for c in res["clusters"] if c["name"] == name)


# ------------------------------------------------------------ layer tokens


@pytest.mark.parametrize("tail,layer", [
    ("$metal1 $L=5 $W=0.75", "metal1"),
    ("$LLX=1 $metal1", "metal1"),             # was 'LL': the regex backtracked
    ("$layer=M1 $W=0.75", "M1"),              # was 'laye'
    ("$lyr=metal2 $W=0.75", "metal2"),
    ("$metal1 $layer=metal2", "metal2"),      # an explicit key wins
    ("$L = 0.5 $metal1", "metal1"),           # spaced '=' is still a key
    ("$SI_W=0.75 $VC=2", None),               # was 'SI_' / 'V'
    ("$lvl=2 $w=0.75", None),                 # was 'lv'
])
def test_layer_token(tail, layer):
    assert _layer_token(tail.split()) == layer


def test_layer_given_as_a_key_gives_the_sample_numbers(tmp_path):
    text = sample_text().replace("$metal1", "$layer=metal1").replace("$cont", "$layer=cont")
    d = write(tmp_path / "kv.dspf", text)
    assert parse(d).layer_tokens() == ["cont", "metal1"]
    r = _run(d, PROC, tmp_path)
    assert abs(r["max_ratio"]["all"] - 4.0 / 2.4) < 1e-9
    assert abs(r["ir_drop"] * 1e3 - 54.0) < 1e-6


def test_level_only_resistors_map_through_dspf_layers(tmp_path):
    text = sample_text().replace(" $metal1", "").replace(" $cont", "")
    d = write(tmp_path / "lvl.dspf", text)
    assert parse(d).layer_tokens() == ["lvl:0", "lvl:1"]
    # unmapped: reported with the dspf_layers hint, never silently skipped
    rep = check_inputs(d, process=PROC, nets=["VDD"], currents=CUR)
    w = [f for f in rep.findings if f.area == "layers" and f.level == "WARN"]
    assert w and "lvl:0" in w[0].message and "dspf_layers" in w[0].fix
    # mapped by number (YAML integer keys), the result is the sample's
    pd = yaml.safe_load(open(PROC))
    pd["dspf_layers"] = {1: "Metal1", 0: "Cont"}
    p = write(tmp_path / "p.yaml", yaml.safe_dump(pd))
    assert check_inputs(d, process=p, nets=["VDD"], currents=CUR).passed
    r = _run(d, p, tmp_path)
    assert abs(r["max_ratio"]["all"] - 4.0 / 2.4) < 1e-9 and len(r["clusters"]) == 10


# ------------------------------------------------------------ via cuts


def test_process_keeps_vcwidth(tmp_path):
    assert Process(yaml.safe_load(open(PROC))).vias["Cont"]["vcwidth"] == 0.13
    p = Process(yaml.safe_load(open(proc_with(tmp_path, vcwidth=None))))
    assert p.vias["Cont"]["vcwidth"] is None          # no built-in default


def test_via_cuts_rule():
    via = {"vcwidth": 0.13}
    assert via_cuts({"cuts": 3, "area": 0.0338}, via) == (3, "count")
    assert via_cuts({"cuts": None, "area": 0.0338}, via) == (2, "area")
    assert via_cuts({"cuts": None, "area": 0.0338}, {"vcwidth": None}) == (1, "assumed")
    assert via_cuts({"cuts": None, "area": None}, via) == (1, "assumed")


def test_cut_count_from_area_uses_the_process_vcwidth(tmp_path):
    """$A = 0.0338 um^2 of 0.13 um cuts is 2 cuts, so each contact carries
    0.5 mA per cut: 0.667x of the 0.75 mA/cut limit. A built-in cut size
    smaller than the process one would count more cuts and read lower."""
    d = write(tmp_path / "area.dspf", rewrite_vias(sample_text(), "$cont $A=0.033800"))
    r = _run(d, proc_with(tmp_path, vcwidth=0.13), tmp_path, "--viewer",
             str(tmp_path / "viewer"))
    viz = json.load(open(tmp_path / "viewer" / "emir_viz.json"))
    vias = {v["name"]: v for v in viz["em"] if v["kind"] == "via"}
    assert sorted(vias) == ["Rc0", "Rc1", "Rc2", "Rc3"]
    assert all(v["cuts"] == 2 and v["cuts_from"] == "area" for v in vias.values())
    assert abs(r["max_ratio"]["via"] - 0.5 / 0.75) < 1e-9
    assert not [c for c in r["clusters"] if c["kind"] == "via"]      # all below warn
    assert len(r["via_cuts"]["area"]) == 4 and not r["via_cuts"]["assumed"]


def test_area_without_vcwidth_is_one_cut_and_warned(tmp_path):
    d = write(tmp_path / "area.dspf", rewrite_vias(sample_text(), "$cont $A=0.033800"))
    p = proc_with(tmp_path, vcwidth=None)
    rep = check_inputs(d, process=p, nets=["VDD"], currents=CUR)
    w = [f for f in rep.findings if f.area == "vias" and f.level == "WARN"]
    assert w and "vcwidth" in w[0].message and "ONE cut" in w[0].message
    r = _run(d, p, tmp_path)
    assert abs(_ratio(r, "Rc0") - 1.0 / 0.75) < 1e-9         # 1 mA on one cut
    assert sorted(r["via_cuts"]["assumed"]) == ["Rc0", "Rc1", "Rc2", "Rc3"]


def test_vias_without_count_or_area_are_checked_as_one_cut(tmp_path):
    """They used to vanish from the verdict and the viewer while the checker
    said they were 'assumed ONE cut'."""
    d = write(tmp_path / "nocut.dspf", rewrite_vias(sample_text(), "$cont"))
    rep = check_inputs(d, process=PROC, nets=["VDD"], currents=CUR)
    w = [f for f in rep.findings if f.area == "vias" and f.level == "WARN"]
    assert w and "neither a cut count" in w[0].message
    r = _run(d, PROC, tmp_path, "--viewer", str(tmp_path / "viewer"))
    rc = [c for c in r["clusters"] if c["kind"] == "via"]
    assert len(rc) == 4 and all(c["cuts"] == 1 and c["cuts_from"] == "assumed" for c in rc)
    assert abs(rc[0]["ratio"] - 1.0 / 0.75) < 1e-9
    s = json.load(open(tmp_path / "summary.json"))
    assert sorted(s["nets"][0]["via_cuts"]["assumed_one_cut"]) == ["Rc0", "Rc1", "Rc2", "Rc3"]
    viz = json.load(open(tmp_path / "viewer" / "emir_viz.json"))
    vias = [v for v in viz["em"] if v["kind"] == "via"]
    assert len(vias) == 4 and all("ONE cut" in v["hint"] for v in vias)


def test_sample_vias_still_use_their_extracted_count(tmp_path):
    r = _run(DSPF, PROC, tmp_path)
    assert r["via_cuts"] == {"area": [], "assumed": []}
    rc = [c for c in r["clusters"] if c["kind"] == "via"]
    assert [(c["name"], c["cuts"], c["cuts_from"]) for c in rc] == [("Rc3", 1, "count")]
