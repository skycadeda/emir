#!/usr/bin/env python3
r"""Write the synthetic inputs of the chip-level example (no simulator needed).

Everything here is invented and hand-checkable; nothing comes from a PDK.

    pad VDD (-50,0) -Rt0 0.5- T1 (0,0) -Rt1 1.0- XADC:VDDA (0,40)            block adc
                               |  \-Rt2 1.0- XPLL:VDDP (40,0)               block pll, west pin
                               \-Rt3 0.5- T2 -Rt4 1.75- T3 -Rt5 0.5- XPLL:VDDP#2 (70,0)
                                                                            block pll, east pin

    adc  one port VDDA at (0,0); a metal1 rail of 4 x 1 ohm; devices M0..M3 behind
         5 ohm contacts at x = 10, 20, 30, 40. Each draws a 2 mA pulse, duty 0.25,
         period 10 ns, the four staggered by a quarter period: 2 mA in total, flat.
    pll  ports VDDP (0,0) and VDDP:E (30,0); a metal1 rail of 3 x 1 ohm; one device
         MV0 behind a 5 ohm contact at x = 10: a 4 mA pulse, duty 0.5, period 2.5 ns.
         Placed at (40, 0), orientation R0.

Both simulations start on one clock edge at t = 0 and carry a start-up transient
(x1.5 for the first ADC period, x1.3 for the first PLL period) that the window
discards. The time step is uniform (2.5 ps) and the 25 ps edges are whole
steps, so the stored periods sample the waveforms exactly.

    python make_inputs.py OUTDIR

writes adc.dspf, pll.dspf, top.dspf (the blocks black-boxed: their pins are
*|I records), flat.dspf (top and both blocks on one net: the reference),
process.yaml, adc.raw, pll.raw (the block simulations), adc_burst.raw (the ADC
firing all four devices together: 8 mA for 1 ns every 10 ns) and flat.raw
(both blocks in one simulation, on the same clock). Results are nutascii with
Spectre's sign (positive into the instance).
"""
from __future__ import annotations

import os
import sys

import numpy as np

DT = 2.5e-12          # uniform simulation step (s)
T_ADC = 10e-9
T_PLL = 2.5e-9
STOP = 40e-9
RISE = 10 * DT        # 25 ps edges

#: block placements in the top frame: instance -> (dx, dy)
PLACE = {"XADC": (0.0, 40.0), "XPLL": (40.0, 0.0)}


def pulse(t, amp, duty, period, delay, rise=RISE):
    """A trapezoid pulse train whose area per period is exactly amp * duty * period."""
    tau = np.mod(t - delay, period)
    top = duty * period - rise
    y = np.zeros_like(t)
    up = tau < rise
    y[up] = amp * tau[up] / rise
    flat = (tau >= rise) & (tau < rise + top)
    y[flat] = amp
    dn = (tau >= rise + top) & (tau < 2 * rise + top)
    y[dn] = amp * (1 - (tau[dn] - rise - top) / rise)
    return y


def adc_currents(t):
    out = {}
    for k in range(4):
        y = pulse(t, 2e-3, 0.25, T_ADC, delay=0.05 * T_ADC + k * T_ADC / 4)
        out[f"M{k}:s"] = y * np.where(t < T_ADC, 1.5, 1.0)
    return out


def adc_burst_currents(t):
    """All four ADC devices together: 2 mA each, 1 ns every 10 ns."""
    return {f"M{k}:s": pulse(t, 2e-3, 0.1, T_ADC, delay=0.05 * T_ADC) for k in range(4)}


def pll_currents(t):
    y = pulse(t, 4e-3, 0.5, T_PLL, delay=0.1 * T_PLL)
    return {"MV0:s": y * np.where(t < T_PLL, 1.3, 1.0)}


def write_nutascii(path, t, sigs: dict):
    names = list(sigs)
    cols = [sigs[n] for n in names]
    with open(path, "w", encoding="utf-8") as f:
        f.write("Title: synthetic tran\nDate: synthetic\n")
        f.write("Plotname: Transient Analysis `tr'\nFlags: real\n")
        f.write(f"No. Variables: {len(names) + 1}\nNo. Points: {len(t)}\n")
        f.write("Variables:\t0\ttime\ttime\n")
        for i, n in enumerate(names, 1):
            f.write(f"\t{i}\t{n}\tI\n")
        f.write("Values:\n")
        for j in range(len(t)):
            f.write(f"{j}\t{t[j]:.15e}\n")
            for c in cols:
                f.write(f"\t{c[j]:.15e}\n")


def R(name, n1, n2, r, layer, x0, y0, x1, y1, w=None, cuts=None, lvl=1):
    """One DSPF resistor line: a wire ($L $W and its box) or a via ($VC cuts)."""
    s = f"{name} {n1} {n2} {r:.6f} ${layer}"
    if cuts:
        s += f" $VC={cuts}"
    else:
        s += f" $L={abs(x1 - x0) + abs(y1 - y0):.4f} $W={w:.6f}"
    lo_x, hi_x = sorted((x0, x1))
    lo_y, hi_y = sorted((y0, y1))
    if w:
        if y0 == y1:
            lo_y, hi_y = y0 - w / 2, y0 + w / 2
        else:
            lo_x, hi_x = x0 - w / 2, x0 + w / 2
    return s + f" $LLX={lo_x:.4f} $LLY={lo_y:.4f} $URX={hi_x:.4f} $URY={hi_y:.4f} $lvl={lvl}"


HEAD = ['*|DSPF 1.3', None, '*|OPERATING_TEMPERATURE 100', '*|GROUND_NET 0']


def _head(design):
    return [h if h else f'*|DESIGN "{design}"' for h in HEAD]


def adc_dspf():
    L = _head("adc") + ['*|NET VDDA 0', '*|P (VDDA I 0 0.0000 0.0000)']
    xs = [0, 10, 20, 30, 40]
    nodes = ["VDDA"] + [f"VDDA:{i}" for i in range(1, 5)]
    for n, x in zip(nodes[1:], xs[1:]):
        L.append(f"*|S ({n} {x:.4f} 0.0000)")
    for k in range(4):
        L.append(f"*|I (M{k}:s M{k} s I 0 {xs[k + 1]:.4f} 0.0000)")
    for i in range(4):
        L.append(R(f"Rm{i + 1}", nodes[i], nodes[i + 1], 1.0, "metal1",
                   xs[i], 0, xs[i + 1], 0, w=3.0))
    for k in range(4):
        L.append(R(f"Rc{k}", nodes[k + 1], f"M{k}:s", 5.0, "cont",
                   xs[k + 1] - .1, -.1, xs[k + 1] + .1, .1, cuts=2, lvl=0))
    return "\n".join(L + [".ENDS"]) + "\n"


def pll_dspf():
    L = _head("pll") + [
        '*|NET VDDP 0', '*|P (VDDP I 0 0.0000 0.0000)', '*|P (VDDP:E I 0 30.0000 0.0000)',
        '*|S (VDDP:1 10.0000 0.0000)', '*|S (VDDP:2 20.0000 0.0000)',
        '*|I (MV0:s MV0 s I 0 10.0000 0.0000)']
    nodes = ["VDDP", "VDDP:1", "VDDP:2", "VDDP:E"]
    xs = [0, 10, 20, 30]
    for i in range(3):
        L.append(R(f"Rm{i + 1}", nodes[i], nodes[i + 1], 1.0, "metal1",
                   xs[i], 0, xs[i + 1], 0, w=1.4))
    L.append(R("Rc0", "VDDP:1", "MV0:s", 5.0, "cont", 9.9, -.1, 10.1, .1, cuts=2, lvl=0))
    return "\n".join(L + [".ENDS"]) + "\n"


#: the top-level grid: (name, n1, n2, ohm, x0, y0, x1, y1, width um)
TOP_R = [
    ("Rt0", "VDD", "VDD:T1", 0.5, -50, 0, 0, 0, 4.4),
    ("Rt1", "VDD:T1", "XADC:VDDA", 1.0, 0, 0, 0, 40, 3.0),
    ("Rt2", "VDD:T1", "XPLL:VDDP", 1.0, 0, 0, 40, 0, 2.0),
    ("Rt3", "VDD:T1", "VDD:T2", 0.5, 0, 0, 0, -20, 2.0),
    ("Rt4", "VDD:T2", "VDD:T3", 1.75, 0, -20, 70, -20, 2.0),
    ("Rt5", "VDD:T3", "XPLL:VDDP#2", 0.5, 70, -20, 70, 0, 2.0),
]


def top_lines():
    L = ['*|NET VDD 0', '*|P (VDD I 0 -50.0000 0.0000)',
         '*|S (VDD:T1 0.0000 0.0000)', '*|S (VDD:T2 0.0000 -20.0000)',
         '*|S (VDD:T3 70.0000 -20.0000)',
         # the black-boxed blocks' pins: instance-pin records of the top, one
         # per physical connection, with coordinates
         '*|I (XADC:VDDA XADC VDDA I 0 0.0000 40.0000)',
         '*|I (XPLL:VDDP XPLL VDDP I 0 40.0000 0.0000)',
         '*|I (XPLL:VDDP#2 XPLL VDDP I 0 70.0000 0.0000)']
    for (n, a, b, r, x0, y0, x1, y1, w) in TOP_R:
        L.append(R(n, a, b, r, "metal2", x0, y0, x1, y1, w=w, lvl=2))
    return L


def top_dspf():
    return "\n".join(_head("top") + top_lines() + [".ENDS"]) + "\n"


def flat_dspf():
    """Top and both blocks on one net: the reference the hierarchy must equal."""
    port_map = {("XADC", "VDDA"): "XADC:VDDA", ("XPLL", "VDDP"): "XPLL:VDDP",
                ("XPLL", "VDDP:E"): "XPLL:VDDP#2"}
    L = _head("flat") + [ln for ln in top_lines() if not ln.startswith("*|I")]
    L += ['*|S (XADC:VDDA 0.0000 40.0000)', '*|S (XPLL:VDDP 40.0000 0.0000)',
          '*|S (XPLL:VDDP#2 70.0000 0.0000)']
    for inst, src in (("XADC", adc_dspf()), ("XPLL", pll_dspf())):
        dx, dy = PLACE[inst]
        ports = {ln.split()[1].lstrip("(") for ln in src.splitlines() if ln.startswith("*|P")}

        def ren(n, inst=inst, ports=ports):
            return port_map[(inst, n)] if n in ports else f"{inst}/{n}"
        for ln in src.splitlines():
            if ln.startswith("*|S"):
                f = ln[ln.find("(") + 1:ln.rfind(")")].split()
                L.append(f"*|S ({ren(f[0])} {float(f[1]) + dx:.4f} {float(f[2]) + dy:.4f})")
            elif ln.startswith("*|I"):
                f = ln[ln.find("(") + 1:ln.rfind(")")].split()
                L.append(f"*|I ({ren(f[0])} {inst}/{f[1]} {f[2]} {f[3]} {f[4]} "
                         f"{float(f[5]) + dx:.4f} {float(f[6]) + dy:.4f})")
            elif ln[:1] == "R":
                f = ln.split()
                # the element's box moves with the block, as its nodes do
                rest = [_shift(w, dx, dy) for w in f[3:]]
                L.append(f"R{inst}_{f[0][1:]} {ren(f[1])} {ren(f[2])} {' '.join(rest)}")
    return "\n".join(L + [".ENDS"]) + "\n"


def _shift(word: str, dx: float, dy: float) -> str:
    """A DSPF annotation `$LLX=...` .. `$URY=...` moved by (dx, dy); any other
    word unchanged."""
    key, eq, val = word.partition("=")
    if not eq:
        return word
    d = {"$LLX": dx, "$URX": dx, "$LLY": dy, "$URY": dy}.get(key)
    return word if d is None else f"{key}={float(val) + d:.4f}"


PROCESS = """# ILLUSTRATIVE PROCESS DATA for examples/05_chip_level -- invented round
# numbers, not a foundry deck; the GDS layer numbers are synthetic too.
pdk: chip-level example (illustrative)
warn_fraction: 0.8
temp_reference: 100.0
em_tref: 100.0
layers:
  Poly:   {gds: [102, 0], sheet_res: 10.0, tc1: 0.0, min_width: 0.2,
           jmax_avg: 0.5, jmax_peak: 1.0}
  Metal1: {gds: [101, 0], sheet_res: 0.1, tc1: 0.0, min_width: 0.2,
           jmax_avg: 1.0, jmax_rms_k: 3.2, jmax_peak: 10.0}
  Metal2: {gds: [103, 0], sheet_res: 0.05, tc1: 0.0, min_width: 0.2,
           jmax_avg: 1.0, jmax_rms_k: 3.2, jmax_peak: 10.0}
vias:
  Cont: {gds: [100, 0], below: Poly, above: Metal1, res: 10.0, vcwidth: 0.2,
         imax_avg: 1.5, imax_rms: 3.0, imax_peak: 6.0}
"""


def main(out):
    os.makedirs(out, exist_ok=True)
    for name, txt in (("adc.dspf", adc_dspf()), ("pll.dspf", pll_dspf()),
                      ("top.dspf", top_dspf()), ("flat.dspf", flat_dspf()),
                      ("process.yaml", PROCESS)):
        with open(os.path.join(out, name), "w", encoding="utf-8") as f:
            f.write(txt)
    t = np.arange(0, STOP + DT / 2, DT)                  # 40 ns
    write_nutascii(os.path.join(out, "adc.raw"), t, adc_currents(t))
    write_nutascii(os.path.join(out, "adc_burst.raw"), t, adc_burst_currents(t))
    tp = np.arange(0, 4 * T_PLL + DT / 2, DT)            # the PLL alone: 10 ns
    write_nutascii(os.path.join(out, "pll.raw"), tp, pll_currents(tp))
    a, p = adc_currents(t), pll_currents(t)
    flat = {f"XADC/{k}": v for k, v in a.items()}
    flat.update({f"XPLL/{k}": v for k, v in p.items()})
    write_nutascii(os.path.join(out, "flat.raw"), t, flat)
    print(f"wrote the example inputs to {out}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "out")
