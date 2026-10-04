"""`emir_data.il` is load()ed -- executed -- by Virtuoso, so nothing that comes
from an input file may become code there: every value is a SKILL literal, an
unset library is `nil`, NaN/Inf are refused, and control characters in the
library, cell and DSPF design name are refused."""
import json

import pytest
from _helpers import CUR, DSPF, PROC, sample_text, write

from emir import site
from emir.check import check_inputs
from emir.dspf import build_parser, run
from emir.layoutview import _sk, check_viewer_names
from emir.errors import EmirError

#: a design name that closes the string, calls system() and reopens it, with a
#: closing paren and a trailing backslash for good measure
EVIL = 'x"system("id")")\\'


def skill_code(text: str) -> tuple[str, bool, list]:
    """`(code outside string literals and comments, ends inside a string,
    raw control characters found inside string literals)`."""
    code, raw, in_str, esc, comment = [], [], False, False, False
    for ch in text:
        if comment:
            if ch == "\n":
                comment = False
                code.append(ch)
            continue
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            elif ord(ch) < 0x20 or ord(ch) == 0x7f:
                raw.append(ch)
            continue
        if ch == '"':
            in_str = True
            code.append(" ")
        elif ch == ";":
            comment = True
        else:
            code.append(ch)
    return "".join(code), in_str, raw


def _viewer(tmp_path, dspf, *extra):
    out = tmp_path / "viewer"
    a = build_parser().parse_args([dspf, "--net", "VDD", "--process", PROC,
                                   "--currents", CUR, "--viewer", str(out), *extra])
    run(a)
    return (out / "emir_data.il").read_text(encoding="utf-8")


def test_sk_literals():
    assert _sk(None) == "nil" and _sk(True) == "t" and _sk(False) == "nil"
    assert _sk(3) == "3" and _sk(2.5) == "2.5"
    assert _sk('a"b\\c') == '"a\\"b\\\\c"'
    assert _sk("l1\nl2\tt") == '"l1\\nl2\\tt"'
    assert _sk("a\x01b\x7fc") == '"a?b?c"'
    for bad in (float("nan"), float("inf"), -float("inf")):
        with pytest.raises(ValueError, match="non-finite"):
            _sk(bad)


def test_design_name_cannot_become_code(tmp_path, monkeypatch):
    monkeypatch.setattr(site, "_cache", {})
    d = write(tmp_path / "evil.dspf",
              sample_text().replace('*|DESIGN "railblock"', f"*|DESIGN {EVIL}"))
    data = _viewer(tmp_path, d)
    code, open_str, raw = skill_code(data)
    assert not open_str and not raw
    assert "system" not in code, code
    assert "emirCell = " in data and 'emirCell = "x' in data
    assert data.splitlines()[0].startswith(";") and "x" not in data.splitlines()[0][1:20]


def test_mark_layer_from_the_site_file_cannot_become_code(tmp_path, monkeypatch):
    monkeypatch.setattr(site, "_cache", {"mark_layer": ['M1")system("id")(', 'dr\nawing\\']})
    data = _viewer(tmp_path, DSPF, "--lib", "L")
    code, open_str, raw = skill_code(data)
    assert not open_str and not raw
    assert "system" not in code


def test_unset_library_is_nil(tmp_path, monkeypatch):
    monkeypatch.setattr(site, "_cache", {})
    data = _viewer(tmp_path, DSPF)
    assert "emirLib = nil" in data and '"None"' not in data
    data = _viewer(tmp_path, DSPF, "--lib", "MYLIB")
    assert 'emirLib = "MYLIB"' in data


@pytest.mark.parametrize("flag,value", [("--cell", "a\nb"), ("--lib", "L\x07")])
def test_control_characters_in_lib_or_cell_are_refused(tmp_path, flag, value):
    rep = check_inputs(DSPF, process=PROC, nets=["VDD"], currents=CUR,
                       viewer=str(tmp_path / "v"), **{flag[2:]: value})
    fails = [f for f in rep.findings if f.level == "FAIL" and f.area == "output"]
    assert fails and "control character" in fails[0].message
    with pytest.raises(EmirError, match="EMIR-E501"):   # the checker stops the run
        _viewer(tmp_path, DSPF, flag, value)
    with pytest.raises(ValueError, match="control character"):   # and so does export
        _viewer(tmp_path, DSPF, flag, value, "--no-check")
    assert not (tmp_path / "viewer" / "emir_data.il").exists()


def test_control_character_in_the_design_name_is_refused(tmp_path):
    d = write(tmp_path / "bell.dspf",
              sample_text().replace('*|DESIGN "railblock"', "*|DESIGN rail\x07block"))
    assert check_viewer_names(design="rail\x07block")
    rep = check_inputs(d, process=PROC, nets=["VDD"], currents=CUR,
                       viewer=str(tmp_path / "v"), lib="L")
    assert not rep.passed
    # --cell names a clean cell, but the design name still reaches the data
    with pytest.raises(ValueError, match="DESIGN"):
        _viewer(tmp_path, d, "--cell", "rail", "--no-check")


def test_nan_currents_fail_and_write_no_viewer_data(tmp_path):
    nan = json.dumps({**json.loads(CUR), "MP3:s": float("nan")})
    rep = check_inputs(DSPF, process=PROC, nets=["VDD"], currents=nan)
    fails = [f for f in rep.findings if f.level == "FAIL" and f.area == "currents"]
    assert fails and "NaN" in fails[0].message
    out = tmp_path / "viewer"
    a = build_parser().parse_args([DSPF, "--net", "VDD", "--process", PROC, "--currents", nan,
                                   "--viewer", str(out), "--no-check"])
    with pytest.raises(ValueError, match="NaN or Inf"):
        run(a)
    assert not (out / "emir_data.il").exists()


def test_a_nan_ratio_is_a_fail(tmp_path):
    nan = json.dumps({**json.loads(CUR), "MP3:s": float("nan")})
    a = build_parser().parse_args([DSPF, "--net", "VDD", "--process", PROC, "--currents", nan,
                                   "--no-check"])
    r = run(a)["VDD"]
    assert r["clusters"] and all(c["severity"] == "FAIL" for c in r["clusters"]
                                 if c["ratio"] != c["ratio"])


def test_load_viewer_falls_back_when_the_run_named_no_library():
    from emir.selftest import _parens_balanced
    from emir.skillgui import MAIN
    assert _parens_balanced(MAIN)
    body = MAIN[MAIN.index("procedure( emirLoadViewer"):MAIN.index("procedure( emirDoViewer")]
    assert "emirBlank(lib)" in body and "emirMainLib" in body and "emirCurLibCell()" in body
