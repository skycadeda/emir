"""rcc's IR is the simulated circuit's: node potentials from the simulated
branch currents and the resistances the simulation ran (the DSPF's), not
those scaled to the analysis temperature. Found on example 08: the DSPF
states no temperature (taken at the process file's temp_reference, 30 C),
EM is analysed at 90 C, and the IR read 2.7 % above the simulator's own
node voltages (out 6.874 mV against 6.691 mV) while every branch current
matched it to 1e-14."""
import json
import os

import jsonschema
import numpy as np
import pytest
from _helpers import DSPF, PROC, sample_text, write

from emir import rcc, schemas
from emir.dspf import analyse, build_parser, parse
from emir.engine import Process
from emir.errors import collecting
from emir.report import build, markdown
from emir.summary import summary_document
from emir.validate import _psf_tran

I_LOAD = 1e-3


def one_load(tmp) -> str:
    """1 mA, constant, from the feed through Rm1..Rm12 and Rc3 into MP3: the
    simulated circuit's drop to MP3:s is 12 x 1 ohm + 24 ohm, x 1 mA = 36 mV
    (the DSPF's resistances, the ones the simulation ran)."""
    t = np.linspace(0.0, 4e-9, 401)
    one = np.full_like(t, I_LOAD)
    sigs = {f"Rm{n}:1": one for n in range(1, 13)}
    sigs.update({"Rc0:1": 0 * one, "Rc1:1": 0 * one, "Rc2:1": 0 * one, "Rc3:1": one})
    return _psf_tran(os.path.join(str(tmp), "load.tran"), t, sigs)


def dspf_at(tmp, temp) -> str:
    """The sample rail, extracted at `temp` C (None: no temperature line)."""
    text = sample_text().replace("*|OPERATING_TEMPERATURE 100\n",
                                 "" if temp is None else f"*|OPERATING_TEMPERATURE {temp}\n")
    return write(os.path.join(str(tmp), f"rail_{temp}.dspf"), text)


@pytest.mark.parametrize("temp, stated", [(50, True), (None, False)])
def test_rcc_ir_uses_the_resistances_the_simulation_ran(tmp_path, temp, stated):
    """The DSPF at 50 C (stated, or the process file's temp_reference), EM at
    100 C: Metal1's TCR would scale each wire x1.2 (the drop would read
    38.4 mV); the simulated circuit's drop is 36 mV, static and dynamic."""
    d = parse(dspf_at(tmp_path, temp))
    proc = Process.load(PROC)
    assert proc.temp == 100 and proc.temp_ref == 50
    with collecting() as found:
        r = rcc.rcc_check(d, "VDD", proc, one_load(tmp_path), "VDD", period=1e-9,
                          log=lambda *a: None)
    assert r["ir_drop"] == pytest.approx(0.036, rel=1e-9)
    assert r["dynamic"]["worst_V"] == pytest.approx(0.036, rel=1e-9)
    assert r["loop_residual"] == 0.0 and r["n_offtree"] == 0
    assert r["r_basis"] == {"kind": "simulated", "stated": stated, "temp_C": 50.0}
    i407 = [n for n in found if n["code"] == "EMIR-I407"]
    assert len(i407) == 1 and "100 C" in i407[0]["message"] and "50 C" in i407[0]["message"]


def test_no_notice_when_the_dspf_is_at_the_analysis_temperature(tmp_path):
    d = parse(DSPF)                           # the sample: extracted at 100 C
    with collecting() as found:
        r = rcc.rcc_check(d, "VDD", Process.load(PROC), one_load(tmp_path), "VDD",
                          period=1e-9, log=lambda *a: None)
    assert r["ir_drop"] == pytest.approx(0.036, rel=1e-9)
    assert not [n for n in found if n["code"] == "EMIR-I407"]


def test_the_summary_and_the_report_say_which_resistances(tmp_path):
    argv = [dspf_at(tmp_path, 50), "--process", PROC, "--net", "VDD", "--cc-sim",
            one_load(tmp_path), "--period", "1e-9", "--power-nets", "VDD"]
    a = build_parser().parse_args(argv)
    a.argv = argv
    with collecting():
        res = analyse(a)
    doc = json.loads(json.dumps(summary_document(res), default=float))
    jsonschema.validate(doc, schemas.get("run"))
    net = doc["nets"][0]
    assert net["ir_drop_mV"] == pytest.approx(36.0, rel=1e-9)
    assert net["ir_resistance"] == {"kind": "simulated", "temp_C": 50.0, "stated": True}
    assert "EMIR-I407" in {n["code"] for n in doc["notices"]}
    md = markdown(build(doc, "summary.json"))
    assert "R as simulated: the DSPF's, at 50 C; the EM limits are at 100 C" in md
    assert "(from the simulated branch currents)" in md and "measured" not in md


def test_a_dc_solve_still_scales_each_wire_to_the_analysis_temperature(tmp_path):
    """DC currents are a model of the block, solved on the R grid at the
    analysis temperature: unchanged (38.4 mV with the wires x1.2)."""
    argv = [dspf_at(tmp_path, 50), "--process", PROC, "--net", "VDD", "--currents",
            json.dumps({"VDD": I_LOAD, "MP3:s": -I_LOAD})]
    a = build_parser().parse_args(argv)
    a.argv = argv
    with collecting():
        res = analyse(a)
    doc = json.loads(json.dumps(summary_document(res), default=float))
    net = doc["nets"][0]
    assert net["ir_drop_mV"] == pytest.approx(12 * 1.2 + 24.0, rel=1e-9)
    assert net["ir_resistance"] == {"kind": "scaled", "temp_C": 100.0, "stated": False}
