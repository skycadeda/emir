# The LinkedIn launch: article, post, carousel

Everything needed to announce EMIR on LinkedIn. Nothing here is published yet: LinkedIn
is posted by hand, after the checklist at the end.

| file | what |
|---|---|
| [`linkedin-article.md`](linkedin-article.md) | the long-form article (about 1,800 words, 8 images) |
| [`linkedin-post.md`](linkedin-post.md) | the feed post that announces it, with the carousel |
| [`carousel-script.md`](carousel-script.md) | the text, alt text and number sources of each carousel page |
| `carousel.pdf` | the carousel: 10 pages, 1080 x 1350, vector text |
| `images/carousel_01.png` .. `carousel_10.png` | the same pages as PNGs |
| `images/cover_article_1920x1080.png` | the article's header (16:9) |
| `images/cover_1200x627.png` | the same cover at 1.91:1, for a single-image post or a link preview |

The other images are the documentation's own, in `docs/images/`. Every image is drawn by
`scripts/make_figures.py` from runs of this repository (`docs/images/README.md` says
which run). Screening, not sign-off: every page and image that shows a result says so.

## Order

1. Publish the article.
2. Put its address into the post (`ARTICLE-URL`) and publish the post with the carousel.

## Publishing the article

In LinkedIn: **Write article**.

1. **Header image**: upload `images/cover_article_1920x1080.png`. Current guides give
   the article header as 16:9 (1920 x 1080); this file is exactly that, so nothing should
   be cropped. (A 16:9 crop of the 1.91:1 `cover_1200x627.png` cuts its source line.)
   Check the editor's preview: LinkedIn's own crop was not tried.
2. **Title**: the article's first line, "EM/IR screening for analog layout, built for your AI
   agent".
3. **Text**: paste section by section. Each `##` line is a heading (the editor's heading
   style). Paste the text without the Markdown marks: LinkedIn shows backticks and
   asterisks as they are, so `emir next` becomes emir next, and `**One entry point.**`
   becomes bold with the editor's bold button.
4. **The install block** under "Try it": the editor's code-snippet block if it offers one,
   otherwise three plain lines.
5. **Images**: insert each where the article has it, in this order, with its alt text
   (the image's "Alt text" field) and its caption (the caption field under the image):

| # | section | file | alt text | caption |
|---|---|---|---|---|
| 1 | header | `blog/images/cover_article_1920x1080.png` | EMIR: EM / IR-drop screening your AI agent can run, beside EMIR's chart of a synthetic power mesh on black: four straps at the feed fail, and the two that leave it carry their fixes. | A synthetic power mesh, invented limits (examples/06_big_block). |
| 2 | What EMIR is | `docs/images/flow.png` | Flow diagram: an AI agent drives four stages, extraction (RC, C-only, R-only), simulation (Spectre, HSPICE, Xyce, ngspice), emir (rcc, cc-grid, streaming) and the report (JSON, HTML, Virtuoso Explorer, KLayout). | How EMIR fits a flow. |
| 3 | Built for an agent first | `docs/images/agent_transcript.png` | Terminal: emir next, given the person's choice of cc-grid, prints a seven-step plan, the verification of Xyce on this machine among them; each step runs as printed; emir report prints a FAIL verdict and the fix, 1 -> 2 cuts. | emir next's plan for examples/02_xyce, each step run as printed (scripted, not a model). |
| 4 | Big blocks | `docs/images/cc_vs_rcc_scaling.png` | Three charts on a synthetic mesh: the RCC simulation time and its result file grow steeply with the mesh, the CC route and its result stay small, and the CC route's speed-up grows with the mesh. | RCC against CC + cc-grid. Read the trends, not the absolute times. |
| 5 | Big blocks | `docs/images/accuracy.png` | Range plot of cc-grid error against RCC per metric: averages and IR within a fraction of a percent; rms and peak conservative on the governing branch but not on every branch. | cc-grid against RCC, branch by branch. |
| 6 | Closing the loop | `docs/images/selfconsist_waveform.png` | Three charts on synthetic inverter meshes: the governing branch's rms and peak error against the pass, falling from up to +70 % to within 1 % of RCC; the residual that stops the loop; what remains, the minor branches' error, widening with the grid. | The waveform loop on switching inverters (Xyce, synthetic, serial). |
| 7 | Streaming | `docs/images/streaming_memory.png` | Five synthetic grids: the dense transfer matrix of the largest exceeds the 2 GiB default --max-memory; the streamed path keeps peak memory low and is faster on the large cases. | Dense against streamed: the same currents, in bounded memory. |
| 8 | Chip level | `docs/images/chip_level.png` | Chip-level diagram: two block abstracts, the top-level network with the ADC and PLL black-boxed and their pin voltages, the IR budget per block, and a PLL segment that turns from WARN to FAIL when back-annotated with the top's pin voltages. | Chip-level EM/IR, hierarchically (synthetic, offline). |

The article uses the light variants (LinkedIn's default theme is light); each has a
`_dark` twin in `docs/images/` except the transcript, which is dark only.

6. **Links**: the repository is `https://github.com/skycadeda/emir`. File names in the
   text (examples/06_big_block/results, pdks/sky130.yaml) may be linked to the
   repository, e.g. `https://github.com/skycadeda/emir/tree/main/examples/06_big_block`.
7. **Last line**: the trademark note, in italics.
8. Preview on a desktop and a phone before publishing.

## Publishing the post

The text is between the markers in [`linkedin-post.md`](linkedin-post.md); paste it as it
is. Then either:

- **a document post**: attach `carousel.pdf`, title "EMIR: EM/IR screening your AI agent
  can run" (a document post takes no alt text per page; the post text carries the
  content), or
- **a multi-image post**: attach `images/carousel_01.png` .. `carousel_10.png` in order,
  each with its **Alt text** line from [`carousel-script.md`](carousel-script.md). Each
  is under 250 characters; guides quote LinkedIn's alt-text limit as 300 or 1,000
  characters (not checked).

The hook is the first two paragraphs, under 210 characters, which guides give as the
desktop fold before "...see more". They put the phone fold nearer 140 characters; the
first sentence (128 characters) carries the problem on its own.

## Checklist before publishing

- [ ] **Your sentence.** The opening has a slot for one sentence from your own
      experience (an HTML comment under the first paragraph: a block where a late EM/IR
      fix cost you). Write it, or delete the comment; no one else should write it.
- [ ] **Licence.** `LICENSE` is the MIT License ("Copyright (c) 2026 Vijay Gupta and
      contributors"; the maintainer's choice, 2026-10-02). Carousel page 1 says
      "OPEN-SOURCE": true once the repository is public.
- [ ] **Disclaimer.** The article and the post each say once that EMIR is a research
      project, provided as is, with no guarantee of accuracy, and that testing and
      verifying its results is the reader's responsibility.
- [ ] **Repository.** Public at its final address. `REPO_URL` in `emir/repo.py` set to it,
      and every file that repeats it updated (`tests/test_repo_url.py` lists them and
      fails until they agree): the article, the post, the issue forms, `mkdocs.yml`.
      `ARTICLE-URL` replaced in the post.
- [ ] **What the article names exists on the published branch**: AGENTS.md, the playbooks
      and the 16 eval cards under `agent/`, `emir mcp`, `emir verify-sim`, `emir process
      new` and `check`, `--self-consistent`, `--dynamic`, the docs, and examples 02, 05,
      06, 12 and 13.
- [ ] **The magic patch.** The article, page 4 and the flow figure say magic runs the
      extraction for SKY130 with the `geoout` patch. Check
      https://github.com/RTimothyEdwards/magic/pull/562: still open, the patch is the fork
      branch's (`docs/open-source-flow.md` says how to build it); merged, name the magic
      release that has it.
- [ ] **Product names.** The article, the flow figure and the carousel name Cadence
      products as integration targets; the article ends with a trademark note.
- [ ] **What is unverified is said so.** No licensed tool (Spectre, HSPICE, Virtuoso,
      PVS, Quantus) runs here, and the article, the post and the carousel claim none as
      verified: each must be verified on site by the user's agent (`emir verify-sim` for
      the simulators, the self-consistent loop's Spectre and HSPICE routes included), and
      is UNVERIFIED until then. Never write that one "is verified". Codex, Cursor and Claude
      Desktop appear on carousel page 10 and in the docs; their MCP configurations follow
      the clients' published formats and were not run (Claude Code was).
- [ ] **Known issues.** `docs/limits.md` lists what EMIR leaves out, and no open bug. If
      a bug is listed there, fix it or accept it: an agent following "Try it" may meet it.
- [ ] **Numbers current.** `python scripts/make_figures.py --check` and
      `pytest tests/test_blog.py tests/test_make_figures.py` pass. If a result or a figure
      changed, update the numbers in the article and the post; the test names the one
      that drifted.
- [ ] **Previewed** on a desktop and a phone: the header image, the image order, the code
      block, the hook before "...see more".

## Where each number in the article comes from

`tests/test_blog.py` recomputes every one of these from its source, except the three
marked "not checked".

| number in the article | source |
|---|---|
| "Metal1: widen 2.000 -> 3.655 um" | `examples/06_big_block/expected/screen_n10.json` (run.sh step 4: the worst item's ratio, 1.82741, on its 2.000 um strap; the fix rounds 2.000 x 1.82741 up to the next 5 nm) |
| 21 commands, 30 JSON schemas, 311 stable codes (errors, warnings, notes) | the package: `emir.cli.COMMANDS`, `emir.schemas.SCHEMAS`, `emir.errors.REGISTRY` |
| sixteen task cards | `agent/evals/cards/` (checked when the directory is present) |
| 32 inverters; 132, 1,632, 4,932 and 40,032 nodes | `examples/06_big_block/results/sweep.csv`, rows k32 |
| RCC simulation 0.64 s to 350 s; CC route 6.96 s | the same rows: `rcc_sim_s`; `cc_sim_s` + `cc_post_s` at N = 200 |
| 9.52 MiB to 1,298 MiB; 1.41 MiB | `rcc_raw_MB`, `cc_raw_MB` |
| 2.0x, 16.9x, 32.4x; 50.3x | `speedup_route` at N = 10, 40, 70; `speedup_sim_vs_cc_route` at N = 200 |
| 10 to 100 times slower | the maintainer's experience, labelled so (not checked) |
| avg and IR 0.12 to 0.15 % low; rms 6.83 to 13.68 %; peak 15.28 to 24.29 % | `avg_gov`, `ir_err`, `rms_gov`, `peak_gov`, rows k32 N = 10 to 70 |
| peak 18.95 % below RCC on minor branches | `peak_minor_min`, same rows |
| eight times the devices, 4.1 % droop, peak 79.90 % high | row k256: 256 against 32 devices, `ir_rcc_mV` on the 1 V rail of `mesh.py`, `peak_gov` |
| ±2 %, about 2 % droop | the guard band the docs derive from these rows (`docs/big-blocks.md`, "Measured accuracy, and the guard band"; not checked) |
| 25 to 52 % high, decap-aware 0.6 to 5.3 %; straps 5.52 % low | `examples/13_decap_aware/results/decap.csv`: `free_rms_gov` and `aware_rms_gov` of the single-rail cases with decaps (`decaps` > 0, `two_rail` False); the decap-free straps' `free_rms_strap_*` (-100 %); the two-rail case's `vdd_aware_rms_strap_min` |
| peak from 15 to 18 % high to within 0.7 % of RCC, four or five simulations, 0.38 to 0.55 % droop | `examples/06_big_block/results/selfconsist_waveform.csv`, the 32-inverter meshes (k32_n10, k32_n20, k32_n40), net VDD: `peak_gov` of the split testbench's pass 0 (the open loop) and of the pass the run reports (`reported_pass`; pass 0 is a simulation), `droop_pct` |
| on a smaller mesh at the same 4.1 % droop (128 inverters, 384 nodes), from +69.8 % to +0.78 % in eight | the same file, case hd16b (`grid_nodes` 384): pass 0 and the reported pass 7 (`stopped`: settled); the waveform figure prints the same. The 4.1 % open-loop case of page 7 and of "eight times the devices" is sweep.csv's k256 (256 inverters, `grid_nodes` 5156), which the loop's table does not hold |
| within 0.77 % on 132 nodes, up to 11.3 % off on 1,632, within 0.2 % without the grid's capacitors | the same file, the 32-inverter meshes at the reported pass: `peak_minor_min`, `peak_minor_max`, and the same against RCC without the grid's capacitors (`*_nc`) |
| 31.3 s, 1.9 s | the same file, case hd16b: `total_s` at the last pass, `rcc_sim_s` |
| 108.000 mV, 100.050 mV, 100.050 mV | `examples/12_self_consistent/expected/summary.json`: `ir_drop_mV` of `runs.open`, `runs.sc`, `runs.rcc` |
| 92,001 nodes, 2,000 taps, 2.70 GiB | `examples/06_big_block/results/stream_bench.csv`, row s300_k2000_t501 |
| 11.7 s, 804 MiB; 61.2 s, 4,593 MiB; 7e-15 | the same row: `auto_*`, `dense_*`, the largest `rel_diff_*` |
| 5e-13 | `examples/05_chip_level/measure.py`, `top_vs_flat_max_rel` (4.94e-13, rounding noise; not checked here, the example's test holds it to 1e-9) |
| 4.00 + 7.50 = 11.50 mV; 14.96 mV; 14.81 mV | `examples/05_chip_level/expected/`: `top.summary.json`, `adc.summary.json`, `measured.json` |
| 0.952x (WARN), 1.005x (FAIL) | `examples/05_chip_level/expected/pll.summary.json`, `pll.annotated.summary.json` |

The post's numbers are a subset; [`linkedin-post.md`](linkedin-post.md) lists them with
their sources, and the carousel's are in [`carousel-script.md`](carousel-script.md).
