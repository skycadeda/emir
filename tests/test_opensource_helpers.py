"""agent/opensource: the one-shot flow scripts (run_xyce.sh, run_ngspice.sh)
and the KLayout marker-database helpers (dspf_preview_gds.py,
lyrdb_summary.py, render_markers.py). Each test is skipped when the tool it
drives is not installed."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys

import jsonschema
import pytest
from _helpers import PROC, ROOT

from emir import schemas, site

HERE = os.path.join(ROOT, "agent", "opensource")
EX = os.path.join(ROOT, "examples", "02_xyce")
HAS_KLAYOUT_PY = importlib.util.find_spec("klayout") is not None
KLAYOUT_APP = shutil.which("klayout")


def flow(script, tmp_path, *opts, tb="tb_rcc.cir"):
    for f in ("loadrail.dspf", "models.sp", "tb_rcc.cir"):
        shutil.copy(os.path.join(EX, f), tmp_path / f)
    return subprocess.run(["sh", os.path.join(HERE, script), *opts, "--out", "out", tb,
                           "loadrail.dspf", PROC], cwd=tmp_path, capture_output=True,
                          text=True, env={**os.environ, "EMIR_PYTHON": sys.executable})


@pytest.mark.parametrize("script, sim", [("run_xyce.sh", "xyce"), ("run_ngspice.sh", "ngspice")])
def test_the_flow_scripts_run_the_whole_flow(tmp_path, script, sim):
    if site.tool(sim)[0] is None:
        pytest.skip(f"{sim} not installed")
    p = flow(script, tmp_path, "--period", "1e-8")
    assert p.returncode == 0, p.stdout[-2000:] + p.stderr[-2000:]
    out = tmp_path / "out"
    s = json.load(open(out / "summary.json"))
    jsonschema.Draft202012Validator(schemas.get("run")).validate(s)
    # rcc reads every branch current: the example's peak FAIL (Rc3) is found
    assert s["verdict"] == "FAIL" and s["nets"][0]["mode"] == "rcc"
    assert s["nets"][0]["worst_items"][0]["name"] == "Rc3"
    assert "Screening, not sign-off." in (out / "report.md").read_text()
    assert (out / "html" / "index.html").is_file()
    assert (out / "sim" / "result.raw.names").is_file()
    if HAS_KLAYOUT_PY:
        assert (out / "viewer" / "emir.lyrdb").is_file()
        assert "klayout <your layout.gds> -m out/viewer/emir.lyrdb" in p.stdout


def test_the_flow_script_refuses_bad_usage(tmp_path):
    p = subprocess.run(["sh", os.path.join(HERE, "run_xyce.sh"), "only-one-arg"],
                       capture_output=True, text=True)
    assert p.returncode == 2 and "TESTBENCH DSPF PROCESS" in p.stderr
    p = subprocess.run(["sh", os.path.join(HERE, "emir_open_flow.sh"), "spectre", "a", "b", "c"],
                       capture_output=True, text=True)
    assert p.returncode == 2
    p = subprocess.run(["sh", os.path.join(HERE, "run_xyce.sh"), "--mode", "cc-grid", "a", "b",
                        "c"], capture_output=True, text=True)
    assert p.returncode == 2 and "one method of a simulated run, rcc" in p.stderr


@pytest.fixture
def lyrdb(tmp_path):
    """A marker database from a DC run of the example DSPF (no simulator)."""
    if not HAS_KLAYOUT_PY:
        pytest.skip("klayout Python package not installed")
    cur = ('{"VDD": 0.010, "XMN0:d": -0.002, "XMN1:d": -0.0025, "XMN2:d": -0.003, '
           '"XMN3:d": -0.0025}')
    p = subprocess.run([sys.executable, "-m", "emir", "run", os.path.join(EX, "loadrail.dspf"),
                        "--process", PROC, "--net", "VDD", "--currents", cur, "--em-temp", "100",
                        "--viewer", str(tmp_path / "viewer")], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr[-2000:]
    return tmp_path / "viewer" / "emir.lyrdb"


def test_the_preview_gds_holds_the_dspf_boxes(tmp_path):
    if not HAS_KLAYOUT_PY:
        pytest.skip("klayout Python package not installed")
    import klayout.db as kdb
    gds = tmp_path / "p.gds"
    p = subprocess.run([sys.executable, os.path.join(HERE, "dspf_preview_gds.py"),
                        os.path.join(EX, "loadrail.dspf"), "-o", str(gds)],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    ly = kdb.Layout()
    ly.read(str(gds))
    top = ly.top_cell()
    assert top.name == "loadrail"
    boxes = {(ly.get_info(li).layer, ly.get_info(li).datatype): top.shapes(li).size()
             for li in ly.layer_indexes()}
    assert boxes == {(1000, 0): 4, (1001, 0): 12}       # cont, metal1 (sorted tokens)
    assert "cont 1000/0, metal1 1001/0" in p.stdout
    assert top.dbbox().right == pytest.approx(60.375) and top.dbbox().left == 0.0


def test_the_lyrdb_summary_reads_the_markers(lyrdb):
    p = subprocess.run([sys.executable, os.path.join(HERE, "lyrdb_summary.py"), str(lyrdb),
                        "--json"], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    s = json.loads(p.stdout)
    assert s["cells"] == ["loadrail"] and "Screening, not sign-off." in s["description"]
    fails = {k: v for k, v in s["categories"].items() if "FAIL" in k}
    # DC: Rm1-6 carry 10 / 8 mA (4.2x, 3.3x avg), every contact fails avg
    assert sum(fails.values()) > 0 and any(k.startswith("EM/VDD/") for k in fails)


@pytest.mark.skipif(KLAYOUT_APP is None, reason="the KLayout application is not installed")
def test_the_klayout_application_loads_and_draws_the_markers(lyrdb, tmp_path):
    """The reader of the GUI's Marker Browser (`pya`, in the application), and
    a headless picture of the markers on the preview layout."""
    p = subprocess.run([KLAYOUT_APP, "-b", "-r", os.path.join(HERE, "lyrdb_summary.py"),
                        "-rd", f"lyrdb={lyrdb}"], capture_output=True, text=True)
    assert p.returncode == 0 and "markers in cell(s) loadrail" in p.stdout, p.stderr
    gds = tmp_path / "p.gds"
    subprocess.run([sys.executable, os.path.join(HERE, "dspf_preview_gds.py"),
                    os.path.join(EX, "loadrail.dspf"), "-o", str(gds)], check=True,
                   capture_output=True)
    png = tmp_path / "m.png"
    p = subprocess.run([KLAYOUT_APP, "-z", "-r", os.path.join(HERE, "render_markers.py"),
                        "-rd", f"gds={gds}", "-rd", f"lyrdb={lyrdb}", "-rd", f"png={png}"],
                       capture_output=True, text=True,
                       env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
    assert p.returncode == 0, p.stdout + p.stderr
    assert png.is_file() and png.stat().st_size > 500 and "marker(s) matching 'FAIL'" in p.stdout
    # a layout with a second top cell: `-rd cell=` names the one emir screened
    # (KLayout's `top_cell()` refuses a layout with several top cells)
    if HAS_KLAYOUT_PY:
        import klayout.db as kdb
        ly = kdb.Layout()
        ly.read(str(gds))
        ly.create_cell("other").shapes(ly.layer(1, 0)).insert(kdb.Box(0, 0, 10, 10))
        two = tmp_path / "two.gds"
        ly.write(str(two))
        p = subprocess.run([KLAYOUT_APP, "-z", "-r", os.path.join(HERE, "render_markers.py"),
                            "-rd", f"gds={two}", "-rd", f"lyrdb={lyrdb}", "-rd",
                            f"png={tmp_path / 'two.png'}", "-rd", "cell=loadrail"],
                           capture_output=True, text=True,
                           env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
        assert p.returncode == 0, p.stdout + p.stderr
        assert "drawn on loadrail" in p.stdout
