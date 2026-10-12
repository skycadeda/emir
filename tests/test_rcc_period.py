"""rcc's window: the record's last whole periods of the person's --period, up to
three. rcc is exact for the simulated record over the whole periods you give
(--period); whether the testbench has settled is yours to establish, as for any
.meas (the maintainer's decision of 2026-10-11, after three rounds of window
confirmation each still let an adversarial case print > 2 % off: a finite
transient cannot prove steady state).

Refused, with a code and no number, only for what EMIR can check for certain:
a transient with no --period (EMIR-E428); fewer than two whole periods in the
record (E431); the last two whole periods differing by more than 2 % in the
avg, rms or peak of a branch carrying 1 % of its net's largest current
(E429). Exactness on the examples' simulated records: tests/test_rcc_exact.py."""
import json
import os

import numpy as np
import pytest
from _helpers import DSPF, PROC, emir

from emir import plan as P
from emir import rcc, waveform
from emir.dspf import parse
from emir.engine import Process
from emir.errors import EmirError, collecting
from emir.validate import _psf_tran

T = 1e-9
PHASES = (0.0, 0.25, 0.5, 0.75)
QUIET = dict(log=lambda *a, **k: None)


def pulse(t, start, width=0.1, peak=2e-3, period=T):
    """A triangle pulse once per `period`, from `start` (in periods)."""
    ph = (t / period) % 1.0
    x = (ph - start) / width
    return np.where((x > 0) & (x < 1), peak * (1 - np.abs(2 * x - 1)), 0.0)


def rail(tmp, name, t, i):
    """The sample rail's branch currents for the four loads' currents `i`
    (Rm1..Rm12 the feed's straps, Rc0..Rc3 the contacts), as a PSF result."""
    sigs = {f"Rm{n}:1": sum(i[(n - 1) // 3:]) for n in range(1, 13)}
    sigs.update({f"Rc{k}:1": i[k] for k in range(4)})
    return _psf_tran(os.path.join(str(tmp), name), t, sigs)


def staggered(tmp, n=8):
    """The four loads, each pulsing once per T, a quarter period apart, over
    n T: the feed (Rm1..Rm3) repeats every T/4, every contact only every T."""
    t = np.linspace(0.0, n * T, n * 2000 + 1)
    return rail(tmp, f"staggered{n}.tran", t, [pulse(t, ph) for ph in PHASES])


def like_example_08(tmp, stop=12.5e-9):
    """Example 08's shape: one load pulsed with a 4 ns period from 0.5 ns."""
    t = np.linspace(0.0, stop, int(round(stop / 5e-12)) + 1)
    on = np.where(t >= 0.5e-9, pulse(t - 0.5e-9, 0.0, width=0.45, period=4e-9), 0.0)
    z = 0 * t
    return rail(tmp, f"ex08_{stop:g}.tran", t, [z, z, z, on])


def contacts(r, m="avg"):
    names = [x["name"] for x in r["network"].res]
    return np.array([r["Ie"][m][names.index(f"Rc{k}")] for k in range(4)])


def check(raw, **kw):
    return rcc.rcc_check(parse(DSPF), "VDD", Process.load(PROC), raw, "VDD", **QUIET, **kw)


def refused(raw, **kw) -> EmirError:
    with pytest.raises(EmirError) as e:
        check(raw, **kw)
    return e.value


def run_json(*args):
    p = emir("run", DSPF, "--process", PROC, "--net", "VDD", *args, "--format", "json")
    return p, json.loads(p.stdout)


# ---------------------------------------------------------------- the window


def test_the_window_is_the_record_s_last_whole_periods_up_to_three(tmp_path):
    """Eight periods: the last three (5 T .. 8 T); two: both; with the
    numbers of every contact exactly one 0.1 T triangle of 2 mA per period
    (0.1 mA average, 2 mA peak)."""
    with collecting():
        r8 = check(staggered(tmp_path, 8), period=T)
        r2 = check(staggered(tmp_path, 2), period=T)
    assert r8["window"]["t0"] == pytest.approx(5 * T) and r8["window"]["t1"] == 8 * T
    assert r8["window"]["n_periods"] == 3 and r8["window"]["period"] == T
    assert r2["window"]["t0"] == pytest.approx(0.0, abs=1e-21)
    assert r2["window"]["n_periods"] == 2
    for r in (r8, r2):
        assert contacts(r) == pytest.approx(np.full(4, 0.1e-3), rel=1e-9)
        assert contacts(r, "peak") == pytest.approx(np.full(4, 2e-3), rel=1e-9)
        assert r["window"]["change"] < 1e-9 and r["window"]["branches"] == 16


def test_a_start_up_period_falls_out_of_a_four_period_record(tmp_path):
    """From the open-simulator proof (scripts/opensim_proof.py): a contact's
    spike out-peaks the feed while its far load starts only in the second
    period. Over four periods the window is the last three, and the feed's
    average is the steady one (MP0 0.25 mA + MP3 0.30 mA; the decap MP1 nets
    zero)."""
    def tri(t, start, width, peak):
        x = ((t / T) % 1.0 - start) / width
        return np.where((x > 0) & (x < 1), peak * (1 - np.abs(2 * x - 1)), 0.0)

    t = np.linspace(0.0, 4 * T, 4 * 2000 + 1)
    i0 = tri(t, 0.10, 0.10, 5e-3)
    i1 = -tri(t, 0.10, 0.10, 2.5e-3) + tri(t, 0.30, 0.50, 0.5e-3)
    i3 = np.where(t >= T, tri(t, 0.55, 0.30, 2e-3), 0.0)
    raw = rail(tmp_path, "startup.tran", t, [i0, i1, 0 * t, i3])
    with collecting():
        r = check(raw, period=T)
    assert r["window"]["t0"] == pytest.approx(T)
    i = [x["name"] for x in r["network"].res].index("Rm1")
    assert r["Ie"]["avg"][i] == pytest.approx(0.25e-3 + 0.30e-3, rel=2e-3)


def test_an_operating_point_needs_no_period(tmp_path):
    t = np.array([0.0])
    raw = rail(tmp_path, "op.tran", t, [np.full(1, 1e-3)] * 3 + [np.full(1, 2e-3)])
    with collecting():
        r = check(raw)
    assert r["window"] is None
    assert contacts(r) == pytest.approx([1e-3, 1e-3, 1e-3, 2e-3])
    for m in ("rms", "peak"):
        assert r["Ie"][m] == pytest.approx(r["Ie"]["avg"])
    p, d = run_json("--cc-sim", raw)
    assert p.returncode == 0 and d["schema"] == "emir.run/1"
    assert d["nets"][0]["window"] is None and d["nets"][0]["max_ratio"]["all"] > 0
    assert not any("period" in m["what"] for m in _plan(cc_sim=raw, net="VDD").missing)


def test_the_window_is_recorded_and_reported(tmp_path):
    raw = staggered(tmp_path)
    out = os.path.join(str(tmp_path), "s.json")
    p = emir("run", DSPF, "--process", PROC, "--net", "VDD", "--cc-sim", raw,
             "--period", "1n", "--summary-out", out)
    assert p.returncode == 0, p.stderr
    win = json.load(open(out))["nets"][0]["window"]
    assert win == {"t0_s": pytest.approx(5 * T), "t1_s": pytest.approx(8 * T),
                   "period_s": T, "n_periods": 3, "change": pytest.approx(0.0, abs=1e-9),
                   "branches": 16}
    md = emir("report", "--from", out).stdout
    assert "Window: the record's last 3 whole periods of 1 ns (--period)" in md
    # the contract, in its one sentence (review of 93da011: the report said it
    # in other words)
    from emir import RCC_CONTRACT
    assert RCC_CONTRACT[:-1] in " ".join(md.split())


# ---------------------------------------------------------------- refusals


def test_a_transient_without_a_period_is_refused(tmp_path):
    """EMIR-E428: EMIR never guesses a period, even where the record would
    let it find one (8 whole periods of every contact)."""
    raw = staggered(tmp_path)
    e = refused(raw)
    assert e.code == "EMIR-E428" and "--period" in e.message and e.exit_code == 2
    p, d = run_json("--cc-sim", raw)
    assert p.returncode == 2 and d["schema"] == "emir.error/1" and d["code"] == "EMIR-E428"
    assert "verdict" not in p.stdout and "x on" not in p.stderr
    assert any(s["command"].startswith("emir next ") for s in d["next_steps"])


def test_a_record_with_one_whole_period_is_refused(tmp_path):
    """EMIR-E431: the last two periods cannot be compared; the stop time for
    two is in the message and its details."""
    t = np.linspace(0.0, 1.5 * T, 3001)
    raw = rail(tmp_path, "short.tran", t, [pulse(t, ph) for ph in PHASES])
    e = refused(raw, period=T)
    assert e.code == "EMIR-E431" and e.details["periods_in_record"] == 1
    assert e.details["periods_needed"] == 2 and e.details["stop_time_s"] == pytest.approx(2 * T)
    assert "to t = 2e-09 s" in e.message
    p, d = run_json("--cc-sim", raw, "--period", "1ns")
    assert p.returncode == 2 and d["code"] == "EMIR-E431"
    p = emir("check", DSPF, "--process", PROC, "--net", "VDD", "--cc-sim", raw,
             "--period", "1e-9", "--format", "json")
    d = json.loads(p.stdout)
    assert p.returncode == 2 and any(f["code"] == "EMIR-E431" and f["level"] == "FAIL"
                                     for f in d["findings"])


def test_a_period_longer_than_the_record_is_questioned(tmp_path):
    t = np.linspace(0.0, 8 * T, 8001)
    raw = rail(tmp_path, "p.tran", t, [pulse(t, ph) for ph in PHASES])
    e = refused(raw, period=1e-6)
    assert e.code == "EMIR-E431" and e.details["periods_in_record"] == 0
    assert "check --period" in e.message
    # emir next asks the person about the period before planning 2 us of simulation
    plan = _plan(cc_sim=raw, net="VDD", period=1e-6)
    assert any("longer than the whole record" in m["why"] for m in plan.missing)
    assert not plan.has("simulate") and not plan.has("run")


def test_a_given_sub_period_is_refused_naming_the_branch(tmp_path):
    """--period T/4 on the staggered loads: the feed repeats over it, each
    contact does not; the change is named on the branch."""
    e = refused(staggered(tmp_path), period=T / 4)
    assert e.code == "EMIR-E429" and e.details["change"] > 0.02
    assert e.details["branch"].startswith(("Rc", "Rm")) and e.details["metric"] in (
        "avg", "rms", "peak")
    assert f"branch {e.details['branch']}'s" in e.message
    assert "Give the right --period, or simulate longer if the block is still starting " \
           "up" in e.message


def test_a_start_up_in_the_last_two_periods_is_refused(tmp_path):
    """A load that starts switching in the record's last period: E429, never
    averaged."""
    t = np.linspace(0.0, 5 * T, 10001)
    i = [pulse(t, 0.1) for _ in PHASES]
    i[3] = i[3] * (t >= 4 * T)
    e = refused(rail(tmp_path, "late.tran", t, i), period=T)
    assert e.code == "EMIR-E429" and e.details["branch"] in ("Rc3", "Rm10", "Rm11", "Rm12")
    assert e.details["change"] == pytest.approx(1.0)
    assert e.details["stop_time_s"] == pytest.approx(10 * T)    # twice the record


def test_the_branches_judge_the_window_not_the_feed():
    """A feed that repeats while two of its branches drift by as much the
    other way (one load growing, one shrinking): refused on the branches."""
    t = np.linspace(0.0, 5 * T, 5001)
    a = pulse(t, 0.1) * (1 + 0.1 * t / T)
    b = pulse(t, 0.1) * (1 - 0.1 * t / T)
    assert waveform.window(t, a + b, period=T, **QUIET)["change"] < 1e-9
    with pytest.raises(EmirError) as e:
        waveform.window(t, np.vstack([a, b]), period=T, names=["a", "b"], **QUIET)
    assert e.value.code == "EMIR-E429" and e.value.details["branch"] in ("a", "b")


def test_a_refused_run_leaves_no_number_in_its_summary_or_html(tmp_path):
    raw = like_example_08(tmp_path)
    out, html = tmp_path / "s.json", tmp_path / "html"
    p = emir("run", DSPF, "--process", PROC, "--net", "VDD", "--cc-sim", raw,
             "--summary-out", out, "--html", html)
    assert p.returncode == 2 and "EMIR-E428" in p.stderr
    doc = json.loads(out.read_text())
    assert doc["schema"] == "emir.error/1" and doc["code"] == "EMIR-E428"
    assert doc["details"]["run"]["cc_sim"] == os.path.abspath(raw)
    page = (html / "index.html").read_text()
    assert "EMIR-E428" in page and "no number" in page
    assert not [f for f in os.listdir(html) if f.endswith(".json")]
    r = emir("report", "--from", out, "--format", "json")
    assert r.returncode == 2 and json.loads(r.stdout)["code"] == "EMIR-E104"
    # the same record with its period: three whole 4 ns periods, the last
    # three: 0.45 x 4 ns triangles of 2 mA, 0.45 mA average
    with collecting():
        r = check(raw, period=4e-9)
    assert r["window"]["n_periods"] == 3 and r["window"]["t0"] == pytest.approx(0.5e-9)
    assert contacts(r)[3] == pytest.approx(0.45e-3, rel=1e-3)


def test_a_refused_run_moves_an_earlier_run_s_numbers_aside(tmp_path):
    """Review of ed6504d: a refused run rewrote only the --html index; the
    earlier run's per-net pages, charts and viewer data (the Explorer's)
    stayed, with their numbers. They are moved to stale/ now."""
    good = staggered(tmp_path)
    html, view = tmp_path / "h", tmp_path / "v"
    p = emir("run", DSPF, "--process", PROC, "--net", "VDD", "--cc-sim", good,
             "--period", "1n", "--html", html, "--viewer", view)
    assert p.returncode == 0, p.stderr
    pages = sorted(f for f in os.listdir(html) if f != "index.html")
    data = sorted(f for f in os.listdir(view) if f in ("emir_viz.json", "emir_data.il"))
    assert pages and data
    p = emir("run", DSPF, "--process", PROC, "--net", "VDD", "--cc-sim", good,
             "--html", html, "--viewer", view)
    assert p.returncode == 2
    left = set(os.listdir(html)) - {"index.html", "stale"}
    assert not [f for f in left if f.endswith((".json", ".html", ".png"))], left
    assert not {"emir_viz.json", "emir_data.il", "emir.lyrdb"} & set(os.listdir(view))
    assert set(pages) <= set(os.listdir(html / "stale"))
    assert set(data) <= set(os.listdir(view / "stale"))
    assert "stale/" in (html / "index.html").read_text()


def test_a_refusal_names_the_charts_without_matplotlib():
    """The refusal moves the charts aside by name, without importing the
    chart module (it needs matplotlib, which a core install lacks: the
    refusal turned into EMIR-E603, exit 3)."""
    from emir import netreport
    chart = pytest.importorskip("emir.chart")
    assert netreport.CHART_FILES == (chart.CHART_FILE,
                                      chart.CHART_FILE.replace(".png", "_light.png"))


def test_a_period_of_zero_or_less_is_refused(tmp_path):
    """Review of ed6504d: --period 0 was taken as no period, and a negative
    one planned a simulation to a negative time."""
    raw = staggered(tmp_path)
    for bad in ("0", "-1n"):
        p, d = run_json("--cc-sim", raw, f"--period={bad}")
        assert p.returncode == 2 and d["code"] == "EMIR-E507", (bad, d)


# ---------------------------------------------------------------- the rule


def m(avg, rms, peak):
    return {"avg": np.array(avg, dtype=float), "rms": np.array(rms, dtype=float),
            "peak": np.array(peak, dtype=float)}


def test_a_branch_matters_from_one_percent_of_its_net_s_largest():
    last = m([1e-3, 0.0, 0.0, 1e-6, 1e-15], [1e-3, 2e-5, 5e-6, 1e-6, 1e-15],
             [1e-3, 3e-5, 9e-6, 1e-6, 1e-15])
    # 1 % of the largest avg, rms or peak: 1e-5 A; and above 1 pA
    assert list(waveform.matters(last, last)) == [True, True, False, False, False]
    # either period counts: a branch silent in the last, active in the one before
    before = m([1e-3, 0.0, 0.0, 2e-5, 0.0], [1e-3, 2e-5, 5e-6, 2e-5, 0.0],
               [1e-3, 3e-5, 9e-6, 2e-5, 0.0])
    assert list(waveform.matters(last, before)) == [True, True, False, True, False]
    # the simulator's noise (below 1 pA) matters to nothing
    assert not waveform.matters(m([1e-13], [1e-13], [1e-13]),
                                m([1e-13], [1e-13], [1e-13])).any()


def test_a_branch_that_is_not_a_number_matters_to_nothing():
    """Review of ed6504d: one NaN made every branch's share NaN, so no branch
    judged the window (with --force); a NaN branch never switches the
    comparison off."""
    last = m([1e-3, 2e-3, np.nan], [1e-3, 2e-3, np.nan], [1e-3, 2e-3, np.nan])
    before = m([1e-3, 1e-3, 1e-3], [1e-3, 1e-3, 1e-3], [1e-3, 1e-3, 1e-3])
    rows = waveform.matters(last, before)
    assert list(rows) == [True, True, False]
    d, r, _ = waveform.change(last, before, rows)
    assert r == 1 and d == pytest.approx(0.5)
    t = np.linspace(0.0, 3 * T, 3001)
    B = np.vstack([pulse(t, 0.1), pulse(t, 0.1) * (t < 2 * T), pulse(t, 0.1)])
    B[2, 2900] = np.nan                     # inside the record's last period
    with pytest.raises(EmirError) as e:
        waveform.window(t, B, period=T, names=["a", "b", "c"], **QUIET)
    assert e.value.code == "EMIR-E429" and e.value.details["branch"] == "b"


def test_an_average_is_held_to_itself_not_to_its_rms():
    """A spiky branch (avg 0.3 of rms): a 4 % change of its average is over
    2 %, though it is only 1.2 % of its rms."""
    d, _, metric = waveform.change(m([0.3], [1.0], [3.0]), m([0.3 * 1.04], [1.0], [3.0]))
    assert metric == "avg" and d == pytest.approx(0.012 / 0.312) and d > waveform.TOL
    # a capacitor's average (near zero) is held to 10 % of its own rms
    d, _, _ = waveform.change(m([1e-9], [1.0], [3.0]), m([2e-9], [1.0], [3.0]))
    assert d < 1e-6


def test_a_change_from_a_silent_period_is_at_most_all_of_it():
    """Review of ed6504d: a branch silent in the record's last period changed
    'by 45097232899.5 %': its rms was divided by the silent one's."""
    d, _, _ = waveform.change(m([0.0], [0.0], [0.0]), m([1e-3], [2e-3], [3e-3]))
    assert d == pytest.approx(1.0)


def test_a_net_that_carries_nothing_takes_the_period_of_the_run(tmp_path):
    """A run over every net: VSS and OUT carry nothing above the simulator's
    noise floor, so no branch of theirs matters and they take the run's
    period, rather than refusing the whole run."""
    from _helpers import two_net_dspf
    t = np.linspace(0.0, 8 * T, 8 * 2000 + 1)
    i = [pulse(t, ph) for ph in PHASES]
    sigs = {f"Rm{n}:1": sum(i[(n - 1) // 3:]) for n in range(1, 13)}
    sigs.update({f"Rc{k}:1": i[k] for k in range(4)})
    sigs.update({f"{r}:1": 1e-15 * np.sin(t / T) for r in ("Rs1", "Rs2", "Rs3", "Rs4",
                                                           "Ro1", "Ro2")})
    raw = _psf_tran(os.path.join(str(tmp_path), "all.tran"), t, sigs)
    p = emir("run", two_net_dspf(tmp_path), "--process", PROC, "--cc-sim", raw,
             "--period", "1n", "--format", "json")
    assert p.returncode == 0, p.stdout[-2000:] + p.stderr[-2000:]
    wins = {n["net"]: n["window"] for n in json.loads(p.stdout)["nets"]}
    assert set(wins) == {"VDD", "VSS", "OUT"}
    for net, w in wins.items():
        assert w["period_s"] == T and w["n_periods"] == 3
        assert w["branches"] == (16 if net == "VDD" else 0)


# ---------------------------------------------------------------- emir next


def _plan(**kw):
    return P.build(P.Facts(dspf=DSPF, process=PROC, have_xyce=True, have_ngspice=False,
                           **kw))


def test_emir_next_asks_for_the_period_of_a_simulated_run(tmp_path):
    raw = staggered(tmp_path)
    plan = _plan(cc_sim=raw, net="VDD")
    why = next(m["why"] for m in plan.missing if "period" in m["what"])
    # a transient without --period: refused, so no run is planned until it is given
    assert "EMIR-E428" in why and not plan.has("run") and not plan.has("check")
    plan = _plan(cc_sim=raw, net="VDD", period=T)
    assert not any("period" in m["what"] for m in plan.missing) and plan.has("run")
    dc = json.dumps({"VDD": 1e-3, "MP3:s": -1e-3})
    assert not any("period" in m["what"] for m in _plan(currents=dc, net="VDD").missing)


def test_emir_next_plans_the_fix_of_a_refusal(tmp_path):
    # the last two periods differ: ask the person whether the period is the
    # block's, and plan the longer simulation in case it is (a start-up)
    plan = _plan(cc_sim=staggered(tmp_path), net="VDD", period=T / 4)
    need = next(m for m in plan.missing if "EMIR-E429" in m["why"])
    assert "confirmed by the person" in need["what"] and "starting up" in need["how"]
    sim = next(s for s in plan.steps if s["id"] == "simulate")
    assert sim["needs_human"] and "twice as long" in sim["why"]
    # too short: the longer simulation, then the run on its result
    t = np.linspace(0.0, 1.5 * T, 3001)
    short = rail(tmp_path, "short.tran", t, [pulse(t, ph) for ph in PHASES])
    plan = _plan(cc_sim=short, net="VDD", period=T)
    assert any(m["what"].startswith("a longer simulation") for m in plan.missing)
    sim = next(s for s in plan.steps if s["id"] == "simulate")
    assert "2 periods" in sim["why"] and sim["needs_human"]
    run = next(s for s in plan.steps if s["id"] == "run")
    assert "--period 1e-09" in run["command"]


def test_emir_next_plans_the_longer_simulation_through_emir_simulate(tmp_path):
    """Review of ed6504d: the plan for a longer simulation ran Xyce directly
    and asked for a names sidecar written by hand, though `emir simulate`
    writes it; its text spoke of save lines it had not planned and said
    'over whole periods' twice."""
    t = np.linspace(0.0, 1.5 * T, 3001)
    short = rail(tmp_path, "short.tran", t, [pulse(t, ph) for ph in PHASES])
    plan = _plan(cc_sim=short, net="VDD", period=T)
    ids = [s["id"] for s in plan.steps]
    assert "names-sidecar" not in ids
    sim = next(s for s in plan.steps if s["id"] == "simulate")
    assert sim["command"].startswith("emir simulate ") and "--sim xyce" in sim["command"]
    assert "save lines above" not in sim["why"] and sim["why"].count("whole periods") <= 1
    run = next(s for s in plan.steps if s["id"] == "run")
    assert "emir_out/sim/result.raw" in run["command"]


def test_emir_next_plans_from_a_refused_run_s_error(tmp_path):
    t = np.linspace(0.0, 1.5 * T, 3001)
    short = rail(tmp_path, "short.tran", t, [pulse(t, ph) for ph in PHASES])
    out = tmp_path / "s.json"
    p = emir("run", DSPF, "--process", PROC, "--net", "VDD", "--cc-sim", short,
             "--period", "1e-9", "--summary-out", out)
    assert p.returncode == 2
    p = emir("next", "--run-summary", out, "--have-xyce", "--format", "json")
    assert p.returncode == 0, p.stdout + p.stderr
    d = json.loads(p.stdout)
    assert any(m["what"].startswith("a longer simulation") for m in d["missing"])
    assert [s["id"] for s in d["steps"]][-3:] == ["check", "run", "report"]
    assert any("refused (EMIR-E431)" in a for a in d["advice"])
    # a run refused for want of a period: the plan asks for it and plans no run
    p = emir("run", DSPF, "--process", PROC, "--net", "VDD", "--cc-sim",
             like_example_08(tmp_path), "--summary-out", out)
    assert p.returncode == 2
    p = emir("next", "--run-summary", out, "--have-xyce", "--format", "json")
    d = json.loads(p.stdout)
    assert any("period" in m["what"] for m in d["missing"])
    assert not [s for s in d["steps"] if s["id"] in ("check", "run")]


def test_emir_check_refuses_what_the_run_refuses(tmp_path):
    """`emir check` takes the window as the run does: E428, E429 and E431 FAIL
    there with the run's own code."""
    raw = staggered(tmp_path)
    p = emir("check", DSPF, "--process", PROC, "--net", "VDD", "--cc-sim", raw,
             "--format", "json")
    d = json.loads(p.stdout)
    assert p.returncode == 2 and any(f["code"] == "EMIR-E428" and f["level"] == "FAIL"
                                     for f in d["findings"])
    p = emir("check", DSPF, "--process", PROC, "--net", "VDD", "--cc-sim", raw,
             "--period", "2.5e-10", "--format", "json")
    assert p.returncode == 2
    assert any(f["code"] == "EMIR-E429" for f in json.loads(p.stdout)["findings"])
    p = emir("check", DSPF, "--process", PROC, "--net", "VDD", "--cc-sim", raw,
             "--period", "1n", "--format", "json")
    assert p.returncode == 0, p.stdout


# ---------------------------------------------------------------- front ends


def test_the_virtuoso_form_gives_the_period():
    """Review of ed6504d: the EMIR form had no Period field, so a result
    refused for want of one (EMIR-E428) could not be screened from the GUI."""
    from emir.skillgui import MAIN
    assert "emirMainPeriod" in MAIN and '"--period" per' in MAIN
    args = MAIN[MAIN.index("procedure( emirMainArgs()"):]
    args = args[:args.index("procedure( emirWriteArgs")]
    assert "emirMainPeriod" in args


def test_over_mcp_a_refused_run_says_what_to_give(tmp_path):
    from emir import mcp_tools
    run = mcp_tools.specs()["emir_run"]
    assert "EMIR-E428" in run.description and "period" in run.description
    period = {p.name: p for p in run.params}["period"]
    assert "needed for a transient result" in period.schema()["description"]
    rc, doc, _ = mcp_tools.call(run, {"dspf": DSPF, "process": PROC, "net": "VDD",
                                      "cc_sim": like_example_08(tmp_path)})
    assert rc == 2 and doc["code"] == "EMIR-E428" and "worst_items" not in json.dumps(doc)
    cmd = doc["next_steps"][0]["command"]
    # every input the run was given, so the plan does not ask for them again
    assert cmd.startswith("emir next ") and "--process" in cmd and "--net VDD" in cmd


def test_the_contract_is_one_sentence_everywhere():
    """The decision's contract, stated the same wherever it is stated (review
    of 93da011: the report, the open-source README, docs/simulators.md and the
    Spectre template said it in other words; check's, next's and the MCP
    tools' --period were not held to it)."""
    from _helpers import ROOT

    from emir import RCC_CONTRACT, mcp_tools
    sentence = ("rcc is exact for the simulated record over the whole periods you give "
                "(--period); whether the testbench has settled is yours to establish, as "
                "for any .meas.")
    assert RCC_CONTRACT == sentence
    for rel in ("docs/method.md", "agent/playbooks/01-screen-a-block.md", "AGENTS.md",
                "agent/skills/emir/SKILL.md", "blog/linkedin-article.md", "docs/simulators.md",
                "agent/opensource/README.md", "agent/cadence/spectre/tb_rcc.scs.tmpl",
                "examples/08_open_layout/README.md"):
        text = open(os.path.join(ROOT, rel), encoding="utf-8").read().replace("//", " ")
        assert sentence in " ".join(text.split()), rel
    from emir.dspf import build_parser
    help_ = next(a.help for a in build_parser()._actions if "--period" in a.option_strings)
    assert sentence[:-1] in " ".join(help_.split())
    specs = mcp_tools.specs()
    assert sentence in " ".join(specs["emir_run"].description.split())
    for tool in ("emir_run", "emir_check", "emir_next"):
        desc = {p.name: p for p in specs[tool].params}["period"].schema()["description"]
        assert sentence[:-1] in " ".join(desc.split()), tool


# ------------------------------------------------- review of 93da011


def nut_plots(path, plots) -> str:
    """A nutascii file of several plots, as ngspice writes `.op` and `.tran`
    with savecurrents: `plots` is [(title, [(name, type, values)])], the
    first variable the scale when it is time (an operating point has none)."""
    L = ["Title: synthetic", "Date: today"]
    for title, cols in plots:
        n = len(cols[0][2])
        L += [f"Plotname: {title}", "Flags: real", f"No. Variables: {len(cols)}",
              f"No. Points: {n}", "Variables:"]
        L += [f"\t{k}\t{name}\t{typ}" for k, (name, typ, _) in enumerate(cols)]
        L.append("Values:")
        for i in range(n):
            L.append(f" {i}\t{cols[0][2][i]:.15e}")
            L += [f"\t{c[2][i]:.15e}" for c in cols[1:]]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    return str(path)


def rail_sigs(t, i) -> dict:
    sigs = {f"Rm{n}:1": sum(i[(n - 1) // 3:]) for n in range(1, 13)}
    sigs.update({f"Rc{k}:1": i[k] for k in range(4)})
    return sigs


def test_an_operating_point_plot_before_the_transient_is_not_the_result(tmp_path):
    """Review of 93da011: an ngspice file with `.op` before `.tran` was read
    as an operating point (its first voltage taken as the time, its first
    signal dropped): no --period was asked for, and a rail the transient
    FAILs was CLEAN. The transient is the result; the operating point is
    read only when asked for, with every signal."""
    from emir import waves
    t = np.linspace(0.0, 4 * T, 4 * 400 + 1)
    tran = rail_sigs(t, [pulse(t, ph) for ph in PHASES])
    op = {k: np.full(1, 1e-4 * (j + 1)) for j, k in enumerate(tran)}
    raw = nut_plots(tmp_path / "opt.raw", [
        ("Operating Point", [("v(vdd)", "voltage", np.full(1, 1.8))]
         + [(k, "current", v) for k, v in op.items()]),
        ("Transient Analysis", [("time", "time", t)]
         + [(k, "current", v) for k, v in tran.items()])])
    w = waves.read(raw)
    assert w.kind == "tran" and len(w.x) == len(t) and set(tran) <= set(w.data)
    for name in ("tran", "TRAN", "transient"):
        assert len(waves.read(raw, analysis=name).x) == len(t)
    o = waves.read(raw, analysis="op")
    assert o.kind == "op" and len(o.x) == 1 and "v(vdd)" in o.data
    assert o.data["Rm1:1"][0] == pytest.approx(1e-4)
    assert waves.read(raw, analysis="sweep:2").kind == "tran"
    with pytest.raises(EmirError) as e:
        waves.read(raw, analysis="AC")
    assert e.value.code == "EMIR-E412" and "Operating Point" in e.value.message
    # the run reads the transient: no period, refused; with it, the numbers
    p, d = run_json("--cc-sim", raw)
    assert p.returncode == 2 and d["code"] == "EMIR-E428"
    p, d = run_json("--cc-sim", raw, "--period", "1n")
    assert p.returncode == 0 and d["nets"][0]["window"]["n_periods"] == 3
    assert not any("period" in m["what"] for m in _plan(cc_sim=raw, net="VDD",
                                                       period=T).missing)
    assert any("period" in m["what"] for m in _plan(cc_sim=raw, net="VDD").missing)


def test_a_result_of_several_transients_or_a_dc_sweep_is_not_guessed(tmp_path):
    from emir import waves
    # --analysis without regard to case, in a result directory too
    d = tmp_path / "raw"
    d.mkdir()
    t0 = np.linspace(0.0, 2 * T, 11)
    _psf_tran(str(d / "tran.tran"), t0, {"Rm1:1": np.full(len(t0), 1e-3)})
    assert len(waves.read(str(d), analysis="TRAN").x) == len(t0)
    t = np.linspace(0.0, 2 * T, 11)
    col = [("Rm1:1", "current", np.full(len(t), 1e-3))]
    two = nut_plots(tmp_path / "two.raw", [("Transient Analysis", [("time", "time", t)] + col),
                                           ("Transient Analysis", [("time", "time", t)] + col)])
    with pytest.raises(EmirError) as e:
        waves.read(two)
    assert e.value.code == "EMIR-E465" and "sweep:2" in e.value.message
    assert waves.read(two, analysis="sweep:2").kind == "tran"
    dc = nut_plots(tmp_path / "dc.raw", [("DC transfer characteristic",
                                          [("v-sweep", "voltage", np.linspace(0, 1, 11))]
                                          + col)])
    with pytest.raises(EmirError) as e:
        waves.read(dc)
    assert e.value.code == "EMIR-E467" and "v-sweep" in e.value.message


def test_a_record_a_sliver_short_of_whole_periods_holds_one_fewer(tmp_path):
    """Review of 93da011: a tolerance of 1e-4 of a period counted a record
    of 2.99995 periods as three, and averaged over all of it: a load firing
    late in each period read 33 % low, labelled as three whole periods."""
    P, ps = 100e-9, 1e-12
    g = np.concatenate([np.linspace(0, P - 10 * ps, 1001), P - np.array([5, 4, 3, 2, 1]) * ps])
    t = np.concatenate([k * P + g for k in range(3)])
    t = t[t <= 3 * P - 5 * ps]
    i = np.where((t % P >= P - 4 * ps - 1e-18) & (t % P <= P - 2 * ps + 1e-18), 1e-3, 0.0)
    win = waveform.window(t, i[None, :], period=P, names=["load"], **QUIET)
    assert win["n_periods"] == 2 and win["t1"] - win["t0"] == pytest.approx(2 * P, rel=1e-12)
    m = waveform.metrics_tran(t, i, win["t0"], win["t1"])
    assert m["avg"] == pytest.approx(3e-8, rel=1e-9)        # 1 mA for 3 ps a period
    with pytest.raises(EmirError) as e:
        waveform.record_window(t[t <= 2 * P - 5 * ps], P)
    assert e.value.code == "EMIR-E431" and e.value.details["periods_in_record"] == 1
    # a record that ends on its last period to float rounding keeps it
    assert waveform.whole(3 * P * (1 - 1e-15), P) == 3


def test_a_small_tap_on_a_net_of_large_ac_currents_is_held_to_itself():
    """Review of 93da011: each change was held to 1 % of the net's largest
    rms, so on a net fed 1 mA + 50 mA of sine a DC tap of 2 % of the feed's
    average (it matters) moved 20 % between the last two periods with no
    EMIR-E429. A change is held to 10 % of the branch's own rms."""
    t = np.linspace(0.0, 4 * T, 4 * 1000 + 1)
    feed = 1e-3 + 50e-3 * np.sin(2 * np.pi * t / T)
    tap = np.where(t > 3 * T, 0.024e-3, 0.02e-3)
    assert waveform.matters(*waveform.last_periods(t, np.vstack([feed, tap]), T))[1]
    with pytest.raises(EmirError) as e:
        waveform.window(t, np.vstack([feed, tap]), period=T, names=["feed", "tap"], **QUIET)
    assert e.value.code == "EMIR-E429" and e.value.details["branch"] == "tap"
    assert e.value.details["change"] == pytest.approx(0.2 / 1.2, rel=1e-6)


def test_dynamic_ir_is_not_a_number_when_a_current_is_not(tmp_path):
    """Review of 93da011: with --force past EMIR-E426, a NaN branch's nodes
    were skipped and a smaller worst printed as the net's dynamic IR."""
    t = np.linspace(0.0, 4 * T, 4 * 400 + 1)
    sigs = rail_sigs(t, [pulse(t, ph) for ph in PHASES])
    sigs["Rc3:1"] = np.where(t > 3.5 * T, np.nan, sigs["Rc3:1"])
    raw = _psf_tran(os.path.join(str(tmp_path), "nan.tran"), t, sigs)
    with collecting():
        r = check(raw, period=T)
    assert r["dynamic"] is None and np.isnan(r["ir_drop"])
    p, d = run_json("--cc-sim", raw, "--period", "1n", "--force")
    assert p.returncode == 0 and d["nets"][0]["dynamic_mV"] is None


def test_a_window_from_t_zero_reads_zero_seconds():
    from emir.report import window_text
    line = window_text({"nets": ["VDD"], "window": {
        "n_periods": 2, "period_s": 1.5e-8, "t0_s": 0.0, "t1_s": 3e-8, "change": 0.0}})
    assert "0 s to 30 ns" in line and "fs" not in line


def test_a_refusal_s_next_step_keeps_every_input_of_the_run(tmp_path):
    """Review of 93da011: a run refused by its input check printed `emir
    next --dspf D --cc-sim R`, dropping --process, --net and --sim, so the
    plan it led to asked for a process file the person had already given."""
    raw = staggered(tmp_path)
    for extra in ([], ["--period", "2.5e-10"], ["--period", "1e-6"]):
        for check_ in ([], ["--no-check"]):
            p, d = run_json("--cc-sim", raw, "--sim", "spectre", *extra, *check_)
            assert p.returncode == 2 and d["code"] in waveform.REFUSALS, (extra, d)
            cmd = d["next_steps"][0]["command"]
            for opt in ("--process", "--net VDD", "--cc-sim", "--sim spectre"):
                assert opt in cmd, (extra, check_, cmd)
    p = emir("check", DSPF, "--process", PROC, "--net", "VDD", "--cc-sim", raw,
             "--format", "json")
    cmd = json.loads(p.stdout)["next_steps"][0]["command"]
    assert "--process" in cmd and "--net VDD" in cmd


def test_a_netlist_run_without_a_period_simulates_nothing(tmp_path):
    """Review of 93da011: `emir run --netlist --run` without --period ran
    Spectre, then refused the result (EMIR-E428); `emir next --netlist`
    planned that run, and the open-source flow script simulated first."""
    import subprocess
    import sys
    tb = tmp_path / "tb.scs"
    tb.write_text("// a testbench\n")
    ran = tmp_path / "spectre_ran"
    spectre = tmp_path / "spectre"
    spectre.write_text(f"#!{sys.executable}\nopen({str(ran)!r}, 'w').close()\n")
    spectre.chmod(0o755)
    env = {**os.environ, "SPECTRE_BIN": str(spectre)}
    for extra in (["--run"], [], ["--run", "--no-check"]):
        p = emir("run", DSPF, "--process", PROC, "--net", "VDD", "--netlist", tb, *extra,
                 "--format", "json", env=env)
        d = json.loads(p.stdout)
        assert p.returncode == 2 and d["code"] == "EMIR-E428", d
        assert "Nothing was simulated" in d["message"] and not ran.exists()
        assert "--netlist" in d["next_steps"][0]["command"]
        assert "--process" in d["next_steps"][0]["command"]
    p = emir("check", DSPF, "--process", PROC, "--net", "VDD", "--netlist", str(tb),
             "--format", "json")
    assert p.returncode == 2 and any(f["code"] == "EMIR-E428" for f in
                                     json.loads(p.stdout)["findings"])
    from _helpers import ROOT
    flow = os.path.join(ROOT, "agent", "opensource", "run_xyce.sh")
    p = subprocess.run(["sh", flow, "--out", str(tmp_path / "o"), str(tb), DSPF, PROC],
                       capture_output=True, text=True)
    assert p.returncode == 2 and "EMIR-E428" in p.stderr
    assert not (tmp_path / "o").exists()
