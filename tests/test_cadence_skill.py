"""The Virtuoso SKILL, checked as text (no Virtuoso exists here): balanced
parentheses, every procedure that calls return() runs it inside prog(), every
emir* function called is defined, and the properties the review fixes rely
on -- environment-driven settings, read-only open, quoting of everything a
shell sees, one menu registration, per-window pulldowns, save lines to a
file, integer band steps (no drawing<N> purposes), nil-safe highlight sets,
clean-up on close, a bounded list. None of this proves the SKILL runs; the
live test (agent/cadence/LIVE_TEST.md) does."""
import os
import re
import subprocess

import pytest
from _helpers import CUR, DSPF, PROC, ROOT

from emir.selftest import _parens_balanced
from emir.skillapp import APP
from emir.skillgui import HEADER, MAIN, display_drf, install, site_stub

CADENCE = os.path.join(ROOT, "agent", "cadence")
INIT = open(os.path.join(CADENCE, "skill", "emir_init.il"), encoding="utf-8").read()
MANUAL = open(os.path.join(CADENCE, "test", "emir_manual_test.il"), encoding="utf-8").read()
OCEAN = open(os.path.join(CADENCE, "ocean", "emir_run.ocn.tmpl"), encoding="utf-8").read()


# ------------------------------------------------------------ a tiny SKILL reader


def split_code(src: str) -> tuple:
    """`(code, strings)`: the source with comments and the content of every
    string literal blanked to spaces (so a ; or ( inside a string is not
    code; positions are kept), and the list of string contents."""
    code, strings, cur, i, n = [], [], None, 0, len(src)
    while i < n:
        c = src[i]
        if cur is not None:
            if c == "\\" and i + 1 < n:
                cur.append(src[i:i + 2])
                code.append("  ")
                i += 2
                continue
            if c == '"':
                strings.append("".join(cur))
                cur = None
                code.append('"')
            else:
                cur.append(c)
                code.append(" " if c != "\n" else "\n")
            i += 1
            continue
        if c == ";":
            j = src.find("\n", i)
            j = n if j < 0 else j
            code.append(" " * (j - i))
            i = j
            continue
        if c == '"':
            cur = []
        code.append(c)
        i += 1
    out = "".join(code)
    assert len(out) == len(src)
    return out, strings


def procedures(src: str, blank: bool = False) -> dict:
    """`{name: body}` of every `procedure( name(args) body )`: the source
    text, or with `blank` the code only (comments and strings blanked)."""
    code, _ = split_code(src)
    out = {}
    def close(start):
        depth = 0
        for k in range(start, len(code)):
            if code[k] == "(":
                depth += 1
            elif code[k] == ")":
                depth -= 1
                if depth == 0:
                    return k
        raise AssertionError("unbalanced")
    for m in re.finditer(r"procedure\(\s*(\w+)\(", code):
        args_end = close(m.end() - 1)                 # the argument list
        k = close(code.index("(", m.start()))         # the whole procedure( ... )
        assert m.group(1) not in out, f"procedure {m.group(1)} defined twice"
        out[m.group(1)] = (code if blank else src)[args_end + 1:k]
    return out


def return_outside_prog(body: str) -> bool:
    """Does a `return(` sit outside every `prog(` of this body?"""
    stack = []
    for m in re.finditer(r"([A-Za-z_][\w]*)?\(|\)", body):
        if m.group(0) == ")":
            if stack:
                stack.pop()
            continue
        name = m.group(1) or ""
        if name == "return" and "prog" not in stack:
            return True
        stack.append(name)
    return False


def called(src: str) -> set:
    """emir* functions called in code and in callback strings."""
    code, strings = split_code(src)
    pat = re.compile(r"\b(emir[A-Za-z0-9_]*)\(")
    return set(pat.findall(code)) | {x for s in strings for x in pat.findall(s)}


SOURCES = {"MAIN": MAIN, "APP": APP, "emir_init.il": INIT, "manual test": MANUAL,
           "OCEAN": OCEAN}


@pytest.mark.parametrize("name", sorted(SOURCES))
def test_parentheses_balance(name):
    assert _parens_balanced(SOURCES[name]), name


@pytest.mark.parametrize("name", sorted(SOURCES))
def test_return_only_inside_prog(name):
    """`return()` works inside prog(), not let(): a procedure that returns
    early from a let raises a SKILL error at the worst moment."""
    bad = [p for p, body in procedures(SOURCES[name], blank=True).items()
           if return_outside_prog(body)]
    assert bad == []


def test_the_return_checker_catches_its_mutant():
    assert return_outside_prog(" let( (p) p = 1 unless( p return(nil) ) p )")
    assert not return_outside_prog(" prog( (p) unless( p return(nil) ) return(p) )")


def test_every_called_emir_function_is_defined():
    defined = set(procedures(MAIN)) | set(procedures(APP))
    for name, src in (("MAIN", MAIN), ("APP", APP)):
        assert called(src) - defined == set(), name
    assert called(MANUAL) - defined - set(procedures(MANUAL)) == set()


def test_no_procedure_is_defined_twice_across_main_and_app():
    assert set(procedures(MAIN)) & set(procedures(APP)) == set()


# ------------------------------------------------------------ headers


def test_generated_files_say_what_they_are():
    for text in (MAIN, APP, display_drf(), site_stub("/x/emir_main.il", "/x/bin/emir")):
        assert text.startswith(HEADER)
        assert "SPDX-License-Identifier: MIT" in text
        assert "Not a Cadence-supplied file." in text
    for text in (INIT, MANUAL, OCEAN):
        assert "SPDX-License-Identifier: MIT" in text


# ------------------------------------------------------------ settings


def test_main_holds_no_path_and_reads_the_environment():
    code, strings = split_code(MAIN)
    assert "@" not in code.replace("@optional", "")             # no @TEMPLATE@ field
    assert not any(re.search(r"@[A-Z]+@", x) for x in strings)
    for sym, var in (("emirInstall", "EMIR_HOME"), ("emirBin", "EMIR_BIN"),
                     ("emirWorkDir", "EMIR_WORK"), ("emirDefProc", "EMIR_PROCESS"),
                     ("emirDefLib", "EMIR_LIB"), ("emirView", "EMIR_VIEW")):
        assert f"emirSetting('{sym} \"{var}\"" in MAIN, sym
    body = procedures(MAIN)["emirSetting"]
    # a value already set wins; an empty value counts as unset, both ways
    assert "boundp(sym)" in body and 'cur != ""' in body and 'env != ""' in body
    assert 'getShellEnvVar("EMIR_UNSET_LD") == "1"' in MAIN


def test_gui_install_writes_main_unchanged_and_one_site_stub(tmp_path):
    files = install(str(tmp_path / "sk"))
    assert set(files) == {"main", "app", "drf", "site"}
    main = open(files["main"]).read()
    assert main == MAIN and str(tmp_path) not in main           # no path baked in
    stub = open(files["site"]).read()
    assert stub.startswith(HEADER) and _parens_balanced(stub)
    assert f'load("{files["main"]}")' in stub and "emirBin = " in stub
    assert f'load("{files["site"]}")' in stub.splitlines()[5]   # its header names it
    assert open(files["app"]).read() == APP


def test_site_stub_values_are_skill_literals():
    stub = site_stub('/a"b/emir_main.il', '/x\n"y"/emir', "", {"emirDefLib": 'L")(system'})
    from test_skill_injection import skill_code
    code, open_str, raw = skill_code(stub)
    assert not open_str and not raw and "system" not in code


def test_init_is_environment_driven():
    assert 'getShellEnvVar("EMIR_HOME")' in INIT and 'home == ""' in INIT
    assert '"/agent/cadence/skill"' in INIT and "load(strcat(dir \"/emir_main.il\"))" in INIT
    # drLoadDrf only behind isCallable and errset
    assert "isCallable('drLoadDrf) car(errset(drLoadDrf(" in INIT
    assert "/home" not in INIT and "@" not in INIT


# ------------------------------------------------------------ shell quoting


def sh_q(s: str) -> str:
    """emirShQ, transcribed: single quotes, a ' closed, escaped, reopened."""
    return "'" + "".join("'\\''" if c == "'" else c for c in s) + "'"


@pytest.mark.parametrize("s", ["/tmp/emir manual/run", "a'b", "'", "$(touch /tmp/x)",
                               "`id`", "a;b|c&d", "\"q\" \\ e", ""])
def test_the_quoting_algorithm_survives_the_shell(s):
    p = subprocess.run(["sh", "-c", "printf %s " + sh_q(s)], capture_output=True, text=True)
    assert p.stdout == s


def test_emirShQ_is_that_algorithm():
    body = procedures(MAIN)["emirShQ"]
    assert "substring(s i 1)" in body and "for( i 1 strlen(s)" in body
    src = MAIN[MAIN.index("procedure( emirShQ"):MAIN.index("procedure( emirTrim")]
    assert '''"'\\\\''"''' in src            # the SKILL literal for '\''


def test_every_shell_command_is_quoted():
    """ipcBeginProcess runs its string through a shell: each call takes a
    command built by emirCmdline (emirBin and the @args path quoted) or an
    xdg-open of a quoted path."""
    code, _ = split_code(MAIN)
    calls = [MAIN[m.end():MAIN.index("\n", m.end())]
             for m in re.finditer(r"ipcBeginProcess\(\s*", code)]
    assert len(calls) == 4
    for c in calls:
        assert c.startswith(("cmd ", "emirCmdline(", 'strcat("xdg-open " emirShQ(')), c
    body = procedures(MAIN)["emirCmdline"]
    assert "emirShQ(emirBin)" in body and 'emirShQ(strcat("@" argsFile))' in body
    env = procedures(MAIN)["emirEnvPrefix"]
    assert "env -u PYTHONHOME -u PYTHONPATH" in MAIN and "PYTHONUNBUFFERED=1" in env
    assert "-u LD_LIBRARY_PATH" in env and "emirUnsetLD" in env
    assert "No shell is involved" not in MAIN


def test_ipc_tail_is_never_nil():
    body = procedures(MAIN)["emirIpcData"]
    assert "rindex(buf" in body and 'or(emirIpcTail "")' in body
    assert "car(last(" not in body


# ------------------------------------------------------------ the form


def test_layout_opens_read_only():
    assert '?mode "r"' in procedures(MAIN)["emirLoadViewer"]
    assert '?mode "a"' not in MAIN and '?mode "w"' not in MAIN
    assert "?view emirView" in procedures(MAIN)["emirLoadViewer"]
    assert "emirFindLayoutWin(lib cell)" in procedures(MAIN)["emirLoadViewer"]


def test_save_lines_go_to_a_file_with_the_testbench_prefix():
    body = procedures(MAIN)["emirDoSaveLines"]
    assert '"--out" emirMainSaveFile' in body and '"--prefix" prefix' in body
    assert '"/emir_save.scs"' in body
    # the Mode starts at "choose": save lines refuse until the person picks one
    assert 'when( mode == "choose"' in body and "emirChooseMode()" in body
    assert "emirMainPrefix" in MAIN and "Testbench instance prefix" in MAIN
    # the menu item only opens the form; nothing runs on an empty form
    assert '?itemText "Spectre save lines..."\n                       ?callback "emirSaveLinesForm()"' in MAIN
    assert "emirDoSaveLines" not in procedures(MAIN)["emirSaveLinesForm"]


def test_args_file_failure_is_a_dialog_not_an_error():
    body = procedures(MAIN)["emirWriteArgs"]
    assert body.lstrip().startswith("prog(") and "car(errset(outfile(" in body
    assert "when( af emirCmdline(sub af) )" in procedures(MAIN)["emirCmd"]


def test_open_html_reads_the_form_and_guide_needs_no_missing_file():
    assert "emirFormOut()" in procedures(MAIN)["emirDoHtml"]
    assert "USER_GUIDE" not in MAIN
    assert "isFile(f)" in procedures(MAIN)["emirOpenNotes"]


# ------------------------------------------------------------ the menu


def test_menu_registers_once_on_schematic_and_layout():
    i = MAIN.index("unless( and(boundp('emirMenuRegistered) emirMenuRegistered)")
    block = MAIN[i:MAIN.index("procedure( emirAddMenu")]
    assert 'foreach( vt list("schematic" "maskLayout")' in block
    assert "deRegUserTriggers(vt nil 'emirMenuTrigger)" in block
    assert "emirMenuRegistered = t" in block
    code, strings = split_code(MAIN)
    assert code.count("deRegUserTriggers(") == 1
    assert "deUnRegUserTriggers(" not in code
    assert not any("Schematics" in s for s in strings)


def test_a_new_pulldown_for_every_window():
    trig = procedures(MAIN)["emirMenuTrigger"]
    assert "emirBuildMenu()" in trig and "boundp" not in trig
    assert "boundp('emirPulldown)" not in MAIN
    assert "emirBuildMenu()" in procedures(MAIN)["emirAddMenu"]


# ------------------------------------------------------------ the Explorer


def test_no_pdk_purposes_anywhere():
    for text in (APP, MAIN, display_drf()):
        assert not re.search(r"drawing[0-9]", text)
    assert "emirPurposes" not in APP and '"grid"' not in APP


def test_viewer_data_has_integer_band_steps(tmp_path):
    from emir.dspf import build_parser, run
    from emir.layoutview import EM_BANDS
    out = tmp_path / "v"
    run(build_parser().parse_args([DSPF, "--net", "VDD", "--process", PROC, "--currents",
                                   CUR, "--viewer", str(out), "--lib", "L"]))
    data = (out / "emir_data.il").read_text()
    assert not re.search(r"drawing[0-9]", data)
    rows = re.findall(r'list\((\d+) [\d.]+ "[^"]*" (\d+)\)', data)
    assert [(int(i), int(s)) for i, s in rows] == [(i, b[2]) for i, b in enumerate(EM_BANDS)]
    assert (out / "emir_explorer.il").read_text() == APP


def test_packets_by_step_and_one_lpp_fallback():
    assert 'sprintf(nil "EMIR_B%d" b)' in procedures(APP)["emirPacketName"]
    assert '"EMIR_SEL"' in procedures(APP)["emirPacketName"]
    new = procedures(APP)["emirNewHS"]
    assert "car(errset(geCreateHilightSet(cv emirMarkLPP)))" in new
    assert "emirSkippedHS = emirSkippedHS + 1" in new
    assert not re.search(r"list\(emirMarkLayer (?!emirMarkPurpose)", APP)   # no per-band LPP
    probe = procedures(APP)["emirDefinePackets"]
    assert "isCallable('geChangeHilightPacketName)" in probe
    assert "isCallable('drGetPacket)" in probe and "emirColourPath" in probe
    assert "colours need emir_display.drf" in probe and "all bands drawn in" in probe


def test_nil_highlight_sets_are_skipped():
    body = procedures(APP)["emirRedraw"]
    assert body.count("when( hs geAddHilightRectangle(hs") == 2
    sel = procedures(APP)["emirSelect"]
    assert "when( emirSelBandHS geAddHilightRectangle" in sel
    assert "when( emirSelHS geAddHilightRectangle" in sel


def test_explorer_start_is_wrapped_and_reports():
    body = procedures(APP)["emirExplorer"]
    assert "errset(emirExplorer1() t)" in body and "errset.errset" in body
    assert 'hiFormCancel(old)' in procedures(APP)["emirExplorer1"]


def test_closing_cleans_up_and_old_forms_leave_new_ones_alone():
    e1 = procedures(APP)["emirExplorer1"]
    assert "?callback list(closeCB closeCB)" in e1
    assert 'sprintf(nil "emirExplorerClosed(%d)" emirFormSeq)' in e1
    closed = procedures(APP)["emirExplorerClosed"]
    assert "seq == emirFormSeq" in closed and 'hiUnregZoomPanProc("emirZoomPanCB")' in closed
    assert "emirClear()" in closed and "emirForm = nil" in closed
    zp = procedures(APP)["emirZoomPanCB"]
    assert "eq(w emirWin())" in zp and "emirExplorerClosed()" in zp
    # the busy flag is reset outside the errset, so a failure cannot leave it set
    assert zp.index("emirPanBusy = nil") > zp.index(") t)")
    clear = procedures(APP)["emirClear"]
    assert "emirDelHS(" in clear and "geDeleteHilightSet" not in clear
    assert "geIsValidHilightSet(hs)" in procedures(APP)["emirDelHS"]


def test_windows_are_layout_windows_of_the_library():
    assert 'cv->cellViewType == "maskLayout"' in procedures(APP)["emirIsLayoutWin"]
    find = procedures(APP)["emirFindLayoutWin"]
    assert "w->cellView->libName == lib" in find and "emirXBlank(lib)" in find
    win = procedures(APP)["emirWin"]
    assert "emirIsLayoutWin(cur)" in win and "cur->cellView) best = cur" not in win


def test_list_is_capped_and_ranks_parse_at_any_width():
    assert "emirMaxRows = 2000" in APP
    assert "n <= emirMaxRows" in procedures(APP)["emirRedraw"]
    assert "list: first %d" in procedures(APP)["emirCountText"]
    assert "atoi(car(parseString(idx)))" in procedures(APP)["emirListCB"]
    assert "substring(idx 1 4)" not in APP
    assert 'makeTable("emirNetT" nil)' in procedures(APP)["emirRedraw"]
    assert "member(" not in procedures(APP, blank=True)["emirRedraw"]


def test_zooms_are_guarded_and_the_halo_uses_the_tunables():
    code, _ = split_code(APP)
    assert code.count("hiZoomIn(") == code.count("errset( hiZoomIn(") == 2
    sel = procedures(APP)["emirSelect"]
    assert "emirApplyHalo(ring)" in sel and '"thick"' not in sel


def test_selftest_reports_missing_functions_and_the_colour_path():
    body = procedures(APP)["emirSelfTest"]
    assert "isCallable(fn)" in body and "emirColourPath" in body


def test_display_drf_is_appended_never_replaced():
    drf = display_drf()
    assert "APPEND this to ~/display.drf" in drf and "Never replace" in drf
    assert "place this file as display.drf" not in drf
    for i in range(10):
        assert f"( display EMIR_B{i}" in drf
    assert "( display EMIR_SEL" in drf
