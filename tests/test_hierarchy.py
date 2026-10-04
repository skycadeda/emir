"""The hierarchy flow (`emir/hier.py`, `emir/hier_cli.py`, `emir/plan_hierarchy.py`):
block abstracts, the top-level run with blocks black-boxed, the alignment
modes, budgets, port voltages and back-annotation, `emir check` / `emir next`
with abstracts, and every refusal's code.

The inputs are the synthetic chip of `examples/05_chip_level` (an ADC block
with one supply pin, a PLL block with two, and the top), written by its
`make_inputs.py`; no simulator is needed. Hand numbers are in that README.
"""
import contextlib
import copy
import importlib.util
import io
import json
import os
import shlex
import types

import jsonschema
import numpy as np
import pytest
from _helpers import ROOT, emir, write

from emir import cli, hier, schemas
from emir.dspf import Network, analyse, build_parser, parse
from emir.errors import EmirError
from emir.hier_cli import Options

EXAMPLE = os.path.join(ROOT, "examples", "05_chip_level")


def _make_inputs():
    spec = importlib.util.spec_from_file_location(
        "make_inputs", os.path.join(EXAMPLE, "make_inputs.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MI = _make_inputs()


def quiet_main(args):
    with contextlib.redirect_stdout(io.StringIO()) as o, \
            contextlib.redirect_stderr(io.StringIO()) as e:
        rc = cli.main([str(x) for x in args])
    return rc, o.getvalue(), e.getvalue()


def validate(doc):
    name = doc["schema"].split(".", 1)[1].split("/")[0]
    jsonschema.Draft202012Validator(schemas.get(name)).validate(doc)
    return doc


def jmain(*args, rc=0):
    """`emir ARGS --format json` in-process: exit status, ONE valid document."""
    code, out, err = quiet_main([*args, "--format", "json"])
    assert code == rc, (code, out[-1500:], err[-1500:])
    return validate(json.loads(out)), err


@pytest.fixture(scope="module")
def ex(tmp_path_factory):
    d = str(tmp_path_factory.mktemp("chip"))
    with contextlib.redirect_stdout(io.StringIO()):
        MI.main(d)
    p = {k: os.path.join(d, k) for k in ("adc.dspf", "pll.dspf", "top.dspf", "flat.dspf",
                                         "process.yaml", "adc.raw", "pll.raw", "flat.raw",
                                         "adc_burst.raw")}
    P = p["process.yaml"]
    for name, raw, per, pts in (("adc", "adc.raw", "10e-9", "4000"),
                                ("pll", "pll.raw", "2.5e-9", "1000"),
                                ("burst", "adc_burst.raw", "10e-9", "4000")):
        dsp = p["pll.dspf"] if name == "pll" else p["adc.dspf"]
        out = os.path.join(d, f"{name}.abstract.json")
        rc, o, e = quiet_main(["abstract", dsp, "--process", P, "--mode", "cc-grid", "--cc-sim", p[raw],
                               "--period", per, "--points", pts, "--vdd", "1.0", "-o", out])
        assert rc == 0, o + e
        p[f"{name}.abstract"] = out
    p["dir"] = d
    p["P"] = P
    return types.SimpleNamespace(**{k.replace(".", "_"): v for k, v in p.items()})


def top_args(ex, *extra, align="sync", place=True):
    a = [ex.top_dspf, "--process", ex.P, "--net", "VDD",
         "--abstract", f"{ex.adc_abstract}@XADC", "--abstract", f"{ex.pll_abstract}@XPLL"]
    if place:
        a += ["--place", "XPLL=40,0,R0"]
    return a + ["--align", align, *extra]


def run_lib(*args):
    a = build_parser().parse_args([str(x) for x in args])
    a.argv, a.command = [str(x) for x in args], "emir run"
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return analyse(a)


def by_name(res, net="VDD"):
    r = res.solved[net]
    return {b["name"]: {m: float(r["Ie"][m][i]) for m in ("avg", "rms", "peak")}
            for i, b in enumerate(r["network"].res)}


# ===================================================================== the pieces


def test_with_short_merges_the_ports_and_keeps_the_original(ex):
    d = parse(ex.pll_dspf)
    assert hier.ports_of(d, "VDDP") == ["VDDP", "VDDP:E"]
    d2, feed = hier.with_short(d, "VDDP", ["VDDP", "VDDP:E"])
    assert feed == "VDDP"
    r3 = next(r for r in d2.res if r["name"] == "Rm3")
    assert (r3["n1"], r3["n2"], r3["_n1o"], r3["_n2o"]) == ("VDDP:2", "VDDP", "VDDP:2", "VDDP:E")
    assert next(r for r in d.res if r["name"] == "Rm3")["n2"] == "VDDP:E"   # untouched
    assert {n for r in d2.net_resistors("VDDP") for n in (r["n1"], r["n2"])} == \
        {"VDDP", "VDDP:1", "VDDP:2", "MV0:s"}


def test_schur_ports_is_the_hand_conductance(ex):
    d = parse(ex.pll_dspf)
    Y = hier.schur_ports(Network(d, "VDDP"), ["VDDP", "VDDP:E"])
    assert np.allclose(Y, [[1 / 3, -1 / 3], [-1 / 3, 1 / 3]], rtol=1e-12, atol=1e-15)


def test_schur_ports_matches_dense_elimination(tmp_path):
    """A 3 x 3 mesh with three ports, random resistances: Y equals the dense
    Schur complement, its rows sum to zero, and it is symmetric."""
    rng = np.random.default_rng(7)
    L = ['*|DSPF 1.3', '*|DESIGN "mesh"', '*|NET N 0']
    nodes = {(i, j): f"N:{i}{j}" for i in range(3) for j in range(3)}
    ports = [nodes[(0, 0)], nodes[(2, 2)], nodes[(0, 2)]]
    for p in ports:
        L.append(f"*|P ({p} I 0 0 0)")
    k = 0
    edges = []
    for (i, j), n in nodes.items():
        for di, dj in ((1, 0), (0, 1)):
            m = nodes.get((i + di, j + dj))
            if m:
                r = round(float(rng.uniform(0.5, 3.0)), 6)
                L.append(f"R{k} {n} {m} {r:.6f} $metal1 $W=1")
                edges.append((n, m, r))
                k += 1
    d = parse(write(tmp_path / "mesh.dspf", "\n".join(L + [".ENDS"]) + "\n"))
    nw = Network(d, "N")
    Y = hier.schur_ports(nw, ports)
    G = np.zeros((9, 9))
    for n, m, r in edges:
        a, b = nw.idx[n], nw.idx[m]
        G[a, a] += 1 / r
        G[b, b] += 1 / r
        G[a, b] -= 1 / r
        G[b, a] -= 1 / r
    P = [nw.idx[p] for p in ports]
    inner = [i for i in range(9) if i not in P]
    dense = G[np.ix_(P, P)] - G[np.ix_(P, inner)] @ np.linalg.solve(
        G[np.ix_(inner, inner)], G[np.ix_(inner, P)])
    assert np.allclose(Y, dense, rtol=1e-12, atol=1e-14)
    assert np.allclose(Y.sum(axis=1), 0, atol=1e-12) and np.allclose(Y, Y.T)


RECS = [{"node": "A", "xy": (40.0, 0.0)}, {"node": "B", "xy": (70.0, 0.0)}]
PORTS = [{"name": "W", "x_um": 0.0, "y_um": 0.0}, {"name": "E", "x_um": 30.0, "y_um": 0.0}]


def test_match_ports_by_name_and_by_placed_coordinates():
    assert hier.match_ports("X", "V", PORTS[:1], RECS[:1]) == {"W": "A"}
    assert hier.match_ports("X", "V", PORTS, RECS, (40, 0, "R0")) == {"W": "A", "E": "B"}
    # mirrored: the west port lands on the east record
    assert hier.match_ports("X", "V", PORTS, RECS, (70, 0, "MY")) == {"W": "B", "E": "A"}
    # every orientation moves (30, 0) where its matrix says
    for o, (a, b, c, e) in hier.ORIENT.items():
        rec = [{"node": "A", "xy": (5.0, 5.0)}, {"node": "B", "xy": (5 + 30 * a, 5 + 30 * c)}]
        assert hier.match_ports("X", "V", PORTS, rec, (5, 5, o)) == {"W": "A", "E": "B"}, o


@pytest.mark.parametrize("ports,recs,place,code", [
    (PORTS, RECS, None, "EMIR-E806"),                      # no placement
    (PORTS[:1], RECS, None, "EMIR-E807"),                  # one port, two records
    (PORTS, RECS[:1], (40, 0, "R0"), "EMIR-E807"),         # two ports, one record
    (PORTS, RECS, (0, 0, "R0"), "EMIR-E808"),              # placed where no record is
    (PORTS, [{"node": "A", "xy": (40.0, 0.0)}, {"node": "B", "xy": (40.3, 0.0)},
             {"node": "C", "xy": (70.0, 0.0)}], (40, 0, "R0"), "EMIR-E808"),  # ambiguous
])
def test_match_ports_refuses_to_guess(ports, recs, place, code):
    with pytest.raises(EmirError) as e:
        hier.match_ports("X", "V", ports, recs, place)
    assert e.value.code == code


@pytest.mark.parametrize("kw,code", [
    ({"abstract": ["a.json"]}, "EMIR-E507"),                         # no @INST
    ({"abstract": ["a.json@X", "b.json@X"]}, "EMIR-E507"),           # one instance twice
    ({"abstract": ["a.json@X"], "place": ["X=1"]}, "EMIR-E507"),
    ({"abstract": ["a.json@X"], "place": ["X=1,2,R45"]}, "EMIR-E507"),
    ({"abstract": ["a.json@X"], "place": ["X=a,2"]}, "EMIR-E507"),
    ({"abstract": ["a.json@X"], "phase": ["X=1e-9"]}, "EMIR-E507"),  # needs sync
    ({"abstract": ["a.json@X"], "phase_sweep": ["X:0"], "align": "sync"}, "EMIR-E507"),
    ({"abstract": ["a.json@X"], "phase_sweep": ["X:100", "Y:100"], "align": "sync"},
     "EMIR-E507"),
    ({"abstract": ["a.json@X"], "budget_mv": -1.0}, "EMIR-E507"),
    ({"abstract": ["a.json@X"], "place": ["Y=1,2"]}, "EMIR-E802"),   # not an --abstract
])
def test_options_are_checked(kw, code):
    with pytest.raises(EmirError) as e:
        Options(types.SimpleNamespace(**kw))
    assert e.value.code == code


def test_options_parse():
    o = Options(types.SimpleNamespace(abstract=["d/a@b.json@XA"], place=["XA=1.5,-2,mxr90"],
                                      align="sync", phase=["XA=2e-10"],
                                      phase_sweep=["XA:8"], budget_mv=10.0))
    assert o.abstracts == {"XA": "d/a@b.json"}            # the last @ splits
    assert o.place == {"XA": (1.5, -2.0, "MXR90")} and o.phase == {"XA": 2e-10}
    assert o.sweep == {"XA": 8} and o.budget == 10.0


def _abstract_doc(ex, name="pll"):
    with open(getattr(ex, f"{name}_abstract"), encoding="utf-8") as f:
        return json.load(f)


@pytest.mark.parametrize("edit", [
    lambda d: d.update(schema="emir.run/1"),
    lambda d: d.update(nets={}),
    lambda d: d["nets"]["VDDP"]["ports"][0].update(wave=[0.0, 1.0]),
    lambda d: d["nets"]["VDDP"]["ports"][0].pop("x_um"),
    lambda d: d["nets"]["VDDP"].update(y_ports=[[1.0]]),
    lambda d: d["nets"]["VDDP"].update(dt_s=0),
])
def test_load_abstract_refuses_what_a_run_cannot_use(ex, tmp_path, edit):
    d = _abstract_doc(ex)
    edit(d)
    p = write(tmp_path / "bad.json", json.dumps(d))
    with pytest.raises(EmirError) as e:
        hier.load_abstract(p)
    assert e.value.code == "EMIR-E801"
    with pytest.raises(EmirError) as e:
        hier.load_abstract(write(tmp_path / "x.json", "{not json"))
    assert e.value.code == "EMIR-E801"
    with pytest.raises(EmirError) as e:
        hier.load_abstract(str(tmp_path / "missing.json"))
    assert e.value.code == "EMIR-E101"


def test_an_abstract_port_list_is_a_tap_file(ex, tmp_path):
    from emir.taps import load_taps
    ports = _abstract_doc(ex)["nets"]["VDDP"]["ports"]
    taps = load_taps(write(tmp_path / "taps.json", json.dumps(ports)))
    assert [t["name"] for t in taps] == ["VDDP", "VDDP:E"]
    assert taps[1]["xy"] == (30.0, 0.0)
    for t, p in zip(taps, ports):
        for m in ("i_avg", "i_rms", "i_peak"):
            assert abs(t[m] - p["uniform"][m]) <= 1e-12 * max(abs(p["uniform"][m]), 1e-30)


# ===================================================================== emir abstract


def test_abstract_documents_and_hand_numbers(ex, tmp_path):
    out = tmp_path / "pll.json"
    d, err = jmain("abstract", ex.pll_dspf, "--process", ex.P, "--mode", "cc-grid", "--cc-sim", ex.pll_raw,
                   "--period", "2.5e-9", "--points", "1000", "-o", out)
    assert d["schema"] == "emir.abstract_summary/1" and d["out"] == str(out)
    assert d["use"] == f"--abstract {out}@<INSTANCE>" and d["verdict"] == "WARN"
    n = d["nets"][0]
    assert [p["name"] for p in n["ports"]] == ["VDDP", "VDDP:E"]
    assert "wave" not in n["ports"][0]
    assert abs(n["ports"][0]["i_avg"] + 4e-3 / 3) < 1e-15
    assert abs(n["ports"][1]["i_avg"] + 2e-3 / 3) < 1e-15
    assert abs(n["internal"]["ir_drop_mV"] - 34 / 3) < 1e-9          # 2 mA x 5 + 4/3 mA x 1
    assert n["internal"]["worst"]["name"] == "Rm1"
    assert {x["code"] for x in d["notices"]} >= {"EMIR-W804"}
    with open(out, encoding="utf-8") as f:
        f_doc = validate(json.load(f))
    assert f_doc["schema"] == "emir.abstract/1"
    assert len(f_doc["nets"]["VDDP"]["ports"][0]["wave"]) == 1000
    assert f_doc["inputs"]["period"] == 2.5e-9 and f_doc["inputs"]["points"] == 1000
    assert "WARNING EMIR-W804" in err


def test_abstract_text_output(ex, tmp_path):
    p = emir("abstract", ex.adc_dspf, "--process", ex.P, "--mode", "cc-grid", "--cc-sim", ex.adc_raw,
             "--period", "10e-9", "--points", "4000", "-o", tmp_path / "a.json")
    assert p.returncode == 0, p.stderr
    assert "pin VDDA" in p.stdout and "-2.0000" in p.stdout
    assert p.stdout.rstrip().splitlines()[-1].startswith("abstract verdict: CLEAN")
    assert "Screening, not sign-off." in p.stdout


def _complex_raw(path):
    """A nutascii PSS fd result (harmonics): no time base."""
    L = ["Title: synthetic fd", "Date: synthetic", "Plotname: PSS fd", "Flags: complex",
         "No. Variables: 4", "No. Points: 3", "Variables:\t0\tfreq\tfrequency"]
    L += [f"\t{i}\tM{i - 1}:s\tI" for i in range(1, 4)]
    L.append("Values:")
    for j in range(3):
        L.append(f"{j}\t{j * 1e8:.6e},0")
        L += ["\t1.0e-3,0.0"] * 3
    return write(path, "\n".join(L) + "\n")


def test_abstract_refusals(ex, tmp_path):
    base = ["--process", ex.P, "--mode", "cc-grid", "--cc-sim", ex.adc_raw, "-o", tmp_path / "x.json"]
    noport = write(tmp_path / "noport.dspf", "".join(
        ln for ln in open(ex.adc_dspf, encoding="utf-8") if not ln.startswith("*|P")))
    d, _ = jmain("abstract", noport, *base, rc=2)
    assert d["code"] == "EMIR-E804"
    d, _ = jmain("abstract", noport, *base, "--net", "VDDA", rc=2)
    assert d["code"] == "EMIR-E803"
    assert d["next_steps"][0]["command"] == f"emir nets {noport} --format json"
    d, _ = jmain("abstract", ex.adc_dspf, *base, "--net", "VDDX", rc=2)
    assert d["code"] == "EMIR-E204"
    d, _ = jmain("abstract", ex.adc_dspf, "--process", ex.P, "--cc-sim",
                 _complex_raw(tmp_path / "fd.raw"), "-o", tmp_path / "x.json", rc=2)
    assert d["code"] == "EMIR-E520"                 # no --mode: the person's choice
    d, _ = jmain("abstract", ex.adc_dspf, "--process", ex.P, "--mode", "cc-grid",
                 "--cc-sim", _complex_raw(tmp_path / "fd.raw"), "-o", tmp_path / "x.json",
                 rc=2)
    assert d["code"] == "EMIR-E805"
    d, _ = jmain("abstract", ex.adc_dspf, *base, "--mode", "rcc", rc=2)
    assert d["code"] == "EMIR-E521"                 # an abstract is cc-grid only
    d, _ = jmain("abstract", ex.adc_dspf, *base, "--points", "4", rc=2)
    assert d["code"] == "EMIR-E507"
    d, _ = jmain("abstract", ex.adc_dspf, "--process", ex.P, "--mode", "cc-grid",
                 "--cc-sim", tmp_path / "none.raw", "-o", tmp_path / "x.json", rc=2)
    assert d["code"] == "EMIR-E101"
    d, _ = jmain("abstract", ex.adc_dspf, *base, "--vdd", "VDDA=x", rc=2)
    assert d["code"] == "EMIR-E507"
    assert not os.path.exists(tmp_path / "x.json")


# ===================================================================== the top level


def test_hierarchical_equals_flat_on_every_top_branch(ex):
    flat = run_lib(ex.flat_dspf, "--process", ex.P, "--net", "VDD", "--mode", "cc-grid", "--cc-sim", ex.flat_raw,
                   "--period", "10e-9", "--mode", "cc-grid")
    top = run_lib(*top_args(ex))
    f, h = by_name(flat), by_name(top)
    rt = sorted(n for n in h if n.startswith("Rt"))
    assert rt == [f"Rt{i}" for i in range(6)]
    for b in rt:
        for m in ("avg", "rms", "peak"):
            assert abs(h[b][m] - f[b][m]) <= 1e-9 * abs(f[b][m]), (b, m)
    # the Y stamp is solved but never checked: no layer
    assert "Y:XPLL:VDDP:VDDP:E" in h
    assert all(c["name"] != "Y:XPLL:VDDP:VDDP:E" for c in top.solved["VDD"]["clusters"])


def test_alignment_modes(ex):
    """avg does not depend on the alignment; inphase bounds sync on every
    branch; a phase sweep is the envelope of its phases; async warns."""
    sync = by_name(run_lib(*top_args(ex)))
    inph = by_name(run_lib(*top_args(ex, align="inphase")))
    asyn_res = run_lib(*top_args(ex, align="async"))
    asyn = by_name(asyn_res)
    for b in sync:
        assert abs(sync[b]["avg"] - inph[b]["avg"]) <= 1e-12 + 1e-9 * sync[b]["avg"]
        assert abs(sync[b]["avg"] - asyn[b]["avg"]) <= 1e-12 + 1e-9 * sync[b]["avg"]
        for m in ("rms", "peak"):
            assert inph[b][m] >= sync[b][m] * (1 - 1e-12), (b, m)
    assert "EMIR-W802" in {n["code"] for n in asyn_res.notices}
    ph = [by_name(run_lib(*top_args(ex, "--phase", f"XPLL={k * 0.625e-9!r}")))
          for k in range(4)]
    env = by_name(run_lib(*top_args(ex, "--phase-sweep", "XPLL:4")))
    for b in env:
        for m in ("rms", "peak"):
            assert abs(env[b][m] - max(p[b][m] for p in ph)) <= 1e-12 * max(1, env[b][m])


def test_run_document_carries_the_hierarchy(ex, tmp_path):
    pv = tmp_path / "pv.json"
    d, err = jmain("run", *top_args(ex, "--budget-mv", "15", "--port-voltages-out", pv,
                                    "--summary-out", tmp_path / "s.json"))
    assert d["verdict"] == "WARN" and d["current_source"]["kind"] == "abstracts"
    assert d["nets"][0]["mode"] == "cc-grid" and "hierarchical" in d["nets"][0]["mode_reason"]
    h = d["hierarchy"]
    pins = {f"{p['instance']}:{p['port']}": p for p in h["nets"][0]["pins"]}
    assert abs(pins["XADC:VDDA"]["V_avg_mV"] + 4.0) < 1e-9
    assert abs(pins["XPLL:VDDP"]["V_avg_mV"] + 92 / 27) < 1e-9            # -3.4074 mV
    assert abs(pins["XPLL:VDDP:E"]["V_avg_mV"] + 98 / 27) < 1e-9          # -3.6296 mV
    t = h["nets"][0]["pass_through"][0]
    assert t["between"] == ["VDDP", "VDDP:E"] and abs(t["R_ohm"] - 3.0) < 1e-12
    assert abs(t["I_avg_A"] - 2e-3 / 27) < 1e-15                          # 0.0741 mA
    rows = {r["instance"]: r for r in h["budget"]["rows"]}
    assert rows["XADC"]["exact"] and not rows["XPLL"]["exact"]
    assert abs(rows["XADC"]["total_mV"] - 11.5) < 1e-9
    assert h["budget"]["verdict"] == "PASS" and h["port_voltages"] == str(pv)
    assert [b["needs_back_annotation"] for b in h["blocks"]] == [False, True]
    steps = [s["command"] for s in d["next_steps"]]
    ann = [c for c in steps if c.startswith("emir abstract ")]
    assert len(ann) == 1 and f"--port-voltages {pv}@XPLL" in ann[0]
    assert "IR budget per block" in err
    with open(pv, encoding="utf-8") as f:
        pvd = validate(json.load(f))
    ent = pvd["instances"]["XPLL"]["nets"]["VDDP"]
    assert ent["exact"] and len(ent["ports"][0]["wave_V"]) == ent["t_common"]["n_samples"]
    assert abs(ent["v_delivered_V"] - (1.0 - 92 / 27e3)) < 1e-12          # least-drooped pin
    assert abs(ent["ports"][1]["dV_avg_V"] + 6 / 27e3) < 1e-15
    # the report relays it
    p = emir("report", "--from", tmp_path / "s.json")
    assert "## Hierarchy (blocks black-boxed)" in p.stdout and "IR budget (15 mV" in p.stdout
    assert "NEEDS back-annotation" in p.stdout


def test_the_next_step_back_annotation_runs_as_printed(ex, tmp_path):
    pv = tmp_path / "pv.json"
    d, _ = jmain("run", *top_args(ex, "--port-voltages-out", pv))
    cmd = next(s["command"] for s in d["next_steps"] if s["command"].startswith("emir abstract"))
    argv = shlex.split(cmd)[1:]
    out = argv[argv.index("-o") + 1]
    assert out.endswith(".annotated.json") and not os.path.exists(out)
    try:
        doc, _ = jmain(*argv)
        ba = doc["nets"][0]["back_annotation"]
        assert doc["verdict"] == "FAIL" and ba["verdict"] == "FAIL" and ba["exact"]
        assert abs(ba["max_ratio"]["all"] - (38 / 27) / 1.4) < 1e-9      # 1.4074 mA on 1.4 um
        assert abs(ba["ir_drop_mV"] - 400 / 27) < 1e-9                   # 14.8148 mV, = flat
        assert doc["nets"][0]["internal"]["verdict"] == "WARN"           # pins shorted
        assert "EMIR-W804" not in {n["code"] for n in doc["notices"]}
    finally:
        if os.path.exists(out):
            os.remove(out)


def test_back_annotation_from_an_inphase_run_bounds_the_exact_one(ex, tmp_path):
    def annotated(align):
        pv = tmp_path / f"pv_{align}.json"
        quiet_main(["run", *top_args(ex, "--port-voltages-out", pv, align=align)])
        d = parse(ex.pll_dspf)
        from emir import waves
        from emir.engine import Process
        entry, ctx = hier.characterise(d, "VDDP", waves.read(ex.pll_raw),
                                       Process.load(ex.P), period=2.5e-9, points=1000)
        det = {}
        ba = hier.back_annotate(ctx, entry, hier.load_port_voltages(pv), (str(pv), "XPLL"),
                                detail=det)
        return ba, det
    exact, de = annotated("sync")
    bound, db = annotated("inphase")
    assert exact["exact"] and not bound["exact"]
    assert np.allclose(db["Ie"]["avg"], de["Ie"]["avg"], rtol=1e-9, atol=1e-15)
    for m in ("rms", "peak"):
        assert np.all(db["Ie"][m] >= de["Ie"][m] * (1 - 1e-12)), m
    assert abs(bound["ir_drop_mV"] - exact["ir_drop_mV"]) < 1e-9          # avg droop: exact


def test_one_pin_back_annotation_adds_the_top_drop(ex, tmp_path):
    pv = tmp_path / "pv.json"
    quiet_main(["run", *top_args(ex, "--port-voltages-out", pv)])
    d, _ = jmain("abstract", ex.adc_dspf, "--process", ex.P, "--mode", "cc-grid", "--cc-sim", ex.adc_raw,
                 "--period", "10e-9", "--points", "4000", "--port-voltages", f"{pv}@XADC",
                 "-o", tmp_path / "adc.json")
    ba = d["nets"][0]["back_annotation"]
    assert ba["exact"] and ba["verdict"] == d["nets"][0]["internal"]["verdict"]
    assert abs(ba["ir_drop_mV"] - 11.5) < 1e-9                             # 4.0 + 7.5
    assert ba["max_ratio"]["all"] == pytest.approx(d["nets"][0]["internal"]["max_ratio"]["all"],
                                                   rel=1e-9)
    assert "EMIR-I804" in {n["code"] for n in d["notices"]}


def test_back_annotation_refuses_another_result(ex, tmp_path):
    pv = tmp_path / "pv.json"
    quiet_main(["run", *top_args(ex, "--port-voltages-out", pv)])
    base = [ex.pll_dspf, "--process", ex.P, "--mode", "cc-grid", "--cc-sim", ex.pll_raw, "--period", "2.5e-9",
            "-o", tmp_path / "x.json"]
    d, _ = jmain("abstract", *base, "--points", "500", "--port-voltages", f"{pv}@XPLL", rc=2)
    assert d["code"] == "EMIR-E812" and "another result" in d["message"]
    d, _ = jmain("abstract", *base, "--points", "1000", "--port-voltages", f"{pv}@XNOPE",
                 rc=2)
    assert d["code"] == "EMIR-E812"
    assert d["next_steps"][0]["command"] == "emir schema port_voltages --format json"


def test_budget_failure_exits_1_with_fail_on_violation(ex):
    d, err = jmain("run", *top_args(ex, "--budget-mv", "12", "--fail-on-violation"), rc=1)
    assert d["verdict"] == "WARN" and d["hierarchy"]["budget"]["verdict"] == "FAIL"
    assert {r["instance"]: r["verdict"] for r in d["hierarchy"]["budget"]["rows"]} == \
        {"XADC": "PASS", "XPLL": "FAIL"}
    assert "IR budget FAIL" in err


def test_large_droop_advises_the_self_consistency_loop(ex, tmp_path):
    ab = tmp_path / "adc.json"
    quiet_main(["abstract", ex.adc_dspf, "--process", ex.P, "--mode", "cc-grid", "--cc-sim", ex.adc_raw,
                "--period", "10e-9", "--points", "4000", "--vdd", "0.1", "-o", ab])
    d, _ = jmain("run", ex.top_dspf, "--process", ex.P, "--abstract", f"{ab}@XADC")
    codes = {n["code"] for n in d["notices"]}
    assert "EMIR-W805" in codes                    # 4 mV is 4 % of 0.1 V
    assert "EMIR-W807" in codes                    # XPLL's pins: no abstract, no current


@pytest.mark.parametrize("extra,code", [
    (["--currents", '{"VDD": 0.001}'], "EMIR-E811"),
    (["--mode", "rcc"], "EMIR-E811"),
    (["--scalars"], "EMIR-E811"),                  # with --align sync
])
def test_run_refuses_what_it_does_not_combine(ex, extra, code):
    d, _ = jmain("run", *top_args(ex, *extra), "--no-check", rc=2)
    assert d["code"] == code


def test_run_refusals_need_placement_and_name_the_records(ex):
    d, _ = jmain("run", *top_args(ex, place=False), rc=2)
    assert d["code"] == "EMIR-E501" and "EMIR-E806" in d["details"]["causes"]
    assert any(s["command"] == f"emir pins {ex.top_dspf} --net VDD --format json"
               for s in d["next_steps"])
    d, _ = jmain("run", *top_args(ex, place=False), "--no-check", rc=2)
    assert d["code"] == "EMIR-E806"
    assert d["next_steps"][0]["command"] == f"emir pins {ex.top_dspf} --net VDD --format json"


def test_incommensurate_periods_refuse_sync(ex, tmp_path):
    d = _abstract_doc(ex)
    d["nets"]["VDDP"]["dt_s"] *= 1.0001
    d["nets"]["VDDP"]["period_s"] = d["nets"]["VDDP"]["dt_s"] * 1000
    bad = write(tmp_path / "pll.json", json.dumps(d))
    args = [ex.top_dspf, "--process", ex.P, "--abstract", f"{ex.adc_abstract}@XADC",
            "--abstract", f"{bad}@XPLL", "--place", "XPLL=40,0,R0"]
    doc, _ = jmain("check", *args, "--align", "sync", rc=2)
    assert "EMIR-E810" in [f["code"] for f in doc["findings"]]
    doc, _ = jmain("run", *args, "--align", "sync", "--no-check", rc=2)
    assert doc["code"] == "EMIR-E810"
    assert doc["next_steps"][0]["command"].endswith("--align inphase")
    jmain("run", *args, "--align", "inphase")      # the suggested run works


TOP_EXTRA = """*|NET VSS 0
*|S (VSS:1 0.0000 -50.0000)
*|I (XQ:VSS XQ VSS I 0 10.0000 -50.0000)
*|I (XADC:VSSA XADC VSSA I 0 0.0000 -50.0000)
Rv1 VSS:1 XQ:VSS 1.000000 $metal2 $L=10 $W=2.000000 $LLX=0 $LLY=-51 $URX=10 $URY=-49 $lvl=2
Rv2 VSS:1 XADC:VSSA 1.000000 $metal2 $L=1 $W=2.000000 $LLX=0 $LLY=-51 $URX=1 $URY=-49 $lvl=2
"""


def test_nets_without_block_pins_and_pins_without_abstracts(ex, tmp_path):
    text = open(ex.top_dspf, encoding="utf-8").read()
    top = write(tmp_path / "top.dspf", text.replace(
        '*|I (XPLL:VDDP#2', '*|I (XADC:VDDX XADC VDDX I 0 0.0000 40.0000)\n*|I (XPLL:VDDP#2')
        .replace(".ENDS", TOP_EXTRA + ".ENDS"))
    base = [top, "--process", ex.P, "--abstract", f"{ex.adc_abstract}@XADC",
            "--abstract", f"{ex.pll_abstract}@XPLL", "--place", "XPLL=40,0,R0"]
    d, _ = jmain("run", *base)                     # --net all: only VDD has block pins
    assert [n["net"] for n in d["nets"]] == ["VDD"]
    assert "EMIR-W806" in {n["code"] for n in d["notices"]}     # XADC:VDDX not abstracted
    d, _ = jmain("run", *base, "--net", "VSS", "--no-check", rc=2)
    assert d["code"] == "EMIR-E813"                # no port on VSS (checked before the pins)
    d, _ = jmain("run", *base, "--net", "VSS", "--feed", "VSS:1", "--no-check", rc=2)
    assert d["code"] == "EMIR-E809"
    d, _ = jmain("run", top, "--process", ex.P, "--abstract", f"{ex.adc_abstract}@XNOPE",
                 "--no-check", rc=2)
    assert d["code"] == "EMIR-E802"


def test_several_pads_are_held_at_one_potential(ex, tmp_path):
    text = open(ex.top_dspf, encoding="utf-8").read().replace(
        "*|P (VDD I 0 -50.0000 0.0000)",
        "*|P (VDD I 0 -50.0000 0.0000)\n*|P (VDD:PAD2 I 0 70.0000 -40.0000)").replace(
        ".ENDS", "Rp2 VDD:T3 VDD:PAD2 0.250000 $metal2 $L=20 $W=4.000000 $LLX=68 $LLY=-40 "
                 "$URX=72 $URY=-20 $lvl=2\n.ENDS")
    top = write(tmp_path / "top2.dspf", text)
    res = run_lib(top, "--process", ex.P, "--abstract", f"{ex.adc_abstract}@XADC",
                  "--abstract", f"{ex.pll_abstract}@XPLL", "--place", "XPLL=40,0,R0")
    assert "EMIR-I801" in {n["code"] for n in res.notices}
    b = by_name(res)
    # the two pads feed 4 mA between them: KCL at the merged feed
    assert abs(b["Rt0"]["avg"] + b["Rp2"]["avg"] - 4e-3) < 1e-12
    assert b["Rp2"]["avg"] > 0


def test_html_and_viewer_with_abstracts(ex, tmp_path):
    d, _ = jmain("run", *top_args(ex, "--html", tmp_path / "html", "--viewer",
                                  tmp_path / "viewer"))
    assert os.path.isfile(tmp_path / "html" / "index.html")
    assert d["viewer"] and all(os.path.exists(p) for p in d["viewer"].values())


# ===================================================================== check, next, E417


def test_check_with_abstracts(ex):
    d, _ = jmain("check", *top_args(ex))
    assert d["passed"] and d["next_steps"][0]["command"].startswith("emir run ")
    assert "--abstract" in d["next_steps"][0]["command"]
    assert "EMIR-I805" in {f["code"] for f in d["findings"]}
    d, _ = jmain("check", *top_args(ex, place=False), rc=2)
    assert [f["code"] for f in d["findings"] if f["level"] == "FAIL"] == ["EMIR-E806"]


def test_next_plans_the_hierarchical_route(ex):
    from emir import plan as P
    f = P.Facts(dspf=ex.top_dspf, process=ex.P, abstracts=[f"{ex.adc_abstract}@XADC",
                                                            f"{ex.pll_abstract}@XPLL"],
                place=["XPLL=40,0,R0"], align="sync")
    plan = P.build(f)
    ids = [s["id"] for s in plan.steps]
    assert ids[-4:] == ["check", "run", "report", "annotate:XPLL"]
    assert plan.mode["chosen"] == "cc-grid" and not plan.blocked
    assert all(s["status"] == "todo" for s in plan.steps)
    run = next(s for s in plan.steps if s["id"] == "run")["command"]
    assert "--port-voltages-out emir_out/port_voltages.json" in run
    # without the placement: blocked, and the records are shown
    plan = P.build(P.Facts(dspf=ex.top_dspf, process=ex.P,
                           abstracts=[f"{ex.adc_abstract}@XADC", f"{ex.pll_abstract}@XPLL"]))
    assert plan.blocked and "--place XPLL" in plan.missing[0]["what"]
    assert [s["id"] for s in plan.steps][-1] == "pins:VDD"
    # no abstracts, but a black-boxed block: the route is advised
    plan = P.build(P.Facts(dspf=ex.top_dspf, process=ex.P))
    assert any("emir abstract BLOCK.dspf" in a for a in plan.advice)


def test_next_cli_takes_abstracts(ex):
    d, _ = jmain("next", "--dspf", ex.top_dspf, "--process", ex.P, "--abstract",
                 f"{ex.adc_abstract}@XADC", "--abstract", f"{ex.pll_abstract}@XPLL",
                 "--place", "XPLL=40,0,R0")
    assert d["mode"]["chosen"] == "cc-grid"
    assert d["inputs"]["abstracts"] == [f"{ex.adc_abstract}@XADC", f"{ex.pll_abstract}@XPLL"]


def test_split_pin_refusal_points_to_the_hierarchical_flow(ex, tmp_path):
    """A top CC simulation that saved the black-box terminal current once:
    EMIR-E417, whose next steps run as printed and lead to this flow."""
    from emir.validate import _psf_tran
    t = np.linspace(0, 4e-9, 401)
    res = _psf_tran(str(tmp_path / "top.tran"), t, {"XADC:VDDA": 2e-3 * np.ones_like(t),
                                                    "XPLL:VDDP": 2e-3 * np.ones_like(t)})
    d, _ = jmain("run", ex.top_dspf, "--process", ex.P, "--net", "VDD", "--mode", "cc-grid", "--cc-sim", res,
                 "--mode", "cc-grid", "--no-check", rc=2)
    assert d["code"] == "EMIR-E417" and "emir abstract" in d["message"]
    cmds = [s["command"] for s in d["next_steps"]]
    # JSON mode: every emir next step keeps --format json
    assert cmds == [f"emir pins {ex.top_dspf} --net VDD --format json",
                    f"emir next --dspf {ex.top_dspf} --net VDD --format json",
                    "emir abstract --help --format json"]
    for c in cmds:
        p = emir(*shlex.split(c)[1:])
        assert p.returncode == 0, (c, p.stderr)
    d, _ = jmain("check", ex.top_dspf, "--process", ex.P, "--net", "VDD", "--mode", "cc-grid", "--cc-sim", res,
                 "--mode", "cc-grid", rc=2)
    f = next(x for x in d["findings"] if x["code"] == "EMIR-E417")
    assert f["command"] == f"emir next --dspf {ex.top_dspf} --net VDD --process {ex.P}"


def test_cli_lists_abstract():
    assert "abstract" in cli.COMMANDS
    p = emir("abstract", "--help")
    assert p.returncode == 0 and "--port-voltages" in p.stdout
    p = emir("--help")
    assert "emir abstract <block.dspf>" in p.stdout
    p = emir("run", "--help")
    assert "--abstract FILE@INST" in p.stdout and "--phase-sweep" in p.stdout


def test_the_abstract_records_its_inputs_for_the_rebuild(ex):
    from emir.hier_cli import abstract_command
    d = _abstract_doc(ex)
    cmd = abstract_command(d, ["-o", "x.json"])
    assert cmd == ("emir abstract " + shlex.join([
        ex.pll_dspf, "--process", ex.P, "--cc-sim", ex.pll_raw, "--mode", "cc-grid",
        "--period", "2.5e-09",
        "--points", "1000", "--vdd", "1.0", "-o", "x.json"]))
    d2 = copy.deepcopy(d)
    d2["inputs"]["cc_sim"] = "/nonexistent.raw"
    assert abstract_command(d2) is None


# ===================================================================== top devices, recursion


def _inner(ln):
    return ln[ln.find("(") + 1:ln.rfind(")")].split()


def _flatten(lines, prefix, port_map, dx, dy, merge_insts=()):
    """A block's DSPF lines renamed into a flat DSPF: its ports become the
    nodes `port_map` names, the pin records of `merge_insts` (sub-blocks
    flattened separately) plain nodes, everything else prefixed."""
    ports = {_inner(ln)[0] for ln in lines if ln.startswith("*|P")}

    def ren(n):
        return port_map[n] if n in ports else f"{prefix}{n}"
    out = []
    for ln in lines:
        if ln.startswith("*|S"):
            f = _inner(ln)
            out.append(f"*|S ({ren(f[0])} {float(f[1]) + dx:.4f} {float(f[2]) + dy:.4f})")
        elif ln.startswith("*|I"):
            f = _inner(ln)
            x, y = float(f[-2]) + dx, float(f[-1]) + dy
            if f[1] in merge_insts:
                out.append(f"*|S ({ren(f[0])} {x:.4f} {y:.4f})")
            else:
                out.append(f"*|I ({ren(f[0])} {prefix}{f[1]} {f[2]} {f[3]} {f[4]} "
                           f"{x:.4f} {y:.4f})")
        elif ln[:1] == "R":
            f = ln.split()
            out.append(f"R{prefix.replace('/', '_')}{f[0][1:]} {ren(f[1])} {ren(f[2])} "
                       + " ".join(f[3:]))
    return out


HEAD2 = ['*|DSPF 1.3', '*|OPERATING_TEMPERATURE 100', '*|GROUND_NET 0']


def _mid_lines():
    R = MI.R
    return ['*|DESIGN "mid"', '*|NET VDDM 0', '*|P (VDDM I 0 0.0000 0.0000)',
            '*|P (VDDM:E I 0 60.0000 -10.0000)',
            '*|S (VDDM:1 10.0000 0.0000)', '*|S (VDDM:2 10.0000 -10.0000)',
            '*|S (VDDM:3 50.0000 -10.0000)', '*|I (MD0:s MD0 s I 0 10.0000 0.0000)',
            '*|I (XP:VDDP XP VDDP I 0 20.0000 0.0000)',
            '*|I (XP:VDDP#2 XP VDDP I 0 50.0000 0.0000)',
            R("Rq1", "VDDM", "VDDM:1", 1.0, "metal2", 0, 0, 10, 0, w=2.0, lvl=2),
            R("Rq2", "VDDM:1", "XP:VDDP", 1.0, "metal2", 10, 0, 20, 0, w=2.0, lvl=2),
            R("Rq3", "VDDM:1", "VDDM:2", 0.5, "metal2", 10, 0, 10, -10, w=2.0, lvl=2),
            R("Rq4", "VDDM:2", "VDDM:3", 2.0, "metal2", 10, -10, 50, -10, w=2.0, lvl=2),
            R("Rq5", "VDDM:3", "XP:VDDP#2", 0.5, "metal2", 50, -10, 50, 0, w=2.0, lvl=2),
            R("Rq6", "VDDM:3", "VDDM:E", 0.5, "metal2", 50, -10, 60, -10, w=2.0, lvl=2),
            R("Rd", "VDDM:1", "MD0:s", 5.0, "cont", 9.9, -.1, 10.1, .1, cuts=2, lvl=0)]


def _top2_lines():
    R = MI.R
    return ['*|NET VDD 0', '*|P (VDD I 0 -20.0000 0.0000)',
            '*|S (VDD:A -20.0000 -30.0000)', '*|S (VDD:B 60.0000 -30.0000)',
            '*|I (XM:VDDM XM VDDM I 0 0.0000 0.0000)',
            '*|I (XM:VDDM#2 XM VDDM I 0 60.0000 -10.0000)',
            R("Rz0", "VDD", "XM:VDDM", 1.0, "metal2", -20, 0, 0, 0, w=3.0, lvl=2),
            R("Rz1", "VDD", "VDD:A", 0.5, "metal2", -20, 0, -20, -30, w=3.0, lvl=2),
            R("Rz2", "VDD:A", "VDD:B", 2.0, "metal2", -20, -30, 60, -30, w=3.0, lvl=2),
            R("Rz3", "VDD:B", "XM:VDDM#2", 0.5, "metal2", 60, -30, 60, -10, w=3.0, lvl=2)]


def _md0(t):
    return MI.pulse(t, 1e-3, 0.5, 5e-9, delay=0.3e-9) * np.where(t < 5e-9, 1.4, 1.0)


@pytest.fixture(scope="module")
def rec(ex, tmp_path_factory):
    """A two-level chip: top2 > mid (two pins, device MD0) > pll (XP, two pins)."""
    d = str(tmp_path_factory.mktemp("rec"))
    mid = _mid_lines()
    pll = [ln for ln in MI.pll_dspf().splitlines() if not ln.startswith(("*|DSPF", "*|DESIGN",
                                                                         "*|OPER", "*|GROUND",
                                                                         ".ENDS"))]
    p = {"mid": write(os.path.join(d, "mid.dspf"), "\n".join(HEAD2 + mid + [".ENDS"]) + "\n"),
         "top": write(os.path.join(d, "top2.dspf"),
                      "\n".join(HEAD2 + ['*|DESIGN "top2"'] + _top2_lines() + [".ENDS"]) + "\n")}
    flat = (HEAD2 + ['*|DESIGN "flat2"']
            + [ln for ln in _top2_lines() if not ln.startswith("*|I")]
            + ['*|S (XM:VDDM 0.0000 0.0000)', '*|S (XM:VDDM#2 60.0000 -10.0000)']
            + _flatten(mid, "XM/", {"VDDM": "XM:VDDM", "VDDM:E": "XM:VDDM#2"}, 0, 0,
                       merge_insts=("XP",))
            + _flatten(pll, "XM/XP/", {"VDDP": "XM/XP:VDDP", "VDDP:E": "XM/XP:VDDP#2"}, 20, 0)
            + [".ENDS"])
    p["flat"] = write(os.path.join(d, "flat2.dspf"), "\n".join(flat) + "\n")
    t = np.arange(0, 20e-9 + MI.DT / 2, MI.DT)
    p["mid_raw"] = os.path.join(d, "mid.raw")
    MI.write_nutascii(p["mid_raw"], t, {"MD0:s": _md0(t)})
    p["flat_raw"] = os.path.join(d, "flat2.raw")
    MI.write_nutascii(p["flat_raw"], t, {"XM/MD0:s": _md0(t),
                                         "XM/XP/MV0:s": MI.pll_currents(t)["MV0:s"]})
    p["mid_abs"] = os.path.join(d, "mid.abstract.json")
    rc, o, e = quiet_main(["abstract", p["mid"], "--process", ex.P, "--mode", "cc-grid", "--cc-sim", p["mid_raw"],
                           "--period", "5e-9", "--abstract", f"{ex.pll_abstract}@XP",
                           "--place", "XP=20,0,R0", "-o", p["mid_abs"]])
    assert rc == 0, o + e
    p["dir"] = d
    return types.SimpleNamespace(**p)


def test_a_mid_level_abstract_composes_its_sub_blocks(ex, rec):
    """top2 with the mid block black-boxed, the mid block abstracted with the
    PLL black-boxed inside it: every top branch equals the three-level flat
    run, and so does every branch of the mid block once back-annotated."""
    with open(rec.mid_abs, encoding="utf-8") as f:
        ab = validate(json.load(f))
    nb = ab["nets"]["VDDM"]
    assert nb["sub_blocks"][0]["instance"] == "XP" and nb["period_s"] == pytest.approx(5e-9)
    assert nb["n_samples"] == 2000 and len(nb["ports"]) == 2
    assert ab["inputs"]["abstracts"] == [f"{ex.pll_abstract}@XP"]
    flat = run_lib(rec.flat, "--process", ex.P, "--net", "VDD", "--mode", "cc-grid", "--cc-sim", rec.flat_raw,
                   "--period", "5e-9", "--mode", "cc-grid")
    pv = os.path.join(rec.dir, "pv.json")
    top = run_lib(rec.top, "--process", ex.P, "--net", "VDD", "--abstract",
                  f"{rec.mid_abs}@XM", "--place", "XM=0,0,R0", "--align", "sync",
                  "--port-voltages-out", pv)
    f, h = by_name(flat), by_name(top)
    for b in ("Rz0", "Rz1", "Rz2", "Rz3"):
        for m in ("avg", "rms", "peak"):
            assert abs(h[b][m] - f[b][m]) <= 1e-9 * abs(f[b][m]), (b, m)
    # the mid block, back-annotated with top2's pin voltages: every branch
    from emir import waves
    from emir.engine import Process
    from emir.hier_cli import Options as O
    sub = hier.load_abstract(ex.pll_abstract)
    subs = {"insts": {"XP": sub},
            "opts": O(types.SimpleNamespace(abstract=[f"{ex.pll_abstract}@XP"],
                                            place=["XP=20,0,R0"], align="sync"))}
    d = parse(rec.mid)
    entry, ctx = hier.characterise(d, "VDDM", waves.read(rec.mid_raw), Process.load(ex.P),
                                   period=5e-9, subs=subs)
    det = {}
    ba = hier.back_annotate(ctx, entry, hier.load_port_voltages(pv), (pv, "XM"), detail=det)
    assert ba["exact"]
    for i, nm in enumerate(det["names"]):
        if nm.startswith("Y:"):
            continue
        fn = f"RXM_{nm[1:]}"
        for m in ("avg", "rms", "peak"):
            assert abs(det["Ie"][m][i] - f[fn][m]) <= 1e-9 * max(abs(f[fn][m]), 1e-12), (nm, m)


def test_the_tops_own_devices_join_the_abstracts(ex, tmp_path):
    """A device on the top net (MT0, behind a contact at T2) from the top's
    CC simulation, next to the two abstracts: equal to the flat run."""
    ct = "Rct VDD:T2 MT0:s 5.000000 $cont $VC=2 $LLX=-0.1 $LLY=-20.1 $URX=0.1 $URY=-19.9 $lvl=0"
    rec_ = "*|I (MT0:s MT0 s I 0 0.0000 -20.0000)"
    text = open(ex.top_dspf, encoding="utf-8").read()
    top = write(tmp_path / "top.dspf", text.replace(".ENDS", f"{rec_}\n{ct}\n.ENDS"))
    ftext = open(ex.flat_dspf, encoding="utf-8").read()
    flat = write(tmp_path / "flat.dspf", ftext.replace(".ENDS", f"{rec_}\n{ct}\n.ENDS"))
    t = np.arange(0, MI.STOP + MI.DT / 2, MI.DT)
    mt0 = MI.pulse(t, 1e-3, 0.5, 5e-9, delay=0.7e-9) * np.where(t < 5e-9, 1.4, 1.0)
    traw = str(tmp_path / "top.raw")
    MI.write_nutascii(traw, t, {"MT0:s": mt0})
    a, p = MI.adc_currents(t), MI.pll_currents(t)
    sigs = {f"XADC/{k}": v for k, v in a.items()} | {f"XPLL/{k}": v for k, v in p.items()}
    fraw = str(tmp_path / "flat.raw")
    MI.write_nutascii(fraw, t, sigs | {"MT0:s": mt0})
    fl = by_name(run_lib(flat, "--process", ex.P, "--net", "VDD", "--mode", "cc-grid", "--cc-sim", fraw,
                         "--period", "10e-9", "--mode", "cc-grid"))
    args = [top, "--process", ex.P, "--net", "VDD", "--abstract", f"{ex.adc_abstract}@XADC",
            "--abstract", f"{ex.pll_abstract}@XPLL", "--place", "XPLL=40,0,R0",
            "--mode", "cc-grid", "--cc-sim", traw, "--period", "5e-9", "--align", "sync"]
    res = run_lib(*args)
    h = by_name(res)
    for b in [f"Rt{i}" for i in range(6)] + ["Rct"]:
        for m in ("avg", "rms", "peak"):
            assert abs(h[b][m] - fl[b][m]) <= 1e-9 * abs(fl[b][m]), (b, m)
    assert "top-level instance pin" in res.solved["VDD"]["mode_reason"]
    assert "EMIR-W807" not in {n["code"] for n in res.notices}
    d, _ = jmain("check", *args)
    assert d["passed"], [f["message"] for f in d["findings"] if f["level"] == "FAIL"]
    d, _ = jmain("run", *args)
    assert d["current_source"]["path"] == traw
    assert [p["instance"] for p in d["hierarchy"]["nets"][0]["pins"]] == \
        ["XADC", "XPLL", "XPLL"]                   # the device is not a block pin


def test_next_with_abstracts_and_the_tops_simulation(ex, tmp_path):
    from emir import plan as P
    abstracts = [f"{ex.adc_abstract}@XADC", f"{ex.pll_abstract}@XPLL"]
    raw = write(tmp_path / "top.raw", "")
    plan = P.build(P.Facts(dspf=ex.top_dspf, process=ex.P, abstracts=abstracts,
                           place=["XPLL=40,0,R0"], cc_sim=raw))
    run = next(s for s in plan.steps if s["id"] == "run")["command"]
    assert f"--cc-sim {raw}" in run
    plan = P.build(P.Facts(dspf=ex.top_dspf, process=ex.P, abstracts=abstracts,
                           place=["XPLL=40,0,R0"], currents='{"VDD": 1}'))
    assert plan.blocked and "EMIR-E811" in plan.missing[0]["why"]


def test_a_transfer_matrix_too_big_is_refused_with_a_runnable_step(ex, monkeypatch):
    real = hier._transfer

    def small(*a, **k):
        return real(*a, **{**k, "max_bytes": 64})
    monkeypatch.setattr(hier, "_transfer", small)
    d, _ = jmain("run", *top_args(ex, align="async"), "--no-check", rc=2)
    assert d["code"] == "EMIR-E418"
    cmd = d["next_steps"][0]["command"]
    assert cmd.endswith("--align inphase")
    monkeypatch.setattr(hier, "_transfer", real)
    jmain("run", *shlex.split(cmd)[2:])           # runs as printed


def test_a_supply_and_a_ground_net_in_one_run(ex, tmp_path):
    """The ADC with a ground rail too: its devices return their current into
    VSSA. --net all screens VDD and VSS; the ground pin bounces up, the
    supply pin droops, and the budget has a row for each."""
    R = MI.R
    adc = open(ex.adc_dspf, encoding="utf-8").read().replace(".ENDS", "\n".join(
        ['*|NET VSSA 0', '*|P (VSSA I 0 0.0000 -5.0000)']
        + [f"*|S (VSSA:{i} {10 * i:.4f} -5.0000)" for i in range(1, 5)]
        + [f"*|I (M{k}:d M{k} d I 0 {10 * (k + 1):.4f} -5.0000)" for k in range(4)]
        + [R(f"Rg{i + 1}", "VSSA" if i == 0 else f"VSSA:{i}", f"VSSA:{i + 1}", 1.0, "metal1",
             10 * i, -5, 10 * (i + 1), -5, w=3.0) for i in range(4)]
        + [R(f"Rd{k}", f"VSSA:{k + 1}", f"M{k}:d", 5.0, "cont", 10 * (k + 1) - .1, -5.1,
             10 * (k + 1) + .1, -4.9, cuts=2, lvl=0) for k in range(4)]) + "\n.ENDS")
    adc = write(tmp_path / "adc2.dspf", adc)
    t = np.arange(0, MI.STOP + MI.DT / 2, MI.DT)
    cur = MI.adc_currents(t)
    sigs = cur | {f"M{k}:d": -cur[f"M{k}:s"] for k in range(4)}      # out of the instance
    raw = str(tmp_path / "adc2.raw")
    MI.write_nutascii(raw, t, sigs)
    ab = str(tmp_path / "adc2.json")
    d, _ = jmain("abstract", adc, "--process", ex.P, "--mode", "cc-grid", "--cc-sim", raw, "--period", "10e-9",
                 "--points", "4000", "--vdd", "VDDA=1.0", "--vdd", "VSSA=0", "-o", ab)
    pins = {n["net"]: n["ports"][0]["i_avg"] for n in d["nets"]}
    assert pins == pytest.approx({"VDDA": -2e-3, "VSSA": 2e-3}, rel=1e-9)
    top = write(tmp_path / "top.dspf", open(ex.top_dspf, encoding="utf-8").read().replace(
        ".ENDS", "*|NET VSS 0\n*|P (VSS I 0 -50.0000 -10.0000)\n"
        "*|I (XADC:VSSA XADC VSSA I 0 0.0000 35.0000)\n"
        + R("Rs0", "VSS", "XADC:VSSA", 2.0, "metal2", -50, -10, 0, 35, w=4.0, lvl=2)
        + "\n.ENDS"))
    doc, _ = jmain("run", top, "--process", ex.P, "--abstract", f"{ab}@XADC", "--abstract",
                   f"{ex.pll_abstract}@XPLL", "--place", "XPLL=40,0,R0", "--budget-mv", "20")
    assert [n["net"] for n in doc["nets"]] == ["VDD", "VSS"]
    v = {(p["instance"], p["block_net"]): p["V_avg_mV"]
         for n in doc["hierarchy"]["nets"] for p in n["pins"]}
    assert v[("XADC", "VSSA")] == pytest.approx(4.0, rel=1e-9)        # 2 ohm x 2 mA, up
    assert v[("XADC", "VDDA")] == pytest.approx(-4.0, rel=1e-9)
    rows = {(r["instance"], r["block_net"]): r for r in doc["hierarchy"]["budget"]["rows"]}
    assert rows[("XADC", "VSSA")]["total_mV"] == pytest.approx(4.0 + 7.5, rel=1e-9)


def test_decap_lump_and_internal_dynamic_droop(ex, tmp_path):
    """1 pF at VDDA:2 (2 ohm from the pin) and 1 pF at VDDA:4 (4 ohm, the
    first 2 ohm shared): C = 2 pF, R = c'G^-1 c / C^2 = (2 + 2*2 + 4) / 4 =
    2.5 ohm. With capacitance the abstract has an internal dynamic droop."""
    text = open(ex.adc_dspf, encoding="utf-8").read().replace(
        ".ENDS", "Cd1 VDDA:2 0 1e-12\nCd2 VDDA:4 0 1e-12\n.ENDS")
    dsp = write(tmp_path / "adc_c.dspf", text)
    d, _ = jmain("abstract", dsp, "--process", ex.P, "--mode", "cc-grid", "--cc-sim", ex.adc_raw, "--period",
                 "10e-9", "--points", "4000", "-o", tmp_path / "a.json")
    n = d["nets"][0]
    assert n["decap"]["c_F"] == pytest.approx(2e-12, rel=1e-12)
    assert n["decap"]["r_ohm"] == pytest.approx(2.5, rel=1e-9)
    dyn = n["internal"]["dynamic"]
    assert dyn is not None and 0 < dyn["worst_mV"] < 20 and dyn["node"]
    # the pin currents do not depend on the capacitance (a static split)
    with open(ex.adc_abstract, encoding="utf-8") as f:
        plain = json.load(f)["nets"]["VDDA"]["ports"][0]
    assert n["ports"][0]["i_avg"] == pytest.approx(plain["i_avg"], rel=1e-12)


# ------------------------------------------------------------ the hierarchical run's size and path


def test_next_flags_a_big_hierarchical_run_long_as_the_flat_one(ex, monkeypatch):
    """the top-level grid is solved at full size: above the big-block size
    the hierarchical run is a long run (the person's go-ahead), as the flat
    run of the same grid is."""
    from emir import plan as P
    monkeypatch.setitem(P.THRESHOLDS, "big_block_nodes", 5)          # the top has 7 nodes
    kw = dict(dspf=ex.top_dspf, process=ex.P, abstracts=[f"{ex.adc_abstract}@XADC",
                                                         f"{ex.pll_abstract}@XPLL"],
              place=["XPLL=40,0,R0"], align="sync")
    run = next(s for s in P.build(P.Facts(**kw)).steps if s["id"] == "run")
    assert run["long_run"] and run["needs_human"] and "a long run" in run["why"]
    flat = next(s for s in P.build(P.Facts(dspf=ex.flat_dspf, process=ex.P,
                                           cc_sim=ex.flat_raw, mode="cc-grid")).steps
                if s["id"] == "run")
    assert flat["long_run"] == run["long_run"]
    monkeypatch.setitem(P.THRESHOLDS, "big_block_nodes", 50_000)
    run = next(s for s in P.build(P.Facts(**kw)).steps if s["id"] == "run")
    assert not run["long_run"] and not run["needs_human"]


def test_next_plans_the_missing_abstracts_and_the_way_back(ex, tmp_path):
    """an abstract that is not written yet gets its `emir abstract` step
    (waiting on the block's files), then `emir next` again; the abstract's
    summary leads back to the top run (`--top`)."""
    from emir import plan as P
    new = str(tmp_path / "adc2.abstract.json")
    plan = P.build(P.Facts(dspf=ex.top_dspf, process=ex.P,
                           abstracts=[f"{new}@XADC", f"{ex.pll_abstract}@XPLL"],
                           place=["XPLL=40,0,R0"], align="sync", json=True))
    by = {s["id"]: s for s in plan.steps}
    ab = by["abstract:XADC"]
    assert ab["status"] == "waiting" and ab["placeholders"] == ["<XADC.dspf>", "<XADC.raw>"]
    assert f"-o {new}" in ab["command"] and f"--top {ex.top_dspf}@XADC" in ab["command"]
    again = by["next"]
    assert again["status"] == "todo" and again["after"] == ["abstract:XADC"]
    assert plan.blocked and "run" not in by
    # the step, with its placeholders filled, writes the abstract; its summary's
    # next step is the plan of the top with it, which runs as printed
    cmd = shlex.split(ab["command"].replace("<XADC.dspf>", ex.adc_dspf)
                      .replace("<XADC.raw>", ex.adc_raw))
    d, _ = jmain(*cmd[1:-2], "--period", "10e-9", "--points", "1000")
    assert os.path.isfile(new)
    (back,) = [s for s in d["next_steps"] if s["command"].startswith("emir next ")]
    argv = shlex.split(back["command"])
    assert argv[argv.index("--abstract") + 1] == f"{new}@XADC"
    nxt, _ = jmain(*argv[1:-2])
    assert nxt["schema"] == "emir.next/1"
    # and the re-plan step, run as printed, now plans the chip-level run
    again_doc, _ = jmain(*shlex.split(again["command"])[1:-2])
    assert {"check", "run", "report"} <= {s["id"] for s in again_doc["steps"]}


def test_the_summary_says_which_superposition_path_ran(ex):
    """inphase superposes per-tap scalars (bounds), sync without a sweep
    the exact waveforms, async and a phase sweep combine per-block rows; the
    summary's `superposition` names each (it was null for all but sync)."""
    got = {}
    for align, extra in (("inphase", ()), ("sync", ()), ("async", ()),
                         ("sync", ("--phase-sweep", "XPLL:2"))):
        d, _ = jmain("run", *top_args(ex, *extra, align=align))
        got[(align, bool(extra))] = d["nets"][0]["superposition"]
    assert got[("inphase", False)]["method"] == "scalar"
    assert got[("inphase", False)]["reason"].startswith("--align inphase: ")
    assert got[("sync", False)]["method"] in ("dense", "stream")
    assert got[("async", False)]["method"] == "combined"
    assert got[("sync", True)]["method"] == "combined"
