"""The command-line output contract, in one place.

Every `emir` command takes ``--format text|json`` (default text):

* **text** -- for a person: tables and lines on stdout, logs and errors on
  stderr.
* **json** -- for an agent: stdout carries exactly ONE JSON document, with a
  ``"schema": "emir.<thing>/1"`` field naming its JSON Schema (`emir schema
  <thing>` prints it) and ``"emir_version"``. Everything else the command
  prints (progress, logs, the tables of text mode) goes to stderr. A command
  that fails prints an ``emir.error/1`` document instead (see
  `error_document`), with the error's registry code, and exits with the
  code's status (`emir/errors.py`).

A command function is ``cmd(argv, out) -> int``: it parses its arguments
with :func:`parser` (which adds ``--format``), does its work, prints text with
`print()` as before, calls ``out.emit(doc)`` once with its document, and
returns the exit status. `cli.main` does the rest: it points stdout at stderr
while a JSON-mode command runs, so a stray `print()` can never corrupt the
document, and turns every exception into a coded error.

`next_steps` lists in documents are ``[{"why", "command"}]`` with commands
built from the caller's own arguments (`command_line` quotes them); each
runs as printed.

`--help` in JSON mode prints an ``emir.help/1`` document (the help text in
``text``), so even that keeps the one-document rule.
"""
from __future__ import annotations

import argparse
import contextvars
import json
import math
import os
import re
import shlex
import sys

from . import __version__
from .errors import EmirError

FORMATS = ("text", "json")


class Out:
    """Where one invocation's document goes."""

    def __init__(self, fmt: str = "text", stdout=None, command: str | None = None):
        self.format = fmt
        self.stdout = stdout or sys.stdout
        self.command = command
        self.emitted = None

    @property
    def json(self) -> bool:
        return self.format == "json"

    def emit(self, doc: dict) -> dict:
        """Record the command's document; in JSON mode, write it to stdout.
        At most once per invocation."""
        assert self.emitted is None, "one document per invocation"
        self.emitted = doc
        if self.json:
            self.stdout.write(dumps(doc) + "\n")
            self.stdout.flush()
        return doc

    def emit_raw(self, obj: dict, text: str) -> dict:
        """For `emir schema NAME`: the document IS a JSON Schema (no
        envelope), printed as `text` in either format."""
        assert self.emitted is None, "one document per invocation"
        self.emitted = obj
        self.stdout.write(text if text.endswith("\n") else text + "\n")
        self.stdout.flush()
        return obj

    def fmt_args(self) -> list:
        """`["--format", "json"]` in JSON mode, so the commands this result
        suggests keep the caller's format; `[]` in text mode."""
        return ["--format", "json"] if self.json else []


class PipeSafe:
    """A standard stream (stdout, stderr) whose reader may leave.

    An agent often reads a command through a pipe that closes early
    (`emir run ... 2>&1 | head -c 300`). The next write then raises
    BrokenPipeError; unhandled in the middle of an analysis, it would end
    the command before it writes its --summary-out or --out files. Through
    this wrapper the first broken-pipe error is swallowed, the stream is
    marked `gone`, its file descriptor is pointed at /dev/null (so the
    interpreter's final flush cannot fail and exit 120), and every later
    write is dropped without being tried again. The command runs to its
    end, writes its files and exits with its own status. Everything else is
    the wrapped stream's (`fileno`, `isatty` ...)."""

    def __init__(self, stream):
        self._stream = stream
        #: True once a write or flush found the reader gone
        self.gone = False

    @property
    def stream(self):
        """The wrapped stream."""
        return self._stream

    def _lost(self):
        self.gone = True
        try:
            fd = self._stream.fileno()
            null = os.open(os.devnull, os.O_WRONLY)
            try:
                os.dup2(null, fd)
            finally:
                os.close(null)
        except (OSError, ValueError, AttributeError):
            # no file descriptor (a StringIO, a test's capture): nothing
            # will flush it at exit either
            pass

    def write(self, s):
        if not self.gone:
            try:
                return self._stream.write(s)
            except BrokenPipeError:
                self._lost()
        return len(s)

    def writelines(self, lines):
        for line in lines:
            self.write(line)

    def flush(self):
        if not self.gone:
            try:
                self._stream.flush()
            except BrokenPipeError:
                self._lost()

    def __getattr__(self, name):
        return getattr(self._stream, name)


def pipe_safe(stream):
    """`stream` wrapped in `PipeSafe` (once: a wrapped stream is returned as
    it is)."""
    return stream if isinstance(stream, PipeSafe) else PipeSafe(stream)


def document(schema: str, **fields) -> dict:
    """A document of schema `emir.<schema>/1`: the two envelope fields first.
    Its `next_steps` that are not emir commands are marked (`mark_shell`)."""
    if isinstance(fields.get("next_steps"), list):
        fields["next_steps"] = mark_shell(fields["next_steps"])
    return {"schema": f"emir.{schema}/1", "emir_version": __version__, **fields}


def is_emir_command(command: str) -> bool:
    """Whether a command line runs emir (`emir ...` or `/path/to/emir ...`)."""
    try:
        tokens = shlex.split(command or "")
    except ValueError:
        return False
    return bool(tokens) and (tokens[0] == "emir" or tokens[0].endswith("/emir"))


def mark_shell(steps: list) -> list:
    """`next_steps` with ``"shell": true`` on every step whose command is not
    an emir command (`ls`, `tail`, `klayout` ...): it needs a shell, which
    the MCP server does not have (`emir_cli` refuses it with EMIR-E591), so
    an agent without one leaves it to the person."""
    out = []
    for s in steps:
        if isinstance(s, dict) and s.get("command") and not is_emir_command(s["command"]):
            s = {**s, "shell": True}
        out.append(s)
    return out


def keep_format(steps: list, fmt: list) -> list:
    """`steps` whose emir commands carry the caller's `--format` (`fmt`, as
    `Out.fmt_args` gives it): a command that names none gets it appended, so
    an agent in JSON mode gets one JSON document from the next step too."""
    if not fmt:
        return list(steps)
    out = []
    for s in steps:
        cmd = s.get("command") if isinstance(s, dict) else None
        if cmd and is_emir_command(cmd):
            tokens = shlex.split(cmd)
            if "--" not in tokens and not any(t == "--format" or t.startswith("--format=")
                                              for t in tokens):
                s = {**s, "command": cmd + " " + shlex.join(fmt)}
        out.append(s)
    return out


def _plain(x):
    """JSON-safe: numpy scalars and arrays to Python, NaN and Inf to null."""
    try:
        import numpy as np
    except ImportError:                          # pragma: no cover
        np = None
    if isinstance(x, dict):
        return {str(k): _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    if np is not None:
        if isinstance(x, np.ndarray):
            return [_plain(v) for v in x.tolist()]
        if isinstance(x, np.generic):
            x = x.item()
    if isinstance(x, float) and not math.isfinite(x):
        return None
    if isinstance(x, (str, int, float, bool)) or x is None:
        return x
    return str(x)


def dumps(doc: dict) -> str:
    return json.dumps(_plain(doc), indent=1, allow_nan=False)


def write_json(path: str, doc: dict) -> str:
    """Write a document to a file (creating its directory)."""
    return write_text(path, dumps(doc) + "\n")


def write_text(path: str, text: str, code: str = "EMIR-E105") -> str:
    """Write an output file, creating its directory. A failure is the coded
    error `code` (EMIR-E105 "cannot write an output"; EMIR-E701 for the
    --html and --viewer directories) naming the path -- never the "input
    file not found" an unhandled FileNotFoundError would read as."""
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError as e:
        raise EmirError(code, f"cannot write {path}: {e.strerror or e}", path=path) from e
    return path


def make_dir(path: str, code: str = "EMIR-E105") -> str:
    """`os.makedirs(path, exist_ok=True)`, with a failure coded (see `write_text`)."""
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as e:
        raise EmirError(code, f"cannot create the directory {path}: {e.strerror or e}",
                        path=path) from e
    return path


_UNSAFE = re.compile(r"[^A-Za-z0-9_.+-]")


def safe_component(name, default: str = "x") -> str:
    """One file-name component built from a name read from an input (a DSPF's
    design, a net, a GDS cell): every character outside ``[A-Za-z0-9_.+-]``
    becomes ``_``, so a name like ``../../x``, ``/tmp/x`` or ``XI0/VDD`` can
    never leave the output directory or create a sub-directory. A name that
    had to change gets a short hash of the original, so two names that differ
    only in such characters (``XI0/VDD`` and ``XI0_VDD``) stay two files."""
    raw = str(name if name is not None else "")
    safe = _UNSAFE.sub("_", raw).strip(".") or default
    if safe in (".", ".."):
        safe = default
    if safe != raw:
        import hashlib
        safe = f"{safe}-{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:6]}"
    return safe


def contained(directory: str, path: str, code: str = "EMIR-E701") -> str:
    """`path`, refused (`code`) unless it lies inside `directory` (after
    resolving symbolic links): the last check before an output whose name
    came from an input is written."""
    d = os.path.realpath(directory)
    p = os.path.realpath(path)
    if os.path.commonpath([d, p]) != d:
        raise EmirError(code, f"refusing to write {path}: it is outside the output "
                        f"directory {directory}", path=path)
    return path


def command_line(*parts) -> str:
    """A runnable command line: `emir` and the parts, each shell-quoted.
    A part that is a list is spliced in."""
    flat = ["emir"]
    for p in parts:
        if isinstance(p, (list, tuple)):
            flat += [str(x) for x in p]
        elif p is not None:
            flat.append(str(p))
    return shlex.join(flat)


def strip_options(argv, drop: dict) -> list:
    """`argv` without the options in `drop` (`{option: number of values}`),
    in either the `--opt value` or the `--opt=value` form."""
    out, skip = [], 0
    for tok in argv:
        if skip:
            skip -= 1
            continue
        name = tok.split("=", 1)[0]
        if name in drop:
            skip = 0 if "=" in tok else drop[name]
            continue
        out.append(tok)
    return out


def expand_argfiles(argv, depth: int = 0) -> list:
    """`argv` with every `@file` replaced by the file's lines, as argparse
    reads them (one argument per line, nested files too). A file that
    cannot be read stays as it is: the parser reports it."""
    out = []
    for tok in argv:
        if tok.startswith("@") and len(tok) > 1 and depth < 10:
            try:
                with open(tok[1:], encoding="utf-8") as f:
                    lines = f.read().splitlines()
            except (OSError, UnicodeError):
                out.append(tok)
                continue
            out += expand_argfiles(lines, depth + 1)
        else:
            out.append(tok)
    return out


def prescan_format(argv) -> str:
    """The `--format` value in `argv` (`@file` arguments included), read
    before the parser runs, so even a usage error is reported in the format
    asked for. `text` if absent or not one of the formats (the parser then
    reports the bad value)."""
    fmt = "text"
    argv = expand_argfiles(argv)
    for i, tok in enumerate(argv):
        if tok == "--":
            break
        if tok == "--format" and i + 1 < len(argv):
            fmt = argv[i + 1]
        elif tok.startswith("--format="):
            fmt = tok.split("=", 1)[1]
    return fmt if fmt in FORMATS else "text"


#: the help text the last `--help` printed (for the JSON-mode help document)
_HELP: contextvars.ContextVar = contextvars.ContextVar("emir_help", default=None)


def last_help() -> str | None:
    """The help text `--help` printed during this command, or None."""
    return _HELP.get()


def reset_help():
    _HELP.set(None)


#: options agents carry over from another command, and what this command
#: takes instead (an unknown option of an `unrecognized arguments` error)
_RUN_OUT = ("run writes --summary-out FILE (the run summary emir report reads), --html DIR "
            "and --viewer DIR; there is no --out (emir_out/ is only the usual place)")
CROSS_COMMAND_GUESSES = {
    ("run", "--out"): _RUN_OUT,
    ("run", "--out-dir"): _RUN_OUT,
    ("run", "--json-out"): "run writes its summary with --summary-out FILE",
    ("check", "--out"): "check writes its findings with --json-out FILE",
    ("check", "--summary-out"): "check writes its findings with --json-out FILE",
    ("report", "--out"): "report prints to stdout: redirect it (> report.md)",
    ("next", "--out"): "next only prints the plan; nothing is written",
    ("report", "--dspf"): "report reads what a run wrote: emir report --from SUMMARY.json",
    ("extract", "--dspf"): "extract writes a DSPF from --gds (Quantus: and --cdl; or "
                           "--tool magic); emir run <dspf> screens it",
    # any other command (`{command}` is filled in)
    (None, "--dspf"): "emir {command} takes no DSPF; the commands that do take it as their "
                      "first argument (emir run <dspf> ..., emir check <dspf> ...), and "
                      "`emir next --dspf D ...` prints them",
}

#: options of methods 1.1 does not have (each pin's current solved through
#: the R grid, its guard, block abstracts, the per-pin probes): an unknown
#: option of these names says so, instead of a fuzzy guess at another option
REMOVED_OPTIONS = frozenset((
    "--superposition", "--dynamic", "--dyn-steps", "--max-memory", "--em-basis",
    "--align", "--phase", "--phase-sweep", "--points", "--self-consistent",
    "--max-passes", "--tol-mV", "--tol-rel", "--droop-file", "--scalars",
    "--split-stub", "--vdd", "--testbench", "--guard-sim", "--guard-dir", "--taps-out",
    "--save-list", "--abstract", "--place", "--place-tol", "--budget-mv",
    "--port-voltages-out", "--port-voltages", "--probe", "--probes", "--types", "--pins",
    "--rcc-nodes"))
REMOVED_TEXT = ("not in 1.1: a simulated run is rcc (every branch current of the RC netlist, "
                "every metric), a DC run takes --currents")

#: the namespace attribute `--dspf FILE` is parsed into before `ArgParser`
#: moves it to `dspf` (see `add_dspf`)
_DSPF_OPTION = "dspf_given_as_option"


#: SPICE's scale suffixes for a time: femto to milli (`u` or `µ` micro)
_TIME_SCALE = {"f": 1e-15, "p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "m": 1e-3, "": 1.0}
#: SPICE reads its suffixes in any case (4NS is 4ns; M is milli): `meg` is no
#: time unit here, so it stays out
_TIME = re.compile(r"^\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*([fpnuµm]?)s?\s*$",
                   re.IGNORECASE)


def seconds(text) -> float:
    """An option's time in seconds: a number (`1e-08`), or a number with a
    SPICE suffix and an optional `s`, in any case as SPICE reads them (`10n`,
    `10ns`, `1.5us`, `4NS`, `1.5US`). A time the person types as the
    testbench writes it is read the same way."""
    m = _TIME.match(str(text))
    if not m:
        raise argparse.ArgumentTypeError(
            f"invalid time {text!r}: give seconds as a number (1e-08) or with a SPICE "
            f"suffix (10n, 10ns, 1.5us)")
    unit = m.group(2).lower()
    v = float(m.group(1)) * _TIME_SCALE["µ" if unit == "\u03bc" else unit]
    if not math.isfinite(v):
        raise argparse.ArgumentTypeError(f"invalid time {text!r}: not finite")
    if v <= 0:
        # a period or a timeout of 0 or less is no time: 0 was taken as "not
        # given", and a negative period planned a simulation to a negative time
        raise argparse.ArgumentTypeError(f"invalid time {text!r}: give a time above 0")
    return v


seconds.json_type = "number"
seconds.example = 1e-08
seconds.__name__ = "seconds"


def add_dspf(ap, help: str, optional: bool = False):
    """The DSPF argument of a command that reads one (run, check, nets, pins,
    save-lines): positional, `emir run <dspf> ...`, and `--dspf FILE`
    accepted as the same. `emir next` takes the DSPF as `--dspf`, and agents
    carry that option over to the commands it prints; refusing it would
    cost a round trip for nothing. Two different files, one each way, are
    refused (EMIR-E507). `--dspf` stays out of the help and the MCP tool
    parameters: the positional is the documented form."""
    act = ap.add_argument("dspf", nargs="?" if optional else None, default=None,
                          help=help + "; `--dspf FILE` is accepted as the same")
    ap.add_argument("--dspf", dest=_DSPF_OPTION, default=None, metavar="DSPF",
                    help=argparse.SUPPRESS)
    ap.dspf_positional = act
    return act


def _dspf_option_given(argv) -> bool:
    for tok in expand_argfiles(list(argv)):
        if tok == "--":
            return False
        if tok == "--dspf" or tok.startswith("--dspf="):
            return True
    return False


class ArgParser(argparse.ArgumentParser):
    """`argparse` with the CLI's rules: no option abbreviations (a prefix
    must never select an option: `--taps f` once became `--taps-out f` and
    overwrote f), `@file` argument files, and usage errors raised as
    `EMIR-E507` so they are coded and formatted like every other error."""

    def __init__(self, *args, **kw):
        kw["allow_abbrev"] = False
        kw.setdefault("fromfile_prefix_chars", "@")
        super().__init__(*args, **kw)

    def parse_known_args(self, args=None, namespace=None):
        pos = getattr(self, "dspf_positional", None)
        if pos is None:
            return super().parse_known_args(args, namespace)
        argv = list(sys.argv[1:] if args is None else args)
        saved = pos.nargs, pos.required
        if _dspf_option_given(argv):
            # the DSPF comes as `--dspf FILE`: the positional may be absent
            pos.nargs, pos.required = "?", False
        try:
            ns, extras = super().parse_known_args(argv, namespace)
        finally:
            pos.nargs, pos.required = saved
        opt = getattr(ns, _DSPF_OPTION, None)
        if hasattr(ns, _DSPF_OPTION):
            delattr(ns, _DSPF_OPTION)
        if opt is not None:
            if ns.dspf is not None and os.path.abspath(ns.dspf) != os.path.abspath(opt):
                self.error(f"two DSPFs: {ns.dspf} (the first argument) and --dspf {opt}; "
                           f"give one")
            ns.dspf = opt
        return ns, extras

    def error(self, message):
        details = {"usage": self.format_usage()}
        # `emir run` -> run; a sub-command's (`emir process new`) -> process,
        # whose --help names the sub-commands (a next step's command is one
        # shell word)
        words = (self.prog or "").split()
        command = words[1] if len(words) > 1 else (words[0] if words else None)
        if getattr(self, "dspf_positional", None) is not None and \
                re.search(r"arguments are required: (.*, )?dspf\b", message):
            message += (f"; the DSPF is the first argument: {self.prog} <dspf> ... "
                        f"(--dspf FILE is accepted as the same)")
        # --mode takes rcc alone in 1.1, and may be left out
        if re.match(r"argument --mode: invalid choice", message):
            message += ("; 1.1 has one method of a simulated run, rcc (every branch current "
                        "of the RC netlist): --mode rcc, or leave --mode out")
        guesses = self._did_you_mean(message, command)
        if guesses:
            details["did_you_mean"] = guesses
            message += "; " + "; ".join(f"{k}: {v}" for k, v in guesses.items())
        raise EmirError("EMIR-E507", message, details=details, command=command)

    def _did_you_mean(self, message: str, command: str | None) -> dict:
        """For each unknown option of an `unrecognized arguments` error: the
        option a guess most likely meant (a known cross-command guess first,
        e.g. `run --out`, else the closest option of this command)."""
        import difflib
        m = re.match(r"unrecognized arguments: (.*)", message)
        if not m:
            return {}
        known = [o for o in self._option_string_actions if o.startswith("--")]
        out = {}
        for tok in m.group(1).split():
            if not tok.startswith("--"):
                continue
            opt = tok.split("=", 1)[0]
            if opt in REMOVED_OPTIONS:
                out[opt] = REMOVED_TEXT
                continue
            fixed = CROSS_COMMAND_GUESSES.get((command, opt)) or \
                CROSS_COMMAND_GUESSES.get((None, opt), "").replace("{command}", command or "")
            if fixed:
                out[opt] = fixed
                continue
            close = difflib.get_close_matches(opt, known, n=2, cutoff=0.6)
            if close:
                out[opt] = "did you mean " + " or ".join(close) + "?"
        return out

    def print_help(self, file=None):
        # kept for the JSON-mode `emir.help/1` document (`cli.main`)
        _HELP.set(self.format_help())
        super().print_help(file)


def parser(prog: str, formats=FORMATS, **kw) -> ArgParser:
    """The parser of one `emir` command, with its `--format` option."""
    kw.setdefault("formatter_class", argparse.RawDescriptionHelpFormatter)
    ap = ArgParser(prog=prog, **kw)
    add_format(ap, formats)
    return ap


def add_format(ap, formats=FORMATS):
    """`--format`; the first of `formats` is the default (`emir report`
    takes md|json|text, md being its text form)."""
    person = (f"{formats[0]} (for a person, the default"
              + ("; text is the same" if "text" in formats[1:] else "") + ")")
    ap.add_argument("--format", choices=formats, default=formats[0],
                    help=f"{person} or json (one JSON document on stdout, logs on "
                         f"stderr; `emir schema` describes it)")
    return ap


def error_document(err: EmirError, command: str | None = None,
                   ctx: dict | None = None, fmt: list | None = None) -> dict:
    """The `emir.error/1` document for an error. `ctx` fills the next-step
    command templates where the error's own context leaves a field open;
    `fmt` is the caller's format (`Out.fmt_args`), which the emir commands
    among the next steps keep (`keep_format`)."""
    e = err.entry
    return document("error", command=command, code=err.code, level=e.level,
                    title=e.title, message=err.message, hint=err.hint,
                    exit_code=err.exit_code,
                    next_steps=keep_format(err.next_steps(**(ctx or {})), fmt or []),
                    details=err.details)


def error_text(err: EmirError, ctx: dict | None = None) -> str:
    """The text-mode rendering: usage (for a usage error), then
    `emir: EMIR-Exxx: message`, the hint and the next command."""
    lines = []
    usage = err.details.get("usage")
    if usage:
        lines.append(usage.rstrip())
    lines.append(f"emir: {err.code}: {err.message}")
    if err.hint:
        lines.append(f"  hint: {err.hint}")
    for s in err.next_steps(**(ctx or {})):
        lines.append(f"  next: {s['command']}")
    return "\n".join(lines)
