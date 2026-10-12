# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] (1.1.0): first public release

EM and IR-drop screening of extracted analog layout, built to be driven by an AI agent.
Screening, not sign-off. EMIR is a research project, provided as is, with no warranty or
guarantee of accuracy: test and verify its results for your process, tools and design
before relying on them.

A faster route for big blocks (cc-grid: device currents solved through the R-only grid) and the
chip-level hierarchy are kept in the development repository (branch `cc-grid-guard`, not
published) for a later release.

### Added

- **EM and IR screening** of every wire segment (mA/um) and via (mA/cut) against the
  process file's avg, rms and peak limits, the IR drop per supply, and a fix for each
  failure in a layout engineer's units (`emir run`); an input checker that names every
  gap with a code and the command that fixes it (`emir check`).
- **Simulated currents, rcc**: every branch current of the RC simulation is read and
  every metric checked (avg, rms and peak EM, static and dynamic IR, the IR from the
  resistances the simulation ran); `--mode rcc` may be left out. The window is the
  record's last whole periods of `--period`, up to three: rcc is exact for the simulated
  record over the whole periods you give (--period); whether the testbench has settled is
  yours to establish, as for any .meas. Refused with no number: a transient or a
  `--netlist` run without `--period` (`EMIR-E428`), fewer than two periods (`E431`), the
  last two differing by over 2 % (`E429`). **DC currents** give a screen: avg = rms = peak.
- **Simulators**: Spectre, HSPICE, Xyce and ngspice (save lines, names sidecars,
  `emir simulate`), each proven on the machine that uses it by `emir verify-sim`.
- **The magic route on SKY130**: `emir extract --tool magic` writes the DSPF from the GDS
  with a magic built with the public `geoout` patch (branch `extresist-geoout` of
  https://github.com/codervijay/magic, offered upstream as
  https://github.com/RTimothyEdwards/magic/pull/562).
- **Process files** for SKY130, GF180MCU and IHP SG13G2 from their public data, every value
  cited; `emir import-ict`; and EM data typed by hand when no readable file exists
  (`emir process new`, `emir process check`), with reports that say where each limit came from.
- **Cadence**: `emir extract` plans PVS LVS and Quantus (a MATCH gate; `--run` after the
  person's go-ahead), Spectre save lines and testbench templates, and a Virtuoso menu and
  Explorer that highlight the worst segments read-only (UNVERIFIED until checked on site).
- **The agent layer**: `AGENTS.md`, five playbooks with worked examples that run as printed,
  a Claude Code skill, `emir next` (the decision tree as code), `--format json` and JSON
  Schemas on every command, stable error codes (`emir explain`), `next_steps`,
  `emir report`, an MCP server (`emir mcp`) that asks the person (`EMIR-E590`) before a
  licensed tool or a long run, and `agent/evals/`: 11 task cards with deterministic graders.
- **Outputs**: the run summary, the report, an HTML page per net with a chart of the
  layout, a KLayout marker database and the Virtuoso Explorer's data.
- **Proof**: `emir selftest`, `emir validate` (6 closed-form cases), and examples that
  compare their results with golden files.
- **Packaging**: a wheel and an offline bundle for Linux, and a leak guard
  (`scripts/leak_guard.py`) that refuses foundry data in any file or commit message.
  **Licence: MIT**; the open PDKs' data in `pdks/` stays under their Apache-2.0 licences.

## [1.0.0] - 2026-09-08

First release, not published.
