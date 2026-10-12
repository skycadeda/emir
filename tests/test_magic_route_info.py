"""What the magic route and the viewers say about themselves:

* `emir info` and `emir next --gds` say which magic the SKY130 route runs,
  and a layout with no patched magic is not planned for extraction;
* the Explorer never formats a metric with no number (nil) as one;
* no text names "the gate network" as a cause (example 08's README).

Every number is invented or computed here.
"""
from __future__ import annotations

import json
import os

import pytest
from _helpers import ROOT, TINY, emir, fake_magic


def jdoc(p, rc=0):
    assert p.returncode == rc, (p.returncode, p.stdout[-2000:], p.stderr[-2000:])
    return json.loads(p.stdout)


# ------------------------------------------------------------ the Explorer


def test_the_explorer_never_formats_a_metric_that_was_not_checked():
    """A nil ratio (a metric the run did not compute) is said in words
    in a row, the detail and the counts, never formatted as a number, and the
    metric field offers what the run checked."""
    from emir.skillapp import APP

    def body(name):
        i = APP.index(f"procedure( {name}(")
        return APP[i:APP.index("\nprocedure(", i + 1)]
    for name in ("emirRowText", "emirDetailText"):
        b = body(name)
        assert "if( numberp(emirVRat(v metric))" in b and "emirNotChecked" in b, name
    c = body("emirCountText")
    assert c.count("numberp(emirVRat(x metric))") >= 2 and "emirNotChecked" in c
    assert "numberp(emirVRat(v metric))" in body("emirRedraw")
    assert '?choices emirMetricChoices()' in APP and '?choices list("avg" "rms" "peak")' \
        not in APP
    assert 'emirNotChecked = "not checked"' in APP



# ------------------------------------------------------------ the texts


def test_no_text_names_the_gate_network_as_a_cause():
    """Example 08's DC operating point, with no gate current, reads as high as
    its transient: the rails' resistance under the sources is the cause, and
    no file speaks of "the gate network" any more (the tests excepted)."""
    import subprocess
    p = subprocess.run(["git", "-C", ROOT, "grep", "-l", "-i", "gate network", "--", ".",
                        ":!tests"], capture_output=True, text=True)
    if p.returncode not in (0, 1):
        pytest.skip("not a git checkout")
    assert p.stdout.split() == []


# ------------------------------------------------------------ the magic the route runs


def test_info_says_which_magic_the_route_runs(tmp_path):
    geo = fake_magic(tmp_path, patched=True)
    env = {**os.environ, "EMIR_MAGIC": geo, "PDK_ROOT": "/pdk/root"}
    d = jdoc(emir("info", "--format", "json", env=env))
    mr = d["magic_route"]
    assert mr["path"] == geo and mr["source"] == "$EMIR_MAGIC" and mr["writes_geometry"]
    assert {"key": "PDK_ROOT", "value": "/pdk/root", "source": "$PDK_ROOT"} in d["settings"]
    # the tools' magic entry names the magic the route runs, which the stock
    # `magic` on $PATH often is not; the text says so on the magic line
    mag = next(t for t in d["tools"] if t["name"] == "magic")
    assert mag["route_path"] == geo
    assert mag["writes_geometry"] == (mag["path"] is not None and
                                      os.path.realpath(mag["path"]) == os.path.realpath(geo))
    text = emir("info", env=env).stdout
    line = next(ln for ln in text.splitlines() if ln.split()[:1] == ["magic"])
    assert mag["writes_geometry"] or f"the SKY130 route runs magic-geo: {geo}" in line
    # none that writes geometry: said, with what was tried
    (tmp_path / "stock").mkdir()
    stock = fake_magic(tmp_path / "stock", patched=False)
    path = os.pathsep.join(p for p in os.environ.get("PATH", "").split(os.pathsep)
                           if p and not any(os.path.exists(os.path.join(p, n)) for n in
                                            ("magic", "magic-8.3.515-geo")))
    env = {**os.environ, "EMIR_MAGIC": stock, "PATH": path}
    env.pop("PDK_ROOT", None)
    d = jdoc(emir("info", "--format", "json", env=env))
    mr = d["magic_route"]
    assert mr["path"] is None and not mr["writes_geometry"]
    assert mr["tried"][0]["result"] == "no geoout (unpatched)"
    assert {"key": "PDK_ROOT", "value": None, "source": "$PDK_ROOT"} in d["settings"]


def test_next_on_a_layout_without_a_patched_magic_asks_for_one(tmp_path):
    """No magic writes resistor geometry here: `emir next --gds` does not plan
    an extraction that would stop with EMIR-E643; it asks for the patched
    magic, with how to build it."""
    stock = fake_magic(tmp_path, patched=False)
    path = os.pathsep.join(p for p in os.environ.get("PATH", "").split(os.pathsep)
                           if p and not any(os.path.exists(os.path.join(p, n)) for n in
                                            ("magic", "magic-8.3.515-geo")))
    env = {**os.environ, "EMIR_MAGIC": stock, "PATH": path}
    d = jdoc(emir("next", "--gds", os.path.join(TINY, "tiny.gds"), "--process",
                  os.path.join(ROOT, "pdks", "sky130.yaml"), "--cell", "tiny", "--format",
                  "json", env=env))
    ids = [s["id"] for s in d["steps"]]
    assert "extract-magic" not in ids and "next-dspf" not in ids
    need = next(m for m in d["missing"] if "geoout patch" in m["what"])
    assert "EMIR-E643" in need["why"] and "git clone -b extresist-geoout" in need["how"]
    assert d["facts"]["magic_route"]["writes_geometry"] is False


def test_next_on_a_layout_says_which_magic_it_would_run(tmp_path):
    geo = fake_magic(tmp_path, patched=True)
    env = {**os.environ, "EMIR_MAGIC": geo}
    d = jdoc(emir("next", "--gds", os.path.join(TINY, "tiny.gds"), "--process",
                  os.path.join(ROOT, "pdks", "sky130.yaml"), "--cell", "tiny", "--format",
                  "json", env=env))
    assert d["facts"]["magic_route"]["path"] == geo
    assert "pdk_root" in d["facts"]
    assert any(a.startswith(f"magic for this route: {geo}") for a in d["advice"])
