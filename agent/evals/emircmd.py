"""Parse emir command lines with emir's own argparse parsers, without running them.

The playbooks and the eval graders both need one answer to "is this a real emir
command line?": the documentation must never print an option emir does not have,
and an agent must never be credited for a command it invented. Both ask here.

    from emircmd import emir_argv, check_argv
    argv = emir_argv(shlex.split("emir run x.dspf --process p.yaml --fast"))
    check_argv(argv)  ->  "unrecognized arguments: --fast"   (None when it parses)

The parser of each command is captured the way `emir mcp` builds its tools
(`emir.mcp_tools.capture_parser`): the command runs up to its `parse_args` call
and no further, so nothing is read, written or simulated.
"""
from __future__ import annotations

import contextlib
import io
import os
import re
import shlex

#: tokens that start an emir invocation: `emir`, a path ending in /emir
#: (`bin/emir`, `.venv/bin/emir`), or `python -m emir`
_PYTHON = re.compile(r"(^|/)python(3(\.\d+)?)?$")

#: a documentation placeholder such as <dspf> or <result.raw>
PLACEHOLDER = re.compile(r"<[^<>\s]+>")

_PARSERS: dict = {}


def split(line: str) -> list:
    """Shell words of one command line; a trailing `# comment` is dropped."""
    lex = shlex.shlex(line, posix=True, punctuation_chars=False)
    lex.whitespace_split = True
    lex.commenters = "#"
    return list(lex)


def emir_argv(tokens: list) -> list | None:
    """The arguments after `emir` (the command first), or None when the tokens
    are not an emir invocation. Leading `VAR=value` assignments and `env ...`
    are skipped; anything after a shell operator (`|`, `>`, `&&`, `;`) is not
    part of the emir call."""
    toks = list(tokens)
    while toks:
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", toks[0]):
            toks = toks[1:]
        elif toks[0] == "env":
            toks = toks[1:]
            while toks and toks[0].startswith("-"):      # env -i, env -u NAME
                toks = toks[2:] if toks[0] == "-u" else toks[1:]
        else:
            break
    if not toks:
        return None
    if toks[0] == "emir" or toks[0].endswith("/emir"):
        rest = toks[1:]
    elif _PYTHON.search(toks[0]) and toks[1:3] == ["-m", "emir"]:
        rest = toks[3:]
    else:
        return None
    out = []
    for t in rest:
        if t in ("|", "||", "&&", ";", ">", ">>", "2>", "2>&1", "<", "&") or \
                re.match(r"^\d?>{1,2}", t):
            break
        out.append(t)
    return out


def resolve(argv: list) -> tuple:
    """`(command, rest)`: a sub-command folded into the command (`process
    new ...` -> ("process new", [...]), as `emir mcp` names its tools)."""
    from emir import cli
    command, rest = argv[0], list(argv[1:])
    subs = cli.SUBCOMMANDS.get(command)
    if subs and rest and rest[0] in subs:
        return f"{command} {rest[0]}", rest[1:]
    return command, rest


def parser_for(command: str):
    """emir's own parser for `emir <command>` (cached), or None; `command`
    may name a sub-command (`process new`)."""
    if command not in _PARSERS:
        from emir.mcp_tools import capture_parser
        _PARSERS[command] = capture_parser(command)
    return _PARSERS[command]


def check_argv(argv: list, placeholders: bool = False) -> str | None:
    """None when `emir <argv>` parses with emir's parser; else what is wrong.

    `placeholders=True` first replaces documentation placeholders (`<dspf>`)
    by a plain word, for template command lines."""
    if placeholders:
        if argv and PLACEHOLDER.fullmatch(argv[0]):
            return None                          # `emir <command> --help`: any command
        if any(PLACEHOLDER.search(a) for a in argv):
            # a placeholder stands for a word or, for a typed option
            # (`--period <T>`), a number: it parses if either does
            err = check_argv([PLACEHOLDER.sub("X", a) for a in argv])
            if err and "invalid" in err:
                err = check_argv([PLACEHOLDER.sub("1", a) for a in argv])
            return err
    if not argv or argv[0] in ("-h", "--help", "-V", "--version") or \
            argv[0].startswith("--format"):
        return None
    from emir import cli
    if argv[0] not in cli.COMMANDS:
        return f"unknown command {argv[0]!r}"
    command, rest = resolve(argv)
    parser = parser_for(command)
    if parser is None:
        return f"no parser could be captured for {command!r}"
    err = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
        try:
            parser.parse_args(rest)
        except SystemExit as e:
            if e.code in (0, None):
                return None                      # --help
            text = err.getvalue().strip().splitlines()
            return text[-1] if text else f"usage error (exit {e.code})"
        except Exception as e:                   # noqa: BLE001 - a coded refusal while parsing
            return f"{type(e).__name__}: {e}"
    return None


def unknown_options(argv: list) -> list:
    """The `--options` of `emir <argv>` its parser does not have (a check that
    still works on a partial command line, with required arguments missing)."""
    if not argv:
        return []
    command, rest = resolve(argv)
    parser = parser_for(command)
    if parser is None:
        return []
    known = set(parser._option_string_actions)
    return [t for t in rest if t.startswith("--") and t.split("=")[0] not in known]


def check_line(line: str, placeholders: bool = False) -> tuple:
    """`(argv or None, error or None)` for one command line."""
    try:
        toks = split(line)
    except ValueError as e:
        return None, f"does not split as shell words: {e}"
    argv = emir_argv(toks)
    if argv is None:
        return None, None
    return argv, check_argv(argv, placeholders=placeholders)


def logical_lines(text: str) -> list:
    """The command lines of a shell block: continuation backslashes joined,
    blank and comment-only lines dropped."""
    out, cur = [], ""
    for raw in text.splitlines():
        line = raw.rstrip()
        if not cur and (not line.strip() or line.lstrip().startswith("#")):
            continue
        if line.endswith("\\"):
            cur += line[:-1] + " "
            continue
        cur += line
        out.append(cur.strip())
        cur = ""
    if cur.strip():
        out.append(cur.strip())
    return out


def is_emir_command(line: str) -> bool:
    try:
        return emir_argv(split(line)) is not None
    except ValueError:
        return False


def same_path(a: str, b: str) -> bool:
    """True when two paths name the same file (relative paths from the cwd)."""
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.normpath(os.path.abspath(a)) == os.path.normpath(os.path.abspath(b))
