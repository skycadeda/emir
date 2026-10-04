# 03 Chip-level EM/IR, hierarchically: block abstracts and the top

A chip, or a top made of blocks, is screened in two levels: each block is characterised once (`emir abstract`:
its pin currents, its own EM verdict and IR drop, the conductance between its pins), then the top is run with the
blocks black-boxed (`emir run --abstract FILE@INST`). The top's pin voltages come back to the blocks fed at several
pins (back-annotation). **Screening, not sign-off.**

## When to use

- The person asks for EM/IR of a chip, an ADC top or a PLL top built from blocks, or brings block abstracts.
- A flat run refused with EMIR-E417 (an instance pin reaching the net at several connections) and that instance
  is a black-boxed block.
- `emir next` advises the hierarchical route (a top with black-boxed blocks and no abstracts).
- Not for a single block: playbook 01 (small) or 02 (big).

## Inputs

| input | per | ask / check |
|---|---|---|
| R-only DSPF with every supply pin shape a `*\|P` port with coordinates | block | `emir nets <block.dspf> --format json`: `nets[].port` true |
| CC simulation of the block, instance-pin currents saved, ASCII, whole periods | block | `emir save-lines <block.dspf> --mode cc-grid` before simulating (an abstract is cc-grid: `emir abstract --mode cc-grid`) |
| the block's period and nominal supply | block | ask the person |
| R-only top DSPF, blocks black-boxed, their pins as `*\|I` records with coordinates | top | `emir pins <top.dspf> --net <VDD> --format json` |
| the placement of a block whose pin reaches the top at several places: origin (um) and orientation | top | ask the person, or read it off the layout with them |
| whether the blocks share a clock, and whether their simulations start on the same edge | chip | ask: it decides `--align` |
| the IR budget per block (mV) | chip | ask the person |
| the process file | chip | playbook 04 |

## Commands

### Worked example (runs as printed from the repository root)

Example 05's synthetic chip: an ADC block (one supply pin), a PLL block (two pins, so the top's VDD runs through
it), a top, an invented process file and closed-form block waveforms. No simulator needed.

<!-- test: run -->
```sh
python examples/05_chip_level/make_inputs.py emir_out/chip
emir abstract emir_out/chip/adc.dspf --process emir_out/chip/process.yaml --cc-sim emir_out/chip/adc.raw --mode cc-grid --period 10e-9 --vdd 1.0 -o emir_out/chip/adc.abstract.json --format json
```

<!-- test: expect -->
```json
{"schema": "emir.abstract_summary/1", "verdict": "CLEAN",
 "nets": [{"net": "VDDA", "ports": [{"name": "VDDA", "i_avg": -0.002}],
           "internal": {"verdict": "CLEAN", "ir_drop_mV": 7.5}}]}
```

<!-- test: run -->
```sh
emir abstract emir_out/chip/pll.dspf --process emir_out/chip/process.yaml --cc-sim emir_out/chip/pll.raw --mode cc-grid --period 2.5e-9 --vdd 1.0 -o emir_out/chip/pll.abstract.json --format json
```

<!-- test: expect -->
```json
{"schema": "emir.abstract_summary/1", "verdict": "WARN",
 "nets": [{"net": "VDDP", "ports": [{"name": "VDDP", "i_avg": -0.001333}, {"name": "VDDP:E", "i_avg": -0.0006667}],
           "internal": {"verdict": "WARN", "max_ratio": {"all": 0.9524}, "ir_drop_mV": 11.33}}],
 "notices": [{"code": "EMIR-W804"}]}
```

EMIR-W804: the PLL is fed at two pins, so its verdict here (pins held at one potential) must be re-checked with the
top's pin voltages. Now the top: its block pins, the plan, the check, the run with the IR budget, the report.

<!-- test: run -->
```sh
emir pins emir_out/chip/top.dspf --net VDD --format json
emir next --dspf emir_out/chip/top.dspf --process emir_out/chip/process.yaml --abstract emir_out/chip/adc.abstract.json@XADC --abstract emir_out/chip/pll.abstract.json@XPLL --place XPLL=40,0,R0 --align sync --format json
```

<!-- test: expect -->
```json
{"schema": "emir.next/1", "mode": {"chosen": "cc-grid"}, "missing": []}
```

<!-- test: run -->
```sh
emir check emir_out/chip/top.dspf --process emir_out/chip/process.yaml --abstract emir_out/chip/adc.abstract.json@XADC --abstract emir_out/chip/pll.abstract.json@XPLL --place XPLL=40,0,R0 --align sync --format json
emir run emir_out/chip/top.dspf --process emir_out/chip/process.yaml --abstract emir_out/chip/adc.abstract.json@XADC --abstract emir_out/chip/pll.abstract.json@XPLL --place XPLL=40,0,R0 --align sync --budget-mv 15 --port-voltages-out emir_out/chip/port_voltages.json --summary-out emir_out/chip/summary.json --format json
```

<!-- test: expect -->
```json
{"schema": "emir.run/1", "verdict": "WARN", "current_source": {"kind": "abstracts"},
 "worst_net": {"net": "VDD", "ratio": 0.9091},
 "nets": [{"mode": "cc-grid", "ir_drop_mV": 4.0, "worst_items": [{"name": "Rt0"}]}],
 "hierarchy": {"align": "sync",
               "budget": {"verdict": "PASS",
                          "rows": [{"instance": "XADC", "total_mV": 11.5, "exact": true},
                                   {"instance": "XPLL", "total_mV": 14.96, "exact": false}]},
               "blocks": [{"instance": "XADC", "needs_back_annotation": false},
                          {"instance": "XPLL", "needs_back_annotation": true}]}}
```

The run's `next_steps` hold the exact back-annotation command for XPLL (with absolute paths). The same command,
with this example's relative paths:

<!-- test: run -->
```sh
emir report --from emir_out/chip/summary.json
emir abstract emir_out/chip/pll.dspf --process emir_out/chip/process.yaml --cc-sim emir_out/chip/pll.raw --mode cc-grid --period 2.5e-9 --vdd 1.0 --port-voltages emir_out/chip/port_voltages.json@XPLL -o emir_out/chip/pll.annotated.json --format json
```

<!-- test: expect -->
```json
{"schema": "emir.abstract_summary/1", "verdict": "FAIL",
 "nets": [{"internal": {"verdict": "WARN"},
           "back_annotation": {"verdict": "FAIL", "exact": true, "max_ratio": {"all": 1.005},
                               "worst": {"name": "Rm1"}, "ir_drop_mV": 14.81}}]}
```

The PLL's Rm1 read 0.952x (WARN) with its pins shorted and 1.005x (FAIL) with the top's real pin voltages: the
current passing through the block from one pin to the other is what the block-alone verdict leaves out.

### On the person's files

```sh
emir abstract <block.dspf> --process <pdk.yaml> --cc-sim <block.raw> --mode cc-grid --period <T> --vdd <V> -o <block.abstract.json> --format json
emir pins <top.dspf> --net <VDD> --format json
emir next --dspf <top.dspf> --process <pdk.yaml> --abstract <block.abstract.json>@<INST> --place <INST>=<dx>,<dy>,<orient> --align sync --format json
emir check <top.dspf> --process <pdk.yaml> --abstract <block.abstract.json>@<INST> --place <INST>=<dx>,<dy>,<orient> --align sync --format json
emir run <top.dspf> --process <pdk.yaml> --abstract <block.abstract.json>@<INST> --place <INST>=<dx>,<dy>,<orient> --align sync --budget-mv <mV> --port-voltages-out emir_out/port_voltages.json --summary-out emir_out/summary.json --format json
emir report --from emir_out/summary.json
```

Then each `needs_back_annotation` block's command from the run's `next_steps`. A mid-level block made of
sub-blocks: abstract the sub-blocks first, then the mid-level block with `--abstract SUB.abstract.json@XSUB`.

`emir next` on the top with an abstract that is not written yet plans that block's `emir abstract` step (`waiting`
on the block's DSPF and result), then `emir next` again; nothing past the abstracts is planned until every one can
be read. `emir abstract ... --top <top.dspf>@<INST>` makes the abstract's own `next_steps` lead back to that plan.

## Decision points

- **Alignment (`--align`)**, after asking whether the blocks share a clock:
  - not known, or a quick bound: `inphase` (the default; rms and peak are upper bounds over every alignment);
  - one clock reference and simulations starting on the same edge: `sync` (`--phase INST=S` for a known offset);
  - related clocks, phase unknown: `sync --phase-sweep INST:N` (the worst of N phases);
  - truly unrelated clocks only: `async`, and report that it is not an upper bound for related clocks (EMIR-W802).
- **EMIR-E806** (a block pin at several records, no placement): ask the person for the placement; never find it
  by trying orientations until one fits. **EMIR-E808**: the placement does not land the ports on the records:
  compare `--place` with `emir pins` coordinates, with the person.
- **`needs_back_annotation` true** (EMIR-W804): run the back-annotation; exact from a `sync` run without a sweep,
  otherwise rms and peak are bounds.
- **EMIR-W805** (a block pin droops more than 2 % of `--vdd`): tell the person; with their go-ahead re-simulate
  the block at `v_delivered_V` from the port-voltages file, re-abstract, re-run, until the droop settles.
- **EMIR-W807** (top-level devices outside the blocks): their current is zero unless the top's own CC result is
  given with `--cc-sim` (and `--period`).
- **EMIR-E810** (the periods share no grid for `sync`): `--align inphase`, the command in its `next_steps`.
- **EMIR-E811**: an option that does not combine with `--abstract` (such as `--dynamic`): drop it. `emir next`
  says so in `missing[]` for `--dynamic` and an `--em-basis` that needs it, and plans nothing they would refuse.
  After the run, `emir next --run-summary` reads its abstracts, placements and alignment back from the summary.
- **The top run's step has `needs_human` (`long_run`)**: the top is above the big-block size; ask the person before
  running it, as for the flat run of the same grid.

## Success criteria

- Every `emir abstract` exited 0 and its `verdict` is known per block.
- `emir check` exited 0; `emir run` exited 0 and its summary holds `hierarchy` (with `budget.verdict` when a budget
  was given).
- Every block with `needs_back_annotation` true was back-annotated, and its `back_annotation.verdict` is reported.

## What to report

Build it from `emir report --from emir_out/summary.json` (the top run) and, for each back-annotated block,
`emir report --from <block.annotated.json>` (it reads an `emir.abstract/1` document too).

The top-level verdict and worst ratio with its fix; each block's own verdict and how its boundary was treated (pins
shorted, or back-annotated); the budget rows with total mV and whether each is exact or a bound; the alignment
mode and what it means; what was not checked; "Screening, not sign-off."

**`back_annotation.ir_drop_mV` is measured from the pad** (`ir_reference: pad`): the top-level drop to the block's
pins is already in it. Compare it with the budget as it is (with the top run's `--budget-mv` the document carries
`budget_mV` and `budget_verdict`); never add the top drop again. In the worked example the PLL's back-annotated
14.81 mV is under its 15 mV budget. For the worked example: top WARN, VDD
0.909x on Rt0, IR budget PASS (XADC 11.5 mV exact, XPLL 14.96 mV a bound, both under 15 mV), and the PLL FAILS once
back-annotated (Rm1 1.005x, from the pass-through current), so the PLL needs a fix although every top-level item
passes.

## Failure modes

| code | exit | meaning | do |
|---|---|---|---|
| EMIR-E801 | 2 | the abstract file is unusable | regenerate it with `emir abstract`; never edit one by hand |
| EMIR-E802 | 2 | the instance is not in the top DSPF | use the instance names of the top's `*\|I` records (`emir pins`) |
| EMIR-E803, EMIR-E804 | 2 | a block net has no port; nothing to abstract | ask for a re-extraction with every pin shape a `*\|P` port, or give `--net` |
| EMIR-E805 | 2 | the block result cannot be abstracted (harmonic PSS) | ask for the time-domain output |
| EMIR-E806, EMIR-E808 | 2 | the placement is missing or wrong | ask the person for it |
| EMIR-E807 | 2 | port and record counts differ | the hint names the extraction to redo |
| EMIR-E809 | 2 | no abstract on the net | check the instance names and `--net` |
| EMIR-E812 | 2 | the port-voltages file is unusable for this block | re-run the top with this abstract and `--port-voltages-out`, then back-annotate |
| EMIR-E813 | 2 | where the net is fed is unknown | ask; `--feed NODE` |
| EMIR-W806 | 0 | a block pin with no current | abstract every supply net of the block; check its pin names |
| EMIR-W801, EMIR-W803, EMIR-W808 | 0 | an aperiodic abstract repeated; peak lost in resampling; an abstract at another temperature | re-abstract with `--period`, a larger `--points`, the run's `--process` and `--em-temp` |
