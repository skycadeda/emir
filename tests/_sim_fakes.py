"""Stand-ins for HSPICE and Spectre, for tests only.

They are NOT simulators and model nothing of HSPICE or Spectre but what
EMIR's side of the route touches: the command line, the deck's save or probe
lines, the result file they write (HSPICE ASCII `.tr0` with `emir.hspice`'s
writer; PSF ASCII with the test helper's) and the exit status. They know one
circuit -- `emir verify-sim`'s testbench (the sample rail, loads ILOAD0..3,
or the devices of its dev case) -- and give its branch currents exactly: a
branch Rm1..Rm3 carries four loads, Rm4..Rm6 three, Rm7..Rm9 two,
Rm10..Rm12 and Rc0..Rc3 one; each load is 1 mA (dc, and the dev case's
devices) or the 0 -> 2 mA trapezoid of the pulse case; any other branch (the
dev case's contacts to a bulk or a gate) carries none. The licensed
simulators themselves are verified on the machine that runs them (`emir
verify-sim --run`), never here. Every call appends its argv (JSON) to
`$FAKE_LOG` when it is set; with `FAKE_SIGN=-1` they write every current
with the wrong sign.
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
LOADS = {{"Rm%d" % k: (4 if k <= 3 else 3 if k <= 6 else 2 if k <= 9 else 1)
          for k in range(1, 13)}}
LOADS.update({{"Rc%d" % k: 1 for k in range(4)}})


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
    # i(xi0.rm1) (HSPICE), XI0.Rm1:1 (Spectre) -> RM1
    m = re.match(r"^([A-Za-z]\w*)\((.*)\)$", name)
    inner = m.group(2) if m else name
    base = inner.split(".")[-1].upper().partition(":")[0]
    if base in [k.upper() for k in LOADS]:
        n = next(v for k, v in LOADS.items() if k.upper() == base)
        return SIGN * n * load(t, pulse)
    return 0.0
'''

_HSPICE = _FAKE + r'''
deck = a[a.index("-i") + 1]
stem = a[a.index("-o") + 1]
text = text_of(deck)
pulse = "PULSE(" in text.upper()
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
rows = [[t] + [current(n, t, pulse) for n in names] for t in ts]
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
