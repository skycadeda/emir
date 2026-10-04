"""blog/: the LinkedIn article, the feed post and the carousel script.

They are published by hand, so they must stay true to the repository: every image
they name exists and carries alt text, the post fits LinkedIn, the carousel script
says what the PDF's pages say, and every number the
article and the post quote is the one the committed results hold. (Names that
must stay out of them are the leak guard's job.)
"""
from __future__ import annotations

import csv
import json
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BLOG = os.path.join(ROOT, "blog")
EX = os.path.join(ROOT, "examples")
ARTICLE = os.path.join(BLOG, "linkedin-article.md")

pytestmark = pytest.mark.skipif(not os.path.isfile(ARTICLE),
                                reason="blog/ is not part of this tree")

IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
FIELD = re.compile(r"^- \*\*([A-Za-z ]+):\*\* (.*)$", re.M)
SOURCES = "## Where the numbers come from"


def read(name: str) -> str:
    with open(os.path.join(BLOG, name), encoding="utf-8") as f:
        return f.read()


def prose(md: str) -> str:
    """The words a reader reads: no image lines, no code blocks, no HTML
    comments (the maintainer's notes to self)."""
    out, code = [], False
    md = re.sub(r"<!--.*?-->", "", md, flags=re.S)
    for line in md.splitlines():
        if line.startswith("```"):
            code = not code
        elif not code and not line.startswith("!["):
            out.append(line)
    return "\n".join(out)


def post_text() -> str:
    m = re.search(r"<!-- post:start -->\n```text\n(.*?)\n```\n<!-- post:end -->",
                  read("linkedin-post.md"), re.S)
    assert m, "linkedin-post.md: the post goes in a text block between the post markers"
    return m.group(1)


def carousel_script() -> dict[int, dict[str, str]]:
    parts = re.split(r"^## Page (\d+): .*$", read("carousel-script.md"), flags=re.M)[1:]
    return {int(n): dict(FIELD.findall(body)) for n, body in zip(parts[::2], parts[1::2])}


def norm(s: str) -> str:
    return " ".join(s.split())


# ----------------------------------------------------------------- images and form


def test_the_article_images_exist_with_alt_text_and_a_caption():
    lines = read("linkedin-article.md").splitlines()
    found = [(i, IMAGE.fullmatch(s.strip())) for i, s in enumerate(lines)]
    found = [(i, m) for i, m in found if m]
    assert len(found) >= 6
    assert found[0][0] < 5, "the header image comes first"
    for i, m in found:
        alt, rel = m.groups()
        assert os.path.isfile(os.path.normpath(os.path.join(BLOG, rel))), rel
        assert 40 <= len(alt) <= 300, f"{rel}: alt text of {len(alt)} characters"
        cap = lines[i + 2] if i + 2 < len(lines) else ""
        assert lines[i + 1] == "" and len(cap) > 2 and cap[0] == cap[-1] == "*", \
            f"{rel}: no italic caption under it"


def test_the_header_image_is_16_by_9():
    # Pillow comes with the `report` extra (matplotlib): CI's core job has none
    Image = pytest.importorskip("PIL.Image")
    rel = IMAGE.search(read("linkedin-article.md")).group(2)
    with Image.open(os.path.join(BLOG, rel)) as im:
        w, h = im.size
    assert w * 9 == h * 16 and w >= 1200, (w, h)


def test_the_readme_says_where_each_image_goes_with_the_same_alt_text():
    readme = read("README.md").splitlines()
    for alt, rel in IMAGE.findall(read("linkedin-article.md")):
        path = os.path.relpath(os.path.normpath(os.path.join(BLOG, rel)), ROOT)
        row = next((s for s in readme if f"`{path}`" in s), None)
        assert row, f"README.md does not say where {path} goes"
        assert alt in row, f"README.md: the alt text of {path} is not the article's"


def test_the_article_length_and_its_screening_line():
    parts = prose(read("linkedin-article.md")).split(SOURCES)
    assert len(parts) == 2, "the article ends with the sources of its numbers"
    words = len(parts[0].split())
    assert 1200 <= words <= 1800, f"{words} words; the brief is about 1200 to 1800"
    assert "Screening, not sign-off." in parts[0]


def test_the_post_fits_linkedin():
    post = post_text()
    assert len(post) <= 3000, f"{len(post)} characters"
    paras = post.split("\n\n")
    hook = "\n\n".join(paras[:2])
    assert len(hook) <= 210, f"the hook is {len(hook)} characters, past the fold"
    tags = re.findall(r"(?<!\S)#\w+", post)
    assert 3 <= len(tags) <= 5 and paras[-1].split() == tags, "3 to 5 hashtags, last"
    assert "**" not in post and "`" not in post, "LinkedIn shows Markdown as it is"
    assert "Screening, not sign-off" in post
    # blog/README.md quotes the first sentence's length (it said 135; it is 128)
    first = re.match(r"(.+?[.!?])(?:\s|$)", paras[0], re.S).group(1)
    quoted = re.search(r"first sentence \((\d+) characters\)", read("README.md"))
    assert quoted and int(quoted.group(1)) == len(first), (len(first), first)


def test_the_blog_names_no_removed_route():
    """`emir mesh` and `emir layout` are removed, and with them examples 04 and
    07 and a GDS-only route: no blog file names them (the maintainer: everyone
    has an extractor; on an open PDK it is magic)."""
    for name in ("linkedin-article.md", "linkedin-post.md", "carousel-script.md", "README.md"):
        text = read(name)
        for gone in ("emir mesh", "emir layout", "04_gds_spine", "07_sky130_strap",
                     "No extractor", "straight from GDS",
                     "the GDS itself", "or a GDS", "GDS only", "GDS alone"):
            assert gone not in text, f"blog/{name} names {gone!r}"


# ----------------------------------------------------------------- the carousel


def test_the_carousel_script_has_every_page():
    pages = carousel_script()
    assert sorted(pages) == list(range(1, 11))
    for n, f in pages.items():
        assert os.path.isfile(os.path.join(BLOG, "images", f"carousel_{n:02d}.png"))
        missing = {"Kicker", "Title", "Text", "Footer", "Alt text", "Numbers"} - set(f)
        assert not missing, f"page {n}: no {sorted(missing)}"
        assert len(f["Alt text"]) <= 250, f"page {n}: alt text too long"


@pytest.mark.skipif(shutil.which("pdftotext") is None, reason="pdftotext is not installed")
def test_the_carousel_script_says_what_the_pdf_says():
    pdf = os.path.join(BLOG, "carousel.pdf")
    for n, f in carousel_script().items():
        page = norm(subprocess.run(["pdftotext", "-f", str(n), "-l", str(n), pdf, "-"],
                                   capture_output=True, text=True, check=True).stdout)
        for key in ("Kicker", "Title", "Text", "Body", "Footer"):
            if key in f:
                assert norm(f[key]) in page, f"page {n}: its {key} is not on the PDF page"


# ----------------------------------------------------------------- the numbers


def _csv(rel):
    with open(os.path.join(EX, rel), encoding="utf-8") as f:
        return list(csv.DictReader(s for s in f if not s.startswith("#")))


def _num(row):
    """A CSV row with its numbers as floats (text and empty cells kept)."""
    out = {}
    for k, v in row.items():
        try:
            out[k] = float(v)
        except ValueError:
            out[k] = v
    return out


def _json(rel):
    with open(os.path.join(EX, rel), encoding="utf-8") as f:
        return json.load(f)


def quoted() -> dict[str, list[str]]:
    """Every number the article and the post quote, written from its source."""
    import emir.cli
    import emir.errors
    import emir.schemas

    rows = _csv("06_big_block/results/sweep.csv")
    k32 = {int(r["N"]): _num(r) for r in rows if r["case"] == "k32"}
    k256 = next(_num(r) for r in rows if r["case"] == "k256")
    acc = [k32[n] for n in (10, 20, 40, 70)]

    def rng(key):
        v = [100 * r[key] for r in acc]
        return min(v), max(v)

    low = [-100 * r[k] for r in acc for k in ("avg_gov", "ir_err")]   # both read low
    rms, peak = rng("rms_gov"), rng("peak_gov")
    big = next(r for r in _csv("06_big_block/results/stream_bench.csv")
               if r["case"] == "s300_k2000_t501")
    diff = max(float(big[k]) for k in ("rel_diff_avg", "rel_diff_rms", "rel_diff_peak"))
    cc_route = k32[200]["cc_sim_s"] + k32[200]["cc_post_s"]
    vdd = 1.0   # the sweep's rail, examples/06_big_block/mesh.py VDD

    chip = "05_chip_level/expected/"
    top, adc = _json(chip + "top.summary.json")["values"], _json(chip + "adc.summary.json")["values"]
    meas = _json(chip + "measured.json")["values"]
    pll = _json(chip + "pll.summary.json")["values"]
    ann = _json(chip + "pll.annotated.summary.json")["values"]

    counts = [f"{len(emir.cli.COMMANDS)} commands"] + code_count()["linkedin-post.md"]
    sweep_common = [
        f"{k32[10]['rcc_sim_s']:.2f} s to {k32[200]['rcc_sim_s']:.0f} s",
        f"{cc_route:.2f} s",
        f"{int(k32[10]['grid_nodes'])} to {int(k32[200]['grid_nodes']):,}",
    ]
    stream_common = [f"{int(big['nodes']):,}-node", f"{int(big['taps']):,} taps",
                     f"{float(big['dense_matrix_GiB']):.2f} GiB",
                     f"{float(big['auto_wall_s']):.1f} s", f"{float(big['auto_peak_rss_MB']):.0f} MiB"]
    article = counts[:1] + sweep_common + stream_common + [
        f"{len(emir.schemas.SCHEMAS)} JSON schemas",
        *code_count()["linkedin-article.md"],
        f"{int(k32[10]['devices'])} inverters",
        f"{k32[10]['rcc_raw_MB']:.2f} MiB to {k32[200]['rcc_raw_MB']:,.0f} MiB",
        f"{k32[10]['cc_raw_MB']:.2f} MiB at every size",
        f"{k32[10]['speedup_route']:.1f}x as long as the CC route at {int(k32[10]['grid_nodes'])}",
        f"{k32[40]['speedup_route']:.1f}x at {int(k32[40]['grid_nodes']):,}",
        f"{k32[70]['speedup_route']:.1f}x at {int(k32[70]['grid_nodes']):,}",
        f"at {int(k32[200]['grid_nodes']):,} nodes the RCC simulation alone took "
        f"{k32[200]['speedup_sim_vs_cc_route']:.1f}x",
        f"avg and IR {min(low):.2f} to {max(low):.2f} % low",
        # rounded in the prose: rms and peak together, the safe direction
        f"rms and peak {min(rms[0], peak[0]):.0f} to {max(rms[1], peak[1]):.0f} % high",
        f"peak down to {-100 * min(r['peak_minor_min'] for r in acc):.0f} % below RCC",
        {8: "eight"}[round(k256["devices"] / k32[70]["devices"])] + " times the devices",
        f"({100 * k256['ir_rcc_mV'] / 1e3 / vdd:.1f} % droop), peak read "
        f"{100 * k256['peak_gov']:.0f} % high",
        f"{float(big['dense_wall_s']):.1f} s and {float(big['dense_peak_rss_MB']):,.0f} MiB",
        f"agree to {diff:.0e} of the largest",
        f"{top['hierarchy.nets.0.pins.0.V_avg_mV'] * -1:.2f} + "
        f"{adc['nets.0.internal.ir_drop_mV']:.2f} = {meas['budget_mV.XADC.total']:.2f} mV",
        f"gets {meas['budget_mV.XPLL.total']:.2f} mV, an upper bound on the flat "
        f"{meas['flat_worst_mV.XPLL']:.2f} mV",
        f"read {pll['nets.0.internal.max_ratio.all']:.3f}x its limit "
        f"({pll['nets.0.internal.verdict']}) with ideal pins and "
        f"{ann['nets.0.back_annotation.max_ratio.all']:.3f}x "
        f"({ann['nets.0.back_annotation.verdict']}) back-annotated",
        screen_fix(),
    ]
    cards = os.path.join(ROOT, "agent", "evals", "cards")
    if os.path.isdir(cards):
        n = len([c for c in os.listdir(cards) if c.endswith(".yaml")])
        article.append({13: "Thirteen", 14: "Fourteen", 15: "Fifteen", 16: "Sixteen",
                        17: "Seventeen"}
                       .get(n, str(n)) + " task cards")
    sc = selfconsist()
    article += sc["article"] + decap()
    post = counts + sweep_common + stream_common + sc["common"] + [
        f"within {max(low):.2f} % of RCC",
    ]
    return {"linkedin-article.md": article, "linkedin-post.md": post}


def screen_fix() -> str:
    """The sample fix the article quotes: the worst item of example 06's mesh
    screened with invented limits (run.sh step 4), as emir words it from the
    golden ratio on the mesh's 2.000 um strap."""
    from emir.layoutview import fix_hint
    gold = _json("06_big_block/expected/screen_n10.json")
    want = {c["path"]: c for c in gold["checks"]}
    assert want["nets.0.worst_items.0.name"]["equals"] == "Rv0_0"
    ratio = want["nets.0.worst_items.0.ratio"]["value"]
    fix = fix_hint({"ratio": ratio, "kind": "wire", "layer": "Metal1", "w": 2.0})
    return fix.split(" (")[0]


def selfconsist() -> dict[str, list[str]]:
    """The self-consistent loop's numbers, as the article and the post quote
    them: the synthetic meshes (examples/06_big_block/results/
    selfconsist_waveform.csv, net VDD: the split testbench's pass 0, the open
    loop, against the pass the run reports; the 32-inverter meshes and the
    128-inverter one at 4.1 % droop, as the waveform figure draws them) and the
    resistive block of example 12."""
    cases = {}
    for r in _csv("06_big_block/results/selfconsist_waveform.csv"):
        if r["net"] == "VDD" and r["deck"] != "cc.cir":
            cases.setdefault(r["case"], []).append(_num(r))
    report, first, last, sims = {}, {}, {}, {}
    for k, passes in cases.items():
        passes.sort(key=lambda r: r["pass"])
        first[k], last[k], sims[k] = passes[0], passes[-1], len(passes)   # pass 0 simulates
        report[k] = next(r for r in passes if r["pass"] == passes[-1]["reported_pass"])
    k32 = ("k32_n10", "k32_n20", "k32_n40")                # 132, 432 and 1,632 nodes
    hd = "hd16b"
    word = {4: "four", 5: "five", 8: "eight"}
    pct = [100 * first[k]["peak_gov"] for k in k32]
    end = max(abs(100 * report[k]["peak_gov"]) for k in k32)
    droop = [first[k]["droop_pct"] for k in k32]
    n = sorted({sims[k] for k in k32})
    assert len(n) == 2, "the 32-inverter meshes no longer settle in two counts of simulations"

    def minor(k, nc=""):
        r = report[k]
        return max(abs(100 * r[f"peak_minor_min{nc}"]), abs(100 * r[f"peak_minor_max{nc}"]))

    h0, hs = first[hd], report[hd]
    swing = f"{100 * h0['peak_gov']:+.1f} % to {100 * hs['peak_gov']:+.2f} %"
    ex12 = _json("12_self_consistent/expected/summary.json")["runs"]
    return {
        "common": [f"{h0['droop_pct']:.1f} % droop",
                   f"peak from {swing} of RCC in {word[sims[hd]]} simulations",
                   f"{int(hs['devices'])}-inverter mesh at {h0['droop_pct']:.1f} % droop"],
        "article": [
            f"the IR drop went from {ex12['open']['ir_drop_mV']:.3f} mV open loop to "
            f"{ex12['sc']['ir_drop_mV']:.3f} mV, against {ex12['rcc']['ir_drop_mV']:.3f} mV "
            "from RCC",
            f"peak from {min(pct):.0f} to {max(pct):.0f} % high to within {end:.1f} % of RCC "
            f"in {word[n[0]]} or {word[n[1]]} simulations ({min(droop):.2f} to "
            f"{max(droop):.2f} % droop)",
            # page 7's 4.1 % case (sweep.csv k256) has eight times the devices: the
            # loop closed on a smaller mesh at the same droop, and the text says so
            f"on a smaller mesh at the same {h0['droop_pct']:.1f} % droop "
            f"({int(hs['devices'])} inverters, {int(hs['grid_nodes'])} nodes), from {swing} "
            f"in {word[sims[hd]]}",
            f"within {minor(k32[0]):.2f} % of RCC on {int(report[k32[0]]['grid_nodes'])} "
            f"nodes, up to {minor(k32[-1]):.1f} % off on {int(report[k32[-1]]['grid_nodes']):,}, "
            f"and within {max(minor(k, '_nc') for k in k32):.1f} % of RCC without the grid's "
            "capacitors",
            f"({last[hd]['total_s']:.1f} s against a {hs['rcc_sim_s']:.1f} s RCC simulation at "
            f"{h0['droop_pct']:.1f} % droop)",
        ],
    }


def decap() -> list[str]:
    """Decap-aware EM as the article quotes it: examples/13_decap_aware/results/
    decap.csv, the governing (feed) branch's rms in the single-rail cases with
    decaps, decap-free against decap-aware, and the two-rail case's straps."""
    rows = [_num(r) for r in _csv("13_decap_aware/results/decap.csv")]
    one = [r for r in rows if r["two_rail"] == "False" and r["decaps"] > 0]
    two = [r for r in rows if r["two_rail"] == "True"]
    assert one and len(two) == 1
    assert all(r["free_rms_strap_max"] == -1.0 for r in one), "decap-free saw a strap's current"
    free = [100 * r["free_rms_gov"] for r in one]
    aware = [100 * r["aware_rms_gov"] for r in one]
    return [f"rms {min(free):.0f} to {max(free):.0f} % high and missed the decaps' straps",
            f"decap-aware read it {min(aware):.1f} to {max(aware):.1f} % high",
            f"(straps {-100 * two[0]['vdd_aware_rms_strap_min']:.2f} % low)"]


def code_count() -> dict[str, list[str]]:
    """How the article and the post quote the registry's code count."""
    import emir.errors
    n = len(emir.errors.REGISTRY)
    return {"linkedin-article.md": [f"one of {n} stable codes (errors, warnings and notes)"],
            "linkedin-post.md": [f"{n} stable codes (errors, warnings and notes)"]}


@pytest.mark.parametrize("name", ["linkedin-article.md", "linkedin-post.md"])
def test_every_quoted_number_is_the_one_the_results_hold(name):
    text = norm(prose(read(name)) if name.endswith("article.md") else post_text())
    missing = [q for q in quoted()[name] if norm(q) not in text]
    assert not missing, f"blog/{name} does not say (or no longer matches): {missing}"


@pytest.mark.parametrize("name", ["linkedin-article.md", "linkedin-post.md"])
def test_the_blog_quotes_the_registrys_code_count(name):
    text = norm(prose(read(name)) if name.endswith("article.md") else post_text())
    missing = [q for q in code_count()[name] if norm(q) not in text]
    assert not missing, f"blog/{name} does not say (or no longer matches): {missing}"


def test_the_readme_quotes_the_packages_counts():
    """blog/README.md's checklist and its table of where the article's numbers
    come from said 307 codes, sixteen cards and "the 16 eval cards" after the
    article and the post (which these tests tie to the code) moved on."""
    import emir.cli
    import emir.errors
    import emir.schemas
    text = norm(read("README.md"))
    want = [f"{len(emir.cli.COMMANDS)} commands, {len(emir.schemas.names())} JSON schemas, "
            f"{len(emir.errors.REGISTRY)} stable codes (errors, warnings, notes)"]
    cards = os.path.join(ROOT, "agent", "evals", "cards")
    if os.path.isdir(cards):
        n = len([c for c in os.listdir(cards) if c.endswith(".yaml")])
        word = {16: "sixteen", 17: "seventeen", 18: "eighteen"}.get(n, str(n))
        want += [f"| {word} task cards |", f"the {n} eval cards under `agent/`"]
    missing = [q for q in want if norm(q) not in text]
    assert not missing, f"blog/README.md does not say (or no longer matches): {missing}"
