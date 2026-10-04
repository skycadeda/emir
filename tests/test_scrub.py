"""Content that must not depend on any licensed PDK: the viewer palette is
EMIR's own, the EM temperature has no hidden default, and the HTML footer
states no rule of a particular PDK."""

import pytest
import yaml
from _helpers import PROC, write

from emir.check import Report, check_process
from emir.engine import Process
from emir import style as S
from emir.layoutview import BAND_RGB, N_STEPS

# ------------------------------------------------------------ palette


def _hex(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def test_band_palette_is_emirs_one_scale():
    """The viewers' twelve steps are the charts' scale (emir/style.py): the
    blue ramp at ten even steps, then the warn amber and the failure red."""
    assert len(BAND_RGB) == N_STEPS + 2 == 12
    assert BAND_RGB[:N_STEPS] == [_hex(S.ramp(k / (N_STEPS - 1))) for k in range(N_STEPS)]
    assert BAND_RGB[0] == _hex(S.EM_RAMP[0]) and BAND_RGB[N_STEPS - 1] == _hex(S.EM_RAMP[-1])
    assert BAND_RGB[N_STEPS:] == [_hex(S.WARN), _hex(S.FAIL)]


def test_report_and_layout_share_the_scale():
    """The HTML pages take their colours from the same module."""
    from emir.dashboard import CSS, legend_html
    assert S.WARN in CSS and S.FAIL in CSS
    html = legend_html("em", 0.8, unchecked=True)
    for c in (S.EM_RAMP[0], S.EM_RAMP[-1], S.WARN, S.FAIL, S.UNCHECKED, S.MARKER):
        assert c in html, c


def test_display_drf_colours_are_the_band_ramp():
    from emir.skillgui import display_drf
    rows = [ln.split() for ln in display_drf().splitlines()
            if ln.strip().startswith("( display EMIR_C")]
    assert [tuple(int(x) for x in r[3:6]) for r in rows] == BAND_RGB


def test_explorer_carries_no_colour_table_of_its_own():
    from emir.skillapp import APP
    assert "emirBandRGB" not in APP and "emirDisplayName" not in APP


# ------------------------------------------------------------ em_tref


def test_em_tref_is_required():
    d = yaml.safe_load(open(PROC))
    del d["em_tref"]
    with pytest.raises(ValueError, match="em_tref"):
        Process(d)


def test_checker_fails_a_process_without_em_tref(tmp_path):
    d = yaml.safe_load(open(PROC))
    del d["em_tref"]
    rep = Report()
    assert check_process(write(tmp_path / "p.yaml", yaml.safe_dump(d)), rep) is None
    fails = [f for f in rep.findings if f.level == "FAIL"]
    assert len(fails) == 1 and "em_tref" in fails[0].message and "no default" in fails[0].message


def test_em_tref_is_what_the_file_says():
    d = yaml.safe_load(open(PROC))
    assert Process(d).temp == Process(d).em_tref == d["em_tref"]
    assert Process(d, temp=85.0).temp == 85.0


# ------------------------------------------------------------ HTML footer
