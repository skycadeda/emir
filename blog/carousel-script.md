# Carousel script

The text of each page of `blog/carousel.pdf` (and of `blog/images/carousel_01.png` ..
`carousel_10.png`, the same pages as images), as it appears on the page. Ten pages,
1080 x 1350, dark theme. The pages are drawn by `scripts/make_figures.py`
(`carousel_pages()`): to change a page, edit that function and run
`python scripts/make_figures.py --only carousel`, then update this file;
`tests/test_blog.py` checks that every **Kicker**, **Title**, **Text**, **Body** and
**Footer** line below is on its page of the PDF.

The **Alt text** lines are for posting the PNGs as a multi-image post (a document post
of the PDF takes no per-page alt text). **Numbers** names where each page's numbers come
from; every page is drawn from a run of this repository, and no page quotes a number
from outside it.

## Page 1: the cover

- **Kicker:** EMIR · OPEN-SOURCE EM/IR SCREENING
- **Title:** EM/IR screening your AI agent can run
- **Text:** Electromigration and IR drop on extracted analog layout: every segment checked, the fix in microns or cuts, a report a person can act on.
- **Visual:** EMIR's EM chart of example 06's 10 x 10 power mesh (32 inverters, Xyce currents through the self-consistent loop), the four failing straps at the feed corner outlined, two callouts, "#1 1.83× FAIL / widen 2.000 -> 3.655 um" and "#2 1.75× FAIL / widen 2.000 -> 3.510 um"; the legend naming every colour (ok below 0.8×, blue by ratio; warn 0.8× to 1×; fail 1× and over, or not a number; the violation marker); the flow agent -> emir next -> emir run -> emir report; "Screening, not sign-off."
- **Footer:** examples/06_big_block: a synthetic 10 x 10 mesh, 32 inverters, Xyce currents through the self-consistent loop (4 simulations); invented limits. Swipe for how it works. Screening, not sign-off.
- **Alt text:** EMIR, EM/IR screening your AI agent can run: an EM chart of a synthetic power mesh where four straps at the feed fail; the two that leave it, at 1.83x and 1.75x, carry their fixes: widen 2.000 to 3.655 and 3.510 um.
- **Numbers:** examples/06_big_block's run.sh step 4 (`emir run --mode cc-grid --self-consistent --sim xyce` with `process_screen.yaml`), run again by `scripts/make_figures.py`; golden values in `examples/06_big_block/expected/screen_n10.json`.

## Page 2: what it checks

- **Kicker:** WHAT IT CHECKS
- **Title:** Every segment, against your limits, with the fix
- **Text:** EM ratio (avg, rms, peak) of each wire section and via cut, the IR drop, and what to change. Here: where the supply enters the mesh.
- **Visual:** the feed corner zoomed (page 1 shows the whole mesh), its four failures labelled, #1 1.83×, #2 1.75×, #3 1.07× and #4 1.03× FAIL, with a 10 µm scale bar; the colour legend; below, two cards, "by hand" against "the run": Kirchhoff at the feed, 3.655 + 3.509 + 0.231 = 7.395 mA (#1 up, #2 right, the corner inverter) against the supply's average current, 7.395 mA; #1, the strap up from the feed, 3.655 mA / 2.0 µm / 1.0 mA/µm = 1.83× against 1.83× FAIL, fix: widen 2.000 -> 3.655 um.
- **Footer:** examples/06_big_block, invented round-number limits; Xyce currents through the self-consistent loop (4 simulations). Screening, not sign-off.
- **Alt text:** The mesh's feed corner, zoomed: four straps fail, at 1.83x, 1.75x, 1.07x and 1.03x; by hand, the currents leaving the feed add up to the supply's 7.395 mA, and 3.655 mA over 2.0 um at 1.0 mA/um is 1.83x.
- **Numbers:** as page 1; the branch currents and the supply's are the run's own (`emir run`'s solved averages and the loop's check of the supply current), each average current is the golden ratio x 2.0 um x 1.0 mA/um.

## Page 3: agent first

- **Kicker:** AGENT FIRST
- **Title:** Built to be driven by an AI agent
- **Text:** Every command answers in JSON, every failure carries a code, and the tool itself says what to run next.
- **Visual:** three counts, 21 "commands, each with --format json", 311 "stable codes: branch on the code, not the text" (errors, warnings and notes), 30 "JSON schemas (emir schema)"; below, the scripted session: the `emir next` plan with its mode line, "mode: cc-grid (the person's choice)", then seven steps (the verification of Xyce among them), step 6 (`emir run`, verdict FAIL) and the `emir report` lines (worst Rc3, peak 1.042x; fix: Cont: 1 -> 2 cuts (+1); IR drop VDD 28.068 mV).
- **Footer:** The plan and its steps on examples/02_xyce, run as printed (scripted; cc-grid as the person chose; Xyce currents, the bundled illustrative limits); long lines shortened (…). Screening, not sign-off.
- **Alt text:** 21 commands, 311 stable codes, 30 JSON schemas; a terminal where emir next, in the mode the person chose, plans seven steps, emir run gives FAIL, and emir report names the worst item, peak 1.042x, and its fix: 1 -> 2 cuts.
- **Numbers:** the counts from the package (`emir.cli.COMMANDS`, `emir.errors.REGISTRY`, `emir schema --list`); the session is example 02's plan with each step run exactly as `emir next` printed it, by `scripts/make_figures.py`, with Xyce.

## Page 4: how it fits

- **Kicker:** HOW IT FITS
- **Title:** Your extraction and simulator in, one report out
- **Text:** The agent drives each stage with one emir command and relays the report.
- **Visual:** an "AI agent" band beside four stages: 1 Extraction (RC · C-only · R-only; emir extract plans the Quantus / PVS run; magic runs it for SKY130), 2 Simulation (Spectre · HSPICE · Xyce · ngspice; save lines, 0 V probes; each must pass emir verify-sim on your machine), 3 emir (rcc · cc-grid · self-consistent · streaming; the mode is your choice; limits from a PDK file, or typed by hand), 4 Report (JSON · HTML · chart · Virtuoso · KLayout; emir report: what to relay, and where the limits came from).
- **Body:** Big blocks: C-only simulation + R-only grid (cc-grid). Small blocks: RC simulation (rcc). You choose: the agent shows the measured trade-offs and asks.
- **Footer:** Spectre, HSPICE, Quantus, PVS and Virtuoso need licences: none runs in this repository's tests, and each must be verified on site by your agent before you rely on it. Xyce, ngspice, KLayout and magic run in its examples.
- **Alt text:** The flow: an AI agent drives extraction (RC, C-only, R-only), simulation (Spectre, HSPICE, Xyce, ngspice), emir (rcc, cc-grid, self-consistent, streaming; the mode is your choice) and the report (JSON, HTML, chart, Virtuoso, KLayout).
- **Numbers:** none.

## Page 5: big blocks, time

- **Kicker:** ADC TOP, PLL TOP: TIME
- **Title:** Big blocks: skip the RCC simulation
- **Text:** Simulate C-only, push the device currents through the R-only grid. From 132 to 40,032 mesh nodes the RCC simulation went from 0.64 s to 350 s; the whole CC route stayed under 7 s.
- **Visual:** log-log wall time against mesh nodes: the RCC simulation rising to 350 s, the CC route (simulation + cc-grid) to 7.0 s; below, two figures: 50× "faster at 40,032 nodes: RCC simulation against the CC route" and 1,298 → 1.4 "MiB of simulation result, RCC against C-only".
- **Footer:** Xyce, synthetic, serial: examples/06_big_block/results/sweep.csv, the same 32 inverters on a growing mesh; CC route = C-only simulation + emir cc-grid. Trends, not absolute times.
- **Alt text:** Wall time against mesh size on a synthetic Xyce mesh: the RCC simulation grows from 0.64 s to 350 s over 132 to 40,032 nodes, while the whole CC route stays under 7 s.
- **Numbers:** `examples/06_big_block/results/sweep.csv`, rows k32 (the CC route at 40,032 nodes is 0.368 + 6.59 = 6.96 s, drawn as 7.0 s; 50× is its `speedup_sim_vs_cc_route`, 50.3; the result files are `rcc_raw_MB` 1,298 and `cc_raw_MB` 1.41).

## Page 6: big blocks, memory

- **Kicker:** BIG BLOCKS: MEMORY
- **Title:** Big grids stream in bounded memory
- **Text:** A 92,001-node grid with 2,000 device taps would need a 2.70 GiB dense transfer matrix; EMIR streams it instead, with the default settings:
- **Visual:** two figures, 11.7 s (wall time, defaults) and 804 MiB (peak memory, defaults); bars of peak memory, dense against streamed, each path forced, on five grids (85 / 73, 1,622 / 241, 2,018 / 424, 1,271 / 282, 4,593 / 806 MiB), each row labelled with its nodes, device taps and samples.
- **Footer:** Synthetic meshes, emir run single-threaded (examples/06_big_block/results/stream_bench.csv). The bars force each path; dense against streamed: the same currents to 7e-15.
- **Alt text:** Peak memory, dense against streamed superposition, on five synthetic grids; the largest, 92,001 nodes with 2,000 device taps, needs 4,593 MiB dense and 806 MiB streamed; with the defaults it ran in 11.7 s at 804 MiB.
- **Numbers:** `examples/06_big_block/results/stream_bench.csv`: the large figures are the default `auto` run (11.7 s, 804 MiB), as the article and the post quote; the bars are the forced runs (dense 4,593 MiB, stream 806 MiB).

## Page 7: big blocks, accuracy

- **Kicker:** BIG BLOCKS: ACCURACY
- **Title:** How close is cc-grid to RCC?
- **Text:** cc-grid minus RCC, the branch that decides the verdict, over four mesh sizes. Plus is conservative.
- **Visual:** five rows: avg −0.15 to −0.12 % (every branch over 1 % of the largest: −0.21 to −0.02 %); rms +6.83 to +13.68 % (every branch: +4.1 to +13.8 %); peak +15.28 to +24.29 % (every branch: −19.0 to +37.8 %); IR drop −0.15 to −0.13 % (the worst node of the net); dynamic IR +14.49 to +16.66 % (open loop, conservative on every mesh measured). Below, a highlighted card, "Open loop at 4.1 % droop (8x the devices)": "avg +1.34 %, rms +38.2 %, peak +79.9 %: the error grows with the droop" and "--self-consistent on a 128-inverter mesh at 4.1 % droop: page 9."
- **Body:** Read cc-grid avg and IR as ±2 %; confirm a fix that a secondary branch's rms or peak decides with an rcc run of that sub-block. Above about 2 % droop, report the open and closed loop side by side.
- **Footer:** Xyce, synthetic, serial: examples/06_big_block/results/sweep.csv (32 inverters, N = 10 to 70; 256 at N = 70, the 4.1 % case). The guard band is the docs' recommendation from these numbers. Screening, not sign-off.
- **Alt text:** cc-grid against RCC on the governing branch of a synthetic mesh: avg and IR 0.12 to 0.15 % low, rms 7 to 14 % high, peak 15 to 24 % high; at 4.1 % droop, with 8x the devices, avg +1.34 %, rms +38.2 % and peak +79.9 %.
- **Numbers:** `examples/06_big_block/results/sweep.csv`, rows k32 N = 10 to 70 (the five rows); row k256 (N = 70, 256 inverters against 32; 4.1 % droop: `rcc_ir_mV` on the 1 V rail), `avg_gov`, `rms_gov` and `peak_gov` (the card); the same row the article's "eight times the devices" quotes.

## Page 8: chip level

- **Kicker:** CHIP LEVEL
- **Title:** Chip-level EM/IR, block by block
- **Text:** Characterise each block once, screen the top with the blocks black-boxed, then re-check the blocks with the top's pin voltages.
- **Visual:** four steps: 1 emir abstract (each block once: its pin currents, port conductance and internal IR drop, in one JSON file); 2 emir run --abstract (the top with the blocks black-boxed: equal to a flat run of the same chip to 5e-13); 3 budgets (IR per block = top + inside: ADC 4.00 + 7.50 = 11.50 mV, within 15 mV); 4 back-annotation (a PLL segment read 0.952× (WARN) with its pins held ideal, 1.005× (FAIL) with the top's voltages). Below, bars of the IR budget per block against 15 mV: ADC 11.50 mV, PLL 14.96 mV.
- **Footer:** examples/05_chip_level: a synthetic chip, offline, every number checked against hand calculation and a flat run. Screening, not sign-off.
- **Alt text:** Chip-level flow in four steps: block abstracts, the top with blocks black-boxed, IR budgets (ADC 11.50 mV, PLL 14.96 mV, both within 15 mV) and back-annotation turning a WARN into a FAIL.
- **Numbers:** `examples/05_chip_level/run.sh` and `measure.py` (golden values in `examples/05_chip_level/expected/measured.json`; the 15 mV budget is run.sh's `--budget-mv 15`).

## Page 9: the self-consistent loop

- **Kicker:** BIG BLOCKS: CLOSING THE LOOP
- **Title:** Feed each pin its own droop, then simulate again
- **Text:** --self-consistent re-simulates the CC testbench with every device pin fed its own droop waveform, until the droop the currents produce is the droop they were fed.
- **Visual:** the governing branch's peak error against the pass, the first panel of the waveform figure (`docs/images/selfconsist_waveform.png`) redrawn for the feed: the three 32-inverter meshes in blue, labelled "32 inverters, 132 to 1,632 nodes: +15 to +18 % → within 0.7 %, 4 or 5 simulations", the 128-inverter mesh in purple, "128 inverters, 4.1 % droop: +69.8 → +0.78 %, 8 simulations", each case's reported pass ringed; below, a highlighted card, "What it leaves: the grid's own capacitance": "Scored on the R-only grid, minor branches read within 0.77 % of RCC on 132 nodes, up to 11.3 % off on 1,632, and within 0.2 % of RCC without the grid's capacitors. Accurate, not conservative."
- **Body:** On grids this small the loop costs more than RCC: 31.3 s against a 1.9 s RCC simulation at 4.1 % droop. The pay-off on a big block is an extrapolation, not a measurement.
- **Footer:** Xyce, synthetic, serial: examples/06_big_block/results/selfconsist_waveform.csv, the governing branch of meshes with 2 fF of grid capacitance a node. Screening, not sign-off.
- **Alt text:** The self-consistent loop pass by pass: the governing branch's peak error falls from +15 to +18 % to within 0.7 % of RCC on three small synthetic meshes, and from +69.8 % to +0.78 % at 4.1 % droop; what it leaves is the grid's capacitance.
- **Numbers:** `examples/06_big_block/results/selfconsist_waveform.csv`, net VDD, cases k32_n10, k32_n20, k32_n40 and hd16b: `peak_gov` of the split testbench pass by pass (pass 0, the open loop, to the pass the run reports, `reported_pass`), `peak_minor_*` and `peak_minor_*_nc` at the reported pass, hd16b's `total_s` at its last pass and `rcc_sim_s`; the same table the waveform figure draws and the article quotes.

## Page 10: try it

- **Kicker:** TRY IT
- **Title:** Point your agent at the repository
- **Text:** It reads AGENTS.md and starts by proving the install.
- **Visual:** four commands: 1 `emir selftest` (proves the install, offline, in seconds); 2 `emir next --dspf ... --process ...` (the plan for what you have; you choose the mode); 3 `emir run ... --mode ... --summary-out s.json` (EM and IR on every net); 4 `emir report --from s.json` (what the agent tells you). Then: Agents: Claude Code, Codex, any MCP client; Simulators: Spectre, HSPICE, Xyce, ngspice (verify each first); Limits: pdks/ for open PDKs, or typed from your manual; Viewers: chart, HTML, Virtuoso, KLayout. A boxed note: "Screening, not sign-off. Foundry-qualified EM/IR remains the sign-off flow; EMIR says where to look first, and what it did not check. A research project, with no guarantee of accuracy: test and verify its results."
- **Footer:** Every number in these pages comes from a run in the repository (scripts/make_figures.py).
- **Alt text:** Four commands to start: emir selftest, emir next (you choose the mode), emir run, emir report; for Claude Code, Codex or any MCP client; limits from pdks/ or typed by hand. Screening, not sign-off.
- **Numbers:** none.
