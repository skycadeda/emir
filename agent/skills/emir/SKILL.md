---
name: emir
description: EM (electromigration) and IR-drop screening of extracted analog layout with the `emir` command line. A DSPF (on SKY130, magic extracts one from the GDS), the PDK's process file and currents (a Spectre, HSPICE, Xyce or ngspice simulation, or DC values) give per-segment EM ratios, IR drop and the fix for each violation. Use it when the person asks for EM, EMIR, IR drop, current density, electromigration or a power-grid check of a layout, or brings a DSPF, an EMIR error code or an EMIR result to explain. Screening, not sign-off.
---

# EMIR

Read `AGENTS.md` at the root of the EMIR repository first; the task playbooks are in `agent/playbooks/` beside it.
Outside the clone, `emir info --format json` gives `package_dir` (the clone's `emir/`). Without a clone, `emir next`
carries the same decision tree and `emir explain <code>` every code.

```sh
emir info --format json
emir selftest --format json
emir next --dspf <block.dspf> --process <pdk.yaml> --cc-sim <result.raw> --format json
```

`emir next` only reads: run it first. Pass **every** input the person gave (`--dspf`, `--process`, `--cc-sim`,
`--currents`, `--netlist`, `--gds`, `--cell`, `--net`, `--period`, `--html`, `--viewer`; after a run,
`--run-summary`), ask for each `missing[]` item, run `steps[]` in order, and ask before every `needs_human: true` step.

- A simulated run is rcc: every branch current, every metric, over the record's last whole periods of the person's
  `--period` (up to three). rcc is exact for the simulated record over the whole periods you give (--period); whether
  the testbench has settled is yours to establish, as for any .meas. Refused with no number: EMIR-E428 (a transient,
  no period), E431, E429 (`emir next` plans the period or the longer simulation). DC currents: avg = rms = peak.
- Never invent a limit, a current, a temperature or a measured value: ask. Never call a result sign-off.
- Ask before licensed tools (Spectre, HSPICE, PVS, Quantus) and long runs; one simulation at a time; verify a
  simulator here (`emir verify-sim`) before relying on it. Never edit the person's files or touch their Virtuoso.
- Report with `emir report --from emir_out/summary.json`, plus "Screening, not sign-off." and the disclaimer: EMIR
  is a research project, provided as is, with no warranty or guarantee of accuracy; the person tests and verifies
  its results before relying on them.
