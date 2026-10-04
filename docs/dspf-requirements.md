# DSPF requirements

What the extracted DSPF must contain for EMIR to check it, which extractor options give
it, and the failure modes the tests cover. Run `emir check <dspf> --process <file>`
on every new extraction: it names each gap with a code before anything is computed.

**Screening, not sign-off.**

## Why DSPF

EM needs the **width** of the wire each resistor stands for (`J = I / W`) and the cut
count of each via. SPEF carries no geometry, so it cannot be checked for EM; a DSPF
written with geometry annotation can. EMIR reads the resistor network of every net,
where the device pins land on it, and where the net is fed.

## What EMIR reads

```text
*|DSPF 1.3
*|DESIGN "railblock"
*|OPERATING_TEMPERATURE 100
*|NET VDD 1.200000e-14
*|P (VDD I *1 0.0000 0.0000)
*|S (VDD:1 5.0000 0.0000)
*|I (MP0:s MP0 s I 0 15.0000 0.0000)
Rm1 VDD VDD:1 1.000000 $metal1 $L=5.0000 $W=0.750000 $LLX=0.0000 $LLY=-0.3750 $URX=5.0000 $URY=0.3750 $lvl=1
Rc0 VDD:3 MP0:s 12.000000 $cont $A=0.033800 $VC=2 $LLX=14.6250 $LLY=-0.3750 $URX=15.3750 $URY=0.3750 $lvl=0
Cm1 VDD:1 0 1.000000e-15
```

(The bundled sample, `emir/data/sample/rail.dspf`.)

| record | what EMIR takes from it | needed for |
|---|---|---|
| \*\|DESIGN "cell" | the design name | the viewers open this cell (`--cell` otherwise; `EMIR-W201`) |
| \*\|OPERATING_TEMPERATURE T (or \*\|GLOBAL_TEMPERATURE) | the extraction temperature | resistance scaling: equal to the analysis temperature, R is used as it is; otherwise it is scaled by the TCRs ([Concepts](concepts.md#temperature-and-derating)) |
| \*\|NET name cap | the current net | assigning the following records to it |
| \*\|P (node dir cap x y) | a port: where the net is fed, with coordinates | the feed and the IR reference; the hierarchical flow's block pins |
| \*\|I (node inst pin dir cap x y) | an instance pin and where it lands | attaching simulated device currents (cc-grid); **without x y the record is dropped** and no current can attach |
| \*\|S (node x y) | a sub-node's coordinates | placing results on the layout (viewers, HTML maps); a node with no \*\|S record is put on its net by splitting its name at `:` |
| `R... n1 n2 value ...` | a resistor, with its annotations (below) | the network |
| `C... n1 n2 value` | a parasitic capacitor | dynamic IR |
| `C... n1 n2 model area=... pj=...` | a capacitor **device** (a value-less line with geometry) | dynamic IR, sized by `cap_models` |
| `M... d g s b model W=... L=...` | a transistor; one with its gate on one net and drain, source and bulk on another is a **MOS decap** | dynamic IR, sized by `cap_models` |

### The resistor annotations

| annotation | meaning | what happens without it |
|---|---|---|
| the layer: a bare `$metal1`, or `$layer=M1` / `$lyr=M1` (an explicit key wins), or only `$lvl=N` | which process-file layer or via limits apply | a resistor with no layer cannot be checked (`EMIR-W206`); a level-only resistor maps through `dspf_layers: {N: name}` |
| `$W=` (or `$SI_W=`), um | the drawn width: a **wire** | a wire with no width cannot be checked: input-check FAIL (`EMIR-E210`), or `EMIR-E203` when no resistor has one |
| `$L=`, um | the length | reported; not needed for the check |
| `$N=`, `$VC=` or `$SAVC=` | the cut count: a **via** | the count comes from the area, below |
| `$A=` (or `$SI_A=`), um^2 | the total cut area: a **via** | with `vcwidth` in the process file, cuts = area / vcwidth^2; without it the via is checked as **one cut** (conservative, `EMIR-W308`) |
| `$LLX $LLY $URX $URY`, or `$X $Y` | the segment's box or centre | the viewers and HTML maps place it from its nodes instead |

Only via annotation (a count or an area) marks a resistor as a via. A resistor with
neither a width nor via annotation is classified by its layer in the process file:
on a conductor layer it is a wire with no width (FAIL), on a via layer a via with no
count, checked as one cut (conservative, `EMIR-W207`).

## Extraction settings, by extractor

### Quantus

`emir extract` writes the command file; its settings that must not be dropped are
listed in [Cadence flow](cadence.md#1-extraction-emir-extract): `-analysis "em"` (no
series reduction: one resistor per segment), `-temperature` at the EM analysis
temperature, `-sub_node_char ":"`, `-disable_instances false` (keeps the `*|I`
records), `-net_name_space "SCHEMATIC"`, `-output_xy`,
`-include_parasitic_res_width_drawn true`, and the three `-include_*_model "false"`.
Quantus 21.1 writes the cut count as `$N`; other vintages `$VC` or `$SAVC`, and some
only the area `$A`, which is why `vcwidth` matters.

### Calibre xRC / xACT and StarRC (UNVERIFIED)

No Calibre or StarRC tool was available where EMIR was developed.
[`agent/cadence/calibre/NOTES.md`](../agent/cadence/calibre/NOTES.md) and
[`agent/cadence/starrc/NOTES.md`](../agent/cadence/starrc/NOTES.md) map each need
above onto the option believed to give it, from public vendor material, with every
item marked UNVERIFIED. The needs, whatever the extractor:

- **R extraction with no series reduction**: one resistor per drawn segment, so a neck
  is not averaged away into its neighbours;
- **width (and length) on every wire resistor**, the layer on every resistor;
- **a cut count (or area) on every via**;
- **node coordinates** (`*|S`) and **instance pins with coordinates** (`*|I`);
- **the extraction temperature** stated (`*|OPERATING_TEMPERATURE`), ideally equal to
  the EM analysis temperature;
- for cc-grid, a **C-only (coupled) netlist from the same LVS run** as the R-only DSPF,
  so instance names match.

### Open source

magic computes every resistor from one drawn shape. A magic built with the public
`geoout` patch writes each one's layer, width, length and place (`extresist geoout on`),
and `emir extract --tool magic` writes that as a DSPF with all of the above. Each
resistor carries `$layer $W $L` and its rectangle, or a via's `$cuts`. Every node is
placed, and every device terminal is a `*|I` record. The run also writes a CC netlist
with the same instance names, so cc-grid's currents map onto the DSPF
([Open-source flow](open-source-flow.md#route-2-a-gds-layout-on-an-open-pdk-extracted-by-magic)).
A stock magic writes only the resistors' values; KLayout writes no DSPF.

## Which extraction for which mode

| mode | the DSPF EMIR screens | the netlist you simulate |
|---|---|---|
| `rcc` (small blocks) | the RC extraction (R and coupling C) | the same RC extraction, with every resistor current saved |
| `cc-grid` (big blocks) | the R-only extraction (or RC, for `--dynamic`) | the C-only (coupled) extraction from the same LVS |
| `dc` | R-only or RC | none |

An R-only netlist is **not** an RCC reference: it has no capacitance.

## The simulation result

- **Text formats only**: Spectre psfascii (`-format psfascii`) or nutascii; HSPICE
  ASCII (`.option post=2 post_version=2001`); CSDF; Xyce and ngspice ASCII raw files
  (`Xyce -a`, ngspice `.options filetype=ascii`). Binary PSF, binary HSPICE and binary
  SPICE raw files are detected and refused with what to re-run (or, for binary PSF, the
  conversion command); they are never guessed at ([Simulators](simulators.md)).
- **Names**: Spectre's `R:1` (branch current, first node to second) and `inst:pin`
  (into the instance). Xyce and ngspice results need the names sidecar `emir simulate`
  or `emir save-lines --names-out` writes ([Open-source flow](open-source-flow.md#names-and-signs)).
- **Whole periods** of a settled operating point, `--period` given.

## Failure modes the tests cover

Each of these was a real way for a DSPF to be read wrong, silently, and each now has a
test:

| failure | now | test |
|---|---|---|
| the layer regex backtracked into annotation keys: `$LLX=1 $metal1` read as layer `LL`, `$layer=M1` as `laye`, leaving segments unmapped | tokens are classified whole; `$layer=` and `$lyr=` (quoted or not, with spaces around `=`) are read | `tests/test_layers_and_vias.py::test_layer_token`, `tests/test_dspf_robustness.py::test_quoted_layer_values` |
| resistors carrying only a `$lvl` level number were unmappable | mapped through `dspf_layers: {N: name}` | `test_level_only_resistors_map_through_dspf_layers` |
| every width-less resistor was taken for a via, so metal segments whose `$W` was missing dropped out of the EM check | only via annotation makes a via; a wire with no width is a FAIL | `tests/test_dspf_robustness.py::test_only_via_annotation_makes_a_via`, `test_wires_without_width_fail_the_check` |
| cut counts from `$A` used a hidden built-in cut size | `vcwidth` from the process file, no default; without it, one cut and a warning | `test_cut_count_from_area_uses_the_process_vcwidth`, `test_area_without_vcwidth_is_one_cut_and_warned` |
| vias with no count and no area were skipped while the checker said "one cut" | checked as one cut (conservative) and reported | `test_vias_without_count_or_area_are_checked_as_one_cut` |
| two \*\|I records of one instance pin overwrote each other | grouped; refused when one saved current would have to be split (`EMIR-E417`) | `tests/test_cc_modes.py::test_split_instance_pin_is_a_check_fail` |
| a file that is not a DSPF at all (no DSPF record, no element line) | refused (`EMIR-E217`) | `tests/test_cli_robustness.py` |
| a net in pieces solved a singular matrix (IR of about 9e15 mV) | refused (`EMIR-E207`); forced past it, each piece solved on its own, unsolvable currents reported as not a number (FAIL) | `tests/test_cli_robustness.py::test_a_forced_run_on_a_net_in_pieces_gives_no_invented_numbers` |
| binary results, truncated nutascii | refused with the fix | `test_binary_spice_raw_is_refused_with_the_fix`, `test_truncated_nutascii_is_explained` |

`emir check --format json` lists every finding with its code and the command that fixes
or diagnoses it; an agent branches on the code.
