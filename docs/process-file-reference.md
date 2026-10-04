# Process file reference

Every key of the process file, the YAML file `--process` points at. The JSON Schema is
`emir schema process` ([`agent/schemas/process.schema.json`](../agent/schemas/process.schema.json));
`tests/test_docs.py` checks that this page names exactly the keys the schema has, so
neither can drift. [`pdks/TEMPLATE.yaml`](../pdks/TEMPLATE.yaml) is a commented
example with every key, and [Onboarding a PDK](pdk-onboarding.md) says how to fill it.

**Units**: lengths in um, resistance in ohm (per square for wires, per cut for vias),
current limits in **mA per um of drawn width** for wires and **mA per cut** for vias,
temperatures in C. Internally EMIR works in A, V, um and ohm and converts once, when it
loads the file.

**Strictness**: an unknown key inside a layer, via or `cap_models` entry is a FAIL of the
input check (`EMIR-E316`: a misspelt limit would otherwise be silently unchecked); an
unknown top-level key is a warning (`EMIR-W314`).

**Empty values**: any value may be written `null` (or left empty): it is not given. The
default applies where there is one, and a limit left empty is reported as not checked;
emir never fills one in. A required value left empty is a FAIL (`EMIR-E320`). `emir
process new` writes a file with every value empty for a person to fill, and `emir
process check` lists what is still empty ([Entering EM data by
hand](pdk-onboarding.md#entering-em-data-by-hand)).

**Plausible ranges**: the input check warns (`EMIR-W321`) when a value is outside the
range its quantity normally takes in the unit emir reads it in (for example a wire
limit outside 0.05 to 100 mA/um, a via limit outside 0.005 to 50 mA per cut, a sheet
resistance outside 0.002 to 50 ohm/square, `em_tref` above 200 C), naming the likely
slip (A for mA, mOhm for ohm, K for C, nm for um); and when limits are out of order
(`EMIR-W322`: peak >= rms >= avg) or an upper metal is more resistive than the one
below it (`EMIR-W323`). The ranges are `QUANTITIES` in `emir/process_file.py`.

## Top level

| key | type | required | default | meaning |
|---|---|---|---|---|
| `pdk` | string or null | no | the file name | the name shown in reports. A name containing "illustrative" makes `emir check` warn that the limits are not real (`EMIR-W306`). Its **first word** is the PDK's directory under `$PDK_ROOT`: `emir extract --tool magic` finds magic's startup file at `$PDK_ROOT/<first word>/libs.tech/magic/<first word>.magicrc` (a word that is not a plain directory name is refused, `EMIR-E641`; `--magicrc` overrides) |
| `provenance` | `hand-entered`, `imported-ict`, `public-pdk-data` or `mixed` | no | not stated (`EMIR-I321`) | where the file's values came from: typed from the design manual (`emir process new`), imported from an ICT (`emir import-ict` sets it), the PDK's public files (the three in `pdks/`), or a mix (an entry may then carry its own). The run summary (`limits_provenance`), `emir report` and the HTML report say it per layer. A `hand-entered` or `mixed` file needs a source for every value: each one without gets `EMIR-W320`, on every run |
| `sources` | mapping {key: text} | no | none | where `em_tref`, `temp_reference`, `jmax_factor` and `derating` came from: the document and table or page, as text (`EMIR-E329` if not text, `EMIR-W325` for a key the file does not hold) |
| `em_tref` | number, C | **yes** | none (`EMIR-E312`; empty: `EMIR-E320`) | the temperature the average EM limits are quoted at. Also the default analysis temperature (`--em-temp` overrides) |
| `temp_reference` | number, C | no | 25 | the temperature `sheet_res` is quoted at; used with `tc1` and `tc2` to scale wire resistance to the analysis temperature |
| `warn_fraction` | number in (0, 1] | no | 0.8 | the ratio from which an item is WARN. A tool setting, not PDK data (`EMIR-E311` outside the range) |
| `jmax_factor` | mapping {C: factor} | no | none: no derating (`EMIR-I302`) | derates the **average** limits of wires and vias at the analysis temperature: piecewise linear between its points, clamped at its ends, 1.0 at `em_tref`. Keys must be temperatures (`EMIR-E314`) |
| `derating` | `{model: black, ea_eV, n}` | no | none (both values empty: no derating) | the other form of the same derating, as design manuals print it: Black's equation, `J(T)/J(em_tref) = exp[(ea_eV / (n k)) (1/T - 1/Tref)]` with T and Tref (`em_tref`) in kelvin and k = 8.617333262e-5 eV/K; `ea_eV` the activation energy in eV, `n` the current-density exponent. Give it or `jmax_factor`, never both (`EMIR-E321`); half entered, another model or a value that is not positive is refused (`EMIR-E322`), and so is a key it does not have (`EMIR-E316`). The input check states the factor at the analysis temperature (`EMIR-I322`). Below `em_tref` the equation would raise the limits: they are applied as quoted (x1, `EMIR-W326`) unless `below_tref: extrapolate`; a temperature at or below -273.15 C is refused (`EMIR-E332`) |
| `unit_confirmed` | list of paths | no | none | values outside the range their quantity normally takes (most often a unit slip, `EMIR-W321`) that the person checked against the design manual: `[em_tref, layers/metal1/jmax_avg]`. Such a value on a temperature, the derating or a limit blocks the run (`EMIR-E331`), whatever the file's `provenance` (or none), until it is corrected or listed here; an agent never lists one itself |
| `dspf_layers` | mapping {token: name} | no | none | maps a DSPF layer token, or an integer `$lvl` level number, to a name in `layers` or `vias`, for the tokens that do not match by name (matching ignores case and drops `_`-suffixed parts first) |
| `magic` | mapping | no | none | the magic route (`emir extract --tool magic`): magic's layer names, the devices' terminal names, the magic to run. Keys below |
| `layers` | mapping {name: entry} | **yes** | | the conductor layers, keyed by the name reports use (at least one; `EMIR-E303`). Entries below |
| `vias` | mapping {name: entry} | no | none | the cut layers, keyed by the name reports use. Entries below |
| `cap_models` | mapping {model: entry} | no | none | sizes capacitor and MOS-decap **devices** that extract as geometry instead of a value; used only by dynamic IR. Entries below |
| `ict_conductor_pattern` | string | no | `{layer}_conn` | `emir import-ict`: the ICT conductor block name for a layer (`{layer}` lower-cased, `{Layer}` as written); an entry's `ict_name` overrides it |
| `ict_process` | string | no | | written by `emir import-ict` (the ICT's process name), for the record only |

## A conductor layer: `layers.<name>`

| key | type, unit | required | meaning |
|---|---|---|---|
| `gds` | [layer, datatype] or null | no | the drawing layer, for the person (the PDK's layer map): no path reads it (`[layer, datatype]` when given, `EMIR-E307`) |
| `sheet_res` | number, ohm/square | **yes** | at `temp_reference` |
| `tc1` | number, 1/C | no | linear TCR: `R(T) = R x (1 + tc1 dT + tc2 dT^2)`, `dT = T - temp_reference` |
| `tc2` | number, 1/C^2 | no | quadratic TCR |
| `min_width` | number, um | no | the minimum drawn width: a record of the PDK's rule, which no check reads |
| `jmax_avg` | number, mA/um | no | the DC average limit at `em_tref`, derated by `jmax_factor` at other temperatures |
| `jmax_rms` | number, mA/um | no | a **fixed** rms limit, as a tech LEF publishes it (`ACCURRENTDENSITY RMS`); `--delta-t` does not change it |
| `jmax_rms_k` | number, (mA/um)^2 per C | no | the self-heating form an ICT gives: the rms limit is `sqrt(jmax_rms_k x delta_T)` mA/um, `delta_T` from `--delta-t` (default 5 C). Give `jmax_rms` or `jmax_rms_k`, never both (`EMIR-E310`) |
| `jmax_peak` | number, mA/um | no | the peak limit (pulse-width independent) |
| `width_loss_um` | number, um | no | the width loss dW of a limit printed as `I_max = J x (W - dW)`: the avg, rms and peak wire limits apply to the drawn width W (the DSPF's `$W`, else `$SI_W`) less dW, and a fix widens by the ratio of `W - dW`. A segment no wider than dW is refused (`EMIR-E323`). Empty or 0: the limit is per um of drawn width |
| `sources` | mapping {key: text} | no | where each value of this entry came from (document, table or page). `emir process new` writes `inferred from the DSPF ... check` for a via's stacking it inferred (`EMIR-W324` until a person replaces it) |
| `provenance` | `hand-entered`, `imported-ict` or `public-pdk-data` | no | this entry's own, in a `mixed` file |
| `estimate` | boolean | no | true flags this layer's limits as estimates in every report (default false) |
| `ict_name` | string | no | `emir import-ict`: the ICT conductor block for this layer, when the name pattern does not find it |

A layer with none of `jmax_avg`, `jmax_rms`, `jmax_rms_k`, `jmax_peak` is not checked
for EM at all (`EMIR-W302`); a missing metric is listed under "Not checked" in every
report. A fixed `jmax_rms` below `jmax_avg` is warned (`EMIR-W301`): the average check
could then never bind.

## A via: `vias.<name>`

| key | type, unit | required | meaning |
|---|---|---|---|
| `gds` | [layer, datatype] or null | no | the cut layer (as for a layer) |
| `below` | string | **yes** | the conductor under the cut, a name in `layers` (`EMIR-W303` if it is not; empty: `EMIR-E320`) |
| `above` | string | **yes** | the conductor over the cut |
| `res` | number, ohm per cut | **yes** | not scaled with temperature |
| `vcwidth` | number, um | no | the cut side: turns an extracted cut **area** (`$A`) into a cut count. No default: without it such a via is checked as one cut, which is conservative, and warned (`EMIR-W308`) |
| `imax_avg` | number, mA per cut | no | the DC average limit at `em_tref`, derated by `jmax_factor` |
| `imax_rms` | number, mA per cut | no | the rms limit |
| `imax_peak` | number, mA per cut | no | the peak limit |
| `estimate` | boolean | no | flags the average limit as an estimate in reports |
| `rms_peak_estimate` | boolean | no | flags `imax_rms` and `imax_peak` as estimates (an older `emir import-ict` set it when it derived them as 5 x avg): flagged values are **not applied**, so those metrics are reported as not checked |
| `sources` | mapping {key: text} | no | as for a layer |
| `provenance` | `hand-entered`, `imported-ict` or `public-pdk-data` | no | as for a layer |
| `ict_name` | string | no | `emir import-ict`: the ICT via block |

A via with no `imax_*` limit is not checked (`EMIR-W304`).

## The magic route: `magic`

| key | type | meaning |
|---|---|---|
| `binary` | string, list or null | the magic to try, a magic program's bare name on `$PATH`, starting with `magic` (a list: in order), after `--magic` and `$EMIR_MAGIC`; a path or another program is refused (`EMIR-E648`: a process file comes with a PDK, so it may not name what EMIR runs); it must be one built with the `geoout` patch (`extresist geoout`), which `emir extract` checks (`EMIR-E643`) |
| `layers` | mapping {magic name: name} | magic's layer names (the PDK's magic technology file, `types` section) onto names in `layers` or `vias` (`locali: li1`, `viali: mcon` on sky130). `dspf_layers` wins where both name a token; a name in neither folds as `dspf_layers` describes, and one that matches nothing carries its current but is not checked (`EMIR-W307`) |
| `devices` | mapping {model or glob: [terminals]} | each device model's terminal names in the order magic writes its nodes, the order of the model's `.subckt` (`"sky130_fd_pr__nfet_*": [d, g, s, b]`); the exact model name wins over a glob. They name the DSPF's `*|I` pins, so a cc-grid simulation's probes match them. A model it does not name gets `t1, t2 ...` (`EMIR-W643`) |
| `sources` | mapping {key: text} | where the values came from |

A key the section does not have is refused (`EMIR-E316`).

## A capacitor or decap model: `cap_models.<model>`

Used only by dynamic IR (`emir run --dynamic`, and rcc mode's dynamic droop), to size
devices that a DSPF carries as geometry instead of a value. Two entry names are
special: `default` sizes a capacitor device with no model name (extractors often omit
it), and `mos_default` a MOS decap whose model has no entry. An unsized decap is
excluded and reported, never counted as zero silently.

| key | type, unit | for | meaning |
|---|---|---|---|
| `cj` | number, F/m^2 | capacitor | area capacitance: `C = cj x area + cjsw x perimeter` |
| `cjsw` | number, F/m | capacitor | perimeter capacitance |
| `cox` | number, F/m^2 | MOS decap | `C = cox x W x L` |
| `ron_sq` | number, ohm/square | MOS decap | the access resistance when no fit is given: `R = ron_sq x L / W / 6` |
| `esr_a` | number, ohm | MOS decap | a fitted access resistance, preferred over `ron_sq`: `R = esr_a + esr_w x W_um + esr_l / L_um` |
| `esr_w` | number, ohm/um | MOS decap | the fit's width term |
| `esr_l` | number, ohm x um | MOS decap | the fit's length term |
| `esr_w_range` | [min, max], um | MOS decap | the widths the fit covers; a device outside is reported as extrapolated |
| `esr_l_range` | [min, max], um | MOS decap | the lengths the fit covers |
| `esr_fit_freq` | number, Hz | MOS decap | the frequency the fit was made at (recorded) |
| `sources` | mapping {key: text} | both | where each value came from, as for a layer |
| `provenance` | `hand-entered`, `imported-ict` or `public-pdk-data` | both | as for a layer |

A decap is only as good as its access resistance: the validation suite shows that
leaving out a 50 ohm access resistance under-predicts the droop by 84.7 % on its test
case (`emir validate`, case "decap series R").

## A minimal file

```yaml
pdk: my_pdk (from the tech LEF, rev X)
em_tref: 105.0                  # C, where the LEF quotes its limits
temp_reference: 25.0
layers:
  M1:
    gds: [10, 0]
    sheet_res: 0.08             # ohm/sq at 25 C
    tc1: 0.0035
    min_width: 0.1
    jmax_avg: 1.5               # mA/um at 105 C
  M2:
    gds: [12, 0]
    sheet_res: 0.08
    tc1: 0.0035
    min_width: 0.1
    jmax_avg: 1.5
vias:
  V1:
    gds: [11, 0]
    below: M1
    above: M2
    res: 4.0                    # ohm per cut
    vcwidth: 0.1                # um
    imax_avg: 0.2               # mA per cut
```

(The numbers are invented; a real file cites a source for each.) Validate any file
with `emir check <dspf> --process <file>`; the check reads the file against this
schema first.
