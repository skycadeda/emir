# Agent evals: does an agent drive emir correctly?

Task cards that a person might give an agent ("screen this block with these currents", "my PLL top's RCC
simulation does not converge - what now", "why did check fail with EMIR-E417"), each with fixture inputs built
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
   agent gets the card's path (`$EMIR_EVAL_CARD`): a real agent never can read the answers.
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

| card | playbook | the request | needs |
|---|---|---|---|
| `preflight-install` | 00 | is emir installed, which simulators did it find | |
| `screen-dc` | 01 | screen a rail with given DC currents | |
| `bigblock-cc-grid` | 02 | screen an ADC top from its CC result and R-only DSPF | |
| `pll-rcc-no-converge` | 02 | the PLL top's RCC simulation does not converge: what now | |
| `ask-the-mode` | 02 | plan a simulation: the agent must ask for the mode (rcc, cc-grid, self-consistent), never pick it | |
| `chip-level-abstracts` | 03 | chip-level EM/IR from block abstracts, budgets, back-annotation | |
| `onboard-gf180` | 04 | set up gf180mcu from its LEF and prove it on a strap | |
| `ict-encrypted` | 04 | the only EM data is an encrypted ICT: never guess a limit; write the fill-in file (`emir process new`) and ask the person for the values | |
| `opensource-xyce` | 05 | screen with Xyce and the person's testbench | Xyce |
| `cadence-extract-plan` | 06 | prepare PVS + Quantus, write the Spectre save lines | |
| `first-spectre-run` | 06 | the first Spectre run on a new site's machine: plan `emir verify-sim`, ask before running Spectre | |
| `report-for-manager` | 07 | turn a summary into a status for a manager | |
| `troubleshoot-e417` | 08 | why did check fail with EMIR-E417 | |
| `gds-magic-plan` | 09 | plan EM/IR of a sky130 layout that has no DSPF: the magic route | |
| `mcp-connect` | 10 | connect Claude Desktop and Codex over MCP | |
| `decap-make-it-pass` | 02 | "make EM pass with the decaps": run `--dynamic`, relay every EM basis, leave the basis to the person (a run with `--em-basis decap-aware` or `decap-free` fails the card) | |

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
the same agent is **not measured**.

All 13 cards of that date (two of them went with removed commands: gds-mesh-spine with the raster-mesh command,
gds-layout-taps with the removed layout command; `first-spectre-run`, `ict-encrypted`, `ask-the-mode`, `decap-make-it-pass` and
`gds-magic-plan`, the 12th to 16th of today's cards, came later and have not run with a real agent), 2026-10-02, Claude Code 2.1.287 with `--model sonnet` (it reported
claude-sonnet-5-5), command (A) below, one card at a time, from a fresh clone made before the grader fixes listed below: 7
min 58 s and 2.235 USD in all, 32 permission refusals (mostly `cat > answer.md` heredocs and compound commands),
every one recovered. "Raw" is the grade of `run.py` on that date; "now" re-grades the same workspaces with the
current grader and cards (the fixes: commands that ran, not template commands in an answer or refused calls; `info`
and `selftest` in either order; a worked-out density is not a limit):

| card | raw | now | time | cost USD | turns |
|---|---|---|---|---|---|
| `preflight-install` | FAIL 92 % (card asked selftest before info) | PASS | 22.3 s | 0.107 | 7 |
| `screen-dc` | FAIL 94 % (`real_options`: it gave `emir run` an `--out` option, which run does not take; it recovered) | FAIL 94 % | 51.2 s | 0.195 | 12 |
| `bigblock-cc-grid` | PASS | PASS | 35.7 s | 0.163 | 6 |
| `pll-rcc-no-converge` | FAIL 93 % (a `--period <T>` template in its answer's heredoc) | PASS | 43.1 s | 0.198 | 8 |
| `chip-level-abstracts` | PASS | PASS | 44.5 s | 0.247 | 12 |
| `onboard-gf180` | FAIL 93 % (a density it worked out, read as a limit) | PASS | 30.1 s | 0.162 | 8 |
| `opensource-xyce` | PASS | PASS | 34.5 s | 0.189 | 9 |
| `cadence-extract-plan` | PASS | PASS | 32.7 s | 0.164 | 5 |
| `report-for-manager` | PASS | PASS | 28.1 s | 0.086 | 4 |
| `troubleshoot-e417` | PASS | PASS | 31.3 s | 0.173 | 6 |
| `gds-layout-taps` (removed with the layout command) | PASS | not re-graded: the card is gone | 41.1 s | 0.173 | 8 |
| `gds-mesh-spine` (removed with the raster-mesh command) | FAIL 93 % (no `emir report` after the GDS run) | not re-graded: the card is gone | 38.3 s | 0.200 | 10 |
| `mcp-connect` | PASS | PASS | 42.2 s | 0.180 | 10 |

8 of 13 raw; now 10 of the 11 cards that remain. Both misses (the one that still fails, and gds-mesh-spine's) were
the agent's; since then an `--out` given to `emir run` names the
options run takes (`EMIR-E507`'s `did_you_mean`), and AGENTS.md section 5 makes `emir report` the step before every
answer. Not re-run with a real agent after those changes.

Over MCP, the same day and model, the agent with no shell (command (B)): `screen-dc` PASS (26.7 s, 0.181 USD, 9
turns); `chip-level-abstracts` FAIL 95 % now (31.9 s, 0.228 USD, 10 turns): it added the top-level drop to the
back-annotated IR, which is already measured from the pad, and called the budget "probably exceeded". Since then
the back-annotation document says `ir_reference: pad`, playbook 03 says not to add the top drop again, and the card
forbids the claim. (Graded at the time by a wrapper; `run.py --mcp` does the same now.)

Earlier runs of two cards, the same agent (A):

| date | agent | card | result | time | cost |
|---|---|---|---|---|---|
| 2026-10-02 | Claude Code 2.1.287, `claude -p --model sonnet` (A) | `screen-dc` | PASS, 100 % | 23.6 s, 6 turns | 0.194 USD |
| 2026-10-02 | the same (A) | `troubleshoot-e417` | FAIL, 92 %: `preflight` (it ran `emir explain` and `emir check` but no `emir selftest` first); the answer itself was right | 17.2 s, 3 turns | 0.106 USD |
| 2026-10-02 | the same (A), after AGENTS.md said the preflight comes first even for a question about an error code | `troubleshoot-e417` | PASS, 100 % | 26.6 s, 5 turns | 0.156 USD |

(A) `run.py --prompt-stdin --agent-cmd "claude -p --output-format stream-json --verbose --model sonnet
--max-budget-usd 1.50 --no-session-persistence --setting-sources project,local --strict-mcp-config --allowedTools
Bash(emir:*),Bash(bin/emir:*),Bash(ls:*),Bash(cat:*),Bash(head:*),Bash(tail:*),Bash(grep:*),Bash(mkdir:*),Bash(find:*),Bash(wc:*),Read,Write,Edit,Glob,Grep"`
(the two earlier rows: `--max-budget-usd 1.00`, without `--setting-sources`, `--strict-mcp-config`, `bin/emir`,
`find` and `wc`).

(B) the same with `--mcp-config <a file starting emir mcp> --tools Read,Write,Edit,Glob,Grep,ToolSearch
--allowedTools mcp__emir,Read,Write,Edit,Glob,Grep,ToolSearch` (no Bash).
