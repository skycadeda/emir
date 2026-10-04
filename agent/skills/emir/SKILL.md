---
name: emir
description: EM (electromigration) and IR-drop screening of extracted analog layout with the `emir` command line. A DSPF or a GDS layout, the PDK's process file and currents (a Spectre, HSPICE, Xyce or ngspice simulation, DC values, or block abstracts) give per-segment avg/rms/peak EM ratios, IR drop and the fix for each violation. Use it when the person asks for EM, EMIR, IR drop, current density, electromigration or a power-grid check of a block or a chip, or brings a DSPF, an EMIR error code or an EMIR result to explain. Screening, not sign-off.
---

# EMIR

`emir` is a command-line tool built for agents: every command takes `--format json` and then prints exactly one
JSON document on stdout, every failure carries a code (`EMIR-E417`), and every result lists `next_steps`.

EMIR is a research project, provided as is, with no warranty or guarantee of accuracy. It is the person's
responsibility to test and verify its results for their process, tools and design before relying on them: say so
with every result you report.

## 1. Find the instructions

The full instructions are `AGENTS.md` at the root of the EMIR repository, and the task playbooks are in
`agent/playbooks/` beside it. Read `AGENTS.md` first. When the working directory is not the clone, `emir info
--format json` gives `package_dir` (the clone's `emir/` when EMIR was installed from a clone): the clone is its
parent directory. Without a clone, `emir next` alone carries the same decision tree, and `emir explain <code>`
every code's meaning.

## 2. Preflight

```sh
emir info --format json
emir selftest --format json
```

Exit 0 with `"passed": true`, or stop and tell the person (playbook 00). Before the first run on this machine that
uses a simulator's result, verify that simulator's route here: `emir verify-sim --sim xyce` (or ngspice); for
Spectre or HSPICE, which are licensed, `emir verify-sim --sim spectre` shows the plan and `--run` runs it after the
person agrees. `emir next` plans it.

## 3. Plan with `emir next`, then follow the plan

```sh
emir next --dspf <block.dspf> --process <pdk.yaml> --cc-sim <result.raw> --format json
```

Pass **every** input the person gave (`--dspf`, `--process`, `--cc-sim`, `--currents`, `--netlist`, `--gds`,
`--cell`, `--abstract`, `--net`, `--period`, `--html`, `--viewer`, `--dynamic`, `--em-basis`; after a run,
`--run-summary`): without `--dynamic` the plan has no decap-aware EM. Ask for every `missing[]` item. Run
`steps[]` in order and stop at the first non-zero exit; ask before every step with `needs_human: true`; fill a
`waiting` step's placeholders from the steps before it.

When a simulation drives the run and no `--mode` was given, the plan has a `choose-mode` step (the first, or after
`process-check` when the limits are typed by hand): the mode is the person's choice, never yours. Show them its
`choices` (rcc, cc-grid, cc-grid self-consistent: what each costs and how accurate it is, measured; `possible`; the
`recommended` one from the block's size), ask, then run the chosen route's `command`. A request that already names
the route ("the CC simulation", "self-consistently") is their choice: say so and pass it. DC `--currents` need no
mode.

| task | playbook in `agent/playbooks/` |
|---|---|
| install check | `00-preflight.md` |
| a block: DSPF, process file, currents | `01-screen-a-block.md` |
| an ADC or PLL top; RCC that does not converge | `02-big-block-cc.md` |
| a chip from block abstracts; EMIR-E417 | `03-chip-level-hierarchy.md` |
| no process file for the PDK | `04-onboard-a-pdk.md` |
| no readable process data (no ICT, or an encrypted one: EMIR-E325) | `04-onboard-a-pdk.md`, "No readable file" |
| Xyce or ngspice | `05-open-source-flow.md` |
| Virtuoso, Quantus, Spectre; HSPICE | `06-cadence-flow.md` |
| telling a person the result | `07-report-to-human.md` |
| an error code | `08-troubleshooting.md` |
| a GDS and no DSPF (an open PDK: magic extracts it) | `09-gds-only.md` |
| MCP tools | `10-mcp.md` |

## 4. Rules

- Never invent a limit, a current, a temperature, a corner, a placement or a measured value: ask.
- With no readable process data (no ICT, or one `emir import-ict` cannot read), never decrypt or guess: `emir
  process new --from-dspf <block.dspf> -o <pdk>.yaml` writes the fill-in file, the person types each limit with its
  source (or confirms each one you transcribe from a manual they give), and `emir process check` must pass before
  any run. Never run `--interactive` yourself.
- Never choose rcc or cc-grid yourself: ask the person, showing `emir next`'s trade-offs. The self-consistent loop
  re-simulates: their go-ahead too.
- Never call a result sign-off; say "Screening, not sign-off."
- Never start, kill or restart the person's Virtuoso or simulator; never edit their layout, DSPF, testbench or
  process file.
- Ask before licensed tools (Spectre, HSPICE, PVS, Quantus) and long runs; one simulation at a time unless told
  otherwise. Without `--run`, `emir simulate` and `emir verify-sim` only plan a licensed simulator's run.
- Never say a simulator or a licensed tool is verified unless this machine holds a passing `emir verify-sim` record
  (`simulator_verification` in the run summary); report EMIR-W464 as "not verified on this machine" (`partly
  verified`: name the kinds of current, `pin_kinds`, that stay unverified), EMIR-W414 as "FAILED its verification
  here: do not rely on these numbers", EMIR-W416 as "not verified: version unknown".
- Never send design data off the machine.
- Branch on codes, never on message text; a null number is "not a number", never 0.
- The EM verdict basis (`--em-basis`: worst, the default with `--dynamic`, without which the verdict is
  decap-free; decap-free; decap-aware) is the person's choice: never choose or change it yourself, never to make
  a FAIL pass; `emir next --run-summary` puts it to them.

## 5. Report

```sh
emir report --from emir_out/summary.json
```

Relay it: verdict, governing metric and worst ratio with the fix, IR per power net, where the currents came from
and whether that simulator is verified on this machine, the temperature, what was not checked, "Screening, not
sign-off.", and that EMIR is a research project with no guarantee of accuracy whose results the person tests and
verifies before relying on them. When the person can see images, attach the chart beside the text: the summary's
`charts` (`--html`: `emir_chart.png`); playbook 07 says how to read it.
