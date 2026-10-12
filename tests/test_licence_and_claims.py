"""The licence (MIT), the research-project disclaimer, and the claims the
repository must not make (the maintainer, 2026-10-02):

* MIT everywhere EMIR states its own licence; Apache-2.0 only for the open
  PDKs' data and third-party packages;
* "EMIR is a research project, provided as is, with no warranty or guarantee
  of accuracy ..." where the project is introduced and where results are
  presented (README, docs, AGENTS.md, the skill, HTML footers, the run
  summary, the report, the console, the MCP server, the blog once);
* no comparison with SAF's results, and one neutral attribution of the code
  `emir/magic.py` derives from it; every module derived from Skyforge credits
  it, and THIRD_PARTY_NOTICES.md names each;
* no claim that a licensed tool (Spectre, HSPICE, Virtuoso, PVS, Quantus,
  OCEAN) was verified anywhere: support is proven on the machine that uses it.
"""
import json
import os
import re
import subprocess

import pytest
from _helpers import CUR, DSPF, PROC, ROOT, emir

import emir as emir_pkg

DISCLAIMER = emir_pkg.RESEARCH_DISCLAIMER


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def tracked_text_files():
    p = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=ROOT,
                       capture_output=True, text=True)
    if p.returncode != 0:
        pytest.skip("not a git checkout")
    out = []
    for rel in p.stdout.splitlines():
        if rel.startswith((".venv", "emir_out")) or "/out/" in rel or not os.path.isfile(
                os.path.join(ROOT, rel)):
            continue
        if rel.endswith((".png", ".pdf", ".gds", ".oas", ".zip", ".gz", ".pyc")):
            continue
        try:
            out.append((rel, read(rel)))
        except UnicodeDecodeError:
            continue
    return out


def flat(s):
    """Whitespace collapsed, Markdown quote marks and bold left out."""
    return " ".join(s.replace("**", "").replace("> ", " ").split())


# ------------------------------------------------------------ the licence


def test_the_licence_is_mit():
    text = read("LICENSE")
    assert text.startswith("MIT License\n\nCopyright (c) 2026 Vijay Gupta and contributors\n")
    assert "Permission is hereby granted, free of charge" in text
    assert 'THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND' in text
    assert re.search(r'^license = "MIT"$', read("pyproject.toml"), re.M)
    assert re.search(r"^license: MIT$", read("CITATION.cff"), re.M)
    assert "MIT License" in read("NOTICE") and "trademark" in read("NOTICE")
    assert "MIT License" in read("README.md") and "MIT License" in read(
        "THIRD_PARTY_NOTICES.md")
    assert "licence (MIT)" in flat(read("CONTRIBUTING.md"))


#: where "Apache" may appear: the open PDKs' data and third-party packages
#: (CHANGELOG.md is not here: its release notes state one licence, MIT, and name
#: Apache-2.0 only for the open PDKs' data, as every other file does)
APACHE_OK = ("pdks/", "THIRD_PARTY_NOTICES.md", "docs/process-files.md",
             ".github/ISSUE_TEMPLATE/pdk_onboarding.yml", "tests/test_pdks.py",
             "tests/test_licence_and_claims.py", "tests/test_cadence_files.py")


def test_apache_only_for_third_party_data():
    for rel, text in tracked_text_files():
        if rel.startswith(APACHE_OK):
            continue
        for ln in text.splitlines():
            if "Apache" in ln:
                assert "PDK" in ln or "pdks" in ln, (rel, ln)
    for rel, text in tracked_text_files():
        if not rel.startswith(APACHE_OK):
            assert "SPDX-License-Identifier: Apache" not in text, rel


# ------------------------------------------------------------ the disclaimer


def test_the_disclaimer_is_where_the_project_is_introduced():
    for rel, head in (("README.md", 12), ("docs/index.md", 10), ("AGENTS.md", 12)):
        top = flat("\n".join(read(rel).splitlines()[:head]))
        assert flat(DISCLAIMER) in top or flat(DISCLAIMER.replace(
            "It is your", "It is the person's").replace("for your", "for their")) in top, rel
    skill = flat(read("agent/skills/emir/SKILL.md"))
    assert "research project, provided as is, with no warranty or guarantee of accuracy" in skill
    assert flat(DISCLAIMER) in flat(read("NOTICE"))
    assert "relay" in flat(read("AGENTS.md")).lower()


def test_the_blog_says_it_once():
    article = read("blog/linkedin-article.md")
    m = re.search(r"<!-- post:start -->(.*?)<!-- post:end -->", read("blog/linkedin-post.md"),
                  re.S)
    for text in (article, m.group(1)):
        assert len(re.findall(r"research project", text)) == 1
        assert "verify its results" in text


def test_results_carry_the_disclaimer(tmp_path):
    p = emir("run", DSPF, "--net", "VDD", "--process", PROC, "--currents", CUR,
             "--html", tmp_path / "nets", "--summary-out", tmp_path / "s.json")
    assert p.returncode == 0, p.stderr
    out = p.stdout.strip().splitlines()
    assert out[-2] == emir_pkg.RESEARCH_LINE and out[-1].startswith("overall verdict")
    s = json.load(open(tmp_path / "s.json"))
    assert s["research_disclaimer"] == DISCLAIMER and s["disclaimer"] == "Screening, not sign-off."
    for page in ("index.html", "railblock_VDD.html"):
        html = (tmp_path / "nets" / page).read_text()
        assert DISCLAIMER in html and "Screening, not sign-off." in html, page
    md = emir("report", "--from", tmp_path / "s.json").stdout
    assert md.count(DISCLAIMER) == 2           # beside the verdict, and the last line
    doc = json.loads(emir("report", "--from", tmp_path / "s.json", "--format", "json").stdout)
    assert doc["research_disclaimer"] == DISCLAIMER


# ------------------------------------------------------------ SAF


def test_saf_is_attributed_once_and_never_compared():
    allowed = {"emir/magic.py", "docs/process-files.md", "THIRD_PARTY_NOTICES.md",
               "NOTICE", "CHANGELOG.md", "tests/test_leak_guard.py",
               "tests/test_licence_and_claims.py"}
    for rel, text in tracked_text_files():
        hits = re.findall(r"\bSAF\b|Analog Framework", text)
        if rel not in allowed:
            assert not hits, rel
        if rel != "tests/test_licence_and_claims.py":
            assert not re.search(r"SAF's (own )?results|compared with SAF|against SAF", text), rel
    assert "Skycadeda Analog Framework (SAF)" in read("emir/magic.py").split('"""')[1]
    assert "Skycadeda Analog Framework (SAF)" in read("NOTICE")
    doc = read("docs/process-files.md")
    assert len(re.findall(r"\bSAF\b", doc)) == 1


# ------------------------------------------------------------ site claims


#: a claim that a licensed tool was verified, or ran, somewhere other than on
#: the machine that uses it (a "live-debugged" SKILL implies a Virtuoso that ran it)
SITE_CLAIM = re.compile(r"VERIFIED-AT|(verified|ran|run|observed|tested|checked)\b[^.]{0,40}"
                        r"original (development )?site|IC6\.1\.8|live[- ]debugged", re.I)


def test_no_licensed_tool_is_claimed_verified_anywhere():
    for rel, text in tracked_text_files():
        if rel in ("CHANGELOG.md", "tests/test_licence_and_claims.py",
                   "tests/test_cadence_files.py", "tests/test_leak_guard.py"):
            continue
        m = SITE_CLAIM.search(flat(text))
        assert not m, (rel, m.group(0) if m else None)


def test_the_simulators_page_states_where_each_simulator_is_tested():
    doc = flat(read("docs/simulators.md"))
    assert "| tested in this repository |" in doc
    assert "verified on your machine by" in doc
    for sim in ("Spectre", "HSPICE", "Xyce", "ngspice"):
        assert f"emir verify-sim --sim {sim.lower()}" in doc
    # no claim of support, and the quoted site measurements labelled as provenance
    assert "Nothing in this repository claims that Spectre or HSPICE works with EMIR" in doc
    assert "measurement provenance, not support" in doc


#: words that cite the private history (its branches, merges and review rounds)
#: rather than state behaviour
PRIVATE_HISTORY = (r"(?i)\bmerg(e|ed) (of|on)\b|post-merge|independent (review|verification)"
                   r"|\bmerged (waveform )?loop\b|\breview of the merged\b|\(integrate\)")


#: the changelog's words for a state of the private history that was never
#: published: what a file "listed before", a figure "drawn again" or "kept as
#: committed" (the release notes state the behaviour that ships)
UNPUBLISHED_STATE = r"(?i)\blisted before\b|\bkept as committed\b|\bdrawn again\b"


def test_the_changelog_reads_as_a_public_changelog():
    """The repository is published as one fresh commit: the changelog (whose
    Unreleased section becomes the release notes) states one licence and
    cites no commit, branch or merge of the private history."""
    text = read("CHANGELOG.md")
    unreleased = text.split("## [Unreleased]", 1)[1].split("\n## [", 1)[0]
    assert re.findall(r"\*\*Licence: ([A-Za-z0-9.-]+)\*\*", unreleased) == ["MIT"]
    assert "LICENSE` (Apache" not in unreleased
    assert not re.search(r"\b(feat|fix)/[a-z]", unreleased)
    assert not re.search(PRIVATE_HISTORY, unreleased), re.search(PRIVATE_HISTORY, unreleased)
    assert not re.search(UNPUBLISHED_STATE, unreleased), re.search(UNPUBLISHED_STATE,
                                                                   unreleased)
    hashes = subprocess.run(["git", "log", "--all", "--format=%h"], cwd=ROOT,
                            capture_output=True, text=True).stdout.split()
    for h in hashes:
        assert not re.search(rf"\b{h}\b", text), h


def test_the_shipped_source_and_tests_cite_no_private_history():
    """The package, the scripts and the tests are published too: a comment or
    a docstring states what the code does or guards, never the branch it came
    from ("feat/modes") or the review round that found it ("post-merge
    verification", "independent review of the merged loop")."""
    bad = re.compile(PRIVATE_HISTORY + r"|\b(feat|fix)/[a-z]")
    found = [f"{rel}:{text[:m.start()].count(chr(10)) + 1}: {m.group(0)}"
             for rel, text in tracked_text_files()
             if rel.startswith(("emir/", "scripts/", "tests/", "agent/", "examples/"))
             and rel != "tests/test_licence_and_claims.py"
             for m in bad.finditer(text)]
    assert not found, found


def test_the_public_docs_cite_no_commit_of_the_private_history():
    """The repository is published as one fresh commit: a document that
    measures or describes against "the commit before" or "at that commit"
    names something a reader cannot check out; it says what the base was
    (and that it is not in the repository) or states the final behaviour.
    (blog/ is the maintainer's to edit.)"""
    bad = re.compile(r"(?i)\bthe commit before\b|\bat that commit\b|\bat the merge of\b")
    found = [f"{rel}:{text[:m.start()].count(chr(10)) + 1}"
             for rel, text in tracked_text_files()
             if rel.endswith((".md", ".py")) and not rel.startswith(("blog/", "tests/"))
             for m in bad.finditer(text)]
    assert not found, found
    # nor a commit of this repository's own history by its hash, in any text
    # file ("at commit <hash>", "(at <hash>)" in a README, a CSV header, a
    # script's comment). A hash has a digit and a letter, so a plain number or
    # a word is not one; a public project's commit (open_pdks, the PDKs the
    # process files cite) is not one of this repository's, so it may stay
    sha = r"((?=[0-9a-f]*[a-f])(?=[0-9a-f]*[0-9])[0-9a-f]{7,40})\b"
    cited = re.compile(r"\b(?:commit|at)\s+\(?" + sha + r"|\(\s*" + sha + r"\s*\)")

    def own(h):
        return subprocess.run(["git", "rev-parse", "--verify", "--quiet", h + "^{commit}"],
                              cwd=ROOT, capture_output=True).returncode == 0
    found = [f"{rel}:{text[:m.start()].count(chr(10)) + 1}: {m.group(0)}"
             for rel, text in tracked_text_files()
             if not rel.startswith(("blog/", "tests/"))
             for m in cited.finditer(text) if own(m.group(1) or m.group(2))]
    assert not found, found


#: the modules derived from the Skycadeda projects, each with what its
#: docstring must name (THIRD_PARTY_NOTICES.md lists them all)
DERIVED = {"emir/magic.py": ["Skycadeda Analog Framework (SAF)", "write_dspf"],
           "emir/style.py": ["Skyforge"],
           "emir/chart.py": ["Skyforge", "fit_window"],
           "emir/dashboard.py": ["Skyforge", "legend_html"],
           "emir/underlay.py": ["Skyforge"]}


@pytest.mark.parametrize("rel", sorted(DERIVED))
def test_every_derived_module_keeps_its_credit(rel):
    """The credits for the code derived from SAF and Skyforge (MIT, by their
    copyright holder) were pinned only for SAF in magic.py and NOTICE: a later
    edit could drop a Skyforge credit, or THIRD_PARTY_NOTICES.md's line for a
    module, silently."""
    doc = flat(read(rel).split('"""')[1])
    for want in DERIVED[rel] + ["Copyright (c) 2026 Skycadeda", "MIT License"]:
        assert want in doc, (rel, want)
    notices = flat(read("THIRD_PARTY_NOTICES.md"))
    assert "Skycadeda Analog Framework (SAF)" in notices and "Skyforge" in notices
    notice = flat(read("NOTICE"))
    assert "Skyforge" in notice and "Skycadeda Analog Framework (SAF)" in notice


#: the private projects' internals: file paths, module and function names and
#: the package name (the maintainer, 2026-10-04: credit the projects only)
PRIVATE_INTERNALS = re.compile(
    r"review_deck|EmirView|LayoutViewer|ui/layout\.py|frontend/src|automator|"
    r"saf[./](backends|methods|flows)|saf\.write_dspf|MAGIC_PEX|emir-magic-network|"
    r"draw_emir|emir_slides")


def test_the_credit_names_the_projects_not_their_internals():
    """SAF and Skyforge are private: the public tree credits them by name and
    never cites their files, modules, functions or package names."""
    found = [(rel, m.group(0)) for rel, text in tracked_text_files()
             if rel != "tests/test_licence_and_claims.py"
             for m in PRIVATE_INTERNALS.finditer(text)]
    assert not found, found
