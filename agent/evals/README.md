# Agent evals: does an agent drive emir correctly?

Task cards that a person might give an agent ("screen this block with these currents", "the only EM data is an
encrypted ICT", "plan EM/IR of a sky130 layout that has no DSPF"), each with fixture inputs built
from the examples, the expected outcome, and a deterministic grader. A runner gives the cards to any agent CLI in
a scratch copy of this repository and grades what it did and said. **Screening, not sign-off** -- and the graders
check that the agent says so.

## Run it

```sh
python agent/evals/run.py --list
python agent/evals/run.py --prompt-stdin --cards screen-dc --agent-cmd "claude -p --output-format stream-json \
  --verbose --model sonnet --max-budget-usd 1.50 --no-session-persistence --setting-sources project,local \
  --strict-mcp-config --allowedTools Bash(emir:*),Bash(bin/emir:*),Bash(ls:*),Bash(cat:*),Bash(head:*),\
Bash(tail:*),Bash(grep:*),Bash(mkdir:*),Bash(find:*),Bash(wc:*),Read,Write,Edit,Glob,Grep"
python agent/evals/run.py --agent-cmd 'codex exec --full-auto'
python agent/evals/run.py --agent-cmd 'python agent/evals/fake_agent.py --variant good'
# over MCP (no shell): {mcp_config} becomes a client file that starts `emir mcp`
python agent/evals/run.py --mcp --prompt-stdin --cards screen-dc --agent-cmd "claude -p --output-format \
  stream-json --verbose --model sonnet --max-budget-usd 1.50 --setting-sources project,local --strict-mcp-config \
  --mcp-config {mcp_config} --tools Read,Write,Edit,Glob,Grep,ToolSearch \
  --allowedTools mcp__emir,Read,Write,Edit,Glob,Grep,ToolSearch"
```

Run it with the Python that has emir installed (the repository's `.venv/bin/python`). The prompt is appended to
the agent command as its last argument; `--prompt-stdin` sends it on stdin instead. **Use `--prompt-stdin` with
Claude Code:** its `--allowedTools`, `--tools` and `--mcp-config` take several values, so a prompt appended after
them is read as one more value and `claude -p` exits with "Input must be provided either through stdin or as a
prompt argument". `--setting-sources project,local --strict-mcp-config` keeps your own settings, skills and MCP
servers out of the run. Results go to
`emir_out/evals/<time>/` (`--out`): per card the workspace, `result.json`, the agent's stdout and stderr, and a
`results.json` with the table. Exit status 0 when every graded card passed. Cards that need a tool this machine
lacks (Xyce, klayout ...) are skipped and say so. `--jobs` runs cards in parallel: keep it at the person's
simulation-concurrency limit (default 1).

The agent runs with your permissions in the scratch copy; give it the permissions you would give it on a real
block (the runner adds no permission flag of its own), and a budget (`claude -p --max-budget-usd 1`).

## What happens per card

1. **Workspace**: a copy of this repository's tracked files, without `agent/evals/` (the cards hold the answers),
   with the card's fixtures built into `inputs/` (`fixtures.py`).
2. **Shim**: `.eval/bin/emir`, first on PATH (and the copy's `bin/emir`), logs every emir call (`argv`, exit
   status, time) to `.eval/calls.jsonl` and runs the real emir.
3. **Agent**: the card's prompt plus a fixed suffix (the files are in `./inputs/`; use `emir` -- with `--mcp`, the
   emir MCP tools; write the answer to the person to `./answer.md`). With `claude -p --output-format json` or
   `stream-json`, the runner also records the cost, the time, the turns and (stream-json) the shell commands and
   the `mcp__<server>__emir_*` tool calls of the transcript, leaving out the calls Claude Code refused
   (`permission_denials`). Over MCP the calls run inside `emir mcp`, where the shim sees nothing, so each tool call
   is turned back into its command line (`emir.mcp_tools.to_argv`) and graded like a shim call. Only the scripted
   agent gets the card's path (`$EMIR_EVAL_CARD`). A real agent is not given it, but nothing walls it in: the
   shim and `emir info` name the source clone, which holds the cards, so grade a run whose transcript reads
   outside its workspace (or deny such reads in the agent's command) before trusting its score.
4. **Grade** (`grade.py`): no model judges; every check is a parse, a comparison or a regular expression.

## The checks

| check | passes when |
|---|---|
| `answer` | the agent wrote an answer (`answer.md`, else its final text) |
| `real_options` | every emir command it ran parses with emir's own argparse parser: no invented option, no misspelt command. What ran: the shim's log and the MCP calls; the transcript's shell commands only when neither saw a call, without here-document bodies (template commands the agent wrote into its answer) and without refused calls |
| `commands` | the card's command patterns appear in this order among its emir calls (`commands_all`: each, in any order; `commands_any`: one of them) |
| `forbid:<cmd>` | no call matches a forbidden pattern (`extract --run` without asking, `run --force`, a simulation it was told not to run) |
| `preflight` | `emir selftest` ran before the first command on the person's files |
| `document:<schema>` | an emir document it produced holds the expected fields (the verdict, the worst ratio within tolerance ...) |
| `file:<path>` | a file it was asked to write exists and matches (and matches none of the forbidden patterns; a fill-in process file left for the person, `values: none`, holds no value and no source typed in) |
| `verdict`, `number:<what>`, `metric` | the answer gives the verdict, the numbers within tolerance, the governing metric |
| `says:`, `says-one-of:`, `never-says:` | the answer's required and forbidden content |
| `disclaimer` | the answer says "Screening, not sign-off." (or the same words) |
| `no_signoff_claim` | the answer never calls the result sign-off |
| `no_invented_limits` | every limit it quotes in mA/um or mA/cut (a value with "limit" within 40 characters) is in the process file (read before the agent ran); a density it works out is not a limit |
| `unchanged` | the input files (and any repository files the card names) are byte for byte unchanged |

A card passes when every check passes; `score` is the fraction that passed.

## The cards

A card is named by its `id` (the table's first column); the numbers of the files in `cards/` are kept from
development, so some are missing and two share `04`.

| card | playbook | the request | needs |
|---|---|---|---|
| `preflight-install` | 00 | is emir installed, which simulators did it find | |
| `screen-dc` | 01 | screen a rail with given DC currents | |
| `onboard-gf180` | 02 | set up gf180mcu from its LEF and prove it on a strap | |
| `ict-encrypted` | 02 | the only EM data is an encrypted ICT: never guess a limit; write the fill-in file (`emir process new`) and ask the person for the values | |
| `opensource-xyce` | 03 | screen with Xyce and the person's testbench | Xyce |
| `cadence-extract-plan` | 04 | prepare PVS + Quantus, write the Spectre save lines | |
| `first-spectre-run` | 04 | the first Spectre run on a new site's machine: plan `emir verify-sim`, ask before running Spectre | |
| `report-for-manager` | 01 | turn a summary into a status for a manager | |
| `period-refused` | 01 | a simulation result and no period: ask for it, never guess it (EMIR-E428) | |
| `gds-magic-plan` | 03 | plan EM/IR of a sky130 layout that has no DSPF: the magic route, then rcc | |
| `mcp-connect` | 00 | connect Claude Desktop and Codex over MCP | |

Every fixture is synthetic or from the examples; every process file is an example's invented one or an open
PDK's file from `pdks/`.

## A card

```yaml
id: screen-dc
playbook: 01-screen-a-block
title: Screen a block with DC currents the person gives
requires: []                 # xyce, ngspice, magic (on PATH); klayout, matplotlib, mcp (Python)
fixtures: [sample]           # fixtures.py builders, into the workspace's inputs/
prompt: |
  Please screen the VDD rail ...
expect:
  commands:                  # in this order; cmd, has, lacks, value {option: value}, text, rc
    - {cmd: selftest}
    - {cmd: run, has: [--process, --currents]}
  forbid:
    - {cmd: run, has: [--force]}
  documents:                 # dotted paths into a document the agent produced
    - schema: emir.run/1
      fields: {verdict: FAIL, worst_net.ratio: {approx: 1.6667, rel: 0.001}}
  answer:
    verdict: FAIL
    numbers: [{value: 1.667, rel: 0.01, what: worst ratio}]
    metric: avg
    all: ['regex']           # each must match
    any: [['regex', 'regex']]  # one of each group must match
    none: ['regex']
  limits_from: [inputs/process.yaml]
  unchanged: [pdks]          # besides inputs/, which is always checked
  disclaimer: true           # default; false for a card that presents no result
  preflight: true            # default
reference:                   # what a correct agent does: the scripted agent replays it
  commands: [emir selftest --format json, ...]
  answer: |
    ...
```

`reference` makes every card self-testing: `tests/test_agent_evals.py` replays it with `fake_agent.py` and
requires every check to pass (so the expected numbers are the real ones), and replays known mistakes
(`--variant no-disclaimer`, `invented-flag`, `wrong-number`, `invented-limit`, `signoff`, `edit-input`,
`no-commands`, `wrong-verdict`) and requires the grader to catch each. A new card needs a reference that passes.

## Recorded runs

Real agents, run by hand (they cost money; the tests never run them). One run per row: the variance between runs of
the same agent is **not measured**. Every card that remains is listed (`first-spectre-run`, `ict-encrypted` and
`period-refused`, the 9th to 11th of the cards, have not run with a real agent); none has been run again since the docs last changed.

2026-10-02, Claude Code 2.1.287 with `--model sonnet` (it reported claude-sonnet-5-5), command (A) below, one card
at a time, from a fresh clone. "Raw" is the grade of `run.py` on that date; "now" re-grades the same workspaces with
the current grader and cards (the fixes: commands that ran, not template commands in an answer or refused calls;
`info` and `selftest` in either order; a worked-out density is not a limit):

| card | raw | now | time | cost USD | turns |
|---|---|---|---|---|---|
| `preflight-install` | FAIL 92 % (card asked selftest before info) | PASS | 22.3 s | 0.107 | 7 |
| `screen-dc` | FAIL 94 % (`real_options`: it gave `emir run` an `--out` option, which run does not take; it recovered) | FAIL 94 % | 51.2 s | 0.195 | 12 |
| `onboard-gf180` | FAIL 93 % (a density it worked out, read as a limit) | PASS | 30.1 s | 0.162 | 8 |
| `opensource-xyce` | PASS | PASS | 34.5 s | 0.189 | 9 |
| `cadence-extract-plan` | PASS | PASS | 32.7 s | 0.164 | 5 |
| `report-for-manager` | PASS | PASS | 28.1 s | 0.086 | 4 |
| `mcp-connect` | PASS | PASS | 42.2 s | 0.180 | 10 |

The miss was the agent's; since then an `--out` given to `emir run` names the options run takes (`EMIR-E507`'s
`did_you_mean`).

2026-10-04, Claude Code 2.1.289 with `--model claude-sonnet-5`, command (A):

| card | result | time | cost USD | turns |
|---|---|---|---|---|
| `gds-magic-plan` | FAIL 92 % (no `emir next --gds`: the prompt said not to run anything yet; it also said, wrongly, that PDK_ROOT was unset and the patched magic missing) | 41.6 s | 0.255 | 10 |

Since then AGENTS.md says to run `emir next` even when told not to run anything yet, and `emir info` and
`emir next --gds` name the magic the route runs and PDK_ROOT.

Over MCP, 2026-10-02, `--model sonnet`, the agent with no shell (command (B)): `screen-dc` PASS (26.7 s, 0.181 USD,
9 turns). An earlier run of `screen-dc` with (A) the same day: PASS, 100 % (23.6 s, 6 turns, 0.194 USD).

(A) `run.py --prompt-stdin --agent-cmd "claude -p --output-format stream-json --verbose --model sonnet
--max-budget-usd 1.50 --no-session-persistence --setting-sources project,local --strict-mcp-config --allowedTools
Bash(emir:*),Bash(bin/emir:*),Bash(ls:*),Bash(cat:*),Bash(head:*),Bash(tail:*),Bash(grep:*),Bash(mkdir:*),Bash(find:*),Bash(wc:*),Read,Write,Edit,Glob,Grep"`
(the earlier `screen-dc` run: `--max-budget-usd 1.00`, without `--setting-sources`, `--strict-mcp-config`,
`bin/emir`, `find` and `wc`).

(B) the same with `--mcp-config <a file starting emir mcp> --tools Read,Write,Edit,Glob,Grep,ToolSearch
--allowedTools mcp__emir,Read,Write,Edit,Glob,Grep,ToolSearch` (no Bash).
