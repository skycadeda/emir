# 08 Troubleshooting: branch on the code

Every refusal, failure and warning carries a stable code (`EMIR-E417`, `EMIR-W403`, `EMIR-I520`) with a message,
a hint and the command that fixes or diagnoses it. Branch on the code; never on the message text, which may
change. **Screening, not sign-off.**

## When to use

- A command exited non-zero, or printed an `emir.error/1` document.
- `emir check` printed `passed: false`, or a result holds `notices[]` you do not understand.
- The person asks "why did this fail" and names a code.

## Inputs

The failing command line, its JSON document (`--format json`) or its stderr (`emir: EMIR-Exxx: message`, then
`  hint:`, then `  next:`), and the files it named.

## Commands

### Worked example (runs as printed from the repository root)

First, as always, the preflight (playbook 00): a failure on a broken install says nothing about the person's
files.

<!-- test: run -->
```sh
emir selftest --format json
```

Three failures and how each one tells you what to do. A net that is not in the DSPF:

<!-- test: run -->
```sh
emir pins emir/data/sample/rail.dspf --net VSS --format json   # exit 2
```

<!-- test: expect -->
```json
{"schema": "emir.error/1", "command": "pins", "code": "EMIR-E204", "exit_code": 2,
 "next_steps": [{"command": "emir nets emir/data/sample/rail.dspf --format json"}]}
```

A misspelt option (options are never abbreviated; nothing is guessed):

<!-- test: run -->
```sh
emir run emir/data/sample/rail.dspf --proces emir/data/sample/process.yaml --format json   # exit 2, a usage error on purpose
```

<!-- test: expect -->
```json
{"schema": "emir.error/1", "code": "EMIR-E507", "next_steps": [{"command": "emir run --help --format json"}]}
```

Currents that do not balance, caught by the check before any run:

<!-- test: run -->
```sh
emir check emir/data/sample/rail.dspf --process emir/data/sample/process.yaml --currents '{"VDD": 0.004, "MP0:s": -0.001}' --format json   # exit 2
```

<!-- test: expect -->
```json
{"schema": "emir.check/1", "passed": false}
```

Its `findings[]` hold the FAIL with `code` EMIR-E406 and a `command` to run. Any code, explained:

<!-- test: run -->
```sh
emir explain E417 --format json
```

<!-- test: expect -->
```json
{"schema": "emir.explain/1", "codes": [{"code": "EMIR-E417", "level": "error", "exit_code": 2, "retired": false}]}
```

EMIR-E417 on a real top: an instance pin that reaches the net at several DSPF connections. `emir pins` shows which
(`split`), and `emir next` names the route:

<!-- test: run -->
```sh
python examples/05_chip_level/make_inputs.py emir_out/chip
emir pins emir_out/chip/top.dspf --net VDD --format json
```

<!-- test: expect -->
```json
{"schema": "emir.pins/1", "net": "VDD", "split": ["XPLL:VDDP"]}
```

<!-- test: run -->
```sh
emir next --dspf emir_out/chip/top.dspf --process emir_out/chip/process.yaml --net VDD --format json
```

XPLL's supply pin reaches VDD at two places (`XPLL:VDDP`, `XPLL:VDDP#2`); one saved current cannot be divided
between them, so cc-grid refuses it. The plan's `advice` names the two routes: the hierarchical one (playbook 03:
abstract XPLL, run the top with `--abstract` and `--place`), or rcc with every branch current saved.

### On the person's files

```sh
emir selftest --format json
emir explain <code> --format json
emir explain --list --format json
```

Then run the failing document's `next_steps[].command` (they run as printed) or the check finding's `command`. A
step with `"shell": true` is not an `emir` command (`ls`, `tail`, `klayout`): it needs a shell, which an MCP client
does not have through emir.

## Decision points

The code's area is its first digit: 1 files, 2 the DSPF or the layout, 3 the process file, 4 currents and
simulation results, 5 options, 6 tools, 7 outputs and viewers, 8 hierarchy, 9 internal; within an area the magic
route has the x40 decade (`emir extract --tool magic`: E243, E640-E645, W640, W643-W645). E is an error (the
command stopped), W a warning (it went on; report it), I a note.

| code | meaning | do |
|---|---|---|
| EMIR-E101, EMIR-E102 | a file is missing or unreadable | check the path (relative to the working directory) |
| EMIR-E103 | a required input is not given | the hint names it; ask the person |
| EMIR-E104 | not the expected kind of file (a refused run's summary) | playbook 07 |
| EMIR-E201, EMIR-E217 | not a parsable DSPF | ask for the text DSPF |
| EMIR-E203, EMIR-E210, EMIR-W204 | wires without width, vias without cut annotation | ask for an extraction with `$W`/`$L` and `$N`/`$VC`/`$A` |
| EMIR-E204 | net not in the DSPF | `emir nets` |
| EMIR-E207, EMIR-W208 | the net is in pieces | an open in the layout or a missing connection: report it; `--force` only if the person decides |
| EMIR-W207 | vias checked as one cut | conservative; report which |
| EMIR-W302, EMIR-W307 | a layer without limit, a DSPF layer not in the process file | report as not checked; playbook 04 |
| EMIR-W306 | the illustrative process file | its limits are invented: say so |
| EMIR-E401, EMIR-E402, EMIR-E406 | `--currents` malformed, NaN, or not balancing | fix the JSON with the person (`emir schema currents`) |
| EMIR-E407 | nothing drives the net | give `--currents`, `--cc-sim`, `--netlist` or abstracts |
| EMIR-E408, EMIR-E409, EMIR-E410 | a binary result | re-simulate in ASCII (`-format psfascii`, `Xyce -a`, `set filetype=ascii`); binary PSF: the `psf -i ... -o ...` conversion in E408's `next_steps` (a shell command) |
| EMIR-E463 | a binary HSPICE result | re-run HSPICE with `.option post=2 post_version=2001` |
| EMIR-E464, EMIR-E466 | an HSPICE ASCII or CSDF result that does not parse (often truncated) | re-run the simulation to completion; pass the file unedited |
| EMIR-E465 | several sweep tables in one result | `--analysis sweep:N` (the message lists them) |
| EMIR-E467 | not a transient result | pass the `.tran`'s result |
| EMIR-W464 | the run's simulator is not verified on this machine, or not for every kind of current it read (`partly verified`), or its result's format is one no verification covers | with a `command` in `simulator_verification`: `emir verify-sim --sim xyce` (its simulator's name) (licensed: `--run` after the person agrees); without one (diode, BJT, JFET terminals; CSDF): nothing to run, report those currents as unverified |
| EMIR-W414 | the latest verification of the run's simulator version failed here | do not rely on the numbers; report it with the record's failing checks |
| EMIR-W415 | invalid records in verified.json (they count as none), or a records file that cannot be read (`records_problem`) | run `emir verify-sim` again (it moves a damaged file aside); never edit the file |
| EMIR-W416 | the result does not say its simulator version: not verified | simulate through `emir simulate` (it records the version); else report it as not verified |
| EMIR-E425 | a DSPF pin names no terminal of its device | check the `*\|I` records against the netlist; a subcircuit: `--probe` |
| EMIR-E426 | currents the run needs are NaN or Inf in the result | re-run the simulation to a finite result; never replace them by zero |
| EMIR-E427 | two result columns share a name (or HSPICE 9007 names cut alike) | re-simulate with `.option post_version=2001`, saving each current once |
| EMIR-I610 | the save lines went before the testbench's first `.alter` | pass the case's own result (`.tr0` the main case) |
| EMIR-W465, EMIR-I465 | HSPICE source-current sign from KCL (or not closing) | W465: probe the devices instead (`emir simulate --probe`) |
| EMIR-E663 | `emir verify-sim` failed (exit 70) | do not rely on that simulator's results here; report the failing checks and its version |
| EMIR-E520 | a simulation with no `--mode` | the mode is the person's choice: show them `emir next`'s `choose-mode` routes (cost, measured accuracy), ask, pass their answer; never pick |
| EMIR-E521 | `emir abstract --mode rcc` | an abstract is cc-grid only: `--mode cc-grid` with the block's CC simulation |
| EMIR-E415, EMIR-E416 | the result does not hold what the mode needs | `emir next ... --cc-sim <result> --mode <mode>` plans the save lines; if the result serves the other mode, tell the person |
| EMIR-W520 | an open-loop cc-grid run's droop above about 2 % of `--vdd` | report it as open loop; offer the self-consistent run (playbook 02); it re-simulates: the person decides |
| EMIR-W406, EMIR-W407, EMIR-W543, EMIR-I520 | the self-consistent loop diverged, hit `--max-passes`, or a later pass's simulation ran past its time limit or failed (each way pass 0, the open loop, is reported: report it as the open loop), or converged (`stopped`: `converged`, or `settled` with the residual above its tolerance) | quote `nets[].self_consistency.passes` and `stopped`; never raise `--max-passes` or `--sim-timeout` without the person |
| EMIR-E522 | the self-consistent split found no DSPF instance pin in the testbench | the instance names differ, or `--save-prefix` is wrong: compare `emir pins` with the testbench |
| EMIR-E523 | `--vdd` differs from the source the testbench drives the net with | the wrong net or testbench, or a typo: check with the person; leave `--vdd` out to take the testbench's |
| EMIR-E524 | the Spectre netlist does not hold the per-tap split (no pin's PWL file named, a split node feeding no pin, or no pin current moved) | the person adds the `--split-stub` lines and reconnects each pin; never edit their netlist yourself |
| EMIR-E525 | `--cc-sim` or `--currents` with `--self-consistent` | the loop simulates: drop them, or drop `--self-consistent` to screen that result |
| EMIR-E526 | a file EMIR did not write sits where the loop writes its droop file (Spectre: the manifest and each pin's .pwl file), a pass's result or log, or its split testbench | `--droop-file` elsewhere; never delete the person's file |
| EMIR-E540 | a device terminal on the net is no DSPF instance pin (the waveform loop moves every terminal with its droop) | compare `emir probe`'s matches with the testbench; the instance names or `--save-prefix` differ, or a device outside the block shares the rail: ask the person |
| EMIR-E541 | one `--vdd` with several nets (`--net VDD,VSS`), or a per-net `--vdd` naming a net `--net` does not close | give one per net (`--vdd VDD=1.2,VSS=0`) or leave `--vdd` out: each rail is read from the testbench |
| EMIR-W540 | the testbench's reltol is above 1e-4 or unset | tell the person: set `reltol=1e-4` or below in the testbench (the simulator's own error is as large as what the loop corrects) |
| EMIR-W541 | the split pins' currents do not add up to the rail source's (KCL) | another load or capacitor of the testbench is on the rail: its droop is not fed back; quote `self_consistency.passes[].kcl` |
| EMIR-W542 | a pass's droop needed more PWL points a pin than the cap (max(1 500, 0.3 x pass 0's time points)) and was compressed more coarsely | accuracy is lost: say so, with the passes' `pwl_tol_mV`; a testbench simulating fewer periods needs fewer points |
| EMIR-W544 | the DSPF has no capacitance on the net, so the loop's droop is the R-only grid's | the devices' peaks and minor branches can read low: say so, and offer the RC extraction of the rail |
| EMIR-W545 | the loop fed back the RC grid's droop, but the branches were scored on the R-only grid (no `--dynamic`, or the decap-free basis) | minor branches can read low or high: say so, and offer the same run with `--dynamic` (it re-simulates; the basis is the person's) |
| EMIR-W546 | the testbench saves its transient from a start time above 0 | the loop held the first droop before it: ask the person to save from 0 |
| a code `emir explain` calls retired | it is no longer raised (the `--mode auto` codes went when the mode became the person's choice) | its `retired_reason` names the code that took its place |
| EMIR-E417 | a pin at several connections | playbook 03 (a black-boxed block) or rcc |
| EMIR-E418 | out of memory | playbook 02 (`--net`, `--max-memory`) |
| EMIR-W403 | instance pins not saved | they count as zero: add their save lines |
| EMIR-W413, EMIR-W401 | the window is not settled, or fewer than two periods | `--period`, a longer simulation |
| EMIR-E501 | the input check failed inside `emir run` | run `emir check` with the same options and fix each FAIL |
| EMIR-E507, EMIR-E508 | usage error, unknown command | `emir <command> --help`, `emir --help` |
| EMIR-E590 | over MCP: the call needs the person's go-ahead | playbook 10 |
| EMIR-E592 | over MCP: an argument the tool does not have, or a required one left out; nothing ran | the tool's parameter names (`from_file`, not `--from`); playbook 10 |
| EMIR-E601, EMIR-E603, EMIR-E642, EMIR-E660, EMIR-E662, EMIR-E680 | a tool or package is missing (exit 3) | `emir info`; tell the person; install nothing without asking |
| EMIR-E602, EMIR-E605 | the simulation failed or timed out | read the log its `next_steps` names |
| EMIR-E701, EMIR-E105 | an output cannot be written | another directory |
| EMIR-E901 | internal error (exit 70) | re-run with `EMIR_DEBUG=1`; report the traceback as an EMIR bug |
| EMIR-E904, EMIR-E906 | interrupted (130), terminated (143) | nothing ran to the end; re-run when the person wants |

## Success criteria

- You named the code, said what it means (from `emir explain`, not from memory), and either ran its fix or asked
  the person for what it needs.
- You did not work around a refusal with `--force` or `--no-check` without the person's decision.

## What to report

The code and its title, the cause in the person's terms, what you ran to diagnose it (`emir explain`, `emir pins`,
`emir next`), the fix or the question for them, and, if a run eventually finished, its report (playbook 07) with
"Screening, not sign-off."

## Failure modes

| code | exit | meaning | do |
|---|---|---|---|
| EMIR-E511 | 2 | not an EMIR code | `emir explain --list` |
| EMIR-E512 | 2 | unknown schema name | `emir schema --list` |
| EMIR-E905 | 70 | an internal consistency check failed | an EMIR bug: report it with the command line |
| a retired code | 0 | an old code from an older EMIR | `emir explain` says which code replaced it |
