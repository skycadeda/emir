"""The command-line contract an agent relies on, for every command:

* `--format json` prints exactly one JSON document on stdout, whose `schema`
  field names its JSON Schema, and the document validates against it;
  everything else goes to stderr;
* a failure in JSON mode prints an `emir.error/1` document with its code;
* the exit status follows the contract: 0 ok, 1 violations with
  --fail-on-violation, 2 bad input or refused, 3 a required tool missing,
  70 internal (or a failed self-test / validation), 130 interrupted.
"""
import json
import os
import shutil

import jsonschema
import pytest
import yaml
from _helpers import CUR, DSPF, PROC, ROOT, emir, rcc_result, write

from emir import cli, schemas

VALIDATOR = jsonschema.Draft202012Validator


def validate(doc):
    name = doc["schema"].split(".", 1)[1].split("/")[0]
    VALIDATOR(schemas.get(name)).validate(doc)
    return doc


def jrun(*args, rc=0, env=None, cwd=None):
    """`emir ARGS --format json`: the exit status, ONE document, valid."""
    p = emir(*args, "--format", "json", env=env, cwd=cwd)
    assert p.returncode == rc, (p.returncode, p.stdout[-2000:], p.stderr[-2000:])
    doc = json.loads(p.stdout)                 # one document: Extra data would raise
    assert isinstance(doc, dict) and doc["emir_version"]
    return validate(doc), p


def no_spectre_env():
    return {**os.environ, "SPECTRE_BIN": "/nonexistent/spectre", "EMIR_SITE": os.devnull}


# ------------------------------------------------------------ every command


def test_info():
    d, p = jrun("info")
    assert d["schema"] == "emir.info/1"
    assert {t["name"] for t in d["tools"]} >= {"spectre", "xyce", "ngspice"}
    assert set(d["commands"]) == set(cli.COMMANDS)
    assert d["exit_codes"]["70"].startswith("internal")
    assert "spectre" in p.stderr and p.stdout.lstrip().startswith("{")


def test_selftest():
    d, _ = jrun("selftest")
    assert d["passed"] is True and d["code"] is None and len(d["checks"]) >= 10
    assert all(c["ok"] for c in d["checks"])


def test_selftest_failure_exits_70(monkeypatch, capsys):
    from emir import selftest
    monkeypatch.setattr(selftest, "SAMPLE_CURRENTS", '{"VDD": 0.004, "MP0:s": -0.004}')
    assert cli.main(["selftest", "--format", "json"]) == 70
    d = validate(json.loads(capsys.readouterr().out))
    assert d["passed"] is False and d["code"] == "EMIR-E902"
    shutil.rmtree(d["out_dir"], ignore_errors=True)


def test_validate(monkeypatch, capsys):
    from emir import validate as V
    monkeypatch.setattr(V, "CASES", [c for c in V.CASES if c[0] in ("dspf", "window")])
    assert cli.main(["validate", "--format", "json"]) == 0
    d = validate(json.loads(capsys.readouterr().out))
    assert d["passed"] and [c["status"] for c in d["cases"]] == ["PASS", "PASS"]


def test_validate_failure_exits_70(monkeypatch, capsys):
    from emir import validate as V

    def broken(out):
        raise AssertionError("1.0 != 2.0")
    monkeypatch.setattr(V, "CASES", [("broken", broken, "core", False)])
    assert cli.main(["validate", "--format", "json"]) == 70
    d = validate(json.loads(capsys.readouterr().out))
    assert d["code"] == "EMIR-E903" and d["cases"][0]["status"] == "FAIL"
    assert "1.0 != 2.0" in d["cases"][0]["reason"]
    shutil.rmtree(d["out_dir"], ignore_errors=True)


def test_nets_pins_save_lines():
    d, _ = jrun("nets", DSPF)
    assert [(n["name"], n["resistors"], n["nodes"], n["inst_pins"], n["port"])
            for n in d["nets"]] == [("VDD", 16, 17, 4, True)]
    d, _ = jrun("pins", DSPF, "--net", "VDD")
    assert [p["name"] for p in d["pins"]] == ["MP0:s", "MP1:s", "MP2:s", "MP3:s"]
    d, _ = jrun("save-lines", DSPF, "--net", "VDD", "--mode", "rcc")
    assert d["mode"] == "rcc" and d["count"] == 16 and d["lines"][0].startswith("save ")
    d, _ = jrun("save-lines", DSPF, "--prefix", "XI0.")
    assert d["lines"][0].startswith("save XI0.Rm1:1 XI0.Rm2:1") and d["count"] == 16
    assert d["sidecars"] == []


@pytest.mark.parametrize("args,code", [
    (["nets", "/nonexistent.dspf"], "EMIR-E101"),
    (["pins", DSPF, "--net", "vdd"], "EMIR-E204"),
    (["save-lines", DSPF, "--net", "vdd", "--mode", "rcc"], "EMIR-E204"),
    (["save-lines", DSPF, "--net", "VDD", "--mode", "cc-grid"], "EMIR-E507"),  # rcc only
    (["pins", DSPF], "EMIR-E507"),                     # --net is required
    (["nets", DSPF, "--he"], "EMIR-E507"),             # no abbreviations
    (["info", "--verbose"], "EMIR-E507"),
    (["selftest", "--kee"], "EMIR-E507"),
    (["validate", "--qui"], "EMIR-E507"),
    (["gui-install", "--di", "x"], "EMIR-E507"),
    (["check"], "EMIR-E103"),                          # a DSPF or --process is required
    (["explain"], "EMIR-E507"),
    (["schema"], "EMIR-E507"),
    (["bogus"], "EMIR-E508"),
    (["schema", "nope"], "EMIR-E512"),
    (["explain", "E999"], "EMIR-E511"),
    (["report", "--from", PROC], "EMIR-E104"),
    (["next", "--mode", "fast"], "EMIR-E507"),
    (["import-ict", "--ict", "/nonexistent.ict", "--emir", PROC], "EMIR-E101"),
])
def test_bad_input_is_an_error_document_and_exit_2(args, code):
    d, _ = jrun(*args, rc=2)
    assert d["schema"] == "emir.error/1" and d["code"] == code and d["exit_code"] == 2
    assert d["next_steps"] and all(s["command"] for s in d["next_steps"])


def test_check_pass_and_fail(tmp_path):
    d, _ = jrun("check", DSPF, "--process", PROC, "--net", "VDD", "--currents", CUR,
                "--json-out", tmp_path / "c.json")
    assert d["passed"] and d["n_fail"] == 0
    assert json.load(open(tmp_path / "c.json")) == d          # the same document
    assert d["next_steps"][0]["command"].startswith("emir run ")
    d, _ = jrun("check", DSPF, "--process", PROC, "--net", "NOPE", "--currents", CUR, rc=2)
    assert d["schema"] == "emir.check/1" and not d["passed"]
    assert "EMIR-E204" in [f["code"] for f in d["findings"]]


def test_run_dc_and_fail_on_violation(tmp_path):
    d, p = jrun("run", DSPF, "--net", "VDD", "--process", PROC, "--currents", CUR,
                "--summary-out", tmp_path / "s.json")
    assert d["verdict"] == "FAIL" and d["nets"][0]["mode"] == "dc"
    assert "overall verdict: FAIL" in p.stderr               # the console text, on stderr
    saved = json.load(open(tmp_path / "s.json"))
    assert saved == d
    d, p = jrun("run", DSPF, "--net", "VDD", "--process", PROC, "--currents", CUR,
                "--fail-on-violation", rc=1)
    assert d["verdict"] == "FAIL" and "exit 1" in p.stderr


def test_run_rcc(tmp_path):
    d, _ = jrun("run", DSPF, "--net", "VDD", "--process", PROC, "--cc-sim",
                rcc_result(tmp_path), "--mode", "rcc", "--period", "1e-9")
    assert d["nets"][0]["mode"] == "rcc"


def test_run_refused_and_nothing_analysed(tmp_path):
    d, _ = jrun("run", DSPF, "--net", "NOPE", "--process", PROC, "--currents", CUR, rc=2)
    assert d["code"] == "EMIR-E501"
    d, p = jrun("run", DSPF, rc=2, env={**os.environ, "EMIR_SITE": os.devnull})
    assert d["code"] == "EMIR-E509" and "NOT ANALYSED" in p.stderr


def test_run_with_a_missing_simulator_exits_3(tmp_path):
    net = write(tmp_path / "tb.scs", "// a netlist\n")
    d, _ = jrun("run", DSPF, "--net", "VDD", "--process", PROC, "--netlist", net,
                "--run", rc=3, env=no_spectre_env())
    assert d["code"] == "EMIR-E601" and d["exit_code"] == 3
    assert d["next_steps"][0]["command"] == "emir info --format json"
    # without --run: planned (the Spectre command), nothing run
    d, _ = jrun("run", DSPF, "--net", "VDD", "--process", PROC, "--netlist", net,
                "--period", "1e-8", env=no_spectre_env())
    assert d["schema"] == "emir.simulate/1" and d["ran"] is False and d["mode"] == "rcc"
    assert d["next_steps"][0]["command"].endswith("--run --format json")
    # without --period the transient would be refused after it ran: refused
    # first (EMIR-E428), nothing planned to run
    d, _ = jrun("run", DSPF, "--net", "VDD", "--process", PROC, "--netlist", net, rc=2,
                env=no_spectre_env())
    assert d["code"] == "EMIR-E428"


def test_a_missing_package_exits_3(monkeypatch, capsys):
    def boom(argv, out):
        raise ModuleNotFoundError("No module named 'klayout'", name="klayout")
    monkeypatch.setitem(cli.COMMANDS, "nets", boom)
    assert cli.main(["nets", "x", "--format", "json"]) == 3
    d = validate(json.loads(capsys.readouterr().out))
    assert d["code"] == "EMIR-E603"


def test_internal_error_exits_70_with_a_9xx_code(monkeypatch, capsys):
    def boom(argv, out):
        raise ZeroDivisionError("division by zero")
    monkeypatch.setitem(cli.COMMANDS, "nets", boom)
    monkeypatch.delenv("EMIR_DEBUG", raising=False)
    assert cli.main(["nets", "x", "--format", "json"]) == 70
    d = validate(json.loads(capsys.readouterr().out))
    assert d["code"] == "EMIR-E901" and d["exit_code"] == 70


def test_interrupt_exits_130(monkeypatch, capsys):
    def boom(argv, out):
        raise KeyboardInterrupt
    monkeypatch.setitem(cli.COMMANDS, "nets", boom)
    assert cli.main(["nets", "x", "--format", "json"]) == 130
    assert validate(json.loads(capsys.readouterr().out))["code"] == "EMIR-E904"


def test_stray_prints_never_reach_stdout_in_json_mode(monkeypatch, capsys):
    def chatty(argv, out):
        print("progress line")                          # a command's own print
        out.emit({"schema": "emir.explain/1", "emir_version": "x", "codes": []})
        print("after")
        return 0
    monkeypatch.setitem(cli.COMMANDS, "nets", chatty)
    assert cli.main(["nets", "--format", "json"]) == 0
    o = capsys.readouterr()
    assert json.loads(o.out)["codes"] == [] and "progress line" in o.err and "after" in o.err


def test_import_ict(tmp_path):
    from test_import_ict import EMIR_YAML, FAKE_ICT
    ict = write(tmp_path / "d.ict", FAKE_ICT % {"m1": "metal1_conn"})
    src = write(tmp_path / "emir.yaml", yaml.safe_dump(EMIR_YAML))
    d, p = jrun("import-ict", "--ict", ict, "--emir", src, "-o", tmp_path / "out.yaml")
    assert d["process"] == "DEMO" and d["em_tref"] == 100.0
    assert {n["code"] for n in d["notices"]} >= {"EMIR-W313"}
    assert "WARNING EMIR-W313" in p.stderr


def test_gui_install(tmp_path):
    d, _ = jrun("gui-install", "--dir", tmp_path / "skill")
    assert os.path.isfile(d["files"]["main"]) and d["cdsinit"].startswith('load("')
    blocker = write(tmp_path / "file", "a file where the directory should go\n")
    d, _ = jrun("gui-install", "--dir", f"{blocker}/skill", rc=2)
    # an output that cannot be written, named as one (not "input file not found")
    assert d["code"] == "EMIR-E105" and f"{blocker}/skill" in d["message"]


def test_schema_commands():
    d, _ = jrun("schema", "--list")
    assert {s["name"] for s in d["schemas"]} == set(schemas.names())
    p = emir("schema", "run", "--format", "json")
    assert p.returncode == 0 and json.loads(p.stdout) == schemas.get("run")
    p = emir("schema", "save-lines")
    assert p.returncode == 0 and json.loads(p.stdout)["$id"] == "urn:emir:schema:save_lines:1"


def test_next_and_report(tmp_path):
    d, _ = jrun("next", "--dspf", DSPF, "--process", PROC, "--currents", CUR,
                "--net", "VDD")
    assert d["mode"]["chosen"] == "dc"
    jrun("run", DSPF, "--net", "VDD", "--process", PROC, "--currents", CUR,
         "--summary-out", tmp_path / "s.json")
    d, _ = jrun("report", "--from", tmp_path / "s.json")
    assert d["verdict"] == "FAIL" and d["markdown"].startswith("# EMIR screening report")


def test_help_exits_0():
    p = emir("run", "--help")
    assert p.returncode == 0 and "--summary-out" in p.stdout and "--format" in p.stdout
    assert "--json" not in p.stdout.replace("--json-out", "")     # the alias is hidden


# ------------------------------------------------------------ --summary-out and --json


def test_old_json_option_still_writes_the_summary(tmp_path):
    """`--json F` was renamed `--summary-out F`; argument files written for
    1.0 (the Virtuoso form wrote `--json`) still parse."""
    args = write(tmp_path / "args.txt", "\n".join(
        [DSPF, "--net", "VDD", "--process", PROC, "--currents", CUR,
         "--json", str(tmp_path / "old.json")]) + "\n")
    p = emir("run", f"@{args}")
    assert p.returncode == 0, p.stderr
    s = json.load(open(tmp_path / "old.json"))
    assert s["schema"] == "emir.run/1" and s["nets"][0]["verdict"] == "FAIL"
    p = emir("run", DSPF, "--net", "VDD", "--process", PROC, "--currents", CUR,
             "--summary-out", tmp_path / "new.json")
    assert p.returncode == 0
    new = json.load(open(tmp_path / "new.json"))
    own = ("command", "argv", "next_steps", "created")     # this run's own
    assert {k: v for k, v in new.items() if k not in own} == \
        {k: v for k, v in s.items() if k not in own}


def test_the_virtuoso_form_writes_summary_out():
    from emir.skillgui import MAIN
    assert '"--summary-out"' in MAIN and '"--json"' not in MAIN


def test_check_still_takes_json_for_its_findings(tmp_path):
    p = emir("check", DSPF, "--process", PROC, "--net", "VDD", "--currents", CUR,
             "--json", tmp_path / "f.json")
    assert p.returncode == 0
    assert json.load(open(tmp_path / "f.json"))["schema"] == "emir.check/1"


def test_cwd_does_not_matter(tmp_path):
    d, _ = jrun("nets", os.path.abspath(DSPF), cwd=str(tmp_path))
    assert d["nets"][0]["name"] == "VDD"
    assert ROOT


@pytest.mark.parametrize("text,code", [
    ("layers: [unclosed\n", "EMIR-E301"),
    ("- a list\n", "EMIR-E302"),
    ("em_tref: 100\n", "EMIR-E303"),
    ("em_tref: 100\nlayers:\n  metal1: {tc1: 0.001}\n", "EMIR-E315"),   # no sheet_res
])
def test_a_broken_process_file_is_coded_even_without_the_check(tmp_path, text, code):
    proc = write(tmp_path / "p.yaml", text)
    d, _ = jrun("run", DSPF, "--net", "VDD", "--process", proc, "--currents", CUR,
                "--no-check", rc=2)
    assert d["code"] == code


def test_bad_currents_are_coded_even_without_the_check():
    d, _ = jrun("run", DSPF, "--net", "VDD", "--process", PROC, "--currents", "{VDD: 1}",
                "--no-check", rc=2)
    assert d["code"] == "EMIR-E401"


# ------------------------------------------------------------ usage errors that guide


def test_an_option_of_another_command_names_what_this_one_takes(capsys):
    """An agent guessed `emir run ... --out emir_out` (layout and simulate
    take --out; run does not): the error says what run writes."""
    code = cli.main(["run", DSPF, "--process", PROC, "--out", "emir_out", "--format", "json"])
    d = json.loads(capsys.readouterr().out)
    assert code == 2 and d["code"] == "EMIR-E507"
    assert "--summary-out" in d["message"] and "--out" in d["details"]["did_you_mean"]
    code = cli.main(["run", DSPF, "--summry-out", "x.json", "--format", "json"])
    d = json.loads(capsys.readouterr().out)
    assert "--summary-out" in d["details"]["did_you_mean"]["--summry-out"]


def test_the_html_note_appears_only_when_html_is_given(capsys):
    cli.main(["run", "--format", "json"])
    assert "--html DIR" not in json.loads(capsys.readouterr().out)["message"]
    cli.main(["run", "--html", DSPF, "--format", "json"])
    assert "--html DIR" in json.loads(capsys.readouterr().out)["message"]


def test_check_takes_a_process_file_alone(capsys, tmp_path):
    """A new PDK's process file is checked before any extraction exists."""
    code = cli.main(["check", "--process", os.path.join(ROOT, "pdks", "TEMPLATE.yaml"),
                     "--format", "json"])
    d = json.loads(capsys.readouterr().out)
    assert code == 0 and d["schema"] == "emir.check/1" and d["passed"]
    assert {f["area"] for f in d["findings"]} == {"process"}
    bad = tmp_path / "bad.yaml"
    bad.write_text("pdk: x\nlayers:\n  M1: {sheet_res: 0.1}\n")
    code = cli.main(["check", "--process", str(bad), "--format", "json"])
    d = json.loads(capsys.readouterr().out)
    assert code == 2 and not d["passed"] and any(f["code"] == "EMIR-E312" for f in d["findings"])
