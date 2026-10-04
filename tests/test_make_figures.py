"""scripts/make_figures.py: the committed figures are there, at the sizes the
docs and the blog need, the manifest is current, the charts draw the numbers
the example tables publish, and no rendered transcript shows a local path."""
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
    assert 8 <= len(pages) <= 10
    for rel in pages:
        assert size(rel) == (1080, 1350), rel
    with open(os.path.join(ROOT, "blog", "carousel.pdf"), "rb") as f:
        pdf = f.read()
    assert len(re.findall(rb"/Type\s*/Page(?![s\w])", pdf)) == len(pages)


def test_the_manifest_is_current(mf, capsys):
    assert mf.main(["--check"]) == 0, capsys.readouterr().err


def test_the_accuracy_figure_draws_the_published_numbers(mf, tmp_path):
    """The ranges the figure draws are the ones examples/06_big_block/README.md
    tabulates from the same CSV (governing branch, N = 10 to 70)."""
    d = mf.accuracy_data(mf.Runs(str(tmp_path)).get("bench06"))
    rng = {k: (round(min(d[k]["gov"]), 2), round(max(d[k]["gov"]), 2))
           for k in ("avg", "rms", "peak", "ir", "dyn")}
    assert rng == {"avg": (-0.15, -0.12), "rms": (6.83, 13.68), "peak": (15.28, 24.29),
                   "ir": (-0.15, -0.13), "dyn": (14.49, 16.66)}
    assert tuple(round(v, 2) for v in d["peak"]["all"]) == (-18.95, 37.75)
    assert d["_n"] == (10, 70)
    assert round(d["_k256"]["droop"], 1) == 4.1


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_data_charts_and_the_flow_draw(mf, tmp_path, theme):
    import matplotlib.pyplot as plt
    bench = mf.Runs(str(tmp_path)).get("bench06")
    with plt.rc_context(mf.rc(theme)):
        for draw in (lambda: mf.draw_scaling(bench, theme), lambda: mf.draw_streaming(bench, theme),
                     lambda: mf.draw_accuracy(bench, theme), lambda: mf.draw_flow(theme)):
            fig = draw()
            w, h = fig.get_size_inches() * mf.DPI
            assert round(w) == 1600 and h >= 800
            plt.close(fig)


def test_a_transcript_shows_no_local_path(mf, tmp_path):
    """The scripted transcript: no local path, long lines shortened with an
    ellipsis, and the report's fix on a line of its own (a narrow carousel
    page once cut it off after "fix: Cont: 1")."""
    work = str(tmp_path / "agent")
    agent = {
        "dir": work,
        "plan_args": ["next", "--dspf", f"{work}/a.dspf", "--process", f"{work}/p.yaml"],
        "plan": ("mode: cc-grid -- why\nsteps:\n  1. emir selftest\n       why\n"
                 f"  2. emir run {work}/a.dspf --process {work}/p.yaml --summary-out "
                 f"{work}/s.json\nadvice:\n  - x\n"),
        "log": [{"args": ["selftest"], "stdout": "SELFTEST PASSED\n", "stderr": "", "code": 0},
                {"args": ["run", f"{work}/a.dspf", "--summary-out", f"{work}/s.json"],
                 "stdout": f"summary : {work}/s.json\noverall verdict: FAIL -- 1 net(s): "
                           f"1 FAIL. Screening, not sign-off.\n", "stderr": "", "code": 0}],
        "report": ("# EMIR screening report: a\n**Verdict: FAIL** -- 1 net(s): 1 FAIL\n"
                   "**Screening, not sign-off.**\n"
                   "| VDD | Rc3 | (60, 0) | Cont | via | peak | 1.042x | Cont: 1 -> 2 cuts (+1) |\n"
                   "- VDD: 28.068 mV (from the feed)\n"),
    }
    lines = mf.transcript_lines(agent, 60)
    text = "\n".join(s for s, _ in lines)
    assert work not in text and str(tmp_path) not in text
    assert "a.dspf" in text and "\u2026" in text
    assert "  fix: Cont: 1 -> 2 cuts (+1)" in [s for s, _ in lines]
    assert "IR drop VDD: 28.068 mV (from the feed)" in text
    assert all(len(s) <= 60 for s, _ in lines)


def test_the_self_consistency_figure_draws_the_published_numbers(mf):
    """The passes the figure draws are the ones examples/06_big_block/README.md
    and docs/big-blocks.md quote from results/selfconsist.csv."""
    cases = mf.selfconsist_data()
    k256 = cases[("k256", 70)]
    assert k256["converged_at"] == 2
    assert round(mf.num(k256["result"], "avg_gov") * 100, 2) == -2.02
    assert round(mf.num(k256["open"], "avg_gov") * 100, 2) == 1.34
    assert round(mf.num(k256["result"], "rms_gov") * 100, 2) == 28.50
    assert round(mf.num(k256["result"], "peak_gov") * 100, 2) == 58.57
    k32 = [c for c in cases.values() if c["case"] == "k32"]
    assert sorted(c["N"] for c in k32) == [10, 20, 40, 70]
    assert {c["converged_at"] for c in cases.values() if c["case"] != "k256"} == {1}
    settled = [mf.num(c["result"], "avg_gov") * 100 for c in k32]
    assert (round(min(settled), 2), round(max(settled), 2)) == (-0.64, -0.40)


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_self_consistency_figure_draws(mf, theme):
    import matplotlib.pyplot as plt
    with plt.rc_context(mf.rc(theme)):
        fig = mf.draw_selfconsist(theme)
        w, h = fig.get_size_inches() * mf.DPI
        assert round(w) == 1600 and h >= 800
        plt.close(fig)


#: the carousel's drawing: carousel_pages and the pages it calls out to
CAROUSEL = ("carousel_pages", "carousel_cover", "carousel_checks", "carousel_loop")


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
    assert len(maps) == 2 and all((kw(c, "size") or 0) >= 26 for c in maps)
    legends = calls("carousel_pages", "em_legend")
    assert len(legends) >= 2 and all((kw(c, "size") or 0) >= 24 for c in legends)
    cover = calls("draw_cover", "screen_map")
    assert cover and all((kw(c, "size") or 0) >= 20 for c in cover)
    assert calls("draw_cover", "em_legend")


@pytest.fixture(scope="module")
def screen(mf, tmp_path_factory):
    """examples/06_big_block's mesh screened as the figures screen it (Xyce,
    four simulations of a 132-node mesh: seconds)."""
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
    examples/06_big_block/run.sh step 4 checks (expected/screen_n10.json): the
    same four failures, and Kirchhoff at the feed holds (page 2 adds it up)."""
    import json
    gold = json.load(open(os.path.join(ROOT, "examples", "06_big_block", "expected",
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
    assert a["Rv0_0"] + a["Rh0_0"] + a["Rc0"] == pytest.approx(screen["supply_A"], rel=1e-6)
    assert screen["sims"] <= want["nets.0.self_consistency.n_passes"]["max"]


def test_the_carousel_pages_draw(mf, screen, tmp_path):
    """All ten pages draw, at the carousel's size; the figures that drew the
    removed examples 04 and 07 are drawn again (no committed file kept)."""
    import matplotlib.pyplot as plt
    assert not hasattr(mf, "KEPT")
    for name in ("hero", "cover", "carousel"):
        assert "screen06" in mf.FIGURES[name]["needs"], name
    runs = mf.Runs(str(tmp_path))
    runs._done["screen06"] = screen
    runs._done["agent02"] = None            # page 3 then draws without the transcript
    with plt.rc_context(mf.rc("dark")):
        pages = mf.carousel_pages(runs, "dark")
    assert list(pages) == list(range(1, 11))
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


def test_the_waveform_loop_figure_draws_the_published_numbers(mf):
    """The passes the waveform figure draws are the ones docs/big-blocks.md and
    examples/06_big_block/README.md quote from results/selfconsist_waveform.csv;
    the replaced loop's figure (selfconsist.png) stays, as its record."""
    cases = mf.waveform_data()
    hd = cases["hd16b"]
    assert hd["stopped"] == "settled" and hd["reported"]["pass"] == "7"
    assert round(mf.num(hd["reported"], "rms_gov") * 100, 2) == 0.34
    assert round(mf.num(hd["reported"], "peak_gov") * 100, 2) == 0.78
    assert round(mf.num(hd["open"], "peak_gov") * 100, 2) == 69.78
    # the docs quote what the figure draws
    for rel in ("docs/big-blocks.md", "examples/06_big_block/README.md"):
        text = open(os.path.join(ROOT, rel), encoding="utf-8").read()
        assert "+0.34 " in text and "+0.78 " in text, rel
    assert cases["hd16b_plain"]["stopped"] == "residual grew twice"
    assert [c["N"] for c in sorted(cases.values(), key=lambda c: c["nodes"])
            if c["case"].startswith("k32")] == [10, 20, 40]
    outs = mf.FIGURES["selfconsist_waveform"]["outputs"]
    assert "docs/images/selfconsist_waveform.png" in outs
    assert "docs/images/selfconsist.png" in mf.FIGURES["selfconsist"]["outputs"]


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_waveform_loop_figure_draws(mf, theme):
    import matplotlib.pyplot as plt
    with plt.rc_context(mf.rc(theme)):
        fig = mf.draw_selfconsist_waveform(theme)
        w, h = fig.get_size_inches() * mf.DPI
        assert round(w) == 1600 and h >= 800
        plt.close(fig)


#: the figures drawn from the committed tables alone (no run): name -> how to
#: draw it from the bench (None: it needs nothing)
TABLE_FIGURES = {
    "cc_vs_rcc_scaling": "draw_scaling", "streaming_memory": "draw_streaming",
    "accuracy": "draw_accuracy", "selfconsist": None, "selfconsist_waveform": None,
}


def test_the_table_figures_redraw_as_committed(mf, tmp_path):
    """docs/images/selfconsist_waveform.png (and its dark twin) were redrawn
    with another matplotlib than every other figure: the text laid out
    differently (a subtitle wrapped elsewhere), 136 000 pixels apart from what
    the pinned toolchain draws, and `--check` (the manifest and the files'
    presence) passed. Redrawn here with the pinned matplotlib, every figure
    drawn from the committed tables is the committed file, pixel for pixel."""
    import matplotlib
    import numpy as np
    from PIL import Image
    if matplotlib.__version__ != mf.MATPLOTLIB:
        pytest.skip(f"matplotlib {matplotlib.__version__}: the figures are drawn with "
                    f"{mf.MATPLOTLIB}")
    bench = mf.Runs(str(tmp_path / "runs")).get("bench06")
    out = str(tmp_path / "img")
    for name, fn in TABLE_FIGURES.items():
        draw = getattr(mf, f"draw_{name}") if fn is None else \
            (lambda f: (lambda th: f(bench, th)))(getattr(mf, fn))
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
