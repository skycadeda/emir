"""EMIR's one colour scale (emir/style.py) and the renderers that draw with it:
the PNG charts (emir/chart.py: `--html`), the HTML net pages
(emir/netreport.py) and the viewers' palette (emir/layoutview.py). Screening,
not sign-off."""
import importlib.util
import json
import math
import os
import subprocess
import sys

import pytest
from _helpers import CUR, DSPF, PROC, ROOT, emir

from emir import style as S

needs_mpl = pytest.mark.skipif(importlib.util.find_spec("matplotlib") is None,
                               reason="the charts need matplotlib (the report extra)")
needs_klayout = pytest.mark.skipif(importlib.util.find_spec("klayout") is None,
                                   reason="needs the klayout package")

# ------------------------------------------------------------------ the scale


def test_the_ramp_runs_dark_to_light_and_its_endpoints_are_the_stops():
    assert S.ramp(0.0) == S.EM_RAMP[0] == "#104281"
    assert S.ramp(1.0) == S.EM_RAMP[-1] == "#b7d3f6"
    for k, c in enumerate(S.EM_RAMP):                 # every stop is hit exactly
        assert S.ramp(k / (len(S.EM_RAMP) - 1)) == c
    assert S.ramp(-3) == S.EM_RAMP[0] and S.ramp(7) == S.EM_RAMP[-1]       # clamped
    assert S.ramp(float("nan")) == S.FAIL                                   # never a shade


@pytest.mark.parametrize("warn", [0.8, 0.5])
def test_em_bands_by_ratio(warn):
    assert S.em_colour(S.EM_FLOOR, warn) == S.EM_RAMP[0]
    assert S.em_colour(1e-9, warn) == S.EM_RAMP[0]                # below the floor
    assert S.em_colour(0.0, warn) == S.EM_RAMP[0]
    assert S.em_colour(warn * 0.999999, warn) == S.ramp(S.em_t(warn * 0.999999, warn))
    assert S.em_t(warn, warn) == 1.0
    assert S.em_colour(warn, warn) == S.WARN                      # the warn band starts here
    assert S.em_colour((warn + 1) / 2, warn) == S.WARN
    assert S.em_colour(0.9999999, warn) == S.WARN
    assert S.em_colour(1.0, warn) == S.FAIL                       # 1x is a failure
    assert S.em_colour(37.0, warn) == S.FAIL


def test_the_ramp_is_logarithmic_from_the_floor_to_the_warn_band():
    warn = 0.8
    mid = 10 ** ((math.log10(S.EM_FLOOR) + math.log10(warn)) / 2)
    assert S.em_t(mid, warn) == pytest.approx(0.5)
    assert S.em_colour(mid, warn) == S.ramp(0.5)
    assert S.em_t(0.01, warn) == pytest.approx(1 / math.log10(warn / S.EM_FLOOR))


def test_not_a_number_is_the_failure_colour_and_no_limit_is_grey():
    for bad in (float("nan"), float("inf"), -float("inf")):
        assert S.em_colour(bad) == S.FAIL
        assert S.em_level(bad) == "fail"
        assert S.ir_colour(bad, 1e-3) == S.FAIL
    assert S.em_colour(None) == S.UNCHECKED == "#6b6b6b"
    assert S.em_level(None) == "unchecked"


def test_ir_runs_on_the_same_ramp():
    assert S.ir_colour(0.0, 5e-3) == S.EM_RAMP[0]
    assert S.ir_colour(5e-3, 5e-3) == S.EM_RAMP[-1]
    assert S.ir_colour(-5e-3, 5e-3) == S.EM_RAMP[-1]      # a ground's bounce is a drop too
    assert S.ir_colour(2.5e-3, 5e-3) == S.ramp(0.5)
    assert S.ir_colour(1e-3, 0.0) == S.EM_RAMP[0]          # no drop anywhere: no scale


def test_numbers_print_and_not_a_number_is_never_zero():
    assert S.num(float("nan")) == S.num(None) == S.num(float("inf")) == "not a number"
    assert S.fmt_ratio(float("nan")) == "not a number"
    assert S.fmt_mv(float("nan")) == "not a number"
    assert S.fmt_ratio(1.0) == "1.00×" and S.fmt_ratio(2.4785) == "2.48×"
    assert S.fmt_ratio(0.0145) == "0.0145×" and S.fmt_ratio(1.68e-4) == "1.68e-4×"
    assert S.fmt_ratio(9e-37) == "~0×"                    # a net with no current
    assert S.fmt_mv(7.8146e-3) == "7.81 mV" and S.num(1234.5) == "1,234"


def test_the_legend_names_every_colour():
    em = S.legend("em", 0.8, unchecked=True, lost=True, markers=True)
    colours = {c for _k, c, _t in em["keys"]}
    assert {S.WARN, S.FAIL, S.UNCHECKED, S.MARKER} <= colours
    texts = " ".join(t for _k, _c, t in em["keys"])
    assert "not a number" in texts and "no limit" in texts and "0.8×" in texts
    assert [lab for _p, lab in em["bar"]["ticks"]] == ["0.001×", "0.01×", "0.1×", "0.8×"]
    assert em["bar"]["stops"][0][1] == S.EM_RAMP[0] and em["bar"]["stops"][-1][1] == S.EM_RAMP[-1]
    ir = S.legend("ir", 0.8, ir_max=8e-3, lost=True)
    assert [lab for _p, lab in ir["bar"]["ticks"]] == ["0", "4.00 mV", "8.00 mV"]
    assert ("swatch", S.FAIL, "IR drop not a number (a failed current)") in ir["keys"]
    assert any(k == "ring" for k, _c, _t in ir["keys"])
    assert "red at 1× and over or not a number" in S.colour_key()


def test_the_style_imports_only_the_standard_library():
    """The Cadence files' generator runs without numpy (pre-commit, `-S`)."""
    code = ("import sys; sys.modules['numpy'] = None; sys.modules['matplotlib'] = None\n"
            f"sys.path.insert(0, {ROOT!r})\nfrom emir import style as S\n"
            "assert S.em_colour(0.9) == S.WARN\n")
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr


# ------------------------------------------------------- the viewers' palette


def test_viewer_bands_follow_the_scale():
    from emir.layoutview import BAND_RGB, EM_BANDS, FAIL_STEP, N_STEPS, WARN_STEP, em_bands
    steps = {lo: step for lo, _lab, step in EM_BANDS}
    assert steps[0.8] == WARN_STEP and steps[1.0] == steps[1.5] == steps[2.5] == FAIL_STEP
    below = [step for lo, _lab, step in EM_BANDS if lo < 0.8]
    assert below == sorted(below) and all(0 <= s < N_STEPS for s in below)
    assert BAND_RGB[WARN_STEP] == S.hex_rgb(S.WARN) and BAND_RGB[FAIL_STEP] == S.hex_rgb(S.FAIL)
    # a process file's own warn fraction moves the warn band
    b5 = em_bands(0.5)
    assert any(lo == 0.5 and step == WARN_STEP for lo, _l, step in b5)


def test_viewer_data_carries_the_palette(tmp_path):
    from emir.layoutview import BAND_RGB
    p = emir("run", DSPF, "--net", "VDD", "--process", PROC, "--currents", CUR, "--viewer",
             tmp_path / "v", "--lib", "L")
    assert p.returncode == 0, p.stdout + p.stderr
    viz = json.load(open(tmp_path / "v" / "emir_viz.json"))
    assert viz["palette"] == [S.rgb_hex(c) for c in BAND_RGB]
    assert viz["em_bands"][0][2] < viz["em_bands"][-1][2]


# ----------------------------------------------------------------- the HTML


def test_the_net_page_draws_on_black_with_the_scale_and_an_ir_view(tmp_path):
    p = emir("run", DSPF, "--net", "VDD", "--process", PROC, "--currents", CUR, "--html",
             tmp_path / "h")
    assert p.returncode == 0, p.stderr
    page = (tmp_path / "h" / "railblock_VDD.html").read_text(encoding="utf-8")
    svg = page.split('<svg id="net"', 1)[1].split("</svg>", 1)[0]
    assert 'data-mode="em"' in svg and 'class="mark"' in svg and 'class="ring"' in svg
    # the failing via is filled with the failure colour, its IR colour ready
    via = svg.split('class="via FAIL"', 1)[1].split(">", 1)[0]
    assert f'fill="{S.FAIL}"' in via and 'data-ir="#' in via
    assert "#net{background:#000}" in page
    assert 'data-mode="ir"' in page and 'data-for="ir" hidden' in page
    for c in (S.WARN, S.FAIL, S.MARKER):
        assert c in page


# ----------------------------------------------------------------- the PNGs


def _png_size(path):
    from PIL import Image
    with Image.open(path) as im:
        return im.size


@needs_mpl
def test_a_dspf_run_draws_its_chart_from_the_dspf_on_black(tmp_path):
    out = tmp_path / "h"
    p = emir("run", DSPF, "--process", PROC, "--currents", CUR, "--html", out,
             "--summary-out", tmp_path / "s.json")
    assert p.returncode == 0, p.stdout + p.stderr
    for name in ("emir_chart.png", "emir_chart_light.png"):
        w, h = _png_size(out / name)
        assert w == 1600 and 600 <= h <= 2080, (name, w, h)
    s = json.load(open(tmp_path / "s.json"))
    assert sorted(os.path.basename(c) for c in s["charts"]) == ["emir_chart.png",
                                                                "emir_chart_light.png"]
    index = (out / "index.html").read_text(encoding="utf-8")
    assert 'data-dark="emir_chart.png"' in index and 'data-light="emir_chart_light.png"' in index
    # the report an agent relays names the chart to attach beside it
    r = emir("report", "--from", tmp_path / "s.json", "--format", "json")
    rep = json.loads(r.stdout)
    assert [os.path.basename(c) for c in rep["charts"]] == ["emir_chart.png",
                                                            "emir_chart_light.png"]
    assert "- Chart (every net on the layout" in rep["markdown"]


def test_the_chart_scene_of_a_run(tmp_path):
    """What the chart shows: every element, the violations outlined, the
    worst numbered, one row per net, the worst IR node; no layout under it."""
    from emir.dspf import build_parser, parse, run
    from emir.engine import Process
    from emir.scene import run_scene
    a = build_parser().parse_args([DSPF, "--net", "VDD", "--process", PROC, "--currents", CUR])
    solved = run(a)
    d, proc = parse(DSPF), Process.load(PROC)
    sc = run_scene(solved, d, proc)
    r = solved["VDD"]
    assert len(sc["shapes"]) == len(r["network"].res)
    assert sc["verdict"] == "FAIL" and sc["underlay"] is None
    assert [m["rank"] for m in sc["markers"]] == list(range(1, len(sc["markers"]) + 1))
    assert len(sc["markers"]) == sum(1 for c in r["clusters"] if c["severity"] == "FAIL")
    worst = sc["items"][0]
    assert worst["rank"] == 1 and worst["ratio"] == pytest.approx(r["worst"]["ratio"])
    assert worst["fix"] and worst["level"] == "fail"
    net = sc["nets"][0]
    assert net["net"] == "VDD" and net["ir"] == pytest.approx(r["ir_drop"])
    assert net["ir_xy"] is not None


@needs_mpl
def test_a_failed_drawing_is_a_warning_not_a_failed_run(tmp_path, monkeypatch, capsys):
    from emir import chart
    from emir.dspf import build_parser, parse, run
    from emir.engine import Process
    from emir.errors import collecting
    from emir.scene import write_charts

    def boom(*a, **k):
        raise RuntimeError("no room")
    a = build_parser().parse_args([DSPF, "--net", "VDD", "--process", PROC, "--currents", CUR])
    solved = run(a)
    monkeypatch.setattr(chart, "draw", boom)
    monkeypatch.delenv("EMIR_DEBUG", raising=False)
    with collecting() as notes:
        out = write_charts(solved, parse(DSPF), Process.load(PROC), str(tmp_path))
    assert out == [] and [n["code"] for n in notes] == ["EMIR-W720"]


@needs_mpl
def test_no_layout_drawn_under_a_gds_chart_is_said(tmp_path, monkeypatch):
    from emir import underlay
    from emir.dspf import build_parser, parse, run
    from emir.engine import Process
    from emir.errors import collecting
    from emir.scene import write_charts
    a = build_parser().parse_args([DSPF, "--net", "VDD", "--process", PROC, "--currents", CUR])
    solved = run(a)
    gds = tmp_path / "x.gds"
    gds.write_bytes(b"")
    monkeypatch.setattr(underlay, "have_klayout", lambda: False)
    monkeypatch.setattr(underlay, "polygons", lambda *a: {})
    monkeypatch.setattr(underlay, "bbox", lambda *a: None)
    with collecting() as notes:
        out = write_charts(solved, parse(DSPF), Process.load(PROC), str(tmp_path), gds=str(gds))
    assert len(out) == 2 and [n["code"] for n in notes] == ["EMIR-I721"]


def test_without_matplotlib_the_html_says_why_there_is_no_chart(tmp_path):
    code = ("import sys; sys.modules['matplotlib'] = None\n"
            "from emir.cli import main\n"
            f"rc = main(['run', {DSPF!r}, '--net', 'VDD', '--process', {PROC!r},"
            f" '--currents', {CUR!r}, '--html', {str(tmp_path / 'h')!r}])\n"
            "raise SystemExit(rc)\n")
    p = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "EMIR-I720" in p.stdout + p.stderr
    assert not (tmp_path / "h" / "emir_chart.png").exists()
    assert (tmp_path / "h" / "index.html").exists()


def _example(name, tmp_path, *args):
    env = {**os.environ, "EMIR_PYTHON": sys.executable,
           "EMIR": f"{sys.executable} -m emir", "OUT": str(tmp_path)}
    p = subprocess.run(["bash", os.path.join(ROOT, "examples", name, "run.sh"), *args],
                       env=env, capture_output=True, text=True, timeout=900, cwd=ROOT)
    assert p.returncode == 0, p.stdout[-3000:] + p.stderr[-3000:]
    return p


@needs_mpl
@needs_klayout
@pytest.mark.example
@pytest.mark.magic
def test_the_layout_example_draws_its_chart_over_the_gds(tmp_path):
    p = subprocess.run(["bash", os.path.join(ROOT, "examples", "08_open_layout", "run.sh"),
                        str(tmp_path)], env={**os.environ, "EMIR_PYTHON": sys.executable},
                       capture_output=True, text=True, timeout=900)
    assert p.returncode == 0, p.stdout + p.stderr
    if "skipped" in p.stdout:
        pytest.skip(p.stdout.strip().splitlines()[-1])
    html = tmp_path / "html"
    assert os.path.getsize(html / "emir_chart.png") > 50_000
    assert os.path.getsize(html / "emir_chart_light.png") > 50_000
    s = json.load(open(tmp_path / "rcc.json"))
    assert len(s["charts"]) == 2


@needs_mpl
@needs_klayout
def test_the_underlay_is_drawn_by_klayout_dimmed_on_black(tmp_path):
    """The layout under a chart: KLayout's own drawing (a neutral grey per
    layer without the PDK's .lyp), on black."""
    import klayout.db as kdb

    from emir import underlay
    if not underlay.have_klayout():
        pytest.skip("klayout.lay is not in this klayout build")
    ly = kdb.Layout()
    top = ly.create_cell("T")
    top.shapes(ly.layer(101, 0)).insert(kdb.DBox(0, 0, 4, 1))
    top.shapes(ly.layer(102, 0)).insert(kdb.DBox(1.5, 0, 2.5, 4))
    gds = str(tmp_path / "t.gds")
    ly.write(gds)
    img, how = underlay.render(gds, "T", (-0.5, -0.5, 4.5, 4.5), 200, 200, lyp=None)
    assert how == "a neutral grey per layer" and img.shape == (200, 200, 3)
    assert img[0, 0].tolist() == [0, 0, 0]                  # the canvas is black
    assert img.max() > 60                                    # and the metal is drawn
    assert underlay.bbox(gds, "T") == pytest.approx([0, 0, 4, 4])
    assert underlay.find_lyp(None) is None and underlay.find_lyp("../x") is None


def test_find_lyp_looks_under_pdk_root(tmp_path, monkeypatch):
    from emir.underlay import find_lyp
    tech = tmp_path / "mypdkA" / "libs.tech" / "klayout" / "tech"
    tech.mkdir(parents=True)
    (tech / "mypdkA.lyp").write_text("<layer-properties/>")
    monkeypatch.setenv("PDK_ROOT", str(tmp_path))
    assert find_lyp("mypdkA (a test PDK)") == str(tech / "mypdkA.lyp")
    monkeypatch.delenv("PDK_ROOT")
    assert find_lyp("mypdkA") is None
