"""agent/CONTRACT.md, the agent contract, is generated from the code by
scripts/gen_contract.py: it is current, it covers every command, document,
exit status, code area and MCP tool, it does not depend on the machine that
wrote it, and pre-commit keeps every generated file current."""
import importlib.util
import os
import re
import subprocess
import sys

import yaml
from _helpers import ROOT

from emir import cli, mcp_tools, schemas
from emir.errors import AREAS, DECADES, EXIT_CODES, REGISTRY

GEN = os.path.join(ROOT, "scripts", "gen_contract.py")
CONTRACT = os.path.join(ROOT, "agent", "CONTRACT.md")


def _gen(*args, env=None, cwd=None):
    return subprocess.run([sys.executable, GEN, *args], capture_output=True, text=True,
                          env=env, cwd=cwd)


def test_the_contract_is_current():
    p = _gen("--check")
    assert p.returncode == 0, p.stdout + p.stderr


def test_a_stale_contract_is_caught(tmp_path):
    spec = importlib.util.spec_from_file_location("gen_contract", GEN)
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    gen.OUT = str(tmp_path / "CONTRACT.md")
    (tmp_path / "CONTRACT.md").write_text("# an old contract\n")
    before = {k: os.environ.get(k) for k in gen._SITE_VARS}
    assert gen.main(["--check"]) == 1
    assert gen.main([]) == 0 and gen.main(["--check"]) == 0
    assert open(gen.OUT).read() == open(CONTRACT).read()
    # in process, the caller's setting is put back
    assert {k: os.environ.get(k) for k in gen._SITE_VARS} == before


def test_the_contract_does_not_depend_on_the_machine(tmp_path):
    """A site file, tool and setting variables change parser defaults and
    tool lookups; the contract is written without them."""
    (tmp_path / "emir_site.yaml").write_text("process: /p/x.yaml\nlib: SITELIB\n")
    env = {**os.environ, "EMIR_PROCESS": "/q/y.yaml", "XYCE_BIN": "/q/Xyce",
           "EMIR_LIB": "ENVLIB", "PYTHONPATH": ROOT}
    p = _gen("--check", env=env, cwd=str(tmp_path))
    assert p.returncode == 0, p.stderr
    text = open(CONTRACT, encoding="utf-8").read()
    assert ROOT not in text and os.path.expanduser("~") not in text


def test_the_contract_covers_the_code():
    text = open(CONTRACT, encoding="utf-8").read()
    for c in cli.COMMANDS:
        assert f"### emir {c}\n" in text, c
    for n in schemas.names():
        assert f"`{n}`" in text or f"emir schema {n}`" in text, n
    for status, meaning in EXIT_CODES.items():
        assert f"| {status} | {meaning} |" in text
    for area, what in AREAS.items():
        assert f"| {area}xx | {what} |" in text
    for who, (lo, hi) in DECADES.items():
        assert f"| x{lo:02d}-x{hi:02d} | {who} |" in text
    assert f"{len(REGISTRY)} codes are registered" in text
    for name in mcp_tools.specs():
        assert f"| `{name}` |" in text, name
    # the rules an agent leans on, stated where they live
    for needle in ("EMIR-E590", "EMIR-E591", "EMIR-E592", '"shell": true',
                   "keeps the caller's `--format`", "Screening, not sign-off.",
                   "area 8 is retired whole",
                   "| x40-x49 | the magic route"):
        assert needle in text, needle
    # the commands' options are the parsers' (one line each)
    run = text.split("### emir run\n", 1)[1].split("\n### ", 1)[0]
    for opt in ("--summary-out", "--fail-on-violation", "--currents", "--mode"):
        assert re.search(rf"^- `{re.escape(opt)}`", run, re.M), opt
    for opt in ("--abstract", "--vdd", "--testbench", "--guard-sim"):
        assert not re.search(rf"^- `{re.escape(opt)}`", run, re.M), opt


def test_pre_commit_checks_every_generated_file():
    cfg = yaml.safe_load(open(os.path.join(ROOT, ".pre-commit-config.yaml")))
    hooks = {h["id"]: h for r in cfg["repos"] for h in r["hooks"]}
    for gen in ("gen_errors_md", "gen_schemas", "gen_cadence_skill", "gen_contract"):
        h = hooks[gen.replace("_", "-")]
        assert h["entry"] == f"python scripts/{gen}.py --check", h
        assert h["pass_filenames"] is False and h["files"].startswith("^(emir/|scripts/")
        assert os.path.isfile(os.path.join(ROOT, "scripts", f"{gen}.py"))


def test_exit_one_names_every_reason_the_run_has():
    """`emir run --fail-on-violation` exits 1 on a FAIL verdict (`cli._cmd_run`,
    AGENTS.md section 3), and on nothing else since the IR budgets left with
    the chip-level hierarchy (1.1). The registry's exit table, `emir --help`,
    this contract and the CLI reference say so."""
    from emir.errors import EXIT_VIOLATION
    with open(CONTRACT, encoding="utf-8") as f:
        contract = f.read()
    with open(os.path.join(ROOT, "docs", "cli-reference.md"), encoding="utf-8") as f:
        ref = f.read()
    for where, text in (("EXIT_CODES", EXIT_CODES[EXIT_VIOLATION]), ("emir --help", cli.__doc__),
                        ("CONTRACT.md", contract), ("cli-reference.md", ref)):
        flat = " ".join(text.split())
        assert "FAIL verdict" in flat and "IR budget" not in flat, where
        assert "EM basis" not in flat, where
    assert "`run`: 1 on a FAIL verdict, only with `--fail-on-violation`" in \
        " ".join(contract.split())
