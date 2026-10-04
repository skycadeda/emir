# Validation

Why to trust a number EMIR prints, and how to check it yourself. There are four layers
of evidence, each runnable from a clone:

| layer | command | time | proves |
|---|---|---|---|
| the self-test | `emir selftest` | seconds | this install works, offline, on the bundled sample |
| the validation suite | `emir validate` | seconds | the solvers and the signal processing against 13 closed-form answers |
| the examples | `bash examples/<name>/run.sh` | seconds each (the big-block sweep: an hour) | each flow end to end against golden results with stated tolerances |
| the test suite | `pytest` | minutes | every feature, every refusal code, every bug fix, the agent contract |

**Screening, not sign-off.** Validation shows that EMIR computes what it says it
computes. It does not make your process file's limits, your extraction or your
stimulus right; [Limits](limits.md) lists what is outside its model.

## `emir selftest`

Run it first, on any machine, before touching real data. It works offline in a scratch
directory and checks 19 things in five steps:

1. **Core imports**: importing EMIR pulls in only numpy, SciPy and PyYAML (no
   matplotlib, no klayout), so the core runs anywhere.
2. **The input checker on the sample**: the sample passes with one expected warning
   (its process file is illustrative), and a bad net and unbalanced currents are
   caught.
3. **The analysis with DC currents**: worst ratio 1.667x, worst via ratio 1.333x, IR
   drop 54.0000 mV, 10 violating segments: the hand values of the sample
   (worked out in [Getting started](getting-started.md#the-first-run) and in
   `emir/selftest.py`).
4. **The viewer data and HTML**: 16 EM segments, no fix hint on a passing segment, IR
   as a magnitude, the Virtuoso and KLayout files and the HTML index written, the
   summary's verdict FAIL.
5. **The Virtuoso SKILL files**: balanced parentheses in each.

It ends with `SELFTEST PASSED` and exit status 0, or names the failed check and exits
70. `emir selftest --format json` gives `emir.selftest/1` for an agent.

## `emir validate`: the analytic cases

Thirteen cases, each against an answer known in closed form, so a pass is a real
cross-check of the code rather than a comparison with its own earlier output. The
"measured" column is one run of `emir validate` in this repository; "passes when" is
the tolerance the case holds it to (`emir/validate.py`). Every process number in the
cases is invented, since the answers follow from whatever numbers are used.

| case | the closed form | measured | passes when | what it proves |
|---|---|---|---|---|
| DSPF necked strap | the neck at exactly 5.0000x, R = 1.7500 ohm | 5.0000x, 1.7500 ohm, one cluster, KCL residual 4.3e-19 A, R at temperature | ratio and R within 1e-6, KCL residual below 1e-12 A | the DSPF path, end to end |
| the hand-entry forms (width loss, Black's derating) | derate exp[(0.9 / (2 k)) (1/398.15 - 1/373.15)] = 0.415315; neck 1 / (0.415315 x 0.15) = 16.0521x, straps 1 / (0.415315 x 1.95) = 1.23477x | x0.415315, 16.0521x, 1.23477x; a segment no wider than dW refused (EMIR-E323) | derate within 1e-7, ratios within 1e-5, the refusal | the two forms design manuals print, as `emir process` has them typed: I_max = J x (W - dW), and Black's equation from em_tref 100 C to 125 C (worked out in [PDK onboarding](pdk-onboarding.md#entering-em-data-by-hand)) |
| transient metrics on non-uniform samples | avg 0.195000, rms 0.386437, peak 2.0000 mA | 0.195000, 0.386468, 2.0000 mA | avg within 1e-9 mA, rms within 1e-4 mA, peak within 1e-12 mA | trapezoid weights on the simulator's own steps; the plain mean over samples would read 4.9 times the true average |
| window selection | the steady-state average 0.1950 mA | fast transient: 7 of 8 periods kept, 0.1954 mA, sensitivity 0.10 %; slow transient: 1 period kept, sensitivity 3.3 %, warned | fast: avg within 2 %, sensitivity below 2 %; slow: at most 2 periods, sensitivity above 2 %, warned | start-up periods are dropped, and a window that does not settle is flagged, never silently averaged |
| PSS harmonics | avg 1.0, rms 1.095445, peak 1.8 | 1.000000, 1.095445, 1.8000 | avg and rms within 1e-12, peak within 1e-3 | single-sided harmonic amplitudes read correctly |
| CC ingest to the grid | 0.19500, 0.38644, 2.00000 mA in the neck | 0.19500, 0.38647, 2.00000 mA | relative 1e-3, and the sign | simulated pin currents reach the grid with the right sign |
| self-consistency fixed point | `V_nom / (R_load + R_grid)` = 9.828010 mA | 9.828010 mA in 3 passes (the waveform loop: the plain update, then the companion), on a supply and on a ground net | relative 1e-4, converged, either sign | the droop loop converges to the right point, with either sign |
| dynamic IR against the exact R \|\| C response | 99.5037, 70.7107, 9.9504 mV at wRC = 0.1, 1, 10 | 99.5017, 70.7092, 9.9502 mV (0.00 %) | relative 5e-3 | the dynamic-IR integrator |
| decap series resistance | 4.7565 mV (ideal decap), 31.1334 mV (50 ohm access) | 4.7703 mV (0.29 %), 31.1753 mV (0.13 %) | relative 5e-3, and the access resistance raising the droop by more than 5 % | a decap's access resistance is modelled; leaving it out would under-predict the droop by 84.7 % |
| decap lumping | 36.000 fF conserved | 8 lumps to 2, 36.000 fF | capacitance within 1e-21 F, resistances within 1e-9 ohm | lumping keeps the capacitance, access resistances combine in parallel |
| rcc mode | R_end = 2.750000 ohm | exact; potentials rebuilt from branch currents match the solve to 0 V; a 10 % error on one branch is caught at 0.0750 mV of loop residual | R_end within 1e-12 ohm; potentials, loop residual and KCL below 1e-15; the planted error caught | the direct route, and its consistency check |
| MOS decap | C = Cox W L = 200.000 fF, R = R_channel / 6 = 1.6667 ohm | exact; the ordinary device excluded; an unmodelled decap warned about, not zeroed | C and R relative 1e-9; the warning raised | MOS decaps are found and sized, and only decaps |
| decap-aware EM | feed segment rms 1.06078 / 1.02375 mA, peak 1.50049 / 1.31004 mA, decap rms 0.30431 / 0.35920 mA at wR(C_w + C_d) = 1 / 3 (decap-free 1.14891 and 1.8 mA) | 1.06077 / 1.02375, 1.50046 / 1.31002, 0.30428 / 0.35917 mA (worst 8.2e-5); avg identity 1.5e-15 / 2.8e-14 | relative 1e-3; the averages' identity `G Vbar = Ibar - C (V_N - V_0)/T` within 1e-10; avg the static one, bit for bit | the dynamic solve's branch currents, the decap's own, and the feed current with the decaps |

Every case needs only numpy, SciPy and PyYAML, so none is skipped for a missing
package. `--quick` skips the three slow transient cases (dynamic IR, decap series R and
decap-aware EM). `--out D` keeps the cases' files.
pytest runs the suite under the `validate` marker.

## The examples, with golden results

Every example runs offline from a clone (with the virtual environment active) and
compares its results with golden files; `run.sh` exits non-zero on a mismatch, and
`pytest -m example` runs them all.

| example | what it runs | golden | tolerance | what it proves |
|---|---|---|---|---|
| [`02_xyce`](../examples/02_xyce/) | Xyce, rcc and cc-grid, on a rail worked out by hand | `expected/summary.json` | relative 1e-3 | the Xyce route, both modes, against the hand values (FAIL on the one-cut contact's peak, 1.04167x) |
| [`03_ngspice`](../examples/03_ngspice/) | the same circuit through ngspice | `expected/summary.json` | relative 1e-3 | the ngspice route; Xyce and ngspice agree |
| [`05_chip_level`](../examples/05_chip_level/) | abstracts, the top run, budgets, back-annotation, alignment modes | `expected/*.json`, 93 values | relative 1e-6 | the hierarchical result equals the flat run (4.9e-13) and the hand numbers |
| [`06_big_block`](../examples/06_big_block/) | streamed against dense superposition; with Xyce, the RCC and CC routes at N = 10, the waveform self-consistent loop against RCC at N = 10, and that mesh screened through the loop with invented limits | `expected/stream_small.json`, `expected/xyce_n10.json`, `expected/selfconsist_n10.json`, `expected/screen_n10.json` | stated in each file | streamed equals dense; the CC route against RCC; the loop's acceptance criteria (governing avg and IR within 0.05 %, rms and peak within 0.2 %, each device's peak within 1 %, at most 5 simulations); the screen's verdict, its four failures by name and their ratios within 0.1 % |
| [`08_open_layout`](../examples/08_open_layout/) | the magic route on a SKY130 NMOS array: rcc, cc-grid open and self-consistent (Xyce or ngspice), and the operating point | `expected/results.json`; `expected/emir_layout_record.json` (a record of the removed layout command, not checked) | relative 5 % (run numbers), 1e-6 (rcc against the simulator's own output, branch by branch), the loop's bounds against rcc | rcc reads the simulator's branch currents exactly (6e-12); the loop within 0.3 % (avg, rms) and 1.9 % (peak) of rcc on the governing branches |
| [`09_cadence_flow`](../examples/09_cadence_flow/) | the extraction plan, save lines and testbench, with no Cadence tool | `expected/summary.json` | exact (two temperatures within 1e-9) | what a Cadence site would run |
| [`10_mcp_agent`](../examples/10_mcp_agent/) | a scripted MCP client following `emir_next` on the sample | `expected/transcript.json` | relative 1e-6, absolute 1e-9 (`expected/tolerances.json`) | the MCP server, the tools' documents, the go-ahead refusal |
| [`11_hspice_offline`](../examples/11_hspice_offline/) | the HSPICE route on SYNTHETIC `.tr0` results and their CSDF twins (HSPICE does not run): save lines, names sidecar, reader, rcc and cc-grid | `expected/summary.json`, four runs | relative 1e-4 | the HSPICE reader and the i3 sign set from KCL, against the hand values (Rm1 avg 1.66667x, IR 54.0 mV); every run "not verified" (the CSDF twins: `format not covered`) |
| [`12_self_consistent`](../examples/12_self_consistent/) | with Xyce (or ngspice): the open loop, the self-consistent loop and the RCC reference on a rail with resistive loads | `expected/summary.json`, and the hand numbers in its README | relative 1e-3, absolute 1e-6 | the self-consistent loop settles on the RCC reference for loads whose current follows their supply, when the block's other rails are ideal (IR 100.050 mV in 3 simulations against RCC 100.050 mV; open loop 108.000 mV) |
| [`13_decap_aware`](../examples/13_decap_aware/) | `emir run --dynamic` once per EM basis on a synthetic decap mesh (offline), and with Xyce the RCC reference on example 06's mesh with decaps | `expected/decap_small.json` (52 checks), `expected/xyce_n20_decap.json` (42 checks) | per check (relative 0.5 % offline; bounds and loose tolerances where another Xyce version takes other steps) | decap-free, decap-aware and worst on one block, the person's choice of basis; decap-aware at or above RCC on every branch class and the straps |

The bundled sample (`emir/data/sample/`) is the quickstart and the self-test's case.

## Cross-checks inside the repository

Beyond closed forms, several results are checked against another way of computing
the same thing:

- **cc-grid against rcc** on the same circuit, through Xyce and ngspice
  (`scripts/opensim_proof.py`): within 1.3e-9 on DC sinks, within 2.9e-4 of the hand
  values on pulsed sinks; on an inverter bank the averages agree within 0.26 % and the
  peaks differ by up to 40 % (conservative). See
  [Open-source flow](open-source-flow.md#measured-the-open-simulator-proof).
- **Streamed against dense superposition** (`examples/06_big_block`): within 7e-15 of
  the largest branch on every case; the tests hold them to 1e-10.
- **Hierarchical against flat** (`examples/05_chip_level`, `tests/test_hierarchy.py`):
  4.9e-13 on the example, within 1e-9 on a two-level chip.
- **magic's DSPF against magic's own netlist** (`tests/test_magic.py`): every resistor
  between the same nodes, each net pair's capacitance counted once; and, marked
  `magic`, a real patched magic's extraction against the committed one.
- **The open-PDK process files against their sources**: every cited line is checked
  when the PDKs are installed (`pytest tests/test_pdks.py -k citations`).

Outside the repository, cc-grid was measured against RCC at the original development
site, on a generated inverter-bank test rail. Those numbers are
quoted, labelled, in [Big blocks](big-blocks.md), and cannot be reproduced from this
repository. They measure the method (cc-grid against RCC), not a simulator's route into
EMIR: no page here says that a licensed tool was verified anywhere. A simulator's route
is verified on the machine that uses it, by `emir verify-sim`
([Simulators](simulators.md#on-site-verification)).

## The test suite

`pytest` from a clone with the `dev` extra runs every test, including the examples and
the validation suite; tests that need a missing simulator, PDK or package are skipped
with the reason. Beyond the physics, the suite enforces the agent contract: every
`--format json` output is one document valid against its schema; every error code is
registered, used and catalogued; the generated files (the error catalog, the schemas,
the Cadence SKILL copies, the [command-line reference](cli-reference.md)) match their
sources; and the leak guard finds no foundry data, site path or licence server in any
file.

## What validation does not cover

- Your process file: EMIR checks its form, not its values. Validate it by hand
  ([Onboarding a PDK](pdk-onboarding.md#validate-the-file)).
- Your extraction: `emir check` finds missing annotations, not a wrong extraction
  setting that still writes them.
- Your stimulus and operating mode: the currents are what your simulation says.
- Anything Cadence on a live Virtuoso, and Calibre and StarRC: see the UNVERIFIED list
  in [Cadence flow](cadence.md#unverified-until-checked-on-site-in-one-list).
- Real designs at scale: the speed and accuracy figures in this repository come from
  synthetic meshes and small blocks ([Limits](limits.md)).
