"""One repository address: `emir.repo.REPO_URL`, https://github.com/skycadeda/emir.

The package's hints, the error catalog and the Virtuoso SKILL take it from
`emir/repo.py`; the files that cannot import it repeat it, and this test fails
unless every copy agrees with the constant. To move the repository: change
`REPO_URL`, regenerate (`scripts/gen_errors_md.py`,
`scripts/gen_cadence_skill.py`), and replace the URL in the files `REPEATS`
lists (this test names any it finds still pointing elsewhere)."""
import os
import re

import pytest
from _helpers import ROOT

from emir import repo

#: files that repeat REPO_URL, because they cannot import it
REPEATS = (".github/ISSUE_TEMPLATE/config.yml", "mkdocs.yml", "pyproject.toml", "CITATION.cff",
           "CONTRIBUTING.md", "docs/getting-started.md", "blog/linkedin-article.md",
           "blog/linkedin-post.md", "blog/README.md")
#: generated from the constant (the generator checks keep them current)
GENERATED = ("agent/errors.md", "agent/cadence/skill/emir_main.il")

#: a reference to this repository on GitHub (any owner)
THIS_REPO = re.compile(r"https?://github\.com/([A-Za-z0-9_.-]+)/emir(?![A-Za-z0-9_-])")
SKIP_DIRS = {".git", ".venv", "venv", "build", "dist", "site", "emir_out", "out",
             "__pycache__", ".pytest_cache", ".ruff_cache", "node_modules"}
TEXT = (".py", ".md", ".yml", ".yaml", ".toml", ".il", ".sh", ".cff", ".txt", ".json",
        ".html", ".in", "")


def _text_files():
    for d, dirs, names in os.walk(ROOT):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS and not x.endswith(".egg-info")]
        for n in names:
            if n.endswith(TEXT):
                p = os.path.join(d, n)
                try:
                    with open(p, encoding="utf-8") as f:
                        yield os.path.relpath(p, ROOT), f.read()
                except (UnicodeDecodeError, OSError):
                    continue


def test_every_reference_to_the_repository_is_repo_url():
    owner = re.match(r"https://github\.com/([^/]+)/emir$", repo.REPO_URL)
    assert owner, repo.REPO_URL
    bad = [f"{rel}:{text[:m.start()].count(chr(10)) + 1}: {m.group(0)}"
           for rel, text in _text_files() for m in THIS_REPO.finditer(text)
           if m.group(1) != owner.group(1)]
    assert not bad, bad


@pytest.mark.parametrize("rel", REPEATS + GENERATED)
def test_the_files_that_repeat_it_hold_it(rel):
    p = os.path.join(ROOT, rel)
    if not os.path.isfile(p):
        pytest.skip(f"{rel} is not in this tree")
    with open(p, encoding="utf-8") as f:
        assert repo.REPO_URL in f.read(), rel


def test_the_package_takes_it_from_the_constant():
    from emir import errors, skillgui
    hints = " ".join(e.hint for e in errors.REGISTRY.values())
    assert hints.count(repo.REPO_URL) >= 3
    assert repo.REPO_URL in skillgui.MAIN
    assert repo.url("agent/playbooks/") == f"{repo.REPO_URL}/tree/main/agent/playbooks"


def test_mkdocs_carries_it_and_the_pages_address():
    text = open(os.path.join(ROOT, "mkdocs.yml"), encoding="utf-8").read()
    assert re.search(rf"^repo_url: {re.escape(repo.REPO_URL)}$", text, re.M)
    # the site docs.yml deploys to GitHub Pages: <owner>.github.io/<repository>/
    owner, name = repo.REPO_URL.rsplit("/", 2)[-2:]
    assert re.search(rf"^site_url: https://{owner}\.github\.io/{name}/$", text, re.M)


def test_pyproject_names_it():
    text = open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8").read()
    urls = text.split("\n[project.urls]\n", 1)[1].split("\n[", 1)[0]
    assert f'Homepage = "{repo.REPO_URL}"' in urls
    assert f'Issues = "{repo.REPO_URL}/issues"' in urls


def test_no_placeholder_is_left():
    assert "OWNER" not in repo.REPO_URL
    placeholder = "github.com/" + "OWNER/"           # (this file would match itself)
    left = [rel for rel, text in _text_files() if placeholder in text]
    assert not left, left
