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
    07 and a GDS-only route (the maintainer: everyone has an extractor; on an
    open PDK it is magic); cc-grid, its guard and the chip-level hierarchy
    (example 05) went in 1.1: no blog file names them."""
    for name in ("linkedin-article.md", "linkedin-post.md", "carousel-script.md", "README.md"):
        text = read(name)
        for gone in ("emir mesh", "emir layout", "04_gds_spine", "07_sky130_strap",
                     "No extractor", "straight from GDS",
                     "the GDS itself", "or a GDS", "GDS only", "GDS alone",
                     "cc-grid", "emir abstract", "05_chip_level", "C-only"):
            assert gone not in text, f"blog/{name} names {gone!r}"


# ----------------------------------------------------------------- the carousel


def test_the_carousel_script_has_every_page():
    pages = carousel_script()
    assert sorted(pages) == list(range(1, 6))
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
    import emir.schemas
    from emir import waveform

    ex02 = _json("02_xyce/expected/summary.json")["runs"]["rcc"]
    ex08 = _json("08_open_layout/expected/results.json")
    counts = [f"{len(emir.cli.COMMANDS)} commands"] + code_count()["linkedin-post.md"]
    article = counts[:1] + [
        f"{len(emir.schemas.SCHEMAS)} JSON schemas",
        *code_count()["linkedin-article.md"],
        f"{ex02['worst_items']['Rc3:peak']:.3f}x its limit",
        f"{ex02['worst_items']['Rm1:avg']:.3f}x on their average",
        f"{ex02['ir_drop_mV']:.2f} mV on average currents",
        f"{ex02['dynamic_mV']:.1f} mV at the instant",
        f"{ex08['extraction']['resistors']} resistors",
        f"{ex08['runs']['rcc']['nets']['out']['max_ratio']:.2f}x: "
        f"{ex08['runs']['rcc']['nets']['out']['worst']['fix'].split(' (')[0]}",
        screen_fix(),
        f"last two periods differ by more than {waveform.TOL * 100:.0f} %",
    ]
    cards = os.path.join(ROOT, "agent", "evals", "cards")
    if os.path.isdir(cards):
        n = len([c for c in os.listdir(cards) if c.endswith(".yaml")])
        article.append({13: "Thirteen", 14: "Fourteen", 15: "Fifteen", 16: "Sixteen",
                        17: "Seventeen"}
                       .get(n, str(n)) + " task cards")
    return {"linkedin-article.md": article, "linkedin-post.md": counts}


def screen_fix() -> str:
    """The sample fix the article quotes: the worst item of example 06's mesh
    screened with invented limits (run.sh), as emir words it from the golden
    ratio on the mesh's 2.000 um strap."""
    from emir.layoutview import fix_hint
    gold = _json("06_supply_mesh/expected/screen_n10.json")
    want = {c["path"]: c for c in gold["checks"]}
    assert want["nets.0.worst_items.0.name"]["equals"] == "Rv0_0"
    ratio = want["nets.0.worst_items.0.ratio"]["value"]
    fix = fix_hint({"ratio": ratio, "kind": "wire", "layer": "Metal1", "w": 2.0})
    return fix.split(" (")[0]


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
        word = {15: "fifteen", 16: "sixteen", 17: "seventeen", 18: "eighteen"}.get(n, str(n))
        want += [f"| {word} task cards |", f"the {n} eval cards under `agent/`"]
    missing = [q for q in want if norm(q) not in text]
    assert not missing, f"blog/README.md does not say (or no longer matches): {missing}"


def test_the_alt_texts_and_the_carousel_quote_the_results():
    """The numbers typed into alt texts and the carousel script's Alt text and
    Visual lines, written from their sources: example 06's golden ratios (each
    average current is the ratio x 2.0 um x 1.0 mA/um) and example 08's golden
    IR drops. The other tests read the prose and skip image lines."""
    from emir.layoutview import fix_hint
    gold = {c["path"]: c for c in _json("06_supply_mesh/expected/screen_n10.json")["checks"]}
    r = [gold[f"nets.0.worst_items.{k}.ratio"]["value"] for k in range(4)]
    widen = [fix_hint({"ratio": x, "kind": "wire", "layer": "Metal1", "w": 2.0})
             .split(" (")[0].split(" -> ")[1].removesuffix(" um") for x in r[:2]]
    amps = [f"{x * 2.0 * 1.0:.3f}" for x in r[:2]]
    ex08 = _json("08_open_layout/expected/results.json")["runs"]["rcc"]["nets"]
    ir = [f"{ex08[n]['ir_drop_mV']:.2f} mV" for n in ("out", "vss")]
    ratio08 = f"{ex08['out']['max_ratio']:.2f}"
    alts = {rel: alt for alt, rel in IMAGE.findall(read("linkedin-article.md"))}
    pages = carousel_script()
    want = {
        "article alt, carousel_02": (alts["images/carousel_02.png"],
                                     [f"{x:.2f}x" for x in r] + [f"{amps[0]} mA"]),
        "page 1 Alt text": (pages[1]["Alt text"], [f"{x:.2f}x" for x in r[:2]] + widen),
        "page 1 Visual": (pages[1]["Visual"], [f"{x:.2f}×" for x in r[:2]] + widen),
        "page 2 Alt text": (pages[2]["Alt text"], [f"{x:.2f}x" for x in r] + [f"{amps[0]} mA"]),
        "page 2 Visual": (pages[2]["Visual"], [f"{x:.2f}×" for x in r] + [" + ".join(amps)]),
        "page 4 Alt text": (pages[4]["Alt text"], ir + [f"{ratio08}x"]),
        "page 4 Visual": (pages[4]["Visual"], [ir[0], ir[1].replace(" mV", " mV on vss"),
                                               f"{ratio08}×"]),
    }
    bad = {k: [q for q in qs if q not in text] for k, (text, qs) in want.items()}
    bad = {k: v for k, v in bad.items() if v}
    assert not bad, f"not quoted (or no longer matching their source): {bad}"
