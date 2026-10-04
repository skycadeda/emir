"""Robustness of the command line, one block per case, each with a test: the open-simulator path `emir next` recommends, runnable next steps,
stale summaries, plans the check refuses, a net in pieces, the names
sidecar, process-file typos, exit codes, non-DSPF inputs, report
robustness, and the JSON-mode gaps (`--help`, @argfiles, SIGTERM).

Every number here is invented; the results are synthetic.
"""
import importlib.util
import json
import os
import pty
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time

import jsonschema
import pytest
import yaml
from _helpers import (CUR, DSPF, PROC, ROOT, SPLIT_DSPF, cc_result, device_waves, emir,
                      rcc_result, sample_text, time_base, two_net_dspf, write)

from emir import schemas
from emir import plan as P

VALIDATOR = jsonschema.Draft202012Validator
PLACEHOLDER = re.compile(r"<[A-Za-z][\w.\-]*>")


def validate(doc):
    name = doc["schema"].split(".", 1)[1].split("/")[0]
    VALIDATOR(schemas.get(name)).validate(doc)
    return doc


def jrun(*args, rc=0, cwd=None, env=None):
    p = emir(*args, "--format", "json", cwd=cwd, env=env)
    assert p.returncode == rc, (p.returncode, p.stdout[-1500:], p.stderr[-1500:])
    return validate(json.loads(p.stdout)), p


def run_line(command, cwd, stdin=None):
    """An `emir ...` command line run exactly as written."""
    argv = shlex.split(command)
    assert argv[0] == "emir", command
    return subprocess.run([sys.executable, "-m", "emir", *argv[1:]], cwd=cwd,
                          capture_output=True, text=True, input=stdin,
                          env={"PYTHONPATH": ROOT, "PATH": "/usr/bin:/bin"})


@pytest.fixture
def tools(monkeypatch):
    """Pin which executables `emir info` would find."""
    from emir import site
    found = {}

    def fake(name, explicit=None):
        return (f"/opt/fake/{name}" if found.get(name) else None), "$PATH"
    monkeypatch.setattr(site, "tool", fake)

    def set_(**kw):
        found.clear()
        found.update(kw)
    return set_


def plan_of(**kw):
    f = P.Facts(**kw)
    return f, P.build(f)


def ids(plan):
    return [s["id"] for s in plan.steps]


# ------------------------------------------------------------ SPICE-style results


def nutascii(path, t, sigs) -> str:
    """A transient result in the ASCII raw form Xyce (`-a`) and ngspice write."""
    names = list(sigs)
    L = ["Title: synthetic", "Plotname: Transient Analysis", "Flags: real",
         f"No. Variables: {len(names) + 1}", f"No. Points: {len(t)}", "Variables:",
         "\t0\tTIME\ttime"]
    L += [f"\t{k}\t{n}\tcurrent" for k, n in enumerate(names, 1)]
    L.append("Values:")
    for i, x in enumerate(t):
        L.append(f"{i}\t{x:.12e}")
        L += [f"\t{sigs[n][i]:.12e}" for n in names]
    return write(path, "\n".join(L) + "\n")


def xyce_cc(tmp, sidecar=True, lower_keys=False) -> str:
    """The sample's pin currents as a SPICE simulator names them: through a
    0 V source in series with each pin, I(V_MP0) ..."""
    t = time_base()
    w = device_waves(t)
    raw = nutascii(os.path.join(str(tmp), "cc.raw"), t,
                   {f"I(V_MP{k})": w[f"MP{k}:s"] for k in range(4)})
    if sidecar:
        m = {(f"i(v_mp{k})" if lower_keys else f"I(V_MP{k})"): [f"MP{k}:s", 1]
             for k in range(4)}
        write(raw + ".names", json.dumps({"map": m}))
    return raw


# ======================================================== 1. the open-simulator path


def test_xyce_plan_simulates_in_xyce_syntax_and_writes_the_sidecar(tools):
    # the open-simulator path is `emir simulate` (emir/plan_opensim.py): the
    # lines in Xyce's syntax and the sidecar are written by emir, not the person
    tools(xyce=True)
    _, plan = plan_of(dspf=DSPF, process=PROC, net="VDD", mode="rcc")
    sim = next(s for s in plan.steps if s["id"] == "simulate")
    assert sim["command"].startswith("emir simulate '<testbench.cir>' --sim xyce")
    assert ".print" in sim["why"] and "result.raw.names" in sim["why"]
    assert "names-sidecar" not in ids(plan) and "save-lines" not in ids(plan)
    assert any("--sim xyce --names-out" in a for a in plan.advice)


def test_ngspice_plan_too(tools):
    tools(ngspice=True)
    _, plan = plan_of(dspf=DSPF, process=PROC, net="VDD", mode="rcc")
    sim = next(s for s in plan.steps if s["id"] == "simulate")
    assert "--sim ngspice" in sim["command"] and ".save" in sim["why"]
    assert any("ngspice -b -r" in a for a in plan.advice)


def test_spice_names_without_a_sidecar_are_explained_not_resimulated(tools, tmp_path):
    """The currents are all there under Xyce's names: the plan asks for the
    sidecar (not a new simulation), and the check names the cause."""
    tools(xyce=True)
    raw = xyce_cc(tmp_path, sidecar=False)
    _, plan = plan_of(dspf=DSPF, process=PROC, cc_sim=raw, net="VDD")
    assert ids(plan)[1:] == ["names-sidecar"] and plan.blocked
    m = plan.missing[0]
    assert m["what"].startswith("a names sidecar") and "I(V_MP0)" in m["why"]
    assert "simulate" not in ids(plan) and "check" not in ids(plan)
    d, _ = jrun("check", DSPF, "--process", PROC, "--net", "VDD", "--cc-sim", raw,
                "--mode", "cc-grid", rc=2)
    fail = next(f for f in d["findings"] if f["level"] == "FAIL")
    assert fail["code"] == "EMIR-E416"
    assert "SPICE style (I(V_MP0)" in fail["message"] and "cc.raw.names" in fail["message"]


@pytest.mark.parametrize("lower", [False, True])
def test_with_the_sidecar_a_spice_result_screens_like_spectres(tmp_path, lower):
    """Keys match without regard to case (ngspice writes i(v_mp0))."""
    raw = xyce_cc(tmp_path, lower_keys=lower)
    a, _ = jrun("run", DSPF, "--process", PROC, "--net", "VDD", "--cc-sim", raw,
                "--mode", "cc-grid")
    b, _ = jrun("run", DSPF, "--process", PROC, "--net", "VDD", "--cc-sim",
                cc_result(tmp_path), "--mode", "cc-grid")
    na, nb = a["nets"][0], b["nets"][0]
    assert na["mode"] == nb["mode"] == "cc-grid"
    assert na["max_ratio"]["all"] == pytest.approx(nb["max_ratio"]["all"], rel=1e-9)
    assert na["ir_drop_mV"] == pytest.approx(nb["ir_drop_mV"], rel=1e-9)


def test_the_sidecar_and_currents_formats_have_schemas(tmp_path):
    raw = xyce_cc(tmp_path)
    VALIDATOR(schemas.get("names")).validate(json.load(open(raw + ".names")))
    VALIDATOR(schemas.get("currents")).validate(json.loads(CUR))
    for bad in ({"map": {"I(X)": ["MP0:s"]}}, {"map": {"I(X)": "MP0:s"}}, [1, 2]):
        with pytest.raises(jsonschema.ValidationError):
            VALIDATOR(schemas.get("names")).validate(bad)
    with pytest.raises(jsonschema.ValidationError):
        VALIDATOR(schemas.get("currents")).validate({"VDD": "1mA"})
    p = emir("schema", "names")
    assert p.returncode == 0 and json.loads(p.stdout)["$id"] == "urn:emir:schema:names:1"


# ======================================================== 2. runnable next steps


def _all_next_steps(doc):
    return [s["command"] for s in doc.get("next_steps") or []]


@pytest.mark.parametrize("args,rc", [
    # unbalanced currents, no --net: the pins step names the DSPF's one net
    (["check", "{dspf}", "--process", "{proc}", "--currents", '{"VDD": 1}'], 2),
    # no process file: `emir next --dspf ...` without a <process> placeholder
    (["check", "{dspf}", "--currents", "{cur}"], 2),
    (["run", "{dspf}", "--currents", "{cur}"], 2),
    (["pins", "{dspf}", "--net", "vdd"], 2),
    (["check", "{dspf}", "--process", "{proc}", "--net", "NOPE", "--currents", "{cur}"], 2),
    (["report", "--from", "{dspf}"], 2),
    (["explain"], 2),
    # a simulated run with no mode: E520's step is `emir next`, which lists them
    (["run", "{dspf}", "--process", "{proc}", "--cc-sim", "{dspf}"], 2),
])
def test_no_next_step_holds_a_placeholder_and_each_runs(tmp_path, args, rc):
    d = tmp_path / "my dir"
    d.mkdir()
    dspf, proc = shutil.copy(DSPF, d / "rail.dspf"), shutil.copy(PROC, d / "pdk.yaml")
    sub = {"{dspf}": str(dspf), "{proc}": str(proc), "{cur}": CUR}
    argv = [sub.get(a, a) for a in args]
    doc, _ = jrun(*argv, rc=rc, cwd=d)
    steps = _all_next_steps(doc)
    assert steps, doc
    for c in steps:
        assert not PLACEHOLDER.search(c), c
        if c.startswith("emir "):
            p = run_line(c, d)
            assert "EMIR-E507" not in p.stderr and "EMIR-E508" not in p.stderr, c
            assert p.returncode in (0, 2), (c, p.stderr)


def test_unbalanced_currents_name_the_net_in_the_fix(tmp_path):
    doc, _ = jrun("check", DSPF, "--process", PROC, "--currents", '{"VDD": 1}', rc=2)
    assert f"emir pins {DSPF} --net VDD --format json" in _all_next_steps(doc)


def test_check_without_a_process_suggests_next_without_placeholders():
    doc, _ = jrun("check", DSPF, "--currents", CUR, rc=2)
    assert f"emir next --dspf {DSPF} --format json" in _all_next_steps(doc)


def test_report_from_stdin_on_a_terminal_is_refused_at_once():
    """It once blocked forever waiting for a terminal to send JSON."""
    master, slave = pty.openpty()
    try:
        t0 = time.time()
        p = subprocess.run([sys.executable, "-m", "emir", "report", "--from", "-",
                            "--format", "json"], stdin=slave, capture_output=True,
                           text=True, timeout=60, env={"PYTHONPATH": ROOT})
    finally:
        os.close(master)
        os.close(slave)
    assert time.time() - t0 < 30
    d = validate(json.loads(p.stdout))
    assert p.returncode == 2 and d["code"] == "EMIR-E103"
    assert _all_next_steps(d) == ["emir report --from emir_out/summary.json --format json"]


def test_plan_steps_say_when_they_cannot_run_as_printed(tools):
    tools(spectre=True)
    _, plan = plan_of(dspf=DSPF, process=PROC, net="VDD", mode="rcc")
    by = {s["id"]: s for s in plan.steps}
    assert by["simulate"]["status"] == "waiting" and by["simulate"]["needs_human"]
    assert set(by["simulate"]["placeholders"]) == {"<result.raw>", "<testbench.scs>"}
    for k in ("check", "run"):
        assert by[k]["status"] == "waiting" and by[k]["placeholders"] == ["<result.raw>"]
    assert by["check"]["after"] == ["simulate"]
    assert by["report"]["status"] == "todo" and by["report"]["after"] == ["run"]
    for s in plan.steps:
        assert (s["status"] == "waiting") == bool(PLACEHOLDER.search(s["command"])), s
    assert any("stop at the first that exits non-zero" in a for a in plan.advice)


# ======================================================== 3. stale summaries


def test_a_refused_run_replaces_the_previous_summary(tmp_path):
    out = tmp_path / "s.json"
    jrun("run", DSPF, "--process", PROC, "--net", "VDD", "--currents", CUR,
         "--summary-out", out)
    first = json.load(open(out))
    assert first["schema"] == "emir.run/1" and first["created"].endswith("Z")
    assert first["command"].startswith("emir run ")
    # the same --summary-out, refused (an unknown net): the old summary must go
    jrun("run", DSPF, "--process", PROC, "--net", "NOPE", "--currents", CUR,
         "--summary-out", out, rc=2)
    err = validate(json.load(open(out)))
    assert err["schema"] == "emir.error/1" and err["code"] == "EMIR-E501"
    d, _ = jrun("report", "--from", out, rc=2)
    assert d["code"] == "EMIR-E104" and "EMIR-E501" in d["message"]
    assert d["details"]["run_error"]["code"] == "EMIR-E501"


def test_the_report_names_its_run(tmp_path):
    out = tmp_path / "s.json"
    jrun("run", DSPF, "--process", PROC, "--net", "VDD", "--currents", CUR,
         "--summary-out", out)
    d, _ = jrun("report", "--from", out)
    s = json.load(open(out))
    assert d["command"] == s["command"] and d["created"] == s["created"]
    assert f"- Run: `{s['command']}`, finished {s['created']}" in d["markdown"]


# ======================================================== 4. plans the check accepts


def test_cc_grid_asked_with_no_result_plans_cc_saves(tools):
    tools(spectre=True)
    _, plan = plan_of(dspf=DSPF, process=PROC, net="VDD", mode="cc-grid")
    by = {s["id"]: s for s in plan.steps}
    assert plan.mode["chosen"] == "cc-grid"
    assert "--mode cc-grid " in by["save-lines"]["command"] + " "
    assert "--mode cc-grid" in by["check"]["command"]


def test_cc_grid_asked_on_an_rcc_result_is_never_checked_in_cc_grid(tools, tmp_path):
    tools(spectre=True)
    rcc = rcc_result(tmp_path)
    _, plan = plan_of(dspf=DSPF, process=PROC, net="VDD", mode="cc-grid", cc_sim=rcc)
    assert not any(rcc in s["command"] for s in plan.steps)
    assert "--mode cc" in next(s for s in plan.steps if s["id"] == "save-lines:VDD")["command"]
    assert any("can screen VDD in rcc mode" in a for a in plan.advice)


def _split(tmp_path):
    d = write(tmp_path / "split.dspf", SPLIT_DSPF)
    t = time_base()
    from emir.validate import _psf_tran
    cc = _psf_tran(str(tmp_path / "cc.tran"), t,
                   {"XA:VDDA": 1e-3 + 0 * t, "XB:VDDB": 2e-3 + 0 * t})
    branches = {f"Rt{k}:1": v for k, v in
                enumerate([3e-3 + 0 * t, 1e-3 + 0 * t, 1e-3 + 0 * t, 1e-3 + 0 * t,
                           1e-3 + 0 * t])}
    rcc = _psf_tran(str(tmp_path / "rcc.tran"), t, branches)
    proc = shutil.copy(PROC, tmp_path / "p.yaml")
    return d, proc, cc, rcc


def test_split_pins_with_a_cc_result_plan_rcc(tools, tmp_path):
    """cc-grid would FAIL E417 and the result has no branch currents: neither
    mode can screen it as it is; with the person's rcc the plan asks for the
    rcc saves."""
    tools(spectre=True)
    d, proc, cc, _ = _split(tmp_path)
    _, plan = plan_of(dspf=d, process=proc, cc_sim=cc, net="VDD")
    c = {x["route"]: x for x in plan.steps[0]["choices"]}
    assert not c["rcc"]["possible"] and not c["cc-grid"]["possible"]
    assert "EMIR-E417" in c["cc-grid"]["why_not"]
    _, plan = plan_of(dspf=d, process=proc, cc_sim=cc, net="VDD", mode="rcc")
    save = next(s for s in plan.steps if s["id"] == "save-lines:VDD")
    assert "--mode rcc" in save["command"]
    assert "1 pin(s) at several connections" in plan.missing[0]["why"]
    assert not any(cc in s["command"] for s in plan.steps)


def test_split_pins_with_an_rcc_result_check_and_run(tools, tmp_path):
    tools(spectre=True)
    d, proc, _, rcc = _split(tmp_path)
    _, plan = plan_of(dspf=d, process=proc, cc_sim=rcc, net="VDD", mode="rcc")
    assert plan.mode["chosen"] == "rcc"
    check = next(s for s in plan.steps if s["id"] == "check")
    assert check["status"] == "todo"
    p = run_line(check["command"], tmp_path)
    assert p.returncode == 0, p.stdout + p.stderr


def test_split_pins_with_cc_grid_asked_are_blocked(tools, tmp_path):
    tools(spectre=True)
    d, proc, cc, _ = _split(tmp_path)
    for kw in ({}, {"cc_sim": cc}):
        _, plan = plan_of(dspf=d, process=proc, net="VDD", mode="cc-grid", **kw)
        assert plan.blocked and "EMIR-E417" in plan.missing[-1]["why"]
        assert "check" not in ids(plan) and "run" not in ids(plan)


def test_split_pins_with_no_result_plan_rcc_saves(tools, tmp_path):
    tools(spectre=True)
    d, proc, _, _ = _split(tmp_path)
    _, plan = plan_of(dspf=d, process=proc, net="VDD")
    c = {x["route"]: x for x in plan.steps[0]["choices"]}
    assert c["rcc"]["recommended"] and not c["cc-grid"]["possible"]
    assert "Only rcc can work" in plan.steps[0]["why"]
    _, plan = plan_of(dspf=d, process=proc, net="VDD", mode="rcc")
    assert plan.mode["chosen"] == "rcc"
    assert "--mode rcc" in next(s for s in plan.steps if s["id"] == "save-lines")["command"]


# ======================================================== 5. a net in pieces


def _cut(tmp_path):
    """The sample with Rm7 removed: MP2 and MP3 hang off a second piece."""
    text = "\n".join(ln for ln in sample_text().splitlines() if not ln.startswith("Rm7 "))
    return write(tmp_path / "cut.dspf", text + "\n")


def test_a_forced_run_on_a_net_in_pieces_gives_no_invented_numbers(tmp_path):
    cut = _cut(tmp_path)
    out = tmp_path / "cut.json"
    d, p = jrun("run", cut, "--process", PROC, "--currents", CUR, "--force",
                "--summary-out", out)
    n = d["nets"][0]
    # the IR of the reference's piece: the feed through Rm1-6 and Rc1 to MP1
    # (2 mA on Rm1-3, 1 mA on Rm4-6 and Rc1, 1 ohm wires and a 12 ohm contact)
    # (2 mA x 3 ohm + 1 mA x 3 ohm + 1 mA x 12 ohm = 21 mV; once 9e15 mV)
    assert n["ir_drop_mV"] == pytest.approx(21.0, rel=1e-9)
    assert abs(n["kcl_residual_A"]) < 1e-12           # once 2.9 mA of 4 mA
    assert n["verdict"] == "FAIL" and n["unsolved"]["n_nodes"] == 8
    unsolved = [i for i in n["worst_items"] if i["ratio"] is None]
    assert {i["name"] for i in unsolved} >= {"Rc2", "Rc3"}
    assert all(i["severity"] == "FAIL" and "not a number" in i["fix"] for i in unsolved)
    # the most severe first: the unsolved before any number
    assert n["worst_items"][0]["ratio"] is None and d["worst_net"]["ratio"] is None
    assert "EMIR-W208" in {x["code"] for x in d["notices"]}
    assert "WARNING EMIR-W208" in p.stderr
    r, _ = jrun("report", "--from", out)
    assert "| VDD | FAIL | dc | avg | not a number |" in r["markdown"]
    assert any("the net is in pieces" in x for x in r["not_checked"])


def test_a_balanced_piece_is_solved_on_its_own(tmp_path):
    """Current that enters and leaves inside a piece flows there: solved."""
    from emir.dspf import Network, parse
    d = parse(_cut(tmp_path))
    nw = Network(d, "VDD")
    inj = {"VDD": 2e-3, "MP0:s": -1e-3, "MP1:s": -1e-3, "MP2:s": 5e-4, "MP3:s": -5e-4}
    V, Ib = nw.solve(inj, "VDD")
    names = [r["name"] for r in nw.res]
    import numpy as np
    assert np.all(np.isfinite(Ib))
    assert abs(Ib[names.index("Rm10")]) == pytest.approx(5e-4)
    assert np.isnan(V[nw.idx["MP3:s"]]) and V[nw.idx["VDD"]] == 0.0


# ======================================================== 6. the names sidecar


@pytest.mark.parametrize("text", ['[1, 2]', '{"map": {"I(V_MP0)": "MP0:s"}}',
                                  '{"map": {"I(V_MP0)": ["MP0:s", "1"]}}', '{not json',
                                  '{"map": []}'])
def test_a_malformed_sidecar_is_coded_and_named(tmp_path, text):
    raw = xyce_cc(tmp_path, sidecar=False)
    write(raw + ".names", text)
    d, _ = jrun("run", DSPF, "--process", PROC, "--net", "VDD", "--cc-sim", raw,
                "--mode", "cc-grid", "--no-check", rc=2)
    assert d["code"] == "EMIR-E424" and "cc.raw.names" in d["message"]
    assert _all_next_steps(d) == ["emir schema names --format json"]
    d, _ = jrun("check", DSPF, "--process", PROC, "--net", "VDD", "--cc-sim", raw,
                "--mode", "cc-grid", rc=2)
    assert "EMIR-E424" in [f["code"] for f in d["findings"]]
    d, _ = jrun("next", "--dspf", DSPF, "--process", PROC, "--cc-sim", raw)
    assert "EMIR-E424" in d["missing"][0]["why"]


# ======================================================== 7. process-file typos


def test_a_misspelt_limit_fails_the_check(tmp_path):
    data = yaml.safe_load(open(PROC))
    data["layers"]["Metal1"]["jmax_peek"] = data["layers"]["Metal1"].pop("jmax_peak")
    data["warn_fracton"] = 0.5
    p = write(tmp_path / "typo.yaml", yaml.safe_dump(data))
    d, _ = jrun("check", DSPF, "--process", p, "--net", "VDD", "--currents", CUR, rc=2)
    fails = [f for f in d["findings"] if f["code"] == "EMIR-E316"]
    assert len(fails) == 1 and "jmax_peek" in fails[0]["message"]
    assert "did you mean 'jmax_peak'" in fails[0]["message"]
    warn = [f for f in d["findings"] if f["code"] == "EMIR-W314"]
    assert warn and "warn_fracton" in warn[0]["message"]
    # the run refuses it too
    d, _ = jrun("run", DSPF, "--process", p, "--net", "VDD", "--currents", CUR, rc=2)
    assert "EMIR-E316" in d["details"]["causes"]


def test_shipped_process_files_have_no_unknown_keys():
    import glob

    from emir.check import Report, check_process
    for path in [PROC] + sorted(glob.glob(os.path.join(ROOT, "pdks", "*.yaml"))):
        rep = Report()
        check_process(path, rep)
        assert not [f for f in rep.findings if f.code in ("EMIR-E316", "EMIR-W314")], path


# ======================================================== 8. codes and exit statuses


def test_a_simulator_timeout_is_coded(tmp_path):
    from emir.ccsim import run_spectre
    from emir.errors import EmirError
    fake = write(tmp_path / "spectre", "#!/bin/sh\nsleep 5\n")
    os.chmod(fake, 0o755)
    net = write(tmp_path / "tb.scs", "// nothing\n")
    with pytest.raises(EmirError) as e:
        run_spectre(net, spectre=fake, timeout=0.5, log=lambda *_: None)
    assert e.value.code == "EMIR-E605" and e.value.exit_code == 2
    assert e.value.next_steps()[0]["command"].startswith("tail -n 40 ")


def test_an_unwritable_output_is_a_write_error(tmp_path):
    from test_import_ict import EMIR_YAML, FAKE_ICT
    ict = write(tmp_path / "d.ict", FAKE_ICT % {"m1": "metal1_conn"})
    src = write(tmp_path / "emir.yaml", yaml.safe_dump(EMIR_YAML))
    d, _ = jrun("import-ict", "--ict", ict, "--emir", src, "-o",
                tmp_path / "no" / "such" / "dir" / "out.yaml")
    assert os.path.isfile(tmp_path / "no" / "such" / "dir" / "out.yaml")  # created
    blocker = write(tmp_path / "file", "x\n")
    d, _ = jrun("import-ict", "--ict", ict, "--emir", src, "-o", f"{blocker}/out.yaml", rc=2)
    assert d["code"] == "EMIR-E105" and "cannot write" in d["message"]
    d, _ = jrun("run", DSPF, "--process", PROC, "--net", "VDD", "--cc-sim",
                cc_result(tmp_path), "--mode", "cc-grid", "--taps-out", f"{blocker}/t.csv",
                "--no-check", rc=2)
    assert d["code"] == "EMIR-E105"


# ======================================================== 9. inputs of the wrong kind


@pytest.mark.parametrize("what", ["yaml", "binary", "empty"])
def test_a_file_that_is_not_a_dspf_is_refused(tmp_path, what):
    path = {"yaml": lambda: PROC,
            "binary": lambda: str(_bytes(tmp_path / "x.raw", b"Title\0\0\0")),
            "empty": lambda: write(tmp_path / "e.dspf", "\n")}[what]()
    d, _ = jrun("nets", path, rc=2)
    assert d["code"] == "EMIR-E217"
    d, _ = jrun("next", "--dspf", path)
    assert d["missing"][0]["what"] == "a readable DSPF" and "EMIR-E217" in d["missing"][0]["why"]


def _bytes(path, data):
    path.write_bytes(data)
    return path


def test_import_ict_refuses_a_file_that_is_not_an_ict(tmp_path):
    src = write(tmp_path / "emir.yaml", open(PROC).read())
    out = tmp_path / "out.yaml"
    d, _ = jrun("import-ict", "--ict", DSPF, "--emir", src, "-o", out, rc=2)
    assert d["code"] == "EMIR-E104" and "not an ICT" in d["message"]
    assert not out.exists()


# ======================================================== 10. report robustness


def _summary(tmp_path):
    out = tmp_path / "s.json"
    jrun("run", DSPF, "--process", PROC, "--net", "VDD", "--currents", CUR,
         "--summary-out", out)
    return json.load(open(out))


def test_null_numbers_are_reported_as_not_a_number(tmp_path):
    s = _summary(tmp_path)
    n = s["nets"][0]
    n["ir_drop_mV"] = None
    n["max_ratio"]["all"] = None
    n["worst_items"][0]["ratio"] = None
    s["worst_net"]["ratio"] = None
    validate(s)                                  # the schema allows the nulls
    path = write(tmp_path / "null.json", json.dumps(s))
    d, _ = jrun("report", "--from", path)
    md = d["markdown"]
    assert "| VDD | FAIL | dc | avg | not a number |" in md and "not a number drop |" in md
    assert "worst not a number on VDD" in md and "- VDD: not a number (from the feed)" in md
    assert "0.000 mV" not in md and "0.000x" not in md


def test_run_warnings_are_not_input_check_warnings(tmp_path):
    s = _summary(tmp_path)
    s["notices"].append({"code": "EMIR-W413", "level": "warning",
                         "message": "the window is not settled"})
    path = write(tmp_path / "w.json", json.dumps(s))
    d, _ = jrun("report", "--from", path)
    assert d["run_warnings"] == ["[EMIR-W413] the window is not settled"]
    assert not any("W413" in w for w in d["input_warnings"])
    md = d["markdown"]
    assert md.index("## Warnings raised during the run") < md.index("[EMIR-W413]")


# ======================================================== 11. the smaller gaps


def test_viewer_without_klayout_says_so(tmp_path, monkeypatch):
    import builtins
    real = builtins.__import__

    def no_klayout(name, *a, **kw):
        if name.startswith("klayout"):
            raise ImportError("no klayout")
        return real(name, *a, **kw)
    monkeypatch.setattr(builtins, "__import__", no_klayout)
    from emir.dspf import analyse, build_parser
    from emir.errors import collecting
    a = build_parser().parse_args([DSPF, "--net", "VDD", "--process", PROC, "--currents", CUR,
                                   "--viewer", str(tmp_path / "v"), "--lib", "L"])
    a.argv, a.argv_text = [], ""
    with collecting():
        res = analyse(a)
    assert "EMIR-W703" in {x["code"] for x in res.notices} and res.viewer["rdb"] is None


def test_next_knows_when_klayout_is_missing(tools, monkeypatch):
    tools()
    import importlib.util
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda n, *a: None if n == "klayout" else real(n, *a))
    _, plan = plan_of(dspf=DSPF, process=PROC, currents=CUR, viewer=True)
    assert any("EMIR-W703" in a for a in plan.advice)
    assert not any("still writes the KLayout marker" in a for a in plan.advice)


def test_the_viewers_say_screening_not_sign_off(tmp_path):
    d, _ = jrun("run", DSPF, "--process", PROC, "--net", "VDD", "--currents", CUR,
                "--viewer", tmp_path / "v", "--lib", "L")
    if importlib.util.find_spec("klayout") is not None:
        rdb = open(d["viewer"]["rdb"], encoding="utf-8").read()
        assert "Screening, not sign-off." in rdb
    else:                       # no klayout package: no marker database, and W703 says so
        assert d["viewer"].get("rdb") is None
        assert "EMIR-W703" in {n["code"] for n in d["notices"]}
    data = open(d["viewer"]["data"], encoding="utf-8").read()
    assert "Screening, not sign-off." in data
    from emir.skillapp import APP
    assert "(screening, not sign-off)" in APP


@pytest.mark.parametrize("argv", [["run", "--help"], ["next", "-h"], ["--help"], []])
def test_help_in_json_mode_is_one_document(argv):
    p = emir(*argv, "--format", "json")
    assert p.returncode == 0
    d = validate(json.loads(p.stdout))
    assert d["schema"] == "emir.help/1" and d["text"].strip()
    if argv[:1] == ["run"]:
        assert d["command"] == "run" and d["text"].startswith("usage: emir run")
        assert "--summary-out" in d["text"]
    if not argv[:1] or argv[0] == "--help":
        assert d["command"] is None and "emir check" in d["text"]


def test_format_json_in_an_argfile_is_honoured(tmp_path):
    args = write(tmp_path / "a.txt", "--format\njson\n--bogus\n")
    p = emir("run", DSPF, f"@{args}")
    assert p.returncode == 2
    d = validate(json.loads(p.stdout))
    assert d["code"] == "EMIR-E507"


def test_sigterm_ends_with_a_coded_document(tmp_path):
    p = subprocess.Popen([sys.executable, "-m", "emir", "validate", "--format", "json",
                          "--out", str(tmp_path / "v")], stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, env={"PYTHONPATH": ROOT,
                                                                 "PATH": os.environ["PATH"]})
    time.sleep(2.0)
    p.send_signal(signal.SIGTERM)
    out, _err = p.communicate(timeout=120)
    assert p.returncode == 143
    d = validate(json.loads(out))
    assert d["code"] == "EMIR-E906" and d["exit_code"] == 143


def test_em_temp_and_delta_t_have_help():
    p = emir("run", "--help")
    text = " ".join(p.stdout.split())
    assert "--em-temp C the temperature (C) to analyse EM at" in text
    assert "--delta-t C the self-heating temperature rise" in text


def test_pins_on_a_net_without_pins_is_a_coded_notice(tmp_path):
    text = sample_text().replace(".ENDS", "*|NET LONE 0\n*|P (LONE I *1 0 9)\n"
                                 "Rl1 LONE LONE:1 1.0 $metal1 $L=1 $W=1\n.ENDS")
    p = emir("pins", write(tmp_path / "lone.dspf", text), "--net", "LONE")
    assert p.returncode == 0 and "WARNING EMIR-W205" in p.stderr


def test_run_on_a_two_net_dspf_still_reports_both(tmp_path):
    # a guard on the per-piece solve: whole nets are solved exactly as before
    d, _ = jrun("run", two_net_dspf(tmp_path), "--process", PROC, "--net", "VDD",
                "--currents", CUR)
    n = d["nets"][0]
    assert n["max_ratio"]["all"] == pytest.approx(5 / 3) and n["unsolved"] is None
    assert n["ir_drop_mV"] == pytest.approx(54.0)
