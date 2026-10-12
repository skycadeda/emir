# The LinkedIn launch: article, post, carousel

Everything needed to announce EMIR on LinkedIn. Nothing here is published yet: LinkedIn
is posted by hand, after the checklist at the end.

| file | what |
|---|---|
| [`linkedin-article.md`](linkedin-article.md) | the long-form article (about 1,400 words, 6 images) |
| [`linkedin-post.md`](linkedin-post.md) | the feed post that announces it, with the carousel |
| [`carousel-script.md`](carousel-script.md) | the text, alt text and number sources of each carousel page |
| `carousel.pdf` | the carousel: 5 pages, 1080 x 1350, vector text |
| `images/carousel_01.png` .. `carousel_05.png` | the same pages as PNGs |
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
| 1 | header | `blog/images/cover_article_1920x1080.png` | EMIR: EM / IR-drop screening your AI agent can run, beside EMIR's chart of a synthetic power mesh on black: four straps at the feed fail, and the two that leave it carry their fixes. | A synthetic power mesh, invented limits (examples/06_supply_mesh). |
| 2 | What EMIR is | `docs/images/flow.png` | Flow diagram: an AI agent drives four stages, extraction (RC, R-only, magic for SKY130), simulation (Spectre, HSPICE, Xyce, ngspice), emir (rcc, the DC screen) and the report (JSON, HTML, Virtuoso Explorer, KLayout). | How EMIR fits a flow. |
| 3 | Built for an agent first | `docs/images/agent_transcript.png` | Terminal: emir next prints a seven-step plan, the verification of Xyce on this machine among them; each step runs as printed; emir report prints a FAIL verdict, the worst item a contact at 1.042x on its peak with its fix, and the IR drop. | emir next's plan for examples/02_xyce, each step run as printed (scripted, not a model). |
| 4 | Exact: every branch current, every metric | `blog/images/carousel_02.png` | The cover's mesh zoomed at its feed corner: four straps fail, at 1.83x, 1.75x, 1.07x and 1.03x; by hand, the currents leaving the feed add up to the devices' 7.397 mA, and 3.656 mA over 2.0 um at 1.0 mA/um is 1.83x. | The feed corner of the cover's mesh, checked by hand (examples/06_supply_mesh, invented limits). |
| 5 | From a GDS on SKY130 | `docs/images/example08_sink.png` | EM and IR charts of example 08's SKY130 NMOS array over its layout, from magic's network: out and vss FAIL on their single via2 cut (1.36x avg, fix 1 -> 2 cuts); the diffusion, poly and well under the devices drawn grey, not checked. | Example 08: magic's network, simulated with Xyce and screened with rcc (synthetic layout, the SKY130 limits of pdks/sky130.yaml). |
| 6 | Cadence, and verification on site | `docs/images/html_report.png` | Screenshot of EMIR's per-net HTML report of an rcc run: verdict FAIL, the worst EM item a contact at 1.042x of its peak limit, the net drawn on black as a vector map coloured by each element's EM ratio, its legend, coverage per metric and the violations table. | The HTML report of examples/02_xyce (Xyce currents, illustrative limits). |

The article uses the light variants (LinkedIn's default theme is light); each has a
`_dark` twin in `docs/images/` except the transcript, which is dark only, and the
carousel page, which is dark.

6. **Links**: the repository is `https://github.com/skycadeda/emir`. File names in the
   text (examples/08_open_layout, pdks/sky130.yaml) may be linked to the
   repository, e.g. `https://github.com/skycadeda/emir/tree/main/examples/06_supply_mesh`.
7. **Last line**: the trademark note, in italics.
8. Preview on a desktop and a phone before publishing.

## Publishing the post

The text is between the markers in [`linkedin-post.md`](linkedin-post.md); paste it as it
is. Then either:

- **a document post**: attach `carousel.pdf`, title "EMIR: EM/IR screening your AI agent
  can run" (a document post takes no alt text per page; the post text carries the
  content), or
- **a multi-image post**: attach `images/carousel_01.png` .. `carousel_05.png` in order,
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
      and the 11 eval cards under `agent/`, `emir mcp`, `emir verify-sim`, `emir process
      new` and `check`, the docs, and examples 02, 03, 06 and 08.
- [ ] **The magic patch.** The article, page 4 and the flow figure say magic runs the
      extraction for SKY130 with the `geoout` patch. Check
      https://github.com/RTimothyEdwards/magic/pull/562: still open, the patch is the fork
      branch's (`docs/process-files.md` says how to build it); merged, name the magic
      release that has it.
- [ ] **Product names.** The article, the flow figure and the carousel name Cadence
      products as integration targets; the article ends with a trademark note.
- [ ] **What is unverified is said so.** No licensed tool (Spectre, HSPICE, Virtuoso,
      PVS, Quantus) runs here, and the article, the post and the carousel claim none as
      verified: each must be verified on site by the user's agent (`emir verify-sim` for
      the simulators), and is UNVERIFIED until then. Never write that one "is verified".
      Codex appears on carousel page 5, and Cursor and Claude Desktop in the docs; their MCP
      configurations follow the clients' published formats and were not run (Claude Code
      was).
- [ ] **What it leaves out.** `docs/method.md` ("What EMIR leaves out") lists it, and no
      open bug. If a bug is listed there, fix it or accept it: an agent following "Try
      it" may meet it.
- [ ] **Numbers current.** `python scripts/make_figures.py --check` and
      `pytest tests/test_blog.py tests/test_make_figures.py` pass. If a result or a figure
      changed, update the numbers in the article and the post; the test names the one
      that drifted.
- [ ] **Previewed** on a desktop and a phone: the header image, the image order, the code
      block, the hook before "...see more".

## Where each number in the article comes from

`tests/test_blog.py` recomputes every one of these from its source, except those marked
"not checked".

| number in the article | source |
|---|---|
| "Metal1: widen 2.000 -> 3.660 um" | `examples/06_supply_mesh/expected/screen_n10.json` (run.sh: the worst item's ratio, 1.827832, on its 2.000 um strap; the fix rounds 2.000 x 1.827832 up to the next 5 nm) |
| 19 commands, 26 JSON schemas, 210 stable codes (errors, warnings, notes) | the package: `emir.cli.COMMANDS`, `emir.schemas.SCHEMAS`, `emir.errors.REGISTRY` |
| 11 task cards | `agent/evals/cards/` (checked when the directory is present) |
| 1.042x, 0.847x, 28.07 mV, 90.0 mV | `examples/02_xyce/expected/summary.json` (the rcc run: `Rc3:peak`, `Rm1:avg`, `ir_drop_mV`, `dynamic_mV`) |
| Xyce and ngspice agree to 1.1e-4 | `scripts/opensim_proof.py` (`docs/simulators.md`; not checked here) |
| 1.83x, 1.75x, 1.07x, 1.03x; 3.656 + 3.510 + 0.231 = 7.397 mA | the carousel's page 2, drawn from the screen of `examples/06_supply_mesh` (`scripts/make_figures.py`) |
| 1135 resistors; 1.36x, via2: 1 -> 2 cuts | `examples/08_open_layout/expected/results.json` |
| more than 2 % (the last two periods) | `emir/waveform.py` (`TOL`), the rule in `docs/method.md` |

The post's numbers are a subset; [`linkedin-post.md`](linkedin-post.md) lists them with
their sources, and the carousel's are in [`carousel-script.md`](carousel-script.md).
