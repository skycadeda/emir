"""`emir next` rules for the chip-level (hierarchical) flow.

With `--abstract FILE@INST` (the blocks black-boxed in --dspf): read each
abstract, check that its ports land on the top's records (asking for
`--place` where a pin reaches the top at several places), then plan check ->
run (with the port voltages; `--cc-sim` is the top's own simulation, for its
devices outside the blocks) -> report -> the back-annotation of every block
fed at several pins.

--dynamic, and an --em-basis that needs it, are refused with --abstract
(EMIR-E811: the top-level solve is resistive): `missing[]` says so and
nothing that would be refused is planned.

After a chip-level run (`emir next --run-summary FILE`, `plan_decap.py`): the
run's own --abstract, --place and --align are read back from its arguments,
its check, run and report are not planned again, and the back-annotation
reads the port voltages the run wrote.

Without abstracts, a DSPF whose instance pins reach a net at several places
(the mark of a black-boxed block) gets the hierarchical route as advice, next
to the rcc plan of `plan._currents`.
"""
from __future__ import annotations

import argparse
import os

from .errors import EmirError
from .output import command_line
from .plan import Facts, Plan, rule

PV = "emir_out/port_voltages.json"
SUMMARY = "emir_out/summary.json"


def add_arguments(ap):
    """The options this flow adds to `emir next`."""
    ap.add_argument("--abstract", action="append", default=None, metavar="FILE@INST",
                    help="a block abstract placed at instance INST of --dspf (a chip-level, "
                         "hierarchical run); repeat per block")
    ap.add_argument("--place", action="append", default=None, metavar="INST=DX,DY[,ORIENT]",
                    help="a block's placement, for its pins at several connections")
    ap.add_argument("--align", default=None, choices=("inphase", "sync", "async"),
                    help="how the blocks line up in time (default inphase)")


def inputs(a) -> dict:
    """What this flow reads from the parsed `emir next` options. After a
    chip-level run (`--run-summary`) given no --abstract, the run's own
    blocks, placements and alignment, from its arguments."""
    out = {"abstracts": list(getattr(a, "abstract", None) or []),
           "place": list(getattr(a, "place", None) or []),
           "align": getattr(a, "align", None)}
    path = getattr(a, "run_summary", None)
    if path and not out["abstracts"]:
        from .plan_decap import summary_args
        from .report import load_summary
        s = load_summary(path)
        ra = summary_args(s)
        if ra.get("abstract"):
            # each abstract where the run read it (the summary's absolute
            # path), whatever directory this plan is made in
            where = {b.get("instance"): b.get("abstract")
                     for b in (s.get("hierarchy") or {}).get("blocks") or []}
            abstracts = []
            for x in ra["abstract"]:
                file, _at, inst = x.rpartition("@")
                abstracts.append(f"{where.get(inst) or file}@{inst}")
            out = {"abstracts": abstracts, "place": list(ra.get("place") or []),
                   "align": ra.get("align")}
    return out


@rule(45, "hierarchy")
def _hierarchy(f: Facts, plan: Plan):
    info = f.dspf_info
    if not info.get("ok"):
        return
    d = info["_dspf"]
    abstracts = f.get("abstracts") or []
    if abstracts:
        _with_abstracts(f, plan, d, abstracts)
    else:
        _advise(f, plan, d)


def _advise(f: Facts, plan: Plan, d):
    split = {n: d.split_pins(n) for n in f.target_nets()}
    split = {n: s for n, s in split.items() if s}
    if not split:
        return
    insts = sorted({k.split(":", 1)[0] for s in split.values() for k in s})
    plan.note(
        f"{', '.join(insts[:6])} reach {', '.join(sorted(split)[:4])} at several connections: "
        f"black-boxed block(s). For a chip-level top, screen it hierarchically: characterise "
        f"each block once (`emir abstract BLOCK.dspf --process P --cc-sim BLOCK.raw "
        f"--mode cc-grid -o "
        f"BLOCK.abstract.json`, every pin shape a *|P port), then plan again with "
        f"`--abstract BLOCK.abstract.json@{insts[0]} --place {insts[0]}=DX,DY,ORIENT` "
        f"(`emir abstract --help`)")


def _namespace(f: Facts, abstracts: list) -> argparse.Namespace:
    return argparse.Namespace(abstract=abstracts, place=f.get("place") or [],
                              align=f.get("align") or "inphase", dspf=f.get("dspf"),
                              phase=None, phase_sweep=None, budget_mv=None,
                              port_voltages_out=None, place_tol=None)


def _next_args(f: Facts, abstracts: list) -> list:
    """The arguments of this `emir next`, to plan again."""
    a = ["--dspf", f.get("dspf")]
    for k, opt in (("process", "--process"), ("cc_sim", "--cc-sim")):
        if f.get(k):
            a += [opt, f.get(k)]
    if f.get("net") and f.get("net") != "all":
        a += ["--net", f.get("net")]
    for x in abstracts:
        a += ["--abstract", x]
    for x in f.get("place") or []:
        a += ["--place", x]
    if f.get("align"):
        a += ["--align", f.get("align")]
    return a


def _finished(f: Facts) -> dict | None:
    """The chip-level run `emir next --run-summary` reads (`{port_voltages}`:
    the file its --port-voltages-out wrote, or None), or None before a run."""
    path = f.get("run_summary")
    if not path:
        return None
    from .plan_decap import summary_args
    from .report import load_summary
    s = load_summary(path)
    pv = (s.get("hierarchy") or {}).get("port_voltages") or \
        summary_args(s).get("port_voltages_out")
    return {"port_voltages": pv}


def _with_abstracts(f: Facts, plan: Plan, d, abstracts: list):
    from .hier import load_abstract, match_ports, top_nets
    from .hier_cli import Options, abstract_command
    fmt = f.fmt()
    other = [o for o, k in (("--currents", "currents"), ("--netlist", "netlist")) if f.get(k)]
    if other:
        plan.need(f"no {' or '.join(other)} with --abstract",
                  f"EMIR-E811: the abstracts drive the top (and the top's own devices come "
                  f"from --cc-sim); {' and '.join(other)} drive it otherwise",
                  "drop it; give the top's CC simulation with --cc-sim for its own devices")
        plan.blocked = True
        return
    from .em_basis import NEEDS_DYNAMIC
    dyn = (["--dynamic"] if f.get("dynamic") else []) + \
        ([f"--em-basis {f.get('em_basis')}"] if f.get("em_basis") in NEEDS_DYNAMIC else [])
    if dyn:
        # the check and the run refuse them (EMIR-E811; a basis that needs the
        # dynamic solve, EMIR-E430): nothing is planned that would be refused
        plan.need(f"no {' or '.join(dyn)} with --abstract",
                  "EMIR-E811: a chip-level run's top-level solve is resistive (the blocks' "
                  "pin currents through the top's R grid), so it has no dynamic solve and "
                  "no decap-aware EM" + (f"; --em-basis {f.get('em_basis')} needs it "
                                         f"(EMIR-E430)" if f.get("em_basis") in NEEDS_DYNAMIC
                                         else ""),
                  "drop it: the top's verdict is the static one (decap-free). A block's "
                  "dynamic droop is in its abstract; its decap-aware EM comes from a run "
                  "of the block's own DSPF and CC result with --dynamic (`emir next --dspf "
                  "BLOCK.dspf --process P --cc-sim BLOCK.raw --mode cc-grid --dynamic`)")
        plan.blocked = True
        return
    # after the run (`emir next --run-summary`): its check, run and report
    # are done; what follows is the back-annotation from its port voltages
    done = _finished(f)
    try:
        opts = Options(_namespace(f, abstracts), command="next")
    except EmirError as e:
        plan.need("valid --abstract / --place values", f"{e.code}: {e.message}", e.hint)
        plan.blocked = True
        return
    insts, to_write, unusable = {}, [], []
    for inst, path in opts.abstracts.items():
        try:
            insts[inst] = load_abstract(path)
        except EmirError as e:
            unusable.append(inst)
            plan.need(f"a usable abstract for {inst}", f"{e.code}: {e.message}",
                      "write it with `emir abstract` (`emir schema abstract` prints the format)")
            if not os.path.exists(path):
                to_write.append((inst, path))
    for inst, path in to_write:
        # characterise the block (its DSPF and CC result are the person's
        # files: placeholders), with --top so its summary leads back here
        plan.add(f"abstract:{inst}", command_line(
            "abstract", f"<{inst}.dspf>", "--process", f.get("process") or "<process.yaml>",
            "--cc-sim", f"<{inst}.raw>", "--mode", "cc-grid", "-o", path, "--top",
            f"{f.get('dspf')}@{inst}", fmt),
            f"characterise block {inst} once: its R-only DSPF (every supply pin shape a *|P "
            f"port) and its CC simulation result replace the placeholders; it writes {path}")
    if to_write:
        plan.add("next", command_line("next", _next_args(f, abstracts), fmt),
                 "plan again once the abstracts are written: the chip-level check, run and "
                 "report", after=[f"abstract:{i}" for i, _p in to_write])
    if unusable:
        # a run that names an abstract it cannot read is refused (EMIR-E801,
        # E101): nothing past the abstracts is planned until they are there
        plan.blocked = True
        plan.mode = {"chosen": None, "why": "decided once every abstract can be read"}
        return
    have = {t["inst"] for t in d.inst_pins.values()}
    for inst in insts:
        if inst not in have:
            plan.need(f"instance {inst} in {f.get('dspf')}",
                      f"EMIR-E802: the top DSPF has no *|I record of {inst!r}",
                      "use the instance name as the top DSPF writes it (`emir pins`)")
    nets = top_nets(d, insts, f.get("net")) if insts else []
    if insts and not nets:
        plan.need("a top net with block pins", "EMIR-E809: no top net has a pin an abstract "
                  "covers", "check the instance names and --net (`emir pins`)")
    for net in nets:
        by = {}
        for t in d.net_taps(net):
            by.setdefault((t["inst"], t["pin"]), []).append(t)
        loose = sorted({i for (i, _p) in by if i not in insts})
        if loose and not f.get("cc_sim"):
            plan.note(f"net {net}: {', '.join(loose[:6])} belong to no abstract; give the top's "
                      f"CC simulation with --cc-sim for their currents, or they carry none "
                      f"(EMIR-W807)")
        for inst, ab in insts.items():
            for bnet, nb in ab["nets"].items():
                recs = by.get((inst, bnet))
                if not recs:
                    continue
                try:
                    match_ports(inst, bnet, nb["ports"], recs, opts.place.get(inst), opts.tol,
                                dspf=f.get("dspf"), net=net, log=None)
                except EmirError as e:
                    what = (f"--place {inst}=DX,DY,ORIENT" if e.code in ("EMIR-E806", "EMIR-E808")
                            else f"matching ports for {inst}:{bnet}")
                    plan.need(what, f"{e.code}: {e.message}", e.hint)
                    if not plan.has(f"pins:{net}"):
                        plan.add(f"pins:{net}", command_line("pins", f.get("dspf"), "--net", net,
                                                              fmt),
                                 "the records of the block pins with their coordinates: read "
                                 "the block's origin and orientation from them")
    if plan.missing and any(m["why"].startswith("EMIR-E8") for m in plan.missing):
        plan.blocked = True
        plan.mode = {"chosen": None, "why": "decided once the abstracts land on the top"}
        return
    plan.mode = {"chosen": "cc-grid",
                 "why": f"hierarchical: {len(insts)} block abstract(s) drive the top-level "
                        f"grid at their black-box pins (align {opts.align})"
                        + ("; the run is done (its summary)" if done else "")}
    if not insts or not (f.get("process") or done):
        return
    pv, after = PV, ["run"]
    if done:
        pv, after = done.get("port_voltages"), []
    base = [f.get("dspf"), "--process", f.get("process")]
    if f.get("net") and f.get("net") != "all":
        base += ["--net", f.get("net")]
    for a in abstracts:
        base += ["--abstract", a]
    for p in f.get("place") or []:
        base += ["--place", p]
    if f.get("cc_sim"):
        base += ["--cc-sim", f.get("cc_sim")]
    base += ["--align", opts.align]
    if not done:
        plan.add("check", command_line("check", base, fmt),
                 "check the top DSPF, the process file, every abstract and where its ports "
                 "land (exit 2 and coded FAILs if not)")
        # the top-level grid is solved at full size whatever the blocks are: a
        # top above the big-block size is as long a run as the flat one (and
        # sync aligns waveforms on top), so it asks the person first, as
        # `_analyse` does
        big = f.size()["class"] == "big"
        plan.add("run", command_line("run", base, "--summary-out", SUMMARY,
                                     "--port-voltages-out", PV, fmt),
                 "screen the top-level grid with the blocks' pin currents and the "
                 "conductance between their pins; the pin voltages feed the back-annotation "
                 "(add --budget-mv for the IR budget per block)"
                 + (f"; {f.size()['why']}: a long run" if big else ""),
                 long_run=big, after=["check"])
        plan.add("report", command_line("report", "--from", SUMMARY),
                 "the summary to relay: the top-level verdict, the block pins, the budgets; "
                 "it says screening, not sign-off", after=["run"])
    for inst, ab in insts.items():
        multi = [b for b, nb in ab["nets"].items()
                 if len(nb["ports"]) > 1 and not nb.get("back_annotation")]
        if not multi:
            continue
        path = opts.abstracts[inst]
        why = (f"{inst} ({', '.join(multi)}) is fed at several pins: re-check its EM with the "
               f"top's pin voltages (the pass-through current the block-alone verdict omits)")
        if not pv:
            plan.note(why + " -- the run wrote no port voltages: the same run with "
                      "--port-voltages-out FILE writes them, then `emir abstract ... "
                      f"--port-voltages FILE@{inst}`")
            continue
        # into the output directory, never beside the person's own abstract
        cmd = abstract_command(ab, ["--port-voltages", f"{pv}@{inst}", "-o",
                                    os.path.join("emir_out", os.path.basename(
                                        os.path.splitext(path)[0]) + ".annotated.json")], fmt)
        if cmd:
            plan.add(f"annotate:{inst}", cmd, why, after=after)
        else:
            plan.note(why + f" -- `emir abstract ... --port-voltages {pv}@{inst}`; the "
                      f"abstract's input files are not here to build the command")
    if opts.align == "async":
        plan.note("--align async is NOT an upper bound for blocks on related clocks: use it "
                  "only for unrelated clocks (inphase is the bound; sync with --phase-sweep "
                  "the worst over the phases of related clocks)")
    plan.note("two verdicts per level: the top-level grid (this run) and each block's own EM "
              "(its abstract, back-annotated when fed at several pins)")
    plan.note("when a block pin droops by more than about 2 % of its supply (EMIR-W805), "
              "re-simulate the block at `v_delivered_V` from the port-voltages file, "
              "re-abstract and re-run until the pin voltages settle")
