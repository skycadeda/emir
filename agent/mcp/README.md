# Connecting an MCP client to EMIR

`emir mcp` is EMIR as a [Model Context Protocol](https://modelcontextprotocol.io)
server over stdio. An MCP client (Claude Code, Claude Desktop, Codex, Cursor,
or any other) starts it and gets:

- **one typed tool per emir command**, `emir_<command>` (`emir_run`,
  `emir_check`, `emir_next` ...), whose parameters are the command's own
  options; a command made of sub-commands is one tool per sub-command
  (`emir_process_new`, `emir_process_check`). A tool returns exactly the JSON
  document `emir <command> --format json` prints, as structured content and
  as JSON text, with the same `schema` field, error codes and `next_steps`;
- **`emir_cli`**, which runs one whole emir command line, for example a
  `next_steps` command copied as printed;
- **resources**: the error catalog (`emir://catalog/errors.md`), every JSON
  Schema (`emir://schema/<name>.json`) and, from a clone of this repository,
  `AGENTS.md` and the agent playbooks by path (`emir://doc/AGENTS.md`,
  `emir://doc/agent/playbooks/...`).

Agents that can run shell commands can use the `emir` CLI directly (see
`AGENTS.md`); MCP is the way in for clients without a shell, and a typed,
discoverable one for those with one.

## Install and check

```sh
pip install -e '.[mcp]'            # from the clone root (emir is not on PyPI); a wheel: pip install 'mcp>=1.19'
emir mcp --list                    # the tools, resources and this machine's snippets
```

`emir mcp --list` works without the MCP SDK and says whether it is
installed (`--format json` gives the `emir.mcp/1` document). Its `clients`
field holds every snippet below with this machine's absolute path to
`emir`, which is what a desktop app needs.

The server needs the official MCP Python SDK, `mcp>=1.19`; both of its lines
work (1.x `FastMCP`, 2.x `MCPServer`).

## Clients

The snippets in this directory assume `emir` is on the client's `PATH`.

| client | file here | where it goes |
|---|---|---|
| Claude Code | [`claude-code.sh`](claude-code.sh) | run once in the project directory: `claude mcp add emir -- emir mcp` |
| Claude Code, shared with a team | [`claude-code.mcp.json`](claude-code.mcp.json) | `.mcp.json` at the project root (or `claude mcp add --scope project emir -- emir mcp`) |
| Claude Desktop | [`claude_desktop_config.json`](claude_desktop_config.json) | `claude_desktop_config.json` (Settings, Developer, Edit Config) |
| Codex | [`codex-config.toml`](codex-config.toml) | `~/.codex/config.toml` |
| Cursor | [`cursor-mcp.json`](cursor-mcp.json) | `.cursor/mcp.json` in the project, or `~/.cursor/mcp.json` |
| any other | [`generic.json`](generic.json) | the client's MCP server settings: transport stdio, command `emir`, arguments `mcp` |

Notes:

- **Claude Desktop** starts servers without your shell's `PATH` and from
  its own working directory: give the absolute path of `emir` (`emir mcp
  --list` prints it) and `--cwd` with the directory your files are in.
- **Relative paths** in tool arguments are taken from the server's working
  directory: the project directory for Claude Code, Codex and Cursor, or
  `--cwd`. Absolute paths always work.
- After adding the server, ask the agent to "run emir_selftest": it proves
  the install in a few seconds without touching your data.

## The go-ahead rule

A tool call that would

- start a licensed tool (`emir_run` with `netlist`
  runs Spectre; `emir_simulate` and `emir_verify_sim` with `run` true and
  `sim` spectre or hspice; `emir_extract` with `run` true runs PVS and
  Quantus),
- run long (`emir_run` on a DSPF of ADC-top or PLL-top size, the threshold
  `emir next` uses),
- rewrite a file in place (`emir_import_ict` without `out`), or
- run a command this server has not classified (a command a later release
  adds),

is refused with `EMIR-E590` unless it carries `confirm: true`. Nothing runs;
`details.reasons` says why. The agent asks the person and passes `confirm:
true` only after they agree. `emir_next`'s steps say the same with
`needs_human`. This is a guard rail for agents, not a sandbox: the server
runs as you and reads and writes files wherever the commands do; MCP clients
also ask you to approve tool calls.

## Options

| option | what |
|---|---|
| `--list` | print what is served and the client snippets, then exit |
| `--cwd DIR` | the working directory for relative paths |
| `--agent-dir DIR` | a clone of this repository, to serve AGENTS.md and the playbooks (found automatically when emir runs from a clone) |
| `--isolate` | run every tool call in a fresh Python process: slower, each call starts clean, a big run's memory is freed |
| `--no-output-schema` | do not advertise output schemas (for a client whose validator rejects them) |

## How it works

- The tools are built from the CLI's own argparse parsers, so a tool
  parameter and its CLI option cannot drift, and a command a later release
  adds is served at once (behind the go-ahead rule until it is classified).
- A call runs the command in the server's process (`emir selftest` in a
  fresh one, since it checks what importing emir pulls in), one call at a
  time; logs go to stderr, which clients show as the server's log.
- While serving, stdout is the protocol channel only: file descriptor 1 is
  pointed at stderr, so no print, library or child process can corrupt it.
- Over MCP, `emir_next` gives each step a `tool` field: the tool call (name
  and arguments) that runs that step exactly.

## Troubleshooting

| symptom | cause, fix |
|---|---|
| `EMIR-E603` when the server starts | the MCP SDK is missing or older than 1.19: `pip install -e '<clone>[mcp]'` from a clone, else `pip install 'mcp>=1.19'` |
| the client says the server failed to start | `emir` is not on the client's `PATH`: use the absolute path from `emir mcp --list` |
| `EMIR-E101` on a file you can see | a relative path from another working directory: pass absolute paths, or `--cwd` |
| `EMIR-E590` | the call needs the person's go-ahead (above) |
| `EMIR-E591` from `emir_cli` | not an emir command (the server has no shell): run it in a shell |
| `EMIR-E592` | an argument the tool does not have (a misspelt option), or a required one missing: nothing ran; `details` names them |
| the client rejects a result's structure | start the server with `--no-output-schema` and report it |

Every code: `emir explain <code>`, or the resource `emir://catalog/errors.md`.
