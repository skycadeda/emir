# Simulators and `emir verify-sim`

EMIR takes its currents from a transient simulation of the block (or from DC values).
Four simulators have a route into it: **Spectre**, **HSPICE**, **Xyce** and **ngspice**.
Each names its currents its own way, writes its own format and has its own sign
conventions, and a release, an option file or a site's wrapper can change any of them.
So **support for a simulator is proven on the machine that uses it**: `emir verify-sim`
runs testbenches whose answers are known by hand through that simulator, there, and
every run from a simulator that has not passed there says so.

> **EMIR is a research project, provided as is, with no warranty or guarantee of
> accuracy. It is your responsibility to test and verify its results for your process,
> tools and design before relying on them.** Screening, not sign-off.

## The support matrix

| | Spectre | HSPICE | Xyce | ngspice |
|---|---|---|---|---|
| licence | licensed | licensed | open source | open source |
| rcc: every branch current | `save XI0.Rm1:1 ...` | `.probe tran i(xi0.rm1) ...` | `.print tran I(XI0:RM1) ...` | `.save @r.xi0.rm1[i] ...` |
| what EMIR reads | PSF ASCII (`-format psfascii`), nutascii | HSPICE ASCII `.trN` (`.option post=2 post_version=2001`), CSDF | ASCII raw (`Xyce -a`), CSDF | ASCII raw (`filetype=ascii`) |
| refused, with the fix | binary PSF (EMIR-E408: the `psf` utility converts it) | binary `.trN` (EMIR-E463) | binary raw (EMIR-E409) | binary raw (EMIR-E409) |
| `emir simulate` | writes the deck and the command; runs Spectre only with `--run` | writes the deck and the command; runs HSPICE only with `--run` | runs Xyce | runs ngspice |
| tested in this repository | readers on synthetic PSF ASCII; the deck and save statements with a stand-in executable; Spectre never runs here | readers on synthetic files; the deck and its `.probe` lines with a stand-in executable; HSPICE never runs here | real runs where Xyce is installed (CI has no Xyce package and skips them) | real runs, in CI and on the development machine |
| verified on your machine by | `emir verify-sim --sim spectre --run` (ask the person first) | `emir verify-sim --sim hspice --run` (ask the person first) | `emir verify-sim --sim xyce` | `emir verify-sim --sim ngspice` |

Nothing in this repository claims that Spectre or HSPICE works with EMIR anywhere. The
numbers quoted from the original development site ([Cadence](cadence.md)) are
measurement provenance, not support: these routes are UNVERIFIED until `emir verify-sim`
passes on the machine that runs them. Xyce and ngspice run in this repository's tests,
and are still verified on site, because the build on your machine is not the one tested
here. A result another simulator wrote in one of these formats is read with a names
sidecar, as `unknown simulator`: UNVERIFIED until you check it against known currents.

## On-site verification

```sh
emir verify-sim --sim xyce                      # open simulators run directly (seconds)
emir verify-sim --sim spectre                   # licensed: writes the decks, prints the commands
emir verify-sim --sim spectre --run             # ... runs them, after the person's go-ahead
emir verify-sim --sim spectre --run --sim-args '+preset=cx +postlpreset=off'
```

Three simulations of the bundled sample rail (twelve 1-ohm segments fed at VDD, four
contacts) go through the same route a run takes (the save lines, the names sidecar, the
reader): ideal 1 mA loads at DC, the loads pulsed on an RC variant, and real devices on
the contacts (a diode-connected PMOS and NMOS, a resistor, an NMOS wrapped in a
subcircuit). Every branch current and its **direction**, every ratio and IR drop must
match the analytic answer (to 1e-3; rms to 1e-2): 79 checks, worked out in the
docstring of `emir/verify_sim.py`.

**What a pass covers**: on this machine, this simulator version, output format and
`--sim-args`: resistor branch currents, the one kind of current an rcc run reads.
**Not** a PDK's own models, PSS, nutascii, CSDF, `.alter` and sweeps, or your own
testbench: UNVERIFIED until checked against known currents on site.

**The record** is kept in `$EMIR_STATE_DIR` (else `~/.emir`), `verified.json`, keyed by
simulator, version, output format and `--sim-args`; the latest record for a key decides,
and a pass made by another EMIR minor version is stale. Every run carries
`simulator_verification`; anything but `verified` adds one warning: `EMIR-W464` (not
verified, or partly; the command that verifies it), `EMIR-W414` (the latest verification
failed: do not rely on the numbers) or `EMIR-W416` (the result does not say its version).
`emir next` plans `emir verify-sim` before the first run that uses a simulator, as a step
that needs the person's go-ahead for Spectre and HSPICE. A failed verification exits 70
(`EMIR-E663`).

## Xyce and ngspice

EMIR reads currents by the DSPF's own names (`R:1` for a branch). Two commands bridge
Xyce's and ngspice's names:

- `emir save-lines --sim xyce|ngspice` prints the lines in that simulator's syntax and
  writes the **names sidecar** (`<result>.names`) that maps its names onto the DSPF's;
- `emir simulate` adds those lines to a copy of your testbench (never edited), runs the
  simulator to an ASCII result, writes the sidecar and checks every current is there.

```sh
emir next --dspf block.dspf --process pdks/sky130.yaml --netlist tb.cir --period <T> --format json
emir verify-sim --sim xyce          # once per machine and Xyce version
emir simulate tb.cir --sim xyce --dspf block.dspf --out emir_out/sim/result.raw
emir run block.dspf --process pdks/sky130.yaml --cc-sim emir_out/sim/result.raw --period <T> \
    --summary-out emir_out/summary.json --html emir_out/html --viewer emir_out/viewer
emir report --from emir_out/summary.json
```

The testbench is yours: supplies, stimulus, models, a `.tran` that settles and then runs
two periods or more (`<T>`, the block's period: the run averages the record's last whole
periods of it, up to three), and the block instantiated once, with the DSPF itself
included (the RC netlist). rcc is exact for the simulated record over the whole periods
you give (--period); whether the testbench has settled is yours to establish, as for any
.meas. A testbench is code: ngspice runs a `.control` section in batch mode too
(`EMIR-W660`), so simulate only decks you trust. A result simulated by hand with EMIR's
lines needs its sidecar: `emir next --cc-sim result.raw ...` plans the `emir save-lines
... --names-out` step. A file of several plots (`.op` before `.tran`) is read at its one
transient; `--analysis op` or `sweep:N` picks another, and a DC sweep is refused
(`EMIR-E467`).

Measured with `scripts/opensim_proof.py` on example 02's rail (Xyce 7.10, ngspice 45):
DC sinks land on the hand values with both simulators (within 1.3e-9); pulsed sinks
within 2.8e-4 of the hand values (the trapezoid rule on the edges); Xyce and ngspice
agree within 1.1e-4. [`examples/02_xyce`](../examples/02_xyce/) and
[`examples/03_ngspice`](../examples/03_ngspice/) run that circuit with rcc (about 3 s
each).

Limits: a transient only (no PSS on this route, `EMIR-E462` without a `.tran`); the
netlist's names must be the DSPF's; one Xyce build (7.10) and ngspice 45 tested.

## Spectre and HSPICE

- **Spectre**: `emir save-lines <dspf> --prefix XI0. --out emir_save.scs`
  writes `save` statements under the DSPF's own names (Spectre's sign convention is
  EMIR's: no sidecar). `emir simulate --sim spectre` writes a deck that includes your
  testbench and those statements, and the command `spectre <deck> -format psfascii -raw
  <result>`. Keep `-format psfascii`. APS and Spectre X reduce the parasitic resistors
  rcc needs unless told not to (`+postlayout=upa`, `+postlpreset=off`): pass the options
  you simulate with as `--sim-args`, to `emir verify-sim` too. rcc reads a transient of
  two or more periods: a PSS time-domain file holds one (`EMIR-E431`), a harmonic one
  none (`EMIR-E414`).
- **HSPICE**:
  `emir save-lines <dspf> --sim hspice --prefix XI0. --names-out <stem>.tr0.names` prints `.option probe post=2 post_version=2001` and the `.probe
  tran` lines (double precision; 9601 keeps five digits, `EMIR-I464`; 9007 cuts names to
  16 characters). A swept result needs
  `--analysis sweep:N` (`EMIR-E465`). [`examples/11_hspice_offline`](../examples/11_hspice_offline/)
  runs the route on synthetic `.tr0` files (HSPICE itself is not run).

The syntax follows the vendors' published manuals and the open-source readers of gwave,
Electric, PyOPUS, WRspice and Xyce; anything not confirmed is marked UNVERIFIED.

## Looking at the results in KLayout

`emir run ... --viewer DIR` writes `DIR/emir.lyrdb`, a KLayout marker database (with the
`klayout` extra; without it, `EMIR-W703`):

```sh
klayout block.gds -m emir_out/viewer/emir.lyrdb
```

Its categories are `EM/<net>/<ratio band>` and `IR/<net>/<mV band>`; each marker is the
segment's box with its ratio, current and fix. [`agent/opensource/`](../agent/opensource/)
has helpers (a preview GDS from a DSPF, a marker summary, a headless render). Checked in
KLayout 0.30 in batch mode; the interactive Marker Browser was not exercised.
