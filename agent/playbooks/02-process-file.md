# 02 The process file: the PDK's limits, from its files or typed by hand

EMIR knows nothing about a process until a process file tells it: per layer the sheet resistance, TCR and EM
limits in mA per um of drawn width; per via the resistance and limits per cut; the temperature the limits hold at.
Every value comes from the PDK's own documents, with its source. **Never invent, estimate or "typical" a limit.**
**Screening, not sign-off.**

## When to use

- `emir next` lists `missing`: "a process file with your PDK's EM limits".
- The person names a PDK: first look in `pdks/` (sky130, gf180mcu, ihp_sg13g2, built from the PDKs' public files
  with a source on every value).
- The PDK is not there: fill a copy of `pdks/TEMPLATE.yaml` from its documents, or import an ICT.
- No readable file at all (no ICT, or EMIR-E325: encrypted, binary, not text): the person enters the values by hand.
- `emir check` warns EMIR-W307 (a DSPF layer with no process entry) or EMIR-W302 (a layer with no limit).

## Inputs

| input | where the values come from |
|---|---|
| per layer: sheet resistance, TCR, avg / rms / peak limits (mA/um) | the tech LEF, the design manual's EM tables |
| per via: below / above, ohm per cut, cut side `vcwidth`, limits (mA/cut) | the tech LEF, the design manual |
| `em_tref`, the temperature the limits hold at | the design manual (required: no default) |
| an ICT, optionally | the person's PDK installation; it never enters this repository |

## Commands

### Worked example (runs as printed from the repository root)

The template, checked against the bundled sample DSPF:

<!-- test: run -->
```sh
ls pdks/
mkdir -p emir_out
cp pdks/TEMPLATE.yaml emir_out/mypdk.yaml
emir check emir/data/sample/rail.dspf --process emir_out/mypdk.yaml --currents '{"VDD": 0.004, "MP0:s": -0.001, "MP1:s": -0.001, "MP2:s": -0.001, "MP3:s": -0.001}' --format json
```

<!-- test: expect -->
```json
{"schema": "emir.check/1", "passed": true,
 "findings": [{"code": "EMIR-W307", "level": "WARN"}, {"code": "EMIR-W306", "level": "WARN"}]}
```

EMIR-W307: the DSPF's layer `cont` has no process entry, so its segments are NOT checked: map it in `dspf_layers`
to the via the documents say it is. EMIR-W306: the file is ILLUSTRATIVE; a real file drops the word.

No readable file: the fill-in file for exactly the layers and vias the DSPF uses, every value `null` with its unit
and a `sources:` slot. Until the values are entered, the check refuses it:

<!-- test: run -->
```sh
emir process new --from-dspf emir/data/sample/rail.dspf -o emir_out/handpdk.yaml --pdk handpdk --format json
emir process check emir_out/handpdk.yaml --dspf emir/data/sample/rail.dspf --format json  # exit 2
```

<!-- test: expect -->
```json
{"schema": "emir.process_check/1", "passed": false, "provenance": "hand-entered",
 "findings": [{"code": "EMIR-E320", "level": "FAIL"}]}
```

### On the person's files

```sh
emir check <block.dspf> --process pdks/gf180mcu.yaml --net <VDD> --currents '{"VDD": 0.001, "M0:s": -0.001}' --format json
emir import-ict --ict <pdk>.ict --emir <pdk>.yaml -o <pdk>.emir.yaml --format json
emir process new --from-dspf <block.dspf> -o <pdk>.yaml --format json
emir process check <pdk>.yaml --dspf <block.dspf> --format json
```

A new PDK from its documents: copy `pdks/TEMPLATE.yaml`, fill every layer and via with the source of each value in
a comment, leave out what the PDK does not publish (reported as not checked), map the extractor's names with
`dspf_layers`, and check it. `emir import-ict` without `-o` rewrites `--emir` in place: ask first.

**Values by hand**: one of two ways, and nothing else. (1) The person types each value from the design manual, in
the unit the comment shows, with its source (`sources: {jmax_avg: "DM rev 2.1, table 9-4, p. 112"}`); at a
terminal they may run `emir process new ... --interactive` themselves, never you (EMIR-E327). (2) The person gives
you the manual's pages, you transcribe one value at a time with its table or page, and **the person confirms each
value and its source**. Never convert a unit silently: show the arithmetic. Then `emir process check` until it
exits 0, and only then `emir next --dspf <block.dspf> --process <pdk>.yaml`. A limit printed as
`I_max = J x (W - dW)` is `width_loss_um: dW`; Black's equation is `derating: {model: black, ea_eV, n}`.

## Decision points

| you see | do |
|---|---|
| the PDK is in `pdks/` | use that file; never write another with different numbers |
| a value is not in the documents | leave it out; ask whether the person has the document |
| EMIR-E325 (an ICT EMIR cannot read) | never decrypt or guess: `emir process new`, the values from the person |
| EMIR-E320 | a required value is still empty: ask the person |
| EMIR-W320 (no source), EMIR-W324 (stacking inferred) | ask for the document and table or page |
| EMIR-W321, EMIR-E331 (outside the plausible range) | likely a unit slip: show the person the value and the unit EMIR expects; they correct it or list it under `unit_confirmed:`, never you |
| a fixed rms limit (a LEF's) | `jmax_rms`; an ICT's self-heating constant is `jmax_rms_k`; never both |
| EMIR-W307 | `dspf_layers` (extractor token -> layer or via name), or a new entry from the documents |
| a licensed PDK's numbers | they stay with the person's PDK installation; the leak guard refuses ICT, tech and layer-map files here |

## Success criteria

- `emir check <block.dspf> --process <pdk.yaml>` exits 0, and every WARN is understood.
- Every value names its source; nothing was estimated. A file entered by hand passes `emir process check`, and
  every value came from the person or was confirmed by them.

## What to report

Which file you used or made, where each group of values came from (typed by hand, imported, or the PDK's public
data, and from which document), what the PDK does not publish (reported as not checked: sky130 has no peak limits
and no via rms limits), `em_tref`, and "Screening, not sign-off."

## Failure modes

| code | exit | meaning | do |
|---|---|---|---|
| EMIR-E301 | 2 | the process file cannot be read | YAML syntax; `emir schema process` |
| EMIR-E316 | 2 | an unknown key in a layer or via | a typo: `emir schema process` lists the keys |
| EMIR-E326 | 2 | `emir process new` will not overwrite a file | another `-o`; never delete the person's file |
| EMIR-E323 | 2 | a wire no wider than its `width_loss_um` | dW's unit (um, not nm), with the person |
| EMIR-E332 | 2 | a temperature at or below -273.15 C | a kelvin value or a sign slip: ask |
| EMIR-W326 | 0 | Black's derating below `em_tref`, applied as x1 | report the limits as quoted |
