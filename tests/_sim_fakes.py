"""Stand-ins for HSPICE and Spectre, for tests only.

They are NOT simulators and model nothing of HSPICE or Spectre but what
EMIR's side of the route touches: the command line, the deck's save or probe
lines, the result file they write (HSPICE ASCII `.tr0` with `emir.hspice`'s
writer; PSF ASCII with the test helper's) and the exit status. They know one
circuit -- `emir verify-sim`'s testbench (the sample rail, loads ILOAD0..3;
and its self-consistent case, resistive loads RLOAD0..3 of 1200 ohm, each on
the rail plus the droop parameter the loop writes) -- and give its currents exactly: a branch Rm1..Rm3 carries four loads, Rm4..Rm6
three, Rm7..Rm9 two, Rm10..Rm12 and Rc0..Rc3 one; each load is 1 mA (dc) or
the 0 -> 2 mA trapezoid of the pulse case. The licensed simulators themselves
are verified on the machine that runs them (`emir verify-sim --run`), never
here. They also know its dev case: the current into each terminal of MP0
(a diode-connected PMOS fed by 1 mA: d -1 mA, s +1 mA), MN1 and the
subcircuit-wrapped XM3 (diode-connected NMOS: d +1 mA, s -1 mA), RL2 (pin 1
-1 mA, pin 2 +1 mA), every gate and bulk 0; a 0 V probe's current is the
one into the terminal it is in series with (found in the deck). Every call
appends its argv (JSON) to `$FAKE_LOG` when it is set; with `FAKE_SIGN=-1`
they write every current with the wrong sign, `FAKE_DEV_SIGN=-1` only the
devices' terminal currents (all of a device's terminals alike, so KCL still
closes), `FAKE_HSPICE_I3=out` writes a MOSFET's i3 out of the device.
"""
import os
import stat
import sys

from _helpers import ROOT

_FAKE = r'''#!{python}
import json, math, os, re, sys
sys.path.insert(0, {root!r})
NAME = {name!r}
a = sys.argv[1:]
log = os.environ.get("FAKE_LOG")
if log:
    with open(log, "a") as f:
        f.write(json.dumps({{"tool": NAME, "argv": a, "cwd": os.getcwd()}}) + "\n")
if a and a[0] in ("-v", "-V"):
    print("fake" + NAME + " version 1.2.3 (a stand-in for the tests, not " + NAME + ")")
    sys.exit(0)
SIGN = float(os.environ.get("FAKE_SIGN", "1"))
DEV_SIGN = float(os.environ.get("FAKE_DEV_SIGN", "1"))
#: the self-consistent case of verify-sim: resistive loads (1200 ohm) on
#: per-pin supplies, each the rail (1.2 V) plus the droop waveform the loop
#: writes (a PWL source per pin: inline in the include for HSPICE, a file per
#: pin for Spectre)
WAVES, SPLIT = {{}}, {{}}
RLOAD_OHM = 1200.0
RAIL_V = 1.2


def read_params(text, here="."):
    stmts = []
    for ln in text.splitlines():
        if ln.lstrip().startswith("+") and stmts:
            stmts[-1] += " " + ln.lstrip()[1:]
        else:
            stmts.append(ln)
    for st in stmts:
        m = re.match(r"^\s*(VEMIRSC\d+)\s+(\S+)\s+\S+\s+(.*)$", st, re.I)
        if m:
            pw = re.search(r"PWL\s*\(([^)]*)\)", m.group(3), re.I)
            nums = [float(x) for x in pw.group(1).split()] if pw else [0.0, 0.0]
            WAVES[m.group(2).upper()] = (nums[0::2], nums[1::2])
            continue
        m = re.match(r'^\s*(\w+)\s*\(\s*(\S+)\s+\S+\s*\)\s*vsource\s+type=pwl\s+'
                     r'file="([^"]+)"', st, re.I)
        if m:
            f = m.group(3) if os.path.isabs(m.group(3)) else os.path.join(here, m.group(3))
            pts = [ln.split() for ln in open(f).read().splitlines() if ln.strip()]
            WAVES[m.group(2).upper()] = ([float(a) for a, _ in pts], [float(b) for _, b in pts])
    for m in re.finditer(r"^\s*(VEMIR\d+)\s+(\S+)\s+\S+\s+0\b", text, re.I | re.M):
        if m.group(2).upper() in WAVES:
            SPLIT[m.group(1).upper()] = m.group(2).upper()


def droop(node, t):
    ts, vs = WAVES[node]
    if t <= ts[0]:
        return vs[0]
    for i in range(1, len(ts)):
        if t <= ts[i]:
            return vs[i - 1] + (vs[i] - vs[i - 1]) * (t - ts[i - 1]) / (ts[i] - ts[i - 1])
    return vs[-1]
LOADS = {{"Rm%d" % k: (4 if k <= 3 else 3 if k <= 6 else 2 if k <= 9 else 1)
          for k in range(1, 13)}}
LOADS.update({{"Rc%d" % k: 1 for k in range(4)}})
# the dev case: the current INTO each terminal of its devices
DEV = {{"MP0": {{"d": -1e-3, "g": 0.0, "s": 1e-3, "b": 0.0}},
        "MN1": {{"d": 1e-3, "g": 0.0, "s": -1e-3, "b": 0.0}},
        "XM3": {{"d": 1e-3, "g": 0.0, "s": -1e-3, "b": 0.0}},
        "RL2": {{"1": -1e-3, "2": 1e-3}}}}
ORDER = {{"MP0": "dgsb", "MN1": "dgsb", "XM3": "dgsb", "RL2": "12"}}
PROBES = {{}}


def find_probes(text):
    """`PROBES[VEMIRk] = (element, terminal index)` from a probed deck."""
    nodes = {{}}
    for ln in text.splitlines():
        w = ln.split()
        if len(w) >= 4 and re.match(r"^VEMIR\d+$", w[0], re.I) and w[3] == "0":
            nodes[w[2].upper()] = w[0].upper()
    for ln in text.splitlines():
        w = ln.split()
        if not w or w[0][:1] in ".*+" or w[0].upper().startswith("VEMIR"):
            continue
        for i, x in enumerate(w[1:]):
            if x.upper() in nodes:
                PROBES[nodes[x.upper()]] = (w[0].upper(), i)


def dev(inst, term):
    return SIGN * DEV_SIGN * DEV[inst][term]


def text_of(path, seen=None):
    seen = seen or set()
    path = os.path.abspath(path)
    if path in seen or not os.path.isfile(path):
        return ""
    seen.add(path)
    with open(path) as f:
        t = f.read()
    out = t
    for m in re.finditer(r'^\s*\.?include\s+["\']?([^"\'\s]+)', t, re.I | re.M):
        out += "\n" + text_of(os.path.join(os.path.dirname(path), m.group(1)), seen)
    return out


def load(t, pulse):
    if not pulse:
        return 1e-3
    x = t - 1e-9
    if x < 0:
        return 0.0
    x = x % 10e-9
    if x < 0.1e-9:
        return 2e-3 * x / 0.1e-9
    if x < 5.0e-9:
        return 2e-3
    if x < 5.1e-9:
        return 2e-3 * (5.1e-9 - x) / 0.1e-9
    return 0.0


def times(pulse):
    pts = {{k * 0.5e-9 for k in range(81)}}
    if pulse:
        for p in range(4):
            for c in (0.0, 0.1e-9, 5.0e-9, 5.1e-9):
                pts.add(1e-9 + p * 10e-9 + c)
    return sorted(t for t in pts if t <= 40e-9 + 1e-18)


def current(name, t, pulse):
    # i(xi0.rm1) (HSPICE), XI0.Rm1:1 / XI0.ILOAD0:p (Spectre) -> RM1 / ILOAD0;
    # i3(xi0.mp0) (HSPICE) / XI0.MP0:s (Spectre) -> a device terminal;
    # XI0.RLOAD0:1 / a probe VEMIRn -> the self-consistent case's resistive load
    m = re.match(r"^([A-Za-z]\w*)\((.*)\)$", name)
    inner = m.group(2) if m else name
    base, _, pin = inner.split(".")[-1].upper().partition(":")
    lead = re.match(r"^[iI]([1-4])$", m.group(1)) if m else None
    if base in DEV:
        if lead:
            return dev(base, ORDER[base][int(lead.group(1)) - 1])
        if pin:
            return dev(base, pin.lower())
    if base in SPLIT:                      # an emir split: the probe of a resistive load
        return SIGN * (RAIL_V + droop(SPLIT[base], t)) / RLOAD_OHM
    if base.startswith("RLOAD"):           # the Spectre split emir writes
        return SIGN * (RAIL_V + droop("VT_" + base + "_1", t)) / RLOAD_OHM
    if base in [k.upper() for k in LOADS]:
        n = next(v for k, v in LOADS.items() if k.upper() == base)
        return SIGN * n * load(t, pulse)
    if base.startswith("VEMIR"):
        el, i = PROBES.get(base, ("ILOAD", 0))
        if el in DEV:
            return dev(el, ORDER[el][i])
        return SIGN * load(t, pulse)
    if base.startswith("ILOAD"):
        return SIGN * load(t, pulse)
    return 0.0
'''

_HSPICE = _FAKE + r'''
deck = a[a.index("-i") + 1]
stem = a[a.index("-o") + 1]
text = text_of(deck)
read_params(text, os.path.dirname(os.path.abspath(deck)))
pulse = "PULSE(" in text.upper()
find_probes(text)
names = []
take = False
for ln in open(deck).read().splitlines():
    s = ln.strip()
    if s.lower().startswith(".probe tran"):
        names += s.split()[2:]
        take = True
    elif s.startswith("+") and take:
        names += s[1:].split()
    else:
        take = False
from emir.hspice import write_tr0ascii
ts = times(pulse)
rows = []
for t in ts:
    row = [t]
    for n in names:
        v = current(n, t, pulse)
        if os.environ.get("FAKE_HSPICE_I3") == "out" and n.lower().startswith("i3("):
            v = -v
        row.append(v)
    rows.append(row)
write_tr0ascii(stem + ".tr0", ["TIME"] + names, [([], rows)], version="2001",
               title="* stand-in hspice: " + os.path.basename(deck),
               copyright_="stand-in for the tests, not HSPICE")
with open(stem + ".lis", "w") as f:
    f.write("stand-in hspice: %d probes\n" % len(names))
'''

_SPECTRE = _FAKE + r'''
deck = a[0]
raw = a[a.index("-raw") + 1]
text = text_of(deck)
read_params(text, os.path.dirname(os.path.abspath(deck)))
pulse = "PULSE(" in text.upper()
names = []
for ln in text.splitlines():
    s = ln.strip()
    if s.startswith("save "):
        names += s.split()[1:]
os.makedirs(raw, exist_ok=True)
ts = times(pulse)
L = ['HEADER', '"PSFversion" "1.00"', '"simulator" "spectre"',
     '"version" "fake 1.2.3"', '"analysis type" "tran"', '"analysis name" "emirtran"',
     'TYPE', '"I" FLOAT DOUBLE PROP(', '"units" "A"', ')',
     'SWEEP', '"time" "sweep" PROP(', '"units" "s"', ')', 'TRACE']
for n in names:
    L += ['"%s" "I" PROP(' % n, '"units" "A"', ')']
L.append("VALUE")
for t in ts:
    L.append('"time" %.15e' % t)
    for n in names:
        L.append('"%s" %.15e' % (n, current(n, t, pulse)))
L.append("END")
with open(os.path.join(raw, "emirtran.tran"), "w") as f:
    f.write("\n".join(L) + "\n")
print("stand-in spectre: %d saves" % len(names))
'''


def install(tmp_path, monkeypatch=None) -> dict:
    """Write `hspice` and `spectre` stand-ins into tmp_path/bin; with
    `monkeypatch`, point $HSPICE_BIN and $SPECTRE_BIN at them."""
    d = os.path.join(str(tmp_path), "bin")
    os.makedirs(d, exist_ok=True)
    paths = {}
    for name, body in (("hspice", _HSPICE), ("spectre", _SPECTRE)):
        p = os.path.join(d, name)
        with open(p, "w") as f:
            f.write(body.format(python=sys.executable, root=ROOT, name=name))
        os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        paths[name] = p
    if monkeypatch is not None:
        monkeypatch.setenv("HSPICE_BIN", paths["hspice"])
        monkeypatch.setenv("SPECTRE_BIN", paths["spectre"])
    return paths
