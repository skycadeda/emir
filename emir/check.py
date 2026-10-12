"""Input checker: find the problems in a run's inputs *before* the run.

Every check here corresponds to a failure that was seen for real -- a net that
does not exist, a layer the process file does not know (so its segments are
silently never checked), currents that do not balance, a simulation that did
not save the signals the analysis needs, a binary PSF. The analysis itself
would fail later, or worse, run and say
less than it appears to. This module says it up front, in one table, with the
fix beside each line.

Severities:

* ``FAIL`` -- the run cannot give a trustworthy answer; ``emir run`` stops.
* ``WARN`` -- the run will proceed but part of the answer is missing or
  approximate; read the line before believing the report.
* ``INFO`` -- a decision the tool made for you, stated so it is not a surprise.
* ``OK``   -- a check that passed and is worth seeing pass.

Every finding carries a registry code (`emir/errors.py`) of its level: an
``E`` code for FAIL, ``W`` for WARN, ``I`` for INFO and OK; `emir explain
<code>` says what it means. In JSON each finding also has the command that
fixes or diagnoses it, filled from the run's own arguments.

Use::

    emir check design.dspf --process pdk.yaml --net VDD --cc-sim sim.raw --period 10n
    emir check design.dspf --process pdk.yaml --format json   # emir.check/1
    emir check design.dspf --process pdk.yaml --json-out check.json

or from Python, ``check_inputs(...)`` returns a :class:`Report`.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field

from .errors import EmirError

LEVELS = ("FAIL", "WARN", "INFO", "OK")
_RANK = {l: i for i, l in enumerate(LEVELS)}

#: required keys and their meaning, per section of the process file
_LAYER_REQ = {"sheet_res": "ohm/square"}
_VIA_REQ = {"res": "ohm per cut",
            "below": "layer name under the via", "above": "layer name over the via"}
_LAYER_NUM = ("sheet_res", "min_width", "jmax_avg", "jmax_rms", "jmax_rms_k", "jmax_peak",
              "width_loss_um")
_LAYER_SIGNED = ("tc1", "tc2")
_VIA_NUM = ("res", "imax_avg", "imax_rms", "imax_peak", "vcwidth")


def _process_keys() -> dict:
    """The keys the process-file format has: top level, and inside a layer
    and a via -- from its JSON Schema, the one source."""
    from .schemas import process_schema
    sc = process_schema()["properties"]
    der = sc["derating"]["anyOf"][0]["properties"]
    return {"top": set(sc), "derating": set(der),
            "layers": set(sc["layers"]["additionalProperties"]["properties"]),
            "vias": set(sc["vias"]["additionalProperties"]["properties"]),
            "magic": set(sc["magic"]["anyOf"][0]["properties"])}


def _check_keys(data, rep) -> bool:
    """FAIL on a key the format does not have inside an entry (most likely a
    typo: `jmax_peek` silently dropped a peak limit), WARN on an unknown
    top-level key. Returns whether an entry failed."""
    from .dspf import did_you_mean
    keys = _process_keys()
    bad = False
    for section in ("layers", "vias"):
        sec = data.get(section) or {}
        if not isinstance(sec, dict):
            continue                    # its own check says so (E303, E304)
        for name, d in sec.items():
            if not isinstance(d, dict):
                continue
            for k in sorted(set(map(str, d)) - keys[section]):
                rep.fail("EMIR-E316", "process",
                         f"{section}/{name}: unknown key `{k}`"
                         f"{did_you_mean(k, keys[section])}: its value is not used",
                         f"correct the key under {section}/{name} (`emir schema process` "
                         f"lists them)")
                bad = True
    magic = data.get("magic")
    if isinstance(magic, dict):
        for k in sorted(set(map(str, magic)) - keys["magic"]):
            rep.fail("EMIR-E316", "process",
                     f"magic: unknown key `{k}`{did_you_mean(k, keys['magic'])}: its value "
                     f"is not used", "correct the key under magic (`emir schema process` "
                     "lists them)")
            bad = True
        from .magic import PROCESS_BINARY
        b = magic.get("binary")
        for v in ([b] if isinstance(b, str) else b if isinstance(b, list) else []):
            if not PROCESS_BINARY.match(str(v)):
                rep.fail("EMIR-E648", "process",
                         f"magic: binary: {v!r} is not a magic program's bare name: EMIR "
                         f"would run it", "name the magic with --magic, $EMIR_MAGIC or the "
                         "site file's magic:, and remove it from the process file")
                bad = True
    for k in sorted(set(map(str, data)) - keys["top"]):
        rep.warn("EMIR-W314", "process",
                 f"unknown top-level key `{k}`{did_you_mean(k, keys['top'])}: it is ignored",
                 "correct or remove it (`emir schema process` lists the keys)")
    return bad


@dataclass
class Finding:
    """One line of the input check. `code` is its registry code
    (`emir/errors.py`): an ``E`` code for FAIL, ``W`` for WARN, ``I`` for INFO
    and OK. `ctx` fills the code's command template (a path, a net)."""
    level: str
    code: str
    area: str
    message: str
    fix: str = ""
    ctx: dict = field(default_factory=dict)

    def command(self, **args) -> str:
        """The command that fixes or diagnoses this finding, filled from the
        run's arguments (`args`) and the finding's own context."""
        from .errors import lookup
        return lookup(self.code).command_for(**{**args, **self.ctx})

    def as_dict(self, **args) -> dict:
        from .errors import lookup
        return {"level": self.level, "code": self.code, "area": self.area,
                "message": self.message, "fix": self.fix or lookup(self.code).hint,
                "command": self.command(**args)}


#: the code letter each level must carry
_LETTER = {"FAIL": "E", "WARN": "W", "INFO": "I", "OK": "I"}


@dataclass
class Report:
    findings: list = field(default_factory=list)
    context: dict = field(default_factory=dict)
    #: the run's arguments, for the findings' commands (`dspf`, `net`,
    #: `process`, `args` -- see `errors.fill`); set by `from_args`
    args: dict = field(default_factory=dict)
    #: the simulation result `check_sim` read, keyed by (path, analysis): a
    #: run reuses it, so a result is read (and its notices said) once per run
    waves: object = None
    waves_for: tuple | None = None
    #: the nets' averaging windows `check_sim` took (`key`: the result,
    #: analysis, period and nets), which the run reuses; or the refusal
    #: (`window_error`) the run raises as it is
    windows: dict | None = None
    window_error: object = None
    #: the parsed DSPF, the process and the nets (`check_inputs`)
    parsed: dict = field(default_factory=dict)

    def add(self, level: str, code: str, area: str, message: str, fix: str = "",
            **ctx):
        from .errors import lookup
        assert level in LEVELS, level
        assert lookup(code).letter == _LETTER[level], (level, code)
        self.findings.append(Finding(level, code, area, message, fix, ctx))

    def fail(self, code, area, msg, fix="", **ctx):
        self.add("FAIL", code, area, msg, fix, **ctx)

    def warn(self, code, area, msg, fix="", **ctx):
        self.add("WARN", code, area, msg, fix, **ctx)

    def info(self, code, area, msg, fix="", **ctx):
        self.add("INFO", code, area, msg, fix, **ctx)

    def ok(self, code, area, msg, **ctx):
        self.add("OK", code, area, msg, **ctx)

    @property
    def n_fail(self) -> int:
        return sum(1 for f in self.findings if f.level == "FAIL")

    @property
    def n_warn(self) -> int:
        return sum(1 for f in self.findings if f.level == "WARN")

    @property
    def passed(self) -> bool:
        return self.n_fail == 0

    def sorted(self) -> list:
        return sorted(self.findings, key=lambda f: (_RANK[f.level], f.area))

    def codes(self, level: str = "FAIL") -> list:
        """The codes of the findings at `level`, in table order."""
        return [f.code for f in self.sorted() if f.level == level]

    def table(self, show_ok: bool = True) -> str:
        rows = [f for f in self.sorted() if show_ok or f.level != "OK"]
        out = []
        for f in rows:
            out.append(f"  {f.level:4s}  {f.area:9s} {f.message}  [{f.code}]")
            if f.fix:
                out.append(f"        {'':9s} -> {f.fix}")
        verdict = ("INPUTS OK" if self.passed else
                   f"INPUTS NOT OK: {self.n_fail} blocking problem(s)")
        if self.n_warn:
            verdict += f", {self.n_warn} warning(s)"
        out.append(f"  {verdict}")
        return "\n".join(out)

    def next_steps(self, run_command: str | None = None) -> list:
        """`[{why, command}]`: the fix of every FAIL (one per distinct
        command), or, when the inputs pass, `run_command`."""
        from .errors import lookup, step
        if self.passed:
            return [step("the inputs pass the check: run the analysis",
                         run_command)] if run_command else []
        out, seen = [], set()
        for f in self.sorted():
            if f.level != "FAIL":
                continue
            cmd = f.command(**self.args)
            if cmd and cmd not in seen:
                seen.add(cmd)
                out.append(step(f"{f.code} {lookup(f.code).title}: "
                                f"{f.fix or lookup(f.code).hint}", cmd))
        return out

    def as_dict(self) -> dict:
        return {"passed": self.passed, "n_fail": self.n_fail, "n_warn": self.n_warn,
                "findings": [f.as_dict(**self.args) for f in self.sorted()],
                "context": self.context}

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), indent=1, default=str)


# ------------------------------------------------------------------ pieces


def _readable(path, what, rep) -> bool:
    if not path:
        rep.fail("EMIR-E103", "files", f"no {what} given", f"pass the {what} path")
        return False
    if not os.path.exists(path):
        rep.fail("EMIR-E101", "files", f"{what} not found: {path}", "check the path", path=path)
        return False
    if os.path.isfile(path) and not os.access(path, os.R_OK):
        rep.fail("EMIR-E102", "files", f"{what} not readable: {path}", "check permissions", path=path)
        return False
    return True


def _lint_dspf_text(path, rep) -> dict:
    """Cheap textual scan for the things the parser would otherwise hide."""
    n_c_dev = n_m = n_cont = n_r = 0
    saw_design = saw_s = saw_i = saw_p = False
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.strip():
                continue
            c = line[0]
            if c == "+":
                n_cont += 1
            elif c in "Rr":
                n_r += 1
            elif c in "Cc":
                fs = line.split()
                if len(fs) >= 4:
                    try:
                        float(fs[3])
                    except ValueError:
                        n_c_dev += 1
            elif c in "Mm":
                n_m += 1
            elif line.startswith("*|DESIGN"):
                saw_design = True
            elif line.startswith("*|S"):
                saw_s = True
            elif line.startswith("*|I"):
                saw_i = True
            elif line.startswith("*|P"):
                saw_p = True
    return {"c_dev": n_c_dev, "mos": n_m, "cont": n_cont, "r": n_r,
            "design": saw_design, "S": saw_s, "I": saw_i, "P": saw_p}


def check_process(path, rep, em_temp=None):
    """Schema and sanity of the process YAML. Returns the Process or None."""
    import yaml
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception as e:                       # noqa: BLE001
        rep.fail("EMIR-E301", "process", f"cannot parse YAML: {e}", "fix the YAML syntax")
        return None
    if not isinstance(data, dict):
        rep.fail("EMIR-E302", "process", "process file is not a mapping",
                 "see the sample, emir/data/sample/process.yaml")
        return None
    layers = data.get("layers")
    if not isinstance(layers, dict) or not layers:
        rep.fail("EMIR-E303", "process", "no `layers:` section", "add at least one metal layer")
        return None
    vias = data.get("vias") or {}
    if not isinstance(vias, dict):
        rep.fail("EMIR-E304", "process", "`vias:` is not a mapping",
                 "see the sample, emir/data/sample/process.yaml")
        return None

    bad = _check_keys(data, rep)
    der = data.get("derating")
    if isinstance(der, dict):
        from .dspf import did_you_mean
        known = set(_process_keys()["derating"])
        for k in sorted(set(map(str, der)) - known):
            rep.fail("EMIR-E316", "process", f"derating: unknown key `{k}`"
                     f"{did_you_mean(k, known)}: its value is not used",
                     "the keys of `derating:` are model, ea_eV, n and below_tref")
            bad = True
    estimates = []
    for name, d in layers.items():
        if not isinstance(d, dict):
            rep.fail("EMIR-E305", "process", f"layer {name}: not a mapping"); bad = True; continue
        for k, why in _LAYER_REQ.items():
            if k not in d:
                rep.fail("EMIR-E306", "process", f"layer {name}: missing `{k}` ({why})",
                         f"add `{k}:` under layers/{name}")
                bad = True
        g = d.get("gds")
        if g is not None and not (isinstance(g, (list, tuple)) and len(g) == 2):
            rep.fail("EMIR-E307", "process", f"layer {name}: `gds` must be [layer, datatype]")
            bad = True
        for k in _LAYER_NUM:
            v = d.get(k)
            if v is not None and (not isinstance(v, (int, float)) or v < 0):
                rep.fail("EMIR-E308", "process", f"layer {name}: `{k}` must be a non-negative number, got {v!r}")
                bad = True
        for k in _LAYER_SIGNED:
            v = d.get(k)
            if v is not None and not isinstance(v, (int, float)):
                rep.fail("EMIR-E309", "process", f"layer {name}: `{k}` must be a number, got {v!r}")
                bad = True
        if d.get("jmax_rms") and d.get("jmax_rms_k"):
            rep.fail("EMIR-E310", "process", f"layer {name}: both `jmax_rms` (a fixed rms limit) and "
                     f"`jmax_rms_k` (sqrt(k * delta_T)) are given",
                     f"keep the one your PDK publishes under layers/{name}")
            bad = True
        rms, avg = d.get("jmax_rms"), d.get("jmax_avg")
        if (isinstance(rms, (int, float)) and isinstance(avg, (int, float))
                and 0 < rms < avg):
            rep.warn("EMIR-W301", "process", f"layer {name}: jmax_rms {rms:g} is below jmax_avg {avg:g} "
                     f"mA/um, so the avg check can never bind (I_rms >= |I_avg|)",
                     "check both values against the PDK")
        if not any(d.get(k) for k in ("jmax_avg", "jmax_rms", "jmax_rms_k", "jmax_peak")):
            rep.warn("EMIR-W302", "process", f"layer {name}: no jmax_* limit -- EM is NOT checked on it",
                     "add jmax_avg (mA/um) at least")
        if d.get("estimate"):
            estimates.append(name)
    for name, d in vias.items():
        if not isinstance(d, dict):
            rep.fail("EMIR-E305", "process", f"via {name}: not a mapping"); bad = True; continue
        for k, why in _VIA_REQ.items():
            if k not in d:
                rep.fail("EMIR-E306", "process", f"via {name}: missing `{k}` ({why})",
                         f"add `{k}:` under vias/{name}")
                bad = True
        g = d.get("gds")
        if g is not None and not (isinstance(g, (list, tuple)) and len(g) == 2):
            rep.fail("EMIR-E307", "process", f"via {name}: `gds` must be [layer, datatype]")
            bad = True
        for k in ("below", "above"):
            v = d.get(k)
            if v is not None and v not in layers:
                rep.warn("EMIR-W303", "process", f"via {name}: `{k}: {v}` is not a layer in this file "
                         f"(the via is still checked; only the stack description is off)",
                         "name a layer from the `layers:` section")
        for k in _VIA_NUM:
            v = d.get(k)
            if v is not None and (not isinstance(v, (int, float)) or v < 0):
                rep.fail("EMIR-E308", "process", f"via {name}: `{k}` must be a non-negative number, got {v!r}")
                bad = True
        if not any(d.get(k) for k in ("imax_avg", "imax_rms", "imax_peak")):
            rep.warn("EMIR-W304", "process", f"via {name}: no imax_* limit -- EM is NOT checked on it",
                     "add imax_avg (mA/cut) at least")
        if d.get("estimate") or d.get("rms_peak_estimate"):
            estimates.append(name)

    if estimates:
        rep.info("EMIR-I301", "process", f"{len(estimates)} entries carry limits flagged `estimate`: "
                 f"{', '.join(estimates[:6])}{' ...' if len(estimates) > 6 else ''}")
    wf = data.get("warn_fraction", 0.8)
    if not isinstance(wf, (int, float)) or not 0 < wf <= 1:
        rep.fail("EMIR-E311", "process", f"warn_fraction must be in (0, 1], got {wf!r}")
        bad = True
    if "em_tref" not in data:
        rep.fail("EMIR-E312", "process", "no `em_tref`: the temperature (C) the avg EM limits are "
                 "quoted at is required -- there is no default",
                 "add `em_tref: <C>` at the top level (an ICT gives it as em_tref)")
        bad = True
    elif data["em_tref"] is None:
        pass            # present but empty: EMIR-E320 (`process_file.lint`, below)
    elif not isinstance(data["em_tref"], (int, float)):
        rep.fail("EMIR-E313", "process", f"`em_tref` must be a temperature in C, got {data['em_tref']!r}")
        bad = True
    for k, v in (("em_tref", data.get("em_tref")), ("temp_reference", data.get("temp_reference")),
                 ("--em-temp", em_temp)):
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v <= -273.15:
            rep.fail("EMIR-E332", "process", f"{k} {v:g} C is at or below absolute zero "
                     f"(-273.15 C)", "give the temperature in C (a kelvin value minus 273.15)")
            bad = True
    jf = data.get("jmax_factor") or {}
    tref = data.get("em_tref") if isinstance(data.get("em_tref"), (int, float)) else None
    t_an = em_temp if em_temp is not None else tref
    from .process_file import _derating_given
    if _derating_given(data):
        # Black's equation (`derating`): `process_file.lint` checks its form
        # (and that no `jmax_factor` table is given beside it)
        der = data.get("derating") or {}
        if tref is not None and t_an is not None and tref > -273.15 and t_an > -273.15 \
                and all(isinstance(der.get(k), (int, float)) and not isinstance(der.get(k), bool)
                        and der[k] > 0 for k in ("ea_eV", "n")):
            from .engine import black_factor
            f = black_factor(der["ea_eV"], der["n"], t_an, tref)
            if f > 1.0 and der.get("below_tref", "clamp") in (None, "clamp"):
                rep.warn("EMIR-W326", "process",
                         f"the analysis temperature {t_an:g} C is below em_tref {tref:g} C: "
                         f"Black's equation would raise the avg limits x{f:.4g}; they are "
                         f"applied as quoted (x1), as the derating is defined above em_tref",
                         "analyse at em_tref or above, or, if the manual defines the "
                         "derating below it, set `derating: below_tref: extrapolate`")
            elif f > 1.0:
                rep.warn("EMIR-W326", "process",
                         f"the avg limits are raised x{f:.4g} by Black's equation below "
                         f"em_tref {tref:g} C (`below_tref: extrapolate`)",
                         "check that the design manual defines the derating there")
            else:
                rep.info("EMIR-I322", "process",
                         f"avg limits derated by Black's equation (Ea {der['ea_eV']:g} eV, "
                         f"n {der['n']:g}): x{f:.4g} at {t_an:g} C against em_tref {tref:g} C")
    elif jf:
        try:
            table = {float(k): float(v) for k, v in jf.items()}
            ks = sorted(table)
            if t_an is not None and not ks[0] <= t_an <= ks[-1]:
                hot = t_an > ks[-1]
                rep.warn("EMIR-W305", "process",
                         f"the analysis temperature {t_an:g} C is outside the jmax_factor "
                         f"table ({ks[0]:g}..{ks[-1]:g} C): the derating is clamped, not "
                         f"extrapolated" + (" -- above the table this is NOT conservative "
                                            "(the avg limits keep falling with temperature)"
                                            if hot else ""))
            if tref is not None and not ks[0] <= tref <= ks[-1]:
                rep.warn("EMIR-W305", "process",
                         f"em_tref {tref:g} C is outside the jmax_factor table "
                         f"({ks[0]:g}..{ks[-1]:g} C): check the table against the PDK")
            elif tref is not None:
                from .engine import _interp
                f_ref = _interp(table, float(tref))
                if abs(f_ref - 1.0) > 0.01:
                    rep.warn("EMIR-W316", "process",
                             f"jmax_factor is {f_ref:.3g} at em_tref {tref:g} C, not 1.0: the "
                             f"limits are quoted at em_tref, so the table should be 1.0 there",
                             "check jmax_factor and em_tref against the PDK")
        except (TypeError, ValueError):
            rep.fail("EMIR-E314", "process", "jmax_factor keys must be temperatures")
            bad = True
    elif t_an is not None and tref is not None and t_an > tref + 0.5:
        rep.warn("EMIR-W315", "process",
                 f"no jmax_factor table: the avg limits, quoted at {tref:g} C, are used "
                 f"unchanged at {t_an:g} C. EM limits fall with temperature, so the avg "
                 f"check is NOT conservative here",
                 "add the PDK's derating as jmax_factor, or analyse at em_tref")
    else:
        rep.info("EMIR-I302", "process", "no jmax_factor table: avg limits are not derated with temperature")
    # the hand-entry checks: empty required values, provenance and sources,
    # unit slips, limits out of order (`emir process check` reads `facts`)
    from .process_file import lint
    facts = lint(data, rep, em_temp=em_temp)
    rep.process_facts = facts
    bad = bad or facts["bad"]
    if bad:
        return None

    from .engine import Process
    try:
        proc = Process(data, temp=em_temp)
    except EmirError as e:
        rep.fail(e.code, "process", e.message, e.hint)
        return None
    except (TypeError, ValueError, KeyError) as e:
        rep.fail("EMIR-E315", "process", f"process file rejected: {e}")
        return None
    for name, (rms, avg, dt_eq) in sorted(proc.rms_below_avg.items()):
        rep.warn("EMIR-W301", "process",
                 f"layer {name}: the self-heating (rms) limit sqrt(jmax_rms_k x delta-T) = "
                 f"{rms:.3g} mA/um at delta-T {proc.delta_t:g} C is below the avg limit "
                 f"{avg:.3g} mA/um, so rms governs: a DC current fails rms before avg (the "
                 f"two meet at delta-T {dt_eq:.3g} C). Not raised: confirm the self-heating "
                 f"budget against the PDK; --delta-t sets it",
                 "check the PDK's self-heating rule; give --delta-t only from it")
    rep.ok("EMIR-I304", "process", f"{data.get('pdk') or os.path.basename(path)}: "
           f"{len(proc.layers)} layers, {len(proc.vias)} vias, analysis at {proc.temp:g} C"
           + (f" (avg derate x{proc.derate:.3g})" if proc.jmax_factor or proc.derating
              else ""))
    if "illustrative" in str(data.get("pdk", "")).lower() or "NOT A FOUNDRY" in open(path, errors="replace").read(400):
        from .plan import _is_sample
        what = ("this is the bundled ILLUSTRATIVE sample process file" if _is_sample(path)
                else f"{path} is marked ILLUSTRATIVE (its `pdk:` line or header)")
        rep.warn("EMIR-W306", "process", f"{what} -- its limits are not real",
                 "point --process at your PDK's file for any result you intend to act on")
    return proc


def check_layer_map(dspf, proc, rep):
    from .dspf import map_layers
    notes = []
    lmap = map_layers(dspf, proc, log=notes.append)
    for n in notes:
        rep.info("EMIR-I305", "layers", n.replace("[emir] ", ""))
    from .dspf import LVL_TOKEN
    toks = dspf.layer_tokens()
    unmapped = [t for t in toks if t not in lmap]
    if unmapped:
        um = set(unmapped)
        n_seg = sum(1 for r in dspf.res if r["layer"] in um)
        lvl = [t for t in unmapped if t.startswith(LVL_TOKEN)]
        rep.warn("EMIR-W307", "layers", f"{len(unmapped)} DSPF layer(s) have no process entry and are "
                 f"NOT checked ({n_seg} segments): {', '.join(unmapped[:8])}"
                 + (" ..." if len(unmapped) > 8 else ""),
                 "add them to the process file, or map them with `dspf_layers:`"
                 + (f" (segments with only a `$lvl` number: map the number, e.g. "
                    f"`dspf_layers: {{{lvl[0][len(LVL_TOKEN):]}: Metal1}}`)" if lvl else ""))
    _check_via_cuts(dspf, proc, lmap, rep)
    _check_wire_widths(dspf, lmap, rep)
    from .process_file import check_widths
    check_widths(dspf, proc, lmap, rep)
    nolayer = sum(1 for r in dspf.res if not r["layer"])
    if nolayer:
        rep.warn("EMIR-W206", "layers", f"{nolayer} resistor(s) carry no $layer token and cannot be checked",
                 "re-extract with layer annotation enabled")
    if toks and not unmapped:
        rep.ok("EMIR-I306", "layers", f"all {len(toks)} DSPF layers map onto the process file")
    return lmap


def _check_via_cuts(dspf, proc, lmap, rep):
    """Say how each checked via's cut count will be found (`dspf.via_cuts`).

    A via current is judged per cut, so the cut count scales its ratio
    directly. A count recovered from the cut area needs the process file's
    `vcwidth`; with neither a count nor a usable area the via is checked as
    ONE cut -- conservative, but worth fixing at the source.
    """
    from .dspf import via_cuts
    area_n, no_vcw, bare = 0, {}, []
    for r in dspf.res:
        hit = lmap.get(r["layer"])
        if not hit or hit[0] != "via":
            continue
        _, src = via_cuts(r, proc.vias[hit[1]])
        if src == "area":
            area_n += 1
        elif src == "assumed" and r.get("area"):
            no_vcw[hit[1]] = no_vcw.get(hit[1], 0) + 1
        elif src == "assumed":
            bare.append(r["name"])
    if no_vcw:
        rep.warn("EMIR-W308", "vias", f"{sum(no_vcw.values())} via(s) give a cut area ($A) but no cut "
                 f"count, and the process file has no `vcwidth` for "
                 f"{', '.join(sorted(no_vcw))}: each is checked as ONE cut (conservative)",
                 "add `vcwidth:` (the cut side, um) under those vias")
    if bare:
        rep.warn("EMIR-W207", "vias", f"{len(bare)} via(s) carry neither a cut count ($N/$VC) nor a cut "
                 f"area ($A): each is checked as ONE cut (conservative), e.g. "
                 f"{', '.join(bare[:4])}",
                 "extract with via annotation (cut count or area) enabled")
    if area_n:
        rep.info("EMIR-I307", "vias", f"{area_n} via cut count(s) derived from the cut area ($A) and the "
                 f"process `vcwidth`")


def _check_wire_widths(dspf, lmap, rep):
    """A resistor on a process-file LAYER (a wire) needs its width: EM on a
    wire is `J = I / w`. One without `$W`/`$SI_W` cannot be checked, so it is
    a FAIL -- such segments used to be classed as vias for want of a width
    and then skipped, and the net looked cleaner than it was."""
    per = {}
    for r in dspf.res:
        hit = lmap.get(r["layer"])
        if hit and hit[0] == "layer" and not r.get("w"):
            per.setdefault(hit[1], []).append(r["name"])
    if per:
        n = sum(len(v) for v in per.values())
        rep.fail("EMIR-E210", "layers", f"{n} wire resistor(s) carry no width ($W / $SI_W), so their "
                 f"current density cannot be evaluated and they would go unchecked: "
                 + "; ".join(f"{lay}: {', '.join(v[:4])}{' ...' if len(v) > 4 else ''}"
                             for lay, v in sorted(per.items())),
                 "extract with geometry annotation ($W $L) enabled")


def check_nets(dspf, nets, rep, ground=None, feed=None, has_sim=False):
    """Existence, connectivity and node count of the nets to analyse."""
    from .dspf import Network
    from .rcc import net_port, reference_node, spanning_order
    out = []
    for net in nets:
        if not dspf.has_net(net):
            from .dspf import did_you_mean
            hint = did_you_mean(net, dspf.nets)
            rep.fail("EMIR-E204", "nets", f"net {net!r} is not in the DSPF",
                     hint.strip(" ()") if hint else
                     "`emir nets <dspf>` lists them", net=net)
            continue
        res = dspf.net_resistors(net)
        if not res:
            rep.fail("EMIR-E205", "nets", f"net {net!r} has no resistors -- nothing to analyse",
                     "was the net extracted with R? check the extraction settings", net=net)
            continue
        try:
            gnd = reference_node(dspf, net, (ground or feed) if len(nets) == 1 else None)
        except EmirError as e:
            rep.fail(e.code, "nets", e.message, "name a node that is on this net", net=net)
            continue
        nw = Network(dspf, net)
        _, _, unreached = spanning_order(nw, gnd)
        if unreached:
            rep.fail("EMIR-E207", "nets", f"net {net!r}: {len(unreached)} of {len(nw.nodes)} nodes are not "
                     f"connected to the reference {gnd!r} (e.g. {', '.join(unreached[:3])})",
                     "the extracted net is in pieces -- check for opens, or the wrong --feed", net=net)
        port = net_port(dspf, net)
        taps = dspf.net_taps(net)
        if not port and not taps:
            rep.fail("EMIR-E208", "nets", f"net {net!r} has neither a port nor instance pins -- no current can enter it", net=net)
        elif not taps:
            rep.warn("EMIR-W205", "nets", f"net {net!r} has no instance pins: DC --currents "
                     f"can name only its nodes", net=net)
        nn = len(nw.nodes)
        out.append({"net": net, "nodes": nn, "res": len(res), "port": port,
                    "taps": len(taps), "ref": gnd, "mode": "rcc" if has_sim else "dc"})
        rep.ok("EMIR-I204", "nets", f"net {net!r}: {nn} nodes, {len(res)} R, {len(taps)} instance pins, "
               f"reference {gnd}" + ("" if port else " (no port: signal net)"), net=net)
    return out


def check_currents(dspf, nets, currents, rep):
    # the net the findings' commands name: only one that is in the DSPF (a
    # command naming a missing net would fail; check_nets reports that one)
    one = nets[0] if len(nets) == 1 and dspf.has_net(nets[0]) else None
    fix = ('e.g. --currents \'{"VDD": 0.004, "MP0:s": -0.004}\', or the path of a JSON file '
           'holding that object (`emir schema currents`)')
    try:
        if isinstance(currents, str):
            from .jsonarg import load
            inj, _path = load(currents, "--currents", "EMIR-E401")
        else:
            inj = dict(currents)
        inj = {str(k): float(v) for k, v in inj.items()}
    except EmirError as e:
        # not inline JSON and no such file (EMIR-E101), unreadable (E102), not JSON
        if e.code == "EMIR-E401":
            rep.fail(e.code, "currents", f"--currents is not a JSON object of node: amps "
                     f"({e.message})", fix, net=one)
        else:
            rep.fail(e.code, "currents", e.message, e.hint, net=one, path=e.ctx.get("path"))
        return None
    except Exception as e:                       # noqa: BLE001
        rep.fail("EMIR-E401", "currents", f"--currents is not a JSON object of node: amps ({e})",
                 fix, net=one)
        return None
    bad = sorted(k for k, v in inj.items() if not math.isfinite(v))
    if bad:
        # JSON accepts NaN and Infinity, and a NaN sum passes every balance test
        rep.fail("EMIR-E402", "currents", f"--currents holds NaN or Inf for: {', '.join(bad[:8])}",
                 "give every node a finite current in amps", net=one)
        return inj
    if len(nets) != 1:
        rep.fail("EMIR-E403", "currents", "--currents drives exactly one net", "give --net")
        return inj
    net = nets[0]
    nodes = {n for r in dspf.net_resistors(net) for n in (r["n1"], r["n2"])}
    # on a net that is not in the DSPF `check_nets` FAILs already (EMIR-E204);
    # naming its nodes here would only repeat it, with a command that cannot
    # work. The balance below does not need the net.
    missing = [k for k in inj if k not in nodes] if dspf.has_net(net) else []
    # a few names, then a count: a JSON file for the wrong net can name
    # thousands of nodes, and the line must stay readable
    shown = ", ".join(missing[:8]) + (f" (+{len(missing) - 8} more)" if len(missing) > 8 else "")
    if missing and nodes:
        cand = sorted(n for n in nodes if ":" not in n)[:4]
        rep.fail("EMIR-E404", "currents", f"{len(missing)} node(s) not on net {net!r}: {shown}",
                 f"nodes on this net look like {', '.join(cand)} ... "
                 f"(`emir pins <dspf> --net {net}` lists the instance pins)", net=net)
    elif missing:
        rep.fail("EMIR-E404", "currents", f"{len(missing)} node(s) not on net {net!r}: {shown}", net=net)
    tot = sum(inj.values())
    mx = max((abs(v) for v in inj.values()), default=0.0)
    if mx == 0:
        rep.fail("EMIR-E405", "currents", "all currents are zero", "give the real injected currents",
                 net=one)
    elif abs(tot) > 1e-6 * mx:
        rep.fail("EMIR-E406", "currents", f"currents do not balance: sum = {tot:.4g} A",
                 "the injection into a net must equal what leaves it (KCL)", net=one)
    else:
        rep.ok("EMIR-I401", "currents", f"{len(inj)} DC injections on {net!r}, balanced, |I|max {mx * 1e3:.4g} mA")
    return inj


def check_sim(dspf, nets, path, rep, analysis=None, period=None):
    """The simulation result exists, is readable, saved every branch current
    rcc reads, and gives every net its window as the run does
    (`rcc.net_windows`): a window the run would refuse (EMIR-E428, E429,
    E431) FAILs here with the run's own code."""
    from . import waves
    from .rcc import save_branches
    if not _readable(path, "simulation result", rep):
        return None
    try:
        w = waves.read(path, analysis=analysis)
    except EmirError as e:
        # binary PSF / raw, truncated, unreadable, an empty directory: the
        # reader's own code says which
        rep.fail(e.code, "sim", e.message, e.hint, path=path)
        return None
    rep.ok("EMIR-I402", "sim", w.summary())
    if not w.is_complex and 1 < len(w.x) < 20:
        rep.warn("EMIR-W402", "sim", f"only {len(w.x)} time points -- rms/peak will be coarse")
    complete = True
    for net in nets:
        names = [r["name"] for r in dspf.net_resistors(net)]
        found = w.match([f"{n}:1" for n in names])
        miss = len(names) - len(found)
        if w.is_complex:
            rep.fail("EMIR-E414", "sim", f"net {net!r}: rcc needs a time-domain result; this one is harmonic",
                     "use the transient or PSS td output", net=net)
        elif miss == len(names):
            rep.fail("EMIR-E415", "sim", f"net {net!r}: none of its {len(names)} branch currents "
                     f"were saved.{waves.naming_hint(w)}",
                     f"add to the netlist: {save_branches(dspf, net)[:120]} ...", net=net)
        elif miss:
            rep.fail("EMIR-E415", "sim", f"net {net!r}: {miss} of {len(names)} branch currents not saved",
                     "run `emir save-lines` and add every line to the netlist", net=net)
        else:
            rep.ok("EMIR-I404", "sim", f"net {net!r}: all {len(names)} branch currents present", net=net)
        if not _check_finite(w, found, net, rep) or miss or w.is_complex:
            complete = False
    if nets and not w.is_complex and len(w.x) > 1 and complete:
        _check_windows(dspf, nets, w, rep, path, analysis, period)
    return w


def _check_windows(dspf, nets, w, rep, path, analysis, period):
    """Every net's window, as the run takes it (with every branch current
    present and finite): the run reuses the windows (`Report.windows`), or
    raises the refusal as it is (`Report.window_error`: EMIR-E428, E429,
    E431)."""
    from .rcc import net_windows
    from .waveform import REFUSALS
    # the run's inputs as given (dspf, process, net, sim ...): the refusal's
    # next step is `emir next` with all of them (with the result and the
    # period alone it planned a new process file the person already had)
    ctx = {**{k: v for k, v in rep.args.items() if k != "args"}, "cc_sim": path,
           "period": period}
    try:
        wins = net_windows(dspf, nets, w, period=period, log=lambda *a, **k: None, ctx=ctx)
    except EmirError as e:
        if e.code in REFUSALS:
            rep.window_error = e
        rep.fail(e.code, "sim", e.message, e.hint, path=path)
        return
    rep.windows = {"key": (path, analysis, period, tuple(nets)), "wins": wins}


def netlist_period_error(netlist, ctx=None) -> EmirError:
    """EMIR-E428 for a --netlist run without --period: the simulation it runs
    is a transient, refused without one, so nothing is simulated (a licence
    spent on a run certain to be refused)."""
    ctx = {k: v for k, v in (ctx or {}).items() if v is not None}
    return EmirError("EMIR-E428", f"--netlist {netlist} runs a transient of the testbench "
                     f"and no --period was given: rcc averages over whole periods of the "
                     f"block's activity, and EMIR never guesses them, so the run would be "
                     f"refused after the simulation. Nothing was simulated. Give --period, "
                     f"the block's period (its clock, or the period of the testbench's PULSE "
                     f"or SIN sources)", details={"period_s": None, "run": dict(ctx)},
                     **{k: v for k, v in ctx.items()
                        if k in ("dspf", "process", "netlist", "net", "sim")})


def _netlist_period(rep, netlist):
    """A --netlist run without --period FAILs with EMIR-E428 (the run raises
    it as it is: `Report.window_error`)."""
    e = netlist_period_error(netlist, {k: v for k, v in rep.args.items() if k != "args"})
    rep.window_error = e
    rep.fail(e.code, "sim", e.message, e.hint, path=netlist)


def _check_finite(w, found: dict, net, rep):
    """EMIR-E426 when currents the net needs hold NaN or Inf: every ratio
    and IR drop computed from them would be "not a number"."""
    import numpy as np
    bad = [req for req, name in found.items()
           if not np.all(np.isfinite(np.asarray(w.data[name])))]
    if bad:
        rep.fail("EMIR-E426", "sim", f"net {net!r}: {len(bad)} current(s) it needs hold NaN or "
                 f"Inf in the result: {', '.join(bad[:6])}{' ...' if len(bad) > 6 else ''}",
                 "re-run the simulation to a finite result (its log names the failure); "
                 "never replace the values by zero", net=net)
    return not bad


def _check_simulator(rep, sim, spectre=None, run=False):
    """The simulator --netlist needs: found, when it would run (a licensed
    one runs only with --run; without it the run only plans and needs none).
    emir run drives Spectre only (EMIR-E604)."""
    from . import site
    if sim != "spectre":
        rep.fail("EMIR-E604", "sim", f"--netlist with --sim {sim}: emir run drives Spectre "
                 f"only",
                 f"simulate with `emir simulate <testbench> --sim {sim} --dspf <dspf>` and "
                 f"pass the result with --cc-sim")
        return
    if sim == "spectre" and not run:
        rep.info("EMIR-I661", "sim", f"{sim} is licensed: without --run the run writes the "
                 f"files and prints the command, and runs nothing")
        return
    p, src = site.tool(sim, spectre)
    if p:
        rep.ok("EMIR-I601", "sim", f"spectre: {p} (from {src})")
    else:
        rep.fail("EMIR-E601", "sim", "--netlist needs spectre and none was found",
                 "set $SPECTRE_BIN, or `spectre:` in the site file, or pass --cc-sim with a "
                 "finished result")


def check_outputs(rep, html=None, viewer=None, lib=None, cell=None, design=None):
    for what, p in (("html", html), ("viewer", viewer)):
        if not p:
            continue
        parent = os.path.abspath(p if os.path.isdir(p) else os.path.dirname(os.path.abspath(p)) or ".")
        while not os.path.exists(parent):
            parent = os.path.dirname(parent)
        if not os.access(parent, os.W_OK):
            rep.fail("EMIR-E701", "output", f"--{what} directory is not writable: {p}", "choose another directory", path=p)
    if viewer:
        from .layoutview import check_viewer_names
        for msg in check_viewer_names(lib, cell, design):
            rep.fail("EMIR-E702", "output", f"--viewer: {msg}; it would be written into SKILL",
                     "use a plain library/cell name (pass --cell to override the "
                     "DSPF's design name)")
        if not lib:
            rep.warn("EMIR-W701", "output", "--viewer without --lib: the Explorer will not know which OA library to open",
                     "pass --lib, or set `lib:` in the site file")
        if not cell and not design:
            rep.warn("EMIR-W702", "output", "--viewer: no --cell and the DSPF has no *|DESIGN; pass --cell")


# ------------------------------------------------------------------- driver


def check_inputs(dspf, process=None, nets=None, currents=None, cc_sim=None,
                 analysis=None, mode=None, em_temp=None, netlist=None, feed=None,
                 ground=None, period=None, html=None, viewer=None, lib=None, cell=None,
                 spectre=None, want_analysis=True, sim=None, run=False) -> Report:
    """Run every applicable check. `nets` is a list, `["all"]` or None."""
    rep = Report()
    ctx = rep.context
    # what the findings' commands are filled from (`Finding.command`)
    one = nets[0] if isinstance(nets, (list, tuple)) and len(nets) == 1 \
        and nets[0] != "all" else None
    rep.args = {k: v for k, v in (("dspf", dspf), ("process", process), ("net", one),
                                  ("cc_sim", cc_sim), ("netlist", netlist),
                                  ("period", period), ("sim", sim))
                if v is not None}

    # ---- files and DSPF
    if not _readable(dspf, "DSPF", rep):
        return rep
    lint = _lint_dspf_text(dspf, rep)
    from .dspf import parse
    try:
        d = parse(dspf)
    except EmirError as e:
        rep.fail(e.code, "dspf", e.message, e.hint)
        return rep
    except (UnicodeError, ValueError, IndexError) as e:
        rep.fail("EMIR-E201", "dspf", f"cannot parse: {e}")
        return rep
    ctx["design"] = d.design
    ctx["nets"] = sorted(d.nets)
    if not d.res:
        rep.fail("EMIR-E202", "dspf", "no resistors at all -- this is not an R or RC extraction",
                 "extract with resistance (R or RC), not C-only")
        return rep
    if not lint["design"]:
        rep.warn("EMIR-W201", "dspf", "no *|DESIGN line: the viewer needs --cell")
    if not lint["S"] and not lint["I"]:
        rep.warn("EMIR-W202", "dspf", "no *|S / *|I node coordinates: results cannot be placed on the layout",
                 "extract with node location annotation ($X $Y) enabled")
    if not lint["I"]:
        rep.warn("EMIR-W203", "dspf", "no *|I instance-pin records: the DSPF names no device "
                 "pin, so DC --currents can name only its nodes, and rcc's KCL check counts "
                 "every device node as one nothing drives",
                 "extract with instance-pin annotation (*|I) enabled")
    geo = sum(1 for r in d.res if r.get("w"))
    vias = sum(1 for r in d.res if r.get("via"))
    if geo == 0 and len(d.res) > vias:
        rep.fail("EMIR-E203", "dspf", "no resistor carries a $W width -- J = I/w cannot be evaluated "
                 "on any wire",
                 "extract with geometry annotation ($W $L) enabled")
    # Without a process file this is all that can be said: whether a resistor
    # with neither a width nor a cut annotation is a via or a wire depends on
    # its layer. With one, check_layer_map says it per layer
    # (`_check_via_cuts`, `_check_wire_widths`).
    bare = sum(1 for r in d.res if r.get("via") is None)
    if bare and not process:
        rep.warn("EMIR-W204", "dspf", f"{bare} resistor(s) carry neither a width ($W) nor a cut "
                 "count or area ($N/$A): a via among them is checked as ONE cut "
                 "(conservative), a wire among them cannot be checked",
                 "extract with geometry and via annotation enabled; give --process "
                 "to have them classified by layer")
    if d.temperature is not None:
        ctx["dspf_temp"] = d.temperature
    rep.ok("EMIR-I203", "dspf", d.summary())

    # ---- nets requested
    if nets in (None, [], ["all"], "all"):
        net_list = [n for n in sorted(d.nets) if d.net_resistors(n)]
    else:
        net_list = list(nets)
    ctx["net_list"] = net_list

    # ---- process
    proc = None
    if process:
        if _readable(process, "process file", rep):
            proc = check_process(process, rep, em_temp=em_temp)
    elif want_analysis:
        rep.fail("EMIR-E103", "process", "no process file", "pass --process <pdk emir.yaml>")
    rep.parsed = {"dspf": d, "proc": proc, "nets": net_list}
    if proc is not None:
        check_layer_map(d, proc, rep)
        if d.temperature is not None and abs(d.temperature - proc.temp) > 0.5:
            how = ("rcc's IR uses the resistances the simulation ran, the DSPF's at "
                   f"{d.temperature:g} C (EMIR-I407)" if (cc_sim or netlist) and not currents
                   else f"each wire's R is scaled from {d.temperature:g} C by its layer's "
                        f"TCR; vias are not scaled")
            rep.info("EMIR-I308", "process", f"DSPF extracted at {d.temperature:g} C, EM "
                     f"analysed at {proc.temp:g} C ({how})")

    # ---- nets
    if net_list:
        check_nets(d, net_list, rep, ground=ground, feed=feed, has_sim=bool(cc_sim or netlist))
    else:
        rep.fail("EMIR-E209", "nets", "no net with resistors to analyse")

    # ---- drive
    drivers = [x for x in (currents, cc_sim, netlist) if x]
    if want_analysis and not drivers:
        rep.fail("EMIR-E407", "drive", "nothing drives the net", "give --cc-sim <result>, "
                 "--currents, or --netlist")
    if currents and (cc_sim or netlist):
        rep.fail("EMIR-E514", "options", "--currents and a simulation ("
                 + ("--cc-sim" if cc_sim else "--netlist") + ") were both given: a run takes "
                 "its currents from one", "give --cc-sim (or --netlist) or --currents, not both")
    if currents:
        check_currents(d, net_list, currents, rep)
    if cc_sim:
        rep.waves = check_sim(d, net_list, cc_sim, rep, analysis=analysis, period=period)
    if rep.waves is not None:
        rep.waves_for = (cc_sim, analysis)
    if netlist and _readable(netlist, "netlist", rep):
        _check_simulator(rep, sim or "spectre", spectre, run)
    if netlist and not (cc_sim or currents) and period is None:
        _netlist_period(rep, netlist)
    if mode == "rcc" and not cc_sim and not netlist:
        rep.fail("EMIR-E506", "options", "--mode rcc needs a simulation result (--cc-sim or --netlist)")

    # ---- outputs
    check_outputs(rep, html=html, viewer=viewer, lib=lib, cell=cell, design=d.design)
    return rep


def from_args(a, want_analysis=True, in_run=False) -> Report:
    """Build a report from a parsed `emir run` / `emir check` namespace.

    `a.argv_text`, when the CLI sets it, is the command's own arguments
    (quoted), so a finding's command can repeat them (`{args}`). `in_run`:
    the run's own check (kept for its callers; the checks are the same).
    """
    nets = None if a.net in (None, "all") else [a.net]
    rep = check_inputs(
        a.dspf, process=a.process, nets=nets, currents=a.currents,
        cc_sim=a.cc_sim, analysis=a.analysis, mode=a.mode, em_temp=a.em_temp,
        netlist=a.netlist, feed=a.feed, ground=a.ground, period=a.period, html=a.html,
        viewer=a.viewer, lib=a.lib, cell=a.cell,
        spectre=getattr(a, "spectre", None), want_analysis=want_analysis,
        sim=getattr(a, "sim", None), run=getattr(a, "run", False))
    if getattr(a, "argv_text", None):
        rep.args["args"] = a.argv_text
    return rep
