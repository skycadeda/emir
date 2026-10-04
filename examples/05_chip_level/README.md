# 05 Chip-level EM/IR, hierarchically

A synthetic chip with two blocks, screened the way a chip-level run is done: each
block characterised once (`emir abstract`), the top screened with the blocks
black-boxed (`emir run --abstract`), the IR budget per block, and the block that
is fed from two sides re-checked with the top's pin voltages. Every number is
checked against hand calculation and against a flat run of the same network.

Everything is invented and offline: `make_inputs.py` writes the DSPFs, the
process file and the simulation results (nutascii) from closed-form waveforms.
No simulator and no PDK are needed. **Screening, not sign-off.**

```
run.sh [OUTDIR]          # default: examples/05_chip_level/out
```

It needs `emir` (from PATH, else `python3 -m emir` from this clone) and exits
non-zero when a result differs from `expected/`.

## The chip

```
pad VDD (-50,0) -Rt0 0.5- T1 (0,0) -Rt1 1.0- XADC:VDDA (0,40)            block adc, 1 pin
                           |  \-Rt2 1.0- XPLL:VDDP (40,0)               block pll, west pin
                           \-Rt3 0.5- T2 -Rt4 1.75- T3 -Rt5 0.5- XPLL:VDDP#2 (70,0)
                                                                        block pll, east pin
```

| block | supply pins | grid | devices |
|---|---|---|---|
| adc | VDDA at (0, 0) | metal1 rail, 4 x 1 ohm, 3 um wide | M0..M3 behind 5 ohm contacts: 2 mA pulses, duty 0.25, period 10 ns, staggered by a quarter period (2 mA in total, flat) |
| pll | VDDP at (0, 0), VDDP:E at (30, 0) | metal1 rail, 3 x 1 ohm, 1.4 um wide | MV0 at x = 10 behind a 5 ohm contact: a 4 mA pulse, duty 0.5, period 2.5 ns |

The PLL is placed at (40, 0), orientation R0, so its two ports land on the top's two
records `XPLL:VDDP` and `XPLL:VDDP#2` by coordinates (`--place XPLL=40,0,R0`).

## What `run.sh` does

1. `emir abstract adc.dspf --mode cc-grid ... -o adc.abstract.json` and the same for the
   PLL (an abstract is cc-grid: the blocks' pin currents through their grids);
2. `emir check` and `emir run top.dspf --abstract adc.abstract.json@XADC --abstract
   pll.abstract.json@XPLL --place XPLL=40,0,R0 --align sync --budget-mv 15
   --port-voltages-out port_voltages.json`, then `emir report`;
3. `emir abstract pll.dspf --mode cc-grid ... --port-voltages port_voltages.json@XPLL`
   (back-annotation);
4. an ADC that fires all four devices together (8 mA for 1 ns every 10 ns), for the
   alignment modes;
5. the flat reference: `emir run flat.dspf --cc-sim flat.raw --mode cc-grid`;
6. `measure.py`: the checks below, and the top-level self-consistency loop run with the
   commands;
7. `compare.py`: the outputs against `expected/` (relative tolerance 1e-6).

## The numbers, by hand

| quantity | hand | run |
|---|---|---|
| ADC pin current into the top net | -2 mA (avg = rms = peak: the staggered pulses add up flat) | -2.0000 mA |
| PLL pin split (west / east) | -2 mA x 2/3 and x 1/3 (10 and 20 ohm to the device) | -1.3333 / -0.6667 mA |
| PLL port conductance | 1 / (3 x 1 ohm) | Y = [[1/3, -1/3], [-1/3, 1/3]] S |
| V at XADC:VDDA | -(0.5 ohm x 4 mA + 1 ohm x 2 mA) | -4.0000 mV |
| V at the PLL pins (west, east) | nodal solve of the two-pin network: -92/27, -98/27 mV | -3.4074, -3.6296 mV |
| pass-through inside the PLL | (V_W - V_E) / 3 ohm = 2/27 mA | 0.07407 mA |
| ADC budget | 4.0 (top) + 7.5 (internal) = 11.5 mV, exact for one pin | 11.5000 mV = flat 11.5000 mV |
| PLL budget | 3.6296 + 11.3333 = 14.963 mV, an upper bound for two pins | flat 14.8148 mV |
| PLL Rm1, pins shorted | 2 mA x 2/3 = 1.3333 mA: 0.952x on 1.4 um, WARN | WARN |
| PLL Rm1, back-annotated | 38/27 = 1.4074 mA: 1.005x, FAIL (the block-alone verdict reads 5.26 % low) | FAIL, equal to flat |

The rms and peak hand values use a pulse sampled every 2.5 ps with 25 ps edges,
integrated by trapezoid: each edge adds amp^2 (R/3 + dt^2 / (6 R)) to the integral
of i^2.

## What `measure.py` checks (all to a relative 1e-9 unless noted)

- every top-level branch, avg / rms / peak: hierarchical (blocks black-boxed, sync)
  equals the flat run; measured maximum relative difference 4.9e-13;
- every block branch, back-annotated with the top's pin voltages, equals the same
  branch of the flat run (6.6e-13 for the ADC, 4.4e-15 for the PLL);
- the pin voltages, the pass-through and the trunk currents equal the hand numbers;
- the budget: exact for the one-pin ADC, a bound for the two-pin PLL;
- alignment on the trunk Rt0, ADC in bursts with the PLL (mA): inphase rms 5.3384
  (the bound), async rms 4.1826 (hand 4.1826), sync over 20 PLL phases rms
  3.7808 to 4.5491 and peak 8 to 12: **async is not an upper bound for related
  clocks**; the phase sweep is the worst of its phases;
- the self-consistency loop (an ohmic stand-in: the ADC draws V / 50 ohm, so the
  converged current has the closed form 1 V / 51.5 ohm = 19.4175 mA): pass 0
  over-reads by 3.00 % (the conservative direction), converged in 6 passes to
  7e-10 of the closed form; EMIR-W805 flags the 3 % droop at every pass.

The measured numbers are written to `OUT/measured.json`; `tests/test_example_chip_level.py`
runs all of this (`pytest -m example`).
