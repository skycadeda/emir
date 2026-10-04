#!/usr/bin/env python3
"""Check that an sdist is the whole source tree: every file git tracks is in
it, except the few listed in `NOT_SHIPPED` (each with its reason).

The sdist is a complete source tree (MANIFEST.in): its tests need bin/,
scripts/, examples/, pdks/ and agent/. A file added under a pattern that
MANIFEST.in does not include is missing from it without any error from the
build; this check, run by CI and by the release workflow, catches that.

    python scripts/check_sdist.py dist/emir-<version>.tar.gz

Exit status 0 when nothing is missing, 1 when a tracked file is not in the
sdist, 2 on a usage error. Standard library only; needs git and a checkout.
"""
from __future__ import annotations

import argparse
import fnmatch
import os
import subprocess
import sys
import tarfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

#: tracked files the sdist leaves out on purpose: (glob, reason)
NOT_SHIPPED = [
    (".gitignore", "git configuration"),
    ("*/.gitignore", "git configuration"),
    (".github/*", "GitHub workflows and forms: they run on GitHub, not from an sdist"),
    (".claude/*", "a link to agent/skills/emir, which the sdist carries"),
]


def tracked(root: str) -> list[str]:
    p = subprocess.run(["git", "-C", root, "ls-files", "-z"], capture_output=True)
    if p.returncode != 0:
        raise SystemExit(f"check_sdist: git ls-files failed in {root}: "
                         f"{p.stderr.decode(errors='replace').strip()}")
    return [f for f in p.stdout.decode().split("\0") if f]


def members(sdist: str) -> set[str]:
    """The sdist's file names without its top directory (`emir-<version>/`)."""
    with tarfile.open(sdist) as t:
        return {m.name.split("/", 1)[1] for m in t.getmembers()
                if "/" in m.name and not m.isdir()}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="check_sdist", description=__doc__.split("\n\n")[0],
                                 allow_abbrev=False)
    ap.add_argument("sdist", help="the .tar.gz that `python -m build --sdist` wrote")
    ap.add_argument("--root", default=ROOT, help="the checkout (default: this repository)")
    a = ap.parse_args(argv)
    if not os.path.isfile(a.sdist):
        print(f"check_sdist: {a.sdist}: no such file", file=sys.stderr)
        return 2
    have = members(a.sdist)
    missing, skipped = [], 0
    for f in tracked(a.root):
        if any(fnmatch.fnmatch(f, g) for g, _ in NOT_SHIPPED):
            skipped += 1
            continue
        if f not in have:
            missing.append(f)
    if missing:
        print(f"check_sdist: {len(missing)} tracked file(s) missing from {a.sdist}:")
        for f in missing:
            print(f"  {f}")
        print("Add a pattern for them to MANIFEST.in, or list them in NOT_SHIPPED in "
              "scripts/check_sdist.py with the reason.")
        return 1
    print(f"check_sdist: {a.sdist} holds every tracked file "
          f"({skipped} left out on purpose: {', '.join(g for g, _ in NOT_SHIPPED)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
