"""`emir extract` (GDS + CDL -> DSPF with PVS LVS and Quantus): the command
file, the dry-run plan, and a --run against stand-in executables (no Cadence
tool exists here: tests/_cadence_fakes.py). Also the standalone
agent/cadence/quantus/render_ccl.py and agent/cadence/pvs/pvs_lvs_quantus.sh,
on the same stand-ins."""
import json
import os
import subprocess
import sys
import time

import jsonschema
import pytest
from _cadence_fakes import calls, make_decks, make_tools
from _helpers import ROOT, emir, write

from emir import schemas
from emir.cadence import CCL_TEMPLATE, KINDS, LOAD_BEARING, render_ccl
from emir.errors import EmirError

CADENCE = os.path.join(ROOT, "agent", "cadence")


def validate(doc):
    name = doc["schema"].split(".", 1)[1].split("/")[0]
    jsonschema.Draft202012Validator(schemas.get(name)).validate(doc)
    return doc


@pytest.fixture
def site(tmp_path):
    """A GDS and CDL (placeholders: the stand-ins never read them), stand-in
    tools and decks, and an environment with every setting given."""
    gds = write(tmp_path / "blk.gds", "not a real GDS\n")
    cdl = write(tmp_path / "blk.cdl", ".SUBCKT railblock VDD\n.ENDS\n")
    tools = make_tools(tmp_path / "bin")
    decks = make_decks(tmp_path)
    env = {**os.environ, "PATH": tools + os.pathsep + os.environ["PATH"],
           "FAKE_LOG": str(tmp_path / "calls.jsonl"), "EMIR_SITE": os.devnull,
           "PVS_RULES": decks["rules"], "QRC_TECH_DIR": decks["tech_dir"],
           "QRC_CORNER": decks["corner"], "EMIR_EXTRACT_TEMP": "25"}
    for k in ("PVS_BIN", "QRC_BIN"):
        env.pop(k, None)
    return {"dir": tmp_path, "gds": gds, "cdl": cdl, "env": env, "decks": decks,
            "log": str(tmp_path / "calls.jsonl"), "work": str(tmp_path / "work")}


def extract(site, *extra, kind="r_only", rc=0, env=None):
    p = emir("extract", "--gds", site["gds"], "--cdl", site["cdl"], "--cell", "railblock",
             "--kind", kind, "--work", site["work"], *extra, "--format", "json",
             env=env or site["env"], cwd=str(site["dir"]))
    assert p.returncode == rc, (p.returncode, p.stdout[-1500:], p.stderr[-1500:])
    return validate(json.loads(p.stdout)), p


# ------------------------------------------------------------ the command file


@pytest.mark.parametrize("kind", KINDS)
def test_every_load_bearing_setting_is_in_the_command_file(kind):
    text = render_ccl("rel/svdb", "blk", "blk.r_only.dspf", "/pdk/qrc/", "typ", 25, kind)
    for flag, _why in LOAD_BEARING:
        assert flag in text, (kind, flag)
    assert f'-type "{kind}"' in text
    assert f'-directory_name "{os.path.abspath("rel/svdb")}"' in text   # absolute
    assert '-technology_directory "/pdk/qrc/typ"' in text
    assert "-temperature 25.000000" in text and '-file_name "blk.r_only.dspf"' in text
    assert "${" not in text


def test_bad_kind_or_database_type_is_coded():
    with pytest.raises(EmirError, match="EMIR-E513"):
        render_ccl("s", "c", "o", "/t", "typ", 25, "rc_everything")
    with pytest.raises(EmirError, match="EMIR-E513"):
        render_ccl("s", "c", "o", "/t", "typ", 25, "r_only", lvs_db_type="other")


def test_standalone_renderer_gives_the_same_command_file(tmp_path):
    """agent/cadence/quantus/render_ccl.py (standard library only) renders
    the committed template exactly as emir does."""
    script = os.path.join(CADENCE, "quantus", "render_ccl.py")
    for kind in KINDS:
        p = subprocess.run([sys.executable, script, "--svdb", "/w/svdb", "--cell", "blk",
                            "--tech-dir", "/pdk/qrc", "--corner", "typ", "--temperature", "25",
                            "--kind", kind], capture_output=True, text=True)
        assert p.returncode == 0, p.stderr
        assert p.stdout == render_ccl("/w/svdb", "blk", f"blk.{kind}.dspf", "/pdk/qrc",
                                      "typ", 25, kind)
    p = subprocess.run([sys.executable, script, "--cell", "blk", "--tech-dir", "/t",
                        "--corner", "typ"], capture_output=True, text=True)
    assert p.returncode == 2 and "--temperature" in p.stderr      # no default assumed
    with open(os.path.join(CADENCE, "quantus", "emir.ccl.tmpl"), encoding="utf-8") as f:
        assert f.read() == CCL_TEMPLATE


# ------------------------------------------------------------ the dry run


def test_dry_run_with_nothing_configured_lists_what_is_missing(tmp_path):
    gds = write(tmp_path / "b.gds", "x\n")
    cdl = write(tmp_path / "b.cdl", "x\n")
    env = {k: v for k, v in os.environ.items()
           if k not in ("PVS_BIN", "QRC_BIN", "PVS_RULES", "QRC_TECH_DIR", "QRC_CORNER",
                        "EMIR_EXTRACT_TEMP")}
    env.update(EMIR_SITE=os.devnull, PATH="/usr/bin:/bin")
    p = emir("extract", "--gds", gds, "--cdl", cdl, "--cell", "b", "--kind", "r_only",
             "--format", "json", env=env, cwd=str(tmp_path))
    assert p.returncode == 0, p.stderr
    d = validate(json.loads(p.stdout))
    assert d["dry_run"] is True and d["ready"] is False and d["next_steps"] == []
    assert {m["what"] for m in d["missing"]} == {
        "pvs", "qrc", "pvs_lvs_rules", "qrc_tech_dir", "qrc_corner", "extract_temperature"}
    assert d["ccl"] is None
    assert "EMIR-I680" in p.stderr
    assert not os.path.exists(tmp_path / "emir_out")            # changed nothing


def test_dry_run_ready_plans_lvs_gate_and_quantus(site):
    d, p = extract(site)
    assert d["dry_run"] and d["ready"] and not d["missing"]
    assert [s["id"] for s in d["steps"]] == ["control-file", "lvs", "lvs-gate", "quantus"]
    lvs = next(s for s in d["steps"] if s["id"] == "lvs")
    assert lvs["argv"][1:4] == ["-lvs", "-rc_data", "-ui_data"]
    assert os.path.isabs(lvs["argv"][lvs["argv"].index("-gds") + 1])
    assert lvs["argv"][-1] == site["decks"]["rules"] and lvs["licensed_tool"] == "pvs"
    q = next(s for s in d["steps"] if s["id"] == "quantus")
    assert q["argv"][1:] == ["-cmd", "qrc_r_only.ccl"] and q["licensed_tool"] == "quantus"
    assert d["dspf"] == os.path.join(site["work"], "railblock.r_only.dspf")
    assert d["ccl"] and '-type "r_only"' in d["ccl"]
    assert d["settings"]["extract_temperature"] == {"value": 25.0,
                                                    "source": "$EMIR_EXTRACT_TEMP"}
    assert d["next_steps"][0]["command"].endswith("--run --format json")
    assert calls(site["log"]) == [] and not os.path.exists(site["work"])   # nothing ran
    assert "DRY RUN" in p.stderr


def test_options_win_over_the_environment(site):
    d, _ = extract(site, "--temperature", "85", "--corner", "typ")
    assert d["settings"]["extract_temperature"]["source"] == "argument"
    assert "-temperature 85.000000" in d["ccl"]


def test_site_file_option(site, tmp_path):
    env = {k: v for k, v in site["env"].items()
           if k not in ("PVS_RULES", "QRC_TECH_DIR", "QRC_CORNER", "EMIR_EXTRACT_TEMP")}
    sf = write(tmp_path / "site.yaml",
               f"pvs_lvs_rules: {site['decks']['rules']}\n"
               f"qrc_tech_dir: {site['decks']['tech_dir']}\nqrc_corner: typ\n"
               f"extract_temperature: 27\n")
    d, _ = extract(site, "--site", sf, env=env)
    assert d["ready"] and d["settings"]["qrc_corner"] == {"value": "typ",
                                                         "source": os.path.abspath(sf)}
    assert "--site" in d["next_steps"][0]["command"]


def test_a_corner_the_tech_dir_lacks_is_missing(site):
    d, _ = extract(site, "--corner", "nonesuch")
    assert [m["what"] for m in d["missing"]] == ["qrc_corner"] and not d["ready"]


def test_missing_gds_is_e101(site):
    p = emir("extract", "--gds", "/nonexistent.gds", "--cdl", site["cdl"], "--cell", "x",
             "--kind", "r_only", "--format", "json", env=site["env"])
    assert p.returncode == 2 and json.loads(p.stdout)["code"] == "EMIR-E101"


def test_kind_is_required_and_checked(site):
    for extra in ([], ["--kind", "rc_everything"]):
        p = emir("extract", "--gds", site["gds"], "--cdl", site["cdl"], "--cell", "x",
                 *extra, "--format", "json", env=site["env"])
        assert p.returncode == 2 and json.loads(p.stdout)["code"] == "EMIR-E507"


# ------------------------------------------------------------ --run


def test_run_lvs_match_then_quantus(site):
    d, p = extract(site, "--run")
    assert d["dry_run"] is False and d["lvs"]["matched"] is True
    assert os.path.isfile(d["dspf"]) and d["dspf"].endswith("railblock.r_only.dspf")
    with open(d["dspf"]) as f:
        assert "*|OPERATING_TEMPERATURE 25.000000" in f.read()   # the -temperature reached it
    tools = [c["tool"] for c in calls(site["log"])]
    assert tools == ["pvs", "qrc"]
    q = calls(site["log"])[1]
    assert q["cwd"] == site["work"] and q["argv"] == ["-cmd", "qrc_r_only.ccl"]
    assert os.path.isfile(os.path.join(site["work"], "pvs_control_file"))   # copied beside
    with open(os.path.join(site["work"], "qrc_r_only.ccl")) as f:
        assert f.read() == d["ccl"]
    assert [s["status"] for s in d["steps"]] == ["done", "done", "passed", "done"]
    assert [s["command"].split()[1] for s in d["next_steps"]] == ["nets", "next"]
    # the DSPF it names is one emir reads
    n = emir("nets", d["dspf"], "--format", "json")
    assert n.returncode == 0 and json.loads(n.stdout)["nets"][0]["name"] == "VDD"


def test_second_kind_reuses_the_matched_lvs(site):
    extract(site, "--run")
    d, _ = extract(site, "--run", kind="c_only_coupled")
    assert d["lvs"]["reuse"] is True and d["dspf"].endswith("railblock.c_only_coupled.dspf")
    assert [c["tool"] for c in calls(site["log"])] == ["pvs", "qrc", "qrc"]
    assert os.path.isfile(os.path.join(site["work"], "railblock.r_only.dspf"))  # not overwritten
    d, _ = extract(site, "--run", "--rerun-lvs")
    assert d["lvs"]["reuse"] is False
    assert [c["tool"] for c in calls(site["log"])][-2:] == ["pvs", "qrc"]


def test_lvs_older_than_the_layout_is_run_again(site):
    extract(site, "--run")
    later = time.time() + 5
    os.utime(site["gds"], (later, later))
    d, _ = extract(site)
    assert d["lvs"]["matched"] is True and d["lvs"]["reuse"] is False


@pytest.mark.parametrize("mode", ["mismatch", "none"])
def test_no_match_refuses_the_extraction(site, mode):
    env = {**site["env"], "FAKE_LVS": mode}
    d, _ = extract(site, "--run", rc=2, env=env)
    assert d["code"] == "EMIR-E280"
    assert [c["tool"] for c in calls(site["log"])] == ["pvs"]       # Quantus never ran
    assert d["next_steps"][0]["command"].startswith("tail -n 60 ")
    assert not os.path.exists(os.path.join(site["work"], "railblock.r_only.dspf"))


def test_quantus_without_a_dspf_is_e281(site):
    d, _ = extract(site, "--run", rc=2, env={**site["env"], "FAKE_QRC": "none"})
    assert d["code"] == "EMIR-E281" and d["details"]["exit_code"] == 1


def test_missing_setting_on_run_is_e580(site):
    env = {k: v for k, v in site["env"].items() if k != "EMIR_EXTRACT_TEMP"}
    d, _ = extract(site, "--run", rc=2, env=env)
    assert d["code"] == "EMIR-E580"
    assert [m["what"] for m in d["details"]["missing"]] == ["extract_temperature"]
    # the fix: the dry run of the same extraction
    assert d["next_steps"][0]["command"].startswith("emir extract --gds ")
    assert "--run" not in d["next_steps"][0]["command"]
    assert calls(site["log"]) == []


def test_missing_tool_on_run_is_e680_exit_3(site):
    d, _ = extract(site, "--run", "--qrc", "/nonexistent/qrc", rc=3)
    assert d["code"] == "EMIR-E680" and d["exit_code"] == 3
    assert calls(site["log"]) == []


def test_a_tool_over_its_time_is_e681(site):
    d, _ = extract(site, "--run", "--timeout", "0.5", rc=2,
                   env={**site["env"], "FAKE_SLEEP": "5"})
    assert d["code"] == "EMIR-E681"


# ------------------------------------------------------------ the shell script


def _sh(site, *args, env=None):
    script = os.path.join(CADENCE, "pvs", "pvs_lvs_quantus.sh")
    e = dict(env or site["env"])
    e["PVS_RULES"], e["EXTRACT_TEMP"] = site["decks"]["rules"], "25"
    return subprocess.run(["sh", script, site["gds"], "railblock", site["cdl"],
                           site["work"], *args], env=e, capture_output=True, text=True,
                          cwd=str(site["dir"]))


def test_shell_script_match_then_quantus(site):
    p = _sh(site)
    assert p.returncode == 0, p.stderr
    out = p.stdout.strip()
    assert out == os.path.join(site["work"], "railblock.r_only.dspf") and os.path.isfile(out)
    pvs, qrc = calls(site["log"])
    assert pvs["argv"][:3] == ["-lvs", "-rc_data", "-ui_data"]
    assert os.path.isabs(pvs["argv"][pvs["argv"].index("-gds") + 1])
    with open(os.path.join(site["work"], "qrc_r_only.ccl")) as f:
        ccl = f.read()
    assert ccl == render_ccl(os.path.join(site["work"], "svdb"), "railblock",
                             "railblock.r_only.dspf", site["decks"]["tech_dir"], "typ", 25)
    # a second kind reuses the matched LVS
    p = _sh(site, "c_only_coupled")
    assert p.returncode == 0 and "reusing" in p.stderr
    assert [c["tool"] for c in calls(site["log"])] == ["pvs", "qrc", "qrc"]


def test_shell_script_refuses_without_match(site):
    p = _sh(site, env={**site["env"], "FAKE_LVS": "mismatch"})
    assert p.returncode == 2 and "did not MATCH" in p.stderr
    assert [c["tool"] for c in calls(site["log"])] == ["pvs"]


def test_shell_script_settings_and_tools(site):
    env = {k: v for k, v in site["env"].items() if k != "QRC_CORNER"}
    p = _sh(site, env=env)
    assert p.returncode == 2 and "QRC_CORNER" in p.stderr
    p = _sh(site, env={**site["env"], "QRC_BIN": "/nonexistent/qrc"})
    assert p.returncode == 3 and "qrc not found" in p.stderr
    p = _sh(site, "bogus_kind")
    assert p.returncode == 2 and "unknown kind" in p.stderr
    assert calls(site["log"]) == []
