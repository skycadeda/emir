# 03 · The open-simulator flow with ngspice

The same block, testbenches and steps as [02_xyce](../02_xyce/README.md), with
**ngspice** supplying the currents. The input files are byte-identical to
02's (a test keeps them so). The two examples are one circuit through two
simulators, and their results must agree with each other and with the hand
calculation in 02's README.

```sh
sh examples/03_ngspice/run.sh     # about 2 s; outputs in examples/03_ngspice/emir_out/
```

You need ngspice on `$PATH` (or `$NGSPICE_BIN`); without it the script stops
with exit status 3 (`EMIR-E660`).

## What is different with ngspice

* **The save lines.** `emir simulate --sim ngspice` adds `.save` lines and
  `.options filetype=ascii` to its copy of the testbench, and runs
  `ngspice -b -r rcc.raw rcc.ngspice.cir`. ngspice names a resistor current
  `i(@r.xi0.rm1[i])`; the `.names` sidecar maps it to the DSPF's `Rm1:1`.
* **The DSPF goes in as it is.** ngspice reads the `$` annotations as
  comments, so its deck includes `loadrail.dspf` itself (Xyce's gets a copy
  without them).
* **No `.control` section runs.** In batch mode with a rawfile (`-b -r`)
  ngspice does not execute the testbench's `.control` block (measured with
  ngspice 45). `emir simulate` warns when it finds one (`EMIR-W660`).

## The numbers

`expected/summary.json` holds this example's results. Against Xyce's (02), on
the same circuit, measured by `scripts/opensim_proof.py`:

| quantity | hand | Xyce rcc | ngspice rcc |
|---|---|---|---|
| Rm1 avg ratio | 0.84722 | 0.84746 | 0.84746 |
| Rm1 rms ratio | 0.25218 | 0.25223 | 0.25223 |
| Rc3 peak ratio (FAIL) | 1.04167 | 1.04167 | 1.04167 |
| IR, average currents | 28.060 mV | 28.068 mV | 28.068 mV |
| dynamic IR | 90.000 mV | 90.000 mV | 90.002 mV |

Across every rail segment and contact, ngspice and Xyce differ by at most 1.0e-4
(every metric: the peak of Rm1).
ngspice ran each simulation in about 0.04 s on the development machine, Xyce
in about 0.2 s (timings vary run to run).

Screening, not sign-off.
