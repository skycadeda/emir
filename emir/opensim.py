"""The open-simulator path: Xyce and ngspice results for the DSPF flow.

EMIR reads a simulation's currents by the DSPF's names: `R:1` for a branch
(rcc mode), `inst:pin` for an instance pin (cc-grid). Spectre saves currents
under exactly those names; Xyce and ngspice do not, and they print different
things in different syntax. This module closes the gap in three commands:

    emir save-lines <dspf> --mode rcc|cc-grid --sim xyce|ngspice [--names-out F]
        the `.print` (Xyce) or `.save` (ngspice) lines for the currents a
        mode needs, and the `<result>.names` sidecar that maps the
        simulator's names back to the DSPF's;
    emir probe <netlist> -o probed.sp --net N [--dspf D] [--names-out F]
        a 0 V source in series with each device terminal on the nets, for
        terminals no simulator prints directly (a device inside a
        subcircuit -- how open PDKs wrap their transistors);
    emir simulate <testbench> --sim xyce|ngspice --dspf D --mode rcc|cc-grid --out R.raw
        all of it: the deck with the lines in it, the run, an ASCII result
        and its sidecar, checked; then `emir run --cc-sim R.raw --mode ...` reads it.

What a simulator can print directly (measured on this build: Xyce 7.10
development, ngspice 45; `emir verify-sim` checks each form on the machine
that uses it):

| current | Xyce | ngspice | sign |
|---|---|---|---|
| resistor, first node to second | `I(XI0:RM1)` | `@r.xi0.rm1[i]` | + |
| pin 2 of a two-terminal element | `I(XI0:RL2)` | `@r.xi0.rl2[i]` | - |
| MOS drain / gate / source / bulk, into the pin | `ID(XI0:MN0)` (IG, IS, IB) | through a 0 V probe | + |
| BJT collector / base / emitter | `IC(...)` (IB, IE) | through a 0 V probe | + |
| a voltage source, + node to - node | `I(XI0:VEMIR0)` | `i(v.xi0.vemir0)` | + |
| a subcircuit's pin (`XMN0:d`) | none | none | probe it |

ngspice's `@m...[id]` (`[is]`, `[ig]`, `[ib]`; a BJT's and a JFET's
likewise) is the device model's internal current: for a PMOS it has the
opposite sign of the current into the pin (ngspice 45, a diode-connected
PMOS fed by 1 mA: `@m[id]` +1 mA where 1 mA flows out of its drain;
tests/test_verify_findings.py measures it), and
nothing in the netlist emir reads says reliably which polarity a model has.
So emir's ngspice route reads MOSFET, BJT and JFET terminals through 0 V
probes (`--probe`), whose sign is the voltage source's.

Spectre's `inst:pin` is positive into the instance and `R:1` positive from
the resistor's first node to its second, so every sign here is +1 but a
two-terminal element's pin 2; a 0 V probe `V node node__emir 0` in series
with a pin carries the pin's current the same way.

HSPICE (`--sim hspice`, licensed) prints with `.probe tran` into an ASCII
`.tr0` (`.option probe post=2 post_version=2001`; `emir.hspice` reads it).
What its manuals document (HSPICE Reference Manual: Commands and Control
Options B-2008.09 and E-2010.12; Star-Hspice Manual 1998.2 ch. 4 and 15):

| current | HSPICE | sign into the DSPF pin |
|---|---|---|
| resistor, first node to second | `i(xi0.rm1)` (= `i1(...)`) | + |
| two-terminal element, pin 1 / pin 2 | `i1(...)` / `i1(...)` | + / - (the current into pin 1; pin 2's is its negative, so `i2`'s convention is never needed) |
| MOS drain, gate, bulk (BJT collector, base, substrate) | `i1`, `i2`, `i4` | + (drawn into the device) |
| MOS source (BJT emitter, JFET source) | `i3` | drawn OUT of the device: -1, but a run was not seen to confirm it, so EMIR saves every terminal of the device and sets the sign from KCL when it reads the result (`waves.kcl_signs`) |
| a voltage source (an `emir probe` 0 V probe), + node to - node | `i(xi0.vemir0)` | + (confirmed on a real netlist and output pair) |
| a subcircuit's pin | `isub(x1.pin)` exists, but how it is named in a .tr0 is unverified | probe it |

Names are lower case with `.` between hierarchy levels. Spectre and HSPICE
need a licence: `emir simulate --sim spectre|hspice` writes the deck and
prints the exact command, and runs it only with `--run`.

A DSPF pin is matched to its device's terminal by name (`d`, `drain`, `1`
...: `lead`); a pin name that is none of the device's terminals is refused
(EMIR-E425), never mapped to another terminal.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import shlex
import subprocess
import sys
import time

from . import subproc
from .errors import EmirError, notice
from .opensim_netlist import (DEFAULT_TYPES, PROBE_NODE, PROBE_SOURCE, Tree,
                              Writer, terminal_names)
from .output import command_line, document, parser, strip_options

SIMS = ("spectre", "hspice", "xyce", "ngspice")
OPEN_SIMS = ("xyce", "ngspice")
#: the simulators that need a licence: planned, run only with --run
LICENSED_SIMS = ("spectre", "hspice")
#: what HSPICE is told: ASCII output (post=2), double precision (2001), only
#: the probed signals written (probe)
HSPICE_OPTIONS = ".option probe post=2 post_version=2001"

#: signals per `.print` / `.save` line before a `+` continuation
PER_LINE = 8

#: terminal letters of the multi-terminal primitives, in position order
_POSITION = {"M": "DGSB", "Q": "CBES", "J": "DGS"}
#: long and alternative pin names, upper case, onto each primitive's
#: terminal letter: a MOSFET's substrate is its bulk, a BJT's its substrate
#: terminal (S); a name not listed is no terminal of the device
_ALIASES = {
    "M": {"DRAIN": "D", "GATE": "G", "SOURCE": "S", "BULK": "B", "BODY": "B", "SUB": "B",
          "SUBSTRATE": "B"},
    "Q": {"COLLECTOR": "C", "BASE": "B", "EMITTER": "E", "SUB": "S", "SUBSTRATE": "S"},
    "J": {"DRAIN": "D", "GATE": "G", "SOURCE": "S"},
}
#: two-terminal pins: the sign of the element current (first node to second)
_TWO = {"1": 1, "P": 1, "PLUS": 1, "POS": 1, "2": -1, "N": -1, "MINUS": -1, "NEG": -1}
#: a diode's pins by anode and cathode too
_DIODE = {"A": 1, "ANODE": 1, "K": -1, "C": -1, "CATHODE": -1}
#: the primitives whose pins `lead` knows
PRIMITIVES = ("M", "Q", "J", "R", "C", "D")


def _stderr(line):
    print(line, file=sys.stderr)


# ------------------------------------------------------------------ names


def split_path(prefix: str | None) -> list:
    """`XI0.` / `XI0:` / `XTOP.XI0.` -> `["XI0"]` / `["XTOP", "XI0"]`."""
    if not prefix:
        return []
    return [p for p in re.split(r"[.:]", prefix) if p]


def xyce_name(path: list, name: str) -> str:
    """Xyce's hierarchical name of an element (`XI0:RM1`)."""
    return ":".join([*path, name])


def ngspice_name(path: list, name: str) -> str:
    """ngspice's flattened name of an element (`r.xi0.rm1`): inside a
    subcircuit its letter, then the instance path, then its own name."""
    if not path:
        return name.lower()
    return ".".join([name[0], *path, name]).lower()


def hspice_name(path: list, name: str) -> str:
    """HSPICE's hierarchical name of an element (`xi0.rm1`): lower case,
    `.` between the levels."""
    return ".".join([*path, name]).lower()


def probe_keys(path: list, source: str) -> list:
    """The names a probe source's current has in a result (upper case, as
    the sidecar keys are matched): Xyce's, ngspice's two spellings and
    HSPICE's."""
    ng = ngspice_name(path, source)
    return [f"I({xyce_name(path, source)})".upper(), f"I({ng})".upper(),
            f"{ng}#branch".upper(), f"I({hspice_name(path, source)})".upper()]


def probe_key(sim: str, path: list, source: str) -> str:
    """The one name `sim` gives a probe source's current."""
    return probe_keys(path, source)[{"xyce": 0, "ngspice": 1, "hspice": 3}[sim]]


#: HSPICE's terminal index of each lead letter (I1 .. I4)
_HSPICE_INDEX = {"M": "DGSB", "Q": "CBES", "J": "DGS"}
#: the lead whose direction HSPICE's manuals draw out of the device
_HSPICE_OUT = {"M": "S", "Q": "E", "J": "S"}


def lead(kind: str, pin: str) -> tuple | None:
    """`(lead letter or "", sign)` of a primitive's pin, or None when `kind`
    is not a primitive emir knows (a subcircuit: probe it) or `pin` is none
    of its terminals (`PRIMITIVES`: the caller refuses it, EMIR-E425)."""
    p = pin.upper()
    if kind in _POSITION:
        p = _ALIASES[kind].get(p, p)
        if p.isdigit() and 1 <= int(p) <= len(_POSITION[kind]):
            p = _POSITION[kind][int(p) - 1]
        if len(p) == 1 and p in _POSITION[kind]:
            return p, 1
        return None
    if kind in ("R", "C", "D"):
        s = _TWO.get(p) or (_DIODE.get(p) if kind == "D" else None)
        return ("", s) if s else None
    return None


def unknown_pin_error(pins: list, kinds: dict, **ctx) -> EmirError:
    """EMIR-E425 for DSPF pins that name no terminal of their device."""
    names = {"M": "MOSFET (d g s b)", "Q": "BJT (c b e s)", "J": "JFET (d g s)",
             "R": "resistor (1 2)", "C": "capacitor (1 2)", "D": "diode (a k, 1 2)"}
    shown = ", ".join(f"{p} ({names[kinds[p.rpartition(':')[0].upper()]]})"
                      for p in pins[:6])
    return EmirError("EMIR-E425", f"{len(pins)} DSPF pin(s) name no terminal of their device: "
                     f"{shown}{' ...' if len(pins) > 6 else ''}; emir does not guess which "
                     f"terminal is meant", details={"pins": pins}, **ctx)


def _signal(sim: str, kind: str, path: list, inst: str, letter: str) -> tuple:
    """`(what to print or save, its name in the result)` for a direct
    current: a primitive's lead (`letter`) or a two-terminal element."""
    if sim == "hspice":
        name = hspice_name(path, inst)
        if kind in _HSPICE_INDEX and letter:
            s = f"i{_HSPICE_INDEX[kind].index(letter) + 1}({name})"
        else:
            s = f"i({name})" if not letter else f"i{letter}({name})"
        return s, s.upper()
    if sim == "xyce":
        s = f"I{letter}({xyce_name(path, inst)})"
        return s, s.upper()
    ng = ngspice_name(path, inst)
    attr = {"M": "i" + letter, "Q": "i" + letter, "J": "i" + letter,
            "D": "id", "R": "i", "C": "i"}[kind]
    s = f"@{ng}[{attr.lower()}]"
    return s, f"i({s})".upper()


def _kind_of(letter: str) -> str:
    """The kind of current (emir/verify_sim.py `KINDS`) a pin of an element
    with this SPICE letter is, read directly."""
    from .verify_sim import LETTER_KIND
    return LETTER_KIND.get(str(letter)[:1].upper(), "other")


def _probe_signal(sim: str, path: list, source: str) -> str:
    if sim == "xyce":
        return f"I({xyce_name(path, source)})"
    if sim == "hspice":
        return f"i({hspice_name(path, source)})"
    return f"i({ngspice_name(path, source)})"


# ------------------------------------------------------------------ save sets


class SaveSet:
    """What one simulation must save for one mode, in one simulator's syntax,
    and how its names map back to the DSPF's."""

    def __init__(self, sim, mode, nets, path):
        self.sim, self.mode, self.nets, self.path = sim, mode, nets, path
        self.signals: list = []           # what to print / save
        self.names: dict = {}             # result name (upper) -> [dspf name, sign]
        self.dspf_names: list = []        # the DSPF names covered
        self.direct = 0
        self.probed = 0
        self.unprintable: list = []       # inst:pin with no way to print it
        self.elements: list = []          # element names the lines reference
                                          # [(path, name)] (checked by simulate)
        self.kcl: dict = {}               # HSPICE: RESULT NAME -> {terms, device}
        self.version: str | None = None   # the simulator's version, when run
        self.sim_args: str | None = None  # emir simulate's --sim-args (normalised)
        self.polarity: list = []          # ngspice MOS/BJT/JFET pins: probe them
        self.kinds: dict = {}             # dspf name -> how it is read (verify_sim.KINDS)

    def add(self, signal, result_name, dspf_name, sign, element=None, kind=None):
        if signal not in self.signals:
            self.signals.append(signal)
        self.names[result_name.upper()] = [dspf_name, int(sign)]
        if kind:
            self.kinds[dspf_name] = kind
        if dspf_name not in self.dspf_names:
            self.dspf_names.append(dspf_name)
        if element:
            self.elements.append(element)

    def save_only(self, signal):
        """A signal saved for a check (a device's other terminal currents,
        for KCL), not mapped to a DSPF name."""
        if signal not in self.signals:
            self.signals.append(signal)

    def lines(self, raw: str = "emir.raw", deck: str = "<testbench.cir>") -> list:
        """The lines to put in the deck (`raw`: the result file Xyce writes)."""
        sig = self.signals
        chunks = [sig[i:i + PER_LINE] for i in range(0, len(sig), PER_LINE)] or [[]]
        if self.sim == "xyce":
            head = f".print tran format=raw file={raw}"
            out = [f"* emir: {len(sig)} currents for {self.mode}; run: Xyce -a {deck}",
                   head + (" " + " ".join(chunks[0]) if chunks[0] else "")]
        elif self.sim == "hspice":
            stem = raw[:-len(".tr0")] if raw.lower().endswith(".tr0") else raw
            deck = "<testbench.sp>" if deck == "<testbench.cir>" else deck
            out = [f"* emir: {len(sig)} currents for {self.mode}; run: hspice -i {deck} -o "
                   f"{stem}  (writes {stem}.tr0)",
                   HSPICE_OPTIONS,
                   ".probe tran " + " ".join(chunks[0])]
        else:
            out = [f"* emir: {len(sig)} currents for {self.mode}; run: ngspice -b -r "
                   f"{raw} {deck}",
                   f"* (interactive ngspice: in .control, run, then write {raw})",
                   ".options filetype=ascii",
                   ".save " + " ".join(chunks[0])]
        out += ["+ " + " ".join(c) for c in chunks[1:]]
        return out

    def sidecar(self) -> dict:
        out = {"map": dict(self.names), "simulator": self.sim}
        if self.version:
            out["simulator_version"] = self.version
        if self.sim_args is not None:
            out["sim_args"] = self.sim_args
        if self.kcl:
            out["kcl"] = {k: dict(v) for k, v in self.kcl.items()}
        if self.kinds:
            # how each current is read: what `emir verify-sim` records cover
            out["kinds"] = dict(self.kinds)
        return out


def target_nets(d, net, mode) -> list:
    """The nets a save covers: `net`, or every net with resistors (rcc) /
    with instance pins (cc)."""
    if net and net != "all":
        return [net]
    if mode == "rcc":
        return [n for n in sorted(d.nets) if d.net_resistors(n)]
    return [n for n in sorted(d.nets) if d.net_taps(n)]


def save_set(d, nets, mode, sim, path=(), probes=None, inst_kinds=None) -> SaveSet:
    """The `SaveSet` for `nets` of DSPF `d`.

    `probes` is `{INST:PIN (upper): probe}` (`emir probe`'s list, keyed);
    a pin with a probe is saved through it. `inst_kinds` is `{INST (upper):
    letter}` from the netlist when one is known (an instance's letter in
    the netlist decides how its pin can be printed); otherwise the DSPF
    instance name's first letter is taken as the element letter.
    """
    path = list(path)
    ss = SaveSet(sim, mode, list(nets), path)
    probes = probes or {}
    if mode == "rcc":
        seen = set()
        for n in nets:
            for r in d.net_resistors(n):
                if r["name"] in seen:
                    continue
                seen.add(r["name"])
                s, key = _signal(sim, "R", path, r["name"], "")
                ss.add(s, key, f"{r['name']}:1", 1, (tuple(path), r["name"]), kind="branch")
                ss.direct += 1
        if not ss.signals:
            raise EmirError("EMIR-E205", f"no resistors on {', '.join(nets) or 'any net'}",
                            net=nets[0] if len(nets) == 1 else None)
        return ss
    pins = []
    for n in nets:
        pins += [p for p in sorted(d.pin_groups(n)) if p not in pins]
    if not pins:
        raise EmirError("EMIR-E211", f"no instance pins on {', '.join(nets) or 'any net'} "
                        f"in the DSPF -- nothing to save",
                        net=nets[0] if len(nets) == 1 else None)
    unknown, known_kinds = [], {}
    for p in pins:
        inst, _, pin = p.rpartition(":")
        pr = probes.get(p.upper())
        if pr is not None:
            ss.add(_probe_signal(sim, pr["path"], pr["source"]),
                   probe_key(sim, pr["path"], pr["source"]),
                   p, pr.get("sign", 1), (tuple(pr["path"]), pr["source"]), kind="probe")
            ss.probed += 1
            continue
        from_netlist = (inst_kinds or {}).get(inst.upper())
        kind = from_netlist or inst[:1].upper()
        how = lead(kind, pin)
        if how is None:
            if from_netlist in PRIMITIVES:
                # the netlist says what the device is, and the pin is none of
                # its terminals: refused, never read from another terminal
                unknown.append(p)
                known_kinds[inst.upper()] = from_netlist
            else:
                ss.unprintable.append(p)
            continue
        letter, sign = how
        if sim == "ngspice" and kind in _POSITION:
            # ngspice's @m[id] carries the model's polarity (a PMOS reads
            # the opposite sign): its MOS, BJT and JFET pins need a probe
            ss.unprintable.append(p)
            ss.polarity.append(p)
            continue
        if sim == "hspice":
            _hspice_pin(ss, kind, path, inst, p, letter, sign)
            continue
        s, key = _signal(sim, kind, path, inst, letter)
        if key.upper() in ss.names and ss.names[key.upper()][0] != p:
            # both pins of a two-terminal device: one current, I(R1), and a
            # sidecar maps one name to one pin -- the second needs a probe
            ss.unprintable.append(p)
            continue
        ss.add(s, key, p, sign, (tuple(path), inst), kind=_kind_of(kind))
        ss.direct += 1
    if unknown:
        raise unknown_pin_error(unknown, known_kinds, dspf=getattr(d, "path", None),
                                net=nets[0] if len(nets) == 1 else None)
    return ss


def _hspice_pin(ss: SaveSet, kind: str, path: list, inst: str, pin: str, letter: str,
                sign: int):
    """One DSPF pin printed directly by HSPICE. A two-terminal element's
    pins are both read from `i1` (the current into pin 1, drawn from node 1
    to node 2): pin 2's current is its negative (-1), so the convention of
    `i2` is never relied on; with both pins on the net the second needs a
    probe (one signal maps to one pin). A MOSFET's, BJT's or JFET's are `iN`
    with N the terminal's position, and every terminal of the device is
    saved so that the reader can set the sign of the one the manuals draw
    out of the device from KCL."""
    name = hspice_name(path, inst)
    if kind not in _HSPICE_INDEX:
        s = f"i1({name})"
        if s.upper() in ss.names and ss.names[s.upper()][0] != pin:
            ss.unprintable.append(pin)
            return
        ss.add(s, s.upper(), pin, sign, (tuple(path), inst), kind=_kind_of(kind))
        ss.direct += 1
        return
    order = _HSPICE_INDEX[kind]
    # a BJT's substrate (i4) only when that is the pin: a three-node BJT has
    # none, and its substrate current is not part of the KCL check then
    n = len(order) if kind != "Q" or letter == "S" else 3
    leads = [f"i{k + 1}({name})" for k in range(n)]
    out_lead = leads[order.index(_HSPICE_OUT[kind])]
    s = leads[order.index(letter)]
    documented = -1 if s == out_lead else 1
    ss.add(s, s.upper(), pin, documented * sign, (tuple(path), inst), kind=_kind_of(kind))
    ss.direct += 1
    for x in leads:
        ss.save_only(x)
    ss.kcl[out_lead.upper()] = {"device": name, "terms": [x.upper() for x in leads
                                                          if x != out_lead]}


def refuse_unprintable(ss: SaveSet, how: str, **ctx):
    """EMIR-E460 for pins no simulator prints directly."""
    u = ss.unprintable
    raise EmirError(
        "EMIR-E460",
        f"{len(u)} instance pin(s) on {', '.join(ss.nets)} have no current {ss.sim} can "
        f"print directly ({', '.join(u[:6])}{' ...' if len(u) > 6 else ''}): a "
        f"subcircuit instance (X...), or a pin name that is not one of the device's "
        f"terminals{polarity_note(ss)}. {how}", details={"pins": u}, **ctx)


def polarity_note(ss: SaveSet) -> str:
    """The clause E460 adds for ngspice's MOS, BJT and JFET pins."""
    if not ss.polarity:
        return ""
    return (f", or a MOSFET, BJT or JFET terminal ({', '.join(ss.polarity[:4])}"
            f"{' ...' if len(ss.polarity) > 4 else ''}): ngspice's @m[id] carries the "
            f"model's polarity (a PMOS reads the opposite sign), so emir reads those "
            f"through 0 V probes")


def infer_names(d, nets, w) -> tuple | None:
    """`(sim, mode, prefix, hits, total)` when a result's current names are
    the ones `save_set` gives for some simulator, mode and instance path --
    a result simulated with emir's lines but no sidecar -- else None. The
    candidate paths come from the result's own hierarchical names."""
    have = {k.upper() for k in w.data}
    cands = {("xyce", ()), ("ngspice", ()), ("hspice", ())}
    for k in have:
        m = re.match(r"^I\d?\(([^@:()\[\]]+)\)$", k)
        if m and "." in m.group(1):
            cands.add(("hspice", tuple(m.group(1).split(".")[:-1])))
        m = re.match(r"^I[A-Z]?\((.+)\)$", k)
        if m and ":" in m.group(1) and not m.group(1).startswith("@"):
            cands.add(("xyce", tuple(m.group(1).split(":")[:-1])))
        m = re.match(r"^I\(@[A-Z]\.(.+)\[[A-Z]+\]\)$", k)
        if m:
            cands.add(("ngspice", tuple(m.group(1).split(".")[:-1])))
    best = None
    for sim, path in sorted(cands):
        for mode in ("rcc", "cc"):
            mnets = [n for n in nets if (d.net_resistors(n) if mode == "rcc"
                                         else d.net_taps(n))]
            if not mnets:
                continue
            try:
                ss = save_set(d, mnets, mode, sim, list(path))
            except EmirError:
                continue
            hits = len(set(ss.names) & have)
            if hits and (best is None or hits > best[3]):
                best = (sim, mode, ".".join(path) + "." if path else "", hits,
                        len(ss.names) + len(ss.unprintable))
    return best


# ------------------------------------------------------------------ sidecars


def write_sidecar(path: str, sidecar: dict) -> str:
    from .output import write_text
    return write_text(path, json.dumps(sidecar, indent=1, sort_keys=False) + "\n")


def load_probes(path: str) -> dict:
    """The `probes` of an `emir probe` sidecar, keyed `INST:PIN` (upper).
    EMIR-E424 when the file is not a names sidecar, EMIR-E461 when it is
    one without the `probes` list `emir probe` writes."""
    from .waves import load_names
    load_names(path)                       # the map itself must be valid (E424)
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    pl = doc.get("probes")
    if not isinstance(pl, list) or not pl:
        raise EmirError("EMIR-E461", f"{path} has no `probes` list: it is a names sidecar "
                        f"but not one `emir probe --names-out` wrote", path=path)
    out = {}
    for k, p in enumerate(pl):
        sign = p.get("sign", 1) if isinstance(p, dict) else None
        ok = (isinstance(p, dict) and isinstance(p.get("source"), str)
              and isinstance(p.get("pin"), str) and ":" in p.get("pin", "")
              and isinstance(p.get("path"), list)
              and all(isinstance(x, str) for x in p["path"])
              and sign in (1, -1) and not isinstance(sign, bool))
        if not ok:
            raise EmirError("EMIR-E424", f"{path}: probes[{k}] is not "
                            f"{{source, pin, path, sign}}; `emir schema names` prints the "
                            f"format", path=path)
        out[p["pin"].upper()] = {"source": p["source"], "path": list(p["path"]),
                                 "sign": p.get("sign", 1), "pin": p["pin"]}
    return out


# ------------------------------------------------------------------ save-lines


def save_lines_command(a, d, out) -> int:
    """`emir save-lines --sim xyce|ngspice` (the CLI's `_cmd_save_lines`
    hands over here for the open simulators)."""
    from .modes import save_kind
    probes = load_probes(a.probes) if a.probes else None
    path = split_path(a.prefix)
    kind = save_kind(a.mode)
    nets = target_nets(d, a.net, kind)
    ss = save_set(d, nets, kind, a.sim, path, probes=probes)
    if ss.unprintable:
        refuse_unprintable(ss, "Probe them: `emir probe <netlist> -o <probed> --net N "
                               "--dspf <dspf> --names-out <probes.names>`, then pass "
                               "--probes <probes.names> here (or let `emir simulate "
                               "--probe` do both)", dspf=a.dspf)
    raw = a.raw or (a.names_out[:-len(".names")] if a.names_out and
                    a.names_out.endswith(".names") else
                    "emir.tr0" if a.sim == "hspice" else "emir.raw")
    lines = ss.lines(raw)
    print("\n".join(lines))
    if a.out:
        from .output import write_text
        write_text(a.out, "\n".join(lines) + "\n")
        _stderr(f"[emir] {len(ss.signals)} saved signals -> {a.out}")
    sidecars = []
    if a.names_out:
        sidecars.append(os.path.abspath(write_sidecar(a.names_out, ss.sidecar())))
        _stderr(f"[emir] names sidecar: {sidecars[0]}")
        if os.path.abspath(a.names_out) != os.path.abspath(raw) + ".names":
            notice("EMIR-W462", f"{a.names_out} is not `{raw}.names`: emir reads the "
                   f"sidecar only beside the result, as <result>.names", _stderr)
    else:
        notice("EMIR-W460", f"no --names-out: the {a.sim} result will name its currents "
               f"{ss.signals[0]} ..., which match nothing in the DSPF until a "
               f"<result>.names sidecar maps them (the `names` field of the JSON "
               f"document, or re-run with --names-out <result>.names)", _stderr)
    out.emit(document("save_lines", dspf=os.path.abspath(a.dspf), net=a.net,
                      mode=a.mode, prefix=a.prefix, simulator=a.sim, lines=lines,
                      count=len(ss.signals), sidecars=sidecars, raw=raw,
                      names=ss.sidecar()["map"], nets=nets, direct=ss.direct,
                      probed=ss.probed, out=os.path.abspath(a.out) if a.out else None))
    return 0


# ------------------------------------------------------------------ probe


def scope_of(tree: Tree, d=None, subckt=None) -> tuple:
    """`(scope, why)`: the subcircuit whose devices are probed (None: the
    top level) -- `--subckt`, else the DSPF's design when the tree defines a
    subcircuit of that name, else the top level."""
    if subckt:
        if subckt.upper() not in tree.subckts:
            raise EmirError("EMIR-E261", f"no .subckt {subckt!r} in {tree.top.path} or "
                            f"the files it includes (have: "
                            f"{', '.join(s.name for s in tree.subckts.values()) or 'none'})",
                            path=tree.top.path)
        return subckt.upper(), f"--subckt {subckt}"
    design = getattr(d, "design", None) if d is not None else None
    if design and design.upper() in tree.subckts:
        return design.upper(), f"the DSPF's design {design!r} is a .subckt of the netlist"
    if d is not None:
        # the subcircuit that holds most of the DSPF's elements (its resistors,
        # the instances of its pins), if the top level holds fewer
        names = {r["name"].upper() for r in d.res}
        names |= {v["inst"].upper() for v in d.inst_pins.values()}
        best, hits = None, len(names & {e.name.upper() for e in tree.top_elements})
        for k, sc in tree.subckts.items():
            n = len(names & {e.name.upper() for e in sc.elements})
            if n > hits:
                best, hits = k, n
        if best is not None:
            return best, (f".subckt {tree.subckts[best].name} holds {hits} of the DSPF's "
                          f"elements")
    return None, "the top level of the netlist"


def scope_path(tree: Tree, scope, prefix=None, command="probe") -> tuple:
    """`(path, why)`: the instance path the simulator names `scope`'s
    elements under. `--prefix` wins; else found in the tree (exactly one
    instance). A subcircuit never instantiated (the block netlist alone) is
    named as if it were the top level, which `--prefix` corrects."""
    if prefix is not None:
        return split_path(prefix), f"--prefix {prefix}"
    if scope is None:
        return [], "no instance path"
    paths = tree.instance_paths(scope)
    if len(paths) == 1:
        return paths[0], f"the only instance of {tree.subckts[scope].name}"
    if not paths:
        notice("EMIR-W263", f"{tree.subckts[scope].name} is not instantiated in "
               f"{os.path.basename(tree.top.path)}: the names written assume it is the "
               f"top level of the simulation; give --prefix (e.g. XI0.) for the instance "
               f"path the testbench uses", _stderr)
        return [], "not instantiated here (assumed top level)"
    shown = ", ".join(".".join(p) for p in paths[:6])
    raise EmirError("EMIR-E261", f"{tree.subckts[scope].name} has {len(paths)} instances "
                    f"({shown}{' ...' if len(paths) > 6 else ''}): give --prefix with the "
                    f"one the DSPF was extracted from (e.g. --prefix {'.'.join(paths[0])}.)",
                    command=command)


class ProbeResult:
    def __init__(self):
        self.probes: list = []
        self.edits: dict = {}
        self.unmatched: list = []          # probed pins the DSPF does not list
        self.guessed: list = []            # instances whose pin names are positions
        self.uncovered: list = []          # DSPF pins no probe covers


def insert_probes(tree: Tree, scope, nets: list, path: list, d=None, types=DEFAULT_TYPES,
                  pins=None) -> ProbeResult:
    """Put a 0 V source in series with every terminal, on `nets`, of every
    element of `scope` whose letter is in `types`."""
    res = ProbeResult()
    want = {n.upper() for n in nets}
    # the DSPF's pins on these nets: INST -> {PIN: "inst:pin"}; and its
    # instance-pin nodes (`MN0:d`), when the netlist is the DSPF itself
    dpins: dict = {}
    pin_nodes: dict = {}
    if d is not None:
        for n in nets:
            for key, taps in d.pin_groups(n).items():
                inst, _, pin = key.rpartition(":")
                dpins.setdefault(inst.upper(), {})[pin.upper()] = key
                for t in taps:
                    pin_nodes[t["node"].upper()] = n
    els = tree.elements(scope)
    used = {e.name.upper() for e in els}
    k = 0
    for el in els:
        if el.kind not in types.upper():
            continue
        hits = [(i, nd) for i, nd in enumerate(el.nodes)
                if nd.upper() in want or nd.upper() in pin_nodes]
        if not hits:
            continue
        names, guessed = terminal_names(tree, el, pins)
        new_words = list(el.words)
        lines = []
        for i, nd in hits:
            while f"{PROBE_SOURCE}{k}" in used:
                k += 1
            src = f"{PROBE_SOURCE}{k}"
            used.add(src)
            k += 1
            new_node = f"{nd}{PROBE_NODE}{k - 1}"
            pin = names[i]
            dp = dpins.get(el.name.upper(), {})
            if pin.upper() in dp:
                full, matched = dp[pin.upper()], True
            elif len(dp) == 1 and len(hits) == 1:
                full, matched = next(iter(dp.values())), True
            else:
                full, matched = f"{el.name}:{pin}", d is None
                if d is not None:
                    res.unmatched.append(full)
                if guessed and el.ref and el.ref not in res.guessed:
                    # a positional name that nothing settled is in the sidecar
                    res.guessed.append(el.ref)
            net = pin_nodes.get(nd.upper()) or next((n for n in nets if n.upper() == nd.upper()),
                                                    nd)
            lines.append(f"{src} {nd} {new_node} 0")
            new_words[1 + i] = new_node
            res.probes.append({"source": src, "path": list(path), "pin": full, "sign": 1,
                               "net": net, "node": nd, "element": el.name,
                               "terminal": names[i], "matched": matched,
                               "file": el.file, "line": tree.files[el.file].chunks[el.chunk]
                               .lineno})
        lines.append(" ".join(new_words))
        res.edits[(el.file, el.chunk)] = lines
    if d is not None:
        covered = {p["pin"].upper() for p in res.probes}
        for n in nets:
            for key in sorted(d.pin_groups(n)):
                if key.upper() not in covered and key not in res.uncovered:
                    res.uncovered.append(key)
    return res


def probe_sidecar(res: ProbeResult, netlist: str, out: str) -> dict:
    m = {}
    for p in res.probes:
        for key in probe_keys(p["path"], p["source"]):
            m[key] = [p["pin"], 1]
    return {"map": m, "generator": "emir probe", "netlist": os.path.abspath(netlist),
            "kinds": {p["pin"]: "probe" for p in res.probes},
            "probed_netlist": os.path.abspath(out),
            "probes": [{k: p[k] for k in ("source", "path", "pin", "sign", "net", "element",
                                          "terminal")} for p in res.probes]}


def _pins_option(values) -> dict:
    """`--pins SUBCKT=d,g,s,b` (repeatable) -> `{SUBCKT: [ports]}`."""
    out = {}
    for v in values or ():
        name, eq, ports = v.partition("=")
        if not eq or not name or not ports:
            raise EmirError("EMIR-E513", f"--pins {v!r}: write SUBCKT=port1,port2,...",
                            command="probe")
        out[name.upper()] = [p for p in ports.split(",") if p]
    return out


class Probed:
    """What `do_probe` did: the probes, the files written, where."""

    def __init__(self, res, copies, tree, scope, path, why):
        self.res, self.copies, self.tree = res, copies, tree
        self.scope, self.path, self.why = scope, path, why

    @property
    def scope_name(self) -> str | None:
        return self.tree.subckts[self.scope].name if self.scope else None


def probe_tree(tree: Tree, nets, d=None, subckt=None, prefix=None, types=DEFAULT_TYPES,
               pins=None, command="probe") -> tuple:
    """`(ProbeResult, scope, path, why)` for a tree in memory (EMIR-E262 when
    nothing is on the nets)."""
    scope, why_scope = scope_of(tree, d, subckt)
    path, why_path = scope_path(tree, scope, prefix, command=command)
    res = insert_probes(tree, scope, nets, path, d=d, types=types, pins=pins)
    if not res.probes:
        where = f"subcircuit {tree.subckts[scope].name}" if scope else "the top level"
        raise EmirError("EMIR-E262", f"no terminal of a {'/'.join(types)} element in {where} "
                        f"of {tree.top.path} is on {', '.join(nets)}: nothing to probe "
                        f"({why_scope}). Check the net names (SPICE compares them without "
                        f"case), --subckt and --types", path=tree.top.path)
    return res, scope, path, f"{why_scope}; {why_path}"


def do_probe(netlist, out_path, nets, d=None, subckt=None, prefix=None, types=DEFAULT_TYPES,
             pins=None) -> Probed:
    """Probe `netlist` into `out_path` (and copies of its included files)."""
    tree = Tree(netlist)
    res, scope, path, why = probe_tree(tree, nets, d=d, subckt=subckt, prefix=prefix,
                                       types=types, pins=pins)
    copies = Writer(tree, out_path, "probed", edits=res.edits, strip=True,
                    command="probe").write()
    return Probed(res, copies, tree, scope, path, why)


def _probe_notices(res: ProbeResult, nets, uncovered: bool = True):
    """The probe step's warnings. `emir simulate` passes `uncovered=False`:
    there a pin without a probe is printed directly or refused (EMIR-E460),
    never left missing."""
    if res.guessed:
        notice("EMIR-W262", f"the pins of {', '.join(res.guessed[:6])} are named by position "
               f"(1, 2, ...): their .subckt is not in the netlist; give --pins "
               f"{res.guessed[0]}=d,g,s,b (its port names, in order)", _stderr)
    if res.unmatched:
        notice("EMIR-W261", f"{len(res.unmatched)} probed pin(s) are not instance pins of the "
               f"DSPF on {', '.join(nets)}: {', '.join(res.unmatched[:6])}"
               f"{' ...' if len(res.unmatched) > 6 else ''}; a run ignores them (are the "
               f"netlist's instance names the DSPF's?)", _stderr)
    if res.uncovered and uncovered:
        notice("EMIR-W260", f"{len(res.uncovered)} instance pin(s) of the DSPF on "
               f"{', '.join(nets)} got no probe: {', '.join(res.uncovered[:6])}"
               f"{' ...' if len(res.uncovered) > 6 else ''}; their current will be missing "
               f"(taken as ZERO, EMIR-W403)", _stderr)


def found_sim() -> str | None:
    """The open simulator `emir info` finds (Xyce first), or None."""
    from . import site
    for s in OPEN_SIMS:
        if site.tool(s)[0]:
            return s
    return None


def cmd_probe(argv, out) -> int:
    """`emir probe`."""
    ap = parser("emir probe", description=(
        "Rewrite a SPICE netlist (a testbench, a block, a DSPF) with a 0 V source in series "
        "with each device terminal on the chosen nets, so Xyce and ngspice can save the "
        "terminal currents a cc-grid run needs (no simulator prints a subcircuit pin's "
        "current). Included files that get probes are written as copies beside the "
        "output, without DSPF $ annotations (Xyce rejects them). The names sidecar maps "
        "each probe's current to the DSPF's inst:pin."))
    ap.add_argument("netlist", help="the SPICE netlist (its .include files are followed)")
    ap.add_argument("-o", "--out", required=True, metavar="FILE",
                    help="the probed netlist to write (never the input itself)")
    ap.add_argument("--net", action="append", default=None, metavar="NET",
                    help="a net whose device terminals get probes (repeatable; default "
                         "with --dspf: every DSPF net with instance pins)")
    ap.add_argument("--dspf", default=None,
                    help="the DSPF the currents are for: names the pins as its *|I records "
                         "do, and reports the pins left without a probe")
    ap.add_argument("--names-out", default=None, metavar="FILE",
                    help="write the names sidecar (emir.names/1 plus the `probes` list) "
                         "for `emir save-lines --probes` / `emir simulate --probes`")
    ap.add_argument("--subckt", default=None,
                    help="the subcircuit whose devices are probed (default: the DSPF's "
                         "design, if the netlist defines it; else the top level)")
    ap.add_argument("--prefix", default=None,
                    help="the instance path of that subcircuit in the simulation, e.g. "
                         "XI0. (default: found in the netlist)")
    ap.add_argument("--types", default=DEFAULT_TYPES,
                    help=f"element letters to probe (default {DEFAULT_TYPES}: devices and "
                         f"subcircuit instances, not parasitic R/C/L or sources)")
    ap.add_argument("--pins", action="append", default=None, metavar="SUBCKT=P1,P2,...",
                    help="the port names of a subcircuit the netlist does not define (a "
                         "PDK device), e.g. --pins sky130_fd_pr__nfet_01v8=d,g,s,b")
    a = ap.parse_args(argv)
    from .errors import collecting
    with collecting() as notes:
        return _probe(a, out, notes)


def _probe(a, out, notes) -> int:
    types = _types_option(a.types, "probe")
    d, nets = _dspf_and_nets(a.dspf, a.net, "probe")
    pr = do_probe(a.netlist, a.out, nets, d=d, subckt=a.subckt, prefix=a.prefix, types=types,
                  pins=_pins_option(a.pins))
    res = pr.res
    _probe_notices(res, nets)
    print(f"[emir] {len(res.probes)} probe(s) on {', '.join(nets)} ({pr.why}); wrote "
          f"{len(pr.copies)} file(s):")
    for src, dst in pr.copies.items():
        print(f"  {dst}  (from {src})")
    side = None
    if a.names_out:
        side = os.path.abspath(write_sidecar(a.names_out, probe_sidecar(res, a.netlist,
                                                                        a.out)))
        print(f"  {side}  (names sidecar)")
    steps = []
    sim = found_sim()
    has_tran = bool(Tree(a.out).analyses)
    if not has_tran:
        # a block (a bare .subckt), not a testbench: `emir simulate` would
        # refuse it (EMIR-E462), so it is not suggested
        notice("EMIR-W463", f"{a.out} (and its includes) has no .tran: a block, not a "
               f"testbench, so `emir simulate` cannot run it as it is (EMIR-E462). Probe "
               f"the testbench that includes the block instead (`emir probe <testbench> "
               f"...`: the included files are probed too), or include {a.out} in a "
               f"testbench with a .tran", _stderr)
    if side and d is not None and sim and has_tran:
        steps.append({"why": f"simulate the probed netlist with {sim} and read the probes' "
                             f"currents (writes the result and its names sidecar)",
                      "command": command_line(
                          "simulate", os.path.abspath(a.out), "--sim", sim, "--dspf",
                          os.path.abspath(a.dspf), *_net_args(a.net), "--mode", "cc-grid",
                          "--probes", side, "--out",
                          os.path.join(os.path.dirname(os.path.abspath(a.out)), "cc.raw"),
                          out.fmt_args())})
    out.emit(document(
        "probe", netlist=os.path.abspath(a.netlist), out=os.path.abspath(a.out),
        files=[{"from": s, "to": t} for s, t in pr.copies.items()], nets=nets,
        scope=pr.scope_name, path=pr.path, why=pr.why, count=len(res.probes),
        probes=[{k: p[k] for k in ("source", "pin", "net", "element", "terminal", "matched",
                                   "file", "line")} for p in res.probes],
        unmatched=res.unmatched, uncovered=res.uncovered, guessed=res.guessed,
        names_out=side, has_tran=has_tran, notices=list(notes), next_steps=steps))
    return 0


def _types_option(types, command) -> str:
    if not re.fullmatch(r"[A-Za-z]+", types or ""):
        raise EmirError("EMIR-E513", f"--types {types!r}: element letters, e.g. "
                        f"{DEFAULT_TYPES}", command=command)
    return types.upper()


def _net_args(nets) -> list:
    if not nets:
        return []
    if isinstance(nets, str):
        nets = [nets]
    return [x for n in nets if n and n != "all" for x in ("--net", n)]


def _dspf_and_nets(dspf, nets, command) -> tuple:
    """`(Dspf or None, nets)`: --net as given (checked against the DSPF), or
    every DSPF net with instance pins."""
    d = None
    if dspf:
        from .dspf import parse
        d = parse(dspf)
    if isinstance(nets, str):
        nets = None if nets == "all" else [nets]
    if not nets:
        if d is None:
            raise EmirError("EMIR-E507", "give --net (or --dspf, to probe every net with "
                            "instance pins)", command=command)
        nets = target_nets(d, None, "cc")
        if not nets:
            raise EmirError("EMIR-E211", f"no net of {dspf} has instance pins", dspf=dspf)
        return d, nets
    if d is not None:
        from .dspf import did_you_mean
        for n in nets:
            if not d.has_net(n):
                raise EmirError("EMIR-E204", f"net {n!r} is not in {dspf}"
                                f"{did_you_mean(n, d.nets)}", dspf=dspf)
    return d, list(nets)


# ------------------------------------------------------------------ simulate


def sim_exe(sim: str) -> str:
    """The simulator's executable (`emir info` resolves it); EMIR-E660
    (an open simulator) or EMIR-E662 (a licensed one), exit 3, when it is
    not found."""
    from . import site
    p, src = site.tool(sim)
    if not p:
        env = {"xyce": "XYCE_BIN", "ngspice": "NGSPICE_BIN", "spectre": "SPECTRE_BIN",
               "hspice": "HSPICE_BIN"}[sim]
        code = "EMIR-E662" if sim in LICENSED_SIMS else "EMIR-E660"
        raise EmirError(code, f"{sim} not found ({src}): put it on $PATH, set ${env} "
                        f"or `{sim}:` in the site file (see `emir info`)", sim=sim)
    return p


#: the flag each simulator prints its version with (documented: Spectre
#: Reference 19.1 `-V`; HSPICE Reference E-2010.12 `-v`; `Xyce -v` and
#: `ngspice -v` as these builds answer them)
VERSION_FLAG = {"spectre": "-V", "hspice": "-v", "xyce": "-v", "ngspice": "-v"}


def version_command(sim: str, exe: str) -> list:
    return [exe, VERSION_FLAG[sim]]


def sim_version(sim: str, exe: str, timeout: float = 60.0) -> str | None:
    """The first line of what the simulator prints for its version flag that
    holds a digit, or None. It runs the simulator: for a licensed one, only
    after the person's go-ahead (`--run`)."""
    try:
        p = subprocess.run(version_command(sim, exe), capture_output=True, text=True,
                           timeout=timeout, stdin=subprocess.DEVNULL, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    for ln in ((p.stdout or "") + "\n" + (p.stderr or "")).splitlines():
        t = ln.strip(" *\t")
        if t and re.search(r"\d", t) and re.search(r"[A-Za-z]", t):
            return t[:200]
    return None


def _check_elements(tree: Tree, scope, path, ss: SaveSet, added=()):
    """EMIR-E263 when the lines name elements the testbench does not have
    (Xyce stops on them; ngspice saves nothing for them). `added`: the
    probe sources this run inserts into `scope`."""
    here = tree.element_names(scope) | {x.upper() for x in added}
    every = None
    missing = []
    for epath, name in ss.elements:
        if list(epath) == list(path):
            ok = name.upper() in here
        else:
            if every is None:
                every = {e.name.upper() for e in tree.top_elements}
                every |= {e.name.upper() for sc in tree.subckts.values() for e in sc.elements}
            ok = name.upper() in every
        if not ok and name not in missing:
            missing.append(name)
    if missing:
        where = f"subcircuit {tree.subckts[scope].name}" if scope else "the top level"
        raise EmirError("EMIR-E263", f"{len(missing)} element(s) the {ss.mode} currents need are "
                        f"not in {where} of {tree.top.path}: {', '.join(missing[:8])}"
                        f"{' ...' if len(missing) > 8 else ''}. Is this the testbench of this "
                        f"DSPF (the {'RC netlist' if ss.mode == 'rcc' else 'CC netlist'})?",
                        path=tree.top.path)


def _error_lines(text: str) -> str:
    keep = [ln for ln in text.splitlines()
            if re.search(r"error|fatal|abort|unknown|not found|could not|can't", ln, re.I)]
    return "\n".join(keep[:20])


#: lines of a deck or a `.spiceinit` that make ngspice run commands: a
#: `.control` block (its `shell` runs any program), a bare `shell` line
_CODE_LINE = re.compile(r"^\s*(\.control\b|shell\b)", re.I)
_INCLUDE_LINE = re.compile(r'^\s*\.(?:include|inc|lib)\s+["\']?([^"\'\s]+)', re.I | re.M)


def deck_runs_code(testbench: str, limit: int = 64) -> list:
    """The files among the testbench, the files its `.include`/`.lib` lines
    name (followed a few levels, relative to the including file) and the
    `.spiceinit` beside it that hold a `.control` block or a `shell` line:
    ngspice runs those as commands. [] when none (or nothing readable)."""
    base = os.path.dirname(os.path.abspath(testbench))
    todo = [os.path.abspath(testbench), os.path.join(base, ".spiceinit")]
    seen, hits = set(), []
    while todo and len(seen) < limit:
        path = todo.pop(0)
        if path in seen or not os.path.isfile(path):
            continue
        seen.add(path)
        try:
            if os.path.getsize(path) > 50_000_000:
                continue
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        for ln in text.splitlines():
            if _CODE_LINE.match(ln):
                hits.append(path)
                break
        for m in _INCLUDE_LINE.finditer(text):
            name = os.path.expanduser(m.group(1))
            todo.append(os.path.normpath(os.path.join(os.path.dirname(path), name)))
    return hits


def spiceinit(testbench: str, deck: str) -> str | None:
    """ngspice reads `.spiceinit` from the directory it runs in (else the
    home directory), and the deck runs beside the result: a `.spiceinit`
    beside the testbench (open PDKs ship one: compatibility mode, model
    checks) is copied there. One already there with other content is never
    overwritten (it may be the person's own). Returns what was done, or None."""
    import shutil
    src = os.path.join(os.path.dirname(os.path.abspath(testbench)), ".spiceinit")
    dst = os.path.join(os.path.dirname(deck), ".spiceinit")
    if not os.path.isfile(src) or os.path.abspath(src) == os.path.abspath(dst):
        return None
    if os.path.isfile(dst):
        with open(src, "rb") as a, open(dst, "rb") as b:
            if a.read() == b.read():
                return f"{dst} already holds the .spiceinit beside the testbench"
        raise EmirError("EMIR-E105", f"{dst} exists and differs from {src}: emir does not "
                        f"overwrite it (ngspice reads it where the deck runs); remove it or "
                        f"write the result to another directory", path=dst)
    try:
        shutil.copyfile(src, dst)
    except OSError as e:
        raise EmirError("EMIR-E105", f"cannot copy {src} to {dst}: {e.strerror or e}",
                        path=dst) from e
    return f"{src} copied beside the deck (ngspice reads .spiceinit where it runs)"


def sim_command(sim: str, exe: str, deck: str, raw: str, extra=()) -> list:
    """The command that runs `deck` (in the result's directory) and writes
    `raw`: Xyce `-a` (ASCII raw), ngspice `-b -r`, HSPICE `-i deck -o stem`
    (writes stem.tr0; the deck asks for ASCII), Spectre `-format psfascii
    -raw dir`; `extra` (--sim-args: Spectre `+aps`, `+preset=cx` ...) is
    appended."""
    if sim == "xyce":
        cmd = [exe, "-a", deck]
    elif sim == "ngspice":
        cmd = [exe, "-b", "-r", os.path.basename(raw), deck]
    elif sim == "hspice":
        stem = os.path.basename(raw)
        stem = stem[:-len(".tr0")] if stem.lower().endswith(".tr0") else stem
        cmd = [exe, "-i", os.path.basename(deck), "-o", stem]
    else:
        cmd = [exe, os.path.basename(deck), "-format", "psfascii", "-raw",
               os.path.basename(raw)]
    if extra:
        # ngspice and Xyce read the deck last: the options go before it
        cmd = cmd[:-1] + list(extra) + cmd[-1:] if sim in ("xyce", "ngspice") \
            else cmd + list(extra)
    return cmd


def sim_args(text: str | None, command: str = "simulate") -> list:
    """`--sim-args` split as a shell would (EMIR-E513 when it does not split)."""
    if not text:
        return []
    try:
        return shlex.split(text)
    except ValueError as e:
        raise EmirError("EMIR-E513", f"--sim-args {text!r}: {e}", command=command) from e


def run_simulator(sim: str, exe: str, deck: str, raw: str, timeout: float, log=_stderr,
                  extra=(), option: str = "--timeout"):
    """Run one deck; the result is `raw` (an ASCII result file, or Spectre's
    PSF ASCII directory). Coded failures; `option` is the command's option
    that sets `timeout` (EMIR-E605 names it)."""
    import shutil
    cwd = os.path.dirname(raw)
    cmd = sim_command(sim, exe, deck, raw, extra)
    logf = raw + ".log"
    log(f"[emir] {sim}: {shlex.join(cmd)}  (in {cwd})")
    t0 = time.time()

    def drop():
        # a stopped or failed run leaves no result that could pass for one
        with contextlib.suppress(OSError):
            if os.path.isdir(raw):
                shutil.rmtree(raw)
            else:
                os.remove(raw)
    try:
        # a wrapper's simulator is stopped with it (its process group)
        p = subproc.run(cmd, timeout=timeout, cwd=cwd)
    except subprocess.TimeoutExpired as e:
        part = e.stdout or ""
        _write_log(logf, part.decode("utf-8", "replace") if isinstance(part, bytes) else part)
        drop()
        raise EmirError("EMIR-E605", f"{sim} ran longer than {timeout:g} s on {deck} and was "
                        f"stopped; run it yourself (the deck is ready) or raise {option}",
                        path=logf) from e
    secs = time.time() - t0
    text = (p.stdout or "") + (p.stderr or "")
    if sim == "hspice":
        # HSPICE writes its messages to <stem>.lis
        lis = os.path.splitext(raw)[0] + ".lis"
        with contextlib.suppress(OSError):
            with open(lis, encoding="utf-8", errors="replace") as f:
                text += "\n" + f.read()
    _write_log(logf, text)
    if p.returncode != 0:
        raise EmirError("EMIR-E602", f"{sim} failed on {deck} (exit {p.returncode}):\n"
                        f"{_error_lines(text) or text[-2000:]}", path=logf)
    empty = (not os.path.exists(raw) or (os.path.isdir(raw) and not os.listdir(raw))
             or (os.path.isfile(raw) and os.path.getsize(raw) == 0))
    if empty:
        raise EmirError("EMIR-E661", f"{sim} exited 0 but wrote no result at {raw}: did the "
                        f"testbench run a transient analysis? (log: {logf})", path=logf)
    return secs, logf


def _write_log(path, text):
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError:
        pass


def cmd_simulate(argv, out) -> int:
    """`emir simulate`."""
    ap = parser("emir simulate", description=(
        "Simulate a testbench so that `emir run --cc-sim` can read the currents: writes a "
        "copy of the testbench with the save lines the mode needs (Xyce .print, ngspice "
        ".save, HSPICE .probe with ASCII output; Spectre: a deck that includes the "
        "testbench and a file of save statements), runs it to a text result, and writes "
        "the <result>.names sidecar that maps the simulator's names to the DSPF's. The "
        "testbench is the person's: stimulus, models, a .tran analysis, and the extracted "
        "netlist (the DSPF itself for rcc, a CC netlist for cc). Spectre and HSPICE need "
        "a licence: without --run the deck is written and the exact command printed, "
        "nothing runs (ask the person, then add --run)."))
    ap.add_argument("testbench", help="the testbench (its .include files are followed; "
                                      "Spectre: any syntax Spectre reads)")
    ap.add_argument("--sim", required=True, choices=SIMS,
                    help="the simulator (spectre and hspice: licensed, planned unless --run)")
    ap.add_argument("--dspf", required=True, help="the DSPF the currents are for")
    ap.add_argument("--net", default=None, help="one net (default: every net)")
    from .modes import mode_arg
    ap.add_argument("--mode", required=True, type=mode_arg, choices=("rcc", "cc-grid"),
                    help="the mode the run will use, the person's choice (`emir next` "
                         "lists the trade-offs): rcc -- the testbench simulates the RC "
                         "netlist and every branch current is saved (exact, slow on big "
                         "blocks); cc-grid -- it simulates the CC netlist (devices on "
                         "ideal rails) and the instance-pin currents are saved. `cc` is "
                         "read as cc-grid")
    ap.add_argument("--out", default=os.path.join("emir_out", "sim", "result.raw"),
                    metavar="RAW", help="the result to write (default "
                                        "emir_out/sim/result.raw; HSPICE: <stem>.tr0; "
                                        "Spectre: a PSF ASCII directory); the deck, the log "
                                        "and the names sidecar go beside it")
    ap.add_argument("--prefix", default=None,
                    help="the instance path of the extracted block in the testbench, e.g. "
                         "XI0. (default: found in the testbench; Spectre: required, '' for "
                         "the top level)")
    ap.add_argument("--subckt", default=None,
                    help="the block's .subckt name (default: the DSPF's design)")
    ap.add_argument("--probe", action="store_true",
                    help="cc: put a 0 V probe on every device terminal on the net(s) first "
                         "(`emir probe`), for devices inside subcircuits (not Spectre)")
    ap.add_argument("--probes", default=None, metavar="FILE",
                    help="cc: the testbench was probed by `emir probe`; its --names-out file")
    ap.add_argument("--types", default=DEFAULT_TYPES,
                    help=f"with --probe: element letters to probe (default {DEFAULT_TYPES})")
    ap.add_argument("--pins", action="append", default=None, metavar="SUBCKT=P1,P2,...",
                    help="with --probe: port names of a subcircuit the netlist does not "
                         "define")
    ap.add_argument("--process", default=None,
                    help="the process file, so the next steps are the exact check and run "
                         "commands")
    ap.add_argument("--timeout", type=float, default=3600.0, metavar="S",
                    help="stop the simulator after this many seconds (default 3600)")
    ap.add_argument("--run", action="store_true",
                    help="spectre, hspice (licensed): run the simulator; without it the deck "
                         "is written and the command printed, and nothing runs (the open "
                         "simulators always run)")
    ap.add_argument("--sim-args", default=None, metavar="ARGS",
                    help="extra arguments for the simulator's command line, split as a "
                         "shell would: Spectre APS '+aps', Spectre X '+preset=cx "
                         "+postlpreset=off' (keep the parasitic resistors for rcc) ...")
    a = ap.parse_args(argv)
    a.extra = sim_args(a.sim_args)
    if a.probe and a.probes:
        raise EmirError("EMIR-E507", "give --probe or --probes, not both", command="simulate")
    from .dspf import did_you_mean, parse
    d = parse(a.dspf)
    if a.net and a.net != "all" and not d.has_net(a.net):
        raise EmirError("EMIR-E204", f"net {a.net!r} is not in {a.dspf}"
                        f"{did_you_mean(a.net, d.nets)}", dspf=a.dspf)
    raw = os.path.abspath(a.out)
    if a.sim == "hspice" and not raw.lower().endswith(".tr0"):
        # HSPICE names its result <stem>.tr0 (`-o stem`)
        raw = os.path.splitext(raw)[0] + ".tr0"
    if re.search(r"\s", os.path.basename(raw)):
        raise EmirError("EMIR-E513", f"--out {a.out!r}: the result's file name cannot hold "
                        f"a space (Xyce reads `file=` up to the first blank)",
                        command="simulate")
    if a.sim == "spectre":
        return _simulate_spectre(a, argv, out, d, raw)
    tree = Tree(a.testbench)
    if not tree.analyses:
        raise EmirError("EMIR-E462", f"{a.testbench} (and its includes) has no .tran "
                        f"analysis: EM needs the currents over time", path=a.testbench)
    scope, why_scope = scope_of(tree, d, a.subckt)
    path, why_path = scope_path(tree, scope, a.prefix, command="simulate")
    from .modes import save_kind
    mode, why_mode = save_kind(a.mode), f"--mode {a.mode}"
    nets = target_nets(d, a.net, mode)
    if not nets and mode == "rcc":
        raise EmirError("EMIR-E209", f"no net of {a.dspf} has resistors", dspf=a.dspf)
    if not nets:
        raise EmirError("EMIR-E211", f"no net of {a.dspf} has instance pins", dspf=a.dspf)
    probes, edits, pres = None, {}, None
    if mode == "cc" and a.probe:
        pres, _, _, _ = probe_tree(tree, nets, d=d, subckt=a.subckt, prefix=a.prefix,
                                   types=_types_option(a.types, "simulate"),
                                   pins=_pins_option(a.pins), command="simulate")
        _probe_notices(pres, nets, uncovered=False)
        probes = {p["pin"].upper(): p for p in pres.probes}
        edits = pres.edits
    elif mode == "cc" and a.probes:
        probes = load_probes(a.probes)
    kinds = {e.name.upper(): e.kind for e in tree.elements(scope)}
    if mode == "cc":
        # the DSPF's instances must be devices of the testbench (a probed one
        # keeps them): when they are not, it is the wrong testbench (or
        # scope), whatever a probe would do
        insts = sorted({k.rpartition(":")[0] for n in nets for k in d.pin_groups(n)})
        absent = [i for i in insts if i.upper() not in kinds]
        if absent:
            where = f"subcircuit {tree.subckts[scope].name}" if scope else "the top level"
            raise EmirError("EMIR-E263", f"{len(absent)} of the {len(insts)} instances with "
                            f"pins on {', '.join(nets)} are not elements of {where} of "
                            f"{tree.top.path}: {', '.join(absent[:8])}"
                            f"{' ...' if len(absent) > 8 else ''}. Is this the testbench of "
                            f"this DSPF (its CC netlist), with the same instance names?",
                            path=tree.top.path)
    ss = save_set(d, nets, mode, a.sim, path, probes=probes, inst_kinds=kinds)
    if ss.unprintable:
        shown = (f"{', '.join(ss.unprintable[:6])}"
                 f"{' ...' if len(ss.unprintable) > 6 else ''}")
        if not a.probe:
            again = command_line("simulate", [x for x in argv if x != "--probe"], "--probe")
            how, why = "Re-run with --probe", "probe every device terminal on the net(s)"
        else:
            # probed, yet still not printable: elements --types leaves out
            # (a device resistor or capacitor), or a second pin of one
            types = _types_option(a.types, "simulate")
            more = "".join(sorted({kinds.get(x.rpartition(":")[0].upper(), "")
                                   for x in ss.unprintable} - set(types) - {""}))
            again = command_line("simulate", strip_options(argv, {"--types": 1}),
                                 "--types", types + (more or "RC"))
            how = f"--types {types} leaves their elements out: add {more or 'R and C'}"
            why = "probe those elements too"
        raise EmirError(
            "EMIR-E460",
            f"{len(ss.unprintable)} instance pin(s) on {', '.join(nets)} have no current "
            f"{a.sim} can print directly ({shown}): subcircuit instances, pin names that "
            f"are not the device's terminals, or both pins of one two-terminal device"
            f"{polarity_note(ss)}. {how}", details={"pins": ss.unprintable},
            next_steps=[{"why": f"{why}, then simulate", "command": again}])
    _check_elements(tree, scope, path, ss,
                    added=[p["source"] for p in pres.probes] if pres else ())
    from .verify_sim import args_key
    # the sidecar says how the result was made: the simulator, its version
    # (when it runs here) and the arguments (`emir verify-sim` keys on them)
    ss.sim_args = args_key(a.sim_args)
    licensed = a.sim in LICENSED_SIMS
    exe = None
    if not licensed or a.run:
        exe = sim_exe(a.sim)
        notice("EMIR-I660", f"{a.sim} found: {exe}", _stderr)
    stem = os.path.splitext(raw)[0]
    deck = f"{stem}.{a.sim}.{'sp' if a.sim == 'hspice' else 'cir'}"
    for stale in (raw, raw + ".names"):
        if os.path.exists(stale):
            os.remove(stale)
    lines = ss.lines(raw=os.path.basename(raw), deck=os.path.basename(deck))
    copies = Writer(tree, deck, a.sim, edits=edits, inject=lines, strip=a.sim == "xyce",
                    command="simulate").write()
    if tree.top.alter is not None:
        notice("EMIR-I610", f"{a.testbench} has .alter cases: the {len(ss.signals)} saved "
               f"currents go before the first .alter (line "
               f"{tree.top.chunks[tree.top.alter].lineno}), so the main case and every "
               f"altered case save them; the result read is the main case's", _stderr)
    stripped = [p for p, sf in tree.files.items() if sf.annotated and a.sim == "xyce"]
    if stripped:
        notice("EMIR-I261", f"the DSPF $ annotations of {len(stripped)} file(s) are left out "
               f"of the {a.sim} deck (Xyce reads them as parameters): "
               f"{', '.join(os.path.basename(p) for p in stripped[:4])}", _stderr)
    if tree.has_control and a.sim == "ngspice":
        notice("EMIR-W660", "the testbench has a .control section: ngspice -b -r runs its "
               "commands too (what it sets applies to the result; a `shell` line runs a "
               "program): the deck is code, run it only if the person trusts it", _stderr)
    notes = []
    if a.sim == "ngspice":
        init = spiceinit(a.testbench, deck)
        if init:
            notes.append(init)
            notice("EMIR-I262", init, _stderr)
    common = dict(simulator=a.sim, testbench=os.path.abspath(a.testbench), deck=deck,
                  files=[{"from": s_, "to": t} for s_, t in copies.items()], result=raw,
                  mode=a.mode, mode_reason=why_mode, nets=nets,
                  scope=tree.subckts[scope].name if scope else None, path=path,
                  where=f"{why_scope}; {why_path}", signals=len(ss.signals),
                  direct=ss.direct, probed=ss.probed, analysis=tree.analyses[0])
    if licensed and not a.run:
        side = write_sidecar(raw + ".names", ss.sidecar())
        return _simulate_plan(a, argv, out, raw, deck, mode, os.path.abspath(side), common,
                              notes)
    if a.sim in ("hspice", "spectre"):
        ss.version = sim_version(a.sim, exe)
    elif exe:
        ss.version = sim_version(a.sim, exe, timeout=30.0)
    secs, logf = run_simulator(a.sim, exe, deck, raw, a.timeout, extra=a.extra)
    side = write_sidecar(raw + ".names", ss.sidecar())
    from . import waves
    w = waves.read(raw)
    got = w.match(ss.dspf_names)
    missing = [n for n in ss.dspf_names if n not in got]
    if missing:
        notice("EMIR-W461", f"{len(missing)} of the {len(ss.dspf_names)} currents asked for "
               f"are not in {raw}: {', '.join(missing[:6])}"
               f"{' ...' if len(missing) > 6 else ''}", _stderr)
    print(f"[emir] {a.sim} finished in {secs:.2f} s: {w.summary()}")
    print(f"  result : {raw}\n  names  : {os.path.abspath(side)}\n  deck   : {deck}\n"
          f"  log    : {logf}\n  mode   : {mode} ({why_mode})")
    out.emit(document(
        "simulate", **common, ran=True, executable=exe, simulator_version=ss.version,
        command=sim_command(a.sim, exe, deck, raw, a.extra), cwd=os.path.dirname(raw),
        names_sidecar=os.path.abspath(side), log=logf, found=len(got), missing=missing,
        seconds=round(secs, 3), points=int(len(w.x)), notes=notes,
        next_steps=simulate_next_steps(a, raw, mode, out)))
    return 0


def _simulate_plan(a, argv, out, raw, deck, mode, side, common, notes) -> int:
    """A licensed simulator without --run: the deck is written, the command
    printed, nothing runs (EMIR-I661); the next step is the same command
    with --run, after the person's go-ahead."""
    from . import site
    exe, _src = site.tool(a.sim)
    cmd = sim_command(a.sim, exe or a.sim, deck, raw, a.extra)
    notice("EMIR-I661", f"{a.sim} needs a licence: nothing was run. Ask the person; then "
           f"re-run with --run (or run `{shlex.join(cmd)}` in {os.path.dirname(raw)})",
           _stderr)
    print(f"[emir] {a.sim} (licensed): the deck is ready, nothing was run")
    print(f"  deck    : {deck}\n  command : {shlex.join(cmd)}\n  in      : "
          f"{os.path.dirname(raw)}\n  result  : {raw}"
          + (f"\n  names   : {side}" if side else ""))
    steps = [{"why": f"ask the person first ({a.sim} is licensed); then run it: the same "
                     f"command with --run simulates, reads the result back and checks the "
                     f"currents",
              "command": command_line("simulate", strip_options(argv, {"--format": 1}),
                                      "--run", out.fmt_args())}]
    out.emit(document(
        "simulate", **common, ran=False, executable=exe, simulator_version=None,
        command=cmd, cwd=os.path.dirname(raw), names_sidecar=side, log=None, found=None,
        missing=[], seconds=None, points=None, notes=notes, next_steps=steps))
    return 0


def _simulate_spectre(a, argv, out, d, raw) -> int:
    """`emir simulate --sim spectre`: the testbench is not read (it may be in
    Spectre's own syntax); a deck includes it and a file of `save`
    statements, and Spectre writes PSF ASCII into the `raw` directory."""
    if a.probe or a.probes:
        raise EmirError("EMIR-E513", "--probe and --probes are for the SPICE simulators: "
                        "Spectre saves an instance's terminal current (inst:pin) directly",
                        command="simulate")
    if a.prefix is None:
        raise EmirError("EMIR-E507", "--sim spectre needs --prefix: the instance path of the "
                        "extracted block in the testbench (e.g. XI0.), or --prefix '' when "
                        "its elements are at the top level (the testbench is not read)",
                        command="simulate")
    if not os.path.isfile(a.testbench):
        raise EmirError("EMIR-E101", f"{a.testbench}: not found", path=a.testbench)
    from .ccsim import save_directives
    from .rcc import save_branches
    from .modes import save_kind
    kind = save_kind(a.mode)
    nets = target_nets(d, a.net, kind)
    modes = {n: kind for n in nets}
    why_mode, mode = f"--mode {a.mode}", kind
    if not nets:
        raise EmirError("EMIR-E209", f"no net of {a.dspf} has resistors", dspf=a.dspf)
    text = "\n".join(save_branches(d, n, prefix=a.prefix) if m == "rcc"
                     else save_directives(d, n, prefix=a.prefix) for n, m in modes.items())
    names = [t for ln in text.splitlines() if ln.startswith("save ")
             for t in ln.split()[1:]]
    stem = os.path.splitext(raw)[0]
    deck = f"{stem}.spectre.scs"
    save_file = f"{stem}.save.scs"
    from .output import write_text
    write_text(save_file, "// emir: the currents a run needs (Spectre save statements)\n"
               "simulator lang=spectre\n" + text + "\n")
    write_text(deck, spectre_deck(os.path.abspath(a.testbench), save_file))
    for stale in (raw,):
        if os.path.isdir(stale) and not os.listdir(stale):
            os.rmdir(stale)
    dspf_names = [n[len(a.prefix):] if a.prefix and n.startswith(a.prefix) else n
                  for n in names]
    common = dict(simulator="spectre", testbench=os.path.abspath(a.testbench), deck=deck,
                  files=[{"from": os.path.abspath(a.testbench), "to": deck},
                         {"from": "(save statements)", "to": save_file}], result=raw,
                  mode=a.mode, mode_reason=why_mode, nets=nets, scope=None,
                  path=split_path(a.prefix), where=f"--prefix {a.prefix!r}",
                  signals=len(names), direct=len(names), probed=0, analysis=None)
    from .verify_sim import args_key
    # Spectre's names need no map; the sidecar beside the result directory
    # (`<result>.names`) keeps how it was made: the version (when Spectre
    # runs here) and the simulator arguments (`emir verify-sim` keys on them)
    facts = {"map": {}, "simulator": "spectre", "sim_args": args_key(a.sim_args)}
    side = raw.rstrip(os.sep) + ".names"
    if not a.run:
        write_sidecar(side, facts)
        return _simulate_plan(a, argv, out, raw, deck, mode, os.path.abspath(side), common,
                              [])
    exe = sim_exe("spectre")
    version = sim_version("spectre", exe)
    if os.path.isdir(raw):
        import shutil
        shutil.rmtree(raw)
    if os.path.exists(side):
        os.remove(side)
    secs, logf = run_simulator("spectre", exe, deck, raw, a.timeout, extra=a.extra)
    write_sidecar(side, {**facts, **({"simulator_version": version} if version else {})})
    from . import waves
    w = waves.read(raw)
    got = w.match(dspf_names)
    missing = [n for n in dspf_names if n not in got]
    if missing:
        notice("EMIR-W461", f"{len(missing)} of the {len(dspf_names)} currents asked for "
               f"are not in {raw}: {', '.join(missing[:6])}"
               f"{' ...' if len(missing) > 6 else ''}", _stderr)
    print(f"[emir] spectre finished in {secs:.2f} s: {w.summary()}")
    out.emit(document(
        "simulate", **common, ran=True, executable=exe, simulator_version=version,
        command=sim_command("spectre", exe, deck, raw, a.extra), cwd=os.path.dirname(raw),
        names_sidecar=os.path.abspath(side), log=logf, found=len(got), missing=missing,
        seconds=round(secs, 3), points=int(len(w.x)), notes=[],
        next_steps=simulate_next_steps(a, raw, mode, out)))
    return 0


def spectre_deck(testbench: str, save_file: str) -> str:
    """The deck `emir simulate --sim spectre` runs: the person's testbench,
    then the save statements. Spectre reads an included file in Spectre
    syntax when its name ends in .scs or it starts with `simulator
    lang=spectre`, else in SPICE syntax, and restores the language after it
    (Spectre Circuit Simulator Reference 19.1, "Include File")."""
    return ("// written by emir simulate --sim spectre: the testbench, then the save\n"
            "// statements EMIR needs. Run: spectre <this file> -format psfascii -raw <dir>\n"
            "simulator lang=spectre\n"
            f'include "{testbench}"\n'
            f'include "{os.path.basename(save_file)}"\n')


def simulate_next_steps(a, raw, mode, out) -> list:
    """After a simulation: check, run and report (with --process), else
    `emir next` with the result."""
    net = _net_args(a.net)
    rmode = "rcc" if mode == "rcc" else "cc-grid"
    if not a.process:
        return [{"why": "plan the screening of this result (it reads the result and the "
                        "sidecar beside it)",
                 "command": command_line("next", "--dspf", os.path.abspath(a.dspf), "--cc-sim",
                                         raw, *net, "--mode", rmode, out.fmt_args())}]
    args = [os.path.abspath(a.dspf), "--process", os.path.abspath(a.process), *net,
            "--cc-sim", raw, "--mode", rmode]
    summary = os.path.join("emir_out", "summary.json")
    return [{"why": "check the inputs before the run (exit 2 and coded FAILs if not)",
             "command": command_line("check", args, out.fmt_args())},
            {"why": "screen EM and IR from the simulated currents",
             "command": command_line("run", args, "--summary-out", summary, out.fmt_args())},
            {"why": "the summary to relay to the person (screening, not sign-off)",
             "command": command_line("report", "--from", summary)}]
