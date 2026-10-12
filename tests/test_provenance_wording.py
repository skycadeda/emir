"""Wording that must stay true: provenance labels that say
what was really measured, "Screening, not sign-off." wherever results are
presented, the sample's cut size, and the sky130 wording."""
import json
import os
import re

import yaml
from _helpers import CUR, DSPF, PROC, ROOT, emir

import emir as emir_pkg
from emir import rcc

# ------------------------------------------------------------ provenance


def test_rcc_timing_table_names_the_generated_rail():
    """The 41/521/2057-node timings came from a generated inverter bank on a
    segmented rail, not an OTA."""
    doc = " ".join(rcc.__doc__.split())
    i = doc.index("| rail nodes |")
    label = doc[max(0, i - 400):i]
    assert "original development site" in label and "inverters" in label
    assert "segmented" in label and "OTA" not in label


# ------------------------------------------------------------ screening note


def test_screening_note_everywhere_results_are_presented(tmp_path):
    note = emir_pkg.SCREENING_NOTE
    assert note == "Screening, not sign-off."
    p = emir("run", DSPF, "--net", "VDD", "--process", PROC, "--currents", CUR,
             "--html", tmp_path / "nets", "--summary-out", tmp_path / "s.json")
    assert p.returncode == 0, p.stdout + p.stderr
    last = p.stdout.strip().splitlines()[-1]
    assert last.startswith("overall verdict: ") and last.endswith(note)
    assert json.load(open(tmp_path / "s.json"))["disclaimer"] == note
    assert note in (tmp_path / "nets" / "index.html").read_text()
    assert note in (tmp_path / "nets" / "railblock_VDD.html").read_text()


# ------------------------------------------------------------ the sample


def test_sample_cut_areas_agree_with_the_cut_size():
    """Every sample contact's `$A` is its cut count times vcwidth squared, so
    the area route and the count route give the same number of cuts."""
    vcw = yaml.safe_load(open(PROC))["vias"]["Cont"]["vcwidth"]
    rows = [ln for ln in open(DSPF) if ln.startswith("Rc")]
    assert len(rows) == 4
    for ln in rows:
        a = float(re.search(r"\$A=([0-9.]+)", ln).group(1))
        n = int(re.search(r"\$VC=([0-9]+)", ln).group(1))
        assert abs(a / vcw ** 2 - n) < 1e-9, ln


# ------------------------------------------------------------ pdks wording


def test_sky130_sheet_resistance_agreement_is_about_four_percent():
    """li1: the LEF's 12.8 ohm/sq is 4.07 % above the models' 12.3, so the
    files say "about 4 %", never "within 4%"."""
    assert 4.0 < (12.8 - 12.3) / 12.3 * 100 < 4.1
    for f in ("pdks/README.md", "pdks/sky130.yaml"):
        text = " ".join(open(os.path.join(ROOT, f), encoding="utf-8").read().split())
        text = text.replace(" # ", " ")
        assert "within 4%" not in text and "about 4 %" in text, f
