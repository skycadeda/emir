"""Synthetic power meshes with decaps, for decap-aware EM. SYNTHETIC: an
invented mesh, invented devices and decaps, invented process numbers.

Built on example 06's mesh (`../06_big_block/mesh.py`): an n x n Metal1 mesh fed
at one corner, k level-1 CMOS inverters switching at 1 GHz in eight phases.
Three cases, each written from ONE description so the routes cannot drift
apart:

* `decap_case(n, k, ndec, cdec, resr, out)` -- example 06's mesh plus `ndec`
  decaps at fixed random mesh nodes (never the feed), each on its own strap:
  - `grid_decap.dspf`: the RC DSPF (2 fF per node) + per decap a Metal1 strap
    `Rsd<q>` (0.5 ohm) to a dead-end node `DS<q>`, and a MOS decap device
    `MDC<q>` there (gate DS<q>; drain, source and bulk on VSS, a net with a
    port and no resistance: an ideal far plate) that `cap_models` sizes to
    C = cox W L = `cdec` behind esr_a = `resr`;
  - `rcc.cir`: example 06's RCC deck + per decap the strap, the ESR and the
    capacitor (linear R + C: the model emir uses), their currents printed;
  - `cc.cir`: example 06's CC deck, unchanged (a decap on an ideal rail
    carries nothing);
  - `process.yaml`: the example's process file with this case's decap model.
* `ref_deck(out, taps, window)` -- the solver-only reference: the same RC
  network with decaps, driven by PWL current sources equal to the CC pin
  waveforms over the window, repeated once before it (`ref.cir`).
* `two_rail_case(n, k, ndec, cdec, resr, out)` -- VDD and a mirrored resistive
  VSS mesh with the inverters between them and the decaps between the meshes
  (a strap on each side), the RCC and CC decks and the DSPF of both nets.
* `small_case(out)` -- offline: a small decap mesh and a synthetic CC result
  (no simulator).
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "06_big_block"))
import mesh  # noqa: E402

#: invented decap device: cox 10 fF/um^2, W L chosen per C (L 5 um)
COX = 1e-2            # F/m^2
DEC_L = 5e-6          # m
STRAP_R = 0.5         # ohm, Metal1 strap from the mesh to the decap
STRAP_W = 0.2         # um: a thin strap, so a decap's current shows on it


def _write(path, lines):
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def decap_sites(n: int, ndec: int, seed: int = 7) -> list:
    """`ndec` distinct mesh sites, never the feed corner, deterministic."""
    rng = np.random.default_rng(seed)
    sites = set()
    while len(sites) < ndec:
        s = (int(rng.integers(0, n)), int(rng.integers(0, n)))
        if s != (0, 0):
            sites.add(s)
    return sorted(sites)


def process_file(out: str, resr: float) -> str:
    """The example's process file with the decap model of this case."""
    with open(os.path.join(HERE, "process.yaml"), encoding="utf-8") as f:
        text = f.read()
    text = text.replace("esr_a: 2.0", f"esr_a: {resr:g}")
    path = os.path.join(out, "process.yaml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def _dec_w(cdec: float) -> float:
    """The width (m) that makes cox W L = cdec."""
    return cdec / (COX * DEC_L)


def decap_case(n: int, k: int, ndec: int, cdec: float, resr: float, out: str,
               seed: int = 7, **mesh_opts) -> dict:
    """Example 06's mesh with `ndec` decaps on straps (see the module doc);
    `mesh_opts` go to `mesh.xyce_case` (`r_seg`: the high-droop mesh)."""
    info = mesh.xyce_case(n, k, out, **mesh_opts)
    sites = decap_sites(n, ndec, seed) if ndec else []
    process_file(out, resr)
    # ---- the DSPF: the RC one, the straps, the decaps, the ideal VSS
    with open(os.path.join(out, "grid_rc.dspf"), encoding="utf-8") as f:
        lines = f.read().splitlines()
    end = lines.index(".ENDS")
    head = [f"*|S (DS{q} {i * mesh.PITCH + 2.0:.4f} {j * mesh.PITCH + 2.0:.4f})"
            for q, (i, j) in enumerate(sites)]
    straps = []
    for q, (i, j) in enumerate(sites):
        x, y = i * mesh.PITCH, j * mesh.PITCH
        straps.append(f"Rsd{q} {mesh._node(i, j)} DS{q} {STRAP_R:g} $metal1 $L=2.0000 "
                      f"$W={STRAP_W:.4f} $LLX={x:.4f} $LLY={y:.4f} $URX={x + 2:.4f} "
                      f"$URY={y + 2:.4f} $lvl=1")
    vss = []
    devs = []
    if sites:
        vss = ["*|NET VSS 0", "*|P (VSS I 0 -10.0000 -10.0000)"]
        for q, (i, j) in enumerate(sites):
            x, y = i * mesh.PITCH + 2.0, j * mesh.PITCH + 2.0
            vss += [f"*|I (MDC{q}:d MDC{q} d I 0 {x:.4f} {y:.4f})",
                    f"*|I (MDC{q}:s MDC{q} s I 0 {x + 0.5:.4f} {y:.4f})"]
            devs.append(f"MDC{q} MDC{q}:d DS{q} MDC{q}:s VSS ncap L={DEC_L:g} "
                        f"W={_dec_w(cdec):g}")
    first_r = next(x for x in range(len(lines)) if lines[x].startswith("R"))
    dspf = lines[:first_r] + head + lines[first_r:end] + straps + vss + devs + [".ENDS"]
    _write(os.path.join(out, "grid_decap.dspf"), dspf)
    # ---- the RCC deck: the straps, the ESR and the capacitor of each decap
    with open(os.path.join(out, "rcc.cir"), encoding="utf-8") as f:
        rc = f.read().splitlines()
    extra = []
    for q, (i, j) in enumerate(sites):
        nd = mesh._xnode(mesh._node(i, j))
        extra += [f"Rsd{q} {nd} ds{q} {STRAP_R:g}", f"Rdc{q} ds{q} dci{q} {resr:g}",
                  f"Cdc{q} dci{q} 0 {cdec:g}"]
    at = next(x for x in range(len(rc)) if rc[x].startswith(".options"))
    rc = rc[:at] + extra + rc[at:]
    pr = next(x for x in range(len(rc)) if rc[x].startswith(".print"))
    rc[pr] += "".join(f" I(Rsd{q}) I(Rdc{q})" for q in range(len(sites)))
    _write(os.path.join(out, "rcc.cir"), rc)
    names_path = os.path.join(out, "rcc.raw.names")
    with open(names_path, encoding="utf-8") as f:
        names = json.load(f)
    for q in range(len(sites)):
        names["map"][f"I(Rsd{q})"] = [f"Rsd{q}:1", 1.0]
        names["map"][f"I(Rdc{q})"] = [f"Rdc{q}:1", 1.0]
    with open(names_path, "w", encoding="utf-8") as f:
        json.dump(names, f)
    info.update(decaps=len(sites), c_decap_F=cdec, r_esr_ohm=resr, strap_ohm=STRAP_R,
                sites=sites)
    return info


def ref_deck(out: str, taps: list, window: dict, n: int, sites: list, cdec: float,
             resr: float, repeats: int = 1) -> str:
    """`ref.cir`: the RC network of `decap_case` (mesh, straps, decaps), with
    each device replaced by a PWL current source carrying its CC pin current
    over the window, the window repeated `repeats` times before itself (the
    decaps' time constants are far shorter). The last window is the one
    compared."""
    with open(os.path.join(out, "rcc.cir"), encoding="utf-8") as f:
        rc = f.read().splitlines()
    keep = [x for x in rc if x.startswith(("R", "C", "VSUP", "*"))]
    tw = window["t1"] - window["t0"]
    src = []
    for tp in taps:
        if tp.get("feed") or tp.get("wave") is None:
            continue
        d = tp["name"].split(":")[0][2:]                     # MP<d>:s
        t = np.asarray(window["t"]) - window["t0"]
        y = -np.asarray(tp["wave"])                          # into the instance
        ts = np.concatenate([t[:-1] + r * tw for r in range(repeats)] + [t + repeats * tw])
        ys = np.concatenate([y[:-1] for _r in range(repeats)] + [y])
        pts = " ".join(f"{a:.9e} {b:.9e}" for a, b in zip(ts, ys))
        src.append(f"IL{d} s{d} 0 PWL({pts})")
    stop = (repeats + 1) * tw
    prints = next(x for x in rc if x.startswith(".print")).replace("file=rcc.raw",
                                                                   "file=ref.raw")
    deck = (["* solver-only reference: the RC network driven by the CC pin currents "
             "(synthetic)"] + keep[1:] + src
            # print only the last window (TSTART): the lead-in is not compared
            + [".options timeint reltol=1e-5", f".tran 0.5p {stop:.6g} {repeats * tw:.6g}",
               prints, ".end"])
    _write(os.path.join(out, "ref.cir"), deck)
    with open(os.path.join(out, "rcc.raw.names"), encoding="utf-8") as f:
        names = json.load(f)
    with open(os.path.join(out, "ref.raw.names"), "w", encoding="utf-8") as f:
        json.dump(names, f)
    return os.path.join(out, "ref.cir")


def two_rail_case(n: int, k: int, ndec: int, cdec: float, resr: float, out: str,
                  seed: int = 7) -> dict:
    """VDD and a mirrored resistive VSS mesh (each fed at the corner (0, 0)),
    the inverters between them, decaps between the meshes with a strap on
    each side. Writes `grid_two.dspf` (both nets, 2 fF per node), `rcc.cir`
    (both meshes RC, every branch current printed), `cc.cir` (both rails
    ideal, an ammeter in each device's supply and ground pin) and the names
    sidecars and process file."""
    os.makedirs(out, exist_ok=True)
    r_seg = mesh.SHEET * mesh.PITCH / mesh.W
    sites = mesh.device_sites(n, k)
    dsites = decap_sites(n, ndec, seed)
    process_file(out, resr)

    def node(net, i, j):
        return net if (i, j) == (0, 0) else f"{net}:{i}_{j}"

    def xn(name):
        return "0" if name == "VSS" else name.replace(":", "_")

    edges = {}
    for net in ("VDD", "VSS"):
        e = []
        for i in range(n):
            for j in range(n):
                if i + 1 < n:
                    e.append((f"Rh{net[1]}{i}_{j}", node(net, i, j), node(net, i + 1, j),
                              i, j, i + 1, j))
                if j + 1 < n:
                    e.append((f"Rv{net[1]}{i}_{j}", node(net, i, j), node(net, i, j + 1),
                              i, j, i, j + 1))
        edges[net] = e
    pin = {"VDD": ("MP", "s", "Rc"), "VSS": ("MN", "s", "Rg")}
    L = ["*|DSPF 1.3", f'*|DESIGN "two{n}"', "*|OPERATING_TEMPERATURE 110"]
    res_all = {}
    for net, off in (("VDD", 0.0), ("VSS", 0.5)):
        dev, term, rc = pin[net]
        L += [f"*|NET {net} 0", f"*|P ({net} I 0 {off:.4f} {off:.4f})"]
        for i in range(n):
            for j in range(n):
                if (i, j) != (0, 0):
                    L.append(f"*|S ({node(net, i, j)} {i * mesh.PITCH + off:.4f} "
                             f"{j * mesh.PITCH + off:.4f})")
        for d, (i, j) in enumerate(sites):
            L.append(f"*|I ({dev}{d}:{term} {dev}{d} {term} I 0 {i * mesh.PITCH + off:.4f} "
                     f"{j * mesh.PITCH + off:.4f})")
        tag = "V" if net == "VDD" else "G"
        for q, (i, j) in enumerate(dsites):
            L.append(f"*|S (DS{tag}{q} {i * mesh.PITCH + 2 + off:.4f} "
                     f"{j * mesh.PITCH + 2 + off:.4f})")
        rs = []
        for nm, a, b, i0, j0, i1, j1 in edges[net]:
            x0, y0, x1, y1 = (i0 * mesh.PITCH + off, j0 * mesh.PITCH + off,
                              i1 * mesh.PITCH + off, j1 * mesh.PITCH + off)
            box = ((x0, y0 - mesh.W / 2, x1, y0 + mesh.W / 2) if y0 == y1
                   else (x0 - mesh.W / 2, y0, x0 + mesh.W / 2, y1))
            rs.append(f"{nm} {a} {b} {r_seg:.6g} $metal1 $L={mesh.PITCH:.4f} "
                      f"$W={mesh.W:.4f} $LLX={box[0]:.4f} $LLY={box[1]:.4f} "
                      f"$URX={box[2]:.4f} $URY={box[3]:.4f} $lvl=1")
        for d, (i, j) in enumerate(sites):
            x, y = i * mesh.PITCH + off, j * mesh.PITCH + off
            rs.append(f"{rc}{d} {node(net, i, j)} {dev}{d}:{term} {mesh.R_CONT:g} $cont "
                      f"$A=0.02 $VC=2 $LLX={x - .1:.4f} $LLY={y - .1:.4f} $URX={x + .1:.4f} "
                      f"$URY={y + .1:.4f} $lvl=0")
        for q, (i, j) in enumerate(dsites):
            x, y = i * mesh.PITCH + off, j * mesh.PITCH + off
            rs.append(f"Rs{tag}{q} {node(net, i, j)} DS{tag}{q} {STRAP_R:g} $metal1 "
                      f"$L=2.0000 $W={STRAP_W:.4f} $LLX={x:.4f} $LLY={y:.4f} "
                      f"$URX={x + 2:.4f} $URY={y + 2:.4f} $lvl=1")
        L += rs
        L += [f"Cn{tag}{i}_{j} {node(net, i, j)} 0 {mesh.C_NODE:.4g}"
              for i in range(n) for j in range(n) if (i, j) != (0, 0)]
        res_all[net] = [x.split()[0] for x in rs]
    L += [f"MDC{q} DSG{q} DSV{q} DSG{q} DSG{q} ncap L={DEC_L:g} W={_dec_w(cdec):g}"
          for q in range(len(dsites))]
    L.append(".ENDS")
    _write(os.path.join(out, "grid_two.dspf"), L)

    def inverters():
        lines = []
        for d in range(len(sites)):
            dly = (d % 8) * mesh.PERIOD / 8 + 0.05e-9
            lines += [f"MP{d} out{d} in{d} s{d} s{d} pch W=40u L=0.1u",
                      f"MN{d} out{d} in{d} g{d} g{d} nch W=20u L=0.1u",
                      f"CL{d} out{d} g{d} 200f",
                      f"VIN{d} in{d} g{d} PULSE(0 {mesh.VDD:g} {dly:.4g} 30p 30p "
                      f"{mesh.PERIOD / 2 - 30e-12:.4g} {mesh.PERIOD:.4g})"]
        return lines
    tail = [".options timeint reltol=1e-4", f".tran 1p {mesh.STOP:.4g}"]
    rc = ["* RCC: two RC meshes + devices + decaps between them (synthetic)", mesh.MODELS,
          f"VSUP VDD 0 DC {mesh.VDD:g}"]
    names = []
    for net in ("VDD", "VSS"):
        for nm, a, b, *_ in edges[net]:
            rc.append(f"{nm} {xn(a)} {xn(b)} {r_seg:.6g}")
            names.append(nm)
        rc += [f"Cn{'V' if net == 'VDD' else 'G'}{i}_{j} {xn(node(net, i, j))} 0 "
               f"{mesh.C_NODE:.4g}" for i in range(n) for j in range(n) if (i, j) != (0, 0)]
    for d, (i, j) in enumerate(sites):
        rc += [f"Rc{d} {xn(node('VDD', i, j))} s{d} {mesh.R_CONT:g}",
               f"Rg{d} {xn(node('VSS', i, j))} g{d} {mesh.R_CONT:g}"]
        names += [f"Rc{d}", f"Rg{d}"]
    for q, (i, j) in enumerate(dsites):
        rc += [f"RsV{q} {xn(node('VDD', i, j))} dsv{q} {STRAP_R:g}",
               f"RsG{q} {xn(node('VSS', i, j))} dsg{q} {STRAP_R:g}",
               f"Rdc{q} dsv{q} dci{q} {resr:g}", f"Cdc{q} dci{q} dsg{q} {cdec:g}"]
        names += [f"RsV{q}", f"RsG{q}", f"Rdc{q}"]
    rc += inverters()
    _write(os.path.join(out, "rcc.cir"), rc + tail + [
        ".print tran format=raw file=rcc.raw " + " ".join(f"I({x})" for x in names), ".end"])
    with open(os.path.join(out, "rcc.raw.names"), "w", encoding="utf-8") as f:
        json.dump({"map": {f"I({x})": [f"{x}:1", 1.0] for x in names}}, f)
    cc = ["* CC: devices on two ideal rails (synthetic)", mesh.MODELS,
          f"VSUP VDD 0 DC {mesh.VDD:g}"]
    cc += [f"VAV{d} VDD s{d} DC 0" for d in range(len(sites))]
    cc += [f"VAG{d} g{d} 0 DC 0" for d in range(len(sites))]
    cc += inverters()
    _write(os.path.join(out, "cc.cir"), cc + tail + [
        ".print tran format=raw file=cc.raw "
        + " ".join(f"I(VAV{d}) I(VAG{d})" for d in range(len(sites))), ".end"])
    # I(VAG) flows out of the device's ground pin into the ideal ground: into
    # the instance is its negative
    m = {f"I(VAV{d})": [f"MP{d}:s", 1.0] for d in range(len(sites))}
    m.update({f"I(VAG{d})": [f"MN{d}:s", -1.0] for d in range(len(sites))})
    with open(os.path.join(out, "cc.raw.names"), "w", encoding="utf-8") as f:
        json.dump({"map": m}, f)
    return {"n": n, "devices": len(sites), "decaps": len(dsites), "c_decap_F": cdec,
            "r_esr_ohm": resr, "strap_ohm": STRAP_R, "resistors": len(names)}


def small_case(out: str, n: int = 12, k: int = 20, ndec: int = 4, cdec: float = 20e-12,
               resr: float = 1.0, nt: int = 1501, periods: int = 3) -> dict:
    """Offline: a 12 x 12 mesh (0.25 ohm segments, 2 fF per node) with 20
    instance pins, each drawing 0.05 mA plus a 10 mA, 20 ps triangular spike
    per 1 ns (random phases), and 4 decaps of 20 pF / 1 ohm on 0.5 ohm straps
    0.2 um wide; the CC result as nutascii, `periods` periods of `nt` points
    (2 ps). No simulator: the pin currents are written directly."""
    os.makedirs(out, exist_ok=True)
    r_seg = mesh.SHEET * mesh.PITCH / mesh.W
    rng = np.random.default_rng(3)
    sites = mesh.device_sites(n, k, seed=2)
    dsites = decap_sites(n, ndec)
    process_file(out, resr)
    L = ["*|DSPF 1.3", '*|DESIGN "small"', "*|OPERATING_TEMPERATURE 110", "*|NET VDD 0",
         "*|P (VDD I 0 0.0000 0.0000)"]
    for i in range(n):
        for j in range(n):
            if (i, j) != (0, 0):
                L.append(f"*|S ({mesh._node(i, j)} {i * mesh.PITCH:.4f} {j * mesh.PITCH:.4f})")
    for d, (i, j) in enumerate(sites):
        L.append(f"*|I (MP{d}:s MP{d} s I 0 {i * mesh.PITCH:.4f} {j * mesh.PITCH:.4f})")
    for q, (i, j) in enumerate(dsites):
        L.append(f"*|S (DS{q} {i * mesh.PITCH + 2:.4f} {j * mesh.PITCH + 2:.4f})")
    for i in range(n):
        for j in range(n):
            for di, dj, tag in ((1, 0, "h"), (0, 1, "v")):
                if i + di < n and j + dj < n:
                    x0, y0 = i * mesh.PITCH, j * mesh.PITCH
                    x1, y1 = (i + di) * mesh.PITCH, (j + dj) * mesh.PITCH
                    box = ((x0, y0 - mesh.W / 2, x1, y0 + mesh.W / 2) if tag == "h"
                           else (x0 - mesh.W / 2, y0, x0 + mesh.W / 2, y1))
                    L.append(f"R{tag}{i}_{j} {mesh._node(i, j)} {mesh._node(i + di, j + dj)} "
                             f"{r_seg:.6g} $metal1 $L={mesh.PITCH:.4f} $W={mesh.W:.4f} "
                             f"$LLX={box[0]:.4f} $LLY={box[1]:.4f} $URX={box[2]:.4f} "
                             f"$URY={box[3]:.4f} $lvl=1")
    for d, (i, j) in enumerate(sites):
        x, y = i * mesh.PITCH, j * mesh.PITCH
        L.append(f"Rc{d} {mesh._node(i, j)} MP{d}:s {mesh.R_CONT:g} $cont $A=0.02 $VC=2 "
                 f"$LLX={x - .1:.4f} $LLY={y - .1:.4f} $URX={x + .1:.4f} $URY={y + .1:.4f} "
                 f"$lvl=0")
    for q, (i, j) in enumerate(dsites):
        x, y = i * mesh.PITCH, j * mesh.PITCH
        L.append(f"Rsd{q} {mesh._node(i, j)} DS{q} {STRAP_R:g} $metal1 $L=2.0000 "
                 f"$W={STRAP_W:.4f} $LLX={x:.4f} $LLY={y:.4f} $URX={x + 2:.4f} "
                 f"$URY={y + 2:.4f} $lvl=1")
    L += [f"Cn{i}_{j} {mesh._node(i, j)} 0 {mesh.C_NODE:.4g}"
          for i in range(n) for j in range(n) if (i, j) != (0, 0)]
    L += ["*|NET VSS 0", "*|P (VSS I 0 -10.0000 -10.0000)"]
    for q, (i, j) in enumerate(dsites):
        x, y = i * mesh.PITCH + 2, j * mesh.PITCH + 2
        L += [f"*|I (MDC{q}:d MDC{q} d I 0 {x:.4f} {y:.4f})",
              f"*|I (MDC{q}:s MDC{q} s I 0 {x + 0.5:.4f} {y:.4f})"]
    L += [f"MDC{q} MDC{q}:d DS{q} MDC{q}:s VSS ncap L={DEC_L:g} W={_dec_w(cdec):g}"
          for q in range(len(dsites))]
    L.append(".ENDS")
    dspf = os.path.join(out, "small.dspf")
    _write(dspf, L)
    per = 1e-9
    t = np.linspace(0.0, periods * per, nt)
    ph = rng.uniform(0.0, per, len(sites))
    W = []
    for d in range(len(sites)):
        u = ((t - ph[d]) % per) / 20e-12               # a 20 ps triangular spike
        W.append(0.05e-3 + 10e-3 * np.clip(1 - np.abs(u - 0.5) * 2, 0, None))
    raw = os.path.join(out, "small.raw")
    with open(raw, "w", encoding="utf-8") as f:
        f.write(f"Title: synthetic decap case\nDate: none\nPlotname: Transient Analysis\n"
                f"Flags: real\nNo. Variables: {len(sites) + 1}\nNo. Points: {len(t)}\n")
        f.write("Variables:\t0\ttime\tsec\n"
                + "".join(f"\t{d + 1}\tMP{d}:s\tI\n" for d in range(len(sites)))
                + "Values:\n")
        for p in range(len(t)):
            f.write(f"{p}\t{t[p]:.9e}\n" + "".join(f"\t{W[d][p]:.9e}\n"
                                                    for d in range(len(sites))))
    return {"dspf": dspf, "raw": raw, "process": os.path.join(out, "process.yaml"),
            "period": per, "decaps": len(dsites), "pins": len(sites)}
