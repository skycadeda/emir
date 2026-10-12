"""The charts: regression tests, one per case.

* the HTML net page's worst-node label stays inside the map;
* a chart names its mode;
* the chart's "Not checked" line is the run's `not_checked` (a wire with no
  width is not checked there either, and not counted as a violation), and a
  layout run's below-the-model note reaches it;
* the chart applies the layer's width loss, as the run does;
* above MAX_SHAPES the IR panel keeps the elements where the drop is worst;
* hand-entered limits' notes are drawn first, in the warn colour;
* the figures that show results carry the screening and research lines.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re

import pytest
from _helpers import CUR, DSPF, PROC, ROOT, write

from emir import cli


def run(*args, rc=0):
    with contextlib.redirect_stdout(io.StringIO()) as o, \
            contextlib.redirect_stderr(io.StringIO()) as e:
        code = cli.main([str(x) for x in args] + ["--format", "json"])
    assert code == rc, (code, o.getvalue()[-1500:], e.getvalue()[-1500:])
    return json.loads(o.getvalue())


# ------------------------------------------------------------------ the net page


def _ring(html_path):
    page = open(html_path, encoding="utf-8").read()
    vb = [float(v) for v in re.search(r'<svg id="net" viewBox="([^"]+)"', page).group(1).split()]
    m = re.search(r'<g class="ring">.*?<text x="([-\d.]+)" y="([-\d.]+)" '
                  r'text-anchor="(\w+)" font-size="([\d.]+)">([^<]+)</text>', page)
    assert m, "no worst-node label"
    x, y, anchor, fs, text = float(m[1]), float(m[2]), m[3], float(m[4]), m[5]
    return vb, x, y, anchor, fs, text


def test_the_worst_node_label_stays_inside_the_map(tmp_path):
    """The sample rail's worst node is its far end, at the view box's right
    edge: 15-17 % of the label showed ("wor")."""
    from emir.netreport import CHAR_EM
    run("run", DSPF, "--process", PROC, "--currents", CUR, "--net", "VDD", "--html",
        tmp_path / "html")
    page = next(p for p in os.listdir(tmp_path / "html") if p.endswith("_VDD.html"))
    (x0, ytop, w, h), x, y, anchor, fs, text = _ring(tmp_path / "html" / page)
    est = CHAR_EM * fs * len(text)
    left = {"start": x, "end": x - est, "middle": x - est / 2}[anchor]
    assert x0 <= left and left + est <= x0 + w, (x0, w, left, est)
    assert ytop <= y - fs and y <= ytop + h
    assert fs < 0.02 * max(w, h)


@pytest.mark.parametrize("px", [0.0, 30.0, 60.0])
def test_ring_label_fits_wherever_the_node_is(px):
    from emir.netreport import CHAR_EM, ring_label
    box = (-3.0, -3.0, 63.0, 3.0)
    x, y, anchor, fs = ring_label((px, 0.0), 1.2, "worst node 54.0 mV", box, 66.0)
    est = CHAR_EM * fs * 18
    left = {"start": x, "end": x - est, "middle": x - est / 2}[anchor]
    assert box[0] <= left and left + est <= box[2]
    assert -box[3] <= y - fs and y <= -box[1]


# ------------------------------------------------------------------ the banner


@pytest.fixture(scope="module")
def mf():
    pytest.importorskip("matplotlib")
    pytest.importorskip("PIL")
    spec = importlib.util.spec_from_file_location(
        "make_figures", os.path.join(ROOT, "scripts", "make_figures.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_figures_that_show_results_say_screening_and_research():
    src = open(os.path.join(ROOT, "scripts", "make_figures.py"), encoding="utf-8").read()
    for name in ("draw_flow", "draw_transcript"):
        m = re.search(rf"def {name}\(.*?(?=\ndef )", src, re.S)
        assert m, name
        foot = m.group(0)[m.group(0).rfind("footer(ax"):]
        assert "SCREENING" in foot and "RESEARCH_LINE" in foot, name
    # the carousel's result pages say screening too
    pages = re.findall(r"page_footer\(ax, fig, theme, (.*?)\)\n", src, re.S)
    off = [p for p in pages if "screening=False" in p]
    assert all("licences" in p or "Every number" in p for p in off), off


# ------------------------------------------------------------------ the run chart


def test_a_wire_with_no_width_is_not_checked_on_the_chart_as_in_the_report(tmp_path):
    pytest.importorskip("matplotlib")
    from emir import scene
    text = open(DSPF).read()
    text = re.sub(r"^(Rm10 .*?) \$W=\S+", r"\1", text, flags=re.M)
    text = re.sub(r"^(Rm10 .*?) \$SI_W=\S+", r"\1", text, flags=re.M)
    d = write(tmp_path / "nan.dspf", text)
    doc = run("run", d, "--process", PROC, "--currents", CUR, "--net", "VDD", "--force",
              "--html", tmp_path / "html", "--summary-out", tmp_path / "s.json")
    assert doc["nets"][0]["no_width"] == ["Rm10"]
    from emir.dspf import analyse, build_parser
    a = build_parser().parse_args([d, "--process", PROC, "--currents", CUR, "--net", "VDD",
                                   "--force"])
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        res = analyse(a)
    sc = scene.run_scene(res.solved, res.dspf, res.proc)
    rm10 = next(s for s in sc["shapes"] if s["name"] == "Rm10")
    assert rm10["ratio"] is None                       # not checked, as the report says
    assert "1 wire(s) with no width: Rm10" in sc["unchecked"]
    fails = sum(1 for c in res.solved["VDD"]["clusters"] if c["severity"] == "FAIL")
    assert len(sc["markers"]) == fails


def test_the_not_checked_line_is_the_runs():
    from emir.scene import not_checked_text
    nc = {"limits_missing": [{"name": "Metal1", "kind": "wire", "metrics": ["peak"]},
                             {"name": "via", "kind": "via", "metrics": ["rms", "peak"]}],
          "vias_assumed_one_cut": {"VDD": ["R4"]}, "avg_not_derated": {"temp_C": 125.0}}
    t = not_checked_text(nc)
    assert "peak on Metal1, via" in t and "rms on via" in t
    assert "one cut" in t and "125 C" in t


def test_the_chart_applies_the_width_loss(tmp_path):
    """The chart drew 0.748x (4 mA / 0.75 um / 7.13) where the run said
    1.020x (4 / (0.75 - 0.2) / 7.13), in the 'ok' blue."""
    from emir import scene
    from emir.dspf import analyse, build_parser
    text = open(PROC).read()
    p = write(tmp_path / "dw.yaml", re.sub(r"(\n  Metal1:\n)", r"\1    width_loss_um: 0.2\n",
                                           text, count=1))
    a = build_parser().parse_args([DSPF, "--process", p, "--currents", CUR, "--net", "VDD"])
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        res = analyse(a)
    r = res.solved["VDD"]
    shapes = {s["name"]: s for s in scene.segments(r, res.dspf, "VDD", res.proc)}
    for c in r["clusters"]:
        assert shapes[c["name"]]["ratio"] == pytest.approx(c["ratio"], rel=1e-12), c["name"]
    worst = r["clusters"][0]
    if worst["kind"] == "wire" and worst["ratio"] >= 1:
        assert "dW 0.2" in shapes[worst["name"]]["fix"]


def test_above_the_cap_the_ir_panel_keeps_the_worst_drop(tmp_path, monkeypatch):
    from emir import scene
    from emir.dspf import analyse, build_parser
    a = build_parser().parse_args([DSPF, "--process", PROC, "--currents", CUR, "--net", "VDD"])
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        res = analyse(a)
    full = scene.run_scene(res.solved, res.dspf, res.proc)
    worst_ir = max(full["shapes"], key=lambda s: s["ir"] or 0)["name"]
    worst_em = max(full["shapes"], key=lambda s: s["ratio"] or 0)["name"]
    monkeypatch.setattr(scene, "MAX_SHAPES", 4)
    cut = scene.run_scene(res.solved, res.dspf, res.proc)
    names = {s["name"] for s in cut["shapes"]}
    assert len(names) == 4 and worst_ir in names and worst_em in names
    assert any("worst by IR drop" in n for n in cut["notes"])


def test_hand_entered_limits_are_drawn_first_in_the_warn_colour(tmp_path, monkeypatch):
    pytest.importorskip("matplotlib")
    from emir import chart, scene
    sc = {"verdict": "FAIL", "counts": {}, "markers": [], "items": [], "nets": [],
          "unchecked": "", "notes": ["a note"],
          "alerts": ["NO SOURCE (EMIR-W320): metal1 jmax_avg", "Limits entered by hand ..."],
          "warn": 0.8, "kind": "shapes", "shapes": []}
    calls = []
    page = chart.Page(800, 600, "dark")
    real = page.para

    def para(x, y, s, px, width, color, *a, **k):
        calls.append((s, color))
        return real(x, y, s, px, width, color, *a, **k)
    monkeypatch.setattr(page, "para", para)
    chart.side_panel(page, 10, 10, 300, 40, sc, ("em",))
    texts = [s for s, _ in calls]
    assert texts.index("NO SOURCE (EMIR-W320): metal1 jmax_avg") < len(texts)
    assert dict(calls)["NO SOURCE (EMIR-W320): metal1 jmax_avg"] == page.t["warn_ink"]
    # the bottom is reached at once: the generic note is dropped, the alerts never
    assert "a note" not in texts and "Limits entered by hand ..." in texts
    assert scene.limits_notes({"entries": [], "suspects": [
        {"path": "em_tref", "value": 383.15, "unit": "C", "confirmed": False}]})[0] \
        .startswith("SUSPECT UNIT")


# ------------------------------------------------------------------ the modes


def test_a_chart_says_its_mode():
    """A chart's subtitle names the mode of its nets: rcc, or dc."""
    from emir.scene import mode_status
    assert mode_status({"VDD": {"mode": "rcc"}}) == ("rcc", [])
    assert mode_status({"VDD": {"mode": "rcc"}, "VSS": {"mode": "dc"}})[0] == "dc, rcc"


# ------------------------------------------------------------------ cosmetics


def test_the_viewer_bars_label_their_edges_with_units():
    """The IR bar labelled its bins 0.00..48.60 with no unit and no top
    value; the EM bar's first bin read 0.00x."""
    from emir.layoutview import bar_labels, em_bands
    em, ir = bar_labels(em_bands(0.8), 54.0, 10)
    assert em[0] == "0x" and em[1] == "0.01x" and em[-1] == "2.5x"
    assert ir[0] == "0" and ir[1] == "5.40" and ir[-1] == "54.0 mV" and len(ir) == 11
    assert bar_labels(em_bands(0.8), 0.0, 10)[1][-1] == "0 mV"
    assert bar_labels(em_bands(0.8), 1e-7, 10)[1][-1] == "~0 mV"


def test_colour_bar_takes_one_label_per_edge(tmp_path):
    from emir.layoutview import colour_bar
    p = colour_bar(str(tmp_path / "b.png"), list(range(10)),
                   ["0"] + [f"{k}" for k in range(1, 10)] + ["54.0 mV"])
    assert os.path.getsize(p) > 100


def test_equal_ratios_are_numbered_left_to_right():
    from emir.scene import items_of, markers_of
    shapes = [{"net": "VDD", "name": n, "layer": "Metal1", "kind": "wire",
               "box": [x, 0, x + 5, 0.75], "ratio": 3.3333333333333 + eps, "fix": ""}
              for n, x, eps in (("Rm3", 10, 2e-15), ("Rm1", 0, 0.0), ("Rm2", 5, -1e-15))]
    assert [i["name"] for i in items_of(shapes, 0.8)] == ["Rm1", "Rm2", "Rm3"]
    assert [m["box"][0] for m in markers_of(shapes)] == [0, 5, 10]


def test_no_ring_on_a_drop_that_prints_as_zero(tmp_path):
    """Example 08's sink labelled 'vb ~0 mV' among the three worst drops."""
    pytest.importorskip("matplotlib")
    from emir import chart
    calls = []

    class Ax:
        def plot(self, *a, **k):
            calls.append("ring")

        def annotate(self, label, *a, **k):
            calls.append(label)

    class Placer:
        def inside(self, x, y):
            return True

        def place(self, *a):
            return (0, 0)
    scene = {"nets": [{"net": "vb", "ir": 2e-10, "ir_xy": [0, 0]},
                      {"net": "VDD", "ir": 0.0078, "ir_xy": [1, 1]}]}
    chart._rings(Ax(), Placer(), scene, 14)
    labels = [c for c in calls if c != "ring"]
    assert labels == ["VDD 7.80 mV"] and calls.count("ring") == 2


def test_the_net_page_keeps_its_map_at_phone_width(tmp_path):
    run("run", DSPF, "--process", PROC, "--currents", CUR, "--net", "VDD", "--html",
        tmp_path / "h")
    page = open(next((tmp_path / "h").glob("*_VDD.html")), encoding="utf-8").read()
    assert "@media (max-width:640px)" in page and "#net{min-height:200px}" in page
    assert ".zbar{position:static" in page
