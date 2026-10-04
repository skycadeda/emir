#!/usr/bin/env python3
"""Print the GitHub Release notes of a version: its CHANGELOG.md section.

GitHub refuses a release body over 125,000 characters (`gh release create`
fails), so a longer section is cut after its last whole entry that fits,
with a note linking the full CHANGELOG. The notes end with the screening
line, the research disclaimer and how to check the downloads. Standard library only: the release job
runs it before anything is installed.

    python scripts/release_notes.py 1.1.0 > notes.md
    python scripts/release_notes.py 1.1.0 --url <the CHANGELOG's URL at the tag>
"""
from __future__ import annotations

import argparse
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
#: GitHub's limit on a release body, in characters
LIMIT = 125_000
#: `emir.RESEARCH_DISCLAIMER`, copied: the release job runs this script before
#: emir is installed (tests/test_community_files.py keeps the two equal)
RESEARCH_DISCLAIMER = ("EMIR is a research project, provided as is, with no warranty or "
                       "guarantee of accuracy. It is your responsibility to test and verify "
                       "its results for your process, tools and design before relying on "
                       "them.")
FOOTER = ("Screening, not sign-off. " + RESEARCH_DISCLAIMER + " Check the downloads against "
          "SHA256SUMS (`sha256sum -c SHA256SUMS`).")


def section(text: str, ver: str) -> str | None:
    """The body of `## [ver]` in a changelog, or None."""
    m = re.search(r"^## \[" + re.escape(ver) + r"\][^\n]*\n(.*?)(?=^## \[|\Z)", text,
                  re.M | re.S)
    return m.group(1).strip() if m else None


def cut(body: str, room: int) -> str:
    """`body` cut after its last whole top-level entry (`- ...` with its
    continuation lines) that ends within `room` characters. It never ends on
    a heading: one whose entries did not fit is left out with them."""
    if len(body) <= room:
        return body
    ends = [m.start() for m in re.finditer(r"\n(?=- |### )", body) if m.start() <= room]
    kept = body[:ends[-1]].rstrip() if ends else body[:room].rsplit("\n", 1)[0]
    return re.sub(r"(\n+#{2,6} [^\n]*)+\s*$", "", kept).rstrip()


def notes(text: str, ver: str, url: str) -> str:
    body = section(text, ver)
    if body is None:
        return f"No CHANGELOG.md section for {ver}.\n\n{FOOTER}\n"
    more = (f"\n\n**The list goes on**: the full list of changes in {ver} is in "
            f"[CHANGELOG.md]({url}).")
    room = LIMIT - len(FOOTER) - len(more) - 16
    kept = cut(body, room)
    return kept + (more if kept != body else "") + "\n\n" + FOOTER + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0], allow_abbrev=False)
    ap.add_argument("version", help="the version, without the tag's v (1.1.0)")
    ap.add_argument("--changelog", default=os.path.join(ROOT, "CHANGELOG.md"))
    ap.add_argument("--url", default=None,
                    help="where the full CHANGELOG is read (default: the repository's, "
                         "at the tag v<version>)")
    a = ap.parse_args(argv)
    with open(a.changelog, encoding="utf-8") as f:
        text = f.read()
    url = a.url or f"https://github.com/skycadeda/emir/blob/v{a.version}/CHANGELOG.md"
    sys.stdout.write(notes(text, a.version, url))
    return 0


if __name__ == "__main__":
    sys.exit(main())
