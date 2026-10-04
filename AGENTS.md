# EMIR for agents

EMIR screens extracted analog layout for electromigration (EM) and IR drop. It reads a DSPF (on an open PDK, magic
extracts one from the GDS: `emir extract --tool magic`), a
process file holding the PDK's EM limits, and the currents (a simulation result, DC currents, or block abstracts),
and writes per-segment EM ratios (avg / rms / peak against the limit), IR drop per power net, the worst items with
their fix in engineer's units, HTML reports and layout viewers. **Screening, not sign-off.** You, the agent, are the
primary user: every command answers in JSON, every failure carries a code, every result says what to run next.

**EMIR is a research project, provided as is, with no warranty or guarantee of accuracy. It is the person's
responsibility to test and verify its results for their process, tools and design before relying on them.** Relay
that with every result you report (`research_disclaimer`; `emir report` prints it).

## 1. Preflight: every session, before touching the person's data

Always first, even when the person only asks about an error code or a result file:

```sh
emir --version
emir info --format json        # which tools (spectre, hspice, xyce, ngspice, magic, virtuoso, pvs, quantus) it found
emir selftest --format json    # offline, seconds: exit 0 and "passed": true
```

No `emir` on PATH: from a clone, `bin/emir`, or `python3 -m venv .venv && .venv/bin/pip install -e '.[all]'`
(ask before installing). Self-test exit 70 (EMIR-E902): stop and tell the person. **Verify a simulator on first
use** (`tools[].verified` null): `emir verify-sim --sim xyce` before the first run here that uses its results
(Spectre, HSPICE: `--run` after the person agrees); `emir next` plans it. Details: playbook 00.

## 2. Decide: run `emir next` with what you have, and follow its plan

```sh
emir next --dspf <block.dspf> --process <pdk.yaml> --cc-sim <result.raw> --format json
```

`emir next` is the decision tree of the playbooks, as code. Pass **every** input the person gave (`--dspf`, `--process`,
`--cc-sim`, `--currents`, `--netlist`, `--gds`, `--cell`, `--abstract`, `--net`, `--period`, `--html`, `--viewer`, `--dynamic`,
`--em-basis`; after a run, `--run-summary`), DC currents included: without `--currents` the plan is a simulation. Then:

- `missing[]`: what to ask the person for (`what`, `why`, `how`). Never fill a gap with a guess.
- `steps[]`: run them in order; stop at the first that exits non-zero and read its JSON (`code`, `next_steps`).
- a step with `needs_human: true` (`licensed_tool`, `long_run`, `ask`, or a choice): ask the person before running it.
- a step with `status: "waiting"`: replace its `placeholders` (`<result.raw>`) with what the steps in `after` produced.
- **`choose-mode`** (a simulation drives the run, no `--mode`; first, or after `process-check` when the limits are
  typed by hand): show the person its `choices` (rcc, cc-grid, cc-grid self-consistent: cost, measured accuracy,
  `possible`, `recommended`), ask, run the chosen route's `command`. Nothing past it is planned. DC needs no mode.
- `mode.chosen` and `mode.why` say how the currents will be used; `advice[]` holds the rest.

| the person has, or asks for | playbook | the first command |
|---|---|---|
| a new machine, a new install, anything odd | [00-preflight](agent/playbooks/00-preflight.md) | `emir selftest --format json` |
| a DSPF, a process file, and currents (DC or a simulation result) | [01-screen-a-block](agent/playbooks/01-screen-a-block.md) | `emir next --dspf D --process P --cc-sim R` |
| a big block (ADC top, PLL top), or an RCC simulation that is too slow or does not converge | [02-big-block-cc](agent/playbooks/02-big-block-cc.md) | `emir next --dspf D --process P` |
| no `--mode` yet for a simulation (the `choose-mode` step); the droop of a cc-grid run is large (EMIR-W520) | [02-big-block-cc](agent/playbooks/02-big-block-cc.md), its "Choosing the mode" | `emir next --dspf D --process P` |
| a chip or a top made of blocks; block abstracts; EMIR-E417 on a black-boxed block | [03-chip-level-hierarchy](agent/playbooks/03-chip-level-hierarchy.md) | `emir next --dspf TOP --process P --abstract A@INST` |
| no process file for the PDK yet | [04-onboard-a-pdk](agent/playbooks/04-onboard-a-pdk.md) | `ls pdks/` |
| no readable process data (no ICT, or EMIR-E325: encrypted): the person types the limits | [04-onboard-a-pdk](agent/playbooks/04-onboard-a-pdk.md), "No readable file" | `emir process new --from-dspf D -o P` |
| Xyce or ngspice, no Spectre; an open PDK | [05-open-source-flow](agent/playbooks/05-open-source-flow.md) | `emir next --dspf D --process P --netlist TB` |
| a Cadence site: Virtuoso, Quantus, Spectre | [06-cadence-flow](agent/playbooks/06-cadence-flow.md) | `emir next --dspf D --process P --have-cadence` |
| HSPICE (licensed), or a `.tr0` result | [06-cadence-flow](agent/playbooks/06-cadence-flow.md), its HSPICE section | `emir next --dspf D --process P --sim hspice` |
| a simulator not verified on this machine (`tools[].verified` null; EMIR-W464, EMIR-W414, EMIR-W416) | [00-preflight](agent/playbooks/00-preflight.md) | `emir verify-sim --sim xyce` |
| a finished run to explain to a person | [07-report-to-human](agent/playbooks/07-report-to-human.md) | `emir report --from emir_out/summary.json` |
| an error code, or a non-zero exit status | [08-troubleshooting](agent/playbooks/08-troubleshooting.md) | `emir explain <code>` |
| a GDS layout and no DSPF (an open PDK: magic extracts it) | [09-gds-only](agent/playbooks/09-gds-only.md) | `emir next --gds G --process P --cell C` |
| an MCP client (no shell, or typed tools wanted) | [10-mcp](agent/playbooks/10-mcp.md) | `emir mcp --list --format json` |

## 3. The contract

- **`--format json` on every command.** stdout then holds exactly one JSON document; its `schema` field names it
  (`emir.run/1`, `emir.check/1`, ...). Logs go to stderr. `emir schema <name>` prints its JSON Schema;
  `emir schema --list` lists them. (`--json` is an old alias of `--summary-out` on `run`: do not use it.)
- **The DSPF is the first argument** of `run`, `check`, `nets`, `pins`, `save-lines` and `abstract`:
  `emir run <dspf> --process P ...`, as `emir next` prints it. `--dspf` is `emir next`'s option (and
  `probe`'s and `simulate`'s); the others accept it as the same, but write the commands as `emir next` prints them.
- **A reader that leaves early costs only the lines it would have read.** `emir run ... 2>&1 | head` still
  finishes, writes its files (`--summary-out`, `--out`) and exits with its own status.
- **Exit status.** 0 done; 1 a FAIL verdict, an IR budget FAIL, or an EM basis asked for and not computed
  (`em_basis.honoured` false), with `--fail-on-violation`; 2 bad input or refused (`check`: inputs not OK);
  3 a required tool or package is missing; 70 internal error, failed self-test or validation; 130 interrupted;
  143 terminated. Without `--fail-on-violation` a FAIL verdict exits 0: it is a result, not an error.
- **Codes.** A failure prints `emir.error/1`: `code` (`EMIR-E417`), `message`, `hint`, `next_steps`, `details`.
  Warnings (`EMIR-Wxxx`) and notes (`EMIR-Ixxx`) arrive in `notices[]` and `findings[]`. Branch on the code, never
  on the message text. `emir explain <code>` prints one entry; [agent/errors.md](agent/errors.md) has them all.
- **`next_steps`**: `[{why, command}]` in every result, run as printed (your `--format` kept); `"shell": true`: not
  an `emir` command (`ls`, `klayout`); `"needs_human": true`: it re-simulates or runs a licensed tool, ask first.
- **Numbers can be null** (not a number). Report them as "not a number", never as 0.
- EMIR never prompts you (only a person at a terminal runs `emir process new --interactive`). It writes only
  where its options say (`emir_out/` by default): `run` `--summary-out FILE` (`--html DIR`, `--viewer DIR`);
  `check` `--json-out FILE`; `extract`, `simulate`, `save-lines`, `probe`, `abstract`, `validate`, `process new`
  `--out` (`-o`); `next`, `report`, `explain` only print.

## 4. Guardrails

1. **Never invent a limit, a current, a temperature, a corner, a period, a placement or a measured value**, nor a
   derived number (a current density, a ratio, a sum of drops): quote EMIR's (`emir report` gives every net's worst
   wire and worst via, passing or not). Limits come from the process file (`pdks/`, or the person's); everything
   else from the person or from EMIR's output. When something is missing, `missing[]` says what to ask for: ask.
2. **Never choose rcc or cc-grid yourself; ask the person, showing `emir next`'s trade-offs** (`choose-mode`). A
   request that names the route ("the CC simulation", "self-consistently") is their choice: pass it, say so. Where
   only one mode can work, `emir next` says why: tell them; it is still theirs to confirm.
3. **Never call a result sign-off.** Say "Screening, not sign-off." wherever you present results.
4. **Never start, kill or restart the person's Virtuoso or simulator**: their session, their running simulation.
5. **Never edit the layout, the DSPF, the testbench or the process file.** EMIR writes copies and highlight files.
6. **Ask before licensed tools** (Spectre, HSPICE, PVS, Quantus, batch Virtuoso: `licensed_tool`; the `--run` of
   `extract`, `simulate`, `verify-sim`, `run --netlist`) **and before long runs** (`long_run`; the self-consistent
   loop, which re-simulates). Without `--run` they only plan; over MCP: EMIR-E590 until the person agrees.
7. **Respect the simulation-concurrency limit**: one simulation at a time unless the person sets a higher limit.
8. **Never send design data anywhere.** DSPF, GDS, netlists, results and process files stay on this machine: no
   uploads, no web services, no pasting them into other tools. EMIR itself works offline.
9. Ask before installing packages or tools, and before overwriting a file you did not create.
10. **Verify a simulator on the machine that uses it** (`emir verify-sim`) before relying on it; never call a
   licensed tool verified because a document says so: only a passing record here does (`simulator_verification`).
11. **Never choose or change the EM verdict basis yourself** (`--em-basis`), never to make a FAIL pass: ask the person.

## 5. Reporting to the person

**Before you answer, always run `emir report`** on what the run wrote, and build the answer from it: it is the
reporting contract, and the evals check that it ran.

```sh
emir report --from emir_out/summary.json      # emir run (--summary-out)
emir report --from emir_out/pll.annotated.json   # a block abstract (emir abstract -o), back-annotated or not
```

Relay its Markdown as it is, or build your message from `emir report --format json`. Every report names: the
verdict; the governing metric and the worst ratio, with the item, **where it is** (the "at (x, y) um" column) and
its fix ("Metal1: widen 0.750 -> 1.255 um"); the worst wire and worst via of each net; the IR drop per power net;
where the currents came from (mode) and whether that simulator is verified here (`simulator_verification`); the
analysis temperature; what was **not checked**; "Screening, not sign-off."; and the research-project disclaimer.
Quote numbers as EMIR printed them; attach the chart (`charts`: `--html`'s `emir_chart.png`) beside them. Playbook 07.

## 6. Where everything is

| path | what |
|---|---|
| [agent/playbooks/](agent/playbooks/) | one playbook per task (above): when, inputs, commands, decisions, success, reporting, failures |
| [agent/README.md](agent/README.md), [agent/mcp/](agent/mcp/README.md) | connecting Claude Code, Codex, Cursor and MCP clients; `emir mcp`: the MCP server and client snippets |
| [agent/skills/emir/](agent/skills/emir/SKILL.md) | the Claude Code skill (`.claude/skills/emir` points at it) |
| [agent/cadence/](agent/cadence/README.md), [agent/opensource/](agent/opensource/README.md) | Virtuoso, PVS/Quantus, Spectre; Xyce, ngspice, KLayout |
| [agent/CONTRACT.md](agent/CONTRACT.md) | the full contract, generated from the code: every command, document, exit status, code area, MCP tool |
| [agent/errors.md](agent/errors.md), [agent/schemas/](agent/schemas/), [agent/evals/](agent/evals/README.md) | every code; every JSON Schema (generated from the code); task cards and graders that score an agent on these playbooks |
| [pdks/](pdks/README.md), `emir/data/sample/` | process files for sky130, gf180mcu, ihp_sg13g2, and `TEMPLATE.yaml`; the bundled sample: `rail.dspf` and an ILLUSTRATIVE `process.yaml` (invented limits) |
| [examples/](examples/README.md) | runnable examples, each with a README and expected results (no `01`: that is the quickstart on the sample) |
| `docs/` | the method, for humans (agents may read it) |

## 7. Changing EMIR itself

Every change passes, before each commit: `ruff check .`, `python scripts/leak_guard.py` (and `--commit-msg`), `pytest
-q`, `emir selftest`, `emir validate` and every `scripts/gen_*.py --check` (CONTRIBUTING.md lists them). A new failure
gets a code in `emir/errors.py`, a new document a schema in `emir/schemas.py`. Never commit foundry data (limits,
layer numbers, decks, ICT files) or site paths: the leak guard refuses them.
