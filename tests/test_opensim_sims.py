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
    for f in ("loadrail.dspf", "models.sp", "cc.sp", "tb_cc.cir", "tb_rcc.cir"):
        shutil.copy(os.path.join(EX, f), tmp_path / f)
    return tmp_path


def simulate(ex, sim, tb, *extra, out="r.raw", rc=0):
    return jdoc(emir("simulate", ex / tb, "--sim", sim, "--dspf", ex / "loadrail.dspf",
                     "--out", ex / "sim" / out, *extra, "--format", "json", cwd=ex), rc=rc)


def screen(ex, raw, mode):
    d = jdoc(emir("run", ex / "loadrail.dspf", "--process", PROC, "--cc-sim", raw,
                  "--mode", mode, "--period", "1e-8", "--em-temp", "100", "--format", "json",
                  cwd=ex))
    n = d["nets"][0]
    items = {(i["name"], i["metric"]): i["ratio"] for i in n["worst_items"]}
    return n, items


@pytest.mark.parametrize("sim", SIMS)
@pytest.mark.parametrize("tb, extra, mode", [("tb_rcc.cir", (), "rcc"),
                                             ("tb_cc.cir", ("--probe",), "cc-grid")])
def test_the_example_matches_its_closed_forms(ex, sim, tb, extra, mode):
    s = simulate(ex, sim, tb, "--mode", mode, *extra)
    assert s["missing"] == [] and s["found"] == s["signals"]
    assert s["mode"] == mode
    assert os.path.isfile(s["names_sidecar"]) and s["names_sidecar"] == s["result"] + ".names"
    n, items = screen(ex, s["result"], mode)
    assert n["mode"] == mode and n["verdict"] == "FAIL"
    assert items[("Rc3", "peak")] == pytest.approx(PEAK_RC3, rel=1e-6)
    # the trapezoid rule over the 0.1 ns ramps, sampled every 10 ps, reads the
    # average 0.03 % high (README.md); 0.1 % covers it
    assert items[("Rm1", "avg")] == pytest.approx(AVG_RM1, rel=1e-3)
    assert n["ir_drop_mV"] == pytest.approx(IR_MV, rel=1e-3)
    if mode == "rcc":
        # only XMN3 on: 2.5 mA through 3 x 4 ohm of rail and the 24 ohm contact
        assert n["dynamic_mV"] == pytest.approx(2.5 * 36, rel=1e-3)


@pytest.mark.parametrize("sim", SIMS)
def test_the_next_steps_of_simulate_run_as_printed(ex, sim):
    s = jdoc(emir("simulate", "tb_rcc.cir", "--sim", sim, "--dspf", "loadrail.dspf",
                  "--mode", "rcc", "--process", PROC, "--format", "json", cwd=ex))
    assert s["result"] == str(ex / "emir_out" / "sim" / "result.raw")
    for step in s["next_steps"]:
        p = emir(*__import__("shlex").split(step["command"])[1:], cwd=ex)
        assert p.returncode == 0, (step, p.stderr[-1500:])
    assert (ex / "emir_out" / "summary.json").is_file()


@pytest.mark.parametrize("sim", SIMS)
@pytest.mark.parametrize("mode", ["rcc", "cc-grid"])
def test_a_block_two_levels_down_is_found_and_named(ex, sim, mode):
    """The block under XTOP.XI0: Xyce names it XTOP:XI0:..., ngspice
    r.xtop.xi0.rm1 / v.xtop.xi0.vemir0 -- every current is found."""
    tb = (ex / f"tb_{'rcc' if mode == 'rcc' else 'cc'}.cir").read_text()
    tb = tb.replace("XI0 vdd 0 g0 g1 g2 g3 loadrail",
                    ".subckt wrap a b c d e f\nXI0 a b c d e f loadrail\n.ends wrap\n"
                    "XTOP vdd 0 g0 g1 g2 g3 wrap")
    write(ex / "tb2.cir", tb)
    s = simulate(ex, sim, "tb2.cir", "--mode", mode, *(("--probe",) if mode != "rcc" else ()))
    assert s["path"] == ["XTOP", "XI0"] and s["missing"] == [] and s["found"] > 0
    n, items = screen(ex, s["result"], mode)
    assert items[("Rc3", "peak")] == pytest.approx(PEAK_RC3, rel=1e-6)


@pytest.mark.parametrize("sim", SIMS)
def test_direct_leads_and_probes_read_the_same_currents(ex, sim):
    """Plain MOSFETs (M...) are printed directly by Xyce (ID(...)); the same
    devices wrapped in subcircuits are read through probes. The probes add
    unknowns, so the time steps differ a little (3.5e-6 relative on Xyce); a
    wrong name or sign would be off by tens of percent. ngspice's @m[id]
    carries the model's polarity, so ngspice reads MOS pins through probes
    only: the direct route is refused (EMIR-E460) with --probe as the fix."""
    dspf = (ex / "loadrail.dspf").read_text().replace("XMN", "MMN")
    lines = []
    for ln in dspf.splitlines():
        if ln.startswith("MMN"):                     # the device: a plain MOSFET now
            ln = ln.replace(" nsink ", " nlv ")
        lines.append(ln)
    write(ex / "loadrail.dspf", "\n".join(lines) + "\n")
    cc = (ex / "cc.sp").read_text().replace("XMN", "MMN").replace(" nsink ", " nlv ")
    write(ex / "cc.sp", cc)
    if sim == "ngspice":
        d = simulate(ex, sim, "tb_cc.cir", "--mode", "cc-grid", out="d.raw", rc=2)
        assert d["code"] == "EMIR-E460" and "polarity" in d["message"]
        assert d["next_steps"][0]["command"].endswith("--probe")
        return
    direct = simulate(ex, sim, "tb_cc.cir", "--mode", "cc-grid", out="d.raw")
    assert direct["direct"] == 4 and direct["probed"] == 0 and direct["missing"] == []
    probed = simulate(ex, sim, "tb_cc.cir", "--mode", "cc-grid", "--probe", out="p.raw")
    assert probed["probed"] == 4
    _, a = screen(ex, direct["result"], "cc-grid")
    _, b = screen(ex, probed["result"], "cc-grid")
    assert a.keys() == b.keys()
    for k in a:
        assert a[k] == pytest.approx(b[k], rel=1e-4), k


@pytest.mark.parametrize("sim", SIMS)
def test_cc_on_the_rc_netlist_probes_the_instance_pin_nodes(ex, sim):
    s = simulate(ex, sim, "tb_rcc.cir", "--mode", "cc-grid", "--probe")
    assert s["probed"] == 4 and s["missing"] == []
    _, items = screen(ex, s["result"], "cc-grid")
    assert items[("Rc3", "peak")] == pytest.approx(PEAK_RC3, rel=1e-6)


@pytest.mark.parametrize("sim", SIMS)
def test_a_failed_or_slow_simulation_is_coded(ex, sim):
    write(ex / "models.sp", (ex / "models.sp").read_text().replace("nlv NMOS", "nlv NOSUCH"))
    d = simulate(ex, sim, "tb_rcc.cir", "--mode", "rcc", rc=2)
    assert d["code"] in ("EMIR-E602", "EMIR-E661")
    assert d["next_steps"][0]["command"].startswith("tail -n 40 ")
    shutil.copy(os.path.join(EX, "models.sp"), ex / "models.sp")
    d = simulate(ex, sim, "tb_rcc.cir", "--mode", "rcc", "--timeout", "0.0001", rc=2)
    assert d["code"] == "EMIR-E605"
    # a failed run leaves no result that could pass for this one
    assert not (ex / "sim" / "r.raw").exists()


@pytest.mark.slow
@pytest.mark.skipif(site.tool("xyce")[0] is None or site.tool("ngspice")[0] is None,
                    reason="needs both Xyce and ngspice")
def test_the_proof_script_passes(tmp_path):
    """scripts/opensim_proof.py: Xyce and ngspice, cc-grid and rcc, against the
    closed forms (the numbers docs/open-source-flow.md quotes)."""
    import subprocess
    import sys
    p = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "opensim_proof.py"),
                        "--out", str(tmp_path)], capture_output=True, text=True)
    assert p.returncode == 0, p.stdout[-3000:] + p.stderr[-2000:]
    res = json.load(open(tmp_path / "proof.json"))
    assert all(c["pass"] for c in res["checks"]) and len(res["checks"]) == 18
    inv = res["cases"]["inverter"]["runs"]
    for sim in ("xyce", "ngspice"):
        cc, rc = inv[f"{sim}/cc"]["ratios"], inv[f"{sim}/rcc"]["ratios"]
        # the averages agree (the same charge per period); the CC netlist's
        # ideal rail gives sharper, higher spikes: cc-grid is the higher one
        assert cc["Rm1:avg"] == pytest.approx(rc["Rm1:avg"], rel=0.01)
        assert cc["Rc3:peak"] > rc["Rc3:peak"] and cc["Rm1:rms"] > rc["Rm1:rms"]


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
    as the testbench's .spiceinit. The currents depend on the drain voltage, so
    cc-grid (full rail) reads a little above rcc."""
    import subprocess
    import sys
    p = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "opensim_proof.py"),
                        "--cases", "sky130", "--sims", "ngspice", "--out", str(tmp_path)],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stdout[-3000:] + p.stderr[-2000:]
    runs = json.load(open(tmp_path / "proof.json"))["cases"]["sky130"]["runs"]
    cc, rc = runs["ngspice/cc"], runs["ngspice/rcc"]
    assert cc["verdict"] == rc["verdict"] == "FAIL"
    for k in ("Rm1:avg", "Rc3:peak"):
        assert 1.0 < cc["ratios"][k] / rc["ratios"][k] < 1.05, k
    assert 1.0 < cc["ir_mV"] / rc["ir_mV"] < 1.05


@pytest.mark.parametrize("sim", SIMS)
def test_the_next_step_of_probe_runs_as_printed(ex, sim, monkeypatch):
    """`emir probe ... --dspf --names-out` suggests the simulate command that
    reads the probes (with the simulator `emir info` finds first)."""
    import shlex
    env = {**os.environ}
    if sim == "ngspice":
        env["XYCE_BIN"] = "/nonexistent/Xyce"               # so ngspice is the one found
    d = jdoc(emir("probe", ex / "tb_cc.cir", "-o", ex / "p" / "tb.probed.cir", "--dspf",
                  ex / "loadrail.dspf", "--net", "VDD", "--names-out", ex / "p" / "probes.names",
                  "--format", "json", cwd=ex, env=env))
    step = d["next_steps"][0]["command"]
    assert f"--sim {sim}" in step and "--probes" in step
    p = emir(*shlex.split(step)[1:], cwd=ex, env=env)
    assert p.returncode == 0, p.stdout[-1500:] + p.stderr[-1500:]
    s = json.loads(p.stdout)
    assert s["probed"] == 4 and s["missing"] == []
