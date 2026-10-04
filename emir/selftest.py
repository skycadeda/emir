"""`emir selftest` -- prove the install works here, offline, in a few seconds.

Runs the checker and the analysis on the bundled sample rail with DC
currents, writes reports and viewer data to a scratch directory, and compares
the numbers with the ones the sample was designed to produce. No simulator,
no PDK, no Cadence tools are touched.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from importlib.resources import files

from .errors import EmirError

SAMPLE_CURRENTS = ('{"VDD": 0.004, "MP0:s": -0.001, "MP1:s": -0.001, '
                   '"MP2:s": -0.001, "MP3:s": -0.001}')


def sample_dir() -> str:
    """The bundled sample (`rail.dspf`, `process.yaml`), as a directory path.

    It is package data (`emir/data/sample`), so it is found the same way from a
    clone, a pip install and the offline bundle.
    """
    p = files("emir") / "data" / "sample"
    if not p.is_dir():
        raise EmirError("EMIR-E902", "emir/data/sample is missing from this installation")
    return os.fspath(p)


def sample_paths() -> tuple[str, str]:
    """`(dspf, process)` paths of the bundled sample."""
    sd = sample_dir()
    return os.path.join(sd, "rail.dspf"), os.path.join(sd, "process.yaml")


def main(argv=None, sink=None) -> int:
    """`emir selftest`. Exit 0 when every check passes, else 70 (EMIR-E902:
    the install is broken). With `sink` (the CLI's `output.Out`), the
    `emir.selftest/1` document is emitted to it."""
    from .output import Out, document, parser
    ap = parser("emir selftest",
                description="Offline proof that the install works (a few seconds).")
    ap.add_argument("--keep", action="store_true", help="keep the output directory")
    ap.add_argument("--out", default=None, help="output directory (default: temp)")
    a = ap.parse_args(argv)
    sink = sink or Out(a.format)
    checks: list = []
    current = [""]

    def finish(ok: bool, out_dir: str | None) -> int:
        from .errors import EXIT_INTERNAL
        sink.emit(document("selftest", passed=bool(ok), checks=checks,
                           code=None if ok else "EMIR-E902",
                           out_dir=out_dir, kept=bool(out_dir)))
        return 0 if ok else EXIT_INTERNAL

    dspf, proc = sample_paths()
    out = a.out or tempfile.mkdtemp(prefix="emir_selftest_")
    os.makedirs(out, exist_ok=True)
    ok = True

    def step(msg):
        current[0] = msg
        print(f"== {msg}")

    def result(cond, msg):
        nonlocal ok
        print(f"   {'ok  ' if cond else 'FAIL'} - {msg}")
        checks.append({"step": current[0], "ok": bool(cond), "message": msg})
        ok = ok and bool(cond)

    step("1. core imports (numpy / scipy / yaml only)")
    try:
        import emir.dspf, emir.engine, emir.rcc, emir.ccsim, emir.dynir, emir.layoutview  # noqa
        heavy = [m for m in ("matplotlib", "klayout") if m in sys.modules]
        result(not heavy, f"core path is clean{'' if not heavy else ' (pulled in ' + ', '.join(heavy) + ')'}")
    except Exception as e:                       # noqa: BLE001
        result(False, f"import failed: {e}")
        return finish(False, out)

    step("2. input checker on the sample")
    from .check import check_inputs
    rep = check_inputs(dspf, process=proc, nets=["VDD"], currents=SAMPLE_CURRENTS,
                       viewer=os.path.join(out, "viewer"), lib="SAMPLE")
    result(rep.passed, f"{rep.n_fail} FAIL, {rep.n_warn} WARN "
           f"(the sample process file is expected to warn that it is illustrative)")
    bad = check_inputs(dspf, process=proc, nets=["NOPE"], currents='{"VDD": 1}')
    result(not bad.passed and bad.n_fail >= 2, "a bad net and unbalanced currents are caught")

    step("3. analysis with DC currents, HTML + viewer data")
    from .dspf import build_parser, run
    # the HTML report needs no extra package (inline SVG, no matplotlib)
    html = ["--html", os.path.join(out, "nets")]
    args = build_parser().parse_args(
        [dspf, "--net", "VDD", "--process", proc, "--currents", SAMPLE_CURRENTS,
         "--viewer", os.path.join(out, "viewer"), "--lib", "SAMPLE",
         "--json", os.path.join(out, "summary.json"), *html])
    import io
    import contextlib
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            solved = run(args)
    except EmirError as e:
        print(buf.getvalue()[-2000:])
        result(False, f"run stopped: {e}")
        return finish(False, out)
    r = solved["VDD"]
    # The expectations, by hand (every input is an invented round number):
    #   wire  Rm1..Rm12: 5 um x 0.75 um Metal1 at 0.15 ohm/sq (100 C) = 1.0 ohm
    #   via   Rc0..Rc2: 2 cuts x 24 ohm/cut in parallel = 12 ohm; Rc3: 1 cut = 24 ohm
    #   4 mA enter at VDD, 1 mA leaves at each MP pin, so Rm1-3 carry 4 mA,
    #   Rm4-6 3 mA, Rm7-9 2 mA, Rm10-12 1 mA, and each contact 1 mA.
    #   IR: the worst node is MP3:s, the far end through the one-cut contact:
    #       1.0 ohm x 3 x (4 + 3 + 2 + 1) mA + 24 ohm x 1 mA = 30 + 24 = 54.0 mV
    #   EM, DC so avg = rms = peak and the smallest limit (avg) governs:
    #       wire limit 3.2 mA/um x 0.75 um = 2.4 mA -> 4/2.4 = 1.667x FAIL (Rm1-3,
    #       the worst), 3/2.4 = 1.250x FAIL (Rm4-6), 2/2.4 = 0.833x WARN (Rm7-9),
    #       1/2.4 = 0.417x OK (Rm10-12);
    #       contact limit 0.75 mA/cut: 0.5 mA/cut -> 0.667x OK (Rc0-2), 1 mA on
    #       one cut -> 1.333x FAIL (Rc3, via-limited while its wire is OK).
    #   Violations at the 0.8 warn fraction: 9 wires + Rc3 = 10; every one of
    #   the 16 resistors is a viewer segment.
    result(abs(r["max_ratio"]["all"] - 4.0 / 2.4) < 1e-6,
           f"worst ratio {r['max_ratio']['all']:.3f}x (expected 1.667x)")
    result(abs(r["max_ratio"]["via"] - 1.0 / 0.75) < 1e-6,
           f"worst via ratio {r['max_ratio']['via']:.3f}x (expected 1.333x)")
    result(abs(r["ir_drop"] * 1e3 - 54.0) < 1e-3, f"IR drop {r['ir_drop'] * 1e3:.4f} mV (expected 54.0000)")
    result(len(r["clusters"]) == 10, f"{len(r['clusters'])} violating segments (expected 10)")

    step("4. viewer data")
    viz = os.path.join(out, "viewer", "emir_viz.json")
    d = json.load(open(viz))
    em, ir = d["em"], d["ir"]
    result(len(em) == 16, f"{len(em)} EM segments (expected 16)")
    result(all(c["hint"] == "" for c in em if c["ratio"] < 1.0), "no fix hint on a passing segment")
    result(min(x["mv"] for x in ir) >= 0.0, "IR drop is a magnitude")
    for f in ("emir_data.il", "emir_explorer.il", "emir_em_bar.png", "emir_ir_bar.png"):
        result(os.path.exists(os.path.join(out, "viewer", f)), f"viewer/{f} written")
    result(os.path.exists(os.path.join(out, "nets", "index.html")), "nets/index.html written")
    s = json.load(open(os.path.join(out, "summary.json")))
    result(s["nets"][0]["verdict"] == "FAIL", f"summary verdict {s['nets'][0]['verdict']} (expected FAIL)")

    step("5. Virtuoso SKILL files")
    from .skillgui import MAIN, site_stub
    from .skillapp import APP
    for name, src in (("emir_explorer.il", APP), ("emir_main.il", MAIN),
                      ("emir_site.il", site_stub("/x/emir_main.il", "/x/bin/emir", "/x"))):
        result(_parens_balanced(src), f"{name}: parentheses balance")

    print()
    if ok:
        print(f"SELFTEST PASSED -- outputs in {out}" if a.keep or a.out else "SELFTEST PASSED")
    else:
        print(f"SELFTEST FAILED -- outputs kept in {out} [EMIR-E902]")
    kept = out if (a.keep or a.out or not ok) else None
    if not kept:
        shutil.rmtree(out, ignore_errors=True)
    return finish(ok, kept)


def _parens_balanced(src: str) -> bool:
    depth, instr, esc, i = 0, False, False, 0
    while i < len(src):
        c = src[i]
        if instr:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                instr = False
        else:
            if c == ";":
                j = src.find("\n", i)
                i = j if j >= 0 else len(src)
                continue
            if c == '"':
                instr = True
            elif c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth < 0:
                    return False
        i += 1
    return depth == 0


if __name__ == "__main__":
    raise SystemExit(main())
