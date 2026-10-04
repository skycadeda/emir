"""`emir next` rules for the magic route: a GDS layout and no DSPF yet.

With `--gds` and no DSPF the plan extracts one with magic, the open-PDK
extractor (`emir extract --tool magic`): it needs a magic that writes each
resistor's geometry (`extresist geoout`, a public patch; `emir extract`
finds one or says how to build it, EMIR-E643), the PDK's magic technology
(the process file's `pdk:` name under $PDK_ROOT, or --magicrc) and the process
file's `magic:` section (magic's layer names, the devices' terminals). The
extraction writes the DSPF (also the RC netlist rcc simulates) and the CC
netlist cc-grid simulates; the next step is the DSPF plan, `emir next --dspf
<cell>.dspf`, which asks the person for the mode. With a DSPF as well, the DSPF
plan stands, and `--gds` draws the layout under the charts (`emir run --gds`).

Without `--gds` or `--dspf`, the plan says the route exists.
"""
from __future__ import annotations

import os

from .output import command_line
from .plan import rule
from .repo import pdk_files


def _process_data(path):
    """The process file's parsed data, or None when it cannot be read or
    parsed (the process preflight reports that, EMIR-E301: a plan never
    crashes on bad input)."""
    try:
        import yaml
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (OSError, ValueError, ImportError):
        return None
    except Exception as e:               # yaml.YAMLError is not a ValueError
        if type(e).__module__.split(".")[0] == "yaml":
            return None
        raise
    return data if isinstance(data, dict) else None


def _magic_section(path) -> bool:
    """Whether the process file has a `magic:` section."""
    data = _process_data(path)
    return isinstance(data, dict) and isinstance(data.get("magic"), dict)


@rule(15, "magic")
def _magic(f, plan):
    gds = f.get("gds")
    if not gds:
        if not f.get("dspf"):
            plan.note("no DSPF? With the GDS layout of a block on an open PDK and its process "
                      "file, magic extracts one: `emir next --gds <layout.gds> --process "
                      "<pdk.yaml>` plans `emir extract --tool magic`")
        return
    if f.get("dspf"):
        return                           # the DSPF plan stands (--gds: the chart's layout)
    plan.mode = {"chosen": None, "why": "the magic route: the DSPF is extracted first, and "
                                        "the mode is asked for with it"}
    if not os.path.isfile(gds):
        plan.need("a readable layout", f"--gds {gds!r} is not found", "check the path")
        plan.stop()
        return
    proc = f.get("process")
    if not proc:
        plan.need("a process file with the PDK's layers, EM limits and magic names",
                  "magic's layers are mapped onto the process file's (its `magic:` section), "
                  "and the limits and resistances come from it; emir never assumes one",
                  f"{pdk_files()} (sky130.yaml has a magic: section) or fill one from the "
                  f"PDK")
        plan.stop()
        return
    if os.path.isfile(proc) and _magic_section(proc):
        from .errors import EmirError
        from .magic import process_binaries
        try:
            process_binaries(_process_data(proc))
        except EmirError as e:           # what emir check refuses is not planned
            plan.need("a process file whose magic: binary: names a magic program", str(e),
                      "name a magic program on $PATH by its bare name in the process file, "
                      "or give the path with --magic, $EMIR_MAGIC or the site file (EMIR-E648)")
            plan.stop()
            return
    if os.path.isfile(proc) and not _magic_section(proc):
        plan.note(f"{proc} has no `magic:` section: magic's layer names map only where "
                  f"they equal (or fold onto) the file's, and the devices' pins are named "
                  f"t1, t2 ... (EMIR-W643); pdks/TEMPLATE.yaml documents the section")
    cell = f.get("cell")
    fmt = f.fmt()
    out = f"emir_out/extract_{cell}" if cell else "emir_out/extract"
    args = ["extract", "--tool", "magic", "--gds", gds, *(["--cell", cell] if cell else []),
            "--process", proc, "--out", out]
    plan.add("extract-magic", command_line(args, fmt),
             "extract the block with magic (open source, seconds on a small block): every "
             "resistor one drawn segment with its layer, width and place; it writes the "
             "DSPF (also the RC netlist an rcc simulation runs) and the CC netlist "
             "(<cell>.cc.spice) a cc-grid simulation runs, and exits 3 (EMIR-E643) with "
             "how to build the magic it needs when there is none")
    dspf = f"{out}/{cell}.dspf" if cell else f"{out}/<cell>.dspf"
    plan.add("next-dspf", command_line("next", "--dspf", dspf, "--process", proc,
                                       "--gds", gds, *(["--cell", cell] if cell else []),
                                       *(["--netlist", f.get("netlist")]
                                         if f.get("netlist") else []), fmt),
             "plan the run on the extracted DSPF: the currents (your testbench, with the "
             "DSPF for rcc or the CC netlist for cc-grid) and the mode, which is the "
             "person's choice; --gds draws the layout under the charts",
             after=["extract-magic"])
    plan.note("magic's network holds every conductor it extracts -- contacts, local "
              "interconnect, diffusion, poly: a layer the process file gives no limit "
              "carries its current in the IR drop and is reported as not checked")
    plan.note("never invent process limits or measured values; report with `emir report`, "
              "which says screening, not sign-off")
    plan.stop()
