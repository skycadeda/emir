"""The waveform self-consistent loop of `emir run --mode cc-grid --self-consistent`.

**What it corrects.** A CC simulation runs every device on an ideal rail. On
an extracted grid each device's pin moves: its own contact resistor
degenerates its source, its neighbours switching in the same phase pull the
shared grid down, and the idle devices beside it give back charge (an
inverter whose output is high is its load capacitor behind its ON PMOS: a
decap). So each device draws a lower, wider current spike than the CC
simulation says, with nearly the same charge. Measured on the synthetic
inverter meshes of `examples/06_big_block` (`results/selfconsist_waveform.md`):
the open loop read the governing branch's rms +7.7 % and peak +16.6 % high at
0.47 % droop, and +34 % / +70 % at 4.1 %.

**The loop.** A fixed point, pass by pass, with the window chosen at pass 0
and held:

1. Pass 0 is the open loop exactly: every pin's split source at 0 V.
2. Each pass's pin current waveforms (every pin on the net, idle ones
   included) go through the grid over the whole transient: the droop
   waveform each pin would see. The grid is the DSPF's: its resistors, and
   its capacitance when the DSPF has some on the net (an RC extraction; the
   grid is then stepped by backward Euler, `NetGrid`), each capacitor to
   ground (a coupling capacitor's far plate taken as ground), with the
   decaps the process file's cap_models size as --dynamic models them
   (capacitor devices from their geometry; each MOS decap a node of its own
   behind its access resistance, `grid_decaps`; one they cannot size left
   out, EMIR-W547: the devices then read low and its straps are not checked;
   one the testbench simulates loads the grid through its pin instead, and
   the loop converges slower). With no
   capacitance in the DSPF the feedback is the R-only grid's, and the run
   says so (EMIR-W544): a real grid's capacitance gives charge locally that
   an R-only grid does not, so its droop at the pins is deeper than the
   real one, the devices' peaks read low, and so do the branches that
   capacitance would feed (measured: `results/selfconsist_waveform.md`).
   The time base is uniform (2 steps per open-loop time point) with a point
   moved onto each corner of the stimuli (`base_grid`: a spike peaks there,
   and a grid that stepped over it fed a shallower dip, so the device's peak
   read high); the simulator's other points are not added (femtosecond
   features fed back make the loop unstable).
3. The residual is the largest difference, over the pins and the base,
   between that droop and the droop the pass simulated (the PWL as written).
   The loop stops when it is below max(--tol-rel x the largest droop,
   --tol-mV) (1 % and 0.2 mV by default) and the governing branch's avg, rms
   and peak each moved less than 0.5 % from the pass before (`stop_rule:
   residual`). Or it stops `settled` (`stop_rule: metrics`): on two passes in
   a row the governing branch, every item the run reports (an EM ratio at
   or above the process file's warning fraction) and every device's own
   avg, rms and peak current each moved less than 0.5 %, with the residual
   under 5 % of the droop, not grown by more than 10 % on either pass, and
   the PWL as fine as the loop's tolerance. That pass is reported; a loop
   that settles leaves the residual above its tolerance, and the summary
   says by how much.
4. Otherwise the next droop is fed back as a PWL source per pin
   (`emir.pwl`, one shared time grid, compressed at 0.3 x the loop's
   tolerance: where the loop gain is above 1 the compression's error comes
   back amplified, so it must stay below the tolerance the residual meets):
   after pass 0 the plain solve (step 2), scaled down when its largest droop
   is above 15 % of the supply (the open loop's droop overstates the real
   one, about twice at 4 % droop, and a block with state -- a latch, a
   regulator -- can change state under a dip it would never see); after
   that the **companion** solve: each pin gets the linear admittance g + C
   d/dt of its device, fitted from the last two passes (the secant of
   current against droop, per pin, clipped at >= 0), and the grid is solved
   by backward Euler with those admittances in it, from the operating point
   at t = 0::

       (G + Cg/dt + diag(g + C/dt)) v_m = -(i_m - g f_m - C (f_m - f_{m-1})/dt)
                                          + (C/dt) v_{m-1} + (Cg/dt) v_{m-1}

   (Cg the grid's own capacitance, when the DSPF has some, on each actual
   step of the base). At the fixed point (v = f) the companion terms
   cancel, so the answer is still the grid with self-consistent currents;
   the companion only changes the path, like a diagonal preconditioner (in
   PyTorch terms, it changes the step, not the minimum), so its own terms
   take the nominal step. The fit is
   kept (frozen) once the residual is within twice the tolerance, or when a
   new fit explains less than 30 % of the current change and less than the
   kept one did.

**Why the companion.** The loop gain is the grid's impedance at the pins
times the devices' admittance. Idle loads act as decap behind the grid's
resistance, so on a high-droop mesh the gain is above 1 at the edge rate: the
plain update rings and diverges (measured on the 4.1 % mesh), like a
regulator with too little phase margin. The companion solve damps exactly
that.

**The guard.** Two growths of the residual in a row, or a droop to feed back
above half the supply (when no net's supply is known: above three times the
open loop's droop), stop the loop, and pass 0, the open loop, is reported
(EMIR-W406); so is a loop that reaches --max-passes unsettled (EMIR-W407),
and a later pass whose simulation runs past its time limit or fails
(EMIR-W543: a diverging droop makes a simulator crawl or fail, and so can a
long block simply be slow; the limit is max(20 x pass 0's time, 60 s,
--sim-timeout)). Never a pass between.

**KCL, every pass.** Where the driver saves the rail source's current, the
sum of the split pins' currents is checked against it (EMIR-W541 when a
share of the rail's current reaches the rail some other way: a terminal
the split does not move, a testbench load).

**Several nets.** Each net's grid is solved on its own, every net's droop
is fed back on every pass (one simulation per pass), and each pin has its
own companion. The loop has converged when every net has.

**Memory.** Each pass holds its pins' currents on the simulator's time
points, and the pass before's (the fit): 2 x pins x points x 8 bytes, plus
the result the simulator wrote. The grid side streams in time chunks of the
base (`CHUNK_MAX` samples, or fewer under --max-memory). The droop fed back
is held as its PWL points only.
"""
from __future__ import annotations

import time

import numpy as np

from . import ccsim, pwl, waves
from .errors import EmirError, notice

#: simulations the loop runs at most, pass 0 included
MAX_PASSES = 10
#: converged when the residual is below max(TOL_REL x the largest droop, TOL_MV)
TOL_MV = 0.2
TOL_REL = 0.01
#: ... and the governing avg, rms and peak each moved less than this (relative);
#: or those, every reported item and every device moved less than it on two
#: passes in a row with the residual below SETTLE_FRACTION of the largest droop
#: (the metric rule)
METRIC_TOL = 0.005
SETTLE_FRACTION = 0.05
#: a device is in the metric rule when its peak current is at least this share
#: of the largest device's
DEVICE_FLOOR = 0.01
#: the grid side's uniform time step: the transient over STEPS_PER_POINT x the
#: open loop's time points, within [MIN_STEPS, MAX_STEPS]
STEPS_PER_POINT = 2
MIN_STEPS = 2000
MAX_STEPS = 200_000
#: the companion fit is kept once the residual is below this many tolerances
#: (a fit to so small a change chases noise), or when a new fit explains less
#: than FIT_R2_MIN of the current change and less than the kept fit did
FREEZE_TOL = 2.0
FIT_R2_MIN = 0.3
#: a pass after pass 0 may take this many times pass 0's simulation, at least
#: PASS_TIME_MIN_S, and at least --sim-timeout when the person gave it (pass 0
#: itself: --sim-timeout, SIM_TIMEOUT_S by default)
PASS_TIME_FACTOR = 20.0
PASS_TIME_MIN_S = 60.0
SIM_TIMEOUT_S = 3600.0
#: the first update (the plain solve of the open loop's currents) feeds at most
#: this share of the supply: larger, it is scaled down to it
FIRST_STEP_FRACTION = 0.15
#: the guard where no net's supply is known (a ground net alone, its rail read
#: as 0 V): a droop above this many times the open loop's
GUARD_OPEN_LOOP = 3.0
#: PWL points a pin may take: max(pwl.MAX_POINTS, this x pass 0's time points)
PWL_POINTS_PER_POINT = 0.3
#: a simulator point is a corner (a breakpoint: a stimulus's corner) when the
#: step before the step into it is this many times the step into it and the
#: step out of it
CORNER_RATIO = 8.0
#: a residual "grew" when it is above this factor of the pass before's
GROWTH = 1.1
#: KCL of the split: the window average within this share of the rail's,
#: every instant within KCL_INSTANT of the rail's peak
KCL_AVG = 1e-5
KCL_INSTANT = 1e-4
#: time samples per chunk of the grid side
CHUNK_MAX = 4096


def _quiet(*_a, **_k):
    pass


# ------------------------------------------------------------------ the grid


def grid_caps(dspf, net: str, nw, proc=None) -> tuple:
    """The DSPF's capacitance on `net`, per node of `nw` (F, over
    `nw.nodes`), each capacitor to ground (a coupling capacitor's far plate
    taken as ground, as the dynamic IR takes it), and `{"n", "total_F",
    "unsized"}`. A capacitor device with no value (a decap cell, a MIM) is
    sized from its geometry by the process file's `cap_models`, as --dynamic
    sizes it (`dynir.device_cap`); one they cannot size is counted in
    `unsized` and left out (EMIR-W547)."""
    from .dynir import device_cap
    models = getattr(proc, "cap_models", {}) or {}
    C = np.zeros(len(nw.nodes))
    n = unsized = 0
    for c in dspf.net_caps(net):
        i = nw.idx.get(c["node"])
        if i is None:
            continue
        val = c.get("c")
        if val is None:
            val = device_cap(c, models)[0]
            if val is None:
                unsized += 1
                continue
        C[i] += float(val)
        n += 1
    return C, {"n": n, "total_F": float(C.sum()), "unsized": unsized}


def grid_decaps(dspf, net: str, nw, proc, simulated=()) -> tuple:
    """The MOS decaps on `net` the loop's grid holds, as --dynamic models
    them (`decap.mos_lumps`: C = cox W L behind its access resistance from
    `cap_models`, split at both channel ends where they are separate nodes
    of the net): `(lumps, {"n", "total_F", "unsized", "simulated"})`.

    A decap's capacitance gives charge at its node as the grid's own does,
    so a loop whose grid left it out feeds back a droop deeper than the real
    one, and its devices read low (measured with the 8 decaps of a synthetic
    4 % droop mesh left out: the devices' peaks down to -5.87 % against RCC,
    examples/13_decap_aware/results/decap_loop.md). A decap the process file
    cannot size (no `cap_models` entry, a fit negative at its geometry) is
    counted in `unsized` and left out (EMIR-W547). A decap whose device the
    testbench simulates (one of its pins on the net is fed by the split and
    its current is in the result: `simulated`, its name) is left out too:
    its current already loads the grid through its pin."""
    from .decap import mos_lumps
    from .dspf import mos_decaps
    from .errors import collecting
    decs = mos_decaps(dspf, net)
    info = {"n": 0, "total_F": 0.0, "unsized": 0, "simulated": 0, "names_simulated": []}
    if not decs:
        return [], info
    sim = {m["name"] for m in decs if m["name"] in set(simulated)}
    try:
        # their notices (W408, W409) are --dynamic's to give: the loop's is W547
        with collecting(propagate=False):
            lumps, _note, _two = mos_lumps(dspf, net, nw, proc, log=_quiet)
    except EmirError as e:
        if e.code != "EMIR-E317":
            raise
        lumps = []
    sized = {L["name"] for L in lumps}
    keep = [L for L in lumps if L["name"] not in sim and L.get("c", 0.0) > 0]
    info.update(n=len({L["name"] for L in keep}), total_F=float(sum(L["c"] for L in keep)),
                unsized=sum(1 for m in decs if m["name"] not in sized and m["name"] not in sim),
                simulated=len(sim), names_simulated=sorted(sim))
    return keep, info


#: an off-nominal step of the RC grid is solved by conjugate gradients
#: preconditioned with the nominal step's LU (`_Stepper._solve`) to this
#: relative residual, in at most CG_MAX iterations (the preconditioned
#: operator's eigenvalues lie between 1 and dt/h: a few iterations)
CG_TOL = 1e-12
CG_MAX = 60


class _Stepper:
    """Backward Euler of one net's grid: the grid's own capacitance (when it
    has some) on each actual step of the base, and each pin's companion
    admittance g + C d/dt (zero for the plain map) on the nominal step `dt`
    (its terms cancel at the fixed point whatever the step)."""

    def __init__(self, grid, dt: float, g=None, C=None):
        import scipy.sparse as sp
        from scipy.sparse.linalg import splu
        P = len(grid.pins)
        self.grid, self.dt = grid, float(dt)
        self.g = np.zeros(P) if g is None else np.asarray(g, float)
        self.C = np.zeros(P) if C is None else np.asarray(C, float)
        self.comp = bool(self.g.any() or self.C.any())
        if not grid.n:
            return
        d = grid.S @ (self.g + self.C / self.dt)
        if grid.Cn is not None:
            d = d + grid.Cn / self.dt
        self.A = (grid.G + sp.diags(d)).tocsc()
        self.lu = splu(self.A)
        # the first step is the operating point: no current through any C
        self.lu0 = (splu((grid.G + sp.diags(grid.S @ self.g)).tocsc()) if self.g.any()
                    else grid.lu)

    def _solve(self, b, h: float) -> np.ndarray:
        """x of (A + Cg (1/h - 1/dt)) x = b: the step `h` of the RC grid, the
        nominal step's matrix A plus a diagonal. On the nominal step the LU
        alone; off it (a point of the base moved onto a stimulus's corner, or
        put in for a second corner), conjugate gradients preconditioned with
        that LU (A and the diagonal are symmetric positive definite)."""
        Cn, dt = self.grid.Cn, self.dt
        if Cn is None or abs(h - dt) <= 1e-9 * dt:
            return self.lu.solve(b)
        dd = Cn * (1.0 / h - 1.0 / dt)

        def A(v):
            return self.A @ v + dd * v
        x = self.lu.solve(b)
        r = b - A(x)
        bn = max(float(np.linalg.norm(b)), 1e-300)
        z = self.lu.solve(r)
        p, rz = z.copy(), float(r @ z)
        for _ in range(CG_MAX):
            if float(np.linalg.norm(r)) <= CG_TOL * bn or rz <= 0:
                break
            Ap = A(p)
            alpha = rz / float(p @ Ap)
            x = x + alpha * p
            r = r - alpha * Ap
            z = self.lu.solve(r)
            rz_new = float(r @ z)
            p, rz = z + (rz_new / rz) * p, rz_new
        return x

    def chunk(self, Iu, Fu, state: dict, h=None) -> np.ndarray:
        """The droop (pins x samples) over one chunk: `Iu` the currents, `Fu`
        the droop the pass simulated (the companion's; None for the plain
        map), `h` the step before each sample (None: the nominal `dt`; the
        grid's capacitance takes it, the companion the nominal `dt`); `state`
        carries the step before across chunks (empty before the first: that
        step is the operating point, as the simulator's transient starts from
        one)."""
        grid = self.grid
        P, c = Iu.shape
        out = np.zeros((P, c))
        if not grid.n:
            return out
        g, C, dt, Cn, S = self.g, self.C, self.dt, grid.Cn, grid.S
        x_prev, v_prev, f_prev = state.get("x"), state.get("v"), state.get("f")
        for m in range(c):
            f = Fu[:, m] if Fu is not None else None
            inj = -Iu[:, m]
            if x_prev is None:
                if self.comp:
                    inj = inj + g * f
                x = self.lu0.solve(np.asarray(S @ inj))
            else:
                if self.comp:
                    inj = inj + g * f + C * (f - f_prev) / dt + (C / dt) * v_prev
                b = np.asarray(S @ inj)
                if Cn is not None:
                    hm = dt if h is None else float(h[m])
                    x = self._solve(b + (Cn / hm) * x_prev, hm)
                else:
                    x = self.lu.solve(b)
            v = np.zeros(P)
            v[grid.valid] = x[grid.rows[grid.valid]]
            out[:, m] = v
            x_prev, v_prev, f_prev = x, v, f
        state.update(x=x_prev, v=v_prev, f=f_prev)
        return out


class NetGrid:
    """One net's grid as the loop solves it.

    The feed is the reference (0 V): a pin's potential is its droop. Only the
    feed's piece is solved (a pin on another piece, or on the feed, keeps 0 V).
    `pins` are every DSPF instance pin of the net (their currents all load
    the grid); `fed` marks the ones whose droop is fed back. `caps`: the
    grid's capacitance per node of `nw` (`grid_caps`), None or all zero for
    the R-only grid. `lumps`: the decaps (`grid_decaps`), each a node of its
    own behind its access resistance with its capacitance to ground (a
    lump off the feed's piece is left out, as that piece is)."""

    def __init__(self, nw, feed: str, pins: list, node_of: dict, fed=None, caps=None,
                 lumps=None):
        import scipy.sparse as sp
        from scipy.sparse.linalg import splu
        pieces = nw._factor(feed)
        _main, _members, fi, lu = next(p for p in pieces if p[0])
        self.n = len(fi)
        self.lu = lu
        n_all = len(nw.nodes)
        pos = -np.ones(n_all, dtype=int)
        pos[fi] = np.arange(self.n)
        self.pins = list(pins)
        self.rows = np.array([pos[nw.idx[node_of[p]]] for p in pins], dtype=int)
        self.valid = self.rows >= 0
        P = len(pins)
        v = np.flatnonzero(self.valid)
        G = sp.coo_matrix(
            (np.concatenate([nw.g, nw.g, -nw.g, -nw.g]),
             (np.concatenate([nw.a, nw.b, nw.a, nw.b]),
              np.concatenate([nw.a, nw.b, nw.b, nw.a]))), shape=(n_all, n_all)).tocsr()
        self.G = G[fi][:, fi].tocsc()
        self.fed = np.ones(P, bool) if fed is None else np.asarray(fed, bool)
        Cn = None if caps is None else np.asarray(caps, float)[fi]
        # the decaps: a node each, after the grid's, joined to its grid node
        # by its access conductance, its capacitance to ground
        at = [(int(pos[nw.idx[L["node"]]]), float(L["c"]), max(float(L["r"]), 1e-12))
              for L in (lumps or ()) if L["node"] in nw.idx and pos[nw.idx[L["node"]]] >= 0
              and L.get("c", 0.0) > 0]
        self.lumps = len(at)
        if at and self.n:
            n0, k = self.n, len(at)
            r = np.array([a[0] for a in at])
            q = np.arange(n0, n0 + k)
            gl = np.array([1.0 / a[2] for a in at])
            ext = sp.coo_matrix(
                (np.concatenate([gl, gl, -gl, -gl]),
                 (np.concatenate([r, q, r, q]), np.concatenate([r, q, q, r]))),
                shape=(n0 + k, n0 + k))
            self.G = (sp.block_diag([self.G, sp.csc_matrix((k, k))]) + ext).tocsc()
            Cn = np.concatenate([Cn if Cn is not None else np.zeros(n0),
                                 np.array([a[1] for a in at])])
            self.n = n0 + k
            # the operating point and the R-only map: a decap's node carries
            # no DC current, so it sits at its grid node's potential
            self.lu = splu(self.G)
        # scatter pins -> nodes
        self.S = sp.csr_matrix((np.ones(len(v)), (self.rows[v], v)), shape=(self.n, P))
        self.Cn = Cn if Cn is not None and Cn.any() else None
        self._plain = None
        self._clu = None

    @property
    def rc(self) -> bool:
        """The grid has capacitance: the droop is a backward-Euler transient."""
        return self.Cn is not None

    def _gather(self, X) -> np.ndarray:
        out = np.zeros((len(self.pins),) + X.shape[1:])
        out[self.valid] = X[self.rows[self.valid]]
        return out

    def plain(self, I) -> np.ndarray:
        """The droop (V, pins x samples) the currents `I` (A into each
        device, pins x samples) produce through the R-only grid (the grid's
        resistors alone, whatever its capacitance)."""
        I = np.atleast_2d(I)
        if not self.n:
            return np.zeros_like(I)
        B = -(self.S @ I)
        return self._gather(np.atleast_2d(self.lu.solve(np.asarray(B))).reshape(self.n, -1))

    def plain_factor(self, dt: float):
        """The plain map's stepper, factored on the nominal step `dt` (the RC
        grid; an off-nominal step of the base is solved from it)."""
        if self.rc:
            self._plain = _Stepper(self, dt)

    def map_chunk(self, Iu, state: dict, h=None) -> np.ndarray:
        """The droop the currents `Iu` produce: the R-only solve, or on an RC
        grid one chunk of its backward-Euler transient (`state` across
        chunks, `h` the step before each sample, `steps`; `plain_factor`
        first)."""
        if not self.rc:
            return self.plain(Iu)
        return self._plain.chunk(Iu, None, state, h)

    def companion_factor(self, g, C, dt: float):
        """Factor G (+ Cg/dt) + diag(g + C/dt) (the admittances summed on
        their nodes), and G + diag(g) for the first step (the operating
        point: no current through any C)."""
        self._comp = _Stepper(self, dt, g, C)
        self.g, self.C, self.dt = self._comp.g, self._comp.C, self._comp.dt
        if self.n:
            self._clu, self._clu0 = self._comp.lu, self._comp.lu0

    def companion_chunk(self, Iu, Fu, state, h=None) -> np.ndarray:
        """Backward-Euler steps over one chunk: `Iu`, `Fu` (pins x samples)
        the currents and the droop the pass simulated, on the time base (the
        companion's terms on the nominal `dt`: it only shapes the path; the
        grid's capacitance on each actual step `h`, `steps`); `state` carries
        the step before across chunks (empty, or None entries, before the
        first: that step is the operating point). Returns the new droop
        (pins x samples)."""
        state = state if state is not None else {}
        if state.get("v") is None:
            state.pop("x", None)
        return self._comp.chunk(Iu, Fu, state, h)


def corners_of(ts) -> np.ndarray:
    """The breakpoints of a simulation, from its time points: the simulator
    lands on a stimulus's corner with a step cut short and restarts after it
    with a tiny, still shrinking step (a fallback where the stimuli are not
    read: `driver.corners()` gives them exactly)."""
    ts = np.asarray(ts, dtype=float)
    if len(ts) < 6:
        return np.zeros(0)
    h = np.diff(ts)
    i = np.arange(3, len(h) - 2)
    ref = np.maximum(h[i - 3], h[i - 2])
    ok = ((h[i - 1] < 0.5 * ref) & (CORNER_RATIO * h[i] < ref) &
          (h[i] >= h[i + 1]) & (h[i + 1] >= h[i + 2] * 0.999))
    return ts[i[ok]]


def base_grid(tu, ts, corners=None) -> np.ndarray:
    """The time base the droop is solved and fed on: the uniform grid `tu`
    with the point nearest each corner of the stimuli (`corners`, else found
    in the simulation's points `ts`, `corners_of`) moved onto it.

    A spike peaks on a stimulus's corner (the input ramp's end), and a
    uniform grid would step over it: a shallower dip is fed at the device's
    own peak, which then reads high (in the development of this loop, about
    1 % on the 10 x 10 mesh, in the periods whose corner the grid missed;
    measured then, not committed). The simulator's other points
    are not added: they resolve femtosecond steps (a gate-overlap current's
    onset), and fed back those make the loop gain the grid's resistance
    times the device's capacitance at THz: unstable. So the base stays about
    uniform, its spacing at least half a step."""
    tu = np.asarray(tu, dtype=float)
    if len(tu) < 3:
        return tu.copy()
    dt = tu[1] - tu[0]
    corners = corners_of(ts) if corners is None else np.asarray(corners, dtype=float)
    corners = np.unique(corners[(corners > tu[0]) & (corners < tu[-1])])
    # a corner closer than half a step to the one before is below the base's
    # resolution: the first is kept (half a step itself is resolved, whatever
    # the rounding of the difference)
    keep, last = [], -np.inf
    for c in corners:
        if c - last >= dt / 2 * (1 - 1e-9):
            keep.append(c)
            last = c
    corners = np.array(keep)
    j = np.clip(np.rint((corners - tu[0]) / dt).astype(int), 0, len(tu) - 1)
    # the nearest point is within half a step by construction; the slack is
    # for a corner half way between two points, whose rounding put it a hair
    # beyond (such a corner was once dropped: its device's peak read high)
    ok = (j > 0) & (j < len(tu) - 1) & (np.abs(tu[j] - corners) <= dt * (0.5 + 1e-6))
    tb = tu.copy()
    # the first corner nearest a point of the base moves it; another corner
    # nearest the same point (two corners less than a step apart, at least
    # half a step) is put in beside it: never dropped. Each lies within half
    # a step of that point, so at least half a step from its neighbours
    j, c = j[ok], corners[ok]
    first = np.ones(len(j), bool)
    first[1:] = j[1:] != j[:-1]
    tb[j[first]] = c[first]
    return np.unique(np.concatenate([tb, c[~first]]))


def steps(tb, s: int, c: int, dt: float) -> np.ndarray:
    """The step before each sample of the base's chunk `tb[s:s + c]` (the
    base's first sample: `dt`, unused -- that step is the operating point)."""
    if s:
        return np.diff(tb[s - 1:s + c])
    return np.concatenate([[dt], np.diff(tb[:c])])


# ------------------------------------------------------------------ the fit


class FitSums:
    """The per-pin normal equations of di ~ g dv + C d(dv)/dt, summed over
    the transient chunk by chunk (dv: the droop simulated this pass minus
    the pass before; di: the current, likewise)."""

    def __init__(self, P: int):
        self.s = {k: np.zeros(P) for k in ("vv", "vd", "dd", "vy", "dy", "yy")}
        self.last = None

    def add(self, dv, di, h):
        """One chunk: `dv`, `di` (pins x samples), `h` the step before each
        sample (the first sample's derivative is 0)."""
        prev = dv[:, :1] if self.last is None else self.last[:, None]
        dd = np.diff(np.concatenate([prev, dv], axis=1), axis=1) / np.maximum(h, 1e-30)
        self.last = dv[:, -1].copy()
        s = self.s
        s["vv"] += (dv * dv).sum(1)
        s["vd"] += (dv * dd).sum(1)
        s["dd"] += (dd * dd).sum(1)
        s["vy"] += (dv * di).sum(1)
        s["dy"] += (dd * di).sum(1)
        s["yy"] += (di * di).sum(1)

    def solve(self) -> tuple:
        """(g, C, r2) per pin; g, C >= 0 (a term that fits negative is dropped
        and the other refitted alone)."""
        s = self.s
        P = len(s["vv"])
        g, C, r2 = np.zeros(P), np.zeros(P), np.zeros(P)
        for p in range(P):
            A = np.array([[s["vv"][p], s["vd"][p]], [s["vd"][p], s["dd"][p]]])
            b = np.array([s["vy"][p], s["dy"][p]])
            yy = s["yy"][p]
            co = None
            if np.linalg.det(A) > 1e-12 * max(A[0, 0] * A[1, 1], 1e-300):
                co = np.linalg.solve(A, b)
            if co is None or co[0] < 0 or co[1] < 0:
                best = (yy, 0.0, 0.0)                       # (sse, g, C)
                for j, (xx, xy) in enumerate(((s["vv"][p], s["vy"][p]),
                                              (s["dd"][p], s["dy"][p]))):
                    c = xy / xx if xx > 0 else 0.0
                    if c > 0:
                        sse = yy - 2 * c * xy + c * c * xx
                        if sse < best[0]:
                            best = (sse, c, 0.0) if j == 0 else (sse, 0.0, c)
                co = np.array(best[1:])
            g[p], C[p] = co
            sse = yy - 2 * (co[0] * b[0] + co[1] * b[1]) + co @ A @ co
            r2[p] = 1.0 - sse / yy if yy > 0 else 1.0
        return g, C, r2


# ------------------------------------------------------------------ the pass data


def _pin_currents(w, pins: list) -> tuple:
    """(I pins x samples, A into each device; the found signals by pin):
    a pin the result does not hold draws nothing (EMIR-W403 is the ingest's)."""
    found = w.match(pins)
    I = np.zeros((len(pins), len(w.x)))
    for j, p in enumerate(pins):
        if p in found:
            I[j] = -ccsim._tap_current(w, found[p])
    return I, found


def _chunks(n: int, rows: int, max_bytes: int | None) -> int:
    per = 8 * max(rows, 1) * 8
    c = CHUNK_MAX if not max_bytes else int(max(64, min(CHUNK_MAX, max_bytes // per)))
    return max(1, min(c, n))


def _rel(a, b, floor: float = 0.0) -> float:
    """The largest relative move of `a` from `b` (arrays), 0 for none. An
    entry below `floor` moves relative to `floor`: a capacitor's average
    current (a decap the testbench simulates, its strap) is about zero, and
    its relative move is noise, not a sign that the answer still moves."""
    a, b = np.abs(np.asarray(a, float)), np.abs(np.asarray(b, float))
    if not len(a):
        return 0.0
    if not (np.isfinite(a).all() and np.isfinite(b).all()):
        return float("inf")
    return float((np.abs(a - b) / np.maximum(b, max(floor, 1e-30))).max())


def _metrics_move(res, prev, ing=None, ing_prev=None) -> dict | None:
    """How far the answer moved from the pass before (relative): the
    governing branch's avg, rms and peak (the largest of that metric this
    pass); `items`, the largest move of any item the run reports (its EM
    ratio at or above the warning fraction) in any metric; `devices`, the
    largest move of any device's own avg, rms or peak current (devices at
    least DEVICE_FLOOR of the largest's peak); `branches`, of any branch
    carrying at least DEVICE_FLOOR of the largest (reported only). An item's
    or a device's metric far below the largest's moves relative to
    DEVICE_FLOOR of the largest (`_rel`'s floor)."""
    if prev is None:
        return None
    out = {}
    for m in ("avg", "rms", "peak"):
        a, b = np.abs(res["Ie"][m]), np.abs(prev["Ie"][m])
        if not len(a) or not np.isfinite(a).all():
            out[m] = None
            continue
        g = int(np.argmax(a))
        out[m] = float(abs(a[g] - b[g]) / max(abs(b[g]), 1e-30))
    # the items either pass reports, by their branch: one item crossing the
    # warning fraction between passes is compared like the others
    # (each metric relative to at least DEVICE_FLOOR of the largest branch's:
    # a strap's average current is about zero)
    names = {c["name"] for c in (res.get("clusters") or []) + (prev.get("clusters") or [])}
    idx = [i for i, r in enumerate(res["network"].res) if r["name"] in names]
    out["items"] = (max(_rel(np.asarray(res["Ie"][m])[idx], np.asarray(prev["Ie"][m])[idx],
                             DEVICE_FLOOR * _largest(prev["Ie"][m]))
                        for m in ("avg", "rms", "peak")) if idx else 0.0)
    # every branch carrying at least DEVICE_FLOOR of the largest (minor ones
    # too): reported, not a stopping condition (an item below the warning
    # fraction does not change a verdict)
    mv = 0.0
    for m in ("avg", "rms", "peak"):
        a, b = np.abs(np.asarray(res["Ie"][m])), np.abs(np.asarray(prev["Ie"][m]))
        if len(a) and np.isfinite(a).all():
            sel = a >= DEVICE_FLOOR * a.max()
            mv = max(mv, _rel(a[sel], b[sel]))
    out["branches"] = mv
    out["devices"] = None
    if ing is not None and ing_prev is not None:
        now = {t["name"]: t for t in ing["taps"] if not t.get("feed")}
        was = {t["name"]: t for t in ing_prev["taps"] if not t.get("feed")}
        names = [n for n in now if n in was]
        if names:
            pk = np.array([abs(now[n]["i_peak"]) for n in names])
            keep = [n for n, p in zip(names, pk) if p >= DEVICE_FLOOR * pk.max()]
            # each metric relative to at least DEVICE_FLOOR of the largest
            # device's: a decap the testbench simulates draws about no average
            out["devices"] = max(_rel([now[n][f"i_{m}"] for n in keep],
                                      [was[n][f"i_{m}"] for n in keep],
                                      DEVICE_FLOOR * _largest([was[n][f"i_{m}"]
                                                               for n in keep]))
                                 for m in ("avg", "rms", "peak"))
    return out


def _largest(v) -> float:
    """The largest magnitude in `v` (0 for none, or not a number)."""
    v = np.abs(np.asarray(v, float))
    return float(v.max()) if len(v) and np.isfinite(v).all() else 0.0


def _still(moved) -> bool:
    """The governing branch moved less than METRIC_TOL in each metric."""
    return moved is not None and all(moved.get(m) is not None and moved[m] < METRIC_TOL
                                     for m in ("avg", "rms", "peak"))


def _all_still(moved) -> bool:
    """`_still`, and every reported item and every device too."""
    return _still(moved) and all(moved.get(k) is None or moved[k] < METRIC_TOL
                                 for k in ("items", "devices"))


def stop_rule(resid: float, tol: float, largest: float, moved, still_before: bool,
              hist_mV: list, coarsened: bool) -> tuple:
    """`(stop, rule, still)` for one net's pass: the residual rule (`resid`
    under `tol`, the governing branch still), or the metric rule (`settled`:
    the governing branch, every reported item and every device still on this
    pass and the one before -- `still_before` --, the residual under
    SETTLE_FRACTION of the droop, not grown by more than GROWTH on either of
    the last two passes -- `hist_mV`, the residuals so far with this one --,
    and the PWL the pass simulated not coarsened past the tolerance by the
    cap). `still`: this pass's half of the metric rule, for the next."""
    still = _all_still(moved)
    not_growing = (len(hist_mV) >= 3 and hist_mV[-1] <= GROWTH * hist_mV[-2]
                   and hist_mV[-2] <= GROWTH * hist_mV[-3])
    by_residual = resid < tol and (moved is None or _still(moved))
    settled = (still and still_before and not_growing and not coarsened
               and resid < SETTLE_FRACTION * max(largest, 1e-30))
    if by_residual:
        return True, "residual", still
    if settled:
        return True, "metrics", still
    return False, None, still


def kcl(w, window: dict, pin_signals: list, source: dict | None) -> dict | None:
    """The split's KCL over the window: the sum of the split pins' currents
    (A into the devices) against the rail source's (`source`: {"signal",
    "sign"}, sign +1 when the rail is the source's + node). None when the
    rail source's current is not in the result."""
    if not source or not pin_signals:
        return None
    m = w.match([source["signal"]])
    if not m:
        return None
    src = -source["sign"] * np.asarray(w[m[source["signal"]]], dtype=float)
    pins = np.sum([np.asarray(w[s], dtype=float) for s in pin_signals], axis=0)
    tt, Y = ccsim.clip_window(w.x, np.vstack([pins, src]), window["t0"], window["t1"])
    wt = ccsim.trapz_weights(tt) / (tt[-1] - tt[0])
    a_pins, a_src = float(Y[0] @ wt), float(Y[1] @ wt)
    scale = max(abs(a_src), 1e-30)
    peak = max(float(np.abs(Y[1]).max()), 1e-30)
    return {"source": source.get("name"), "pins_avg_A": a_pins, "source_avg_A": a_src,
            "avg_rel": abs(a_pins - a_src) / scale,
            "instant_rel": float(np.abs(Y[0] - Y[1]).max()) / peak}


#: a load's current "rose with the droop" when pass 1 drew this much more than
#: pass 0 (relative), fed a droop
RISE = 1e-3


def open_loop_reads(passes: list) -> dict:
    """Which way the open loop (pass 0) reads, as far as the loop measured
    it: `{"reads": "high" | "low" | None, "droop_mV", "avg_mA": [pass 0,
    pass 1], "worst": [pass 0, pass 1]}`. Pass 1 fed the droop the open
    loop's currents produce: when it drew more current than pass 0 (a load
    whose current rises as its supply falls: a constant-power load, a
    regulator), the open loop's currents are LOW, not conservative, and that
    is what makes such a loop diverge; when it drew less, the open loop reads
    high, as on every mesh measured. None when no pass fed a droop back.
    `passes`: one net's pass records (the summary's or the loop's)."""
    out = {"reads": None, "droop_mV": None, "avg_mA": None, "worst": None}
    if len(passes) < 2:
        return out
    p0, p1 = passes[0], passes[1]
    a0, a1 = p0.get("total_avg_A"), p1.get("total_avg_A")
    fed = p1.get("simulated_droop_mV")
    if a0 is None or a1 is None or not fed:
        return out
    out.update(droop_mV=fed, avg_mA=[abs(a0) * 1e3, abs(a1) * 1e3],
               worst=[p0.get("worst_ratio"), p1.get("worst_ratio")])
    rel = (abs(a1) - abs(a0)) / max(abs(a0), 1e-30)
    w0, w1 = p0.get("worst_ratio"), p1.get("worst_ratio")
    rose = rel > RISE or (w0 and w1 and w1 > (1 + RISE) * w0 and rel >= 0)
    out["reads"] = "low" if rose else "high"
    return out


def reads_text(net: str, how: dict) -> str:
    """One sentence for a loop that reports pass 0: which way the open loop
    reads, as `open_loop_reads` measured it."""
    if how.get("reads") == "low":
        a0, a1 = how["avg_mA"]
        return (f"{net}'s open loop reads LOW here, not conservative: fed "
                f"{how['droop_mV']:.4g} mV of droop on pass 1, the block drew {a1:.4g} mA "
                f"against {a0:.4g} mA open loop, a load whose current rises as its supply "
                f"falls")
    if how.get("reads") == "high":
        a0, a1 = how["avg_mA"]
        return (f"{net}'s open loop reads high here: fed {how['droop_mV']:.4g} mV of droop "
                f"on pass 1, the block drew {a1:.4g} mA against {a0:.4g} mA open loop")
    return (f"no pass fed {net}'s droop back, so whether its open loop reads high or low "
            f"here was not measured")


def pass_timeout(k: int, t0_pass: float | None, sim_timeout: float | None) -> float:
    """The time limit of pass `k`'s simulation: pass 0 --sim-timeout
    (`sim_timeout`; SIM_TIMEOUT_S when None); a later pass max(PASS_TIME_FACTOR
    x pass 0's time, PASS_TIME_MIN_S, --sim-timeout when given)."""
    if k == 0 or t0_pass is None:
        return float(sim_timeout or SIM_TIMEOUT_S)
    return float(max(PASS_TIME_FACTOR * t0_pass, PASS_TIME_MIN_S, sim_timeout or 0.0))


# ------------------------------------------------------------------ the loop


#: the simulator failures of a later pass that stop the loop (pass 0 reported)
#: instead of the run: past its time limit, failed, no result written
STOP_CODES = {"EMIR-E605": "time limit", "EMIR-E602": "simulation failed",
              "EMIR-E661": "simulation failed"}


def run(dspf, nets, proc, feeds: dict, vdds: dict, *, driver=None, simulate=None,
        netlist=None, period=None, max_passes: int = MAX_PASSES, tol_mV: float = TOL_MV,
        tol_rel: float = TOL_REL, analysis=None, waveforms=True, on_pass=None,
        max_bytes=None, superposition="auto", sim_timeout: float | None = None,
        guard_V: float | None = None, companion: bool = True, log=print) -> dict:
    """The waveform loop on `nets` (one or several): see the module doc.

    `driver.run(k, fed, timeout=)` simulates pass `k` with the droop `fed`
    (`{"t": times or None, "v": {pin: values}}`; None times: every droop 0 V,
    the open loop) and returns `{"raw", "seconds"}`; `driver.pins(net)` are
    the pins it can feed (the split's), `driver.kcl_source(net)` the rail
    source to check KCL against. `companion` False takes the plain update
    on every pass (how the guard is tested: it diverges where the loop gain
    is above 1). `simulate(netlist, k, fed)` replaces the
    driver (tests, `emir validate`): it returns `{"raw"}` or `{"waves"}` (a
    `waves.Waves`) and `seconds`, and every pin is fed. `sim_timeout`: pass
    0's time limit (None: SIM_TIMEOUT_S), and the least a later pass gets
    (`pass_timeout`). `guard_V`: the largest droop that may be fed back
    (default half the largest of `vdds`; none known: GUARD_OPEN_LOOP x the
    open loop's droop).

    Returns `{"nets": {net: per-net result}, "passes", "converged",
    "diverged", "stopped", "reported_pass", ...}`; each per-net result has
    the keys the single-net `selfconsist.converge` returns."""
    from .dspf import check_net
    nets = [nets] if isinstance(nets, str) else list(nets)
    for net in nets:
        if not dspf.net_taps(net):
            raise EmirError("EMIR-E211", f"no instance pins on net {net!r}", net=net)
    pins = {n: sorted(dspf.pin_groups(n)) for n in nets}
    node_of = {n: {p: v[0]["node"] for p, v in dspf.pin_groups(n).items()} for n in nets}
    if simulate is None:
        fed_pins = {n: set(driver.pins(n)) for n in nets}
    else:
        fed_pins = {n: set(pins[n]) for n in nets}
    span = max((abs(v) for v in vdds.values() if v is not None), default=0.0)
    guard = guard_V if guard_V is not None else (span / 2 if span > 0 else None)
    # the first update's largest droop: a share of the supply (of the guard's
    # supply when only the guard is given)
    first_cap = (FIRST_STEP_FRACTION * span if span > 0 else
                 2 * FIRST_STEP_FRACTION * guard_V if guard_V else None)
    sim_name = getattr(driver, "sim", None)

    passes, per = [], {n: [] for n in nets}
    state = {n: {} for n in nets}               # per-net loop state
    fed = {"t": None, "v": {p: 0.0 for n in nets for p in sorted(fed_pins[n])}}
    first = keep = None
    converged = diverged = False
    stopped = "pass limit"
    ws0, grids, tu, tb, dt, t0_pass = {}, {}, None, None, None, None
    pwl_cap = pwl.MAX_POINTS
    feedback, sim_decaps = {}, {}
    t_post = time.perf_counter()
    for k in range(max_passes):
        # ------------------------------------------------ simulate
        timeout = pass_timeout(k, t0_pass, sim_timeout)
        try:
            if simulate is not None:
                sim = simulate(netlist, k, fed)
            else:
                sim = driver.run(k, fed, timeout=timeout)
        except EmirError as e:
            if k >= 1 and e.code in STOP_CODES:
                stopped = STOP_CODES[e.code]
                slow = e.code == "EMIR-E605"
                # one line: the simulator's own output (many lines, '***' and
                # '|' rules) is in its log, not in a notice or a report's list
                line1 = next((ln.strip() for ln in str(e.message).splitlines()
                              if ln.strip()), "")
                what = (f"ran past its time limit ({timeout:.4g} s: max({PASS_TIME_FACTOR:g} x "
                        f"pass 0's {t0_pass or 0:.4g} s, {PASS_TIME_MIN_S:g} s"
                        + (f", --sim-timeout {sim_timeout:g} s" if sim_timeout else "") + "))"
                        if slow else f"failed ({e.code}: {line1.rstrip(':')})")
                where = ((getattr(e, "ctx", None) or {}).get("path")
                         or (getattr(e, "details", None) or {}).get("path"))
                notice("EMIR-W543", f"self-consistency stopped: pass {k}'s simulation {what}; "
                       f"pass 0 (the open loop) is reported. "
                       + ("A diverging droop makes a simulator crawl, but a long pass may "
                          "also be just slow" if slow else
                          "A diverging droop can make a simulator fail")
                       + ": the passes so far are in the summary"
                       + (f"; the simulator's log: {where}" if where else ""), log)
                keep = first
                break
            raise
        if k == 0:
            t0_pass = float(sim.get("seconds") or 0.0)
        t_post = time.perf_counter()
        w = sim["waves"] if sim.get("waves") is not None else waves.load(
            sim["raw"], analysis=analysis)
        if w.is_complex:
            raise EmirError("EMIR-E550", "the self-consistent loop needs a transient result "
                            "(time-domain currents), not harmonics", net=nets[0])
        rec = {"pass": k, "seconds": float(sim.get("seconds") or 0.0),
               "points": int(len(w.x)), "pwl_points": fed.get("points"),
               "pwl_tol_mV": fed.get("tol_mV"), "fed_by": fed.get("by", "open loop"),
               "fed_scale": fed.get("scale"), "raw": sim.get("raw"), "nets": {}}
        if k == 0:
            # the grid side's two time bases, fixed at pass 0: a uniform one
            # for the companion's backward-Euler steps (and the fit), and the
            # base where the droop is solved, compared and fed (`base_grid`)
            tstop, tstart = float(w.x[-1]), float(w.x[0])
            if tstart > 0:
                notice("EMIR-W546", f"the transient is saved from {tstart:.4g} s, not from 0: "
                       f"the loop knows no currents before it, and a PWL source holds its "
                       f"first value from 0, so every pass runs [0, {tstart:.4g}] s with the "
                       f"droop of {tstart:.4g} s held, not its own (the IR and the currents "
                       f"can read low); save the transient from 0 (no tstart on .tran)", log)
            n_steps = int(np.clip(STEPS_PER_POINT * len(w.x), MIN_STEPS, MAX_STEPS))
            tu = np.linspace(tstart, tstop, n_steps + 1)
            dt = float(tu[1] - tu[0])
            tb = base_grid(tu, w.x, corners=(driver.corners() if simulate is None and
                                              hasattr(driver, "corners") else None))
            pwl_cap = max(pwl.MAX_POINTS, int(PWL_POINTS_PER_POINT * len(w.x)))
        # ------------------------------------------------ every net: score, solve
        all_ok, grew = True, False
        for net in nets:
            st = state[net]
            feed = feeds[net]
            if k == 0:
                ing = ccsim.cc_taps(dspf, net, w, analysis=analysis, period=period,
                                    feed=feed, waveforms=waveforms, log=log)
                ws0[net] = ing["window"]
            else:
                ing = ccsim.taps_from_waves(dspf, net, w, window_sel=ws0[net], feed=feed,
                                            waveforms=waveforms, log=log)
            inj = {}
            for t in ing["taps"]:
                inj[t["node"]] = inj.get(t["node"], 0.0) + t["i_avg"]
            res = check_net(dspf, net, proc, inj, feed, taps_list=ing["taps"],
                            tap_weights=ing["weights"], network=st.get("nw"), log=log,
                            max_bytes=max_bytes, superposition=superposition)
            st["nw"] = res["network"]
            if k == 0:
                caps, info = grid_caps(dspf, net, res["network"], proc)
                # a decap whose device the testbench simulates (a pin of it fed
                # and in the result) loads the grid through that pin already
                hit = w.match(sorted(fed_pins[net]))
                simulated = {p.rsplit(":", 1)[0] for p in hit}
                lumps, dinfo = grid_decaps(dspf, net, res["network"], proc, simulated)
                grids[net] = NetGrid(res["network"], feed, pins[net], node_of[net],
                                     fed=[p in fed_pins[net] for p in pins[net]], caps=caps,
                                     lumps=lumps)
                grids[net].plain_factor(dt)
                feedback[net] = {"grid": "RC" if grids[net].rc else "R-only",
                                 "caps": info["n"], "caps_F": info["total_F"],
                                 "unsized_caps": info["unsized"],
                                 "decaps": dinfo["n"], "decaps_F": dinfo["total_F"],
                                 "unsized_decaps": dinfo["unsized"],
                                 "simulated_decaps": dinfo["simulated"]}
                sim_decaps[net] = dinfo["names_simulated"]
                if grids[net].rc:
                    log(f"[emir] {net}: the droop fed back is the RC grid's ({info['n']} "
                        f"capacitors, {info['total_F'] * 1e15:.4g} fF, each to ground"
                        + (f"; {dinfo['n']} decap(s), {dinfo['total_F'] * 1e12:.4g} pF, "
                           f"each behind its access resistance" if dinfo["n"] else "")
                        + ")")
                if not info["n"]:
                    notice("EMIR-W544", f"net {net!r}: the DSPF has no capacitance on the "
                           f"net" + (f" (only the {dinfo['n']} decap(s) the process file "
                                     f"sizes)" if dinfo["n"] else "")
                           + ", so the droop fed back is the R-only grid's"
                           + (" with those decaps" if dinfo["n"] else "")
                           + ": deeper at the pins than a grid with capacitance would give, "
                           "so the devices' peaks and the branches that capacitance would "
                           "feed read low (see the code's meaning for the measured size); "
                           "give the RC extraction of the rail to feed back the RC grid's "
                           "droop", log)
                left = info["unsized"] + dinfo["unsized"]
                if left:
                    notice("EMIR-W547", f"net {net!r}: {left} decap device(s) on the net "
                           f"(capacitor devices or MOS decaps) are left out of the grid the "
                           f"droop is fed back from: the process file's cap_models cannot "
                           f"size them. The droop fed back is then deeper than the real "
                           f"one: the devices' currents read LOW (not conservative), the "
                           f"governing branch high, and those decaps' straps are not "
                           f"checked; add their cap_models entries (`emir process check "
                           f"--dspf`)", log)
            grid = grids[net]
            I, found = _pin_currents(w, pins[net])
            Fk = st.get("fed")                  # (t, F pins x len(t)) simulated, or None
            # sweep 1: the grid's droop for this pass's currents against what
            # the pass simulated (the PWL as written)
            c = _chunks(len(tb), 4 * len(pins[net]) + grid.n, max_bytes)
            resid = largest = 0.0
            m = grid.fed
            mstate = {}
            for s in range(0, len(tb), c):
                tc = tb[s:s + c]
                Vp = grid.map_chunk(pwl.interp_rows(tc, w.x, I), mstate,
                                    steps(tb, s, c, dt))
                Au = pwl.interp_rows(tc, *Fk) if Fk is not None else np.zeros_like(Vp)
                if m.any():
                    resid = max(resid, float(np.abs(Vp[m] - Au[m]).max()))
                    largest = max(largest, float(np.abs(Vp[m]).max()))
            # the companion's fit: this pass against the one before
            fit, hist = None, st.get("hist")
            if k >= 1 and hist is not None:
                fit = FitSums(len(pins[net]))
                for s in range(0, len(tb), c):
                    tc = tb[s:s + c]
                    h = np.diff(tb[max(s - 1, 0):s + c]) if s else \
                        np.concatenate([[0.0], np.diff(tc)])
                    Fu = pwl.interp_rows(tc, *Fk) if Fk is not None else 0.0
                    Fp = pwl.interp_rows(tc, *hist["fed"]) if hist["fed"] is not None else 0.0
                    Iu = pwl.interp_rows(tc, w.x, I)
                    Ip = pwl.interp_rows(tc, hist["t"], hist["I"])
                    fit.add(np.broadcast_to(Fu - Fp, Iu.shape), Iu - Ip, h)
            tol = max(tol_rel * largest, tol_mV * 1e-3)
            moved = _metrics_move(res, st.get("res"), ing, st.get("ing"))
            prev_I = st.get("Iavg")
            Iavg = np.abs(res["Ie"]["avg"])
            scale = max(float(Iavg.max()) if len(Iavg) else 0.0, 1e-18)
            d_I = (float(np.abs(Iavg - prev_I).max() / scale) if prev_I is not None
                   else None)
            hist_r = [r["residual_mV"] for r in per[net]] + [resid * 1e3]
            ok, rule, still = stop_rule(resid, tol, largest, moved, bool(st.get("still")),
                                        hist_r, bool(st.get("coarsened")))
            st["still"] = still
            # two growths in a row (each above GROWTH, so that noise at the
            # floor is not one), above the tolerance
            if len(hist_r) >= 3 and hist_r[-1] > GROWTH * hist_r[-2] and \
                    hist_r[-2] > GROWTH * hist_r[-3] and resid >= tol:
                grew = True
            kc = None
            if getattr(driver, "kcl_source", None) is not None and simulate is None:
                sigs = [found[p] for p in pins[net] if p in found and p in fed_pins[net]]
                kc = kcl(w, ws0[net], sigs, driver.kcl_source(net))
            nrec = {"pass": k, "residual_mV": resid * 1e3, "droop_change_mV": resid * 1e3,
                    "tolerance_mV": tol * 1e3, "max_droop_mV": largest * 1e3,
                    "simulated_droop_mV": (float(np.abs(Fk[1]).max()) * 1e3
                                           if Fk is not None else 0.0),
                    "current_change": d_I, "metric_change": moved,
                    # the net's largest ratio: its worst violation's, or with
                    # none the largest of every checked segment (never 0.0)
                    "worst_ratio": (res["worst"]["ratio"] if res["worst"]
                                    else (res.get("max_ratio") or {}).get("all")),
                    "ir_drop_mV": float(res["ir_drop"]) * 1e3,
                    "total_avg_A": float(ing["total_avg"]),
                    "seconds": rec["seconds"], "points": rec["points"],
                    "pwl_points": rec["pwl_points"],
                    "pwl_tol_mV": (st.get("pwl_tol") * 1e3 if st.get("pwl_tol") is not None
                                   else None),
                    "fed_by": rec["fed_by"], "fed_scale": rec["fed_scale"],
                    "companion": st.get("comp_summary"), "kcl": kc,
                    "converged": bool(ok), "stop_rule": rule}
            if kc is not None and (kc["avg_rel"] > KCL_AVG or kc["instant_rel"] > KCL_INSTANT):
                if not st.get("kcl_warned"):
                    st["kcl_warned"] = True
                    notice("EMIR-W541", f"net {net!r}, pass {k}: the split pins draw "
                           f"{kc['pins_avg_A'] * 1e3:.6g} mA on average and the rail source "
                           f"{kc['source'] or ''} supplies {kc['source_avg_A'] * 1e3:.6g} mA "
                           f"({kc['avg_rel']:.2e} apart; {kc['instant_rel']:.2e} of its peak at "
                           f"worst): part of the rail's current reaches it some other way (a "
                           f"terminal the split does not move, a testbench load or capacitor "
                           f"on the rail), and that part is not fed back", log)
            per[net].append(nrec)
            if on_pass:
                nrec.update(on_pass(k, net, res, ing) or {})
            rec["nets"][net] = nrec
            all_ok = all_ok and ok
            st.update(res=res, ing=ing, Iavg=Iavg, I_now=(w.x.copy(), I),
                      resid=resid, largest=largest, tol=tol, fit=fit,
                      hist_next={"t": w.x.copy(), "I": I, "fed": Fk})
            wr = nrec["worst_ratio"]
            log(f"[emir] pass {k} {net}: residual {resid * 1e3:.4f} mV (tolerance "
                f"{tol * 1e3:.4g} mV), largest droop {largest * 1e3:.4f} mV, worst "
                f"{'not a number' if wr is None else f'{wr:.4f}x'}, IR "
                f"{nrec['ir_drop_mV']:.4f} mV")
        snap = {"pass": k, "raw": sim.get("raw"),
                "nets": {n: {"res": state[n]["res"], "ing": state[n]["ing"]} for n in nets}}
        if first is None:
            first = snap
        keep = snap
        # a Spectre netlist (split by hand) that ignores the droop: the pass fed
        # a droop and no pin current moved at all
        if sim_name == "spectre" and simulate is None and k >= 1:
            for net in nets:
                r = per[net][-1]
                if r["simulated_droop_mV"] > tol_mV and r["current_change"] is not None \
                        and r["current_change"] < 1e-9:
                    raise EmirError("EMIR-E524", f"pass {k} fed {r['simulated_droop_mV']:.4g} "
                                    f"mV of droop and no pin current of {net} moved: "
                                    f"{netlist} does not read the per-pin PWL files, or its "
                                    f"split nodes feed no pin (add the `emir run "
                                    f"--split-stub` lines and reconnect each pin as its "
                                    f"comments say)", path=netlist, net=net)
        passes.append(rec)
        if all_ok:
            converged = True
            worst = max(per[n][-1]["residual_mV"] for n in nets)
            tol_w = max(per[n][-1]["tolerance_mV"] for n in nets)
            droop_w = max(per[n][-1]["max_droop_mV"] for n in nets)
            last = (f"; at the last allowed pass (--max-passes {max_passes})"
                    if k == max_passes - 1 and k else "")
            if any(per[n][-1]["stop_rule"] == "metrics" for n in nets):
                stopped = "settled"
                br = max((per[n][-1]["metric_change"] or {}).get("branches") or 0.0
                         for n in nets)
                notice("EMIR-I520", f"self-consistent after {k + 1} simulation(s), settled: "
                       f"on two passes in a row the governing branch's avg, rms and peak, "
                       f"every reported item and every device's own current moved less than "
                       f"{100 * METRIC_TOL:g} %; the residual, {worst:.4g} mV, is above its "
                       f"{tol_w:.4g} mV tolerance ({100 * worst / max(droop_w, 1e-30):.2g} % "
                       f"of the {droop_w:.4g} mV droop, under {100 * SETTLE_FRACTION:g} %, not "
                       f"growing); the branches below the warning fraction moved up to "
                       f"{100 * br:.2g} % on the last pass{last}", log)
            else:
                stopped = "converged"
                notice("EMIR-I520", f"self-consistent after {k + 1} simulation(s): the droop "
                       f"the currents produce is within {worst:.4g} mV of the droop fed "
                       f"(tolerance {tol_w:.4g} mV)"
                       + (" and the governing avg, rms and peak moved less than "
                          f"{100 * METRIC_TOL:g} %" if k else "") + last, log)
            break
        if grew:
            diverged, stopped = True, "residual grew twice"
            notice("EMIR-W406", "self-consistency is diverging: the residual grew on two "
                   "passes in a row (" + ", ".join(
                       f"{n}: " + " -> ".join(f"{r['residual_mV']:.4g}" for r in per[n][-3:])
                       + " mV" for n in nets)
                   + ") -- stopped; pass 0 (the open loop) is reported; "
                   + "; ".join(reads_text(n, open_loop_reads(per[n])) for n in nets), log)
            keep = first
            break
        if k == max_passes - 1:
            break
        # ------------------------------------------------ the next droop
        if k == 0 and guard is None:
            # no supply known (a ground net alone): bound the droop by the
            # open loop's own
            guard = GUARD_OPEN_LOOP * max(state[n]["largest"] for n in nets)
        new_fed, over = _next_droop(nets, state, grids, pins, fed_pins, tb, dt,
                                    k if companion else 0, max_bytes, log, pwl_cap,
                                    first_cap if k == 0 else None)
        if guard is not None and over > guard:
            diverged, stopped = True, "droop above half the supply"
            notice("EMIR-W406", f"self-consistency is diverging: the droop to feed back "
                   f"reaches {over * 1e3:.4g} mV, above "
                   + (f"half the supply ({guard * 1e3:.4g} mV)" if span > 0 or guard_V else
                      f"{GUARD_OPEN_LOOP:g} x the open loop's droop ({guard * 1e3:.4g} mV)")
                   + " -- stopped before simulating it; pass 0 (the open loop) is "
                   "reported; " + "; ".join(reads_text(n, open_loop_reads(per[n]))
                                            for n in nets), log)
            keep = first
            break
        for net in nets:
            state[net]["hist"] = state[net].pop("hist_next")
        fed = new_fed
        passes[-1]["post_seconds"] = time.perf_counter() - t_post
    if not converged and not diverged and stopped == "pass limit":
        notice("EMIR-W407", f"stopped at the {max_passes}-simulation limit before the droop "
               f"settled (last residual "
               + ", ".join(f"{n} {per[n][-1]['residual_mV']:.4g} mV against "
                           f"{per[n][-1]['tolerance_mV']:.4g} mV" for n in nets)
               + "): pass 0 (the open loop) is reported, not a pass between", log)
        keep = first
    if passes and "post_seconds" not in passes[-1]:
        passes[-1]["post_seconds"] = time.perf_counter() - t_post
    at_limit = converged and len(passes) == max_passes and max_passes > 1
    out = {"passes": passes, "converged": converged, "diverged": diverged,
           "stopped": stopped, "reported_pass": keep["pass"], "raw": keep["raw"],
           "max_passes": max_passes, "tol_mV": tol_mV, "tol_rel": tol_rel,
           "simulator": sim_name, "nets": {}, "net_list": nets, "dt": dt,
           "steps": None if tu is None else len(tu) - 1, "at_pass_limit": at_limit,
           "pwl_cap": pwl_cap}
    for net in nets:
        snapn = keep["nets"][net]
        out["nets"][net] = {
            "passes": per[net], "result": snapn["res"], "taps": snapn["ing"]["taps"],
            "ingest": snapn["ing"], "raw": keep["raw"], "reported_pass": keep["pass"],
            "network": state[net].get("nw"), "converged": converged, "diverged": diverged,
            "stopped": stopped, "tol_mV": tol_mV, "tol_rel": tol_rel,
            "max_passes": max_passes, "v_nominal": vdds.get(net), "simulator": sim_name,
            "nets": nets, "fed": state[net].get("fed"), "at_pass_limit": at_limit,
            "feedback": feedback.get(net), "pwl_cap": pwl_cap,
            "simulated_decaps": sim_decaps.get(net, [])}
    return out


def _next_droop(nets, state, grids, pins, fed_pins, tb, dt, k, max_bytes, log,
                cap: int = pwl.MAX_POINTS, first_cap: float | None = None) -> tuple:
    """The droop every net's pins get on the next pass: the plain solve after
    pass 0 (scaled down to `first_cap` when its largest droop is above it),
    the companion solve after that (fitted, or kept: frozen), both on the
    base `tb` (the companion's terms on the nominal step `dt`, the grid's
    capacitance on each actual step), as one shared-grid PWL per net, at
    most `cap` points (EMIR-W542 when it must be coarser than its tolerance
    for that). Returns (fed for the driver, the largest droop fed)."""
    out_v, over = {}, 0.0
    tol_used = 0.0
    per_net = {}
    for net in nets:
        st, grid = state[net], grids[net]
        how = "plain"
        if k >= 1 and st.get("fit") is not None:
            g, C, r2 = st["fit"].solve()
            g[~grid.fed], C[~grid.fed] = 0.0, 0.0
            r2_new = float(np.median(r2[grid.fed]))
            frozen = st.get("comp") is not None and (
                st["resid"] < FREEZE_TOL * st["tol"]
                or (r2_new < FIT_R2_MIN and r2_new < st["comp_summary"]["r2_median"]))
            if not frozen:
                st["comp"] = (g, C)
                st["comp_summary"] = {
                    "g_mS": [float(np.median(g[grid.fed])) * 1e3, float(g.max()) * 1e3],
                    "C_fF": [float(np.median(C[grid.fed])) * 1e15, float(C.max()) * 1e15],
                    "r2_median": r2_new, "frozen": False}
            else:
                st["comp_summary"] = dict(st["comp_summary"], frozen=True)
            how = "companion (frozen)" if frozen else "companion"
        per_net[net] = (pwl.tolerance(st["tol"]), how)
    # the first update's scale: the plain map of pass 0's currents is the
    # droop the residual sweep solved (`largest`)
    scale = 1.0
    if first_cap is not None:
        big = max(state[n]["largest"] for n in nets)
        if big > first_cap:
            scale = first_cap / big
    by = next(iter(per_net.values()))[1]
    if scale < 1.0:
        by = "plain (scaled)"
        log(f"[emir] the first update's droop is above {first_cap * 1e3:.4g} mV "
            f"({FIRST_STEP_FRACTION:.0%} of the supply): fed at x{scale:.3g}")
    # solve every net's new droop on the base chunk by chunk, and compress
    # it as it comes on ONE time grid for every pin of every net (each row in
    # units of its net's tolerance), at most `cap` points (memory: one chunk)
    tols = np.concatenate([np.full(len(pins[n]), per_net[n][0]) for n in nets])
    comps = {n: (state[n].get("comp") if per_net[n][1].startswith("companion") else None)
             for n in nets}
    c = _chunks(len(tb), sum(4 * len(pins[n]) + grids[n].n for n in nets), max_bytes)
    factor = 1.0
    while True:
        for n in nets:
            if comps[n] is not None:
                # the companion's terms on the nominal step: it only shapes the
                # path (they cancel at the fixed point whatever the step)
                grids[n].companion_factor(comps[n][0], comps[n][1], dt)
        cstates = {n: {} for n in nets}
        kept_t, kept_v, last = [], [], None
        largest = {n: 0.0 for n in nets}
        for s in range(0, len(tb), c):
            tbc = tb[s:s + c]
            h = steps(tb, s, c, dt)
            blocks = []
            for n in nets:
                st, grid = state[n], grids[n]
                t_sim, I_sim = st["I_now"]
                Iu = pwl.interp_rows(tbc, t_sim, I_sim)
                if comps[n] is None:
                    V = grid.map_chunk(Iu, cstates[n], h)
                else:
                    Fk = st.get("fed")
                    Fu = pwl.interp_rows(tbc, *Fk) if Fk is not None else np.zeros_like(Iu)
                    V = grid.companion_chunk(Iu, Fu, cstates[n], h)
                V = V * scale
                V[~grid.fed] = 0.0
                largest[n] = max(largest[n], float(np.abs(V).max()) if V.size else 0.0)
                blocks.append(V)
            V = np.vstack(blocks)
            if last is None:
                T, Y = tbc, V
            else:
                T = np.concatenate([[last[0]], tbc])
                Y = np.concatenate([last[1][:, None], V], axis=1)
            ix = pwl.compress(T, Y / tols[:, None], factor)
            if last is not None:
                ix = ix[1:]
            kept_t.append(T[ix])
            kept_v.append(Y[:, ix])
            last = (T[-1], Y[:, -1].copy())
        tk = np.concatenate(kept_t)
        if len(tk) <= cap:
            break
        factor *= 1.5
    Fall = np.concatenate(kept_v, axis=1)
    row = 0
    for net in nets:
        st = state[net]
        tol0 = per_net[net][0]
        tol = factor * tol0
        if factor > 1.0:
            notice("EMIR-W542", f"net {net!r}: the droop waveforms need more than {cap} PWL "
                   f"points per pin at {tol0 * 1e3:.4g} mV (every point is a simulator "
                   f"breakpoint): fed at {tol * 1e3:.4g} mV, {len(tk)} points"
                   + ("; coarser than the loop's tolerance, so the loop cannot converge on "
                      "it" if tol > st["tol"] else ""), log)
        st["pwl_tol"] = tol
        st["coarsened"] = factor > 1.0 and tol > st["tol"]
        over = max(over, largest[net])
        F = Fall[row:row + len(pins[net])]
        row += len(pins[net])
        st["fed"] = (tk, F)
        for j, p in enumerate(pins[net]):
            if p in fed_pins[net]:
                out_v[p] = F[j]
        tol_used = max(tol_used, tol)
    return ({"t": tk, "v": out_v, "points": len(tk), "tol_mV": tol_used * 1e3,
             "by": by, "scale": scale}, over)
