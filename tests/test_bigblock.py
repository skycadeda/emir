"""Big blocks (`emir/bigblock.py`, `emir/plan_bigblock.py`): the streamed
superposition equals the dense one, cc-grid dynamic IR matches the closed
forms of the validation suite, the new options are checked and coded, the
run summary carries both, and `emir next` gives the CC + R-only recipe.

Every input is synthetic and every number invented; no simulator is needed.
"""
import json
import os

import jsonschema
import numpy as np
import pytest
import yaml
from _helpers import CUR, DSPF, PERIOD, PROC, cc_result, emir, rcc_result, write

from emir import bigblock, schemas
from emir import plan as P
from emir.dspf import Network, analyse, build_parser, check_net, parse
from emir.engine import Process
from emir.summary import summary_document
from emir.validate import RC_C, RC_DSPF, RC_R, _psf_tran


def quiet(*_a, **_k):
    pass


# ------------------------------------------------------------ builders


def mesh_dspf(tmp, n=12, k=20, caps=False, seed=0, name="mesh.dspf"):
    """An n x n Metal1 mesh fed at one corner, k instance pins (`M<q>:s`)
    through 2-cut contacts; with `caps`, 1 fF from every mesh node to ground."""
    rng = np.random.default_rng(seed)
    nid = lambda i, j: "VDD" if (i, j) == (0, 0) else f"VDD:{i}_{j}"   # noqa: E731
    L = ["*|DSPF 1.3", '*|DESIGN "mesh"', "*|OPERATING_TEMPERATURE 100", "*|NET VDD 0",
         "*|P (VDD I *1 0.0 0.0)"]
    sites = rng.choice(n * n - 1, k, replace=False) + 1
    for q, s in enumerate(sites):
        i, j = divmod(int(s), n)
        L.append(f"*|I (M{q}:s M{q} s I 0 {i * 2.0:.3f} {j * 2.0:.3f})")
    r = 0
    for i in range(n):
        for j in range(n):
            for di, dj in ((1, 0), (0, 1)):
                if i + di < n and j + dj < n:
                    L.append(f"R{r} {nid(i, j)} {nid(i + di, j + dj)} 0.3 $metal1 $W=1.0 "
                             f"$L=2.0 $lvl=1")
                    r += 1
    for q, s in enumerate(sites):
        i, j = divmod(int(s), n)
        L.append(f"Rc{q} {nid(i, j)} M{q}:s 10.0 $cont $VC=2 $lvl=0")
    if caps:
        L += [f"Cn{i}_{j} {nid(i, j)} 0 1e-15" for i in range(n) for j in range(n)
              if (i, j) != (0, 0)]
    L.append(".ENDS")
    return write(os.path.join(str(tmp), name), "\n".join(L) + "\n"), \
        [f"M{q}:s" for q in range(k)]


def pulse_result(tmp, pins, nt=121, periods=3, nonuniform=False, seed=1, name="cc.tran"):
    """Pin currents in Spectre's convention (into the instance): a base plus a
    pulse of random phase per pin, over whole periods; optionally on an
    uneven time base (as a variable-step simulator writes it)."""
    rng = np.random.default_rng(seed)
    if nonuniform:
        u = np.sort(rng.uniform(0.0, 1.0, nt - 2))
        t = np.concatenate([[0.0], u, [1.0]]) * periods * PERIOD
    else:
        t = np.linspace(0.0, periods * PERIOD, nt)
    ph = rng.uniform(0.0, PERIOD, len(pins))
    sigs = {p: 1e-4 + 4e-4 * (((t - ph[q]) % PERIOD) < 0.3 * PERIOD) + 1e-5 * q
            for q, p in enumerate(pins)}
    return _psf_tran(os.path.join(str(tmp), name), t, sigs)


def run_doc(*argv):
    """`emir run ARGV` in process: (RunResult, the emir.run/1 document)."""
    a = build_parser().parse_args([str(x) for x in argv])
    a.argv = [str(x) for x in argv]
    a.command = "emir run " + " ".join(a.argv)
    res = analyse(a)
    return res, summary_document(res)


def proc_with_caps(tmp, models) -> str:
    with open(PROC, encoding="utf-8") as f:
        d = yaml.safe_load(f)
    d["cap_models"] = models
    return write(os.path.join(str(tmp), "proc_caps.yaml"), yaml.safe_dump(d))


def taps_of(d, net, result, **kw):
    from emir.ccsim import cc_taps
    ing = cc_taps(d, net, result, log=quiet, **kw)
    inj = {}
    for t in ing["taps"]:
        inj[t["node"]] = inj.get(t["node"], 0.0) + t["i_avg"]
    return ing, inj


def rel(a, b):
    return float(np.max(np.abs(a - b)) / max(np.max(np.abs(a)), 1e-30))


# ------------------------------------------------------------ streamed == dense


@pytest.mark.parametrize("nonuniform", [False, True])
@pytest.mark.parametrize("budget", [None, "three", "one"])
def test_streamed_superposition_equals_dense_on_a_mesh(tmp_path, nonuniform, budget):
    """Every branch, every metric, to 1e-10 of the largest: whole chunks,
    a partial last chunk (3 samples per chunk over 121) and one sample at a
    time; on an even and on an uneven time base (trapezoid weights)."""
    path, pins = mesh_dspf(tmp_path)
    d = parse(path)
    proc = Process.load(PROC)
    ing, inj = taps_of(d, "VDD", pulse_result(tmp_path, pins, nonuniform=nonuniform),
                       period=PERIOD, feed="VDD")
    nw = Network(d, "VDD")
    per = bigblock.bytes_per_sample(len(nw.nodes), len(nw.res))
    mb = {None: None, "three": 3 * per, "one": 1}[budget]
    dense = check_net(d, "VDD", proc, inj, "VDD", taps_list=ing["taps"],
                      tap_weights=ing["weights"], log=quiet, superposition="dense",
                      max_bytes=1 << 30)
    stream = check_net(d, "VDD", proc, inj, "VDD", taps_list=ing["taps"],
                       tap_weights=ing["weights"], log=quiet, superposition="stream",
                       max_bytes=mb)
    assert dense["superposition"]["method"] == "dense"
    s = stream["superposition"]
    assert s["method"] == "stream"
    if budget == "three":
        assert s["chunk"] == 3 and s["chunks"] == -(-s["points"] // 3)
    if budget == "one":
        assert s["chunk"] == 1
    for m in ("avg", "rms", "peak"):
        assert rel(dense["Ie"][m], stream["Ie"][m]) <= 1e-10, m
    assert dense["max_ratio"]["all"] == pytest.approx(stream["max_ratio"]["all"], rel=1e-10)


def test_the_dense_path_keeps_its_reduction_inside_the_budget(tmp_path):
    """The dense matrix plus four branches-long arrays per sample of the time
    reduction fit --max-memory: a budget just above the matrix splits the
    reduction into chunks, and the answer does not move."""
    path, pins = mesh_dspf(tmp_path)
    d = parse(path)
    proc = Process.load(PROC)
    ing, inj = taps_of(d, "VDD", pulse_result(tmp_path, pins), period=PERIOD, feed="VDD")
    nb = len(Network(d, "VDD").res)
    need = bigblock.dense_bytes(nb, len(ing["taps"]))
    budget = need + 32 * nb * 10                 # room for 10 samples at a time
    r = {}
    for mb in (budget, 1 << 30):
        r[mb] = check_net(d, "VDD", proc, inj, "VDD", taps_list=ing["taps"],
                          tap_weights=ing["weights"], log=quiet, superposition="dense",
                          max_bytes=mb)
    sup = r[budget]["superposition"]
    assert sup["method"] == "dense" and sup["chunk"] == 10
    assert sup["chunks"] == -(-sup["points"] // 10)
    assert r[1 << 30]["superposition"]["chunks"] == 1
    for m in ("avg", "rms", "peak"):
        assert rel(r[budget]["Ie"][m], r[1 << 30]["Ie"][m]) <= 1e-12, m


def test_streamed_superposition_equals_dense_with_the_feed_moved(tmp_path):
    """--feed away from the reference: the feed tap carries a waveform too."""
    cc = cc_result(tmp_path)
    out = {}
    for path in ("dense", "stream"):
        res, _ = run_doc(DSPF, "--process", PROC, "--net", "VDD", "--cc-sim", cc,
                         "--mode", "cc-grid", "--period", PERIOD, "--feed", "VDD:5",
                         "--ground", "VDD",
                         "--superposition", path)
        out[path] = res.solved["VDD"]
        assert out[path]["superposition"]["method"] == path
    for m in ("avg", "rms", "peak"):
        assert rel(out["dense"]["Ie"][m], out["stream"]["Ie"][m]) <= 1e-10, m


def test_a_piece_with_unbalanced_current_is_nan_on_both_paths(tmp_path):
    """A net in pieces (only a run forced past EMIR-E207 gets there): the
    piece away from the reference has a tap and no return, so its branch
    currents have no answer (NaN, a FAIL) on both paths; the rest agree."""
    text = """*|DSPF 1.3
*|NET VDD 0
*|P (VDD I *1 0 0)
*|I (MA:s MA s I 0 10 0)
*|I (MB:s MB s I 0 30 0)
R1 VDD MA:s 1.0 $metal1 $W=1 $L=10
R2 VDD:7 MB:s 1.0 $metal1 $W=1 $L=10
.ENDS
"""
    d = parse(write(tmp_path / "pieces.dspf", text))
    proc = Process.load(PROC)
    t = np.linspace(0, 1, 11)
    taps = [{"name": "MA:s", "node": "MA:s", "i_avg": -1e-3, "wave": -1e-3 * (1 + t)},
            {"name": "MB:s", "node": "MB:s", "i_avg": -1e-3, "wave": -1e-3 * (2 - t)},
            {"name": "VDD:feed", "node": "VDD", "i_avg": 2e-3, "wave": 3e-3 * np.ones(11),
             "feed": True}]
    w = np.full(11, 1 / 11)
    r = {p: check_net(d, "VDD", proc, {"MA:s": -1e-3, "VDD": 1e-3}, "VDD", taps_list=taps,
                      tap_weights=w, log=quiet, superposition=p) for p in ("dense", "stream")}
    names = [x["name"] for x in r["dense"]["network"].res]
    i1, i2 = names.index("R1"), names.index("R2")
    for m in ("avg", "rms", "peak"):
        assert np.isnan(r["dense"]["Ie"][m][i2]) and np.isnan(r["stream"]["Ie"][m][i2])
        assert r["dense"]["Ie"][m][i1] == pytest.approx(r["stream"]["Ie"][m][i1], rel=1e-12)


def test_a_tap_off_the_net_is_coded_on_both_paths(tmp_path):
    from emir.errors import EmirError
    d = parse(DSPF)
    nw = Network(d, "VDD")
    taps = [{"name": "X:s", "node": "NOT_A_NODE", "i_avg": 0.0, "wave": np.zeros(5)}]
    with pytest.raises(EmirError) as e:
        bigblock.superpose_stream(nw, "VDD", taps, ("avg",), log=quiet)
    assert e.value.code == "EMIR-E206"
    with pytest.raises(EmirError) as e:
        nw.solve({"NOT_A_NODE": 1.0}, "VDD")
    assert e.value.code == "EMIR-E206"


def test_choose_by_memory_then_by_solve_count():
    gib = 1 << 30
    # the dense matrix does not fit: stream, whatever was asked
    for m in ("auto", "dense", "stream"):
        assert bigblock.choose(181_401, 2001, 501, 2 * gib, m)[0] == "stream"
    path, why = bigblock.choose(181_401, 2001, 501, 2 * gib)
    assert "2.7 GiB" in why and "--max-memory 2.0 GiB" in why
    # it fits: auto streams only when there are fewer than 2x as many samples as taps
    assert bigblock.choose(1000, 50, 401, gib)[0] == "dense"
    assert bigblock.choose(1000, 200, 400, gib)[0] == "dense"
    assert bigblock.choose(1000, 201, 401, gib) == (
        "stream", "401 time samples < 2 x 201 taps: streaming (one blocked solve per "
                  "sample) is the cheaper path")
    assert bigblock.choose(1000, 500, 401, gib, "dense")[0] == "dense"
    assert bigblock.choose(1000, 50, 401, gib, "stream")[0] == "stream"


def test_a_budget_below_the_matrix_streams_and_says_so(tmp_path):
    """Through the CLI, as an agent runs it: the JSON summary records the path
    and its reason, the notices carry EMIR-I450, and it validates."""
    path, pins = mesh_dspf(tmp_path, n=40, k=100)
    res = pulse_result(tmp_path, pins, nt=61)
    nb = len(Network(parse(path), "VDD").res)
    assert bigblock.dense_bytes(nb, 101) > 1 << 20           # the 1M budget is too small
    p = emir("run", path, "--process", PROC, "--cc-sim", res, "--period", PERIOD,
             "--mode", "cc-grid", "--max-memory", "1M", "--superposition", "dense",
             "--format", "json")
    assert p.returncode == 0, p.stderr
    doc = json.loads(p.stdout)
    jsonschema.validate(doc, schemas.get("run"))
    sup = doc["nets"][0]["superposition"]
    assert sup["method"] == "stream" and "above --max-memory 1.0 MiB" in sup["reason"]
    assert sup["budget_bytes"] == 1 << 20 and sup["dense_bytes"] == nb * 101 * 8
    assert "EMIR-I450" in [n["code"] for n in doc["notices"]]
    assert "streamed in" in doc["nets"][0]["metric_note"]


def test_the_scalar_route_says_so_and_dc_has_no_superposition_record(tmp_path):
    """an agent tells the in-phase bounds from the exact path by the
    record, not by the prose of `metric_note`."""
    _, doc = run_doc(DSPF, "--process", PROC, "--net", "VDD", "--cc-sim",
                     cc_result(tmp_path), "--mode", "cc-grid", "--period", PERIOD,
                     "--scalars")
    sup = doc["nets"][0]["superposition"]
    assert sup["method"] == "scalar" and "in-phase upper bound" in sup["reason"]
    assert sup["taps"] > 0 and sup["points"] is None and sup["dense_bytes"] is None
    _, doc = run_doc(DSPF, "--process", PROC, "--net", "VDD", "--currents", CUR)
    assert doc["nets"][0]["superposition"] is None


def test_dense_over_the_budget_warns_and_a_budget_over_the_ram_is_lowered(tmp_path,
                                                                         monkeypatch):
    """`--superposition dense` above --max-memory streams with a coded
    warning (not only an info line), and a --max-memory the machine cannot
    hold is lowered to 80 % of its physical memory (it was OOM-killed: exit
    137 and no document)."""
    from emir.errors import collecting
    path, pins = mesh_dspf(tmp_path, n=40, k=100)
    res = pulse_result(tmp_path, pins, nt=61)
    p = emir("run", path, "--process", PROC, "--cc-sim", res, "--period", PERIOD,
             "--mode", "cc-grid", "--max-memory", "1M", "--superposition", "dense",
             "--format", "json")
    doc = json.loads(p.stdout)
    assert {"EMIR-W452", "EMIR-I450"} <= {n["code"] for n in doc["notices"]}
    monkeypatch.setattr(bigblock, "physical_memory", lambda: 1 << 30)
    with collecting() as notes:
        assert bigblock.memory_budget(64 << 30, log=None) == int(0.8 * (1 << 30))
        assert bigblock.memory_budget(512 << 20, log=None) == 512 << 20
    assert [n["code"] for n in notes] == ["EMIR-W550"]
    help_text = emir("run", "--help").stdout
    assert "EMIR-W452" in help_text and "EMIR-W550" in help_text


@pytest.mark.parametrize("text,value", [("2G", 2 << 30), ("512M", 512 << 20),
                                        ("1.5GiB", int(1.5 * (1 << 30))), ("4096k", 4 << 20),
                                        ("1048576", 1 << 20), ("3 g", 3 << 30)])
def test_parse_size(text, value):
    assert bigblock.parse_size(text) == value


@pytest.mark.parametrize("bad", ["5", "lots", "-1G", "2X"])
def test_a_bad_size_is_a_usage_error(bad):
    p = emir("run", DSPF, "--process", PROC, "--max-memory", bad, "--format", "json")
    assert p.returncode == 2
    assert json.loads(p.stdout)["code"] == "EMIR-E507"


# ------------------------------------------------------------ dynamic IR in cc-grid


def sine_result(tmp, freq, i0=1e-3, periods=12, per=200, phase=0.0, name="sine.tran"):
    t = np.linspace(0.0, periods / freq, periods * per + 1)
    return _psf_tran(os.path.join(str(tmp), name), t,
                     {"MA:s": i0 * np.sin(2 * np.pi * freq * t + phase)})


@pytest.mark.parametrize("wrc", [0.1, 1.0, 10.0])
def test_dynamic_ir_matches_the_exact_rc_response(tmp_path, wrc):
    """The validation suite's R || C case through `emir run --mode cc-grid
    --dynamic`: a sinusoid into R || C settles at |I| |R || 1/(jwC)|, and the
    periodic steady state is what the run reports (no start-up transient)."""
    rc = write(tmp_path / "rc.dspf", RC_DSPF)
    f = wrc / (2 * np.pi * RC_R * RC_C)
    res = sine_result(tmp_path, f)
    _, doc = run_doc(rc, "--process", PROC, "--cc-sim", res, "--mode", "cc-grid",
                     "--period", 1 / f, "--dynamic")
    dy = doc["nets"][0]["dynamic"]
    exact = 1e-3 * abs(1.0 / (1.0 / RC_R + 1j * 2 * np.pi * f * RC_C)) * 1e3
    assert dy["worst_mV"] == pytest.approx(exact, rel=5e-3)
    assert doc["nets"][0]["dynamic_mV"] == dy["worst_mV"]
    assert dy["periodic"] and dy["settled"] and dy["node"] == "MA:s"
    assert (dy["x_um"], dy["y_um"]) == (10.0, 0.0)
    assert "open loop" in dy["source"]
    jsonschema.validate(json.loads(json.dumps(doc, default=float)), schemas.get("run"))


DECAP_RC_DSPF = RC_DSPF.replace(".ENDS\n.END\n", """*|NET VSS 0.0
*|P (VSS I *1 0.0000 -5.0000)
*|I (MD:d MD d I 0 10.0000 -5.0000)
*|I (MD:s MD s I 0 10.2000 -5.0000)
Rd VSS MD:d 0.001000 $metal1 $L=0.01 $W=1.000000 $lvl=1
Rs VSS MD:s 0.001000 $metal1 $L=0.01 $W=1.000000 $lvl=1
MD MD:d MA:s MD:s VSS ncap_demo L=2e-07 W=1e-04
.ENDS
.END
""")


@pytest.mark.parametrize("racc", [50.0, 1e-3])
def test_dynamic_ir_with_a_mos_decap_matches_the_closed_form(tmp_path, racc):
    """The validation suite's decap-series-R case, with the decap a MOS device
    in the DSPF sized from the process file's cap_models: C = cox W L = 20 pF,
    R_access = esr_a. The droop at wRC = 1 is |I| |R || 1/jwC_w || (R_acc +
    1/jwC_d)|, and the access R is what keeps the decap from erasing it."""
    d = write(tmp_path / "decap.dspf", DECAP_RC_DSPF)
    proc = proc_with_caps(tmp_path, {"ncap_demo": {"cox": 1.0, "esr_a": racc}})
    f = 1.0 / (2 * np.pi * RC_R * RC_C)
    w = 2 * np.pi * f
    res = sine_result(tmp_path, f)
    _, doc = run_doc(d, "--process", proc, "--net", "VDD", "--cc-sim", res,
                     "--mode", "cc-grid", "--period", 1 / f, "--dynamic")
    dy = doc["nets"][0]["dynamic"]
    cd = 1.0 * 1e-4 * 2e-7
    exact = 1e-3 * abs(1.0 / (1.0 / RC_R + 1j * w * RC_C + 1.0 / (racc + 1.0 / (1j * w * cd))))
    assert dy["worst_mV"] == pytest.approx(exact * 1e3, rel=5e-3)
    assert dy["settled"]
    assert ("1 MOS decap" in dy["decap_note"]) and dy["c_total_F"] > 2e-11
    if racc == 50.0:
        ideal = 1e-3 * abs(1.0 / (1.0 / RC_R + 1j * w * (RC_C + cd)))
        assert dy["worst_mV"] > 1.05 * ideal * 1e3     # R_acc matters


def test_periodic_steady_state_removes_the_start_up(tmp_path):
    """dynir.transient: from the DC solution the window's first periods carry
    a start-up transient the circuit does not have; periodic=True integrates
    the window again from where it ended until it repeats."""
    from emir.dynir import grounded_caps, transient
    d = parse(write(tmp_path / "rc.dspf", RC_DSPF))
    nw = Network(d, "VDD")
    C, _ = grounded_caps(d, "VDD", nw.idx, log=quiet)
    f = 1.0 / (2 * np.pi * RC_R * RC_C)
    t = np.linspace(0.0, 4 / f, 801)
    tap = {"name": "MA:s", "node": "MA:s", "i_avg": 0.0, "wave": 1e-3 * np.cos(2 * np.pi * f * t)}
    exact = 1e-3 * abs(1.0 / (1.0 / RC_R + 1j * 2 * np.pi * f * RC_C))
    once = transient(nw, "VDD", [tap], t, node_c=C, n_steps=8000, log=quiet)
    pss = transient(nw, "VDD", [tap], t, node_c=C, n_steps=8000, log=quiet, periodic=True)
    assert once["passes"] == 1 and not once["periodic"]
    # the DC solution at t0 is R I0, sqrt(2) x the steady amplitude at wRC = 1
    assert once["worst_V"] == pytest.approx(RC_R * 1e-3, rel=1e-6)
    assert once["worst_V"] == pytest.approx(np.sqrt(2) * exact, rel=1e-6)
    assert pss["worst_V"] == pytest.approx(exact, rel=2e-3)
    assert pss["passes"] >= 2 and pss["settled"]


def test_the_html_report_shows_the_cc_grid_dynamic_droop(tmp_path):
    rc = write(tmp_path / "rc.dspf", RC_DSPF)
    f = 1.0 / (2 * np.pi * RC_R * RC_C)
    res, doc = run_doc(rc, "--process", PROC, "--cc-sim", sine_result(tmp_path, f),
                       "--mode", "cc-grid", "--period", 1 / f, "--dynamic",
                       "--html", tmp_path / "html")
    html = next((tmp_path / "html").glob("*_VDD.html")).read_text()
    assert "dynamic IR" in html and "Worst dynamic excursion by node" in html
    assert f"{doc['nets'][0]['dynamic_mV']:.4f} mV" in html


def test_dynamic_on_an_r_only_dspf_says_why_not(tmp_path):
    """No capacitance on the net: the static results stand, dynamic is null and
    EMIR-W450 says what to extract."""
    path, pins = mesh_dspf(tmp_path)
    res, doc = run_doc(path, "--process", PROC, "--cc-sim", pulse_result(tmp_path, pins),
                       "--period", PERIOD, "--mode", "cc-grid", "--dynamic")
    net = doc["nets"][0]
    assert net["dynamic"] is None and net["dynamic_mV"] is None
    w = [n for n in doc["notices"] if n["code"] == "EMIR-W450"]
    assert w and "R-only extraction carries none" in w[0]["message"]
    assert net["verdict"] in ("FAIL", "WARN", "CLEAN") and net["ir_drop_mV"] > 0


def test_dynamic_with_mesh_capacitance_runs_on_both_paths(tmp_path):
    """--dynamic is independent of how the static superposition ran."""
    path, pins = mesh_dspf(tmp_path, caps=True)
    res = pulse_result(tmp_path, pins)
    out = {}
    for p in ("dense", "stream"):
        _, doc = run_doc(path, "--process", PROC, "--cc-sim", res, "--period", PERIOD,
                         "--mode", "cc-grid", "--dynamic", "--superposition", p,
                         "--dyn-steps", "800")
        out[p] = doc["nets"][0]["dynamic"]
        assert out[p]["n_steps"] == 800 and out[p]["settled"]
    assert out["dense"]["worst_mV"] == pytest.approx(out["stream"]["worst_mV"], rel=1e-12)
    assert out["dense"]["worst_mV"] > 0


def test_dynamic_on_a_harmonic_result_says_why_not(tmp_path):
    """A PSS fd result has harmonics, not waveforms: cc-grid still runs (scalar
    in-phase bounds) and EMIR-W450 says the dynamic droop was not computed."""
    rc = write(tmp_path / "rc.dspf", RC_DSPF)
    L = ['HEADER', '"PSFversion" "1.00"', '"analysis type" "pss"', '"analysis name" "pss"',
         'TYPE', 'SWEEP', '"freq" "sweep"', 'TRACE', '"MA:s" "I"', 'VALUE']
    for h, (re_, im) in enumerate([(1e-3, 0.0), (5e-4, 0.0), (1e-4, 0.0)]):
        L += [f'"freq" {h * 1e9:.6e}', f'"MA:s" ({re_:.6e} {im:.6e})']
    fd = write(tmp_path / "pss.fd", "\n".join(L + ["END"]) + "\n")
    _, doc = run_doc(rc, "--process", PROC, "--cc-sim", fd, "--mode", "cc-grid",
                     "--dynamic")
    assert doc["nets"][0]["dynamic"] is None
    assert any(n["code"] == "EMIR-W450" and "harmonic" in n["message"]
               for n in doc["notices"])


@pytest.mark.parametrize("extra", [["--currents", '{"VDD": 0.001, "MP0:s": -0.001}'],
                                   ["--cc-sim", "SIM", "--mode", "cc-grid", "--scalars"]])
def test_dynamic_without_waveforms_is_refused_before_the_run(tmp_path, extra):
    extra = [str(cc_result(tmp_path)) if x == "SIM" else x for x in extra]
    p = emir("check", DSPF, "--process", PROC, "--net", "VDD", "--dynamic", *extra,
             "--format", "json")
    assert p.returncode == 2
    doc = json.loads(p.stdout)
    assert "EMIR-E550" in [f["code"] for f in doc["findings"] if f["level"] == "FAIL"]
    p = emir("run", DSPF, "--process", PROC, "--net", "VDD", "--dynamic", *extra,
             "--format", "json")
    assert p.returncode == 2 and "EMIR-E550" in json.loads(p.stdout)["details"]["causes"]


def test_rcc_dynamic_ir_is_in_the_summary_object(tmp_path):
    """rcc mode always measured dynamic IR; the summary now says where."""
    _, doc = run_doc(DSPF, "--process", PROC, "--net", "VDD", "--cc-sim",
                     rcc_result(tmp_path), "--mode", "rcc", "--period", PERIOD)
    net = doc["nets"][0]
    assert net["mode"] == "rcc" and net["dynamic"]["worst_mV"] == net["dynamic_mV"]
    assert "measured" in net["dynamic"]["source"] and net["dynamic"]["node"]


def test_self_consistent_runs_take_dynamic_too(tmp_path, monkeypatch):
    """--self-consistent re-simulates (Spectre, with --run); with the simulator
    stubbed by a fixed result, one pass (the open loop, reported at the pass
    limit) and --dynamic integrates its currents. (A fixed result on later
    passes is a netlist that ignores the droop: EMIR-E524.)"""
    from _helpers import spectre_split_netlist

    from emir import ccsim
    rc = write(tmp_path / "rc.dspf", RC_DSPF)
    f = 1.0 / (2 * np.pi * RC_R * RC_C)
    res = sine_result(tmp_path, f)
    monkeypatch.setattr(ccsim, "run_spectre",
                        lambda netlist, **kw: {"raw": res, "seconds": 0.0})
    droop = tmp_path / "droop.scs"
    net = spectre_split_netlist(tmp_path / "cc.scs", rc, "VDD", droop)
    _, doc = run_doc(rc, "--process", PROC, "--net", "VDD", "--netlist", net,
                     "--mode", "cc-grid", "--self-consistent", "--vdd", "1.0", "--run",
                     "--droop-file", droop, "--max-passes", "1",
                     "--period", 1 / f, "--dynamic", "--no-check")
    n = doc["nets"][0]
    assert n["self_consistent"] and n["dynamic"]["settled"]
    exact = 1e-3 * abs(1.0 / (1.0 / RC_R + 1j * 2 * np.pi * f * RC_C)) * 1e3
    assert n["dynamic"]["worst_mV"] == pytest.approx(exact, rel=5e-3)


def test_the_report_relays_the_dynamic_droop(tmp_path):
    from emir.report import build, markdown
    rc = write(tmp_path / "rc.dspf", RC_DSPF)
    f = 1.0 / (2 * np.pi * RC_R * RC_C)
    _, doc = run_doc(rc, "--process", PROC, "--cc-sim", sine_result(tmp_path, f),
                     "--mode", "cc-grid", "--period", 1 / f, "--dynamic",
                     "--power-nets", "VDD")
    r = build(json.loads(json.dumps(doc, default=float)), "summary.json")
    assert r["ir_by_power_net"][0]["dynamic_mV"] == pytest.approx(
        doc["nets"][0]["dynamic_mV"])
    md = markdown(r)
    assert "dynamic (open loop)" in md and "periodic steady state" in md


# ------------------------------------------------------------ emir next


@pytest.fixture
def tools(monkeypatch):
    from emir import site
    monkeypatch.setattr(site, "tool", lambda name, explicit=None: (None, "$PATH"))


def plan_for(**kw):
    f = P.Facts(have_xyce=True, **kw)
    return P.build(f)


def test_a_big_block_gets_the_whole_recipe(tools, monkeypatch):
    monkeypatch.setitem(P.THRESHOLDS, "big_block_nodes", 10)
    plan = plan_for(dspf=DSPF, process=PROC, net="VDD")
    adv = " | ".join(plan.advice)
    assert "bigblock" in plan.rules
    for needle in ("C-only with coupling caps", "R-only with geometry",
                   "emir save-lines <dspf> --mode cc-grid", "PSS", "coherent stimulus",
                   "--period", "one EM run per operating mode", "about 2% of its supply",
                   "--self-consistent", "hierarchically"):
        assert needle in adv, needle
    assert plan.advice[-1].startswith("never invent process limits")
    assert plan.mode["chosen"] is None and "cc-grid is recommended" in plan.mode["why"]


def test_dynamic_advice_follows_the_dspf_capacitance(tools, monkeypatch, tmp_path):
    monkeypatch.setitem(P.THRESHOLDS, "big_block_nodes", 10)
    with_c = " | ".join(plan_for(dspf=DSPF, process=PROC, net="VDD").advice)
    assert "carries capacitance on VDD: add --dynamic" in with_c
    path, _ = mesh_dspf(tmp_path)
    without = " | ".join(plan_for(dspf=path, process=PROC).advice)
    assert "extract the rails RC" in without


def test_rcc_on_a_big_block_is_cautioned(tools, monkeypatch):
    monkeypatch.setitem(P.THRESHOLDS, "big_block_nodes", 10)
    plan = plan_for(dspf=DSPF, process=PROC, net="VDD", mode="rcc")
    assert any(a.startswith("--mode rcc on a block this size") for a in plan.advice)


def test_the_memory_estimate_is_advised_at_any_size(tools, monkeypatch, tmp_path):
    from emir import plan_bigblock as PB
    path, _ = mesh_dspf(tmp_path)
    plan = plan_for(dspf=path, process=PROC)
    assert "bigblock" not in plan.rules               # small, and it fits
    monkeypatch.setattr(PB, "DEFAULT_MAX_MEMORY", 1000)
    plan = plan_for(dspf=path, process=PROC)
    assert "bigblock" in plan.rules
    est = PB.memory_estimate(P.Facts(dspf=path))
    assert est == [("VDD", 284, 21, 284 * 21 * 8)]
    assert any("VDD: the exact superposition's dense transfer matrix would need 46.6 KiB "
               "(284 branches x 21 taps)" in a for a in plan.advice)
    assert not any("big-block recipe" in a for a in plan.advice)


def test_next_json_carries_the_recipe_and_validates(tools, monkeypatch):
    import io

    from emir.output import Out
    monkeypatch.setitem(P.THRESHOLDS, "big_block_nodes", 10)
    buf = io.StringIO()
    assert P.main(["--dspf", DSPF, "--process", PROC, "--net", "VDD", "--have-xyce",
                   "--format", "json"], sink=Out("json", buf)) == 0
    doc = json.loads(buf.getvalue())
    jsonschema.validate(doc, schemas.get("next"))
    assert "bigblock" in doc["rules"] and doc["facts"]["size"]["class"] == "big"
    assert any(a.startswith("big-block recipe") for a in doc["advice"])
