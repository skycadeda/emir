# 00 Preflight: prove the install before touching anyone's data

Run this at the start of every session, after installing or upgrading EMIR, and whenever a command behaves
oddly. It takes seconds, needs no design data and no licence, and tells you which route the machine supports.
**Screening, not sign-off.** EMIR is a research project, provided as is, with no warranty or guarantee of accuracy:
the person tests and verifies its results before relying on them; say so with every result you report.

## When to use

- Always first: before reading, simulating or screening any of the person's files.
- After `pip install`, a `git pull`, a new Python, or moving to another machine.
- When a later command fails with exit 70 (internal error) or exit 3 (a tool or package missing).

## Inputs

None. The self-test runs on the bundled sample (`emir/data/sample/`), whose process file is ILLUSTRATIVE (invented
limits, the right shape and the wrong values).

## Commands

### Worked example (runs as printed from the repository root)

<!-- test: run -->
```sh
emir --version
emir info --format json
emir selftest --format json
emir validate --quick --format json
```

`emir selftest --format json` prints (key fields):

```json
{"schema": "emir.selftest/1", "passed": true}
```

`emir info --format json` lists `tools[]` (`name`, `found`, `path`, `source`, `licensed`, `version`; for the
simulators `verified`: the newest record on this machine whose version's latest `emir verify-sim` passed with this
EMIR version, or null) for spectre, hspice,
xyce, ngspice, magic, virtuoso, pvs and quantus, `packages[]` (`name`, `version`: null when not installed,
`required`), `records_file`, `commands` and `exit_codes`. What it finds depends on the machine. `emir validate --quick` runs
the 10 fast analytic validation cases of the 13 (it leaves out the 3 slow transient ones, which `emir validate` runs)
and prints `passed`, `n_run`, `n_failed`, `n_skipped`.

<!-- test: expect -->
```json
{"schema": "emir.validate/1", "passed": true, "n_failed": 0}
```

When the person asks what EMIR can do here, two more read-only commands answer it:

<!-- test: run -->
```sh
emir --help --format json
emir schema --list --format json
```

### On-site verification of a simulator

Support for a simulator is proven on the machine that uses it, never assumed. Before the first run on this machine
whose currents come from a simulator (Spectre, HSPICE, Xyce or ngspice), verify that simulator's route once (per
simulator version and output format); `emir next` plans it, and `tools[].verified` says whether it is done:

```sh
emir verify-sim --sim xyce --format json          # open simulators: runs at once (seconds)
emir verify-sim --sim spectre --format json       # licensed: writes the decks, prints the commands, runs nothing
emir verify-sim --sim spectre --run --format json  # only after the person said yes (a licence, six short runs)
```

`passed` true (exit 0): the record is kept in `records_file` and later runs say `verified`. `passed` false (exit
70, EMIR-E663): do not rely on runs from that simulator here; report the failing `checks[]` (name, expected, got)
and the `version` the simulator printed. Spectre APS or Spectre X: pass the options the person simulates with,
`--sim-args '+aps'` or `--sim-args '+preset=cx +postlpreset=off'`. Report: the simulator and its version, PASS or
FAIL with the number of checks, the failing ones, and where the record is.

### On the person's machine

The same commands. If `emir` is not on PATH, from a clone of the repository:

```sh
bin/emir selftest --format json
```

or install it (ask first): `python3 -m venv .venv && .venv/bin/pip install -e '.[all]'`, then `.venv/bin/emir`.

## Decision points

| you see | do |
|---|---|
| `emir: command not found` | `bin/emir` from a clone; otherwise ask the person how EMIR is installed |
| `emir selftest` exit 0, `passed: true` | go on: pick the playbook from `AGENTS.md`, or run `emir next` |
| exit 70, `code` EMIR-E902 | the self-test failed: stop, show the person the failing `checks[]`; do not screen anything |
| exit 3, `code` EMIR-E603 | a Python package is missing: tell the person; with their go-ahead run the install command the message names (from a clone, `pip install -e '<clone>[all]'`; EMIR is not on PyPI) |
| `tools[]`: spectre, virtuoso, pvs or quantus `found` | a Cadence site: playbook 06 (each is licensed: ask before running one) |
| `tools[]`: hspice `found` | the HSPICE route (playbook 06, its HSPICE section; licensed: ask before running it) |
| a simulator you will use has `verified` null | verify it before its first run: `emir verify-sim --sim xyce` (open: it runs), `emir verify-sim --sim spectre` then, after the person agrees, the same with `--run` (licensed) |
| `tools[]`: magic `found`, notice EMIR-W640 | magic is older than the version EMIR was checked with: the magic route may crash (playbook 09); tell the person |
| `tools[]`: xyce or ngspice `found`, no spectre | the open-source route: playbook 05 |
| no simulator found | DC currents (`--currents`), block abstracts, or a result the person simulated elsewhere |
| `packages[]`: klayout `version` null | no KLayout marker database and no layout under the charts; `emir extract --tool magic` needs `--cell`; the DSPF flow still works |
| `packages[]`: matplotlib `version` null | `--html` writes its pages without the PNG chart (EMIR-I720); the numbers are the same |
| `emir validate` exit 70, `code` EMIR-E903 | a validation case failed: report it as an EMIR bug, with `cases[]` |

## Success criteria

- `emir selftest --format json` exits 0 with `"passed": true`.
- You know which simulators and optional packages exist (`emir info`), so you can pick the route.
- Nothing of the person's was read or written.

## What to report

One or two lines: the EMIR version, "self-test passed", which simulators were found and which of them are verified on
this machine (`tools[].verified`), that Spectre, HSPICE, Virtuoso, PVS and Quantus are licensed tools you will not
start without asking, and which optional packages are missing with what they disable. Do not report the sample's
numbers as if they meant anything: the sample's limits are invented.

## Failure modes

| code | exit | meaning | do |
|---|---|---|---|
| EMIR-E902 | 70 | self-test failed | stop; report the failing check; reinstall with the person |
| EMIR-E903 | 70 | a validation case failed | report it; the analysis is not trustworthy on this install |
| EMIR-E603 | 3 | a Python package is missing | `emir info` shows which; install the extra with the person's go-ahead |
| EMIR-E901 | 70 | internal error | re-run with `EMIR_DEBUG=1` for the traceback and report it |
| EMIR-E507 | 2 | a usage error (a misspelt option) | `emir <command> --help`; options are never abbreviated |
| EMIR-E508 | 2 | unknown command | `emir --help` lists the commands |
| EMIR-E663 | 70 | `emir verify-sim` failed: the simulator's route does not give the analytic answer here | stop using that simulator's results here; report the failing checks and its version |
| EMIR-W415 | | verified.json holds records that are not valid `emir.verify_sim/1` records: they count as none | never edit verified.json; run `emir verify-sim` again |
| EMIR-E662, EMIR-E660 | 3 | the simulator `--run` asked for is not found | `emir info`; tell the person (`$SPECTRE_BIN`, `$HSPICE_BIN`, `$XYCE_BIN`, `$NGSPICE_BIN`, the site file) |
| EMIR-E102, EMIR-E105 (`emir verify-sim`) | 2 | this user may not read the records file, or write the state directory: a verification here could keep no record, so none runs | tell the person: read access to `verified.json`, or `EMIR_STATE_DIR` set to a directory of their own |
| EMIR-W467 | | the verification record could not be kept | the state directory (`$EMIR_STATE_DIR`, else `~/.emir`) is not writable, or its `verified.json` is not readable by this user: tell the person |
