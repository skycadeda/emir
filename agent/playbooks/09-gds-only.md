# 09 A layout and no DSPF: the magic route

The person has the GDS of a block on an open PDK (SKY130 here) and no extracted netlist. magic, the open-source
extractor, extracts the network: every resistor one drawn segment, with its layer, width, length and place.
`emir extract --tool magic` runs it and writes the DSPF that `emir run` reads. That DSPF is also the RC netlist
an rcc simulation runs. It also writes the CC netlist that a cc-grid simulation runs. From there the flow is
playbook 05's.

It needs a magic built with the `geoout` patch (`extresist geoout`), which makes magic write each resistor's
geometry. The patch is public: branch `extresist-geoout` of https://github.com/codervijay/magic, offered
upstream as https://github.com/RTimothyEdwards/magic/pull/562. With a stock magic, `emir extract` exits 3
(EMIR-E643) and says how to build one.

**Screening, not sign-off.**

## When to use

- The person has a GDS, no DSPF, and the PDK is one magic extracts (sky130: `pdks/sky130.yaml` has the
  `magic:` section).
- `emir next --gds <layout.gds> --process <pdk.yaml> --cell <cell>` plans it: the extraction, then the plan on
  the extracted DSPF. With a DSPF as well, the DSPF plan stands (playbook 01), and `--gds` draws the layout under
  the charts.
- At a Cadence site, extract with Quantus instead (playbook 06).

## Inputs

| input | what it must be | ask / check |
|---|---|---|
| the layout | GDS; the top cell (`--cell`; without it, the layout's only top cell, read with the klayout package) | EMIR-E270 lists the top cells |
| the process file | the PDK's limits, and a `magic:` section: magic's layer names mapped onto the file's, each device model's terminal names, the magic to try | `pdks/sky130.yaml`; `pdks/TEMPLATE.yaml` documents the section |
| magic's technology | `$PDK_ROOT/<pdk>/libs.tech/magic/<pdk>.magicrc` for the process file's `pdk:` name, or `--magicrc` | EMIR-E641 when there is none |
| a magic that writes geometry | `--magic`, `$EMIR_MAGIC`, the process file's `magic: binary:` (a magic's bare name on `$PATH` only: EMIR-E648), the site file's `magic:`, then `magic-8.3.515-geo` and `magic` on `$PATH` | `emir extract` probes each; EMIR-E643 lists what it tried |
| the testbench | Xyce or ngspice, instantiating the block's subckt (its ports in magic's order: the DSPF's `.SUBCKT` line), with the DSPF (rcc) or the CC netlist (cc-grid) included | ask the person |

## Commands

### Worked example (runs as printed from the repository root)

The magic route's test cell: one NMOS, its `vss` rail labelled at both ends. The plan extracts it, then plans
the run on its DSPF.

<!-- test: run -->
```sh
emir next --gds tests/data/magic_tiny/tiny.gds --process pdks/sky130.yaml --cell tiny --format json
```

<!-- test: expect -->
```json
{"schema": "emir.next/1",
 "steps": [{"id": "selftest"}, {"id": "extract-magic"}, {"id": "next-dspf", "after": ["extract-magic"]}]}
```

Then, with the magic it needs, the extraction:

```sh
emir extract --tool magic --gds tests/data/magic_tiny/tiny.gds --cell tiny --process pdks/sky130.yaml --out emir_out/extract_tiny --format json
```

Its document names `dspf` (tiny.dspf), `cc_netlist` (tiny.cc.spice) and `unchecked_layers`: magic's conductors
the process file gives no limit (diffusion, poly, the wells). Their current is in the IR drop, and the run
reports them as not checked. Example 08 runs the whole route on a SKY130 NMOS array, rcc against cc-grid,
branch by branch.

### On the person's files

```sh
emir next --gds <layout.gds> --process <pdk.yaml> --cell <cell> --format json
emir extract --tool magic --gds <layout.gds> --cell <cell> --process <pdk.yaml> --out emir_out/extract_<cell> --format json
emir next --dspf emir_out/extract_<cell>/<cell>.dspf --process <pdk.yaml> --netlist <tb.cir> --gds <layout.gds> --cell <cell> --format json
```

Then follow playbook 05 with the mode the person chooses:

- **rcc**: the testbench includes the DSPF itself (`emir simulate <tb> --sim xyce --dspf <dspf> --mode rcc`).
- **cc-grid**: the testbench includes `<cell>.cc.spice` (`emir simulate <tb> --sim xyce --dspf <dspf> --mode
  cc-grid --probe`). The probes name each device terminal as the DSPF's `*|I` records do (the models' own
  subcircuit ports, `d g s b` for sky130's MOSFETs).

For a DSPF this route wrote, `emir next`'s choose-mode step adds what each route measured on example 08's
magic network to its `accuracy`: show it to the person as it is. On a signal net (a gate, a clock) cc-grid
is not a bound, open loop or closed: example 08's gate net read low in the loop.

With `--html`, add `--gds <layout.gds> --cell <cell>` to `emir run`, and the chart draws the layout under the
results.

## Decision points

| you see | do |
|---|---|
| EMIR-E643, exit 3 | no magic writes geometry here: tell the person, give them the hint's build steps (the fork's branch, upstream PR #562), and ask before building or installing anything |
| EMIR-E270 several top cells | `--cell <name>`; its `next_steps` list each |
| EMIR-W645 nets not split into resistors | magic left those nets whole (`--tolerance`): there is nothing to check on them; say so, or extract again with a lower `--tolerance` after the person agrees |
| EMIR-W643 device terminals named by position | the process file names no terminals for those models: their pins are `t1 t2 ...`; a cc-grid simulation must then name them the same way (`--pins MODEL=t1,t2,...`); tell the person |
| `unchecked_layers` in the extraction | the layers with no limit (diffusion, poly, wells, li1 on sky130): report them as not checked |
| a port with several pins (`extra_pins`) | each pin is its own node, tied to the port by a 0 V source (`--port-pins tied`, the default): every pin is fed |
| EMIR-W640 magic older than the version checked | an old magic can crash on the technology file (then EMIR-E640): tell the person |

## Success criteria

- `emir extract --tool magic` exited 0, and its document names the DSPF and the CC netlist.
- `emir check <dspf> --process <pdk.yaml>` passes before the simulation is spent.
- `emir report --from <the run's --summary-out>` ran before you answered, and it is what you relayed.

## What to report

The verdict, the worst items with their fixes ("via2: 1 -> 2 cuts (+1)"), the IR drop per net, the extractor
(magic's own network: every contact and the diffusion included), where the currents came from and the mode,
whether the simulator is verified here, what was not checked (the layers with no limit), and "Screening, not
sign-off."

## Failure modes

| code | exit | meaning | do |
|---|---|---|---|
| EMIR-E643 | 3 | no magic writes resistor geometry | the hint's build steps; the person decides |
| EMIR-E642 | 3 | the magic chosen cannot be started | the path and its permissions (`emir info`) |
| EMIR-E641 | 2 | no magic technology | `PDK_ROOT`, or `--magicrc` |
| EMIR-E640 | 2 | magic wrote no netlist (or crashed) | the magic log in `magic/` under the `--out` directory; `--cell`; an old magic (EMIR-W640) |
| EMIR-E646 | 2 | `--out` holds a `<cell>.dspf` or `<cell>.cc.spice` EMIR did not write, or a `magic/` that is not EMIR's | never move or delete the person's files: ask, or give another `--out` |
| EMIR-E647 | 2 | magic outputs of a hierarchy extracted cell by cell | extract again with `emir extract --tool magic` (it flattens first) |
| EMIR-E648 | 2 | the process file's `magic: binary:` is a path or not a magic | tell the person; name the magic with `--magic` or `$EMIR_MAGIC` |
| EMIR-W646 | 0 | capacitor nodes on no net of the network | report them; unconnected metal is expected, a circuit net is not |
| EMIR-W647 | 0 | wires on diagonal shapes: width not across the current | report them as not checked (the run lists `diagonal_wires`) |
| EMIR-E644 | 2 | magic wrote no `.res.geo`, or output EMIR cannot read | a cell with no devices has nothing to split; the log |
| EMIR-E645 | 2 | magic ran past `--timeout` | ask before a longer `--timeout` |
| EMIR-E243 | 2 | the cell's name is unsafe for magic's script | the person renames the cell |
| EMIR-E106, EMIR-E170 | 2 | no such cell; the layout cannot be read | the message lists the top cells; ask for a complete GDS |
| EMIR-E603 | 3 | the klayout package is missing (to find the top cell) | give `--cell`, or install the klayout extra with the person's go-ahead |
| EMIR-E507 | 2 | an option of the other tool (`--kind`, `--cdl` with `--tool magic`), or `--process` missing | the options of `emir extract --tool magic` |
