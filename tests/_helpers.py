"""Shared builders for the tests: DSPF variants of the sample,
synthetic simulation results written as PSF ASCII, and a CLI runner.

Every number here is invented; nothing comes from a real process.
"""
import os
import re
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from emir.selftest import SAMPLE_CURRENTS, sample_paths  # noqa: E402
from emir.validate import _psf_tran  # noqa: E402

DSPF, PROC = sample_paths()
CUR = SAMPLE_CURRENTS
PERIOD = 1e-9


def sample_text() -> str:
    with open(DSPF, encoding="utf-8") as f:
        return f.read()


def write(path, text) -> str:
    path = str(path)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def proc_with(tmp, **via_extra) -> str:
    """The sample process file with extra keys on the Cont via."""
    import yaml
    with open(PROC, encoding="utf-8") as f:
        d = yaml.safe_load(f)
    d["vias"]["Cont"].update(via_extra)
    return write(os.path.join(str(tmp), "proc.yaml"), yaml.safe_dump(d))


def rewrite_vias(text: str, tail: str) -> str:
    """Replace the `$cont ...` annotations of the sample's Rc lines by `tail`
    (keeping the bounding box, so the viewer can still place them)."""
    out = []
    for line in text.splitlines():
        if line.startswith("Rc"):
            f = line.split()
            box = " ".join(t for t in f[4:] if re.match(r"\$(LLX|LLY|URX|URY)=", t))
            line = " ".join(f[:4] + [tail, box])
        out.append(line)
    return "\n".join(out) + "\n"


# ----------------------------------------------------------- waveforms

def time_base(periods=4, per=100):
    return np.linspace(0.0, periods * PERIOD, periods * per + 1)


def device_waves(t) -> dict:
    """Terminal currents of MP0..MP3 in Spectre's convention (positive INTO
    the instance): 1 mA average each, phases 60 degrees apart, so the total
    is not constant and rms/peak differ from avg."""
    return {f"MP{k}:s": 1e-3 * (1.0 + 0.8 * np.sin(2 * np.pi * t / PERIOD + k * np.pi / 3))
            for k in range(4)}


def branch_waves(t) -> dict:
    """What an RCC simulation of the sample (R only, ideal loads) saves: each
    resistor's current from its first terminal to its second."""
    i = [device_waves(t)[f"MP{k}:s"] for k in range(4)]
    out = {}
    for n in range(1, 13):
        first = (n - 1) // 3              # Rm1-3 carry all four, Rm4-6 three ...
        out[f"Rm{n}:1"] = sum(i[first:])
    for k in range(4):
        out[f"Rc{k}:1"] = i[k]
    return out


def cc_result(tmp, name="cc.tran", extra=None) -> str:
    """A result of the sample's pin currents only, no branch current: what
    rcc refuses (EMIR-E415)."""
    t = time_base()
    sigs = device_waves(t)
    sigs.update(extra or {})
    return _psf_tran(os.path.join(str(tmp), name), t, sigs)


def rcc_result(tmp, name="rcc.tran") -> str:
    """An RCC (branch currents only) result for the sample."""
    t = time_base()
    return _psf_tran(os.path.join(str(tmp), name), t, branch_waves(t))


# ----------------------------------------------------------- DSPFs

#: the sample's VDD plus a VSS net (two pins) and a port-less OUT net
TWO_NET_EXTRA = """*|NET VSS 0
*|P (VSS I *1 0.0000 -5.0000)
*|S (VSS:1 10.0000 -5.0000)
*|S (VSS:2 20.0000 -5.0000)
*|I (MN0:s MN0 s I 0 10.0000 -5.0000)
*|I (MN1:s MN1 s I 0 20.0000 -5.0000)
Rs1 VSS VSS:1 1.500000 $metal1 $L=10 $W=1.000000 $LLX=0 $LLY=-5.5 $URX=10 $URY=-4.5 $lvl=1
Rs2 VSS:1 VSS:2 1.500000 $metal1 $L=10 $W=1.000000 $LLX=10 $LLY=-5.5 $URX=20 $URY=-4.5 $lvl=1
Rs3 VSS:1 MN0:s 12.000000 $cont $VC=2 $LLX=9.8 $LLY=-5.2 $URX=10.2 $URY=-4.8 $lvl=0
Rs4 VSS:2 MN1:s 12.000000 $cont $VC=2 $LLX=19.8 $LLY=-5.2 $URX=20.2 $URY=-4.8 $lvl=0
*|NET OUT 0
*|S (OUT:1 15.0000 -10.0000)
*|I (MN0:d MN0 d I 0 10.0000 -10.0000)
*|I (MN1:d MN1 d I 0 20.0000 -10.0000)
Ro1 OUT:1 MN0:d 1.000000 $metal1 $L=5 $W=0.750000 $LLX=10 $LLY=-10.375 $URX=15 $URY=-9.625 $lvl=1
Ro2 OUT:1 MN1:d 1.000000 $metal1 $L=5 $W=0.750000 $LLX=15 $LLY=-10.375 $URX=20 $URY=-9.625 $lvl=1
"""


def two_net_dspf(tmp) -> str:
    text = sample_text().replace(".ENDS", TWO_NET_EXTRA + ".ENDS")
    return write(os.path.join(str(tmp), "twonet.dspf"), text)


def two_net_branches(t) -> dict:
    """Every branch current of `two_net_dspf`: the sample's VDD; 1 mA from
    MN0 and 2 mA from MN1 into VSS, to its port; 1 mA from MN0:d to MN1:d
    through the port-less OUT (each current from the resistor's first node to
    its second)."""
    one = np.ones_like(t)
    sigs = branch_waves(t)
    sigs.update({"Rs1:1": -3e-3 * one, "Rs2:1": -2e-3 * one, "Rs3:1": -1e-3 * one,
                 "Rs4:1": -2e-3 * one, "Ro1:1": -1e-3 * one, "Ro2:1": 1e-3 * one})
    return sigs


def two_net_result(tmp, name="all.tran") -> str:
    """An RCC result of `two_net_dspf`: every branch current of its nets."""
    t = time_base()
    return _psf_tran(os.path.join(str(tmp), name), t, two_net_branches(t))


#: a parent net with a black-boxed block whose supply pin XB:VDDB reaches the
#: net at two places (two `*|I` records, one saved terminal current)
SPLIT_DSPF = """*|DSPF 1.3
*|DESIGN "top"
*|OPERATING_TEMPERATURE 100
*|NET VDD 0
*|P (VDD I *1 0.0000 0.0000)
*|S (VDD:1 10.0000 0.0000)
*|S (VDD:2 10.0000 -10.0000)
*|I (XA:VDDA XA VDDA I 0 10.0000 10.0000)
*|I (XB:VDDB XB VDDB I 0 20.0000 0.0000)
*|I (XB:VDDB#2 XB VDDB I 0 20.0000 -10.0000)
Rt0 VDD VDD:1 0.750000 $metal1 $L=10 $W=2.000000 $LLX=0 $LLY=-1 $URX=10 $URY=1 $lvl=1
Rt1 VDD:1 XA:VDDA 0.750000 $metal1 $L=10 $W=2.000000 $LLX=9 $LLY=0 $URX=11 $URY=10 $lvl=1
Rt2 VDD:1 XB:VDDB 0.750000 $metal1 $L=10 $W=2.000000 $LLX=10 $LLY=-1 $URX=20 $URY=1 $lvl=1
Rt3 VDD:1 VDD:2 0.750000 $metal1 $L=10 $W=2.000000 $LLX=9 $LLY=-10 $URX=11 $URY=0 $lvl=1
Rt4 VDD:2 XB:VDDB#2 0.750000 $metal1 $L=10 $W=2.000000 $LLX=10 $LLY=-11 $URX=20 $URY=-9 $lvl=1
.ENDS
"""


def many_nets_dspf(tmp, n) -> str:
    """`n` small nets: two resistors, one port and one instance pin each."""
    L = ['*|DSPF 1.3', '*|DESIGN "many"', '*|OPERATING_TEMPERATURE 100']
    for k in range(n):
        net = f"N{k}"
        L += [f"*|NET {net} 0", f"*|P ({net} I *1 0.0 {k}.0)",
              f"*|S ({net}:1 1.0 {k}.0)", f"*|I (M{k}:d M{k} d I 0 2.0 {k}.0)",
              f"R{k}a {net} {net}:1 1.0 $metal1 $L=5 $W=0.75",
              f"R{k}b {net}:1 M{k}:d 1.0 $metal1 $L=5 $W=0.75"]
    return write(os.path.join(str(tmp), f"many{n}.dspf"), "\n".join(L) + "\n.ENDS\n")


# ----------------------------------------------------------- CLI

def emir(*args, cwd=None, env=None):
    """`python -m emir ARGS`, captured."""
    return subprocess.run([sys.executable, "-m", "emir", *map(str, args)],
                          cwd=cwd or ROOT, capture_output=True, text=True, env=env)


# ----------------------------------------------------------- the magic route

#: the magic route's test cell and the outputs a magic with the geoout patch
#: wrote for it (tests/data/magic_tiny/README.md)
TINY = os.path.join(ROOT, "tests", "data", "magic_tiny")
TINY_OUTPUTS = ("tiny.ext", "tiny.res.ext", "tiny.res.geo", "tiny_pex.spice",
                "tiny_lumped.spice")

_FAKE_MAGIC = """#!{python}
\"\"\"A stand-in magic: `--version`, the geometry probe, and an extraction that
copies the committed outputs of tests/data/magic_tiny into place.\"\"\"
import os, shutil, sys
if "--version" in sys.argv:
    print("8.3.515")
    sys.exit(0)
script = open(sys.argv[-1]).read()
if "EMIR_PROBE" in script:
    print("EMIR_PROBE")
    print({answer!r})
    sys.exit(0)
for f in {files!r}:
    shutil.copy(os.path.join({tiny!r}, f), f)
print("exttospice finished.")
"""


def fake_magic(tmp, patched=True) -> str:
    """A stand-in magic in `tmp` (its path): it answers `extresist geoout`
    (`patched`) or does not, and its extraction copies tiny's outputs."""
    p = os.path.join(str(tmp), "magic-geo" if patched else "magic-stock")
    with open(p, "w", encoding="utf-8") as f:
        f.write(_FAKE_MAGIC.format(python=sys.executable, tiny=TINY, files=list(TINY_OUTPUTS),
                                   answer="on" if patched else "Unknown option: geoout"))
    os.chmod(p, 0o755)
    return p
