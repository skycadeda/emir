"""Where the repository's documents are, for the advice emir prints.

The package's advice cites files of the repository that a wheel does not
ship: the agent playbooks (`agent/playbooks/...`), `agent/README.md`, the
Cadence integration (`agent/cadence/...`) and the process files of the open
PDKs (`pdks/...`). Run from a clone, a reference is the file in it; installed
from a wheel, it is the file's page in the repository on the web, so the
advice works either way.

`REPO_URL` is the public repository, https://github.com/skycadeda/emir. It is
the one constant: the CLI's hints and the error catalog (`emir.errors`), the
Virtuoso SKILL (`emir.skillgui`, then `scripts/gen_cadence_skill.py` and
`scripts/gen_errors_md.py` regenerate the committed copies) take it from
here. Files that cannot import it repeat it -- the issue forms' `config.yml`,
`mkdocs.yml` (`repo_url`), `pyproject.toml` (`[project.urls]`), the clone
commands in CONTRIBUTING.md and docs/getting-started.md, and the blog -- and
`tests/test_repo_url.py` fails unless every copy agrees.
"""
from __future__ import annotations

import os

#: the public repository
REPO_URL = "https://github.com/skycadeda/emir"
#: the branch the references point at
BRANCH = "main"


def clone_root() -> str | None:
    """The clone this package runs from (its `pyproject.toml` and `agent/`
    beside the package), or None for an installed wheel."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.isfile(os.path.join(root, "pyproject.toml")) and \
            os.path.isdir(os.path.join(root, "agent")):
        return root
    return None


#: the optional extras of pyproject.toml and the packages each installs
#: (`tests/test_packaging.py` checks they agree): emir is not on PyPI, so a
#: hint never asks pip for emir itself with an extra
EXTRAS = {
    "report": ["matplotlib>=3.6"],
    "klayout": ["klayout>=0.29"],
    "mcp": ["mcp>=1.19"],
    "all": ["matplotlib>=3.6", "klayout>=0.29", "mcp>=1.19"],
    "dev": ["matplotlib>=3.6", "klayout>=0.29", "gdstk>=0.9", "jsonschema>=4.18",
            "mcp>=1.19", "pytest>=7", "ruff>=0.6", "pre-commit>=3", "build>=1"],
}


def bundle_root() -> str | None:
    """The offline bundle this package runs from (its `python/bin/python3`
    and `wheels/` beside the package), or None."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.isfile(os.path.join(root, "python", "bin", "python3")) and \
            os.path.isdir(os.path.join(root, "wheels")):
        return root
    return None


def install_hint(extra: str) -> str:
    """The command that installs an optional extra, for this install: from a
    clone, `pip install -e '<clone>[extra]'`; otherwise the packages it
    names (emir itself is not on PyPI, so asking pip for emir with an extra
    would fail, or later install someone else's package)."""
    import shlex
    root = clone_root()
    if root is not None:
        return "pip install -e " + shlex.quote(f"{root}[{extra}]")
    pkgs = " ".join(shlex.quote(p) for p in EXTRAS[extra])
    b = bundle_root()
    if b is not None:
        return f"{shlex.quote(os.path.join(b, 'python', 'bin', 'python3'))} -m pip install {pkgs}"
    return f"pip install {pkgs}"


def url(rel: str) -> str:
    """The web page of repository path `rel` (a directory when it ends in `/`)."""
    kind = "tree" if rel.endswith("/") else "blob"
    return f"{REPO_URL}/{kind}/{BRANCH}/{rel.rstrip('/')}"


def ref(rel: str) -> str:
    """Repository path `rel` (`agent/playbooks/03-chip-level-hierarchy.md`,
    `agent/cadence/spectre/`) as an agent can open it here: the path in this
    clone, else its page on the web (`url`)."""
    root = clone_root()
    if root is not None:
        return os.path.join(root, rel.rstrip("/"))
    return url(rel)


def pdk_files() -> str:
    """The open PDKs' process files and the template, as the plans cite them."""
    return (f"{ref('pdks/sky130.yaml')}, {ref('pdks/gf180mcu.yaml')} or "
            f"{ref('pdks/ihp_sg13g2.yaml')} for those open PDKs; otherwise copy "
            f"{ref('pdks/TEMPLATE.yaml')}")
