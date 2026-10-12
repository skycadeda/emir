"""The error-code registry (`emir/errors.py`) and its rules: every failure,
finding and warning carries a registered code; the catalog is generated from
the registry; `emir explain` reads it."""
import ast
import glob
import json
import os
import re
import subprocess
import sys

import pytest
from _helpers import CUR, DSPF, PROC, ROOT, emir

from emir import errors
from emir.check import check_inputs
from emir.errors import (AREAS, EXIT_CODES, REGISTRY, RETIRED, EmirError, collecting,
                         lookup, notice)

SOURCES = sorted(p for p in glob.glob(os.path.join(ROOT, "emir", "*.py"))
                 if not p.endswith("errors.py"))

#: the feature decades of each area (`errors.DECADES`, the module doc)
BLOCKS = {who: range(lo, hi + 1) for who, (lo, hi) in errors.DECADES.items()}


def test_the_decades_cover_01_to_99_once():
    nums = sorted(n for r in BLOCKS.values() for n in r)
    assert nums == list(range(1, 100))
    assert BLOCKS["the base package"] == range(1, 40)


def normalise_cmd(cmd):
    return " ".join(cmd.split())


def _tree(path):
    return ast.parse(open(path, encoding="utf-8").read(), filename=path)


def _calls(tree, names):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
            if name in names:
                yield name, node


# ------------------------------------------------------------ the registry


def test_registry_entries_are_complete():
    assert len(REGISTRY) > 100
    for code, e in REGISTRY.items():
        assert re.fullmatch(r"EMIR-[EWI][1-9][0-9]{2}", code), code
        # `command` may be empty (no command helps; the hint says what to do)
        assert e.title and e.meaning and e.hint, code
        # ... but it is never `emir explain` of the code itself: that only
        # repeats the hint the error already carries
        assert normalise_cmd(e.command) != f"emir explain {code}", code
        assert e.title == e.title.strip() and not e.title.endswith("."), code
        assert int(code[6]) in AREAS
        n = int(code[7:])
        assert any(n in r for r in BLOCKS.values()), code
        if e.letter == "E":
            assert e.exit_code in EXIT_CODES and e.exit_code not in (0, 1), code
        else:
            assert e.exit_code is None, code


def test_hierarchy_area_is_retired_and_internal_codes_exit_70():
    # area 8 was the chip-level hierarchy's alone, removed in 1.1: every code
    # of it is retired (kept, never reused); the magic route's codes sit in
    # the x40 decade of the areas their conditions belong to (the cell's
    # name 2, tools 6)
    assert not [c for c in REGISTRY if c[6] == "8"]
    assert {c for c in RETIRED if c[6] == "8"} >= {"EMIR-E801", "EMIR-E814", "EMIR-W808"}
    assert AREAS[8] == "hierarchy (removed)"
    forty = {c for c in REGISTRY if 40 <= int(c[7:]) <= 49 and c[6] != "5"}
    route = open(os.path.join(ROOT, "emir", "magic.py"), encoding="utf-8").read()
    assert forty and all(f'"{c}"' in route for c in forty)
    assert {c[6] for c in forty} == {"2", "6"}
    # area 5's x40 decade was the removed self-consistent loop's: retired
    assert not {c for c in REGISTRY if c[6] == "5" and 40 <= int(c[7:]) <= 49}
    assert {c for c in RETIRED if c[6] == "5" and 40 <= int(c[7:]) <= 49}
    assert lookup("EMIR-E642").exit_code == 3          # magic cannot start: a missing tool
    assert lookup("EMIR-E643").exit_code == 3          # no magic writes geometry: the same
    for c, e in REGISTRY.items():
        if c[6] == "9" and e.letter == "E" and c not in ("EMIR-E904", "EMIR-E906"):
            assert e.exit_code == 70, c
    assert lookup("EMIR-E904").exit_code == 130
    assert lookup("EMIR-E906").exit_code == 143
    assert lookup("EMIR-E601").exit_code == lookup("EMIR-E603").exit_code == 3


def test_retired_codes_are_never_reused(monkeypatch):
    assert not set(RETIRED) & set(REGISTRY)
    monkeypatch.setitem(RETIRED, "EMIR-E199", "replaced by EMIR-E101 (test)")
    with pytest.raises(KeyError, match="retired"):
        lookup("EMIR-E199")
    assert errors.explain("e199")["retired"] is True


def test_every_registered_code_is_used():
    """A code in the registry that nothing raises or reports is dead weight
    in the catalog an agent reads."""
    src = "".join(open(p, encoding="utf-8").read() for p in SOURCES)
    assert [c for c in REGISTRY if f'"{c}"' not in src] == []


def test_lookup_accepts_short_forms():
    assert lookup("e204") is lookup("E204") is lookup("emir-E204") is REGISTRY["EMIR-E204"]
    with pytest.raises(KeyError):
        lookup("EMIR-E999")


def test_command_templates_are_filled_and_quoted():
    e = lookup("EMIR-E204")
    # the catalog form keeps the placeholder; a next step is never one
    assert errors.fill(e.command) == "emir nets <dspf>"
    assert e.command_for() == ""
    assert e.command_for(dspf="/a b/x.dspf") == "emir nets '/a b/x.dspf'"
    # {args} is already a quoted argument string
    assert lookup("EMIR-E420").command_for(args="x.dspf --net VDD") \
        == "emir check x.dspf --net VDD"


def test_emir_error_carries_code_hint_exit_and_next_steps():
    err = EmirError("EMIR-E204", "net 'X' is not in a.dspf", dspf="a.dspf")
    assert isinstance(err, ValueError)            # old `except ValueError` still works
    assert str(err) == "EMIR-E204: net 'X' is not in a.dspf"
    assert err.exit_code == 2 and err.hint == lookup("E204").hint
    assert err.next_steps() == [{"why": f"net not in the DSPF: {err.hint}",
                                 "command": "emir nets a.dspf"}]
    with pytest.raises(KeyError):
        EmirError("EMIR-W306", "a warning is not an error")


def test_notice_logs_a_coded_line_and_records_it():
    lines = []
    with collecting() as found:
        line = notice("EMIR-W203", "VDD has no instance-pin records", lines.append)
        with collecting() as inner:                # nested collectors both see it
            notice("EMIR-I305", "metal1_conn -> Metal1", None)
    assert line == "[emir] WARNING EMIR-W203: VDD has no instance-pin records" == lines[0]
    assert inner == [{"code": "EMIR-I305", "level": "info", "message": "metal1_conn -> Metal1"}]
    assert [n["code"] for n in found] == ["EMIR-W203", "EMIR-I305"]
    with pytest.raises(KeyError, match="retired"):
        notice("EMIR-W403", "a retired code is never noticed")
    with pytest.raises(KeyError):
        notice("EMIR-E204", "an error is raised, not noticed")


# ------------------------------------------------------------ every failure is coded


def test_no_plain_exception_is_raised():
    """Every refusal is an EmirError with a registered code: no `raise
    ValueError(...)`, `raise SystemExit("...")` and the like in the package
    (a private exception caught inside its module is fine)."""
    import builtins
    builtin = {n for n, v in vars(builtins).items()
               if isinstance(v, type) and issubclass(v, BaseException)}
    bad = []
    for p in SOURCES:
        for node in ast.walk(_tree(p)):
            if not isinstance(node, ast.Raise) or node.exc is None:
                continue
            exc = node.exc
            name = (exc.func.id if isinstance(exc, ast.Call) and isinstance(exc.func, ast.Name)
                    else exc.id if isinstance(exc, ast.Name) else None)
            if name == "SystemExit" and isinstance(exc, ast.Call) and exc.args \
                    and isinstance(exc.args[0], ast.Call):
                continue                            # `raise SystemExit(main())`
            if name in builtin:
                bad.append(f"{os.path.relpath(p, ROOT)}:{node.lineno}: raise {name}")
    assert bad == []


def _literal_code(node):
    a = node.args[0] if node.args else None
    return a.value if isinstance(a, ast.Constant) and isinstance(a.value, str) else None


def test_every_emir_error_and_notice_names_a_registered_code():
    seen = 0
    for p in SOURCES:
        for name, node in _calls(_tree(p), {"EmirError", "notice"}):
            code = _literal_code(node)
            where = f"{os.path.relpath(p, ROOT)}:{node.lineno}"
            if code is None:
                # re-raising a caught error's own code (`e.code`) is the one
                # allowed non-literal form
                a0 = node.args[0] if node.args else None
                assert isinstance(a0, (ast.Attribute, ast.Subscript, ast.Name)), where
                continue
            seen += 1
            assert code in REGISTRY, where
            assert REGISTRY[code].letter == ("E" if name == "EmirError" else
                                             REGISTRY[code].letter), where
            if name == "notice":
                assert REGISTRY[code].letter in "WI", where
    assert seen > 60


def test_every_checker_finding_has_a_code_of_its_level():
    letter = {"fail": "E", "warn": "W", "info": "I", "ok": "I"}
    n = 0
    for p in SOURCES:
        for name, node in _calls(_tree(p), set(letter)):
            if not (isinstance(node.func, ast.Attribute)
                    and getattr(node.func.value, "id", "") == "rep"):
                continue
            n += 1
            code = _literal_code(node)
            where = f"{os.path.relpath(p, ROOT)}:{node.lineno}"
            if code is None:                        # rep.fail(e.code, ...)
                assert isinstance(node.args[0], ast.Attribute), where
                continue
            assert code in REGISTRY and REGISTRY[code].letter == letter[name], where
    assert n > 90


#: functions whose string arguments are lines a person or an agent reads
_LOGGERS = {"print", "log", "_log", "write", "_stderr", "warn", "warning"}


def warning_lines(tree) -> list:
    """Line numbers of warning text that bypasses `errors.notice`: any
    `[emir] warn...` literal in any case, and any argument of a print/log
    call that starts with "warning" in any case (`log("Warning: ...")`)."""
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and "[emir] warn" in node.value.lower():
            bad.append(node.lineno)
        if isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
            if name not in _LOGGERS:
                continue
            for arg in node.args:
                parts = arg.values if isinstance(arg, ast.JoinedStr) else [arg]
                first = parts[0] if parts else None
                if isinstance(first, ast.Constant) and isinstance(first.value, str) \
                        and first.value.lstrip().lower().startswith("warning"):
                    bad.append(node.lineno)
    return bad


def test_no_warning_line_bypasses_notice():
    """Warning lines come from `errors.notice`, so each has a code."""
    bad = [f"{os.path.relpath(p, ROOT)}:{n}" for p in SOURCES for n in warning_lines(_tree(p))]
    assert bad == []


@pytest.mark.parametrize("src", [
    'log("[emir] WARNING something")',
    'log("[emir] warning: something")',          # the review's mutant
    'log(f"[emir] Warning {x}: something")',
    'print("warning: something", file=sys.stderr)',
    'log(f"Warning: {n} pins not saved")',
])
def test_the_warning_line_check_catches_its_mutants(src):
    assert warning_lines(ast.parse(src)), src


def test_the_warning_line_check_passes_coded_lines():
    assert not warning_lines(ast.parse('notice("EMIR-W403", f"{n} pins not saved", log)\n'
                                       'print(f"  {n} warning(s)")'))


def test_no_uncoded_error_line_on_stderr():
    """The old `emir: <message>` error lines are gone: the only `emir: ` line
    is the renderer's, `emir: EMIR-Exxx: ...`."""
    for p in SOURCES:
        if p.endswith("output.py"):
            continue
        for node in ast.walk(_tree(p)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert not node.value.startswith("emir: "), f"{p}:{node.lineno}"


def test_every_finding_on_real_inputs_carries_its_code(tmp_path):
    rep = check_inputs(DSPF, process=PROC, nets=["NOPE"], currents='{"VDD": 1}',
                       viewer=str(tmp_path / "v"))
    assert rep.findings
    for f in rep.findings:
        assert lookup(f.code).letter == {"FAIL": "E", "WARN": "W", "INFO": "I",
                                         "OK": "I"}[f.level]
    d = json.loads(rep.to_json())
    assert {f["code"] for f in d["findings"]} >= {"EMIR-E204", "EMIR-W306"}
    assert all(f["command"] for f in d["findings"])


# ------------------------------------------------------------ the catalog


def test_committed_catalog_is_the_registry():
    text = open(os.path.join(ROOT, "agent", "errors.md"), encoding="utf-8").read()
    assert text == errors.catalog_markdown(), \
        "agent/errors.md is stale: run python scripts/gen_errors_md.py"
    for code in REGISTRY:
        assert f"### {code} " in text
    p = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "gen_errors_md.py"),
                        "--check"], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr


def test_catalog_check_fails_when_stale(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "gen_errors_md", os.path.join(ROOT, "scripts", "gen_errors_md.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    stale = tmp_path / "errors.md"
    stale.write_text("# old\n")
    monkeypatch.setattr(mod, "OUT", str(stale))
    assert mod.main(["--check"]) == 1
    assert mod.main([]) == 0 and mod.main(["--check"]) == 0


# ------------------------------------------------------------ emir explain


def test_explain_text_and_json():
    p = emir("explain", "e204")
    assert p.returncode == 0, p.stderr
    assert p.stdout.startswith("EMIR-E204  net not in the DSPF")
    assert "command  emir nets <dspf>" in p.stdout and "exit 2" in p.stdout
    p = emir("explain", "EMIR-W403", "I305", "--format", "json")
    assert p.returncode == 0
    d = json.loads(p.stdout)
    assert d["schema"] == "emir.explain/1"
    assert [c["code"] for c in d["codes"]] == ["EMIR-W403", "EMIR-I305"]
    assert d["codes"][0]["exit_code"] is None and d["codes"][0]["level"] == "warning"


def test_explain_list_covers_the_registry():
    # every code, and every retired one (marked so, with its reason)
    p = emir("explain", "--list", "--format", "json")
    assert p.returncode == 0
    codes = json.loads(p.stdout)["codes"]
    assert {c["code"] for c in codes} == set(REGISTRY) | set(RETIRED)
    assert {c["code"] for c in codes if c["retired"]} == set(RETIRED)
    p = emir("explain", "--list")
    assert len(p.stdout.splitlines()) == len(REGISTRY) + len(RETIRED)


def test_explain_unknown_code_is_coded():
    p = emir("explain", "E999")
    assert p.returncode == 2 and p.stderr.startswith("emir: EMIR-E511: ")
    p = emir("explain", "E999", "--format", "json")
    d = json.loads(p.stdout)
    assert p.returncode == 2 and d["code"] == "EMIR-E511"
    assert d["next_steps"][0]["command"] == "emir explain --list --format json"


def test_explain_retired_code(monkeypatch, capsys):
    from emir import cli
    monkeypatch.setitem(RETIRED, "EMIR-E199", "replaced by EMIR-E101 (test)")
    assert cli.main(["explain", "E199"]) == 0
    assert "RETIRED: replaced by EMIR-E101" in capsys.readouterr().out


def test_run_refusal_names_every_cause():
    p = emir("run", DSPF, "--net", "NOPE", "--process", PROC, "--currents", CUR,
             "--format", "json")
    assert p.returncode == 2
    d = json.loads(p.stdout)
    assert d["code"] == "EMIR-E501"
    assert "EMIR-E204" in d["details"]["causes"]
    assert any(s["command"].startswith("emir nets ") for s in d["next_steps"])
