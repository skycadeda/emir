# LinkedIn feed post

The short post that goes with the article. Paste the text between the markers as it
is: plain text, no Markdown (LinkedIn shows `**` and backticks literally).

- **Attach the carousel**: `blog/carousel.pdf` as a document post (10 pages, 1080 x 1350),
  document title "EMIR: EM/IR screening your AI agent can run". Or post the ten PNGs
  `blog/images/carousel_01.png` .. `carousel_10.png` as a multi-image post, with the alt
  texts in [carousel-script.md](carousel-script.md).
- **Cover**: the article's header is `blog/images/cover_article_1920x1080.png` (16:9);
  LinkedIn shows it when the article link is shared. `blog/images/cover_1200x627.png` is
  the same cover at 1.91:1, for a single-image post or a link preview. A post carries
  either the carousel or a link preview, so the carousel goes here and the article is
  linked in the text.
- **Before posting**: replace `ARTICLE-URL`, and go through the checklist in
  [README.md](README.md).

The first two paragraphs, under 210 characters, are the hook that shows before
"...see more". The whole post stays under LinkedIn's 3,000-character limit, with four
hashtags; `tests/test_blog.py` checks both.

<!-- post:start -->
```text
EM/IR is usually the last check on an analog block, and on an ADC or PLL top the RC simulation it needs often does not converge.

So I built EMIR: EM/IR screening your AI agent can run.

It checks every wire segment and via of a DSPF against your PDK's avg, rms and peak limits, computes the IR drop on each supply, and gives the fix in microns or cuts, numbered on a chart of the layout. Screening, not sign-off: it says where to look first, and what it did not check.

Built for an agent: a preflight and a playbook per task in AGENTS.md; emir next prints the commands to run; 21 commands answer in JSON, with 311 stable codes (errors, warnings and notes); an MCP server; graded evals. RCC or CC is your choice: the agent shows you the measured trade-offs and asks; it never picks.

Big blocks: simulate C-only, then push the device currents through the R-only grid. On a synthetic Xyce mesh of 132 to 40,032 nodes, the RCC simulation went from 0.64 s to 350 s; the whole CC route took 6.96 s. On the branch that decides the verdict, avg and IR came within 0.15 % of RCC; rms and peak read high, the safe side.

Closing the loop (re-simulating with each pin fed its own droop waveform) lands on the RCC answer for resistive loads, with the other rail ideal. On a 128-inverter mesh at 4.1 % droop it took the governing branch's peak from +69.8 % to +0.78 % of RCC in eight simulations. What it leaves is the grid's own capacitance, and on these small grids it costs more than RCC.

Big nets: a 92,001-node grid with 2,000 taps would need a 2.70 GiB dense matrix; EMIR streams it: 11.7 s, 804 MiB.

No readable EM data for your PDK (missing, or an encrypted ICT)? emir process new writes a fill-in file for the layers your DSPF uses, emir process check catches gaps and unit slips, and every report says which limits were typed by hand, and from which document.

Also: chip level, SKY130 limits from the public LEF, a Cadence flow, and currents from Spectre, HSPICE, Xyce or ngspice. Each simulator must be verified on your own machine (emir verify-sim) before EMIR relies on it; none of the licensed tools runs in the repository's tests.

Every number comes from a run in the repository (synthetic test structures); the article has the method and the sources. EMIR is a research project, provided as is, with no guarantee of accuracy: test and verify its results before relying on them. Open source, MIT licence.

If you run it on your PDK, tell me what broke; process files for other open PDKs are welcome as pull requests.

Repository: https://github.com/skycadeda/emir
Article: ARTICLE-URL

#AnalogDesign #EDA #AIAgents #Electromigration
```
<!-- post:end -->

## Where each number in the post comes from

| number | source |
|---|---|
| 21 commands, 311 stable codes | the package: `emir --help`, `agent/errors.md` (generated from `emir/errors.py`) |
| 132 to 40,032 nodes; RCC 0.64 s to 350 s; CC route 6.96 s | `examples/06_big_block/results/sweep.csv`, rows k32 (Xyce, synthetic, serial) |
| avg and IR within 0.15 % of RCC on the governing branch; rms and peak high | the same file, N = 10 to 70 (avg -0.12 to -0.15 %, IR -0.13 to -0.15 %; rms +6.83 to +13.68 %, peak +15.28 to +24.29 %) |
| 92,001 nodes, 2,000 taps, 2.70 GiB; 11.7 s, 804 MiB | `examples/06_big_block/results/stream_bench.csv`, row s300_k2000_t501 (the `auto` columns) |
| a 128-inverter mesh at 4.1 % droop, peak +69.8 % to +0.78 % of RCC in eight simulations; costs more than RCC | `examples/06_big_block/results/selfconsist_waveform.csv`, case hd16b (128 inverters): `peak_gov` of the split testbench's pass 0 (the open loop) and of the pass the run reports (pass 7, settled, so eight simulations); the loop's `total_s` 31.3 s against `rcc_sim_s` 1.9 s; the same numbers the waveform figure prints |
| what it leaves: the grid's own capacitance | the same file: the minor branches' peak at the reported pass against RCC and against RCC without the grid's capacitors (`peak_minor_*`, `peak_minor_*_nc`) |
| lands on the RCC answer for resistive loads, with the other rail ideal | `examples/12_self_consistent/expected/summary.json` (IR 100.050 mV self-consistent, 100.050 mV RCC and by hand) |
