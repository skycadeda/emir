# Error codes

Every refusal, failure, warning and notice EMIR raises carries a stable code. The full
catalog, generated from the registry in `emir/errors.py` (a test fails when it is
stale), is **[`agent/errors.md`](../agent/errors.md)**. From the command line:

```sh
emir explain EMIR-E417          # one entry: meaning, hint, the command that fixes or diagnoses it
emir explain E417 W207          # several; the EMIR- prefix and case are optional
emir explain --list             # every code
```

## How to read a code

`EMIR-<level><area><number>`:

| part | values |
|---|---|
| level | `E` an error (a refusal, or an input-check FAIL), `W` a warning, `I` information |
| area (first digit) | 1 files and I/O; 2 DSPF and extracted layout; 3 process file and layer mapping; 4 currents and simulation results; 5 options and command-line usage; 6 external tools; 7 outputs and viewers; 8 hierarchy; 9 internal |
| number | within the area, a decade per part of EMIR: 01-39 the core; 40 the magic route (`emir extract --tool magic`; the removed layout command before it), 50 big blocks, 60 open simulators, 70 the GDS layout reader and the PDK files, 80 Cadence, 90 MCP (area 8 is the hierarchy flow's alone) |

Codes are never reused: a code whose condition is gone moves to the catalog's retired
list, with the code that replaced it. Branch on the code, never on the message text,
which may be reworded.

## Exit status

| status | meaning |
|---|---|
| 0 | done (`emir check`: the inputs are OK) |
| 1 | a FAIL verdict, an IR budget FAIL, or an EM basis asked for and not computed (`em_basis.honoured` false), with `--fail-on-violation` |
| 2 | bad input, or refused |
| 3 | a required external tool or Python package is missing |
| 70 | internal error (a bug), or a failed self-test or validation |
| 130 | interrupted (SIGINT) |
| 143 | terminated (SIGTERM) |

In text mode an error goes to stderr as `emir: EMIR-Exxx: message`, then `hint:` and
`next:` lines. With `--format json` it is an `emir.error/1` document on stdout with
`code`, `level`, `title`, `message`, `hint`, `exit_code`, `next_steps` and `details`.
A run that is refused or fails also writes that document to `--summary-out`, so a stale
summary is never relayed. `EMIR_DEBUG=1` re-raises an uncoded exception with its
traceback, for a bug report.

## The ones you will meet first

| code | meaning | what to do |
|---|---|---|
| `EMIR-W306` | the process file is the illustrative sample: its limits are not real | point `--process` at your PDK's file |
| `EMIR-W307` | DSPF layers with no process entry: not checked | add them, or map them with `dspf_layers` |
| `EMIR-E210` | wire resistors without a width: they cannot be checked | extract with geometry annotation (`$W`, `$L`) |
| `EMIR-W207`, `EMIR-W308` | vias checked as one cut (no count; or an area and no `vcwidth`) | extract with via annotation, or add `vcwidth` |
| `EMIR-E204` | the net is not in the DSPF | `emir nets <dspf>` lists them (the message suggests close names) |
| `EMIR-E401` | `--currents` is not a JSON object | give it inline (`'{"VDD": 0.004, "MP0:s": -0.004}'`) or the path of a file holding it; `emir schema currents` |
| `EMIR-E406` | `--currents` do not balance | the currents into the net must sum to zero |
| `EMIR-E520` | a simulation with no `--mode`: the mode is the person's choice, EMIR does not pick one | ask the person; `emir next` without `--mode` lists rcc, cc-grid and cc-grid self-consistent with their measured cost and accuracy |
| `EMIR-E415`, `EMIR-E416` | branch (rcc) or pin (cc-grid) currents not saved | the save lines: `emir save-lines <dspf> --mode rcc` or `--mode cc-grid` |
| `EMIR-W520` | an open-loop cc-grid run's droop is above about 2 % of `--vdd` | report it as open loop; the self-consistent run (`--mode cc-grid --self-consistent`) is the person's call ([Methods and modes](method-modes.md#closing-the-loop-self-consistency)) |
| `EMIR-E417` | an instance pin reaches the net at several places, one saved current | rcc for a small net, or the [hierarchical flow](chip-level.md) |
| `EMIR-E418` | the analysis would not fit in memory (in a hierarchical run: async alignment or a phase sweep) | `--scalars`, one `--net` at a time, or `--align inphase`; a flat cc-grid run streams instead (`--superposition auto`) |
| `EMIR-E590` | an MCP call needs the person's go-ahead | ask, then pass `confirm: true` |
| `EMIR-E592` | an MCP call named an argument the tool does not have, or left out a required one | the tool's parameter names (`from_file`, not `--from`); nothing ran |
| `EMIR-E601`, `EMIR-E603` | Spectre (for `--netlist`) or a Python package is missing (exit 3) | `emir info`; a finished result with `--cc-sim`; the install command the message names (`pip install -e '.[all]'` from a clone) |
| `EMIR-W464` | the run's simulator is not verified on this machine, or not for every kind of current it read (`partly verified`: a diode, BJT or JFET terminal read directly; a CSDF result) | `emir verify-sim --sim xyce` (the run's simulator) once ([Simulators](simulators.md#on-site-verification)); when the warning gives no command, no verification can cover those currents: report them as unverified |
| `EMIR-W414` | the latest verification of the run's simulator version failed on this machine | do not rely on the numbers; fix the cause and verify again |
| `EMIR-W416` | the result does not say which simulator version wrote it: not verified | simulate through `emir simulate`, which records the version |
| `EMIR-E425` | a DSPF pin names no terminal of its device (it is never read from another one) | check the DSPF's \*\|I records; a subcircuit's pins: `--probe` |
| `EMIR-E426` | currents the run needs are NaN or Inf in the result | re-run the simulation to a finite result |
| `EMIR-E427` | two result columns share a name (HSPICE 9007 cuts names to 16 characters) | `.option post_version=2001`, each current saved once |
| `EMIR-E408`, `EMIR-E463` | a binary result (Spectre PSF, HSPICE `.tr0`) | convert with `psf -i ... -o ...`, or re-simulate as text (`-format psfascii`; `.option post=2`) |

[Concepts](concepts.md#the-input-checker) explains the input checker that raises most
of them, and the playbook
[`agent/playbooks/08-troubleshooting.md`](../agent/playbooks/08-troubleshooting.md) is
organised by code.
