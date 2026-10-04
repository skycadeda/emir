"""Big blocks (an ADC top, a PLL top): the cc-grid path at scale.

An RCC transient of a big block carries every extracted node of the rails
through every Newton iteration of every time step, and it saves one current
per resistor per step. The cc-grid route avoids both: the block is simulated
with a C-only extraction (each rail one ideal node), only the instance-pin
currents are saved, and the R-only grid is solved afterwards by superposition.
This module holds the two pieces that make that route work on big nets:

**Streaming superposition.** The exact waveform path builds a dense transfer
matrix, branches x taps, then reduces `T @ W(t)` over time. Its size is the
product: 181 k resistors x 2 000 taps is 2.7 GiB, which used to stop the run
(EMIR-E418). Above the memory budget (`--max-memory`) the run streams
instead: for each chunk of time samples it builds the injection columns
`B = P W(t)` (P scatters each tap onto its grid node), solves `G U = B` with
the grid's one LU factorisation (a multi-right-hand-side back-substitution),
forms the branch currents and accumulates their trapezoid-weighted average,
mean square and running peak. Nothing of size branches x taps or branches x
time is ever held: memory is one chunk, and the solves are one per time
sample instead of one per tap. By linearity the answer is the dense one; the
tests hold them to 1e-10 relative.

**Dynamic IR in cc-grid** (`--dynamic`). `dynir` integrates
`C dV/dt + G V = I(t)` with the tap waveforms of the CC simulation, the
DSPF's own capacitance and the decap devices the process file's
`cap_models` can size. The window the CC currents are read over is a whole
number of periods, so the RC state is taken to periodic steady state first
(`dynir.transient(periodic=True)`): a start from the DC solution would add a
start-up transient the circuit does not have. The droop is open loop: the
currents come from an ideal rail and do not react to it, so it is an upper
bound to the extent the currents are. With --self-consistent the currents
are those of the loop's reported pass (`loop_words`), each pin fed its droop
waveform -- the RC grid's when the DSPF has capacitance on the net, so the
droop here and the currents nearly agree (the loop's grid takes each
capacitor to ground and no decap device the process file sizes) -- and open
loop when the loop reported pass 0.

Both are screening, not sign-off.
"""
from __future__ import annotations

import argparse
import re

import numpy as np

from .errors import notice

#: the default --max-memory: above this the dense transfer matrix is not built
DEFAULT_MAX_MEMORY = 2 << 30

#: columns per SuperLU solve in the streamed path. On the 92 k-node mesh of
#: examples/06_big_block (results/solve_blocks.csv) a column cost 15.6 ms
#: solved alone and 6.6 ms (one BLAS thread) to 7.9 ms (the library's default
#: threads) in blocks of 8; with the default threads, blocks of 24 or more
#: columns cost 84 to 105 ms per column. 8 is safe either way.
SOLVE_BLOCK = 8

#: at most this many time samples per streamed chunk: the solves dominate and
#: are blocked anyway, so a wider chunk buys little time for a lot of memory
#: (same file, one thread: chunks of 32 and 64 samples took 3.9 and 4.5 s
#: with 0.2 and 0.4 GB of arrays, 512 samples 5.7 s with 2.1 GB)
CHUNK_MAX = 64

_SIZE = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*([kmgt]?)(i?b?)\s*$", re.I)
_UNIT = {"": 1, "k": 1 << 10, "m": 1 << 20, "g": 1 << 30, "t": 1 << 40}


def parse_size(text) -> int:
    """`2G`, `512M`, `1.5GiB`, `64k`, `4096` -> bytes (binary units).

    An argparse `type`: a value it cannot read is a usage error (EMIR-E507)."""
    m = _SIZE.match(str(text))
    if not m:
        raise argparse.ArgumentTypeError(
            f"{text!r} is not a size: write bytes, or a number with k, M, G or T "
            f"(binary: 2G = 2 GiB)")
    n = int(float(m.group(1)) * _UNIT[m.group(2).lower()])
    if n < 1 << 20:
        raise argparse.ArgumentTypeError(f"{text!r} is below 1M; give at least 1M")
    return n


def _positive_int(text) -> int:
    try:
        v = int(text)
    except ValueError:
        v = 0
    if v < 10:
        raise argparse.ArgumentTypeError(f"{text!r}: give a whole number >= 10")
    return v


# what the options read, for tools built from the parser (`emir mcp`)
parse_size.json_type, parse_size.example = "string", "4G"
_positive_int.json_type, _positive_int.example = "integer", 4000


def human(n: float) -> str:
    """Bytes as a short binary-unit string (`2.9 GiB`)."""
    for unit, k in (("GiB", 1 << 30), ("MiB", 1 << 20), ("KiB", 1 << 10)):
        if abs(n) >= k:
            return f"{n / k:.1f} {unit}"
    return f"{n:.0f} B"


def add_options(ap) -> None:
    """The run options of this module (`emir run`, `emir check`)."""
    g = ap.add_argument_group("big blocks (cc-grid at scale)")
    g.add_argument("--max-memory", type=parse_size, default=DEFAULT_MAX_MEMORY,
                   metavar="SIZE",
                   help="memory the exact waveform superposition may use (default 2G). "
                        "When the dense transfer matrix (branches x taps x 8 bytes) "
                        "would exceed it, the run streams in time chunks instead: "
                        "same answer, memory bounded by this budget. A budget above "
                        "80 %% of the physical memory is lowered to that (EMIR-W550)")
    g.add_argument("--superposition", default="auto", choices=("auto", "dense", "stream"),
                   help="how the exact waveform superposition runs: auto (default) "
                        "streams when the dense matrix exceeds --max-memory or the "
                        "result has fewer than twice as many time samples as taps, "
                        "else dense. dense is still capped by --max-memory: above it "
                        "the run streams and says so (EMIR-W452). The answer is the "
                        "same either way (to rounding); the summary's `superposition` "
                        "says which path ran")
    g.add_argument("--dynamic", action="store_true",
                   help="cc-grid: also integrate the dynamic droop (C dV/dt + G V = "
                        "I(t)) with the DSPF's capacitance and the decap devices the "
                        "process file's cap_models size; needs simulated waveforms. "
                        "rcc mode reports dynamic IR from the measured currents anyway")
    g.add_argument("--dyn-steps", type=_positive_int, default=None, metavar="N",
                   help="uniform time steps of the --dynamic transient (default: 4 per "
                        "interval of the window's samples, at least 2000, at most 200000, "
                        "a whole multiple of the intervals where that fits; refined where "
                        "the waveforms' edges or a fast decap need it)")
    from .decap import add_option
    add_option(g)


def check_options(a, rep) -> None:
    """Input-check findings for this module's options (`check.from_args`)."""
    from .decap import check_dynamic
    from .decap import check_options as basis_options
    basis_options(a, rep)
    if not getattr(a, "dynamic", False):
        return
    check_dynamic(a, rep)
    if getattr(a, "scalars", False):
        rep.fail("EMIR-E550", "options", "--dynamic integrates the tap waveforms, and "
                 "--scalars drops them", "drop --scalars (or --dynamic)")
    elif getattr(a, "currents", None) and not (a.cc_sim or a.netlist):
        rep.fail("EMIR-E550", "options", "--dynamic needs simulated waveforms; DC "
                 "--currents have no time base", "give --cc-sim with a transient result "
                 "(or drop --dynamic)")


# ------------------------------------------------------------------ superposition


def dense_bytes(n_branch: int, n_taps: int) -> int:
    """What the dense transfer matrix needs."""
    return int(n_branch) * int(n_taps) * 8


def bytes_per_sample(n_nodes: int, n_branch: int) -> int:
    """The streaming working set per time sample: the injection and node
    columns, the branch currents and the temporaries of their reduction."""
    return 8 * (4 * int(n_nodes) + 3 * int(n_branch)) + 64


def choose(n_branch: int, n_taps: int, n_points: int, max_bytes: int,
           method: str = "auto") -> tuple:
    """`(path, reason)`: "dense" or "stream" for the exact superposition.

    * the dense transfer matrix must fit `max_bytes`, else stream;
    * `auto` streams also when the result has fewer than twice as many time
      samples as taps. Dense solves once per tap, one column at a time;
      streaming once per sample, in blocks (`SOLVE_BLOCK`), which cost 2.3 to
      2.4 times less per column (results/solve_blocks.csv, 92 k nodes). Measured
      on a 23.5 k-node mesh, one thread
      (examples/06_big_block/results/stream_bench.csv): 200 taps and 2 001
      samples 4.4 s dense against 5.2 s streamed; 1 000 taps and 2 001
      samples 12.4 s against 7.8 s;
    * `dense` / `stream` force a path (dense still only within the budget).
    """
    need = dense_bytes(n_branch, n_taps)
    if need > max_bytes:
        return "stream", (f"the dense transfer matrix would need {human(need)} "
                          f"({n_branch} branches x {n_taps} taps), above --max-memory "
                          f"{human(max_bytes)}")
    if method == "stream":
        return "stream", "--superposition stream"
    if method == "auto" and n_points < 2 * n_taps:
        return "stream", (f"{n_points} time samples < 2 x {n_taps} taps: streaming "
                          f"(one blocked solve per sample) is the cheaper path")
    return "dense", ("--superposition dense" if method == "dense" else
                     f"the transfer matrix fits ({human(need)}) and {n_points} time "
                     f"samples >= 2 x {n_taps} taps")


def physical_memory() -> int | None:
    """This machine's physical memory in bytes, or None where it cannot be read."""
    import os
    try:
        return int(os.sysconf("SC_PAGE_SIZE")) * int(os.sysconf("SC_PHYS_PAGES"))
    except (ValueError, OSError, AttributeError):
        return None


#: the share of the physical memory a --max-memory budget may take
PHYSICAL_SHARE = 0.8


def memory_budget(max_bytes, log=print, physical=None) -> int:
    """`--max-memory` as the run may use it: lowered to `PHYSICAL_SHARE` of
    the physical memory when it asks for more (EMIR-W550). A budget the
    machine cannot hold ended in the kernel's OOM kill (exit 137, no error
    document) instead of the streamed path."""
    max_bytes = int(max_bytes or DEFAULT_MAX_MEMORY)
    phys = physical_memory() if physical is None else physical
    if phys and max_bytes > PHYSICAL_SHARE * phys:
        cap = int(PHYSICAL_SHARE * phys)
        notice("EMIR-W550", f"--max-memory {human(max_bytes)} is more than this machine's "
               f"{human(phys)} of physical memory allows: the budget is {human(cap)} "
               f"({PHYSICAL_SHARE:.0%})", log)
        return cap
    return max_bytes


def superpose(nw, ground, branch_fn, taps, metrics, weights=None,
              max_bytes=None, method="auto", log=print) -> tuple:
    """Exact per-metric branch currents from waveform taps, dense or streamed
    (`choose`). Returns `(Ie_m, note, info)`; `info` says which path ran and
    why (the run summary carries it as `superposition`)."""
    from .taps import superpose_waveforms
    max_bytes = memory_budget(max_bytes, log)
    waved = [t for t in taps if t.get("wave") is not None]
    nb, nt = len(nw.res), len(waved[0]["wave"])
    path, why = choose(nb, len(waved), nt, max_bytes, method or "auto")
    if method == "dense" and path == "stream":
        notice("EMIR-W452", f"net {nw.net!r}: --superposition dense was asked, but {why}: "
               f"streamed instead (the same answer)", log)
    info = {"method": path, "reason": why, "taps": len(waved), "points": nt,
            "branches": nb, "nodes": len(nw.nodes),
            "dense_bytes": dense_bytes(nb, len(waved)), "budget_bytes": max_bytes,
            "chunk": None, "chunks": None}
    if path == "dense":
        # the matrix plus the chunk's temporaries (T @ W, + DC, I^2, |I|: four
        # branches-long arrays per sample) inside the budget
        c = int(max(1, min(nt, (max_bytes - info["dense_bytes"]) // (32 * max(nb, 1)))))
        Ie_m, note = superpose_waveforms(branch_fn, taps, nb, metrics, weights=weights,
                                         max_bytes=max_bytes, chunk=c, log=log)
        info.update(chunk=c, chunks=-(-nt // c))
        return Ie_m, note, info
    notice("EMIR-I450", f"net {nw.net!r}: superposition streamed in time chunks: {why} "
           f"(same answer)", log)
    Ie_m, note, sinfo = superpose_stream(nw, ground, taps, metrics, weights=weights,
                                         max_bytes=max_bytes, log=log)
    info.update(sinfo)
    return Ie_m, note, info


def _check_waves(waved, weights):
    from .errors import EmirError
    nt = len(waved[0]["wave"])
    for t in waved:
        if len(t["wave"]) != nt:
            raise EmirError("EMIR-E905", f"tap {t['name']}: waveform length "
                            f"{len(t['wave'])} != {nt}; all taps must share one time base")
    if weights is None:
        return nt, np.full(nt, 1.0 / nt), "uniform time weights"
    weights = np.asarray(weights, dtype=float)
    if len(weights) != nt:
        raise EmirError("EMIR-E905", f"weights length {len(weights)} != waveform length {nt}")
    if abs(weights.sum() - 1.0) > 1e-6:
        raise EmirError("EMIR-E905", f"time weights sum to {weights.sum():.6g}, not 1 "
                        f"-- they must normalise the window")
    return nt, weights, "trapezoid time weights"


def solve_columns(nw, ground: str, B: np.ndarray) -> np.ndarray:
    """Branch currents for several injection columns at once.

    `B` is (nodes x k), amps into each node of `nw`; returns (branches x k).
    The same rules as `Network.solve`, per column: the reference's piece is
    solved; another piece (only a run forced past EMIR-E207 has one) only
    where the current injected into it balances, its branches are NaN in the
    other columns (a current with nowhere to go has no answer)."""
    pieces = nw._factor(ground)
    n, k = B.shape
    U = np.zeros((n, k))
    bad_branch = None
    for main, members, fi, lu in pieces:
        if lu is not None and len(fi):
            # SuperLU's multi-column solve is fastest in small blocks
            # (`SOLVE_BLOCK`)
            for j in range(0, k, SOLVE_BLOCK):
                U[fi, j:j + SOLVE_BLOCK] = lu.solve(B[fi, j:j + SOLVE_BLOCK])
        if main:
            continue
        tot = B[members].sum(axis=0)
        scale = np.maximum(np.abs(B).max(axis=0), 1e-30)
        bad_cols = ~np.isfinite(tot) | (np.abs(tot) > 1e-9 * scale)
        if bad_cols.any():
            if bad_branch is None:
                bad_branch = np.zeros((len(nw.g), k), bool)
            inside = np.isin(nw.a, members)
            bad_branch[np.ix_(inside, bad_cols)] = True
    Ib = U[nw.a]
    Ib -= U[nw.b]
    Ib *= nw.g[:, None]
    if bad_branch is not None:
        Ib[bad_branch] = np.nan
    return Ib


def superpose_stream(nw, ground, taps, metrics, weights=None, max_bytes=None,
                     log=print) -> tuple:
    """The exact waveform superposition, streamed over time.

    For each chunk of `c` samples: `B = P W[:, chunk] + b0` (P scatters the
    taps onto their nodes, b0 the DC taps), branch currents from one
    multi-column back-substitution (`solve_columns`), then the weighted sums
    of I and I^2 and the running max of |I|. Returns `(Ie_m, note, info)`."""
    import scipy.sparse as sp
    max_bytes = int(max_bytes or DEFAULT_MAX_MEMORY)
    waved = [t for t in taps if t.get("wave") is not None]
    nt, weights, wnote = _check_waves(waved, weights)
    n, nb = len(nw.nodes), len(nw.res)
    for t in taps:
        if t["node"] not in nw.idx:
            from .errors import EmirError
            raise EmirError("EMIR-E206", f"injection node {t['node']!r} not on net "
                            f"{nw.net!r}", net=nw.net)
    rows = np.array([nw.idx[t["node"]] for t in waved])
    P = sp.csr_matrix((np.ones(len(waved)), (rows, np.arange(len(waved)))),
                      shape=(n, len(waved)))
    W = np.array([t["wave"] for t in waved])            # taps x time (the input)
    b0 = np.zeros(n)
    scalars = [t for t in taps if t.get("wave") is None]
    for t in scalars:                                   # DC taps ride along
        b0[nw.idx[t["node"]]] += t["i_avg"]
    per = bytes_per_sample(n, nb)
    c = int(max(1, min(nt, CHUNK_MAX, max_bytes // per)))
    s1 = np.zeros(nb)
    s2 = np.zeros(nb)
    sa = np.zeros(nb)
    pk = np.zeros(nb)
    n_chunks = 0
    for i in range(0, nt, c):
        B = np.asarray(P @ W[:, i:i + c])
        B += b0[:, None]
        Ie = solve_columns(nw, ground, B)
        del B
        wc = weights[i:i + c]
        s1 += Ie @ wc
        s2 += (Ie * Ie) @ wc
        np.abs(Ie, out=Ie)
        sa += Ie @ wc
        np.maximum(pk, Ie.max(axis=1), out=pk)
        n_chunks += 1
    nw.n_solves += nt
    out = {}
    if "avg" in metrics:
        out["avg"] = np.abs(s1)
        out["absavg"] = sa            # the time average of |I| (not a checked metric)
    if "rms" in metrics:
        out["rms"] = np.sqrt(np.maximum(s2, 0.0))
    if "peak" in metrics:
        out["peak"] = pk
    note = (f"exact from {nt}-point waveforms on {len(waved)} taps, {wnote}"
            + (f" (+{len(scalars)} DC taps)" if scalars else "")
            + f"; streamed in {n_chunks} time chunk(s) of up to {c} samples")
    log(f"[emir] superposition: {note} ({nt} multi-column back-substitutions, "
        f"working set about {human(per * c)})")
    return out, note, {"method": "stream", "chunk": c, "chunks": n_chunks,
                       "working_bytes": per * c}


# ------------------------------------------------------------------ dynamic IR


def cc_dynamic(d, net: str, r: dict, ground: str, taps, time, proc, a,
               log=print) -> dict | None:
    """Dynamic IR of one cc-grid net, or None (with a coded notice saying why
    it was not computed). `r` is `check_net`'s result, `taps`/`time` the
    ingest's waveforms and window. The same steps give the net's decap-aware
    EM, and the verdict's basis is swapped into `r` (`decap.solve`)."""
    from .decap import not_computed, solve
    why = None
    if time is None or not any(t.get("wave") is not None for t in taps or []):
        why = ("harmonic", "the result carries no time-domain waveforms (a harmonic PSS "
                           "result)")
    elif r.get("unsolved"):
        why = ("pieces", "the net is in pieces (forced past EMIR-E207)")
    if why is None:
        from .dspf import mos_decaps
        n_caps = len(d.net_caps(net)) + len(mos_decaps(d, net))
        if not n_caps:
            why = ("no-capacitance", "the DSPF has no capacitance on this net (an R-only "
                   "extraction carries none): give an RC extraction of the rails, or decap "
                   "devices the process file's cap_models can size")
    if why is not None:
        notice("EMIR-W450", f"net {net!r}: dynamic IR not computed: {why[1]}", log)
        r["net"] = net
        not_computed(r, a, *why, log=log)
        return None
    from .decap import mark_unsized
    from .errors import EmirError
    try:
        dyn = solve(d, net, r, ground, taps, time, proc, a, log=log)
    except EmirError as e:
        if e.code != "EMIR-E423":
            raise
        # caps were listed but none is on a solved node, or none could be sized
        notice("EMIR-W450", f"net {net!r}: dynamic IR not computed: {e.message}", log)
        not_computed(r, a, "no-capacitance", e.message, log=log)
        mark_unsized(r, d, net, proc)
        return None
    # decaps cap_models cannot size were left out: their straps are not
    # checked, and a basis that needs decap-aware EM is not honoured there
    mark_unsized(r, d, net, proc)
    if not dyn.get("settled", True):
        notice("EMIR-W451", f"net {net!r}: the RC state did not reach periodic steady "
               f"state in {dyn['passes']} passes over the window (slowest decap time "
               f"constant long against the window); the droop still holds part of the "
               f"start-up from the DC solution. Simulate more periods", log)
    dyn["source"] = dynamic_source(r.get("self_consistency"))
    return dyn


def dynamic_entry(r: dict) -> dict | None:
    """The run summary's `dynamic` object for one net (rcc or cc-grid)."""
    dy = r.get("dynamic")
    if not dy:
        return None
    nw = r.get("network")
    node = dy.get("worst_node")
    xy = None
    if nw is not None and node is not None:
        xy = nw.dspf.node_xy.get(node)
    return {"worst_mV": float(dy["worst_V"]) * 1e3, "node": node,
            "x_um": float(xy[0]) if xy else None, "y_um": float(xy[1]) if xy else None,
            "t_s": float(dy["worst_t"]), "source": dy.get("source"),
            "n_steps": dy.get("n_steps"), "step_s": dy.get("h"),
            "peak_retention": dy.get("peak_retention"),
            "c_total_F": dy.get("c_total"), "decap_lumps": dy.get("n_lumps"),
            "periodic": dy.get("periodic"), "passes": dy.get("passes"),
            "settled": dy.get("settled"), "cap_note": dy.get("cap_note"),
            "decap_note": dy.get("decap_note")}


def loop_pass(sc: dict | None) -> int | None:
    """The self-consistent pass whose currents a cc-grid run's numbers (its
    dynamic droop included) come from: None for the open loop -- a run
    without --self-consistent, or a loop that reported pass 0 (diverging, at
    its pass limit, a pass's simulation stopped; or converged at pass 0,
    where the open loop's currents already produce the droop they ran at)."""
    k = (sc or {}).get("reported_pass")
    return k if isinstance(k, int) and not isinstance(k, bool) and k > 0 else None


def loop_words(sc: dict | None, short: bool = False) -> str:
    """Where a cc-grid dynamic droop's currents come from, against the
    supply feedback: "open loop", or the self-consistent loop's reported
    pass, whose every pin was fed its droop waveform."""
    k = loop_pass(sc)
    if k is None:
        return "open loop"
    if short:
        return f"pass {k} of the self-consistent loop"
    return (f"the currents of pass {k} of the self-consistent loop, each pin fed its droop "
            f"waveform")


def dynamic_source(sc: dict | None) -> str:
    """The `dynamic.source` of a cc-grid --dynamic run."""
    if loop_pass(sc) is None:
        if (sc or {}).get("converged"):
            return ("cc-grid: CC tap waveforms through the RC grid (open loop, which the "
                    "self-consistent loop found self-consistent at pass 0)")
        return ("cc-grid: CC tap waveforms through the RC grid "
                "(open loop: an upper bound to the extent the currents are)")
    return f"cc-grid: CC tap waveforms through the RC grid ({loop_words(sc)})"


def print_dynamic(dy: dict, ir_drop: float, sc: dict | None = None) -> None:
    """The console line of a cc-grid dynamic IR (the rcc branch prints its own);
    `sc`: the net's self-consistency, when the loop ran."""
    from .dspf import _dyn_ratio
    extra = (f"; periodic steady state after {dy['passes']} pass(es)"
             if dy.get("periodic") else "")
    print(f"  dynamic IR     : {dy['worst_V'] * 1e3:.4f} mV at {dy['worst_node']} at "
          f"t={dy['worst_t']:.4g} s{_dyn_ratio(dy['worst_V'], ir_drop)}  "
          f"({loop_words(sc, short=True)}, {dy['n_steps']} steps{extra})")
    if dy.get("em_line"):
        print(dy["em_line"])
