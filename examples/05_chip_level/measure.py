#!/usr/bin/env python3
"""Measure the chip-level example through the library, against hand calculation
and against a flat run of the same network (top and both blocks on one net).

    python measure.py OUT        # after run.sh has written OUT

writes OUT/measured.json (every number below, with its check) and exits 1 when
a check fails. Sections:

1. hierarchical (blocks black-boxed, sync) vs flat: every top-level branch,
   avg / rms / peak;
2. back-annotation: every block branch re-checked with the top's pin
   voltages vs the same branch of the flat run;
3. the IR budget vs the flat droop (exact for the one-pin ADC, a bound for
   the two-pin PLL);
4. alignment on the shared trunk Rt0, with the ADC firing in bursts:
   inphase, async, and sync swept over the PLL's period;
5. the top-level self-consistency loop, run with the commands (`emir
   abstract`, `emir run --port-voltages-out`), with an ohmic stand-in for the
   block simulation so the converged value has a closed form.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import make_inputs as MI  # noqa: E402

from emir import cli, hier  # noqa: E402
from emir.dspf import analyse, build_parser, parse  # noqa: E402
from emir.engine import Process  # noqa: E402

CHECKS = []


def check(label, got, want, rel=1e-9, cmp="eq"):
    """`eq`: |got - want| <= rel |want|; `ge` / `le`: an inequality."""
    if cmp == "eq":
        err = abs(got - want) / max(abs(want), 1e-30)
        ok = err <= rel
    elif cmp == "ge":
        err, ok = got - want, got >= want * (1 - 1e-12)
    else:
        err, ok = want - got, got <= want * (1 + 1e-12)
    CHECKS.append({"check": label, "got": float(got), "want": float(want), "cmp": cmp,
                   "rel_tol": rel, "ok": bool(ok)})
    return got


def quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return fn(*a, **k)


def run(*args):
    a = build_parser().parse_args([str(x) for x in args])
    a.argv, a.command = [str(x) for x in args], "emir run"
    return quiet(analyse, a)


def branches(res, net="VDD"):
    r = res.solved[net]
    return {b["name"]: {m: float(r["Ie"][m][i]) for m in ("avg", "rms", "peak")}
            for i, b in enumerate(r["network"].res)}


def main(out: str) -> int:
    P = os.path.join(out, "process.yaml")
    ex = {k: os.path.join(out, k) for k in os.listdir(out)}
    R_ = MI.RISE
    # a trapezoid pulse sampled every DT and integrated by trapezoid: each
    # edge adds amp^2 (R/3 + DT^2 / (6 R)) to the integral of i^2 (the
    # continuous edge gives amp^2 R / 3)
    edges = 2 * R_ / 3 + MI.DT ** 2 / (3 * R_)
    rms_pll = 4e-3 * np.sqrt((1.25e-9 - R_ + edges) / 2.5e-9)
    G = np.array([[1 + 1 / 3, -1 / 3], [-1 / 3, 1 / 2.75 + 1 / 3]])
    vW, vE = np.linalg.solve(G, [-4e-3 / 3, -2e-3 / 3])            # from T1, volts
    hand = {"V_XADC": -4.0e-3, "V_W": -2e-3 + vW, "V_E": -2e-3 + vE,
            "pass_WE": (vW - vE) / 3, "Rt0_avg": 4e-3, "Rt0_peak": 6e-3,
            "Rt0_rms": np.sqrt(4e-6 + 2 * 2e-3 * 2e-3 + rms_pll ** 2),
            "Rt2_avg": -vW, "Rt2_rms": -vW / 2e-3 * rms_pll, "Rt2_peak": -vW / 2e-3 * 4e-3,
            "pll_Rm1_short": 2e-3 * 2 / 3, "pll_Rm1_flat": -vW,
            "pll_worst": 2e-3 - (2 / 3 * vW + 1 / 3 * vE) + 4e-3 / 3 + 10e-3,
            "adc_worst": 11.5e-3}
    nums = {"hand": {k: float(v) for k, v in hand.items()}}

    # ---- 1. hierarchical vs flat, every top-level branch
    flat = run(ex["flat.dspf"], "--process", P, "--net", "VDD", "--cc-sim", ex["flat.raw"],
               "--period", "10e-9", "--mode", "cc-grid")
    top = run(ex["top.dspf"], "--process", P, "--net", "VDD",
              "--abstract", f"{ex['adc.abstract.json']}@XADC",
              "--abstract", f"{ex['pll.abstract.json']}@XPLL", "--place", "XPLL=40,0,R0",
              "--align", "sync")
    fb, tb = branches(flat), branches(top)
    worst = 0.0
    for b in sorted(n for n in tb if n.startswith("Rt")):
        for m in ("avg", "rms", "peak"):
            worst = max(worst, abs(tb[b][m] - fb[b][m]) / max(abs(fb[b][m]), 1e-30))
    nums["top_vs_flat_max_rel"] = worst
    check("every top branch x avg/rms/peak, hierarchical vs flat (max rel diff)", worst, 1e-9,
          cmp="le")
    for b, m in (("Rt0", "avg"), ("Rt0", "rms"), ("Rt0", "peak"), ("Rt2", "avg"),
                 ("Rt2", "rms"), ("Rt2", "peak")):
        check(f"{b} {m} vs hand", tb[b][m], hand[f"{b}_{m}"], 1e-9)
    pins = {f"{p['instance']}:{p['port']}": p for p in
            top.hierarchy["nets"][0]["pins"]}
    check("V(XADC:VDDA) vs hand", pins["XADC:VDDA"]["V_avg_mV"] * 1e-3, hand["V_XADC"])
    check("V(XPLL west pin) vs hand", pins["XPLL:VDDP"]["V_avg_mV"] * 1e-3, hand["V_W"])
    check("V(XPLL east pin) vs hand", pins["XPLL:VDDP:E"]["V_avg_mV"] * 1e-3, hand["V_E"])
    pt = top.hierarchy["nets"][0]["pass_through"][0]["I_avg_A"]
    check("pass-through west -> east inside the PLL vs hand", pt, hand["pass_WE"], 1e-9)
    nums["pins_mV"] = {k: v["V_avg_mV"] for k, v in pins.items()}
    nums["pass_through_mA"] = pt * 1e3

    # ---- 2. back-annotation: the blocks with the top's pin voltages vs flat
    pv = hier.load_port_voltages(ex["port_voltages.json"])
    proc = Process.load(P)
    from emir import waves
    nums["annotated_vs_flat_max_rel"] = {}
    for inst, dsp, raw, per, pts, net in (("XADC", "adc.dspf", "adc.raw", 10e-9, 4000, "VDDA"),
                                          ("XPLL", "pll.dspf", "pll.raw", 2.5e-9, 1000, "VDDP")):
        d = parse(ex[dsp])
        entry, ctx = quiet(hier.characterise, d, net, waves.read(ex[raw]), proc, period=per,
                           points=pts)
        det = {}
        ba = quiet(hier.back_annotate, ctx, entry, pv, (ex["port_voltages.json"], inst),
                   detail=det)
        w = 0.0
        for i, nm in enumerate(det["names"]):
            fn = f"R{inst}_{nm[1:]}"
            for m in ("avg", "rms", "peak"):
                w = max(w, abs(det["Ie"][m][i] - fb[fn][m]) / max(abs(fb[fn][m]), 1e-30))
        nums["annotated_vs_flat_max_rel"][inst] = w
        check(f"{inst}: every block branch, back-annotated vs flat (max rel diff)", w, 1e-9,
              cmp="le")
        if inst == "XPLL":
            i = det["names"].index("Rm1")
            check("PLL Rm1 avg with the pins shorted vs hand", ctx["res"]["Ie"]["avg"][i],
                  hand["pll_Rm1_short"], 1e-9)
            check("PLL Rm1 avg back-annotated vs hand (= flat)", det["Ie"]["avg"][i],
                  hand["pll_Rm1_flat"], 1e-9)
            nums["pll_Rm1"] = {"short_circuit_mA": float(ctx["res"]["Ie"]["avg"][i]) * 1e3,
                               "annotated_mA": float(det["Ie"]["avg"][i]) * 1e3,
                               "under_read": 1 - float(ctx["res"]["Ie"]["avg"][i])
                               / float(det["Ie"]["avg"][i]),
                               "ratio_short": entry["internal"]["max_ratio"]["all"],
                               "ratio_annotated": ba["max_ratio"]["all"],
                               "verdict_short": entry["internal"]["verdict"],
                               "verdict_annotated": ba["verdict"]}
            check("PLL droop from the pad, back-annotated vs flat", ba["ir_drop_mV"] * 1e-3,
                  hand["pll_worst"], 1e-9)

    # ---- 3. the IR budget vs the flat droop
    fV = {n: float(flat.solved["VDD"]["V"][i])
          for i, n in enumerate(flat.solved["VDD"]["network"].nodes)}
    flat_adc = max(abs(v) for n, v in fV.items() if n.startswith("XADC"))
    flat_pll = max(abs(v) for n, v in fV.items() if n.startswith("XPLL"))
    budget = run(ex["top.dspf"], "--process", P, "--net", "VDD",
                 "--abstract", f"{ex['adc.abstract.json']}@XADC",
                 "--abstract", f"{ex['pll.abstract.json']}@XPLL", "--place", "XPLL=40,0,R0",
                 "--align", "sync", "--budget-mv", "15").hierarchy["budget"]["rows"]
    rows = {r["instance"]: r for r in budget}
    check("ADC budget total = flat worst droop (one pin: exact)", rows["XADC"]["total_mV"] * 1e-3,
          flat_adc, 1e-9)
    check("ADC flat worst droop vs hand", flat_adc, hand["adc_worst"], 1e-9)
    check("PLL budget total bounds the flat worst droop (two pins)",
          rows["XPLL"]["total_mV"] * 1e-3, flat_pll, cmp="ge")
    check("PLL flat worst droop vs hand", flat_pll, hand["pll_worst"], 1e-9)
    nums["budget_mV"] = {k: {"top": r["top_mV"], "internal": r["internal_mV"],
                             "total": r["total_mV"], "exact": r["exact"]}
                         for k, r in rows.items()}
    nums["flat_worst_mV"] = {"XADC": flat_adc * 1e3, "XPLL": flat_pll * 1e3}

    # ---- 4. alignment on the shared trunk, the ADC firing in bursts
    burst = ex["burst.abstract.json"]
    base = [ex["top.dspf"], "--process", P, "--net", "VDD", "--abstract", f"{burst}@XADC",
            "--abstract", f"{ex['pll.abstract.json']}@XPLL", "--place", "XPLL=40,0,R0"]
    al = {}
    for mode in ("inphase", "async"):
        al[mode] = branches(run(*base, "--align", mode))["Rt0"]
    sw = [branches(run(*base, "--align", "sync", "--phase", f"XPLL={k * 0.125e-9!r}"))["Rt0"]
          for k in range(20)]
    env = branches(run(*base, "--align", "sync", "--phase-sweep", "XPLL:20"))["Rt0"]
    rms_b = 8e-3 * np.sqrt((1e-9 - R_ + edges) / 10e-9)
    hand_async = np.sqrt((0.8e-3 + 2e-3) ** 2 + (rms_b ** 2 - 0.64e-6) + (rms_pll ** 2 - 4e-6))
    check("async rms on Rt0 vs hand (unrelated clocks, long-time)", al["async"]["rms"],
          hand_async, 1e-9)
    check("inphase rms on Rt0 vs hand (the sum of the pins' rms)", al["inphase"]["rms"],
          rms_b + rms_pll, 1e-9)
    check("phase-sweep rms = the worst of the 20 sync phases", env["rms"],
          max(x["rms"] for x in sw), 1e-12)
    check("phase-sweep peak = the worst of the 20 sync phases", env["peak"],
          max(x["peak"] for x in sw), 1e-12)
    check("inphase rms bounds every sync phase", al["inphase"]["rms"],
          max(x["rms"] for x in sw), cmp="ge")
    check("async rms is NOT an upper bound for related clocks (sync max exceeds it)",
          max(x["rms"] for x in sw), al["async"]["rms"], cmp="ge")
    nums["alignment_Rt0_mA"] = {
        "inphase": {k: v * 1e3 for k, v in al["inphase"].items()},
        "async": {k: v * 1e3 for k, v in al["async"].items()},
        "sync_phases": {"rms_min": min(x["rms"] for x in sw) * 1e3,
                        "rms_max": max(x["rms"] for x in sw) * 1e3,
                        "peak_min": min(x["peak"] for x in sw) * 1e3,
                        "peak_max": max(x["peak"] for x in sw) * 1e3},
        "hand_async_rms": hand_async * 1e3, "hand_inphase_rms": (rms_b + rms_pll) * 1e3}

    # ---- 5. the top-level self-consistency loop, with the commands
    nums["self_consistency"] = loop(out)
    nums["checks"] = CHECKS
    nums["passed"] = all(c["ok"] for c in CHECKS)
    with open(os.path.join(out, "measured.json"), "w", encoding="utf-8") as f:
        json.dump(nums, f, indent=1)
    for c in CHECKS:
        print(f"  {'ok ' if c['ok'] else 'BAD'} {c['check']}: got {c['got']:.9g}, "
              f"want {c['want']:.9g} ({c['cmp']})")
    print("ALL CHECKS PASSED" if nums["passed"] else
          f"{sum(not c['ok'] for c in CHECKS)} CHECK(S) FAILED")
    return 0 if nums["passed"] else 1


R_LOAD, V_NOM = 50.0, 1.0


def loop(out: str) -> dict:
    """The documented loop on the ADC alone: characterise the block at the
    voltage it is delivered, re-run the top, repeat until the pin voltage
    settles. The block 'simulation' is an ohmic stand-in (I = V / 50 ohm,
    shared by the four devices), so the answer has a closed form:
    I* = V_nom / (50 + 1.5) (0.5 + 1.0 ohm from the pad to the pin)."""
    d = os.path.join(out, "loop")
    os.makedirs(d, exist_ok=True)
    P = os.path.join(out, "process.yaml")
    t = np.arange(0, MI.STOP + MI.DT / 2, MI.DT)
    v, passes = V_NOM, []
    for k in range(8):
        i_tot = v / R_LOAD
        raw = os.path.join(d, f"adc_{k}.raw")
        MI.write_nutascii(raw, t, {f"M{j}:s": np.full_like(t, i_tot / 4) for j in range(4)})
        ab = os.path.join(d, f"adc_{k}.abstract.json")
        pvf = os.path.join(d, f"pv_{k}.json")
        rc = quiet(cli.main, ["abstract", os.path.join(out, "adc.dspf"), "--process", P,
                              "--cc-sim", raw, "--mode", "cc-grid", "--period", "10e-9",
                              "--points", "4000",
                              "--vdd", str(V_NOM), "-o", ab])
        assert rc == 0, rc
        rc = quiet(cli.main, ["run", os.path.join(out, "top.dspf"), "--process", P,
                              "--net", "VDD", "--abstract", f"{ab}@XADC",
                              "--port-voltages-out", pvf, "--summary-out",
                              os.path.join(d, f"top_{k}.json")])
        assert rc == 0, rc
        with open(pvf, encoding="utf-8") as f:
            ent = json.load(f)["instances"]["XADC"]["nets"]["VDDA"]
        with open(os.path.join(d, f"top_{k}.json"), encoding="utf-8") as f:
            codes = sorted({n["code"] for n in json.load(f)["notices"]})
        passes.append({"pass": k, "v_block_V": v, "i_mA": i_tot * 1e3,
                       "droop_mV": ent["droop_mV"]["max"], "W805": "EMIR-W805" in codes})
        v_new = ent["v_delivered_V"]
        if abs(v_new - v) < 1e-9:
            break
        v = v_new
    i_star = V_NOM / (R_LOAD + 1.5)
    check("self-consistent block current vs the closed form", v / R_LOAD, i_star, 1e-6)
    check("pass 0 over-reads the current (the conservative direction)",
          passes[0]["i_mA"] * 1e-3, i_star, cmp="ge")
    return {"passes": passes, "converged_mA": v / R_LOAD * 1e3, "closed_form_mA": i_star * 1e3,
            "pass0_over_read": (V_NOM / R_LOAD) / i_star - 1, "n_passes": len(passes)}


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "out")))
