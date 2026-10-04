"""The EM verdict's bases (`--em-basis`, `emir/decap.py`) and the default:
the one place to change it. Standard library only, so the schemas (which
pre-commit generates without numpy) read the same constants.

* ``decap-free`` -- the static superposition (the 1.x verdict);
* ``decap-aware`` -- the dynamic solve with the decaps;
* ``worst`` -- per segment and metric the larger of the two.
"""
from __future__ import annotations

#: the verdict bases, in the order reports list them
BASES = ("decap-free", "decap-aware", "worst")
#: the basis a run with --dynamic uses when --em-basis is not given
DEFAULT_BASIS = "worst"
#: the bases whose verdict needs decap-aware EM (so --dynamic)
NEEDS_DYNAMIC = ("decap-aware", "worst")
#: what each basis is, in a few words (the texts that name the default say
#: the default's own, never worst's under another name)
SHORT = {"decap-free": "the static superposition",
         "decap-aware": "rms and peak from the dynamic solve with the decaps, avg static",
         "worst": "per segment the larger of decap-free and decap-aware"}


def reads_lower(basis: str, than: str = DEFAULT_BASIS) -> bool:
    """Whether `basis` reads lower than `than` on some segments: worst never
    does (per segment the larger of the other two); decap-free and decap-aware
    each do, against each other (the decaps' straps; the feed path) and
    against worst."""
    return basis != than and basis != "worst"


def not_default(basis: str) -> str:
    """Why a basis other than the default is the person's choice, in words."""
    return (f"--em-basis {basis} is not the default ({DEFAULT_BASIS})"
            + (" and reads lower than it on some segments" if reads_lower(basis) else ""))
