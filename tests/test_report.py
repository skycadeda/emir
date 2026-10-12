"""`emir report`: the summary an agent relays to a person, on the sample
and on runs that leave things unchecked."""
import json
import re

import pytest
import yaml
from _helpers import CUR, DSPF, PROC, emir, rcc_result, rewrite_vias, sample_text, write

from emir.report import build, markdown


def _summary(tmp_path, *args, name="s.json"):
    out = tmp_path / name
    args = [str(a) for a in args]
    p = emir("run", *args, "--summary-out", out)
    assert p.returncode == 0, p.stdout + p.stderr
    return json.load(open(out)), out


def test_report_on_the_sample(tmp_path):
    s, out = _summary(tmp_path, DSPF, "--net", "VDD", "--process", PROC, "--currents", CUR)
    p = emir("report", "--from", out)
    assert p.returncode == 0, p.stderr
    md = p.stdout
    lines = md.splitlines()
    assert lines[0] == "# EMIR screening report: railblock"
    assert lines[2].startswith("**Verdict: FAIL** -- 1 net(s): 1 FAIL, 0 WARN, 0 CLEAN; "
                               "worst 1.667x on VDD.")
    assert "**Screening, not sign-off.**" in md and md.rstrip().splitlines()[-1].startswith(
        "Screening, not sign-off.")
    assert "- Mode: dc" in md and "- Currents: DC currents given with --currents" in md
    assert "- Analysis temperature: 100 C" in md
    # the worst wire and via of every net, failing or not
    assert "| VDD | FAIL | dc | avg | 1.667x | 1.667x | 1.333x | 54.000 mV drop |" in md
    # the worst items, where they are, with the fix in the engineer's units
    assert "| VDD | Rm1 | (2.500, 0.000) | Metal1 | wire | avg | 1.667x | Metal1: widen " \
           "0.750 -> 1.255 um (x1.67) |" in md
    assert "| VDD | Rc3 | (60.000, 0.000) | Cont | via | avg | 1.333x | Cont: 1 -> 2 cuts " \
           "(+1) |" in md
    assert "- VDD: 54.000 mV (from the feed)" in md
    # DC currents cannot show transients: the report says so (it once said
    # nothing was left unchecked)
    assert "Every item the run found was checked against a limit." not in md
    assert "- DC currents (--currents): avg = rms = peak by construction" in md
    assert "- [EMIR-W306] this is the bundled ILLUSTRATIVE sample process file" in md
    # which run this is
    assert "- Run: `emir run " in md and ", finished 20" in md


def test_no_fix_below_one(tmp_path):
    """Below 1.00x the arithmetic would say "narrow this wire": the report
    prescribes nothing there, and every fix it gives is a growth."""
    s, _ = _summary(tmp_path, DSPF, "--net", "VDD", "--process", PROC, "--currents", CUR)
    r = build(s, "x")
    assert len(r["worst_items"]) == 10
    for it in r["worst_items"]:
        if it["ratio"] < 1.0:
            assert it["fix"] is None, it
        else:
            m = re.search(r"x(\d+\.\d+)\)|(\d+) -> (\d+) cuts", it["fix"])
            assert m, it
            if m.group(1):
                assert float(m.group(1)) >= 1.0
            else:
                assert int(m.group(3)) > int(m.group(2))
    assert "none needed (below the limit" in markdown(r)


def test_what_was_not_checked_is_said(tmp_path):
    """Vias without a cut count, a layer without an rms or peak limit, a
    forced run: each gets its line."""
    dspf = write(tmp_path / "nocut.dspf", rewrite_vias(sample_text(), "$cont"))
    d = yaml.safe_load(open(PROC))
    del d["layers"]["Metal1"]["jmax_peak"], d["layers"]["Metal1"]["jmax_rms_k"]
    proc = write(tmp_path / "p.yaml", yaml.safe_dump(d))
    s, out = _summary(tmp_path, dspf, "--net", "VDD", "--process", proc, "--cc-sim",
                      rcc_result(tmp_path), "--period", "1e-9")
    nc = s["not_checked"]
    assert nc["vias_assumed_one_cut"] == {"VDD": ["Rc0", "Rc1", "Rc2", "Rc3"]}
    assert "unsaved_pins" not in nc
    assert {"name": "Metal1", "kind": "wire", "metrics": ["rms", "peak"]} in nc["limits_missing"]
    md = emir("report", "--from", out).stdout
    assert "VDD: 4 via(s) with no cut count, checked as ONE cut" in md
    assert "not saved by the simulation" not in md
    assert "layer Metal1: no rms, peak limit in the process file" in md
    assert "- Mode: rcc" in md and "a finished simulation result (--cc-sim)" in md


def test_a_forced_run_reports_what_it_was_forced_past(tmp_path):
    d = write(tmp_path / "now.dspf", re.sub(r" \$W=[0-9.]+ \$SI_W=[0-9.]+", "",
                                            sample_text()))
    s, out = _summary(tmp_path, d, "--net", "VDD", "--process", PROC, "--currents", CUR,
                      "--force")
    md = emir("report", "--from", out).stdout
    assert "VDD: 12 wire(s) with no width, NOT checked: Rm1" in md
    assert "the run was forced past 2 input-check FAIL(s): [EMIR-E203]" in md
    assert "; [EMIR-E210] 12 wire resistor(s) carry no width" in md


def test_report_reads_stdin_and_refuses_other_files(tmp_path):
    p = emir("run", DSPF, "--net", "VDD", "--process", PROC, "--currents", CUR,
             "--format", "json")
    import subprocess
    import sys
    r = subprocess.run([sys.executable, "-m", "emir", "report", "--from", "-", "--format",
                        "json"], input=p.stdout, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    d = json.loads(r.stdout)
    assert d["schema"] == "emir.report/1" and d["source"] == "-"
    assert d["ir_by_power_net"] == [{"net": "VDD", "ir_drop_mV": d["nets"][0]["ir_drop_mV"],
                                     "ir_kind": "drop", "ir_resistance": {
                                         "kind": "as extracted", "temp_C": 100.0,
                                         "stated": True}}]
    p = emir("report", "--from", PROC)
    assert p.returncode == 2 and "EMIR-E104" in p.stderr
    bad = write(tmp_path / "x.json", json.dumps({"schema": "emir.check/1"}))
    p = emir("report", "--from", bad, "--format", "json")
    assert p.returncode == 2 and json.loads(p.stdout)["code"] == "EMIR-E104"


@pytest.mark.parametrize("w,r,want", [(0.36, 1.0, "0.365"), (0.75, 5 / 3, "1.255"),
                                      (1.0, 1.2857142857, "1.290"), (0.2, 4.999, "1.000")])
def test_a_width_fix_goes_past_the_limit(w, r, want):
    """At exactly 1.00x an item still FAILs: 'widen 0.360 -> 0.360 um' changed
    nothing, and a width rounded to the nearest digit could land below the
    limit (1.25 -> 1.250 is exactly 1.00x, still a FAIL)."""
    from emir.layoutview import fix_hint
    hint = fix_hint({"ratio": r, "kind": "wire", "layer": "M1", "w": w})
    new = float(hint.split("->")[1].split()[0])
    assert hint.split("->")[1].split()[0] == want
    assert new > w * r - 1e-12 and new / (w * r) - 1 > -1e-12
    assert w * r / new < 1.0                    # the fixed wire passes


@pytest.mark.parametrize("n,r,want", [(1, 1.0, 2), (1, 1.3333, 2), (1, 2.0, 3), (10, 1.706, 18),
                                      (5, 2.6, 14)])
def test_a_cut_fix_goes_past_the_limit(n, r, want):
    from emir.layoutview import fix_hint
    hint = fix_hint({"ratio": r, "kind": "via", "layer": "V1", "cuts": n,
                     "cuts_from": "count"})
    assert hint.startswith(f"V1: {n} -> {want} cuts")
    assert n * r / want < 1.0


def test_the_html_report_shows_a_failing_via_and_each_fix(tmp_path):
    """A via is a zero-length segment: drawn as a line it was invisible, and
    the taps were drawn over it, so the screenshot's FAIL had no red on the
    map. The violations table had no fix column."""
    p = emir("run", DSPF, "--process", PROC, "--currents", CUR, "--html", tmp_path / "h")
    assert p.returncode == 0, p.stderr
    page = (tmp_path / "h" / "railblock_VDD.html").read_text(encoding="utf-8")
    svg = page.split('<svg id="net"', 1)[1].split("</svg>", 1)[0]
    assert 'class="via FAIL"' in svg
    # drawn after the taps and ports: on top
    assert svg.rindex('class="via FAIL"') > svg.rindex('class="tap"')
    assert "<td>Cont: 1 -&gt; 2 cuts (+1)</td>" in page and "<th>fix</th>" in page
    assert ">1 cut<" in page and "Voltus" not in page and "RedHawk" not in page
    index = (tmp_path / "h" / "index.html").read_text(encoding="utf-8")
    # the failing wire is as red as the failing via
    assert 'style="color:var(--fail);font-weight:700">1.667' in index
    assert 'style="color:var(--fail);font-weight:700">1.333' in index
