"""The process model: a process file's limits and resistances at one
analysis temperature (`Process`), and the temperature helpers it uses
(Black's derating, the `jmax_factor` table, the TCR scaling).

Every path that checks currents against limits reads its numbers here: the
DSPF path (`emir run`, `check`) and the validation suite.

This is a SCREENING tool — sign-off EM/IR remains the foundry-qualified flow.
Units: geometry um, resistance ohm, current A internally; limits converted
from mA/um and mA/cut once at load.
"""
from __future__ import annotations

import math

import yaml

from .errors import EmirError, notice


#: EM metrics, in the order they are reported. `avg` is the only one derated
#: with temperature (an ICT attaches `jmax_factor` to its dc_avg lines).
METRICS = ("avg", "rms", "peak")


def _interp(table: dict, t: float) -> float:
    """Piecewise-linear lookup in a {temperature: factor} table, clamped."""
    if not table:
        return 1.0
    ks = sorted(table)
    if t <= ks[0]:
        return table[ks[0]]
    if t >= ks[-1]:
        return table[ks[-1]]
    for a, b in zip(ks, ks[1:]):
        if a <= t <= b:
            f = (t - a) / (b - a)
            return table[a] + f * (table[b] - table[a])
    return 1.0


#: Boltzmann's constant, eV/K (CODATA 2018, exact)
K_BOLTZMANN_EV = 8.617333262e-5
#: 0 C in kelvin
T0_K = 273.15


def absolute(t_c, what: str) -> float:
    """A temperature in C, refused at or below absolute zero (EMIR-E332): a
    kelvin value typed as C, or a sign slip; nothing in the derating or the
    resistance scaling means anything there."""
    t = float(t_c)
    if not math.isfinite(t) or t <= -T0_K:
        raise EmirError("EMIR-E332", f"{what} {t:g} C is at or below absolute zero "
                        f"(-273.15 C): a temperature in C is needed")
    return t


def black_factor(ea_ev: float, n: float, t_c: float, tref_c: float) -> float:
    """J(T) / J(Tref) from Black's equation, MTTF = A J^-n exp(Ea / (k T)):
    at equal lifetime, J(T)/J(Tref) = exp[(Ea / (n k)) (1/T - 1/Tref)], with
    the temperatures in kelvin. Below 1 above Tref (the limits fall), 1 at
    Tref, above 1 below it (`Process` applies at most 1 unless the file asks
    for extrapolation, `below_tref: extrapolate`)."""
    t = absolute(t_c, "the analysis temperature") + T0_K
    tref = absolute(tref_c, "em_tref") + T0_K
    return math.exp(ea_ev / (n * K_BOLTZMANN_EV) * (1.0 / t - 1.0 / tref))


#: what `derating: below_tref:` may say: clamp (the default: below em_tref
#: the limits stay as quoted) or extrapolate (Black's equation below em_tref)
BELOW_TREF = ("clamp", "extrapolate")


def black_derating(data: dict) -> dict | None:
    """The file's `derating: {model: black, ea_eV, n}` when it is entered, else
    None (left empty: no derating). Refused when half entered, of another
    model, not positive (EMIR-E322), or given beside a `jmax_factor` table
    (EMIR-E321)."""
    der = data.get("derating")
    if der is None:
        return None
    if not isinstance(der, dict):
        raise EmirError("EMIR-E322", f"`derating` must be a mapping {{model: black, ea_eV, "
                        f"n}}, got {der!r}")
    ea, n = der.get("ea_eV"), der.get("n")
    if ea is None and n is None:
        return None                     # the fill-in form, not entered: no derating
    model = der.get("model", "black")
    if model != "black":
        raise EmirError("EMIR-E322", f"`derating: model: {model}` is not known; the one "
                        f"form emir reads is black (Black's equation)")
    bad = [k for k, v in (("ea_eV", ea), ("n", n))
           if isinstance(v, bool) or not isinstance(v, (int, float)) or not v > 0]
    if bad:
        raise EmirError("EMIR-E322", f"`derating` needs ea_eV and n as positive numbers; "
                        f"{', '.join(bad)} is {', '.join(repr(der.get(k)) for k in bad)}")
    if data.get("jmax_factor"):
        raise EmirError("EMIR-E321", "the process file gives both `derating` (Black's "
                        "equation) and a `jmax_factor` table: keep one")
    below = der.get("below_tref", "clamp")
    if below is None:
        below = "clamp"
    if below not in BELOW_TREF:
        raise EmirError("EMIR-E322", f"`derating: below_tref: {below}` is not one of "
                        f"{', '.join(BELOW_TREF)}")
    return {"model": "black", "ea_eV": float(ea), "n": float(n), "below_tref": below}


class Process:
    """PDK data at one analysis temperature.

    Everything temperature dependent is resolved here, once: sheet and via
    resistance are scaled from the ICT's `temp_reference` to the analysis
    temperature via the per-layer TCR, and the avg EM limits are derated
    through the ICT `jmax_factor` table. Downstream code sees plain numbers.
    """

    def __init__(self, data: dict, temp: float | None = None,
                 delta_t: float = 5.0, r_at_temp: bool = False):
        self.data = data
        # An empty (null) value is a value not given: the default applies, or
        # the check it feeds is not made -- never a zero (a hand-entered file
        # from `emir process new` starts with every value null).
        wf = data.get("warn_fraction")
        self.warn_fraction = float(0.8 if wf is None else wf)
        tr = data.get("temp_reference")
        self.temp_ref = absolute(25.0 if tr is None else tr, "temp_reference")
        # No default: the avg limits mean nothing without the temperature
        # they are quoted at, and a guessed one silently shifts every ratio.
        if data.get("em_tref") is None:
            raise EmirError("EMIR-E312",
                "process file has no `em_tref` (the temperature, in C, its "
                "avg EM limits are quoted at): add it -- there is no default")
        self.em_tref = absolute(data["em_tref"], "em_tref")
        self.temp = absolute(self.em_tref if temp is None else temp,
                             "the analysis temperature (--em-temp)")
        self.delta_t = float(delta_t)
        self.jmax_factor = {float(k): float(v)
                            for k, v in (data.get("jmax_factor") or {}).items()}
        # the avg derating: Black's equation (`derating`) or the table
        # (`jmax_factor`), never both (`black_derating` refuses that)
        self.derating = black_derating(data)
        #: Black's factor beyond 1 that was not applied: the analysis is
        #: colder than em_tref, and the file does not ask for extrapolation
        self.derate_clamped = None
        if self.derating:
            f = black_factor(self.derating["ea_eV"], self.derating["n"],
                             self.temp, self.em_tref)
            if f > 1.0 and self.derating.get("below_tref", "clamp") == "clamp":
                # most manuals define the derating above Tref only: below it
                # the limits stay as quoted (conservative), never multiplied
                self.derate_clamped, f = f, 1.0
            self.derate = f
        else:
            self.derate = _interp(self.jmax_factor, self.temp)
        dT = self.temp - self.temp_ref

        self.layers = {}
        #: layers whose self-heating (rms) limit at --delta-t is below their
        #: avg limit: {name: (rms mA/um, avg mA/um, delta-T that would make
        #: them equal)}. The person's delta-T is kept: rms then governs.
        self.rms_below_avg = {}
        for name, d in data["layers"].items():
            tc1 = float(d.get("tc1") or 0.0)
            tc2 = float(d.get("tc2") or 0.0)
            # R is scaled from the file's value, which the ICT states is at
            # `temp_reference`, for a path that builds resistance from the
            # sheet values. When an extractor runs at the
            # analysis temperature the R is already correct -- pass
            # r_at_temp=True then, or the scaling double-counts.
            rscale = 1.0 if r_at_temp else (1.0 + tc1 * dT + tc2 * dT * dT)
            rms_k = float(d["jmax_rms_k"]) if d.get("jmax_rms_k") else None
            # An rms limit comes in one of two forms: an ICT's self-heating
            # constant (`jmax_rms_k`: sqrt(k * delta_T), so it follows
            # --delta-t), or a fixed value as a tech LEF publishes it
            # (`jmax_rms`, ACCURRENTDENSITY RMS), which no option changes.
            rms_fix = float(d["jmax_rms"]) if d.get("jmax_rms") else None
            if rms_k and rms_fix:
                raise EmirError("EMIR-E310",
                    f"layer {name}: give `jmax_rms` (a fixed rms limit, mA/um) or "
                    f"`jmax_rms_k` (limit = sqrt(k * delta_T) mA/um), not both")
            j_avg = (float(d["jmax_avg"]) * self.derate) if d.get("jmax_avg") else None

            # I_rms >= |I_avg| for every waveform, so an rms limit below the
            # avg limit makes the avg check unreachable: rms binds first. The
            # self-heating budget stays the person's --delta-t (it once was
            # raised silently until the two met, which loosened the
            # self-heating check); the input check warns (EMIR-W301).
            dt_eff = self.delta_t
            if rms_k and j_avg and (rms_k * dt_eff) ** 0.5 < j_avg:
                self.rms_below_avg[name] = ((rms_k * dt_eff) ** 0.5, j_avg,
                                            j_avg * j_avg / rms_k)
            dw = d.get("width_loss_um")
            self.layers[name] = {
                # optional: the layer's GDS numbers, which no path reads (the
                # removed `emir layout` did)
                "gds": tuple(d["gds"]) if d.get("gds") is not None else None,
                "tc1": tc1, "tc2": tc2,
                "sheet_res": float(d["sheet_res"]) * rscale,
                "sheet_res_ref": float(d["sheet_res"]),
                "rscale": rscale,
                "delta_t": dt_eff,
                # mA/um -> A/um, avg derated with temperature
                "jmax": {
                    "avg": j_avg * 1e-3 if j_avg else None,
                    "rms": (rms_fix * 1e-3 if rms_fix
                            else ((rms_k * dt_eff) ** 0.5 * 1e-3) if rms_k else None),
                    "peak": (float(d["jmax_peak"]) * 1e-3)
                    if d.get("jmax_peak") else None,
                },
                # True when the rms limit is a fixed value (`jmax_rms`), which
                # the self-heating budget (--delta-t) does not scale
                "rms_fixed": bool(rms_fix),
                # um: the design manual's width loss dW, I_max = J x (W - dW)
                # with W the drawn width (`wire_width`); 0 when not given
                "width_loss": float(dw) if dw else 0.0,
                "estimate": bool(d.get("estimate", False)),
            }
        self.vias = {}
        for name, d in data.get("vias", {}).items():
            self.vias[name] = {
                "gds": tuple(d["gds"]) if d.get("gds") is not None else None,
                "below": d["below"], "above": d["above"],
                "res": float(d["res"]),
                # cut side (um): turns a via's cut AREA ($A) into a cut count
                # when the DSPF has no count (`dspf.via_cuts`). Kept, never
                # defaulted: a built-in cut size silently scales every via ratio
                "vcwidth": float(d["vcwidth"]) if d.get("vcwidth") else None,
                # rms and peak flagged `rms_peak_estimate` (an old import's
                # rule of thumb) are not applied: an estimate is not a limit,
                # so those metrics are reported as not checked
                "imax": {
                    "avg": (float(d["imax_avg"]) * self.derate * 1e-3)
                    if d.get("imax_avg") else None,
                    "rms": (float(d["imax_rms"]) * 1e-3)
                    if d.get("imax_rms") and not d.get("rms_peak_estimate") else None,
                    "peak": (float(d["imax_peak"]) * 1e-3)
                    if d.get("imax_peak") and not d.get("rms_peak_estimate") else None,
                },
                "estimate": bool(d.get("estimate", False)),
                "rms_peak_estimate": bool(d.get("rms_peak_estimate", False)),
            }

    def wire_width(self, layer: str, w: float) -> float:
        """The width a wire limit applies to: the drawn width `w` (um) less
        the layer's width loss, I_max = J x (W - dW) as design manuals print
        it. A segment no wider than dW has no limit at all: EMIR-E323."""
        dw = self.layers[layer]["width_loss"]
        if not dw:
            return w
        if w <= dw:
            raise EmirError("EMIR-E323", f"layer {layer}: a segment {w:g} um wide is not "
                            f"wider than the layer's width_loss_um {dw:g} um, so I_max = "
                            f"J x (W - dW) is not positive (is dW in um?)")
        return w - dw

    def r_factor(self, layer: str, t_from: float, t_to: float) -> float:
        """A layer's resistance at `t_to` over its resistance at `t_from`
        (C): f(t_to) / f(t_from), f(T) = 1 + tc1 (T - T_ref) + tc2 (T - T_ref)^2
        with T_ref the file's `temp_reference`."""
        d = self.layers[layer]

        def f(t):
            dt = t - self.temp_ref
            return 1.0 + d["tc1"] * dt + d["tc2"] * dt * dt
        return f(t_to) / f(t_from)

    def limited(self, metric: str) -> bool:
        """Is there a limit for this metric on any layer or via?"""
        return (any(l["jmax"][metric] is not None for l in self.layers.values())
                or any(v["imax"][metric] is not None for v in self.vias.values()))

    @classmethod
    def load(cls, path: str, temp: float | None = None, delta_t: float = 5.0,
             r_at_temp: bool = False, warn_provenance: bool = False,
             log=print) -> Process:
        """Read a process file. A file the engine cannot use is a coded
        error (`emir check` explains every problem one by one), also when
        the input check was skipped (`--no-check`).

        `warn_provenance`: say (EMIR-W320) when the file was entered by hand and some values
        name no source, the values that look like a unit slip (EMIR-W321)
        and Black's derating below em_tref (EMIR-W326) -- for the commands
        whose input check does not run (`emir check` says them as findings)."""
        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise EmirError("EMIR-E301", f"{path}: {e}") from e
        if not isinstance(data, dict):
            raise EmirError("EMIR-E302", f"{path} is not a mapping")
        if not isinstance(data.get("layers"), dict) or not data["layers"]:
            raise EmirError("EMIR-E303", f"{path} has no `layers:` section")
        from .process_file import required_empty, suspects, unsourced, unsourced_text
        empty = required_empty(data)
        if empty:
            raise EmirError("EMIR-E320", f"{path}: required value(s) not entered: "
                            + ", ".join(empty[:8]) + (" ..." if len(empty) > 8 else ""),
                            process=path)
        try:
            proc = cls(data, temp, delta_t, r_at_temp)
        except EmirError:
            raise
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            raise EmirError("EMIR-E315", f"{path}: {type(e).__name__}: {e} -- "
                            f"`emir check` lists every problem of the file") from e
        if warn_provenance:
            # the input check did not run (--no-check): say what it would
            missing = unsourced(data)
            if missing:
                notice("EMIR-W320", unsourced_text(data, missing), log)
            for x in suspects(data):
                notice("EMIR-W321", f"{x['path']} = {x['value']:g} is outside the range "
                       f"{x['range'][0]:g} .. {x['range'][1]:g} {x['unit']} it normally "
                       f"takes" + (f": {x['likely']}" if x["likely"] else "")
                       + (" (the input check refuses it: EMIR-E331)" if x["blocking"]
                          else ""), log)
        # (the input check says it as a finding: once, from whichever ran)
        if proc.derate_clamped and warn_provenance:
            notice("EMIR-W326", f"the analysis temperature {proc.temp:g} C is below em_tref "
                   f"{proc.em_tref:g} C: Black's equation would raise the avg limits "
                   f"x{proc.derate_clamped:.4g}; they are applied as quoted (x1), as the "
                   f"derating is defined above em_tref (`derating: below_tref: "
                   f"extrapolate` applies it)", log)
        elif proc.derating and proc.derate > 1.0 and warn_provenance:
            notice("EMIR-W326", f"the analysis temperature {proc.temp:g} C is below em_tref "
                   f"{proc.em_tref:g} C and the file asks for extrapolation (`below_tref: "
                   f"extrapolate`): the avg limits are raised x{proc.derate:.4g} by Black's "
                   f"equation, beyond where the manual quotes them", log)
        return proc
