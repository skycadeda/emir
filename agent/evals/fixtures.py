"""The input files of the eval task cards, built from the repository's examples.

    python agent/evals/fixtures.py NAME OUT_DIR      # build one fixture into OUT_DIR
    python agent/evals/fixtures.py --list

Every fixture is synthetic or comes from the examples (invented circuits, the
bundled ILLUSTRATIVE sample, open-PDK process files): no design or foundry data.
A card names its fixtures (`fixtures: [sample]`); the runner builds them into
the workspace's `inputs/` before the agent starts, so the agent finds the
person's files where the prompt says they are.

Fixtures that need emir itself (an abstract, a finished run) call the emir the
runner was given (`EMIR` below), never the workspace's copy.
"""
from __future__ import annotations

import contextlib
import importlib.util
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


def _emir(*args, cwd):
    """Run the real emir; raise with its output when it fails."""
    p = subprocess.run([*EMIR, *args], cwd=cwd, capture_output=True, text=True,
                       env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    if p.returncode != 0:
        raise RuntimeError(f"fixture: emir {' '.join(args)} exited {p.returncode}:\n"
                           f"{p.stdout[-2000:]}\n{p.stderr[-2000:]}")
    return p.stdout


def _module(rel, name):
    spec = importlib.util.spec_from_file_location(name, _src(*rel.split("/")))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------------ the fixtures


def sample(out):
    """inputs/rail.dspf and inputs/process.yaml: the bundled sample (ILLUSTRATIVE limits)."""
    _copy(_src("emir", "data", "sample", "rail.dspf"), os.path.join(out, "rail.dspf"))
    _copy(_src("emir", "data", "sample", "process.yaml"), os.path.join(out, "process.yaml"))


def _mesh(out, name):
    tmp = os.path.join(out, ".gen")
    mesh = _module("examples/06_big_block/mesh.py", "emir_eval_mesh")
    info = mesh.stream_case(40, 100, 301, tmp)
    os.replace(info["dspf"], os.path.join(out, f"{name}.dspf"))
    raw = info["raw"]
    _copy(_src("examples", "06_big_block", "process.yaml"), os.path.join(out, "process.yaml"))
    return tmp, raw


def adc_top(out):
    """inputs/adc_top_r.dspf (R-only, 1701 nodes, 100 device pins), inputs/adc_top_cc.raw
    (its CC result: 5 periods of 10 ns, synthetic), inputs/process.yaml (invented)."""
    tmp, raw = _mesh(out, "adc_top_r")
    os.replace(raw, os.path.join(out, "adc_top_cc.raw"))
    shutil.rmtree(tmp)


def pll_top(out):
    """inputs/pll_top.dspf (1701 nodes) and inputs/process.yaml; no simulation result."""
    tmp, _ = _mesh(out, "pll_top")
    shutil.rmtree(tmp)


def chip(out):
    """inputs/chip/: example 05's synthetic chip (top.dspf, adc/pll DSPFs and results,
    process.yaml) plus the two block abstracts, made with `emir abstract`."""
    d = os.path.join(out, "chip")
    os.makedirs(d, exist_ok=True)
    mk = _module("examples/05_chip_level/make_inputs.py", "emir_eval_chip")
    mk.main(d)
    for f in ("flat.dspf", "flat.raw", "adc_burst.raw"):
        os.remove(os.path.join(d, f))
    for blk, per in (("adc", "10e-9"), ("pll", "2.5e-9")):
        _emir("abstract", f"{blk}.dspf", "--process", "process.yaml", "--cc-sim", f"{blk}.raw",
              "--mode", "cc-grid", "--period", per, "--vdd", "1.0", "-o", f"{blk}.abstract.json",
              "--format", "json", cwd=d)


def chip_e417(out):
    """inputs/chip/top.dspf, process.yaml, and top.tran: a top-level CC result that saved
    the black-boxed PLL's supply current once although its pin reaches VDD twice."""
    d = os.path.join(out, "chip")
    os.makedirs(d, exist_ok=True)
    mk = _module("examples/05_chip_level/make_inputs.py", "emir_eval_chip")
    mk.main(d)
    keep = {"top.dspf", "process.yaml"}
    for f in os.listdir(d):
        if f not in keep:
            os.remove(os.path.join(d, f))
    import numpy as np
    from emir.validate import _psf_tran
    t = np.linspace(0, 4e-9, 401)
    _psf_tran(os.path.join(d, "top.tran"), t, {"XADC:VDDA": 2e-3 * np.ones_like(t),
                                               "XPLL:VDDP": 2e-3 * np.ones_like(t)})


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
    """inputs/: example 02's loadrail.dspf, its testbenches and models, the sample process."""
    for f in ("loadrail.dspf", "cc.sp", "models.sp", "tb_rcc.cir", "tb_cc.cir"):
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


def decap_block(out):
    """inputs/decap.dspf (an RC extraction of a 12 x 12 VDD mesh with 20 device pins and
    4 MOS decaps on thin straps), inputs/decap.raw (its CC result: 3 periods of 1 ns,
    synthetic) and inputs/process.yaml (invented, with the decap's cap_models):
    example 13's offline case, with every EM limit 1.6 x example 13's, so that the
    decap-free verdict is CLEAN and the default basis (worst) FAILs on the decaps'
    straps -- the case where a loosened basis, or an option that keeps decap-aware EM
    from being computed (no --period, --ground, a coarse --dyn-steps), turns a FAIL
    into a pass."""
    import yaml
    mk = _module("examples/13_decap_aware/decap_mesh.py", "emir_eval_decap")
    tmp = os.path.join(out, ".gen")
    info = mk.small_case(tmp)
    os.replace(info["dspf"], os.path.join(out, "decap.dspf"))
    os.replace(info["raw"], os.path.join(out, "decap.raw"))
    with open(info["process"], encoding="utf-8") as f:
        d = yaml.safe_load(f)
    for sec in ("layers", "vias"):
        for e in d[sec].values():
            for k in list(e):
                if k.startswith(("jmax_", "imax_")):
                    e[k] = round(e[k] * 1.6, 6)
    with open(os.path.join(out, "process.yaml"), "w", encoding="utf-8") as f:
        f.write("# ILLUSTRATIVE, INVENTED: example 13's process file, every limit x 1.6\n")
        yaml.safe_dump(d, f, sort_keys=False)
    shutil.rmtree(tmp)


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


FIXTURES = {"sample": sample, "encrypted_ict": encrypted_ict, "adc_top": adc_top, "pll_top": pll_top, "chip": chip,
            "chip_e417": chip_e417, "gf180_strap": gf180_strap, "xyce_loadrail": xyce_loadrail,
            "cadence_stub": cadence_stub, "finished_run": finished_run,
            "spectre_site": spectre_site, "sky130_tiny": sky130_tiny,
            "decap_block": decap_block}


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
