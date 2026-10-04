# Examples

Each runs offline from a clone (with the virtual environment active) with
`bash examples/<name>/run.sh [OUT_DIR]`, and compares its results with golden files.
Every `run.sh` writes into the directory given as its argument, else its default
(`out/` or `emir_out/` beside the script, its header says which); the environment
variable some of them also read (`OUT`, `EMIR_EXAMPLE_OUT`) is the fallback.
06 and 13 take their options (`--big`, `--sweep` ...) beside it, in any order. Each has a
README with the hand calculation or the source of its numbers. Screening, not sign-off:
every layout and limit here is synthetic or illustrative unless its README says it
comes from a public PDK.

There is no `01`: the first step is the quickstart on the bundled sample
(`emir/data/sample/`, an invented rail and an ILLUSTRATIVE process file), which needs
nothing but EMIR itself. There is no `04` or `07` either: those examples went with the
`emir mesh` command, which was removed ([CHANGELOG](../CHANGELOG.md)). From the
repository root (the README's quickstart):

```sh
emir selftest
emir run emir/data/sample/rail.dspf --process emir/data/sample/process.yaml \
    --currents '{"VDD": 0.004, "MP0:s": -0.001, "MP1:s": -0.001, "MP2:s": -0.001, "MP3:s": -0.001}' \
    --summary-out emir_out/summary.json --html emir_out/html
emir report --from emir_out/summary.json
```

| example | shows | needs |
|---|---|---|
| [`02_xyce`](02_xyce/) | the open-simulator flow, rcc and cc-grid, on a rail worked out by hand | Xyce |
| [`03_ngspice`](03_ngspice/) | the same circuit through ngspice | ngspice |
| [`05_chip_level`](05_chip_level/) | chip-level EM/IR, hierarchically: abstracts, budgets, back-annotation; equal to the flat run | nothing more |
| [`06_big_block`](06_big_block/) | streamed against dense superposition; RCC against CC + cc-grid on a size sweep, the self-consistent loop pass by pass, and the mesh screened with invented limits (a few straps fail, with their fixes) | Xyce for the sweep |
| [`08_open_layout`](08_open_layout/) | a SKY130 layout on the magic route: magic's network, rcc branch by branch against the simulator, cc-grid open and self-consistent against rcc | the `klayout` extra, a SKY130 PDK, a magic built with the `geoout` patch, Xyce or ngspice |
| [`09_cadence_flow`](09_cadence_flow/) | what a Cadence site runs, offline: the extraction plan, save lines, the testbench | nothing more |
| [`10_mcp_agent`](10_mcp_agent/) | a scripted MCP client following `emir_next` on the sample | the `mcp` extra |
| [`11_hspice_offline`](11_hspice_offline/) | the HSPICE route on synthetic HSPICE ASCII and CSDF results (HSPICE itself is not run): save lines, names sidecars, rcc and cc-grid, the "not verified on this machine" line | nothing more |
| [`12_self_consistent`](12_self_consistent/) | cc-grid open loop (9 % droop, EMIR-W520), then `--self-consistent` (the droop fed back, pass by pass), against the RCC reference and the closed loop worked out by hand | Xyce or ngspice |
| [`13_decap_aware`](13_decap_aware/) | decap-aware EM with `--dynamic`: each EM basis on a small decap mesh, the person's choice in `emir next`; RCC against decap-free and decap-aware on Xyce, decap straps and two resistive rails included; the self-consistent loop with decaps fed back, left out or simulated | Xyce for the comparison |

An agent picks the example closest to its task from this table; the playbooks in
[`agent/playbooks/`](../agent/playbooks/) name the ones each task uses.
