"""Regression tests for the simulator routes and their readers, one per case
(the verify-sim records are in test_verify_sim.py and test_verify_coverage.py):

* the HSPICE header parses in time linear in the signal count, and a run
  reads its result once;
* `.include` / `.lib` quotes are kept; save lines go before the first
  `.alter` (EMIR-I610);
* `emir check --cc-sim` FAILs on NaN or Inf currents (EMIR-E426);
* duplicate and cut-alike signal names are refused (EMIR-E427);
* accurate messages for an Inf field, a file cut inside a value, CSDF
  complex values; the naming hint knows HSPICE's i1( .. i4( forms.

Every number is invented or computed here; no simulator runs.
"""
import json
import time

import numpy as np
import pytest
from _helpers import DSPF, PROC, emir, write

from emir import csdf, hspice, waves
from emir.errors import EmirError, collecting
from emir.opensim_netlist import Tree, Writer


def jdoc(p, rc=0):
    assert p.returncode == rc, p.stdout[-2000:] + p.stderr[-2000:]
    return json.loads(p.stdout)


# ------------------------------------------------------------ HSPICE header and one read


def _big_header(n):
    """A header record of `n` currents (built here: the writer's count fields
    hold four digits)."""
    names = ["TIME"] + [f"i(xtop.xblock{k // 100}.rpar_seg{k}" for k in range(n)]
    rec = hspice.header_text(names[:2]).replace("\n", "")[:264]
    rec += "".join(f"{t:3d}     " for t in [1] + [8] * n)
    rec += "".join(nm.ljust(((len(nm) + 16) // 16) * 16) for nm in names)
    rec += hspice.END_MARK
    rec += " " * (-len(rec) % 80)
    return [rec[i:i + 80] for i in range(0, len(rec), 80)]


def test_the_hspice_header_parses_in_linear_time():
    """100 000 signals within 2 s: re-scanning the growing record per line
    (quadratic) took far longer."""
    lines = _big_header(100_000)
    t0 = time.perf_counter()
    head, start = hspice.parse_header(lines)
    assert time.perf_counter() - t0 < 2.0
    assert len(head["names"]) == 100_001 and start == len(lines)
    assert head["names"][-1] == "i(xtop.xblock999.rpar_seg99999"


def test_an_end_mark_across_a_line_break_still_ends_the_header():
    rec = hspice.header_text(["TIME", "i(r1"]).replace("\n", "")
    body = rec[:rec.index(hspice.END_MARK)]
    rec = body + " " * ((78 - len(body)) % 80) + hspice.END_MARK   # "$&" | "%#"
    lines = [rec[i:i + 80] for i in range(0, len(rec), 80)] + [""]
    k = next(i for i, ln in enumerate(lines) if ln.endswith("$&"))
    assert lines[k + 1] == "%#"
    head, start = hspice.parse_header(lines)
    assert head["names"] == ["TIME", "i(r1"] and start == k + 2


#: the sample's branches and the loads each carries (1 mA each)
_LOADS = {**{f"rm{k}": (4 if k <= 3 else 3 if k <= 6 else 2 if k <= 9 else 1)
             for k in range(1, 13)}, **{f"rc{k}": 1 for k in range(4)}}


def _hspice_rcc_result(tmp_path):
    """save-lines --sim hspice on the sample and a .tr0 with those names:
    every branch current, 1 mA per load it feeds."""
    side = tmp_path / "rcc.tr0.names"
    d = jdoc(emir("save-lines", DSPF, "--sim", "hspice", "--names-out", side, "--format",
                  "json"))
    sig = [ln for s in d["lines"] if s.startswith((".probe", "+")) for ln in s.split()
           if "(" in ln]
    t = np.linspace(0, 4e-9, 9)
    rows = [[x, *[_LOADS[s[2:-1]] * 1e-3 for s in sig]] for x in t]
    return hspice.write_tr0ascii(str(tmp_path / "rcc.tr0"), ["TIME", *sig], [([], rows)])


def test_a_run_reads_its_result_once(tmp_path, monkeypatch):
    res = _hspice_rcc_result(tmp_path)
    s = jdoc(emir("run", DSPF, "--process", PROC, "--cc-sim", res, "--period", "1ns",
                   "--format", "json"))
    assert abs(s["nets"][0]["ir_drop_mV"] - 54.0) < 1e-6
    # in process: the input check's read is the run's
    from emir import dspf as D
    calls = []
    real = waves.read
    monkeypatch.setattr(waves, "read", lambda *a, **k: calls.append(a) or real(*a, **k))
    a = D.build_parser().parse_args([DSPF, "--process", PROC, "--cc-sim", res, "--period",
                                     "1ns"])
    with collecting(propagate=False):
        D.analyse(a)
    assert [c[0] for c in calls] == [res]


# ------------------------------------------------------------ the deck emir simulate writes


def test_include_quotes_are_kept_and_lines_go_before_alter(tmp_path):
    write(tmp_path / "models.sp", "* models\n.model nm nmos level=1\n")
    write(tmp_path / "lib.sp", "* lib\n.lib tt\n.model pm pmos level=1\n.endl\n")
    tb = write(tmp_path / "tb.sp", "* tb\n.include 'models.sp'\n.lib \"lib.sp\" tt\n"
                                   "V1 a 0 1\nR1 a 0 1k\n.tran 1n 10n\n"
                                   ".alter\nR1 a 0 2k\n.alter\nR1 a 0 3k\n.end\n")
    tree = Tree(tb)
    assert tree.top.alter is not None
    out = tmp_path / "o" / "deck.sp"
    Writer(tree, str(out), "x", inject=[".probe tran i(r1)"]).write()
    text = out.read_text().splitlines()
    assert any(ln.startswith(".include '") and ln.endswith("models.sp'") for ln in text)
    assert any(ln.startswith('.lib "') and ln.endswith('lib.sp" tt') for ln in text)
    assert text.index(".probe tran i(r1)") < text.index(".alter")
    assert text.count(".probe tran i(r1)") == 1


def test_emir_simulate_says_where_the_lines_went(tmp_path, monkeypatch):
    from _sim_fakes import install

    from emir.verify_sim import build
    install(tmp_path, monkeypatch)
    f = build("hspice", str(tmp_path / "v"))[0]
    tb = open(f["testbench"]).read().replace(".end\n", ".alter\n.param x=1\n.end\n")
    write(f["testbench"], tb)
    p = emir("simulate", f["testbench"], "--sim", "hspice", "--dspf", f["dspf"],
             "--prefix", "XI0.", "--out", tmp_path / "r.tr0", "--format", "json")
    doc = jdoc(p)
    assert "EMIR-I610" in p.stderr
    deck = open(doc["deck"]).read().splitlines()
    assert next(i for i, ln in enumerate(deck) if ln.startswith(".probe")) < deck.index(".alter")
    assert any(ln.startswith(".include '") for ln in deck)        # verify-sim's own quotes


# ------------------------------------------------------------ results that are not numbers


def _rcc_result_with(tmp_path, value):
    """The sample's branch currents as a Spectre-named PSF result, one value
    of Rm2's `value`."""
    from emir.validate import _psf_tran
    t = np.linspace(0, 4e-9, 9)
    sig = {f"{b[0].upper()}{b[1:]}:1": np.full(len(t), n * 1e-3) for b, n in _LOADS.items()}
    sig["Rm2:1"][3] = value
    return _psf_tran(str(tmp_path / "rcc.tran"), t, sig)


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_check_fails_on_currents_that_are_not_numbers(tmp_path, value):
    res = _rcc_result_with(tmp_path, value)
    d = jdoc(emir("check", DSPF, "--process", PROC, "--cc-sim", res, "--format", "json"),
             rc=2)
    bad = [f for f in d["findings"] if f["code"] == "EMIR-E426"]
    assert bad and bad[0]["level"] == "FAIL" and "Rm2:1" in bad[0]["message"]
    assert d["passed"] is False


def test_an_inf_field_is_a_value_and_a_cut_value_is_truncation(tmp_path):
    names = ["TIME", "i(r1", "i(r2"]
    rows = [[k * 1e-9, 1e-3, 2e-3] for k in range(4)]
    p = hspice.write_tr0ascii(str(tmp_path / "a.tr0"), names, [([], rows)])
    text = open(p).read()
    one = hspice.number(2e-3)
    # an Inf current reads as a value (the check refuses it: EMIR-E426) ...
    write(tmp_path / "inf.tr0", text.replace(one, "inf".rjust(len(one)), 1))
    w = waves.read(str(tmp_path / "inf.tr0"))
    assert np.isinf(w.data["i(r2)"][0]) and len(w.x) == 4
    # ... an Inf time is refused
    write(tmp_path / "t.tr0", text.replace(hspice.number(1e-9), "inf".rjust(len(one)), 1))
    with pytest.raises(EmirError, match="time column") as e:
        waves.read(str(tmp_path / "t.tr0"))
    assert e.value.code == "EMIR-E464"
    # a file that ends inside a value ("0.1000000E+" of the end marker) ...
    write(tmp_path / "cut.tr0", text.rstrip("\n")[:-2] + "\n")
    with pytest.raises(EmirError, match="ends inside a value") as e:
        waves.read(str(tmp_path / "cut.tr0"))
    assert e.value.code == "EMIR-E464" and "truncated" in e.value.message
    # ... or where the rest still reads as a number ("0.1000000"): no end marker
    write(tmp_path / "cut2.tr0", text.rstrip("\n")[:-4] + "\n")
    with pytest.raises(EmirError, match="no end marker") as e:
        waves.read(str(tmp_path / "cut2.tr0"))
    assert "truncated" in e.value.message


# ------------------------------------------------------------ names that collide


def test_duplicate_hspice_names_are_refused(tmp_path):
    rows = [[k * 1e-9, 1e-3, 2e-3] for k in range(3)]
    p = hspice.write_tr0ascii(str(tmp_path / "d.tr0"), ["TIME", "i(r1", "i(R1"], [([], rows)])
    with pytest.raises(EmirError) as e:
        waves.read(p)
    assert e.value.code == "EMIR-E427" and "i(R1)" in e.value.message


def test_9007_names_cut_alike_are_refused(tmp_path):
    a, b = "i(xbuf.rpar_vdd_seg12", "i(xbuf.rpar_vdd_seg13"
    rows = [[k * 1e-9, 1e-3, 2e-3] for k in range(3)]
    p = hspice.write_tr0ascii(str(tmp_path / "c.tr0"), ["TIME", a, b], [([], rows)],
                              version="9007")
    with pytest.raises(EmirError) as e:
        waves.read(p)
    assert e.value.code == "EMIR-E427" and "post_version=2001" in e.value.message
    # one cut name that several sidecar names start with
    p = hspice.write_tr0ascii(str(tmp_path / "s.tr0"), ["TIME", a], [([], [r[:2] for r in rows])],
                              version="9007")
    with open(p + ".names", "w") as f:
        json.dump({"map": {a.upper() + ")": ["Ra:1", 1], b.upper() + ")": ["Rb:1", 1]}}, f)
    with pytest.raises(EmirError) as e:
        waves.read(p)
    assert e.value.code == "EMIR-E427"


def test_duplicate_csdf_names_are_refused_and_complex_is_ac(tmp_path):
    p = csdf.write_csdf(str(tmp_path / "d.csd"), ["I(R1)", "I(R1)"],
                        [[[0.0, 1e-3, 1e-3], [1e-9, 1e-3, 1e-3]]])
    with pytest.raises(EmirError) as e:
        waves.read(p)
    assert e.value.code == "EMIR-E427"
    p = csdf.write_csdf(str(tmp_path / "c.csd"), ["I(R1)"], [[[0.0, 1e-3], [1e9, 1e-3]]])
    write(p, open(p).read().replace("COMPLEXVALUES='NO'", "COMPLEXVALUES='YES'")
          .replace("SWEEPVAR='Time'", "SWEEPVAR='Frequency'"))
    with pytest.raises(EmirError, match="complex values") as e:
        waves.read(p)
    assert e.value.code == "EMIR-E467" and "AC" in e.value.message


# ------------------------------------------------------------ the naming hint


def test_the_naming_hint_knows_hspice_terminal_currents():
    w = waves.Waves([0, 1], {"i1(xi0.mn0)": np.zeros(2), "i3(xi0.mn0)": np.zeros(2)})
    w.meta["path"] = "/r/cc.tr0"
    assert "SPICE style" in waves.naming_hint(w) and "cc.tr0.names" in waves.naming_hint(w)
