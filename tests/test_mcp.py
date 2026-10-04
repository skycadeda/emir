"""`emir mcp`: the MCP server (emir/mcp_server.py) and its tools
(emir/mcp_tools.py).

The first part needs no MCP SDK: the tools are built from the CLI's own
parsers, their arguments become exactly the command line the CLI takes, the
go-ahead gate refuses what needs the person, and `emir mcp --list` keeps the
one-document contract. The second part (skipped without the `mcp` package)
connects a real MCP client -- in-process, and over stdio to `python -m emir
mcp` -- and validates every tool result against its emir schema and against
the output schema the tool advertises. The tests run with the SDK's 1.x
(`FastMCP`) and 2.x (`MCPServer`) lines alike.
"""
import json
import os
import shlex
import subprocess
import sys
from contextlib import asynccontextmanager

import jsonschema
import pytest
from _helpers import CUR, DSPF, PROC, ROOT

from emir import cli, mcp_server, mcp_tools, schemas
from emir.errors import catalog_markdown

V = jsonschema.Draft202012Validator


def validate_doc(doc):
    """Validate a document against the emir schema its `schema` field names."""
    name = doc["schema"].split(".", 1)[1].split("/")[0]
    V(schemas.get(name)).validate(doc)
    return doc


def jcli(*args, rc=0, env=None, cwd=None):
    p = subprocess.run([sys.executable, "-m", "emir", *map(str, args), "--format", "json"],
                       capture_output=True, text=True, cwd=cwd or ROOT,
                       env={**os.environ, "PYTHONPATH": ROOT, **(env or {})})
    assert p.returncode == rc, (p.returncode, p.stdout[-2000:], p.stderr[-2000:])
    return validate_doc(json.loads(p.stdout)), p


# ------------------------------------------------------------ the tools, from the CLI


def test_every_command_but_mcp_is_a_tool_and_emir_cli_comes_last():
    specs = mcp_tools.specs()
    want = {mcp_tools.tool_name(c) for c in cli.COMMANDS if c != "mcp"
            and c not in cli.SUBCOMMANDS}
    # a command made of sub-commands is one tool per sub-command
    want |= {mcp_tools.tool_name(f"{c} {x}") for c, subs in cli.SUBCOMMANDS.items()
             for x in subs}
    assert set(specs) == want | {"emir_cli"}
    assert {"emir_process_new", "emir_process_check"} <= set(specs)
    assert mcp_tools.served_commands() == sorted(s.command for s in specs.values()
                                                 if s.command)
    assert list(specs)[-1] == "emir_cli"
    for name, s in specs.items():
        assert name.startswith("emir_") and s.description and s.title
        V.check_schema(s.input_schema())
        assert s.input_schema()["additionalProperties"] is False


def test_tool_parameters_are_the_cli_options():
    run = mcp_tools.specs()["emir_run"]
    names = {p.name: p for p in run.params}
    assert names["dspf"].required and names["dspf"].flag is None
    assert names["cc_sim"].flag == "--cc-sim" and names["cc_sim"].kind == "string"
    assert names["period"].kind == "number" and names["max_passes"].kind == "integer"
    assert names["mode"].choices == ("rcc", "cc-grid") and not names["mode"].required
    # required with a simulation result or a netlist (the person's choice), as
    # draft-07 and 2020-12 validators both read it
    schema = run.input_schema()
    assert schema["anyOf"] == [{"required": ["mode"]},
                               {"not": {"anyOf": [{"required": ["cc_sim"]},
                                                  {"required": ["netlist"]}]}}]
    for name in ("emir_simulate", "emir_save_lines", "emir_abstract"):
        spec = mcp_tools.specs()[name]
        assert "mode" in spec.input_schema()["required"], name
        assert {p.name: p for p in spec.params}["mode"].choices == ("rcc", "cc-grid")
    assert names["fail_on_violation"].kind == "flag"
    # hidden: --format (always json), --help, --open (a browser on the server),
    # --rcc-nodes (kept for old argument files), --json (suppressed alias)
    assert not {"format", "help", "open", "rcc_nodes", "json"} & set(names)
    nxt = {p.name: p for p in mcp_tools.specs()["emir_next"].params}
    assert nxt["have_xyce"].kind == "tristate" and nxt["have_xyce"].neg_flag == "--no-have-xyce"
    rep = {p.name: p for p in mcp_tools.specs()["emir_report"].params}
    assert rep["from_file"].flag == "--from" and rep["from_file"].required
    assert mcp_tools.specs()["emir_explain"].params[0].kind == "array"


def _sample_value(p):
    if p.kind == "flag":
        return True
    if p.kind == "tristate":
        return False
    if p.example is not None:                 # an option with its own type (`--max-memory`)
        return p.example
    v = {"number": 1.5, "integer": 3}.get(p.kind if p.kind != "array" else p.item, "x y")
    if p.choices:
        v = p.choices[-1]
    return [v, v] if p.kind == "array" else v


@pytest.mark.parametrize("name", sorted(set(mcp_tools.specs()) - {"emir_cli"}))
def test_each_parameter_reaches_its_option(name):
    """Every parameter, alone, becomes the argv the CLI parses into that
    option's dest (so a tool can never pass an option the CLI lacks)."""
    spec = mcp_tools.specs()[name]
    required = {p.name: _sample_value(p) for p in spec.params if p.required}
    for p in spec.params:
        args = {**required, p.name: _sample_value(p)}
        argv = mcp_tools.to_argv(spec, args)
        ns = spec.parser.parse_args(argv)
        got = getattr(ns, p.dest)
        want = _sample_value(p)
        # an option with its own type stores what the type reads ("4G" -> bytes)
        typed = next(a.type for a in spec.parser._actions if a.dest == p.dest) \
            if p.example is not None else (lambda v: v)
        if p.kind == "flag":
            assert got != p.default, (p.name, argv)
        else:
            assert got == typed(want), (p.name, argv, got)
        # and back: the namespace stands for the same arguments
        back = mcp_tools.from_namespace(spec, ns).get(p.name)
        assert (typed(str(back)) if p.example is not None else back) == (
            True if p.kind == "flag" else typed(want))


def test_values_never_read_as_options_or_argument_files(tmp_path):
    spec = mcp_tools.specs()["emir_pins"]
    argv = mcp_tools.to_argv(spec, {"dspf": "-x.dspf", "net": "--force"})
    assert argv == ["./-x.dspf", "--net=--force"]
    assert spec.parser.parse_args(argv).net == "--force"
    argv = mcp_tools.to_argv(spec, {"dspf": "@args.txt", "net": "@VDD"})
    assert argv == ["./@args.txt", "--net=@VDD"]
    assert mcp_tools.to_argv(mcp_tools.specs()["emir_explain"], {"codes": ["-x", "E101"]}) \
        == ["./-x", "E101"]
    # a list given after one option cannot use the --opt=value form: refused
    spec = mcp_tools.ToolSpec(
        name="emir_x", command="x", title="x", description="x", outputs=None,
        confirm="never", params=[mcp_tools.Param(name="many", dest="many", flag="--many",
                                                 kind="array", help="")])
    assert mcp_tools.to_argv(spec, {"many": ["a", "b"]}) == ["--many", "a", "b"]
    with pytest.raises(mcp_tools.EmirError) as e:
        mcp_tools.to_argv(spec, {"many": ["a", "-b"]})
    assert e.value.code == "EMIR-E513"


def test_annotations_and_gates():
    s = mcp_tools.specs()
    assert s["emir_info"].annotations["readOnlyHint"] is True
    assert s["emir_import_ict"].annotations["destructiveHint"] is True
    assert s["emir_run"].annotations["readOnlyHint"] is False
    # every tool that writes where the caller says can overwrite a file the
    # person owns: it takes confirm, and is neither read-only nor idempotent
    assert [k for k, v in s.items() if v.confirm != "never"] == \
        ["emir_abstract", "emir_check", "emir_extract", "emir_gui_install",
         "emir_import_ict", "emir_probe", "emir_process_new",
         "emir_run", "emir_save_lines", "emir_selftest", "emir_simulate", "emir_validate",
         "emir_verify_sim", "emir_cli"]
    assert sorted(k for k, v in s.items() if v.annotations["readOnlyHint"]) == \
        ["emir_explain", "emir_info", "emir_nets", "emir_next", "emir_pins",
         "emir_process_check", "emir_report", "emir_schema"]
    for k, v in s.items():
        ro = v.annotations["readOnlyHint"]
        assert ro == v.annotations["idempotentHint"] and ro != v.annotations["destructiveHint"]
        if ro:       # a read-only tool has no parameter that names an output
            assert not any(p.dest in mcp_tools.OUTPUT_DESTS for p in v.params) \
                or v.command in mcp_tools.PLAN_ONLY, k
            assert "Read-only" not in v.description or not v.writes
    assert "Read-only" not in s["emir_save_lines"].description
    for k in ("emir_run", "emir_import_ict", "emir_cli", "emir_save_lines"):
        props = s[k].input_schema()["properties"]
        assert props["confirm"]["type"] == "boolean" and props["confirm"]["default"] is False
        assert "ASK THE PERSON FIRST" in s[k].description
    assert "confirm" not in s["emir_nets"].input_schema()["properties"]


def test_overwriting_a_file_outside_the_working_tree_needs_the_go_ahead(monkeypatch,
                                                                        tmp_path):
    """emir_save_lines was annotated read-only and overwrote any `out`: a
    file outside the server's working directory is now refused without
    confirm; one inside it (the call's own output) is not."""
    work, elsewhere = tmp_path / "work", tmp_path / "elsewhere"
    work.mkdir()
    elsewhere.mkdir()
    victim = elsewhere / "victim.txt"
    victim.write_text("the person's file\n")
    monkeypatch.chdir(work)
    spec = mcp_tools.by_command("save-lines")
    assert spec.annotations["readOnlyHint"] is False
    rc, doc, _ = mcp_tools.call(spec, {"dspf": DSPF, "out": str(victim)})
    assert (rc, doc["code"]) == (2, "EMIR-E592"), doc            # mode is required
    assert doc["details"]["missing"] == ["mode"]
    rc, doc, _ = mcp_tools.call(spec, {"dspf": DSPF, "mode": "rcc", "out": str(victim)})
    assert (rc, doc["code"]) == (2, "EMIR-E590"), doc
    assert doc["details"]["reasons"][0]["rewrites"] == str(victim)
    assert victim.read_text() == "the person's file\n"
    rc, doc, _ = mcp_tools.call(spec, {"dspf": DSPF, "mode": "rcc", "out": str(victim),
                                       "confirm": True})
    assert rc == 0 and victim.read_text() != "the person's file\n"
    # inside the working directory: the call's own output, no go-ahead
    rc, doc, _ = mcp_tools.call(spec, {"dspf": DSPF, "mode": "rcc",
                                       "out": "emir_out/save.txt"})
    assert rc == 0 and (work / "emir_out" / "save.txt").is_file()
    rc, doc, _ = mcp_tools.call(spec, {"dspf": DSPF, "mode": "rcc",
                                       "out": "emir_out/save.txt"})
    assert rc == 0


def test_run_needs_the_go_ahead_for_spectre_and_runs_with_it(monkeypatch, tmp_path):
    monkeypatch.setenv("SPECTRE_BIN", "/nonexistent/spectre")
    monkeypatch.setenv("EMIR_SITE", os.devnull)
    monkeypatch.chdir(tmp_path)
    net = tmp_path / "tb.scs"
    net.write_text("// a netlist\n")
    run = mcp_tools.specs()["emir_run"]
    args = {"dspf": DSPF, "process": PROC, "net": "VDD", "netlist": str(net),
            "mode": "cc-grid"}
    # without run: Spectre is licensed, so the run only plans (no go-ahead)
    rc, doc, line = mcp_tools.call(run, args)
    validate_doc(doc)
    assert rc == 0 and doc["schema"] == "emir.simulate/1" and doc["ran"] is False
    args["run"] = True
    rc, doc, line = mcp_tools.call(run, args)
    validate_doc(doc)
    assert (rc, doc["code"], doc["exit_code"]) == (2, "EMIR-E590", 2)
    assert doc["details"]["licensed_tool"] == "spectre" and doc["details"]["long_run"]
    assert doc["next_steps"] == []
    assert "--netlist" in line and line.endswith("--format json")
    # with the go-ahead the run starts -- and stops at the missing simulator
    rc, doc, _ = mcp_tools.call(run, {**args, "confirm": True})
    validate_doc(doc)
    assert rc == 3 and doc["code"] == "EMIR-E601"
    # the open simulators re-simulate in the self-consistent loop: ask first too
    rc, doc, _ = mcp_tools.call(run, {**args, "run": False, "sim": "xyce",
                                      "self_consistent": True, "vdd": 1.0})
    assert doc["code"] == "EMIR-E590" and doc["details"]["licensed_tool"] is None


def test_big_blocks_need_the_go_ahead(monkeypatch):
    from emir import plan
    monkeypatch.setitem(plan.THRESHOLDS, "big_block_nodes", 5)      # the sample has 17
    run = mcp_tools.specs()["emir_run"]
    rc, doc, _ = mcp_tools.call(run, {"dspf": DSPF, "process": PROC, "currents": CUR})
    assert doc["code"] == "EMIR-E590" and doc["details"]["long_run"] is True
    assert doc["details"]["licensed_tool"] is None
    # printing the save lines is not a run
    rc, doc, _ = mcp_tools.call(run, {"dspf": DSPF, "save_branches": True})
    assert rc == 0 and doc["schema"] == "emir.save_lines/1"


def test_import_ict_in_place_needs_the_go_ahead(tmp_path):
    spec = mcp_tools.specs()["emir_import_ict"]
    ict = tmp_path / "x.ict"                 # never read: the gate comes first
    rc, doc, _ = mcp_tools.call(spec, {"ict": str(ict), "emir": PROC})
    assert doc["code"] == "EMIR-E590" and doc["details"]["reasons"][0]["rewrites"] == PROC
    rc, doc, _ = mcp_tools.call(spec, {"ict": str(ict), "emir": PROC,
                                       "out": str(tmp_path / "new.yaml")})
    assert doc["code"] == "EMIR-E101"         # gated no more: the missing ICT is reported


def test_an_unclassified_command_always_needs_the_go_ahead(monkeypatch):
    """A command a later release adds is served at once -- behind the gate,
    until this server classifies it."""
    from emir.output import document, parser

    def newcmd(argv, out):
        ap = parser("emir newcmd", description="A command from a later release.")
        ap.add_argument("thing")
        ap.add_argument("--level", type=int, default=1)
        a = ap.parse_args(argv)
        out.emit(document("info", python="x", tools=[], packages=[], commands=[a.thing],
                          exit_codes={}))
        return 0
    monkeypatch.setitem(cli.COMMANDS, "newcmd", newcmd)
    try:
        specs = mcp_tools.specs(refresh=True)
        s = specs["emir_newcmd"]
        assert s.confirm == "always" and [p.name for p in s.params] == ["thing", "level"]
        assert s.annotations["destructiveHint"] is True and s.outputs is None
        rc, doc, _ = mcp_tools.call(s, {"thing": "a"})
        assert doc["code"] == "EMIR-E590"
        rc, doc, _ = mcp_tools.call(s, {"thing": "a", "level": 2, "confirm": True})
        assert rc == 0 and doc["commands"] == ["a"]
        rc, doc, _ = mcp_tools.call_line("emir newcmd a")
        assert doc["code"] == "EMIR-E590"
    finally:
        monkeypatch.delitem(cli.COMMANDS, "newcmd")
        mcp_tools.specs(refresh=True)


def test_emir_cli_runs_emir_command_lines_only(tmp_path):
    rc, doc, line = mcp_tools.call_line(f"emir nets {shlex.quote(DSPF)} --format text")
    assert rc == 0 and validate_doc(doc)["schema"] == "emir.nets/1"
    assert line == f"emir nets {shlex.quote(DSPF)} --format json"
    rc, doc, _ = mcp_tools.call_line(f"nets {shlex.quote(DSPF)}")     # without `emir`
    assert rc == 0 and doc["schema"] == "emir.nets/1"
    for cmdline, code in (("ls -l x", "EMIR-E591"), ("tail -n 40 sim.log", "EMIR-E591"),
                          ("emir mcp --list", "EMIR-E591"), ("emir bogus", "EMIR-E508"),
                          ("emir 'unbalanced", "EMIR-E507"), ("", "EMIR-E507")):
        rc, doc, _ = mcp_tools.call_line(cmdline)
        assert validate_doc(doc)["code"] == code, cmdline
        assert rc == doc["exit_code"] == 2
    rc, doc, _ = mcp_tools.call_line(f"emir run {shlex.quote(DSPF)} --netlist tb.scs "
                                     f"--mode cc-grid --run")
    assert doc["code"] == "EMIR-E590"
    # an @argument file is read before the gate decides
    (tmp_path / "args.txt").write_text(f"{DSPF}\n--netlist\ntb.scs\n--mode\ncc-grid\n"
                                       f"--run\n")
    rc, doc, _ = mcp_tools.call_line(f"emir run @{tmp_path / 'args.txt'}")
    assert doc["code"] == "EMIR-E590"


def test_a_next_step_runs_through_emir_cli_as_printed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rc, doc, _ = mcp_tools.call(mcp_tools.specs()["emir_check"],
                                {"dspf": DSPF, "process": PROC, "net": "VDD", "currents": CUR})
    assert rc == 0 and doc["passed"]
    (step,) = doc["next_steps"]
    rc, doc, _ = mcp_tools.call_line(step["command"])
    assert rc == 0 and validate_doc(doc)["verdict"] == "FAIL"
    assert (tmp_path / "emir_out" / "summary.json").is_file()


def test_selftest_runs_in_a_fresh_interpreter():
    """The self-test checks what importing emir pulls in: after `info` has
    imported matplotlib into this process, in-process it would fail."""
    mcp_tools.call(mcp_tools.specs()["emir_info"], {})
    rc, doc, _ = mcp_tools.call(mcp_tools.specs()["emir_selftest"], {})
    assert rc == 0 and validate_doc(doc)["passed"] is True


def test_isolate_runs_each_call_in_a_fresh_interpreter(monkeypatch):
    monkeypatch.setitem(mcp_tools.ISOLATE, "on", True)
    rc, doc, line = mcp_tools.call(mcp_tools.specs()["emir_next"],
                                   {"dspf": DSPF, "process": PROC, "currents": CUR})
    assert rc == 0 and validate_doc(doc)["steps"][0]["tool"] == {
        "name": "emir_selftest", "arguments": {}}


def test_calls_from_several_threads_run_one_at_a_time(monkeypatch):
    """The SDK runs tool calls in worker threads; each call redirects the
    process-wide stdout, so they must not overlap (the gate and the command
    together)."""
    import threading
    import time
    spans, real = [], cli.main

    def slow_main(argv):
        t0 = time.monotonic()
        time.sleep(0.05)
        rc = real(argv)
        spans.append((t0, time.monotonic()))
        return rc
    monkeypatch.setattr(cli, "main", slow_main)
    spec = mcp_tools.specs()["emir_nets"]
    docs = []
    threads = [threading.Thread(target=lambda: docs.append(
        mcp_tools.call(spec, {"dspf": DSPF})[1])) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert [d["schema"] for d in docs] == ["emir.nets/1"] * 4
    spans.sort()
    assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:])), spans


def test_a_document_that_is_not_one_is_an_internal_error():
    doc = mcp_tools.one_document('{"a": 1}\n{"b": 2}\n', "nets")
    assert validate_doc(doc)["code"] == "EMIR-E901"
    assert mcp_tools.one_document("", "nets")["code"] == "EMIR-E901"


# ------------------------------------------------------------ emir next over MCP


def test_next_gives_each_step_its_tool_call_over_mcp_only(monkeypatch, tmp_path):
    nxt = mcp_tools.specs()["emir_next"]
    rc, doc, _ = mcp_tools.call(nxt, {"dspf": DSPF, "process": PROC, "currents": CUR,
                                      "net": "VDD", "html": True})
    validate_doc(doc)
    assert "mcp" in doc["rules"] and doc["advice"][-1].startswith("never invent")
    assert any(a.startswith("over MCP:") for a in doc["advice"])
    for s in doc["steps"]:
        call = s["tool"]
        assert call is not None, s
        # the call's arguments are exactly the step's command line
        spec = mcp_tools.specs()[call["name"]]
        argv = shlex.split(s["command"])[2:]
        want = vars(spec.parser.parse_args(argv))
        got = vars(spec.parser.parse_args(mcp_tools.to_argv(spec, call["arguments"])))
        want.pop("format"), got.pop("format")      # the tools always print JSON
        assert got == want, s["command"]
    assert [s["tool"]["name"] for s in doc["steps"]] == [
        "emir_selftest", "emir_check", "emir_run", "emir_report"]
    # a simulation step is not an emir command: no tool
    rc, doc, _ = mcp_tools.call(nxt, {"dspf": DSPF, "process": PROC, "net": "VDD",
                                      "have_cadence": True, "mode": "rcc"})
    sim = next(s for s in doc["steps"] if s["id"] == "simulate")
    assert sim["tool"] is None and sim["needs_human"]
    # on the command line the plan is unchanged
    d, _ = jcli("next", "--dspf", DSPF, "--process", PROC, "--currents", CUR)
    assert all("tool" not in s for s in d["steps"]) and "mcp" not in d["rules"]


# ------------------------------------------------------------ emir mcp --list


def test_list_is_one_valid_document_without_the_sdk(monkeypatch, capsys):
    from importlib import metadata
    real = metadata.version

    def no_mcp(name):
        if name == "mcp":
            raise metadata.PackageNotFoundError(name)
        return real(name)
    monkeypatch.setattr(metadata, "version", no_mcp)
    assert cli.main(["mcp", "--list", "--format", "json"]) == 0
    doc = validate_doc(json.loads(capsys.readouterr().out))
    assert doc["sdk"] == {"installed": False, "version": None, "api": None,
                          "supported": False, "requirement": mcp_server.SDK_REQUIREMENT}
    assert [t["name"] for t in doc["tools"]] == list(mcp_tools.specs())
    assert doc["next_steps"][0]["command"].endswith(
        shlex.quote(mcp_server.SDK_REQUIREMENT))
    assert doc["next_steps"][-1]["command"].startswith("claude mcp add emir -- ")
    # serving without the SDK is a coded refusal, exit 3
    assert cli.main(["mcp", "--format", "json"]) == 3
    err = validate_doc(json.loads(capsys.readouterr().out))
    # the install hint of this install, whatever the clone's directory is
    # called (from a clone: `pip install -e '<clone>[mcp]'`); never pip's
    # `emir[mcp]`, which names a package that is not on PyPI
    from emir.repo import install_hint
    assert err["code"] == "EMIR-E603" and install_hint("mcp") in err["message"]
    assert "[mcp]" in install_hint("mcp") or "mcp>=" in install_hint("mcp")
    assert "pip install 'emir[" not in err["message"]


def test_list_from_the_command_line(tmp_path):
    doc, p = jcli("mcp", "--list", "--cwd", tmp_path)
    assert doc["server"]["cwd"] == str(tmp_path) and doc["server"]["transport"] == "stdio"
    assert {"claude-code", "claude-desktop", "codex", "cursor", "generic"} <= set(doc["clients"])
    assert "emir_run" in p.stderr                     # the text table went to stderr
    uris = [r["uri"] for r in doc["resources"]]
    assert uris[0] == "emir://catalog/errors.md"
    assert {f"emir://schema/{n}.json" for n in schemas.names()} <= set(uris)
    d, _ = jcli("mcp", "--cwd", tmp_path / "nope", rc=2)
    assert d["code"] == "EMIR-E101"
    # a relative --agent-dir is taken from where emir started, not from --cwd
    (tmp_path / "clone" / "agent" / "playbooks").mkdir(parents=True)
    (tmp_path / "clone" / "agent" / "playbooks" / "00-preflight.md").write_text("# pre\n")
    (tmp_path / "data").mkdir()
    doc, _ = jcli("mcp", "--list", "--agent-dir", "clone", "--cwd", "data", cwd=tmp_path)
    assert doc["server"]["agent_dir"] == str(tmp_path / "clone")
    assert doc["server"]["cwd"] == str(tmp_path / "data")
    assert "emir://doc/agent/playbooks/00-preflight.md" in [r["uri"] for r in doc["resources"]]


def test_agent_documents_are_served_by_path(tmp_path):
    (tmp_path / "agent" / "playbooks").mkdir(parents=True)
    (tmp_path / "agent" / "evals").mkdir()
    (tmp_path / "AGENTS.md").write_text("# agents\n")
    (tmp_path / "agent" / "playbooks" / "01-screen-a-block.md").write_text("# play\n")
    (tmp_path / "agent" / "evals" / "grader.md").write_text("# answers\n")
    res = {r["uri"]: r for r in mcp_server.resources(str(tmp_path))}
    assert res["emir://doc/AGENTS.md"]["read"]() == "# agents\n"
    assert res["emir://doc/agent/playbooks/01-screen-a-block.md"]["read"]() == "# play\n"
    assert not [u for u in res if "evals" in u]
    assert res["emir://catalog/errors.md"]["read"]() == catalog_markdown()


def test_output_schemas_use_keywords_every_validator_knows():
    """Clients validate structured results with the validator they have
    (Ajv's draft-07 default in many JavaScript clients): no 2020-12-only
    keyword and no `$schema`/`$id` inside a tool's output schema."""
    draft7_and_2020 = {"type", "properties", "required", "additionalProperties", "items",
                       "enum", "const", "anyOf", "not", "pattern", "minProperties",
                       "minItems", "maxItems", "minLength", "maxLength", "propertyNames",
                       "title", "description",
                       "minimum", "exclusiveMinimum", "maximum", "default", "examples"}

    def walk(s, where):
        if isinstance(s, dict):
            for k, v in s.items():
                if where.endswith("properties") or where.endswith("$defs"):
                    walk(v, where + "/" + k)                   # property names are free
                    continue
                assert k in draft7_and_2020, (where, k)
                walk(v, where + "/" + k)
        elif isinstance(s, list):
            for i, v in enumerate(s):
                walk(v, f"{where}/{i}")
    for name, spec in mcp_tools.specs().items():
        out = spec.output_schema()
        assert out["type"] == "object"
        V.check_schema(out)
        walk(out, name)
        walk(spec.input_schema(), name + ":input")


def test_client_snippets_in_agent_mcp_match_the_generator():
    conf = mcp_server.client_configs(["emir", "mcp"], "/path/to/your/project")
    # a desktop app does not see the shell's PATH: its snippet has a full path
    conf["claude-desktop"] = mcp_server.client_configs(
        ["/path/to/venv/bin/emir", "mcp"], "/path/to/your/project")["claude-desktop"]
    files = {"claude-code": "claude-code.sh", "claude-code-project": "claude-code.mcp.json",
             "claude-desktop": "claude_desktop_config.json", "codex": "codex-config.toml",
             "cursor": "cursor-mcp.json", "generic": "generic.json"}
    for client, fname in files.items():
        text = open(os.path.join(ROOT, "agent", "mcp", fname), encoding="utf-8").read()
        body = "".join(ln for ln in text.splitlines(keepends=True) if not ln.startswith("#"))
        assert body.strip() == conf[client]["snippet"].strip(), fname
        if fname.endswith(".json"):
            json.loads(body)
    if sys.version_info >= (3, 11):
        import tomllib
        t = tomllib.loads(conf["codex"]["snippet"])
        assert t["mcp_servers"]["emir"] == {"command": "emir", "args": ["mcp"]}
    assert conf["claude-code"]["snippet"].strip() == "claude mcp add emir -- emir mcp"


def test_the_protocol_channel_is_kept_clean():
    """After `divert_std_streams`, Python prints, C-level writes to fd 1 and
    child processes all land on stderr; only the wire reaches stdout."""
    code = ("import os, sys\n"
            "from emir.mcp_server import divert_std_streams\n"
            "divert_std_streams()\n"
            "os.write(1, b'fd1-leak\\n')\n"
            "os.system('echo child-leak; cat')\n"            # cat reads /dev/null, not the wire
            "sys.stdout.write('WIRE ' + sys.stdin.readline())\n"
            "sys.stdout.flush()\n")
    p = subprocess.run([sys.executable, "-c", code], input="hello\n", capture_output=True,
                       text=True, cwd=ROOT, env={**os.environ, "PYTHONPATH": ROOT},
                       timeout=60)
    assert p.returncode == 0, p.stderr
    assert p.stdout == "WIRE hello\n"
    assert "fd1-leak" in p.stderr and "child-leak" in p.stderr


def test_registered_codes_are_raised_where_documented():
    from emir.errors import lookup
    assert lookup("EMIR-E590").exit_code == 2 and lookup("EMIR-E590").command == ""
    assert lookup("EMIR-E591").exit_code == 2
    assert lookup("EMIR-I690").level == lookup("EMIR-I691").level == "info"


# ------------------------------------------------------------ a real MCP client


def _sdk_major():
    from importlib import metadata
    try:
        return int(metadata.version("mcp").split(".")[0])
    except metadata.PackageNotFoundError:
        return None


needs_sdk = pytest.mark.skipif(_sdk_major() is None, reason="the mcp package is not installed")


def _get(obj, snake):
    """An SDK object's field under its 2.x (snake) or 1.x (camel) name."""
    if hasattr(obj, snake):
        return getattr(obj, snake)
    camel = snake.split("_")[0] + "".join(w.title() for w in snake.split("_")[1:])
    return getattr(obj, camel)


@asynccontextmanager
async def connect(server=None, stdio_args=None):
    """A client session to `server` in-process, or to `python -m emir mcp
    <stdio_args>` over stdio, whichever SDK line is installed."""
    from mcp import StdioServerParameters
    params = None
    if stdio_args is not None:
        params = StdioServerParameters(command=sys.executable,
                                       args=["-m", "emir", "mcp", *stdio_args],
                                       env={**os.environ, "PYTHONPATH": ROOT}, cwd=ROOT)
    if params is None and _sdk_major() >= 2:
        from mcp import Client
        async with Client(server) as c:
            yield c
        return
    from mcp import ClientSession
    if params is None:
        from mcp.shared.memory import create_connected_server_and_client_session
        async with create_connected_server_and_client_session(server) as s:
            yield s
        return
    from mcp.client.stdio import stdio_client
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        yield s


def _check_result(result, tools, name):
    """The result's document validates against its emir schema and against
    the output schema the tool advertises; it matches the text content."""
    doc = _get(result, "structured_content")
    validate_doc(doc)
    out = _get(tools[name], "output_schema")
    if out:
        jsonschema.validate(doc, out)
    assert json.loads(result.content[0].text) == doc
    assert _get(result, "is_error") is (doc["schema"] == "emir.error/1")
    return doc


def _run(coro):
    import anyio
    return anyio.run(coro)


@needs_sdk
def test_in_process_client_lists_tools_and_runs_the_sample(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    server = mcp_server.build_server(None, cwd=str(tmp_path))

    async def session():
        async with connect(server) as c:
            listed = (await c.list_tools()).tools
            tools = {t.name: t for t in listed}
            assert list(tools) == list(mcp_tools.specs())
            for t in listed:
                spec = mcp_tools.specs()[t.name]
                assert _get(t, "input_schema") == spec.input_schema()
                assert _get(t, "output_schema") == spec.output_schema()
                assert _get(t.annotations, "read_only_hint") is spec.read_only
            docs = {}
            for name, args in (
                    ("emir_info", {}),
                    ("emir_selftest", {}),
                    ("emir_next", {"dspf": DSPF, "process": PROC, "currents": CUR}),
                    ("emir_check", {"dspf": DSPF, "process": PROC, "currents": CUR}),
                    ("emir_run", {"dspf": DSPF, "process": PROC, "currents": CUR,
                                  "summary_out": "emir_out/summary.json"}),
                    ("emir_report", {"from_file": "emir_out/summary.json"}),
                    ("emir_explain", {"codes": ["E590"]}),
                    ("emir_schema", {"name": "run"}),
                    ("emir_pins", {"dspf": DSPF}),                 # --net left out
                    # a misspelt option is refused, never dropped silently
                    ("emir_run", {"dspf": DSPF, "process": PROC, "currents": CUR,
                                  "bogus_flag": 1, "summaryout": "x.json"}),
                    ("emir_run", {"dspf": DSPF, "process": PROC, "netlist": "tb.scs",
                                  "mode": "cc-grid", "run": True}),
                    ("emir_cli", {"command_line": "tail -n 40 x.log"})):
                r = await c.call_tool(name, args)
                meta = _get(r, "meta")
                if name == "emir_schema":
                    assert _get(r, "structured_content") == schemas.get("run")
                    continue
                d = _check_result(r, tools, name)
                assert meta["emir/exit_code"] == (d.get("exit_code") or 0) or \
                    d["schema"] != "emir.error/1"
                docs.setdefault(name, []).append(d)
            res = (await c.list_resources()).resources
            uris = [str(x.uri) for x in res]
            assert "emir://catalog/errors.md" in uris and "emir://schema/run.json" in uris
            rr = await c.read_resource("emir://catalog/errors.md")
            assert rr.contents[0].text == catalog_markdown()
            rr = await c.read_resource("emir://schema/next.json")
            assert json.loads(rr.contents[0].text) == schemas.get("next")
            return docs
    docs = _run(session)
    assert docs["emir_info"][0]["schema"] == "emir.info/1"
    assert docs["emir_selftest"][0]["passed"] is True
    plan = docs["emir_next"][0]
    names = [s["tool"]["name"] for s in plan["steps"]]
    assert names[0] == "emir_selftest" and names[-3:] == ["emir_check", "emir_run",
                                                          "emir_report"]
    assert docs["emir_check"][0]["passed"] is True
    run = docs["emir_run"][0]
    assert run["verdict"] == "FAIL" and run["disclaimer"] == "Screening, not sign-off."
    assert run["worst_net"]["net"] == "VDD"
    assert abs(run["worst_net"]["ratio"] - 5 / 3) < 1e-9
    assert abs(run["nets"][0]["ir_drop_mV"] - 54.0) < 1e-9
    assert (tmp_path / "emir_out" / "summary.json").is_file()
    assert "Screening, not sign-off." in docs["emir_report"][0]["markdown"]
    assert docs["emir_explain"][0]["codes"][0]["code"] == "EMIR-E590"
    # a required parameter left out is named as the tool's parameter (net), and
    # an unknown one is refused (EMIR-E592): nothing runs
    pins = docs["emir_pins"][0]
    assert pins["code"] == "EMIR-E592" and pins["details"]["missing"] == ["net"]
    assert "missing required net" in pins["message"] and "--net" not in pins["message"]
    run, unknown, refused = docs["emir_run"]
    assert unknown["code"] == "EMIR-E592"
    assert unknown["details"]["unknown"] == ["bogus_flag", "summaryout"]
    assert "did you mean 'summary_out'?" in unknown["message"]
    assert not (tmp_path / "x.json").exists()
    assert refused["code"] == "EMIR-E590"
    assert docs["emir_cli"][0]["code"] == "EMIR-E591"


@needs_sdk
def test_concurrent_calls_get_their_own_documents(capsys):
    """A client may send calls without waiting: they run one at a time, each
    gets its own document, and no command's text reaches the real stdout
    (the MCP channel when serving)."""
    import anyio
    server = mcp_server.build_server(None)
    capsys.readouterr()
    calls = [("emir_validate", {"quick": True}, "emir.validate/1"),
             ("emir_nets", {"dspf": DSPF}, "emir.nets/1"),
             ("emir_pins", {"dspf": DSPF, "net": "VDD"}, "emir.pins/1"),
             ("emir_info", {}, "emir.info/1"),
             ("emir_explain", {"codes": ["E101"]}, "emir.explain/1"),
             ("emir_check", {"dspf": DSPF, "process": PROC, "currents": CUR}, "emir.check/1"),
             ("emir_save_lines", {"dspf": DSPF, "mode": "rcc"}, "emir.save_lines/1"),
             ("emir_run", {"dspf": DSPF, "netlist": "tb.scs", "mode": "cc-grid", "run": True},
              "emir.error/1")] * 2
    got = [None] * len(calls)

    async def session():
        async with connect(server) as c:
            async def one(i, name, args):
                r = await c.call_tool(name, args)
                got[i] = _get(r, "structured_content")
            async with anyio.create_task_group() as tg:
                for i, (name, args, _) in enumerate(calls):
                    tg.start_soon(one, i, name, args)
    _run(session)
    assert [validate_doc(d)["schema"] for d in got] == [want for *_, want in calls]
    assert capsys.readouterr().out == ""


@needs_sdk
def test_without_output_schemas():
    server = mcp_server.build_server(None, output_schemas=False)

    async def session():
        async with connect(server) as c:
            tools = (await c.list_tools()).tools
            assert all(_get(t, "output_schema") is None for t in tools)
            r = await c.call_tool("emir_nets", {"dspf": DSPF})
            return _get(r, "structured_content")
    assert validate_doc(_run(session))["nets"][0]["name"] == "VDD"


@needs_sdk
def test_over_stdio_like_a_client_starts_it():
    """`python -m emir mcp` as an MCP client starts it: the handshake, the
    tool list, a call; with --isolate every call runs in a fresh process."""
    async def session():
        out = {}
        async with connect(stdio_args=["--isolate"]) as c:
            tools = {t.name: t for t in (await c.list_tools()).tools}
            out["n"] = len(tools)
            r = await c.call_tool("emir_info", {})
            out["info"] = _check_result(r, tools, "emir_info")
            r = await c.call_tool("emir_nets", {"dspf": DSPF})
            out["nets"] = _check_result(r, tools, "emir_nets")
        return out
    out = _run(session)
    assert out["n"] == len(mcp_tools.specs())
    assert out["info"]["schema"] == "emir.info/1"
    assert out["nets"]["nets"][0]["name"] == "VDD"



@needs_sdk
@pytest.mark.parametrize("how,rc,code", [("eof", 0, None), ("SIGTERM", 143, "EMIR-E906"),
                                         ("SIGINT", 130, "EMIR-E904")])
def test_server_ends_with_the_contracts_status(how, rc, code):
    """Closing stdin ends the server (0); SIGTERM and SIGINT end it at once
    with 143 / 130 and a coded line on stderr (never waiting on the thread
    that reads stdin)."""
    import signal
    p = subprocess.Popen([sys.executable, "-m", "emir", "mcp"], cwd=ROOT, text=True,
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         env={**os.environ, "PYTHONPATH": ROOT})
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "test", "version": "0"}}}
    p.stdin.write(json.dumps(init) + "\n")
    p.stdin.flush()
    reply = json.loads(p.stdout.readline())
    assert reply["id"] == 1 and reply["result"]["serverInfo"]["name"] == "emir"
    if how == "eof":
        p.stdin.close()
    else:
        p.send_signal(getattr(signal, how))
    try:
        assert p.wait(timeout=60) == rc
    finally:
        p.kill()
    err = p.stderr.read()
    assert "EMIR-I690" in err
    if code:
        assert f"emir: {code}:" in err
