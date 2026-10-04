"""The mode of a simulated run is the person's choice, and the self-consistent
loop works on every simulator.

* `--mode` (rcc or cc-grid) is required wherever currents come from a
  simulation (EMIR-E520, with the trade-offs in its hint); `auto` is gone from
  the code, the docs, the examples and the agent layer; a DC run needs none;
* `emir next` without a mode never picks one: its first step is
  `choose-mode`, with the three routes and their measured numbers;
* the loop (`emir/selfconsist.py`): pass 0 is the open loop, a split
  testbench at the nominal rail reproduces the unsplit answer, the fixed point
  of a resistive load is the closed form, divergence is caught, and the open
  simulators run it for real;
* an open-loop cc-grid run whose droop is above about 2 % of --vdd warns
  (EMIR-W520) and proposes the self-consistent route (`needs_human`).
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys

import numpy as np
import pytest
from _helpers import CUR, DSPF, PROC, ROOT, emir, write

from emir import modes, selfconsist, site

HAVE = {s: site.tool(s)[0] is not None for s in ("xyce", "ngspice")}


def jdoc(p, rc=0):
    assert p.returncode == rc, (p.returncode, p.stdout[-2000:], p.stderr[-2000:])
    return json.loads(p.stdout)


# ------------------------------------------------------------ the text everywhere


def _tracked(*suffixes):
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                         text=True).stdout.split()
    skip = ("CHANGELOG.md", "docs/changelog.md", "blog/")
    return [f for f in out if f.endswith(suffixes) and not f.startswith(skip)
            and f not in skip and os.path.isfile(os.path.join(ROOT, f))]


def _logical(text):
    """Lines with their backslash continuations joined."""
    out, cur = [], ""
    for ln in text.splitlines():
        if ln.rstrip().endswith("\\"):
            cur += ln.rstrip()[:-1] + " "
            continue
        out.append(cur + ln)
        cur = ""
    return out


#: an emir command in a doc or a script: `emir run`, `bin/emir run`,
#: `$EMIR run`, `"$EMIR" run`, `cli.main(["run", ...` is code (tests)
_CMD = re.compile(r"""(?:\bemir|\$\{?EMIR\}?"?|\$E|\$emir)\s+(run|check|simulate|save-lines|abstract)\b(.*)""")


#: the files generated from the code (their text is the parsers' help)
GENERATED = ("agent/CONTRACT.md", "agent/errors.md", "docs/cli-reference.md")


def _offences(path):
    """Command lines (the command followed by its first argument, not prose
    naming an option: `emir save-lines --sim hspice prints ...`) that read
    or plan a simulation without --mode."""
    bad = []
    text = open(os.path.join(ROOT, path), encoding="utf-8", errors="replace").read()
    for ln in _logical(text):
        for m in _CMD.finditer(ln):
            cmd, rest = m.group(1), m.group(2)
            rest = re.split(r"`|\s\|\s|;|&&|\*\*", rest)[0]
            words = rest.split()
            # a command line: the command's first argument is a file (a path,
            # a placeholder, a shell variable), not prose or an option
            first = words[0].strip("\"'") if words else ""
            if not re.match(r"^[<>{}$\w./-]+$", first) or first.startswith("-") or \
                    not re.search(r"[./<$]", first) or "--help" in rest \
                    or "--mode" in rest or "..." in rest:
                continue
            if cmd in ("run", "check"):
                if ("--cc-sim" in rest or "--netlist" in rest) and "--abstract" not in rest:
                    bad.append(f"{path}: {ln.strip()[:160]}")
            else:
                bad.append(f"{path}: {ln.strip()[:160]}")
    return bad


def test_every_printed_simulated_run_names_its_mode():
    """Docs, examples, scripts and the agent layer: a run or check on a
    simulation, a simulate, a save-lines or an abstract always shows --mode
    (an emir command without it is refused, EMIR-E520)."""
    bad = []
    for f in _tracked(".md", ".sh", ".yaml", ".tmpl", ".ocn", ".il", ".py"):
        if f.startswith(("tests/", "emir/")) or f in GENERATED:
            continue
        bad += _offences(f)
    assert not bad, "\n".join(bad)


def test_auto_is_gone():
    """`--mode auto` (and the decision it made) is nowhere but where a text
    says it is gone."""
    bad = []
    for f in _tracked(".md", ".sh", ".yaml", ".tmpl", ".ocn", ".il", ".py", ".json"):
        if f == "tests/test_modes.py":
            continue
        text = open(os.path.join(ROOT, f), encoding="utf-8", errors="replace").read()
        for ln in text.splitlines():
            if re.search(r"--mode\s+auto|mode auto|\"auto\", \"rcc\"|auto -> (rcc|cc)", ln) \
                    and not re.search(r"gone|removed|retired|no longer|never picks|"
                                      r"not pick", ln):
                bad.append(f"{f}: {ln.strip()[:140]}")
    assert not bad, "\n".join(bad)


# ------------------------------------------------------------ the CLI


def test_the_mode_is_required_where_a_simulation_is(tmp_path):
    from _helpers import cc_result
    cc = cc_result(tmp_path)
    for argv in (["run", DSPF, "--process", PROC, "--cc-sim", cc],
                 ["save-lines", DSPF],
                 ["simulate", "tb.cir", "--sim", "xyce", "--dspf", DSPF],
                 ["abstract", DSPF, "--process", PROC, "--cc-sim", cc, "-o", "a.json"],
                 ["run", DSPF, "--process", PROC, "--netlist", "x.scs"],
                 ["run", DSPF, "--process", PROC, "--cc-sim", cc, "--mode", "auto"]):
        d = jdoc(emir(*argv, "--format", "json", cwd=tmp_path), rc=2)
        assert d["code"] == "EMIR-E520", argv
        # the hint names both modes, their trade-offs, and emir next
        assert "rcc" in d["hint"] and "cc-grid" in d["hint"] and "emir next" in d["hint"]
        assert "converges poorly" in d["hint"] and "--self-consistent" in d["hint"]
        assert d["next_steps"] and d["next_steps"][0]["command"].startswith("emir next")
    d = jdoc(emir("check", DSPF, "--process", PROC, "--cc-sim", cc, "--format", "json"),
             rc=2)
    assert "EMIR-E520" in [f["code"] for f in d["findings"] if f["level"] == "FAIL"]
    # a DC run needs no mode, and `cc` is still read as cc-grid
    jdoc(emir("run", DSPF, "--process", PROC, "--net", "VDD", "--currents", CUR,
              "--format", "json"))
    d = jdoc(emir("save-lines", DSPF, "--mode", "cc", "--format", "json"))
    assert d["mode"] == "cc-grid"
    # an abstract is cc-grid only
    d = jdoc(emir("abstract", DSPF, "--process", PROC, "--cc-sim", cc, "--mode", "rcc",
                  "-o", tmp_path / "a.json", "--format", "json"), rc=2)
    assert d["code"] == "EMIR-E521"


def test_retired_auto_codes_are_explained():
    for code in ("EMIR-E413", "EMIR-I205", "EMIR-I206", "EMIR-I403", "EMIR-I406"):
        p = emir("explain", code, "--format", "json")
        d = json.loads(p.stdout)
        entry = d["codes"][0] if "codes" in d else d
        assert entry.get("retired") is True or "retired" in json.dumps(d), code


def test_the_routes_quote_their_sources():
    rs = modes.routes()
    assert [r["route"] for r in rs] == ["rcc", "cc-grid", "cc-grid self-consistent"]
    for r in rs:
        assert os.path.isfile(os.path.join(ROOT, r["source"])), r["source"]
        assert r["source"] in r["cost"]
        assert r["source"] in r["accuracy"] or r["route"] == "rcc"     # rcc is the reference
    # the self-consistent route says what it does not fix
    sc = rs[2]["accuracy"]
    assert "over-corrected" in sc and "rms and peak" in sc and "verify-sim" in sc


def test_the_routes_quote_the_committed_measurement():
    """The numbers the self-consistent route quotes are the waveform loop's
    CSV (examples/06_big_block/results/selfconsist_waveform.csv)."""
    import csv
    with open(os.path.join(ROOT, modes.SC_CSV), encoding="utf-8") as f:
        rows = list(csv.DictReader(ln for ln in f if not ln.startswith("#")))
    hd = [r for r in rows if r["case"] == "hd16b" and r["deck"].endswith("split")]
    ol = next(r for r in rows if r["case"] == "hd16b" and r["deck"] == "cc.cir")
    rep = next(r for r in hd if r["pass"] == hd[-1]["reported_pass"])
    assert int(hd[-1]["pass"]) + 1 == 8 and hd[-1]["stopped"] == "settled"
    text = rs_text()
    for m, want in (("avg", "+0.01 %"), ("rms", "+0.34 %"), ("peak", "+0.78 %")):
        assert f"{100 * float(rep[m + '_gov']):+.2f} %" == want and want in text
    assert f"+{100 * float(ol['rms_gov']):.0f} %" == "+34 %" and "+34 %" in text
    assert f"+{100 * float(ol['peak_gov']):.0f} %" == "+70 %" and "+70 %" in text
    # the whole loop's time with 128 inverters, 4.1 % to 7.9 % droop
    big = [[r for r in rows if r["case"] == c and r["deck"].endswith("split")][-1]
           for c in ("hd16b", "hd16b_p4", "hd16x", "hd16b_c20", "hd16b_c20rc")]
    lo, hi = (min(float(r["total_s"]) for r in big), max(float(r["total_s"]) for r in big))
    assert f"{lo:.0f} s to {hi:.0f} s with 128" in text
    n40 = [r for r in rows if r["case"] == "k32_n40" and r["deck"].endswith("split")][-1]
    minor = max(abs(float(n40["peak_minor_min"])), abs(float(n40["peak_minor_max"])))
    assert f"{100 * minor:.1f} % on 1 632" == "11.3 % on 1 632" and "11.3 % on 1 632" in text
    # the record of the loop it replaced stays as it was measured
    assert os.path.isfile(os.path.join(ROOT, modes.SC_AVERAGE_CSV))


def rs_text():
    return " ".join(r["accuracy"] + r["cost"] for r in modes.routes())


# ------------------------------------------------------------ emir next


def test_next_without_a_mode_asks_the_person(tmp_path):
    d = jdoc(emir("next", "--dspf", DSPF, "--process", PROC, "--net", "VDD",
                  "--have-xyce", "--format", "json"))
    st = d["steps"][0]
    assert st["id"] == "choose-mode" and st["n"] == 1 and st["needs_human"]
    assert st["placeholders"] == ["<mode>"] and d["mode"]["chosen"] is None
    assert {s["id"] for s in d["steps"]} == {"choose-mode", "selftest"}
    routes = {c["route"]: c for c in st["choices"]}
    assert routes["rcc"]["recommended"] and all(c["possible"] for c in routes.values())
    # each route's command plans that route; replacing <mode> works as well
    for c in routes.values():
        p = subprocess.run([sys.executable, "-m", "emir", *shlex.split(c["command"])[1:]],
                           capture_output=True, text=True, cwd=ROOT,
                           env={**os.environ, "PYTHONPATH": ROOT})
        nxt = json.loads(p.stdout)
        assert nxt["mode"]["chosen"] == c["mode"]
        assert "choose-mode" not in [s["id"] for s in nxt["steps"]]


def test_next_self_consistent_plans_the_loop(tmp_path):
    from emir import plan as P
    tb = write(tmp_path / "tb.cir", "* tb\n.tran 1n 10n\n.end\n")
    f = P.Facts(dspf=DSPF, process=PROC, net="VDD", mode="cc-grid", self_consistent=True,
                netlist=tb, vdd=1.2, have_xyce=True, have_cadence=False, sim="xyce")
    plan = P.build(f)
    ids = [s["id"] for s in plan.steps]
    assert ids[-3:] == ["check", "run", "report"], ids
    run = plan.steps[-2]
    assert "--self-consistent" in run["command"] and "--vdd 1.2" in run["command"]
    assert "--sim xyce" in run["command"] and run["needs_human"] and run["long_run"]
    assert not run["licensed_tool"]
    # Spectre: the split is the person's one edit, and the run needs --run
    f = P.Facts(dspf=DSPF, process=PROC, net="VDD", mode="cc-grid", self_consistent=True,
                netlist=tb, vdd=1.2, sim="spectre", have_cadence=True)
    plan = P.build(f)
    by = {s["id"]: s for s in plan.steps}
    assert "--split-stub" in by["split-stub"]["command"]
    assert by["run"]["licensed_tool"] == "spectre" and "--run" in by["run"]["command"]
    # what is missing, asked for
    f = P.Facts(dspf=DSPF, process=PROC, mode="cc-grid", self_consistent=True,
                have_xyce=True, have_cadence=False)
    plan = P.build(f)
    what = " | ".join(m["what"] for m in plan.missing)
    assert "--vdd" in what and "CC testbench" in what and plan.blocked
    f = P.Facts(dspf=DSPF, process=PROC, net="VDD", mode="rcc", self_consistent=True,
                netlist=tb, vdd=1.2, have_xyce=True)
    assert P.build(f).blocked


# ------------------------------------------------------------ the loop's arithmetic


STRAP = os.path.join(ROOT, "emir", "data", "sample", "rail.dspf")


def _stub_rail(tmp_path, r_load=1200.0, gain=1.0):
    """The sample rail with resistive loads: each pin draws its supply (the
    1.2 V rail plus the droop it is fed) over `r_load` (`gain` < 0 makes the
    feedback positive: divergence)."""
    from emir.validate import _psf_tran
    t = np.linspace(0, 1e-9, 21)
    raw = str(tmp_path / "s.tran")

    def sim(netlist, k, fed):
        sig = {}
        for pin in fed["v"]:
            v = 0.0 if fed["t"] is None else np.interp(t, fed["t"], fed["v"][pin])
            sig[pin] = np.full_like(t, 1.2 / r_load) + gain * v / r_load
        _psf_tran(raw, t, sig)
        return {"raw": raw, "seconds": 0.0}
    return sim


def test_pass_zero_is_the_open_loop_and_the_fixed_point_is_the_closed_form(tmp_path):
    from emir.dspf import parse
    from emir.engine import Process
    from emir.verify_sim import sc_answer
    d = parse(STRAP)
    proc = Process.load(PROC)
    cv = selfconsist.converge(None, str(tmp_path / "droop.scs"), d, "VDD", proc, "VDD", 1.2,
                              period=1e-9, max_passes=8, tol_mV=1e-6, tol_rel=0.0,
                              simulate=_stub_rail(tmp_path), log=lambda *a: None)
    p0 = cv["passes"][0]
    assert p0["simulated_droop_mV"] == 0 and p0["current_change"] is None
    assert p0["ir_drop_mV"] == pytest.approx(54.0, rel=1e-9)            # the open loop
    assert p0["droop_change_mV"] == pytest.approx(54.0, rel=1e-9)
    assert cv["converged"] and not cv["diverged"]
    want = sc_answer()
    assert cv["result"]["ir_drop"] * 1e3 == pytest.approx(want["ir_drop_mV"], rel=1e-6)
    ch = [p["droop_change_mV"] for p in cv["passes"]]
    assert all(b < a for a, b in zip(ch, ch[1:]))
    assert cv["passes"][-1]["converged"] and not cv["passes"][-2]["converged"]
    # the droop fed on the reported pass is the closed loop's, pin by pin
    t, F = cv["fed"]
    j = sorted(d.pin_groups("VDD")).index("MP3:s")
    assert -F[j].min() * 1e3 == pytest.approx(want["ir_drop_mV"], rel=1e-6)


def test_a_diverging_loop_reports_pass_zero(tmp_path):
    from emir.dspf import parse
    from emir.engine import Process
    from emir.errors import collecting
    d = parse(STRAP)
    proc = Process.load(PROC)
    with collecting() as notes:
        cv = selfconsist.converge(None, str(tmp_path / "droop.scs"), d, "VDD", proc, "VDD",
                                  1.2, period=1e-9, max_passes=6, tol_mV=1e-6,
                                  simulate=_stub_rail(tmp_path, r_load=30.0, gain=-3.0),
                                  log=lambda *a: None)
    assert cv["diverged"] and cv["reported_pass"] == 0 and not cv["converged"]
    assert "EMIR-W406" in [n["code"] for n in notes]
    assert cv["result"]["ir_drop"] == pytest.approx(cv["passes"][0]["ir_drop_mV"] / 1e3)


def test_parameter_names_stay_unique():
    names = selfconsist.param_names(["MP0:s", "MP0_s", "mp0:s"])
    assert len({n.lower() for n in names.values()}) == 3
    assert names["MP0:s"] == "vd_MP0_s"


# ------------------------------------------------------------ the open simulators


EX02 = os.path.join(ROOT, "examples", "02_xyce")


def _rail_case(tmp_path):
    for f in ("loadrail.dspf", "models.sp", "cc.sp", "tb_cc.cir"):
        shutil.copy(os.path.join(EX02, f), tmp_path / f)
    return tmp_path


@pytest.mark.parametrize("sim", ["xyce", "ngspice"])
def test_a_split_testbench_at_the_nominal_reproduces_the_unsplit_answer(tmp_path, sim):
    """Pass 0 of the loop simulates the split testbench with every pin at the
    nominal rail: electrically the CC testbench itself. The open loop through
    `emir simulate --probe` and pass 0 must agree (to the simulators' step
    control on two decks that differ by a source per pin)."""
    if not HAVE[sim]:
        pytest.skip(f"{sim} is not installed")
    ex = _rail_case(tmp_path)
    s = jdoc(emir("simulate", ex / "tb_cc.cir", "--sim", sim, "--dspf", ex / "loadrail.dspf",
                  "--mode", "cc-grid", "--probe", "--out", ex / "ol" / "cc.raw",
                  "--format", "json", cwd=ex))
    ol = jdoc(emir("run", ex / "loadrail.dspf", "--process", PROC, "--cc-sim", s["result"],
                   "--mode", "cc-grid", "--period", "1e-8", "--net", "VDD", "--format", "json",
                   cwd=ex))
    sc = jdoc(emir("run", ex / "loadrail.dspf", "--process", PROC, "--net", "VDD", "--mode",
                   "cc-grid", "--self-consistent", "--sim", sim, "--netlist",
                   ex / "tb_cc.cir", "--vdd", "1.8", "--period", "1e-8", "--max-passes", "1",
                   "--droop-file", ex / "sc" / "droop.inc", "--format", "json", cwd=ex))
    a, b = ol["nets"][0], sc["nets"][0]
    p0 = b["self_consistency"]["passes"][0]
    assert b["self_consistency"]["reported_pass"] == 0 and p0["simulated_droop_mV"] == 0
    assert p0["ir_drop_mV"] == pytest.approx(a["ir_drop_mV"], rel=1e-3)
    assert b["ir_drop_mV"] == pytest.approx(a["ir_drop_mV"], rel=1e-3)
    assert b["max_ratio"]["all"] == pytest.approx(a["max_ratio"]["all"], rel=1e-3)
    # what emir wrote: the person's testbench untouched, a split copy beside it
    deck = open(ex / "sc" / f"split.{sim}.cir").read()
    block = open(ex / "sc" / f"cc.split_{sim}.sp").read()    # the block's copy, split
    assert f".include cc.split_{sim}.sp" in deck and ".include droop.inc" in block
    assert "VEMIRSC0" not in block and "VEMIRSC0" in open(ex / "sc" / "droop.inc").read()
    assert "VEMIRSC" not in open(ex / "tb_cc.cir").read()
    assert b["self_consistent"] and sc["current_source"]["simulator"] == sim


@pytest.mark.parametrize("sim", ["xyce", "ngspice"])
def test_the_loop_runs_and_settles_on_an_open_simulator(tmp_path, sim):
    if not HAVE[sim]:
        pytest.skip(f"{sim} is not installed")
    ex = _rail_case(tmp_path)
    d = jdoc(emir("run", ex / "loadrail.dspf", "--process", PROC, "--net", "VDD", "--mode",
                  "cc-grid", "--self-consistent", "--sim", sim, "--netlist",
                  ex / "tb_cc.cir", "--vdd", "1.8", "--period", "1e-8",
                  "--summary-out", ex / "s.json", "--format", "json", cwd=ex))
    sc = d["nets"][0]["self_consistency"]
    assert sc["converged"] and sc["simulator"] == sim and sc["n_passes"] >= 2
    assert sc["passes"][0]["current_change"] is None
    assert all(p["current_change"] is not None for p in sc["passes"][1:])
    assert sc["passes"][-1]["residual_mV"] < sc["passes"][-1]["tolerance_mV"]
    assert "EMIR-I520" in [n["code"] for n in d["notices"]]
    # the report says it was self-consistent
    r = jdoc(emir("report", "--from", ex / "s.json", "--format", "json"))
    assert "self-consistent" in r["markdown"]


def test_a_licensed_loop_plans_without_run(tmp_path, monkeypatch):
    """HSPICE: the split deck and the droop include are written, the command
    printed, nothing runs (EMIR-I661); the next step adds --run."""
    monkeypatch.delenv("HSPICE_BIN", raising=False)
    ex = _rail_case(tmp_path)
    tb = (ex / "tb_cc.cir").read_text().replace(".include", ".include")
    write(ex / "tb.sp", tb)
    d = jdoc(emir("run", ex / "loadrail.dspf", "--process", PROC, "--net", "VDD", "--mode",
                  "cc-grid", "--self-consistent", "--sim", "hspice", "--netlist", ex / "tb.sp",
                  "--vdd", "1.8", "--droop-file", ex / "sc" / "droop.sp", "--format", "json",
                  cwd=ex))
    assert d["schema"] == "emir.simulate/1" and d["ran"] is False
    assert d["self_consistent"]["net"] == "VDD" and d["self_consistent"]["vdd"] == 1.8
    deck = open(d["deck"]).read()
    block = open(ex / "sc" / "cc.split_hspice.sp").read()
    assert ".include 'droop.sp'" in block and "VEMIRSC0" not in block
    assert ".option probe post=2" in deck
    assert re.search(r"^VEMIRSC0 \S+ \S+ DC 0$", open(ex / "sc" / "droop.sp").read(), re.M)
    assert d["next_steps"][0]["command"].endswith("--run --format json")


# ------------------------------------------------------------ the open loop's droop


def test_a_large_open_loop_droop_proposes_the_self_consistent_route(tmp_path):
    from _helpers import cc_result
    cc = cc_result(tmp_path)
    base = ["run", DSPF, "--process", PROC, "--net", "VDD", "--cc-sim", cc, "--mode",
            "cc-grid", "--period", "1e-9", "--format", "json"]
    d = jdoc(emir(*base, "--vdd", "1.2"))           # the sample's 54 mV: 4.5 % of 1.2 V
    n = d["nets"][0]
    assert n["droop_fraction"] == pytest.approx(n["ir_drop_mV"] / 1200.0)
    assert "EMIR-W520" in [x["code"] for x in d["notices"]]
    step = next(s for s in d["next_steps"] if "--self-consistent" in s["command"])
    assert step["needs_human"] is True and step["command"].startswith("emir next ")
    assert "--vdd 1.2" in step["command"] and "--mode cc-grid" in step["command"]
    p = subprocess.run([sys.executable, "-m", "emir", *shlex.split(step["command"])[1:]],
                       capture_output=True, text=True, cwd=ROOT,
                       env={**os.environ, "PYTHONPATH": ROOT})
    assert p.returncode == 0 and json.loads(p.stdout)["mode"]["chosen"] == "cc-grid"
    d = jdoc(emir(*base, "--vdd", "12"))            # 0.45 %: no proposal
    assert not any("--self-consistent" in s["command"] for s in d["next_steps"])
    d = jdoc(emir(*base))                           # no supply given: nothing to judge
    assert d["nets"][0]["droop_fraction"] is None
