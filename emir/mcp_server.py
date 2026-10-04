"""`emir mcp`: EMIR as an MCP server (stdio), for MCP clients such as Claude
Code, Claude Desktop, Codex and Cursor.

    emir mcp                       serve over stdio (an MCP client starts this)
    emir mcp --list                what it serves, and this machine's client
                                   configuration snippets (no MCP SDK needed)

Tools: one per emir command, ``emir_<command>``, with the command's options
as typed parameters, returning the document ``emir <command> --format json``
prints (`emir/mcp_tools.py` builds them from the CLI's own parsers), plus
``emir_cli`` for a whole command line. A tool that would start a licensed
tool, run long, rewrite a file in place, loosen the EM verdict (an EM basis
other than the default; --dynamic with --ground, with --dyn-steps below 2000
or without --period), or run a command the server has not classified
needs ``confirm: true``, which the agent passes only after the
person agreed (EMIR-E590 otherwise).

Resources: the error catalog (``emir://catalog/errors.md``), every JSON
Schema (``emir://schema/<name>.json``) and, from a clone of the repository,
AGENTS.md and the agent playbooks by path (``emir://doc/agent/playbooks/...``).

The server needs the official MCP Python SDK (the ``mcp`` extra:
``pip install -e '.[mcp]'`` from a clone, else ``pip install mcp``);
it works with the SDK's 1.x line (``FastMCP``) and 2.x line (``MCPServer``).
While it serves, stdout belongs to the protocol: file descriptor 1 is pointed
at stderr, so neither a stray print nor a subprocess can corrupt the channel.
"""
from __future__ import annotations

import functools
import inspect
import io
import json
import os
import shlex
import sys

from . import RESEARCH_DISCLAIMER
from . import __version__
from .errors import EmirError, notice, step

#: the SDK versions the server is tested with (see tests/test_mcp.py)
SDK_REQUIREMENT = "mcp>=1.19"
SDK_MIN = (1, 19)

#: how big a document resource may be (a playbook is a few kB)
MAX_DOC_BYTES = 1 << 20


# ------------------------------------------------------------------ the SDK


def sdk_info() -> dict:
    """`{"installed", "version", "api", "supported", "requirement"}`."""
    from importlib import metadata
    try:
        ver = metadata.version("mcp")
    except metadata.PackageNotFoundError:
        ver = None
    api = None
    if ver is not None:
        api = "MCPServer" if _major(ver) >= 2 else "FastMCP"
    return {"installed": ver is not None, "version": ver, "api": api,
            "supported": ver is not None and _version_tuple(ver) >= SDK_MIN,
            "requirement": SDK_REQUIREMENT}


def _version_tuple(v: str) -> tuple:
    out = []
    for part in v.split(".")[:3]:
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)


def _major(v: str) -> int:
    return _version_tuple(v)[0] if v else 0


def _sdk():
    """`(server class, Tool class, types module)` of the installed SDK, or
    EMIR-E603 (exit 3) when it is missing or too old."""
    info = sdk_info()
    if not info["installed"] or not info["supported"]:
        have = f"mcp {info['version']} is installed" if info["installed"] else \
            "the mcp package is not installed"
        from .repo import install_hint
        raise EmirError("EMIR-E603", f"`emir mcp` needs the MCP Python SDK ({SDK_REQUIREMENT}); "
                        f"{have}. `{install_hint('mcp')}` installs it")
    from mcp import types
    try:
        from mcp.server.mcpserver import MCPServer as Server   # SDK 2.x
        from mcp.server.mcpserver.tools import Tool
    except ImportError:
        try:
            from mcp.server.fastmcp import FastMCP as Server   # SDK 1.x
            from mcp.server.fastmcp.tools import Tool
        except ImportError as e:
            raise EmirError("EMIR-E603", f"the installed MCP SDK ({info['version']}) has "
                            f"neither MCPServer nor FastMCP: {e}") from e
    return Server, Tool, types


# ------------------------------------------------------------------ resources


def agent_root(agent_dir: str | None = None) -> str | None:
    """The directory holding AGENTS.md and agent/ (a clone of the repo): the
    one given, else the package's parent when it is a clone, else None."""
    if agent_dir:
        if not os.path.isdir(agent_dir):
            raise EmirError("EMIR-E101", f"--agent-dir {agent_dir} is not a directory",
                            path=agent_dir)
        return os.path.abspath(agent_dir)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.isfile(os.path.join(root, "AGENTS.md")) or \
            os.path.isdir(os.path.join(root, "agent", "playbooks")):
        return root
    return None


def _doc_paths(root: str | None) -> list:
    """The agent documents served by path: AGENTS.md, agent/README.md, the
    playbooks and the MCP notes (never the evals, whose graders hold the
    answers)."""
    if not root:
        return []
    out = []
    for rel in ("AGENTS.md", os.path.join("agent", "README.md")):
        if os.path.isfile(os.path.join(root, rel)):
            out.append(rel)
    for sub in (os.path.join("agent", "playbooks"), os.path.join("agent", "mcp")):
        d = os.path.join(root, sub)
        if os.path.isdir(d):
            out += [os.path.join(sub, f) for f in sorted(os.listdir(d))
                    if f.endswith(".md") and os.path.isfile(os.path.join(d, f))]
    return out


def _read_doc(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        text = f.read(MAX_DOC_BYTES + 1)
    return text[:MAX_DOC_BYTES]


def resources(root: str | None) -> list:
    """`[{uri, name, mime_type, description, read}]`: what the server lists
    (`read()` gives the text when a client reads it)."""
    from . import schemas
    from .errors import catalog_markdown
    out = [{"uri": "emir://catalog/errors.md", "name": "errors",
            "mime_type": "text/markdown",
            "description": "Every EMIR error, warning and info code: meaning, hint, the "
                           "command that fixes or diagnoses it, and the exit statuses.",
            "read": catalog_markdown}]
    for n in schemas.names():
        out.append({"uri": f"emir://schema/{n}.json", "name": f"schema-{n}",
                    "mime_type": "application/schema+json",
                    "description": schemas.get(n)["title"] + " (JSON Schema 2020-12)",
                    "read": functools.partial(schemas.text, n)})
    for rel in _doc_paths(root):
        uri_path = rel.replace(os.sep, "/")
        out.append({"uri": f"emir://doc/{uri_path}", "name": uri_path,
                    "mime_type": "text/markdown",
                    "description": "agent document: " + uri_path,
                    "read": functools.partial(_read_doc, os.path.join(root, rel))})
    return out


# ------------------------------------------------------------------ the server


def instructions(cwd: str) -> str:
    """What the client shows the model about this server."""
    return (
        "EMIR screens extracted analog layout (a DSPF plus simulated currents) for "
        "electromigration (avg/rms/peak per wire and via) and IR drop. Screening, not "
        "sign-off. " + RESEARCH_DISCLAIMER + " Relay that with every result.\n"
        "Start with emir_selftest (proves the install), then emir_next with what you "
        "have (dspf, process, cc_sim, netlist, currents ...): it returns the ordered "
        "steps, each with the tool call that runs it (`tool`).\n"
        "Every emir_* tool returns the JSON document `emir <command> --format json` "
        "prints (its `schema` field names it; resource emir://schema/<name>.json). A "
        "failure is an emir.error/1 document with a code (isError true): branch on "
        "the code (emir_explain), never on the message. `next_steps` hold emir command "
        "lines: run each with emir_cli, or the typed tool of the same name; a step marked "
        "shell=true is not an emir command (emir_cli refuses it, EMIR-E591): run it in a "
        "shell if you have one, or leave it to the person. A tool's "
        "arguments are its parameter names exactly: an unknown or a missing required one "
        "is refused (EMIR-E592) and nothing runs.\n"
        "Guard rails: never invent process limits or measured values; never call a "
        "result sign-off; relay results with emir_report (its markdown). Before the "
        "first run that uses a simulator's result on this machine, verify that route "
        "with emir_verify_sim (sim=...): a run whose simulator is not verified here "
        "says so (EMIR-W464; EMIR-W414 when its latest verification failed, EMIR-W416 "
        "when the result does not say its version). A tool call that would run a licensed tool (Spectre, "
        "HSPICE, PVS, Quantus), run long, rewrite a file in place, or run an "
        "unclassified command is refused (EMIR-E590) unless it carries confirm=true: "
        "ask the person first, every time.\n"
        f"Relative paths are taken from the server's working directory: {cwd}")


class _AnyDocument:
    """The output model SDK 1.x and 2.0 want beside an output schema: any
    JSON object (built lazily: pydantic is the SDK's dependency, not emir's)."""
    model = None

    @classmethod
    def get(cls):
        if cls.model is None:
            from pydantic import BaseModel, ConfigDict

            class Document(BaseModel):
                model_config = ConfigDict(extra="allow")
            cls.model = Document
        return cls.model


def _annotation(p):
    """The Python type of a tool parameter (the SDK validates calls with it)."""
    from typing import Annotated, Literal, Optional

    from pydantic import Field
    base = {"string": str, "number": float, "integer": int}
    if p.kind == "flag":
        t = bool
    elif p.kind == "tristate":
        t = Optional[bool]                                       # noqa: UP045
    elif p.kind == "array":
        item = Literal[tuple(p.choices)] if p.choices else base[p.item]
        t = list[item]
    else:
        t = Literal[tuple(p.choices)] if p.choices else base[p.kind]
    # a plain `str` stays `str` when optional (default None, never sent as
    # null): the SDK JSON-decodes a string sent for any other annotation, which
    # would turn `--currents '{"VDD": 0.004, ...}'` into a dict
    if p.kind not in ("flag", "tristate") and t is not str:
        t = Optional[t]                                          # noqa: UP045
    return Annotated[t, Field(description=p.help)]


def _tool_function(spec, types):
    """An async function whose signature is the tool's parameters; it runs
    the call in a worker thread (the event loop keeps answering the client)."""
    from . import mcp_tools
    params = []
    for p in spec.params:
        # every parameter is optional to the SDK: a required one left out
        # reaches emir's own parser, whose usage error is coded (EMIR-E507);
        # the advertised schema still marks it required
        params.append(inspect.Parameter(p.name, inspect.Parameter.KEYWORD_ONLY,
                                        default=False if p.kind == "flag" else None,
                                        annotation=_annotation(p)))
    if spec.confirm != "never":
        from typing import Annotated

        from pydantic import Field
        params.append(inspect.Parameter(
            mcp_tools.CONFIRM, inspect.Parameter.KEYWORD_ONLY, default=False,
            annotation=Annotated[bool, Field(description="true only after the person "
                                                         "agreed to this call")]))

    async def tool(**kwargs):
        import anyio
        rc, doc, line = await anyio.to_thread.run_sync(
            functools.partial(mcp_tools.call, spec, kwargs))
        r = mcp_tools.result_parts(rc, doc, line)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=r["text"])],
            structuredContent=r["structured"], isError=r["is_error"], _meta=r["meta"])
    tool.__name__ = spec.name
    tool.__qualname__ = spec.name
    tool.__doc__ = spec.description
    tool.__signature__ = inspect.Signature(params, return_annotation=types.CallToolResult)
    return tool


def _strict(Tool, types):
    """`Tool` that checks a call's raw arguments before the SDK validates
    them: the SDK drops an argument its model does not know, so a misspelt
    option would vanish without a word. An unknown or missing argument is
    answered with emir's coded document (EMIR-E592, `mcp_tools.argument_error`)."""
    from . import mcp_tools

    class EmirTool(Tool):
        async def run(self, arguments, *args, **kwargs):
            spec = mcp_tools.specs().get(self.name)
            bad = mcp_tools.argument_error(spec, dict(arguments or {})) if spec else None
            if bad is None:
                return await super().run(arguments, *args, **kwargs)
            from .output import error_document
            doc = error_document(bad, command=spec.command or "mcp")
            r = mcp_tools.result_parts(bad.exit_code, doc, None)
            result = types.CallToolResult(
                content=[types.TextContent(type="text", text=r["text"])],
                structuredContent=r["structured"], isError=True, _meta=r["meta"])
            convert = kwargs.get("convert_result", args[1] if len(args) > 1 else False)
            return self.fn_metadata.convert_result(result) if convert else result
    EmirTool.__name__ = EmirTool.__qualname__ = Tool.__name__
    return EmirTool


def build_server(root: str | None = None, output_schemas: bool = True, cwd: str | None = None):
    """The MCP server object (not yet serving): every tool and resource."""
    from . import mcp_tools
    Server, Tool, types = _sdk()
    Tool = _strict(Tool, types)
    tools = []
    for spec in mcp_tools.specs().values():
        t = Tool.from_function(
            _tool_function(spec, types), name=spec.name, title=spec.title,
            description=spec.description,
            annotations=types.ToolAnnotations(**spec.annotations))
        # the advertised parameters are the CLI's (plain JSON Schema); the
        # SDK still validates each call against the function's types
        t.parameters = spec.input_schema()
        if output_schemas:
            t.fn_metadata.output_schema = spec.output_schema()
            # SDK 1.x and 2.0 want a model beside the schema (any JSON object
            # passes it: the document's own schema is the advertised one)
            if "output_model" in type(t.fn_metadata).model_fields:
                t.fn_metadata.output_model = _AnyDocument.get()
        tools.append(t)
    kw = {"name": "emir", "instructions": instructions(cwd or os.getcwd()), "tools": tools,
          "log_level": "WARNING"}
    if "version" in inspect.signature(Server.__init__).parameters:
        kw["version"] = __version__
    server = Server(**kw)
    for r in resources(root):
        server.resource(r["uri"], name=r["name"], description=r["description"],
                        mime_type=r["mime_type"])(_reader(r["read"], r["name"]))
    return server


def _reader(read, name):
    def resource() -> str:
        return read()
    resource.__name__ = "resource_" + "".join(ch if ch.isalnum() else "_" for ch in name)
    return resource


def divert_std_streams():
    """Serve the protocol from private copies of stdin and stdout, and point
    file descriptor 1 at stderr and 0 at /dev/null: a print, a C library or a
    simulator a command starts can then never write into (or read from) the
    MCP channel. The SDK's stdio transport takes `sys.stdin` / `sys.stdout`,
    which become the private copies."""
    for s in (sys.stdout, sys.stderr):
        try:
            s.flush()
        except (OSError, ValueError):
            pass
    wire_in, wire_out = os.dup(0), os.dup(1)          # not inherited (PEP 446)
    null = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null, 0)
    os.close(null)
    os.dup2(2, 1)
    sys.stdin = os.fdopen(wire_in, "r", encoding="utf-8")
    sys.stdout = os.fdopen(wire_out, "w", encoding="utf-8")


# ------------------------------------------------------------------ clients


def server_command() -> list:
    """The command that starts this server, as absolute as this install
    allows (a client started from a desktop app does not see the shell's
    PATH): the `emir` script beside this Python, else `python -m emir`."""
    exe = os.path.join(os.path.dirname(sys.executable), "emir")
    if os.path.isfile(exe) and os.access(exe, os.X_OK):
        return [exe, "mcp"]
    return [sys.executable, "-m", "emir", "mcp"]


def client_configs(command: list, project: str) -> dict:
    """Configuration snippets that start `command` (e.g. `["emir", "mcp"]`) in
    the MCP clients emir documents (agent/mcp/ holds the same with `emir`
    on PATH)."""
    exe, args = command[0], list(command[1:])
    entry = {"command": exe, "args": args}

    def js(obj):
        return json.dumps(obj, indent=2) + "\n"
    toml = (f"[mcp_servers.emir]\ncommand = {json.dumps(exe)}\n"
            f"args = [{', '.join(json.dumps(a) for a in args)}]\n")
    desktop = {"command": exe, "args": [*args, "--cwd", project]}
    return {
        "claude-code": {"file": "(a shell, in the project directory)", "format": "shell",
                        "snippet": shlex.join(["claude", "mcp", "add", "emir", "--", *command])
                        + "\n"},
        "claude-code-project": {"file": ".mcp.json (project root, shared with the team)",
                                "format": "json",
                                "snippet": js({"mcpServers": {"emir": {"type": "stdio",
                                                                       **entry}}})},
        "claude-desktop": {"file": "claude_desktop_config.json", "format": "json",
                           "snippet": js({"mcpServers": {"emir": desktop}})},
        "codex": {"file": "~/.codex/config.toml", "format": "toml", "snippet": toml},
        "cursor": {"file": ".cursor/mcp.json (project) or ~/.cursor/mcp.json",
                   "format": "json", "snippet": js({"mcpServers": {"emir": entry}})},
        "generic": {"file": "(the client's MCP server settings)", "format": "json",
                    "snippet": js({"emir": {"transport": "stdio", **entry}})},
    }


# ------------------------------------------------------------------ the command


def _listing(root, output_schemas: bool) -> dict:
    from . import mcp_tools
    cmd = server_command()
    info = sdk_info()
    steps = []
    if not info["supported"]:
        steps.append(step("install the MCP Python SDK the server needs",
                          shlex.join([sys.executable, "-m", "pip", "install",
                                      SDK_REQUIREMENT])))
    steps.append(step("register this server with Claude Code (once, in the project "
                      "directory); `clients` has the other clients' snippets",
                      shlex.join(["claude", "mcp", "add", "emir", "--", *cmd])))
    return {
        "sdk": info,
        "server": {"name": "emir", "version": __version__, "transport": "stdio",
                   "command": cmd, "cwd": os.getcwd(), "agent_dir": root,
                   "output_schemas": output_schemas, "isolate": mcp_tools.ISOLATE["on"],
                   "instructions": instructions(os.getcwd())},
        "tools": [s.as_dict() for s in mcp_tools.specs().values()],
        "resources": [{k: r[k] for k in ("uri", "name", "mime_type", "description")}
                      for r in resources(root)],
        "clients": client_configs(cmd, os.getcwd()),
        "next_steps": steps,
    }


def cmd_mcp(argv, out):
    from .output import document, parser
    ap = parser("emir mcp", description=(
        "Serve EMIR over the Model Context Protocol (stdio) for MCP clients: one typed "
        "tool per command, returning the command's JSON document. An MCP client starts "
        "it; stdout is the protocol channel, logs go to stderr. Needs the MCP Python "
        "SDK (the `mcp` extra: pip install -e '.[mcp]' from a clone, or pip install mcp)."))
    ap.add_argument("--list", action="store_true",
                    help="print the tools, resources and this machine's client "
                         "configuration snippets, and exit (works without the SDK)")
    ap.add_argument("--cwd", default=None, metavar="DIR",
                    help="the working directory for the tools' relative paths "
                         "(default: where the client starts the server)")
    ap.add_argument("--agent-dir", default=None, metavar="DIR",
                    help="a clone of the emir repository, to serve AGENTS.md and the "
                         "agent playbooks as resources (default: found beside the "
                         "package, in a clone)")
    ap.add_argument("--isolate", action="store_true",
                    help="run every tool call in a fresh Python process: slower, but "
                         "each call starts clean and a big run's memory is freed")
    ap.add_argument("--no-output-schema", action="store_true",
                    help="do not advertise the tools' output schemas (for a client whose "
                         "validator rejects them)")
    a = ap.parse_args(argv)
    # --agent-dir is taken from where emir was started, before --cwd moves
    root = agent_root(a.agent_dir)
    if a.cwd:
        if not os.path.isdir(a.cwd):
            raise EmirError("EMIR-E101", f"--cwd {a.cwd} is not a directory", path=a.cwd)
        os.chdir(a.cwd)
    from . import mcp_tools
    mcp_tools.ISOLATE["on"] = bool(a.isolate)
    if a.list:
        doc = _listing(root, not a.no_output_schema)
        sdk = doc["sdk"]
        print(f"emir mcp: {len(doc['tools'])} tools, {len(doc['resources'])} resources; "
              f"MCP SDK {sdk['version'] or 'not installed'}"
              + ("" if sdk["supported"] else f" (needs {SDK_REQUIREMENT})"))
        for t in doc["tools"]:
            gate = {"never": "", "conditional": "  [ask first: conditional]",
                    "always": "  [ask first: always]"}[t["confirm"]]
            print(f"  {t['name']:18s} {t['title']}{gate}")
        print(f"start command: {shlex.join(doc['server']['command'])}")
        out.emit(document("mcp", **doc))
        return 0
    server = build_server(root, output_schemas=not a.no_output_schema)
    log = functools.partial(print, file=sys.stderr)
    if root is None:
        from .repo import url
        notice("EMIR-I691", "no AGENTS.md or agent/playbooks beside the package: serving "
               "the error catalog and the schemas only (--agent-dir adds the playbooks; "
               f"they are also at {url('agent/playbooks/')})",
               log=log)
    tty = " (stdin is a terminal: an MCP client starts this; Ctrl-D ends it)" \
        if _isatty(sys.stdin) else ""
    notice("EMIR-I690", f"serving {len(mcp_tools.specs())} tools over stdio, MCP SDK "
           f"{sdk_info()['version']}, working directory {os.getcwd()}{tty}", log=log)
    divert_std_streams()
    _exit_on_signals()
    server.run("stdio")
    return 0


def _exit_on_signals():
    """While serving, SIGTERM and SIGINT end the process at once with the
    contract's status (143, 130) and a coded line on stderr. The default
    path (an exception unwinding the event loop) would wait forever for the
    transport's thread blocked reading stdin, and stdout is the protocol
    channel, so no error document can be printed there anyway."""
    import signal
    import threading
    if threading.current_thread() is not threading.main_thread():
        return
    from .output import error_text

    def stop(signum, frame):
        err = (EmirError("EMIR-E906", "terminated (SIGTERM)") if signum == signal.SIGTERM
               else EmirError("EMIR-E904", "interrupted"))
        try:
            os.write(2, (error_text(err) + "\n").encode())
        finally:
            os._exit(err.exit_code)
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, stop)
        except (ValueError, OSError):            # pragma: no cover - no signals here
            pass


def _isatty(stream) -> bool:
    try:
        return stream.isatty()
    except (AttributeError, ValueError, io.UnsupportedOperation):
        return False
