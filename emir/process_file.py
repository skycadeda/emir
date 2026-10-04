"""Process data entered by hand: `emir process new` and `emir process check`.

When no readable process data exists for a PDK -- no file in `pdks/`, and the
ICT is missing or encrypted (`emir import-ict` refuses an unreadable one with
EMIR-E325) -- the EM limits are typed from the design manual's EM tables. This
module guides that:

    emir process new --from-dspf blk.dspf -o mypdk.yaml     # the fill-in file
    emir process new --from-dspf blk.dspf -o mypdk.yaml --interactive
    emir process check mypdk.yaml --dspf blk.dspf            # what is still missing

`process new` writes a process file for exactly the layers and vias the DSPF
uses -- its layer tokens and `$lvl` numbers, the `dspf_layers` map filled in,
each via's below/above stacking inferred where the DSPF shows it and marked
`inferred ... check` -- with every value empty (null), its unit beside it and
a `sources:` slot for the document, table or page it comes from; and
`cap_models` entries for the capacitor and MOS-decap models the DSPF holds
(for `--dynamic`). An empty value is a value not given: emir reports what it
would have checked as NOT checked and never guesses one. `--interactive` asks
a person value by value at a terminal; it refuses a pipe or a redirected
stdin (EMIR-E327). That is a check on stdin, not on who types: the rule that
an agent never drives it is the playbook's (agent/playbooks/04-onboard-a-pdk.md).

`process check` (and `emir check --process`, which runs the same checks
through `check.check_process`) says what is still empty (required: emir
refuses to run; optional: that check is not made), which values name no
source in a hand-entered file, which look like a unit slip (each quantity has
a plausible range in the unit emir reads, `QUANTITIES`), which limits are out
of order (peak >= rms >= avg), where an upper metal is more resistive than the
one below it, and, with --dspf, which DSPF layers the file does not cover.

Provenance: the file's `provenance:` is hand-entered, imported-ict,
public-pdk-data or mixed (an entry may carry its own, for a mixed file); each
layer, via and cap model, and the top level, may hold `sources: {key: "document,
table or page"}`. The run summary (`limits_provenance`), `emir report` and the
HTML report say per layer where its limits came from, and a hand-entered file
with values that name no source gets EMIR-W320 on every run.

The two forms design manuals print are read as printed: a width loss,
`width_loss_um` (I_max = J x (W - dW) with W the drawn width; `engine.Process.
wire_width`), and Black's equation for the temperature derating, `derating:
{model: black, ea_eV, n}` (`engine.black_factor`), the alternative to a
`jmax_factor` table.

Screening, not sign-off.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import sys
from dataclasses import dataclass

from .errors import EmirError

#: the file's `provenance:`; an entry's own may be any but `mixed`
PROVENANCE = ("hand-entered", "imported-ict", "public-pdk-data", "mixed")
ENTRY_PROVENANCE = PROVENANCE[:3]
#: the provenances whose values must each name a source
HAND = ("hand-entered", "mixed")
#: what each provenance is, for a person
PROVENANCE_TEXT = {"hand-entered": "entered by hand",
                   "imported-ict": "imported from an ICT (emir import-ict)",
                   "public-pdk-data": "from the PDK's public data",
                   "mixed": "mixed (see each value's source)",
                   None: "not stated in the process file"}
#: a source that `emir process new` inferred: true until a person confirms it
INFERRED = "inferred"


@dataclass(frozen=True)
class Q:
    """One quantity of the process file: its key, the unit emir reads it in,
    what it is, and its plausible range in that unit (a value outside it is
    most likely a unit slip: `low` and `high` say which)."""
    key: str
    unit: str
    what: str
    lo: float | None = None
    hi: float | None = None
    low: str = ""
    high: str = ""
    required: bool = False


_C = "kelvin? (C = K - 273.15)"
_LINE = ("or a current for a whole line where emir reads a density per um of drawn width")
#: the quantities of the process file, per section, in the order a person types them
QUANTITIES = {
    "top": (
        Q("em_tref", "C", "the temperature the avg EM limits are quoted at",
          -55, 200, "", _C, required=True),
        Q("temp_reference", "C", "the temperature sheet_res is quoted at (empty: 25)",
          -55, 200, "", _C),
    ),
    "derating": (
        Q("ea_eV", "eV", "Black's equation: activation energy",
          0.3, 1.5, "joules? (1 eV = 1.602e-19 J)", "kJ/mol? (1 eV = 96.49 kJ/mol)"),
        Q("n", "", "Black's equation: current-density exponent", 0.5, 4.0),
    ),
    "layers": (
        Q("sheet_res", "ohm/square", "sheet resistance at temp_reference",
          0.002, 50, "kohm/sq?", "mOhm/sq? (125 mOhm = 0.125 ohm; an unsilicided resistor "
          "layer can be higher)", required=True),
        Q("tc1", "1/C", "linear temperature coefficient of R", -0.01, 0.01,
          "", "ppm/C or %/C? (3900 ppm/C = 0.39 %/C = 0.0039 /C)"),
        Q("tc2", "1/C^2", "quadratic temperature coefficient of R", -1e-4, 1e-4,
          "", "ppm/C^2? (x1e-6)"),
        Q("width_loss_um", "um", "width loss dW: I_max = J x (W - dW), W drawn",
          0.0, 0.5, "", "nm? (20 nm = 0.02 um)"),
        Q("jmax_avg", "mA/um", "DC average limit per um of drawn width, at em_tref",
          0.05, 100, f"A/um? (2.8 mA/um = 0.0028 A/um), {_LINE}",
          f"uA/um? (2800 uA/um = 2.8 mA/um), {_LINE}"),
        Q("jmax_rms", "mA/um", "rms limit per um of drawn width (a fixed value)",
          0.05, 100, f"A/um? {_LINE}", f"uA/um? {_LINE}"),
        Q("jmax_peak", "mA/um", "peak limit per um of drawn width",
          0.05, 500, f"A/um? {_LINE}", f"uA/um? {_LINE}"),
        Q("jmax_rms_k", "(mA/um)^2 per C", "self-heating rms form: sqrt(k x delta_T)",
          0.01, 1e5),
    ),
    "vias": (
        Q("res", "ohm per cut", "resistance of one cut", 0.01, 200, "",
          "mOhm? (4500 mOhm = 4.5 ohm)", required=True),
        Q("vcwidth", "um", "cut side: a cut area ($A) -> a cut count", 0.01, 10,
          "", "nm? (150 nm = 0.15 um)"),
        Q("imax_avg", "mA per cut", "DC average limit per cut, at em_tref",
          0.005, 50, "A per cut? (0.29 mA = 0.00029 A)", "uA per cut? (290 uA = 0.29 mA)"),
        Q("imax_rms", "mA per cut", "rms limit per cut", 0.005, 50,
          "A per cut?", "uA per cut?"),
        Q("imax_peak", "mA per cut", "peak limit per cut", 0.005, 200,
          "A per cut?", "uA per cut?"),
    ),
    "cap": (
        Q("cj", "F/m^2", "area capacitance", 1e-5, 0.1, "", "fF/um^2? (1 fF/um^2 = 1e-3 F/m^2)"),
        Q("cjsw", "F/m", "perimeter capacitance", 1e-13, 1e-8, "",
          "fF/um? (1 fF/um = 1e-9 F/m)"),
    ),
    "mos": (
        Q("cox", "F/m^2", "gate capacitance per area: C = cox x W x L", 1e-4, 0.1, "",
          "fF/um^2? (10 fF/um^2 = 1e-2 F/m^2)"),
        Q("ron_sq", "ohm/square", "channel resistance per square: access R = ron_sq L/W/6",
          1.0, 1e6),
        # the fitted access resistance, preferred to ron_sq's closed form
        # (`dynir.mos_decap_lumps`): R = esr_a + esr_w W + esr_l / L, W and L in um
        Q("esr_a", "ohm", "fitted access resistance per device: R = esr_a + esr_w W + "
          "esr_l / L (W, L in um); preferred to ron_sq", 1e-3, 500, "",
          "mOhm? (1000 mOhm = 1 ohm)"),
        Q("esr_w", "ohm/um", "the fit's W coefficient (signed)"),
        Q("esr_l", "ohm um", "the fit's 1/L coefficient (signed)"),
        Q("esr_fit_freq", "Hz", "the frequency the fit was made at", 1e6, 1e11,
          "MHz or GHz? (1 GHz = 1e9 Hz)", ""),
    ),
}
_BY_KEY = {sec: {q.key: q for q in qs} for sec, qs in QUANTITIES.items()}
#: the via keys that describe the stack (exempt from the source rule, unless inferred)
STACK = ("below", "above")
#: keys of an entry that are bookkeeping, not values typed from a document
_META = {"sources", "provenance", "estimate", "rms_peak_estimate", "ict_name", "model"}
#: the top-level keys whose values come from the PDK's documents
_TOP_DATA = ("em_tref", "temp_reference", "derating", "jmax_factor")


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _section(data, name: str) -> dict:
    """A section of the file (`layers`, `vias`, `cap_models`) when it is a
    mapping, else {} (the input check says what is wrong with it)."""
    s = data.get(name) if isinstance(data, dict) else None
    return s if isinstance(s, dict) else {}


def _entries(data: dict):
    """`(section, name, entry, quantity section)` of every layer, via and cap model."""
    for section, qsec in (("layers", "layers"), ("vias", "vias")):
        for name, d in _section(data, section).items():
            if isinstance(d, dict):
                yield section, name, d, qsec
    for name, d in _section(data, "cap_models").items():
        if isinstance(d, dict):
            yield "cap_models", name, d, ("mos" if set(_MOS_KEYS) & set(d) else "cap")


def _quantity(qsec: str, key: str) -> Q | None:
    return _BY_KEY.get(qsec, {}).get(key)


# ------------------------------------------------------------------ provenance


def file_provenance(data: dict) -> str | None:
    p = data.get("provenance") if isinstance(data, dict) else None
    return p if p in PROVENANCE else None


def entry_provenance(data: dict, entry: dict) -> str | None:
    """An entry's own `provenance:`, else the file's."""
    own = entry.get("provenance") if isinstance(entry, dict) else None
    return own if own in ENTRY_PROVENANCE else file_provenance(data)


def _sources(d) -> dict:
    s = d.get("sources") if isinstance(d, dict) else None
    return s if isinstance(s, dict) else {}


def _given(v) -> bool:
    """A value someone entered (not null, not an empty mapping or list)."""
    return v is not None and v != {} and v != []


def _named(s) -> bool:
    """A source that names something: text that is not blank."""
    return isinstance(s, str) and bool(s.strip())


def unsourced(data: dict) -> list:
    """Paths of the values a hand-entered (or mixed) file gives without a
    source: `em_tref`, `layers/M1/jmax_avg` ... Empty for a file that does
    not say it was entered by hand."""
    out = []
    if not isinstance(data, dict):
        return out
    if file_provenance(data) in HAND:
        top = _sources(data)
        for k in _TOP_DATA:
            if _given(data.get(k)) and not (k == "derating" and not _derating_given(data)) \
                    and not _named(top.get(k)):
                out.append(k)
    for section, name, d, _q in _entries(data):
        if entry_provenance(data, d) not in HAND:
            continue
        src = _sources(d)
        for k, v in d.items():
            if k in _META or k in STACK or not _given(v):
                continue
            if not _named(src.get(k)):
                out.append(f"{section}/{name}/{k}")
    return out


def unsourced_text(data: dict, missing: list) -> str:
    return (f"the process file was entered by hand ({file_provenance(data)}) and "
            f"{len(missing)} value(s) name no source (document, table or page): "
            + ", ".join(missing[:8]) + (" ..." if len(missing) > 8 else "")
            + " -- add each one under its entry's `sources:`")


def _derating_given(data: dict) -> bool:
    der = data.get("derating")
    return isinstance(der, dict) and (der.get("ea_eV") is not None or der.get("n") is not None)


def required_empty(data: dict) -> list:
    """Paths of the values emir cannot run without that are present but
    empty (null): `em_tref`, a layer's `sheet_res`, a via's `res`, `below`,
    `above`. (A key that is missing altogether is the input check's
    EMIR-E306 / EMIR-E312.)"""
    out = []
    if not isinstance(data, dict):
        return out
    if "em_tref" in data and data["em_tref"] is None:
        out.append("em_tref")
    for name, d in _section(data, "layers").items():
        if isinstance(d, dict) and "sheet_res" in d and d["sheet_res"] is None:
            out.append(f"layers/{name}/sheet_res")
    for name, d in _section(data, "vias").items():
        if isinstance(d, dict):
            for k in ("res", *STACK):
                if k in d and d[k] is None:
                    out.append(f"vias/{name}/{k}")
    return out


def empty_values(data: dict) -> dict:
    """`{"required": [...], "optional": [...]}`: every value present but
    empty, each `{path, unit, what}`."""
    req, opt = [], []
    if not isinstance(data, dict):
        return {"required": req, "optional": opt}
    must = set(required_empty(data))

    def add(path, q, what=None):
        item = {"path": path, "unit": q.unit if q else "", "what": what or (q.what if q else "")}
        (req if path in must else opt).append(item)
    for q in QUANTITIES["top"]:
        if q.key in data and data[q.key] is None:
            add(q.key, q)
    der = data.get("derating")
    if isinstance(der, dict):
        for q in QUANTITIES["derating"]:
            if q.key in der and der[q.key] is None:
                add(f"derating/{q.key}", q)
    for section, name, d, qsec in _entries(data):
        for k, v in d.items():
            if k in _META or v is not None:
                continue
            q = _quantity(qsec, k)
            if k in STACK:
                add(f"{section}/{name}/{k}", None, f"the layer {k} the via")
            elif k == "gds":
                add(f"{section}/{name}/gds", None,
                    "[layer, datatype]: optional, not read by emir")
            else:
                add(f"{section}/{name}/{k}", q)
    return {"required": req, "optional": opt}


def inferred(data: dict) -> list:
    """Paths whose source says `emir process new` inferred them (not yet
    confirmed by a person)."""
    out = []
    for section, name, d, _q in _entries(data if isinstance(data, dict) else {}):
        for k, s in _sources(d).items():
            if isinstance(s, str) and s.strip().lower().startswith(INFERRED):
                out.append(f"{section}/{name}/{k}")
    return out


# ------------------------------------------------------------------ the checks


def _limiting(path: str) -> bool:
    """A value whose unit slip changes what the limits allow: the temperatures
    the limits and R are quoted at, the derating, or a limit itself."""
    last = path.split("/")[-1]
    return (path in ("em_tref", "temp_reference", "jmax_factor")
            or path.startswith("derating/") or last.startswith(("jmax_", "imax_")))


def confirmed_units(data: dict) -> set:
    """The paths listed under the top-level `unit_confirmed:`: values the
    person checked against the design manual although they look like a unit
    slip."""
    u = data.get("unit_confirmed") if isinstance(data, dict) else None
    return {str(x).strip() for x in u if isinstance(x, str)} \
        if isinstance(u, (list, tuple)) else set()


def suspects(data: dict) -> list:
    """Every value outside the range its quantity normally takes in the unit
    emir reads (most often a unit slip): `{path, value, unit, range, likely,
    limiting, hand, confirmed, blocking}`. `blocking`: a value that sets what
    the limits allow (`_limiting`) and is not under `unit_confirmed:` -- the
    input check FAILs on it (EMIR-E331), whatever the file's `provenance:`
    says or whether it says one (a slip loosens the check all the same);
    every other one is a warning (EMIR-W321). `hand` says whether the value
    was entered by hand (the file's or the entry's provenance)."""
    out = []
    if not isinstance(data, dict):
        return out
    conf = confirmed_units(data)
    top_hand = file_provenance(data) in HAND

    def judge(path, q, v, hand):
        if q is None or not _num(v) or (q.lo is None and q.hi is None):
            return
        if q.lo is not None and v < q.lo:
            likely = q.low
        elif q.hi is not None and v > q.hi:
            likely = q.high
        else:
            return
        lim, ok = _limiting(path), path in conf
        out.append({"path": path, "value": v, "unit": q.unit, "range": [q.lo, q.hi],
                    "likely": likely or None, "limiting": lim, "hand": hand,
                    "confirmed": ok, "blocking": bool(lim and not ok)})
    for q in QUANTITIES["top"]:
        judge(q.key, q, data.get(q.key), top_hand)
    if isinstance(data.get("derating"), dict):
        for q in QUANTITIES["derating"]:
            judge(f"derating/{q.key}", q, data["derating"].get(q.key), top_hand)
    jf = data.get("jmax_factor")
    if isinstance(jf, dict):
        hot = [k for k in jf if _num(k) and k > 250]
        if hot:
            ok = "jmax_factor" in conf
            out.append({"path": "jmax_factor", "value": max(hot), "unit": "C",
                        "range": [-55, 250], "likely": _C, "limiting": True,
                        "hand": top_hand, "confirmed": ok,
                        "blocking": not ok})
    # a cap value EMIR-E317 refuses is not also a unit slip
    from .decap import refused_paths
    refused = refused_paths(_section(data, "cap_models"))
    for section, name, d, qsec in _entries(data):
        hand = entry_provenance(data, d) in HAND
        for k, v in d.items():
            if f"{section}/{name}/{k}" not in refused:
                judge(f"{section}/{name}/{k}", _quantity(qsec, k), v, hand)
    return out


def lint(data: dict, rep, em_temp=None) -> dict:
    """The hand-entry checks of a process file, as findings of `rep` (the
    input checker's `Report`): required values left empty (EMIR-E320),
    malformed provenance or sources (EMIR-E329), the derating form (E321,
    E322), values with no source (W320), unconfirmed inferred stacking
    (W324), sources for absent keys (W325), likely unit slips (W321; E331 on
    a temperature, the derating or a limit, until confirmed), limits
    out of order (W322), an upper metal more resistive than the one below
    (W323), optional values left empty (I320), no provenance stated (I321),
    a width loss in use (I323). Returns what it found, for `emir process
    check`'s document; `bad` is True when an E finding was added."""
    from .engine import black_derating
    facts = {"bad": False, "suspects": [], "unsourced": [], "inferred": [],
             "empty": empty_values(data), "provenance": file_provenance(data)}
    # -- required values left empty
    for path in required_empty(data):
        parts = path.split("/")
        q = (_quantity(parts[0], parts[2]) if len(parts) == 3 else _BY_KEY["top"].get(path))
        rep.fail("EMIR-E320", "process", f"{path} is empty: a required value"
                 + (f" ({q.unit}, {q.what})" if q else " (the via's stack)"),
                 f"type it from the PDK's documents, with its source, under {path}")
        facts["bad"] = True
    # -- provenance and sources: their form
    prov = data.get("provenance")
    if prov is not None and prov not in PROVENANCE:
        rep.fail("EMIR-E329", "process", f"provenance {prov!r} is not one of "
                 f"{', '.join(PROVENANCE)}")
        facts["bad"] = True
    holders = [("", data)] + [(f"{s}/{n}/", d) for s, n, d, _q in _entries(data)]
    for where, d in holders:
        if where and d.get("provenance") is not None and \
                d["provenance"] not in ENTRY_PROVENANCE:
            rep.fail("EMIR-E329", "process", f"{where}provenance {d['provenance']!r} is not "
                     f"one of {', '.join(ENTRY_PROVENANCE)}")
            facts["bad"] = True
        src = d.get("sources")
        if src is None:
            continue
        if not isinstance(src, dict):
            rep.fail("EMIR-E329", "process", f"{where}sources is not a mapping of "
                     f"key: source")
            facts["bad"] = True
            continue
        bad = [k for k, v in src.items() if v is not None and not isinstance(v, str)]
        if bad:
            rep.fail("EMIR-E329", "process", f"{where}sources: {', '.join(map(str, bad))} "
                     f"must be text (a document, table or page)")
            facts["bad"] = True
        keys = set(_TOP_DATA) if not where else set(d) - {"sources"}
        stray = sorted(str(k) for k in src if k not in keys)
        if stray:
            rep.warn("EMIR-W325", "process", f"{where}sources names {', '.join(stray)}, "
                     f"which {'the file' if not where else 'the entry'} does not hold")
    # -- the derating form
    try:
        black_derating(data)
    except EmirError as e:
        rep.fail(e.code, "process", e.message, e.hint)
        facts["bad"] = True
    # -- values with no source, stacking not confirmed
    facts["unsourced"] = unsourced(data)
    if facts["unsourced"]:
        rep.warn("EMIR-W320", "process", unsourced_text(data, facts["unsourced"]))
    facts["inferred"] = inferred(data)
    if facts["inferred"]:
        rep.warn("EMIR-W324", "process", f"{len(facts['inferred'])} value(s) were inferred "
                 f"from the DSPF and not confirmed: " + ", ".join(facts["inferred"][:8])
                 + (" ..." if len(facts["inferred"]) > 8 else ""),
                 "confirm each against the PDK's layer stack and replace its `inferred` "
                 "source with the document")
    # -- unit slips: each value against its quantity's plausible range. A slip
    # on a temperature or a limit can loosen every check (K typed for C, uA
    # for mA): it blocks the run until the person confirms the value
    # (`unit_confirmed:`), whatever provenance the file states, if any
    facts["suspects"] = suspects(data)
    for x in facts["suspects"]:
        text = (f"{x['path']} = {x['value']:g} is outside the range {x['range'][0]:g} .. "
                f"{x['range'][1]:g} {x['unit']} it normally takes"
                + (f": {x['likely']}" if x["likely"] else ""))
        if x["blocking"]:
            rep.fail("EMIR-E331", "process", text + " -- this value sets what the limits "
                     "allow, so a slip here can pass a failing wire", f"ask the person to check {x['path']} and its unit "
                     f"({x['unit']}) against the design manual; once they confirm it, list "
                     f"it under `unit_confirmed:`")
            facts["bad"] = True
        else:
            rep.warn("EMIR-W321", "process", text + (" (confirmed by the person: "
                                                     "unit_confirmed)" if x["confirmed"]
                                                     else ""),
                     f"check {x['path']} and its unit ({x['unit']}) against the design "
                     f"manual")
    # -- limits out of order: peak >= rms >= avg where given
    for section, name, d, qsec in _entries(data):
        if section == "cap_models":
            continue
        pre = "jmax" if section == "layers" else "imax"
        vals = {m: d.get(f"{pre}_{m}") for m in ("avg", "rms", "peak")}
        vals = {m: v for m, v in vals.items() if _num(v) and v > 0}
        bad = []
        if "peak" in vals and "rms" in vals and vals["peak"] < vals["rms"]:
            bad.append(f"peak {vals['peak']:g} < rms {vals['rms']:g}")
        if "peak" in vals and "avg" in vals and vals["peak"] < vals["avg"]:
            bad.append(f"peak {vals['peak']:g} < avg {vals['avg']:g}")
        if section == "vias" and "rms" in vals and "avg" in vals and vals["rms"] < vals["avg"]:
            # a layer's rms < avg is EMIR-W301 (the input checker's own)
            bad.append(f"rms {vals['rms']:g} < avg {vals['avg']:g}")
        if bad:
            rep.warn("EMIR-W322", "process", f"{section}/{name}: limits out of order "
                     f"({'; '.join(bad)}): peak >= rms >= avg for every waveform",
                     f"check {section}/{name}'s limits against the design manual")
    # -- an upper metal more resistive than the one below it
    layers = _section(data, "layers")
    for vname, v in _section(data, "vias").items():
        if not isinstance(v, dict):
            continue
        lo, hi = layers.get(v.get("below")), layers.get(v.get("above"))
        if not (isinstance(lo, dict) and isinstance(hi, dict)):
            continue
        rlo, rhi = lo.get("sheet_res"), hi.get("sheet_res")
        if _num(rlo) and _num(rhi) and rlo > 0 and rhi > rlo * 1.05:
            rep.warn("EMIR-W323", "process", f"over {vname}, {v['above']} has sheet_res "
                     f"{rhi:g} ohm/sq, above {v['below']}'s {rlo:g}: upper metals are "
                     f"usually as thick or thicker (a unit or row slip?)",
                     "check both sheet_res values against the design manual")
    # -- what is not entered, and what the file says of itself
    if facts["empty"]["optional"]:
        opt = facts["empty"]["optional"]
        rep.info("EMIR-I320", "process", f"{len(opt)} optional value(s) empty, so what "
                 f"they check or scale is not used: "
                 + ", ".join(x["path"] for x in opt[:6]) + (" ..." if len(opt) > 6 else ""))
    if prov is None:
        rep.info("EMIR-I321", "process", "the file does not say where its limits came "
                 "from (no `provenance:`)")
    wl = sorted(n for n, d in layers.items()
                if isinstance(d, dict) and _num(d.get("width_loss_um")) and d["width_loss_um"] > 0)
    if wl:
        rep.info("EMIR-I323", "process", f"wire limits I_max = J x (W - dW) on "
                 f"{', '.join(wl)} (width_loss_um)")
    return facts


def check_widths(dspf, proc, lmap: dict, rep):
    """EMIR-E323 for every wire no wider than its layer's width loss."""
    per = {}
    for r in dspf.res:
        hit = lmap.get(r["layer"])
        if not hit or hit[0] != "layer" or not r.get("w"):
            continue
        dw = proc.layers[hit[1]]["width_loss"]
        if dw and r["w"] <= dw:
            per.setdefault(hit[1], []).append(f"{r['name']} ({r['w']:g} um)")
    if per:
        n = sum(len(v) for v in per.values())
        rep.fail("EMIR-E323", "layers", f"{n} wire segment(s) not wider than their layer's "
                 f"width_loss_um, so I_max = J x (W - dW) is not positive: "
                 + "; ".join(f"{lay} (dW {proc.layers[lay]['width_loss']:g} um): "
                             f"{', '.join(v[:4])}{' ...' if len(v) > 4 else ''}"
                             for lay, v in sorted(per.items())),
                 "check width_loss_um (um, not nm) against the design manual")


# ------------------------------------------------------------------ what a run says


def limits_provenance(proc, lmap_values, cap_models=False) -> dict | None:
    """Where the limits a run applied came from: the file's provenance, and
    per layer, via (and, with `cap_models`, cap model: a --dynamic run) in
    use its own provenance, the source of each value and the values that
    name none; the temperatures' sources (and which name none); and the
    values that look like a unit slip (`suspects`). For the run summary
    (`limits_provenance`), `emir report`, the HTML report and the chart."""
    if proc is None:
        return None
    data = proc.data
    entries = []
    seen = set()
    for kind, name in sorted(set(lmap_values)):
        if (kind, name) in seen:
            continue
        seen.add((kind, name))
        section = "vias" if kind == "via" else "layers"
        d = (data.get(section) or {}).get(name) or {}
        src = _sources(d)
        prov = entry_provenance(data, d)
        limits = [k for k in d if k.startswith(("jmax_", "imax_")) and _given(d.get(k))]
        values = [k for k in d if k not in _META and k not in STACK and _given(d.get(k))]
        entries.append({
            "name": name, "kind": "via" if kind == "via" else "wire", "provenance": prov,
            "sources": {k: src.get(k) for k in values},
            "unsourced": ([k for k in values if not src.get(k)] if prov in HAND else []),
            "limits": limits,
            "width_loss_um": (proc.layers[name]["width_loss"] or None
                              if kind != "via" and name in proc.layers else None)})
    if cap_models:
        for name, d in sorted(_section(data, "cap_models").items()):
            if not isinstance(d, dict):
                continue
            src = _sources(d)
            prov = entry_provenance(data, d)
            values = [k for k in d if k not in _META and _given(d.get(k))]
            entries.append({"name": name, "kind": "cap", "provenance": prov,
                            "sources": {k: src.get(k) for k in values},
                            "unsourced": ([k for k in values if not _named(src.get(k))]
                                          if prov in HAND else []),
                            "limits": [], "width_loss_um": None})
    top = _sources(data)
    der = getattr(proc, "derating", None)
    hand = file_provenance(data) in HAND
    t_unsourced = []
    if hand:
        for k in ("em_tref", "temp_reference"):
            if _given(data.get(k)) and not _named(top.get(k)):
                t_unsourced.append(k)
        if der and not _named(top.get("derating")):
            t_unsourced.append("derating")
        if not der and proc.jmax_factor and not _named(top.get("jmax_factor")):
            t_unsourced.append("jmax_factor")
    return {"file": file_provenance(data), "entries": entries,
            "temperature": {"em_tref_C": proc.em_tref, "em_tref_source": top.get("em_tref"),
                            "derating": ("black" if der else "table" if proc.jmax_factor
                                         else None),
                            "black": der or None,
                            "derating_source": (top.get("derating") if der else
                                                top.get("jmax_factor")),
                            "unsourced": t_unsourced},
            "suspects": [{k: x[k] for k in ("path", "value", "unit", "likely", "confirmed",
                                            "blocking")} for x in suspects(data)]}


def limits_lines(lp: dict | None) -> list:
    """`limits_provenance` in sentences, one per entry (for `emir report`)."""
    if not lp:
        return []
    t = lp.get("temperature") or {}
    said = (lp.get("file") or t.get("em_tref_source") or t.get("derating_source")
            or any(e.get("provenance") or any((e.get("sources") or {}).values())
                   for e in lp.get("entries") or []))
    if not said:
        # a file that says nothing of where its values came from: one line
        return ["the process file does not say where its limits came from (no `provenance:`, "
                "no `sources:`)"]
    out = [f"process file: {PROVENANCE_TEXT.get(lp.get('file'), lp.get('file'))}"]
    nos = set(t.get("unsourced") or [])
    if _named(t.get("em_tref_source")):
        out.append(f"em_tref {t.get('em_tref_C'):g} C from {t['em_tref_source']}")
    elif "em_tref" in nos:
        out.append(f"em_tref {t.get('em_tref_C'):g} C -- NO SOURCE")
    if "temp_reference" in nos:
        out.append("temp_reference -- NO SOURCE")
    if t.get("derating") == "black" and t.get("black"):
        b = t["black"]
        out.append(f"avg derating: Black's equation, Ea {b['ea_eV']:g} eV, n {b['n']:g}"
                   + (f" (from {t['derating_source']})" if _named(t.get("derating_source"))
                      else " -- NO SOURCE" if "derating" in nos else ""))
    elif "jmax_factor" in nos:
        out.append("avg derating: the jmax_factor table -- NO SOURCE")
    for x in lp.get("suspects") or []:
        out.append(f"SUSPECT UNIT: {x['path']} = {x['value']:g} {x['unit']} is outside the "
                   f"range it normally takes" + (f" ({x['likely']})" if x.get("likely") else "")
                   + (" -- confirmed by the person (unit_confirmed)" if x.get("confirmed")
                      else " -- check it against the design manual (EMIR-W321)"))
    for e in lp.get("entries") or []:
        what = {"via": "via", "cap": "cap model"}.get(e["kind"], "layer")
        how = PROVENANCE_TEXT.get(e.get("provenance"), e.get("provenance"))
        given = [f"{k} from {s}" for k, s in (e.get("sources") or {}).items()
                 if _named(s) and (k in (e.get("limits") or []) or e["kind"] == "cap")]
        line = f"{what} {e['name']}: " + ("values" if e["kind"] == "cap" else "limits") \
            + f" {how}"
        if given:
            line += " -- " + "; ".join(given)
        if not e.get("limits") and e["kind"] != "cap":
            line += " -- no EM limit given (not checked)"
        if e.get("unsourced"):
            line += f" -- NO SOURCE for {', '.join(e['unsourced'])}"
        if e.get("width_loss_um"):
            line += f" -- width loss {e['width_loss_um']:g} um (I_max = J x (W - dW))"
        out.append(line)
    return out


# ------------------------------------------------------------------ the fill-in file


@dataclass
class Entry:
    """One layer, via or cap model of the fill-in file."""
    section: str            # layers | vias | cap_models
    name: str
    qsec: str               # the QUANTITIES section of its values
    note: str = ""          # the comment on its line
    below: str | None = None
    above: str | None = None
    stack_note: str | None = None
    #: the two layers a via joins when the DSPF does not say which is below
    candidates: tuple | None = None


def _token_name(tok: str) -> str:
    from .dspf import LVL_TOKEN
    return f"lvl{tok[len(LVL_TOKEN):]}" if tok.startswith(LVL_TOKEN) else tok


def _trailing_int(s: str):
    m = re.search(r"(\d+)\D*$", s)
    return int(m.group(1)) if m else None


def from_dspf(path: str) -> dict:
    """What a fill-in file for the DSPF at `path` holds: `{"design", "layers",
    "vias", "caps", "mos", "dspf_layers"}` -- one entry per layer token its
    resistors carry, wires and vias told apart by their annotation (a width,
    or a cut count or area), each via's stacking inferred from the wires its
    resistors join, and the capacitor and MOS-decap models."""
    from .dspf import LVL_TOKEN, parse
    d = parse(path)
    toks = d.layer_tokens()
    if not toks:
        raise EmirError("EMIR-E328", f"{path}: no resistor names a layer ($metal1, "
                        f"$layer=..., or a $lvl number): there is nothing to write a "
                        f"process file for", dspf=path)
    stats = {t: {"wire": 0, "via": 0, "bare": 0, "lvl": []} for t in toks}
    for r in d.res:
        t = r["layer"]
        if not t:
            continue
        s = stats[t]
        s["via" if r.get("via") else "wire" if r.get("via") is False else "bare"] += 1
        if r.get("lvl") is not None:
            s["lvl"].append(r["lvl"])

    def kind(t):
        s = stats[t]
        if s["via"]:
            return "via"
        if s["wire"]:
            return "layer"
        return "via" if re.search(r"(?i)via|cont|cut|mcon|vi", t) else "layer"
    kinds = {t: kind(t) for t in toks}
    lvl = {t: (sum(s["lvl"]) / len(s["lvl"]) if s["lvl"] else None)
           for t, s in stats.items()}
    names = {t: _token_name(t) for t in toks}
    # which wire layers touch each node
    at = {}
    for r in d.res:
        if r["layer"] and kinds.get(r["layer"]) == "layer":
            for n in (r["n1"], r["n2"]):
                at.setdefault(n, {}).setdefault(r["layer"], 0)
                at[n][r["layer"]] += 1
    layers, vias = [], []
    for t in toks:
        s = stats[t]
        bits = [f"DSPF token `{t}`" if not t.startswith(LVL_TOKEN) else
                f"DSPF $lvl={t[len(LVL_TOKEN):]} (no layer name)"]
        if kinds[t] == "layer":
            bits.append(f"{s['wire'] + s['bare']} wire resistor(s)")
            if lvl[t] is not None and not t.startswith(LVL_TOKEN):
                bits.append(f"$lvl {lvl[t]:g}")
            layers.append(Entry("layers", names[t], "layers", note=", ".join(bits)))
            continue
        bits.append(f"{s['via'] + s['bare']} via resistor(s)")
        e = Entry("vias", names[t], "vias", note=", ".join(bits))
        near = {}
        for r in d.res:
            if r["layer"] != t:
                continue
            for n in (r["n1"], r["n2"]):
                for lt, k in (at.get(n) or {}).items():
                    near[lt] = near.get(lt, 0) + k
        two = sorted(near, key=lambda x: -near[x])[:2]
        if len(two) == 2:
            a, b = two
            la, lb = lvl.get(a), lvl.get(b)
            if la is not None and lb is not None and la != lb:
                lo, hi = (a, b) if la < lb else (b, a)
                how = "by their $lvl"
            else:
                ia, ib = _trailing_int(a), _trailing_int(b)
                if ia is not None and ib is not None and ia != ib:
                    lo, hi = (a, b) if ia < ib else (b, a)
                    how = "by the numbers in their names"
                else:
                    # nothing says which is below: never a guess (an upside-down
                    # contact moves every device-pin tap onto the wrong layer).
                    # Left empty, the input check refuses it (EMIR-E320) until
                    # the person names both from the PDK's layer stack
                    lo = hi = None
                    e.candidates = (names[a], names[b])
                    e.stack_note = (f"its resistors join {names[a]} and {names[b]} in the "
                                    f"DSPF, and nothing there says which is below (no $lvl, "
                                    f"no number in their names): name below and above from "
                                    f"the PDK's layer stack")
            if lo is not None:
                e.below, e.above = names[lo], names[hi]
                e.stack_note = (f"{INFERRED} from the DSPF: its resistors join {lo} and "
                                f"{hi} ({how}): check")
        elif len(two) == 1:
            a = two[0]
            if lvl.get(a) is not None and lvl.get(t) is not None and lvl[a] != lvl[t]:
                if lvl[a] > lvl[t]:
                    e.above = names[a]
                    e.stack_note = (f"{INFERRED} from the DSPF: it joins {a} ($lvl "
                                    f"{lvl[a]:g}) above its own $lvl {lvl[t]:g}; what is "
                                    f"below is not in the DSPF: check")
                else:
                    e.below = names[a]
                    e.stack_note = (f"{INFERRED} from the DSPF: it joins {a} ($lvl "
                                    f"{lvl[a]:g}) below its own $lvl {lvl[t]:g}: check")
            else:
                e.stack_note = f"joins only {a} in the DSPF: name both layers from the PDK"
        vias.append(e)
    # capacitor devices (a geometry, no value) and MOS decaps, by model
    caps = sorted({c["model"] or "default" for c in d.cap if c.get("c") is None})
    mos = []
    for m in d.dev:
        ng = d.node_net.get(m["g"])
        others = {d.node_net.get(m[k]) for k in ("d", "s", "b")}
        if len(others) == 1:
            no = next(iter(others))
            if ng is not None and no is not None and ng != no and m["model"] not in mos:
                mos.append(m["model"])
    dl = {}
    for t in toks:
        if t.startswith(LVL_TOKEN):
            dl[int(t[len(LVL_TOKEN):])] = names[t]
        else:
            dl[t] = names[t]
    return {"design": d.design, "layers": layers, "vias": vias, "caps": caps,
            "mos": sorted(mos), "dspf_layers": dl}


def from_lists(layers, vias) -> dict:
    """The same, from --layers and --vias name lists (no stacking: below and
    above are left for the person)."""
    return {"design": None, "layers": [Entry("layers", n, "layers") for n in layers],
            "vias": [Entry("vias", n, "vias") for n in vias], "caps": [], "mos": [],
            "dspf_layers": {}}


#: the values of each entry kind, in the order the file lists them
_LAYER_KEYS = ("sheet_res", "tc1", "tc2", "width_loss_um", "jmax_avg", "jmax_rms",
               "jmax_peak")
_VIA_KEYS = ("below", "above", "res", "vcwidth", "imax_avg", "imax_rms", "imax_peak")
_CAP_KEYS = ("cj", "cjsw")
_MOS_KEYS = ("cox", "ron_sq", "esr_a", "esr_w", "esr_l", "esr_fit_freq")


def build_model(info: dict, pdk: str | None) -> dict:
    """The fill-in file as data: every value None, every source None, the
    stacking and its inferred source where `from_dspf` found it."""
    data = {"pdk": pdk, "provenance": "hand-entered", "em_tref": None,
            "temp_reference": None,
            "derating": {"model": "black", "ea_eV": None, "n": None},
            "sources": {"em_tref": None, "temp_reference": None, "derating": None}}
    if info["dspf_layers"]:
        data["dspf_layers"] = dict(info["dspf_layers"])
    data["layers"] = {}
    for e in info["layers"]:
        data["layers"][e.name] = {k: None for k in _LAYER_KEYS}
        data["layers"][e.name]["sources"] = {k: None for k in _LAYER_KEYS}
    data["vias"] = {}
    for e in info["vias"]:
        v = {k: None for k in _VIA_KEYS}
        v["below"], v["above"] = e.below, e.above
        v["sources"] = {k: None for k in _VIA_KEYS}
        if e.stack_note and e.stack_note.startswith(INFERRED):
            for k in STACK:
                if v[k] is not None:
                    v["sources"][k] = e.stack_note
        data["vias"][e.name] = v
    caps = {}
    for m in info["caps"]:
        caps[m] = {"cj": None, "cjsw": None, "sources": {"cj": None, "cjsw": None}}
    for m in info["mos"]:
        # the fit (esr_*) where the PDK characterises one, else ron_sq
        caps[m] = {k: None for k in _MOS_KEYS}
        caps[m]["sources"] = {k: None for k in _MOS_KEYS}
    if caps:
        data["cap_models"] = caps
    return data


def _y(v) -> str:
    """A YAML scalar: null, a number, or a string (double-quoted when it
    needs it)."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v) if isinstance(v, float) else str(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_y(x) for x in v) + "]"
    s = str(v)
    import yaml
    plain = yaml.safe_dump(s, default_flow_style=True, width=10 ** 6).strip()
    if plain.endswith("..."):
        plain = plain[:-3].strip()
    try:
        if yaml.safe_load(plain) == s and "\n" not in plain:
            return plain
    except yaml.YAMLError:
        pass
    return json.dumps(s)


def _k(name) -> str:
    """A YAML key."""
    return str(name) if isinstance(name, int) else _y(str(name))


def _line(indent: int, text: str, comment: str = "", col: int = 34) -> str:
    """`text` at `indent`, its comment from column `col` (or one space after)."""
    s = " " * indent + text
    if not comment:
        return s
    return (s.ljust(col) if len(s) < col else s + " ") + "# " + comment


def _qcomment(q: Q | None, required=False, extra="") -> str:
    if q is None:
        return extra
    unit = f"{q.unit}, " if q.unit else ""
    return ("R. " if required or q.required else "") + unit + q.what + (f"; {extra}" if extra else "")


def render(data: dict, info: dict, source: str | None) -> str:
    """The fill-in file's text, from `build_model`'s data (values as they
    stand: None is written null)."""
    L = ["# emir process file -- ENTERED BY HAND (provenance: hand-entered).",
         f"# Written by `emir process new`{' from ' + os.path.basename(source) if source else ''}"
         f" on {datetime.date.today().isoformat()}: {len(info['layers'])} conductor layer(s), "
         f"{len(info['vias'])} via(s)"
         + (f", {len(data.get('cap_models') or {})} capacitor / decap model(s)"
            if data.get("cap_models") else "") + ".",
         "#",
         "# Fill each null with the value your PDK's design manual prints, in the unit",
         "# shown, and write where it came from (document, table or page) in the entry's",
         "# `sources:`. Leave a value null when the manual does not give it: emir then",
         "# reports that check as NOT checked. Never estimate, derive or copy a 'typical'",
         "# limit, and keep this file with your PDK installation (it holds its data).",
         "# R = required: emir refuses to run while it is empty. Then check it:",
         "#   emir process check <this file>"
         + (f" --dspf {os.path.basename(source)}" if source else ""),
         "# Screening, not sign-off.", ""]
    top = _BY_KEY["top"]
    L.append(f"pdk: {_y(data.get('pdk'))}" if data.get("pdk") else
             _line(0, "pdk: null", "the PDK's name, shown in reports"))
    L.append(_line(0, f"provenance: {_y(data.get('provenance'))}",
                   "hand-entered | imported-ict | public-pdk-data | mixed"))
    for k in ("em_tref", "temp_reference"):
        L.append(_line(0, f"{k}: {_y(data.get(k))}", _qcomment(top[k])))
    der = data.get("derating") or {}
    L.append(_line(0, "derating:", "avg-limit derating, Black's equation:"))
    L.append(_line(2, f"model: {_y(der.get('model', 'black'))}",
                   "J(T)/J(em_tref) = exp[(ea_eV/(n k)) (1/T - 1/Tref)], T in K"))
    for q in QUANTITIES["derating"]:
        L.append(_line(2, f"{q.key}: {_y(der.get(q.key))}", _qcomment(q)))
    L.append("# jmax_factor: {C: factor, ...} is the table form of the same derating; give")
    L.append("# one of the two (both empty: the avg limits are used as quoted at em_tref).")
    L.append(_line(0, "sources:", "where each value above came from"))
    for k, s in (data.get("sources") or {}).items():
        L.append(f"  {_k(k)}: {_y(s)}")
    if data.get("dspf_layers"):
        L.append("")
        L.append(_line(0, "dspf_layers:", "the DSPF's layer tokens -> the entries below"))
        for k, v in data["dspf_layers"].items():
            L.append(f"  {_k(k)}: {_y(v)}")
    notes = {("layers", e.name): e for e in info["layers"]}
    notes.update({("vias", e.name): e for e in info["vias"]})
    for section in ("layers", "vias"):
        L += ["", f"{section}:"]
        for name, d in (data.get(section) or {}).items():
            e = notes.get((section, name))
            L.append(_line(2, f"{_k(name)}:", e.note if e else ""))
            qs = _BY_KEY[section]
            for k, v in d.items():
                if k == "sources":
                    continue
                if k in STACK:
                    c = ("R. the layer under the cut" if k == "below" else
                         "R. the layer over the cut")
                    if e and e.stack_note and v is not None:
                        c += ": inferred, check"
                    elif e and e.candidates:
                        c += (f": {' or '.join(e.candidates)} (the DSPF does not say which "
                              f"is below): name it from the PDK's stack")
                    elif e and e.stack_note:
                        c += ": not in the DSPF, name it from the PDK's stack"
                elif k == "gds":
                    c = "[layer, datatype]: optional, not read by emir"
                else:
                    c = _qcomment(qs.get(k))
                L.append(_line(4, f"{k}: {_y(v)}", c))
            L.append(_line(4, "sources:", "document, table or page of each value"))
            for k, s in (d.get("sources") or {}).items():
                L.append(f"      {k}: {_y(s)}")
    if data.get("cap_models"):
        L += ["", _line(0, "cap_models:", "--dynamic only: sizes the DSPF's capacitor "
                                           "and MOS-decap devices")]
        for name, d in data["cap_models"].items():
            mos = "cox" in d
            L.append(_line(2, f"{_k(name)}:", "a MOS decap model" if mos else
                           ("a capacitor device with no model name" if name == "default"
                            else "a capacitor model")))
            qs = _BY_KEY["mos" if mos else "cap"]
            for k, v in d.items():
                if k == "sources":
                    continue
                L.append(_line(4, f"{k}: {_y(v)}", _qcomment(qs.get(k))))
            L.append(_line(4, "sources:", "document, table or page of each value"))
            for k, s in (d.get("sources") or {}).items():
                L.append(f"      {k}: {_y(s)}")
    return "\n".join(L) + "\n"


def fields(data: dict) -> list:
    """`(holder, key, quantity, path)` of every value a person types, in
    the file's order (the stacking last within a via, after its values)."""
    out = []
    for q in QUANTITIES["top"]:
        out.append((data, q.key, q, q.key))
    for q in QUANTITIES["derating"]:
        out.append((data["derating"], q.key, q, f"derating/{q.key}"))
    for section, name, d, qsec in _entries(data):
        for k in list(d):
            if k in _META:
                continue
            q = _quantity(qsec, k)
            out.append((d, k, q, f"{section}/{name}/{k}"))
    return out


def _source_holder(data: dict, path: str):
    """The `sources` map a value's source goes in, and its key there."""
    parts = path.split("/")
    if len(parts) == 1:
        return data.setdefault("sources", {}), parts[0]
    if parts[0] == "derating":
        return data.setdefault("sources", {}), "derating"
    d = data[parts[0]][parts[1]]
    return d.setdefault("sources", {}), parts[2]


class _Stopped(Exception):
    """End of input (Ctrl-D) while asking; caught in `interactive`."""


def interactive(data: dict, write, stdin=None, stderr=None) -> int:
    """Ask a person for each value, at a terminal: the unit shown, Enter
    leaves it empty (not given: not checked), a non-number is refused and
    asked again, and a source is asked for each value given (`=` repeats the
    previous one; Enter leaves it without one, which every report then says,
    EMIR-W320: a value never gets another value's source unless the person
    types `=`). `write(data)` is called after every value, so an
    interruption loses nothing typed. Returns how many values were given.

    It refuses a stdin that is not a terminal (a pipe, a redirect:
    EMIR-E327); that is a check on stdin, not on who types."""
    stdin = stdin or sys.stdin
    stderr = stderr or sys.stderr
    try:
        tty = bool(stdin.isatty())
    except (AttributeError, ValueError):
        tty = False
    if not tty:
        raise EmirError("EMIR-E327", "--interactive asks a person at a terminal, and stdin "
                        "is not one: write the file without --interactive and have the "
                        "person type the values")

    def ask(prompt):
        stderr.write(prompt)
        stderr.flush()
        line = stdin.readline()
        if not line:
            raise _Stopped()
        return line.strip()
    stderr.write("emir process new --interactive: type each value in the unit shown, as the "
                 "design manual prints it.\nEnter leaves a value empty (not given: that check "
                 "is not made). Give each value's source; `=` repeats the previous source. "
                 "Never estimate a limit. Ctrl-D stops; what you typed is kept.\n\n")
    given, last = 0, ""
    try:
        for holder, key, q, path in fields(data):
            if key == "gds":
                continue                      # optional, read by no path
            if key in STACK:
                cur = holder.get(key)
                hint = f" [{cur}]" if cur else ""
                ans = ask(f"{path} (layer name){hint}: ")
                if ans:
                    holder[key] = ans
                if holder.get(key):
                    sh, sk = _source_holder(data, path)
                    # Enter keeps the source it has (an inferred one stays
                    # inferred, and `emir process check` keeps saying so); `=`
                    # takes the previous value's
                    keep = sh.get(sk)
                    s = ask(f"  source of {key} (document, table or page"
                            f"{'; = for ' + repr(last) if last else ''})"
                            f"{' [' + keep + ']' if keep else ''}: ")
                    s = last if s == "=" else (s or keep)
                    if s:
                        sh[sk] = s
                        if not s.lower().startswith(INFERRED):
                            last = s
                write(data)
                continue
            unit = f" ({q.unit})" if q and q.unit else ""
            what = f"  -- {q.what}" if q else ""
            while True:
                ans = ask(f"{path}{unit}{what}: ")
                if not ans:
                    break
                try:
                    v = float(ans)
                except ValueError:
                    stderr.write(f"  not a number: {ans!r}. Type the number alone, in "
                                 f"{q.unit if q else 'the unit shown'} (Enter: leave it "
                                 f"empty)\n")
                    continue
                if v != v or v in (float("inf"), float("-inf")):
                    stderr.write("  not a finite number\n")
                    continue
                holder[key] = int(v) if v.is_integer() and q and q.key == "n" else v
                given += 1
                if q and ((q.lo is not None and v < q.lo) or (q.hi is not None and v > q.hi)):
                    likely = q.low if q.lo is not None and v < q.lo else q.high
                    stderr.write(f"  note: {v:g} is outside the range {q.lo:g} .. {q.hi:g} "
                                 f"{q.unit} this quantity normally takes"
                                 + (f" ({likely})" if likely else "")
                                 + ": kept as typed; check the unit\n")
                sh, sk = _source_holder(data, path)
                s = ask(f"  source of {key} (document, table or page"
                        f"{'; = for ' + repr(last) if last else ''}; Enter: none): ")
                s = last if s == "=" else s
                if s:
                    sh[sk] = s
                    last = s
                break
            write(data)
    except (_Stopped, KeyboardInterrupt):
        stderr.write("\nstopped: the values typed so far are written.\n")
        write(data)
    return given


# ------------------------------------------------------------------ the commands


def _write(path: str, text: str):
    from .output import write_text
    write_text(path, text)


def cmd_new(argv, out) -> int:
    """`emir process new`."""
    from .output import command_line, document, keep_format, parser
    ap = parser("emir process new",
                description="Write a fill-in process file for the layers and vias a DSPF "
                            "uses (or the ones you list), every value empty with its unit "
                            "and a source slot, for a person to type from the PDK's design "
                            "manual when no readable process data exists (the ICT is missing "
                            "or encrypted). It never fills in a value.")
    ap.add_argument("--from-dspf", default=None, metavar="DSPF",
                    help="the DSPF whose layer tokens, $lvl numbers, vias and capacitor / "
                         "MOS-decap models the file covers")
    ap.add_argument("--pdk", default=None, metavar="NAME",
                    help="the PDK's name, written as `pdk:` (shown in reports)")
    ap.add_argument("-o", "--out", default="emir_out/process.yaml", metavar="FILE",
                    help="the file to write (default emir_out/process.yaml); an existing "
                         "file is never overwritten (EMIR-E326)")
    ap.add_argument("--layers", default=None, metavar="L1,L2",
                    help="without --from-dspf: the conductor layers, comma-separated")
    ap.add_argument("--vias", default=None, metavar="V1,V2",
                    help="without --from-dspf: the vias, comma-separated (their below/above "
                         "are left for the person)")
    ap.add_argument("--interactive", action="store_true",
                    help="ask a person for each value and its source at a terminal; refused "
                         "when stdin is not a terminal (agents never use it)")
    a = ap.parse_args(argv)
    if a.from_dspf and (a.layers or a.vias):
        raise EmirError("EMIR-E513", "give --from-dspf, or --layers/--vias, not both",
                        command="process")
    if a.interactive:
        try:
            tty = sys.stdin.isatty()
        except (AttributeError, ValueError):
            tty = False
        if not tty:
            raise EmirError("EMIR-E327", "--interactive asks a person at a terminal, and "
                            "stdin is not one: write the file without --interactive and "
                            "have the person type the values")
    if os.path.lexists(a.out):
        raise EmirError("EMIR-E326", f"{a.out} exists: emir process new writes a new file "
                        f"only (a process file may hold typed values)", path=a.out)
    if a.from_dspf:
        if not os.path.isfile(a.from_dspf):
            raise EmirError("EMIR-E101", f"DSPF not found: {a.from_dspf}", path=a.from_dspf)
        info = from_dspf(a.from_dspf)
    else:
        lay = [x.strip() for x in (a.layers or "").split(",") if x.strip()]
        via = [x.strip() for x in (a.vias or "").split(",") if x.strip()]
        if not lay:
            raise EmirError("EMIR-E328", "give --from-dspf <dspf>, or --layers (and --vias): "
                            "the layers and vias to write the file for")
        dup = sorted({x for x in lay + via if (lay + via).count(x) > 1})
        if dup:
            raise EmirError("EMIR-E513", f"named twice: {', '.join(dup)}", command="process")
        info = from_lists(lay, via)
    data = build_model(info, a.pdk)
    src = a.from_dspf

    def write(d):
        _write(a.out, render(d, info, src))
    write(data)
    filled = interactive(data, write) if a.interactive else 0
    import yaml
    with open(a.out, encoding="utf-8") as f:
        back = yaml.safe_load(f)
    empty = empty_values(back)
    path = os.path.abspath(a.out)
    print(f"[emir] wrote {a.out}: {len(info['layers'])} layer(s), {len(info['vias'])} via(s)"
          + (f", {len(data.get('cap_models') or {})} cap model(s)" if data.get("cap_models")
             else "") + f"; {len(empty['required'])} required and "
          f"{len(empty['optional'])} optional value(s) still empty")
    for e in info["vias"]:
        if e.stack_note:
            print(f"  via {e.name}: {e.stack_note}")
    check = command_line("process", "check", a.out,
                         *(["--dspf", a.from_dspf] if a.from_dspf else []), out.fmt_args())
    steps = []
    if not a.interactive or empty["required"]:
        steps.append({"why": "the person types each value from the PDK's design manual, in "
                             "the unit shown, with its source (document, table or page) -- or "
                             "the agent transcribes them from a manual the person gives and "
                             "the person confirms each one; never estimate a limit. Then "
                             "check what is still missing",
                      "command": check})
    else:
        steps.append({"why": "check the file before any run", "command": check})
    out.emit(document(
        "process_new", out=path, dspf=os.path.abspath(a.from_dspf) if a.from_dspf else None,
        design=info.get("design"), pdk=a.pdk, provenance="hand-entered",
        layers=[{"name": e.name, "note": e.note} for e in info["layers"]],
        vias=[{"name": e.name, "below": data["vias"][e.name]["below"],
               "above": data["vias"][e.name]["above"],
               "stacking": e.stack_note} for e in info["vias"]],
        cap_models=sorted(data.get("cap_models") or {}),
        dspf_layers={str(k): v for k, v in (data.get("dspf_layers") or {}).items()},
        empty=empty, interactive=bool(a.interactive), values_given=filled,
        next_steps=keep_format(steps, out.fmt_args())))
    return 0


def coverage(data: dict, dspf) -> dict:
    """The DSPF's layer tokens and which the process file does not cover
    (neither by name, suffix folding nor `dspf_layers`)."""
    from types import SimpleNamespace

    from .dspf import map_layers
    view = SimpleNamespace(layers=_section(data, "layers"), vias=_section(data, "vias"),
                           data=data)
    lmap = map_layers(dspf, view)
    toks = dspf.layer_tokens()
    missing = [t for t in toks if t not in lmap]
    return {"tokens": toks, "missing": missing,
            "segments_not_covered": sum(1 for r in dspf.res if r["layer"] in set(missing))}


def cmd_check(argv, out) -> int:
    """`emir process check`."""
    from .check import Report, check_layer_map, check_process
    from .output import command_line, document, keep_format, parser
    ap = parser("emir process check",
                description="What a process file still lacks before a run: empty values "
                            "(required: emir refuses to run; optional: that check is not "
                            "made), values with no source in a hand-entered file, likely unit "
                            "slips, limits out of order, and with --dspf the DSPF layers it "
                            "does not cover. Exit 2 when a required value is missing or "
                            "another check FAILs. `emir check --process` runs the same "
                            "checks.")
    ap.add_argument("process", metavar="PROCESS", help="the process file to check")
    ap.add_argument("--dspf", default=None, metavar="DSPF",
                    help="a DSPF of the block: the layers it uses must be in the file, and "
                         "its wires wider than their width loss")
    ap.add_argument("--em-temp", type=float, default=None, metavar="C",
                    help="the analysis temperature to check the derating at (default: the "
                         "file's em_tref)")
    a = ap.parse_args(argv)
    if not os.path.isfile(a.process):
        raise EmirError("EMIR-E101", f"process file not found: {a.process}", path=a.process)
    rep = Report()
    rep.args = {k: v for k, v in (("process", a.process), ("dspf", a.dspf)) if v}
    proc = check_process(a.process, rep, em_temp=a.em_temp)
    facts = getattr(rep, "process_facts", None) or {}
    import yaml
    try:
        with open(a.process, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (OSError, yaml.YAMLError):
        data = None
    cov = None
    if a.dspf:
        from .check import _readable
        if _readable(a.dspf, "DSPF", rep):
            from .dspf import parse
            try:
                d = parse(a.dspf)
            except EmirError as e:
                rep.fail(e.code, "dspf", e.message, e.hint)
                d = None
            if d is not None and isinstance(data, dict):
                cov = coverage(data, d)
                if proc is not None:
                    check_layer_map(d, proc, rep)
                    # decaps its cap_models cannot size (a cox left null):
                    # a --dynamic run would not check their straps
                    from .decap import warn_unsized
                    warn_unsized(d, sorted(n for n in d.nets if d.net_resistors(n)),
                                 proc, rep)
                elif cov["missing"]:
                    rep.warn("EMIR-W307", "layers", f"{len(cov['missing'])} DSPF layer(s) have "
                             f"no process entry and are NOT checked "
                             f"({cov['segments_not_covered']} segments): "
                             + ", ".join(cov["missing"][:8]),
                             "add them to the process file, or map them with `dspf_layers:`")
    print(rep.table(show_ok=True))
    empty = facts.get("empty") or (empty_values(data) if isinstance(data, dict)
                                   else {"required": [], "optional": []})
    if empty["required"]:
        print(f"  still empty, required: {', '.join(x['path'] for x in empty['required'][:12])}")
    steps = []
    again = command_line("process", "check", a.process,
                         *(["--dspf", a.dspf] if a.dspf else []), out.fmt_args())
    sus = [x for x in facts.get("suspects") or [] if not x.get("confirmed")]
    if not rep.passed:
        steps.append({"why": "fill or correct each FAIL (the person types the values from the "
                             "PDK's documents, with their sources, and checks each suspect "
                             "unit), then check again",
                      "command": again})
    elif sus:
        steps.append({"why": f"{len(sus)} value(s) look like a unit slip (EMIR-W321: "
                             + ", ".join(x["path"] for x in sus[:4])
                             + "): the person checks each against the design manual (and "
                               "corrects it, or lists it under unit_confirmed), then check "
                               "again before planning the run",
                      "command": again, "needs_human": True})
    elif a.dspf:
        steps.append({"why": "the process file passes: plan the run",
                      "command": command_line("next", "--dspf", a.dspf, "--process",
                                              a.process, out.fmt_args())})
    pdk = data.get("pdk") if isinstance(data, dict) else None
    out.emit(document(
        "process_check", process=os.path.abspath(a.process),
        dspf=os.path.abspath(a.dspf) if a.dspf else None, pdk=pdk,
        provenance=(file_provenance(data) if isinstance(data, dict) else None),
        passed=rep.passed, n_fail=rep.n_fail, n_warn=rep.n_warn,
        empty=empty, unsourced=facts.get("unsourced", []),
        inferred=facts.get("inferred", []), suspects=facts.get("suspects", []),
        coverage=cov, findings=[f.as_dict(**rep.args) for f in rep.sorted()],
        next_steps=keep_format(steps, out.fmt_args())))
    return 0 if rep.passed else 2


#: the sub-commands of `emir process`
SUBCOMMANDS = {"new": cmd_new, "check": cmd_check}


def cmd_process(argv, out) -> int:
    """`emir process new|check ...` (a `--format` before the sub-command is
    passed on to it)."""
    argv, lead = list(argv), []
    while argv and (argv[0] == "--format" or argv[0].startswith("--format=")):
        n = 1 if "=" in argv[0] else 2
        lead, argv = lead + argv[:n], argv[n:]
    if argv and argv[0] in SUBCOMMANDS:
        return SUBCOMMANDS[argv[0]](argv[1:] + lead, out)
    argv = lead + argv
    from .output import parser
    ap = parser("emir process",
                description="Process data entered by hand, when no readable process file "
                            "exists for the PDK (the ICT is missing or encrypted). `emir process "
                            "new` writes the fill-in file for the layers and vias a DSPF uses; "
                            "`emir process check` says what it still lacks. `emir process "
                            "new --help` and `emir process check --help` for each.")
    ap.add_argument("subcommand", choices=tuple(SUBCOMMANDS),
                    help="new: write the fill-in file; check: what it still lacks")
    ap.parse_args(argv)
    return 0                                       # pragma: no cover - parse_args exits
