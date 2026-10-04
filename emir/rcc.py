"""The other mode: simulate the RC netlist and read the currents off it.

CC + grid exists because RCC gets slow. On a small block it does not, and then
the decoupling buys nothing while still costing an approximation. This module
is the direct route -- no superposition, no tap mapping, no droop feedback:
every resistor in the extracted network is a real element in the simulation, so
its current is *measured* rather than reconstructed.

    save Rw1:1 Rw2:1 ...        # generated from the DSPF
    -> avg / rms / peak per branch, over a whole-period window
    -> the same EM verdict function every other path uses

**Which mode to use.** Measured at the original development site on a
generated test block -- a bank of inverters tapping a segmented metal supply
rail, the acceptance gate that compares CC + grid with RCC -- refining the
rail's segmentation while the CC netlist stayed identical:

| rail nodes | RCC | CC + grid |
|---|---|---|
| 41 | 1.92 s | 1.97 s |
| 521 | 3.83 s | 2.19 s |
| 2057 | 9.53 s | 2.61 s |

So: **small net, use `rcc`** -- it is fast enough (seconds), and it is the
answer rather than an estimate of it. The CC route earns its keep as the node count climbs, and
that crossover is a property of the design, not a rule of thumb: measure it on
your own block rather than assume it.

**Voltages come free.** Given branch currents and the network, node potentials
follow by walking a spanning tree from the reference node and accumulating
`I*R` -- so RCC mode needs only the branch-current saves, and static *and*
dynamic IR both fall out. Better, the branches *not* on the tree are then a
check nothing else provides: each one independently predicts the same potential
difference, and `loop_residual` reports the worst disagreement. A netlist paired
with the wrong DSPF, a missing save, or a flipped sign shows up there
immediately, where a grid solve would have quietly produced a plausible number.
"""
from __future__ import annotations

import numpy as np

from . import ccsim, waves
from .dspf import Network, em_clusters, map_layers, net_scaling
from .errors import EmirError, notice


def save_branches(dspf, net=None, per_line=40, prefix="") -> str:
    """The Spectre `save` lines for every resistor on `net`, or on every net.

    Saving branch currents rather than `save *:currents` is what keeps an RCC
    result file manageable: one signal per extracted resistor, and nothing
    else.

    `net=None` covers **every** net, which is what EM actually needs -- EM is a
    property of any wire carrying current, not just the rails. `prefix` is the
    instance path when the extracted subckt is instantiated in a testbench
    (e.g. `XI0.`).
    """
    if net is None:
        names = [r["name"] for r in dspf.res]
    else:
        names = [r["name"] for r in dspf.net_resistors(net)]
    if not names:
        raise EmirError("EMIR-E205", f"no resistors on net {net!r}", net=net)
    out = []
    for i in range(0, len(names), per_line):
        out.append("save " + " ".join(f"{prefix}{n}:1"
                                      for n in names[i:i + per_line]))
    return "\n".join(out)


def net_port(dspf, net: str):
    """The port node on `net`, or None if it has no port.

    On a rail this is the feed -- where the net's current imbalance enters. A
    net with no port is closed by its own device pins, so it has no feed and
    needs none.
    """
    nodes = {n for r in dspf.net_resistors(net) for n in (r["n1"], r["n2"])}
    ports = [p for p in dspf.net_ports(net) if p in nodes]
    return ports[0] if ports else None


def reference_node(dspf, net: str, explicit=None) -> str:
    """The node potentials are measured against.

    A power net has a port and that is the feed. A signal net may have none, and
    then the choice is arbitrary -- which is fine, because the reported IR
    figure is `V.max() - V.min()`, a *spread* that is invariant to it. What is
    not fine is silently grounding at a port belonging to some other net, so a
    node on this net is required.
    """
    nodes = {n for r in dspf.net_resistors(net) for n in (r["n1"], r["n2"])}
    if not nodes:
        raise EmirError("EMIR-E205", f"no resistors on net {net!r}", net=net)
    if explicit:
        if explicit not in nodes:
            raise EmirError("EMIR-E206", f"reference node {explicit!r} is not on net "
                             f"{net!r}", net=net)
        return explicit
    ports = [p for p in dspf.net_ports(net) if p in nodes]
    if ports:
        return ports[0]
    return sorted(nodes)[0]


def spanning_order(nw, ground: str) -> tuple:
    """Breadth-first tree from `ground`. Returns (steps, tree_mask, unreached).

    `steps` is [(node, branch, sign)] in an order where each node's parent is
    already known, so potentials can be accumulated in one pass.
    """
    n = len(nw.nodes)
    adj = [[] for _ in range(n)]
    for e, (a, b) in enumerate(zip(nw.a, nw.b)):
        adj[a].append((b, e, -1.0))       # V[b] = V[a] - I_e * R_e
        adj[b].append((a, e, +1.0))       # V[a] = V[b] + I_e * R_e
    seen = np.zeros(n, bool)
    g = nw.idx[ground]
    seen[g] = True
    queue, steps, tree = [g], [], np.zeros(len(nw.res), bool)
    while queue:
        u = queue.pop()
        for v, e, s in adj[u]:
            if not seen[v]:
                seen[v] = True
                tree[e] = True
                steps.append((v, u, e, s))
                queue.append(v)
    return steps, tree, [nw.nodes[i] for i in np.flatnonzero(~seen)]


def node_voltages(nw, steps, Ib, R=None) -> np.ndarray:
    """Accumulate potentials from branch currents along the tree.

    `Ib` may be one vector or (branches x time); the reference node sits at 0,
    so every value is a signed excursion from the feed. `R`: each branch's
    resistance (default the network's, at the analysis temperature).
    """
    Ib = np.asarray(Ib)
    R = 1.0 / nw.g if R is None else R
    two = Ib.ndim == 2
    V = np.zeros((len(nw.nodes), Ib.shape[1])) if two else np.zeros(len(nw.nodes))
    for v, u, e, s in steps:
        V[v] = V[u] + s * Ib[e] * R[e]
    return V


def simulated_r(nw) -> np.ndarray:
    """Each branch's resistance as the DSPF gives it: what the simulation
    ran, before any temperature scaling (`Network.g` is scaled per layer)."""
    return np.array([r["r"] if r["r"] > 0 else 1e-12 for r in nw.res])


def loop_residual(nw, steps, Ib, tree=None, V=None, R=None) -> float:
    """Worst disagreement on the branches the tree did not use, in volts.

    Every off-tree branch is an independent prediction of a potential
    difference the tree already fixed. They agree only if the simulated
    currents really belong to this network, which is exactly the pairing this
    mode cannot otherwise verify. **On a loop-free network there are no such
    branches and this check is vacuous** -- `rcc_check` reports the off-tree
    count so a zero cannot be mistaken for a passed test.
    """
    R = 1.0 / nw.g if R is None else R
    if V is None:
        V = node_voltages(nw, steps, Ib, R=R)
    off = np.ones(len(nw.res), bool) if tree is None else ~tree
    if not off.any():
        return 0.0
    pred = V[nw.a[off]] - V[nw.b[off]]
    return float(np.abs(pred - np.asarray(Ib)[off] * R[off]).max())


def internal_kcl(nw, dspf, net, Ib, ground) -> float:
    """Worst KCL imbalance on nodes nothing injects into, in amps.

    An RCC run does not save the device currents, so a tap node legitimately
    fails to balance -- that mismatch *is* the device current. Only nodes with
    no element attached can be asked to sum to zero, and they are the ones that
    catch a mis-read or mis-scaled branch current.
    """
    n = len(nw.nodes)
    net_in = np.zeros(n)
    np.add.at(net_in, nw.a, Ib)
    np.add.at(net_in, nw.b, -Ib)
    driven = {t["node"] for t in dspf.net_taps(net)} | {ground}
    driven |= set(dspf.net_ports(net))
    keep = np.array([nd not in driven for nd in nw.nodes])
    return float(np.abs(net_in[keep]).max()) if keep.any() else 0.0


def feed_wave(nw, B, ground: str) -> np.ndarray:
    """The waveform the averaging window is chosen on: the current into the
    net at its reference node -- the feed on a rail, so the whole block's
    activity -- as the sum of the branches there (`B`: branches x time).

    It used to be the branch with the largest peak, taken to be the feed. It
    is not always: a contact's spike can out-peak the feed segment when the
    rail's and the device's capacitance supply part of it, and that one
    device's waveform can repeat from the first period while the rest of the
    block is still starting up. The window then took in the start-up period
    and every average came out LOW (measured on an open-simulator inverter
    bank, scripts/opensim_proof.py: the feed's average 12.4 % low). A
    reference node nothing flows through (a port-less net's arbitrary node)
    falls back to the largest-peak branch.
    """
    g = nw.idx[ground]
    sign = np.where(nw.a == g, 1.0, 0.0) - np.where(nw.b == g, 1.0, 0.0)
    if sign.any():
        feed = sign @ B
        if np.abs(feed).max() > 1e-9 * max(np.abs(B).max(), 1e-30):
            return feed
    return B[int(np.argmax(np.abs(B).max(axis=1)))]


def rcc_check(dspf, net: str, proc, result, ground: str, analysis=None,
              period=None, window_sel=None, explicit_layers=None, dynamic=True,
              chunk=4096, log=print) -> dict:
    """EM + IR on one net from an RCC simulation, in `check_net`'s shape.

    Same return contract as the CC+grid path so reporting, the CLI and the
    gate consume either without caring which produced it.

    `result` is a result path, or a `waves.Waves` already read (so a run over
    many nets reads the file once).
    """
    from .engine import METRICS
    lmap = map_layers(dspf, proc, explicit_layers, log=log)
    unmapped = [t for t in dspf.layer_tokens() if t not in lmap]
    r_at_temp, rscale = net_scaling(dspf, proc, lmap)
    nw = Network(dspf, net, rscale=rscale)

    w = waves.load(result, analysis=analysis)
    log(f"[emir] {w.summary()}")
    if w.is_complex:
        raise EmirError("EMIR-E414", "RCC mode needs a time-domain result (tran or PSS td);"
                         " harmonics carry no branch-by-branch time base here")
    names = [r["name"] for r in nw.res]
    found = w.match([f"{n}:1" for n in names])
    missing = [n for n in names if f"{n}:1" not in found]
    if missing:
        raise EmirError("EMIR-E415",
            f"{len(missing)} of {len(names)} branch currents were not saved "
            f"(e.g. {', '.join(missing[:4])}). Add:\n{save_branches(dspf, net)}", net=net)
    B = np.array([np.asarray(w[found[f"{n}:1"]]) for n in names])

    # window from the net's feed, whose periodicity is the block's
    lead = feed_wave(nw, B, ground)
    if window_sel is not None:
        win = window_sel
    else:
        if period is None:
            period = ccsim.detect_period(w.x, lead)
            if period:
                log(f"[emir] period auto-detected: {period:.6g} s "
                    f"({1 / period:.6g} Hz) -- override with --period")
        win = ccsim.window(w.x, lead, period=period, log=log)
    tt, Bw = ccsim.clip_window(w.x, B, win["t0"], win["t1"])
    wt = ccsim.trapz_weights(tt)
    T = tt[-1] - tt[0]
    Ie_m = {"avg": np.abs((Bw @ wt) / T),
            "rms": np.sqrt((Bw * Bw) @ wt / T),
            "peak": np.abs(Bw).max(axis=1)}
    Ie_m = {m: Ie_m[m] for m in METRICS}
    # the time average of |I|: a reversing current cancels in the signed one
    Ie_m["absavg"] = (np.abs(Bw) @ wt) / T

    steps, tree, unreached = spanning_order(nw, ground)
    if unreached:
        notice("EMIR-W208", f"{len(unreached)} node(s) unreachable from "
            f"{ground!r} -- the net is not connected; their IR is not solved", log)
    Ib = (Bw @ wt) / T                      # signed average branch currents
    V = node_voltages(nw, steps, Ib)
    n_off = int((~tree).sum())
    # the check is Kirchhoff's voltage law on the resistances the simulation
    # ran: with each layer scaled by its own TCR (90 C on sky130) a loop
    # through two layers (li1 and pwell in magic's network) no longer closed
    # and the check said the currents were not the DSPF's (EMIR-W405)
    R_sim = simulated_r(nw)
    V_sim = node_voltages(nw, steps, Ib, R=R_sim)
    resid = loop_residual(nw, steps, Ib, tree=tree, V=V_sim, R=R_sim)
    scale = max(np.abs(V_sim).max(), 1e-15)
    if n_off and resid > 1e-3 * scale:
        notice("EMIR-W405", f"loop residual {resid * 1e3:.4g} mV "
            f"({resid / scale:.2%} of the IR spread) -- the simulated currents "
            f"are not consistent with this DSPF. Wrong netlist/DSPF pairing?", log)
    elif not n_off:
        log("[emir] network is loop-free: no off-tree branch to cross-check "
            "the currents against, so the loop residual proves nothing here")

    dyn = None
    if dynamic:
        # running max over time, in chunks: never nodes x timesteps in memory
        worst = np.zeros(len(nw.nodes))
        t_worst = np.zeros(len(nw.nodes))
        for i in range(0, Bw.shape[1], chunk):
            Vc = node_voltages(nw, steps, Bw[:, i:i + chunk])
            av = np.abs(Vc)
            j = av.argmax(axis=1)
            best = av[np.arange(len(j)), j]
            hit = best > worst
            worst[hit] = best[hit]
            t_worst[hit] = tt[i:i + chunk][j[hit]]
        k = int(np.argmax(worst))
        dyn = {"worst": worst, "t_worst": t_worst, "nodes": nw.nodes,
               "worst_V": float(worst[k]), "worst_node": nw.nodes[k],
               "worst_t": float(t_worst[k]), "n_steps": int(Bw.shape[1]),
               "source": "RCC branch currents (measured, not modelled)"}
        log(f"[emir] dynamic IR: worst excursion {worst[k] * 1e3:.4f} mV at "
            f"{nw.nodes[k]} at t={t_worst[k]:.4g} s")

    notes = {}
    viol, max_ratio = em_clusters(nw, Ie_m, proc, lmap, notes=notes)
    if unmapped:
        notice("EMIR-W307", f"DSPF layers with no PDK match (not checked): "
            f"{', '.join(unmapped)}", log)
    note = (f"RCC branch currents read directly over {T:.4g} s"
            + (f" ({win['n_periods']} periods)" if win.get("n_periods") else "")
            + f", window sensitivity {win['sensitivity']:.2%}")
    return {"clusters": viol, "worst": viol[0] if viol else None,
            "max_ratio": max_ratio,
            "ir_drop": float(V.max() - V.min()), "V": V, "Ib": Ib, "Ie": Ie_m,
            "network": nw, "layer_map": lmap, "unmapped": unmapped,
            "r_at_temp": r_at_temp, "rscale": rscale, "metric_note": note,
            "kcl_residual": internal_kcl(nw, dspf, net, Ib, ground),
            "loop_residual": resid, "window": win, "dynamic": dyn,
            "n_offtree": n_off, "mode": "rcc", "via_cuts": notes["via_cuts"],
            "no_width": notes["no_width"], "bidirectional": notes["bidirectional"],
            "via_crowding": notes["via_crowding"]}
