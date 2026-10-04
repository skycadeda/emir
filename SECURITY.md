# Security policy

## Reporting a vulnerability

Report it **privately**, never in a public issue, pull request or discussion:
use GitHub's private vulnerability reporting (the repository's **Security** tab,
**Report a vulnerability**). Please include:

- the EMIR version (`emir --version`) and how it is installed (pip, clone, offline bundle);
- what an attacker controls (an input file, an MCP tool call, a file in the work
  directory) and what they gain (code run in Virtuoso or Python, a file written
  outside the output directory, a gate bypassed);
- the smallest input that shows it, **made up by hand**: no file extracted with a
  licensed PDK, no foundry data.

The maintainer acknowledges the report, agrees a disclosure date with the
reporter, and credits the reporter in the release notes unless asked not to. A
fix is released before the details are published.

The same private channel is for **foundry or site data found in this repository
or its history** (a licensed PDK's limits or names, a site path, a licence
server): report it privately, because a public issue would copy it again.

## Supported versions

Fixes go into the next release from `main`; only the latest release is
supported. Check that the problem is still there in it before reporting.

## What is security-relevant in EMIR

EMIR reads files that come from other people (DSPF and SPEF files, simulation
results, process files, ICTs, GDS layouts) and writes files that other programs
run or display. These are the places where an input file could become code:

### SKILL files loaded into Virtuoso

Virtuoso runs every file it `load()`s. EMIR writes three kinds:

- **the viewer data file**, `emir_data.il`, written by `emir run --viewer`. It
  holds net, layer, cell and library names and numbers taken from the DSPF and
  the run, and a designer loads it into a live Virtuoso session. Every value is
  written as a SKILL literal: `nil`, `t`, a number, or a string with `\` and `"`
  escaped and no raw control character; NaN and Inf are refused, and so are
  control characters in the library, cell and design names (`emir/layoutview.py`,
  `tests/test_skill_injection.py`);
- **the Explorer**, `emir_explorer.il`, and **the menu and form**, `emir_main.il`
  (generated from `emir/skillapp.py` and `emir/skillgui.py`; `emir gui-install`
  writes them with a site stub `emir_site.il`). The form starts `emir` through
  `ipcBeginProcess`, which runs a shell: every path and value on that command line
  is single-quoted for `/bin/sh` (`emirShQ`);
- `agent/cadence/skill/emir_init.il`, the entry point a `.cdsinit` loads.

**In scope:** any input (a DSPF, a result file, a process file, a site file, a
value typed in the form) that makes one of these files run SKILL or a shell
command it was not written to run.

### ICT expression parsing

`emir import-ict` reads a foundry ICT and evaluates its EM-limit expressions
(`<J> * (w - <dw>)`, `sqrt(...)`) to merge the limits into a process file. An
ICT is an input file, so the expressions are **never passed to Python's `eval`**:
each is parsed and walked against a whitelist (numbers, the variables `w` and
`delta_T`, `+ - * / **`, unary signs and `sqrt`) and limited in length;
anything else, and any result that is not a finite number, is refused
(`emir/pdk_import.py`, `tests/test_import_ict.py`).

**In scope:** an ICT expression that runs code, reads or writes a file, or makes
the import hang or exhaust memory.

### Also in scope

- **Files EMIR writes** stay where the options say (`--html`, `--viewer`,
  `--out`, `--summary-out`): every file name built from an input (a DSPF's design,
  a net, a GDS cell, a layer) is reduced to one safe component
  (`emir.output.safe_component`) and checked to lie inside its directory. A name
  taken from an input file that makes EMIR write elsewhere is a vulnerability.
- **Scripts EMIR writes for other tools**: the script magic runs
  (`emir extract --tool magic`) names a fixed file for the layout and a cell
  name it has checked (plain characters only, EMIR-E243), never a path from the
  command line; the Quantus command file
  (`emir extract`) refuses a cell name or path with a quote, bracket, brace, `$`
  or backslash; the block's name in a probed netlist must be one SPICE word
  (EMIR-E243). An input that makes one of them run a command is a vulnerability.
- **The HTML reports** embed names from the input files: a name that runs script
  when the report is opened is a vulnerability.
- **The site file** names programs EMIR runs (`emir info` asks magic its
  version). It is read only from `$EMIR_SITE`, `--site`, `~/.emir/site.yaml` or the
  install directory, never from the working directory by itself. A process file,
  which comes with a PDK or a layout, may name only a magic program on `$PATH` by its
  bare name (`magic: binary:`, a name starting with `magic`); a path or another
  program there is refused (EMIR-E648). A process file that makes EMIR run another
  program is a vulnerability.
- **The magic route's outputs**: `emir extract --tool magic` runs magic in a
  directory of its own under `--out` (`magic/`, marked by `.emir-magic-work`) and
  writes `<cell>.dspf` and `<cell>.cc.spice` into `--out`; it never overwrites one
  of those, or a `magic/` directory, that EMIR did not write (EMIR-E646), over the
  command line or MCP alike, and never removes the layout it reads. An input that
  makes it remove or overwrite a file of the person's is a vulnerability.
- **`emir mcp`**, the MCP server: its tools run only `emir` commands (never a
  shell), and a call that would start a licensed tool, run long (an ADC-top or
  PLL-top size DSPF, or `dynamic` with `dyn_steps` above 200000), rewrite a file
  in place, overwrite an existing file outside the server's working directory,
  loosen the EM verdict (`emir_run` with an `em_basis` other than the default, or
  `dynamic` with `ground`, with `dyn_steps` below 2000 or without `period`), or
  simulate a deck that runs commands of its own is refused (EMIR-E590) unless the
  agent passes `confirm: true` after asking the person. A tool that can write a
  file is never annotated read-only. A way past that gate, or a tool call that
  runs something other than `emir`, is a vulnerability.
- **The release files**: building the offline bundle verifies the checksum of
  the standalone Python it embeds (`scripts/build_bundle.sh`), and every release
  carries `SHA256SUMS`. A way to make the bundle run code from outside its own
  directory is a vulnerability.

### Testbenches are code

`emir simulate` runs Xyce or ngspice on the
person's own testbench (and `emir simulate --run`, Spectre or HSPICE: the
person's go-ahead first, `confirm` over MCP), and copy the `.spiceinit` beside it
next to the deck. `emir verify-sim` runs only decks it writes itself.
ngspice runs the commands of a `.control` block (its `shell` command runs any
program) and of a `.spiceinit`, so a testbench is a program: run only decks from
a source you trust. EMIR never overwrites an existing `.spiceinit` with other
content, and over MCP a deck (or its includes, or its `.spiceinit`) that holds a
`.control` block or a `shell` line needs the person's go-ahead. That a trusted
deck runs its own commands is not a vulnerability; EMIR running one without that
go-ahead over MCP is.

### Not security issues

- A wrong EM or IR number. EMIR is a screening tool, not sign-off, and a research
  project provided as is, with no warranty or guarantee of accuracy: report it as a
  bug (the bug form asks for what is needed), with data that can be public.
- Anything that needs the attacker to already run commands as the user.
