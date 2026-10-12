"""The open-simulator path with real simulators: `emir simulate` runs Xyce and
ngspice on the loadrail example (examples/02_xyce) and `emir run` reads the
results. Each test is skipped when its simulator is not installed (CI's core
job has neither). One simulator process at a time.

The numbers checked are the example's closed forms (examples/02_xyce/
README.md): four NMOS sinks, I_on = 25 uA * W/L = 2.0, 2.5, 3.0, 2.5 mA, each
conducting for one staggered 2 ns pulse (0.1 ns ramps) every 10 ns, so each
draws I_on * (2 + 0.2/6) / 10 on average."""
import json
import os
import shutil

import jsonschema
import pytest
from _helpers import PROC, ROOT, emir, write

from emir import schemas, site

EX = os.path.join(ROOT, "examples", "02_xyce")
SIMS = [pytest.param(s, marks=pytest.mark.skipif(site.tool(s)[0] is None,
                                                 reason=f"{s} not installed"))
        for s in ("xyce", "ngspice")]

#: the closed forms (see the module doc)
F = (2.0 + 0.2 / 6) / 10.0                    # average / on-current of every sink
I_ON = (2.0, 2.5, 3.0, 2.5)                   # mA
AVG_RM1 = sum(I_ON) * F / (3.2 * 0.75)        # Rm1 carries all four; limit 2.4 mA
PEAK_RC3 = 2.5 / 2.4                          # the 1-cut contact of the 2.5 mA sink
IR_MV = F * (3 * (10 + 8 + 5.5 + 2.5) + 24 * 2.5)   # to XMN3:d, average currents


def jdoc(p, rc=0):
    assert p.returncode == rc, (p.returncode, p.stdout[-1500:], p.stderr[-2500:])
    d = json.loads(p.stdout)
    name = d["schema"].split(".", 1)[1].split("/")[0]
    jsonschema.Draft202012Validator(schemas.get(name)).validate(d)
    return d


@pytest.fixture
def ex(tmp_path):
    for f in ("loadrail.dspf", "models.sp", "tb_rcc.cir"):
        shutil.copy(os.path.join(EX, f), tmp_path / f)
    return tmp_path


def simulate(ex, sim, tb, *extra, out="r.raw", rc=0):
    return jdoc(emir("simulate", ex / tb, "--sim", sim, "--dspf", ex / "loadrail.dspf",
                     "--out", ex / "sim" / out, *extra, "--format", "json", cwd=ex), rc=rc)


def screen(ex, raw):
    d = jdoc(emir("run", ex / "loadrail.dspf", "--process", PROC, "--cc-sim", raw,
                  "--period", "1e-8", "--em-temp", "100", "--format", "json", cwd=ex))
    n = d["nets"][0]
    items = {(i["name"], i["metric"]): i["ratio"] for i in n["worst_items"]}
    return n, items


@pytest.mark.parametrize("sim", SIMS)
def test_the_example_matches_its_closed_forms(ex, sim):
    s = simulate(ex, sim, "tb_rcc.cir")
    assert s["missing"] == [] and s["found"] == s["signals"]
    assert s["mode"] == "rcc"
    assert os.path.isfile(s["names_sidecar"]) and s["names_sidecar"] == s["result"] + ".names"
    n, items = screen(ex, s["result"])
    assert n["mode"] == "rcc" and n["verdict"] == "FAIL"
    assert items[("Rc3", "peak")] == pytest.approx(PEAK_RC3, rel=1e-6)
    # the trapezoid rule over the 0.1 ns ramps, sampled every 10 ps, reads the
    # average 0.03 % high (README.md); 0.1 % covers it
    assert items[("Rm1", "avg")] == pytest.approx(AVG_RM1, rel=1e-3)
    assert n["ir_drop_mV"] == pytest.approx(IR_MV, rel=1e-3)
    # only XMN3 on: 2.5 mA through 3 x 4 ohm of rail and the 24 ohm contact
    assert n["dynamic_mV"] == pytest.approx(2.5 * 36, rel=1e-3)


@pytest.mark.parametrize("sim", SIMS)
def test_the_next_steps_of_simulate_run_as_printed(ex, sim):
    """A transient needs the block's period (EMIR-E428 without it), which
    `emir simulate` does not know: its next step is `emir next` with the
    result, which asks for it and plans no run; with the person's period,
    `emir next` plans check, run and report, and they run as printed over the
    record's last three 10 ns periods."""
    import shlex
    s = jdoc(emir("simulate", "tb_rcc.cir", "--sim", sim, "--dspf", "loadrail.dspf",
                  "--process", PROC, "--format", "json", cwd=ex))
    assert s["result"] == str(ex / "emir_out" / "sim" / "result.raw")
    steps = s["next_steps"]
    assert [shlex.split(x["command"])[1] for x in steps] == ["next"]
    plan = jdoc(emir(*shlex.split(steps[0]["command"])[1:], cwd=ex))
    assert any("period" in m["what"] for m in plan["missing"])
    assert not [st for st in plan["steps"] if st["id"] in ("check", "run")]
    plan = jdoc(emir("next", "--dspf", "loadrail.dspf", "--process", PROC, "--cc-sim",
                     s["result"], "--period", "10ns", "--format", "json", cwd=ex))
    assert not plan["missing"]
    todo = [st for st in plan["steps"] if st["id"] in ("check", "run", "report")]
    assert [st["id"] for st in todo] == ["check", "run", "report"]
    assert all("--period" in st["command"] for st in todo if st["id"] in ("check", "run"))
    for st in todo:
        p = emir(*shlex.split(st["command"])[1:], cwd=ex)
        assert p.returncode == 0, (st, p.stderr[-1500:])
    win = json.loads((ex / "emir_out" / "summary.json").read_text())["nets"][0]["window"]
    assert win["period_s"] == pytest.approx(1e-8) and win["n_periods"] == 3
    assert win["t0_s"] == pytest.approx(1e-8) and win["t1_s"] == pytest.approx(4e-8)


@pytest.mark.parametrize("sim", SIMS)
def test_a_block_two_levels_down_is_found_and_named(ex, sim):
    """The block under XTOP.XI0: Xyce names it XTOP:XI0:..., ngspice
    r.xtop.xi0.rm1 -- every current is found."""
    tb = (ex / "tb_rcc.cir").read_text()
    tb = tb.replace("XI0 vdd 0 g0 g1 g2 g3 loadrail",
                    ".subckt wrap a b c d e f\nXI0 a b c d e f loadrail\n.ends wrap\n"
                    "XTOP vdd 0 g0 g1 g2 g3 wrap")
    write(ex / "tb2.cir", tb)
    s = simulate(ex, sim, "tb2.cir")
    assert s["path"] == ["XTOP", "XI0"] and s["missing"] == [] and s["found"] == 16
    n, items = screen(ex, s["result"])
    assert items[("Rm1", "avg")] == pytest.approx(AVG_RM1, rel=1e-3)


@pytest.mark.parametrize("sim", SIMS)
def test_a_failed_or_slow_simulation_is_coded(ex, sim):
    write(ex / "models.sp", (ex / "models.sp").read_text().replace("nlv NMOS", "nlv NOSUCH"))
    d = simulate(ex, sim, "tb_rcc.cir", rc=2)
    assert d["code"] in ("EMIR-E602", "EMIR-E661")
    assert d["next_steps"][0]["command"].startswith("tail -n 40 ")
    shutil.copy(os.path.join(EX, "models.sp"), ex / "models.sp")
    d = simulate(ex, sim, "tb_rcc.cir", "--timeout", "0.0001", rc=2)
    assert d["code"] == "EMIR-E605"
    # a failed run leaves no result that could pass for this one
    assert not (ex / "sim" / "r.raw").exists()


@pytest.mark.slow
@pytest.mark.skipif(site.tool("xyce")[0] is None or site.tool("ngspice")[0] is None,
                    reason="needs both Xyce and ngspice")
def test_the_proof_script_passes(tmp_path):
    """scripts/opensim_proof.py: Xyce and ngspice, rcc, against the closed
    forms and each other (the numbers docs/simulators.md quotes)."""
    import subprocess
    import sys
    p = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "opensim_proof.py"),
                        "--out", str(tmp_path)], capture_output=True, text=True)
    assert p.returncode == 0, p.stdout[-3000:] + p.stderr[-2000:]
    res = json.load(open(tmp_path / "proof.json"))
    # dc and pulse: each simulator against the hand calculation, and the two
    # against each other; inverter (no closed form): the two simulators
    assert all(c["pass"] for c in res["checks"]) and len(res["checks"]) == 7
    inv = res["cases"]["inverter"]["runs"]
    for sim in ("xyce", "ngspice"):
        assert {"Rm1:avg", "Rm1:rms", "Rc3:peak"} <= set(inv[f"{sim}/rcc"]["ratios"])


def _sky130():
    root = os.environ.get("PDK_ROOT")
    return bool(root) and os.path.isfile(os.path.join(
        root, "sky130A", "libs.tech", "ngspice", "sky130.lib.spice"))


@pytest.mark.slow
@pytest.mark.skipif(site.tool("ngspice")[0] is None or not _sky130(),
                    reason="needs ngspice and the sky130 PDK under $PDK_ROOT")
def test_the_sky130_case_runs_on_the_pdks_own_models(tmp_path):
    """Open models: the sinks as sky130_fd_pr__nfet_01v8, a subcircuit defined
    only in the PDK's .lib (the DSPF settles the pin names), the PDK's spinit
    as the testbench's .spiceinit; every branch current is read."""
    import subprocess
    import sys
    p = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "opensim_proof.py"),
                        "--cases", "sky130", "--sims", "ngspice", "--out", str(tmp_path)],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stdout[-3000:] + p.stderr[-2000:]
    runs = json.load(open(tmp_path / "proof.json"))["cases"]["sky130"]["runs"]
    rc = runs["ngspice/rcc"]
    assert rc["verdict"] == "FAIL"
    assert rc["ratios"]["Rm1:avg"] > rc["ratios"]["Rm12:avg"] > 0 and rc["ir_mV"] > 0
