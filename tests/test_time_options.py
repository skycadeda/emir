"""--period (and --sim-timeout) read a time as the testbench writes it. A
real agent told "the activity repeats every 10 ns" wrote `--period 10n`, and
emir refused it with a bare argparse 'invalid float value' (eval card
opensource-xyce, real_options)."""
import argparse
import json

import pytest
from _helpers import DSPF, PROC, emir

from emir import mcp_tools
from emir.output import seconds


@pytest.mark.parametrize("text, value", [("1e-08", 1e-8), ("10e-9", 1e-8), ("10n", 1e-8),
                                         ("10ns", 1e-8), ("1.5us", 1.5e-6), ("4ns", 4e-9),
                                         ("250p", 2.5e-10), ("2ms", 2e-3), ("3600", 3600.0),
                                         ("10s", 10.0), ("1u", 1e-6)])
def test_seconds_reads_spice_times(text, value):
    assert seconds(text) == pytest.approx(value, rel=1e-12)


@pytest.mark.parametrize("text, value", [("4NS", 4e-9), ("10NS", 1e-8), ("1.5US", 1.5e-6),
                                         ("250P", 2.5e-10), ("2MS", 2e-3), ("1Us", 1e-6),
                                         ("3\u03bcs", 3e-6)])
def test_seconds_reads_a_suffix_in_any_case_as_spice_does(text, value):
    """SPICE reads its suffixes in any case (M is milli there too); a person
    who types 4NS meant 4 ns."""
    assert seconds(text) == pytest.approx(value, rel=1e-12)
    p = emir("next", "--dspf", DSPF, "--process", PROC, "--period", text, "--format", "json")
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["inputs"]["period"] == pytest.approx(value)


@pytest.mark.parametrize("text", ["10x", "n", "10 meg", "10MEG", "1Meg", "1megs", "1e400", "",
                                  "ten ns"])
def test_seconds_refuses_what_is_not_a_time(text):
    with pytest.raises(argparse.ArgumentTypeError):
        seconds(text)


def test_next_and_run_take_10n():
    p = emir("next", "--dspf", DSPF, "--process", PROC, "--period", "10n", "--format", "json")
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["inputs"]["period"] == pytest.approx(1e-8)
    p = emir("run", DSPF, "--process", PROC, "--net", "VDD", "--currents",
             json.dumps({"VDD": 1e-3, "MP3:s": -1e-3}), "--period", "10ns", "--format", "json")
    assert p.returncode == 0, p.stderr


def test_a_bad_time_says_what_a_time_is():
    p = emir("next", "--dspf", DSPF, "--process", PROC, "--period", "10x", "--format", "json")
    d = json.loads(p.stdout)
    assert p.returncode == 2 and d["code"] == "EMIR-E507"
    assert "seconds as a number (1e-08) or with a SPICE suffix (10n, 10ns" in d["message"]


def test_the_mcp_parameter_stays_a_number():
    p = {x.name: x for x in mcp_tools.specs()["emir_run"].params}["period"]
    assert p.kind == "number" and p.schema()["type"] == "number"
