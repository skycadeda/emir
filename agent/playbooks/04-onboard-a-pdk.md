# 04 Onboard a PDK: the process file

EMIR knows nothing about a process until a process file tells it: per conductor layer the GDS number, sheet
resistance, TCR and EM limits in mA per um of drawn width; per via the cut resistance, cut size and limits in mA per
cut; the temperature the limits hold at. Every value comes from the PDK's own documents, with a citation.
**Never invent, estimate or "typical" a limit.** **Screening, not sign-off.**

## When to use

- `emir next` reports `missing`: "a process file with your PDK's EM limits".
- The person names a PDK: first look in `pdks/`. sky130, gf180mcu (gf180mcuD) and ihp_sg13g2 are there, built
  from the PDKs' public tech LEFs, design manuals and KLayout layer files, with a source on every value.
- The person's PDK is not there: fill a copy of `pdks/TEMPLATE.yaml` from its documents, or import an ICT.
- There is no readable file at all: no ICT, or `emir import-ict` refuses it (EMIR-E325: encrypted, binary, not
  text). Then the values are entered by hand: the branch "No readable file" below.
- `emir check` warns EMIR-W307 (a DSPF layer with no process entry) or EMIR-W302 (a layer with no limit).

## Inputs

| input | where the values come from |
|---|---|
| the PDK's name and variant (metal stack, top-metal thickness) | the person |
| per layer: GDS layer/datatype, sheet resistance (ohm/sq), TCR, min width, avg / rms / peak limits (mA/um) | the tech LEF (`RPERSQ`, `DCCURRENTDENSITY AVERAGE`, `ACCURRENTDENSITY RMS`/`PEAK`, `WIDTH`), the design manual's EM section, the KLayout `.lyp`, the resistor models |
| per via: below / above layers, ohm per cut, cut side `vcwidth` (um), limits (mA/cut) | the tech LEF (`RESISTANCE`, the cut `WIDTH`, `DCCURRENTDENSITY`) |
| `em_tref`: the temperature the avg limits are quoted at | the design manual (required: no default) |
| the extractor's layer names, if they differ from the file's | a DSPF of that PDK: `emir check` lists unmapped layers (EMIR-W307) |
| an ICT (Cadence technology file for Quantus), optionally | the person's PDK installation; it never enters this repository |

## Commands

### Worked example (runs as printed from the repository root)

What exists, what a process file holds, and how a new one is validated: the template, checked against the
bundled sample DSPF.

<!-- test: run -->
```sh
ls pdks/
emir schema process --format json
mkdir -p emir_out
cp pdks/TEMPLATE.yaml emir_out/mypdk.yaml
emir check emir/data/sample/rail.dspf --process emir_out/mypdk.yaml --currents '{"VDD": 0.004, "MP0:s": -0.001, "MP1:s": -0.001, "MP2:s": -0.001, "MP3:s": -0.001}' --format json
```

<!-- test: expect -->
```json
{"schema": "emir.check/1", "passed": true,
 "findings": [{"code": "EMIR-W307", "level": "WARN"}, {"code": "EMIR-W306", "level": "WARN"}]}
```

EMIR-W307 says the DSPF's layer `cont` has no process entry and its 4 segments are NOT checked: map it in
`dspf_layers` to the via the PDK's documents say it is (or add that via). EMIR-W306 says the file is ILLUSTRATIVE
(its `pdk:` name contains the word): a real file drops it. The bundled sample's file has the contact, so the same
check is clean of W307:

<!-- test: run -->
```sh
emir check emir/data/sample/rail.dspf --process emir/data/sample/process.yaml --currents '{"VDD": 0.004, "MP0:s": -0.001, "MP1:s": -0.001, "MP2:s": -0.001, "MP3:s": -0.001}' --format json
```

<!-- test: expect -->
```json
{"schema": "emir.check/1", "passed": true, "findings": [{"code": "EMIR-W306"}]}
```

### On the person's files

An open PDK that `pdks/` has: use it as it is, and check it against a DSPF of that PDK.

```sh
emir check <block.dspf> --process pdks/gf180mcu.yaml --net <VDD> --currents '{"VDD": 0.001, "M0:s": -0.001}' --format json
```

A new PDK from its documents: copy `pdks/TEMPLATE.yaml` to `<pdk>.yaml`, change `pdk:`, fill every layer and via
from the documents with the source of each value in a comment (`# [LEF]:98 DCCURRENTDENSITY AVERAGE 0.67`), leave
out what the PDK does not publish (EMIR then reports that metric as not checked), map the extractor's names with
`dspf_layers`, and validate:

```sh
emir check <block.dspf> --process <pdk>.yaml --net <VDD> --currents '{"VDD": 0.001, "M0:s": -0.001}' --format json
emir next --dspf <block.dspf> --process <pdk>.yaml --format json
```

From an ICT (the person's licensed data; keep the result with their PDK installation, never in this repository):

```sh
emir import-ict --ict <pdk>.ict --emir <pdk>.yaml -o <pdk>.emir.yaml --format json
```

It fills the EM limits, TCRs, `jmax_factor` and `em_tref` from the ICT onto the geometry and resistance keys
already in the file, names the ICT as each value's source (`sources:`), sets `provenance: imported-ict` (or
`mixed`), and says what it could not find. Without `-o` it rewrites `--emir` in place: ask first. An ICT EMIR
cannot read (encrypted, binary, compressed, not text) is refused with EMIR-E325, nothing written: go to the next
branch. Never try to decrypt it, and never ask the person how to.

### No readable file (missing or encrypted): the person enters the EM data by hand

**You never invent, estimate or derive a limit**, and never fill one from memory, from another PDK, from a
"typical" value or from a similar node. You write the fill-in file; the values come from the person.

<!-- test: run -->
```sh
emir process new --from-dspf emir/data/sample/rail.dspf -o emir_out/handpdk.yaml --pdk handpdk --format json
```

<!-- test: expect -->
```json
{"schema": "emir.process_new/1", "provenance": "hand-entered", "interactive": false,
 "layers": [{"name": "metal1"}], "vias": [{"name": "cont", "above": "metal1"}],
 "empty": {"required": [{"path": "em_tref"}]}}
```

The file covers exactly the layers and vias the DSPF uses, every value `null` with its unit and a `sources:` slot,
and the via stacking it could infer marked `inferred from the DSPF ... check`. Until the values are entered, the
check refuses it (exit 2, EMIR-E320 for each required value):

<!-- test: run -->
```sh
emir process check emir_out/handpdk.yaml --dspf emir/data/sample/rail.dspf --format json  # exit 2
```

<!-- test: expect -->
```json
{"schema": "emir.process_check/1", "passed": false, "provenance": "hand-entered",
 "findings": [{"code": "EMIR-E320", "level": "FAIL"}]}
```

Then, one of two ways, and nothing else:

1. **The person types each value** from the design manual's EM tables, in the unit the comment shows, with its
   source (`sources: {jmax_avg: "DM rev 2.1, table 9-4, p. 112"}`), leaving null what the manual does not give
   (that check is then reported as not checked). At a terminal the person may run `emir process new ...
   --interactive` themselves; you never do (it refuses a stdin that is not a terminal, EMIR-E327).
2. **The person gives you the manual's pages** and you transcribe: one value at a time with its table or page,
   in the unit the file asks for, and **the person confirms each value and its source** before it counts. Never
   convert a unit silently: show the arithmetic (a limit printed per cross-section, a current per line) and have
   the person confirm it.

Then `emir process check <file> --dspf <block.dspf>` until it exits 0 (EMIR-W320: a value with no source;
EMIR-W321: a likely unit slip; EMIR-W324: stacking nobody confirmed; read each), and only then `emir next --dspf
<block.dspf> --process <file>`. (`emir next` without `--process` plans exactly this: `process-new`,
`process-check`, then, when a simulation drives the run, the `choose-mode` step, whose commands already carry
`--process <file>`.) The design manual's two common forms are typed as printed: `width_loss_um` for `I_max = J x
(W - dW)`, and `derating: {model: black, ea_eV, n}` for Black's equation (instead of `jmax_factor`). Every report
then says per layer that the limits were entered by hand and from which document.

## Decision points

| you see | do |
|---|---|
| the PDK is in `pdks/` | use that file; never write another one with different numbers |
| a value is not in the documents you have | leave the key out (`# not published by the PDK - not checked`); ask the person whether they have the document |
| no ICT, or `emir import-ict` refuses it (EMIR-E325: encrypted, binary, not text) | never decrypt or guess: `emir process new --from-dspf <block.dspf> -o <pdk>.yaml`, then the person types each value with its source (or confirms each one you transcribe from a manual they give); `emir process check` must pass |
| `emir process check` FAILs EMIR-E320 | a required value is still empty: ask the person for it (never fill it in) |
| EMIR-W320 (a value with no source) or EMIR-W324 (stacking inferred, not confirmed) | ask the person for the document and table or page; every report says it until then |
| EMIR-W321 (outside the plausible range) | likely a unit slip (A for mA, mOhm for ohm, K for C, nm for um): show the person the value and the unit EMIR expects; they decide |
| EMIR-E331 (a temperature, derating or limit outside its range, in any process file) | it blocks the run: a K typed for C or uA for mA can make a failing wire read clean. Show the person the value and the unit; they correct it, or, if the manual really says so, list its path under `unit_confirmed:` themselves. Never confirm one yourself |
| the manual prints `I_max = J x (W - dW)`, or Black's equation for the derating | `width_loss_um: dW`; `derating: {model: black, ea_eV, n}` (never with `jmax_factor`) |
| the documents give limits at several temperatures | `em_tref` is the column you use; `jmax_factor` holds the derating table only when the documents give it |
| a fixed rms limit (a LEF's `ACCURRENTDENSITY RMS`) | `jmax_rms` (mA/um); a self-heating constant (an ICT) is `jmax_rms_k`; never both (`emir check` FAILs a layer with both) |
| EMIR-W307 a DSPF layer has no entry | add it to `dspf_layers` (extractor token -> layer or via name), or add the layer |
| EMIR-W302 a layer has no limit | expected where the PDK publishes none (sky130 `li1`): report it as not checked |
| EMIR-W204 resistors without width or cut annotation | the DSPF lacks `$W`/`$L` or cut counts: ask for an extraction with them |
| EMIR-E316 / EMIR-W314 an unknown key | a typo: `emir schema process` lists the keys |
| a licensed PDK's numbers | they stay with the person's PDK installation; the leak guard refuses ICT, tech and layer-map files here |

## Success criteria

- `emir check <block.dspf> --process <pdk.yaml>` on a DSPF of that PDK exits 0 with no FAIL, and every WARN is understood
  (an unpublished limit, an intended `dspf_layers` gap).
- Every value in the file names its source; nothing was estimated. For a file entered by hand: `emir process
  check <file> --dspf <block.dspf>` exits 0, and every value came from the person or was confirmed by them.
- The person agreed to where the file lives (their PDK installation, or `pdks/` for an open PDK with public data).

## What to report

Which file you used or made, where each group of values came from (for a file entered by hand: that the limits
were typed by hand, and from which document, as `emir report` says per layer), what the PDK does not publish (so EMIR reports
it as not checked: for example sky130 has no peak limits and no rms limits for vias; gf180mcu and ihp_sg13g2 have
no rms or peak limits at all), the temperature the limits hold at (`em_tref`), and "Screening, not sign-off."

## Failure modes

| code | exit | meaning | do |
|---|---|---|---|
| EMIR-E301 | 2 | the process file cannot be read | YAML syntax; `emir schema process` |
| EMIR-E316 | 2 | an unknown key inside a layer, via or cap model | fix the key (a typo) |
| EMIR-W314 | 0 | an unknown top-level key | fix or remove it |
| EMIR-W306 | 0 | the file is marked illustrative | its limits are not real: never report them as the PDK's |
| EMIR-W307 | 0 | a DSPF layer with no process entry | `dspf_layers`, or a new entry from the documents |
| EMIR-W302 | 0 | a layer with no limit | report as not checked |
| EMIR-E103 | 2 | a required input is not given (no `--process` and no site default) | give `--process` |
| EMIR-E325 | 2 | `emir import-ict` cannot read the ICT (encrypted, binary, not text) | never decrypt it: `emir process new`, the values from the person |
| EMIR-E320 | 2 | a required value is empty in a file entered by hand | ask the person for it |
| EMIR-E326 | 2 | `emir process new` will not overwrite a file | another `-o`; never delete the person's file |
| EMIR-E327 | 2 | `--interactive` without a terminal | never use it: the person types the values |
| EMIR-E323 | 2 | a wire no wider than its `width_loss_um` | check dW's unit (um, not nm) with the person |
| EMIR-W320 | 0 | a hand-entered value with no source | ask for the document; reports say it until then |
| EMIR-W321 | 0 | a value outside its plausible range | likely a unit slip: ask the person |
| EMIR-E331 | 2 | a temperature, derating or limit outside its range (any provenance) | ask the person to correct it or to list it under `unit_confirmed:`; never do either yourself |
| EMIR-E332 | 2 | a temperature at or below -273.15 C | a kelvin value or a sign slip: ask the person |
| EMIR-W326 | 0 | Black's derating below em_tref, applied as x1 | report the limits as quoted; `below_tref: extrapolate` only if the person's manual says so |
