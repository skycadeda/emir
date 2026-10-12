"""HSPICE ASCII and CSDF results, and the HSPICE and Spectre routes.

The readers are tested on the format research's synthetic samples
(tests/data/hspice, written to the documented layout), on a real Xyce CSDF
file, and on files written by `emir.hspice` / `emir.csdf` (round trips over
many random tables). The HSPICE and Spectre routes (`save-lines`,
`simulate`) are tested up to the simulator's command line, and past it with
stand-in executables (tests/_sim_fakes.py): no licensed tool runs here, and
nothing here says what HSPICE or Spectre do -- that is verified on the
machine that runs them (`emir verify-sim`).
"""
import json
import os

import numpy as np
import pytest
from _helpers import CUR, DSPF, PROC, ROOT, emir, write

from emir import csdf, hspice, waves
from emir.errors import EmirError, collecting

DATA = os.path.join(ROOT, "tests", "data", "hspice")


def data(name):
    return os.path.join(DATA, name)


# ------------------------------------------------------------ the research samples


def test_9601_rcc_sample_reads_with_its_straddling_name_and_kcl():
    w = waves.read(data("rcc_post2_9601.tr0"))
    assert w.meta["format"] == "tr0ascii" and w.meta["post_version"] == "9601"
    assert w.kind == "tran" and len(w.x) == 20
    # names get their closing parenthesis back; the 21-character name that
    # straddles a header line break is whole
    assert list(w.data) == ["v(n1)", "i(r0)", "i(r1)", "i(xbuf.rpar_vdd_seg12)", "i(r2)",
                            "i(r3)", "i(vdd)"]
    dt = np.diff(w.x)
    assert dt.max() / dt.min() > 10                     # uneven time points, as HSPICE's
    d = w.data
    scale = np.max(np.abs(d["i(r0)"]))
    # KCL at n1 and n3, and the source: five significant digits (9601)
    assert np.max(np.abs(d["i(r0)"] - d["i(r1)"] - d["i(r2)"])) < 1e-4 * scale
    assert np.max(np.abs(d["i(r2)"] - d["i(r3)"] - d["i(xbuf.rpar_vdd_seg12)"])) < 1e-4 * scale
    assert np.max(np.abs(d["i(vdd)"] + d["i(r0)"])) < 1e-4 * scale
    # the file says it is synthetic: it names no simulator
    assert w.meta["simulator"] is None


def test_2001_sweep_sample_needs_a_table_and_each_table_closes_kcl():
    with pytest.raises(EmirError) as e:
        waves.read(data("cc_post2_2001_sweep.tr0"))
    assert e.value.code == "EMIR-E465" and "sweep:2 = 0.005" in e.value.message
    for k, il in ((1, 1e-3), (2, 5e-3)):
        w = waves.read(data("cc_post2_2001_sweep.tr0"), analysis=f"sweep:{k}")
        assert w.meta["sweep"] == {"iload_dc": il} and w.meta["table"] == k
        assert w.meta["post_version"] == "2001"
        names = [n for n in w.data if n.startswith("i")]
        assert names == ["i1(xldo.mn_bleed)", "i1(xldo.xpass_dev.mp1)", "i1(xldo.rfb1)",
                         "i1(xldo.cout)", "i(iload)"]
        total = sum(w.data[n] for n in names)
        assert np.max(np.abs(total)) < 1e-6 * np.max(np.abs(w.data["i(iload)"]))
    for bad in ("sweep:3", "sweep:0"):
        with pytest.raises(EmirError) as e:
            waves.read(data("cc_post2_2001_sweep.tr0"), analysis=bad)
        assert e.value.code == "EMIR-E465"


def test_csdf_samples_both_forms():
    w = waves.read(data("rcc_xyce_style.csd"))
    assert w.meta["format"] == "csdf" and len(w.x) == 20
    t = waves.read(data("rcc_post2_9601.tr0"))
    # the same RCC data in two formats
    np.testing.assert_allclose(w.data["I(R0)"], t.data["i(r0)"], rtol=1e-4, atol=1e-12)
    with pytest.raises(EmirError) as e:
        waves.read(data("cc_plain_sweep.csd"))
    assert e.value.code == "EMIR-E465"
    p = waves.read(data("cc_plain_sweep.csd"), analysis="sweep:2")
    total = sum(p.data[n] for n in p.data if n.startswith("i"))
    # six significant digits in this file
    assert np.max(np.abs(total)) < 1e-5 * np.max(np.abs(p.data["i(iload)"]))
    assert "5.00000e-03" in p.meta["subtitle"]


def test_real_xyce_csdf_closes_kcl_and_names_its_simulator():
    w = waves.read(data("xyce_rcc_probe.cir.csd"))
    assert w.meta["simulator"] == "xyce" and w.meta["simulator_version"] == "7.11.0"
    d = w.data
    assert np.max(np.abs(d["I(R0)"] - d["I(R1)"] - d["I(R2)"])) < 1e-9
    assert np.max(np.abs(d["I(VDD)"] + d["I(R0)"])) < 1e-12
    assert np.max(np.abs(d["I(X1:RL)"] - d["I(R1)"])) < 1e-12


def test_psfascii_sample_names_spectre():
    w = waves.read(data("rcc_tran.psfascii"))
    assert w.meta["format"] == "psfascii" and w.meta["simulator"] == "spectre"
    assert set(w.match(["R0:1", "xbuf.rpar_vdd_seg12:1"])) == {"R0:1",
                                                              "xbuf.rpar_vdd_seg12:1"}


def test_peek_reads_only_the_header():
    assert waves.peek(data("xyce_rcc_probe.cir.csd"))["simulator"] == "xyce"
    p = waves.peek(data("rcc_post2_9601.tr0"))
    assert p["format"] == "tr0ascii" and p["post_version"] == "9601"
    assert waves.peek(data("rcc_tran.psfascii"))["simulator"] == "spectre"
    assert waves.peek(os.path.join(DATA, "missing.tr0")) == {}


def test_simulator_named_by_the_copyright_field():
    assert hspice.simulator_of("Copyright (c) 1986 - 2026 by Synopsys, Inc.") == "hspice"
    assert hspice.simulator_of("synthetic sample") is None
    assert csdf.simulator_of("Xyce") == "xyce" and csdf.simulator_of("SYNTHETIC") is None


# ------------------------------------------------------------ refusals


def test_binary_hspice_is_refused_with_the_option_to_set(tmp_path):
    for order in (b"\x04\x00\x00\x00", b"\x00\x00\x00\x04"):
        p = tmp_path / "bin.tr0"
        p.write_bytes(order + b"\x00\x00\x00\x10" + order + b"\x00\x00\x01\x00"
                      + b"00010007000000009601    * x" + b"\x00" * 64)
        with pytest.raises(EmirError) as e:
            waves.read(str(p))
        assert e.value.code == "EMIR-E463" and "post=2" in e.value.message
        assert "post=2" in e.value.hint


def _good(tmp_path, version="2001", name="r.tr0"):
    t = np.linspace(0, 1e-9, 5)
    rows = [[x, 1.0, 1e-3, -1e-3] for x in t]
    return hspice.write_tr0ascii(str(tmp_path / name), ["TIME", "v(a", "i(r1", "i(vdd"],
                                 [([], rows)], version=version)


def test_malformed_hspice_files_are_coded(tmp_path):
    p = _good(tmp_path)
    text = open(p).read()
    cases = {
        "truncated": text[:text.rindex("0.1000000E+31")],
        "bad value": text.replace("0.1000000E-02", "0.10000x0E-02", 1),
        "no end": text.split("$&%#")[0],
        "short rows": text.replace("0.1000000E+31", "0.0000000E+000.1000000E+31", 1),
    }
    for why, body in cases.items():
        q = write(tmp_path / f"{why.replace(' ', '_')}.tr0", body)
        with pytest.raises(EmirError) as e:
            waves.read(q)
        assert e.value.code == "EMIR-E464", why
    ac = text.replace("  1     ", "  2     ", 1)        # the independent variable: frequency
    with pytest.raises(EmirError) as e:
        waves.read(write(tmp_path / "ac.tr0", ac))
    assert e.value.code == "EMIR-E467"


def test_malformed_csdf_files_are_coded(tmp_path):
    good = open(data("rcc_xyce_style.csd")).read()
    cases = {"no end": good.replace("#;", ""), "count": good.replace("#C 0.00000e+00 7",
                                                                      "#C 0.00000e+00 8", 1),
             "value": good.replace("4.30000e-06:2", "4.3x0000e-06:2", 1)}
    for why, body in cases.items():
        with pytest.raises(EmirError) as e:
            waves.read(write(tmp_path / f"{why.replace(' ', '_')}.csd", body))
        assert e.value.code == "EMIR-E466", why
    with pytest.raises(EmirError) as e:
        waves.read(write(tmp_path / "ac.csd", good.replace("SWEEPVAR='Time'",
                                                             "SWEEPVAR='Freq'")))
    assert e.value.code == "EMIR-E467"


def test_binary_psf_error_gives_the_conversion_command(tmp_path):
    p = tmp_path / "tran.tran"
    p.write_bytes(b"\x00\x00\x04\x00PSFversion\x00" + b"\x00" * 64)
    with pytest.raises(EmirError) as e:
        waves.read(str(p))
    assert e.value.code == "EMIR-E408"
    assert f"psf -i {p} -o {p}.psfascii" in e.value.message
    assert e.value.next_steps(path=str(p))[0]["command"] == f"psf -i {p} -o {p}.psfascii"


# ------------------------------------------------------------ the writers (property tests)


@pytest.mark.parametrize("version", ["9007", "9601", "2001"])
def test_hspice_round_trip_over_random_tables(tmp_path, version):
    rng = np.random.default_rng(int(version))
    digits = hspice.DIGITS[version]
    for case in range(25):
        n = int(rng.integers(1, 30))
        npts = int(rng.integers(1, 40))
        # names long enough to straddle the 80-column header lines (16 in 9007)
        names = ["TIME"] + [f"i(x{case}.{'r' * int(rng.integers(1, 30))}{k}"
                            for k in range(n)]
        if version == "9007":
            names = ["TIME"] + [f"i(r{k}" for k in range(n)]
        t = np.sort(rng.uniform(0, 1e-6, npts))
        vals = rng.normal(size=(npts, n)) * 10.0 ** rng.integers(-9, 2, size=(1, n))
        rows = [[ti, *v] for ti, v in zip(t, vals)]
        sweeps = int(rng.integers(0, 3))
        if sweeps:
            tables = [([float(k + 1)], rows) for k in range(sweeps + 1)]
            p = hspice.write_tr0ascii(str(tmp_path / f"r{case}.tr0"), names, tables,
                                      version=version, sweep_names=["vsw"])
            pick = int(rng.integers(1, sweeps + 2))
            w = hspice.read_tr0ascii(p, analysis=f"sweep:{pick}")
            assert w.meta["sweep"] == {"vsw": float(pick)}
        else:
            p = hspice.write_tr0ascii(str(tmp_path / f"r{case}.tr0"), names, [([], rows)],
                                      version=version)
            w = hspice.read_tr0ascii(p)
        assert list(w.data) == [hspice.normal_name(x) for x in names[1:]]
        tol = 0.6 * 10.0 ** (1 - digits)
        np.testing.assert_allclose(w.x, t, rtol=tol, atol=1e-300)
        for j, nm in enumerate(names[1:]):
            np.testing.assert_allclose(w.data[hspice.normal_name(nm)], vals[:, j], rtol=tol,
                                       atol=1e-300)


def test_hspice_number_format():
    assert hspice.number(1.234567e-4, "2001") == "0.1234567E-03"
    assert hspice.number(-1.234567e-4, "2001") == "-.1234567E-03"
    assert hspice.number(1.2345e-4, "9601") == "0.12345E-03"
    assert hspice.number(1.2345e-4, "9007") == " .12345E-03"
    assert hspice.number(0.0, "9601") == "0.00000E+00"
    assert hspice.number(0.0999999999, "2001") == "0.1000000E+00"
    assert hspice.end_marker("9601") == "0.10000E+31"
    with pytest.raises(EmirError) as e:
        hspice.number(float("nan"))
    assert e.value.code == "EMIR-E905"


def test_csdf_round_trip_both_forms(tmp_path):
    rng = np.random.default_rng(7)
    for indexed in (True, False):
        names = [f"I(R{k})" for k in range(9)]
        blocks = []
        for _ in range(2):
            t = np.sort(rng.uniform(0, 1e-6, 12))
            blocks.append([[ti, *rng.normal(size=9) * 1e-3] for ti in t])
        p = csdf.write_csdf(str(tmp_path / f"x{indexed}.csd"), names, blocks, indexed=indexed)
        for k in (1, 2):
            w = csdf.read_csdf(p, analysis=f"sweep:{k}")
            got = np.column_stack([w.data[n] for n in names])
            np.testing.assert_allclose(got, np.array(blocks[k - 1])[:, 1:], rtol=1e-7)


# ------------------------------------------------------------ names


def test_9007_names_cut_at_16_match_the_sidecar(tmp_path):
    full = "i(xbuf.rpar_vdd_seg12"
    t = np.linspace(0, 1e-9, 4)
    p = hspice.write_tr0ascii(str(tmp_path / "old.tr0"), ["TIME", full, "i(r0"],
                              [([], [[x, 2e-3, 1e-3] for x in t])], version="9007")
    with open(p + ".names", "w") as f:
        json.dump({"map": {"I(XBUF.RPAR_VDD_SEG12)": ["xbuf.rpar_vdd_seg12:1", 1],
                           "I(R0)": ["R0:1", 1]}}, f)
    with collecting() as notes:
        w = waves.read(p)
    assert "xbuf.rpar_vdd_seg12:1" in w.data and "R0:1" in w.data
    assert any(n["code"] == "EMIR-W466" for n in notes)


# ------------------------------------------------------------ save-lines --sim hspice


def test_save_lines_hspice_rcc_and_its_sidecar(tmp_path):
    side = tmp_path / "res.tr0.names"
    p = emir("save-lines", DSPF, "--mode", "rcc", "--sim", "hspice", "--prefix", "XI0.",
             "--names-out", side, "--format", "json")
    assert p.returncode == 0, p.stderr
    doc = json.loads(p.stdout)
    assert doc["simulator"] == "hspice" and doc["raw"].endswith("res.tr0")
    assert doc["lines"][1] == ".option probe post=2 post_version=2001"
    assert doc["lines"][2].startswith(".probe tran i(xi0.rm1) i(xi0.rm2)")
    assert all(ln.startswith("+ ") for ln in doc["lines"][3:])
    sc = json.load(open(side))
    assert sc["simulator"] == "hspice" and sc["map"]["I(XI0.RC3)"] == ["Rc3:1", 1]


def test_hspice_route_end_to_end_through_the_run(tmp_path):
    """save-lines --sim hspice, a .tr0 written with those names (the
    branch currents of the sample's DC loads), then emir run: the selftest's
    hand-checked numbers (1.667x, 54 mV)."""
    from emir.verify_sim import DC
    side = tmp_path / "rcc.tr0.names"
    p = emir("save-lines", DSPF, "--sim", "hspice", "--names-out", side, "--format", "json")
    assert p.returncode == 0, p.stderr
    names = list(json.loads(p.stdout)["names"])
    t = np.linspace(0, 4e-9, 9)
    cols = [DC["branch_A"][json.load(open(side))["map"][key][0].split(":")[0]]
            for key in names]
    rows = [[x, *cols] for x in t]
    res = hspice.write_tr0ascii(str(tmp_path / "rcc.tr0"),
                                ["TIME"] + [k.lower() for k in names], [([], rows)])
    r = emir("run", DSPF, "--process", PROC, "--cc-sim", res, "--sim", "hspice",
             "--period", "1ns", "--format", "json")
    assert r.returncode == 0, r.stderr
    s = json.loads(r.stdout)
    net = s["nets"][0]
    assert net["mode"] == "rcc"
    assert abs(net["max_ratio"]["all"] - 4 / 2.4) < 1e-6
    assert abs(net["ir_drop_mV"] - 54.0) < 1e-6
    v = s["simulator_verification"]
    assert (v["status"], v["reason"]) == ("not verified", "version unknown")
    assert v["simulator"] == "hspice"
    # save-lines' sidecar does not know the version: never matched (W416)
    assert any(n["code"] == "EMIR-W416" for n in s["notices"])


# ------------------------------------------------------------ simulate --sim hspice|spectre


def _verify_bench(tmp_path, sim):
    from emir.verify_sim import build
    return build(sim, str(tmp_path / sim))


def test_simulate_hspice_plans_without_run(tmp_path, monkeypatch):
    monkeypatch.delenv("HSPICE_BIN", raising=False)
    f = _verify_bench(tmp_path, "hspice")[0]
    p = emir("simulate", f["testbench"], "--sim", "hspice", "--dspf", f["dspf"], "--mode",
             "rcc", "--out", tmp_path / "out" / "r.raw", "--format", "json",
             env={**os.environ, "PATH": "/usr/bin:/bin"})
    assert p.returncode == 0, p.stderr
    doc = json.loads(p.stdout)
    assert doc["ran"] is False and doc["result"].endswith("r.tr0")
    assert doc["command"][1:] == ["-i", "r.hspice.sp", "-o", "r"]
    assert doc["next_steps"][0]["command"].endswith("--run --format json")
    deck = open(doc["deck"]).read()
    assert ".option probe post=2 post_version=2001" in deck and ".probe tran i(xi0.rm1)" in deck
    assert "EMIR-I661" in p.stderr
    assert json.load(open(doc["names_sidecar"]))["simulator"] == "hspice"


def test_simulate_licensed_run_without_the_simulator_exits_3(tmp_path, monkeypatch):
    monkeypatch.setenv("HSPICE_BIN", str(tmp_path / "nowhere" / "hspice"))
    f = _verify_bench(tmp_path, "hspice")[0]
    p = emir("simulate", f["testbench"], "--sim", "hspice", "--dspf", f["dspf"], "--mode",
             "rcc", "--out", tmp_path / "r.tr0", "--run", "--format", "json")
    assert p.returncode == 3 and json.loads(p.stdout)["code"] == "EMIR-E662"


def test_simulate_spectre_needs_a_prefix(tmp_path):
    f = _verify_bench(tmp_path, "spectre")[0]
    base = ["simulate", f["testbench"], "--sim", "spectre", "--dspf", f["dspf"],
            "--format", "json"]
    p = emir(*base)
    assert p.returncode == 2 and json.loads(p.stdout)["code"] == "EMIR-E507"
    p = emir(*base, "--prefix", "XI0.", "--probe")      # gone in 1.1
    assert p.returncode == 2 and json.loads(p.stdout)["code"] == "EMIR-E507"
    p = emir(*base, "--prefix", "XI0.", "--mode", "rcc", "--out", tmp_path / "s.raw")
    assert p.returncode == 0, p.stderr
    doc = json.loads(p.stdout)
    assert doc["ran"] is False and doc["command"][2:] == ["-format", "psfascii", "-raw",
                                                          "s.raw"]
    deck = open(doc["deck"]).read()
    assert "simulator lang=spectre" in deck and f'include "{f["testbench"]}"' in deck
    save = open(os.path.join(os.path.dirname(doc["deck"]), "s.save.scs")).read()
    assert "save XI0.Rm1:1 XI0.Rm2:1" in save


def test_sim_args_reach_the_command_line(tmp_path):
    f = _verify_bench(tmp_path, "spectre")[0]
    p = emir("simulate", f["testbench"], "--sim", "spectre", "--dspf", f["dspf"], "--prefix",
             "XI0.", "--mode", "rcc", "--sim-args", "+preset=cx +postlpreset=off",
             "--out", tmp_path / "s.raw", "--format", "json")
    assert json.loads(p.stdout)["command"][-2:] == ["+preset=cx", "+postlpreset=off"]


@pytest.mark.parametrize("sim", ["hspice", "spectre"])
def test_simulate_run_with_a_stand_in_reads_the_result(tmp_path, monkeypatch, sim):
    from _sim_fakes import install
    install(tmp_path, monkeypatch)
    f = _verify_bench(tmp_path, sim)[0]                     # dc loads, rcc
    out = tmp_path / "res" / ("r.tr0" if sim == "hspice" else "r.raw")
    p = emir("simulate", f["testbench"], "--sim", sim, "--dspf", f["dspf"], "--mode", "rcc",
             "--prefix", "XI0.", "--out", out, "--run", "--process", PROC,
             "--format", "json")
    assert p.returncode == 0, p.stderr
    doc = json.loads(p.stdout)
    assert doc["ran"] and not doc["missing"] and doc["found"] == 16
    assert doc["simulator_version"].startswith("fake" + sim)
    r = emir("run", f["dspf"], "--process", PROC, "--cc-sim", out, "--mode", "rcc",
             "--period", "10ns", "--format", "json")
    assert r.returncode == 0, r.stderr
    s = json.loads(r.stdout)
    assert abs(s["nets"][0]["ir_drop_mV"] - 54.0) < 1e-6
    v = s["simulator_verification"]
    assert v["status"] == "not verified" and v["simulator"] == sim
    assert v["detected_from"] == ("result header" if sim == "spectre" else "names sidecar")


def test_dc_currents_have_no_simulator_to_verify(tmp_path):
    p = emir("run", DSPF, "--process", PROC, "--currents", CUR, "--format", "json")
    s = json.loads(p.stdout)
    assert s["simulator_verification"]["status"] == "not applicable"
    assert not any(n["code"] == "EMIR-W464" for n in s["notices"])
