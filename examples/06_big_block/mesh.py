"""Synthetic power meshes for the big-block example. SYNTHETIC: no real design,
no real process; every number here is invented and round.

Two generators, both deterministic (fixed seeds):

* `xyce_case(n, k, out)` -- an n x n metal mesh fed at one corner, with k
  level-1 CMOS inverters on it, written four ways from ONE description so the
  routes cannot drift apart:

  - `grid_r.dspf`   the R-only extraction (what cc-grid solves),
  - `grid_rc.dspf`  the same plus the mesh capacitance (for --dynamic),
  - `rcc.cir`       the RC netlist with every branch current saved (the RCC
                    route; `rcc0.cir` saves nothing, `rccv.cir` node voltages),
  - `cc.cir`        the devices on an ideal rail, a 0 V ammeter per device
                    supply pin (the C-only CC route),
  - `cc_tb.cir`     the same devices on the ideal rail with nothing probed or
                    saved: the CC testbench `emir run --self-consistent`
                    splits per pin itself,

  and, with `vss=True`, a second mesh for VSS (the same geometry, the NMOS
  sources on it through their own contacts): the DSPFs hold both nets, the
  RCC decks both meshes, and the CC decks an ideal VSS rail driven by a 0 V
  source, so `emir run --self-consistent --net VDD,VSS` closes both rails
  (VSS's droop is a rise: ground bounce),

  plus the `<result>.names` sidecars that map Xyce's current names onto the
  DSPF's (`emir schema names`).

* `stream_case(n, k, nt, out)` -- an n x n mesh with k taps and synthetic
  periodic tap currents written as a nutascii result, no simulator needed.
  n=300, k=2000, nt=501 is the case (about 92 k nodes, 181 k resistors) whose
  dense transfer matrix needs 2.7 GiB (181 401 branches x 2 001 taps x 8 bytes).
"""
from __future__ import annotations

import json
import os

import numpy as np

# ---- xyce_case: invented values
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
#: the same devices without gate-overlap capacitance (a probe of where the
#: small average error of cc-grid comes from)
MODELS_NO_OVERLAP = MODELS.replace(" cgso=0.3n cgdo=0.3n", "")


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


def xyce_case(n: int, k: int, out: str, r_seg: float | None = None,
              r_cont: float | None = None, overlap: bool = True, vss: bool = False,
              phases: int = 8, c_node: float | None = None,
              stop: float | None = None) -> dict:
    """Write the four decks and two DSPFs of one mesh size into `out`.
    `r_seg`, `r_cont` override the segment and contact resistance; `overlap`
    False drops the devices' gate-overlap capacitance; `vss` adds the VSS
    mesh (the module doc); `phases` is how many staggered phases the
    inverters switch in (8: k / 8 devices switch together); `c_node`
    overrides the mesh capacitance per node (`grid_rc.dspf` and the RCC
    decks); `stop` the transient's length (STOP, 4 periods, by default)."""
    r_seg = float(r_seg or SHEET * PITCH / W)
    r_cont = float(r_cont or R_CONT)
    cn = float(c_node or C_NODE)
    models = MODELS if overlap else MODELS_NO_OVERLAP
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
    edges = mesh_edges("VDD", "R")
    gedges = mesh_edges("VSS", "RG") if vss else []
    sites = device_sites(n, k)

    # ---- the DSPFs (R-only, and R + mesh capacitance)
    head = ["*|DSPF 1.3", f'*|DESIGN "mesh{n}"', "*|OPERATING_TEMPERATURE 110",
            "*|NET VDD 0", "*|P (VDD I 0 0.0000 0.0000)"]
    for i in range(n):
        for j in range(n):
            if (i, j) != (0, 0):
                head.append(f"*|S ({_node(i, j)} {i * PITCH:.4f} {j * PITCH:.4f})")
    for d, (i, j) in enumerate(sites):
        head.append(f"*|I (MP{d}:s MP{d} s I 0 {i * PITCH:.4f} {j * PITCH:.4f})")
    res = []
    for nm, a, b, i0, j0, i1, j1 in edges:
        x0, y0, x1, y1 = i0 * PITCH, j0 * PITCH, i1 * PITCH, j1 * PITCH
        box = ((x0, y0 - W / 2, x1, y0 + W / 2) if y0 == y1
               else (x0 - W / 2, y0, x0 + W / 2, y1))
        res.append(f"{nm} {a} {b} {r_seg:.6g} $metal1 $L={PITCH:.4f} $W={W:.4f} "
                   f"$LLX={box[0]:.4f} $LLY={box[1]:.4f} $URX={box[2]:.4f} "
                   f"$URY={box[3]:.4f} $lvl=1")
    for d, (i, j) in enumerate(sites):
        res.append(f"Rc{d} {_node(i, j)} MP{d}:s {r_cont:.6g} $cont $A=0.02 $VC=2 "
                   f"$LLX={i * PITCH - .1:.4f} $LLY={j * PITCH - .1:.4f} "
                   f"$URX={i * PITCH + .1:.4f} $URY={j * PITCH + .1:.4f} $lvl=0")
    caps = [f"Cn{i}_{j} {_node(i, j)} 0 {cn:.4g}"
            for i in range(n) for j in range(n) if (i, j) != (0, 0)]
    if vss:
        # the VSS net: the same mesh, the NMOS sources through their contacts
        res = head + res
        head = ["*|NET VSS 0", "*|P (VSS I 0 0.0000 0.0000)"]
        for i in range(n):
            for j in range(n):
                if (i, j) != (0, 0):
                    head.append(f"*|S ({_node(i, j, 'VSS')} {i * PITCH:.4f} {j * PITCH:.4f})")
        for d, (i, j) in enumerate(sites):
            head.append(f"*|I (MN{d}:s MN{d} s I 0 {i * PITCH:.4f} {j * PITCH:.4f})")
        for nm, a, b, i0, j0, i1, j1 in gedges:
            x0, y0, x1, y1 = i0 * PITCH, j0 * PITCH, i1 * PITCH, j1 * PITCH
            box = ((x0, y0 - W / 2, x1, y0 + W / 2) if y0 == y1
                   else (x0 - W / 2, y0, x0 + W / 2, y1))
            head.append(f"{nm} {a} {b} {r_seg:.6g} $metal1 $L={PITCH:.4f} $W={W:.4f} "
                        f"$LLX={box[0]:.4f} $LLY={box[1]:.4f} $URX={box[2]:.4f} "
                        f"$URY={box[3]:.4f} $lvl=1")
        for d, (i, j) in enumerate(sites):
            head.append(f"RGc{d} {_node(i, j, 'VSS')} MN{d}:s {r_cont:.6g} $cont $A=0.02 $VC=2 "
                        f"$LLX={i * PITCH - .1:.4f} $LLY={j * PITCH - .1:.4f} "
                        f"$URX={i * PITCH + .1:.4f} $URY={j * PITCH + .1:.4f} $lvl=0")
        caps += [f"Cg{i}_{j} {_node(i, j, 'VSS')} 0 {cn:.4g}"
                 for i in range(n) for j in range(n) if (i, j) != (0, 0)]
        head, res = res, head
    _write(os.path.join(out, "grid_r.dspf"), head + res + [".ENDS"])
    _write(os.path.join(out, "grid_rc.dspf"), head + res + caps + [".ENDS"])

    def inverters(supply, ground=lambda d: "0"):
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
    if vss:
        names += [e[0] for e in gedges] + [f"RGc{d}" for d in range(len(sites))]
    tail = [".options timeint reltol=1e-4", f".tran 1p {float(stop or STOP):.4g}"]
    gnd_src = ["VGND VSS 0 DC 0"] if vss else []

    # ---- RCC: the RC mesh with the devices, every branch current printed
    rc = ["* RCC: RC mesh + devices (synthetic)", models, f"VSUP VDD 0 DC {VDD:g}"]
    rc += [f"{nm} {_xnode(a)} {_xnode(b)} {r_seg:.6g}" for nm, a, b, *_ in edges]
    rc += [f"Cn{i}_{j} {_xnode(_node(i, j))} 0 {cn:.4g}"
           for i in range(n) for j in range(n) if (i, j) != (0, 0)]
    rc += [f"Rc{d} {_xnode(_node(i, j))} s{d} {r_cont:.6g}" for d, (i, j) in enumerate(sites)]
    if vss:
        rc += gnd_src
        rc += [f"{nm} {_xnode(a)} {_xnode(b)} {r_seg:.6g}" for nm, a, b, *_ in gedges]
        rc += [f"Cg{i}_{j} {_xnode(_node(i, j, 'VSS'))} 0 {cn:.4g}"
               for i in range(n) for j in range(n) if (i, j) != (0, 0)]
        rc += [f"RGc{d} {_xnode(_node(i, j, 'VSS'))} g{d} {r_cont:.6g}"
               for d, (i, j) in enumerate(sites)]
    rc += inverters(lambda d: f"s{d}", (lambda d: f"g{d}") if vss else (lambda d: "0"))
    _write(os.path.join(out, "rcc.cir"), rc + tail + [
        ".print tran format=raw file=rcc.raw " + " ".join(f"I({x})" for x in names), ".end"])
    _write(os.path.join(out, "rcc0.cir"), rc + tail + [
        ".print tran format=raw file=rcc0.raw I(VSUP)", ".end"])
    _write(os.path.join(out, "rccv.cir"), rc + tail + [
        ".print tran format=raw file=rccv.raw V(*)", ".end"])
    with open(os.path.join(out, "rcc.raw.names"), "w", encoding="utf-8") as f:
        json.dump({"map": {f"I({x})": [f"{x}:1", 1.0] for x in names}}, f)

    # ---- CC: the rail is one ideal node; a 0 V ammeter in each supply pin.
    # I(VAd) flows from VDD into the device's source: into the instance, the
    # sign emir's inst:pin convention uses.
    cc = ["* CC: devices on an ideal rail (synthetic)", models, f"VSUP VDD 0 DC {VDD:g}"]
    cc += [f"VA{d} VDD s{d} DC 0" for d in range(len(sites))]
    printed = [f"I(VA{d})" for d in range(len(sites))]
    names_map = {f"I(VA{d})": [f"MP{d}:s", 1.0] for d in range(len(sites))}
    if vss:
        # I(VGd) flows from the NMOS source to VSS: out of the instance
        cc += gnd_src + [f"VG{d} g{d} VSS DC 0" for d in range(len(sites))]
        printed += [f"I(VG{d})" for d in range(len(sites))]
        names_map.update({f"I(VG{d})": [f"MN{d}:s", -1.0] for d in range(len(sites))})
    cc += inverters(lambda d: f"s{d}", (lambda d: f"g{d}") if vss else (lambda d: "0"))
    _write(os.path.join(out, "cc.cir"), cc + tail + [
        ".print tran format=raw file=cc.raw " + " ".join(printed), ".end"])
    with open(os.path.join(out, "cc.raw.names"), "w", encoding="utf-8") as f:
        json.dump({"map": names_map}, f)

    # ---- the CC testbench as a person writes it: the devices straight on the
    # ideal rail, nothing probed and nothing saved. `emir run --mode cc-grid
    # --self-consistent --sim xyce --netlist cc_tb.cir` splits it (a supply
    # node, a source and a 0 V probe per instance pin) and saves the probes.
    tb = ["* CC testbench: devices on an ideal rail (synthetic)", models,
          f"VSUP VDD 0 DC {VDD:g}"]
    tb += gnd_src + inverters(lambda d: "VDD", (lambda d: "VSS") if vss else (lambda d: "0"))
    _write(os.path.join(out, "cc_tb.cir"), tb + tail + [".end"])
    info = {"n": n, "grid_nodes": n * n + len(sites), "resistors": len(names),
            "devices": len(sites), "caps": len(caps), "r_seg": r_seg, "r_cont": r_cont,
            "overlap": overlap, "phases": phases, "c_node": cn}
    if vss:
        info.update(vss=True, grid_nodes=2 * (n * n + len(sites)))
    return info


# ---- stream_case: synthetic currents, no simulator

S_PITCH = 1.0         # um
S_W = 0.4             # um
S_RSEG = 0.3125       # ohm per segment
S_RCONT = 28.0        # ohm, 2 cuts
S_SPAN = 50e-9        # s simulated
S_PERIOD = 10e-9      # s


def stream_case(n: int, k: int, nt: int, out: str, seed: int = 0) -> dict:
    """An n x n mesh, k taps at random sites, and a nutascii result with nt
    points of periodic pulse currents (20 uA base, 100 uA for 2.5 ns of every
    10 ns, a random phase per tap). Returns the paths and sizes."""
    os.makedirs(out, exist_ok=True)
    tag = f"s{n}_k{k}_t{nt}"
    rng = np.random.default_rng(seed)
    nid = lambda i, j: f"VDD:{i * n + j + 1}"            # noqa: E731
    L = ["*|DSPF 1.3", f'*|DESIGN "smesh{n}"', "*|OPERATING_TEMPERATURE 110",
         "*|NET VDD 0", "*|P (VDD I *1 0.0 0.0)"]
    for i in range(n):
        for j in range(n):
            L.append(f"*|S ({nid(i, j)} {j * S_PITCH:.3f} {i * S_PITCH:.3f})")
    taps = rng.choice(n * n, k, replace=False)
    for q, t in enumerate(taps):
        i, j = divmod(int(t), n)
        L.append(f"*|I (M{q}:s M{q} s I 0 {j * S_PITCH:.3f} {i * S_PITCH:.3f})")
    r = 0
    L.append(f"R{r} VDD {nid(0, 0)} 0.01 $metal1 $W=2.0 $L=0.1 $LLX=0 $LLY=-1 "
             f"$URX=0.1 $URY=1")
    r += 1
    for i in range(n):
        for j in range(n):
            if j + 1 < n:
                L.append(f"R{r} {nid(i, j)} {nid(i, j + 1)} {S_RSEG:.5f} $metal1 "
                         f"$W={S_W} $L={S_PITCH} $LLX={j * S_PITCH} "
                         f"$LLY={i * S_PITCH - S_W / 2:.3f} $URX={(j + 1) * S_PITCH} "
                         f"$URY={i * S_PITCH + S_W / 2:.3f}")
                r += 1
            if i + 1 < n:
                L.append(f"R{r} {nid(i, j)} {nid(i + 1, j)} {S_RSEG:.5f} $metal1 "
                         f"$W={S_W} $L={S_PITCH} $LLX={j * S_PITCH - S_W / 2:.3f} "
                         f"$LLY={i * S_PITCH} $URX={j * S_PITCH + S_W / 2:.3f} "
                         f"$URY={(i + 1) * S_PITCH}")
                r += 1
    for q, t in enumerate(taps):
        i, j = divmod(int(t), n)
        L.append(f"Rc{q} {nid(i, j)} M{q}:s {S_RCONT} $cont $A=0.02 $VC=2 "
                 f"$LLX={j * S_PITCH - .1:.3f} $LLY={i * S_PITCH - .1:.3f} "
                 f"$URX={j * S_PITCH + .1:.3f} $URY={i * S_PITCH + .1:.3f}")
    L.append(".ENDS")
    dspf = os.path.join(out, f"{tag}.dspf")
    _write(dspf, L)
    t = np.linspace(0.0, S_SPAN, nt)
    ph = rng.uniform(0.0, S_PERIOD, k)
    # Spectre's convention: positive INTO the instance (out of the net)
    Wv = 20e-6 + 80e-6 * (((t[None, :] - ph[:, None]) % S_PERIOD) < 2.5e-9)
    raw = os.path.join(out, f"{tag}.raw")
    with open(raw, "w", encoding="utf-8") as f:
        f.write(f"Title: synthetic stream case\nDate: none\nPlotname: Transient Analysis\n"
                f"Flags: real\nNo. Variables: {k + 1}\nNo. Points: {nt}\n")
        f.write("Variables:\t0\ttime\tsec\n"
                + "".join(f"\t{q + 1}\tM{q}:s\tI\n" for q in range(k)) + "Values:\n")
        for p in range(nt):
            f.write(f"{p}\t{t[p]:.6e}\n" + "".join(f"\t{Wv[q, p]:.6e}\n" for q in range(k)))
    return {"tag": tag, "dspf": dspf, "raw": raw, "nodes": n * n + k + 1,
            "resistors": r + k, "taps": k, "points": nt, "period": S_PERIOD}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], allow_abbrev=False)
    ap.add_argument("kind", choices=("xyce", "stream"))
    ap.add_argument("--n", type=int, required=True)
    ap.add_argument("--k", type=int, default=32)
    ap.add_argument("--nt", type=int, default=501)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    info = (xyce_case(a.n, a.k, a.out) if a.kind == "xyce"
            else stream_case(a.n, a.k, a.nt, a.out))
    print(json.dumps(info))
