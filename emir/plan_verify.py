"""`emir next` rules for the on-site verification of a simulator route.

Support for a simulator is proven on the machine that uses it (`emir
verify-sim`, emir/verify_sim.py). Before the first run that reads a
simulator's result here, the plan verifies that simulator's route -- once
per machine, simulator version and output format: a passing record in the
state directory ($EMIR_STATE_DIR, else ~/.emir) ends the matter.

* the simulator is the one the plan's simulate step runs (Spectre, HSPICE,
  Xyce, ngspice), or the one that wrote a given --cc-sim result (its header,
  its names sidecar, or --sim);
* the verify step comes before the simulate step (or, for a finished result,
  before the check and the run);
* for Spectre and HSPICE it runs a licensed tool: `needs_human`, the person's
  go-ahead first (without --run the command only plans);
* none where no verification can change the answer: a result of unknown
  version or in a format verify-sim never writes (a note says so); none
  where this user may not read the records file either (`missing[]` asks the
  person for access: verify-sim could keep no record);
* after a run (`emir next --run-summary`), the summary's own
  `simulator_verification` decides.

A record counts as `emir run` counts it (emir/verify_sim.py): the latest
valid record of a version decides (a later FAIL cancels an earlier PASS), and
a pass of another emir minor version is stale -- the plan verifies again,
and says why.

`--sim` (added to `emir next` here) names the simulator the person uses when
no tool on this machine says it, or to choose among several.
"""
from __future__ import annotations

from .output import command_line
from .plan import Facts, Plan, rule

SIMS = ("spectre", "hspice", "xyce", "ngspice")
LICENSED = ("spectre", "hspice")


def add_arguments(ap):
    ap.add_argument("--sim", default=None, choices=SIMS,
                    help="the simulator the person uses (default: detected: Spectre at a "
                         "Cadence site, then HSPICE, Xyce, ngspice); for a finished "
                         "--cc-sim, the one that wrote it when the result does not say")


def inputs(a) -> dict:
    return {"sim": getattr(a, "sim", None)}


def _planned_sim(f: Facts, plan: Plan) -> str | None:
    """The simulator whose result the planned run reads."""
    for s in plan.steps:
        if s["id"] in ("simulate", "simulate-plan"):
            cmd = s["command"]
            if cmd.startswith("spectre "):
                return "spectre"
            if cmd.startswith("hspice "):
                return "hspice"
            if cmd.startswith(("Xyce ", "xyce ")):
                return "xyce"
            if cmd.startswith("ngspice "):
                return "ngspice"
            parts = cmd.split()
            if "--sim" in parts:
                return parts[parts.index("--sim") + 1]
    return None


def _why_not(sim: str) -> str:
    """Why no record of `sim` counts here: the newest version's reason."""
    from .verify_sim import FORMAT, KINDS, missing_covers, routes
    rs = routes(sim, FORMAT.get(sim))
    if not rs:
        return "no passing record is kept here"
    last = rs[-1]
    rec = last["record"]
    gap = missing_covers(rec, sim) if last["status"] == "verified" else []
    if gap:
        return (f"its passing record ({last['version']}, {rec.get('date')}) does not cover "
                f"{', '.join(KINDS[k] for k in gap)}, which this emir's verification "
                f"covers (a record made before records said what they cover): verify "
                f"again with this emir")
    if last["reason"] == "failed":
        return (f"its latest verification here ({last['version']}, {rec.get('date')}) "
                f"FAILED; tell the person, and verify again once the cause is fixed")
    if last["reason"] == "stale":
        return (f"its passing record ({last['version']}) is from emir "
                f"{rec.get('emir_version')}: stale for this emir, so verify again")
    return "no passing record is kept here"


@rule(58, "verify-sim")
def _verify(f: Facts, plan: Plan):
    from .verify_sim import FORMAT, verified_route
    sim = _planned_sim(f, plan)
    if sim is None:
        # a finished result: decided at rule 62, once the check step exists
        f._cache["verify_pending"] = True
        return
    rec = verified_route(sim, FORMAT.get(sim))
    if rec is not None:
        plan.note(f"{sim}: its route into emir was verified on this machine "
                  f"({rec.get('date')}, {rec.get('version')}); a run whose result names "
                  f"another version, or does not say its version, says so (EMIR-W464, "
                  f"EMIR-W416)")
        return
    where = next((k for k, s in enumerate(plan.steps)
                  if s["id"] in ("simulate-plan", "simulate", "save-lines")
                  or s["id"].startswith("save-lines:")), None)
    _add(f, plan, sim, where)


def _add(f: Facts, plan: Plan, sim: str, where, why: str | None = None):
    from .verify_sim import records_path, records_unreadable
    prob = records_unreadable()
    if prob:
        # no record counts here and verify-sim could keep none (it refuses,
        # EMIR-E102): the person's access, not a verification, comes first
        path = records_path()
        plan.need(f"read access to the verification records ({path})",
                  f"{path} {prob}: this user may not read it, so no verification of "
                  f"{sim} counts here and emir verify-sim cannot keep its record "
                  f"(EMIR-W415, EMIR-E102)",
                  "ask the person to give this user read access to it, or to set "
                  "EMIR_STATE_DIR to a directory of this user's own; then plan again (the "
                  "plan verifies the simulator there)")
        return
    licensed = sim in LICENSED
    cmd = command_line("verify-sim", "--sim", sim, *(["--run"] if licensed else []), f.fmt())
    plan.add("verify-sim", cmd,
             f"prove on this machine that {sim}'s route into emir gives the right numbers "
             f"(hand-checkable testbenches: ideal loads and real devices, every branch "
             f"current read by rcc; seconds) before the first run that uses {sim}: "
             f"{why or _why_not(sim)}"
             + (f" ({sim} is licensed: ask the person; without --run it only plans)"
                if licensed else ""),
             licensed_tool=sim if licensed else None, long_run=False)
    if where is not None:
        plan.steps.insert(where, plan.steps.pop())
    for k, s in enumerate(plan.steps, 1):
        s["n"] = k
    for s in plan.steps:
        if (s["id"] in ("check", "run", "simulate", "simulate-plan")
                and "verify-sim" not in s["after"]):
            s["after"].append("verify-sim")


def _planned_pins(f: Facts, plan: Plan) -> dict | None:
    """The kinds of current the planned run reads from a finished result
    (`verify_sim.run_pins` over the target nets: their branch currents), or
    None when the DSPF is not known or the run reads no simulation."""
    from .verify_sim import run_pins
    info = f.dspf_info
    if not info.get("ok") or (plan.mode or {}).get("chosen") != "rcc":
        return None
    return run_pins(info["_dspf"], {n: {"mode": "rcc"} for n in f.target_nets()})


@rule(62, "verify-sim-result")
def _verify_result(f: Facts, plan: Plan):
    """A finished --cc-sim: the simulator that wrote it, verified here? (A run
    refused for its window wrote no summary: its fix is planned as a new run.)"""
    if f.get("run_summary") and not f.get("refused"):
        f._cache.pop("verify_pending", None)
        _after_run(f, plan)
        return
    if not f._cache.pop("verify_pending", False) or not f.get("cc_sim"):
        return
    if not plan.has("check"):
        return
    from . import waves
    from .verify_sim import KINDS, missing_covers, status
    meta = waves.peek(f.get("cc_sim"))
    st = status(meta, sim_hint=f.get("sim"), pins=_planned_pins(f, plan))
    never = [k for k in st.get("current_kinds") or [] if not k["verify_sim_covers"]]
    if never and st.get("reason") != "current kinds not covered":
        plan.note("emir verify-sim does not cover the result's "
                  + "; ".join(f"{KINDS[k['kind']]} ({', '.join(k['examples'])}"
                              + (" ..." if k["count"] > len(k["examples"]) else "") + ")"
                              for k in never)
                  + ": those currents stay UNVERIFIED on this machine whatever it finds "
                    "(EMIR-W464)")
    if st["status"] == "verified":
        gap = ([] if st.get("current_kinds") is not None
               else missing_covers(st.get("record"), st["simulator"]))
        if gap:
            # the run's kinds are not known here: the record must cover every
            # kind a verification of this emir covers, as before a simulation
            where = next(k for k, s in enumerate(plan.steps) if s["id"] == "check")
            _add(f, plan, st["simulator"], where)
            return
        plan.note(f"{st['simulator']}: the result's route was verified on this machine "
                  f"({(st.get('record') or {}).get('date')})"
                  + (" for every kind of current the run reads" if st.get("current_kinds")
                     else ""))
        return
    if st.get("reason") == "current kinds not covered":
        # what the run will say (EMIR-W464): a verification of this emir
        # covers some of those kinds (plan it), or none can (say so)
        if st.get("command"):
            where = next(k for k, s in enumerate(plan.steps) if s["id"] == "check")
            _add(f, plan, st["simulator"], where, why=st["note"])
        else:
            plan.note(f"{st['simulator']} route {st['status']} for this result: "
                      f"{st['note']} (EMIR-W464)")
        return
    if st.get("reason") == "format not covered":
        # no verification can cover it: none is planned, the run says so
        plan.note(f"{st['note']} (EMIR-W464)")
        return
    if st["status"] == "unknown simulator":
        plan.note("the result does not say which simulator wrote it: pass --sim to "
                  "`emir next` (and to `emir run`) so its on-site verification can be "
                  "planned and checked")
        return
    if st["reason"] == "version unknown":
        # no verification can make this result verified: none is planned (a
        # licensed one least of all); `emir simulate` records the version
        sim = st["simulator"]
        plan.note(f"the result does not say which {sim} version wrote it, so no "
                  f"verification can be matched to it: the run will say not verified "
                  f"(EMIR-W416) whatever emir verify-sim finds. For a result that can be "
                  f"verified, simulate through `emir simulate --sim {sim}` (it records the "
                  f"version), with the route verified here (`emir verify-sim --sim {sim}`"
                  + (", licensed: ask the person first" if sim in LICENSED else "") + ")")
        return
    where = next(k for k, s in enumerate(plan.steps) if s["id"] == "check")
    _add(f, plan, st["simulator"], where,
         why=st.get("note") if st["reason"] in ("failed", "stale") else None)


def _after_run(f: Facts, plan: Plan):
    """After a run (`emir next --run-summary`): its own `simulator_verification`
    decides, as its report says it. A verification is planned only where one
    can change it (a `command`: no record, a stale or failed one, kinds a
    verification of this emir covers); a result no verification can cover
    (a format verify-sim never writes, an unknown version, kinds none covers)
    gets a note. A verification planned for a simulation the plan no longer
    runs is dropped: the run is done."""
    from .report import load_summary
    v = load_summary(f.get("run_summary")).get("simulator_verification") or {}
    plan.steps[:] = [s for s in plan.steps if s["id"] != "verify-sim"]
    for s in plan.steps:
        s["after"] = [x for x in s["after"] if x != "verify-sim"]
    st, reason, sim = v.get("status"), v.get("reason"), v.get("simulator")
    if st in (None, "not applicable"):
        return
    if st == "verified":
        plan.note(f"{sim}: the run's simulator route is verified on this machine (its "
                  f"summary)")
        return
    if st == "unknown simulator":
        plan.note("the run's result does not say which simulator wrote it: re-run it with "
                  "--sim so its on-site verification can be looked up")
        return
    if v.get("command") and _verified_since(v):
        # the verification passed after the run: its summary is older than it
        plan.note(f"the run's summary says its {sim} route is {st} on this machine "
                  f"({reason}), and its report says so; a passing verification that covers "
                  f"what the run read is recorded here since. The same run again (the "
                  f"summary's `command`) records it in its summary")
        return
    if reason == "records unreadable" or v.get("command"):
        plan.note(f"the run's summary says its {sim} route is {st} on this machine "
                  f"({reason}), and its report says so. Once the verification passes, the "
                  f"same run again (the summary's `command`) records it in its summary")
        where = next((k for k, s in enumerate(plan.steps) if s["id"] != "selftest"), None)
        _add(f, plan, sim, where, why=v.get("note"))
        return
    plan.note(f"the run's summary says its {sim} route is {st} on this machine ({reason}): "
              f"{v.get('note')}; no emir verify-sim changes that for this result "
              f"({'EMIR-W416' if reason == 'version unknown' else 'EMIR-W464'})")


def _verified_since(v: dict) -> bool:
    """Whether a passing record of this machine now matches the run's result
    (`simulator_verification`'s simulator, version, format and arguments) and
    covers the kinds of current it read that a verification can cover."""
    from .verify_sim import lookup, missing_covers, record_covers, valid_records
    lk = lookup(v.get("simulator"), v.get("version"), v.get("format"), v.get("sim_args"),
                records=valid_records()[0])
    if lk["status"] != "verified":
        return False
    kinds = v.get("current_kinds")
    if kinds is None:
        return not missing_covers(lk["record"], v.get("simulator"))
    have = set(record_covers(lk["record"]))
    return all(k["kind"] in have for k in kinds if k.get("verify_sim_covers"))
