"""Where the waveform loop meets the other features: the decaps, the droop
file, the simulator's processes, the plan, the report and the chart. No
simulator: stand-ins and closed forms.

* the loop feeds back the decaps --dynamic sizes (each MOS decap behind its
  access resistance), not the DSPF's plain capacitors alone: on a single node
  fed through 100 ohm, with a 20 pF decap behind 50 ohm and 1 pF of wire
  capacitance, the droop fed back is the closed form of that RC network
  (the grid without the decap reads 57 % deeper);
* a decap the process file cannot size is left out with EMIR-W547; a decap
  whose device the testbench simulates (its pin fed, its current in the
  result) is not added a second time.
"""
from __future__ import annotations

import os
import re

import numpy as np
import pytest
import yaml
from _helpers import PROC, write

from emir import tdloop, waves
from emir.errors import collecting
from emir.validate import RC_DSPF

# ------------------------------------------------------------------ decaps fed back

#: the validation suite's R || C node (100 ohm feed, 1 pF at MA:s) and a MOS
#: decap whose gate is MA:s and whose d/s/b are on VSS (a port, no resistance:
#: an ideal far plate): C = cox W L = 1 F/m^2 x 100 um x 0.2 um = 20 pF
R, CW, CD, IDC, IAC = 100.0, 1e-12, 20e-12, 1e-3, 0.8e-3
DECAP = RC_DSPF.replace(".ENDS\n.END\n", """*|NET VSS 0.0
*|P (VSS I *1 0.0000 -5.0000)
*|I (MD:d MD d I 0 10.0000 -5.0000)
*|I (MD:s MD s I 0 10.2000 -5.0000)
MD MD:d MA:s MD:s VSS ncap_demo L=2e-07 W=1e-04
.ENDS
.END
""")
#: the same decap with its gate an instance pin of VDD (MD:g, 1 ohm from MA:s):
#: a testbench that simulates the decap feeds that pin and reads its current
DECAP_PIN = DECAP.replace("*|I (MA:s MA s I 0 10.0000 0.0000)\n",
                          "*|I (MA:s MA s I 0 10.0000 0.0000)\n"
                          "*|I (MD:g MD g I 0 10.0000 1.0000)\n").replace(
    "CL MA:s 0 1.0e-12\n",
    "CL MA:s 0 1.0e-12\nRg MA:s MD:g 1.000000 $metal1 $L=1.0 $W=1.000000 $LLX=9.5 "
    "$LLY=0.0 $URX=10.5 $URY=1.0 $lvl=1\n").replace("MD MD:d MA:s MD:s", "MD MD:d MD:g MD:s")


def _proc(tmp, esr=50.0, models=True):
    with open(PROC, encoding="utf-8") as f:
        d = yaml.safe_load(f)
    if models:
        d["cap_models"] = {"ncap_demo": {"cox": 1.0, "esr_a": esr}}
    from emir.engine import Process
    return Process.load(write(os.path.join(str(tmp), "proc.yaml"), yaml.safe_dump(d)))


def _cosine_loads(w, t, extra=None):
    """A stand-in simulator: MA:s draws I_dc + I_ac cos(w t) whatever its
    supply (a current source), `extra` {pin: current} beside it."""
    def sim(netlist, k, fed):
        data = {"MA:s": IDC + IAC * np.cos(w * t)}
        data.update(extra or {})
        return {"waves": waves.Waves(t, data, name="cos", kind="tran"), "seconds": 0.0}
    return sim


def _loop(tmp, text, proc, extra=None, periods=6, per=200):
    from emir.dspf import parse
    d = parse(write(os.path.join(str(tmp), "dec.dspf"), text))
    w = 1.0 / (R * (CW + CD))                    # w R C_tot = 1
    T = 2 * np.pi / w
    t = np.linspace(0.0, periods * T, periods * per + 1)
    with collecting() as notes:
        out = tdloop.run(d, ["VDD"], proc, {"VDD": "VDD"}, {"VDD": 1.2},
                         simulate=_cosine_loads(w, t, extra), period=T, max_passes=3,
                         tol_mV=1e-9, tol_rel=1e-9, log=lambda *a: None)
    return out, [n["code"] for n in notes], w, T


def _settled_amplitude(cv, T):
    """Half the swing of the droop fed to MA:s over its last period."""
    tk, F = cv["fed"]
    v = F[0]                                  # MA:s, the first pin by name
    sel = tk >= tk[-1] - T
    return 0.5 * float(v[sel].max() - v[sel].min())


def _closed_form(w, decap=True, esr=50.0):
    """|Z| I_ac at MA:s: R || 1/(jw C_w) [|| (R_e + 1/(jw C_d))]."""
    y = 1 / R + 1j * w * CW + ((1 / (esr + 1 / (1j * w * CD))) if decap else 0.0)
    return abs(1 / y) * IAC


def test_the_loop_feeds_back_the_decap_behind_its_access_resistance(tmp_path):
    out, codes, w, T = _loop(tmp_path, DECAP, _proc(tmp_path))
    cv = out["nets"]["VDD"]
    amp = _settled_amplitude(cv, T)
    want = _closed_form(w)
    # without the decap the grid's droop is far deeper: the test tells them apart
    assert _closed_form(w, decap=False) > 1.5 * want
    # backward Euler at 400 steps a period: within 1 % of the closed form
    assert amp == pytest.approx(want, rel=1e-2)
    fb = cv["feedback"]
    assert fb["grid"] == "RC" and fb["caps"] == 1 and fb["decaps"] == 1
    assert fb["decaps_F"] == pytest.approx(CD) and fb["unsized_decaps"] == 0
    assert fb["simulated_decaps"] == 0 and "EMIR-W547" not in codes


def test_a_decap_the_process_file_cannot_size_is_left_out_with_w547(tmp_path):
    out, codes, w, T = _loop(tmp_path, DECAP, _proc(tmp_path, models=False))
    cv = out["nets"]["VDD"]
    fb = cv["feedback"]
    assert fb["decaps"] == 0 and fb["unsized_decaps"] == 1 and "EMIR-W547" in codes
    assert _settled_amplitude(cv, T) == pytest.approx(_closed_form(w, decap=False),
                                                       rel=1e-2)


def test_a_decap_the_testbench_simulates_is_not_added_twice(tmp_path):
    """MD's gate is an instance pin the split feeds: with its current in the
    result it loads the grid through that pin, and no lump is added for it;
    without it in the result (a testbench that leaves the decap out) the
    lump stands in for it."""
    w = 1.0 / (R * (CW + CD))
    T = 2 * np.pi / w
    t = np.linspace(0.0, 6 * T, 6 * 200 + 1)
    out, codes, _w, _T = _loop(tmp_path, DECAP_PIN, _proc(tmp_path),
                               extra={"MD:g": np.zeros_like(t)})
    fb = out["nets"]["VDD"]["feedback"]
    assert fb["decaps"] == 0 and fb["simulated_decaps"] == 1
    assert out["nets"]["VDD"]["simulated_decaps"] == ["MD"]
    out, codes, _w, _T = _loop(tmp_path, DECAP_PIN, _proc(tmp_path))
    fb = out["nets"]["VDD"]["feedback"]
    assert fb["decaps"] == 1 and fb["simulated_decaps"] == 0


def test_decap_aware_em_after_the_loop_does_not_add_a_simulated_decap_again(tmp_path):
    """The run hands the loop's simulated decaps to the dynamic solve
    (`_loop_simulated_decaps`): their charge is in their pins' currents, so
    decap-aware EM adds no lump for them (it did, counting it twice)."""
    from emir import ccsim, decap
    from emir.dspf import build_parser, check_net, parse
    from emir.validate import _psf_tran
    path = write(tmp_path / "dec.dspf", DECAP_PIN)
    d, proc = parse(path), _proc(tmp_path)
    w = 1.0 / (R * (CW + CD))
    T = 2 * np.pi / w
    t = np.linspace(0, 4 * T, 801)
    res = _psf_tran(str(tmp_path / "cos.tran"), t,
                    {"MA:s": IDC + IAC * np.cos(w * t), "MD:g": 0 * t})
    ing = ccsim.cc_taps(d, "VDD", res, period=T, feed="VDD", waveforms=True,
                        log=lambda *a: None)
    inj = {}
    for tp in ing["taps"]:
        inj[tp["node"]] = inj.get(tp["node"], 0.0) + tp["i_avg"]
    a = build_parser().parse_args([path, "--dynamic", "--em-basis", "decap-aware",
                                   "--mode", "cc-grid", "--cc-sim", res])
    seen = {}
    for sim in ([], ["MD"]):
        r = check_net(d, "VDD", proc, inj, "VDD", taps_list=ing["taps"],
                      tap_weights=ing["weights"], log=lambda *a: None)
        r["_loop_simulated_decaps"] = sim
        with collecting():
            dyn = decap.solve(d, "VDD", r, "VDD", ing["taps"], ing["time"], proc, a,
                              log=lambda *a: None)
        seen[bool(sim)] = (r["dynamic_em"]["decaps"]["n"], dyn["decap_note"])
    assert seen[False][0] == 1
    assert seen[True][0] == 0 and "simulated by the self-consistent loop" in seen[True][1]


# ------------------------------------------------------------------ --droop-file

EX12 = os.path.join(os.path.dirname(PROC), "..", "..", "..", "examples", "12_self_consistent")

def test_a_capacitive_pins_average_does_not_keep_the_loop_from_settling():
    """A decap the testbench simulates draws about no average current, and
    neither does its strap: their relative moves were noise (27 % to 76 %
    between passes whose answer moved under 0.01 %), so on a 4 % droop mesh
    the loop never settled, stopped at its pass limit even at 16 passes and
    reported the open loop (examples/13_decap_aware/results/decap_loop.md).
    A metric far below the largest of its kind moves relative to
    DEVICE_FLOOR of the largest; a real move still counts."""
    from types import SimpleNamespace
    nw = SimpleNamespace(res=[{"name": "R0"}, {"name": "Rsd0"}])

    def res(strap_avg, peak=30e-3):
        return {"network": nw, "clusters": [{"name": "R0"}, {"name": "Rsd0"}],
                "Ie": {"avg": np.array([10e-3, strap_avg]), "rms": np.array([12e-3, 3e-3]),
                       "peak": np.array([peak, 9e-3])}}

    def ing(dec_avg, peak=5e-3):
        return {"taps": [{"name": "MP0:s", "i_avg": 1e-3, "i_rms": 2e-3, "i_peak": peak},
                         {"name": "MDC0:g", "i_avg": dec_avg, "i_rms": 1e-3,
                          "i_peak": 4e-3}]}
    mv = tdloop._metrics_move(res(1e-9), res(-2e-9), ing(1e-9), ing(3e-9))
    assert mv["items"] < tdloop.METRIC_TOL and mv["devices"] < tdloop.METRIC_TOL, mv
    assert tdloop._all_still(mv)
    mv = tdloop._metrics_move(res(1e-9, peak=31e-3), res(1e-9), ing(1e-9, peak=5.2e-3),
                              ing(1e-9))
    assert mv["peak"] > tdloop.METRIC_TOL and mv["devices"] > tdloop.METRIC_TOL
    assert not tdloop._all_still(mv)



@pytest.mark.parametrize("target", ["tb_cc.cir", "biasrail.dspf", "mine.inc"])
def test_droop_file_never_overwrites_a_file_emir_did_not_write(tmp_path, target):
    """--droop-file naming the testbench, the DSPF or any file of the person's
    is refused (EMIR-E526) before the split is written, and the file is
    kept; a droop file emir wrote is replaced, as at the default place."""
    import shutil

    from emir import selfconsist
    from emir.dspf import parse
    from emir.errors import EmirError
    for f in ("tb_cc.cir", "loads.sp", "cc.sp", "biasrail.dspf"):
        shutil.copy(os.path.join(EX12, f), tmp_path / f)
    (tmp_path / "mine.inc").write_text("* my precious include\n")
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    d = parse(str(tmp_path / "biasrail.dspf"))
    with pytest.raises(EmirError) as e:
        selfconsist.SpiceDriver("xyce", str(tmp_path / "tb_cc.cir"), d, "VDD", 1.2,
                                str(tmp_path), droop_path=str(tmp_path / target))
    assert e.value.code == "EMIR-E526" and target in e.value.message
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before
    # Spectre: the manifest --droop-file names is guarded the same way
    with pytest.raises(EmirError) as e:
        selfconsist.SpectreDriver(str(tmp_path / "tb_cc.cir"), str(tmp_path / target),
                                  ["MP0:s"], guard=True)
    assert e.value.code == "EMIR-E526"
    # emir's own droop file there is replaced
    out = tmp_path / "sc"
    out.mkdir()
    for _ in range(2):
        drv = selfconsist.SpiceDriver("xyce", str(tmp_path / "tb_cc.cir"), d, "VDD", 1.2,
                                      str(out), droop_path=str(out / "droop.inc"))
    assert selfconsist.HEADER in (out / "droop.inc").read_text().splitlines()[0]
    assert os.path.isfile(drv.deck)


def test_the_run_passes_the_guard_to_spectre_with_a_droop_file(tmp_path):
    from types import SimpleNamespace

    from emir import dspf as D
    seen = {}

    class Drv:
        def __init__(self, *a, **k):
            seen.update(k)
    import emir.selfconsist as sc
    orig = sc.SpectreDriver
    sc.SpectreDriver = Drv
    try:
        from _helpers import spectre_split_netlist
        dsp = write(tmp_path / "rail.dspf", open(os.path.join(EX12, "biasrail.dspf")).read())
        droop = str(tmp_path / "droop.scs")
        nl = spectre_split_netlist(tmp_path / "cc.scs", dsp, "VDD", droop)
        a = SimpleNamespace(sim="spectre", droop_file=droop, netlist=nl, spectre=None,
                            sim_timeout=None, vdd=1.2, save_prefix=None)
        D._sc_driver(a, D.parse(dsp), "VDD")
    finally:
        sc.SpectreDriver = orig
    assert seen["guard"] is True


# ------------------------------------------------------------------ the simulator's processes


def _wrapper(tmp_path, name="Xyce"):
    """A stand-in for a site's simulator wrapper: it starts the 'simulator'
    (a sleep) as its child, writes the child's pid, and waits for it."""
    import stat
    exe = tmp_path / name
    exe.write_text("#!/bin/sh\nsleep 60 &\necho $! > \"$(dirname \"$0\")/child.pid\"\n"
                   "wait\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return exe


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # a zombie is reaped by init: read its state
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as f:
            return f.read().split()[2] != "Z"
    except OSError:
        return True


@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX")
def test_a_simulator_stopped_at_its_time_limit_takes_its_children_with_it(tmp_path):
    """EMIR-E605 on a wrapper whose simulator is its child: the child is
    stopped too (it ran on after the run reported pass 0, breaking the
    one-simulation-at-a-time rule)."""
    import time

    from emir import opensim
    from emir.errors import EmirError
    exe = _wrapper(tmp_path)
    (tmp_path / "d.cir").write_text("* deck\n.tran 1n 1n\n.end\n")
    with pytest.raises(EmirError) as e:
        opensim.run_simulator("xyce", str(exe), str(tmp_path / "d.cir"),
                              str(tmp_path / "r.raw"), 1.0, log=lambda *a: None,
                              option="--sim-timeout")
    assert e.value.code == "EMIR-E605"
    pid = int((tmp_path / "child.pid").read_text())
    for _ in range(50):
        if not _alive(pid):
            break
        time.sleep(0.1)
    alive = _alive(pid)
    if alive:
        os.kill(pid, 9)
    assert not alive


@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX")
def test_spectre_stopped_at_its_time_limit_takes_its_children_with_it(tmp_path):
    import time

    from emir import ccsim
    from emir.errors import EmirError
    exe = _wrapper(tmp_path, "spectre")
    (tmp_path / "cc.scs").write_text("// netlist\n")
    with pytest.raises(EmirError) as e:
        ccsim.run_spectre(str(tmp_path / "cc.scs"), spectre=str(exe), timeout=1.0,
                          log=lambda *a: None)
    assert e.value.code == "EMIR-E605"
    pid = int((tmp_path / "child.pid").read_text())
    for _ in range(50):
        if not _alive(pid):
            break
        time.sleep(0.1)
    alive = _alive(pid)
    if alive:
        os.kill(pid, 9)
    assert not alive


# ------------------------------------------------------------------ an earlier run's passes


def test_a_shorter_rerun_removes_the_earlier_runs_later_passes(tmp_path):
    """A 4-pass run left pass0..pass3; a rerun that stops after 2 passes
    rewrote pass0 and pass1 and left pass2 and pass3 beside them, stamped
    'emir replaces them on its next run'. Now pass 0 removes emir's own
    later passes first; a file emir did not stamp is kept."""
    from emir import selfconsist
    from emir.dspf import parse
    from emir.engine import Process
    d = parse(os.path.join(os.path.dirname(PROC), "rail.dspf"))
    pins = sorted(d.pin_groups("VDD"))
    out = tmp_path / "sc"
    out.mkdir()
    for k in range(4):                          # the earlier run's
        r = out / f"pass{k}.raw"
        selfconsist.claim(str(r), k)
        for f in (r, out / f"pass{k}.raw.log", out / f"pass{k}.raw.names"):
            f.write_text("earlier run\n")
    (out / "pass7.raw").write_text("not emir's\n")
    t = np.linspace(0, 1e-9, 21)

    class Driver:
        sim = "xyce"

        def pins(self, net):
            return pins

        def kcl_source(self, net):
            return None

        def pass_result(self, k):
            return str(out / f"pass{k}.raw")

        def run(self, k, fed, timeout=None):
            selfconsist.claim(self.pass_result(k), k)
            (out / f"pass{k}.raw").write_text("this run\n")
            data = {}
            for p in fed["v"]:
                v = 0.0 if fed["t"] is None else np.interp(t, fed["t"], fed["v"][p])
                data[p] = np.full_like(t, 1.2 / 1200.0) + v / 1200.0
            return {"waves": waves.Waves(t, data, name="dc", kind="tran"), "seconds": 0.0,
                    "raw": self.pass_result(k)}
    with collecting():
        cv = selfconsist.converge(None, str(out / "droop.inc"), d, "VDD",
                                  Process.load(PROC), "VDD", 1.2, driver=Driver(),
                                  period=1e-9, max_passes=2, tol_mV=1e-12, tol_rel=0.0,
                                  log=lambda *a: None)
    assert len(cv["passes"]) == 2
    left = sorted(os.listdir(out))
    assert "pass2.raw" not in left and "pass3.raw" not in left
    assert "pass3.raw.emir" not in left and "pass2.raw.log" not in left
    assert (out / "pass0.raw").read_text() == "this run\n"
    assert (out / "pass1.raw").read_text() == "this run\n"
    assert (out / "pass7.raw").read_text() == "not emir's\n"


# ------------------------------------------------------------------ EMIR-W543's words


def _rail_loop(sim, **kw):
    from emir.dspf import parse
    from emir.engine import Process
    d = parse(os.path.join(os.path.dirname(PROC), "rail.dspf"))
    with collecting() as notes:
        out = tdloop.run(d, ["VDD"], Process.load(PROC), {"VDD": "VDD"}, {"VDD": 1.2},
                         simulate=sim, period=1e-9, log=lambda *a: None, **kw)
    return out, notes


def _dc(t=np.linspace(0, 1e-9, 21), fail_at=None, error=None):
    from emir.errors import EmirError

    def sim(netlist, k, fed):
        if fail_at is not None and k >= fail_at:
            raise EmirError(*error)
        data = {}
        for p in fed["v"]:
            v = 0.0 if fed["t"] is None else np.interp(t, fed["t"], fed["v"][p])
            data[p] = np.full_like(t, 1.2 / 1200.0) + 8 * v / 1200.0
        return {"waves": waves.Waves(t, data, name="dc", kind="tran"), "seconds": 1.0}
    return sim


def test_w543_is_one_line_with_the_log_and_says_slow_only_for_a_time_limit():
    """A failed pass's simulator output (many lines, '***' and '|' rules) went
    into W543's message, and so into the report's Markdown list; and 'may
    also be just slow' was said of a simulation that failed."""
    out, notes = _rail_loop(_dc(fail_at=2, error=(
        "EMIR-E602", "xyce failed on split.xyce.cir (exit 1):\n***** error\n| a rule |\n",
        )), max_passes=10, tol_mV=1e-6, tol_rel=1e-6)
    w = [n for n in notes if n["code"] == "EMIR-W543"]
    assert len(w) == 1 and "\n" not in w[0]["message"]
    assert "EMIR-E602: xyce failed on split.xyce.cir (exit 1)" in w[0]["message"]
    assert "just slow" not in w[0]["message"]
    from emir.errors import EmirError

    def with_log(netlist, k, fed):
        if k >= 1:
            raise EmirError("EMIR-E605", "xyce ran longer than 60 s", path="/x/sc.raw.log")
        return _dc()(netlist, k, fed)
    out, notes = _rail_loop(with_log, max_passes=10, tol_mV=1e-6, tol_rel=1e-6)
    w = [n for n in notes if n["code"] == "EMIR-W543"][0]["message"]
    assert "just slow" in w and "the simulator's log: /x/sc.raw.log" in w


# ------------------------------------------------------------------ the chart


@pytest.mark.parametrize("stopped", ["residual grew twice", "droop above half the supply",
                                     "pass limit", "time limit", "simulation failed"])
def test_every_loop_alert_is_drawn_in_the_warn_colour(monkeypatch, stopped):
    """STOPPED (EMIR-W543) was drawn in the plain grey: the chart coloured
    only DIVERGING and NOT CONVERGED, while the docs promise the warn colour
    for every loop that reports pass 0."""
    pytest.importorskip("matplotlib")
    from emir import chart, scene
    sc = {"converged": False, "diverged": stopped.startswith(("residual", "droop")),
          "stopped": stopped, "n_passes": 3, "passes": [{"tolerance_mV": 0.2}]}
    _words, alerts = scene.loop_status({"VDD": {"mode": "cc-grid", "self_consistency": sc}})
    assert len(alerts) == 1
    page = chart.Page(800, 600, "light")
    calls = []
    real = page.para

    def para(x, y, s, px, width, color, *a, **k):
        calls.append((s, color))
        return real(x, y, s, px, width, color, *a, **k)
    monkeypatch.setattr(page, "para", para)
    chart.side_panel(page, 10, 10, 300, 580, {
        "verdict": "FAIL", "counts": {}, "markers": [], "items": [], "nets": [],
        "unchecked": "", "notes": [], "alerts": alerts, "warn": 0.8, "kind": "shapes",
        "shapes": []}, ("em",))
    assert dict(calls)[alerts[0]] == page.t["warn_ink"]


# ------------------------------------------------------------------ a diverging loop's report


def _loads(gain, r_load=30.0):
    """The sample rail's pins as loads drawing 1.2 V / R plus gain x droop / R:
    gain < 0, a load whose current rises as its supply falls (it diverges)."""
    t = np.linspace(0, 1e-9, 21)

    def sim(netlist, k, fed):
        data = {}
        for p in fed["v"]:
            v = 0.0 if fed["t"] is None else np.interp(t, fed["t"], fed["v"][p])
            data[p] = np.full_like(t, 1.2 / r_load) + gain * v / r_load
        return {"waves": waves.Waves(t, data, name="dc", kind="tran"), "seconds": 0.0}
    return sim


def test_a_diverging_loop_says_its_open_loop_reads_low_when_the_current_rose():
    """A load whose current rises as its supply droops makes the loop
    diverge, and is exactly where the open loop reads LOW: the report and
    W406 said 'reads high (conservative)' whatever the loop measured."""
    from emir.report import self_consistency_text
    from emir.selfconsist import summary
    out, notes = _rail_loop(_loads(gain=-3.0), max_passes=8)
    assert out["diverged"] and out["reported_pass"] == 0
    w = [n["message"] for n in notes if n["code"] == "EMIR-W406"][0]
    assert "reads LOW here, not conservative" in w
    cv = dict(out["nets"]["VDD"], v_nominal=1.2)
    sc = summary(cv)
    p = sc["passes"]
    assert abs(p[1]["total_avg_A"]) > abs(p[0]["total_avg_A"]) and p[1]["simulated_droop_mV"] > 0
    text = self_consistency_text("VDD", sc)
    assert "reads LOW, not conservative" in text and "conservative on the governing" \
        not in text


def test_a_loop_that_stopped_on_an_ordinary_load_says_the_open_loop_reads_high():
    from emir.report import self_consistency_text
    from emir.selfconsist import summary
    out, notes = _rail_loop(_loads(gain=1.0, r_load=1200.0), max_passes=2, tol_mV=1e-9,
                            tol_rel=0.0)
    assert out["stopped"] == "pass limit"
    sc = summary(dict(out["nets"]["VDD"], v_nominal=1.2))
    text = self_consistency_text("VDD", sc)
    assert "reads high here" in text and "LOW" not in text
    # no pass fed a droop back (the guard stopped it before pass 1): not measured
    sc1 = dict(sc, passes=sc["passes"][:1])
    assert "was not measured" in self_consistency_text("VDD", sc1)


# ------------------------------------------------------------------ converged at pass 0


def test_a_loop_converged_at_pass_zero_fed_nothing_back():
    """With --tol-mV above the open loop's droop the loop converges at pass 0:
    nothing was fed back, so W545 ('the loop fed back the RC grid's droop')
    does not apply, and the report says no droop was fed back."""
    from emir.report import self_consistency_text
    from emir.selfconsist import scoring_notice, summary
    out, notes = _rail_loop(_loads(gain=1.0, r_load=1200.0), max_passes=4, tol_mV=200.0)
    assert out["converged"] and out["reported_pass"] == 0
    sc = summary(dict(out["nets"]["VDD"], v_nominal=1.2))
    assert sc["feedback"]["grid"] == "RC"                 # the sample rail has capacitors
    sc["scoring"] = {"grid": "R-only", "basis": None}
    with collecting() as found:
        scoring_notice("VDD", sc, log=lambda *a: None)
    assert "EMIR-W545" not in [n["code"] for n in found]
    text = self_consistency_text("VDD", sc)
    assert "No droop was fed back" in text and "fed back was the" not in text
    # converged after feeding back: W545 does fire
    sc2 = dict(sc, reported_pass=2)
    with collecting() as found:
        scoring_notice("VDD", sc2, log=lambda *a: None)
    assert "EMIR-W545" in [n["code"] for n in found]


# ------------------------------------------------------------------ emir next, several nets


def test_next_plans_the_multi_net_loop_its_choose_mode_offers(tmp_path):
    """`--net VDD,VSS` (the route choose-mode offers: several nets in one
    loop) was 'not a net with resistors in the DSPF': no check, no run.
    `--net VDD,VDD` was refused too, though the run closes VDD once."""
    from _helpers import two_net_dspf

    from emir import plan as P
    dspf = two_net_dspf(tmp_path)
    tb = write(tmp_path / "tb.cir", "* tb\n.tran 1n 10n\n.end\n")
    kw = dict(dspf=dspf, process=PROC, mode="cc-grid", self_consistent=True, netlist=tb,
              have_xyce=True, have_cadence=False, sim="xyce")
    plan = P.build(P.Facts(net="VDD,VSS", **kw))
    ids = [s["id"] for s in plan.steps]
    assert plan.missing == [] and ids[-3:] == ["check", "run", "report"], (plan.missing, ids)
    run = plan.steps[-2]["command"]
    assert "--net VDD,VSS" in run and "--self-consistent" in run and "--vdd" not in run
    plan = P.build(P.Facts(net="VDD,VDD", vdd=1.2, **kw))
    assert plan.missing == [] and "--net VDD " in plan.steps[-2]["command"] + " "
    # an unknown one among them is named on its own
    plan = P.build(P.Facts(net="VDD,VXX", **kw))
    assert [m["what"] for m in plan.missing] == ["net 'VXX'"]
    # several nets without the loop: one net per run, asked for
    plan = P.build(P.Facts(dspf=dspf, process=PROC, net="VDD,VSS", mode="cc-grid",
                           cc_sim=str(tmp_path / "r.raw"), have_xyce=True))
    assert plan.stopped and "one net" in plan.missing[0]["what"]


# ------------------------------------------------------------------ how the branches were scored


def test_the_worst_basis_is_not_called_rc_scoring():
    """Under --dynamic's default basis, worst, a segment takes the larger of
    the R-only and the RC grid's current: it keeps the R-only grid's high
    readings (+25.3 % on minor branches at 20 fF a node), so it is not
    'scored through the RC grid ... within 0.52 %'."""
    from emir.report import self_consistency_text
    from emir.selfconsist import WORST_GRID, scoring
    t = [{"wave": np.array([0.0, 1.0, 0.0])}]
    assert scoring({"dynamic": {"worst_mV": 1.0}, "em_basis": {"used": "worst"}}, t)["grid"] == WORST_GRID
    assert scoring({"dynamic": {"worst_mV": 1.0}, "em_basis": {"used": "decap-aware"}}, t)["grid"] == "RC"
    assert scoring({"dynamic": {"worst_mV": 1.0}, "em_basis": {"used": "decap-free"}}, t)["grid"] == "R-only"
    assert scoring({"em_basis": {}}, t)["grid"] == "R-only"
    sc = {"converged": True, "diverged": False, "stopped": "converged", "reported_pass": 5,
          "passes": [{"pass": k, "ir_drop_mV": 40.0, "residual_mV": 1.0,
                      "tolerance_mV": 1.0} for k in range(6)],
          "vdd": 1.0, "simulator": "xyce", "nets": ["VDD"], "feedback": {"grid": "RC"},
          "scoring": {"grid": WORST_GRID, "basis": "worst", "ac": True}}
    text = self_consistency_text("VDD", sc)
    assert "conservative, not accurate" in text and "within 0.52 %" in text
    assert "scored through the RC grid (--dynamic, the worst" not in text
    sc["scoring"] = {"grid": "RC", "basis": "decap-aware", "ac": True}
    assert "decap-aware EM basis" in self_consistency_text("VDD", sc)


def test_constant_pin_currents_raise_no_w545():
    """Example 12's DC loads: the RC grid's capacitors carry no current, so the
    R-only scoring is exact, and W545's minor-branch caveat does not apply."""
    from emir.report import self_consistency_text
    from emir.selfconsist import scoring, scoring_notice
    dc = [{"wave": np.full(5, 2e-3)}, {"wave": np.full(5, 1.9e-3)},
          {"feed": True, "wave": np.linspace(0, 1, 5)}]
    s = scoring({"em_basis": {}}, dc)
    assert s == {"grid": "R-only", "basis": None, "ac": False}
    sc = {"converged": True, "reported_pass": 2, "feedback": {"grid": "RC"}, "scoring": s,
          "passes": [{"pass": k, "ir_drop_mV": 100.0} for k in range(3)], "nets": ["VDD"]}
    with collecting() as found:
        scoring_notice("VDD", sc, log=lambda *a: None)
    assert found == []
    assert "every scoring of the branches agrees" in self_consistency_text("VDD", sc)
    sc["scoring"] = scoring({"em_basis": {}}, [{"wave": np.array([1e-3, 2e-3])}])
    with collecting() as found:
        scoring_notice("VDD", sc, log=lambda *a: None)
    assert [n["code"] for n in found] == ["EMIR-W545"]


# ------------------------------------------------------------------ the W545 step and the period


def _w545_sc(**kw):
    sc = {"converged": True, "diverged": False, "stopped": "converged", "reported_pass": 3,
          "n_passes": 4, "max_passes": 10, "at_pass_limit": False,
          "feedback": {"grid": "RC"}, "scoring": {"grid": "R-only", "basis": None, "ac": True},
          "passes": [{"pass": k, "residual_mV": 1.0, "tolerance_mV": 1.0,
                      "total_avg_A": -8e-3, "simulated_droop_mV": 0.0 if k == 0 else 50.0,
                      "worst_ratio": 3.0} for k in range(4)]}
    sc.update(kw)
    return sc


def test_the_w545_step_asks_for_the_period_it_needs(tmp_path):
    """Following W545's step (the same run with --dynamic) without --period
    computed no decap-aware EM (EMIR-W433) and raised W545 again: two more
    runs of the loop. Without --period the step now plans the re-run with
    `emir next` (which asks for the period); with it, the run is offered as
    it was."""
    from types import SimpleNamespace

    from emir.summary import _sc_dynamic_step
    a = SimpleNamespace(argv=["rail.dspf", "--net", "VDD", "--mode", "cc-grid",
                              "--self-consistent", "--netlist", "tb.cir"],
                        dynamic=False, period=None, dspf="rail.dspf", process="p.yaml",
                        netlist="tb.cir", net="VDD", vdd=1.2, sim="xyce")
    st = _sc_dynamic_step(a, "VDD", _w545_sc(), [])
    assert st["command"].startswith("emir next ") and "--dynamic" in st["command"]
    assert "--period" in st["why"] and st["needs_human"]
    a.period = 1e-9
    a.argv += ["--period", "1e-09"]
    st = _sc_dynamic_step(a, "VDD", _w545_sc(), [])
    assert st["command"].startswith("emir run ") and st["command"].endswith("--dynamic")
    # and `emir next` with --dynamic but no --period asks for it, the run waiting on it
    from _helpers import DSPF

    from emir import plan as P
    res = str(tmp_path / "r.raw")
    from emir.validate import _psf_tran
    t = np.linspace(0, 4e-9, 401)
    _psf_tran(res, t, {"MP0:s": 1e-3 + 0 * t})
    plan = P.build(P.Facts(dspf=DSPF, process=PROC, net="VDD", mode="cc-grid", cc_sim=res,
                           dynamic=True))
    run = next(s for s in plan.steps if s["id"] == "run")
    assert "--period '<period>'" in run["command"] and run["status"] == "waiting"
    assert run["placeholders"] == ["<period>"]
    assert any("period" in m["what"] for m in plan.missing)
    plan = P.build(P.Facts(dspf=DSPF, process=PROC, net="VDD", mode="cc-grid", cc_sim=res,
                           dynamic=True, period=1e-9))
    run = next(s for s in plan.steps if s["id"] == "run")
    assert "<period>" not in run["command"] and run["status"] == "todo"


def test_a_loop_converged_on_its_last_pass_is_offered_the_w545_step_too(tmp_path):
    """`at_pass_limit`: the retry (more passes) was offered and the W545
    --dynamic step never was (a `continue` skipped it)."""
    from types import SimpleNamespace

    from emir.summary import _self_consistent_steps
    a = SimpleNamespace(argv=["rail.dspf", "--period", "1e-09"], dynamic=False, period=1e-9,
                        dspf="rail.dspf", process="p.yaml", netlist="tb.cir", net="VDD",
                        vdd=1.2, sim="xyce", format="json")
    sc = _w545_sc(at_pass_limit=True, max_passes=4)
    res = SimpleNamespace(args=a, solved={"VDD": {"self_consistent": True, "mode": "cc-grid",
                                                  "self_consistency": sc}})
    cmds = [s["command"] for s in _self_consistent_steps(res, ["--format", "json"])]
    assert any("--max-passes 10" in c for c in cmds), cmds
    assert any(c.endswith("--dynamic") or "--dynamic " in c for c in cmds), cmds


def _summary(tmp_path, sc, argv, cwd):
    import json
    doc = {"schema": "emir.run/1", "dspf": str(tmp_path / "rail.dspf"), "process": PROC,
           "argv": argv, "cwd": cwd,
           "current_source": {"kind": "netlist", "path": str(tmp_path / "tb.cir")},
           "nets": [{"net": "VDD", "mode": "cc-grid", "self_consistent": True,
                     "self_consistency": sc}], "notices": []}
    p = tmp_path / "s.json"
    p.write_text(json.dumps(doc))
    return str(p)


@pytest.mark.parametrize("how,want", [
    ({"converged": False, "stopped": "pass limit", "reported_pass": 0}, "--max-passes 16"),
    ({"converged": False, "stopped": "time limit", "reported_pass": 0}, "--sim-timeout"),
    ({"at_pass_limit": True}, "--max-passes 16"),
])
def test_next_from_a_run_summary_plans_how_the_loop_ended(tmp_path, monkeypatch, how, want):
    """`emir next --run-summary` planned only [selftest, report] whatever the
    loop did: the run's own needs_human steps (more passes, a longer time
    limit, --dynamic for W545) were lost to an agent that takes emir next's."""
    import json
    import shutil
    shutil.copy(os.path.join(os.path.dirname(PROC), "rail.dspf"), tmp_path / "rail.dspf")
    (tmp_path / "tb.cir").write_text("* tb\n.tran 1n 4n\n.end\n")
    argv = ["rail.dspf", "--process", PROC, "--net", "VDD", "--mode", "cc-grid",
            "--self-consistent", "--sim", "xyce", "--netlist", "tb.cir", "--max-passes", "10",
            "--summary-out", "s.json"]
    path = _summary(tmp_path, _w545_sc(**how), argv, str(tmp_path))
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.chdir(other)
    import contextlib
    import io

    from emir.output import Out
    from emir.plan import main
    sink = Out("json", stdout=io.StringIO())
    with contextlib.redirect_stdout(io.StringIO()):
        main(["--run-summary", path, "--format", "json"], sink=sink)
    doc = json.loads(json.dumps(sink.emitted, default=str))
    by = {s["id"]: s for s in doc["steps"]}
    assert want in by["rerun-loop"]["command"] and by["rerun-loop"]["needs_human"]
    # its paths are the run's, absolute: it runs from any directory
    cmd = by["rerun-loop"]["command"]
    assert str(tmp_path / "rail.dspf") in cmd and str(tmp_path / "tb.cir") in cmd
    assert str(tmp_path / "s.json") in cmd
    if how.get("at_pass_limit"):
        dyn = by["rerun-with-dynamic"]
        assert "--dynamic --period '<period>'" in dyn["command"]
        assert dyn["status"] == "waiting" and any("period" in m["what"] for m in doc["missing"])
    assert "report" in by


def _next_from(path):
    import contextlib
    import io
    import json

    from emir.output import Out
    from emir.plan import main
    sink = Out("json", stdout=io.StringIO())
    with contextlib.redirect_stdout(io.StringIO()):
        main(["--run-summary", path, "--format", "json"], sink=sink)
    doc = json.loads(json.dumps(sink.emitted, default=str))
    return doc, {s["id"]: s for s in doc["steps"]}


@pytest.mark.parametrize("dynamic_wanted", [True, False])
def test_a_loop_converged_before_its_pass_limit_is_offered_no_more_passes(
        tmp_path, monkeypatch, dynamic_wanted):
    """`emir next --run-summary` offered every converged loop a needs_human
    re-run with --max-passes 16, saying it 'converged only on its last allowed
    pass' (`at_pass_limit` false): `_loop_steps` called the retry step without
    the run's own filter. The retry step now refuses such a loop itself."""
    import shutil
    from types import SimpleNamespace

    from emir.summary import _sc_retry_step
    shutil.copy(os.path.join(os.path.dirname(PROC), "rail.dspf"), tmp_path / "rail.dspf")
    (tmp_path / "tb.cir").write_text("* tb\n.tran 1n 4n\n.end\n")
    argv = ["rail.dspf", "--process", PROC, "--net", "VDD", "--mode", "cc-grid",
            "--self-consistent", "--sim", "xyce", "--netlist", "tb.cir", "--period", "1e-09",
            "--summary-out", "s.json"]
    sc = _w545_sc() if dynamic_wanted else _w545_sc(
        scoring={"grid": "R-only", "basis": None, "ac": False})
    assert sc["converged"] and sc["reported_pass"] == 3 and not sc["at_pass_limit"]
    a = SimpleNamespace(argv=argv)
    assert _sc_retry_step(a, "VDD", sc, []) is None
    assert "--max-passes 16" in _sc_retry_step(a, "VDD", {**sc, "at_pass_limit": True},
                                               [])["command"]
    path = _summary(tmp_path, sc, argv, str(tmp_path))
    monkeypatch.chdir(tmp_path)
    doc, by = _next_from(path)
    assert "rerun-loop" not in by, by["rerun-loop"]
    assert not any("last allowed pass" in s["why"] for s in doc["steps"])
    assert "report" in by
    assert ("rerun-with-dynamic" in by) == dynamic_wanted


def test_the_w545_step_without_period_keeps_the_runs_own_options(tmp_path, monkeypatch):
    """A run without --period that raised W545 planned `emir next --dspf ...
    --dynamic`, dropping its --droop-file, --summary-out and loop options (a
    loop that needed --max-passes 16 would be re-planned at 10). With a
    summary the step is `emir next --run-summary`, whose re-run is the run's
    whole command; without one, the reason names the options to give again."""
    import shutil
    from types import SimpleNamespace

    from emir.summary import _sc_dynamic_step, _self_consistent_steps
    shutil.copy(os.path.join(os.path.dirname(PROC), "rail.dspf"), tmp_path / "rail.dspf")
    (tmp_path / "tb.cir").write_text("* tb\n.tran 1n 4n\n.end\n")
    argv = ["rail.dspf", "--process", PROC, "--net", "VDD", "--mode", "cc-grid",
            "--self-consistent", "--sim", "xyce", "--netlist", "tb.cir", "--vdd", "1.0",
            "--max-passes", "16", "--tol-mV", "0.1", "--droop-file", "np.sc/droop.inc",
            "--summary-out", "s.json"]
    path = _summary(tmp_path, _w545_sc(max_passes=16), argv, str(tmp_path))
    a = SimpleNamespace(argv=argv, dynamic=False, period=None, dspf="rail.dspf",
                        process=PROC, netlist="tb.cir", net="VDD", vdd=1.0, sim="xyce")
    res = SimpleNamespace(args=a, summary_path=path,
                          solved={"VDD": {"self_consistent": True, "mode": "cc-grid",
                                          "self_consistency": _w545_sc(max_passes=16)}})
    steps = _self_consistent_steps(res, ["--format", "json"])
    assert [s["command"] for s in steps] == [
        f"emir next --run-summary {path} --format json"], steps
    assert steps[0]["needs_human"] and "--period" in steps[0]["why"]
    # following it: the re-run keeps every option of the run
    monkeypatch.chdir(tmp_path)
    doc, by = _next_from(path)
    dyn = by["rerun-with-dynamic"]
    for want in ("--max-passes 16", "--tol-mV 0.1", "--droop-file", "np.sc/droop.inc",
                 "--summary-out", "--vdd 1.0", "--dynamic --period '<period>'"):
        assert want in dyn["command"], (want, dyn["command"])
    assert dyn["status"] == "waiting" and any("period" in m["what"] for m in doc["missing"])
    assert "rerun-loop" not in by
    # without a summary the plan starts from the inputs: the reason says what to keep
    st = _sc_dynamic_step(a, "VDD", _w545_sc(max_passes=16), [])
    assert st["command"].startswith("emir next ") and "--dynamic" in st["command"]
    for want in ("--max-passes 16", "--tol-mV 0.1", "--droop-file np.sc/droop.inc",
                 "--summary-out s.json"):
        assert want in st["why"], want
    assert "--vdd" not in st["why"]                 # one net: the plan carries it


# ------------------------------------------------------------------ the quoted numbers


def _rows():
    import csv
    from _helpers import ROOT
    from emir import modes
    with open(os.path.join(ROOT, modes.SC_CSV), encoding="utf-8") as f:
        return list(csv.DictReader(ln for ln in f if not ln.startswith("#")))


def _loop_rows(rows, case, net="VDD"):
    return [r for r in rows if r["case"] == case and r["net"] == net
            and r["deck"].endswith("split")]


def _ratio(rows, case, col):
    p = _loop_rows(rows, case)
    base = float(next(r for r in p if r["pass"] == "0")[col])
    return [float(r[col]) / base for r in p if r["pass"] != "0"]


def test_the_quoted_loop_costs_and_ranges_are_the_committed_csvs():
    """Every cost and range the texts quote of the loop (the PWL points, a
    pass's time points and simulation time against the open loop's, W544's
    device range, W540's reltol shifts, the ranges' cases) is computed here
    from the committed CSV."""
    from emir import errors, modes
    rows = _rows()
    cases = {r["case"] for r in rows}
    one_net = [c for c in cases if c not in ("vss_n10", "k32_n10_long", "hd16b_plain",
                                             "k32_n20_reltol3")]
    meshes = [c for c in one_net if c != "one"]
    text = " ".join(r["accuracy"] + " " + r["cost"] for r in modes.routes())
    meaning = {c: errors.lookup(c).meaning for c in ("EMIR-W540", "EMIR-W543", "EMIR-W544")}
    # the PWL points a pin over 4 ns on one net of the meshes, and with both rails
    pwl = [float(r["pwl_points"]) for c in meshes for r in _loop_rows(rows, c)
           if r["pass"] != "0"]
    both = [float(r["pwl_points"]) for r in _loop_rows(rows, "vss_n10") if r["pass"] != "0"]
    assert (min(pwl), max(pwl), max(both)) == (394, 1148, 1261)
    assert "394 to 1 148 a pin over 4 ns on one net of the meshes, 1 261 with both" in text
    # a pass's time points and simulation time against pass 0's (the open loop)
    steps = [x for c in one_net for x in _ratio(rows, c, "steps")]
    assert f"{min(steps):.1f} to {max(steps):.1f}" == "1.7 to 3.8"
    k32 = [x for c in ("k32_n10", "k32_n20", "k32_n40", "c0_n20", "p4_n20", "null_n20")
           for x in _ratio(rows, c, "steps")]
    assert f"{min(k32):.2f} to {max(k32):.2f}" == "2.64 to 3.29"
    lo3 = _ratio(rows, "k32_n20_reltol3", "steps")
    assert f"{min(lo3):.1f} to {max(lo3):.1f}" == "4.3 to 4.9"
    for want in ("1.7 to 3.8 times", "2.64 to 3.29", "4.3 to 4.9"):
        assert want in text, want
    sim = [x for c in meshes for x in _ratio(rows, c, "sim_s")]
    assert f"{min(sim):.1f} to {max(sim):.1f}" == "2.7 to 4.2"
    assert "took 2.7 to 4.2 times the open loop's simulation time" in meaning["EMIR-W543"]
    # W544: the RC DSPF's loop, its devices' peaks at the reported pass
    rc = _loop_rows(rows, "hd16b_c20rc")
    rep = next(r for r in rc if r["pass"] == rc[-1]["reported_pass"])
    lo, hi = float(rep["dev_peak_min"]), float(rep["dev_peak_max"])
    assert f"{100 * lo:+.2f} % to {100 * hi:+.2f} %" == "-0.08 % to +0.01 %"
    assert "within 0.1 % (-0.08 % to +0.01 %)" in meaning["EMIR-W544"]
    # W540: reltol 1e-3 against 1e-4, the open loop and the converged loop
    def gov(case, deck, rp=False):
        rs = [r for r in rows if r["case"] == case and r["deck"].startswith(deck)]
        r = next(x for x in rs if x["pass"] == rs[-1]["reported_pass"]) if rp else rs[0]
        return [float(r[m + "_gov"]) for m in ("avg", "rms", "peak")]
    ol = [abs(a - b) * 100 for a, b in zip(gov("k32_n20_reltol3", "cc.cir"),
                                           gov("k32_n20", "cc.cir"))]
    lp = [abs(a - b) * 100 for a, b in zip(gov("k32_n20_reltol3", "cc_tb", True),
                                           gov("k32_n20", "cc_tb", True))]
    want = (f"by {min(ol):.2f} to {max(ol):.2f} points and the converged loop's by "
            f"{min(lp):.2f} to {max(lp):.2f}")
    assert want == "by 0.29 to 0.41 points and the converged loop's by 0.09 to 0.45"
    assert want in meaning["EMIR-W540"]
    # VSS, closed with VDD: its governing peak, quoted beside the 0.14 % claim
    vss = _loop_rows(rows, "vss_n10", "VSS")
    rep = next(r for r in vss if r["pass"] == vss[-1]["reported_pass"])
    assert f"{100 * float(rep['peak_gov']):.2f} %" == "0.45 %"
    assert "VSS closed with VDD on 264 nodes within 0.45 %" in text


def _text(rel):
    from _helpers import ROOT
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return " ".join(f.read().split())


def test_the_quoted_ranges_name_the_cases_they_cover():
    """'Minor branches within 3.1 % up to 432 nodes (4.7 % in four phases)' left
    out the 128-inverter meshes of 384 nodes (-4.10 %, +5.05 %); 'within 0.14 %
    ... up to 432 nodes' the 432-node mesh at reltol 1e-3 (rms +0.53 %); '1.7 to
    3.8 times ... over every one-net case' the plain update (6.12). Each claim
    now names its cases, and each number is the committed CSV's."""
    import math

    from emir import modes
    rows = _rows()

    def reported(case, net="VDD"):
        p = _loop_rows(rows, case, net)
        return next(r for r in p if r["pass"] == p[-1]["reported_pass"])
    # the loops that fed a droop back (not pass 0), on the 2 fF meshes
    conv = [c for c in dict.fromkeys(r["case"] for r in rows)
            if _loop_rows(rows, c) and reported(c)["reported_pass"] != "0"
            and float(reported(c)["c_node_fF"]) == 2.0]
    by = {c: reported(c) for c in conv}
    minor = {c: (100 * float(r["peak_minor_min"]), 100 * float(r["peak_minor_max"]))
             for c, r in by.items() if r["peak_minor_min"] not in ("", None)}
    k32 = [c for c in minor if by[c]["devices"] == "32" and int(by[c]["grid_nodes"]) <= 432]
    eight = max(max(-minor[c][0], minor[c][1]) for c in k32 if by[c]["phases"] == "8")
    four = max(max(-minor[c][0], minor[c][1]) for c in k32 if by[c]["phases"] == "4")
    assert (math.ceil(eight * 10) / 10, math.ceil(four * 10) / 10) == (3.1, 4.7)
    k128 = [c for c in minor if by[c]["devices"] == "128"]
    lo, hi = min(minor[c][0] for c in k128), max(minor[c][1] for c in k128)
    assert (f"{lo:+.1f}", f"{hi:+.1f}") == ("-4.1", "+5.1")
    claim = "-4.1 % to +5.1 % with 128 inverters at 4.1-7.9 % droop"
    for rel in ("agent/playbooks/02-big-block-cc.md", "CHANGELOG.md"):
        t = _text(rel)
        assert "on the 32-inverter meshes of 132 and 432 nodes" in t and claim in t, rel
        assert "within 3.1 % up to 432 nodes" not in t, rel
    route = next(r for r in modes.routes() if r["route"] == "cc-grid self-consistent")
    assert "-4.1 % to +5.1 % with 128 at 4.1 % to 7.9 % droop" in route["accuracy"]
    mm = _text("docs/method-modes.md")
    assert "| minor branches, 2 fF a node, 128 inverters (4.1 % to 7.9 % droop), 384 nodes " \
           "| | | -4.1 % to +5.1 % |" in mm
    # within 0.14 %: at reltol 1e-4; the 432-node mesh at 1e-3 read rms +0.53 %
    r3 = reported("k32_n20_reltol3")
    assert f"{100 * float(r3['rms_gov']):+.2f} %" == "+0.53 %"
    for rel in ("agent/playbooks/02-big-block-cc.md", "CHANGELOG.md", "docs/big-blocks.md"):
        t = _text(rel)
        i = t.index("within 0.14 %")
        assert "reltol 1e-4" in t[i:i + 120], rel
    assert "+0.53 % rms at reltol 1e-3" in route["accuracy"]
    # 1.7 to 3.8 times: with the companion update; the plain update up to 6.1
    plain = _ratio(rows, "hd16b_plain", "steps")
    assert f"{max(plain):.1f}" == "6.1"
    for rel in ("docs/big-blocks.md", "docs/method-modes.md", "examples/06_big_block/README.md"):
        t = _text(rel)
        for m in re.finditer(r"1\.7 to 3\.8", t):
            assert "companion update" in t[m.start():m.start() + 160], rel
    assert "up to 6.1 with the plain one" in route["cost"]


def _decap_rows():
    import csv
    from _helpers import ROOT
    with open(os.path.join(ROOT, "examples", "13_decap_aware", "results", "decap_loop.csv"),
              encoding="utf-8") as f:
        return list(csv.DictReader(ln for ln in f if not ln.startswith("#")))


def test_the_decap_loop_numbers_are_the_committed_csv():
    """The loop with decaps was quoted from a scratch case no script in the
    repository makes (W547's '-24 % rms and -42 % peak' was a configuration
    that can no longer arise). examples/13_decap_aware/decap_loop.py writes
    results/decap_loop.csv; every number W547, the report, the CHANGELOG and
    the docs quote of it is computed here from that file."""
    from emir import errors, report
    rows = _decap_rows()

    def one(case, variant, reported=True, k=None):
        return next(r for r in rows if r["case"] == case and r["variant"] == variant
                    and (r["reported"] == "True" if k is None else r["pass"] == str(k)))

    def pct(r, key, fmt="+.2f"):
        return f"{100 * float(r[key]):{fmt}}"

    def spread(r, basis):
        v = [100 * float(r[f"{basis}_{m}_{c}_{e}"]) for m in ("avg", "rms", "peak")
             for c in ("major", "secondary", "minor") for e in ("min", "max")
             if r[f"{basis}_{m}_{c}_{e}"] not in ("", None)]
        v += [100 * float(r[f"{basis}_{m}_strap_{e}"]) for m in ("rms", "peak")
              for e in ("min", "max")]
        return min(v), max(v)
    sized, unsized = one("hd16b_dec", "sized"), one("hd16b_dec", "unsized")
    ol = one("hd16b_dec", "sized", k=0)
    sim10, sim16 = one("hd16b_dec_sim", "sized"), one("hd16b_dec_sim", "sized_16")
    # how each loop ended
    assert (sized["stopped"], sized["n_sims"], sized["feedback_decaps"]) == ("converged", "6", "8")
    assert (unsized["stopped"], unsized["unsized_decaps"]) == ("settled", "8")
    assert "EMIR-W547" in unsized["codes"]
    assert (sim10["stopped"], sim10["reported_pass"], sim10["simulated_decaps"]) == \
        ("pass limit", "0", "1") and "EMIR-W407" in sim10["codes"]
    assert (sim16["stopped"], sim16["n_sims"]) == ("settled", "11")
    # the numbers
    lo, hi = spread(sized, "aware")
    assert max(abs(lo), abs(hi)) <= 1.2 and f"{hi:.2f}" == "1.14"
    dev = [abs(100 * float(sized[f"dev_{m}_{e}"])) for m in ("avg", "rms", "peak")
           for e in ("min", "max")]
    assert max(dev) <= 0.05
    assert (pct(sized, "dev_peak_min"), pct(sized, "dev_peak_max")) == ("-0.04", "+0.02")
    assert (pct(unsized, "dev_peak_min"), pct(unsized, "dev_peak_max")) == ("-5.87", "-1.00")
    assert (pct(unsized, "aware_rms_gov"), pct(unsized, "aware_peak_gov")) == ("+13.90",
                                                                               "+31.94")
    assert unsized["aware_honoured"] == "False" and float(unsized["aware_rms_strap_max"]) == -1
    assert (pct(sized, "worst_rms_gov", ".1f"), pct(sized, "worst_peak_gov", ".1f")) == \
        (report.WORST_DECAP_RMS, report.WORST_DECAP_PEAK)
    assert report.AWARE_DECAP == "1.2"
    assert (pct(ol, "dev_peak_min", ".0f"), pct(ol, "dev_peak_max", ".0f")) == ("15", "28")
    assert (pct(ol, "aware_rms_strap_min", ".0f"), pct(ol, "aware_rms_strap_max", ".0f"),
            pct(ol, "aware_peak_strap_min", ".0f"), pct(ol, "aware_peak_strap_max", ".0f")) \
        == ("50", "52", "63", "70")
    assert (pct(sim16, "simdec_rms"), pct(sim16, "simdec_peak")) == ("-1.08", "-5.94")
    assert max(abs(100 * float(sim16[f"dev_peak_{e}"])) for e in ("min", "max")) <= 0.05
    # where they are quoted
    w547 = " ".join((errors.lookup("EMIR-W547").meaning + " "
                     + errors.lookup("EMIR-W547").hint).split())
    for want in ("-5.87 % to -1.00 %", "+13.90 % rms and +31.94 % peak", "within 1.2 %",
                 "-0.04 % to +0.02 %", "decap_loop.md", "not checked"):
        assert want in w547, want
    for old in ("-24 %", "-42 %", "-8.7 %", "read LOW, not conservative, whichever"):
        assert old not in w547, old
    texts = {rel: _text(rel) for rel in ("CHANGELOG.md", "docs/big-blocks.md",
                                         "docs/method-modes.md",
                                         "examples/13_decap_aware/README.md")}
    for rel, t in texts.items():
        assert "decap_loop" in t, rel
        assert "-5.94" in t, rel
        assert ("-0.19 .. +1.14 %" if rel.endswith("README.md") else "within 1.2 %") in t, rel
        assert "-24 % rms" not in t and "-42 % peak" not in t, rel
    for rel in ("CHANGELOG.md", "docs/big-blocks.md"):
        assert "-5.87 % to -1.00 %" in texts[rel] and "+13.90 % rms" in texts[rel], rel
    assert "+23.5 % rms and +55.7 % peak" in texts["docs/big-blocks.md"]
    for rel in ("emir/tdloop.py",):
        t = _text(rel)
        assert "-24 % rms" not in t and "-5.87 %" in t


def test_the_report_says_how_the_worst_basis_and_unsized_decaps_read():
    """The report called a worst-basis loop 'accurate rather than conservative'
    and, in the same paragraph, 'conservative, not accurate', with the 20 fF
    numbers whatever the run (+23.5 % rms on a mesh with decaps); and a loop
    that left decaps out (EMIR-W547: devices low, governing branch high,
    straps not checked) 'accurate rather than conservative'."""
    from emir.report import WORST_DECAP_PEAK, WORST_DECAP_RMS, self_consistency_text
    from emir.selfconsist import WORST_GRID
    base = {"converged": True, "diverged": False, "stopped": "converged", "reported_pass": 5,
            "passes": [{"pass": k, "ir_drop_mV": 40.0, "residual_mV": 1.0,
                        "tolerance_mV": 1.0} for k in range(6)],
            "vdd": 1.0, "simulator": "xyce", "nets": ["VDD"]}
    t = self_consistency_text("VDD", {**base, "feedback": {"grid": "RC", "decaps": 8},
                                      "scoring": {"grid": WORST_GRID, "basis": "worst",
                                                  "ac": True}})
    assert "accurate rather than conservative" not in t
    assert "conservative rather than accurate on the governing branch" in t
    assert "conservative, not accurate" in t and "on one synthetic mesh at 20 fF a node" in t
    assert f"+{WORST_DECAP_RMS} % rms and +{WORST_DECAP_PEAK} % peak" in t
    t = self_consistency_text("VDD", {**base, "feedback": {"grid": "RC", "decaps": 0,
                                                           "unsized_decaps": 8},
                                      "scoring": {"grid": "RC", "basis": "decap-aware",
                                                  "ac": True}})
    assert "accurate rather than conservative" not in t
    for want in ("EMIR-W547", "neither accurate nor conservative", "LOW",
                 "governing branch reads high", "not checked"):
        assert want in t, want
    t = self_consistency_text("VDD", {**base, "feedback": {"grid": "RC", "decaps": 8},
                                      "scoring": {"grid": "RC", "basis": "decap-aware",
                                                  "ac": True}})
    assert "accurate rather than conservative" in t


def test_accurate_is_said_only_of_the_bases_that_are():
    """big-blocks.md said 'conservative rather than accurate' of the worst
    basis and then, unscoped, 'The answer is accurate rather than
    conservative'; limits.md and the routes the same."""
    from emir import modes
    route = next(r for r in modes.routes() if r["route"] == "cc-grid self-consistent")
    for name, t in (("docs/big-blocks.md", _text("docs/big-blocks.md")),
                    ("docs/limits.md", _text("docs/limits.md")),
                    ("routes", " ".join(route["accuracy"].split()))):
        for m in re.finditer(r"(?i)the answer is accurate", t):
            before = t[max(0, m.start() - 600):m.start()]
            if re.search(r"conservative rather than accurate|keeps the R-only grid's high "
                         r"readings|conservative\)", before):
                assert "decap-aware" in t[max(0, m.start() - 60):m.start() + 60], \
                    (name, t[m.start() - 200:m.start() + 80])


def test_the_open_loop_is_called_conservative_where_it_is():
    """Above about 2 % droop the texts said to report the open loop as
    'conservative on the governing branch in every case measured': its rms and
    peak are, but its avg and IR read -0.17 % at 4.19 % droop in four phases."""
    rows = _rows()
    ol = [r for r in rows if r["deck"] == "cc.cir"]
    assert all(float(r["rms_gov"]) > 0 and float(r["peak_gov"]) > 0 for r in ol)
    high = [r for r in ol if float(r["droop_pct"]) > 2.0]
    low = min(high, key=lambda r: float(r["avg_gov"]))
    assert (low["case"], f"{100 * float(low['avg_gov']):+.2f}", f"{100 * float(low['ir_err']):+.2f}",
            f"{float(low['droop_pct']):.2f}") == ("hd16b_p4", "-0.17", "-0.17", "4.19")
    for rel in ("docs/big-blocks.md", "docs/method-modes.md", "CHANGELOG.md"):
        t = _text(rel)
        hits = [m.start() for m in re.finditer(r"conservative on the governing branch in "
                                               r"every case", t)]
        assert hits, rel
        for i in hits:
            assert "rms and peak" in t[i - 40:i], (rel, t[i - 80:i + 80])


def test_the_texts_describe_the_waveform_loop_not_the_average_droop_one():
    """README's 'Choosing the mode' row (and the PyPI README copied from it, and
    an eval card's reference answer) described the replaced loop ('drooped
    supply', 'two or three CC simulations', 'rms and peak keep most of their
    error'); the help said the droop is solved on the R-only grid; playbook 02
    said 'one net per run' beside --net VDD,VSS; README and the docs index
    listed the licensed routes without UNVERIFIED."""
    from _helpers import ROOT

    def read(p):
        with open(os.path.join(ROOT, p), encoding="utf-8") as f:
            return f.read()
    for p in ("README.md", "README.pypi.md", "agent/evals/cards/02-pll-rcc-no-converge.yaml"):
        t = " ".join(read(p).split())
        for old in ("drooped supply", "two or three CC simulations", "keep most of their error",
                    "it moves the average and the IR, not rms and peak"):
            assert old not in t, (p, old)
    row = next(ln for ln in read("README.md").splitlines()
               if ln.startswith("| `--mode cc-grid --self-consistent`"))
    for want in ("own droop waveform", "4 or 5 CC simulations", "+0.01 / +0.34 / +0.78 %",
                 "+1.0 / +34 / +70 %"):
        assert want in row, want
    # two open-loop averages at 4.1 % droop, on two meshes: each names its own
    hd = _loop_rows(_rows(), "hd16b")[0]
    assert (hd["devices"], hd["grid_nodes"]) == ("128", "384")
    assert "at 4.1 % droop (128 inverters, 384 nodes)" in row
    cc = next(ln for ln in read("README.md").splitlines() if ln.startswith("| `--mode cc-grid` |"))
    assert "+1.3 % at 4.1 % (256 inverters, 5 156 nodes)" in cc
    from emir.dspf import build_parser
    helps = " ".join(a.help or "" for a in build_parser()._actions)
    assert "solve the R-only grid over the whole transient" not in helps
    assert "R-only, or RC when the DSPF has capacitance" in helps
    pb = " ".join(read("agent/playbooks/02-big-block-cc.md").split())
    assert "(one net per run;" not in pb and "Spectre one net per run" in pb
    readme = read("README.md").splitlines()
    for key in ("| **Virtuoso** |", "| HSPICE, Xyce and ngspice:", "| PVS LVS and Quantus"):
        assert "UNVERIFIED" in next(ln for ln in readme if ln.startswith(key)), key
    idx = read("docs/index.md").splitlines()
    for key in ("| Spectre, HSPICE, Xyce or ngspice |", "| Cadence tools |"):
        assert "UNVERIFIED" in next(ln for ln in idx if ln.startswith(key)), key
    # the "Three ways in" list names Virtuoso too: its item, to the next one
    item = read("docs/index.md").split("3. **Virtuoso.**", 1)[1].split("\n\n", 1)[0]
    assert "UNVERIFIED" in item, item


def test_next_from_a_multi_net_run_summary_keeps_the_report_step(tmp_path, monkeypatch):
    """After `emir run --net VDD,VSS --self-consistent`, `emir next --run-summary`
    answered 'missing: net VDD,VSS' and dropped the report step AGENTS.md
    requires."""
    import contextlib
    import io
    import json

    from _helpers import two_net_dspf

    from emir.output import Out
    from emir.plan import main
    dspf = two_net_dspf(tmp_path)
    (tmp_path / "tb.cir").write_text("* tb\n.tran 1n 4n\n.end\n")
    sc = _w545_sc(scoring={"grid": "R-only", "basis": None, "ac": False})
    argv = [dspf, "--process", PROC, "--net", "VDD,VSS", "--mode", "cc-grid",
            "--self-consistent", "--sim", "xyce", "--netlist", "tb.cir"]
    doc = {"schema": "emir.run/1", "dspf": dspf, "process": PROC, "argv": argv,
           "cwd": str(tmp_path),
           "current_source": {"kind": "netlist", "path": str(tmp_path / "tb.cir")},
           "nets": [{"net": n, "mode": "cc-grid", "self_consistent": True,
                     "self_consistency": sc} for n in ("VDD", "VSS")], "notices": []}
    path = tmp_path / "mn.json"
    path.write_text(json.dumps(doc))
    monkeypatch.chdir(tmp_path)
    sink = Out("json", stdout=io.StringIO())
    with contextlib.redirect_stdout(io.StringIO()):
        main(["--run-summary", str(path), "--format", "json"], sink=sink)
    out = json.loads(json.dumps(sink.emitted, default=str))
    assert not [m for m in out["missing"] if "VDD,VSS" in m["what"]], out["missing"]
    assert "report" in [s["id"] for s in out["steps"]]
