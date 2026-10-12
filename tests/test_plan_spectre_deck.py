"""`emir next --netlist <a Spectre deck>` on a machine with Xyce and no
Spectre: the deck was handed to the open-simulator route, parsed as SPICE,
and the plan reported a false 'no .tran (EMIR-E462)' blocker for a deck
whose transient is the Spectre statement `tr tran stop=40n` (the eval card
first-spectre-run, its spectre_site fixture)."""
import os
import sys

import pytest
from _helpers import ROOT

from emir import plan as P
from emir.opensim import is_spectre

sys.path.insert(0, os.path.join(ROOT, "agent", "evals"))
import fixtures  # noqa: E402


def facts(inputs, **kw):
    f = P.Facts(dspf=os.path.join(inputs, "rail.dspf"),
                process=os.path.join(inputs, "process.yaml"),
                netlist=os.path.join(inputs, "tb.scs"), net="VDD", period=4e-8, **kw)
    t = f.tools                        # this machine's, then: Xyce here, Spectre not
    t["spectre"]["found"] = False
    t["have_cadence"], t["have_xyce"] = False, True
    return f


@pytest.fixture
def inputs(tmp_path):
    out = str(tmp_path / "inputs")
    fixtures.build(["spectre_site"], out)
    return out


def test_a_spectre_deck_is_recognised(inputs, tmp_path):
    assert is_spectre(os.path.join(inputs, "tb.scs"))
    spice = tmp_path / "tb.txt"
    spice.write_text("* simulator lang=spectre in a comment\nV1 a 0 1\n.tran 1n 10n\n")
    assert not is_spectre(str(spice))
    lang = tmp_path / "tb.net"
    lang.write_text("// a deck\nsimulator lang=spectre\nV1 (a 0) vsource dc=1\n")
    assert is_spectre(str(lang))


def test_next_plans_the_spectre_route_not_a_false_e462(inputs):
    plan = P.build(facts(inputs))
    text = " ".join(f"{m['what']} {m['why']}" for m in plan.missing)
    assert "E462" not in text and ".tran" not in text
    assert any(m["what"].startswith("Spectre, for the Spectre testbench") for m in plan.missing)


def test_a_spectre_deck_for_an_open_simulator_is_a_language_mismatch(inputs):
    plan = P.build(facts(inputs, sim="xyce"))
    assert any("xyce's language" in m["what"] for m in plan.missing)
    assert "E462" not in " ".join(m["why"] for m in plan.missing)
    assert not {"simulate", "check", "run"} & {s["id"] for s in plan.steps}
