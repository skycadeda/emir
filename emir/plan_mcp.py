"""`emir next` over MCP: each step gets the tool call that runs it.

When the plan is built inside an `emir mcp` tool call (`mcp_tools.in_mcp`),
every step gains a ``tool`` field: ``{"name": "emir_check", "arguments":
{...}}`` -- the typed tool and the arguments that run the step's command
exactly -- or null for a step that is not an emir command (the simulation,
which the person runs). A plan built on the command line is unchanged.
"""
from __future__ import annotations

from .plan import rule

#: what the plan advises over MCP (kept before the closing guard-rail line)
ADVICE = ("over MCP: make each step's `tool` call (its name and arguments) in order; "
          "a step whose needs_human is true needs the person's go-ahead first, then "
          "confirm=true on the call; a step whose tool is null is not an emir command "
          "(the person runs it, e.g. the simulation); a waiting step's placeholders "
          "are replaced first")


@rule(99, "mcp", final=True)          # also after a rule stopped the plan (a GDS plan)
def _mcp(f, plan):
    from .mcp_tools import in_mcp, tool_call_for
    if not in_mcp() or not plan.steps:
        return
    for s in plan.steps:
        s["tool"] = tool_call_for(s["command"])
    # the guard-rail line stays the last word
    last = next((i for i, a in enumerate(plan.advice) if a.startswith("never invent")),
                len(plan.advice))
    if ADVICE not in plan.advice:
        plan.advice.insert(last, ADVICE)
