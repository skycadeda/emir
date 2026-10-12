# EMIR with Calibre extraction (UNVERIFIED)

**Status: UNVERIFIED until checked on site.** No Calibre tool was available where
EMIR was developed, and nothing on this page has been run. On the machine that has
Calibre, the local agent verifies the DSPF it writes with `emir check <dspf> --process
<pdk.yaml>`, which names every annotation EMIR needs that is missing. It maps what EMIR's DSPF reader needs onto
Calibre options named in public Siemens EDA material, so that a Calibre user can
check each point on their release. Please report what works (an issue with the
DSPF header and one resistor line is enough).

## Two routes

**A. Calibre LVS, then Quantus** (where Quantus is licensed). Quantus can read LVS
connectivity from several LVS tools; its `input_db -type` option lists `calibre`
besides `pvs` (seen in Quantus's own option help during development; UNVERIFIED
until checked on site). EMIR's Quantus command file then needs only another
database type:

```sh
python agent/cadence/quantus/render_ccl.py --lvs-db-type calibre --svdb <calibre LVS dir> \
    --cell <cell> --tech-dir <qrc tech dir> --corner <corner> --temperature <C> --kind r_only
```

The Calibre `input_db` takes options of its own (the design cell name, the layout
format, a layer map); which of them a Calibre LVS run needs is UNVERIFIED.

**B. Calibre xRC / xACT writing the DSPF.** From the public Siemens EDA blog posts
cited below: the extraction types include R, C+CC, RC and RCC; the netlist formats
include DSPF; reduction is controlled with `PEX REDUCE` (ANALOG, DIGITAL, TICER,
CC); and resistor annotations named RLAYER, RLENGTH, RLOCATION and RWIDTH exist
(shown for the CALIBREVIEW format; whether they annotate a DSPF is UNVERIFIED).

## What EMIR's DSPF reader needs, and the Calibre option believed to give it

| EMIR needs (reader in `emir/dspf.py`) | Calibre (UNVERIFIED) |
|---|---|
| one resistor per segment, no series merging | extraction type R; analog reduction off |
| `$W` (and `$L`) on each wire resistor: J = I/w needs the width | RWIDTH, RLENGTH |
| the conductor layer on each resistor: a bare `$name`, or `$layer=` / `$lyr=`, or `$lvl=N` with `dspf_layers` mapping the number | RLAYER (check the token form) |
| coordinates of resistor nodes (`*\|S`) and segments | RLOCATION |
| `*\|I` instance-pin records WITH x y (without them the pin is dropped and no simulated current attaches) | the instance section with locations |
| a via cut count (`$N`, `$VC`, `$SAVC`, or `$A` with `vcwidth` in the process file); without one each via is checked as ONE cut (conservative, EMIR-W207) | UNVERIFIED |
| `*\|OPERATING_TEMPERATURE` when R was scaled to temperature | UNVERIFIED |
| for rcc: the RC extraction (R with coupling C), simulated and screened | extraction type RCC (UNVERIFIED) |

Check a Calibre DSPF with `emir check <dspf> --process <process.yaml>` before a run:
its findings name every missing annotation with a code.

## Command sequence (UNVERIFIED, from general Calibre usage)

`emir_calibre_pex.svrf.tmpl` is a sketch of the PEX statements; the run itself is
usually an LVS run that keeps the connectivity database, then the PEX steps that
read it and write the netlist. Follow your release's Calibre xRC/xACT manual.

## Sources

- Siemens EDA blog, "How to use Calibre parasitic extraction tools: step-by-step
  guide", 2026-02-17:
  https://blogs.sw.siemens.com/calibre/2026/02/17/how-to-use-calibre-parasitic-extraction-tools-step-by-step-guide/
- Siemens EDA blog, "Boost simulation results with powerful selective net extraction
  with Calibre xACT", 2025-10-01:
  https://blogs.sw.siemens.com/calibre/2025/10/01/boost-simulation-results-with-powerful-selective-net-extraction-with-calibre-xact/

Calibre is a trademark of Siemens Industry Software Inc.; Quantus of Cadence Design
Systems, Inc. EMIR is not affiliated with either.
