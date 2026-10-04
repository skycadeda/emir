#!/usr/bin/env python3
"""Leak guard: keep foundry data, site details and licence data out of the repo.

EMIR is built against licensed PDKs at real sites; this repository must carry
none of that. The guard fails on:

* text that points at a site -- site and desktop-user paths, a site's tool
  install directory, licence servers and licence variables, wording that names
  an internal deployment's users or plan, a closed-licence string (the public
  rules below);
* text that identifies a licensed PDK or an internal project -- its name, node,
  device models, layers, display packets and numbers, internal project, module,
  cell and user names (the private rules: see below);
* files that are PDK data by their type -- an ICT (`*.ict`), a tech file
  (`*.tf`), a layer map (`*.layermap`), a display resource file (`*.drf`;
  EMIR's own generated `emir_display.drf` is the one exception) -- and compiled
  Python (`*.pyc`, `__pycache__/`), which embeds the build machine's paths;
* text in a PDF's compressed streams, read the way a reader shows it
  (`pdftotext` when it is installed, and every deflated stream).

**Private rules.** A pattern that names what it guards against is itself the
leak, however it is spelt, so the identifying patterns are not in this file or
anywhere in the repository. They are read from a private rule file: the file
`$EMIR_LEAK_PRIVATE` names, else `.leak-guard-private` at the repository root
(git ignores it; it is never committed, never in a release). The maintainer
keeps it; CI writes it from a repository secret. One rule per line, four
tab-separated fields (and an optional fifth), `#` lines are comments::

    <rule id>  <text|any>  <Python regex>  <why>  [<path glob>,<path glob>...]

`text` rules are not applied to binary files (short patterns match random
bytes there); the globs limit a rule to the files they match (a number that
is a leak in a page but a coordinate in a drawing). Without the file only the public rules run, and `--list-rules`
says so; `--require-private` makes its absence an error (for CI).

Every directory is scanned, `.claude/`, `.github/`, `docs/`, `examples/` and
`blog/` included, and the test suite runs the guard on its own source.

Intentional, documented mentions go in `.leak-guard-allow` at the repo root,
one per line::

    <path glob>  <rule id or *>  [text=<substring the line must contain>]  # <reason>

The reason is required. `text=` narrows an entry to the lines containing that
substring, so allowing one example path does not allow every path in the file.

    python scripts/leak_guard.py              # every tracked + untracked file
    python scripts/leak_guard.py --staged     # what `git commit` would record
    python scripts/leak_guard.py FILE ...     # just these (the pre-commit hook)
    python scripts/leak_guard.py --commit-msg .git/COMMIT_EDITMSG   # the message
    python scripts/leak_guard.py --commit REV ...   # each file a commit adds or changes
    python scripts/leak_guard.py --list-rules

Exit status 0 when clean, 1 on a finding, 2 on a usage, allowlist or rule-file
error. Standard library only, so it runs before anything is installed.
"""
from __future__ import annotations

import argparse
import fnmatch
import os
import re
import subprocess
import sys
from dataclasses import dataclass

ALLOW_FILE = ".leak-guard-allow"


@dataclass(frozen=True)
class Rule:
    id: str
    regex: re.Pattern
    why: str
    binary: bool = True      # also searched in binary files (long literals only)
    paths: tuple = ()        # path globs the rule is limited to (none: every file)


def _r(p, flags=re.IGNORECASE):
    return re.compile(p, flags)


#: the public content rules: what points at a site, a licence or a closed
#: licence. Nothing here identifies a PDK or a project (those are the private
#: rules). Short or regex-like patterns are text-only: in a binary file they
#: would match random bytes.
RULES = [
    Rule("site-path", _r(r"/ho[m]e/|/ca[d]/|/pro[j]/"), "a site or user path"),
    Rule("user-path", _r(r"/User[s]/|\b[A-Za-z]:[\\/]+User[s][\\/]"),
         "a desktop user's home path"),
    # an install root at the top of the file system, or a tool directory
    # named the way a site installs one release beside the next
    # (a release tag as a site names its install directory: three digits,
    # in any case, with or without a suffix -- `_ISR`, `-64b`; the dotted
    # product version, as in "Virtuoso 6.1.8 or later", is public and passes)
    Rule("site-install",
         _r(r"(?:^|(?<=[\s\"'=:(\[,]))/(?:instal[l]|too[l]s)/"
            r"|/instal[l]/(?:IC|SPECTRE|QUANTUS|PVS|FOUNDRY|PDK)"
            r"|(?i:(?<![A-Za-z0-9])(?:I[C]|SPECTR[E]|QUANTU[S]|PV[S])[0-9]{3}(?![0-9]))", 0),
         "a site's tool install directory", binary=False),
    Rule("licence-server", _r(r"[0-9]{4,5}@[A-Za-z]", 0), "a port@host licence server",
         binary=False),
    Rule("licence-env", _r(r"CDS_LIC_[F]ILE|LM_LICENSE_[F]ILE"), "a licence variable"),
    Rule("deployment-wording", _r(r"\bcusto[m]ers?\b"),
         "wording that names an internal deployment's users", binary=False),
    Rule("internal-plan", _r(r"\bpla[n] ?\u00a7"), "a reference into an internal plan",
         binary=False),
    Rule("closed-licence", _r(r"\bpropri[e]tary\b"), "a closed-licence string, in any case"),
]

#: the private rule file (module doc): `$EMIR_LEAK_PRIVATE`, else this at the root
PRIVATE_FILE = ".leak-guard-private"
PRIVATE_ENV = "EMIR_LEAK_PRIVATE"


def private_path(root: str) -> str | None:
    """The private rule file to read, or None when there is none."""
    p = os.environ.get(PRIVATE_ENV) or os.path.join(root, PRIVATE_FILE)
    return p if os.path.isfile(p) else None


def load_private(root: str) -> list[Rule]:
    """The private rules (module doc). A malformed line is an error: a rule
    that silently does nothing is worse than none."""
    path = private_path(root)
    if path is None:
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for n, raw in enumerate(f, 1):
            line = raw.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) not in (4, 5) or parts[1] not in ("text", "any") \
                    or not parts[0].strip():
                raise ValueError(f"{path}:{n}: expected '<rule id>\\t<text|any>\\t<regex>"
                                 f"\\t<why>[\\t<globs>]'")
            try:
                rx = re.compile(parts[2])
            except re.error as e:
                raise ValueError(f"{path}:{n}: bad regex: {e}") from e
            globs = tuple(g.strip() for g in parts[4].split(",") if g.strip()) \
                if len(parts) == 5 else ()
            out.append(Rule(parts[0].strip(), rx, parts[3].strip(), binary=parts[1] == "any",
                            paths=globs))
    return out


#: file types that are PDK data in themselves
FILE_RULES = [("*.ict", "an ICT (process EM/RC data)"),
              ("*.tf", "a tech file"),
              ("*.layermap", "a layer map"),
              ("*.drf", "a display resource file")]
FILE_EXCEPTIONS = {"emir_display.drf"}       # EMIR's own packets, no PDK content
#: compiled Python embeds the absolute source paths of the machine that built it
COMPILED = ("*.pyc", "*.pyo")

#: never scanned (when walking a directory that is not a git checkout)
SKIP_DIRS = {".git", ".venv", "venv", "build", "dist", ".pytest_cache",
             ".ruff_cache", "node_modules", "emir_out", "site"}


@dataclass
class Allow:
    glob: str
    rule: str
    text: str | None
    reason: str
    lineno: int


def load_allowlist(root: str, rules=None) -> list[Allow]:
    path = os.path.join(root, ALLOW_FILE)
    out = []
    if not os.path.isfile(path):
        return out
    ids = {r.id for r in (rules or RULES)} | {r.id for r in RULES} | {"file-type", "*"}
    with open(path, encoding="utf-8") as f:
        for n, raw in enumerate(f, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            body, sep, reason = line.partition("#")
            if not sep or not reason.strip():
                raise ValueError(f"{ALLOW_FILE}:{n}: every entry needs a '# reason'")
            text = None
            if "text=" in body:
                body, _, text = body.partition("text=")
                text = text.strip()
            fields = body.split()
            if len(fields) != 2:
                raise ValueError(f"{ALLOW_FILE}:{n}: expected '<glob> <rule> [text=...] # reason'")
            if fields[1] not in ids:
                raise ValueError(f"{ALLOW_FILE}:{n}: unknown rule {fields[1]!r} "
                                 f"(one of {', '.join(sorted(ids))})")
            out.append(Allow(fields[0], fields[1], text, reason.strip(), n))
    return out


def _allowed(allow: list[Allow], path: str, rule: str, line: str) -> bool:
    for a in allow:
        if a.rule not in ("*", rule):
            continue
        if not fnmatch.fnmatch(path, a.glob):
            continue
        if a.text is not None and a.text not in line:
            continue
        return True
    return False


def _git(root: str, *args: str) -> list[str] | None:
    try:
        p = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True)
    except FileNotFoundError:
        return None
    if p.returncode != 0:
        return None
    return [ln for ln in p.stdout.split("\0") if ln]


def tree_files(root: str) -> list[str]:
    """Tracked plus untracked-but-not-ignored files; a plain walk outside git."""
    files = _git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    if files is not None:
        return sorted({f for f in files if os.path.isfile(os.path.join(root, f))})
    out = []
    for d, dirs, names in os.walk(root):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS and not x.endswith(".egg-info")]
        for nm in names:
            out.append(os.path.relpath(os.path.join(d, nm), root))
    return sorted(out)


def staged_files(root: str) -> list[str]:
    files = _git(root, "diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR")
    if files is None:
        raise RuntimeError("--staged needs a git checkout")
    return sorted(files)


def _read(root: str, rel: str, staged: bool) -> bytes:
    if staged:
        p = subprocess.run(["git", "-C", root, "show", f":{rel}"], capture_output=True)
        if p.returncode == 0:
            return p.stdout
    with open(os.path.join(root, rel), "rb") as f:
        return f.read()


#: a PDF's compressed stream
_PDF_STREAM = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.S)


def pdf_texts(data: bytes) -> list[tuple[str, str]]:
    """`[(where, text)]` a PDF holds beyond its raw bytes: what `pdftotext`
    reads from it (when installed), and each stream that inflates."""
    import shutil
    import tempfile
    import zlib
    out = []
    exe = shutil.which("pdftotext")
    if exe:
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "in.pdf")
            with open(src, "wb") as f:
                f.write(data)
            try:
                p = subprocess.run([exe, "-q", src, "-"], capture_output=True, timeout=120)
                if p.returncode == 0:
                    out.append(("pdftotext", p.stdout.decode("utf-8", errors="replace")))
            except (OSError, subprocess.SubprocessError):
                pass
    for k, m in enumerate(_PDF_STREAM.finditer(data)):
        try:
            raw = zlib.decompress(m.group(1))
        except zlib.error:
            continue
        out.append((f"stream {k}", raw.decode("latin-1", errors="replace")))
    return out


def scan_bytes(rel: str, data: bytes, allow: list[Allow], rules=None) -> list[str]:
    """Findings for one file's content, as `path:line: rule: text` strings
    (`rules`: the public rules, plus the private ones when loaded). A PDF's
    compressed text is read too (`pdf_texts`)."""
    rules = RULES if rules is None else rules
    out = []
    if data[:5] == b"%PDF-":
        for where, text in pdf_texts(data):
            out += scan_bytes(f"{rel} ({where})", text.encode("utf-8"), allow, rules)
    binary = b"\0" in data[:8192]
    text = data.decode("latin-1" if binary else "utf-8", errors="replace")
    base = rel.split(" (", 1)[0]
    rules = [r for r in rules if not r.paths or any(fnmatch.fnmatch(base, g) for g in r.paths)]
    for n, line in enumerate(text.splitlines(), 1):
        for rule in rules:
            if binary and not rule.binary:
                continue
            for m in rule.regex.finditer(line):
                if _allowed(allow, rel, rule.id, line):
                    continue
                shown = line.strip() if not binary else m.group(0)
                if len(shown) > 160:
                    s = max(m.start() - 60, 0)
                    shown = "..." + line[s:s + 140].strip() + "..."
                out.append(f"{rel}:{n}: {rule.id} ({rule.why}): {shown}")
    return out


def scan_name(rel: str, allow: list[Allow]) -> list[str]:
    base = os.path.basename(rel)
    if base in FILE_EXCEPTIONS:
        return []
    if ("__pycache__" in rel.split("/")
            or any(fnmatch.fnmatch(base.lower(), p) for p in COMPILED)):
        if not _allowed(allow, rel, "file-type", ""):
            return [f"{rel}: file-type (compiled Python, which embeds the build "
                    f"machine's paths): never commit it"]
        return []
    for pat, why in FILE_RULES:
        if fnmatch.fnmatch(base.lower(), pat) and not _allowed(allow, rel, "file-type", ""):
            return [f"{rel}: file-type ({why}): PDK data files must not be committed"]
    return []


#: git's scissors line: everything below it (`commit -v`) is not the message
SCISSORS = "# ------------------------ >8 ------------------------"


def message_text(raw: str) -> str:
    """The part of a commit-message file git will record: comment lines and
    everything from the scissors line down are dropped."""
    out = []
    for line in raw.splitlines():
        if line.startswith(SCISSORS):
            break
        if not line.startswith("#"):
            out.append(line)
    return "\n".join(out) + "\n"


def check_message(path: str, root: str, rules=None) -> int:
    """Scan one commit message. Exit status as for files."""
    try:
        rules = RULES + load_private(root) if rules is None else rules
        allow = load_allowlist(root, rules)
        with open(path, encoding="utf-8", errors="replace") as f:
            text = message_text(f.read())
    except (OSError, ValueError) as e:
        print(f"leak_guard: {e}", file=sys.stderr)
        return 2
    findings = scan_bytes("<commit message>", text.encode("utf-8"), allow, rules)
    if findings:
        for f in findings:
            print(f)
        print(f"\nleak_guard: {len(findings)} finding(s) in the commit message. "
              f"Reword it; history is published.", file=sys.stderr)
        return 1
    print("leak_guard: commit message clean")
    return 0


def commit_files(root: str, rev: str) -> list[tuple[str, bytes]]:
    """`[(path, content)]` of every file commit `rev` adds or changes, as
    that commit recorded it: every commit is published, not only the last,
    so each is checked on its own. A merge commit's files are those that
    differ from any of its parents (`-m`): what a merge resolution wrote
    exists in no other commit (plain `diff-tree` lists nothing for a merge)."""
    names = _git(root, "diff-tree", "--root", "--no-commit-id", "-r", "-m", "-z",
                 "--name-only", "--diff-filter=ACMR", rev)
    if names is None:
        raise RuntimeError(f"--commit {rev}: not a commit of this repository")
    out = []
    for rel in dict.fromkeys(names):
        p = subprocess.run(["git", "-C", root, "show", f"{rev}:{rel}"], capture_output=True)
        if p.returncode == 0:
            out.append((rel, p.stdout))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="leak_guard", description=__doc__.split("\n\n")[0],
                                 allow_abbrev=False)
    ap.add_argument("files", nargs="*", help="files to scan (default: the whole tree)")
    ap.add_argument("--staged", action="store_true",
                    help="scan the staged content of the files `git commit` would record")
    ap.add_argument("--root", default=None,
                    help="repository root (default: the parent of scripts/)")
    ap.add_argument("--commit-msg", metavar="FILE", default=None,
                    help="scan a commit message (the commit-msg hook passes "
                         "the message file)")
    ap.add_argument("--commit", metavar="REV", action="append", default=None,
                    help="scan every file these commits add or change, as each recorded "
                         "it, and their messages (repeatable; CI passes each new commit)")
    ap.add_argument("--require-private", action="store_true",
                    help="fail (exit 2) when there is no private rule file")
    ap.add_argument("--list-rules", action="store_true")
    a = ap.parse_args(argv)

    root = os.path.abspath(a.root or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                  ".."))
    try:
        private = load_private(root)
    except (OSError, ValueError) as e:
        print(f"leak_guard: {e}", file=sys.stderr)
        return 2
    if a.require_private and not private:
        print(f"leak_guard: no private rules (${PRIVATE_ENV} or {PRIVATE_FILE} at the root): "
              f"only the public rules would run", file=sys.stderr)
        return 2
    rules = RULES + private

    if a.list_rules:
        for r in RULES:
            print(f"  {r.id:20s} {r.why}{'' if r.binary else '  (text files only)'}")
        for pat, why in FILE_RULES:
            print(f"  {'file-type':20s} {pat}: {why}")
        print(f"  {'file-type':20s} {', '.join(COMPILED)}, __pycache__/: compiled Python")
        src = private_path(root)
        print(f"  private rules: {len(private)}" + (f" from {src}" if src else
              f" (none: set ${PRIVATE_ENV} or add {PRIVATE_FILE} at the root)"))
        return 0

    if a.commit_msg:
        return check_message(a.commit_msg, root, rules)
    try:
        allow = load_allowlist(root, rules)
        if a.commit:
            blobs = []
            for rev in a.commit:
                blobs += [(f"{rev[:12]}:{rel}", rel, data)
                          for rel, data in commit_files(root, rev)]
                msg = subprocess.run(["git", "-C", root, "log", "-1", "--format=%B", rev],
                                     capture_output=True)
                blobs.append((f"{rev[:12]}:<commit message>", "<commit message>",
                              msg.stdout))
            files = None
        elif a.files:
            files = [os.path.relpath(os.path.abspath(f), root) for f in a.files]
        elif a.staged:
            files = staged_files(root)
        else:
            files = tree_files(root)
    except (ValueError, RuntimeError) as e:
        print(f"leak_guard: {e}", file=sys.stderr)
        return 2

    findings = []
    if files is None:
        for shown, rel, data in blobs:
            if rel != "<commit message>":
                findings += [f"{shown.split(':', 1)[0]}:{f}" for f in scan_name(rel, allow)]
            findings += [f"{shown.split(':', 1)[0]}:{f}"
                         for f in scan_bytes(rel, data, allow, rules)]
        n = len(blobs)
    else:
        for rel in files:
            rel = rel.replace(os.sep, "/")
            findings += scan_name(rel, allow)
            try:
                data = _read(root, rel, a.staged)
            except OSError as e:
                findings.append(f"{rel}: cannot read ({e})")
                continue
            findings += scan_bytes(rel, data, allow, rules)
        n = len(files)

    if findings:
        for f in findings:
            print(f)
        print(f"\nleak_guard: {len(findings)} finding(s) in {n} files. Remove "
              f"them, or -- only for a documented, intentional mention -- add an entry "
              f"to {ALLOW_FILE}.", file=sys.stderr)
        return 1
    print(f"leak_guard: {n} files clean"
          + ("" if private else " (public rules only: no private rule file)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
