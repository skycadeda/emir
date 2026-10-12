"""DSPF robustness: width-less wires are a FAIL (they were classed as vias and
skipped), `is_complex` is decided once per result, quoted layer values,
`emir pins` on an unknown net, the off-net node list, and `emir info`."""
import json
import os
import re

import numpy as np
import pytest
from _helpers import CUR, DSPF, PROC, emir, many_nets_dspf, sample_text, write

from emir import waves
from emir.check import check_inputs
from emir.dspf import Network, _layer_token, _resistor, build_parser, parse, run
from emir.validate import _psf_tran


def _no_width(text: str) -> str:
    return re.sub(r" \$W=[0-9.]+ \$SI_W=[0-9.]+", "", text)


# ------------------------------------------------------------ wires with no width


@pytest.mark.parametrize("line,via,w", [
    ("Rc0 a b 12.0 $cont $A=0.0392 $VC=2", True, None),
    ("Rc0 a b 12.0 $cont $VC=2", True, None),
    ("Rc0 a b 12.0 $cont $A=0.0392", True, None),
    ("Rm1 a b 1.0 $metal1 $L=5 $W=0.75", False, 0.75),
    ("Rm1 a b 1.0 $metal1 $L=5 $SI_W=0.75", False, 0.75),
    ("Rm1 a b 1.0 $metal1 $L=5", None, None),        # was via=True, then skipped
])
def test_only_via_annotation_makes_a_via(line, via, w):
    r = _resistor(line)
    assert r["via"] is via and r["w"] == w


def test_wires_without_width_fail_the_check(tmp_path):
    """The sample with every $W removed used to read '0 wire R + 16 via R',
    pass the input check, and lose its Metal1 failures (wire ratio 0.0)."""
    d = write(tmp_path / "now.dspf", _no_width(sample_text()))
    assert "12 R with neither a width nor a cut annotation" in parse(d).summary()
    rep = check_inputs(d, process=PROC, nets=["VDD"], currents=CUR)
    fails = [f for f in rep.findings if f.level == "FAIL" and f.area == "layers"]
    assert not rep.passed and fails and "12 wire resistor(s) carry no width" in fails[0].message
    assert "Metal1: Rm1, Rm2, Rm3, Rm4 ..." in fails[0].message
    # without a process file the lint still says something is off
    rep = check_inputs(d, nets=["VDD"], currents=CUR, want_analysis=False)
    assert any("neither a width" in f.message for f in rep.findings if f.level == "WARN")
    assert any("no resistor carries a $W" in f.message for f in rep.findings if f.level == "FAIL")


def test_one_missing_width_is_named_and_the_rest_still_checked(tmp_path):
    text = sample_text().replace("Rm5 VDD:4 VDD:5 1.000000 $metal1 $L=5.0000 $W=0.750000 "
                                 "$SI_W=0.7500", "Rm5 VDD:4 VDD:5 1.000000 $metal1 $L=5.0000")
    d = write(tmp_path / "one.dspf", text)
    rep = check_inputs(d, process=PROC, nets=["VDD"], currents=CUR)
    fails = [f.message for f in rep.findings if f.level == "FAIL"]
    assert len(fails) == 1 and "1 wire resistor(s)" in fails[0] and "Rm5" in fails[0]
    a = build_parser().parse_args([d, "--net", "VDD", "--process", PROC, "--currents", CUR,
                                   "--force", "--json", str(tmp_path / "s.json")])
    r = run(a)["VDD"]
    assert r["no_width"] == ["Rm5"]
    assert "Rm5" not in [c["name"] for c in r["clusters"]]
    assert {"Rm4", "Rm6"} <= {c["name"] for c in r["clusters"]}       # its neighbours
    s = json.load(open(tmp_path / "s.json"))["nets"][0]
    assert s["no_width"] == ["Rm5"] and s["verdict"] == "FAIL"


def test_forced_run_reports_unchecked_wires_and_fails(tmp_path):
    d = write(tmp_path / "now.dspf", _no_width(sample_text()))
    p = emir("run", d, "--net", "VDD", "--process", PROC, "--currents", CUR, "--force",
             "--fail-on-violation")
    assert p.returncode == 1, p.stdout + p.stderr
    assert "12 wire(s) NOT checked (no width)" in p.stdout
    assert "were NOT checked: Rm1, Rm2" in p.stdout


def test_the_old_per_resistor_checker_is_gone():
    """`em_clusters` is the one EM judgement for every path; the unused
    `Network.em_check` still skipped vias with no cut count."""
    assert not hasattr(Network, "em_check")


# ------------------------------------------------------------ is_complex, once


def test_is_complex_scans_the_signals_once(monkeypatch):
    n = 500
    w = waves.Waves(np.arange(4.0), {f"s{k}": np.zeros(4) for k in range(n)})
    calls = []
    real = np.iscomplexobj
    monkeypatch.setattr(waves.np, "iscomplexobj", lambda v: calls.append(1) or real(v))
    for _ in range(100):
        assert w.is_complex is False
    assert len(calls) == n
    w.data = {**w.data, "h": np.zeros(4, dtype=complex)}       # a new result
    assert w.is_complex is True


def _complex_checks(tmp_path, n, monkeypatch):
    """np.iscomplexobj calls during an rcc run over every net of an n-net DSPF."""
    import contextlib
    import io
    sub = tmp_path / f"n{n}"
    sub.mkdir()
    d = many_nets_dspf(sub, n)
    t = np.linspace(0, 4e-9, 41)
    i = 1e-4 * (1 + 0.5 * np.sin(2 * np.pi * t / 1e-9))
    res = _psf_tran(str(sub / "rcc.tran"), t,
                    {f"R{k}{x}:1": i for k in range(n) for x in "ab"})
    calls = []
    real = np.iscomplexobj
    monkeypatch.setattr(waves.np, "iscomplexobj", lambda v: calls.append(1) or real(v))
    argv = [d, "--process", PROC, "--cc-sim", res, "--period", "1e-9", "--no-check"]
    a = build_parser().parse_args(argv)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        assert len(run(a)) == n
    monkeypatch.setattr(waves.np, "iscomplexobj", real)
    return len(calls)


def test_a_run_over_every_net_is_linear_in_is_complex(tmp_path, monkeypatch):
    """One scan of the saved signals per result, not one per net: doubling
    the nets doubles the checks (it used to quadruple them -- measured on
    4000 nets, 8.25 s against 2.36 s once cached)."""
    small = _complex_checks(tmp_path, 100, monkeypatch)
    big = _complex_checks(tmp_path, 200, monkeypatch)
    assert big <= 2.2 * small, (small, big)


# ------------------------------------------------------------ layer tokens


@pytest.mark.parametrize("tail,layer", [
    ('$layer="M1" $W=0.75', "M1"),
    ("$lyr='metal2' $W=0.75", "metal2"),
    ('$layer = "M1"', "M1"),
    ('$layer="" $metal1', "metal1"),                  # an empty value names nothing
])
def test_quoted_layer_values(tail, layer):
    assert _layer_token(tail.split()) == layer


def test_quoted_layer_maps_like_a_bare_one(tmp_path):
    d = write(tmp_path / "q.dspf", sample_text().replace("$metal1", '$layer="metal1"'))
    assert parse(d).layer_tokens() == ["cont", "metal1"]
    assert check_inputs(d, process=PROC, nets=["VDD"], currents=CUR).passed


# ------------------------------------------------------------ CLI


@pytest.mark.parametrize("net,hint", [("vdd", "'VDD'"), ("VDX", "'VDD'"), ("NOPE", None)])
def test_pins_on_an_unknown_net_exits_2(net, hint):
    p = emir("pins", DSPF, "--net", net)
    assert p.returncode == 2 and "is not in" in p.stderr and "Traceback" not in p.stderr
    if hint:
        assert f"did you mean {hint}?" in p.stderr
    else:
        assert "did you mean" not in p.stderr


def test_check_suggests_a_close_net_name():
    rep = check_inputs(DSPF, process=PROC, nets=["VDX"], currents=CUR)
    fix = [f.fix for f in rep.findings if f.level == "FAIL" and f.area == "nets"]
    assert fix and "did you mean 'VDD'?" in fix[0]


def test_off_net_current_nodes_are_capped():
    cur = json.dumps({**{f"X{k}:d": 0.0 for k in range(50)}, **json.loads(CUR)})
    rep = check_inputs(DSPF, process=PROC, nets=["VDD"], currents=cur)
    msg = next(f.message for f in rep.findings if "not on net" in f.message)
    assert msg.startswith("50 node(s) not on net 'VDD'") and "(+42 more)" in msg
    assert len(msg) < 200 and "X49:d" not in msg


def test_info_lists_every_simulator(tmp_path):
    fake = tmp_path / "Xyce"
    fake.write_text("#!/bin/sh\n")
    p = emir("info", env={**os.environ, "XYCE_BIN": str(fake)})
    assert p.returncode == 0, p.stderr
    rows = {ln.split()[0]: ln for ln in p.stdout.splitlines()[1:] if ln.strip()}
    for sim in ("spectre", "xyce", "ngspice"):
        assert sim in rows, p.stdout
    assert str(fake) in rows["xyce"] and "$XYCE_BIN" in rows["xyce"]
