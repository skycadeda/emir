# 10 · EMIR over MCP: an agent's session, scripted

`emir mcp` serves EMIR to MCP clients (Claude Code, Claude Desktop, Codex,
Cursor, ...). This example is the session such a client has with it, written
as a short Python script so it runs the same way every time, offline:

1. start `emir mcp` over stdio, as a client does, and list its tools;
2. ask `emir_next` for a plan on the bundled sample (a DSPF, its process file
   and DC currents);
3. make each planned tool call in order (`emir_selftest`, `emir_nets`,
   `emir_check`, `emir_run`, `emir_report`), stopping at the first failure;
4. show the go-ahead rule: an `emir_run` that would start Spectre (`run: true`,
   in the mode the person chose) is refused with `EMIR-E590`, because the call
   carries no `confirm: true` (an agent passes it only after the person agreed).

No network, no simulator, no licence is used.

## Run it

From a clone, in a Python environment with emir and the MCP SDK:

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[mcp]'
EMIR_PYTHON=.venv/bin/python sh examples/10_mcp_agent/run.sh
```

It prints one line per tool call and ends with `matches expected/`:

```
emir_next        -> emir.next/1
emir_selftest    -> emir.selftest/1
emir_nets        -> emir.nets/1
emir_check       -> emir.check/1
emir_run         -> emir.run/1  verdict FAIL
emir_report      -> emir.report/1  verdict FAIL
emir_run         -> emir.error/1  EMIR-E590
matches expected/
```

The output directory (default `examples/10_mcp_agent/out/`, or the first
argument) holds:

| file | what |
|---|---|
| `transcript.json` | every call: the tool, its arguments, the result's schema, `isError`, the exit status, and the fields worth comparing |
| `report.md` | what the agent relays to the person (`emir_report`'s `markdown`) |
| `server.log` | the server's own log (its stderr: the plan, the self-test, the run), kept out of the terminal |
| `emir_out/summary.json` | the run summary (`emir.run/1`), written by `emir_run`'s `summary_out` |

`compare.py` checks `transcript.json` against `expected/transcript.json`:
strings and verdicts exactly, numbers within `expected/tolerances.json`
(relative 1e-6).

## What the numbers are

The sample is a synthetic rail made for the self-test (invented layer data,
not a real process). It is designed so that four 1 mA loads on a Metal1
rail give a worst ratio of 5/3 (1.667x the average-current limit, on the
three segments that carry all 4 mA), 10 violating segments and 54 mV of IR
drop; `emir selftest` checks the same values. The verdict is FAIL on
purpose: the report shows what an agent relays when something fails.
Screening, not sign-off.

## What to look at

- The plan's steps carry a `tool` field over MCP: the exact call
  (`{"name": "emir_check", "arguments": {...}}`) that runs each step.
- Each result is the same JSON document the CLI prints with
  `--format json`; `emir schema <name>` (or the resource
  `emir://schema/<name>.json`) describes it.
- The refused call's document (`emir.error/1`, `EMIR-E590`) lists why in
  `details.reasons`: here, Spectre is a licensed tool and the run can be long.
