"""rcc is exact for the simulated record over the whole periods you give
(--period): on examples 02 (a rail worked out by hand) and 06 (a 10 x 10 supply
mesh), every branch's avg, rms and peak, recomputed here straight from the
simulator's raw record (its own reader, numpy's trapezoid on the samples and
their squares, the window's ends interpolated; no EMIR helper), match EMIR's
to 1e-9 relative. And on example 06's mesh, --period 125p (the feed's
sub-period: each contact repeats only every 1 ns) is refused, EMIR-E429.

Xyce simulates each example once (a few seconds); skipped without Xyce."""
import json
import math
import os
import shutil
import subprocess
import sys

import numpy as np
import pytest
from _helpers import ROOT

pytestmark = [pytest.mark.example,
              pytest.mark.skipif(shutil.which("Xyce") is None, reason="Xyce is not on PATH")]

EX = os.path.join(ROOT, "examples")
#: example 02: --period 10 ns on a 40 ns record; 06: 1 ns on a 4 ns record
CASES = {"02": 10e-9, "06": 1e-9}


def raw(path) -> tuple:
    """(time, {name: values}) of an ASCII raw file (Xyce -a), read here."""
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    nvar = int(next(ln for ln in lines if ln.startswith("No. Variables:")).split(":")[1])
    k = lines.index("Variables:")
    names = [lines[k + 1 + j].split()[1] for j in range(nvar)]
    vals = [float(x) for ln in lines[lines.index("Values:") + 1:] for x in ln.split()]
    data = np.array(vals).reshape(-1, nvar + 1)[:, 1:]
    return data[:, 0], {n.upper(): data[:, j] for j, n in enumerate(names)}


def reference(t, y, t0, t1) -> dict:
    """avg, rms and peak of `y` over [t0, t1]: the ends interpolated, numpy's
    trapezoid on the samples and on their squares."""
    inner = (t > t0) & (t < t1)
    tt = np.concatenate([[t0], t[inner], [t1]])
    yy = np.concatenate([[np.interp(t0, t, y)], y[inner], [np.interp(t1, t, y)]])
    T = t1 - t0
    return {"avg": abs(np.trapezoid(yy, tt) / T), "rms": math.sqrt(np.trapezoid(yy * yy, tt) / T),
            "peak": float(np.abs(yy).max())}


@pytest.fixture(scope="module")
def results(tmp_path_factory):
    """Each example's DSPF, process file, raw result and names sidecar."""
    out = tmp_path_factory.mktemp("exact")
    env = {**os.environ, "PYTHONPATH": ROOT}
    d02 = out / "02"
    shutil.copytree(os.path.join(EX, "02_xyce"), d02,
                    ignore=shutil.ignore_patterns("emir_out", "expected"))
    subprocess.run([sys.executable, "-m", "emir", "simulate", "tb_rcc.cir", "--sim", "xyce",
                    "--dspf", "loadrail.dspf", "--out", str(d02 / "rcc.raw")], cwd=d02,
                   env=env, check=True, capture_output=True, timeout=120)
    d06 = out / "06"
    subprocess.run([sys.executable, "mesh.py", "xyce", "--n", "10", "--k", "32", "--out",
                    str(d06)], cwd=os.path.join(EX, "06_supply_mesh"), env=env, check=True,
                   capture_output=True, timeout=120)
    subprocess.run(["Xyce", "-a", "rcc.cir"], cwd=d06, check=True, capture_output=True,
                   timeout=120)
    return {"02": (d02 / "loadrail.dspf", os.path.join(ROOT, "emir", "data", "sample",
                                                        "process.yaml"), d02 / "rcc.raw"),
            "06": (d06 / "grid_rc.dspf", os.path.join(EX, "06_supply_mesh",
                                                      "process_screen.yaml"), d06 / "rcc.raw")}


@pytest.mark.parametrize("case", sorted(CASES))
def test_every_branch_is_the_record_s_own_over_the_last_whole_periods(results, case):
    from emir.dspf import parse
    from emir.engine import Process
    from emir.errors import collecting
    from emir.rcc import rcc_check, reference_node
    dspf, proc, res = results[case]
    P = CASES[case]
    t, sig = raw(res)
    side = json.load(open(f"{res}.names", encoding="utf-8"))["map"]
    d = parse(str(dspf))
    n_checked = 0
    for net in (n for n in sorted(d.nets) if d.net_resistors(n)):
        with collecting():
            r = rcc_check(d, net, Process.load(proc), str(res), reference_node(d, net),
                          period=P, log=lambda *a, **k: None)
        # the window: the record's last whole periods, up to three
        n = int(math.floor((t[-1] - t[0]) / P + 1e-9))
        t0 = t[-1] - min(n, 3) * P
        assert r["window"]["t0"] == pytest.approx(t0, rel=1e-12)
        assert r["window"]["t1"] == t[-1] and r["window"]["n_periods"] == min(n, 3) == 3
        names = [x["name"] for x in r["network"].res]
        for name, (branch, sign) in side.items():
            if branch[:-2] not in names:
                continue
            i = names.index(branch[:-2])
            ref = reference(t, sign * sig[name.upper()], t0, t[-1])
            for m in ("avg", "rms", "peak"):
                got = float(r["Ie"][m][i])
                assert got == pytest.approx(ref[m], rel=1e-9, abs=1e-18), (case, net, branch, m)
            n_checked += 1
    assert n_checked == len(side)


def test_a_sub_period_of_the_mesh_is_refused(results):
    """The mesh's feed repeats every 125 ps (eight inverter phases), each
    contact only every 1 ns: over --period 125p the record's last two periods
    differ on the contacts, so the run is refused with no number."""
    dspf, proc, res = results["06"]
    p = subprocess.run([sys.executable, "-m", "emir", "run", str(dspf), "--process", proc,
                        "--net", "VDD", "--cc-sim", str(res), "--sim", "xyce", "--period",
                        "125p", "--format", "json"], capture_output=True, text=True,
                       env={**os.environ, "PYTHONPATH": ROOT}, timeout=300)
    d = json.loads(p.stdout)
    assert p.returncode == 2 and d["code"] == "EMIR-E429", p.stdout[-2000:]
    assert d["details"]["change"] > 0.02 and d["details"]["branch"]
    assert "worst_items" not in p.stdout and "verdict" not in p.stdout
