"""A scripted MCP client that drives `emir mcp` the way an agent does.

It starts the server over stdio (as Claude Code, Codex or Cursor would),
lists the tools, proves the install with emir_selftest, asks emir_next for a
plan on the bundled sample, makes each planned tool call in order (stopping
at the first failure), and shows the go-ahead rule: a run that would start
Spectre (run=true, in the person's mode) is refused (EMIR-E590) because the
call carries no confirm=true.

It writes, into the output directory:
  transcript.json   every call: tool, arguments, result schema, isError,
                    exit status and the fields worth comparing
  report.md         the report the agent relays to the person
  emir_out/         what the run wrote (the server's working directory)

Offline: no network, no simulator, no licence. Needs the MCP Python SDK
(`pip install -e '.[mcp]'` in a clone); works with its 1.x and 2.x lines.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import anyio

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)

from emir.selftest import SAMPLE_CURRENTS, sample_paths  # noqa: E402

#: the tools this example calls (the transcript records whether each takes
#: `confirm`, the person's go-ahead)
USED = ("emir_next", "emir_selftest", "emir_nets", "emir_check", "emir_run", "emir_report")

#: the fields of each result kept in the transcript (the rest varies by
#: machine: paths, versions, timestamps)
KEEP = {
    "emir.selftest/1": ["passed"],
    "emir.nets/1": [],
    "emir.check/1": ["passed", "n_fail"],
    "emir.run/1": ["verdict", "counts", "worst_net", "disclaimer"],
    "emir.report/1": ["verdict", "disclaimer"],
    "emir.error/1": ["code", "exit_code"],
}


def facts(doc: dict) -> dict:
    """The comparable fields of a result document."""
    out = {k: doc.get(k) for k in KEEP.get(doc.get("schema"), [])}
    if doc.get("schema") == "emir.next/1":
        out["mode"] = doc["mode"]["chosen"]
        out["plan"] = [st["id"] for st in doc["steps"]]
    if doc.get("schema") == "emir.run/1":
        out["nets"] = [{k: n[k] for k in ("net", "verdict", "governing_metric", "ir_drop_mV",
                                          "n_violations")} for n in doc["nets"]]
    return out


def _get(obj, snake):
    """A field under its SDK 2.x (snake_case) or 1.x (camelCase) name."""
    if hasattr(obj, snake):
        return getattr(obj, snake)
    parts = snake.split("_")
    return getattr(obj, parts[0] + "".join(p.title() for p in parts[1:]))


def _relative(args: dict, sample_dir: str) -> dict:
    """Arguments with the sample's machine-specific directory replaced."""
    return {k: (v.replace(sample_dir, "<sample>") if isinstance(v, str) else v)
            for k, v in args.items()}


async def session(out_dir: str) -> list:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    dspf, proc = sample_paths()
    sample_dir = os.path.dirname(dspf)
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(
        p for p in (ROOT, os.environ.get("PYTHONPATH")) if p)}
    params = StdioServerParameters(command=sys.executable,
                                   args=["-m", "emir", "mcp", "--cwd", out_dir], env=env)
    transcript = []
    # the server's own log (the plan, the self-test, the run) goes to a file,
    # so the terminal shows one line per tool call
    log = open(os.path.join(out_dir, "server.log"), "w", encoding="utf-8")
    async with stdio_client(params, errlog=log) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        tools = (await s.list_tools()).tools
        transcript.append({"step": "list_tools", "tools": {
            # a tool that takes `confirm` can need the person's go-ahead (a
            # licensed tool, a long run, overwriting a file they own)
            t.name: {"takes_confirm": "confirm" in _get(t, "input_schema")["properties"]}
            for t in tools if t.name in USED}})

        async def call(name, args):
            r = await s.call_tool(name, args)
            doc = _get(r, "structured_content")
            meta = _get(r, "meta") or {}
            row = {"tool": name, "arguments": _relative(args, sample_dir),
                   "schema": doc.get("schema"), "is_error": bool(_get(r, "is_error")),
                   "exit_code": meta.get("emir/exit_code")}
            row.update(facts(doc))
            transcript.append(row)
            return doc

        # 1. the plan for what we have: the sample DSPF, its process file and
        #    DC currents (as the person would hand them over)
        plan = await call("emir_next", {"dspf": dspf, "process": proc,
                                        "currents": SAMPLE_CURRENTS})
        # 2. each step's tool call, in order; stop at the first that fails
        for st in plan["steps"]:
            if st["tool"] is None or st["needs_human"]:
                transcript.append({"tool": None, "skipped": st["id"],
                                   "why": "not an emir command, or needs the person"})
                continue
            doc = await call(st["tool"]["name"], st["tool"]["arguments"])
            if doc.get("schema") == "emir.error/1":
                break
            if doc.get("schema") == "emir.report/1":
                with open(os.path.join(out_dir, "report.md"), "w", encoding="utf-8") as f:
                    f.write(doc["markdown"])
        # 3. the go-ahead rule: a run that would start Spectre (run=true, in
        #    the mode the person chose, here rcc), without confirm=true, is
        #    refused before anything runs
        await call("emir_run", {"dspf": dspf, "process": proc, "netlist": "tb.scs",
                                "mode": "rcc", "run": True})
    return transcript


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True, help="output directory")
    a = ap.parse_args(argv)
    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    transcript = anyio.run(session, out)
    with open(os.path.join(out, "transcript.json"), "w", encoding="utf-8") as f:
        json.dump(transcript, f, indent=1)
        f.write("\n")
    for row in transcript[1:]:
        if row.get("tool"):
            print(f"{row['tool']:16s} -> {row['schema']}"
                  + (f"  {row.get('code')}" if row["is_error"] else "")
                  + (f"  verdict {row['verdict']}" if row.get("verdict") else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
