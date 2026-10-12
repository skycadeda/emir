"""The agent layer: AGENTS.md, the playbooks, the skill, agent/README.md.

The documents an agent reads are tested like code:

* every emir command they print (fenced blocks and inline code) parses with
  emir's own argparse parser -- no invented option survives;
* every worked example (a fenced block preceded by ``<!-- test: run -->``)
  runs as printed from a copy of the repository's examples, with the exit
  status its ``# exit N`` comment gives (0 otherwise), and every
  ``<!-- test: expect -->`` JSON excerpt is a subset of what the command
  before it printed (numbers to 0.5 %, the rounding the docs use);
* every code they cite is a live code of the registry, every relative link
  resolves, every playbook has the sections an agent looks for;
* ``.claude/skills/emir`` is a relative symlink to ``agent/skills/emir``.

Markers: ``<!-- test: run requires=xyce,klayout -->`` skips a block when a
tool (xyce, ngspice, magic on PATH) or a Python package (klayout,
matplotlib, mcp) is missing; ``<!-- test: tool-call -->`` before a JSON block
checks an MCP tool call (`{"name", "arguments"}`) against the tool's
parameters.
"""
from __future__ import annotations

import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
EVALS = os.path.join(ROOT, "agent", "evals")
for p in (ROOT, EVALS):
    if p not in sys.path:
        sys.path.insert(0, p)

import emircmd  # noqa: E402

PLAYBOOK_DIR = os.path.join(ROOT, "agent", "playbooks")
PLAYBOOKS = ["00-preflight.md", "01-screen-a-block.md", "02-process-file.md",
             "03-open-source-flow.md", "04-cadence-flow.md"]
SECTIONS = ["When to use", "Inputs", "Commands", "Decision points", "Success criteria",
            "What to report", "Failure modes"]
DOCS = (["AGENTS.md", "CLAUDE.md", "agent/README.md", "agent/CONTRACT.md",
         "agent/skills/emir/SKILL.md", "agent/evals/README.md"]
        + [f"agent/playbooks/{p}" for p in PLAYBOOKS])

#: the comment that marks a command line as a usage error shown on purpose
ON_PURPOSE = "a usage error on purpose"
MARKER = re.compile(r"^<!--\s*test:\s*(\S+)(.*?)-->\s*$")
FENCE = re.compile(r"^(```+|~~~+)\s*([\w+-]*)\s*$")


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


class Block:
    def __init__(self, info, text, marker, args, line):
        self.info, self.text, self.marker, self.args, self.line = info, text, marker, args, line

    def requires(self):
        m = re.search(r"requires=([\w,]+)", self.args or "")
        return m.group(1).split(",") if m else []


def blocks(text):
    """Fenced blocks of a Markdown text, each with the `<!-- test: ... -->`
    marker on the line before its fence (blank lines between allowed)."""
    out, lines, i, pending = [], text.splitlines(), 0, None
    while i < len(lines):
        line = lines[i]
        m = MARKER.match(line.strip())
        if m:
            pending = (m.group(1), m.group(2).strip(), i + 1)
            i += 1
            continue
        f = FENCE.match(line.strip())
        if f:
            fence, info, start = f.group(1), f.group(2), i + 1
            body = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith(fence):
                body.append(lines[i])
                i += 1
            mk, args = (pending[0], pending[1]) if pending else (None, None)
            out.append(Block(info, "\n".join(body), mk, args, start))
            pending = None
        elif line.strip():
            pending = None
        i += 1
    return out


def inline_code(text):
    """The inline code spans outside fenced blocks."""
    out, in_fence = [], False
    for line in text.splitlines():
        if FENCE.match(line.strip()):
            in_fence = not in_fence
            continue
        if not in_fence:
            out += re.findall(r"`([^`\n]+)`", line)
    return out


# ------------------------------------------------------------------ files


def test_the_playbooks_are_the_fixed_set():
    assert sorted(os.listdir(PLAYBOOK_DIR)) == sorted(PLAYBOOKS)


def test_claude_md_imports_agents_md():
    assert "@AGENTS.md" in _read("CLAUDE.md").split()


def test_agents_md_is_short_and_operational():
    text = _read("AGENTS.md")
    assert len(text.splitlines()) <= 90
    for p in PLAYBOOKS:
        assert f"agent/playbooks/{p}" in text, p
    for needle in ("emir --version", "emir info --format json", "emir selftest --format json",
                   "emir next", "--format json", "next_steps", "emir report",
                   "Screening, not sign-off.", "EMIR-E590"):
        assert needle in text, needle
    for status in ("0 done", "1 a FAIL verdict", "2 bad input", "3 a required tool", "70",
                   "130", "143"):
        assert status in text, status
    guard = text.split("## 4. Guardrails")[1].split("## 5.")[0].lower()
    for rule in ("never invent a limit", "never call a result sign-off", "virtuoso",
                 "never edit the layout", "licensed tools", "long runs", "concurrency",
                 "never send design data"):
        assert rule in guard, rule


@pytest.mark.parametrize("name", PLAYBOOKS)
def test_every_playbook_has_the_sections(name):
    text = _read(f"agent/playbooks/{name}")
    heads = re.findall(r"^## (.+?)\s*$", text, flags=re.M)
    for s in SECTIONS:
        assert any(h.startswith(s) for h in heads), (name, s, heads)
    assert text.startswith(f"# {name[:2]} "), name
    assert "Screening, not sign-off." in text, name


@pytest.mark.parametrize("rel", DOCS)
def test_relative_links_resolve(rel):
    text = _read(rel)
    base = os.path.dirname(os.path.join(ROOT, rel))
    for target in re.findall(r"\]\(([^)\s]+)\)", text):
        if re.match(r"^[a-z]+://|^mailto:|^#", target):
            continue
        path = target.split("#")[0]
        assert os.path.exists(os.path.join(base, path)), (rel, target)


@pytest.mark.parametrize("rel", DOCS)
def test_cited_codes_are_live(rel):
    from emir.errors import REGISTRY, RETIRED
    for code in set(re.findall(r"EMIR-[EWI]\d{3}", _read(rel))):
        assert code in REGISTRY and code not in RETIRED, (rel, code)


@pytest.mark.parametrize("rel", DOCS)
def test_every_printed_emir_command_parses(rel):
    """Fenced and inline: each emir command line parses with emir's parser
    (placeholders such as <dspf> stand for a word)."""
    text = _read(rel)
    lines = []
    for b in blocks(text):
        if b.info in ("json", "yaml", "toml", "markdown", "md"):
            continue
        lines += emircmd.logical_lines(b.text)
    from emir import cli
    for c in inline_code(text):
        if not c.startswith(("emir ", "bin/emir ")):
            continue
        words = c.split()
        if len(words) == 2 and not words[1].startswith("-"):
            # prose naming a command (`emir extract`), not a command line
            assert words[1] in cli.COMMANDS, (rel, c)
            continue
        lines.append((c, True))
    n = 0
    for line in lines:
        line, inline = line if isinstance(line, tuple) else (line, False)
        if "..." in line:
            continue
        argv, err = emircmd.check_line(line, placeholders=True)
        if argv is None:
            continue
        n += 1
        if ON_PURPOSE in line:
            assert err, (rel, line, "parses, though marked as a usage error")
            continue
        if inline and err and not re.search(r"unrecognized arguments|invalid choice|"
                                            r"unknown command", err):
            # prose quoting part of a command (`emir probe --names-out`): no
            # value or required argument, but its options must exist
            assert not emircmd.unknown_options(argv), (rel, line)
            continue
        assert err is None, (rel, line, err)
    if rel not in ("CLAUDE.md", "agent/evals/README.md"):
        assert n, f"{rel} prints no emir command"


def test_the_contract_is_the_generated_one():
    """agent/CONTRACT.md is written from the code by scripts/gen_contract.py
    (tests/test_contract.py holds it to the code); the agent's entry points
    link it."""
    assert _read("agent/CONTRACT.md").startswith(
        "# EMIR agent contract\n\nGenerated from the code by `scripts/gen_contract.py`")
    for rel in ("AGENTS.md", "agent/README.md"):
        assert "CONTRACT.md" in _read(rel), rel


# ------------------------------------------------------------------ worked examples


def _have(req):
    import importlib.util
    if req in ("klayout", "matplotlib", "mcp", "gdstk"):
        return importlib.util.find_spec(req) is not None
    exe = {"xyce": "Xyce"}.get(req, req)
    return shutil.which(exe) is not None


def _tracked(prefix):
    """The repository's files under `prefix` (git ls-files, or a walk)."""
    try:
        p = subprocess.run(["git", "-C", ROOT, "ls-files", "-z", "--cached", "--others",
                            "--exclude-standard", prefix],
                           capture_output=True, text=True, check=True)
        files = [f for f in p.stdout.split("\0") if f]
        if files:
            return files
    except (OSError, subprocess.CalledProcessError):
        pass
    out = []
    for d, dirs, names in os.walk(os.path.join(ROOT, prefix)):
        dirs[:] = [x for x in dirs if x not in ("out", "emir_out", "__pycache__")]
        out += [os.path.relpath(os.path.join(d, n), ROOT) for n in names
                if not n.endswith((".pyc", ".pyo"))]
    return out


#: what a worked example may read, copied into the workspace it runs in
WORKSPACE = ["emir/data/sample", "examples", "pdks", "agent/cadence", "agent/opensource",
             "agent/mcp", "tests/data/magic_tiny"]


def workspace(tmp_path):
    ws = tmp_path / "ws"
    for prefix in WORKSPACE:
        for rel in _tracked(prefix):
            dst = ws / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(os.path.join(ROOT, rel), dst)
    return ws


def _env():
    env = dict(os.environ)
    env["PATH"] = os.path.dirname(sys.executable) + os.pathsep + env.get("PATH", "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("EMIR_DEBUG", None)
    return env


def subset(exp, act, where="$"):
    """None when `exp` is contained in `act`; else where they differ."""
    if isinstance(exp, dict):
        if not isinstance(act, dict):
            return f"{where}: expected an object, got {act!r}"
        for k, v in exp.items():
            if k not in act:
                return f"{where}.{k}: missing"
            d = subset(v, act[k], f"{where}.{k}")
            if d:
                return d
        return None
    if isinstance(exp, list):
        if exp == [] and act != []:
            return f"{where}: expected an empty list, got {act!r}"
        if not isinstance(act, list) or len(act) < len(exp):
            return f"{where}: expected at least {len(exp)} items, got {act!r}"
        for i, v in enumerate(exp):
            d = subset(v, act[i], f"{where}[{i}]")
            if d:
                return d
        return None
    if isinstance(exp, (int, float)) and not isinstance(exp, bool):
        if isinstance(act, (int, float)) and not isinstance(act, bool) and \
                math.isclose(exp, act, rel_tol=5e-3, abs_tol=1e-9):
            return None
        return f"{where}: expected {exp!r}, got {act!r}"
    return None if exp == act else f"{where}: expected {exp!r}, got {act!r}"


def _exit_of(line):
    m = re.search(r"#\s*exit\s+(\d+)\b", line)
    return int(m.group(1)) if m else 0


RUNNABLE = [p for p in PLAYBOOKS if os.path.isfile(os.path.join(PLAYBOOK_DIR, p))
            and any(b.marker == "run" for b in blocks(_read(f"agent/playbooks/{p}")))]


def run_block(lines, ws, env, tmp) -> list:
    """Run a block's command lines as one script in one shell, as a person
    pasting the block (or an agent whose shell keeps its working directory)
    runs them: a `cd` in one line holds for the next. Each line's stdout,
    stderr and exit status are kept apart; the script stops at the first
    line whose status is not the one it declares (`# exit N`, default 0).
    Returns [(line, status, stdout, stderr)] for the lines that ran."""
    tmp = os.path.abspath(str(tmp))
    os.makedirs(tmp, exist_ok=True)
    script = ["set -o pipefail"]
    for i, line in enumerate(lines):
        o, e, r = (os.path.join(tmp, f"{x}{i}") for x in "oer")
        script += [f"{{ {line}", f"}} > {shlex.quote(o)} 2> {shlex.quote(e)}",
                   f"echo $? > {shlex.quote(r)}",
                   f'[ "$(cat {shlex.quote(r)})" = "{_exit_of(line)}" ] || exit 97']
    subprocess.run(["bash", "-c", "\n".join(script)], cwd=ws, env=env,
                   capture_output=True, text=True, timeout=600 * max(len(lines), 1))
    out = []
    for i, line in enumerate(lines):
        r = os.path.join(tmp, f"r{i}")
        if not os.path.isfile(r):
            break
        with open(r, encoding="utf-8") as f:
            status = int(f.read().strip() or -1)
        with open(os.path.join(tmp, f"o{i}"), encoding="utf-8", errors="replace") as f:
            so = f.read()
        with open(os.path.join(tmp, f"e{i}"), encoding="utf-8", errors="replace") as f:
            se = f.read()
        out.append((line, status, so, se))
    return out


def test_a_block_runs_in_one_shell(tmp_path):
    """A `cd` in a line holds for the next, as in the person's shell: the
    playbook's `cd emir_out/mesh && Xyce ...` once left the next line's
    relative paths pointing nowhere, unseen by a runner that gave every line
    a fresh shell."""
    ws = tmp_path / "ws"
    (ws / "sub").mkdir(parents=True)
    ran = run_block(["cd sub && pwd", "pwd", "false  # exit 1", "true"], ws, _env(),
                    tmp_path / "io")
    assert [r[1] for r in ran] == [0, 0, 1, 0]
    assert ran[1][2].strip().endswith("/ws/sub")
    ran = run_block(["false", "true"], ws, _env(), tmp_path / "io2")
    assert [r[1] for r in ran] == [1]         # stopped at the first wrong status


@pytest.mark.example
@pytest.mark.parametrize("name", RUNNABLE)
def test_worked_examples_run_as_printed(name, tmp_path):
    ws = workspace(tmp_path)
    env, last, ran, skipped = _env(), None, 0, False
    for k, b in enumerate(blocks(_read(f"agent/playbooks/{name}"))):
        if b.marker == "run":
            skipped = any(not _have(r) for r in b.requires())
            if skipped:
                last = None
                continue
            lines = emircmd.logical_lines(b.text)
            for line in lines:
                argv, err = emircmd.check_line(line)
                assert (err is not None) == (ON_PURPOSE in line), (name, line, err)
            done = run_block(lines, ws, env, tmp_path / f"io{k}")
            for line, status, so, se in done:
                assert status == _exit_of(line), (name, b.line, line, status, so[-2000:],
                                                  se[-3000:])
            assert len(done) == len(lines), (name, b.line, "stopped", done[-1:])
            ran += len(done)
            p_stdout = done[-1][2] if done else ""
            try:
                last = json.loads(p_stdout) if p_stdout.strip() else None
            except json.JSONDecodeError:
                last = None
        elif b.marker == "expect":
            if skipped:
                continue        # the block before it needs a tool not installed here
            assert last is not None, (name, b.line, "the command before printed no JSON")
            d = subset(json.loads(b.text), last)
            assert d is None, (name, b.line, d)
        elif b.marker == "tool-call":
            _check_tool_call(json.loads(b.text), name, b.line)
    if not ran:
        pytest.skip("every worked example needs a tool that is not installed here")


def _check_tool_call(call, name, line):
    from emir import mcp_tools
    tool = call["name"]
    spec = mcp_tools.specs().get(tool)
    assert spec is not None, (name, line, tool)
    args = {k: v for k, v in call.get("arguments", {}).items() if k != mcp_tools.CONFIRM}
    unknown = set(args) - {p.name for p in spec.params}
    assert not unknown, (name, line, tool, unknown)
    if tool == mcp_tools.GENERIC:
        argv, err = emircmd.check_line(args["command_line"])
        assert argv is not None and err is None, (name, line, call, err)
        return
    argv = mcp_tools.to_argv(spec, args)
    err = emircmd.check_argv([*spec.command.split(), *argv])
    assert err is None, (name, line, call, err)


@pytest.mark.parametrize("name", PLAYBOOKS)
def test_tool_calls_name_real_parameters(name):
    for b in blocks(_read(f"agent/playbooks/{name}")):
        if b.marker == "tool-call":
            _check_tool_call(json.loads(b.text), name, b.line)


# ------------------------------------------------------------------ skill, MCP


def test_the_skill_is_thin_and_simulator_neutral():
    text = _read("agent/skills/emir/SKILL.md")
    m = re.match(r"^---\n(.*?)\n---\n", text, flags=re.S)
    assert m, "SKILL.md needs YAML frontmatter"
    import yaml
    front = yaml.safe_load(m.group(1))
    assert front["name"] == "emir"
    desc = front["description"]
    assert desc.rstrip().endswith("Screening, not sign-off.")
    assert len(desc) <= 1024
    for sim in ("Spectre", "Xyce", "ngspice"):
        assert sim in desc
    body = text[m.end():]
    assert "AGENTS.md" in body and "agent/playbooks" in body and "emir next" in body
    assert len(text.splitlines()) <= 60


def test_claude_code_finds_the_skill_through_a_relative_symlink():
    link = os.path.join(ROOT, ".claude", "skills", "emir")
    if not os.path.islink(link):
        pytest.skip("not a checkout with symlinks (an sdist copies the files)")
    target = os.readlink(link)
    assert not os.path.isabs(target), target
    assert os.path.realpath(link) == os.path.realpath(os.path.join(ROOT, "agent", "skills",
                                                                   "emir"))
    assert os.path.isfile(os.path.join(link, "SKILL.md"))


def test_mcp_serves_agents_md_and_every_playbook_but_no_eval():
    from emir import mcp_server
    uris = {r["uri"] for r in mcp_server.resources(ROOT)}
    assert "emir://doc/AGENTS.md" in uris and "emir://doc/agent/README.md" in uris
    for p in PLAYBOOKS:
        assert f"emir://doc/agent/playbooks/{p}" in uris, p
    assert not [u for u in uris if "evals" in u]


# ------------------------------------------------------------------ the name


@pytest.mark.parametrize("rel", ["AGENTS.md", "agent/README.md", "agent/skills/emir/SKILL.md"]
                         + [f"agent/playbooks/{p}" for p in PLAYBOOKS])
def test_the_tool_is_emir_in_prose_and_the_command_in_backticks(rel):
    """The convention of the README and the docs: "EMIR" names the tool in
    prose, `emir` (in backticks) the command; a bare lowercase "emir" in a
    sentence mixes the two."""
    text = _read(rel)
    text = re.sub(r"\A---\n.*?\n---\n", lambda m: "\n" * m.group(0).count("\n"), text,
                  flags=re.S)                                   # the skill's front matter
    text = re.sub(r"^(```|~~~).*?^\1\s*$", lambda m: "\n" * m.group(0).count("\n"), text,
                  flags=re.S | re.M)                            # fenced blocks
    text = re.sub(r"`[^`]*`", lambda m: "``" + "\n" * m.group(0).count("\n"), text)
    text = re.sub(r"\]\([^)]*\)|https?://\S+", "]()", text)     # link targets, URLs
    bare = [text[:m.start()].count("\n") + 1
            for m in re.finditer(r"(?<![\w/.\-])emir(?![\w/\-.:])", text)]
    assert not bare, f"{rel}: bare 'emir' in prose on lines {bare}"


def test_every_planning_input_is_named_for_emir_next():
    """`emir next` plans after a run from --run-summary: AGENTS.md's "Pass
    every input" list names it (it once named none), and every name in it is
    an option `emir next` takes (--dynamic and --em-basis went with
    decap-aware EM; --vdd, --testbench and --abstract with cc-grid and the
    chip-level hierarchy); AGENTS.md and playbook 01 say a simulated run is
    rcc, and name no removed method."""
    import re

    from emir import mcp_tools
    text = " ".join(_read("AGENTS.md").split())
    m = re.search(r"Pass \*\*every\*\* input the person gave \(([^()]*)\)", text)
    assert m, "AGENTS.md: the list of emir next's inputs"
    for opt in ("--dspf", "--process", "--cc-sim", "--currents", "--period", "--run-summary"):
        assert f"`{opt}`" in m.group(1), opt
    takes = {o for a in mcp_tools.capture_parser("next")._actions for o in a.option_strings}
    for opt in re.findall(r"`(--[a-z-]+)`", m.group(1)):
        assert opt in takes, opt
    for rel in ("AGENTS.md", "agent/playbooks/01-screen-a-block.md"):
        pb = " ".join(_read(rel).split())
        assert "every branch current" in pb and "rcc" in pb, rel
        assert "cc-grid" not in pb and "EMIR-E530" not in pb, rel
    # the skill, which an agent outside a clone loads alone, lists the same
    skill = " ".join(_read("agent/skills/emir/SKILL.md").split())
    s = re.search(r"Pass \*\*every\*\* input the person gave \(([^()]*)\)", skill)
    assert s, "SKILL.md: the list of emir next's inputs"
    assert re.findall(r"`(--[a-z-]+)`", s.group(1)) == re.findall(r"`(--[a-z-]+)`",
                                                                  m.group(1))


def test_every_reason_for_exit_1_is_named():
    """Exit 1 is a FAIL verdict, with --fail-on-violation (the registry's
    wording; an EM basis not computed went with decap-aware EM, an IR budget
    FAIL with the chip-level hierarchy): the contract's summaries name it,
    and no other."""
    from emir.errors import EXIT_CODES, EXIT_VIOLATION
    want = EXIT_CODES[EXIT_VIOLATION]
    assert "a FAIL verdict" in want
    assert "EM basis" not in want and "IR budget" not in want
    for rel in ("AGENTS.md",):
        text = " ".join(_read(rel).split())
        line = text.split("**Exit status.**", 1)[1].split(" 2 bad input", 1)[0]
        for what in ("a FAIL verdict", "--fail-on-violation"):
            assert what in line, (rel, what)
        assert "EM basis" not in line and "IR budget" not in line, rel


def test_every_mcp_go_ahead_is_in_the_go_ahead_lists():
    """The MCP server asks the person (EMIR-E590) before a long run
    (`mcp_tools.GATE_WHEN`); its gates on `dynamic`, `dyn_steps`, `em_basis`
    and `self_consistent` went with those options: no page that lists the
    go-ahead rule names them any more."""
    from emir.mcp_tools import GATE_WHEN
    gates = " ".join(w for ws in GATE_WHEN.values() for w in ws)
    for gone in ("dyn_steps", "em_basis", "self_consistent", "dynamic"):
        assert gone not in gates, gone
    for rel in ("agent/mcp/README.md", "SECURITY.md", "agent/playbooks/00-preflight.md"):
        text = " ".join(_read(rel).split())
        assert "long run" in text or "run long" in text, rel
        for gone in ("`dyn_steps`", "`em_basis`", "`self_consistent`"):
            assert gone not in text, (rel, gone)


def test_the_changelog_counts_the_eval_cards():
    n = len([c for c in os.listdir(os.path.join(ROOT, "agent", "evals", "cards"))
             if c.endswith(".yaml")])
    text = " ".join(_read("CHANGELOG.md").split())
    said = re.findall(r"`agent/evals/`: (\d+) task cards", text)
    assert said == [str(n)], (said, n)
