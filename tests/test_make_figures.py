"""scripts/make_figures.py: the committed figures are there, at the sizes the
docs and the blog need, the manifest is current, the screen the figures draw
holds its golden values, and no rendered transcript shows a local path."""
from __future__ import annotations

import importlib.util
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "make_figures.py")


@pytest.fixture(scope="module")
def mf():
    pytest.importorskip("matplotlib")
    pytest.importorskip("PIL")
    spec = importlib.util.spec_from_file_location("make_figures", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _outputs(mf):
    return [o for f in mf.FIGURES.values() for o in f["outputs"]]


def test_every_figure_is_committed_and_small(mf):
    outs = _outputs(mf)
    assert len(outs) == len(set(outs)), "two figures write the same file"
    for rel in outs:
        path = os.path.join(ROOT, rel)
        assert os.path.isfile(path), f"{rel} is missing: run scripts/make_figures.py"
        size = os.path.getsize(path)
        assert size <= 1024 * 1024, f"{rel}: {size} B, over the repository's 1 MB limit"
        if rel.endswith(".png"):
            assert size <= mf.PNG_TARGET, f"{rel}: {size} B, over the {mf.PNG_TARGET} B target"


def test_the_sizes_the_blog_and_the_readme_need(mf):
    from PIL import Image

    def size(rel):
        with Image.open(os.path.join(ROOT, rel)) as im:
            return im.size

    assert size("docs/images/hero.png") == (1600, 800)
    assert size("docs/images/hero_light.png") == (1600, 800)
    assert size("blog/images/cover_1200x627.png") == (1200, 627)
    assert size("blog/images/cover_article_1920x1080.png") == (1920, 1080)   # 16:9
    pages = sorted(o for o in _outputs(mf) if re.search(r"carousel_\d+\.png$", o))
    assert len(pages) == 5
    for rel in pages:
        assert size(rel) == (1080, 1350), rel
    with open(os.path.join(ROOT, "blog", "carousel.pdf"), "rb") as f:
        pdf = f.read()
    assert len(re.findall(rb"/Type\s*/Page(?![s\w])", pdf)) == len(pages)


def test_the_manifest_is_current(mf, capsys):
    assert mf.main(["--check"]) == 0, capsys.readouterr().err


def test_the_optimiser_never_recolours_a_legend_swatch(mf, tmp_path):
    """A busy chart (too big as RGB, so the optimiser tries a palette) with a
    small amber warn swatch: example 08's chart came out of the median cut
    with that swatch red, beside the words "warn: 0.8x to 1x"."""
    import numpy as np
    from PIL import Image
    rng = np.random.default_rng(0)
    # 600 elements, 40 x 20 px each, shaded along the blue ramp by their ratio
    lo, hi = np.array((0x10, 0x42, 0x81), float), np.array((0xb7, 0xd3, 0xf6), float)
    shades = (lo + (hi - lo) * rng.random(600)[:, None]).round()
    a = np.repeat(np.repeat(shades.reshape(30, 20, 3), 20, axis=0), 40, axis=1)
    a = np.clip(a + rng.integers(-2, 3, a.shape), 0, 255).astype(np.uint8)
    amber = (0xfa, 0xb2, 0x19)
    a[300:312, 400:418] = amber                       # 18 x 12 px, as the legend draws it
    path = str(tmp_path / "chart.png")
    Image.fromarray(a).save(path)
    mf.optimise_png(path)
    with Image.open(path) as im:
        got = np.asarray(im.convert("RGB"))
    assert (got[300:312, 400:418] == amber).all(), got[305, 405]
    # and the check itself: a palette that moves a colour filling an area is
    # refused, one that moves only a colour of a few edge pixels is not
    a[0, 0] = (1, 2, 3)
    rgb = Image.fromarray(a)
    red = a.copy()
    red[300:312, 400:418] = (0xd0, 0x3b, 0x3b)
    assert not mf.palette_keeps_colours(rgb, Image.fromarray(red))
    edge = a.copy()
    edge[0, 0] = (40, 40, 40)
    assert mf.palette_keeps_colours(rgb, Image.fromarray(edge))


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_flow_draws(mf, theme):
    import matplotlib.pyplot as plt
    with plt.rc_context(mf.rc(theme)):
        fig = mf.draw_flow(theme)
        w, h = fig.get_size_inches() * mf.DPI
        assert round(w) == 1600 and h >= 800
        texts = " ".join(t.get_text() for ax in fig.axes for t in ax.texts)
        plt.close(fig)
    assert "rcc" in texts and "cc-grid" not in texts and "C-only" not in texts


def test_a_transcript_shows_no_local_path(mf, tmp_path):
    """The scripted transcript: no local path, long lines shortened with an
    ellipsis, and the report's fix on a line of its own (a narrow carousel
    page once cut it off after "fix: Cont: 1")."""
    work = str(tmp_path / "agent")
    agent = {
        "dir": work,
        "plan_args": ["next", "--dspf", f"{work}/a.dspf", "--process", f"{work}/p.yaml"],
        "plan": ("mode: rcc -- why\nsteps:\n  1. emir selftest\n       why\n"
                 f"  2. emir run {work}/a.dspf --process {work}/p.yaml --summary-out "
                 f"{work}/s.json\nadvice:\n  - x\n"),
        "log": [{"args": ["selftest"], "stdout": "SELFTEST PASSED\n", "stderr": "", "code": 0},
                {"args": ["run", f"{work}/a.dspf", "--summary-out", f"{work}/s.json"],
                 "stdout": f"summary : {work}/s.json\noverall verdict: FAIL -- 1 net(s): "
                           f"1 FAIL. Screening, not sign-off.\n", "stderr": "", "code": 0}],
        "report": ("# EMIR screening report: a\n**Verdict: FAIL** -- 1 net(s): 1 FAIL\n"
                   "**Screening, not sign-off.**\n## Nets\n"
                   "| VDD | FAIL | rcc | peak | 1.042x | 0.9x | 1.042x | 28 mV drop |\n"
                   "## Worst items\n"
                   "| VDD | Rc3 | (60, 0) | Cont | via | peak | 1.042x | Cont: 1 -> 2 cuts (+1) |\n"
                   "- VDD: 28.068 mV (from the feed)\n## Not checked\n"
                   "Every item the run found was checked against a limit.\n"),
    }
    lines = mf.transcript_lines(agent, 50)
    text = "\n".join(s for s, _ in lines)
    assert work not in text and str(tmp_path) not in text
    assert "a.dspf" in text and "\u2026" in text
    assert "  fix: Cont: 1 -> 2 cuts (+1)" in [s for s, _ in lines]
    assert "  worst: Rc3 (Cont via) peak 1.042x" in [s for s, _ in lines]
    assert [s for s, _ in lines][-1] == "  IR drop VDD: 28.068 mV (from the feed)"
    assert "mode: rcc" in text
    assert all(len(s) <= 50 for s, _ in lines)


#: the carousel's drawing: carousel_pages and the pages it calls out to
CAROUSEL = ("carousel_pages", "carousel_cover", "carousel_checks", "carousel_sky130",
            "carousel_site")


def test_the_linkedin_images_are_legible_at_feed_size(mf):
    """The carousel is read about 390 px wide in the feed (0.36 of its 1080
    px) and the 1200 px cover about 552 px wide (0.46): the maps' callouts
    were once 13 to 17 px, 5 to 6 px on a phone. Every map on a carousel page
    draws its words at 26 px or more (its legend 24), the cover's at 20 or
    more, and pages 1 and 2 and the covers name every colour."""
    import ast
    tree = ast.parse(open(SCRIPT, encoding="utf-8").read())
    funcs = {f.name: f for f in ast.walk(tree) if isinstance(f, ast.FunctionDef)}

    def calls(fn, name):
        fns = CAROUSEL if fn == "carousel_pages" else (fn,)
        return [c for f in fns for c in ast.walk(funcs[f]) if isinstance(c, ast.Call)
                and getattr(c.func, "id", None) == name]

    def kw(c, k):
        v = next((x.value for x in c.keywords if x.arg == k), None)
        return v.value if isinstance(v, ast.Constant) else None
    maps = calls("carousel_pages", "screen_map")
    assert len(maps) == 3 and all((kw(c, "size") or 0) >= 26 for c in maps)
    legends = calls("carousel_pages", "em_legend")
    assert len(legends) >= 2 and all((kw(c, "size") or 0) >= 24 for c in legends)
    cover = calls("draw_cover", "screen_map")
    assert cover and all((kw(c, "size") or 0) >= 20 for c in cover)
    assert calls("draw_cover", "em_legend")


@pytest.fixture(scope="module")
def screen(mf, tmp_path_factory):
    """examples/06_supply_mesh's mesh screened as the figures screen it (Xyce,
    one simulation of a 132-node mesh, then rcc: seconds)."""
    import shutil
    if not shutil.which("Xyce"):
        pytest.skip("Xyce is not on PATH: the screened mesh needs it")
    work = str(tmp_path_factory.mktemp("figures"))
    old = os.environ.get("EMIR_STATE_DIR")
    os.environ["EMIR_STATE_DIR"] = os.path.join(work, "state")
    try:
        return mf.Runs(work).get("screen06")
    finally:
        if old is None:
            os.environ.pop("EMIR_STATE_DIR", None)
        else:
            os.environ["EMIR_STATE_DIR"] = old


def test_the_screen_holds_its_golden_values(mf, screen):
    """The run the banner, the covers and pages 1 and 2 draw is the one
    examples/06_supply_mesh/run.sh checks (expected/screen_n10.json): the same
    four failures, and Kirchhoff at the feed holds (page 2 adds it up; the
    mesh's capacitance moves the averages by its charge over the window, a few
    parts per million)."""
    import json
    gold = json.load(open(os.path.join(ROOT, "examples", "06_supply_mesh", "expected",
                                       "screen_n10.json"), encoding="utf-8"))
    want = {c["path"]: c for c in gold["checks"]}
    items = screen["items"]
    fails = [it for it in items if it["level"] == "fail"]
    assert len(fails) == want["nets.0.n_violations"]["equals"] == 4
    for k, it in enumerate(fails):
        assert it["name"] == want[f"nets.0.worst_items.{k}.name"]["equals"]
        assert it["ratio"] == pytest.approx(want[f"nets.0.worst_items.{k}.ratio"]["value"],
                                            rel=want[f"nets.0.worst_items.{k}.ratio"]["rel"])
    a = screen["avg_A"]
    assert a["Rv0_0"] + a["Rh0_0"] + a["Rc0"] == pytest.approx(screen["supply_A"], rel=1e-5)
    assert want["nets.0.metrics"]["equals"] == ["avg", "rms", "peak"]


def test_the_carousel_pages_draw(mf, screen, tmp_path):
    """All five pages draw, at the carousel's size; the figures that drew the
    removed examples 04 and 07 are drawn again (no committed file kept)."""
    import matplotlib.pyplot as plt
    assert not hasattr(mf, "KEPT")
    for name in ("hero", "cover", "carousel"):
        assert "screen06" in mf.FIGURES[name]["needs"], name
    assert "sink08" in mf.FIGURES["carousel"]["needs"]
    runs = mf.Runs(str(tmp_path))
    runs._done["screen06"] = screen
    runs._done["agent02"] = None            # page 3 then draws without the transcript
    runs._done["sink08"] = None             # and page 4 without example 08's map
    with plt.rc_context(mf.rc("dark")):
        pages = mf.carousel_pages(runs, "dark")
    assert list(pages) == list(range(1, 6))
    for fig in pages.values():
        w, h = fig.get_size_inches() * mf.DPI
        assert (round(w), round(h)) == (1080, 1350)
        plt.close(fig)


@pytest.mark.parametrize("which,n", [("hero", 2), ("cover", 2), ("carousel 1", 2),
                                     ("carousel 2", 4)])
def test_no_callout_covers_another_callout_s_leader(mf, screen, which, n):
    """Each numbered callout's leader reaches its item in the clear, and no
    two callouts overlap: on a page enlarged for the feed a box once lay over
    another's leader, which then seemed to leave that box."""
    import matplotlib.pyplot as plt

    from emir import chart
    draw = {"hero": lambda t: mf.draw_hero(screen, t),
            "cover": lambda t: mf.draw_cover(screen, t),
            "carousel 1": lambda t: mf.carousel_cover(screen, t),
            "carousel 2": lambda t: mf.carousel_checks(screen, t)}[which]
    for theme in ("dark", "light"):
        with plt.rc_context(mf.rc(theme)):
            fig = draw(theme)
            found = [t.get_gid() for ax in fig.axes for t in ax.texts]
            assert found.count(chart.CALLOUT_GID) == n
            assert chart.callout_crossings(fig) == []
            plt.close(fig)


@pytest.mark.parametrize("which,least", [("cover", 18), ("carousel 1", 24)])
def test_the_disclaimers_are_legible_at_feed_size(mf, screen, which, least):
    """Read in the feed (a carousel page about 390 px wide, the cover about
    552 px), a 19 px footer and a 15 px line are 5 to 7 px: the page with a
    map says "Screening, not sign-off." at 24 px or more, and the cover says
    it, and that EMIR is a research project, at 18 px or more."""
    import matplotlib.pyplot as plt
    draw = {"cover": lambda: mf.draw_cover(screen, "dark"),
            "carousel 1": lambda: mf.carousel_cover(screen, "dark")}[which]
    with plt.rc_context(mf.rc("dark")):
        fig = draw()
        texts = [(t.get_text(), t.get_fontsize() / mf.fs(1)) for ax in fig.axes
                 for t in ax.texts]
        plt.close(fig)
    assert any(mf.SCREENING in s and px >= least - 1e-6 for s, px in texts), texts
    if which == "cover":
        research = [(s, px) for s, px in texts if s and s in mf.RESEARCH_LINE]
        assert " ".join(s for s, _ in research) == mf.RESEARCH_LINE
        assert all(px >= least - 1e-6 for _, px in research)


#: the figures drawn from nothing but the script (no run): name -> how to draw it
TABLE_FIGURES = {"flow": None}


def test_the_table_figures_redraw_as_committed(mf, tmp_path):
    """A figure redrawn with another matplotlib than every other figure (once,
    the self-consistent loop's figure and its dark twin): the text laid out
    differently (a subtitle wrapped elsewhere), 136 000 pixels apart from what
    the pinned toolchain draws, and `--check` (the manifest and the files'
    presence) passed. Redrawn here with the pinned matplotlib, every figure
    drawn from the script alone is the committed file, pixel for pixel."""
    import matplotlib
    import numpy as np
    from PIL import Image
    if matplotlib.__version__ != mf.MATPLOTLIB:
        pytest.skip(f"matplotlib {matplotlib.__version__}: the figures are drawn with "
                    f"{mf.MATPLOTLIB}")
    out = str(tmp_path / "img")
    for name, fn in TABLE_FIGURES.items():
        draw = getattr(mf, f"draw_{name}") if fn is None else getattr(mf, fn)
        mf.save_variants(draw, name, out=out)
        for sfx in ("", "_dark"):
            with Image.open(os.path.join(out, f"{name}{sfx}.png")) as a, \
                    Image.open(os.path.join(ROOT, "docs", "images", f"{name}{sfx}.png")) as b:
                x, y = np.asarray(a.convert("RGBA")), np.asarray(b.convert("RGBA"))
            assert x.shape == y.shape, f"{name}{sfx}.png: {x.shape} against {y.shape}"
            n = int((x != y).any(axis=-1).sum())
            assert n == 0, (f"docs/images/{name}{sfx}.png: {n} pixels differ from a redraw "
                            f"with matplotlib {mf.MATPLOTLIB}: run scripts/make_figures.py "
                            f"--only {name} with it")


def test_drawing_with_another_matplotlib_is_refused(mf, monkeypatch, tmp_path, capsys):
    """Nothing is drawn with a matplotlib other than the pinned one, unless
    asked for a draft (--any-matplotlib); --check still runs."""
    monkeypatch.setattr(mf.matplotlib, "__version__", "0.0.1")
    drawn = []
    monkeypatch.setitem(mf.FIGURES["flow"], "fn", lambda runs: drawn.append(1))
    assert mf.main(["--only", "flow", "--work", str(tmp_path)]) == 2
    err = capsys.readouterr().err
    assert f"matplotlib=={mf.MATPLOTLIB}" in err and "--any-matplotlib" in err
    assert not drawn
    assert mf.main(["--check"]) == 0
