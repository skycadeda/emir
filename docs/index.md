# EMIR

> **EMIR is a research project, provided as is, with no warranty or guarantee of
> accuracy. It is your responsibility to test and verify its results for your process,
> tools and design before relying on them.**

**Electromigration (EM) and IR-drop screening of extracted analog layout, built to be
driven by an AI agent.** EMIR reads the resistor network of each net (a DSPF; on SKY130,
magic extracts one from the GDS), your PDK's EM limits and the currents (a Spectre,
HSPICE, Xyce or ngspice simulation, or DC values). It says which wire segments and vias
carry more current than the process allows, how far each supply drops, and what to
change: widen this wire to that width, add that many cuts.

**Screening, not sign-off.** EMIR finds EM and IR problems early and says where to
look. Foundry-qualified EM/IR analysis remains the sign-off flow.

![Flow diagram: an AI agent drives four stages, extraction (RC, R-only, magic for SKY130), simulation (Spectre, HSPICE, Xyce, ngspice), emir (rcc, the DC screen) and the report (JSON, HTML, Virtuoso Explorer, KLayout).](images/flow.png#only-light)
![Flow diagram: an AI agent drives four stages, extraction (RC, R-only, magic for SKY130), simulation (Spectre, HSPICE, Xyce, ngspice), emir (rcc, the DC screen) and the report (JSON, HTML, Virtuoso Explorer, KLayout).](images/flow_dark.png#only-dark)

*How EMIR fits a flow, with the agent driving each stage.*

A simulated run reads every branch current of the RC netlist (rcc), so every metric is
checked exactly: avg, rms and peak EM, static and dynamic IR. DC currents give a screen
of a current budget (avg = rms = peak). [Method and accuracy](method.md) has the method
and the evidence.

## Three ways in

1. **An AI agent.** Point Claude Code, Codex, Cursor or any agent at the repository: it
   reads [`AGENTS.md`](../AGENTS.md), asks `emir next` what to run, branches on stable
   error codes and relays `emir report`. MCP clients use `emir mcp`.
2. **The command line.** `emir selftest`, `emir next`, `emir check`, `emir run`,
   `emir report`: [Getting started](getting-started.md).
3. **Virtuoso.** An EMIR menu and an Explorer that highlights the worst segments on the
   layout, read-only (UNVERIFIED until checked on site): [Cadence](cadence.md).

## The pages

| page | for |
|---|---|
| [Getting started](getting-started.md) | install, prove the install, the first run |
| [Method and accuracy](method.md) | what EMIR checks, rcc and DC currents, how exact it is, what it leaves out |
| [Simulators](simulators.md) | Spectre, HSPICE, Xyce and ngspice, and `emir verify-sim` on your machine |
| [Process files and extraction](process-files.md) | your PDK's limits (from its files or typed by hand), the DSPF, magic on SKY130 |
| [Cadence](cadence.md) | PVS, Quantus, Spectre and Virtuoso |
| [Reference](cli-reference.md) | every command and option (generated), with the codes and exit statuses |

EMIR runs on Linux with Python 3.10 or newer; its core needs only numpy, SciPy and
PyYAML, and no licensed tool. It is licensed under the MIT License. Cadence, Virtuoso,
Spectre, Quantus and PVS are trademarks of Cadence Design Systems, Inc.; Calibre is a
trademark of Siemens; HSPICE and StarRC are trademarks of Synopsys. EMIR is not
affiliated with or endorsed by any of them.
