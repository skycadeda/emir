# EMIR for agents

EMIR screens extracted analog layout for electromigration (EM) and IR drop: a DSPF (on SKY130 `emir extract --tool
magic` makes one), the PDK's EM limits and the currents (a simulation with every branch current, or DC values) give
per-segment EM ratios, IR drop per supply and each failure's fix. **Screening, not sign-off.** You, the agent, are
its primary user: every command answers in JSON, every failure carries a code and a next step.

**EMIR is a research project, provided as is, with no warranty or guarantee of accuracy. It is the person's
responsibility to test and verify its results for their process, tools and design before relying on them.** Relay
that with every result you report (`emir report` prints it).

## 1. Preflight, every session

```sh
emir --version
emir info --format json        # the tools; tools[].verified; magic_route: the magic SKY130 runs
emir selftest --format json    # offline, seconds: exit 0 and "passed": true
```

No `emir` on PATH: `bin/emir` from a clone. A simulator with `verified` null: `emir verify-sim --sim xyce` first.

## 2. Plan with `emir next`, then follow the plan

```sh
emir next --dspf <block.dspf> --process <pdk.yaml> --cc-sim <result.raw> --format json
```

`emir next` only reads: run it first, even when the person says not to run anything yet. Pass **every** input the
person gave (`--dspf`, `--process`, `--cc-sim`, `--currents`, `--netlist`, `--gds`, `--cell`, `--net`, `--period`,
`--html`, `--viewer`; after a run, `--run-summary`); a testbench instance `XI0` is `--prefix XI0.` of save-lines and
simulate, never `--cell`. Ask for each `missing[]` item; run `steps[]` in order, stop at the first non-zero exit, ask
before each `needs_human: true` step, fill a `waiting` step's placeholders from the steps before it.

**A simulated run is rcc**: every branch current, every metric, over the record's last whole periods of `--period`
(up to three). rcc is exact for the simulated record over the whole periods you give (--period); whether the testbench
has settled is yours to establish, as for any .meas. Refused, no number: EMIR-E428 (a transient, no period), E431,
E429 (`emir next` plans the fix). DC: avg = rms = peak.

| the person has | playbook |
|---|---|
| a DSPF, a process file and currents; a result to explain | [01-screen-a-block](agent/playbooks/01-screen-a-block.md) |
| no process file, or no readable EM data (the person types it) | [02-process-file](agent/playbooks/02-process-file.md) |
| Xyce or ngspice; a GDS on SKY130 and no DSPF | [03-open-source-flow](agent/playbooks/03-open-source-flow.md) |
| Virtuoso, PVS, Quantus, Spectre, HSPICE | [04-cadence-flow](agent/playbooks/04-cadence-flow.md) |
| a new machine, an error code, an MCP client | [00-preflight](agent/playbooks/00-preflight.md) |

## 3. The contract

- **`--format json` on every command**: stdout holds exactly one JSON document, its `schema` names it; logs and
  the text for a person go to stderr, so parse stdout alone (`2>/dev/null`, never `2>&1`).
- **The DSPF is the first argument** of `run`, `check`, `nets`, `pins` and `save-lines`: `emir run <dspf> --process P`.
- **Exit status.** 0 done; 1 a FAIL verdict, with `--fail-on-violation`; 2 bad input or refused;
  3 a required tool or package is missing; 70 internal error, failed self-test or validation; 130 interrupted;
  143 terminated. A FAIL verdict without `--fail-on-violation` exits 0: it is a result.
- **Codes**: a failure prints `emir.error/1` (`code`, `hint`, `next_steps`). Branch on the code, never the text.
- **`next_steps`**: run them as printed (`"shell": true`: not an `emir` command; `"needs_human": true`: ask first).
  A null number is "not a number", never 0. EMIR never prompts, and writes only where its options say.
- Every command and document: [agent/CONTRACT.md](agent/CONTRACT.md) (generated); every code:
  [agent/errors.md](agent/errors.md); examples: [examples/](examples/README.md).

## 4. Guardrails

1. **Never invent a limit, a current, a temperature, a corner, a period or a measured value**, nor a derived
   number: quote EMIR's. When something is missing, ask.
2. **Never call a result sign-off.** Say "Screening, not sign-off." wherever you present results.
3. **Never start, kill or restart the person's Virtuoso or simulator; never edit the layout, DSPF, testbench or
   process file.**
4. **Ask before licensed tools** (Spectre, HSPICE, PVS, Quantus: `--run`) **and before long runs**; over MCP,
   EMIR-E590 until the person agrees.
5. **Respect the simulation-concurrency limit**: one simulation at a time unless the person sets another.
6. **Never send design data anywhere**: it stays on this machine. Never call a simulator verified unless this
   machine holds a passing `emir verify-sim` record.

## 5. Reporting

Run `emir report --from emir_out/summary.json` and relay it: the verdict, the worst items with their place and
fix, IR per power net, the current source, whether the simulator is verified here, where the limits came from,
what was **not checked**, "Screening, not sign-off.", the research-project disclaimer.

Changing EMIR itself: the checks in CONTRIBUTING.md pass before each commit; never commit foundry data.
