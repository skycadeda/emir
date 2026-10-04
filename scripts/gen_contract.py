#!/usr/bin/env python3
"""Write agent/CONTRACT.md, the agent contract, from the code: the commands
and their options (the CLI's own parsers), the JSON documents and their
schemas (`emir/schemas.py`), the code areas and decades and the exit
statuses (`emir/errors.py`), the next-step rules (`emir/output.py`,
`emir/plan.py`) and the MCP mapping (`emir/mcp_tools.py`). Never edit the
contract by hand: change the code, then run this.

    python scripts/gen_contract.py            # write it
    python scripts/gen_contract.py --check    # exit 1 if it is stale

`tests/test_contract.py` runs the check. The contract is built in a clean
setting (no site file, no tool or setting variables), so it says what emir
does anywhere, not on the machine that ran this.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
OUT = os.path.join(ROOT, "agent", "CONTRACT.md")

#: environment variables that change a parser's defaults or a tool lookup
_SITE_VARS = ("EMIR_SITE", "EMIR_PROCESS", "EMIR_LIB", "EMIR_WORK", "EMIR_MAGICRC",
              "SPECTRE_BIN", "XYCE_BIN", "NGSPICE_BIN", "MAGIC_BIN", "VIRTUOSO_BIN",
              "PVS_BIN", "QRC_BIN")


def _clean_site():
    """No site file and no site variables: the defaults are emir's own.
    Returns what `_restore` needs to put the caller's setting back."""
    saved = {k: os.environ.get(k) for k in _SITE_VARS}
    for k in _SITE_VARS:
        os.environ.pop(k, None)
    fd, empty = tempfile.mkstemp(suffix=".yaml", prefix="emir_site_")
    os.close(fd)
    os.environ["EMIR_SITE"] = empty
    _forget_site()
    return empty, saved


def _restore(empty, saved):
    os.unlink(empty)
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    _forget_site()


def _forget_site():
    """Drop what emir cached from the site (its site file, and the MCP tools
    whose parsers took their defaults from it) when emir is imported in this
    process."""
    site = sys.modules.get("emir.site")
    if site is not None:
        site._cache = None
    tools = sys.modules.get("emir.mcp_tools")
    if tools is not None:
        tools._SPECS = None


def _first_sentence(text: str) -> str:
    """The first sentence of `text` (to the first ". ")."""
    t = " ".join((text or "").split())
    if ". " in t:
        t = t[:t.index(". ") + 1]
    return t if t.endswith(".") or not t else t + "."


def _sentence(text: str) -> str:
    """`text` on one line, ending with a full stop."""
    t = " ".join((text or "").split())
    return t if not t or t.endswith((".", ")", "]")) else t + "."


def _help(action, parser) -> str:
    h = action.help or ""
    if "%" in h:
        try:
            h = h % {**vars(action), "prog": parser.prog}
        except (KeyError, TypeError, ValueError):
            pass
    return " ".join(h.split())


def _option(action, parser) -> str | None:
    """One option of a parser, as a line of the contract."""
    if action.help == argparse.SUPPRESS or isinstance(action, (argparse._HelpAction,
                                                                argparse._VersionAction)):
        return None
    if action.option_strings:
        name = ", ".join(f"`{s}`" for s in action.option_strings)
    else:
        name = f"`<{action.metavar or action.dest}>`"
    kind = ""
    if isinstance(action, argparse.BooleanOptionalAction):
        kind = "flag (and its `--no-` form; absent: detected)"
    elif isinstance(action, (argparse._StoreTrueAction, argparse._StoreConstAction,
                             argparse._CountAction)):
        kind = "flag"
    elif action.choices:
        kind = " | ".join(str(c) for c in action.choices)
    elif action.option_strings:
        kind = str(action.metavar or action.dest.upper())
    many = ("repeatable" if isinstance(action, argparse._AppendAction) else
            "one or more" if action.nargs == "+" else
            "any number" if action.nargs == "*" else "")
    req = (action.required if action.option_strings
           else action.nargs not in ("?", "*"))
    d = action.default
    show_default = (d not in (None, False, "", [], argparse.SUPPRESS)
                    and isinstance(d, (int, float, str)) and not isinstance(d, bool))
    bits = [", ".join(x for x in (kind, many) if x)] if (kind or many) else []
    if req:
        bits.append("required")
    if show_default:
        bits.append(f"default {d}")
    text = _sentence(_help(action, parser))
    return f"- {name}" + (f" ({'; '.join(bits)})" if bits else "") + \
        (f": {text}" if text else "")


def _commands(out: list):
    from emir import cli, mcp_tools
    out += ["## 1. Commands", "",
            f"{len(cli.COMMANDS)} commands. Each takes `--format text|json` (default text; "
            "`emir report` takes md|json|text, md being its text). Options are never "
            "abbreviated (a prefix is a usage error, EMIR-E507), and `@FILE` reads "
            "arguments from FILE, one per line. `emir <command> --help` prints the full help "
            "(`emir.help/1` in JSON mode). `--format` and `--help` are left out of the lists "
            "below."
            + "".join(f" `emir {c}` is made of sub-commands ("
                      + ", ".join(f"`emir {c} {x}`" for x in subs)
                      + "), each with its own options." for c, subs in
                      sorted(cli.SUBCOMMANDS.items())), ""]
    names = []
    for c in sorted(cli.COMMANDS):
        names += [c] + [f"{c} {x}" for x in cli.SUBCOMMANDS.get(c, ())]
    for name in names:
        parser = mcp_tools.capture_parser(name)
        out.append(f"### emir {name}")
        out.append("")
        if parser is None:
            out += ["(no parser to read)", ""]
            continue
        desc = (parser.description or "").strip().split("\n\n")[0]
        if desc:
            out += [_sentence(desc), ""]
        lines = [x for x in (_option(a, parser) for a in parser._actions
                             if "--format" not in a.option_strings) if x]
        out += (lines or ["No options."]) + [""]


def _documents(out: list):
    from emir import mcp_tools, schemas
    from emir.cli import COMMANDS
    from emir.cli import SUBCOMMANDS
    printed = {}
    for c in sorted(COMMANDS):
        for name in [f"{c} {x}" for x in SUBCOMMANDS[c]] if c in SUBCOMMANDS else [c]:
            for d in (mcp_tools._outputs(name) or []):
                printed.setdefault(d, []).append(name)
    docs, inputs = [], []
    for n in schemas.names():
        s = schemas.get(n)
        const = ((s.get("properties") or {}).get("schema") or {}).get("const")
        (docs if const else inputs).append((n, s, const))
    out += ["## 2. JSON output and documents", "",
            "- With `--format json`, stdout carries exactly ONE JSON document; everything "
            "else (progress, tables, logs) goes to stderr. Every document starts with "
            "`schema` (`emir.<name>/1`) and `emir_version`.",
            "- A value emir could not compute (NaN, Inf) is written as null: every computed "
            "number may be null, and a reader reports it as \"not a number\", never as 0.",
            "- A command that cannot do its job prints `emir.error/1` instead (its fields: "
            "command, code, level, title, message, hint, exit_code, next_steps, details).",
            "- A refused, failed or interrupted `emir run` also writes "
            "its `emir.error/1` to `--summary-out`, replacing any previous summary there.",
            "- Documents may gain fields without a version change; removing a field or "
            "changing its meaning moves the version (`/2`).",
            "- `emir schema <name>` prints a schema (draft 2020-12, ids "
            "`urn:emir:schema:<name>:1`); `emir schema --list` lists them; the files are "
            "`agent/schemas/<name>.schema.json`.", "",
            f"{len(docs)} documents:", "",
            "| document | schema | printed by |", "|---|---|---|"]
    special = {"error": "every command, when it cannot do its job",
               "help": "`--help` in JSON mode", "schema_list": "`emir schema --list`"}
    for n, s, const in docs:
        by = special.get(n) or ", ".join(f"`emir {c}`" for c in printed.get(n, [])) or \
            "written to a file (see its schema)"
        out.append(f"| `{const}` | `emir schema {n}`: {s.get('title', '')} | {by} |")
    out += ["", f"{len(inputs)} inputs and written files without the envelope:", ""]
    for n, s, _ in inputs:
        out.append(f"- `{n}`: {_first_sentence(s.get('description') or s.get('title', ''))}")
    out += ["", "`emir schema NAME` itself prints the raw schema (no envelope) in either "
            "format.", ""]


def _exits(out: list):
    from emir.errors import EXIT_CODES
    out += ["## 3. Exit status", "", "| status | meaning |", "|---|---|"]
    out += [f"| {k} | {v} |" for k, v in EXIT_CODES.items()]
    out += ["", "`check`, `selftest`, `validate`, `verify-sim` and `run` judge something: they "
            "always print their own document, and their status follows the verdict (`check`: "
            "2 when an input FAILs; `selftest`, `validate`, `verify-sim`: 70 on a failure; "
            "`run`: 1 on a FAIL verdict, an IR budget FAIL, or an EM basis asked for and not "
            "computed (`em_basis.honoured` false), only with `--fail-on-violation`). "
            "Text-mode errors go "
            "to stderr as `emir: EMIR-Exxx: "
            "message`, then `  hint:` and `  next:` lines. `EMIR_DEBUG=1` re-raises an "
            "uncoded exception with its traceback. emir never prompts an agent: the one "
            "prompt, `emir process new --interactive`, is a person's at a terminal and "
            "refuses any other stdin (EMIR-E327).", ""]


def _codes(out: list):
    from emir.errors import AREAS, DECADES, LEVELS, REGISTRY, RETIRED
    count = {}
    for c in REGISTRY:
        count.setdefault(int(c[6]), {}).setdefault(c[5], 0)
        count[int(c[6])][c[5]] += 1
    out += ["## 4. Codes", "",
            "Format `EMIR-<L><area><nn>`; the letter is the level ("
            + ", ".join(f"`{k}` {v}" for k, v in LEVELS.items()) + "). Branch on the "
            "code, never on the message. Every `E` code has an exit status; `W` and `I` "
            f"codes are notices (`notices[]` in the documents). {len(REGISTRY)} codes are "
            f"registered and {len(RETIRED)} retired; `agent/errors.md` (generated) is the "
            "catalog, and `emir explain <code>` prints one entry.", "",
            "| area | what | E | W | I |", "|---|---|---|---|---|"]
    for a in sorted(AREAS):
        n = count.get(a, {})
        out.append(f"| {a}xx | {AREAS[a]} | {n.get('E', 0)} | {n.get('W', 0)} | "
                   f"{n.get('I', 0)} |")
    out += ["", "Within an area each part of emir has its own decade (so parallel work "
            "never collides); area 8 is the hierarchy flow's alone:", "",
            "| numbers | owner |", "|---|---|"]
    out += [f"| x{lo:02d}-x{hi:02d} | {who} |" for who, (lo, hi) in DECADES.items()]
    out += ["", "- A code's `command` template may hold `[...]` optional parts and may "
            "have a `fallback`; its command may be empty, and then the hint says what to "
            "do. No code's command is `emir explain` of itself.",
            "- Codes are never reused: a code no longer raised moves to `RETIRED` with the "
            "reason.",
            "- To add a code: add it to `emir/errors.py`, raise or notice it, run "
            "`scripts/gen_errors_md.py` (and this script). The tests enforce: every code "
            "registered and used, the letter matches the level, no plain `raise`, no "
            "uncoded warning line, the catalog current.", ""]


def _next_steps(out: list):
    from emir import output, plan, schemas
    plan.load_extensions()
    rules = sorted(plan.RULES)
    step_fields = ", ".join(schemas.PLAN_STEP["properties"])
    out += ["## 5. Next steps", "",
            "Documents carry `next_steps`, a list of `{why, command}`. "
            + _sentence(schemas.NEXT_STEPS["items"].get("description", "")), "",
            "- Every command runs as printed: paths are shell-quoted, and a step whose "
            "command cannot be filled completely is not given.",
            "- An emir command keeps the caller's `--format` (`output.keep_format`), error "
            "next steps and the fixes `emir check` suggests included; the `emir report` "
            "step of a run is given without it, because its Markdown is what to relay "
            "(add `--format json` for the JSON document).",
            "- A step whose command is not an emir command (`ls`, `tail`, `klayout` ...) "
            "carries `\"shell\": true` (`output.mark_shell`): it needs a shell, which the "
            "MCP server does not have (`emir_cli` refuses it with EMIR-E591).",
            "- `emir check`: when the inputs pass, the run command with `--summary-out "
            "emir_out/summary.json`; when they fail, each FAIL's command, once per "
            "distinct command.",
            "- `emir run`: `emir report --from <summary>` (with `--summary-out`; without "
            "it, the run again with `--summary-out emir_out/summary.json`, then the "
            "report); on FAIL or WARN the run with `--html emir_out/html`; on FAIL the "
            "same command again, to re-run after the fix.",
            "- Errors: the code's filled command (or its fallback), or none.", "",
            "### emir next", "",
            "`emir next` (`emir.next/1`) is the playbooks' decision tree as code. Its "
            f"`steps[]` items have: {step_fields}. Run them in order and stop at the "
            "first that exits non-zero. A step with `needs_human` true (a licensed tool, "
            "a long run, or an `ask`: values only the person can give) needs the person's "
            "go-ahead first. A `waiting` step does not "
            "run as printed: replace its `placeholders` (`<result.raw>`, "
            "`<testbench.cir>` ...; the pattern is `" + plan.PLACEHOLDER.pattern + "`) "
            "with what the steps in `after` produced. Inputs it is given (a currents "
            "file, a tap file, a process file, abstracts) are read as the run reads "
            "them: it plans no step the run would refuse, and `missing[]` says what is "
            "needed.", "",
            "Thresholds (printed in every plan): " + ", ".join(
                f"`{k}` {v}" for k, v in plan.THRESHOLDS.items()) + ".", "",
            "Rules, in order (a feature adds its own in `emir/plan_<feature>.py`):", "",
            ", ".join(f"{r.order:g} {r.name}" for r in rules) + ".", ""]
    del output


def _mcp(out: list):
    from emir import mcp_tools as T
    specs = T.specs()
    out += ["## 6. MCP", "",
            "`emir mcp` (the `[mcp]` extra) serves every command but `mcp` as a typed "
            f"tool `{T.TOOL_PREFIX}<command>` (`-` becomes `_`; a command made of "
            f"sub-commands is one tool per sub-command, `{T.tool_name('process new')}`), "
            f"plus `{T.GENERIC}` for a "
            "whole emir command line (a `next_steps` command as printed; emir commands "
            "only, no shell). A tool's parameters are its command's options, named after "
            "the long option (`--cc-sim` -> `cc_sim`; "
            + ", ".join(f"`{k}` -> `{v}`" for k, v in T.RENAME.items())
            + "). Options no tool has: " + "; ".join(
                ", ".join(f"`--{x.replace('_', '-')}`" for x in sorted(v))
                + (" (every tool)" if c == "*" else f" (`{c}`)")
                for c, v in sorted(T.HIDDEN.items()))
            + ". A call runs `emir <command> ... --format json` and returns its one "
            "document (`structuredContent`; `isError` for `emir.error/1`; the exit status "
            "and command line in `_meta`).", "",
            "- Arguments are the tool's parameter names exactly: an unknown one, or a "
            "required one left out, is refused with EMIR-E592 and nothing runs.",
            f"- `{T.CONFIRM}: true` is the person's go-ahead. A call that would run a "
            "licensed tool, run long, rewrite a file in place, loosen the EM verdict "
            "(`emir_run` with an `em_basis` other than the default, or `dynamic` with "
            "`ground`, with `dyn_steps` below 2000 or without `period`), or run a command "
            "the server has not classified is refused with EMIR-E590 without it; ask the "
            "person first, every time.",
            "- `emir next` over MCP gives each step its `tool` call (null for a step that "
            "is not an emir command).", "",
            "| tool | command | go-ahead (`confirm`) | read-only |", "|---|---|---|---|"]
    for name, s in specs.items():
        when = "; ".join(s.confirm_when) if s.confirm == "conditional" else s.confirm
        cmd = f"`emir {s.command}`" if s.command else "a command line"
        out.append(f"| `{name}` | {cmd} | {when} | {'yes' if s.read_only else 'no'} |")
    out.append("")


def _python(out: list):
    out += ["## 7. Python API (for contributors)", "",
            "- `EmirError(code, message, *, hint, next_steps, details, **ctx)`: a refusal; "
            "`ctx` fills the code's command template.",
            "- `notice(code, msg, log)` inside `collecting()`: a coded warning or info.",
            "- Checker findings: `Report.fail/warn/info/ok(code, area, msg, fix, **ctx)`.",
            "- A new command: `def cmd(argv, out)` that parses with `output.parser`, calls "
            "`out.emit(document(...))` once, is registered in `cli.COMMANDS`, and has its "
            "schema in `emir/schemas.py`; its MCP tool follows from its parser.",
            "- Options that take JSON inline or from a file read it with `emir/jsonarg.py`; "
            "references to the repository's files go through `emir/repo.py`.", ""]


def contract() -> str:
    from emir import RESEARCH_DISCLAIMER, __version__
    out = ["# EMIR agent contract", "",
           "Generated from the code by `scripts/gen_contract.py`; do not edit by hand (a "
           "test fails when it is stale). It is what an agent may rely on: the commands, "
           "the documents they print, the exit statuses, the codes, the next steps and "
           f"the MCP tools of emir {__version__}. Screening, not sign-off. "
           f"{RESEARCH_DISCLAIMER} Relay that with every result.", ""]
    for part in (_commands, _documents, _exits, _codes, _next_steps, _mcp, _python):
        part(out)
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="gen_contract", allow_abbrev=False,
                                 description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true",
                    help="do not write; exit 1 when the contract is stale")
    a = ap.parse_args(argv)
    empty, saved = _clean_site()
    try:
        sys.path.insert(0, ROOT)
        text = contract()
    finally:
        _restore(empty, saved)
    old = open(OUT, encoding="utf-8").read() if os.path.isfile(OUT) else None
    rel = os.path.relpath(OUT, ROOT)
    if a.check:
        if old != text:
            print(f"{rel} is stale: run python scripts/gen_contract.py", file=sys.stderr)
            return 1
        print(f"{rel} is up to date")
        return 0
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"wrote {rel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
