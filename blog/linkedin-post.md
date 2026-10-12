# LinkedIn feed post

The short post that goes with the article. Paste the text between the markers as it
is: plain text, no Markdown (LinkedIn shows `**` and backticks literally).

- **Attach the carousel**: `blog/carousel.pdf` as a document post (5 pages, 1080 x 1350),
  document title "EMIR: EM/IR screening your AI agent can run". Or post the five PNGs
  `blog/images/carousel_01.png` .. `carousel_05.png` as a multi-image post, with the alt
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
EM/IR is usually the last check on an analog block, and sign-off is the most expensive place to find a via with one cut too few.

So I built EMIR: EM/IR screening your AI agent can run.

It checks every wire segment and via of a DSPF against your PDK's avg, rms and peak limits, computes the IR drop on each supply, and gives the fix in microns or cuts, numbered on a chart of the layout. Screening, not sign-off: it says where to look first, and what it did not check.

Built for an agent: a preflight and a playbook per task in AGENTS.md; emir next prints the commands to run; 19 commands answer in JSON, with 210 stable codes (errors, warnings and notes); an MCP server; graded evals.

Exact for the simulated record: EMIR reads every branch current of your RC simulation (rcc), so avg, rms and peak EM and static and dynamic IR all come from the simulated currents, and a loop residual checks that they fit the network. Big blocks need that RC simulation too in this release, and a faster route is planned. With no simulation yet, DC currents give a quick screen.

On SKY130, magic (with a public patch) extracts the network from the GDS. No readable EM data for your PDK? emir process new writes a fill-in file for the layers your DSPF uses, and every report says which limits were typed by hand.

Also: a Cadence flow (PVS and Quantus plans, Spectre save lines, a Virtuoso Explorer) and currents from Spectre, HSPICE, Xyce or ngspice. Each simulator must pass emir verify-sim on your own machine before EMIR relies on it; none of the licensed tools runs in the repository's tests.

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
| 19 commands, 210 stable codes | the package: `emir --help`, `agent/errors.md` (generated from `emir/errors.py`) |
