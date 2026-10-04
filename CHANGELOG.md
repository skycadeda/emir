# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

The public repository, built from the 1.0.0 release (version `1.1.0.dev0`).
The bundled sample is new and fully invented (see Changed), so `emir selftest`
now expects a 1.667x worst ratio, a 54.0000 mV IR drop and 10 violating
segments; the hand calculation is written out in `emir/selftest.py`.

### Security

- Names read from input files reach file names only as one safe component
  (`emir.output.safe_component`), checked to lie inside their directory
  (`output.contained`, EMIR-E701): a DSPF's `*|DESIGN "../../x"` or an
  absolute design, a GDS top cell `../../c`, a hierarchical net `XI0/VDD` and a
  process-file layer name no longer write outside `--html` or `--out` (a
  hierarchical net's report also reaches the index again).
  `tests/test_security.py`.
- `emir extract --tool magic`: magic's script names only fixed, safe words: the
  layout is linked in as `input.gds`, and a cell's name must be one plain word (new
  EMIR-E243), so a GDS cell named `x[exec ...]` cannot run that command through
  magic's Tcl. A `pdk:` first word that is not a plain directory name is refused
  (EMIR-E641); the block's name in a probed netlist must be one SPICE word
  (EMIR-E243). A process file may name only a magic program on `$PATH`, by its bare
  name (`magic: binary:`; a path or another program is refused, new EMIR-E648). magic
  runs in `magic/` under `--out`, EMIR's own directory; the layout it reads is never
  removed, and a `<cell>.dspf` or `<cell>.cc.spice` EMIR did not write, or a `magic/`
  that is not EMIR's, is never overwritten (new EMIR-E646), over MCP as well.
- The Quantus command file (`emir extract`, `agent/cadence/quantus/
  render_ccl.py`) refuses a cell name or a path with a quote, bracket, brace,
  `$`, backslash or line break (EMIR-E513).
- The HTML index links its rows through a `data-href` attribute and one
  listener: a design name with a quote ran script from the row's `onclick`.
- A site file in the working directory is no longer read by itself (`emir
  info` ran the `magic:` it named): `$EMIR_SITE`, `--site`, `~/.emir/site.yaml`
  or the install's `emir_site.yaml` only.
- `emir mcp`: a tool whose command can write where the caller says (save-lines,
  check, probe, selftest, validate ...) is no longer annotated read-only
  or idempotent, and is marked destructive; any output parameter that names an
  existing file outside the server's working directory needs `confirm`
  (EMIR-E590), as does simulating a deck (or its includes, or the `.spiceinit`
  beside it) that holds an ngspice `.control` block or a `shell` line.
  `emir simulate` never overwrites an existing `.spiceinit` with other content.
  EMIR-W660 said ngspice `-b -r` does not run a `.control` section; it does
  (checked with ngspice 45): the warning and the docs say so.
- `scripts/leak_guard.py` carries public rules (site paths, licence servers
  and variables, closed-licence strings, PDK file types) and reads a site's own
  rules from a private rule file outside the repository (`$EMIR_LEAK_PRIVATE`,
  or a git-ignored `.leak-guard-private`; CI writes it from the
  `EMIR_LEAK_PRIVATE` secret); a private rule can be limited to some files. The
  guard also reads a PDF's text (`pdftotext`, inflated streams), and `--commit
  REV` checks a commit's files and message (CI runs it on every new commit; a merge
  commit's files are those that differ from any of its parents).
- The offline bundle ships the licence texts of what its standalone Python
  links (OpenSSL, ncurses, libffi, Tcl/Tk, bzip2, xz, libedit, zlib, SQLite ...;
  from the same python-build-standalone build's checksummed full archive) and a
  source pointer for the GCC runtime libraries; a missing LICENSE, NOTICE or
  THIRD_PARTY_NOTICES.md stops the build. THIRD_PARTY_NOTICES.md names the
  third-party code and the open-PDK data in `pdks/`; `NOTICE` points to it.

### Added

- Example 06's `run.sh` step 4 screens its N = 10 mesh through the self-consistent
  loop with invented limits (`process_screen.yaml`, golden `expected/screen_n10.json`):
  the four straps nearest the feed fail. The banner, the blog's covers and carousel
  pages 1 and 2 are drawn from it.
- **The magic route: `emir extract --tool magic`.** On an open PDK, magic extracts a
  GDS's cell with every resistor kept as one drawn segment, with its layer, width,
  length and place. That needs a magic built with the public `geoout` patch
  (`extresist geoout on`: branch `extresist-geoout` of
  https://github.com/codervijay/magic, upstream as
  https://github.com/RTimothyEdwards/magic/pull/562). EMIR finds one (`--magic`,
  `$EMIR_MAGIC`, the process file's `magic: binary:`, the site file's `magic:`,
  `magic-8.3.515-geo` and `magic` on `$PATH`) by asking each `extresist geoout`, and
  runs it in `magic/` under `--out` (a directory of its own) with a fixed-name link to
  the layout, on the cell flattened (`flatten -dotoplabels`: a hierarchical layout
  extracts as the flat one would). It writes `<cell>.dspf` (each resistor
  with `$layer $W $L $X $Y $X2 $Y2` and its rectangle, or a via's `$cuts` and `$N`;
  `*|P` per pin, a port's extra pins tied by 0 V sources; `*|I` per device terminal,
  named from the process file; the capacitors counted once and spread over their
  nets' nodes, on the split netlist's own nets: a net with no label keeps its
  capacitance) and `<cell>.cc.spice`, the CC netlist with the same instance names.
  A wire on a diagonal shape, whose width magic gives as its tiles' extent, is named
  (EMIR-W647) and `emir run` lists it under not checked (`diagonal_wires`). For a
  DSPF it wrote, `emir next`'s choose-mode quotes what each route measured on
  example 08, its gate net included.
  `emir nets`, `pins`, `check`, `run`, `simulate` and `probe` read them as they are.
  The DSPF writer and the geometry reader are ported, with credit, from the
  Skycadeda Analog Framework (THIRD_PARTY_NOTICES.md). `emir next --gds G --cell C`
  plans it. Over MCP, a layout above `magic_gds_mb` (20 MB) needs the person's
  go-ahead. New codes: EMIR-E643 (no magic writes geometry, exit 3), E644, E645,
  E646, E647, E648, W643, W644, W645, W646, W647; EMIR-E243, E640, E641, E642 and
  W640 now belong to this route.
- **The process file's `magic:` section**: magic's layer names mapped onto the file's
  own (`layers`), each device model's terminal names (`devices`, globs allowed), and
  the magic to try (`binary`). `pdks/sky130.yaml` has it, every layer cited from the
  PDK's magic technology file and the MOSFETs' terminals from its SPICE library;
  `pdks/TEMPLATE.yaml` documents it.
- **`emir run --gds G`** draws the layout under the `--html` charts (`--cell` names
  its cell). `emir next --gds` passes it on to the planned run.
- **Example 08 on the magic route**, on the same synthetic SKY130 layout. rcc is
  checked branch by branch against the simulator's own output (440 branches, 6e-12).
  The open and self-consistent cc-grid loops are scored against rcc, branch by
  branch, and the operating point is compared with what the removed layout command
  reported. A test cell's committed magic outputs (`tests/data/magic_tiny`) let CI
  write and check the DSPF without magic; the tests marked `magic` run a real patched
  magic when there is one. The eval card `gds-magic-plan` covers playbook 09.

- **HSPICE.** `emir save-lines --sim hspice` prints `.option probe post=2
  post_version=2001` and the `.probe tran` lines a run needs (`i(xi0.rm1)` for a
  branch; `i1`..`i4` for a MOSFET's, BJT's or JFET's terminals; `i1` / `i2` for a
  two-terminal element's pins; the 0 V probes of `emir probe` for subcircuit pins),
  with `--prefix` for an instantiated block, and the names sidecar that maps HSPICE's
  lower-case names onto the DSPF's with their signs. HSPICE's manuals draw a MOSFET's
  i3 (a BJT's emitter current) out of the device and a run was not seen to confirm
  it, so every terminal of such a device is saved and the reader sets that sign from
  KCL (EMIR-I465; EMIR-W465 when the terminals do not close).
  `emir simulate --sim hspice|spectre` writes the deck and prints the exact command;
  `--run` runs the licensed simulator (over MCP: `confirm`), Spectre through a deck
  that includes the testbench and a file of save statements (`-format psfascii`).
  `--sim-args` passes options such as Spectre X's `+postlpreset=off`.
  `emir info` finds `hspice` (`$HSPICE_BIN`, the site file, `$PATH`; licensed).
  `emir next --sim spectre|hspice|xyce|ngspice` chooses the simulator; the HSPICE route
  is planned without `--run`, then with it (`needs_human`).
- **Readers for HSPICE ASCII (`.trN`, formats 9007, 9601 and 2001) and CSDF**
  (`emir/hspice.py`, `emir/csdf.py`), recognised by their headers, into the same
  `Waves` the other readers return: the 80-column header record joined without
  added blanks (names that straddle a line), fixed-width values whose rows do not
  line up with lines, the 1e30 table end, sweep tables (`--analysis sweep:N`, else
  EMIR-E465), 9007's 16-character names (EMIR-W466), single precision (EMIR-I464).
  A binary HSPICE result is refused (EMIR-E463: `.option post=2`); malformed files
  are EMIR-E464 and EMIR-E466, a non-transient result EMIR-E467. Writers for both
  layouts serve the tests (round trips over random tables) and example 11. Tested
  on the format research's synthetic samples (`tests/data/hspice/`), a real Xyce
  CSDF file, and, privately, real third-party HSPICE ASCII files (every complete
  transient file read). `tests/test_simulators.py`.
- **On-site verification: `emir verify-sim --sim spectre|hspice|xyce|ngspice [--run]`.**
  Support for a simulator is proven on the machine that uses it: a testbench with a
  hand-checkable answer (the sample rail, ideal current-source loads, DC and pulsed,
  R and RC) goes through the same route a run takes (`emir simulate`), is read back,
  screened in rcc and cc-grid and compared with the analytic answer and between the
  modes (154 checks; "Simulator routes and their verification" below has the details). The record (`emir.verify_sim/1`) is kept in `$EMIR_STATE_DIR`
  (else `~/.emir`) `verified.json` (`emir.verified/1`), with the simulator's version
  from its own version flag, the format, the emir version and the date (the host
  name only with `--record-host`). Licensed simulators are planned unless `--run`
  (EMIR-I661); a failure exits 70 (EMIR-E663). Every run whose currents come from a
  simulator result now carries `simulator_verification` (verified, not verified,
  unknown simulator or not applicable, with the record) in its summary, EMIR-W464
  ("simulator route not verified on this machine: run emir verify-sim --sim X") when
  no passing record of that simulator, version and format exists, and `emir report`
  says so next to the verdict. The simulator is read from the result's header, else
  its names sidecar (which `emir simulate` and `save-lines` now write it into), else
  `emir run --sim`. `emir next` plans the verification before the first run that
  uses a simulator (`needs_human` for Spectre and HSPICE); `emir info` shows each
  simulator's newest record. Verified for real here with Xyce and ngspice;
  HSPICE and Spectre with stand-in executables only (`tests/_sim_fakes.py`).
  `tests/test_verify_sim.py`.
- The headless SKILL self-test, `python agent/cadence/python/virtuoso_batch.py
  --selftest`: batch Virtuoso loads `emir_main.il` and checks its procedures, the
  check before `agent/cadence/LIVE_TEST.md`.
- `docs/simulators.md`: what each simulator supports, what is tested here and how,
  and the on-site verification. Example 11 (`examples/11_hspice_offline`): the HSPICE
  route on synthetic `.tr0` files and their CSDF twins (HSPICE is not run). The eval
  card `first-spectre-run`: plan `emir verify-sim` and ask before running Spectre.
- The research-project disclaimer ("EMIR is a research project, provided as is, with
  no warranty or guarantee of accuracy. It is your responsibility to test and verify
  its results for your process, tools and design before relying on them."): README,
  docs, AGENTS.md (relayed with every result), the skill, every HTML footer, the run
  summary (`research_disclaimer`, with the layout, mesh and abstract documents),
  `emir report`, the console verdict, the MCP server's instructions, the blog.

- `--dspf FILE` is accepted as the DSPF on every command that takes it as its
  first argument (`run`, `check`, `nets`, `pins`, `save-lines`, `abstract`):
  `emir next` takes the DSPF as `--dspf`, and agents carried the option over to
  the commands it prints. Two different files, one each way, are refused
  (EMIR-E507); a command that reads no DSPF names the ones that do
  (`details.did_you_mean`); a missing DSPF says it is the first argument. The
  positional stays the documented form (help, MCP tools, `emir next`), and
  AGENTS.md says so. `tests/test_dspf_alias.py`.
- `tests/test_repo_url.py`: every reference to the repository is `REPO_URL` of
  `emir/repo.py` (the one constant: https://github.com/skycadeda/emir). The
  issue forms' links, `mkdocs.yml` (`repo_url`, and `site_url`
  https://skycadeda.github.io/emir/, where the docs workflow deploys),
  `pyproject.toml` (`[project.urls]`), the clone commands of CONTRIBUTING.md
  and docs/getting-started.md and the blog use it.
- New warnings EMIR-W315 (no `jmax_factor` table and an analysis temperature
  above `em_tref`: the avg limits are used as quoted at the cooler temperature,
  which is NOT conservative) and EMIR-W316 (a `jmax_factor` table that is not
  1.0 at `em_tref`). EMIR-W305 now also says that a temperature above the
  table's top is not conservative, and checks `em_tref` against the table. The
  run's `not_checked.avg_not_derated` and the report carry the gap.
- `not_checked.bidirectional`: a branch whose signed average current cancels
  (a reversing current) but whose average of |I| reaches the warn fraction of
  the avg limit is listed, with both ratios (no bidirectional recovery rule is
  applied). `not_checked.via_crowding`: a multi-cut via array within 10 % of
  its limit is listed, since its ratio is the array's mean per cut and edge
  cuts carry more (+5 to +10 % in examples 04 and 07).
- `emir next --period S`: the planned `check` and `run` align their window to
  the block's period (not added for DC currents).
- `emir info` names the bundled sample's directory (`sample_dir`) and says
  what an unset site key is for.
- `examples/README.md`: the examples' table, and where `01` went (the
  quickstart on the bundled sample); AGENTS.md links it. `examples/02_xyce`
  and `examples/09_cadence_flow` compare their results with `compare.py`.
- The HTML report of a net draws its vias, gives each violation its fix and a
  "width / cuts" column.

- `emir report --from` reads a block abstract (`emir.abstract/1`): per block
  net the block-alone and back-annotated verdicts, the IR from the pad against
  the budget, the worst items.
- Every worst item carries `bbox_um` (from the DSPF's `$LLX..$URY`, or the
  layout's rectangle), and the report's Worst items table an "at (x, y) um"
  column; its Nets table gives every net's worst wire and worst via.
- `emir check --process FILE` with no DSPF checks a process file on its own.
- A usage error for an option another command takes says what this one takes
  instead (`details.did_you_mean`; an `--out` given to `emir run` names `--summary-out`,
  `--html`, `--viewer`), or the closest option.
- Back-annotation documents say `ir_reference: "pad"` (the top-level drop is in
  `ir_drop_mV`) and, when the top run had `--budget-mv`, `budget_mV` and
  `budget_verdict`; the port-voltages file carries the budget.
- The agent evals grade MCP-driven agents (`run.py --mcp`, `{mcp_config}`),
  count only commands that ran (not template commands in an answer's heredoc,
  not calls the harness refused), take `commands_all` (any order), and read a
  value as a limit only next to the word "limit". The shim logs a call ended by
  SIGTERM; `$EMIR_EVAL_CARD` reaches only the scripted agent. The README's
  Claude Code examples use `--prompt-stdin`, and records all 13 cards run with a
  real agent.
- `emir validate [--out D] [--keep] [--quick] [--no-skip]`: the analytic
  validation suite (13 cases with closed-form answers), also run by pytest
  under the `validate` marker; `--quick` leaves out the three slow transient cases.
- `scripts/leak_guard.py` with a documented allowlist (`.leak-guard-allow`):
  fails on site paths and desktop-user home paths (macOS and Windows), a
  site's tool install directories, licence servers and variables,
  internal-deployment and internal-plan wording, closed-licence strings, PDK
  data files (`*.ict`, `*.tf`, `*.layermap`, `*.drf`) and compiled Python
  (`*.pyc`, `__pycache__/`) (`--list-rules` lists them), and on the rules of
  the private rule file (see Security). Every directory is scanned, `.claude/`, `.github/`, `docs/`,
  `examples/` and `blog/` included. The guard has no exemption for itself, and
  the tests run it on its own source; every rule has a planted fixture built
  from fragments and clean look-alikes that must pass. `--commit-msg FILE`
  checks a commit message.
- `.pre-commit-config.yaml`: ruff check, check-yaml, end-of-file-fixer,
  trailing-whitespace, check-added-large-files (1024 KB), the leak guard on
  the staged files and, as a `commit-msg` hook, on the commit message
  (`pre-commit install` installs both hook types).
- Tests for the packaging, launcher, gui-install, viewer and import-ict changes
  below (`tests/test_packaging.py`, `tests/test_leak_guard.py`,
  `tests/test_validate.py`).
- `LICENSE` (MIT), `NOTICE`, this changelog.
- `emir run --fail-on-violation`: exit 1 when any net's verdict is FAIL (for
  CI and agents). The full exit-status contract is under "The agent
  contract" below.
- `dspf_layers:` accepts `$lvl` numbers as keys (`1: Metal1`, or `"lvl:1"`)
  for extractors that write a level number and no layer name; such
  resistors get the layer token `lvl:N` and are reported until mapped.
- DSPF layers written as `$layer=<name>` or `$lyr=<name>` are read, quoted
  or not.
- `emir import-ict --conductor-pattern '{layer}_conn'` (also
  `ict_conductor_pattern:` in the file) and a per-layer or per-via
  `ict_name:` for ICTs whose block names differ from `<layer>_conn`.
- The run summary JSON records, per net, `self_consistent` and `via_cuts`
  (how many cut counts came from the cut area, and which vias were checked
  as one cut).
- `pdks/`: process files for three open PDKs, built only from the PDKs' public,
  Apache-2.0 PDK files, with a source (file and line, section, or URL) on every
  value: `sky130.yaml` (li1, met1-met5 and their vias; EM at Tj = 90 C),
  `gf180mcu.yaml` (gf180mcuD, Metal1-Metal5; 125 C), `ihp_sg13g2.yaml`
  (Metal1-Metal5, TopMetal1-2; 105 C). Limits a PDK does not publish (peak
  everywhere, rms on vias, and more; `pdks/README.md` has the table) are left
  out and reported as not checked. `pdks/TEMPLATE.yaml` lists every key emir
  reads, with units and invented values. `tests/test_pdks.py` loads and checks
  each file, hand-checks the units (sky130 met1: 2.8 mA/um x 0.14 um =
  0.392 mA) and, when the PDKs are installed, that every cited line holds its
  value.
- `jmax_rms:` on a layer: a fixed rms limit (mA/um), as a tech LEF publishes
  it (`ACCURRENTDENSITY RMS`), which `--delta-t` does not scale. The ICT form
  `jmax_rms_k` is unchanged; a layer with both fails `emir check`, a fixed rms
  limit below the avg limit is warned about, and `emir import-ict` replaces a
  `jmax_rms` with the ICT's `jmax_rms_k`. The mesh flow's summary lists the
  layers with a fixed rms limit (`rms_fixed`).
- Tests for every fix below: `tests/test_layers_and_vias.py`,
  `tests/test_cc_modes.py`, `tests/test_cli_ux.py`, `tests/test_import_ict.py`,
  `tests/test_scrub.py`, `tests/test_skill_injection.py`,
  `tests/test_dspf_robustness.py`, `tests/test_cli_strict.py` (synthetic PSF
  results, no simulator needed).
- `THIRD_PARTY_NOTICES.md`: what the offline bundle redistributes (the
  standalone CPython and the libraries it links, Tcl/Tk, pip and setuptools,
  numpy and scipy with their bundled OpenBLAS and GCC runtime libraries,
  PyYAML, matplotlib and its dependencies including pillow) and under which
  licences; copied into the bundle and the sdist, and checked against the
  build's dependency list by `tests/test_packaging.py`.
- "Screening, not sign-off." ends the console's `overall verdict:` line, is
  in the footer of every HTML page (each net's report, the per-net
  `index.html`, the mesh flow's run index) and is the run summary JSON's
  `disclaimer` field (`emir.SCREENING_NOTE`).
- The agent contract. EMIR's first user is an AI agent; every
  command now speaks to one:
  - **Error codes.** `emir/errors.py` is the one registry: `EMIR-Exxx`
    (refusals and input-check FAILs), `EMIR-Wxxx` (warnings), `EMIR-Ixxx`
    (infos, and the checker's OK lines), by area (1xx files, 2xx DSPF, 3xx
    process file, 4xx currents and results, 5xx options, 6xx tools, 7xx
    outputs, 8xx reserved for the hierarchy flow, 9xx internal), each with a
    title, meaning, hint, fix-or-diagnose command template and exit status;
    codes are never reused (`RETIRED`). Every refusal is an `EmirError`
    (a `ValueError`) carrying its code, hint and `next_steps`; every
    checker finding carries a code of its level; every warning logged during
    a run is a coded `[emir] WARNING EMIR-Wxxx:` line (`errors.notice`) and
    is recorded for the JSON output. `tests/test_errors.py` fails on any
    plain `raise`, uncoded finding or warning line.
  - `emir explain <code> | --list` prints registry entries; `agent/errors.md`
    is generated from the registry by `scripts/gen_errors_md.py` (a test
    fails when it is stale).
  - **`--format text|json` on every command.** With json, stdout carries
    exactly one JSON document with `"schema": "emir.<name>/1"` and
    `emir_version`; logs and the text tables go to stderr; a command that
    cannot do its job prints an `emir.error/1` document (code, message,
    hint, exit status, next steps, details).
  - **JSON Schemas** (draft 2020-12) for every document and for the process
    file: `emir/schemas.py`, copied to `agent/schemas/<name>.schema.json` by
    `scripts/gen_schemas.py`; `emir schema <name> | --list`. The tests
    validate the real output of every command, and every shipped process
    file, against them.
  - **Exit status contract**, the same for every command: 0 done; 1
    violations with `--fail-on-violation`; 2 bad input or refused (`check`:
    inputs not OK); 3 a required tool or Python package missing; 70 an
    internal error (EMIR-E9xx), a failed self-test or validation; 130
    interrupted.
  - **`next_steps`** in the `check`, `run` and error documents: `{why,
    command}` with exact commands built from the caller's own arguments,
    paths quoted, keeping the caller's `--format` (`tests/test_next_steps.py`
    runs them from a directory with a space in its name).
  - **`emir next`**: the playbooks' decision tree as code. From what the
    agent has (`--dspf`, `--process`, `--cc-sim`, `--netlist`, `--currents`,
    `--mode`, `--net`, `--viewer`/`--html` wishes, and the tools `emir info`
    finds, or `--have-cadence/--have-xyce/--have-ngspice`) it prints the
    ordered commands with reasons, what is missing and how to get it, the
    mode (rcc for a small net with every branch current; cc-grid for pin
    currents or bigger nets; the C-only / R-only extraction advice at
    ADC-top / PLL-top size), and flags each step that needs the person's
    go-ahead (a licensed tool or a long run). Rules are small registered
    functions; a feature adds its own in `emir/plan_<feature>.py`.
  - **`emir report --from <summary> [--format md|json]`** (`-` reads stdin):
    the summary an agent relays -- verdict, mode, current source, analysis
    temperature, governing metric and IR per net, the worst items with their
    fix in engineer's units (never below 1.00x), IR per power net, what was
    NOT checked (layers or vias without limits, estimated limits, unsaved
    pins, vias counted as one cut, wires without width, a forced or skipped
    input check), the input checker's warnings, and "Screening, not
    sign-off.".
  - `emir info` also looks for Virtuoso (`$VIRTUOSO_BIN`) and Quantus
    (`$QRC_BIN`, `qrc`) and marks the licensed tools.
  - Tests: `tests/test_errors.py`, `tests/test_output_contract.py`,
    `tests/test_schemas.py`, `tests/test_next_steps.py`, `tests/test_plan.py`,
    `tests/test_report.py`; `jsonschema` joins the `dev` extra.
- Robustness fixes (tests in `tests/test_cli_robustness.py`):
  - Codes EMIR-E217 (not a DSPF), E316 (unknown key in a process entry),
    W314 (unknown top-level process key), E424 (names sidecar unusable),
    E605 (simulation timed out), W209 (mesh grid coarsened), W703 (KLayout
    marker database not written) and E906 (terminated by SIGTERM, exit 143).
  - Schemas `names` (the `<result>.names` sidecar that maps a Xyce or ngspice
    result's signal names onto the DSPF's), `currents` (the `--currents`
    JSON) and `help` (19 in all).
  - `--help` in `--format json` prints an `emir.help/1` document, so JSON
    mode always prints one document; `--format json` inside an `@argfile`
    is honoured from the first error on; SIGTERM ends a command with its
    EMIR-E906 document and exit 143.
  - The run summary records when the run finished (`created`, UTC); `emir
    report` names the run (command and time) and lists the run's own
    warnings apart from the input check's.
  - `emir next` steps carry `after` (the steps whose output they read) and
    `placeholders`; a step whose command holds one (`<result.raw>`) is
    `waiting`, every other step runs as printed.

The features, one entry each (four more have headings of their own, below):

- **Chip-level EMIR, hierarchically (`emir abstract`, `emir run --abstract`)**
  - `emir abstract BLOCK.dspf --process P --cc-sim R -o X.abstract.json`: a block characterised
    once for a chip-level run. Per block net: the block's own EM/IR verdict with every pin of
    the supply held at one ideal potential; one period of the current each pin pushes into the
    top-level net (uniform samples) and the exact window scalars; the exact Norton conductance
    between the pins (`y_ports`, a Schur complement of the block grid); a two-moment R + C lump
    of the block's capacitance and, when the DSPF has capacitance, its internal dynamic droop.
    Options `--net A,B`, `--period`, `--points`, `--vdd V|NET=V`, `--em-temp`, `--delta-t`,
    `--fail-on-violation`, `--no-check`, `--force`. The file is `emir.abstract/1`; its port
    list is a tap file. `--format json` prints `emir.abstract_summary/1` (the abstract without
    its waveforms, the `--abstract` argument that uses it, notices, next steps).
  - `emir run TOP.dspf --abstract FILE@INST ...`: the top with its blocks black-boxed. Each
    abstract port lands on the top's `*|I` record of its pin, by name, or by coordinates
    through `--place INST=DX,DY,ORIENT` (`--place-tol`); ambiguity is refused. The block's
    port conductance is stamped between its records (solved, never checked). Several `*|P`
    pads of the top net are held at one potential (EMIR-I801).
  - Alignment of the blocks in time, `--align`: `inphase` (default; the upper bound), `sync`
    (one clock reference, exact for it; `--phase INST=S`, `--phase-sweep INST:N` for the worst
    over N phases of related clocks), `async` (unrelated clocks only; warns that it is not an
    upper bound for related clocks, EMIR-W802).
  - `--budget-mv MV`: the IR budget per block, top-level drop at its worst pin plus its
    internal drop (exact for a one-pin supply, an upper bound for several pins);
    `--fail-on-violation` also exits 1 on a budget FAIL.
  - `--port-voltages-out F` (`emir.port_voltages/1`): the top-level voltage at every block pin
    and between the pins of a block net, with `v_delivered_V` for the self-consistency loop;
    `emir abstract ... --port-voltages F@INST` re-checks the block with them
    (back-annotation: exact from a sync run, avg exact and rms/peak bounds otherwise), and the
    run's next steps hold that command for every block fed at several pins.
  - Recursion: `emir abstract MID.dspf --cc-sim MID.raw --abstract SUB.abstract.json@XSUB
    [--place XSUB=...] [--phase XSUB=S]` abstracts a mid-level block from its own devices and
    its sub-blocks' abstracts (one clock reference); the sub-blocks' port conductances join its
    grid, so its `y_ports` composes theirs. The net entry lists them in `sub_blocks`.
  - `emir run ... --abstract ... --cc-sim TOP.raw`: the top's own devices outside the blocks take
    their currents from the top's CC simulation, next to the abstracts, in every alignment mode.
  - The run summary gains a `hierarchy` section (blocks, pins, pass-through, budgets, notes),
    `current_source.kind` `abstracts`; `emir report` relays it.
  - `emir check` reads the abstracts and checks where their ports land; `emir next` takes
    `--abstract`, `--place`, `--align` (and `--cc-sim` for the top's devices) and plans check,
    run, report and back-annotation
    (`emir/plan_hierarchy.py`); with a black-boxed block and no abstracts it advises the route.
  - Error codes EMIR-E801..E813, W801..W808, I801..I805 (the 8xx area), schemas `abstract`,
    `abstract_summary`, `port_voltages`.
  - `examples/05_chip_level`: a synthetic ADC block (one supply pin) and PLL block (two pins)
    in a top; `run.sh` runs the whole flow offline and compares with `expected/`; every
    top-level branch equals a flat run of the same network to 4.9e-13 (relative), and the
    back-annotated block branches too. pytest marker `example`.
  - The method, commands, limits and measured numbers (now `docs/chip-level.md`) and
    the agent playbook (now `agent/playbooks/03-chip-level-hierarchy.md`).
- **Big blocks: streamed superposition and cc-grid dynamic IR**
  - Streaming waveform superposition (`emir/bigblock.py`). When the dense transfer
    matrix (branches x taps x 8 bytes) would exceed `--max-memory` (default 2G), a
    cc-grid run no longer stops with EMIR-E418: it solves the grid in time chunks
    (one multi-column back-substitution per chunk, in blocks of 8 columns) and
    accumulates each branch's trapezoid-weighted average, mean square and running
    peak. The answer equals the dense path's to rounding (tests hold it to 1e-10;
    measured within 7e-15). The 92 k-node, 181 k-resistor, 2 000-tap synthetic case
    the dense path refused now runs in about 12 s with a peak of 0.8 GiB, single-threaded,
    where the dense path (given the memory) takes 61 s and 4.5 GiB
    (`examples/06_big_block/results/stream_bench.csv`).
  - `emir run` / `emir check` options `--max-memory SIZE` (2G, 512M ...),
    `--superposition auto|dense|stream` (auto also streams when the result has
    fewer than twice as many time samples as taps, where streaming is the cheaper
    path), `--dynamic` and `--dyn-steps N`.
  - Dynamic IR in cc-grid (`--dynamic`): the CC tap waveforms are integrated through
    the RC grid (the DSPF's capacitance, plus the MOS decaps and capacitor devices
    the process file's `cap_models` size), taken to periodic steady state over the
    whole-period window. Tested against the validation suite's closed forms (R || C
    at wRC = 0.1, 1, 10; a decap with and without access resistance) to 0.5 %.
  - `dynir.transient(periodic=True)`: integrate the window again from where it
    ended until it repeats (`passes`, `settled` in its result).
  - Run summary (`emir.run/1`), per net: `superposition` (which path ran, why, the
    matrix size, the budget, the chunking) and `dynamic` (worst droop, node and its
    coordinates, time, steps, peak retention, capacitance, decap lumps, periodic
    passes). `emir report` relays the dynamic droop beside the IR drop of each
    power net (`ir_by_power_net[].dynamic_mV`).
  - Codes EMIR-I450 (superposition streamed), EMIR-W450 (dynamic IR not computed,
    and why), EMIR-W451 (dynamic IR not at periodic steady state), EMIR-E550
    (`--dynamic` without simulated waveforms: refused by the input check).
  - `emir next` rules for ADC-top / PLL-top-size DSPFs (`emir/plan_bigblock.py`):
    the CC + R-only recipe (which extractions, `save-lines --mode cc`, PSS or a
    coherent transient over whole periods, one run per operating mode,
    self-consistency above about 2 % droop, `--dynamic` when the DSPF carries
    capacitance, hierarchy for the chip), a caution on `--mode rcc` at that size,
    and, at any size, the dense-matrix memory estimate when it exceeds the default
    budget.
  - `examples/06_big_block`: a synthetic mesh generator, the streamed-against-dense
    check (no simulator) and the RCC against CC + cc-grid sweep on Xyce, with
    committed result tables (`results/`, labelled "Xyce, synthetic, serial") and
    golden files with tolerances (`expected/`); `run.sh` works offline from a clone.
  - pytest marker `example`.
- **Xyce and ngspice for the DSPF flow (`emir save-lines --sim`, `emir probe`, `emir simulate`)**
  - `emir save-lines --sim xyce|ngspice`: Xyce `.print tran format=raw` and ngspice
    `.save` (with `.options filetype=ascii`) lines for every branch current (rcc) or
    instance-pin current (cc). `--names-out` writes the `<result>.names` sidecar,
    `--raw` names the result, and `--probes` reads pins probed by `emir probe`.
    Spectre output is unchanged.
  - `emir probe`: rewrites a SPICE netlist (a testbench, a block, a DSPF) with a 0 V
    source in series with each device terminal on the chosen nets, following its
    `.include` files into copies. Pins are named as the DSPF's `*|I` records name
    them, and the sidecar gets a `probes` list.
  - `emir simulate`: runs Xyce or ngspice on a copy of the person's testbench:
    print/save lines in, relative includes rebased, DSPF `$` annotations left out
    for Xyce, the testbench's `.spiceinit` beside ngspice's deck, `--probe` on
    request. It writes an ASCII result and its names
    sidecar, then checks that every current asked for is in the result. Its
    next steps are check / run / report.
  - `emir next` (`emir/plan_opensim.py`): with Xyce or ngspice and no Spectre, the
    simulation step is `emir simulate`, and `--netlist` is its testbench, read to
    choose rcc or cc and whether to probe. Every step runs as printed. A result
    made with emir's lines but no sidecar gets the `save-lines --names-out` step
    that maps it.
  - Error codes E260-E263, W260-W263, I261, I262, E460-E462, W460-W462, E660
    (exit 3), E661, W660, I660. Schemas `probe` and `simulate`. The `save_lines` schema
    gains `simulator` values, `raw`, `names`, `nets`, `direct` and `probed`; the
    `names` schema gains `probes`.
  - `examples/02_xyce`, `examples/03_ngspice`: one invented rail with four
    subcircuit NMOS loads through both simulators, cc-grid and rcc, with golden
    results and a hand calculation (`tests/test_examples_opensim.py`, marker
    `example`).
  - `scripts/opensim_proof.py`: Xyce vs ngspice, cc-grid vs rcc, against closed
    forms, on DC sinks, pulsed sinks and a CMOS inverter bank, and (opt-in,
    `--cases sky130`) on the sky130 PDK's own nfet through ngspice.
  - `agent/opensource/`: `run_xyce.sh` and `run_ngspice.sh` (the whole flow for a
    person), `dspf_preview_gds.py`, `lyrdb_summary.py` and `render_markers.py`
    (KLayout marker database checks and pictures without a window), and a
    README.
- A feature's `plan_<feature>.py` can add `emir next` options (`add_arguments(ap)`,
  `inputs(a)`).
- **Cadence integration (`emir extract`, `agent/cadence/`)**
  - `emir extract --gds G --cdl C --cell X --kind r_only|c_only_coupled|rc_coupled|rc_decoupled|c_only_decoupled`:
    GDS + CDL -> `<cell>.<kind>.dspf` with PVS LVS and Quantus. A dry run by default
    (the plan: commands, command file, where every tool and deck was found, what is
    missing; EMIR-I680); `--run` runs LVS, refuses to extract unless the report says
    MATCH (EMIR-E280), reuses a matched LVS newer than the inputs, and runs Quantus.
    Settings from the options, `$PVS_RULES` / `$QRC_TECH_DIR` / `$QRC_CORNER` /
    `$EMIR_EXTRACT_TEMP` / `$PVS_BIN` / `$QRC_BIN`, or the site file; `--site FILE`.
    New codes EMIR-E580, E680 (exit 3), E280, E281, E681. Schema `emir.extract/1`.
    Tests run it against stand-in executables (`tests/_cadence_fakes.py`).
  - `emir save-lines --mode rcc|cc-grid` (the person's mode) and `--out FILE` (the
    testbench include file).
  - `emir run --viewer-all`; without it a run with more than 20 000 segments keeps the
    worst in the viewer data (clean segments below 0.5x and IR off the rails left out;
    EMIR-I780).
  - `agent/cadence/`: the environment-driven Virtuoso entry point
    (`skill/emir_init.il`), the generated `skill/emir_main.il` and
    `skill/emir_display.drf`, the Quantus command file and `render_ccl.py`, the PVS +
    Quantus shell script with the MATCH gate, Spectre RCC and CC testbench templates,
    an OCEAN sketch, Calibre and StarRC notes, `python/virtuoso_batch.py`, a site
    example, `SKILL_NOTES.md`, and the live test (`LIVE_TEST.md`,
    `test/emir_manual_test.il`). Every file says VERIFIED-AT-ORIGINAL-SITE or
    UNVERIFIED. `scripts/gen_cadence_skill.py [--check]` keeps the generated copies.
  - `emir next` rules for the Cadence flow (`emir/plan_cadence.py`): extraction steps
    (for callers that pass `gds`, `cdl`, `cell`), big-block, testbench and viewer
    advice at a Cadence site.
  - `examples/09_cadence_flow/` (offline: the plans, save lines, a filled testbench)
    and the `example` pytest marker.
- **EMIR as an MCP server (`emir mcp`)**
  - `emir mcp`: EMIR as a Model Context Protocol server over stdio, for MCP
    clients (Claude Code, Claude Desktop, Codex, Cursor and others). Needs the
    optional extra `emir[mcp]` (the official MCP Python SDK, `mcp>=1.19`; its
    1.x `FastMCP` and 2.x `MCPServer` lines both work).
  - One typed tool per command, `emir_<command>`, built from the command's own
    options, returning the same JSON document as `emir <command> --format
    json` (structured content and JSON text; `isError` for `emir.error/1`;
    the exit status in `_meta`). `emir_cli` runs one emir command line as
    printed in `next_steps`. A command added later is served at once.
  - Resources: the error catalog, every JSON Schema, and (from a clone)
    AGENTS.md and the agent playbooks by path.
  - The go-ahead rule: a tool call that would start a licensed tool (Spectre;
    PVS and Quantus through `emir extract --run`), run long (a DSPF above
    `emir next`'s big-block threshold; `emir simulate` above the small-net size;
    `emir abstract` of an ADC/PLL-top block), rewrite a file in place, or run an unclassified command is
    refused with EMIR-E590 unless it carries `confirm: true`.
  - `emir mcp --list` (`emir.mcp/1`, new schema `mcp`): the tools, resources
    and this machine's client configuration snippets; works without the SDK.
  - Options `--cwd`, `--agent-dir`, `--isolate` (every call in a fresh
    process) and `--no-output-schema`.
  - Over MCP, `emir next` gives each step a `tool` field: the exact call that
    runs it.
  - New codes: EMIR-E590 (the person's go-ahead is needed), EMIR-E591 (not
    runnable over MCP), EMIR-I690 (server started), EMIR-I691 (agent
    documents not found).
  - `agent/mcp/`: configuration snippets for Claude Code (command and
    `.mcp.json`), Claude Desktop, Codex, Cursor and a generic client, and a
    README.
  - `examples/10_mcp_agent/`: a scripted MCP client that follows `emir_next`
    on the sample offline and shows the go-ahead rule, with expected results.
  - `scripts/mcp_sdk_matrix.sh`: the MCP tests against several SDK releases.
  - The `mcp` package joins the `dev` extra; pytest marker `example`.
  - Notes:
    - While `emir mcp` serves, stdout is the protocol channel (file descriptor 1
      is pointed at stderr), the one exception to the one-document rule; SIGTERM
      and SIGINT end it at once with 143 and 130.
    - `emir selftest` called over MCP runs in a fresh interpreter (its import
      check depends on what earlier calls loaded).
- **The agent contract, generated (`agent/CONTRACT.md`)**: what an agent may
  rely on, written from the code by `scripts/gen_contract.py`: every command
  with its options (the CLI's own parsers), the JSON documents and their
  schemas, the exit statuses, the code areas and decades (`errors.DECADES`),
  the next-step rules and `emir next`'s step fields, thresholds and rules, and
  the MCP tool of each command with its go-ahead rule. It is written without
  the site file or tool variables, so it does not depend on the machine; a
  test and a pre-commit hook fail when it is stale, and pre-commit now checks
  the other generated files too (`gen_errors_md`, `gen_schemas`,
  `gen_cadence_skill`).
- **Where the features meet** (`tests/test_merge_interplay.py`):
  - `emir save-lines --mode rcc|cc-grid` with `--sim xyce|ngspice` and `--out`: the
    person's mode in the open simulator's syntax, with its names sidecar.
  - `emir/gds.py`: the one layout reader every GDS command uses (E101, E170,
    E212, E270 with runnable next steps per command, E106, E603).
  - `@rule(order, name, final=True)` in `emir/plan.py`: a rule that runs even
    after another stopped the plan (the MCP rule, which gives every step its
    tool call).
  - A hierarchical top run (`emir run --abstract`) honours the big-block
    options `--max-memory` and `--superposition` (the transfer matrices of
    `--align async` and phase sweeps use the same budget).
  - MCP tool output schemas use only keywords draft-07 and 2020-12 share (the
    names sidecar's two-item entry becomes `items`/`anyOf`); a parameter
    whose option has its own type shows an example (`--max-memory 4G`).
  - `agent/CONTRACT.md` counts the commands, codes and schemas from the code.
- **CI and community files:**
  - `.github/workflows/ci.yml`: `core` (Python 3.10, 3.12, 3.13 with no
    extras; `emir selftest` from `/`), `full` (every extra and ngspice: every
    test, the examples, `emir validate --no-skip`; Xyce when Ubuntu packages
    it, otherwise a notice and the Xyce cases skip), `lowest` (every direct
    dependency at its floor, `uv --resolution lowest-direct`, Python 3.10),
    `wheel` (build, `twine check`, the sdist holds the source tree, the wheel
    installed alone runs `emir selftest` from `/`), `lint` (pre-commit; the
    leak guard on every new commit message), `docs` (`mkdocs build
    --strict`), and one `all green` check to require. Tests marked `cadence`
    are deselected.
  - `.github/workflows/release.yml` (tags `v*`): checks the tag against
    `emir.__version__`, builds the sdist, the wheel and the offline bundle,
    runs each from `/`, writes `SHA256SUMS` and attaches all four to a draft
    GitHub Release. PyPI trusted publishing is prepared and commented out.
    `.github/workflows/docs.yml`: the documentation site from `main` to GitHub
    Pages through the `github-pages` environment (approval by its reviewers).
    Every action is pinned by commit SHA.
  - Issue forms (bug, feature, PDK onboarding, simulator / extractor / DSPF
    dialect, agent integration), each with a required no-foundry-data
    confirmation; the bug form asks for `emir --version`, `emir info --format
    json` and `emir check --format json`. A pull-request template with the
    gates, Dependabot (pip and actions, monthly).
  - `CONTRIBUTING.md`, `SECURITY.md` (private reporting; the SKILL files Virtuoso
    loads and the ICT expression parser), `CITATION.cff`.
  - `scripts/check_sdist.py`: the sdist holds every tracked file except the
    ones it lists with a reason. The `docs` extra (mkdocs, mkdocs-material).
  - pre-commit's checks of `gen_errors_md.py`, `gen_schemas.py` and
    `gen_cadence_skill.py` need only the standard library (no numpy, scipy or
    PyYAML in the hook's environment).
- **The documentation** (`docs/`, readable on GitHub and built with MkDocs
  Material: `pip install -e '.[docs]'`, `mkdocs build --strict`): getting
  started, concepts, methods and modes, big blocks, chip level, the open-source
  and Cadence flows, driving EMIR from an agent, PDK onboarding, the process file
  reference, DSPF requirements, validation, limits, FAQ.
  - `docs/cli-reference.md` is generated from the commands' own parsers by
    `scripts/gen_cli_reference.py` (`--check` in the tests), so it cannot drift.
  - `tests/test_docs.py`: the README's quickstart runs as written; the process
    file reference names exactly the schema's keys; every relative link and
    anchor resolves; the MkDocs navigation lists every page.
  - `scripts/mkdocs_hooks.py` turns links to repository files outside `docs/`
    into links into the repository once `repo_url` is set in `mkdocs.yml`.
  - The README is rewritten for the public repository: agent first, a
    quickstart, the features, the examples, the licence and trademark notes.
- **The agent layer** (`AGENTS.md`, `agent/`): the primary interface.
  - `AGENTS.md`, which Codex and other agents read and `CLAUDE.md` imports:
    the preflight, the decision tree as a table of playbooks plus "run `emir
    next` with what you have and follow its plan", the contract, the
    guardrails and the reporting contract.
  - `agent/playbooks/00-preflight.md` to `10-mcp.md`: one per task (when to
    use, inputs, commands, decision points with their codes, success
    criteria, what to report, failure modes), each with a worked example on
    the examples' files that runs as printed.
  - `AGENTS.md` and the playbooks point at the generated `agent/CONTRACT.md`
    (above) for every command's document, the exit statuses, the code areas
    and the plan and run-summary fields.
  - `agent/skills/emir/SKILL.md`: the Claude Code skill; `.claude/skills/emir`
    is a relative link to it. `agent/README.md`: connecting Claude Code,
    Codex, Cursor and MCP clients.
  - `agent/evals/`: 16 task cards covering every playbook, with fixtures built
    from the examples, deterministic graders (every emir command the agent ran
    is parsed with emir's own parser; verdict, worst ratio, governing metric,
    disclaimer, no invented limit, no input changed) and a runner for any
    agent CLI (`run.py --agent-cmd 'claude -p'`, `'codex exec'`), with a
    logging shim for the agent's emir calls.
  - `tests/test_agent_layer.py` runs every worked example and parses every
    emir command the documents print; `tests/test_agent_evals.py` replays each
    card's reference with a scripted agent and checks that the graders catch
    eight kinds of mistake.
- The package's long description is `README.pypi.md`, generated from README.md by
  `scripts/gen_pypi_readme.py` with every repository link absolute (relative links break
  on PyPI) and the banner's light version (PyPI shows a `<picture>`'s fallback image on
  a light page); a test and a pre-commit hook keep it current. The bug form offers
  "wheel from a GitHub Release" (emir is not on PyPI).
- The GitHub Release notes are the version's CHANGELOG section, cut after the last
  whole entry within GitHub's 125,000-character limit, never on a heading without its
  entries, with a link to the full list (`scripts/release_notes.py`). The sections run
  Added, Changed, Removed, Fixed, so a cut falls in the fixes.

#### Simulator routes and their verification

- **`emir verify-sim` records.** The latest valid record of this machine for a key --
  simulator, version, output format and simulator arguments (`--sim-args`, as the shell
  splits them) -- decides: a later FAIL cancels an earlier PASS (EMIR-W414 on a run),
  and a run simulated with `+aps` is matched only to a verification made with it.
  Records are validated strictly (`passed` a JSON boolean that agrees with the checks;
  `schema`, `simulator`, `version`, `format`, `emir_version`, `date` and `checks`
  required); an invalid one counts as none (EMIR-W415). A pass of another emir minor
  version is stale (EMIR-W464, `reason` `stale`, and `emir next` verifies again). A
  result whose version cannot be told (its header, else the names sidecar, where `emir
  simulate` now records the simulator's version and arguments, Spectre's beside its
  result directory) is "not verified: version unknown" (EMIR-W416), never matched. A
  record made with `--record-host` counts only on that host; a simulator that prints no
  version fails the verification. `simulator_verification` carries `reason`, `sim_args`,
  `sim_args_known` and `ignored_records`; the `verified` and `verify_sim` schemas
  describe the record strictly.
- **`emir verify-sim` exercises device terminals.** A third case, `dev` (rcc and cc),
  puts real devices on the rail, each fed by an ideal 1 mA source: a diode-connected
  PMOS (source and bulk on the rail), a diode-connected NMOS (drain and gate), a
  resistor read at its pin 2 and a subcircuit-wrapped NMOS read through `emir probe`'s
  0 V sources, saved with the forms the cc route uses (HSPICE `i1`..`i4` with the KCL
  sign and `-i1`, Xyce `ID`/`IS`/`IG`/`IB`, ngspice probes and `@r[i]`, Spectre
  `inst:pin`), each compared with its known value and direction: 154 checks with the
  self-consistent case (under "The mode is the person's choice" below); Xyce 7.10
  and ngspice 45 pass them here. docs/simulators.md and the README say
  exactly what a pass covers, and what not (BJT, JFET and diode terminals, PDK models).
- **ngspice MOS, BJT and JFET pins are read through 0 V probes.** ngspice's
  `@m[id]` is the model's internal current: for a PMOS it has the opposite sign of the
  current into the pin (a diode-connected PMOS fed by 1 mA reads +1 mA where 1 mA flows
  out of its drain; `tests/test_verify_findings.py` measures it), not the current into the
  pin. `save-lines --sim ngspice` refuses those
  pins (EMIR-E460, saying why) and `emir simulate` / `emir next` probe them.
- **HSPICE pin 2 of a two-terminal element** is read as `-i1(...)`, never `i2(...)`:
  no dependence on how HSPICE draws `i2`; both pins on one net give the second a probe.
- **Pin names.** A MOSFET's `SUB` / `SUBSTRATE` is its bulk, a BJT's its substrate
  terminal; anode and cathode names
  are a diode's only. A pin that names no terminal of a device the netlist knows is
  refused (EMIR-E425), never read from another terminal.
- **The HSPICE reader** parses its header in time linear in the signal count
  (`tests/test_verify_findings.py` holds a 100 000-signal header to 2 s) and keeps the values as packed doubles. A run reads its result once (the input check's read is
  reused), so EMIR-I465 is said once. NaN and Inf fields are values, and `emir check
  --cc-sim` FAILs on currents a net needs that hold them (EMIR-E426); an Inf time, and a
  file cut inside a value, get messages that say so. Duplicate signal names, 9007 names
  cut alike, and a cut name several sidecar names start with are refused (EMIR-E427).
  CSDF with complex values is reported as an AC result; the naming hint knows HSPICE's
  `i1(`..`i4(` forms.
- **`emir simulate`** keeps the quotes of `.include` / `.lib` paths, and puts its lines
  before the testbench's first `.alter` (EMIR-I610), so the main case's `.tr0` holds
  them; inside `simulate` a pin without a probe is printed directly or refused (no
  EMIR-W260).
- **CI.** `tests/test_blog.py` skips its image check without Pillow, and
  `tests/test_packaging.py` reads `pyproject.toml` with `tomli` on Python 3.10 (skips
  without it), so the core job (`pip install . pytest jsonschema`) passes on 3.10.
- **Wording.** The measurement-provenance sections (docs/cadence.md,
  agent/cadence/README.md, docs/method-modes.md, docs/big-blocks.md) quote the original
  development site only as the source of a number, not as evidence that a tool works
  anywhere; the HSPICE reader follows the format rules described in the gwave, Electric
  and PyOPUS sources (it was not run against them). New codes: EMIR-E425, E426, E427, W414, W415, W416, I610.
- **Simulator verification is per kind of current.** Each check of `emir verify-sim`
  names the kind of current it proves and the record lists the kinds it covers
  (`covers`): resistor branch currents, MOSFET terminals, resistor and capacitor
  pins, pins read through emir's 0 V probes, and (Spectre) subcircuit and source pins.
  No testbench holds a diode, BJT or JFET, so a run that reads their terminals
  directly is `partly verified` (or `not verified`), never `verified`: the summary's
  `simulator_verification.pin_kinds` lists each kind the run read, how many pins, and
  whether the record covers it, and EMIR-W464 names the pins that stay unverified. The
  names sidecars of `emir simulate`, `save-lines` and `emir probe` record how each pin
  is read (`kinds`). A record cannot claim a kind no passing check of it proves.
  - `emir next` judges a finished `--cc-sim` result by the kinds of current the planned
    run reads (the DSPF's nets in the chosen mode, the names sidecar's kinds), as the
    run does: it plans a verification when the record lacks a kind this emir's
    verification covers, and says which pins no verification covers.
  - Before a simulation it plans, and in `emir info` (`tools[].verified`), a record
    counts only when it covers every kind this emir's verification covers: one that
    lists no `covers` is verified again, and the plan says why.
- **A result in a format verify-sim never writes** (CSDF; Spectre's nutascii) is
  `format not covered`: EMIR-W464 says its currents stay unverified and suggests no
  verification, and `emir next` plans none. A version of nothing but zeros (a CSDF
  header's `VERSION='0'`) is no version.
- **The records file** (`verified.json`) is written under a lock and replaced
  atomically: two verifications that finish together keep both records, and the file
  keeps its mode (a new one gets the umask's). A damaged file (not JSON, no `records`
  list) is reported on every run (EMIR-W415, `records_problem`) and moved aside to
  `verified.json.bad-<date>` by the next verification, never read as empty and
  overwritten. One this user cannot read is left alone: runs say `records unreadable`
  and suggest no verification, `emir next` asks the person for access, and `emir
  verify-sim` refuses to start (EMIR-E102; EMIR-E105 for a state directory it may not
  write) rather than simulate for a record it could not keep (EMIR-W467).
- **No verification is suggested where none can help.** A result whose version is
  unknown, or a records file this user may not read (`reason` `records unreadable`), has
  no `simulator_verification.command`, and `emir next` plans no verify-sim for it (for the
  unreadable file, `missing[]` asks the person for access). A run whose answer a
  verification would change carries the verify-sim step in its `next_steps`, and `emir
  next --run-summary` plans from the summary's own `simulator_verification`.

#### EM data entered by hand

For a PDK with no readable process data (no file in `pdks/`, and the ICT
missing or encrypted), the EM data is typed from the design manual, and EMIR
guides it and says so in every report.

- `emir process new --from-dspf D [--pdk NAME] [-o F]` writes a fill-in
  process file for exactly the layers and vias D uses: its layer tokens and
  `$lvl` numbers, the `dspf_layers` map filled in, each via's below/above
  inferred from the wires its resistors join (by their `$lvl`, else the numbers
  in their names) and marked `inferred from the DSPF ... check`, and
  `cap_models` entries for its capacitor and MOS-decap models. Every value is
  null with its unit and a `sources:` slot; it never fills in a value and never
  overwrites a file (EMIR-E326). Without a DSPF, `--layers` and `--vias`.
  `--interactive` asks a person value by value at a terminal (a non-number is
  refused, a source is asked for each value) and refuses a stdin that is not a
  terminal (EMIR-E327): agents never use it. Document `emir.process_new/1`.
- `emir process check F [--dspf D]`: the values still empty, required
  (EMIR-E320, exit 2) and optional (EMIR-I320), values with no source in a
  hand-entered file (EMIR-W320), stacking nobody confirmed (EMIR-W324), likely
  unit slips against each quantity's plausible range in the unit EMIR reads
  (EMIR-W321: A for mA, mOhm for ohm, K for C, nm for um), limits out of order
  (EMIR-W322, peak >= rms >= avg), an upper metal more resistive than the one
  below (EMIR-W323), sources for keys an entry does not hold (EMIR-W325), and
  with --dspf the DSPF layers not covered and wires no wider than their width
  loss. Document `emir.process_check/1`. `emir check --process` runs the same
  checks (they live in `check.check_process`).
- Provenance: the process file's `provenance:` (hand-entered, imported-ict,
  public-pdk-data, mixed; an entry may carry its own) and `sources:` maps per
  layer, via, cap model and at the top level. The run summary gains
  `limits_provenance`; `emir report` a section "Where the limits
  came from"; the HTML report a "limits from" row: per layer and via, how the
  limits were entered and from which document. A hand-entered file's value with no source
  gets EMIR-W320 on every run (the input check's finding, or a notice when the
  check is skipped). The three files in `pdks/` say
  `provenance: public-pdk-data`; `emir import-ict` names the ICT as each
  imported value's source and sets `provenance` (imported-ict, or mixed).
- The two forms design manuals print: `width_loss_um` (I_max = J x (W - dW),
  W the drawn width, on the avg, rms and peak wire limits; the fix widens by the
  ratio of W - dW; a segment no wider than dW is refused, EMIR-E323), and
  `derating: {model: black, ea_eV, n}`, Black's equation J(T)/J(Tref) =
  exp[(Ea/(n k)) (1/T - 1/Tref)] with T in kelvin, the alternative to a
  `jmax_factor` table (both: EMIR-E321; half entered or not positive: EMIR-E322;
  the factor stated, EMIR-I322). `emir validate` has a case, "hand-entry
  forms", held to the hand values (derate 0.415315, neck 16.0521x).
- `emir import-ict` refuses an ICT it cannot read (binary, compressed, encrypted,
  not text, an undeclared encoding, an encryption marker) with EMIR-E325, never
  decrypting or guessing it, and points to `emir process new`.
- An empty (null) value is a value not given everywhere in the engine (the
  default applies, or the check is not made); a null `gds` is accepted.
- `emir next` without `--process` plans `emir process new` (`needs_human`, with
  the new step field `ask`) and `emir process check`. Playbook 04 has the branch
  "No readable file (missing or encrypted)"; AGENTS.md, the skill, the MCP
  playbook and README, and the docs ("Entering EM data by hand" in PDK
  onboarding, the process file reference, pdks/TEMPLATE.yaml) describe it. MCP
  serves a command made of sub-commands as one tool per sub-command:
  `emir_process_new` (writes a new file; no `interactive`) and the read-only
  `emir_process_check`. A new eval card, `ict-encrypted`: the agent must not
  guess a limit, must write the fill-in file and ask.
- In a hand-entered file, a value that sets what the limits allow (em_tref,
  temp_reference, the derating, jmax_factor, a jmax_* or imax_* limit) outside
  its plausible range blocks the run (EMIR-E331) until the person corrects
  it or lists it under the top-level `unit_confirmed:` (a K-for-C slip in
  em_tref would make a FAIL read CLEAN). A run with `--no-check` raises
  EMIR-W321 for each, the summary's `limits_provenance.suspects` lists them,
  and the verdict line and `emir report` say the limits are SUSPECT.
- Black's derating counts as derating in every report (no "NOT conservative");
  below em_tref the factor is clamped at 1 unless `derating: below_tref:
  extrapolate` (EMIR-W326); a temperature at or below -273.15 C is refused
  (EMIR-E332).
- The provenance names unsourced temperatures (`temperature.unsourced`) and, on
  a `--dynamic` run, the cap models; a blank source is no source; an unknown key
  under `derating:` and an unusable cap-model value are refused (EMIR-E316;
  EMIR-E317, under decap-aware EM below); the E320, W320 and W324 hints and
  `emir next` tell an agent to ask the person.
- `emir import-ict`: an encryption marker or binary data anywhere in the file
  refuses it; base64 armour is encrypted; "Encryption: none" is not a marker; a limit written `J * (w - dW)`
  writes `width_loss_um` with its source; a file with no block points at
  `emir process new`.
- `emir process new` leaves a via's below/above empty when the DSPF does not
  say which is below; `--interactive` copies the previous source only when the
  person types `=`.
- The ict-encrypted eval card fails a fill-in file with any value or source
  typed in (`values: none` in grade.py's file check).
- **A unit slip blocks the run whatever the process file's provenance.** A temperature,
  derating or limit outside its plausible range is EMIR-E331 (until corrected or listed
  under `unit_confirmed:`) in a file with no `provenance:` key, or one that says
  imported-ict or public-pdk-data, as in a hand-entered one: plausibility does not
  depend on the key.

#### The mode is the person's choice; self-consistency on every simulator

- **The mode is chosen by the person, never by emir.** `--mode` (rcc or cc-grid)
  is required wherever currents come from a simulation: `run` and `check` with
  `--cc-sim` or `--netlist`, `simulate`, `save-lines`, the big-block and
  hierarchy paths, the MCP tools (a required enum), the Virtuoso form (a
  "choose" entry) and the OCEAN template. `--mode auto` is gone everywhere; a
  run with DC `--currents` needs no mode; a block abstract implies cc-grid.
  A missing mode is EMIR-E520, whose hint names both modes, their trade-offs
  and `emir next`; EMIR-E521 refuses rcc where only cc-grid works (abstract).
  The retired codes (EMIR-I205, I206, E413, I403, I406) are in
  `emir.errors.RETIRED` with the reason.
- **`emir next` without a mode** plans one step, "choose-mode" (needs_human):
  rcc, cc-grid and cc-grid self-consistent with their measured cost and
  accuracy (cited: `examples/06_big_block/results/sweep.csv` and
  `selfconsist_waveform.csv`), a recommendation from the net's size, and which mode
  alone can work when only one can; no run step until the person chooses
  (`emir.modes`). An open-loop cc-grid run given `--vdd` whose droop is above
  about 2 % of the supply warns (EMIR-W520) and plans the self-consistent run
  in its `next_steps`, marked needs_human.
- **The self-consistent loop runs on Xyce, ngspice, HSPICE and Spectre**:
  `emir run --mode cc-grid --self-consistent --sim ... --netlist TB
  [--max-passes N] [--tol-rel R] [--tol-mV X]`, feeding each pin its droop waveform
  (how, when it stops and what it measured: Waveform self-consistency, below). For
  the SPICE simulators emir writes a split copy of the testbench (never edits it):
  each instance pin on the net gets a node of its own, fed from the testbench's own
  rail through `VEMIRSC<k>`, the pin's droop, and through the 0 V probe that measures
  its current (the `emir probe` machinery); the `VEMIRSC<k>` sources are an include
  (`--droop-file`) rewritten each pass. Pass 0 has every droop at 0 V: the
  testbench's open loop, whatever drives the rail (a package resistance included).
  Spectre, whose netlist emir does not read, keeps the split the person adds
  once (`emir run --split-stub` prints it: one `vsource type=pwl file=` per pin);
  the loop rewrites each pin's PWL file and runs Spectre each pass. The licensed
  simulators run only with `--run`
  (a dry-run plan otherwise; the MCP tool confirms); HSPICE's split follows
  the documented syntax: UNVERIFIED until checked on site. EMIR-E522: the
  split matched no instance pin. `emir run --netlist` without the loop
  follows the same rule: Spectre runs only with `--run`, as in `emir
  simulate` (it ran at once before).
- **`emir next` with no process file** (EM data entered by hand, above) plans
  `process-new` and `process-check` first, then `choose-mode`, whose commands
  carry `--process` with the file being entered; with a process file the choice
  comes first, as before (`tests/test_merge_interplay.py`).
- **emir verify-sim** has a self-consistent case: the sample rail with
  resistive loads, against the closed-loop answer worked out by hand
  (seven checks; 154 in all with the dev case, passing on Xyce and ngspice).
- **Measured**: under Waveform self-consistency, below. The original development
  site's +1.53 % to -0.05 % stays, labelled as a measurement from elsewhere.
- **Example 12** (`examples/12_self_consistent`): open loop, self-consistent
  loop and RCC on a block worked out by hand, with Xyce or ngspice; goldens in
  `expected/summary.json`, `tests/test_example_self_consistent.py`.
- **Agent layer and docs**: AGENTS.md guardrail ("never choose rcc or cc-grid
  yourself"), its decision table, playbooks 01, 02, 05, 06 (and 03, 08), the
  skill and the eval cards (a new card, `02-ask-the-mode`, where the agent must
  ask); docs/method-modes.md and docs/big-blocks.md self-consistency sections
  rewritten, README "Choosing the mode", docs/simulators.md matrix row; every
  example, script and printed command passes `--mode` (a test scans the docs).
- `--vdd` is checked against the testbench's source with Xyce, ngspice and HSPICE
  (EMIR-E523), and read from it when not given (EMIR-I521); `emir next` reads it
  the same way.
- Spectre: a netlist without the split (it names no pin's PWL file, or a
  split node feeds no pin) is refused before Spectre runs, and a pass that
  fed a droop but moved no pin current stops the loop (EMIR-E524).
  The droop files (the manifest `droop.scs` and each pin's `.pwl`) and each pass's
  result (`pass<k>.raw`) go to
  `emir_out/selfconsist/<net>/`, never beside the netlist; a file emir did not
  write there is not overwritten (EMIR-E526). The stub says to move a
  bulk on the rail with its pin.
- `--cc-sim` or `--currents` with `--self-consistent` is refused (EMIR-E525).
- `emir next`: the choose-mode routes keep `--netlist` and `--vdd`, and each
  carries its MCP tool call; the self-consistent route plans `emir
  verify-sim` for its simulator; the run comes after the Spectre split stub; a
  licensed plan's `--run` step is `needs_human` with its `licensed_tool`.
- The texts say what the loop does: it feeds each pin its droop waveform, brings the
  device currents to RCC's and leaves the grid's own capacitance (the EMIR-W520 hint,
  the big-block note, `modes.ROUTES`, the report, docs/method-modes.md); a load that
  follows its supply lands on rcc when the block's other rails are ideal or closed in
  the same loop. The usage lines name `--mode`.
- **The self-consistent loop never overwrites a file it did not write.** Each pass
  stamps its result (`pass<k>.raw.emir`) before the simulator writes it; a `pass<k>.raw`
  (or its `.log` or `.names`), or a simulator output the pass replaces (`sc.raw.log`;
  HSPICE's `.lis`, `.st0`, `.pa0`, `.ic0`, `.mt0`), without emir's stamp is refused
  with EMIR-E526, for Spectre's result directory and the SPICE simulators' files alike,
  and is never removed. The split testbench and the split copies of its includes are
  listed in emir's stamp beside the deck (`split.<sim>.cir.emir`): one of those files
  there that emir did not write is refused (EMIR-E526) before anything is written, and
  every pass's result (up to --max-passes) is checked before pass 0 simulates.

#### Waveform self-consistency

- **The self-consistent loop feeds each pin its droop waveform** (`emir run --mode
  cc-grid --self-consistent`; `emir/tdloop.py`, `emir/pwl.py`). Every pin on the net,
  idle ones included, gets the droop the DSPF's grid gives over the whole transient --
  the R-only grid's, or the RC grid's when the DSPF has capacitance on the net (backward
  Euler, each capacitor to ground; an R-only DSPF warns, EMIR-W544) -- as a PWL on one
  time grid shared by every pin of every net closed (0.3 x the loop's tolerance, at
  least 0.05 mV; at most max(1 500, 0.3 x pass 0's time points) a pin, EMIR-W542 when
  that cap coarsens it; landing on the stimuli's corners). Pass 0 is the open loop
  exactly; after it the plain update (scaled to 15 % of the supply when the open loop's
  droop is above that), then the companion update: each pin's device fitted as g + C
  d/dt from the last two passes (clipped at >= 0, frozen near convergence or when a new
  fit explains less) and the grid solved by backward Euler with it, streamed in time
  chunks; at the fixed point the companion cancels. An average-droop loop measured
  during development over-corrected switching CMOS (the governing branch's average from
  +1.34 % to -2.02 % of RCC at 4.1 % droop) and left most of the rms and peak error;
  `examples/06_big_block/results/selfconsist.csv` keeps that record and
  `docs/images/selfconsist.png` draws it.
- **Stopping, the guard, KCL.** Converged when the droop the currents produce is within
  max(`--tol-rel` x the largest droop, `--tol-mV`) of the droop the pass simulated (1 %
  and 0.2 mV by default; `--max-passes` 10, pass 0 included) with the governing branch
  moving less than 0.5 %; or settled, when on two passes in a row the governing branch,
  every reported item and every device moved less than 0.5 %, with the residual under
  5 % of the droop, not growing, and the PWL not coarsened past the tolerance (the
  summary's `at_pass_limit` says when that was the last pass allowed); EMIR-I520 says
  which. Two growths in a row or a droop above half the supply (a ground net alone: half
  the testbench's largest supply, or three times the open loop's droop) report pass 0,
  the open loop (EMIR-W406); so does the pass limit (EMIR-W407), and a later pass whose
  simulation runs past max(20 x pass 0's time, 60 s, `--sim-timeout`) or fails
  (EMIR-W543; `--sim-timeout` is pass 0's limit). The window is chosen at pass 0. Every
  pass checks the split pins' currents against the rail source (EMIR-W541). A transient
  saved from a start time above 0 warns (EMIR-W546).
- **The simulator's processes and the loop's files.** A simulation stopped at its time
  limit (or by an interrupt) is stopped with its whole process group, a site's launcher
  script and the simulator it starts alike (`emir.subproc`). EMIR-W543 holds one line of
  the simulator's error and the log's path, and says "just slow" only of a time limit.
  `--droop-file` naming a file emir did not write (the testbench, the DSPF, a file of
  the person's) is refused (EMIR-E526) before anything is written, as at the default
  place; pass 0 removes the later passes an earlier, longer run stamped (never a file
  emir did not stamp).
- **Every terminal on the net moves**: a device terminal on it that is no DSPF pin would
  stay on the ideal rail, and is refused (EMIR-E540).
- **Several nets**: `--net VDD,VSS` closes both rails in one loop (Xyce, ngspice, HSPICE;
  each rail read from the testbench, or given per net, `--vdd VDD=1.2,VSS=0`; EMIR-E541
  for one `--vdd` with several nets). A net named twice is closed once.
- **Constructs**: Xyce, ngspice and HSPICE get an include of the split's sources with
  inline PWL, in the block's subcircuit (confirmed on Xyce and ngspice; HSPICE
  UNVERIFIED); Spectre's split is one `vsource type=pwl file=` per pin (`--split-stub`;
  UNVERIFIED). `docs/simulators.md` tabulates them.
- **Numerics**: a testbench reltol above 1e-4, or unset, warns (EMIR-W540, Spectre's
  route too; `emir verify-sim`'s own self-consistent testbench sets 1e-4); measured,
  Xyce's default 1e-3 moved the converged loop's governing branch by up to 0.45 points.
  The loop's time base holds every stimulus corner at least half a step from the one
  before (two nearest one point of the base are both kept), and the RC grid's capacitance
  takes each actual step of it. The loop's codes are area 5's x40 decade (EMIR-E540,
  E541, W540 to W546). Example 12's testbenches set reltol 1e-4.
- **Scoring**: from an RC DSPF the loop feeds back the RC grid's droop, and `--dynamic
  --em-basis decap-aware` scores the converged currents' branches through the RC grid;
  the default basis with `--dynamic`, worst, takes per segment the larger of that and
  the R-only grid's current (`self_consistency.scoring.grid` `max(R-only, RC)`:
  conservative, it keeps the R-only grid's high readings); without `--dynamic` they are
  scored on the R-only grid, which misses the current the grid's capacitors carry, and a
  converged loop says so (EMIR-W545, the summary's `self_consistency.scoring`, the report,
  and a next step with `--dynamic` and the block's period, a loop that converged on its
  last allowed pass included; not after a loop that converged at pass 0, nor when the
  pins' currents are constant). On a run without `--period` that step is a plan that
  asks for the period: `emir next --run-summary` when the run wrote a summary (its
  re-run keeps every option of the run), else `emir next` with the run's inputs, whose
  reason names the run's own loop options to give again.
- **Decaps**: the capacitor devices and MOS decaps the process file's `cap_models` size
  are fed back with the grid, each MOS decap behind its access resistance, as `--dynamic`
  models them (`self_consistency.feedback.decaps`); a decap the testbench simulates
  (its pin fed by the split) is not added again, there or in the decap-aware EM after the
  loop. One `cap_models` cannot size is left out (EMIR-W547: the devices read low, the
  governing branch high, its straps are not checked). A capacitive pin's or strap's
  average current (about zero) no longer keeps the loop from settling. Measured
  (`examples/13_decap_aware/results/decap_loop.csv`, `decap_loop.py`; Xyce, synthetic,
  example 06's 4.1 % droop mesh with 8 decaps of 5 pF behind 2 ohm): fed back, every
  branch class, and the straps' rms and peak, within 1.2 % of RCC on the decap-aware
  basis and the devices within 0.05 %, in 6 simulations; all 8 left out, the devices
  -5.87 % to -1.00 % and the governing branch +13.90 % rms and +31.94 % peak; one of
  them simulated by the testbench, the loop settled in 11 simulations at `--max-passes
  16` (at the default 10 it stops at its pass limit, EMIR-W407), that decap's peak
  -5.94 %.
- **How a run says how it ended**: the console, `emir report`, the HTML provenance row
  and the chart's subtitle say converged, settled or which way it stopped, the pass
  reported and the residual against its tolerance; a loop that reports pass 0 is
  reported as the open loop, with which way it reads as the loop measured it: LOW on a
  load whose current rises as its supply falls (what makes a loop diverge; the report
  and EMIR-W406 say so), high where pass 1 drew less. On the worst basis the report
  calls the answer conservative, not accurate; with decaps left out (EMIR-W547) neither.
  After a loop, `--dynamic`'s RC droop and decap-aware EM use the reported pass's
  currents, and say so.
- **Planning the loop**: `emir next` plans the several-net loop its own choose-mode
  offers (`--net VDD,VSS`; `--net VDD,VDD` closes VDD once) and keeps the report step
  after such a run; `--dynamic` planned without `--period` asks for it (the run step
  waits on `<period>`). `emir next --run-summary` plans how the loop ended, as the run's
  own next steps say it: more passes (a loop stopped at its pass limit, or converged
  only on its last allowed one), longer passes (a pass's time limit), `--dynamic` with
  the period (EMIR-W545); its re-run commands resolve the run's relative paths against
  the summary's `cwd`, so they run from any directory.
- **Measured** (`examples/06_big_block/results/selfconsist_waveform.csv`, Xyce,
  synthetic): the governing branch within 0.14 % of RCC at 0.35-0.55 % droop on one net
  up to 432 nodes at reltol 1e-4, in eight or four phases (open loop rms +3.5 to +8.5 %,
  peak +6.8 to +19.3 %; VSS closed with VDD on 264 nodes, its window from 0: peak +0.45 %), +0.01 /
  +0.34 / +0.78 % at 4.1 % droop (open loop +1.0 / +34 / +70 %), +1.31 % peak at 7.9 %, in
  4 to 10 simulations; each device's peak within 1.3 % with 2 fF a node; the same on ngspice and
  over a 40 ns transient. What remains is the grid's own capacitance: minor branches
  within 3.1 % on the 32-inverter meshes of 132 and 432 nodes in eight phases (4.7 % in
  four), 11.3 % on 1 632, -4.1 % to +5.1 % with 128 inverters at 4.1-7.9 % droop, and
  within 0.3 % of RCC without the grid's capacitors (2.2 % at 4 % droop); with 20 fF of
  grid capacitance a node an R-only DSPF's loop read the devices' peaks up to 4.8 % low,
  the RC DSPF's within 0.1 %, its minor branches -19.5 % to +25.3 % scored on the R-only
  grid and within 0.52 % through the RC grid (decap-aware; the open loop's -7.1 % to +90.1
  %). On
  these small grids the loop costs more than RCC; a
  big-block speed-up is an extrapolation. Example 06 has `selfconsist.py`'s waveform
  cases, mesh options (VSS, phases, grid capacitance, length), the RC-grid scoring of an
  RC DSPF's passes (`*_rcs`), a golden at N = 10 and the figure
  `docs/images/selfconsist_waveform.png`; example 12 lands on RCC to 1e-9 in 3
  simulations.

#### One chart style and one colour scale

Every picture EMIR draws, in one style and on one colour scale.

- One colour scale for every renderer, `emir/style.py` (no plotting library, the
  standard library only at import): below the warn band a blue ramp by the
  ratio's log10 from 0.001x (#104281) to the warn fraction (#b7d3f6); the warn
  band (the warn fraction to 1x) amber #fab219; 1x and over, **or not a number**,
  red #d03b3b; no limit stated grey #6b6b6b; violation markers #ff3030. IR drops
  run on the same ramp from 0 to the largest drop shown; a drop that is not a
  number is red, never a shade. Its legend names every colour (`style.legend`),
  and its numbers print as "not a number", never as 0 (`style.num`).
- `emir/chart.py`, the one PNG renderer: the layout drawn on black and dimmed
  (`emir/underlay.py`: KLayout with the PDK's `.lyp` found under `$PDK_ROOT`, else
  a neutral grey per layer; gdstk's polygons without KLayout), every checked
  element filled by its ratio (the worst drawn last) or by its drop (each net's
  worst node ringed, the three worst labelled), violations outlined red, the cell
  outline dashed, the axes in um; beside it the verdict, the worst items numbered
  as on the map with where they are and their fix, one row per net (worst EM, at,
  IR worst), what was not checked and the legend; "Screening, not sign-off." and
  the research disclaimer at the foot of every chart. `emir/scene.py` builds what
  a chart shows from a run's solved nets.
- `emir run --html` also draws the run's chart, `emir_chart.png` (and
  `emir_chart_light.png`): every net, EM above IR, from the DSPF's own coordinates on
  black, or over the GDS (`--gds`). The index page shows it; the run summary lists it
  (`charts`, `emir schema run`), and `emir report` names it (a "Chart" line;
  `charts`) for the agent to attach beside the text.
  New codes EMIR-I720 (no matplotlib: no chart, the HTML pages still written),
  EMIR-W720 (a drawing failed: the run stands) and EMIR-I721 (no klayout or
  gdstk: the chart without the layout under it).
- The HTML net pages draw each net on black with the scale, the worst on top,
  violations outlined, a toggle to the IR view (the worst node ringed), the
  cursor's x, y in um, and the full legend.
- The Virtuoso Explorer's packets `EMIR_B0..11` (`emir_display.drf`, regenerated)
  are the scale: the ramp in ten steps, the warn amber, the failure red; the EM
  bands follow the ratio's log and the process file's warn fraction
  (`layoutview.em_bands`), and the viewer data carries the bands and the palette.
- The figures of the docs and the blog drawn by `scripts/make_figures.py` from
  the public examples only: the hero and the covers (with the research line), the
  HTML report screenshot (with its research footer), and carousel pages 1, 2 and
  9 (page 3 counts the registry's codes). docs/concepts.md says how to read the charts;
  playbook 07 and the skill say to attach the chart.
- With the process data entered by hand (above): the run's chart names the layers
  and vias whose limits a person typed and each
  value with no source (EMIR-W320, `scene.limits_notes`); the HTML net page's
  "limits from" row shows NO SOURCE in the warn colour. The self-consistency
  figure (above) draws its series on the same blue ramp, with the
  research line under it.
- The run chart and the HTML net map apply the layer's width loss and a via's
  cut source; a wire with no width is not checked on
  the chart, as in the report. The chart's "Not checked" line is built from the
  run's `not_checked`. Hand-entered
  limits' notes and suspect units are drawn under it, never dropped, NO SOURCE
  and SUSPECT in the warn colour.
- The net page's worst-node label stays inside the map; above MAX_SHAPES the
  chart keeps the worst by IR drop beside the worst by ratio; a chip-level run
  (`--abstract --html`) draws its chart.
- Example 05's flat DSPF moves each block element's box with the block. The
  banner's notes sit under its wrapped legend; the scaling, streaming,
  accuracy, flow, chip-level and transcript figures and carousel pages 5 and 6
  carry the screening line (and the figures the research line).
- **The chip-level chart's block models.** An abstract's port-to-port element is drawn
  dotted, off the colour scale, with the legend key "block model (its abstract)" and a
  note naming the blocks: its EM is checked in each block's own abstract run. The HTML
  net page names it the same way.
- **Open loop or self-consistent, on the chart.** The run chart's subtitle says, per
  net, "cc-grid, self-consistent (converged in N passes, tol X mV)" (or settled, the
  tolerance the last pass was held to), or "cc-grid, open loop (droop P% of the
  supply)"; an open loop above about 2 % (EMIR-W520) and a loop that reported pass 0
  (diverging, EMIR-W406; at its pass limit, W407; a pass's simulation past its time
  limit or failed, W543) are alerts in the warn colour under "Not checked".
- Chart cosmetics: the Virtuoso viewer's colour bars label their edges, the IR bar with
  its largest drop and its unit ("54.0 mV"), the EM bar from 0x; equal ratios are
  numbered left to right; no worst-node ring where the drop prints as ~0; the HTML net
  map keeps a 200 px height and puts its toolbar above the map on a phone. A callout's
  leader never runs under another callout's box (`chart.callout_crossings` checks a
  drawn figure).

#### Decap-aware EM from the dynamic solve (`--dynamic`, `--em-basis`)

In the cc-grid route the simulation's rail is ideal, so a decap across it carries
nothing and every edge of the load current is charged to the grid from the feed.
`emir run --dynamic` integrates `C dV/dt + G V = I(t)` for the droop; the same
steps give every grid resistor's current, and so its EM with the decaps.

- **Three EM bases, the person's choice.** `--em-basis worst` (per segment and
  metric the larger of the two; the default of a cc-grid `--dynamic` run, one
  constant in `emir/em_basis.py`), `decap-free` (the static superposition) or
  `decap-aware` (rms and peak from the dynamic solve; avg is the static one in
  every basis: a capacitor carries no DC). The verdict, `max_ratio`, `worst`,
  `worst_items`, the notes and every consumer (console, HTML, viewers,
  `--fail-on-violation`) follow the basis; every basis's verdict, worst ratio and
  segment is in the summary (`nets[].dynamic_em.bases`). A basis other than the
  default warns (EMIR-W437); `decap-aware` or `worst` without `--dynamic` is
  refused before the input check (EMIR-E430, which `--force` does not pass); rcc
  measures the decaps (`used: measured`, EMIR-I432). A run without `--dynamic` is
  labelled `basis decap-free (no --dynamic)` (`em_basis.source` `no-dynamic`).
  With `--dynamic`, a net whose decaps' straps carry more than their limit FAILs
  under the default basis.
- **The numerics, measured against exact references** (`tests/test_decap.py`,
  `tests/test_decap_steps.py`): per-step branch currents `g (V_a - V_b)` reduced to
  running sums (memory O(branches); the input is interpolated at each step, never
  stored resampled); the sums exact for a current linear between points (not the
  trapezoid of x^2, which reads steep flanks high). The steps
  (`dynir.StepSequence`): the default grid is a whole multiple of the result's
  intervals (`--dyn-steps` four per interval of its samples), so evenly spaced
  samples are grid points; where the grid's chord misses a waveform feature (an
  edge, a spike's tip) by more than 1e-3 of its pin's own scale, the interval is
  halved dyadically down to the feature, each level one factorisation (up to 16
  levels within 1 GiB of factorisations); the step after a change of slope is
  refined while the trapezoidal rule could ring on a decap faster than it (the
  decaps' stiff time constant seen from each pin, two back-substitutions for all
  pins), and the steps grow back at most twofold every three. A decap lump is
  folded onto its node only when its access R C is below a tenth of the finest
  step taken. rms is measured like with like (each pin's AC rms on the steps
  against on the simulator's own samples, both exact for a linear segment): 1 to
  rounding when the steps resolve the waveforms. Where they cannot (a feature kept
  below 99 % of its own height at the finest level or past the refinement's
  budget, or the feed's current above the load's own on the same steps: the rule
  rang) decap-aware EM is not computed (EMIR-W434) and the run gives the
  `--dyn-steps` that resolves it, never fewer, up to 2 000 000
  (`decap.DYN_STEPS_MAX`; past that none, and `emir next` asks the person). A
  feature whose charge and energy beyond the grid's chord are below 1e-6 of its
  pin's per period (`dynir.NEGLIGIBLE`) never counts: it is said (EMIR-W429,
  `numerics.features_ignored`) and decap-aware EM is computed; its own peak,
  shorter than the finest step, is not in the decap-aware currents. W411 judges a
  waveform feature's height kept on the steps. The averages are checked by
  `G Vbar = Ibar - C (V_N - V_0)/T`. The closed forms of a feed R, wire C and a
  decap with series R within 1e-4 (`emir validate`), the folded decap and a decap
  on its own strap within 1e-3, the pulse-train ladder within 0.2 % (rms) and
  -0.5 % .. +1 % (peak) of its matrix-exponential reference; a one-state decap's
  feed and strap within 0.5 % of their exact periodic steady state under a 10 ps
  spike in a 1 us window (453 ps steps), 20 ps edges against a 24 ps decap (144 ps
  steps), a lump whose 3 ps R C the step crosses, and a record sampled only at its
  corners. The droop (`dynamic`) comes from the same integration.
- **The periodic steady state** carries the wrap: a pass restarts from the last
  sample's injection, so the trapezoid's (-1)^n mode decays on a node without
  capacitance (an R-only grid with MOS decaps whose window ends differ by 0.1 %
  settles in at most 3 passes); a slow pass restarts from its corrected mean
  (each mode then contracts by at most 0.30 a pass: a decap time constant five
  times the window, which a plain restart contracts by only e^-0.2 = 0.82,
  settles in at most 8); the averages are checked to 1e-4.
- **The decap model**: MOS decaps on the solved net's drain/source side attach
  half at each channel end; coupling caps to signal nets are not counted as
  decap in the EM solve (EMIR-I431); decap-aware leans on the decap model
  (EMIR-W438, also when a MOS decap has no access resistance: an ideal capacitor,
  the feed path low and the straps high; it names folded lumps) and reads the
  straps of a decap between two resistive rails low (EMIR-W439, measured in
  example 13).
- **The cap models.** `cap_models` has one check, EMIR-E317: a value the decap
  model cannot use (not a number, a negative C or R, an entry with no
  capacitance, a fit frequency not positive, a fit range not low to high from 0,
  an entry that is not a mapping) fails `emir check` and `emir process check`
  (a fitted access R negative at a decap's own geometry: `emir check
  --dynamic`), and is refused by the dynamic solve under `--no-check` (it reads the file's own values; the engine
  keeps only numbers). `esr_w` and `esr_l` are signed. EMIR-E308 covers the
  layers and vias; the process schema's minimums are E317's. A value E317 refuses
  is not also a unit slip (EMIR-W321); `esr_a`, `esr_w`, `esr_l` and
  `esr_fit_freq` have units and plausible ranges, and `emir process new` offers
  their slots.
- **Not computed, said with its own code**: no feed (EMIR-W430), the reference not
  the feed (W431), not at periodic steady state (W432), a window not whole
  periods (W433), a step too coarse (W434), and decaps the cap models cannot size
  (a `cox: null`, as `emir process new` writes it: EMIR-W409, W410, the net's
  `em_basis.decaps_not_sized`); `em_basis.fallback` says it machine-readably.
  Under a basis that needs decap-aware EM (worst or decap-aware) the net's
  verdict is then decap-free, NOT the basis asked for: the notice says so, the
  summary's top-level `em_basis` carries `used`, `honoured: false` and
  `not_computed` (net, reason, the `--dyn-steps` that would do),
  `--fail-on-violation` exits 1 and says why, the report's headline and its
  Decap-aware EM section say the verdict is decap-free there, and `emir next
  --run-summary` plans the fix as the person's step (`rerun-dyn-steps`,
  `rerun-without-ground`, `rerun-with-period`, or what to ask for in
  `missing[]`, such as a decap's cox). `emir process check --dspf` and `emir
  check --dynamic` warn about unsized decaps before any run. W435: the steps lost
  more than 2 % of a pin's AC rms. I430: computed (decaps on the feed node
  reported apart: they carry nothing).
- **EMIR-W428**: a cc-grid run without `--dynamic` over a net with decap devices
  says that its static verdict gives a decap's straps nothing (`em_basis` on the
  net, fallback `no-dynamic`); `emir next` advises `--dynamic` as the person's
  choice, and after such a run `emir next --run-summary` plans the same run with
  `--dynamic` as their step (`rerun-with-dynamic`; it asks for the period when
  the run had none, and keeps a basis the person gave). The summary's
  `not_checked.decap_straps_not_covered` lists, per net, the decaps with no
  `--dynamic`, the decaps the cap models cannot size, the segments the
  decap-free basis does not cover (EMIR-W436) and decap-aware EM not computed;
  the report's and the chart's Not checked show them.
- **Agent contract**: `nets[].em_basis` and `nets[].dynamic_em` in `emir.run/1`
  (and `argv`, the run's own arguments), every basis's ratio on each worst item;
  `emir report` names the basis in its headline and the Nets table, adds a
  "Decap-aware EM" section after the Nets table and "The person's choice" when
  the bases disagree (`em_bases`, `em_choices`, `decap_items` in
  `emir.report/1`); `emir next --dynamic --em-basis` plans the run with them (the
  choose-mode routes keep both), and `emir next --run-summary FILE` reads a
  finished run (its inputs from the summary), plans the person's choice
  (`choose-em-basis`, needs_human, with `choices`; `emir.next/1` keeps a route's
  `tool` and required fields beside them) and, with `--em-basis`, the re-run; a
  summary on a chosen basis gets `confirm-em-basis`. EMIR-E520's `emir next` keeps the run's
  `--period`, `--dynamic`, `--em-basis`, `--netlist`, `--sim`, `--vdd`,
  `--self-consistent`, `--html` and `--viewer`. The exit-1 text (the registry,
  `emir --help`, the contract, the CLI reference) names an EM basis asked for and
  not computed. Over MCP an `em_basis` other than the default needs `confirm`
  (EMIR-E590): a choice that loosens the verdict is the person's; so do `dynamic`
  with `ground`, without `period`, or with `dyn_steps` below 2000 or above
  200000; every go-ahead list names these gates. The HTML index and net page, the
  per-net JSON, the viewer data (beside its EM bands and palette) and KLayout
  markers, the Virtuoso data's load message and the report's Worst items name the
  basis the numbers are on; the run chart's subtitle names it beside the mode,
  and on a `--dynamic` run the chart says its IR panel is the average drop, with
  the dynamic droop's peak in a note. AGENTS.md (a guardrail: never choose or
  change the basis), the skill and playbooks 02 and 07 say so; eval card
  `decap-make-it-pass` (decap-free CLEAN, worst FAIL: a looser basis,
  `--ground`, `--dyn-steps` or a run without `--period` fails it).
- **Example 13** (`examples/13_decap_aware`): a small decap mesh screened once per
  basis offline, and RCC against decap-free and decap-aware on Xyce (N = 20, 40,
  70; no decap, 2 ohm and 20 ohm decaps; two resistive rails), with the
  solver-only reference; `results/decap.csv`, `decap.md`; a row in
  `docs/validation.md`.
- `emir validate` has a sixteenth case, decap-aware EM against its closed form
  (`--quick` leaves it out with the other two slow transient cases).
- **What it costs**: CPU time of `emir run --dynamic` with decap-aware EM against
  the droop-only integration (`examples/13_decap_aware/results/cost.md`,
  `cost.py`, one thread, a shared machine whose load moves it): 1.25x to 1.85x on
  the N = 200 Xyce mesh (40 k nodes; 103.6 -> 129.4 s the least of two runs each,
  five back-to-back pairs 1.45x to 1.85x), 51.5 -> 65.8 s (1.28x) and 65.5 ->
  79.8 s (1.22x) on streamed meshes of 10 k and 90 k nodes with 2000 pins; peak
  memory the same on the first two, 846 -> 1020 MB on the last.
- With `--self-consistent`, `--dynamic`'s droop and decap-aware EM come from the
  currents of the loop's reported pass (each pin fed its droop waveform):
  the report, the chart's note, the console line and `dynamic.source` say so, and say
  open loop only when pass 0 is reported.
- `emir next` refuses `--dynamic`, and an `--em-basis` that needs it, with `--abstract`,
  as `check` and `run` do (EMIR-E811, in `missing[]`). After a chip-level run, `emir next
  --run-summary` reads the run's `--abstract`, `--place` and `--align` back from its
  summary, plans the back-annotation from the port voltages it wrote, and advises no
  re-run with `--dynamic`.
- New codes: EMIR-E317, E430, W428, W429, W430 to W439, I430, I431, I432.

### Changed

- **cc-grid on a signal net.** The docs, `emir next`'s choose-mode and example 08 say
  that the open loop read the governing branch's rms and peak high on the supply nets
  measured, not on a signal net: part of a signal net's current charges its own wire
  capacitance, which cc-grid's branches do not carry. Example 08 scores its gate net
  vb as well (rms and peak; reported, not bounded): the loop read it rms -6.55 % and
  peak -4.68 %, the open loop +1.34 % and +5.31 %.
- EMIR-E212 is "no cell in the layout": the GDS holds no cell. Its title and hint
  spoke of conductor geometry and the process file's `gds:` numbers, which no path
  reads.
- **Licence: MIT** (`LICENSE`, "Copyright (c) 2026 Vijay Gupta and contributors"),
  declared as a PEP 639 SPDX expression (`license = "MIT"` in `pyproject.toml`, `setuptools>=77`), and in
  `CITATION.cff`, the SPDX lines of every SKILL, display-resource and template file,
  README, CONTRIBUTING (with the DCO) and THIRD_PARTY_NOTICES. `NOTICE` holds the
  copyright line, the disclaimer, the derived code's credit and the trademark note. The open PDKs' data in
  `pdks/` keeps its Apache-2.0 licences.
- No document says a licensed tool (Spectre, HSPICE, Virtuoso, PVS, Quantus, OCEAN)
  was verified anywhere. Each piece is UNVERIFIED until checked on site by the local
  agent where a check exists (`emir verify-sim` for the simulators; the headless SKILL
  self-test and `LIVE_TEST.md` for Virtuoso; the LVS MATCH gate and `emir check` on the
  DSPF for PVS and Quantus), and checked by hand otherwise. Measurements keep their
  provenance ("measured at the original development site on ..."), never as a support
  claim.
- The code derived from the Skycadeda Analog Framework (SAF) and from Skyforge, both
  Copyright (c) 2026 Skycadeda and licensed under MIT by their copyright holder, is
  credited in `NOTICE` and THIRD_PARTY_NOTICES.md (the wheel carries both), and in
  the docstring of each module that holds such code.
- EMIR-E408 (binary PSF) gives the conversion command (`psf -i <file> -o
  <file>.psfascii`, Cadence's `psf` utility); a directory's `.psfascii` file is read
  before its binary `.tran.tran`. `emir.collecting(propagate=False)` keeps a command's
  internal runs' notices to themselves.
- Resistance follows each layer from the DSPF's own temperature
  (`*|OPERATING_TEMPERATURE`, else the process file's `temp_reference`) to the
  analysis temperature, by that layer's TCR: f(T_a)/f(T_x), f(T) = 1 + tc1
  (T - T_ref) + tc2 (T - T_ref)^2. Vias and contacts are not scaled (the
  process file gives them no TCR). It once applied one mean factor to every
  resistor, vias included, from `temp_reference` whatever the DSPF said: the
  sample at `--em-temp 150` now reads 30 x 1.4 / 1.2 + 24 = 59.0 mV (was 54 x 1.4
  = 75.6).
- The self-heating budget is the person's `--delta-t`, never raised: a layer
  whose rms limit sqrt(jmax_rms_k x delta-T) is below its avg limit gets
  EMIR-W301 (rms governs) instead of a silent rise of its delta-T, which
  loosened the rms check.
- A fix is strictly past the limit: a width rounds up to the next 5 nm above
  w x ratio, and a via array needs floor(n x ratio) + 1 cuts (at exactly the
  limit the ratio is 1.00x, still a FAIL).
- Install hints name a command that works for the install: from a clone
  `pip install -e '<clone>[extra]'`, from the offline bundle its own Python's
  pip, otherwise the packages themselves; EMIR is not on PyPI, so no hint asks
  pip for `emir[extra]`. A missing core package (numpy, SciPy, PyYAML) says
  so. `bin/emir` exits 3 (a missing tool or package) when it finds no Python
  with them.
- The docs, the playbooks and the agent instructions no longer call an
  open-loop cc-grid result "an upper bound": above about 2 % droop (static, or
  dynamic when `--dynamic` ran) re-run self-consistently or report it as open
  loop, its rms and peak conservative on the governing branch in every case
  measured, its avg and IR not always (-0.17 % at 4.19 % droop in four
  phases), not a bound on every branch. rcc on a small net is "fast enough
  (seconds)", not "as fast"; the 500-node and 50 000-node thresholds are policy, not measured
  crossovers.
- Figures (`scripts/make_figures.py`): the agent transcript is labelled as
  scripted ("the plan, run as printed"), not a session; the accuracy figure
  has two panels (avg and IR on +-3 % with the guard band; rms, peak and
  dynamic IR); the streaming figures and the carousel quote the default run
  (11.7 s, 804 MiB); carousel page 2 zooms on each failure, page 5 adds the
  speed-up and the result sizes. The docs pages pair each figure with its dark
  variant.
- AGENTS.md and the playbooks write "EMIR" for the tool and `emir` for the
  command (`tests/test_agent_layer.py` checks it).
- `docs/big-blocks.md` opens with a summary, splits its benchmark table in two and
  uses MiB/GiB throughout.

- AGENTS.md: run `emir report` before every answer; pass every input to
  `emir next`, DC currents included; where each command writes; quote emir's
  derived numbers, never your own. Playbooks 01, 02, 05 and 09 list the report
  as a success criterion; playbook 03 says the back-annotated IR is measured
  from the pad; the preflight card takes `info` and `selftest` in either order.
- `pyproject.toml`: version read from `emir.__version__`; extras `report`,
  `klayout`, `mcp`, `all`, `docs`, `dev`; pytest markers; ruff configuration.
- The sample moved into the package, `emir/data/sample/`, and is loaded with
  `importlib.resources`; the wheel no longer installs a top-level `sample/`
  directory into site-packages.
- The sample (`rail.dspf`, `process.yaml`) is replaced by one built only from
  invented round numbers: twelve 5 um x 0.75 um Metal1 segments at 0.15 ohm/sq
  (1.0 ohm each, at 100 C), contacts at 24 ohm per 0.13 um cut, limits of
  3.2 mA/um and 0.75 mA/cut (`imax_peak` >= `imax_avg`), EM and extraction
  temperature 100 C, and synthetic GDS numbers (Cont 100/0, Metal1 101/0,
  Poly 102/0). Its `Poly` layer makes the `Cont` via's `below:` a layer that
  exists, so the sample raises one WARN (that it is illustrative). The two
  files agree (24 ohm per cut in both; 0.125 ohm/sq at 50 C with tc1 0.004 is
  the DSPF's 0.15 ohm/sq at 100 C). The demo shows FAIL, WARN and OK bands, a
  wire-limited spot near the feed and a via-limited one at the far end, where
  MP3 lands through a single cut.
- `emir gui-install` writes to `<clone or bundle>/skill` when run from a source
  tree or the offline bundle, else to `~/.emir/skill` -- never into
  site-packages. `--dir` still overrides. The generated header names the real
  `emir_main.il` path.
- `bin/emir` and `scripts/selftest.sh` resolve symlinks, so a link on `$PATH`
  works.
- Viewer: no default mark layer. Without a site `mark_layer`, the Explorer
  paints on the current entry layer.
  `layoutview.export(lib=...)` defaults to `None`. The colour ramp is EMIR's
  own palette: ten steps, piecewise linear through the HTML report's colours
  (`layoutview.BAND_RGB`), from which `emir_display.drf` and the legend PNGs
  are generated, so the report, the legend and the layout share one palette.
  The Explorer's unused colour table (`emirBandRGB`) is gone.
- `emir import-ict` prefers any long-wire branch (`L > ...`) of an ICT limit,
  not only one specific threshold.
- `Process`: a layer without `min_width` falls back to 0.1 um (mesh flow only).
- `scripts/build_bundle.sh` (CI only; refuses to run elsewhere unless
  `EMIR_ALLOW_LOCAL_BUNDLE=1`): builds the wheel in a throw-away venv (no
  `pip --user`), verifies the standalone Python against a pinned SHA-256, has
  no fallback downloads, isolates itself from the host's user site and pip
  configuration, writes no `.pyc` files, tars with `--owner=0 --group=0
  --numeric-owner`, and writes `SHA256SUMS`. The staged tree is made
  relocatable before it is packed: pip's `direct_url.json` records are
  removed and the console-script launchers' shebangs (which named the build
  machine's interpreter) become a shell trampoline to the `python3` beside
  them; the build fails if any text file still names the build directory.
- Measurements quoted from the original development site are labelled
  "measured at the original development site on <the test object>". SKILL
  comments state EMIR's own observations.
- `em_tref` is required in the process file: there is no default EM
  temperature (a guessed one shifts every avg ratio). `emir check` FAILs
  without it.
- The mesh report's footer says the EM limits come from the process file at
  the run's temperature.
- Ruff baseline (E9, F, B006, B904, UP; line length 100): unused imports and
  placeholder-free f-strings removed, `raise ... from` in `taps.load_taps`.
- `--mode auto` is gone (1.0.0 chose by node count, so a CC result on a net under
  2000 nodes picked `rcc` and failed): the mode is the person's choice, `--mode
  rcc|cc-grid` (EMIR-E520; "The mode is the person's choice" under Added).
  `--rcc-nodes` is accepted and no longer used.
- `emir run` without `--net` analyses every net (it only listed them). With
  nothing to drive the analysis it lists the nets, prints `verdict: NOT
  ANALYSED` and exits 2 (it exited 0, which a script or an agent could read
  as a clean result). Every analysis run ends with an `overall verdict:` line
  over all its nets.
- `--html` and `--viewer` always take a directory (they had an optional value
  that could swallow the DSPF argument).
- The DSPF summary line goes to stderr when stdout carries save lines or the
  rail-split fragment, so `emir run ... --save-branches > saves.scs` is SPICE.
- `--save-list` honours `--save-prefix` and covers every net without `--net`.
- The mode label is the same in every output: `dc` for `--currents` runs (the
  HTML said `cc-grid`), `cc-grid` and `rcc` otherwise.
- The HTML report needs no matplotlib (`emir.style` is imported only by the
  mesh flow's map pages); `emir selftest` always writes it.
- Errors reach the terminal as one line (`emir: ...`), never a traceback;
  `EMIR_DEBUG=1` shows it. Out-of-memory suggests `--scalars`, a missing
  package names the extra to install. `emir nets ... | head` is quiet.
- Per-net lookups (`net_resistors`, `net_taps`, ports, layer tokens) use one
  index: `emir nets` on a synthetic 3000-net DSPF went from 2.0 s to 0.04 s.
  Simulation-result name matching is indexed too, and `emir run` reads the
  result once for all nets.
- `emir import-ict` evaluates ICT expressions with an AST whitelist instead
  of `eval` -- numbers, `w`, `delta_T`, `+ - * / ^` and `sqrt` of one
  argument, nothing else, and a non-finite result is refused -- keeps curated
  (unflagged) via `imax_rms`/`imax_peak`, and writes only the ICT's file name,
  not its path, into the generated file.
- The sample's contact cut is 0.13 um (was 0.14 um), with cut areas
  0.0338 and 0.0169 um^2: still invented and round, re-checked so that no
  sample number sits near a real process's. The results do not move (the
  extracted cut count governs), and the area route still gives 2 and 1
  cuts.
- `emir run --json FILE` is now `--summary-out FILE`; `--json` stays as a
  hidden alias so existing argument files (the Virtuoso form's included)
  parse. The summary is the `emir.run/1` document `--format json` prints:
  the 1.0 fields keep their names, and it adds the overall verdict and
  counts, the current source, the analysis and EM-limit temperatures, per
  net the mode and why, the governing metric, the IR kind (drop or spread),
  power-net flag, unsaved pins and the worst items with their fixes, the
  `not_checked` section, the input check's warnings, the coded notices and
  the next steps.
- Exit statuses follow the contract: an internal error exits 70 (was 1), a
  missing Python package 3 (was 2), a failed `emir selftest` or `emir
  validate` 70 (was 1). A run refused by its input check raises EMIR-E501
  with every FAIL's code in `details.causes`, unless a required tool is
  missing, which keeps its own code and exits 3.
- Errors read `emir: EMIR-Exxx: <message>`, then the hint and the next
  command; usage errors keep argparse's usage line and are EMIR-E507. The
  checker's table shows each finding's code, and its JSON (`--json-out`)
  gives each finding a `code` and a `command`.
- A DSPF line that does not parse is EMIR-E201 naming the file and line, and
  a simulation result that does not parse is EMIR-E410 (both were
  uncaught exceptions). `import-ict`'s warnings are coded too.
- With `--no-check`, a process file the engine cannot use and `--currents`
  that are not a JSON object are still coded errors (EMIR-E301/E302/E303/
  E315, EMIR-E401, exit 2), not internal ones.
- `--format` help names each command's default (`md` for `emir report`).
- `emir check` no longer lists a `--currents` node as off the net when the
  net itself is not in the DSPF (the net's own FAIL says it, and the node
  list's suggested command could not work); the balance is still checked.
- Every suggested next step runs as printed. A registry command whose
  field the case cannot fill uses its `[...]`-optional form or its fallback
  (`emir pins <dspf> --net <net>` becomes `emir nets <dspf>`), or is not
  suggested; about 30 codes whose only step was `emir explain` of
  themselves now suggest nothing (the hint says what to do) or a real
  diagnostic (`tail -n 40 <log>` for a failed simulation). The check's
  currents findings name the net. `emir run --format json` without
  `--summary-out` suggests keeping the summary in a file and reporting from
  it, not `emir report --from -` alone.
- `emir next` plans only what the check accepts. A mode the result cannot
  serve (`--mode rcc` on pin currents, `--mode cc-grid` on branch currents,
  cc-grid on an instance pin with several DSPF connections) gives the save
  lines and simulation that serve it, plus the advice that the result as it
  is serves the other mode; `--mode cc-grid` with no result plans cc saves;
  split pins plan rcc, or block an explicit cc-grid. With Xyce or ngspice it
  says the save lines are Spectre syntax and adds the names-sidecar step; a
  result with SPICE-style names and no sidecar asks for the sidecar, not a
  new simulation. `--currents` on a one-net DSPF needs no `--net` (as
  `emir run`); `--viewer` without the klayout package says no marker
  database will be written.
- The process-file check FAILs on an unknown key inside a layer, via or
  cap_models entry (EMIR-E316, with the nearest key: a misspelt peak limit
  was silently dropped) and warns on an unknown top-level key (EMIR-W314),
  as the schema does.
- `emir validate --no-skip` without the optional packages is EMIR-E603,
  exit 3 (it was EMIR-E903, exit 70).
- EMIR-E417 (an instance pin at several connections) points to the
  hierarchical flow: its message gives the `emir abstract` / `emir run
  --abstract` route next to the rcc one, its finding's command is `emir next
  --dspf ... --net ...`, and a run's error carries the runnable steps `emir
  pins`, `emir next`, `emir abstract --help`.
- E413, E415 and E416 say when a result names its currents in SPICE style
  and no `<result>.names` sidecar maps them, and show the names.
- `--em-temp` and `--delta-t` have help text.

Features:

- **Chip-level EMIR, hierarchically (`emir abstract`, `emir run --abstract`)**
  - `Dspf.path`: a parsed DSPF knows the file it was read from.
- **Xyce and ngspice for the DSPF flow (`emir save-lines --sim`, `emir probe`, `emir simulate`)**
  - `emir next` no longer plans `Xyce -r <result.raw> -a <testbench.cir>` with
    Spectre save lines, or `emir schema names` as the sidecar step, for a
    simulation it plans. Base tests that pinned those steps now check the new
    ones.
- Within each error-code area the base package has x01-x39, and the magic route the
  x40 decade (area 8 stays the hierarchy flow's).
- `emir run --taps-out` writes one file per net when several nets are screened
  (`<stem>_<net><ext>`, or `{net}` in the path), adds a `net` column, writes
  currents to 12 significant digits, and prefers the PDK conductor layer over a
  raw `$lvl`.
- **Cadence integration (`emir extract`, `agent/cadence/`)**
  - `emir_main.il` holds no path: each setting is read at load from what is set, else
    `EMIR_HOME`, `EMIR_BIN`, `EMIR_WORK`, `EMIR_PROCESS`, `EMIR_LIB`, `EMIR_VIEW`,
    `EMIR_UNSET_LD`, else a default (an empty value is unset). `emir gui-install` also
    writes `emir_site.il`, the only file with this machine's paths, and `.cdsinit`
    loads that (`files.site` in `emir.gui_install/1`); without a clone it writes a
    launcher.
  - The Screening form: a testbench-instance-prefix field; Save lines writes
    `<output>/emir_save.scs` (in the mode the form names) instead of only the log; the
    "Spectre save lines..." menu item opens the form and runs nothing.
  - The Explorer's bands are steps 0..9 of EMIR's ramp end to end (`EM_BANDS` carries
    the step; no `drawing<N>` purpose anywhere); without the display packets every
    band is drawn on the one mark LPP with one CIW line; the packet probe checks
    `isCallable` and, where it exists, `drGetPacket`, and names the colour path.
  - `emir_display.drf` and the docs say: append to `~/display.drf`, never replace a
    project display.drf.
  - Generated SKILL and display-resource files start with an SPDX line, the copyright
    line and "Not a Cadence-supplied file."
  - The offline bundle and the sdist carry `agent/` (the Cadence entry point loads
    from it).
- **Where the features meet:**
  - `emir run --abstract ... --dynamic` is refused with EMIR-E811: the
    top-level solve is resistive (each block's internal dynamic droop is in
    its abstract).
  - `emir mcp` classifies every feature command: `probe` never asks;
    `extract` asks with `run`, and `simulate` and `abstract` when `emir next`
    marks the same step `needs_human`. `emir info` reports the `mcp` package;
    `emir --help` lists `mcp`.
  - `MANIFEST.in` ships every example's inputs and `docs/`.
- `klayout>=0.29` (was `>=0.28`) in the `klayout`, `all` and `dev`
  extras. At the old floor pip installs 0.28.17.post1, where
  `agent/opensource/lyrdb_summary.py` fails (`each_sub_category` of a const
  category); the new `lowest` CI job found it.
- **The documents follow the code:**
  - The playbooks, `AGENTS.md`, `agent/README.md`, `docs/agents.md` and the
    docs pages cite the magic route's codes and the new ones (E592,
    W452, W463, W550, W640); say that `--currents` takes a file, that next
    steps keep the caller's `--format` and mark shell commands
    (`"shell": true`), that `emir info` lists magic and PVS, what
    `emir abstract --top` and `emir next` with unwritten abstracts do, the
    `scalar` and `combined` superposition paths.
  - The playbooks list the magic route's codes and options (09), W806 (03),
    W260 to W262 (05), `--split-stub` (02) and the `--site` file (06).
  - The docs pages show the generated figures (the flow, RCC against CC,
    streamed superposition, accuracy, chip level, the HTML report, an agent
    session), drawn from the code by `scripts/make_figures.py`.
  - `tests/test_docs.py` fails on a link to a file this tree does not have. It also
    holds every emir command the human documents print (`docs/`, the README,
    CONTRIBUTING, `pdks/`, `blog/`, the examples' and integrations' READMEs)
    to the commands and options the parsers have, as the agent-layer test does
    for the playbooks; and a test checks that every repository path the
    package cites (advice, hints, next steps, MCP resources) exists.
- Docs: the status column of the Cadence pages reads "UNVERIFIED until checked on site
  by the local agent (the check)"; the measurements from the original development site
  are labelled as provenance, not support; `docs/validation.md` lists examples 11 and 12.
  SECURITY.md lists the release files under "Also in scope".
- The LinkedIn images, read at feed size (a carousel page about 390 px wide, the cover
  about 552 px): the carousel's map callouts and port labels at 28 px (page 6's chart
  labels at 24 to 26 px), a colour legend on page 2, and "Screening, not sign-off." at
  24 px or more on the pages with a map (1 and 9); the covers have larger callouts, no
  port labels, a one-row colour legend, and the screening and research-project lines at
  18 px. The other pages' footers (sources, and the screening line) stay small print.
- Docs: the chip-level page says what the self-consistent loop does within a block
  (each pin fed its droop waveform).
- `docs/limits.md`: limits per layer and metric now take the design manual's width loss
  (`I_max = J x (W - dW)`, `width_loss_um`) and Black's derating; what is still not
  modelled is named (limit tables by width range, the Blech length, thermal maps). The
  onboarding page says `emir import-ict` keeps an ICT's `(w - dw)` offset as
  `width_loss_um` (it no longer drops it).
- Every example's `run.sh` takes an optional output directory as its argument; the
  environment variable some of them read (`OUT`, `EMIR_EXAMPLE_OUT`) is the fallback
  (`examples/README.md`). An example that skips (no sky130 PDK, no simulator) says why
  there too.

### Removed

- The hatched marks of an OTA measured at the original development site on
  `accuracy*.png`, and the docs pages that quoted its accuracy (its timings stay, and
  the self-consistency note in `emir/selfconsist.py` keeps its measured figure).
- **`emir layout`, the network built from a GDS's rectangles.** magic's own network
  replaces it (`emir extract --tool magic`). The rectangle network modelled only the
  process file's layers from li1 up, so it left out each device's contacts and
  diffusion, and it approximated the extraction. On example 08's layout, with the
  same currents, it read the IR drop 57 % high on out and 36 % high on vss. Removed
  with it: `emir/layout_cmd.py`, `layout_gds.py`, `layout_net.py`,
  `layout_devices.py` and `plan_layout.py`; its MCP tool `emir_layout`; the schemas
  `layout` and `layout_taps`; `emir next --taps`; the run summary's
  `not_checked.below_modelled_layers`; the eval card `gds-layout-taps`; example 08's
  grid with its tap file (magic extracts no devices from it); and the tests of those
  modules. Its codes are retired, never reused: EMIR-E213, E240, E241, E242, E330,
  E370, E440, E441, W240, W241, W242, W243, W340, W440 to W445. The process file's
  `gds:` numbers are now optional (no path reads them), and `emir process new` no
  longer writes them.

- **`emir mesh`, the GDS raster-mesh flow**, with everything only it used: the
  command and its MCP tool `emir_mesh`; its engine (`engine.run_emir` and the
  raster helpers; `emir/engine.py` keeps the process model), `emir/mesh.py`,
  `emir/meshplot.py`, `emir/meshview.py` and `emir/plan_mesh.py`; the mesh
  report page and run index (`dashboard.html_report`, `dashboard.index`; the
  stylesheet, tiles and legend stay in `emir/dashboard.py` for the net pages);
  the raster arrays of `emir/style.py` and the raster panel and port labels of
  `emir/chart.py`; `emir next --mesh` and `--ports`, and `emir report` of a
  `mesh.json`; the schemas `mesh` and `mesh_ports`; the `mesh` extra; examples
  `04_gds_spine` and `07_sky130_strap` and their figures; the eval card
  `gds-mesh-spine`; the three GDS cases of `emir validate`, which now has 13
  (every one needs only numpy, SciPy and PyYAML; `--no-skip` is accepted and has
  nothing to skip). Its codes are retired, never reused (`emir explain` names
  each): EMIR-E214, E215, E324, E470, E570, E571, E572, E770, I270, I370, I470,
  W209, W270, W370 and W470; the layout reader's codes (E106, E170, E212, E270) stay.
  `emir process new` no longer writes `min_width` (no check reads it; a file that
  has it still loads).
- `dspf.Network.em_check`, an unused second EM checker that still skipped
  vias with no cut count; `dspf.em_clusters` is the one EM judgement every
  path uses.

### Fixed

- The self-consistent loop's pass records give the net's largest ratio when nothing
  violates (`worst_ratio`, and the log's "worst ...x"); they said 0.0.
- A DSPF annotation whose value is a word starting with `e` (`$dir=ew`, as magic's
  DSPF writes it) read as the number `e` and failed the whole file (EMIR-E201).
  Annotation values are numbers only when they have digits.
- rcc's loop residual (EMIR-W405) used each layer's resistance scaled to the
  analysis temperature by its own TCR. A loop through two layers (li1 and the p-well
  in magic's network) then no longer closed, and the run said the currents were not
  the DSPF's. The check now uses the resistances the simulation ran.
- Two device terminals on one DSPF node no longer overwrite each other's `*|I`
  record.
- A DSPF's 0 V sources between two nodes of one net (a port's tied pins) are now a
  short in the network. Before, the second pin was an unfed node, so cc-grid's
  current left by the first pin alone.

- A reader that leaves early no longer costs a command its result. With an
  agent's `emir run ... --format json 2>&1 | head -c 300`, the next log line
  met a broken pipe: `run` replaced the finished run's `--summary-out` with an
  EMIR-E107 error document and exited 120. stdout and stderr are now wrapped
  (`emir.output.PipeSafe`): the first broken pipe is swallowed, later writes to
  that stream are dropped, and the command finishes, writes its files and
  exits with its own status. `tests/test_broken_pipe.py` covers run, nets,
  extract, abstract and simulate.
- `bin/emir` with `EMIR_PYTHON` naming a missing interpreter (or a directory)
  exits 3 with EMIR-E603, as the contract says, instead of the shell's 127; a
  bare name is looked up on `PATH`.
- A test passed only in a clone directory named `emir` (the MCP SDK's install
  hint names the clone's path); skip reasons name the install's real hint
  (`emir.repo.install_hint`), never `pip install 'emir[...]'`.
- Via rms and peak limits flagged `rms_peak_estimate` (an older `emir
  import-ict`'s 5 x avg rule of thumb) are no longer applied: those metrics are
  reported as not checked. `emir import-ict` writes no estimate (EMIR-W313 says
  the metrics are not checked) and removes an earlier import's.

- The back-annotation step of `emir next` and of a run's `next_steps` writes the
  annotated abstract under `emir_out/`, not beside the person's abstract.
- `emir next` without currents keeps the DC-currents alternative (`--currents`)
  in its missing-input advice.
- Security: `emir_data.il`, which Virtuoso `load()`s, writes every value as an
  escaped SKILL literal. A DSPF design name, cell, library or site
  `mark_layer` holding a quote could close its string and run SKILL code
  when the viewer loaded. An unset library is written as `nil` (it was the
  string `"None"`) and the viewer then takes the library from the form or the
  current window. Control characters in `--lib`, `--cell` or the DSPF design
  name are refused (`emir check` FAILs); a NaN or infinite result writes no
  viewer data, and a non-finite EM ratio is a FAIL.
- No option prefix selects an option: every parser sets
  `allow_abbrev=False`. `emir run ... --taps f` was taken as `--taps-out f`,
  and a CC run then overwrote `f` with the ingested taps; it is now a usage
  error (exit 2).
- `--currents` holding NaN or Infinity is an input-check FAIL: JSON accepts
  both, and a NaN sum passed the balance check.
- A metal resistor without `$W` was classed as a via (for want of a width)
  and then skipped: the sample with its widths removed read "0 wire R + 16 via
  R", passed the input check and lost its Metal1 failures. Only via
  annotation (`$A`, `$N`, `$VC`, `$SAVC`) now makes a via; anything else is
  classified by its layer in the process file, and a wire with no width is an
  input-check FAIL naming the resistors. A run forced past the check prints
  them, lists them as `no_width` in the summary JSON and gives a FAIL verdict.
- A run over every net with a simulation result was quadratic in the net
  count: `Waves.is_complex` rescanned every saved signal once per net. It is
  decided once per result; on a synthetic DSPF, 2000 nets went from 2.76 s to
  1.17 s and 4000 nets from 8.25 s to 2.36 s.
- A quoted layer value, `$layer="M1"`, is read as `M1` (it kept the quotes
  and then matched no process layer).
- `emir pins --net` with a net that is not in the DSPF exits 2 with a
  did-you-mean (it printed nothing and exited 0); `emir check` suggests close
  net names too.
- The input check lists at most eight off-net `--currents` nodes, then a
  count (it printed every one).
- `emir info` lists Xyce and ngspice beside Spectre (`$XYCE_BIN`,
  `$NGSPICE_BIN`, `xyce:` / `ngspice:` in the site file, or `$PATH`).
- `emir import-ict` never nulls a value the file already has. An expression
  the whitelist refuses used to overwrite a via's `imax_avg` with null,
  silently, and `emir check` then passed with the via unchecked; now the
  file's value is kept, flagged `estimate`, and a WARNING (on stderr) names
  the block and the expression. An ICT with no `em_tref` or
  `temp_reference` keeps the file's value, with a WARNING; the importer no
  longer assumes a temperature the ICT does not state.

- The MOS-decap "excluded" warning no longer starts with a stray "Run".
- Via cut counts from the cut area use the process file's `vcwidth`, which
  `Process` used to drop: a hidden built-in cut size, smaller than the real
  one, counted too many cuts and under-read the current per cut
  (`tests/test_layers_and_vias.py`). There is no default: without `vcwidth`
  such a via is checked as one cut, and `emir check` warns.
- Vias with no cut count and no area are checked as ONE cut (conservative)
  and reported in the console, HTML, summary and viewer; they used to be
  skipped silently while the checker said they were assumed one cut.
- DSPF layer tokens are read whole: `$LLX=1 $metal1` was read as layer `LL`,
  `$layer=M1` as `laye`, and lines without a bare token gave junk such as
  `SI_` or `V`.
- An instance pin with several DSPF connections (two `*|I` records, one saved
  terminal current, as a black-boxed block's supply pin has) is refused in
  CC mode with a message naming the pins: the later record used to overwrite
  the earlier one and put the whole current on one connection. The hierarchy
  flow (`emir abstract`) or an explicit split is needed.
- `--feed` moves the feed tap, not only the reference. The feed tap now
  carries the negated sum of the tap waveforms (PSS: of their harmonics), so
  branch currents no longer depend on the reference node; with the default
  feed at the port nothing changes.
- Binary SPICE raw files (ngspice's default, Xyce `-r` without `-a`) are
  refused with the ASCII options for each simulator, instead of an
  `IndexError`; a truncated ASCII raw says so.
- `emir check --json FILE` writes the findings (it was accepted and ignored).
- `--taps-out` creates its directory.
- The HTML provenance line reads `emir run <args>` (it read `emir run run`).
- The viewer colours each IR segment by its worse end, so its maximum equals
  the reported IR drop (on the sample, 42 mV against 54 mV).
- With `--self-consistent`, the droop is measured from the feed node.
- `scripts/leak_guard.py`: a closed-licence string is caught in any case
  (rule `closed-licence`), and a site's tool install directories (an install
  or tools root at the top of the file system, versioned tool directories)
  are caught too.
- The offline bundle builds from a long build path: pip writes a console
  script's interpreter path on the second line of a `sh` trampoline when the
  path is longer than a shebang allows, and the relocation step rewrote only
  launchers whose first line named it, so the build failed its own leak
  check. `scripts/relocate_launchers.sh` handles both forms and is tested on
  both.
- `emir import-ict` refuses an expression whose value is complex, such as a
  fractional power of a negative number at the wide-wire probe; it crashed
  with an internal error instead of keeping the file's value with a
  WARNING.
- Provenance: the RCC-versus-CC timing table (`emir/rcc.py`) and the +1.5 %
  CC error (`emir/selfconsist.py`) were labelled as measured on an extracted
  OTA; both came from the acceptance gate at the original development site,
  a generated bank of inverters tapping a segmented metal rail. The labels
  say so, and the self-consistency note adds the extracted OTA's own figure
  (+0.6 % to +1.1 %).
- `pdks/README.md` and `pdks/sky130.yaml` say the LEF's sheet resistances
  agree with the models' to about 4 % (li1 is 4.1 % above); they said
  "within 4%".
- A run forced past EMIR-E207 on a net in pieces factorised a singular
  matrix: IR read about 9e15 mV and the branch currents looked plausible.
  Each piece is now solved on its own: the reference's piece as before,
  another piece only when its injected current balances; elsewhere the
  currents are not a number (a FAIL) and the IR covers the reference's
  piece, with EMIR-W208 and a `not_checked.unsolved` entry. A ratio that is
  not a number ranks worst everywhere (it once hid behind a finite
  maximum), and the report says "not a number", never 0.
- A refused or failed `emir run` left the previous run's `--summary-out` in
  place, where `emir report` relayed it as the new one: the file now holds
  the failed run's `emir.error/1`, which `emir report` refuses (EMIR-E104)
  with that run's code.
- `emir report --from -` waited forever when stdin was a terminal; it is
  refused at once (EMIR-E103). A null ratio or IR in a summary was printed
  as 0.000 and a null worst ratio crashed the report; the run schema now
  allows the nulls the contract writes for NaN.
- A malformed `<result>.names` sidecar (a list, a string value, invalid
  JSON) was an internal error or blamed the result file: it is EMIR-E424
  naming the sidecar. Its keys match without regard to case.
- A Spectre run over its time limit was an internal error (exit 70): it is
  EMIR-E605. An output that cannot be written (`gui-install --dir`,
  `import-ict -o`, `--taps-out`, the summary) is EMIR-E105, and the HTML and
  viewer directories EMIR-E701; they read as "input file not found".
- `emir nets`, `emir next` and `emir check` refuse a file that is not a DSPF
  (a binary file, a process file, an empty file) with EMIR-E217; `nets`
  exited 0 with no nets. `emir import-ict` refuses a file with no conductor
  or via block (EMIR-E104) and writes nothing.
- Without the klayout package, `--viewer` skipped `emir.lyrdb` silently: it
  now says so (EMIR-W703). The marker database, the Explorer's title and
  the data file's load message say "Screening, not sign-off.".
- The mesh's grid coarsening and `emir pins` on a net without pins logged
  uncoded lines (EMIR-W209, EMIR-W205 now); the test that guards warning
  lines also catches other cases and wordings.
- The bundle's relocated launchers (`python/bin/emir` and the rest) failed
  through a symlink (exit 127): the trampoline follows the links to the
  real launcher's directory.

Features:

- **Big blocks: streamed superposition and cc-grid dynamic IR**
  - A big flat cc-grid run is no longer refused for the size of its transfer matrix
    (it streams instead), and its superposition no longer needs memory in
    proportion to branches x taps.
  - The dense superposition's time reduction is chunked so that the matrix and the
    chunk's temporaries stay inside the memory budget together; it used chunks the
    size of the whole budget, and with its temporaries could reach several times it
    on a long result (`taps.superpose_waveforms(chunk=...)`).
- **Xyce and ngspice for the DSPF flow (`emir save-lines --sim`, `emir probe`, `emir simulate`)**
  - rcc chose its averaging window on the branch with the largest peak, taken to
    be the feed. A contact can out-peak the feed segment, and its waveform can
    repeat while the rest of the block is still starting up. The window then took
    in the start-up period and the averages came out low: the feed's 12.4 % low on
    an inverter bank. It is now chosen on the current into the net at its
    reference node (`rcc.feed_wave`).
- **Cadence integration (`emir extract`, `agent/cadence/`)**
  - The viewer opened the layout for edit (`?mode "a"`): now read-only.
  - Paths in the commands the form starts were not shell-quoted (a space broke Check
    and Run; `;` or `$()` in the output directory ran): every path and `emirBin` is
    single-quoted, and the child runs under `env -u PYTHONHOME -u PYTHONPATH
    PYTHONUNBUFFERED=1` (`EMIR_UNSET_LD=1` also drops `LD_LIBRARY_PATH`); `bin/emir`
    drops an inherited `PYTHONHOME` / `PYTHONPATH` (`EMIR_KEEP_PYTHONPATH=1` keeps it)
    and runs unbuffered.
  - The menu: registered on `"schematic"` (was `"Schematics"`) and `"maskLayout"`, once
    per session (a reload added a second menu), with a new pulldown per window.
  - The Explorer could draw on a schematic window of the same cell, or a layout of
    another library: it now takes the maskLayout window of the run's library and cell.
  - Closing the Explorer left its highlights and a zoom/pan callback that resized the
    ring in every window: the close callback clears them, the callback acts only on
    the Explorer's window and resets its busy flag on error, a second start replaces
    the first form, and deleting a highlight set of a closed cellview no longer throws.
  - A highlight set that could not be created made the redraw throw; it is skipped and
    counted. The Explorer's start (from a timer) prints its error instead of failing
    silently.
  - Rows past 9999 selected the wrong segment; the list holds at most 2000 rows and
    says so; net filtering uses a table instead of `member()`.
  - SKILL: `return()` inside `let()` in the args-file writer; "Open HTML" before a run;
    an empty IPC chunk could leave a nil tail; the User guide item opened a file the
    repository does not have; unguarded `hiZoomIn`; the selection halo ignored the
    halo tunables.
  - `virtuoso_batch` stripped only three of the `-log` transcript prefixes.
- **Where the features meet:**
  - `emir next` over MCP gave no `tool` calls when a rule stopped the plan
    (no DSPF, and every GDS plan).
- **Commands as an agent runs them:**
  - `agent/opensource/render_markers.py` takes `-rd cell=` and otherwise draws
    on the cell KLayout shows, so a layout with several top cells draws the right
    one.
  - `--currents` takes inline JSON or the path of a file holding it (any
    layout of the JSON) in `emir run`, `check` and `next`, and in
    their MCP tools; one reader (`emir/jsonarg.py`) serves them all. `emir
    next` reads the file as the run will and plans no run on currents the run
    would refuse (a missing file, not JSON, NaN). Before, `emir next
    --currents FILE` planned `emir run ... --currents FILE`, which failed with
    EMIR-E401, and `@FILE` worked only for a one-line file. The EMIR-E401 hint
    says a file is fine.
  - MCP tool calls are held to their input schemas: an argument the tool does
    not have (a misspelt option, which the SDK dropped without a word) or a
    required one left out is refused with the new EMIR-E592, naming the
    tool's parameters (`from_file`, not `--from`); nothing runs.
  - Error next steps keep the caller's `--format` (as the other next steps
    do), and so do the fixes `emir check` suggests; a next step that is not
    an emir command (`ls`, `tail`, `klayout`) is marked `"shell": true`: the
    MCP server, which has no shell, refuses it (EMIR-E591).
  - A missing input file reads "input file not found: PATH", not Python's
    "[Errno 2] ..." text.
  - `emir info` lists every tool a command runs: magic and PVS join Spectre,
    Xyce, ngspice, Virtuoso and Quantus (`qrc`), with `$MAGIC_BIN` and
    `$PVS_BIN` beside the site file's `magic:` and `pvs:`. It was the next
    step of EMIR-E642 (magic not found) without naming magic. It reports
    magic's version and warns (new EMIR-W640) when it is older than the
    oldest magic EMIR was checked with: magic 8.3.105 crashed on the sky130
    technology file during development, which read as "magic extracted
    nothing". `emir extract --tool magic` gives the same warning, and a magic
    that crashes is named as the cause in EMIR-E640.
  - `emir probe` of a block with no `.tran` (a bare subcircuit) suggested
    `emir simulate` on it, which EMIR-E462 refuses; it now says why there is
    no such step (new EMIR-W463: probe the testbench instead). The probe
    document gains `has_tran` and `notices`.
  - `emir next` flags the chip-level run of a top above the big-block size
    as a long run (`needs_human`), as it flags the flat run of the same grid.
  - `emir next` with an abstract that is not written yet plans its `emir
    abstract` step (waiting on the block's DSPF and result) and then `emir
    next` again; nothing past the abstracts is planned until every one can be
    read. `emir abstract --top TOP.dspf@INST` gives the abstract's summary the
    next step back to the chip-level plan.
  - The run summary's `superposition` says which path ran on every cc-grid
    route: `scalar` (per-tap scalars: `--scalars`, a tap file with rms and
    peak, a chip-level run with `--align inphase`) and `combined` (`--align
    async`, a phase sweep) join `dense` and `stream`. It was null on all but
    the exact path, so an agent could not tell bounds from exact values.
  - `--superposition dense` above `--max-memory` streams with a warning (new
    EMIR-W452), not only an info line, and `--help` says so. A `--max-memory`
    above 80 % of the physical memory is lowered to that with a warning (new
    EMIR-W550): 64G on a smaller machine was killed by the kernel (exit 137,
    no document).
  - Only the bundled sample is called "the bundled ILLUSTRATIVE sample" by
    `emir next` and EMIR-W306; another process file marked illustrative (an
    example's) is named as such.
  - The `--summary-out` file and the printed run summary are built apart and
    could carry `created` times a second apart; a run has one time now.
  - The advice that cites the repository's files (the agent playbooks, the
    Cadence integration in `agent/cadence/`, the open PDKs' process files in
    `pdks/`) names the file in the clone emir runs from, and from a wheel,
    which ships none of them, the file's page in the repository
    (`emir/repo.py`; `REPO_URL`, `https://github.com/skycadeda/emir`). EMIR-I691 and the Virtuoso menu's notes
    message give the address too.
  - `sh examples/NN/run.sh` failed for the bash examples ("Illegal option -o
    pipefail"): they hand themselves to bash first.
  - `test_repo_is_clean`, run from an unpacked sdist (no git: the guard walks
    the disk), failed on the bytecode the run's own earlier tests wrote; it
    leaves out bytecode newer than the sdist's PKG-INFO (shipped bytecode
    still fails).
  - `emir simulate --timeout`: a simulator stopped by the timeout could leave
    the result it had begun to write (seen with ngspice on a loaded machine,
    which made `test_a_failed_or_slow_simulation_is_coded` flaky); the stopped
    run's result file is now removed (EMIR-E605 as before). Found by the
    verification and by the CI runs, fixed once.
- **With the CI files:**
  - Tests assumed the optional packages and failed in an install with no
    extras: they skip without `klayout` or `matplotlib`, and the viewer one checks
    the documented behaviour without `klayout` (no marker database, EMIR-W703).
- **With the documentation:**
  - Help texts: `--em-temp` on `emir run`, `check` and `abstract` said the
    default was the process file's `temp_reference`; it is `em_tref`, as the
    engine and the other commands say.

## [1.0.0] - 2026-09-08

First release, not published.
