# 00 Preflight, errors and MCP: prove the install, branch on codes

Run the preflight at the start of every session, after an install or upgrade, and whenever a command behaves
oddly: seconds, no design data, no licence. When a command fails, its code says what to do. MCP clients get the
same commands as typed tools. **Screening, not sign-off.** EMIR is a research project, provided as is, with no
warranty or guarantee of accuracy: the person tests and verifies its results before relying on them; say so with
every result you report.

## When to use

- Always first, before reading, simulating or screening any of the person's files.
- A command exited non-zero, printed an `emir.error/1` document, or the person asks what a code means.
- The person's client speaks MCP (Claude Desktop, or typed tools wanted in Claude Code, Codex, Cursor).

## Inputs

None for the preflight: the self-test runs on the bundled sample, whose process file is ILLUSTRATIVE (invented
limits). For an error: the failing command line and its JSON document (`--format json`).

## Commands

### Worked example (runs as printed from the repository root)

<!-- test: run -->
```sh
emir --version
emir info --format json
emir selftest --format json
emir validate --quick --format json
```

`emir info` lists `tools[]` (spectre, hspice, xyce, ngspice, magic, virtuoso, pvs, quantus: `found`, `path`,
`licensed`, `version`, and for the simulators `verified`), `magic_route` (the magic SKY130's extraction runs, often
not `tools[]`'s stock magic, and `writes_geometry`), `PDK_ROOT` under `settings[]`, and `packages[]` (`version`
null when not installed).
`emir validate --quick` runs the 6 analytic validation cases (none is slow; `--quick` is kept for the scripts that
pass it):

<!-- test: expect -->
```json
{"schema": "emir.validate/1", "passed": true, "n_failed": 0}
```

**Verify a simulator on first use** (`tools[].verified` null), before the first run that uses its results here;
`emir next` plans it:

```sh
emir verify-sim --sim xyce --format json           # open simulators: runs at once (seconds)
emir verify-sim --sim spectre --format json        # licensed: writes the decks, prints the commands
emir verify-sim --sim spectre --run --format json  # only after the person said yes
```

`passed` true: later runs say `verified`. `passed` false (exit 70, EMIR-E663): do not rely on that simulator here;
report the failing `checks[]` and the version. Spectre APS or X: the person's options with `--sim-args`.

### When a command fails

Every failure is an `emir.error/1` document with `code`, `message`, `hint` and `next_steps` (they run as printed).
A net that is not in the DSPF:

<!-- test: run -->
```sh
emir pins emir/data/sample/rail.dspf --net VSS --format json   # exit 2
```

<!-- test: expect -->
```json
{"schema": "emir.error/1", "command": "pins", "code": "EMIR-E204", "exit_code": 2,
 "next_steps": [{"command": "emir nets emir/data/sample/rail.dspf --format json"}]}
```

Any code, explained (from the registry, never from memory):

<!-- test: run -->
```sh
emir explain E415 --format json
```

<!-- test: expect -->
```json
{"schema": "emir.explain/1", "codes": [{"code": "EMIR-E415", "level": "error", "exit_code": 2, "retired": false}]}
```

### MCP

`emir mcp` serves every command as a typed tool (`emir_run`, `emir_next`, ...) plus `emir_cli` for a whole command
line, and the error catalog, the schemas, AGENTS.md and these playbooks as resources:

<!-- test: run -->
```sh
emir mcp --list --format json
```

<!-- test: expect -->
```json
{"schema": "emir.mcp/1", "server": {"name": "emir", "transport": "stdio"}}
```

Its `clients` hold this machine's configuration snippets; the person registers the server (for Claude Code,
`claude mcp add emir -- emir mcp`). A tool returns exactly what `emir <command> --format json` prints. Calls, each
with the tool's own parameter names:

<!-- test: tool-call -->
```json
{"name": "emir_selftest", "arguments": {}}
```

<!-- test: tool-call -->
```json
{"name": "emir_report", "arguments": {"from_file": "emir_out/summary.json"}}
```

<!-- test: tool-call -->
```json
{"name": "emir_cli", "arguments": {"command_line": "emir report --from emir_out/summary.json"}}
```

A call that would start a licensed tool, run long or rewrite a file in place is refused with EMIR-E590 until the
person agrees; then repeat it with `"confirm": true`. `examples/10_mcp_agent/run.sh` is a scripted session.

## Decision points

| you see | do |
|---|---|
| `emir: command not found` | `bin/emir` from a clone; or, with the person's go-ahead, `python3 -m venv .venv && .venv/bin/pip install -e '.[all]'` |
| exit 70, EMIR-E902 (self-test) or EMIR-E903 (validation) | stop; show the person the failing checks; screen nothing |
| exit 3, EMIR-E603 (a package) or EMIR-E601, EMIR-E660, EMIR-E662, EMIR-E680 (a tool) | tell the person; install nothing without asking (EMIR is not on PyPI: `pip install -e '<clone>[all]'`) |
| a simulator you will use has `verified` null | `emir verify-sim --sim xyce` (its name; licensed: `--run` after the person agrees) |
| spectre, virtuoso, pvs, quantus or hspice found | a Cadence or HSPICE site: playbook 04; each is licensed, ask before running one |
| xyce or ngspice found, no spectre; magic found | playbook 03 |
| EMIR-E101, EMIR-E102 | a path is wrong or unreadable (relative to the working directory, or the MCP server's) |
| EMIR-E204 | `emir nets` lists the nets |
| EMIR-E401, EMIR-E406 | `--currents` is not a JSON object, or does not balance: fix it with the person |
| EMIR-E408, EMIR-E409, EMIR-E463 | a binary result: re-simulate as text (`-format psfascii`, `Xyce -a`, `filetype=ascii`, `.option post=2`) |
| EMIR-E415 | the result lacks the branch currents rcc reads: playbook 01 (the save lines, a re-simulation) |
| EMIR-E428, EMIR-E429, EMIR-E431 | a refused window, no number: the block's period from the person, or the longer simulation `emir next` plans (playbook 01) |
| EMIR-W464, EMIR-W414, EMIR-W416 | the simulator is not verified here, failed its verification, or its version is unknown: say so with the result |
| EMIR-E507, EMIR-E508 | a usage error: `emir <command> --help`; options are never abbreviated |
| EMIR-E590, EMIR-E592 (MCP) | ask the person, then `"confirm": true`; or use the tool's own parameter names (`from_file`, not `--from`) |
| a retired code | `emir explain` gives the reason it was retired |

## Success criteria

- `emir selftest --format json` exited 0 with `"passed": true`; you know which simulators and packages exist.
- For a failure: you named the code, said what it means (from `emir explain`), and ran its fix or asked the person;
  no `--force` or `--no-check` without their decision.

## What to report

The EMIR version, "self-test passed", which simulators were found and which are verified here, that the licensed
tools are not started without asking, and which optional packages are missing. For a failure: the code and its
title, the cause in the person's terms, the fix or the question. Never report the sample's numbers as meaning
anything: its limits are invented.

## Failure modes

| code | exit | meaning | do |
|---|---|---|---|
| EMIR-E902 | 70 | self-test failed | stop; report the failing check |
| EMIR-E903 | 70 | a validation case failed | report it as an EMIR bug |
| EMIR-E901 | 70 | internal error | re-run with `EMIR_DEBUG=1`; report the traceback |
| EMIR-E663 | 70 | `emir verify-sim` failed | do not rely on that simulator here; report its checks |
| EMIR-W415 | 0 | invalid records in verified.json | run `emir verify-sim` again; never edit the file |
| EMIR-E511 | 2 | not an EMIR code | `emir explain --list` |
| EMIR-E591 | 2 | `emir_cli` got a non-emir command | the person runs it in a shell |
| EMIR-I691 | 0 | no AGENTS.md or playbooks beside the package (a wheel) | `emir mcp --agent-dir <clone>` |
