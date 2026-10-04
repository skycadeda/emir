"""`emir next` rules for a PDK with no readable process file (process data
entered by hand: emir/process_file.py).

With a DSPF and no --process, the plan says where a process file comes from
(`missing[]`, the base rule): the PDK's file in `pdks/`, `emir import-ict` on a
readable ICT, or -- when no readable file exists (the ICT is missing or
encrypted) -- values typed from the design manual. That last route is planned
here, as two steps:

* `emir process new --from-dspf D -o emir_out/process.yaml`: the fill-in file
  for exactly the layers and vias D uses. `needs_human`: the person types each
  value from the design manual with its source (or the agent transcribes them
  from a manual the person gives and the person confirms each one). Never an
  invented, estimated or derived limit.
* `emir process check emir_out/process.yaml --dspf D`: exit 2 until every
  required value is entered; it must pass before any run. Then `emir next`
  again with `--process`.
"""
from __future__ import annotations

from .output import command_line
from .plan import Facts, Plan, rule

#: where the planned fill-in file goes (`emir process new`'s default)
OUT = "emir_out/process.yaml"


@rule(31, "process-by-hand")
def _by_hand(f: Facts, plan: Plan):
    if f.get("process") or not f.get("dspf") or not f.dspf_info["ok"]:
        return
    if f.get("abstracts"):
        return                       # a chip-level plan: the blocks' process file
    plan.add("process-new", command_line("process", "new", "--from-dspf", f.get("dspf"),
                                         "-o", OUT, f.fmt()),
             "only when no readable process file exists for the PDK -- not in pdks/ (ls "
             "pdks/) and no ICT emir can read (missing, or encrypted: emir import-ict "
             "refuses it, EMIR-E325): write the fill-in file for exactly the layers and "
             "vias this DSPF uses, every value empty with its unit and a source slot. The "
             "person then types each value from the design manual's EM tables with its "
             "source, or you transcribe them from a manual the person gives and the person "
             "confirms each one. Never invent, estimate or derive a limit",
             needs_human=True,
             ask="the person types the PDK's EM values (or confirms each one you transcribe)")
    plan.add("process-check", command_line("process", "check", OUT, "--dspf", f.get("dspf"),
                                           f.fmt()),
             "after the values are typed: what is still empty, unsourced or a likely unit "
             "slip; it exits 2 until every required value is entered, and must pass before "
             "any run. Then plan again with --process "
             + OUT, after=["process-new"])
    plan.note("a process file entered by hand: every report says per layer that its limits "
              "were typed by hand and from which document (EMIR-W320 on every run for a "
              "value with no source)")
