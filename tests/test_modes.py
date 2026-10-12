"""One method of a simulated run: rcc.

* a run on a simulation result is rcc whether or not `--mode rcc` is given;
  no other mode is accepted (a usage error that says so); a DC run takes
  none, and `--mode rcc` with DC currents is refused (EMIR-E506);
* `emir next` with a simulation plans rcc directly (no step asks for a mode);
* no printed command of the docs, the examples, the scripts or the agent
  layer uses a mode that is gone (cc-grid, cc, auto), and none of the removed
  commands (`emir abstract`, `emir probe`, `emir split-stub`);
* the codes of the removed methods are retired, and explained as such.
"""
from __future__ import annotations

import json
import os
import re
import subprocess

import pytest
from _helpers import CUR, DSPF, PROC, ROOT, emir, rcc_result


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


#: a mode or a command 1.1 does not have, in a command line
_GONE = re.compile(r"--mode[ =](cc-grid|cc|auto)\b|\bemir (abstract|probe|split-stub)\b")


def test_no_printed_command_uses_a_removed_mode_or_command():
    """Docs, examples, scripts and the agent layer: no command shows a mode
    other than rcc, or a removed command (each would be refused)."""
    if not os.path.isdir(os.path.join(ROOT, ".git")) and not os.path.isfile(
            os.path.join(ROOT, ".git")):
        pytest.skip("not a git checkout")
    bad = []
    for f in _tracked(".md", ".sh", ".yaml", ".tmpl", ".ocn", ".il", ".py", ".json"):
        if f.startswith("tests/"):
            continue
        text = open(os.path.join(ROOT, f), encoding="utf-8", errors="replace").read()
        for ln in text.splitlines():
            # a retired code's reason, or the changelog's line, names what went
            if _GONE.search(ln) and not re.search(r"removed|retired|preserved on|is gone", ln):
                bad.append(f"{f}: {ln.strip()[:160]}")
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


def test_a_simulated_run_needs_no_mode(tmp_path):
    rcc = rcc_result(tmp_path)
    d = jdoc(emir("run", DSPF, "--process", PROC, "--net", "VDD", "--cc-sim", rcc,
                  "--period", "1e-9", "--format", "json"))
    assert d["nets"][0]["mode"] == "rcc" and d["current_source"]["kind"] == "simulation"
    d = jdoc(emir("check", DSPF, "--process", PROC, "--net", "VDD", "--cc-sim", rcc,
                  "--period", "1e-9", "--format", "json"))
    assert d["passed"]
    d = jdoc(emir("save-lines", DSPF, "--format", "json"))
    assert d["mode"] == "rcc" and d["lines"][0].startswith("save Rm1:1")
    d = jdoc(emir("save-lines", DSPF, "--mode", "rcc", "--format", "json"))
    assert d["mode"] == "rcc"


@pytest.mark.parametrize("argv", [["save-lines", DSPF],
                                  ["simulate", "tb.cir", "--sim", "xyce", "--dspf", DSPF],
                                  ["run", DSPF, "--process", PROC, "--cc-sim", "r.raw"],
                                  ["check", DSPF, "--process", PROC, "--cc-sim", "r.raw"],
                                  ["next", "--dspf", DSPF]])
@pytest.mark.parametrize("mode", ["cc-grid", "cc", "auto"])
def test_no_other_mode_is_accepted(argv, mode, tmp_path):
    d = jdoc(emir(*argv, "--mode", mode, "--format", "json", cwd=tmp_path), rc=2)
    assert d["code"] == "EMIR-E507", argv
    assert "one method of a simulated run, rcc" in d["message"]


def test_a_dc_run_takes_no_mode():
    jdoc(emir("run", DSPF, "--process", PROC, "--net", "VDD", "--currents", CUR,
              "--format", "json"))
    d = jdoc(emir("check", DSPF, "--process", PROC, "--net", "VDD", "--currents", CUR,
                  "--mode", "rcc", "--format", "json"), rc=2)
    assert "EMIR-E506" in [f["code"] for f in d["findings"] if f["level"] == "FAIL"]


@pytest.mark.parametrize("cmd", ["abstract", "probe", "split-stub"])
def test_the_removed_commands_are_unknown(cmd):
    d = jdoc(emir(cmd, DSPF, "--format", "json"), rc=2)
    assert d["code"] == "EMIR-E508"


@pytest.mark.parametrize("code", ["EMIR-E413", "EMIR-I205", "EMIR-I206", "EMIR-I403",
                                  "EMIR-I406", "EMIR-E520", "EMIR-E521", "EMIR-E530",
                                  "EMIR-E535", "EMIR-W535", "EMIR-E417", "EMIR-W403",
                                  "EMIR-E801", "EMIR-E814", "EMIR-E460", "EMIR-W465"])
def test_retired_codes_are_explained(code):
    d = jdoc(emir("explain", code, "--format", "json"))
    entry = d["codes"][0]
    assert entry["retired"] is True and entry["retired_reason"]


# ------------------------------------------------------------ emir next


def test_next_with_a_simulation_plans_rcc(tmp_path):
    d = jdoc(emir("next", "--dspf", DSPF, "--process", PROC, "--net", "VDD",
                  "--have-xyce", "--format", "json"))
    assert d["mode"]["chosen"] == "rcc" and "every branch current" in d["mode"]["why"]
    ids = [s["id"] for s in d["steps"]]
    assert "choose-mode" not in ids and "simulate" in ids
    assert not any("choices" in s for s in d["steps"])
    d = jdoc(emir("next", "--dspf", DSPF, "--process", PROC, "--net", "VDD",
                  "--cc-sim", rcc_result(tmp_path), "--period", "1e-9", "--format", "json"))
    assert d["mode"]["chosen"] == "rcc"
    assert [s["id"] for s in d["steps"]][-3:] == ["check", "run", "report"]


# ------------------------------------------------------------ the texts


def test_cited_test_files_exist():
    """A docstring or doc that points at a test file names one that exists."""
    bad = []
    for d in ("emir", "docs"):
        for root, _dirs, files in os.walk(os.path.join(ROOT, d)):
            for f in files:
                if not f.endswith((".py", ".md")):
                    continue
                p = os.path.join(root, f)
                for m in re.finditer(r"tests/(test_\w+\.py)", open(p, encoding="utf-8").read()):
                    if not os.path.isfile(os.path.join(ROOT, "tests", m.group(1))):
                        bad.append(f"{os.path.relpath(p, ROOT)}: {m.group(0)}")
    assert not bad, bad
