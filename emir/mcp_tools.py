"""The tools `emir mcp` serves, built from the CLI itself (no MCP SDK needed).

Every command in `cli.COMMANDS` (except `mcp`) is one typed MCP tool,
``emir_<command>``, whose parameters are that command's own argparse options:
the parser is captured by running the command up to its `parse_args` call
(`capture_parser`; a command that does not parse first is not served), so a
CLI option and its tool parameter cannot drift, and a command a later release
adds is served without a change here. A tool call is the CLI run in-process
-- ``emir <command> ... --format json`` through `cli.main` -- and its result is
the one document that run prints (`invoke`): the same `emir.<name>/1` JSON,
the same codes, the same `next_steps`. `emir selftest` (and every command
under ``emir mcp --isolate``) runs in a fresh interpreter instead.

``emir_cli`` runs a whole emir command line (a `next_steps` command, copied
as printed) for whatever the typed tools do not cover.

A command made of sub-commands (`cli.SUBCOMMANDS`: `emir process new|check`)
is served as one tool per sub-command, ``emir_process_new``: its "command" is
the two words, `"process new"`, everywhere here.

The guard rail (`gates`): a call that would start a licensed tool (Spectre or
HSPICE, through `emir run --netlist --run`, `emir simulate --run` and `emir
verify-sim --run`; PVS and Quantus, through `emir extract --run`), re-simulate
(`emir run --self-consistent` with Xyce or ngspice),
run long (a block of more than `plan.THRESHOLDS["big_block_nodes"]` nodes; an
open-simulator run of a block above the small-net size; a transient of the
extracted layout), rewrite a user file in place (`emir import-ict` without
--out), loosen the EM verdict (`emir run --em-basis` other than the default,
or --dynamic with --ground, with --dyn-steps below 2000 or without --period:
a choice that loosens the verdict is the person's), or run a command this
server has not classified, is refused with
EMIR-E590 unless the call carries ``confirm: true`` -- which the agent sets
only after the person agreed. The same steps of `emir next` carry
`needs_human`.

Everything here is plain Python; `emir/mcp_server.py` binds it to the SDK.
"""
from __future__ import annotations

import argparse
import contextlib
import contextvars
import io
import json
import keyword
import os
import shlex
import sys
import threading
from dataclasses import dataclass, field

from .errors import EmirError, lookup

TOOL_PREFIX = "emir_"
#: the tool that runs a whole command line
GENERIC = "emir_cli"
#: commands with no tool: the server itself
NOT_SERVED = frozenset({"mcp"})
#: options no tool exposes: `--format` (always json), `--help`, opening a
#: browser on the server's machine, and an option kept only for old argument
#: files
HIDDEN = {"*": {"help", "format"}, "run": {"open", "rcc_nodes"},
          "check": {"open", "rcc_nodes"},
          # a person at a terminal only (EMIR-E327): never over MCP
          "process new": {"interactive"}}
#: tool parameter names for options whose own name is a Python keyword
RENAME = {"from": "from_file"}
#: parameter descriptions that differ from the CLI help: (command, parameter)
PARAM_HELP = {("report", "from_file"): "the run summary: the file emir_run wrote as "
                                       "summary_out (stdin is the MCP channel here)"}
#: the parameter every gated tool takes
CONFIRM = "confirm"

#: commands that only read: their tools are annotated read-only (a command
#: with an option that names a file or directory to write is never
#: read-only, whatever this list says: `ToolSpec.writes`)
READ_ONLY = frozenset({"info", "nets", "pins", "explain", "schema", "next", "report",
                       "process check"})
#: the argparse dests of the options that name a file or directory a
#: command writes (`emir next`'s --html/--viewer only name them in the plan)
OUTPUT_DESTS = frozenset({"out", "names_out", "summary_out", "json_out", "html", "viewer",
                          "taps_out", "port_voltages_out", "dir", "work"})
PLAN_ONLY = frozenset({"next"})
#: the gate every command with an output option has
OVERWRITE_WHEN = ("an output parameter names an existing file outside the server's working "
                  "directory (it would be overwritten)")
#: commands whose tools may overwrite a file the person owns
DESTRUCTIVE = frozenset({"import-ict", "gui-install"})
#: commands that never need the person's go-ahead
UNGATED = frozenset({"info", "selftest", "validate", "nets", "pins", "save-lines", "check",
                     "report", "next", "explain", "schema", "gui-install",
                     "probe",
                     # writes a NEW file only (never overwrites: EMIR-E326); reads
                     "process new", "process check"})

#: the documents each command prints besides `emir.error/1` (default: the
#: schema named after the command; None: any JSON object, e.g. a raw schema)
OUTPUTS = {"run": ["run", "save_lines", "simulate"], "schema": None,
           "abstract": ["abstract_summary"],
           "process new": ["process_new"], "process check": ["process_check"]}
#: --- feature modes --- a parameter a tool requires when others are given:
#: the mode of a run whose currents come from a simulation is the person's
#: choice (EMIR-E520), so `mode` is required with `cc_sim` or `netlist` (a
#: DC run, `currents`, needs none; `self_consistent` needs `netlist`).
#: `simulate`, `save_lines` and `abstract` require it always (their parsers
#: say so). Written as `anyOf` (mode given, or none of them), which draft-07
#: and 2020-12 validators both read.
DEPENDENT = {c: ("mode", ("cc_sim", "netlist")) for c in ("run", "check")}

#: what each tool is for, from an agent's point of view (a command not
#: listed is described by its parser's description)
NOTES = {
    "info": "Where the simulators, packages and settings were found. Read-only.",
    "selftest": "Offline proof that the install works (a few seconds): run it first, "
                "before touching user data.",
    "validate": "The analytic validation suite: closed-form cases (quick=true leaves out "
                "the slow transient ones).",
    "nets": "The nets of a DSPF and their sizes. Read-only.",
    "pins": "The instance pins on one net of a DSPF. Read-only.",
    "save-lines": "The simulator save lines a run in the person's mode (rcc or cc-grid, "
                  "required) needs (Spectre syntax unless sim); written to `out` (and the "
                  "names sidecar to `names_out`) when given.",
    "check": "Check every input of a run before spending it (the options of emir_run); "
             "passed=false lists coded FAIL findings, each with its fix.",
    "run": "Screen EM (avg/rms/peak per segment) and IR drop. Pass summary_out (e.g. "
           "emir_out/summary.json) to feed emir_report. With cc_sim or netlist, mode "
           "(rcc or cc-grid) is required and is the person's choice: ask them (emir_next "
           "without mode lists the routes). Screening, not sign-off.",
    "report": "The summary of a run to relay to the person (the `markdown` field): "
              "verdict, worst items with fixes, what was not checked.",
    "next": "What to run next, and why, for what you have: the ordered steps, each with "
            "the tool call to make (`tool`). Start here with any user data.",
    "import-ict": "Fill a process file's EM limits from an ICT file.",
    "gui-install": "Write the Virtuoso SKILL files of the EMIR Explorer.",
    "explain": "What an error code means and the command that fixes or diagnoses it.",
    "schema": "The JSON Schema of a document emir reads or writes.",
    "extract": "GDS -> DSPF. tool quantus (default): GDS + CDL with PVS LVS and Quantus, "
               "the plan only (what would run, what is missing) unless run=true, which "
               "starts the licensed tools. tool magic (open PDKs): magic extracts the cell "
               "with every resistor's geometry and emir writes the DSPF and the CC netlist; "
               "a big layout needs confirm=true.",
    "probe": "Write a copy of a SPICE netlist with a 0 V probe in series with each device "
             "terminal on the nets (the input is never changed), and its names sidecar.",
    "simulate": "Run a simulator on the testbench with the save lines a run needs; the "
                "text result and its names sidecar feed emir_run (cc_sim). Xyce and "
                "ngspice run; Spectre and HSPICE (licensed) are only planned unless "
                "run=true, which needs confirm=true (the person's go-ahead).",
    "verify-sim": "Prove on this machine that a simulator's route into EMIR gives the "
                  "right numbers (a hand-checkable testbench, rcc and cc-grid, compared "
                  "with the analytic answer) and keep the record that runs look up. "
                  "Do it before the first run that uses that simulator here. Spectre and "
                  "HSPICE: planned unless run=true, which needs confirm=true.",
    "abstract": "Characterise a block once for the chip-level flow: its pin currents, "
                "pin-to-pin conductance and own EM/IR, written to out (emir.abstract/1); "
                "emir_run takes it as abstract FILE@INST.",
    "process new": "When no readable process data exists for the PDK (no file in pdks/, "
                   "the ICT missing or encrypted): write a NEW fill-in process file (out) "
                   "for the layers and vias a DSPF uses, every value empty with its unit "
                   "and a source slot. It never fills in a value: the person types each one "
                   "from the design manual, or you transcribe values from a manual the "
                   "person gives and the person confirms each one with its source. Never "
                   "invent, estimate or derive a limit. It never overwrites a file.",
    "process check": "What a process file still lacks before a run: empty values (required "
                     "ones fail), values with no source in a hand-entered file, likely unit "
                     "slips, limits out of order, and (dspf) the DSPF layers it does not "
                     "cover. Must pass before any run on a hand-entered file. Read-only.",
}

#: set while a tool call runs: `emir next` then gives each step its tool call
IN_MCP: contextvars.ContextVar = contextvars.ContextVar("emir_in_mcp", default=False)

#: one tool call at a time, start to end (the gate, the command, the parser
#: capture): each redirects the process-wide `sys.stdout` while it runs, and
#: one at a time also keeps to one simulator
_RUN = threading.RLock()


# ------------------------------------------------------------------ the parsers


class _Captured(BaseException):
    """Raised by the patched `parse_args` with the parser it was called on
    (a BaseException so no `except Exception` in a command swallows it)."""

    def __init__(self, parser):
        super().__init__("captured")
        self.parser = parser


def capture_parser(command: str):
    """The argparse parser of `emir <command>`, or None.

    The command is run with no arguments and `parse_args` (every command
    parses its arguments before it does anything, `output.py`) raises with
    the parser instead of parsing, so nothing else of the command runs."""
    from . import cli
    from .output import Out
    head, _, sub = command.partition(" ")
    fn = cli.COMMANDS.get(head)
    if fn is None or (sub and sub not in cli.SUBCOMMANDS.get(head, ())):
        return None
    cls = argparse.ArgumentParser
    saved = cls.parse_args, cls.parse_known_args

    def grab(self, *a, **k):
        raise _Captured(self)
    with _RUN:
        cls.parse_args = cls.parse_known_args = grab
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                fn([sub] if sub else [], Out("json", io.StringIO(), command=head))
        except _Captured as c:
            return c.parser
        except Exception:                       # noqa: BLE001 - not a parser-first command
            return None
        finally:
            cls.parse_args, cls.parse_known_args = saved
    return None


# ------------------------------------------------------------------ parameters


@dataclass
class Param:
    """One tool parameter: an argparse argument of the command."""
    name: str                 # the tool parameter
    dest: str                 # the argparse dest
    flag: str | None          # the option ("--net"); None for a positional
    kind: str                 # string number integer flag tristate array
    help: str
    required: bool = False
    choices: tuple | None = None
    default: object = None    # the argparse default
    item: str = "string"      # array items: string number integer
    append: bool = False      # array given as --opt a --opt b
    neg_flag: str | None = None   # tristate: the --no- form
    min_items: int = 0
    example: object = None    # a value the option's type accepts (`type.example`)

    def schema(self) -> dict:
        def base(kind):
            s = {"type": kind}
            if self.choices:
                s["enum"] = list(self.choices)
            return s
        if self.kind == "flag":
            s = {"type": "boolean", "default": False}
        elif self.kind == "tristate":
            s = {"type": "boolean"}
        elif self.kind == "array":
            s = {"type": "array", "items": base(self.item)}
            if self.min_items:
                s["minItems"] = self.min_items
        else:
            s = base(self.kind)
        if self.example is not None and self.kind not in ("flag", "tristate", "array"):
            s["examples"] = [self.example]
        s["description"] = self.help
        return s


def _kind(action) -> str:
    t = action.type
    if getattr(t, "json_type", None):          # a type function that says what it reads
        return t.json_type
    return "number" if t is float else "integer" if t is int else "string"


def _help(action, parser) -> str:
    h = action.help or ""
    if "%" in h:
        try:
            h = h % {**vars(action), "prog": parser.prog}
        except (KeyError, TypeError, ValueError):
            pass
    d = action.default
    if d not in (None, False, "", [], argparse.SUPPRESS) and "default" not in h \
            and (isinstance(d, (int, float)) or (action.choices and d in action.choices)):
        h = (h + " " if h else "") + f"(default: {d})"
    return " ".join(h.split())


def params_of(command: str, parser) -> list:
    """The tool parameters of a command's parser."""
    hidden = HIDDEN["*"] | HIDDEN.get(command, set())
    out, seen = [], set()
    for a in parser._actions:
        if a.help == argparse.SUPPRESS or isinstance(a, (argparse._HelpAction,
                                                           argparse._VersionAction)):
            continue
        longs = [s for s in a.option_strings if s.startswith("--")]
        flag = longs[0] if longs else (a.option_strings[0] if a.option_strings else None)
        name = (flag.lstrip("-") if flag else a.dest).replace("-", "_")
        if name in hidden or a.dest in hidden:
            continue
        name = RENAME.get(name, name + "_" if keyword.iskeyword(name) else name)
        if name == CONFIRM or name in seen or not name.isidentifier():
            continue
        seen.add(name)
        p = Param(name=name, dest=a.dest, flag=flag, kind=_kind(a),
                  help=PARAM_HELP.get((command, name)) or _help(a, parser),
                  choices=tuple(a.choices) if a.choices else None, default=a.default,
                  example=getattr(a.type, "example", None))
        if isinstance(a, argparse.BooleanOptionalAction):
            p.kind, p.neg_flag = "tristate", next(
                (s for s in a.option_strings if s.startswith("--no-")), None)
        elif isinstance(a, (argparse._StoreTrueAction, argparse._StoreFalseAction,
                            argparse._StoreConstAction, argparse._CountAction)):
            p.kind = "flag"
        elif isinstance(a, argparse._AppendAction) or a.nargs in ("*", "+") or \
                (isinstance(a, argparse._StoreAction) and isinstance(a.nargs, int)
                 and a.nargs > 1):
            p.kind, p.item = "array", _kind(a)
            p.append = isinstance(a, argparse._AppendAction)
            p.min_items = 1 if a.nargs == "+" else a.nargs if isinstance(a.nargs, int) else 0
        if flag is None:
            p.required = a.nargs not in ("?", "*")
        else:
            p.required = bool(a.required)
        out.append(p)
    return out


# ------------------------------------------------------------------ the gate


@dataclass
class Gate:
    """Why a call needs the person's go-ahead."""
    reason: str
    licensed_tool: str | None = None
    long_run: bool = False
    rewrites: str | None = None

    def as_dict(self) -> dict:
        return {"reason": self.reason, "licensed_tool": self.licensed_tool,
                "long_run": self.long_run, "rewrites": self.rewrites}


def _big(dspf, net) -> bool:
    """`emir next`'s size class, on the same threshold."""
    if not dspf or not os.path.isfile(dspf):
        return False
    from .plan import Facts
    try:
        return Facts(dspf=dspf, net=net).size()["class"] == "big"
    except Exception:                           # noqa: BLE001 - the run reports it
        return False


def _gate_run(ns) -> list:
    sc = getattr(ns, "self_consistent", False)
    if getattr(ns, "netlist", None) or sc:
        sim = getattr(ns, "sim", None) or "spectre"
        loop = " once per pass of the self-consistent loop" if sc else ""
        if sim in ("spectre", "hspice"):
            if not getattr(ns, "run", False):
                return []               # licensed without run: it only plans
            return [Gate(f"it runs {sim} (a licensed tool) on the netlist{loop}, which can "
                         f"take a long time", licensed_tool=sim, long_run=True)]
        return [Gate(f"it simulates the CC testbench with {sim}{loop} (it re-simulates), "
                     f"which can take a long time", long_run=True)] + \
            _deck_code(getattr(ns, "netlist", None))
    if not any(getattr(ns, k, False) for k in ("save_list", "save_branches", "split_stub")) \
            and _big(getattr(ns, "dspf", None), getattr(ns, "net", None)):
        from .plan import THRESHOLDS
        return [Gate(f"the DSPF has more than {THRESHOLDS['big_block_nodes']} nodes "
                     f"(ADC-top or PLL-top size): a long run", long_run=True)]
    return []


def _size_class(dspf, net) -> str | None:
    """`emir next`'s size class of a DSPF (small, medium, big), or None."""
    if not dspf or not os.path.isfile(dspf):
        return None
    from .plan import Facts
    try:
        return Facts(dspf=dspf, net=net).size()["class"]
    except Exception:                           # noqa: BLE001 - the command reports it
        return None


def _gate_abstract(ns) -> list:
    if _big(getattr(ns, "dspf", None), None):
        from .plan import THRESHOLDS
        return [Gate(f"the block DSPF has more than {THRESHOLDS['big_block_nodes']} nodes: "
                     f"a long characterisation", long_run=True)]
    return []


def _deck_code(testbench) -> list:
    """A gate when the testbench, a file it includes or the `.spiceinit`
    beside it makes the simulator run commands (`.control`, `shell`)."""
    if not testbench:
        return []
    from .opensim import deck_runs_code
    hits = deck_runs_code(testbench)
    if not hits:
        return []
    return [Gate("the testbench runs commands of its own (a `.control` block or a `shell` "
                 f"line in {', '.join(os.path.basename(h) for h in hits[:3])}): ngspice "
                 "executes them, so the deck is code -- run it only from a source the "
                 "person trusts")]


def _licensed_run(ns) -> list:
    """A gate when `--run` starts a licensed simulator (Spectre, HSPICE)."""
    sim = getattr(ns, "sim", None)
    if sim in ("spectre", "hspice") and getattr(ns, "run", False):
        return [Gate(f"it runs {sim} (a licensed tool) on the deck, which can take a long "
                     f"time", licensed_tool=sim, long_run=True)]
    return []


def _gate_verify_sim(ns) -> list:
    return _licensed_run(ns)


def _gate_simulate(ns) -> list:
    out = _licensed_run(ns) + _deck_code(getattr(ns, "testbench", None))
    # `emir next` marks the simulate step long above the small-net size
    if _size_class(getattr(ns, "dspf", None), getattr(ns, "net", None)) in ("medium", "big"):
        out.append(Gate("it simulates a block above the small-net size in Xyce or ngspice, "
                        "which can take a long time", long_run=True))
    return out


def _gate_extract(ns) -> list:
    if getattr(ns, "run", False):
        return [Gate("it runs PVS LVS and Quantus (licensed tools), which can take a long "
                     "time", licensed_tool="pvs, quantus", long_run=True)]
    if getattr(ns, "tool", None) == "magic":
        from .plan import THRESHOLDS
        gds = getattr(ns, "gds", None)
        try:
            mb = os.path.getsize(gds) / 1e6 if gds else 0.0
        except OSError:
            mb = 0.0
        if mb > THRESHOLDS["magic_gds_mb"]:
            return [Gate(f"the layout is {mb:.0f} MB (above {THRESHOLDS['magic_gds_mb']} "
                         f"MB): magic's extraction with every resistor kept can take a long "
                         f"time", long_run=True)]
    return []


def _gate_import_ict(ns) -> list:
    if getattr(ns, "out", None):
        return []
    return [Gate("it rewrites the process file given as `emir` in place (pass `out` to "
                 "write a new file instead)", rewrites=getattr(ns, "emir", None))]


#: command -> fn(argparse namespace) -> [Gate]: when the command needs the
#: person's go-ahead. A command in neither this table nor UNGATED always does.
GATES = {"run": _gate_run, "import-ict": _gate_import_ict, "abstract": _gate_abstract,
         "simulate": _gate_simulate, "extract": _gate_extract,
         "verify-sim": _gate_verify_sim}

#: what the conditional gates say in the tool list
GATE_WHEN = {
    "run": ["netlist or self_consistent is given with run true and sim spectre (the "
            "default) or hspice: it runs a licensed simulator, once per pass of the "
            "self-consistent loop (without run it only plans)",
            "self_consistent is given with sim xyce or ngspice: it re-simulates the CC "
            "testbench once per pass, possibly for a long time",
            "the DSPF is ADC-top or PLL-top size (the run is long)"],
    "import-ict": ["out is not given: it rewrites the process file in place"],
    "abstract": ["the block DSPF is ADC-top or PLL-top size (a long characterisation)"],
    "simulate": ["run is true with sim spectre or hspice: it runs a licensed simulator",
                 "the DSPF is above the small-net size (a long simulation)",
                 "the testbench or the .spiceinit beside it holds a .control block or a "
                 "shell line (the simulator runs those commands)"],
    "extract": ["run is true: it runs PVS and Quantus (licensed), possibly for a long time",
                "tool is magic and the layout is above the size where magic's extraction "
                "can run long (plan.THRESHOLDS magic_gds_mb)"],
    "verify-sim": ["run is true with sim spectre or hspice: it runs a licensed simulator "
                   "(six short simulations)"],
}
UNCLASSIFIED = Gate("this command is not classified by the MCP server: it may start "
                    "licensed tools or long jobs")


# --- feature decap --- a choice that loosens the verdict is the person's: an
# EM basis other than the default (em_basis.DEFAULT_BASIS) is theirs to choose
def _gate_em_basis(ns) -> list:
    from .em_basis import DEFAULT_BASIS, not_default
    b = getattr(ns, "em_basis", None)
    out = []
    if b and b != DEFAULT_BASIS:
        out.append(Gate(not_default(b).replace("--em-basis ", "em_basis ", 1)
                        + ": the verdict's basis is the person's choice"))
    if getattr(ns, "dynamic", False) and b != "decap-free":
        # options that keep decap-aware EM from being computed: the verdict
        # then falls back to decap-free, a loosening the person did not choose
        if getattr(ns, "ground", None):
            out.append(Gate("ground with dynamic: decap-aware EM draws the decaps' charge "
                            "from the feed, so a reference elsewhere leaves it not computed "
                            "and the verdict on decap-free (EMIR-W431): the person's choice"))
        steps = getattr(ns, "dyn_steps", None)
        if steps is not None and steps < 2000:
            out.append(Gate(f"dyn_steps {steps} with dynamic is below the default rule's "
                            f"least (2000): a step too coarse leaves decap-aware EM not "
                            f"computed and the verdict on decap-free (EMIR-W434): the "
                            f"person's choice"))
        if getattr(ns, "period", None) is None and getattr(ns, "mode", None) != "rcc":
            out.append(Gate("dynamic without period: decap-aware EM needs a window of whole "
                            "periods, and the detection finds none in a window of fewer "
                            "than about four, which leaves it not computed and the verdict "
                            "on decap-free (EMIR-W433): pass the block's period, or the "
                            "person's go-ahead"))
    steps = getattr(ns, "dyn_steps", None)
    if getattr(ns, "dynamic", False) and steps is not None and steps > 200_000:
        out.append(Gate(f"dyn_steps {steps} is above the default rule's most (200000): a "
                        f"long run", long_run=True))
    return out


_GATE_RUN_BASE = GATES["run"]
GATES["run"] = lambda ns: _GATE_RUN_BASE(ns) + _gate_em_basis(ns)
def _gate_when_em_basis() -> str:
    from .em_basis import DEFAULT_BASIS
    return (f"em_basis is not the default ({DEFAULT_BASIS}): the verdict's basis is the "
            "person's choice (decap-free and decap-aware each read lower than worst on some "
            "segments)")


GATE_WHEN["run"] = list(GATE_WHEN["run"]) + [
    _gate_when_em_basis(),
    "dynamic with ground, with dyn_steps below 2000, or without period: decap-aware EM "
    "may not be computed, which loosens the verdict to decap-free (EMIR-W431, EMIR-W434, "
    "EMIR-W433)",
    "dyn_steps above 200000 with dynamic (the default rule's most): a long run"]
# --- end feature decap ---


def writes(command: str, params) -> bool:
    """Whether one of a command's parameters names an output it writes."""
    return command not in PLAN_ONLY and any(p.dest in OUTPUT_DESTS for p in params)


def _overwrites(command: str, ns) -> list:
    """A gate for each output option that names an existing file outside
    the server's working directory: writing it would replace a file the
    person owns, which a call never does without their go-ahead (an output
    directory, and files under the working directory, are the call's own)."""
    if ns is None or command in PLAN_ONLY:
        return []
    here = os.path.realpath(os.getcwd())
    out = []
    for dest in sorted(OUTPUT_DESTS):
        v = getattr(ns, dest, None)
        for path in (v if isinstance(v, (list, tuple)) else [v]):
            if not isinstance(path, str) or not path or not os.path.isfile(path):
                continue
            real = os.path.realpath(path)
            if os.path.commonpath([here, real]) == here:
                continue
            out.append(Gate(f"it would overwrite {path}, an existing file outside the "
                            f"working directory", rewrites=path))
    return out


def gates(command: str, argv: list, parser=None) -> list:
    """The reasons `emir <command> argv` needs the person's go-ahead ([]: none)."""
    # a command line that does not parse runs nothing: the CLI reports it
    ns = _parse_quietly(parser, argv) if parser is not None else None
    over = _overwrites(command, ns)
    if command in UNGATED:
        return over
    fn = GATES.get(command)
    if fn is None or parser is None:
        return [UNCLASSIFIED] + over
    return (fn(ns) if ns is not None else []) + over


def _parse_quietly(parser, argv):
    """`parser.parse_args(argv)`, or None; prints nothing (the server's stdout
    is the MCP channel)."""
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        try:
            return parser.parse_args(argv)
        except (Exception, SystemExit):            # noqa: BLE001 - usage errors, --help
            return None


# ------------------------------------------------------------------ the tools


@dataclass
class ToolSpec:
    name: str
    command: str                 # "" for emir_cli
    title: str
    description: str
    params: list
    outputs: list | None         # document names besides error; None: any object
    confirm: str                 # never | conditional | always
    confirm_when: list = field(default_factory=list)
    parser: object = None

    @property
    def writes(self) -> bool:
        """Whether a parameter names a file or directory the command writes
        (so it can overwrite one the person owns)."""
        return writes(self.command, self.params)

    @property
    def read_only(self) -> bool:
        return self.command in READ_ONLY and not self.writes

    @property
    def annotations(self) -> dict:
        """MCP tool annotations (hints for the client): a tool that writes to a
        path the caller chooses is neither read-only nor idempotent, and may
        overwrite a file (destructive)."""
        known = self.command in UNGATED or self.command in GATES
        return {"title": self.title, "readOnlyHint": self.read_only,
                "destructiveHint": (self.command in DESTRUCTIVE or self.writes)
                if known else True,
                "idempotentHint": self.read_only,
                "openWorldHint": not known}

    def input_schema(self) -> dict:
        props = {p.name: p.schema() for p in self.params}
        if self.confirm != "never":
            props[CONFIRM] = {
                "type": "boolean", "default": False,
                "description": "true only after the person agreed to this call: "
                               + "; ".join(self.confirm_when or [UNCLASSIFIED.reason])}
        s = {"type": "object", "properties": props, "additionalProperties": False}
        req = [p.name for p in self.params if p.required]
        if req:
            s["required"] = req
        dep = DEPENDENT.get(self.command)
        if dep and dep[0] in props:
            need, when = dep[0], [w for w in dep[1] if w in props]
            s["anyOf"] = [{"required": [need]},
                          {"not": {"anyOf": [{"required": [w]} for w in when]}}]
        return s

    def output_schema(self) -> dict:
        """`anyOf` the documents the tool returns (the emir schemas, without
        their `$schema`/`$id`), always including `emir.error/1`."""
        if self.outputs is None:
            return {"type": "object"}
        from . import schemas
        subs = []
        for n in [*self.outputs, "error"]:
            s = {k: v for k, v in schemas.get(n).items() if k not in ("$schema", "$id")}
            subs.append(portable(s))
        return {"type": "object", "anyOf": subs}

    def as_dict(self) -> dict:
        return {"name": self.name, "command": self.command or None, "title": self.title,
                "description": self.description, "input_schema": self.input_schema(),
                "outputs": (None if self.outputs is None else [*self.outputs, "error"]),
                "annotations": self.annotations, "confirm": self.confirm,
                "confirm_when": list(self.confirm_when)}


def portable(schema):
    """`schema` in the keywords draft-07 and 2020-12 share: a tuple's
    `prefixItems` (2020-12 only) becomes `items` of any of its members
    (looser, never stricter: a valid document stays valid)."""
    if isinstance(schema, list):
        return [portable(x) for x in schema]
    if not isinstance(schema, dict):
        return schema
    out = {k: portable(v) for k, v in schema.items() if k != "prefixItems"}
    if "prefixItems" in schema and "items" not in schema:
        out["items"] = {"anyOf": portable(schema["prefixItems"])}
    return out


def tool_name(command: str) -> str:
    return TOOL_PREFIX + command.replace("-", "_").replace(" ", "_")


def served_commands() -> list:
    """Every command a tool serves: the CLI's commands, a command with
    sub-commands as one entry per sub-command (`process new`), not `mcp`."""
    from . import cli
    out = []
    for c in sorted(cli.COMMANDS):
        if c in NOT_SERVED:
            continue
        subs = cli.SUBCOMMANDS.get(c)
        out += [f"{c} {s}" for s in subs] if subs else [c]
    return sorted(out)


def resolve(command: str, argv: list) -> tuple:
    """`(command, argv)` with a sub-command folded into the command:
    ("process", ["new", ...]) -> ("process new", [...])."""
    from . import cli
    subs = cli.SUBCOMMANDS.get(command)
    if subs and argv and argv[0] in subs:
        return f"{command} {argv[0]}", list(argv[1:])
    return command, list(argv)


def _outputs(command: str):
    from . import schemas
    if command in OUTPUTS:
        return OUTPUTS[command]
    k = command.replace("-", "_")
    return [k] if k in schemas.SCHEMAS else None


def _description(command: str, parser, confirm: str, when: list) -> str:
    note = NOTES.get(command, "")
    # the description's first paragraph (import-ict's is a whole manual)
    desc = " ".join((getattr(parser, "description", None) or "").strip()
                    .split("\n\n")[0].split())
    outs = _outputs(command)
    doc = f"emir.{outs[0]}/1" if outs else "a JSON object"
    parts = [note or desc,
             f"Runs `emir {command} --format json` and returns its document ({doc}, or "
             f"emir.error/1 with a code when it cannot do its job)."]
    if confirm == "conditional":
        parts.append("ASK THE PERSON FIRST when " + "; or when ".join(when)
                     + ". Then pass confirm=true.")
    elif confirm == "always":
        parts.append(f"ASK THE PERSON FIRST: {UNCLASSIFIED.reason}. Then pass confirm=true.")
    return " ".join(_sentence(p) for p in parts if p.strip())


def _sentence(text: str) -> str:
    text = text.strip()
    return text if text.endswith((".", ")", ":")) else text + "."


def _spec(command: str) -> ToolSpec | None:
    parser = capture_parser(command)
    if parser is None:
        return None
    params = params_of(command, parser)
    confirm = ("never" if command in UNGATED else
               "conditional" if command in GATES else "always")
    when = list(GATE_WHEN.get(command, [UNCLASSIFIED.reason] if confirm == "always" else []))
    if writes(command, params):
        # every command that writes where the caller says can be asked to
        # overwrite a file the person owns
        when.append(OVERWRITE_WHEN)
        if confirm == "never":
            confirm = "conditional"
    return ToolSpec(name=tool_name(command), command=command,
                    title=f"emir {command}",
                    description=_description(command, parser, confirm, when),
                    params=params, outputs=_outputs(command),
                    confirm=confirm, confirm_when=when, parser=parser)


def _generic_spec() -> ToolSpec:
    return ToolSpec(
        name=GENERIC, command="", title="emir (command line)",
        description="Run one emir command line as printed in a `next_steps` or plan "
                    "`command` (e.g. \"emir run x.dspf --process p.yaml --currents c.json\"), "
                    "for what the typed emir_* tools do not cover. No shell is involved: "
                    "emir commands only (EMIR-E591 otherwise). Returns the command's JSON "
                    "document. The same go-ahead rule as the typed tools: ASK THE PERSON "
                    "FIRST when the command runs a licensed tool, runs long, rewrites a "
                    "file in place, or is a command this server has not classified; then "
                    "pass confirm=true.",
        params=[Param(name="command_line", dest="command_line", flag=None, kind="string",
                      required=True,
                      help="the emir command line, with or without the leading `emir`; "
                           "--format is ignored (the result is always the JSON document)")],
        outputs=None, confirm="conditional",
        confirm_when=["the command runs a licensed tool, runs long, rewrites a file in "
                      "place, or is not classified by this server"])


_SPECS: dict | None = None


def specs(refresh: bool = False) -> dict:
    """`{tool name: ToolSpec}` for every served command, then `emir_cli`."""
    global _SPECS
    with _RUN:
        return _specs(refresh)


def _specs(refresh: bool) -> dict:
    global _SPECS
    if _SPECS is None or refresh:
        out = {}
        for c in served_commands():
            s = _spec(c)
            if s is not None:
                out[s.name] = s
        g = _generic_spec()
        out[g.name] = g
        _SPECS = out
    return _SPECS


def by_command(command: str) -> ToolSpec | None:
    return specs().get(tool_name(command)) if command not in NOT_SERVED else None


# ------------------------------------------------------------------ arguments <-> argv


def _token(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return repr(v) if isinstance(v, float) else str(v)


def _risky(s: str) -> bool:
    """A value argparse would read as an option or an @argument file."""
    return s.startswith(("-", "@"))


def to_argv(spec: ToolSpec, args: dict) -> list:
    """The command-line arguments of a tool call (positionals first, then the
    options in the parser's order). A value that starts with `-` or `@` is
    passed as `--opt=value` (a positional as `./value`), so no value is ever
    read as an option or an argument file."""
    pos, opts = [], []
    for p in spec.params:
        v = args.get(p.name)
        if v is None:
            continue
        if p.flag is None:
            for x in (v if isinstance(v, list) else [v]):
                s = _token(x)
                pos.append("./" + s if _risky(s) else s)
            continue
        if p.kind == "flag":
            if v:
                opts.append(p.flag)
        elif p.kind == "tristate":
            opts.append(p.flag if v else p.neg_flag)
        elif p.kind == "array":
            vals = [_token(x) for x in v]
            if p.append:
                for s in vals:
                    opts += [f"{p.flag}={s}"] if _risky(s) else [p.flag, s]
            elif vals:
                bad = [s for s in vals if _risky(s)]
                if bad:
                    raise EmirError("EMIR-E513", f"{p.name}: {bad[0]!r} starts with "
                                    f"'-' or '@', which {p.flag} cannot take",
                                    command=spec.command)
                opts += [p.flag, *vals]
        else:
            s = _token(v)
            opts += [f"{p.flag}={s}"] if _risky(s) else [p.flag, s]
    return pos + opts


def from_namespace(spec: ToolSpec, ns) -> dict:
    """The tool arguments a parsed command line stands for (the options left
    at their defaults are left out)."""
    out = {}
    for p in spec.params:
        v = getattr(ns, p.dest, None)
        if v is None or v == p.default or (p.kind == "array" and not v):
            continue
        if p.kind == "flag":
            out[p.name] = True
        elif p.kind == "array":
            out[p.name] = [x for x in v]
        else:
            out[p.name] = v
    return out


def split_command_line(line: str):
    """`(command, argv, was_emir)` of an emir command line, `--format` dropped.
    `was_emir` is False when the line does not start with `emir`."""
    from .output import strip_options
    try:
        tokens = shlex.split(line)
    except ValueError as e:
        raise EmirError("EMIR-E507", f"the command line does not split: {e}",
                        command="mcp") from e
    was_emir = bool(tokens) and (tokens[0] == "emir" or tokens[0].endswith("/emir"))
    if was_emir:
        tokens = tokens[1:]
    if not tokens:
        raise EmirError("EMIR-E507", "an empty command line", command="mcp")
    return tokens[0], strip_options(tokens[1:], {"--format": 1}), was_emir


def tool_call_for(command_line: str) -> dict | None:
    """`{"name", "arguments"}` of the typed tool that runs an emir command
    line (None for anything else: a simulator, a shell command)."""
    try:
        command, argv, was_emir = split_command_line(command_line)
    except EmirError:
        return None
    command, argv = resolve(command, argv)
    spec = by_command(command) if was_emir else None
    if spec is None:
        return None
    ns = _parse_quietly(spec.parser, argv)
    if ns is None:
        return None
    return {"name": spec.name, "arguments": from_namespace(spec, ns)}


# ------------------------------------------------------------------ running


#: commands that run in a fresh interpreter even in-process mode: the
#: self-test checks what importing emir pulls in, which in a long-lived
#: server depends on the calls before it
FRESH = frozenset({"selftest"})
#: set in a fresh interpreter's environment, so `emir next` there still
#: gives each step its tool call
CHILD_ENV = "EMIR_MCP_CHILD"
#: `emir mcp --isolate`: every call in a fresh interpreter
ISOLATE = {"on": False}


def in_mcp() -> bool:
    """True inside a tool call (in-process, or in the fresh interpreter one
    runs in)."""
    return bool(IN_MCP.get()) or os.environ.get(CHILD_ENV) == "1"


def invoke(command: str, argv: list) -> tuple:
    """Run `emir <command> --format json argv`; returns `(exit status, the
    one document)`. In-process through `cli.main` (or in a fresh interpreter
    for `FRESH` commands and under --isolate), one call at a time."""
    with _RUN:
        if command in FRESH or ISOLATE["on"]:
            return _invoke_fresh(command, argv)
        return _invoke_here(command, argv)


def _with_json(argv: list) -> list:
    """`argv` and `--format json` (last, so the provenance line a run writes
    reads like the command a person types; before a `--`, which ends the
    options)."""
    if "--" in argv:
        i = argv.index("--")
        return [*argv[:i], "--format", "json", *argv[i:]]
    return [*argv, "--format", "json"]


def _invoke_here(command: str, argv: list) -> tuple:
    from . import cli
    buf = io.StringIO()
    token = IN_MCP.set(True)
    stdin = sys.stdin
    # stdin is the MCP channel: a command never reads it (`--from -` reads
    # nothing)
    sys.stdin = io.StringIO("")
    try:
        with contextlib.redirect_stdout(buf):
            rc = cli.main([*command.split(), *_with_json(argv)])
    except BaseException as e:                      # noqa: BLE001 - EMIR_DEBUG re-raises
        from .output import error_document
        err = cli.coded(e) if isinstance(e, Exception) else EmirError(
            "EMIR-E904", "interrupted")
        return err.exit_code, error_document(err, command=command)
    finally:
        sys.stdin = stdin
        IN_MCP.reset(token)
    return rc, one_document(buf.getvalue(), command)


def _invoke_fresh(command: str, argv: list) -> tuple:
    """The same, as `python -m emir` in a child process (its stderr is the
    server's stderr; stdin is /dev/null)."""
    import subprocess
    # the child imports this same emir, wherever the server found it (a
    # clone run with `python -m emir` from another directory included)
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.pathsep.join(p for p in (here, os.environ.get("PYTHONPATH")) if p)
    env = {**os.environ, CHILD_ENV: "1", "PYTHONPATH": path}
    try:
        p = subprocess.run([sys.executable, "-m", "emir", *command.split(), *_with_json(argv)],
                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=None, env=env, text=True, check=False)
    except OSError as e:
        from .output import error_document
        err = EmirError("EMIR-E901", f"internal error: cannot start {sys.executable}: {e}")
        return err.exit_code, error_document(err, command=command)
    return p.returncode, one_document(p.stdout, command)


def one_document(text: str, command: str) -> dict:
    """The JSON document of a command's stdout (an EMIR-E901 error document if
    there is not exactly one)."""
    from .output import error_document
    body = text.lstrip()
    try:
        doc, end = json.JSONDecoder().raw_decode(body)
        why = ("more than one document" if body[end:].strip() else
               None if isinstance(doc, dict) else "not a JSON object")
    except ValueError as e:
        why = str(e)
    if why is None:
        return doc
    err = EmirError("EMIR-E901", f"internal error: `emir {command}` did not print one "
                    f"JSON document ({why})", details={"stdout": text[:2000]})
    return error_document(err, command=command)


def refusal(command: str, tool: str, reasons: list, args) -> dict:
    """The EMIR-E590 document of a call that needs the person's go-ahead."""
    from .output import error_document
    err = EmirError(
        "EMIR-E590",
        f"{tool} needs the person's go-ahead: " + "; ".join(g.reason for g in reasons),
        details={"tool": tool, "arguments": args, "reasons": [g.as_dict() for g in reasons],
                 "licensed_tool": next((g.licensed_tool for g in reasons if g.licensed_tool),
                                       None),
                 "long_run": any(g.long_run for g in reasons)})
    return error_document(err, command=command or None)


def call(spec: ToolSpec, arguments: dict) -> tuple:
    """One typed tool call: `(exit status, document, the emir command line)`."""
    with _RUN:
        return _call(spec, arguments)


def argument_error(spec: ToolSpec, arguments: dict) -> EmirError | None:
    """EMIR-E592 when a call's arguments do not match the tool: a name the
    tool has no parameter for (every input schema says
    `additionalProperties: false`, and a misspelt option must not be dropped
    silently), or a required parameter left out -- named as the tool's
    parameter (`from_file`), not as the CLI option (`--from`). `confirm` is
    accepted by every tool (on an ungated one it changes nothing)."""
    import difflib
    names = [p.name for p in spec.params]
    known = set(names) | {CONFIRM}
    unknown = sorted(k for k in arguments if k not in known)
    missing = [p.name for p in spec.params
               if p.required and arguments.get(p.name) in (None, "", [])]
    if not (unknown or missing):
        return None
    parts = []
    for k in unknown:
        close = difflib.get_close_matches(k, names, n=1)
        parts.append(f"no parameter {k!r}" + (f" (did you mean {close[0]!r}?)" if close else ""))
    if missing:
        parts.append("missing required " + ", ".join(missing))
    return EmirError("EMIR-E592", f"{spec.name}: " + "; ".join(parts) + ". Its parameters: "
                     + ", ".join(names + ([CONFIRM] if spec.confirm != "never" else [])),
                     details={"tool": spec.name, "unknown": unknown, "missing": missing,
                              "parameters": names,
                              "required": [p.name for p in spec.params if p.required]})


def _call(spec: ToolSpec, arguments: dict) -> tuple:
    bad = argument_error(spec, arguments)
    if bad is not None:
        from .output import error_document
        return bad.exit_code, error_document(bad, command=spec.command or "mcp"), None
    args = {k: v for k, v in arguments.items() if v is not None}
    confirm = bool(args.pop(CONFIRM, False))
    if spec.name == GENERIC:
        return _call_line(args.get("command_line", ""), confirm)
    try:
        argv = to_argv(spec, args)
    except EmirError as e:
        from .output import error_document
        return e.exit_code, error_document(e, command=spec.command), None
    line = shlex.join(["emir", *spec.command.split(), *_with_json(argv)])
    why = gates(spec.command, argv, spec.parser)
    if why and not confirm:
        return lookup("EMIR-E590").exit_code, refusal(spec.command, spec.name, why, args), line
    rc, doc = invoke(spec.command, argv)
    return rc, doc, line


def call_line(command_line: str, confirm: bool = False) -> tuple:
    """`emir_cli`: one emir command line."""
    with _RUN:
        return _call_line(command_line, confirm)


def _call_line(command_line: str, confirm: bool) -> tuple:
    from .output import error_document
    try:
        command, argv, was_emir = split_command_line(command_line)
        if command in NOT_SERVED:
            raise EmirError("EMIR-E591", f"`emir {command}` is the server itself; it does "
                            f"not run inside it", command="mcp")
        if command not in _commands():
            if was_emir:
                raise EmirError("EMIR-E508", f"unknown command {command!r}; `emir --help` "
                                f"lists them")
            raise EmirError("EMIR-E591", f"`{command}` is not an emir command: the MCP "
                            f"server runs emir commands only (no shell); run it in a "
                            f"shell, or ask the person", command="mcp")
    except EmirError as e:
        return e.exit_code, error_document(e, command="mcp"), None
    command, argv = resolve(command, argv)
    spec = by_command(command)
    line = shlex.join(["emir", *command.split(), *_with_json(argv)])
    why = gates(command, argv, spec.parser if spec else None)
    if why and not confirm:
        return (lookup("EMIR-E590").exit_code,
                refusal(command, GENERIC, why, {"command_line": command_line}), line)
    rc, doc = invoke(command, argv)
    return rc, doc, line


def _commands() -> set:
    from . import cli
    return set(cli.COMMANDS) - NOT_SERVED


def result_parts(rc: int, doc: dict, line: str | None) -> dict:
    """What a tool call returns, SDK-neutral: the document as structured
    content and as JSON text, `is_error` for an `emir.error/1` document, and
    the exit status and command line in `_meta`."""
    from .output import dumps
    return {"text": dumps(doc), "structured": doc,
            "is_error": doc.get("schema") == "emir.error/1",
            "meta": {"emir/exit_code": rc, "emir/command_line": line}}
