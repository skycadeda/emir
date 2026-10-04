"""rcc chooses its averaging window on the net's feed, not on the branch with
the largest peak (emir/rcc.py `feed_wave`). Found by the open-simulator proof
(scripts/opensim_proof.py, the inverter case): a contact's spike out-peaked
the feed segment, its own waveform repeated from the first period, and the
window took in the start-up period -- every average about 12 % LOW."""
import os

import numpy as np
import pytest
from _helpers import DSPF, PROC

from emir import rcc
from emir.dspf import Network, parse
from emir.engine import Process
from emir.errors import collecting
from emir.validate import _psf_tran

T = 1e-9


def tri(t, start, width, peak):
    """A triangle pulse in every period: `start` and `width` in periods."""
    ph = (t / T) % 1.0
    x = (ph - start) / width
    return np.where((x > 0) & (x < 1), peak * (1 - np.abs(2 * x - 1)), 0.0)


def branches(t):
    """KCL-consistent branch currents (A) of the sample rail: MP0 spikes 5 mA
    every period from the start; MP1, a decap beside it, supplies half the
    spike and recharges later (no net charge); MP3, at the far end, draws 2 mA
    pulses from the second period on."""
    i0 = tri(t, 0.10, 0.10, 5e-3)
    i1 = -tri(t, 0.10, 0.10, 2.5e-3) + tri(t, 0.30, 0.50, 0.5e-3)
    i3 = np.where(t >= T, tri(t, 0.55, 0.30, 2e-3), 0.0)
    i2 = np.zeros_like(t)
    out = {}
    for n in range(1, 13):
        first = (n - 1) // 3                       # taps at nodes 3, 6, 9, 12
        out[f"Rm{n}:1"] = sum((i0, i1, i2, i3)[first:])
    for k, i in enumerate((i0, i1, i2, i3)):
        out[f"Rc{k}:1"] = i
    return out


def test_the_window_is_chosen_on_the_feed(tmp_path):
    t = np.linspace(0.0, 4 * T, 4 * 2000 + 1)
    b = branches(t)
    # the premise: a contact out-peaks the feed segment
    assert np.abs(b["Rc0:1"]).max() > 1.5 * np.abs(b["Rm1:1"]).max()
    raw = _psf_tran(os.path.join(str(tmp_path), "rcc.tran"), t, b)
    d = parse(DSPF)
    proc = Process.load(PROC)
    with collecting():
        r = rcc.rcc_check(d, "VDD", proc, raw, "VDD", period=T, log=lambda *a: None)
    assert r["window"]["t0"] == pytest.approx(T)           # the start-up period is out
    i = [x["name"] for x in r["network"].res].index("Rm1")
    # the feed's steady average: MP0 0.25 mA + MP3 0.30 mA (the decap nets zero)
    assert r["Ie"]["avg"][i] == pytest.approx(0.25e-3 + 0.30e-3, rel=2e-3)


def test_the_feed_is_the_current_at_the_reference_node():
    d = parse(DSPF)
    nw = Network(d, "VDD")
    t = np.linspace(0.0, 4 * T, 801)
    b = branches(t)
    B = np.array([b[f"{x['name']}:1"] for x in nw.res])
    assert np.allclose(rcc.feed_wave(nw, B, "VDD"), b["Rm1:1"])
    # a node nothing enters (KCL: zero) falls back to the largest-peak branch
    assert np.allclose(rcc.feed_wave(nw, B, "VDD:4"), b["Rc0:1"])
