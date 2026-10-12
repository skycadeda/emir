"""A synthetic supply mesh for example 06. SYNTHETIC: no real design,
no real process; every number here is invented and round.

`xyce_case(n, k, out)` -- an n x n metal mesh fed at one corner, with k
level-1 CMOS inverters on it, written from ONE description so the extraction
and the testbench cannot drift apart:

- `grid_rc.dspf`    the RC extraction: the mesh, its capacitance and the
                    devices' contacts (what `emir run` screens),
- `rcc.cir`         the RC netlist with the devices, every branch current
                    printed (the rcc route),
- `rcc.raw.names`   the sidecar that maps Xyce's current names onto the
                    DSPF's (`emir schema names`).

With `vss=True` a second mesh carries VSS (the same geometry, the NMOS sources
on it through their own contacts): the DSPF holds both nets and the deck both
meshes.
"""
from __future__ import annotations

import json
import os

import numpy as np

# ---- invented values
PITCH = 10.0          # um, mesh pitch
W = 2.0               # um, strap width
SHEET = 0.05          # ohm/square -> 0.25 ohm per segment
C_NODE = 2e-15        # F of wire capacitance per mesh node
R_CONT = 5.0          # ohm, device contact (2 cuts)
PERIOD = 1e-9         # s
STOP = 4e-9           # s
VDD = 1.0             # V

MODELS = """.model nch NMOS level=1 vto=0.4 kp=200u lambda=0.05 cgso=0.3n cgdo=0.3n
.model pch PMOS level=1 vto=-0.4 kp=80u lambda=0.05 cgso=0.3n cgdo=0.3n
"""


def _node(i, j, net="VDD"):
    return net if (i, j) == (0, 0) else f"{net}:{i}_{j}"


def _xnode(name):                       # the same node in the SPICE deck
    return name.replace(":", "_")


def device_sites(n, k, seed=1):
    """k distinct mesh sites, deterministic."""
    rng = np.random.default_rng(seed)
    sites = set()
    while len(sites) < k:
        sites.add((int(rng.integers(0, n)), int(rng.integers(0, n))))
    return sorted(sites)


def _write(path, lines):
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def xyce_case(n: int, k: int, out: str, vss: bool = False, phases: int = 8) -> dict:
    """Write the RC DSPF, the Xyce deck and its names sidecar of one mesh
    into `out`. `vss` adds the VSS mesh (the module doc); `phases` is how
    many staggered phases the inverters switch in (8: k / 8 devices switch
    together)."""
    r_seg = SHEET * PITCH / W
    os.makedirs(out, exist_ok=True)

    def mesh_edges(net, prefix):
        out_ = []
        for i in range(n):
            for j in range(n):
                if i + 1 < n:
                    out_.append((f"{prefix}h{i}_{j}", _node(i, j, net), _node(i + 1, j, net),
                                 i, j, i + 1, j))
                if j + 1 < n:
                    out_.append((f"{prefix}v{i}_{j}", _node(i, j, net), _node(i, j + 1, net),
                                 i, j, i, j + 1))
        return out_

    def strap(nm, a, b, i0, j0, i1, j1):
        x0, y0, x1, y1 = i0 * PITCH, j0 * PITCH, i1 * PITCH, j1 * PITCH
        box = ((x0, y0 - W / 2, x1, y0 + W / 2) if y0 == y1
               else (x0 - W / 2, y0, x0 + W / 2, y1))
        return (f"{nm} {a} {b} {r_seg:.6g} $metal1 $L={PITCH:.4f} $W={W:.4f} "
                f"$LLX={box[0]:.4f} $LLY={box[1]:.4f} $URX={box[2]:.4f} "
                f"$URY={box[3]:.4f} $lvl=1")

    def contact(nm, node, pin, i, j):
        return (f"{nm} {node} {pin} {R_CONT:.6g} $cont $A=0.02 $VC=2 "
                f"$LLX={i * PITCH - .1:.4f} $LLY={j * PITCH - .1:.4f} "
                f"$URX={i * PITCH + .1:.4f} $URY={j * PITCH + .1:.4f} $lvl=0")

    def net_block(net, prefix, dev, cprefix, cname):
        """One net of the DSPF: its records, its resistors, its capacitors."""
        recs = [f"*|NET {net} 0", f"*|P ({net} I 0 0.0000 0.0000)"]
        recs += [f"*|S ({_node(i, j, net)} {i * PITCH:.4f} {j * PITCH:.4f})"
                 for i in range(n) for j in range(n) if (i, j) != (0, 0)]
        recs += [f"*|I ({dev}{d}:s {dev}{d} s I 0 {i * PITCH:.4f} {j * PITCH:.4f})"
                 for d, (i, j) in enumerate(sites)]
        res = [strap(*e) for e in mesh_edges(net, prefix)]
        res += [contact(f"{cprefix}{d}", _node(i, j, net), f"{dev}{d}:s", i, j)
                for d, (i, j) in enumerate(sites)]
        caps = [f"{cname}{i}_{j} {_node(i, j, net)} 0 {C_NODE:.4g}"
                for i in range(n) for j in range(n) if (i, j) != (0, 0)]
        return recs + res, caps

    sites = device_sites(n, k)
    edges = mesh_edges("VDD", "R")
    gedges = mesh_edges("VSS", "RG") if vss else []

    # ---- the RC DSPF
    head = ["*|DSPF 1.3", f'*|DESIGN "mesh{n}"', "*|OPERATING_TEMPERATURE 110"]
    body, caps = net_block("VDD", "R", "MP", "Rc", "Cn")
    if vss:
        gbody, gcaps = net_block("VSS", "RG", "MN", "RGc", "Cg")
        body, caps = body + gbody, caps + gcaps
    _write(os.path.join(out, "grid_rc.dspf"), head + body + caps + [".ENDS"])

    # ---- the deck: the RC mesh with the devices, every branch current printed
    def inverters(supply, ground):
        lines = []
        for d in range(len(sites)):
            dly = (d % phases) * PERIOD / phases + 0.05e-9    # staggered phases
            lines += [f"MP{d} out{d} in{d} {supply(d)} {supply(d)} pch W=40u L=0.1u",
                      f"MN{d} out{d} in{d} {ground(d)} {ground(d)} nch W=20u L=0.1u",
                      f"CL{d} out{d} 0 200f",
                      f"VIN{d} in{d} 0 PULSE(0 {VDD:g} {dly:.4g} 30p 30p "
                      f"{PERIOD / 2 - 30e-12:.4g} {PERIOD:.4g})"]
        return lines

    names = [e[0] for e in edges] + [f"Rc{d}" for d in range(len(sites))]
    rc = ["* rcc: RC mesh + devices (synthetic)", MODELS, f"VSUP VDD 0 DC {VDD:g}"]
    rc += [f"{nm} {_xnode(a)} {_xnode(b)} {r_seg:.6g}" for nm, a, b, *_ in edges]
    rc += [f"Cn{i}_{j} {_xnode(_node(i, j))} 0 {C_NODE:.4g}"
           for i in range(n) for j in range(n) if (i, j) != (0, 0)]
    rc += [f"Rc{d} {_xnode(_node(i, j))} s{d} {R_CONT:.6g}" for d, (i, j) in enumerate(sites)]
    if vss:
        names += [e[0] for e in gedges] + [f"RGc{d}" for d in range(len(sites))]
        rc += ["VGND VSS 0 DC 0"]
        rc += [f"{nm} {_xnode(a)} {_xnode(b)} {r_seg:.6g}" for nm, a, b, *_ in gedges]
        rc += [f"Cg{i}_{j} {_xnode(_node(i, j, 'VSS'))} 0 {C_NODE:.4g}"
               for i in range(n) for j in range(n) if (i, j) != (0, 0)]
        rc += [f"RGc{d} {_xnode(_node(i, j, 'VSS'))} g{d} {R_CONT:.6g}"
               for d, (i, j) in enumerate(sites)]
    rc += inverters(lambda d: f"s{d}", (lambda d: f"g{d}") if vss else (lambda d: "0"))
    tail = [".options timeint reltol=1e-4", f".tran 1p {STOP:.4g}"]
    _write(os.path.join(out, "rcc.cir"), rc + tail + [
        ".print tran format=raw file=rcc.raw " + " ".join(f"I({x})" for x in names), ".end"])
    with open(os.path.join(out, "rcc.raw.names"), "w", encoding="utf-8") as f:
        json.dump({"map": {f"I({x})": [f"{x}:1", 1.0] for x in names}}, f)

    info = {"n": n, "grid_nodes": n * n + len(sites), "resistors": len(names),
            "devices": len(sites), "caps": len(caps), "r_seg": r_seg, "r_cont": R_CONT,
            "phases": phases, "c_node": C_NODE}
    if vss:
        info.update(vss=True, grid_nodes=2 * (n * n + len(sites)))
    return info


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], allow_abbrev=False)
    ap.add_argument("kind", choices=("xyce",))
    ap.add_argument("--n", type=int, required=True)
    ap.add_argument("--k", type=int, default=32)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    print(json.dumps(xyce_case(a.n, a.k, a.out)))
