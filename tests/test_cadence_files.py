"""agent/cadence/: every file says how it is verified on site (by the local
agent, with a named check) or that it is UNVERIFIED until checked on site --
and none claims a Cadence tool was verified anywhere; the generated copies
are current; no private reference leaks in; the SKILL balances; the templates
are well formed; the batch helper and its headless self-test work against a
stand-in `virtuoso`."""
import glob
import importlib.util
import os
import re
import string
import subprocess
import sys

import pytest
import yaml
from _cadence_fakes import make_tools
from _helpers import ROOT

from emir.selftest import _parens_balanced

CADENCE = os.path.join(ROOT, "agent", "cadence")
FILES = sorted(p for p in glob.glob(os.path.join(CADENCE, "**", "*"), recursive=True)
               if os.path.isfile(p) and "__pycache__" not in p)
REL = [os.path.relpath(p, CADENCE) for p in FILES]


def read(rel):
    with open(os.path.join(CADENCE, rel), encoding="utf-8") as f:
        return f.read()


def test_the_integration_is_all_there():
    expected = {"README.md", "SKILL_NOTES.md", "LIVE_TEST.md",
                "skill/emir_init.il", "skill/emir_main.il", "skill/emir_display.drf",
                "quantus/emir.ccl.tmpl", "quantus/render_ccl.py",
                "pvs/pvs_lvs_quantus.sh",
                "spectre/tb_rcc.scs.tmpl", "spectre/tb_cc.scs.tmpl",
                "ocean/emir_run.ocn.tmpl",
                "calibre/NOTES.md", "calibre/emir_calibre_pex.svrf.tmpl",
                "starrc/NOTES.md", "starrc/emir_starrc.cmd.tmpl",
                "python/virtuoso_batch.py", "site/emir_site.cadence.yaml.example",
                "test/emir_manual_test.il"}
    assert expected <= set(REL), expected - set(REL)


#: a claim that a licensed tool was verified, or ran, somewhere other than on
#: the site that uses it (the maintainer, 2026-10-02: support is proven on
#: the machine that uses the tool, by the local agent)
SITE_CLAIM = re.compile(r"VERIFIED-AT|(verified|ran|run|observed|tested|checked)\b"
                        r"[^.]{0,40}original (development )?site|IC6\.1\.8|"
                        r"\b(PVS|Quantus|Spectre|Virtuoso) \d+\.\d+(?! ?s\b)", re.I)


@pytest.mark.parametrize("rel", REL)
def test_every_file_says_how_it_is_verified_on_site(rel):
    """Measurements may keep their provenance ("measured at the original
    development site on ..."), never as a tool-support claim."""
    text = " ".join(read(rel).split())
    assert not SITE_CLAIM.search(text), (rel, SITE_CLAIM.search(text))
    if rel == "LIVE_TEST.md":
        assert "not run yet" in text and "UNVERIFIED until checked on site" in text
        return
    assert "UNVERIFIED" in text or "verified on site by the local agent" in text, rel


@pytest.mark.parametrize("rel", [r for r in REL if r.endswith((".il", ".ocn.tmpl", ".drf"))])
def test_skill_files_balance(rel):
    assert _parens_balanced(read(rel)), rel


@pytest.mark.parametrize("rel", [r for r in REL if not r.endswith((".md", ".example"))])
def test_code_files_carry_the_licence_line(rel):
    assert "SPDX-License-Identifier: MIT" in read(rel) or rel.endswith(".py"), rel
    assert "Apache" not in read(rel), rel
    if rel.endswith((".il", ".drf", ".sh")):
        assert "Not a Cadence-supplied file." in read(rel)


#: a reference into a private source: a file and line number
FILE_LINE = re.compile(r"\b[\w./-]+\.(?:py|il|md|log|ccl|yaml):\d+")


@pytest.mark.parametrize("rel", REL)
def test_no_file_line_references_into_private_sources(rel):
    assert not FILE_LINE.search(read(rel)), FILE_LINE.search(read(rel)).group(0)


def test_leak_guard_passes_on_the_integration():
    p = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "leak_guard.py"),
                        *FILES], capture_output=True, text=True, cwd=ROOT)
    assert p.returncode == 0, p.stdout + p.stderr


def test_no_cadence_documentation_quote():
    """The old list-box sentence was worded like Cadence's documentation; the
    paraphrase is in SKILL_NOTES.md and in the Explorer's comment."""
    for p in FILES + [os.path.join(ROOT, "emir", "skillapp.py")]:
        with open(p, encoding="utf-8") as f:
            assert "exited the list box" not in f.read(), p


# ------------------------------------------------------------ generated copies


def _gen():
    spec = importlib.util.spec_from_file_location(
        "gen_cadence_skill", os.path.join(ROOT, "scripts", "gen_cadence_skill.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_generated_copies_are_current():
    p = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "gen_cadence_skill.py"),
                        "--check"], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    from emir.cadence import CCL_TEMPLATE
    from emir.skillgui import MAIN, display_drf
    assert read("skill/emir_main.il") == MAIN
    assert read("skill/emir_display.drf") == display_drf()
    assert read("quantus/emir.ccl.tmpl") == CCL_TEMPLATE


def test_generated_copies_check_fails_when_stale(tmp_path, monkeypatch):
    mod = _gen()
    monkeypatch.setattr(mod, "CADENCE", str(tmp_path))
    assert mod.main(["--check"]) == 1
    assert mod.main([]) == 0 and mod.main(["--check"]) == 0


# ------------------------------------------------------------ templates


@pytest.mark.parametrize("rel", ["spectre/tb_rcc.scs.tmpl", "spectre/tb_cc.scs.tmpl"])
def test_spectre_templates(rel):
    text = read(rel)
    fields = {m.group("braced") for m in string.Template.pattern.finditer(text)
              if m.group("braced")}
    assert fields >= {"MODELS", "SECTION", "PORTS", "CELL", "VDD", "VDD_NET", "TSTOP"}
    assert not [m.group(0) for m in string.Template.pattern.finditer(text)
                if m.group("invalid") is not None]
    assert "simulator lang=spectre" in text and 'include "emir_save.scs"' in text
    assert "-format psfascii" in text and "XDUT (${PORTS}) ${CELL}" in text
    assert "--prefix XDUT." in text and "--out emir_save.scs" in text
    filled = string.Template(text).safe_substitute(
        {k: "x" for k in fields})
    assert "${" not in filled


def test_rcc_template_warns_about_r_only():
    assert "An R-only DSPF" in read("spectre/tb_rcc.scs.tmpl")
    assert "--mode cc-grid" in read("spectre/tb_cc.scs.tmpl")
    assert "same LVS" in read("spectre/tb_cc.scs.tmpl")


def test_site_example_names_every_key_emir_reads():
    data = yaml.safe_load(read("site/emir_site.cadence.yaml.example"))
    from emir.cadence import SETTINGS, TOOLS
    keys = set(SETTINGS) | {k for _env, k, _exe in TOOLS.values()}
    assert keys <= set(data), keys - set(data)
    assert {"spectre", "process", "lib", "mark_layer", "work_dir", "cds_lib"} <= set(data)
    for k, v in data.items():
        if isinstance(v, str) and v.startswith("/"):
            assert v.startswith("/path/to/"), (k, v)


def test_ocean_sketch_uses_current_options():
    text = read("ocean/emir_run.ocn.tmpl")
    assert "--summary-out" in text and "--json" not in text and "--out" in text
    assert "--net all" in text and "emirOcnQ(" in text


# ------------------------------------------------------------ virtuoso_batch


def _batch():
    """The module, imported without writing bytecode into agent/cadence."""
    spec = importlib.util.spec_from_file_location(
        "virtuoso_batch", os.path.join(CADENCE, "python", "virtuoso_batch.py"))
    mod = importlib.util.module_from_spec(spec)
    old, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = old
    return mod


@pytest.mark.parametrize("line,out", [
    ("\\o EMIR_OK\n", "EMIR_OK\n"), ("\\i load(x)\n", "load(x)\n"),
    ("\\w *WARNING* x\n", "*WARNING* x\n"), ("\\e *Error* y\n", "*Error* y\n"),
    ("\\# comment\n", "comment\n"), ("\\p > \n", "> \n"), ("\\t t\n", "t\n"),
    ("plain\n", "plain\n")])
def test_log_prefixes_are_stripped(line, out):
    assert _batch().strip_log([line]) == out


def test_wrapped_script_balances_and_exits():
    w = _batch().wrap('printf("x %s\\n" "y")')
    assert _parens_balanced(w) and w.rstrip().endswith("(exit)")
    assert "errset.errset" in w and "EMIR_OK" in w and "EMIR_FAIL" in w


def test_run_skill_against_a_stand_in(tmp_path, monkeypatch):
    vb = _batch()
    tools = make_tools(tmp_path / "bin")
    exe = os.path.join(tools, "virtuoso")
    monkeypatch.delenv("EMIR_CDS_LIB", raising=False)
    out = vb.run_skill("t", str(tmp_path / "run"), virtuoso=exe,
                       cds_lib=str(tmp_path / "my.cds.lib"))
    assert "EMIR_OK" in out
    with open(tmp_path / "run" / "cds.lib") as f:
        assert f.read() == f"INCLUDE {tmp_path / 'my.cds.lib'}\n"
    with pytest.raises(vb.SkillError, match="EMIR_FAIL"):
        vb.run_skill("FAIL_ME", str(tmp_path / "run2"), virtuoso=exe, cds_lib="")
    monkeypatch.setenv("FAKE_VIRTUOSO", "log")          # stdout lost, the log has it
    assert "EMIR_OK" in vb.run_skill("t", str(tmp_path / "run3"), virtuoso=exe, cds_lib="")


def test_headless_selftest_against_a_stand_in(tmp_path, monkeypatch):
    """The on-site check of the SKILL before the live test: batch Virtuoso
    loads emir_main.il and every procedure the menu calls is defined."""
    vb = _batch()
    exe = os.path.join(make_tools(tmp_path / "bin"), "virtuoso")
    monkeypatch.delenv("EMIR_CDS_LIB", raising=False)
    body = vb.selftest_body()
    assert 'load("' in body and "emir_main.il" in body and _parens_balanced(body)
    assert all(f"'{p}" in body for p in vb.SELFTEST_PROCS)
    r = vb.selftest(run_dir=str(tmp_path / "r1"), virtuoso=exe)
    assert r["passed"] and r["procedures"] == {p: "t" for p in vb.SELFTEST_PROCS}
    monkeypatch.setenv("FAKE_MISSING", "emirMain")
    r = vb.selftest(run_dir=str(tmp_path / "r2"), virtuoso=exe)
    assert not r["passed"]
    monkeypatch.delenv("FAKE_MISSING")
    assert vb.main(["--selftest", "--run-dir", str(tmp_path / "r3"), "--virtuoso", exe]) == 0
