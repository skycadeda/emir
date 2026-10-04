"""The method's physics, as the technical-accuracy review checked it: the
self-heating budget is the person's, resistance follows each layer from the
DSPF's own temperature, an undrated average limit above its temperature is
said to be non-conservative, reversing currents and marginal via arrays are
listed as not checked, and no estimated limit is applied.

Every number here is invented; the sample's hand values are in
emir/selftest.py."""
import json
import math

import numpy as np
import pytest
import yaml
from _helpers import CUR, DSPF, PROC, emir, sample_text, write

from emir.check import Report, check_process
from emir.engine import Process


def _proc(tmp_path, name="p.yaml", **top):
    d = yaml.safe_load(open(PROC, encoding="utf-8"))
    for k, v in top.items():
        if v is None:
            d.pop(k, None)
        else:
            d[k] = v
    return d, write(tmp_path / name, yaml.safe_dump(d))


def _run(tmp_path, dspf=DSPF, proc=PROC, *extra, name="s.json"):
    out = tmp_path / name
    p = emir("run", dspf, "--process", proc, "--currents", CUR, "--summary-out", out, *extra)
    assert p.returncode == 0, p.stderr
    return json.loads(out.read_text()), p


# ------------------------------------------------------------ the self-heating budget


def test_the_self_heating_budget_is_never_raised(tmp_path):
    """sqrt(0.288 x 5) = 1.2 mA/um is below the 3.2 mA/um avg limit: it once
    became delta-T 35.6 C, silently, loosening the rms check."""
    d, path = _proc(tmp_path)
    d["layers"]["Metal1"]["jmax_rms_k"] = 0.288
    path = write(tmp_path / "p.yaml", yaml.safe_dump(d))
    proc = Process.load(path)
    m1 = proc.layers["Metal1"]
    assert m1["delta_t"] == 5.0
    assert m1["jmax"]["rms"] == pytest.approx(math.sqrt(0.288 * 5.0) * 1e-3)
    rep = Report()
    check_process(path, rep)
    w = [f for f in rep.findings if f.code == "EMIR-W301"]
    assert w and "rms governs" in w[0].message and "35.6" in w[0].message
    s, _ = _run(tmp_path, DSPF, path)
    net = s["nets"][0]
    # 4 mA in 0.75 um = 5.33 mA/um against 1.2 mA/um rms: rms governs now
    assert net["governing_metric"] == "rms"
    assert net["max_ratio"]["all"] == pytest.approx((4 / 0.75) / math.sqrt(1.44), rel=1e-6)


# ------------------------------------------------------------ resistance and temperature


def test_resistance_scales_per_layer_from_the_dspf_temperature(tmp_path):
    """The sample DSPF states 100 C. At 150 C its Metal1 scales by
    f(150)/f(100) = 1.4/1.2 (it was 1.4, from temp_reference), and the
    contact is not scaled (it was): 30 x 7/6 + 24 = 59 mV."""
    s, p = _run(tmp_path, DSPF, PROC, "--em-temp", "150")
    assert s["nets"][0]["ir_drop_mV"] == pytest.approx(30.0 * 1.4 / 1.2 + 24.0, rel=1e-9)
    assert "Metal1 x1.1667; vias not scaled" in p.stdout


def test_a_dspf_with_no_temperature_is_taken_at_temp_reference(tmp_path):
    text = sample_text().replace("*|OPERATING_TEMPERATURE 100\n", "")
    dspf = write(tmp_path / "x.dspf", text)
    s, _ = _run(tmp_path, dspf, PROC, "--em-temp", "100")
    # from temp_reference (50 C) to 100 C: 1.2 on the metal, the contact as is
    assert s["nets"][0]["ir_drop_mV"] == pytest.approx(30.0 * 1.2 + 24.0, rel=1e-9)


# ------------------------------------------------------------ derating


def test_an_undrated_average_limit_above_em_tref_is_said_non_conservative(tmp_path):
    s, p = _run(tmp_path, DSPF, PROC, "--em-temp", "150")
    assert "EMIR-W315" in p.stdout
    gap = s["not_checked"]["avg_not_derated"]
    assert gap["temp_C"] == 150 and gap["em_tref_C"] == 100 and gap["table_top_C"] is None
    r = emir("report", "--from", tmp_path / "s.json")
    assert "NOT conservative" in r.stdout
    # at em_tref: nothing to say
    s, p = _run(tmp_path, DSPF, PROC, name="t.json")
    assert s["not_checked"]["avg_not_derated"] is None and "EMIR-W315" not in p.stdout


def test_a_derating_table_is_checked_at_its_ends_and_at_em_tref(tmp_path):
    _, path = _proc(tmp_path, jmax_factor={25: 4.0, 100: 1.0, 125: 0.7})
    rep = Report()
    check_process(path, rep, em_temp=150.0)
    hot = [f for f in rep.findings if f.code == "EMIR-W305"]
    assert hot and "NOT conservative" in hot[0].message
    s, _ = _run(tmp_path, DSPF, path, "--em-temp", "150")
    assert s["not_checked"]["avg_not_derated"]["table_top_C"] == 125
    _, path = _proc(tmp_path, name="q.yaml", jmax_factor={25: 4.0, 100: 0.9, 125: 0.7})
    rep = Report()
    check_process(path, rep)
    assert any(f.code == "EMIR-W316" for f in rep.findings)
    _, path = _proc(tmp_path, name="r.yaml", jmax_factor={25: 4.0, 80: 1.5})
    rep = Report()
    check_process(path, rep)
    assert any(f.code == "EMIR-W305" and "em_tref" in f.message for f in rep.findings)


# ------------------------------------------------------------ reversing currents, via arrays


def _clusters(Ie_m, proc=None, cuts_text=None):
    import os
    import tempfile

    from emir.dspf import Network, em_clusters, map_layers, parse
    text = sample_text() if cuts_text is None else cuts_text
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "x.dspf")
        write(p, text)
        dspf = parse(p)
    proc = proc or Process.load(PROC)
    nw = Network(dspf, "VDD")
    notes = {}
    em_clusters(nw, Ie_m, proc, map_layers(dspf, proc, log=lambda *a: None), notes=notes)
    return nw, notes


def test_a_reversing_current_is_listed_as_not_checked():
    """+-I averages to zero: the avg check passes on the signed average, the
    least conservative reading; the average of |I| is listed."""
    from emir.dspf import Network, parse
    nw = Network(parse(DSPF), "VDD")
    n = len(nw.res)
    i = [k for k, r in enumerate(nw.res) if r["name"] == "Rm1"][0]
    avg = np.full(n, 1e-6)
    absavg = np.full(n, 1e-6)
    absavg[i] = 3.0e-3                 # 3 mA in 0.75 um = 4 mA/um: 1.25x the avg limit
    Ie = {"avg": avg, "rms": absavg.copy(), "peak": absavg.copy(), "absavg": absavg}
    _, notes = _clusters(Ie)
    rows = notes["bidirectional"]
    assert [r["name"] for r in rows] == ["Rm1"]
    assert rows[0]["abs_avg_ratio"] == pytest.approx(4.0 / 3.2)
    assert rows[0]["avg_ratio"] < 0.01


def test_a_marginal_via_array_is_listed():
    """A via array's ratio is its mean per cut: one at 0.95x is marginal."""
    text = sample_text().replace("$cont", "$cont $N=4", 1)
    import os
    import tempfile

    from emir.dspf import Network, parse
    with tempfile.TemporaryDirectory() as d:
        dspf = parse(write(os.path.join(d, "x.dspf"), text))
    nw = Network(dspf, "VDD")
    via = next(j for j, r in enumerate(nw.res) if r.get("cuts") == 4)
    cur = np.zeros(len(nw.res))
    cur[via] = 0.95 * 0.75e-3 * 4     # 0.95x of 0.75 mA per cut over 4 cuts
    Ie = {"avg": cur, "rms": cur, "peak": cur}
    _, notes = _clusters(Ie, cuts_text=text)
    assert [r["cuts"] for r in notes["via_crowding"]] == [4]
    assert notes["via_crowding"][0]["ratio"] == pytest.approx(0.95)


def test_estimated_via_limits_are_not_applied(tmp_path):
    d, _ = _proc(tmp_path)
    d["vias"]["Cont"].update(imax_rms=0.01, imax_peak=0.01, rms_peak_estimate=True)
    path = write(tmp_path / "e.yaml", yaml.safe_dump(d))
    proc = Process.load(path)
    assert proc.vias["Cont"]["imax"]["rms"] is None and proc.vias["Cont"]["imax"]["peak"] is None
    s, _ = _run(tmp_path, DSPF, path)
    # 0.01 mA would have failed Rc3 at 100x; it is not applied
    assert s["nets"][0]["max_ratio"]["via"] == pytest.approx(1.0 / 0.75)
    missing = {e["name"]: e["metrics"] for e in s["not_checked"]["limits_missing"]}
    assert set(missing["Cont"]) == {"rms", "peak"}


def test_the_plan_says_no_more_than_was_measured():
    p = emir("next", "--dspf", DSPF, "--process", PROC, "--format", "json")
    why = json.loads(p.stdout)["mode"]["why"]
    assert "as fast" not in why and "exact" in why
