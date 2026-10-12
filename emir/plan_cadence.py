"""`emir next` rules for the Cadence flow (imported by `emir.plan`).

They act when Cadence tools are on this machine (`emir info` finds Spectre,
Virtuoso or Quantus; `--have-cadence` / `--no-have-cadence` override), or
when the facts name a layout to extract:

* no DSPF yet -- with `gds`, `cdl` and `cell` among the facts: the
  `emir extract` steps (the dry run, then `--run`, which needs the person's
  go-ahead: PVS and Quantus are licensed); without them: how to get one;
* a Spectre simulation step -- the testbench template and the save-lines
  file (`--prefix`, `--out`);
* `--viewer` -- how the person opens the Explorer from Virtuoso.

`emir next` takes no --gds / --cdl yet; the extraction steps are planned for
callers that pass them as facts (`plan.Facts(gds=..., cdl=..., cell=...)`).
"""
from __future__ import annotations

from .output import command_line
from .plan import rule

#: where the Spectre testbench templates are: the clone's, else the web page
#: of the repository's (a wheel does not ship agent/)
def _testbenches() -> str:
    from .repo import ref
    return ref("agent/cadence/spectre/")


def _cadence(f) -> bool:
    return bool(f.tools.get("have_cadence"))


@rule(15, "cadence-extract")
def _extract(f, plan):
    """No DSPF yet: plan the extraction, or say how to get one."""
    if f.get("dspf"):
        return
    gds, cdl, cell = f.get("gds"), f.get("cdl"), f.get("cell")
    if gds and cdl and cell:
        k = "rc_coupled"
        base = ["extract", "--gds", gds, "--cdl", cdl, "--cell", cell, "--kind", k]
        plan.add(f"extract-plan:{k}", command_line(base, f.fmt()),
                 f"the PVS LVS + Quantus plan for the {k} DSPF (the RC netlist rcc "
                 f"simulates): where the tools and decks were found and what is missing; "
                 f"runs nothing")
        plan.add(f"extract:{k}", command_line(base, "--run", f.fmt()),
                 f"run LVS (it must MATCH) and Quantus, kind {k}",
                 licensed_tool="quantus", long_run=True, after=[f"extract-plan:{k}"])
        return
    if _cadence(f):
        plan.note("Cadence site: the DSPF comes from `emir extract --gds <layout.gds> --cdl "
                  "<netlist.cdl> --cell <cell> --kind rc_coupled` -- it prints the PVS LVS + "
                  "Quantus plan and runs them only with --run (licensed tools: ask the "
                  "person first); --kind r_only gives a DSPF for DC currents alone")


@rule(55, "cadence-testbench")
def _testbench(f, plan):
    """A Spectre simulation is planned: the templates and the include file."""
    sim = next((s for s in plan.steps if s["id"] == "simulate"), None)
    if sim is None or not sim["command"].startswith("spectre "):
        return
    tb = _testbenches()
    plan.note(f"Spectre testbench template: {tb}/tb_rcc.scs.tmpl (the RC DSPF itself is "
              f"simulated); add --prefix <instance>. and --out emir_save.scs to the "
              f"save-lines step and include that file; keep -format psfascii (binary PSF "
              f"is not read). Or let `emir simulate <testbench.scs> --sim spectre --dspf "
              f"<dspf> --prefix <instance>.` write the deck (it includes the testbench and "
              f"the save statements) and print the command; with --run it runs Spectre "
              f"(ask the person first)")


@rule(75, "cadence-viewer")
def _viewer(f, plan):
    """--viewer at a Cadence site: the person opens the Explorer."""
    if not (f.get("viewer") and _cadence(f)):
        return
    from .repo import ref
    plan.note("in Virtuoso the person picks EMIR > Load results... on the run directory "
              f"(the menu comes from {ref('agent/cadence/skill/emir_init.il')} with "
              "EMIR_HOME set to a clone, or "
              "from the stub `emir gui-install` writes); the Explorer opens the layout "
              "read-only and changes nothing; its colours need emir_display.drf appended "
              "to ~/display.drf")
