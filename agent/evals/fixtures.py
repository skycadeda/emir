"""The input files of the eval task cards, built from the repository's examples.

    python agent/evals/fixtures.py NAME OUT_DIR      # build one fixture into OUT_DIR
    python agent/evals/fixtures.py --list

Every fixture is synthetic or comes from the examples (invented circuits, the
bundled ILLUSTRATIVE sample, open-PDK process files): no design or foundry data.
A card names its fixtures (`fixtures: [sample]`); the runner builds them into
the workspace's `inputs/` before the agent starts, so the agent finds the
person's files where the prompt says they are.

Fixtures that need emir itself (a finished run) call the emir the
runner was given (`EMIR` below), never the workspace's copy.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
#: the emir command fixtures run (the runner sets it to the real one)
EMIR = [sys.executable, "-m", "emir"]

#: the sample's DC currents (selftest's): 4 mA in at VDD, 1 mA out of each source
SAMPLE_CURRENTS = '{"VDD": 0.004, "MP0:s": -0.001, "MP1:s": -0.001, "MP2:s": -0.001, "MP3:s": -0.001}'


def _src(*parts):
    return os.path.join(ROOT, *parts)


def _copy(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(src, dst)
    return dst


def _emir(*args, cwd, rc=0):
    """Run the real emir; raise with its output when it does not exit `rc`."""
    p = subprocess.run([*EMIR, *args], cwd=cwd, capture_output=True, text=True,
                       env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    if p.returncode != rc:
        raise RuntimeError(f"fixture: emir {' '.join(args)} exited {p.returncode}:\n"
                           f"{p.stdout[-2000:]}\n{p.stderr[-2000:]}")
    return p.stdout


# ------------------------------------------------------------------ the fixtures


def sample(out):
    """inputs/rail.dspf and inputs/process.yaml: the bundled sample (ILLUSTRATIVE limits)."""
    _copy(_src("emir", "data", "sample", "rail.dspf"), os.path.join(out, "rail.dspf"))
    _copy(_src("emir", "data", "sample", "process.yaml"), os.path.join(out, "process.yaml"))


#: a 10 um Metal1 strap, 0.5 um wide, and one Via1 cut down to the load, written
#: with gf180mcu's layer names (invented geometry; the limits come from pdks/gf180mcu.yaml)
GF180_STRAP = """*|DSPF 1.3
*|DESIGN "strap"
*|OPERATING_TEMPERATURE 125
*|GROUND_NET 0
*|NET VDD 1e-15
*|P (VDD I *1 0.0 0.0)
*|S (VDD:1 10.0 0.0)
*|I (M0:s M0 s I 0 10.0 0.0)
R1 VDD VDD:1 1.8 $metal1 $L=10.0 $W=0.5 $LLX=0 $LLY=-0.25 $URX=10 $URY=0.25
R2 VDD:1 M0:s 4.5 $via1 $VC=1 $LLX=9.87 $LLY=-0.13 $URX=10.13 $URY=0.13
.ENDS
"""


def gf180_strap(out):
    """inputs/strap.dspf: a gf180mcu Metal1 strap and one Via1 cut (synthetic)."""
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "strap.dspf"), "w", encoding="utf-8") as f:
        f.write(GF180_STRAP)


def xyce_loadrail(out):
    """inputs/: example 02's loadrail.dspf, its testbench and models, the sample process."""
    for f in ("loadrail.dspf", "models.sp", "tb_rcc.cir"):
        _copy(_src("examples", "02_xyce", f), os.path.join(out, f))
    _copy(_src("emir", "data", "sample", "process.yaml"), os.path.join(out, "process.yaml"))


def cadence_stub(out):
    """inputs/railblock.gds and .cdl (stand-ins: a dry run only checks they exist) and the
    sample's RC DSPF as inputs/rail.dspf."""
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "railblock.gds"), "w", encoding="utf-8") as f:
        f.write("stand-in for the layout of railblock (the dry run does not read it)\n")
    with open(os.path.join(out, "railblock.cdl"), "w", encoding="utf-8") as f:
        f.write(".SUBCKT railblock VDD\n.ENDS\n")
    _copy(_src("emir", "data", "sample", "rail.dspf"), os.path.join(out, "rail.dspf"))


def spectre_site(out):
    """inputs/rail.dspf, inputs/process.yaml (the sample) and inputs/tb.scs: the person's
    Spectre testbench, a stand-in (no Spectre runs in the evals)."""
    sample(out)
    with open(os.path.join(out, "tb.scs"), "w", encoding="utf-8") as f:
        f.write("// the person's Spectre testbench of railblock (a stand-in for the eval)\n"
                "simulator lang=spectre\n"
                'include "rail.dspf"\n'
                "XI0 (vdd) railblock\n"
                "VSUP (vdd 0) vsource dc=1.2\n"
                "tr tran stop=40n\n")


def finished_run(out):
    """inputs/run/summary.json: last night's run of the sample (DC currents), made here."""
    d = os.path.join(out, "run")
    os.makedirs(d, exist_ok=True)
    sample(d)
    _emir("run", "rail.dspf", "--process", "process.yaml", "--currents", SAMPLE_CURRENTS,
          "--summary-out", "summary.json", "--format", "json", cwd=d)


def sky130_tiny(out):
    """inputs/tiny.gds: the magic route's synthetic one-transistor SKY130 cell (top cell tiny)."""
    _copy(_src("tests", "data", "magic_tiny", "tiny.gds"), os.path.join(out, "tiny.gds"))


def encrypted_ict(out):
    """inputs/pdk.ict, the PDK's ICT as the foundry sent it: encrypted (a protected
    envelope around unreadable bytes; synthetic, made here), and inputs/rail.dspf, the
    bundled sample's DSPF. No other EM data is at hand."""
    import base64
    import hashlib
    _copy(_src("emir", "data", "sample", "rail.dspf"), os.path.join(out, "rail.dspf"))
    blob, h = b"", b"emir eval: not a real ICT"
    while len(blob) < 1536:
        h = hashlib.sha256(h).digest()
        blob += h
    lines = base64.b64encode(blob).decode()
    body = "\n".join(lines[i:i + 64] for i in range(0, len(lines), 64))
    with open(os.path.join(out, "pdk.ict"), "w", encoding="utf-8") as f:
        f.write("`pragma protect begin_protected\n"
                "`pragma protect version=1\n"
                "`pragma protect data_method=\"aes256-cbc\"\n"
                "`pragma protect encoding=(enctype=\"base64\")\n"
                "`pragma protect data_block\n" + body + "\n"
                "`pragma protect end_protected\n")


def transient_result(out):
    """inputs/rail.dspf, inputs/process.yaml (the sample) and inputs/result.tran: a
    transient of the sample rail with every branch current (synthetic, made here), its
    far load pulsed with a period the person has not given: a run without --period is
    refused (EMIR-E428), and EMIR never guesses the period."""
    import numpy as np
    sys.path.insert(0, ROOT)
    from emir.validate import _psf_tran
    sample(out)
    t = np.linspace(0.0, 12.5e-9, 2501)
    ph = ((t - 0.5e-9) / 4e-9) % 1.0
    x = ph / 0.45
    on = np.where((t >= 0.5e-9) & (x > 0) & (x < 1), 2e-3 * (1 - np.abs(2 * x - 1)), 0.0)
    i = [0 * t, 0 * t, 0 * t, on]
    sigs = {f"Rm{n}:1": sum(i[(n - 1) // 3:]) for n in range(1, 13)}
    sigs.update({f"Rc{k}:1": i[k] for k in range(4)})
    _psf_tran(os.path.join(out, "result.tran"), t, sigs)


FIXTURES = {"sample": sample, "encrypted_ict": encrypted_ict, "gf180_strap": gf180_strap,
            "xyce_loadrail": xyce_loadrail, "cadence_stub": cadence_stub,
            "finished_run": finished_run, "spectre_site": spectre_site,
            "sky130_tiny": sky130_tiny, "transient_result": transient_result}


def build(names, out, emir=None):
    """Build each named fixture into `out` (the workspace's inputs/)."""
    global EMIR
    if emir:
        EMIR = list(emir)
    os.makedirs(out, exist_ok=True)
    for n in names:
        if n not in FIXTURES:
            raise KeyError(f"unknown fixture {n!r}; fixtures.py --list")
        with contextlib.redirect_stdout(io.StringIO()):     # the generators' chatter
            FIXTURES[n](out)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], allow_abbrev=False)
    ap.add_argument("name", nargs="?")
    ap.add_argument("out", nargs="?")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args(argv)
    if a.list or not a.name:
        print(json.dumps({k: (v.__doc__ or "").split("\n")[0] for k, v in FIXTURES.items()},
                         indent=1))
        return 0
    build([a.name], a.out or ".")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
