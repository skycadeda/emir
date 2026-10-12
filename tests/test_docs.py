"""The documentation: generated pages are current, links resolve, the README's
quickstart runs as written, and the process-file reference names exactly the
schema's keys.

These checks keep the pages from drifting away from the code: a renamed
option, a new process key or a moved file fails here, not in a reader's hands.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DOCS = os.path.join(ROOT, "docs")
PAGES = sorted(f for f in os.listdir(DOCS) if f.endswith(".md"))

_LINK = re.compile(r"(!?)\[([^\]\n]*)\]\(([^)\s]+)\)")
_FENCE = re.compile(r"^\s*(```|~~~)")


def _text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _outside_fences(text):
    """The lines of `text` that are not inside a fenced code block."""
    fenced = False
    for line in text.splitlines():
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if not fenced:
            yield line


def _slug(heading):
    """The anchor GitHub and MkDocs' toc both give a heading (ASCII headings)."""
    s = heading.strip().lower()
    s = re.sub(r"[^\w\- ]", "", s)
    return s.replace(" ", "-")


def _anchors(path):
    return {_slug(m.group(1)) for m in re.finditer(r"^#{1,6}\s+(.+?)\s*#*\s*$",
                                                   _text(path), re.M)}


def _links(path):
    for line in _outside_fences(_text(path)):
        for m in _LINK.finditer(line):
            yield m.group(3)


# ------------------------------------------------------------------ generated


def test_cli_reference_is_current():
    p = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "gen_cli_reference.py"),
                        "--check"], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr or p.stdout


def test_cli_reference_has_every_command():
    from emir import cli
    text = _text(os.path.join(DOCS, "cli-reference.md"))
    for cmd in cli.COMMANDS:
        assert f"\n## emir {cmd}\n" in text, cmd


def test_cli_reference_shows_every_visible_option():
    """Every option a command's parser shows in --help is on the page."""
    import argparse

    from emir import cli
    from emir.mcp_tools import capture_parser
    text = _text(os.path.join(DOCS, "cli-reference.md"))
    sections = dict(re.findall(r"\n## emir (\S+)\n(.*?)(?=\n## emir |\Z)", text, re.S))
    for cmd in cli.COMMANDS:
        p = capture_parser(cmd)
        for a in p._actions:
            if a.help is argparse.SUPPRESS or not a.option_strings:
                continue
            for o in a.option_strings:
                if o in ("-h", "--help"):
                    continue
                assert f"`{o}" in sections[cmd], (cmd, o)


# ------------------------------------------------------------------ process keys


def _schema_keys():
    from emir.schemas import SCHEMAS
    s = SCHEMAS["process"]
    props = s["properties"]

    def entry(name):
        e = props[name]["additionalProperties"]
        return set(e["properties"])
    return {"top": set(props), "layer": entry("layers"), "via": entry("vias")}


def _doc_keys():
    """The keys the reference's tables name, per section."""
    text = _text(os.path.join(DOCS, "process-files.md"))
    out, cur = {}, None
    names = {"## Top level": "top", "## A conductor layer": "layer", "## A via": "via"}
    for line in text.splitlines():
        if line.startswith("## "):
            cur = next((v for k, v in names.items() if line.startswith(k)), None)
            continue
        m = re.match(r"^\| `([a-z_0-9]+)` \|", line)
        if m and cur:
            out.setdefault(cur, set()).add(m.group(1))
    return out


def test_process_reference_names_exactly_the_schema_keys():
    want, got = _schema_keys(), _doc_keys()
    for part in want:
        assert got.get(part) == want[part], (part, sorted(want[part] ^ got.get(part, set())))


def test_process_reference_minimal_file_loads(tmp_path):
    """The page's minimal example is a valid process file."""
    from emir.engine import Process
    text = _text(os.path.join(DOCS, "process-files.md"))
    block = re.search(r"```yaml\n(.*?)```", text, re.S).group(1)
    path = tmp_path / "min.yaml"
    path.write_text(block, encoding="utf-8")
    proc = Process.load(str(path))
    assert proc.em_tref == 105.0 and set(proc.layers) == {"M1", "M2"}


# ------------------------------------------------------------------ links


@pytest.mark.parametrize("page", PAGES + ["../README.md"])
def test_relative_links_resolve(page):
    path = os.path.normpath(os.path.join(DOCS, page))
    base = os.path.dirname(path)
    bad = []
    for target in _links(path):
        if re.match(r"^[a-z][a-z0-9+.-]*:", target):
            continue                                   # http:, https:, mailto:
        file_part, _, anchor = target.partition("#")
        dest = os.path.normpath(os.path.join(base, file_part)) if file_part else path
        if not os.path.exists(dest):
            bad.append(target)
            continue
        if anchor and dest.endswith(".md") and anchor not in _anchors(dest):
            bad.append(target)
    assert not bad, f"{page}: broken links {bad}"


def test_no_code_span_with_an_escaped_pipe_in_a_table():
    """GitHub and MkDocs disagree on `a\\|b` inside a code span in a table
    (MkDocs shows the backslash), so tables keep pipes out of code spans."""
    bad = []
    for page in PAGES:
        for line in _outside_fences(_text(os.path.join(DOCS, page))):
            if line.startswith("|"):
                for span in re.findall(r"`([^`]*)`", line):
                    if "\\|" in span:
                        bad.append((page, span))
    assert not bad, bad


# ------------------------------------------------------------------ nav and pages


def test_mkdocs_nav_lists_every_page_once():
    import yaml
    with open(os.path.join(ROOT, "mkdocs.yml"), encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    def walk(node):
        if isinstance(node, str):
            yield node
        elif isinstance(node, list):
            for x in node:
                yield from walk(x)
        elif isinstance(node, dict):
            for v in node.values():
                yield from walk(v)
    nav = list(walk(cfg["nav"]))
    assert sorted(nav) == PAGES
    assert len(nav) == len(set(nav))


@pytest.mark.parametrize("page", PAGES + ["../README.md"])
def test_pages_say_screening_not_sign_off(page):
    text = _text(os.path.normpath(os.path.join(DOCS, page)))
    if page == "cli-reference.md":
        pytest.skip("the generated reference, not a presentation of results")
    assert "screening, not sign-off" in text.lower() or "is a screening tool" in text


def test_readme_opens_with_the_title_and_the_agent_line():
    lines = _text(os.path.join(ROOT, "README.md")).splitlines()
    assert lines[0] == "# EMIR"
    assert lines[2].startswith("**EM and IR-drop screening for analog layout, built to be "
                               "driven by an AI agent.**")


def test_examples_have_an_index_that_matches_the_readme():
    """examples/README.md lists every example directory, with the README's
    rows, says where 01 went (the quickstart), and repeats the quickstart as
    the README has it; AGENTS.md links the index, not a bare directory."""
    ex = os.path.join(ROOT, "examples")
    # an example is a directory with its README (a removed example's ignored
    # outputs can linger in a working tree)
    dirs = sorted(d for d in os.listdir(ex)
                  if os.path.isfile(os.path.join(ex, d, "README.md")) and re.match(r"\d\d_", d))
    index = _text(os.path.join(ex, "README.md"))
    readme = _text(os.path.join(ROOT, "README.md"))
    rows = re.findall(r"^\| \[`(\d\d_[a-z0-9_]+)`\]\(\1/\) \| (.*)$", index, re.M)
    assert [d for d, _ in rows] == dirs
    for d, rest in rows:
        assert f"| [`{d}`](examples/{d}/) | {rest}" in readme, d
    assert "no `01`" in index and _quickstart_block() in index
    assert "[examples/](examples/README.md)" in _text(os.path.join(ROOT, "AGENTS.md"))
    assert "screening, not sign-off" in index.lower()


# ------------------------------------------------------------------ the quickstart


def _quickstart_block():
    text = _text(os.path.join(ROOT, "README.md"))
    m = re.search(r"<!-- quickstart:[^\n]*-->\n```sh\n(.*?)```", text, re.S)
    assert m, "README.md has no marked quickstart block"
    return m.group(1)


def test_readme_quickstart_runs_as_written(tmp_path):
    """The README's quickstart, run from a copy of the clone's layout: the
    sample's hand numbers come out, and the report says it is screening."""
    os.symlink(os.path.join(ROOT, "emir"), tmp_path / "emir")
    shim = tmp_path / "bin" / "emir"
    shim.parent.mkdir()
    shim.write_text(f"#!/bin/sh\nexec {sys.executable} -m emir \"$@\"\n", encoding="utf-8")
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR)
    env = dict(os.environ, PATH=f"{shim.parent}{os.pathsep}{os.environ.get('PATH', '')}",
               PYTHONPATH=ROOT, PYTHONDONTWRITEBYTECODE="1")
    script = "set -e\n" + _quickstart_block()
    p = subprocess.run([shutil.which("bash") or "/bin/sh", "-c", script], cwd=tmp_path,
                       env=env, capture_output=True, text=True, timeout=300)
    assert p.returncode == 0, p.stdout[-2000:] + p.stderr[-2000:]
    assert "SELFTEST PASSED" in p.stdout + p.stderr
    assert "Metal1: widen 0.750 -> 1.255 um (x1.67)" in p.stdout
    assert "Screening, not sign-off." in p.stdout
    with open(tmp_path / "emir_out" / "summary.json", encoding="utf-8") as f:
        d = json.load(f)
    assert d["verdict"] == "FAIL"
    assert d["nets"][0]["ir_drop_mV"] == pytest.approx(54.0, rel=1e-9)
    assert (tmp_path / "emir_out" / "html" / "index.html").is_file()


# ------------------------------------------------------------------ help text the pages quote


@pytest.mark.parametrize("cmd", ["run", "check"])
def test_em_temp_help_names_the_default_the_engine_uses(cmd, tmp_path):
    """`--em-temp` defaults to the process file's `em_tref` (engine.Process);
    two help texts said `temp_reference`, and the reference page repeats them."""
    import argparse

    from emir.engine import Process
    from emir.mcp_tools import capture_parser
    a = next(x for x in capture_parser(cmd)._actions if "--em-temp" in x.option_strings)
    assert "em_tref" in a.help and "temp_reference" not in a.help
    assert a.default is None or a.default is argparse.SUPPRESS
    proc = Process({"em_tref": 90.0, "temp_reference": 30.0,
                    "layers": {"M1": {"gds": [1, 0], "sheet_res": 0.1, "jmax_avg": 1.0}}})
    assert proc.temp == 90.0


# ------------------------------------------------------------------ codes the pages cite


@pytest.mark.parametrize("page", PAGES + ["../README.md"])
def test_every_cited_code_is_a_live_code(page):
    """A code a page names is registered and not retired: a renumbered or
    retired code fails here, with the page to update."""
    from emir.errors import REGISTRY
    text = _text(os.path.normpath(os.path.join(DOCS, page)))
    cited = set(re.findall(r"EMIR-[EWI][1-9][0-9]{2}", text))
    # a short code right after a full one (`EMIR-W207` or `W308`) is cited too
    for m in re.finditer(r"EMIR-([EWI])[1-9][0-9]{2}`?(?:,| or| and| to)? `?([EWI][1-9][0-9]{2})\b",
                         text):
        cited.add("EMIR-" + m.group(2))
    dead = sorted(c for c in cited if c not in REGISTRY)
    assert not dead, f"{page}: codes not in the registry (renumbered or retired): {dead}"


# ------------------------------------------------------------------ commands


def _human_documents():
    """The Markdown a person reads that prints emir commands; the agent
    documents (AGENTS.md, agent/README.md, the playbooks, the skill) have
    their own, stricter test in tests/test_agent_layer.py."""
    import glob
    pats = ["README.md", "CONTRIBUTING.md", "SECURITY.md", "docs/*.md", "pdks/*.md",
            "blog/*.md", "examples/*/README.md", "agent/mcp/*.md", "agent/opensource/*.md",
            "agent/cadence/*.md", "agent/cadence/*/*.md"]
    return sorted({os.path.relpath(f, ROOT) for pat in pats
                   for f in glob.glob(os.path.join(ROOT, pat))})


def _command_lines(text):
    """emir command lines: every line of a fenced block (JSON and YAML
    excepted) and every inline code span that starts with `emir `."""
    sys.path.insert(0, os.path.join(ROOT, "agent", "evals"))
    try:
        import emircmd
    finally:
        sys.path.pop(0)
    lines, fenced, info, body = [], False, "", []
    for line in text.splitlines():
        m = re.match(r"^\s*(```+|~~~+)\s*([\w+-]*)", line)
        if m and not fenced:
            fenced, info, body = True, m.group(2), []
            continue
        if m and fenced:
            if info not in ("json", "yaml", "toml"):
                lines += emircmd.logical_lines("\n".join(body))
            fenced = False
            continue
        if fenced:
            body.append(line)
        else:
            lines += [c for c in re.findall(r"`([^`\n]+)`", line) if c.startswith("emir ")]
    return emircmd, lines


#: lines a page may print although they name a command the code no longer has,
#: while a rewrite of that page is pending: each must still be there -- when the
#: page drops it, take it out of here -- and nothing else may be (the blog named
#: `emir mesh` here until it was redrawn from example 06)
PENDING_BLOG_REWRITE: dict = {}


@pytest.mark.parametrize("rel", _human_documents())
def test_every_printed_emir_command_names_real_options(rel):
    """No page prints an emir command or option the code does not have. Pages
    may write synopses (`[options]`, `{a,b}`, `<dspf>`, `--period T`), so only
    the command and the option names are held to the parser here."""
    from emir import cli
    emircmd, lines = _command_lines(_text(os.path.join(ROOT, rel)))
    pending = PENDING_BLOG_REWRITE.get(rel, set())
    assert pending <= set(lines), f"{rel} no longer says {pending - set(lines)}: take it " \
        "out of PENDING_BLOG_REWRITE"
    lines = [line for line in lines if line not in pending]
    bad = []
    for line in lines:
        try:
            argv = emircmd.emir_argv(emircmd.split(line))
        except ValueError:
            continue
        if not argv or argv[0].startswith("-") or emircmd.PLACEHOLDER.fullmatch(argv[0]):
            continue
        if argv[0] not in cli.COMMANDS:
            if len(argv) == 1 and not re.fullmatch(r"[a-z][a-z-]*", argv[0]):
                continue                              # prose, not a command
            bad.append((line, f"unknown command {argv[0]!r}"))
            continue
        unknown = emircmd.unknown_options([argv[0]] + [a.strip("[]") for a in argv[1:]])
        if unknown:
            bad.append((line, unknown))
    assert not bad, f"{rel}: {bad}"


# ------------------------------------------------------------------ former known issues


def _sample():
    d = os.path.join(ROOT, "emir", "data", "sample")
    return os.path.join(d, "rail.dspf"), os.path.join(d, "process.yaml")


def _emir(*args, cwd=None):
    return subprocess.run([sys.executable, "-m", "emir", *args, "--format", "json"],
                          capture_output=True, text=True, cwd=cwd or ROOT, timeout=300)


def _documented(phrase):
    pages = [os.path.join(DOCS, p) for p in PAGES] + [os.path.join(ROOT, "README.md")]
    return [os.path.basename(p) for p in pages if phrase in _text(p)]


def test_former_known_issue_currents_file_works(tmp_path):
    """`--currents FILE` on run/check was a known issue in the docs until it
    was fixed; it works now, and no page says otherwise."""
    dspf, proc = _sample()
    f = tmp_path / "currents.json"
    f.write_text(json.dumps({"VDD": 0.004, "MP0:s": -0.001, "MP1:s": -0.001,
                             "MP2:s": -0.001, "MP3:s": -0.001}, indent=1), encoding="utf-8")
    works = _emir("check", dspf, "--process", proc, "--currents", str(f)).returncode == 0
    pages = _documented("takes JSON text, not a file name") + _documented("take the JSON text itself")
    assert works, "--currents FILE fails again"
    assert not pages, f"--currents FILE works: drop the known issue from {pages}"


def test_the_validation_page_lists_every_example_with_golden_results():
    """docs/method.md's table of the examples with golden results (it once
    stopped at 10_mcp_agent while 11 compared against goldens too). Every
    example with an `expected/` directory has a row naming each of its files."""
    ex = os.path.join(ROOT, "examples")
    with open(os.path.join(DOCS, "method.md"), encoding="utf-8") as f:
        text = f.read()
    start = text.index("## The examples, with golden results")
    section = text[start:text.index("\n## ", start + 1)]
    rows = {m.group(1): m.group(0) for m in
            re.finditer(r"^\| \[`([0-9a-z_]+)`\]\(\.\./examples/\1/\) \|.*$", section, re.M)}
    for name in sorted(os.listdir(ex)):
        exp = os.path.join(ex, name, "expected")
        if not os.path.isdir(exp):
            continue
        assert name in rows, f"docs/method.md: no row for examples/{name}"
        if "`expected/*.json`" in rows[name]:
            continue
        for f in sorted(os.listdir(exp)):
            assert f"`expected/{f}`" in rows[name], (name, f)
