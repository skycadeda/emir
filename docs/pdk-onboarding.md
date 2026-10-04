# Onboarding a PDK

EMIR keeps every process number in one YAML file per PDK, the **process file**, passed
with `--process`. No limit lives in the code. This page says where the files come from,
how to write one for your PDK, the unit traps to avoid, and how to prove the file is
right before you trust a result made with it. Every key is in the
[process file reference](process-file-reference.md).

**Screening, not sign-off.** A process file you write is your reading of the PDK's
data, not the foundry's qualified rule deck.

## The files in `pdks/`

| file | PDK | metal stack in the file | EM limits quoted at |
|---|---|---|---|
| [`sky130.yaml`](../pdks/sky130.yaml) | SkyWater SKY130 (`sky130A`) | li1, met1 to met5; mcon, via, via2 to via4 | Tj = 90 C |
| [`gf180mcu.yaml`](../pdks/gf180mcu.yaml) | GlobalFoundries GF180MCU (`gf180mcuD`: 5 metals, 1.1 um top metal) | Metal1 to Metal5; Via1 to Via4 | 125 C |
| [`ihp_sg13g2.yaml`](../pdks/ihp_sg13g2.yaml) | IHP SG13G2 | Metal1 to Metal5, TopMetal1 and 2; Via1 to Via4, TopVia1 and 2 | 105 C |
| [`TEMPLATE.yaml`](../pdks/TEMPLATE.yaml) | none: invented values | every key EMIR reads, with units and comments | |

The three PDK files are built only from the PDKs' own public files (Apache-2.0), and
every value names its source on its own line, as `[FILE]:line` (or a section and
parameter name for a PDF, or a URL). Each file's header lists the source files,
relative to the PDK root variable it names (`$PDK_ROOT`, `$GF180_PDK_ROOT`,
`$IHP_PDK_ROOT`), and the PDK release they were read from. With the PDKs installed, the
test suite checks that every cited line still holds its value:

```sh
pip install -e '.[dev]'          # once, from the clone: pytest and the other test tools
PDK_ROOT=... GF180_PDK_ROOT=... IHP_PDK_ROOT=... pytest tests/test_pdks.py -k citations
```

[`pdks/README.md`](../pdks/README.md) lists what each file does **not** check because
the PDK does not publish the limit (no peak limits on any of the three, no rms limits
on vias, no derating tables), and the choices made where the PDK's own files disagree.

## Write one for your PDK

1. **Copy the template.** `cp pdks/TEMPLATE.yaml my_pdk.yaml` (from a clone or the
   offline bundle, which both carry `pdks/`), and change `pdk:`. A name containing
   "illustrative" makes `emir check` warn that the limits are not real, so remove that
   word. The first word of `pdk:` is also the PDK's directory under `$PDK_ROOT`: `emir
   layout --testbench` looks for magic's startup file at
   `$PDK_ROOT/<first word>/libs.tech/magic/<first word>.magicrc` (so write
   `pdk: ihp-sg13g2 (my reading)`, not `pdk: IHP SG13G2`), unless you pass `--magicrc`.
2. **Fill in each conductor layer** from your PDK: the GDS layer and datatype, sheet
   resistance (ohm/square) at `temp_reference`, the TCRs, the minimum width, and the EM
   limits in **mA per um of drawn width**, at the temperature you put in `em_tref`.
   Cite the source of each value in a comment.
3. **Fill in each via**: its GDS layer and datatype, the layers `below` and `above`,
   resistance **per cut** (ohm), the cut side `vcwidth` (um), and the limits in **mA
   per cut**.
4. **Leave out what your PDK does not publish.** A missing limit is reported as "not
   checked" in every report; never estimate one. If you must carry an estimate, mark
   the entry `estimate: true` so every report says so (a via's `rms_peak_estimate:
   true` makes its rms and peak limits not applied at all).
5. **Map the extractor's layer names** that do not match yours with `dspf_layers`
   (below).
6. **Validate** it (below), before any result made with it is acted on.

### From a foundry ICT

Keep the geometry and resistance keys (`gds`, `sheet_res`, `res`, `min_width`,
`below`, `above`) in the file, then:

```sh
emir import-ict --ict <pdk>.ict --emir my_pdk.yaml            # rewrites my_pdk.yaml
emir import-ict --ict <pdk>.ict --emir my_pdk.yaml -o new.yaml   # or writes a copy
```

It fills `jmax_avg`, `jmax_peak`, `jmax_rms_k`, `tc1`/`tc2`, `jmax_factor`, `em_tref`
and the via limits from the ICT's EM models, and says what it could not find. Its
conversions, and their direction:

- A DC average limit `<mult> * <J> * (w - dw)` becomes `<J>` mA/um with
  `width_loss_um: dw` (with its source), so a minimum-width wire is judged on `w - dw`, as
  the ICT judges it (the largest offset of the avg and peak lines, the strictest).
- Where the ICT has a long-wire branch (`L > Lmin`), it is used: the conservative rule,
  since EMIR does not credit short wires.
- The rms (self-heating) limit becomes `jmax_rms_k`, its wide-wire asymptote
  `sqrt(K x delta_T)`, which is the minimum over width: conservative for narrow wires.
- Where the ICT gives vias only a DC average limit, no rms or peak limit is written:
  those metrics are reported as not checked on the vias (`EMIR-W313`), since EMIR never
  estimates a limit. Curated (unflagged) values already in the file are kept; values an
  older import estimated (flagged `rms_peak_estimate: true`) are removed, and are not
  applied by a run either.

ICT block names: a layer `Metal1` is looked up as the conductor block given by
`ict_conductor_pattern` (default `{layer}_conn`), then as `metal1` and `Metal1`; an
entry's `ict_name` names its block outright.

A file made from a licensed PDK's data is that foundry's confidential data: keep it
with your PDK installation, never in this repository. The leak guard
(`scripts/leak_guard.py`, run by the tests and pre-commit) refuses ICT, tech-file and
layer-map files and known licensed-PDK names.

## Entering EM data by hand

When no readable process data exists for your PDK (it is not in `pdks/`, and there is
no ICT, or the ICT is encrypted), type the EM data from the design manual's EM tables.
`emir import-ict` refuses an ICT it cannot read (binary, compressed, encrypted, or not
text: `EMIR-E325`), and never tries to decrypt it or guess its content. EMIR guides
the typing:

```sh
emir process new --from-dspf block.dspf -o my_pdk.yaml     # the fill-in file
emir process check my_pdk.yaml --dspf block.dspf            # what is still missing
```

1. **The fill-in file.** `emir process new` reads the DSPF and writes a process file
   for exactly the layers and vias it uses: one entry per layer token (`$metal1`,
   `$layer=M1`) or `$lvl` number, the `dspf_layers` map filled in, wires and vias told
   apart by their annotation (a width; a cut count or area), each via's `below` and
   `above` inferred from the wires its resistors join (ordered by their `$lvl`, else by
   the numbers in their names) and marked `inferred from the DSPF ... check` in its
   `sources:`, and `cap_models` entries for the capacitor and MOS-decap models the DSPF
   holds (for `--dynamic`). Every value is `null`, with its unit and meaning in a
   comment, and a `sources:` slot beside it; `provenance: hand-entered` says how the
   file was made. It never fills in a value and never overwrites a file (`EMIR-E326`).
   Without a DSPF, `--layers M1,M2 --vias V1` names the entries (the stacking is then
   left for you).
2. **Type each value** as the design manual prints it, in the unit shown, with its
   source: `sources: {jmax_avg: "DM rev 2.1, table 9-4, p. 112"}`. Leave a value null
   when the manual does not give it: EMIR reports that check as not checked. In a
   terminal, `emir process new ... --interactive` asks for each value and its source in
   turn (Enter leaves a value empty; a non-number is refused and asked again; `=` repeats
   the previous source, Enter leaves a value without one). It refuses a pipe or a
   redirected stdin (`EMIR-E327`); that checks stdin, not who types, so the rule that an
   agent never drives it is the playbook's: the person types the values.
3. **Check it.** `emir process check` lists the values still empty, required
   (`em_tref`, each layer's `sheet_res`, each via's `res`, `below` and `above`:
   `EMIR-E320`, exit 2) and optional (`EMIR-I320`); the values with no source
   (`EMIR-W320`); the stacking nobody has confirmed (`EMIR-W324`); likely unit slips
   (`EMIR-W321`: each quantity has a plausible range in the unit EMIR reads, for
   example 0.05 to 100 mA/um for a wire limit, so a value typed in A/um or uA/um is
   caught; a slip by a factor of ten mostly is not). A likely slip on a temperature
   (`em_tref`, `temp_reference`), the derating or a limit blocks the run, whatever the
   file's `provenance:` says or if it states none
   (`EMIR-E331`: a K typed for C, or uA for mA, can make a failing wire read clean) until
   the person checks it and either corrects it or lists its path under `unit_confirmed:`;
   limits out of order (`EMIR-W322`:
   peak >= rms >= avg); an upper metal more resistive than the one below
   (`EMIR-W323`); and, with `--dspf`, the DSPF layers the file does not cover
   (`EMIR-W307`) and the wires no wider than their width loss (`EMIR-E323`). It must
   pass before any run. `emir check --process` runs the same checks.
4. **Every run says where the limits came from.** The run summary's
   `limits_provenance`, `emir report` (its section "Where the limits came from") and
   the HTML report's provenance table say per layer and via that the limits were
   entered by hand, and from which document; a value with no source is named, and
   `EMIR-W320` is raised on every run until it has one.

### The forms design manuals print

Two forms are read as printed, so nothing is converted by hand:

- **A width loss.** A limit printed as `I_max = J x (W - dW)` is `jmax_avg: J` (and
  `jmax_rms`, `jmax_peak` alike) with `width_loss_um: dW`: the wire limits apply to the
  drawn width less dW. For J = 2.0 mA/um and dW = 0.02 um, a 0.30 um wire may carry
  2.0 x (0.30 - 0.02) = 0.56 mA, not 2.0 x 0.30 = 0.60 mA: its ratio reads 0.30 / 0.28
  = 1.071 times higher, 7 % stricter. A segment no wider than dW has no limit and is
  refused (`EMIR-E323`); most often dW was typed in nm.
- **Black's equation.** A derating printed as Black's equation, `MTTF = A J^-n exp(Ea /
  (k T))`, is `derating: {model: black, ea_eV: Ea, n: n}`: at equal lifetime the avg
  limits scale by `J(T)/J(Tref) = exp[(Ea / (n k)) (1/T - 1/Tref)]`, with T and Tref
  (`em_tref`) in kelvin and k = 8.617333262e-5 eV/K. For Ea = 0.9 eV, n = 2, `em_tref`
  105 C and an analysis at 125 C: Ea / (n k) = 0.9 / (2 x 8.617333262e-5) = 5222.0 K,
  1/398.15 - 1/378.15 = -1.3284e-4 per K, and the factor is exp(5222.0 x -1.3284e-4) =
  exp(-0.69368) = 0.4997: the avg limits halve. It is the alternative to a
  `jmax_factor` table; give one of the two (`EMIR-E321`). Below `em_tref` the equation
  would raise the limits; most manuals define the derating above `em_tref` only, so
  there they are applied as quoted (x1, `EMIR-W326`) unless the file says `derating:
  {..., below_tref: extrapolate}`. A temperature at or below -273.15 C is refused
  (`EMIR-E332`).

`emir validate` holds both forms to a closed form (case "hand-entry forms" in
[Validation](validation.md)).

### What an agent does

An agent never invents, estimates or derives a limit. It writes the fill-in file; then
the person types each value, or the agent transcribes the values from a design manual
the person gives it and the person confirms each one with its source; `emir process
check` must pass before any run ([playbook 04](../agent/playbooks/04-onboard-a-pdk.md),
"No readable file"). `emir next` without `--process` plans these steps.

## Unit traps

Most wrong results from a new process file are unit errors. Check each:

| trap | the right form |
|---|---|
| a limit quoted as current density per cross-section (for example MA/cm^2) | EMIR wants **mA per um of drawn width**: multiply the density by the metal thickness and convert (1 MA/cm^2 = 10 mA/um^2) |
| a limit quoted as an absolute current for a width range (narrow wires) | divide by the width, and keep the strictest value over the widths you use (IHP SG13G2's file explains its choice) |
| a via limit quoted per via array, or per um^2 of cut | EMIR wants **mA per cut** |
| a tech LEF's cut-layer `DCCURRENTDENSITY AVERAGE` (no unit is written: per cut, or per um^2 of cut?) | confirm it against the design manual's per-via table before writing `imax_avg`: the shipped `gf180mcu.yaml` (DRM Table 14.4, 0.18 mA per via) and `ihp_sg13g2.yaml` (process spec 2.15, "mA/Via") show how |
| via resistance quoted per array | EMIR wants **ohm per cut**; it divides by the cut count itself |
| `vcwidth` given as a cut area | it is the cut **side** in um; EMIR squares it to turn an extracted cut area (`$A`) into a count |
| limits quoted at one temperature, `em_tref` set to another | `em_tref` is the temperature the **limits** hold at (sky130's LEF: 90 C); `temp_reference` is the temperature the **sheet resistance** is quoted at; they are different keys |
| an rms limit from a tech LEF (`ACCURRENTDENSITY RMS`) written as `jmax_rms_k` | a LEF's fixed rms value is `jmax_rms` (mA/um); `jmax_rms_k` is an ICT's self-heating constant, (mA/um)^2 per C. Never both on one layer (`EMIR-E310`) |
| a bidirectional (AC) average limit used as the DC limit | give the unidirectional DC limit (gf180mcu's file uses the unidirectional values for this reason). EMIR checks the magnitude of the *signed* average against it: a current that reverses cancels towards zero, the least conservative reading of a bidirectional rule, and EMIR does not apply a PDK's recovery or absolute-average rule ([Limits](limits.md)) |
| A or A/um | the file is in **mA** and **mA/um**; EMIR converts to A internally |

`emir check` catches some of these (a negative or non-numeric value, an rms limit below
the average one, `EMIR-W301`; a value outside its quantity's plausible range, which is
where an A-for-mA or nm-for-um slip lands, `EMIR-W321`; limits out of order,
`EMIR-W322`), not all: a limit off by a factor of ten looks like a plausible number.

## Mapping the extractor's layer names: `dspf_layers`

A DSPF names each resistor's layer with a token (`$metal1`, `$layer=M1`, or only a
level number `$lvl=2`). EMIR matches tokens to the file's layer and via names without
regard to case, then with `_`-suffixed parts dropped (`metal1_conn` becomes `Metal1`,
reported as `EMIR-I305`). Map what does not fold:

```yaml
dspf_layers:
  m1_conn_a: Metal1     # an extractor token -> a name in layers: or vias:
  1: Metal1             # an integer key: a `$lvl=1` level number, for resistors with no name
```

A DSPF layer with no match is **not checked** and the input check says so
(`EMIR-W307`); a run with unmapped layers that carry current is not clean.

## Validate the file

Before you act on any result made with a new process file:

1. **The checker.** `emir check --process my_pdk.yaml` checks the file on its own, with
   no DSPF (the schema, the units, limits that cannot bind, layers with no limit); then
   `emir check <dspf> --process my_pdk.yaml` checks it against an extraction. Each exits
   0 when the file passes. Read every WARN: a layer or via with no limit (`EMIR-W302`,
   `W304`), a DSPF layer that maps to nothing (`W307`), a via with a cut area and no
   `vcwidth` (`W308`).
2. **A hand-checkable case.** Run one wire whose answer you can work out, at its limit.
   For sky130, `met1` allows 2.8 mA/um on average, so a minimum-width 0.14 um wire may
   carry 2.8 x 0.14 = 0.392 mA, and one `via` cut 0.29 mA; `tests/test_pdks.py` runs a
   strap of exactly that wire through `emir run` and gets the ratios by hand. Do the
   same for your stack: a small DSPF (copy the bundled sample's format,
   `emir/data/sample/rail.dspf`), or, on an open PDK, a small cell extracted with
   `emir extract --tool magic` ([`examples/08_open_layout`](../examples/08_open_layout/)
   is one, on sky130).
3. **Temperature.** Run at `em_tref` and at a second temperature; with a `jmax_factor`
   table the average limits must move by the factor you expect, and the resistance by
   your TCRs.
4. **The citations.** Keep a `[FILE]:line` (or document and table) comment on every
   value, so the next person can check it against the PDK, as `tests/test_pdks.py`
   does for the three open PDKs.

The template itself is tested: `tests/test_pdks.py` checks that it documents every key
the checker knows, and that a filled copy passes `emir check`.

## Where the process file is found

`--process FILE` on the command line wins; then `$EMIR_PROCESS`; then the site file's
`process:` key. The site file (`$EMIR_SITE`, else `~/.emir/site.yaml`) holds a site's
paths and is never committed. A site file in the working directory is not read by
itself, since it names programs emir runs: point `$EMIR_SITE` at it.
`emir info` says what was found and where.
