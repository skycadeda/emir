# Getting started

Install EMIR, prove the install, and screen the bundled sample. A minute or two,
offline after the install.

**Screening, not sign-off.** The sample's numbers are invented; its verdict is FAIL on
purpose, so you see what a failure looks like. EMIR is a research project, provided as
is, with no warranty or guarantee of accuracy: test and verify its results for your
process, tools and design before relying on them.

## What you need

- Linux (x86_64 is what is built and tested), Python 3.10 or newer.
- The core needs only numpy, SciPy and PyYAML. Everything else is optional and depends
  on the flow you use (table below).
- No licence server and no licensed tool. Licensed tools (Spectre, HSPICE, Quantus,
  PVS, Virtuoso) are needed only for their flows, and EMIR never starts one without
  being asked (`--run`).

## Install

### From a clone (recommended, and what an agent uses)

```sh
git clone https://github.com/skycadeda/emir emir
cd emir
python3 -m venv .venv
. .venv/bin/activate
pip install -e .              # the core: numpy, scipy, pyyaml
```

Add the extras your flow needs (table below), for example `pip install -e '.[all]'`.

A clone also works without installing: `bin/emir` runs the clone's code with the first
`python3` (3.10 or newer) that has numpy, SciPy and PyYAML, or with `$EMIR_PYTHON`.
It resolves symbolic links, so `ln -s "$PWD/bin/emir" ~/bin/emir` puts it on your
`PATH`.

### The offline bundle

For a machine with no network and no suitable Python (a typical design server), the
release workflow builds an offline bundle with `scripts/build_bundle.sh` and attaches
it to the GitHub Release: `emir-<version>-linux-x86_64.tar.gz`, with `SHA256SUMS`.
It holds a standalone Python with numpy, SciPy, PyYAML and matplotlib, the wheels it
was installed from, `bin/emir`, the documentation, the `agent/` directory (the
Cadence integration loads from there), `pdks/` (the template and the open PDKs'
process files) and `examples/`, and the licence texts of what it redistributes
(`licenses/`).

```sh
sha256sum -c SHA256SUMS --ignore-missing
tar xzf emir-<version>-linux-x86_64.tar.gz
emir-<version>/bin/emir selftest
```

Nothing is installed outside the unpacked directory. Where no release carries a
bundle (or for another Python), build it yourself with
`EMIR_ALLOW_LOCAL_BUNDLE=1 sh scripts/build_bundle.sh`; it needs network access once,
to fetch the standalone Python and the wheels.

### The wheel

```sh
pip install emir-<version>-py3-none-any.whl            # from a GitHub Release
pip install 'emir-<version>-py3-none-any.whl[all]'     # with the extras
```

or build it from a clone: `pip install build && python -m build` writes
`dist/emir-<version>-py3-none-any.whl` (the `build` package is in the `dev` extra too).
emir is not on PyPI: install it from the wheel file or a clone, never by name. A wheel
holds the package and the bundled sample, not the repository's `agent/`, `examples/`
and `pdks/` directories: an agent works best from a clone. Advice that cites one of
those files (a playbook, `agent/cadence/`, `pdks/sky130.yaml`) names the file in the
clone emir runs from, and from a wheel its page in the repository.

With a wheel, the quickstart's files are in the installed package: `emir info` prints
where (`sample`), so from any directory:

```sh
S=$(emir info --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["sample_dir"])')
emir run "$S/rail.dspf" --process "$S/process.yaml" \
    --currents '{"VDD": 0.004, "MP0:s": -0.001, "MP1:s": -0.001, "MP2:s": -0.001, "MP3:s": -0.001}' \
    --summary-out emir_out/summary.json
emir report --from emir_out/summary.json
```

### Extras

| extra | adds | for |
|---|---|---|
| `report` | matplotlib | the PNG charts: the EM/IR chart of `--html` |
| `klayout` | the klayout Python package | the KLayout marker database (`emir.lyrdb`); the layout under a chart (`emir run --gds`); finding the top cell for `emir extract --tool magic` |
| `mcp` | the MCP Python SDK (1.19 or newer) | `emir mcp`, the MCP server |
| `all` | all of the above | everything a user runs |
| `dev` | the above plus gdstk, jsonschema, pytest, ruff, pre-commit, build | the test suite |
| `docs` | MkDocs with the Material theme | `mkdocs build --strict` of this site |

External tools, each needed only by its flow: Xyce or ngspice (simulation in the
[open-source flow](open-source-flow.md)), HSPICE ([Simulators](simulators.md)), magic
built with the public `geoout` patch, and a PDK (`emir extract --tool magic`), KLayout
(looking at the markers), Spectre, PVS and
Quantus (the [Cadence flow](cadence.md)). `emir info` says what it found and where, and
which simulators are verified on this machine: run `emir verify-sim --sim xyce` (your simulator's name) once
per simulator before relying on its results
([On-site verification](simulators.md#on-site-verification)).

## Prove the install

```sh
emir selftest
```

It runs offline in a few seconds, on the bundled sample, and changes nothing outside a
scratch directory: 19 checks, from "the core imports only numpy, SciPy and PyYAML" to
"the Virtuoso SKILL files have balanced parentheses". It ends with `SELFTEST PASSED`
and exit status 0. Run it before you touch real data, and after any upgrade.

```sh
emir validate          # 16 cases with closed-form answers, in seconds
emir info              # versions, and where each external tool was found
```

`emir validate` needs the `dev` extra for its three GDS cases; without it they are
skipped with a message (`--quick` also skips the three slow transient cases). See
[Validation](validation.md) for what each case proves.

## The first run

The bundled sample is a 60 um Metal1 rail, 0.75 um wide, in 12 segments of 1 ohm,
with four loads of 1 mA tapping it through contacts. Its process data are invented
round numbers, so every result can be worked out by hand (`emir/selftest.py` shows
the arithmetic). From the root of the clone:

```sh
emir run emir/data/sample/rail.dspf --process emir/data/sample/process.yaml \
    --currents '{"VDD": 0.004, "MP0:s": -0.001, "MP1:s": -0.001, "MP2:s": -0.001, "MP3:s": -0.001}' \
    --summary-out emir_out/summary.json --html emir_out/html
emir report --from emir_out/summary.json
```

`--currents` gives DC currents in amps flowing *into* the net: 4 mA in at the port
`VDD`, 1 mA out at each load's pin. The run first checks its inputs (the sample's
process file warns, with `EMIR-W306`, that its limits are not real), then prints:

```text
  IR drop        : 54.0000 mV
  verdict        : FAIL, worst 1.667x avg wire on Metal1
    FAIL   1.667x avg  wire Metal1   Rm1
    ...
    FAIL   1.333x avg  via  Cont     Rc3
overall verdict: FAIL -- 1 net(s): 1 FAIL, 0 WARN, 0 CLEAN; worst 1.667x on VDD. Screening, not sign-off.
```

and `emir report` writes the summary a person reads, with the fix for each item:

```text
| VDD | Rm1 | (2.500, 0.000) | Metal1 | wire | avg | 1.667x | Metal1: widen 0.750 -> 1.255 um (x1.67) |
| VDD | Rc3 | (60.000, 0.000) | Cont | via | avg | 1.333x | Cont: 1 -> 2 cuts (+1) |
```

By hand: the first three segments carry all 4 mA in 0.75 um, 5.33 mA/um against the
sample's 3.2 mA/um, which is 1.667x. The last contact carries 1 mA through one cut
against 0.75 mA per cut: 1.333x. The IR drop is 4 + 3 + 2 + 1 mA through 3 ohm each
(30 mV), plus 1 mA through the last contact's 24 ohm (24 mV): 54 mV.

Open `emir_out/html/index.html` in a browser for the per-net page: a map of the net
coloured by ratio, and the tables. `emir run --format json` prints the same summary as
one JSON document (`emir schema run` describes it).

## Next

`emir next` reads what you have and prints the commands to run, in order, each with
its reason:

```sh
emir next --dspf block.dspf --process pdk.yaml --cc-sim result.raw
```

With a simulation result its first step is the choice of mode (rcc or cc-grid), which is
yours: it shows what each costs and how accurate it is, and which one your result can
serve. Run it again with `--mode rcc` or `--mode cc-grid` for the commands.

| you have | start with |
|---|---|
| a DSPF and a simulation result | [Concepts](concepts.md), then [Methods and modes](method-modes.md) |
| an ADC top or a PLL top | [Big blocks](big-blocks.md) |
| a chip made of blocks | [Chip level](chip-level.md) |
| Xyce or ngspice, or only a GDS | [Open-source flow](open-source-flow.md) |
| Spectre or HSPICE results, or a simulator to verify on this machine | [Simulators](simulators.md) |
| Cadence tools | [Cadence flow](cadence.md) |
| a PDK with no process file yet | [Onboarding a PDK](pdk-onboarding.md) |
| an agent to drive all this | [Driving EMIR from an agent](agents.md) |

The worked examples in [`examples/`](../examples/) each run offline with
`bash examples/<name>/run.sh` from the clone's root (with the virtual environment
active) and compare their results with golden files; [Validation](validation.md)
lists them.
