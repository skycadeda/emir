"""Decap-aware EM: the branch currents of the dynamic solve, beside the
decap-free ones of the static solve, and the person's choice of which one the
verdict uses (`--em-basis`).

The analog picture. In the cc-grid route the devices are simulated on an ideal
rail, so a decoupling capacitor across that rail sees no dV/dt and carries no
current: every edge of the load current is charged to the grid from the feed.
That is a board with its bypass capacitors unsoldered. `--dynamic` already
integrates `C dV/dt + G V = I(t)` on the R grid with the DSPF's capacitance,
the capacitor devices and the MOS decaps (`dynir`); the same steps give every
grid resistor's current `i = g (V_a - V_b)`, and so its rms and peak with the
bypass capacitors soldered in: the trace from the feed sees the low-passed
current, and the capacitor's own leads (its straps) carry the AC.

Neither number is right on every segment. Decap-free reads the feed path high
and a decap's straps near zero; decap-aware rests on the decap model (`cox`,
the access resistance) and the extraction's capacitance, and on the open loop
like everything in cc-grid. So three bases (PyTorch picture: three reductions
of the same forward pass):

* ``decap-free`` -- the static superposition, the 1.x verdict;
* ``decap-aware`` -- the dynamic solve (avg is the static one: a capacitor
  carries no DC);
* ``worst`` -- per segment and metric the larger of the two: a segment
  passes only if it passes with and without its decaps. It reads high on
  every branch class measured, but it is not a guaranteed bound (the open
  loop, a per-net solve of a decap between two resistive rails).

The default with --dynamic is `em_basis.DEFAULT_BASIS`, the one place to
change it.

Numerics (measured and tested, `tests/test_decap.py`, docs/big-blocks.md):

* **the steps** follow the input and the decaps (`dynir.StepSequence`): the
  waveforms' edges and spike tips are integrated in dyadic sub-steps of the
  waveforms themselves, down to EVENT_TOL of each pin's own scale; the step
  after a change of slope is refined while the trapezoidal rule could ring on
  a decap faster than it; the steps grow back geometrically. Where that
  cannot be done (the finest level reached, the refinement's budget spent, or
  the feed reading above the load's own current: the rule rang), decap-aware
  EM is not computed (EMIR-W434) and the run says which --dyn-steps would do.
* **rms** is reduced exactly for a current linear between the solve's points;
  what the steps lose is measured like with like, every pin's AC rms on the
  steps against on the simulator's own samples (`pin_correction`), and the
  worst pin's factor scales every segment (1 to rounding when the steps
  resolve the input).
* **peak** is the largest over every point of the steps, sub-steps included.
* **avg** is the static one in every basis; the dynamic average is a check
  of the periodic steady state (`G Vbar = Ibar - C (V_N - V_0) / T`).

Screening, not sign-off.
"""
from __future__ import annotations

import numpy as np

# the bases and the default: `emir/em_basis.py`, the one place to change it
from .em_basis import BASES, DEFAULT_BASIS, NEEDS_DYNAMIC, SHORT  # noqa: F401 (re-exported)
from .errors import EmirError, notice

#: what each basis is, for people and for `emir next`'s choices
BASIS_TEXT = {
    "decap-free": "the static superposition: every edge of the load current drawn from "
                  "the feed through the grid, the decaps carry nothing (the 1.x verdict); "
                  "reads high on the feed path and low on a decap's straps",
    "decap-aware": "the dynamic solve: the decaps supply the fast part of the current, "
                   "the grid from the feed carries it low-passed and the straps carry the "
                   "decaps' AC; rests on the decap model (cap_models) and the extraction's "
                   "capacitance",
    "worst": "per segment and metric the larger of decap-free and decap-aware: a segment "
             "passes only if it passes with and without its decaps; on the feed path that "
             "is decap-free's numbers, on a decap's straps decap-aware's",
}
#: the steps' rms loss (`pin_correction`) above which a notice says so, and
#: above which the decap-aware value is not used alone near the warn fraction
#: (the larger of the two bases stands in)
CORR_WARN, CORR_STANDIN = 1.02, 1.10
#: the bases disagree when a verdict differs or a worst ratio by more than this
DISAGREE = 0.02
#: coupling capacitance (to quiet nets) above this share of what the solve
#: counts: the decap-aware numbers lean on the AC-ground assumption
COUPLING_SHARE = 0.25
#: an ESR fitted this far from the window's fundamental, either way, is flagged
ESR_FREQ_SPAN = 10.0
#: the periodic-steady-state check on the branch averages
AVG_TOL = 1e-4
#: how many decaps, and how many segments of the union table, the summary lists
TOP_DECAPS, TOP_ITEMS = 20, 10

#: machine-readable reasons a net's decap-aware EM was not computed
#: (`em_basis.fallback`), with the code that says so (None: W450 said it)
FALLBACKS = {
    "no-dynamic": None, "no-capacitance": None, "harmonic": None, "pieces": None,
    "no-feed": "EMIR-W430", "reference-not-feed": "EMIR-W431",
    "not-settled": "EMIR-W432", "window-not-periodic": "EMIR-W433",
    "step-too-coarse": "EMIR-W434",
}


def _quiet(*_a, **_k):
    pass


# ------------------------------------------------------------------ options


def add_option(group) -> None:
    """`--em-basis` (in the big-block option group of `emir run` and `check`)."""
    group.add_argument(
        "--em-basis", default=None, choices=BASES, metavar="BASIS",
        help="cc-grid with --dynamic: which currents the EM verdict uses: worst (per "
             "segment the larger of decap-free and decap-aware), decap-free (the static "
             "superposition, decaps carry nothing) or decap-aware (the dynamic solve with "
             f"the decaps); default {DEFAULT_BASIS}. Every basis's verdict is in the "
             "summary; a basis other than the default is the person's choice "
             "(EMIR-W437). decap-aware and worst need --dynamic (EMIR-E430)")


def requested(a) -> tuple:
    """`(basis, source)`: the basis asked for, and whether by option or default."""
    b = getattr(a, "em_basis", None)
    return (b, "option") if b else (DEFAULT_BASIS, "default")


def relevant(a) -> bool:
    """Whether the run's summary carries the EM basis (with --dynamic, or a
    basis given)."""
    return bool(getattr(a, "dynamic", False) or getattr(a, "em_basis", None))


def _blocker(a):
    """What keeps --dynamic from running with these options, as (option, code)."""
    if getattr(a, "abstract", None):
        return "--abstract", "EMIR-E811"
    if getattr(a, "scalars", False):
        return "--scalars", "EMIR-E550"
    if getattr(a, "currents", None) and not (getattr(a, "cc_sim", None)
                                             or getattr(a, "netlist", None)):
        return "--currents", "EMIR-E550"
    return None


def _e430_text(a) -> tuple:
    basis = a.em_basis
    block = _blocker(a)
    msg = (f"--em-basis {basis} needs the dynamic solve: decap-aware currents come from "
           f"--dynamic (cc-grid), which this run does not have")
    if block:
        msg += (f"; and --dynamic cannot run here: {block[0]} ({block[1]}). Drop "
                f"--em-basis (the verdict is then the static one)")
    return msg, block


def refuse(a) -> None:
    """Refuse a basis that needs --dynamic without it (EMIR-E430), before the
    input check, so --force and --no-check cannot run past it; note a basis
    given with --mode rcc (EMIR-I432)."""
    basis = getattr(a, "em_basis", None)
    if not basis:
        return
    if getattr(a, "mode", None) == "rcc":
        notice("EMIR-I432", f"--em-basis {basis} with --mode rcc: the RC simulation drives "
               f"the decaps and every branch current is measured, so the basis does not "
               f"apply (the summary says `used: measured`)")
        return
    if basis == "decap-free" or getattr(a, "dynamic", False):
        return
    msg, block = _e430_text(a)
    # {args}: the run's arguments without --fail-on-violation, which the
    # command the code gives (`emir check ... --dynamic`) does not take
    raise EmirError("EMIR-E430", msg, next_steps=[] if block else None,
                    args=getattr(a, "argv_text", None))


def check_options(a, rep) -> None:
    """The same as an input-check FAIL (`emir check`)."""
    basis = getattr(a, "em_basis", None)
    if not basis or basis == "decap-free" or getattr(a, "dynamic", False) \
            or getattr(a, "mode", None) == "rcc":
        return
    msg, block = _e430_text(a)
    extra = {"args": ""} if block else {}
    rep.fail("EMIR-E430", "options", msg,
             f"drop --em-basis ({block[0]} blocks --dynamic)" if block
             else "add --dynamic (or drop --em-basis)", **extra)


# ------------------------------------------------------------------ cap models

#: the cap model keys: capacitances (cox for a MOS decap, cj and cjsw for a
#: capacitor device; never negative, and not all zero), access resistances
#: (never negative), the fit's coefficients R = esr_a + esr_w W + esr_l / L
#: (esr_w and esr_l signed: a fit may fall with W or with 1/L) and the
#: frequency it was made at (positive). One check, EMIR-E317, in `emir check`
#: and in the dynamic solve under --no-check.
CAP_C = ("cox", "cj", "cjsw")
CAP_NONNEG = CAP_C + ("esr_a", "ron_sq")
CAP_SIGNED = ("esr_w", "esr_l")
CAP_RANGES = ("esr_w_range", "esr_l_range")


def _number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and bool(np.isfinite(v))


def _cap_problems(models) -> list:
    """`[(paths, message)]`: what makes `cap_models` entries unusable
    (EMIR-E317), one message per value (an entry with no capacitance: one for
    the entry), with the paths (`cap_models/<name>/<key>`) it refuses."""
    out = []
    if not isinstance(models, dict):
        return [((), f"cap_models must be a mapping of model names to entries, "
                     f"got {models!r}")]
    for name, m in sorted(models.items(), key=lambda kv: str(kv[0])):
        if m is None:
            continue                    # an empty entry: it sizes nothing (EMIR-W409)
        if not isinstance(m, dict):
            out.append(((f"cap_models/{name}",),
                        f"cap_models/{name} must be a mapping of keys to values (cox, "
                        f"ron_sq, esr_a ... or cj, cjsw), got {m!r}"))
            continue
        for k in CAP_NONNEG + CAP_SIGNED + ("esr_fit_freq",):
            v = m.get(k)
            if v is None:
                continue
            path = (f"cap_models/{name}/{k}",)
            if not _number(v):
                out.append((path, f"cap_models/{name}: `{k}` must be a number, got {v!r}"))
            elif k in CAP_NONNEG and v < 0:
                out.append((path, f"cap_models/{name}: `{k}` must be a non-negative number, "
                                  f"got {v!r}"))
            elif k == "esr_fit_freq" and v <= 0:
                out.append((path, f"cap_models/{name}: `esr_fit_freq` must be a positive "
                                  f"frequency (Hz), got {v!r}"))
        given = {k: m[k] for k in CAP_C if m.get(k) is not None}
        if given and all(_number(v) and v == 0 for v in given.values()):
            out.append((tuple(f"cap_models/{name}/{k}" for k in given),
                        f"cap_models/{name}: no capacitance ("
                        + ", ".join(f"`{k}` 0" for k in given)
                        + "): its devices would be sized at 0 F, which deletes their "
                        "decoupling; give the PDK's value, or leave the entry out"))
        for k in CAP_RANGES:
            v = m.get(k)
            if v is not None and not (isinstance(v, (list, tuple)) and len(v) == 2
                                      and all(_number(x) for x in v)
                                      and 0 <= v[0] <= v[1]):
                out.append(((f"cap_models/{name}/{k}",),
                            f"cap_models/{name}: `{k}` must be [low, high] in um, got {v!r}"))
    return out


def cap_model_problems(models: dict) -> list:
    """What makes `cap_models` entries unusable (EMIR-E317), one line per
    value (an entry with no capacitance: one line for the entry)."""
    return [msg for _paths, msg in _cap_problems(models)]


def refused_paths(models) -> set:
    """The `cap_models/<name>/<key>` paths EMIR-E317 refuses (the unit-slip
    check, EMIR-W321, says nothing more of them)."""
    return {p for paths, _msg in _cap_problems(models) for p in paths}


def check_cap_models(data: dict, rep) -> bool:
    """`emir check`: a FAIL (EMIR-E317) per unusable cap_models value.
    Returns whether one failed."""
    probs = cap_model_problems(data.get("cap_models") or {})
    for p in probs:
        rep.fail("EMIR-E317", "process", p, "correct it from the PDK's decap "
                 "characterisation (`emir schema process`)")
    return bool(probs)


def refuse_cap_models(proc) -> None:
    """The dynamic solve under --no-check: the same refusal (EMIR-E317), on
    the file's own values (the engine keeps only the numbers, `Process`, so
    a quoted "0.01" or `true` would otherwise pass as a number)."""
    if proc is None:
        return
    data = getattr(proc, "data", None)
    raw = data.get("cap_models") if isinstance(data, dict) else None
    probs = cap_model_problems(raw if raw is not None else
                               (getattr(proc, "cap_models", {}) or {}))
    if probs:
        raise EmirError("EMIR-E317", "; ".join(probs[:4]) + (" ..." if len(probs) > 4 else ""))


# ------------------------------------------------------------------ the model


def quiet_nets(d, a=None) -> set:
    """The nets whose far plate counts as an AC ground for decap-aware EM:
    the run's power nets (`--power-nets`, else by name)."""
    from .dspf import power_net_list
    from .layoutview import power_nets
    rails = power_net_list(a) if a is not None else None
    return set(rails or power_nets(sorted(d.nets)))


def capacitance(d, net: str, idx: dict, proc, quiet: set) -> dict:
    """The capacitance of the EM solve, per node, with its accounting.

    * wire C to ground, and coupling C to a quiet net (a rail): counted, the
      far plate an AC ground;
    * capacitor devices (sized by `cap_models`): counted, listed per node;
    * coupling C to any other net (a signal): NOT counted. In the CC
      simulation its current flows between the signal and the ideal rail, so
      no instance pin carries it; taking it as decap would low-pass the feed
      current with a capacitor whose far plate switches. Its share is reported.
    """
    from .dynir import device_cap
    models = getattr(proc, "cap_models", {}) or {}
    signal = np.zeros(len(idx))
    acc = {"wire_F": 0.0, "devices_F": 0.0, "coupling_quiet_F": 0.0,
           "coupling_signal_F": 0.0, "default_model": 0}
    devices, two_rail = {}, []
    for c in d.net_caps(net):
        i = idx.get(c["node"])
        if i is None:
            continue
        val, dev = c["c"], c["c"] is None
        if dev:
            val, how = device_cap(c, models)
            if val is None:
                continue
            acc["default_model"] += how == "default"
        far_net = None if c["to_ground"] else d.node_net.get(c["far"])
        if far_net is not None and far_net not in quiet:
            acc["coupling_signal_F"] += val
            signal[i] += val
            continue
        if dev:
            acc["devices_F"] += val
            devices.setdefault(c["node"], []).append(
                {"name": c["name"], "kind": "device", "c_F": float(val),
                 "model": c.get("model") or "default"})
            if far_net is not None and d.net_resistors(far_net):
                two_rail.append(c["name"])
        elif c["to_ground"]:
            acc["wire_F"] += val
        else:
            acc["coupling_quiet_F"] += val
            # a valued capacitor to another rail with resistance here (a MIM
            # decap between two meshes, or rail-to-rail coupling): its far
            # plate moves too, so the per-net solve reads it low (EMIR-W439)
            if far_net is not None and far_net != net and d.net_resistors(far_net):
                two_rail.append(c["name"])
    acc["signal_per_node"] = signal
    acc["devices"] = devices
    acc["two_rail"] = two_rail
    return acc


def mos_lumps(d, net: str, nw, proc, log=print) -> tuple:
    """MOS decaps on `net` as `R_access + C` lumps (`dynir.mos_decap_lumps`),
    with the d/s/b side split: the channel is contacted at both ends, so a
    decap whose drain and source are separate nodes of this net attaches C/2
    with 2 R at each (the parallel combination is unchanged), and the source
    strap carries its half. `(lumps, note, two_rail)`."""
    from .dspf import mos_decaps
    from .dynir import mos_decap_lumps
    lumps, note = mos_decap_lumps(d, net, proc, idx=nw.idx, log=log, em=True)
    by_name = {m["name"]: m for m in mos_decaps(d, net)}
    out, two_rail = [], []
    for L in lumps:
        m = by_name.get(L["name"])
        other = None
        if m is not None:
            other = m["body_net"] if m["gate_net"] == net else m["gate_net"]
            if other and d.net_resistors(other):
                two_rail.append(L["name"])
        if m is not None:
            L = {**L, "model": m["model"]}
        if (m is not None and m["gate_net"] != net and m["s"] != m["d"]
                and m["s"] in nw.idx and L["node"] == m["d"]):
            out.append({**L, "c": L["c"] / 2, "r": 2 * L["r"], "half": "d"})
            out.append({**L, "node": m["s"], "c": L["c"] / 2, "r": 2 * L["r"], "half": "s"})
        else:
            out.append(dict(L))
    return out, note, two_rail


# ------------------------------------------------------------------ the solve


def _feed_tap(taps):
    return next((t for t in taps if t.get("feed")), None)


def _window_periods(taps):
    """Whole periods the waveforms span (`ccsim.taps_from_waves`), or None."""
    for t in taps:
        if t.get("wave") is not None and "window_periods" in t:
            return t["window_periods"]
    return None


#: the fallbacks that leave a basis needing decap-aware EM unhonoured (no
#: capacitance is not one: decap-aware EM then is decap-free exactly)
LOST = ("no-feed", "reference-not-feed", "not-settled", "window-not-periodic",
        "step-too-coarse", "harmonic", "pieces")


def not_computed(r: dict, a, reason: str, message: str | None = None, log=print) -> None:
    """Record why a net's decap-aware EM was not computed (`em_basis.fallback`)
    and say so with the reason's code, unless W450 already did (no
    capacitance, a harmonic result, a net in pieces). Under a basis that needs
    decap-aware EM (worst or decap-aware) the net's verdict is then
    not on the basis asked for: the notice says so, the summary's
    `em_basis.honoured` is false and --fail-on-violation exits 1 (a fallback
    must never loosen the verdict behind the person's back)."""
    basis, source = requested(a)
    r["em_basis"] = {"requested": basis, "used": "decap-free", "source": source,
                     "fallback": reason, "fallback_reason": message}
    r["dynamic_em"] = None
    code = FALLBACKS.get(reason)
    d = getattr(r.get("network"), "dspf", None)
    n = decap_devices(d, r["net"]) if d is not None and r.get("net") else 0
    if n:
        uncovered(r, "not-computed", code or "EMIR-W450", n,
                  f"{n} decap device(s), and decap-aware EM was not computed ({reason}): the "
                  f"verdict is decap-free there, which gives their straps nothing, so they "
                  f"are not checked")
    if code is None:
        return
    tail = "the verdict uses decap-free there"
    if basis != "decap-free":
        tail += (f", NOT the {basis} basis " + ("(the default) " if source == "default" else "")
                 + "asked for: it does not cover a decap's straps (the summary's "
                   "em_basis.honoured is false; --fail-on-violation exits 1)")
    notice(code, f"net {r.get('net') or '?'}: decap-aware EM not computed: {message}; {tail}",
           log)


def not_honoured(res) -> list:
    """`[(net, em_basis)]`: the nets whose verdict is not on the basis the run
    asked for (worst or decap-aware, whose decap-aware EM could not be
    computed: `LOST`)."""
    out = []
    for net, r in (res.solved or {}).items():
        eb = r.get("em_basis") or {}
        if eb.get("requested") not in NEEDS_DYNAMIC:
            continue
        if eb.get("used") == "decap-free" and eb.get("fallback") in LOST:
            out.append((net, eb))
        elif eb.get("decaps_not_sized"):
            # decap-aware EM ran without the decaps cap_models cannot size:
            # their straps carry nothing, so the basis asked for does not
            # cover them (a cap model left empty must never read as a pass)
            ds = eb["decaps_not_sized"]
            out.append((net, {**eb, "fallback": "decaps-not-sized", "fallback_reason":
                              f"{ds['n']} decap device(s) that cap_models cannot size "
                              f"({', '.join(ds['models'])}) left out of the decap-aware "
                              f"solve: their straps carry nothing and are not checked",
                              "models": list(ds["models"])}))
    return out


def exit_reason(res) -> str | None:
    """Why --fail-on-violation exits 1 although no verdict is FAIL: the basis
    asked for was not computed on a net (None: it was, everywhere)."""
    lost = not_honoured(res)
    if not lost:
        return None
    eb = lost[0][1]
    return (f"EM basis {eb['requested']} not computed on "
            + ", ".join(f"{n} ({e['fallback']})" for n, e in lost[:4])
            + (" ..." if len(lost) > 4 else ""))


def decap_devices(d, net: str) -> int:
    """The decap devices on a net: MOS decaps and capacitor devices (sized
    or not)."""
    from .dspf import mos_decaps
    return len(mos_decaps(d, net)) + sum(1 for c in d.net_caps(net) if c.get("c") is None)


def uncovered(r: dict, reason: str, code: str | None, n: int, what: str,
              items=()) -> None:
    """Record on a net what its EM verdict does not cover of its decaps
    (`summary.not_checked`'s `decap_straps_not_covered`, which the report,
    the chart and the HTML show): `reason` no-dynamic (EMIR-W428), not-sized
    (EMIR-W409/W410), basis (decap-free chosen, EMIR-W436) or not-computed
    (a fallback)."""
    r.setdefault("decaps_uncovered", []).append(
        {"reason": reason, "code": code, "n": int(n), "what": what,
         "items": [str(x) for x in list(items)[:20]]})


def unsized_decaps(d, net: str, proc) -> dict:
    """The decap devices on `net` that `cap_models` cannot size: a MOS decap
    whose model (or `mos_default`) has no cox, or no W and L (EMIR-W409); a
    capacitor device with no model entry or no geometry (EMIR-W410).
    `{"n", "mos", "devices", "models"}`: the devices' names and the models
    that would size them (`default` / `mos_default` for a device whose DSPF
    names none)."""
    from .dspf import mos_decaps
    from .dynir import device_cap
    models = getattr(proc, "cap_models", {}) or {}
    mos, dev, names = [], [], set()
    for m in mos_decaps(d, net):
        mm = models.get(m["model"]) or models.get("mos_default")
        if not mm or not m.get("w") or not m.get("l") or not mm.get("cox"):
            mos.append(m["name"])
            names.add(m["model"] or "mos_default")
    for c in d.net_caps(net):
        if c.get("c") is None and device_cap(c, models)[0] is None:
            dev.append(c["name"])
            names.add(c.get("model") or "default")
    return {"n": len(mos) + len(dev), "mos": mos, "devices": dev, "models": sorted(names)}


def warn_unsized(d, nets, proc, rep, area: str = "process") -> None:
    """Before a run (`emir process check --dspf`, `emir check --dynamic`):
    a WARN per net whose decaps cap_models cannot size (EMIR-W409 for MOS
    decaps, EMIR-W410 for capacitor devices): the run would leave them out,
    and decap-aware EM would not check their straps."""
    fix = ("ask the person for each model's capacitance (cox in F/m^2 for a MOS decap; cj "
           "and cjsw for a capacitor) from the PDK's decap characterisation, with its source")
    for net in nets:
        u = unsized_decaps(d, net, proc)
        if not u["n"]:
            continue
        msg = (f"net {net}: {unsized_text(u)}: a --dynamic run leaves them out (the droop "
               f"reads high) and decap-aware EM gives their straps nothing, so those straps "
               f"would NOT be checked")
        if u["mos"]:
            rep.warn("EMIR-W409", area, msg, fix)
        else:
            rep.warn("EMIR-W410", area, msg, fix)


def check_dynamic(a, rep) -> None:
    """`emir check --dynamic` (and a run's input check): the decaps
    cap_models cannot size (EMIR-W409/W410) and a fitted access resistance
    that is negative at a decap's own geometry (EMIR-E317: the dynamic solve
    refuses it, so the check FAILs it before the run is spent)."""
    from .dynir import negative_fits
    parsed = getattr(rep, "parsed", None) or {}
    d, proc = parsed.get("dspf"), parsed.get("proc")
    if d is None or proc is None or getattr(a, "mode", None) == "rcc":
        return
    nets = parsed.get("nets") or []
    warn_unsized(d, nets, proc, rep)
    for net in nets:
        for msg in negative_fits(d, net, proc):
            rep.fail("EMIR-E317", "process", msg, "correct the fit (esr_a, esr_w, esr_l) "
                     "from the PDK's decap characterisation, or its range (esr_w_range, "
                     "esr_l_range) to the geometries it holds for")


def unsized_text(u: dict) -> str:
    """`unsized_decaps` in words."""
    return (f"{u['n']} decap device(s) that cap_models cannot size "
            f"({', '.join(u['models'])})")


def mark_unsized(r: dict, d, net: str, proc) -> None:
    """After the dynamic solve of a net: the decaps it left out because
    `cap_models` cannot size them. Their straps carry nothing in decap-aware
    EM, so they are not checked (`decaps_uncovered`), and a basis that needs
    decap-aware EM is not honoured there (`not_honoured`: decaps-not-sized,
    --fail-on-violation exits 1)."""
    u = unsized_decaps(d, net, proc)
    if not u["n"]:
        return
    if r.get("em_basis") is not None:
        r["em_basis"]["decaps_not_sized"] = {"n": u["n"], "models": u["models"]}
        if r["em_basis"].get("fallback") == "no-capacitance":
            # none could be sized: this entry says why, not the fallback's
            r["decaps_uncovered"] = [x for x in r.get("decaps_uncovered") or []
                                     if x["reason"] != "not-computed"]
    uncovered(r, "not-sized", "EMIR-W409" if u["mos"] else "EMIR-W410", u["n"],
              f"{unsized_text(u)}: left out of the decap-aware solve, their straps carry "
              f"nothing and are not checked; give each model's capacitance (cox for a MOS "
              f"decap, cj and cjsw for a capacitor) from the PDK's decap characterisation",
              u["mos"] + u["devices"])


def _no_dynamic(d, net: str, r: dict, log=print) -> None:
    """A cc-grid net with decap devices on a run without --dynamic (and no
    --em-basis): its verdict is the static one (decap-free), whose
    superposition charges every edge of the load to the feed and gives the
    decaps' straps nothing. The net says so (`em_basis.fallback:
    no-dynamic`, EMIR-W428): decap-aware EM needs --dynamic."""
    if r.get("mode") != "cc-grid" or "em_basis" in r or d is None:
        return
    n = decap_devices(d, net)
    if not n:
        return
    # the basis is decap-free because the run has no --dynamic, not by
    # default (the default, `DEFAULT_BASIS`, needs --dynamic): `source`
    # says so, and no output calls it the default
    r["em_basis"] = {"requested": "decap-free", "used": "decap-free",
                     "source": "no-dynamic", "fallback": "no-dynamic",
                     "fallback_reason": f"{n} decap device(s) on the net and the run has "
                                        f"no --dynamic: the static superposition gives "
                                        f"their straps nothing"}
    uncovered(r, "no-dynamic", "EMIR-W428", n,
              f"{n} decap device(s) and no --dynamic: the static verdict (decap-free) "
              f"gives their straps nothing, so they are not checked")
    notice("EMIR-W428", f"net {net!r}: {n} decap device(s), and the run has no --dynamic: "
           f"the verdict is the static one (decap-free), which draws every edge of the "
           f"load from the feed and gives a decap's straps nothing, so they are not "
           f"checked; --dynamic (the person's choice: a transient solve) adds decap-aware "
           f"EM, which covers them", log)


def _prepare(a, d, net: str, r: dict) -> None:
    """A net's `em_basis` on a run where it applies (--dynamic or
    --em-basis) and the net has none yet: rcc measures the decaps' currents
    (`used: measured`), DC currents and a cc-grid net without --dynamic have
    only the static solve (`used: decap-free`)."""
    if "em_basis" in r:
        return
    basis, source = requested(a)
    mode = r.get("mode") or "dc"
    used, why, text = (("measured", None, None) if mode == "rcc" else
                       ("decap-free", "dc", "DC currents have no time base") if mode == "dc"
                       else ("decap-free", "no-dynamic", "the run has no --dynamic"))
    r["em_basis"] = {"requested": basis, "used": used, "source": source,
                     "fallback": why, "fallback_reason": text}
    n = decap_devices(d, net) if why == "no-dynamic" and d is not None else 0
    if n:
        # --em-basis given without --dynamic (no W428: the person chose the
        # basis); the straps are still not checked
        uncovered(r, "no-dynamic", None, n,
                  f"{n} decap device(s) and no --dynamic: the static verdict "
                  f"(decap-free) gives their straps nothing, so they are not checked")


def after_net(a, d, net: str, r: dict, log=print) -> None:
    """One net as soon as it is solved, before its HTML page, the run's chart
    and the viewer data are written, so each of them names the net's EM basis
    and what it does not cover (`_prepare` with --dynamic or --em-basis, else
    `_no_dynamic`)."""
    if relevant(a):
        _prepare(a, d, net, r)
    else:
        _no_dynamic(d, net, r, log)


def after_analyse(res, log=print) -> None:
    """After the run, the nets `after_net` did not see (a hierarchical run):
    a cc-grid net with decap devices and no --dynamic (EMIR-W428)."""
    a = res.args
    if relevant(a) or not res.solved:
        return
    for net, r in res.solved.items():
        _no_dynamic(res.dspf, net, r, log)


def em_spec(acc: dict, lumps: list):
    """What the transient keeps for decap-aware EM: the capacitor current of
    the nodes holding capacitor devices or decap lumps (a lump folded onto its
    node is reported as its share of that node's capacitor current)."""
    from .dynir import EmSpec
    cap_nodes = sorted(set(acc["devices"]) | {L["node"] for L in lumps})
    return EmSpec(avg_tol=AVG_TOL, cap_nodes=cap_nodes)


#: the most --dyn-steps a step-too-coarse refusal suggests (EMIR-W434): ten
#: times the default rule's most (200000), a long run on a big net already. A
#: feature that needs more is said, and no --dyn-steps given (a smaller one
#: would only repeat the refusal). A design choice, not a measurement
DYN_STEPS_MAX = 2_000_000


def _ignored_row(x):
    """The largest negligible feature (EMIR-W429) for the summary, or None."""
    if not x:
        return None
    return {k: x[k] for k in ("t", "height", "charge", "charge_share", "energy_share")}


def step_checks(run: dict):
    """Why the dynamic solve's steps cannot carry decap-aware EM, as `(why,
    the --dyn-steps that would)`, or None. Three ways (`dynir.transient`):
    a feature of the waveforms keeps less than FEATURE_KEEP of its own height
    on the steps (the finest sub-step reached, or the refinement's budget
    spent), and the feed's current reads above the load's own on the same
    steps (a passive RC network only low-passes it: the trapezoidal rule
    rang on a decap faster than the step). A feature with negligible charge
    and energy never counts (`dynir.NEGLIGIBLE`, EMIR-W429). The suggestion
    resolves it, never less (a smaller one could repeat itself: at 200000
    steps the old one did); above DYN_STEPS_MAX it is None and the reason
    says so."""
    from .dynir import FEATURE_KEEP, FEED_TOL, NEGLIGIBLE
    n = int(run["n_steps"])
    sub = run["sub_steps"]
    if sub.get("dropped"):
        need = max(2 * n, int(np.ceil((sub["needed"] - n) / 3.0)) + 1)
        return (f"the steps needed {sub['needed']} points to resolve the waveforms' edges, "
                f"more than the refinement's budget ({n + sub.get('budget', 3 * n)}); "
                f"{sub['dropped']} interval(s) kept the grid's step", need)
    keep = run["peak_retention_points"]
    if keep < FEATURE_KEEP:
        # the grid's step that puts the cell the sharpest unresolved kink needs
        # at the finest level the net's factorisations allow
        cell = run.get("feature_need")
        span = float(run["h"]) * (n - 1)
        k = 1
        if cell:
            k = max(1, int(np.ceil(np.log2(float(run["h"]) / (cell * (1 << sub["max_depth"]))))))
        need = (n - 1) * (1 << k) + 1
        why = (f"the step kept only {max(keep, 0.0):.1%} of a waveform feature's own height "
               f"(below {FEATURE_KEEP:.0%}) with sub-steps down to "
               f"{sub['finest_s']:.3g} s over a {span:.3g} s window")
        if need > DYN_STEPS_MAX:
            return (why + f"; resolving it would take --dyn-steps {need}, more than "
                    f"{DYN_STEPS_MAX} (not suggested): the feature carries more than "
                    f"{NEGLIGIBLE:g} of its pin's charge or energy per period, so it is "
                    f"not ignored; ask the person whether it is real (a simulator's glitch?)",
                    None)
        return why, need
    em = run.get("em") or {}
    ff, fd = em.get("feed_free"), em.get("feed")
    if ff and fd:
        rp = fd["peak"] / max(ff["peak"], 1e-300)
        rr = fd["rms"] / max(ff["rms"], 1e-300)
        if rp > 1.0 + FEED_TOL or rr > 1.0 + FEED_TOL:
            return (f"the feed's current read {(max(rp, rr) - 1) * 100:.1f} % above the load's "
                    f"own on the same steps (a passive RC network only low-passes it): the "
                    f"trapezoidal rule rang on a decap faster than the step", 4 * (n - 1) + 1)
    return None


def solve(d, net: str, r: dict, ground: str, taps, time, proc, a, log=print) -> dict:
    """The dynamic droop of one cc-grid net (what `dynir.dynamic_ir` returns)
    and, from the same steps, its decap-aware EM: the three bases, the basis
    the verdict uses swapped into `r` (`clusters`, `worst`, `max_ratio`, `Ie`
    and their notes), and `r["dynamic_em"]` for the summary."""
    import time as _time

    from .dynir import grounded_caps, transient
    t_start = _time.time()
    r["net"] = net
    nw = r["network"]
    basis, source = requested(a)
    refuse_cap_models(proc)
    # ---- the RC model of the droop (as dynamic_ir), with the MOS decaps
    # split at both channel ends
    C, cnote = grounded_caps(d, net, nw.idx, coupling_as_ground=True, proc=proc, log=log,
                             em=True)
    lumps, lnote, two_mos = mos_lumps(d, net, nw, proc, log=log) if proc is not None \
        else ([], "", [])
    # a decap the self-consistent loop simulated (its pin fed by the split, its
    # current in the reported pass's result) already loads the grid through
    # that pin: its lump would count its charge twice
    sim = set(r.get("_loop_simulated_decaps") or ())
    if sim and lumps:
        lumps = [L for L in lumps if L.get("name") not in sim]
        lnote = (lnote + "; " if lnote else "") + (
            f"{len(sim)} of them simulated by the self-consistent loop: their current is "
            f"their pins', not added again")
    lnote = lnote or "no added decap"
    acc = capacitance(d, net, nw.idx, proc, quiet_nets(d, a))
    feed = _feed_tap(taps)
    periods = _window_periods(taps)
    acc["periods"] = periods
    why = None
    if feed is None:
        why = ("no-feed", "the net has no port, so there is no feed for the decaps' "
                          "current to be drawn from")
    elif feed["node"] != ground:
        why = ("reference-not-feed", f"the solve's reference ({ground}) is not the feed "
               f"({feed['node']}): the decaps' charge would be drawn from the wrong node; "
               f"drop --ground")
    elif periods is None:
        why = ("window-not-periodic", "the window is not a whole number of periods (no "
               "period given or detected), so the periodic steady state the decaps reach "
               "is that of another signal; give --period")
    spec = None if why else em_spec(acc, lumps)
    same = acc["coupling_signal_F"] == 0.0
    n_steps = getattr(a, "dyn_steps", None)
    dyn = transient(nw, ground, taps, time, node_c=C, lumps=lumps, n_steps=n_steps,
                    periodic=True, log=log, em=spec if same else None)
    dyn["cap_note"], dyn["decap_note"] = cnote, lnote
    if why:
        not_computed(r, a, *why, log=log)
        dyn["em_line"] = _console_not(r)
        return dyn
    run = dyn
    if not same:
        # coupling to signal nets is decap for the droop's convention, not for
        # EM (`capacitance`): the EM solve has its own model
        C_em = np.maximum(C - acc["signal_per_node"], 0.0)
        run = transient(nw, ground, taps, time, node_c=C_em, lumps=lumps, n_steps=n_steps,
                        periodic=True, log=_quiet, em=spec)
    if not run["settled"]:
        not_computed(r, a, "not-settled",
                     f"the RC state did not reach periodic steady state in {run['passes']} "
                     f"passes (a decap time constant long against the window): simulate "
                     f"more periods", log=log)
        dyn["em_line"] = _console_not(r)
        return dyn
    coarse = step_checks(run)
    if coarse:
        why, need = coarse
        not_computed(r, a, "step-too-coarse",
                     f"{why}: rerun with --dyn-steps {need}" if need else why, log=log)
        r["em_basis"]["dyn_steps"] = need
        dyn["em_line"] = _console_not(r)
        return dyn
    em = _bases(d, net, r, ground, taps, proc, a, run, acc, lumps, two_mos, same,
                log=log)
    em["numerics"]["seconds"] = round(_time.time() - t_start, 3)
    em["numerics"]["steps_from"] = "--dyn-steps" if n_steps else "default"
    dyn["em_line"] = _console(r)
    return dyn


# ------------------------------------------------------------------ the bases


class _WarnAt:
    """The process with another warn fraction: `em_clusters` then lists every
    checked segment at or above it with its ratios, by the same arithmetic as
    the verdict's (0: every checked segment)."""

    def __init__(self, proc, warn_fraction=0.0):
        self._p = proc
        self.warn_fraction = float(warn_fraction)

    def __getattr__(self, k):
        return getattr(self._p, k)


class _Sub:
    """A view of a network holding some of its resistors (for `em_clusters`)."""

    def __init__(self, nw, sel):
        self.res = [nw.res[i] for i in sel]
        self.net = nw.net


def segment_ratios(nw, Ie: dict, proc, lmap: dict, sel) -> dict:
    """`{branch index: (governing metric, its ratio, {metric: ratio})}` for
    the branches `sel`, from the currents `Ie` (one basis), with the verdict's
    own arithmetic (`dspf.em_clusters`). A branch that is not checked (no
    layer match, no width) is left out."""
    from .dspf import em_clusters
    sel = [int(i) for i in sel]
    if not sel:
        return {}
    sub = _Sub(nw, sel)
    Ie_s = {m: np.asarray(v)[sel] for m, v in Ie.items() if v is not None}
    cl, _ = em_clusters(sub, Ie_s, _WarnAt(proc), lmap)
    pos = {nw.res[i]["name"]: i for i in sel}
    return {pos[c["name"]]: (c["metric"], float(c["ratio"]),
                             {k: float(v) for k, v in c["ratios"].items()})
            for c in cl if c["name"] in pos}


def _basis_result(nw, Ie, proc, lmap, r) -> dict:
    from .dspf import _verdict, em_clusters
    notes = {}
    cl, mr = em_clusters(nw, Ie, proc, lmap, notes=notes)
    v = _verdict({"clusters": cl, "no_width": r.get("no_width")}, proc)
    return {"clusters": cl, "worst": cl[0] if cl else None, "max_ratio": mr, "Ie": Ie,
            "bidirectional": notes["bidirectional"], "via_crowding": notes["via_crowding"],
            "verdict": v}


def _summary_basis(b: dict) -> dict:
    w = b["worst"]
    return {"verdict": b["verdict"],
            "max_ratio": {k: float(v) for k, v in b["max_ratio"].items()},
            "governing_metric": w["metric"] if w else None,
            "worst": ({k: (float(w[k]) if k == "ratio" else w[k])
                       for k in ("name", "layer", "kind", "metric", "ratio")} if w else None),
            "n_violations": len(b["clusters"])}


def _bases(d, net, r, ground, taps, proc, a, run, acc, lumps, two_mos, same,
           log=print) -> dict:
    """The three bases from the settled pass, the corrections, the swap."""
    nw = r["network"]
    lmap = r["layer_map"]
    em = run["em"]
    br = em["branch"]
    free = {k: np.asarray(v) for k, v in r["Ie"].items()}
    # ---- what the steps lose, like with like: every pin's AC rms (the
    # waveform minus its mean) on the steps against on the simulator's own
    # samples, both exact for a current linear between points. The steps
    # resolve every feature to EVENT_TOL of its pin, so the factor is 1 to
    # rounding unless the refinement ran short; the worst pin's factor scales
    # every segment (a segment's own decap-free factor would measure the
    # static rms's trapezoid-of-x^2 convention, which no step removes)
    c_net = pin_correction(em["pins"])
    c_used = np.full(len(nw.res), c_net)
    aware_rms = br["rms"] * c_used
    aware_abs = br["absavg"] * c_used
    aware_pk = br["peak"]
    # ---- notices on the correction, on the segments that matter (at or above
    # half the warn fraction under either basis)
    from .dspf import em_clusters
    names = {x["name"]: i for i, x in enumerate(nw.res)}
    tentative = {"avg": free["avg"], "rms": aware_rms, "peak": aware_pk,
                 "absavg": aware_abs}
    half = _WarnAt(proc, 0.5 * proc.warn_fraction)
    near = sorted({names[c["name"]] for Ie in (free, tentative)
                   for c in em_clusters(nw, Ie, half, lmap)[0]})
    standin = [i for i in near if c_used[i] > CORR_STANDIN]
    hi_c = [i for i in near if CORR_WARN < c_used[i] <= CORR_STANDIN]
    if standin:
        s = np.asarray(standin)
        aware_rms[s] = np.maximum(free["rms"][s], aware_rms[s])
        aware_abs[s] = np.maximum(free["absavg"][s], aware_abs[s])
    Ie_aw = {"avg": free["avg"], "rms": aware_rms, "peak": aware_pk, "absavg": aware_abs}
    Ie_wo = {m: np.maximum(free[m], Ie_aw[m]) for m in ("avg", "rms", "peak", "absavg")}
    bases = {"decap-free": _basis_result(nw, free, proc, lmap, r),
             "decap-aware": _basis_result(nw, Ie_aw, proc, lmap, r),
             "worst": _basis_result(nw, Ie_wo, proc, lmap, r)}
    basis, source = requested(a)
    used = basis
    # ---- the union of the bases' listed segments, each with every basis's
    # own ratio (computed from that basis's currents, never looked up)
    union = []
    for b in BASES:
        for c in bases[b]["clusters"][:50]:
            if c["name"] not in union:
                union.append(c["name"])
    rows = _rows(nw, bases, proc, lmap, [names[x] for x in union])
    for b in BASES:
        for c in bases[b]["clusters"]:
            if c["name"] in rows:
                c["by_basis"] = dict(rows[c["name"]],
                                     decided_by=(rows[c["name"]]["decided_by"]
                                                 if b == "worst" else b))
    # ---- segments the decap-free verdict does not cover: at or above the
    # warn fraction with their decaps, and higher than without
    aw = [names[c["name"]] for c in bases["decap-aware"]["clusters"]]
    fr = segment_ratios(nw, free, proc, lmap, aw)
    higher = []
    for c in bases["decap-aware"]["clusters"]:
        i = names[c["name"]]
        f = fr.get(i)
        if f is not None and (c["ratio"] > f[1] * (1 + 1e-9) or np.isnan(c["ratio"])):
            higher.append((c, {"ratio_decap_free": f[1], "ratio_decap_aware": c["ratio"]}))
    # ---- the swap: every consumer (console, summary, verdict, HTML, viewer)
    # follows the basis without knowing about it
    r["em_free"] = {k: r.get(k) for k in ("clusters", "worst", "max_ratio", "Ie",
                                          "bidirectional", "via_crowding")}
    pick = bases[used]
    for k in ("clusters", "worst", "max_ratio", "Ie", "bidirectional", "via_crowding"):
        r[k] = pick[k]
    r["metric_note"] = (r.get("metric_note") or "") + \
        f"; EM basis {used}" + (" (the default)" if source == "default" else "") + \
        ": " + SHORT[used]
    r["em_basis"] = {"requested": basis, "used": used, "source": source,
                     "fallback": None, "fallback_reason": None}
    # ---- the decap currents
    decaps = _decap_rows(em, acc, lumps)
    numer = {
        "n_steps": run["n_steps"], "step_s": run["h"], "steps_from": None,
        "peak_retention": run["peak_retention_points"],
        "wrap_mismatch": run["wrap_mismatch"],
        "passes": run["passes"], "settled": run["settled"], "restart": run["restart"],
        "avg_check": {"max_rel": em["avg_defect"], "tolerance": em["avg_tol"],
                      "passed": bool(em["avg_defect"] <= em["avg_tol"])},
        "avg_identity_residual": em["avg_identity_residual"],
        "resample_avg_error": _resample_avg_error(em["pins"]),
        "correction_max": {"rms": float(c_used.max(initial=1.0))},
        "n_corrected": len(hi_c), "n_stand_in": len(standin),
        "sub_steps": dict(run["sub_steps"]), "feed_check": _feed_check(em),
        # negligible features left unresolved (EMIR-W429): how many, the largest
        "features_ignored": {"n": run["features_ignored"]["n"],
                             "largest": _ignored_row(run["features_ignored"]["largest"])},
        "separate_solve": not same, "seconds": None}
    feed = _feed_numbers(taps, em, c_net)
    # capacitance on the feed node itself (the solve's fixed node) carries no
    # current: it is reported apart, not counted (EMIR-I430's "counted")
    inert = _on_feed(nw, ground, acc, lumps)
    cap_tot = (acc["wire_F"] + acc["devices_F"] + acc["coupling_quiet_F"]
               + sum(L["c"] for L in lumps)) - inert
    out = {
        "bases": {b: _summary_basis(bases[b]) for b in BASES},
        "higher_aware": {"n": len(higher), "worst": (
            {"name": higher[0][0]["name"], "layer": higher[0][0]["layer"],
             "kind": higher[0][0]["kind"],
             "ratio_decap_free": higher[0][1]["ratio_decap_free"],
             "ratio_decap_aware": higher[0][1]["ratio_decap_aware"]} if higher else None)},
        "feed": feed,
        "decaps": decaps,
        "capacitance": {"wire_F": acc["wire_F"], "devices_F": acc["devices_F"],
                        "mos_F": float(sum(L["c"] for L in lumps)),
                        "coupling_quiet_F": acc["coupling_quiet_F"],
                        "coupling_signal_excluded_F": acc["coupling_signal_F"],
                        "coupling_share": (acc["coupling_quiet_F"] / cap_tot
                                           if cap_tot > 0 else 0.0),
                        "counted_F": cap_tot, "on_feed_F": inert,
                        "default_model": acc["default_model"],
                        "two_rail": len(two_mos) + len(acc["two_rail"])},
        "items": [dict(name=n, **_where(nw, n, lmap), **rows[n]) for n in
                  sorted(rows, key=lambda n: -max(_nz(rows[n]["ratio_decap_free"]),
                                                  _nz(rows[n]["ratio_decap_aware"])))
                  ][:TOP_ITEMS],
        "numerics": numer,
    }
    r["dynamic_em"] = out
    r["_em_rows"] = rows
    _notices(r, net, out, used, source, higher, hi_c, standin, c_used, run, a, acc,
             two_mos, lumps, proc, log)
    return out


def _on_feed(nw, ground, acc, lumps) -> float:
    """The decap capacitance on the solve's fixed node (the feed): it sees no
    dV/dt and carries nothing. Wire capacitance there is not separated."""
    c = sum(it["c_F"] for it in acc["devices"].get(ground, []))
    c += sum(L["c"] for L in lumps if L["node"] == ground)
    return float(c)


def _rows(nw, bases, proc, lmap, sel) -> dict:
    """`{segment: {ratio_<basis>, metric_<basis>, decided_by}}` for the
    branches `sel`, every basis's ratio from that basis's own currents."""
    per = {b: segment_ratios(nw, bases[b]["Ie"], proc, lmap, sel) for b in BASES}
    rows = {}
    for i in sel:
        f, w_, wo = per["decap-free"].get(i), per["decap-aware"].get(i), per["worst"].get(i)
        if not f:
            continue
        decided = "decap-aware" if (w_ and (w_[1] > f[1] or np.isnan(w_[1]))) \
            else "decap-free"
        rows[nw.res[i]["name"]] = {
            "ratio_decap_free": f[1], "metric_decap_free": f[0],
            "ratio_decap_aware": w_[1] if w_ else None,
            "metric_decap_aware": w_[0] if w_ else None,
            "ratio_worst": wo[1] if wo else None, "metric_worst": wo[0] if wo else None,
            "decided_by": decided}
    return rows


def _nz(v):
    return -1.0 if v is None else (float("inf") if np.isnan(v) else v)


def _where(nw, name, lmap) -> dict:
    r = next(x for x in nw.res if x["name"] == name)
    return {"layer": (lmap.get(r.get("layer")) or (None, r.get("layer")))[1],
            "bbox_um": [float(v) for v in r["bbox"]] if r.get("bbox") else None}


def pin_correction(pins: dict) -> float:
    """The worst pin's rms loss on the steps, like with like: its AC rms on
    the simulator's own samples over its AC rms on the steps (at least 1),
    over the pins whose AC rms is at least 1e-3 of the largest."""
    ac_sim, ac_seq = np.asarray(pins["ac_sim"]), np.asarray(pins["ac_seq"])
    if not len(ac_sim):
        return 1.0
    big = ac_sim >= 1e-3 * max(float(ac_sim.max(initial=0.0)), 1e-300)
    big &= ac_seq > 0
    if not big.any():
        return 1.0
    return float(max(1.0, float((ac_sim[big] / ac_seq[big]).max())))


def _resample_avg_error(pins: dict) -> float:
    """The worst pin's average on the steps against on the simulator's own
    samples, relative to the largest pin average (the steps' charge error,
    reported, not used: EM avg is the static one)."""
    a_sim, a_seq = np.asarray(pins["avg_sim"]), np.asarray(pins["avg_seq"])
    if not len(a_sim):
        return 0.0
    s = max(float(np.abs(a_sim).max(initial=0.0)), 1e-300)
    return float(np.abs(a_seq - a_sim).max(initial=0.0) / s)


def _feed_check(em: dict) -> dict:
    """The feed's current with the decaps against the load's own on the same
    steps: a passive RC network only low-passes it (`step_checks`)."""
    ff, fd = em["feed_free"], em["feed"]
    return {"peak_ratio": fd["peak"] / max(ff["peak"], 1e-300),
            "rms_ratio": fd["rms"] / max(ff["rms"], 1e-300)}


def _feed_numbers(taps, em, c_net: float) -> dict:
    """The current the feed delivers, in both bases: decap-free exact on the
    simulator's samples (the feed tap), decap-aware from the dynamic solve
    (rms scaled by the steps' loss, peak over every point)."""
    ft = _feed_tap(taps)
    f = em["feed"]
    return {"node": ft["node"],
            "i_avg_A": float(-sum(t["i_avg"] for t in taps if not t.get("feed"))),
            "i_rms_A": {"decap-free": float(ft["i_rms"]),
                        "decap-aware": float(f["rms"] * c_net)},
            "i_peak_A": {"decap-free": float(ft["i_peak"]), "decap-aware": float(f["peak"])}}


def _decap_rows(em, acc, lumps) -> dict:
    """Every decap's current (not EM-checked: a device, not metal): the
    resolved lumps from their access branch, the capacitor devices and the
    folded lumps as their share of their node's capacitor current."""
    rows = []
    lu = em["lump"]
    for k, L in enumerate(em["lumps"]):
        row = {"name": L.get("name") or f"lump{k}", "kind": "mos", "node": L["node"],
               "c_F": float(L["c"]), "r_ohm": float(L["r"]),
               "r_model": L.get("r_model"), "folded": False,
               "i_avg_A": float(lu["avg"][k]), "i_rms_A": float(lu["rms"][k]),
               "i_peak_A": float(lu["peak"][k])}
        if L.get("half"):
            row["half"] = L["half"]
        rows.append(row)
    cap = em["cap"]
    pos = {n: q for q, n in enumerate(em["cap_nodes"])}
    folded = em["folded"]
    for node, items in sorted(acc["devices"].items()):
        q = pos.get(node)
        if q is None:
            continue
        ctot = float(em["cap_c"][q])
        for it in items:
            sh = it["c_F"] / ctot if ctot > 0 else 0.0
            rows.append({"name": it["name"], "kind": "device", "node": node,
                         "c_F": it["c_F"], "r_ohm": None, "r_model": it["model"],
                         "folded": False, "i_avg_A": float(cap["avg"][q] * sh),
                         "i_rms_A": float(cap["rms"][q] * sh),
                         "i_peak_A": float(cap["peak"][q] * sh)})
    for L in folded:
        q = pos.get(L["node"])
        if q is None:
            continue
        ctot = float(em["cap_c"][q])
        sh = L["c"] / ctot if ctot > 0 else 0.0
        rows.append({"name": L.get("name") or "lump", "kind": "mos", "node": L["node"],
                     "c_F": float(L["c"]), "r_ohm": float(L["r"]),
                     "r_model": L.get("r_model"), "folded": True,
                     **({"half": L["half"]} if L.get("half") else {}),
                     "i_avg_A": float(cap["avg"][q] * sh),
                     "i_rms_A": float(cap["rms"][q] * sh),
                     "i_peak_A": float(cap["peak"][q] * sh)})
    rows.sort(key=lambda x: -x["i_rms_A"])
    c_all = float(sum(L["c"] for L in lumps) + acc["devices_F"])
    folded_c = float(sum(L["c"] for L in folded))
    return {"n": len({x["name"] for x in rows}), "c_total_F": c_all,
            "folded_F": folded_c, "top": rows[:TOP_DECAPS]}


# ------------------------------------------------------------------ notices


def _notices(r, net, out, used, source, higher, hi_c, standin, c_used, run, a, acc,
             two_mos, lumps, proc, log) -> None:
    n = out["numerics"]
    cap = out["capacitance"]
    b = out["bases"]
    notice("EMIR-I430", f"net {net!r}: decap-aware EM computed ({n['n_steps']} steps of "
           f"{n['step_s']:.3g} s, {n['passes']} pass(es), periodic-average check "
           f"{n['avg_check']['max_rel']:.1e}; rms correction up to "
           f"{(n['correction_max']['rms'] - 1) * 100:.2f} %; {cap['counted_F'] * 1e12:.4g} pF "
           f"counted"
           + (f", {cap['on_feed_F'] * 1e12:.4g} pF on the feed node (inert: it sees no dV/dt)"
              if cap.get("on_feed_F") else "")
           + f"): decap-free {b['decap-free']['verdict']} "
           f"{_rt(b['decap-free']['max_ratio']['all'])}, decap-aware "
           f"{b['decap-aware']['verdict']} {_rt(b['decap-aware']['max_ratio']['all'])}, "
           f"worst {b['worst']['verdict']} {_rt(b['worst']['max_ratio']['all'])}", log)
    if source == "option" and used != DEFAULT_BASIS:
        notice("EMIR-W437", f"net {net!r}: the EM verdict uses --em-basis {used}, not the "
               f"default ({DEFAULT_BASIS}): {b[used]['verdict']} {_rt(b[used]['max_ratio']['all'])}"
               f" against {b[DEFAULT_BASIS]['verdict']} "
               f"{_rt(b[DEFAULT_BASIS]['max_ratio']['all'])} with the default. That is the "
               f"person's choice; relay both", log)
    if used == "decap-free" and higher:
        c0, row = higher[0]
        uncovered(r, "basis", "EMIR-W436", len(higher),
                  f"{len(higher)} segment(s) read higher with their decaps than the "
                  f"decap-free basis covers (a decap's straps, the mesh around it), e.g. "
                  f"{c0['layer']} {c0['name']} decap-aware {_rt(row['ratio_decap_aware'])} "
                  f"against decap-free {_rt(row['ratio_decap_free'])}",
                  [c["name"] for c, _row in higher])
        notice("EMIR-W436", f"net {net!r}: {len(higher)} segment(s) read higher with their "
               f"decaps than the decap-free verdict covers (a decap's straps, the mesh "
               f"around it), e.g. {c0['layer']} {c0['name']} decap-aware "
               f"{_rt(row['ratio_decap_aware'])} against decap-free "
               f"{_rt(row['ratio_decap_free'])}", log)
    if hi_c or standin:
        n2 = int(min(200_000, 2 * run["n_steps"]))
        notice("EMIR-W435", f"net {net!r}: the integration step lost more than "
               f"{(CORR_WARN - 1) * 100:.0f} % of the rms on {len(hi_c) + len(standin)} "
               f"segment(s) near the warn fraction (up to "
               f"{(float(c_used.max()) - 1) * 100:.1f} %): their decap-aware rms is corrected "
               f"up" + (f", and on {len(standin)} the loss is above "
                        f"{(CORR_STANDIN - 1) * 100:.0f} %, so the larger of decap-free and "
                        f"decap-aware stands in" if standin else "")
               + f". A finer step: --dyn-steps {n2}", log)
    lean = []
    if cap["coupling_share"] > COUPLING_SHARE:
        lean.append(f"coupling caps are {cap['coupling_share']:.0%} of the capacitance "
                    f"counted (their far plate taken as an AC ground)")
    if cap["default_model"]:
        lean.append(f"{cap['default_model']} capacitor device(s) sized through the "
                    f"`default` cap model (the DSPF names none)")
    ideal = [L for L in lumps if L.get("r", 0.0) <= 0.0]
    closed = [L for L in lumps if str(L.get("r_model", "")).startswith("R_channel")
              and L.get("r", 0.0) > 0.0]
    if closed:
        lean.append(f"{len(closed)} MOS decap(s) with the closed-form access resistance "
                    f"(R_channel/6, no fitted esr_*): known to read the access resistance "
                    f"several-fold high, so their straps' current reads low")
    if ideal:
        lean.append(f"{len(ideal)} MOS decap(s) with no access resistance at all (cox only: "
                    f"no esr_a and no ron_sq): an ideal capacitor, so the feed path reads "
                    f"low and their straps high")
    folded = run["em"].get("folded") or []
    if folded:
        cf = sum(L["c"] for L in folded)
        ctot = max(sum(L["c"] for L in lumps), 1e-300)
        lean.append(f"{len(folded)} decap lump(s) ({cf * 1e12:.4g} pF, {cf / ctot:.0%} of the "
                    f"decap C) folded onto their nodes, their access R (at most "
                    f"{max(L['r'] for L in folded):.4g} ohm) dropped: its R*C is below a "
                    f"tenth of the finest step ({run['sub_steps']['finest_s']:.3g} s)")
    far = _esr_far(lumps, proc, run, acc.get("periods"))
    if far:
        lean.append(far)
    if lean:
        notice("EMIR-W438", f"net {net!r}: decap-aware EM leans on the decap model: "
               + "; ".join(lean), log)
    if cap["two_rail"]:
        notice("EMIR-W439", f"net {net!r}: {cap['two_rail']} decap(s) sit between this net "
               f"and another net with resistance in this DSPF; each net is solved alone, "
               f"with the other plate an ideal AC ground, so the current through these "
               f"decaps, their straps and the mesh near them reads LOW (by up to (R_this + "
               f"R_other) / R_this below the decap's corner frequency; in example 13's "
               f"two-rail case the straps read 5.52 % low, the feed path high)", log)
    if acc["coupling_signal_F"] > 0:
        notice("EMIR-I431", f"net {net!r}: {acc['coupling_signal_F'] * 1e15:.4g} fF of "
               f"coupling to signal nets left out of the decap-aware solve (in the CC "
               f"simulation its current flows from the ideal rail, through no instance "
               f"pin); the droop keeps it as an AC ground, as before", log)


def _esr_far(lumps, proc, run, periods=None):
    """An ESR fitted more than `ESR_FREQ_SPAN` times away from the window's
    fundamental, either way (ESR depends on frequency: on the characterised
    cell of `dynir.mos_decap_lumps` it roughly doubled from 1 GHz to 100 MHz),
    for a model the net's decaps use."""
    models = getattr(proc, "cap_models", {}) or {}
    used = {L.get("model") for L in lumps if L.get("model")}
    span = float(run["t"][-1] - run["t"][0]) if len(run["t"]) else 0.0
    if span <= 0:
        return None
    f0 = (periods or 1) / span
    bad = []
    for name, m in models.items():
        if not isinstance(m, dict) or m.get("esr_a") is None or not m.get("esr_fit_freq"):
            continue
        if used and name not in used:
            continue
        f = float(m["esr_fit_freq"])
        if not f0 / ESR_FREQ_SPAN <= f <= f0 * ESR_FREQ_SPAN:
            bad.append((name, f))
    if not bad:
        return None
    return (f"the access resistance of {bad[0][0]} was fitted at {bad[0][1]:.3g} Hz, more "
            f"than {ESR_FREQ_SPAN:g}x from this window's fundamental ({f0:.3g} Hz): ESR "
            f"depends on frequency, re-fit near the activity's own")


def _rt(v) -> str:
    return "not a number" if v is None or not np.isfinite(v) else f"{v:.3f}x"


# ------------------------------------------------------------------ console


def _console(r) -> str:
    em = r["dynamic_em"]
    b = em["bases"]
    eb = r["em_basis"]
    f = em["feed"]
    n = em["numerics"]

    def one(k):
        x = b[k]
        w = x["worst"]
        return (f"{k} {x['verdict']} {_rt(x['max_ratio']['all'])}"
                + (f" {w['metric']} ({w['layer']} {w['name']})" if w else ""))
    return (f"  decap-aware EM : verdict on {eb['used']}"
            + (" (default)" if eb["source"] == "default" else " (--em-basis)")
            + "; " + "; ".join(one(k) for k in BASES)
            + f"; feed rms {f['i_rms_A']['decap-free'] * 1e3:.4g} -> "
              f"{f['i_rms_A']['decap-aware'] * 1e3:.4g} mA, peak "
              f"{f['i_peak_A']['decap-free'] * 1e3:.4g} -> "
              f"{f['i_peak_A']['decap-aware'] * 1e3:.4g} mA; {em['decaps']['n']} decap(s), "
              f"{em['decaps']['c_total_F'] * 1e12:.4g} pF; step {n['step_s'] * 1e12:.3g} ps x "
              f"{n['n_steps']}, rms correction <= {(n['correction_max']['rms'] - 1) * 100:.2f} %")


def _console_not(r) -> str:
    eb = r.get("em_basis") or {}
    return (f"  decap-aware EM : not computed ({eb.get('fallback')}: "
            f"{eb.get('fallback_reason')}); verdict on decap-free")


# ------------------------------------------------------------------ the summary


def net_fields(r: dict) -> dict:
    """The run summary's per-net `em_basis` and `dynamic_em` (none on a run
    without --dynamic or --em-basis: its summary is the 1.x one)."""
    if "em_basis" not in r:
        return {}
    return {"em_basis": dict(r["em_basis"]), "dynamic_em": r.get("dynamic_em")}


def prepare_summary(res) -> None:
    """Every net's `em_basis` on a run where it applies (`_prepare`), for
    the nets `after_net` did not see."""
    a = res.args
    if not relevant(a):
        return
    for net, r in (res.solved or {}).items():
        _prepare(a, res.dspf, net, r)


def summary_fields(res) -> dict:
    """The summary's top-level `em_basis` (with --dynamic or --em-basis, or a
    net that says why its decaps are not covered) and `argv`, the run's own
    arguments (`emir next --run-summary` re-plans from them). `used` lists
    the bases the nets' verdicts are on; `honoured` is false when a net could
    not have the basis asked for (`not_computed` says which and why)."""
    import os
    a = res.args
    # `cwd`: where the run ran, so the relative paths of `argv` resolve from
    # anywhere (`plan_decap.summary_args`)
    out = {"argv": list(getattr(a, "argv", None) or []), "cwd": os.getcwd()}
    nets = [(n, r["em_basis"]) for n, r in (res.solved or {}).items() if r.get("em_basis")]
    if relevant(a) or nets:
        # a run with neither: decap-free because it has no --dynamic (W428)
        basis, source = requested(a) if relevant(a) else ("decap-free", "no-dynamic")
        lost = not_honoured(res)
        out["em_basis"] = {
            "requested": basis, "source": source, "default": DEFAULT_BASIS,
            "used": sorted({eb["used"] for _n, eb in nets}),
            "honoured": not lost,
            "not_computed": [{"net": n, "fallback": eb["fallback"],
                              "fallback_reason": eb.get("fallback_reason"),
                              "dyn_steps": eb.get("dyn_steps"),
                              **({"models": eb["models"]} if eb.get("models") else {})}
                             for n, eb in lost]}
    return out


def disagree(em: dict) -> bool:
    """Whether the bases tell a different story: a verdict differs, or a worst
    ratio by more than `DISAGREE`."""
    b = (em or {}).get("bases") or {}
    if len(b) < 2:
        return False
    vs = {x["verdict"] for x in b.values()}
    if len(vs) > 1:
        return True
    rs = [x["max_ratio"]["all"] for x in b.values()]
    if any(v is None or not np.isfinite(v) for v in rs):
        return False
    lo, hi = min(rs), max(rs)
    return hi > (1.0 + DISAGREE) * lo if lo > 0 else hi > 0


def next_steps(res, fmt) -> list:
    """The run's next steps of this feature: the person's choice of basis
    (`emir next --run-summary`: a plan, never a re-run, so following the steps
    never switches the basis behind the person's back) and a finer step where
    a net asked for one."""
    from .errors import step
    from .output import command_line, strip_options
    a = res.args
    argv = list(getattr(a, "argv", None) or [])
    out, seen = [], set()

    def add(why, cmd):
        if cmd and cmd not in seen:
            seen.add(cmd)
            out.append(step(why, cmd))
    ask = []
    for net, r in (res.solved or {}).items():
        eb = r.get("em_basis") or {}
        em = r.get("dynamic_em")
        base = strip_options(argv, {"--format": 1, "--dyn-steps": 1})
        if eb.get("fallback") == "step-too-coarse" and argv and eb.get("dyn_steps"):
            add(f"{net}: decap-aware EM needs a finer step (EMIR-W434): the same run with "
                f"--dyn-steps {eb['dyn_steps']}",
                command_line("run", base, "--dyn-steps", eb["dyn_steps"], fmt))
        if eb.get("decaps_not_sized") and eb.get("requested") in NEEDS_DYNAMIC \
                and res.summary_path:
            add(f"{net}: {unsized_text(eb['decaps_not_sized'])}, so the {eb['requested']} "
                f"basis does not cover their straps (EMIR-W409): emir next says what to ask "
                f"the person for", command_line("next", "--run-summary", res.summary_path, fmt))
        if eb.get("fallback") == "reference-not-feed" and argv:
            add(f"{net}: decap-aware EM draws the decaps' charge from the feed (EMIR-W431): "
                f"the same run without --ground",
                command_line("run", strip_options(argv, {"--format": 1, "--ground": 1}),
                             fmt))
        if em:
            n = em["numerics"]
            if (n.get("n_corrected") or n.get("n_stand_in")) and argv:
                n2 = int(min(200_000, 2 * n["n_steps"]))
                add(f"{net}: a tighter decap-aware rms (EMIR-W435): the same run with twice "
                    f"the steps", command_line("run", base, "--dyn-steps", n2, fmt))
            if disagree(em) or eb.get("source") == "option" or \
                    (eb.get("used") == "decap-free" and em["higher_aware"]["n"]):
                ask.append(net)
    if ask and res.summary_path:
        add(f"{', '.join(ask[:4])}: the EM bases disagree (or the verdict is on a basis the "
            f"person chose): emir next shows each basis's verdict and asks the person "
            f"which one decides (a plan, not a re-run)",
            command_line("next", "--run-summary", res.summary_path, fmt))
    return out


# ------------------------------------------------------------------ the report


def _overall(nets: list, basis: str) -> tuple:
    """`(verdict, worst ratio, violations)` of a run under `basis`: nets with
    decap-aware EM by that basis's numbers, the others by their own (a
    worst ratio that is not a number makes the run's None)."""
    rank = {"FAIL": 2, "WARN": 1, "CLEAN": 0}
    v, worst, nv, nan = "CLEAN", 0.0, 0, False
    for n in nets:
        b = ((n.get("dynamic_em") or {}).get("bases") or {}).get(basis)
        if b:
            nv_, vv, mr = b["n_violations"], b["verdict"], b["max_ratio"].get("all")
        else:
            m = n.get("max_ratio")
            nv_, vv = n.get("n_violations") or 0, n["verdict"]
            mr = m.get("all") if isinstance(m, dict) else m
        v = vv if rank[vv] > rank[v] else v
        nv += nv_
        if mr is None or not np.isfinite(mr):
            nan = True
        else:
            worst = max(worst, float(mr))
    return v, (None if nan else worst), nv


def report_fields(s: dict, source: str, fmt=()) -> dict:
    """What `emir report` adds for this feature (`emir.report/1`): the basis
    asked for, per net its basis and every basis's verdict, the person's
    choices when the bases disagree, and the segments' table. `fmt`: the
    --format of the `emir next` that plans from it (each choice's command
    keeps it)."""
    if not s.get("em_basis"):
        return {}
    nets = s.get("nets") or []
    per, items, ask = {}, [], False
    for n in nets:
        eb = n.get("em_basis")
        if not eb:
            continue
        em = n.get("dynamic_em")
        row = {"em_basis": eb, "computed": bool(em)}
        if em:
            row.update(bases={b: {"verdict": x["verdict"],
                                  "worst_ratio": x["max_ratio"].get("all"),
                                  "governing_metric": x["governing_metric"],
                                  "worst": x["worst"], "n_violations": x["n_violations"]}
                              for b, x in em["bases"].items()},
                       feed=em["feed"], decaps={k: em["decaps"][k] for k in
                                                ("n", "c_total_F", "folded_F")},
                       capacitance=em["capacitance"], numerics=em["numerics"],
                       higher_aware=em["higher_aware"], disagree=disagree(em))
            ask = ask or row["disagree"] or eb.get("source") == "option"
            items += [{"net": n["net"], **it} for it in em.get("items") or []]
        per[n["net"]] = row
    choices = []
    if ask and any(r["computed"] for r in per.values()):
        from .output import command_line
        used = {x["em_basis"]["used"] for x in per.values()}
        argv = list(s.get("argv") or [])
        sims = any(x.split("=")[0] in ("--netlist", "--self-consistent") for x in argv)
        cost = ("the run computed every basis; choosing one re-runs it on the same inputs "
                + ("and SIMULATES AGAIN (--netlist): ask before it runs" if sims else
                   "with no new simulation (emir next plans it)"))
        for b in BASES:
            v, mr, nv = _overall(nets, b)
            choices.append({
                "option": f"--em-basis {b}", "route": f"EM basis {b}", "basis": b,
                "what": BASIS_TEXT[b],
                "cost": cost,
                "accuracy": ACCURACY[b], "source": ACCURACY_SOURCE,
                "possible": True, "why_not": None, "recommended": b == DEFAULT_BASIS,
                "verdict": v, "max_ratio": mr, "n_violations": nv,
                "in_use": b in used,
                "command": (command_line("next", "--run-summary", source, "--em-basis", b,
                                         list(fmt))
                            if source and source != "-" else None)})
    items.sort(key=lambda x: -max(_nz(x.get("ratio_decap_free")),
                                  _nz(x.get("ratio_decap_aware"))))
    return {"em_basis": s["em_basis"], "em_bases": per, "em_choices": choices,
            "decap_items": items[:TOP_ITEMS]}


#: how accurate each basis is, measured against RCC on Xyce on example 13's
#: synthetic decap meshes (the governing branch and every branch class; one
#: resistive rail unless said)
ACCURACY = {
    "decap-free": "against RCC with decaps: the governing branch +25.42 % to +52.27 % (rms), "
                  "+37.14 % to +110.95 % (peak); a decap's straps -100 %; minor branches "
                  "down to -95.61 % (rms)",
    "decap-aware": "against RCC with decaps: the governing branch +0.59 % to +5.31 % (rms), "
                   "+5.46 % to +13.10 % (peak); every class and the straps at or above RCC "
                   "(+0.39 % and +2.16 % the least); between two resistive rails the straps "
                   "and the mesh near them read low (the straps -5.52 %, a minor branch "
                   "-3.81 %, rms; EMIR-W439); rests on the decap model",
    "worst": "against RCC with decaps: on the feed path it is decap-free's numbers (the "
             "governing branch +25.42 % to +52.27 % rms, +37.14 % to +110.95 % peak: "
             "decap-free is the larger there), on a decap's straps decap-aware's; at or above "
             "RCC on every class and the straps with one resistive rail; not a guaranteed "
             "bound (the open loop; between two resistive rails the straps and the mesh near "
             "them read low, the straps -5.52 % rms)",
}
ACCURACY_SOURCE = "examples/13_decap_aware/results/decap.md"


def _report_nets(r: dict) -> list:
    """The report's nets in the shape `_overall` reads."""
    per = r.get("em_bases") or {}
    out = []
    for n in r["nets"]:
        x = per.get(n["net"]) or {}
        row = {"net": n["net"], "verdict": n["verdict"], "max_ratio": n.get("worst_ratio"),
               "n_violations": 0}
        if x.get("computed"):
            row["dynamic_em"] = {"bases": {
                b: {"verdict": y["verdict"], "max_ratio": {"all": y["worst_ratio"]},
                    "n_violations": y["n_violations"]} for b, y in x["bases"].items()}}
        out.append(row)
    return out


def headline(r: dict) -> str:
    """The text after the verdict on the report's first line: the basis, and
    the other bases' verdicts and worst ratios where they differ (a verdict,
    or a worst ratio by more than `DISAGREE`)."""
    eb = r.get("em_basis")
    per = r.get("em_bases") or {}
    lost = [(n, x["em_basis"]) for n, x in per.items()
            if x["em_basis"].get("fallback") in LOST
            and x["em_basis"].get("requested") in NEEDS_DYNAMIC]
    if eb and lost:
        b0 = lost[0][1]["requested"]
        return (" (EM basis decap-free on " + ", ".join(n for n, _ in lost[:4])
                + f": {b0}{' (the default)' if eb.get('source') == 'default' else ''} "
                f"not computed ("
                + ", ".join(sorted({x['fallback'] for _, x in lost}))
                + "), so the verdict does not cover a decap's straps)")
    if not eb or not any(x["computed"] for x in per.values()):
        return ""
    used = sorted({x["em_basis"]["used"] for x in per.values() if x["computed"]})
    tail = " (the default)" if eb.get("source") == "default" else " (--em-basis)"
    unsized = [(n, x["em_basis"]["decaps_not_sized"]) for n, x in per.items()
               if x["em_basis"].get("decaps_not_sized")
               and x["em_basis"].get("requested") in NEEDS_DYNAMIC]
    if unsized:
        return (f" (EM basis {'/'.join(used)}{tail} without "
                + "; ".join(f"{unsized_text(u)} on {n}" for n, u in unsized[:4])
                + ": their straps are not checked, so the basis asked for is not "
                  "honoured there)")
    nets = _report_nets(r)
    v0, m0, _ = _overall(nets, used[0])
    others = []
    for b in BASES:
        if b in used:
            continue
        v, m, _ = _overall(nets, b)
        far = (m is None) != (m0 is None) or (m is not None and m0 is not None and
                                              abs(m - m0) > DISAGREE * max(m, m0))
        if v != v0 or far:
            others.append(f"{b} {v}" + (f" {m:.3f}x" if m is not None else ""))
    return (f" (EM basis {'/'.join(used)}{tail}"
            + ("; " + ", ".join(others) if others else "; the other bases agree") + ")")


def verdict_cell(n: dict, r: dict) -> str:
    """The Nets table's verdict cell, with the basis it is on."""
    per = (r.get("em_bases") or {}).get(n["net"])
    if not per:
        return n["verdict"]
    eb = per["em_basis"]
    if eb["used"] == "measured":
        return f"{n['verdict']} (measured)"
    if not per["computed"]:
        return f"{n['verdict']} (decap-free: decap-aware not computed)"
    if eb.get("decaps_not_sized"):
        return (f"{n['verdict']} (basis {eb['used']}; {eb['decaps_not_sized']['n']} "
                f"decap(s) not sized)")
    return f"{n['verdict']} (basis {eb['used']})"


def report_lines(r: dict) -> list:
    """The report's "Decap-aware EM" section (right after the Nets table)."""
    per = r.get("em_bases") or {}
    if not per:
        return []
    from .bigblock import loop_pass
    from .report import _x, at_text
    # the currents' loop: open, or the self-consistent loop's reported pass
    # (each pin fed its droop waveform)
    closed = {n: loop_pass(x) for n, x in (r.get("self_consistency") or {}).items()
              if loop_pass(x) is not None}
    loop = ("Open loop like every cc-grid number" if not closed else
            "From the currents of the self-consistent loop's reported pass ("
            + ", ".join(f"{n}: pass {k}" for n, k in closed.items())
            + "), each pin fed its droop waveform"
            + ("; the other nets open loop" if set(per) - set(closed) else ""))
    L = ["", "## Decap-aware EM", "",
         "With --dynamic the dynamic solve gives every segment's current with the decaps "
         "(decap-aware), beside the static one without them (decap-free); `worst` takes "
         "the larger of the two per segment. avg is the same in every basis (a capacitor "
         f"carries no DC). {loop}; decap-aware rests on the "
         "process file's cap_models and the extraction's capacitance. Screening, not "
         "sign-off.", ""]
    for net, x in per.items():
        eb = x["em_basis"]
        if eb["used"] == "measured":
            L.append(f"- {net}: rcc measures the decaps' currents; the basis does not apply.")
            continue
        if not x["computed"]:
            L.append(f"- {net}: decap-aware EM not computed ({eb.get('fallback')}: "
                     f"{eb.get('fallback_reason')}); the verdict uses decap-free.")
            continue
        parts = []
        for b in BASES:
            y = x["bases"][b]
            w = y.get("worst")
            parts.append(f"{b} {y['verdict']} {_x(y['worst_ratio'])}"
                         + (f" {w['metric']} ({w['layer']} {w['name']})" if w else ""))
        f = x["feed"]
        n = x["numerics"]
        L.append(f"- {net}: the verdict uses **{eb['used']}**"
                 + (" (the default)" if eb["source"] == "default" else " (--em-basis)")
                 + ". " + "; ".join(parts) + ".")
        L.append(f"  Feed current at {f['node']}: rms "
                 f"{_x(_mA(f['i_rms_A']['decap-free']), ' mA')} decap-free, "
                 f"{_x(_mA(f['i_rms_A']['decap-aware']), ' mA')} decap-aware; peak "
                 f"{_x(_mA(f['i_peak_A']['decap-free']), ' mA')} and "
                 f"{_x(_mA(f['i_peak_A']['decap-aware']), ' mA')}. "
                 f"{x['decaps']['n']} decap(s), {x['decaps']['c_total_F'] * 1e12:.4g} pF"
                 f" (capacitance counted {x['capacitance']['counted_F'] * 1e12:.4g} pF).")
        L.append(f"  Numerics: {n['n_steps']} steps of {n['step_s'] * 1e12:.3g} ps "
                 f"({n['sub_steps']['intervals']} of them refined, "
                 f"{n['sub_steps'].get('added', 0)} sub-steps added for the waveforms' edges "
                 f"and the decaps' fast response), {n['passes']} pass(es) to periodic steady "
                 f"state, rms corrected for the step by at most "
                 f"{(n['correction_max']['rms'] - 1) * 100:.2f} %.")
        if eb.get("decaps_not_sized"):
            L.append(f"  NOT covered by this verdict: {unsized_text(eb['decaps_not_sized'])}, "
                     f"left out of the decap-aware solve: their straps carry nothing and are "
                     f"not checked (EMIR-W409). Ask the person for each model's capacitance "
                     f"from the PDK's decap characterisation.")
        if x["higher_aware"]["n"] and eb["used"] == "decap-free":
            h = x["higher_aware"]["worst"]
            L.append(f"  NOT covered by this verdict: {x['higher_aware']['n']} segment(s) read "
                     f"higher with their decaps, e.g. {h['layer']} {h['name']} "
                     f"{_x(h['ratio_decap_aware'])} decap-aware against "
                     f"{_x(h['ratio_decap_free'])} decap-free.")
    items = r.get("decap_items") or []
    if items:
        L += ["", "| net | segment | at (x, y) um | layer | decap-free ratio (metric) "
                  "| decap-aware ratio (metric) | decided by |",
              "|---|---|---|---|---|---|---|"]
        for it in items:
            L.append(f"| {it['net']} | {it['name']} | {at_text(it)} | {it.get('layer') or '-'} "
                     f"| {_x(it.get('ratio_decap_free'))} ({it.get('metric_decap_free') or '-'}) "
                     f"| {_x(it.get('ratio_decap_aware'))} "
                     f"({it.get('metric_decap_aware') or '-'}) | {it['decided_by']} |")
    ch = r.get("em_choices") or []
    if ch:
        L += ["", "**The person's choice: which basis decides the EM verdict.** The bases "
                  "disagree (or the verdict is on a basis the person chose). Show the person "
                  "each, ask, and relay their choice; never choose it for them, and never "
                  "to make a FAIL pass:", ""]
        for c in ch:
            L.append(f"- {c['basis']}" + (" (recommended, the default)" if c["recommended"]
                                          else "")
                     + f": {c['verdict']}, worst {_x(c['max_ratio'])}, "
                       f"{c['n_violations']} violation(s). {c['what']}."
                     + (f" Then: `{c['command']}`" if c.get("command") else ""))
    return L


def basis_label(r: dict) -> str:
    """The EM basis a net's numbers are on, for the outputs that show them
    (`basis <basis> (the default)`; "" on a run without one)."""
    eb = (r or {}).get("em_basis") or {}
    if not eb.get("used"):
        return ""
    if eb["used"] == "measured":
        return "basis measured (rcc)"
    if eb.get("fallback") == "no-dynamic":
        return f"basis {eb['used']} (no --dynamic)"
    tail = (" (the default)" if eb.get("source") == "default" and eb["used"] == eb["requested"]
            else " (--em-basis)" if eb.get("source") == "option" and eb["used"] == eb["requested"]
            else f" ({eb['requested']} not computed: {eb.get('fallback')})"
            if eb["used"] != eb["requested"] else "")
    return f"basis {eb['used']}{tail}"


def worst_items_caption(r: dict) -> list:
    """The report's line under "Worst items": which EM basis the ratios are
    on, per net (the Decap-aware EM section has each basis's)."""
    per = r.get("em_bases") or {}
    if not per:
        return []
    return ["Ratios on each net's EM basis: "
            + "; ".join(f"{n} {basis_label({'em_basis': x['em_basis']})}"
                        for n, x in per.items()) + ".", ""]


def _mA(v):
    return None if v is None else v * 1e3


# ------------------------------------------------------------------ HTML


def html_section(res: dict) -> str:
    """The net report's "Decap-aware EM" section: the basis, every basis's
    verdict, the feed current, the decap currents and the segments of both
    bases (the map above is coloured by the verdict's basis)."""
    eb = res.get("em_basis")
    if not eb:
        return ""
    from html import escape as _e
    em = res.get("dynamic_em")
    if not em:
        return (f"<h2>Decap-aware EM</h2><p class=\"hint\">Not computed "
                f"({_e(str(eb.get('fallback')))}: {_e(str(eb.get('fallback_reason')))}); the "
                f"verdict uses {_e(eb['used'])}.</p>")
    rows = res.get("_em_rows") or {}
    nw = res["network"]
    where = {r["name"]: r for r in nw.res}

    def rt(v):
        return "not a number" if v is None or not np.isfinite(v) else f"{v:.3f}×"
    seg = []
    order = sorted(rows, key=lambda n: -max(_nz(rows[n]["ratio_decap_free"]),
                                            _nz(rows[n]["ratio_decap_aware"])))[:100]
    for n in order:
        x = rows[n]
        b = where[n].get("bbox")
        at = (f"{(b[0] + b[2]) / 2:.3f}, {(b[1] + b[3]) / 2:.3f}" if b else "—")
        lay = (res["layer_map"].get(where[n].get("layer")) or (None, where[n].get("layer")))[1]
        seg.append(f"<tr><td>{_e(n)}</td><td>{_e(str(lay))}</td>"
                   f"<td class=\"num\">{at}</td>"
                   f"<td class=\"num\">{rt(x['ratio_decap_free'])} "
                   f"{_e(str(x['metric_decap_free']))}</td>"
                   f"<td class=\"num\">{rt(x['ratio_decap_aware'])} "
                   f"{_e(str(x['metric_decap_aware']))}</td>"
                   f"<td>{_e(x['decided_by'])}</td></tr>")
    dec = "".join(
        f"<tr><td>{_e(d['name'])}{' (' + _e(d['half']) + ')' if d.get('half') else ''}</td>"
        f"<td>{_e(d['kind'])}{' folded' if d['folded'] else ''}</td><td>{_e(d['node'])}</td>"
        f"<td class=\"num\">{d['c_F'] * 1e12:.4g}</td>"
        f"<td class=\"num\">{'—' if d['r_ohm'] is None else format(d['r_ohm'], '.4g')}</td>"
        f"<td class=\"num\">{d['i_rms_A'] * 1e3:.4f}</td>"
        f"<td class=\"num\">{d['i_peak_A'] * 1e3:.4f}</td></tr>"
        for d in em["decaps"]["top"])
    b = em["bases"]
    f = em["feed"]
    n = em["numerics"]
    cap = em["capacitance"]
    bl = "".join(f"<li><b>{_e(k)}</b>: {_e(b[k]['verdict'])} "
                 f"{rt(b[k]['max_ratio']['all'])}"
                 + (f" {_e(b[k]['worst']['metric'])} ({_e(b[k]['worst']['layer'])} "
                    f"{_e(b[k]['worst']['name'])})" if b[k]["worst"] else "") + "</li>"
                 for k in BASES)
    return f"""<h2>Decap-aware EM — the verdict uses <b>{_e(eb['used'])}</b>{' (the default)' if eb['source'] == 'default' else ' (--em-basis, the person’s choice)'}</h2>
<p class="hint">The same dynamic solve gives every segment's current with the decaps
 (decap-aware) beside the static one without them (decap-free); worst takes the larger
 per segment. avg is the same in every basis. Open loop; decap-aware rests on the
 process file's cap_models and the extraction's capacitance. The map above is coloured
 by the verdict's basis.</p>
<ul>{bl}</ul>
<p>Feed current at {_e(f['node'])}: rms {f['i_rms_A']['decap-free'] * 1e3:.4g} mA decap-free,
 {f['i_rms_A']['decap-aware'] * 1e3:.4g} mA decap-aware; peak
 {f['i_peak_A']['decap-free'] * 1e3:.4g} and {f['i_peak_A']['decap-aware'] * 1e3:.4g} mA.
 Capacitance counted {cap['counted_F'] * 1e12:.4g} pF (wire {cap['wire_F'] * 1e12:.4g},
 devices {cap['devices_F'] * 1e12:.4g}, MOS {cap['mos_F'] * 1e12:.4g}, coupling to rails
 {cap['coupling_quiet_F'] * 1e12:.4g}; coupling to signal nets left out
 {cap['coupling_signal_excluded_F'] * 1e12:.4g} pF). Numerics: {n['n_steps']} steps of
 {n['step_s'] * 1e12:.3g} ps, {n['passes']} pass(es), rms corrected by at most
 {(n['correction_max']['rms'] - 1) * 100:.2f} %, {n['sub_steps']['intervals']}
 step(s) refined ({n['sub_steps'].get('added', 0)} sub-steps) for the waveforms' edges and
 the decaps' fast response.</p>
<div class="tblwrap"><table><thead><tr><th>segment</th><th>layer</th>
<th class="num">x, y <span class="u">µm</span></th><th class="num">decap-free ratio</th>
<th class="num">decap-aware ratio</th><th>decided by</th></tr></thead>
<tbody>{''.join(seg) or '<tr><td colspan="6">no segment at or above the warn fraction in any basis</td></tr>'}</tbody></table></div>
<h2>Decap currents (devices, not checked against a metal limit)</h2>
<div class="tblwrap"><table><thead><tr><th>decap</th><th>kind</th><th>node</th>
<th class="num">C <span class="u">pF</span></th><th class="num">R access <span class="u">ohm</span></th>
<th class="num">rms <span class="u">mA</span></th><th class="num">peak <span class="u">mA</span></th></tr></thead>
<tbody>{dec or '<tr><td colspan="7">none</td></tr>'}</tbody></table></div>"""
