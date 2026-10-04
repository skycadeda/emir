# 10 MCP: EMIR as typed tools

`emir mcp` serves every `emir` command as a typed MCP tool (`emir_run`, `emir_next`, ...) over stdio, plus
`emir_cli` for a whole command line and resources (the error catalog, every JSON Schema, AGENTS.md and these
playbooks). A tool returns exactly the document `emir <command> --format json` prints. **Screening, not sign-off.**

## When to use

- The person's client speaks MCP and has no shell (Claude Desktop), or typed, discoverable tools are wanted
  (Claude Code, Codex, Cursor).
- An agent with a shell can use the CLI directly (AGENTS.md): the documents, codes and plans are the same.

## Inputs

| input | where |
|---|---|
| EMIR with the MCP SDK | from a clone, `pip install -e '.[mcp]'` (EMIR is not on PyPI; with a wheel, `pip install 'mcp>=1.19'`); the person installs it |
| the client's configuration file | the person's: Claude Code (`claude mcp add`, or `.mcp.json`), Claude Desktop (`claude_desktop_config.json`), Codex (`~/.codex/config.toml`), Cursor (`.cursor/mcp.json`) |
| the directory the files are in | the server's working directory, or `--cwd` |

## Commands

### Worked example (runs as printed from the repository root)

<!-- test: run -->
```sh
emir mcp --list --format json
```

<!-- test: expect -->
```json
{"schema": "emir.mcp/1", "server": {"name": "emir", "transport": "stdio"}}
```

`sdk.supported` says whether the server can start (`emir mcp` without the SDK exits 3, EMIR-E603); `tools[]`
lists every tool with its `confirm` rule; `resources[]` the URIs; `clients` holds this machine's configuration
snippets with the absolute path of `emir`; `next_steps` the `claude mcp add` command. Give the person the snippet
for their client: they register the server (it writes their configuration, so you do not).

A session, as tool calls (each is checked against the tool's real parameters). Prove the install:

<!-- test: tool-call -->
```json
{"name": "emir_selftest", "arguments": {}}
```

Plan, with what the person gave you:

<!-- test: tool-call -->
```json
{"name": "emir_next", "arguments": {"dspf": "emir/data/sample/rail.dspf", "process": "emir/data/sample/process.yaml",
 "currents": "{\"VDD\": 0.004, \"MP0:s\": -0.001, \"MP1:s\": -0.001, \"MP2:s\": -0.001, \"MP3:s\": -0.001}"}}
```

Over MCP each planned step carries `tool`: the exact call that runs it. Make those calls in order, for example the
run and the report:

<!-- test: tool-call -->
```json
{"name": "emir_run", "arguments": {"dspf": "emir/data/sample/rail.dspf", "process": "emir/data/sample/process.yaml",
 "currents": "{\"VDD\": 0.004, \"MP0:s\": -0.001, \"MP1:s\": -0.001, \"MP2:s\": -0.001, \"MP3:s\": -0.001}",
 "summary_out": "emir_out/summary.json"}}
```

<!-- test: tool-call -->
```json
{"name": "emir_report", "arguments": {"from_file": "emir_out/summary.json"}}
```

A `next_steps` command copied as printed:

<!-- test: tool-call -->
```json
{"name": "emir_cli", "arguments": {"command_line": "emir report --from emir_out/summary.json"}}
```

No readable process data for the PDK (playbook 04, "No readable file"): the fill-in file, then, once the person
has typed the values with their sources, its check (it must pass before any run). `emir_process_new` writes a new
file only and never asks a person anything over MCP (its `interactive` option is not a parameter):

<!-- test: tool-call -->
```json
{"name": "emir_process_new", "arguments": {"from_dspf": "emir/data/sample/rail.dspf", "out": "emir_out/handpdk.yaml"}}
```

<!-- test: tool-call -->
```json
{"name": "emir_process_check", "arguments": {"process": "emir_out/handpdk.yaml", "dspf": "emir/data/sample/rail.dspf"}}
```

`examples/10_mcp_agent/run.sh` is this session driven by a scripted stdio client, with the go-ahead refusal.

### On the person's machine

```sh
emir mcp --list --format json
claude mcp add emir -- emir mcp
```

The second line is for Claude Code, run by the person once in their project directory (team-wide:
`claude mcp add --scope project emir -- emir mcp`, which writes `.mcp.json`). The other clients' snippets are in
`agent/mcp/` and in `clients` of `emir mcp --list`. Claude Desktop starts servers without the shell's PATH and
from its own directory: the absolute path of `emir` and `--cwd <the files' directory>`.

## Decision points

| you see | do |
|---|---|
| `isError` true | the document is `emir.error/1`: branch on `code` (`emir_explain` with `codes`), run its `next_steps` |
| EMIR-E590 (`details.reasons`) | the call needs the person's go-ahead (a licensed tool, a long run (a big DSPF, or `dynamic` with `dyn_steps` above 200000), a file rewritten in place, or a looser EM verdict: `emir_run` with an `em_basis` other than the default, or `dynamic` with `ground`, with `dyn_steps` below 2000 or without `period`): ask; only after their yes repeat it with `"confirm": true` |
| EMIR-E591 from `emir_cli` | not an `emir` command (the server has no shell): the person runs it |
| EMIR-E592 (`details`) | an argument the tool does not have (a misspelt option: `from_file`, not `--from`), or a required one left out; nothing ran: use the names of the tool's input schema |
| a next step with `"shell": true` | not an `emir` command (`ls`, `tail`, `klayout`): `emir_cli` refuses it (EMIR-E591); the person runs it |
| a step with `tool` null | not an `emir` command (the simulation): the person runs it |
| a step `waiting` | replace its `placeholders` with what the steps in `after` produced, then call it |
| EMIR-E101 on a file you can see | relative paths are from the server's working directory (in its instructions): use absolute paths |
| EMIR-E603 when the server starts | the SDK is missing: the person runs the install command the message names (`pip install -e '<clone>[mcp]'`, or `pip install mcp`) |
| a number is null | "not a number", never 0 |

## Success criteria

- `emir_selftest` returned `passed: true`.
- The plan's steps ran in order, or stopped at a coded failure you reported; no call carried `confirm: true`
  without the person's yes.
- The result was relayed from `emir_report`'s `markdown`.

## What to report

From `emir_report` (playbook 07): the verdict, worst items with fixes, IR, mode, temperature, what was not
checked, and "Screening, not sign-off." When connecting a client, report what the person must add to which file
(the snippet from `clients`), and that the go-ahead rule asks them before licensed or long runs.

## Failure modes

| code | exit | meaning | do |
|---|---|---|---|
| EMIR-E590 | 2 | the go-ahead is needed | ask the person |
| EMIR-E591 | 2 | `emir_cli` got a non-emir command | run it in a shell, or ask the person |
| EMIR-E603 | 3 | the MCP SDK is missing | the install command the message names (the person) |
| EMIR-E592 | 2 | an unknown argument, or a required one missing (`details` names them and the tool's parameters) | the tool's input schema lists them |
| EMIR-E507 | 2 | a parameter's value is wrong (`details.usage`) | the tool's input schema says what it takes |
| EMIR-I691 | 0 | no AGENTS.md or playbooks beside the package (a wheel install) | `emir mcp --agent-dir <clone>` serves them |
