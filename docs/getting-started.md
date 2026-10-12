# Getting started

Install EMIR, prove the install, and screen the bundled sample: a minute or two, offline
after the install.

**Screening, not sign-off.** The sample's limits are invented and its verdict is FAIL on
purpose. EMIR is a research project, provided as is, with no warranty or guarantee of
accuracy: test and verify its results for your process, tools and design before relying
on them.

## Install

Linux (x86_64 is what is built and tested) and Python 3.10 or newer. The core needs only
numpy, SciPy and PyYAML, and no licence.

**From a clone** (recommended, and what an agent uses):

```sh
git clone https://github.com/skycadeda/emir emir
cd emir
python3 -m venv .venv
. .venv/bin/activate
pip install -e .              # the core; pip install -e '.[all]' adds every extra
```

A clone also runs without installing: `bin/emir` uses the first `python3` (3.10 or
newer) that has numpy, SciPy and PyYAML, or `$EMIR_PYTHON`.

**The offline bundle**, for a design server with no network: each GitHub Release carries
`emir-<version>-linux-x86_64.tar.gz` and `SHA256SUMS` (a standalone Python with the
packages, `bin/emir`, the docs, `agent/`, `pdks/` and `examples/`). Check it with
`sha256sum -c SHA256SUMS --ignore-missing`, unpack it and run
`emir-<version>/bin/emir selftest`. `EMIR_ALLOW_LOCAL_BUNDLE=1 sh scripts/build_bundle.sh`
builds one yourself.

**The wheel**: `pip install emir-<version>-py3-none-any.whl` (from a Release, or
`python -m build` in a clone). EMIR is not on PyPI. A wheel holds the package and the
bundled sample, not `agent/`, `examples/` or `pdks/`: advice that cites one of those
files names its page in the repository. `emir info` prints where the sample is
(`sample_dir`).

| extra | adds | for |
|---|---|---|
| `report` | matplotlib | the EM/IR chart of `--html` |
| `klayout` | the klayout package | the KLayout marker database, the layout under a chart, the top cell for `emir extract --tool magic` |
| `mcp` | the MCP Python SDK (1.21.1 or newer) | `emir mcp` |
| `all` | all of the above | everything a user runs |
| `dev` | the above plus gdstk, jsonschema, pytest, ruff, pre-commit, build | the tests |
| `docs` | MkDocs with the Material theme | this site |

External tools are needed only by their flows: Xyce or ngspice, HSPICE, Spectre, PVS and
Quantus, magic with the public `geoout` patch, KLayout. `emir info` says what it found.

## Prove the install

```sh
emir selftest          # 19 checks on the bundled sample, offline, seconds: SELFTEST PASSED
emir validate          # the 6 analytic validation cases
emir info              # versions, and where each external tool was found
```

Run `emir selftest` before you touch real data, and after any upgrade. Before the first
run that uses a simulator's results on a machine, verify its route there:
`emir verify-sim --sim xyce` (your simulator's name; see [Simulators](simulators.md)).

## The first run

The bundled sample is a 60 um Metal1 rail, 0.75 um wide, in 12 segments of 1 ohm, with
four 1 mA loads tapping it through contacts, and invented round-number limits. From the
root of the clone:

```sh
emir run emir/data/sample/rail.dspf --process emir/data/sample/process.yaml \
    --currents '{"VDD": 0.004, "MP0:s": -0.001, "MP1:s": -0.001, "MP2:s": -0.001, "MP3:s": -0.001}' \
    --summary-out emir_out/summary.json --html emir_out/html
emir report --from emir_out/summary.json
```

`--currents` gives DC currents in amps flowing *into* the net: 4 mA in at the port `VDD`,
1 mA out at each load's pin. The input check warns (`EMIR-W306`) that the sample's
limits are not real; then the report gives each failure with its fix:

```text
| VDD | Rm1 | (2.500, 0.000) | Metal1 | wire | avg | 1.667x | Metal1: widen 0.750 -> 1.255 um (x1.67) |
| VDD | Rc3 | (60.000, 0.000) | Cont | via | avg | 1.333x | Cont: 1 -> 2 cuts (+1) |
- VDD: 54.000 mV (from the feed)
```

By hand: the first three segments carry all 4 mA in 0.75 um, 5.33 mA/um against the
sample's 3.2 mA/um: 1.667x. The last contact carries 1 mA through one cut against
0.75 mA per cut: 1.333x. The IR drop is 4 + 3 + 2 + 1 mA through 3 ohm each (30 mV),
plus 1 mA through the last contact's 24 ohm (24 mV): 54 mV. Open
`emir_out/html/index.html` for the map of the net.

## Next

`emir next` reads what you have and prints the commands to run, in order, each with its
reason:

```sh
emir next --dspf block.dspf --process pdk.yaml --cc-sim result.raw --period <T>
```

With a simulation result it plans rcc: the check and the run on every branch current
the result holds, or the save lines and the simulation when it lacks them. A transient
needs the block's period (`--period`): without it, the plan asks for it.

| you have | read |
|---|---|
| a DSPF and a simulation result | [Method and accuracy](method.md) |
| Xyce, ngspice, Spectre or HSPICE | [Simulators](simulators.md) |
| a PDK with no process file yet, or only a GDS on SKY130 | [Process files and extraction](process-files.md) |
| Cadence tools | [Cadence](cadence.md) |
| an agent to drive all this | [`AGENTS.md`](../AGENTS.md) |

The worked examples in [`examples/`](../examples/README.md) run offline with
`bash examples/<name>/run.sh` and compare their results with golden files.
