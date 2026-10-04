"""`emir next` rules for the Cadence flow (imported by `emir.plan`).

They act when Cadence tools are on this machine (`emir info` finds Spectre,
Virtuoso or Quantus; `--have-cadence` / `--no-have-cadence` override), or
when the facts name a layout to extract:

* no DSPF yet -- with `gds`, `cdl` and `cell` among the facts: the
  `emir extract` steps (the dry run, then `--run`, which needs the person's
  go-ahead: PVS and Quantus are licensed); without them: how to get one;
* a block of ADC-top / PLL-top size -- the two extractions from one LVS
  (R-only for the grid, C-only coupled for the CC netlist) and cc-grid;
* a Spectre simulation step -- the testbench templates and the save-lines
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
        kinds = ["r_only"] + (["c_only_coupled"] if f.get("mode") == "cc-grid" else [])
        for k in kinds:
            base = ["extract", "--gds", gds, "--cdl", cdl, "--cell", cell, "--kind", k]
            plan.add(f"extract-plan:{k}", command_line(base, f.fmt()),
                     f"the PVS LVS + Quantus plan for the {k} DSPF: where the tools and "
                     f"decks were found and what is missing; runs nothing")
            plan.add(f"extract:{k}", command_line(base, "--run", f.fmt()),
                     f"run LVS (it must MATCH) and Quantus, kind {k}; a second kind "
                     f"reuses the matched LVS",
                     licensed_tool="quantus", long_run=True, after=[f"extract-plan:{k}"])
        return
    if _cadence(f):
        plan.note("Cadence site: the DSPF comes from `emir extract --gds <layout.gds> --cdl "
                  "<netlist.cdl> --cell <cell> --kind r_only` -- it prints the PVS LVS + "
                  "Quantus plan and runs them only with --run (licensed tools: ask the "
                  "person first); --kind c_only_coupled from the same LVS gives the CC "
                  "netlist of a big block")


@rule(45, "cadence-bigblock")
def _bigblock(f, plan):
    """ADC top, PLL top: R-only grid plus a CC simulation, both from one LVS."""
    if not (_cadence(f) and f.size()["class"] == "big"):
        return
    plan.note("Cadence, big block: extract twice from one LVS with `emir extract` -- "
              "--kind r_only for the grid DSPF that `emir run` screens and --kind "
              "c_only_coupled for the netlist to simulate (no resistance: the rails are "
              "ideal and it converges); simulate the C-only netlist with the lines of "
              "`emir save-lines <r_only.dspf> --mode cc-grid --prefix <instance>. --out "
              f"emir_save.scs` ({_testbenches()}/tb_cc.scs.tmpl) and run --mode cc-grid")


@rule(55, "cadence-testbench")
def _testbench(f, plan):
    """A Spectre simulation is planned: the templates and the include file."""
    sim = next((s for s in plan.steps if s["id"] == "simulate"), None)
    if sim is None or not sim["command"].startswith("spectre "):
        return
    tb = _testbenches()
    plan.note(f"Spectre testbench templates: {tb}/tb_rcc.scs.tmpl (rcc: the RC "
              f"DSPF itself is simulated) and {tb}/tb_cc.scs.tmpl (cc-grid: the "
              f"C-only DSPF or the schematic); add --prefix <instance>. and --out "
              f"emir_save.scs to the save-lines step and include that file; keep "
              f"-format psfascii (binary PSF is not read). Or let `emir simulate "
              f"<testbench.scs> --sim spectre --dspf <dspf> --mode {f.get('mode')} "
              f"--prefix <instance>.` write "
              f"the deck (it includes the testbench and the save statements) and print "
              f"the command; with --run it runs Spectre (ask the person first)")


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
