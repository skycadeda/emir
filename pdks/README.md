# Process files for open PDKs

`emir` reads the process data it needs (layer resistance, EM current limits,
GDS layer numbers, the via stack) from one YAML file per PDK, passed with
`--process`. This directory holds that file for three open PDKs, built only
from the PDKs' own public files, and a template for making your own.

| file | PDK | metal stack in the file | EM limits quoted at |
|---|---|---|---|
| [`sky130.yaml`](sky130.yaml) | SkyWater SKY130 (`sky130A`) | li1, met1-met5; mcon, via, via2-via4 | Tj = 90 C |
| [`gf180mcu.yaml`](gf180mcu.yaml) | GlobalFoundries GF180MCU (`gf180mcuD`: 5 metals, 1.1 um top metal) | Metal1-Metal5; Via1-Via4 | 125 C |
| [`ihp_sg13g2.yaml`](ihp_sg13g2.yaml) | IHP SG13G2 | Metal1-Metal5, TopMetal1-2; Via1-Via4, TopVia1-2 | 105 C |
| [`TEMPLATE.yaml`](TEMPLATE.yaml) | none: invented values | every key emir reads, with units | - |

```bash
emir check block.dspf --process pdks/sky130.yaml --net VDD --currents '{"VDD": 4e-3, "M0:s": -4e-3}'
emir run   block.dspf --process pdks/sky130.yaml --net VDD --currents '{"VDD": 4e-3, "M0:s": -4e-3}' --html out/
```

The analysis temperature defaults to the file's `em_tref`; `--em-temp` changes
it, which rescales the wire resistance by the file's TCR (layers only; via
resistance is not scaled). Results are **EM/IR screening, not sign-off**: the
foundry-qualified flow remains the sign-off.

## Sources and licence

Each of the three PDK files says `provenance: public-pdk-data`, and every report
says so per layer. A file typed by hand from a design manual (when no readable
data exists: [Entering EM data by hand](../docs/pdk-onboarding.md#entering-em-data-by-hand))
says `provenance: hand-entered` and names a source per value under `sources:`.

Every value in the three PDK files names its source on its own line, as
`[FILE]:line` (or a section and parameter name for a PDF, or a URL). Each file's
header lists the source files, relative to the PDK root variable it names
(`$PDK_ROOT`, `$GF180_PDK_ROOT`, `$IHP_PDK_ROOT`), and the PDK release they were
read from.

| file | sources (all Apache-2.0) | release read |
|---|---|---|
| `sky130.yaml` | standard-cell tech LEF (`sky130_fd_sc_hdll__nom.tlef`: RPERSQ, via RESISTANCE, DC average and RMS current density, widths); KLayout layer properties (GDS numbers); the device models' metal resistor TCRs; magic's tech file and the KLayout LVS deck (extractor layer names) | open_pdks 1.0.510 |
| `gf180mcu.yaml` | standard-cell tech LEF (`gf180mcu_fd_sc_mcu9t5v0__nom.tlef`: current densities, via resistance, widths); the typical-corner metal resistor models (sheet resistance, TCR); KLayout layer properties; the public design manual, section 14.2 Electro-migration (the temperature the limits hold at) | open_pdks 1689ac3f; [gf180mcu-pdk docs](https://gf180mcu-pdk.readthedocs.io/en/latest/physical_verification/design_manual/drm_14_2.html) |
| `ihp_sg13g2.yaml` | process specification (`SG13G2_os_process_spec.pdf`, Rev. 1.2: sheet and via resistance, TCR, maximum current densities and their temperature); tech LEF (`sg13g2_tech.lef`: current densities, widths); layout rules (`SG13G2_os_layout_rules.pdf`: the Via1 cut size); KLayout layer properties; magic's tech file | IHP-Open-PDK 22f43352 |

The numbers are the PDK authors' data, published under the Apache License 2.0
(copyright the SkyWater PDK Authors, the GlobalFoundries PDK Authors and the
IHP PDK Authors respectively; the layer names quoted from the KLayout and magic
decks are Efabless's and the PDK projects'). Upstream:
[google/skywater-pdk](https://github.com/google/skywater-pdk),
[RTimothyEdwards/open_pdks](https://github.com/RTimothyEdwards/open_pdks),
[google/gf180mcu-pdk](https://github.com/google/gf180mcu-pdk),
[IHP-GmbH/IHP-Open-PDK](https://github.com/IHP-GmbH/IHP-Open-PDK).

With the PDKs installed, the test suite checks that every cited line still
holds its value (PDF and URL sources are checked by hand):

```bash
PDK_ROOT=... GF180_PDK_ROOT=... IHP_PDK_ROOT=... pytest tests/test_pdks.py -k citations
```

A hand check of the units: sky130 `met1` allows 2.8 mA/um on average
(`DCCURRENTDENSITY AVERAGE 2.8`), so a minimum-width 0.14 um wire may carry
2.8 x 0.14 = 0.392 mA, and one `via` cut 0.29 mA. `tests/test_pdks.py` runs a
strap of exactly that wire through `emir run` and gets the ratios by hand.

## What is not checked

A limit the PDK does not publish is left out, with the comment
`not published by the PDK - not checked`; emir then reports that metric as
not checked rather than guessing. In these files:

| | sky130 | gf180mcu | ihp_sg13g2 |
|---|---|---|---|
| avg (DC) limit on wires | met1-met5 (li1: none published) | all | all |
| avg limit per via cut | all | all | all |
| rms limit on wires | met1-met5, fixed values (`jmax_rms`) | none published | none published |
| rms limit on vias | none published | none published | none published |
| peak limits | none published | none published | none published |
| derating of avg limits with temperature (`jmax_factor`) | none published | not encoded (the manual gives 85 and 110 C columns; the 125 C limits are used, conservative below 125 C) | none published |
| contacts to poly and diffusion, and the layers under them | not in the file | not in the file | not in the file |

Two more simplifications, each conservative where it matters:

- **gf180mcu:** emir's avg metric is checked against the *unidirectional*
  limit. The LEF's `ACCURRENTDENSITY AVERAGE` values are the manual's
  *bi-directional* limits, which allow more on signal nets; they are not used.
- **ihp_sg13g2:** for narrow wires the specification gives an absolute current
  (Metal1 0.36 mA for widths 0.16-0.36 um); the per-um limit used here allows
  less on those wires.

## Choices and disagreements found while building the files

- **sky130 `via` resistance:** 4.50 ohm per cut (standard process, every
  `*__nom.tlef`, and the device models). The ReRAM-option tech LEFs
  (`*__rram__nom.tlef`) give 9.00 ohm; use them only for that process option.
- **sky130 `via4` resistance:** 0.38 ohm (tech LEF). The continuous models'
  nominal parameter file says 3.41, the same as via2/via3, while their Monte
  Carlo parameters give 0.38; the LEF value is used, as for every other via.
- **sky130 temperature reference:** the tech LEF does not say at what
  temperature its sheet resistance holds; the metal resistor models are
  referenced to 30 C and their nominal values agree with the LEF's to about
  4 % (the largest gap is li1: the LEF's 12.8 ohm/sq is 4.1 % above the
  models' 12.3), so `temp_reference` is 30 C, with the models' TCRs.
- **gf180mcu resistance:** the tech LEF states that its parasitic data is not
  correct, so sheet resistance comes from the typical resistor models. They
  agree with the LEF on Metal1-4 (0.09 ohm/sq) and differ on Metal5: 0.040
  ohm/sq for the 1.1 um top metal of `gf180mcuD` (magic's extraction agrees),
  where the LEF has 0.060, the 0.6 um top metal's value.
- **ihp_sg13g2 resistance:** typical values (the specification's "Target"
  column), like the nominal values of the other two files. The tech LEF carries
  the "Max" column (Metal1 0.135 against 0.110 ohm/sq, a via 20 against 9 ohm);
  each entry quotes it, for a worst-case IR screen.

## Making a file for your PDK

From the template:

1. Copy [`TEMPLATE.yaml`](TEMPLATE.yaml) to a file named after your PDK and
   change `pdk:` (a name containing "illustrative" makes `emir check` warn that
   the limits are not real).
2. Fill in each layer and via from your PDK: the GDS layer/datatype, sheet
   resistance (ohm/square), resistance per via cut (ohm), the cut side
   (`vcwidth`, um), and the EM limits in **mA per um of drawn width** for wires
   and **mA per cut** for vias, at the temperature you put in `em_tref`.
   Cite the source of each value in a comment. Leave out what your PDK does not
   publish; never estimate a limit.
3. Map extractor layer names that do not match yours with `dspf_layers`.
4. Validate: `emir check <dspf> --process <your file>` exits 0 and lists any
   layer whose EM is not checked.

From a foundry ICT: keep the geometry and resistance keys (`gds`,
`sheet_res`, `res`, `min_width`, `below`/`above`) in the file, then

```bash
emir import-ict --ict <pdk>.ict --emir <your file>.yaml
```

fills `jmax_avg`, `jmax_peak`, `jmax_rms_k`, `tc1`/`tc2`, `jmax_factor`,
`em_tref` and the via limits from the ICT's EM model, and says what it could
not find. A file made from a licensed PDK's data is that foundry's
confidential data: keep it with your PDK installation, not in this repository
(the leak guard refuses ICT, tech and layer-map files).

### The two forms of an rms limit

A tech LEF publishes a fixed rms current density (`ACCURRENTDENSITY RMS`):
write it as `jmax_rms` (mA/um), which `--delta-t` does not change. An ICT gives
a self-heating constant instead: `jmax_rms_k`, with the limit
sqrt(`jmax_rms_k` x delta-T) mA/um and delta-T from `--delta-t` (default 5 C).
A layer carries one form or the other; `emir check` fails a layer that has
both, and `emir import-ict` replaces a `jmax_rms` with the ICT's form.
