# Contributing to EMIR

Thank you for helping. EMIR screens extracted analog layout for electromigration
and IR drop. Its first user is an AI agent and its second a designer, so a change
is finished when an agent can drive it from `--format json`, `EMIR-` codes and
`next_steps` alone, and a person can check every number it prints.

Security problems go to the private report in [SECURITY.md](SECURITY.md), never to
an issue.

## Ground rules

1. **No foundry data, ever.** Nothing from a licensed PDK goes into a file, an
   issue, a pull request or a commit message: no DSPF or netlist extracted with
   it, no EM limits, sheet resistances or rule values, no ICT, tech file, layer
   map or display file, no layer, purpose, display-packet or device-model names.
   No site paths, user or host names, licence servers or internal project names
   either. The history is published and permanent. `scripts/leak_guard.py` runs
   on every commit (files and message) and in CI; it catches the patterns it
   knows, not everything, so the rule is yours to keep. Invent test data: round
   numbers, obviously synthetic GDS layers (100/0 and up), names like `metalA`.
2. **Every number has a source**: a run in this repository, best reproduced by
   a committed script; a cited public document; or a measurement quoted as such
   ("measured at <where> on <the real test object>"). Never invent one. A
   projection is labelled as a projection.
3. **Screening, not sign-off.** Results are presented as screening everywhere
   (the `emir.SCREENING_NOTE` constant), and nothing calls them sign-off.
4. **Units**: mA/um and mA/cut in process files; A, V, um and ohm in the code.
5. **The agent contract holds** (`agent/CONTRACT.md`): with `--format json`, stdout is
   exactly one JSON document with a `schema` field; logs go to stderr; every
   refusal, failure and warning carries a registered `EMIR-` code; exit statuses
   are 0 done, 1 violations with `--fail-on-violation`, 2 bad input, 3 a missing
   tool, 70 internal, 130 interrupted, 143 terminated; nothing prompts.
6. **Docs follow the code.** A changed option, code or output updates the
   reference, the playbooks and the `emir next` rules in the same pull request.
7. **Every bug fix and every feature comes with a test.**

## Development setup

```sh
git clone https://github.com/skycadeda/emir emir && cd emir
python3 -m venv .venv
.venv/bin/pip install -e '.[all,dev]'
.venv/bin/pre-commit install        # the pre-commit and commit-msg hooks
.venv/bin/emir selftest             # offline proof that the install works
```

Python 3.10 or later. The core needs only numpy, scipy and PyYAML; the extras
add matplotlib (`report`), the KLayout Python module (`klayout`), the
MCP SDK (`mcp`), the test and lint tools (`dev`) and mkdocs (`docs`).

Optional tools widen what the tests cover; each test that needs one skips and
says why (`pytest -rs`): ngspice and Xyce (the open-simulator flow and examples
02/03), the KLayout application, magic, and the open PDKs' files
(`PDK_ROOT` for sky130A, `GF180_PDK_ROOT`, `IHP_PDK_ROOT`: the citation checks of
`pdks/*.yaml`, example 08's part B). No Cadence tool and no HSPICE is needed:
the Cadence code is tested against stand-in tools, and the Spectre and HSPICE
routes against stand-in simulators (`tests/_sim_fakes.py`). Never write that a
licensed tool was verified: it is verified on the machine that uses it
(`emir verify-sim`, the headless SKILL self-test, `LIVE_TEST.md`).

The documentation site: `.venv/bin/pip install -e '.[docs]' && .venv/bin/mkdocs serve`.

## The gates

Every commit passes these; CI runs them on every pull request.

```sh
.venv/bin/pre-commit run --all-files          # ruff, YAML, whitespace, large files,
                                              # the leak guard, the generator checks
.venv/bin/python scripts/leak_guard.py        # the whole tree, tracked and untracked
.venv/bin/python -m pytest                    # every marker (a few minutes)
.venv/bin/emir selftest
.venv/bin/emir validate
.venv/bin/python scripts/gen_errors_md.py --check       # agent/errors.md is current
.venv/bin/python scripts/gen_schemas.py --check         # agent/schemas/ are current
.venv/bin/python scripts/gen_cadence_skill.py --check   # agent/cadence/ generated copies
.venv/bin/python scripts/gen_contract.py --check        # agent/CONTRACT.md is current
.venv/bin/python scripts/gen_cli_reference.py --check   # docs/cli-reference.md is current
.venv/bin/python scripts/gen_pypi_readme.py --check     # README.pypi.md (the PyPI description)
.venv/bin/mkdocs build --strict                         # the site (the [docs] extra)
```

The commit-msg hook runs the leak guard on each message (`--commit-msg`); CI
checks the messages of every pushed commit again.

Test markers (`pyproject.toml`): `validate` (the analytic suite), `example`
(each `examples/NN_*/run.sh` against its `expected/` results), `slow`, and
`cadence` (needs a real Spectre, Virtuoso or Quantus licence; deselected in CI).
For a quick run: `pytest -m "not example and not slow"`.

What CI runs (`.github/workflows/ci.yml`):

| job | what it proves |
|---|---|
| core | Python 3.10, 3.12 and 3.13 with no extras: the tests skip cleanly without the optional packages and tools; `emir selftest` from `/` |
| full | Python 3.12 with every extra and ngspice: every test, the examples, `emir validate --no-skip` |
| lowest | Python 3.10 with every direct dependency at its `>=` floor (`uv --resolution lowest-direct`) |
| wheel | the sdist and wheel build, pass `twine check`, the sdist holds the whole source tree (`scripts/check_sdist.py`), and the wheel installed alone runs `emir selftest` from `/` |
| lint | `pre-commit run --all-files`, and the leak guard on each new commit message |
| docs | `mkdocs build --strict` |

There is no Xyce package for Ubuntu; CI says so in a notice and the Xyce cases
skip. Run them locally before changing the open-simulator code.

## How to add things

### A command

1. Write `cmd_x(argv, out) -> int` in your module (or `_cmd_x` in `emir/cli.py`).
   Build its parser with `emir.output.parser("emir x", description=...)`, which
   adds `--format text|json`, refuses abbreviated options and turns usage errors
   into `EMIR-E507`.
2. Print text for a person with `print()` (in JSON mode `cli.main` sends it to
   stderr), call `out.emit(document("x", ...))` exactly once
   (`emir.output.document` adds the `schema` and `emir_version` fields), and
   return the exit status.
3. Register it in `COMMANDS` in `emir/cli.py`.
4. Give its document a schema (next section) and add the name to the expected
   set in `tests/test_schemas.py`.
5. Classify it for the MCP server in `emir/mcp_tools.py`: in `UNGATED` if it never
   needs the person's go-ahead, else a gate function in `GATES` (a licensed tool,
   a long run, a file rewritten in place); `READ_ONLY` and `DESTRUCTIVE` set its
   tool annotations. The MCP tool itself is built from the parser.
6. Raise a registered code for every failure (below); put `next_steps` in results.
7. If the command belongs to a flow, add the `emir next` rule and the playbook
   step that plan it.
8. Test the JSON output against the schema, each error code it raises, and its
   exit statuses (`tests/test_output_contract.py` shows the pattern).
9. Regenerate `agent/CONTRACT.md` (`python scripts/gen_contract.py`) and
   `docs/cli-reference.md` (`python scripts/gen_cli_reference.py`): both list
   every command and its options from the code.

### An error code

1. Pick the area (1 files, 2 DSPF and layout, 3 process, 4 currents, 5 options,
   6 tools, 7 outputs, 8 hierarchy, 9 internal), then the decade of the part of
   emir raising it (`errors.DECADES`: x01-x39 the base package, x40 the open
   layout path, x50 big blocks, x60 the simulators, x70 the GDS layout reader
   and the PDK files, x80 Cadence, x90 MCP), and a free number in it
   (`emir explain --list`). Codes are never reused.
2. Add a `Code(...)` to `_ENTRIES` in `emir/errors.py`: code, title, meaning, hint,
   and the command that fixes or diagnoses it (a template: `{field}` from the
   case at hand, `[...]` optional parts, a `fallback`). An `E` code exits 2
   (9xx: 70) unless `exit=` says otherwise (a missing tool: 3).
3. Use it: `raise EmirError("EMIR-E2nn", message, **fields)`;
   `notice("EMIR-W2nn", message, log)` for a warning inside `collecting()`;
   `Report.fail/warn/info/ok(code, area, message, fix)` in the input checker.
4. `python scripts/gen_errors_md.py` regenerates `agent/errors.md`, and
   `python scripts/gen_contract.py` the code counts in `agent/CONTRACT.md`.
5. A code no longer raised moves to `RETIRED` with the reason.

`tests/test_errors.py` fails on an unregistered or unused code, a level that
does not match the letter, a plain `raise`, an uncoded warning line and a stale
catalog. If an agent needs to know what to do about the code, add it to
`agent/playbooks/08-troubleshooting.md`.

### A schema

Schemas live in `emir/schemas.py` (JSON Schema 2020-12); `emir schema <name>`
prints one. `python scripts/gen_schemas.py` writes `agent/schemas/<name>.schema.json`.
A document may gain fields without a version change; removing a field or
changing its meaning moves `emir.<name>/1` to `/2`. Numbers a run could not
compute are `null`, never 0. The tests validate every command's real output
against its schema.

### A plan rule (`emir next`)

`emir next` is the playbooks' decision tree as code. A feature's rules go in
`emir/plan_<feature>.py` (imported automatically):

```python
from .plan import rule

@rule(45, "myflow")                 # runs in order: 10 preflight ... 90 guardrails
def _myflow(f, plan):               # f: the Facts (inputs, cheap probes)
    if f.inputs.get("something"):
        plan.add("my-step", "emir x ...", "why this step")
```

`add_arguments(ap)` and `inputs(a)` in the same module add `emir next` options.
Every step runs as printed or is `waiting` on a `<placeholder>` an earlier step
produces; a step that runs a licensed tool or takes long is marked
`needs_human` (`plan.add(..., licensed_tool=..., long_run=True)`). Plan only
what `emir check` accepts. Test the plan, and run its steps as printed.

### A playbook

The playbooks in `agent/playbooks/` are what an agent follows: `00-preflight`,
`01-screen-a-block`, `02-big-block-cc`, `03-chip-level-hierarchy`,
`04-onboard-a-pdk`, `05-open-source-flow`, `06-cadence-flow`,
`07-report-to-human`, `08-troubleshooting`, `09-gds-only`, `10-mcp`. A new one
takes the next number and is listed in `AGENTS.md` and `agent/README.md`.
Write them for an agent: short, operational, every command one that exists
(copied from `emir next` or `--help`, never an invented flag), the codes to
branch on, the guardrails (never invent a process limit, never call a result
sign-off, ask the person before a licensed tool or a long run, never touch
their Virtuoso, simulator or layout) and reporting through `emir report`. When a
playbook changes what an agent should do, update or add its task card in
`agent/evals/`.

### A PDK process file

`pdks/<pdk>.yaml` is built only from the PDK's public, redistributable files.
Every value carries its source (`# [TAG]:line`, a section, or a URL); the header
names the variable that holds the PDK root, and `tests/test_pdks.py` checks each
cited line when the PDK is installed. Limits the PDK does not publish are left
out (reported as not checked), never estimated. `pdks/TEMPLATE.yaml` lists every
key with its unit. A licensed PDK's file stays at its site.

### Cadence and SKILL changes

Nothing Cadence runs in CI, so:

- **A change to SKILL needs a live Virtuoso try before it is merged**: the menu and
  form (`emir/skillgui.py`, written to `agent/cadence/skill/emir_main.il` by
  `scripts/gen_cadence_skill.py`), the Explorer (`emir/skillapp.py`), the viewer
  data file (`emir/layoutview.py`) or `agent/cadence/skill/emir_init.il`. Follow
  `agent/cadence/LIVE_TEST.md` (with `agent/cadence/test/emir_manual_test.il`),
  (after the headless self-test, `python agent/cadence/python/virtuoso_batch.py
  --selftest`). Record the result with the change (the pull request), never as a
  status in the repository: the docs say how each piece is verified on site by the
  local agent, or that it is UNVERIFIED until checked on site, and never that a
  Cadence tool was verified anywhere.
  `agent/cadence/SKILL_NOTES.md` lists the traps.
- A file Virtuoso loads is code: every value from an input file is written as a
  SKILL literal (`tests/test_skill_injection.py`), and every value that reaches a
  shell is quoted (`emirShQ`).
- Write SKILL from the public API reference, in your own words: no text copied
  from Cadence documentation or sample code, and no PDK layer, purpose or
  packet names.

## Commits and pull requests

- One topic per pull request; the template's checklist is the gates above.
- Commit messages say what changed and why. They are checked by the leak guard
  like files: no licensed PDK names or numbers in them.
- **Sign off every commit** (`git commit -s`). The `Signed-off-by:` line certifies the
  [Developer Certificate of Origin 1.1](https://developercertificate.org/): that
  you wrote the change or have the right to submit it under the project's
  licence (MIT).
- Keep the history clean of generated output: run results (`emir_out/`,
  `examples/*/out/`), `build/`, `dist/` and `site/` are ignored.
- Hooks are pinned; `pre-commit autoupdate` moves them (in its own pull request).

## Releases (the maintainer)

1. Set the version in `emir/__init__.py` and `CITATION.cff` (a test keeps them
   equal), and turn `[Unreleased]` in `CHANGELOG.md` into `## [x.y.z] - YYYY-MM-DD`.
2. Wait for CI to pass on that commit, then tag it: `git tag -a vx.y.z -m "EMIR x.y.z"`
   and push the tag.
3. `.github/workflows/release.yml` checks that the tag equals the version, builds
   the sdist, the wheel and the offline bundle (`scripts/build_bundle.sh`), runs
   each from `/`, writes `SHA256SUMS` and attaches all four to a **draft** GitHub
   Release with the changelog section as notes. Review it, then publish.
4. Publishing to PyPI is prepared at the end of `release.yml` (trusted publishing)
   and commented out; the steps to turn it on are written there.
5. `.github/workflows/docs.yml` deploys the documentation site from `main` to
   GitHub Pages, through the `github-pages` environment (give it a required
   reviewer to approve each deploy).
