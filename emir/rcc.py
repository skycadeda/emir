"""rcc: simulate the RC netlist and read the currents off it.

Every resistor in the extracted network is a real element in the simulation,
so its current is read from the simulation rather than reconstructed, and
every metric (avg, rms and peak EM, static and dynamic IR) is checked:

    save Rw1:1 Rw2:1 ...        # generated from the DSPF
    -> avg / rms / peak per branch, over a whole-period window
    -> the same EM verdict function every other path uses

**Voltages come free.** Given branch currents and the network, node potentials
follow by walking a spanning tree from the reference node and accumulating
`I*R` -- so RCC mode needs only the branch-current saves, and static *and*
dynamic IR both fall out. `R` is what the simulation ran, the DSPF's own
resistance: the IR is the simulated circuit's, whatever temperature the EM
limits are taken at (`r_basis`; EMIR-I407 when the two differ). Better, the branches *not* on the tree are then a
check nothing else provides: each one independently predicts the same potential
difference, and `loop_residual` reports the worst disagreement. A netlist paired
with the wrong DSPF, a missing save, or a flipped sign shows up there
immediately, where a grid solve would have quietly produced a plausible number.

The cost is the RC transient itself: every resistor is an element of the
simulation and its current one signal per time step, so on a big block it is
slow and may converge poorly. Measured at the original development site on a
generated test block -- a bank of inverters tapping a segmented metal supply
rail -- refining the rail's segmentation:

| rail nodes | RCC |
|---|---|
| 41 | 1.92 s |
| 521 | 3.83 s |
| 2057 | 9.53 s |
"""
from __future__ import annotations

import numpy as np

from . import waveform, waves
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


def r_basis(dspf, proc) -> dict:
    """The resistances rcc's IR is computed with: those the simulation ran,
    the DSPF's, at the temperature it states (`*|OPERATING_TEMPERATURE`),
    else the process file's `temp_reference` (`stated` False)."""
    stated = dspf.temperature is not None
    return {"kind": "simulated", "stated": stated,
            "temp_C": float(dspf.temperature if stated else proc.temp_ref)}


def simulated_r(nw) -> np.ndarray:
    """Each branch's resistance as the DSPF gives it: what the simulation
    ran, before any temperature scaling (a `Network` built with `rscale`
    scales `g` per layer; rcc's is built without)."""
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


def branch_currents(dspf, net: str, w) -> tuple:
    """`(network, B)`: the net's resistor network as the simulation ran it (the
    DSPF's resistances) and every branch current (branches x time) from the
    result `w`; EMIR-E415 when one was not saved."""
    nw = Network(dspf, net)
    names = [r["name"] for r in nw.res]
    found = w.match([f"{n}:1" for n in names])
    missing = [n for n in names if f"{n}:1" not in found]
    if missing:
        raise EmirError("EMIR-E415",
            f"{len(missing)} of {len(names)} branch currents were not saved "
            f"(e.g. {', '.join(missing[:4])}). Add:\n{save_branches(dspf, net)}", net=net)
    return nw, np.array([np.asarray(w[found[f"{n}:1"]]) for n in names])


def net_window(dspf, net: str, w, period=None, log=print, ctx=None, nw=None,
               B=None) -> dict | None:
    """The window of one net (`waveform.window`): the record's last whole
    periods of `period`, up to three, its last two compared on the branches
    that matter. None for an operating point (one time point). Refusals are
    EMIR-E428, E429 and E431."""
    if len(w.x) < 2:
        return None
    if nw is None or B is None:
        nw, B = branch_currents(dspf, net, w)
    return waveform.window(w.x, B, period, names=[r["name"] for r in nw.res], net=net,
                           log=log, ctx=ctx)


def net_windows(dspf, nets, w, period=None, log=print, ctx=None) -> dict:
    """`{net: window}` for every net of a run, before any number is printed:
    the record's refusal (EMIR-E428, E431) first, whatever the net; then a
    run whose last two periods differ on some net (EMIR-E429) is refused
    whole, with every refused net in `details.nets`. An operating point has
    no window (None)."""
    if len(w.x) < 2:
        return {n: None for n in nets}
    waveform.record_window(w.x, period, ctx)
    out, refused = {}, []
    for net in nets:
        nw, B = branch_currents(dspf, net, w)
        try:
            out[net] = net_window(dspf, net, w, period, log, ctx, nw=nw, B=B)
        except EmirError as e:
            refused.append(e)
    if refused:
        e = refused[0]
        e.details["nets"] = [{"net": x.details.get("net"), "code": x.code,
                              "message": x.message} for x in refused]
        if len(refused) > 1:
            e.message += f" ({len(refused) - 1} other net(s) refused too: details.nets)"
        raise e
    return out


def rcc_check(dspf, net: str, proc, result, ground: str, analysis=None,
              period=None, window_sel=None, explicit_layers=None, dynamic=True,
              chunk=4096, log=print) -> dict:
    """EM + IR on one net from an RCC simulation, in `check_net`'s shape.

    Same return contract as the DC path so reporting, the CLI and the
    viewers consume either without caring which produced it.

    `result` is a result path, or a `waves.Waves` already read (so a run over
    many nets reads the file once).
    """
    from .engine import METRICS
    lmap = map_layers(dspf, proc, explicit_layers, log=log)
    unmapped = [t for t in dspf.layer_tokens() if t not in lmap]
    r_at_temp, _ = net_scaling(dspf, proc, lmap)
    basis = r_basis(dspf, proc)
    if not r_at_temp:
        src = "" if basis["stated"] else ", the process file's temp_reference"
        notice("EMIR-I407", f"net {net}: the IR is the simulated circuit's, from the "
               f"resistances the simulation ran (the DSPF's, at {basis['temp_C']:g} C"
               f"{src}); the EM "
               f"limits are at {proc.temp:g} C. For IR at {proc.temp:g} C, extract or "
               f"simulate at {proc.temp:g} C", log)

    w = waves.load(result, analysis=analysis)
    log(f"[emir] {w.summary()}")
    if w.is_complex:
        raise EmirError("EMIR-E414", "RCC mode needs a time-domain result (tran or PSS td);"
                         " harmonics carry no branch-by-branch time base here")
    # the network the simulation ran: the DSPF's resistances, unscaled. Its
    # IR once used each wire scaled by its layer's TCR to the analysis
    # temperature, while the currents came from the unscaled circuit: on
    # example 08 (DSPF at 30 C, EM at 90 C) the IR read 2.7 % above the
    # simulator's own node voltages
    nw, B = branch_currents(dspf, net, w)

    # the window: the record's last whole periods of --period, or the run is
    # refused (`waveform.window`); an operating point (one time point) has none
    win = (window_sel if window_sel is not None or len(w.x) < 2 else
           net_window(dspf, net, w, period, log, nw=nw, B=B))
    if win is None:
        tt, Bw = w.x[:1], B[:, :1]
        Ib = Bw[:, 0]                       # the operating point: avg = rms = peak
        Ie_m = {m: np.abs(Ib) for m in ("avg", "rms", "peak")}
    else:
        tt, Bw = waveform.clip_window(w.x, B, win["t0"], win["t1"])
        wt = waveform.trapz_weights(tt)
        T = tt[-1] - tt[0]
        Ib = (Bw @ wt) / T                  # signed average branch currents
        Ie_m = {"avg": np.abs(Ib), "rms": np.sqrt((Bw * Bw) @ wt / T),
                "peak": np.abs(Bw).max(axis=1)}
    Ie_m = {m: Ie_m[m] for m in METRICS}
    # the time average of |I|: a reversing current cancels in the signed one
    Ie_m["absavg"] = np.abs(Ib) if win is None else (np.abs(Bw) @ wt) / T

    steps, tree, unreached = spanning_order(nw, ground)
    if unreached:
        notice("EMIR-W208", f"{len(unreached)} node(s) unreachable from "
            f"{ground!r} -- the net is not connected; their IR is not solved", log)
    # Kirchhoff's voltage law on the resistances the simulation ran (the
    # network's: unscaled): with each layer scaled by its own TCR (90 C on
    # sky130) a loop through two layers (li1 and pwell in magic's network) no
    # longer closed and the check said the currents were not the DSPF's
    # (EMIR-W405)
    R_sim = simulated_r(nw)
    V = node_voltages(nw, steps, Ib, R=R_sim)
    n_off = int((~tree).sum())
    resid = loop_residual(nw, steps, Ib, tree=tree, V=V, R=R_sim)
    scale = max(np.abs(V).max(), 1e-15)
    if n_off and resid > 1e-3 * scale:
        notice("EMIR-W405", f"loop residual {resid * 1e3:.4g} mV "
            f"({resid / scale:.2%} of the IR spread) -- the simulated currents "
            f"are not consistent with this DSPF. Wrong netlist/DSPF pairing?", log)
    elif not n_off:
        log("[emir] network is loop-free: no off-tree branch to cross-check "
            "the currents against, so the loop residual proves nothing here")

    dyn = None
    if dynamic and not np.all(np.isfinite(Bw)):
        # a current that is not a number (past EMIR-E426 with --force or
        # --no-check): the worst excursion is not a number either. Taking the
        # max over the finite rest skipped the nodes behind it and printed a
        # smaller worst as if it were the net's
        log("[emir] dynamic IR: not a number (a branch current in the window is not "
            "finite)")
    elif dynamic:
        # running max over time, in chunks: never nodes x timesteps in memory
        worst = np.zeros(len(nw.nodes))
        t_worst = np.zeros(len(nw.nodes))
        for i in range(0, Bw.shape[1], chunk):
            Vc = node_voltages(nw, steps, Bw[:, i:i + chunk], R=R_sim)
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
               "source": "rcc: the simulated branch currents and the resistances the "
                         "simulation ran"}
        log(f"[emir] dynamic IR: worst excursion {worst[k] * 1e3:.4f} mV at "
            f"{nw.nodes[k]} at t={t_worst[k]:.4g} s")

    notes = {}
    viol, max_ratio = em_clusters(nw, Ie_m, proc, lmap, notes=notes)
    if unmapped:
        notice("EMIR-W307", f"DSPF layers with no PDK match (not checked): "
            f"{', '.join(unmapped)}", log)
    note = ("RCC branch currents read directly at the operating point (one time point: "
            "avg = rms = peak)" if win is None else
            f"RCC branch currents read directly over {T:.4g} s, the record's last "
            f"{win['n_periods']} whole periods of {win['period']:.4g} s")
    return {"clusters": viol, "worst": viol[0] if viol else None,
            "max_ratio": max_ratio,
            "ir_drop": float(V.max() - V.min()), "V": V, "Ib": Ib, "Ie": Ie_m,
            "network": nw, "layer_map": lmap, "unmapped": unmapped,
            "r_at_temp": r_at_temp, "rscale": 1.0, "r_basis": basis,
            "metric_note": note,
            "kcl_residual": internal_kcl(nw, dspf, net, Ib, ground),
            "loop_residual": resid, "window": win, "dynamic": dyn,
            "n_offtree": n_off, "mode": "rcc", "via_cuts": notes["via_cuts"],
            "no_width": notes["no_width"], "bidirectional": notes["bidirectional"],
            "via_crowding": notes["via_crowding"]}
