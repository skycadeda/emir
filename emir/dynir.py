"""Dynamic IR: the droop the rail actually sees, not the one it averages to.

Average IR drop is reassuring and misleading. A digital or switching block
draws its current in spikes several times the mean, and the rail follows the
*instantaneous* demand, so the droop that matters can be several times the
average one. That is the number a supply spec is written against, and the
static solve cannot produce it -- it needs C.

Two things are modelled here.

**The RC grid.** `C dV/dt + G V = I(t)`, integrated on a uniform step so the
system matrix factorises once and each timestep costs a back-substitution --
the same economy the static solve gets from superposition. A periodic window
refines that step dyadically where the waveforms or a fast decap need it
(`StepSequence`): each finer level is one more factorisation. Grounding the
feed makes every node potential a signed excursion from the rail, which is
droop on a supply net and bounce on a ground net without a special case.

**The decoupling capacitance.** Two sources, treated
differently because their physics differ:

* **Wire capacitance already in an RC-extracted DSPF** attaches to grid nodes
  by name. Its access resistance *is* the grid, already meshed. It needs no
  reduction and no series R -- just a diagonal entry.
* **Decap that is not in the R extraction** (explicit cells, device gate and
  junction capacitance) has to be added, and **its series resistance is not
  optional**: a decap's usefulness is bounded by how fast charge can get out
  of it, and a pure-C lump silently overstates how much droop it kills.
  `lump_decap` reduces such caps to a bounded number of `R_eff + C_eff`
  branches by geometric binning, summing C and combining the access paths in
  parallel, and reports how far it moved charge so the reduction is auditable.

**What is deliberately not claimed.** The tap currents come from a CC sim, so
they do not react to the dynamic droop this module computes: the open-loop
approximation. The result is then an upper bound on droop to the extent the
currents are, and it is labelled as such. `selfconsist` closes that loop
before this module runs (`--self-consistent --dynamic`): each pin is fed its
droop waveform, the RC grid's when the DSPF has capacitance on the net, and
the currents of the loop's reported pass come here, labelled with that pass
(`bigblock.dynamic_source`).
"""
from __future__ import annotations

import numpy as np

from .errors import EmirError, notice

METHODS = ("trap", "be")

#: A MOS decap's channel is a distributed RC line, so its capacitance sees only
#: a fraction of the channel resistance: `R_access = R_channel / 6`.
ACCESS_DIVISOR = 6.0


def mos_decap_lumps(dspf, net: str, proc, idx=None, log=print, em=False) -> tuple:
    """MOS decaps on `net` as `R_eff + C_eff` branches. `(lumps, note)`.
    `em`: the solve also gives decap-aware EM (`decap.solve`), whose
    warning on an excluded decap says its straps are not checked.

    **No simulation happens here.** The device was characterised once, offline,
    and reduced to a few coefficients per model in the PDK file. At run time
    this is arithmetic:

        C        = cox * W * L
        R_access = esr_a + esr_w * W_um + esr_l / L_um      [fitted, preferred]
        R_access = ron_sq * L / W / 6                       [fallback]

    **The fitted form is preferred because the closed form is badly wrong.**
    `R_channel/6` treats the decap as a bare distributed channel; a real cell
    also has source/drain contacts, a poly gate and its contact, and metal
    straps, and its source and drain are tied so the channel is contacted at
    both ends. Measured against an RCC extraction of a real decap layout at
    the original development site (swept over W, L and finger count), the
    closed form over-predicted the per-finger access resistance several-fold
    -- i.e. it makes good decap look useless -- while the fitted model tracked
    the sweep to within about 5%.

    The fit is **per finger**, which is what an extraction hands back: each
    finger is its own device record, and the parallel combination then happens
    in the grid solve. The shared rail strap is deliberately excluded from the
    fit -- the real extraction already contains it.

    `esr_fit_freq` records the frequency it was fitted at; ESR is frequency
    dependent (on the characterised cell it roughly doubled going from 1 GHz
    down to 100 MHz), so a
    droop analysis on a very different timescale should re-fit.
    """
    models = getattr(proc, "cap_models", {}) or {}
    from .dspf import mos_decaps
    lumps, unsized, seen, extrap = [], 0, set(), []
    for m in mos_decaps(dspf, net):
        mm = models.get(m["model"]) or models.get("mos_default")
        w, l = m.get("w"), m.get("l")
        if not mm or not w or not l or not mm.get("cox"):
            unsized += 1
            seen.add(m["model"])
            continue
        if idx is not None and m["node"] not in idx:
            unsized += 1
            seen.add(m["model"])
            continue
        c = mm["cox"] * w * l
        if mm.get("esr_a") is not None:
            wu, lu = w * 1e6, l * 1e6
            r = fitted_r(mm, w, l)
            how = "fitted"
            if r < 0:
                raise EmirError("EMIR-E317", negative_r_text(m, r))
            # an empirical fit says nothing outside the geometries it saw
            wr, lr = mm.get("esr_w_range"), mm.get("esr_l_range")
            if ((wr and not wr[0] * 0.999 <= wu <= wr[1] * 1.001)
                    or (lr and not lr[0] * 0.999 <= lu <= lr[1] * 1.001)):
                extrap.append(f"{m['name']} W={wu:g} L={lu:g}")
                how = "fitted (extrapolated)"
        else:
            r = mm.get("ron_sq", 0.0) * l / w / ACCESS_DIVISOR
            how = f"R_channel/{ACCESS_DIVISOR:g}"
        lumps.append({"node": m["node"], "c": c, "r": r, "name": m["name"],
                      "r_model": how})
    note = ""
    if lumps:
        note = (f"{len(lumps)} MOS decap(s), "
                f"{sum(x['c'] for x in lumps) * 1e15:.2f} fF, access R "
                f"{min(x['r'] for x in lumps):.4g}..."
                f"{max(x['r'] for x in lumps):.4g} ohm "
                f"({lumps[0]['r_model']})")
        log(f"[emir] {note}")
    if extrap:
        note += (f"; {len(extrap)} outside the fit range, EXTRAPOLATED")
        notice("EMIR-W408", f"MOS decap access R extrapolated beyond the "
            f"geometries the model was fitted on: {', '.join(extrap[:4])}"
            + (" ..." if len(extrap) > 4 else "")
            + ". Re-run the layout sweep over this range if it matters.", log)
    if unsized:
        note += (f"; {unsized} MOS decap(s) EXCLUDED -- no cox/ron_sq for "
                 f"{', '.join(sorted(seen))}")
        notice("EMIR-W409", f"{unsized} MOS decap(s) on {net} excluded: no "
            f"`cap_models` cox for {', '.join(sorted(seen))}. "
            f"Add `cap_models` entries to the process file (see the user guide). "
            f"Excluding decap OVERSTATES droop"
            + (" and gives the decaps' straps no current in decap-aware EM, so those "
               "straps are NOT checked (the basis asked for is not honoured there)"
               if em else "") + ".", log)
    return lumps, note


def fitted_r(mm: dict, w: float, l: float) -> float:
    """A MOS decap's fitted access resistance `esr_a + esr_w W + esr_l / L`
    (W, L in m; the fit's units are um)."""
    return mm["esr_a"] + mm.get("esr_w", 0.0) * w * 1e6 + mm.get("esr_l", 0.0) / (l * 1e6)


def negative_r_text(m: dict, r: float) -> str:
    """EMIR-E317's message for a fit that is negative at a decap's geometry."""
    return (f"cap_models/{m['model']}: the fitted access resistance of {m['name']} "
            f"(W {m['w'] * 1e6:g} um, L {m['l'] * 1e6:g} um) is {r:.4g} ohm, negative: the "
            f"fit does not hold there")


def negative_fits(dspf, net: str, proc) -> list:
    """EMIR-E317's messages for the MOS decaps on `net` whose fitted access
    resistance is negative at their own geometry (the dynamic solve refuses
    them; `emir check --dynamic` says so before the run)."""
    from .dspf import mos_decaps
    models = getattr(proc, "cap_models", {}) or {}
    out = []
    for m in mos_decaps(dspf, net):
        mm = models.get(m["model"]) or models.get("mos_default")
        if not mm or not m.get("w") or not m.get("l") or not mm.get("cox") \
                or mm.get("esr_a") is None:
            continue
        r = fitted_r(mm, m["w"], m["l"])
        if r < 0:
            out.append(negative_r_text(m, r))
    return out


# ------------------------------------------------------------ capacitance


def device_cap(c: dict, models: dict) -> tuple:
    """`(capacitance, how)` for a capacitor *device*, from its geometry.

    `models` maps a DSPF model name to `{cj: F/m^2, cjsw: F/m}`, the area and
    perimeter capacitance of the PDK's capacitor model.

    **The model name is usually absent.** Quantus only writes it under
    `-include_cap_model true`, and that form does not simulate -- it emits the
    3-terminal MIM with 2 positional terminals and `$SUB=` as an annotation,
    which Spectre rejects outright. So the flow must extract without the model
    name, and a device cap arrives as geometry with nothing to key on. A
    `default` entry covers the common single-cap-flavour case; it is applied
    only when there is no name, and the count is reported, because a design
    mixing MIM and MOS decap would need the real names.

    Returning zero for an unsizable cap is not an option: it silently deletes
    decoupling capacitance and the resulting droop looks conservative.
    """
    if c.get("area") is None:
        return None, None
    name = c.get("model")
    m = models.get(name) if name else models.get("default")
    if not m:
        return None, None
    return (m.get("cj", 0.0) * c["area"]
            + m.get("cjsw", 0.0) * (c.get("perim") or 0.0),
            "model" if name else "default")


def grounded_caps(dspf, net: str, idx: dict, coupling_as_ground=True,
                  proc=None, log=print, em=False) -> tuple:
    """Per-node capacitance from the DSPF's own `C` lines.

    Returns `(C vector over idx, note)`. A cap from the net to a *quiet* node
    is decoupling; one to a node that swings with the rail is not. Extracted
    netlists do not say which, so the standard power-analysis assumption --
    the far plate is an AC ground -- is taken and named. It is optimistic for
    any far net that happens to swing in phase with this one.

    Capacitor **devices** (decap cells, MIM caps) carry geometry rather than a
    value. `proc.cap_models` converts them; without it they are counted and
    **warned about** rather than treated as zero, because silently dropping
    decap overstates droop and looks like a clean conservative answer.
    """
    models = getattr(proc, "cap_models", {}) or {}
    C = np.zeros(len(idx))
    n_gnd = n_coup = skipped = n_dev = n_default = 0
    unresolved, unres_models = 0, set()
    for c in dspf.net_caps(net):
        i = idx.get(c["node"])
        if i is None:
            skipped += 1
            continue
        val = c["c"]
        if val is None:
            val, how = device_cap(c, models)
            if val is None:
                unresolved += 1
                unres_models.add(c.get("model") or c["name"])
                continue
            n_dev += 1
            n_default += how == "default"
        if c["to_ground"]:
            C[i] += val
            n_gnd += 1
        elif coupling_as_ground:
            C[i] += val
            n_coup += 1
    # MOS decaps are handled by `mos_decap_lumps` as R+C *branches*, not as
    # node capacitance: their access resistance is the whole point and a
    # diagonal entry cannot carry one.

    note = (f"{n_gnd} caps to ground"
            + (f" + {n_coup} coupling caps treated as AC ground (optimistic "
               f"for any far net swinging in phase)" if n_coup else "")
            + (f"; {n_dev} capacitor devices sized from geometry"
               + (f" ({n_default} via the `default` model -- the DSPF carries "
                  f"no model name)" if n_default else "") if n_dev else "")
            + (f"; {skipped} on nodes outside the solved net" if skipped else ""))
    log(f"[emir] rail capacitance {C.sum() * 1e15:.3f} fF -- {note}")
    if unresolved:
        note += (f"; {unresolved} capacitor device(s) NOT included "
                 f"({', '.join(sorted(unres_models))})")
        notice("EMIR-W410", f"{unresolved} capacitor/MOS-decap device(s) on "
            f"{net} could not be sized -- no `cap_models` entry for "
            f"{', '.join(sorted(unres_models))}. They are EXCLUDED, which "
            f"OVERSTATES droop"
            + (" and gives their straps no current in decap-aware EM, so those straps "
               "are NOT checked" if em else "")
            + ". Add to the PDK file: cap_models: "
            "{<model>: {cj: F/m2, cjsw: F/m}} for capacitors, "
            "{<model>: {cox: F/m2}} for MOS decaps.", log)
    return C, note


def lump_decap(items, node_xy, idx, max_lumps=64, log=print) -> tuple:
    """Reduce a decap list to at most `max_lumps` `R_eff + C_eff` branches.

    `items`: [{xy, c, r}] -- position, capacitance, and **access resistance**,
    which must be real. Binning is geometric over the net's bounding box; each
    bin sums C, combines the access paths in parallel (`1/sum(1/r)`), and
    attaches to the grid node nearest its charge centroid.

    Charge moves when it is binned, so the displacement is reported: it is the
    error this reduction introduces, and it is the number to look at before
    trusting a coarse `max_lumps`.
    """
    items = [it for it in items if it.get("c", 0) > 0]
    if not items:
        return [], "no decap"
    nodes = [n for n in idx if n in node_xy]
    if not nodes:
        raise EmirError("EMIR-E216", "no solved node has coordinates -- cannot place decap")
    P = np.array([node_xy[n] for n in nodes])
    xy = np.array([it["xy"] for it in items], dtype=float)
    c = np.array([it["c"] for it in items], dtype=float)
    r = np.array([max(it.get("r") or 0.0, 1e-12) for it in items], dtype=float)

    nb = max(1, int(np.floor(np.sqrt(max_lumps))))
    lo, hi = xy.min(axis=0), xy.max(axis=0)
    span = np.maximum(hi - lo, 1e-9)
    ij = np.clip(((xy - lo) / span * nb).astype(int), 0, nb - 1)
    key = ij[:, 0] * nb + ij[:, 1]

    lumps, moved = [], 0.0
    for k in np.unique(key):
        m = key == k
        csum = float(c[m].sum())
        cen = (xy[m] * c[m, None]).sum(axis=0) / csum      # charge centroid
        j = int(np.argmin(((P - cen) ** 2).sum(axis=1)))
        moved = max(moved, float(np.sqrt(((xy[m] - P[j]) ** 2).sum(axis=1)).max()))
        lumps.append({"node": nodes[j], "c": csum,
                      "r": float(1.0 / (1.0 / r[m]).sum()), "n": int(m.sum())})
    note = (f"{len(items)} decap elements -> {len(lumps)} lumps "
            f"({sum(l['c'] for l in lumps) * 1e15:.2f} fF total), charge moved "
            f"at most {moved:.3f} um")
    log(f"[emir] decap reduction: {note}")
    return lumps, note


# -------------------------------------------------------------- transient


def default_steps(n_points: int) -> int:
    """The uniform steps of a --dynamic transient when --dyn-steps is not
    given: four per interval of the window's samples, at least 2000 and at
    most 200000, as a whole multiple of the intervals where that fits, so the
    simulator's samples are grid points when they are evenly spaced (a fixed
    print step): the grid's linear interpolation then misses nothing of them,
    and no interval needs sub-steps for its input."""
    iv = max(int(n_points) - 1, 1)
    k = max(4, -(-1999 // iv))
    if k * iv + 1 > 200_000:
        k = 199_999 // iv
    return k * iv + 1 if k >= 1 else 200_000


def resample(t, W, t0, t1, n_steps, log=print, warn=True) -> tuple:
    """Put tap waveforms on a uniform step, and say what that cost.

    A fixed step is what lets the system matrix factorise once. The risk is
    that a uniform grid coarser than the simulator's adaptive one clips the
    current spikes that *cause* dynamic droop, so `retention` -- the fraction
    of each tap's peak that survived -- is reported and warned on. Losing peak
    here understates droop, which is the wrong direction to be wrong in.
    """
    tu = np.linspace(t0, t1, n_steps)
    Wu = np.array([np.interp(tu, t, w) for w in W])
    pk0 = np.abs(W).max(axis=1)
    pk1 = np.abs(Wu).max(axis=1)
    keep = float(np.min(pk1 / np.maximum(pk0, 1e-30)))
    if keep < 0.99 and warn:
        notice("EMIR-W411", f"resampling to {n_steps} uniform steps kept only "
            f"{keep:.1%} of the worst tap's peak current -- dynamic droop will "
            f"be UNDERSTATED; raise n_steps", log)
    return tu, Wu, keep


#: The step sequence of a periodic pass (`StepSequence`). A feature of a pin's
#: waveform (an edge, a spike's tip) that the sequence's linear interpolation
#: misses by more than EVENT_TOL of the pin's scale is refined; so is the step
#: after a change of the input's slope whose trapezoidal error could exceed
#: RING_TOL of it (the ringing guard)
EVENT_TOL, RING_TOL = 1e-3, 1e-2
#: the trapezoidal rule's error over the first step after a change db of the
#: input's slope, on a mode of time constant tau, is |db| tau |r(x) - e^-x|
#: (x = step / tau, r = (1 - x/2) / (1 + x/2)): at most RING x step x |db|
#: (at x ~ 4.5), and at most |db| x tau (r - e^-x)/(x/(x+2))^2 <= 1)
RING = 0.088
#: a pin's scale: its own largest current, and at least this share of the
#: net's largest (a pin that carries almost nothing is not refined for its noise)
PIN_FLOOR = 1e-2
#: the finest sub-step is the grid's step / 2^MAX_DEPTH; the factorisations of
#: the sub-step sizes share LU_BUDGET bytes, so a big grid gets fewer levels
MAX_DEPTH, LU_BUDGET = 16, 1 << 30
#: the most points the refinement may add: SUB_BUDGET x the grid's steps, or
#: as many as SUB_WORK node-steps of solve allow on a small net
SUB_BUDGET, SUB_WORK = 3.0, 2e7
#: the steps grow back to the grid's at most twofold every GRADE steps, so a
#: decap's transient has decayed (by e^-GRADE) before the step outgrows its
#: time constant
GRADE = 3
#: a decap lump whose own access R*C is below this share of the finest step
#: the sequence takes is folded onto its node (its ESR invisible at every step)
FOLD_FRAC = 0.1
#: the least share of its own height every feature of the waveforms keeps on
#: the sequence for decap-aware EM to be computed (`decap.solve`)
FEATURE_KEEP = 0.99
#: an unresolved feature is negligible -- left out of that judgement, and said
#: (EMIR-W429) -- when both the charge and the energy (the integral of i^2) it
#: carries beyond the grid's chord are below this share of its pin's own per
#: period: then it moves no average (the static solve's anyway), the droop by
#: at most that share of the pin's charge, and a branch's rms^2 by about that
#: share of the pin's; only its own peak, for less than the finest step, is
#: not in the decap-aware currents (a decap's strap near the pin may carry up
#: to the feature's height for that long). A design choice, not a measurement
NEGLIGIBLE = 1e-6
#: the dynamic solve's feed current may read above the load's own on the same
#: steps (a passive RC network only low-passes it) by at most this
FEED_TOL = 5e-3


class EmSpec:
    """What `transient` keeps for decap-aware EM (`decap.py`), besides the droop.

    * `avg_tol`: the periodic-steady-state check on the branch averages (see
      `transient`), relative to the largest decap-free branch average.
    * `cap_nodes`: nodes whose capacitor current (from KCL) is kept, for the
      capacitor devices and the decap lumps folded onto them.
    """

    def __init__(self, avg_tol: float = 1e-4, cap_nodes=()):
        self.avg_tol = avg_tol
        self.cap_nodes = list(cap_nodes)


def segment_sums(a, b, dt_over_T):
    """The time integrals over one step of a quantity linear between its end
    values `a` and `b`, divided by the window: of x and x^2 (exact for a
    linear segment: the input is piecewise linear between the points of the
    step sequence, and the trapezoid of x^2, `(a^2 + b^2)/2`, reads a steep
    linear flank high by `(a - b)^2 / 6` per step), and of |x| (the
    trapezoid: high by at most a quarter step's worth where x changes sign;
    it only flags reversing currents)."""
    s1 = (0.5 * dt_over_T) * (a + b)
    s2 = a * (a + b)
    s2 += b * b
    s2 *= dt_over_T / 3.0
    sa = np.abs(a)
    sa += np.abs(b)
    sa *= 0.5 * dt_over_T
    return s1, s2, sa


class _Stats:
    """The mean, mean square and mean |x| of a vector quantity over the actual
    step sequence of one pass (exact for a quantity linear between the
    points: the sums of `segment_sums`), and its running peak over every point
    of it. In place, with scratch buffers: on a big grid this runs once per
    point for every branch."""

    def __init__(self, n: int, T: float):
        self.s1, self.s2, self.sa = np.zeros(n), np.zeros(n), np.zeros(n)
        self.pk = np.zeros(n)
        self.T = T
        self.prev = None
        self._ap, self._ax = np.zeros(n), np.zeros(n)     # |previous|, |x|
        self._t, self._u, self._v = np.zeros(n), np.zeros(n), np.zeros(n)

    def add(self, x: np.ndarray, dt: float | None):
        ax, t, u, v = self._ax, self._t, self._u, self._v
        np.abs(x, out=ax)
        np.maximum(self.pk, ax, out=self.pk)
        if dt is not None and self.prev is not None:
            w = dt / self.T
            a = self.prev
            np.add(a, x, out=t)                 # a + b
            np.multiply(a, t, out=u)            # a (a + b)
            np.multiply(x, x, out=v)            # b^2
            u += v
            u *= w / 3.0
            self.s2 += u                        # (a^2 + a b + b^2) w / 3
            t *= 0.5 * w
            self.s1 += t                        # (a + b) w / 2
            np.add(self._ap, ax, out=t)
            t *= 0.5 * w
            self.sa += t                        # (|a| + |b|) w / 2
        self.prev = x
        self._ap, self._ax = ax, self._ap

    def as_dict(self) -> dict:
        return {"avg": self.s1, "rms": np.sqrt(np.maximum(self.s2, 0.0)),
                "absavg": self.sa, "peak": self.pk}


class _EmHook:
    """The step hook of decap-aware EM: after every step it reads the node
    vector and keeps, per grid resistor, the current `g (V_a - V_b)` reduced
    to running sums (the PyTorch picture: a forward hook that reads each step
    without changing it). Memory is O(branches), never branches x steps."""

    def __init__(self, nw, idx, ground, fi, n, ng, lumps, cvec, T, spec):
        self.spec = spec
        self.a, self.b, self.g = np.asarray(nw.a), np.asarray(nw.b), np.asarray(nw.g)
        self.V = np.zeros(n)
        self.fi = fi
        self.T = T
        self.lnode = np.array([idx[L["node"]] for L in lumps], dtype=np.int64)
        self.lint = ng + np.arange(len(lumps), dtype=np.int64)
        self.lg = np.array([1.0 / max(L["r"], 1e-12) for L in lumps])
        gi = idx[ground]
        self.out_m = np.flatnonzero(self.a == gi)   # branches leaving the feed (n1 there)
        self.in_m = np.flatnonzero(self.b == gi)    # branches entering it (n2 there)
        pos = {int(j): q for q, j in enumerate(fi)}
        # two branch-current buffers, alternating: the statistics keep the
        # previous point's, the gathers write the next one's in place
        self._A = [np.zeros(len(self.g)), np.zeros(len(self.g))]
        self._gb = np.zeros(len(self.g))
        self._k = 0
        self.cap_names = [c for c in spec.cap_nodes if idx.get(c) in pos]
        self.cap_q = np.array([pos[idx[c]] for c in self.cap_names], dtype=np.int64)
        self.cap_c = np.array([cvec[idx[c]] for c in self.cap_names])

    def begin(self):
        self.br = _Stats(len(self.g), self.T)
        self.lu = _Stats(len(self.lg), self.T)
        self.cap = _Stats(len(self.cap_q), self.T)
        self.feed = _Stats(1, self.T)

    def sample(self, Vf, Ik, GV, dt):
        """One point of the step sequence, `dt` after the previous (None:
        the first point of the pass)."""
        V = self.V
        V[self.fi] = Vf
        self._k ^= 1
        A = self._A[self._k]
        np.take(V, self.a, out=A)
        np.take(V, self.b, out=self._gb)
        A -= self._gb
        A *= self.g
        self.br.add(A, dt)
        if len(self.lg):
            self.lu.add(self.lg * (V[self.lnode] - V[self.lint]), dt)
        if len(self.cap_q):
            # the capacitor current of a node from KCL: what is injected minus
            # what the conductances carry away. Exact for the trapezoidal
            # system (it satisfies the companion relation identically) and
            # stateless, so a pass that starts from a carried state carries
            # no error with it
            self.cap.add((Ik - GV)[self.cap_q], dt)
        self.feed.add(np.array([A[self.out_m].sum() - A[self.in_m].sum()]), dt)


class StepSequence:
    """The time points one periodic pass integrates over: the uniform grid of
    `n_steps`, refined dyadically where the input needs it, so every step is
    the grid's step / 2^level and each level used is one factorisation.

    The grid's linear interpolation can miss what matters in the simulator's
    waveforms: an edge between two grid points loses its charge, and a
    spike's tip its peak -- with nothing in the pin's own largest current to
    say so (a 10 ps spike on a pin whose largest current is a slow pulse read
    its decap strap 30 % low). And the trapezoidal rule, A-stable but not
    L-stable, rings in a (-1)^n mode after a change of the input's slope on a
    decap whose time constant is shorter than the step (the feed of a 24 ps
    decap read 19 % above the load's own peak at a 144 ps step). So, round by
    round, each flagged cell is halved:

    (a) a cell whose chord misses a simulator sample inside it by more than
        EVENT_TOL of that pin's scale (the features, down to their tips);
    (b) the cell after a point where a pin's slope changes by db, while
        |db| min(tau, RING x cell) exceeds RING_TOL of the pin's scale (or
        all pins' together, of the net's): `tau` is the stiff part of the
        decaps' time constants as that pin sees them (`transient`), so a
        wire-cap mesh with no fast decap refines nothing;
    (c) a cell more than twice the one before it (cyclically: the pass
        wraps), so the steps grow back to the grid's geometrically and a
        decap's transient has decayed before the step outgrows it.

    At most SUB_BUDGET x the grid's steps are added: past that the intervals
    with the smallest features keep the grid's step (`dropped`).
    `feature_keep()` is the least share of its own height (its largest miss
    from the grid's chord) any feature of any pin keeps on the sequence.
    """

    def __init__(self, t, W, n_steps: int, skip=(), periodic: bool = True,
                 depth: int = MAX_DEPTH):
        self.t = np.asarray(t, dtype=float)
        self.tt = self.t - self.t[0]
        self.W = W
        self.n = int(n_steps)
        self.periodic = bool(periodic)
        skip = set(skip)
        self.rows = np.array([j for j in range(len(W)) if j not in skip], dtype=np.int64)
        pk = (np.abs(W[self.rows]).max(axis=1) if len(self.rows) and W.shape[1]
              else np.zeros(len(self.rows)))
        top = float(pk.max(initial=0.0))
        self.scale = np.maximum(pk, max(PIN_FLOOR * top, 1e-300))
        tot = (np.abs(W[self.rows].sum(axis=0)).max(initial=0.0)
               if len(self.rows) and W.shape[1] else 0.0)
        self.total = max(float(tot), 1e-300)
        self.T = float(self.tt[-1]) if len(self.tt) else 0.0
        self.dropped, self.needed, self.cap = 0, None, 0
        self.set_depth(depth)

    # ---- geometry
    def set_depth(self, depth: int) -> None:
        """Start again from the grid, with sub-steps down to h / 2^depth."""
        self.D = max(int(depth), 0)
        self.unit = 1 << self.D
        self.q = self.T / ((self.n - 1) * self.unit) if self.n > 1 else 1.0
        self.grid = np.arange(self.n, dtype=np.int64) * self.unit
        self.P = self.grid.copy()
        self.u = self.tt / self.q

    @property
    def h(self) -> float:
        return self.q * self.unit

    def times(self) -> np.ndarray:
        return self.t[0] + self.P.astype(float) * self.q

    def steps(self) -> np.ndarray:
        return np.diff(self.P).astype(float) * self.q

    def levels(self) -> np.ndarray:
        """Each step's level: its size is the grid's step / 2^level."""
        d = np.diff(self.P)
        return self.D - np.round(np.log2(d)).astype(np.int64)

    def s_min(self) -> float:
        return float(np.diff(self.P).min(initial=self.unit)) * self.q

    def at(self, P=None) -> tuple:
        """(index, fraction): the simulator's samples around the points P."""
        x = (self.P if P is None else P).astype(float) * self.q
        n = len(self.tt)
        i = np.clip(np.searchsorted(self.tt, x, side="right") - 1, 0, max(n - 2, 0))
        if n < 2:
            return i, np.zeros(len(x))
        dt = self.tt[i + 1] - self.tt[i]
        f = np.where(dt > 0, (x - self.tt[i]) / np.where(dt > 0, dt, 1.0), 0.0)
        return i, np.clip(f, 0.0, 1.0)

    def values(self, P=None, rows=None) -> np.ndarray:
        """The simulator's waveforms (the kept rows, or `rows`) at the points,
        gathered column by column (never a copy of the whole waveforms)."""
        i, f = self.at(P)
        r = self.rows if rows is None else np.asarray(rows, dtype=np.int64)
        if self.W.shape[1] < 2:
            return np.repeat(self.W[np.ix_(r, [0])], len(i), axis=1)
        return self.W[np.ix_(r, i)] * (1.0 - f) + self.W[np.ix_(r, i + 1)] * f

    # ---- the criteria
    def _inside(self, P):
        """The simulator's samples strictly inside a cell of P: (sample, cell,
        fraction of the cell)."""
        c = np.searchsorted(P, self.u, side="right") - 1
        ok = (c >= 0) & (c < len(P) - 1)
        c = np.where(ok, c, 0)
        lo = P[c].astype(float)
        hi = P[np.minimum(c + 1, len(P) - 1)].astype(float)
        s = np.flatnonzero(ok & (self.u - lo > 1e-6) & (hi - self.u > 1e-6))
        return s, c[s], (self.u[s] - lo[s]) / (hi[s] - lo[s])

    def _miss(self, P) -> np.ndarray:
        """Per cell of P: the largest miss of its chord at a simulator sample
        inside it, relative to that pin's scale (the largest over pins)."""
        out = np.zeros(len(P) - 1)
        if not len(self.rows):
            return out
        s, c, fr = self._inside(P)
        step = max(1, 2_000_000 // len(self.rows))
        for a in range(0, len(s), step):
            ss, cs, ff = s[a:a + step], c[a:a + step], fr[a:a + step]
            v0, v1 = self.values(P[cs]), self.values(P[cs + 1])
            ch = v0 + (v1 - v0) * ff
            m = (np.abs(self.W[np.ix_(self.rows, ss)] - ch)
                 / self.scale[:, None]).max(axis=0)
            np.maximum.at(out, cs, m)
        return out

    def _slopes(self, P, k, d) -> np.ndarray:
        """The input's slope over the cells k, as the solve takes it."""
        start = self.values(P[k])
        if self.periodic and (k == 0).any():
            # the wrap is carried: the pass's first step starts from the last
            # point's input (`transient`)
            start[:, k == 0] = self.values(P[-1:])
        return (self.values(P[k + 1]) - start) / d[k]

    def _ring(self, P, tau) -> np.ndarray:
        """Per cell of P: the trapezoidal rule's error bound over it after the
        change of slope at its start, relative to the pin's scale (the largest
        over pins), or of every pin's together relative to the net's."""
        d = np.diff(P).astype(float) * self.q
        n = len(d)
        out = np.zeros(n)
        if tau is None or not len(self.rows) or n < 2 or not np.any(tau > 0):
            return out
        tau = np.asarray(tau, dtype=float)[:, None]
        step = max(1, 1_000_000 // len(self.rows))
        for a in range(0, n, step):
            k = np.arange(a, min(n, a + step))
            db = np.abs(self._slopes(P, k, d) - self._slopes(P, (k - 1) % n, d))
            if not self.periodic:
                db[:, k == 0] = 0.0
            e = db * np.minimum(tau, RING * d[k][None, :])
            out[k] = np.maximum((e / self.scale[:, None]).max(axis=0),
                                e.sum(axis=0) / self.total)
        return out

    def _grade(self, P) -> np.ndarray:
        """A cell more than twice the smallest of the GRADE cells before it."""
        d = np.diff(P)
        prev = d.copy()
        for j in range(1, GRADE + 1):
            r = np.roll(d, j)
            if not self.periodic:
                r[:j] = d[:j]
            np.minimum(prev, r, out=prev)
        return d > 2 * prev

    def refine(self, tau=None, rounds=None) -> None:
        """Halve the flagged cells (a), (b) with `tau`, and (c), round by
        round, until none is flagged or the cells reach h / 2^D."""
        if not len(self.rows) or len(self.t) < 2 or self.n < 2:
            return
        P = self.P
        for _ in range(rounds or 4 * self.D + 8):
            d = np.diff(P)
            split = self._miss(P) > EVENT_TOL
            if tau is not None:
                split |= self._ring(P, tau) > RING_TOL
            split |= self._grade(P)
            split &= d > 1
            if not split.any():
                break
            P = np.union1d(P, P[:-1][split] + d[split] // 2)
        self.P = P

    def budget(self, tau=None, nodes: int = 0) -> None:
        """At most SUB_BUDGET x the grid's steps of refinement (or SUB_WORK
        node-steps on a net of `nodes`): past that, the grid intervals whose
        features (or the feature before them, whose steps grow back through
        them) are smallest keep the grid's step."""
        self.needed = len(self.P)
        cap = int(max(SUB_BUDGET * self.n, SUB_WORK / max(nodes, 1)))
        self.cap = cap
        if len(self.P) - self.n <= cap:
            return
        pr = self._miss(self.grid) / EVENT_TOL
        if tau is not None:
            pr = np.maximum(pr, self._ring(self.grid, tau) / RING_TOL)
        before = np.roll(pr, 1)
        if not self.periodic:
            before[0] = 0.0
        pr = np.maximum(pr, before)
        inner = self.P % self.unit != 0
        k = self.P[inner] // self.unit
        cnt = np.bincount(k, minlength=self.n - 1)
        order = np.argsort(-pr, kind="stable")
        keep = np.zeros(self.n - 1, bool)
        keep[order[np.cumsum(cnt[order]) <= cap]] = True
        drop = np.zeros(len(self.P), bool)
        drop[np.flatnonzero(inner)] = ~keep[k]
        self.dropped = int(((cnt > 0) & ~keep).sum())
        self.P = self.P[~drop]

    def _pin_totals(self, rows, periods) -> tuple:
        """Per pin of `rows`: its charge (the integral of |i|) and energy (of
        i^2) per period over the simulator's own samples (exact for i linear
        between them; |i| by the trapezoid)."""
        dt = np.diff(self.tt)
        w = self.W[rows]
        q = (0.5 * (np.abs(w[:, :-1]) + np.abs(w[:, 1:])) * dt).sum(axis=1)
        e = ((w[:, :-1] ** 2 + w[:, :-1] * w[:, 1:] + w[:, 1:] ** 2) * dt / 3.0).sum(axis=1)
        return q / periods, e / periods

    def _feature_sums(self, r, sg, cg) -> tuple:
        """Per grid interval: the charge and energy of the residual `r` (the
        waveform minus the grid's chord, at the samples `sg` inside the
        intervals `cg`; zero at the grid's points), exact for r linear between
        the points (|r| by the trapezoid)."""
        tg = self.tt[sg]
        lo = self.grid[cg].astype(float) * self.q
        hi = self.grid[cg + 1].astype(float) * self.q
        same = np.r_[cg[1:] == cg[:-1], False]
        tn = np.where(same, np.r_[tg[1:], 0.0], hi)
        rn = np.where(same, np.r_[r[1:], 0.0], 0.0)
        first = ~np.r_[False, cg[1:] == cg[:-1]]
        d0 = np.where(first, tg - lo, 0.0)
        dq = 0.5 * (np.abs(r) + np.abs(rn)) * (tn - tg) + 0.5 * np.abs(r) * d0
        de = (r * r + r * rn + rn * rn) * (tn - tg) / 3.0 + r * r * d0 / 3.0
        Q = np.zeros(self.n - 1)
        E = np.zeros(self.n - 1)
        np.add.at(Q, cg, dq)
        np.add.at(E, cg, de)
        return Q, E

    def feature_keep(self, periods=None) -> tuple:
        """`(keep, cell, ignored)`. Per pin and grid interval, a feature's
        height is the largest miss of the grid's chord at a simulator sample
        inside it, its residual that of the sequence's chord. A feature the
        refinement left above twice EVENT_TOL of its pin's scale (the finest
        level, or the budget) and that keeps less than FEATURE_KEEP of its
        height is unresolved: `keep` is the least share of its height an
        unresolved feature keeps (1.0: none), `cell` the largest step that
        would resolve the sharpest of them (a chord misses a kink of slope
        change db by up to db x cell / 4, so cell = 4 EVENT_TOL scale / db;
        None: none). With `periods` (the whole periods the window spans), an
        unresolved feature whose charge and energy beyond the grid's chord are
        both below NEGLIGIBLE of its pin's per period is left out of `keep`
        and `cell` and counted in `ignored` (`{n, largest}`, the largest by
        charge share: `{row, t, height, charge, charge_share,
        energy_share}`)."""
        ignored = {"n": 0, "largest": None}
        if not len(self.rows) or len(self.t) < 3:
            return 1.0, None, ignored
        sg, cg, fg = self._inside(self.grid)
        if not len(sg):
            return 1.0, None, ignored
        sp, cp, fp = self._inside(self.P)
        on_p = np.ones(len(self.t), bool)
        on_p[sp] = False
        pos = np.full(len(self.t), -1)
        pos[sp] = np.arange(len(sp))
        m = ~on_p[sg]
        pp = pos[sg[m]]
        keep, cell = 1.0, None
        blk = max(1, 4_000_000 // max(len(self.P), 1))
        for b0 in range(0, len(self.rows), blk):
            rb = self.rows[b0:b0 + blk]
            Vg, Vp = self.values(self.grid, rows=rb), self.values(self.P, rows=rb)
            Wb = self.W[np.ix_(rb, sg)]
            if periods:
                qpin, epin = self._pin_totals(rb, float(periods))
            for q in range(len(rb)):
                w = Wb[q]
                rg = w - (Vg[q, cg] + (Vg[q, cg + 1] - Vg[q, cg]) * fg)
                hgt = np.abs(rg)
                res = np.zeros(len(sg))
                res[m] = np.abs(w[m] - (Vp[q, cp[pp]] + (Vp[q, cp[pp] + 1] - Vp[q, cp[pp]])
                                        * fp[pp]))
                H = np.zeros(self.n - 1)
                R = np.zeros(self.n - 1)
                np.maximum.at(H, cg, hgt)
                np.maximum.at(R, cg, res)
                tol = EVENT_TOL * self.scale[b0 + q]
                bad = (R > 2.0 * tol) & (R > (1.0 - FEATURE_KEEP) * H)
                if not bad.any():
                    continue
                if periods:
                    Qf, Ef = self._feature_sums(rg, sg, cg)
                    qs = Qf / max(qpin[q], 1e-300)
                    es = Ef / max(epin[q], 1e-300)
                    small = bad & (qs <= NEGLIGIBLE) & (es <= NEGLIGIBLE)
                    if small.any():
                        ignored["n"] += int(small.sum())
                        c = int(np.flatnonzero(small)[np.argmax(qs[small])])
                        top = ignored["largest"]
                        if top is None or qs[c] > top["charge_share"]:
                            ignored["largest"] = {
                                "row": int(rb[q]), "t": float(self.t[0] + self.grid[c] * self.q),
                                "height": float(H[c]), "charge": float(Qf[c]),
                                "charge_share": float(qs[c]), "energy_share": float(es[c])}
                    bad &= ~small
                    if not bad.any():
                        continue
                keep = min(keep, float((1.0 - R[bad] / H[bad]).min()))
                # the sharpest kink among the unresolved samples
                k = sg[bad[cg] & (res > 2.0 * tol)]
                k = k[(k > 0) & (k < len(self.t) - 1)]
                if len(k):
                    wj = self.W[rb[q]]
                    sl = (wj[k + 1] - wj[k]) / np.maximum(self.tt[k + 1] - self.tt[k], 1e-300)
                    sr = (wj[k] - wj[k - 1]) / np.maximum(self.tt[k] - self.tt[k - 1], 1e-300)
                    db = float(np.abs(sl - sr).max())
                    if db > 0:
                        c = 4.0 * tol / db
                        cell = c if cell is None else min(cell, c)
        return keep, cell, ignored


def _stiff_tau(lu, Gf, Cd, pos) -> np.ndarray:
    """Per node position: the stiff part of the decaps' time constants as a
    current injected there sees them, `[(G + mC)^-1 G (G + mC)^-1 C 1]`, m = 2/h
    (`lu` factorises G + mC). In the modes of `G v = l C v` it is the Elmore
    delay `G^-1 C 1` with each mode weighted by `(l / (l + m))^2`: a mode
    much slower than the step (which the trapezoidal rule integrates well)
    drops out, one faster counts whole. Two back-substitutions for every pin."""
    if not Cd.any():
        return np.zeros(len(pos))
    y = lu.solve(Cd)
    z = lu.solve(Gf.dot(y))
    return np.array([abs(float(z[p])) if p >= 0 else 0.0 for p in pos])


def transient(nw, ground: str, taps, t, node_c=None, lumps=None,
              n_steps=None, method="trap", probes=None, log=print,
              periodic=False, max_passes=50, settle_tol=1e-4, em=None) -> dict:
    """Integrate `C dV/dt + G V = I(t)` and keep the worst excursion per node.

    Returns per-node worst |V - V_feed| and when it happened, traces at
    `probes` (at the grid's points), and the global worst. Only running
    maxima and a few traces are kept, so memory is O(nodes), not
    O(nodes x timesteps); the input is interpolated from the simulator's
    samples at each step, never stored resampled.

    `periodic`: the waveforms span a whole number of periods (a cc-grid
    window), so the state is taken to periodic steady state: the window is
    integrated again from the state the previous pass ended in, until a pass
    ends where it started (to `settle_tol` of its worst excursion), and that
    pass is the one reported (`passes`, `settled`). Without it the one pass
    starts from the DC solution at t0, start-up transient included, on the
    uniform grid alone (a lump faster than the step folded onto its node).

    Three details make the periodic passes right:

    * **The wrap is carried.** A pass ends in the state of the window's last
      sample, so the next one starts from the injection that state is
      consistent with (the last sample's), not the first sample's. Starting
      from the first sample's injection was a step in the input at every
      wrap: on nodes with little or no capacitance the trapezoidal rule's
      (-1)^n mode never decays, and a window whose ends differ by 0.1 % of
      the peak did not settle in 50 passes (an R-only grid with MOS decaps).
    * **A slow pass is restarted from the corrected mean.** Summing the
      trapezoidal steps over one pass gives `G Vbar = Ibar - C (V_N - V_0)/T`,
      so the mean of the periodic steady state is `G^-1 Ibar`, known in one
      back-substitution. When a pass contracts the error by less than 0.3
      (a decap time constant long against the window), the next starts from
      the end state shifted by its mean's error: each mode then contracts by
      `e^-x - (1 - e^-x)/x` (x = T/tau), at most 0.30, against `e^-x`.
    * **The steps follow the input and the decaps** (`StepSequence`): the
      waveforms' edges and spike tips are integrated in dyadic sub-steps down
      to their own resolution, the step after a change of slope is refined
      while the trapezoidal rule could ring on a decap faster than it, and
      the steps grow back geometrically. A decap lump is folded onto its node
      only when its access R*C is below a tenth of the finest step taken.
      `sub_steps` says what it cost; `peak_retention` is the least share of
      its own height any feature keeps; `feed_check` compares the feed's
      current with the load's own on the same steps (a passive network can
      only low-pass it: above it, the step rang).

    `em` (an `EmSpec`, trapezoidal only): also keep the branch currents of
    the grid for decap-aware EM, from the same steps (`_EmHook`), each pin's
    rms on the steps and on the simulator's own samples (`pins`: what the
    steps lose, like with like), and check the averages against the
    decap-free ones of that input (the pass is periodic when they agree to
    `em.avg_tol`).
    """
    import scipy.sparse as sp
    from scipy.sparse.linalg import splu
    if method not in METHODS:
        raise EmirError("EMIR-E513", f"method must be one of {METHODS}")
    if em is not None and method != "trap":
        raise EmirError("EMIR-E905", "decap-aware EM is integrated with the trapezoidal "
                        "rule only (backward Euler damps the peaks)")
    waved = [tp for tp in taps if tp.get("wave") is not None]
    if not waved:
        raise EmirError("EMIR-E422", "dynamic IR needs tap waveforms; the scalar tap path "
                         "carries no time information")
    t = np.asarray(t, dtype=float)
    W = np.array([tp["wave"] for tp in waved], dtype=float)
    if W.shape[1] != len(t):
        raise EmirError("EMIR-E905", f"tap waveforms have {W.shape[1]} points but the time "
                         f"base has {len(t)}")

    ng = len(nw.nodes)
    lumps = list(lumps or [])
    idx = nw.idx
    for L in lumps:
        if L["node"] not in idx:
            raise EmirError("EMIR-E905", f"decap lump on unknown node {L['node']!r}")
    n_steps = int(n_steps or default_steps(len(t)))
    subbed = periodic or em is not None
    feed_rows = {j for j, tp in enumerate(waved) if tp.get("feed")}
    rows = np.array([idx[tp["node"]] for tp in waved])
    wrap = float(np.abs(W[:, -1] - W[:, 0]).max()
                 / max(float(np.abs(W).max()), 1e-30)) if len(W) else 0.0
    seq = StepSequence(t, W, n_steps, skip=feed_rows, periodic=periodic)
    h = seq.h
    T = seq.T
    gi = idx[ground]
    base_c = np.zeros(ng) if node_c is None else np.asarray(node_c, dtype=float)

    def system(fold):
        """G and C of the grid with the lumps not in `fold` as branches to
        internal nodes (`fold`: their C straight onto their node)."""
        kept = [L for k, L in enumerate(lumps) if k not in fold]
        n = ng + len(kept)
        ga, gb, gv = list(nw.a), list(nw.b), list(nw.g)
        for k, L in enumerate(kept):
            ga.append(idx[L["node"]])
            gb.append(ng + k)
            gv.append(1.0 / max(L["r"], 1e-12))
        ga, gb, gv = np.array(ga), np.array(gb), np.array(gv)
        G = sp.coo_matrix(
            (np.concatenate([gv, gv, -gv, -gv]),
             (np.concatenate([ga, gb, ga, gb]),
              np.concatenate([ga, gb, gb, ga]))), shape=(n, n)).tocsr()
        cvec = np.zeros(n)
        cvec[:ng] = base_c
        for k in fold:
            cvec[idx[lumps[k]["node"]]] += lumps[k]["c"]
        for k, L in enumerate(kept):
            cvec[ng + k] = L["c"]
        if not cvec.any():
            raise EmirError("EMIR-E423", "no capacitance anywhere -- dynamic IR is the static "
                             "solve; supply an RC DSPF or a decap list")
        free = np.ones(n, bool)
        free[gi] = False
        fi = np.flatnonzero(free)
        Gf = G[fi][:, fi].tocsc()
        return {"fold": set(fold), "kept": kept, "n": n, "cvec": cvec, "fi": fi,
                "Gf": Gf, "Cd": cvec[fi].copy(), "luG": splu(Gf)}

    def lu_at(s, step):
        coef = (2.0 if method == "trap" else 1.0) / step
        return splu((sp.diags(s["Cd"] * coef) + s["Gf"]).tocsc())

    def taus(s, lu0):
        posmap = {int(j): q for q, j in enumerate(s["fi"])}
        pos = [posmap.get(int(idx[waved[j]["node"]]), -1) for j in seq.rows]
        return _stiff_tau(lu0, s["Gf"], s["Cd"], pos)

    tau = None
    if not subbed:
        # one pass on the uniform grid, from the DC solution: a lump whose own
        # R*C sits below the step reaches quasi-equilibrium within it, so its
        # C is folded straight onto its node (keeping it would add a stiff mode
        # the trapezoidal rule rings on rather than integrates)
        keep_pk = seq.values(seq.grid, rows=np.arange(len(W)))
        keep = float(np.min(np.abs(keep_pk).max(axis=1)
                            / np.maximum(np.abs(W).max(axis=1), 1e-30)))
        if keep < 0.99:
            notice("EMIR-W411", f"resampling to {n_steps} uniform steps kept only "
                   f"{keep:.1%} of the worst tap's peak current -- dynamic droop will "
                   f"be UNDERSTATED; raise n_steps", log)
        fold = {k for k, L in enumerate(lumps) if max(L["r"], 0.0) * L["c"] < h}
        sysm = system(fold)
        lu0 = lu_at(sysm, h)
        n_marginal = sum(1 for L in sysm["kept"] if L["r"] * L["c"] < 10.0 * h)
        if n_marginal:
            notice("EMIR-W412", f"{n_marginal} decap lump(s) have access R*C within "
                   f"10x of the {h:.3g} s timestep: neither resolved nor negligible. "
                   f"Raise n_steps or treat their droop contribution as approximate.", log)
    else:
        # the factorisations of the sub-step sizes share LU_BUDGET: a first
        # system (lumps with no access resistance folded) sizes them
        fold = {k for k, L in enumerate(lumps) if L["r"] <= 0.0}
        sysm = system(fold)
        lub = (sysm["luG"].L.nnz + sysm["luG"].U.nnz) * 12 + 16 * len(sysm["fi"])
        seq.set_depth(min(MAX_DEPTH, int(LU_BUDGET // max(lub, 1))))
        seq.refine()                                  # (a): the input's features
        # a lump's access R is invisible only below the finest step taken
        want = {k for k, L in enumerate(lumps)
                if L["r"] <= 0.0 or L["r"] * L["c"] < FOLD_FRAC * seq.s_min()}
        for _ in range(2):
            if want != sysm["fold"]:
                sysm = system(want)
            lu0 = lu_at(sysm, h)
            tau = taus(sysm, lu0)
            seq.refine(tau)                            # (b) and (c)
            want = {k for k in want if lumps[k]["r"] <= 0.0
                    or lumps[k]["r"] * lumps[k]["c"] < FOLD_FRAC * seq.s_min()}
            if want == sysm["fold"]:
                break
        if want != sysm["fold"]:
            sysm = system(want)
            lu0 = lu_at(sysm, h)
        seq.budget(tau, nodes=len(sysm["fi"]))
    folded_lumps = [lumps[k] for k in sorted(sysm["fold"])]
    lumps = sysm["kept"]
    n, cvec, fi, Gf, Cd, luG = (sysm[k] for k in ("n", "cvec", "fi", "Gf", "Cd", "luG"))
    folded = float(sum(L["c"] for L in folded_lumps))
    if folded and not subbed:
        log(f"[emir] {len(folded_lumps)} decap lump(s) ({folded * 1e15:.2f} fF) have "
            f"access R*C below the {h:.3g} s step -- folded onto their nodes as plain "
            f"capacitance")
    elif folded:
        log(f"[emir] {len(folded_lumps)} decap lump(s) ({folded * 1e15:.2f} fF) have "
            f"access R*C below a tenth of the finest step ({seq.s_min():.3g} s) -- folded "
            f"onto their nodes as plain capacitance")

    # ---- the sequence: its points, steps, levels and the input at each point
    P = seq.P
    N = len(P)
    tseq = seq.times()
    dts = seq.steps()
    lvl = seq.levels()
    ci, cf = seq.at()
    if periodic:
        # the wrap is carried (see above): every pass starts in the state of
        # the last sample, so its first injection is the last sample's
        ci, cf = ci.copy(), cf.copy()
        ci[0], cf[0] = ci[-1], cf[-1]
    on_grid = np.flatnonzero(P % seq.unit == 0)
    grid_at = np.full(N, -1)
    grid_at[on_grid] = P[on_grid] // seq.unit
    n_sub = int(N - n_steps)
    refined = np.unique(P[P % seq.unit != 0] // seq.unit)
    ignored = {"n": 0, "largest": None}
    if subbed:
        # whole periods of the window (`ccsim.taps_from_waves`): a feature is
        # judged against its pin's charge and energy per period
        periods = next((tp["window_periods"] for tp in waved
                        if tp.get("window_periods")), None) if periodic else None
        keep, need, ignored = seq.feature_keep(periods)
        if ignored["n"]:
            x = ignored["largest"]
            name = waved[x["row"]].get("name") or waved[x["row"]]["node"]
            notice("EMIR-W429", f"{ignored['n']} waveform feature(s) narrower than the "
                   f"finest sub-step ({seq.s_min():.3g} s) left unresolved and ignored: "
                   f"each carries below {NEGLIGIBLE:g} of its pin's charge and of its "
                   f"pin's integral of i^2 per period (the largest: {name} at "
                   f"{x['t']:.4g} s, {x['charge']:.3g} C, {x['charge_share']:.2g} of the "
                   f"pin's charge, {x['energy_share']:.2g} of its energy; height "
                   f"{x['height'] * 1e3:.4g} mA), so the droop, the averages and a branch's "
                   f"rms^2 move by no more than that share of the pin's; their own peak, "
                   f"for less than the finest step, is not in the decap-aware currents (a "
                   f"decap's strap near the pin may carry up to the feature's height for "
                   f"that long)", log)
        if keep < FEATURE_KEEP:
            notice("EMIR-W411", f"{n_steps} uniform steps, with their sub-steps, kept only "
                   f"{max(keep, 0.0):.1%} of a waveform feature's height (its largest "
                   f"miss from the grid's chord) -- the dynamic droop and decap-aware EM "
                   f"will be UNDERSTATED; raise --dyn-steps", log)
    else:
        need = None

    def column(k):
        i, f = ci[k], cf[k]
        if W.shape[1] < 2:
            return W[:, 0].copy()
        return W[:, i] * (1.0 - f) + W[:, i + 1] * f

    dc = np.zeros(n)
    for tp in taps:
        if tp.get("wave") is None:
            dc[idx[tp["node"]]] += tp["i_avg"]

    def inject_col(col):
        v = dc.copy()
        np.add.at(v, rows, col)
        return v[fi]

    lus = {0: lu0}

    def lu_level(level):
        if level not in lus:
            lus[level] = lu_at(sysm, h / (1 << int(level)))
        return lus[level]

    # the input's trapezoid mean over the sequence, and the mean of the
    # periodic steady state it implies: G Vbar* = Ibar (the weights scattered
    # back onto the simulator's samples, so the input is never stored dense)
    wseq = np.zeros(N)
    wseq[:-1] += 0.5 * dts / T
    wseq[1:] += 0.5 * dts / T
    om = np.zeros(len(t))
    if W.shape[1] >= 2:
        np.add.at(om, ci, wseq * (1.0 - cf))
        np.add.at(om, np.minimum(ci + 1, len(t) - 1), wseq * cf)
    else:
        om[0] = 1.0
    Ibar = dc.copy()
    np.add.at(Ibar, rows, W @ om)
    Vstar = luG.solve(Ibar[fi])

    I_first = inject_col(column(0))
    # start from the DC solution at t0 (periodic: of the carried last
    # sample): our own start-up transient would otherwise be
    # indistinguishable from the circuit's
    Vf = luG.solve(I_first)

    probes = [p for p in (probes or []) if p in idx]
    pidx = [int(np.flatnonzero(fi == idx[p])[0]) for p in probes
            if idx[p] in fi]
    coef = 2.0 if method == "trap" else 1.0

    hook = None
    if em is not None:
        hook = _EmHook(nw, idx, ground, fi, n, ng, lumps, cvec, T, em)

    def one_pass(Vf, hook=None):
        """Integrate the window once from state `Vf` at t0; returns the
        state at the end, the pass's running maxima and traces, and its
        trapezoid mean state."""
        worst = np.zeros(n)
        t_worst = np.zeros(n)
        trace = np.zeros((len(probes), n_steps))
        Vsum = np.zeros(len(fi))
        Ik = I_first
        GV = Gf.dot(Vf) if method == "trap" else None

        def point(Vf, Ik, GV, tk, dt):
            av = np.abs(Vf)
            hit = av > worst[fi]
            if hit.any():
                w = worst[fi]
                w[hit] = av[hit]
                worst[fi] = w
                tw = t_worst[fi]
                tw[hit] = tk
                t_worst[fi] = tw
            if hook is not None:
                hook.sample(Vf, Ik, GV, dt)

        if hook is not None:
            hook.begin()
        point(Vf, Ik, GV, tseq[0], None)
        for j, pi in enumerate(pidx):
            trace[j, 0] = Vf[pi]
        for k in range(1, N):
            s = dts[k - 1]
            Ik1 = inject_col(column(k))
            CV = Cd * Vf
            CV *= coef / s
            rhs = (Ik1 + Ik + CV - GV) if method == "trap" else (Ik1 + CV)
            Vp = Vf
            Vf = lu_level(lvl[k - 1]).solve(rhs)
            Ik = Ik1
            if method == "trap":
                GV = Gf.dot(Vf)
            if periodic:
                Vsum += 0.5 * s / T * (Vf + Vp)
            point(Vf, Ik, GV, tseq[k], s)
            g = grid_at[k]
            if g >= 0:
                for j, pi in enumerate(pidx):
                    trace[j, g] = Vf[pi]
        return Vf, worst, t_worst, trace, Vsum

    def avg_defect(V_end, V_start):
        """The branch averages' departure from the periodic steady state:
        the response of the grid to `C (V_N - V_0) / T` (one back-substitution;
        see the identity above)."""
        U = np.zeros(n)
        U[fi] = luG.solve(Cd * (V_end - V_start) / T)
        return hook.g * (U[hook.a] - U[hook.b])

    if hook is not None:
        U = np.zeros(n)
        U[fi] = Vstar
        # the decap-free branch averages of the same input, signed
        free_avg = hook.g * (U[hook.a] - U[hook.b])
        avg_scale = max(float(np.abs(free_avg).max(initial=0.0)), 1e-30)

    passes, settled, restart, prev_moved, defect = 0, True, "plain", None, None
    while True:
        start = Vf
        Vf, worst, t_worst, trace, Vbar = one_pass(Vf, hook)
        passes += 1
        if not periodic:
            break
        moved = float(np.abs(Vf - start).max()) if len(Vf) else 0.0
        ok = moved <= settle_tol * max(float(worst.max(initial=0.0)), 1e-30)
        if hook is not None:
            defect = float(np.abs(avg_defect(Vf, start)).max(initial=0.0)) / avg_scale
            ok = ok and defect <= em.avg_tol
        if ok:
            break
        if passes >= max_passes:
            settled = False
            break
        if prev_moved and moved > 0.3 * prev_moved:
            restart = "mean-corrected"
        prev_moved = moved
        if restart == "mean-corrected":
            Vf = Vf - (Vbar - Vstar)

    j = int(np.argmax(worst))
    log(f"[emir] dynamic IR: worst excursion {worst[j] * 1e3:.4f} mV at "
        f"{nw.nodes[j] if j < ng else f'decap{j - ng}'} at t={t_worst[j]:.4g} s "
        f"({n_steps} steps of {h:.4g} s, {method}"
        + (f"; {len(refined)} of them refined, {n_sub} sub-steps added, finest "
           f"{seq.s_min():.3g} s" if n_sub else "")
        + (f"; periodic steady state, pass {passes}" if periodic else "") + ")")
    lv = np.unique(lvl) if len(lvl) else np.zeros(0, int)
    out = {"worst": worst[:ng], "t_worst": t_worst[:ng],
           "worst_node": nw.nodes[j] if j < ng else None,
           "worst_V": float(worst[j]), "worst_t": float(t_worst[j]),
           "nodes": nw.nodes, "probes": probes, "trace": trace,
           "t": np.linspace(t[0], t[-1], n_steps),
           "h": float(h), "method": method, "n_steps": n_steps,
           "peak_retention": keep, "n_lumps": len(lumps),
           "c_total": float(cvec.sum()), "periodic": bool(periodic),
           "passes": passes, "settled": settled, "restart": restart,
           "wrap_mismatch": wrap,
           "sub_steps": {"intervals": int(len(refined)), "added": n_sub,
                         "finest_s": seq.s_min(), "depth": int(lv.max(initial=0)),
                         "levels": int(len(lv)), "max_depth": seq.D,
                         "dropped": int(seq.dropped),
                         "needed": int(seq.needed if seq.needed is not None else N),
                         "budget": int(getattr(seq, "cap", 0))},
           "feature_need": need,
           "features_ignored": ignored,
           "peak_retention_points": keep,
           "folded": list(folded_lumps)}
    if hook is None:
        return out
    br, lus_, caps, feed = (hook.br.as_dict(), hook.lu.as_dict(), hook.cap.as_dict(),
                            hook.feed.as_dict())
    defect_b = avg_defect(Vf, start)
    residual = float(np.abs(br["avg"] - (free_avg - defect_b)).max(initial=0.0)) / avg_scale
    out["em"] = {
        "branch": br, "lumps": list(lumps), "lump": lus_, "folded": list(folded_lumps),
        "cap_nodes": list(hook.cap_names), "cap_c": hook.cap_c, "cap": caps,
        "feed": {k: float(v[0]) for k, v in feed.items()},
        "free_avg_grid": free_avg,
        "avg_defect": float(np.abs(defect_b).max(initial=0.0)) / avg_scale,
        "avg_tol": em.avg_tol, "avg_identity_residual": residual,
        # every pin's rms on the steps against its rms on the simulator's own
        # samples, like with like (exact for a linear segment on both), and the
        # load's current the feed would deliver without the decaps, on the
        # same steps: what the step loses, and whether it rang
        "pins": _pin_stats(seq, W, ci, cf, tseq, T),
        "feed_free": _feed_free(seq, W, ci, cf, tseq, T, rows, dc, fi)}
    return out


def _seq_values(W, ci, cf, a, b):
    """The input of every row at the sequence's points a..b-1."""
    if W.shape[1] < 2:
        return np.repeat(W[:, :1], b - a, axis=1)
    i, f = ci[a:b], cf[a:b]
    return W[:, i] * (1.0 - f) + W[:, i + 1] * f


def _pin_stats(seq, W, ci, cf, tseq, T) -> dict:
    """Per kept pin, mean and rms (and the rms of its AC part, the waveform
    minus its mean) on the sequence's points as the solve takes them (the
    wrap carried) and on the simulator's own samples, each exact for a
    current linear between its points."""
    rws = seq.rows
    Wr = W[rws]
    out = {"names": [int(j) for j in rws]}
    for tag, (tt, getv) in {
            "seq": (tseq, lambda a, b: _seq_values(Wr, ci, cf, a, b)),
            "sim": (seq.t, lambda a, b: Wr[:, a:b])}.items():
        s1, s2 = np.zeros(len(rws)), np.zeros(len(rws))
        n = len(tt)
        step = max(2, 2_000_000 // max(len(rws), 1))
        prev = None
        for a in range(0, n, step):
            b = min(n, a + step)
            V = getv(a, b)
            if prev is not None:
                V = np.concatenate([prev, V], axis=1)
                ts = tt[a - 1:b]
            else:
                ts = tt[a:b]
            if V.shape[1] >= 2:
                d1, d2, _ = segment_sums(V[:, :-1], V[:, 1:], np.diff(ts) / T)
                s1 += d1.sum(axis=1)
                s2 += d2.sum(axis=1)
            prev = V[:, -1:]
        out[f"avg_{tag}"] = s1
        out[f"rms_{tag}"] = np.sqrt(np.maximum(s2, 0.0))
        out[f"ac_{tag}"] = np.sqrt(np.maximum(s2 - s1 * s1, 0.0))
    return out


def _feed_free(seq, W, ci, cf, tseq, T, rows, dc, fi) -> dict:
    """The current the feed delivers without the decaps on the sequence's
    points: what every pin injects at a node of the solve (and the DC taps),
    as the solve takes it. Its peak and rms (exact for a linear segment)."""
    inside = np.zeros(len(dc), bool)
    inside[fi] = True
    wsel = inside[rows].astype(float)
    dsum = float(dc[fi].sum())
    pk, s2 = 0.0, 0.0
    n = len(tseq)
    step = max(2, 2_000_000 // max(len(W), 1))
    prev = None
    for a in range(0, n, step):
        b = min(n, a + step)
        x = wsel @ _seq_values(W, ci, cf, a, b) + dsum
        if prev is not None:
            x = np.concatenate([[prev], x])
            ts = tseq[a - 1:b]
        else:
            ts = tseq[a:b]
        pk = max(pk, float(np.abs(x).max(initial=0.0)))
        if len(x) >= 2:
            s2 += float(segment_sums(x[:-1], x[1:], np.diff(ts) / T)[1].sum())
        prev = x[-1]
    return {"peak": pk, "rms": float(np.sqrt(max(s2, 0.0)))}


def dynamic_ir(dspf, net, nw, ground, taps, t, decap=None, max_lumps=64,
               n_steps=None, method="trap", probes=None,
               coupling_as_ground=True, proc=None, log=print,
               periodic=False) -> dict:
    """Assemble the RC model for one net and run the transient (`periodic`:
    see `transient`)."""
    C, cnote = grounded_caps(dspf, net, nw.idx,
                             coupling_as_ground=coupling_as_ground,
                             proc=proc, log=log)
    lumps, lnote = ([], "no added decap")
    if decap:
        lumps, lnote = lump_decap(decap, dspf.node_xy, nw.idx,
                                  max_lumps=max_lumps, log=log)
    # MOS decaps found in the netlist itself, each with its own access R
    if proc is not None:
        mos, mnote = mos_decap_lumps(dspf, net, proc, idx=nw.idx, log=log)
        if mos:
            lumps = list(lumps) + mos
            lnote = (lnote + "; " if lnote != "no added decap" else "") + mnote
        elif mnote:
            lnote = lnote + "; " + mnote
    r = transient(nw, ground, taps, t, node_c=C, lumps=lumps,
                  n_steps=n_steps, method=method, probes=probes, log=log,
                  periodic=periodic)
    r["cap_note"], r["decap_note"] = cnote, lnote
    return r


def droop_csv(res, dspf, path) -> str:
    """Per-node worst excursion and when it happened -- the worst-droop map as
    data, so a zoomable viewer can render it without a PNG in the way."""
    with open(path, "w", encoding="utf-8") as f:
        f.write("node,x_um,y_um,worst_mV,t_worst_s\n")
        order = np.argsort(-res["worst"])
        for i in order:
            n = res["nodes"][i]
            x, y = dspf.node_xy.get(n, (float("nan"), float("nan")))
            f.write(f"{n},{x:.4f},{y:.4f},{res['worst'][i] * 1e3:.6f},"
                    f"{res['t_worst'][i]:.6e}\n")
    return path
