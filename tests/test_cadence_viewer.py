"""The Cadence-facing behaviour that runs here: the viewer data (bounded on a
big run, the numbers the live test expects), `emir save-lines` with a prefix
and `--out`, `emir gui-install`'s site stub and launcher, `bin/emir` dropping
a parent's Python environment, and the `emir next` rules of the Cadence flow."""
import json
import os
import subprocess
import sys
import time

import jsonschema
import pytest
from _helpers import CUR, DSPF, PROC, ROOT, emir, sample_text, write

from emir import plan as P
from emir import schemas
from emir.layoutview import VIEWER_MAX, trim


def validate(doc):
    name = doc["schema"].split(".", 1)[1].split("/")[0]
    jsonschema.Draft202012Validator(schemas.get(name)).validate(doc)
    return doc


def jrun(*args, rc=0, env=None):
    p = emir(*args, "--format", "json", env=env)
    assert p.returncode == rc, (p.returncode, p.stdout[-1500:], p.stderr[-1500:])
    return validate(json.loads(p.stdout)), p


# ------------------------------------------------------------ the live test's numbers


def test_the_sample_gives_the_numbers_the_live_test_expects(tmp_path):
    """agent/cadence/LIVE_TEST.md expects 10 EM rows at the Explorer's default
    80 % (avg metric) and 16 IR rows on the rails, and quotes these fixes."""
    d, _ = jrun("run", DSPF, "--net", "VDD", "--process", PROC, "--currents", CUR,
                "--viewer", tmp_path / "v", "--lib", "L")
    viz = json.load(open(tmp_path / "v" / "emir_viz.json"))
    em, ir = viz["em"], viz["ir"]
    assert len(em) == 16
    assert sum(1 for v in em if v["ratios"]["avg"] >= 0.8) == 10
    assert viz["rails"] == ["VDD"] and sum(1 for r in ir if r["net"] in viz["rails"]) == 16
    hints = {v["name"]: v["hint"] for v in em}
    assert hints["Rm1"] == "Metal1: widen 0.750 -> 1.255 um (x1.67)"
    assert hints["Rc3"] == "Cont: 1 -> 2 cuts (+1)"
    assert not any(x["code"] == "EMIR-I780" for x in d["notices"])     # nothing trimmed


# ------------------------------------------------------------ bounded viewer data


def chain_dspf(tmp, n) -> str:
    """One net, `n` series resistors from the port to one instance pin at
    the far end (every resistor carries the same current)."""
    L = ['*|DSPF 1.3', '*|DESIGN "chain"', '*|OPERATING_TEMPERATURE 100',
         '*|NET VDD 0', '*|P (VDD I *1 0.0 0.0)']
    for k in range(1, n):
        L.append(f"*|S (VDD:{k} {k * 0.1:.1f} 0.0)")
    L.append(f"*|I (M0:s M0 s I 0 {n * 0.1:.1f} 0.0)")
    nodes = ["VDD"] + [f"VDD:{k}" for k in range(1, n)] + ["M0:s"]
    for k in range(n):
        L.append(f"Rm{k} {nodes[k]} {nodes[k + 1]} 0.01 $metal1 $L=0.1 $W=0.75")
    return write(os.path.join(str(tmp), f"chain{n}.dspf"), "\n".join(L) + "\n.ENDS\n")


def test_trim_keeps_the_worst_and_says_so():
    em = [{"net": "A", "ratio": r} for r in (0.1, 0.9, 0.4, 1.5, 0.6)]
    ir = [{"net": n, "mv": mv} for n, mv in (("VDD", 1.0), ("SIG", 9.0), ("VDD", 3.0),
                                             ("VDD", 2.0))]
    e, i, info = trim(em, ir, ["VDD"], cap=2)
    assert [v["ratio"] for v in e] == [1.5, 0.9]
    assert [r["mv"] for r in i] == [3.0, 2.0]                    # rails first, by drop
    assert info == {"cap": 2, "em_total": 5, "em_kept": 2, "ir_total": 4, "ir_kept": 2}
    assert trim(em, ir, ["VDD"], cap=10) == (em, ir, None)      # below the cap: untouched


@pytest.mark.slow
@pytest.mark.parametrize("n", [50_000])
def test_a_50k_resistor_net_gives_bounded_viewer_data(tmp_path, n):
    """Measured here: the viewer data of a 50 000-resistor net, trimmed by
    default and complete with --viewer-all (sizes and times in the report)."""
    dspf = chain_dspf(tmp_path, n)
    cur = json.dumps({"VDD": 0.001, "M0:s": -0.001})
    t0 = time.time()
    d, p = jrun("run", dspf, "--net", "VDD", "--process", PROC, "--currents", cur,
                "--viewer", tmp_path / "v", "--lib", "L", "--no-check")
    t_trim = time.time() - t0
    note = next(x["message"] for x in d["notices"] if x["code"] == "EMIR-I780")
    assert f"{n} EM segments -> 0" in note and f"{n} IR segments -> {VIEWER_MAX}" in note
    assert "EMIR-I780" in p.stderr
    viz = json.load(open(tmp_path / "v" / "emir_viz.json"))
    assert len(viz["em"]) == 0 and len(viz["ir"]) == VIEWER_MAX   # every segment at 0.1x
    data = tmp_path / "v" / "emir_data.il"
    size = data.stat().st_size
    assert size < 4_000_000                                     # bounded: IR cap x ~100 B
    t0 = time.time()
    d2, _ = jrun("run", dspf, "--net", "VDD", "--process", PROC, "--currents", cur,
                 "--viewer", tmp_path / "all", "--lib", "L", "--no-check", "--viewer-all")
    t_all = time.time() - t0
    assert not any(x["code"] == "EMIR-I780" for x in d2["notices"])
    assert len(json.load(open(tmp_path / "all" / "emir_viz.json"))["em"]) == n
    size_all = (tmp_path / "all" / "emir_data.il").stat().st_size
    assert size_all > 2 * size
    print(f"\n50k-resistor net: emir_data.il {size} B trimmed ({t_trim:.1f} s), "
          f"{size_all} B with --viewer-all ({t_all:.1f} s)")


# ------------------------------------------------------------ save-lines / --out


def test_save_lines_with_a_prefix(tmp_path):
    out = tmp_path / "tb" / "emir_save.scs"
    d, p = jrun("save-lines", DSPF, "--prefix", "XDUT.", "--out", out)
    assert d["mode"] == "rcc" and "modes" not in d
    assert d["lines"][0].startswith("save XDUT.Rm1:1 ") and d["count"] == 16
    assert out.read_text() == "\n".join(d["lines"]) + "\n" and d["out"] == str(out)
    d, p = jrun("save-lines", DSPF, "--mode", "auto", rc=2)
    assert d["code"] == "EMIR-E507" and "one method of a simulated run, rcc" in d["message"]


def test_save_lines_out_where_no_directory_can_be_made(tmp_path):
    blocker = write(tmp_path / "f", "x")
    d, _ = jrun("save-lines", DSPF, "--out", f"{blocker}/s.scs", rc=2)
    assert d["code"] == "EMIR-E105"


# ------------------------------------------------------------ gui-install, bin/emir


def test_gui_install_without_a_clone_writes_a_launcher(tmp_path, monkeypatch):
    from emir import skillgui
    real = os.path.isfile
    monkeypatch.setattr(skillgui.os.path, "isfile",
                        lambda p: False if p.endswith(os.path.join("bin", "emir")) else real(p))
    monkeypatch.setattr(skillgui.sys if hasattr(skillgui, "sys") else sys, "executable",
                        str(tmp_path / "py" / "python3"))
    files = skillgui.install(str(tmp_path / "sk"))
    launcher = tmp_path / "sk" / "emir_launch.sh"
    stub = open(files["site"]).read()
    assert launcher.exists() and os.access(launcher, os.X_OK)
    assert f'emirBin = "{launcher}"' in stub and "emirInstall" not in stub
    text = launcher.read_text()
    assert "unset PYTHONHOME PYTHONPATH" in text and "-m emir" in text


def test_gui_install_in_a_clone_uses_bin_emir(tmp_path):
    files = __import__("emir.skillgui", fromlist=["install"]).install(str(tmp_path / "sk"))
    stub = open(files["site"]).read()
    assert f'emirBin = "{os.path.join(ROOT, "bin", "emir")}"' in stub
    assert f'emirInstall = "{ROOT}"' in stub


def test_bin_emir_drops_the_parents_python_environment(tmp_path):
    """Virtuoso can export a PYTHONHOME or PYTHONPATH meant for its own Python;
    bin/emir must start anyway, unbuffered."""
    env = {**os.environ, "EMIR_PYTHON": sys.executable, "PYTHONHOME": "/nonexistent/home",
           "PYTHONPATH": "/nonexistent/path"}
    p = subprocess.run([os.path.join(ROOT, "bin", "emir"), "--version"], env=env,
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    probe = write(tmp_path / "probe.sh", "#!/bin/sh\n" + open(
        os.path.join(ROOT, "bin", "emir")).read().split("exec ")[0].replace(
        'PYTHONSAFEPATH=1 PYTHONPATH="$here${PYTHONPATH:+:$PYTHONPATH}" ', "")
        + 'echo "HOME=${PYTHONHOME-unset} PATH=${PYTHONPATH-unset} UNBUF=$PYTHONUNBUFFERED"\n')
    p = subprocess.run(["sh", probe], env=env, capture_output=True, text=True)
    assert p.stdout.strip().endswith("HOME=unset PATH=unset UNBUF=1"), p.stdout + p.stderr
    p = subprocess.run(["sh", probe], env={**env, "EMIR_KEEP_PYTHONPATH": "1"},
                       capture_output=True, text=True)
    assert "PATH=/nonexistent/path" in p.stdout


# ------------------------------------------------------------ emir next (plan_cadence)


def _plan(**kw):
    return P.build(P.Facts(**kw))


def test_next_plans_the_extraction_from_a_layout(tmp_path):
    plan = _plan(gds="blk.gds", cdl="blk.cdl", cell="blk", have_cadence=True)
    ids = [s["id"] for s in plan.steps]
    # the RC extraction: the netlist rcc simulates and the DSPF it screens
    assert ids == ["selftest", "extract-plan:rc_coupled", "extract:rc_coupled"]
    run = plan.steps[2]
    assert run["command"] == ("emir extract --gds blk.gds --cdl blk.cdl --cell blk "
                              "--kind rc_coupled --run")
    assert run["needs_human"] and run["licensed_tool"] == "quantus"
    assert not plan.steps[1]["needs_human"] and "cadence-extract" in plan.rules
    assert run["after"] == ["extract-plan:rc_coupled"]


def test_next_advises_extract_at_a_cadence_site():
    plan = _plan(have_cadence=True)
    assert any("emir extract --gds" in a for a in plan.advice)
    plan = _plan(have_cadence=False, have_xyce=True)
    assert not any("emir extract --gds" in a for a in plan.advice)


def test_next_points_a_spectre_simulation_at_the_templates():
    plan = _plan(dspf=DSPF, process=PROC, have_cadence=True)
    sim = next(s for s in plan.steps if s["id"] == "simulate")
    assert sim["command"].startswith("spectre ")
    assert any("tb_rcc.scs.tmpl" in a and "--out" in a for a in plan.advice)


def test_next_on_a_big_block_at_a_cadence_site(monkeypatch):
    monkeypatch.setitem(P.THRESHOLDS, "big_block_nodes", 10)
    plan = _plan(dspf=DSPF, process=PROC, have_cadence=True)
    assert any(a.startswith("ADC-top / PLL-top size") for a in plan.advice)
    assert not any("c_only" in a or "cc-grid" in a for a in plan.advice)
    sim = next(s for s in plan.steps if s["id"] == "simulate")
    assert sim["long_run"] and sim["needs_human"]


def test_next_viewer_at_a_cadence_site():
    plan = _plan(dspf=DSPF, process=PROC, currents=CUR, net="VDD", viewer=True,
                 lib="L", have_cadence=True)
    assert any("EMIR > Load results" in a and "read-only" in a for a in plan.advice)


def test_next_cli_still_validates_with_the_cadence_rules():
    d, _ = jrun("next", "--dspf", DSPF, "--process", PROC, "--have-cadence")
    assert "cadence-testbench" in d["rules"]
    assert sample_text()                                         # (fixture sanity)
