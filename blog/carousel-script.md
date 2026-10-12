# Carousel script

The text of each page of `blog/carousel.pdf` (and of `blog/images/carousel_01.png` ..
`carousel_05.png`, the same pages as images), as it appears on the page. Five pages,
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
- **Visual:** EMIR's EM chart of example 06's 10 x 10 power mesh (32 inverters, Xyce currents, rcc: every metric), the four failing straps at the feed corner outlined, two callouts, "#1 1.83× FAIL / widen 2.000 -> 3.660 um" and "#2 1.75× FAIL / widen 2.000 -> 3.510 um"; the legend naming every colour (ok below 0.8×, blue by ratio; warn 0.8× to 1×; fail 1× and over, or not a number; the violation marker); the flow agent -> emir next -> emir run -> emir report; "Screening, not sign-off."
- **Footer:** examples/06_supply_mesh: a synthetic 10 x 10 mesh, 32 inverters, Xyce currents, rcc (every metric); invented limits. Swipe for how it works. Screening, not sign-off.
- **Alt text:** EMIR, EM/IR screening your AI agent can run: an EM chart of a synthetic power mesh where four straps at the feed fail; the two that leave it, at 1.83x and 1.75x, carry their fixes: widen 2.000 to 3.660 and 3.510 um.
- **Numbers:** examples/06_supply_mesh's run.sh (Xyce on the mesh's `rcc.cir`, then `emir run` with `process_screen.yaml`), run again by `scripts/make_figures.py`; golden values in `examples/06_supply_mesh/expected/screen_n10.json`.

## Page 2: exact for the simulated record

- **Kicker:** EXACT FOR THE SIMULATED RECORD
- **Title:** Every segment, against your limits, with the fix
- **Text:** rcc reads every resistor's current from your RC simulation: each segment's avg, rms and peak, the IR drop, the fix. Here: where the supply enters the mesh.
- **Visual:** the feed corner zoomed (page 1 shows the whole mesh), its four failures labelled, #1 1.83×, #2 1.75×, #3 1.07× and #4 1.03× FAIL, with a 10 µm scale bar; the colour legend; below, two cards, "by hand" against "the run": Kirchhoff at the feed, 3.656 + 3.510 + 0.231 = 7.397 mA (#1 up, #2 right, the corner inverter) against the devices' average current, 7.397 mA; #1, the strap up from the feed, 3.656 mA / 2.0 µm / 1.0 mA/µm = 1.83× against 1.83× FAIL, fix: widen 2.000 -> 3.660 um.
- **Footer:** examples/06_supply_mesh, invented round-number limits; Xyce currents, rcc: every branch current read, every metric checked. Screening, not sign-off.
- **Alt text:** The mesh's feed corner, zoomed: four straps fail, at 1.83x, 1.75x, 1.07x and 1.03x; by hand, the currents leaving the feed add up to the devices' 7.397 mA, and 3.656 mA over 2.0 um at 1.0 mA/um is 1.83x.
- **Numbers:** as page 1; the branch currents are the run's own (its simulated averages; the devices' is the sum of their contacts), each average current is the golden ratio x 2.0 um x 1.0 mA/um.

## Page 3: agent first

- **Kicker:** AGENT FIRST
- **Title:** Built to be driven by an AI agent
- **Text:** Every command answers in JSON, every failure carries a code, and the tool itself says what to run next.
- **Visual:** three counts, 19 "commands, each with --format json", 210 "stable codes: branch on the code, not the text" (errors, warnings and notes), 26 "JSON schemas (emir schema)"; below, the scripted session: the `emir next` plan with its mode line as printed, shortened with an ellipsis ("mode: rcc -- currents from a simulation of the RC …"), then seven steps (the verification of Xyce among them), step 6 (`emir run`, verdict FAIL) and the `emir report` lines (verdict FAIL; IR drop VDD 28.068 mV, and the dynamic drop).
- **Footer:** The plan and its steps on examples/02_xyce, run as printed (scripted; rcc; Xyce currents, the bundled illustrative limits); long lines shortened (…). Screening, not sign-off.
- **Alt text:** 19 commands, 210 stable codes, 26 JSON schemas; a terminal where emir next plans seven steps, emir run gives FAIL, and emir report gives the verdict and the IR drop.
- **Numbers:** the counts from the package (`emir.cli.COMMANDS`, `emir.errors.REGISTRY`, `emir schema --list`); the session is example 02's plan with each step run exactly as `emir next` printed it, by `scripts/make_figures.py`, with Xyce.

## Page 4: SKY130, from a GDS

- **Kicker:** SKY130: FROM A GDS
- **Title:** Open PDK, open tools: magic extracts, rcc checks
- **Text:** magic, with the public geoout patch, writes every resistor's layer, width and place; Xyce simulates that RC netlist; EMIR reads every branch current.
- **Visual:** example 08's synthetic SKY130 cell as EMIR's chart panel draws it, magic's network over the layout on black, each element by its governing ratio; the two failures labelled, "#1 1.36× FAIL / 1 -> 2 cuts (+1)" (out's via2) and "#2 1.36× FAIL / 1 -> 2 cuts (+1)" (vss's via2); the colour legend; three tiles: 1135 "resistors in magic's network, every one read", 1.36× "via2 avg: one cut where two are needed", 6.69 mV "IR drop on out (4.84 mV on vss)".
- **Footer:** examples/08_open_layout: a synthetic SKY130 cell (four NMOS), magic with the geoout patch, Xyce, rcc; the limits of pdks/sky130.yaml; diffusion, poly and wells grey: no limit, not checked. Screening, not sign-off.
- **Alt text:** A SKY130 cell of four NMOS over its layout, magic's network screened with rcc: out and vss fail on their single via2 cut at 1.36x (fix: 1 -> 2 cuts); 1135 resistors read; IR drop 6.69 mV on out, 4.84 mV on vss.
- **Numbers:** examples/08_open_layout's rcc run (`emir extract --tool magic`, `emir simulate` with Xyce, then `emir run --period 4e-9`), run again by `scripts/make_figures.py`; golden values in `examples/08_open_layout/expected/results.json`.

## Page 5: try it

- **Kicker:** TRY IT
- **Title:** Your limits and tools, verified where they run
- **Text:** Point your agent at the repository: it reads AGENTS.md and proves the install first.
- **Visual:** three cards: Limits (pdks/ for SKY130, GF180MCU and IHP SG13G2, every value cited; or typed by hand from your design manual: emir process new, emir process check); Cadence (emir extract plans PVS LVS and Quantus; Spectre save lines; a Virtuoso Explorer highlights the worst segments, read-only); Simulators (Spectre, HSPICE, Xyce, ngspice: each passes emir verify-sim on your machine before EMIR relies on it). Then "Start": emir selftest -> emir next -> emir run -> emir report; "Agents": Claude Code, Codex, any MCP client (emir mcp); "Code": github.com/skycadeda/emir · MIT licence. A boxed note: "Screening, not sign-off. Foundry-qualified EM/IR remains the sign-off flow; EMIR says where to look first, and what it did not check. A research project, with no guarantee of accuracy: test and verify its results."
- **Footer:** Spectre, HSPICE, Quantus, PVS and Virtuoso need licences: none runs in this repository's tests, and each is UNVERIFIED until checked on site. Every number in these pages comes from a run in the repository (scripts/make_figures.py).
- **Alt text:** Your limits and tools: process files from pdks/ or typed by hand, a Cadence flow, each simulator verified on your machine; start with emir selftest, emir next, emir run, emir report. Screening, not sign-off.
- **Numbers:** none.
